#!/usr/bin/env python3
"""Supermemory, Mem0 and claude-mem on month B, the held-out month, for the final head-to-head with the check.

    python3 bench/outside/month_b.py [--tools claude-mem,mem0,supermemory] [--workers 2]

Each rival runs month A's setup from bench/README.md on bench/month-b, through bench/run.py's own answer loop, judge
and row format, imported unchanged. Rows go to bench/results/hillclimb/month/b-final/<rival>.jsonl, beside the check's
final row, as each question finishes, and a rerun runs only the questions with no row yet. Run records go to
bench/outside/results/month-b-runs.json, since the rest of b-final/ belongs to the Hillclimb track. These runs are not
held-out runs of the check and are not logged as one.

The rivals run one at a time, with at most two model calls at once, under limits.py: bench/gateway.py's retries on HTTP
429 are replaced, and the OpenAI SDK's are turned off, so a rate limit stops new work on that model for 30 minutes,
then work resumes once; a second limit stops the run. Supermemory's server and claude-mem's worker make their own
model calls while they take in the month, which cannot go through these slots; nothing else runs while they do.
Set SUPERMEMORY_URL, SUPERMEMORY_KEY_FILE and CLAUDE_MEM_HOME as for month A, with claude-mem's worker started on an
empty store.
"""
import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import outside
from limits import RateLimited
from mem0_locomo import _Count

import gateway
import run
import tools
import tools.supermemory

OUT = os.path.join(run.HERE, "results", "hillclimb", "month", "b-final")
RECORD = os.path.join(HERE, "results", "month-b-runs.json")
STORES = os.path.join(outside.STORES, "month-b")
RIVALS = ("claude-mem", "mem0", "supermemory")
NAMESPACE = "month-b"
# How long claude-mem's worker may take to finish every session it was given before the run gives up on its store.
SETTLE_SECONDS = 3 * 3600


class Incomplete(RuntimeError):
    """A rival's store may be missing part of the month, so it is not scored."""


def install():
    """Every model call this process makes goes through outside.LIMITS: the answer loop, judge and token counts
    (gateway.post, without its retries) and, where the OpenAI SDK that Mem0 needs is installed, Mem0's OpenAI client
    (SDK retries off)."""
    gateway.post = outside.post
    try:
        import openai
        from openai.resources.chat.completions import Completions
    except ImportError:
        return
    init, create = openai.OpenAI.__init__, Completions.create

    def no_retries(self, *args, **kwargs):
        init(self, *args, **{**kwargs, "max_retries": 0})

    openai.OpenAI.__init__ = no_retries
    Completions.create = lambda self, **kwargs: outside.LIMITS.send(kwargs["model"], lambda: create(self, **kwargs))


def load_mem0(tool, items):
    """Mem0's store, kept outside /tmp so a restart reopens it; one built while a model was rate-limited may be
    missing memories, since the adapter counts a failed add and goes on, so it is not kept."""
    folder = os.path.join(STORES, "mem0")
    done = os.path.join(folder, "loaded.json")
    if os.path.exists(done):
        tool.load([], folder)
        with open(done, encoding="utf-8") as fh:
            return {**json.load(fh), "reused": True}
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    limited, count = sum(outside.LIMITS.seen.values()), _Count()
    logging.getLogger("mem0.memory.main").addHandler(count)
    try:
        stats = tool.load(items, folder)
    finally:
        logging.getLogger("mem0.memory.main").removeHandler(count)
    if sum(outside.LIMITS.seen.values()) > limited:
        shutil.rmtree(folder)
        raise RateLimited("Mem0 met a rate limit while taking in the month; its store was not kept")
    if stats.get("add_failures"):
        shutil.rmtree(folder)
        raise Incomplete(f"{stats['add_failures']} of Mem0's adds failed; its store was not kept")
    stats = {**stats, "extraction_parse_errors": count.n}
    with open(done, "w", encoding="utf-8") as fh:
        json.dump(stats, fh)
    return stats


def load_supermemory(tool, items):
    tools.supermemory.NAMESPACE = NAMESPACE
    stats = tool.load(items, STORES)
    if not outside.supermemory_ready(len(items)):
        raise RuntimeError("Supermemory has not extracted every item's memories; run again to wait for the rest")
    return stats


def load_claude_mem(tool, items):
    """The adapter gives each replayed session a fixed time and counts one still in the worker as unsettled, and it
    reuses any store holding the month's number of sessions, even one whose replay stopped partway. So the worker
    must finish every session it was given before any question runs, and a reused store must be the one this runner
    replayed to the end: its counts are recorded when a replay finishes and must still match."""
    from tools import claude_mem
    marker = os.path.join(STORES, "claude-mem-replayed.json")
    stats = tool.load(items, STORES)
    if not tool._drain(SETTLE_SECONDS):
        raise Incomplete("claude-mem's worker has not finished the sessions it was given; run again to wait")
    database = claude_mem.worker("GET", "/api/stats")["database"]
    counts = {k: database[k] for k in ("sessions", "observations", "summaries")}
    if stats.get("reused_store"):
        recorded = json.load(open(marker)) if os.path.exists(marker) else None
        if recorded != counts:
            raise RuntimeError("claude-mem's store was not replayed to the end by this runner; start its worker on an "
                               "empty store and run again")
    else:
        os.makedirs(STORES, exist_ok=True)
        with open(marker, "w", encoding="utf-8") as fh:
            json.dump(counts, fh)
    return {**stats, **counts, "drained": True}


LOADERS = {"claude-mem": load_claude_mem, "mem0": load_mem0, "supermemory": load_supermemory}


def waits(tool):
    """A question waits out any model's pause before its lookup, so no new work starts during one. A lookup that
    failed would leave the answer without the rival's memory, so the question gets no row and runs again."""
    retrieve = tool.retrieve

    def retrieve_after_pause(question):
        outside.LIMITS.wait()
        found = retrieve(question)
        if found.error:
            raise RuntimeError(f"the lookup failed with {found.error}")
        return found

    tool.retrieve = retrieve_after_pause
    return tool


def run_rival(name, items, questions, workers):
    outside.LIMITS.wait()
    tool = tools.make(name)
    started = time.monotonic()
    try:
        stats = LOADERS[name](tool, items)
    except (RateLimited, Incomplete):
        outside.LIMITS.wait()
        stats = LOADERS[name](tool, items)
    stats = {**stats, "load_seconds": round(time.monotonic() - started)}
    print(f"{name}: loaded {json.dumps(stats)}", file=sys.stderr)
    passes = [run.run_tool(name, waits(tool), stats, questions, OUT, workers, False, run.MONTH_SETUP)]
    if passes[-1]["failed"] and not outside.LIMITS.stopped:
        outside.LIMITS.wait()
        passes.append(run.run_tool(name, tool, stats, questions, OUT, workers, False, run.MONTH_SETUP))
    return {"tool": name, "load": stats, "passes": [{k: p[k] for k in ("ran", "failed")} for p in passes]}


def record(entry):
    runs = json.load(open(RECORD))["runs"] if os.path.exists(RECORD) else []
    with open(RECORD, "w", encoding="utf-8") as fh:
        json.dump({"runs": runs + [entry]}, fh, indent=1)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tools", default=",".join(RIVALS))
    p.add_argument("--workers", type=int, default=outside.Limits.PARALLEL, help="at most 2: the plans' limit per run")
    args = p.parse_args(argv)
    names = args.tools.split(",")
    if set(names) - set(RIVALS):
        raise SystemExit(f"month_b.py runs only the rivals: {', '.join(RIVALS)}")
    install()
    items = run.read_jsonl(os.path.join(run.MONTHS["b"], "items.jsonl"))
    questions = run.read_jsonl(os.path.join(run.MONTHS["b"], "questions.jsonl"))
    commit = subprocess.run(["git", "log", "-1", "--format=%H", "--", run.MONTHS["b"]], capture_output=True,
                            text=True, cwd=run.ROOT).stdout.strip()
    os.makedirs(OUT, exist_ok=True)
    failed = 0
    for name in names:
        started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            entry = run_rival(name, items, questions, min(args.workers, outside.Limits.PARALLEL))
        except Exception as exc:
            entry = {"tool": name, "error": f"{type(exc).__name__}: {exc}"}
            print(f"{name}: {entry['error']}", file=sys.stderr)
        failed += bool(entry.get("error")) or sum(p["failed"] for p in entry.get("passes", [])[-1:])
        record({**entry, "started": started, "month_commit": commit, "answer_model": run.ANSWER_MODEL,
                "judge_model": run.MONTH_SETUP.judge, "parallel_model_calls": outside.Limits.PARALLEL,
                "rate_limits": dict(outside.LIMITS.seen), "stopped": outside.LIMITS.stopped})
        if outside.LIMITS.stopped:
            break
    for name in names:
        path = os.path.join(OUT, f"{name}.jsonl")
        if os.path.exists(path):
            print(name, json.dumps(run.summarize(run.read_jsonl(path))))
    return 1 if failed or outside.LIMITS.stopped else 0


if __name__ == "__main__":
    sys.exit(main())
