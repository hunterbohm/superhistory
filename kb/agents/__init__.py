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
import copy
import json
import math
import os
import pkgutil
import re
import shlex
import shutil
import tempfile
import time
from typing import Callable, List, NamedTuple, Optional

from kb import check
from kb.agents import _turns
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
    """Whether a hook command is exactly the one command() writes for this agent: a program named kb, "hook", name."""
    try:
        argv = shlex.split(str(text))
    except ValueError:
        return False
    return len(argv) == 3 and argv[1:] == ["hook", name] and os.path.basename(argv[0]) == "kb"


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
    event = json.loads(stdin_text)
    transcript = event.get("transcript_path") if isinstance(event, dict) else None
    offset = _turns.size(transcript)
    text = check.answer(paths, config, json.dumps({"message": message, "before": before}), session or "")
    # A hook run in the background can finish after the agent's turn; its records would then come with the next
    # message, which is about something else.
    if text and offset is not None and _turns.turn_ended(transcript, offset):
        return ""
    return load(name).reply(text) if text else ""


def fill(template, values):
    """Puts each value in for its placeholder in one pass, so a value that holds a placeholder, such as a kb path
    with one in a folder name, stays as it is."""
    return re.sub("|".join(map(re.escape, values)), lambda m: values[m.group()], template)


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
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup, n = f"{path}.bak-kb-{stamp}", 1
        while os.path.exists(backup):
            backup, n = f"{path}.bak-kb-{stamp}-{n}", n + 1
        shutil.copy2(path, backup)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".kb-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    if exists:
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def _dump(data, old_text):
    return (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
            + ("\n" if old_text.endswith("\n") or not old_text else ""))


class Unreadable(ValueError):
    pass


def _load(path):
    text = read(path)
    return text, _parse(text, path)


def _finite(number):
    value = float(number)
    if not math.isfinite(value):
        raise ValueError(f"{number} does not fit in a JSON number written back")
    return value


def _not_json(name):
    raise ValueError(f"{name} is not JSON")


def _parse(text, path):
    """The JSON object in `text`, read strictly: a number such as 1e400, or NaN, would not write back as it came."""
    try:
        data = json.loads(text, parse_float=_finite, parse_constant=_not_json) if text.strip() else {}
    except ValueError:
        raise Unreadable(f"{path} is not valid JSON kb can write back; fix it, then run this again") from None
    if not isinstance(data, dict):
        raise Unreadable(f"{path} does not hold a JSON object")
    return data


def without_kb(data, event, is_kb, grouped=True):
    """The parsed file as the agent sees it apart from kb: kb's entries out, a group they alone filled out too, and
    an empty event list or hooks table counted as absent."""
    data = copy.deepcopy(data)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict) or not isinstance(hooks.get(event), list):
        return data
    entries = []
    for entry in hooks[event]:
        if not grouped:
            if not is_kb(entry):
                entries.append(entry)
            continue
        handlers = entry.get("hooks") if isinstance(entry, dict) else None
        if not isinstance(handlers, list):
            entries.append(entry)
            continue
        others = [h for h in handlers if not is_kb(h)]
        if others or not handlers:
            entries.append({**entry, "hooks": others})
    hooks[event] = entries
    if not entries:
        del hooks[event]
    if not hooks:
        del data["hooks"]
    return data


def checked_write(path, old, new, event, is_kb, kb_after, grouped=True):
    """Writes `new` over `old` only if it parses and means what `old` did apart from kb's entries, which must come out
    as `kb_after`. Returns None when written or unchanged, else why it was not."""
    if new == old:
        return None
    try:
        before, after = _parse(old, path), _parse(new, path)
    except Unreadable as exc:
        return str(exc)
    if without_kb(after, event, is_kb, grouped) != without_kb(before, event, is_kb, grouped):
        return "the edit would change more than kb's own entries"
    entries = (after.get("hooks") or {}).get(event) or []
    found = [h for e in entries for h in ((e.get("hooks") or []) if grouped else [e]) if is_kb(h)]
    if found != kb_after:
        return "kb's entry did not come out as intended"
    write(path, new)
    return None


def _is_kb_handler(name):
    return lambda handler: isinstance(handler, dict) and is_kb_command(handler.get("command", ""), name)


def add_json_hook(path, event, name, kb_command, background=False):
    text = read(path)
    new, gi, hi, handler, action = json_hook_added(text, path, event, name, kb_command, background)
    problem = checked_write(path, text, new, event, _is_kb_handler(name), [handler])
    if problem:
        raise Unreadable(f"{problem}; wrote nothing")
    return gi, hi, handler, Change(path, action)


def json_hook_added(text, path, event, name, kb_command, background=False):
    """The file's text with kb's hook added or brought up to date: (text, group, position, handler, action).
    background: the agent runs the hook without waiting for it ("async") and gives its records to the next model
    request, so the user's message goes through at once."""
    data = _parse(text, path)
    groups = data.setdefault("hooks", {}).setdefault(event, [])
    found = next(((gi, hi, handler) for gi, group in enumerate(groups) for hi, handler in enumerate(group.get("hooks") or [])
                  if is_kb_command(handler.get("command", ""), name)), None)
    wanted = {"type": "command", "command": kb_command, "timeout": HOOK_TIMEOUT}
    if background:
        wanted["async"] = True
    if found and all(found[2].get(k) == v for k, v in wanted.items()):
        return (text, *found, "unchanged")
    if found:
        found[2].update(wanted)
        action = "updated"
    else:
        groups.append({"hooks": [dict(wanted)]})
        found = (len(groups) - 1, 0, groups[-1]["hooks"][0])
        action = "added"
    return (_dump(data, text), *found, action)


def has_json_hook(path, event, name):
    try:
        groups = (_load(path)[1].get("hooks") or {}).get(event) or []
    except Unreadable:
        return False
    return any(is_kb_command(h.get("command", ""), name) for group in groups for h in group.get("hooks") or [])


def remove_json_hook(path, event, name):
    text = read(path)
    new, action = json_hook_removed(text, path, event, name)
    problem = checked_write(path, text, new, event, _is_kb_handler(name), [])
    if problem:
        raise Unreadable(f"{problem}; wrote nothing")
    return Change(path, action)


def json_hook_removed(text, path, event, name):
    """The file's text without kb's hook: (text, action)."""
    data = _parse(text, path)
    if not text.strip():
        return text, "unchanged"
    groups = (data.get("hooks") or {}).get(event) or []
    kept = []
    removed = False
    for group in groups:
        handlers = [h for h in group.get("hooks") or [] if not is_kb_command(h.get("command", ""), name)]
        if len(handlers) == len(group.get("hooks") or []):
            kept.append(group)
            continue
        removed = True
        if handlers:
            kept.append({**group, "hooks": handlers})
    if not removed:
        return text, "unchanged"
    if kept:
        data["hooks"][event] = kept
    else:
        del data["hooks"][event]
        if not data["hooks"]:
            del data["hooks"]
    return _dump(data, text), "removed"
