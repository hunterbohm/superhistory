"""The canary harness. A different secret is planted in every kind of input superhistory takes from outside: config secrets,
an environment variable, `cmd:` secrets that fail or time out, item text and metadata, file and repository paths,
the output and errors of ssh, gh and git, and HTTP and IMAP answers, malformed ones included. Then every superhistory command
and every library entry point the Sync client and the sync relay call runs, and no canary may reach stdout,
stderr, a log record, a file superhistory writes under its data, log or cache folders, check.log, or the text of an exception.

Each canary has a secret's shape, because mask() can only mask what looks like a secret. A path this harness does
not cover is a missing case: add the input or the call here.
"""
import contextlib
import functools
import imaplib
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import dataclasses

from kb import ai, check, cli, config, ids, ingest, remote, search, store, summarize
from kb.ai import api
from kb.ai import cli as ai_cli
from kb.paths import Paths
from kb.sources import Excludes, Item, Progress, Run, SourceType, eden, git, github, gmail, notes, youtube
from tests.test_gmail import FakeGmail
from tests.test_hermes import make_store

KINDS = ("env", "cmd", "cmdfail", "cmdtimeout", "configkey", "itemtext", "itemmeta", "metakey", "notepath",
         "notepath2", "repopath", "sshout", "ssherr", "ghout", "gherr", "ghpath", "gitout", "giterr", "gitcwd", "http",
         "httpbad", "httpstatus", "modeltext", "imap", "imapbad", "imaperr", "remoteopt", "aipath", "session",
         "edenkey", "edenrefuse", "ytpath", "yturl")
CANARY = {kind: f"sk-ant-api03-canary-{kind}-" + "Z" * 24 for kind in KINDS}
# Secrets that only their field name gives away, as in "password=...": mask() knows them by the name in front.
KEYED = ("metapw", "metanested", "ghtail", "aitail", "turntail", "sumjoin", "syncpw", "idassign", "syncappend",
         "idarrow", "barepw")
CANARY.update({kind: f"canary{kind}Zz12345678" for kind in KEYED})
NAMES = ("machinename", "hostname", "sourcename", "timestamp", "mark", "ghargs", "hookprompt", "hooksession",
         "transcript", "hooksettings", "schedout", "sortsubject", "sortsender", "contextmessage", "trialid",
         "trialsource", "setupkey", "setupexclude", "imaplong", "aimodel", "oauthsecret", "oauthtoken", "cctitle",
         "cxtitle", "cxtool", "hmuser", "hmtitle", "ghbody", "gmbody", "gmurl", "sumq", "sums", "suma", "synctitle",
         "synctext", "syncmeta", "syncsplit", "syncid", "oldtitle", "oldcheck", "oldid", "oldsnip", "oldpage", "oldslice", "oldsplit", "hugeerror")
# Each of these sits across a limit where superhistory cuts text, so only the 12-character check sees a cut made before masking.
CUT = {"cctitle": 84, "cxtitle": 84, "cxtool": 284, "hmtitle": 84, "hmuser": 2984, "ghbody": 19984, "gmbody": 19984,
       "sumq": 384, "sums": 1484, "suma": 1984, "synctitle": 74, "oldtitle": 74, "oldcheck": 104, "oldpage": cli.PAGE - 16,
       "oldslice": summarize.MAX_INPUT_CHARS // summarize.SLICES - 16, "oldsplit": 10 * store.CHUNK - 16}


def across(kind):
    return "." * CUT[kind] + CANARY[kind]
# A number its field name marks as secret, and a private key's body, whose line breaks repr() would escape.
NUMBER = 928374615001
PEM_BODY = "Q2FuYXJ5UHJpdmF0ZUtleUJvZHkxMjM0NTY3OA"
PEM = f"-----BEGIN PRIVATE KEY-----\n{PEM_BODY}\n{PEM_BODY[::-1]}\n-----END PRIVATE KEY-----"
# A key long enough that its body fills a whole chunk, so a chunk can hold body lines and neither of its ends.
LONG_PEM_LINE = ("T2xkUGVtQm9keUxpbmU" * 4)[:64]
LONG_PEM = "-----BEGIN PRIVATE KEY-----\n" + f"{LONG_PEM_LINE}\n" * 90 + "-----END PRIVATE KEY-----"
CANARY.update({kind: f"sk-ant-api03-canary-{kind}-" + "z" * 24 for kind in NAMES})
NOW = datetime.now(timezone.utc).replace(microsecond=0)
ME = "pat@example.com"

FAKE_SSH = """#!{python}
import subprocess, sys
args = sys.argv[1:]
while args[0] == "-o":
    args = args[2:]
alias, program = args[0], sys.stdin.buffer.read()
if alias == "noisy":
    sys.stderr.write("Permission denied for {ssherr}\\n")
    sys.exit(1)
if alias == "garbage":
    print('{{"oops": "{sshout}"}}')
    sys.exit(0)
out = subprocess.run([{python!r}, "-I", "-"], input=program, stdout=subprocess.PIPE, cwd="/")
sys.stdout.buffer.write(out.stdout)
sys.exit(out.returncode)
"""
FAKE_GH = """#!{python}
import json, sys
if {fail}:
    sys.stderr.write("HTTP 401: Bad credentials {gherr}\\n")
    sys.exit(1)
if sys.argv[1] == "search":
    print(json.dumps([{{"repository": {{"nameWithOwner": "acme/demo"}}, "number": 7, "isPullRequest": False,
                       "updatedAt": "{updated}"}}]))
else:
    print(json.dumps({{"title": "Leaked {ghout}", "body": {ghbody!r}, "state": "OPEN",
                      "author": {{"login": "dana"}}, "createdAt": "{updated}", "updatedAt": "{updated}",
                      "closedAt": None, "labels": [], "url": "https://github.com/acme/demo/issues/7?t={ghout}",
                      "comments": [{{"author": {{"login": "dana"}}, "createdAt": "{updated}", "body": "{ghout}"}}]}}))
"""


class Model(BaseHTTPRequestHandler):
    """An Anthropic-compatible server. In "ok" it answers every call with JSON that carries a canary; in "refuse"
    it echoes the key it was sent; in "garbage" it answers with something other than JSON."""
    mode = "ok"
    query = None  # the search "ok" writes for the check, when not its own
    sent = []  # every request body: what the configured model was sent

    def do_POST(self):
        Model.sent.append(self.rfile.read(int(self.headers.get("content-length", 0))).decode("utf-8", "replace"))
        if Model.mode == "long":
            text = json.dumps({"segments": [{"title": "t", "question": across("sumq"), "summary": across("sums")}],
                               "atoms": [{"type": "decision", "text": across("suma")}]})
            body = json.dumps({"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        if Model.mode == "joined":
            # Each part holds no secret alone; a segment's row puts the question's last line before the summary.
            text = json.dumps({"segments": [{"title": "t", "question": "Which one? password=",
                                             "summary": CANARY["sumjoin"] + " was the one"}], "atoms": []})
            body = json.dumps({"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        if Model.mode == "hugeerror":
            # Past the 65,536 bytes an error body is read to, after spaces that splitting the detail removes.
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b" " * 65520 + CANARY["hugeerror"].encode())
            return
        if Model.mode == "pem":
            body = json.dumps({"content": [{"type": "text", "text": f"Here it is:\n{PEM}"}]}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        if Model.mode == "status":
            self.wfile.write(CANARY["httpstatus"].encode() + b"\r\n\r\n")
            self.close_connection = True
            return
        if Model.mode == "edenrefuse":
            body = json.dumps({"ok": False, "status": "forbidden", "message": f"no {CANARY['edenrefuse']}"}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        if Model.mode == "refuse":
            body = json.dumps({"error": {"message": f"invalid x-api-key {self.headers.get('x-api-key')} "
                                                    f"{CANARY['http']}"}}).encode()
            self.send_response(401)
        elif Model.mode == "garbage":
            body = f"<html>upstream error {CANARY['httpbad']}</html>".encode()
            self.send_response(200)
        else:
            text = json.dumps({"query": Model.query or "pelican " + "." * 176 + CANARY["modeltext"], "search": True,
                               "scores": {"1": 2, "2": 2},
                               "keep": [1, 2],
                               "segments": [{"title": CANARY["modeltext"], "question": CANARY["modeltext"],
                                             "summary": CANARY["modeltext"]}],
                               "atoms": [{"type": "fact", "text": CANARY["modeltext"]}]})
            body = json.dumps({"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}).encode()
            self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Gmail(FakeGmail):
    """Refuses a sign-in by quoting the password, or answers a search with something other than UIDs."""

    def __init__(self, user):
        super().__init__()
        self.user = user
        self.add("1781000000000000001", NOW - timedelta(days=1), "Dana <dana@example.org>",
                 f"The deploy key is {CANARY['imap']}", subject=f"Key {CANARY['imap']}")
        self.add("1781000000000000002", NOW - timedelta(hours=5), f"Ops <ops-{CANARY['sortsender']}@example.org>",
                 "Rotate it today.", subject=f"Rotated {CANARY['sortsubject']}")
        link = "https://files.example.org/" + "a" * 17 + "/" + CANARY["gmurl"] + "b" * 40
        self.add("1781000000000000003", NOW - timedelta(hours=4), "Dana <dana@example.org>",
                 f"See {link} and the notes.\n" + across("gmbody"), subject="Notes")

    def login(self, user, password):
        if user == "long@example.com":
            # Longer than the 200 characters an error keeps, with the token at its end.
            raise imaplib.IMAP4.error(("." * 177 + " " + CANARY["imaplong"]).encode())
        if user == "refused@example.com":
            raise imaplib.IMAP4.error(f"[AUTHENTICATIONFAILED] bad password {password} {CANARY['imaperr']}".encode())

    def uid(self, command, *args):
        if command == "SEARCH" and self.user == "odd@example.com":
            return "OK", [CANARY["imapbad"].encode()]
        return super().uid(command, *args)


class CanaryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = os.path.realpath(tmp.name)
        self.home = os.path.join(self.tmp, "home")
        self.paths = Paths.under(os.path.join(self.tmp, "superhistory"))
        self.seen = []
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self._program("ssh", FAKE_SSH.format(python=sys.executable, ssherr=CANARY["ssherr"], sshout=CANARY["sshout"]))
        updated = (NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for name, fail in (("gh-ok", False), ("gh-fail", True)):
            self._program(name, FAKE_GH.format(python=sys.executable, fail=fail, gherr=CANARY["gherr"],
                                               ghout=CANARY["ghout"], updated=updated,
                                               ghbody=f"token {CANARY['ghout']} " + across("ghbody")))
        for tool in ("systemctl", "launchctl", "crontab"):
            # Turning a job on fails, so superhistory shows the tool's own output, which holds a canary.
            self._program(tool, f"#!{sys.executable}\nimport sys\nprint('unit superhistory-ingest {CANARY['schedout']}')\n"
                                f"sys.stderr.write('note {CANARY['schedout']}\\n')\n"
                                "sys.exit(1 if {'enable', 'load', 'bootstrap', 'start', '-'} & set(sys.argv[1:]) else 0)\n")
        for kind in ("ghtail", "aitail"):
            # The field name ends up outside the last 400 characters an error keeps; only masking first catches it.
            tail = f"password={CANARY[kind]} " + "." * (400 - len(CANARY[kind]) - 1)
            self._program(kind, f"#!{sys.executable}\nimport sys\nsys.stderr.write({tail!r})\nsys.exit(1)\n")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        Model.mode = "ok"
        Model.sent = []
        self.notes = os.path.join(self.tmp, f"notes-{CANARY['notepath']}")
        os.makedirs(os.path.join(self.notes, "Area"))
        texts = {"plan.md": f"---\nsource: https://vault.example/?api_key={CANARY['itemmeta']}\n---\n"
                            f"The pelican staging token is {CANARY['itemtext']}\n" + "Long line of notes.\n" * 800,
                 f"key-{CANARY['notepath']}.md": "the first file named after a token",
                 f"key-{CANARY['notepath2']}.md": "the second file named after a token",
                 f"token={CANARY['idassign']}.md": "a file whose name assigns a token by its key's name",
                 f"password=>{CANARY['idarrow']}.md": "a file whose name assigns a password with =>"}
        for name, text in texts.items():
            with open(os.path.join(self.notes, "Area", name), "w") as fh:
                fh.write(text)
            os.utime(os.path.join(self.notes, "Area", name), (time.time() - 86400,) * 2)
        self._sessions()
        self.repos = os.path.join(self.home, "code")
        self._repo(os.path.join(self.repos, f"app-{CANARY['repopath']}"))
        broken = os.path.join(self.repos, "broken")
        os.makedirs(broken)
        with open(os.path.join(broken, ".git"), "w") as fh:
            fh.write(f"gitdir: /nonexistent/{CANARY['giterr']}\n")
        os.makedirs(os.path.dirname(self.paths.config_file), exist_ok=True)
        with open(self.paths.config_file, "w") as fh:
            fh.write(self._config())
        env = {"HOME": self.home, "SUPERHISTORY_HOME": os.path.dirname(self.paths.config_file), "SUPERHISTORY_CANARY_KEY": CANARY["env"],
               "PATH": self.bin + os.pathsep + os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1",
               "SUPERHISTORY_CANARY_OAUTH": CANARY["oauthsecret"]}
        # create=True lets this file run on code from before the masking work, where it must find the leaks. Retries
        # skip their backoff, since the fake plug-in's failures would otherwise wait up to a minute each.
        no_wait = functools.partial(ai.with_retries, sleep=lambda seconds: None)
        for patch in (mock.patch.object(ai, "with_retries", no_wait), mock.patch.dict(os.environ, env), mock.patch.object(config, "SECRET_TIMEOUT", 0.5, create=True),
                      mock.patch.object(gmail, "connect", self._connect), mock.patch.object(github, "SPAN_DAYS", 30)):
            patch.start()
            self.addCleanup(patch.stop)
        self.records = []
        self.cloud = logging.getLogger("cloud-engine")
        catcher = logging.Handler()
        catcher.emit = lambda record: self.records.append(logging.Formatter().format(record))
        for name in ("", "kb", "cloud-engine"):
            logging.getLogger(name).addHandler(catcher)
            self.addCleanup(logging.getLogger(name).removeHandler, catcher)
        self.cloud.setLevel(logging.INFO)
        kb_log = logging.getLogger("kb")
        saved = kb_log.handlers[:]

        def restore():
            for handler in kb_log.handlers:
                if handler not in saved and handler is not catcher:
                    handler.close()
            kb_log.handlers = saved

        self.addCleanup(restore)
        self._connections = {}

    def _sessions(self):
        """Agent sessions whose first prompt, tool call and long message each cross a limit superhistory cuts at."""
        stamp = (NOW - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        claude = os.path.join(self.home, ".claude", "projects", "-work", "c1.jsonl")
        os.makedirs(os.path.dirname(claude))
        with open(claude, "w") as fh:
            for record in ({"type": "user", "timestamp": stamp, "sessionId": "c1", "cwd": "/work",
                            "message": {"content": across("cctitle")}},
                           {"type": "assistant", "timestamp": stamp, "sessionId": "c1",
                            "message": {"content": [{"type": "text", "text": "On it."}]}}):
                fh.write(json.dumps(record) + "\n")
        sid = "019e0000-0000-7000-8000-00000000c0de"
        codex_file = os.path.join(self.home, ".codex", "sessions", "2026", "10", "26", f"rollout-a-{sid}.jsonl")
        os.makedirs(os.path.dirname(codex_file))

        def rec(n, kind, payload):
            return json.dumps({"timestamp": stamp, "ordinal": n, "type": kind, "payload": payload},
                              separators=(",", ":"))

        with open(codex_file, "w") as fh:
            fh.write("\n".join([
                rec(0, "session_meta", {"id": sid, "timestamp": stamp, "cwd": "/work", "source": "cli"}),
                rec(1, "response_item", {"type": "message", "role": "user",
                                         "content": [{"type": "input_text", "text": across("cxtitle")}]}),
                rec(2, "response_item", {"type": "function_call", "name": "shell",
                                         "arguments": json.dumps({"command": across("cxtool")})}),
                rec(3, "response_item", {"type": "message", "role": "assistant",
                                         "content": [{"type": "output_text", "text": "Done."}]})]) + "\n")
        make_store(os.path.join(self.home, ".hermes", "state.db"), [
            ("h1", "cli", None, time.time() - 86400, [("user", across("hmtitle"), {}), ("assistant", "ok", {}),
                                                      ("user", across("hmuser"), {}), ("assistant", "ok", {})])])

    def _program(self, name, text):
        path = os.path.join(self.bin, name)
        with open(path, "w") as fh:
            fh.write(text)
        os.chmod(path, 0o755)
        return path

    def _repo(self, path):
        os.makedirs(path)
        who = {"GIT_AUTHOR_NAME": "Pat", "GIT_AUTHOR_EMAIL": ME, "GIT_COMMITTER_NAME": "Pat",
               "GIT_COMMITTER_EMAIL": ME, "HOME": self.home}
        for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", f"Rotate {CANARY['gitout']}"]):
            subprocess.run(["git", *args], cwd=path, check=True, env={**os.environ, **who}, capture_output=True)

    def _config(self):
        mail = 'type = "gmail"\nsort = false\nwindow_days = 30\n'
        return f"""machine = "desk"
[ai]
plugin = "anthropic-compatible"
base_url = "http://127.0.0.1:{self.server.server_address[1]}"
model = "fake"
key = "env:SUPERHISTORY_CANARY_KEY"
[hosts.noisy]
ssh = "noisy"
python = "{sys.executable}"
[hosts.garbage]
ssh = "garbage"
python = "{sys.executable}"
[hosts.laptop]
ssh = "laptop"
python = "{sys.executable}"
[sources.notes]
path = {json.dumps(self.notes)}
hosts = ["desk", "laptop", "noisy", "garbage"]
summarize = true
[sources.claude]
type = "claude_code"
[sources.sessions]
type = "codex"
[sources.hermes]
[sources.code]
type = "git"
roots = [{json.dumps(self.repos)}]
identities = ["{ME}"]
window_days = 30
[sources.issues]
type = "github"
gh = {json.dumps(os.path.join(self.bin, "gh-ok"))}
window_days = 1
[sources.issues_down]
type = "github"
gh = {json.dumps(os.path.join(self.bin, "gh-fail"))}
window_days = 1
[sources.mail]
{mail}address = "{ME}"
password = "cmd:printf %s {CANARY['cmd']}"
[sources.mail_sorted]
type = "gmail"
sort = true
window_days = 30
address = "sorted@example.com"
password = "cmd:printf ok"
[sources.mail_long]
{mail}address = "long@example.com"
password = "cmd:printf ok"
[sources.mail_oauth]
{mail}address = "{ME}"
auth = "oauth"
client_id = "client-1"
client_secret = "env:SUPERHISTORY_CANARY_OAUTH"
[sources.mail_refused]
{mail}address = "refused@example.com"
password = "cmd:printf %s {CANARY['cmd']}"
[sources.mail_odd]
{mail}address = "odd@example.com"
password = "cmd:printf ok"
[sources.mail_slow]
{mail}address = "slow@example.com"
password = "cmd:sleep 5; echo {CANARY['cmdtimeout']}"
[sources.mail_failing]
{mail}address = "failing@example.com"
password = "cmd:echo {CANARY['cmdfail']} >&2; exit 3"
"""

    def _connect(self):
        conn = Gmail(None)
        original = conn.login

        def login(user, password):
            conn.user = user
            return original(user, password)

        conn.login = login
        return conn

    @contextlib.contextmanager
    def captured(self):
        """Python's stdout and stderr, and the file descriptors under them, so a child process is caught too."""
        out, err = io.StringIO(), io.StringIO()
        files = [tempfile.TemporaryFile(), tempfile.TemporaryFile()]
        sys.stdout.flush()
        sys.stderr.flush()
        saved = [os.dup(1), os.dup(2)]
        try:
            os.dup2(files[0].fileno(), 1)
            os.dup2(files[1].fileno(), 2)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                yield
        finally:
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            for fd in saved:
                os.close(fd)
            for fh in files:
                fh.seek(0)
                self.seen.append(fh.read().decode("utf-8", "replace"))
                fh.close()
            self.seen += [out.getvalue(), err.getvalue()]

    def kb(self, *argv, stdin=""):
        with self.captured(), mock.patch.object(sys, "stdin", io.StringIO(stdin)):
            try:
                return cli.main(list(argv))
            except SystemExit as exc:
                return exc.code
            except Exception as exc:  # a command that crashes prints its traceback, so that is output too
                self.seen.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
                return 1

    def call(self, fn, *args, **kwargs):
        """A library call: its printed output and the text of any exception it raises, cause and context included."""
        with self.captured():
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                self.seen.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))

    def written(self):
        out = []
        for folder in (self.paths.data_dir, self.paths.log_dir, self.paths.cache_dir):
            for root, _, files in os.walk(folder):
                for name in files:
                    with open(os.path.join(root, name), "rb") as fh:
                        out.append(f"{os.path.join(root, name)}\n" + fh.read().decode("latin-1"))
        return out

    def assert_no_canary(self):
        channels = {"output and exceptions": "\n".join(self.seen), "log records": "\n".join(self.records),
                    "files": "\n".join(self.written()), "AI requests": "\n".join(Model.sent)}
        planted = {**CANARY, "number": str(NUMBER), "pem": PEM_BODY, "pem reversed": PEM_BODY[::-1],
                   "long pem": LONG_PEM_LINE}
        for name, text in channels.items():
            for kind, canary in planted.items():
                self.assertNotIn(canary, text, f"the {kind} canary reached {name}")
                # A cut before masking keeps a token's start or its end, too short to mask, so those count too.
                for part, piece in (("start", canary[:12]), ("end", canary[-12:])):
                    self.assertNotIn(piece, text, f"the {part} of the {kind} canary reached {name}")
        return channels

    def _check_log_lines(self):
        try:
            with open(self.paths.log("check")) as fh:
                return sum(1 for _ in fh)
        except FileNotFoundError:
            return 0

    @staticmethod
    def cut_turn():
        """An assistant turn whose last 1,500 characters, all the check keeps, hold a password but not its name."""
        return "password=" + CANARY["turntail"] + " " + "." * (1499 - len(CANARY["turntail"]))

    def drive_setup(self):
        """superhistory setup from the start, over the existing config, with answers and a typed key that hold secrets."""
        kept = open(self.paths.config_file).read()
        answers = {"Replace it": "yes", "computer's name": "desk", "Folder of notes": self.notes,
                   "leave out": f"Private, {CANARY['setupexclude']}", "Gmail address": "", "Which one": "anthropic",
                   "Install the hook": "no", "in the background": "no", "Read everything now": "no"}

        def ask(question, default=""):
            return next((a for q, a in answers.items() if q in question), default)

        shown = len(self.seen)
        with mock.patch.object(cli, "_ask", ask), mock.patch.object(cli, "_secret", lambda q: CANARY["setupkey"]):
            self.kb("setup")
        self.assertIn("Wrote ", "".join(self.seen[shown:]), "setup ran to the end")
        with open(self.paths.config_file, "w") as fh:
            fh.write(kept)

    def drive_stored_rows(self):
        """Rows that reach the store without store_item: an item another store sent, written as Sync
        writes it, and rows an earlier version of mask() stored, which today's changes. Search, get and summarize read
        both, and an old row's text is cut at a title, a snippet, a page, a summarizer slice and between two chunks,
        which remask has to join to mask."""
        db = store.connect(self.paths.db)
        store.replace_rows(db, "notes", "sync.md", [
            {"kind": "doc", "ts": NOW.isoformat(), "title": "walrus sync" + across("synctitle")[11:],
             "content": f"walrus ledger {CANARY['synctext']}",
             "metadata": {"url": f"https://x.example/?token={CANARY['syncmeta']}", "password": CANARY["syncpw"]}}])
        split = "." * (store.CHUNK - 16) + CANARY["syncsplit"] + "." * store.CHUNK
        store.replace_rows(db, "notes", "sync-long.md", [
            {"kind": "raw", "ts": NOW.isoformat(), "title": "split", "content": split[i:i + store.CHUNK],
             "metadata": {"hash": "synclong", "chunk": n, "chunks": 3}}
            for n, i in enumerate(range(0, len(split), store.CHUNK))])
        with self.assertRaises(ValueError, msg="an id with no host is read whole"):
            store.replace_rows(db, "notes", f"password:{CANARY['barepw']}", [
                {"kind": "doc", "ts": NOW.isoformat(), "title": "t", "content": "walrus bare", "metadata": {}}])
        with self.assertRaises(ValueError, msg="an id that looks like a secret is refused, as ingest refuses it"):
            store.replace_rows(db, "notes", f"desk:key-{CANARY['syncid']}.md", [
                {"kind": "doc", "ts": NOW.isoformat(), "title": "t", "content": "walrus id", "metadata": {}}])
        for n, text in enumerate(("walrus append password=", f"{CANARY['syncappend']} walrus deployment")):
            with self.assertRaises(ValueError, msg="chunks added one write at a time would be masked apart"):
                store.replace_rows(db, "notes", "sync-append.md", [
                    {"kind": "raw", "ts": NOW.isoformat(), "title": "append", "content": text,
                     "metadata": {"hash": "append", "chunk": n, "chunks": 2}}], kinds=())
        db.commit()
        self.assert_no_canary()  # here, before remask below would mask what replace_rows let through
        old = (NOW - timedelta(hours=2)).isoformat()
        size = 200_000
        long = across("oldslice") + "." * (CUT["oldpage"] - len(across("oldslice"))) + CANARY["oldpage"]
        pad = CUT["oldsplit"] - 14 - len(long)  # words, so the snippet around the match is short enough to show
        long += "." * (pad % 6) + "lorem " * (pad // 6) + "walrus ledger " + CANARY["oldsplit"]
        long += "." * (size - len(long))
        rows = [("doc", "old-short.md", "walrus old" + across("oldtitle")[10:],
                 f"walrus ledger {CANARY['oldsnip']} end", {"hash": "oldshort"}),
                ("doc", "old-check.md", "walrus check" + across("oldcheck")[12:], "walrus ledger deploys",
                 {"hash": "oldcheck"})]
        parts = store.chunk(long)
        own = ids.prefix(ids.install_id(self.paths))  # this install's own item, which its summarizer reads
        rows += [("raw", f"{own}old-long.md", "Long notes", part, {"hash": "oldlong", "chunk": i, "chunks": len(parts)})
                 for i, part in enumerate(parts)]
        # A key across three chunks: the chunk that matches holds body lines and the footer, but not the header.
        parts = store.chunk("p" * 3820 + "\n" + LONG_PEM + "\nwalrus ledger deployment notes\n")
        rows += [("raw", "old-pem.md", "Key notes", part, {"hash": "oldpem", "chunk": i, "chunks": len(parts)})
                 for i, part in enumerate(parts)]
        rows.append(("doc", f"desk:key-{CANARY['oldid']}.md", "zebra", "zebra quokka ledger", {"hash": "oldid"}))
        older = (NOW - timedelta(hours=3)).isoformat()
        db.executemany("INSERT INTO docs(kind, source, source_id, ts, title, content, metadata, masked) "
                       "VALUES (?, 'notes', ?, ?, ?, ?, ?, '0123456789abcdef')",
                       [(kind, sid, older if sid == "old-pem.md" else old, title, text, json.dumps(meta))
                        for kind, sid, title, text, meta in rows])
        db.commit()
        rids = [r[0] for r in db.execute("SELECT id FROM docs WHERE source_id IN ('sync.md', 'old-short.md', ?) "
                                         "ORDER BY id", (f"{own}old-long.md",))]
        db.close()
        self.kb("search", "walrus ledger")
        Model.query = "walrus ledger"
        sent = len(Model.sent)
        self.kb("context", "--session", "s5",
                stdin=json.dumps({"message": "What does the walrus ledger record about deploys?", "before": []}))
        self.assertTrue(any("[Records]" in body and "walrus" in body for body in Model.sent[sent:]),
                        "the check read the rows' excerpts")
        Model.query = "zebra quokka"  # only the old row whose id holds a secret matches, so the lookup would pick it
        self.kb("context", "--session", "s6",
                stdin=json.dumps({"message": "What did the zebra quokka ledger record?", "before": []}))
        Model.query = None
        pem = [r[0] for r in store.connect(self.paths.db, readonly=True).execute(
            "SELECT id FROM docs WHERE source_id = 'old-pem.md' ORDER BY id")]
        self.kb("get", str(pem[-1]))
        for rid in (rids[0], rids[1], rids[2], rids[-1]):
            self.kb("get", str(rid))
        sent = len(Model.sent)
        self.kb("summarize", "--limit", "2")
        self.assertTrue(any("Long notes" in body for body in Model.sent[sent:]), "the old long item was summarized")

    def drive_trial_month(self):
        month = os.path.join(self.tmp, "month")
        os.makedirs(month)
        records = [("notes", "ok.md"), ("notes", f"key-{CANARY['trialid']}.md"), (CANARY["trialsource"], "x.md")]
        with open(os.path.join(month, "items.jsonl"), "w") as fh:
            for source, sid in records:
                fh.write(json.dumps({"source": source, "source_id": sid, "ts": NOW.isoformat(), "title": "t",
                                     "content": "pelican notes", "meta": {}}) + "\n")
        with open(os.path.join(month, "questions.jsonl"), "w") as fh:
            fh.write(json.dumps({"id": "q1", "question": "Pelican?", "answer": "", "evidence": [], "kind": "none"}))
        shown = len(self.seen)
        self.kb("try", "--month", month)
        self.assertIn("Loaded 1 made-up records", "".join(self.seen[shown:]), "only the ordinary record loads")

    def drive_hooks(self):
        """Every agent's hook gets a prompt, a session name and earlier turns that hold secrets, and superhistory hooks edits a
        user's settings that hold one."""
        transcript = os.path.join(self.tmp, "session.jsonl")
        with open(transcript, "w") as fh:
            for role, text in (("user", f"the deploy key is {CANARY['transcript']}"), ("assistant", self.cut_turn())):
                fh.write(json.dumps({"type": role, "message": {"content": text}}) + "\n")
        prompt = f"What is the pelican staging token? Mine is {CANARY['hookprompt']}"
        events = {
            "claude-code": {"prompt": prompt, "session_id": CANARY["hooksession"], "transcript_path": transcript},
            "codex": {"prompt": prompt, "session_id": CANARY["hooksession"], "transcript_path": transcript},
            "hermes": {"session_id": CANARY["hooksession"], "user_message": prompt, "platform": "cli",
                       "conversation_history": [{"role": "user", "content": CANARY["transcript"]},
                                                {"role": "assistant", "content": self.cut_turn()}]},
            "opencode": {"session_id": CANARY["hooksession"], "message": prompt},
            "copilot": {"prompt": prompt, "sessionId": CANARY["hooksession"]},
        }
        logged = self._check_log_lines()
        for agent, event in events.items():
            self.kb("hook", agent, stdin=json.dumps(event))
        self.assertGreaterEqual(self._check_log_lines() - logged, len(events), "every agent's hook ran the check")
        settings = os.path.join(self.home, ".claude", "settings.json")
        os.makedirs(os.path.dirname(settings), exist_ok=True)
        with open(settings, "w") as fh:
            json.dump({"env": {"API_TOKEN": CANARY["hooksettings"]}}, fh)
        os.makedirs(os.path.join(self.home, ".codex"), exist_ok=True)
        for action in ("status", "install", "status", "uninstall"):
            self.kb("hooks", action, "--command", os.path.join(self.bin, "superhistory"))

    def test_every_command(self):
        self.kb("ingest")
        db = store.connect(self.paths.db, readonly=True)
        self.addCleanup(db.close)
        rows = [r[0] for r in db.execute("SELECT id FROM docs ORDER BY id")]
        self.assertTrue(rows, "ingest stored something to search")
        failing = dict(store.failing(db))
        self.drive_stored_rows()
        self.kb("search", "staging token")
        self.kb("search", "pelican", "--source", "issues")
        for rid in rows[:40]:
            self.kb("get", str(rid))
        self.kb("stats")
        self.kb("summarize", "--estimate")
        self.kb("summarize", "--limit", "2")
        self.kb("context", "--session", CANARY["session"],
                stdin=json.dumps({"message": f"What is the staging token for pelican? {CANARY['contextmessage']}",
                                  "before": [{"role": "user", "text": f"It was {CANARY['transcript']}"},
                                             {"role": "assistant", "text": self.cut_turn()}]}))
        self.assertTrue(os.listdir(self.paths.seen_dir), "the lookup kept its session's records, so it really ran")
        self.kb("ai", "test")
        for mode in ("refuse", "garbage", "status", "pem", "hugeerror"):
            Model.mode = mode
            self.kb("ai", "test")
            self.kb("context", "--session", "s2", stdin=json.dumps({"message": "What did Dana say about pelican?",
                                                                     "before": []}))
            self.kb("summarize", "--limit", "1")
        Model.mode = "ok"
        self.kb("ai", "list")
        self.kb("paths")
        self.kb("config")
        self.kb("supersede", str(rows[0]), str(rows[-1]))
        self.kb("remask")
        login = mock.Mock(side_effect=[RuntimeError(f"denied {CANARY['http']}"), CANARY["oauthtoken"]])
        with mock.patch.object(gmail, "oauth_login", login):
            self.kb("login", "mail_oauth")
            self.kb("login", "mail_oauth")
        self.assertEqual(login.call_count, 2, "both sign-ins reached Google's step")
        self.assertEqual(login.call_args[0][1], CANARY["oauthsecret"], "the client secret was read")
        kept = open(self.paths.config_file).read()
        with open(self.paths.config_file, "w") as fh:
            fh.write(kept.replace('model = "fake"', f'model = "{CANARY["aimodel"]}"'))
        self.kb("ai", "test")
        with open(self.paths.config_file, "w") as fh:
            fh.write(kept)
        self.drive_hooks()
        self.kb("try")
        self.kb("try", "--question", "q1")
        shown = len(self.seen)
        for action in ("status", "on", "status", "off"):
            self.kb("schedule", action, "--command", os.path.join(self.bin, "superhistory"))
        self.assertIn("[masked]", "".join(self.seen[shown:]), "the scheduler's own output was shown, masked")
        self.drive_trial_month()
        self.drive_setup()
        with open(self.paths.config_file, "a") as fh:
            fh.write(f'"{CANARY["configkey"]}" = 1\n')
        self.kb("stats")
        channels = self.assert_no_canary()
        self.assertIn("[masked]", channels["files"], "masked text was stored, so the canaries were read")
        names = [r[0] for r in db.execute("SELECT source_id FROM docs")]
        self.assertFalse([n for n in names if "key-" in n], "files named after a token are never stored")
        self.assertIn("whose name looks like it holds a secret", channels["files"])
        self.assertTrue(any("Rotated" in body for body in Model.sent), "the sorter sent its thread lines")
        self.assertTrue(any("staging token" in body for body in Model.sent), "the check sent the user's message")
        for unit in ("notes@noisy", "notes@garbage", "issues_down@desk", "mail_refused@desk", "mail_odd@desk",
                     "mail_long@desk",
                     "mail_slow@desk", "mail_failing@desk"):
            self.assertIn(unit, failing, f"{unit} was driven into its failure path")

    def test_every_library_entry_point(self):
        cfg = config.load(self.paths.config_file)
        db = store.connect(self.paths.db)
        self.addCleanup(db.close)
        self.call(ingest.run, db, cfg, self.cloud)
        self.call(ingest.apply, db, "notes", "desk", Item(f"x-{CANARY['notepath']}.md", NOW.isoformat(),
                                                          CANARY["itemtext"], CANARY["itemtext"],
                                                          {"url": f"https://x.example/?token={CANARY['itemmeta']}"}, 0))
        self.call(ingest.apply, db, "notes", "desk", Item("meta.md", NOW.isoformat(), "keys", "keys",
                                                          {CANARY["metakey"]: "public",
                                                           "nested": {CANARY["metakey"]: [CANARY["metakey"]]}}, 0))
        self.call(check.write_log, self.paths.log("check"), {"nested": {CANARY["metakey"]: CANARY["metakey"]}})
        self.call(ingest.apply, db, "notes", "desk", Item("numbers.md", NOW.isoformat(), "n", "n",
                                                          {"password": NUMBER, "auth": {"passcode": NUMBER}}, 0))
        self.call(check.write_log, self.paths.log("check"), {"password": NUMBER})
        self.call(remote.decode, json.dumps({"oops": PEM}))
        self.call(gmail._ok, ("NO", [PEM.encode()]))
        self.call(github.run_gh, os.path.join(self.bin, "gh-fail"), ["issue", "view", CANARY["ghargs"]])
        db.commit()
        self.call(search.search, db, "staging token")
        model = ai.load(cfg.ai)
        self.call(check.lookup, db, "What is the staging token for pelican?", [], "", set(), model, 10)
        self.call(check.answer, self.paths, cfg, json.dumps({"message": "What is the staging token?", "before": []}),
                  "s3")
        self.seen.append(self.call(check.answer, self.paths, cfg,
                                   json.dumps({"message": "What is the staging token for pelican?", "before": []}),
                                   "s4") or "")
        self.call(summarize.run, db, cfg, model, self.cloud, 2)
        Model.mode = "long"
        db.execute("DELETE FROM docs WHERE kind IN ('segment', 'atom')")
        db.execute("DELETE FROM ingest_state WHERE source = ?", (summarize.FAILED,))
        db.commit()
        self.call(summarize.run, db, cfg, model, self.cloud, 1)
        kinds = {r[0] for r in db.execute("SELECT DISTINCT kind FROM docs WHERE kind IN ('segment', 'atom')")}
        self.assertEqual(kinds, {"segment", "atom"}, "the long summary was written, so its cuts were made")
        Model.mode = "ok"
        Model.mode = "joined"
        db.execute("DELETE FROM docs WHERE kind IN ('segment', 'atom')")
        db.execute("DELETE FROM ingest_state WHERE source = ?", (summarize.FAILED,))
        db.commit()
        self.call(summarize.run, db, cfg, model, self.cloud, 1)
        self.assertTrue(db.execute("SELECT 1 FROM docs WHERE kind = 'segment'").fetchone(), "the joined row is stored")
        for mode in ("refuse", "garbage", "status", "pem", "hugeerror"):
            Model.mode = mode
            self.call(model.ask, ai.Prompt("s", "u", 10), 10)
            self.call(check.lookup, db, "What did Dana decide about the pelican launch?", [], "", set(), model, 10)
            self.call(summarize.run, db, cfg, model, self.cloud, 1)
        Model.mode = "ok"
        self.call(ai.load, dataclasses.replace(cfg.ai, options=dataclasses.replace(cfg.ai.options,
                                                                                 model=CANARY["aimodel"])))
        self.call(api.post, f"bad-url-{CANARY['http']}", {}, {}, 1)
        self.call(ai_cli.run, ai_cli.Call([os.path.join(self.tmp, CANARY["aipath"], "model")], "", {}, None),
                  self.tmp, 5, "model")
        for name in ("mail_slow", "mail_failing", "mail_refused"):
            self.call(cfg.source(name).options.password.reveal)
        self.call(config.parse, f'"{CANARY["configkey"]}" = 1\n')
        self.call(remote.decode, json.dumps({"oops": CANARY["sshout"]}))
        self.call(ingest.apply, db, "notes", "desk", Item("keyed.md", NOW.isoformat(), "keyed", "keyed",
                                                          {"password": CANARY["metapw"],
                                                           "auth": {"api_key": CANARY["metanested"]}}, 0))
        self.call(ingest.apply, db, "notes", "desk", Item("dated.md", CANARY["timestamp"], "dated", "dated", {}, 0))
        self.call(ingest.apply, db, CANARY["sourcename"], "desk", Item("a.md", NOW.isoformat(), "t", "t", {}, 0))
        self.call(ingest.apply, db, "notes", CANARY["hostname"], Item("a.md", NOW.isoformat(), "t", "t", {}, 0))
        for name in ("machine", "hosts", "sources"):
            text = {"machine": f'machine = "{CANARY["machinename"]}"\n',
                    "hosts": f'[hosts.{CANARY["hostname"]}]\nssh = "x"\n',
                    "sources": f'[sources.{CANARY["sourcename"]}]\ntype = "notes"\npath = "/n"\n'}[name]
            self.call(config.parse, text.replace("Z", "z"))
        odd = dataclasses.replace(cfg, machine=CANARY["machinename"], sources=tuple(
            dataclasses.replace(s, name=CANARY["sourcename"], hosts=(CANARY["machinename"],)) for s in cfg.sources[:1]))
        self.call(ingest.run, db, odd, self.cloud, with_summaries=False)
        marked = SourceType(notes.Options, lambda run: iter([
            Item("marked.md", NOW.isoformat(), "marked", "marked", {}, CANARY["mark"]), Progress(CANARY["mark"]),
            Item("numbered.md", NOW.isoformat(), "numbered", "numbered", {"passcode": NUMBER}, 1)]))
        with mock.patch.object(notes, "TYPE", marked):
            self.call(remote.serve, {"verb": "extract", "type": "notes", "since": None, "exclude": [],
                                     "options": {"path": self.notes}})
            unit = dataclasses.replace(cfg.sources[0], type=marked)
            self.call(ingest.run_unit, db, unit, "desk", ingest.LocalReader(), self.cloud)
        missing = os.path.join(self.tmp, CANARY["notepath2"], "gone")
        locked = os.path.join(self.tmp, f"locked-{CANARY['notepath2']}")
        os.makedirs(os.path.join(locked, "inside"))
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o755)
        for root in (missing, locked):
            for verb in ("extract", "live_ids"):
                self.call(remote.serve, {"verb": verb, "type": "notes", "since": None, "exclude": [],
                                         "options": {"path": root}})
        self.call(github.run_gh, os.path.join(self.bin, "ghtail"), ["search", "issues"])
        self.call(ai_cli.run, ai_cli.Call([os.path.join(self.bin, "aitail")], "", {}, None), self.tmp, 5, "aitail")
        Model.mode = "status"
        with mock.patch.object(gmail, "TOKEN_URL", f"http://127.0.0.1:{self.server.server_address[1]}/token"):
            self.call(gmail._token_request, {"grant_type": "refresh_token"})
        Model.mode = "ok"
        db.commit()
        self.call(remote.serve, {"verb": "extract", "type": "notes", "since": None, "exclude": [],
                                 "options": {"path": "/n", CANARY["remoteopt"]: 1}})
        self.call(github.run_gh, os.path.join(self.bin, "gh-fail"), ["search", "issues"])
        self.call(github.run_gh, os.path.join(self.tmp, CANARY["ghpath"], "gh"), ["search", "issues"])
        self.call(youtube.listing, youtube.Options("@sam", yt_dlp=os.path.join(self.bin, "ghtail")))
        self.call(youtube.read_videos, youtube.Options("@sam", yt_dlp=os.path.join(self.bin, "gh-fail")), ["v1"])
        self.call(youtube.listing, youtube.Options("@sam", yt_dlp=os.path.join(self.tmp, CANARY["ytpath"], "yt-dlp")))
        self.call(youtube.fetch, f"http://127.0.0.1:1/{CANARY['yturl']}")
        with mock.patch.dict(os.environ, {"EDEN_CANARY_KEY": CANARY["edenkey"]}), \
                mock.patch.object(eden, "API", f"http://127.0.0.1:{self.server.server_address[1]}/v1/tools/"):
            for mode in ("refuse", "garbage", "status", "hugeerror", "edenrefuse"):
                Model.mode = mode
                self.call(eden.call, config.Secret("env:EDEN_CANARY_KEY"), "eden_search_social_content", {})
            Model.mode = "ok"
        self.call(git.run_git, ["rev-parse", CANARY["giterr"]], self.tmp)
        self.call(git.run_git, ["status"], os.path.join(self.tmp, CANARY["gitcwd"]))
        for verb in ("extract", "live_ids"):
            self.call(remote.serve, {"verb": verb, "type": "notes", "since": None, "exclude": [],
                                     "options": {"path": self.notes}})
        conn = Gmail("odd@example.com")
        conn.select('"[Gmail]/All Mail"', readonly=True)
        self.call(gmail._search, conn, "ALL")
        self.call(gmail._ok, ("NO", [CANARY["imaperr"].encode()]))
        self.assert_no_canary()


if __name__ == "__main__":
    unittest.main()
