import json
import math
import re
import sqlite3
from datetime import datetime, timezone

from kb.mask import mask, mask_all
from kb.store import SUMMARY_KINDS, TOKENIZE, current, secret_id

STOPWORDS = set("""a an and are as at be been but by can could did do does doing for from had has have
how i if in into is it its just me my no not of on or our should so than that the their them then there
these they this those to was we were what when where which who why will with would you your about any
all also get got make made say said tell""".split())
RRF_K = 60
RAW_WEIGHT = 0.5
CANDIDATES = 60
# The longest passage FTS5's snippet() gives: the stretch of a record where its matching words cluster.
EXCERPT_TOKENS = 64
EXCERPT_CHARS = 500
SNIPPET_CHARS = 220
MARK, END = "\x02", "\x03"


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
    sql = (f"SELECT d.id, d.source, d.source_id, bm25(docs_fts, 4.0, 1.0) AS score FROM docs_fts "
           f"JOIN docs d ON d.id = docs_fts.rowid WHERE docs_fts MATCH ? AND d.kind IN ({','.join('?' * len(kinds))})")
    args = [match, *kinds]
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
           f"(PARTITION BY source, source_id ORDER BY score) AS nth FROM m) WHERE nth = 1 ORDER BY score LIMIT ?")
    return [rid for (rid,) in db.execute(sql, (*args, n))]


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


def search(db, query, after=None, sources=(), limit=10, before=None, keep=None):
    """sources: names to search in, or () for every source.
    before: an ISO time; the search then sees only older items and ranks recency from that time.
    keep: a test on each hit, applied before the limit so skipped hits don't use up slots."""
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
        f"SELECT id, kind, source, source_id, ts, title, content, metadata, superseded_by FROM docs "
        f"WHERE id IN ({','.join('?' * len(fused))})", list(fused)).fetchall()
    best, total = {}, {}
    for row in rows:
        rid, kind, src, sid, ts, title, content, meta, superseded = row
        score = fused[rid] * coverage[rid] * (1 + math.exp(-_age_days(ts, now) / 180))
        if superseded:
            score *= 0.5
        hit = {"id": rid, "kind": kind, "source": src, "source_id": sid, "ts": ts, "title": title,
               "content": content, "meta": json.loads(meta), "superseded_by": superseded, "score": score}
        total[(src, sid)] = total.get((src, sid), 0.0) + score
        if (src, sid) not in best or score > best[(src, sid)]["score"]:
            best[(src, sid)] = hit
    for key, hit in best.items():
        hit["score"] = total[key]
    fresh = current(db, [h["id"] for h in best.values()])
    # A store from before ingest refused such ids can hold an item whose id or source looks like a secret.
    allowed = (h for h in best.values()
               if h["id"] in fresh or not secret_id(h["source"], h["source_id"], h["meta"].get("host")))
    hits = sorted((h for h in allowed if keep is None or keep(h)), key=lambda h: -h["score"])[:limit]
    for hit in hits:
        if hit["id"] in fresh:
            snip, marked = db.execute(
                f"SELECT snippet(docs_fts, 1, '[', ']', ' … ', 24), snippet(docs_fts, 1, char(2), char(3), ' … ', "
                f"{EXCERPT_TOKENS}) FROM docs_fts WHERE docs_fts MATCH ? AND rowid = ?", (match, hit["id"])).fetchone()
            hit["snippet"] = re.sub(r"\s+", " ", snip if "[" in snip else hit["content"][:SNIPPET_CHARS]).strip()
            hit["excerpt"] = excerpt(marked if MARK in marked else hit["content"])
        else:
            # Older rows are masked whole before any cut, and the match is found again in the masked text.
            hit["title"], hit["content"], hit["meta"] = mask(hit["title"]), _masked_item(db, hit), mask_all(hit["meta"])
            marked = _marked(hit["content"], match)
            hit["snippet"] = excerpt(marked.replace(MARK, MARK + "[").replace(END, "]" + END), SNIPPET_CHARS)
            hit["excerpt"] = excerpt(marked)
    return hits


def _masked_item(db, hit):
    """The hit's text masked whole: a doc or summary row by itself, a chunk as its item's chunks joined in order."""
    if hit["kind"] != "raw":
        return mask(hit["content"])
    return mask("".join(r[0] for r in db.execute(
        "SELECT content FROM docs WHERE source=? AND source_id=? AND kind='raw' "
        "ORDER BY json_extract(metadata, '$.chunk')", (hit["source"], hit["source_id"]))))


def _marked(text, match):
    """`text` with the words the FTS query `match` finds marked as snippet() marks them, found by FTS5 itself in an
    index of just this text, so stemming matches as it does in the store."""
    index = sqlite3.connect(":memory:")
    try:
        index.execute(f"CREATE VIRTUAL TABLE t USING fts5(content, tokenize='{TOKENIZE}')")
        index.execute("INSERT INTO t(rowid, content) VALUES (1, ?)", (text,))
        row = index.execute("SELECT highlight(t, 0, char(2), char(3)) FROM t WHERE t MATCH ?", (match,)).fetchone()
        return row[0] if row else text
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
