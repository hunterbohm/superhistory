"""Codex sessions: one item per session, read from rollout files in the Codex sessions folders.

Keeps user and assistant text, tool calls and clipped tool output. Drops reasoning, instructions, injected
context, token counts and Codex's own guardian approval-review sessions. A session resumed later continues in more
files, and the session is still one item. A session is skipped when its working folder matches an exclude pattern.

The exclude key is the session's working folder (cwd) with the home folder written as "~", such as
"~/code/private-client". A pattern without a slash ("scratch") matches any one folder name in it. A session with no
cwd has the key "". Add the folders to skip under `exclude` in the source's config table.
"""
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Tuple

from kb.ai.cli import WORKDIR_PREFIX
from kb.mask import clip, is_auth_code, mask
from kb.sources import Gone, Item, Progress, SourceType, home_key


@dataclass(frozen=True)
class Options:
    roots: Tuple[str, ...] = ("~/.codex/sessions", "~/.codex/archived_sessions")


TOOL_CHARS = 300
ERROR_CHARS = 800
AGENT_CHARS = 800

SKIP_HEAD = (
    '"payload":{"type":"token_count"',
    '"payload":{"type":"reasoning"',
    '"type":"turn_context"',
    '"type":"world_state"',
    '"type":"token_usage_record"',
    '"type":"compacted"',
    '"type":"inter_agent_communication_metadata"',
)
HEAD = 200
TIMESTAMP = re.compile(r'"timestamp":"([^"]+)"')
ORDINAL = re.compile(r'"ordinal":(\d+)')
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# A resumed session continues in rollout-<ts>-<session>_<segment>.jsonl, so the session id is the first uuid.
FILE_ID = re.compile(rf"-({UUID})(?:_{UUID})?\.jsonl$")
INJECTED = (
    "# AGENTS.md instructions", "<environment_context", "<user_instructions", "<recommended_plugins",
    "<in-app-browser-context", "<turn_aborted", "<permissions", "<app-context", "<skills_instructions",
    "<INSTRUCTIONS", "</image>", "<image ",
)
TAG = re.compile(r"</?[A-Za-z_][^>]*>")
REQUEST_MARKER = "## My request for Codex:"
EXIT_CODE = re.compile(r"Process exited with code (\d+)")
FAILED = re.compile(r"Process exited with code [1-9]|exit_code\\?\"?\s*[:=]\s*[1-9]|\"is_error\"\s*:\s*true")


def _clip(text, n):
    return clip(text.strip(), n)


def _parts_text(parts):
    if isinstance(parts, str):
        return parts
    return " ".join(x.get("text", "") for x in parts or [] if isinstance(x, dict) and x.get("type") != "input_image")


def _call_args(arguments):
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except ValueError:
        return str(arguments)
    if not isinstance(args, dict):
        return str(args)
    for key in ("cmd", "command", "path", "file_path", "pattern", "query", "url", "message", "prompt", "text"):
        if key in args:
            value = args[key]
            return " ".join(map(str, value)) if isinstance(value, list) else str(value)
    return json.dumps(args, ensure_ascii=False)


def _output_line(output):
    text = _parts_text(output)
    failed = bool(FAILED.search(text[:2000]))
    if text.startswith("Chunk ID:"):
        end = text.find("Output:\n", 0, 300)
        if end >= 0:
            m = EXIT_CODE.search(text[:end])
            failed = failed or bool(m and m.group(1) != "0")
            text = text[end + len("Output:\n"):]
    if failed:
        return f"ERROR: {_clip(text, ERROR_CHARS)}"
    return f"RESULT: {_clip(text, TOOL_CHARS)}"


def _title(text):
    text = text.split("Payload:", 1)[-1] if text.startswith("Message Type:") else text
    for line in text.splitlines():
        line = TAG.sub("", line).strip()
        if line:
            return mask(line)[:100]
    return ""


def _is_injected(text):
    return text.lstrip().startswith(INJECTED)


def _is_guardian(meta):
    source = meta.get("source")
    return meta.get("thread_source") == "guardian_review" or (
        isinstance(source, dict) and (source.get("subagent") or {}) == {"other": "guardian"}
    )


def _message_lines(payload):
    role = payload.get("role")
    if role not in ("user", "assistant"):
        return
    label = role.upper()
    for part in payload.get("content") or []:
        if not isinstance(part, dict) or part.get("type") not in ("input_text", "output_text"):
            continue
        text = part.get("text", "").strip()
        if text and not (role == "user" and _is_injected(text)) and not is_auth_code(text):
            yield label, text


def _raw_lines(paths):
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as fh:
            yield from fh


def render(paths, mark):
    """The session spread over `paths` as an Item, or None when it holds no conversation."""
    path = paths[0]
    meta = None
    skip_until = None
    first_prompt = fallback_title = None
    last_ts = None
    lines = []
    for raw in _raw_lines(paths):
        prefix = raw[:HEAD]
        m = TIMESTAMP.search(prefix)
        if m:
            last_ts = m.group(1)
        if any(s in prefix for s in SKIP_HEAD):
            continue
        if skip_until is not None:
            m = ORDINAL.search(prefix)
            if m and int(m.group(1)) <= skip_until:
                continue
        try:
            o = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(o, dict):
            continue
        kind = o.get("type")
        payload = o.get("payload")
        if kind == "session_meta" and isinstance(payload, dict):
            if meta is None:
                meta = payload
                if _is_guardian(meta):
                    return None
            elif (skip_until is None and payload.get("id") != meta.get("id")
                    and meta.get("subagent_history_start_ordinal") is not None):
                # A forked file replays the parent's history before the fork point; the parent has its own item.
                skip_until = meta["subagent_history_start_ordinal"]
            continue
        if kind != "response_item" or not isinstance(payload, dict):
            continue
        ptype = payload.get("type")
        if ptype == "message":
            for label, text in _message_lines(payload):
                if label == "USER":
                    if first_prompt is None:
                        first_prompt = text.split(REQUEST_MARKER, 1)[-1].strip() or text
                else:
                    fallback_title = fallback_title or text
                lines.append(f"{label}: {text}")
        elif ptype == "function_call":
            lines.append(f"TOOL {payload.get('name')}: {_clip(_call_args(payload.get('arguments')), TOOL_CHARS)}")
        elif ptype == "custom_tool_call":
            lines.append(f"TOOL {payload.get('name')}: {_clip(str(payload.get('input') or ''), TOOL_CHARS)}")
        elif ptype in ("function_call_output", "custom_tool_call_output"):
            lines.append(_output_line(payload.get("output")))
        elif ptype == "web_search_call":
            action = payload.get("action") or {}
            query = action.get("query") or action.get("url")
            if query:
                lines.append(f"TOOL web_search: {_clip(str(query), TOOL_CHARS)}")
        elif ptype == "agent_message":
            text = _parts_text(payload.get("content")).strip()
            if text:
                if not fallback_title and _title(text):
                    fallback_title = text
                lines.append(f"AGENT {payload.get('author')} -> {payload.get('recipient')}: {_clip(text, AGENT_CHARS)}")
    if not meta or not lines or not last_ts:
        return None
    cwd = meta.get("cwd")
    branch = (meta.get("git") or {}).get("branch")
    title = _title(first_prompt or fallback_title or "") or os.path.basename(path)
    head = f"Codex session in {cwd or '?'}" + (f" ({branch})" if branch else "")
    return Item(
        meta.get("id") or meta.get("session_id") or os.path.basename(path)[:-6], last_ts, title,
        head + "\n\n" + "\n\n".join(lines),
        {"path": path, "cwd": cwd, "branch": branch, "started": meta.get("timestamp")}, mark)


def _candidates(roots, since):
    """Segment paths per session, oldest first, for sessions touched after `since`.

    A session is one item however many files it spans. A file in both sessions/ and archived_sessions/ counts once,
    taking the newer copy.
    """
    sessions = {}
    for root in roots:
        for dirpath, _, names in os.walk(os.path.expanduser(root)):
            for name in names:
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    mtime = os.stat(path).st_mtime
                except OSError:
                    continue
                m = FILE_ID.search(name)
                segments = sessions.setdefault(m.group(1) if m else path, {})
                if name not in segments or mtime > segments[name][0]:
                    segments[name] = (mtime, path)
    for segments in sessions.values():
        if since is None or max(mtime for mtime, _ in segments.values()) > since:
            yield [segments[name][1] for name in sorted(segments)]


def extract(run):
    started = time.time() - 60
    for paths in _candidates(run.options.roots, run.since):
        try:
            item = render(paths, started)
        except OSError:
            continue
        if not item:
            continue
        if run.excludes(home_key(item.meta["cwd"])):
            yield Gone(item.source_id, started)
        else:
            yield item
    yield Progress(started)


TYPE = SourceType(Options, extract, exclude=(WORKDIR_PREFIX + "*",))
