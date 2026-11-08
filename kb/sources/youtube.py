"""Videos from one YouTube channel, read with `yt-dlp` and the channel's public feed: one item per video, short or
stream, holding its title, description and captions. It needs no API key and no Google account.

The first run lists the channel's videos, shorts and streams with yt-dlp and reads each one. Later runs read the
channel's feed, which lists its newest FEED_SIZE uploads with their publish times, and read only those published
since the last run; when every feed entry is new, the run lists the whole channel again so none is missed. When the
feed cannot be read, the run lists the channel with yt-dlp and reads the times of each tab's newest PROBE videos. A video
that is still live, still processing, or has no captions yet is left for a later run, up to WAIT_DAYS after it was
published, and so is every video published at or after it, so the watermark never passes it. A video in the feed
that yt-dlp cannot read fails the run, so it is tried again; exclude its id if it stays unreadable.

`channel` is "@handle", a channel URL or a channel id ("UC..."). `yt-dlp` must be installed.

Exclude key: the video's kind and id, such as "short/abc123"; the kinds are "video", "short" and "livestream".
"""
import dataclasses
import glob
import html
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Tuple

from kb.mask import clip, mask
from kb.sources import Item, SourceType

DAY = 86400
WAIT_DAYS = 1
FEED_SIZE = 15
# Without the feed, how many of each tab's newest videos are read for their publish times.
PROBE = 3
TABS = ("videos", "shorts", "streams")
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
CHANNEL_ID = re.compile(r"UC[0-9A-Za-z_-]{22}")
# yt-dlp reads one video in a few seconds; a whole channel gets this long per video it lists.
YTDLP_TIMEOUT = 300
PER_VIDEO = 30
HTTP_TIMEOUT = 30
TEXT_CHARS = 20000
TRANSCRIPT_CHARS = 200000
USER_AGENT = "Mozilla/5.0 (compatible; kb)"
NOT_READY = ("is_live", "is_upcoming", "post_live")
ATOM = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


@dataclass(frozen=True)
class Options:
    channel: str
    # Captions in the first of these languages the video has, its own or YouTube's automatic ones.
    languages: Tuple[str, ...] = ("en",)
    captions: bool = True
    yt_dlp: str = "yt-dlp"


def run_ytdlp(binary, args, timeout=YTDLP_TIMEOUT):
    try:
        proc = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(mask(f"yt-dlp did not run: {exc}")) from None
    return proc


def _ytdlp_error(proc):
    lines = [line for line in proc.stderr.splitlines() if line.startswith("ERROR")] or proc.stderr.splitlines()
    return mask(f"yt-dlp exited {proc.returncode}: ") + mask("\n".join(lines).strip())[-400:]


def fetch(url):
    request = urllib.request.Request(url, headers={"user-agent": USER_AGENT, "accept-language": "en"})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(mask(f"YouTube answered HTTP {exc.code} for {url}")) from None
    except Exception as exc:
        raise RuntimeError(mask(f"cannot reach {url}: {getattr(exc, 'reason', exc)}")) from None


def channel_url(channel):
    channel = channel.strip().rstrip("/")
    if channel.startswith(("http://", "https://")):
        return channel
    if CHANNEL_ID.fullmatch(channel):
        return f"https://www.youtube.com/channel/{channel}"
    return f"https://www.youtube.com/@{channel.lstrip('@')}"


def channel_id(channel):
    if CHANNEL_ID.fullmatch(channel.strip()):
        return channel.strip()
    page = fetch(channel_url(channel))
    # The page links its own feed and names its id; YouTube serves either one depending on the visitor.
    for pattern in (r'feeds/videos\.xml\?channel_id=(UC[0-9A-Za-z_-]{22})"',
                    r'<meta itemprop="identifier" content="(UC[0-9A-Za-z_-]{22})"',
                    r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[0-9A-Za-z_-]{22})"'):
        found = re.search(pattern, page)
        if found:
            return found.group(1)
    raise RuntimeError(mask(f"found no YouTube channel at {channel_url(channel)}"))


def _epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def feed(cid):
    """The channel's newest uploads as (video id, publish time), newest first."""
    try:
        root = ET.fromstring(fetch(FEED.format(cid)))
    except ET.ParseError as exc:
        raise RuntimeError(f"the YouTube feed for {cid} is not readable: {exc}") from None
    return [(e.findtext("yt:videoId", "", ATOM), _epoch(e.findtext("a:published", "", ATOM)))
            for e in root.findall("a:entry", ATOM)]


def tabs(options):
    """{tab: its video ids, newest first} for the videos, shorts and streams tabs the channel has."""
    found = {}
    for tab in TABS:
        proc = run_ytdlp(options.yt_dlp, ["--flat-playlist", "--print", "id", f"{channel_url(options.channel)}/{tab}"])
        if proc.returncode and "does not have a" in proc.stderr:
            continue
        if proc.returncode:
            raise RuntimeError(_ytdlp_error(proc))
        found[tab] = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return found


def listing(options):
    """Every video id on the channel's videos, shorts and streams tabs."""
    return list(dict.fromkeys(vid for ids in tabs(options).values() for vid in ids))


def without_feed(options, since):
    """The videos to read and their publish times when the feed cannot be read. yt-dlp reads the times of the newest
    PROBE videos on each tab; a tab whose probed videos are all newer than `since` is read whole, since more may be
    new behind them. A probed video yt-dlp cannot read counts as new, so a later step decides about it."""
    by_tab = tabs(options)
    probed = read_videos(dataclasses.replace(options, captions=False),
                         [vid for ids in by_tab.values() for vid in ids[:PROBE]])
    times = {vid: info.get("timestamp") or info.get("release_timestamp") for vid, (info, _) in probed.items()}
    times = {vid: at for vid, at in times.items() if at}
    ids = []
    for listed in by_tab.values():
        new = [vid for vid in listed[:PROBE] if times.get(vid, since + 1) > since]
        ids += listed if len(new) == PROBE and len(listed) > PROBE else new
    return list(dict.fromkeys(ids)), times


def _sub_langs(languages):
    return ",".join(f"{lang},{lang}-orig" for lang in languages)


def read_videos(options, ids):
    """{video id: (yt-dlp's info, transcript)} for the videos yt-dlp could read. One that is private, removed or not
    out yet is missing."""
    if not ids:
        return {}
    with tempfile.TemporaryDirectory() as folder:
        args = ["--skip-download", "--write-info-json", "--no-write-playlist-metafiles", "--ignore-errors",
                "--no-warnings", "-o", os.path.join(folder, "%(id)s.%(ext)s")]
        if options.captions:
            args += ["--write-subs", "--write-auto-subs", "--sub-format", "vtt", "--sub-langs",
                     _sub_langs(options.languages)]
        proc = run_ytdlp(options.yt_dlp, args + ["--", *ids], YTDLP_TIMEOUT + PER_VIDEO * len(ids))
        found = {}
        for path in glob.glob(os.path.join(folder, "*.info.json")):
            with open(path, encoding="utf-8") as fh:
                info = json.load(fh)
            found[info["id"]] = (info, _captions(folder, info["id"], options.languages))
        # An error about one video leaves it out, and extract decides whether to wait for it; any other error, such
        # as yt-dlp being blocked, fails the read.
        errors = [line for line in proc.stderr.splitlines() if line.startswith("ERROR")]
        if proc.returncode and (not errors or not all(any(f"] {vid}:" in line for vid in ids) for line in errors)):
            raise RuntimeError(_ytdlp_error(proc))
        return found


def _captions(folder, video, languages):
    files = {os.path.basename(p)[len(video) + 1:-len(".vtt")]: p for p in glob.glob(os.path.join(folder, f"{video}.*.vtt"))}
    for lang in languages:
        for name in (lang, f"{lang}-orig"):
            if name in files:
                with open(files[name], encoding="utf-8", errors="replace") as fh:
                    return transcript(fh.read())
    return ""


def transcript(vtt):
    """The words of a WebVTT file. YouTube's automatic captions repeat each line in the cue after it, so a line equal
    to the one before is dropped."""
    lines = []
    for line in vtt.splitlines():
        if "-->" in line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or line.strip().isdigit():
            continue
        text = " ".join(html.unescape(re.sub(r"<[^>]*>", "", line)).split())
        if text and (not lines or lines[-1] != text):
            lines.append(text)
    return " ".join(lines)


def _kind(info):
    return info.get("media_type") or "video"


def _ready(info, words, options):
    if info.get("live_status") in NOT_READY:
        return False
    return bool(words) or not options.captions


def _render(info, words, mark):
    kind = _kind(info)
    when = info.get("timestamp") or info.get("release_timestamp") or mark
    blocks = [f"YouTube {kind} on {info.get('channel') or info.get('uploader') or 'its channel'}"
              + (f" | {int(info['duration']) // 60} min" if info.get("duration") else ""),
              clip((info.get("description") or "").strip(), TEXT_CHARS) or "(no description)"]
    if words:
        blocks.append("Transcript:\n" + clip(words, TRANSCRIPT_CHARS))
    return Item(info["id"], _iso(when), info.get("title") or info["id"], "\n\n".join(blocks),
                {"url": info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}",
                 "channel": info.get("channel") or "", "kind": kind, "duration": info.get("duration") or 0},
                mark)


def extract(run):
    options, since, now = run.options, run.since, time.time()
    try:
        published = dict(feed(channel_id(options.channel)))
    except RuntimeError:
        # YouTube's feed has gone down for every channel at once (2026-11-07); yt-dlp can still list the channel.
        if since is not None:
            ids, published = without_feed(options, since)
        else:
            ids, published = listing(options), {}
    else:
        new = [vid for vid, at in published.items() if since is None or at > since]
        # A feed of FEED_SIZE entries, all new, may have dropped older new ones off its end.
        if since is None or (len(published) >= FEED_SIZE and len(new) == len(published)):
            ids = listing(options)
        else:
            ids = new
    # A bare id in exclude skips a video before yt-dlp reads it, so one it cannot read can be left out.
    ids = [vid for vid in ids if not run.excludes(vid)]
    videos = read_videos(options, ids)

    def mark(vid):
        info = videos.get(vid, ({}, ""))[0]
        return published.get(vid) or info.get("timestamp") or info.get("release_timestamp") or 0

    # A listed video with no time could not be read; on a later run it may be one pushed off the feed while waiting.
    order = sorted((vid for vid in ids if since is None or mark(vid) > since or not mark(vid)),
                   key=lambda vid: (mark(vid), vid))
    ready, wait = [], None
    for vid in order:
        info, words = videos.get(vid, (None, ""))
        if run.excludes(vid if info is None else f"{_kind(info)}/{vid}"):
            continue
        if info is not None and _ready(info, words, options):
            ready.append((info, words))
        elif mark(vid) and now - mark(vid) < WAIT_DAYS * DAY:
            # Captions and a finished stream come some hours after publishing; a later run reads it again.
            wait = mark(vid)
            break
        elif info is None and (vid in published or since is not None):
            # A public upload yt-dlp could not read now. Skipping it would move the watermark past it for good.
            raise RuntimeError(f"yt-dlp could not read video {vid}; if it stays unreadable, add it to exclude")
        elif info is not None and info.get("live_status") not in NOT_READY:
            ready.append((info, words))
    for info, words in ready:
        # A video published in the same second as the one waiting would move the watermark onto it.
        if wait is not None and mark(info["id"]) >= wait:
            return
        yield _render(info, words, mark(info["id"]))


def _account(options):
    name = channel_url(options.channel).rsplit("/", 1)[-1]
    return f"youtube:{name if CHANNEL_ID.fullmatch(name) else name.lstrip('@').lower()}"


TYPE = SourceType(Options, extract, account=_account)
