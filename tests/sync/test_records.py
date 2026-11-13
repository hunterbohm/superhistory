import json
import os
import tempfile
import unittest

from kb import ids, store
from kb.sync import records, state
from .support import connect, put_item, put_rows, put_summary

A = "c-aaaaaaaaaaaaa"
B = "c-bbbbbbbbbbbbb"
IDS = [f"{A}:n.md", f"{A}@laptop:n.md", f"{A}@:n.md", f"{A}@laptop", A, f"{A}:{B}:n.md", f"{A}@laptop:{B}@x:n",
       f"{B}:n.md", f"{B}@laptop:n.md", f"{A.upper()}:n.md", f"{A}x:n.md", f"{A[:-1]}:n.md", f"{A};n.md", f"{A}\n:n.md",
       f"x{A}:n.md", f" {A}:n.md", "notes:n.md", "n.md", "laptop@" + A + ":n.md"]


def rows(db):
    return db.execute("SELECT kind, source, source_id, ts, title, content, metadata FROM docs "
                      "ORDER BY source, source_id, kind, id").fetchall()


def marks(db):
    return db.execute("SELECT source, source_id, marked_by, new_source, new_source_id, at, v FROM supersedes "
                      "ORDER BY source, source_id, marked_by").fetchall()


def _chunk_as_kb_does(text, size=store.CHUNK):
    """kb's chunking, cutting a long line by copying what is left of it. It keeps a tail of only spaces, which the
    Sync client's own copy of it dropped."""
    if len(text) <= size:
        return [text]
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:size])
            line = line[size:]
        if len(cur) + len(line) > size:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur:
        chunks.append(cur)
    return chunks


class ChunkTest(unittest.TestCase):
    def test_chunks_are_kbs_whatever_the_lines(self):
        import random
        rnd = random.Random(12)
        ends = ("\n", "\r\n", "\r", "\u2028", "\x1c", "")
        for _ in range(3000):
            lines = [rnd.choice("ab ") * rnd.choice((0, 1, 7, 3999, 4000, 4001, 7999, 8000, 8001, 12345))
                     + rnd.choice(ends) for _ in range(rnd.randrange(1, 6))]
            text = "".join(lines)
            for size in (4000, 7):
                self.assertEqual(store.chunk(text, size), _chunk_as_kb_does(text, size))


class StoreToStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = self.open("a.sqlite")
        self.b = self.open("b.sqlite")
        self.now = 1_700_000_000_000

    def tearDown(self):
        self.a.close()
        self.b.close()
        self.tmp.cleanup()

    def open(self, name):
        db = connect(os.path.join(self.tmp.name, name))
        state.init(db)
        return db

    def ship(self, src, own, dst, dst_own):
        self.now += 1000
        changed = records.changes(src, own)
        with state.tx(src):
            records.stage(src, changed, self.now)
            lines = [r[0] for r in src.execute("SELECT record FROM sync_outbox ORDER BY id")]
            src.execute("DELETE FROM sync_outbox")
        recs = [records.from_wire(json.loads(line)) for line in filter(None, (records.outgoing(line, src) for line in lines))]  # as a pack carries them
        with state.tx(dst):
            records.apply(dst, recs, dst_own, frozenset())
        return recs

    def test_items_summaries_marks_and_deletes_converge(self):
        a, b = self.a, self.b
        put_item(a, "notes", f"{A}:short", "2026-10-01", "Short", "a short note", {"path": "n.md"})
        put_item(a, "notes", f"{A}:long", "2026-10-02", "Long", ("line of text\n" * 500), {"path": "l.md"})
        long_hash = a.execute("SELECT json_extract(metadata,'$.hash') FROM docs WHERE source_id=?",
                              (f"{A}:long",)).fetchone()[0]
        put_summary(a, "notes", f"{A}:long", long_hash, [("segment", "Part 1", "first half"),
                                                                     ("atom", "Fact", "a fact")])
        put_item(b, "git", f"{B}:commit", "2026-10-03", "Commit", "fix the thing", {})
        self.ship(b, B, a, A)
        store.mark_superseded(a, "git", f"{B}:commit", ("notes", f"{A}:short"), A, "2026-10-04")
        self.ship(a, A, b, B)
        self.assertEqual(rows(a), rows(b))
        self.assertEqual(marks(a), marks(b))
        self.assertEqual(store.superseded_by(b, "git", f"{B}:commit"), ("notes", f"{A}:short"))

        store.delete_item(a, "notes", f"{A}:short")
        sent = self.ship(a, A, b, B)
        self.assertEqual([r.kind for r in sent], ["gone"])
        self.assertEqual(rows(a), rows(b))

    def test_rows_and_hash_travel_as_stored_with_or_without_a_whitespace_tail(self):
        # An item's last chunk can be only whitespace, which kb keeps. The receiver takes the hash of the text it
        # stored, so a sender's hash is the true one here: a store has no other, as kb rebuilds a store with rows an
        # older kb wrote.
        for name, parts in (("before", ["a" * 4000, "a" * 4000]), ("after", ["a" * 4000, "a" * 4000, "\n"])):
            put_rows(self.a, "notes", f"{A}:{name}", "2026-10-01", "Pad", parts,
                     {"hash": store.item_hash("Pad", "".join(parts))})
        self.ship(self.a, A, self.b, B)
        self.assertEqual(rows(self.a), rows(self.b))

    def test_a_secret_cut_across_two_chunks_leaves_masked_as_one_text(self):
        # kb before #14 cut an item before masking it, so neither chunk shows the key and neither is masked.
        key = "sk-ant-api03-" + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAb7cDe0f"
        parts = ["note " + key[:8], key[8:] + " end"]
        put_rows(self.a, "notes", f"{A}:cut", "2026-10-01", "Cut", parts, {"hash": "h", "path": key})
        put_summary(self.a, "notes", f"{A}:cut", "h", [("atom", key, "the key is " + key)])
        self.ship(self.a, A, self.b, B)
        held = json.dumps(rows(self.b))
        for piece in (key[:8], key[8:20], key[-12:]):
            self.assertNotIn(piece, held)
        got = records.read_item(self.b, "notes", f"{A}:cut")
        self.assertIn("[masked]", "".join(got["rows"]))
        self.assertEqual(got["meta"]["hash"], records.item_hash(got["title"], "".join(got["rows"])),
                         "masking changed the text, so the hash is of what left, never of the key")
        self.assertEqual(records.read_summary(self.b, "notes", f"{A}:cut")["of_hash"], got["meta"]["hash"],
                         "the summary still names the item it summarizes")

    def test_a_key_glued_to_a_word_by_an_invisible_leaves_an_item_masked(self):
        # Gate 12 in a Sync pack: the item's text reaches the chokepoint as stored, invisibles included, so kb's mask
        # sees the one that bounds the key. Stripping the rows first would glue the word to the key and hide nothing.
        key = "sk-proj-" + "a1" * 24
        put_rows(self.a, "notes", f"{A}:glued", "2026-10-01", "Glued", ["budget\u200b" + key + " paid"],
                             {"hash": "h"})
        self.ship(self.a, A, self.b, B)
        self.assertNotIn(key, json.dumps(rows(self.b)))
        got = records.read_item(self.b, "notes", f"{A}:glued")
        self.assertEqual(got["meta"]["hash"], records.item_hash(got["title"], "".join(got["rows"])))

    def test_a_header_in_an_items_meta_is_masked(self):
        # Round 7: a cookie held under its header's name in an item's metadata left whole.
        put_rows(self.a, "notes", f"{A}:request", "2026-10-01", "Request", ["sent it"],
                             {"hash": "h1", "headers": {"Cookie": "session=Pw7xYzQ9mK2nL4pR"}})
        self.now += 1000
        changed = records.changes(self.a, A)
        with state.tx(self.a):
            records.stage(self.a, changed, self.now)
            (line,) = [r[0] for r in self.a.execute("SELECT record FROM sync_outbox ORDER BY id") if ":request" in r[0]]
        self.assertNotIn("Pw7x", records.outgoing(line, self.a))

    def test_a_header_whose_field_name_holds_an_invisible_leaves_masked_and_settled(self):
        # Round 8: the first pass only read the field's name as `value`; signing again changed the pack every time.
        put_rows(self.a, "notes", f"{A}:capture", "2026-10-01", "Capture", ["sent it"],
                             {"hash": "h1", "headers": [{"name": "Cookie", "va\u200blue": "session=Pw7xYzQ9mK2nL4pR"}]})
        self.now += 1000
        changed = records.changes(self.a, A)
        with state.tx(self.a):
            records.stage(self.a, changed, self.now)
            (line,) = [r[0] for r in self.a.execute("SELECT record FROM sync_outbox ORDER BY id") if ":capture" in r[0]]
        once = records.outgoing(line, self.a)
        self.assertNotIn("Pw7x", once)
        self.assertEqual(records.outgoing(once, self.a), once)

    def test_an_item_whose_invisible_goes_leaves_the_same_on_every_pass(self):
        # Round 2: a stale hash over masked text, a long chunk and a whitespace tail behind an invisible. Whatever leaves,
        # passing it through again changes nothing, so a pack signed through the mask never needs signing again.
        put_rows(self.a, "notes", f"{A}:tail", "2026-10-01", "Tail",
                             ["[masked] note\n" + "x" * 4000 + "\n\u200b   "], {"hash": "stale"})
        self.now += 1000
        changed = records.changes(self.a, A)
        with state.tx(self.a):
            records.stage(self.a, changed, self.now)
            (line,) = [r[0] for r in self.a.execute("SELECT record FROM sync_outbox ORDER BY id")
                       if '"tail"' in r[0] or ":tail" in r[0]]
        once = records.outgoing(line, self.a)
        self.assertEqual(records.outgoing(once, self.a), once)
        got = records.from_wire(json.loads(once)).body
        self.assertEqual(got["meta"]["hash"], records.item_hash(got["title"], "".join(got["rows"])))

    def test_an_item_whose_rows_change_on_the_way_out_hashes_what_leaves(self):
        # Round 3: a correct hash over text with an invisible, which leaves without it. The hash of what leaves is taken
        # at once, so the next pass keeps it and a signed pack never needs signing again.
        put_rows(self.a, "notes", f"{A}:inv", "2026-10-01", "T", ["[masked] note\u200b"],
                             {"hash": records.item_hash("T", "[masked] note\u200b")})
        self.now += 1000
        changed = records.changes(self.a, A)
        with state.tx(self.a):
            records.stage(self.a, changed, self.now)
            (line,) = [r[0] for r in self.a.execute("SELECT record FROM sync_outbox ORDER BY id") if ":inv" in r[0]]
        once = records.outgoing(line, self.a)
        self.assertEqual(records.outgoing(once, self.a), once)
        got = records.from_wire(json.loads(once)).body
        self.assertEqual(got["meta"]["hash"], records.item_hash(got["title"], "".join(got["rows"])))

    def test_delete_outranks_a_delayed_older_write(self):
        key = f"{A}:x"
        item = records.Record("item", "notes", key, 10, {"ts": "t", "title": "X", "meta": {"hash": "h1"}, "rows": ["x"]})
        gone = records.Record("gone", "notes", key, 30, {})
        late = records.Record("item", "notes", key, 20, {"ts": "t", "title": "X", "meta": {"hash": "h2"}, "rows": ["y"]})
        for rec in (item, gone, late):
            with state.tx(self.b):
                records.apply(self.b, [rec], B, frozenset())
        self.assertFalse(records.item_exists(self.b, "notes", key))

    def test_mark_by_another_computer_on_an_own_item_applies(self):
        put_item(self.a, "notes", f"{A}:old", "2026-10-01", "Old", "old text", {})
        self.ship(self.a, A, self.b, B)
        store.mark_superseded(self.b, "notes", f"{A}:old", ("notes", f"{A}:new"), B, "2026-10-02")
        self.ship(self.b, B, self.a, A)
        self.assertEqual(store.superseded_by(self.a, "notes", f"{A}:old"), ("notes", f"{A}:new"))

    def test_summary_waits_for_its_item(self):
        key = f"{A}:late"
        summary = records.Record("summary", "notes", key, 5, {"of_hash": "h", "rows": [
            {"kind": "atom", "ts": "t", "title": "F", "content": "fact", "metadata": {"of_hash": "h"}}]})
        item = records.Record("item", "notes", key, 4, {"ts": "t", "title": "L", "meta": {"hash": "h"}, "rows": ["x"]})
        with state.tx(self.b):
            records.apply(self.b, [summary], B, frozenset())
        self.assertEqual(state.count_parked(self.b), 1)
        with state.tx(self.b):
            records.apply(self.b, [item], B, frozenset())
        self.assertEqual(state.count_parked(self.b), 0)
        self.assertEqual(records.read_summary(self.b, "notes", key)["rows"][0]["content"], "fact")

    def test_own_items_are_exactly_the_ones_kbids_owner_gives_this_install(self):
        for sid in IDS:
            put_item(self.a, "notes", sid, "2026-10-01", "T", f"text of {sid}", {})
        mine = {sid for sid in IDS if ids.owner(sid) == A}
        self.assertIn(f"{A}@laptop:n.md", mine)
        self.assertNotIn(f"{A}\n:n.md", mine)
        self.assertEqual({key[2] for key, _, _ in records.changes(self.a, A)}, mine)

        records.drop_lacking(self.a, A, present=set())
        unowned = {sid for sid in IDS if not records.has_owner(sid)}
        self.assertIn("notes:n.md", unowned)
        self.assertEqual({r[0] for r in self.a.execute("SELECT DISTINCT source_id FROM docs")}, mine | unowned,
                         "a snapshot's cleanup deletes only items another install owns")

    def test_marks_on_items_no_install_owns_stay_home(self):
        put_item(self.a, "notes", f"{A}:new", "2026-10-01", "New", "replacement", {})
        store.mark_superseded(self.a, "notes", "notes:legacy", ("notes", f"{A}:new"), A, "2026-10-02")
        store.mark_superseded(self.a, "notes", f"{B}@laptop:old", ("notes", f"{A}:new"), A, "2026-10-02")
        self.assertEqual(sorted((key[0], key[2]) for key, _, _ in records.changes(self.a, A)),
                         [("item", f"{A}:new"), ("supersede", f"{B}@laptop:old")])

    def test_a_record_needs_an_install_prefix(self):
        wire = {"k": "item", "v": 1, "s": "notes", "ts": "t", "title": "T", "meta": {"hash": "h"}, "rows": ["x"]}
        self.assertEqual(records.from_wire({**wire, "i": f"{A}@laptop:n.md"}).writer, A)
        for sid in ("notes:n.md", "n.md", f"{A}\n:n.md", f"{A.upper()}:n.md", f"x{A}:n.md", ""):
            with self.subTest(source_id=sid), self.assertRaises(records.BadRecord):
                records.from_wire({**wire, "i": sid})
            with self.subTest(marked=sid), self.assertRaises(records.BadRecord):
                records.from_wire({"k": "supersede", "v": 1, "s": "notes", "i": sid, "by": A, "new": None, "at": ""})

    def test_a_record_with_text_that_is_not_unicode_is_refused(self):
        # JSON can carry a lone surrogate. SQLite refuses it only once a write is under way.
        lone = "\ud800"
        item = {"k": "item", "v": 1, "s": "notes", "i": f"{A}:n.md", "ts": "t", "title": "T", "meta": {"hash": "h"},
                "rows": ["x"]}
        row = {"kind": "atom", "ts": "t", "title": "T", "content": "c", "metadata": {}}
        for name, wire in {
            "item text": {**item, "rows": [lone]}, "item title": {**item, "title": lone},
            "item id": {**item, "i": f"{A}:{lone}"}, "item metadata": {**item, "meta": {"hash": "h", "path": lone}},
            "summary text": {"k": "summary", "v": 1, "s": "notes", "i": f"{A}:n.md", "of_hash": "h",
                             "rows": [{**row, "content": lone}]},
            "supersede target": {"k": "supersede", "v": 1, "s": "notes", "i": f"{A}:n.md", "by": A,
                                 "new": ["notes", f"{A}:{lone}"], "at": ""},
        }.items():
            with self.subTest(name), self.assertRaises(records.BadRecord):
                records.from_wire(wire)
        self.assertEqual(records.from_wire(item).source_id, f"{A}:n.md")
        self.assertEqual(records.from_wire({"k": "summary", "v": 1, "s": "notes", "i": f"{A}:n.md", "of_hash": "h",
                                            "rows": [row]}).kind, "summary")

    def test_a_record_whose_pointer_or_id_names_are_not_text_is_refused(self):
        # kb reads these as text in every search that finds the item, so anything else would fail there once stored.
        item = {"k": "item", "v": 1, "s": "notes", "i": f"{A}:n.md", "ts": "t", "title": "T", "rows": ["x"]}
        row = {"kind": "atom", "ts": "t", "title": "T", "content": "c"}
        summary = {"k": "summary", "v": 1, "s": "notes", "i": f"{A}:n.md", "of_hash": "h"}
        for field in records.TEXT_META:
            for bad in ([], {}, 5):
                with self.subTest(field=field, value=bad, kind="item"), self.assertRaises(records.BadRecord):
                    records.from_wire({**item, "meta": {"hash": "h", field: bad}})
                with self.subTest(field=field, value=bad, kind="summary"), self.assertRaises(records.BadRecord):
                    records.from_wire({**summary, "rows": [{**row, "metadata": {field: bad}}]})
            for good in ("laptop", "", None):
                with self.subTest(field=field, value=good):
                    records.from_wire({**item, "meta": {"hash": "h", field: good}})
                    records.from_wire({**summary, "rows": [{**row, "metadata": {field: good}}]})

    def test_a_write_the_store_refuses_part_way_keeps_nothing_and_the_older_item_stays(self):
        item = {"k": "item", "s": "notes", "i": f"{A}:n.md", "ts": "t", "title": "T", "meta": {"hash": "h"}}
        with state.tx(self.b):
            records.apply(self.b, [records.from_wire({**item, "v": 1, "rows": ["the first text"]})], B)

        def refused_after_deleting(db, rec):
            store.delete_item(db, rec.source, rec.source_id)
            raise ValueError("refused once the write was under way")

        with state.tx(self.b):
            skipped = records.apply(self.b, [records.from_wire({**item, "v": 2, "rows": ["the second text"]})], B,
                                    write={**records._WRITE, "item": refused_after_deleting})
        self.assertEqual(skipped, 1)
        self.assertEqual(records.read_item(self.b, "notes", f"{A}:n.md")["rows"], ["the first text"],
                         "a skipped record leaves the older item as it was")
        self.assertEqual(state.version(self.b, ("item", "notes", f"{A}:n.md", ""))[0], 1)


if __name__ == "__main__":
    unittest.main()
