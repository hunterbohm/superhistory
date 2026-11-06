"""SyncClient: one computer's side of Superhistory Sync. docs/design.md, "Sync protocol", is the specification."""
import contextlib
import dataclasses
import fcntl
import functools
import hashlib
import logging
import os
import struct
import subprocess
import tempfile
import time
import uuid

from . import keys, pack, records, relay, roster, state
from .. import ids, store
from .keys import Refused
from .login import NotSignedIn, Paths, approve, signed_in

DAY_MS = 86_400_000
PACK_BUDGET = 2 << 20
# With no snapshot yet, "twice the snapshot" is zero; wait for this much before the first size-driven compaction.
COMPACT_FLOOR = 1 << 20
# Packs past the snapshot that make a compaction due whatever their size. A pack is at least 16 KiB, so twice the
# snapshot alone would let a store with a large snapshot and small changes collect more packs than a pull may go past
# (MAX_PASSED) before it compacts.
COMPACT_PACKS = 10_000
PAGE = 200
MAX_RESIGNS = 2
# How many times sealing will rotate to catch a removal that landed during the previous rotation before it gives up and
# seals nothing. One rotation is the normal case; more only when a revocation keeps arriving mid-rotation.
MAX_REMOVAL_ROUNDS = 8
# How long a command waits for another sync on the same store to finish. The scheduled run waits only briefly and then
# leaves the work to its next run; a command the person ran waits longer and then says why it stopped.
SYNC_LOCK_WAIT = 120.0
HOOK_LOCK_WAIT = 10.0
# What one pull may go past without applying, counted across all its passes over the relay's listing, so no pass starts
# a count over. Pull uses a pack only by applying it, which needs a valid signature and a step forward in its author's
# chain. Everything else it and the two passes that read ahead (for a removed computer's chain and for a snapshot's
# announcement) go past is counted in one of two ways:
# - A change pack a computer in the roster signed, at a later place in its chain than this computer applied and than
#   this pass has seen, counts toward MAX_SIGNED_PASSED. A relay can list one only as often as a computer signed one,
#   so this bounds a removed computer's packs, which a pull must read through however many it sent, and not what the
#   relay can repeat. It is checked at about 6 ms a pack, so this many takes under two hours, and a removed computer
#   would have to have sent about 16 GiB of packs past the last compaction to reach it.
# - Anything else counts toward MAX_PASSED: a snapshot part, a pack listed again, one not signed by its author, or one
#   of a computer this computer does not know yet. A relay can list those again for nothing. An honest relay lists no
#   more than the current snapshot's parts (one per PACK_BUDGET of it) and the few packs a computer sent since this
#   computer last applied its chain.
# Past either, the pull stops.
MAX_PASSED = 100_000
MAX_SIGNED_PASSED = 1_000_000

log = logging.getLogger(__name__)


class SyncError(Exception):
    """Something the person has to act on."""


def missing_notice(label):
    """What `superhistory sync` and `superhistory sync status` say about a removed computer whose confirmed packs never arrived."""
    return (f"Packs that {label} sent before it was removed have not arrived here. If {label} was lost or stolen, its "
            "key may have been used to claim packs it never sent, and nothing is missing. If not, the relay is holding "
            "them back: a computer that has them adds them to its next snapshot, and they arrive here after that.")


class _OverLimit(Refused):
    """A pull went past more than MAX_PASSED or MAX_SIGNED_PASSED allows."""


class Busy(SyncError):
    """Another sync is running on this store."""


@dataclasses.dataclass
class Pairing:
    code: str
    secret: bytes


@dataclasses.dataclass
class Removal:
    cleared: bool
    recovery_code_due: bool


@dataclasses.dataclass
class Report:
    received: int = 0
    sent: int = 0
    compacted: bool = False
    stalled: object = None
    skipped: int = 0
    busy: bool = False  # another sync held this store for the whole wait, so this run did nothing
    # Removed computers whose confirmed packs neither the relay nor its snapshot serves: kept back, or made up by a
    # stolen token. Named on every sync rather than stopping it, since a made-up one could never be served.
    missing: tuple = ()


@dataclasses.dataclass(frozen=True)
class Proven:
    """A roster chain `_verified_walk` proved: its start's hash matches a head a removed computer cannot forge (the
    account's roster head the app holds, or this computer's own signed envelope) and every link after it verified under
    `Roster.accept`. Only that walk builds one, so `_trust`, the only place that saves keys, clears a removed computer's
    items and confirms the roster to the app, cannot run on a link this computer has not proven from the account head.
    Passing a raw `(ring, roster)` to `_trust` is a type error, not merely wrong."""
    ring: object      # keys.KeyRing, up to the roster's epoch
    roster: object    # roster.Roster at the account head, walked forward from the trusted start
    hashes: dict      # {version: hash} seen from the start through the newest accepted link

    @classmethod
    def authored(cls, ring, ro):
        """A link this computer built on its own trusted roster and published to the relay (init, approve, rotate,
        recover). This computer signed it, so it needs no walk; it is never an external link."""
        return cls(ring, ro, {ro.version: ro.hash})


DEVICE_KEYS = ("prefix", "age_recipient", "ssh_key")
JOIN_KEYS = ("device_id", "prefix", "age_recipient", "ssh_key", "label", "accounts")


@dataclasses.dataclass(frozen=True)
class Frozen:
    """Where a removed device's packs stop counting. None past `bound`, the head the trusted roster pinned when it
    removed the device (None while only the app has revoked it). Below that, a pack counts when the device's chain runs
    through it to `pin`, that pinned head, or to a pack the device itself confirmed to the app while it was a member.
    `clear` is the choice the app recorded when it revoked the device, asked after the pack was fetched: True deletes
    what it read, so none of its records applies; None means the app recorded none, so none of its packs applies and
    sync stalls until it does."""
    bound: object  # int or None
    pin: object  # (dseq, hash) or None
    clear: object  # True, False or None


@dataclasses.dataclass(frozen=True)
class Removed:
    """Install ids of the computers this computer treats as removed, by what happens to what they wrote. `cleared` never
    applies and is deleted. `undecided`, removed by the app with no recorded choice, neither applies nor is deleted."""
    cleared: frozenset
    undecided: frozenset


class Vouch:
    """Whether a device's signature counts, by the app's word, asked after the artifact it judges was fetched. Pull, a
    snapshot, the roster walk and the link a welcome or envelope names all ask this, and nothing else decides it.

    A device's signature counts when the app lists the device and has not revoked it. A revocation is never undone, so
    whatever was fetched before the app said so was signed while the device was a member. After that, a roster link it
    signed counts only up to a link some device confirmed while it was a member, and a pack it signed only as `Frozen`
    says."""

    def __init__(self, me, roster, devices):
        """`devices` lists the account's devices. It is called at most once, when a signature first needs it."""
        self.me, self.roster, self._devices, self._listed = me, roster, devices, None

    def _device(self, signer):
        if self._listed is None:
            self._listed = {d["device_id"]: d for d in self._devices()}
        listed = self._listed.get(signer)
        if listed is None:
            raise Refused("signed by a computer the account does not list")
        return listed

    def revoked(self, signer):
        """Whether the app has revoked `signer`. A device the account does not list is refused."""
        return signer != self.me and bool(self._device(signer)["revoked_on"])

    def frozen(self, signer):
        """None when every pack `signer` signed counts, else where its packs stop counting. The app alone says a device
        is revoked and what happens to what it read: a signer the app has not revoked counts in full, whatever a roster
        link claims, and `clear` is the app's recorded choice, never a link's. A removal link only refines where a revoked
        signer's packs stop, and its own signature still bounds them."""
        if not self.revoked(signer):
            return None
        clear = self._device(signer)["revoked_clear"]
        entry = self.roster.entry(signer) if self.roster else None
        if entry and entry["removed"]:
            removed = entry["removed"]
            return Frozen(removed["head_dseq"], (removed["head_dseq"], removed["head_hash"]), clear)
        return Frozen(None, None, clear)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _recovery_aad(epoch, version):
    return struct.pack(">QQ", epoch, version)


def _quoted(labels):
    return " and ".join(f'"{label}"' for label in labels)


def _signed_in(paths):
    try:
        return signed_in(paths)
    except NotSignedIn as exc:
        raise SyncError(str(exc)) from None


def _locked(method):
    """Run `method` holding the store for sync, waiting up to SYNC_LOCK_WAIT for another sync on it to finish."""
    @functools.wraps(method)
    def run(self, *args, **kwargs):
        with self._exclusive(SYNC_LOCK_WAIT):
            return method(self, *args, **kwargs)
    return run


class SyncClient:
    def __init__(self, paths, db, app, relay_api, login, prefix, clock, accounts=None):
        self.paths, self.db, self.app, self.relay, self.clock = paths, db, app, relay_api, clock
        self.accounts = accounts or {}  # {source name: account key}, from kb's config (`Config.account_keys`)
        self.device_id, self.label = login["device_id"], login["label"]
        self.app_token = login["app_token"]  # the relay gets only the sync token
        self.app_url = login["app"]
        self.prefix = prefix
        ring = keys.load_json(paths.keyring)
        saved = keys.load_json(paths.roster)
        self.keyring = keys.KeyRing.from_json(ring) if ring else None
        self.roster = roster.Roster(saved["link"]) if saved else None
        self.hashes = {int(v): h for v, h in saved["hashes"].items()} if saved else {}
        self._joining = None
        path = db.execute("PRAGMA database_list").fetchone()[2]
        self._lock_path = (path or os.fspath(paths.db)) + ".sync-lock"
        self._lock_depth = 0
        self._passed = self._signed_passed = 0  # what the current pull went past, as `_passing` counts it

    @contextlib.contextmanager
    def _exclusive(self, wait):
        """Hold this store for sync, so no two syncs on it ever overlap: an OS lock on a file beside the store, which the
        system drops when the process ends, however it ends, so a crash never leaves the store held. A method holding it
        may call another that takes it, without waiting. Raises Busy when another process still holds it after `wait`
        seconds."""
        if self._lock_depth:
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            deadline = time.monotonic() + wait
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise Busy("Another sync is running on this store. Try again when it finishes.") from None
                    time.sleep(0.1)
            self._lock_depth = 1
            try:
                yield
            finally:
                self._lock_depth = 0
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    @classmethod
    def load(cls, kb_paths, transport=None, clock=time.time, accounts=None):
        """A client for the signed-in computer whose paths are `kb_paths` (kb.paths.Paths). It works in kb's own
        database, which it opens in autocommit since it opens its own transactions; a database that needs
        `rebuild` raises store.NeedsRebuild. `accounts` is `Config.account_keys()`: the online accounts this computer
        reads."""
        paths = Paths(kb_paths)
        login = _signed_in(paths)
        app_t = transport or relay.HttpTransport(login["app"])
        relay_t = transport or relay.HttpTransport(login["relay"])
        db = store.connect(kb_paths.db)
        db.isolation_level = None
        state.init(db)
        return cls(paths, db, relay.App(app_t), relay.Relay(relay_t, login["sync_token"]), login, ids.install_id(kb_paths),
                   clock, accounts)

    def close(self):
        self.db.close()

    def _now_ms(self):
        return int(self.clock() * 1000)

    def _require_keys(self):
        """`superhistory sync login` makes the device keys and registers them with the app. Keys made anywhere else would match nothing
        the app holds, so every other computer would refuse this one."""
        if not (os.path.exists(self.paths.device_age) and os.path.exists(self.paths.device_ssh)):
            raise SyncError("This computer's keys are missing. Run `superhistory sync login` again.")

    def _my_entry(self, added_in):
        return roster.check_entry({
            "device_id": self.device_id, "prefix": self.prefix, "age_recipient": keys.device_recipient(self.paths.device_age),
            "ssh_key": keys.ssh_public(self.paths.device_ssh), "label": self.label, "added_in": added_in,
            "accounts": roster.claims(self.accounts), "removed": None})

    def _trust(self, proven):
        """Save the key ring and trust the roster of a chain `_verified_walk` proved, confirm it to the app, and clear
        what a removed computer cleared. `proven` is a Proven only that walk builds, so none of these side effects lands
        on a link this computer has not proven from the account head.

        An epoch key counts only once the proven link sets its epoch, and a key ring only with exactly the keys that
        link names: every epoch's public key and the recovery key. Each secret key new to this computer must be the one
        its public key belongs to. So an envelope, a welcome or a recovery envelope brings only keys the proven link
        names, whoever sealed it."""
        ring, ro = proven.ring.upto(proven.roster.epoch), proven.roster
        if roster.ring_commitment(ring) != ro.link["keys"]:
            raise Refused("the key ring is not the one the roster names")
        held = self.keyring.epochs if self.keyring else {}
        if any(held.get(e) != pair and not keys.is_pair(*pair) for e, pair in ring.epochs.items()):
            raise Refused("a key in the key ring is not the one its public key names")
        self._confirm_with_app(ro)
        self.keyring, self.roster = ring, ro
        self.hashes.update(proven.hashes)
        self.hashes[ro.version] = ro.hash
        self.paths.make()
        keys.save_json(self.paths.keyring, ring.to_json())
        keys.save_json(self.paths.roster, {"link": ro.link, "hashes": {str(v): h for v, h in sorted(self.hashes.items())}})
        self._clear_removed()
        self._confirm_roster()

    def _confirm_roster(self):
        """Tell the app which roster link this computer trusts, so that a change a removed computer signs later is past
        it. A fork is refused. When the app cannot be reached, the next refresh tells it."""
        head = [self.roster.version, self.roster.hash]
        if state.get(self.db, "confirmed_roster") == head:
            return
        try:
            self.app.confirm_roster(self.app_token, *head)
        except relay.RelayError as exc:
            if exc.status == 409:
                raise Refused("this computer's roster is not the one the account lists") from None
            if exc.status < 500:
                raise
            return
        except relay.TransportError:
            return
        state.put(self.db, "confirmed_roster", head)

    def _confirm_with_app(self, ro):
        """The app, not the relay, says which computers the account has, under which keys, and which it removed. A roster
        that adds a computer the app does not list with the same install id and keys, or removes one the app has not
        revoked, is refused. The choice about a removed computer's items is never read from the link here: the app's
        `revoked_clear` alone decides it, in `_removals`, so a link's own `clear` changes nothing and needs no check.
        So a roster forked by a removed computer cannot bring in a device under keys it chose, or take or bring back
        another computer. `superhistory sync login` registers a computer's keys before it joins, and a real removal revokes first. Only
        what changed since the trusted roster is checked, and not this computer's own removal, which never deletes its
        own items. A computer the link lists as active that the app has revoked is trusted only as history: `_removals`
        treats it as removed."""
        old = {e["device_id"]: e for e in self.roster.entries} if self.roster else {}
        added = [e for e in ro.entries if e["device_id"] not in old]
        removed = {e["device_id"] for e in ro.entries
                   if e["removed"] and not (old.get(e["device_id"]) or {}).get("removed")
                   and e["device_id"] != self.device_id}
        if not added and not removed:
            return
        listed = {d["device_id"]: d for d in self.app.devices(self.app_token)}
        vouched = all(e["device_id"] in listed and all(listed[e["device_id"]].get(k) == e[k] for k in DEVICE_KEYS)
                      for e in added)
        revoked = all((listed.get(d) or {}).get("revoked_on") for d in removed)
        if not vouched or not revoked:
            raise Refused("the roster does not match the computers on the account")

    def _removals(self, listed):
        """{device_id: clear} for every computer the app has revoked, other than this one, with the choice the app
        recorded when it revoked it, None when it recorded none. The app alone decides this: a computer is removed
        because the app revoked it, not because a roster link, a snapshot or a pack says so, and the choice is the app's
        `revoked_clear`, never a roster link's `clear`. So a roster a relay rolled back below a removal, a removal a relay
        hides, a device a served roster never listed, or a link carrying a choice the person never made all change
        nothing here. None means stall: the device's items are neither applied nor deleted until the app records a
        choice."""
        return {d: dev["revoked_clear"] for d, dev in listed.items()
                if dev["revoked_on"] and d != self.device_id}

    def _removed(self, listed=None):
        """Install ids of the removed computers, as `_removals` says, by what happens to what they wrote. Keyed off the
        app's device list, so a computer the served roster never listed is still covered."""
        listed = self._listed() if listed is None else listed
        removals = self._removals(listed)
        return Removed(frozenset(listed[d]["prefix"] for d, clear in removals.items() if clear),
                       frozenset(listed[d]["prefix"] for d, clear in removals.items() if clear is None))

    def _cleared_here(self):
        """Install ids whose items this computer has deleted, written in the same transaction as the delete. Read where
        records apply, it is never older than a delete, even one a roster change trusted partway through a pull made.
        Nothing such a computer wrote applies again, and no record waits for one of its items."""
        return frozenset(state.get(self.db, "cleared", []))

    def _clear_removed(self):
        """Delete what each computer removed with its items cleared wrote, as `_removals` says, once per store. Never
        this computer's own items. Returns the `Removed` it acted on."""
        removed = self._removed()
        if removed.cleared - self._cleared_here() - {self.prefix}:
            with state.tx(self.db):
                done = self._cleared_here()
                for install in sorted(removed.cleared - done - {self.prefix}):
                    records.clear(self.db, install)
                state.put(self.db, "cleared", sorted(done | (removed.cleared - {self.prefix})))
        return removed

    def _recovery_envelope(self, ring, ro):
        body = {"keyring": ring.to_json(), "roster": {"version": ro.version, "hash": ro.hash}}
        return pack.seal_tagged(ring.recovery["tag"], ring.recovery["recipient"], body, _recovery_aad(ring.newest, ro.version))

    def _member(self):
        if self.roster is None:
            raise SyncError("This computer is not in a sync group. Run `superhistory sync init`, `superhistory sync join` or `superhistory sync recover`.")
        me = self.roster.entry(self.device_id)
        if me is None or me["removed"] is not None:
            raise SyncError("This computer was removed from the sync group.")

    @_locked
    def init(self, show):
        """Set up the group from this computer, and record it with the app as the group's first member once the person
        approves that in the browser, which `show` points them to. Returns (recovery code or None if already set up,
        first sync report)."""
        st = self.relay.state()
        if st["group"]:
            if st.get("member") and self.roster is None:
                self._bootstrap(show, "Another computer is already in this account's sync group. Run `superhistory sync join` here.")
                self._restore_from_envelope()
            if st.get("member"):
                return None, self.run_once()
            raise SyncError("Sync is already set up for this account. Run `superhistory sync join` here, or `superhistory sync recover`.")
        self._require_keys()
        # Asked before the group exists, so a person who denies it leaves nothing behind, and a try that stops after it
        # asks again to the same end: the app takes the same computer as the first member twice.
        self._bootstrap(show, "Another computer set up sync for this account first. Run `superhistory sync join` here.")
        code, secret = keys.new_recovery_code()
        rident, rtag = keys.recovery_keys(secret)
        first = keys.new_identity()
        ring = keys.KeyRing({1: (first, keys.recipient_of(first))},
                            {"recipient": keys.recipient_of(rident), "tag": rtag, "since": 1})
        me = self._my_entry(1)
        ro = roster.Roster(roster.new_link(None, 1, self.device_id, [me], ring))
        self.relay.create_group(roster.seal_link(ro.link, self.paths.device_ssh, ring.recipient(1)),
                                roster.seal_envelope(ring, ro, me, self.device_id, self.paths.device_ssh),
                                self._recovery_envelope(ring, ro))
        state.put(self.db, "joined_behind", None)
        self._trust(Proven.authored(ring, ro))
        return code, self.run_once()

    def _bootstrap(self, show, taken, action=None):
        """Record this computer with the app as the group's first member once the person approves it on the page `show`
        names. The app does this only while no other computer is a member, and once for two computers racing; `taken`
        says what to do when the other one won. A recovery's `action` also revokes the others first."""
        try:
            self._approved(show, action or {"do": "bootstrap"})
        except relay.RelayError as exc:
            if exc.status == 409:
                raise SyncError(taken) from None
            raise

    def _restore_from_envelope(self):
        """A computer that set up the group but crashed before saving its keys gets them back from its own envelope."""
        payload, body, sig = roster.open_envelope(self.relay.envelope()[1], self.paths.device_age)
        if payload.get("for") != self.device_id or payload.get("signer") != self.device_id or not keys.verify(
                self.device_id, keys.ssh_public(self.paths.device_ssh), pack.NS_ENVELOPE, body, sig):
            raise Refused("this computer's envelope was not made by it")
        self._anchor(keys.KeyRing.from_json(payload["keyring"]), own=payload["roster"])

    def _anchor(self, ring, own=None):
        """Trust a roster from a start a removed computer cannot forge, then walk every link forward from it before any
        side effect. For join and recover the start is the account's roster head the app holds, which only an unrevoked
        member can move. For restore the caller passes `own`, this computer's own signed envelope's {version, hash},
        already checked against its own key. `ring` only opens links; trust comes from the start's hash and the signature
        walk, never from the ring. So a link a removed computer forged above the head, or a lower-version fork, reaches
        no `_trust`."""
        self.hashes = {}
        self._trust(self._verified_walk(ring, own=own, anchoring=True))

    def add_device(self):
        """Open a pairing slot. Show the returned code on this computer, then call `approve`."""
        self._member()
        code, secret = keys.new_pairing_code()
        self.relay.open_slot(keys.PairingKeys(secret).slot_id)
        return Pairing(code, secret)

    def _wait_slot(self, slot_id, which, wait, sleep):
        deadline = time.monotonic() + wait
        while True:
            blob = self.relay.slot(slot_id)[which]
            if blob:
                return blob
            if time.monotonic() > deadline:
                raise SyncError("The other computer did not answer within the time limit.")
            sleep(2)

    @_locked
    def approve(self, pairing, confirm, wait=600, sleep=time.sleep):
        """Wait for the joining computer, ask `confirm(label, words)`, then add it. Returns its label."""
        pk = keys.PairingKeys(pairing.secret)
        joined = self._wait_slot(pk.slot_id, 0, wait, sleep)
        try:
            data = pack.open_tagged(pk.join_key, joined, identity=pk.join_identity)
            entry = roster.check_entry({**{k: data[k] for k in JOIN_KEYS}, "added_in": 1, "removed": None})
        except (Refused, KeyError, TypeError):
            self.relay.delete_slot(pk.slot_id)
            raise Refused("the join was not made with this pairing code") from None
        self._refresh()
        self._refuse_join_on_clash(pk, entry)
        if not confirm(entry["label"], keys.fingerprint_words(entry["device_id"], entry["age_recipient"], entry["ssh_key"])):
            self.relay.delete_slot(pk.slot_id)
            raise SyncError("Not added.")
        for _ in range(3):
            self._refresh()
            self._seal_allowed()  # a removal it finishes changes the roster and the key, so both are read after it
            ring, ro = self.keyring, self.roster
            if ro.entry(entry["device_id"]) or any(e["prefix"] == entry["prefix"] for e in ro.active()):
                raise SyncError("That computer, or its item prefix, is already in the group.")
            self._refuse_join_on_clash(pk, entry)
            if entry["prefix"] in self._removed().cleared:
                raise SyncError(f'"{entry["label"]}" was removed with its items cleared, so it cannot join again as the '
                                "same install. To add it, delete the install-id file in its Superhistory config folder first. "
                                "The items it read before are then cleared on it too.")
            new = {**entry, "added_in": ro.version + 1}
            new_ro = roster.Roster(roster.new_link(ro, ro.epoch, self.device_id, ro.entries + [new], ring))
            self._confirm_with_app(new_ro)  # before the relay stores the link, so a computer the app does not vouch for is never added
            revoked = self._revoked()  # asked last, right before the link and the joiner's envelope are sealed
            if new["device_id"] in revoked:
                self.relay.delete_slot(pk.slot_id)
                raise Refused("the account removed the joining computer")
            if revoked & {e["device_id"] for e in ro.active()}:
                continue  # a removal landed since the check above; the next try finishes it first
            self._may_write_next_link()
            try:
                self.relay.put_roster(ro.version, roster.seal_link(new_ro.link, self.paths.device_ssh, ring.recipient(ro.epoch)),
                                      [(new["device_id"], roster.seal_envelope(ring, new_ro, new, self.device_id,
                                                                               self.paths.device_ssh))],
                                      self._recovery_envelope(ring, new_ro))
            except relay.RelayError as exc:
                if exc.status == 409:
                    continue
                raise
            self._trust(Proven.authored(ring, new_ro))
            self._assert_members()  # the joiner is a member before its welcome exists
            welcome = {"keyring": ring.to_json(), "roster": {"version": new_ro.version, "hash": new_ro.hash}}
            self.relay.put_welcome(pk.slot_id, pack.seal_tagged(pk.welcome_key, new["age_recipient"], welcome))
            return new["label"]
        raise SyncError("The group kept changing while adding the computer. Try again.")

    def join(self, code):
        """Post this computer's keys to the slot named by the pairing code. Returns the words to compare."""
        try:
            pk = keys.PairingKeys(keys.parse_pairing_code(code))
        except ValueError:
            raise SyncError("That is not a pairing code.") from None
        self._require_keys()
        me = self._my_entry(1)
        payload = {k: me[k] for k in JOIN_KEYS}
        self.relay.put_join(pk.slot_id, pack.seal_tagged(pk.join_key, keys.recipient_of(pk.join_identity), payload))
        self._joining = pk
        return keys.fingerprint_words(me["device_id"], me["age_recipient"], me["ssh_key"])

    @_locked
    def finish_join(self, wait=600, sleep=time.sleep):
        """Wait for the welcome, trust the roster it names, and sync. Returns the first sync report."""
        pk = self._joining
        welcome = self._wait_slot(pk.slot_id, 1, wait, sleep)
        data = pack.open_tagged(pk.welcome_key, welcome, identity_file=self.paths.device_age)
        if isinstance(data, dict) and data.get("refused") == "accounts":
            self._joining = None
            with contextlib.suppress(relay.RelayError):
                self.relay.delete_slot(pk.slot_id)
            raise SyncError(self._taken_message(self._refusal_held(data), "run `superhistory sync join` again"))
        self._anchor(keys.KeyRing.from_json(data["keyring"]))
        version = self.roster.version
        state.put(self.db, "joined_behind", version if self._next_link_stood_behind(version) else None)
        mine, me = self.roster.entry(self.device_id), self._my_entry(1)
        if mine is None or mine["removed"] or (mine["age_recipient"], mine["ssh_key"]) != (me["age_recipient"], me["ssh_key"]):
            raise Refused("the roster does not hold this computer's keys")
        try:
            self.relay.delete_slot(pk.slot_id)
        except relay.RelayError:
            pass
        self._joining = None
        return self.run_once()

    def _refuse_join_on_clash(self, pk, entry):
        """If the joiner reads an online account an active member already reads, seal a refusal into the welcome position,
        as a welcome is sealed, and raise. Neither computer's roster changes."""
        held = self.roster.holders(entry["accounts"])
        if not held:
            return
        labels = sorted({label for owners in held.values() for label in owners})
        refusal = {"refused": "accounts", "digests": sorted(held), "held_by": labels}
        self.relay.put_welcome(pk.slot_id, pack.seal_tagged(pk.welcome_key, entry["age_recipient"], refusal))
        who = _quoted(labels)
        raise SyncError(f'Did not add "{entry["label"]}": it reads an online account that {who} already '
                        f'{"reads" if len(labels) == 1 else "read"} in your sync group, and one computer reads each online '
                        f'account. On "{entry["label"]}", remove the source that reads it from its config, then run '
                        "`superhistory sync join` there with a new code from `superhistory sync add-device`.")

    @staticmethod
    def _refusal_held(refusal):
        """{digest: [labels]} from a refusal an approver sealed into a welcome. Every digest names the same holders."""
        digests, labels = refusal.get("digests"), refusal.get("held_by")
        if not (roster.valid_digests(digests) and isinstance(labels, list) and all(roster.valid_label(x) for x in labels)):
            raise Refused("the other computer's refusal is not one this version reads")
        return {d: labels for d in digests}

    def _taken_message(self, held, then):
        """What a person reads when this computer's config names an online account that active members of the group
        already read. It names this computer's sources and the other computers by label, never the account."""
        digests, labels = set(held), sorted({label for owners in held.values() for label in owners})
        names = sorted(name for name, key in self.accounts.items() if roster.claim(key) in digests)
        where = " and ".join(f"[sources.{name}]" for name in names) or "this computer's config"
        reads = "reads" if len(labels) == 1 else "read"
        return (f"This computer reads the online account{'s' if len(names) > 1 else ''} in {where}, which {_quoted(labels)} "
                f"already {reads} in your sync group. One computer reads each online account. Remove "
                f"{where if names else 'the source that reads it'} from this computer's config, then {then}. To move that "
                f"account here instead, run `superhistory forget NAME` on {_quoted(labels)}, where NAME is the source that "
                "reads it there, remove that source from its config, and sync there first.")

    def _refresh(self):
        st = self.relay.state()
        if not st["group"]:
            raise SyncError("Sync is not set up for this account.")
        if st["roster_version"] > self.roster.version or st["epoch"] not in self.keyring.epochs:
            self._accept_roster_links()
        self._confirm_roster()
        return st

    def _accept_roster_links(self, need_envelope=False):
        """Catch up on the relay's roster links from the one this computer already trusts."""
        self._trust(self._verified_walk(self.keyring, need_envelope=need_envelope))

    def _verified_walk(self, ring, own=None, anchoring=False, need_envelope=False):
        """The one verified walk, and the only place a link is accepted or the roster advanced. From a trusted start it
        accepts every relay link forward under `Roster.accept` (an active member signed it and the signature verifies)
        and holds each to `Vouch`. It changes no state; it returns a Proven for `_trust` alone.

        The app keeps every link a device confirmed while it was a member. Every link a member still stands behind must
        be on this computer's chain, and the account's roster head, the newest of them, must be reached. A link signed by
        a device the app has revoked counts only up to a confirmed link the walk reaches by its hash, so one a removed
        computer signs after its removal never counts, and a confirmation that names no real link, such as one a stolen
        token made up, vouches for nothing and blocks nothing once its device is revoked.

        Steady state (`anchoring` false) starts from the roster this computer already trusts. The anchor flows start
        from a link whose hash matches a head a removed computer cannot forge: `own`, this computer's own signed
        envelope's {version, hash} for restore, else the account's roster head, else, when no member stands behind any
        link, the newest confirmed link the relay has. That start is trusted by its hash, not its signature, and every
        link after it is accepted. `ring` opens links, gaining an envelope's keys only to decrypt (which trust never
        rests on); the proven ring is built from `base`. The app's device list and confirmed links are asked after the
        links are fetched."""
        vouch = self._vouch()
        head_version, head_hash = self.app.roster_head(self.app_token)
        if anchoring:
            anchor = own or ({"version": head_version, "hash": head_hash} if head_version else None)
            ro, start, prior, base = None, anchor["version"] if anchor else 1, {}, ring
        else:
            anchor, ro, start, prior, base = None, self.roster, self.roster.version, self.hashes, self.keyring
        links, opened = self._open_links(self.relay.roster_links(after=start - 1 if anchoring else start), ring)
        newest = max((link["version"] for link, _, _ in links), default=start)
        points = self.app.roster_points(self.app_token, newest)
        if anchoring and anchor is None:
            hashes = {link["version"]: roster.link_hash(link) for link, _, _ in links}
            reachable = [point for point in points if hashes.get(point[0]) == point[1]]
            if not reachable:
                raise Refused("the account has no roster head to trust")
            version, digest = max(reachable)
            anchor, start = {"version": version, "hash": digest}, version
        if anchoring:
            links = [opened_link for opened_link in links if opened_link[0]["version"] >= start]
            if not links or links[0][0]["version"] != start:
                raise Refused("the relay does not have the roster head")
        seen, revoked_signed = {}, []
        for link, body, sig in links:
            if ro is None:
                ro = roster.Roster(link)  # the start, trusted by its hash matching the account head, not its signature
                if (ro.version, ro.hash) != (anchor["version"], anchor["hash"]):
                    raise Refused("the roster head does not match")
            else:
                ro = ro.accept(link, body, sig)
            seen[ro.version] = ro.hash
            if vouch.revoked(ro.link["signer"]):
                revoked_signed.append(ro.version)
        known = {**prior, **seen}
        for (version, digest), active in points.items():
            if active and version > start and seen.get(version) != digest:
                raise Refused("the relay's roster is not the one the account lists")
            if active and known.get(version, digest) != digest:
                raise Refused("this computer's roster is not the one the account lists")
        if head_version > newest:
            raise Refused("the relay's roster is not the one the account lists")
        reached = max((version for version, digest in points if seen.get(version) == digest), default=start)
        if any(version > reached for version in revoked_signed):
            raise Refused("a roster change its signer made after the account removed it")
        if need_envelope and opened is None:
            opened = roster.open_envelope(self.relay.envelope()[1], self.paths.device_age)
        final = base if opened is None else base.merged(*roster.trust_envelope(ro, opened, self.device_id))
        if anchoring:
            final = keys.KeyRing(final.epochs, {**final.recovery, "head": ro.version})
        return Proven(final, ro, seen)

    def _open_links(self, fetched, ring):
        """([(link, signed body, signature)], the opened envelope or None). A link no key in `ring` opens is tried
        again with this computer's envelope's keys, which only decrypt: trust never rests on them."""
        links, opened = [], None
        for _, blob in fetched:
            try:
                links.append(roster.open_link(blob, ring))
            except Refused:
                if opened is not None:
                    raise
                opened = roster.open_envelope(self.relay.envelope()[1], self.paths.device_age)
                ring = ring.merged(keys.KeyRing.from_json(opened[0].get("keyring")))  # tentative until its signer checks out
                links.append(roster.open_link(blob, ring))
        return links, opened

    def _identity(self, epoch):
        if epoch not in self.keyring.epochs:
            self._accept_roster_links(need_envelope=True)
        if epoch not in self.keyring.epochs:
            raise Refused(f"no key for epoch {epoch}")
        return self.keyring.identity(epoch)

    def _author(self, device_id):
        if self.roster.entry(device_id) is None:
            self._accept_roster_links()
        entry = self.roster.entry(device_id)
        if entry is None:
            raise Refused("pack from a device not in the roster")
        return entry

    def _open(self, p):
        """Check 1: the pack opens with the key for its epoch, and its header names the same epoch."""
        identity = self._identity(p["epoch"])
        pk = pack.parse(keys.open_with([identity], self.relay.pack(p["seq"])), inflate=False)
        if pk.header["epoch"] != p["epoch"]:
            raise Refused("pack epoch does not match its header")
        return pk

    def _signed(self, pk, entry):
        if pk.is_part:
            raise Refused("snapshot part listed as a change pack")
        if not pack.verify(pk, entry):
            raise Refused("pack signature does not verify")

    def _continues(self, pk, entry, head):
        h = pk.header
        if (h["dseq"], h["prev"]) != (head[0] + 1, head[1]):
            raise Refused(f"gap in the chain of {entry['label']}")

    def _check_records(self, recs, entry=None):
        """Check 6 for a change pack (`entry` given) and check 7 for every pack."""
        limit = self._now_ms() + DAY_MS
        for rec in recs:
            if entry and rec.kind != "snapshot" and rec.writer != entry["prefix"]:
                raise Refused("record outside its author's prefix")
            if rec.v > limit:
                raise Refused("record version is more than a day ahead")

    def _note_roster(self, header):
        known = self.hashes.get(header["roster_version"])
        if known and known != header["roster_hash"]:
            state.put(self.db, "fork", {"author": header["author"], "version": header["roster_version"]})

    def _refuse(self, where, reason, received=0, skipped=0):
        state.put(self.db, "problem", {"at": where, "reason": reason})
        return Report(received=received, stalled=f"{where}: {reason}", skipped=skipped)

    @_locked
    def pull(self):
        """Apply every pack after the cursor, in order. Stops at the first pack that fails a check."""
        self._member_or_recovering()
        self._passed = self._signed_passed = 0
        try:
            st = self._refresh()
        except Refused as exc:
            return self._refuse("the roster", str(exc))
        self._clear_removed()
        adopting = state.get(self.db, "adopting")
        if adopting is None:
            adopting = not state.has_sent(self.db)
            state.put(self.db, "adopting", adopting)
        cursor = state.get(self.db, "cursor", 0)
        snap = st["snapshot"]
        # What each computer told the app the relay holds, asked before any pack is listed: an honest relay serves all
        # of it in this pull, and anything it leaves out is checked below, whatever its listing says.
        listed = self._listed()
        confirmed = self._reach(listed)
        active = {d: dev["chain"]["dseq"] for d, dev in listed.items() if dev["chain"]["dseq"] and not dev["revoked_on"]}
        skipped, taken = 0, set()  # every snapshot this pull read, so a relay cannot keep handing back old ones
        if snap and cursor < snap["through"]:
            try:
                skipped = self._read_snapshot(snap, adopting)
            except Refused as exc:
                return self._refuse(f"snapshot {snap['id']}", str(exc))
            taken.add(snap["id"])
            cursor = state.get(self.db, "cursor", 0)
        received, vouched, gone_past = 0, {}, {}
        while True:
            try:
                page = self._page(cursor)
            except Refused as exc:
                return self._refuse("the pack listing", str(exc), received, skipped)
            relist = False
            for p in page:
                if p["snapshot_id"]:
                    try:
                        self._passing()
                    except Refused as exc:
                        return self._refuse("the pack listing", str(exc), received, skipped)
                    cursor = p["seq"]
                    state.put(self.db, "cursor", cursor)
                    continue
                author = None
                try:
                    pk = self._open(p)
                    entry = self._author(pk.header["author"])
                    self._signed(pk, entry)
                    author = entry["label"]  # named only once its signature shows it really wrote the pack
                    frozen = self._vouch().frozen(entry["device_id"])  # asked after the pack was fetched
                    if frozen and frozen.clear is None:
                        raise Refused(f"the account removed {entry['label']} without saying whether to keep what it "
                                      "read, so nothing it wrote applies until a removal link says")
                    head = state.head(self.db, entry["device_id"])
                    if pk.header["dseq"] > head[0] + 1:
                        # A signed pack past where this computer is in its author's chain: the packs between are not
                        # here. A compaction that landed after this pull read the relay's state holds them, so it is
                        # taken first. Should it not reach them, the chain check below stops the pull.
                        current = self.relay.state().get("snapshot")
                        if self._new_snapshot(current, taken):
                            taken.add(current["id"])
                            skipped += self._read_snapshot(current, adopting)
                            if state.get(self.db, "cursor", 0) >= p["seq"]:
                                # The snapshot reaches this pack: this page was listed before that compaction, which
                                # holds this pack and may hold others after it, dropped from the relay since. So the
                                # relay is asked again from where the snapshot left this computer. Every time that
                                # happens the cursor has moved past a pack, so it cannot go on for ever.
                                cursor, relist = state.get(self.db, "cursor", 0), True
                                break
                            head = state.head(self.db, entry["device_id"])
                    if frozen and p["seq"] not in vouched:
                        vouched.update(self._vouched_run(entry, head, cursor, frozen, partial=True))
                    if frozen and vouched.get(p["seq"]) is None:
                        if pk.header["dseq"] > gone_past.get(entry["device_id"], head[0]):  # its signature checked out
                            gone_past[entry["device_id"]] = pk.header["dseq"]
                            self._passing(signed=True)
                        else:
                            self._passing()
                        cursor = p["seq"]  # past every pack it confirmed before its removal, so never applied
                        state.put(self.db, "cursor", cursor)
                        continue
                    if frozen and vouched[p["seq"]] != pk.hash:
                        raise Refused(f"pack from removed computer {entry['label']} changed while reading ahead")
                    self._continues(pk, entry, head)
                    self._check_records(pk.records, entry)
                except Refused as exc:
                    where = f"pack {p['seq']} from {author}" if author else f"pack {p['seq']}"
                    return self._refuse(where, str(exc), received, skipped)
                self._note_roster(pk.header)
                with state.tx(self.db):
                    # Read for each pack, never once for the pull: a roster change trusted partway through, such as one
                    # that opening a pack under a new key needs, can clear a computer. A set read earlier would bring
                    # back what was just deleted, or park a live author's mark on it forever, since the item never
                    # arrives and the cleared mark stops any later delete. The author's own records also wait on the
                    # app's choice for it.
                    drop = self._cleared_here() | ({entry["prefix"]} if frozen and frozen.clear else frozenset())
                    skipped += records.apply(self.db, pk.records, self.prefix, drop, adopting)
                    state.set_head(self.db, entry["device_id"], pk.header["dseq"], pk.hash)
                    state.put(self.db, "cursor", p["seq"])
                cursor = p["seq"]
                received += 1
            if not relist and len(page) < PAGE:
                break
        # Every computer's chain must reach what it confirmed. Short of it, the packs this computer lacks were compacted
        # after this pull read the relay's state, or the relay is keeping them back, and no listing proves which, since the
        # relay writes the listing. So any short chain, a removed computer's included, since what the person kept of one
        # must reach here too, takes the current snapshot, as far as it reaches past what this computer applied. A
        # computer still in the account that is short after that stops the pull, named. A removed computer's
        # confirmation may be one a stolen token made up, which nothing can ever serve, and stopping on it would stop
        # every computer for good, so a removed computer still short is named in the report on every sync instead.
        if self._short_of(confirmed):
            current = self.relay.state().get("snapshot")
            if self._new_snapshot(current, taken):
                try:
                    skipped += self._read_snapshot(current, adopting)
                except Refused as exc:
                    return self._refuse(f"snapshot {current['id']}", str(exc), received, skipped)
        labels = lambda ids: sorted((self.roster.entry(d) or {}).get("label", "another computer") for d in ids)
        short = self._short_of(active)
        if short:
            return self._refuse("the relay", f"it has not served packs confirmed by {', '.join(labels(short))}",
                                received, skipped)
        unserved = self._short_of(confirmed)
        missing = tuple(labels(unserved))
        for label in missing:
            log.warning(missing_notice(label))  # the scheduled run reads no report, so its log carries this
        # Asked again at the end: a computer the account removed, deleting what it read, while this pull ran may have
        # had records apply in it, such as a mark that waited for an item this pull brought. They go now, not a pull later.
        self._clear_removed()
        with state.tx(self.db):
            state.put(self.db, "adopting", False)
            state.put(self.db, "problem", None)
            state.put(self.db, "missing", unserved)
        try:
            self.relay.applied(cursor)
        except relay.RelayError:
            pass  # a recovering computer is not a member yet
        return Report(received=received, skipped=skipped, missing=missing)

    def _confirm_sent(self):
        """Tell the app which of this computer's packs the relay holds, as far as this computer knows."""
        sent = state.get(self.db, "sent_head")
        if sent and sent != state.get(self.db, "confirmed_head"):
            self.app.confirm_chain(self.app_token, *sent)
            state.put(self.db, "confirmed_head", sent)

    def _vouched_run(self, entry, head, after, frozen, partial=False):
        """{seq: hash, or None when it does not count} for the change packs of `entry`, a computer the account removed,
        after seq `after`. A pack counts when the chain from `head`, the last of its packs this computer applied, runs
        through it to `frozen.pin` or to a pack the computer itself confirmed to the app while it was a member, never
        past `frozen.bound`. Its confirmations are asked after its packs are fetched. A confirmed pack the chain from
        `head` does not reach means the relay forked or broke the chain below it, so none of them apply and sync stops.
        A confirmation that matches no pack the relay serves, such as one a stolen token made up, vouches for nothing,
        and the packs after the last one that counts are never applied.

        With `partial`, a pass the pull's limits stop before the end of the listing still returns what it read up to the
        last pack that counts, and nothing after it, so the pull applies and keeps that much and a later sync reads on
        from there. It raises the limit when nothing it read counts."""
        packs, seen, over = [], {}, None
        try:
            while True:
                page = self._page(after)
                for p in page:
                    after = p["seq"]
                    if p["snapshot_id"]:
                        self._passing()
                        continue
                    pk = self._open(p)
                    signed = self._ahead(pk, seen)
                    if not pk.is_part and pk.header["author"] == entry["device_id"]:
                        packs.append((p["seq"], pk.header["dseq"], pk.header["prev"], pk.hash, signed))
                if len(page) < PAGE:
                    break
        except _OverLimit as exc:
            if not partial:
                raise
            over = exc
        upto = max((dseq for _, dseq, _, _, _ in packs), default=0)
        if frozen.bound is not None:
            upto = min(upto, frozen.bound)
        confirmed = self.app.chain_points(self.app_token, entry["device_id"], head[0], upto) if upto > head[0] else {}
        good = set(confirmed.items()) | ({tuple(frozen.pin)} if frozen.pin else set())
        out, run, last = {}, {}, tuple(head)
        for seq, dseq, prev, digest, signed in packs:
            out[seq] = None
            if frozen.bound is not None and dseq > frozen.bound:
                continue
            if (dseq, prev) == (last[0] + 1, last[1]) and signed:
                last, run[seq] = (dseq, digest), digest
                if last in good:
                    out.update(run)
            elif (dseq, digest) in good:
                raise Refused(f"chain of removed computer {entry['label']} does not reach a pack it confirmed")
        if over:
            counted = [seq for seq, digest in out.items() if digest is not None]
            if not counted:
                raise over
            out = {seq: digest for seq, digest in out.items() if seq <= max(counted)}
        return out

    def _member_or_recovering(self):
        if self.roster is None:
            raise SyncError("This computer is not in a sync group. Run `superhistory sync init`, `superhistory sync join` or `superhistory sync recover`.")

    def _read_snapshot(self, snap, adopting):
        """Take the current snapshot, trusting it only through its announcement in its author's chain. Returns how many
        of its records were skipped for an id that holds a secret. While the account has removed a computer without
        saying what happens to what it read, no snapshot applies, since one would add or drop that computer's items.

        It is taken author by author. Where the snapshot reaches further in an author's chain than this computer
        applied, it stands for that author up to `through`: its records apply, that author's items it lacks are deleted,
        and the head moves to the snapshot's. Where this computer applied as far or further, as for its own packs sent
        after the snapshot was made, or a computer that joined after it, nothing of that author's changes. So a snapshot
        never deletes what this computer applied past it, and a pull that reaches packs past a compaction still gets
        what was compacted. The one snapshot refused is a fork: a different pack at the same place in an author's
        chain."""
        listed, parts = self.relay.snapshot_parts(snap["id"]), []
        for p in listed:
            pk = self._open(p)
            if not pk.is_part or pk.header["snapshot"]["id"] != snap["id"]:
                raise Refused("snapshot part id does not match")
            if len(listed) != pk.header["snapshot"]["parts"]:  # before any more is fetched
                raise Refused("snapshot parts are missing or repeated")
            parts.append(pk)
        if not parts:
            raise Refused("snapshot has no parts")
        author, first = parts[0].header["author"], parts[0].header["snapshot"]
        shared = ("id", "through", "parts", "heads")
        if any(pk.header["author"] != author or any(pk.header["snapshot"][k] != first[k] for k in shared) for pk in parts):
            raise Refused("snapshot parts disagree")
        parts.sort(key=lambda pk: pk.header["snapshot"]["part"])
        if first["through"] != snap["through"] or [pk.header["snapshot"]["part"] for pk in parts] != list(range(first["parts"])):
            raise Refused("snapshot parts are missing or repeated")
        entry = self._author(author)
        for pk in parts:
            if not pack.verify(pk, entry):
                raise Refused("snapshot part signature does not verify")
            self._check_records(pk.records)
        self._find_announcement(entry, first, pack.manifest([pk.plain for pk in parts]))
        # Asked only now, after the parts and the announcement were fetched and opened: opening one under a new key
        # can trust a roster change that clears a computer, and a set read before it would bring that computer's items
        # back while the cleared mark stops any later pull from deleting them again.
        removed = self._removed()
        if removed.undecided:
            raise Refused("the account removed a computer without saying whether to keep what it read")
        listed, heads = {a: tuple(v) for a, v in first["heads"].items()}, state.heads(self.db)
        if any(d and heads.get(a, (0, ""))[0] == d and heads[a][1] != h for a, (d, h) in listed.items()):
            raise Refused("snapshot forks from packs this computer already applied")
        behind = {a for a, (d, _) in listed.items() if heads.get(a, (0, ""))[0] < d}
        # The snapshot stands for every other install up to `through`, as it always has, except one this computer
        # applied further than the snapshot reaches: those items stay, and so do those of any computer under the same
        # install id, such as one removed with its items kept that joined again as itself.
        keep = frozenset(self._author(a)["prefix"] for a, (d, _) in heads.items() if d > listed.get(a, (0, ""))[0])
        recs = [r for pk in parts for r in pk.records]
        with state.tx(self.db):
            skipped = 0
            if behind:
                skipped = records.apply(self.db, recs, self.prefix, removed.cleared | self._cleared_here(), adopting)
                records.drop_lacking(self.db, self.prefix,
                                     {(r.source, r.source_id) for r in recs if r.kind in ("item", "gone")}, keep)
                for a in behind:
                    state.set_head(self.db, a, *listed[a])
            if state.get(self.db, "cursor", 0) < snap["through"]:
                state.put(self.db, "cursor", snap["through"])
            state.put(self.db, "taken_snapshot", snap["id"])
        return skipped

    def _new_snapshot(self, current, taken):
        """Whether the relay's current snapshot is one to read: not read in this pull, and not the one this computer
        took last. Reading a snapshot again changes nothing, since it holds no more than this computer applied from it,
        so a chain that stays short of a confirmation nothing can serve, such as a stolen token's made-up one, does not
        cost a download of the whole snapshot on every sync."""
        return bool(current) and current["id"] not in taken and current["id"] != state.get(self.db, "taken_snapshot")

    def _page(self, after):
        """The relay's next page of packs after `after`. It must climb strictly from `after`, checked whole before any
        of it is used: a pack listed after a later one would sit below the cursor once that one applied, where no pull
        would ask for it again, and a page listed again would hold a pass for ever. Raises Refused."""
        page = self.relay.packs(after=after, limit=PAGE)
        seqs = [after] + [p["seq"] for p in page]
        if any(a >= b for a, b in zip(seqs, seqs[1:])):
            raise Refused("the relay listed packs out of order")
        return page

    def _passing(self, signed=False):
        """Count one more entry this pull went past without applying it, in any of its passes: toward MAX_SIGNED_PASSED
        when `signed`, a pack a computer signed at a later place in its chain, else toward MAX_PASSED. Past either the
        relay is listing more than any sync goes past, and the pull stops. Raises Refused."""
        if signed:
            self._signed_passed += 1
        else:
            self._passed += 1
        if self._passed > MAX_PASSED or self._signed_passed > MAX_SIGNED_PASSED:
            raise _OverLimit("the relay listed more packs than any sync goes past")

    def _ahead(self, pk, seen):
        """Count `pk`, a pack a pass reading ahead opened and goes past, and return whether its author in the roster
        signed it. It counts as signed, toward MAX_SIGNED_PASSED, when it is a change pack its author signed at a later
        place in its chain than this computer applied and than this pass has seen (`seen`, which this updates), and
        toward MAX_PASSED otherwise."""
        entry = None if pk.is_part or not self.roster else self.roster.entry(pk.header["author"])
        signed = entry is not None and keys.verify(entry["device_id"], entry["ssh_key"], pack.NS_PACK, pk.body, pk.sig)
        author = pk.header["author"]
        forward = signed and pk.header["dseq"] > seen.get(author, state.head(self.db, author)[0])
        if forward:
            seen[author] = pk.header["dseq"]
        self._passing(signed=forward)
        return signed

    def _reach(self, listed):
        """How far each computer's chain must reach here: the last pack it confirmed to the app, or for a removed
        computer the head its removal pinned in the trusted roster when that is further, as when its last pack reached
        the relay but its confirmation never reached the app. Short of either, this computer lacks a pack that counts."""
        reach = {d: dev["chain"]["dseq"] for d, dev in listed.items() if dev["chain"]["dseq"]}
        for e in self.roster.entries if self.roster else ():
            pin = (e["removed"] or {}).get("head_dseq") or 0
            if pin > reach.get(e["device_id"], 0):
                reach[e["device_id"]] = pin
        return reach

    def _short_of(self, confirmed):
        """The computers whose chain this computer has not applied as far as they confirmed to the app."""
        return sorted(d for d, dseq in confirmed.items() if state.head(self.db, d)[0] < dseq)

    def _find_announcement(self, entry, snap, manifest):
        """The compactor's next change pack after `through` must announce this snapshot, and count as `Vouch` says,
        asked after the announcement was fetched. A compactor the account has removed since must have announced it in a
        pack that counts, as `_vouched_run` says, so a removed device cannot announce a snapshot after its removal."""
        head, after, seen = tuple(snap["heads"].get(entry["device_id"], (0, ""))), snap["through"], {}
        while True:
            page = self._page(after)
            for p in page:
                after = p["seq"]
                if p["snapshot_id"]:
                    self._passing()
                    continue
                pk = self._open(p)
                if pk.is_part or pk.header["author"] != entry["device_id"]:
                    self._ahead(pk, seen)
                    continue
                self._signed(pk, entry)
                self._continues(pk, entry, head)
                found = [r.body for r in pk.records if r.kind == "snapshot" and r.body["id"] == snap["id"]]
                if not found or (found[0]["through"], found[0]["parts"], found[0]["manifest"]) != (
                        snap["through"], snap["parts"], manifest):
                    raise Refused("snapshot does not match its author's announcement")
                frozen = self._vouch().frozen(entry["device_id"])  # asked after the announcement was fetched
                if frozen and self._vouched_run(entry, head, p["seq"] - 1, frozen).get(p["seq"]) != pk.hash:
                    raise Refused(f"snapshot from removed computer {entry['label']} is announced past the packs it "
                                  "confirmed before its removal")
                return
            if len(page) < PAGE:
                raise Refused("snapshot is not announced in its author's chain")

    @_locked
    def push(self):
        """Send every own change since the last push. Returns how many records went up."""
        self._member()
        self._seal_allowed()
        if state.get(self.db, "adopting") is not False and self.pull().stalled:
            raise SyncError("A new or rebuilt store must pull to the head before its first push, and the pull stopped.")
        self._reclaim_outbox()
        sent = self._upload_pending()
        changed = records.changes(self.db, self.prefix)
        if changed:
            with state.tx(self.db):
                records.stage(self.db, changed, self._now_ms())
        while self._sign_next():
            sent += self._upload_pending()
        return sent

    def _reclaim_outbox(self):
        """A store kept through a removal and a new join can still hold packs its earlier device signed and never sent,
        and records its earlier install id wrote. The packs go back to unsigned, so this computer signs them again as
        itself, and the records this install id may not write are dropped."""
        if not state.count_outbox(self.db):
            return
        with state.tx(self.db):
            if state.get(self.db, "signed", [self.device_id])[0] != self.device_id:
                state.unsign_all(self.db)
            state.drop_outbox(self.db, [rid for rid, line in state.unsigned_all(self.db)
                                        if records.own_record(line).writer != self.prefix])

    def _signing_head(self):
        author, dseq, digest = state.get(self.db, "signed", [None, 0, ""])
        if author != self.device_id:
            dseq, digest = 0, ""
        seen = state.head(self.db, self.device_id)
        return seen if seen[0] > dseq else (dseq, digest)

    def _sign(self, dseq, prev, lines, snapshot=None):
        lines = [line for line in (records.outgoing(line, self.db) for line in lines) if line is not None]
        header = {"epoch": self.keyring.newest, "author": self.device_id, "dseq": dseq, "prev": prev,
                  "roster_version": self.roster.version, "roster_hash": self.roster.hash, "snapshot": snapshot}
        return pack.build(header, lines, self.paths.device_ssh)

    def _save_pending(self, ids, dseq, prev, plain):
        with state.tx(self.db):
            state.save_pending(self.db, ids, dseq, prev, str(uuid.uuid4()), self.keyring.newest, plain)
            state.put(self.db, "signed", [self.device_id, dseq, _sha(plain)])

    def _sign_next(self):
        batch = state.unsigned(self.db, PACK_BUDGET)
        if not batch:
            return False
        self._seal_allowed()  # a removal can land while a long push runs, so the app is asked before each pack's key is chosen
        dseq, prev = self._signing_head()
        self._save_pending([i for i, _ in batch], dseq + 1, prev, self._sign(dseq + 1, prev, [r for _, r in batch]))
        return True

    def _upload_pending(self):
        self._confirm_sent()  # one a run sent but could not confirm before it stopped
        sent, retries, resigned = 0, 0, 0
        while True:
            row = state.pending(self.db)
            if row is None:
                return sent
            dseq, prev, pack_id, epoch, plain = row
            if not self._masked_when_signed(plain):
                resigned += 1
                if resigned > MAX_RESIGNS:  # masking is stable, so a pack signed again leaves as it is; never loop
                    raise SyncError("A pack still changed after it was signed again through the mask, so nothing more "
                                    "is sent.")
                # Signed by a client that did not mask at signing. If the relay already holds it, it has left; if not, the
                # same records are signed again, masked, under the same dseq, as after a refused upload.
                self.pull()
                if state.head(self.db, self.device_id)[0] >= dseq:
                    with state.tx(self.db):
                        sent += state.finish_pending(self.db, dseq)
                        state.put(self.db, "sent_head", list(state.head(self.db, self.device_id)))
                    self._confirm_sent()
                else:
                    self._seal_allowed()  # a removal can land before a resign; finish it so nothing seals to a removed key
                    self._save_pending([], dseq, prev, self._sign(dseq, prev, state.outbox_records(self.db, dseq)))
                continue
            try:
                self.relay.put_pack(pack_id, epoch, pack.seal(plain, self.keyring.recipient(epoch)))
            except relay.RelayError as exc:
                if exc.status != 409 or exc.code not in ("stale_epoch", "rotation_due") or retries >= 3:
                    raise
                retries += 1
                # The relay stored nothing. Catch up on keys after the stale epoch, then finish any removal the app
                # lists before sealing again, whatever the relay claims about rotation (its rotation_due flag is its
                # word, not the app's), so nothing is sealed to a computer revoked mid-push and the chain never forks.
                self._refresh()
                self._seal_allowed()
                self._save_pending([], dseq, prev, self._sign(dseq, prev, state.outbox_records(self.db, dseq)))
                continue
            with state.tx(self.db):
                sent += state.finish_pending(self.db, dseq)
                state.put(self.db, "sent_head", [dseq, _sha(plain)])
            self._confirm_sent()

    def _masked_when_signed(self, plain):
        """Whether every record in a signed pack is as `records.outgoing` leaves it."""
        lines = [records.to_json(r) for r in pack.parse(plain).records]
        return [records.outgoing(line, self.db) for line in lines] == lines

    def _listed(self):
        """The account's devices as the app lists them, by id."""
        return {d["device_id"]: d for d in self.app.devices(self.app_token)}

    def _vouch(self):
        """The app's word on whose signatures count, for artifacts fetched before this call. The account's devices are
        asked when a signature first needs them."""
        return Vouch(self.device_id, self.roster, lambda: self.app.devices(self.app_token))

    def _revoked(self, listed=None):
        return {i for i, d in (listed or self._listed()).items() if d["revoked_on"]}

    def _revoked_members(self):
        return {e["device_id"] for e in self.roster.active()} & self._revoked()

    def _finish_removals(self, pulled=None):
        """A computer the app revoked that the trusted roster still holds active has the current epoch key. So before
        sealing anything under that key, this computer rotates to remove it, whatever the relay says, and it seals
        nothing while it cannot. A revocation that lands during a rotation — after that rotation read the app's list but
        before it sealed — would leave the newly revoked computer holding the fresh key, so the revoked set is asked
        again after each rotation and rotated again until none is left active. Returns the devices still due (non-empty
        only when sync stalled or it could not finish in MAX_REMOVAL_ROUNDS), so the caller seals nothing while any
        remains."""
        for _ in range(MAX_REMOVAL_ROUNDS):
            due = self._revoked_members()
            if not due:
                return set()
            if (pulled or self.pull()).stalled:
                return due
            pulled = None
            self._rotate(due)
        return self._revoked_members()

    def _seal_allowed(self):
        if self._finish_removals():
            raise SyncError("A removed computer still holds the current key, so nothing is sent until sync can finish "
                            "removing it.")

    def _removing(self, ro, targets, version, clear, listed):
        """`ro`'s entries with `targets` marked removed at the last pack the app lists each as having sent, frozen when
        it revoked the device, or at the last one this computer applied when that is later, as a pack sent but not yet
        confirmed can be. A relay that held packs back from this computer cannot cut a removed computer's history short."""
        heads, out = state.heads(self.db), []
        for e in ro.entries:
            if e["device_id"] in targets:
                chain = listed[e["device_id"]]["chain"]
                applied = heads.get(e["device_id"], (0, ""))
                d, h = applied if applied[0] > chain["dseq"] else (chain["dseq"], chain["hash"] or "")
                e = {**e, "removed": {"in_version": version, "head_dseq": d, "head_hash": h,
                                      "clear": e["device_id"] in clear}}
            out.append(e)
        return out

    def _rotate(self, removed=(), new_recovery=False):
        """A new epoch, marking `removed` with the heads of their chains. Returns the new recovery code if one was made.

        Every device the app lists as revoked is removed in the same rotation, so no envelope, and no new recovery tag,
        is sealed for one, whatever the relay says. Each removal carries the choice the app recorded when it revoked the
        device, whichever computer finishes it. One the app recorded no choice for is never written."""
        for _ in range(3):
            self._refresh()
            ring, ro = self.keyring, self.roster
            listed = self._listed()  # asked on every try, right before the envelopes are sealed
            targets = {e["device_id"] for e in ro.active()} & (set(removed) | (self._revoked(listed) - {self.device_id}))
            if not targets and not new_recovery:
                return None
            choices = self._removals(listed)
            if any(choices[d] is None for d in targets):
                raise SyncError("The account removed a computer without saying whether to keep what it read, so this "
                                "computer writes no change about it and sends nothing until a removal says.")
            self._may_write_next_link()
            version, epoch = ro.version + 1, ring.newest + 1
            entries = self._removing(ro, targets, version, {d for d in targets if choices[d]}, listed)
            new_ring, code = ring.with_epoch(epoch, keys.new_identity()), None
            if new_recovery:
                code, secret = keys.new_recovery_code()
                rident, rtag = keys.recovery_keys(secret)
                new_ring = new_ring.with_recovery(keys.recipient_of(rident), rtag, version)
            new_ro = roster.Roster(roster.new_link(ro, epoch, self.device_id, entries, new_ring))
            envelopes = [(e["device_id"], roster.seal_envelope(new_ring, new_ro, e, self.device_id, self.paths.device_ssh))
                         for e in new_ro.active()]
            try:
                self.relay.rotate(ring.newest, ro.version, roster.seal_link(new_ro.link, self.paths.device_ssh,
                                                                            new_ring.recipient(epoch)),
                                  envelopes, self._recovery_envelope(new_ring, new_ro))
            except relay.RelayError as exc:
                if exc.status == 409:
                    continue
                raise
            self._trust(Proven.authored(new_ring, new_ro))
            return code
        raise SyncError("Another computer kept changing the group. Try again.")

    def _may_write_next_link(self):
        """A computer joins from the account's roster head. When a computer still in the account had already
        confirmed the next link as this one joined, that link may be one a relay now hides, so this computer writes no
        link of its own, at that version or any after, until it trusts a link past the one it joined at. It waits for
        the confirmed link instead. Only a confirmation by a computer still in the account holds it back, asked when it
        is about to write: a revoked device's confirmation may be one a stolen token made up, and revoking a device
        ends whatever it did to the head (requirement 8), so a joiner never waits on it for good. Every computer still
        in the account that trusts a link confirmed it, so the only link a joiner may write past is one that only
        revoked devices stand behind, which nothing in the account depends on.

        This is kept to such a fresh joiner on purpose. Every other computer must be able to write past a version a
        revoked device confirmed: a stolen device token can confirm a made-up hash at the next version while its device
        is a member, the app cannot tell that claim from a real link a relay hides, and once the device is revoked nobody
        could ever write that version again. A link another computer writes there still carries the app's recorded
        choice for every removal, so it neither keeps what the person asked to delete nor deletes what they kept."""
        if state.get(self.db, "joined_behind") == self.roster.version and self._next_link_stood_behind(self.roster.version):
            raise SyncError("Another computer confirmed a change to the group that this computer has not seen yet, so "
                            "it waits for that change before it writes one.")

    def _next_link_stood_behind(self, version):
        """Whether a computer still in the account confirmed a roster link at `version + 1`, asked now. A revoked
        device's confirmation never counts: it may be one a stolen token made up while the device was a member, and
        revoking the device ends whatever it did to the head (requirement 8)."""
        return any(v == version + 1 and active
                   for (v, _), active in self.app.roster_points(self.app_token, version + 1).items())

    def _other(self, label):
        matches = [e for e in self.roster.active() if e["label"] == label and e["device_id"] != self.device_id]
        if len(matches) != 1:
            raise SyncError("No single other computer in the group has that label. `superhistory sync status` lists them.")
        return matches[0]

    def items_read_by(self, label):
        """How many items in this store the other computer named `label` read."""
        self._member()
        return records.count_items(self.db, self._other(label)["prefix"])

    def _approved(self, show, action):
        """What the app did for `action`, asked by this computer with its own token, once the person approved exactly
        that on the page `show` names. The store stays held meanwhile, so a scheduled sync finds it busy and tries again."""
        return approve(self.app, self.app_url, show, action, token=self.app_token)

    @_locked
    def remove(self, label, show, *, clear):
        """Revoke a computer through the app once the person approves it in the browser, which `show` points them to,
        then pull to the head, rotate and compact. With `clear`, every computer deletes what the removed one wrote. The
        app records the choice with the revocation, so whichever computer finishes the removal applies it. The returned
        `cleared` says what the removal that landed does."""
        self._member()
        target = self._other(label)["device_id"]
        if target not in self._approved(show, {"do": "remove", "device_id": target, "clear": clear}):
            raise relay.RelayError(502, "bad_answer")
        pulled = self.pull()
        if pulled.stalled:
            then = " and clears its items" if clear else ""
            raise SyncError(f"Revoked {label}, but sync is stalled at {pulled.stalled}. The next sync rotates the keys{then}.")
        self._rotate({target})
        landed = self.roster.entry(target)["removed"]["clear"]
        self.compact(force=True)
        return Removal(cleared=landed, recovery_code_due=self.recovery_code_due())

    def recovery_code_due(self):
        since = self.keyring.recovery["since"]
        return any(e["removed"] and e["removed"]["in_version"] > since for e in self.roster.entries)

    @_locked
    def new_recovery_code(self):
        """Replace the recovery code through a rotation. Returns the new code."""
        self._member()
        if self.pull().stalled:
            raise SyncError("Sync is stalled, so the recovery code cannot change now. `superhistory sync status` shows why.")
        code = self._rotate(new_recovery=True)
        self.compact(force=True)
        return code

    @_locked
    def recover(self, code, show):
        """Rebuild from the recovery code on a freshly signed-in computer, then revoke every other computer, and record
        this one as the group's first member, once the person approves that in the browser, which `show` points them to.
        Returns the new recovery code."""
        try:
            secret = keys.parse_recovery_code(code)
        except ValueError:
            raise SyncError("That is not a recovery code.") from None
        rident, rtag = keys.recovery_keys(secret)
        epoch, version, blob = self.relay.recovery()
        data = pack.open_tagged(rtag, blob, _recovery_aad(epoch, version), identity=rident)
        ring, head = keys.KeyRing.from_json(data["keyring"]), data["roster"]
        st = self.relay.state()
        if (ring.newest, head["version"]) != (epoch, version) or (st["epoch"], st["roster_version"]) != (epoch, version):
            raise Refused("the recovery envelope is older than the relay's state")
        newest = self.relay.packs(after=max(st["head"] - 1, 0), limit=1)
        if newest and (newest[0]["epoch"] not in ring.epochs):
            raise Refused("the key ring cannot open the newest pack")
        if newest:
            keys.open_with([ring.identity(newest[0]["epoch"])], self.relay.pack(newest[0]["seq"]))
        self._require_keys()
        self._anchor(ring)  # the account's roster head anchors trust; the recovery envelope's ring only opens links
        if self.prefix in self._removed().cleared:
            raise SyncError("This computer was removed with its items cleared, so it cannot recover the group as the "
                            "same install. Nothing was revoked.")

        def pull_or_stop():
            stalled = self.pull().stalled
            if stalled:
                raise SyncError(f"Recovery stopped at {stalled}.")

        pull_or_stop()  # rebuild and check the history before revoking anyone
        others = any(d["device_id"] != self.device_id and not d["revoked_on"] for d in self.app.devices(self.app_token))
        # Recovery restores everyone's history, so the others' items are kept.
        self._bootstrap(show, "Another computer is still in the sync group, so recovery stopped. Remove it on the "
                              "website, then run `superhistory sync recover` again.", {"do": "recover"} if others else {"do": "bootstrap"})
        pull_or_stop()  # packs that arrived before the revocations took effect
        ring, ro, listed = self.keyring, self.roster, self._listed()
        version, epoch = ro.version + 1, ring.newest + 1
        # It clears the computers it revoked itself, none, and any the account removed earlier with a choice to clear.
        targets, choices = {e["device_id"] for e in ro.active()}, self._removals(listed)
        if any(choices.get(d, False) is None for d in targets):
            raise SyncError("The account removed a computer without saying whether to keep what it read, so recovery "
                            "stopped. Nothing was rotated.")
        entries = self._removing(ro, targets, version, {d for d in targets if choices.get(d)}, listed) + [
            self._my_entry(version)]
        new_code, new_secret = keys.new_recovery_code()
        nident, ntag = keys.recovery_keys(new_secret)
        new_ring = ring.with_epoch(epoch, keys.new_identity()).with_recovery(keys.recipient_of(nident), ntag, version)
        new_ro = roster.Roster(roster.new_link(ro, epoch, self.device_id, entries, new_ring))
        envelope = roster.seal_envelope(new_ring, new_ro, new_ro.entry(self.device_id), self.device_id, self.paths.device_ssh)
        self.relay.rotate(ring.newest, ro.version, roster.seal_link(new_ro.link, self.paths.device_ssh, new_ring.recipient(epoch)),
                          [(self.device_id, envelope)], self._recovery_envelope(new_ring, new_ro), recovery=True)
        state.put(self.db, "joined_behind", None)
        self._trust(Proven.authored(new_ring, new_ro))
        self.run_once()
        return new_code

    def run_once(self, wait=HOOK_LOCK_WAIT):
        """One sync: pull, finish any removal, push, compact when due. The scheduled run calls it as it is: when another
        sync holds the store past a short wait, it logs why and returns a report marked busy, raising nothing, so its next
        run catches up. `superhistory sync` passes a longer wait and says why it stopped."""
        try:
            with self._exclusive(wait):
                return self._run_once()
        except Busy:
            log.info("another sync is running on this store, so this run did nothing; the next run catches up")
            return Report(busy=True)

    def _run_once(self):
        report = self.pull()
        self._assert_members()
        if self._finish_removals(report):
            report.stalled = report.stalled or "a removed computer still holds the current key, so nothing was sent"
            return report
        self._settle_accounts()
        if not (report.stalled and state.get(self.db, "adopting")):
            report.sent = self.push()
        self._check_recovery_envelope()
        if self._compaction_due(self.relay.state()):
            report.compacted = self.compact(force=True)
        return report

    def _settle_accounts(self):
        """Before anything is sealed or sent: refuse when another active computer reads an online account this one's
        config names, and otherwise bring this computer's entry in line with its config, one link that changes only
        its own `accounts`. A link another computer wrote first is read before trying again, and a clash it brings
        is refused."""
        mine = roster.claims(self.accounts)
        for _ in range(3):
            ro, ring = self.roster, self.keyring
            held = ro.holders(mine, besides=self.device_id)
            if held:
                raise SyncError(self._taken_message(held, "run `superhistory sync` again"))
            me = ro.entry(self.device_id)
            if me["accounts"] == mine:
                return
            new_ro = roster.Roster(roster.new_link(
                ro, ro.epoch, self.device_id, [{**e, "accounts": mine} if e is me else e for e in ro.entries], ring))
            self._confirm_with_app(new_ro)
            self._may_write_next_link()
            try:
                self.relay.put_roster(ro.version, roster.seal_link(new_ro.link, self.paths.device_ssh, ring.recipient(ro.epoch)),
                                      [], self._recovery_envelope(ring, new_ro))
            except relay.RelayError as exc:
                if exc.status == 409:
                    self._refresh()
                    continue
                raise
            self._trust(Proven.authored(ring, new_ro))
            return
        raise SyncError("Another computer kept changing the group. Try again.")

    def _assert_members(self):
        """Record with the app, as a member, every computer active in this computer's trusted roster that the app does
        not list yet, so a crash between a roster change and this call heals on the next sync of any member. Only a
        member may add one, adding one again changes nothing, and only computers this computer already trusts are named.
        When the app cannot be reached, the next sync tries again."""
        try:
            listed = self.app.members(self.app_token)
            if self.device_id not in listed:
                return
            missing = {e["device_id"] for e in self.roster.active()} - listed
            if missing:
                missing -= self._revoked()
            for device_id in sorted(missing):
                try:
                    self.app.add_member(self.app_token, device_id)
                except relay.RelayError as exc:
                    if exc.status != 404:  # revoked since it was listed
                        raise
        except relay.TransportError:
            pass
        except relay.RelayError as exc:
            if exc.status < 500:
                raise

    def _check_recovery_envelope(self):
        """Members reseal the recovery envelope when its tag or the state it names is wrong."""
        epoch, version, blob = self.relay.recovery()
        ring, ro = self.keyring, self.roster
        if (epoch, version) == (ring.newest, ro.version) and len(blob) > 32 and keys.tag_ok(
                ring.recovery["tag"], _recovery_aad(epoch, version) + blob[32:], blob[:32]):
            return True
        try:
            self.relay.put_recovery(ro.version, self._recovery_envelope(ring, ro))
        except relay.RelayError as exc:
            if exc.status != 409:
                raise
        return False

    def _compaction_due(self, st):
        snap = st["snapshot"]
        parts = self.relay.snapshot_parts(snap["id"]) if snap else []
        if self.keyring.newest > 1 and max((p["epoch"] for p in parts), default=0) < self.keyring.newest:
            return True  # after a rotation, until a snapshot exists at the new epoch
        return (st["pack_bytes"] > max(2 * sum(p["size"] for p in parts), COMPACT_FLOOR)
                or st["head"] - (snap["through"] if snap else 0) > COMPACT_PACKS)

    @_locked
    def compact(self, force=False):
        """Write the whole replica as a snapshot, announce it in this computer's chain, and make it current."""
        self._member()
        self._seal_allowed()
        if not force and not self._compaction_due(self.relay.state()):
            return False
        if self.pull().stalled or state.count_parked(self.db) or state.count_outbox(self.db):
            return False
        if records.changes(self.db, self.prefix):
            return False  # a snapshot holds the store as it is, so an edit not sent yet would arrive under its old version
        self._seal_allowed()  # a removal can land during the pull, so the app is asked again before the snapshot is sealed
        st = self.relay.state()
        through = state.get(self.db, "cursor", 0)
        if through != st["head"] or self._signing_head() != state.head(self.db, self.device_id):
            return False  # this computer's own packs must be back before its announcement takes the next dseq
        heads = {a: [d, h] for a, (d, h) in state.heads(self.db).items()}
        heads.setdefault(self.device_id, [0, ""])
        groups, size = [[]], 0
        for rec in records.replica(self.db):
            line = records.to_json(rec)
            if groups[-1] and size + len(line) > PACK_BUDGET:
                groups.append([])
                size = 0
            groups[-1].append(line)
            size += len(line)
        sid, epoch = str(uuid.uuid4()), self.keyring.newest
        plains = [self._sign(None, None, lines, {"id": sid, "through": through, "part": i, "parts": len(groups),
                                                 "heads": heads}) for i, lines in enumerate(groups)]
        # Asked once more after the last relay request and after reading what the snapshot holds, right before it is
        # sealed: a removal that landed since then finishes first, and this snapshot, under the old key, never leaves.
        if self._revoked_members():
            self._seal_allowed()
            return False
        try:
            for plain in plains:
                self.relay.put_pack(str(uuid.uuid4()), epoch, pack.seal(plain, self.keyring.recipient(epoch)), snapshot=sid)
        except relay.RelayError as exc:
            if exc.status == 409:
                return False
            raise
        announce = records.Record("snapshot", "", "", self._now_ms(), {"id": sid, "through": through, "parts": len(groups),
                                                                       "manifest": pack.manifest(plains)})
        with state.tx(self.db):
            state.add_outbox(self.db, [records.to_json(announce)])
        self._sign_next()
        self._upload_pending()
        try:
            self.relay.compact(sid, through, len(groups), st["snapshot"]["id"] if st["snapshot"] else None)
        except relay.RelayError as exc:
            if exc.status in (400, 409):
                return False
            raise
        return True

    def status(self):
        self._member_or_recovering()
        st = self.relay.state()
        listed = self._listed()
        revoked, reach = self._revoked(listed), self._reach(listed)
        problems = []
        problem = state.get(self.db, "problem")
        if problem:
            problems.append(f"Sync is stopped at {problem['at']}: {problem['reason']}.")
        fork = state.get(self.db, "fork")
        if fork:
            problems.append(f"The relay showed device {fork['author']} a different roster at version {fork['version']}.")
        if st["group"] and st["roster_version"] < self.roster.version:
            problems.append("The relay shows an older roster than this computer trusts.")
        held = self.roster.holders(roster.claims(self.accounts), besides=self.device_id)
        if held:
            problems.append(self._taken_message(held, "run `superhistory sync` again") + " Nothing is sent from here until then.")
        if st.get("rotation_due"):
            problems.append("A removed computer still holds keys. The next sync rotates them.")
        if self.recovery_code_due():
            problems.append("A removed computer still knows the recovery code. Run `superhistory sync recovery-code --new`.")
        # Named while the chain is still short, read now, so a pull that brought the packs and then stopped on
        # something else does not leave the notice standing.
        problems += [missing_notice((self.roster.entry(d) or {}).get("label", "another computer"))
                     for d in state.get(self.db, "missing", [])
                     if d in listed and reach.get(d, 0) > state.head(self.db, d)[0]]
        return {
            "label": self.label, "prefix": self.prefix, "epoch": self.keyring.newest, "roster_version": self.roster.version,
            "computers": [{"label": e["label"], "device_id": e["device_id"], "this": e["device_id"] == self.device_id,
                           "removed": e["removed"] is not None, "revoked": e["device_id"] in revoked}
                          for e in self.roster.entries],
            "reads": sorted(self.accounts),
            "behind": max(st.get("head", 0) - state.get(self.db, "cursor", 0), 0),
            "waiting": state.count_outbox(self.db), "problems": problems}

    def doctor(self):
        """(check, ok, detail) for the two programs, key file modes, and devices that share a key."""
        out = []
        for prog in ("age", "age-keygen"):
            try:
                proc = subprocess.run([prog, "--version"], capture_output=True)
                out.append((prog, proc.returncode == 0, proc.stdout.decode().strip()))
            except FileNotFoundError:
                out.append((prog, False, "not installed"))
        with tempfile.TemporaryDirectory(prefix="superhistory-sync-") as tmp:
            probe = os.path.join(tmp, "probe")
            try:
                keys.make_device_keys(probe + ".age", probe + ".ssh")
                sig = keys.sign(probe + ".ssh", pack.NS_PACK, b"probe")
                ok = keys.verify(self.device_id, keys.ssh_public(probe + ".ssh"), pack.NS_PACK, b"probe", sig)
                out.append(("ssh-keygen -Y", ok, "signs and verifies" if ok else "cannot verify"))
            except keys.ToolError as exc:
                out.append(("ssh-keygen -Y", False, str(exc)))
        for path in (self.paths.device_age, self.paths.device_ssh, self.paths.keyring, self.paths.login):
            if os.path.exists(path):
                mode = os.stat(path).st_mode & 0o777
                out.append((os.path.basename(path), mode == 0o600, oct(mode)))
        if self.roster:
            seen, shared = {}, []
            for e in self.roster.entries:
                for k in (e["ssh_key"], e["age_recipient"]):
                    if k in seen:
                        shared.append(f"{seen[k]} and {e['label']}")
                    seen[k] = e["label"]
            out.append(("one key per device", not shared, "; ".join(shared) or "every device has its own keys"))
        return out
