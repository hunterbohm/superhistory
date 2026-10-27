"""The canary harness. A different secret is planted in every kind of input kb takes from outside: config secrets,
an environment variable, `cmd:` secrets that fail or time out, item text and metadata, file and repository paths,
the output and errors of ssh, gh and git, and HTTP and IMAP answers, malformed ones included. Then every kb command
and every library entry point the Sync client and the Cloud engine call runs, and no canary may reach stdout,
stderr, a log record, a file kb writes under its data, log or cache folders, check.log, or the text of an exception.

Each canary has a secret's shape, because mask() can only mask what looks like a secret. A path this harness does
not cover is a missing case: add the input or the call here.
"""
import contextlib
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

from kb import ai, check, cli, config, ingest, remote, search, store, summarize
from kb.ai import api
from kb.ai import cli as ai_cli
from kb.paths import Paths
from kb.sources import Excludes, Item, Progress, Run, SourceType, git, github, gmail, notes
from tests.test_gmail import FakeGmail

KINDS = ("env", "cmd", "cmdfail", "cmdtimeout", "configkey", "itemtext", "itemmeta", "metakey", "notepath",
         "notepath2", "repopath", "sshout", "ssherr", "ghout", "gherr", "ghpath", "gitout", "giterr", "gitcwd", "http",
         "httpbad", "httpstatus", "modeltext", "imap", "imapbad", "imaperr", "remoteopt", "aipath", "session")
CANARY = {kind: f"sk-ant-api03-canary-{kind}-" + "Z" * 24 for kind in KINDS}
# Secrets that only their field name gives away, as in "password=...": mask() knows them by the name in front.
KEYED = ("metapw", "metanested", "ghtail", "aitail")
CANARY.update({kind: f"canary{kind}Zz12345678" for kind in KEYED})
NAMES = ("machinename", "hostname", "sourcename", "timestamp", "mark")
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
    print(json.dumps({{"title": "Leaked {ghout}", "body": "token {ghout}", "state": "OPEN",
                      "author": {{"login": "dana"}}, "createdAt": "{updated}", "updatedAt": "{updated}",
                      "closedAt": None, "labels": [], "url": "https://github.com/acme/demo/issues/7?t={ghout}",
                      "comments": [{{"author": {{"login": "dana"}}, "createdAt": "{updated}", "body": "{ghout}"}}]}}))
"""


class Model(BaseHTTPRequestHandler):
    """An Anthropic-compatible server. In "ok" it answers every call with JSON that carries a canary; in "refuse"
    it echoes the key it was sent; in "garbage" it answers with something other than JSON."""
    mode = "ok"

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        if Model.mode == "status":
            self.wfile.write(CANARY["httpstatus"].encode() + b"\r\n\r\n")
            self.close_connection = True
            return
        if Model.mode == "refuse":
            body = json.dumps({"error": {"message": f"invalid x-api-key {self.headers.get('x-api-key')} "
                                                    f"{CANARY['http']}"}}).encode()
            self.send_response(401)
        elif Model.mode == "garbage":
            body = f"<html>upstream error {CANARY['httpbad']}</html>".encode()
            self.send_response(200)
        else:
            text = json.dumps({"query": f"pelican {CANARY['modeltext']}", "search": True, "scores": {"1": 2, "2": 2},
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

    def login(self, user, password):
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
        self.paths = Paths.under(os.path.join(self.tmp, "kb"))
        self.seen = []
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self._program("ssh", FAKE_SSH.format(python=sys.executable, ssherr=CANARY["ssherr"], sshout=CANARY["sshout"]))
        updated = (NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for name, fail in (("gh-ok", False), ("gh-fail", True)):
            self._program(name, FAKE_GH.format(python=sys.executable, fail=fail, gherr=CANARY["gherr"],
                                               ghout=CANARY["ghout"], updated=updated))
        for kind in ("ghtail", "aitail"):
            # The field name ends up outside the last 400 characters an error keeps; only masking first catches it.
            tail = f"password={CANARY[kind]} " + "." * (400 - len(CANARY[kind]) - 1)
            self._program(kind, f"#!{sys.executable}\nimport sys\nsys.stderr.write({tail!r})\nsys.exit(1)\n")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        Model.mode = "ok"
        self.notes = os.path.join(self.tmp, f"notes-{CANARY['notepath']}")
        os.makedirs(os.path.join(self.notes, "Area"))
        texts = {"plan.md": f"---\nsource: https://vault.example/?api_key={CANARY['itemmeta']}\n---\n"
                            f"The pelican staging token is {CANARY['itemtext']}\n" + "Long line of notes.\n" * 800,
                 f"key-{CANARY['notepath']}.md": "the first file named after a token",
                 f"key-{CANARY['notepath2']}.md": "the second file named after a token"}
        for name, text in texts.items():
            with open(os.path.join(self.notes, "Area", name), "w") as fh:
                fh.write(text)
            os.utime(os.path.join(self.notes, "Area", name), (time.time() - 86400,) * 2)
        self.repos = os.path.join(self.home, "code")
        self._repo(os.path.join(self.repos, f"app-{CANARY['repopath']}"))
        broken = os.path.join(self.repos, "broken")
        os.makedirs(broken)
        with open(os.path.join(broken, ".git"), "w") as fh:
            fh.write(f"gitdir: /nonexistent/{CANARY['giterr']}\n")
        os.makedirs(os.path.dirname(self.paths.config_file), exist_ok=True)
        with open(self.paths.config_file, "w") as fh:
            fh.write(self._config())
        env = {"HOME": self.home, "KB_HOME": os.path.dirname(self.paths.config_file), "KB_CANARY_KEY": CANARY["env"],
               "PATH": self.bin + os.pathsep + os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1"}
        # create=True lets this file run on code from before the masking work, where it must find the leaks.
        for patch in (mock.patch.dict(os.environ, env), mock.patch.object(config, "SECRET_TIMEOUT", 0.5, create=True),
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
key = "env:KB_CANARY_KEY"
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
                    "files": "\n".join(self.written())}
        for name, text in channels.items():
            for kind, canary in CANARY.items():
                self.assertNotIn(canary, text, f"the {kind} canary reached {name}")
        return channels

    def test_every_command(self):
        self.kb("ingest")
        db = store.connect(self.paths.db, readonly=True)
        self.addCleanup(db.close)
        rows = [r[0] for r in db.execute("SELECT id FROM docs ORDER BY id")]
        self.assertTrue(rows, "ingest stored something to search")
        failing = dict(store.failing(db))
        self.kb("search", "staging token")
        self.kb("search", "pelican", "--source", "issues")
        for rid in rows[:40]:
            self.kb("get", str(rid))
        self.kb("stats")
        self.kb("summarize", "--estimate")
        self.kb("summarize", "--limit", "2")
        self.kb("context", "--session", CANARY["session"],
                stdin=json.dumps({"message": "What is the staging token for pelican?", "before": []}))
        self.assertTrue(os.listdir(self.paths.seen_dir), "the lookup kept its session's records, so it really ran")
        self.kb("ai", "test")
        for mode in ("refuse", "garbage", "status"):
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
        with mock.patch.object(gmail, "oauth_login", side_effect=RuntimeError(f"denied {CANARY['http']}")):
            self.kb("login", "mail")
        with open(self.paths.config_file, "a") as fh:
            fh.write(f'"{CANARY["configkey"]}" = 1\n')
        self.kb("stats")
        channels = self.assert_no_canary()
        self.assertIn("[masked]", channels["files"], "masked text was stored, so the canaries were read")
        names = [r[0] for r in db.execute("SELECT source_id FROM docs")]
        self.assertFalse([n for n in names if "key-" in n], "files named after a token are never stored")
        self.assertIn("whose name looks like it holds a secret", channels["files"])
        for unit in ("notes@noisy", "notes@garbage", "issues_down@desk", "mail_refused@desk", "mail_odd@desk",
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
        for mode in ("refuse", "garbage", "status"):
            Model.mode = mode
            self.call(model.ask, ai.Prompt("s", "u", 10), 10)
            self.call(check.lookup, db, "What did Dana decide about the pelican launch?", [], "", set(), model, 10)
            self.call(summarize.run, db, cfg, model, self.cloud, 1)
        Model.mode = "ok"
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
            Item("marked.md", NOW.isoformat(), "marked", "marked", {}, CANARY["mark"]), Progress(CANARY["mark"])]))
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
