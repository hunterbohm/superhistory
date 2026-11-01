import json
import logging
import os
import random
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from kb import search, store


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

    def test_a_secret_with_a_line_separator_inside_is_masked_whole(self):
        for separator in ("\u2028", "\u2029", "\x85"):
            for text in (f"export password=abc{separator}def12345", f"export password={separator}password"):
                self.add("sep", text)
                stored = self.db.execute("SELECT content FROM docs WHERE source_id='sep'").fetchone()[0]
                self.assertEqual(stored, "export password=[masked]", repr(text))

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
        self.assertEqual(store.remask(self.db), (1, 0))
        self.assertNotIn("abcdef1234567890", self.rows()[0][4])
        self.assertEqual(self.fts_ids("abcdef1234567890abcdef1234567890"), [])
        self.assert_fts_consistent()
        self.assertEqual(store.remask(self.db), (0, 0))
        with open(self.path, "rb") as fh:
            self.assertNotIn(b"abcdef1234567890abcdef1234567890", fh.read())


class MaskVersionTest(TempDB):
    """A row records the mask() version it was masked with. Only a row of today's version, and for a chunk only if
    every chunk of its item is, may be cut as it is stored; ingest and remask bring the rest up to date."""
    TOKEN = "sk-ant-api03-version-" + "Q" * 24

    def old(self, sid, content, kind="doc", meta=None, version=None):
        self.db.execute("INSERT INTO docs(kind, source, source_id, ts, title, content, metadata, masked) "
                        "VALUES (?, 's', ?, ?, 't', ?, ?, ?)", (kind, sid, iso(), content, json.dumps(meta or {}),
                                                                 version))
        self.db.commit()

    def ids(self, sid=None):
        sql, args = "SELECT id FROM docs", ()
        if sid:
            sql, args = sql + " WHERE source_id=?", (sid,)
        return [r[0] for r in self.db.execute(sql + " ORDER BY id", args)]

    def test_rows_store_writes_are_current(self):
        self.add("a", "short note")
        self.add("b", long_body("kumquat"))
        self.db.commit()
        self.assertEqual(store.current(self.db, self.ids()), set(self.ids()))
        self.assertEqual(store.old_rows(self.db), 0)

    def test_rows_of_no_version_or_another_are_old(self):
        self.add("a", "short note")
        self.old("b", "older note")
        self.old("c", "older note", version="0123456789abcdef")
        self.assertEqual(store.current(self.db, self.ids()), set(self.ids("a")))
        self.assertEqual(store.old_rows(self.db), 2)

    def test_a_chunk_is_current_only_while_every_chunk_of_its_item_is(self):
        self.add("a", long_body("kumquat"))
        chunks = self.ids("a")
        self.db.execute("UPDATE docs SET masked=NULL WHERE id=?", (chunks[-1],))
        self.assertEqual(store.current(self.db, chunks), set())

    def test_a_new_mask_version_makes_every_stored_row_old(self):
        self.add("a", "short note")
        with mock.patch.object(store, "VERSION", "next"):
            self.assertEqual(store.current(self.db, self.ids()), set())
            self.assertEqual(store.old_rows(self.db), 1)
            store.remask(self.db)
            self.assertEqual(store.current(self.db, self.ids()), set(self.ids()))

    def test_a_store_from_before_versions_gains_the_column_with_every_row_old(self):
        path = os.path.join(self.tmp.name, "old.sqlite")
        old = sqlite3.connect(path)
        old.executescript(store.SCHEMA.replace("  superseded_by INTEGER,\n  masked TEXT\n", "  superseded_by INTEGER\n"))
        self.assertNotIn("masked", [column[1] for column in old.execute("PRAGMA table_info(docs)")])
        old.execute("INSERT INTO docs(kind, source, source_id, ts, title, content) VALUES ('doc','s','a',?,'t','x')",
                    (iso(),))
        old.commit()
        old.close()
        reader = store.connect(path, readonly=True)
        self.addCleanup(reader.close)
        self.assertEqual((store.current(reader, [1]), store.old_rows(reader)), (set(), 1))
        writer = store.connect(path)
        self.addCleanup(writer.close)
        self.assertEqual((store.current(writer, [1]), store.old_rows(writer)), (set(), 1))
        self.assertEqual(store.remask(writer), (0, 0))
        self.assertEqual(store.current(writer, [1]), {1})

    def test_replace_rows_refuses_an_id_or_source_that_looks_like_a_secret(self):
        row = {"kind": "doc", "ts": iso(), "title": "t", "content": "c", "metadata": {}}
        for source, sid in (("s", f"desk:key-{self.TOKEN}.md"), (self.TOKEN, "a")):
            with self.assertRaises(ValueError) as caught:
                store.replace_rows(self.db, source, sid, [row])
            self.assertNotIn(self.TOKEN[:12], str(caught.exception))
        self.assertEqual(self.ids(), [])

    def test_an_id_without_a_host_is_checked_whole(self):
        for sid in ("password:walrus2secret9", "https://user:walrus9pw@example.com/notes",
                    "Cookie:sessionid=Walrus2Secret987654", "cookie:sessionid=Walrus2Secret987654", "token=>walrus2secret9",
                    "token2:walrus2secret9", "my-password:walrus2secret9"):
            with self.subTest(sid=sid):
                self.assertTrue(store.secret_id("notes", sid), "with no host given")
                self.assertTrue(store.secret_id("notes", f"desk:{sid}", names=("desk",)), "under a host")
                if not sid.startswith(("token2", "my-password")):  # these two can be hosts, if a row says so
                    self.assertTrue(store.secret_id("notes", sid, names=(sid.partition(":")[0],)), "no host is named so")
                with self.assertRaises(ValueError):
                    store.replace_rows(self.db, "notes", sid, [{"kind": "doc", "ts": iso(), "title": "t",
                                                                "content": "c", "metadata": {}}])
                self.old(sid, "walrus deployment")
        self.assertEqual(search.search(self.db, "walrus deployment"), [], "old rows with such ids are left out")
        store.remask(self.db)
        self.assertEqual(self.ids(), [], "and remask deletes them")

    def test_the_host_and_the_readers_id_are_checked_apart(self):
        for host in ("desk", "tokenbox", "secret-lab", "studio-pw", "passwordless", "token2"):
            with self.subTest(host=host):
                self.assertFalse(store.secret_id("notes", f"{host}:Areas/notes-2026-10-27.md", names=(host,)))
                self.assertTrue(store.secret_id("notes", f"{host}:key-{self.TOKEN}.md", names=(host,)))
        self.assertTrue(store.secret_id("notes", f"{self.TOKEN}:a.md", names=(self.TOKEN,)), "a host that is a token")
        self.assertTrue(store.secret_id(self.TOKEN, "desk:a.md", names=("desk",)), "a source that is a token")
        self.assertTrue(store.secret_id("notes", "tokenbox:walrus2secret9.md"), "with no host given, read whole")
        row = {"kind": "doc", "ts": iso(), "title": "t", "content": "c", "metadata": {"host": "tokenbox"}}
        store.replace_rows(self.db, "notes", "tokenbox:Areas/notes-2026-10-27.md", [row])
        self.assertEqual(len(self.ids()), 1, "the row's own host, in its metadata, says how its id is read")

    def test_a_whole_key_with_short_lines_is_masked(self):
        body = ["MC4CAQAw", "BQYDK2Vw", "BCIEIAAB", "AgMEBQYH", "CAkKCwwN", "Dg8QERIT", "FBUWFxgZ", "GhscHR4f"]
        for brk in ("\n", "\r\n", "\\n"):
            with self.subTest(brk=brk):
                key = brk.join(["-----BEGIN PRIVATE KEY-----", *body, "-----END PRIVATE KEY-----"])
                self.assertNotIn("CAkKCwwN", store.mask(key))
                self.add(f"k{len(brk)}", f"notes {key} after")
        self.old("old", "-----BEGIN PRIVATE KEY-----\n" + "\n".join(body) + "\n-----END PRIVATE KEY-----")
        store.remask(self.db)
        self.assertNotIn("CAkKCwwN", json.dumps(self.rows()))

    def test_a_cut_off_key_is_masked_through_the_end_of_its_paragraph(self):
        line = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7"
        short = ["MC4CAQAw", "BQYDK2Vw", "BCIEIAAB", "AgMEBQYH"]
        for brk in ("\n", "\r\n", "\\n"):
            with self.subTest(brk=brk):
                named = brk.join(["-----BEGIN PRIVATE KEY-----", line, "clientSecretToken: walrus2secret9"])
                spaced = brk.join(["-----BEGIN PRIVATE KEY-----", line, "clientSecretToken = walrus2secret9"])
                ran_on = brk.join(["-----BEGIN PRIVATE KEY-----", line, line + " ledger approved"])
                cut = brk.join(["-----BEGIN PRIVATE KEY-----", *short])
                for text in (named, spaced, ran_on, cut, cut + brk + "notes after", f'{{"key": "{cut}"}}'):
                    masked = store.mask(text)
                    for gone in ("walrus2secret9", *short, line[:12]):
                        self.assertNotIn(gone, masked, text[-30:])
                    self.assertEqual(masked, "[masked]" if text.startswith("-") else '{"key": "[masked]', text[-30:])
                # The next paragraph is not the key's.
                self.assertEqual(store.mask(cut + brk + brk + "notes after"), "[masked]" + brk + brk + "notes after")
                self.add(f"cut{len(brk)}", f"walrus {named}{brk}{cut}")
        self.assertNotIn("walrus2secret9", json.dumps(self.rows()))
        self.assertNotIn("CAkKCwwN", json.dumps(self.rows()))

    def test_remask_reads_an_items_host_only_when_all_its_rows_agree(self):
        sid = "token2:walrus2secret9"
        for hosts in (("token2", None), (None, "token2"), ("token2", "desk")):
            with self.subTest(hosts=hosts):
                for n, host in enumerate(hosts):
                    self.old(sid, "unrelated text" if n == 0 else "walrus ledger", kind="raw",
                             meta={"chunk": n, "chunks": 2, **({"host": host} if host else {})})
                self.assertEqual(search.search(self.db, "walrus"), [])
                store.remask(self.db)
                self.assertEqual(self.ids(sid), [], "remask deletes it")
                self.assertEqual(search.search(self.db, "walrus"), [])

    def test_an_encrypted_key_is_masked_with_its_header_fields(self):
        key = ("-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES-256-CBC,0A1B2C3D4E5F60718293A4B5C6D7E8F9"
               "\n\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\nAgMEBQYH\n-----END RSA PRIVATE KEY-----")
        self.assertEqual(store.mask(f"key: {key} done"), "key: [masked] done")

    def test_chunks_are_written_all_at_once(self):
        def chunk(text, n):
            return {"kind": "raw", "ts": iso(), "title": "t", "content": text, "metadata": {"chunk": n, "chunks": 2}}
        # Two writes that each add a chunk would be masked apart, so "password=" and its value would pass.
        for kinds in ((), ("doc",), ("segment", "atom")):
            with self.subTest(kinds=kinds), self.assertRaises(ValueError):
                store.replace_rows(self.db, "s", "desk:a", [chunk("notes password=", 0)], kinds)
        self.assertEqual(self.ids(), [])
        store.replace_rows(self.db, "s", "desk:a", [chunk("notes password=", 0), chunk("walrus2secret9 more", 1)])
        self.assertNotIn("walrus2secret9", "".join(r[4] for r in self.rows()))

    def test_an_update_by_an_older_kb_clears_the_version(self):
        # The statements an older kb runs: _repoint's, remask's, and a plain edit of a row's text.
        self.add("a", "same text", meta={"path": "/one"})
        rid = self.ids()[0]
        for sql, args in (("UPDATE docs SET metadata=? WHERE id=?", (json.dumps({"password": "walrus2secret9"}), rid)),
                          ("UPDATE docs SET title=?, content=?, metadata=? WHERE id=?", ("t", "c", "{}", rid)),
                          ("UPDATE docs SET ts=? WHERE id=?", (iso(), rid))):
            with self.subTest(sql=sql):
                self.db.execute("UPDATE docs SET masked=? WHERE id=?", (store.VERSION, rid))
                self.db.execute(sql, args)
                self.assertEqual(store.current(self.db, [rid]), set())
        self.db.execute("UPDATE docs SET superseded_by=1 WHERE id=?", (rid,))
        store.remask(self.db)
        self.assertEqual(store.current(self.db, [rid]), {rid})
        self.db.execute("UPDATE docs SET superseded_by=NULL WHERE id=?", (rid,))
        self.assertEqual(store.current(self.db, [rid]), {rid}, "a supersede mark is not the row's text")

    def test_a_rebuild_cut_short_after_the_last_batch_is_finished_by_the_next_remask(self):
        self.old("a", f"note with {self.TOKEN}")
        with mock.patch.object(store, "_rebuild", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                store.remask(self.db, seconds=60)
        self.assertEqual(store.old_rows(self.db), 0)
        self.assertTrue(store.rebuild_owed(self.db))
        self.assertEqual(store.remask(self.db, seconds=60), (0, 0))
        self.assertFalse(store.rebuild_owed(self.db))
        with open(self.path, "rb") as fh:
            self.assertNotIn(self.TOKEN.encode(), fh.read())

    def test_remask_masks_an_old_items_chunks_as_one_text(self):
        text = "." * (store.CHUNK - 16) + self.TOKEN + "." * store.CHUNK
        for n, i in enumerate(range(0, len(text), store.CHUNK)):
            self.old("long", text[i:i + store.CHUNK], kind="raw", meta={"chunk": n, "chunks": 3})
        self.assertEqual(store.remask(self.db)[1], 0)
        joined = "".join(r[4] for r in self.rows(source_id="long"))
        self.assertNotIn(self.TOKEN[:12], joined)
        self.assertNotIn(self.TOKEN[-12:], joined)
        self.assertEqual(store.current(self.db, self.ids("long")), set(self.ids("long")))

    def test_remask_deletes_an_old_item_whose_id_looks_like_a_secret(self):
        self.old(f"desk:key-{self.TOKEN}.md", "a note")
        with self.assertLogs("varve-test-remask", "WARNING") as logs:
            self.assertEqual(store.remask(self.db, log=logging.getLogger("varve-test-remask")), (1, 0))
        self.assertIn("rename it", logs.output[0], "the deletion is logged")
        self.assertNotIn(self.TOKEN[:12], logs.output[0], "with the id masked")
        self.assertEqual(self.ids(), [])
        with open(self.path, "rb") as fh:
            self.assertNotIn(self.TOKEN.encode(), fh.read())

    def test_remask_keeps_an_old_note_named_with_basic_or_bearer(self):
        for n, name in enumerate(("Courses/Basic Statistics/lecture-01.md", "Wedding/Ring Bearer duties/plan-2026.md")):
            self.old(f"desk:{name}", f"central limit theorem {n}", meta={"host": "desk"})
        store.remask(self.db)
        self.assertEqual(len(self.ids()), 2)
        self.assertEqual(len(search.search(self.db, "central limit theorem")), 2)

    def test_remask_reads_a_profile_its_rows_record_apart_from_the_id_after_it(self):
        kept = "desk:secret-project:20261028_101500_a1b2c3"
        self.old(kept, "hermes session", meta={"host": "desk", "profile": "secret-project"})
        for sid, profile in (("desk:password:walrus2secret9", "password"), ("desk:my-token:walrus2secret9", None),
                             ("desk:my-token:walrus2secret9x", "other")):
            self.old(sid, "hermes session", meta={"host": "desk", **({"profile": profile} if profile else {})})
        store.remask(self.db)
        self.assertEqual([r[0] for r in self.db.execute("SELECT DISTINCT source_id FROM docs")], [kept],
                         "a name that is a secret's word alone, or one the rows do not record, is not split off")

    def test_remask_stopped_by_its_time_leaves_the_rest_old(self):
        for i in range(120):
            self.old(f"n{i}", f"note {i} with {self.TOKEN}")
        changed, left = store.remask(self.db, seconds=0)
        self.assertEqual((changed, left), (50, 70))
        done = store.current(self.db, self.ids())
        self.assertEqual(len(done), 50)
        for rid in done:
            self.assertNotIn(self.TOKEN, self.db.execute("SELECT content FROM docs WHERE id=?", (rid,)).fetchone()[0])
        self.assertEqual(store.get_watermark(self.db, store.STATE, "remask"), "rebuild")
        self.assertEqual(store.remask(self.db, seconds=60), (70, 0))
        self.assertIsNone(store.get_watermark(self.db, store.STATE, "remask"))
        with open(self.path, "rb") as fh:
            self.assertNotIn(self.TOKEN.encode(), fh.read())

    def test_new_metadata_for_unchanged_text_leaves_the_row_old_until_remask(self):
        self.add("a", "same text", meta={"path": "/one"})
        self.add("a", "same text", meta={"path": "/two"})
        self.assertEqual(json.loads(self.rows()[0][5])["path"], "/two")
        self.assertEqual(store.old_rows(self.db), 1)
        store.remask(self.db)
        self.assertEqual(store.old_rows(self.db), 0)


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
