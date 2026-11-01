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
import time
from datetime import datetime, timezone

from .config import NAME_RE
from .mask import ID_NAMES, KEY_WORD, VERSION, clean, holds_secret, mask, mask_all, masked_log, secret_in_id

CHUNK = 4000
ITEM_KINDS = ("doc", "raw")
SUMMARY_KINDS = ("segment", "atom")
CHUNK_KEYS = ("chunk", "chunks")
# The metadata a summary row copies from its item, so a search hit on a summary points at the original.
POINTER_KEYS = ("url", "path", "host")
STATE = "_kb"
# How long a connection waits for another program's lock on the store before it gives up.
BUSY_MS = 30000
TOKENIZE = "porter unicode61"

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
  superseded_by INTEGER,
  masked TEXT
);
CREATE INDEX IF NOT EXISTS docs_item ON docs(source, source_id, kind);
CREATE INDEX IF NOT EXISTS docs_ts ON docs(ts);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
  title, content, content='docs', content_rowid='id', tokenize='""" + TOKENIZE + """');
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
-- A change to a row's time, title, text or metadata clears its mask version. kb's own writers stamp it again once
-- they have masked the row; an older kb, which does not, leaves the row to be masked again.
CREATE TRIGGER IF NOT EXISTS docs_unmask AFTER UPDATE OF ts, title, content, metadata ON docs BEGIN
  UPDATE docs SET masked = NULL WHERE id = new.id;
END;
CREATE TABLE IF NOT EXISTS ingest_state(
  source TEXT NOT NULL, key TEXT NOT NULL, watermark TEXT, PRIMARY KEY(source, key));
"""


class NeedsRebuild(Exception):
    """A store holds rows an older kb wrote, which kb neither reads nor masks again in place."""


REBUILD = ("{path} holds rows an older kb wrote, before each row recorded how it was masked. kb no longer reads such rows "
           "or masks them again in place, because an older kb's masking can leave a secret that no pattern finds. Run "
           "`kb rebuild` to read your sources into a fresh knowledge base; it takes this one's place once it checks out. "
           "If a source no longer has some of this one's items, such as sessions an agent has since deleted, it stops "
           "and says how many.")


def peek(path):
    """A connection that reads the store at `path` and leaves its files as they are. Closing a writable connection
    moves the rows in the WAL file into the store, so one is opened read-only when the WAL files are there; without
    them a read-only one would leave new ones behind, and a writable one has nothing to move."""
    path = os.path.realpath(path)  # SQLite keeps a linked store's WAL files beside its target
    if os.path.exists(path + "-wal") and os.path.exists(path + "-shm"):
        db = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True)
    else:
        db = sqlite3.connect(path)
    db.execute(f"PRAGMA busy_timeout={BUSY_MS}")
    return db


def connect(path, readonly=False):
    """The store at `path`, made if it is new. Raises NeedsRebuild for a store with rows an older kb wrote, before
    writing anything to it, so an older kb still opens it as it was."""
    path = os.fspath(path)
    if os.path.exists(path):
        with contextlib.closing(peek(path)) as db:
            older = has_unversioned(db)
        if older:
            raise NeedsRebuild(REBUILD.format(path=path))
    if readonly:
        db = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        db = sqlite3.connect(path)
    db.execute(f"PRAGMA busy_timeout={BUSY_MS}")
    if has_unversioned(db):  # an older kb wrote since the look above
        db.close()
        raise NeedsRebuild(REBUILD.format(path=path))
    if not readonly:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
        if not _versioned(db):
            db.execute("ALTER TABLE docs ADD COLUMN masked TEXT")  # an empty store from before rows recorded it
        db.execute("CREATE INDEX IF NOT EXISTS docs_masked ON docs(masked)")
    return db


def has_unversioned(db):
    """Whether the store holds a row that records no mask version: one an older kb wrote or changed."""
    columns = {column[1] for column in db.execute("PRAGMA table_info(docs)")}
    if not columns:
        return False  # a new store
    if "masked" not in columns:
        return db.execute("SELECT 1 FROM docs LIMIT 1").fetchone() is not None
    return db.execute("SELECT 1 FROM docs WHERE masked IS NULL LIMIT 1").fetchone() is not None


def _versioned(db):
    return any(column[1] == "masked" for column in db.execute("PRAGMA table_info(docs)"))


def unversioned(db, ids):
    """The ids among `ids` that an older kb wrote or changed, which record no mask version, and for a chunk the ids
    of an item any of whose chunks records none. A connection opened before an older writer changed a row can meet
    one; no reader shows it and remask leaves it as it is."""
    if not ids:
        return set()
    if not _versioned(db):
        return set(ids)
    return {rid for (rid,) in db.execute(
        f"SELECT r.id FROM docs r WHERE r.id IN ({','.join('?' * len(ids))}) AND (r.masked IS NULL OR (r.kind = 'raw' "
        f"AND EXISTS (SELECT 1 FROM docs c WHERE c.source = r.source AND c.source_id = r.source_id AND c.kind = 'raw' "
        f"AND c.masked IS NULL)))", ids)}


def current(db, ids):
    """The ids among `ids` whose text was masked whole with today's mask(): the row records mask.VERSION, and for a
    chunk so does every chunk of its item, since a secret could run from one chunk into the next. Only these may be
    cut as they are stored."""
    if not ids or not _versioned(db):
        return set()
    return {rid for (rid,) in db.execute(
        f"SELECT r.id FROM docs r WHERE r.id IN ({','.join('?' * len(ids))}) AND r.masked = ? AND (r.kind != 'raw' "
        f"OR NOT EXISTS (SELECT 1 FROM docs c WHERE c.source = r.source AND c.source_id = r.source_id "
        f"AND c.kind = 'raw' AND c.masked IS NOT ?))", (*ids, VERSION, VERSION))}


def old_rows(db):
    """How many rows were masked with another version of mask(), which remask brings up to date. Rows that record no
    version are not among them: an older kb wrote them, and the store is rebuilt instead."""
    if not _versioned(db):
        return 0
    return db.execute("SELECT count(*) FROM docs WHERE masked IS NOT NULL AND masked != ?", (VERSION,)).fetchone()[0]


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


# Fields of an item's metadata whose values its stored id starts with, each joined by a colon: kb's host, then its
# reader's own (mask.ID_NAMES).
ID_FIELDS = ("host",) + ID_NAMES


def secret_id(source, source_id, names=()):
    """Whether kb refuses an item under this source and stored id. kb stores "<host>:<reader's id>" and records the
    host in the item's metadata. `names` are the values the item's rows agree on for ID_FIELDS, the host first. Given
    that host, with the name config allows a host and the id starting with it, the id is checked as the host and the
    reader's id apart, since joined by kb's colon a host such as "tokenbox" would read as the name of a secret; the
    reader's id is read with the reader's names (mask.secret_in_id). Any other id is checked whole, so
    "password:...", "token2:..." or a link with a password in it is refused."""
    host = names[0] if names else None
    if (host and source_id.startswith(f"{host}:") and NAME_RE.match(host) and not KEY_WORD.fullmatch(host)
            and not source_id.startswith("//", len(host) + 1)):
        return holds_secret(source) or holds_secret(host) or secret_in_id(source_id[len(host) + 1:], names[1:])
    return holds_secret(source) or secret_in_id(source_id)


def _agreed(values):
    """The one value in `values`, or None when they differ."""
    return values.pop() if len(values) == 1 else None


def item_names(db, source, source_id):
    """The values the item's own stored rows record for ID_FIELDS, each None where they differ or one records none.
    Summary rows are left out: an older kb's copy only some fields."""
    fields = ", ".join(f"json_extract(metadata, '$.{field}')" for field in ID_FIELDS)
    rows = db.execute(f"SELECT {fields} FROM docs WHERE source=? AND source_id=? AND kind IN ('doc', 'raw')",
                      (source, source_id)).fetchall()
    return tuple(_agreed({row[i] for row in rows}) for i in range(len(ID_FIELDS)))


def _names(rows):
    """The values the rows' metadata agrees on for ID_FIELDS, as item_names() reads them from stored rows."""
    meta = [r["metadata"] if isinstance(r["metadata"], dict) else {} for r in rows]
    return tuple(_agreed({m.get(field) for m in meta}) for field in ID_FIELDS)


def replace_rows(db, source, source_id, rows, kinds=ITEM_KINDS):
    """Swap one item's rows of the given kinds for `rows` (dicts: kind, ts, title, content, metadata), masked with
    today's mask() and marked with its version. Raises ValueError for a source or item id that looks like it holds a
    secret, which ingest does not store either: a masked id could stand for two items."""
    own = [r for r in rows if r["kind"] in ITEM_KINDS]
    if rows and secret_id(source, source_id, _names(own) if own else item_names(db, source, source_id)):
        raise ValueError("a source or item id that looks like it holds a secret is not stored; rename it")
    if "raw" not in kinds and any(r["kind"] == "raw" for r in rows):
        # Chunks added one write at a time would be masked apart, and a secret could run from one into the next.
        raise ValueError("an item's chunks are replaced all at once, with the raw kind")
    rows = _whole(rows)
    marks = ",".join("?" * len(kinds))
    if db.execute(f"SELECT 1 FROM docs WHERE source=? AND source_id=? AND kind IN ({marks}) AND masked IS NOT ? LIMIT 1",
                  (source, source_id, *kinds, VERSION)).fetchone():
        # Rows of another mask's version may hold what today's would hide; their words stay in the index's data and
        # the file's free pages until a rebuild.
        set_watermark(db, STATE, "remask", "rebuild")
    db.execute(f"DELETE FROM docs WHERE source=? AND source_id=? AND kind IN ({marks})",
               (source, source_id, *kinds))
    # Masked here too, since Sync and Cloud write rows another store sent; for rows store_item made it changes nothing.
    db.executemany(
        "INSERT INTO docs(kind, source, source_id, ts, title, content, metadata, masked) VALUES (?,?,?,?,?,?,?,?)",
        [(r["kind"], source, source_id, mask(str(r["ts"])), mask(r["title"]), mask(r["content"]),
          json.dumps(mask_all(r["metadata"])), VERSION) for r in rows])


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
    """Give an item's rows new metadata, already masked, without touching their text, so its summaries stay current.
    Each row keeps the mask version it had, which the store's trigger clears on the update."""
    for rid, kind, old, version in db.execute(
            "SELECT id, kind, metadata, masked FROM docs WHERE source=? AND source_id=?", (source, source_id)).fetchall():
        old = json.loads(old)
        if kind in ITEM_KINDS:
            new = {**meta, **{k: old[k] for k in CHUNK_KEYS if k in old}}
        else:
            new = {k: v for k, v in old.items() if k not in POINTER_KEYS}
            new.update((k, meta[k]) for k in POINTER_KEYS if k in meta)
        db.execute("UPDATE docs SET metadata=? WHERE id=?", (json.dumps(new), rid))
        db.execute("UPDATE docs SET masked=? WHERE id=?", (version, rid))


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


def clean_state(db):
    """Drop what ingest_state holds from before today's mask(), once for each version: every failure line, since an
    older kb cut one before masking it and the next failing run writes it again, and every row whose source, key or
    value holds a secret, such as a position an older kb read from an id it now refuses. A source whose position goes
    reads from the start again."""
    if get_watermark(db, STATE, "state_masked") == VERSION:
        return
    db.execute("DELETE FROM ingest_state WHERE source=? AND key GLOB 'fail:*'", (STATE,))
    db.executemany("DELETE FROM ingest_state WHERE source=? AND key=?", [
        (source, key) for source, key, value in db.execute("SELECT source, key, watermark FROM ingest_state")
        if holds_secret(source) or holds_secret(key) or holds_secret(str(value))])
    set_watermark(db, STATE, "state_masked", VERSION)


def record_failure(db, unit, exc):
    db.rollback()
    clean_state(db)
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
    """(unit, "<when> <error line>") for units whose last run failed. Until clean_state() has run under today's
    mask(), a line may be an older kb's, which can hold a piece of a secret too short to mask, so only its time shows."""
    if get_watermark(db, STATE, "state_masked") != VERSION:
        return [(unit, line.split(" ", 1)[0] + " (recorded before kb was updated; the next run records the error)")
                for unit, line in _state(db, "fail:")]
    return [(unit, mask(line)) for unit, line in _state(db, "fail:")]


def away(db):
    """(host, time of the first miss) for hosts ssh cannot reach."""
    return _state(db, "away:")


def last_ingest(db):
    return get_watermark(db, STATE, "last_ingest")


def remask(db, seconds=None, log=None):
    """Mask every row of another version again, a long item's chunks as one text, and record today's version. An item
    with a row that records no version is left as it is: an older kb wrote it, and the store is rebuilt instead. An
    item whose source or id looks like a secret is deleted, as ingest would not store it, and named masked on
    `log`. With `seconds`, stops at the first item past that time and leaves the rest old. Returns (rows changed,
    rows left). Once none are left, rebuilds the file so the old values leave no bytes behind."""
    db.execute("PRAGMA secure_delete=ON")
    log = masked_log(log) if log else None
    started, changed = time.monotonic(), 0
    items = db.execute(
        "SELECT DISTINCT r.source, r.source_id FROM docs r WHERE r.masked IS NOT NULL AND r.masked != ? AND NOT EXISTS "
        "(SELECT 1 FROM docs n WHERE n.source = r.source AND n.source_id = r.source_id AND n.masked IS NULL)",
        (VERSION,)).fetchall()
    for n, (source, source_id) in enumerate(items, 1):
        if secret_id(source, source_id, item_names(db, source, source_id)):
            changed += delete_item(db, source, source_id)
            if log:
                log.warning("remask: deleted %s %s, whose name looks like it holds a secret; rename it to index it",
                            source, source_id)
        else:
            changed += _remask_item(db, source, source_id)
        if n % 50 == 0 or n == len(items):
            if changed:
                set_watermark(db, STATE, "remask", "rebuild")
            db.commit()
            if seconds is not None and time.monotonic() - started > seconds:
                break
    left = old_rows(db)
    if not left and (seconds is None or rebuild_owed(db)):
        _rebuild(db)
    return changed, left


def rebuild_owed(db):
    """Whether remask changed rows whose old values may still sit in the file, until _rebuild() has run."""
    return get_watermark(db, STATE, "remask") is not None


def _rebuild(db):
    """Merge FTS's old segments, which keep deleted terms, and rewrite the file, whose free pages keep old bytes.
    The mark that a rebuild is owed goes only once both are done, so an interrupted one runs again."""
    db.execute("INSERT INTO docs_fts(docs_fts) VALUES('optimize')")
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.execute("VACUUM")
    db.execute("DELETE FROM ingest_state WHERE source=? AND key='remask'", (STATE,))
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _remask_item(db, source, source_id):
    """remask() for one item: its chunks as one text if any is old, and each other old row by itself. Returns the
    number of rows whose text changed."""
    changed = 0
    raw = [{"kind": "raw", "ts": ts, "title": title, "content": content, "metadata": json.loads(meta), "masked": v}
           for ts, title, content, meta, v in db.execute(
               "SELECT ts, title, content, metadata, masked FROM docs WHERE source=? AND source_id=? AND kind='raw'",
               (source, source_id))]
    if any(r["masked"] != VERSION for r in raw):
        whole = _whole(raw)
        new = [{**r, "ts": mask(str(r["ts"])), "title": mask(r["title"]), "metadata": mask_all(r["metadata"])}
               for r in whole]
        if whole is raw and all((n["ts"], n["title"], n["metadata"]) == (r["ts"], r["title"], r["metadata"])
                                for n, r in zip(new, raw)):
            db.execute("UPDATE docs SET masked=? WHERE source=? AND source_id=? AND kind='raw'",
                       (VERSION, source, source_id))
        else:
            replace_rows(db, source, source_id, new, ("raw",))
            changed += len(raw)
    for rid, ts, title, content, meta in db.execute(
            "SELECT id, ts, title, content, metadata FROM docs WHERE source=? AND source_id=? AND kind != 'raw' "
            "AND masked IS NOT ?", (source, source_id, VERSION)).fetchall():
        new = (mask(ts), mask(title), mask(content), json.dumps(mask_all(json.loads(meta))))
        if new[:3] == (ts, title, content) and json.loads(new[3]) == json.loads(meta):
            db.execute("UPDATE docs SET masked=? WHERE id=?", (VERSION, rid))
        else:
            db.execute("UPDATE docs SET ts=?, title=?, content=?, metadata=? WHERE id=?", (*new, rid))
            db.execute("UPDATE docs SET masked=? WHERE id=?", (VERSION, rid))  # after docs_unmask cleared it
            changed += 1
    return changed
