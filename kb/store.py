"""The database: one SQLite file holding every item, its full-text index, and the ingest state.

An item is one `doc` row, or `raw` chunks when its text is long. The summarizer adds `segment` and `atom` rows that
point back at a long item. `ingest_state` holds each source's watermark and the health of every source on every host.
"""
import atexit
import bisect
import contextlib
import fcntl
import hashlib
import itertools
import json
import os
import pathlib
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone

from . import COMMAND, ids
from .config import NAME_RE
from .mask import (ID_NAMES, KEY_WORD, VERSION, clean, holds_secret, mask, mask_all, mask_with_origin, masked_log,
                   secret_in_id, stored_row)

CHUNK = 4000
ITEM_KINDS = ("doc", "raw")
SUMMARY_KINDS = ("segment", "atom")
CHUNK_KEYS = ("chunk", "chunks")
# The metadata a summary row copies from its item, so a search hit on a summary points at the original.
POINTER_KEYS = ("url", "path", "host")
STATE = "_kb"
# A summary that failed, by item, holding the item's hash then; the summarizer tries it again once the hash changes.
SUMMARY_FAILED = "_summarize_failed"
# How long a connection waits for another program's lock on the store before it gives up.
BUSY_MS = 30000
TOKENIZE = "porter unicode61"

SUPERSEDES = """CREATE TABLE IF NOT EXISTS supersedes(
  source TEXT NOT NULL, source_id TEXT NOT NULL, marked_by TEXT NOT NULL,
  new_source TEXT, new_source_id TEXT, at TEXT NOT NULL, v INTEGER,
  PRIMARY KEY(source, source_id, marked_by))"""
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
""" + SUPERSEDES + """;
-- A change to a row's time, title, text or metadata clears its mask version. This program's writers stamp it again once
-- they have masked the row; an older version, which does not, leaves the row to be masked again.
CREATE INDEX IF NOT EXISTS supersedes_new ON supersedes(new_source, new_source_id);
CREATE TRIGGER IF NOT EXISTS docs_unmask AFTER UPDATE OF ts, title, content, metadata ON docs BEGIN
  UPDATE docs SET masked = NULL WHERE id = new.id;
END;
CREATE TABLE IF NOT EXISTS ingest_state(
  source TEXT NOT NULL, key TEXT NOT NULL, watermark TEXT, PRIMARY KEY(source, key));
"""


class NeedsRebuild(Exception):
    """A store holds rows an older superhistory wrote, which superhistory neither reads nor masks again in place."""


REBUILD = (f"{{path}} holds rows an older {COMMAND} wrote, before each row recorded how it was masked. {COMMAND} no longer reads such rows "
           f"or masks them again in place, because an older {COMMAND}'s masking can leave a secret that no pattern finds. Run "
           f"`{COMMAND} rebuild` to read your sources into a fresh knowledge base; it takes this one's place once it checks out. "
           "If a source no longer has some of this one's items, such as sessions an agent has since deleted, it stops "
           "and says how many.")


def peek(path):
    """A connection that reads the store at `path` and leaves its files as they are. Closing a writable connection
    moves the rows in the WAL file into the store, so one is opened read-only when there is a WAL file; it may leave
    an index file (-shm) beside it, which SQLite rebuilds anyway. Without one, a read-only connection would leave new
    WAL files behind, and a writable one has nothing to move."""
    path = os.path.realpath(path)  # SQLite keeps a linked store's WAL files beside its target
    if os.path.exists(path + "-wal"):
        db = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True)
    else:
        db = sqlite3.connect(path)
    db.execute(f"PRAGMA busy_timeout={BUSY_MS}")
    return db


def connect(path, readonly=False):
    """The store at `path`, made if it is new. Raises NeedsRebuild for a store with rows an older superhistory wrote, before
    writing anything to it, so an older superhistory still opens it as it was."""
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
    if has_unversioned(db):  # an older superhistory wrote since the look above
        db.close()
        raise NeedsRebuild(REBUILD.format(path=path))
    if not readonly:
        db.execute("PRAGMA journal_mode=WAL")
        _move_marks(db)  # before the schema, which would make the supersedes table without them
        db.executescript(SCHEMA)
        if not _versioned(db):
            db.execute("ALTER TABLE docs ADD COLUMN masked TEXT")  # an empty store from before rows recorded it
        db.execute("CREATE INDEX IF NOT EXISTS docs_masked ON docs(masked)")
    return db


def _stat(name):
    try:
        st = os.stat(name)
    except FileNotFoundError:
        return None
    return st.st_ino, st.st_size, st.st_mtime_ns


def _stamp(path):
    """(main file, -wal file, -shm file) at `path`, each as its inode, size and modification time, or None."""
    return _stat(path), _stat(path + "-wal"), _stat(path + "-shm")


READ_TRIES = 5
COPY_PREFIX = f"{COMMAND}-read-"
IMMUTABLE, ORDINARY, COPY = "immutable", "ordinary", "copy"
_copies = {}  # a store's path -> [folder holding a private copy, the stamp of the store's files it was copied from]


def read_only(path, run, scratch=None):
    """What `run(db)` returns, for a connection that cannot write the store. `run` must return its result, not print it.
    Which connection it gets depends on the files beside the store, and so does what the read promises:

    1. No -wal file, or an empty one and no -shm file: no writer is open and the main file holds every row. It is read
       as immutable, which takes no lock and makes no file, in a folder you can write or not.
    2. A -wal file and an -shm file: a writer is open. An ordinary read-only connection reads one snapshot of the store,
       rows not yet checkpointed included, and SQLite's own locks keep a checkpoint from changing it. It copies
       nothing and makes no file, in a folder you can write or not.
    3. The window between the look at 2 and SQLite's first read: a writer that closes there removes its two files. In a
       folder you cannot write SQLite cannot make them, the open fails, and the read starts again at 1. In a writable
       folder SQLite makes its own empty -wal and -shm, which the next writer takes over. That is the one case where
       files can appear beside a store that is being read. They hold none of the store's rows, and the rows read are
       right. The window is narrowed by looking again after the connection is made.
    4. A -wal file with rows and no -shm file: a writer died. In a writable folder an ordinary read-only connection
       reads its rows, and makes an -shm file. In a folder you cannot write that fails, so `run` reads a private copy of
       the main file and the -wal file in `scratch` (the system's temporary folder by default). The copy's folder is
       readable only by you, it is kept for the next call while neither file changes, and drop_copies() removes it; it
       also goes at normal exit and on an exception. A process ended by SIGKILL cannot remove its copy, and the next
       process that makes a copy removes what a dead one left.

    A write to the main file during an immutable read, or a -wal file with rows that appears during it, starts the read
    again."""
    path = os.path.realpath(path)
    last = None
    for _ in range(READ_TRIES):
        before = _stamp(path)
        if before[0] is None:
            raise sqlite3.OperationalError(f"unable to open database file: {path}")
        mode = _mode(path, before)
        try:
            if mode == COPY:
                db = _connect(_private_copy(path, scratch, before), immutable=False)
            else:
                db = _connect(path, immutable=mode == IMMUTABLE)
        except sqlite3.OperationalError as exc:
            last = exc
            continue
        with contextlib.closing(db):
            if mode == ORDINARY and (os.path.exists(path + "-wal"), os.path.exists(path + "-shm")) != (
                    before[1] is not None, before[2] is not None):
                continue  # a writer came or went since the look
            try:
                _check_version(db, path)
            except sqlite3.OperationalError as exc:
                last = exc
                continue
            result = error = None
            try:
                result = run(db)
            except NeedsRebuild:
                raise
            except Exception as exc:
                error = exc
        after = _stamp(path)
        if mode != IMMUTABLE or (after[0] == before[0] and not (after[1] and after[1][1])):
            if error:
                raise error
            return result
    raise last or sqlite3.OperationalError("the knowledge base keeps changing under the read; ask again")


def _mode(path, stamp):
    main, wal, shm = stamp
    if wal is None or (not wal[1] and shm is None):
        return IMMUTABLE
    if shm is not None or os.access(os.path.dirname(path), os.W_OK | os.X_OK):
        return ORDINARY
    return COPY


def _connect(path, immutable):
    uri = pathlib.Path(path).as_uri() + "?mode=ro" + ("&immutable=1" if immutable else "")
    db = sqlite3.connect(uri, uri=True)
    db.execute(f"PRAGMA busy_timeout={BUSY_MS}")
    return db


def _check_version(db, shown):
    """The first read of the connection: where SQLite makes a missing -wal and -shm file, and fails if it cannot."""
    if has_unversioned(db):
        db.close()
        raise NeedsRebuild(REBUILD.format(path=shown))


def _copy_file(src, dst):
    os.close(os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    shutil.copyfile(src, dst)


def _private_copy(path, scratch, stamp):
    """The path of a private copy of the store's main file and -wal file, as of `stamp`: the one made for an earlier
    call when neither file has changed since, else a new one. Raises sqlite3.OperationalError when the files changed
    while they were copied, or the disk has no room."""
    held = _copies.get(path)
    if held and held[1] == (stamp[0], stamp[1]) and os.path.isdir(held[0]):
        return os.path.join(held[0], f"{COMMAND}.sqlite")
    drop_copies(path)
    scratch = os.fspath(scratch or tempfile.gettempdir())
    os.makedirs(scratch, mode=0o700, exist_ok=True)
    _sweep(scratch)
    folder = tempfile.mkdtemp(prefix=f"{COPY_PREFIX}{os.getpid()}-", dir=scratch)  # mode 0700
    _copies[path] = [folder, None]  # registered before the copy, so a signal halfway through still removes it
    try:
        need = stamp[0][1] + stamp[1][1]
        if shutil.disk_usage(folder).free < need + need // 10 + (64 << 20):
            raise sqlite3.OperationalError(f"not enough room in {scratch} to read the knowledge base at {path}")
        copy = os.path.join(folder, f"{COMMAND}.sqlite")
        _copy_file(path, copy)
        _copy_file(path + "-wal", copy + "-wal")
        if _stamp(path)[:2] != stamp[:2] or _stat(path + "-shm"):
            raise sqlite3.OperationalError("the knowledge base changed while it was copied")
    except BaseException:
        drop_copies(path)
        raise
    _copies[path][1] = (stamp[0], stamp[1])
    return copy


def drop_copies(path=None):
    """Remove the private copy of the store at `path`, or every private copy."""
    for key in [path] if path else list(_copies):
        held = _copies.pop(key, None)
        if held:
            shutil.rmtree(held[0], ignore_errors=True)


atexit.register(drop_copies)


def _sweep(scratch):
    """Remove the copies a process that no longer runs left in `scratch`."""
    for name in os.listdir(scratch):
        pid = name[len(COPY_PREFIX):].split("-", 1)[0]
        if not name.startswith(COPY_PREFIX) or not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            shutil.rmtree(os.path.join(scratch, name), ignore_errors=True)
        except OSError:
            pass


# Who made a mark moved from a store an older superhistory wrote: this computer, before it had an install id. As the lowest
# name it ranks below any mark this install makes since, a clear too, and above a synced one, as an unsynced mark does.
MOVED_MARK_BY = ""


def _has_old_marks(db):
    return any(column[1] == "superseded_by" for column in db.execute("PRAGMA table_info(docs)"))


def _old_marks(db):
    """{item: the item that replaces it} as an older superhistory kept them: each in docs.superseded_by, the replacing row's
    id. A row since gone, as an ingest that refreshed the replacing item leaves it, is named ("", "#<id>"): the item
    is still superseded, as the older superhistory read it. Empty for a store with no such column."""
    if not _has_old_marks(db):
        return {}
    marks = {}
    for src, sid, new_src, new_sid, rid in db.execute(
            "SELECT o.source, o.source_id, n.source, n.source_id, o.superseded_by FROM docs o "
            "LEFT JOIN docs n ON n.id = o.superseded_by WHERE o.superseded_by IS NOT NULL ORDER BY o.id"):
        marks[(src, sid)] = (new_src, new_sid) if new_src is not None else ("", f"#{rid}")
    return marks


def _move_marks(db):
    """Moves an older superhistory's supersede marks into the supersedes table in one transaction, which makes the table too.
    Should it stop partway, the store is as it was, its readers take the marks from docs.superseded_by as before, and
    the error goes to the caller. The column is emptied as the marks move, so a second run moves nothing."""
    if not _has_old_marks(db) or not db.execute("SELECT 1 FROM docs WHERE superseded_by IS NOT NULL LIMIT 1").fetchone():
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(SUPERSEDES)
        db.executemany("INSERT INTO supersedes(source, source_id, marked_by, new_source, new_source_id, at, v) "
                       "VALUES (?,?,?,?,?,?,NULL) ON CONFLICT(source, source_id, marked_by) DO UPDATE SET "
                       "new_source=excluded.new_source, new_source_id=excluded.new_source_id, at=excluded.at",
                       [(*item, MOVED_MARK_BY, *new, now_iso()) for item, new in _old_marks(db).items()])
        db.execute("UPDATE docs SET superseded_by = NULL WHERE superseded_by IS NOT NULL")
        db.commit()
    except BaseException:
        db.rollback()
        raise


def all_marks(db):
    """Every supersede mark and clear the store holds, as (source, source_id, marked_by, new_source, new_source_id, at,
    v) in the order they were made: the rows of the supersedes table, a clear's too, and an older kb's marks still in
    docs.superseded_by, read as _move_marks would move them."""
    rows = []
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='supersedes'").fetchone():
        rows = db.execute("SELECT source, source_id, marked_by, new_source, new_source_id, at, v FROM supersedes "
                          "ORDER BY at, source, source_id, marked_by").fetchall()
    marks = {row[:3]: row for row in rows}
    at = now_iso()
    for item, new in _old_marks(db).items():
        key = (*item, MOVED_MARK_BY)  # as _move_marks does: the column's replacement and time win, the row's version stays
        marks[key] = (*key, *new, at, marks[key][6] if key in marks else None)
    return list(marks.values())


def has_unversioned(db):
    """Whether the store holds a row that records no mask version: one an older superhistory wrote or changed."""
    columns = {column[1] for column in db.execute("PRAGMA table_info(docs)")}
    if not columns:
        return False  # a new store
    if "masked" not in columns:
        return db.execute("SELECT 1 FROM docs LIMIT 1").fetchone() is not None
    return db.execute("SELECT 1 FROM docs WHERE masked IS NULL LIMIT 1").fetchone() is not None


def _versioned(db):
    return any(column[1] == "masked" for column in db.execute("PRAGMA table_info(docs)"))


def unversioned(db, ids):
    """The ids among `ids` that an older superhistory wrote or changed, which record no mask version, and for a chunk the ids
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
    version are not among them: an older superhistory wrote them, and the store is rebuilt instead."""
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


def _masked_chunks(chunks):
    """A long item's chunk texts masked so that each masks clean by itself, as a reader shows one, and all of them as
    one text, as an older superhistory or another store may have cut an item before masking it and split a secret neither chunk
    shows: masked as one text, each chunk taking back what came from its own characters (mask_with_origin), then each
    by itself, until neither changes anything. A chunk keeps its place, so no cut moves to leave another token alone
    at a chunk's start; a placeholder goes with the chunk its first character came from, and a chunk can be left
    empty."""
    for _ in range(16):
        text = "".join(chunks)
        masked, origin = mask_with_origin(text)
        if masked != text:
            ends, parts, at = list(itertools.accumulate(len(c) for c in chunks)), [[] for _ in chunks], 0
            for character, came in zip(masked, origin):
                if came >= 0:
                    at = bisect.bisect_right(ends, came)
                parts[at].append(character)
            chunks = ["".join(part) for part in parts]
        again = [mask(c) for c in chunks]
        if again == chunks and masked == text:
            return chunks
        chunks = again
    return [c if mask(c) == c else "[masked]" for c in chunks]  # still changing: what still changes goes whole


def item_hash(title, text):
    """The hash an item's rows record of its title and text, as stored: masked, so it confirms no guess at a secret."""
    return hashlib.sha1(f"{title}\0{text}".encode()).hexdigest()[:16]


def _as_stored(rows):
    """`rows` as replace_rows() stores them: masked, a long item's chunks by _masked_chunks(), and the item's rows
    recording the hash of its masked title and text, whatever hash they came with. When that differs from the hash
    they came with, summaries among `rows` name the new one as _rehash_summaries() has them do."""
    rows = [{**r, "ts": mask(str(r["ts"])), "title": mask(r["title"]), "metadata": mask_all(r["metadata"])} for r in rows]
    raw = sorted((r for r in rows if r["kind"] == "raw"), key=lambda r: r["metadata"].get("chunk", 0))
    rows = [{**r, "content": mask(r["content"])} for r in rows if r["kind"] != "raw"]
    if raw:
        # Each chunk keeps its own row and metadata; one left empty goes, and the rest are numbered again.
        kept = [(r, part) for r, part in zip(raw, _masked_chunks([r["content"] for r in raw])) if part]
        rows += [{**r, "content": part, "metadata": {**r["metadata"], "chunk": i, "chunks": len(kept)}}
                 for i, (r, part) in enumerate(kept)]
    items = sorted((r for r in rows if r["kind"] in ITEM_KINDS), key=lambda r: r["metadata"].get("chunk", 0))
    if items:
        sent = items[0]["metadata"].get("hash")
        digest = item_hash(items[0]["title"], "".join(r["content"] for r in items))
        for r in items:
            r["metadata"]["hash"] = digest
        if sent is not None and sent != digest:
            # The text is not what the sender hashed: summaries written with it follow it, as remask's do.
            for r in rows:
                if r["kind"] in SUMMARY_KINDS:
                    of_hash = r["metadata"].pop("of_hash", None)
                    r["metadata"].pop("prefix", None)
                    if of_hash == sent:
                        r["metadata"]["of_hash"] = digest
    return rows


# Fields of an item's metadata whose values its stored id starts with, each joined by a colon: superhistory's host, then its
# reader's own (mask.ID_NAMES).
ID_FIELDS = ("host",) + ID_NAMES


def secret_id(source, source_id, names=()):
    """Whether superhistory refuses an item under this source and stored id. superhistory stores "<install>:<reader's id>", or for an item
    read over SSH "<install>@<host>:<reader's id>" (kb.ids): that prefix is superhistory's own and is checked apart, its host as
    a name config allows. A Config with no install id writes "<machine>:" and "<machine>@<host>:" instead. An id from before the prefix is "<host>:<reader's id>", with the host in the item's
    metadata. `names` are the values the item's rows agree on for ID_FIELDS, the host first. Given
    that host, with the name config allows a host and the id starting with it, the id is checked as the host and the
    reader's id apart, since joined by superhistory's colon a host such as "tokenbox" would read as the name of a secret; the
    reader's id is read with the reader's names (mask.secret_in_id). Any other id is checked whole, so
    "password:...", "token2:..." or a link with a password in it is refused."""
    # The install's prefix, "<install>:" or "<install>@<host>:" (kb.ids), is superhistory's own, with a host config allows. A
    # Config with no install id prefixes by the machine instead, "<machine>@<host>:", read so where the item's rows
    # record that host and the machine is a name config allows (its "<machine>:" is the host branch below).
    head, colon, rest = source_id.partition(":")
    install, at, ssh_host = head.partition("@")
    machine = (at and NAME_RE.match(install) and not KEY_WORD.fullmatch(install) and not holds_secret(install)
               and names and names[0] == ssh_host)
    if (colon and (ids.INSTALL_ID.fullmatch(install) or machine) and not rest.startswith("//")
            and (not at or NAME_RE.match(ssh_host) and not KEY_WORD.fullmatch(ssh_host))):
        return holds_secret(source) or holds_secret(ssh_host) or secret_in_id(rest, names[1:])
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
    Summary rows are left out: an older superhistory's copy only some fields."""
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
    # Masked here too, since Sync writes rows another store sent; for rows store_item made it changes nothing.
    rows = _as_stored(rows)
    marks = ",".join("?" * len(kinds))
    if db.execute(f"SELECT 1 FROM docs WHERE source=? AND source_id=? AND kind IN ({marks}) AND masked IS NOT ? LIMIT 1",
                  (source, source_id, *kinds, VERSION)).fetchone():
        # Rows of another mask's version may hold what today's would hide; their words stay in the index's data and
        # the file's free pages until a rebuild.
        set_watermark(db, STATE, "remask", "rebuild")
    db.execute(f"DELETE FROM docs WHERE source=? AND source_id=? AND kind IN ({marks})",
               (source, source_id, *kinds))
    db.executemany(
        "INSERT INTO docs(kind, source, source_id, ts, title, content, metadata, masked) VALUES (?,?,?,?,?,?,?,?)",
        [(r["kind"], source, source_id, r["ts"], r["title"], r["content"], json.dumps(r["metadata"]), VERSION)
         for r in rows])


def delete_item(db, source, source_id):
    """Removes the item's rows. Returns the number of rows removed. Supersede marks on it stay, so a store reaches the
    same marks whether Sync delivers a mark before or after the item's deletion; but when its id holds a secret, read
    while its rows still name it, the marks on and to it go too, or they would keep the id."""
    secret = _secret_item(db, source, source_id)
    removed = db.execute("DELETE FROM docs WHERE source=? AND source_id=?", (source, source_id)).rowcount
    if secret:
        db.execute("DELETE FROM supersedes WHERE source=? AND source_id=? OR new_source=? AND new_source_id=?",
                   (source, source_id, source, source_id))
    return removed


def _refuse_secret_ids(db, *items):
    """Raises ValueError when an item's id holds a secret, as an older superhistory's row can, so no mark copies it."""
    if any(_secret_item(db, *item) for item in items):
        raise ValueError("an item whose id looks like it holds a secret takes no supersede mark")


def _secret_item(db, source, source_id):
    """Whether the item's id holds a secret, read with the names its rows here record (item_names). An item with no
    rows here is not judged: without its names an id such as a Hermes profile's reads as a secret it is not, and a
    mark to an item deleted for now, or one Sync sent before the item, would be lost."""
    if not db.execute("SELECT 1 FROM docs WHERE source=? AND source_id=? LIMIT 1", (source, source_id)).fetchone():
        return False
    return secret_id(source, source_id, item_names(db, source, source_id))


def mark_superseded(db, source, source_id, new, by, at=None, v=None):
    """Marks the item as replaced by `new`, a (source, source_id) pair. The mark is about items, not rows, so it
    survives a refresh of either. `by` is the install that made it, and each install keeps its own mark on an item, so
    a mark synced from another computer never overwrites this one's. `v` is Sync's version of the mark. Raises
    ValueError when either item's id holds a secret."""
    _refuse_secret_ids(db, (source, source_id), new)
    db.execute("INSERT INTO supersedes(source, source_id, marked_by, new_source, new_source_id, at, v) "
               "VALUES (?,?,?,?,?,?,?) ON CONFLICT(source, source_id, marked_by) DO UPDATE SET "
               "new_source=excluded.new_source, new_source_id=excluded.new_source_id, at=excluded.at, v=excluded.v",
               (source, source_id, by, new[0], new[1], now_iso() if at is None else at, v))


def clear_superseded(db, source, source_id, by, at=None, v=None):
    """Clears this install's mark by keeping its row with no target, so the clear still outranks an older mark from
    another computer. Raises ValueError when the item's id holds a secret."""
    _refuse_secret_ids(db, (source, source_id))
    db.execute("INSERT INTO supersedes(source, source_id, marked_by, new_source, new_source_id, at, v) "
               "VALUES (?,?,?,NULL,NULL,?,?) ON CONFLICT(source, source_id, marked_by) DO UPDATE SET "
               "new_source=NULL, new_source_id=NULL, at=excluded.at, v=excluded.v",
               (source, source_id, by, now_iso() if at is None else at, v))


def superseded_by(db, source, source_id):
    return superseded(db, [(source, source_id)]).get((source, source_id))


def superseded(db, items):
    """{item: the item that replaces it} for those of `items` that are superseded. Of several marks the latest wins:
    one not synced yet (no `v`), else the highest `v`, then the highest marking install. A winning clear (no target)
    means the item is not superseded."""
    items = list(items)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='supersedes'").fetchone():
        old = _old_marks(db)  # a store an older superhistory wrote, read before superhistory first wrote to it
        return {item: old[item] for item in items if item in old and not _secret_item(db, *old[item])}
    marks = {}
    for i in range(0, len(items), 400):
        batch = items[i:i + 400]
        where = " OR ".join("(source=? AND source_id=?)" for _ in batch)
        for src, sid, by, new_src, new_sid, v in db.execute(
                f"SELECT source, source_id, marked_by, new_source, new_source_id, v FROM supersedes WHERE {where}",
                [v for pair in batch for v in pair]):
            rank = (v is None, v or 0, by)
            if (src, sid) not in marks or rank > marks[(src, sid)][0]:
                marks[(src, sid)] = (rank, (new_src, new_sid))
    # A mark that names an id holding a secret, synced or written before superhistory refused one, names no replacement. Each
    # replacement is read once, however many marks name it.
    secret = {}
    for _, new in marks.values():
        if new[0] is not None and new not in secret:
            secret[new] = _secret_item(db, *new)
    return {item: new for item, (_, new) in marks.items() if new[0] is not None and not secret[new]}


def store_item(db, source, source_id, ts, title, content, meta):
    """Store one item as a `doc` row, or as `raw` chunks when long. Returns False when nothing changed. The same text
    under new metadata, such as a moved file, keeps its rows and summaries and takes the new metadata."""
    # The title is cut after masking, so no secret is cut too short to know, then masked again, as the cut can leave
    # the start of something a mask takes: the hash is then of the title as stored.
    ts, title, content = mask(str(ts)), mask(mask(clean(title or ""))[:300]), mask(clean(content or ""))
    meta = mask_all(meta)
    parts = chunk(content)
    if len(parts) == 1:
        rows = [{"kind": "doc", "ts": ts, "title": title, "content": content, "metadata": dict(meta)}]
        kinds = ITEM_KINDS + SUMMARY_KINDS
    else:
        rows = [{"kind": "raw", "ts": ts, "title": title, "content": part,
                 "metadata": {**meta, "chunk": i, "chunks": len(parts)}} for i, part in enumerate(parts)]
        kinds = ITEM_KINDS
    # The rows as replace_rows() stores them, so the hash compared is the one they record.
    rows = _as_stored(rows)
    digest = rows[0]["metadata"]["hash"]
    meta = {**meta, "hash": digest}
    old = db.execute("SELECT metadata FROM docs WHERE source=? AND source_id=? AND kind IN ('doc','raw') LIMIT 1",
                     (source, source_id)).fetchone()
    if old:
        stored = {k: v for k, v in json.loads(old[0]).items() if k not in CHUNK_KEYS}
        if stored == meta:
            return False
        if stored.get("hash") == digest:
            _repoint(db, source, source_id, meta)
            return True
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
    older superhistory cut one before masking it and the next failing run writes it again, and every row whose source, key or
    value holds a secret, such as a position an older superhistory read from an id it now refuses. A source whose position goes
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
    mask(), a line may be an older superhistory's, which can hold a piece of a secret too short to mask, so only its time shows."""
    if get_watermark(db, STATE, "state_masked") != VERSION:
        return [(unit, line.split(" ", 1)[0] + f" (recorded before {COMMAND} was updated; the next run records the error)")
                for unit, line in _state(db, "fail:")]
    return [(unit, mask(line)) for unit, line in _state(db, "fail:")]


def away(db):
    """(host, time of the first miss) for hosts ssh cannot reach."""
    return _state(db, "away:")


def last_ingest(db):
    return get_watermark(db, STATE, "last_ingest")


def remask(db, seconds=None, log=None):
    """Mask every row of another version again, a long item's chunks as one text, and record today's version. An item
    with a row that records no version is left as it is: an older superhistory wrote it, and the store is rebuilt instead. An
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
    number of rows it rewrote. An item whose text changed records the hash of its masked text, its summaries name that
    hash, and no hash of its text before stays: not their prefix hashes, nor a failed summary's mark."""
    changed, before = 0, _recorded_hash(db, source, source_id)
    for kind in ITEM_KINDS:
        old = [{"id": rid, "kind": kind, "ts": ts, "title": title, "content": content, "metadata": json.loads(meta),
                "masked": v} for rid, ts, title, content, meta, v in db.execute(
                    "SELECT id, ts, title, content, metadata, masked FROM docs WHERE source=? AND source_id=? "
                    "AND kind=?", (source, source_id, kind))]
        if all(r["masked"] == VERSION for r in old):
            continue
        versions = [r["masked"] for r in old]  # masked again as the rows they are: an older kb's may hold a key's rest
        with stored_row(*versions):
            new = _as_stored(old)
        if new == old:
            db.execute("UPDATE docs SET masked=? WHERE source=? AND source_id=? AND kind=?",
                       (VERSION, source, source_id, kind))
        elif kind == "raw":
            with stored_row(*versions):
                replace_rows(db, source, source_id, old, ("raw",))
            changed += len(old)
        else:
            for r in new:
                db.execute("UPDATE docs SET ts=?, title=?, content=?, metadata=? WHERE id=?",
                           (r["ts"], r["title"], r["content"], json.dumps(r["metadata"]), r["id"]))
                db.execute("UPDATE docs SET masked=? WHERE id=?", (VERSION, r["id"]))  # after docs_unmask cleared it
            changed += len(new)
    after = _recorded_hash(db, source, source_id)
    if after != before:
        _rehash_summaries(db, source, source_id, before, after)
    for rid, ts, title, content, meta, version in db.execute(
            "SELECT id, ts, title, content, metadata, masked FROM docs WHERE source=? AND source_id=? "
            "AND kind NOT IN (?, ?) AND masked IS NOT ?", (source, source_id, *ITEM_KINDS, VERSION)).fetchall():
        with stored_row(version):
            new = (mask(ts), mask(title), mask(content), json.dumps(mask_all(json.loads(meta))))
        if new[:3] == (ts, title, content) and json.loads(new[3]) == json.loads(meta):
            db.execute("UPDATE docs SET masked=? WHERE id=?", (VERSION, rid))
        else:
            db.execute("UPDATE docs SET ts=?, title=?, content=?, metadata=? WHERE id=?", (*new, rid))
            db.execute("UPDATE docs SET masked=? WHERE id=?", (VERSION, rid))
            changed += 1
    return changed


def _recorded_hash(db, source, source_id):
    row = db.execute("SELECT json_extract(metadata, '$.hash') FROM docs WHERE source=? AND source_id=? "
                     "AND kind IN ('doc', 'raw') LIMIT 1", (source, source_id)).fetchone()
    return row[0] if row else None


def _rehash_summaries(db, source, source_id, before, after):
    """After an item's text changed under a new mask: a summary of that text names its new hash, one of an older text
    names none, as its hash could be of a prefix of today's text with a secret in it, and none keeps the hash of the
    text it started from, so the item's next growth gets a full pass. A failed summary's mark moves the same way."""
    for rid, meta in db.execute("SELECT id, metadata FROM docs WHERE source=? AND source_id=? AND kind IN (?, ?)",
                                (source, source_id, *SUMMARY_KINDS)).fetchall():
        meta = json.loads(meta)
        of_hash = meta.pop("of_hash", None)
        meta.pop("prefix", None)
        if of_hash == before:
            meta["of_hash"] = after
        db.execute("UPDATE docs SET metadata=? WHERE id=?", (json.dumps(meta), rid))
    key = f"{source}|{source_id}"
    db.execute("UPDATE ingest_state SET watermark=? WHERE source=? AND key=? AND watermark=?",
               (after, SUMMARY_FAILED, key, before))
    db.execute("DELETE FROM ingest_state WHERE source=? AND key=? AND watermark IS NOT ?", (SUMMARY_FAILED, key, after))
