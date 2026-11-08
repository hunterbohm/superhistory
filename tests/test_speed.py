import ast
import inspect
import unittest

from tests import speed


class SpeedTest(unittest.TestCase):
    def test_the_factor_is_measured_once_and_never_tightens_a_cap(self):
        first = speed.factor()
        self.assertGreaterEqual(first, 1.0)
        self.assertIs(speed.factor(), first)
        self.assertAlmostEqual(speed.scaled(2.0), 2.0 * first)

    def test_the_reference_workload_calls_no_code_of_this_repo(self):
        imports = [node for node in ast.walk(ast.parse(inspect.getsource(speed)))
                   if isinstance(node, (ast.Import, ast.ImportFrom))]
        names = {alias.name.split(".")[0] for node in imports for alias in node.names}
        names |= {node.module.split(".")[0] for node in imports if isinstance(node, ast.ImportFrom) and node.module}
        self.assertEqual(names, {"re", "time"})


if __name__ == "__main__":
    unittest.main()
