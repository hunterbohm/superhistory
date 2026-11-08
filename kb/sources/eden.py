"""Your own posts on X, or on another network Eden indexes, read through Eden (eden.so) with your Eden API key: one
item per post, or per thread. Eden indexes public posts itself, so this needs no X API plan.

Eden charges research credits for each call. Its price list (eden.so/skills/eden-mcp/references/errors.md, read
2026-11-07) puts a search at 0.5 credits, a read of your own analytics at 0.2 and a whole post at 1. So a run asks
Eden at most once every `every_hours`. It lists the account's posts PAGE at a time, newest first: the whole history
on the first run, then only posts published since the last run. Each run stops SETTLE short of now, so Eden has time
to index a post before a run passes it, and no post is read twice, so a whole post or thread already stored is never
replaced by its start. Eden's search returns the start of a long post and only the first post of a thread, so the run
also reads your own analytics, which mark the posts that start a thread, and reads whole each new post that was cut
short or starts a thread, at most `full_reads` a run, newest first. Analytics cover only accounts connected to your
Eden workspace; for another account, threads keep their first post only. Eden's analytics return at most the
newest 100 posts and cannot be paged (Starter keeps 30 days of them), so on the first run, threads older than those
keep their first post only. A later run's window is short, so its threads are all found.

The key needs Eden's research tools. Make one in Eden under Settings, API.

Exclude key: the post's id on its network.
"""
import html
import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from kb.config import Secret
from kb.mask import clip, mask
from kb.sources import Item, Progress, SourceType

API = "https://mcp.eden.so/v1/tools/"
PAGE = 50
DAY = 86400
SETTLE = 6 * 3600
HTTP_TIMEOUT = 120
TEXT_CHARS = 24000
TRANSCRIPT_CHARS = 200000
USER_AGENT = "kb"
NETWORKS = {"twitter": "X", "linkedin": "LinkedIn", "threads": "Threads", "instagram": "Instagram",
            "tiktok": "TikTok", "youtube": "YouTube", "substack": "Substack"}


@dataclass(frozen=True)
class Options:
    username: str
    key: Secret
    platform: Literal["twitter", "linkedin", "threads", "instagram", "tiktok", "youtube", "substack"] = "twitter"
    every_hours: int = 24
    full_reads: int = 5


def call(key, tool, body):
    """One Eden tool call. Failures, Eden's own `ok: false` answers included, raise RuntimeError with masked text."""
    request = urllib.request.Request(API + tool, data=json.dumps(body).encode(), method="POST", headers={
        "authorization": f"Bearer {key.reveal()}", "content-type": "application/json", "user-agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as resp:
            answer = json.load(resp)
    except urllib.error.HTTPError as exc:
        with exc:
            raw = exc.read(65537)
        # A body cut at the bytes read could end in part of a secret mask() cannot know, so it is left out.
        detail = "(longer than 64 KB, left out)" if len(raw) > 65536 else " ".join(
            mask(raw.decode(errors="replace")).split())[:300]
        raise RuntimeError(f"Eden {tool} answered HTTP {exc.code}: {detail}") from None
    except Exception as exc:
        raise RuntimeError(mask(f"cannot reach Eden for {tool}: {getattr(exc, 'reason', exc)}")) from None
    if not isinstance(answer, dict):
        raise RuntimeError(f"Eden {tool} answered with something other than an object")
    if answer.get("ok") is False:
        status, message = answer.get("status") or "", answer.get("message") or answer.get("error") or ""
        raise RuntimeError(mask(f"Eden {tool} refused: {status} {message}".strip())[:400])
    return answer


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d")


def posts(options, after=None):
    """Every post of the account Eden has, newest first, published on or after the UTC date `after`."""
    body = {"scope": "creator", "creatorRef": {"platform": options.platform, "username": options.username.lstrip("@")},
            "orderBy": "recent", "limit": PAGE}
    if after:
        body["publishedAfter"] = after
    found, offset = [], 0
    while True:
        page = call(options.key, "eden_search_social_content", {**body, "offset": offset})
        found += page.get("results") or []
        if not page.get("hasMore") or page.get("nextOffset") in (None, offset):
            return found
        offset = page["nextOffset"]


def whole(options, post):
    answer = call(options.key, "eden_read_social_post", {"contentId": post["contentId"], "platform": post["platform"],
                                                         "includeTranscript": True})
    # A read by contentId answers at the top level; a read by URL wraps the post in "post".
    return answer.get("post") or answer


def thread_heads(options, days):
    """The ids of your own posts in the last `days` days (None: all Eden keeps) that start a thread."""
    body = {"platform": options.platform, "limit": 100}
    if days:
        body["days"] = days
    rows = call(options.key, "eden_list_analytics_posts", body).get("posts") or []
    return {_id_in(row.get("url") or "") for row in rows if row.get("threadTotals") is not None} - {""}


def _id_in(url):
    return url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]


def _native_id(post):
    return _id_in(post.get("contentUrl") or "") or post["contentId"]


def _text(text):
    return html.unescape(text or "").strip()


def _render(options, post, full, head):
    network = NETWORKS.get(post.get("platform"), post.get("platform") or "")
    user = options.username.lstrip("@")
    body = _text((full or {}).get("body") or post.get("bodyPreview"))
    cut = bool(post.get("bodyTruncated")) and (not full or bool(full.get("bodyTruncated")))
    title = _text(post.get("title"))
    thread = ((full or {}).get("thread") or {}).get("posts") or []
    # Only the author's own posts: a reply from someone else is not part of the thread.
    author = next((p.get("authorPlatformId") for p in thread if p.get("platformContentId") == _native_id(post)), None)
    later = [_text(p.get("body")) for p in thread if p.get("platformContentId") != _native_id(post)
             and p.get("authorPlatformId") == author and _text(p.get("body"))]
    kind = "thread" if later or head else (post.get("contentType") or "post")
    posts_text = ["\n\n".join(part for part in (title, clip(body, TEXT_CHARS)) if part) or "(no text)"]
    posts_text += [clip(text, TEXT_CHARS) for text in later]
    blocks = [f"{network} {kind} by @{user}", "\n\n---\n\n".join(posts_text)]
    if cut:
        blocks.append("(Eden returned only the start of this post.)")
    elif head and not full:
        blocks.append("(The rest of this thread was not read.)")
    if full and full.get("transcript"):
        blocks.append("Transcript:\n" + clip(_text(full["transcript"]), TRANSCRIPT_CHARS))
    first = " ".join(body.split())[:80]
    return Item(f"{post.get('platform')}/{_native_id(post)}", _iso(post["publishedAt"] / 1000),
                f"@{user} on {network}: {title or first or '(no text)'}", "\n\n".join(blocks),
                {"url": post.get("contentUrl") or "", "network": network, "kind": kind}, None)


def extract(run):
    options, since, now = run.options, run.since, time.time()
    # Each run reads the posts published up to SETTLE ago and marks that time, so a post Eden indexes a little after
    # it went out is still read, and no post is read twice.
    upto = now - SETTLE
    if since is not None and upto - since < options.every_hours * 3600:
        return
    found = [p for p in posts(options, None if since is None else _date(since))
             if (since is None or p["publishedAt"] / 1000 > since) and p["publishedAt"] / 1000 <= upto
             and not run.excludes(_native_id(p))]
    heads = thread_heads(options, None if since is None else min(365, math.ceil((now - since) / DAY) + 1)) \
        if found else set()
    reads = options.full_reads
    for post in found:
        head = _native_id(post) in heads
        full = None
        if (head or post.get("bodyTruncated")) and reads > 0:
            full = whole(options, post)
            reads -= 1
        yield _render(options, post, full, head)._replace(mark=upto)
    yield Progress(upto)


def _account(options):
    return f"eden:{options.platform}:{options.username.lstrip('@').lower()}"


TYPE = SourceType(Options, extract, account=_account)
