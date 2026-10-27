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

    def test_a_rate_limit_in_a_200_reply_stops_too_and_is_not_asked_again(self):
        reply = '{"type": "error", "error": {"type": "rate_limit_error", "message": "credentials are cooling down"}}'
        result, calls = self.post_with(reply, reply, reply)
        self.assertIsInstance(result, gateway.RateLimited)
        self.assertEqual(len(calls), 1)
        _, calls = self.post_with('{"ok": true}')
        self.assertEqual(calls, [])

    def test_the_hedged_copy_after_a_rate_limit_sends_nothing(self):
        calls = []

        def busy(url, body, headers, timeout):
            calls.append(url)
            raise ai.Busy("HTTP 429 from http://gateway/v1/messages: rate limited")
        model = check.Counted(check.model())
        with mock.patch.object(check, "_post", busy), check.counting(model):
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


def tools_case(i):
    import common
    return common.Case(f"c{i}", "g", "single", "question", "gold", "", ())


if __name__ == "__main__":
    unittest.main()
