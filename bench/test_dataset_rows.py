import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIST = os.path.join(HERE, "DATASET_ROWS.txt")


def setUpModule():
    if not os.path.exists(LIST):
        raise unittest.SkipTest("bench/DATASET_ROWS.txt is not in this tree")


def listed():
    with open(LIST, encoding="utf-8") as fh:
        return [line.rstrip("\n").split("\t") for line in fh if line.strip()]


class DatasetRowsTest(unittest.TestCase):
    def test_each_line_is_a_row_file_path_and_a_reason(self):
        for line in listed():
            self.assertEqual(len(line), 2, line)
            path, reason = line
            self.assertTrue(path.startswith("bench/") and path.endswith((".jsonl", ".jsonl.gz")), path)
            self.assertTrue(reason.strip(), path)

    def test_no_path_repeats_and_no_table_or_chart_is_listed(self):
        paths = [path for path, _ in listed()]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertFalse([p for p in paths if p.endswith((".md", ".png", ".tsv", ".run.json"))])

    def test_the_month_rows_are_never_listed(self):
        for path, _ in listed():
            self.assertNotIn("/month/", path, path)
            self.assertNotIn("/month-b/", path, path)

    def test_a_listed_file_that_is_present_is_a_file(self):
        for path, _ in listed():
            full = os.path.join(ROOT, path)
            if not os.path.exists(full):
                continue
            self.assertTrue(os.path.isfile(full), path)


if __name__ == "__main__":
    unittest.main()
