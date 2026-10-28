import argparse
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "public")]

import gateway  # noqa: E402
import lookups  # noqa: E402
import run  # noqa: E402
import tools  # noqa: E402
from tools import Retrieved, check  # noqa: E402

from kb import ai  # noqa: E402


def http_error(code, body):
    return urllib.error.HTTPError("http://gateway/v1/messages", code, "error", {}, io.BytesIO(body.encode()))


class Stalled(io.RawIOBase):
    """A reply whose body never arrives."""

    def readable(self):
        return True

    def readinto(self, buffer):
        raise TimeoutError("timed out")


def stalled_error(code):
    return urllib.error.HTTPError("http://gateway/v1/messages", code, "error", {}, io.BufferedReader(Stalled()))


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

    def test_a_limit_seen_during_a_backoff_stops_the_next_attempt(self):
        with mock.patch("time.sleep", lambda seconds: gateway.LIMITED.set()):
            calls = []

            def urlopen(request, timeout):
                calls.append(request)
                raise http_error(503, "busy")
            with mock.patch("urllib.request.urlopen", urlopen), self.assertRaises(gateway.RateLimited):
                gateway.post("/v1/messages", {})
        self.assertEqual(len(calls), 1)

    def test_a_bare_cooling_down_reply_stops_and_is_not_asked_again(self):
        reply = '{"message": "All credentials for model x are cooling down"}'
        calls = []

        def urlopen(request, timeout):
            calls.append(request)
            return Answer(reply.encode())
        with mock.patch("urllib.request.urlopen", urlopen), self.assertRaises(gateway.RateLimited):
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
        return mock.patch("urllib.request.urlopen", urlopen), calls

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

    def test_a_server_error_whose_reply_stalls_is_still_tried_again(self):
        network, calls = self.failing(stalled_error(503))
        with network, mock.patch("time.sleep"), self.assertRaises(RuntimeError) as raised:
            gateway.post("/v1/messages", {})
        self.assertNotIsInstance(raised.exception, gateway.RateLimited)
        self.assertEqual(len(calls), gateway.ATTEMPTS)
        gateway.check_limited()

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
    """Mem0 calls its LLM through the OpenAI client, not gateway.post, so its adapter has its own guard."""

    def setUp(self):
        gateway.LIMITED.clear()
        self.addCleanup(gateway.LIMITED.clear)

    def load(self, error):
        from tools import mem0
        added = []

        class Memory:
            llm = mock.Mock()

            @classmethod
            def from_config(cls, config):
                return cls()

            def add(self, messages, **kwargs):
                added.append(messages)
                if error:
                    raise error

            def get_all(self, **kwargs):
                return {"results": []}
        items = [{"ts": f"2026-09-0{i + 1}", "source": "gmail", "source_id": f"s{i}", "title": "t", "content": "c"}
                 for i in range(3)]
        with mock.patch.dict(sys.modules, {"mem0": mock.Mock(Memory=Memory)}), \
                mock.patch("importlib.metadata.version", lambda name: "test"), \
                mock.patch.object(gateway, "_key", lambda: "test"):
            try:
                return mem0.Mem0().load(items, tempfile.gettempdir()), added
            except gateway.RateLimited as exc:
                return exc, added

    def test_a_429_inside_mem0_stops_the_load(self):
        class APIStatusError(Exception):
            status_code = 429
        try:
            try:
                raise APIStatusError("Error code: 429")
            except APIStatusError as inner:
                raise RuntimeError("LLM extraction failed") from inner
        except RuntimeError as wrapped:
            result, added = self.load(wrapped)
        self.assertIsInstance(result, gateway.RateLimited)
        self.assertEqual(len(added), 1)
        self.assertTrue(gateway.LIMITED.is_set())

    def test_a_cooling_down_inside_mem0_stops_the_load(self):
        class InternalServerError(Exception):
            status_code = 503
            body = "All credentials for model x are cooling down"
        result, added = self.load(InternalServerError("Error code: 503"))
        self.assertIsInstance(result, gateway.RateLimited)
        self.assertEqual(len(added), 1)

    def test_other_mem0_errors_still_count_as_failed_adds(self):
        stats, added = self.load(RuntimeError("Error code: 400 - bad request"))
        self.assertEqual((stats["add_failures"], len(added)), (3, 3))

    def test_mem0s_client_retries_a_busy_server_but_not_a_rate_limit(self):
        from tools import mem0
        client = mock.Mock(_should_retry=lambda response: True)
        mem0.no_retry_on_limits(client)
        self.assertFalse(client._should_retry(mock.Mock(status_code=429, text="")))
        self.assertFalse(client._should_retry(mock.Mock(status_code=503, text="model x is cooling down")))
        self.assertTrue(client._should_retry(mock.Mock(status_code=503, text="busy")))

    def test_a_200_that_says_cooling_down_stops_before_mem0_reads_it(self):
        from tools import mem0
        reply = {"choices": None, "message": "All credentials for model x are cooling down"}
        client = mock.Mock()
        client.chat.completions.create = lambda **kwargs: mock.Mock(model_dump=lambda: reply)
        mem0.no_retry_on_limits(client)
        with self.assertRaises(gateway.RateLimited):
            client.chat.completions.create(model="m", messages=[])
        self.assertTrue(gateway.LIMITED.is_set())

    def test_a_completion_about_rate_limits_reaches_mem0(self):
        from tools import mem0
        reply = {"choices": [{"message": {"content": "the job is cooling down after a rate limit"}}]}
        client = mock.Mock()
        client.chat.completions.create = lambda **kwargs: mock.Mock(model_dump=lambda: reply)
        mem0.no_retry_on_limits(client)
        self.assertEqual(client.chat.completions.create(model="m").model_dump(), reply)
        gateway.check_limited()


def tools_case(i):
    import common
    return common.Case(f"c{i}", "g", "single", "question", "gold", "", ())


if __name__ == "__main__":
    unittest.main()
