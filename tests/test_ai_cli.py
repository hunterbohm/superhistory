import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import quote

from kb import ai, check, cli, config, store
from kb.ai import Busy, NotReady, Prompt
from kb.paths import Paths

WATCHED = ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "OPENAI_API_KEY",
           "CODEX_HOME", "COPILOT_HOME", "XAI_API_KEY", "KB_NESTED", "HOME")
FAKE_CLI = """#!{python}
import json, os, subprocess, sys, time
args = sys.argv[1:]
record = {{"argv": args, "stdin": sys.stdin.read(), "cwd": os.getcwd(),
          "env": {{k: os.environ.get(k) for k in {watched!r}}}}}
if "--prompt-file" in args:
    record["prompt_file"] = open(args[args.index("--prompt-file") + 1]).read()
if record["env"]["COPILOT_HOME"]:
    record["copilot_home_exists"] = os.path.isdir(record["env"]["COPILOT_HOME"])
with open({record_path!r}, "a") as fh:
    fh.write(json.dumps(record) + "\\n")
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "signed-out":
    sys.stderr.write("Error: not logged in. Please log in again.\\n")
    sys.exit(1)
if mode == "crash":
    sys.stderr.write("something broke\\n")
    sys.exit(3)
if mode == "hang":
    subprocess.Popen([sys.executable, "-c", "import os, time; open({child_path!r}, 'w').write(str(os.getpid())); "
                      "time.sleep(60)"])
    time.sleep(60)
if mode == "grok-session":
    folder = os.path.join(os.environ.get("GROK_HOME") or os.path.join(os.environ["HOME"], ".grok"), "sessions",
                          __import__("urllib.parse").parse.quote(
        os.path.realpath(os.getcwd()), safe=""))
    os.makedirs(folder)
    open(os.path.join(folder, "prompt_history.jsonl"), "w").write("{{}}")
answer = os.environ.get("FAKE_ANSWER", "ok")
if "system" in os.environ.get("FAKE_ANSWER_BY", ""):
    system = args[args.index("--system-prompt") + 1]
    answer = json.loads(os.environ["FAKE_ANSWERS"])["scores" if "Score each record" in system else "query"]
if "--output-last-message" in args:
    open(args[args.index("--output-last-message") + 1], "w").write(answer)
else:
    sys.stdout.write(answer)
"""


class CliCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.record_path = os.path.join(self.tmp, "calls.jsonl")
        self.child_path = os.path.join(self.tmp, "child.pid")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.binary = os.path.join(self.tmp, "fake-cli")
        with open(self.binary, "w") as fh:
            fh.write(FAKE_CLI.format(python=sys.executable, watched=WATCHED, record_path=self.record_path,
                                     child_path=self.child_path))
        os.chmod(self.binary, 0o755)
        patcher = mock.patch.dict(os.environ, {
            "HOME": self.home, "ANTHROPIC_API_KEY": "paid-key", "ANTHROPIC_BASE_URL": "http://paid.example",
            "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "OPENAI_API_KEY": "paid-openai",
            "XAI_API_KEY": "paid-xai", "CODEX_HOME": os.path.join(self.home, ".codex")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def model(self, plugin, extra=""):
        text = f'[ai]\nplugin = "{plugin}"\nbinary = {json.dumps(self.binary)}\n{extra}'
        return ai.load(config.parse(text).ai)

    def calls(self):
        with open(self.record_path) as fh:
            return [json.loads(line) for line in fh]

    def ask(self, plugin, extra="", timeout=30, **env):
        with mock.patch.dict(os.environ, env):
            return self.model(plugin, extra).ask(Prompt("Answer with one word.", "Say ok."), timeout)


class CliPluginTest(CliCase):
    def assert_clean_call(self, call):
        self.assertEqual(call["env"]["KB_NESTED"], "1")
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
                     "OPENAI_API_KEY", "XAI_API_KEY"):
            self.assertIsNone(call["env"][name], name)
        self.assertTrue(os.path.basename(call["cwd"]).startswith("kb-ai-"))
        self.assertFalse(os.path.exists(call["cwd"]), "the call's folder is removed")

    def test_claude_code_runs_print_mode_with_no_tools_and_no_session_file(self):
        self.assertEqual(self.ask("claude-code", FAKE_ANSWER='{"query": "x"}'), '{"query": "x"}')
        call, = self.calls()
        self.assert_clean_call(call)
        argv = call["argv"]
        self.assertEqual(argv[:3], ["-p", "--model", "haiku"])
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertIn("--no-session-persistence", argv)
        self.assertEqual(argv[argv.index("--system-prompt") + 1], "Answer with one word.")
        self.assertEqual(call["stdin"], "Say ok.")

    def test_chatgpt_runs_codex_exec_ephemeral_and_reads_the_last_message(self):
        self.assertEqual(self.ask("chatgpt", FAKE_ANSWER="ok from codex"), "ok from codex")
        call, = self.calls()
        self.assert_clean_call(call)
        self.assertEqual(call["env"]["CODEX_HOME"], os.path.join(self.home, ".codex"))
        argv = call["argv"]
        self.assertEqual(argv[0], "exec")
        for flag in ("--ephemeral", "--ignore-user-config", "--skip-git-repo-check"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertEqual(argv[-1], "-")
        self.assertEqual(call["stdin"], "Answer with one word.\n\nSay ok.")

    def test_grok_reads_the_prompt_from_a_file_and_its_session_folder_is_removed(self):
        self.assertEqual(self.ask("grok", FAKE_MODE="grok-session"), "ok")
        call, = self.calls()
        self.assert_clean_call(call)
        self.assertEqual(call["prompt_file"], "Say ok.")
        self.assertEqual(call["argv"][call["argv"].index("--rules") + 1], "Answer with one word.")
        self.assertEqual(call["argv"][call["argv"].index("--max-turns") + 1], "1")
        self.assertIn("run_terminal_cmd", call["argv"][call["argv"].index("--disallowed-tools") + 1])
        self.assertEqual(call["argv"][call["argv"].index("--model") + 1], "grok-4.7-build-fast")
        session = os.path.join(self.home, ".grok", "sessions", quote(os.path.realpath(call["cwd"]), safe=""))
        self.assertFalse(os.path.exists(session))

    def test_grok_cleanup_follows_grok_home(self):
        grok_home = os.path.join(self.tmp, "grok-home")
        self.ask("grok", FAKE_MODE="grok-session", GROK_HOME=grok_home)
        call, = self.calls()
        self.assertFalse(os.path.exists(os.path.join(grok_home, "sessions", quote(os.path.realpath(call["cwd"]), safe=""))))

    def test_a_copilot_prompt_too_big_for_one_argument_is_refused_not_a_crash(self):
        self.assertLessEqual(self.model("copilot").traits.max_prompt_chars * 4, 128 * 1024 - 8 * 1024)
        with self.assertRaises(ai.Refused):
            self.model("copilot").ask(Prompt("Answer with one word.", "\u6587" * 50_000), 30)

    def test_grok_cleanup_leaves_other_sessions_alone(self):
        other = os.path.join(self.home, ".grok", "sessions", "%2Fhome%2Fsam%2Fcode")
        os.makedirs(other)
        self.ask("grok", FAKE_MODE="grok-session")
        self.assertTrue(os.path.isdir(other))

    def test_copilot_gets_a_throwaway_home_that_is_removed(self):
        self.assertEqual(self.ask("copilot"), "ok")
        call, = self.calls()
        self.assert_clean_call(call)
        self.assertTrue(call["copilot_home_exists"])
        self.assertTrue(call["env"]["COPILOT_HOME"].startswith(call["cwd"]))
        self.assertEqual(call["argv"][:2], ["-p", "Answer with one word.\n\nSay ok."])
        self.assertIn("--no-ask-user", call["argv"])

    def test_a_model_setting_reaches_the_cli(self):
        self.ask("chatgpt", 'model = "big-model"\n')
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[argv.index("--model") + 1], "big-model")

    def test_cursor_is_off_unless_allowed(self):
        with self.assertRaises(config.ConfigError):
            config.parse('[ai]\nplugin = "cursor"\n')
        self.assertNotIn("cursor", ai.visible_plugins())
        self.assertEqual(self.ask("cursor", "allow_unconfirmed = true\n"), "ok")
        self.assertEqual(self.calls()[0]["argv"][0], "-p")

    def test_a_signed_out_cli_is_not_ready_and_a_crash_is_busy(self):
        with self.assertRaises(NotReady):
            self.ask("claude-code", FAKE_MODE="signed-out")
        with self.assertRaises(Busy):
            self.ask("claude-code", FAKE_MODE="crash")

    def test_a_missing_cli_is_not_ready(self):
        model = ai.load(config.parse('[ai]\nplugin = "grok"\nbinary = "/nonexistent/grok"\n').ai)
        with self.assertRaises(NotReady):
            model.ask(Prompt("s", "u"), 5)

    def test_a_cli_past_its_time_is_stopped_with_everything_it_started(self):
        started = time.monotonic()
        with self.assertRaises(Busy):
            self.ask("claude-code", timeout=1.5, FAKE_MODE="hang")
        self.assertLess(time.monotonic() - started, 10)
        with open(self.child_path) as fh:
            child = int(fh.read())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("the CLI's child process is still running")

    def test_a_call_cut_off_when_kb_exits_still_cleans_up(self):
        script = (f"import os, sys, threading, time\nsys.path.insert(0, {os.getcwd()!r})\n"
                  "from kb import ai, config\n"
                  f"model = ai.load(config.parse('[ai]\\nplugin = \"grok\"\\nbinary = \"{self.binary}\"\\n').ai)\n"
                  "threading.Thread(target=lambda: model.ask(ai.Prompt('s', 'u'), 30), daemon=True).start()\n"
                  "time.sleep(1.5)\n")
        env = dict(os.environ, FAKE_MODE="hang")
        import subprocess
        subprocess.run([sys.executable, "-c", script], env=env, timeout=20)
        call, = self.calls()
        self.assertFalse(os.path.exists(call["cwd"]))
        with open(self.child_path) as fh:
            child = int(fh.read())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("the CLI's child process is still running")

    def test_cli_plugins_give_the_check_more_time_and_no_duplicate_calls(self):
        self.assertEqual((self.model("chatgpt").traits.check_budget, self.model("chatgpt").traits.hedge_after),
                         (20.0, None))
        self.assertEqual(self.model("grok").traits.check_budget, 25.0)


class CheckThroughCliTest(CliCase):
    def test_the_check_runs_through_claude_code_and_adds_nothing_for_an_unrelated_message(self):
        paths = Paths.under(os.path.join(self.tmp, "kb"))
        db = store.connect(paths.db)
        store.store_item(db, "notes", "m:pelican.md", "2026-10-01T00:00:00Z", "Pelican invoice",
                         "Dana approved the pelican invoice on Tuesday.", {"path": "/notes/pelican.md"})
        db.commit()
        db.close()
        cfg = config.parse(f'[ai]\nplugin = "claude-code"\nbinary = {json.dumps(self.binary)}\n')
        request = json.dumps({"message": "did dana approve the pelican invoice", "before": []})
        answers = {"query": '{"query": "pelican invoice Dana"}', "scores": '{"scores": {"1": 2}}'}
        with mock.patch.dict(os.environ, {"FAKE_ANSWER_BY": "system", "FAKE_ANSWERS": json.dumps(answers)}):
            shown = check.answer(paths, cfg, request, "s1")
        self.assertIn("Pelican invoice", shown)
        self.assertEqual(len(self.calls()), 2)
        answers["scores"] = '{"scores": {"1": 0}}'
        with mock.patch.dict(os.environ, {"FAKE_ANSWER_BY": "system", "FAKE_ANSWERS": json.dumps(answers)}):
            self.assertEqual(check.answer(paths, cfg, request.replace("did", "write a poem, not about"), "s2"), "")


class AiCommandTest(CliCase):
    def run_cli(self, *argv):
        kb_home = os.path.join(self.tmp, "kb")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"KB_HOME": kb_home}), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def write_config(self, text):
        os.makedirs(os.path.join(self.tmp, "kb"), exist_ok=True)
        with open(os.path.join(self.tmp, "kb", "config.toml"), "w") as fh:
            fh.write(text)

    def test_ai_test_says_which_plugin_answered(self):
        self.write_config(f'[ai]\nplugin = "grok"\nbinary = {json.dumps(self.binary)}\n')
        code, out = self.run_cli("ai", "test")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^grok \(grok-4\.7-build-fast\) answered in \d+\.\d s: 'ok'")

    def test_ai_test_without_a_plugin_fails_with_the_next_step(self):
        code, _ = self.run_cli("ai", "test")
        self.assertEqual(code, 1)

    def test_ai_list_marks_the_configured_plugin_and_hides_cursor(self):
        self.write_config(f'[ai]\nplugin = "chatgpt"\nbinary = {json.dumps(self.binary)}\n')
        code, out = self.run_cli("ai", "list")
        self.assertEqual(code, 0)
        self.assertIn("* chatgpt", out)
        self.assertNotIn("cursor", out)


if __name__ == "__main__":
    unittest.main()
