"""What each dashboard page shows, as plain data. Every function reads a store opened read-only and writes nothing.

Text that comes out has been masked: a row of today's mask version is shown as stored, as `get` shows it, and any
other row is masked again whole first.
"""
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

from kb import search, store
from kb.dashboard import catalog as cat
from kb.mask import mask, mask_all

PAGE_CHARS = 30000
LIST_LIMIT = 50
DAILY_DAYS = 90
STALE_HOURS = 1
LOG_TAIL = 2_000_000
LOG_DAYS = 14
SEARCH_LIMIT = 25
TOP = 8
URL_SCHEMES = ("http://", "https://", "obsidian://", "imessage://", "sms:")
TURN = re.compile(r"(?:\A|(?<=\n\n))(USER|ASSISTANT|ERROR|RESULT|TOOL [^\s:]+): ")
LOG_LINE = re.compile(r"(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d),\d+ (\w+) (.*)")
RUN_END = re.compile(r"ingest done: (\d+) failures?")


def parse_ts(ts):
    """A stored time as an aware datetime, or None."""
    text = str(ts).replace("Z", "+00:00")
    for candidate in (text, re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], text)):
        try:
            then = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return then if then.tzinfo else then.replace(tzinfo=timezone.utc)
    return None


def local_day(ts):
    """The calendar day an item belongs to. A bare midnight-UTC stamp is a date, as a note's is, not an instant."""
    ts = str(ts)
    if ts[10:] in ("T00:00:00Z", "T00:00:00.000Z"):
        return ts[:10]
    then = parse_ts(ts)
    return then.astimezone().date().isoformat() if then else ts[:10]


def safe_url(url):
    url = str(url or "")
    return url if url.startswith(URL_SCHEMES) else None


def _row(entry):
    return {"id": entry.id, "source": entry.source, "ts": entry.ts, "title": entry.title, "family": entry.family}


def _listing(entries, offset):
    return {"items": [_row(e) for e in entries[offset:offset + LIST_LIMIT]], "total": len(entries), "offset": offset,
            "limit": LIST_LIMIT}


def group_summary(group):
    return {"key": group.key, "name": group.name, "count": group.count, "last": group.last,
            "families": group.by_family()}


def group_list(groups, automated=None):
    chosen = [g for g in groups.values() if automated is None or g.automated == automated]
    return sorted((group_summary(g) for g in chosen), key=lambda g: (-g["count"], g["name"].casefold()))


def projects(catalog):
    return group_list(catalog.projects)


def people(catalog):
    return {"people": group_list(catalog.people, automated=False), "senders": group_list(catalog.people, automated=True)}


def project(catalog, key, offset=0):
    group = catalog.projects.get(key.casefold())
    if group is None:
        return None
    return {**group_summary(group), **_listing(group.entries, offset), "daily": _daily(group.entries)}


def person(catalog, key, offset=0):
    group = catalog.people.get(key.casefold())
    if group is None:
        return None
    first = min((e.ts for e in group.entries), default="")
    return {**group_summary(group), **_listing(group.entries, offset), "addresses": sorted(group.addresses),
            "first": first, "automated": group.automated, "daily": _daily(group.entries)}


def source(catalog, name, offset=0):
    entries = catalog.sources.get(name)
    if entries is None:
        return None
    counts = {}
    for entry in entries:
        counts[entry.family] = counts.get(entry.family, 0) + 1
    return {"name": name, "families": counts, **_listing(entries, offset), "daily": _daily(entries)}


def _daily(entries, days=DAILY_DAYS):
    """Items per day, by family, over the `days` days that end on the newest item's day."""
    if not entries:
        return {"end": None, "days": []}
    counts = {}
    for entry in entries:
        by = counts.setdefault(local_day(entry.ts), {})
        by[entry.family] = by.get(entry.family, 0) + 1
    end = date.fromisoformat(max(counts))
    out = []
    for i in range(days):
        day = (end - timedelta(days=days - 1 - i)).isoformat()
        by = counts.get(day, {})
        out.append({"day": day, "n": sum(by.values()), "by": by})
    return {"end": end.isoformat(), "days": out}


def read_log(path, now=None):
    """Runs and failures from the end of the ingest log: each run ends at "ingest done", and each ERROR line starts a
    failure whose reason is the exception line that closes its traceback."""
    now = now or datetime.now()
    cutoff = (now - timedelta(days=LOG_DAYS)).strftime("%Y-%m-%d")
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - LOG_TAIL))
            lines = fh.read().decode("utf-8", "replace").split("\n")[1 if size > LOG_TAIL else 0:]
    except OSError:
        return {"runs": [], "failures": [], "log": False}
    runs, failures, pending, last = [], [], [], None
    for line in lines:
        m = LOG_LINE.match(line)
        if not m:
            if last is not None and line.strip() and not line.startswith((" ", "Traceback")):
                last["reason"] = mask(line.strip())[:240]
                last = None
            continue
        day, clock, level, message = m.groups()
        if day < cutoff:
            continue
        when = datetime.strptime(f"{day} {clock}", "%Y-%m-%d %H:%M:%S").astimezone().isoformat()
        done = RUN_END.match(message)
        if done:
            runs.append({"t": when, "failures": int(done.group(1)), "failed": sorted({f["what"] for f in pending})})
            pending = []
            last = None
        elif level in ("ERROR", "CRITICAL"):
            last = {"t": when, "what": mask(message.replace(" failed", ""))[:120], "reason": ""}
            failures.append(last)
            pending.append(last)
    return {"runs": runs[-1400:], "failures": failures[::-1][:30], "log": True}


def _units(db):
    ok = dict(store.succeeded(db))
    failing = {}
    for unit, line in store.failing(db):
        when, _, error = line.partition(" ")
        failing[unit] = {"t": when, "error": error}
    return ok, failing


def source_rows(db, catalog, config):
    ok, failing = _units(db)
    names = set(catalog.sources) | {u.split("@", 1)[0] for u in (*ok, *failing)} - {"summarize", "sync"}
    names |= {s.name for s in config.sources} if config else set()
    rows = []
    for name in sorted(names):
        entries = catalog.sources.get(name, [])
        units = [u for u in {*ok, *failing} if u.split("@", 1)[0] == name]
        if config:
            units = sorted({*units, *(f"{s.name}@{h}" for s in config.sources if s.name == name for h in s.hosts)})
        bad = [{"unit": u, **failing[u]} for u in units if u in failing]
        done = [ok[u] for u in units if u in ok]
        rows.append({"name": name, "items": len(entries), "newest": max((e.ts for e in entries), default=None),
                     "families": sorted({e.family for e in entries}), "last_ok": max(done, default=None),
                     "failing": bad, "units": units, "configured": bool(config and any(
                         s.name == name for s in config.sources))})
    for unit in ("summarize", "sync"):
        if unit in ok or unit in failing:
            rows.append({"name": unit, "items": None, "newest": None, "families": [], "last_ok": ok.get(unit),
                         "failing": [{"unit": unit, **failing[unit]}] if unit in failing else [], "units": [unit],
                         "configured": True, "service": True})
    return rows


def overview(db, catalog, paths, config):
    docs = db.execute("SELECT count(*), sum(kind IN ('segment', 'atom')) FROM docs").fetchone()
    counts = {}
    for entry in catalog.entries:
        counts[entry.family] = counts.get(entry.family, 0) + 1
    ok, failing = _units(db)
    logs = read_log(paths.log("ingest"))
    ranked = sorted(catalog.projects.values(), key=lambda g: (-g.count, g.name.casefold()))[:TOP]
    folks = sorted((g for g in catalog.people.values() if not g.automated),
                   key=lambda g: (-g.count, g.name.casefold()))[:TOP]
    newest = max((e.ts for e in catalog.entries), default=None)
    return {
        "now": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "db_mb": round(os.path.getsize(paths.db) / 1e6, 1) if paths.db.exists() else 0,
        "items": len(catalog.entries), "rows": docs[0], "summaries": docs[1] or 0, "families": counts,
        "newest": newest, "last_ingest": store.last_ingest(db), "stale_hours": STALE_HOURS,
        "failing": [{"unit": u, **f} for u, f in sorted(failing.items())],
        "away": [{"host": h, "since": since} for h, since in store.away(db)],
        "old_rows": store.old_rows(db),
        "mask_capped": int(store.get_watermark(db, store.STATE, "mask_capped") or 0),
        "sources": source_rows(db, catalog, config), "daily": _daily(catalog.entries),
        "projects": [group_summary(g) for g in ranked], "people": [group_summary(g) for g in folks],
        "project_total": len(catalog.projects), "people_total": len(catalog.people), **logs,
    }


def search_hits(db, query, source_name=None):
    hits = search.search(db, query[:200], sources=(source_name,) if source_name else (), limit=SEARCH_LIMIT)
    out = []
    for hit in hits:
        meta = hit["meta"]
        out.append({"id": hit["id"], "kind": hit["kind"], "source": hit["source"], "ts": hit["ts"],
                    "title": hit["title"], "excerpt": hit["excerpt"], "family": cat.family_of(meta),
                    "link": search.pointer(meta), "url": safe_url(meta.get("url"))})
    return out


def turn_starts(text):
    return [m.start() for m in TURN.finditer(text)]


def page_bounds(text, turns=()):
    """Where each page starts. A page ends at a turn start in its second half when one exists, else at a line end."""
    starts, i = [0], 0
    half = PAGE_CHARS // 2
    while i + PAGE_CHARS < len(text):
        cut = max((t for t in turns if i + half < t <= i + PAGE_CHARS), default=0)
        cut = cut or text.rfind("\n", i + half, i + PAGE_CHARS) + 1 or i + PAGE_CHARS
        starts.append(cut)
        i = cut
    return starts


def parse_turns(text):
    """The turns in a page of an agent session's text. Text before the first marker, such as the line naming the
    folder, is the head. A tool call is folded with the result or error that follows it."""
    marks = list(TURN.finditer(text))
    head = text[:marks[0].start()].strip() if marks else text.strip()
    raw = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end].strip("\n")
        label = m.group(1)
        raw.append({"role": "tool" if label.startswith("TOOL") else label.lower(),
                    "name": label[5:] if label.startswith("TOOL") else None, "text": body, "at": m.start()})
    turns = []
    for turn in raw:
        if turn["role"] in ("result", "error") and turns and turns[-1]["role"] == "tool" and "output" not in turns[-1]:
            turns[-1]["output"], turns[-1]["failed"] = turn["text"], turn["role"] == "error"
        elif turn["role"] == "tool":
            turns.append({"role": "tool", "name": turn["name"], "input": turn["text"], "at": turn["at"]})
        else:
            turns.append(turn)
    return head, turns


def is_session(text):
    return bool(re.match(r"(?:Claude Code|Codex|Hermes)\b[^\n]* session", text)) and "\n\nUSER: " in text


def item(db, catalog, rid, page=None, query=""):
    row = db.execute("SELECT id, kind, source, source_id, metadata FROM docs WHERE id = ?", (rid,)).fetchone()
    if not row:
        return None
    rid, kind, src, sid, meta = row
    rows = store.item_rows(db, rid)
    if not rows:
        return {"error": "This is a summary whose item has no stored text."}
    ids = [r[0] for r in rows]
    if store.unversioned(db, ids):
        return {"error": "This item was written by an older version. Run `superhistory rebuild`."}
    fresh = len(store.current(db, ids)) == len(ids)
    if not fresh and store.secret_id(src, sid, store.item_names(db, src, sid)):
        return None
    first = rows[0]
    item_meta = json.loads(first[5])
    title = first[3]
    text = "".join(r[4] for r in rows)
    summaries = [{"kind": k, "title": t, "content": c} for k, t, c in db.execute(
        "SELECT kind, title, content FROM docs WHERE source = ? AND source_id = ? AND kind IN ('segment', 'atom') "
        "ORDER BY id LIMIT 40", (src, sid))]
    if not fresh:
        text, title, item_meta = mask(text), mask(title), mask_all(item_meta)
        summaries = [{**s, "title": mask(s["title"]), "content": mask(s["content"])} for s in summaries]
    anchor = None
    own = json.loads(meta)
    if kind in ("segment", "atom") and isinstance(own.get("from_chars"), int):
        anchor = own["from_chars"]
    elif kind == "raw":
        anchor = sum(len(r[4]) for r in rows[:ids.index(rid)]) if rid in ids else None
    if query:
        # With no place of its own the query's first word marks one; a chunk's place moves to the word within the chunk.
        low = text.lower()
        span = len(rows[ids.index(rid)][4]) if kind == "raw" and rid in ids else len(text)
        start = anchor or 0
        found = [low.find(t, start, start + span) for t in re.findall(r"\w{3,}", query.lower())]
        found = [f for f in found if f >= 0]
        if found and (anchor is None or kind == "raw"):
            anchor = min(found)
    if anchor is not None:
        anchor = max(0, min(anchor, max(len(text) - 1, 0)))
    session = cat.family_of(item_meta) == "agent" and is_session(text)
    starts = page_bounds(text, turn_starts(text) if session else ())
    if page is None:
        page = max(i for i, s in enumerate(starts) if s <= (anchor or 0))
    page = max(0, min(page, len(starts) - 1))
    begin = starts[page]
    end = starts[page + 1] if page + 1 < len(starts) else len(text)
    entry = catalog.by_id.get(first[0])
    family = entry.family if entry else cat.family_of(item_meta)
    out = {"id": first[0], "kind": kind, "source": src, "ts": first[2], "title": title, "family": family,
           "summaries": summaries, "page": page, "pages": len(starts), "offset": begin, "end": end, "total": len(text),
           "anchor": anchor - begin if anchor is not None and begin <= anchor < end else None,
           "link": search.pointer(item_meta), "url": safe_url(item_meta.get("url")),
           "superseded": bool(store.superseded_by(db, src, sid)), "session": session,
           "project": None, "people": []}
    if entry:
        if entry.project and entry.project in catalog.projects:
            out["project"] = {"key": entry.project, "name": catalog.projects[entry.project].name}
        out["people"] = [{"key": k, "name": catalog.people[k].name} for k in entry.people if k in catalog.people]
    if session:
        out["head"], out["turns"] = parse_turns(text[begin:end])
    else:
        out["text"] = text[begin:end]
    return out
