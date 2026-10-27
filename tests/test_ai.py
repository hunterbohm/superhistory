import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from kb import ai, config
from kb.ai import BadAnswer, Busy, NotReady, Prompt, Refused


class FakeAnthropic:
    def __init__(self, test):
        self.requests = []
        self.replies = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                fake.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                status, payload, headers = fake.replies.pop(0)
                data = json.dumps(payload).encode()
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def reply(self, status=200, payload=None, headers=None):
        self.replies.append((status, payload if payload is not None else {}, headers or {}))


def text_reply(text, stop="end_turn"):
    return {"content": [{"type": "thinking", "thinking": "{\"query\": \"not this\"}"}, {"type": "text", "text": text}],
            "stop_reason": stop}


class AnthropicPluginTest(unittest.TestCase):
    def setUp(self):
        self.server = FakeAnthropic(self)
        os.environ["KB_TEST_API_KEY"] = "test-key-123"
        self.addCleanup(os.environ.pop, "KB_TEST_API_KEY", None)

    def model(self, extra=""):
        c = config.parse(f'[ai]\nplugin = "anthropic"\nbase_url = "{self.server.url}"\nkey = "env:KB_TEST_API_KEY"\n'
                         + extra)
        return ai.load(c.ai)

    def test_sends_the_prompt_and_returns_only_the_text(self):
        self.server.reply(payload=text_reply('{"query": "pelican invoice"}'))
        answer = self.model().ask(Prompt("system words", "user words", 123), timeout=5)
        self.assertEqual(answer, '{"query": "pelican invoice"}')
        sent = self.server.requests[0]
        self.assertEqual(sent["path"], "/v1/messages")
        self.assertEqual(sent["headers"]["x-api-key"], "test-key-123")
        self.assertEqual(sent["body"]["system"], "system words")
        self.assertEqual(sent["body"]["messages"], [{"role": "user", "content": "user words"}])
        self.assertEqual(sent["body"]["max_tokens"], 123)
        self.assertEqual(sent["body"]["model"], "claude-haiku-5-5")
        self.assertEqual(sent["body"]["output_config"], {"effort": "low"})

    def test_bearer_auth_and_no_effort_for_a_compatible_server(self):
        self.server.reply(payload=text_reply("ok"))
        c = config.parse(f'[ai]\nplugin = "anthropic-compatible"\nbase_url = "{self.server.url}/"\n'
                         'key = "env:KB_TEST_API_KEY"\neffort = "none"\nmodel = "local-small"\n')
        self.assertEqual(ai.load(c.ai).ask(Prompt("s", "u"), timeout=5), "ok")
        sent = self.server.requests[0]
        self.assertEqual(sent["headers"]["authorization"], "Bearer test-key-123")
        self.assertNotIn("x-api-key", sent["headers"])
        self.assertNotIn("output_config", sent["body"])
        self.assertEqual(sent["body"]["model"], "local-small")

    def test_a_compatible_server_without_a_key_sends_no_auth(self):
        self.server.reply(payload=text_reply("ok"))
        c = config.parse(f'[ai]\nplugin = "anthropic-compatible"\nbase_url = "{self.server.url}"\n')
        ai.load(c.ai).ask(Prompt("s", "u"), timeout=5)
        headers = {k.lower() for k in self.server.requests[0]["headers"]}
        self.assertFalse({"authorization", "x-api-key"} & headers)

    def test_status_codes_map_to_what_the_caller_should_do(self):
        cases = [(401, NotReady), (403, NotReady), (404, NotReady), (400, Refused), (429, Busy), (500, Busy),
                 (529, Busy)]
        for status, error in cases:
            with self.subTest(status):
                self.server.reply(status, {"error": {"message": "nope"}})
                with self.assertRaises(error):
                    self.model().ask(Prompt("s", "u"), timeout=5)

    def test_rate_limit_carries_retry_after(self):
        self.server.reply(429, {"error": {}}, {"retry-after": "7"})
        with self.assertRaises(Busy) as caught:
            self.model().ask(Prompt("s", "u"), timeout=5)
        self.assertEqual(caught.exception.retry_after, 7.0)

    def test_a_refusal_is_refused(self):
        self.server.reply(payload=text_reply("", stop="refusal"))
        with self.assertRaises(Refused):
            self.model().ask(Prompt("s", "u"), timeout=5)

    def test_an_unreachable_server_is_busy(self):
        c = config.parse('[ai]\nplugin = "anthropic"\nbase_url = "http://127.0.0.1:9"\nkey = "env:KB_TEST_API_KEY"\n')
        with self.assertRaises(Busy):
            ai.load(c.ai).ask(Prompt("s", "u"), timeout=2)

    def test_missing_or_unreadable_key_is_not_ready_and_sends_nothing(self):
        for extra in ("", 'key = "file:/nonexistent/key"\n'):
            with self.subTest(extra):
                c = config.parse(f'[ai]\nplugin = "anthropic"\nbase_url = "{self.server.url}"\n{extra}')
                with self.assertRaises(NotReady):
                    ai.load(c.ai).ask(Prompt("s", "u"), timeout=5)
        self.assertEqual(self.server.requests, [])

    def test_an_ambient_api_key_is_never_used(self):
        os.environ["ANTHROPIC_API_KEY"] = "ambient-key"
        self.addCleanup(os.environ.pop, "ANTHROPIC_API_KEY", None)
        c = config.parse(f'[ai]\nplugin = "anthropic"\nbase_url = "{self.server.url}"\n')
        with self.assertRaises(NotReady):
            ai.load(c.ai).ask(Prompt("s", "u"), timeout=5)
        self.assertEqual(self.server.requests, [])

    def test_no_ai_table_is_not_ready(self):
        with self.assertRaises(NotReady):
            ai.load(None)


class FindJsonTest(unittest.TestCase):
    def test_finds_the_object_with_the_key_inside_prose_and_fences(self):
        text = 'Sure! Here is {not json} and an example {"other": 1}.\n```json\n{"query": "a b c"}\n```\nDone.'
        self.assertEqual(ai.find_json(text, "query"), {"query": "a b c"})

    def test_any_object_when_no_key_is_named(self):
        self.assertEqual(ai.find_json('x {"a": {"b": 1}} y'), {"a": {"b": 1}})

    def test_no_matching_object_is_a_bad_answer(self):
        for text in ("", "no braces", '{"other": 1}', "{broken"):
            with self.subTest(text), self.assertRaises(BadAnswer):
                ai.find_json(text, "query")


class RetryTest(unittest.TestCase):
    def test_busy_is_retried_with_backoff_then_succeeds(self):
        waits, calls = [], []

        def call():
            calls.append(1)
            if len(calls) < 3:
                raise Busy("slow down", retry_after=5 if len(calls) == 2 else 0)
            return "ok"

        self.assertEqual(ai.with_retries(call, sleep=waits.append), "ok")
        self.assertEqual(len(calls), 3)
        self.assertGreaterEqual(waits[1], 5)

    def test_busy_on_every_attempt_raises_the_last(self):
        def call():
            raise Busy("down")
        with self.assertRaises(Busy):
            ai.with_retries(call, attempts=3, sleep=lambda s: None)

    def test_other_errors_are_not_retried(self):
        calls = []

        def call():
            calls.append(1)
            raise NotReady("no key")
        with self.assertRaises(NotReady):
            ai.with_retries(call, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
