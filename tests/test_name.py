import pathlib
import unittest

import kb

PACKAGE = pathlib.Path(kb.__file__).parent


class ProductNameTest(unittest.TestCase):
    def test_the_product_name_is_written_only_in_its_constants(self):
        offenders = []
        for path in PACKAGE.rglob("*.py"):
            if "_vendor" in path.parts or path == PACKAGE / "__init__.py":
                continue
            text = path.read_text(encoding="utf-8").lower()
            if kb.NAME.lower() in text or kb.LEGACY_NAME.lower() in text:
                offenders.append(str(path.relative_to(PACKAGE)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
