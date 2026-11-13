"""Records: the one shape every change takes when it leaves a computer, its wire form, and `apply`.

`Item` and `Gone` share one version key per item, `Summary` has its own, and `Supersede` has one per marking computer
and item. `Snapshot` announces a compaction inside its author's chain and changes nothing when applied.
"""
import dataclasses
import hashlib
import json
import re
from datetime import datetime

from . import egress, state
from .. import ids, store
from ..mask import KEY_WORD

# The rows whose source_id `kb.ids.owner` gives to one install: "<id>", "<id>:..." and "<id>@<host>:...". Its arguments
# come from `owned_args`, and the bare "<id>" term is there only so this agrees with `kb.ids.owner` exactly.
OWNED = "(source_id = ? OR substr(source_id, 1, ?) IN (?, ?))"
_HEX64 = re.compile(r"[0-9a-f]{64}")
HOST_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")  # the names kb's config allows a host
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


class BadRecord(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Kind:
    lane: object
    order: int
    fields: dict


KINDS = {
    "item": Kind("item", 0, {"ts": str, "title": str, "meta": dict, "rows": list}),
    "gone": Kind("item", 0, {}),
    "summary": Kind("summary", 1, {"of_hash": str, "rows": list}),
    "supersede": Kind("supersede", 2, {"by": str, "new": (list, type(None)), "at": str}),
    "snapshot": Kind(None, 3, {"id": str, "through": int, "parts": int, "manifest": str}),
}
SUMMARY_ROW = {"kind", "ts", "title", "content", "metadata"}


@dataclasses.dataclass(frozen=True)
class Record:
    kind: str
    source: str
    source_id: str
    v: int
    body: dict

    @property
    def key(self):
        return KINDS[self.kind].lane, self.source, self.source_id, self.body["by"] if self.kind == "supersede" else ""

    @property
    def writer(self):
        """The install id of the only computer allowed to write this record, or None for an announcement."""
        if self.kind == "snapshot":
            return None
        return self.body["by"] if self.kind == "supersede" else ids.owner(self.source_id)


def owned_args(install):
    return install, len(install) + 1, install + ":", install + "@"


def has_owner(source_id):
    """`kb.ids.owner` names a real install id. Its `$` also lets a look-alike that ends in a newline through."""
    return ids.INSTALL_ID.fullmatch(ids.owner(source_id) or "") is not None


def to_wire(rec):
    head = {"k": rec.kind, "v": rec.v}
    if rec.kind != "snapshot":
        head.update(s=rec.source, i=rec.source_id)
    return {**head, **rec.body}


def to_json(rec):
    return json.dumps(to_wire(rec), ensure_ascii=False, separators=(",", ":"))


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _is_unicode(obj):
    """Whether every string in a record encodes as UTF-8. JSON can carry a lone surrogate, which SQLite refuses only
    once a write is under way, and the store, the versions and the parked records all key on the ids."""
    try:
        json.dumps(obj, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def from_wire(obj):
    """Parse one record from untrusted JSON. Everything past this point trusts the shape."""
    if not isinstance(obj, dict) or obj.get("k") not in KINDS:
        raise BadRecord("unknown record kind")
    if not _is_unicode(obj):
        raise BadRecord("text that is not valid unicode")
    kind = obj["k"]
    spec = KINDS[kind].fields
    head = {"k", "v"} if kind == "snapshot" else {"k", "v", "s", "i"}
    if set(obj) != head | set(spec):
        raise BadRecord(f"{kind} record has the wrong fields")
    if not _is_int(obj["v"]) or obj["v"] < 0:
        raise BadRecord("bad version")
    source, source_id = obj.get("s", ""), obj.get("i", "")
    if kind != "snapshot" and not (isinstance(source, str) and source and isinstance(source_id, str)
                                   and has_owner(source_id)):
        raise BadRecord("item key has no install prefix")
    for name, typ in spec.items():
        if not isinstance(obj[name], typ) or (typ is int and isinstance(obj[name], bool)):
            raise BadRecord(f"bad {name}")
    body = {name: obj[name] for name in spec}
    _CHECK[kind](body)
    return Record(kind, source, source_id, obj["v"], body)


TEXT_META = ("host", "path", "profile", "url")  # what kb reads as text: its pointer, and the names that tell ids apart


def _meta_is_text(meta):
    """kb reads these fields as text, in the pointer it prints for a hit and in its secret check, and anything else fails
    there, in every search that finds the item, after it was stored. Absent or null, kb reads as none."""
    return all(meta.get(field) is None or isinstance(meta[field], str) for field in TEXT_META)


def _check_item(body):
    if (not body["rows"] or not all(isinstance(r, str) for r in body["rows"])
            or not isinstance(body["meta"].get("hash"), str) or not _meta_is_text(body["meta"]) or not _keys_kept(body["meta"])):
        raise BadRecord("bad item rows")


_KB_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})")


def _is_aware_time(value):
    """A pass's `at` in the form kb writes: ASCII digits, a T and a zone, so egress, which takes invisible characters
    out, leaves it as it is. kb reads it with datetime.fromisoformat, a trailing Z read as UTC, and subtracts it from an
    aware now."""
    if not (isinstance(value, str) and _KB_TIME.fullmatch(value)):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _reads_back(meta, of_hash):
    """Whether a summary row's metadata reads back the way kb and this client read it. This client takes a stored
    summary's `of_hash` from its first row, so each row's is the record's own. kb's summarizer compares a pass's
    `prefix` and does arithmetic and date math on its `of_chars` and `at`, so a row with a prefix has all three, each
    in its type."""
    if meta.get("of_hash", of_hash) != of_hash:
        return False
    if "prefix" in meta and not ({"at", "of_chars"} <= meta.keys() and isinstance(meta["prefix"], str)):
        return False
    if "of_chars" in meta and not (_is_int(meta["of_chars"]) and meta["of_chars"] >= 0):
        return False
    return "at" not in meta or _is_aware_time(meta["at"])


def _keys_kept(meta):
    """Whether egress sends metadata on with every key as it is. Egress cleans each key like any text, taking invisible
    characters out, and of two keys that then meet the later wins: `at` with a zero-width space would replace `at`. A
    computer that accepted that could put a field its next computer refuses into a snapshot. Egress keeps each value's
    type, so a key it keeps keeps its field. A key of printable ASCII has nothing for egress to take out, and masking,
    the one change left to it, never makes a field's name, so only other keys need the costly clean."""
    return all((key.isascii() and key.isprintable()) or egress.clean(key) == key for key in meta)


def _check_summary(body):
    for row in body["rows"]:
        if not (isinstance(row, dict) and set(row) == SUMMARY_ROW and row["kind"] in store.SUMMARY_KINDS
                and all(isinstance(row[f], str) for f in ("ts", "title", "content")) and isinstance(row["metadata"], dict)
                and _meta_is_text(row["metadata"]) and _reads_back(row["metadata"], body["of_hash"])
                and _keys_kept(row["metadata"])):
            raise BadRecord("bad summary row")


def _check_supersede(body):
    new = body["new"]
    if not ids.INSTALL_ID.fullmatch(body["by"]) or (
            new is not None and not (len(new) == 2 and all(isinstance(x, str) for x in new))):
        raise BadRecord("bad supersede")


def _check_snapshot(body):
    if not (_UUID.fullmatch(body["id"]) and body["through"] >= 0 and body["parts"] >= 1 and _HEX64.fullmatch(body["manifest"])):
        raise BadRecord("bad snapshot")


_CHECK = {"item": _check_item, "gone": lambda body: None, "summary": _check_summary, "supersede": _check_supersede,
          "snapshot": _check_snapshot}


def encode(recs):
    return "\n".join(to_json(r) for r in recs).encode()


def decode(data):
    try:
        lines = data.decode().split("\n") if data else []
        return [from_wire(json.loads(line)) for line in lines]
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BadRecord("records are not JSON lines") from None


def next_version(have, now_ms):
    return max(now_ms, (have[0] if have else 0) + 1)


def summary_digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def mark_digest(new, at):
    return hashlib.sha256(json.dumps([new, at]).encode()).hexdigest()


def item_exists(db, source, source_id):
    return db.execute("SELECT 1 FROM docs WHERE source=? AND source_id=? AND kind IN ('doc','raw') LIMIT 1",
                      (source, source_id)).fetchone() is not None


def holds_secret(*texts):
    """Whether the chokepoint would change any of these texts."""
    return any(isinstance(t, str) and egress.hide(t) != t for t in texts)


def id_holds_secret(source_id, host=None):
    """Whether an item id holds a secret, so that the item stays on the computer that read it, as kb no longer stores
    one: a changed id could stand for another item. As in kb, an id that starts with its rows' `host`, or with an
    install's prefix, is read as that head and the rest, since joined by a colon a host such as "tokenbox" would read as
    a secret's name. A host counts only under a name kb's config allows a host, which is never a word for a secret, and
    a rest that starts with "//" is a link. Any other id is read whole."""
    if not isinstance(source_id, str):
        return False
    install, at, named = source_id.split(":", 1)[0].partition("@")
    if _host_name(host) and source_id.startswith(f"{host}:"):
        head = host
    elif ids.INSTALL_ID.fullmatch(install) and ":" in source_id and (not at or _host_name(named)):
        head = source_id.split(":", 1)[0]
    else:
        return holds_secret(source_id)
    rest = source_id[len(head) + 1:]
    return holds_secret(source_id) if rest.startswith("//") else holds_secret(head, rest)


def _host_name(host):
    return isinstance(host, str) and HOST_NAME.fullmatch(host) is not None and not KEY_WORD.fullmatch(host)


def _host(rec):
    """The host an item's or summary's own metadata names, as kb records it."""
    if rec.kind == "item":
        return rec.body.get("meta", {}).get("host")
    if rec.kind == "summary":
        hosts = {r.get("metadata", {}).get("host") for r in rec.body.get("rows", []) if isinstance(r, dict)}
        return hosts.pop() if len(hosts) == 1 else None
    return None


def _ids_hold_secret(rec):
    target = (rec.body.get("new") or [None, None]) if rec.kind == "supersede" else [None, None]
    return holds_secret(rec.source, target[0]) or id_holds_secret(rec.source_id, _host(rec)) or id_holds_secret(target[1])


def _kb_refuses(db, source, source_id):
    """Whether kb's own store would refuse this item's id, read with the names its stored rows record."""
    return store.secret_id(source, source_id, store.item_names(db, source, source_id))


def item_hash(title, text):
    """kb's hash of an item: the first 64 bits of SHA-1 over its title and text."""
    return hashlib.sha1(f"{title}\0{text}".encode()).hexdigest()[:16]


def sent_hash(db, source, source_id):
    """(the stored hash, the hash it leaves with) for an item, or None when there is none."""
    item = read_item(db, source, source_id)
    if item is None:
        return None
    out = outgoing_record(Record("item", source, source_id, 0, item), db)
    return item["meta"].get("hash"), out.body["meta"].get("hash") if out else None


def outgoing_record(rec, db):
    """`rec` as it may leave this computer, through the chokepoint, or None when an id in it holds a secret. Item text
    is the one field with its own handling: masked as one whole text and cut again only where masking changed it,
    since a store that cut an item before masking it, as kb once did, can split a secret across two chunks
    that neither shows. Its `hash` stays as stored, so every store holds the same item, unless it could be over text from
    before masking: when masking changes the title or the text on the way out, or when the stored hash does not match the
    stored text and that text holds `[masked]`, as after kb's remask. Then it is kb's hash of what leaves, since a hash
    of text before masking lets whoever reads it test guesses at what was masked. A stored hash that does not match its
    text for any other reason, such as a whitespace tail an older kb's chunker dropped, covers nothing secret. A
    summary's `of_hash` names the item it summarizes the same way, read from `db`; one for an older text leaves empty. The rows' `host`, and a supersede's target, leave as they are, since the id rule above has read them."""
    if _ids_hold_secret(rec) or _kb_refuses(db, rec.source, rec.source_id):
        return None
    if rec.kind == "summary":
        return dataclasses.replace(rec, body=_of_hash(egress.clean(rec.body), rec, db))
    if rec.kind == "supersede":
        return dataclasses.replace(rec, body=egress.clean(rec.body, passing=("new",)))
    if rec.kind != "item":
        return dataclasses.replace(rec, body=egress.clean(rec.body))
    whole = "".join(rec.body["rows"])  # as stored, invisible characters included, so the mask sees the one that bounds a key
    hidden = egress.hide(whole)
    rows = [egress.visible(row) for row in rec.body["rows"]]
    masked = hidden != "".join(rows)
    if masked:
        rows = store.chunk(hidden)
    body = {**egress.clean({k: v for k, v in rec.body.items() if k != "rows"}), "rows": rows}
    title, stored = rec.body.get("title", ""), "".join(rec.body["rows"])
    masked_at_rest = "[masked]" in title + stored and rec.body.get("meta", {}).get("hash") != item_hash(title, stored)
    if "".join(rows) != stored or body.get("title") != title or masked_at_rest:
        # Of the rows that leave, whenever they are not the rows stored: masking, leaving out invisible characters and
        # chunking all change them, and a hash of anything else would be taken again on the next pass.
        body["meta"] = {**body.get("meta", {}), "hash": item_hash(body.get("title", ""), "".join(rows))}
    return dataclasses.replace(rec, body=body)


def _of_hash(body, rec, db):
    known = sent_hash(db, rec.source, rec.source_id)
    of = body.get("of_hash")
    sent = "" if known is None or of not in known else known[1]
    return {**body, "of_hash": sent, "rows": [
        {**r, "metadata": {**r["metadata"], "of_hash": sent}} if isinstance(r.get("metadata"), dict) and "of_hash" in r[
            "metadata"] else r for r in body.get("rows", [])]}


def own_record(line):
    """Parse a record line this computer wrote: one it queued, or one of its snapshot. A summary's rows each keep the
    `of_hash` of the text they summarized, and kb's append pass keeps the earlier pass's rows, so the rows of one summary
    can name two texts, as could a summary queued before from_wire checked them. Every row that names an `of_hash`
    names the record's first, as egress sends it."""
    obj = json.loads(line)
    if isinstance(obj, dict) and obj.get("k") == "summary" and isinstance(obj.get("rows"), list):
        obj = {**obj, "rows": [{**r, "metadata": {**r["metadata"], "of_hash": obj.get("of_hash")}}
                               if isinstance(r, dict) and isinstance(r.get("metadata"), dict) and "of_hash" in r["metadata"]
                               else r for r in obj["rows"]]}
    return from_wire(obj)


def outgoing(line, db):
    """A queued record line as it leaves in a pack, or None. It passes the chokepoint as it is signed, so a queue staged
    before any rule here still leaves masked."""
    rec = outgoing_record(own_record(line), db)
    return None if rec is None else to_json(rec)


def read_item(db, source, source_id):
    rows = db.execute("SELECT ts, title, content, metadata FROM docs WHERE source=? AND source_id=? "
                      "AND kind IN ('doc','raw') ORDER BY id", (source, source_id)).fetchall()
    if not rows:
        return None
    meta = json.loads(rows[0][3])
    meta.pop("chunk", None)
    meta.pop("chunks", None)
    return {"ts": rows[0][0], "title": rows[0][1], "meta": meta, "rows": [r[2] for r in rows]}


def _summary_rows(cursor_rows):
    return [{"kind": k, "ts": ts, "title": t, "content": c, "metadata": json.loads(m)} for k, ts, t, c, m in cursor_rows]


def read_summary(db, source, source_id):
    rows = _summary_rows(db.execute("SELECT kind, ts, title, content, metadata FROM docs WHERE source=? AND source_id=? "
                                    "AND kind IN ('segment','atom') ORDER BY id", (source, source_id)))
    return {"of_hash": rows[0]["metadata"].get("of_hash", "") if rows else "", "rows": rows}


def read_mark(db, source, source_id, by):
    row = db.execute("SELECT new_source, new_source_id, at FROM supersedes WHERE source=? AND source_id=? AND marked_by=?",
                     (source, source_id, by)).fetchone()
    return {"by": by, "new": [row[0], row[1]] if row and row[0] is not None else None, "at": row[2] if row else ""}


def _own_digests(db, own):
    out = {}
    for s, i, h, host in db.execute("SELECT source, source_id, json_extract(metadata, '$.hash'), "
                                    "json_extract(metadata, '$.host') FROM docs "
                                    f"WHERE kind IN ('doc','raw') AND {OWNED} GROUP BY source, source_id",
                                    owned_args(own)):
        if not holds_secret(s) and not id_holds_secret(i, host) and not _kb_refuses(db, s, i):
            out[("item", s, i, "")] = h
    grouped = {}
    for s, i, *row in db.execute("SELECT source, source_id, kind, ts, title, content, metadata FROM docs "
                                 f"WHERE kind IN ('segment','atom') AND {OWNED} ORDER BY id", owned_args(own)):
        grouped.setdefault((s, i), []).append(row)
    for (s, i), rows in grouped.items():
        if not holds_secret(s) and not id_holds_secret(i) and not _kb_refuses(db, s, i):
            out[("summary", s, i, "")] = summary_digest(_summary_rows(rows))
    for s, i, ns, ni, at in db.execute("SELECT source, source_id, new_source, new_source_id, at FROM supersedes "
                                       "WHERE marked_by=?", (own,)):
        # Every receiver refuses a record about an item no install owns, and would stall on it.
        if (has_owner(i) and not holds_secret(s, ns) and not id_holds_secret(i) and not id_holds_secret(ni)
                and not _kb_refuses(db, s, i)):
            out[("supersede", s, i, own)] = mark_digest([ns, ni] if ns is not None else None, at)
    return out


_READ = {"item": lambda db, s, i, m: read_item(db, s, i), "summary": lambda db, s, i, m: read_summary(db, s, i),
         "supersede": read_mark}


def changes(db, own, sent_table=state.sent):
    """Every own item, summary and mark that differs from what `sent_table` (sync_sent by default) says went out, as
    (key, record or None, digest or None).

    Read in one transaction. Records carry v=0 until the caller gives them versions.
    """
    db.execute("BEGIN")
    try:
        sent = {k: d for k, d in sent_table(db).items() if (k[3] if k[0] == "supersede" else ids.owner(k[2])) == own}
        now = _own_digests(db, own)
        out = []
        for key, digest in now.items():
            if sent.get(key) != digest:
                lane, s, i, marker = key
                out.append((key, Record(lane, s, i, 0, _READ[lane](db, s, i, marker)), digest))
        for key in sent.keys() - now.keys():
            lane, s, i, marker = key
            if lane == "item":
                out.append((key, Record("gone", s, i, 0, {}), None))
            elif lane == "summary" and ("item", s, i, "") in now:
                out.append((key, Record("summary", s, i, 0, {"of_hash": "", "rows": []}), None))
            elif lane == "supersede":
                out.append((key, Record("supersede", s, i, 0, {"by": marker, "new": None, "at": ""}), None))
            else:
                out.append((key, None, None))  # a summary whose item is gone; the Gone clears it everywhere
    finally:
        db.execute("COMMIT")
    return sorted(out, key=lambda c: KINDS[c[1].kind].order if c[1] else 0)


def stage(db, changed, now_ms):
    """Give each change its version and queue it. Runs inside the caller's transaction."""
    lines = []
    for key, rec, digest in changed:
        if rec is not None:
            rec = dataclasses.replace(rec, v=next_version(state.version(db, key), now_ms))
            state.set_version(db, key, rec.v, gone=rec.kind == "gone")
            if rec.kind == "supersede":
                db.execute("UPDATE supersedes SET v=? WHERE source=? AND source_id=? AND marked_by=?",
                           (rec.v, rec.source, rec.source_id, rec.body["by"]))
            lines.append(to_json(rec))
        if digest is None:
            state.drop_sent(db, key)
        else:
            state.set_sent(db, key, digest)
    state.add_outbox(db, lines)
    return len(lines)


def replica(db):
    """This store's whole synced state, with original versions, for a snapshot."""
    out = []
    for lane, s, i, marker, v, gone in state.versions(db):
        if lane == "item" and gone:
            out.append(Record("gone", s, i, v, {}))
        elif lane == "supersede":
            out.append(Record("supersede", s, i, v, read_mark(db, s, i, marker)))
        elif item_exists(db, s, i):
            out.append(Record(lane, s, i, v, _READ[lane](db, s, i, marker)))
    return out


def _write_item(db, rec):
    """An item's rows as the sender had them: one text is a `doc` row, several are `raw` chunks. kb's store masks them
    with this install's mask and takes the hash of what it stored, which is the sent hash unless masking changed the
    text."""
    b = rec.body
    texts, meta = b["rows"], b["meta"]
    if len(texts) == 1:
        rows = [{"kind": "doc", "ts": b["ts"], "title": b["title"], "content": texts[0], "metadata": meta}]
        kinds = store.ITEM_KINDS + store.SUMMARY_KINDS  # only long items keep summaries
    else:
        # In the order kb's store_item writes a chunk's keys, hash last, so the rows are the sender's text for text.
        base, digest = {k: v for k, v in meta.items() if k != "hash"}, {k: v for k, v in meta.items() if k == "hash"}
        rows = [{"kind": "raw", "ts": b["ts"], "title": b["title"], "content": part,
                 "metadata": {**base, "chunk": i, "chunks": len(texts), **digest}} for i, part in enumerate(texts)]
        kinds = store.ITEM_KINDS
    store.replace_rows(db, rec.source, rec.source_id, rows, kinds)


def _write_summary(db, rec):
    store.replace_rows(db, rec.source, rec.source_id, rec.body["rows"], store.SUMMARY_KINDS)


def _write_mark(db, rec):
    b = rec.body
    if b["new"]:
        store.mark_superseded(db, rec.source, rec.source_id, b["new"], b["by"], b["at"], rec.v)
    else:
        store.clear_superseded(db, rec.source, rec.source_id, b["by"], b["at"], rec.v)


_WRITE = {"item": _write_item, "gone": lambda db, rec: store.delete_item(db, rec.source, rec.source_id),
          "summary": _write_summary, "supersede": _write_mark}


def _note_sent(db, rec):
    """A rebuilt store fills sync_sent from its own records, so its next push sends only real changes."""
    if rec.kind == "item":
        state.set_sent(db, rec.key, rec.body["meta"]["hash"])
    elif rec.kind == "gone":
        state.drop_sent(db, rec.key)
        state.drop_sent(db, ("summary", rec.source, rec.source_id, ""))
    elif rec.kind == "summary" and rec.body["rows"]:
        state.set_sent(db, rec.key, summary_digest(rec.body["rows"]))
    elif rec.kind == "supersede":
        state.set_sent(db, rec.key, mark_digest(rec.body["new"], rec.body["at"]))
    else:
        state.drop_sent(db, rec.key)


def _apply_one(db, rec, own, adopting, cleared, write):
    """Returns 1 when the record is skipped for an id that holds a secret, else 0."""
    if rec.kind == "snapshot" or rec.writer in cleared:
        return 0
    if _ids_hold_secret(rec):  # sent by an older client, this one's own included; kb no longer stores such an item
        return 1
    mine = rec.writer == own
    if mine and not adopting:
        state.raise_version(db, rec.key, rec.v, rec.kind == "gone")
        return 0
    have = state.version(db, rec.key)
    if have and rec.v <= have[0]:
        return 0
    if rec.kind in ("summary", "supersede") and not item_exists(db, rec.source, rec.source_id):
        if ids.owner(rec.source_id) not in cleared:  # a cleared item never arrives, so the record would wait forever
            state.park(db, rec.key, rec.v, to_json(rec))
        return 0
    db.execute("SAVEPOINT apply_one")
    try:
        write[rec.kind](db, rec)
    except ValueError:  # kb's store refuses an item whose id looks like it holds a secret
        db.execute("ROLLBACK TO apply_one")  # so a refusal after the write began, such as one deleting first, keeps nothing
        db.execute("RELEASE apply_one")
        return 1
    db.execute("RELEASE apply_one")
    state.set_version(db, rec.key, rec.v, gone=rec.kind == "gone")
    if mine:
        _note_sent(db, rec)
    return 0


def apply(db, recs, own, cleared=frozenset(), adopting=False, write=_WRITE):
    """Write records into a store: Item and Gone, then Summary, then Supersede, each only when newer.

    Skips `Item`, `Gone` and `Summary` under `own` and marks `own` made, unless `adopting` (a new or rebuilt store).
    Skips every record written by an install in `cleared`. `write` maps each kind to the function that writes it into
    this kind of store. Runs inside the caller's transaction. Returns how many records were skipped because an id in
    them holds a secret; the ids themselves are never kept or shown.
    """
    skipped = 0
    for rec in sorted(recs, key=lambda r: KINDS[r.kind].order):
        skipped += _apply_one(db, rec, own, adopting, cleared, write)
    for key, line in state.parked(db):
        rec = from_wire(json.loads(line))
        item = state.version(db, ("item", rec.source, rec.source_id, ""))
        if item and item[1]:
            state.unpark(db, key)
        elif item_exists(db, rec.source, rec.source_id):
            state.unpark(db, key)
            skipped += _apply_one(db, rec, own, adopting, cleared, write)
    return skipped


def drop_lacking(db, own, present, keep=frozenset()):
    """After a snapshot: delete every item another install owns that the snapshot does not hold, live or tombstoned,
    except the items of the installs in `keep`. An item no install owns never syncs, so no snapshot holds it, and it
    stays."""
    for s, i in db.execute(f"SELECT DISTINCT source, source_id FROM docs WHERE NOT {OWNED}", owned_args(own)).fetchall():
        if (s, i) not in present and has_owner(i) and ids.owner(i) not in keep:
            store.delete_item(db, s, i)


def count_items(db, install):
    return db.execute(f"SELECT count(*) FROM (SELECT DISTINCT source, source_id FROM docs WHERE kind IN ('doc','raw') "
                      f"AND {OWNED})", owned_args(install)).fetchone()[0]


def clear(db, install):
    """Delete everything `install` wrote: its items with their summaries, every mark it made, and the versions, parked
    records and sent digests that track them. Marks other computers made on its items stay. Runs inside the caller's
    transaction."""
    args = owned_args(install)
    for s, i in db.execute(f"SELECT DISTINCT source, source_id FROM docs WHERE {OWNED}", args).fetchall():
        store.delete_item(db, s, i)
    db.execute("DELETE FROM supersedes WHERE marked_by=?", (install,))
    db.execute(f"DELETE FROM sync_versions WHERE (lane != 'supersede' AND {OWNED}) OR marker=?", (*args, install))
    db.execute(f"DELETE FROM sync_parked WHERE {OWNED} OR marker=?", (*args, install))
    db.execute(f"DELETE FROM sync_sent WHERE (lane != 'supersede' AND {OWNED}) OR marker=?", (*args, install))
