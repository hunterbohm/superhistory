import json
import unittest
from unittest import mock

from kb.sync import records, state
from kb.sync.client import DAY_MS

from .support import EMAIL, World


def item(source_id, text, v):
    return records.Record("item", "notes", source_id, v, {"ts": "2026-10-01", "title": "T", "meta": {"hash": text[:8]},
                                                          "rows": [text]})


class PackRefusalTest(unittest.TestCase):
    """A computer applies a pack only when every check passes, and never moves its cursor past one that fails."""

    def setUp(self):
        self.world = World()
        (self.a, self.b, self.c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for c in (self.a, self.b, self.c):
            c.sync()

    def tearDown(self):
        self.world.close()

    def now_ms(self):
        return int(self.world.now * 1000)

    def assert_stalls_at(self, reader, seq, words):
        cursor = state.get(reader.db, "cursor", 0)
        report = reader.sync()
        self.assertIsNotNone(report.stalled, "the pack must be refused")
        self.assertIn(words, report.stalled)
        self.assertLess(state.get(reader.db, "cursor", 0), seq)
        self.assertEqual(state.get(reader.db, "cursor", 0), cursor)
        self.assertTrue(any(words in p for p in reader.client.status()["problems"]))

    def test_changed_byte_is_refused(self):
        key = self.a.item("notes", "x", "a note")
        self.a.sync()
        stored = self.world.fake.group(EMAIL).packs[-1]
        honest = stored["blob"]
        stored["blob"] = honest[:-20] + bytes([honest[-20] ^ 1]) + honest[-19:]
        self.assert_stalls_at(self.b, stored["seq"], "age refused")
        self.assertFalse(self.b.has("notes", key))
        stored["blob"] = honest
        self.assertIsNone(self.b.sync().stalled)
        self.assertTrue(self.b.has("notes", key), "once the relay serves the real pack, it applies")

    def test_signature_from_another_device_is_refused(self):
        seq, _ = self.world.forge(self.a, [item(self.a.key("forged"), "forged text", self.now_ms())], signer=self.c)
        self.assert_stalls_at(self.b, seq, "signature")
        self.assertIn(f"Sync is stopped at pack {seq}: pack signature does not verify.", self.b.client.status()["problems"],
                      "a pack whose signature fails is not named by the author it claims")
        self.assertFalse(self.b.has("notes", self.a.key("forged")))

    def test_epoch_mismatch_is_refused(self):
        seq, _ = self.world.forge(self.a, [item(self.a.key("e"), "epoch text", self.now_ms())], header={"epoch": 2},
                                  seal_epoch=1, listed_epoch=1)
        self.assert_stalls_at(self.b, seq, "epoch")

    def test_chain_gap_stalls_instead_of_dropping(self):
        first = self.a.item("notes", "first", "first note")
        self.a.sync()
        second = self.a.item("notes", "second", "second note")
        self.a.sync()
        group = self.world.fake.group(EMAIL)
        dropped = group.packs[-2]
        group.packs.remove(dropped)
        self.assert_stalls_at(self.b, group.packs[-1]["seq"], "gap")
        self.assertFalse(self.b.has("notes", first))
        self.assertFalse(self.b.has("notes", second), "a later pack never applies past a missing one")

    def test_removed_device_packs_are_never_applied(self):
        late = item(self.c.key("late"), "written after removal", self.now_ms())
        removal = self.a.client.remove("computer 3", self.world.browser(), clear=True)
        self.assertTrue(removal.recovery_code_due, "the removed computer still knows the recovery code")
        self.b.sync()
        seq, _ = self.world.forge(self.c, [late], seal_epoch=1)
        report = self.b.sync()
        self.assertIsNone(report.stalled, "a pack signed after the removal is passed over, not waited for")
        self.assertFalse(self.b.has("notes", self.c.key("late")))
        self.assertGreaterEqual(state.get(self.b.db, "cursor", 0), seq)

    def test_record_under_a_foreign_prefix_is_refused(self):
        group = self.world.fake.group(EMAIL)
        for sid in (self.b.key("planted"), f"{self.b.prefix}@laptop:planted"):
            with self.subTest(source_id=sid):
                seq, _ = self.world.forge(self.a, [item(sid, "planted text", self.now_ms())])
                self.assert_stalls_at(self.c, seq, "prefix")
                self.assertIn(f"Sync is stopped at pack {seq} from computer 1: record outside its author's prefix.",
                              self.c.client.status()["problems"])
                self.assertFalse(self.c.has("notes", sid))
                group.packs = [p for p in group.packs if p["seq"] != seq]

    def test_record_without_an_install_prefix_is_refused(self):
        seq, _ = self.world.forge(self.a, [item("notes:planted", "planted text", self.now_ms())])
        self.assert_stalls_at(self.c, seq, "install prefix")
        self.assertFalse(self.c.has("notes", "notes:planted"))

    def test_a_record_whose_text_is_not_unicode_stops_the_pull_at_its_whole_pack(self):
        # JSON can carry a lone surrogate. Such a record does not parse, so the whole pack is refused and sync stops
        # there and says why: no record in the pack applies, and none is dropped in silence.
        fine, odd = self.a.key("fine"), self.a.key("odd")
        escaped = lambda rec: json.dumps(records.to_wire(rec), separators=(",", ":"))  # as a hostile device writes it
        with mock.patch.object(records, "to_json", escaped):
            seq, _ = self.world.forge(self.a, [item(fine, "a fine note", self.now_ms()),
                                               item(odd, "text \ud800", self.now_ms())])
        self.assert_stalls_at(self.c, seq, "bad record")
        self.assertFalse(self.c.has("notes", fine), "nothing in the refused pack applies")
        self.assertFalse(self.c.has("notes", odd))

    def test_version_more_than_a_day_ahead_is_refused(self):
        seq, _ = self.world.forge(self.a, [item(self.a.key("future"), "future text", self.now_ms() + 2 * DAY_MS)])
        self.assert_stalls_at(self.b, seq, "day ahead")


if __name__ == "__main__":
    unittest.main()
