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
    start = next((i for i, line in enumerate(lines) if _is_header(line, key) and _parses("".join(lines[:i]))), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if _opens_table(lines, i)), len(lines))
    return start, end


def _opens_table(lines, i):
    """Whether line i is a table header, not a line of a multi-line array or string that starts with "[": it parses
    as a header alone, and everything before it parses too."""
    return lines[i].lstrip().startswith("[") and _parses(lines[i]) and _parses("".join(lines[:i]))


def _parses(text):
    try:
        read_toml(text)
    except ValueError:
        return False
    return True


def _trusted_line(lines, start, end):
    """The line in a table that sets trusted_hash, however its key is quoted, and not a line inside a multi-line
    string that only looks like one: everything before it parses too."""
    def sets_hash(i):
        try:
            return list(read_toml(lines[i])) == ["trusted_hash"] and _parses("".join(lines[:i]))
        except ValueError:
            return False
    return next((i for i in range(start + 1, end) if sets_hash(i)), None)


def _lines(text):
    """TOML's lines, split at line feeds only: splitlines() also splits at characters a path may hold, such as U+2028."""
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _trusted(text, key, digest):
    """The config's text with this hook position trusted with `digest`: (text, action)."""
    entry = f'trusted_hash = "{digest}"'
    lines = _lines(text)
    section = _section(lines, key)
    if section is None:
        if text and not text.endswith("\n"):
            text += "\n"
        return text + ("\n" if text else "") + f"{_header(key)}\n{entry}\n", "added"
    start, end = section
    found = _trusted_line(lines, start, end)
    if found is not None and lines[found].strip() == entry:
        return text, "unchanged"
    if found is None:
        lines.insert(start + 1, entry + "\n")
    else:
        lines[found] = entry + "\n"
    return "".join(lines), "updated"


def _untrusted(text, key):
    """The config's text without this position's trust table: (text, whether it had one)."""
    lines = _lines(text)
    section = _section(lines, key)
    if section is None:
        return text, False
    start, end = section
    if end == len(lines) and start > 0 and not lines[start - 1].strip():
        start -= 1
    # A comment line inside the span is the user's (often about the section after it), so it stays.
    kept = [line for i, line in enumerate(lines[start:end], start) if i > section[0] and _is_comment(lines, i)]
    return "".join(lines[:start] + kept + lines[end:]), True


def _is_comment(lines, i):
    """Whether line i is a whole-line comment, not a line of a multi-line string that starts with "#"."""
    return lines[i].lstrip().startswith("#") and _parses("".join(lines[:i]))


def _comments(text):
    lines = _lines(text)
    return [lines[i].strip() for i in range(len(lines)) if _is_comment(lines, i)]


def _moved(text, old_key, new_key):
    """The config's text with the trust table at `old_key` moved, whole, to `new_key`: its header is renamed."""
    lines = _lines(text)
    section = _section(lines, old_key)
    if section is None:
        return text, False
    line = lines[section[0]]
    # Keep what follows the header on its line, such as a comment: the shortest prefix ending in "]" that is the
    # header alone is the header, whatever its key holds.
    end = next(i + 1 for i, ch in enumerate(line) if ch == "]" and _is_header(line[:i + 1], old_key))
    lines[section[0]] = line[:len(line) - len(line.lstrip())] + _header(new_key) + line[end:]
    return "".join(lines), True


def _view(hooks_json, hooks, config, kb_keys):
    """What the user's Codex does, apart from kb: every hook that is not kb's, in order, with its group's other
    fields and the trust entry at the position it sits in now; every other hook event; every other setting. Trust
    entries at `kb_keys`, the positions kb's hook holds before or after the edit, are kb's unless a user hook sits
    there."""
    state = dict(((config.get("hooks") or {}).get("state") or {}))
    prefix = f"{os.path.realpath(hooks_json)}:{CODEX_EVENT}:"
    users = []
    for gi, group in enumerate(((hooks.get("hooks") or {}).get(EVENT) or [])):
        handlers = list(group.get("hooks") or [])
        mine = [hi for hi, h in enumerate(handlers) if agents.is_kb_command(h.get("command", ""), "codex")]
        if handlers and len(mine) == len(handlers):
            continue
        users.append(({k: v for k, v in group.items() if k != "hooks"},
                      [(h, state.pop(f"{prefix}{gi}:{hi}", None)) for hi, h in enumerate(handlers) if hi not in mine]))
    for key in kb_keys:
        state.pop(key, None)
    other_hooks = {**hooks, "hooks": {k: v for k, v in (hooks.get("hooks") or {}).items() if k != EVENT}}
    other_config = {**config, "hooks": {**(config.get("hooks") or {}), "state": state}}
    for data in (other_hooks, other_config):
        for name in ("state",) if data is other_config else ():
            if not data["hooks"][name]:
                del data["hooks"][name]
        if not data["hooks"]:
            del data["hooks"]
    return users, other_hooks, other_config


def _kb_hooks(hooks):
    groups = (hooks.get("hooks") or {}).get(EVENT) or []
    return [(gi, hi, h) for gi, group in enumerate(groups) for hi, h in enumerate(group.get("hooks") or [])
            if agents.is_kb_command(h.get("command", ""), "codex")]


def _guard(hooks_json, old, new, kb_after, kb_trust):
    """Why the edit must not be written, or None. `old` and `new` are (hooks.json text, config.toml text). It is safe
    when both new files parse, the user's Codex does exactly what it did apart from kb (_view), kb's hooks are
    `kb_after` ([(group, position, handler)]), and kb's trust entries are `kb_trust` ({key: hash})."""
    try:
        old_hooks, old_config = agents._parse(old[0], hooks_json), read_toml(old[1])
    except ValueError as exc:
        return f"the files do not parse as they are ({exc})"
    try:
        hooks, config = agents._parse(new[0], hooks_json), read_toml(new[1])
    except ValueError as exc:
        return f"the edit would not parse ({exc})"
    kb_keys = {_key(hooks_json, gi, hi) for gi, hi, _ in _kb_hooks(old_hooks) + _kb_hooks(hooks)}
    if _view(hooks_json, hooks, config, kb_keys) != _view(hooks_json, old_hooks, old_config, kb_keys):
        return "the edit would change more than kb's own entries"
    # Parsing drops comments, so they are checked as text: every whole-line comment stays, in order.
    kept = iter(_comments(new[1]))
    if not all(comment in kept for comment in _comments(old[1])):
        return "the edit would drop a comment from config.toml"
    if _kb_hooks(hooks) != kb_after:
        return "kb's hook did not come out as intended"
    state = (config.get("hooks") or {}).get("state") or {}
    if any((state.get(key) or {}).get("trusted_hash") != digest for key, digest in kb_trust.items()):
        return "kb's trust entry did not come out as intended"
    return None


def _write(hooks_json, config, old, new):
    for path, before, after in ((hooks_json, old[0], new[0]), (config, old[1], new[1])):
        if after != before:
            agents.write(path, after)


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
    old = (agents.read(hooks_json), agents.read(config))
    by_hand = (f"To add the hook by hand, put {{\"type\": \"command\", \"command\": {json.dumps(kb_command)}, "
               f"\"timeout\": {agents.BACKGROUND_HOOK_TIMEOUT}, \"async\": true}} under hooks.UserPromptSubmit in {hooks_json}, "
               "then trust it once with /hooks in Codex.")
    try:
        new_hooks, gi, hi, handler, action = agents.json_hook_added(old[0], hooks_json, EVENT, "codex", kb_command,
                                                                    background=True)
    except ValueError as exc:
        return [agents.Change(hooks_json, "skipped", f"{exc}; left as it is")]
    key = _key(hooks_json, gi, hi)
    if set(handler) - CODEX_FIELDS:
        new, kb_trust, trust_change = (new_hooks, old[1]), {}, agents.Change(
            config, "skipped", f"the hook has fields kb cannot hash; trust it once with /hooks in Codex ({key})")
    else:
        digest = codex_hook_hash(CODEX_EVENT, handler)
        new_config, trust_action = _trusted(old[1], key, digest)
        new, kb_trust, trust_change = (new_hooks, new_config), {key: digest}, agents.Change(
            config, trust_action, f"trusts {key}")
    problem = _guard(hooks_json, old, new, [(gi, hi, handler)], kb_trust)
    if problem:
        return [agents.Change(hooks_json, "skipped", f"{problem}; wrote nothing. {by_hand}"),
                agents.Change(config, "skipped", problem)]
    _write(hooks_json, config, old, new)
    return [agents.Change(hooks_json, action), trust_change]


def _positions(hooks):
    groups = (hooks.get("hooks") or {}).get(EVENT) or []
    return [(gi, hi, handler) for gi, group in enumerate(groups) for hi, handler in enumerate(group.get("hooks") or [])]


def uninstall(home):
    hooks_json, config = _hooks_json(home), _config(home)
    old = (agents.read(hooks_json), agents.read(config))
    try:
        new_hooks, action = agents.json_hook_removed(old[0], hooks_json, EVENT, "codex")
        before, after = _positions(agents._parse(old[0], hooks_json)), _positions(agents._parse(new_hooks, hooks_json))
    except ValueError as exc:
        return [agents.Change(hooks_json, "skipped", f"{exc}; left as it is")]
    mine = [(gi, hi) for gi, hi, h in before if agents.is_kb_command(h.get("command", ""), "codex")]
    if not mine:
        return [agents.Change(hooks_json, action)]
    new_config, removed = old[1], False
    for gi, hi in mine:
        new_config, gone = _untrusted(new_config, _key(hooks_json, gi, hi))
        removed = removed or gone
    # Codex keys trust by position, so a user hook that moved up when kb's left keeps its trust only if its table
    # moves with it. Moving up, in order, each one lands on a key the one before it has just left.
    others = [p for p in before if not agents.is_kb_command(p[2].get("command", ""), "codex")]
    moved = 0
    for (old_gi, old_hi, _), (new_gi, new_hi, _) in zip(others, after):
        if (old_gi, old_hi) != (new_gi, new_hi):
            new_config, shifted = _moved(new_config, _key(hooks_json, old_gi, old_hi), _key(hooks_json, new_gi, new_hi))
            moved += shifted
    problem = _guard(hooks_json, old, (new_hooks, new_config), [], {})
    if problem:
        return [agents.Change(hooks_json, "skipped", f"{problem}; wrote nothing. To remove the hook by hand, delete "
                                                     f"the kb hook codex entry under hooks.UserPromptSubmit in "
                                                     f"{hooks_json}; Codex asks you to trust the hooks after it again."),
                agents.Change(config, "skipped", problem)]
    _write(hooks_json, config, old, (new_hooks, new_config))
    note = f"moved the trust of {moved} hook(s) that shifted up" if moved else ""
    return [agents.Change(hooks_json, action), agents.Change(config, "removed" if removed else "unchanged", note)]


AGENT = Agent("Codex", detect, installed, install, uninstall, request, reply, background=True)
