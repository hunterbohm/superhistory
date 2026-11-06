import os
import stat
import subprocess
import sys
import unittest

from kb import cli, ids
from kb.paths import HOME_ENV, Paths
from kb.sync import APP_URL, RELAY_URL

from .network import Network, run_cli
from .support import World

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def subcommands():
    parser = cli.parser()
    return next(a for a in parser._actions if a.dest == "cmd").choices


class CommandTest(unittest.TestCase):
    def test_sync_is_one_subcommand_with_the_documented_actions(self):
        choices = subcommands()
        self.assertIn("sync", choices)
        actions = next(a for a in choices["sync"]._actions if a.dest == "action").choices
        self.assertEqual(sorted(actions), ["add-device", "compact", "doctor", "init", "join", "login", "recover",
                                           "recovery-code", "remove", "status"])

    def test_cloud_commands_did_not_come_along(self):
        for name in ("push", "cloud"):
            self.assertNotIn(name, subcommands())
        actions = next(a for a in subcommands()["sync"]._actions if a.dest == "action").choices
        self.assertFalse({"push", "cloud", "context"} & set(actions))

    def test_default_addresses(self):
        args = cli.parser().parse_args(["sync", "login"])
        self.assertEqual((args.app, args.relay), ("https://app.superhistory.dev", "https://sync.superhistory.dev"))
        self.assertEqual((APP_URL, RELAY_URL), (args.app, args.relay))

    def test_importing_the_command_line_does_not_load_the_client(self):
        code = ("import sys, kb.cli; kb.cli.parser(); "
                "heavy = [m for m in ('kb.sync.client', 'kb.sync.cli', 'kb.sync.relay', 'kb.sync.login') if m in sys.modules]; "
                "assert not heavy, heavy")
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
                              env={**os.environ, "PYTHONPATH": ROOT}, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_mistyped_value_is_not_quoted_back(self):
        code, out = run_cli("/nonexistent-home", "sync", "remove", "--bogus=hunter2-the-password")
        self.assertEqual(code, 2)
        self.assertNotIn("hunter2-the-password", out)

    def test_not_signed_in_names_the_full_command(self):
        world = World()
        self.addCleanup(__import__("shutil").rmtree, world.tmp, True)
        net = Network()
        with net.wired():
            code, out = run_cli(os.path.join(world.tmp, "empty"), "sync", "status")
        self.assertNotEqual(code, 0)
        self.assertIn("superhistory sync login", out)


class OneStoreTest(unittest.TestCase):
    def test_sync_opens_the_knowledge_base_and_keeps_its_files_beside_the_config(self):
        world = World()
        self.addCleanup(__import__("shutil").rmtree, world.tmp, True)
        c = world.computer("laptop")
        paths = Paths.under(c.home)
        files = c.db.execute("PRAGMA database_list").fetchall()
        self.assertEqual([os.path.realpath(f[2]) for f in files if f[1] == "main"], [os.path.realpath(paths.db)])
        self.assertEqual(c.prefix, ids.install_id(paths))
        folder = paths.config_file.parent / "sync"
        self.assertTrue(folder.is_dir())
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        self.assertFalse([n for n in os.listdir(paths.config_file.parent) if n.endswith(".db") and n != paths.db.name])


if __name__ == "__main__":
    unittest.main()
