import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from kb import ai, cli, config, store, trial
from kb.paths import Paths
from tests.support import FakeModel, plan_and_pick

MONTH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kb", "sample_month")


class TrialTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = Paths.under(tmp.name)
        self.lines = []

    def run_trial(self, cfg=None, model=None, chosen=None):
        cfg = cfg or config.Config()
        with mock.patch.object(ai, "load", lambda settings: model):
            trial.run(self.paths, cfg, MONTH, chosen, out=self.lines.append)
        return "\n".join(self.lines)

    def test_without_a_plugin_it_shows_what_search_finds_and_stops_before_the_pick(self):
        out = self.run_trial()
        self.assertIn("Loaded 8 made-up records", out)
        self.assertIn('Question: "When is the Pelican Bakery website launching now?"', out)
        self.assertIn("gmail  Pelican launch date", out)
        self.assertIn("Set one up with `kb setup`", out)
        self.assertIn("the right result is to add nothing", out)
        self.assertFalse(self.paths.db.exists(), "the user's own database is never created")

    def test_with_a_plugin_it_shows_the_records_the_check_keeps_and_adds_nothing_when_nothing_fits(self):
        launch = plan_and_pick("pelican launch", use=[1])
        poem = plan_and_pick("autumn rain tin roof poem", use=[])
        model = FakeModel(lambda prompt: (poem if "poem" in prompt.user else launch).reply(prompt))
        cfg = config.parse('[ai]\nplugin = "anthropic"\nkey = "env:KB_TEST_UNUSED"\n')
        out = self.run_trial(cfg, model)
        self.assertIn("The check runs with fake (fake-1).", out)
        self.assertIn("The check searched for: pelican launch", out)
        self.assertIn("Records that answer it: 1 of 2 shown.", out)
        self.assertRegex(out, r"Question: \"Write a short poem.*\n(.*\n)*.*Added nothing\.")

    def test_one_question_by_id(self):
        out = self.run_trial(chosen="q2")
        self.assertIn("Why did the Pelican checkout fail", out)
        self.assertNotIn("poem", out)

    def test_loading_the_month_twice_changes_nothing(self):
        self.run_trial()
        db = store.connect(self.paths.for_try().db)
        self.addCleanup(db.close)
        before = db.execute("SELECT count(*), max(id) FROM docs").fetchone()
        self.assertEqual(trial.load(db, MONTH), 8)
        self.assertEqual(db.execute("SELECT count(*), max(id) FROM docs").fetchone(), before)


class TryCommandTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"KB_HOME": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_try_then_search_the_made_up_month(self):
        code, out, _ = self.run_cli("try", "--month", MONTH)
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("--try", "search", "sandbox key")
        self.assertEqual(code, 0)
        self.assertIn("Pelican Bakery checkout bug", out)
        code, _, err = self.run_cli("search", "sandbox key")
        self.assertEqual(code, 1, "the user's own knowledge base is separate")

    def test_plain_kb_try_works_without_the_benchmarks_month(self):
        code, out, _ = self.run_cli("try")
        self.assertEqual(code, 0)
        self.assertIn("made-up records", out)

    def test_a_missing_month_says_how_to_point_at_one(self):
        code, _, err = self.run_cli("try", "--month", "/nonexistent/month")
        self.assertEqual(code, 1)
        self.assertIn("--month", err)


if __name__ == "__main__":
    unittest.main()
