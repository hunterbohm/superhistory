#!/usr/bin/env python3
"""The check's lookups alone on the dev halves, with no answer model or judge: a cheap screen before a full run.

    python3 bench/lookups.py LABEL [--tests month,longmemeval,locomo] [--workers 8]

Rows go to bench/results/hillclimb/lookups/<test>-<LABEL>.jsonl. Per test it prints how often a card was a record
that holds the answer, how often the cards held the answer's key phrases (the month only: its stories list them), how
often a question nothing fits got no cards, the tokens the cards and the check's own model calls used, and errors.
"""
import argparse
import json
import os
import statistics
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "public"), os.path.join(HERE, "month")]

import build  # noqa: E402
import common  # noqa: E402
import gateway  # noqa: E402
import locomo  # noqa: E402
import longmemeval  # noqa: E402
import run  # noqa: E402
import splits  # noqa: E402

OUT = os.path.join(HERE, "results", "hillclimb", "lookups")


def month_cases():
    items, questions = build.collect(build.MONTHS["a"])
    ids = {i.key: i.source_id for i in items}
    cases = [common.Case(q.id, "month", q.kind, q.question, q.answer, "", tuple(ids[k] for k in q.evidence))
             for q in questions]
    phrases = {q.id: q.evidence_has for q in questions}
    return cases, {"month": [i.row() for i in items]}, phrases, True


def longmemeval_cases():
    cases, groups = longmemeval.cases_and_groups(longmemeval.load("s"))
    keep = splits.read("longmemeval-s", "dev")
    cases = [c._replace(kind="none" if "_abs" in c.id else c.kind) for c in cases if c.id in keep]
    return cases, groups, {}, False


def locomo_cases():
    cases, groups, _ = locomo.cases_and_groups(locomo.load())
    keep = splits.read("locomo", "dev")
    return [c for c in cases if c.group in keep], groups, {}, True


TESTS = {"month": month_cases, "longmemeval": longmemeval_cases, "locomo": locomo_cases}


def one(stores, case, phrases):
    store, _ = stores.get(case.group)
    try:
        found = store.lookup(case.question)
    finally:
        stores.done(case.group)
    flat = " ".join(found.context.split()).lower()
    return {"id": case.id, "kind": case.kind, "query": found.query, "evidence": list(case.evidence),
            "shown": found.items, "context_tokens": gateway.count_tokens(run.ANSWER_MODEL, found.context),
            "model_tokens": found.model_tokens, "seconds": round(found.seconds, 3), "error": found.error,
            "phrases_on_cards": bool(phrases) and all(p.lower() in flat for p in phrases)}


def report(test, rows):
    answerable = [r for r in rows if r["kind"] != "none"]
    none = [r for r in rows if r["kind"] == "none"]
    timed = sorted(r["seconds"] for r in rows if r["query"])
    line = (f"{test}: {len(rows)} lookups, evidence on a card {sum(bool(set(r['evidence']) & set(r['shown'])) for r in answerable)}"
            f"/{len(answerable)}")
    if any(r["phrases_on_cards"] for r in answerable):
        line += f", answer phrases on the cards {sum(r['phrases_on_cards'] for r in answerable)}/{len(answerable)}"
    if none:
        line += f", no cards when nothing fits {sum(not r['shown'] for r in none)}/{len(none)}"
    line += (f", card tokens {statistics.mean(r['context_tokens'] for r in rows):,.0f}, check model tokens "
             f"{statistics.mean(r['model_tokens'] for r in rows):,.0f}, p50 {timed[len(timed) // 2] if timed else 0:.2f}s, "
             f"{sum(bool(r['error']) for r in rows)} errors")
    return line


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("label")
    p.add_argument("--tests", default=",".join(TESTS))
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    for test in args.tests.split(","):
        cases, groups, phrases, summaries = TESTS[test]()
        path = os.path.join(OUT, f"{test}-{args.label}.jsonl")
        if not os.path.exists(path):
            with tempfile.TemporaryDirectory(prefix="bench-lookups-") as workdir, ThreadPoolExecutor(args.workers) as pool:
                stores = common.Stores(groups, cases, workdir, summaries)
                rows = list(pool.map(lambda c: one(stores, c, phrases.get(c.id, ())), cases))
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(json.dumps(r) + "\n" for r in rows)
        print(report(test, run.read_jsonl(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
