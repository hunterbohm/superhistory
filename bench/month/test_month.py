import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build


class MonthTest(unittest.TestCase):
    def test_committed_month_matches_a_fresh_valid_build(self):
        self.assertEqual(build.main(["--check"]), 0)


if __name__ == "__main__":
    unittest.main()
