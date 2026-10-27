import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

from kb import NAME
from kb.paths import HOME_ENV

SLUG = NAME.lower()
LABEL = f"{SLUG}.kb.ingest"
UNIT = f"{SLUG}-kb-ingest"
SERVICE = f"{UNIT}.service"
TIMER = f"{UNIT}.timer"
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


def _plist_path(home):
    return _home(home) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _target():
    return f"gui/{os.getuid()}/{LABEL}"


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
    return f"Scheduled ingest {_every(minutes)} with the launchd agent {LABEL}."


def _mac_off(home):
    unloaded = _run("launchctl", "bootout", _target())[0] == 0
    removed = _remove(_plist_path(home))
    if not (unloaded or removed):
        return "Nothing was scheduled."
    return f"Stopped the launchd agent {LABEL} and removed its file."


def _mac_status(home):
    plist = _plist_path(home)
    loaded = _run("launchctl", "print", _target())[0] == 0
    if plist.exists() and loaded:
        return f"Scheduled: launchd agent {LABEL} is loaded ({plist})."
    if plist.exists():
        return f"Not running: {plist} exists but launchd has not loaded it. Turn the schedule on again."
    if loaded:
        return f"launchd has {LABEL} loaded but {plist} is gone. Turn the schedule off, then on again."
    return "Not scheduled."


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
    return (f"Scheduled ingest {_every(minutes)} with the systemd timer {TIMER}. "
            "A user timer stops at logout unless lingering is on (`loginctl enable-linger`).")


def _linux_off(home):
    unit_dir = _unit_dir(home)
    disabled = _run("systemctl", "--user", "disable", "--now", TIMER)[0] == 0
    removed = [_remove(unit_dir / name) for name in (TIMER, SERVICE)]
    _run("systemctl", "--user", "daemon-reload")
    if not (disabled or any(removed)):
        return "Nothing was scheduled."
    return f"Stopped the systemd timer {TIMER} and removed its files."


def _linux_status(home):
    exists = (_unit_dir(home) / TIMER).exists()
    active = _run("systemctl", "--user", "is-active", TIMER)[0] == 0
    if exists and active:
        return f"Scheduled: systemd timer {TIMER} is active."
    if exists:
        return f"Not running: {TIMER} is written but not active. Turn the schedule on again."
    if active:
        return f"systemd has {TIMER} active but its file is gone. Turn the schedule off, then on again."
    return "Not scheduled."


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
