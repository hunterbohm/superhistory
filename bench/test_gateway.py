import argparse
import http.server
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "public")]

import gateway  # noqa: E402
import lookups  # noqa: E402
import run  # noqa: E402
import tools  # noqa: E402
from tools import Retrieved, check  # noqa: E402

from kb import ai  # noqa: E402
from kb.ai import api  # noqa: E402
from kb.config import Secret  # noqa: E402


def http_error(code, body):
    return urllib.error.HTTPError("http://gateway/v1/messages", code, "error", {}, io.BytesIO(body.encode()))


class Stalled(io.RawIOBase):
    """A reply whose body stops arriving after `first`, its next read failing `wait` seconds later."""

    def __init__(self, first=b"", wait=0):
        self.first, self.wait = first, wait

    def readable(self):
        return True

    def readinto(self, buffer):
        if not self.first:
            time.sleep(self.wait)
            raise TimeoutError("timed out")
        sent, self.first = self.first, b""
        buffer[:len(sent)] = sent
        return len(sent)


def stalled_error(code, first=b"", wait=0):
    return urllib.error.HTTPError("http://gateway/v1/messages", code, "error", {},
                                  io.BufferedReader(Stalled(first, wait)))


class Answer(io.BytesIO):
    headers, url, status = {}, "http://gateway/v1/messages", 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Pieces(io.RawIOBase):
    """A reply that arrives in the given pieces."""

    def __init__(self, pieces):
        self.pieces = list(pieces)

    def readable(self):
        return True

    def readinto(self, buffer):
        if not self.pieces:
            return 0
        piece = self.pieces.pop(0)
        buffer[:len(piece)] = piece
        return len(piece)


class PiecesAnswer(io.BufferedReader):
    """A status 200 reply that arrives in the given pieces."""
    headers, url, status = {}, "http://gateway/v1/messages", 200

    def __init__(self, pieces):
        super().__init__(Pieces(pieces))


class StalledAnswer(io.BufferedReader):
    """A status 200 reply whose body stops arriving after `first`."""
    headers, url, status = {}, "http://gateway/v1/messages", 200

    def __init__(self, first, wait=0):
        super().__init__(Stalled(first, wait))


class ConfigTest(unittest.TestCase):
    """The endpoint and the key come from settings only: nothing is assumed when they are unset."""

    def setUp(self):
        for patch in (mock.patch.object(gateway, "BASE_URL", ""), mock.patch.object(gateway, "KEY_FILE", ""),
                      mock.patch.dict(os.environ, {}, clear=False)):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("BENCH_API_KEY", None)

    def test_an_unset_endpoint_stops_with_a_message_that_names_the_setting(self):
        with self.assertRaises(SystemExit) as stop:
            gateway.require()
        self.assertIn("BENCH_BASE_URL", str(stop.exception))
        with self.assertRaises(gateway.NotConfigured):
            gateway.post("/v1/messages", {})

    def test_an_unset_key_stops_with_a_message_that_names_both_settings(self):
        with mock.patch.object(gateway, "BASE_URL", "http://model.test"):
            with self.assertRaises(SystemExit) as stop:
                gateway.require()
        self.assertIn("BENCH_API_KEY", str(stop.exception))
        self.assertIn("BENCH_KEY_FILE", str(stop.exception))

    def test_the_key_comes_from_the_environment_first_then_the_key_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "key")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("file-key\n")
            with mock.patch.object(gateway, "KEY_FILE", path):
                self.assertEqual((gateway.key_source(), gateway._key()), (f"file:{path}", "file-key"))
                with mock.patch.dict(os.environ, {"BENCH_API_KEY": "env-key"}):
                    self.assertEqual((gateway.key_source(), gateway._key()), ("env:BENCH_API_KEY", "env-key"))


class RateLimitTest(unittest.TestCase):
    def setUp(self):
        gateway.LIMITED.clear()
        self.addCleanup(gateway.LIMITED.clear)
        env = mock.patch.dict(os.environ, {"BENCH_API_KEY": "test"})
        env.start()
        self.addCleanup(env.stop)
        base = mock.patch.object(gateway, "BASE_URL", "http://model.test")
        base.start()
        self.addCleanup(base.stop)

    def post_with(self, *replies):
        calls = []

        def urlopen(request, timeout):
            calls.append(request)
            reply = replies[len(calls) - 1]
            if isinstance(reply, Exception):
                raise reply
            return Answer(reply.encode())
        with mock.patch.object(gateway, "_open", urlopen), mock.patch("time.sleep"):
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

    def test_a_limit_seen_during_a_backoff_stops_the_next_attempt(self):
        with mock.patch("time.sleep", lambda seconds: gateway.LIMITED.set()):
            calls = []

            def urlopen(request, timeout):
                calls.append(request)
                raise http_error(503, "busy")
            with mock.patch.object(gateway, "_open", urlopen), self.assertRaises(gateway.RateLimited):
                gateway.post("/v1/messages", {})
        self.assertEqual(len(calls), 1)

    def test_a_bare_cooling_down_reply_stops_and_is_not_asked_again(self):
        reply = '{"message": "All credentials for model x are cooling down"}'
        calls = []

        def urlopen(request, timeout):
            calls.append(request)
            return Answer(reply.encode())
        with mock.patch.object(gateway, "_open", urlopen), self.assertRaises(gateway.RateLimited):
            gateway.ask_json("m", "system", "user")
        self.assertEqual(len(calls), 1)

    def test_a_model_that_talks_about_rate_limits_is_not_a_rate_limit(self):
        reply = {"type": "message", "content": [{"type": "text", "text": "the job is cooling down; rate limit hit"}]}
        result, calls = self.post_with(json.dumps(reply))
        self.assertEqual(result, reply)
        self.assertFalse(gateway.limited_reply({"input_tokens": 12}))
        gateway.check_limited()

    def failing(self, error):
        """A network whose every call fails with `error`, and the list of calls it saw."""
        calls = []

        def urlopen(request, timeout):
            calls.append(request)
            raise http_error(*error) if isinstance(error, tuple) else error
        return mock.patch.object(gateway, "_open", urlopen), calls

    def test_the_checks_plugin_stops_on_a_429_instead_of_retrying(self):
        network, calls = self.failing((429, '{"error": "rate_limit_error"}'))
        with network, mock.patch("time.sleep"), self.assertRaises(gateway.RateLimited):
            ai.with_retries(lambda: check.model().ask(ai.Prompt("system", "user"), 5))
        self.assertEqual(len(calls), 1)
        self.assertTrue(gateway.LIMITED.is_set())

    def test_the_checks_plugin_still_retries_a_busy_server(self):
        network, calls = self.failing((503, "busy"))
        with network, self.assertRaises(ai.Busy):
            ai.with_retries(lambda: check.model().ask(ai.Prompt("system", "user"), 5), sleep=lambda seconds: None)
        self.assertEqual(len(calls), 6)
        gateway.check_limited()

    def test_a_429_whose_reply_stalls_still_stops_both_paths(self):
        network, calls = self.failing(stalled_error(429))
        with network, mock.patch("time.sleep"), self.assertRaises(gateway.RateLimited):
            gateway.post("/v1/messages", {})
        self.assertEqual(len(calls), 1)
        gateway.LIMITED.clear()
        network, calls = self.failing(stalled_error(429))
        with network, self.assertRaises(gateway.RateLimited):
            ai.with_retries(lambda: check.model().ask(ai.Prompt("system", "user"), 5), sleep=lambda seconds: None)
        self.assertEqual(len(calls), 1)

    def test_cooling_down_that_arrives_before_a_stall_still_stops_both_paths(self):
        first = b'{"error": {"message": "All credentials for model x are cooling down"'
        network, calls = self.failing(stalled_error(503, first))
        with network, mock.patch("time.sleep"), self.assertRaises(gateway.RateLimited):
            gateway.post("/v1/messages", {})
        self.assertEqual(len(calls), 1)
        gateway.LIMITED.clear()
        network, calls = self.failing(stalled_error(503, first))
        with network, self.assertRaises(gateway.RateLimited):
            ai.with_retries(lambda: check.model().ask(ai.Prompt("system", "user"), 5), sleep=lambda seconds: None)
        self.assertEqual(len(calls), 1)

    def answering(self, text):
        """A network whose every call answers status 200 with `text`, and the list of calls it saw."""
        calls = []

        def urlopen(request, timeout):
            calls.append(request)
            return Answer(text.encode())
        return mock.patch.object(gateway, "_open", urlopen), calls

    def test_a_plain_text_cooling_down_with_status_200_stops_both_paths(self):
        network, calls = self.answering("All credentials for model x are cooling down")
        with network, self.assertRaises(gateway.RateLimited):
            gateway.ask_json("m", "system", "user")
        self.assertEqual(len(calls), 1)
        gateway.LIMITED.clear()
        network, calls = self.answering("All credentials for model x are cooling down")
        with network, self.assertRaises(gateway.RateLimited):
            ai.with_retries(lambda: check.model().ask(ai.Prompt("system", "user"), 5), sleep=lambda seconds: None)
        self.assertEqual(len(calls), 1)

    def replying(self, make):
        """A network whose every call answers with a fresh `make()`, and the list of calls it saw."""
        calls = []

        def urlopen(request, timeout):
            calls.append(request)
            return make()
        return mock.patch.object(gateway, "_open", urlopen), calls

    def assert_both_paths_stop(self, make):
        network, calls = self.replying(make)
        with network, mock.patch("time.sleep"), self.assertRaises(gateway.RateLimited):
            gateway.post("/v1/messages", {})
        self.assertEqual(len(calls), 1)
        gateway.LIMITED.clear()
        network, calls = self.replying(make)
        with network, self.assertRaises(gateway.RateLimited):
            ai.with_retries(lambda: check.model().ask(ai.Prompt("system", "user"), 5), sleep=lambda seconds: None)
        self.assertEqual(len(calls), 1)

    def test_a_200_error_that_stalls_after_it_ends_still_stops_both_paths(self):
        error = json.dumps({"type": "error", "error": {"message": "All credentials for model x are cooling down"}})
        self.assert_both_paths_stop(lambda: StalledAnswer(error.encode()))

    def test_plain_text_that_starts_like_json_still_stops_both_paths(self):
        self.assert_both_paths_stop(lambda: Answer(b"[ERROR] All credentials for model x are cooling down"))

    def test_a_completion_is_never_read_for_limit_words(self):
        self.assertFalse(gateway.limited_reply({"choices": [{"message": {"content": "a rate limit cooling down"}}]}))
        self.assertTrue(gateway.limited_reply({"choices": None, "message": "model x is cooling down"}))

    def test_a_limit_is_seen_as_it_arrives_so_the_hedge_sends_nothing(self):
        error = b'{"type": "error", "error": {"message": "All credentials for model x are cooling down"}}'
        for make in (lambda: stalled_error(503, error, 0.5), lambda: StalledAnswer(error, 0.5)):
            gateway.LIMITED.clear()
            calls = []

            def opened(request, timeout, make=make):
                calls.append(request)
                reply = make()
                if isinstance(reply, Exception):
                    raise reply
                return reply
            model = check.Counted(check.model())
            with mock.patch.object(gateway, "_open", opened), check.counting(model), self.assertRaises(Exception):
                ai.hedged(model, ai.Prompt("system", "user"), timeout=2, hedge_after=0.1)
            model.settle(2)
            self.assertEqual(len(calls), 1)
            self.assertTrue(gateway.LIMITED.is_set())

    def test_an_error_beside_a_content_or_choices_list_is_still_read(self):
        self.assertTrue(gateway.limited_reply(
            {"type": "error", "content": [], "error": {"message": "model x is cooling down"}}))
        self.assertTrue(gateway.limited_reply({"choices": [], "message": "model x is cooling down"}))
        self.assertTrue(gateway.limited_reply({"content": [{"text": "x"}], "error": "model x is cooling down"}))
        network, calls = self.answering('{"type": "error", "content": [], "error": {"message": "cooling down"}}')
        with network, self.assertRaises(gateway.RateLimited):
            gateway.ask_json("m", "system", "user")
        self.assertEqual(len(calls), 1)

    def test_a_redirect_is_not_followed_and_its_reply_is_read(self):
        seen = []

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["content-length"]))
                seen.append(self.path)
                body = Redirect.body
                self.send_response(302)
                self.send_header("location", "/v1/elsewhere")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                # Where a followed redirect would land: an ordinary reply.
                seen.append(self.path)
                self.send_response(200)
                self.send_header("content-length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with mock.patch.object(gateway, "BASE_URL", base), mock.patch("time.sleep"):
            Redirect.body = b"All credentials for model x are cooling down"
            with self.assertRaises(gateway.RateLimited):
                gateway.post("/v1/messages", {})
            self.assertEqual(seen, ["/v1/messages"])
            gateway.LIMITED.clear()
            seen.clear()
            plugin = api.Anthropic("anthropic-compatible", api.AnthropicCompatibleOptions(
                key=Secret("env:BENCH_API_KEY"), model="m", base_url=base))
            with self.assertRaises(gateway.RateLimited):
                ai.with_retries(lambda: plugin.ask(ai.Prompt("system", "user"), 5), sleep=lambda seconds: None)
            self.assertEqual(seen, ["/v1/messages"])
            gateway.LIMITED.clear()
            seen.clear()
            Redirect.body = b"moved"
            with self.assertRaises(RuntimeError) as raised:
                gateway.post("/v1/messages", {})
            self.assertNotIsInstance(raised.exception, gateway.RateLimited)
            self.assertEqual(seen, ["/v1/messages"])

    def test_a_lookup_that_fails_once_a_limit_is_seen_gets_no_row(self):
        def lookup(*args, **kwargs):
            try:
                gateway.limited("the hedge copy met a limit")
            except gateway.RateLimited:
                pass
            raise TimeoutError("the other copy ran out of time")
        with mock.patch.object(check.store, "connect", lambda *args, **kwargs: mock.Mock()), \
                mock.patch.object(check.check, "lookup", lookup), self.assertRaises(gateway.RateLimited):
            check.Store(mock.Mock()).lookup("a question")

    def test_a_limit_named_across_two_pieces_is_found(self):
        pieces = [b"All credentials for model x are cool", b"ing down"]
        self.assert_both_paths_stop(lambda: PiecesAnswer(pieces))
        gateway.LIMITED.clear()
        network, calls = self.failing(urllib.error.HTTPError(
            "http://gateway/v1/messages", 503, "error", {}, io.BufferedReader(Pieces(pieces))))
        with network, mock.patch("time.sleep"), self.assertRaises(gateway.RateLimited):
            gateway.post("/v1/messages", {})
        self.assertEqual(len(calls), 1)

    def test_a_long_reply_is_parsed_once_not_after_every_piece(self):
        reply = json.dumps({"type": "message", "content": [{"type": "text", "text": "x" * 2_000_000}]}).encode()
        pieces = [reply[i:i + 4096] for i in range(0, len(reply), 4096)]
        request = urllib.request.Request("http://gateway/v1/messages", data=b"{}", method="POST")
        with mock.patch.object(gateway, "_open", lambda request, timeout: PiecesAnswer(pieces)), \
                mock.patch.object(gateway.json, "loads", wraps=json.loads) as loads:
            with gateway.urlopen(request, 5) as answer:
                self.assertEqual(answer.read(), reply)
        self.assertGreater(len(pieces), 400)
        self.assertEqual(loads.call_count, 1)
        gateway.check_limited()

    def test_two_hedged_lookups_at_once_send_at_most_two_calls_at_a_time(self):
        reply = json.dumps({"type": "message", "content": [{"type": "text", "text": "ok"}],
                            "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
        lock, now, most, sent = threading.Lock(), [0], [0], []

        def opened(request, timeout):
            with lock:
                now[0] += 1
                most[0] = max(most[0], now[0])
                sent.append(request)
            time.sleep(0.3)
            with lock:
                now[0] -= 1
            return Answer(reply)

        def lookup():
            model = check.Counted(check.model())
            with check.counting(model):
                ai.hedged(model, ai.Prompt("system", "user"), timeout=3, hedge_after=0.05)
                model.settle(3)
        with mock.patch.object(gateway, "_open", opened):
            workers = [threading.Thread(target=lookup) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(10)
        self.assertEqual(len(sent), 4)
        self.assertLessEqual(most[0], 2)

    def test_plain_text_that_names_no_limit_fails_as_before(self):
        network, calls = self.answering("<html>upstream hiccup</html>")
        with network, self.assertRaises(ValueError):
            gateway.post("/v1/messages", {})
        network, calls = self.answering("<html>upstream hiccup</html>")
        with network, self.assertRaises(ai.Busy):
            ai.with_retries(lambda: check.model().ask(ai.Prompt("system", "user"), 5), sleep=lambda seconds: None)
        self.assertEqual(len(calls), 6)
        gateway.check_limited()

    def test_a_server_error_whose_reply_stalls_is_still_tried_again(self):
        network, calls = self.failing(stalled_error(503))
        with network, mock.patch("time.sleep"), self.assertRaises(RuntimeError) as raised:
            gateway.post("/v1/messages", {})
        self.assertNotIsInstance(raised.exception, gateway.RateLimited)
        self.assertEqual(len(calls), gateway.ATTEMPTS)
        gateway.check_limited()

    def test_a_rate_limit_error_type_under_another_status_stops_both_paths(self):
        body = json.dumps({"error": {"type": "rate_limit_error", "message": "Rate limit exceeded"}})
        for make in (lambda: http_error(503, body), lambda: http_error(500, '{"error": "Rate limit exceeded"}')):
            gateway.LIMITED.clear()
            network, calls = self.failing(make())
            with network, mock.patch("time.sleep"), self.assertRaises(gateway.RateLimited):
                gateway.post("/v1/messages", {})
            self.assertEqual(len(calls), 1)
            gateway.LIMITED.clear()
            network, calls = self.failing(make())
            with network, self.assertRaises(gateway.RateLimited):
                ai.with_retries(lambda: check.model().ask(ai.Prompt("system", "user"), 5), sleep=lambda seconds: None)
            self.assertEqual(len(calls), 1)

    def test_cooling_down_after_a_long_diagnostic_still_stops_both_paths(self):
        body = json.dumps({"diagnostic": "x" * 1000, "message": "All credentials for model x are cooling down"})
        network, calls = self.failing((503, body))
        with network, mock.patch("time.sleep"), self.assertRaises(gateway.RateLimited):
            gateway.post("/v1/messages", {})
        self.assertEqual(len(calls), 1)
        gateway.LIMITED.clear()
        network, calls = self.failing((503, body))
        with network, mock.patch("time.sleep"), self.assertRaises(gateway.RateLimited):
            ai.with_retries(lambda: check.model().ask(ai.Prompt("system", "user"), 5))
        self.assertEqual(len(calls), 1)

    def test_a_rate_limit_in_a_200_reply_stops_too_and_is_not_asked_again(self):
        reply = '{"type": "error", "error": {"type": "rate_limit_error", "message": "credentials are cooling down"}}'
        result, calls = self.post_with(reply, reply, reply)
        self.assertIsInstance(result, gateway.RateLimited)
        self.assertEqual(len(calls), 1)
        _, calls = self.post_with('{"ok": true}')
        self.assertEqual(calls, [])

    def test_the_hedged_copy_after_a_rate_limit_sends_nothing(self):
        network, calls = self.failing((429, '{"error": "rate_limit_error"}'))
        model = check.Counted(check.model())
        with network, check.counting(model):
            with self.assertRaises(Exception):
                ai.hedged(model, ai.Prompt("system", "user"), timeout=2, hedge_after=0.05)
            model.settle(2)
        self.assertEqual(len(calls), 1)

    def test_rejudge_keeps_what_it_graded_and_resumes_the_rest(self):
        questions = run.read_jsonl(os.path.join(run.MONTHS["a"], "questions.jsonl"))[:3]
        rows = [{"id": q["id"], "kind": q["kind"], "answer": "x", "correct": False, "gold": q["answer"],
                 "context": "", "context_tokens": 0, "tool_tokens": 0, "lookup_model_tokens": 0, "lookup_seconds": 1,
                 "lookup_error": None, "evidence": q["evidence"], "context_items": [], "read_items": []}
                for q in questions]
        graded = []

        def grade(setup, q, text):
            graded.append(q["id"])
            if len(graded) == 2:
                gateway.limited("test")
            return {"correct": True, "reason": "ok"}
        with tempfile.TemporaryDirectory() as out, mock.patch.object(run, "grade", grade), \
                mock.patch.object(run, "REGRADES", os.path.join(tempfile.gettempdir(), "unused-regrades.tsv")):
            path = os.path.join(out, "check.jsonl")
            with open(path, "w") as fh:
                fh.writelines(json.dumps(r) + "\n" for r in rows)
            self.assertIsInstance(run.rejudge(out, questions, run.MONTH_SETUP, 1, "test"), gateway.RateLimited)
            self.assertEqual(sum("regraded" in r for r in run.read_jsonl(path)), 1)
            gateway.LIMITED.clear()
            self.assertIsNone(run.rejudge(out, questions, run.MONTH_SETUP, 1, "test"))
            self.assertEqual(len(graded), 4)
            self.assertEqual(graded.count(questions[0]["id"]), 1)
            self.assertTrue(all(r["correct"] and "regraded" in r for r in run.read_jsonl(path)))

    def test_the_lookup_screen_stops_cleanly_when_its_first_lookup_is_limited(self):
        def one(stores, case, phrases):
            gateway.limited("test")
        with tempfile.TemporaryDirectory() as out, mock.patch.object(lookups, "OUT", out), \
                mock.patch.dict(lookups.TESTS, {"fake": lambda: ([tools_case(0)], {"g": []}, {}, False)}), \
                mock.patch.object(lookups, "one", one):
            self.assertEqual(lookups.main(["t", "--tests", "fake", "--workers", "1"]), 1)

    def test_the_lookup_screen_keeps_finished_rows_and_resumes(self):
        cases = [tools_case(i) for i in range(3)]
        seen = []

        def one(stores, case, phrases):
            gateway.check_limited()
            seen.append(case.id)
            if len(seen) == 2:
                gateway.limited("test")
            return {"id": case.id, "kind": "single", "query": "q", "evidence": [], "shown": [], "context_tokens": 0,
                    "model_tokens": 0, "seconds": 1.0, "error": None, "phrases_on_cards": False}
        with tempfile.TemporaryDirectory() as out, mock.patch.object(lookups, "OUT", out), \
                mock.patch.dict(lookups.TESTS, {"fake": lambda: (cases, {"g": []}, {}, False)}), \
                mock.patch.object(lookups, "one", one):
            self.assertEqual(lookups.main(["t", "--tests", "fake", "--workers", "1"]), 1)
            self.assertEqual(len(run.read_jsonl(os.path.join(out, "fake-t.jsonl"))), 1)
            gateway.LIMITED.clear()
            self.assertEqual(lookups.main(["t", "--tests", "fake", "--workers", "1"]), 0)
            self.assertEqual(sorted(r["id"] for r in run.read_jsonl(os.path.join(out, "fake-t.jsonl"))),
                             ["c0", "c1", "c2"])

    def test_the_latency_run_keeps_finished_rows_and_resumes(self):
        timed = []

        class Fake(tools.Tool):
            def retrieve(self, question):
                timed.append(question["id"])
                if len(timed) == 2:
                    gateway.limited("test")
                return Retrieved("", [], 1.0)
        args = argparse.Namespace(tools="fake", questions=3, rounds=1, fresh=False)
        with tempfile.TemporaryDirectory() as out, mock.patch.object(tools, "make", lambda name: Fake()):
            args.out = out
            with self.assertRaises(gateway.RateLimited):
                run.latency(args)
            self.assertEqual(len(run.read_jsonl(os.path.join(out, "latency.jsonl"))), 1)
            gateway.LIMITED.clear()
            run.latency(args)
            self.assertEqual(len(run.read_jsonl(os.path.join(out, "latency.jsonl"))), 3)
            self.assertEqual(len(timed), 4)


class Mem0RateLimitTest(unittest.TestCase):
    """Mem0 calls its LLM through the OpenAI client, not gateway.post, so the adapter gives that client a transport
    through gateway.urlopen and stops its load on the flag."""

    def setUp(self):
        gateway.LIMITED.clear()
        self.addCleanup(gateway.LIMITED.clear)
        key = mock.patch.dict(os.environ, {"BENCH_API_KEY": "test"})
        key.start()
        self.addCleanup(key.stop)
        base = mock.patch.object(gateway, "BASE_URL", "http://model.test")
        base.start()
        self.addCleanup(base.stop)

    def load(self, add):
        from tools import mem0
        added = []

        class Memory:
            llm = mock.Mock()

            @classmethod
            def from_config(cls, config):
                return cls()

            def add(self, messages, **kwargs):
                added.append(messages)
                add()

            def get_all(self, **kwargs):
                return {"results": []}
        items = [{"ts": f"2026-09-0{i + 1}", "source": "gmail", "source_id": f"s{i}", "title": "t", "content": "c"}
                 for i in range(3)]
        with mock.patch.dict(sys.modules, {"mem0": mock.Mock(Memory=Memory)}), \
                mock.patch("importlib.metadata.version", lambda name: "test"), \
                mock.patch.object(mem0, "gateway_client", lambda: None):
            try:
                return mem0.Mem0().load(items, tempfile.gettempdir()), added
            except gateway.RateLimited as exc:
                return exc, added

    def test_a_rate_limit_inside_mem0s_model_call_stops_the_load(self):
        def add():
            try:
                gateway.limited("test")
            except gateway.RateLimited as inner:
                raise RuntimeError("LLM extraction failed") from inner
        result, added = self.load(add)
        self.assertIsInstance(result, gateway.RateLimited)
        self.assertEqual(len(added), 1)

    def test_other_mem0_errors_still_count_as_failed_adds(self):
        def add():
            raise RuntimeError("Error code: 400 - bad request")
        stats, added = self.load(add)
        self.assertEqual((stats["add_failures"], len(added)), (3, 3))


@unittest.skipUnless(importlib.util.find_spec("httpx2") and importlib.util.find_spec("openai"),
                     "needs the Mem0 Python, which has the OpenAI client")
class Mem0ClientTest(unittest.TestCase):
    """The OpenAI client Mem0 gets, against replies at the network: run with the Mem0 Python."""

    def setUp(self):
        gateway.LIMITED.clear()
        self.addCleanup(gateway.LIMITED.clear)
        for patch in (mock.patch.dict(os.environ, {"BENCH_API_KEY": "test"}), mock.patch("time.sleep"),
                      mock.patch.object(gateway, "BASE_URL", "http://model.test")):
            patch.start()
            self.addCleanup(patch.stop)

    def ask(self, *replies):
        from tools import mem0
        calls = []

        def urlopen(request, timeout):
            calls.append(request)
            reply = replies[min(len(calls), len(replies)) - 1]
            if isinstance(reply, Exception):
                raise reply
            return reply
        with mock.patch.object(gateway, "_open", urlopen):
            try:
                return mem0.gateway_client().chat.completions.create(
                    model="m", messages=[{"role": "user", "content": "hi"}]), calls
            except Exception as exc:
                return exc, calls

    def test_a_429_whose_reply_stalls_is_sent_once_and_stops(self):
        result, calls = self.ask(stalled_error(429))
        self.assertIsInstance(result, gateway.RateLimited)
        self.assertEqual(len(calls), 1)

    def test_a_plain_text_cooling_down_is_sent_once_and_stops(self):
        result, calls = self.ask(Answer(b"[ERROR] All credentials for model x are cooling down"))
        self.assertIsInstance(result, gateway.RateLimited)
        self.assertEqual(len(calls), 1)

    def test_a_busy_server_and_a_timeout_are_still_retried(self):
        import openai
        result, calls = self.ask(http_error(503, "busy"))
        self.assertIsInstance(result, openai.InternalServerError)
        self.assertEqual(len(calls), 3)
        result, calls = self.ask(TimeoutError("timed out"))
        self.assertIsInstance(result, openai.APITimeoutError)
        self.assertEqual(len(calls), 3)
        gateway.check_limited()

    def test_a_completion_about_rate_limits_comes_through(self):
        reply = {"id": "a", "object": "chat.completion", "created": 1, "model": "m", "choices": [
            {"index": 0, "finish_reason": "stop",
             "message": {"role": "assistant", "content": "the job is cooling down after a rate limit"}}]}
        result, calls = self.ask(Answer(json.dumps(reply).encode()))
        self.assertEqual(result.choices[0].message.content, reply["choices"][0]["message"]["content"])
        self.assertEqual(len(calls), 1)
        gateway.check_limited()


def tools_case(i):
    import common
    return common.Case(f"c{i}", "g", "single", "question", "gold", "", ())


if __name__ == "__main__":
    unittest.main()
