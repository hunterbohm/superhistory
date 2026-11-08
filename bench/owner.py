#!/usr/bin/env python3
"""Owner questions: is an MCP server a good idea (Part A), and are stronger-model facts or notes worth it (Part B).

    python3 bench/owner.py part-a [--arms push-pull,pull,pull-lookup,push] [--limit N]
    python3 bench/owner.py part-b [--arms haiku,sonnet,notes,facts] [--limit N]
    python3 bench/owner.py tables

Both parts run on month A, the dev month. Rows go to bench/results/owner-questions/<part>/<arm>.jsonl, one per
question, and a rerun runs only the questions with no row. Every model call is added to
bench/results/owner-questions/ledger.jsonl with the stage that made it. The first rate limit stops the run with exit
status 3 and keeps every row; running the same command again resumes it. OWNER_SLOTS=1 holds a run to one call at a
time. Stores live under OWNER_SCRATCH (default ~/.cache/superhistory-bench/owner-questions) and are kept between runs, since a
new store would hold different summaries; `clean` removes them.
"""
import argparse
import atexit
import collections
import json
import math
import os
import shutil
import statistics
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import compare
import gateway
import owner_store
import run
from tools import check as ct, owner as arms

OUT = os.path.expanduser(os.environ.get("OWNER_OUT") or os.path.join(HERE, "results", "owner-questions"))
SCRATCH = os.path.expanduser(os.environ.get("OWNER_SCRATCH", "~/.cache/superhistory-bench/owner-questions"))
SUMMARY_MODEL = "claude-haiku-5-5"
PARTS = {"part-a": ("push-pull", "pull", "pull-lookup", "push"),
         "part-b": ("haiku", "sonnet", "notes", "facts", "facts-all")}
LIMITED_EXIT = 3
MONTH_ITEMS = 471


class Ledger:
    """Every model call, by stage: the model, the input (cached part included) and output tokens. Calls that fail or
    are cut off before they return usage are not in it."""

    def __init__(self):
        self.stage, self.lock, self.counts, self.path = "setup", threading.Lock(), collections.Counter(), None

    def start(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atexit.register(self.flush)

    def at(self, stage):
        self.flush()
        self.stage = stage

    def record(self, model, usage):
        usage = usage or {}
        given = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                "cache_read_input_tokens"))
        line = {"stage": self.stage, "model": model, "in": given, "out": usage.get("output_tokens") or 0,
                "think": (usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0}
        with self.lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")

    def counted(self):
        with self.lock:
            self.counts[self.stage] += 1

    def flush(self):
        with self.lock:
            if self.path and self.counts:
                with open(self.path, "a", encoding="utf-8") as fh:
                    for stage, n in self.counts.items():
                        fh.write(json.dumps({"stage": stage, "model": "count_tokens", "calls": n}) + "\n")
            self.counts.clear()


LEDGER = Ledger()


def install_ledger():
    """Route every model call through the ledger: the answer and judge calls through gateway.post, the check's and the
    summarizer's through superhistory's plug-in."""
    post = gateway.post

    def ledgered(path, body, timeout=300):
        response = post(path, body, timeout)
        if path.endswith("count_tokens"):
            LEDGER.counted()
        else:
            LEDGER.record(body.get("model"), response.get("usage"))
        return response

    gateway.post = ledgered
    kb_post = ct.api.post

    def kb_ledgered(url, body, headers, timeout):
        response = kb_post(url, body, headers, timeout)
        LEDGER.record(response.get("model") or body.get("model"), response.get("usage"))
        return response

    ct.api.post = kb_ledgered
    LEDGER.start(os.path.join(OUT, "ledger.jsonl"))


def run_one(tool, q, setup):
    """bench/run.py's run_one, plus the seconds to an answer, the tool calls' model tokens and whether any tool call
    showed the agent a record."""
    gateway.check_limited()
    tool.begin()
    started = time.monotonic()
    found = tool.retrieve(q)
    text, calls, shown, printed = run.answer(tool, q["question"], found.context, setup.system)
    answer_seconds = time.monotonic() - started
    verdict = run.grade(setup, q, text)
    extra = tool.end()
    lookup_error = found.error or (f"lookup tool timed out {extra['lookup_errors']}x" if extra["lookup_errors"] else None)
    return {"id": q["id"], "kind": q["kind"], "question": q["question"], "gold": q["answer"],
            "evidence": q["evidence"], "context": found.context, "context_items": found.items, "query": found.query,
            "lookup_seconds": round(found.seconds, 3), "lookup_error": lookup_error,
            "lookup_model_tokens": found.model_tokens + extra["model_tokens"],
            "lookup_model_detail": found.model_detail,
            "context_tokens": gateway.count_tokens(run.ANSWER_MODEL, found.context),
            "tool_calls": calls, "read_items": shown, "tool_tokens": sum(c["tokens"] for c in calls),
            "tool_model_tokens": extra["model_tokens"], "tool_model_calls": extra["model_calls"],
            "records_from_tools": bool(shown), "answer_seconds": round(answer_seconds, 3),
            "answer": text, "correct": bool(verdict.get("correct")), "judge_reason": verdict.get("reason")}


run.run_one = run_one


def read(path):
    return run.read_jsonl(path) if os.path.exists(path) else []


def month_a():
    return (run.read_jsonl(os.path.join(run.MONTH, "items.jsonl")),
            run.read_jsonl(os.path.join(run.MONTH, "questions.jsonl")))


def stored(name, build):
    """The store `name` and its stats: built once into SCRATCH/stores/<name>, with stats.json marking it whole, and
    reused by every later run. A build a rate limit stopped is resumed: summaries already written stay."""
    folder = os.path.join(SCRATCH, "stores", name)
    marker = os.path.join(folder, "stats.json")
    if os.path.exists(marker):
        with open(marker, encoding="utf-8") as fh:
            return ct.Store(owner_store.Paths.under(folder)), json.load(fh)
    LEDGER.at(f"{name}:ingest")
    made, stats = build(folder)
    with open(marker, "w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=1)
    return made, stats


def questions_for(arm, tool, store_, stats, items, questions, out, workers, limit):
    LEDGER.at(f"{out.rsplit('/', 1)[-1]}:{arm}:questions")
    tool.store = store_
    return run.run_tool(arm, tool, stats, questions[:limit], out, workers, False, run.MONTH_SETUP)


def tool_definition_tokens(tool):
    return arms.definition_tokens(tool.agent_tools, run.ANSWER_MODEL)


def part_a(args):
    items, questions = month_a()
    out = os.path.join(OUT, "part-a")
    os.makedirs(out, exist_ok=True)
    base, base_stats = stored("a-haiku", lambda folder: owner_store.build(items, folder, SUMMARY_MODEL))
    ran = []
    for arm in args.arms:
        tool = arms.ARMS[arm]()
        LEDGER.at(f"part-a:{arm}:setup")
        info = {"arm": arm, "tool_definition_tokens": tool_definition_tokens(tool)}
        ran.append({**questions_for(arm, tool, base, info, items, questions, out, args.workers, args.limit), **info})
    write_meta(out, ran, base_stats, args.workers)
    return finish(ran)


def part_b(args):
    items, questions = month_a()
    out = os.path.join(OUT, "part-b")
    os.makedirs(out, exist_ok=True)
    ran, built = [], {}
    for arm in args.arms:
        if arm == "haiku" or arm == "sonnet":
            model = SUMMARY_MODEL if arm == "haiku" else "claude-sonnet-5-5"
            built[arm] = stored(f"b-{arm}", lambda folder, m=model: owner_store.build(items, folder, m))
            tool = arms.PushPull()
        elif arm == "notes":
            base, base_stats = stored("b-haiku", lambda folder: owner_store.build(items, folder, SUMMARY_MODEL))

            def with_notes(folder):
                copy = owner_store.copy_store(base, folder)
                stats = owner_store.add_notes(copy)
                return copy, {"summaries_and_atoms": base_stats, "notes": stats}
            built[arm] = stored("b-notes", with_notes)
            tool = arms.PushPull()
        elif arm == "facts-all":
            notes_store, _ = stored("b-notes", lambda folder: (_ for _ in ()).throw(
                SystemExit("run the notes arm first: this store is cut from it")))

            def facts_all(folder):
                copy = owner_store.copy_store(notes_store, folder)
                more = owner_store.add_notes(copy, others=True)
                cut, stats = owner_store.facts_only(copy, os.path.join(folder, "cut"))
                shutil.copyfile(cut.paths.db, copy.paths.db)
                shutil.rmtree(os.path.join(folder, "cut"))
                return copy, {**stats, "built_from": "b-notes", "notes_for_every_other_item": more}
            built[arm] = stored("b-facts-all", facts_all)
            tool = arms.Facts()
        else:
            notes_store, notes_stats = stored("b-notes", lambda folder: (_ for _ in ()).throw(
                SystemExit("run the notes arm first: the facts-only store is cut from it")))

            def facts(folder):
                copy, stats = owner_store.facts_only(notes_store, folder)
                return copy, {**stats, "built_from": "b-notes", "model_calls_added": 0}
            built[arm] = stored("b-facts", facts)
            tool = arms.Facts()
        store_, stats = built[arm]
        ran.append({**questions_for(arm, tool, store_, stats, items, questions, out, args.workers, args.limit),
                    "store": stats})
    write_meta(out, ran, None, args.workers)
    return finish(ran)


def write_meta(out, ran, base_stats, workers):
    path = os.path.join(out, "run.json")
    earlier = json.load(open(path)).get("arms", []) if os.path.exists(path) else []
    names = {a["tool"] for a in ran}
    meta = {"date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "month": "a (dev)",
            "answer_model": run.ANSWER_MODEL, "judge_model": run.JUDGE_MODEL, "summary_model": SUMMARY_MODEL,
            "workers": workers,
            "slots": gateway.SLOTS._initial_value, "check_commit": run.git_head(),
            "arms": [a for a in earlier if a["tool"] not in names] + ran}
    if base_stats:
        meta["shared_store"] = base_stats
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)


def finish(ran):
    failed = sum(a["failed"] for a in ran)
    if gateway.LIMITED.is_set():
        print(f"rate limited; {failed} questions have no row. Wait 30 minutes and run the same command once.",
              file=sys.stderr)
        return LIMITED_EXIT
    if failed:
        print(f"{failed} questions failed and have no row; run the same command again to retry them", file=sys.stderr)
    return 1 if failed else 0


# Tables.

def quiet(row, strict):
    return not row["context"] and not (strict and row.get("records_from_tools"))


def handled(row, strict=False):
    return row["correct"] and (row["kind"] != "none" or quiet(row, strict))


def pct(part, whole):
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def pctile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else 0.0


def numbers(rows):
    answerable = [r for r in rows if r["kind"] != "none"]
    none = [r for r in rows if r["kind"] == "none"]
    called = [r for r in rows if r["tool_calls"] or r.get("tool_model_calls")]
    return {
        "n": len(rows),
        "handled": sum(handled(r) for r in rows), "handled_strict": sum(handled(r, True) for r in rows),
        "answerable_right": sum(r["correct"] for r in answerable), "answerable": len(answerable),
        "none_right": sum(r["correct"] for r in none), "none": len(none),
        "none_quiet": sum(quiet(r, False) for r in none), "none_quiet_strict": sum(quiet(r, True) for r in none),
        "added": statistics.mean(r["context_tokens"] + r["tool_tokens"] for r in rows),
        "model": statistics.mean(r["lookup_model_tokens"] for r in rows),
        "p50": pctile([r["answer_seconds"] for r in rows], 0.5), "mean_s": statistics.mean(r["answer_seconds"] for r in rows),
        "p90": pctile([r["answer_seconds"] for r in rows], 0.9),
        "called": len(called), "calls": statistics.mean(len(r["tool_calls"]) for r in rows),
        "errors": sum(bool(r["lookup_error"]) for r in rows),
    }


def strict_rows(rows):
    """The rows with a tool call that showed records counted as context, so compare.handled sees it as not quiet."""
    return [{**r, "context": r["context"] or ("records from tools" if r.get("records_from_tools") else "")} for r in rows]


def paired_cell(a, b, strict=False):
    if strict:
        a, b = strict_rows(a), strict_rows(b)
    r = compare.paired("month", a, b)
    lo, hi = r["right_interval"]
    return (f"{r['right_change']:+.1f} ({lo:+.1f} to {hi:+.1f})", f"{r['only'][0]} vs {r['only'][1]}",
            f"{compare.mcnemar(*r['only']):.3f}", r)


def table(out, arm_names, labels, reference, extra_cols=None):
    rows = {a: read(os.path.join(out, f"{a}.jsonl")) for a in arm_names}
    rows = {a: r for a, r in rows.items() if r}
    num = {a: numbers(r) for a, r in rows.items()}
    lines = ["| Arm | Questions | Handled right | Right, of the answerable | Quiet on the nothing-to-find | "
             "Mean tokens added | Latency to an answer, p50 / mean | Questions with a tool call | Mean tool calls |",
             "|---|---|---|---|---|---|---|---|---|"]
    for a, n in num.items():
        lines.append(
            f"| {labels[a]} | {n['n']} | {n['handled']} ({pct(n['handled'], n['n'])}) | "
            f"{n['answerable_right']} of {n['answerable']} ({pct(n['answerable_right'], n['answerable'])}) | "
            f"{n['none_quiet']} of {n['none']} ({pct(n['none_quiet'], n['none'])}) | {n['added']:,.0f} | "
            f"{n['p50']:.1f} s / {n['mean_s']:.1f} s | {pct(n['called'], n['n'])} | {n['calls']:.2f} |")
    lines += ["", "Handled right counts a correct answer and, where nothing fits, nothing added before the message "
              "(bench/run.py's rule). Where an agent can pull, the strict columns below also count a tool call that "
              "showed it records as added.", "",
              "| Arm | Handled right, strict | Quiet on the nothing-to-find, strict | Said it did not know or answered "
              "correctly, of the nothing-to-find | Mean model tokens of lookups | Lookup errors |",
              "|---|---|---|---|---|---|"]
    for a, n in num.items():
        lines.append(f"| {labels[a]} | {n['handled_strict']} ({pct(n['handled_strict'], n['n'])}) | "
                     f"{n['none_quiet_strict']} of {n['none']} ({pct(n['none_quiet_strict'], n['none'])}) | "
                     f"{n['none_right']} of {n['none']} | {n['model']:,.0f} | {n['errors']} |")
    if reference in rows:
        lines += ["", f"Each arm against {labels[reference]}, question by question. Change is handled right in "
                  "points, arm minus reference, with a 95% interval from 10,000 paired resamples; the McNemar column "
                  "counts the questions only the arm got right against those only the reference got right.", "",
                  "| Arm | Questions | Handled right change (95% interval) | Only the arm right vs only the reference | "
                  "McNemar p | Strict: change (95% interval) | Strict: only the arm vs only the reference | "
                  "Strict: McNemar p | Right of the answerable, change (95% interval) | Only the arm vs only the "
                  "reference | McNemar p | Tokens added change |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for a in rows:
            if a == reference:
                continue
            ch, only, p, r = paired_cell(rows[a], rows[reference])
            sch, sonly, sp, _ = paired_cell(rows[a], rows[reference], True)
            ans = [(x, y) for x in rows[a] for y in rows[reference] if x["id"] == y["id"] and x["kind"] != "none"]
            ar = answerable_pair(rows[a], rows[reference])
            lines.append(f"| {labels[a]} | {r['n']} | {ch} | {only} | {p} | {sch} | {sonly} | {sp} | {ar[0]} | "
                         f"{ar[1]} | {ar[2]} | {r['added_change']:+.0f}% ({r['added_interval'][0]:+.0f} to "
                         f"{r['added_interval'][1]:+.0f}) |")
    return "\n".join(lines) + "\n"


def answerable_pair(a_rows, b_rows):
    """Right of the answerable, the first arm against the second: change in points, interval, only-one counts, p."""
    import random
    b_by = {r["id"]: r for r in b_rows}
    pairs = [(r["correct"], b_by[r["id"]]["correct"]) for r in a_rows if r["id"] in b_by and r["kind"] != "none"]
    n, rng = len(pairs), random.Random(0)
    draws = sorted(100 * sum(pairs[i][0] - pairs[i][1] for i in (rng.randrange(n) for _ in range(n))) / n
                   for _ in range(compare.RESAMPLES))
    only = (sum(a and not b for a, b in pairs), sum(b and not a for a, b in pairs))
    change = 100 * sum(a - b for a, b in pairs) / n
    lo, hi = draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]
    return f"{change:+.1f} ({lo:+.1f} to {hi:+.1f})", f"{only[0]} vs {only[1]}", f"{compare.mcnemar(*only):.3f}"


def ledger_totals(stage_prefix=None):
    """Calls, input and output tokens by (stage, model) from the ledger."""
    total = collections.defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "think": 0})
    for line in read(os.path.join(OUT, "ledger.jsonl")):
        if stage_prefix and not line["stage"].startswith(stage_prefix):
            continue
        t = total[(line["stage"], line["model"])]
        t["calls"] += line.get("calls", 1)
        t["in"] += line.get("in", 0)
        t["out"] += line.get("out", 0)
        t["think"] += line.get("think", 0)
    return total


LABELS = {"push-pull": "1. Push and pull (ships today)", "pull": "2. Pull only (search, get)",
          "pull-lookup": "3. Pull with lookup (search, get, lookup)", "push": "4. Push only (cards, no tools)",
          "haiku": "1. Shipped: haiku summaries and atoms", "sonnet": "2. Sonnet summaries and atoms",
          "notes": "3. Haiku summaries and atoms, plus sonnet notes", "facts": "4. Facts only (atoms and notes)",
          "facts-all": "4b. Facts only, notes for every item (beyond the brief)"}


def ingest_table():
    """Model tokens spent at ingest by each Part B arm, from the ledger, and the estimate per 1,000 items."""
    t = ledger_totals()

    def spent(stage):
        mine = [v for (s, m), v in t.items() if s == stage and m != "count_tokens"]
        return {k: sum(v[k] for v in mine) for k in ("calls", "in", "out")}
    haiku, sonnet, notes = spent("b-haiku:ingest"), spent("b-sonnet:ingest"), spent("b-notes:ingest")
    others = spent("b-facts-all:ingest")
    both = {k: haiku[k] + notes[k] for k in haiku}
    rows = [("1. Haiku summaries and atoms", "claude-haiku-5-5", haiku, ""),
            ("2. Sonnet summaries and atoms", "claude-sonnet-5-5", sonnet, ""),
            ("3. Notes (sonnet), on top of arm 1", "claude-sonnet-5-5", notes, "81 agent sessions"),
            ("3. Arm 1 and the notes together", "haiku and sonnet", both, ""),
            ("4. Facts only, cut from arm 3's store", "none beyond arm 3", both, "no calls of its own"),
            ("4b. Notes for the other 390 items (sonnet)", "claude-sonnet-5-5", others, ""),
            ("4b. Arm 3 and these notes together", "haiku and sonnet",
             {k: both[k] + others[k] for k in both}, "")]
    lines = ["| Ingest step | Model | Calls | Input tokens | Output tokens | Total tokens | Total per 1,000 items "
             f"(of the month's {MONTH_ITEMS}) |", "|---|---|---|---|---|---|---|"]
    for name, model, v, note in rows:
        total = v["in"] + v["out"]
        lines.append(f"| {name} | {model} | {v['calls']} | {v['in']:,} | {v['out']:,} | {total:,} | "
                     f"{total * 1000 / MONTH_ITEMS:,.0f} |")
    return "\n".join(lines) + "\n"


def tables():
    for part, reference in (("part-a", "push-pull"), ("part-b", "haiku")):
        out = os.path.join(OUT, part)
        if not os.path.isdir(out):
            continue
        text = table(out, PARTS[part], LABELS, reference)
        if part == "part-a":
            meta = json.load(open(os.path.join(out, "run.json")))
            text += "\nTool definitions add, per request, by the gateway's token count: " + ", ".join(
                f"{LABELS[a['tool']]}: {a['tool_definition_tokens']}" for a in meta["arms"]) + " tokens.\n"
        else:
            text += "\nModel tokens spent at ingest.\n\n" + ingest_table()
        with open(os.path.join(out, "table.md"), "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"## {part}\n{text}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in PARTS:
        s = sub.add_parser(name)
        s.add_argument("--arms", default=",".join(PARTS[name]))
        s.add_argument("--limit", type=int)
        s.add_argument("--workers", type=int, default=int(os.environ.get("OWNER_WORKERS", "1")))
    sub.add_parser("tables", help="write part-a/table.md and part-b/table.md from the rows")
    sub.add_parser("clean", help="remove the scratch stores")
    args = p.parse_args(argv)
    if args.cmd == "clean":
        owner_store.remove(os.path.join(SCRATCH, "stores"))
        return 0
    if args.cmd == "tables":
        tables()
        return 0
    args.arms = args.arms.split(",")
    gateway.require()
    slots = int(os.environ.get("OWNER_SLOTS", "2"))
    gateway.SLOTS = threading.BoundedSemaphore(slots)
    install_ledger()
    try:
        return {"part-a": part_a, "part-b": part_b}[args.cmd](args)
    except gateway.RateLimited as exc:
        print(exc, file=sys.stderr)
        return LIMITED_EXIT


if __name__ == "__main__":
    sys.exit(main())
