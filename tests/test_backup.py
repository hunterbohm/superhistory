import contextlib
import hashlib
import io
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

from kb import backup, cli, ids, search, store, trial
from kb.paths import Paths

PASSPHRASE = "correct horse battery staple"
CONFIG = 'machine = "studio"\n'
_month = {}


def month_store():
    """The made-up month in a store, loaded once: masking its items takes longer than anything a test checks."""
    if not _month:
        tmp = tempfile.TemporaryDirectory()
        unittest.addModuleCleanup(tmp.cleanup)
        db = store.connect(Path(tmp.name) / "kb.sqlite")
        _month["items"] = trial.load(db, trial.month_dir())
        db.close()
        _month["path"] = Path(tmp.name) / "kb.sqlite"
    return _month["path"], _month["items"]


class BackupCase(unittest.TestCase):
    """A made-up month in a store under a temp folder, with a config and an install id beside it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.paths = Paths.under(self.tmp / "home")
        patch = mock.patch.object(backup, "ITERATIONS", 1000)  # the real count is checked once, below
        patch.start()
        self.addCleanup(patch.stop)
        template, self.items = month_store()
        self.paths.data_dir.mkdir(parents=True)
        shutil.copy(template, self.paths.db)
        self.paths.config_file.write_text(CONFIG, encoding="utf-8")
        self.install = ids.install_id(self.paths)
        self.secret = self.tmp / "passphrase"
        self.secret.write_text(PASSPHRASE + "\n", encoding="utf-8")
        self.file = self.tmp / "out" / "month.backup"
        self.file.parent.mkdir()

    def make(self, **kwargs):
        return backup.make(self.paths, self.file, PASSPHRASE.encode(), **kwargs)

    def remove_everything(self):
        for path in (self.paths.db, *(Path(f"{self.paths.db}{s}") for s in ("-wal", "-shm")), self.paths.config_file,
                     self.paths.config_file.parent / ids.FILE_NAME):
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)

    def restore(self, agree=lambda existing, manifest: True, passphrase=PASSPHRASE.encode()):
        return backup.restore(self.paths, self.file, passphrase, agree)

    def hits(self, query):
        db = store.connect(self.paths.db, readonly=True)
        try:
            return [(h["source"], h["source_id"], h["title"]) for h in search.search(db, query, limit=10)]
        finally:
            db.close()

    def has_item(self, source_id):
        with contextlib.closing(sqlite3.connect(self.paths.db)) as db:
            return db.execute("SELECT count(*) FROM docs WHERE source_id=?", (source_id,)).fetchone()[0] > 0

    def tree(self):
        return sorted(str(p.relative_to(self.tmp)) for p in self.tmp.rglob("*") if p.is_file() and p.name != "kb.lock")


class RoundTripTest(BackupCase):
    def test_a_restored_month_is_searched_the_same(self):
        asked = ["sandbox key", "Pelican launch date", "invoice", "checkout"]
        before = {q: self.hits(q) for q in asked}
        self.assertTrue(any(before.values()))
        made = self.make()
        self.assertEqual(made.items, self.items)
        self.remove_everything()
        done = self.restore()
        self.assertEqual(done.manifest.items, self.items)
        self.assertEqual({q: self.hits(q) for q in asked}, before)
        self.assertEqual(ids.install_id(self.paths), self.install)
        self.assertEqual(self.paths.config_file.read_text(encoding="utf-8"), CONFIG)

    def test_every_row_comes_back_as_it_was(self):
        def rows():
            db = sqlite3.connect(self.paths.db)
            try:
                return db.execute("SELECT id, kind, source, source_id, ts, title, content, metadata, masked FROM docs "
                                  "ORDER BY id").fetchall()
            finally:
                db.close()
        before = rows()
        self.make()
        self.remove_everything()
        self.restore()
        self.assertEqual(rows(), before)

    def test_the_real_iteration_count_is_written_to_the_file(self):
        with mock.patch.object(backup, "ITERATIONS", 600_000):
            self.make()
        head = self.file.read_bytes()[:backup.HEADER.size]
        magic, number, iterations, _ = backup.HEADER.unpack(head)
        self.assertEqual((magic, number, iterations), (b"SUPERHISTORY BACKUP\n", 1, 600_000))
        self.remove_everything()
        self.restore()
        self.assertTrue(self.hits("sandbox key"))

    def test_the_file_is_readable_by_hand_with_openssl_gunzip_and_tar(self):
        self.make()
        data = self.file.read_bytes()[backup.BODY:]
        read, write = os.pipe()
        os.write(write, PASSPHRASE.encode() + b"\n")
        os.close(write)
        work = self.tmp / "by-hand"
        work.mkdir()
        try:
            opened = subprocess.run(["openssl", "enc", "-d", "-aes-256-ctr", "-pbkdf2", "-md", "sha256", "-iter",
                                     "1000", "-pass", f"fd:{read}"], input=data, capture_output=True, pass_fds=(read,))
        finally:
            os.close(read)
        self.assertEqual(opened.returncode, 0, opened.stderr)
        unpacked = subprocess.run(["tar", "xzf", "-", "-C", str(work)], input=opened.stdout, capture_output=True)
        self.assertEqual(unpacked.returncode, 0, unpacked.stderr)
        self.assertEqual((work / "config.toml").read_text(encoding="utf-8"), CONFIG)
        with contextlib.closing(sqlite3.connect(work / "store.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(DISTINCT source_id) FROM docs").fetchone()[0], self.items)

    def test_a_passphrase_typed_two_ways_is_the_same_passphrase(self):
        word = "café au lait, très bien"
        self.assertEqual(backup.passphrase_bytes(unicodedata.normalize("NFD", word)),
                         backup.passphrase_bytes(unicodedata.normalize("NFC", word)))

    def test_a_store_with_no_config_or_install_id_backs_up_and_comes_back(self):
        os.remove(self.paths.config_file)
        os.remove(self.paths.config_file.parent / ids.FILE_NAME)
        self.make()
        self.remove_everything()
        self.restore()
        self.assertTrue(self.hits("sandbox key"))
        self.assertFalse(self.paths.config_file.exists())


class SecrecyTest(BackupCase):
    def test_the_file_holds_no_readable_text_of_the_store(self):
        db = sqlite3.connect(self.paths.db)
        words = [r[0] for r in db.execute("SELECT title FROM docs WHERE kind='doc' LIMIT 40")]
        words += ["Pelican", "sandbox", "SQLite format", "machine = ", self.install]
        db.close()
        self.make()
        data = self.file.read_bytes()
        for word in words:
            self.assertNotIn(word.encode(), data, word)
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)

    def test_two_backups_of_one_store_differ(self):
        self.make()
        first = self.file.read_bytes()
        self.make(overwrite=True)
        self.assertNotEqual(first, self.file.read_bytes())

    def test_the_passphrase_is_never_on_a_command_line(self):
        started = []
        real = subprocess.Popen

        def watch(args, *a, **kw):
            started.append(list(args))
            return real(args, *a, **kw)
        with mock.patch.object(subprocess, "Popen", watch):
            self.make()
            self.remove_everything()
            self.restore()
        self.assertTrue(started)
        for args in started:
            self.assertNotIn(PASSPHRASE, " ".join(args))
            self.assertRegex(" ".join(args), r"-pass fd:\d+")

    def test_no_plain_copy_is_left_behind(self):
        self.make()
        left = [p for p in self.tree() if p.endswith(".sqlite") or "partial" in p or ".backup-" in p]
        self.assertEqual(left, [f"home/data/kb.sqlite"])
        self.assertEqual([p.name for p in (self.tmp / "home" / "data").iterdir() if p.name.startswith(".backup-")], [])


class RefusalTest(BackupCase):
    def assertUntouched(self, before):
        self.assertEqual(self.tree(), before)

    def test_a_wrong_passphrase_says_so_and_changes_nothing(self):
        self.make()
        self.remove_everything()
        before = self.tree()
        with self.assertRaisesRegex(backup.BackupError, "does not open.*lost cannot be got back"):
            self.restore(passphrase=b"not the passphrase at all")
        self.assertUntouched(before)

    def test_a_wrong_passphrase_leaves_an_existing_store_as_it_was(self):
        self.make()
        bytes_before = self.paths.db.read_bytes()
        with self.assertRaises(backup.BackupError):
            self.restore(passphrase=b"not the passphrase at all")
        self.assertEqual(self.paths.db.read_bytes(), bytes_before)

    def test_a_damaged_body_is_named_as_damaged(self):
        self.make()
        data = bytearray(self.file.read_bytes())
        data[len(data) // 2] ^= 0xFF
        self.file.write_bytes(bytes(data))
        before = self.tree()
        with self.assertRaisesRegex(backup.BackupError, "damaged or cut short"):
            self.restore()
        self.assertUntouched(before)

    def test_a_file_cut_short_fails(self):
        self.make()
        data = self.file.read_bytes()
        for keep in (len(data) - 1, len(data) // 2, backup.BODY):
            self.file.write_bytes(data[:keep])
            with self.assertRaisesRegex(backup.BackupError, "damaged or cut short"):
                self.restore()
        self.file.write_bytes(data[:50])
        with self.assertRaisesRegex(backup.BackupError, "cut short"):
            self.restore()

    def test_a_damaged_start_fails_without_a_long_wait(self):
        self.make()
        data = bytearray(self.file.read_bytes())
        data[24:28] = (4_000_000_000).to_bytes(4, "big")
        self.file.write_bytes(bytes(data))
        with self.assertRaisesRegex(backup.BackupError, "damaged"):
            self.restore()

    def test_a_file_that_is_no_backup_is_said_to_be_none(self):
        self.file.write_bytes(b"just some text, long enough to have a header " * 5)
        with self.assertRaisesRegex(backup.BackupError, "not a Superhistory backup"):
            self.restore()
        self.file.write_bytes(b"")
        with self.assertRaisesRegex(backup.BackupError, "not a Superhistory backup"):
            self.restore()

    def test_a_newer_format_asks_for_a_newer_superhistory(self):
        self.make()
        data = bytearray(self.file.read_bytes())
        data[20:24] = (2).to_bytes(4, "big")
        self.file.write_bytes(bytes(data))
        with self.assertRaisesRegex(backup.BackupError, "format 2.*newer Superhistory"):
            self.restore()

    def test_a_missing_file_is_named(self):
        with self.assertRaisesRegex(backup.BackupError, "there is no file"):
            self.restore()

    def test_a_short_passphrase_is_refused_for_a_new_backup(self):
        with self.assertRaisesRegex(backup.BackupError, "at least 12"):
            backup.choose_passphrase("short one")

    def test_a_passphrase_with_a_line_break_is_refused(self):
        with self.assertRaises(backup.BackupError):
            backup.passphrase_bytes("two\nlines of words")

    def test_a_backup_does_not_replace_a_file_without_force(self):
        self.make()
        first = self.file.read_bytes()
        with self.assertRaisesRegex(backup.BackupError, "--force"):
            self.make()
        self.assertEqual(self.file.read_bytes(), first)

    def test_a_backup_that_fails_leaves_the_earlier_file_and_no_partial_one(self):
        self.make()
        first = self.file.read_bytes()
        with mock.patch.object(backup, "read", side_effect=backup.BackupError("no")):
            with self.assertRaises(backup.BackupError):
                self.make(overwrite=True)
        self.assertEqual(self.file.read_bytes(), first)
        self.assertEqual([p.name for p in self.file.parent.iterdir()], [self.file.name])

    def test_force_cannot_aim_a_backup_at_the_live_files(self):
        link = self.tmp / "link"
        os.symlink(self.paths.config_file, link)
        live = [self.paths.db, Path(f"{self.paths.db}-wal"), self.paths.config_file,
                self.paths.config_file.parent / ids.FILE_NAME, link]
        before = {p: p.read_bytes() for p in live if p.exists()}
        for dest in live:
            with self.assertRaisesRegex(backup.BackupError, "live"):
                backup.make(self.paths, dest, PASSPHRASE.encode(), overwrite=True)
        self.assertEqual({p: p.read_bytes() for p in live if p.exists()}, before)

    def test_no_store_means_no_backup(self):
        self.remove_everything()
        with self.assertRaisesRegex(backup.BackupError, "nothing to back up"):
            self.make()
        self.assertFalse(self.file.exists())

    def test_a_damaged_store_is_not_backed_up(self):
        raw = bytearray(self.paths.db.read_bytes())
        for at in range(8192, len(raw) - 4096, 40000):
            raw[at:at + 64] = b"\xff" * 64
        self.paths.db.write_bytes(bytes(raw))
        with self.assertRaises(backup.BackupError):
            self.make()
        self.assertFalse(self.file.exists())

    def test_no_openssl_is_said_plainly(self):
        with mock.patch.object(shutil, "which", return_value=None):
            with self.assertRaisesRegex(backup.BackupError, "`openssl` command, which was not found"):
                self.make()

    def test_an_openssl_too_old_for_pbkdf2_is_said_plainly(self):
        failing = mock.Mock(side_effect=backup.BackupError("exit code 1"))
        with mock.patch.object(backup._Openssl, "finish", failing):
            with self.assertRaisesRegex(backup.BackupError, "OpenSSL 1.1.1 or LibreSSL 3.1"):
                self.make()


class ReplaceTest(BackupCase):
    def setUp(self):
        super().setUp()
        self.make()
        db = store.connect(self.paths.db)
        store.store_item(db, "notes", "x:new", "2026-10-30T10:00:00Z", "A newer note", "written after the backup", {})
        db.commit()
        db.close()
        self.newer = self.paths.db.read_bytes()

    def test_a_store_that_is_there_is_replaced_only_when_the_user_agrees(self):
        asked = []

        def decline(existing, manifest):
            asked.append((existing, manifest.items))
            return False
        self.assertIsNone(self.restore(decline))
        self.assertEqual(self.paths.db.read_bytes(), self.newer)
        self.assertEqual(len(asked), 1)
        self.assertTrue(any("knowledge base" in line and f"{self.items + 1:,} items" in line for line in asked[0][0]))
        self.assertEqual([p for p in self.tree() if "restore" in p], [])

    def test_agreeing_replaces_it_and_leaves_nothing_aside(self):
        self.restore()
        self.assertFalse(self.has_item("x:new"))
        self.assertTrue(self.hits("sandbox key"))
        self.assertEqual([p for p in self.tree() if "restore" in p or "before" in p], [])

    def test_a_restore_that_does_not_check_out_puts_the_old_store_back(self):
        config_before = self.paths.config_file.read_bytes()
        with mock.patch.object(backup, "_check_store", side_effect=[None, backup.BackupError("not sound")]):
            with self.assertRaisesRegex(backup.BackupError, "not sound"):
                self.restore()
        self.assertEqual(self.paths.db.read_bytes(), self.newer)
        self.assertEqual(self.paths.config_file.read_bytes(), config_before)
        self.assertEqual([p for p in self.tree() if "restore" in p or "before" in p], [])

    def test_the_old_store_is_kept_aside_until_the_restore_has_been_checked(self):
        seen = []

        def check(path, manifest, whole):
            if not whole:
                seen.append(sorted(p.name for p in self.paths.db.parent.iterdir()))

        with mock.patch.object(backup, "_check_store", check):
            self.restore()
        self.assertIn("kb.sqlite.before-restore", seen[0])

    def test_an_interrupted_restore_puts_the_old_store_back(self):
        with mock.patch.object(backup, "_check_store", side_effect=[None, KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):
                self.restore()
        self.assertEqual(self.paths.db.read_bytes(), self.newer)

    def test_a_stop_before_anything_moved_leaves_the_old_wal_file_alone(self):
        db = store.connect(self.paths.db)
        self.addCleanup(db.close)
        store.store_item(db, "notes", "x:late", "2026-10-31T10:00:00Z", "A late note", "only in the log file", {})
        db.commit()
        wal = Path(f"{self.paths.db}-wal")
        before = wal.read_bytes()
        self.assertTrue(before)
        with mock.patch.object(backup.os, "replace", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.restore()
        self.assertEqual(wal.read_bytes(), before)
        self.assertEqual(self.paths.db.read_bytes(), self.newer)

    def test_a_backup_named_like_the_restore_staging_file_is_not_deleted_by_the_restore(self):
        for name in (f"{self.paths.db}.restore", f"{self.paths.config_file}.restore"):
            self.file = Path(name)
            self.make()
            kept = self.file.read_bytes()
            self.restore()
            self.assertEqual(self.file.read_bytes(), kept)
            self.assertTrue(self.hits("sandbox key"))

    def test_a_store_left_aside_by_an_earlier_restore_stops_the_next(self):
        aside = Path(f"{self.paths.db}.before-restore")
        aside.write_bytes(b"someone's file")
        with self.assertRaisesRegex(backup.BackupError, "earlier restore left"):
            self.restore()
        self.assertEqual(aside.read_bytes(), b"someone's file")
        self.assertEqual(self.paths.db.read_bytes(), self.newer)

    def test_a_running_ingest_stops_the_restore(self):
        with store.exclusive(self.paths.lock) as held:
            self.assertTrue(held)
            with self.assertRaisesRegex(backup.BackupError, "an ingest is running"):
                self.restore()
        self.assertEqual(self.paths.db.read_bytes(), self.newer)

    def test_a_wal_file_beside_the_old_store_is_not_read_into_the_restored_one(self):
        db = store.connect(self.paths.db)
        store.store_item(db, "notes", "x:late", "2026-10-31T10:00:00Z", "A late note", "only in the log file", {})
        db.commit()  # in the WAL file until a checkpoint
        self.assertTrue(os.path.exists(f"{self.paths.db}-wal"))
        self.restore()
        db.close()
        self.assertFalse(self.has_item("x:late"))


class DuringIngestTest(BackupCase):
    def test_a_backup_taken_while_rows_are_written_is_sound(self):
        writing, stop = threading.Event(), threading.Event()
        written = []

        def ingest():
            db = store.connect(self.paths.db)
            try:
                n = 0
                while not stop.is_set():
                    store.store_item(db, "notes", f"w:{n}", "2026-10-30T10:00:00Z", f"Late note {n}",
                                     f"words written during the backup {n} " * 50, {})
                    db.commit()
                    n += 1
                    written.append(n)
                    writing.set()
            finally:
                db.close()

        thread = threading.Thread(target=ingest)
        thread.start()
        try:
            self.assertTrue(writing.wait(10))
            made = [self.make(overwrite=True) for _ in range(3)]
        finally:
            stop.set()
            thread.join()
        self.remove_everything()
        self.restore()
        db = sqlite3.connect(self.paths.db)
        try:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            db.execute("INSERT INTO docs_fts(docs_fts) VALUES('integrity-check')")
            items = db.execute("SELECT count(DISTINCT source_id) FROM docs").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(items, made[-1].items)
        self.assertTrue(self.items <= items <= self.items + len(written))


class CommandTest(BackupCase):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(Paths, "for_user", return_value=self.paths)
        patch.start()
        self.addCleanup(patch.stop)

    def run_cli(self, *argv, answer=None, tty=False):
        out, err = io.StringIO(), io.StringIO()
        stdin = mock.Mock(isatty=lambda: tty)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(cli.sys, "stdin", stdin):
            if answer is None:
                code = cli.main(list(argv))
            else:
                with mock.patch("builtins.input", return_value=answer):
                    code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_backup_then_restore_through_the_commands(self):
        code, out, err = self.run_cli("backup", str(self.file), "--passphrase-file", str(self.secret))
        self.assertEqual((code, err), (0, ""), err)
        self.assertIn(f"{self.items:,} items", out)
        self.assertIn("does not keep the passphrase", out)
        self.remove_everything()
        code, out, err = self.run_cli("restore", str(self.file), "--passphrase-file", str(self.secret))
        self.assertEqual((code, err), (0, ""), err)
        self.assertTrue(self.hits("sandbox key"))

    def test_a_folder_gets_a_dated_file(self):
        code, out, err = self.run_cli("backup", str(self.file.parent), "--passphrase-file", str(self.secret))
        self.assertEqual(code, 0, err)
        names = [p.name for p in self.file.parent.iterdir()]
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], r"^superhistory-\d{4}-\d\d-\d\d\.backup$")

    def test_a_wrong_passphrase_exits_one_with_a_plain_message(self):
        self.run_cli("backup", str(self.file), "--passphrase-file", str(self.secret))
        other = self.tmp / "other"
        other.write_text("a different passphrase entirely", encoding="utf-8")
        before = self.paths.db.read_bytes()
        code, out, err = self.run_cli("restore", str(self.file), "--passphrase-file", str(other), "--yes")
        self.assertEqual(code, 1)
        self.assertIn("does not open", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.paths.db.read_bytes(), before)

    def test_restore_over_a_store_without_a_terminal_or_yes_is_refused_up_front(self):
        self.run_cli("backup", str(self.file), "--passphrase-file", str(self.secret))
        code, _, err = self.run_cli("restore", str(self.file), "--passphrase-file", str(self.secret))
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)

    def test_the_prompt_says_what_is_replaced_and_no_leaves_it(self):
        self.run_cli("backup", str(self.file), "--passphrase-file", str(self.secret))
        before = self.paths.db.read_bytes()
        code, out, _ = self.run_cli("restore", str(self.file), "--passphrase-file", str(self.secret), answer="n",
                                    tty=True)
        self.assertEqual(code, 0)
        self.assertIn("Nothing restored.", out)
        self.assertIn(f"{self.items:,} items", out)
        self.assertIn(str(self.paths.db), out)
        self.assertEqual(self.paths.db.read_bytes(), before)
        code, out, _ = self.run_cli("restore", str(self.file), "--passphrase-file", str(self.secret), answer="y",
                                    tty=True)
        self.assertEqual(code, 0)
        self.assertIn("Restored", out)

    def test_the_passphrase_is_asked_for_twice_on_a_terminal_and_must_match(self):
        answers = iter([PASSPHRASE, PASSPHRASE + "!"])
        with mock.patch.object(backup.getpass, "getpass", lambda prompt: next(answers)):
            code, _, err = self.run_cli("backup", str(self.file), tty=True)
        self.assertEqual(code, 1)
        self.assertIn("two passphrases differ", err)
        self.assertFalse(self.file.exists())

    def test_without_a_terminal_or_a_file_it_says_how_to_give_the_passphrase(self):
        code, _, err = self.run_cli("backup", str(self.file))
        self.assertEqual(code, 1)
        self.assertIn("--passphrase-file", err)

    def test_a_config_that_does_not_read_does_not_stop_a_backup_or_a_restore(self):
        self.paths.config_file.write_text("this is [not toml", encoding="utf-8")
        code, _, err = self.run_cli("backup", str(self.file), "--passphrase-file", str(self.secret))
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli("restore", str(self.file), "--passphrase-file", str(self.secret), "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.paths.config_file.read_text(encoding="utf-8"), "this is [not toml")
        code, _, _ = self.run_cli("stats")
        self.assertEqual(code, 2, "other commands still stop on a config that does not read")

    def test_the_made_up_months_store_is_not_backed_up(self):
        code, _, err = self.run_cli("--try", "backup", str(self.file), "--passphrase-file", str(self.secret))
        self.assertEqual(code, 1)
        self.assertIn("made-up month", err)

    def test_restore_creates_the_folders_of_a_computer_with_nothing_yet(self):
        self.run_cli("backup", str(self.file), "--passphrase-file", str(self.secret))
        shutil.rmtree(self.tmp / "home")
        code, _, err = self.run_cli("restore", str(self.file), "--passphrase-file", str(self.secret))
        self.assertEqual(code, 0, err)
        self.assertTrue(self.hits("sandbox key"))
        self.assertEqual(ids.install_id(self.paths), self.install)

    def test_the_passphrase_can_come_from_standard_input(self):
        out = io.StringIO(PASSPHRASE + "\n")
        out.isatty = lambda: False
        with mock.patch.object(cli.sys, "stdin", out), contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["backup", str(self.file), "--passphrase-file", "-"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
