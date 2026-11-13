import ast
import hashlib
import inspect
import unittest
from unittest import mock

from tests import speed

BLOB = "76d5de4ecdc664c7d51860d8c5c3330eb4afdd95"  # git rev-parse 5fc31ad:tests/fixtures/main_mask.py


def imported(source):
    """The top-level names of every module `source` imports."""
    nodes = [node for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.Import, ast.ImportFrom))]
    names = {alias.name.split(".")[0] for node in nodes for alias in node.names}
    return names | {node.module.split(".")[0] for node in nodes if isinstance(node, ast.ImportFrom) and node.module}


class SpeedTest(unittest.TestCase):
    def test_the_factor_is_measured_once_and_never_tightens_a_cap(self):
        first = speed.factor()
        self.assertGreaterEqual(first, 1.0)
        self.assertIs(speed.factor(), first)
        self.assertAlmostEqual(speed.scaled(2.0), 2.0 * first)

    def test_the_reference_workload_calls_no_code_of_this_repo(self):
        self.assertEqual(imported(inspect.getsource(speed)), {"importlib", "json", "os", "sys", "time"})
        with open(speed._FROZEN, encoding="utf-8") as f:
            self.assertEqual(imported(f.read()),
                             {"base64", "bisect", "hashlib", "logging", "os", "re", "sys", "traceback"})

    def test_the_frozen_mask_it_times_never_changes(self):
        with open(speed._FROZEN, "rb") as f:
            data = f.read()
        self.assertEqual(hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(), BLOB)

    def test_a_failed_cap_shows_the_factor(self):
        with mock.patch.object(speed, "_factor", 1.25):
            with self.assertRaises(AssertionError) as caught:
                self.assertLess(2.0, speed.scaled(1.5))
        self.assertIn("1.875 (a 1.5 s cap times speed factor 1.250)", str(caught.exception))

    def test_a_python_with_no_time_of_its_own_is_held_to_the_larger(self):
        with mock.patch.object(speed.sys, "version_info", (3, 11, 0)):
            self.assertEqual(speed.reference(), max(speed.REFERENCE_SECONDS.values()))
        with mock.patch.object(speed.sys, "version_info", (3, 9, 25)):
            self.assertEqual(speed.reference(), speed.REFERENCE_SECONDS[(3, 9)])


if __name__ == "__main__":
    unittest.main()
