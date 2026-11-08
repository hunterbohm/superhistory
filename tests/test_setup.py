import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kb import agents, ai, config, setup
from kb.ai import cli as ai_cli
from kb.paths import Paths

CLAUDE_BIN = "/opt/fake/bin/claude"
CODEX_BIN = "/opt/fake/bin/codex"


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def api_plugins():
    cli_names = {spec.name for spec in ai_cli.SPECS}
    return [name for name in ai.visible_plugins() if name not in cli_names]


class SetupCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.paths = Paths.under(self.root / "superhistory")
        for folder in (".claude/projects", ".codex/sessions", "Notes/Journal", "Notes/Work"):
            (self.home / folder).mkdir(parents=True)
        host = mock.patch("kb.setup.socket.gethostname", return_value="Sams-Laptop.example.com")
        host.start()
        self.addCleanup(host.stop)

    def run_setup(self, answers, secrets=(), platform="linux", binaries=None):
        answers, secrets, asked, bins = list(answers), list(secrets), [], binaries or {}

        def ask(question, default):
            asked.append((question, default))
            if not answers:
                raise AssertionError(f"unexpected question: {question}")
            return answers.pop(0)

        def secret(question):
            asked.append((question, None))
            if not secrets:
                raise AssertionError(f"unexpected secret question: {question}")
            return secrets.pop(0)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = setup.run(self.paths, ask, secret, home=str(self.home), platform=platform, which=bins.get)
        self.assertEqual(answers, [], "answers were left over, so a question was not asked")
        self.assertEqual(secrets, [], "secrets were left over, so a secret was not asked")
        return result, out.getvalue(), asked

    def load(self):
        return config.load(str(self.paths.config_file))

    def sources(self):
        return {s.name: s for s in self.load().sources}

    def asked_about(self, asked, text):
        return [q for q, _ in asked if text in q]


class HappyPathTest(SetupCase):
    ANSWERS = ["sam-laptop", "yes", "yes", "~/Notes", "Journal, Work", "sam@example.com", "anthropic", "yes", "yes", "no"]
    SECRETS = ["abcd efgh ijkl mnop", "sk-test-key-123"]

    def test_the_file_matches_the_answers(self):
        result, _, _ = self.run_setup(self.ANSWERS, self.SECRETS)
        cfg = self.load()
        self.assertEqual(cfg.machine, "sam-laptop")
        self.assertEqual(sorted(s.name for s in cfg.sources), ["claude_code", "codex", "gmail", "notes"])
        notes = cfg.source("notes")
        self.assertEqual(notes.options.path, "~/Notes")
        self.assertTrue({"Journal", "Work"} <= set(notes.exclude))
        self.assertEqual(cfg.source("gmail").options.address, "sam@example.com")
        self.assertEqual(cfg.ai.plugin, "anthropic")
        self.assertEqual(cfg.schedule.every_minutes, 15)
        self.assertEqual(result, setup.SetupResult(True, True, False, self.paths.config_file))

    def test_secrets_sit_in_private_files_that_the_config_points_at(self):
        _, output, _ = self.run_setup(self.ANSWERS, self.SECRETS)
        cfg = self.load()
        password_file = self.paths.keys_dir / "gmail.password"
        key_file = self.paths.keys_dir / "anthropic.key"
        self.assertEqual(mode(self.paths.keys_dir), 0o700)
        self.assertEqual(mode(password_file), 0o600)
        self.assertEqual(mode(key_file), 0o600)
        self.assertEqual(cfg.source("gmail").options.password.ref, f"file:{password_file}")
        self.assertEqual(cfg.source("gmail").options.password.reveal(), "abcdefghijklmnop")
        self.assertEqual(cfg.ai.options.key.ref, f"file:{key_file}")
        self.assertEqual(cfg.ai.options.key.reveal(), "sk-test-key-123")
        text = self.paths.config_file.read_text(encoding="utf-8")
        for value in ("abcdefghijklmnop", "abcd efgh", "sk-test-key-123"):
            self.assertNotIn(value, text)
            self.assertNotIn(value, output)

    def test_the_questions_come_in_the_promised_order(self):
        _, _, asked = self.run_setup(self.ANSWERS, self.SECRETS)
        expected = ["name", "Claude Code", "Codex", "notes to read", "leave out", "Gmail address", "app password",
                    "Which one", "Key for anthropic", "hook", "background", "now"]
        self.assertEqual(len(asked), len(expected))
        for (question, _), fragment in zip(asked, expected):
            self.assertIn(fragment, question)
        self.assertIn("myaccount.google.com/apppasswords", asked[6][0])
        self.assertIn("2-Step Verification", asked[6][0])
        self.assertIn("Claude Code", asked[9][0])


class DefaultsTest(SetupCase):
    def test_empty_answers_take_every_default(self):
        result, _, asked = self.run_setup([""] * 9)
        self.assertEqual(asked[0][1], "sams-laptop")
        self.assertTrue(all(default == "yes" for q, default in asked if "Read " in q or "hook" in q or "now" in q))
        cfg = self.load()
        self.assertEqual(cfg.machine, "sams-laptop")
        self.assertEqual(sorted(s.name for s in cfg.sources), ["claude_code", "codex"])
        self.assertIsNone(cfg.ai)
        self.assertEqual(result, setup.SetupResult(True, True, True, self.paths.config_file))

    def test_the_computer_name_is_cut_to_the_name_rule(self):
        with mock.patch("kb.setup.socket.gethostname", return_value="Sam's  Big MacBook Pro_" + "x" * 60 + ".local"):
            self.assertTrue(config.NAME_RE.match(setup.default_machine()))
        with mock.patch("kb.setup.socket.gethostname", return_value="..."):
            self.assertEqual(setup.default_machine(), "local")

    def test_a_name_that_is_a_secrets_word_is_asked_again(self):
        _, output, asked = self.run_setup(["password", "sam-2", "no", "no", "", "", "", "no"])
        self.assertEqual(self.load().machine, "sam-2")
        self.assertEqual(len(self.asked_about(asked, "name")), 2)
        self.assertIn("reads like a word for a secret", output)

    def test_a_name_that_breaks_the_rule_is_asked_again(self):
        _, output, asked = self.run_setup(["Bad Name!", "sam-2", "no", "no", "", "", "", "no"])
        self.assertEqual(self.load().machine, "sam-2")
        self.assertEqual(len(self.asked_about(asked, "name")), 2)
        self.assertIn("lowercase", output)


class ExistingConfigTest(SetupCase):
    OLD = 'machine = "old"\n'

    def put_old(self):
        self.paths.config_file.parent.mkdir(parents=True)
        self.paths.config_file.write_text(self.OLD, encoding="utf-8")

    def test_no_keeps_the_file_and_asks_nothing_else(self):
        self.put_old()
        result, _, asked = self.run_setup(["no"])
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0][1], "no")
        self.assertEqual(self.paths.config_file.read_text(encoding="utf-8"), self.OLD)
        self.assertEqual(os.listdir(self.paths.config_file.parent), ["config.toml"])
        self.assertEqual(result, setup.SetupResult(False, False, False, self.paths.config_file))

    def test_an_empty_answer_also_keeps_it(self):
        self.put_old()
        self.run_setup([""])
        self.assertEqual(self.paths.config_file.read_text(encoding="utf-8"), self.OLD)

    def test_yes_replaces_it_after_keeping_a_backup(self):
        self.put_old()
        self.run_setup(["yes", "sam", "no", "no", "", "", "", "no"])
        backups = [n for n in os.listdir(self.paths.config_file.parent) if n.startswith("config.toml.bak")]
        self.assertEqual(len(backups), 1)
        self.assertEqual((self.paths.config_file.parent / backups[0]).read_text(encoding="utf-8"), self.OLD)
        self.assertEqual(self.load().machine, "sam")


class NotesTest(SetupCase):
    def test_a_missing_folder_is_refused_until_a_real_one_comes(self):
        _, output, asked = self.run_setup(["sam", "no", "no", "~/Nope", str(self.home / "Notes"), "", "", "", "no", "no", "no"])
        self.assertEqual(len(self.asked_about(asked, "notes to read")), 2)
        self.assertIn("~/Nope", output)
        self.assertEqual(self.sources()["notes"].options.path, "~/Notes")
        self.assertNotIn("exclude", self.paths.config_file.read_text(encoding="utf-8"))

    def test_blank_skips_notes_and_the_leave_out_question(self):
        _, _, asked = self.run_setup(["sam", "no", "no", "", "", "", "no"])
        self.assertEqual(self.asked_about(asked, "leave out"), [])
        self.assertEqual(self.sources(), {})

    def test_a_folder_with_an_emoji_in_its_name_is_written_and_read_back(self):
        notes = self.home / "Notes" / "\U0001f4da reading"
        notes.mkdir(parents=True)
        self.run_setup(["sam", "no", "no", str(notes), "", "", "", "no", "no", "no"])
        self.assertEqual(self.sources()["notes"].options.path, "~/Notes/\U0001f4da reading")

    def test_a_folder_outside_home_is_kept_as_an_absolute_path(self):
        outside = self.root / "shared notes"
        outside.mkdir()
        self.run_setup(["sam", "no", "no", str(outside), "", "", "", "no", "no", "no"])
        self.assertEqual(self.sources()["notes"].options.path, str(outside))


class GmailTest(SetupCase):
    def test_a_blank_password_leaves_gmail_out(self):
        self.run_setup(["sam", "no", "no", "", "sam@example.com", "", "no"], [" "])
        self.assertNotIn("gmail", self.sources())
        self.assertFalse(self.paths.keys_dir.exists())

    def test_an_address_without_an_at_sign_is_asked_again(self):
        _, _, asked = self.run_setup(["sam", "no", "no", "", "sam", "", "", "no"])
        self.assertEqual(len(self.asked_about(asked, "Gmail address")), 2)


class AIChoiceTest(SetupCase):
    BASE = ["sam", "no", "no", "", ""]
    TAIL = ["no"]

    def test_none_for_now_writes_no_ai(self):
        none = str(len(api_plugins()) + 1)
        _, _, asked = self.run_setup(self.BASE + [none] + self.TAIL)
        self.assertIsNone(self.load().ai)
        self.assertNotIn("[ai]", self.paths.config_file.read_text(encoding="utf-8"))

    def test_the_list_numbers_the_api_plugins_and_ends_with_none(self):
        _, output, _ = self.run_setup(self.BASE + ["1"] + self.TAIL, ["sk-1"])
        lines = [line.strip() for line in output.splitlines() if line.strip()[:2] in ("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.")]
        names = api_plugins()
        self.assertEqual(len(lines), len(names) + 1)
        self.assertTrue(lines[0].startswith("1. " + ai.PLUGINS[names[0]].label))
        self.assertTrue(lines[-1].endswith("none for now"))

    def test_an_api_plugin_asks_for_its_key_and_saves_it(self):
        number = str(api_plugins().index("openai") + 1)
        self.run_setup(self.BASE + [number] + self.TAIL, ["sk-open"])
        cfg = self.load()
        self.assertEqual(cfg.ai.plugin, "openai")
        key_file = self.paths.keys_dir / "openai.key"
        self.assertEqual(mode(key_file), 0o600)
        self.assertEqual(cfg.ai.options.key.reveal(), "sk-open")

    def test_no_key_for_a_plugin_that_needs_one_leaves_ai_off(self):
        _, output, _ = self.run_setup(self.BASE + ["anthropic"] + self.TAIL, [""])
        self.assertIsNone(self.load().ai)
        self.assertFalse((self.paths.keys_dir / "anthropic.key").exists())
        self.assertIn("left for later", output)

    def test_an_openai_compatible_server_gets_a_base_url_and_model_and_may_skip_the_key(self):
        answers = self.BASE + ["openai-compatible", "", "http://localhost:1234/v1", "", "local-model"] + self.TAIL
        _, _, asked = self.run_setup(answers, [""])
        cfg = self.load()
        self.assertEqual((cfg.ai.plugin, cfg.ai.options.base_url, cfg.ai.options.model),
                         ("openai-compatible", "http://localhost:1234/v1", "local-model"))
        self.assertIsNone(cfg.ai.options.key)
        self.assertEqual(len(self.asked_about(asked, "Base URL")), 2)
        self.assertEqual(len(self.asked_about(asked, "Model name")), 2)

    def test_an_anthropic_compatible_server_gets_a_base_url_and_its_key(self):
        self.run_setup(self.BASE + ["anthropic-compatible", "https://llm.example.com"] + self.TAIL, ["tok"])
        cfg = self.load()
        self.assertEqual(cfg.ai.options.base_url, "https://llm.example.com")
        self.assertEqual(cfg.ai.options.key.reveal(), "tok")
        self.assertEqual(mode(self.paths.keys_dir / "anthropic-compatible.key"), 0o600)

    def test_a_cli_plugin_found_on_path_is_listed_first_and_gets_its_absolute_path(self):
        bins = {"claude": CLAUDE_BIN, "codex": CODEX_BIN}
        _, output, asked = self.run_setup(self.BASE + [""] + self.TAIL, binaries=bins)
        lines = [line for line in output.splitlines() if line.strip().startswith(("1.", "2.", "3."))]
        self.assertIn("(found)", lines[0])
        self.assertIn(ai.PLUGINS["claude-code"].label, lines[0])
        self.assertIn("(found)", lines[1])
        self.assertNotIn("(found)", lines[2])
        self.assertEqual(asked[-2][1], "1")
        cfg = self.load()
        self.assertEqual((cfg.ai.plugin, cfg.ai.options.binary), ("claude-code", CLAUDE_BIN))
        self.assertFalse(self.paths.keys_dir.exists())

    def test_choosing_the_second_cli_plugin_writes_its_binary(self):
        bins = {"claude": CLAUDE_BIN, "codex": CODEX_BIN}
        self.run_setup(self.BASE + ["2"] + self.TAIL, binaries=bins)
        cfg = self.load()
        self.assertEqual((cfg.ai.plugin, cfg.ai.options.binary), ("chatgpt", CODEX_BIN))

    def test_a_cli_that_is_not_installed_is_not_offered(self):
        _, output, _ = self.run_setup(self.BASE + ["1"] + self.TAIL, ["sk"], binaries={})
        self.assertNotIn("(found)", output)
        self.assertNotIn("claude -p", output)

    def test_an_answer_outside_the_list_is_asked_again(self):
        _, _, asked = self.run_setup(self.BASE + ["99", "abc", str(len(api_plugins()) + 1)] + self.TAIL)
        self.assertEqual(len(self.asked_about(asked, "Which one")), 3)


class FoundSourcesTest(SetupCase):
    def test_hermes_git_and_github_are_offered_when_present(self):
        (self.home / ".hermes").mkdir()
        (self.home / ".hermes" / "state.db").write_bytes(b"")
        (self.home / "code").mkdir()
        (self.home / "work repos").mkdir()
        answers = ["sam", "no", "no", "yes", "yes", "~/code, ~/work repos, ~/missing", "yes", "", "", "", "no", "no", "no"]
        _, output, asked = self.run_setup(answers, binaries={"gh": "/opt/fake/bin/gh"})
        cfg = self.load()
        self.assertEqual(sorted(s.name for s in cfg.sources), ["git", "github", "hermes"])
        self.assertEqual(cfg.source("git").options.roots, ("~/code", "~/work repos"))
        self.assertIn("~/missing", output)
        self.assertEqual([d for q, d in asked if "git projects" in q], ["~/code"])

    def test_sources_that_are_not_on_this_computer_are_not_offered(self):
        (self.home / ".claude" / "projects").rmdir()
        (self.home / ".codex" / "sessions").rmdir()
        _, _, asked = self.run_setup(["sam", "", "", "", "no"])
        self.assertEqual([q for q, _ in asked if q.startswith("Read ")], [])
        self.assertEqual(self.sources(), {})

    def test_skipping_every_source_asks_neither_about_the_schedule_nor_a_first_read(self):
        result, output, asked = self.run_setup(["sam", "no", "no", "", "", "", "no"])
        self.assertEqual(self.asked_about(asked, "background"), [])
        self.assertEqual(self.asked_about(asked, "now"), [])
        self.assertEqual(result, setup.SetupResult(False, False, False, self.paths.config_file))
        self.assertIn("No source was chosen", output)


class ImessageTest(SetupCase):
    def put_db(self):
        folder = self.home / "Library" / "Messages"
        folder.mkdir(parents=True)
        (folder / "chat.db").write_bytes(b"")
        return folder

    def answers(self, with_imessage):
        return ["sam", "no", "no"] + (["yes"] if with_imessage else []) + ["", "", "", "no"] + (["no", "no"] if with_imessage else [])

    def test_a_mac_with_the_database_is_offered_it_with_the_full_disk_access_note(self):
        self.put_db()
        _, output, asked = self.run_setup(self.answers(True), platform="darwin")
        self.assertEqual(len(self.asked_about(asked, "iMessage")), 1)
        self.assertIn("Full Disk Access", output)
        self.assertIn(os.path.realpath(sys.executable), output)
        self.assertIn("imessage", self.sources())

    def test_other_systems_are_not_offered_it_even_with_the_file_there(self):
        self.put_db()
        _, output, asked = self.run_setup(self.answers(False), platform="linux")
        self.assertEqual(self.asked_about(asked, "iMessage"), [])
        self.assertNotIn("Full Disk Access", output)
        self.assertNotIn("imessage", self.sources())

    def test_a_mac_without_the_database_is_not_offered_it(self):
        _, _, asked = self.run_setup(self.answers(False), platform="darwin")
        self.assertEqual(self.asked_about(asked, "iMessage"), [])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can read everything")
    def test_a_mac_that_denies_access_to_the_database_still_gets_the_offer(self):
        folder = self.put_db()
        folder.chmod(0)
        self.addCleanup(folder.chmod, 0o700)
        _, _, asked = self.run_setup(self.answers(True), platform="darwin")
        self.assertEqual(len(self.asked_about(asked, "iMessage")), 1)


class HooksTest(SetupCase):
    def test_the_hook_question_names_the_agents_found(self):
        _, _, asked = self.run_setup(["sam", "no", "no", "", "", "", "yes"])
        question = self.asked_about(asked, "hook")[0]
        self.assertIn("Claude Code", question)

    def test_no_agent_found_means_no_question_and_no_hook(self):
        for folder in (".claude/projects", ".claude"):
            (self.home / folder).rmdir()
        with mock.patch.object(agents, "names", lambda: ()):
            result, output, asked = self.run_setup(["sam", "yes", "", "", "", "yes", "no"])
        self.assertEqual(self.asked_about(asked, "hook"), [])
        self.assertFalse(result.hooks)
        self.assertTrue(result.schedule)
        self.assertFalse(result.ingest_now)

    def test_declining_every_next_step_is_reported(self):
        result, _, _ = self.run_setup(["sam", "yes", "yes", "", "", "", "no", "no", "no"])
        self.assertEqual(result, setup.SetupResult(False, False, False, self.paths.config_file))


class WriteTest(SetupCase):
    def test_the_file_has_a_comment_header_and_one_table_per_source(self):
        self.run_setup(["sam", "yes", "yes", "~/Notes", "", "", "", "no", "no", "no"])
        text = self.paths.config_file.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# "))
        for header in ("[sources.claude_code]", "[sources.codex]", "[sources.notes]", "[schedule]"):
            self.assertEqual(text.count(header), 1, header)

    def test_text_that_does_not_parse_stops_setup_before_anything_is_written(self):
        with mock.patch("kb.setup.Plan.text", return_value='machine = "Bad Name"\n'):
            with self.assertRaises(config.ConfigError):
                self.run_setup(["sam", "no", "no", "", "", ""])
        self.assertFalse(self.paths.config_file.exists())
        self.assertFalse(self.paths.keys_dir.exists())


if __name__ == "__main__":
    unittest.main()
