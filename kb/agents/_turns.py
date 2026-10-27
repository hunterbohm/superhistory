"""The last two conversation turns before a prompt, from a Claude Code or Codex session transcript, so the check can
tell what "this" or "him" means."""
import json
import os
import re

TAIL_BYTES = 1_000_000
BEFORE_CHARS = {"assistant": 1500, "user": 600}
# Turns a harness injects as the user's; nobody typed them.
INJECTED = re.compile(r"^\s*(<task-notification>|<system-reminder>|<command-|<local-command|# AGENTS\.md instructions"
                      r"|<environment_context>|<user_instructions>|<skill>|Caveat:|\[Request interrupted)")


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content
                         if isinstance(part, dict) and part.get("type") in ("text", "input_text", "output_text"))
    return ""


def _turn(entry):
    """(role, text) for a Claude Code or Codex transcript line that holds a conversation turn, else None."""
    if entry.get("type") in ("user", "assistant") and not entry.get("isMeta") and not entry.get("isSidechain"):
        role, text = entry["type"], _text((entry.get("message") or {}).get("content"))
    elif entry.get("type") == "response_item" and (entry.get("payload") or {}).get("type") == "message":
        role, text = entry["payload"].get("role"), _text(entry["payload"].get("content"))
    else:
        return None
    text = text.strip()
    if role not in ("user", "assistant") or not text or (role == "user" and INJECTED.match(text)):
        return None
    return role, text


def turns_before(transcript_path, prompt):
    """The last two turns before the prompt, oldest first, as [{"role", "text"}]."""
    with open(transcript_path, "rb") as fh:
        fh.seek(max(0, os.path.getsize(transcript_path) - TAIL_BYTES))
        lines = fh.read().decode("utf-8", "replace").splitlines()[1:]
    turns = []
    for line in lines:
        try:
            found = _turn(json.loads(line))
        except ValueError:
            continue
        if found and turns and turns[-1][0] == found[0]:
            turns[-1] = (found[0], turns[-1][1] + "\n" + found[1])
        elif found:
            turns.append(found)
    if turns and turns[-1] == ("user", prompt.strip()):
        turns.pop()
    out = []
    for role, text in turns[-2:]:
        limit = BEFORE_CHARS[role]
        out.append({"role": role, "text": text[-limit:] if role == "assistant" else text[:limit]})
    return out
