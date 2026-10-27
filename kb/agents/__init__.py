"""Agent hooks: on every prompt, an agent runs `kb hook <agent>`, which prints the check's records in the form that
agent reads, or nothing.

An agent is a module here that defines AGENT, an Agent:

    detect(home) -> bool                      the agent is installed for this user
    installed(home) -> bool                   kb's hook is in place
    install(home, command) -> [Change]        adds the hook that runs `command`; running it twice changes nothing
    uninstall(home) -> [Change]               removes only kb's hook
    request(event) -> (message, before, session) or None
                                              reads the agent's hook input; None when the check should stay out
    reply(text) -> str                        wraps the records the way the agent reads them on stdout

The module's name, with "-" for "_", is the agent's name in `kb hook` and `kb hooks`.
"""
import importlib
import json
import os
import pkgutil
import shlex
import shutil
import tempfile
import time
from typing import Callable, List, NamedTuple, Optional

from kb import check
from kb.config import MAX_CHECK_BUDGET
from kb.ai.cli import NESTED_ENV

# A few seconds above the check's longest budget, so the agent never cuts the check off.
HOOK_TIMEOUT = int(MAX_CHECK_BUDGET) + 5
# Grok runs the hooks in Claude Code's settings file too, but drops what a prompt hook prints.
GROK_ENV = "GROK_HOOK_EVENT"


class Change(NamedTuple):
    path: str
    action: str
    note: str = ""


class Agent(NamedTuple):
    label: str
    detect: Callable[[str], bool]
    installed: Callable[[str], bool]
    install: Callable[[str, str], List[Change]]
    uninstall: Callable[[str], List[Change]]
    request: Callable[[dict], Optional[tuple]]
    reply: Callable[[str], str]


def names():
    return tuple(sorted(m.name.replace("_", "-") for m in pkgutil.iter_modules(__path__) if not m.name.startswith("_")))


def load(name):
    if name not in names():
        raise KeyError(name)
    return importlib.import_module(f"{__name__}.{name.replace('-', '_')}").AGENT


def command(kb_path, name):
    return f"{shlex.quote(kb_path)} hook {name}"


def is_kb_command(text, name):
    return str(text).rstrip().endswith(f" hook {name}")


def run(paths, config, name, stdin_text):
    if os.environ.get(NESTED_ENV) or os.environ.get(GROK_ENV):
        return ""
    try:
        found = load(name).request(json.loads(stdin_text))
    except Exception:
        return ""
    if not found:
        return ""
    message, before, session = found
    text = check.answer(paths, config, json.dumps({"message": message, "before": before}), session or "")
    return load(name).reply(text) if text else ""


def read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def write(path, text):
    path = os.path.realpath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exists = os.path.exists(path)
    if exists:
        shutil.copy2(path, f"{path}.bak-kb-{time.strftime('%Y%m%d-%H%M%S')}")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".kb-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    if exists:
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def _dump(data, old_text):
    return json.dumps(data, indent=2, ensure_ascii=False) + ("\n" if old_text.endswith("\n") or not old_text else "")


class Unreadable(ValueError):
    pass


def _load(path):
    text = read(path)
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        raise Unreadable(f"{path} is not valid JSON; fix it, then run this again") from None
    if not isinstance(data, dict):
        raise Unreadable(f"{path} does not hold a JSON object")
    return text, data


def add_json_hook(path, event, name, kb_command):
    text, data = _load(path)
    groups = data.setdefault("hooks", {}).setdefault(event, [])
    found = next(((gi, hi, handler) for gi, group in enumerate(groups) for hi, handler in enumerate(group.get("hooks") or [])
                  if is_kb_command(handler.get("command", ""), name)), None)
    wanted = {"type": "command", "command": kb_command, "timeout": HOOK_TIMEOUT}
    if found and all(found[2].get(k) == v for k, v in wanted.items()):
        return (*found, Change(path, "unchanged"))
    if found:
        found[2].update(wanted)
        action = "updated"
    else:
        groups.append({"hooks": [dict(wanted)]})
        found = (len(groups) - 1, 0, groups[-1]["hooks"][0])
        action = "added"
    write(path, _dump(data, text))
    return (*found, Change(path, action))


def has_json_hook(path, event, name):
    try:
        groups = (_load(path)[1].get("hooks") or {}).get(event) or []
    except Unreadable:
        return False
    return any(is_kb_command(h.get("command", ""), name) for group in groups for h in group.get("hooks") or [])


def remove_json_hook(path, event, name):
    text, data = _load(path)
    if not text.strip():
        return Change(path, "unchanged")
    groups = (data.get("hooks") or {}).get(event) or []
    kept = []
    removed = False
    for group in groups:
        handlers = [h for h in group.get("hooks") or [] if not is_kb_command(h.get("command", ""), name)]
        removed = removed or len(handlers) != len(group.get("hooks") or [])
        if handlers:
            kept.append({**group, "hooks": handlers})
    if not removed:
        return Change(path, "unchanged")
    if kept:
        data["hooks"][event] = kept
    else:
        del data["hooks"][event]
        if not data["hooks"]:
            del data["hooks"]
    write(path, _dump(data, text))
    return Change(path, "removed")
