"""The signed device list. Each change is a link; a computer accepts a link only when it follows the last one it trusts.

link  = {version, epoch, prev_hash, signer, entries, keys}
entry = {device_id, prefix, age_recipient, ssh_key, label, added_in,
         removed: {in_version, head_dseq, head_hash, clear} or none}
"""
import hashlib
import re

from . import keys, pack
from .. import ids
from ..mask import mask

LINK_KEYS = {"version", "epoch", "prev_hash", "signer", "entries", "keys"}
_HEX64 = re.compile(r"[0-9a-f]{64}")
ENTRY_KEYS = {"device_id", "prefix", "age_recipient", "ssh_key", "label", "added_in", "removed"}
REMOVED_KEYS = {"in_version", "head_dseq", "head_hash", "clear"}
FIXED = ("prefix", "age_recipient", "ssh_key", "label", "added_in")
# A label is shown and stored as is, so it may not hold a source_id or a phone number.
_PRIVATE_IN_LABEL = re.compile(r"c-[a-z2-7]{13}[:@]|\+?[0-9](?:[ ().-]?[0-9]){6,}")


def link_hash(link):
    return hashlib.sha256(pack.canonical(link)).hexdigest()


def _int(x, low):
    return isinstance(x, int) and not isinstance(x, bool) and x >= low


def valid_label(label):
    """A label is printable, short, and holds no code, source_id or phone number and nothing `kb.mask.mask` would hide,
    so it can be shown and stored as is."""
    return (isinstance(label, str) and 1 <= len(label) <= 64 and label.isprintable() and mask(label) == label
            and not keys.code_spans(label) and not _PRIVATE_IN_LABEL.search(label))


def check_entry(e):
    ok = (isinstance(e, dict) and set(e) == ENTRY_KEYS and isinstance(e["device_id"], str)
          and keys.DEVICE_ID.fullmatch(e["device_id"]) and isinstance(e["prefix"], str)
          and ids.INSTALL_ID.fullmatch(e["prefix"]) and isinstance(e["age_recipient"], str)
          and keys.AGE_RECIPIENT.fullmatch(e["age_recipient"]) and isinstance(e["ssh_key"], str)
          and keys.SSH_KEY.fullmatch(e["ssh_key"]) and valid_label(e["label"]) and _int(e["added_in"], 1))
    r = e.get("removed") if ok else None
    if ok and r is not None:
        ok = (isinstance(r, dict) and set(r) == REMOVED_KEYS and _int(r["in_version"], 2) and _int(r["head_dseq"], 0)
              and isinstance(r["head_hash"], str) and isinstance(r["clear"], bool))
    if not ok:
        raise keys.Refused("bad roster entry")
    return e


def check_link(link):
    ok = (isinstance(link, dict) and set(link) == LINK_KEYS and _int(link["version"], 1) and _int(link["epoch"], 1)
          and isinstance(link["prev_hash"], str) and isinstance(link["signer"], str)
          and isinstance(link["entries"], list) and link["entries"]
          and isinstance(link["keys"], str) and _HEX64.fullmatch(link["keys"]))
    if not ok:
        raise keys.Refused("bad roster link")
    for e in link["entries"]:
        check_entry(e)
    if len({e["device_id"] for e in link["entries"]}) != len(link["entries"]):
        raise keys.Refused("roster lists a device twice")
    active = [e["prefix"] for e in link["entries"] if e["removed"] is None]
    if len(set(active)) != len(active):
        raise keys.Refused("roster lists two active devices under one install id")
    cleared = _cleared(link["entries"])
    if any(e["removed"] is None and e["prefix"] in cleared for e in link["entries"]):
        raise keys.Refused("roster lists an active device under an install id whose items were cleared")
    return link


def _cleared(entries):
    return frozenset(e["prefix"] for e in entries if e["removed"] and e["removed"]["clear"])


class Roster:
    """The newest link this computer trusts."""

    def __init__(self, link):
        self.link = link
        self.hash = link_hash(link)

    version = property(lambda self: self.link["version"])
    epoch = property(lambda self: self.link["epoch"])
    entries = property(lambda self: self.link["entries"])

    def entry(self, device_id):
        return next((e for e in self.entries if e["device_id"] == device_id), None)

    def active(self):
        return [e for e in self.entries if e["removed"] is None]

    def accept(self, link, body, sig):
        """The next link, if it follows this one and an active member signed it. Raises Refused otherwise."""
        check_link(link)
        if link["version"] != self.version + 1 or link["prev_hash"] != self.hash:
            raise keys.Refused("roster link does not follow the trusted one")
        signer = self.entry(link["signer"])
        if signer is None or signer["removed"] is not None:
            raise keys.Refused("roster link signer is not an active member")
        if not keys.verify(signer["device_id"], signer["ssh_key"], pack.NS_ROSTER, body, sig):
            raise keys.Refused("roster link signature does not verify")
        rotated = link["epoch"] == self.epoch + 1
        if not rotated and link["epoch"] != self.epoch:
            raise keys.Refused("roster epoch jumps")
        new = {e["device_id"]: e for e in link["entries"]}
        for old in self.entries:
            e = new.pop(old["device_id"], None)
            if e is None or any(e[k] != old[k] for k in FIXED):
                raise keys.Refused("roster link drops or rewrites an entry")
            if e["removed"] != old["removed"] and (old["removed"] is not None or not rotated
                                                   or e["removed"]["in_version"] != link["version"]):
                raise keys.Refused("roster link changes a removal")
        if any(e["added_in"] != link["version"] or e["removed"] is not None for e in new.values()):
            raise keys.Refused("roster link adds a bad entry")
        return Roster(link)


def new_link(prev, epoch, signer, entries, ring):
    """The next link, naming the keys of `ring`, the key ring it is trusted with."""
    return {"version": prev.version + 1 if prev else 1, "epoch": epoch, "prev_hash": prev.hash if prev else "",
            "signer": signer, "entries": entries, "keys": ring_commitment(ring.upto(epoch))}


def ring_commitment(ring):
    """A hash of every public key in `ring`, one per epoch, with the recovery recipient, the recovery tag key and the
    roster version the recovery code was made at. Every link names it for the key ring it is trusted with, so a key ring
    that an envelope, a welcome or a recovery envelope brings counts only with exactly the keys a trusted link names,
    whoever sealed it, and the hash gives away no key."""
    rec = ring.recovery
    body = {"epochs": {str(e): r for e, (_, r) in sorted(ring.epochs.items())}, "recovery": rec["recipient"],
            "tag": rec["tag"].hex(), "since": rec["since"]}
    return hashlib.sha256(b"sync-keys\0" + pack.canonical(body)).hexdigest()


def seal_link(link, ssh_path, recipient):
    return pack.seal_signed(pack.LINK, link, ssh_path, pack.NS_ROSTER, recipient)


def open_link(blob, ring):
    """(link, signed body, signature). Tries every epoch key, since the relay does not say which one sealed it."""
    link, body, sig = pack.open_signed(pack.LINK, keys.open_with([i for i, _ in ring.epochs.values()], blob))
    return check_link(link), body, sig


def seal_envelope(ring, roster, entry, signer, ssh_path):
    payload = {"for": entry["device_id"], "signer": signer, "keyring": ring.to_json(),
               "roster": {"version": roster.version, "hash": roster.hash}}
    return pack.seal_signed(pack.ENVELOPE, payload, ssh_path, pack.NS_ENVELOPE, entry["age_recipient"])


def open_envelope(blob, device_age):
    payload, body, sig = pack.open_signed(pack.ENVELOPE, keys.open_file(device_age, blob))
    if not isinstance(payload, dict):
        raise keys.Refused("bad envelope")
    return payload, body, sig


def trust_envelope(roster, opened, device_id):
    """(key ring, roster version it names) from an envelope whose signer is an active member of `roster`."""
    payload, body, sig = opened
    if not isinstance(payload, dict) or payload.get("for") != device_id:
        raise keys.Refused("envelope is not for this computer")
    signer = roster.entry(payload.get("signer"))
    if signer is None or signer["removed"] is not None:
        raise keys.Refused("envelope signer is not an active member")
    if not keys.verify(signer["device_id"], signer["ssh_key"], pack.NS_ENVELOPE, body, sig):
        raise keys.Refused("envelope signature does not verify")
    head = payload.get("roster") or {}
    if not isinstance(head.get("version"), int) or head["version"] > roster.version:
        raise keys.Refused("envelope names a roster this computer cannot see")
    if head["version"] == roster.version and head.get("hash") != roster.hash:
        raise keys.Refused("envelope names a forked roster")
    return keys.KeyRing.from_json(payload["keyring"]), head["version"]
