import json
import math
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from kb import decisions, store
from kb.mask import mask, mask_all
from kb.sources import delegated
from kb.store import SUMMARY_KINDS, TOKENIZE, current, item_names, secret_id, unversioned

STOPWORDS = set("""a an and are as at be been but by can could did do does doing for from had has have
how i if in into is it its just me my no not of on or our should so than that the their them then there
these they this those to was we were what when where which who why will with would you your about any
all also get got make made say said tell whats wheres theres heres hows whos thats now still again thing things
ones much often like ever already currently really supposed stuff end up back""".split())
# A word's punctuation at either end, and a contraction's ending: "again," is "again" and "where's" is "where".
EDGES = re.compile(r"^[^\w~/.-]+|[^\w/]+$")
CONTRACTION = re.compile(r"['’](?:s|re|ll|ve|d|m)$", re.I)
RRF_K = 60
RAW_WEIGHT = 0.5
# The share of its score a record keeps when it holds only one of the query's words; the rest grows with each other
# word it holds, so a note missing one word of a long question keeps most of its score.
COVERAGE_FLOOR = 0.4
# A record ranks up to twice its score when new, the bonus shrinking by e every DECAY_DAYS. A chat, mail, issue or
# agent session goes out of date faster than a note, which whoever keeps it edits.
DECAY_DAYS = 180.0
EPHEMERAL = ("claude_code", "codex", "hermes", "gmail", "imessage", "github")  # source types, or names
EPHEMERAL_DECAY_DAYS = 60.0
# A note its owner edited in the last NOTE_LIVE_DAYS is the record kept current, so it outranks talk about the same
# thing. One left alone longer is a record of its day, as is any note under a folder named in NOT_KEPT, such as an
# agent's state log.
NOTE_WEIGHT = 2.0
NOTE_LIVE_DAYS = 14.0
NOT_KEPT = ("state",)
# A helper session another agent started repeats its parent's words. A decision found in one keeps its rank: the
# decisions pass judged it, and the helper may be where it was made. The session's first message is in the first
# HEAD_CHARS of its text, after its header line.
HELPER_WEIGHT = 0.5
HELPER_KEEPS = ("decision",)
HEAD_CHARS = 1000
CANDIDATES = 60
# Rows a search scores before it groups them by item. A common word like "test" can match a third of a store's rows,
# and reading each one's item took up to 3.5 s on a 440,000-row store; the top 3,000 held every full list in 400
# real lookups.
TOP_ROWS = 3000
# The longest passage FTS5's snippet() gives: the stretch of a record where its matching words cluster.
EXCERPT_TOKENS = 64
# The short snippet with its matches in brackets, then the excerpt with them marked for excerpt() to cut around.
SNIPPETS = ("snippet({table}, {column}, '[', ']', ' … ', 24), "
            "snippet({table}, {column}, char(2), char(3), ' … ', " + str(EXCERPT_TOKENS) + ")")
EXCERPT_CHARS = 500
SNIPPET_CHARS = 220
MARK, END = "\x02", "\x03"
STALE_NOTE = "[Out of date: replaced on {date} by #{id}: {text}] "
STALE_TEXT_CHARS = 140
# The share of a replaced decision's words a record must hold to count as stating it (decisions.terms).
STATES = 0.5
# A record that states a replaced or rejected decision ranks at this share of its score.
STALE_WEIGHT = 0.5
REJECTED_NOTE = "[A decision here was rejected by the owner] "


def fts_terms(text):
    """Each word or "quoted phrase" becomes a quoted FTS phrase. A word loses its punctuation at either end and a
    contraction's ending first, so "again," and "where's" are the stopwords they hold."""
    terms = []
    for token in re.findall(r'"[^"]+"|\S+', text):
        quoted = len(token) > 1 and token[0] == token[-1] == '"'
        token = token.strip('"').replace('"', "")
        if not quoted:
            token = CONTRACTION.sub("", EDGES.sub("", token))
        words = re.findall(r"\w+", token)
        if not words or (len(words) == 1 and words[0].lower() in STOPWORDS):
            continue
        phrase = f'"{token}"'
        if phrase not in terms:
            terms.append(phrase)
    return terms


def fts_query(text):
    return " OR ".join(fts_terms(text))


def _ranked(db, match, after, sources, n, before=None):
    """Best-first row ids for the summary list (doc, segment and atom rows) and for the raw list, one per item in each,
    so one long session can't fill a list. One full-text match feeds both: scoring every matching row is most of a
    search's time."""
    best = db.execute("SELECT rowid, bm25(docs_fts, 4.0, 1.0) AS score FROM docs_fts WHERE docs_fts MATCH ? "
                      "ORDER BY score, rowid LIMIT ?", (match, TOP_ROWS + 1)).fetchall()
    # Exact when every list fills from the best rows: an item with no row among them scores below every item with one.
    # A tie across the cut would make "the best rows" ambiguous, so it counts as not filling.
    if len(best) <= TOP_ROWS or best[TOP_ROWS - 1][1] != best[TOP_ROWS][1]:
        # json_extract, not ->>, which needs SQLite 3.38 (Ubuntu 22.04's Python has 3.37).
        rows = "SELECT json_extract(value, '$[0]') AS id, json_extract(value, '$[1]') AS score FROM json_each(?)"
        lists = _grouped(db, rows, json.dumps(best[:TOP_ROWS]), after, sources, n, before)
        if len(best) <= TOP_ROWS or min(map(len, lists)) >= n:
            return lists
    rows = "SELECT rowid AS id, bm25(docs_fts, 4.0, 1.0) AS score FROM docs_fts WHERE docs_fts MATCH ?"
    return _grouped(db, rows, match, after, sources, n, before)


def _grouped(db, rows, rows_arg, after, sources, n, before):
    """The two lists from `rows`, a query of (id, score) taking one argument: the best row of each item, best first,
    ties by row id so every run orders them the same. Each list keeps its own `n` places."""
    kinds = ("doc",) + SUMMARY_KINDS + ("raw",)
    # CROSS JOIN keeps the rows first; otherwise SQLite may walk every doc and run the full-text match for each.
    sql = (f"SELECT d.id, d.source, d.source_id, d.kind = 'raw' AS raw, r.score FROM ({rows}) r "
           f"CROSS JOIN docs d {store.by_id(db)} ON d.id = r.id WHERE d.kind IN ({','.join('?' * len(kinds))})")
    args = [rows_arg, *kinds]
    if after:
        sql += " AND d.ts >= ?"
        args.append(after)
    if before:
        sql += " AND d.ts < ?"
        args.append(before)
    if sources:
        sql += f" AND d.source IN ({','.join('?' * len(sources))})"
        args += list(sources)
    # One row per item before the limit: a session with hundreds of matching rows would otherwise take every slot.
    # MATERIALIZED keeps bm25() inside the full-text query, the only place SQLite allows it.
    sql = (f"WITH m AS MATERIALIZED ({sql}) SELECT id, raw FROM ("
           f"SELECT id, raw, row_number() OVER (PARTITION BY raw ORDER BY score, id) AS place FROM ("
           f"SELECT id, raw, score, row_number() OVER (PARTITION BY raw, source, source_id ORDER BY score, id) AS nth "
           f"FROM m) WHERE nth = 1) WHERE place <= ? ORDER BY raw, place")
    lists = ([], [])
    for rid, raw in db.execute(sql, (*args, n)):
        lists[raw].append(rid)
    return lists


def _unsummarized(db, ids):
    """Those of the raw rows `ids` whose item has no doc or summary row."""
    if not ids:
        return set()
    return {i for (i,) in db.execute(
        f"SELECT d.id FROM docs d WHERE d.id IN ({','.join('?' * len(ids))}) AND NOT EXISTS (SELECT 1 FROM docs s "
        f"WHERE s.source = d.source AND s.source_id = d.source_id AND s.kind IN ('doc', 'segment', 'atom'))", ids)}


def _stale_rows(db, match, rows):
    """{id: (the newer decisions, whether the owner rejected one)} for those of `rows` that state a decision search
    leaves out, so search ranks them lower and each carries a note naming what replaced it. A row states a decision
    of its own item that matches the search when it holds at least STATES of the decision's words, more of them than
    of the newer decision's, and is older than the newer decision: the same session's other records, and the one
    written after the change, keep their rank and get no note."""
    items = sorted({(r[2], r[3]) for r in rows})
    marked = set()
    for i in range(0, len(items), 300):
        batch = items[i:i + 300]
        where = " OR ".join("(source=? AND source_id=?)" for _ in batch)
        marked.update(db.execute(f"SELECT DISTINCT source, source_id FROM decisions WHERE (new_key IS NOT NULL "
                                 f"OR answer='rejected') AND ({where})", [v for t in batch for v in t]))
    found = {}
    for src, sid in sorted(marked):
        atoms = db.execute("SELECT id, source, source_id, kind, content FROM docs WHERE source=? AND source_id=? "
                           "AND kind='atom'", (src, sid)).fetchall()
        matching = {i for (i,) in db.execute(
            f"SELECT rowid FROM docs_fts WHERE docs_fts MATCH ? AND rowid IN ({','.join('?' * len(atoms))})",
            (match, *[a[0] for a in atoms]))} if atoms else set()
        texts = {a[0]: a[4] for a in atoms}
        olds = []
        for rid, by in decisions.hidden(db, [a for a in atoms if a[0] in matching]).items():
            new = None if by == "rejected" else decisions._find(db, by)
            if by == "rejected" or new:
                olds.append((decisions.terms(texts[rid]), new))
        for row in rows:
            if (row[2], row[3]) != (src, sid):
                continue
            words = set(decisions.terms(f"{row[5]} {row[6]}"))
            newer, rejected = [], False
            for old, new in olds:
                share = _share(old, words)
                if share < STATES or new and (row[4] >= new.ts or _share(decisions.terms(new.text), words) >= share):
                    continue
                rejected |= new is None
                if new and new not in newer:
                    newer.append(new)
            if newer or rejected:
                found[row[0]] = (newer, rejected)
    return found


def _share(words, found):
    return sum(w in found for w in words) / len(words) if words else 0.0


def _stand_ins(db, match, lost, fused, after, before):
    """For each hidden decision that was its item's best row, the item's best other matching row that is not hidden,
    in the same rank, so a hidden decision does not take a current one from the same item out of the results."""
    found = []
    for rid, kind, src, sid, *_ in lost:
        kinds = ("doc",) + SUMMARY_KINDS if kind != "raw" else ("raw",)
        sql = (f"SELECT id FROM docs WHERE source=? AND source_id=? AND kind IN ({','.join('?' * len(kinds))})"
               + (" AND ts >= ?" if after else "") + (" AND ts < ?" if before else ""))
        ids = [i for (i,) in db.execute(sql, (src, sid, *kinds, *[t for t in (after, before) if t]))
               if i not in fused]
        if not ids:
            continue
        rows = db.execute(
            f"SELECT d.id, d.kind, d.source, d.source_id, d.ts, d.title, d.content, d.metadata FROM docs_fts "
            f"JOIN docs d ON d.id = docs_fts.rowid WHERE docs_fts MATCH ? AND docs_fts.rowid IN "
            f"({','.join('?' * len(ids))}) ORDER BY bm25(docs_fts, 4.0, 1.0), d.id", (match, *ids)).fetchall()
        out = decisions.hidden(db, [(r[0], r[2], r[3], r[1], r[6]) for r in rows])
        row = next((r for r in rows if r[0] not in out), None)
        if row:
            fused[row[0]] = fused[rid]
            found.append(row)
    return found


def _coverage(db, terms, ids):
    if len(terms) < 2:
        return dict.fromkeys(ids, 1.0)
    hits = dict.fromkeys(ids, 0)
    marks = ",".join("?" * len(ids))
    for term in terms:
        for (rid,) in db.execute(f"SELECT rowid FROM docs_fts WHERE docs_fts MATCH ? AND rowid IN ({marks})",
                                 (term, *ids)):
            hits[rid] += 1
    return {rid: COVERAGE_FLOOR + (1 - COVERAGE_FLOOR) * (n - 1) / (len(terms) - 1) if n else 0.0
            for rid, n in hits.items()}


def _age_days(ts, now):
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return 3650.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return abs((now - t).total_seconds()) / 86400


def source_types():
    """{source name: type name} from the user's config file, read once. A source it does not name is of the type its
    name names, as in the config itself; an unreadable file names none."""
    global _TYPES
    if _TYPES is None:
        try:
            from kb import config, paths
            with open(paths.Paths.for_user().config_file, encoding="utf-8") as fh:
                tables = config.read_toml(fh.read()).get("sources", {})
            _TYPES = {name: t.get("type", name) for name, t in tables.items() if isinstance(t, dict)}
        except (OSError, ValueError, AttributeError):
            _TYPES = {}
    return _TYPES


_TYPES = None


def _note_weight(source_type, source_id, meta, age):
    """NOTE_WEIGHT for a kept note, else 1."""
    if source_type != "notes" or age > NOTE_LIVE_DAYS:
        return 1.0
    folders = {f.lower() for f in str(meta.get("path") or source_id).split("/")[:-1]}
    return 1.0 if folders & set(NOT_KEPT) else NOTE_WEIGHT


def _helpers(db, items):
    """Those of `items`, (source, source_id) pairs, that are helper sessions another agent started: ingest marks one
    "subagent", and an item stored before it did shows it in its first message."""
    found = set()
    for src, sid in items:
        row = db.execute("SELECT title, substr(content, 1, ?), metadata FROM docs WHERE source=? AND source_id=? "
                         "AND kind IN ('doc', 'raw') ORDER BY kind = 'raw', json_extract(metadata, '$.chunk') LIMIT 1",
                         (HEAD_CHARS, src, sid)).fetchone()
        if not row:
            continue
        title, head, meta = row
        prompt = head.split("USER: ", 1)[1] if "USER: " in head else ""
        if json.loads(meta).get("subagent") or delegated(prompt) or delegated(title):
            found.add((src, sid))
    return found


def search(db, query, after=None, sources=(), limit=10, before=None, keep=None, history=False, types=None):
    """sources: names to search in, or () for every source.
    before: an ISO time; the search then sees only older items and ranks recency from that time.
    keep: a test on each hit, applied before the limit so skipped hits don't use up slots.
    history: also find decisions a newer one replaced or the owner rejected, which are left out by default; such a hit
    names what replaced it in hit["replaced_by"].
    types: {source name: type name}, which say which records are kept notes and which go out of date fast; by default
    the user's config (source_types())."""
    terms = fts_terms(query)
    if not terms:
        return []
    match = " OR ".join(terms)
    fused = {}
    summary, raw = _ranked(db, match, after, tuple(sources), CANDIDATES, before)
    alone = _unsummarized(db, raw)
    for ids, weight in ((summary, 1.0), (raw, RAW_WEIGHT)):
        for rank, rid in enumerate(ids, 1):
            # A long note stored only as chunks has no summary to stand for it in the summary list, so its chunk
            # weighs as a summary row at its rank. Raw chunks of a summarized item weigh RAW_WEIGHT.
            share = 1.0 if rid in alone else weight
            fused[rid] = fused.get(rid, 0.0) + share / (RRF_K + rank)
    if not fused:
        return []
    coverage = _coverage(db, terms, list(fused))
    now = datetime.now(timezone.utc)
    if before:
        now = datetime.fromisoformat(before.replace("Z", "+00:00"))
    rows = db.execute(
        f"SELECT id, kind, source, source_id, ts, title, content, metadata FROM docs "
        f"WHERE id IN ({','.join('?' * len(fused))})", list(fused)).fetchall()
    out = decisions.hidden(db, [(r[0], r[2], r[3], r[1], r[6]) for r in rows])
    if not history:
        lost = [r for r in rows if r[0] in out]
        rows = [r for r in rows if r[0] not in out]
        for row in _stand_ins(db, match, lost, fused, after, before):
            rows.append(row)
            coverage.update(_coverage(db, terms, [row[0]]))
    marks = store.superseded(db, {(r[2], r[3]) for r in rows})
    stale = _stale_rows(db, match, rows) if not history and decisions.has_table(db) else {}
    types = source_types() if types is None else types
    helpers = _helpers(db, {(r[2], r[3]) for r in rows})
    best, total = {}, {}
    for row in rows:
        rid, kind, src, sid, ts, title, content, meta = row
        superseded = marks.get((src, sid))
        meta = json.loads(meta)
        age, source_type = _age_days(ts, now), types.get(src, src)
        score = fused[rid] * coverage[rid] * _note_weight(source_type, sid, meta, age)
        score *= 1 + math.exp(-age / (EPHEMERAL_DECAY_DAYS if {src, source_type} & set(EPHEMERAL) else DECAY_DAYS))
        if superseded:
            score *= 0.5
        if rid in stale:
            score *= STALE_WEIGHT
        if (src, sid) in helpers and not (kind == "atom" and meta.get("atom") in HELPER_KEEPS):
            score *= HELPER_WEIGHT
        hit = {"id": rid, "kind": kind, "source": src, "source_id": sid, "ts": ts, "title": title,
               "content": content, "meta": meta, "superseded_by": superseded, "score": score,
               "replaced_by": out.get(rid)}
        total[(src, sid)] = total.get((src, sid), 0.0) + score
        if (src, sid) not in best or score > best[(src, sid)]["score"]:
            best[(src, sid)] = hit
    for key, hit in best.items():
        hit["score"] = total[key]
    fresh = current(db, [h["id"] for h in best.values()])
    skipped = unversioned(db, [h["id"] for h in best.values()])  # an older superhistory's rows, which no reader shows
    # A store from before ingest refused such ids can hold an item whose id or source looks like a secret.
    allowed = (h for h in best.values() if h["id"] not in skipped and (h["id"] in fresh
               or not secret_id(h["source"], h["source_id"], item_names(db, h["source"], h["source_id"]))))
    hits = sorted((h for h in allowed if keep is None or keep(h)), key=lambda h: -h["score"])[:limit]
    for hit in hits:
        if hit["id"] in fresh:
            own = hit["content"]
            snip, marked = db.execute(f"SELECT {SNIPPETS.format(table='docs_fts', column=1)} FROM docs_fts "
                                      "WHERE docs_fts MATCH ? AND rowid = ?", (match, hit["id"])).fetchone()
        else:
            # Older rows are masked whole before any cut, and the same passages are cut from the hit's own stretch of
            # the masked text, so their cards read as a current row's would.
            hit["title"], hit["meta"] = mask(hit["title"]), mask_all(hit["meta"])
            hit["content"], own = _masked_item(db, hit)
            snip, marked = _snippets(hit["title"], own, match)
        hit["snippet"] = re.sub(r"\s+", " ", snip if "[" in snip else own[:SNIPPET_CHARS]).strip()
        hit["excerpt"] = excerpt(marked if MARK in marked else own)
        newer, rejected = stale.get(hit["id"], ([], False))
        note = ""
        if newer:
            hit["replaced_in_item"] = [n.row for n in newer]
            for n in newer:
                text = re.sub(r"\s+", " ", mask(n.text)).strip()
                text = text if len(text) <= STALE_TEXT_CHARS else text[:STALE_TEXT_CHARS - 1].rstrip() + "…"
                note += STALE_NOTE.format(date=n.ts[:10], id=n.row, text=text)
        if rejected:
            hit["rejected_in_item"] = True
            note += REJECTED_NOTE
        if note:
            hit["snippet"], hit["excerpt"] = note + hit["snippet"], note + hit["excerpt"]
        hit["newer"] = _newer(db, match, hit)
    return hits


def _newer(db, match, hit):
    """(id, ts, title) of the newest record of the hit's item that matches the search and was written at least a day
    after the hit, such as a later part of a long session, or None. A decision search leaves out does not count."""
    made = _when(hit["ts"])
    if made is None:
        return None
    rows = db.execute("SELECT d.id, d.kind, d.source, d.source_id, d.ts, d.title, d.content FROM docs_fts JOIN docs d "
                      "ON d.id = docs_fts.rowid WHERE docs_fts MATCH ? AND d.source = ? AND d.source_id = ? "
                      "AND d.kind IN ('doc', 'segment', 'atom')", (match, hit["source"], hit["source_id"])).fetchall()
    rows = sorted((r for r in rows if (_when(r[4]) or made) >= made + timedelta(days=1)),
                  key=lambda r: (-_when(r[4]).timestamp(), r[0]))
    out = decisions.hidden(db, [(r[0], r[2], r[3], r[1], r[6]) for r in rows])
    row = next((r for r in rows if r[0] not in out), None)
    return (row[0], row[4], mask(row[5])) if row else None


def _when(ts):
    """`ts` as an aware time, UTC when it names no zone, or None when it does not read as one."""
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def search_stores(stores, query, after=None, sources=(), limit=10, before=None, keep=None, history=False):
    """search() in each of several stores, merged best first. `stores` is [(label, db), ...]. Row ids are unique only
    within one store, so each hit names its store in hit["store"]."""
    hits = []
    for label, db in stores:
        for hit in search(db, query, after, sources, limit, before, keep, history):
            hits.append({**hit, "store": label})
    return sorted(hits, key=lambda h: -h["score"])[:limit]


def _masked_item(db, hit):
    """The hit's item masked whole, and the hit's own stretch of it. A doc or summary row is its own item. A chunk's
    item is its chunks joined in order, masked together so a secret across two chunks is still found. Its own stretch
    is the chunk masked alone when the text before it, the chunk and the text after it, each masked alone, join up to
    the whole masked item; otherwise a secret crosses its edge and the stretch is the whole item."""
    if hit["kind"] != "raw":
        whole = mask(hit["content"])
        return whole, whole
    rows = db.execute("SELECT content, json_extract(metadata, '$.chunk') FROM docs WHERE source=? AND source_id=? "
                      "AND kind='raw' ORDER BY 2", (hit["source"], hit["source_id"])).fetchall()
    parts = [content for content, _ in rows]
    whole = mask("".join(parts))
    at = [i for i, (_, chunk) in enumerate(rows) if chunk == hit["meta"].get("chunk")]
    if len(at) != 1:
        return whole, whole
    before, own, after = (mask("".join(p)) for p in (parts[:at[0]], parts[at[0]:at[0] + 1], parts[at[0] + 1:]))
    return whole, own if before + own + after == whole else whole


def _snippets(title, text, match):
    """The two passages search() cuts from a current row, cut by FTS5 itself from an index of just this title and
    text, laid out as docs_fts is, so stemming matches, a match in the title alone counts, and the matching words
    cluster as they do in the store."""
    index = sqlite3.connect(":memory:")
    try:
        index.execute(f"CREATE VIRTUAL TABLE t USING fts5(title, content, tokenize='{TOKENIZE}')")
        index.execute("INSERT INTO t(rowid, title, content) VALUES (1, ?, ?)", (title, text))
        row = index.execute(f"SELECT {SNIPPETS.format(table='t', column=1)} FROM t WHERE t MATCH ?", (match,)).fetchone()
        return row if row else ("", text)
    finally:
        index.close()


def excerpt(marked, chars=EXCERPT_CHARS):
    """The text cut to `chars` around its first marked word, so a long run of words before a match cannot push the
    match out."""
    text = re.sub(r"\s+", " ", marked).strip()
    start = max(0, min(text.find(MARK) - chars // 3, len(text) - chars))
    text = text[start:start + chars].replace(MARK, "").replace(END, "")
    return ("… " + text.lstrip("… ") if start else text).strip()


def pointer(meta, machine=None):
    """Where to open the original. A file path on another computer names that computer."""
    where = meta.get("url") or meta.get("path") or ""
    host = meta.get("host")
    if host and host != machine and meta.get("path") and where == meta.get("path"):
        where = f"{host}:{where}"
    return mask(where)
