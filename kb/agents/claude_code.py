import json
import os

from kb import agents
from kb.agents import Agent, _turns

EVENT = "UserPromptSubmit"


def _settings(home):
    return os.path.join(home, ".claude", "settings.json")


def detect(home):
    return os.path.isdir(os.path.join(home, ".claude"))


def installed(home):
    return agents.has_json_hook(_settings(home), EVENT, "claude-code")


def install(home, kb_command):
    return [agents.add_json_hook(_settings(home), EVENT, "claude-code", kb_command, background=True)[3]]


def uninstall(home):
    return [agents.remove_json_hook(_settings(home), EVENT, "claude-code")]


def request(event):
    prompt = str(event.get("prompt") or "")
    try:
        before = _turns.turns_before(event["transcript_path"], prompt) if event.get("transcript_path") else []
    except OSError:
        before = []
    return prompt, before, str(event.get("session_id") or "")


def reply(text):
    return json.dumps({"hookSpecificOutput": {"hookEventName": EVENT, "additionalContext": text}})


AGENT = Agent("Claude Code", detect, installed, install, uninstall, request, reply)
