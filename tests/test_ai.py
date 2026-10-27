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
        self.assertTrue(sent["headers"]["user-agent"].startswith("kb/"))

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
        cases = [(401, NotReady), (402, NotReady), (403, NotReady), (404, NotReady), (400, Refused), (429, Busy), (500, Busy),
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


class OtherApiPluginTest(unittest.TestCase):
    def setUp(self):
        self.server = FakeAnthropic(self)
        os.environ["KB_TEST_API_KEY"] = "test-key-123"
        self.addCleanup(os.environ.pop, "KB_TEST_API_KEY", None)

    def model(self, plugin, extra=""):
        text = f'[ai]\nplugin = "{plugin}"\nbase_url = "{self.server.url}/v9"\nkey = "env:KB_TEST_API_KEY"\n{extra}'
        return ai.load(config.parse(text).ai)

    def test_openai_sends_chat_completions_with_a_bearer_key(self):
        self.server.reply(payload={"choices": [{"message": {"content": '{"query": "a b"}'}, "finish_reason": "stop"}]})
        self.assertEqual(self.model("openai").ask(Prompt("sys", "usr", 77), 5), '{"query": "a b"}')
        sent = self.server.requests[0]
        self.assertEqual(sent["path"], "/v9/chat/completions")
        self.assertEqual(sent["headers"]["authorization"], "Bearer test-key-123")
        self.assertEqual(sent["body"]["messages"], [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}])
        self.assertEqual((sent["body"]["model"], sent["body"]["max_completion_tokens"]), ("gpt-5.4-mini", 77))
        self.assertEqual(sent["body"]["reasoning_effort"], "low")

    def test_openrouter_and_compatible_servers_take_max_tokens_and_no_effort(self):
        for plugin, model in (("openrouter", "anthropic/claude-haiku-5.5"), ("openai-compatible", "local-small")):
            with self.subTest(plugin):
                self.server.reply(payload={"choices": [{"message": {"content": "ok"}}]})
                extra = 'model = "local-small"\n' if plugin == "openai-compatible" else ""
                self.assertEqual(self.model(plugin, extra).ask(Prompt("s", "u", 50), 5), "ok")
                body = self.server.requests[-1]["body"]
                self.assertEqual((body["model"], body["max_tokens"]), (model, 50))
                self.assertNotIn("reasoning_effort", body)

    def test_an_openai_compatible_server_needs_a_model(self):
        with self.assertRaises(NotReady):
            self.model("openai-compatible")

    def test_a_filtered_answer_is_refused(self):
        self.server.reply(payload={"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]})
        with self.assertRaises(Refused):
            self.model("openai").ask(Prompt("s", "u"), 5)

    def test_opencode_go_speaks_anthropic_with_haiku(self):
        self.server.reply(payload=text_reply("ok"))
        model = self.model("opencode-go")
        self.assertEqual(model.ask(Prompt("s", "u"), 5), "ok")
        sent = self.server.requests[0]
        self.assertEqual((sent["path"], sent["headers"]["x-api-key"]), ("/v9/v1/messages", "test-key-123"))
        self.assertEqual(sent["body"]["model"], "claude-haiku-5-5")
        self.assertRegex(sent["headers"]["x-opencode-session"], r"^[0-9a-f-]{36}$")
        self.server.reply(payload=text_reply("ok"))
        model.ask(Prompt("s", "u"), 5)
        self.assertNotEqual(self.server.requests[1]["headers"]["x-opencode-session"], sent["headers"]["x-opencode-session"])
        self.assertEqual(config.parse('[ai]\nplugin = "opencode-go"\n').ai.options.base_url,
                         "https://opencode.ai/inference/go/anthropic")

    def test_google_sends_generate_content_and_skips_thoughts(self):
        self.server.reply(payload={"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True},
                                                                          {"text": "ok"}]}, "finishReason": "STOP"}]})
        self.assertEqual(self.model("google").ask(Prompt("sys", "usr", 60), 5), "ok")
        sent = self.server.requests[0]
        self.assertEqual(sent["path"], "/v9/models/gemini-3.8-flash:generateContent")
        self.assertEqual(sent["headers"]["x-goog-api-key"], "test-key-123")
        self.assertEqual(sent["body"]["systemInstruction"], {"parts": [{"text": "sys"}]})
        self.assertEqual(sent["body"]["generationConfig"], {"maxOutputTokens": 60})

    def test_google_blocked_prompt_is_refused(self):
        self.server.reply(payload={"promptFeedback": {"blockReason": "SAFETY"}})
        with self.assertRaises(Refused):
            self.model("google").ask(Prompt("s", "u"), 5)

    def test_hosted_plugins_need_a_key(self):
        for plugin in ("openai", "openrouter", "google", "opencode-go"):
            with self.subTest(plugin), self.assertRaises(NotReady):
                ai.load(config.parse(f'[ai]\nplugin = "{plugin}"\n').ai).ask(Prompt("s", "u"), 5)
        self.assertEqual(self.server.requests, [])


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
