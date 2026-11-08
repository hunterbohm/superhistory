"""The separator harness. str.splitlines(), str.strip() and regex \\s treat U+2028, U+2029, U+0085, a vertical tab, a
form feed and \\x1c to \\x1e as line breaks or whitespace; JSONL, YAML front matter, git output, TOML, HTML, mail and a
secret's value do not. Each separator goes at a line's start and end, inside a token or a secret, and next to every
marker superhistory reads (quotes, signatures, forwarded originals, front-matter keys, Codex and Hermes wrappers), through
every reader, the masking, secret references and the config. Nothing may be lost, split, re-dated, read as a quote or
a marker, or left unmasked. A reader or an input this file lacks is a missing case: add it here.
"""
import base64
import json
import os
import subprocess
import tempfile
import unittest
from email.message import EmailMessage
from unittest import mock

from kb import agents, config, remote, store
from kb.agents import _turns, codex as codex_agent, hermes as hermes_agent
from kb.config import Secret
from kb.sources import Excludes, Item, Run, claude_code, codex, git, github, gmail, hermes, imessage
from tests.test_codex import conversation
from tests.test_github import FakeGh, GitHubCase
from tests.test_gmail import ME, PRICING, ago
from tests.test_hermes import HermesCase, chat, make_store, tool_call
from tests.test_imessage import Case as IMessageCase, add_message, apple_ns
from tests.test_notes import NotesCase, write

SEPARATORS = {"LS": "\u2028", "PS": "\u2029", "NEL": "\x85", "VT": "\x0b", "FF": "\x0c", "FS": "\x1c", "GS": "\x1d",
              "RS": "\x1e", "US": "\x1f"}
# git writes a control character in a name or a path its own way, so those cases use the three that are text; a
# commit message keeps every separator.
TEXT_SEPARATORS = {k: SEPARATORS[k] for k in ("LS", "PS", "NEL")}


# A form feed is whitespace to HTML too, so <br\f> is a break and <script\f> a script, as they should be.
HTML_SEPARATORS = {k: v for k, v in SEPARATORS.items() if k != "FF"}


class SeparatorCase(unittest.TestCase):
    pass


class MaskTest(SeparatorCase):
    """Into the store: a secret with a separator inside it, before it or after its name is masked whole."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = store.connect(os.path.join(tmp.name, "superhistory.sqlite"))
        self.addCleanup(self.db.close)

    def stored(self, text):
        store.store_item(self.db, "s", "a", "2026-10-01T00:00:00Z", "t", text, {})
        return self.db.execute("SELECT content FROM docs WHERE source_id='a'").fetchone()[0]

    def test_secrets_with_separators_are_masked_whole(self):
        for name, sep in SEPARATORS.items():
            with self.subTest(separator=name):
                token = "sk-ant-api03-" + "Q" * 32
                cases = {f"export password={sep}walrus2secret9": "walrus2secret9",
                         f"export password=abc{sep}def12345": "def12345",
                         f"export password={sep}password": "password=password",
                         f"API_KEY=abc{sep}xyz123456789": "xyz123456789",
                         f"Authorization: Bearer{sep}{token}": token[12:],
                         f"https://user:pa{sep}ss1234@host.example/": "ss1234",
                         f"key{sep}{token}{sep}": token[12:],
                         f"-----BEGIN PRIVATE KEY-----{sep}TUlJRXZRSUJBREFOQmdrcWhraUc5dzBC{sep}"
                         f"-----END PRIVATE KEY-----": "TUlJRXZRSUJBREFOQmdrcWhraUc5dzBC"}
                for text, secret in cases.items():
                    stored = self.stored(text)
                    self.assertNotIn(secret, stored.replace(sep, ""), repr(text))

    def test_a_value_on_the_next_line_is_still_masked(self):
        for text in ('{"password":\n"turtle9secret"}', '{"password":\r\n"turtle9secret"}', "password:\n  turtle9secret",
                     'api_key =\n  "turtle9secret"'):
            with self.subTest(text=text):
                self.assertNotIn("turtle9secret", self.stored(text))

    def test_long_text_comes_back_whole_from_its_chunks(self):
        for name, sep in SEPARATORS.items():
            with self.subTest(separator=name):
                text = ("line\n" * 799 + "x" * 3 + sep) * 3 + sep
                store.store_item(self.db, "s", "long", "2026-10-01T00:00:00Z", "t", text, {})
                rid = self.db.execute("SELECT id FROM docs WHERE source_id='long' LIMIT 1").fetchone()[0]
                self.assertEqual("".join(r[4] for r in store.item_rows(self.db, rid)), text)


def secret_split_by(sep):
    """A secret its field name gives away, with a separator inside: masked whole, nothing of it may be stored."""
    return f"password=abcd{sep}tail9secret"


class ReaderToStoreTest(SeparatorCase):
    """A reader's output goes through the store's masking: a reader that turns a separator into a space first would
    break the secret in two and leave its second half."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.db = store.connect(os.path.join(tmp.name, "superhistory.sqlite"))
        self.addCleanup(self.db.close)

    def assert_stored_masked(self, item):
        store.store_item(self.db, "s", "x", item.ts, item.title, item.content, item.meta)
        title, content, meta = self.db.execute("SELECT title, content, metadata FROM docs WHERE source_id='x'").fetchone()
        self.assertNotIn("tail9secret", title + content + meta)

    def test_hermes_tool_calls_and_results(self):
        for n, (name, sep) in enumerate(SEPARATORS.items()):
            with self.subTest(separator=name):
                root = os.path.join(self.tmp, f"h{n}")
                make_store(os.path.join(root, "state.db"), [("s", "cli", None, 1000.0, [
                    ("user", "run it", {}),
                    ("assistant", "", {"calls": tool_call("terminal", command=f"export {secret_split_by(sep)}")}),
                    ("tool", json.dumps({"output": secret_split_by(sep)}), {"tool": "terminal"}),
                    ("assistant", "done", {})])])
                run = Run(hermes.Options(root), Excludes(("tool",)), None)
                [item] = [e for e in hermes.extract(run) if isinstance(e, Item)]
                self.assert_stored_masked(item)

    def test_gmail_subjects_and_senders(self):
        for name, sep in SEPARATORS.items():
            with self.subTest(separator=name):
                # A header carries such a character in an encoded word, as a mail server delivers it.
                def word(text):
                    return "=?utf-8?b?" + base64.b64encode(text.encode()).decode() + "?="

                raw = (f"From: {word('Ops ' + secret_split_by(sep))} <ops@example.org>\r\n"
                       f"Subject: {word('Keys ' + secret_split_by(sep))}\r\n"
                       "Content-Type: text/plain; charset=utf-8\r\n\r\nSee subject.\r\n").encode()
                thread = gmail.Thread(PRICING, 0, (gmail.Message(1, ago(days=1).timestamp(), ("\\Inbox",),
                                                                 "Ops <ops@example.org>", "Keys"),))
                self.assert_stored_masked(gmail._render(ME, thread, {1: raw}))


class IMessageToStoreTest(IMessageCase, SeparatorCase):
    def test_a_message_with_a_split_secret(self):
        for n, (name, sep) in enumerate(SEPARATORS.items()):
            self.add(950 + n, 1, f"{secret_split_by(sep)} {n}", None, 1, 0, apple_ns(2026, 10, 21, 9, n))
        item = self.items()["iMessage;-;+15550100123:2026-10-21"]
        with tempfile.TemporaryDirectory() as tmp:
            db = store.connect(os.path.join(tmp, "superhistory.sqlite"))
            store.store_item(db, "imessage", "x", item.ts, item.title, item.content, item.meta)
            self.assertNotIn("tail9secret", db.execute("SELECT content FROM docs").fetchone()[0])
            db.close()


class SecretAndConfigTest(SeparatorCase):
    def test_a_secret_reference_keeps_its_separators(self):
        for name, sep in SEPARATORS.items():
            with self.subTest(separator=name):
                value = f"{sep}pw{sep}word{sep}"
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"SUPERHISTORY_SEP_SECRET": value}):
                    path = os.path.join(tmp, "key")
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(value + "\n")
                    self.assertEqual(Secret("env:SUPERHISTORY_SEP_SECRET").reveal(), value)
                    self.assertEqual(Secret(f"file:{path}").reveal(), value)
                    self.assertEqual(Secret(f"cmd:cat {path}").reveal(), value)

    def test_a_config_path_keeps_its_separators(self):
        for name, sep in SEPARATORS.items():
            with self.subTest(separator=name):
                path = f"/notes/a{sep}b/{sep}c"
                text = f"[sources.notes]\npath = {json.dumps(path, ensure_ascii=False)}\n"
                self.assertEqual(config.parse(text).source("notes").options.path, path)


class NotesTest(NotesCase, SeparatorCase):
    def test_front_matter_dates_and_body_lines(self):
        for name, sep in SEPARATORS.items():
            with self.subTest(separator=name):
                write(self.root, "a.md", f"---\nsummary: moved{sep}date: 2019-01-02\ndate: 2026-05-06\n---\n"
                                         f"{sep}Decision{sep}the launch is approved{sep}\n{sep}next line")
                write(self.root, "b.md", f"---\ndate{sep}: 2019-01-02\ndate: 2026-05-06\n---\nbody")
                got = self.items()
                self.assertEqual(got["a.md"].ts[:10], "2026-05-06")
                self.assertEqual(got["b.md"].ts[:10], "2026-05-06")
                self.assertIn(f"Decision{sep}the launch is approved{sep}", got["a.md"].content)

    def test_only_a_whole_delimiter_line_opens_or_closes_front_matter(self):
        june = 1780272000  # 2026-06-01, the files' mtime, which a note without front matter is dated by
        controls = {"plain.md": "---\ndate: 2026-05-06\n---\nbody", "crlf.md": "---\r\ndate: 2026-05-06\r\n---\r\nbody",
                    "spaces.md": "--- \t\ndate: 2026-05-06\n---\t\nbody"}
        for rel, text in controls.items():
            write(self.root, rel, text, june)
        for name, sep in SEPARATORS.items():
            with self.subTest(separator=name):
                write(self.root, "open.md", f"---{sep}\ndate: 2019-01-02\n---\nDecision: launch approved.\n", june)
                write(self.root, "close.md", f"---\ntitle: x\n---{sep}\ndate: 2019-01-02\n---\nbody", june)
                got = self.items()
                self.assertEqual(got["open.md"].ts[:10], "2026-06-01", "a first line of ---{sep} opens nothing")
                self.assertEqual(got["close.md"].ts[:10], "2019-01-02", "a line of ---{sep} closes nothing")
                for rel in controls:
                    self.assertEqual(got[rel].ts[:10], "2026-05-06", rel)


class GmailTest(SeparatorCase):
    def render(self, body, subtype="plain"):
        msg = EmailMessage()
        msg["From"], msg["Subject"] = "Dana <dana@example.org>", "Launch"
        msg.set_content(body, subtype=subtype)
        thread = gmail.Thread(PRICING, 0, (gmail.Message(1, ago(days=1).timestamp(), ("\\Inbox",),
                                                         "Dana <dana@example.org>", "Launch"),))
        return gmail._render(ME, thread, {1: msg.as_bytes()}).content

    def test_nothing_next_to_a_marker_is_read_as_the_marker(self):
        for name, sep in SEPARATORS.items():
            with self.subTest(separator=name):
                plain = [f"{sep}> 5, and the launch is approved.",
                         f"Status\n{sep}> 5, and the launch is approved.",
                         f"Status\n--{sep}\nThe launch is approved.",
                         f"Status\n{sep}--\nThe launch is approved.",
                         f"Status\n---{sep}Original Message---\nThe launch is approved.",
                         f"Status\nOn{sep}Monday the team wrote:\n> earlier\nThe launch is approved.",
                         f"Status\nFrom:{sep}Dana\nSent: Monday\nTo: Sam\nThe launch is approved."]
                html = [f"<div>Decision: the required comparison is<br{sep}>&gt; 5, and the launch is approved.</div>",
                        f"<div>Decision: <script{sep}>the launch is approved.</script></div>",
                        f"<div>Decision: <style{sep}>the launch is approved.</style></div>"] if name in HTML_SEPARATORS else []
                for body in plain:
                    self.assertIn("launch is approved.", self.render(body), repr(body))
                for body in html:
                    self.assertIn("launch is approved.", self.render(body, "html"), repr(body))

    def test_real_markers_still_work(self):
        self.assertNotIn("old reply", self.render("New answer.\n> old reply"))
        self.assertNotIn("Sam\nSent from", self.render("New answer.\n-- \nSam\nSent from my phone"))
        self.assertNotIn("alert", self.render("<div>Hi<script>alert(1)</script></div>", "html"))


class GitTest(SeparatorCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = os.path.realpath(tmp.name)
        patch = mock.patch.dict(os.environ, {"HOME": self.home, "GIT_CONFIG_NOSYSTEM": "1",
                                             "GIT_CONFIG_GLOBAL": os.path.join(self.home, ".gitconfig")})
        patch.start()
        self.addCleanup(patch.stop)

    def git(self, cwd, *args, env=None):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env={**os.environ, **(env or {})})

    def test_names_messages_and_file_names(self):
        for n, (name, sep) in enumerate(TEXT_SEPARATORS.items()):
            with self.subTest(separator=name):
                name = f"Sam{sep}Rivera{sep}"
                self.git(self.home, "config", "--global", "user.name", name)
                self.git(self.home, "config", "--global", "user.email", "other@example.org")
                repo = os.path.join(self.home, "code", f"repo{n}")
                os.makedirs(repo)
                self.git(repo, "init", "-q")
                with open(os.path.join(repo, f"notes{sep}draft.md"), "w") as fh:
                    fh.write("x")
                self.git(repo, "add", "-A")
                who = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": "elsewhere@example.org",
                       "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": "elsewhere@example.org"}
                self.git(repo, "commit", "-q", "-m", f"Rotate{sep}the key", env=who)
                run = Run(git.Options(roots=(os.path.join(self.home, "code"),), window_days=30), Excludes(()), None)
                items = [e for e in git.TYPE.extract(run) if isinstance(e, Item) and e.source_id.endswith("")]
                item = next(i for i in items if f"repo{n}" in i.title)
                self.assertIn(f"Rotate{sep}the key", item.content)
                self.assertIn(f"notes{sep}draft.md", item.content)


class GitMessageTest(GitTest):
    def test_every_separator_inside_a_commit_body(self):
        body = "Update setting\n\n" + "".join(f"Decision{sep}approved {name}\n" for name, sep in SEPARATORS.items())
        repo = os.path.join(self.home, "code", "msgs")
        os.makedirs(repo)
        self.git(self.home, "config", "--global", "user.name", "Sam")
        self.git(self.home, "config", "--global", "user.email", "sam@example.org")
        self.git(repo, "init", "-q")
        self.git(repo, "commit", "-q", "--allow-empty", "--cleanup=verbatim", "-m", body)
        run = Run(git.Options(roots=(os.path.join(self.home, "code"),), window_days=30), Excludes(()), None)
        [item] = [e for e in git.TYPE.extract(run) if isinstance(e, Item)]
        for name, sep in SEPARATORS.items():
            self.assertIn(f"Decision{sep}approved {name}", item.content)


class SessionTest(SeparatorCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def jsonl(self, path, records):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for record in records:
                fh.write((record if isinstance(record, str) else json.dumps(record, ensure_ascii=False)) + "\n")

    def test_claude_code_turns_arrive_whole(self):
        for n, (name, sep) in enumerate(SEPARATORS.items()):
            with self.subTest(separator=name):
                root = os.path.join(self.tmp, f"cc{n}")
                text = f"{sep}first{sep}second{sep}"
                self.jsonl(os.path.join(root, "-work", "s1.jsonl"), [
                    {"type": "user", "timestamp": "2026-10-18T10:00:00Z", "sessionId": "s1", "cwd": "/work",
                     "message": {"content": text}},
                    {"type": "assistant", "timestamp": "2026-10-18T10:01:00Z", "sessionId": "s1",
                     "message": {"content": [{"type": "text", "text": text}]}}])
                run = Run(claude_code.Options(roots=(root,)), Excludes(()), None)
                [item] = [e for e in claude_code.TYPE.extract(run) if isinstance(e, Item)]
                self.assertIn(f"first{sep}second", item.content)

    def test_codex_turns_arrive_whole_and_a_split_marker_is_not_a_marker(self):
        for n, (name, sep) in enumerate(SEPARATORS.items()):
            with self.subTest(separator=name):
                home = os.path.join(self.tmp, f"cx{n}")
                lines = [json.dumps(json.loads(line), ensure_ascii=False, separators=(",", ":"))
                         for line in conversation(prompt=f"#{sep}AGENTS.md instructions{sep}fix the zebra test")]
                sid = json.loads(lines[0])["payload"]["id"]
                self.jsonl(os.path.join(home, ".codex", "sessions", "2026", "06", "03", f"rollout-a-{sid}.jsonl"), lines)
                run = Run(codex.Options(roots=(os.path.join(home, ".codex", "sessions"),)), Excludes(()), None)
                [item] = [e for e in codex.TYPE.extract(run) if isinstance(e, Item)]
                self.assertIn(f"AGENTS.md instructions{sep}fix the zebra test", item.content)

    def test_agent_hook_transcripts_arrive_whole(self):
        for n, (name, sep) in enumerate(SEPARATORS.items()):
            with self.subTest(separator=name):
                path = os.path.join(self.tmp, f"t{n}.jsonl")
                self.jsonl(path, [{"type": "user", "message": {"content": f"one{sep}two"}},
                                  {"type": "assistant", "message": {"content": f"three{sep}four"}}])
                turns = _turns.turns_before(path, "next")
                self.assertEqual([t["text"] for t in turns], [f"one{sep}two", f"three{sep}four"])
                history = [{"role": "user", "content": f"one{sep}two"}, {"role": "assistant", "content": f"three{sep}four"}]
                hermes_turns = hermes_agent.request({"session_id": "s", "user_message": "next", "platform": "cli",
                                                     "conversation_history": history})[1]
                self.assertEqual([t["text"] for t in hermes_turns], [f"one{sep}two", f"three{sep}four"])


class HermesSourceTest(HermesCase, SeparatorCase):
    def test_messages_and_wrappers(self):
        for n, (name, sep) in enumerate(SEPARATORS.items()):
            with self.subTest(separator=name):
                root = os.path.join(self.root, str(n))
                make_store(os.path.join(root, "state.db"), [
                    ("s1", "cli", None, 1000.0, chat(f"one{sep}two", f"three{sep}four")),
                    ("s2", "cli", None, 2000.0, chat(f"[OUT-OF-BAND{sep}USER MESSAGE] keep me", "ok")),
                    ("s3", "cli", None, 3000.0, chat(f"[OUT-OF-BAND USER MESSAGE]{sep}inner text{sep}"
                                                     "[/OUT-OF-BAND USER MESSAGE]", "ok"))])
                items = self.items(root=root)
                self.assertIn(f"one{sep}two", items["default:s1"].content)
                self.assertIn(f"three{sep}four", items["default:s1"].content)
                self.assertIn("keep me", items["default:s2"].content)
                self.assertIn("inner text", items["default:s3"].content)


class IMessageTest(IMessageCase, SeparatorCase):
    def test_a_message_keeps_its_words(self):
        for n, (name, sep) in enumerate(SEPARATORS.items()):
            with self.subTest(separator=name):
                self.add(900 + n, 1, f"Approved{sep}the launch{sep}walrus{n}", None, 1, 0, apple_ns(2026, 10, 20, 9, n))
        content = self.items()["iMessage;-;+15550100123:2026-10-20"].content
        for n, _ in enumerate(SEPARATORS):
            # A message is one line by design, so a separator inside it becomes a space; no word is lost.
            self.assertIn(f"Approved the launch walrus{n}", content)


class GitHubTest(GitHubCase, SeparatorCase):
    def test_a_thread_keeps_its_separators(self):
        for n, (name, sep) in enumerate(SEPARATORS.items()):
            with self.subTest(separator=name):
                self.gh.add(n + 1, self.now - 3600, title=f"Launch{sep}plan {n}", body=f"Decision{sep}the launch {n}")
        items = [e for e in self.read(since=self.now - 7200) if isinstance(e, Item)]
        for n, sep in enumerate(SEPARATORS.values()):
            item = next(i for i in items if i.source_id.endswith(f"#{n + 1}"))
            self.assertIn(f"Decision{sep}the launch {n}", item.content)


class RemoteTest(SeparatorCase):
    def test_an_item_crosses_the_remote_protocol_whole(self):
        for name, sep in SEPARATORS.items():
            with self.subTest(separator=name):
                item = Item(f"a{sep}b.md", "2026-10-01T00:00:00Z", f"t{sep}", f"{sep}one{sep}two{sep}",
                            {"path": f"/n/a{sep}b.md"}, 7)
                self.assertEqual(remote.decode(remote.encode(item)), item)


class CodexTrustTest(SeparatorCase):
    def test_a_home_with_a_separator_installs_and_uninstalls_cleanly(self):
        for n, (name, sep) in enumerate(SEPARATORS.items()):
            with self.subTest(separator=name):
                with tempfile.TemporaryDirectory() as tmp:
                    home = os.path.join(tmp, f"team{sep}{n}")
                    os.makedirs(os.path.join(home, ".codex"))
                    with open(os.path.join(home, ".codex", "config.toml"), "w") as fh:
                        fh.write('# mine\nmodel = "x"\n')
                    kb = agents.command("/opt/superhistory", "codex")
                    self.assertNotIn("skipped", [c.action for c in codex_agent.AGENT.install(home, kb)])
                    self.assertTrue(codex_agent.AGENT.installed(home))
                    codex_agent.AGENT.uninstall(home)
                    with open(os.path.join(home, ".codex", "config.toml")) as fh:
                        self.assertEqual(fh.read(), '# mine\nmodel = "x"\n')


if __name__ == "__main__":
    unittest.main()
