"""A planted secret must not reach exception text on any path where superhistory builds an error from text it did not write:
an AI provider's answer, a CLI's or ssh's error output, a `cmd:` secret, a remote line, Gmail and Google answers."""
import io
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

from kb import ai, check, config, ingest, remote, store
from kb.ai import api
from kb.ai import cli as ai_cli
from kb.sources import git, github, gmail

TOKEN = "sk-ant-api03-" + "Q" * 40


class MaskedErrorsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def assert_masked(self, exc):
        self.assertNotIn(TOKEN, str(exc))
        self.assertNotIn(TOKEN, repr(exc))
        self.assertIn("[masked]", str(exc))

    def program(self, name, stderr, code=1):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            fh.write(f"#!{sys.executable}\nimport sys\nsys.stderr.write({stderr!r})\nsys.exit({code})\n")
        os.chmod(path, 0o755)
        return path

    def test_an_ai_providers_error_answer(self):
        body = io.BytesIO(f'{{"error": "invalid x-api-key {TOKEN}"}}'.encode())
        failure = urllib.error.HTTPError("https://api.example.com/v1/messages", 401, "Unauthorized", {}, body)
        with mock.patch.object(api.urllib.request, "urlopen", side_effect=failure):
            with self.assertRaises(ai.AIError) as caught:
                api.post("https://api.example.com/v1/messages", {}, {}, 5)
        self.assert_masked(caught.exception)

    def test_an_ai_clis_error_output(self):
        call = ai_cli.Call([self.program("model-cli", f"auth failed for {TOKEN}\n")], "", {}, None)
        with self.assertRaises(ai.AIError) as caught:
            ai_cli.run(call, self.tmp, 10, "model-cli")
        self.assert_masked(caught.exception)

    def test_a_model_error_raised_through_the_check(self):
        class Model:
            def ask(self, prompt, timeout):
                raise ai.Busy(f"gateway said {TOKEN}")

        with self.assertRaises(ai.AIError) as caught:
            check.lookup_with(lambda query, limit, keep: [], "What did Dana decide about the Pelican launch date?", [],
                              "", set(), Model(), 5)
        self.assert_masked(caught.exception)

    def test_a_cmd_secret_that_fails(self):
        with self.assertRaises(config.SecretError) as caught:
            config.Secret(f"cmd:false {TOKEN}").reveal()
        self.assert_masked(caught.exception)

    def test_a_config_problem_naming_a_key(self):
        with self.assertRaises(config.ConfigError) as caught:
            config.parse(f'"{TOKEN}" = 1\n')
        self.assert_masked(caught.exception)
        self.assertNotIn(TOKEN, "\n".join(caught.exception.problems))

    def test_ssh_error_output(self):
        ssh = self.program("ssh", f"Permission denied for key {TOKEN}\n", code=255)
        host = config.Host("laptop", "laptop", sys.executable)
        source = config.parse('[sources.notes]\npath = "/n"\n').source("notes")
        with mock.patch.dict(os.environ, {"PATH": os.path.dirname(ssh) + os.pathsep + os.environ["PATH"]}):
            with self.assertRaises(RuntimeError) as caught:
                list(remote.SshReader(host).events(source, None))
        self.assert_masked(caught.exception)

    def test_a_remote_line_that_is_not_an_event(self):
        with self.assertRaises(ValueError) as caught:
            remote.decode(f'{{"text": "{TOKEN}"}}')
        self.assert_masked(caught.exception)

    def test_gh_and_git_error_output(self):
        with self.assertRaises(RuntimeError) as caught:
            github.run_gh(self.program("gh", f"HTTP 401: Bad credentials {TOKEN}\n"), ["search", "issues"])
        self.assert_masked(caught.exception)
        subprocess.run(["git", "init", "-q", self.tmp], check=True)
        with self.assertRaises(RuntimeError) as caught:
            git.run_git(["rev-parse", TOKEN], self.tmp)
        self.assert_masked(caught.exception)

    def test_gmail_and_google_answers(self):
        with self.assertRaises(RuntimeError) as caught:
            gmail._ok(("NO", [f"[AUTHENTICATIONFAILED] {TOKEN}".encode()]))
        self.assert_masked(caught.exception)
        body = io.BytesIO(f'{{"error": "invalid_grant {TOKEN}"}}'.encode())
        failure = urllib.error.HTTPError(gmail.TOKEN_URL, 400, "Bad Request", {}, body)
        with mock.patch.object(gmail.urllib.request, "urlopen", side_effect=failure):
            with self.assertRaises(RuntimeError) as caught:
                gmail._token_request({"grant_type": "refresh_token"})
        self.assert_masked(caught.exception)

    def test_a_sync_failure_stays_inside_the_ingest_and_is_stored_masked(self):
        hook = mock.Mock(side_effect=RuntimeError(f"relay refused {TOKEN}"))
        db = store.connect(os.path.join(self.tmp, "superhistory.sqlite"))
        self.addCleanup(db.close)
        with mock.patch.dict(sys.modules, {"kb.sync": mock.Mock(after_ingest=hook)}):
            self.assertEqual(ingest.after(None, config.parse("[sync]\n"), db, mock.Mock()), 1)
        [(unit, line)] = store.failing(db)
        self.assertNotIn(TOKEN, line)


if __name__ == "__main__":
    unittest.main()
