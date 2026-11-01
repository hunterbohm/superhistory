import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import splits  # noqa: E402

HOLD = """
import sys, time
sys.path.insert(0, {here!r})
import splits
splits.LOG = {log!r}
splits.held_out_run({test!r}, "baseline", "code", "commit", "run")
print("held", flush=True)
time.sleep(5)
"""


class HeldOutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = os.path.join(self.tmp.name, "runs.tsv")
        self.test = f"unit-{os.getpid()}-{time.monotonic_ns()}"
        self.addCleanup(self.release)

    def release(self):
        for label in splits.RUNS:
            held = splits._held.pop((self.test, label), None)
            if held:
                held.close()
            lock = os.path.join(tempfile.gettempdir(), f"bench-heldout-{self.test}-{label}.lock")
            if os.path.exists(lock):
                os.remove(lock)

    def holder(self):
        holder = subprocess.Popen([sys.executable, "-c", HOLD.format(here=HERE, log=self.log, test=self.test)],
                                  stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        splits.LOG = self.log

    def test_a_second_run_of_the_same_test_and_label_waits_its_turn(self):
        self.holder()
        with self.assertRaises(SystemExit) as stop:
            splits.held_out_run(self.test, "baseline", "code", "commit", "run")
        self.assertIn("is running", str(stop.exception))

    def test_another_label_of_the_same_test_runs_beside_it(self):
        self.holder()
        splits.held_out_run(self.test, "baseline-paired", "code", "commit", "paired run")
        self.assertIn((self.test, "baseline-paired"), splits._held)

    def test_a_run_logged_ahead_of_time_holds_nothing(self):
        self.holder()
        splits.held_out_run(self.test, "baseline", "code", "commit", "run", hold=False)
        self.assertNotIn((self.test, "baseline"), splits._held)

    def test_a_label_reruns_only_its_own_run(self):
        splits.LOG = self.log
        splits.held_out_run(self.test, "baseline", "code", "commit", "run")
        for code, run in (("other", "run"), ("code", "other")):
            with self.subTest(code=code, run=run), self.assertRaises(SystemExit):
                splits._held.pop((self.test, "baseline")).close()
                splits.held_out_run(self.test, "baseline", code, "commit", run)

    def test_only_the_four_labels(self):
        splits.LOG = self.log
        with self.assertRaises(SystemExit):
            splits.held_out_run(self.test, "fifth", "code", "commit", "run")


if __name__ == "__main__":
    unittest.main()
