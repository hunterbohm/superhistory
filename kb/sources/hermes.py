"""Hermes sessions: one item per session in <root>/state.db and each <root>/profiles/<name>/state.db.

state.db is the canonical record. The older sessions/*.jsonl and session_*.json files hold the same conversations
under other session ids, so reading both would index every early conversation twice. Profile databases share no
session ids with the default one. Databases open read-only.

Exclude key: "<profile>/<platform>", such as "default/discord" or "work/imessage". The root database is the profile
"default". The platform is Hermes's own name for the chat service ("bluebubbles", "cron", "tool"), and a session on
a service that Hermes reaches through a bridge (bluebubbles, photon) also matches as "imessage", the label its title
shows. So "tool" drops the synthetic tool platform in every profile and "work" drops a whole profile.
"""
import glob
import json
import os
import pathlib
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from kb.mask import clip, is_auth_noise, mask, mask_all
from kb.sources import Item, Progress, SourceType

TOOL_CHARS = 300
USER_CHARS = 3000
BATCH_CHARS = 600
RESULT_CHARS = 200
ERROR_CHARS = 800
PLATFORM = {"bluebubbles": "imessage", "photon": "imessage"}
QUIET_TOOLS = {"skill_view", "skills_list", "tool_describe"}
# Gateway and compaction injections, not words the user or Hermes said.
DROP_PREFIXES = (
    "[CONTEXT COMPACTION", "[PRIOR CONTEXT", "[Your active task list", "[System note:", "[System:",
    "[STILL IN PROGRESS", "[IMPORTANT: Background process", "You've reached the max",
    "⏩ Steered", "Operation interrupted", "[SILENT]", "∅", "<t3_code_instructions>", "✔ [", "✖ [",
    "⚡ Interrupting",
)
CRON_PREAMBLE = re.compile(r"\[IMPORTANT: You are running as a scheduled cron job\..*?\]\s*\n", re.S)
PREVIOUS_RUN = re.compile(r"## Your previous run's output\n.*?```\n.*?```\s*", re.S)
SKILL_INVOKED = re.compile(r'^\[IMPORTANT: The user has invoked the "([^"]+)" skill')
OUT_OF_BAND = re.compile(
    r"^\[OUT-OF-BAND USER MESSAGE[^\]]*\]\s*(?:Gateway message origin.*?insufficient\.\s*)?(.*?)\s*\[/OUT-OF-BAND USER MESSAGE\]",
    re.S)
# The gateway tags a message with the sender's name ("[Sam] text").
WRAPPERS = re.compile(
    r"^(?:\[Triggering message id[^\]]*\]|\[[^\]\n:]{1,40}\] (?=\[)|"
    r"Gateway message origin \(JSON data.*?insufficient\.)\s*", re.S)


@dataclass(frozen=True)
class Options:
    root: str = "~/.hermes"


def _clip(text, n):
    text = " ".join(mask(text).split())  # masked first: joining would break a secret around a U+2028 in two
    return clip(text, n)


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unwrap(text):
    while True:
        stripped = WRAPPERS.sub("", text)
        if stripped == text:
            return text
        text = stripped


def clean_text(text, role):
    """The words actually said, or "" for an injected or empty message."""
    text = (text or "").strip()
    if role == "user":
        skill = SKILL_INVOKED.match(text)
        if skill:
            tail = CRON_PREAMBLE.split(text, maxsplit=1)[1] if CRON_PREAMBLE.search(text) else ""
            return f"[skill {skill.group(1)}] {PREVIOUS_RUN.sub('', tail).strip()}".strip()
        text = _unwrap(text)
        oob = OUT_OF_BAND.match(text)
        if oob:
            text = _unwrap(oob.group(1))
        text = PREVIOUS_RUN.sub("", CRON_PREAMBLE.sub("", text)).strip()
        if text.startswith(("[ASYNC DELEGATION BATCH", "## Script Output", "## Script Error")):
            text = _clip(text, BATCH_CHARS)
        elif len(text) > USER_CHARS:
            text = clip(text, USER_CHARS)
    return "" if text.startswith(DROP_PREFIXES) else text


def _tool_input(args):
    try:
        args = json.loads(args)
    except (TypeError, ValueError):
        return str(args)
    if not isinstance(args, dict):
        return str(args)
    for key in ("command", "path", "file_path", "query", "url", "pattern", "goal", "name", "message"):
        if key in args:
            return str(args[key])
    return json.dumps(mask_all(args), ensure_ascii=False)  # masked by field, before JSON escapes them


def _tool_calls(raw):
    try:
        calls = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(calls, list):
        return []
    return [f"TOOL {(c.get('function') or {}).get('name')}: {_clip(_tool_input((c.get('function') or {}).get('arguments')), TOOL_CHARS)}"
            for c in calls if isinstance(c, dict)]


def _tool_result(content, name):
    if name in QUIET_TOOLS:
        return None
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict):
        if data.get("error"):
            return f"ERROR: {_clip(str(data['error']), ERROR_CHARS)}"
        content = str(data.get("output", data.get("result", content)))
    return f"RESULT: {_clip(str(content or ''), RESULT_CHARS)}"


def _columns(db, table):
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def render(db, profile, db_path, row, superseded=""):
    """The Item for one session row, or None when nobody said anything or nothing answered.
    `superseded` is an SQL condition for message rows to skip (see _superseded)."""
    sid, source, title, started, origin, display_name = row
    lines, first_prompt, last_ts, answered = [], None, started, False
    seen = set()
    for role, content, calls, tool_name, ts, kind, summary in db.execute(
            "SELECT role, content, tool_calls, tool_name, timestamp, display_kind, _compressed_summary "
            f"FROM messages WHERE session_id=? AND role IN ('user','assistant','tool'){superseded} ORDER BY id",
            (sid,)):
        # Compaction clones the recent rows into new ids with the same timestamps and content.
        identity = (role, content, calls, ts)
        if identity in seen:
            continue
        seen.add(identity)
        if summary or kind in ("hidden", "internal_notification", "model_switch", "auto_continue",
                               "failed_turn", "process_complete"):
            continue
        last_ts = max(last_ts, ts)
        if role == "tool":
            line = _tool_result(content, tool_name)
            if line and not is_auth_noise(line):
                lines.append(line)
            continue
        text = clean_text(content, role)
        if text and is_auth_noise(text):
            text = ""
        if text:
            if role == "user":
                first_prompt = first_prompt or text
                lines.append(f"USER: {text}")
            else:
                answered = True
                lines.append(f"ASSISTANT: {text}")
        if role == "assistant":
            lines.extend(_tool_calls(calls))
    if not first_prompt or not answered:
        return None
    platform = PLATFORM.get(source, source)
    chat = display_name
    try:
        chat = chat or json.loads(origin or "{}").get("chat_name")
    except (ValueError, AttributeError):
        pass
    if source == "cron" and sid.startswith("cron_"):
        chat = chat or "job " + sid.split("_")[1]
    first_line = mask(first_prompt.replace("\n", " "))[:100]
    title = clean_text(title, "user") or first_line
    head = f"Hermes {platform} session, profile {profile}" + (f", {chat}" if chat else "")
    return {
        "source_id": f"{profile}:{sid}",
        "ts": _iso(last_ts),
        "title": f"[{profile}/{platform}] {title}",
        "content": head + "\n\n" + "\n\n".join(lines),
        "meta": {"path": f"{db_path}#{sid}", "profile": profile, "platform": source, "session_id": sid,
                 "started": _iso(started)},
    }


def _stores(root):
    yield "default", os.path.join(root, "state.db")
    for path in sorted(glob.glob(os.path.join(root, "profiles", "*", "state.db"))):
        yield os.path.basename(os.path.dirname(path)), path


def _superseded(message_columns):
    # active=0 with compacted=1 are the originals a summary replaced: the only copy of that conversation, so kept.
    # active=0 with compacted=0 are rewound or superseded rows, copies of live ones.
    if {"active", "compacted"} <= message_columns:
        return " AND NOT (active=0 AND compacted=0)"
    return ""


def _excluded(excludes, profile, source):
    return excludes(f"{profile}/{source}") or excludes(f"{profile}/{PLATFORM.get(source, source)}")


def extract(run):
    # Message rowids are per store, so the shared watermark is time: sessions with a message newer than it.
    # A compaction clones old messages with their old timestamps, so a session's own last_activity_at also counts.
    started = time.time() - 60
    since = run.since
    root = os.path.expanduser(run.options.root)
    for profile, path in _stores(root):
        if run.excludes(profile) or not os.path.exists(path):
            continue
        db = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True, timeout=30)
        try:
            query = "SELECT id, source, title, started_at, origin_json, display_name FROM sessions"
            args = []
            if since is not None:
                touched = "id IN (SELECT session_id FROM messages WHERE timestamp > ?)"
                args.append(since)
                if "last_activity_at" in _columns(db, "sessions"):
                    touched += " OR last_activity_at > ?"
                    args.append(since)
                query += f" WHERE {touched}"
            superseded = _superseded(_columns(db, "messages"))
            for row in db.execute(query, args).fetchall():
                if _excluded(run.excludes, profile, row[1]):
                    continue
                item = render(db, profile, path, row, superseded)
                if item:
                    yield Item(item["source_id"], item["ts"], item["title"], item["content"], item["meta"], started)
        finally:
            db.close()
    yield Progress(started)


TYPE = SourceType(Options, extract, exclude=("tool",))
