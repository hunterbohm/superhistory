import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

from kb import agents
from kb.agents import Change, codex

AGENT = codex.AGENT
COMMAND = "'/opt/kb tools/kb' hook codex"
TIMEOUT = agents.BACKGROUND_HOOK_TIMEOUT

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
        return {"type": "command", "command": command, "timeout": timeout, "async": True}

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
        self.realistic_home()
        AGENT.install(self.home, COMMAND)
        state = codex.read_toml(self.text(self.config))["hooks"]["state"]
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

    def test_a_quoted_hash_key_is_updated_in_place(self):
        for quoted in ('"trusted_hash"', "'trusted_hash'", '"trusted\\u005fhash"'):
            AGENT.install(self.home, "/old/kb hook codex")
            self.put(self.config, self.text(self.config).replace("trusted_hash =", f"{quoted} ="))
            AGENT.install(self.home, COMMAND)
            self.assertEqual(codex.read_toml(self.text(self.config))["hooks"]["state"][self.key],
                             {"trusted_hash": self.digest()}, quoted)
            self.assertTrue(AGENT.installed(self.home), quoted)
            os.remove(self.config)
            os.remove(self.hooks_json)

    def test_a_multi_line_array_in_the_table_does_not_end_it(self):
        AGENT.install(self.home, "/old/kb hook codex")
        self.put(self.config, self.text(self.config).replace(
            "trusted_hash =", 'notes = [\n  ["approved", "in /hooks"],\n]\ntrusted_hash ='))
        AGENT.install(self.home, COMMAND)
        state = codex.read_toml(self.text(self.config))["hooks"]["state"][self.key]
        self.assertEqual(state, {"notes": [["approved", "in /hooks"]], "trusted_hash": self.digest()})
        AGENT.uninstall(self.home)
        self.assertEqual(codex.read_toml(self.text(self.config)), {})

    def test_a_multi_line_string_in_the_table_does_not_end_it(self):
        AGENT.install(self.home, "/old/kb hook codex")
        self.put(self.config, self.text(self.config).replace(
            "trusted_hash =", 'note = """\n[not a table]\n"""\ntrusted_hash ='))
        AGENT.install(self.home, COMMAND)
        state = codex.read_toml(self.text(self.config))["hooks"]["state"][self.key]
        self.assertEqual(state, {"note": "[not a table]\n", "trusted_hash": self.digest()})
        AGENT.uninstall(self.home)
        self.assertEqual(codex.read_toml(self.text(self.config)), {})

    def test_a_hash_line_inside_a_multi_line_string_is_not_the_hash(self):
        AGENT.install(self.home, "/old/kb hook codex")
        self.put(self.config, self.text(self.config).replace(
            "trusted_hash =", 'note = """\ntrusted_hash = "documentation"\n"""\ntrusted_hash ='))
        AGENT.install(self.home, COMMAND)
        state = codex.read_toml(self.text(self.config))["hooks"]["state"][self.key]
        self.assertEqual(state, {"note": 'trusted_hash = "documentation"\n', "trusted_hash": self.digest()})
        self.assertTrue(AGENT.installed(self.home))

    def test_a_header_line_inside_a_multi_line_string_is_not_the_table(self):
        AGENT.install(self.home, COMMAND)
        decoy = f'model_instructions = """\n[hooks.state."{self.key}"]\ntrusted_hash = "old"\n"""\n\n'
        self.put(self.config, decoy + self.text(self.config))
        AGENT.uninstall(self.home)
        self.assertEqual(self.text(self.config), decoy.rstrip("\n") + "\n", "the string is kept, the table is gone")

    def test_uninstall_never_writes_a_config_that_does_not_parse(self):
        AGENT.install(self.home, COMMAND)
        broken = self.text(self.config) + "oops = = 1\n"
        self.put(self.config, broken)
        hooks = self.text(self.hooks_json)
        changes = AGENT.uninstall(self.home)
        self.assertEqual([c.action for c in changes], ["skipped", "skipped"])
        self.assertEqual((self.text(self.hooks_json), self.text(self.config)), (hooks, broken))

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

    def test_a_config_that_would_not_parse_back_is_skipped_and_left_alone(self):
        self.put(self.config, "model = = broken\n")
        changes = AGENT.install(self.home, COMMAND)
        self.assertEqual([c.action for c in changes], ["skipped", "skipped"])
        self.assertIn("by hand", changes[0].note)
        self.assertIn("/hooks in Codex", changes[0].note)
        self.assertEqual(self.text(self.config), "model = = broken\n")
        self.assertFalse(os.path.exists(self.hooks_json), "nothing is written, not even the hook")

    def test_the_guard_refuses_an_edit_that_does_not_parse(self):
        hooks = json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [self.handler()]}]}})
        config = self.trust_table()
        self.assertIsNone(codex._guard(self.hooks_json, (hooks, config), (hooks, config),
                                       [(0, 0, self.handler())], {self.key: self.digest()}))
        self.assertIn("would not parse", codex._guard(self.hooks_json, (hooks, config), (hooks, config + "[x\n"),
                                                      [(0, 0, self.handler())], {self.key: self.digest()}))


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

    def test_trust_written_with_single_quotes_and_a_comment_still_moves(self):
        user = {"type": "command", "command": "/usr/local/bin/notify-prompt", "timeout": 5}
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler()]}, {"hooks": [user]}]}})
        user_digest = codex.codex_hook_hash("user_prompt_submit", user)
        moved_from = f"{self.hooks_json}:user_prompt_submit:1:0"
        self.put(self.config, self.trust_table() + f'\n[hooks.state."{moved_from}"]\n'
                                                   f"trusted_hash = '{user_digest}'  # approved in /hooks\n")
        AGENT.uninstall(self.home)
        config = self.text(self.config)
        self.assertEqual(codex.read_toml(config)["hooks"]["state"], {self.key: {"trusted_hash": user_digest}})
        self.assertIn("# approved in /hooks", config, "the table moves whole, comment and all")

    def test_uninstall_keeps_a_comment_about_the_next_section(self):
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler()]}]}})
        tail = "# Do not change Acme settings; my other tools depend on them.\n[model_providers.acme]\nname = \"Acme\"\n"
        self.put(self.config, f'[hooks.state."{self.key}"]\ntrusted_hash = "old"\n\n' + tail)
        self.assertNotIn("skipped", [c.action for c in AGENT.uninstall(self.home)])
        self.assertEqual(self.text(self.config), tail)

    def test_the_guard_refuses_an_edit_that_drops_a_comment(self):
        def also_drops_comments(text, key):
            new, gone = untrusted(text, key)
            return "".join(line for line in new.splitlines(True) if not line.startswith("#")), gone

        untrusted = codex._untrusted
        AGENT.install(self.home, COMMAND)
        self.put(self.config, "# keep me\nmodel = \"x\"\n\n" + self.text(self.config))
        before = (self.text(self.hooks_json), self.text(self.config))
        with mock.patch.object(codex, "_untrusted", also_drops_comments):
            changes = AGENT.uninstall(self.home)
        self.assertEqual([c.action for c in changes], ["skipped", "skipped"])
        self.assertIn("drop a comment", changes[0].note)
        self.assertEqual((self.text(self.hooks_json), self.text(self.config)), before)

    def test_a_multi_line_hash_is_refused_and_nothing_is_written(self):
        self.put(self.config, f'[hooks.state."{self.key}"]\ntrusted_hash = """\nold\n"""\n')
        before = self.text(self.config)
        changes = AGENT.install(self.home, COMMAND)
        self.assertEqual([c.action for c in changes], ["skipped", "skipped"])
        self.assertEqual(self.text(self.config), before)
        self.assertFalse(os.path.exists(self.hooks_json))

    def test_a_moved_trust_table_keeps_the_comment_on_its_header(self):
        user = {"type": "command", "command": "/usr/local/bin/notify-prompt", "timeout": 5}
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler()]}, {"hooks": [user]}]}})
        user_digest = codex.codex_hook_hash("user_prompt_submit", user)
        moved_from = f"{self.hooks_json}:user_prompt_submit:1:0"
        self.put(self.config, self.trust_table() + f'\n[hooks.state."{moved_from}"]  # approved by user, keep this\n'
                                                   f'trusted_hash = "{user_digest}"\n')
        AGENT.uninstall(self.home)
        config = self.text(self.config)
        self.assertIn(f'[hooks.state."{self.key}"]  # approved by user, keep this\n', config)
        self.assertEqual(codex.read_toml(config)["hooks"]["state"], {self.key: {"trusted_hash": user_digest}})

    def test_trust_headers_with_a_trailing_comment_are_recognized(self):
        user = {"type": "command", "command": "/usr/local/bin/notify-prompt", "timeout": 5}
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler()]}, {"hooks": [user]}]}})
        user_digest = codex.codex_hook_hash("user_prompt_submit", user)
        moved_from = f"{self.hooks_json}:user_prompt_submit:1:0"
        commented = self.trust_table().replace("]\n", "]  # approved\n", 1)
        self.put(self.config, commented + "\n" + self.trust_table(moved_from, user_digest))
        AGENT.uninstall(self.home)
        config = self.text(self.config)
        self.assertEqual(codex.read_toml(config)["hooks"]["state"][self.key]["trusted_hash"], user_digest)
        self.assertEqual(config.count(self.key + '"'), 1)
        self.assertNotIn(moved_from, config)

    def test_trust_headers_spelled_with_literal_letters_or_spaces_still_move(self):
        home = os.path.join(self.home, "José W")
        hooks_json = os.path.join(home, ".codex", "hooks.json")
        config = os.path.join(home, ".codex", "config.toml")
        user = {"type": "command", "command": "/usr/local/bin/notify-prompt", "timeout": 5}
        self.put(hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler()]}, {"hooks": [user]}]}})
        user_digest = codex.codex_hook_hash("user_prompt_submit", user)
        kb_key, user_key = (f"{hooks_json}:user_prompt_submit:{g}:0" for g in (0, 1))
        self.put(config, f'[hooks.state."{kb_key}"]\ntrusted_hash = "{self.digest()}"\n\n'
                         f'[ hooks . state . "{user_key}" ]  # approved\ntrusted_hash = "{user_digest}"\n')
        changes = AGENT.uninstall(home)
        self.assertEqual([c.action for c in changes], ["removed", "removed"])
        state = codex.read_toml(self.text(config))["hooks"]["state"]
        self.assertEqual(state, {kb_key: {"trusted_hash": user_digest}}, "the user's hook kept its trust at 0:0")

    def test_a_trust_that_cannot_move_is_reported_and_names_the_way_back(self):
        user = {"type": "command", "command": "/usr/local/bin/notify-prompt", "timeout": 5}
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler()]}, {"hooks": [user]}]}})
        moved_from = f"{self.hooks_json}:user_prompt_submit:1:0"
        self.put(self.config, self.trust_table() + "\n" + self.trust_table(
            moved_from, codex.codex_hook_hash("user_prompt_submit", user)))
        before = (self.text(self.hooks_json), self.text(self.config))
        with mock.patch.object(codex, "_moved", lambda text, old, new: (text, False)):
            changes = AGENT.uninstall(self.home)
        self.assertEqual([c.action for c in changes], ["skipped", "skipped"])
        self.assertIn("more than kb's own entries", changes[0].note)
        self.assertIn("by hand", changes[0].note)
        self.assertEqual((self.text(self.hooks_json), self.text(self.config)), before, "nothing is written")

    def test_a_home_with_an_emoji_or_a_delete_character_is_trusted_and_untrusted(self):
        for name in ("team-🙂", "odd\x7fname", "team-\u2028", "a\x85b"):
            home = os.path.join(self.home, name)
            config = os.path.join(home, ".codex", "config.toml")
            os.makedirs(os.path.join(home, ".codex"))
            self.assertEqual([c.action for c in AGENT.install(home, COMMAND)], ["added", "added"], name)
            self.assertTrue(AGENT.installed(home), name)
            AGENT.uninstall(home)
            self.assertEqual(codex.read_toml(self.text(config)), {}, name)

    def test_an_untrusted_user_hook_that_moves_up_gains_no_trust(self):
        user = {"type": "command", "command": "/usr/local/bin/notify-prompt", "timeout": 5}
        self.put(self.hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler()]}, {"hooks": [user]}]}})
        self.put(self.config, self.trust_table())
        AGENT.uninstall(self.home)
        self.assertNotIn("trusted_hash", self.text(self.config))


class GuardTest(Home):
    """Every edge case earlier reviews found in hand-editing config.toml goes through install and uninstall. Each must
    leave the user's own settings, hooks and trust exactly as they were, and a wrong edit must write nothing."""

    USER = {"type": "command", "command": "/usr/local/bin/notify-prompt", "timeout": 5}
    HOMES = ("plain", "José W", "team-🙂", "team-\u2028", "a\x85b", "odd\x7fname")
    CONFIGS = {
        "plain": "{user}",
        "comments": "# my settings\nmodel = \"gpt-6.1\"  # chosen\n{user}",
        "quoted keys": "{user}".replace("trusted_hash =", '"trusted_hash" ='),
        "escaped key": "{user}".replace("trusted_hash =", '"trusted\\u005fhash" ='),
        "spaced header": "{user}".replace("[hooks.state.", "[ hooks . state . ").replace('"]\n', '" ]  # ok\n', 1),
        "single-quoted header": "{user}".replace('[hooks.state."', "[hooks.state.'").replace('"]\n', "']\n", 1),
        "multi-line string": 'notes = """\n[hooks.state."{kb}"]\ntrusted_hash = "decoy"\n"""\n{user}',
        "multi-line array": 'paths = [\n  ["a", "b"],\n  [\n    "c",\n  ],\n]\n{user}',
        "crlf": "model = \"gpt-6.1\"\r\n{user}",
    }

    def home_with(self, name, config):
        home = os.path.join(self.home, name)
        hooks_json = os.path.join(home, ".codex", "hooks.json")
        config_toml = os.path.join(home, ".codex", "config.toml")
        user_key = f"{os.path.realpath(hooks_json)}:user_prompt_submit:1:0"
        kb_key = f"{os.path.realpath(hooks_json)}:user_prompt_submit:0:0"
        user = f'[hooks.state."{user_key}"]\ntrusted_hash = "{codex.codex_hook_hash("user_prompt_submit", self.USER)}"\n'
        self.put(hooks_json, {"hooks": {"UserPromptSubmit": [{"hooks": [self.handler("/old/kb hook codex")]},
                                                              {"hooks": [self.USER]}]}})
        # TOML has no raw DEL in a string, so a key holding one is written escaped, as Codex writes it.
        self.put(config_toml, config.format(user=user, kb=kb_key).replace("\x7f", "\\u007f"))
        return home, hooks_json, config_toml

    def test_every_edge_case_installs_and_uninstalls_without_touching_the_users_entries(self):
        for home_name in self.HOMES:
            for config_name, config in self.CONFIGS.items():
                if "\x7f" in home_name and config_name == "single-quoted header":
                    continue  # a literal string cannot hold DEL, so no valid config spells this key that way
                with self.subTest(home=home_name, config=config_name):
                    home, hooks_json, config_toml = self.home_with(f"{home_name}-{config_name}", config)
                    original = codex.read_toml(self.text(config_toml))
                    self.assertNotIn("skipped", [c.action for c in AGENT.install(home, COMMAND)])
                    self.assertTrue(AGENT.installed(home))
                    self.assertNotIn("skipped", [c.action for c in AGENT.uninstall(home)])
                    self.assertFalse(AGENT.installed(home))
                    hooks = json.loads(self.text(hooks_json))
                    self.assertEqual(hooks, {"hooks": {"UserPromptSubmit": [{"hooks": [self.USER]}]}})
                    after = codex.read_toml(self.text(config_toml))
                    state = original["hooks"]["state"]
                    moved = {f"{os.path.realpath(hooks_json)}:user_prompt_submit:0:0":
                             state[f"{os.path.realpath(hooks_json)}:user_prompt_submit:1:0"]}
                    self.assertEqual(after, {**original, "hooks": {**original["hooks"], "state": moved}},
                                     "only kb's entry left, and the user's trust moved with its hook")

    def test_a_wrong_edit_writes_nothing(self):
        def also_changes_a_setting(text, key, digest):
            new, action = trusted(text, key, digest)
            return new.replace('model = "gpt-6.1"', 'model = "gpt-0"'), action

        def also_drops_the_users_trust(text, key):
            new, gone = untrusted(text, key)
            return new.replace("trusted_hash", "trusted_hash_was"), gone

        trusted, untrusted = codex._trusted, codex._untrusted
        for patch, step in ((mock.patch.object(codex, "_trusted", also_changes_a_setting), AGENT.install),
                            (mock.patch.object(codex, "_untrusted", also_drops_the_users_trust), AGENT.uninstall)):
            home, hooks_json, config_toml = self.home_with(step.__name__, self.CONFIGS["comments"])
            if step is AGENT.uninstall:
                AGENT.install(home, COMMAND)
            before = (self.text(hooks_json), self.text(config_toml))
            with patch:
                changes = step(home, COMMAND) if step is AGENT.install else step(home)
            self.assertEqual([c.action for c in changes], ["skipped", "skipped"], step.__name__)
            self.assertIn("more than kb's own entries", changes[0].note)
            self.assertIn("by hand", changes[0].note)
            self.assertEqual((self.text(hooks_json), self.text(config_toml)), before, "nothing is written")


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
