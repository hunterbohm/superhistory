"""A summary another computer sends must read back the way kb and this client read it. kb's summarizer does arithmetic
and date math on the newest pass's `of_chars` and `at` and compares its `prefix`, and this client takes a stored
summary's `of_hash` from its first row. A row with any of these in the wrong type was stored, and then stopped kb's
summarizer on every run, or this client's compaction on every try, until the summary was replaced. So from_wire refuses
it, and the pull stops at its pack and says why, as for any other bad record."""
import hashlib
import unittest
from datetime import datetime, timezone

from kb import store
from kb.sync import records, state

from .support import World

TEXT = "Made-up session text that grows as the session goes on. " * 40
END = 1200
GOOD = {"of_hash": "h", "from_chars": 0, "of_chars": END, "prefix": hashlib.sha1(TEXT[:END].encode()).hexdigest()[:16],
        "at": "2026-11-01T10:00:00Z"}

BAD = {
    "of_hash a list": {**GOOD, "of_hash": []},
    "of_hash a number": {**GOOD, "of_hash": 5},
    "of_hash naming another text": {**GOOD, "of_hash": "other"},
    "of_chars text": {**GOOD, "of_chars": str(END)},
    "of_chars a fraction": {**GOOD, "of_chars": END + 0.5},
    "of_chars true": {**GOOD, "of_chars": True},
    "of_chars below zero": {**GOOD, "of_chars": -1},
    "prefix a number": {**GOOD, "prefix": 5},
    "at a number": {**GOOD, "at": 1793600000},
    "at not a time": {**GOOD, "at": "yesterday"},
    "at with no time zone": {**GOOD, "at": "2026-11-01T10:00:00"},
    "at with an invisible separator, which egress takes out": {**GOOD, "at": "2026-11-01\u200b10:00:00+00:00"},
    "at in other digits": {**GOOD, "at": "\uff12\uff10\uff12\uff16-11-01T10:00:00Z"},
    "a key egress turns into at": {**GOOD, "at\u200b": 7},
    "a key egress turns into of_chars": {**GOOD, "of_chars\u2060": "1200"},
    "a key egress turns into host": {**GOOD, "host\u200b": 5},
    "a prefix with no at": {k: v for k, v in GOOD.items() if k != "at"},
    "a prefix with no of_chars": {k: v for k, v in GOOD.items() if k != "of_chars"},
}
HONEST = {
    "a full pass, as kb writes it": GOOD,
    "an append pass": {**GOOD, "from_chars": 800},
    "a time with an offset": {**GOOD, "at": "2026-11-01T11:00:00+01:00"},
    "a pass from before kb recorded its span": {"of_hash": "h"},
    "a row with no metadata kb reads back": {},
}


def summary(source_id, meta, of_hash="h"):
    row = {"kind": "segment", "ts": "2026-10-01", "title": "t", "content": "made-up summary", "metadata": meta}
    return records.Record("summary", "notes", source_id, 1, {"of_hash": of_hash, "rows": [row]})


class SummaryContractTest(unittest.TestCase):
    def test_a_summary_kb_would_read_back_wrong_stops_the_pull_at_its_pack_and_says_why(self):
        world = World()
        self.addCleanup(world.close)
        (a, b), _ = world.group("computer 1", "computer 2")
        plain = a.item("notes", "plain", "made-up text")
        a.sync()
        b.sync()
        world.forge(a, [summary(plain, BAD["of_hash a list"])])
        world.tick()
        report = b.sync()
        self.assertFalse(b.client.compact(force=True), "compaction waits for the stopped pull, and nothing it reads fails")
        self.assertRegex(report.stalled or "", "bad record")
        self.assertEqual(b.db.execute("SELECT count(*) FROM docs WHERE kind IN ('segment', 'atom')").fetchone(), (0,))

    def test_an_append_pass_whose_rows_name_two_texts_still_leaves_and_arrives(self):
        # kb's append pass adds its rows and keeps the earlier pass's, each naming the text it summarized.
        world = World()
        self.addCleanup(world.close)
        (a, b), _ = world.group("computer 1", "computer 2")
        long = a.item("notes", "long", TEXT)
        a.sync()
        b.sync()
        row = {"kind": "segment", "ts": "2026-10-01", "title": "pass", "content": "made-up summary"}
        store.replace_rows(a.db, "notes", long, [{**row, "metadata": {**GOOD, "of_hash": "first text"}}],
                               store.SUMMARY_KINDS)
        store.replace_rows(a.db, "notes", long, [{**row, "metadata": {
            **GOOD, "of_hash": "grown text", "from_chars": END, "of_chars": len(TEXT),
            "prefix": hashlib.sha1(TEXT.encode()).hexdigest()[:16]}}], ())
        a.sync()
        self.assertIsNone(b.sync().stalled)
        self.assertEqual(b.db.execute("SELECT count(*) FROM docs WHERE source_id=? AND kind='segment'", (long,)).fetchone(),
                         (2,))

    def test_a_summary_queued_before_the_check_still_leaves(self):
        # A client from before this check queued its summaries with each row's of_hash as stored.
        world = World()
        self.addCleanup(world.close)
        (a, b), _ = world.group("computer 1", "computer 2")
        long = a.item("notes", "long", TEXT)
        a.sync()
        b.sync()
        row = {"kind": "segment", "ts": "2026-10-01", "title": "pass", "content": "made-up summary"}
        queued = records.Record("summary", "notes", long, 1, {"of_hash": "first text", "rows": [
            {**row, "metadata": {**GOOD, "of_hash": "first text"}}, {**row, "metadata": {**GOOD, "of_hash": "grown text"}}]})
        with state.tx(a.db):
            state.add_outbox(a.db, [records.to_json(queued)])
        a.sync()
        self.assertEqual(state.count_outbox(a.db), 0)
        self.assertIsNone(b.sync().stalled)
        self.assertEqual(b.db.execute("SELECT count(*) FROM docs WHERE source_id=? AND kind='segment'", (long,)).fetchone(),
                         (2,))

    def test_a_summary_whose_first_row_names_no_text_leaves_compaction_working(self):
        world = World()
        self.addCleanup(world.close)
        (a, b), _ = world.group("computer 1", "computer 2")
        plain = a.item("notes", "plain", "made-up text")
        a.sync()
        b.sync()
        row = {"kind": "segment", "ts": "2026-10-01", "title": "t", "content": "made-up summary"}
        world.forge(a, [records.Record("summary", "notes", plain, 1, {"of_hash": "h", "rows": [
            {**row, "metadata": {}}, {**row, "metadata": {"of_hash": "h"}}]})])
        world.tick()
        self.assertIsNone(b.sync().stalled)
        self.assertTrue(b.client.compact(force=True))

    def test_each_wrong_type_is_refused_and_kb_s_own_rows_pass(self):
        for name, meta in BAD.items():
            with self.subTest(refused=name), self.assertRaises(records.BadRecord):
                records.from_wire(records.to_wire(summary("c-aaaaaaaaaaaaa:one", meta)))
        for name, meta in HONEST.items():
            with self.subTest(accepted=name):
                records.from_wire(records.to_wire(summary("c-aaaaaaaaaaaaa:one", meta)))

    def test_an_item_whose_metadata_egress_would_make_unreadable_is_refused(self):
        item = {"ts": "2026-10-01", "title": "Note", "rows": ["made-up text"]}
        for name, meta in (("a key egress turns into host", {"hash": "h", "host\u200b": 5}),
                           ("a key egress turns into hash", {"hash": "h", "hash\u200b": 5})):
            with self.subTest(refused=name), self.assertRaises(records.BadRecord):
                records.from_wire(records.to_wire(records.Record("item", "notes", "c-aaaaaaaaaaaaa:one", 1,
                                                                 {**item, "meta": meta})))
        records.from_wire(records.to_wire(records.Record("item", "notes", "c-aaaaaaaaaaaaa:one", 1, {
            **item, "meta": {"hash": "h", "host": "laptop", "path": "/notes/a.md"}})))

    def test_whatever_from_wire_accepts_kb_s_summarizer_reads(self):
        from kb import summarize
        now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        for name, meta in {**BAD, **HONEST}.items():
            try:
                records.from_wire(records.to_wire(summary("c-aaaaaaaaaaaaa:one", meta)))
            except records.BadRecord:
                continue
            with self.subTest(accepted=name):
                summarize.plan(TEXT, meta, now)
                summarize.waits(len(TEXT), meta, now)


if __name__ == "__main__":
    unittest.main()
