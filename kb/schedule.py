import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

from kb import LEGACY_NAME, NAME
from kb.paths import HOME_ENV

SLUG = NAME.lower()
LABEL = f"{SLUG}.kb.ingest"
UNIT = f"{SLUG}-kb-ingest"
SERVICE = f"{UNIT}.service"
TIMER = f"{UNIT}.timer"
# The job as it was named before the rename. Turning the schedule on replaces it; off stops it; status reports it.
OLD_LABEL = f"{LEGACY_NAME.lower()}.kb.ingest"
OLD_UNIT = f"{LEGACY_NAME.lower()}-kb-ingest"
OLD_SERVICE = f"{OLD_UNIT}.service"
OLD_TIMER = f"{OLD_UNIT}.timer"
NOT_SCHEDULED = "Not scheduled."
FALLBACK_PATH = "/usr/local/bin:/usr/bin:/bin"


class ScheduleError(RuntimeError):
    pass


def _is_mac(platform):
    return (platform or sys.platform) == "darwin"


def _home(home):
    return Path(home or os.path.expanduser("~"))


def _environment():
    env = {"PATH": os.environ.get("PATH") or FALLBACK_PATH}
    # Where kb's config, database, logs and cache are; the job must find the same ones.
    for name in (HOME_ENV, "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


def _run(*argv):
    try:
        done = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    except OSError as exc:
        return 127, str(exc)
    return done.returncode, done.stdout.strip()


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _remove(path):
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _minutes(config):
    minutes = config.schedule.every_minutes
    if not isinstance(minutes, int) or minutes < 1:
        raise ScheduleError(f"schedule.every_minutes must be a whole number of minutes, 1 or more (got {minutes!r}).")
    return minutes


def _kb_path(kb_path):
    kb_path = str(kb_path)
    if not os.path.isabs(kb_path):
        raise ScheduleError(f"The path to the kb command must be absolute (got {kb_path!r}).")
    return kb_path


def _every(minutes):
    return f"every {minutes} minute" + ("" if minutes == 1 else "s")


def _plist_path(home, label=LABEL):
    return _home(home) / "Library" / "LaunchAgents" / f"{label}.plist"


def _target(label=LABEL):
    return f"gui/{os.getuid()}/{label}"


def _mac_on(paths, config, kb_path, home):
    minutes = _minutes(config)
    plist = _plist_path(home)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    log = str(paths.log("launchd"))
    _write(plist, plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [kb_path, "ingest"],
        "StartInterval": minutes * 60,
        "RunAtLoad": False,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": _environment(),
    }))
    _run("launchctl", "bootout", _target())
    code, out = _run("launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist))
    if code:
        raise ScheduleError(f"launchctl could not load {plist}: {out or 'exit ' + str(code)}")
    message = f"Scheduled ingest {_every(minutes)} with the launchd agent {LABEL}."
    if _plist_path(home, OLD_LABEL).exists() and _mac_stop(OLD_LABEL, home):
        message += f" Replaced the old launchd agent {OLD_LABEL}."
    return message


def _mac_stop(label, home):
    unloaded = _run("launchctl", "bootout", _target(label))[0] == 0
    removed = _remove(_plist_path(home, label))
    return unloaded or removed


def _mac_off(home):
    stopped = _mac_stop(LABEL, home)
    stopped_old = _mac_stop(OLD_LABEL, home)
    if not (stopped or stopped_old):
        return "Nothing was scheduled."
    names = LABEL if stopped and not stopped_old else OLD_LABEL if stopped_old and not stopped else f"{LABEL} and {OLD_LABEL}"
    return f"Stopped the launchd agent {names} and removed its file."


def _mac_status(home):
    message = _mac_job_status(home, LABEL)
    if message == NOT_SCHEDULED:
        old = _mac_job_status(home, OLD_LABEL)
        if old != NOT_SCHEDULED:
            return f"{old} It moves to {LABEL} when you turn the schedule on again."
    return message


def _mac_job_status(home, label):
    plist = _plist_path(home, label)
    loaded = _run("launchctl", "print", _target(label))[0] == 0
    if plist.exists() and loaded:
        return f"Scheduled: launchd agent {label} is loaded ({plist})."
    if plist.exists():
        return f"Not running: {plist} exists but launchd has not loaded it. Turn the schedule on again."
    if loaded:
        return f"launchd has {label} loaded but {plist} is gone. Turn the schedule off, then on again."
    return NOT_SCHEDULED


def _unit_dir(home):
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if os.path.isabs(xdg) else _home(home) / ".config"
    return base / "systemd" / "user"


def _quote(text, dollars=True):
    """One systemd word in double quotes. systemd expands % specifiers everywhere and $ variables only in Exec lines,
    so a $ is doubled only where it would be expanded."""
    text = text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{text.replace("$", "$$") if dollars else text}"'


def _cron_line(kb_path, minutes):
    when = f"*/{minutes} * * * *" if minutes < 60 else f"0 */{minutes // 60} * * *"
    env = " ".join(f"{k}={shlex.quote(v)}" for k, v in _environment().items())
    return f"{when} {env} {shlex.quote(kb_path)} ingest"


def _service_text(kb_path):
    lines = ["[Unit]", f"Description={NAME} knowledge base ingest", "", "[Service]", "Type=oneshot",
             f"ExecStart={_quote(kb_path)} ingest"]
    lines += [f"Environment={_quote(f'{k}={v}', dollars=False)}" for k, v in _environment().items()]
    return "\n".join(lines) + "\n"


def _timer_text(minutes):
    return "\n".join([
        "[Unit]", f"Description={NAME} knowledge base ingest, {_every(minutes)}", "",
        "[Timer]", "OnBootSec=2min", f"OnUnitActiveSec={minutes}min", "Persistent=true", f"Unit={SERVICE}", "",
        "[Install]", "WantedBy=timers.target", ""])


def _linux_on(config, kb_path, home):
    minutes = _minutes(config)
    if _run("systemctl", "--user", "show-environment")[0]:
        raise ScheduleError(
            "systemd has no user manager here, so a timer cannot run. Add this line with `crontab -e` instead:\n"
            + _cron_line(kb_path, minutes))
    unit_dir = _unit_dir(home)
    _write(unit_dir / SERVICE, _service_text(kb_path).encode("utf-8"))
    _write(unit_dir / TIMER, _timer_text(minutes).encode("utf-8"))
    # A timer that is already running keeps its old interval until it restarts.
    for argv in (("daemon-reload",), ("enable", "--now", TIMER), ("restart", TIMER)):
        code, out = _run("systemctl", "--user", *argv)
        if code:
            raise ScheduleError(f"systemctl --user {' '.join(argv)} failed: {out or 'exit ' + str(code)}")
    message = f"Scheduled ingest {_every(minutes)} with the systemd timer {TIMER}. "
    if (_unit_dir(home) / OLD_TIMER).exists() and _linux_stop(OLD_TIMER, OLD_SERVICE, home):
        message += f"Replaced the old timer {OLD_TIMER}. "
    return message + "A user timer stops at logout unless lingering is on (`loginctl enable-linger`)."


def _linux_stop(timer, service, home):
    unit_dir = _unit_dir(home)
    disabled = _run("systemctl", "--user", "disable", "--now", timer)[0] == 0
    removed = [_remove(unit_dir / name) for name in (timer, service)]
    _run("systemctl", "--user", "daemon-reload")
    return disabled or any(removed)


def _linux_off(home):
    stopped = _linux_stop(TIMER, SERVICE, home)
    stopped_old = _linux_stop(OLD_TIMER, OLD_SERVICE, home)
    if not (stopped or stopped_old):
        return "Nothing was scheduled."
    names = TIMER if stopped and not stopped_old else OLD_TIMER if stopped_old and not stopped else f"{TIMER} and {OLD_TIMER}"
    return f"Stopped the systemd timer {names} and removed its files."


def _linux_status(home):
    message = _linux_job_status(home, TIMER)
    if message == NOT_SCHEDULED:
        old = _linux_job_status(home, OLD_TIMER)
        if old != NOT_SCHEDULED:
            return f"{old} It moves to {TIMER} when you turn the schedule on again."
    return message


def _linux_job_status(home, timer):
    exists = (_unit_dir(home) / timer).exists()
    active = _run("systemctl", "--user", "is-active", timer)[0] == 0
    if exists and active:
        return f"Scheduled: systemd timer {timer} is active."
    if exists:
        return f"Not running: {timer} is written but not active. Turn the schedule on again."
    if active:
        return f"systemd has {timer} active but its file is gone. Turn the schedule off, then on again."
    return NOT_SCHEDULED


def on(paths, config, kb_path, platform=None, home=None):
    """Run `kb ingest` on the configured interval. Safe to repeat: it leaves one job with the current interval."""
    kb_path = _kb_path(kb_path)
    if _is_mac(platform):
        return _mac_on(paths, config, kb_path, home)
    return _linux_on(config, kb_path, home)


def off(platform=None, home=None):
    """Stop the scheduled ingest and remove its files. Succeeds when nothing is scheduled."""
    return _mac_off(home) if _is_mac(platform) else _linux_off(home)


def status(platform=None, home=None):
    return _mac_status(home) if _is_mac(platform) else _linux_status(home)
