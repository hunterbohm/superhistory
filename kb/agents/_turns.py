import json
import os
import re

from kb.mask import mask

TAIL_BYTES = 1_000_000
# How far back from the hook's start to look for the prompt's own transcript line.
PROMPT_BYTES = 200_000
BEFORE_CHARS = {"assistant": 1500, "user": 600}
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
    with open(transcript_path, "rb") as fh:
        start = max(0, os.path.getsize(transcript_path) - TAIL_BYTES)
        fh.seek(start)
        # JSON may hold U+2028 and other characters splitlines() would split at; a JSONL line ends at a line feed.
        lines = fh.read().decode("utf-8", "replace").split("\n")
        if start:
            lines = lines[1:]
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
        text = mask(text)  # whole, before the cut, so the cut cannot keep a secret without its field name
        out.append({"role": role, "text": text[-limit:] if role == "assistant" else text[:limit]})
    return out


def size(path):
    try:
        return os.path.getsize(path)
    except (OSError, TypeError):
        return None


def _ends_turn(entry):
    """Whether a transcript line written after the prompt shows the agent's turn is over: Claude Code's final
    answer or interrupt note, or Codex's task_complete or turn_aborted."""
    if entry.get("isSidechain"):
        return False
    if entry.get("type") == "assistant":
        return (entry.get("message") or {}).get("stop_reason") == "end_turn"
    if entry.get("type") == "user":
        return _text((entry.get("message") or {}).get("content")).lstrip().startswith("[Request interrupted")
    if entry.get("type") == "event_msg":
        return (entry.get("payload") or {}).get("type") in ("task_complete", "turn_aborted")
    return False


def turn_ended(transcript_path, prompt, offset):
    """Whether the turn this prompt started is over: an end line after the prompt's own line, or after `offset`
    (the transcript's size when the hook began) if the prompt's line is not found. An unreadable transcript counts
    as over, since nothing shows the turn still runs."""
    start = max(0, offset - PROMPT_BYTES)
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(start)
            data = fh.read()
    except OSError:
        return True
    lines, at, after = [], start, offset - 1
    for raw in data.split(b"\n"):
        lines.append((at, raw))
        at += len(raw) + 1
    for at, raw in lines:
        try:
            if _turn(json.loads(raw)) == ("user", prompt.strip()):
                after = at
        except (ValueError, AttributeError):
            continue
    for at, raw in lines:
        if at <= after:
            continue
        try:
            if _ends_turn(json.loads(raw)):
                return True
        except (ValueError, AttributeError):
            continue
    return False
