import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run  # noqa: E402


class HeldoutTableTest(unittest.TestCase):
    def test_the_committed_table_regenerates_byte_for_byte(self):
        with open(run.HELDOUT_TABLE, "rb") as fh:
            committed = fh.read()
        self.assertEqual(run.table_text(run.HELDOUT_ROWS).encode("utf-8"), committed,
                         "bench/run.py heldout-table no longer writes the committed table")

    def test_every_tool_has_all_171_questions(self):
        for name, path in run.HELDOUT_ROWS:
            self.assertEqual(len(run.read_jsonl(path)), 171, name)


if __name__ == "__main__":
    unittest.main()
