"""claude-mem's local worker, fed the way its hooks feed it.

claude-mem records agent sessions only: Claude Code, Codex and Hermes. Each of those month items is replayed
through the worker routes its hooks call (a prompt, each tool call with its output, then the closing summary). It
never sees the month's email, texts, notes, commits or GitHub threads, because it has no way to read them.

Start the worker with an empty store first; CLAUDE_MEM_URL points at it.
"""
import glob
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from tools import Retrieved, Tool

URL = os.environ.get("CLAUDE_MEM_URL", "http://127.0.0.1:37700").rstrip("/")
# A question arrives in a new session that is not inside any one repository.
CWD = "/Users/jules"
PLATFORM = {"claude_code": "claude-code", "codex": "codex", "hermes": "hermes"}
TURN = re.compile(r"\n\n(?=(?:USER|ASSISTANT|RESULT|ERROR|TOOL [\w.:-]+): )")
SESSION_SECONDS = 15 * 60

HOME_DIR = os.path.expanduser(os.environ.get("CLAUDE_MEM_HOME", "~/.cache/kb-bench/claude-mem/home"))
# The three tools of claude-mem's documented search workflow: search, then timeline, then get_observations.
SEARCH_TOOLS = ("search", "timeline", "get_observations")


class MCP:
    """claude-mem's own MCP server over stdio, started the way its plugin registers it."""

    def __init__(self):
        script = sorted(glob.glob(os.path.join(
            HOME_DIR, ".claude/plugins/cache/thedotmack/claude-mem/*/scripts/mcp-server.cjs")))[-1]
        self.proc = subprocess.Popen(["node", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, env={**os.environ, "HOME": HOME_DIR})
        self.lock = threading.Lock()
        self.last_id = 0
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "bench", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params):
        with self.lock:
            self.last_id += 1
            self._send({"jsonrpc": "2.0", "id": self.last_id, "method": method, "params": params})
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("claude-mem's MCP server stopped")
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if message.get("id") == self.last_id:
                    if "error" in message:
                        raise RuntimeError(f"MCP error: {message['error']}")
                    return message["result"]

    def tools(self):
        """The search tools as the server lists them, in the Messages API's tool format."""
        return tuple({"name": t["name"], "description": t["description"], "input_schema": t["inputSchema"]}
                     for t in self.request("tools/list", {})["tools"] if t["name"] in SEARCH_TOOLS)

    def call(self, name, args):
        result = self.request("tools/call", {"name": name, "arguments": args})
        return "\n".join(part.get("text", "") for part in result.get("content") or [])


def worker(method, path, body=None, timeout=120):
    req = urllib.request.Request(URL + path, method=method, headers={"content-type": "application/json"},
                                 data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def text_of(reply):
    if isinstance(reply, str):
        return reply
    if isinstance(reply, dict) and isinstance(reply.get("content"), list):
        return "\n".join(part.get("text", "") for part in reply["content"])
    if isinstance(reply, dict) and isinstance(reply.get("context"), str):
        return reply["context"]
    return json.dumps(reply)


def turns(content):
    """(label, text) pairs from a session item's transcript, after its heading line."""
    body = content.split("\n\n", 1)[1] if "\n\n" in content else ""
    found = []
    for part in TURN.split("\n\n" + body):
        label, _, text = part.strip().partition(": ")
        if label:
            found.append((label, text))
    return found


def tool_input(name, text):
    key = {"Bash": "command", "Read": "file_path", "Edit": "file_path", "Write": "file_path", "Grep": "pattern",
           "exec_command": "cmd", "web_search": "query"}.get(name, "input")
    return {key: text}


class ClaudeMem(Tool):
    label = "claude-mem"

    def _connect(self):
        self.mcp = MCP()
        self.agent_tools = self.mcp.tools()

    def load(self, items, workdir):
        self._connect()
        started = time.monotonic()
        sessions = [i for i in sorted(items, key=lambda i: i["ts"]) if i["source"] in PLATFORM]
        stats = worker("GET", "/api/stats")["database"]
        if stats["sessions"] == len(sessions):
            return {"reused_store": True, "observations": stats["observations"], "summaries": stats["summaries"]}
        if stats["observations"]:
            raise RuntimeError("claude-mem's store holds part of the month; start the worker on an empty data folder")
        unsettled = 0
        # Real sessions arrive hours apart; replayed all at once, they park behind the worker's two agent slots.
        for item in sessions:
            self._replay(item)
            unsettled += not self._drain(SESSION_SECONDS)
        stats = worker("GET", "/api/stats")["database"]
        return {"version": worker("GET", "/api/stats")["worker"]["version"], "sessions_replayed": len(sessions),
                "observations": stats["observations"], "summaries": stats["summaries"],
                "sessions_unsettled": unsettled, "seconds": round(time.monotonic() - started)}

    def _drain(self, seconds):
        """Wait until the worker has nothing queued or running. Returns False on timeout."""
        deadline, quiet = time.monotonic() + seconds, 0
        while time.monotonic() < deadline:
            status = worker("GET", "/api/processing-status")
            quiet = quiet + 1 if not (status.get("isProcessing") or status.get("queueDepth")
                                      or status.get("parkedSessions")) else 0
            if quiet >= 2:
                return True
            time.sleep(3)
        return False

    def _replay(self, item):
        sid, platform = item["source_id"], PLATFORM[item["source"]]
        cwd = item["meta"].get("cwd") or CWD
        base = {"contentSessionId": sid, "platformSource": platform, "cwd": cwd}
        pending, last_answer = None, ""
        for label, text in turns(item["content"]):
            if label == "USER":
                worker("POST", "/api/sessions/init", {**base, "prompt": text})
            elif label == "ASSISTANT":
                last_answer = text
            elif label.startswith("TOOL "):
                pending = label[5:]
                pending_input = text
            elif label in ("RESULT", "ERROR") and pending:
                worker("POST", "/api/sessions/observations", {
                    **base, "tool_name": pending, "tool_input": tool_input(pending, pending_input),
                    "tool_response": ("Error: " if label == "ERROR" else "") + text})
                pending = None
        worker("POST", "/api/sessions/summarize", {**base, "last_assistant_message": last_answer})
        worker("POST", "/api/sessions/session-end", base)

    def _context(self, question):
        return text_of(worker("GET", "/api/context/inject?cwd=" + urllib.parse.quote(CWD, safe=""))).strip()

    def retrieve(self, question):
        started = time.monotonic()
        try:
            text = self._context(question["question"])
        except Exception as exc:
            return Retrieved("", None, time.monotonic() - started, type(exc).__name__)
        return Retrieved(text, None, time.monotonic() - started)

    def call(self, name, args):
        """Run one of claude-mem's MCP search tools on its own MCP server, as an agent's MCP client does."""
        if name not in SEARCH_TOOLS:
            raise KeyError(name)
        return self.mcp.call(name, args), []


class ClaudeMemSemantic(ClaudeMem):
    """With claude-mem's opt-in per-prompt lookup (CLAUDE_MEM_SEMANTIC_INJECT), which is off by default."""
    label = "claude-mem, per-prompt lookup on"

    def load(self, items, workdir):
        self._connect()
        stats = worker("GET", "/api/stats")["database"]
        return {"reused_store": True, "observations": stats["observations"], "summaries": stats["summaries"]}

    def _context(self, question):
        start = super()._context(question)
        notes = text_of(worker("POST", "/api/context/semantic", {"q": question, "cwd": CWD, "limit": 5})).strip()
        return "\n\n".join(part for part in (start, notes) if part)
