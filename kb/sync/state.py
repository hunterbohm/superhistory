import contextlib
import json

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_sent(
  lane TEXT NOT NULL, source TEXT NOT NULL, source_id TEXT NOT NULL, marker TEXT NOT NULL,
  digest TEXT NOT NULL, PRIMARY KEY(lane, source, source_id, marker));
CREATE TABLE IF NOT EXISTS sync_versions(
  lane TEXT NOT NULL, source TEXT NOT NULL, source_id TEXT NOT NULL, marker TEXT NOT NULL,
  v INTEGER NOT NULL, gone INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(lane, source, source_id, marker));
CREATE TABLE IF NOT EXISTS sync_parked(
  lane TEXT NOT NULL, source TEXT NOT NULL, source_id TEXT NOT NULL, marker TEXT NOT NULL,
  v INTEGER NOT NULL, record TEXT NOT NULL, PRIMARY KEY(lane, source, source_id, marker));
CREATE TABLE IF NOT EXISTS sync_outbox(id INTEGER PRIMARY KEY, record TEXT NOT NULL, dseq INTEGER);
CREATE TABLE IF NOT EXISTS sync_outbox_pack(
  dseq INTEGER PRIMARY KEY, prev TEXT NOT NULL, pack_id TEXT NOT NULL, epoch INTEGER NOT NULL, plaintext BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS sync_cursor(name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sync_heads(author TEXT PRIMARY KEY, dseq INTEGER NOT NULL, hash TEXT NOT NULL);
"""


def init(db):
    db.executescript(SCHEMA)


@contextlib.contextmanager
def tx(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def get(db, name, default=None):
    row = db.execute("SELECT value FROM sync_cursor WHERE name=?", (name,)).fetchone()
    return json.loads(row[0]) if row else default


def put(db, name, value):
    db.execute("INSERT INTO sync_cursor(name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
               (name, json.dumps(value)))


def version(db, key):
    row = db.execute("SELECT v, gone FROM sync_versions WHERE lane=? AND source=? AND source_id=? AND marker=?",
                     key).fetchone()
    return (row[0], bool(row[1])) if row else None


def set_version(db, key, v, gone=False):
    db.execute("INSERT INTO sync_versions(lane, source, source_id, marker, v, gone) VALUES (?,?,?,?,?,?) "
               "ON CONFLICT(lane, source, source_id, marker) DO UPDATE SET v=excluded.v, gone=excluded.gone",
               (*key, v, int(gone)))


def raise_version(db, key, v, gone=False):
    """Record a version only when it is newer, as when this computer sees its own record come back."""
    have = version(db, key)
    if have is None or v > have[0]:
        set_version(db, key, v, gone)


def versions(db):
    return db.execute("SELECT lane, source, source_id, marker, v, gone FROM sync_versions "
                      "ORDER BY lane, source, source_id, marker").fetchall()


def sent(db):
    return {tuple(r[:4]): r[4] for r in db.execute("SELECT lane, source, source_id, marker, digest FROM sync_sent")}


def has_sent(db):
    return db.execute("SELECT 1 FROM sync_sent LIMIT 1").fetchone() is not None


def set_sent(db, key, digest):
    db.execute("INSERT INTO sync_sent(lane, source, source_id, marker, digest) VALUES (?,?,?,?,?) "
               "ON CONFLICT(lane, source, source_id, marker) DO UPDATE SET digest=excluded.digest", (*key, digest))


def drop_sent(db, key):
    db.execute("DELETE FROM sync_sent WHERE lane=? AND source=? AND source_id=? AND marker=?", key)


def park(db, key, v, record_json):
    have = db.execute("SELECT v FROM sync_parked WHERE lane=? AND source=? AND source_id=? AND marker=?",
                      key).fetchone()
    if have is None or v > have[0]:
        db.execute("INSERT OR REPLACE INTO sync_parked(lane, source, source_id, marker, v, record) VALUES (?,?,?,?,?,?)",
                   (*key, v, record_json))


def parked(db):
    return [(tuple(r[:4]), r[4]) for r in
            db.execute("SELECT lane, source, source_id, marker, record FROM sync_parked ORDER BY lane DESC, rowid")]


def unpark(db, key):
    db.execute("DELETE FROM sync_parked WHERE lane=? AND source=? AND source_id=? AND marker=?", key)


def count_parked(db):
    return db.execute("SELECT count(*) FROM sync_parked").fetchone()[0]


def add_outbox(db, record_jsons):
    db.executemany("INSERT INTO sync_outbox(record) VALUES (?)", [(r,) for r in record_jsons])


def unsigned(db, budget):
    """The next batch of unsigned records, about `budget` bytes of JSON and at least one record."""
    batch, size = [], 0
    for rid, rec in db.execute("SELECT id, record FROM sync_outbox WHERE dseq IS NULL ORDER BY id"):
        if batch and size + len(rec) > budget:
            break
        batch.append((rid, rec))
        size += len(rec)
    return batch


def unsigned_all(db):
    return db.execute("SELECT id, record FROM sync_outbox WHERE dseq IS NULL ORDER BY id").fetchall()


def unsign_all(db):
    db.execute("DELETE FROM sync_outbox_pack")
    db.execute("UPDATE sync_outbox SET dseq=NULL")


def drop_outbox(db, ids):
    db.executemany("DELETE FROM sync_outbox WHERE id=?", [(i,) for i in ids])


def outbox_records(db, dseq):
    return [r[0] for r in db.execute("SELECT record FROM sync_outbox WHERE dseq=? ORDER BY id", (dseq,))]


def count_outbox(db):
    return db.execute("SELECT count(*) FROM sync_outbox").fetchone()[0]


def save_pending(db, ids, dseq, prev, pack_id, epoch, plaintext):
    db.executemany("UPDATE sync_outbox SET dseq=? WHERE id=?", [(dseq, i) for i in ids])
    db.execute("INSERT OR REPLACE INTO sync_outbox_pack(dseq, prev, pack_id, epoch, plaintext) VALUES (?,?,?,?,?)",
               (dseq, prev, pack_id, epoch, plaintext))


def pending(db):
    return db.execute("SELECT dseq, prev, pack_id, epoch, plaintext FROM sync_outbox_pack ORDER BY dseq LIMIT 1").fetchone()


def finish_pending(db, dseq):
    db.execute("DELETE FROM sync_outbox_pack WHERE dseq=?", (dseq,))
    return db.execute("DELETE FROM sync_outbox WHERE dseq=?", (dseq,)).rowcount


def heads(db):
    return {a: (d, h) for a, d, h in db.execute("SELECT author, dseq, hash FROM sync_heads")}


def head(db, author):
    row = db.execute("SELECT dseq, hash FROM sync_heads WHERE author=?", (author,)).fetchone()
    return tuple(row) if row else (0, "")


def set_head(db, author, dseq, digest):
    db.execute("INSERT INTO sync_heads(author, dseq, hash) VALUES (?,?,?) "
               "ON CONFLICT(author) DO UPDATE SET dseq=excluded.dseq, hash=excluded.hash", (author, dseq, digest))
