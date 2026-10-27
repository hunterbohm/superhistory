import io
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import month_b
import outside  # puts bench/ on the path for gateway
import gateway

try:
    import openai
    from openai.resources.chat.completions import Completions
except ImportError:
    openai = None


class Installed(unittest.TestCase):
    """Runs each test with month_b.install() applied and a fresh set of limits, then undoes both."""

    def setUp(self):
        self.saved = [gateway.post, outside.LIMITS]
        if openai:
            self.saved += [openai.OpenAI.__init__, Completions.create]
        outside.LIMITS = outside.Limits()
        month_b.install()

    def tearDown(self):
        gateway.post, outside.LIMITS = self.saved[:2]
        if openai:
            openai.OpenAI.__init__, Completions.create = self.saved[2:]


class GatewayTest(Installed):
    def test_a_429_from_the_gateway_is_sent_once_then_pauses_the_model_and_a_second_stops_the_run(self):
        sent, urlopen = [], urllib.request.urlopen

        def limited(req, timeout=None):
            sent.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(b"cooling down"))

        urllib.request.urlopen = limited
        outside.LIMITS.PAUSE_SECONDS = 0.1
        try:
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
            with self.assertRaises(outside.Stopped):
                outside.LIMITS.wait()
        finally:
            urllib.request.urlopen = urlopen


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
        tool = month_b.waits(SimpleNamespace(retrieve=lambda q: order.append("lookup")))
        tool.retrieve({"question": "q"})
        self.assertEqual(order, ["wait", "lookup"])

    def test_a_mem0_store_with_a_failed_add_is_not_kept_or_scored(self):
        with tempfile.TemporaryDirectory() as stores:
            saved, month_b.STORES = month_b.STORES, stores
            try:
                with self.assertRaises(month_b.Incomplete):
                    month_b.load_mem0(SimpleNamespace(load=lambda items, folder: {"add_failures": 1}), [])
                self.assertFalse(os.path.exists(os.path.join(stores, "mem0", "loaded.json")))
            finally:
                month_b.STORES = saved

    def test_claude_mem_is_not_scored_until_its_worker_has_finished_every_session(self):
        unfinished = SimpleNamespace(load=lambda items, folder: {"reused_store": True}, _drain=lambda seconds: False)
        with self.assertRaises(month_b.Incomplete):
            month_b.load_claude_mem(unfinished, [])
        finished = SimpleNamespace(load=lambda items, folder: {"reused_store": True}, _drain=lambda seconds: True)
        self.assertTrue(month_b.load_claude_mem(finished, [])["drained"])

    def test_it_runs_only_the_rivals(self):
        with self.assertRaises(SystemExit):
            month_b.main(["--tools", "check"])


if __name__ == "__main__":
    unittest.main()
