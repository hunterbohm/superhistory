import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import compare  # noqa: E402


def row(i, correct, added, model):
    return {"id": f"q{i}", "kind": "single", "context": "", "correct": correct, "context_tokens": added,
            "tool_tokens": 0, "lookup_model_tokens": model}


class PairedTest(unittest.TestCase):
    def test_mcnemar_counts_only_the_questions_one_run_got_right(self):
        self.assertEqual(compare.mcnemar(0, 0), 1.0)
        self.assertAlmostEqual(compare.mcnemar(0, 4), 2 / 16)
        self.assertAlmostEqual(compare.mcnemar(1, 5), 2 * 7 / 64)
        self.assertEqual(compare.mcnemar(3, 3), 1.0)

    def test_a_run_compared_with_itself_changes_nothing(self):
        rows = [row(i, i % 3 != 0, 100 + i, 50) for i in range(30)]
        r = compare.paired("longmemeval", rows, rows, resamples=200)
        self.assertEqual((r["right_change"], r["right_interval"], r["only"]), (0.0, (0.0, 0.0), (0, 0)))
        self.assertEqual((r["added_change"], r["total_change"]), (0.0, 0.0))

    def test_changes_are_paired_by_question_and_cover_only_shared_questions(self):
        a = [row(i, True, 80, 20) for i in range(40)]
        b = [row(i, i >= 4, 100, 20) for i in range(40)] + [row(99, False, 10_000, 0)]
        r = compare.paired("locomo", a, b, resamples=500)
        self.assertEqual(r["n"], 40)
        self.assertEqual(r["only"], (4, 0))
        self.assertAlmostEqual(r["right_change"], 10.0)
        self.assertAlmostEqual(r["added_change"], -20.0)
        self.assertAlmostEqual(r["total_change"], -1 / 6 * 100)
        lo, hi = r["right_interval"]
        self.assertTrue(lo <= 10.0 <= hi and lo > 0)
        for end in r["added_interval"]:
            self.assertAlmostEqual(end, -20.0)

    def test_a_resample_with_no_baseline_tokens_is_left_out_not_a_crash(self):
        a = [row(0, True, 0, 0), row(1, True, 100, 0)]
        r = compare.paired("locomo", a, a, resamples=200)
        self.assertEqual(r["added_change"], 0.0)
        self.assertEqual(r["added_interval"], (0.0, 0.0))

    def test_runs_with_no_question_in_common_say_so(self):
        self.assertEqual(compare.paired("locomo", [row(0, True, 1, 1)], [row(1, True, 1, 1)]), {"n": 0})
        with tempfile.TemporaryDirectory() as out, mock.patch.object(compare, "OUT", out):
            os.makedirs(os.path.join(out, "locomo"))
            for label, i in (("a", 0), ("b", 1)):
                with open(os.path.join(out, "locomo", f"locomo10-with-summaries-test-{label}.jsonl"), "w") as fh:
                    fh.write(json.dumps(row(i, True, 1, 1)) + "\n")
            self.assertIn("no question both runs finished yet", compare.paired_table(("a", "b"), "test"))


if __name__ == "__main__":
    unittest.main()
