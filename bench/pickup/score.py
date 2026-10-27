#!/usr/bin/env python3
"""Score each tool on the made-up month using only what it collects by itself.

    python3 bench/pickup/score.py

capture.json lists, for each tool setup, which of the month's sources its own plugins and connectors collect, with
the vendor's documentation and the date it was read. A question is within reach of a setup when every item in its
gold evidence comes from a source that setup collects. The verdicts are the month run's, where every tool was handed
all 471 items, read from bench/results/month/<tool>.jsonl. Writes results/rows.jsonl, one row per setup and question,
and results/table.md.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(HERE)
MONTH = os.path.join(os.path.dirname(BENCH), "demo", "month")
MONTH_RESULTS = os.path.join(BENCH, "results", "month")
OUT = os.path.join(HERE, "results")


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def score(setups, sources, questions, verdicts):
    """verdicts maps a tool's name to {question id: correct} from its month rows."""
    rows = []
    for s in setups:
        reads = set(s["reads"])
        if reads & set(s["skips"]) or reads | set(s["skips"]) != set(sources.values()):
            raise SystemExit(f"capture.json: {s['setup']} must list each month source once, under reads or skips")
        for q in questions:
            needs = sorted({sources[e] for e in q["evidence"]})
            rows.append({"setup": s["setup"], "id": q["id"], "needs": needs, "within_reach": set(needs) <= reads,
                         "verdict_from": f"bench/results/month/{s['tool']}.jsonl",
                         "correct": verdicts[s["tool"]][q["id"]]})
    return rows


def table(setups, rows):
    lines = ["| Setup | Per month | Answer within reach, of 131 | Right from what it collects | Right when handed "
             "everything |", "|---|---|---|---|---|"]
    for s in setups:
        mine = [r for r in rows if r["setup"] == s["setup"]]
        lines.append(f"| {s['setup']} | {s['per_month']} | {sum(r['within_reach'] for r in mine)} | "
                     f"{sum(r['within_reach'] and r['correct'] for r in mine)} | {sum(r['correct'] for r in mine)} |")
    sources = sorted({n for r in rows for n in r["needs"]})
    lines += ["", "Questions whose evidence needs each source. A question can need more than one.", "",
              "| Source | Questions |", "|---|---|"]
    lines += [f"| {n} | {sum(n in r['needs'] for r in rows if r['setup'] == setups[0]['setup'])} |" for n in sources]
    return "\n".join(lines) + "\n"


def main():
    sources = {i["source_id"]: i["source"] for i in read_jsonl(os.path.join(MONTH, "items.jsonl"))}
    questions = [q for q in read_jsonl(os.path.join(MONTH, "questions.jsonl")) if q["kind"] != "none"]
    with open(os.path.join(HERE, "capture.json"), encoding="utf-8") as fh:
        setups = json.load(fh)
    verdicts = {t: {r["id"]: r["correct"] for r in read_jsonl(os.path.join(MONTH_RESULTS, f"{t}.jsonl"))}
                for t in {s["tool"] for s in setups}}
    rows = score(setups, sources, questions, verdicts)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "rows.jsonl"), "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)
    text = table(setups, rows)
    with open(os.path.join(OUT, "table.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
