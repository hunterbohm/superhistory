import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score

SOURCES = {"m1": "gmail", "t1": "imessage"}
QUESTIONS = [{"id": "q1", "evidence": ["m1"]}, {"id": "q2", "evidence": ["m1", "t1"]}]
VERDICTS = {"x": {"q1": True, "q2": True}}


def setup(reads, skips):
    return {"tool": "x", "setup": "x", "per_month": "$0", "reads": dict.fromkeys(reads, ""),
            "skips": dict.fromkeys(skips, "")}


class ScoreTest(unittest.TestCase):
    def test_a_question_is_within_reach_only_when_every_evidence_source_is_collected(self):
        rows = score.score([setup(["gmail"], ["imessage"])], SOURCES, QUESTIONS, VERDICTS)
        self.assertEqual({r["id"]: r["within_reach"] for r in rows}, {"q1": True, "q2": False})

    def test_a_setup_must_place_every_month_source(self):
        with self.assertRaises(SystemExit):
            score.score([setup(["gmail"], [])], SOURCES, QUESTIONS, VERDICTS)

    def test_a_source_cannot_be_both_read_and_skipped(self):
        with self.assertRaises(SystemExit):
            score.score([setup(["gmail", "imessage"], ["imessage"])], SOURCES, QUESTIONS, VERDICTS)


if __name__ == "__main__":
    unittest.main()
