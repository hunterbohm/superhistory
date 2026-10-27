"""`kb setup`: ask what to read and which AI to use, then write the config file.

run() only asks and writes. The questions come in through `ask(question, default)` and `secret(question)`, so a
terminal, a script and a test drive it the same way. What the user chose to do next (hooks, schedule, a first ingest) is
handed back for the CLI to carry out.
"""
import json
import os
import re
import shutil
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Tuple

from kb import NAME, agents, ai, config, sources
from kb.agents import write as write_file
from kb.ai import cli as ai_cli

YES = ("y", "yes")
NO = ("n", "no")
NAME_RULE = "Use lowercase letters, digits, - and _, up to 40 characters."
NONE_LABEL = "none for now"


class SetupResult(NamedTuple):
    hooks: bool
    schedule: bool
    ingest_now: bool
    config_path: Path


class Offer(NamedTuple):
    name: str
    what: str
    found: bool
    note: str = ""


class Plan:
    """The file to write: tables in order, and the secrets to save once the text has parsed."""

    def __init__(self, machine):
        self.machine = machine
        self.tables: List[Tuple[str, Dict[str, Any]]] = []
        self.secrets: Dict[str, str] = {}

    def table(self, header, **values):
        self.tables.append((header, values))

    def secret(self, paths, filename, value):
        """Holds the value for saving later and returns the config reference to its file."""
        self.secrets[filename] = value
        return f"file:{paths.keys_dir / filename}"

    def text(self):
        lines = [f"# {NAME} config, written by `kb setup`.",
                 "# Edit it freely. `kb setup` keeps a backup beside it before it replaces this file.",
                 "# Keys and passwords are not here: each is a file under the keys folder that a key line names.",
                 "", f"machine = {_toml(self.machine)}"]
        for header, values in self.tables:
            lines += ["", f"[{header}]"] + [f"{key} = {_toml(value)}" for key, value in values.items()]
        return "\n".join(lines) + "\n"


def _toml(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return json.dumps(value)


def default_machine():
    """The short host name as a config machine name: lowercase, odd characters turned to "-", at most 40 long."""
    short = socket.gethostname().split(".")[0].lower()
    short = re.sub(r"[^a-z0-9_-]+", "-", short).strip("-_")[:40].strip("-_")
    return short if config.NAME_RE.match(short) else "local"


def _yes(ask, question, default):
    while True:
        answer = (ask(question, default) or default).strip().lower()
        if answer in YES:
            return True
        if answer in NO:
            return False
        print("  Please answer yes or no.")


def _expand(text, home):
    text = text.strip()
    if text == "~" or text.startswith("~/"):
        text = home + text[1:]
    return os.path.abspath(text)


def _list(text):
    return [part.strip() for part in text.split(",") if part.strip()]


def _present(path):
    """Whether the file is there. A Mac that has not granted Full Disk Access still answers "not permitted"."""
    try:
        os.stat(path)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _offers(home, platform, which):
    def under(*parts):
        return os.path.join(home, *parts)

    mac_python = os.path.realpath(sys.executable)
    return [
        Offer("claude_code", "Claude Code sessions (~/.claude/projects)", os.path.isdir(under(".claude", "projects"))),
        Offer("codex", "Codex sessions (~/.codex/sessions)", os.path.isdir(under(".codex", "sessions"))),
        Offer("hermes", "Hermes sessions (~/.hermes/state.db)", os.path.isfile(under(".hermes", "state.db"))),
        Offer("imessage", "iMessage (~/Library/Messages/chat.db)",
              platform == "darwin" and _present(under("Library", "Messages", "chat.db")),
              f"iMessage needs Full Disk Access for the Python program that runs kb: {mac_python}. Add it in "
              "System Settings, Privacy & Security, Full Disk Access."),
        Offer("git", "your git commits", os.path.isdir(under("code"))),
        Offer("github", "your GitHub issues and pull requests (gh)", bool(which("gh"))),
    ]


def _git_roots(ask, home):
    answer = ask("Folders that hold your git projects, comma-separated", "~/code") or "~/code"
    roots = []
    for item in _list(answer):
        path = _expand(item, home)
        if os.path.isdir(path):
            roots.append(sources.home_key(path, home))
        else:
            print(f"  Skipping {item}: not a folder.")
    return roots


def _notes(ask, home):
    while True:
        answer = ask("Folder of notes to read, blank to skip", "").strip()
        if not answer:
            return None
        path = _expand(answer, home)
        if os.path.isdir(path):
            break
        print(f"  {answer} is not a folder here. Try again, or leave it blank to skip.")
    left_out = _list(ask("Folders under it to leave out, comma-separated, blank for none", ""))
    return sources.home_key(path, home), left_out


def _gmail(ask, secret):
    while True:
        address = ask("Gmail address to read, blank to skip", "").strip()
        if not address or "@" in address:
            break
        print("  That is not an email address.")
    if not address:
        return None
    password = "".join(secret(
        "Gmail app password. Create one at myaccount.google.com/apppasswords (it needs 2-Step Verification on)"
    ).split())
    if not password:
        print("  No password entered, so Gmail is left out.")
        return None
    return address, password


def _choose_ai(ask, secret, which, plan, paths):
    clis = [s for s in ai_cli.SPECS if not s.hidden]
    found = {s.name: which(s.program) for s in clis}
    visible = ai.visible_plugins()
    options = [name for name in (s.name for s in clis) if found[name]]
    options += [name for name in visible if name not in found]
    labels = [ai.PLUGINS[n].label + (" (found)" if found.get(n) else "") for n in options]
    print("AI that sorts and summarizes what is read:")
    for i, label in enumerate(labels, 1):
        print(f"  {i}. {label}")
    print(f"  {len(options) + 1}. {NONE_LABEL}")
    default = "1" if any(found.values()) else str(len(options) + 1)
    while True:
        answer = (ask("Which one", default) or default).strip()
        if answer.isdigit() and 1 <= int(answer) <= len(options) + 1:
            index = int(answer) - 1
            break
        if answer in options:
            index = options.index(answer)
            break
        print("  Pick a number from the list.")
    if index == len(options):
        return None
    name = options[index]
    table = {"plugin": name}
    if name in found:
        table["binary"] = os.path.abspath(found[name])
        plan.table("ai", **table)
        return name
    compatible = name.endswith("-compatible")
    if compatible:
        table["base_url"] = _required(ask, "Base URL of the server")
        if name == "openai-compatible":
            table["model"] = _required(ask, "Model name to ask for")
    key = secret(f"Key for {name}" + (", blank if the server needs none" if compatible else "")).strip()
    if not key and not compatible:
        print("  No key entered, so the AI is left for later.")
        return None
    if key:
        table["key"] = plan.secret(paths, f"{name}.key", key)
    plan.table("ai", **table)
    return name


def _required(ask, question):
    while True:
        answer = ask(question, "").strip()
        if answer:
            return answer
        print("  This one cannot be blank.")


def run(paths, ask, secret, home=None, platform=None, which=shutil.which):
    """Asks the questions and writes paths.config_file. Nothing is written when the user keeps the file that is there."""
    home = str(home or os.path.expanduser("~")).rstrip("/") or "/"
    platform = platform or sys.platform
    path = paths.config_file
    declined = SetupResult(False, False, False, path)

    if path.exists() and not _yes(ask, f"A config already exists at {path}. Replace it (a backup is kept)?", "no"):
        print("Kept the config as it is.")
        return declined

    while True:
        machine = (ask("This computer's name in stored records", default_machine()) or default_machine()).strip().lower()
        if config.NAME_RE.match(machine):
            break
        print(f"  {NAME_RULE}")
    print(f"  Computer name: {machine}.")
    plan = Plan(machine)

    chosen = []
    for offer in _offers(home, platform, which):
        if not offer.found:
            continue
        if offer.note:
            print(offer.note)
        if not _yes(ask, f"Read {offer.what}?", "yes"):
            continue
        if offer.name == "git":
            roots = _git_roots(ask, home)
            if not roots:
                continue
            plan.table("sources.git", roots=roots)
        else:
            plan.table(f"sources.{offer.name}")
        chosen.append(offer.name)
        print(f"  Reading {offer.what}.")

    notes = _notes(ask, home)
    if notes:
        path_text, left_out = notes
        values = {"path": path_text}
        if left_out:
            values["exclude"] = left_out
        plan.table("sources.notes", **values)
        chosen.append("notes")
        print(f"  Reading notes in {path_text}.")

    mail = _gmail(ask, secret)
    if mail:
        address, password = mail
        plan.table("sources.gmail", address=address, password=plan.secret(paths, "gmail.password", password))
        chosen.append("gmail")
        print(f"  Reading Gmail for {address}.")

    plugin = _choose_ai(ask, secret, which, plan, paths)
    print(f"  AI: {plugin or NONE_LABEL}.")

    plan.table("schedule", every_minutes=config.Schedule().every_minutes)
    text = plan.text()
    config.parse(text)

    labels = [agents.load(n).label for n in agents.names() if agents.load(n).detect(home)]
    hooks = schedule = ingest_now = False
    if labels:
        hooks = _yes(ask, f"Install the hook in {', '.join(labels)}, so each prompt can see what kb has read?", "yes")
        print(f"  Hook: {'will install' if hooks else 'skipped'}.")
    else:
        print("No agent to hook into was found.")
    if chosen:
        schedule = _yes(ask, f"Read the sources every {config.Schedule().every_minutes} minutes in the background?", "yes")
        print(f"  Background reads: {'will turn on' if schedule else 'off'}.")
        ingest_now = _yes(ask, "Read everything now? The first read can take a while", "yes")
        print(f"  First read: {'will start' if ingest_now else 'later'}.")
    else:
        print("No source was chosen. Add one to the config, then run `kb ingest`.")

    for filename, value in plan.secrets.items():
        _save_secret(paths, filename, value)
    write_file(str(path), text)
    try:
        config.load(str(path))
    except config.ConfigError as exc:
        raise RuntimeError(f"setup wrote {path}, but it does not parse:\n{exc}") from None
    print(f"Wrote {path}.")
    return SetupResult(hooks, schedule, ingest_now, path)


def _save_secret(paths, filename, value):
    folder = paths.keys_dir
    os.makedirs(folder, exist_ok=True)
    os.chmod(folder, 0o700)
    fd = os.open(folder / filename, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write(value + "\n")
    print(f"Saved {filename} in {folder}.")
