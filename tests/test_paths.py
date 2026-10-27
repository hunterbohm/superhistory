import unittest
from pathlib import Path

from kb import NAME
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


if __name__ == "__main__":
    unittest.main()
