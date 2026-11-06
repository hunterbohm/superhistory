"""No two syncs on one store ever overlap. A sync holds an OS lock beside the store that the system drops when its
process ends, however it ends. The scheduled run that finds the store held waits briefly, then does nothing and logs
why, and its next run catches up. A command the person ran waits longer, then says another sync is running."""
import argparse
import contextlib
import io
import os
import signal
import subprocess
import sys
import unittest
from unittest import mock

import kb
from kb import cli as kbcli
from kb.paths import HOME_ENV
from kb.sync import cli, records, state
from kb.sync.client import SyncClient

from .support import World, rows


class StoreLockTest(unittest.TestCase):
    def setUp(self):
        self.world = World()
        (self.a, self.b), _ = self.world.group("computer 1", "computer 2")
        for comp in (self.a, self.b):
            comp.sync()

    def tearDown(self):
        self.world.close()

    def other(self, comp):
        """A second sync client on `comp`'s own store, as a second `superhistory sync` there would be."""
        other = SyncClient.load(comp.paths, transport=self.world.fake, clock=self.world.clock)
        self.addCleanup(other.close)
        return other

    def test_a_scheduled_run_while_another_sync_holds_the_store_does_nothing_and_logs_why(self):
        note = self.a.item("notes", "a1", "a note waiting to go out")
        cursor = state.get(self.b.db, "cursor", 0)
        with self.other(self.b)._exclusive(5), self.assertLogs("kb.sync.client", "INFO") as logs:
            report = self.b.client.run_once(wait=0.3)
        self.assertTrue(report.busy)
        self.assertIsNone(report.stalled, "a held store is not a problem to report")
        self.assertEqual(state.get(self.b.db, "cursor", 0), cursor, "the run did nothing")
        self.assertIn("another sync is running on this store", "\n".join(logs.output))
        self.a.sync()
        self.assertFalse(self.b.sync().busy, "the next run catches up")
        self.assertTrue(self.b.has("notes", note))

    def test_sync_says_another_sync_is_running_and_fails(self):
        err = io.StringIO()
        with self.other(self.b)._exclusive(5), mock.patch.object(cli, "SYNC_LOCK_WAIT", 0.3), \
                contextlib.redirect_stderr(err):
            code = cli.cmd_sync(self.b.client, argparse.Namespace())
        self.assertEqual(code, 1)
        self.assertIn("Another sync is running on this store.", err.getvalue())

    def test_a_command_waits_for_the_store_and_then_runs(self):
        other = self.other(self.b)
        with other._exclusive(5):
            with self.assertRaisesRegex(Exception, "Another sync is running on this store"):
                with mock.patch("kb.sync.client.SYNC_LOCK_WAIT", 0.3):
                    self.b.client.pull()
        self.assertIsNone(self.b.client.pull().stalled, "once the other lets go, the command runs")

    def test_a_sync_killed_while_it_holds_the_store_lets_the_next_run_go(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(kb.__file__)))
        holder = (f"import sys, time\nsys.path.insert(0, {root!r})\nfrom kb.paths import Paths\nfrom kb.sync.client import SyncClient\n"
                  f"c = SyncClient.load(Paths.under({self.b.home!r}))\n"
                  "with c._exclusive(5):\n    print('held', flush=True)\n    time.sleep(120)\n")
        proc = subprocess.Popen([sys.executable, "-c", holder], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self.addCleanup(proc.stdout.close)
        self.assertEqual(proc.stdout.readline().strip(), "held")
        self.assertTrue(self.b.client.run_once(wait=0.3).busy, "the store is held by the other process")
        proc.send_signal(signal.SIGKILL)
        proc.wait(10)
        report = self.b.client.run_once(wait=2)
        self.assertFalse(report.busy, "a killed sync leaves no lock behind")
        self.assertIsNone(report.stalled)


class InterruptTest(unittest.TestCase):
    """`superhistory sync` stopped with Ctrl-C lets the store go and leaves it as the last pack it applied left it, so the next
    sync carries on from there."""

    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        (self.a, self.b, self.c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (self.a, self.b, self.c):
            comp.sync()
        self.notes = []
        with mock.patch.object(self.b.client, "_compaction_due", return_value=False):
            for n in range(3):
                self.notes.append(self.b.item("notes", f"b{n}", f"computer 2's note {n}"))
                self.assertIsNone(self.b.sync().stalled)
        self.world.fake.revoke(self.b.client.device_id, clear=False)  # so computer 3's pull reads ahead through them

    def test_ctrl_c_while_reading_ahead_lets_the_store_go_and_keeps_it_whole(self):
        c = self.c
        before = (state.get(c.db, "cursor", 0), state.heads(c.db), rows(c.db))
        real, seen = c.client._ahead, []

        def ahead(*args):
            seen.append(real(*args))
            if len(seen) == 2:
                signal.raise_signal(signal.SIGINT)  # Ctrl-C, as the terminal sends it, partway through reading ahead
            return seen[-1]
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        err = io.StringIO()
        with mock.patch.object(cli.SyncClient, "load", return_value=c.client), \
                mock.patch.object(c.client, "_ahead", side_effect=ahead), mock.patch.dict(os.environ, {HOME_ENV: c.home}), \
                contextlib.redirect_stderr(err):
            code = kbcli.main(["sync"])
        self.assertEqual((code, len(seen)), (1, 2), "stopped where it was interrupted")
        self.assertIn("Stopped.", err.getvalue())

        other = SyncClient.load(c.paths, transport=self.world.fake, clock=self.world.clock)
        self.addCleanup(other.close)
        with other._exclusive(0):
            pass  # the store is free at once
        self.assertEqual(other.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertFalse(other.db.in_transaction)
        self.assertEqual((state.get(other.db, "cursor", 0), state.heads(other.db), rows(other.db)), before,
                         "nothing was half applied")
        report = other.run_once(wait=0)
        self.assertIsNone(report.stalled)
        self.assertTrue(all(records.item_exists(other.db, "notes", name) for name in self.notes), "the next sync carries on")


if __name__ == "__main__":
    unittest.main()
