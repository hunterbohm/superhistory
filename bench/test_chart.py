import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import chart  # noqa: E402


class SecondsNoteTest(unittest.TestCase):
    def found(self, alone):
        return {f"t{i}": {"label": f"Tool {i}", "alone": a} for i, a in enumerate(alone)}

    def test_the_note_says_where_each_number_comes_from(self):
        self.assertEqual(chart.seconds_note(self.found([True, True])), "median of lookups timed alone")
        self.assertEqual(chart.seconds_note(self.found([False, False])),
                         "median of each tool's lookups during its month run")
        self.assertEqual(chart.seconds_note(self.found([True, False])),
                         "median of lookups timed alone (Tool 1: its month run)")
        self.assertEqual(chart.seconds_note(self.found([False, True, False])),
                         "median of lookups timed alone (Tool 0, Tool 2: their month runs)")

    def test_a_tool_without_a_timed_alone_run_uses_its_month_run(self):
        rows = [{"lookup_seconds": s} for s in (1.0, 2.0, 9.0)]
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(chart.lookup_seconds(tmp, "check", rows), (2.0, False))
            with open(os.path.join(tmp, "latency.jsonl"), "w") as fh:
                fh.write("\n".join(json.dumps({"tool": "check", "seconds": s}) for s in (0.5, 0.7, 0.9)) + "\n")
            self.assertEqual(chart.lookup_seconds(tmp, "check", rows), (0.7, True))
            self.assertEqual(chart.lookup_seconds(tmp, "mem0", rows), (2.0, False))


if __name__ == "__main__":
    unittest.main()
