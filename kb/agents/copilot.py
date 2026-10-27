"""GitHub Copilot CLI: a userPromptSubmitted command hook in ~/.copilot/config.json ($COPILOT_HOME when set). The
additionalContext the hook prints is added to the user's message. This format comes from reports on Copilot CLI 1.0.79
and was not verified on a live install."""
import json
import os
import shutil

from kb import agents
from kb.agents import Agent, Change

EVENT = "userPromptSubmitted"
NAME = "copilot"


def _config(home):
    return os.path.join(os.environ.get("COPILOT_HOME") or os.path.join(home, ".copilot"), "config.json")


def _load(path):
    """(text, config, the event's hook list); raises ValueError when the file is not a config kb can edit."""
    text = agents.read(path)
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict) \
            or not isinstance(data.get("hooks", {}).get(EVENT, []), list):
        raise ValueError(path)
    return text, data, data.get("hooks", {}).get(EVENT, [])


def _is_kb(entry):
    return isinstance(entry, dict) and agents.is_kb_command(entry.get("bash", ""), NAME)


def _unreadable(path):
    return Change(path, "skipped", "not a JSON object kb can edit; fix it and run again")


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
    agents.write(path, agents._dump(data, text))
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
    agents.write(path, agents._dump(data, text))
    return [Change(path, "removed")]


def request(event):
    """The prompt and session id; Copilot passes no transcript, so no turns before."""
    prompt = str(event.get("prompt") or "")
    if not prompt.strip():
        return None
    return prompt, [], str(event.get("sessionId") or "")


def reply(text):
    return json.dumps({"additionalContext": text})


AGENT = Agent("GitHub Copilot CLI", detect, installed, install, uninstall, request, reply)
