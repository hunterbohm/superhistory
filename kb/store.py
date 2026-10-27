"""The database: one SQLite file holding every item, its full-text index, and the ingest state.

An item is one `doc` row, or `raw` chunks when its text is long. The summarizer adds `segment` and `atom` rows that
point back at a long item. `ingest_state` holds each source's watermark and the health of every source on every host.
"""
import contextlib
import fcntl
import hashlib
import json
import os
import pathlib
import sqlite3
from datetime import datetime, timezone

from .mask import clean, mask, mask_all

CHUNK = 4000
ITEM_KINDS = ("doc", "raw")
SUMMARY_KINDS = ("segment", "atom")
CHUNK_KEYS = ("chunk", "chunks")
# The metadata a summary row copies from its item, so a search hit on a summary points at the original.
POINTER_KEYS = ("url", "path", "host")
STATE = "_kb"

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  superseded_by INTEGER
);
CREATE INDEX IF NOT EXISTS docs_item ON docs(source, source_id, kind);
CREATE INDEX IF NOT EXISTS docs_ts ON docs(ts);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
  title, content, content='docs', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
  INSERT INTO docs_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
  INSERT INTO docs_fts(docs_fts, rowid, title, content) VALUES ('delete', old.id, old.title, old.content);
END;
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE OF title, content ON docs BEGIN
  INSERT INTO docs_fts(docs_fts, rowid, title, content) VALUES ('delete', old.id, old.title, old.content);
  INSERT INTO docs_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;
CREATE TABLE IF NOT EXISTS ingest_state(
  source TEXT NOT NULL, key TEXT NOT NULL, watermark TEXT, PRIMARY KEY(source, key));
"""


def connect(path, readonly=False):
    path = os.fspath(path)
    if readonly:
        db = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        db = sqlite3.connect(path)
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
    db.execute("PRAGMA busy_timeout=30000")
    return db


@contextlib.contextmanager
def exclusive(lock_path):
    """Yields True while this process holds the database's lock, or False when another run holds it."""
    os.makedirs(os.path.dirname(os.fspath(lock_path)), exist_ok=True)
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def chunk(text, size=CHUNK):
    if len(text) <= size:
        return [text]
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:size])
            line = line[size:]
        if len(cur) + len(line) > size:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur:
        chunks.append(cur)
    return chunks


def _whole(rows):
    """`rows` with any raw chunks masked as one text and cut again where it changed: a store that cut an item before
    masking it, as an older kb did and another store may, can split a secret in two that neither chunk shows."""
    raw = sorted((r for r in rows if r["kind"] == "raw"), key=lambda r: r["metadata"].get("chunk", 0))
    text = "".join(r["content"] for r in raw)
    masked = mask(text)
    if masked == text:
        return rows
    parts = chunk(masked)
    meta = {k: v for k, v in raw[0]["metadata"].items() if k not in CHUNK_KEYS}
    return [r for r in rows if r["kind"] != "raw"] + [
        {**raw[0], "content": part, "metadata": {**meta, "chunk": i, "chunks": len(parts)}}
        for i, part in enumerate(parts)]


def replace_rows(db, source, source_id, rows, kinds=ITEM_KINDS):
    """Swap one item's rows of the given kinds for `rows` (dicts: kind, ts, title, content, metadata)."""
    rows = _whole(rows)
    marks = ",".join("?" * len(kinds))
    db.execute(f"DELETE FROM docs WHERE source=? AND source_id=? AND kind IN ({marks})",
               (source, source_id, *kinds))
    # Masked here too, since Sync and Cloud write rows another store sent; for rows store_item made it changes nothing.
    db.executemany(
        "INSERT INTO docs(kind, source, source_id, ts, title, content, metadata) VALUES (?,?,?,?,?,?,?)",
        [(r["kind"], source, source_id, mask(str(r["ts"])), mask(r["title"]), mask(r["content"]),
          json.dumps(mask_all(r["metadata"]))) for r in rows])


def delete_item(db, source, source_id):
    """Returns the number of rows removed."""
    return db.execute("DELETE FROM docs WHERE source=? AND source_id=?", (source, source_id)).rowcount


def store_item(db, source, source_id, ts, title, content, meta):
    """Store one item as a `doc` row, or as `raw` chunks when long. Returns False when nothing changed. The same text
    under new metadata, such as a moved file, keeps its rows and summaries and takes the new metadata."""
    ts, title, content = mask(str(ts)), mask(clean(title or ""))[:300], mask(clean(content or ""))
    digest = hashlib.sha1(f"{title}\0{content}".encode()).hexdigest()[:16]
    meta = {**mask_all(meta), "hash": digest}
    old = db.execute("SELECT metadata FROM docs WHERE source=? AND source_id=? AND kind IN ('doc','raw') LIMIT 1",
                     (source, source_id)).fetchone()
    if old:
        stored = {k: v for k, v in json.loads(old[0]).items() if k not in CHUNK_KEYS}
        if stored == meta:
            return False
        if stored.get("hash") == digest:
            _repoint(db, source, source_id, meta)
            return True
    parts = chunk(content)
    if len(parts) == 1:
        rows = [{"kind": "doc", "ts": ts, "title": title, "content": content, "metadata": meta}]
        kinds = ITEM_KINDS + SUMMARY_KINDS
    else:
        rows = [{"kind": "raw", "ts": ts, "title": title, "content": part,
                 "metadata": {**meta, "chunk": i, "chunks": len(parts)}} for i, part in enumerate(parts)]
        kinds = ITEM_KINDS
    replace_rows(db, source, source_id, rows, kinds)
    return True


def _repoint(db, source, source_id, meta):
    """Give an item's rows new metadata without touching their text, so its summaries stay current."""
    for rid, kind, old in db.execute("SELECT id, kind, metadata FROM docs WHERE source=? AND source_id=?",
                                     (source, source_id)).fetchall():
        old = json.loads(old)
        if kind in ITEM_KINDS:
            new = {**meta, **{k: old[k] for k in CHUNK_KEYS if k in old}}
        else:
            new = {k: v for k, v in old.items() if k not in POINTER_KEYS}
            new.update((k, meta[k]) for k in POINTER_KEYS if k in meta)
        db.execute("UPDATE docs SET metadata=? WHERE id=?", (json.dumps(new), rid))


def item_rows(db, rid):
    """The doc, or the raw chunks in order, of the item that row `rid` belongs to."""
    return db.execute(
        "SELECT i.id, i.source, i.ts, i.title, i.content, i.metadata FROM docs r JOIN docs i "
        "ON i.source = r.source AND i.source_id = r.source_id AND i.kind IN ('doc', 'raw') "
        "WHERE r.id = ? ORDER BY json_extract(i.metadata, '$.chunk')", (rid,)).fetchall()


def get_watermark(db, source, key):
    row = db.execute("SELECT watermark FROM ingest_state WHERE source=? AND key=?", (source, key)).fetchone()
    return row[0] if row else None


def set_watermark(db, source, key, value):
    db.execute("INSERT INTO ingest_state(source, key, watermark) VALUES (?,?,?) "
               "ON CONFLICT(source, key) DO UPDATE SET watermark=excluded.watermark", (source, key, str(value)))


def record_ok(db, unit):
    set_watermark(db, STATE, f"ok:{unit}", now_iso())
    db.execute("DELETE FROM ingest_state WHERE source=? AND key=?", (STATE, f"fail:{unit}"))


def record_failure(db, unit, exc):
    db.rollback()
    line = mask(str(exc).splitlines()[0])[:200] if str(exc) else type(exc).__name__
    set_watermark(db, STATE, f"fail:{unit}", f"{now_iso()} {line}")
    db.commit()


def record_away(db, host):
    db.execute("INSERT OR IGNORE INTO ingest_state(source, key, watermark) VALUES (?, ?, ?)",
               (STATE, f"away:{host}", now_iso()))


def clear_away(db, host):
    db.execute("DELETE FROM ingest_state WHERE source=? AND key=?", (STATE, f"away:{host}"))


def _state(db, prefix):
    return db.execute("SELECT substr(key, ?), watermark FROM ingest_state WHERE source=? AND key GLOB ? ORDER BY key",
                      (len(prefix) + 1, STATE, prefix + "*")).fetchall()


def succeeded(db):
    return _state(db, "ok:")


def failing(db):
    """(unit, "<when> <error line>") for units whose last run failed."""
    return _state(db, "fail:")


def away(db):
    """(host, time of the first miss) for hosts ssh cannot reach."""
    return _state(db, "away:")


def last_ingest(db):
    return get_watermark(db, STATE, "last_ingest")


def remask(db):
    """Reapply mask() to every stored row's title, text and metadata, a long item's chunks as one text, then rebuild
    the file so the old values leave no bytes behind."""
    db.execute("PRAGMA secure_delete=ON")
    changed, last = 0, 0
    for source, source_id in db.execute("SELECT DISTINCT source, source_id FROM docs WHERE kind='raw'").fetchall():
        rows = [{"kind": "raw", "ts": ts, "title": title, "content": content, "metadata": json.loads(meta)}
                for ts, title, content, meta in db.execute(
                    "SELECT ts, title, content, metadata FROM docs WHERE source=? AND source_id=? AND kind='raw'",
                    (source, source_id))]
        if _whole(rows) is not rows:
            replace_rows(db, source, source_id, rows, ("raw",))
            db.commit()
            changed += len(rows)
    while True:
        batch = db.execute("SELECT id, title, content, metadata FROM docs WHERE id > ? ORDER BY id LIMIT 2000",
                           (last,)).fetchall()
        if not batch:
            break
        updates = []
        for rid, title, content, meta in batch:
            new_title, new_content, new_meta = mask(title), mask(content), json.dumps(mask_all(json.loads(meta)))
            if (new_title, new_content, json.loads(new_meta)) != (title, content, json.loads(meta)):
                updates.append((new_title, new_content, new_meta, rid))
        db.executemany("UPDATE docs SET title=?, content=?, metadata=? WHERE id=?", updates)
        db.commit()
        changed += len(updates)
        last = batch[-1][0]
    # FTS keeps deleted terms in old segments until they merge; optimize merges them away.
    db.execute("INSERT INTO docs_fts(docs_fts) VALUES('optimize')")
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.execute("VACUUM")
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return changed
