"""The writes an older kb makes to a store a newer kb opened: main's store_item, _repoint and replace_rows from before
rows recorded a mask version, copied as they were. `mask` stands for that kb's masking; the identity is one whose
patterns miss every secret, the worst an older kb can do."""
import hashlib
import json
import re

from kb.mask import SECRET_PATTERNS, clean

ITEM_KINDS = ("doc", "raw")
SUMMARY_KINDS = ("segment", "atom")
CHUNK_KEYS = ("chunk", "chunks")
POINTER_KEYS = ("url", "path", "host")


def chunk(text, size):
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


def replace_rows(db, source, source_id, rows, kinds=ITEM_KINDS):
    marks = ",".join("?" * len(kinds))
    db.execute(f"DELETE FROM docs WHERE source=? AND source_id=? AND kind IN ({marks})",
               (source, source_id, *kinds))
    db.executemany(
        "INSERT INTO docs(kind, source, source_id, ts, title, content, metadata) VALUES (?,?,?,?,?,?,?)",
        [(r["kind"], source, source_id, r["ts"], r["title"], r["content"], json.dumps(r["metadata"]))
         for r in rows])


def store_item(db, source, source_id, ts, title, content, meta, size, mask=lambda text: text):
    title, content = mask(clean(title or ""))[:300], mask(clean(content or ""))
    digest = hashlib.sha1(f"{title}\0{content}".encode()).hexdigest()[:16]
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
    parts = chunk(content, size)
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
    for rid, kind, old in db.execute("SELECT id, kind, metadata FROM docs WHERE source=? AND source_id=?",
                                     (source, source_id)).fetchall():
        old = json.loads(old)
        if kind in ITEM_KINDS:
            new = {**meta, **{k: old[k] for k in CHUNK_KEYS if k in old}}
        else:
            new = {k: v for k, v in old.items() if k not in POINTER_KEYS}
            new.update((k, meta[k]) for k in POINTER_KEYS if k in meta)
        db.execute("UPDATE docs SET metadata=? WHERE id=?", (json.dumps(new), rid))


def remask(db, mask):
    """main's remask without its rebuild: each row's title and text by itself, as its mask() sees them."""
    last = 0
    while True:
        batch = db.execute("SELECT id, title, content FROM docs WHERE id > ? ORDER BY id LIMIT 2000", (last,)).fetchall()
        if not batch:
            break
        updates = []
        for rid, title, content in batch:
            new_title, new_content = mask(title), mask(content)
            if (new_title, new_content) != (title, content):
                updates.append((new_title, new_content, rid))
        db.executemany("UPDATE docs SET title=?, content=? WHERE id=?", updates)
        last = batch[-1][0]


# main's private key pattern, which takes any run of base64 characters and whitespace after the header, so in one
# chunk it masks to the chunk's end and leaves the rest of the body, glued to its placeholder, in the next.
MAIN_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[A-Za-z0-9+/=\s]*(?:-----END [A-Z ]*PRIVATE KEY-----)?")


def token_mask(text):
    """A mask that knows only token shapes, with main's key pattern for keys."""
    text = MAIN_KEY.sub("[masked]", text)
    for pattern in SECRET_PATTERNS[1:]:
        text = pattern.sub("[masked]", text)
    return text
