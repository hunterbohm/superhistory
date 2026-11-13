import os
import shutil
import tempfile
import unittest

from kb import config
from kb.paths import Paths

from .network import Network, run_cli

LOGIN = ("sync", "login", "--app", "https://app.test", "--relay", "https://relay.test", "--label", "computer 1")
BROKEN = '[sources.github]\ninvolves = "@me"\n'  # valid alone; with [sync] on, a GitHub source must name its account


class ConfigTableTest(unittest.TestCase):
    """After init, join or recover the config gets an empty [sync] table, which is what makes ingest sync."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="superhistory-config-table-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.net = Network()
        self.home = os.path.join(self.tmp, "home")
        self.paths = Paths.under(self.home)

    def kb(self, *argv, **kw):
        with self.net.wired():
            return run_cli(self.home, *argv, **kw)

    def text(self):
        with open(self.paths.config_file, encoding="utf-8") as fh:
            return fh.read()

    def write(self, text):
        os.makedirs(self.home, exist_ok=True)
        with open(self.paths.config_file, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_init_adds_an_empty_table_that_still_loads(self):
        self.assertEqual(self.kb(*LOGIN)[0], 0)
        self.assertFalse(os.path.exists(self.paths.config_file), "login alone does not turn syncing on")
        code, out = self.kb("sync", "init")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.text(), "[sync]\n")
        loaded = config.load(self.paths.config_file)
        self.assertIn("sync", loaded.extra)
        self.assertIn("sync", loaded.units())
        self.assertIn("[sync]", out)

    def test_it_keeps_what_the_config_had_and_adds_the_table_once(self):
        notes = '[sources.notes]\npath = "/tmp/notes"'
        self.write(notes)  # no newline at the end
        self.assertEqual(self.kb(*LOGIN)[0], 0)
        self.assertEqual(self.kb("sync", "init")[0], 0)
        self.assertEqual(self.text(), notes + "\n\n[sync]\n")
        self.assertEqual(len(config.load(self.paths.config_file).sources), 1)
        self.assertEqual(self.kb("sync", "recover", stdin="\n")[0] != 0, True)  # a bad code adds nothing more
        self.assertEqual(self.text().count("[sync]"), 1)

    def test_a_config_that_already_has_the_table_is_not_touched(self):
        self.write('[sync]\n# mine\n')
        self.assertEqual(self.kb(*LOGIN)[0], 0)
        self.assertEqual(self.kb("sync", "init")[0], 0)
        self.assertEqual(self.text(), '[sync]\n# mine\n')

    def test_a_config_that_would_not_load_with_the_table_is_left_alone_and_says_why(self):
        self.write(BROKEN)
        self.assertEqual(self.kb(*LOGIN)[0], 0)
        code, out = self.kb("sync", "init")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.text(), BROKEN)
        self.assertIn("unchanged", out)
        self.assertIn("superhistory ingest", out)

    def test_other_actions_do_not_add_it(self):
        self.assertEqual(self.kb(*LOGIN)[0], 0)
        self.kb("sync", "status")
        self.assertFalse(os.path.exists(self.paths.config_file))


if __name__ == "__main__":
    unittest.main()
