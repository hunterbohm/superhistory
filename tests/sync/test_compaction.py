import argparse
import contextlib
import io
import itertools
import types
import unittest
import uuid
from unittest import mock

from kb import store
from kb.sync import cli
from kb.sync import pack, records, relay, state
from kb.sync.client import COMPACT_PACKS, MAX_PASSED, Frozen, missing_notice
from kb.sync.keys import Refused

from .support import EMAIL, World, put_item, rows


def change_seqs(group):
    return [p["seq"] for p in group.packs if p["snapshot_id"] is None]


class CompactionTest(unittest.TestCase):
    def setUp(self):
        self.world = World()

    def tearDown(self):
        self.world.close()

    def snapshot_author(self, reader):
        group = self.world.fake.group(EMAIL)
        return next(h["author"] for seq, h in self.world.opened(reader)
                    if h["snapshot"] and h["snapshot"]["id"] == group.snapshot[0])

    def test_compaction_reaches_a_lagging_device(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        doomed = a.item("notes", "doomed", "deleted while computer 3 is away")
        kept = b.item("git", "kept", "stays")
        for comp in (a, b, c, a):
            comp.sync()
        self.assertTrue(c.has("notes", doomed))
        put_item(c.db, "notes", b.key("stray"), "2026-10-01", "Stray", "held by no snapshot", {})

        store.delete_item(a.db, "notes", doomed)
        a.sync()
        b.sync()
        later = a.item("notes", "later", "written while computer 3 is away")
        a.sync()
        self.assertTrue(a.client.compact(force=True))
        group = self.world.fake.group(EMAIL)
        through = group.snapshot[1]
        self.assertFalse([s for s in change_seqs(group) if s <= through], "the Gone pack left the relay")

        self.assertIsNone(c.sync().stalled)
        self.assertFalse(c.has("notes", doomed), "a delete compacted away still reaches a computer that was behind")
        self.assertFalse(c.has("notes", b.key("stray")))
        self.assertTrue(c.has("notes", later) and c.has("git", kept))
        self.assertEqual(rows(c.db), rows(a.db))

    def test_many_small_packs_make_a_compaction_due_whatever_their_bytes(self):
        # Twice a 1 GiB snapshot is far more than 10,000 packs of 16 KiB, so only their count makes it due, and a pull
        # never lists more packs past a snapshot than it may go past.
        (a,), _ = self.world.group("computer 1")
        c = a.client
        st = {"snapshot": {"id": str(uuid.uuid4()), "through": 100}, "head": 100 + COMPACT_PACKS,
              "pack_bytes": (1 << 30) + COMPACT_PACKS * (16 << 10)}
        with mock.patch.object(c.relay, "snapshot_parts", return_value=[{"epoch": c.keyring.newest, "size": 1 << 30}]):
            self.assertFalse(c._compaction_due(st))
            self.assertTrue(c._compaction_due({**st, "head": st["head"] + 1}))
        self.assertLess(5 * COMPACT_PACKS, MAX_PASSED, "a pull can read ahead past these packs a few times over")

    def test_compactions_race(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        a.item("notes", "a1", "from a")
        b.item("notes", "b1", "from b")
        for comp in (a, b, a, b):
            comp.sync()
        fake = self.world.fake

        def a_wins_first(method, path, headers):
            if method == "POST" and path == "/sync/compact" and a_wins_first.armed and \
                    headers.get("authorization") == f"Bearer {b.client.relay.token}":
                a_wins_first.armed = False
                self.assertTrue(a.client.compact(force=True))
        a_wins_first.armed = True
        fake.before(a_wins_first)

        self.assertFalse(b.client.compact(force=True), "the second compaction fails its compare-and-swap and stops")
        self.assertFalse(a_wins_first.armed)
        self.assertEqual(self.snapshot_author(a), a.client.device_id)
        group = fake.group(EMAIL)
        self.assertEqual({p["snapshot_id"] for p in group.packs if p["snapshot_id"]}, {group.snapshot[0]})
        d = self.world.computer("computer 4")
        self.world.pair(a, d)
        for comp in (a, b, d):
            self.assertIsNone(comp.sync().stalled)
        self.assertEqual(rows(a.db), rows(b.db))
        self.assertEqual(rows(a.db), rows(d.db))

    def test_snapshot_from_a_newly_joined_member_is_accepted(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        a.item("notes", "a1", "from a")
        b.item("notes", "b1", "from b")
        for comp in (a, b, a, b):
            comp.sync()
        self.assertTrue(b.client.compact(force=True))
        self.assertEqual(self.snapshot_author(a), b.client.device_id)
        c = self.world.computer("computer 3")
        self.assertIsNone(self.world.pair(a, c).stalled)
        self.assertEqual(rows(c.db), rows(a.db))

    def test_compact_remove_then_lose_every_computer_then_recover(self):
        (a, b), code = self.world.group("computer 1", "computer 2")
        a.item("notes", "a1", "from a")
        b.item("notes", "b1", "from b")
        for comp in (a, b, a, b):
            comp.sync()
        self.assertTrue(b.client.compact(force=True))
        fake = self.world.fake

        def compaction_never_lands(method, path, headers):
            if method == "POST" and path == "/sync/compact":
                raise relay.TransportError("lost")
        fake.before(compaction_never_lands)
        with self.assertRaises(relay.TransportError):
            a.client.remove("computer 2", self.world.browser(), clear=False)
        fake.before_hooks.clear()
        group = fake.group(EMAIL)
        self.assertEqual(group.epoch, 2)
        self.assertEqual(self.snapshot_author(a), b.client.device_id, "the current snapshot is the removed computer's")
        expected = rows(a.db)
        a.lose_everything()
        b.lose_everything()

        c = self.world.computer("computer 3")
        c.client.recover(code, self.world.browser())
        self.assertEqual(rows(c.db), expected)

    def test_removed_device_cannot_make_an_accepted_snapshot(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        a.item("notes", "a1", "from a")
        for comp in (a, b, c):
            comp.sync()
        a.client.remove("computer 3", self.world.browser(), clear=False)
        b.sync()

        cc, group = c.client, self.world.fake.group(EMAIL)
        through = max(p["seq"] for p in group.packs)
        dseq, digest = cc._signing_head()
        evil = records.Record("item", "notes", c.key("evil"), int(self.world.now * 1000),
                              {"ts": "t", "title": "Evil", "meta": {"hash": "e"}, "rows": ["planted"]})
        sid = str(uuid.uuid4())
        part = pack.build({"epoch": 1, "author": cc.device_id, "dseq": None, "prev": None,
                           "roster_version": cc.roster.version, "roster_hash": cc.roster.hash,
                           "snapshot": {"id": sid, "through": through, "part": 0, "parts": 1,
                                        "heads": {cc.device_id: [dseq, digest]}}},
                          [records.to_json(evil)], cc.paths.device_ssh)
        self.world.put_raw(pack.seal(part, cc.keyring.recipient(1)), 1, snapshot_id=sid)
        announce = records.Record("snapshot", "", "", int(self.world.now * 1000),
                                  {"id": sid, "through": through, "parts": 1, "manifest": pack.manifest([part])})
        self.world.forge(c, [announce], seal_epoch=1)
        group.packs = [p for p in group.packs if p["snapshot_id"] == sid or (p["snapshot_id"] is None and p["seq"] > through)]
        group.snapshot = (sid, through)

        d = self.world.computer("computer 4")
        report = self.world.pair(a, d)
        self.assertIn("removed computer", report.stalled or "")
        self.assertFalse(d.has("notes", c.key("evil")))
        caught_up = b.sync()
        self.assertIsNone(caught_up.stalled, "the announcement computer 3 signed after its removal is passed over")
        self.assertFalse(b.has("notes", c.key("evil")))



def item(source_id, text, v):
    return records.Record("item", "notes", source_id, v, {"ts": "2026-10-01", "title": "T", "meta": {"hash": text[:8]},
                                                          "rows": [text]})


class SnapshotCoverageTest(unittest.TestCase):
    """A snapshot is taken author by author: it stands for every install up to `through` except one this computer
    applied further. So a pull that reaches packs past a compaction still gets what was compacted, a computer's own
    later packs are never in the way, and the one snapshot refused is a fork."""

    def setUp(self):
        self.world = World()
        (self.a, self.b, self.c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (self.a, self.b, self.c):
            comp.sync()

    def tearDown(self):
        self.world.close()

    def test_a_computer_that_sent_its_own_packs_after_the_snapshot_takes_it_without_stopping(self):
        a, b, c = self.a, self.b, self.c
        theirs = b.item("notes", "b1", "computer 2's note")
        self.assertIsNone(b.sync().stalled)
        self.assertTrue(a.client.compact(force=True))  # computer 3 never pulled computer 2's pack, now compacted
        mine = c.item("notes", "c1", "computer 3's note, sent after the snapshot")
        c.client.push()
        report = c.sync()
        self.assertIsNone(report.stalled, "behind for others and ahead for itself is ordinary")
        self.assertTrue(c.has("notes", theirs), "what was compacted reaches it from the snapshot")
        self.assertTrue(c.has("notes", mine))
        self.assertIsNone(a.sync().stalled)
        self.assertTrue(a.has("notes", mine))

    def test_a_pull_that_reads_the_relay_before_a_compaction_lands_still_gets_what_it_compacted(self):
        a, b, c = self.a, self.b, self.c
        theirs = b.item("notes", "b1", "computer 2's note, compacted before computer 3 pulls it")
        self.assertIsNone(b.sync().stalled)
        self.assertTrue(a.client.compact(force=True))
        group = self.world.fake.group(EMAIL)
        self.assertGreater(group.snapshot[1], state.get(c.db, "cursor", 0))
        seen = []

        def before_the_compaction(method, path, status, out):
            if method == "GET" and path == "/sync/state" and not seen:
                seen.append(1)
                out = {**out, "snapshot": None}  # this pull read the relay just before the compaction landed
            return status, out
        self.world.fake.rewrite(before_the_compaction)
        self.assertIsNone(c.client.pull().stalled)
        self.assertTrue(c.has("notes", theirs), "the pull takes the snapshot when it meets the gap the compaction left")
        self.assertIsNone(c.sync().stalled)
        self.assertTrue(c.has("notes", theirs), "and no later sync loses it")

    def test_a_relay_that_lists_a_made_up_part_over_a_compaction_cannot_hide_what_it_compacted(self):
        a, b, c = self.a, self.b, self.c
        theirs = b.item("notes", "b1", "computer 2's note, which computer 2 confirmed it sent")
        self.assertIsNone(b.sync().stalled)
        self.assertTrue(a.client.compact(force=True))
        group = self.world.fake.group(EMAIL)
        cursor = state.get(c.db, "cursor", 0)
        self.assertGreater(group.snapshot[1], cursor)
        made_up = {"seq": cursor + 1, "pack_id": str(uuid.uuid4()), "epoch": group.epoch, "blob": b"made up" * 8,
                   "snapshot_id": str(uuid.uuid4()), "day": "2026-10-01"}
        group.packs = sorted(group.packs + [made_up], key=lambda p: p["seq"])  # fills the gap the compaction left
        lying = [True]

        def no_snapshot(method, path, status, out):
            if method == "GET" and path == "/sync/state" and lying[0]:
                out = {**out, "snapshot": None}
            return status, out
        self.world.fake.rewrite(no_snapshot)
        report = c.client.pull()
        self.assertIn("has not served packs confirmed by computer 2", report.stalled or "",
                      "while the relay hides the compaction, the pull stops and names whose packs are missing")
        self.assertFalse(c.client.compact(force=True), "and nothing short of them is compacted")
        lying[0] = False
        self.assertIsNone(c.client.pull().stalled)
        self.assertTrue(c.has("notes", theirs), "once the relay names its snapshot, the pull takes it")
        self.assertIsNone(c.sync().stalled)
        self.assertTrue(c.has("notes", theirs))

    def test_a_pack_confirmation_a_stolen_token_made_up_stops_the_others_only_until_its_computer_is_revoked(self):
        a, b, c = self.a, self.b, self.c
        dseq = self.world.fake.chain(b.client.device_id)[0]
        self.world.fake.devices[b.client.device_id]["points"][dseq + 5] = "f" * 64  # no such pack exists anywhere
        self.assertIn("has not served packs confirmed by computer 2", c.client.pull().stalled or "",
                      "while computer 2 is a member, its confirmation counts, and the pull says what is missing")
        self.world.fake.revoke(b.client.device_id, clear=False)
        report = c.client.pull()
        self.assertIsNone(report.stalled, "once revoked, a made-up confirmation blocks nothing")
        self.assertEqual(report.missing, ("computer 2",), "but every sync names it, rather than dropping it in silence")
        self.assertEqual(a.client.pull().missing, ("computer 2",))
        notice = missing_notice("computer 2")
        self.assertIn("If computer 2 was lost or stolen", notice)
        self.assertIn(notice, c.client.status()["problems"], "`kb sync status` always shows it")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(cli.cmd_sync(c.client, argparse.Namespace()))
        self.assertIn(notice, err.getvalue(), "and `kb sync` prints the same text")
        with self.assertLogs("kb.sync.client", "WARNING") as logs:
            self.assertIsNone(c.client.run_once().stalled)
        self.assertIn(notice, "\n".join(logs.output), "the scheduled run, which reads no report, logs it")
        state.set_head(c.db, b.client.device_id, dseq + 5, "f" * 64)  # as if its packs arrived since
        state.put(c.db, "problem", {"at": "pack 9", "reason": "something else"})  # and a later pull stopped on another
        problems = c.client.status()["problems"]
        self.assertNotIn(notice, problems, "a notice whose packs have since arrived is not left standing")
        self.assertIn("Sync is stopped at pack 9: something else.", problems)

    def test_what_the_person_kept_of_a_removed_computer_reaches_a_pull_that_read_the_relay_before_a_compaction(self):
        a, b, c = self.a, self.b, self.c
        kept = b.item("notes", "b1", "computer 2's note, kept when it is removed")
        self.assertIsNone(b.sync().stalled)  # sent and confirmed; computer 3 has not pulled it
        self.world.fake.revoke(b.client.device_id, clear=False)
        with mock.patch.object(a.client, "_compaction_due", return_value=False):
            self.assertIsNone(a.sync().stalled)  # finishes the removal
        self.assertTrue(a.client.compact(force=True))  # computer 2's pack now lives only in the snapshot
        seen = []

        def before_the_compaction(method, path, status, out):
            if method == "GET" and path == "/sync/state" and not seen:
                seen.append(1)
                out = {**out, "snapshot": None}
            return status, out
        self.world.fake.rewrite(before_the_compaction)
        report = c.client.pull()
        self.assertIsNone(report.stalled)
        self.assertTrue(c.has("notes", kept), "the removed computer's confirmed pack takes the snapshot that holds it")
        self.assertEqual(report.missing, ())
        self.assertNotIn(missing_notice("computer 2"), c.client.status()["problems"], "no notice once it arrived")
        self.assertIsNone(c.sync().stalled)
        self.assertTrue(c.has("notes", kept))

    def test_a_pull_that_meets_an_author_past_a_compaction_takes_the_snapshot_and_never_sticks(self):
        a, b, c = self.a, self.b, self.c
        theirs = b.item("notes", "b1", "computer 2's note")
        self.assertIsNone(b.sync().stalled)
        self.assertTrue(a.client.compact(force=True))
        self.assertTrue(a.client.compact(force=True))  # the first announcement is compacted too
        seen = []

        def before_the_compaction(method, path, status, out):
            if method == "GET" and path == "/sync/state" and not seen:
                seen.append(1)
                out = {**out, "snapshot": None}
            return status, out
        self.world.fake.rewrite(before_the_compaction)
        self.assertIsNone(c.client.pull().stalled, "computer 1's chain jumps, so the pull takes the snapshot first")
        self.assertTrue(c.has("notes", theirs))
        self.assertIsNone(c.sync().stalled)

    def test_a_pull_that_takes_a_snapshot_partway_through_a_page_lists_again_from_it(self):
        a, b, c = self.a, self.b, self.c
        group = self.world.fake.group(EMAIL)
        a.item("notes", "a1", "computer 1's first note")
        self.assertIsNone(a.sync().stalled)
        self.assertTrue(a.client.compact(force=True))
        later = []
        for name in ("a2", "a3"):
            later.append(a.item("notes", name, f"computer 1's note {name}"))
            with mock.patch.object(a.client, "_compaction_due", return_value=False):
                self.assertIsNone(a.sync().stalled)
        stale = [dict(p) for p in group.packs if p["snapshot_id"] is None][-2:]
        self.assertIsNone(b.sync().stalled)
        self.assertTrue(b.client.compact(force=True))  # a newer snapshot, which holds both of computer 1's packs
        group.packs = sorted(group.packs + stale, key=lambda p: p["seq"])  # still listed, as on a page fetched before
        seen = []

        def before_the_compaction(method, path, status, out):
            if method == "GET" and path == "/sync/state" and not seen:
                seen.append(1)
                out = {**out, "snapshot": None}
            return status, out
        self.world.fake.rewrite(before_the_compaction)
        cursor = state.get(c.db, "cursor", 0)
        self.assertIsNone(c.client.pull().stalled, "packs the snapshot holds are not met again, so they are no gap")
        self.assertTrue(all(c.has("notes", key) for key in later))
        self.assertGreaterEqual(state.get(c.db, "cursor", 0), group.snapshot[1], "and the cursor never went back")
        self.assertGreater(state.get(c.db, "cursor", 0), cursor)

    def test_a_relay_that_keeps_handing_back_old_snapshots_cannot_hold_a_pull_for_ever(self):
        a, b, c = self.a, self.b, self.c
        group = self.world.fake.group(EMAIL)
        snapshots, kept = [], {}
        for n in (1, 2):
            a.item("notes", f"a{n}", f"computer 1's note {n}")
            self.assertIsNone(a.sync().stalled)
            self.assertTrue(a.client.compact(force=True))
            snapshots.append({"id": group.snapshot[0], "through": group.snapshot[1]})
            kept.update({p["seq"]: dict(p) for p in group.packs})  # its parts and announcement, which a relay can keep
            self.assertIsNone(c.sync().stalled)  # computer 3 takes it
        for n in (1, 2):
            b.item("notes", f"b{n}", f"computer 2's note {n}")
            with mock.patch.object(b.client, "_compaction_due", return_value=False):
                self.assertIsNone(b.sync().stalled)
        hidden = [p for p in group.packs if p["snapshot_id"] is None][-2]["seq"]
        packs = {p["seq"]: p for p in group.packs if p["seq"] != hidden}
        for seq, p in kept.items():
            packs.setdefault(seq, p)
        group.packs = sorted(packs.values(), key=lambda p: p["seq"])
        reads = []

        def alternate(method, path, status, out):
            if method == "GET" and path == "/sync/state":
                reads.append(1)
                if len(reads) > 20:
                    raise AssertionError("the pull kept asking for the relay's state")
                out = {**out, "snapshot": snapshots[len(reads) % 2]}
            return status, out
        self.world.fake.rewrite(alternate)
        report = c.client.pull()
        self.assertIn("gap in the chain of computer 2", report.stalled or "", "it stops at the hidden pack, visibly")
        self.assertLess(len(reads), 6)

    def test_a_snapshot_that_forks_from_what_this_computer_applied_stops_the_pull(self):
        a, b, c = self.a, self.b, self.c
        group = self.world.fake.group(EMAIL)
        first = b.key("one")
        seq, _ = self.world.forge(b, [item(first, "computer 2's first pack", int(self.world.now * 1000))])
        self.assertIsNone(c.sync().stalled)
        self.assertTrue(c.has("notes", first))
        group.packs = [p for p in group.packs if p["seq"] != seq]
        other = b.key("other")
        self.world.forge(b, [item(other, "another pack at the same place", int(self.world.now * 1000))])
        self.assertIsNone(a.sync().stalled)
        self.assertTrue(a.client.compact(force=True))  # its heads name the other pack where computer 3 applied the first
        report = c.sync()
        self.assertIn("forks", report.stalled or "", "a fork stops the pull, visibly")
        self.assertTrue(c.has("notes", first) and not c.has("notes", other), "and nothing of the fork applies")



class ListingBoundsTest(unittest.TestCase):
    """Every pass over the relay's listing makes progress or stops: a page must climb past the last one, and the snapshot
    parts a pass goes past unread are counted. So a relay can hold no pull for ever by listing the same page again, or
    made-up parts without end."""

    def setUp(self):
        self.world = World()
        (self.a, self.b, self.c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (self.a, self.b, self.c):
            comp.sync()

    def tearDown(self):
        self.world.close()

    def made_up_parts(self, start, calls, cap=30):
        """A listing that answers every call with 200 made-up snapshot parts from `start` on, the same page each time,
        recording each call, and failing the test past `cap` calls rather than run for ever."""
        def packs(after, limit=200):
            calls.append(after)
            if len(calls) > cap:
                raise AssertionError("the pass kept listing")
            return [{"seq": start + i, "epoch": 1, "snapshot_id": str(uuid.uuid4()), "size": 10} for i in range(200)]
        return packs

    def test_reading_ahead_for_a_removed_computer_stops_when_the_relay_lists_a_page_again(self):
        c, b = self.c.client, self.b.client
        calls, after = [], state.get(self.c.db, "cursor", 0)
        with mock.patch.object(c.relay, "packs", side_effect=self.made_up_parts(after + 1, calls)):
            with self.assertRaisesRegex(Refused, "the relay listed packs out of order"):
                c._vouched_run(c.roster.entry(b.device_id), state.head(self.c.db, b.device_id), after, Frozen(None, None, False))
        self.assertEqual(len(calls), 2, "the page listed again is refused the second time")

    def test_looking_for_a_snapshot_announcement_stops_when_the_relay_lists_a_page_again(self):
        c, a = self.c.client, self.a.client
        calls, through = [], state.get(self.c.db, "cursor", 0)
        snap = {"id": str(uuid.uuid4()), "through": through, "parts": 1, "heads": {a.device_id: [0, ""]}}
        with mock.patch.object(c.relay, "packs", side_effect=self.made_up_parts(through + 1, calls)):
            with self.assertRaisesRegex(Refused, "the relay listed packs out of order"):
                c._find_announcement(c.roster.entry(a.device_id), snap, "manifest")
        self.assertEqual(len(calls), 2)

    def replayed_packs(self, calls):
        """A listing that climbs for ever, every entry called an ordinary pack, each of which opens as a genuine part
        replayed: what a pass reading ahead goes past as not what it looks for."""
        def packs(after, limit=200):
            calls.append(after)
            if len(calls) > 30:
                raise AssertionError("the pass kept listing")
            return [{"seq": after + 1 + i, "epoch": 1, "snapshot_id": None, "size": 10} for i in range(200)]
        part = types.SimpleNamespace(is_part=True, header={"author": "replayed", "dseq": None})
        return packs, (lambda p: part)

    def test_reading_ahead_for_a_removed_computer_stops_when_the_relay_replays_packs_without_end(self):
        c, b = self.c.client, self.b.client
        calls, after = [], state.get(self.c.db, "cursor", 0)
        packs, opened = self.replayed_packs(calls)
        with mock.patch("kb.sync.client.MAX_PASSED", 1000), mock.patch.object(c.relay, "packs", side_effect=packs), \
                mock.patch.object(c, "_open", side_effect=opened):
            with self.assertRaisesRegex(Refused, "the relay listed more packs than any sync goes past"):
                c._vouched_run(c.roster.entry(b.device_id), state.head(self.c.db, b.device_id), after, Frozen(None, None, False))
        self.assertLessEqual(len(calls), 6)

    def test_looking_for_a_snapshot_announcement_stops_when_the_relay_replays_packs_without_end(self):
        c, a = self.c.client, self.a.client
        calls, through = [], state.get(self.c.db, "cursor", 0)
        packs, opened = self.replayed_packs(calls)
        snap = {"id": str(uuid.uuid4()), "through": through, "parts": 1, "heads": {a.device_id: [0, ""]}}
        with mock.patch("kb.sync.client.MAX_PASSED", 1000), mock.patch.object(c.relay, "packs", side_effect=packs), \
                mock.patch.object(c, "_open", side_effect=opened):
            with self.assertRaisesRegex(Refused, "the relay listed more packs than any sync goes past"):
                c._find_announcement(c.roster.entry(a.device_id), snap, "manifest")
        self.assertLessEqual(len(calls), 6)

    def test_a_pull_stops_when_the_relay_replays_a_removed_computers_packs_that_do_not_count(self):
        # A removed computer's genuine pack checks out, but past what it confirmed it never applies: the pull goes past
        # it. Listed again and again at rising numbers, each one is counted, and the pull stops at the cap.
        c, b = self.c.client, self.b
        b.item("notes", "b1", "computer 2's note")
        self.assertIsNone(b.sync().stalled)
        entry = next(p for p in reversed(self.world.fake.group(EMAIL).packs) if p["snapshot_id"] is None)
        genuine = c._open({"seq": entry["seq"], "epoch": entry["epoch"]})
        self.world.fake.revoke(b.client.device_id, clear=False)
        calls = []

        def rising(after, limit=200):
            calls.append(after)
            if len(calls) > 30:
                raise AssertionError("the pull kept listing")
            return [{"seq": after + 1 + i, "epoch": entry["epoch"], "snapshot_id": None, "size": 10} for i in range(200)]
        with mock.patch("kb.sync.client.MAX_PASSED", 1000), mock.patch.object(c.relay, "packs", side_effect=rising), \
                mock.patch.object(c, "_open", return_value=genuine), mock.patch.object(c, "_vouched_run", return_value={}):
            report = c.pull()
        self.assertIn("the relay listed more packs than any sync goes past", report.stalled or "")
        self.assertLessEqual(len(calls), 6)

    def test_a_pull_stops_when_each_read_ahead_ends_but_the_relay_lists_a_removed_computers_pack_again(self):
        # Round 16's sequence: computer 2 sends a pack it never confirms and is removed. The relay lists that genuine
        # pack again at ever higher numbers, and lets each read-ahead see only the page in hand, then the end. Every
        # read-ahead stays under the limit and finds nothing that counts, so the pull goes past every copy, and only a
        # count of those copies stops it.
        c, b = self.c.client, self.b
        b.item("notes", "b1", "computer 2's note")
        self.assertIsNone(b.sync().stalled)
        b.item("notes", "b2", "computer 2's note it never confirms")
        with mock.patch.object(b.client, "_confirm_sent"):
            self.assertIsNone(b.sync().stalled)
        entry = next(p for p in reversed(self.world.fake.group(EMAIL).packs) if p["snapshot_id"] is None)
        genuine = c._open({"seq": entry["seq"], "epoch": entry["epoch"]})
        self.world.fake.revoke(b.client.device_id, clear=False)
        calls, reading, real = [], [], c._vouched_run

        def vouched_run(entry, head, after, frozen, **kwargs):
            reading.append(after)
            try:
                return real(entry, head, after, frozen, **kwargs)
            finally:
                reading.pop()

        def page(after):
            return [{"seq": after + 1 + i, "epoch": entry["epoch"], "snapshot_id": None, "size": 10} for i in range(200)]

        def packs(after, limit=200):
            calls.append(after)
            if len(calls) > 60:
                raise AssertionError("the pull kept listing")
            return [] if reading and after != reading[-1] else page(after)
        with mock.patch("kb.sync.client.MAX_PASSED", 1000), mock.patch.object(c.relay, "packs", side_effect=packs), \
                mock.patch.object(c, "_open", return_value=genuine), \
                mock.patch.object(c, "_vouched_run", side_effect=vouched_run):
            report = c.pull()
        self.assertIn("the relay listed more packs than any sync goes past", report.stalled or "")
        self.assertLessEqual(len(calls), 15)
        self.assertFalse(self.c.has("notes", b.key("b2")), "a pack it never confirmed never applies")

    def test_a_removed_computers_backlog_past_the_limit_reaches_computers_that_were_away(self):
        # Round 17's sequence: computers 1 and 3 are away while computer 2 sends and confirms more packs than the limit
        # on what a pull goes past, and never compacts. Then it is removed with its items kept. Its packs are its own
        # signed chain, so reading ahead through them is not held to that limit, and both computers apply them, finish
        # the removal and can compact.
        a, b, c = self.a, self.b, self.c
        names = []
        with mock.patch.object(b.client, "_compaction_due", return_value=False):
            for n in range(6):
                names.append(b.item("notes", f"b{n}", f"computer 2's note {n}"))
                self.assertIsNone(b.sync().stalled)
        self.assertEqual(len(change_seqs(self.world.fake.group(EMAIL))), 6, "a pack for each note")
        self.world.fake.revoke(b.client.device_id, clear=False)
        with mock.patch("kb.sync.client.MAX_PASSED", 5):
            for comp in (a, c):
                self.assertIsNone(comp.sync().stalled)
                self.assertTrue(all(comp.has("notes", name) for name in names), "what the person kept arrived")
                self.assertTrue(comp.client.roster.entry(b.client.device_id)["removed"], "the removal finished")
            self.assertTrue(a.client.compact(force=True))

    def test_a_removed_computers_backlog_past_both_limits_arrives_over_several_syncs(self):
        # Round 18's sequence: the backlog is longer than even the limit on signed packs. A read-ahead that limit stops
        # still returns the packs it found that count, so each sync applies and keeps some, and the next reads on from
        # there, until the computers that were away hold them all and finish the removal.
        a, b, c = self.a, self.b, self.c
        names = []
        with mock.patch.object(b.client, "_compaction_due", return_value=False):
            for n in range(6):
                names.append(b.item("notes", f"b{n}", f"computer 2's note {n}"))
                self.assertIsNone(b.sync().stalled)
        self.world.fake.revoke(b.client.device_id, clear=False)
        with mock.patch("kb.sync.client.MAX_SIGNED_PASSED", 5):
            for comp in (a, c):  # each away computer's first pull, before either finishes the removal and compacts
                first = comp.client.pull()
                self.assertIn("the relay listed more packs than any sync goes past", first.stalled or "")
                held = sum(comp.has("notes", name) for name in names)
                self.assertTrue(0 < held < len(names), "the first pull applied and kept what it read that counts")
            for comp in (a, c):
                for _ in range(4):
                    if comp.sync().stalled is None:
                        break
                self.assertTrue(all(comp.has("notes", name) for name in names), "what the person kept arrived")
                self.assertTrue(comp.client.roster.entry(b.client.device_id)["removed"], "the removal finished")
            self.assertTrue(a.client.compact(force=True))

    def test_an_endless_chain_signed_with_a_removed_computers_key_stops_at_its_own_limit(self):
        # A relay holding a removed computer's key can sign packs at ever later places in its chain. Each costs a
        # signature, and they count toward their own limit, so the pull stops there.
        c, b = self.c.client, self.b.client
        self.world.fake.revoke(b.device_id, clear=False)
        dseqs, calls = itertools.count(1000), []

        def signed(p):
            head = {"epoch": c.keyring.newest, "author": b.device_id, "dseq": next(dseqs), "prev": "0" * 64,
                    "roster_version": c.roster.version, "roster_hash": c.roster.hash, "snapshot": None}
            return pack.parse(pack.build(head, [], b.paths.device_ssh), inflate=False)

        def rising(after, limit=200):
            calls.append(after)
            if len(calls) > 30:
                raise AssertionError("the pull kept listing")
            return [{"seq": after + 1 + i, "epoch": c.keyring.newest, "snapshot_id": None, "size": 10} for i in range(200)]
        with mock.patch("kb.sync.client.MAX_SIGNED_PASSED", 50), mock.patch.object(c.relay, "packs", side_effect=rising), \
                mock.patch.object(c, "_open", side_effect=signed):
            report = c.pull()
        self.assertIn("the relay listed more packs than any sync goes past", report.stalled or "")
        self.assertLessEqual(len(calls), 3)

    def test_a_pull_counts_what_every_read_ahead_goes_past_toward_one_limit(self):
        # Each of a removed computer's packs the pull goes past starts a read-ahead. Counted pass by pass, a relay could
        # answer every read-ahead with parts just short of the limit, then list such a pack again, without end.
        c, b = self.c.client, self.b
        b.item("notes", "b1", "computer 2's note")
        self.assertIsNone(b.sync().stalled)
        entry = next(p for p in reversed(self.world.fake.group(EMAIL).packs) if p["snapshot_id"] is None)
        genuine = c._open({"seq": entry["seq"], "epoch": entry["epoch"]})
        self.world.fake.revoke(b.client.device_id, clear=False)
        calls, reading, real = [], [], c._vouched_run

        def vouched_run(entry, head, after, frozen, **kwargs):
            reading.append(after)
            try:
                return real(entry, head, after, frozen, **kwargs)
            finally:
                reading.pop()

        def packs(after, limit=200):
            calls.append(after)
            if len(calls) > 60:
                raise AssertionError("the pull kept listing")
            if reading:  # reading ahead: 999 parts, then the end of the listing
                return [{"seq": after + 1 + i, "epoch": entry["epoch"], "snapshot_id": str(uuid.uuid4()), "size": 10}
                        for i in range(min(200, reading[-1] + 999 - after))]
            return [{"seq": after + 1 + i, "epoch": entry["epoch"], "snapshot_id": None, "size": 10} for i in range(200)]
        with mock.patch("kb.sync.client.MAX_PASSED", 1000), mock.patch.object(c.relay, "packs", side_effect=packs), \
                mock.patch.object(c, "_open", return_value=genuine), \
                mock.patch.object(c, "_vouched_run", side_effect=vouched_run):
            report = c.pull()
        self.assertIn("the relay listed more packs than any sync goes past", report.stalled or "")
        self.assertLessEqual(len(calls), 15)
        self.assertNotIn("listed more packs", c.pull().stalled or "", "the next pull starts its own count")

    def test_a_snapshot_listed_with_one_part_again_and_again_stops_at_the_first(self):
        self.a.item("notes", "a1", "computer 1's note")
        self.a.sync()
        self.assertTrue(self.a.client.compact(force=True))
        c, snap = self.c.client, self.c.client.relay.state()["snapshot"]
        (part,) = c.relay.snapshot_parts(snap["id"])
        fetched, fetch = [], c.relay.pack
        with mock.patch.object(c.relay, "snapshot_parts", return_value=[part] * 5000), \
                mock.patch.object(c.relay, "pack", side_effect=lambda seq: fetched.append(seq) or fetch(seq)):
            with self.assertRaisesRegex(Refused, "snapshot parts are missing or repeated"):
                c._read_snapshot(snap, False)
        self.assertEqual(len(fetched), 1, "the listing is checked against the part's count before more is fetched")

    def test_a_pull_stops_at_the_first_replayed_part_the_relay_calls_a_pack(self):
        c, a = self.c.client, self.a.client
        calls = []
        packs, _ = self.replayed_packs(calls)
        part = types.SimpleNamespace(is_part=True, header={"author": a.device_id, "dseq": None, "epoch": 1})
        with mock.patch.object(c.relay, "packs", side_effect=packs), mock.patch.object(c, "_open", return_value=part):
            report = c.pull()
        self.assertIn("snapshot part listed as a change pack", report.stalled or "", "pull checks every pack it reads")
        self.assertEqual(len(calls), 1)

    def test_a_pull_stops_when_the_relay_lists_made_up_parts_without_end(self):
        c = self.c.client
        calls = []

        def rising(after, limit=200):
            calls.append(after)
            if len(calls) > 30:
                raise AssertionError("the pull kept listing")
            return [{"seq": after + 1 + i, "epoch": 1, "snapshot_id": str(uuid.uuid4()), "size": 10} for i in range(200)]
        with mock.patch("kb.sync.client.MAX_PASSED", 1000), mock.patch.object(c.relay, "packs", side_effect=rising):
            report = c.pull()
        self.assertIn("the relay listed more packs than any sync goes past", report.stalled or "")
        self.assertLessEqual(len(calls), 6)

class PageOrderTest(unittest.TestCase):
    def setUp(self):
        self.world = World()
        (self.a, self.b, self.c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (self.a, self.b, self.c):
            comp.sync()

    def tearDown(self):
        self.world.close()

    def test_a_page_listed_out_of_order_applies_nothing_and_says_so(self):
        a, b, c = self.a, self.b, self.c
        first = a.item("notes", "a1", "computer 1's note")
        self.assertIsNone(a.sync().stalled)
        second = b.item("notes", "b1", "computer 2's note")
        self.assertIsNone(b.sync().stalled)
        group = self.world.fake.group(EMAIL)
        cursor = state.get(c.db, "cursor", 0)
        ordered = list(group.packs)
        group.packs = sorted(group.packs, key=lambda p: -p["seq"])  # the later pack listed first
        report = c.client.pull()
        self.assertIn("the relay listed packs out of order", report.stalled or "")
        self.assertEqual(state.get(c.db, "cursor", 0), cursor, "nothing in the page applied")
        self.assertFalse(c.has("notes", first) or c.has("notes", second))
        group.packs = ordered
        self.assertIsNone(c.sync().stalled)
        self.assertTrue(c.has("notes", first) and c.has("notes", second), "none of the page is lost")


if __name__ == "__main__":
    unittest.main()
