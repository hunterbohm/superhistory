import os
import sys
import threading
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import outside


def row(user, kind, score=None, answer="a", subtype=""):
    return {"user": user, "group": "g", "kind": kind, "score": score, "answer": answer, "subtype": subtype}


class SetupTest(unittest.TestCase):
    def test_replies_come_from_the_benchmarks_answer_model(self):
        self.assertEqual(outside.run.ANSWER_MODEL, "claude-sonnet-5-5")


class LimitsTest(unittest.TestCase):
    def test_no_more_than_two_calls_run_at_once(self):
        limits, lock, now, most = outside.Limits(), threading.Lock(), [0], [0]

        def call():
            with lock:
                now[0] += 1
                most[0] = max(most[0], now[0])
            time.sleep(0.05)
            with lock:
                now[0] -= 1

        threads = [threading.Thread(target=limits.send, args=("m", call)) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(most[0], 2)

    def test_a_limited_model_gets_no_calls_until_its_pause_ends_and_a_second_limit_stops_the_run(self):
        limits = outside.Limits()
        limits.PAUSE_SECONDS = 0.2
        limits.limited("m")
        with self.assertRaises(outside.RateLimited):
            limits.send("m", lambda: self.fail("sent during the pause"))
        self.assertEqual(limits.send("other", lambda: "ok"), "ok")
        time.sleep(0.25)
        self.assertEqual(limits.send("m", lambda: "ok"), "ok")
        limits.limited("m")
        with self.assertRaises(outside.Stopped):
            limits.wait()


class ParseTest(unittest.TestCase):
    def test_fact_score_takes_the_first_grade_it_finds_in_the_judges_order(self):
        self.assertEqual(outside.fact_score("0.5"), 0.5)
        self.assertEqual(outside.fact_score("Score: 1.0"), 1.0)
        self.assertIsNone(outside.fact_score("no grade here"))

    def test_op_score_takes_the_first_number_and_clamps_it(self):
        self.assertEqual(outside.op_score("0.85"), 0.85)
        self.assertEqual(outside.op_score("Output: 1.4"), 1.0)
        self.assertIsNone(outside.op_score("none"))


class OpScoresTest(unittest.TestCase):
    def embed(self, texts):
        vectors = {"same": [1.0, 0.0], "other": [0.0, 1.0]}
        return np.array([vectors[t] for t in texts])

    def test_identical_replies_score_zero_and_unrelated_replies_score_one(self):
        self.assertEqual(outside.diversity([row("u", "diversity", answer="same")] * 3, self.embed), 0.0)
        self.assertEqual(outside.diversity([row("u", "diversity", answer="same"), row("u", "diversity", answer="other")],
                                       self.embed), 1.0)

    def test_overall_is_the_mean_over_each_persons_tasks_not_over_messages(self):
        rows = [row("u", "irrelevance_easy", 1.0), row("u", "irrelevance_easy", 0.0), row("u", "irrelevance_easy", 0.5),
                row("u", "sycophancy", 1.0, subtype="fact"),
                row("u", "diversity", answer="same"), row("u", "diversity", answer="other")]
        overall, by_task, subtypes = outside.op_scores(rows, self.embed)
        self.assertEqual(by_task["irrelevance_easy"], 0.5)
        self.assertEqual(overall, (0.5 + 1.0 + 1.0) / 3)
        self.assertEqual(subtypes["fact"], 1.0)
        self.assertIsNone(by_task["irrelevance_hard"])


if __name__ == "__main__":
    unittest.main()
