import json
import os
import shutil
import unittest
from unittest import mock

from kb import store
from kb.sync import keys, pack, records, relay, state
from kb.sync.client import SyncError, _recovery_aad

from .support import EMAIL, World, rows


class GroupTest(unittest.TestCase):
    def setUp(self):
        self.world = World()

    def tearDown(self):
        self.world.close()

    def chains(self, reader):
        """Each author's dseqs on the relay, in seq order. A fork shows up as a repeated or skipped number."""
        out = {}
        for _, h in self.world.opened(reader):
            if h["snapshot"] is None:
                out.setdefault(h["author"], []).append(h["dseq"])
        return out

    def removal_asks(self):
        """The codes asked for an account change after the group was set up."""
        asks = self.world.fake.asks[getattr(self.world, "setup_asks", 0):]
        return [(action, by) for action, by in asks if action["do"] != "enroll"]

    def assert_clean_chains(self, reader):
        for author, dseqs in self.chains(reader).items():
            self.assertEqual(dseqs, list(range(dseqs[0], dseqs[0] + len(dseqs))), f"chain of {author} forked")

    def test_removing_a_computer_on_the_website_ends_all_its_access(self):
        """A computer holds only its own device's tokens and never an account session, so the website's Remove leaves
        it nothing that works: not its tokens, and not the codes it asked for, which ran once and are gone."""
        codes = []

        def keep_codes(method, path, status, out):
            if path == "/api/auth/device/code" and status == 200:
                codes.append(out["device_code"])
            return status, out
        self.world.fake.rewrite(keep_codes)
        (a, b), _ = self.world.group("computer 1", "computer 2")
        saved = keys.load_json(b.client.paths.login)
        self.assertEqual(len(codes), 3, "each sign-in and the start of the group asked for one code")
        for folder, _, files in os.walk(b.config):
            for name in files:
                with open(os.path.join(folder, name), "rb") as fh:
                    held = fh.read()
                self.assertFalse(any(code.encode() in held for code in codes), name)
        self.world.fake.remove(b.client.device_id)
        app = relay.App(self.world.fake)
        tries = {"read the account's computers": lambda: app.devices(saved["app_token"]),
                 "sync": lambda: relay.Relay(self.world.fake, saved["sync_token"]).state(),
                 "ask to remove another computer": lambda: app.ask({"do": "remove", "device_id": a.client.device_id, "clear": False},
                                                                   saved["app_token"]),
                 "ask to recover": lambda: app.ask({"do": "recover"}, saved["app_token"]),
                 "ask to start the group": lambda: app.ask({"do": "bootstrap"}, saved["app_token"])}
        for what, attempt in tries.items():
            with self.subTest(what), self.assertRaises(relay.RelayError) as caught:
                attempt()
            self.assertEqual((caught.exception.status, caught.exception.code), (403, "revoked"))
        for code in codes:
            status, out = self.world.fake.request("POST", "/v1/actions/run", json.dumps({"device_code": code}).encode())
            self.assertEqual((status, json.loads(out)), (400, {"error": "invalid_grant"}), "a code runs once")

    def test_rejoin_keeps_writing_newer_versions(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        key = b.item("notes", "plan", "version one")
        b.sync()
        b.item("notes", "plan", "version two")
        b.sync()
        a.sync()
        self.assertEqual(a.text("notes", key)["rows"], ["version two"])

        a.client.remove("computer 2", self.world.browser(), clear=False)
        b.client.close()
        b.client = None
        shutil.rmtree(os.path.join(b.config, "sync"))
        os.remove(b.store)
        b.offset = -3600.0  # its clock now runs an hour behind the clock that wrote "version two"
        b.login(label="computer 2")
        self.world.pair(a, b)
        self.assertEqual(b.text("notes", key)["rows"], ["version two"], "a rebuilt store gets its own items back")
        b.item("notes", "plan", "version three")
        b.sync()
        a.sync()
        self.assertEqual(a.text("notes", key)["rows"], ["version three"])

    def test_rebuilt_store_pulls_before_its_first_push(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        key = b.item("notes", "plan", "version one")
        b.sync()
        b.item("notes", "plan", "version two")
        b.sync()
        a.sync()
        b.client.close()
        b.client = None
        os.remove(b.store)
        b.offset = -3600.0
        b.open()
        b.client.push()
        self.assertEqual(b.text("notes", key)["rows"], ["version two"])
        b.item("notes", "plan", "version three")
        b.client.push()
        a.sync()
        self.assertEqual(a.text("notes", key)["rows"], ["version three"])

    def test_a_rebuilt_store_does_not_resend_its_old_clear(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        target = a.item("notes", "target", "the item that gets superseded")
        first = a.item("notes", "first", "one replacement")
        a.sync()
        b.sync()
        store.mark_superseded(b.db, "notes", target, ("notes", first), b.prefix, "2026-10-05")
        store.clear_superseded(b.db, "notes", target, b.prefix, "2026-10-06")
        b.sync()
        a.sync()
        store.mark_superseded(a.db, "notes", target, ("notes", first), a.prefix, "2026-10-07")
        a.sync()
        b.sync()
        self.assertEqual(store.superseded_by(b.db, "notes", target), ("notes", first))
        b.client.close()
        b.client = None
        os.remove(b.store)
        b.open()
        b.sync()
        a.sync()
        self.assertEqual(store.superseded_by(a.db, "notes", target), ("notes", first), "the newer mark still wins")
        self.assertEqual(store.superseded_by(b.db, "notes", target), ("notes", first))

    def test_crash_and_replay(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        fake = self.world.fake

        def fail_once(when):
            def hook(method, path, *rest):
                if method == "PUT" and path.startswith("/sync/packs/") and hook.armed:
                    hook.armed = False
                    raise relay.TransportError(f"lost {when} upload")
            hook.armed = True
            (fake.before if when == "before" else fake.after)(hook)

        a.item("notes", "one", "crash while signing")
        with mock.patch.object(pack, "build", side_effect=RuntimeError("crash")):
            self.assertRaises(RuntimeError, a.client.push)
        self.assertEqual(state.count_outbox(a.db), 1, "records reached the outbox before the crash")

        a.item("notes", "two", "crash before upload")
        fail_once("before")
        self.assertRaises(relay.TransportError, a.client.push)
        pending = state.pending(a.db)
        self.assertIsNotNone(pending, "the signed pack waits in the outbox")

        a.sync()
        self.assertEqual(sum(1 for p in fake.group(EMAIL).packs if p["pack_id"] == pending[2]), 1)

        a.item("notes", "three", "response lost after upload")
        fail_once("after")
        before = len(fake.group(EMAIL).packs)
        self.assertRaises(relay.TransportError, a.client.push)
        self.assertEqual(len(fake.group(EMAIL).packs), before + 1, "the relay stored the pack")
        a.sync()
        self.assertEqual(len(fake.group(EMAIL).packs), before + 1, "the retry with the same pack_id stored nothing new")
        self.assertIsNone(state.pending(a.db))

        a.item("notes", "four", "stale epoch after a rotation")
        fail_once("before")
        self.assertRaises(relay.TransportError, a.client.push)
        b.client.new_recovery_code()
        a.sync()

        b.sync()
        for name in ("one", "two", "three", "four"):
            self.assertTrue(b.has("notes", a.key(name)), name)
        self.assert_clean_chains(b)
        self.assertEqual(rows(a.db), rows(b.db))

    def test_crash_while_applying_replays_only_that_pack(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        a.item("notes", "x", "first")
        a.sync()
        a.item("notes", "y", "second")
        a.sync()
        real = state.set_head
        calls = []

        def crash_on_second(*args):
            calls.append(args)
            if len(calls) == 2:
                raise RuntimeError("crash")
            return real(*args)

        with mock.patch.object(state, "set_head", side_effect=crash_on_second):
            self.assertRaises(RuntimeError, b.client.pull)
        self.assertTrue(b.has("notes", a.key("x")))
        self.assertFalse(b.has("notes", a.key("y")), "the crashed pack rolled back as a whole")
        self.assertIsNone(b.sync().stalled)
        self.assertEqual(rows(a.db), rows(b.db))

    def test_pairing_rejects_a_swapped_key(self):
        a = self.world.computer("computer 1")
        a.client.init(self.world.browser())
        b = self.world.computer("computer 2")
        pairing = a.client.add_device()
        b.client.join(pairing.code)
        (slot,) = self.world.fake.slots.values()
        _, other = keys.new_pairing_code()
        attacker = keys.PairingKeys(other)
        slot["join"] = pack.seal_tagged(attacker.join_key, keys.recipient_of(attacker.join_identity), {
            "device_id": b.client.device_id, "prefix": b.prefix, "age_recipient": keys.recipient_of(keys.new_identity()),
            "ssh_key": keys.ssh_public(b.client.paths.device_ssh), "label": "computer 2"})
        asked = []
        with self.assertRaises(keys.Refused):
            a.client.approve(pairing, lambda label, words: asked.append(label) or True, sleep=lambda s: None)
        self.assertEqual(asked, [], "a swapped join never reaches the person")
        self.assertEqual(self.world.fake.group(EMAIL).roster_version, 1)

        pairing = a.client.add_device()
        b.client.join(pairing.code)
        a.client.approve(pairing, lambda label, words: True, sleep=lambda s: None)
        (slot,) = self.world.fake.slots.values()
        stranger = keys.new_identity()
        forged_ring = keys.KeyRing({1: (stranger, keys.recipient_of(stranger))}, a.client.keyring.recovery)
        slot["welcome"] = pack.seal_tagged(attacker.welcome_key, keys.device_recipient(b.client.paths.device_age),
                                           {"keyring": forged_ring.to_json(), "roster": {"version": 2, "hash": "0" * 64}})
        with self.assertRaises(keys.Refused):
            b.client.finish_join(sleep=lambda s: None)
        self.assertIsNone(b.client.roster)

    def test_removed_device_reads_nothing_new(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        a.item("notes", "before", "written before removal")
        a.sync()
        c.sync()
        a.client.remove("computer 3", self.world.browser(), clear=True)
        secret = a.item("notes", "after", "PLANTED written after removal")
        a.sync()
        b.sync()
        self.assertTrue(b.has("notes", secret))

        with self.assertRaises(relay.RelayError) as caught:
            c.client.run_once()
        self.assertEqual((caught.exception.status, caught.exception.code), (403, "revoked"))
        group = self.world.fake.group(EMAIL)
        held = [i for i, _ in c.client.keyring.epochs.values()]
        self.assertEqual(sorted(c.client.keyring.epochs), [1])
        new_packs = [p for p in group.packs if p["epoch"] >= 2]
        self.assertTrue(new_packs)
        for p in new_packs:
            self.assertRaises(keys.Refused, keys.open_with, held, p["blob"])
        self.assertRaises(keys.Refused, keys.open_with, held, group.roster[group.roster_version])
        for _, blob in group.envelopes.values():
            self.assertRaises(keys.Refused, keys.open_file, c.client.paths.device_age, blob)
        self.assertTrue(all(p["epoch"] >= 2 for p in group.packs), "compaction removed every pack c could open")

    def test_uploads_wait_for_a_due_rotation(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        self.world.fake.remove(b.client.device_id)
        self.assertTrue(a.client.relay.state()["rotation_due"], "a removal from the web makes a rotation due")
        a.item("notes", "x", "written while a rotation is due")
        a.client.push()
        group = self.world.fake.group(EMAIL)
        self.assertEqual(group.epoch, 2, "the upload waited for the rotation")
        self.assertEqual(group.packs[-1]["epoch"], 2)
        self.assertIsNotNone(a.client.roster.entry(b.client.device_id)["removed"])

    def test_login_registers_the_device_keys_and_sync_needs_them(self):
        a = self.world.computer("computer 1")
        paths, registered = a.client.paths, self.world.fake.devices[a.client.device_id]
        self.assertEqual((registered["age_recipient"], registered["ssh_key"]),
                         (keys.device_recipient(paths.device_age), keys.ssh_public(paths.device_ssh)))
        os.remove(paths.device_ssh)
        with self.assertRaises(SyncError):
            a.client.init(self.world.browser())

    def test_a_computer_joining_under_keys_the_app_does_not_hold_is_never_added(self):
        a = self.world.computer("computer 1")
        a.client.init(self.world.browser())
        b = self.world.computer("computer 2")
        for path in (b.client.paths.device_age, b.client.paths.device_ssh, b.client.paths.device_ssh + ".pub"):
            os.remove(path)
        keys.make_device_keys(b.client.paths.device_age, b.client.paths.device_ssh)
        version = self.world.fake.group(EMAIL).roster_version
        with self.assertRaises(keys.Refused):
            self.world.pair(a, b)
        self.assertEqual(self.world.fake.group(EMAIL).roster_version, version, "the relay never got the link")

    def test_a_pack_signed_unmasked_that_the_relay_already_holds_is_never_signed_again(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        key = "sk-ant-api03-" + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAb7cDe0f"
        line = records.to_json(records.Record("item", "notes", a.key("waiting"), int(self.world.now * 1000), {
            "ts": "2026-10-01", "title": "Waiting", "meta": {"hash": "h"}, "rows": [key]}))
        with state.tx(a.db):
            state.add_outbox(a.db, [line])
        dseq, prev = a.client._signing_head()
        header = {"epoch": a.client.keyring.newest, "author": a.client.device_id, "dseq": dseq + 1, "prev": prev,
                  "roster_version": a.client.roster.version, "roster_hash": a.client.roster.hash, "snapshot": None}
        plain = pack.build(header, [line], a.client.paths.device_ssh)
        a.client._save_pending([rid for rid, rec in state.unsigned(a.db, 1 << 30) if rec == line], dseq + 1, prev, plain)
        pack_id, epoch = state.pending(a.db)[2], state.pending(a.db)[3]
        a.client.relay.put_pack(pack_id, epoch, pack.seal(plain, a.client.keyring.recipient(epoch)))  # then a crash
        before = len(self.world.fake.group(EMAIL).packs)
        a.client.push()
        self.assertIsNone(state.pending(a.db), "the pack the relay holds is done, not signed again")
        self.assertEqual(len(self.world.fake.group(EMAIL).packs), before)
        self.assertIsNone(b.sync().stalled, "no second pack under one dseq")

    def test_login_only_device_does_not_block_rotation(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        self.world.computer("computer 3")
        a.client.remove("computer 2", self.world.browser(), clear=True)
        self.assertEqual(self.world.fake.group(EMAIL).epoch, 2)

    def test_recovery_code(self):
        (a, b), code = self.world.group("computer 1", "computer 2")
        a.item("notes", "a1", "from the first computer")
        b.item("git", "b1", "from the second computer")
        a.sync()
        b.sync()
        a.sync()
        expected = rows(a.db)
        old = {a.client.device_id, b.client.device_id}
        a.lose_everything()
        b.lose_everything()

        c = self.world.computer("computer 3")
        new_code = c.client.recover(code, self.world.browser())
        self.assertNotEqual(new_code, code)
        self.assertEqual(rows(c.db), expected)
        self.assertEqual({d for d, dev in self.world.fake.devices.items() if dev["revoked"]}, old)
        self.assertEqual(self.removal_asks(), [({"do": "recover"}, c.client.device_id)], "one approval, asked by c")
        self.assertEqual([e["label"] for e in c.client.roster.active()], ["computer 3"])

        d = self.world.computer("computer 4")
        self.world.pair(c, d)
        self.assertEqual(rows(d.db), expected)

    def test_recovery_revokes_nothing_until_the_history_checks_out(self):
        (a, b), code = self.world.group("computer 1", "computer 2")
        a.item("notes", "a1", "from the first computer")
        a.sync()
        a.item("notes", "a2", "the newest pack stays intact")
        a.sync()
        stored = self.world.fake.group(EMAIL).packs[-2]
        stored["blob"] = stored["blob"][:-20] + bytes([stored["blob"][-20] ^ 1]) + stored["blob"][-19:]
        c = self.world.computer("computer 3")
        with self.assertRaises(SyncError):
            c.client.recover(code, self.world.browser())
        self.assertFalse([d for d, dev in self.world.fake.devices.items() if dev["revoked"]])
        self.assertEqual(self.removal_asks(), [], "the browser is asked only once the history checks out")
        self.assertEqual(self.world.fake.group(EMAIL).epoch, 1)

    def test_a_denied_recovery_removes_nobody(self):
        (a, b), code = self.world.group("computer 1", "computer 2")
        c = self.world.computer("computer 3")
        self.world.fake.browser_denies = True
        with self.assertRaises(keys.Unaccepted):
            c.client.recover(code, self.world.browser())
        self.assertFalse([d for d, dev in self.world.fake.devices.items() if dev["revoked"]])
        self.assertEqual(self.world.fake.group(EMAIL).epoch, 1)

    def test_recovery_removes_nobody_when_no_other_computer_is_left(self):
        (a, b), code = self.world.group("computer 1", "computer 2")
        for gone in (a, b):
            self.world.fake.remove(gone.client.device_id)
            gone.lose_everything()
        c = self.world.computer("computer 3")
        c.client.recover(code, self.world.browser())
        self.assertEqual(self.removal_asks(), [({"do": "bootstrap"}, c.client.device_id)],
                         "nobody is left to remove, so it asks only to make this computer the group's first member")
        self.assertEqual([e["label"] for e in c.client.roster.active()], ["computer 3"])

    def test_a_recovery_approved_after_its_computer_s_first_ten_minutes_still_finishes(self):
        """A computer's first ten minutes let it read the group before it is a member. Recovery asks the person to approve
        once it has checked the history, and the approval can come after those minutes, by when the app has revoked the
        others. Running the approved change gives the computer its ten minutes again, so it can finish."""
        for others in (True, False):
            with self.subTest(others=others):
                world = self.world = World()
                self.addCleanup(world.close)
                (a, b), code = world.group("computer 1", "computer 2")
                a.item("notes", "a1", "from the first computer")
                a.sync()
                old = (a.client.device_id, b.client.device_id)
                for gone in (a, b):
                    if not others:
                        world.fake.remove(gone.client.device_id)
                    gone.lose_everything()
                c = world.computer("computer 3")
                world.tick(550)
                c.client.recover(code, lambda uri, user_code: world.tick(60))
                self.assertEqual([e["label"] for e in c.client.roster.active()], ["computer 3"])
                self.assertEqual(c.client.keyring.newest, 2, "the group rotated to a key only computer 3 holds")
                self.assertTrue(all(world.fake.devices[d]["revoked"] for d in old))

    def test_new_recovery_code_rotates(self):
        (a, b), old_code = self.world.group("computer 1", "computer 2")
        group = self.world.fake.group(EMAIL)
        old_envelope = (group.epoch, group.roster_version, group.recovery)
        new_code = a.client.new_recovery_code()
        self.assertNotEqual(new_code, old_code)
        self.assertEqual(group.epoch, 2)

        def opens(code, envelope):
            identity, tag_key = keys.recovery_keys(keys.parse_recovery_code(code))
            epoch, version, blob = envelope
            try:
                return keys.KeyRing.from_json(pack.open_tagged(tag_key, blob, _recovery_aad(epoch, version),
                                                               identity=identity)["keyring"])
            except keys.Refused:
                return None

        current = (group.epoch, group.roster_version, group.recovery)
        self.assertIsNone(opens(old_code, current), "the old code no longer opens the recovery envelope")
        self.assertEqual(opens(new_code, current).newest, 2)
        stale = opens(old_code, old_envelope)
        self.assertEqual(sorted(stale.epochs), [1], "a copy of the old envelope holds only the old epoch")
        later = a.item("notes", "later", "written after the new code")
        a.sync()
        b.sync()
        self.assertTrue(b.has("notes", later))
        self.assertTrue(all(p["epoch"] not in stale.epochs for p in group.packs))



class ResetTest(unittest.TestCase):
    """What design.md says about starting Sync over: clearing the relay's data alone does not let a computer set Sync up
    again, since the account still names the old roster."""

    def test_clearing_the_relay_alone_is_not_enough(self):
        world = World()
        self.addCleanup(world.close)
        (a, b), _ = world.group("computer 1", "computer 2")
        world.fake.groups.pop(world.fake.user_of(EMAIL), None)
        with self.assertRaisesRegex(keys.Refused, "this computer's roster is not the one the account lists"):
            a.client.init(world.browser())


if __name__ == "__main__":
    unittest.main()
