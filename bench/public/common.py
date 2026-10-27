"""What the public tests share: data download, one scratch store per haystack, the answer loop, and the rows file.

A case is one question and the haystack it runs against. Cases that share a haystack share a store, which is built
on first use and deleted when the last of them is done. Each finished case appends one row to the rows file, so a
rerun runs only the cases with no row yet.
"""
import hashlib
import json
import os
import random
import sys
import tempfile
import threading
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import NamedTuple

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BENCH)

import gateway
import run
from tools import check

RESULTS = os.path.join(BENCH, "results", "public")
DATA = os.path.expanduser(os.environ.get("BENCH_DATA", "~/.cache/kb-bench/data"))
# Past this many summary calls, a set runs without summaries or on a labelled sample with them.
MAX_SUMMARY_CALLS = 3000
SOURCE = "chat"
# A final reply with no text is no answer. The first runs got one on about 1 question in 60: the answer model ended
# its turn after a thinking block, and asking the same thing again got the same.
EMPTY_RETRIES = 2
# The published public rows ran with 4 tool turns; the month runs allow 8.
TOOL_TURNS = 4
NUDGE = "Reply with your answer now."


class Case(NamedTuple):
    id: str
    group: str          # the haystack this question runs against
    kind: str           # the test's question type or category
    question: str
    gold: str
    today: str          # the date the answer model is told
    evidence: tuple     # source_ids that hold the answer


def fetch(url, name, sha256):
    """The path of a cached download, fetched once and checked against its published hash."""
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        print(f"downloading {url}", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=600) as resp, open(path + ".part", "wb") as fh:
            while block := resp.read(1 << 20):
                fh.write(block)
        os.replace(path + ".part", path)
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(1 << 20):
            digest.update(block)
    if digest.hexdigest() != sha256:
        raise SystemExit(f"{path} has sha256 {digest.hexdigest()}, expected {sha256}; delete it to download again")
    return path


def summarized_sources():
    """The check summarizes long items only from the sources its summarizer lists. Chat history is not one of them
    as shipped, so the runs add it with the agent-session settings."""
    _, _, summarize = check.modules()
    summarize.SOURCES.setdefault(SOURCE, None)


def long_items(items):
    """How many of the items the check stores as chunks, which is what gets one summary call each."""
    kb, _, _ = check.modules()
    return sum(len(kb.chunk(kb.mask(kb.clean(i["content"])))) > 1 for i in items)


def plan(cases, groups):
    """Model calls a run of these cases would make, before any is made."""
    _, context, _ = check.modules()
    used = {c.group for c in cases}
    searched = sum(context.worth_asking(c.question, []) for c in cases)
    return {"questions": len(cases), "stores": len(used),
            "summary calls (one per long item)": sum(long_items(groups[g]) for g in used),
            "check calls (at most 2 per question it searches for)": 2 * searched,
            "questions the check skips as too short": len(cases) - searched,
            "answer calls (1 to 5 per question)": f"{len(cases)} to {5 * len(cases)}",
            "judge calls": len(cases)}


def choose(cases, sample, seed, limit):
    if sample:
        picked = set(random.Random(seed).sample(sorted(c.id for c in cases), sample))
        cases = [c for c in cases if c.id in picked]
    return cases[:limit] if limit else cases


def run_name(base, summaries, sample, seed, limit):
    name = f"{base}-{'with' if summaries else 'without'}-summaries"
    if sample:
        name += f"-sample{sample}-seed{seed}"
    if limit:
        name += f"-first{limit}"
    return name


def run_names(out):
    return sorted(f[:-6] for f in os.listdir(out) if f.endswith(".jsonl"))


def read_rows(path):
    return run.read_jsonl(path) if os.path.exists(path) else []


class Stores:
    """One scratch store per group, built on first use and deleted when its last case is done."""

    def __init__(self, groups, cases, workdir, summaries):
        self.items, self.workdir, self.summaries = groups, workdir, summaries
        self.left = Counter(c.group for c in cases)
        self.locks = {g: threading.Lock() for g in self.left}
        self.built = {}

    def path(self, group):
        return os.path.join(self.workdir, hashlib.sha1(group.encode()).hexdigest()[:16] + ".sqlite")

    def get(self, group):
        with self.locks[group]:
            if group not in self.built:
                started = time.monotonic()
                store, stats = check.Store.build(self.items[group], self.path(group), self.summaries)
                self.built[group] = store, {**stats, "items": len(self.items[group]),
                                            "build_seconds": round(time.monotonic() - started, 1)}
            return self.built[group]

    def done(self, group):
        with self.locks[group]:
            self.left[group] -= 1
            if self.left[group] == 0:
                self.built.pop(group, None)
                for suffix in ("", "-wal", "-shm"):
                    if os.path.exists(self.path(group) + suffix):
                        os.remove(self.path(group) + suffix)


def ask(body, empty):
    """The model's reply. A reply with neither text nor a tool call is no answer, so it is asked again, with a last
    line that asks for the answer."""
    for _ in range(EMPTY_RETRIES + 1):
        response = gateway.post("/v1/messages", body)
        if gateway.text(response) or any(b.get("type") == "tool_use" for b in response["content"]):
            return response
        empty.append({"stop_reason": response.get("stop_reason"),
                      "content": [b.get("type") for b in response["content"]]})
        body = {**body, "messages": nudged(body["messages"])}
    return response


def nudged(messages):
    last = messages[-1]
    content = last["content"] if isinstance(last["content"], list) else [{"type": "text", "text": last["content"]}]
    if content[-1].get("text") == NUDGE:
        return messages
    return messages[:-1] + [{**last, "content": content + [{"type": "text", "text": NUDGE}]}]


def answer(store, system, question, context):
    """bench/run.py's answer loop, with the system prompt passed in and empty replies asked again: the answer
    model's reply, the tool calls it made, the source_ids they showed, how many characters they printed, and the
    empty replies it gave."""
    messages = [{"role": "user", "content": f"{context}\n\n{question}" if context else question}]
    calls, shown, chars, empty = [], [], 0, []
    for turn in range(TOOL_TURNS + 1):
        body = {"model": run.ANSWER_MODEL, "max_tokens": 2000, "system": system, "messages": messages,
                "tools": list(check.AGENT_TOOLS)}
        if turn == TOOL_TURNS:
            body["tool_choice"] = {"type": "none"}
        response = ask(body, empty)
        uses = [b for b in response["content"] if b.get("type") == "tool_use"]
        if not uses:
            return gateway.text(response), calls, shown, chars, empty
        messages.append({"role": "assistant", "content": response["content"]})
        results = []
        for use in uses:
            out, ids = store.call(use["name"], use.get("input") or {})
            calls.append({"name": use["name"], "input": use.get("input")})
            shown += [i for i in ids if i not in shown]
            chars += len(out)
            results.append({"type": "tool_result", "tool_use_id": use["id"], "content": out})
        messages.append({"role": "user", "content": results})
    raise RuntimeError("the answer model kept calling tools")


def run_case(stores, case, system, score):
    store, stats = stores.get(case.group)
    found = store.lookup(case.question)
    text, calls, shown, chars, empty = answer(store, system(case), case.question, found.context)
    return {"id": case.id, "group": case.group, "kind": case.kind, "question": case.question, "gold": case.gold,
            "today": case.today, "evidence": list(case.evidence), "store": stats, "query": found.query,
            "context": found.context, "context_items": found.items, "lookup_seconds": round(found.seconds, 3),
            "lookup_error": found.error, "lookup_model_tokens": found.model_tokens, "tool_calls": calls,
            "read_items": shown, "tool_chars": chars, "empty_replies": empty, "answer": text, **score(case, text)}


def run_cases(name, cases, groups, out, system, score, summaries, workers, fresh, meta):
    """Run every case with no row yet in out/<name>.jsonl, appending a row as each one finishes."""
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"{name}.jsonl")
    if fresh and os.path.exists(path):
        os.remove(path)
    done = {r["id"] for r in read_rows(path)}
    todo = [c for c in cases if c.id not in done]
    if summaries:
        summarized_sources()
    started, lock, failures = time.monotonic(), threading.Lock(), []
    print(f"{name}: {len(todo)} of {len(cases)} questions to run", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="bench-public-") as workdir:
        stores = Stores(groups, todo, workdir, summaries)

        def one(case):
            try:
                return run_case(stores, case, system, score)
            finally:
                stores.done(case.group)

        with ThreadPoolExecutor(workers) as pool, open(path, "a", encoding="utf-8") as fh:
            futures = {pool.submit(one, c): c for c in todo}
            for n, future in enumerate(as_completed(futures), 1):
                case = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    failures.append(f"{case.id}: {type(exc).__name__}: {exc}")
                    print(f"{name} {failures[-1]}", file=sys.stderr)
                    continue
                with lock:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                if n % 25 == 0:
                    print(f"{name}: {n}/{len(todo)} in {time.monotonic() - started:.0f}s", file=sys.stderr)
    record = {**meta, "started": datetime.fromtimestamp(time.time() - (time.monotonic() - started), timezone.utc)
              .strftime("%Y-%m-%dT%H:%M:%SZ"), "seconds": round(time.monotonic() - started),
              "summaries": summaries, "answer_model": run.ANSWER_MODEL, "check_code": check.code_id(),
              "check_prompts": "the product's wording, kb/check.py on repo/package at 18a4335",
              "workers": workers, "ran": len(todo) - len(failures), "failed": len(failures),
              "failures": failures[:50]}
    meta_path = os.path.join(out, f"{name}.run.json")
    runs = json.load(open(meta_path))["runs"] if os.path.exists(meta_path) and not fresh else []
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump({"runs": runs + [record]}, fh, indent=1)
    return record


def exit_status(record):
    """1 when any question failed and has no row, so a caller can't take a partial run for a whole one."""
    if record["failed"]:
        print(f"{record['failed']} questions failed and have no row; run the same command again to retry them",
              file=sys.stderr)
    return 1 if record["failed"] else 0


def pct(part, whole):
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def work_checks(rows):
    """Did the work happen: stores built, lookups searched and returned, the agent read, the summaries ran."""
    stores = {r["group"]: r["store"] for r in rows}.values()
    answered = [r for r in rows if r["evidence"]]
    seen_lookup = sum(bool(set(r["evidence"]) & set(r["context_items"] or [])) for r in answered)
    seen_any = sum(bool(set(r["evidence"]) & set((r["context_items"] or []) + r["read_items"])) for r in answered)
    timed = sorted(r["lookup_seconds"] for r in rows if r["query"])
    return {
        "rows": len(rows),
        "stores": len(stores),
        "empty stores": sum(not s["rows"] for s in stores),
        "items summarized": sum(s["summarized"] for s in stores),
        "summary failures": sum(s["summary_failures"] for s in stores),
        "check searched": sum(bool(r["query"]) for r in rows),
        "check made no search": sum(not r["query"] and not r["lookup_error"] for r in rows),
        "lookup errors": sum(bool(r["lookup_error"]) for r in rows),
        "lookup added records": sum(bool(r["context"]) for r in rows),
        "searched, added nothing": sum(bool(r["query"]) and not r["context"] for r in rows),
        "answers that ran kb tools": sum(bool(r["tool_calls"]) for r in rows),
        "empty replies asked again": sum(len(r.get("empty_replies", [])) for r in rows),
        "empty answers": sum(not r["answer"].strip() for r in rows),
        "evidence in lookup": pct(seen_lookup, len(answered)),
        "evidence in lookup or tools": pct(seen_any, len(answered)),
        "lookup p50 s": f"{timed[len(timed) // 2]:.1f}" if timed else "n/a",
    }


def markdown(head, lines):
    return "\n".join(["| " + " | ".join(head) + " |", "|---" * len(head) + "|"] +
                     ["| " + " | ".join(str(c) for c in line) + " |" for line in lines])


def rivals(test):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rivals.json"), encoding="utf-8") as fh:
        return [r for r in json.load(fh) if r["test"] == test]
