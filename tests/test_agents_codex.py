import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

from kb import agents
from kb.agents import Change, codex

try:
    import tomllib
except ImportError:
    tomllib = None

AGENT = codex.AGENT
COMMAND = "'/opt/kb tools/kb' hook codex"
TIMEOUT = agents.HOOK_TIMEOUT

HOOKS = {"hooks": {"SessionStart": [{"hooks": [
    {"command": "bash '/home/user/.codex/agent-state.sh' session", "timeout": 10, "type": "command"}]}]}}
CONFIG = """model = "gpt-6.1"
model_reasoning_effort = "high"

[projects."/home/user/code"]
trust_level = "trusted"

[features]
hooks = true

[hooks.state]

[hooks.state."/home/user/.codex/hooks.json:session_start:0:0"]
trusted_hash = "sha256:a109443cbfe2f9e51b7bf11a6cf4bbcb4ccbf5895f2e2276a4b6d0dfe19965e3"

[hooks.state."example@market:hooks/hooks.json:session_start:0:0"]
trusted_hash = "sha256:62acbaff2b2f55f41971e21a164990aaa53c96919baa0a5eae2eb5db73b9e6e6"
"""


class HashTest(unittest.TestCase):
    """The two fixed values are trusted_hash entries Codex's own /hooks review wrote."""

    @staticmethod
    def sha(identity):
        return "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def test_command_hook_with_timeout_hashes_its_compact_sorted_identity(self):
        handler = {"command": "bash agent-state.sh session", "timeout": 10, "type": "command"}
        identity = ('{"event_name":"session_start","hooks":[{"async":false,"command":"bash agent-state.sh session",'
                    '"timeout":10,"type":"command"}]}')
        self.assertEqual(codex.codex_hook_hash("session_start", handler), self.sha(identity))

    def test_matcher_default_timeout_and_windows_command(self):
        handler = {"type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd\" session-start",
                   "commandWindows": "cmd.exe /c run-hook.cmd session-start", "async": False}
        self.assertEqual(codex.codex_hook_hash("session_start", handler, matcher="startup|clear|compact"),
                         "sha256:62acbaff2b2f55f41971e21a164990aaa53c96919baa0a5eae2eb5db73b9e6e6")

    def test_user_prompt_submit_with_status_message(self):
        handler = {"command": "signal running", "statusMessage": "Marking active", "timeout": 5, "type": "command"}
        identity = ('{"event_name":"user_prompt_submit","hooks":[{"async":false,"command":"signal running",'
                    '"statusMessage":"Marking active","timeout":5,"type":"command"}]}')
        self.assertEqual(codex.codex_hook_hash("user_prompt_submit", handler), self.sha(identity))

    def test_a_missing_timeout_is_codexs_default_and_a_matcher_is_part_of_the_identity(self):
        identity = '{"event_name":"x","hooks":[{"async":true,"command":"c","timeout":600,"type":"command"}],"matcher":"m"}'
        self.assertEqual(codex.codex_hook_hash("x", {"command": "c", "async": True}, matcher="m"), self.sha(identity))

    def test_empty_matcher_is_part_of_the_identity(self):
        self.assertEqual(codex.codex_hook_hash("session_start", {"command": "gh-axi", "timeout": 10}, matcher=""),
                         "sha256:42ad5843ba79bdd68be42269ac7b513889a767e052dda6c6c28079a53af982ee")
        self.assertNotEqual(codex.codex_hook_hash("session_start", {"command": "gh-axi", "timeout": 10}),
                            codex.codex_hook_hash("session_start", {"command": "gh-axi", "timeout": 10}, matcher=""))

    def test_a_different_command_or_timeout_is_a_different_hash(self):
        base = codex.codex_hook_hash("user_prompt_submit", {"command": "a", "timeout": 30})
        self.assertNotEqual(base, codex.codex_hook_hash("user_prompt_submit", {"command": "b", "timeout": 30}))
        self.assertNotEqual(base, codex.codex_hook_hash("user_prompt_submit", {"command": "a", "timeout": 29}))


class Home(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = os.path.realpath(tmp.name)
        self.hooks_json = os.path.join(self.home, ".codex", "hooks.json")
        self.config = os.path.join(self.home, ".codex", "config.toml")
        self.key = f"{self.hooks_json}:user_prompt_submit:0:0"

    def put(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data, indent=2))

    def text(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def backups(self):
        return sorted(os.path.join(d, f) for d, _, files in os.walk(self.home) for f in files if ".bak-kb-" in f)

    def snapshot(self):
        return {p: self.text(p) for p in (self.hooks_json, self.config) if os.path.exists(p)}

    def handler(self, command=COMMAND, timeout=TIMEOUT):
        return {"type": "command", "command": command, "timeout": timeout}

    def digest(self, command=COMMAND, timeout=TIMEOUT):
        return codex.codex_hook_hash("user_prompt_submit", self.handler(command, timeout))

    def realistic_home(self):
        self.put(self.hooks_json, HOOKS)
        self.put(self.config, CONFIG)

    def trust_table(self, key=None, digest=None):
        return f'[hooks.state."{key or self.key}"]\ntrusted_hash = "{digest or self.digest()}"\n'


class InstallTest(Home):
    def test_installs_into_realistic_files_and_keeps_everything_else(self):
        self.realistic_home()
        before = self.snapshot()
        changes = AGENT.install(self.home, COMMAND)
        self.assertEqual([c[:2] for c in changes], [(self.hooks_json, "added"), (self.config, "added")])
        expected = json.loads(json.dumps(HOOKS))
        expected["hooks"]["UserPromptSubmit"] = [{"hooks": [self.handler()]}]
        self.assertEqual(self.text(self.hooks_json), json.dumps(expected, indent=2))
        self.assertEqual(self.text(self.config), CONFIG + "\n" + self.trust_table())
        self.assertEqual(sorted(self.text(b) for b in self.backups()), sorted(before.values()))

    def test_the_trust_entry_parses_and_holds_the_hooks_hash(self):
        if tomllib is None:
            self.skipTest("tomllib needs Python 3.11")
        self.realistic_home()
        AGENT.install(self.home, COMMAND)
        state = tomllib.loads(self.text(self.config))["hooks"]["state"]
        self.assertEqual(len(state), 3)
        self.assertEqual(state[self.key], {"trusted_hash": self.digest()})

    def test_creates_missing_files(self):
        os.makedirs(os.path.dirname(self.hooks_json))
        changes = AGENT.install(self.home, COMMAND)
        self.assertEqual([c.action for c in changes], ["added", "added"])
        self.assertEqual(json.loads(self.text(self.hooks_json)), {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler()]}]}})
        self.assertEqual(self.text(self.config), self.trust_table())
        self.assertEqual(self.backups(), [])

    def test_a_config_without_a_final_newline_gets_one_before_the_entry(self):
        self.put(self.config, 'model = "gpt-6.1"')
        AGENT.install(self.home, COMMAND)
        self.assertEqual(self.text(self.config), 'model = "gpt-6.1"\n\n' + self.trust_table())

    def test_installing_twice_changes_nothing_and_makes_no_second_backup(self):
        self.realistic_home()
        AGENT.install(self.home, COMMAND)
        after_first, backups = self.snapshot(), self.backups()
        changes = AGENT.install(self.home, COMMAND)
        self.assertEqual(changes, [Change(self.hooks_json, "unchanged"), Change(self.config, "unchanged", f"trusts {self.key}")])
        self.assertEqual(self.snapshot(), after_first)
        self.assertEqual(self.backups(), backups)

    def test_a_stale_trust_at_the_same_position_is_replaced(self):
        self.realistic_home()
        stale = CONFIG + f'\n[hooks.state."{self.key}"]\ntrusted_hash = "sha256:{"0" * 64}"\nenabled = true\n'
        self.put(self.config, stale)
        changes = AGENT.install(self.home, COMMAND)
        self.assertEqual([c.action for c in changes], ["added", "updated"])
        self.assertEqual(self.text(self.config), stale.replace("0" * 64, self.digest()[len("sha256:"):]))

    def test_a_trust_table_without_a_hash_gets_one(self):
        self.put(self.config, f'[hooks.state."{self.key}"]\nenabled = true\n')
        AGENT.install(self.home, COMMAND)
        self.assertEqual(self.text(self.config),
                         f'[hooks.state."{self.key}"]\ntrusted_hash = "{self.digest()}"\nenabled = true\n')

    def test_an_older_kb_entry_is_updated_in_place_and_retrusted_at_its_position(self):
        self.realistic_home()
        hooks = json.loads(json.dumps(HOOKS))
        hooks["hooks"]["UserPromptSubmit"] = [
            {"hooks": [{"command": "tmux-signal running", "timeout": 5, "type": "command"}]},
            {"hooks": [{"command": "echo hi", "type": "command"},
                       {"command": "/old/kb hook codex", "timeout": 14, "type": "command"}]}]
        self.put(self.hooks_json, hooks)
        changes = AGENT.install(self.home, COMMAND)
        self.assertEqual([c.action for c in changes], ["updated", "added"])
        hooks["hooks"]["UserPromptSubmit"][1]["hooks"][1] = self.handler()
        self.assertEqual(json.loads(self.text(self.hooks_json)), hooks)
        key = f"{self.hooks_json}:user_prompt_submit:1:1"
        self.assertIn(self.trust_table(key), self.text(self.config))

    def test_installs_beside_the_users_other_prompt_hooks(self):
        other = {"hooks": [{"command": "tmux-signal running", "timeout": 5, "type": "command"}]}
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [other]}})
        AGENT.install(self.home, COMMAND)
        self.assertEqual(json.loads(self.text(self.hooks_json))["hooks"]["UserPromptSubmit"],
                         [other, {"hooks": [self.handler()]}])
        self.assertIn(self.trust_table(f"{self.hooks_json}:user_prompt_submit:1:0"), self.text(self.config))

    def test_the_trust_key_uses_the_real_path_of_the_hooks_file(self):
        real = os.path.join(self.home, "dotfiles", "hooks.json")
        self.put(real, {})
        os.makedirs(os.path.dirname(self.hooks_json))
        os.symlink(real, self.hooks_json)
        AGENT.install(self.home, COMMAND)
        self.assertTrue(os.path.islink(self.hooks_json))
        self.assertIn(f'[hooks.state."{real}:user_prompt_submit:0:0"]', self.text(self.config))

    def test_a_hook_with_fields_kb_cannot_hash_is_left_for_the_user_to_trust(self):
        data = {"hooks": {"UserPromptSubmit": [{"hooks": [
            {"type": "command", "command": "/old/kb hook codex", "timeout": 14, "futureField": True}]}]}}
        self.put(self.hooks_json, data)
        changes = AGENT.install(self.home, COMMAND)
        self.assertEqual([c.action for c in changes], ["updated", "skipped"])
        self.assertIn("/hooks in Codex", changes[1].note)
        self.assertIn(self.key, changes[1].note)
        self.assertFalse(os.path.exists(self.config))

    def test_a_malformed_hooks_file_is_skipped_and_left_alone(self):
        self.put(self.hooks_json, "{ not json")
        self.put(self.config, CONFIG)
        changes = AGENT.install(self.home, COMMAND)
        self.assertEqual([c.action for c in changes], ["skipped"])
        self.assertEqual(self.text(self.hooks_json), "{ not json")
        self.assertEqual(self.text(self.config), CONFIG)
        self.assertEqual(self.backups(), [])

    @unittest.skipIf(tomllib is None, "tomllib needs Python 3.11")
    def test_a_config_that_would_not_parse_back_is_skipped_and_left_alone(self):
        self.put(self.config, "model = = broken\n")
        changes = AGENT.install(self.home, COMMAND)
        self.assertEqual([c.action for c in changes], ["added", "skipped"])
        self.assertIn("/hooks in Codex", changes[1].note)
        self.assertEqual(self.text(self.config), "model = = broken\n")

    def test_trust_refuses_a_result_that_does_not_parse_back(self):
        if tomllib is None:
            self.skipTest("tomllib needs Python 3.11")
        self.put(self.config, "[hooks.state\n")
        with self.assertRaises(ValueError):
            codex.trust(self.config, self.key, self.digest())
        self.assertEqual(self.text(self.config), "[hooks.state\n")


class UninstallTest(Home):
    def test_removes_kbs_hook_and_trust_and_restores_the_original_files(self):
        self.realistic_home()
        before = self.snapshot()
        AGENT.install(self.home, COMMAND)
        changes = AGENT.uninstall(self.home)
        self.assertEqual([c[:2] for c in changes], [(self.hooks_json, "removed"), (self.config, "removed")])
        self.assertEqual(json.loads(self.text(self.hooks_json)), json.loads(before[self.hooks_json]))
        self.assertEqual(self.text(self.config), before[self.config])

    def test_leaves_other_hooks_and_their_trust(self):
        other = {"command": "echo hi", "type": "command"}
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [other, self.handler()]}]}})
        other_key = f"{self.hooks_json}:user_prompt_submit:0:0"
        self.put(self.config, self.trust_table(other_key, "sha256:" + "a" * 64)
                 + "\n" + self.trust_table(f"{self.hooks_json}:user_prompt_submit:0:1"))
        AGENT.uninstall(self.home)
        self.assertEqual(json.loads(self.text(self.hooks_json)), {"hooks": {"UserPromptSubmit": [{"hooks": [other]}]}})
        self.assertEqual(self.text(self.config), self.trust_table(other_key, "sha256:" + "a" * 64))

    def test_removes_the_trust_table_whichever_hash_it_holds(self):
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler()]}]}})
        self.put(self.config, 'model = "x"\n\n' + self.trust_table(digest="sha256:" + "0" * 64) + "\n[features]\nhooks = true\n")
        AGENT.uninstall(self.home)
        self.assertEqual(self.text(self.config), 'model = "x"\n\n[features]\nhooks = true\n')

    def test_a_hook_without_a_trust_entry_still_goes(self):
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler()]}]}})
        self.put(self.config, CONFIG)
        changes = AGENT.uninstall(self.home)
        self.assertEqual([c.action for c in changes], ["removed", "unchanged"])
        self.assertEqual(self.text(self.config), CONFIG)

    def test_the_claude_code_entry_in_the_same_file_is_not_touched(self):
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler("/kb hook claude-code")]}]}})
        self.assertEqual(AGENT.uninstall(self.home), [Change(self.hooks_json, "unchanged")])
        self.assertTrue(agents.has_json_hook(self.hooks_json, "UserPromptSubmit", "claude-code"))

    def test_uninstalling_twice_and_with_no_files_changes_nothing(self):
        self.assertEqual(AGENT.uninstall(self.home), [Change(self.hooks_json, "unchanged")])
        self.assertFalse(os.path.exists(self.hooks_json) or os.path.exists(self.config))
        self.realistic_home()
        AGENT.install(self.home, COMMAND)
        AGENT.uninstall(self.home)
        after, backups = self.snapshot(), self.backups()
        self.assertEqual(AGENT.uninstall(self.home), [Change(self.hooks_json, "unchanged")])
        self.assertEqual(self.snapshot(), after)
        self.assertEqual(self.backups(), backups)

    def test_a_malformed_hooks_file_is_skipped_and_left_alone(self):
        self.put(self.hooks_json, "{ not json")
        self.assertEqual([c.action for c in AGENT.uninstall(self.home)], ["skipped"])
        self.assertEqual(self.text(self.hooks_json), "{ not json")


class DetectTest(Home):
    def test_detect_needs_the_codex_folder(self):
        self.assertFalse(AGENT.detect(self.home))
        os.makedirs(os.path.dirname(self.hooks_json))
        self.assertTrue(AGENT.detect(self.home))

    def test_installed_needs_the_hook_and_its_trust(self):
        os.makedirs(os.path.dirname(self.hooks_json))
        self.assertFalse(AGENT.installed(self.home))
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler()]}]}})
        self.assertFalse(AGENT.installed(self.home), "an untrusted hook does not run")
        AGENT.install(self.home, COMMAND)
        self.assertTrue(AGENT.installed(self.home))
        AGENT.uninstall(self.home)
        self.assertFalse(AGENT.installed(self.home))

    def test_a_changed_hook_is_no_longer_trusted(self):
        AGENT.install(self.home, COMMAND)
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler("/elsewhere/kb hook codex")]}]}})
        self.assertFalse(AGENT.installed(self.home))

    def test_a_malformed_hooks_file_is_not_installed(self):
        self.put(self.hooks_json, "{ not json")
        self.assertFalse(AGENT.installed(self.home))

    def test_the_agent_is_found_by_name(self):
        self.assertIs(agents.load("codex"), AGENT)
        self.assertEqual(AGENT.label, "Codex")


class ShiftedTrustTest(Home):
    def test_a_user_hook_that_moves_up_on_uninstall_keeps_its_trust(self):
        user = {"type": "command", "command": "/usr/local/bin/notify-prompt", "timeout": 5}
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler()]}, {"hooks": [user]}]}})
        user_digest = codex.codex_hook_hash("user_prompt_submit", user)
        moved_from = f"{self.hooks_json}:user_prompt_submit:1:0"
        self.put(self.config, self.trust_table() + "\n" + self.trust_table(moved_from, user_digest))
        AGENT.uninstall(self.home)
        config = self.text(self.config)
        self.assertIn(self.trust_table(self.key, user_digest), config)
        self.assertNotIn(moved_from, config)
        self.assertNotIn(self.digest(), config)

    def test_an_untrusted_user_hook_that_moves_up_gains_no_trust(self):
        user = {"type": "command", "command": "/usr/local/bin/notify-prompt", "timeout": 5}
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler()]}, {"hooks": [user]}]}})
        self.put(self.config, self.trust_table())
        AGENT.uninstall(self.home)
        self.assertNotIn("trusted_hash", self.text(self.config))


class RequestTest(unittest.TestCase):
    def transcript(self, lines):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "rollout.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(line) for line in lines) + "\n")
        return path

    def before(self, transcript, prompt):
        message, before, session = AGENT.request({"session_id": "s", "prompt": prompt, "transcript_path": transcript,
                                                  "hook_event_name": "UserPromptSubmit", "cwd": "/tmp"})
        self.assertEqual((message, session), (prompt, "s"))
        return before

    def test_codex_transcript_gives_the_two_turns_before(self):
        item = lambda role, text: {"type": "response_item", "payload": {
            "type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}}
        path = self.transcript([
            {"type": "session_meta", "payload": {"id": "s"}},
            item("developer", "<permissions>"), item("user", "# AGENTS.md instructions for /x"),
            item("user", "is the folder verified?"), {"type": "event_msg", "payload": {"type": "agent_message"}},
            item("assistant", "Not yet. " + "x" * 2000 + " The thread stopped early."),
            item("user", "look at context from that thread"),
        ])
        before = self.before(path, "look at context from that thread")
        self.assertEqual(before[0], {"role": "user", "text": "is the folder verified?"})
        self.assertEqual(before[1]["role"], "assistant")
        self.assertEqual(len(before[1]["text"]), 1500)
        self.assertTrue(before[1]["text"].endswith("The thread stopped early."))

    def test_codex_output_text_parts_count_as_the_assistants(self):
        path = self.transcript([
            {"type": "session_meta", "payload": {}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "q"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                  "content": [{"type": "output_text", "text": "a"}]}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "shell"}},
        ])
        self.assertEqual(self.before(path, "next"), [{"role": "user", "text": "q"}, {"role": "assistant", "text": "a"}])

    def test_the_prompt_the_session_and_no_turns_without_a_transcript(self):
        self.assertEqual(AGENT.request({"session_id": "019e", "prompt": "what did she say"}),
                         ("what did she say", [], "019e"))
        self.assertEqual(AGENT.request({"prompt": "p", "transcript_path": None}), ("p", [], ""))

    def test_a_missing_transcript_sends_no_turns(self):
        self.assertEqual(self.before("/nonexistent/rollout.jsonl", "what did she say"), [])

    def test_the_reply_is_claude_codes_additional_context(self):
        self.assertEqual(json.loads(AGENT.reply("RECORDS")), {"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit", "additionalContext": "RECORDS"}})

    def test_run_answers_with_the_check_and_stays_out_when_nested(self):
        from kb import check
        from kb.ai.cli import NESTED_ENV
        event = json.dumps({"session_id": "s", "prompt": "who said that"})
        with mock.patch.dict(os.environ), mock.patch.object(check, "answer", return_value="RECORDS") as answer:
            os.environ.pop(NESTED_ENV, None)
            os.environ.pop(agents.GROK_ENV, None)
            self.assertEqual(json.loads(agents.run("p", "c", "codex", event))["hookSpecificOutput"]["additionalContext"],
                             "RECORDS")
            os.environ[NESTED_ENV] = "1"
            self.assertEqual(agents.run("p", "c", "codex", event), "")
            answer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
