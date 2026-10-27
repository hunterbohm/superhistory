import importlib.util
import json
import os
import shlex
import stat
import tempfile
import unittest
from unittest import mock

from kb import agents
from kb.agents import hermes

FAKE_KB = """#!/bin/sh
PATH=/usr/bin:/bin
printf '%s\\n' "$@" > "$0.argv"
cat > "$0.stdin"
cat "$0.answer" 2>/dev/null
exit $(cat "$0.exit" 2>/dev/null || echo 0)
"""
FAKE_HERMES = """#!/bin/sh
PATH=/usr/bin:/bin
printf '%s\\n' "$@" >> "$0.calls"
exit $(cat "$0.exit" 2>/dev/null || echo 0)
"""


def script(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


def put(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def get(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return default


class HermesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        self.folder = os.path.join(self.home, ".hermes", "plugins", "kb-context")
        self.init = os.path.join(self.folder, "__init__.py")
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bin)
        self.kb = script(os.path.join(self.tmp.name, "kb"), FAKE_KB)
        self.command = agents.command(self.kb, "hermes")
        patch = mock.patch.dict(os.environ, {"PATH": self.bin})
        patch.start()
        self.addCleanup(patch.stop)

    def fake_hermes(self):
        return script(os.path.join(self.bin, "hermes"), FAKE_HERMES)

    def files(self):
        return sorted(os.listdir(self.folder)) if os.path.isdir(self.folder) else []

    def backups(self):
        return [f for f in self.files() if ".bak-kb-" in f]

    def plugin(self):
        spec = importlib.util.spec_from_file_location("kb_context_plugin_under_test", self.init)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        hooks = {}

        class Ctx:
            def register_hook(self, name, fn):
                hooks[name] = fn

        module.register(Ctx())
        return module, hooks["pre_llm_call"]


class PluginFileTest(HermesCase):
    def setUp(self):
        super().setUp()
        hermes.AGENT.install(self.home, self.command)
        self.module, self.hook = self.plugin()

    def call(self, answer="records\n", exit_code=None, **kwargs):
        put(self.kb + ".answer", answer)
        if exit_code is not None:
            put(self.kb + ".exit", str(exit_code))
        payload = {"session_id": "20261019_101500_abc123", "user_message": "what did Ana say about the lease",
                   "platform": "discord", "parent_session_id": "", "is_first_turn": False, "model": "some-model",
                   "conversation_history": [], "task_id": "t", **kwargs}
        return self.hook(**payload)

    def sent(self):
        stdin = get(self.kb + ".stdin")
        return None if stdin is None else (get(self.kb + ".argv").split(), json.loads(stdin))

    def test_the_hook_sends_its_arguments_to_kb_and_returns_the_answer_as_context(self):
        self.assertEqual(self.call(), {"context": "records"})
        argv, stdin = self.sent()
        self.assertEqual(argv, ["hook", "hermes"])
        self.assertEqual(stdin, {"session_id": "20261019_101500_abc123", "user_message": "what did Ana say about the lease",
                                 "platform": "discord", "parent_session_id": "", "conversation_history": []})

    def test_a_kb_path_with_spaces_still_runs(self):
        folder = os.path.join(self.tmp.name, "my tools")
        os.makedirs(folder)
        spaced = script(os.path.join(folder, "kb"), FAKE_KB)
        hermes.AGENT.install(self.home, agents.command(spaced, "hermes"))
        _, self.hook = self.plugin()
        put(spaced + ".answer", "from a spaced path")
        self.assertEqual(self.hook(session_id="s", user_message="hello", platform="cli"),
                         {"context": "from a spaced path"})
        self.assertEqual(get(spaced + ".argv").split(), ["hook", "hermes"])

    def test_a_kb_path_with_an_emoji_or_a_placeholder_still_runs(self):
        for name in ("tools-🙂", "tools-@TIMEOUT@"):
            folder = os.path.join(self.tmp.name, name)
            os.makedirs(folder)
            kb = script(os.path.join(folder, "kb"), FAKE_KB)
            hermes.AGENT.install(self.home, agents.command(kb, "hermes"))
            _, self.hook = self.plugin()
            put(kb + ".answer", f"from {name}")
            self.assertEqual(self.hook(session_id="s", user_message="hello", platform="cli"),
                             {"context": f"from {name}"})

    def test_the_whole_history_goes_to_kb_which_picks_the_turns(self):
        history = [{"role": "user", "content": "draft the note to Ana"}, {"role": "assistant", "content": "Which items?"}]
        self.call(user_message="all of them", conversation_history=history)
        self.assertEqual(self.sent()[1]["conversation_history"], history)

    def test_nothing_printed_a_failure_or_a_missing_kb_returns_none(self):
        self.assertIsNone(self.call(answer=""))
        self.assertIsNone(self.call(answer="ssh: oops\n", exit_code=255))
        os.remove(self.kb)
        self.assertIsNone(self.call())

    def test_history_that_cannot_be_encoded_returns_none_not_an_error(self):
        put(self.kb + ".answer", "records")
        self.assertEqual(self.hook(session_id="s", user_message="hi", platform="cli", conversation_history=[object()]),
                         {"context": "records"})

    def test_a_check_that_hangs_is_cut_off_at_the_timeout(self):
        script(self.kb, "#!/bin/sh\nPATH=/usr/bin:/bin\nsleep 5\necho late\n")
        self.module.TIMEOUT = 0.3
        self.assertIsNone(self.call())

    def test_the_plugin_waits_as_long_as_every_hook_does(self):
        self.assertEqual(self.module.TIMEOUT, agents.HOOK_TIMEOUT)

    def test_it_registers_one_pre_llm_call_hook(self):
        self.assertTrue(callable(self.hook))


class RequestTest(unittest.TestCase):
    def event(self, **kw):
        return {"session_id": "s1", "user_message": "what did Ana say", "platform": "discord", "parent_session_id": "",
                "conversation_history": [], **kw}

    def test_a_message_on_a_talk_platform(self):
        for platform in ("discord", "desktop", "tui", "cli", "acp", "imessage", "photon", "bluebubbles", "telegram",
                         "api_server", "Discord"):
            with self.subTest(platform):
                self.assertEqual(hermes.request(self.event(platform=platform)), ("what did Ana say", [], "s1"))

    def test_background_turns_and_subagents_send_nothing(self):
        for kw in ({"platform": "cron"}, {"platform": "kanban"}, {"platform": "webhook"}, {"platform": ""},
                   {"platform": None}, {"parent_session_id": "20261019_100000_parent"}, {"user_message": "  "},
                   {"user_message": None}):
            with self.subTest(**kw):
                self.assertIsNone(hermes.request(self.event(**kw)))

    def test_the_two_turns_before_the_message_merge_same_role_runs_and_skip_the_rest(self):
        history = [{"role": "system", "content": "soul"}, {"role": "user", "content": "draft the note to Ana"},
                   {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]}, {"role": "tool", "content": "ok"},
                   {"role": "assistant", "content": [{"type": "text", "text": "Which items"}]},
                   {"role": "assistant", "content": "should it cover?"},
                   {"role": "user", "content": "all of them"}]
        self.assertEqual(hermes.request(self.event(user_message="all of them", conversation_history=history))[1],
                         [{"role": "user", "text": "draft the note to Ana"},
                          {"role": "assistant", "text": "Which items\nshould it cover?"}])

    def test_only_the_last_two_turns_and_clipped_long_ones(self):
        history = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "older reply"},
                   {"role": "user", "content": "u" * 900}, {"role": "assistant", "content": "a" * 2000},
                   {"role": "user", "content": "now"}]
        _, before, _ = hermes.request(self.event(user_message="now", conversation_history=history))
        self.assertEqual([t["role"] for t in before], ["user", "assistant"])
        self.assertEqual(len(before[0]["text"]), 600)
        self.assertEqual(len(before[1]["text"]), 1500)

    def test_content_parts_are_read_as_text(self):
        parts = [{"type": "text", "text": "what did Ana"}, {"type": "image_url", "image_url": {"url": "x"}},
                 {"type": "text", "text": "say"}]
        self.assertEqual(hermes.request(self.event(user_message=parts))[0], "what did Ana say")

    def test_reply_is_the_text_as_is(self):
        self.assertEqual(hermes.reply("a\nb"), "a\nb")

    def test_it_is_a_registered_agent(self):
        self.assertIn("hermes", agents.names())
        self.assertIs(agents.load("hermes"), hermes.AGENT)


class InstallTest(HermesCase):
    def install(self, command=None):
        return hermes.AGENT.install(self.home, command or self.command)

    def test_detect_and_installed(self):
        self.assertFalse(hermes.AGENT.detect(self.home))
        self.assertFalse(hermes.AGENT.installed(self.home))
        os.makedirs(os.path.join(self.home, ".hermes"))
        self.assertTrue(hermes.AGENT.detect(self.home))
        self.assertFalse(hermes.AGENT.installed(self.home))
        self.install()
        self.assertTrue(hermes.AGENT.installed(self.home))

    def test_install_writes_the_plugin_folder(self):
        changes = self.install()
        self.assertEqual([(c.path, c.action) for c in changes],
                         [(self.init, "added"), (os.path.join(self.folder, "plugin.yaml"), "added")])
        self.assertEqual(self.files(), ["__init__.py", "plugin.yaml"])
        self.assertIn("name: kb-context", get(os.path.join(self.folder, "plugin.yaml")))
        self.assertIn("pre_llm_call", get(os.path.join(self.folder, "plugin.yaml")))
        self.assertIn(shlex.quote(self.kb), get(self.init))

    def test_install_twice_changes_nothing_and_leaves_no_backup(self):
        self.install()
        before = {f: get(os.path.join(self.folder, f)) for f in self.files()}
        changes = self.install()
        self.assertEqual({c.action for c in changes}, {"unchanged"})
        self.assertEqual({f: get(os.path.join(self.folder, f)) for f in self.files()}, before)
        self.assertEqual(self.backups(), [])

    def test_install_over_a_plugin_for_another_kb_path_updates_it_and_backs_it_up(self):
        old = agents.command("/old/place/kb", "hermes")
        self.install(old)
        old_text = get(self.init)
        changes = self.install()
        self.assertEqual([c.action for c in changes], ["updated", "unchanged"])
        self.assertNotIn("/old/place/kb", get(self.init))
        self.assertIn(self.kb, get(self.init))
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(get(os.path.join(self.folder, backups[0])), old_text)
        self.assertEqual(self.plugin()[0].COMMAND, self.command)

    def test_a_foreign_plugin_with_an_empty_init_is_left_alone(self):
        os.makedirs(self.folder)
        yaml = "name: kb-context\nversion: \"99\"\nprovides_hooks: []\n"
        put(self.init, "")
        put(os.path.join(self.folder, "plugin.yaml"), yaml)
        changes = hermes.AGENT.install(self.home, self.command)
        self.assertEqual([c.action for c in changes], ["skipped"])
        self.assertEqual((get(self.init), get(os.path.join(self.folder, "plugin.yaml"))), ("", yaml))

    def test_uninstall_keeps_files_kb_did_not_write(self):
        hermes.AGENT.install(self.home, self.command)
        put(os.path.join(self.folder, "notes.txt"), "mine")
        hermes.AGENT.uninstall(self.home)
        self.assertEqual(sorted(os.listdir(self.folder)), ["notes.txt"])
        self.assertFalse(hermes.AGENT.installed(self.home))

    def test_install_leaves_a_foreign_plugin_of_the_same_name_alone(self):
        os.makedirs(self.folder)
        put(self.init, "# someone else's plugin\n")
        changes = self.install()
        self.assertEqual([c.action for c in changes], ["skipped"])
        self.assertEqual(get(self.init), "# someone else's plugin\n")
        self.assertEqual(self.files(), ["__init__.py"])
        self.assertFalse(hermes.AGENT.installed(self.home))

    def test_uninstall_removes_the_folder(self):
        self.install()
        changes = hermes.AGENT.uninstall(self.home)
        self.assertEqual([(c.path, c.action) for c in changes], [(self.folder, "removed")])
        self.assertFalse(os.path.exists(self.folder))
        self.assertFalse(hermes.AGENT.installed(self.home))
        self.assertEqual([c.action for c in hermes.AGENT.uninstall(self.home)], ["unchanged"])

    def test_uninstall_leaves_a_foreign_plugin_of_the_same_name_alone(self):
        os.makedirs(self.folder)
        put(self.init, "# someone else's plugin\n")
        put(os.path.join(self.folder, "plugin.yaml"), "name: kb-context\n")
        self.fake_hermes()
        changes = hermes.AGENT.uninstall(self.home)
        self.assertEqual([c.action for c in changes], ["skipped"])
        self.assertEqual(get(self.init), "# someone else's plugin\n")
        self.assertEqual(self.files(), ["__init__.py", "plugin.yaml"])
        self.assertIsNone(get(os.path.join(self.bin, "hermes.calls")))

    def test_uninstall_with_nothing_there_changes_nothing(self):
        self.assertEqual([c.action for c in hermes.AGENT.uninstall(self.home)], ["unchanged"])
        self.assertFalse(os.path.exists(os.path.join(self.home, ".hermes")))


class HermesBinaryTest(HermesCase):
    def calls(self):
        return (get(os.path.join(self.bin, "hermes.calls")) or "").split()

    def test_install_enables_the_plugin_and_says_hermes_picks_it_up_on_its_next_start(self):
        self.fake_hermes()
        changes = hermes.AGENT.install(self.home, self.command)
        self.assertEqual(self.calls(), ["plugins", "enable", "kb-context"])
        self.assertIn("next start", changes[-1].note)
        self.assertIn("enabled", changes[-1].note)

    def test_install_without_hermes_on_path_says_how_to_enable_it(self):
        changes = hermes.AGENT.install(self.home, self.command)
        note = changes[-1].note
        self.assertIn("hermes plugins enable kb-context", note)
        self.assertIn("next start", note)
        self.assertTrue(os.path.exists(self.init))

    def test_a_failing_enable_is_reported_but_the_plugin_stays_written(self):
        self.fake_hermes()
        put(os.path.join(self.bin, "hermes.exit"), "3")
        changes = hermes.AGENT.install(self.home, self.command)
        self.assertIn("failed", changes[-1].note)
        self.assertTrue(os.path.exists(self.init))

    def test_uninstall_disables_the_plugin_before_removing_it(self):
        hermes.AGENT.install(self.home, self.command)
        self.fake_hermes()
        hermes.AGENT.uninstall(self.home)
        self.assertEqual(self.calls(), ["plugins", "disable", "kb-context"])
        self.assertFalse(os.path.exists(self.folder))

    def test_nothing_restarts_hermes(self):
        self.fake_hermes()
        hermes.AGENT.install(self.home, self.command)
        hermes.AGENT.uninstall(self.home)
        self.assertEqual(self.calls(), ["plugins", "enable", "kb-context", "plugins", "disable", "kb-context"])


if __name__ == "__main__":
    unittest.main()
