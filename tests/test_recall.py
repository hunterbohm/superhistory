import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from kb import store

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("recall", os.path.join(HERE, "..", "bench", "recall.py"))
recall = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recall)


def iso(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


class RecallTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.db_path = os.path.join(tmp.name, "data", "superhistory.sqlite")
        db = store.connect(self.db_path)
        store.store_item(db, "vault", "offer/SALES-OFFER.md", iso(3), "SALES-OFFER",
                         "Walrus plan for teams of 26 to 50 costs $15,000 a month.", {"path": "/v/offer/SALES-OFFER.md"})
        for n in range(3):
            store.store_item(db, "git", f"commit{n}", iso(20), "price commit",
                             "Walrus plan walrus plan for teams of 26 to 50 costs $10,000 a month.", {})
        db.commit()
        db.close()

    def run_probe(self, questions, *flags, db=True):
        path = os.path.join(self.dir, "q.jsonl")
        with open(path, "w") as f:
            f.write("# a comment line\n\n" + "".join(json.dumps(q) + "\n" for q in questions))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            recall.main([path, *(["--db", self.db_path] if db else []), *flags])
        return out.getvalue()

    def test_a_stale_record_above_the_current_one_is_counted(self):
        q = {"q": "walrus plan teams 26 to 50", "want": [{"path": "SALES-OFFER", "text": "$15,000"}],
             "stale": [{"text": ["$10,000", "26"]}], "topic": "price"}
        out = self.run_probe([q], "--json")
        row = json.loads(out.splitlines()[0])
        self.assertEqual(row["stale_rank"], 1)
        self.assertGreater(row["rank"], 1)
        self.assertTrue(row["stale_above"])
        self.assertTrue(row["top"])
        self.assertFalse(json.loads(self.run_probe([q], "--json", "--top", "1").splitlines()[0])["top"])

    def test_every_field_of_a_matcher_must_fit(self):
        q = {"q": "walrus plan", "want": [{"path": "SALES-OFFER", "text": "$10,000"}]}
        row = json.loads(self.run_probe([q], "--json").splitlines()[0])
        self.assertIsNone(row["rank"])
        self.assertFalse(row["top"])
        self.assertFalse(row["stale_above"])

    def test_the_summary_counts_found_questions_and_stale_ones_among_changed_questions(self):
        found = {"q": "walrus plan", "want": [{"source": "vault"}], "stale": [{"source": "nowhere"}]}
        missed = {"q": "walrus plan", "want": [{"id": -1}]}
        out = self.run_probe([found, missed])
        self.assertIn("yes rank  ", out.splitlines()[0])
        self.assertIn("no  rank  -", out.splitlines()[1])
        self.assertEqual(out.splitlines()[-1], "top 5: 1 of 2; stale above current: 0 of 1 changed questions")

    def test_the_installed_commands_printed_ids_are_scored_in_its_order(self):
        db = store.connect(self.db_path, readonly=True)
        offer, commit = (db.execute("SELECT id FROM docs WHERE source=?", (s,)).fetchone()[0] for s in ("vault", "git"))
        db.close()
        fake = os.path.join(self.dir, "fake.py")
        with open(fake, "w") as f:
            f.write("import sys\nassert sys.argv[1:] == ['walrus plan', '--limit', '40']\n"
                    f"print('header line')\nprint('1. 2026-11-17  vault  raw #{offer}  SALES-OFFER')\n"
                    f"print('   snippet #{commit}')\nprint('2. 2026-11-01 (19 days ago)  git  doc #{commit}  price commit')\n")
        q = {"q": "walrus plan", "want": [{"path": "SALES-OFFER"}], "stale": [{"source": "git"}]}
        with mock.patch.dict(os.environ, {"SUPERHISTORY_HOME": self.dir}):
            row = json.loads(self.run_probe([q], "--json", "--command", f"{sys.executable} {fake}",
                                            db=False).splitlines()[0])
        self.assertEqual((row["rank"], row["stale_rank"], row["stale_above"]), (1, 2, False))

    def test_the_command_with_another_store_is_refused(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_probe([{"q": "walrus", "want": [{"id": 1}]}], "--command", "superhistory search")

    def test_a_skipped_hit_takes_no_place(self):
        q = {"q": "walrus plan teams 26 to 50", "want": [{"path": "SALES-OFFER"}], "stale": [{"source": "git"}]}
        row = json.loads(self.run_probe([q], "--json", "--skip", "commit").splitlines()[0])
        self.assertEqual((row["rank"], row["stale_rank"]), (1, None))

    def test_a_question_without_a_wanted_record_is_refused(self):
        with self.assertRaises(SystemExit):
            self.run_probe([{"q": "walrus"}])


if __name__ == "__main__":
    unittest.main()
