import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gateway  # noqa: E402
from tools import check  # noqa: E402

from kb import ai  # noqa: E402


def http_error(code, body):
    return urllib.error.HTTPError("http://gateway/v1/messages", code, "error", {}, io.BytesIO(body.encode()))


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RateLimitTest(unittest.TestCase):
    def setUp(self):
        gateway.LIMITED.clear()
        self.addCleanup(gateway.LIMITED.clear)
        env = mock.patch.dict(os.environ, {"BENCH_API_KEY": "test"})
        env.start()
        self.addCleanup(env.stop)

    def post_with(self, *replies):
        calls = []

        def urlopen(request, timeout):
            calls.append(request)
            reply = replies[len(calls) - 1]
            if isinstance(reply, Exception):
                raise reply
            return Answer(reply.encode())
        with mock.patch("urllib.request.urlopen", urlopen), mock.patch("time.sleep"):
            try:
                return gateway.post("/v1/messages", {}), calls
            except Exception as exc:
                return exc, calls

    def test_a_429_is_not_retried_and_stops_new_work(self):
        result, calls = self.post_with(http_error(429, '{"error": "rate_limit_error"}'), '{"ok": true}')
        self.assertIsInstance(result, gateway.RateLimited)
        self.assertEqual(len(calls), 1)
        with self.assertRaises(gateway.RateLimited):
            gateway.check_limited()

    def test_cooling_down_is_a_rate_limit_whatever_its_status(self):
        result, calls = self.post_with(http_error(500, "All credentials for model x are cooling down"), '{"ok": 1}')
        self.assertIsInstance(result, gateway.RateLimited)
        self.assertEqual(len(calls), 1)

    def test_a_server_error_is_still_tried_again(self):
        result, calls = self.post_with(http_error(503, "busy"), '{"ok": true}')
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)
        gateway.check_limited()

    def test_the_checks_plugin_stops_on_a_429_instead_of_retrying(self):
        calls = []

        def busy(url, body, headers, timeout):
            calls.append(url)
            raise ai.Busy("HTTP 429 from http://gateway/v1/messages: rate limited")
        with mock.patch.object(check, "_post", busy), mock.patch("time.sleep"):
            with self.assertRaises(gateway.RateLimited):
                ai.with_retries(lambda: check.model().ask(ai.Prompt("system", "user"), 5))
        self.assertEqual(len(calls), 1)
        self.assertTrue(gateway.LIMITED.is_set())


if __name__ == "__main__":
    unittest.main()
