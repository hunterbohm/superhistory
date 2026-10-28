import json
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import outside  # puts bench/ on the path for the next three
import gateway
import mem0_locomo
from tools import Retrieved


def row(user, kind, score=None, answer="a", subtype=""):
    return {"user": user, "group": "g", "kind": kind, "score": score, "answer": answer, "subtype": subtype}


class Fresh(unittest.TestCase):
    """gateway.LIMITED belongs to the whole process, so each test starts and ends with it clear."""

    def setUp(self):
        gateway.LIMITED.clear()

    def tearDown(self):
        gateway.LIMITED.clear()


class SetupTest(Fresh):
    def test_replies_come_from_the_benchmarks_answer_model(self):
        self.assertEqual(outside.run.ANSWER_MODEL, "claude-sonnet-5-5")


class LimitsTest(Fresh):
    def test_no_more_than_two_calls_run_at_once(self):
        limits, lock, now, most = outside.Limits(), threading.Lock(), [0], [0]

        def call():
            with lock:
                now[0] += 1
                most[0] = max(most[0], now[0])
            time.sleep(0.05)
            with lock:
                now[0] -= 1

        threads = [threading.Thread(target=limits.send, args=("m", call)) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(most[0], 2)

    def test_a_limit_pauses_every_model_until_its_pause_ends_and_a_second_limit_stops_the_run(self):
        limits = outside.Limits()
        limits.PAUSE_SECONDS = 0.2
        limits.limited("m")
        for model in ("m", "other"):
            with self.assertRaises(outside.RateLimited):
                limits.send(model, lambda: self.fail("sent during the pause"))
        limits.wait()
        self.assertEqual(limits.send("m", lambda: "ok"), "ok")
        limits.limited("other")
        with self.assertRaises(outside.Stopped):
            limits.wait()

    def test_a_limit_seen_by_the_gateways_own_guard_pauses_the_run_and_clears_once_it_is_over(self):
        limits = outside.Limits()
        limits.PAUSE_SECONDS = 0.2
        with self.assertRaises(gateway.RateLimited):
            gateway.limited("HTTP 429 from the plan")
        with self.assertRaises(outside.RateLimited):
            limits.send("m", lambda: self.fail("sent during the pause"))
        limits.wait()
        self.assertFalse(gateway.LIMITED.is_set())
        self.assertEqual(limits.send("m", lambda: "ok"), "ok")
        with self.assertRaises(gateway.RateLimited):
            gateway.limited("HTTP 429 again")
        with self.assertRaises(outside.Stopped):
            limits.wait()

    def test_the_bench_has_one_rate_limit_error(self):
        self.assertIs(outside.RateLimited, gateway.RateLimited)


    def test_a_call_waiting_for_a_slot_does_not_send_once_a_pause_starts(self):
        limits, release, sent = outside.Limits(), threading.Event(), []
        holders = [threading.Thread(target=limits.send, args=("m", release.wait)) for _ in range(2)]
        for t in holders:
            t.start()
        time.sleep(0.05)
        errors = []

        def waiting():
            try:
                limits.send("m", lambda: sent.append(1))
            except outside.RateLimited as exc:
                errors.append(exc)

        waiter = threading.Thread(target=waiting)
        waiter.start()
        time.sleep(0.05)
        limits.limited("m")
        release.set()
        for t in [*holders, waiter]:
            t.join()
        self.assertEqual((sent, len(errors)), ([], 1))


class RowTest(Fresh):
    def test_a_message_whose_lookup_failed_gets_no_row(self):
        tool = SimpleNamespace(retrieve=lambda q: Retrieved("", [], 10.0, "TimeoutError"))
        case = outside.Case("op", "g/u/irrelevance_easy/0", "g", "irrelevance_easy", "u", "hi", "", "", ())
        with self.assertRaises(RuntimeError):
            outside.run_case("check", tool, case)


class Mem0Test(Fresh):
    sample = {"sample_id": "conv-x", "conversation": {"speaker_a": "A", "speaker_b": "B"}}

    def test_a_benchmark_run_never_builds_a_mem0_store(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError):
                mem0_locomo.LocomoMem0(self.sample, build=False).load([], os.path.join(folder, "conv-x"))

    def test_mem0s_model_calls_go_through_the_limits_without_the_sdks_retries(self):
        retries = []
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: "reply")))
        sdk = SimpleNamespace(with_options=lambda max_retries: retries.append(max_retries) or client)

        def send(model, call):
            raise outside.RateLimited(model)

        mem0 = mem0_locomo.LocomoMem0(self.sample, send)
        mem0.memory = SimpleNamespace(llm=SimpleNamespace(client=sdk))
        mem0._gate()
        with self.assertRaises(outside.RateLimited):
            mem0.memory.llm.client.chat.completions.create(model="m", messages=[])
        self.assertEqual((retries, mem0.limited), ([0], 1))


    def test_a_parse_error_log_is_counted_without_hanging_the_build(self):
        count, log = mem0_locomo._Count(), logging.getLogger("test-mem0-count")
        log.addHandler(count)
        worker = threading.Thread(target=log.error, args=(f"{mem0_locomo.PARSE_ERROR}: bad json",), daemon=True)
        worker.start()
        worker.join(2)
        log.removeHandler(count)
        self.assertEqual((worker.is_alive(), count.n), (False, 1))


class SupermemoryTest(Fresh):
    def test_a_benchmark_run_never_feeds_supermemory_a_store(self):
        ready, outside.supermemory_ready = outside.supermemory_ready, lambda count: False
        try:
            with self.assertRaises(RuntimeError):
                outside.load("supermemory", Mem0Test.sample, [{}], "/nonexistent")
        finally:
            outside.supermemory_ready = ready


    def test_a_session_whose_extraction_failed_is_not_ready(self):
        statuses = {"a": {"status": "done", "dreamingStatus": "done"}, "b": {"status": "done", "dreamingStatus": "failed"}}
        store = SimpleNamespace(_documents=lambda: list(statuses))
        make, api = outside.tools.supermemory.Supermemory, outside.tools.supermemory.api
        outside.tools.supermemory.Supermemory = lambda: store
        outside.tools.supermemory.api = lambda path, body=None, method="POST": statuses[path.rsplit("/", 1)[1]]
        try:
            self.assertFalse(outside.supermemory_ready(2))
            statuses["b"]["dreamingStatus"] = "done"
            self.assertTrue(outside.supermemory_ready(2))
        finally:
            outside.tools.supermemory.Supermemory, outside.tools.supermemory.api = make, api


class RepetitionTest(Fresh):
    def test_the_tables_read_stored_similarities_only_for_the_replies_they_were_made_from(self):
        with tempfile.TemporaryDirectory() as out:
            saved, outside.OUT = outside.OUT, out
            try:
                os.makedirs(os.path.join(out, "sample", "op"))
                rows, keep = {"check": {"a": {"answer": "one"}, "b": {"answer": "two"}}}, {"p": ["a", "b"]}
                with open(outside.similarity_path(), "w", encoding="utf-8") as fh:
                    json.dump({"ids": keep, "replies_sha256": outside.replies_hash(rows, keep, ["p"]),
                               "similarity": {"check": {"p": [[1, 0.5], [0.5, 1]]}}}, fh)
                self.assertEqual(outside.similarities(rows, keep, ["p"])["check"]["p"][0][1], 0.5)
                rows["check"]["b"]["answer"] = "two, edited"
                with self.assertRaises(SystemExit):
                    outside.similarities(rows, keep, ["p"])
                with self.assertRaises(SystemExit):
                    outside.similarities(rows, {"p": ["a", "c"]}, ["p"])
            finally:
                outside.OUT = saved


class SampleTest(Fresh):
    def test_op_strata_record_each_persons_count_and_messages_weigh_by_it(self):
        cases = [outside.Case("op", f"g/{p}/irrelevance_easy/{n}", "g", "irrelevance_easy", p, "q", "", "", ())
                 for p, count in (("a", 1), ("b", 3)) for n in range(count)]
        drawn = outside.draw(cases, 1.0, 0)["op/irrelevance_easy"]
        self.assertEqual(drawn["people"], {"g/a": 1, "g/b": 3})
        values, weights = np.array([1.0, 0.0, 0.0, 0.0]), np.array([1.0, 1 / 3, 1 / 3, 1 / 3])
        self.assertAlmostEqual(outside.weighted(values, weights, np.arange(4)), 0.5)


class ParseTest(Fresh):
    def test_fact_score_takes_the_first_grade_it_finds_in_the_judges_order(self):
        self.assertEqual(outside.fact_score("0.5"), 0.5)
        self.assertEqual(outside.fact_score("Score: 1.0"), 1.0)
        self.assertIsNone(outside.fact_score("no grade here"))

    def test_op_score_takes_the_first_number_and_clamps_it(self):
        self.assertEqual(outside.op_score("0.85"), 0.85)
        self.assertEqual(outside.op_score("Output: 1.4"), 1.0)
        self.assertIsNone(outside.op_score("none"))


class OpScoresTest(Fresh):
    def embed(self, texts):
        vectors = {"same": [1.0, 0.0], "other": [0.0, 1.0]}
        return np.array([vectors[t] for t in texts])

    def test_identical_replies_score_zero_and_unrelated_replies_score_one(self):
        self.assertEqual(outside.diversity([row("u", "diversity", answer="same")] * 3, self.embed), 0.0)
        self.assertEqual(outside.diversity([row("u", "diversity", answer="same"), row("u", "diversity", answer="other")],
                                       self.embed), 1.0)

    def test_overall_is_the_mean_over_each_persons_tasks_not_over_messages(self):
        rows = [row("u", "irrelevance_easy", 1.0), row("u", "irrelevance_easy", 0.0), row("u", "irrelevance_easy", 0.5),
                row("u", "sycophancy", 1.0, subtype="fact"),
                row("u", "diversity", answer="same"), row("u", "diversity", answer="other")]
        overall, by_task, subtypes = outside.op_scores(rows, self.embed)
        self.assertEqual(by_task["irrelevance_easy"], 0.5)
        self.assertEqual(overall, (0.5 + 1.0 + 1.0) / 3)
        self.assertEqual(subtypes["fact"], 1.0)
        self.assertIsNone(by_task["irrelevance_hard"])


if __name__ == "__main__":
    unittest.main()
