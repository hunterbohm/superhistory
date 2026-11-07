import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from kb import agents, check
from kb.agents import Change, _turns
from kb.ai.cli import NESTED_ENV

KB = "'/opt/kb tools/kb' hook claude-code"


class TempDir(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.realpath(tmp.name)
        self.file = os.path.join(self.dir, "sub", "hooks.json")

    def put(self, data, path=None):
        path = path or self.file
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data, indent=2))

    def text(self, path=None):
        with open(path or self.file, encoding="utf-8") as fh:
            return fh.read()

    def data(self):
        return json.loads(self.text())

    def backups(self):
        folder = os.path.dirname(self.file)
        return sorted(f for f in os.listdir(folder) if ".bak-kb-" in f)

    def leftovers(self):
        return [f for f in os.listdir(os.path.dirname(self.file)) if f.startswith(".kb-")]


class RegistryTest(unittest.TestCase):
    def test_names_are_the_agent_modules_with_dashes_sorted_and_without_helpers(self):
        names = agents.names()
        self.assertIn("claude-code", names)
        self.assertIn("codex", names)
        self.assertEqual(list(names), sorted(names))
        self.assertFalse([n for n in names if n.startswith("_") or "_" in n])

    def test_load_gives_each_named_agent(self):
        for name in agents.names():
            with self.subTest(name):
                agent = agents.load(name)
                self.assertIsInstance(agent, agents.Agent)
                self.assertTrue(agent.label)

    def test_load_refuses_names_that_are_not_agents(self):
        for name in ("nope", "_turns", "claude_code", "../x", ""):
            with self.subTest(name):
                with self.assertRaises(KeyError):
                    agents.load(name)

    def test_command_quotes_the_path_and_names_the_agent(self):
        self.assertEqual(agents.command("/usr/bin/kb", "codex"), "/usr/bin/kb hook codex")
        self.assertEqual(agents.command("/opt/kb tools/kb", "claude-code"), KB)

    def test_is_kb_command_matches_any_kb_path_for_that_agent_only(self):
        self.assertTrue(agents.is_kb_command(KB, "claude-code"))
        self.assertTrue(agents.is_kb_command("kb hook claude-code\n", "claude-code"))
        self.assertTrue(agents.is_kb_command("/old/place/kb hook codex", "codex"))
        self.assertFalse(agents.is_kb_command(KB, "codex"))
        self.assertFalse(agents.is_kb_command("tmux-signal running", "codex"))
        self.assertFalse(agents.is_kb_command("kb hook codex --extra", "codex"))
        self.assertFalse(agents.is_kb_command(None, "codex"))


class RunTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(NESTED_ENV, None)
        os.environ.pop(agents.GROK_ENV, None)
        answer = mock.patch.object(check, "answer", return_value="RECORDS")
        self.answer = answer.start()
        self.addCleanup(answer.stop)

    def run_hook(self, stdin, name="claude-code"):
        return agents.run("paths", "config", name, stdin)

    def test_records_come_back_in_the_form_the_agent_reads(self):
        out = self.run_hook(json.dumps({"prompt": "what did she say", "session_id": "s1"}))
        self.assertEqual(json.loads(out), {"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit", "additionalContext": "RECORDS"}})
        paths, config, request, session = self.answer.call_args[0]
        self.assertEqual((paths, config, session), ("paths", "config", "s1"))
        self.assertEqual(json.loads(request), {"message": "what did she say", "before": []})

    def deliver(self, written_during_check):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = os.path.join(tmp, "session.jsonl")
            with open(transcript, "w") as fh:
                fh.write(json.dumps({"type": "assistant", "message": {"stop_reason": "end_turn"}}) + "\n")

            def check_while_the_agent_works(*args):
                with open(transcript, "a") as fh:
                    fh.write(json.dumps(written_during_check) + "\n")
                return "RECORDS"

            self.answer.side_effect = check_while_the_agent_works
            event = {"prompt": "what did she say", "session_id": "s", "transcript_path": transcript}
            return self.run_hook(json.dumps(event))

    def test_records_reach_a_turn_still_running(self):
        for name, line in {
            "the prompt itself": {"type": "user", "message": {"content": "what did she say"}},
            "a tool call": {"type": "assistant", "message": {"stop_reason": "tool_use"}},
            "a subagent's answer": {"type": "assistant", "isSidechain": True, "message": {"stop_reason": "end_turn"}},
            "a tool result naming end_turn": {"type": "user", "message": {"content": [
                {"type": "tool_result", "content": '"stop_reason":"end_turn" task_complete'}]}},
        }.items():
            with self.subTest(name):
                self.assertEqual(json.loads(self.deliver(line))["hookSpecificOutput"]["additionalContext"], "RECORDS")

    def test_records_for_a_turn_that_ended_are_dropped(self):
        for name, line in {
            "claude answered": {"type": "assistant", "message": {"stop_reason": "end_turn"}},
            "claude interrupted": {"type": "user", "message": {"content": "[Request interrupted by user]"}},
            "codex finished": {"type": "event_msg", "payload": {"type": "task_complete"}},
            "codex aborted": {"type": "event_msg", "payload": {"type": "turn_aborted"}},
        }.items():
            with self.subTest(name):
                self.assertEqual(self.deliver(line), "")

    def test_nothing_found_prints_nothing(self):
        self.answer.return_value = ""
        self.assertEqual(self.run_hook(json.dumps({"prompt": "yes", "session_id": "s"})), "")
        self.answer.assert_called_once()

    def test_inside_a_kb_model_call_it_stays_out(self):
        os.environ[NESTED_ENV] = "1"
        self.assertEqual(self.run_hook(json.dumps({"prompt": "p"})), "")
        self.answer.assert_not_called()

    def test_inside_a_grok_hook_it_stays_out(self):
        os.environ[agents.GROK_ENV] = "user_prompt_submit"
        self.assertEqual(self.run_hook(json.dumps({"prompt": "p"})), "")
        self.answer.assert_not_called()

    def test_bad_input_prints_nothing_and_does_not_raise(self):
        for stdin in ("", "not json", "[]", "null", "42", '"text"'):
            with self.subTest(stdin):
                self.assertEqual(self.run_hook(stdin), "")
        self.assertEqual(self.run_hook(json.dumps({"prompt": "p"}), name="no-such-agent"), "")
        self.answer.assert_not_called()

    def test_a_request_that_stays_out_never_reaches_the_check(self):
        agent = agents.load("claude-code")._replace(request=lambda event: None)
        with mock.patch.object(agents, "load", return_value=agent):
            self.assertEqual(self.run_hook("{}"), "")
        self.answer.assert_not_called()


class WriteTest(TempDir):
    def test_creates_a_new_file_and_its_folder_without_a_backup(self):
        agents.write(self.file, "one\n")
        self.assertEqual(self.text(), "one\n")
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.leftovers(), [])

    def test_backs_up_the_old_text_beside_the_file_before_replacing_it(self):
        self.put("old\n")
        agents.write(self.file, "new\n")
        self.assertEqual(self.text(), "new\n")
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.text(os.path.join(os.path.dirname(self.file), self.backups()[0])), "old\n")
        self.assertEqual(self.leftovers(), [])

    def test_keeps_the_file_mode(self):
        self.put("old\n")
        os.chmod(self.file, 0o600)
        agents.write(self.file, "new\n")
        self.assertEqual(stat.S_IMODE(os.stat(self.file).st_mode), 0o600)

    def test_writes_through_a_symlink_and_leaves_it_a_symlink(self):
        target = os.path.join(self.dir, "dotfiles", "hooks.json")
        self.put("old\n", target)
        os.makedirs(os.path.dirname(self.file))
        os.symlink(target, self.file)
        agents.write(self.file, "new\n")
        self.assertTrue(os.path.islink(self.file))
        self.assertEqual(self.text(target), "new\n")

    def test_read_gives_empty_text_for_a_missing_file(self):
        self.assertEqual(agents.read(self.file), "")
        self.put("x")
        self.assertEqual(agents.read(self.file), "x")


class JsonHookTest(TempDir):
    EVENT = "UserPromptSubmit"

    def add(self, command=KB, name="claude-code"):
        return agents.add_json_hook(self.file, self.EVENT, name, command)

    def kb_handler(self, command=KB, timeout=agents.HOOK_TIMEOUT):
        return {"type": "command", "command": command, "timeout": timeout}

    def test_adds_to_a_missing_file_with_the_hook_timeout(self):
        gi, hi, handler, change = self.add()
        self.assertEqual((gi, hi, change), (0, 0, Change(self.file, "added")))
        self.assertEqual(handler, self.kb_handler())
        self.assertEqual(self.data(), {"hooks": {self.EVENT: [{"hooks": [self.kb_handler()]}]}})
        self.assertEqual(self.backups(), [])

    def test_adds_to_an_empty_file(self):
        self.put("")
        self.assertEqual(self.add()[3].action, "added")
        self.assertEqual(self.data(), {"hooks": {self.EVENT: [{"hooks": [self.kb_handler()]}]}})

    def test_adding_twice_changes_nothing_and_makes_no_second_backup(self):
        self.put({"model": "x"})
        self.assertEqual(self.add()[3].action, "added")
        after_first, backups = self.text(), self.backups()
        self.assertEqual(len(backups), 1)
        gi, hi, _, change = self.add()
        self.assertEqual((gi, hi, change), (0, 0, Change(self.file, "unchanged")))
        self.assertEqual(self.text(), after_first)
        self.assertEqual(self.backups(), backups)

    def test_keeps_the_users_other_hooks_and_settings_and_their_formatting(self):
        other = {"matcher": "", "hooks": [{"type": "command", "command": "tmux-signal running"}]}
        self.put(json.dumps({"model": "opus", "hooks": {"SessionStart": [{"hooks": [{"command": "x"}]}],
                                                       self.EVENT: [other]}}, indent=2) + "\n")
        gi, hi, _, _ = self.add()
        self.assertEqual((gi, hi), (1, 0))
        expected = {"model": "opus", "hooks": {"SessionStart": [{"hooks": [{"command": "x"}]}],
                                               self.EVENT: [other, {"hooks": [self.kb_handler()]}]}}
        self.assertEqual(self.text(), json.dumps(expected, indent=2) + "\n")

    def test_no_trailing_newline_stays_that_way(self):
        self.put(json.dumps({"a": 1}))
        self.add()
        self.assertFalse(self.text().endswith("\n"))

    def test_an_older_kb_entry_with_another_path_or_timeout_is_updated_in_place(self):
        old = {"type": "command", "command": "/old/kb hook claude-code", "timeout": 14, "statusMessage": "keep"}
        beside = {"type": "command", "command": "echo hi"}
        self.put({"hooks": {self.EVENT: [{"hooks": [beside, old]}]}})
        gi, hi, handler, change = self.add()
        self.assertEqual((gi, hi, change.action), (0, 1, "updated"))
        self.assertEqual(self.data()["hooks"][self.EVENT],
                         [{"hooks": [beside, {**self.kb_handler(), "statusMessage": "keep"}]}])
        self.assertEqual(len(self.backups()), 1)

    def test_only_the_timeout_differing_is_an_update(self):
        self.put({"hooks": {self.EVENT: [{"hooks": [self.kb_handler(timeout=5)]}]}})
        self.assertEqual(self.add()[3].action, "updated")
        self.assertEqual(self.data()["hooks"][self.EVENT][0]["hooks"][0]["timeout"], agents.HOOK_TIMEOUT)

    def test_another_agents_kb_entry_is_not_this_ones(self):
        self.put({"hooks": {self.EVENT: [{"hooks": [self.kb_handler("/kb hook codex")]}]}})
        self.assertEqual(self.add()[3].action, "added")
        self.assertFalse(agents.has_json_hook(self.file, self.EVENT, "hermes"))
        self.assertTrue(agents.has_json_hook(self.file, self.EVENT, "codex"))
        self.assertTrue(agents.has_json_hook(self.file, self.EVENT, "claude-code"))

    def test_a_malformed_file_raises_and_is_left_alone(self):
        self.put("{not json")
        with self.assertRaises(ValueError):
            self.add()
        self.assertEqual(self.text(), "{not json")
        self.assertEqual(self.backups(), [])

    def test_has_json_hook_is_false_for_missing_empty_and_hookless_files(self):
        self.assertFalse(agents.has_json_hook(self.file, self.EVENT, "claude-code"))
        self.put("")
        self.assertFalse(agents.has_json_hook(self.file, self.EVENT, "claude-code"))
        self.put({"hooks": None})
        self.assertFalse(agents.has_json_hook(self.file, self.EVENT, "claude-code"))
        self.put({"hooks": {self.EVENT: [{"hooks": [{"command": "echo hi"}]}, {}]}})
        self.assertFalse(agents.has_json_hook(self.file, self.EVENT, "claude-code"))

    def test_remove_takes_only_kb_and_leaves_the_rest(self):
        other = {"type": "command", "command": "echo hi"}
        self.put({"model": "x", "hooks": {self.EVENT: [{"matcher": "m", "hooks": [other, self.kb_handler()]}],
                                          "Stop": [{"hooks": [other]}]}})
        change = agents.remove_json_hook(self.file, self.EVENT, "claude-code")
        self.assertEqual(change, Change(self.file, "removed"))
        self.assertEqual(self.data(), {"model": "x", "hooks": {self.EVENT: [{"matcher": "m", "hooks": [other]}],
                                                              "Stop": [{"hooks": [other]}]}})
        self.assertFalse(agents.has_json_hook(self.file, self.EVENT, "claude-code"))

    def test_remove_drops_an_emptied_group_event_and_hooks_key(self):
        self.put({"model": "x", "hooks": {self.EVENT: [{"hooks": [self.kb_handler()]}]}})
        agents.remove_json_hook(self.file, self.EVENT, "claude-code")
        self.assertEqual(self.data(), {"model": "x"})

    def test_remove_keeps_other_events_and_groups(self):
        other = {"hooks": [{"command": "echo hi"}]}
        self.put({"hooks": {self.EVENT: [other, {"hooks": [self.kb_handler()]}], "Stop": [other]}})
        agents.remove_json_hook(self.file, self.EVENT, "claude-code")
        self.assertEqual(self.data(), {"hooks": {self.EVENT: [other], "Stop": [other]}})

    def test_remove_with_nothing_to_remove_changes_nothing(self):
        self.assertEqual(agents.remove_json_hook(self.file, self.EVENT, "claude-code"),
                         Change(self.file, "unchanged"))
        self.assertFalse(os.path.exists(self.file))
        self.put("")
        self.assertEqual(agents.remove_json_hook(self.file, self.EVENT, "claude-code").action, "unchanged")
        data = {"hooks": {self.EVENT: [{"hooks": [{"command": "echo hi"}]}]}}
        self.put(data)
        before = self.text()
        self.assertEqual(agents.remove_json_hook(self.file, self.EVENT, "claude-code").action, "unchanged")
        self.assertEqual(self.text(), before)
        self.assertEqual(self.backups(), [])

    def test_install_then_remove_restores_the_file(self):
        original = json.dumps({"model": "x", "hooks": {"Stop": [{"hooks": [{"command": "echo hi"}]}]}}, indent=2) + "\n"
        self.put(original)
        self.add()
        agents.remove_json_hook(self.file, self.EVENT, "claude-code")
        self.assertEqual(self.text(), original)

    def test_remove_from_a_malformed_file_raises_and_leaves_it(self):
        self.put("{not json")
        with self.assertRaises(ValueError):
            agents.remove_json_hook(self.file, self.EVENT, "claude-code")
        self.assertEqual(self.text(), "{not json")


class TurnsTest(TempDir):
    def transcript(self, lines, header=True):
        lines = ([{"type": "summary", "summary": "x"}] if header else []) + list(lines)
        path = os.path.join(self.dir, "session.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines) + "\n")
        return path

    def claude(self, role, text, **extra):
        return {"type": role, "message": {"content": text}, **extra}

    def test_the_prompt_itself_is_dropped_from_the_turns(self):
        path = self.transcript([self.claude("user", "first"), self.claude("assistant", "answer"),
                                self.claude("user", "now this")])
        self.assertEqual(_turns.turns_before(path, "now this"), [{"role": "user", "text": "first"},
                                                                {"role": "assistant", "text": "answer"}])
        self.assertEqual(_turns.turns_before(path, "  now this \n"), [{"role": "user", "text": "first"},
                                                                     {"role": "assistant", "text": "answer"}])

    def test_a_turn_holding_a_line_separator_character_is_read_whole(self):
        raw = json.dumps(self.claude("user", "one\u2028two\x85three"), ensure_ascii=False)  # as JavaScript writes it
        path = self.transcript([raw, self.claude("assistant", "answer")])
        self.assertEqual(_turns.turns_before(path, "next")[0], {"role": "user", "text": "one\u2028two\x85three"})

    def test_a_different_last_user_turn_is_kept(self):
        path = self.transcript([self.claude("assistant", "answer"), self.claude("user", "something else")])
        self.assertEqual(_turns.turns_before(path, "now this")[-1], {"role": "user", "text": "something else"})

    def test_only_the_last_two_turns_come_back_oldest_first(self):
        path = self.transcript([self.claude("user", "a"), self.claude("assistant", "b"), self.claude("user", "c"),
                                self.claude("assistant", "d")])
        self.assertEqual([t["text"] for t in _turns.turns_before(path, "e")], ["c", "d"])

    def test_injected_meta_and_sidechain_turns_are_skipped(self):
        path = self.transcript([
            self.claude("user", "real question"),
            self.claude("user", "<system-reminder>x</system-reminder>"),
            self.claude("user", "<task-notification>done</task-notification>"),
            self.claude("user", "Caveat: the messages below", isMeta=True),
            self.claude("user", "<command-name>/clear</command-name>"),
            self.claude("user", "[Request interrupted by user]"),
            self.claude("assistant", "side", isSidechain=True),
            {"type": "summary", "summary": "x"},
            self.claude("user", [{"type": "tool_result", "content": "ok"}]),
            self.claude("assistant", [{"type": "text", "text": "the answer"}, {"type": "tool_use", "name": "Bash"}]),
        ])
        self.assertEqual(_turns.turns_before(path, "next"), [{"role": "user", "text": "real question"},
                                                            {"role": "assistant", "text": "the answer"}])

    def test_consecutive_turns_of_one_role_are_joined(self):
        path = self.transcript([self.claude("user", "q"), self.claude("assistant", "one"),
                                self.claude("assistant", "two")])
        self.assertEqual(_turns.turns_before(path, "x")[-1], {"role": "assistant", "text": "one\ntwo"})

    def test_long_turns_are_clipped_user_from_the_start_assistant_from_the_end(self):
        path = self.transcript([self.claude("user", "u" * 700), self.claude("assistant", "a" * 1700 + "END")])
        user, assistant = _turns.turns_before(path, "x")
        self.assertEqual(user["text"], "u" * _turns.BEFORE_CHARS["user"])
        self.assertEqual(len(assistant["text"]), _turns.BEFORE_CHARS["assistant"])
        self.assertTrue(assistant["text"].endswith("END"))

    def test_unreadable_lines_are_skipped(self):
        path = self.transcript(["", "not json", self.claude("user", "q"), self.claude("assistant", "a")])
        self.assertEqual(len(_turns.turns_before(path, "x")), 2)

    def test_a_big_transcript_reads_only_its_tail(self):
        filler = self.claude("user", "f" * 1000)
        path = self.transcript([filler, self.claude("assistant", "f")] * 1200
                               + [self.claude("user", "q"), self.claude("assistant", "a")])
        self.assertGreater(os.path.getsize(path), _turns.TAIL_BYTES)
        self.assertEqual([t["text"] for t in _turns.turns_before(path, "x")], ["q", "a"])

    def test_the_first_line_of_a_small_transcript_is_a_turn_too(self):
        path = self.transcript([self.claude("user", "first"), self.claude("assistant", "answer")], header=False)
        self.assertEqual([t["text"] for t in _turns.turns_before(path, "x")], ["first", "answer"])

    def test_a_missing_transcript_raises_oserror_for_the_agents_to_handle(self):
        with self.assertRaises(OSError):
            _turns.turns_before(os.path.join(self.dir, "none.jsonl"), "x")


if __name__ == "__main__":
    unittest.main()


class GuardedJsonWritesTest(unittest.TestCase):
    """Claude Code, Codex and Copilot keep their hooks in the user's JSON. kb writes there only when the file reads
    back exactly and nothing but kb's entry changes."""

    FILES = {"claude-code": (".claude", "settings.json"), "codex": (".codex", "hooks.json"),
             "copilot": (".copilot", "config.json")}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        patch = mock.patch.dict(os.environ, {"COPILOT_HOME": ""})
        patch.start()
        self.addCleanup(patch.stop)

    def path(self, name):
        return os.path.join(self.home, *self.FILES[name])

    def put(self, name, text):
        os.makedirs(os.path.dirname(self.path(name)), exist_ok=True)
        with open(self.path(name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def text(self, name):
        with open(self.path(name), encoding="utf-8") as fh:
            return fh.read()

    def act(self, name, action):
        agent = agents.load(name)
        try:
            changes = agent.install(self.home, agents.command("/opt/kb", name)) if action == "install" \
                else agent.uninstall(self.home)
        except agents.Unreadable as exc:
            return ["skipped", str(exc)]
        return [c.action for c in changes]

    def test_a_number_json_cannot_write_back_is_left_alone(self):
        for name in self.FILES:
            for odd in ('{"custom_number": 1e400}', '{"custom": NaN}', '{"custom": -Infinity}'):
                with self.subTest(agent=name, file=odd):
                    self.put(name, odd)
                    self.assertIn("skipped", self.act(name, "install"))
                    self.assertEqual(self.text(name), odd)

    def test_an_empty_group_of_the_users_survives_install_and_uninstall(self):
        for name, event in (("claude-code", "UserPromptSubmit"), ("codex", "UserPromptSubmit")):
            with self.subTest(agent=name):
                original = {"model": "user", "hooks": {event: [{"matcher": "saved", "hooks": []}]}}
                self.put(name, json.dumps(original))
                self.assertNotIn("skipped", self.act(name, "install"))
                self.assertNotIn("skipped", self.act(name, "uninstall"))
                self.assertEqual(json.loads(self.text(name)), original)

    def test_a_wrong_json_edit_writes_nothing(self):
        def also_drops_a_setting(text, path, event, name):
            new, action = removed(text, path, event, name)
            data = json.loads(new)
            data.pop("model", None)
            return json.dumps(data), action

        removed = agents.json_hook_removed
        self.put("claude-code", json.dumps({"model": "user"}))
        self.act("claude-code", "install")
        before = self.text("claude-code")
        with mock.patch.object(agents, "json_hook_removed", also_drops_a_setting):
            result = self.act("claude-code", "uninstall")
        self.assertEqual(result[0], "skipped")
        self.assertIn("more than kb's own entries", result[1])
        self.assertEqual(self.text("claude-code"), before)

    def test_copilot_writes_only_its_own_entry(self):
        original = {"theme": "dark", "hooks": {"userPromptSubmitted": [{"type": "command", "bash": "notify"}]}}
        self.put("copilot", json.dumps(original))
        self.assertEqual(self.act("copilot", "install"), ["added"])
        self.assertEqual(self.act("copilot", "uninstall"), ["removed"])
        self.assertEqual(json.loads(self.text("copilot")), original)
        with mock.patch.object(agents, "_dump", lambda data, old: json.dumps({**data, "theme": "light"})):
            self.assertEqual(self.act("copilot", "install"), ["skipped"])
        self.assertEqual(json.loads(self.text("copilot")), original)


class UnreadableSettingsTest(unittest.TestCase):
    def test_a_malformed_settings_file_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            with open(path, "w") as fh:
                fh.write("{not json")
            with self.assertRaises(agents.Unreadable):
                agents.add_json_hook(path, "UserPromptSubmit", "claude-code", "'/x/kb' hook claude-code")
            with self.assertRaises(agents.Unreadable):
                agents.remove_json_hook(path, "UserPromptSubmit", "claude-code")
            self.assertFalse(agents.has_json_hook(path, "UserPromptSubmit", "claude-code"))
            with open(path) as fh:
                self.assertEqual(fh.read(), "{not json")
            self.assertEqual(os.listdir(tmp), ["settings.json"])


class OwnershipAndBackupTest(unittest.TestCase):
    def test_only_a_kb_command_counts_as_kb_s_hook(self):
        self.assertTrue(agents.is_kb_command("'/opt/kb tools/kb' hook claude-code", "claude-code"))
        self.assertTrue(agents.is_kb_command(agents.command("/home/sam/.local/bin/kb", "codex"), "codex"))
        for other in ("notify-send hook claude-code", "/usr/bin/kb hook codex", "kb hook claude-code --x", "kb 'unclosed"):
            with self.subTest(other):
                self.assertFalse(agents.is_kb_command(other, "claude-code"))

    def test_install_and_uninstall_leave_a_user_hook_with_the_same_ending_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            user = {"type": "command", "command": "notify-send hook claude-code"}
            with open(path, "w") as fh:
                json.dump({"hooks": {"UserPromptSubmit": [{"hooks": [user]}]}}, fh)
            agents.add_json_hook(path, "UserPromptSubmit", "claude-code", "'/x/kb' hook claude-code")
            agents.remove_json_hook(path, "UserPromptSubmit", "claude-code")
            with open(path) as fh:
                self.assertEqual(json.load(fh)["hooks"]["UserPromptSubmit"], [{"hooks": [user]}])

    def test_two_changes_in_one_second_keep_the_original_in_a_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            with open(path, "w") as fh:
                fh.write("original")
            agents.write(path, "first change")
            agents.write(path, "second change")
            texts = sorted(open(os.path.join(tmp, f)).read() for f in os.listdir(tmp) if ".bak-kb-" in f)
            self.assertEqual(texts, ["first change", "original"])


class KbPathTest(unittest.TestCase):
    def test_hooks_install_refuses_a_command_not_named_kb(self):
        import contextlib
        import io
        from kb import cli
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "home", ".claude"))
            err = io.StringIO()
            with mock.patch.dict(os.environ, {"KB_HOME": os.path.join(tmp, "kb"), "HOME": os.path.join(tmp, "home")}), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = cli.main(["hooks", "install", "claude-code", "--kb", "/opt/tools/kb-custom"])
            self.assertEqual(code, 1)
            self.assertIn("named kb", err.getvalue())
            self.assertFalse(os.path.exists(os.path.join(tmp, "home", ".claude", "settings.json")))
