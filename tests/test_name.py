import pathlib
import unittest

import kb

PACKAGE = pathlib.Path(kb.__file__).parent


class ProductNameTest(unittest.TestCase):
    def test_the_product_name_is_written_only_in_its_constant(self):
        offenders = []
        for path in PACKAGE.rglob("*.py"):
            if "_vendor" in path.parts or path == PACKAGE / "__init__.py":
                continue
            if kb.NAME.lower() in path.read_text(encoding="utf-8").lower():
                offenders.append(str(path.relative_to(PACKAGE)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
