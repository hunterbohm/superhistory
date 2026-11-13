import contextlib
import io
import os
import stat
import tempfile
import unittest
from unittest import mock

from kb import cli
from kb.sources import gmail, plugin
from tests.fake_http import FakeServer
from tests.test_calendar_plugin import event
from tests.test_notion_plugin import TOKEN, page, para


class CliCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="bundled-")
        self.addCleanup(tmp.cleanup)
        self.home = os.path.join(tmp.name, "home")
        os.makedirs(self.home)
        env = mock.patch.dict(os.environ, {"SUPERHISTORY_HOME": self.home})
        env.start()
        self.addCleanup(env.stop)

    def write_config(self, text):
        with open(os.path.join(self.home, "config.toml"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def key(self, name, value):
        os.makedirs(os.path.join(self.home, "keys"), exist_ok=True)
        path = os.path.join(self.home, "keys", name)
        with open(path, "w") as fh:
            fh.write(value + "\n")
        return path

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class InstallTest(CliCase):
    def test_install_copies_the_plug_in_as_the_users_own_runnable_file_and_prints_its_config(self):
        code, out, _ = self.run_cli("plugins", "install", "google-calendar")
        self.assertEqual(code, 0)
        path = os.path.join(self.home, "plugins", "google-calendar")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)
        self.assertEqual(plugin.executable(os.path.join(self.home, "plugins"), "google-calendar"), path)
        self.assertIn('[sources.calendar]', out)
        self.assertIn('plugin = "google-calendar"', out)
        self.assertIn("calendar.readonly", out)
        with open(path) as fh:
            self.assertEqual(fh.readline().strip(), f"#!{os.sys.executable}")

    def test_install_never_replaces_a_file_that_is_there_unless_told_to(self):
        self.run_cli("plugins", "install", "notion")
        path = os.path.join(self.home, "plugins", "notion")
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\necho mine\n")
        code, _, err = self.run_cli("plugins", "install", "notion")
        self.assertEqual(code, 1)
        self.assertIn("--force", err)
        with open(path) as fh:
            self.assertIn("echo mine", fh.read())
        code, _, _ = self.run_cli("plugins", "install", "notion", "--force")
        self.assertEqual(code, 0)
        with open(path) as fh:
            self.assertIn("Notion pages", fh.read())

    def test_install_refuses_a_name_that_is_not_bundled(self):
        code, _, err = self.run_cli("plugins", "install", "../notion")
        self.assertEqual(code, 1)
        self.assertIn("google-calendar", err)
        code, _, err = self.run_cli("plugins", "install")
        self.assertEqual(code, 1)
        self.assertIn("notion", err)

    def test_install_does_not_write_through_a_link(self):
        os.makedirs(os.path.join(self.home, "plugins"), mode=0o700)
        target = os.path.join(self.home, "elsewhere")
        os.symlink(target, os.path.join(self.home, "plugins", "notion"))
        code, _, _ = self.run_cli("plugins", "install", "notion")
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(target))

    def test_list_names_the_bundled_plug_ins_not_yet_installed(self):
        _, out, _ = self.run_cli("plugins", "list")
        self.assertIn("Bundled, not installed: google-calendar, notion", out)
        self.run_cli("plugins", "install", "notion")
        _, out, _ = self.run_cli("plugins", "list")
        self.assertIn("Bundled, not installed: google-calendar.", out)
        self.assertIn("notion", out.split("Bundled")[0])


class LoginTest(CliCase):
    CONFIG = ('[sources.calendar]\ntype = "plugin"\nplugin = "google-calendar"\n'
              '[sources.calendar.settings]\nclient_id = "id-1.apps.example.com"\ngoogle_scope = "%s"\n'
              '[sources.calendar.secrets]\nclient_secret = "file:%s"\n')

    def test_login_signs_a_calendar_source_in_with_the_read_only_scope_and_saves_the_token(self):
        secret = self.key("google.client-secret", "shh")
        self.write_config(self.CONFIG % ("https://www.googleapis.com/auth/calendar.readonly", secret))
        calls = []
        with mock.patch.object(gmail, "oauth_login",
                               lambda cid, sec, scope: calls.append((cid, sec, scope)) or "token-123"):
            code, out, _ = self.run_cli("login", "calendar")
        self.assertEqual(code, 0)
        self.assertEqual(calls, [("id-1.apps.example.com", "shh", "https://www.googleapis.com/auth/calendar.readonly")])
        key = os.path.join(self.home, "keys", "calendar.refresh-token")
        with open(key) as fh:
            self.assertEqual(fh.read().strip(), "token-123")
        self.assertEqual(stat.S_IMODE(os.stat(key).st_mode), 0o600)
        self.assertIn(f'under [sources.calendar.secrets]:\n\nrefresh_token = "file:{key}"', out)
        self.assertNotIn("token-123", out)

    def test_the_sign_in_page_asks_google_for_the_scope_it_is_given(self):
        seen = []

        def browser(url):
            seen.append(url)
            raise RuntimeError("stop here")

        with self.assertRaises(RuntimeError):
            gmail.oauth_login("id", "secret", open_browser=browser, timeout=1,
                              scope="https://www.googleapis.com/auth/calendar.readonly")
        self.assertIn("scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcalendar.readonly", seen[0])
        with self.assertRaises(RuntimeError):
            gmail.oauth_login("id", "secret", open_browser=browser, timeout=1)
        self.assertIn("scope=https%3A%2F%2Fmail.google.com%2F", seen[1])

    def test_login_refuses_a_scope_that_can_write(self):
        secret = self.key("google.client-secret", "shh")
        self.write_config(self.CONFIG % ("https://www.googleapis.com/auth/calendar", secret))
        with mock.patch.object(gmail, "oauth_login", side_effect=AssertionError("must not sign in")):
            code, _, err = self.run_cli("login", "calendar")
        self.assertEqual(code, 1)
        self.assertIn("read-only", err)

    def test_login_refuses_a_plug_in_that_names_no_google_scope(self):
        self.write_config('[sources.notion]\ntype = "plugin"\nplugin = "notion"\n')
        code, _, err = self.run_cli("login", "notion")
        self.assertEqual(code, 1)
        self.assertIn("google_scope", err)


class EndToEndTest(CliCase):
    def test_installed_calendar_and_notion_plug_ins_ingest_and_are_found_by_search(self):
        def answer(seen):
            if seen.path == "/token":
                return 200, {"access_token": "access-1"}
            if seen.path.endswith("/events"):
                return 200, {"items": [event("e1", "Zebrafish kickoff", attendees=[{"email": "kim@example.com"}]),
                                       {"id": "e0", "status": "cancelled", "updated": "2026-11-02T10:00:00.000Z"}]}
            if seen.path == "/search":
                return 200, {"results": [page("n1", "Narwhal budget", "2026-11-05T10:00:00.000Z")], "has_more": False}
            return 200, {"results": [para("the quarterly quokka plan")], "has_more": False}

        with FakeServer(answer) as server:
            for name in ("google-calendar", "notion"):
                self.assertEqual(self.run_cli("plugins", "install", name)[0], 0)
            self.write_config(
                'machine = "m1"\n'
                '[sources.calendar]\ntype = "plugin"\nplugin = "google-calendar"\n'
                f'[sources.calendar.settings]\nclient_id = "id-1"\nbase_url = "{server.url}"\n'
                f'token_url = "{server.url}/token"\n'
                f'[sources.calendar.secrets]\nclient_secret = "file:{self.key("g.secret", "cs")}"\n'
                f'refresh_token = "file:{self.key("g.refresh", "rt")}"\n'
                '[sources.notion]\ntype = "plugin"\nplugin = "notion"\n'
                f'[sources.notion.settings]\nbase_url = "{server.url}"\nmin_interval_seconds = 0\n'
                f'[sources.notion.secrets]\ntoken = "file:{self.key("notion.token", TOKEN)}"\n')
            code, _, err = self.run_cli("ingest", "--no-summaries")
            self.assertEqual(code, 0, err)
            _, out, _ = self.run_cli("search", "zebrafish")
            self.assertIn("Zebrafish kickoff", out)
            self.assertIn("calendar", out)
            self.assertIn("https://calendar.example/event/e1", out)
            _, out, _ = self.run_cli("search", "quokka")
            self.assertIn("Narwhal budget", out)
            self.assertIn("https://www.notion.so/n1", out)
            _, out, _ = self.run_cli("search", "kim@example.com", "--source", "calendar")
            self.assertIn("Zebrafish kickoff", out)
            _, out, _ = self.run_cli("stats")
            self.assertNotIn("FAILING", out)


if __name__ == "__main__":
    unittest.main()
