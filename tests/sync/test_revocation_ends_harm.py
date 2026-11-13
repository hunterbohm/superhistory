"""Revoking a device ends any harm its confirmations did (requirement 8), and what a removed computer's pinned head says
is heard. These are the gate 9 re-check's findings, each from its repro."""

import unittest
from unittest import mock

from kb import store
from kb.sync import relay, state
from kb.sync.client import SyncError

from .support import World, marks


class PlantedRosterConfirmationTest(unittest.TestCase):
    """A stolen member token can confirm made-up roster links ahead of the head while its device is a member. Once the
    device is revoked, those confirmations must hold no computer back."""

    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.app = relay.App(self.world.fake)

    def plant(self, thief, versions):
        for v in versions:
            self.app.confirm_roster(thief.client.app_token, v, "%064x" % v)

    def test_a_revoked_thiefs_confirmations_do_not_freeze_a_later_joiner(self):
        # The re-check's sequence: the thief plants v+1..v+5, is revoked, computer 3 joins, and later computer 1 is
        # lost too, leaving computer 3 alone. Computer 3 must still finish removals, send and approve.
        (a, s), _ = self.world.group("computer 1", "computer 2")
        for comp in (a, s):
            comp.sync()
        v = a.client.roster.version
        self.plant(s, range(v + 1, v + 6))
        self.world.fake.remove(s.client.device_id)
        self.assertIsNone(a.sync().stalled)
        j = self.world.computer("computer 3")
        self.world.pair(a, j)
        self.world.fake.remove(a.client.device_id)
        j.item("notes", "j1", "written on computer 3, the only computer left")
        report = j.sync()
        self.assertIsNone(report.stalled)
        self.assertGreater(report.sent, 0, "what computer 3 wrote went out")
        self.assertIsNotNone(j.client.roster.entry(a.client.device_id)["removed"], "computer 3 finished the removal")
        self.world.pair(j, self.world.computer("computer 4"))

    def test_a_next_link_a_member_stands_behind_still_holds_a_joiner_back(self):
        # What the rule is for: a joiner must not write past a next link that a computer still in the account trusts,
        # though the relay hides it. Once only revoked devices stand behind that link, nothing holds the joiner back.
        (a, b), _ = self.world.group("computer 1", "computer 2")
        for comp in (a, b):
            comp.sync()
        j = self.world.computer("computer 3")
        self.world.pair(a, j)
        v = j.client.roster.version
        state.put(j.db, "joined_behind", v)
        self.world.fake.vouch_roster(b.client.device_id, v + 1, "ab" * 32)
        with self.assertRaisesRegex(SyncError, "waits for that change"):
            j.client._may_write_next_link()
        self.world.fake.revoke(b.client.device_id, clear=False)
        j.client._may_write_next_link()


class PlantedChainConfirmationTest(unittest.TestCase):
    """A stolen member token can confirm a made-up pack number far ahead. Afterwards the device's chain always looks
    short, which is named in a notice, but no computer downloads the same snapshot again on every sync for it."""

    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)

    def downloads(self, comp):
        before = len(self.world.fake.log)
        report = comp.sync()
        return report, [e for e in self.world.fake.log[before:] if e[0] == "GET" and e[1].startswith("/sync/packs/")]

    def test_a_made_up_pack_confirmation_does_not_make_every_sync_download_the_snapshot_again(self):
        (a, b, s), _ = self.world.group("computer 1", "computer 2", "computer 3")
        a.item("notes", "a1", "a note on computer 1")
        for comp in (a, b, s):
            comp.sync()
        relay.App(self.world.fake).confirm_chain(s.client.app_token, 1_000_000, "ab" * 32)
        self.world.fake.remove(s.client.device_id)
        a.sync()  # finishes the removal and compacts
        b.sync()  # takes that snapshot once
        for _ in range(3):
            report, fetched = self.downloads(b)
            self.assertEqual(fetched, [], "an idle sync downloads nothing")
            self.assertIsNone(report.stalled)
            self.assertEqual(report.missing, ("computer 3",), "the made-up confirmation is still named")


class PinnedTailTest(unittest.TestCase):
    """A removal pins the last pack of the removed computer that counts. A computer that trusts that pin but never got
    the pack is short of it, as of a confirmation: it takes the snapshot that holds it, and until then says so."""

    def test_a_pinned_pack_the_relay_withholds_is_named_and_arrives_with_the_next_snapshot(self):
        world = World()
        self.addCleanup(world.close)
        (a, b, c), _ = world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        last = c.item("notes", "c-last", "computer 3's last note, sent before its removal")
        with mock.patch.object(c.client.app, "confirm_chain", side_effect=relay.TransportError("lost")):
            with self.assertRaises(relay.TransportError):
                c.client.push()  # the pack reaches the relay, its confirmation never reaches the app
        b.sync()
        self.assertTrue(b.has("notes", last))
        c_id = c.client.device_id
        world.fake.remove(c_id)
        with mock.patch.object(b.client, "_compaction_due", return_value=False):
            b.item("notes", "b1", "computer 2 writes on")
            self.assertIsNone(b.sync().stalled)  # finishes the removal, pinning computer 3 at that last pack
        pin = b.client.roster.entry(c_id)["removed"]["head_dseq"]
        hidden = [seq for seq, h in world.opened(b) if h["author"] == c_id and h["dseq"] == pin]
        listing = a.client.relay.packs
        with mock.patch.object(a.client.relay, "packs",
                               side_effect=lambda after, limit=200: [p for p in listing(after=after, limit=limit)
                                                                     if p["seq"] not in hidden]):
            report = a.sync()
            self.assertEqual(a.client.roster.entry(c_id)["removed"]["head_dseq"], pin, "computer 1 trusts the pin")
            self.assertFalse(a.has("notes", last))
            self.assertEqual(report.missing, ("computer 3",), "computer 1 says the pinned pack has not arrived")
            self.assertIn("computer 3", " ".join(a.client.status()["problems"]))
            self.assertTrue(b.client.compact(force=True))
            report = a.sync()
        self.assertTrue(a.has("notes", last), "the pinned pack arrives with the snapshot")
        self.assertEqual(report.missing, ())


class WaitingMarkTest(unittest.TestCase):
    """A mark waits for its item. When the computer that made it is removed with what it read deleted while a pull is
    under way, the mark may apply as its item arrives, but it does not outlive that pull."""

    def test_a_waiting_mark_from_a_computer_cleared_during_the_pull_is_gone_when_the_pull_ends(self):
        world = World()
        self.addCleanup(world.close)
        (a, b, x), _ = world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, x):
            comp.sync()
        target, newer = b.key("t"), b.key("u")
        store.mark_superseded(x.db, "notes", target, ("notes", newer), x.prefix, "2026-10-05")
        x.sync()  # computer 3's mark goes out before the item it names
        b.item("notes", "t", "an older note")
        b.item("notes", "u", "the note that replaces it")
        b.sync()
        item_packs = {seq for seq, h in world.opened(a) if h["author"] == b.client.device_id}

        def revoke_on_fetch(method, path, headers):
            if method == "GET" and path.startswith("/sync/packs/") and int(path.rsplit("/", 1)[1]) in item_packs:
                if not world.fake.devices[x.client.device_id]["revoked"]:
                    world.fake.revoke(x.client.device_id, clear=True)  # removed, deleting what it read, mid-pull
        world.fake.before(revoke_on_fetch)
        report = a.client.pull()
        self.assertIsNone(report.stalled)
        self.assertTrue(a.has("notes", target))
        self.assertEqual([m for m in marks(a.db) if m[2] == x.prefix], [], "computer 3's mark did not outlive the pull")


if __name__ == "__main__":
    unittest.main()
