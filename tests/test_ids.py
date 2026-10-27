import os
import stat
import tempfile
import unittest
from unittest import mock

from kb import ids
from kb.paths import Paths


class InstallIdTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = Paths.under(tmp.name)
        self.file = self.paths.config_file.parent / ids.FILE_NAME

    def test_made_once_kept_private_and_read_back(self):
        made = ids.install_id(self.paths)
        self.assertRegex(made, ids.INSTALL_ID)
        self.assertEqual(ids.install_id(self.paths), made)
        self.assertEqual(stat.S_IMODE(os.stat(self.file).st_mode), 0o600)

    def test_holds_64_random_bits_and_nothing_about_the_computer(self):
        made = {ids.new_install_id() for _ in range(200)}
        self.assertEqual(len(made), 200)
        self.assertTrue(all(ids.INSTALL_ID.match(m) for m in made))
        with mock.patch.object(ids.os, "urandom", lambda n: b"\0" * n):
            self.assertEqual(ids.new_install_id(), "c-aaaaaaaaaaaaa")

    def test_a_run_that_loses_the_race_to_make_it_keeps_the_winners(self):
        real = ids.os.link

        def another_run_lands_first(src, dst):
            with open(dst, "w") as fh:
                fh.write("c-bbbbbbbbbbbbb\n")
            real(src, dst)

        with mock.patch.object(ids.os, "link", another_run_lands_first):
            self.assertEqual(ids.install_id(self.paths), "c-bbbbbbbbbbbbb")
        self.assertEqual(ids.install_id(self.paths), "c-bbbbbbbbbbbbb")
        self.assertEqual(os.listdir(self.file.parent).count(ids.FILE_NAME), 1)
        self.assertFalse([name for name in os.listdir(self.file.parent) if name.startswith(".")])

    def test_a_file_that_holds_something_else_is_refused(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text("laptop\n")
        with self.assertRaisesRegex(ValueError, "does not hold an install id"):
            ids.install_id(self.paths)


class PrefixTest(unittest.TestCase):
    def test_local_and_remote_items_share_an_owner_but_not_a_prefix(self):
        here, there = ids.prefix("c-aaaaaaaaaaaaa"), ids.prefix("c-aaaaaaaaaaaaa", "laptop")
        self.assertEqual((here, there), ("c-aaaaaaaaaaaaa:", "c-aaaaaaaaaaaaa@laptop:"))
        self.assertFalse(there.startswith(here))
        for sid in (here + "Area/plan.md", there + "iMessage;-;chat1:2026-10-01"):
            self.assertEqual(ids.owner(sid), "c-aaaaaaaaaaaaa")
        self.assertEqual(ids.native(there + "iMessage;-;chat1:2026-10-01"), "iMessage;-;chat1:2026-10-01")

    def test_an_id_without_an_install_prefix_has_no_owner(self):
        self.assertIsNone(ids.owner("demo:q1"))
        self.assertIsNone(ids.owner("m1:Area/plan.md"))


if __name__ == "__main__":
    unittest.main()
