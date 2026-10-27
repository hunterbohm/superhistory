import json
import os
import tempfile
import unittest
from unittest import mock

from kb import agents

AGENT = agents.load("copilot")
KB = agents.command("/opt/kb/bin/kb", "copilot")
EVENT = "userPromptSubmitted"


def kb_entry(command=KB, timeout=30):
    return {"type": "command", "bash": command, "timeoutSec": timeout}


class CopilotCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.home)
        os.makedirs(self.bin)
        env = {k: v for k, v in os.environ.items() if k != "COPILOT_HOME"}
        env.update(HOME=self.home, PATH=self.bin)
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.folder = os.path.join(self.home, ".copilot")
        self.config = os.path.join(self.folder, "config.json")

    def write(self, data):
        os.makedirs(self.folder, exist_ok=True)
        with open(self.config, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data, indent=2) + "\n")

    def read(self):
        with open(self.config, encoding="utf-8") as fh:
            return json.load(fh)

    def backups(self):
        return [f for f in os.listdir(self.folder) if f.startswith("config.json.bak-kb-")]


class InstallTest(CopilotCase):
    def test_install_into_a_missing_config_adds_the_hook(self):
        self.assertFalse(AGENT.installed(self.home))
        self.assertEqual(AGENT.install(self.home, KB), [agents.Change(self.config, "added")])
        self.assertEqual(self.read(), {"hooks": {EVENT: [kb_entry()]}})
        self.assertTrue(AGENT.installed(self.home))

    def test_install_twice_changes_nothing_and_makes_no_backup(self):
        self.write({"model": "some-model"})
        AGENT.install(self.home, KB)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(AGENT.install(self.home, KB), [agents.Change(self.config, "unchanged")])
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.read(), {"model": "some-model", "hooks": {EVENT: [kb_entry()]}})

    def test_install_keeps_the_users_settings_and_other_hooks(self):
        theirs = {"type": "command", "bash": "./log-prompt.sh", "timeoutSec": 5}
        session = {"type": "command", "bash": "./start.sh"}
        self.write({"model": "some-model", "trusted_folders": ["/work"],
                    "hooks": {EVENT: [theirs], "sessionStart": [session]}})
        AGENT.install(self.home, KB)
        self.assertEqual(self.read(), {"model": "some-model", "trusted_folders": ["/work"],
                                       "hooks": {EVENT: [theirs, kb_entry()], "sessionStart": [session]}})
        with open(os.path.join(self.folder, self.backups()[0]), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["hooks"][EVENT], [theirs])

    def test_an_older_kb_entry_is_updated_in_place(self):
        theirs = {"type": "command", "bash": "./after.sh"}
        self.write({"hooks": {EVENT: [kb_entry("'/old/kb' hook copilot", 12), theirs]}})
        self.assertEqual(AGENT.install(self.home, KB), [agents.Change(self.config, "updated")])
        self.assertEqual(self.read(), {"hooks": {EVENT: [kb_entry(), theirs]}})

    def test_the_config_follows_copilot_home(self):
        elsewhere = os.path.join(self.tmp.name, "copilot-home")
        with mock.patch.dict(os.environ, {"COPILOT_HOME": elsewhere}):
            self.assertEqual(AGENT.install(self.home, KB),
                             [agents.Change(os.path.join(elsewhere, "config.json"), "added")])
            self.assertTrue(AGENT.installed(self.home))
        self.assertFalse(os.path.exists(self.config))
        self.assertFalse(AGENT.installed(self.home))

    def test_a_malformed_config_is_skipped_and_left_as_it_is(self):
        for text in ("{not json", "[1, 2]", '{"hooks": []}', '{"hooks": {"userPromptSubmitted": {}}}'):
            with self.subTest(text=text):
                self.write(text)
                self.assertEqual([c.action for c in AGENT.install(self.home, KB)], ["skipped"])
                self.assertEqual([c.action for c in AGENT.uninstall(self.home)], ["skipped"])
                self.assertFalse(AGENT.installed(self.home))
                with open(self.config, encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), text)
                self.assertEqual(self.backups(), [])

    def test_an_empty_config_file_is_filled_in(self):
        self.write("")
        self.assertEqual(AGENT.install(self.home, KB), [agents.Change(self.config, "added")])
        self.assertEqual(self.read(), {"hooks": {EVENT: [kb_entry()]}})


class UninstallTest(CopilotCase):
    def test_uninstall_removes_only_kbs_entry(self):
        theirs = {"type": "command", "bash": "./log-prompt.sh"}
        self.write({"model": "m", "hooks": {EVENT: [theirs, kb_entry()], "sessionEnd": [theirs]}})
        self.assertEqual(AGENT.uninstall(self.home), [agents.Change(self.config, "removed")])
        self.assertEqual(self.read(), {"model": "m", "hooks": {EVENT: [theirs], "sessionEnd": [theirs]}})
        self.assertFalse(AGENT.installed(self.home))
        self.assertEqual(AGENT.uninstall(self.home), [agents.Change(self.config, "unchanged")])

    def test_uninstalling_the_only_hook_drops_the_empty_hooks_section(self):
        self.write({"model": "m"})
        AGENT.install(self.home, KB)
        AGENT.uninstall(self.home)
        self.assertEqual(self.read(), {"model": "m"})

    def test_uninstall_without_a_config_changes_nothing(self):
        self.assertEqual(AGENT.uninstall(self.home), [agents.Change(self.config, "unchanged")])
        self.assertFalse(os.path.exists(self.folder))


class DetectTest(CopilotCase):
    def test_nothing_found_without_a_config_folder_or_the_command(self):
        self.assertFalse(AGENT.detect(self.home))

    def test_found_by_its_config_folder(self):
        os.makedirs(self.folder)
        self.assertTrue(AGENT.detect(self.home))

    def test_found_by_the_command_on_path(self):
        path = os.path.join(self.bin, "copilot")
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(path, 0o755)
        self.assertTrue(AGENT.detect(self.home))


class RequestReplyTest(unittest.TestCase):
    def test_request_reads_the_prompt_and_session_from_the_hook_input(self):
        event = {"sessionId": "6a1f0c2e-1111-4222-8333-444455556666", "timestamp": 1793087381567,
                 "cwd": "/work/app", "prompt": "What date did the launch move to?"}
        self.assertEqual(AGENT.request(event),
                         ("What date did the launch move to?", [], "6a1f0c2e-1111-4222-8333-444455556666"))

    def test_request_stays_out_of_an_empty_prompt(self):
        self.assertIsNone(AGENT.request({"sessionId": "s", "timestamp": 1, "cwd": "/", "prompt": " "}))
        self.assertIsNone(AGENT.request({}))

    def test_reply_is_json_with_the_records_as_additional_context(self):
        self.assertEqual(json.loads(AGENT.reply("[kb] a record\nline two")),
                         {"additionalContext": "[kb] a record\nline two"})


if __name__ == "__main__":
    unittest.main()
