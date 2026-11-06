import os
import shutil
import tempfile
import unittest
from unittest import mock

import kb.sync
from kb import store
from kb.paths import Paths

from .network import Network, run_cli
from .support import connect

LOGIN = ("sync", "login", "--app", "https://app.test", "--relay", "https://relay.test", "--label", "computer 1")


class AfterIngestTest(unittest.TestCase):
    """`kb ingest` runs one sync when the config has a [sync] table, through the real command against the fake relay."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="superhistory-after-ingest-")
        self.net = Network()
        self.home = os.path.join(self.tmp, "home")
        self.notes = os.path.join(self.tmp, "notes")
        os.makedirs(self.notes)
        with open(os.path.join(self.notes, "plan.md"), "w", encoding="utf-8") as fh:
            fh.write("# The plan\n\nbuy more pickled onions for the winter\n")
        self.paths = Paths.under(self.home)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write_config(self, sync=True):
        os.makedirs(self.home, exist_ok=True)
        with open(self.paths.config_file, "w", encoding="utf-8") as fh:
            fh.write(f'[sources.notes]\npath = "{self.notes}"\n' + ("\n[sync]\n" if sync else ""))

    def kb(self, *argv, **kw):
        with self.net.wired():
            return run_cli(self.home, *argv, **kw)

    def failure(self):
        db = connect(self.paths.db)
        try:
            return store.get_watermark(db, store.STATE, "fail:sync")
        finally:
            db.close()

    def set_up_sync(self):
        code, out = self.kb(*LOGIN)
        self.assertEqual(code, 0, out)
        code, out = self.kb("sync", "init")
        self.assertEqual(code, 0, out)

    def test_ingest_sends_what_it_read(self):
        self.write_config(sync=False)
        self.set_up_sync()
        self.write_config()
        self.net.sealed.clear()
        code, out = self.kb("ingest", "--no-summaries")
        self.assertEqual(code, 0, out)
        self.assertTrue(any(b"pickled onions" in plain for plain in self.net.sealed), "the note never left")
        self.assertIsNone(self.failure())

    def test_no_write_transaction_is_open_when_sync_runs(self):
        self.write_config(sync=False)
        self.set_up_sync()
        self.write_config()
        seen = []
        real = kb.sync.after_ingest

        def spy(paths, config, db, log):
            seen.append(db.in_transaction)
            return real(paths, config, db, log)

        with mock.patch.object(kb.sync, "after_ingest", spy):
            code, out = self.kb("ingest", "--no-summaries")
        self.assertEqual((code, seen), (0, [False]), out)

    def test_not_signed_in_says_how_to_sign_in(self):
        self.write_config()
        code, out = self.kb("ingest", "--no-summaries")
        self.assertEqual(code, 1, out)
        self.assertIn("superhistory sync login", self.failure())

    def test_signed_in_but_not_in_a_group_says_how_to_join(self):
        self.write_config(sync=False)
        code, out = self.kb(*LOGIN)
        self.assertEqual(code, 0, out)
        self.write_config()
        code, out = self.kb("ingest", "--no-summaries")
        self.assertEqual(code, 1, out)
        self.assertIn("superhistory sync", self.failure())
        self.assertNotIn("Traceback", self.failure())

    def test_no_sync_table_means_no_sync(self):
        self.write_config(sync=False)
        self.set_up_sync()
        self.write_config(sync=False)
        self.net.sealed.clear()
        code, out = self.kb("ingest", "--no-summaries")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.net.sealed, [])

    def test_sync_table_alone_is_a_config_that_ingests(self):
        self.write_config(sync=False)
        self.set_up_sync()
        with open(self.paths.config_file, "w", encoding="utf-8") as fh:
            fh.write("[sync]\n")
        code, out = self.kb("ingest", "--no-summaries")
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
