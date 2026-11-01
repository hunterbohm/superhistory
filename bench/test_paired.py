import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "public")]

import common  # noqa: E402
import paired  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def kb_hash(folder):
    digest = hashlib.sha256()
    for root, _, files in sorted(os.walk(os.path.join(folder, "kb"))):
        for name in sorted(f for f in files if f.endswith(".py")):
            digest.update(open(os.path.join(root, name), "rb").read())
    return digest.hexdigest()[:16]


class PairedTest(unittest.TestCase):
    def test_the_run_names_are_the_runners_own(self):
        self.assertEqual(paired.TESTS["month"][1].format(label="final"), "b-final:check")
        for test, base in (("longmemeval-s", "s-without-summaries"), ("locomo", "locomo10-with-summaries")):
            args = argparse.Namespace(split="test", label="final", slice=None, sample=None, limit=None, fresh=False,
                                      count=False)
            with mock.patch("splits.read", lambda test, half: set()):
                _, name = common.in_split(test, [], base, args)
            self.assertEqual(paired.TESTS[test][1].format(label="final"), name)

    def test_the_baseline_checkout_is_the_final_commit_with_the_baselines_kb(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            os.makedirs(os.path.join(repo, "kb"))
            git(tmp, "init", "-q", repo)
            git(repo, "config", "user.email", "test@example.com")
            git(repo, "config", "user.name", "test")
            open(os.path.join(repo, "kb", "check.py"), "w").write("PICKS = 4\n")
            open(os.path.join(repo, "kb", "items.jsonl"), "w").write("{}\n")
            git(repo, "add", ".")
            git(repo, "commit", "-qm", "baseline")
            base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
            expected = kb_hash(repo)
            open(os.path.join(repo, "kb", "check.py"), "w").write("PICKS = 8\n")
            open(os.path.join(repo, "kb", "added.py"), "w").write("NEW = 1\n")
            os.makedirs(os.path.join(repo, "bench"))
            open(os.path.join(repo, "bench", "harness.py"), "w").write("HARNESS = 2\n")
            git(repo, "add", ".")
            git(repo, "commit", "-qm", "final")
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
            folder = os.path.join(tmp, "paired")
            self.assertEqual(paired.baseline_checkout(folder, head, base, expected, kb_hash, repo), expected)
            self.assertEqual(open(os.path.join(folder, "kb", "check.py")).read(), "PICKS = 4\n")
            self.assertFalse(os.path.exists(os.path.join(folder, "kb", "added.py")))
            self.assertEqual(open(os.path.join(folder, "bench", "harness.py")).read(), "HARNESS = 2\n")
            self.assertEqual(paired.baseline_checkout(folder, head, base, expected, kb_hash, repo), expected)
            open(os.path.join(folder, "kb", "items.jsonl"), "w").write('{"changed": 1}\n')
            with self.assertRaises(SystemExit) as stop:
                paired.baseline_checkout(folder, head, base, expected, kb_hash, repo)
            self.assertIn("kb/items.jsonl", str(stop.exception))
            open(os.path.join(folder, "kb", "items.jsonl"), "w").write("{}\n")
            open(os.path.join(folder, "bench", "harness.py"), "w").write("HARNESS = 3\n")
            with self.assertRaises(SystemExit):
                paired.baseline_checkout(folder, head, base, expected, kb_hash, repo)
            with self.assertRaises(SystemExit):
                paired.baseline_checkout(os.path.join(tmp, "other"), head, base, "0" * 16, kb_hash, repo)

    def repo(self, tmp):
        repo = os.path.join(tmp, "repo")
        for folder in ("kb", "bench/splits", "bench/results"):
            os.makedirs(os.path.join(repo, folder))
        git(tmp, "init", "-q", repo)
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "test")
        for path in ("kb/check.py", "bench/run.py", "bench/splits/heldout-runs.tsv", "bench/results/old.jsonl"):
            open(os.path.join(repo, path), "w").write("v1\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "frozen")
        return repo

    def test_what_a_run_writes_is_not_a_code_change_but_anything_else_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            open(os.path.join(repo, "bench/splits/heldout-runs.tsv"), "a").write("a run\n")
            open(os.path.join(repo, "bench/splits/paired-freeze.json"), "w").write("{}\n")
            open(os.path.join(repo, "bench/results/old.jsonl"), "a").write("a row\n")
            open(os.path.join(repo, "bench/results/new.jsonl"), "w").write("a row\n")
            self.assertEqual(paired.code_changes(repo), [])
            open(os.path.join(repo, "kb/added.py"), "w").write("NEW = 1\n")
            open(os.path.join(repo, "bench/helper.py"), "w").write("HELPER = 1\n")
            open(os.path.join(repo, "bench/run.py"), "a").write("v2\n")
            self.assertEqual(sorted(paired.code_changes(repo)), ["bench/helper.py", "bench/run.py", "kb/added.py"])
            self.assertEqual(sorted(paired.code_changes(repo, kb_from_baseline=True)),
                             ["bench/helper.py", "bench/run.py", "kb/added.py"])
            os.remove(os.path.join(repo, "kb/added.py"))
            open(os.path.join(repo, "kb/check.py"), "w").write("baseline\n")
            self.assertNotIn("kb/check.py", paired.code_changes(repo, kb_from_baseline=True))
            self.assertIn("kb/check.py", paired.code_changes(repo))

    def test_a_pass_resumes_only_its_own_commits_runs(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(paired.splits, "LOG", os.path.join(tmp, "log")):
            final, baseline = os.path.join(tmp, "final"), os.path.join(tmp, "baseline")
            with open(paired.splits.LOG, "w") as fh:
                fh.write("date\ttest\tlabel\trun\tcode\tcommit\n"
                         "d\tmonth\tfinal\tb-final:check\tcode\thead\n")
            paired.earlier_runs(["month"], "head", (final, baseline))
            with self.assertRaises(SystemExit):
                paired.earlier_runs(["month"], "other", (final, baseline))
            os.makedirs(os.path.join(baseline, "bench/results/hillclimb/month/b-baseline-paired"))
            open(os.path.join(baseline, "bench/results/hillclimb/month/b-baseline-paired/check.jsonl"), "w").close()
            with self.assertRaises(SystemExit) as stop:
                paired.earlier_runs(["month"], "head", (final, baseline))
            self.assertIn("no logged run", str(stop.exception))

    def test_rival_rows_beside_the_final_month_rows_are_not_a_run(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(paired.splits, "LOG", os.path.join(tmp, "log")):
            final, baseline = os.path.join(tmp, "final"), os.path.join(tmp, "baseline")
            with open(paired.splits.LOG, "w") as fh:
                fh.write("date\ttest\tlabel\trun\tcode\tcommit\n")
            month = os.path.join(final, "bench/results/hillclimb/month/b-final")
            os.makedirs(month)
            for rival in ("mem0", "supermemory", "claude-mem"):
                open(os.path.join(month, f"{rival}.jsonl"), "w").write("{}\n")
            paired.earlier_runs(["month"], "head", (final, baseline))
            open(os.path.join(month, "check.jsonl"), "w").write("{}\n")
            with self.assertRaises(SystemExit) as stop:
                paired.earlier_runs(["month"], "head", (final, baseline))
            self.assertIn("no logged run", str(stop.exception))

    def test_the_baseline_month_rows_are_copied_beside_the_final_and_rival_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout, out = os.path.join(tmp, "baseline"), os.path.join(tmp, "out")
            ran = os.path.join(checkout, "bench/results/hillclimb/month/b-baseline-paired")
            os.makedirs(ran)
            for name in ("check.jsonl", "run.json", "table.md"):
                open(os.path.join(ran, name), "w").write(name)
            os.makedirs(os.path.join(out, "month/b-final"))
            open(os.path.join(out, "month/b-final/mem0.jsonl"), "w").write("rival")
            paired.copy_baseline_results("month", checkout, out)
            self.assertEqual(sorted(os.listdir(os.path.join(out, "month/b-baseline-paired"))),
                             ["check.jsonl", "run.json", "table.md"])
            self.assertEqual(open(os.path.join(out, "month/b-baseline-paired/check.jsonl")).read(), "check.jsonl")
            self.assertEqual(os.listdir(os.path.join(out, "month/b-final")), ["mem0.jsonl"])

    def test_a_start_with_other_code_than_the_frozen_code_refuses(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(paired, "FREEZE", os.path.join(tmp, "f.json")):
            paired.freeze("head", "final", "base", "baseline")
            paired.freeze("head", "final", "base", "baseline")
            self.assertEqual(json.load(open(paired.FREEZE))["final_code"], "final")
            with self.assertRaises(SystemExit):
                paired.freeze("head", "changed", "base", "baseline")


if __name__ == "__main__":
    unittest.main()
