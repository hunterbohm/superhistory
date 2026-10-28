#!/usr/bin/env python3
"""Dev numbers of labelled runs side by side, with the band of the baseline's repeat runs.

    python3 bench/compare.py LABEL [LABEL ...] [--noise noise1,noise2,noise3] [--slice]
    python3 bench/compare.py baseline midpoint final --split test
    python3 bench/compare.py final baseline-paired --split test --paired

Handled right: on the month, a correct answer, and for a question nothing fits, a lookup that also added nothing; on
LongMemEval and LoCoMo, the test's own judge. Tokens: what the lookup and the agent's kb commands added to the
agent's prompt, plus the check's own model calls, per question. With --slice, each run is the slice, and the noise
runs are cut to the same questions.

--paired compares the first label with the second question by question, over the questions both ran: the change in
handled right, with McNemar's exact test on the questions only one got right, and the change in tokens added to the
agent's prompt and in total tokens. Each change has a 95% interval from paired bootstrap resamples of the questions.
"""
import argparse
import json
import math
import os
import random
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


RESAMPLES = 10_000


def mcnemar(only_a, only_b):
    """The two-sided exact p-value that two runs differ, from the questions only one of them got right."""
    n = only_a + only_b
    if not n:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(only_a, only_b) + 1)) / 2 ** n)


def interval(values):
    """The middle 95% of `values`, or None when there are none."""
    if not values:
        return None
    values = sorted(values)
    return values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]


def ratio_change(pairs):
    """The change of the first sum against the second, in percent, or None when the second is 0."""
    a, b = sum(x for x, _ in pairs), sum(y for _, y in pairs)
    return 100 * (a / b - 1) if b else None


def paired(test, a_rows, b_rows, resamples=RESAMPLES, seed=0):
    """`a` against `b` on the questions both ran: handled right in points, and tokens as a change in percent."""
    b_by_id = {r["id"]: r for r in b_rows}
    pairs = [(r, b_by_id[r["id"]]) for r in a_rows if r["id"] in b_by_id]
    right = [(handled(test, a), handled(test, b)) for a, b in pairs]
    added = [(a["context_tokens"] + a["tool_tokens"], b["context_tokens"] + b["tool_tokens"]) for a, b in pairs]
    total = [(x + a["lookup_model_tokens"], y + b["lookup_model_tokens"]) for (x, y), (a, b) in zip(added, pairs)]
    n, rng = len(pairs), random.Random(seed)
    if not n:
        return {"n": 0}
    draws = {"right": [], "added": [], "total": []}
    for _ in range(resamples):
        pick = [rng.randrange(n) for _ in range(n)]
        draws["right"].append(100 * sum(right[i][0] - right[i][1] for i in pick) / n)
        for key, pairs_ in (("added", added), ("total", total)):
            # A resample whose second run added no tokens has no ratio; it is left out of that interval.
            drawn = ratio_change([pairs_[i] for i in pick])
            if drawn is not None:
                draws[key].append(drawn)
    change = ratio_change
    return {"n": n, "right": (100 * sum(a for a, _ in right) / n, 100 * sum(b for _, b in right) / n),
            "right_change": 100 * sum(a - b for a, b in right) / n, "right_interval": interval(draws["right"]),
            "only": (sum(a and not b for a, b in right), sum(b and not a for a, b in right)),
            "added": (statistics.mean(a for a, _ in added), statistics.mean(b for _, b in added)),
            "added_change": change(added), "added_interval": interval(draws["added"]),
            "total": (statistics.mean(a for a, _ in total), statistics.mean(b for _, b in total)),
            "total_change": change(total), "total_interval": interval(draws["total"])}


def paired_table(labels, split, slice_=False):
    a, b = labels
    lines = [f"| {a} against {b} | questions | handled right | change, 95% interval | only one right, McNemar p | "
             "tokens added to the agent's prompt | change | total tokens with the check's model | change |",
             "|---|---|---|---|---|---|---|---|---|"]
    for test, where in TESTS.items():
        rows = [read(where(split, slice_, label)) for label in labels]
        if not all(rows):
            continue
        r = paired(test, *rows)
        if not r["n"]:
            lines.append(f"| {test} | 0 | no question both runs finished yet |  |  |  |  |  |  |")
            continue

        def change(key, unit="%"):
            value, bounds = r[f"{key}_change"], r[f"{key}_interval"]
            if value is None:
                return "n/a"
            return f"{value:+.1f}{unit}" + (f" ({bounds[0]:+.1f} to {bounds[1]:+.1f})" if bounds else "")
        lines.append(
            f"| {test} | {r['n']} | {r['right'][0]:.1f}% vs {r['right'][1]:.1f}% | {change('right', ' points')} | "
            f"{r['only'][0]} vs {r['only'][1]}, p {mcnemar(*r['only']):.2f} | "
            f"{r['added'][0]:,.0f} vs {r['added'][1]:,.0f} | {change('added')} | "
            f"{r['total'][0]:,.0f} vs {r['total'][1]:,.0f} | {change('total')} |")
    return "\n".join(lines)


def cell(n):
    return (f"{n['right']:.1f}% of {n['n']}, {n['added'] + n['model']:,.0f} tok "
            f"({n['added']:,.0f} + {n['model']:,.0f}), p50 {n['p50']:.1f}s, {n['errors']} err")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("labels", nargs="+")
    p.add_argument("--noise", default="noise1,noise2,noise3")
    p.add_argument("--split", choices=("dev", "test"), default="dev")
    p.add_argument("--slice", action="store_true")
    p.add_argument("--paired", action="store_true", help="the first label against the second, question by question")
    args = p.parse_args(argv)
    if args.paired:
        if len(args.labels) != 2:
            raise SystemExit("--paired takes two labels")
        print(paired_table(args.labels, args.split, args.slice))
        return 0
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
