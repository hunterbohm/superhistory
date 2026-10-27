"""GitHub issues and pull requests the user is involved in, read with the `gh` CLI: one item per thread.

An item holds the description and every comment and submitted review, so a thread updated later is read again
whole and replaces the stored one. The search API returns at most SEARCH_CAP results per query and allows about
30 requests a minute, so a run searches SPAN_DAYS windows, splits a window that fills a search, and paces the
searches to SEARCH_PER_MINUTE.

`gh` must be installed and signed in. `involves` is passed to `gh search issues --involves=`: "@me" or a login.

Exclude key: "owner/repo", such as "acme-co/dash". "acme-co/*" skips every repository of one owner.
"""
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NamedTuple, Tuple

from kb.sources import Item, Progress, SourceType

DAY = 86400
SPAN_DAYS = 30
# A thread's updatedAt can reach the search index after the run that should have listed it.
OVERLAP = 3600
SEARCH_CAP = 1000
PAGE = 100
SEARCH_PER_MINUTE = 25
FETCH_WORKERS = 4
BODY_CHARS = 20000
GH_TIMEOUT = 300
ISSUE_FIELDS = "title,body,state,author,createdAt,updatedAt,closedAt,labels,comments,url"
PR_FIELDS = ISSUE_FIELDS + ",mergedAt,reviews,headRefName,baseRefName"


@dataclass(frozen=True)
class Options:
    window_days: int = 365
    involves: str = "@me"
    gh: str = "gh"


class Hit(NamedTuple):
    repo: str
    number: int
    is_pr: bool
    epoch: float

    @property
    def source_id(self):
        return f"{self.repo}#{self.number}"


class Post(NamedTuple):
    author: str
    at: str
    text: str
    verdict: str


class Thread(NamedTuple):
    hit: Hit
    title: str
    body: str
    state: str
    author: str
    created: str
    closed: str
    merged: str
    branches: str
    labels: Tuple[str, ...]
    url: str
    updated: str
    posts: Tuple[Post, ...]


def run_gh(binary, args):
    proc = subprocess.run([binary, *args], capture_output=True, text=True, timeout=GH_TIMEOUT)
    if proc.returncode:
        raise RuntimeError(f"gh {' '.join(args[:3])} exited {proc.returncode}: {proc.stderr.strip()[-400:]}")
    return json.loads(proc.stdout)


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _clip(text):
    text = (text or "").strip()
    return text if len(text) <= BODY_CHARS else text[:BODY_CHARS] + " …"


def _login(who):
    return (who or {}).get("login") or "ghost"


def _search(options, start, end):
    rows = run_gh(options.gh, ["search", "issues", "--include-prs", f"--involves={options.involves}",
                               f"--updated={_iso(start)}..{_iso(end)}", "--limit", str(SEARCH_CAP),
                               "--json", "repository,number,isPullRequest,updatedAt"])
    return [Hit(r["repository"]["nameWithOwner"], r["number"], r["isPullRequest"], _epoch(r["updatedAt"]))
            for r in rows]


def _hits(options, start, end):
    windows = [(lo, min(int(end), lo + SPAN_DAYS * DAY)) for lo in range(int(start), int(end), SPAN_DAYS * DAY)]
    found, ready = {}, 0.0
    while windows:
        lo, hi = windows.pop(0)
        time.sleep(max(0.0, ready - time.time()))
        hits = _search(options, lo, hi)
        # gh asks for PAGE results at a time until it has them all, and each page counts as one search.
        ready = time.time() + (len(hits) // PAGE + 1) * 60 / SEARCH_PER_MINUTE
        if len(hits) >= SEARCH_CAP and hi - lo > 1:
            mid = (lo + hi) // 2
            windows[:0] = [(lo, mid), (mid, hi)]
        else:
            found.update((h.source_id, h) for h in hits)
    return sorted(found.values(), key=lambda h: (h.epoch, h.source_id))


def _thread(options, hit):
    kind = "pr" if hit.is_pr else "issue"
    data = run_gh(options.gh, [kind, "view", str(hit.number), "-R", hit.repo, "--json",
                               PR_FIELDS if hit.is_pr else ISSUE_FIELDS])
    posts = [Post(_login(c.get("author")), c["createdAt"], _clip(c.get("body")), "")
             for c in data.get("comments") or [] if not c.get("isMinimized")]
    for r in data.get("reviews") or []:
        # A pending review has not been submitted. One with no body and the COMMENTED verdict only holds line
        # comments, which gh does not return.
        if r.get("submittedAt") and (r.get("body") or r.get("state") != "COMMENTED"):
            posts.append(Post(_login(r.get("author")), r["submittedAt"], _clip(r.get("body")),
                              "review " + r["state"].lower().replace("_", " ")))
    branches = f"{data['headRefName']} -> {data['baseRefName']}" if hit.is_pr else ""
    return Thread(hit, data["title"], _clip(data.get("body")), data["state"].lower(), _login(data.get("author")),
                  data["createdAt"], data.get("closedAt") or "", data.get("mergedAt") or "", branches,
                  tuple(label["name"] for label in data.get("labels") or []), data["url"], data["updatedAt"],
                  tuple(sorted(posts, key=lambda p: p.at)))


def _render(thread):
    hit = thread.hit
    kind = "PR" if hit.is_pr else "issue"
    head = [f"{kind} {thread.state}", f"author {thread.author}", f"created {thread.created}"]
    if thread.merged:
        head.append(f"merged {thread.merged}")
    elif thread.closed:
        head.append(f"closed {thread.closed}")
    if thread.branches:
        head.append(thread.branches)
    if thread.labels:
        head.append("labels " + ", ".join(thread.labels))
    blocks = [" | ".join(head) + "\n\n" + (thread.body or "(no description)")]
    blocks += [f"{p.author} ({p.at}{', ' + p.verdict if p.verdict else ''}): {p.text}".rstrip() for p in thread.posts]
    return Item(hit.source_id, _iso(_epoch(thread.updated)), f"{hit.source_id} [{kind} {thread.state}]: {thread.title}",
                "\n\n---\n\n".join(blocks),
                {"url": thread.url, "repo": hit.repo, "number": hit.number, "kind": kind.lower(),
                 "state": thread.state},
                hit.epoch)


def extract(run):
    now = time.time()
    options = run.options
    start = now - options.window_days * DAY if run.since is None else run.since - OVERLAP
    hits = [h for h in _hits(options, start, now) if not run.excludes(h.repo)]
    pool = ThreadPoolExecutor(FETCH_WORKERS)
    try:
        for thread in pool.map(lambda hit: _thread(options, hit), hits):
            yield _render(thread)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if not hits:
        yield Progress(now)


TYPE = SourceType(Options, extract)
