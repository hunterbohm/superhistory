import ast
import pathlib
import unittest

import kb

PACKAGE = pathlib.Path(kb.__file__).parent


def printed_strings(tree):
    """Every string in the code except docstrings, which only explain."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(node):
            docstrings.add(id(node.body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


class ProductNameTest(unittest.TestCase):
    def test_the_product_name_is_written_only_in_its_constants(self):
        offenders = []
        for path in PACKAGE.rglob("*.py"):
            if "_vendor" in path.parts or path == PACKAGE / "__init__.py":
                continue
            strings = printed_strings(ast.parse(path.read_text(encoding="utf-8")))
            if any(kb.NAME.lower() in text.lower() for text in strings):
                offenders.append(str(path.relative_to(PACKAGE)))
        self.assertEqual(offenders, [])

    def test_the_command_is_the_lowercase_name(self):
        self.assertEqual(kb.COMMAND, "superhistory")


if __name__ == "__main__":
    unittest.main()
