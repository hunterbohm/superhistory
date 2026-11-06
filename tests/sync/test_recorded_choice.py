"""Every computer applies the keep-or-delete choice the app recorded with a revocation, whatever roster a relay shows
it. A relay that hides a removal can make a computer wait, never delete what the person kept nor bring back what they
asked to delete. When the relay serves a link some device confirmed, every computer adopts it by its hash rather than
forging another at that version; when the relay withholds it, a computer that just joined waits, and any other writes
its own link carrying the recorded choice, which still cannot delete or restore."""
import contextlib
import copy
import threading
import unittest
from unittest import mock

from kb import store
from kb.sync import keys, roster, state
from kb.sync.client import SyncClient, SyncError

from .support import EMAIL, World

RELAY_STATE = ("epoch", "roster_version", "roster", "envelopes", "recovery", "packs", "snapshot", "next_seq", "applied")


class RecordedChoiceTest(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.user = self.world.fake.user_of(EMAIL)

    def tearDown(self):
        self.world.close()

    def fresh(self):
        self.world.close()
        self.world = World()
        self.user = self.world.fake.user_of(EMAIL)

    def relay_state(self):
        group = self.world.fake.group(EMAIL)
        return {k: copy.deepcopy(getattr(group, k)) for k in RELAY_STATE}

    def serve(self, saved):
        """The relay shows `saved` again: the group as it was, before anything after it."""
        group = self.world.fake.group(EMAIL)
        for k, v in saved.items():
            setattr(group, k, copy.deepcopy(v))

    def confirmed(self, computer, version):
        return self.world.fake.roster_points.get(self.user, {}).get((computer.client.device_id, version))

    def revoked_as_the_pull_begins(self, reader, device_id):
        """The relay answers `reader`'s next state as the roster and key it already holds, which a relay may, and the
        app's next device list still shows `device_id` unrevoked: its revocation lands just after that read. So the
        pull starts with a removal set that leaves `device_id` out, and finds the change only by opening something
        under a key it lacks."""
        epoch, version, seen = reader.client.keyring.newest, reader.client.roster.version, set()

        def delayed(method, path, status, out):
            if method == "GET" and path == "/sync/state" and "state" not in seen:
                seen.add("state")
                out = {**out, "epoch": epoch, "roster_version": version}
            elif method == "GET" and path == "/v1/devices" and "devices" not in seen:
                seen.add("devices")
                out = {"devices": [{**d, "revoked_on": None, "revoked_clear": None} if d["device_id"] == device_id else d
                                   for d in out["devices"]]}
            return status, out
        self.world.fake.rewrite(delayed)

    def another_sync_after_a_revocation(self, reader, device_id, after):
        """As `another_sync`, where the account first revokes `device_id` with delete."""
        return self.another_sync(reader, after, before=lambda: self.world.fake.revoke(device_id, clear=True))

    def another_sync(self, reader, after, before=lambda: None):
        """Patches for `reader`: once `after` has run, the next time `reader` reads what it has cleared (`_cleared_here`)
        or which computers are removed (`_removed`), `before` runs and then another sync on the same store starts a pull,
        given two seconds before `reader` goes on. Returns the patches and a function that waits for the other sync and
        returns (what it raised, whether it was still waiting after those two seconds, its report)."""
        client, armed, threads, errors, reports, waited = reader.client, [], [], [], [], []

        def other_sync():
            try:
                other = SyncClient.load(reader.paths, transport=self.world.fake, clock=self.world.clock)
                try:
                    reports.append(other.pull())
                finally:
                    other.close()
            except Exception as exc:  # reported by `finish`
                errors.append(exc)

        def then(method):
            def run(*args, **kwargs):
                value = method(*args, **kwargs)
                if armed == [True]:
                    armed.append("done")
                    before()
                    threads.append(threading.Thread(target=other_sync))
                    threads[0].start()
                    threads[0].join(2.0)
                    waited.append(threads[0].is_alive())
                return value
            return run

        def arm(method):
            def run(*args, **kwargs):
                value = method(*args, **kwargs)
                armed[:] = armed or [True]
                return value
            return run

        patches = [mock.patch.object(client, name, side_effect=then(getattr(client, name)))
                   for name in ("_cleared_here", "_removed")]
        patches.append(mock.patch.object(client, after, side_effect=arm(getattr(client, after))))

        def finish():
            for t in threads:
                t.join()
            return errors, waited == [True], reports[0] if reports else None
        return patches, finish

    def test_a_second_sync_waits_for_one_applying_a_pack_then_clears_what_the_removal_deletes(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        item = b.item("notes", "b1", "computer 2's first text")
        for comp in (b, a, c):
            comp.sync()
        b.item("notes", "b1", "computer 2's second text")
        self.assertIsNone(b.sync().stalled)  # computer 3 fetches this while computer 2 is still a member
        patches, finish = self.another_sync_after_a_revocation(c, b.client.device_id, after="_note_roster")
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            c.client.pull()
        errors, waited, other = finish()
        self.assertEqual(errors, [])
        self.assertTrue(waited, "the second sync waits while the first holds the store")
        self.assertIsNone(other.stalled)
        self.assertFalse(c.has("notes", item), "the second sync, once it runs, deletes what the removal cleared")
        self.assertIsNone(c.sync().stalled)
        self.assertFalse(c.has("notes", item), "and no later sync brings it back")

    def test_a_second_sync_waits_for_one_applying_a_snapshot_then_clears_what_the_removal_deletes(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        item = b.item("notes", "b1", "computer 2's first text")
        for comp in (b, a, c):
            comp.sync()
        b.item("notes", "b1", "computer 2's second text")
        self.assertIsNone(b.sync().stalled)
        self.assertTrue(a.client.compact(force=True))  # a snapshot past computer 3 that holds the second text
        self.assertGreater(self.world.fake.group(EMAIL).snapshot[1], state.get(c.db, "cursor", 0))
        patches, finish = self.another_sync_after_a_revocation(c, b.client.device_id, after="_find_announcement")
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            c.client.pull()
        errors, waited, other = finish()
        self.assertEqual(errors, [])
        self.assertTrue(waited, "the second sync waits while the first holds the store")
        self.assertIsNone(other.stalled)
        self.assertFalse(c.has("notes", item), "the second sync, once it runs, deletes what the removal cleared")
        c.sync()
        self.assertFalse(c.has("notes", item), "and no later sync brings it back")

    def test_a_second_sync_waits_for_one_applying_a_snapshot_then_finds_its_work_done(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        a.item("notes", "a0", "a note the snapshot holds")
        self.assertIsNone(a.sync().stalled)
        self.assertTrue(a.client.compact(force=True))
        newer = a.item("notes", "a1", "a note written after the snapshot")
        with mock.patch.object(a.client, "_compaction_due", return_value=False):
            self.assertIsNone(a.sync().stalled)  # a pack past the snapshot
        self.assertGreater(self.world.fake.group(EMAIL).snapshot[1], state.get(c.db, "cursor", 0))
        patches, finish = self.another_sync(c, after="_find_announcement")
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            c.client.pull()
        errors, waited, other = finish()
        self.assertEqual(errors, [])
        self.assertTrue(waited, "the second sync waits while the first holds the store")
        self.assertEqual((other.received, other.stalled), (0, None), "and then finds the first already applied it all")
        self.assertTrue(c.has("notes", newer))
        self.assertIsNone(c.sync().stalled)
        self.assertTrue(c.has("notes", newer), "and no later sync loses it")

    def test_a_removal_trusted_partway_through_a_pull_never_brings_back_what_it_cleared(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        item = b.item("notes", "b1", "computer 2's first text")
        for comp in (b, a, c):
            comp.sync()
        self.assertTrue(c.has("notes", item))
        b.item("notes", "b1", "computer 2's second text")
        self.assertIsNone(b.sync().stalled)  # confirmed to the app; computer 3 has not pulled it
        self.world.fake.revoke(b.client.device_id, clear=True)
        note = a.item("notes", "a1", "computer 1's note")
        with mock.patch.object(a.client, "_compaction_due", return_value=False):  # the relay still holds every pack
            self.assertIsNone(a.sync().stalled)  # removes computer 2 under a new key, then sends a pack under it
        group = self.world.fake.group(EMAIL)
        b_pack, a_pack = [p for p in group.packs if not p["snapshot_id"]][-2:]
        self.assertEqual(a_pack["epoch"], b_pack["epoch"] + 1)
        b_pack["seq"], a_pack["seq"] = a_pack["seq"], b_pack["seq"]  # the relay serves computer 1's pack first
        group.packs.sort(key=lambda p: p["seq"])
        self.revoked_as_the_pull_begins(c, b.client.device_id)

        self.assertIsNone(c.client.pull().stalled)
        self.assertTrue(c.has("notes", note))
        self.assertFalse(c.has("notes", item), "computer 2's later pack does not bring back what its removal cleared")
        self.assertIsNone(c.sync().stalled)
        self.assertFalse(c.has("notes", item), "and no later sync brings it back")

    def test_a_live_computers_mark_on_an_item_a_removal_cleared_partway_through_a_pull_never_waits_forever(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        item = b.item("notes", "b1", "an item computer 2 read")
        for comp in (b, a, c):
            comp.sync()
        self.assertTrue(c.has("notes", item))
        newer = a.item("notes", "a1", "computer 1's newer note")
        store.mark_superseded(a.db, "notes", item, ("notes", newer), a.prefix, "2026-10-05")
        self.world.fake.revoke(b.client.device_id, clear=True)
        with mock.patch.object(a.client, "_compaction_due", return_value=False):  # the relay still holds every pack
            self.assertIsNone(a.sync().stalled)  # removes computer 2 under a new key, then sends its mark under it
        self.revoked_as_the_pull_begins(c, b.client.device_id)

        self.assertIsNone(c.client.pull().stalled)
        self.assertFalse(c.has("notes", item))
        self.assertTrue(c.has("notes", newer))
        self.assertEqual(state.count_parked(c.db), 0, "no record waits for an item a removal deleted, which never arrives")
        self.assertTrue(c.client.compact(force=True), "so nothing stops this computer from compacting")

    def test_a_removal_trusted_while_reading_a_snapshot_never_brings_back_what_it_cleared(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        item = b.item("notes", "b1", "computer 2's first text")
        for comp in (b, a, c):
            comp.sync()
        self.assertTrue(c.has("notes", item))
        b.item("notes", "b1", "computer 2's second text")
        self.assertIsNone(b.sync().stalled)  # computer 3 has not pulled it
        a.client.new_recovery_code()  # a new key, and a snapshot under it past computer 3 that holds the second text
        snap = self.world.fake.group(EMAIL).snapshot
        self.assertGreater(snap[1], state.get(c.db, "cursor", 0), "computer 3 is behind the snapshot, so it reads it")
        self.world.fake.revoke(b.client.device_id, clear=True)
        self.revoked_as_the_pull_begins(c, b.client.device_id)

        c.client.pull()
        self.assertFalse(c.has("notes", item), "the snapshot does not bring back what the recorded delete cleared")
        c.sync()
        self.assertFalse(c.has("notes", item), "and no later sync brings it back")

    def joined_past_a_removal_only_a_removed_computer_confirmed(self, clear):
        """Computer 1 removes computer 2, and computer 1 alone confirms that link before it is itself revoked, so the
        account's head falls back to the link that added computer 4, which is still joining. Returns what the test
        needs: the computers, computer 2's item, the two versions, and the relay with and without the removal."""
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        read = b.item("notes", "b1", "an item computer 2 read")
        for comp in (b, a, c):
            comp.sync()
        j = self.world.computer("computer 4")
        pairing = a.client.add_device()
        j.client.join(pairing.code)
        a.client.approve(pairing, lambda label, words: True, sleep=lambda s: None)
        joined = a.client.roster.version
        c.sync()  # computer 3 confirms the link that adds computer 4
        before = self.relay_state()
        a.client.remove("computer 2", self.world.browser(), clear=clear)
        removal = a.client.roster.version
        self.assertEqual(removal, joined + 1)
        self.world.fake.remove(a.client.device_id, False)
        self.assertEqual(self.world.fake.head(self.user)[0], joined, "the head fell back to computer 3's link")
        return (a, b, c, j), read, joined, removal, before, self.relay_state()

    def test_a_computer_that_just_joined_takes_a_removal_the_relay_serves_though_its_confirmer_is_removed(self):
        for clear in (True, False):
            with self.subTest(clear=clear):
                self.fresh()
                (a, b, c, j), read, joined, removal, before, after = \
                    self.joined_past_a_removal_only_a_removed_computer_confirmed(clear)
                self.assertIsNone(j.client.finish_join(sleep=lambda s: None).stalled)
                self.assertEqual(self.world.fake.group(EMAIL).roster[removal], after["roster"][removal],
                                 "computer 4 took computer 1's removal by its hash and wrote none of its own there")
                self.assertEqual(j.client.roster.entry(b.client.device_id)["removed"]["clear"], clear)
                self.assertEqual(j.has("notes", read), not clear, "computer 4 applies the recorded choice")

    def test_a_computer_that_just_joined_writes_past_a_hidden_removal_only_a_removed_computer_confirmed(self):
        # Requirement 8: once computer 1 is revoked, its confirmation holds nobody back, since it may be one a stolen
        # token made up. So when a relay hides that removal, computer 4 writes its own link there, carrying the app's
        # recorded choice. A computer the relay later shows the real link stops, visibly, which is the withholding
        # fork-stall design.md accepts: neither link can delete or restore anything the account decided.
        for clear in (True, False):
            with self.subTest(clear=clear):
                self.fresh()
                (a, b, c, j), read, joined, removal, before, after = \
                    self.joined_past_a_removal_only_a_removed_computer_confirmed(clear)
                self.serve(before)  # the relay hides computer 1's removal and serves the link that adds computer 4
                self.assertIsNone(j.client.finish_join(sleep=lambda s: None).stalled)
                self.assertNotEqual(self.world.fake.group(EMAIL).roster[removal], after["roster"][removal],
                                    "computer 4 wrote its own link at that version")
                self.assertEqual(j.client.roster.entry(b.client.device_id)["removed"]["clear"], clear)
                self.assertIsNotNone(j.client.roster.entry(a.client.device_id)["removed"])
                self.assertEqual(j.has("notes", read), not clear, "computer 4 applies the recorded choice")

                self.serve(after)  # the relay now shows computer 1's real removal link
                self.assertRegex(c.sync().stalled or "", "not the one the account lists")
                self.assertTrue(c.has("notes", read), "the stopped computer changed nothing")
                self.assertIsNotNone(j.sync().stalled, "computer 4 stops visibly too")

    def test_a_member_behind_a_removal_the_relay_hides_applies_the_recorded_choice(self):
        for clear in (True, False):
            with self.subTest(clear=clear):
                self.fresh()
                (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
                read = b.item("notes", "b1", "an item computer 2 read")
                for comp in (b, a, c):
                    comp.sync()
                before = self.relay_state()
                a.client.remove("computer 2", self.world.browser(), clear=clear)
                removal, a_hash = a.client.roster.version, a.client.roster.hash
                self.world.fake.remove(a.client.device_id, False)
                self.serve(before)  # the relay withholds computer 1's removal, so computer 3 cannot adopt it

                self.assertIsNone(c.sync().stalled)
                self.assertEqual(c.client.roster.entry(b.client.device_id)["removed"]["clear"], clear,
                                 "the removal computer 3 writes carries the recorded choice, never a guess")
                self.assertEqual(c.has("notes", read), not clear)
                self.assertEqual(c.client.roster.version, removal)
                self.assertNotEqual(c.client.roster.hash, a_hash,
                                    "the real link withheld, computer 3 writes its own: the accepted fork, which still "
                                    "cannot delete or restore against the recorded choice")

    def test_recovery_records_keep_and_applies_an_earlier_recorded_delete_the_relay_hides(self):
        (a, b, c), code = self.world.group("computer 1", "computer 2", "computer 3")
        mine, gone = a.item("notes", "a1", "computer 1's note"), c.item("notes", "c1", "computer 3's note")
        for comp in (a, c, b, a):
            comp.sync()
        before = self.relay_state()
        a.client.remove("computer 3", self.world.browser(), clear=True)
        self.world.fake.remove(a.client.device_id, False)
        self.serve(before)
        ids = {comp.name: comp.client.device_id for comp in (a, b, c)}
        for comp in (a, b, c):
            comp.lose_everything()

        d = self.world.computer("computer 4")
        d.client.recover(code, self.world.browser())
        self.assertIs(self.world.fake.devices[ids["computer 2"]]["revoked_clear"], False, "recovery recorded keep")
        self.assertIs(self.world.fake.devices[ids["computer 3"]]["revoked_clear"], True, "the earlier choice stays")
        self.assertTrue(d.has("notes", mine), "what the computers recovery revoked read stays")
        self.assertFalse(d.has("notes", gone), "what the person asked to delete stays deleted")
        self.assertFalse(d.client.roster.entry(ids["computer 2"])["removed"]["clear"])
        self.assertTrue(d.client.roster.entry(ids["computer 3"])["removed"]["clear"])

    def test_a_removal_with_no_recorded_choice_applies_nothing_deletes_nothing_and_is_never_written(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        read = b.item("notes", "b1", "an item computer 2 read")
        b.sync()
        a.sync()  # computer 1 has it, computer 3 does not
        self.world.fake.revoke(b.client.device_id, clear=None)
        version = self.world.fake.group(EMAIL).roster_version

        self.assertRegex(c.client.pull().stalled or "", "without saying whether to keep")
        self.assertFalse(c.has("notes", read), "computer 3 applies none of it")
        self.assertIsNotNone(c.sync().stalled)
        with self.assertRaises(SyncError):
            a.sync()
        self.assertTrue(a.has("notes", read), "computer 1 deletes none of it")
        self.assertEqual(self.world.fake.group(EMAIL).roster_version, version, "no computer writes a change about it")

    def test_a_member_adopts_a_served_confirmed_removal_keeping_its_hash(self):
        # The honest case behind P1 #3: computer 1 removes computer 2 and confirms it, then is revoked, so the head
        # falls back below the removal; the relay still serves computer 1's removal link. Every other computer ADOPTS
        # that link, keeping its hash, rather than forging a different one at the same version.
        (a, b, c, d), _ = self.world.group("computer 1", "computer 2", "computer 3", "computer 4")
        for comp in (a, b, c, d):
            comp.sync()
        a.client.remove("computer 2", self.world.browser(), clear=True)
        removal, a_hash = a.client.roster.version, a.client.roster.hash
        self.world.fake.remove(a.client.device_id, False)
        self.assertEqual(self.world.fake.head(self.user)[0], removal - 1, "the head fell back below the removal")

        self.assertIsNone(c.sync().stalled)
        self.assertEqual(c.client.hashes.get(removal), a_hash, "computer 3 adopted computer 1's link, same hash")
        self.assertIsNone(d.sync().stalled)
        self.assertEqual(d.client.hashes.get(removal), a_hash, "computer 4 adopted the same link, so no fork wedges it")

    def test_a_made_up_confirmation_from_a_revoked_token_is_ignored_and_members_progress(self):
        # A stolen token of a still-active computer confirms a made-up hash at the next version; no such link exists on
        # the relay. Once that computer is revoked its confirmation goes inactive, and the others must still write and
        # trust their own link there, never stall forever on a hash the relay can never produce.
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        v = a.client.roster.version + 1
        self.world.fake.vouch_roster(c.client.device_id, v, "f" * 64)
        self.world.fake.remove(c.client.device_id, True)

        self.assertIsNone(a.sync().stalled, "the made-up hash from a revoked token blocks nothing")
        self.assertGreaterEqual(a.client.roster.version, v, "computer 1 wrote and trusts its own link at the version")
        self.assertNotEqual(a.client.hashes.get(v), "f" * 64)

    def test_a_removal_link_whose_choice_contradicts_the_app_applies_the_apps_choice(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        note = c.item("notes", "c1", "an item computer 3 wrote")
        for comp in (c, a, b):
            comp.sync()
        self.assertTrue(b.has("notes", note))
        self.world.fake.revoke(c.client.device_id, clear=True)
        ro, ac = b.client.roster, a.client
        epoch, version = ro.epoch + 1, ro.version + 1
        ring = ac.keyring.with_epoch(epoch, keys.new_identity())
        kept = {"in_version": version, "head_dseq": 0, "head_hash": "", "clear": False}  # the link lies: it says keep
        entries = [{**e, "removed": kept} if e["device_id"] == c.client.device_id else e for e in ro.entries]
        link = roster.Roster(roster.new_link(ro, epoch, ac.device_id, entries, ring))
        group = self.world.fake.group(EMAIL)
        group.roster[version] = roster.seal_link(link.link, ac.paths.device_ssh, ring.recipient(epoch))
        group.roster_version, group.epoch = version, epoch
        for e in link.active():
            group.envelopes[e["device_id"]] = (epoch, roster.seal_envelope(ring, link, e, ac.device_id, ac.paths.device_ssh))

        self.assertIsNone(b.client.pull().stalled)
        self.assertEqual(b.client.roster.version, version, "the link is trusted; its claimed choice changes nothing")
        self.assertFalse(b.has("notes", note), "the app's recorded delete wins, whatever the link's own clear says")


if __name__ == "__main__":
    unittest.main()
