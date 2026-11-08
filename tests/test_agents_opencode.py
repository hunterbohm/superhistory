import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from kb import agents
from kb.agents import opencode

AGENT = agents.load("opencode")
JS_RUNTIME = shutil.which("bun") or shutil.which("node")

FAKE_KB = """#!{python}
import json, os, sys, time
with open(os.environ["FAKE_SUPERHISTORY_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"argv": sys.argv[1:], "stdin": json.loads(sys.stdin.read())}}) + "\\n")
time.sleep(float(os.environ.get("FAKE_SUPERHISTORY_SLEEP") or 0))
sys.stdout.write(os.environ.get("FAKE_SUPERHISTORY_OUTPUT", ""))
sys.exit(int(os.environ.get("FAKE_SUPERHISTORY_EXIT") or 0))
"""

DRIVER = """import { pathToFileURL } from "node:url";
const [file, events, input, output] = [process.argv[2], ...process.argv.slice(3).map((a) => JSON.parse(a))];
const mod = await import(pathToFileURL(file).href);
const hooks = await Object.values(mod)[0]({});
for (const event of events) await hooks.event({ event });
await hooks["chat.message"](input, output);
console.log(JSON.stringify({ exports: Object.keys(mod), parts: output.parts }));
"""


class OpenCodeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.home)
        os.makedirs(self.bin)
        env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        env.update(HOME=self.home, PATH=self.bin)
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.plugins = os.path.join(self.home, ".config", "opencode", "plugins")
        self.plugin = os.path.join(self.plugins, "superhistory.js")
        self.kb = self.fake_kb("superhistory")

    def fake_kb(self, name):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(FAKE_KB.format(python=sys.executable))
        os.chmod(path, 0o755)
        return path

    def command(self, kb_path=None):
        return agents.command(kb_path or self.kb, "opencode")

    def files(self):
        return sorted(os.listdir(self.plugins))

    def read(self):
        with open(self.plugin, encoding="utf-8") as fh:
            return fh.read()


class InstallTest(OpenCodeCase):
    def test_install_writes_the_plugin_into_the_global_plugins_folder(self):
        self.assertFalse(AGENT.installed(self.home))
        self.assertEqual(AGENT.install(self.home, self.command()), [agents.Change(self.plugin, "added")])
        self.assertTrue(AGENT.installed(self.home))
        self.assertEqual(self.files(), ["superhistory.js"])

    def test_install_twice_changes_nothing_and_makes_no_backup(self):
        AGENT.install(self.home, self.command())
        before = self.read()
        self.assertEqual(AGENT.install(self.home, self.command()), [agents.Change(self.plugin, "unchanged")])
        self.assertEqual(self.read(), before)
        self.assertEqual(self.files(), ["superhistory.js"])

    def test_install_over_an_older_kb_plugin_updates_it_and_backs_it_up(self):
        AGENT.install(self.home, self.command(self.fake_kb("old-superhistory")))
        self.assertEqual(AGENT.install(self.home, self.command()), [agents.Change(self.plugin, "updated")])
        self.assertEqual(len([f for f in self.files() if f.startswith("superhistory.js.bak-superhistory-")]), 1)
        self.assertTrue(AGENT.installed(self.home))

    def test_a_timeout_change_updates_the_plugin(self):
        AGENT.install(self.home, self.command())
        with mock.patch.object(agents, "HOOK_TIMEOUT", 5):
            self.assertEqual(AGENT.install(self.home, self.command()), [agents.Change(self.plugin, "updated")])

    def test_the_config_folder_follows_xdg_config_home(self):
        xdg = os.path.join(self.tmp.name, "xdg")
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": xdg}):
            self.assertEqual(AGENT.install(self.home, self.command()),
                             [agents.Change(os.path.join(xdg, "opencode", "plugins", "superhistory.js"), "added")])
            self.assertTrue(AGENT.installed(self.home))
        self.assertFalse(os.path.exists(self.plugins))

    def test_other_plugins_are_kept_by_install_and_uninstall(self):
        os.makedirs(self.plugins)
        other = os.path.join(self.plugins, "mine.js")
        with open(other, "w", encoding="utf-8") as fh:
            fh.write("export const Mine = async () => ({});\n")
        AGENT.install(self.home, self.command())
        AGENT.uninstall(self.home)
        self.assertEqual(self.files(), ["mine.js"])

    def test_a_file_of_the_same_name_that_kb_did_not_write_is_left_alone(self):
        os.makedirs(self.plugins)
        theirs = "export const Theirs = async () => ({});\n"
        with open(self.plugin, "w", encoding="utf-8") as fh:
            fh.write(theirs)
        self.assertEqual([c.action for c in AGENT.install(self.home, self.command())], ["skipped"])
        self.assertFalse(AGENT.installed(self.home))
        self.assertEqual([c.action for c in AGENT.uninstall(self.home)], ["skipped"])
        self.assertEqual(self.read(), theirs)
        self.assertEqual(self.files(), ["superhistory.js"])


class RenderTest(unittest.TestCase):
    def test_a_kb_path_holding_a_placeholder_is_kept_as_it_is(self):
        path = "/opt/tools-__TIMEOUT_MS__/superhistory"
        rendered = opencode._render(agents.command(path, "opencode"))
        self.assertIn(json.dumps([path, "hook", "opencode"]), rendered)
        self.assertIn(f"const TIMEOUT_MS = {agents.HOOK_TIMEOUT * 1000};", rendered)


class EmptyFileTest(OpenCodeCase):
    def test_an_empty_file_with_the_plugins_name_is_left_alone(self):
        os.makedirs(self.plugins, exist_ok=True)
        open(self.plugin, "w").close()
        self.assertEqual([c.action for c in AGENT.install(self.home, self.command())], ["skipped"])
        self.assertEqual(self.read(), "")


class UninstallTest(OpenCodeCase):
    def test_uninstall_removes_the_plugin_and_a_second_run_changes_nothing(self):
        AGENT.install(self.home, self.command())
        self.assertEqual(AGENT.uninstall(self.home), [agents.Change(self.plugin, "removed")])
        self.assertFalse(AGENT.installed(self.home))
        self.assertEqual(self.files(), [])
        self.assertEqual(AGENT.uninstall(self.home), [agents.Change(self.plugin, "unchanged")])

    def test_uninstall_without_opencode_changes_nothing(self):
        self.assertEqual(AGENT.uninstall(self.home), [agents.Change(self.plugin, "unchanged")])
        self.assertFalse(os.path.exists(self.plugins))


class DetectTest(OpenCodeCase):
    def test_nothing_found_without_a_config_folder_or_the_command(self):
        self.assertFalse(AGENT.detect(self.home))

    def test_found_by_its_config_folder(self):
        os.makedirs(os.path.join(self.home, ".config", "opencode"))
        self.assertTrue(AGENT.detect(self.home))

    def test_found_by_the_command_on_path(self):
        path = os.path.join(self.bin, "opencode")
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(path, 0o755)
        self.assertTrue(AGENT.detect(self.home))


class RequestReplyTest(unittest.TestCase):
    def test_request_takes_the_message_and_session_the_plugin_sends(self):
        self.assertEqual(AGENT.request({"session_id": "ses_1", "message": "where did the launch move?"}),
                         ("where did the launch move?", [], "ses_1"))

    def test_request_stays_out_of_an_empty_message(self):
        self.assertIsNone(AGENT.request({"session_id": "ses_1", "message": "  \n"}))
        self.assertIsNone(AGENT.request({}))

    def test_reply_is_the_records_as_they_are(self):
        self.assertEqual(AGENT.reply("[superhistory] a record\nline two"), "[superhistory] a record\nline two")


@unittest.skipUnless(JS_RUNTIME, "needs bun or node to run the plugin")
class PluginTest(OpenCodeCase):
    def setUp(self):
        super().setUp()
        self.log = os.path.join(self.tmp.name, "calls.jsonl")
        self.driver = os.path.join(self.tmp.name, "driver.mjs")
        with open(self.driver, "w", encoding="utf-8") as fh:
            fh.write(DRIVER)
        AGENT.install(self.home, self.command())

    def chat(self, parts, session="ses_main", events=(), **fake):
        env = {"FAKE_SUPERHISTORY_LOG": self.log, **{f"FAKE_SUPERHISTORY_{k.upper()}": str(v) for k, v in fake.items()}}
        proc = subprocess.run([JS_RUNTIME, self.driver, self.plugin, json.dumps(list(events)),
                               json.dumps({"sessionID": session}), json.dumps({"message": {}, "parts": parts})],
                              capture_output=True, text=True, timeout=60, env={**os.environ, **env})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]

    def test_the_records_are_appended_to_the_users_text(self):
        parts = [{"type": "text", "text": "What date did the launch move to?"},
                 {"type": "file", "url": "file:///notes.md"},
                 {"type": "text", "text": "file contents", "synthetic": True}]
        got = self.chat(parts, output="[superhistory] the launch moved to November 14\n")
        self.assertEqual(got["exports"], ["KbContext"])
        self.assertEqual(got["parts"][0]["text"],
                         "What date did the launch move to?\n\n[superhistory] the launch moved to November 14")
        self.assertEqual(got["parts"][1:], parts[1:])
        self.assertEqual(self.calls(), [{"argv": ["hook", "opencode"],
                                         "stdin": {"session_id": "ses_main",
                                                   "message": "What date did the launch move to?"}}])

    def test_every_text_part_goes_to_kb_and_the_records_follow_the_last(self):
        got = self.chat([{"type": "text", "text": "first"}, {"type": "text", "text": "second"}], output="REC")
        self.assertEqual([p["text"] for p in got["parts"]], ["first", "second\n\nREC"])
        self.assertEqual(self.calls()[0]["stdin"]["message"], "first\nsecond")

    def test_no_output_leaves_the_message_as_it_is(self):
        got = self.chat([{"type": "text", "text": "hello"}], output="")
        self.assertEqual(got["parts"], [{"type": "text", "text": "hello"}])
        self.assertEqual(len(self.calls()), 1)

    def test_a_message_without_text_does_not_run_kb(self):
        got = self.chat([{"type": "file", "url": "file:///a.png"}], output="REC")
        self.assertEqual(got["parts"], [{"type": "file", "url": "file:///a.png"}])
        self.assertEqual(self.calls(), [])

    def test_a_child_session_does_not_run_kb(self):
        created = {"type": "session.created", "properties": {"info": {"id": "ses_child", "parentID": "ses_main"}}}
        got = self.chat([{"type": "text", "text": "subagent task"}], session="ses_child", events=[created],
                        output="REC")
        self.assertEqual(got["parts"], [{"type": "text", "text": "subagent task"}])
        self.assertEqual(self.calls(), [])
        got = self.chat([{"type": "text", "text": "user message"}], events=[created], output="REC")
        self.assertEqual(got["parts"][0]["text"], "user message\n\nREC")

    def test_a_failing_kb_leaves_the_message_as_it_is(self):
        got = self.chat([{"type": "text", "text": "hello"}], output="partial", exit=3)
        self.assertEqual(got["parts"], [{"type": "text", "text": "hello"}])

    def test_a_missing_kb_leaves_the_message_as_it_is(self):
        os.remove(self.kb)
        got = self.chat([{"type": "text", "text": "hello"}], output="REC")
        self.assertEqual(got["parts"], [{"type": "text", "text": "hello"}])

    def test_a_kb_that_runs_past_the_hook_timeout_is_cut_off(self):
        with mock.patch.object(agents, "HOOK_TIMEOUT", 1):
            AGENT.install(self.home, self.command())
        started = time.monotonic()
        got = self.chat([{"type": "text", "text": "hello"}], output="late", sleep=20)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(got["parts"], [{"type": "text", "text": "hello"}])


if __name__ == "__main__":
    unittest.main()
