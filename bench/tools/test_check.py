import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import check  # noqa: E402

from kb import ai  # noqa: E402

USAGE = {"input_tokens": 60, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 30, "output_tokens": 10}


class TokenCountTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"BENCH_API_KEY": "test"})
        env.start()
        self.addCleanup(env.stop)

    def test_a_hedged_copy_that_loses_the_race_still_counts_with_cached_input(self):
        delays = iter([0.4, 0.05])

        def gateway(url, body, headers, timeout):
            time.sleep(next(delays))
            return {"content": [{"type": "text", "text": "ok"}], "usage": USAGE}

        model = check.Counted(check.model())
        with mock.patch.object(check, "_post", gateway):
            self.assertEqual(ai.hedged(model, ai.Prompt("system", "user"), timeout=2, hedge_after=0.1), "ok")
            self.assertEqual(model.settle(2), 200)
        self.assertEqual(model.calls, 2)

    def test_the_summarizer_is_held_to_two_calls_at_once(self):
        self.assertLessEqual(check.model().traits.parallel, 2)


if __name__ == "__main__":
    unittest.main()
