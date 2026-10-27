"""Hermes appends the {"context": text} a plugin's pre_llm_call hook returns to the user message."""
import json
import os
import shutil
import subprocess

from kb import agents
from kb.agents import Agent, _turns

PLUGIN = "kb-context"
MARKER = "kb-managed Hermes plugin"
PLATFORMS = {"discord", "desktop", "tui", "cli", "acp", "imessage", "photon", "bluebubbles", "telegram", "api_server"}

PLUGIN_INIT = '''# ''' + MARKER + '''
import json
import shlex
import subprocess

COMMAND = @COMMAND@
TIMEOUT = @TIMEOUT@
ARGUMENTS = ("session_id", "user_message", "platform", "parent_session_id", "conversation_history")


def _on_pre_llm_call(**kwargs):
    try:
        proc = subprocess.run(shlex.split(COMMAND), input=json.dumps({k: kwargs.get(k) for k in ARGUMENTS}, default=str),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT)
        context = proc.stdout.strip() if proc.returncode == 0 else ""
        return {"context": context} if context else None
    except Exception:
        return None


def register(ctx):
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
'''

PLUGIN_YAML = '''name: kb-context
version: "1"
description: Adds a few knowledge-base records related to each message the user sends, looked up by kb, or nothing when none relate.
provides_hooks:
  - pre_llm_call
'''


def _folder(home):
    return os.path.join(home, ".hermes", "plugins", PLUGIN)


def _init(home):
    return os.path.join(_folder(home), "__init__.py")


def plugin_source(kb_command):
    return agents.fill(PLUGIN_INIT, {"@COMMAND@": repr(kb_command), "@TIMEOUT@": str(agents.HOOK_TIMEOUT)})


def _ours(home):
    return MARKER in agents.read(_init(home))


def _hermes(action):
    binary = shutil.which("hermes")
    if not binary:
        return None
    try:
        proc = subprocess.run([binary, "plugins", action, PLUGIN], capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()


def detect(home):
    return os.path.isdir(os.path.join(home, ".hermes"))


def installed(home):
    return _ours(home)


def install(home, kb_command):
    init = _init(home)
    if agents.read(init) and not _ours(home):
        return [agents.Change(init, "skipped", f"a plugin named {PLUGIN} that kb did not write is already there")]
    changes = []
    for path, text in ((init, plugin_source(kb_command)), (os.path.join(_folder(home), "plugin.yaml"), PLUGIN_YAML)):
        old = agents.read(path)
        if old != text:
            agents.write(path, text)
        changes.append(agents.Change(path, "unchanged" if old == text else "updated" if old else "added"))
    enabled = _hermes("enable")
    if enabled is None:
        note = f"run `hermes plugins enable {PLUGIN}` to turn it on; Hermes picks the plugin up on its next start"
    elif enabled[0]:
        note = "enabled; Hermes picks the plugin up on its next start"
    else:
        note = f"`hermes plugins enable {PLUGIN}` failed ({enabled[1]}); run it yourself"
    return changes[:-1] + [changes[-1]._replace(note=note)]


def uninstall(home):
    folder = _folder(home)
    if not os.path.exists(_init(home)):
        return [agents.Change(folder, "unchanged")]
    if not _ours(home):
        return [agents.Change(folder, "skipped", f"the {PLUGIN} plugin there was not written by kb")]
    _hermes("disable")
    shutil.rmtree(folder)
    return [agents.Change(folder, "removed")]


def _text(message):
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return " ".join(part["text"] for part in message
                        if isinstance(part, dict) and isinstance(part.get("text"), str))
    return ""


def _turns_before(history, text):
    turns = []
    for message in history or []:
        role = message.get("role") if isinstance(message, dict) else None
        body = _text(message.get("content")).strip() if role in ("user", "assistant") else ""
        if not body:
            continue
        if turns and turns[-1][0] == role:
            turns[-1] = (role, turns[-1][1] + "\n" + body)
        else:
            turns.append((role, body))
    if turns and turns[-1] == ("user", text.strip()):
        turns.pop()
    limits = _turns.BEFORE_CHARS
    return [{"role": role, "text": body[-limits[role]:] if role == "assistant" else body[:limits[role]]}
            for role, body in turns[-2:]]


def request(event):
    if str(event.get("platform") or "").lower() not in PLATFORMS or event.get("parent_session_id"):
        return None
    text = _text(event.get("user_message"))
    if not text.strip():
        return None
    return text, _turns_before(event.get("conversation_history"), text), str(event.get("session_id") or "")


def reply(text):
    return text


AGENT = Agent("Hermes", detect, installed, install, uninstall, request, reply)
