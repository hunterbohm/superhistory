import io
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.response
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import month_b
import outside  # puts bench/ on the path for gateway
import gateway
import limits
from tools import Retrieved, claude_mem

try:
    import openai
    from openai.resources.chat.completions import Completions
except ImportError:
    openai = None


class Installed(unittest.TestCase):
    """Runs each test with month_b.install() applied and a fresh set of limits, then undoes both."""

    def setUp(self):
        gateway.LIMITED.clear()
        self.saved = [gateway.post, outside.LIMITS]
        if openai:
            self.saved += [openai.OpenAI.__init__, Completions.create]
        outside.LIMITS = outside.Limits()
        month_b.install()

    def tearDown(self):
        gateway.LIMITED.clear()
        gateway.post, outside.LIMITS = self.saved[:2]
        if openai:
            openai.OpenAI.__init__, Completions.create = self.saved[2:]


class Stalls(io.BytesIO):
    def read(self, *args):
        raise TimeoutError("the reply stalled")


class GatewayTest(Installed):
    """gateway.post's own guard reads each reply for a limit; these fake its transport, so nothing leaves the process."""

    def send(self, *replies):
        sent, key = [], os.environ.get("BENCH_API_KEY")

        def opened(request, timeout):
            sent.append(request.full_url)
            reply = replies[min(len(sent), len(replies)) - 1]
            if isinstance(reply, Exception):
                raise reply
            return urllib.response.addinfourl(io.BytesIO(reply.encode()), {}, request.full_url, 200)

        os.environ["BENCH_API_KEY"] = "test"
        saved, gateway._open = gateway._open, opened
        self.addCleanup(setattr, gateway, "_open", saved)
        self.addCleanup(os.environ.pop if key is None else os.environ.__setitem__, "BENCH_API_KEY",
                        *(() if key is None else (key,)))
        return sent

    def http_error(self, code, body=b"", fp=None):
        return urllib.error.HTTPError("http://gateway/v1/messages", code, "error", {}, fp or io.BytesIO(body))

    def test_a_429_from_the_gateway_is_sent_once_then_pauses_the_model_and_a_second_stops_the_run(self):
        sent = self.send(self.http_error(429, b"cooling down"))
        outside.LIMITS.PAUSE_SECONDS = 0.1
        with self.assertRaises(outside.RateLimited):
            gateway.post("/v1/messages", {"model": "m", "messages": []})
        self.assertEqual(len(sent), 1)
        with self.assertRaises(outside.RateLimited):
            gateway.count_tokens("m", "text")
        self.assertEqual(len(sent), 1)
        outside.LIMITS.wait()
        with self.assertRaises(outside.RateLimited):
            gateway.post("/v1/messages", {"model": "m", "messages": []})
        self.assertEqual(len(sent), 2)
        with self.assertRaises(limits.Stopped):
            outside.LIMITS.wait()

    def test_a_429_whose_reply_stalls_is_one_limit_sent_once(self):
        sent = self.send(self.http_error(429, fp=Stalls()))
        with self.assertRaises(outside.RateLimited):
            gateway.post("/v1/messages", {"model": "m", "messages": []})
        self.assertEqual((len(sent), outside.LIMITS.limits), (1, 1))

    def test_a_200_that_says_cooling_down_is_a_limit_and_is_not_asked_again(self):
        sent = self.send('{"message": "All credentials for model m are cooling down"}')
        with self.assertRaises(outside.RateLimited):
            gateway.post("/v1/messages", {"model": "m", "messages": []})
        self.assertEqual((len(sent), outside.LIMITS.limits), (1, 1))

    def test_the_first_call_after_the_pause_finds_the_flag_clear(self):
        sent = self.send(self.http_error(429), '{"content": [{"type": "text", "text": "hi"}]}')
        outside.LIMITS.PAUSE_SECONDS = 0.1
        with self.assertRaises(outside.RateLimited):
            gateway.post("/v1/messages", {"model": "m", "messages": []})
        time.sleep(0.15)
        self.assertEqual(gateway.post("/v1/messages", {"model": "m", "messages": []})["content"][0]["text"], "hi")
        self.assertEqual((len(sent), outside.LIMITS.limits, gateway.LIMITED.is_set()), (2, 1, False))


@unittest.skipUnless(openai, "needs the OpenAI SDK, as Mem0 does")
class OpenAITest(Installed):
    def test_mem0s_openai_client_has_no_retries_and_each_call_takes_a_slot(self):
        client = openai.OpenAI(api_key="test", base_url="http://127.0.0.1:9/v1")
        self.assertEqual(client.max_retries, 0)
        used = []
        outside.LIMITS = SimpleNamespace(send=lambda model, call: used.append(model) or "reply")
        self.assertEqual(client.chat.completions.create(model="claude-haiku-5-5", messages=[]), "reply")
        self.assertEqual(used, ["claude-haiku-5-5"])


class RunnerTest(Installed):
    def test_a_question_waits_out_a_pause_before_its_lookup(self):
        order = []
        outside.LIMITS = SimpleNamespace(wait=lambda: order.append("wait"))
        tool = month_b.waits(SimpleNamespace(retrieve=lambda q: order.append("lookup") or Retrieved("", [], 0.0)))
        tool.retrieve({"question": "q"})
        self.assertEqual(order, ["wait", "lookup"])

    def test_a_question_whose_lookup_failed_gets_no_row(self):
        outside.LIMITS = SimpleNamespace(wait=lambda: None)
        tool = month_b.waits(SimpleNamespace(retrieve=lambda q: Retrieved("", [], 30.0, "TimeoutError")))
        with self.assertRaises(RuntimeError):
            tool.retrieve({"question": "q"})

    def test_a_mem0_store_with_a_failed_add_is_not_kept_or_scored(self):
        with tempfile.TemporaryDirectory() as stores:
            saved, month_b.STORES = month_b.STORES, stores
            try:
                with self.assertRaises(month_b.Incomplete):
                    month_b.load_mem0(SimpleNamespace(load=lambda items, folder: {"add_failures": 1}), [])
                self.assertFalse(os.path.exists(os.path.join(stores, "mem0", "loaded.json")))
            finally:
                month_b.STORES = saved

    def test_claude_mem_is_scored_only_on_a_store_this_runner_replayed_to_the_end(self):
        database = {"sessions": 82, "observations": 500, "summaries": 50}
        worker, claude_mem.worker = claude_mem.worker, lambda method, path, body=None: {"database": dict(database)}
        with tempfile.TemporaryDirectory() as stores:
            saved, month_b.STORES = month_b.STORES, stores
            try:
                unfinished = SimpleNamespace(load=lambda items, folder: {"sessions_replayed": 82},
                                             _drain=lambda seconds: False)
                with self.assertRaises(month_b.Incomplete):
                    month_b.load_claude_mem(unfinished, [])
                reused = SimpleNamespace(load=lambda items, folder: {"reused_store": True}, _drain=lambda s: True)
                with self.assertRaises(RuntimeError):
                    month_b.load_claude_mem(reused, [])
                replayed = SimpleNamespace(load=lambda items, folder: {"sessions_replayed": 82}, _drain=lambda s: True)
                self.assertTrue(month_b.load_claude_mem(replayed, [])["drained"])
                self.assertTrue(month_b.load_claude_mem(reused, [])["drained"])
                database["observations"] = 0
                with self.assertRaises(RuntimeError):
                    month_b.load_claude_mem(reused, [])
            finally:
                month_b.STORES, claude_mem.worker = saved, worker

    def test_a_rate_limit_raised_by_gateway_during_mem0s_load_pauses_then_loads_once_more(self):
        calls = []

        def load(items, folder):
            calls.append(folder)
            if len(calls) == 1:
                gateway.limited("Mem0's model call: HTTP 429")
            return {"add_failures": 0, "memories": 3}

        make, run_tool, saved = month_b.tools.make, month_b.run.run_tool, month_b.STORES
        month_b.tools.make = lambda name: SimpleNamespace(load=load, retrieve=lambda q: Retrieved("", [], 0.0))
        month_b.run.run_tool = lambda *args: {"ran": 0, "failed": 0}
        outside.LIMITS.PAUSE_SECONDS = 0.1
        with tempfile.TemporaryDirectory() as stores:
            month_b.STORES = stores
            try:
                entry = month_b.run_rival("mem0", [], [], 2)
            finally:
                month_b.tools.make, month_b.run.run_tool, month_b.STORES = make, run_tool, saved
        self.assertEqual((len(calls), entry["load"]["memories"], outside.LIMITS.limits), (2, 3, 1))
        self.assertFalse(gateway.LIMITED.is_set())

    def test_it_runs_only_the_rivals(self):
        with self.assertRaises(SystemExit):
            month_b.main(["--tools", "check"])


if __name__ == "__main__":
    unittest.main()
