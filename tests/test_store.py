import json
import os
import random
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from kb import store


def iso(delta=timedelta(0)):
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def long_body(term):
    return f"{term} leads the first line\n" + "padding line of ordinary text\n" * 200


def summary_row(kind, text):
    return {"kind": kind, "ts": iso(), "title": kind, "content": text, "metadata": {}}


class TempDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "data", "kb.sqlite")
        self.db = store.connect(self.path)
        self.addCleanup(self.db.close)

    def add(self, sid, content, title="t", ts=None, source="s", meta=None):
        changed = store.store_item(self.db, source, sid, ts or iso(), title, content, meta or {})
        self.db.commit()
        return changed

    def rows(self, **where):
        sql = "SELECT kind, source, source_id, title, content, metadata FROM docs"
        if where:
            sql += " WHERE " + " AND ".join(f"{k}=?" for k in where)
        return self.db.execute(sql + " ORDER BY id", tuple(where.values())).fetchall()

    def fts_ids(self, term):
        return sorted(r[0] for r in self.db.execute(
            "SELECT rowid FROM docs_fts WHERE docs_fts MATCH ?", (f'"{term}"',)))

    def assert_fts_consistent(self):
        self.db.execute("INSERT INTO docs_fts(docs_fts) VALUES('integrity-check')")


class ConnectTest(TempDB):
    def test_creates_missing_folders_and_reopens_with_data(self):
        self.add("a", "kept across connections")
        self.db.close()
        again = store.connect(self.path)
        self.addCleanup(again.close)
        self.assertEqual(again.execute("SELECT content FROM docs").fetchall(), [("kept across connections",)])

    def test_readonly_connection_reads_but_cannot_write(self):
        self.add("a", "readable")
        ro = store.connect(self.path, readonly=True)
        self.addCleanup(ro.close)
        self.assertEqual(ro.execute("SELECT count(*) FROM docs").fetchone()[0], 1)
        with self.assertRaises(sqlite3.OperationalError):
            ro.execute("DELETE FROM docs")

    def test_exclusive_lock_is_held_by_one_run_at_a_time(self):
        lock = os.path.join(self.tmp.name, "data", "kb.lock")
        with store.exclusive(lock) as first:
            self.assertTrue(first)
            with store.exclusive(lock) as second:
                self.assertFalse(second)
        with store.exclusive(lock) as again:
            self.assertTrue(again)


class ChunkTest(unittest.TestCase):
    def assert_lossless_and_bounded(self, text):
        parts = store.chunk(text)
        self.assertTrue(parts)
        self.assertTrue(all(len(p) <= 4000 for p in parts), [len(p) for p in parts])
        self.assertEqual("".join(parts), text)
        return parts

    def test_short_text_is_one_chunk(self):
        self.assertEqual(store.chunk("hello"), ["hello"])
        self.assertEqual(store.chunk("x" * 4000), ["x" * 4000])

    def test_just_over_the_limit_splits(self):
        parts = self.assert_lossless_and_bounded("x\n" * 2001)
        self.assertEqual(len(parts), 2)

    def test_many_lines(self):
        text = "".join(f"line {i} " + "w" * (i % 97) + "\n" for i in range(3000))
        parts = self.assert_lossless_and_bounded(text)
        self.assertGreater(len(parts), 5)

    def test_one_line_longer_than_the_limit(self):
        for n in (4001, 8000, 8001, 12345):
            with self.subTest(n):
                self.assert_lossless_and_bounded("y" * n)

    def test_long_line_between_normal_lines(self):
        text = "head\n" + "z" * 9000 + "\ntail\n"
        parts = self.assert_lossless_and_bounded(text)
        self.assertIn("head\n", parts[0])
        self.assertTrue(parts[-1].endswith("tail\n"))

    def test_odd_line_breaks_and_unicode(self):
        text = ("a\r\nb\x0bc\x0cd eé\U0001f600\n" * 800) + "no newline at end"
        self.assert_lossless_and_bounded(text)

    def test_random_inputs(self):
        rng = random.Random(7)
        alphabet = "ab \n\t.é"
        for _ in range(60):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(3900, 20000))) + "!"
            self.assert_lossless_and_bounded(text)


class StoreItemTest(TempDB):
    def test_short_item_is_one_doc_row(self):
        self.assertTrue(self.add("a", "short body", title="Title", meta={"url": "http://x"}))
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        kind, source, sid, title, content, meta = rows[0]
        self.assertEqual((kind, source, sid, title, content), ("doc", "s", "a", "Title", "short body"))
        self.assertIn('"url": "http://x"', meta)
        self.assertIn('"hash"', meta)

    def test_a_long_items_text_comes_back_whole_from_its_chunks(self):
        for tail in ("\u2028", "\n   \n", "\u2029\x85"):
            body = "x" * 8000 + tail
            self.add("whole", body)
            rid = self.db.execute("SELECT id FROM docs WHERE source_id='whole' LIMIT 1").fetchone()[0]
            self.assertEqual("".join(r[4] for r in store.item_rows(self.db, rid)), body, repr(tail))

    def test_long_item_is_raw_chunks_only(self):
        body = "".join(f"line {i}\n" for i in range(2000))
        self.assertGreater(len(body), 4000)
        self.add("big", body)
        rows = self.rows()
        self.assertGreater(len(rows), 1)
        self.assertEqual({r[0] for r in rows}, {"raw"})
        self.assertTrue(all(len(r[4]) <= 4000 for r in rows))
        self.assertEqual("".join(r[4] for r in rows), body)
        chunks = [self.db.execute("SELECT json_extract(metadata,'$.chunk'), json_extract(metadata,'$.chunks') "
                                  "FROM docs WHERE id=?", (i,)).fetchone()
                  for i in range(1, len(rows) + 1)]
        self.assertEqual(chunks, [(i, len(rows)) for i in range(len(rows))])

    def test_item_rows_returns_the_whole_item_in_chunk_order_from_any_row(self):
        body = "".join(f"line {i}\n" for i in range(2000))
        self.add("big", body)
        self.add("other", "unrelated")
        store.replace_rows(self.db, "s", "big", [summary_row("atom", "a fact")], kinds=("atom",))
        self.db.commit()
        atom_id = self.db.execute("SELECT id FROM docs WHERE kind='atom'").fetchone()[0]
        for rid in (1, atom_id):
            with self.subTest(rid):
                self.assertEqual("".join(r[4] for r in store.item_rows(self.db, rid)), body)

    def test_item_rows_of_a_short_item_is_its_doc(self):
        self.add("a", "just this")
        rows = store.item_rows(self.db, 1)
        self.assertEqual([(r[1], r[3], r[4]) for r in rows], [("s", "t", "just this")])

    def test_rerun_is_a_noop(self):
        self.assertTrue(self.add("a", "same body", title="same"))
        before = self.db.execute("SELECT id FROM docs").fetchall()
        self.assertFalse(self.add("a", "same body", title="same"))
        self.assertEqual(self.db.execute("SELECT id FROM docs").fetchall(), before)

    def test_rerun_of_long_item_is_a_noop(self):
        body = long_body("needle")
        self.assertTrue(self.add("a", body))
        ids = self.db.execute("SELECT id FROM docs").fetchall()
        self.assertFalse(self.add("a", body))
        self.assertEqual(self.db.execute("SELECT id FROM docs").fetchall(), ids)

    def test_title_change_counts_as_change(self):
        self.add("a", "body", title="one")
        self.assertTrue(self.add("a", "body", title="two"))
        self.assertEqual([r[3] for r in self.rows()], ["two"])

    def test_changed_content_replaces_item_rows(self):
        self.add("a", long_body("alpha"))
        self.add("b", "other item")
        self.assertTrue(self.add("a", "now short and about beta"))
        rows = self.rows(source_id="a")
        self.assertEqual([(r[0], r[4]) for r in rows], [("doc", "now short and about beta")])
        self.assertEqual(len(self.rows(source_id="b")), 1)

    def test_changed_long_content_keeps_summaries_until_it_turns_short(self):
        self.add("a", long_body("alpha"))
        store.replace_rows(self.db, "s", "a", [summary_row("segment", "segment summary"),
                                               summary_row("atom", "atom fact")], kinds=store.SUMMARY_KINDS)
        self.db.commit()
        self.assertTrue(self.add("a", long_body("gamma")))
        kinds = [r[0] for r in self.rows(source_id="a")]
        self.assertEqual((kinds.count("segment"), kinds.count("atom"), kinds.count("doc")), (1, 1, 0))
        self.assertTrue(self.add("a", "rewritten into a short doc"))
        self.assertEqual([r[0] for r in self.rows(source_id="a")], ["doc"])

    def test_replace_rows_only_touches_given_kinds(self):
        self.add("a", long_body("alpha"))
        raw_before = len(self.rows(kind="raw"))
        store.replace_rows(self.db, "s", "a", [summary_row("segment", "s1"), summary_row("atom", "a1")],
                           kinds=store.SUMMARY_KINDS)
        store.replace_rows(self.db, "s", "a", [summary_row("atom", "a2")], kinds=("atom",))
        self.assertEqual(len(self.rows(kind="raw")), raw_before)
        self.assertEqual([r[4] for r in self.rows(kind="segment")], ["s1"])
        self.assertEqual([r[4] for r in self.rows(kind="atom")], ["a2"])
        store.replace_rows(self.db, "s", "a", [], kinds=("raw",))
        self.assertEqual(len(self.rows(kind="raw")), 0)
        self.assertEqual(len(self.rows(kind="segment")), 1)

    def test_replace_rows_leaves_other_items_alone(self):
        self.add("a", "one")
        self.add("b", "two")
        store.replace_rows(self.db, "s", "a", [], kinds=store.ITEM_KINDS)
        self.assertEqual([r[2] for r in self.rows()], ["b"])

    def test_same_source_id_in_two_sources_is_two_items(self):
        self.add("a", "one", source="gmail")
        self.add("a", "two", source="vault")
        self.assertFalse(self.add("a", "one", source="gmail"))
        self.assertEqual(len(self.rows()), 2)

    def test_secrets_never_reach_the_table(self):
        self.add("a", "deploy with sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWx and password: sw0rdfish9",
                 title="key ghp_" + "A1b2C3d4E5" * 4)
        text = " ".join(" ".join(map(str, r)) for r in self.rows())
        for secret in ("sk-ant-api03", "sw0rdfish9", "ghp_"):
            self.assertNotIn(secret, text)
        self.assertEqual(self.fts_ids("sw0rdfish9"), [])

    def test_masked_secret_rotation_is_not_a_change(self):
        self.add("a", "deploy key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWx")
        self.assertFalse(self.add("a", "deploy key sk-ant-api03-ZzYyXxWwVvUuTtSsRrQqPpOo"))

    def test_title_is_cut_to_300(self):
        self.add("a", "body", title="T" * 500)
        self.assertEqual(len(self.rows()[0][3]), 300)

    def test_delete_item_removes_every_kind(self):
        self.add("a", long_body("alpha"))
        store.replace_rows(self.db, "s", "a", [summary_row("atom", "y")], kinds=("atom",))
        self.add("b", "keep")
        store.delete_item(self.db, "s", "a")
        self.assertEqual([r[2] for r in self.rows()], ["b"])

    def test_watermarks_round_trip(self):
        self.assertIsNone(store.get_watermark(self.db, "s", "k"))
        store.set_watermark(self.db, "s", "k", 12.5)
        store.set_watermark(self.db, "s", "k", 99.25)
        self.assertEqual(store.get_watermark(self.db, "s", "k"), "99.25")

    def test_watermarks_are_kept_per_source_and_key(self):
        store.set_watermark(self.db, "s", "k", "1")
        store.set_watermark(self.db, "s", "other", "2")
        store.set_watermark(self.db, "t", "k", "3")
        self.assertEqual([store.get_watermark(self.db, *sk) for sk in (("s", "k"), ("s", "other"), ("t", "k"))],
                         ["1", "2", "3"])


class MovedItemTest(TempDB):
    def metas(self, kind):
        return [json.loads(r[5]) for r in self.rows(kind=kind)]

    def test_a_moved_file_points_to_its_new_path(self):
        self.add("a", "same words", meta={"path": "/old/Notes/a.md"})
        self.assertTrue(self.add("a", "same words", meta={"path": "/new/Notes/a.md"}))
        self.assertEqual([m["path"] for m in self.metas("doc")], ["/new/Notes/a.md"])
        self.assertFalse(self.add("a", "same words", meta={"path": "/new/Notes/a.md"}))

    def test_a_moved_long_item_keeps_its_summaries_and_points_them_to_the_new_path(self):
        body = long_body("pelican")
        self.add("a", body, meta={"path": "/old/a.md", "url": "obsidian://old"})
        store.replace_rows(self.db, "s", "a", [
            {"kind": "segment", "ts": iso(), "title": "seg", "content": "pelican summary",
             "metadata": {"path": "/old/a.md", "url": "obsidian://old", "of_hash": "h1"}}], ())
        self.db.commit()
        self.assertTrue(self.add("a", body, meta={"path": "/new/a.md"}))
        raw = self.metas("raw")
        self.assertEqual({m["path"] for m in raw}, {"/new/a.md"})
        self.assertEqual(sorted(m["chunk"] for m in raw), list(range(len(raw))))
        self.assertFalse(any("url" in m for m in raw))
        segment, = self.metas("segment")
        self.assertEqual((segment["path"], segment["of_hash"]), ("/new/a.md", "h1"))
        self.assertNotIn("url", segment)
        self.assert_fts_consistent()
        self.assertEqual(len(self.fts_ids("pelican")), 2)


class HealthTest(TempDB):
    def test_a_failure_is_listed_until_the_unit_next_succeeds(self):
        store.record_failure(self.db, "notes@laptop", RuntimeError("disk gone\nsecond line"))
        failing = store.failing(self.db)
        self.assertEqual([unit for unit, _ in failing], ["notes@laptop"])
        self.assertIn("disk gone", failing[0][1])
        self.assertNotIn("second line", failing[0][1])
        self.assertEqual(store.succeeded(self.db), [])
        store.record_ok(self.db, "notes@laptop")
        self.assertEqual(store.failing(self.db), [])
        self.assertEqual([unit for unit, _ in store.succeeded(self.db)], ["notes@laptop"])

    def test_a_failure_with_no_message_names_the_error_type(self):
        store.record_failure(self.db, "summarize", KeyError())
        self.assertIn("KeyError", store.failing(self.db)[0][1])

    def test_failures_are_per_unit(self):
        store.record_failure(self.db, "a@laptop", RuntimeError("x"))
        store.record_failure(self.db, "b@laptop", RuntimeError("y"))
        store.record_ok(self.db, "a@laptop")
        self.assertEqual([unit for unit, _ in store.failing(self.db)], ["b@laptop"])

    def test_away_keeps_the_first_miss_and_clears(self):
        store.record_away(self.db, "laptop")
        first = store.away(self.db)
        self.assertEqual([host for host, _ in first], ["laptop"])
        store.record_away(self.db, "laptop")
        self.assertEqual(store.away(self.db), first)
        store.clear_away(self.db, "laptop")
        self.assertEqual(store.away(self.db), [])

    def test_last_ingest_reads_back(self):
        self.assertIsNone(store.last_ingest(self.db))
        store.set_watermark(self.db, store.STATE, "last_ingest", "2026-01-01T00:00:00Z")
        self.assertEqual(store.last_ingest(self.db), "2026-01-01T00:00:00Z")


class RemaskTest(TempDB):
    def test_remask_removes_old_values_from_rows_and_index(self):
        leaked = "GITHUB_TOKEN=abcdef1234567890abcdef1234567890"
        self.db.execute("INSERT INTO docs(kind, source, source_id, ts, title, content) VALUES "
                        "('doc', 's', 'a', ?, 't', ?)", (iso(), f"deploy with {leaked} today"))
        self.db.commit()
        self.assertEqual(self.fts_ids("abcdef1234567890abcdef1234567890"), [1])
        self.assertEqual(store.remask(self.db), 1)
        self.assertNotIn("abcdef1234567890", self.rows()[0][4])
        self.assertEqual(self.fts_ids("abcdef1234567890abcdef1234567890"), [])
        self.assert_fts_consistent()
        self.assertEqual(store.remask(self.db), 0)
        with open(self.path, "rb") as fh:
            self.assertNotIn(b"abcdef1234567890abcdef1234567890", fh.read())


class FtsTriggerTest(TempDB):
    def test_insert_is_searchable(self):
        self.add("a", "kumquat marmalade")
        self.assertEqual(len(self.fts_ids("kumquat")), 1)
        self.assert_fts_consistent()

    def test_delete_removes_from_index(self):
        self.add("a", "kumquat marmalade")
        self.add("b", "other")
        self.db.execute("DELETE FROM docs WHERE source_id='a'")
        self.assertEqual(self.fts_ids("kumquat"), [])
        self.assertEqual(len(self.fts_ids("other")), 1)
        self.assert_fts_consistent()

    def test_update_content_and_title_reindexes(self):
        self.add("a", "kumquat marmalade", title="jarring")
        self.db.execute("UPDATE docs SET content='persimmon jam', title='canning'")
        self.assertEqual(self.fts_ids("kumquat"), [])
        self.assertEqual(self.fts_ids("jarring"), [])
        self.assertEqual(len(self.fts_ids("persimmon")), 1)
        self.assertEqual(len(self.fts_ids("canning")), 1)
        self.assert_fts_consistent()

    def test_store_item_replacement_leaves_no_stale_terms(self):
        self.add("a", long_body("kumquat"))
        self.add("a", "persimmon only now")
        self.assertEqual(self.fts_ids("kumquat"), [])
        self.assertEqual(len(self.fts_ids("persimmon")), 1)
        store.delete_item(self.db, "s", "a")
        self.assertEqual(self.fts_ids("persimmon"), [])
        self.assert_fts_consistent()

    def test_updating_other_columns_keeps_index_intact(self):
        self.add("a", "kumquat marmalade")
        self.db.execute("UPDATE docs SET superseded_by=99")
        self.assertEqual(len(self.fts_ids("kumquat")), 1)
        self.assert_fts_consistent()


if __name__ == "__main__":
    unittest.main()
