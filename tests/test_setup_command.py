import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from kb import cli, schedule, setup


class SetupCommandTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        patcher = mock.patch.dict(os.environ, {"SUPERHISTORY_HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        with open(os.path.join(self.home, "config.toml"), "w") as fh:
            fh.write(f'[sources.notes]\npath = "{self.home}"\n')
        self.calls = []

    def run_setup(self, result, kb_path="/opt/superhistory/bin/superhistory"):
        out = io.StringIO()
        with mock.patch.object(setup, "run", lambda *a, **k: result), \
                mock.patch.object(cli.shutil, "which", lambda name: kb_path), \
                mock.patch.object(cli, "cmd_hooks", lambda paths, config, args: self.calls.append(("hooks", args.kb))), \
                mock.patch.object(schedule, "on", lambda paths, config, kb: self.calls.append(("schedule", kb)) or "on"), \
                mock.patch.object(cli, "cmd_ingest", lambda paths, config, args: self.calls.append(("ingest",)) or 0), \
                contextlib.redirect_stdout(out):
            code = cli.main(["setup"])
        return code, out.getvalue()

    def test_the_choices_run_the_hooks_the_schedule_and_the_first_read(self):
        code, _ = self.run_setup(setup.SetupResult(True, True, True, None))
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [("hooks", "/opt/superhistory/bin/superhistory"), ("schedule", "/opt/superhistory/bin/superhistory"), ("ingest",)])

    def test_declined_steps_do_not_run(self):
        self.run_setup(setup.SetupResult(False, False, False, None))
        self.assertEqual(self.calls, [])

    def test_without_kb_on_path_the_hooks_and_schedule_wait_and_the_read_still_runs(self):
        code, out = self.run_setup(setup.SetupResult(True, True, True, None), kb_path=None)
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [("ingest",)])
        self.assertIn("superhistory hooks install", out)

    def test_a_schedule_the_system_cannot_run_is_reported_not_fatal(self):
        def refuse(paths, config, kb):
            raise schedule.ScheduleError("no systemd user session")
        with mock.patch.object(schedule, "on", refuse):
            out = io.StringIO()
            with mock.patch.object(setup, "run", lambda *a, **k: setup.SetupResult(False, True, False, None)), \
                    mock.patch.object(cli.shutil, "which", lambda name: "/opt/superhistory/bin/superhistory"), \
                    contextlib.redirect_stdout(out):
                code = cli.main(["setup"])
        self.assertEqual(code, 0)
        self.assertIn("The schedule is off: no systemd user session", out.getvalue())


if __name__ == "__main__":
    unittest.main()
