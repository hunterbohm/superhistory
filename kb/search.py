import json
import math
import re
import sqlite3
from datetime import datetime, timezone

from kb import decisions, store
from kb.mask import mask, mask_all
from kb.store import SUMMARY_KINDS, TOKENIZE, current, item_names, secret_id, unversioned

STOPWORDS = set("""a an and are as at be been but by can could did do does doing for from had has have
how i if in into is it its just me my no not of on or our should so than that the their them then there
these they this those to was we were what when where which who why will with would you your about any
all also get got make made say said tell""".split())
RRF_K = 60
RAW_WEIGHT = 0.5
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
STALE_NOTE = "[A decision here was replaced later; see {}] "


def fts_terms(text):
    """Each word or "quoted phrase" becomes a quoted FTS phrase."""
    terms = []
    for token in re.findall(r'"[^"]+"|\S+', text):
        token = token.strip('"').replace('"', "")
        words = re.findall(r"\w+", token)
        if not words or (len(words) == 1 and words[0].lower() in STOPWORDS):
            continue
        phrase = f'"{token}"'
        if phrase not in terms:
            terms.append(phrase)
    return terms


def fts_query(text):
    return " OR ".join(fts_terms(text))


def _ranked(db, match, kinds, after, sources, n, before=None):
    """Best-first row ids of these kinds, one per item, so one long session can't fill the list."""
    best = db.execute("SELECT rowid, bm25(docs_fts, 4.0, 1.0) AS score FROM docs_fts WHERE docs_fts MATCH ? "
                      "ORDER BY score, rowid LIMIT ?", (match, TOP_ROWS + 1)).fetchall()
    # Exact when the list fills from the best rows: an item with no row among them scores below every item with one.
    # A tie across the cut would make "the best rows" ambiguous, so it counts as not filling.
    if len(best) <= TOP_ROWS or best[TOP_ROWS - 1][1] != best[TOP_ROWS][1]:
        # json_extract, not ->>, which needs SQLite 3.38 (Ubuntu 22.04's Python has 3.37).
        rows = "SELECT json_extract(value, '$[0]') AS id, json_extract(value, '$[1]') AS score FROM json_each(?)"
        ids = _grouped(db, rows, json.dumps(best[:TOP_ROWS]), kinds, after, sources, n, before)
        if len(best) <= TOP_ROWS or len(ids) >= n:
            return ids
    rows = "SELECT rowid AS id, bm25(docs_fts, 4.0, 1.0) AS score FROM docs_fts WHERE docs_fts MATCH ?"
    return _grouped(db, rows, match, kinds, after, sources, n, before)


def _grouped(db, rows, rows_arg, kinds, after, sources, n, before):
    """The best row of each item among `rows`, a query of (id, score) taking one argument, best first, ties by row id
    so every run orders them the same."""
    # CROSS JOIN keeps the rows first; otherwise SQLite may walk every doc and run the full-text match for each.
    sql = (f"SELECT d.id, d.source, d.source_id, r.score FROM ({rows}) r CROSS JOIN docs d ON d.id = r.id "
           f"WHERE d.kind IN ({','.join('?' * len(kinds))})")
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
    sql = (f"WITH m AS MATERIALIZED ({sql}) SELECT id FROM (SELECT id, score, row_number() OVER "
           f"(PARTITION BY source, source_id ORDER BY score, id) AS nth FROM m) WHERE nth = 1 ORDER BY score, id "
           f"LIMIT ?")
    return [rid for (rid,) in db.execute(sql, (*args, n))]


def _stale_items(db, match, items):
    """{item: the ids of the newer decisions} for those of `items` holding a decision that matches the search and
    that search leaves out, so their other rows, such as the session text that still states the old choice, carry a
    note naming what replaced it. Checked per item, whichever of its rows the search picked."""
    found = {}
    for src, sid in items:
        if not db.execute("SELECT 1 FROM decisions WHERE source=? AND source_id=? AND (new_key IS NOT NULL "
                          "OR answer='rejected') LIMIT 1", (src, sid)).fetchone():
            continue
        atoms = db.execute("SELECT id, source, source_id, kind, content FROM docs WHERE source=? AND source_id=? "
                           "AND kind='atom'", (src, sid)).fetchall()
        matching = {i for (i,) in db.execute(
            f"SELECT rowid FROM docs_fts WHERE docs_fts MATCH ? AND rowid IN ({','.join('?' * len(atoms))})",
            (match, *[a[0] for a in atoms]))} if atoms else set()
        rows = []
        for by in decisions.hidden(db, [a for a in atoms if a[0] in matching]).values():
            row = decisions.describe(db, by)[0] if by != "rejected" else None
            if row and row not in rows:
                rows.append(row)
        if rows:
            found[(src, sid)] = rows
    return found


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
    return {rid: n / len(terms) for rid, n in hits.items()}


def _age_days(ts, now):
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return 3650.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return abs((now - t).total_seconds()) / 86400


def search(db, query, after=None, sources=(), limit=10, before=None, keep=None, history=False):
    """sources: names to search in, or () for every source.
    before: an ISO time; the search then sees only older items and ranks recency from that time.
    keep: a test on each hit, applied before the limit so skipped hits don't use up slots.
    history: also find decisions a newer one replaced or the owner rejected, which are left out by default; such a hit
    names what replaced it in hit["replaced_by"]."""
    terms = fts_terms(query)
    if not terms:
        return []
    match = " OR ".join(terms)
    fused = {}
    for kinds, weight in ((("doc",) + SUMMARY_KINDS, 1.0), (("raw",), RAW_WEIGHT)):
        for rank, rid in enumerate(_ranked(db, match, kinds, after, tuple(sources), CANDIDATES, before), 1):
            fused[rid] = fused.get(rid, 0.0) + weight / (RRF_K + rank)
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
    best, total = {}, {}
    for row in rows:
        rid, kind, src, sid, ts, title, content, meta = row
        superseded = marks.get((src, sid))
        score = fused[rid] * coverage[rid] * (1 + math.exp(-_age_days(ts, now) / 180))
        if superseded:
            score *= 0.5
        hit = {"id": rid, "kind": kind, "source": src, "source_id": sid, "ts": ts, "title": title,
               "content": content, "meta": json.loads(meta), "superseded_by": superseded, "score": score,
               "replaced_by": out.get(rid)}
        total[(src, sid)] = total.get((src, sid), 0.0) + score
        if (src, sid) not in best or score > best[(src, sid)]["score"]:
            best[(src, sid)] = hit
    for key, hit in best.items():
        hit["score"] = total[key]
    fresh = current(db, [h["id"] for h in best.values()])
    skipped = unversioned(db, [h["id"] for h in best.values()])  # an older kb's rows, which no reader shows
    # A store from before ingest refused such ids can hold an item whose id or source looks like a secret.
    allowed = (h for h in best.values() if h["id"] not in skipped and (h["id"] in fresh
               or not secret_id(h["source"], h["source_id"], item_names(db, h["source"], h["source_id"]))))
    hits = sorted((h for h in allowed if keep is None or keep(h)), key=lambda h: -h["score"])[:limit]
    stale = {}
    if not history and decisions.has_table(db):
        stale = _stale_items(db, match, {(h["source"], h["source_id"]) for h in hits})
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
        newer = stale.get((hit["source"], hit["source_id"]))
        if newer:
            hit["replaced_in_item"] = newer
            note = STALE_NOTE.format(", ".join(f"#{n}" for n in newer))
            hit["snippet"], hit["excerpt"] = note + hit["snippet"], note + hit["excerpt"]
    return hits


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
