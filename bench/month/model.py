"""Builders for the made-up month's items. Each mirrors what that source's connector stores.

Callers write times as "MM-DD HH:MM" in Denver time. Ids and the seconds of each time come from the seed.
"""
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote

YEAR = 2026
MDT = timezone(timedelta(hours=-6))
HOME = "/Users/jules"
ACCOUNT = "jules@marlowworks.dev"
ME = "Jules"
TOOL_CHARS = 300
ERROR_CHARS = 800
HERMES_RESULT_CHARS = 200

_seed = 0


def configure(seed):
    global _seed
    _seed = seed


def digest(*parts):
    return hashlib.sha256(":".join(map(str, (_seed, *parts))).encode()).digest()


@dataclass(frozen=True)
class Item:
    key: str
    source: str
    source_id: str
    ts: str
    title: str
    content: str
    meta: dict

    def row(self):
        return {"source": self.source, "source_id": self.source_id, "ts": self.ts, "title": self.title,
                "content": self.content, "meta": self.meta}


@dataclass(frozen=True)
class Q:
    id: str
    kind: str
    question: str
    answer: str
    evidence: tuple = ()
    evidence_has: tuple = ()
    month_lacks: tuple = ()

    def row(self, ids):
        return {"id": self.id, "question": self.question, "answer": self.answer,
                "evidence": [ids[key] for key in self.evidence], "kind": self.kind}


@dataclass
class Story:
    items: list = field(default_factory=list)
    questions: list = field(default_factory=list)

    def add(self, item):
        self.items.append(item)
        return item

    def ask(self, *questions):
        self.questions.extend(questions)


def local(when):
    return datetime.strptime(f"{YEAR}-{when}", "%Y-%m-%d %H:%M").replace(tzinfo=MDT)


def seeded_utc(when, key, millis=False):
    d = digest(key, when)
    t = local(when).astimezone(timezone.utc) + timedelta(seconds=d[0] % 60, milliseconds=d[1] * 3 if millis else 0)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" if millis else t.strftime("%Y-%m-%dT%H:%M:%SZ")


def later(when, minutes):
    return (local(when) + timedelta(minutes=minutes)).strftime("%m-%d %H:%M")


def clip(text, n):
    text = text.strip()
    return text if len(text) <= n else text[:n] + " …"


LINE = re.compile(r"^(U|A|R|E|T [\w.:-]+):(?: |$)(.*)$")
LABELS = {"U": "USER", "A": "ASSISTANT", "R": "RESULT", "E": "ERROR"}


def transcript(script, result_chars=TOOL_CHARS, collapse=False):
    """Session lines from a script. A line starts a turn with `U:` (user), `A:` (assistant), `T Name:` (tool call),
    `R:` (tool result) or `E:` (tool error); any other line continues the turn above it."""
    turns = []
    for raw in script.strip("\n").splitlines():
        m = LINE.match(raw)
        if m:
            turns.append([m.group(1), m.group(2)])
        elif turns:
            turns[-1][1] += "\n" + raw
        elif raw.strip():
            raise ValueError(f"script starts without a turn mark: {raw!r}")
    lines = []
    for mark, text in turns:
        text = text.strip()
        if collapse and mark in "TRE":
            text = " ".join(text.split())
        if mark.startswith("T "):
            lines.append(f"TOOL {mark[2:]}: {clip(text, TOOL_CHARS)}")
        elif mark == "R":
            lines.append(f"RESULT: {clip(text, result_chars)}")
        elif mark == "E":
            lines.append(f"ERROR: {clip(text, ERROR_CHARS)}")
        else:
            lines.append(f"{LABELS[mark]}: {text}")
    return "\n\n".join(lines)


def _uuid4(*parts):
    return str(uuid.UUID(bytes=digest(*parts)[:16], version=4))


def _uuid7(start, *parts):
    ms = int(local(start).timestamp() * 1000)
    raw = ms.to_bytes(6, "big") + digest(*parts)[:10]
    value = int.from_bytes(raw, "big")
    value = (value & ~(0xF << 76)) | (0x7 << 76)
    value = (value & ~(0x3 << 62)) | (0x2 << 62)
    return str(uuid.UUID(int=value))


def repo_dir(repo):
    return f"{HOME}/code/{repo.split('/')[-1]}"


def claude(key, start, minutes, repo, title, script, branch=None):
    sid = _uuid4("claude", key)
    cwd = repo_dir(repo)
    head = f"Claude Code session in {cwd}" + (f" ({branch})" if branch else "")
    return Item(key, "claude_code", sid, seeded_utc(later(start, minutes), key, millis=True), title,
                head + "\n\n" + transcript(script),
                {"path": f"{HOME}/.claude/projects/{cwd.replace('/', '-')}/{sid}.jsonl", "cwd": cwd,
                 "branch": branch, "started": seeded_utc(start, key + ":start", millis=True)})


def codex(key, start, minutes, repo, script, branch=None, title=None):
    sid = _uuid7(start, "codex", key)
    cwd = repo_dir(repo)
    head = f"Codex session in {cwd}" + (f" ({branch})" if branch else "")
    body = transcript(script)
    if title is None:
        title = body.split("\n", 1)[0].removeprefix("USER: ")[:100]
    started = seeded_utc(start, key + ":start", millis=True)
    stamp = datetime.fromisoformat(started.replace("Z", "+00:00")).astimezone(MDT).strftime("%Y-%m-%dT%H-%M-%S")
    return Item(key, "codex", sid, seeded_utc(later(start, minutes), key, millis=True), title, head + "\n\n" + body,
                {"path": f"{HOME}/.codex/sessions/{stamp[:4]}/{stamp[5:7]}/{stamp[8:10]}/rollout-{stamp}-{sid}.jsonl",
                 "cwd": cwd, "branch": branch, "started": started})


def hermes(key, start, minutes, title, script, platform="telegram", chat="Jules Marlow"):
    sid = local(start).strftime("%Y%m%d_%H%M%S_") + digest("hermes", key)[:3].hex()
    head = f"Hermes {platform} session, profile default" + (f", {chat}" if chat else "")
    return Item(key, "hermes", f"default:{sid}", seeded_utc(later(start, minutes), key), f"[default/{platform}] {title}",
                head + "\n\n" + transcript(script, HERMES_RESULT_CHARS, collapse=True),
                {"path": f"{HOME}/.hermes/state.db#{sid}", "profile": "default", "platform": platform,
                 "session_id": sid, "started": seeded_utc(start, key + ":start")})


@dataclass(frozen=True)
class Mail:
    sender: str
    to: str
    when: str
    body: str
    cc: str = ""
    attachments: tuple = ()


ADDRESS = re.compile(r"<([^>]+)>|([\w.+-]+@[\w.-]+)")
SUBJECT_PREFIX = re.compile(r"(?i)^\s*(?:(?:re|fwd?)\s*:\s*)+")


def email(key, subject, messages, labels=("INBOX", "IMPORTANT")):
    tid = digest("gmail", key)[:8].hex()
    blocks, people = [], []
    for i, m in enumerate(messages):
        head = [f"From: {m.sender}", f"To: {m.to}"] + ([f"Cc: {m.cc}"] if m.cc else [])
        head.append(f"Date: {seeded_utc(m.when, f'{key}:{i}')}")
        text = "\n".join([m.body.strip(), *(f"[attachment: {n}]" for n in m.attachments)]).strip()
        blocks.append("\n".join(head) + "\n\n" + text)
        for field_ in (m.sender, m.to, m.cc):
            for a, b in ADDRESS.findall(field_):
                addr = (a or b).lower()
                if addr not in people:
                    people.append(addr)
    return Item(key, "gmail", f"{ACCOUNT}:{tid}", seeded_utc(messages[-1].when, f"{key}:{len(messages) - 1}"),
                SUBJECT_PREFIX.sub("", subject).strip() or "(no subject)", "\n\n---\n\n".join(blocks),
                {"url": f"https://mail.google.com/mail/u/?authuser={quote(ACCOUNT)}#all/{tid}", "account": ACCOUNT,
                 "participants": people[:30], "labels": list(labels)})


def _chat_day(key, guid, chat, day, script, meta):
    lines = [line.strip() for line in script.strip().splitlines() if line.strip()]
    return Item(key, "imessage", f"{guid}:{YEAR}-{day}", seeded_utc(f"{day} {lines[-1][:5]}", key),
                f"iMessage with {chat} on {YEAR}-{day}", "\n".join(lines),
                {"path": f"{HOME}/Library/Messages/chat.db", "chat": guid, **meta})


def texts(key, name, phone, day, script):
    """One day of texts with one person. `script` lines read `HH:MM Name: text`."""
    guid = f"iMessage;-;{phone}"
    return _chat_day(key, guid, name, day, script, {"participants": [name], "url": f"imessage://{phone}"})


def group(key, chat, people, day, script):
    """One day of a named group chat. `script` lines read `HH:MM Name: text`."""
    guid = "iMessage;+;chat" + str(int.from_bytes(digest("chat", chat)[:6], "big"))
    return _chat_day(key, guid, chat, day, script, {"participants": list(people)})


def note(key, path, body, edited=None):
    """A note dated in its file name takes that date, as the vault connector does; any other takes `edited`."""
    folder, _, name = path.rpartition("/")
    stem = name[:-3]
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", stem)
    if not m and edited is None:
        raise ValueError(f"{key}: a note without a date in its name needs `edited`")
    ts = f"{m.group(1)}-{m.group(2)}-{m.group(3)}T00:00:00Z" if m else seeded_utc(edited, key)
    return Item(key, "vault", path, ts, stem, f"vault/{folder}".rstrip("/") + "\n\n" + body.strip() + "\n",
                {"path": f"{HOME}/notes/{path}",
                 "url": f"obsidian://open?vault=notes&file={quote(path[:-3], safe='')}"})


def commit(key, repo, when, message, files):
    sha = digest("git", key)[:20].hex()
    message = message.strip()
    content = message + ("\n\nFiles changed:\n" + "\n".join(files) if files else "")
    return Item(key, "git", f"{repo}@{sha}", seeded_utc(when, key), f"{repo}: {message.splitlines()[0]}", content,
                {"path": repo_dir(repo), "repo": repo, "sha": sha, "url": f"https://github.com/{repo}/commit/{sha}"})


@dataclass(frozen=True)
class Post:
    author: str
    when: str
    text: str
    verdict: str = ""


def thread(key, repo, number, kind: Literal["issue", "PR"], title, author, created, body, posts=(), state="open",
           closed=None, merged=None, branches="", labels=()):
    stamps = [seeded_utc(created, f"{key}:created")]
    head = [f"{kind} {state}", f"author {author}", f"created {stamps[0]}"]
    if merged:
        stamps.append(seeded_utc(merged, f"{key}:merged"))
        head.append(f"merged {stamps[-1]}")
    elif closed:
        stamps.append(seeded_utc(closed, f"{key}:closed"))
        head.append(f"closed {stamps[-1]}")
    if branches:
        head.append(branches)
    if labels:
        head.append("labels " + ", ".join(labels))
    blocks = [" | ".join(head) + "\n\n" + (body.strip() or "(no description)")]
    for i, p in enumerate(posts):
        stamps.append(seeded_utc(p.when, f"{key}:{i}"))
        tag = stamps[-1] + (f", {p.verdict}" if p.verdict else "")
        blocks.append(f"{p.author} ({tag}): {p.text.strip()}".rstrip())
    return Item(key, "github", f"{repo}#{number}", max(stamps), f"{repo}#{number} [{kind} {state}]: {title}",
                "\n\n---\n\n".join(blocks),
                {"url": f"https://github.com/{repo}/{'pull' if kind == 'PR' else 'issues'}/{number}", "repo": repo,
                 "number": number, "kind": kind.lower(), "state": state})
