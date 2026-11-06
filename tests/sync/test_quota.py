"""When the relay refuses a pack for a storage limit, the pack stays on this computer, the run says why in a plain line,
and nothing asks the relay again until the next sync."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from kb.paths import HOME_ENV
from kb.sync import relay, state

from .fake_relay import FakeRelay
from .support import EMAIL, World, connect, put_item, store_path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAID = {"quota_day": "storage allowance for today is used up", "quota_total": "storage is full"}


class QuotaLineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="superhistory-quota-test-")
        self.fake = FakeRelay()
        self.fake.sign_up(EMAIL)
        self.fake.browser_user = EMAIL
        self.url, self.server = self.fake.serve()
        self.config = os.path.join(self.tmp, "home")
        self.store = os.fspath(store_path(self.config))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        argv = [sys.executable, "-u", "-m", "kb", *args]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                              env={**os.environ, "PYTHONPATH": ROOT, HOME_ENV: self.config})
        return proc.returncode, proc.stdout + proc.stderr

    def write(self, name, text):
        db = connect(self.store)
        with open(os.path.join(self.config, "install-id"), encoding="ascii") as fh:
            prefix = fh.read().strip()
        put_item(db, "notes", f"{prefix}:{name}", "2026-10-01", name, text, {})
        db.close()

    def waiting(self):
        db = connect(self.store)
        try:
            return state.count_outbox(db)
        finally:
            db.close()

    def puts(self):
        return sum(1 for method, path, status in self.fake.log if method == "PUT" and path.startswith("/sync/packs/"))

    def test_a_refused_pack_is_kept_and_named_and_sent_once_there_is_room(self):
        rc, out = self.cli("sync", "login", "--app", self.url, "--relay", self.url, "--label", "computer 1")
        self.assertEqual(rc, 0, out)
        self.write("first", "a note that goes up before the limit")
        rc, out = self.cli("sync", "init")
        self.assertEqual(rc, 0, out)
        for code, said in SAID.items():
            with self.subTest(code=code):
                self.write(code, f"a note written while {code} holds")
                self.fake.refuse_packs = code
                before = self.puts()
                rc, out = self.cli("sync")
                self.assertEqual(rc, 1, out)
                self.assertIn(said, out)
                self.assertNotIn("507", out)
                self.assertNotIn("The relay said", out)
                self.assertEqual(self.puts() - before, 1, "the pack was asked for once, not in a loop")
                self.assertGreater(self.waiting(), 0, "the pack is still waiting on this computer")
                self.fake.refuse_packs = None
                rc, out = self.cli("sync")
                self.assertEqual(rc, 0, out)
                self.assertEqual(self.waiting(), 0)

    def full(self):
        rc, out = self.cli("sync", "login", "--app", self.url, "--relay", self.url, "--label", "computer 1")
        self.assertEqual(rc, 0, out)
        rc, out = self.cli("sync", "init")
        self.assertEqual(rc, 0, out)
        self.write("full", "a note written while the account is full")
        self.fake.refuse_packs = "quota_total"
        rc, out = self.cli("sync")
        self.assertEqual(rc, 1, out)
        return out

    def test_a_full_account_is_told_which_computer_makes_room_and_whom_to_write_to(self):
        out = self.full()
        self.assertIn("superhistory sync status", out)
        self.assertIn("0 records waiting", out)
        self.assertIn("superhistory sync compact", out, "a computer with nothing waiting compacts when told to, due or not")
        self.assertIn("support@example.com", out, "the address the app names, saved at login")

    def test_without_an_address_from_the_app_the_line_still_names_support(self):
        for answer in (None, {"support_email": "write to\u001b[2Jus"}, {"support_email": ["support@example.com"]}):
            with self.subTest(answer=answer):
                shutil.rmtree(self.config, ignore_errors=True)
                if os.path.exists(self.store):
                    os.remove(self.store)
                self.fake = FakeRelay()
                self.fake.sign_up(EMAIL)
                self.fake.browser_user = EMAIL
                self.server.shutdown()
                self.server.server_close()
                self.url, self.server = self.fake.serve()
                self.fake.service_answer = answer
                out = self.full()
                self.assertIn("contact Superhistory support", out)
                self.assertNotIn("\u001b", out)

    def test_sync_compact_compacts_when_no_compaction_is_due(self):
        rc, out = self.cli("sync", "login", "--app", self.url, "--relay", self.url, "--label", "computer 1")
        self.assertEqual(rc, 0, out)
        self.write("first", "a note in the first snapshot")
        rc, out = self.cli("sync", "init")
        self.assertEqual(rc, 0, out)
        rc, out = self.cli("sync")
        self.assertEqual((rc, "Compacted." in out), (0, False), "no compaction is due yet")
        before = self.fake.group(EMAIL).snapshot
        rc, out = self.cli("sync", "compact")
        self.assertEqual(rc, 0, out)
        self.assertIn("Compacted.", out)
        self.assertNotEqual(self.fake.group(EMAIL).snapshot, before)

    def test_sync_compact_sends_an_unsent_edit_first(self):
        rc, out = self.cli("sync", "login", "--app", self.url, "--relay", self.url, "--label", "computer 1")
        self.assertEqual(rc, 0, out)
        self.write("note", "the first text")
        self.assertEqual(self.cli("sync", "init")[0], 0)
        self.write("note", "an edit not sent yet")
        rc, out = self.cli("sync", "compact")
        self.assertEqual(rc, 0, out)
        self.assertIn("Compacted.", out)
        rc, out = self.cli("sync")
        self.assertEqual(rc, 0, out)
        self.assertIn("sent 0 records", out, "the edit went up before the compaction")


class QuotaCompactionTest(unittest.TestCase):
    def setUp(self):
        self.world = World()

    def tearDown(self):
        self.world.close()

    def test_a_snapshot_never_holds_an_edit_this_computer_has_not_sent(self):
        # Review round 3 (P1): a snapshot of an edit never sent, under the item's old version, left computers different.
        (a, b), _ = self.world.group("computer 1", "computer 2")
        a.item("notes", "x", "original")
        a.sync()
        b.sync()
        a.item("notes", "x", "temporary edit")
        self.assertFalse(a.client.compact(force=True), "an edit not sent yet waits for a sync")
        a.sync()
        self.assertTrue(a.client.compact(force=True))
        c = self.world.computer("computer 3")
        self.world.pair(a, c)
        c.sync()
        b.sync()
        for computer in (b, c):
            self.assertEqual("".join(computer.text("notes", a.key("x"))["rows"]), "temporary edit", computer.name)

    def test_a_refused_snapshot_part_surfaces_the_code_and_changes_nothing(self):
        (a,), _ = self.world.group("computer 1")
        a.item("notes", "x", "a note worth a snapshot")
        a.sync()
        group = self.world.fake.group("person@example.com")
        before = (group.snapshot, [p["seq"] for p in group.packs])
        self.world.fake.refuse_packs = "quota_total"
        with self.assertRaises(relay.RelayError) as caught:
            a.client.compact(force=True)
        self.assertEqual((caught.exception.status, caught.exception.code), (507, "quota_total"))
        self.assertEqual((group.snapshot, [p["seq"] for p in group.packs]), before)
        self.world.fake.refuse_packs = None
        self.assertTrue(a.client.compact(force=True))
