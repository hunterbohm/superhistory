import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import check  # noqa: E402

from kb import ai  # noqa: E402

USAGE = {"input_tokens": 60, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 30, "output_tokens": 10}


def answered_after(*delays):
    """A gateway whose nth call answers after the nth delay."""
    delays = iter(delays)

    def gateway(url, body, headers, timeout):
        time.sleep(next(delays))
        return {"content": [{"type": "text", "text": "ok"}], "usage": USAGE}
    return gateway


class TokenCountTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"BENCH_API_KEY": "test"})
        env.start()
        self.addCleanup(env.stop)

    def test_a_hedged_copy_that_loses_the_race_still_counts_with_cached_input(self):
        model = check.Counted(check.model())
        with mock.patch.object(check, "_post", answered_after(0.4, 0.05)), check.counting(model):
            self.assertEqual(ai.hedged(model, ai.Prompt("system", "user"), timeout=2, hedge_after=0.1), "ok")
            self.assertEqual(model.settle(2), 200)
        self.assertEqual(model.calls, 2)

    def test_a_hedged_copy_whose_thread_runs_late_still_counts(self):
        class SecondRunsLate(threading.Thread):
            runs = 0

            def run(self):
                SecondRunsLate.runs += 1
                if SecondRunsLate.runs == 2:
                    time.sleep(0.4)
                super().run()

        model = check.Counted(check.model())
        with mock.patch.object(check, "_post", answered_after(0.15, 0.0)), \
                mock.patch.object(check.threading, "Thread", SecondRunsLate), check.counting(model):
            self.assertEqual(ai.hedged(model, ai.Prompt("system", "user"), timeout=2, hedge_after=0.1), "ok")
            self.assertEqual(model.settle(2), 200)

    def test_a_lookup_keeps_where_its_tokens_went_and_which_model_answered(self):
        def gateway(url, body, headers, timeout):
            time.sleep(next(delays))
            return {"model": "claude-haiku-5-5", "content": [{"type": "text", "text": "ok"}],
                    "usage": {**USAGE, "output_tokens_details": {"thinking_tokens": 4}}}
        delays = iter((0.4, 0.05))
        model = check.Counted(check.model())
        with mock.patch.object(check, "_post", gateway), check.counting(model):
            ai.hedged(model, ai.Prompt("system", "user"), timeout=2, hedge_after=0.1)
            self.assertEqual(model.settle(2), 200)
        self.assertEqual(model.detail, {"input": 180, "output": 20, "thinking": 8, "models": ["claude-haiku-5-5"]})

    def test_the_summarizer_is_held_to_two_calls_at_once(self):
        self.assertLessEqual(check.model().traits.parallel, 2)


if __name__ == "__main__":
    unittest.main()
