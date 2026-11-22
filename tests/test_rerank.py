import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from kb import ai, cli, config, rerank, search, store
from tests.support import FakeModel, unused_model


def iso(days=0):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


ON = config.Config(ai=config.AISettings("fake", None), search=config.SearchSettings(rerank=True))
WIKI = config.Config(ai=config.AISettings("fake", None), search=config.SearchSettings(rerank=True, wiki_source="wiki"))
QUERY = "walrus price"


def answer(order, confident=True, wiki_docs=(), retry_query=""):
    return "thinking first\n" + json.dumps({"order": list(order), "confident": confident, "wiki_docs": list(wiki_docs),
                                             "retry_query": retry_query})


def ids(hits):
    return [h["id"] for h in hits]


class RerankTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.db = store.connect(os.path.join(tmp.name, "data", "superhistory.sqlite"))
        self.addCleanup(self.db.close)
        types = mock.patch.object(search, "_TYPES", {})
        types.start()
        self.addCleanup(types.stop)
        for n in range(4):
            self.add("chat", f"c{n}", f"walrus price walrus price chatter number {n}", days=n)

    def add(self, source, sid, content, title="t", days=0):
        store.store_item(self.db, source, sid, iso(days), title, content, {"path": f"/x/{sid}"})
        self.db.commit()
        return self.db.execute("SELECT id FROM docs WHERE source=? AND source_id=? ORDER BY id LIMIT 1",
                               (source, sid)).fetchone()[0]

    def keyword(self, query=QUERY, limit=10, sources=()):
        return ids(search.search(self.db, query, None, sources, limit))

    def test_rerank_is_off_by_default(self):
        self.assertFalse(config.parse("").search.rerank)
        self.assertEqual(config.SearchSettings(), config.Config().search)
        hits = rerank.rerank(self.db, config.Config(), QUERY, model=unused_model())
        self.assertEqual(ids(hits), self.keyword())

    def test_without_an_ai_table_it_searches_by_keyword(self):
        cfg = config.Config(search=config.SearchSettings(rerank=True))
        with mock.patch.object(ai, "load", side_effect=AssertionError("no model")):
            self.assertEqual(ids(rerank.rerank(self.db, cfg, QUERY)), self.keyword())

    def test_one_word_or_a_limit_of_one_asks_no_model(self):
        self.assertEqual(ids(rerank.rerank(self.db, ON, "walrus", model=unused_model())), self.keyword("walrus"))
        self.assertEqual(ids(rerank.rerank(self.db, ON, QUERY, limit=1, model=unused_model())), self.keyword(limit=1))

    def test_a_confident_first_pass_orders_the_hits_in_one_call(self):
        kw = self.keyword()
        model = FakeModel(answer([3, 1, "2", 3, 99, True]))
        hits = rerank.rerank(self.db, ON, QUERY, model=model)
        self.assertEqual(ids(hits), [kw[2], kw[0], kw[1], kw[3]])
        self.assertEqual(len(model.prompts), 1)
        prompt = model.prompts[0]
        self.assertEqual(prompt.system, rerank.PASS1_SYS)
        self.assertEqual(prompt.max_tokens, 2000)
        self.assertIn(datetime.now(timezone.utc).strftime("Today: %Y-%m-%d"), prompt.user)
        self.assertIn("4. [", prompt.user)
        self.assertLessEqual(model.timeouts[0], ON.search.rerank_timeout)
        for key in ("id", "kind", "source", "source_id", "ts", "title", "content", "snippet", "excerpt", "meta",
                    "superseded_by", "replaced_by", "newer"):
            self.assertIn(key, hits[0])

    def test_the_limit_cuts_the_reordered_list(self):
        kw = self.keyword()
        hits = rerank.rerank(self.db, ON, QUERY, limit=2, model=FakeModel(answer([4, 3])))
        self.assertEqual(ids(hits), [kw[3], kw[2]])

    def test_a_failed_call_leaves_the_keyword_order(self):
        kw = self.keyword()
        for reply in (TimeoutError("late"), ai.Busy("rate limited"), ai.NotReady("signed out"), "no json here",
                      '{"order": "first"}', RuntimeError("anything")):
            with self.subTest(reply=reply):
                self.assertEqual(ids(rerank.rerank(self.db, ON, QUERY, model=FakeModel(reply))), kw)

    def test_a_model_that_cannot_load_leaves_the_keyword_order(self):
        with mock.patch.object(ai, "load", side_effect=ai.NotReady("no key")):
            self.assertEqual(ids(rerank.rerank(self.db, ON, QUERY)), self.keyword())

    def test_a_slow_model_is_cut_off_at_the_timeout(self):
        cfg = config.Config(ai=ON.ai, search=config.SearchSettings(rerank=True, rerank_timeout=1))
        event = threading.Event()
        self.addCleanup(event.set)
        model = FakeModel(lambda prompt: event.wait(5) and answer([2, 1]))
        self.assertEqual(ids(rerank.rerank(self.db, cfg, QUERY, model=model)), self.keyword())

    def test_no_match_and_no_wiki_is_no_hits_and_no_call(self):
        self.assertEqual(rerank.rerank(self.db, ON, "pangolin zebra", model=unused_model()), [])

    def test_a_doubtful_first_pass_opens_a_wiki_page_and_searches_again(self):
        page = self.add("wiki", "pricing.md", "# Pricing\n\n## Plans\nThe walrus plan costs $40 a month.",
                        title="Pricing", days=30)
        tusk = self.add("mail", "m1", "tusk subscription invoice for the herd, current rate", days=1)
        kw = ids(rerank.rerank(self.db, WIKI, QUERY, model=FakeModel(TimeoutError())))
        replies = [answer([2, 1], confident=False, wiki_docs=[f"doc:{page}", "doc:99999"],
                          retry_query="tusk subscription"), None]

        def reply(prompt):
            if prompt.system == rerank.PASS1_SYS:
                return replies[0]
            listed = [line for line in prompt.user.splitlines() if line[:1].isdigit()]
            replies[1] = listed
            pricing = next(i for i, line in enumerate(listed, 1) if "Pricing" in line)
            tusks = next(i for i, line in enumerate(listed, 1) if "mail" in line)
            return json.dumps({"order": [pricing, tusks]})

        model = FakeModel(reply)
        hits = rerank.rerank(self.db, WIKI, QUERY, model=model)
        self.assertEqual(len(model.prompts), 2)
        self.assertEqual(model.prompts[1].system, rerank.PASS2_SYS)
        self.assertEqual(model.prompts[1].max_tokens, 2000)
        self.assertEqual(ids(hits)[:2], [page, tusk])
        # Pass 1's hits keep their places after what Pass 2 picked: its first two, then the rest.
        self.assertEqual(ids(hits)[2:4], [kw[1], kw[0]])
        self.assertIn(f"doc:{page} [", model.prompts[0].user)
        self.assertIn("pricing.md | Pricing | Pricing / Plans", model.prompts[0].user)
        self.assertIn("(Living Wiki)", model.prompts[1].user)
        wiki_hit = hits[0]
        for key in ("id", "kind", "source", "source_id", "ts", "title", "content", "snippet", "excerpt", "meta",
                    "superseded_by", "replaced_by", "newer"):
            self.assertIn(key, wiki_hit)
        self.assertIn("$40", wiki_hit["excerpt"])

    def test_pass_two_keeps_pass_ones_best_ahead_of_what_it_adds(self):
        for n in range(30):
            self.add("chat", f"more{n}", f"walrus price talk {n}", days=5 + n)
        for n in range(20):
            self.add("mail", f"tusk{n}", f"tusk subscription {n}", days=n)
        cfg = config.Config(ai=ON.ai, search=config.SearchSettings(rerank=True, rerank_pool=40))

        def reply(prompt):
            if prompt.system == rerank.PASS1_SYS:
                return answer([], confident=False, retry_query="tusk subscription")
            reply.pass2 = prompt.user
            return '{"order": []}'

        hits = rerank.rerank(self.db, cfg, QUERY, limit=30, model=FakeModel(reply))
        kw = self.keyword(limit=40)
        self.assertEqual(ids(hits)[:rerank.KEEP_FIRST], kw[:rerank.KEEP_FIRST])
        listed = [line for line in reply.pass2.splitlines() if line[:1].isdigit()]
        self.assertLessEqual(len(listed), rerank.POOL_CAP)
        self.assertTrue(any(" mail " in line for line in listed))

    def test_a_confident_or_retry_off_first_pass_asks_once(self):
        page = self.add("wiki", "pricing.md", "# Pricing\nThe walrus plan costs $40.", title="Pricing")
        for cfg, confident in ((WIKI, True), (config.Config(ai=ON.ai, search=config.SearchSettings(
                rerank=True, smart_retry=False, wiki_source="wiki")), False)):
            with self.subTest(smart_retry=cfg.search.smart_retry):
                model = FakeModel(answer([1], confident=confident, wiki_docs=[f"doc:{page}"], retry_query="x y"))
                rerank.rerank(self.db, cfg, QUERY, model=model)
                self.assertEqual(len(model.prompts), 1)

    def test_a_failed_second_pass_keeps_the_first_passes_order(self):
        kw = self.keyword()

        def reply(prompt):
            if prompt.system == rerank.PASS1_SYS:
                return answer([4, 3, 2, 1], confident=False, retry_query="walrus chatter")
            raise ai.Busy("rate limited")

        self.assertEqual(ids(rerank.rerank(self.db, ON, QUERY, model=FakeModel(reply))), kw[::-1])

    def test_the_wiki_index_and_its_keyword_hits_reach_the_first_pass(self):
        for n in range(15):
            self.add("chat", f"loud{n}", "walrus price walrus price walrus price " * 3, days=n)
        page = self.add("wiki", "deep/pricing.md", "# Walrus\nprice list lives here", title="Prices", days=400)
        other = self.add("wiki", "unrelated.md", "# Zebra\n## Stripes\n### Count\n#### Four\n##### Five\nnothing",
                         title="Zebra")
        model = FakeModel(answer([1]))
        hits = rerank.rerank(self.db, WIKI, QUERY, limit=3, model=model)
        user = model.prompts[0].user
        self.assertIn(f"- doc:{other} [", user)
        self.assertIn("unrelated.md | Zebra | Zebra / Stripes / Count / Four", user)
        self.assertNotIn("Five", user)
        records = user.split("Living Wiki index:")[0]
        self.assertIn("Prices", records)
        self.assertIn("(Living Wiki)", records)
        # The wiki's hit comes after the first `limit` keyword hits, ahead of the rest.
        listed = [line for line in records.splitlines() if line[:1].isdigit()]
        self.assertTrue(any("Prices" in line for line in listed[3:3 + rerank.WIKI_HITS]))
        self.assertEqual(len(hits), 3)
        self.assertNotIn(page, self.keyword(limit=3))

    def test_a_search_of_other_sources_leaves_the_wiki_out(self):
        self.add("wiki", "pricing.md", "# Pricing\nwalrus price", title="Pricing")
        model = FakeModel(answer([1]))
        rerank.rerank(self.db, WIKI, QUERY, sources=("chat",), model=model)
        self.assertNotIn("Living Wiki", model.prompts[0].user)

    def test_keep_applies_to_every_hit(self):
        page = self.add("wiki", "pricing.md", "# Pricing\nwalrus price", title="Pricing")
        keep = lambda h: h["source"] != "wiki"  # noqa: E731
        model = FakeModel(answer([1], confident=False, wiki_docs=[f"doc:{page}"]))
        hits = rerank.rerank(self.db, WIKI, QUERY, keep=keep, model=model)
        self.assertNotIn(page, ids(hits))
        self.assertNotIn("pricing.md", model.prompts[0].user)
        self.assertEqual(len(model.prompts), 1)  # the page is not in the index, so there is nothing to open


class SettingsTest(unittest.TestCase):
    def test_the_search_table_is_read_and_checked(self):
        c = config.parse('[search]\nrerank = true\nrerank_pool = 20\nrerank_timeout = 3\nsmart_retry = false\n'
                         'wiki_source = "wiki"\n[sources.wiki]\ntype = "notes"\npath = "/w"\n')
        self.assertEqual(c.search, config.SearchSettings(True, 20, 3, False, "wiki"))
        with self.assertRaises(config.ConfigError) as caught:
            config.parse('[search]\nrerank_pool = 4\nrerank_timeout = 0\nrerank = "yes"\nwiki_source = "wiki"\n')
        joined = "\n".join(caught.exception.problems)
        self.assertIn("search.rerank: expected true or false", joined)
        with self.assertRaises(config.ConfigError) as caught:
            config.parse('[search]\nrerank_pool = 4\n')
        self.assertIn("search.rerank_pool: must be at least 5", caught.exception.problems)
        with self.assertRaises(config.ConfigError) as caught:
            config.parse('[search]\nrerank_timeout = 0\n')
        self.assertIn("search.rerank_timeout: must be at least 1", caught.exception.problems)
        with self.assertRaises(config.ConfigError) as caught:
            config.parse('[search]\nwiki_source = "wiki"\n')
        self.assertIn("search.wiki_source: no [sources.wiki] table", caught.exception.problems)

    def test_a_wiki_source_turned_off_still_counts(self):
        c = config.parse('[search]\nwiki_source = "wiki"\n[sources.wiki]\ntype = "notes"\npath = "/w"\nenabled = false\n')
        self.assertEqual(c.search.wiki_source, "wiki")


class CommandTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        env = mock.patch.dict(os.environ, {"SUPERHISTORY_HOME": self.home})
        env.start()
        self.addCleanup(env.stop)
        db = store.connect(os.path.join(self.home, "data", "superhistory.sqlite"))
        for n in range(3):
            store.store_item(db, "s", f"a{n}", iso(n), f"Walrus {n}", f"walrus price number {n}", {})
        db.commit()
        db.close()
        with open(os.path.join(self.home, "config.toml"), "w", encoding="utf-8") as fh:
            fh.write('machine = "studio"\n[ai]\nplugin = "anthropic"\n[search]\nrerank = true\n')

    def run_kb(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        self.assertEqual(code, 0, err.getvalue())
        return [line.split("  ")[-1] for line in out.getvalue().splitlines() if line[:1].isdigit()]

    def test_search_uses_the_model_unless_told_not_to(self):
        model = FakeModel(answer([3, 2, 1]))
        with mock.patch.object(ai, "load", return_value=model):
            ordered = self.run_kb("search", "walrus price")
            plain = self.run_kb("search", "walrus price", "--no-rerank")
        self.assertEqual(len(model.prompts), 1)
        self.assertEqual(ordered, plain[::-1])

    def test_the_mcp_search_tool_reranks_too(self):
        from kb import mcp, paths
        model = FakeModel(answer([3, 2, 1]))
        cfg = config.load(os.path.join(self.home, "config.toml"))
        with mock.patch.object(ai, "load", return_value=model):
            text, failed = mcp.call(paths.Paths.under(self.home), cfg, "search", {"query": "walrus price"})
        self.assertFalse(failed, text)
        self.assertEqual(len(model.prompts), 1)
        self.assertLess(text.index("Walrus 2"), text.index("Walrus 0"))


if __name__ == "__main__":
    unittest.main()
