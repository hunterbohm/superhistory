import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from kb import search, store


def iso(delta=timedelta(0)):
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def long_body(term):
    return f"{term} leads the first line\n" + "padding line of ordinary text\n" * 200


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


class OldRowTest(TempDB):
    """Rows an older kb stored, which today's mask() changes, so a reader masks them before cutting them."""

    def old(self, sid, content, kind="doc", meta='{}'):
        self.db.execute("INSERT INTO docs(kind, source, source_id, ts, title, content, metadata) "
                        "VALUES (?, 's', ?, ?, 't', ?, ?)", (kind, sid, iso(), content, meta))
        self.db.commit()

    def test_a_match_far_from_a_masked_secret_stays_in_the_passage(self):
        self.old("d.md", "token sk-ant-api03-old-" + "Z" * 24 + " " + "ordinary filler " * 100
                 + "walrus deployment was approved by Dana.")
        hit = search.search(self.db, "walrus deployment")[0]
        for passage in (hit["snippet"], hit["excerpt"]):
            self.assertIn("walrus", passage)
            self.assertNotIn("Z" * 12, passage)
        self.assertIn("[walrus]", hit["snippet"])

    def test_a_secret_across_three_chunks_is_masked_in_every_passage(self):
        line = ("T2xkUGVtQm9keUxpbmU" * 4)[:64]
        key = "-----BEGIN PRIVATE KEY-----\n" + f"{line}\n" * 90 + "-----END PRIVATE KEY-----"
        parts = store.chunk("p" * 3820 + "\n" + key + "\nwalrus deployment notes\n")
        self.assertEqual(len(parts), 3)
        for i, part in enumerate(parts):
            self.old("k.md", part, kind="raw", meta=f'{{"chunk": {i}, "chunks": 3}}')
        hit = search.search(self.db, "walrus deployment")[0]
        self.assertEqual(hit["meta"]["chunk"], 2, "the match is in the chunk without the key's header")
        for text in (hit["snippet"], hit["excerpt"], hit["content"]):
            self.assertNotIn(line[:12], text)
        self.assertIn("walrus", hit["excerpt"])

    def test_a_stemmed_match_stays_in_the_passage(self):
        self.old("r.md", "ordinary filler " * 150 + "The run was approved by Dana.")
        hit = search.search(self.db, "running")[0]
        self.assertIn("[run] was approved", hit["snippet"])
        self.assertIn("run was approved", hit["excerpt"])

    def test_an_old_item_whose_id_looks_like_a_secret_is_left_out(self):
        self.old("desk:key-sk-ant-api03-old-" + "Z" * 24 + ".md", "walrus deployment")
        self.assertEqual(search.search(self.db, "walrus deployment"), [])

    def test_an_old_row_gets_the_card_a_current_row_gets_for_the_same_text(self):
        key = "sk-ant-api03-old-" + "Z" * 24
        text = "ordinary filler " * 60 + f"the walrus deployment key is {key} for now " + "more filler " * 60
        self.add("now.md", text)
        self.old("then.md", text)
        hits = {h["source_id"]: h for h in search.search(self.db, "walrus deployment")}
        old, current = hits["then.md"], hits["now.md"]
        self.assertEqual((old["snippet"], old["excerpt"]), (current["snippet"], current["excerpt"]))
        self.assertIn("walrus deployment key", old["excerpt"])
        self.assertNotIn("Z" * 12, old["excerpt"])
        self.assertLess(len(old["excerpt"]), search.EXCERPT_CHARS)

    def test_current_rows_keep_the_index_snippet(self):
        self.add("d.md", "ordinary filler " * 100 + "walrus deployment was approved by Dana.")
        hit = search.search(self.db, "walrus deployment")[0]
        self.assertIn("[walrus] [deployment]", hit["snippet"])


class FtsQueryTest(unittest.TestCase):
    def test_words_are_quoted_and_or_joined(self):
        self.assertEqual(search.fts_query("hello world"), '"hello" OR "world"')

    def test_stopwords_dropped(self):
        self.assertEqual(search.fts_query("how do I fix the build"), '"fix" OR "build"')

    def test_only_stopwords_gives_empty_query(self):
        self.assertEqual(search.fts_query("what is the"), "")
        self.assertEqual(search.fts_query("   "), "")
        self.assertEqual(search.fts_query(""), "")
        self.assertEqual(search.fts_query('"the"'), "")

    def test_quoted_phrase_kept_whole(self):
        self.assertEqual(search.fts_query('"exact phrase" other'), '"exact phrase" OR "other"')
        self.assertEqual(search.fts_query('"the end"'), '"the end"')

    def test_duplicates_collapse(self):
        self.assertEqual(search.fts_query("foo foo FOO"), '"foo" OR "FOO"')

    def test_terms_are_the_pieces_of_the_query(self):
        self.assertEqual(search.fts_terms("hello world"), ['"hello"', '"world"'])
        self.assertEqual(search.fts_terms('"exact phrase" other'), ['"exact phrase"', '"other"'])
        self.assertEqual(search.fts_terms("the"), [])

    def test_punctuation_stays_inside_quotes(self):
        self.assertEqual(search.fts_query("ERR_CONNECTION_REFUSED"), '"ERR_CONNECTION_REFUSED"')
        self.assertEqual(search.fts_query("--force"), '"--force"')
        self.assertEqual(search.fts_query("~/code/tools/app.py"), '"~/code/tools/app.py"')

    def test_stray_quotes_do_not_break_syntax(self):
        for q in ('say "hi', 'a"b', '"', '""', 'x " y', "NEAR(a b)", "a AND b OR c", "foo*", "col:val", "-x", "(", "^a"):
            with self.subTest(q):
                db = sqlite3.connect(":memory:")
                self.addCleanup(db.close)
                db.executescript(store.SCHEMA)
                match = search.fts_query(q)
                if match:
                    db.execute("SELECT rowid FROM docs_fts WHERE docs_fts MATCH ?", (match,)).fetchall()


class SearchTest(TempDB):
    def titles(self, query, **kw):
        return [h["title"] for h in search.search(self.db, query, **kw)]

    def test_empty_and_stopword_queries_return_nothing(self):
        self.add("a", "something here")
        self.assertEqual(search.search(self.db, ""), [])
        self.assertEqual(search.search(self.db, "what is the"), [])
        self.assertEqual(search.search(self.db, "absent"), [])

    def test_hostile_queries_do_not_raise(self):
        self.add("a", "alpha beta")
        for q in ('"', 'NEAR(', 'a AND', '*', '(', 'x" OR "y', "col:val", "--", "\\", "'"):
            with self.subTest(q):
                search.search(self.db, q)

    def test_punctuated_terms_find_their_documents(self):
        self.add("err", "Chrome showed ERR_CONNECTION_REFUSED on localhost", title="net")
        self.add("flag", "run git push --force to overwrite", title="git")
        self.add("path", "edit /home/sam/code/tools/kb/app.py today", title="file")
        self.add("noise", "unrelated words only", title="noise")
        self.assertEqual(self.titles("ERR_CONNECTION_REFUSED"), ["net"])
        self.assertEqual(self.titles("--force"), ["git"])
        self.assertEqual(self.titles("/home/sam/code/tools/kb/app.py"), ["file"])

    def test_exact_phrase_is_adjacent_words(self):
        self.add("a", "the quick brown fox", title="adjacent")
        self.add("b", "brown things and quick things", title="scattered")
        hits = self.titles('"quick brown"')
        self.assertEqual(hits[:1], ["adjacent"])
        self.assertNotIn("scattered", hits[:1])
        self.assertEqual(self.titles('"brown quick"'), [])

    def test_stopwords_do_not_match_everything(self):
        self.add("a", "the and of it", title="stops")
        self.add("b", "zeppelin hangar", title="real")
        self.assertEqual(self.titles("the zeppelin"), ["real"])

    def test_stemming(self):
        self.add("a", "we were running tests", title="r")
        self.assertEqual(self.titles("run"), ["r"])

    def test_raw_chunk_match_returns_item_once(self):
        body = "".join(f"zephyr chunk {i}\n" + "filler words go here\n" * 40 for i in range(20))
        self.add("long", body, title="long one")
        hits = search.search(self.db, "zephyr")
        self.assertEqual([h["title"] for h in hits], ["long one"])
        self.assertEqual(hits[0]["kind"], "raw")
        self.assertIn("[zephyr]", hits[0]["snippet"])

    def test_one_result_per_item_keeps_its_best_row(self):
        atom = {"kind": "atom", "ts": iso(), "title": "atom", "content": "quasar fact", "metadata": {}}
        for atom_first in (False, True):
            with self.subTest(atom_first=atom_first):
                self.db.execute("DELETE FROM docs")
                if atom_first:
                    store.replace_rows(self.db, "s", "long", [atom], kinds=("atom",))
                    self.add("long", long_body("quasar"), title="long")
                else:
                    self.add("long", long_body("quasar"), title="long")
                    store.replace_rows(self.db, "s", "long", [atom], kinds=("atom",))
                self.db.commit()
                hits = search.search(self.db, "quasar")
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["kind"], "atom")

    def test_item_with_many_matching_rows_leaves_room_for_others(self):
        atoms = [{"kind": "atom", "ts": iso(), "title": f"pelican {i}", "content": "pelican pelican", "metadata": {}}
                 for i in range(search.CANDIDATES * 6)]
        store.replace_rows(self.db, "s", "busy", atoms, kinds=("atom",))
        for name in ("one", "two", "three"):
            self.add(name, f"a {name} note that mentions a pelican once among other words", title=name)
        hits = search.search(self.db, "pelican", limit=10)
        self.assertEqual({h["source_id"] for h in hits}, {"busy", "one", "two", "three"})

    def test_fusion_merges_doc_and_raw_lists(self):
        self.add("d", "marmot sighting", title="doc item")
        self.add("r", long_body("marmot"), title="raw item")
        self.assertEqual(sorted(self.titles("marmot")), ["doc item", "raw item"])

    def test_raw_matches_weigh_half(self):
        ts = iso()
        self.add("d", "marmot sighting", title="doc item", ts=ts)
        self.add("r", long_body("marmot"), title="raw item", ts=ts)
        hits = {h["title"]: h for h in search.search(self.db, "marmot")}
        self.assertAlmostEqual(hits["raw item"]["score"] / hits["doc item"]["score"], search.RAW_WEIGHT, places=6)
        self.assertEqual(self.titles("marmot"), ["doc item", "raw item"])

    def test_raw_row_matching_every_word_beats_doc_matching_one(self):
        self.add("d", "kestrel newsletter", title="one word")
        self.add("r", "kestrel falcon osprey\n" + "padding line of ordinary text\n" * 200, title="every word")
        self.assertEqual(self.titles("kestrel falcon osprey"), ["every word", "one word"])
        hits = {h["title"]: h for h in search.search(self.db, "kestrel falcon osprey")}
        self.assertAlmostEqual(hits["one word"]["score"] / (1 / (search.RRF_K + 1)), 2 / 3, places=2)

    def test_reciprocal_rank_scores(self):
        self.add("a", "narwhal narwhal narwhal", title="strong", ts="2000-01-01T00:00:00Z")
        self.add("b", "narwhal " + "filler " * 30, title="weak", ts="2000-01-01T00:00:00Z")
        hits = search.search(self.db, "narwhal")
        self.assertEqual([h["title"] for h in hits], ["strong", "weak"])
        self.assertAlmostEqual(hits[0]["score"], 1 / (search.RRF_K + 1), places=4)
        self.assertAlmostEqual(hits[1]["score"], 1 / (search.RRF_K + 2), places=4)

    def test_newer_equal_match_ranks_first(self):
        for order in (("old", "new"), ("new", "old")):
            with self.subTest(order=order):
                self.db.execute("DELETE FROM docs")
                ages = {"old": timedelta(days=900), "new": timedelta(days=1)}
                for name in order:
                    self.add(name, "identical heron text", title=name, ts=iso(ages[name]))
                self.assertEqual(self.titles("heron"), ["new", "old"])

    def test_recency_bonus_decays_toward_one(self):
        self.add("fresh", "ibis", title="fresh", ts=iso())
        fresh = search.search(self.db, "ibis")[0]["score"]
        self.db.execute("UPDATE docs SET ts=?", (iso(timedelta(days=36500)),))
        old = search.search(self.db, "ibis")[0]["score"]
        self.assertAlmostEqual(fresh / old, 2.0, places=2)

    def test_item_found_in_summaries_and_raw_text_counts_both(self):
        for i in range(3):
            self.add(f"doc{i}", "pelican", title=f"doc{i}")
        self.add("both", long_body("pelican"), title="both")
        store.replace_rows(self.db, "s", "both", [{"kind": "segment", "ts": iso(), "title": "both",
                                                   "content": "pelican " + "summary words " * 30, "metadata": {}}],
                           kinds=store.SUMMARY_KINDS)
        self.db.commit()
        self.assertEqual(self.titles("pelican")[0], "both")

    def test_note_dated_ahead_gets_no_extra_recency(self):
        for order in (("ahead", "yesterday"), ("yesterday", "ahead")):
            with self.subTest(order=order):
                self.db.execute("DELETE FROM docs")
                ages = {"ahead": -timedelta(days=30), "yesterday": timedelta(days=1)}
                for name in order:
                    self.add(name, "identical heron text", title=name, ts=iso(ages[name]))
                self.assertEqual(self.titles("heron"), ["yesterday", "ahead"])

    def test_unparseable_timestamp_is_treated_as_old(self):
        self.add("a", "ibis", title="bad", ts="garbage")
        self.add("b", "ibis", title="good", ts=iso())
        self.assertEqual(self.titles("ibis"), ["good", "bad"])

    def test_superseded_row_score_is_halved(self):
        self.add("a", "gecko", title="a")
        before = search.search(self.db, "gecko")[0]["score"]
        self.db.execute("UPDATE docs SET superseded_by=123")
        hit = search.search(self.db, "gecko")[0]
        self.assertAlmostEqual(hit["score"], before / 2, places=9)
        self.assertEqual(hit["superseded_by"], 123)

    def test_superseded_loses_to_equal_current_row(self):
        ts = iso()
        self.add("old", "gecko", title="old", ts=ts)
        self.add("new", "gecko", title="new", ts=ts)
        self.db.execute("UPDATE docs SET superseded_by=2 WHERE source_id='old'")
        self.assertEqual(self.titles("gecko"), ["new", "old"])

    def test_after_filter(self):
        self.add("a", "lynx", title="before", ts="2025-12-31T23:59:59Z")
        self.add("b", "lynx", title="on", ts="2026-01-01T00:00:00Z")
        self.add("c", "lynx", title="later", ts="2026-06-01T12:00:00Z")
        self.assertEqual(sorted(self.titles("lynx", after="2026-01-01")), ["later", "on"])
        self.assertEqual(self.titles("lynx", after="2026-02-01"), ["later"])
        self.assertEqual(self.titles("lynx", after="2027-01-01"), [])

    def test_after_filter_applies_to_raw_rows(self):
        self.add("a", long_body("lynx"), title="old long", ts="2020-01-01T00:00:00Z")
        self.assertEqual(self.titles("lynx", after="2021-01-01"), [])
        self.assertEqual(self.titles("lynx", after="2019-01-01"), ["old long"])

    def test_before_filter_hides_newer_items(self):
        self.add("a", "lynx", title="early", ts="2025-03-01T00:00:00Z")
        self.add("b", "lynx", title="late", ts="2026-03-01T00:00:00Z")
        self.add("c", long_body("lynx"), title="late long", ts="2026-03-01T00:00:00Z")
        self.assertEqual(self.titles("lynx", before="2026-01-01T00:00:00Z"), ["early"])
        self.assertEqual(self.titles("lynx", before="2025-01-01T00:00:00Z"), [])
        self.assertEqual(len(self.titles("lynx", before="2027-01-01T00:00:00Z")), 3)

    def test_before_ranks_recency_from_that_time(self):
        self.add("a", "heron", title="recent then", ts="2020-12-25T00:00:00Z")
        now_score = search.search(self.db, "heron")[0]["score"]
        then_score = search.search(self.db, "heron", before="2021-01-01T00:00:00Z")[0]["score"]
        self.assertAlmostEqual(now_score, 1 / (search.RRF_K + 1), delta=0.001)
        self.assertGreater(then_score / now_score, 1.8)

    def test_source_filter(self):
        self.add("a", "otter", title="mail", source="gmail")
        self.add("b", "otter", title="note", source="vault")
        self.add("c", long_body("otter"), title="session", source="claude_code")
        self.assertEqual(self.titles("otter", sources=("gmail",)), ["mail"])
        self.assertEqual(self.titles("otter", sources=("claude_code",)), ["session"])
        self.assertEqual(sorted(self.titles("otter", sources=("gmail", "vault"))), ["mail", "note"])
        self.assertEqual(len(self.titles("otter")), 3)
        self.assertEqual(self.titles("otter", sources=("nope",)), [])

    def test_limit(self):
        for i in range(15):
            self.add(f"i{i}", "badger", title=f"b{i}")
        self.assertEqual(len(search.search(self.db, "badger", limit=4)), 4)
        self.assertEqual(len(search.search(self.db, "badger")), 10)

    def test_keep_skips_hits_before_the_limit_is_applied(self):
        for i in range(5):
            self.add(f"i{i}", "badger", title=f"b{i}", ts=iso(timedelta(days=i)))
        hits = search.search(self.db, "badger", limit=2, keep=lambda h: h["title"] not in ("b0", "b1"))
        self.assertEqual([h["title"] for h in hits], ["b2", "b3"])

    def test_title_match_outranks_body_match(self):
        ts = iso()
        self.add("a", "mentions walrus once", title="unrelated", ts=ts)
        self.add("b", "plain body", title="walrus", ts=ts)
        self.assertEqual(self.titles("walrus")[0], "walrus")

    def test_hit_carries_metadata_for_the_pointer(self):
        self.add("a", "capybara", meta={"path": "/tmp/x.md", "host": "laptop"})
        hit = search.search(self.db, "capybara")[0]
        self.assertEqual(search.pointer(hit["meta"]), "laptop:/tmp/x.md")
        self.assertEqual(search.pointer(hit["meta"], machine="laptop"), "/tmp/x.md")


class ExcerptTest(TempDB):
    def test_a_match_after_a_long_run_of_words_stays_in_the_excerpt(self):
        filler = " ".join(f"word{i}" for i in range(300))
        self.add("a", f"Kickoff notes. {filler} the walrus contract was signed")
        hit = search.search(self.db, "walrus contract")[0]
        self.assertIn("walrus contract was signed", hit["excerpt"])
        self.assertLessEqual(len(hit["excerpt"]), search.EXCERPT_CHARS + 2)
        self.assertNotIn("Kickoff", hit["excerpt"])

    def test_a_record_that_matches_only_in_its_title_shows_its_opening(self):
        self.add("a", "Opening line of the note. " + "more text " * 100, title="Walrus plan")
        hit = search.search(self.db, "walrus")[0]
        self.assertTrue(hit["excerpt"].startswith("Opening line of the note."))
        self.assertLessEqual(len(hit["excerpt"]), search.EXCERPT_CHARS)


class PointerTest(unittest.TestCase):
    def test_pointer_forms(self):
        self.assertEqual(search.pointer({"url": "https://example.com/y", "path": "/p"}), "https://example.com/y")
        self.assertEqual(search.pointer({"path": "/p", "host": "laptop"}), "laptop:/p")
        self.assertEqual(search.pointer({"path": "/p", "host": "studio"}), "studio:/p")
        self.assertEqual(search.pointer({"path": "/p"}), "/p")
        self.assertEqual(search.pointer({}), "")

    def test_host_prefix_appears_only_for_another_machine(self):
        meta = {"path": "/p", "host": "laptop"}
        self.assertEqual(search.pointer(meta, machine="laptop"), "/p")
        self.assertEqual(search.pointer(meta, machine="studio"), "laptop:/p")

    def test_a_url_is_never_prefixed(self):
        self.assertEqual(search.pointer({"url": "https://example.com/y", "host": "laptop"}, machine="studio"),
                         "https://example.com/y")


if __name__ == "__main__":
    unittest.main()
