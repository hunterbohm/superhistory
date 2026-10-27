#!/usr/bin/env python3
"""Dev numbers of labelled runs side by side, with the band of the baseline's repeat runs.

    python3 bench/compare.py LABEL [LABEL ...] [--noise noise1,noise2,noise3] [--slice]
    python3 bench/compare.py baseline midpoint final --split test

Handled right: on the month, a correct answer, and for a question nothing fits, a lookup that also added nothing; on
LongMemEval and LoCoMo, the test's own judge. Tokens: what the lookup and the agent's kb commands added to the
agent's prompt, plus the check's own model calls, per question. With --slice, each run is the slice, and the noise
runs are cut to the same questions.
"""
import argparse
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results", "hillclimb")
TESTS = {
    "month": lambda split, slice_, label: os.path.join(OUT, "month", f"{'a' if split == 'dev' else 'b'}-{label}",
                                                       "check.jsonl"),
    "longmemeval": lambda split, slice_, label: os.path.join(
        OUT, "longmemeval", f"s-without-summaries{'-slice100' if slice_ else ''}-{split}-{label}.jsonl"),
    "locomo": lambda split, slice_, label: os.path.join(
        OUT, "locomo", f"locomo10-with-summaries{'-slice150' if slice_ else ''}-{split}-{label}.jsonl"),
}


def read(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def handled(test, row):
    if test == "month":
        return row["correct"] and (row["kind"] != "none" or not row["context"])
    return row["correct"]


def numbers(test, rows):
    timed = sorted(r["lookup_seconds"] for r in rows if r["query"])
    return {"n": len(rows),
            "right": 100 * sum(handled(test, r) for r in rows) / len(rows),
            "added": statistics.mean(r["context_tokens"] + r["tool_tokens"] for r in rows),
            "model": statistics.mean(r["lookup_model_tokens"] for r in rows),
            "p50": timed[len(timed) // 2] if timed else 0.0,
            "errors": sum(bool(r["lookup_error"]) for r in rows)}


def cell(n):
    return (f"{n['right']:.1f}% of {n['n']}, {n['added'] + n['model']:,.0f} tok "
            f"({n['added']:,.0f} + {n['model']:,.0f}), p50 {n['p50']:.1f}s, {n['errors']} err")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("labels", nargs="+")
    p.add_argument("--noise", default="noise1,noise2,noise3")
    p.add_argument("--split", choices=("dev", "test"), default="dev")
    p.add_argument("--slice", action="store_true")
    args = p.parse_args(argv)
    noise = [n for n in args.noise.split(",") if n] if args.split == "dev" else []
    lines = ["| Run | " + " | ".join(TESTS) + " |", "|---" * (len(TESTS) + 1) + "|"]
    band = {}
    for test, where in TESTS.items():
        runs = {label: read(where(args.split, args.slice, label)) for label in args.labels}
        ids = set.intersection(*[{r["id"] for r in rows} for rows in runs.values() if rows] or [set()])
        base = [rows for rows in (read(where(args.split, False, label)) for label in noise) if rows]
        if args.slice:
            base = [[r for r in rows if r["id"] in ids] for rows in base]
        base = [numbers(test, rows) for rows in base if rows]
        band[test] = (base, {label: numbers(test, rows) for label, rows in runs.items() if rows})
    for i, label in enumerate(noise):
        lines.append(f"| {label} | " + " | ".join(
            cell(band[t][0][i]) if i < len(band[t][0]) else "" for t in TESTS) + " |")
    if noise:
        def spread(test):
            base = band[test][0]
            if len(base) < 2:
                return ""
            right = [n["right"] for n in base]
            tokens = [n["added"] + n["model"] for n in base]
            return (f"right {statistics.mean(right):.1f}% ({min(right):.1f} to {max(right):.1f}), tok "
                    f"{statistics.mean(tokens):,.0f} ({min(tokens):,.0f} to {max(tokens):,.0f})")
        lines.append("| noise band | " + " | ".join(spread(t) for t in TESTS) + " |")
    for label in args.labels:
        lines.append(f"| {label} | " + " | ".join(
            cell(band[t][1][label]) if label in band[t][1] else "" for t in TESTS) + " |")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
