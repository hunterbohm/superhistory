import contextlib
import io
import json
import os
import shutil
import unittest
import uuid
from unittest import mock

from kb import cli as kbcli, ids, store
from kb.sync import cli, keys, pack, records, relay, roster, state
from kb.sync.client import SyncError

from .support import EMAIL, World, marks, put_item, put_summary, rows


def wrote(comp, install):
    """The kinds of every row and mark in `comp`'s store that `install` wrote, by `ids.owner`."""
    found = sorted(k for k, sid in comp.db.execute("SELECT kind, source_id FROM docs") if ids.owner(sid) == install)
    return found + ["mark" for (by,) in comp.db.execute("SELECT marked_by FROM supersedes") if by == install]


def others(comp, install):
    """Every row and mark in `comp`'s store that `install` did not write."""
    return ([r for r in rows(comp.db) if ids.owner(r[2]) != install],
            [m for m in marks(comp.db) if m[2] != install])


def no_compaction(method, path, headers):
    if method == "POST" and path == "/sync/compact":
        raise relay.TransportError("held back")


class RemovalTest(unittest.TestCase):
    def setUp(self):
        self.world = World()

    def tearDown(self):
        self.world.close()

    def three(self):
        """Computer 3 reads a note, an item over SSH and a long item with a summary, and marks an item of computer 1.
        Computer 1 marks computer 3's note. Every computer holds all of it."""
        (a, b, c), code = self.world.group("computer 1", "computer 2", "computer 3")
        self.a1, self.a2 = a.item("notes", "a1", "computer 1's note"), a.item("notes", "a2", "its newer note")
        b.item("git", "b1", "computer 2's commit")
        self.c1 = c.item("notes", "c1", "computer 3's note")
        put_item(c.db, "notes", f"{c.prefix}@laptop:remote.md", "2026-10-01", "Remote", "read over ssh", {})
        long = c.item("notes", "long", "".join(f"paragraph {i} about the relay\n" for i in range(300)))
        put_summary(c.db, "notes", long, c.text("notes", long)["meta"]["hash"], [("atom", "Fact", "a fact")])
        for comp in (a, b, c):
            comp.sync()
        store.mark_superseded(c.db, "notes", self.a1, ("notes", self.a2), c.prefix, "2026-10-05")
        store.mark_superseded(a.db, "notes", self.c1, ("notes", self.a1), a.prefix, "2026-10-05")
        for comp in (c, a, b, c, a, b):
            comp.sync()
        for comp in (a, b):
            self.assertEqual(sorted(wrote(comp, c.prefix)), ["atom", "doc", "doc", "mark"] + ["raw"] * len(
                c.text("notes", long)["rows"]))
        return a, b, c, code

    def test_a_denied_removal_changes_nothing(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        self.world.fake.browser_denies = True
        with self.assertRaises(keys.Unaccepted):
            a.client.remove("computer 3", self.world.browser(), clear=True)
        self.assertIsNone(self.world.fake.devices[c.client.device_id]["revoked"])
        self.assertEqual(state.get(a.db, "clear_on_removal", {}), {})
        self.assertEqual(self.world.fake.group(EMAIL).epoch, 1)

    def test_a_removal_the_app_does_not_report_is_not_acted_on(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        self.world.fake.rewrite(lambda method, path, status, out: (
            (status, {**out, "revoked": []}) if path == "/v1/actions/run" else (status, out)))
        with self.assertRaises(relay.RelayError) as caught:
            a.client.remove("computer 3", self.world.browser(), clear=True)
        self.assertEqual((caught.exception.status, caught.exception.code), (502, "bad_answer"))
        self.assertEqual(state.get(a.db, "clear_on_removal", {}), {})
        self.assertEqual(self.world.fake.group(EMAIL).epoch, 1)

    def test_clearing_deletes_what_it_wrote_on_every_computer_and_nothing_else(self):
        a, b, c, _ = self.three()
        kept = {comp.name: others(comp, c.prefix) for comp in (a, b)}
        self.assertIn(self.c1, [m[1] for m in kept["computer 1"][1]], "computer 1's mark on computer 3's note")

        removal = a.client.remove("computer 3", self.world.browser(), clear=True)
        self.assertTrue(removal.cleared)
        self.assertEqual(wrote(a, c.prefix), [])
        self.assertEqual(others(a, c.prefix), kept["computer 1"], "nothing computer 3 did not write changes")
        self.assertIsNone(b.sync().stalled)
        self.assertEqual(wrote(b, c.prefix), [])
        self.assertEqual(others(b, c.prefix), kept["computer 2"])
        self.assertTrue(b.client.roster.entry(c.client.device_id)["removed"]["clear"])
        self.assertEqual(store.superseded_by(b.db, "notes", self.a1), None, "computer 3's mark is gone")

        group = self.world.fake.group(EMAIL)
        parts = [pack.parse(keys.open_with([a.client.keyring.identity(p["epoch"])], p["blob"]))
                 for p in group.packs if p["snapshot_id"] == group.snapshot[0]]
        self.assertTrue(parts, "the removal compacted")
        self.assertEqual([r for pk in parts for r in pk.records if r.writer == c.prefix], [],
                         "the new snapshot carries nothing computer 3 wrote, not even its keys")

    def test_a_crash_before_the_clear_commits_is_caught_up(self):
        a, b, c, _ = self.three()
        a.client.remove("computer 3", self.world.browser(), clear=True)
        real = records.clear

        def crash_after_deleting(db, install):
            real(db, install)
            raise RuntimeError("crash")
        with mock.patch.object(records, "clear", crash_after_deleting):
            self.assertRaises(RuntimeError, b.client.pull)
        self.assertTrue(b.client.roster.entry(c.client.device_id)["removed"]["clear"], "the roster was saved")
        self.assertTrue(wrote(b, c.prefix), "the deletes rolled back as a whole")
        self.assertIsNone(b.sync().stalled)
        self.assertEqual(wrote(b, c.prefix), [])

    def test_keep_items_leaves_everything(self):
        a, b, c, _ = self.three()
        before = {comp.name: (rows(comp.db), marks(comp.db)) for comp in (a, b)}
        removal = a.client.remove("computer 3", self.world.browser(), clear=False)
        self.assertFalse(removal.cleared)
        b.sync()
        for comp in (a, b):
            self.assertEqual((rows(comp.db), marks(comp.db)), before[comp.name])
        self.assertFalse(b.client.roster.entry(c.client.device_id)["removed"]["clear"])
        d = self.world.computer("computer 4")
        self.assertIsNone(self.world.pair(a, d).stalled)
        self.assertEqual(rows(d.db), rows(a.db))
        self.assertEqual(marks(d.db), marks(a.db))

    def test_a_lagging_computer_checks_the_chain_but_applies_nothing_of_it(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        a1 = a.item("notes", "a1", "computer 1's note")
        early = c.item("notes", "early", "computer 2 gets this one")
        for comp in (a, c, b):
            comp.sync()
        self.assertTrue(b.has("notes", early))
        later = [c.item("notes", "c2", "second")]
        c.sync()
        later.append(f"{c.prefix}@laptop:remote.md")
        put_item(c.db, "notes", later[-1], "2026-10-01", "Remote", "read over ssh", {})
        c.sync()
        store.mark_superseded(c.db, "notes", a1, ("notes", early), c.prefix, "2026-10-05")
        c.sync()
        a.sync()
        fake = self.world.fake
        fake.before(no_compaction)
        with self.assertRaises(relay.TransportError):
            a.client.remove("computer 3", self.world.browser(), clear=True)
        fake.before_hooks.clear()

        cid, group = c.client.device_id, fake.group(EMAIL)
        pinned = a.client.roster.entry(cid)["removed"]["head_dseq"]
        pending = [p for p in group.packs if any(seq == p["seq"] and h["author"] == cid for seq, h in self.world.opened(b))
                   and p["seq"] > state.get(b.db, "cursor", 0)]
        self.assertEqual(len(pending), 3, "computer 2 still has computer 3's packs to apply, up to its pinned head")
        self.assertEqual(state.head(b.db, cid)[0] + 3, pinned)

        group.packs.remove(pending[1])
        cursor = state.get(b.db, "cursor", 0)
        self.assertIn("removed computer", b.client.pull().stalled or "", "a gap in its chain still stalls sync")
        self.assertFalse(b.has("notes", early), "accepting the removal cleared what computer 2 already had")
        self.assertEqual(state.get(b.db, "cursor", 0), cursor)

        group.packs = sorted(group.packs + [pending[1]], key=lambda p: p["seq"])
        self.assertIsNone(b.client.pull().stalled)
        self.assertEqual(state.head(b.db, cid)[0], pinned, "its chain was checked to its pinned head")
        self.assertEqual(state.get(b.db, "cursor"), max(p["seq"] for p in group.packs))
        self.assertEqual(wrote(b, c.prefix), [])
        self.assertIsNone(store.superseded_by(b.db, "notes", a1))
        self.assertEqual(state.count_parked(b.db), 0)

    def test_a_computer_that_joins_from_an_older_snapshot_gets_none_of_it(self):
        a, b, c, _ = self.three()
        self.assertTrue(a.client.compact(force=True))
        older = self.world.fake.group(EMAIL).snapshot
        self.world.fake.before(no_compaction)
        with self.assertRaises(relay.TransportError):
            a.client.remove("computer 3", self.world.browser(), clear=True)
        self.world.fake.before_hooks.clear()
        self.assertEqual(self.world.fake.group(EMAIL).snapshot, older, "the current snapshot still holds computer 3's items")

        d = self.world.computer("computer 4")
        self.assertIsNone(self.world.pair(a, d).stalled)
        self.assertEqual(wrote(d, c.prefix), [])
        self.assertEqual(rows(d.db), rows(a.db))
        self.assertEqual(marks(d.db), [m for m in marks(a.db) if m[1] != self.c1],
                         "computer 1's mark on a cleared item has nothing to mark on a new computer")
        self.assertEqual(state.count_parked(d.db), 0, "and it does not wait for an item that never comes")

        a.sync()
        e = self.world.computer("computer 5")
        self.assertIsNone(self.world.pair(a, e).stalled)
        self.assertEqual(wrote(e, c.prefix), [])
        self.assertEqual(rows(e.db), rows(a.db))

    def test_recovery_keeps_everything_and_an_earlier_clear_stays(self):
        a, b, c, code = self.three()
        b_ssh = f"{b.prefix}@laptop:b.md"
        put_item(b.db, "notes", b_ssh, "2026-10-01", "B", "computer 2 read this over ssh", {})
        b.sync()
        a.client.remove("computer 3", self.world.browser(), clear=True)
        b.sync()
        a.sync()
        expected, old = rows(a.db), (a.client.device_id, b.client.device_id)
        self.assertTrue(any(r[2] == b_ssh for r in expected))
        a.lose_everything()
        b.lose_everything()

        r = self.world.computer("computer 4")
        r.client.recover(code, self.world.browser())
        self.assertEqual(rows(r.db), expected, "recovery keeps every item of the computers it removes")
        self.assertEqual(wrote(r, c.prefix), [], "a computer cleared before recovery stays cleared")
        for device_id in old:
            self.assertFalse(r.client.roster.entry(device_id)["removed"]["clear"])
        d = self.world.computer("computer 5")
        self.world.pair(r, d)
        self.assertEqual(rows(d.db), expected)

    def test_a_removal_finished_later_keeps_the_choice(self):
        a, b, c, _ = self.three()
        junk = self.world.put_raw(keys.seal([keys.recipient_of(keys.new_identity())], b"junk"), 1)
        with self.assertRaises(SyncError):
            a.client.remove("computer 3", self.world.browser(), clear=True)
        group = self.world.fake.group(EMAIL)
        group.packs = [p for p in group.packs if p["seq"] != junk]
        self.assertIsNone(a.sync().stalled)
        self.assertTrue(a.client.roster.entry(c.client.device_id)["removed"]["clear"])
        b.sync()
        self.assertEqual(wrote(b, c.prefix), [])

    def test_a_removal_from_the_web_applies_the_choice_made_there(self):
        for clear in (False, True):
            with self.subTest(clear=clear):
                self.world.close()
                self.world = World()
                a, b, c, _ = self.three()
                self.world.fake.remove(c.client.device_id, clear)
                self.assertIsNone(b.sync().stalled)
                self.assertEqual(b.client.roster.entry(c.client.device_id)["removed"]["clear"], clear)
                a.sync()
                for comp in (a, b):
                    self.assertEqual(bool(wrote(comp, c.prefix)), not clear)

    def test_another_computer_that_removes_it_first_applies_the_choice_recorded_with_the_revocation(self):
        a, b, c, _ = self.three()

        def b_rotates_first(method, path, headers):
            if (method, path) == ("POST", "/sync/rotate") and b_rotates_first.armed and \
                    headers.get("authorization") == f"Bearer {a.client.relay.token}":
                b_rotates_first.armed = False
                b.client.run_once()
        b_rotates_first.armed = True
        self.world.fake.before(b_rotates_first)
        removal = a.client.remove("computer 3", self.world.browser(), clear=True)
        self.assertFalse(b_rotates_first.armed)
        self.assertTrue(b.client.roster.entry(c.client.device_id)["removed"]["clear"], "computer 2 applied the choice")
        self.assertTrue(removal.cleared, "the removal that landed clears, as the person asked")
        for comp in (a, b):
            self.assertEqual(wrote(comp, c.prefix), [])

    def test_a_cleared_install_cannot_come_back_as_itself(self):
        (a, b), code = self.world.group("computer 1", "computer 2")
        mine = b.item("notes", "b1", "computer 2's note")
        b.sync()
        a.sync()
        a.client.remove("computer 2", self.world.browser(), clear=True)
        self.assertFalse(a.has("notes", mine))

        b.client.close()
        b.client = None
        shutil.rmtree(os.path.join(b.config, "sync"))
        b.login(label="computer 2")
        with self.assertRaises(SyncError) as caught:
            self.world.pair(a, b)
        self.assertIn("cleared", str(caught.exception))
        self.assertTrue(b.has("notes", mine), "its own items stay on it")

        a.lose_everything()
        revoked = {d for d, dev in self.world.fake.devices.items() if dev["revoked"]}
        with self.assertRaises(SyncError):
            b.client.recover(code, self.world.browser())
        self.assertTrue(b.has("notes", mine), "reading a roster that clears it never deletes its own items")
        self.assertEqual({d for d, dev in self.world.fake.devices.items() if dev["revoked"]}, revoked)

    def rejoin(self, clear, new_install_id=True):
        """Computer 2 reads a note and marks one of computer 1's, then changes its note and crashes after signing that
        change but before sending it. It is removed, and joins again with its store kept, under a new install id or the
        same one. Returns both computers, its old install id and its note."""
        (a, b), _ = self.world.group("computer 1", "computer 2")
        theirs = a.item("notes", "a1", "computer 1's note")
        a.sync()
        b.sync()
        old_prefix, old = b.prefix, b.item("notes", "b1", "computer 2's note")
        store.mark_superseded(b.db, "notes", theirs, ("notes", old), b.prefix, "2026-10-05")
        b.sync()
        a.sync()
        b.item("notes", "b1", "computer 2's note, changed")
        changed = records.changes(b.db, b.prefix)
        with state.tx(b.db):
            records.stage(b.db, changed, int(self.world.now * 1000))
        self.assertTrue(b.client._sign_next())
        a.client.remove("computer 2", self.world.browser(), clear=clear)

        b.client.close()
        b.client = None
        shutil.rmtree(os.path.join(b.config, "sync"))
        if new_install_id:
            os.remove(os.path.join(b.config, ids.FILE_NAME))
        b.login(label="computer 2")
        self.world.pair(a, b)
        self.assertEqual(b.prefix != old_prefix, new_install_id)
        return a, b, old_prefix, old

    def assert_it_syncs_as_its_new_self(self, a, b):
        new = b.item("notes", "b2", "computer 2's new note")
        for comp in (b, a, b):
            self.assertIsNone(comp.sync().stalled)
        self.assertTrue(a.has("notes", new))

    def test_a_cleared_computer_rejoins_under_a_new_install_id_and_keeps_its_store(self):
        a, b, old_prefix, old = self.rejoin(clear=True)
        self.assertFalse(b.has("notes", old), "what it read under the old install id is cleared on it too")
        self.assertEqual([k for k in state.sent(b.db) if old_prefix in (k[3], ids.owner(k[2]))], [],
                         "the clear forgets what the old install id sent")
        self.assert_it_syncs_as_its_new_self(a, b)

    def test_a_computer_removed_with_its_items_kept_rejoins_under_a_new_install_id(self):
        a, b, _, old = self.rejoin(clear=False)
        self.assertTrue(b.has("notes", old))
        self.assert_it_syncs_as_its_new_self(a, b)
        self.assertEqual(a.text("notes", old)["rows"], ["computer 2's note"],
                         "it never sends a record about what its old install id wrote, not even one it had signed")

    def test_a_computer_removed_with_its_items_kept_rejoins_as_itself_and_sends_what_it_had_signed(self):
        a, b, _, old = self.rejoin(clear=False, new_install_id=False)
        self.assert_it_syncs_as_its_new_self(a, b)
        self.assertEqual(a.text("notes", old)["rows"], ["computer 2's note, changed"])

    def test_a_record_queued_before_masking_moved_to_the_readers_leaves_masked(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        key = "sk-ant-api03-" + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAb7cDe0f"
        rec = records.Record("item", "notes", a.key("queued"), int(self.world.now * 1000),
                             {"ts": "2026-10-01", "title": "Queued", "meta": {"hash": "h"}, "rows": ["note " + key[:8], key[8:]]})
        with state.tx(a.db):
            state.add_outbox(a.db, [records.to_json(rec)])
        before = len(self.world.fake.group(EMAIL).packs)
        a.client.push()
        sent = [pack.parse(keys.open_with([a.client.keyring.identity(p["epoch"])], p["blob"]))
                for p in self.world.fake.group(EMAIL).packs[before:]]
        held = json.dumps([records.to_wire(r) for pk in sent for r in pk.records])
        self.assertIn("[masked]", held)
        self.assertNotIn(key[:8], held)

    def test_no_two_active_computers_share_an_install_id(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        ro = a.client.roster
        twin = {**ro.entry(b.client.device_id), "device_id": str(uuid.uuid4()), "label": "computer 2 twin",
                "added_in": ro.version + 1}
        with self.assertRaises(keys.Refused):
            roster.check_link(roster.new_link(ro, ro.epoch, a.client.device_id, ro.entries + [twin], a.client.keyring))

    def test_a_removal_and_its_clear_flag_never_change(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        a.client.remove("computer 3", self.world.browser(), clear=False)
        ro, ring, signer = a.client.roster, a.client.keyring, a.client
        flipped = [{**e, "removed": {**e["removed"], "clear": True}} if e["removed"] else e for e in ro.entries]
        for epoch in (ro.epoch, ro.epoch + 1):
            link = roster.new_link(ro, epoch, signer.device_id, flipped, ring)
            blob = roster.seal_link(link, signer.paths.device_ssh, ring.recipient(ro.epoch))
            with self.subTest(epoch=epoch), self.assertRaises(keys.Refused):
                ro.accept(*roster.open_link(blob, ring))

    def test_no_active_computer_writes_under_a_cleared_install_id(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        a.client.remove("computer 2", self.world.browser(), clear=True)
        ro = a.client.roster
        again = {**ro.entry(b.client.device_id), "device_id": str(uuid.uuid4()), "label": "computer 2 again",
                 "added_in": ro.version + 1, "removed": None}
        with self.assertRaises(keys.Refused):
            roster.check_link(roster.new_link(ro, ro.epoch, a.client.device_id, ro.entries + [again], a.client.keyring))


class RemoveCommandTest(unittest.TestCase):
    """`sync remove` says how many items the computer read, and asks before it deletes them."""

    def setUp(self):
        self.world = World()
        (self.a, self.b), _ = self.world.group("computer 1", "computer 2")
        for name in ("one", "two"):
            self.b.item("notes", name, f"computer 2's note {name}")
        self.b.sync()
        self.a.sync()

    def tearDown(self):
        self.world.close()

    def run_remove(self, *argv, answers=()):
        answers, asked = list(answers), []

        def answer(prompt):
            asked.append(prompt)
            if not answers:
                raise EOFError
            return answers.pop(0)

        args = kbcli.parser().parse_args(["sync", "remove", "computer 2", *argv])
        with mock.patch("builtins.input", answer), contextlib.redirect_stdout(io.StringIO()) as out:
            rc = cli.cmd_remove(self.a.client, args)
        return rc, out.getvalue(), asked

    def revoked(self):
        return self.world.fake.devices[self.b.client.device_id]["revoked"]

    def test_no_removes_nothing(self):
        for answer in (["n"], [""], []):
            with self.subTest(answer=answer):
                rc, out, asked = self.run_remove(answers=answer)
                self.assertEqual(rc, 1)
                self.assertIn("computer 2 read 2 items. Removing it deletes them from every computer. Remove it?", asked[0])
                self.assertIn("Nothing was removed", out)
                self.assertFalse(self.revoked())
                self.assertEqual(len(wrote(self.a, self.b.prefix)), 2)

    def test_yes_at_the_prompt_clears(self):
        rc, out, asked = self.run_remove(answers=["y", "n"])
        self.assertFalse(rc)
        self.assertEqual(len(asked), 2, "then it offers a new recovery code")
        self.assertIn("deleted here", out)
        self.assertTrue(self.revoked())
        self.assertEqual(wrote(self.a, self.b.prefix), [])

    def test_yes_flag_clears_without_asking_about_items(self):
        rc, out, asked = self.run_remove("--yes", answers=["n"])
        self.assertFalse(rc)
        self.assertIn("computer 2 read 2 items. Removing it deletes them from every computer.", out)
        self.assertEqual([p for p in asked if "items" in p], [])
        self.assertEqual(wrote(self.a, self.b.prefix), [])

    def test_keep_items_removes_without_asking_about_items(self):
        rc, out, asked = self.run_remove("--keep-items", answers=["n"])
        self.assertFalse(rc)
        self.assertIn("computer 2 read 2 items. They stay on every computer.", out)
        self.assertEqual([p for p in asked if "items" in p], [])
        self.assertTrue(self.revoked())
        self.assertEqual(len(wrote(self.a, self.b.prefix)), 2)


if __name__ == "__main__":
    unittest.main()
