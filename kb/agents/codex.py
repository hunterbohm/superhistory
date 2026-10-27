"""Codex does not run a hook until the user has reviewed it with /hooks. That review stores a hash of the hook under
[hooks.state."<hooks.json>:user_prompt_submit:<group>:<handler>"], so install writes the same entry and the hook
runs at once. Codex sends the same prompt, transcript_path and session_id fields as Claude Code and reads the same
hookSpecificOutput.additionalContext reply, so the request and reply are Claude Code's.
"""
import hashlib
import json
import os

from kb import agents
from kb.config import read_toml
from kb.agents import Agent, claude_code

EVENT = "UserPromptSubmit"
CODEX_EVENT = "user_prompt_submit"
CODEX_FIELDS = {"type", "command", "commandWindows", "timeout", "async", "statusMessage"}

request = claude_code.request
reply = claude_code.reply


def _hooks_json(home):
    return os.path.join(home, ".codex", "hooks.json")


def _config(home):
    return os.path.join(home, ".codex", "config.toml")


def detect(home):
    return os.path.isdir(os.path.join(home, ".codex"))


def codex_hook_hash(event, handler, matcher=None):
    """The trusted_hash Codex's /hooks review stores: sha256 over the hook's normalized identity as compact,
    key-sorted JSON (hook_hash and version_for_toml in openai/codex, rust-v0.163.0). Codex drops the matcher
    for UserPromptSubmit, Stop and Interrupt, and commandWindows off Windows."""
    timeout = handler.get("timeout")
    hook = {"type": "command", "command": handler["command"], "async": bool(handler.get("async", False)),
            "timeout": 600 if timeout is None else max(int(timeout), 1)}
    if handler.get("statusMessage") is not None:
        hook["statusMessage"] = handler["statusMessage"]
    identity = {"event_name": event, "hooks": [hook]}
    if matcher is not None:
        identity["matcher"] = matcher
    data = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def _key(hooks_json, group, handler):
    # Codex keys trust by the canonical path (a CODEX_HOME under /tmp shows up as /private/tmp).
    return f"{os.path.realpath(hooks_json)}:{CODEX_EVENT}:{group}:{handler}"


def _header(key):
    # A TOML basic string: JSON's escapes are TOML's, except that an emoji must stay literal (TOML has no surrogate
    # pairs) and DEL must be escaped.
    return "[hooks.state." + json.dumps(key, ensure_ascii=False).replace("\x7f", "\\u007f") + "]"


def _is_header(line, key):
    """Whether a line opens this key's table, however the TOML spells the key: either quotes, escaped or literal
    characters such as an accented letter in a home folder, spaces around the dots, a trailing comment."""
    line = line.strip()
    if not line.startswith("[") or line.startswith("[["):
        return False
    try:
        return read_toml(line + "\n") == {"hooks": {"state": {key: {}}}}
    except ValueError:
        return False


def _section(lines, key):
    start = next((i for i, line in enumerate(lines) if _is_header(line, key)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    return start, end


def _trusted_line(lines, start, end):
    return next((i for i in range(start + 1, end) if lines[i].split("=")[0].strip() == "trusted_hash"), None)


def trust(path, key, digest):
    text = agents.read(path)
    entry = f'trusted_hash = "{digest}"'
    lines = text.splitlines(keepends=True)
    section = _section(lines, key)
    if section is None:
        if text and not text.endswith("\n"):
            text += "\n"
        new = text + ("\n" if text else "") + f"{_header(key)}\n{entry}\n"
        action = "added"
    else:
        start, end = section
        found = _trusted_line(lines, start, end)
        if found is not None and lines[found].strip() == entry:
            return "unchanged"
        if found is None:
            lines.insert(start + 1, entry + "\n")
        else:
            lines[found] = entry + "\n"
        new = "".join(lines)
        action = "updated"
    if read_toml(new)["hooks"]["state"][key]["trusted_hash"] != digest:
        raise ValueError(f"{path}: the trust entry did not parse back")
    agents.write(path, new)
    return action


def _untrust(path, key):
    lines = agents.read(path).splitlines(keepends=True)
    section = _section(lines, key)
    if section is None:
        return False
    start, end = section
    if end == len(lines) and start > 0 and not lines[start - 1].strip():
        start -= 1
    agents.write(path, "".join(lines[:start] + lines[end:]))
    return True


def _is_trusted(path, key, digest):
    """Whether config.toml trusts this hook position with this hash, however the TOML is written."""
    try:
        data = read_toml(agents.read(path))
    except ValueError:
        return False
    entry = ((data.get("hooks") or {}).get("state") or {}).get(key)
    return isinstance(entry, dict) and entry.get("trusted_hash") == digest


def _find(hooks_json):
    text = agents.read(hooks_json)
    data = json.loads(text) if text.strip() else {}
    groups = (data.get("hooks") or {}).get(EVENT) or []
    return next(((gi, hi, handler) for gi, group in enumerate(groups) for hi, handler in enumerate(group.get("hooks") or [])
                 if agents.is_kb_command(handler.get("command", ""), "codex")), None)


def installed(home):
    try:
        found = _find(_hooks_json(home))
    except ValueError:
        return False
    if not found:
        return False
    gi, hi, handler = found
    if set(handler) - CODEX_FIELDS:
        return True
    return _is_trusted(_config(home), _key(_hooks_json(home), gi, hi), codex_hook_hash(CODEX_EVENT, handler))


def install(home, kb_command):
    hooks_json, config = _hooks_json(home), _config(home)
    try:
        gi, hi, handler, change = agents.add_json_hook(hooks_json, EVENT, "codex", kb_command)
    except ValueError as exc:
        return [agents.Change(hooks_json, "skipped", f"not valid JSON ({exc}); left as it is")]
    key = _key(hooks_json, gi, hi)
    if set(handler) - CODEX_FIELDS:
        return [change, agents.Change(config, "skipped", f"the hook has fields kb cannot hash; trust it once with "
                                                         f"/hooks in Codex ({key})")]
    try:
        action = trust(config, key, codex_hook_hash(CODEX_EVENT, handler))
    except ValueError as exc:
        return [change, agents.Change(config, "skipped", f"could not record trust ({exc}); trust the hook once with "
                                                         f"/hooks in Codex")]
    return [change, agents.Change(config, action, f"trusts {key}")]


def _positions(hooks_json):
    text = agents.read(hooks_json)
    groups = ((json.loads(text) if text.strip() else {}).get("hooks") or {}).get(EVENT) or []
    return [(gi, hi, handler) for gi, group in enumerate(groups) for hi, handler in enumerate(group.get("hooks") or [])]


def uninstall(home):
    hooks_json, config = _hooks_json(home), _config(home)
    try:
        before = _positions(hooks_json)
        change = agents.remove_json_hook(hooks_json, EVENT, "codex")
        after = _positions(hooks_json)
    except ValueError as exc:
        return [agents.Change(hooks_json, "skipped", f"not valid JSON ({exc}); left as it is")]
    mine = [(gi, hi) for gi, hi, h in before if agents.is_kb_command(h.get("command", ""), "codex")]
    if not mine:
        return [change]
    try:
        removed = [_untrust(config, _key(hooks_json, gi, hi)) for gi, hi in mine]
        moved = _move_trust(config, hooks_json, before, after)
    except (OSError, ValueError) as exc:
        return [change, agents.Change(config, "skipped", f"{exc}; if a hook of yours stopped running, trust it again "
                                                         "with /hooks in Codex")]
    note = f"moved the trust of {moved} hook(s) that shifted up" if moved else ""
    return [change, agents.Change(config, "removed" if any(removed) else "unchanged", note)]


def _move_trust(config, hooks_json, before, after):
    """Codex keys trust by a hook's position, so a user hook that moved up when kb's left would lose its trust. Each one
    the user had trusted, by the hash of its current definition, gets its trust at its new position."""
    others = [p for p in before if not agents.is_kb_command(p[2].get("command", ""), "codex")]
    moved = 0
    for (old_gi, old_hi, handler), (new_gi, new_hi, _) in zip(others, after):
        if (old_gi, old_hi) == (new_gi, new_hi):
            continue
        digest = codex_hook_hash(CODEX_EVENT, handler)
        if _is_trusted(config, _key(hooks_json, old_gi, old_hi), digest):
            # The new trust is written first, so a failure leaves the old one in place.
            trust(config, _key(hooks_json, new_gi, new_hi), digest)
            _untrust(config, _key(hooks_json, old_gi, old_hi))
            moved += 1
    return moved


AGENT = Agent("Codex", detect, installed, install, uninstall, request, reply)
