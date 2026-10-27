import contextlib
import dataclasses
import fcntl
import gc
import io
import json
import logging
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

from kb import ai, cli, config, ids, ingest, search, store
from kb.paths import Paths
from kb.sources import Gone, Item, Progress, SourceType

TS = "2025-01-01T10:00:00Z"
LOG = logging.getLogger("varve-test-ingest")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False


@dataclasses.dataclass(frozen=True)
class NoOptions:
    pass


def item(sid, content="body", mark=1.0, title=None, meta=None):
    return Item(sid, TS, title or f"title {sid}", content, {"path": f"/src/{sid}"} if meta is None else meta, mark)


class Script:
    def __init__(self, name="fake", events=(), live=None, summarize=False, needs_model=False, platforms=None):
        self.name = name
        self.events = list(events)
        self.live = live
        self.since_seen = []
        self.models_seen = []
        extra = {"platforms": platforms} if platforms else {}
        self.type = SourceType(NoOptions, self.extract, self.list_live if live is not None else None,
                               summarize=summarize, needs_model=needs_model, **extra)
        self.source = config.Source(name, "fake", self.type, NoOptions(), (), config.Summarize(summarize), ("m1",))

    def extract(self, run):
        self.since_seen.append(run.since)
        self.models_seen.append(run.model)
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event

    def list_live(self, run):
        if isinstance(self.live, Exception):
            raise self.live
        return list(self.live)


def make_config(*scripts, ai_plugin=False):
    text = 'machine = "m1"\n' + ('[ai]\nplugin = "anthropic"\n' if ai_plugin else "")
    return dataclasses.replace(config.parse(text), sources=tuple(s.source for s in scripts))


class FakeModel:
    name = "fake-model"
    model = "fake"
    traits = ai.Traits(check_budget=10.0, hedge_after=None, parallel=1, max_prompt_chars=600_000)

    def __init__(self):
        self.asked = 0

    def ask(self, prompt, timeout):
        self.asked += 1
        return json.dumps({"segments": [{"title": "Report", "question": "what shipped?", "summary": "It shipped."}],
                           "atoms": []})


class IngestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = store.connect(os.path.join(self.tmp.name, "kb.sqlite"))
        self.addCleanup(self.db.close)

    def run_unit(self, script, host="m1"):
        ingest.run_unit(self.db, script.source, host, ingest.LocalReader(), LOG, f"{host}:")

    def run_all(self, *scripts, **kwargs):
        plugin = kwargs.pop("ai_plugin", False)
        return ingest.run(self.db, make_config(*scripts, ai_plugin=plugin), LOG, **kwargs)

    def rows(self, source=None):
        sql = "SELECT kind, source, source_id, ts, title, content, metadata FROM docs"
        args = ()
        if source:
            sql, args = sql + " WHERE source=?", (source,)
        return self.db.execute(sql + " ORDER BY source, source_id, kind, id", args).fetchall()

    def ids(self, source):
        return sorted({r[2] for r in self.rows(source)})

    def mark(self, source, host="m1"):
        raw = store.get_watermark(self.db, source, host)
        return None if raw is None else json.loads(raw)


class RunUnitTest(IngestCase):
    def test_ids_are_prefixed_with_the_host_and_meta_carries_it(self):
        self.run_unit(Script(events=[item("x", "alpha text")]))
        rows = self.rows("fake")
        self.assertEqual([(r[0], r[2], r[4], r[5]) for r in rows], [("doc", "m1:x", "title x", "alpha text")])
        meta = json.loads(rows[0][6])
        self.assertEqual((meta["host"], meta["path"]), ("m1", "/src/x"))

    def test_the_watermark_is_the_largest_mark_and_feeds_the_next_run(self):
        script = Script(events=[item("a", mark=5.0), item("b", mark=9.0), item("c", mark=7.0)])
        self.run_unit(script)
        self.assertEqual(self.mark("fake"), 9.0)
        self.run_unit(script)
        self.assertEqual(script.since_seen, [None, 9.0])

    def test_the_watermark_survives_a_run_with_no_events(self):
        script = Script(events=[item("a", mark=5.0)])
        self.run_unit(script)
        script.events = []
        self.run_unit(script)
        self.assertEqual(self.mark("fake"), 5.0)

    def test_no_events_on_the_first_run_store_no_watermark(self):
        self.run_unit(Script())
        self.assertIsNone(self.mark("fake"))

    def test_each_host_has_its_own_watermark_and_its_own_ids(self):
        script = Script(events=[item("a", mark=5.0)])
        self.run_unit(script, "m1")
        self.run_unit(script, "m2")
        self.assertEqual(script.since_seen, [None, None])
        self.assertEqual(self.ids("fake"), ["m1:a", "m2:a"])
        self.assertEqual((self.mark("fake", "m1"), self.mark("fake", "m2"), self.mark("fake", "m3")), (5.0, 5.0, None))

    def test_a_finished_run_is_recorded_as_a_success_of_that_source_on_that_host(self):
        self.run_unit(Script(events=[item("a")]), "m2")
        self.assertEqual([unit for unit, _ in store.succeeded(self.db)], ["fake@m2"])

    def test_a_rerun_converges_to_the_same_rows(self):
        script = Script(events=[item("a", "one"), item("b", "two " * 3000), item("c", "three")])
        self.run_unit(script)
        first = self.rows()
        self.assertEqual({r[0] for r in first}, {"doc", "raw"})
        self.run_unit(script)
        self.assertEqual(self.rows(), first)

    def test_a_changed_item_is_rewritten_and_an_unchanged_one_keeps_its_row(self):
        script = Script(events=[item("a", "one"), item("b", "two")])
        self.run_unit(script)
        ids = dict(self.db.execute("SELECT source_id, id FROM docs"))
        script.events = [item("a", "one"), item("b", "two edited")]
        self.run_unit(script)
        self.assertEqual({r[2]: r[5] for r in self.rows("fake")}, {"m1:a": "one", "m1:b": "two edited"})
        self.assertEqual(self.db.execute("SELECT id FROM docs WHERE source_id='m1:a'").fetchone()[0], ids["m1:a"])

    def test_apply_reports_whether_rows_changed(self):
        self.assertTrue(ingest.apply(self.db, "fake", "m1:", item("a", "one"), "m1"))
        self.assertFalse(ingest.apply(self.db, "fake", "m1:", item("a", "one"), "m1"))
        self.assertTrue(ingest.apply(self.db, "fake", "m1:", item("a", "two"), "m1"))
        self.assertFalse(ingest.apply(self.db, "fake", "m1:", Progress(5.0), "m1"))
        self.assertTrue(ingest.apply(self.db, "fake", "m1:", Gone("a", 6.0), "m1"))
        self.assertEqual(self.rows(), [])

    def test_applying_gone_twice_changes_nothing_the_second_time(self):
        ingest.apply(self.db, "fake", "m1:", item("a", "one"), "m1")
        self.assertTrue(ingest.apply(self.db, "fake", "m1:", Gone("a", 6.0), "m1"))
        self.assertFalse(ingest.apply(self.db, "fake", "m1:", Gone("a", 6.0), "m1"))

    def test_gone_removes_every_row_of_the_item_and_moves_the_watermark(self):
        script = Script(events=[item("a", "x" * 9000 + "\n" + "y" * 9000), item("b", "keep")])
        self.run_unit(script)
        store.replace_rows(self.db, "fake", "m1:a", [{"kind": "atom", "ts": TS, "title": "t", "content": "c",
                                                      "metadata": {}}], kinds=("atom",))
        script.events = [Gone("a", 20.0)]
        self.run_unit(script)
        self.assertEqual(self.ids("fake"), ["m1:b"])
        self.assertEqual(self.mark("fake"), 20.0)

    def test_progress_moves_the_watermark_without_touching_rows(self):
        script = Script(events=[item("a", mark=5.0)])
        self.run_unit(script)
        before = self.rows()
        script.events = [Progress(42.0)]
        self.run_unit(script)
        self.assertEqual(self.mark("fake"), 42.0)
        self.assertEqual(self.rows(), before)

    def test_marks_below_a_stale_watermark_replace_it(self):
        script = Script(events=[item("a", mark=3.0)])
        store.set_watermark(self.db, "fake", "m1", json.dumps(99.0))
        store.set_watermark(self.db, "fake", "m1:settings", ingest.settings_digest(script.source))
        self.run_unit(script)
        self.assertEqual(script.since_seen, [99.0])
        self.assertEqual(self.mark("fake"), 3.0)

    def test_secrets_are_masked_on_the_way_in(self):
        token = "ghp_" + "Ab1Cd2Ef3G" * 4
        self.run_unit(Script(events=[item("a", f"token {token} end", title=f"key {token}")]))
        row = self.rows("fake")[0]
        self.assertNotIn("ghp_", row[4] + row[5])
        self.assertIn("[masked]", row[5])

    def test_a_large_run_is_stored_complete_in_commit_sized_batches(self):
        n = ingest.COMMIT_EVERY * 2 + 17
        script = Script(events=[item(f"i{i}", f"body {i}", mark=float(i)) for i in range(n)])
        self.run_unit(script)
        self.assertEqual(len(self.ids("fake")), n)
        self.assertEqual(self.mark("fake"), float(n - 1))

    def test_a_lone_surrogate_is_stored_not_fatal(self):
        self.run_unit(Script(events=[item("s", content="bad \ud800 text")]))
        self.assertEqual(self.ids("fake"), ["m1:s"])
        self.assertIn("text", self.rows("fake")[0][5])


class PruneTest(IngestCase):
    def test_live_ids_prunes_removed_items_with_their_summaries(self):
        script = Script(events=[item("a"), item("b"), item("c")], live=["a", "b", "c"])
        self.run_unit(script)
        store.replace_rows(self.db, "fake", "m1:b", [{"kind": "segment", "ts": TS, "title": "s", "content": "c",
                                                      "metadata": {}}], kinds=("segment",))
        self.db.commit()
        script.live, script.events = ["a", "c"], []
        self.run_unit(script)
        self.assertEqual(self.ids("fake"), ["m1:a", "m1:c"])

    def test_pruning_leaves_other_hosts_and_other_sources_alone(self):
        script = Script(events=[item("a")], live=["a"])
        self.run_unit(script)
        store.store_item(self.db, "fake", "m2:z", TS, "other host", "other host body", {})
        other = Script("other", events=[item("a")], live=["a"])
        self.run_unit(other)
        self.db.commit()
        script.live, script.events = ["new"], []
        self.run_unit(script)
        self.assertEqual(self.ids("fake"), ["m2:z"])
        self.assertEqual(self.ids("other"), ["m1:a"])

    def test_pruning_one_host_leaves_a_host_whose_name_differs_by_a_wildcard_character(self):
        store.store_item(self.db, "fake", "acb:z", TS, "other host", "other host body", {})
        store.store_item(self.db, "fake", "a%b:z", TS, "other host", "other host body", {})
        self.db.commit()
        self.run_unit(Script(events=[item("a")], live=["a"]), host="a_b")
        self.assertEqual(self.ids("fake"), ["a%b:z", "a_b:a", "acb:z"])

    def test_an_empty_listing_prunes_nothing(self):
        script = Script(events=[item("a"), item("b")], live=["a", "b"])
        self.run_unit(script)
        script.live, script.events = [], []
        self.run_unit(script)
        self.assertEqual(self.ids("fake"), ["m1:a", "m1:b"])

    def test_a_type_without_a_listing_never_prunes(self):
        script = Script(events=[item("a"), item("b")])
        self.run_unit(script)
        script.events = []
        self.run_unit(script)
        self.assertEqual(self.ids("fake"), ["m1:a", "m1:b"])

    def test_pruning_then_rerunning_is_stable(self):
        script = Script(events=[item("a"), item("b")], live=["a", "b"])
        self.run_unit(script)
        script.live, script.events = ["a"], []
        self.run_unit(script)
        after = self.rows()
        self.run_unit(script)
        self.assertEqual(self.rows(), after)
        self.assertEqual(self.ids("fake"), ["m1:a"])

    def test_a_listing_that_fails_keeps_what_was_read_and_prunes_nothing(self):
        script = Script(events=[item("a"), item("b")], live=["a", "b"])
        self.run_unit(script)
        script.events = [item("c")]
        script.live = OSError("folder unreadable")
        failures = self.run_all(script)
        self.assertEqual(failures, 1)
        self.assertEqual(self.ids("fake"), ["m1:a", "m1:b", "m1:c"])
        self.assertEqual([unit for unit, _ in store.failing(self.db)], ["fake@m1"])


class RunTest(IngestCase):
    def test_one_failing_source_does_not_stop_the_others(self):
        bad = Script("bad", events=[item("b1"), RuntimeError("boom")])
        good = Script("good", events=[item("g1", mark=3.0), item("g2", mark=4.0)])
        good2 = Script("good2", events=[item("h1", mark=8.0)])
        self.assertEqual(self.run_all(bad, good, good2), 1)
        self.assertEqual(self.ids("good"), ["m1:g1", "m1:g2"])
        self.assertEqual(self.ids("good2"), ["m1:h1"])
        self.assertEqual((self.mark("good"), self.mark("good2")), (4.0, 8.0))

    def test_a_failure_is_reported_until_the_next_success(self):
        script = Script("flaky", events=[RuntimeError("chat db unreadable: grant access")])
        cfg = make_config(script)
        ingest.run(self.db, cfg, LOG)
        self.assertIn("Not refreshing: flaky@m1", cli.header(self.db, cfg))
        self.assertEqual([unit for unit, _ in store.failing(self.db)], ["flaky@m1"])
        self.assertIn("grant access", store.failing(self.db)[0][1])
        script.events = [item("f1")]
        self.assertEqual(ingest.run(self.db, cfg, LOG), 0)
        self.assertNotIn("Not refreshing", cli.header(self.db, cfg))
        self.assertEqual(store.failing(self.db), [])
        self.assertEqual([unit for unit, _ in store.succeeded(self.db)], ["flaky@m1"])

    def test_a_failed_source_stores_no_watermark_and_no_partial_rows(self):
        script = Script("bad", events=[item("b1", mark=2.0), RuntimeError("boom")])
        self.run_all(script)
        self.assertEqual(self.ids("bad"), [])
        self.assertIsNone(self.mark("bad"))
        self.assertEqual(store.succeeded(self.db), [])

    def test_a_failure_after_a_commit_batch_converges_on_the_next_run(self):
        n = ingest.COMMIT_EVERY + 50
        events = [item(f"i{i}", f"body {i}", mark=float(i)) for i in range(n)]
        script = Script("flaky", events=events[:ingest.COMMIT_EVERY + 10] + [RuntimeError("boom")])
        self.assertEqual(self.run_all(script), 1)
        self.assertIsNone(self.mark("flaky"))
        script.events = events
        self.assertEqual(self.run_all(script), 0)
        self.assertEqual(len(self.ids("flaky")), n)
        self.assertEqual(self.db.execute("SELECT count(*) FROM docs WHERE source='flaky'").fetchone()[0], n)

    def test_one_bad_item_is_skipped_and_the_watermark_moves_on(self):
        bad = item("x", meta={"unstorable": object()}, mark=2.0)
        script = Script("mixed", events=[item("a", mark=1.0), bad, item("c", mark=3.0)])
        self.assertEqual(self.run_all(script), 0)
        self.assertEqual(self.ids("mixed"), ["m1:a", "m1:c"])
        self.assertEqual(self.mark("mixed"), 3.0)

    def test_a_rerun_through_run_converges_and_reads_incrementally(self):
        script = Script("good", events=[item("a", "one", mark=5.0), item("b", "two " * 3000, mark=6.0)],
                        live=["a", "b"])
        self.run_all(script)
        first = self.rows()
        script.events = []
        self.run_all(script)
        self.assertEqual(self.rows(), first)
        self.assertEqual(script.since_seen, [None, 6.0])

    def test_pruning_through_run(self):
        script = Script("good", events=[item("a"), item("b")], live=["a", "b"])
        self.run_all(script)
        script.events, script.live = [], ["a"]
        self.run_all(script)
        self.assertEqual(self.ids("good"), ["m1:a"])

    def test_the_time_of_the_last_ingest_is_recorded_even_when_a_source_fails(self):
        self.assertIsNone(store.last_ingest(self.db))
        self.assertEqual(self.run_all(Script("good", events=[item("g1")])), 0)
        first = store.last_ingest(self.db)
        self.assertRegex(first, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(self.run_all(Script("bad", events=[RuntimeError("boom")])), 1)
        self.assertGreaterEqual(store.last_ingest(self.db), first)

    def test_only_reads_the_named_sources(self):
        one, two = Script("one", events=[item("o")]), Script("two", events=[item("t")])
        self.run_all(one, two, only=("two",))
        self.assertEqual(self.ids("one"), [])
        self.assertEqual(self.ids("two"), ["m1:t"])
        self.assertEqual(one.since_seen, [])

    def test_a_source_that_does_not_run_on_this_system_is_a_failure_not_a_crash(self):
        elsewhere = Script("elsewhere", events=[item("e")], platforms=frozenset({"plan9"}))
        good = Script("good", events=[item("g")])
        self.assertEqual(self.run_all(elsewhere, good), 1)
        self.assertEqual(self.ids("elsewhere"), [])
        self.assertEqual(self.ids("good"), ["m1:g"])
        self.assertEqual([unit for unit, _ in store.failing(self.db)], ["elsewhere@m1"])

    def test_a_model_is_built_only_for_sources_that_need_one(self):
        model = FakeModel()
        built = []

        def factory():
            built.append(1)
            return model

        plain = Script("plain", events=[item("p")])
        smart_a = Script("smart-a", events=[item("a")], needs_model=True)
        smart_b = Script("smart-b", events=[item("b")], needs_model=True)
        self.run_all(plain, smart_a, smart_b, model_factory=factory)
        self.assertEqual((plain.models_seen, smart_a.models_seen, smart_b.models_seen), ([None], [model], [model]))
        self.assertEqual(len(built), 1)


class SummaryTest(IngestCase):
    def long_source(self):
        return Script("notes", events=[item("long", "word " * 3000), item("short", "tiny")], summarize=True)

    def test_new_long_items_are_summarized_after_reading(self):
        model = FakeModel()
        self.assertEqual(self.run_all(self.long_source(), ai_plugin=True, model_factory=lambda: model), 0)
        kinds = {(r[0], r[2]) for r in self.rows("notes")}
        self.assertIn(("segment", "m1:long"), kinds)
        self.assertNotIn(("segment", "m1:short"), kinds)
        self.assertEqual(model.asked, 1)
        self.assertEqual([unit for unit, _ in store.succeeded(self.db)], ["notes@m1", "summarize"])

    def test_with_summaries_off_reads_without_calling_the_model(self):
        model = FakeModel()
        self.assertEqual(self.run_all(self.long_source(), ai_plugin=True, model_factory=lambda: model,
                                      with_summaries=False), 0)
        self.assertEqual(model.asked, 0)
        self.assertNotIn("segment", {r[0] for r in self.rows("notes")})
        self.assertEqual(self.ids("notes"), ["m1:long", "m1:short"])

    def test_no_summaries_without_an_ai_plugin(self):
        built = []
        self.assertEqual(self.run_all(self.long_source(), model_factory=lambda: built.append(1)), 0)
        self.assertEqual(built, [])
        self.assertNotIn("segment", {r[0] for r in self.rows("notes")})

    def test_a_summarize_crash_is_reported_until_a_run_summarizes(self):
        script = Script("notes", events=[item("g1")])
        cfg = make_config(script, ai_plugin=True)

        def refused():
            raise RuntimeError("cannot reach the model: Connection refused")

        self.assertEqual(ingest.run(self.db, cfg, LOG, model_factory=refused), 1)
        self.assertIn("Not refreshing: summarize", cli.header(self.db, cfg))
        self.assertIn("Connection refused", dict(store.failing(self.db))["summarize"])
        self.assertEqual(self.ids("notes"), ["m1:g1"])
        self.assertEqual(ingest.run(self.db, cfg, LOG, model_factory=FakeModel), 0)
        self.assertNotIn("Not refreshing", cli.header(self.db, cfg))
        self.assertEqual(store.failing(self.db), [])


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "kb-home")
        self.paths = Paths.under(self.home)
        env = mock.patch.dict(os.environ, {"KB_HOME": self.home})
        env.start()
        self.addCleanup(env.stop)
        logger = logging.getLogger("kb")
        saved = logger.handlers[:]
        logger.handlers = []

        def restore():
            for handler in logger.handlers:
                handler.close()
            logger.handlers = saved

        self.addCleanup(restore)

    def configure(self, notes_path, extra=""):
        os.makedirs(self.home, exist_ok=True)
        with open(self.paths.config_file, "w") as fh:
            fh.write(f'machine = "m1"\n[sources.notes]\npath = {json.dumps(notes_path)}\n{extra}')

    def make_notes(self):
        notes = os.path.join(self.tmp.name, "Notes")
        os.makedirs(os.path.join(notes, "Area"))
        with open(os.path.join(notes, "Area", "plan.md"), "w") as fh:
            fh.write("walrus pricing")
        return notes

    def ingest(self):
        return self.run_cli("ingest")[0]

    def run_cli(self, *argv):
        out, self.err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(self.err):
                return cli.main(list(argv)), out.getvalue()
        finally:
            gc.collect()

    def health(self):
        db = store.connect(self.paths.db)
        try:
            return store.failing(db), [unit for unit, _ in store.succeeded(db)]
        finally:
            db.close()

    def stored(self):
        db = store.connect(self.paths.db)
        try:
            return sorted(r[0] for r in db.execute("SELECT source_id FROM docs"))
        finally:
            db.close()

    def test_ingest_reads_the_configured_sources(self):
        self.configure(self.make_notes())
        self.assertEqual(self.ingest(), 0)
        self.assertEqual(self.stored(), [f"{ids.install_id(self.paths)}:Area/plan.md"])

    def test_a_second_ingest_exits_cleanly_while_the_lock_is_held(self):
        self.configure(self.make_notes())
        os.makedirs(os.path.dirname(self.paths.lock), exist_ok=True)
        with open(self.paths.lock, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual(self.ingest(), 0)
        self.assertFalse(os.path.exists(self.paths.db))
        self.assertEqual(self.ingest(), 0)
        self.assertEqual(self.stored(), [f"{ids.install_id(self.paths)}:Area/plan.md"])

    def test_ingest_exits_1_when_a_source_fails(self):
        self.configure(os.path.join(self.tmp.name, "missing folder"))
        self.assertEqual(self.ingest(), 1)
        db = store.connect(self.paths.db)
        self.addCleanup(db.close)
        self.assertEqual([unit for unit, _ in store.failing(db)], ["notes@m1"])

    def test_sync_runs_after_ingest_only_when_the_config_turns_it_on(self):
        notes = self.make_notes()
        calls = []
        client = types.ModuleType("kb.sync")
        client.after_ingest = lambda paths, cfg, db, log: calls.append(cfg.install)
        with mock.patch.dict(sys.modules, {"kb.sync": client}):
            self.configure(notes)
            self.assertEqual(self.ingest(), 0)
            self.assertEqual(calls, [])
            self.configure(notes, '[sync]\nrelay = "https://relay.example"\n')
            self.assertEqual(self.ingest(), 0)
        self.assertEqual(calls, [ids.install_id(self.paths)])
        self.assertIn("sync", self.health()[1])

    def test_a_sync_failure_fails_the_run_and_its_recorded_error_is_masked(self):
        client = types.ModuleType("kb.sync")

        def refuse(*args):
            raise RuntimeError("relay refused sk-ant-api03-" + "x" * 40)

        client.after_ingest = refuse
        self.configure(self.make_notes(), "[sync]\n")
        with mock.patch.dict(sys.modules, {"kb.sync": client}):
            self.assertEqual(self.ingest(), 1)
        [(unit, line)] = self.health()[0]
        self.assertEqual(unit, "sync")
        self.assertIn("relay refused [masked]", line)
        with open(self.paths.log("ingest")) as fh:
            logged = fh.read()
        self.assertIn("relay refused [masked]", logged, "the traceback is logged, masked")
        for where in (logged, self.err.getvalue()):
            self.assertNotIn("x" * 40, where)
        self.assertEqual(self.stored(), [f"{ids.install_id(self.paths)}:Area/plan.md"], "the items still landed")

    def test_a_computer_with_only_sync_configured_still_runs_it(self):
        os.makedirs(self.home, exist_ok=True)
        with open(self.paths.config_file, "w") as fh:
            fh.write('machine = "m1"\n[sync]\n')
        calls = []
        client = types.ModuleType("kb.sync")
        client.after_ingest = lambda paths, cfg, db, log: calls.append(cfg.install)
        with mock.patch.dict(sys.modules, {"kb.sync": client}):
            self.assertEqual(self.ingest(), 0)
        self.assertEqual(calls, [ids.install_id(self.paths)], "the Gone records of a forgotten reader still go out")

    def test_sync_turned_on_without_its_client_is_reported_not_skipped(self):
        self.configure(self.make_notes(), "[sync]\n")
        with mock.patch.dict(sys.modules, {"kb.sync": None}):
            self.assertEqual(self.ingest(), 1)
        self.assertEqual([unit for unit, _ in self.health()[0]], ["sync"])

    def test_forget_deletes_this_computers_items_of_one_source_and_its_watermarks(self):
        self.configure(self.make_notes())
        self.assertEqual(self.ingest(), 0)
        mine = ids.install_id(self.paths)
        db = store.connect(self.paths.db)
        store.store_item(db, "notes", "c-bbbbbbbbbbbbb:Area/plan.md", "2026-10-01T00:00:00Z", "theirs", "walrus", {})
        store.store_item(db, "other", f"{mine}:x", "2026-10-01T00:00:00Z", "kept", "walrus", {})
        store.mark_superseded(db, "notes", f"{mine}:Area/plan.md", ("other", f"{mine}:x"), mine)
        db.commit()
        db.close()
        code, out = self.run_cli("forget", "notes", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("is still in", out)
        self.assertIn("Deleted 1 item of notes", out)
        self.assertEqual(self.stored(), sorted(["c-bbbbbbbbbbbbb:Area/plan.md", f"{mine}:x"]))
        db = store.connect(self.paths.db)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("SELECT count(*) FROM ingest_state WHERE source='notes' OR key LIKE '%notes@%'")
                         .fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT count(*) FROM supersedes").fetchone()[0], 1, "a mark stays, as on any delete")

    def test_the_summarize_estimate_counts_this_computers_items(self):
        notes = self.make_notes()
        with open(os.path.join(notes, "Area", "long.md"), "w") as fh:
            fh.write("walrus pricing line\n" * 600)
        os.utime(os.path.join(notes, "Area", "long.md"), (time.time() - 86400,) * 2)
        self.configure(notes)
        with open(self.paths.config_file, "a") as fh:
            fh.write("summarize = true\n")
        self.assertEqual(self.run_cli("ingest", "--no-summaries")[0], 0)
        code, out = self.run_cli("summarize", "--estimate")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"notes\s+1\s")

    def test_forget_without_a_terminal_asks_for_yes(self):
        self.configure(self.make_notes())
        self.assertEqual(self.ingest(), 0)
        with mock.patch.object(sys.stdin, "isatty", lambda: False):
            self.assertEqual(self.run_cli("forget", "notes")[0], 1)
        self.assertEqual(len(self.stored()), 1)


class SearchAfterIngestTest(IngestCase):
    def test_an_ingested_item_is_found_by_its_words_and_names_its_host(self):
        self.run_all(Script("fake", events=[item("a", "walrus pricing"), item("b", "unrelated")]))
        hits = search.search(self.db, "walrus", sources=("fake",))
        self.assertEqual([h["source_id"] for h in hits], ["m1:a"])
        self.assertEqual(hits[0]["meta"]["host"], "m1")


if __name__ == "__main__":
    unittest.main()


class SettingsChangeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.db = store.connect(os.path.join(self.tmp, "kb.sqlite"))
        self.addCleanup(self.db.close)

    def note(self, rel, text):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)

    def files_predate_the_last_run(self):
        store.set_watermark(self.db, "notes", "m1", json.dumps(time.time() + 3600))
        self.db.commit()

    def ingest(self, table):
        cfg = config.parse(f'machine = "m1"\n[sources.notes]\n{table}')
        self.assertEqual(ingest.run(self.db, cfg, LOG, with_summaries=False), 0)
        return sorted(r[0] for r in self.db.execute("SELECT DISTINCT source_id FROM docs"))

    def test_a_new_folder_is_read_in_full_even_when_its_files_predate_the_watermark(self):
        self.note("a/one.md", "first folder")
        self.note("b/two.md", "second folder")
        self.assertEqual(self.ingest(f'path = {json.dumps(os.path.join(self.tmp, "a"))}\n'), ["m1:one.md"])
        self.files_predate_the_last_run()
        self.assertEqual(self.ingest(f'path = {json.dumps(os.path.join(self.tmp, "b"))}\n'), ["m1:two.md"])

    def test_removing_an_exclusion_reads_the_files_it_hid(self):
        self.note("n/Journal/day.md", "a day")
        self.note("n/plan.md", "a plan")
        path = f'path = {json.dumps(os.path.join(self.tmp, "n"))}\n'
        self.assertEqual(self.ingest(path + 'exclude = ["Journal"]\n'), ["m1:plan.md"])
        self.files_predate_the_last_run()
        self.assertEqual(self.ingest(path), ["m1:Journal/day.md", "m1:plan.md"])

    def test_unchanged_settings_keep_reading_only_what_changed(self):
        self.note("n/plan.md", "a plan")
        path = f'path = {json.dumps(os.path.join(self.tmp, "n"))}\n'
        self.ingest(path)
        self.files_predate_the_last_run()
        self.db.execute("DELETE FROM docs")
        self.db.commit()
        self.assertEqual(self.ingest(path), [])
