import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run


def row(user, kind, score=None, answer="a", subtype=""):
    return {"user": user, "group": "g", "kind": kind, "score": score, "answer": answer, "subtype": subtype}


class ParseTest(unittest.TestCase):
    def test_fact_score_takes_the_first_grade_it_finds_in_the_judges_order(self):
        self.assertEqual(run.fact_score("0.5"), 0.5)
        self.assertEqual(run.fact_score("Score: 1.0"), 1.0)
        self.assertIsNone(run.fact_score("no grade here"))

    def test_op_score_takes_the_first_number_and_clamps_it(self):
        self.assertEqual(run.op_score("0.85"), 0.85)
        self.assertEqual(run.op_score("Output: 1.4"), 1.0)
        self.assertIsNone(run.op_score("none"))


class OpScoresTest(unittest.TestCase):
    def embed(self, texts):
        vectors = {"same": [1.0, 0.0], "other": [0.0, 1.0]}
        return np.array([vectors[t] for t in texts])

    def test_identical_replies_score_zero_and_unrelated_replies_score_one(self):
        self.assertEqual(run.diversity([row("u", "diversity", answer="same")] * 3, self.embed), 0.0)
        self.assertEqual(run.diversity([row("u", "diversity", answer="same"), row("u", "diversity", answer="other")],
                                       self.embed), 1.0)

    def test_overall_is_the_mean_over_each_persons_tasks_not_over_messages(self):
        rows = [row("u", "irrelevance_easy", 1.0), row("u", "irrelevance_easy", 0.0), row("u", "irrelevance_easy", 0.5),
                row("u", "sycophancy", 1.0, subtype="fact"),
                row("u", "diversity", answer="same"), row("u", "diversity", answer="other")]
        overall, by_task, subtypes = run.op_scores(rows, self.embed)
        self.assertEqual(by_task["irrelevance_easy"], 0.5)
        self.assertEqual(overall, (0.5 + 1.0 + 1.0) / 3)
        self.assertEqual(subtypes["fact"], 1.0)
        self.assertIsNone(by_task["irrelevance_hard"])


if __name__ == "__main__":
    unittest.main()
