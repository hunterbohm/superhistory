import os
import plistlib
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kb import LEGACY_NAME, NAME, schedule
from kb.config import Config, Schedule
from kb.paths import Paths

LABEL = f"{NAME.lower()}.kb.ingest"
TIMER = f"{NAME.lower()}-kb-ingest.timer"
SERVICE = f"{NAME.lower()}-kb-ingest.service"
OLD_LABEL = f"{LEGACY_NAME.lower()}.kb.ingest"
OLD_TIMER = f"{LEGACY_NAME.lower()}-kb-ingest.timer"
OLD_SERVICE = f"{LEGACY_NAME.lower()}-kb-ingest.service"

# The real tools fail when a job is loaded twice or unloaded while not loaded.
FAKE_TOOL = """#!{python}
import os, sys
state = os.environ["FAKE_STATE"]
tool = os.path.basename(sys.argv[0])
args = [a for a in sys.argv[1:] if a != "--user"]
with open(os.path.join(state, "calls"), "a") as fh:
    fh.write(tool + " " + " ".join(args) + "\\n")
if any(word in args for word in os.environ.get("FAKE_FAIL_ON", "").split()):
    sys.stderr.write("simulated failure\\n")
    sys.exit(1)
flag = os.path.join(state, "loaded")
loaded = os.path.exists(flag)
verb = args[0]
if tool == "launchctl":
    if verb == "bootstrap":
        if loaded or not os.path.exists(args[2]):
            sys.exit(5)
        open(flag, "w").close()
    elif verb == "bootout" or verb == "print":
        if not loaded:
            sys.exit(113)
        if verb == "bootout":
            os.remove(flag)
else:
    if verb == "enable":
        open(flag, "w").close()
    elif verb == "disable":
        if not loaded:
            sys.exit(1)
        os.remove(flag)
    elif verb == "is-active":
        print("active" if loaded else "inactive")
        sys.exit(0 if loaded else 3)
"""

KB = "/home/sam/.local/bin/kb"
SPACED_KB = "/home/sam/my tools/kb"


def unit_lines(path):
    # systemd allows a key twice, so each key maps to a list.
    found = {}
    for line in Path(path).read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            found.setdefault(key, []).append(value)
    return found


class ScheduleCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.state = root / "state"
        self.state.mkdir()
        self.bin_dir = bin_dir = root / "bin"
        bin_dir.mkdir()
        for name in ("launchctl", "systemctl"):
            tool = bin_dir / name
            tool.write_text(FAKE_TOOL.format(python=sys.executable))
            tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        patcher = mock.patch.dict(os.environ, {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_STATE": str(self.state), "FAKE_FAIL_ON": "",
            "HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for var in ("XDG_CONFIG_HOME", "KB_HOME"):
            os.environ.pop(var, None)
        self.paths = Paths.under(root / "kb-home")

    def calls(self):
        log = self.state / "calls"
        return log.read_text().splitlines() if log.exists() else []

    def path_with(self, *dirs):
        return os.pathsep.join([*dirs, str(self.bin_dir), os.environ["PATH"]])

    def fail_on(self, *words):
        os.environ["FAKE_FAIL_ON"] = " ".join(words)

    def config(self, minutes=15):
        return Config(schedule=Schedule(every_minutes=minutes))


class MacTest(ScheduleCase):
    def on(self, minutes=15, kb_path=KB):
        return schedule.on(self.paths, self.config(minutes), kb_path, platform="darwin", home=self.home)

    def off(self):
        return schedule.off(platform="darwin", home=self.home)

    def status(self):
        return schedule.status(platform="darwin", home=self.home)

    @property
    def plist(self):
        return self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    def read_plist(self):
        return plistlib.loads(self.plist.read_bytes())

    def test_on_writes_an_agent_that_runs_ingest_on_the_interval(self):
        message = self.on(minutes=20)
        job = self.read_plist()
        self.assertEqual(job["Label"], LABEL)
        self.assertEqual(job["ProgramArguments"], [KB, "ingest"])
        self.assertEqual(job["StartInterval"], 20 * 60)
        self.assertIs(job["RunAtLoad"], False)
        self.assertEqual(Path(job["StandardOutPath"]).parent, self.paths.log_dir)
        self.assertEqual(Path(job["StandardErrorPath"]).parent, self.paths.log_dir)
        self.assertTrue(self.paths.log_dir.is_dir())
        self.assertIn("20 minutes", message)
        self.assertIn("is loaded", self.status())

    def test_the_agent_carries_the_path_it_was_turned_on_with(self):
        path = self.path_with("/opt/homebrew/bin")
        with mock.patch.dict(os.environ, {"PATH": path}):
            self.on()
        env = self.read_plist()["EnvironmentVariables"]
        self.assertEqual(env["PATH"], path)
        self.assertNotIn("KB_HOME", env)

    def test_the_agent_carries_kb_home_when_it_is_set(self):
        with mock.patch.dict(os.environ, {"KB_HOME": "/home/sam/trial"}):
            self.on()
        self.assertEqual(self.read_plist()["EnvironmentVariables"]["KB_HOME"], "/home/sam/trial")

    def test_the_agent_carries_custom_xdg_folders(self):
        folders = {"XDG_CONFIG_HOME": "/home/sam/cfg", "XDG_DATA_HOME": "/home/sam/data",
                   "XDG_STATE_HOME": "/home/sam/state", "XDG_CACHE_HOME": "/home/sam/cache"}
        with mock.patch.dict(os.environ, folders):
            self.on()
        env = self.read_plist()["EnvironmentVariables"]
        self.assertEqual({k: env.get(k) for k in folders}, folders)

    def test_on_twice_leaves_one_loaded_job(self):
        self.on()
        self.on()
        self.assertEqual([p.name for p in self.plist.parent.iterdir()], [self.plist.name])
        self.assertIn("is loaded", self.status())

    def test_on_after_an_interval_change_rewrites_the_job(self):
        self.on(minutes=15)
        self.on(minutes=45)
        self.assertEqual(self.read_plist()["StartInterval"], 45 * 60)
        self.assertIn("is loaded", self.status())

    def test_off_unloads_the_job_and_removes_the_file(self):
        self.on()
        self.off()
        self.assertFalse(self.plist.exists())
        self.assertEqual(self.status(), "Not scheduled.")

    def test_off_with_nothing_scheduled_succeeds_and_says_so(self):
        self.assertEqual(self.off(), "Nothing was scheduled.")
        self.assertEqual(self.off(), "Nothing was scheduled.")

    def test_off_still_removes_a_file_launchd_never_loaded(self):
        self.on()
        self.fail_on("bootout")
        self.off()
        self.assertFalse(self.plist.exists())

    def test_status_tells_a_loaded_job_from_a_file_launchd_dropped(self):
        self.assertEqual(self.status(), "Not scheduled.")
        self.on()
        self.assertIn("is loaded", self.status())
        self.fail_on("print")
        self.assertIn("has not loaded it", self.status())

    def test_a_job_that_fails_to_load_is_an_error(self):
        self.fail_on("bootstrap")
        with self.assertRaises(schedule.ScheduleError) as caught:
            self.on()
        self.assertIn("simulated failure", str(caught.exception))

    def test_a_relative_kb_path_or_a_zero_interval_is_refused(self):
        with self.assertRaises(schedule.ScheduleError):
            self.on(kb_path="kb")
        with self.assertRaises(schedule.ScheduleError):
            self.on(minutes=0)
        self.assertFalse(self.plist.exists())


class LinuxTest(ScheduleCase):
    def on(self, minutes=15, kb_path=KB):
        return schedule.on(self.paths, self.config(minutes), kb_path, platform="linux", home=self.home)

    def off(self):
        return schedule.off(platform="linux", home=self.home)

    def status(self):
        return schedule.status(platform="linux", home=self.home)

    @property
    def unit_dir(self):
        return self.home / ".config" / "systemd" / "user"

    def test_on_writes_a_oneshot_service_and_a_timer_on_the_interval(self):
        message = self.on(minutes=30)
        service = unit_lines(self.unit_dir / SERVICE)
        timer = unit_lines(self.unit_dir / TIMER)
        self.assertEqual(service["Type"], ["oneshot"])
        self.assertEqual(service["ExecStart"], [f'"{KB}" ingest'])
        self.assertEqual(timer["OnBootSec"], ["2min"])
        self.assertEqual(timer["OnUnitActiveSec"], ["30min"])
        self.assertEqual(timer["Persistent"], ["true"])
        self.assertEqual(timer["Unit"], [SERVICE])
        self.assertIn("30 minutes", message)
        self.assertEqual(self.status(), f"Scheduled: systemd timer {TIMER} is active.")

    def test_on_says_a_user_timer_needs_lingering_without_running_loginctl(self):
        message = self.on()
        self.assertIn("loginctl enable-linger", message)
        self.assertFalse([c for c in self.calls() if c.startswith("loginctl")])

    def test_a_kb_path_with_a_space_is_quoted_for_systemd(self):
        self.on(kb_path=SPACED_KB)
        self.assertEqual(unit_lines(self.unit_dir / SERVICE)["ExecStart"], [f'"{SPACED_KB}" ingest'])

    def test_percent_and_dollar_signs_in_the_path_are_escaped_for_systemd(self):
        self.on(kb_path="/home/sam/100%/$bin/kb")
        self.assertEqual(unit_lines(self.unit_dir / SERVICE)["ExecStart"], ['"/home/sam/100%%/$$bin/kb" ingest'])

    def test_a_dollar_sign_in_an_environment_value_stays_single(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/home/sam/$data", "XDG_STATE_HOME": "/home/sam/100%"}):
            self.on()
        env = unit_lines(self.unit_dir / SERVICE)["Environment"]
        self.assertIn('"XDG_DATA_HOME=/home/sam/$data"', env)
        self.assertIn('"XDG_STATE_HOME=/home/sam/100%%"', env)

    def test_the_service_carries_the_path_it_was_turned_on_with(self):
        path = self.path_with("/home/sam/.local/bin")
        with mock.patch.dict(os.environ, {"PATH": path}):
            self.on()
        env = unit_lines(self.unit_dir / SERVICE)["Environment"]
        self.assertEqual(env, [f'"PATH={path}"'])

    def test_the_service_carries_custom_xdg_folders(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/home/sam/data", "XDG_STATE_HOME": "/home/sam/state"}):
            self.on()
        env = unit_lines(self.unit_dir / SERVICE)["Environment"]
        self.assertIn('"XDG_DATA_HOME=/home/sam/data"', env)
        self.assertIn('"XDG_STATE_HOME=/home/sam/state"', env)

    def test_the_service_carries_kb_home_when_it_is_set(self):
        with mock.patch.dict(os.environ, {"KB_HOME": "/home/sam/trial"}):
            self.on()
        self.assertIn('"KB_HOME=/home/sam/trial"', unit_lines(self.unit_dir / SERVICE)["Environment"])

    def test_units_go_under_xdg_config_home_when_it_is_set(self):
        xdg = self.home / "xdg"
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg)}):
            self.on()
        self.assertTrue((xdg / "systemd" / "user" / TIMER).exists())
        self.assertFalse(self.unit_dir.exists())

    def test_on_twice_leaves_one_active_timer(self):
        self.on()
        self.on()
        self.assertEqual(sorted(p.name for p in self.unit_dir.iterdir()), sorted([SERVICE, TIMER]))
        self.assertIn("is active", self.status())

    def test_on_after_an_interval_change_rewrites_the_timer_and_restarts_it(self):
        self.on(minutes=15)
        self.on(minutes=45)
        self.assertEqual(unit_lines(self.unit_dir / TIMER)["OnUnitActiveSec"], ["45min"])
        calls = self.calls()
        last_reload = max(i for i, c in enumerate(calls) if c.endswith("daemon-reload"))
        self.assertIn(f"systemctl restart {TIMER}", calls[last_reload:])

    def test_off_disables_the_timer_and_removes_both_files(self):
        self.on()
        self.off()
        self.assertEqual(list(self.unit_dir.iterdir()), [])
        self.assertEqual(self.status(), "Not scheduled.")

    def test_off_with_nothing_scheduled_succeeds_and_says_so(self):
        self.assertEqual(self.off(), "Nothing was scheduled.")
        self.assertEqual(self.off(), "Nothing was scheduled.")

    def test_status_tells_an_active_timer_from_a_written_one_that_is_not_running(self):
        self.assertEqual(self.status(), "Not scheduled.")
        self.on()
        self.assertIn("is active", self.status())
        self.fail_on("is-active")
        self.assertIn("not active", self.status())

    def test_without_a_user_manager_on_says_what_to_put_in_crontab_and_writes_nothing(self):
        self.fail_on("show-environment")
        path = self.path_with("/home/sam/.local/bin")
        with mock.patch.dict(os.environ, {"PATH": path}):
            with self.assertRaises(schedule.ScheduleError) as caught:
                self.on(minutes=10, kb_path=SPACED_KB)
        message = str(caught.exception)
        self.assertIn("*/10 * * * * PATH=", message)
        self.assertIn(path, message)
        self.assertIn("'/home/sam/my tools/kb' ingest", message)
        self.assertFalse((self.home / ".config").exists())
        self.assertEqual(self.calls(), ["systemctl show-environment"])

    def test_the_crontab_line_for_a_whole_number_of_hours_runs_on_the_hour(self):
        self.fail_on("show-environment")
        with self.assertRaises(schedule.ScheduleError) as caught:
            self.on(minutes=120)
        self.assertIn("0 */2 * * * ", str(caught.exception))

    def test_a_failing_systemctl_step_is_an_error(self):
        self.fail_on("enable")
        with self.assertRaises(schedule.ScheduleError):
            self.on()


class FakeServices:
    """launchd and systemd as sets of loaded job names, so a test can tell the old job from the new one."""

    def __init__(self):
        self.loaded = set()
        self.calls = []

    def __call__(self, *argv):
        self.calls.append(" ".join(argv))
        tool, args = argv[0], [a for a in argv[1:] if a != "--user"]
        verb = args[0]
        if tool == "launchctl":
            if verb == "bootstrap":
                self.loaded.add(plistlib.loads(Path(args[2]).read_bytes())["Label"])
                return 0, ""
            label = args[1].rsplit("/", 1)[1]
            if label not in self.loaded:
                return 113, "not loaded"
            if verb == "bootout":
                self.loaded.discard(label)
            return 0, ""
        if verb == "enable":
            self.loaded.add(args[-1])
        elif verb == "disable":
            if args[-1] not in self.loaded:
                return 1, "not loaded"
            self.loaded.discard(args[-1])
        elif verb == "is-active":
            return (0, "active") if args[-1] in self.loaded else (3, "inactive")
        return 0, ""


class OldNameCase(unittest.TestCase):
    """A job turned on while the product was called Varve is replaced, stopped and reported by the new name."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.home.mkdir()
        self.paths = Paths.under(Path(tmp.name) / "kb-home")
        self.fake = FakeServices()
        for patcher in (mock.patch.object(schedule, "_run", self.fake),
                        mock.patch.dict(os.environ, {"HOME": str(self.home)})):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop("XDG_CONFIG_HOME", None)
        self.config = Config(schedule=Schedule(every_minutes=15))


class MacOldNameTest(OldNameCase):
    def old_plist(self):
        return self.home / "Library" / "LaunchAgents" / f"{OLD_LABEL}.plist"

    def new_plist(self):
        return self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    def turn_on_old(self):
        self.old_plist().parent.mkdir(parents=True)
        self.old_plist().write_bytes(plistlib.dumps({"Label": OLD_LABEL, "ProgramArguments": [KB, "ingest"],
                                                      "StartInterval": 900}))
        self.fake.loaded.add(OLD_LABEL)

    def on(self):
        return schedule.on(self.paths, self.config, KB, platform="darwin", home=self.home)

    def test_on_replaces_the_old_job_with_one_under_the_new_name(self):
        self.turn_on_old()
        message = self.on()
        self.assertEqual(self.fake.loaded, {LABEL})
        self.assertFalse(self.old_plist().exists())
        self.assertTrue(self.new_plist().exists())
        self.assertIn(OLD_LABEL, message)

    def test_a_new_job_that_fails_to_load_leaves_the_old_one_running(self):
        self.turn_on_old()
        real = self.fake.__call__

        def refuse(*argv):
            return (5, "simulated failure") if argv[1] == "bootstrap" else real(*argv)
        with mock.patch.object(schedule, "_run", refuse):
            with self.assertRaises(schedule.ScheduleError):
                self.on()
        self.assertIn(OLD_LABEL, self.fake.loaded)
        self.assertTrue(self.old_plist().exists())

    def test_without_an_old_job_on_says_nothing_about_one(self):
        self.assertNotIn(OLD_LABEL, self.on())

    def test_off_stops_the_old_job_and_removes_its_file(self):
        self.turn_on_old()
        message = schedule.off(platform="darwin", home=self.home)
        self.assertEqual(self.fake.loaded, set())
        self.assertFalse(self.old_plist().exists())
        self.assertIn(OLD_LABEL, message)

    def test_off_stops_both_when_both_are_there(self):
        self.turn_on_old()
        self.new_plist().write_bytes(plistlib.dumps({"Label": LABEL}))
        self.fake.loaded.add(LABEL)
        schedule.off(platform="darwin", home=self.home)
        self.assertEqual(self.fake.loaded, set())
        self.assertFalse(self.new_plist().exists() or self.old_plist().exists())

    def test_status_reports_the_old_job_and_how_to_move_it(self):
        self.turn_on_old()
        message = schedule.status(platform="darwin", home=self.home)
        self.assertIn(f"launchd agent {OLD_LABEL} is loaded", message)
        self.assertIn(LABEL, message)

    def test_status_is_unchanged_with_no_job_of_either_name(self):
        self.assertEqual(schedule.status(platform="darwin", home=self.home), "Not scheduled.")


class LinuxOldNameTest(OldNameCase):
    def unit_dir(self):
        return self.home / ".config" / "systemd" / "user"

    def turn_on_old(self):
        self.unit_dir().mkdir(parents=True)
        (self.unit_dir() / OLD_SERVICE).write_text("[Service]\nType=oneshot\n")
        (self.unit_dir() / OLD_TIMER).write_text("[Timer]\nOnBootSec=2min\n")
        self.fake.loaded.add(OLD_TIMER)

    def on(self):
        return schedule.on(self.paths, self.config, KB, platform="linux", home=self.home)

    def test_on_replaces_the_old_timer_with_one_under_the_new_name(self):
        self.turn_on_old()
        message = self.on()
        self.assertEqual(self.fake.loaded, {TIMER})
        self.assertEqual(sorted(p.name for p in self.unit_dir().iterdir()), sorted([SERVICE, TIMER]))
        self.assertIn(OLD_TIMER, message)

    def test_a_new_timer_that_fails_to_start_leaves_the_old_one_running(self):
        self.turn_on_old()
        real = self.fake.__call__

        def refuse(*argv):
            return (1, "simulated failure") if "enable" in argv else real(*argv)
        with mock.patch.object(schedule, "_run", refuse):
            with self.assertRaises(schedule.ScheduleError):
                self.on()
        self.assertIn(OLD_TIMER, self.fake.loaded)
        self.assertTrue((self.unit_dir() / OLD_TIMER).exists())

    def test_without_an_old_timer_on_says_nothing_about_one(self):
        self.assertNotIn(OLD_TIMER, self.on())

    def test_off_stops_the_old_timer_and_removes_both_files(self):
        self.turn_on_old()
        message = schedule.off(platform="linux", home=self.home)
        self.assertEqual(self.fake.loaded, set())
        self.assertEqual(list(self.unit_dir().iterdir()), [])
        self.assertIn(OLD_TIMER, message)

    def test_status_reports_the_old_timer_and_how_to_move_it(self):
        self.turn_on_old()
        message = schedule.status(platform="linux", home=self.home)
        self.assertIn(f"systemd timer {OLD_TIMER} is active", message)
        self.assertIn(TIMER, message)

    def test_status_is_unchanged_with_no_timer_of_either_name(self):
        self.assertEqual(schedule.status(platform="linux", home=self.home), "Not scheduled.")


class ErrorTypeTest(unittest.TestCase):
    def test_schedule_errors_are_runtime_errors(self):
        self.assertTrue(issubclass(schedule.ScheduleError, RuntimeError))


if __name__ == "__main__":
    unittest.main()
