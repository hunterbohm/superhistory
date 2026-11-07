import json
import os
import re
import time

from kb.mask import mask

TAIL_BYTES = 1_000_000
# How far back from the hook's start to look for the prompt's own transcript line.
PROMPT_BYTES = 200_000
# How long, from the hook's start, records may wait for a model request that reads them. Below the hook timeout.
WAIT_SECONDS = 50
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


def _calls(entry):
    """(tool calls opened, tool calls closed) by one transcript line, as ids."""
    if entry.get("isSidechain"):
        return (), ()
    if entry.get("type") in ("assistant", "user"):
        content = (entry.get("message") or {}).get("content")
        parts = [p for p in content if isinstance(p, dict)] if isinstance(content, list) else []
        return ([p.get("id") for p in parts if p.get("type") == "tool_use"],
                [p.get("tool_use_id") for p in parts if p.get("type") == "tool_result"])
    payload = entry.get("payload") or {}
    if entry.get("type") == "response_item" and payload.get("call_id"):
        kind = str(payload.get("type"))
        return ([payload["call_id"]], ()) if kind.endswith("_call") else \
            ((), [payload["call_id"]]) if kind.endswith("_call_output") else ((), ())
    return (), ()


def _replied(entry):
    """Whether a transcript line is the agent's own output in the main conversation: text, thinking or a tool call."""
    if entry.get("isSidechain"):
        return False
    return entry.get("type") == "assistant" or (
        entry.get("type") == "response_item" and (entry.get("payload") or {}).get("type") != "message"
        or (entry.get("payload") or {}).get("role") == "assistant")


def turn_state(transcript_path, prompt, offset):
    """Where the turn this prompt started stands: "ended" (an end line after the prompt's own line), "ready" (a
    tool call is still running, so the agent makes another model request, which reads the records) or "busy"
    (the model is mid-request and may end the turn with it). The turn starts at the prompt's line, the last one
    matching it before `offset` (the transcript's size when the hook began), else at `offset` itself. An
    unreadable transcript counts as ended, since nothing shows the turn still runs."""
    start = max(0, offset - PROMPT_BYTES)
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(start)
            data = fh.read()
    except (OSError, TypeError):
        return "ended"
    entries, at = [], start
    for raw in data.split(b"\n"):
        try:
            entry = json.loads(raw)
            if isinstance(entry, dict):
                entries.append((at, entry))
        except ValueError:
            pass
        at += len(raw) + 1
    prompts = [at for at, entry in entries if _turn(entry) == ("user", prompt.strip())]
    before = [at for at in prompts if at < offset]
    # Never a match after `offset`: that can be the same words sent again after this turn ended.
    anchor = before[-1] if before else offset - 1
    running, answered = set(), False
    for at, entry in entries:
        if at <= anchor:
            continue
        # A new message after the agent replied ends the turn too, however the turn ended (an API error, say).
        if _ends_turn(entry) or (answered and (_turn(entry) or ("",))[0] == "user"):
            return "ended"
        answered = answered or _replied(entry)
        opened, closed = _calls(entry)
        running.update(opened)
        running.difference_update(closed)
    return "ready" if running else "busy"


def await_reader(transcript_path, prompt, offset, deadline, poll=0.25):
    """"ready" once a later model request in the prompt's turn is sure to read records printed now; "ended" when the
    turn ends first; "deadline" when `deadline` (time.monotonic) passes."""
    while True:
        state = turn_state(transcript_path, prompt, offset)
        if state != "busy":
            return state
        if time.monotonic() >= deadline:
            return "deadline"
        time.sleep(poll)
