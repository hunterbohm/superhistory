import os
import tempfile
import unittest

from kb import config
from kb.config import ConfigError, Secret, SecretError


class ParseTest(unittest.TestCase):
    def problems(self, text):
        with self.assertRaises(ConfigError) as caught:
            config.parse(text)
        return caught.exception.problems

    def test_empty_file_gives_defaults(self):
        c = config.parse("")
        self.assertEqual(c.machine, "local")
        self.assertIsNone(c.ai)
        self.assertEqual(c.sources, ())
        self.assertTrue(c.check.enabled)
        self.assertEqual(c.summarize.per_run, 150)
        self.assertEqual(c.schedule.every_minutes, 15)

    def test_missing_file_gives_defaults(self):
        self.assertEqual(config.load("/nonexistent/kb/config.toml"), config.Config())

    def test_a_source_type_defaults_to_its_table_name(self):
        c = config.parse('[sources.notes]\npath = "~/Notes"\n')
        source = c.source("notes")
        self.assertEqual(source.type_name, "notes")
        self.assertEqual(source.options.path, "~/Notes")
        self.assertEqual(source.options.extensions, (".md",))

    def test_several_instances_of_one_type(self):
        c = config.parse('[sources.notes]\npath = "~/Notes"\n'
                         '[sources.client-notes]\ntype = "notes"\npath = "~/clients"\nextensions = [".md", ".txt"]\n')
        self.assertEqual([s.name for s in c.sources], ["notes", "client-notes"])
        self.assertEqual(c.source("client-notes").options.extensions, (".md", ".txt"))

    def test_excludes_add_to_the_type_defaults_unless_dropped(self):
        c = config.parse('[sources.a]\ntype = "notes"\npath = "/a"\nexclude = ["Journal"]\n'
                         '[sources.b]\ntype = "notes"\npath = "/b"\nexclude = ["Journal"]\nexclude_defaults = false\n')
        self.assertEqual(c.source("a").exclude[-1], "Journal")
        self.assertIn(".*", c.source("a").exclude)
        self.assertEqual(c.source("b").exclude, ("Journal",))

    def test_summarize_takes_a_flag_or_globs_and_defaults_to_the_type(self):
        c = config.parse('[sources.a]\ntype = "notes"\npath = "/a"\n'
                         '[sources.b]\ntype = "notes"\npath = "/b"\nsummarize = true\n'
                         '[sources.c]\ntype = "notes"\npath = "/c"\nsummarize = ["Meetings/**"]\n')
        self.assertFalse(c.source("a").summarize.covers("x.md"))
        self.assertTrue(c.source("b").summarize.covers("x.md"))
        self.assertTrue(c.source("c").summarize.covers("Meetings/2026/standup.md"))
        self.assertFalse(c.source("c").summarize.covers("Ideas/standup.md"))

    def test_a_disabled_source_is_left_out(self):
        c = config.parse('[sources.notes]\npath = "/n"\nenabled = false\n')
        self.assertEqual(c.sources, ())

    def test_every_problem_is_reported_with_its_key(self):
        problems = self.problems('machine = "Not Valid"\nextra = 1\n[check]\nbudget_seconds = 99\n'
                                 '[sources.notes]\npth = "/n"\n[sources.mail]\ntype = "nope"\n'
                                 '[ai]\nplugin = "nope"\n')
        joined = "\n".join(problems)
        for expected in ("machine:", "extra: unknown key", "check.budget_seconds", "sources.notes.pth: unknown key",
                         "sources.notes.path: required", "sources.mail.type", "ai.plugin"):
            self.assertIn(expected, joined)

    def test_wrong_value_types_are_problems(self):
        problems = self.problems('[sources.notes]\npath = 3\nextensions = ".md"\n[summarize]\nper_run = true\n')
        joined = "\n".join(problems)
        self.assertIn("sources.notes.path: expected text", joined)
        self.assertIn("sources.notes.extensions: expected a list of texts", joined)
        self.assertIn("summarize.per_run: expected a whole number", joined)

    def test_a_literal_option_lists_its_choices(self):
        problems = self.problems('[sources.notes]\npath = "/n"\nlinks = "web"\n')
        self.assertIn("'auto'", problems[0])

    def test_invalid_toml_is_one_problem(self):
        self.assertIn("not valid TOML", self.problems("[sources\n")[0])

    def test_ai_table_binds_the_plugin_options(self):
        c = config.parse('[ai]\nplugin = "anthropic-compatible"\nbase_url = "http://127.0.0.1:9"\n'
                         'key = "env:SOME_KEY"\nmodel = "small"\n')
        self.assertEqual(c.ai.plugin, "anthropic-compatible")
        self.assertEqual(c.ai.options.base_url, "http://127.0.0.1:9")
        self.assertEqual(c.ai.options.key, Secret("env:SOME_KEY"))
        self.assertEqual(c.ai.options.auth, "bearer")

    def test_ai_keys_unknown_to_the_plugin_are_problems(self):
        self.assertIn("ai.base_ur: unknown key", self.problems('[ai]\nplugin = "anthropic"\nbase_ur = "x"\n'))

    def test_a_bad_secret_reference_is_a_problem(self):
        self.assertIn("ai.key", self.problems('[ai]\nplugin = "anthropic"\nkey = "sk-plain-value"\n')[0])

    def test_units_cover_every_source_and_the_summarizer(self):
        c = config.parse('machine = "studio"\n[sources.notes]\npath = "/n"\n')
        self.assertEqual(c.units(), ("notes@studio", "summarize"))


class SecretTest(unittest.TestCase):
    def test_env_file_and_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "key")
            with open(path, "w") as fh:
                fh.write("from-file\n")
            os.environ["KB_TEST_SECRET"] = "from-env"
            self.addCleanup(os.environ.pop, "KB_TEST_SECRET", None)
            self.assertEqual(Secret("env:KB_TEST_SECRET").reveal(), "from-env")
            self.assertEqual(Secret(f"file:{path}").reveal(), "from-file")
            self.assertEqual(Secret("cmd:echo from-command").reveal(), "from-command")

    def test_unreadable_or_empty_secrets_raise(self):
        for ref in ("env:KB_TEST_UNSET_SECRET", "file:/nonexistent/key", "cmd:exit 3", "cmd:true"):
            with self.subTest(ref), self.assertRaises(SecretError):
                Secret(ref).reveal()

    def test_repr_never_shows_the_value(self):
        os.environ["KB_TEST_SECRET"] = "hidden-value"
        self.addCleanup(os.environ.pop, "KB_TEST_SECRET", None)
        self.assertNotIn("hidden-value", repr(Secret("env:KB_TEST_SECRET")))


if __name__ == "__main__":
    unittest.main()
