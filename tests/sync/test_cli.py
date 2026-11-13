import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from kb.paths import HOME_ENV
from kb.sync import keys, records

from .fake_relay import FakeRelay
from .support import EMAIL, connect, put_item, store_path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="superhistory-cli-test-")
        self.fake = FakeRelay()
        self.fake.sign_up(EMAIL)
        self.fake.browser_user = EMAIL
        self.url, self.server = self.fake.serve()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def argv(self, *args):
        return [sys.executable, "-u", "-m", "kb", *args]

    def env(self, home):
        return {**os.environ, "PYTHONPATH": ROOT, HOME_ENV: os.path.join(self.tmp, home)}

    def run_cli(self, home, *args, stdin=""):
        proc = subprocess.run(self.argv(*args), input=stdin, capture_output=True, text=True, env=self.env(home),
                              timeout=120)
        return proc.returncode, proc.stdout + proc.stderr

    def store(self, home):
        with open(os.path.join(self.tmp, home, "install-id"), encoding="ascii") as fh:
            prefix = fh.read().strip()
        return connect(store_path(os.path.join(self.tmp, home))), prefix

    def test_cli_sets_up_pairs_and_syncs(self):
        rc, out = self.run_cli("a", "sync", "login", "--app", self.url, "--relay", self.url, "--label", "computer 1")
        self.assertEqual(rc, 0, out)
        self.assertIn('This computer is "computer 1"', out)
        db, prefix = self.store("a")
        put_item(db, "notes", f"{prefix}:hello", "2026-10-01", "Hello", "hello from computer 1", {})
        db.close()
        rc, out = self.run_cli("a", "sync", "init")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"VRC1(-[0-9A-Z]{4}){8}")
        self.assertEqual(oct(os.stat(os.path.join(self.tmp, "a", "sync", "keyring.json")).st_mode & 0o777),
                         "0o600")

        rc, out = self.run_cli("b", "sync", "login", "--app", self.url, "--relay", self.url, "--label", "computer 2")
        self.assertEqual(rc, 0, out)
        adder = subprocess.Popen(self.argv("sync", "add-device"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, env=self.env("a"))
        shown = ""
        while "VP1-" not in shown:
            shown += adder.stdout.readline()
        code = re.search(r"VP1[-0-9A-Z]+", shown).group(0)
        adder.stdin.write("y\n")
        adder.stdin.flush()
        rc, joined = self.run_cli("b", "sync", "join", stdin=code + "\n")
        added = shown + adder.communicate(timeout=120)[0]
        self.assertEqual(rc, 0, joined)
        self.assertEqual(adder.returncode, 0, added)
        words = re.search(r"same words: (\w+ \w+ \w+)", joined).group(1)
        self.assertIn(f"Its words: {words}", added)
        self.assertIn('"computer 2" joined', added)

        db, b_prefix = self.store("b")
        self.assertTrue(records.item_exists(db, "notes", f"{prefix}:hello"))
        theirs = f"{b_prefix}@laptop:notes.md"
        put_item(db, "notes", theirs, "2026-10-02", "Notes", "read by computer 2 over ssh", {})
        db.close()
        rc, out = self.run_cli("b", "sync", "status")
        self.assertEqual(rc, 0, out)
        self.assertIn("computer 1", out)
        self.assertIn("computer 2 (this computer)", out)
        rc, out = self.run_cli("b", "sync")
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("a", "sync")
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("a", "sync", "doctor")
        self.assertEqual(rc, 0, out)
        self.assertIn("one key per device", out)

        removed = keys.load_json(os.path.join(self.tmp, "b", "sync", "login.json"))["device_id"]
        rc, out = self.run_cli("a", "sync", "remove", "computer 2", stdin="n\n")
        self.assertEqual(rc, 1, out)
        self.assertIn("computer 2 read 1 item. Removing it deletes them from every computer. Remove it? [y/N]", out)
        self.assertIn("Nothing was removed", out)
        self.assertFalse(self.fake.devices[removed]["revoked"])
        rc, out = self.run_cli("a", "sync", "remove", "computer 2", stdin="y\nn\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("Removed computer 2", out)
        self.assertTrue(self.fake.devices[removed]["revoked"])
        db, _ = self.store("a")
        self.assertFalse(records.item_exists(db, "notes", theirs), "the removed computer's item is cleared")
        self.assertTrue(records.item_exists(db, "notes", f"{prefix}:hello"))
        db.close()
        asker = keys.load_json(os.path.join(self.tmp, "a", "sync", "login.json"))["device_id"]
        removals = [(action, by) for action, by in self.fake.asks if action["do"] not in ("enroll", "bootstrap")]
        self.assertEqual(removals, [({"do": "remove", "device_id": removed, "clear": True}, asker)],
                         "one approval, for exactly this removal, asked with this computer's own token")


if __name__ == "__main__":
    unittest.main()
