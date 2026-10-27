import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from kb import agents, check, cli
from kb.agents import Change, claude_code
from kb.ai.cli import NESTED_ENV

AGENT = claude_code.AGENT
COMMAND = "'/opt/kb tools/kb' hook claude-code"

SETTINGS = {
    "cleanupPeriodDays": 36500,
    "env": {"CLAUDE_CODE_NO_FLICKER": "1", "ENABLE_TOOL_SEARCH": "true"},
    "permissions": {"defaultMode": "auto"},
    "model": "opus[1m]",
    "hooks": {
        "PermissionRequest": [{"matcher": "", "hooks": [
            {"type": "command", "command": "\"${TMUX_AGENT_INDICATOR_DIR:-$HOME/.tmux/plugins/x}\"/scripts/hook.sh needs-input"}]}],
        "SessionStart": [{"hooks": [
            {"type": "command", "command": "bash '/home/user/.claude/hooks/agent-state.sh' session", "timeout": 10}]}],
        "UserPromptSubmit": [{"matcher": "", "hooks": [
            {"type": "command", "command": "\"${TMUX_AGENT_INDICATOR_DIR:-$HOME/.tmux/plugins/x}\"/scripts/hook.sh running"}]}],
    },
    "statusLine": {"type": "command", "command": "bash ~/.claude/statusline.sh"},
    "enabledPlugins": {"example@market": True},
    "outputStyle": "Explanatory — terse",
}


class Home(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = os.path.realpath(tmp.name)
        self.settings = os.path.join(self.home, ".claude", "settings.json")

    def put(self, data):
        os.makedirs(os.path.dirname(self.settings), exist_ok=True)
        with open(self.settings, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def text(self):
        with open(self.settings, encoding="utf-8") as fh:
            return fh.read()

    def backups(self):
        return sorted(f for f in os.listdir(os.path.dirname(self.settings)) if ".bak-kb-" in f)

    def our_group(self, command=COMMAND, timeout=agents.HOOK_TIMEOUT):
        return {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}


class InstallTest(Home):
    def test_installs_into_realistic_settings_and_keeps_everything_else(self):
        self.put(SETTINGS)
        before = self.text()
        self.assertEqual(AGENT.install(self.home, COMMAND), [Change(self.settings, "added")])
        expected = json.loads(json.dumps(SETTINGS))
        expected["hooks"]["UserPromptSubmit"].append(self.our_group())
        self.assertEqual(self.text(), json.dumps(expected, indent=2, ensure_ascii=False) + "\n")
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        with open(os.path.join(os.path.dirname(self.settings), backups[0]), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)

    def test_creates_the_settings_file_when_only_the_folder_exists(self):
        os.makedirs(os.path.dirname(self.settings))
        self.assertEqual(AGENT.install(self.home, COMMAND)[0].action, "added")
        self.assertEqual(json.loads(self.text()), {"hooks": {"UserPromptSubmit": [self.our_group()]}})
        self.assertEqual(self.backups(), [])

    def test_installing_twice_changes_nothing_and_makes_no_second_backup(self):
        self.put(SETTINGS)
        AGENT.install(self.home, COMMAND)
        after_first, backups = self.text(), self.backups()
        self.assertEqual(AGENT.install(self.home, COMMAND), [Change(self.settings, "unchanged")])
        self.assertEqual(self.text(), after_first)
        self.assertEqual(self.backups(), backups)

    def test_an_older_kb_entry_with_another_path_and_timeout_is_updated_in_place(self):
        data = json.loads(json.dumps(SETTINGS))
        data["hooks"]["UserPromptSubmit"].append(self.our_group("/old/kb hook claude-code", 14))
        data["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "echo done"}]}]
        self.put(data)
        self.assertEqual(AGENT.install(self.home, COMMAND), [Change(self.settings, "updated")])
        data["hooks"]["UserPromptSubmit"][1] = self.our_group()
        self.assertEqual(json.loads(self.text()), data)

    def test_a_malformed_settings_file_raises_and_is_left_alone(self):
        self.put("{ not json")
        with self.assertRaises(ValueError):
            AGENT.install(self.home, COMMAND)
        self.assertEqual(self.text(), "{ not json")
        self.assertEqual(self.backups(), [])


class UninstallTest(Home):
    def test_removes_only_kb_and_restores_the_original_file(self):
        self.put(SETTINGS)
        original = self.text()
        AGENT.install(self.home, COMMAND)
        self.assertEqual(AGENT.uninstall(self.home), [Change(self.settings, "removed")])
        self.assertEqual(self.text(), original)

    def test_leaves_other_hooks_sharing_kbs_group(self):
        other = {"type": "command", "command": "echo hi"}
        self.put({"hooks": {"UserPromptSubmit": [{"hooks": [other, self.our_group()["hooks"][0]]}]}})
        AGENT.uninstall(self.home)
        self.assertEqual(json.loads(self.text()), {"hooks": {"UserPromptSubmit": [{"hooks": [other]}]}})

    def test_leaves_the_codex_entry_that_looks_alike(self):
        self.put({"hooks": {"UserPromptSubmit": [self.our_group("/kb hook codex")]}})
        self.assertEqual(AGENT.uninstall(self.home)[0].action, "unchanged")
        self.assertTrue(agents.has_json_hook(self.settings, "UserPromptSubmit", "codex"))

    def test_uninstalling_twice_and_with_no_file_changes_nothing(self):
        self.assertEqual(AGENT.uninstall(self.home), [Change(self.settings, "unchanged")])
        self.assertFalse(os.path.exists(self.settings))
        self.put(SETTINGS)
        AGENT.install(self.home, COMMAND)
        AGENT.uninstall(self.home)
        backups = self.backups()
        self.assertEqual(AGENT.uninstall(self.home)[0].action, "unchanged")
        self.assertEqual(self.backups(), backups)


class DetectTest(Home):
    def test_detect_needs_the_claude_folder(self):
        self.assertFalse(AGENT.detect(self.home))
        os.makedirs(os.path.dirname(self.settings))
        self.assertTrue(AGENT.detect(self.home))

    def test_installed_follows_install_and_uninstall(self):
        os.makedirs(os.path.dirname(self.settings))
        self.assertFalse(AGENT.installed(self.home))
        self.put(SETTINGS)
        self.assertFalse(AGENT.installed(self.home))
        AGENT.install(self.home, COMMAND)
        self.assertTrue(AGENT.installed(self.home))
        AGENT.uninstall(self.home)
        self.assertFalse(AGENT.installed(self.home))

    def test_the_agent_is_found_by_name(self):
        self.assertIs(agents.load("claude-code"), AGENT)
        self.assertEqual(AGENT.label, "Claude Code")


class TranscriptCase(unittest.TestCase):
    def transcript(self, lines):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "session.jsonl")
        lines = [{"type": "summary", "summary": "x"}] + lines
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(line) for line in lines) + "\n")
        return path


class RequestTest(TranscriptCase):
    def before(self, transcript, prompt):
        message, before, session = AGENT.request({"session_id": "s", "prompt": prompt, "transcript_path": transcript})
        self.assertEqual((message, session), (prompt, "s"))
        return before

    def test_the_prompt_the_session_and_no_turns_without_a_transcript(self):
        event = {"session_id": "abc-123", "prompt": "what did she say", "hook_event_name": "UserPromptSubmit",
                 "cwd": "/tmp"}
        self.assertEqual(AGENT.request(event), ("what did she say", [], "abc-123"))

    def test_missing_fields_become_empty_text(self):
        self.assertEqual(AGENT.request({}), ("", [], ""))
        self.assertEqual(AGENT.request({"prompt": None, "session_id": None, "transcript_path": None}), ("", [], ""))

    def test_claude_code_transcript_gives_the_two_turns_before(self):
        path = self.transcript([
            {"type": "user", "message": {"content": "start the report"}},
            {"type": "user", "isMeta": True, "message": {"content": "<local-command-caveat>x"}},
            {"type": "user", "message": {"content": "draft the note to Dana"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Checking her last email."},
                                                          {"type": "tool_use", "name": "Bash", "input": {}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
            {"type": "user", "message": {"content": "<task-notification>done</task-notification>"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Which items should it cover?"}]}},
            {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "side"}]}},
            {"type": "user", "message": {"content": "all of them"}},
        ])
        self.assertEqual(self.before(path, "all of them"), [
            {"role": "user", "text": "start the report\ndraft the note to Dana"},
            {"role": "assistant", "text": "Checking her last email.\nWhich items should it cover?"}])

    def test_a_missing_transcript_sends_no_turns(self):
        self.assertEqual(self.before("/nonexistent/session.jsonl", "what did she say"), [])

    def test_a_directory_as_the_transcript_sends_no_turns(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.before(folder, "p"), [])


class ReplyTest(unittest.TestCase):
    def test_the_records_become_additional_context(self):
        self.assertEqual(json.loads(AGENT.reply("Knowledge base records ...\n1. one")), {"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit", "additionalContext": "Knowledge base records ...\n1. one"}})

    def test_the_reply_is_one_line(self):
        self.assertNotIn("\n", AGENT.reply("a\nb"))


class HookCommandTest(TranscriptCase):
    def setUp(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(NESTED_ENV, None)
        os.environ.pop(agents.GROK_ENV, None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["KB_HOME"] = os.path.join(tmp.name, "kb")
        patcher = mock.patch.object(check, "answer", return_value="RECORDS")
        self.answer = patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, event, *argv):
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(event if isinstance(event, str) else json.dumps(event))), \
                contextlib.redirect_stdout(out):
            code = cli.main(["hook", *(argv or ("claude-code",))])
        return code, out.getvalue()

    def test_prints_the_records_as_json_for_claude_code(self):
        path = self.transcript([{"type": "user", "message": {"content": "draft the note"}},
                                {"type": "assistant", "message": {"content": "Which items?"}},
                                {"type": "user", "message": {"content": "all of them"}}])
        code, out = self.call({"session_id": "abc", "prompt": "all of them", "transcript_path": path})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit", "additionalContext": "RECORDS"}})
        text, session = self.answer.call_args[0][2:]
        self.assertEqual(session, "abc")
        self.assertEqual(json.loads(text), {"message": "all of them", "before": [
            {"role": "user", "text": "draft the note"}, {"role": "assistant", "text": "Which items?"}]})

    def test_prints_nothing_when_the_check_finds_nothing(self):
        self.answer.return_value = ""
        self.assertEqual(self.call({"session_id": "s", "prompt": "yes"}), (0, ""))
        self.answer.assert_called_once()

    def test_prints_nothing_and_skips_the_check_inside_a_kb_model_call(self):
        os.environ[NESTED_ENV] = "1"
        self.assertEqual(self.call({"session_id": "s", "prompt": "p"}), (0, ""))
        self.answer.assert_not_called()

    def test_prints_nothing_and_skips_the_check_inside_a_grok_hook(self):
        os.environ[agents.GROK_ENV] = "user_prompt_submit"
        self.assertEqual(self.call({"session_id": "s", "prompt": "p"}), (0, ""))
        self.answer.assert_not_called()

    def test_bad_input_prints_nothing_and_exits_0(self):
        for stdin in ("not json", "", "[]"):
            with self.subTest(stdin):
                self.assertEqual(self.call(stdin), (0, ""))
        self.answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class BrokenConfigTest(unittest.TestCase):
    def test_a_broken_config_makes_the_hook_print_nothing_and_exit_0(self):
        import contextlib
        import io
        from kb import cli
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "config.toml"), "w") as fh:
                fh.write("[sources\n")
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"KB_HOME": tmp}), mock.patch("sys.stdin", io.StringIO('{"prompt": "hi"}')), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["hook", "claude-code"])
        self.assertEqual((code, out.getvalue(), err.getvalue()), (0, "", ""))


class UnreadableConfigTest(unittest.TestCase):
    def test_an_unreadable_config_makes_the_hook_print_nothing_and_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"KB_HOME": tmp}), \
                    mock.patch("kb.config.open", side_effect=PermissionError(13, "Permission denied"), create=True), \
                    mock.patch("sys.stdin", io.StringIO('{"prompt": "hi"}')), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["hook", "claude-code"])
        self.assertEqual((code, out.getvalue(), err.getvalue()), (0, "", ""))


class InvalidUtf8ConfigTest(unittest.TestCase):
    def test_a_config_that_is_not_utf8_makes_the_hook_print_nothing_and_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "config.toml"), "wb") as fh:
                fh.write(b'machine = "\xff"\n')
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"KB_HOME": tmp}), mock.patch("sys.stdin", io.StringIO('{"prompt": "hi"}')), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["hook", "claude-code"])
        self.assertEqual((code, out.getvalue(), err.getvalue()), (0, "", ""))
