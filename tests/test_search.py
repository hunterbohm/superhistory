import contextlib
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


MINE, THEIRS = "c-aaaaaaaaaaaaa", "c-bbbbbbbbbbbbb"


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
    """Rows an earlier version of mask() stored, which today's changes, so a reader masks them before cutting them."""

    def old(self, sid, content, kind="doc", meta='{}', title="t"):
        self.db.execute("INSERT INTO docs(kind, source, source_id, ts, title, content, metadata, masked) "
                        "VALUES (?, 's', ?, ?, ?, ?, ?, '0123456789abcdef')", (kind, sid, iso(), title, content, meta))
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

    def test_an_old_chunk_gets_the_card_its_current_chunk_gets(self):
        text = "prefix padding " * 180 + "\nwalrus deployment approved.\n" + "suffix padding " * 180
        self.add("now.md", text)
        parts = store.chunk(text)
        self.assertGreater(len(parts), 1)
        for i, part in enumerate(parts):
            self.old("then.md", part, kind="raw", meta=f'{{"chunk": {i}, "chunks": {len(parts)}}}')
        hits = {h["source_id"]: h for h in search.search(self.db, "walrus deployment")}
        old, current = hits["then.md"], hits["now.md"]
        self.assertEqual(old["meta"]["chunk"], current["meta"]["chunk"])
        self.assertEqual((old["snippet"], old["excerpt"]), (current["snippet"], current["excerpt"]))
        self.assertNotIn("suffix", old["excerpt"])

    def test_an_old_chunk_a_key_runs_into_gets_the_card_of_its_whole_masked_item(self):
        line = ("T2xkUGVtQm9keUxpbmU" * 4)[:64]
        key = "-----BEGIN PRIVATE KEY-----\n" + f"{line}\n" * 90 + "-----END PRIVATE KEY-----"
        text = "context " + " " * 3800 + "\n" + key + "\nwalrus deployment notes\n"
        self.add("now.md", text)
        parts = store.chunk(text)
        self.assertEqual(len(parts), 3)
        for i, part in enumerate(parts):
            self.old("then.md", part, kind="raw", meta=f'{{"chunk": {i}, "chunks": 3}}')
        hits = {h["source_id"]: h for h in search.search(self.db, "walrus deployment")}
        old, current = hits["then.md"], hits["now.md"]
        self.assertEqual(old["meta"]["chunk"], 2, "the match is in the chunk the key runs into")
        self.assertEqual((old["snippet"], old["excerpt"]), (current["snippet"], current["excerpt"]))
        self.assertIn("context", old["excerpt"])
        self.assertNotIn(line[:12], old["snippet"] + old["excerpt"])

    def test_an_old_row_matched_by_its_title_alone_gets_a_current_rows_card(self):
        for n, body in enumerate(("Opening [note] " + "ordinary filler " * 60,
                                  "token sk-ant-api03-old-" + "Z" * 24 + " " + "ordinary filler " * 60)):
            with self.subTest(body=body[:20]):
                self.add(f"now{n}.md", body, title=f"walrus{n}")
                self.old(f"then{n}.md", body, title=f"walrus{n}")
                hits = {h["source_id"]: h for h in search.search(self.db, f"walrus{n}")}
                old, current = hits[f"then{n}.md"], hits[f"now{n}.md"]
                self.assertEqual((old["snippet"], old["excerpt"]), (current["snippet"], current["excerpt"]))
                self.assertNotIn("Z" * 12, old["snippet"] + old["excerpt"])

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
        self.db.execute("UPDATE docs SET masked=?", (store.VERSION,))  # the test's edit, not an older kb's
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

    def test_superseded_item_score_is_halved(self):
        self.add("a", "gecko", title="a")
        before = search.search(self.db, "gecko")[0]["score"]
        store.mark_superseded(self.db, "s", "a", ("s", "b"), MINE)
        hit = search.search(self.db, "gecko")[0]
        self.assertAlmostEqual(hit["score"], before / 2, places=9)
        self.assertEqual(hit["superseded_by"], ("s", "b"))

    def test_superseded_loses_to_equal_current_row(self):
        ts = iso()
        self.add("old", "gecko", title="old", ts=ts)
        self.add("new", "gecko", title="new", ts=ts)
        store.mark_superseded(self.db, "s", "old", ("s", "new"), MINE)
        self.assertEqual(self.titles("gecko"), ["new", "old"])

    def test_a_mark_survives_a_refresh_of_the_item(self):
        self.add("old", "gecko first words", title="old")
        store.mark_superseded(self.db, "s", "old", ("s", "new"), MINE)
        self.add("old", "gecko changed words", title="old")
        self.assertEqual(search.search(self.db, "gecko")[0]["superseded_by"], ("s", "new"))
        store.clear_superseded(self.db, "s", "old", MINE)
        self.assertIsNone(search.search(self.db, "gecko")[0]["superseded_by"])

    def test_a_mark_and_a_deletion_give_one_answer_in_either_order(self):
        """Sync applies records one at a time as they arrive, or in a batch with marks after items. A mark from another
        computer, the item's deletion and its return reach the same store either way."""
        def apply(order):
            db = store.connect(os.path.join(self.tmp.name, f"{order}.sqlite"))
            self.addCleanup(db.close)
            steps = {"item": lambda: store.store_item(db, "s", "old", iso(), "old", "gecko", {}),
                     "mark": lambda: store.mark_superseded(db, "s", "old", ("s", "new"), THEIRS, "2026-10-01", v=1),
                     "gone": lambda: store.delete_item(db, "s", "old")}
            for step in order.split("-"):
                steps[step]()
            return store.superseded_by(db, "s", "old")

        one_at_a_time = apply("item-mark-gone-item")
        self.assertEqual(one_at_a_time, ("s", "new"))
        self.assertEqual(apply("item-gone-item-mark"), one_at_a_time, "a batch applies marks after items")

    def test_each_computer_keeps_its_own_mark_and_the_latest_wins(self):
        self.add("old", "gecko", title="old")
        store.mark_superseded(self.db, "s", "old", ("s", "theirs"), THEIRS, "2026-10-01", v=3)
        store.mark_superseded(self.db, "s", "old", ("s", "mine"), MINE, "2026-10-02", v=2)
        self.assertEqual(store.superseded_by(self.db, "s", "old"), ("s", "theirs"), "the higher version wins")
        store.mark_superseded(self.db, "s", "old", ("s", "mine"), MINE)
        self.assertEqual(store.superseded_by(self.db, "s", "old"), ("s", "mine"), "a mark not synced yet is newest")

    def test_a_later_clear_outranks_an_older_mark_from_another_computer(self):
        self.add("old", "gecko", title="old")
        store.mark_superseded(self.db, "s", "old", ("s", "a-new"), THEIRS, "2026-10-01", v=1)
        store.mark_superseded(self.db, "s", "old", ("s", "b-new"), MINE, "2026-10-02", v=2)
        store.clear_superseded(self.db, "s", "old", MINE, "2026-10-03", v=3)
        self.assertIsNone(store.superseded_by(self.db, "s", "old"), "the older mark does not win again")
        self.assertIsNone(search.search(self.db, "gecko")[0]["superseded_by"])
        store.mark_superseded(self.db, "s", "old", ("s", "a-newer"), THEIRS, "2026-10-04", v=4)
        self.assertEqual(store.superseded_by(self.db, "s", "old"), ("s", "a-newer"), "a newer mark wins over the clear")

    def test_search_over_several_stores_merges_by_score_and_names_each_store(self):
        other = store.connect(os.path.join(self.tmp.name, "other.sqlite"))
        self.addCleanup(other.close)
        self.add("a", "heron heron heron", title="strong here")
        store.store_item(other, "s", "b", iso(), "weak there", "heron and many other words besides", {})
        store.store_item(other, "s", "c", iso(), "strong there", "heron heron heron", {})
        other.commit()
        hits = search.search_stores([("mine", self.db), ("team", other)], "heron", limit=2)
        self.assertEqual(sorted((h["store"], h["title"]) for h in hits), [("mine", "strong here"), ("team", "strong there")])
        self.assertEqual(len(search.search_stores([("mine", self.db), ("team", other)], "heron")), 3)

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


class SupersedeMarkTest(TempDB):
    SECRET = "sk-ant-api03-" + "Q" * 40  # made up

    def older_row(self, sid):
        # A row an older kb wrote under an older mask, before ingest refused an id that holds a secret.
        self.db.execute("INSERT INTO docs(kind, source, source_id, ts, title, content, metadata, masked) VALUES "
                        "('doc', 's', ?, ?, 'old', 'walrus notes', '{}', 'older-mask')", (sid, iso()))
        self.db.commit()

    def test_a_mark_on_or_to_an_item_whose_id_holds_a_secret_is_refused(self):
        self.add("plain", "walrus notes")
        self.older_row(self.SECRET)
        for name, mark in (("to it", lambda: store.mark_superseded(self.db, "s", "plain", ("s", self.SECRET), MINE)),
                           ("on it", lambda: store.mark_superseded(self.db, "s", self.SECRET, ("s", "plain"), MINE)),
                           ("cleared", lambda: store.clear_superseded(self.db, "s", self.SECRET, MINE))):
            with self.subTest(name):
                with self.assertRaises(ValueError) as caught:
                    mark()
                self.assertNotIn(self.SECRET, str(caught.exception))
        self.assertEqual(self.db.execute("SELECT count(*) FROM supersedes").fetchone()[0], 0)

    def test_remask_deletes_the_item_and_every_mark_that_names_its_id(self):
        self.add("plain", "walrus notes")
        self.older_row(self.SECRET)
        # Marks written before kb refused them, or synced from another computer.
        self.db.executemany("INSERT INTO supersedes(source, source_id, marked_by, new_source, new_source_id, at, v) "
                            "VALUES (?,?,?,?,?,'2026-10-01',1)",
                            [("s", "plain", MINE, "s", self.SECRET), ("s", self.SECRET, THEIRS, "s", "plain"),
                             ("s", "plain", THEIRS, "s", "other")])
        self.db.commit()
        store.remask(self.db)
        self.assertNotIn(self.SECRET, "\n".join(self.db.iterdump()))
        self.assertNotIn(self.SECRET, repr(search.search(self.db, "walrus")))
        self.assertEqual(store.superseded_by(self.db, "s", "plain"), ("s", "other"), "other marks stay")
        with open(self.path, "rb") as fh:
            self.assertNotIn(self.SECRET.encode(), fh.read())

    def test_a_hit_never_names_a_replacement_whose_id_holds_a_secret(self):
        self.add("plain", "walrus notes")
        self.older_row(self.SECRET)
        self.db.execute("INSERT INTO supersedes(source, source_id, marked_by, new_source, new_source_id, at, v) "
                        "VALUES ('s', 'plain', ?, 's', ?, '2026-10-01', 1)", (THEIRS, self.SECRET))
        self.db.commit()
        self.assertNotIn(self.SECRET, repr(search.search(self.db, "walrus")))
        self.assertIsNone(store.superseded_by(self.db, "s", "plain"))

    PROFILE_ID = "m1:secret-project:20261028_101500_a1b2c3"  # a Hermes session in a profile named so
    PROFILE_META = {"host": "m1", "profile": "secret-project"}

    def test_a_mark_to_an_item_that_is_gone_for_now_stays_through_remask(self):
        store.store_item(self.db, "hermes", "m1:old", iso(), "old", "walrus", {"host": "m1"})
        store.store_item(self.db, "hermes", self.PROFILE_ID, iso(), "new", "replacement", self.PROFILE_META)
        store.mark_superseded(self.db, "hermes", "m1:old", ("hermes", self.PROFILE_ID), MINE)
        self.db.commit()
        store.delete_item(self.db, "hermes", self.PROFILE_ID)
        store.remask(self.db)
        self.assertEqual(store.superseded_by(self.db, "hermes", "m1:old"), ("hermes", self.PROFILE_ID),
                         "a mark to a deleted item stays, as main's does")
        store.store_item(self.db, "hermes", self.PROFILE_ID, iso(), "new", "replacement", self.PROFILE_META)
        self.assertEqual(search.search(self.db, "walrus")[0]["superseded_by"], ("hermes", self.PROFILE_ID))

    def test_a_mark_that_arrives_before_its_item_is_kept(self):
        # Sync can deliver a mark before the items it names, as a batch with marks after items does not.
        store.mark_superseded(self.db, "hermes", "m1:old", ("hermes", self.PROFILE_ID), THEIRS, "2026-10-01", v=1)
        store.store_item(self.db, "hermes", "m1:old", iso(), "old", "walrus", {"host": "m1"})
        store.store_item(self.db, "hermes", self.PROFILE_ID, iso(), "new", "replacement", self.PROFILE_META)
        self.assertEqual(store.superseded_by(self.db, "hermes", "m1:old"), ("hermes", self.PROFILE_ID))

    def test_marks_to_one_long_item_cost_remask_and_search_time_in_proportion(self):
        import json
        import time
        took = {}
        for n in (200, 800):
            db = store.connect(os.path.join(self.tmp.name, f"many-{n}.sqlite"))
            self.addCleanup(db.close)
            db.executemany("INSERT INTO docs(kind, source, source_id, ts, title, content, metadata, masked) "
                           "VALUES (?,?,?,?,?,?,?,?)",
                           [("doc", "s", f"old-{i}", iso(), "old", "walrus notes", "{}", "older-mask") for i in range(n)]
                           + [("raw", "s", "latest", iso(), "latest", "ordinary facts\n" * 40,
                               json.dumps({"chunk": i, "chunks": n}), store.VERSION) for i in range(n)])
            db.executemany("INSERT INTO supersedes VALUES ('s', ?, ?, 's', 'latest', '2026-10-01', NULL)",
                           [(f"old-{i}", MINE) for i in range(n)])
            db.commit()
            started = time.process_time()
            store.remask(db)
            search.search(db, "walrus", limit=n)
            took[n] = time.process_time() - started
            self.assertEqual(db.execute("SELECT count(*) FROM supersedes").fetchone()[0], n)
        self.assertLess(took[800], 8 * max(took[200], 0.02))

    def main_store(self):
        """A store main wrote: each mark is the replacing row's id in docs.superseded_by, and there is no supersedes
        table."""
        self.add("old", "gecko first", title="old")
        self.add("new", "gecko second", title="new")
        self.db.execute("DROP TABLE supersedes")
        self.db.execute("ALTER TABLE docs ADD COLUMN superseded_by INTEGER")
        self.db.execute("UPDATE docs SET superseded_by=(SELECT id FROM docs WHERE source_id='new') WHERE source_id='old'")
        self.db.commit()
        self.db.close()

    def test_a_mark_main_kept_to_a_row_since_gone_still_marks_its_item(self):
        # Main points a mark at the replacing row's id, which an ingest that refreshes that item deletes.
        self.main_store()
        raw = sqlite3.connect(self.path)
        target = raw.execute("SELECT superseded_by FROM docs WHERE source_id='old'").fetchone()[0]
        raw.execute("DELETE FROM docs WHERE source_id='new'")
        raw.commit()
        raw.close()
        for readonly in (True, False, False):
            with self.subTest(readonly=readonly), contextlib.closing(store.connect(self.path, readonly=readonly)) as db:
                self.assertEqual(store.superseded_by(db, "s", "old"), ("", f"#{target}"))
                self.assertTrue(search.search(db, "gecko")[0]["superseded_by"])

    def test_a_store_main_wrote_keeps_its_marks_when_read_and_once_written(self):
        self.main_store()
        with contextlib.closing(store.connect(self.path, readonly=True)) as db:
            self.assertEqual(store.superseded_by(db, "s", "old"), ("s", "new"), "read before any write")
            hits = {h["title"]: h["superseded_by"] for h in search.search(db, "gecko")}
            self.assertEqual(hits, {"old": ("s", "new"), "new": None})
        with contextlib.closing(store.connect(self.path)) as db:
            self.assertEqual(store.superseded_by(db, "s", "old"), ("s", "new"), "the first write moves the mark")
            self.assertEqual(db.execute("SELECT count(*) FROM docs WHERE superseded_by IS NOT NULL").fetchone()[0], 0)
            store.clear_superseded(db, "s", "old", MINE)
            db.commit()
            self.assertIsNone(store.superseded_by(db, "s", "old"), "a clear made since outranks the moved mark")
        with contextlib.closing(store.connect(self.path)) as db:
            self.assertIsNone(store.superseded_by(db, "s", "old"), "and the mark is moved once")


class MarkMoveFailureTest(SupersedeMarkTest):
    def test_a_move_that_fails_partway_leaves_the_store_as_it_was_and_a_second_move_does_nothing(self):
        self.main_store()
        raw = sqlite3.connect(self.path)
        # A failure after the table is made and the marks are written, as the column is emptied.
        raw.execute("CREATE TRIGGER fail_move BEFORE UPDATE OF superseded_by ON docs "
                    "BEGIN SELECT RAISE(ABORT, 'disk full'); END")
        raw.commit()
        before = list(raw.iterdump())
        with self.assertRaises(sqlite3.Error):
            store.connect(self.path)
        self.assertEqual(list(raw.iterdump()), before, "the store is as it was")
        with contextlib.closing(store.connect(self.path, readonly=True)) as db:
            self.assertEqual(store.superseded_by(db, "s", "old"), ("s", "new"), "and still reads its marks")
        raw.execute("DROP TRIGGER fail_move")
        raw.commit()
        store.connect(self.path).close()
        moved = list(raw.iterdump())
        self.assertNotEqual(moved, before)
        store.connect(self.path).close()
        self.assertEqual(list(raw.iterdump()), moved, "a second move does nothing")
        with contextlib.closing(store.connect(self.path, readonly=True)) as db:
            self.assertEqual(store.superseded_by(db, "s", "old"), ("s", "new"))
        raw.close()


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
