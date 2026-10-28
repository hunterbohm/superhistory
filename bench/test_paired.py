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
            open(os.path.join(folder, "bench", "harness.py"), "w").write("HARNESS = 3\n")
            with self.assertRaises(SystemExit):
                paired.baseline_checkout(folder, head, base, expected, kb_hash, repo)
            with self.assertRaises(SystemExit):
                paired.baseline_checkout(os.path.join(tmp, "other"), head, base, "0" * 16, kb_hash, repo)

    def test_a_start_with_other_code_than_the_frozen_code_refuses(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(paired, "FREEZE", os.path.join(tmp, "f.json")):
            paired.freeze("head", "final", "base", "baseline")
            paired.freeze("head", "final", "base", "baseline")
            self.assertEqual(json.load(open(paired.FREEZE))["final_code"], "final")
            with self.assertRaises(SystemExit):
                paired.freeze("head", "changed", "base", "baseline")


if __name__ == "__main__":
    unittest.main()
