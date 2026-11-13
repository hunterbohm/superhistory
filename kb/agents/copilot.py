"""The hook format (userPromptSubmitted, bash, timeoutSec, printed additionalContext added to the user's message) comes
from reports on Copilot CLI 1.0.79 and was not verified on a live install."""
import json
import os
import shutil

from kb import COMMAND, agents
from kb.agents import Agent, Change

EVENT = "userPromptSubmitted"
NAME = "copilot"


def _config(home):
    return os.path.join(os.environ.get("COPILOT_HOME") or os.path.join(home, ".copilot"), "config.json")


def _load(path):
    text = agents.read(path)
    data = agents._parse(text, path)
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict) \
            or not isinstance(data.get("hooks", {}).get(EVENT, []), list):
        raise ValueError(path)
    return text, data, data.get("hooks", {}).get(EVENT, [])


def _is_kb(entry):
    return isinstance(entry, dict) and agents.is_kb_command(entry.get("bash", ""), NAME)


def _unreadable(path):
    return Change(path, "skipped", f"not a JSON object {COMMAND} can edit; fix it and run again")


def detect(home):
    return os.path.isdir(os.path.dirname(_config(home))) or bool(shutil.which("copilot"))


def installed(home):
    try:
        return any(_is_kb(e) for e in _load(_config(home))[2])
    except ValueError:
        return False


def install(home, kb_command):
    path = _config(home)
    try:
        text, data, _ = _load(path)
    except ValueError:
        return [_unreadable(path)]
    entries = data.setdefault("hooks", {}).setdefault(EVENT, [])
    wanted = {"type": "command", "bash": kb_command, "timeoutSec": agents.HOOK_TIMEOUT}
    found = next((e for e in entries if _is_kb(e)), None)
    if found and all(found.get(k) == v for k, v in wanted.items()):
        return [Change(path, "unchanged")]
    if found:
        found.update(wanted)
    else:
        entries.append(wanted)
    problem = agents.checked_write(path, text, agents._dump(data, text), EVENT, _is_kb, [found or wanted], grouped=False)
    if problem:
        return [Change(path, "skipped", f"{problem}; wrote nothing")]
    return [Change(path, "updated" if found else "added")]


def uninstall(home):
    path = _config(home)
    try:
        text, data, entries = _load(path)
    except ValueError:
        return [_unreadable(path)]
    kept = [e for e in entries if not _is_kb(e)]
    if len(kept) == len(entries):
        return [Change(path, "unchanged")]
    if kept:
        data["hooks"][EVENT] = kept
    else:
        del data["hooks"][EVENT]
        if not data["hooks"]:
            del data["hooks"]
    problem = agents.checked_write(path, text, agents._dump(data, text), EVENT, _is_kb, [], grouped=False)
    if problem:
        return [Change(path, "skipped", f"{problem}; wrote nothing")]
    return [Change(path, "removed")]


def request(event):
    prompt = str(event.get("prompt") or "")
    if not prompt.strip():
        return None
    # Copilot passes no transcript, so there are no earlier turns.
    return prompt, [], str(event.get("sessionId") or "")


def reply(text):
    return json.dumps({"additionalContext": text})


AGENT = Agent("GitHub Copilot CLI", detect, installed, install, uninstall, request, reply)
