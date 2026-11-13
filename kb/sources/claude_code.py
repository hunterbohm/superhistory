"""Claude Code sessions: one item per main session file in <root>/<project>/<session>.jsonl.

Subagent transcripts (<session>/subagents/*.jsonl) are skipped, and so are sidechain and meta records, system
reminders and unrecognised record types. A session is skipped when its working folder matches an exclude pattern.

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
from kb.mask import clip, is_auth_code, mask, mask_all, typed_secrets
from kb.sources import Gone, Item, Progress, SourceType, home_key

TOOL_CHARS = 300
ERROR_CHARS = 800
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


@dataclass(frozen=True)
class Options:
    roots: Tuple[str, ...] = ("~/.claude/projects",)


def _clip(text, n):
    return clip(text.strip(), n)


def _tool_input(inp, name=None):
    if not isinstance(inp, dict):
        return str(inp)
    typed = typed_secrets(name, inp)
    if typed != inp:  # a secret it typed, masked, with the field it typed into; else as before
        return json.dumps(mask_all(typed), ensure_ascii=False)
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt"):
        if key in inp:
            return str(inp[key])
    return json.dumps(mask_all(inp), ensure_ascii=False)  # masked by field, before JSON escapes them


def _result_text(part):
    content = part.get("content")
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content if isinstance(x, dict))
    return str(content or "")


def render(path, mark):
    """The session in `path` as an Item, or None when it holds no conversation."""
    title = first_prompt = cwd = branch = session = None
    first_ts = last_ts = None
    lines = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            kind = o.get("type")
            if kind == "ai-title":
                title = o.get("aiTitle") or title
                continue
            if kind == "summary" and not title:
                title = o.get("summary")
                continue
            if kind not in ("user", "assistant") or o.get("isSidechain") or o.get("isMeta"):
                continue
            ts = o.get("timestamp")
            if ts:
                first_ts = first_ts or ts
                last_ts = ts
            cwd = cwd or o.get("cwd")
            branch = branch or o.get("gitBranch")
            session = session or o.get("sessionId")
            content = (o.get("message") or {}).get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            for part in content or []:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "text":
                    text = REMINDER.sub("", part.get("text", "")).strip()
                    if not text or is_auth_code(text):
                        continue
                    if kind == "user":
                        first_prompt = first_prompt or text
                        lines.append(f"USER: {text}")
                    else:
                        lines.append(f"ASSISTANT: {text}")
                elif ptype == "tool_use":
                    tool_input = _tool_input(part.get("input"), part.get("name"))
                    lines.append(f"TOOL {part.get('name')}: {_clip(tool_input, TOOL_CHARS)}")
                elif ptype == "tool_result":
                    if part.get("is_error"):
                        lines.append(f"ERROR: {_clip(_result_text(part), ERROR_CHARS)}")
                    else:
                        lines.append(f"RESULT: {_clip(_result_text(part), TOOL_CHARS)}")
    if not first_prompt or not last_ts:
        return None
    title = title or mask(first_prompt.splitlines()[0])[:100]
    head = f"Claude Code session in {cwd or '?'}" + (f" ({branch})" if branch else "")
    return Item(
        session or os.path.basename(path)[:-6], last_ts, title, head + "\n\n" + "\n\n".join(lines),
        {"path": path, "cwd": cwd, "branch": branch, "started": first_ts}, mark)


def _session_files(root, since):
    """Paths of the main session files changed after `since`."""
    try:
        projects = [p for p in os.scandir(root) if p.is_dir()]
    except OSError:
        return
    for project in projects:
        try:
            entries = list(os.scandir(project.path))
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.name.endswith(".jsonl") or not entry.is_file():
                    continue
                if since is not None and entry.stat().st_mtime <= since:
                    continue
            except OSError:
                continue
            yield entry.path


def extract(run):
    started = time.time() - 60
    for root in run.options.roots:
        for path in _session_files(os.path.expanduser(root), run.since):
            try:
                item = render(path, started)
            except OSError:
                continue
            if not item:
                continue
            if run.excludes(home_key(item.meta["cwd"])):
                yield Gone(item.source_id, started)
            else:
                yield item
    yield Progress(started)


TYPE = SourceType(Options, extract, exclude=(WORKDIR_PREFIX + "*",), reads_files=True)
