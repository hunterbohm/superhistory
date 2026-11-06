"""Byte formats: packs, signed small blobs (roster links and envelopes), and tagged blobs (pairing and recovery).

plaintext = magic | u32 length | field | ... | u32 signature length | signature | zero padding
"""
import dataclasses
import hashlib
import json
import re
import struct
import zlib

from . import keys, records

MAGIC = b"VSP1"
LINK = b"VSR1"
ENVELOPE = b"VSE1"
NS_PACK = "varve-sync-pack"
NS_ROSTER = "varve-sync-roster"
NS_ENVELOPE = "varve-sync-envelope"
MAX_RECORDS = 256 << 20
HEADER_KEYS = {"epoch", "author", "dseq", "prev", "roster_version", "roster_hash", "snapshot"}
SNAPSHOT_KEYS = {"id", "through", "part", "parts", "heads"}
_HEX64 = re.compile(r"[0-9a-f]{64}")


def bucket(n):
    """Pack sizes the relay may see: powers of two from 16 KiB to 1 MiB, then whole MiB."""
    size = 16 << 10
    while size < n and size < (1 << 20):
        size <<= 1
    return size if n <= size else -(-n // (1 << 20)) * (1 << 20)


def small_bucket(n):
    return max(1, -(-n // 4096)) * 4096


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _lp(data):
    return struct.pack(">I", len(data)) + data


def frame(magic, fields, ssh_path, namespace):
    body = magic + b"".join(_lp(f) for f in fields)
    return body + _lp(keys.sign(ssh_path, namespace, body))


def unframe(magic, data, nfields):
    """Split a padded plaintext into (fields, signed body, signature, unpadded plaintext). Strict about every byte."""
    if not data.startswith(magic):
        raise keys.Refused("wrong magic")
    pos, parts = len(magic), []
    for _ in range(nfields + 1):
        if pos + 4 > len(data):
            raise keys.Refused("cut off")
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        if pos + 4 + n > len(data):
            raise keys.Refused("cut off")
        parts.append((pos, data[pos + 4:pos + 4 + n]))
        pos += 4 + n
    if data[pos:] != bytes(len(data) - pos):
        raise keys.Refused("padding is not zero")
    sig_at, sig = parts[-1]
    return [p for _, p in parts[:-1]], data[:sig_at], sig, data[:pos]


def pad(plain, size_of):
    return plain + bytes(size_of(len(plain)) - len(plain))


@dataclasses.dataclass
class Pack:
    header: dict
    records: list  # None until the signature verifies, for a pack parsed with `inflate=False`
    body: bytes
    sig: bytes
    plain: bytes
    packed: bytes = b""  # the records, still compressed

    @property
    def hash(self):
        return hashlib.sha256(self.plain).hexdigest()

    @property
    def is_part(self):
        return self.header["snapshot"] is not None


def build(header, record_lines, ssh_path):
    """The signed, unpadded plaintext of one pack."""
    return frame(MAGIC, [canonical(header), zlib.compress("\n".join(record_lines).encode(), 6)], ssh_path, NS_PACK)


def seal(plain, recipient):
    return keys.seal([recipient], pad(plain, bucket))


def _int(x, low):
    return isinstance(x, int) and not isinstance(x, bool) and x >= low


def _check_header(h):
    if not isinstance(h, dict) or set(h) != HEADER_KEYS:
        raise keys.Refused("bad pack header")
    ok = (_int(h["epoch"], 1) and isinstance(h["author"], str) and keys.DEVICE_ID.fullmatch(h["author"])
          and _int(h["roster_version"], 1) and isinstance(h["roster_hash"], str) and _HEX64.fullmatch(h["roster_hash"]))
    snap = h["snapshot"]
    if snap is None:
        ok = ok and _int(h["dseq"], 1) and isinstance(h["prev"], str) and (h["prev"] == "" or _HEX64.fullmatch(h["prev"]))
    else:
        ok = (ok and h["dseq"] is None and h["prev"] is None and isinstance(snap, dict) and set(snap) == SNAPSHOT_KEYS
              and isinstance(snap["id"], str) and _int(snap["through"], 0) and _int(snap["parts"], 1)
              and _int(snap["part"], 0) and snap["part"] < snap["parts"] and isinstance(snap["heads"], dict)
              and all(keys.DEVICE_ID.fullmatch(a) and isinstance(v, list) and len(v) == 2 and _int(v[0], 0)
                      and isinstance(v[1], str) for a, v in snap["heads"].items()))
    if not ok:
        raise keys.Refused("bad pack header")


def parse(padded, inflate=True):
    """Parse an opened pack. Checks framing and shape, not the signature or the chain. With `inflate` false, as for any
    pack from the relay, the records stay compressed until `verify` passes, so a pack that a removed computer sealed
    under a key it still holds is refused before up to MAX_RECORDS is inflated and parsed."""
    (hb, rb), body, sig, plain = unframe(MAGIC, padded, 2)
    try:
        header = json.loads(hb)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise keys.Refused("bad pack header") from None
    _check_header(header)
    return Pack(header, _inflate(rb) if inflate else None, body, sig, plain, rb)


def _inflate(rb):
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(rb, MAX_RECORDS)
    except zlib.error:
        raise keys.Refused("records do not inflate") from None
    if inflater.unconsumed_tail or not inflater.eof:
        raise keys.Refused("records are cut off or too large")
    try:
        return records.decode(raw)
    except records.BadRecord as exc:
        raise keys.Refused(f"bad record: {exc}") from None


def verify(pk, entry):
    """Whether `entry` signed the pack. Only then are its records inflated, if they were not yet."""
    if not keys.verify(entry["device_id"], entry["ssh_key"], NS_PACK, pk.body, pk.sig):
        return False
    if pk.records is None:
        pk.records = _inflate(pk.packed)
    return True


def manifest(part_plains):
    return hashlib.sha256(b"".join(part_plains)).hexdigest()


def seal_signed(magic, obj, ssh_path, namespace, recipient):
    return keys.seal([recipient], pad(frame(magic, [canonical(obj)], ssh_path, namespace), small_bucket))


def open_signed(magic, padded):
    """(object, signed body, signature) from an opened small blob."""
    (raw,), body, sig, _ = unframe(magic, padded, 1)
    try:
        obj = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise keys.Refused("bad signed blob") from None
    if canonical(obj) != raw:
        raise keys.Refused("signed blob is not canonical")
    return obj, body, sig


def seal_tagged(tag_key, recipient, obj, aad=b""):
    sealed = keys.seal([recipient], pad(canonical(obj), small_bucket))
    return keys.tag(tag_key, aad + sealed) + sealed


def open_tagged(tag_key, blob, aad=b"", identity=None, identity_file=None):
    """Check the tag before anything reaches `age`, then open and parse."""
    mac, sealed = blob[:32], blob[32:]
    if len(blob) <= 32 or not keys.tag_ok(tag_key, aad + sealed, mac):
        raise keys.Refused("tag does not match")
    plain = keys.open_with([identity], sealed) if identity else keys.open_file(identity_file, sealed)
    try:
        return json.loads(plain.rstrip(b"\0"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise keys.Refused("bad tagged blob") from None
