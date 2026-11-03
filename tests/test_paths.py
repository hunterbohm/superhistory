import tempfile
import unittest
from pathlib import Path

from kb import LEGACY_NAME, NAME, store
from kb.paths import Paths


class PathsTest(unittest.TestCase):
    def test_kb_home_puts_everything_in_one_folder(self):
        paths = Paths.for_user({"KB_HOME": "/scratch/kb"}, "darwin", home="/home/sam")
        for where in (paths.config_file, paths.db, paths.lock, paths.log("ingest"), paths.seen_dir):
            self.assertTrue(str(where).startswith("/scratch/kb/"), where)

    def test_mac_keeps_config_in_dot_config_and_data_in_library(self):
        paths = Paths.for_user({}, "darwin", home="/Users/sam")
        self.assertEqual(paths.config_file, Path(f"/Users/sam/.config/{NAME.lower()}/config.toml"))
        self.assertEqual(paths.db.parent, Path(f"/Users/sam/Library/Application Support/{NAME}"))
        self.assertEqual(paths.log_dir, Path(f"/Users/sam/Library/Logs/{NAME}"))
        self.assertEqual(paths.cache_dir, Path(f"/Users/sam/Library/Caches/{NAME}"))

    def test_linux_follows_xdg_and_ignores_relative_values(self):
        env = {"XDG_DATA_HOME": "/xdg/data", "XDG_CACHE_HOME": "relative/cache"}
        paths = Paths.for_user(env, "linux", home="/home/sam")
        slug = NAME.lower()
        self.assertEqual(paths.config_file, Path(f"/home/sam/.config/{slug}/config.toml"))
        self.assertEqual(paths.db.parent, Path(f"/xdg/data/{slug}"))
        self.assertEqual(paths.log_dir, Path(f"/home/sam/.local/state/{slug}/logs"))
        self.assertEqual(paths.cache_dir, Path(f"/home/sam/.cache/{slug}"))

    def test_try_keeps_the_config_and_moves_everything_else(self):
        paths = Paths.under("/scratch/kb")
        trial = paths.for_try()
        self.assertEqual(trial.config_file, paths.config_file)
        self.assertNotEqual(trial.db, paths.db)
        self.assertNotEqual(trial.lock, paths.lock)
        self.assertNotEqual(trial.seen_dir, paths.seen_dir)
        self.assertNotEqual(trial.log("check"), paths.log("check"))


class LegacyFoldersTest(unittest.TestCase):
    """An install written while the product was called Varve keeps its folders, and a new install gets the new name."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def written_under(self, name, platform, *, db=True, config=True):
        paths = Paths.for_user({}, platform, home=self.home, name=name)
        paths.config_file.parent.mkdir(parents=True)
        paths.data_dir.mkdir(parents=True)
        if config:
            paths.config_file.write_text("machine = \"desk\"\n", encoding="utf-8")
        if db:
            conn = store.connect(paths.db)
            store.store_item(conn, "notes", "desk:old.md", "2026-10-03T10:00:00Z", "old note", "the heron plan", {})
            conn.commit()
            conn.close()
        return paths

    def test_the_old_and_new_names_differ(self):
        self.assertEqual((NAME, LEGACY_NAME), ("Superhistory", "Varve"))

    def test_a_linux_install_in_the_varve_folders_keeps_using_them(self):
        old = self.written_under(LEGACY_NAME, "linux")
        now = Paths.for_user({}, "linux", home=self.home)
        self.assertEqual(now, old)
        self.assertEqual(now.config_file, self.home / ".config" / "varve" / "config.toml")
        self.assertEqual(now.db, self.home / ".local" / "share" / "varve" / "kb.sqlite")
        conn = store.connect(now.db)
        self.addCleanup(conn.close)
        self.assertEqual([r[0] for r in conn.execute("SELECT source_id FROM docs")], ["desk:old.md"])

    def test_a_mac_install_in_the_varve_folders_keeps_using_them(self):
        old = self.written_under(LEGACY_NAME, "darwin")
        now = Paths.for_user({}, "darwin", home=self.home)
        self.assertEqual(now, old)
        self.assertEqual(now.db.parent, self.home / "Library" / "Application Support" / "Varve")
        self.assertEqual(now.log_dir, self.home / "Library" / "Logs" / "Varve")
        self.assertEqual(now.cache_dir, self.home / "Library" / "Caches" / "Varve")

    def test_an_old_database_alone_is_enough_to_keep_the_old_folders(self):
        old = self.written_under(LEGACY_NAME, "linux", config=False)
        self.assertEqual(Paths.for_user({}, "linux", home=self.home), old)

    def test_an_old_config_alone_is_enough_to_keep_the_old_folders(self):
        old = self.written_under(LEGACY_NAME, "linux", db=False)
        self.assertEqual(Paths.for_user({}, "linux", home=self.home), old)

    def test_a_new_install_uses_the_new_name(self):
        now = Paths.for_user({}, "linux", home=self.home)
        self.assertEqual(now.config_file, self.home / ".config" / "superhistory" / "config.toml")
        self.assertEqual(now.db.parent, self.home / ".local" / "share" / "superhistory")
        mac = Paths.for_user({}, "darwin", home=self.home)
        self.assertEqual(mac.db.parent, self.home / "Library" / "Application Support" / "Superhistory")

    def test_empty_old_folders_do_not_hide_a_new_install(self):
        Paths.for_user({}, "linux", home=self.home, name=LEGACY_NAME).data_dir.mkdir(parents=True)
        now = Paths.for_user({}, "linux", home=self.home)
        self.assertEqual(now.data_dir, self.home / ".local" / "share" / "superhistory")

    def test_a_store_under_the_new_name_wins_and_the_old_one_is_left_alone(self):
        old = self.written_under(LEGACY_NAME, "linux")
        new = self.written_under(NAME, "linux")
        self.assertEqual(Paths.for_user({}, "linux", home=self.home), new)
        self.assertTrue(old.db.exists())

    def test_kb_home_and_try_are_unaffected(self):
        self.written_under(LEGACY_NAME, "linux")
        paths = Paths.for_user({"KB_HOME": str(self.home / "trial")}, "linux", home=self.home)
        self.assertEqual(paths, Paths.under(self.home / "trial"))


if __name__ == "__main__":
    unittest.main()
