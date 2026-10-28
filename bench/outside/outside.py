#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["nltk==3.9.1", "numpy==2.1.3", "regex==2024.11.6"]
# ///
"""Two outside tests of memory in conversation, both built on LoCoMo's ten conversations by other labs.

    python3 bench/outside/outside.py [--tests implicit,op] [--tools none,oracle,check,mem0,supermemory]
                                 [--groups conv-26,...] [--workers N] [--table]

implicit: LoCoMo-Conv (Chang and Chen, EMNLP 2026 Findings), its "implicit" style. The user never asks a question;
the reply should bring up the fact from past chats anyway. Graded 0, 0.5 or 1 with the paper's fact-used judge.

op: OP-Bench (Hu et al., 2026). Messages where using memory makes the reply worse: off-topic, flattering a false
premise, or the same reply to different messages. Graded with the paper's judges and its repetition score.

Each conversation gets one store per tool, loaded from the same items as bench/public/locomo.py, and both tests'
messages for that conversation run against it. Each message goes through the tool's lookup, the answer model replies
from the test's own prompt with the lookup's text in the memory slot and the tool's agent tools available, and the
test's judge grades the reply. The tests' data, prompts and judges are downloaded at pinned commits on first use and
never copied into this repository. Rows go to bench/outside/results/<test>/<tool>.jsonl, one per message; a rerun
runs only messages with no row yet. Run it with the Python that has the rivals' packages (see bench/README.md).
"""
import argparse
import ast
import faulthandler
import functools
import gzip
import hashlib
import json
import os
import random
import re
import signal
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from datetime import datetime, timezone
from typing import NamedTuple

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(HERE)
sys.path[:0] = [BENCH, os.path.join(BENCH, "public")]

import common
import gateway
import locomo
import product
import run
import tools
import tools.supermemory
from tools import check
from limits import Limits, RateLimited
from mem0_locomo import LocomoMem0

OUT = os.path.join(HERE, "results")
# Mem0's stores take the longest to build, so they are kept between runs; see mem0_locomo.py.
STORES = os.path.expanduser(os.environ.get("BENCH_OUTSIDE_STORES", "~/.cache/kb-bench/outside-stores"))
JUDGE_MODEL = "gpt-6.1-sol"
EMBEDDER = "BAAI/bge-base-en-v1.5"
TOOLS = ("none", "oracle", "check", "mem0", "supermemory")
LABELS = {"none": "No memory", "oracle": "Gold sessions", "check": f"{product.NAME} check", "mem0": "Mem0",
          "supermemory": "Supermemory"}
CONV_REV = "1925e924c36e632283ea91f2212157829f4e0e45"
OP_REV = "44e7d0e526152997b04208218a8ad7362174dae4"
FILES = {
    "conv_jobs": (f"https://raw.githubusercontent.com/MiuLab/LoCoMo-Conv/{CONV_REV}/data/response_eval_full_ext.json",
                  "bbf7d9a09d8becab0bb0600c04aabe047cf21084369a07a4f23aedcc63c53c89"),
    "conv_answer": (f"https://raw.githubusercontent.com/MiuLab/LoCoMo-Conv/{CONV_REV}/response_eval/run_response_eval.py",
                    "e2358b6c2c1487766a6b597aeb6251bd66890f06f2a69a7ac08eacc8854d923e"),
    "conv_judge": (f"https://raw.githubusercontent.com/MiuLab/LoCoMo-Conv/{CONV_REV}/response_eval/score_fact_used_partial.py",
                   "a9e7e89f7db2f1ba171485968ce7714e250a17868854b1f0339cee6018f8ad55"),
    "op_tasks": (f"https://raw.githubusercontent.com/yulinlp/OP-Bench/{OP_REV}/data/locomo10_overpersonalized.json",
                 "250f4005a57226eb80a13c345ed0b0a88db26dfbd0c9f3339f19f32cad690992"),
    "op_prompts": (f"https://raw.githubusercontent.com/yulinlp/OP-Bench/{OP_REV}/src/opbench/prompts.py",
                   "8301b9413a156b14974f6b8f7b16829573821ef5078562c5ee6295c226d4fbc1"),
}
OP_TASKS = ("irrelevance_easy", "irrelevance_hard", "sycophancy", "diversity")
SYCOPHANCY_JUDGES = {"fact": "SCORE_SYCOPHANCY_FACT_PROMPT", "value": "SCORE_SYCOPHANCY_VALUE_PROMPT",
                     "memory": "SCORE_SYCOPHANCY_MEMORY_PROMPT"}


class Case(NamedTuple):
    test: str           # "implicit" or "op"
    id: str
    group: str          # the LoCoMo conversation whose store answers it
    kind: str           # implicit: LoCoMo's category; op: OP-Bench's task type
    user: str           # who sends the message
    message: str
    gold: str           # implicit: LoCoMo's answer; op: ""
    subtype: str        # op sycophancy: fact, value or memory; otherwise ""
    evidence: tuple     # implicit: source_ids of the sessions that hold the answer


def path(key):
    url, sha256 = FILES[key]
    return common.fetch(url, os.path.join("outside", key + os.path.splitext(url)[1]), sha256)


@functools.cache
def constants(key):
    """Module-level string constants from a downloaded Python file, read without running it."""
    with open(path(key), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    return {t.id: node.value.value for node in tree.body if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant) for t in node.targets if isinstance(t, ast.Name)}


def strings(key, *names):
    found = constants(key)
    missing = [n for n in names if n not in found]
    if missing:
        raise SystemExit(f"{FILES[key][0]} no longer defines {missing}")
    return {n: found[n] for n in names}


def implicit_cases(samples):
    """The implicit jobs from the paper's evaluation list, without category 5, which its fact-used judge skips."""
    with open(path("conv_jobs"), encoding="utf-8") as fh:
        jobs = json.load(fh)["samples"]["implicit"]
    cases = []
    for job in jobs:
        if job["category"] not in locomo.CATEGORIES:
            continue
        sample = samples[job["sample_idx"]]
        sid = sample["sample_id"]
        sessions = sorted({int(m) for e in job["evidence"] for m in re.findall(r"D(\d+):", e)})
        answer = job["answer"]
        cases.append(Case("implicit", f"{sid}/{job['q_idx']}", sid, locomo.CATEGORIES[job["category"]],
                          sample["conversation"][job["subject_speaker"]], job["query"],
                          ", ".join(map(str, answer)) if isinstance(answer, list) else str(answer), "",
                          tuple(f"{sid}/session_{n}" for n in sessions)))
    return cases


def op_cases(samples):
    """Every OP-Bench message for both people in each conversation, as the paper ran it (20 users)."""
    with open(path("op_tasks"), encoding="utf-8") as fh:
        conversations = json.load(fh)
    cases = []
    for sample, people in zip(samples, conversations):
        sid = sample["sample_id"]
        for person, data in people.items():
            for task in OP_TASKS:
                entries = data["tasks"].get(task, [])
                if task == "diversity":
                    entries = [q for group in entries for q in group["questions"]]
                for n, entry in enumerate(entries):
                    # types.py's _question_text: a string or a record with a question; anything else is skipped.
                    text = entry if isinstance(entry, str) else entry.get("question") if isinstance(entry, dict) else None
                    if not text or not text.strip():
                        continue
                    subtype = entry.get("type", "") if task == "sycophancy" and isinstance(entry, dict) else ""
                    if task == "sycophancy" and subtype not in SYCOPHANCY_JUDGES:
                        subtype = "memory"
                    cases.append(Case("op", f"{sid}/{person}/{task}/{n}", sid, task, person, text.strip(), "",
                                      subtype, ()))
    return cases


def implicit_prompt(case, tool, context):
    p = strings("conv_answer", "ANSWER_PROMPT", "NO_MEMORY_PROMPT")
    if tool == "none":
        return None, p["NO_MEMORY_PROMPT"].format(query=case.message, speaker=case.user)
    return None, p["ANSWER_PROMPT"].format(memory_block=context or "(no memories retrieved)", query=case.message,
                                           speaker=case.user)


def op_prompt(case, tool, context):
    """OP-Bench's evaluation.py: the memory prompt when the lookup returned something, the plain one otherwise."""
    p = strings("op_prompts", "ANSWER_SYSTEM_PROMPT", "ANSWER_USER_PROMPT_WITH_MEMORY",
                "ANSWER_USER_PROMPT_WITHOUT_MEMORY")
    if context.strip():
        memory = f"Memories for user {case.user}:\n\n{context}"
        return p["ANSWER_SYSTEM_PROMPT"], p["ANSWER_USER_PROMPT_WITH_MEMORY"].format(memory=memory,
                                                                                    question=case.message)
    return p["ANSWER_SYSTEM_PROMPT"], p["ANSWER_USER_PROMPT_WITHOUT_MEMORY"].format(question=case.message)


LIMITS = Limits()


# gateway.post as bench/gateway.py has it: it reads every reply for a rate limit and never retries one. A run swaps
# gateway.post for post below, which sends it in one of the run's two slots.
GATEWAY_POST = gateway.post


def post(path, body, timeout=300):
    return LIMITS.send(body["model"], lambda: GATEWAY_POST(path, body, timeout))


def install_limits():
    """Route every model call this run makes through LIMITS: the answer loop and judges (gateway.post), the check's
    query writer and picker (kb's ask_model, with its 3-second hedge sent only when a slot is free), and kb's
    summaries while a store is built (2 workers, none of kb's own retries). Mem0's calls go through it in
    mem0_locomo.py."""
    gateway.post = post
    _, context, summarize = check.modules()
    ask = context.ask_model
    context.ask_model = lambda body, timeout: LIMITS.send(body["model"], lambda: ask(body, timeout), timeout)
    summarize.WORKERS, summarize.ATTEMPTS = Limits.PARALLEL, 1
    call_api, run_summaries = summarize.call_api, summarize.run
    summarize.run = lambda db, log, limit=summarize.RUN_CAP, source=None: run_summaries(
        db, log, limit, source, lambda body: LIMITS.send(body["model"], lambda: call_api(body)))


def judge(prompt, parse):
    for _ in range(3):
        reply = gateway.text(gateway.post("/v1/messages", {"model": JUDGE_MODEL, "max_tokens": 1000, "temperature": 0,
                                                           "messages": [{"role": "user", "content": prompt}]}))
        score = parse(reply)
        if score is not None:
            return score, reply
    raise ValueError(f"{JUDGE_MODEL} gave no score")


def fact_score(reply):
    """score_fact_used_partial.py's _parse_score."""
    s = (reply or "").strip()
    for token in ("1.0", "0.5", "0.0", "1", "0"):
        if token in s:
            return float(token)
    return None


def op_score(reply):
    """scoring.py's _parse_score: the first number, clamped to 0 to 1."""
    found = re.search(r"(?<![\d.])-?(?:\d+(?:\.\d+)?|\.\d+)(?![\d.])", reply or "")
    return max(0.0, min(1.0, float(found.group(0)))) if found else None


def implicit_grade(case, answer):
    prompt = strings("conv_judge", "PARTIAL_PROMPT")["PARTIAL_PROMPT"].format(
        user_name=case.user, query=case.message, gold=case.gold, response=answer)
    score, reply = judge(prompt, fact_score)
    return {"fact_score": score, "judge_reply": reply}


def op_grade(case, answer):
    """Irrelevance and sycophancy get a judge score per message. Repetition is scored over each person's whole set
    of diversity replies, in the table."""
    if case.kind == "diversity":
        return {}
    name = "SCORE_IRRELEVANCE_PROMPT" if case.kind.startswith("irrelevance") else SYCOPHANCY_JUDGES[case.subtype]
    score, reply = judge(strings("op_prompts", name)[name].format(question=case.message, response=answer), op_score)
    return {"score": score, "judge_reply": reply}


class Test(NamedTuple):
    cases: object
    prompt: object
    grade: object
    tools: tuple        # tools this test runs; gold sessions exist only where a message has evidence


TESTS = {"implicit": Test(implicit_cases, implicit_prompt, implicit_grade, TOOLS),
         "op": Test(op_cases, op_prompt, op_grade, tuple(t for t in TOOLS if t != "oracle"))}


def answer(tool, system, text):
    """The answer loop of bench/public/common.py, with this tool's agent tools in place of the check's."""
    messages = [{"role": "user", "content": text}]
    calls, shown, chars, empty = [], [], 0, []
    for turn in range(common.TOOL_TURNS + 1):
        body = {"model": run.ANSWER_MODEL, "max_tokens": 2000, "messages": messages}
        if system:
            body["system"] = system
        if tool.agent_tools:
            body["tools"] = list(tool.agent_tools)
            if turn == common.TOOL_TURNS:
                body["tool_choice"] = {"type": "none"}
        response = common.ask(body, empty)
        uses = [b for b in response["content"] if b.get("type") == "tool_use"]
        if not uses:
            return gateway.text(response), calls, shown, chars, empty
        messages.append({"role": "assistant", "content": response["content"]})
        results = []
        for use in uses:
            out, ids = tool.call(use["name"], use.get("input") or {})
            calls.append({"name": use["name"], "input": use.get("input")})
            shown += [i for i in ids if i not in shown]
            chars += len(out)
            results.append({"type": "tool_result", "tool_use_id": use["id"], "content": out})
        messages.append({"role": "user", "content": results})
    raise RuntimeError("the answer model kept calling tools")


def run_case(name, tool, case):
    LIMITS.wait()
    found = tool.retrieve({"question": case.message, "evidence": list(case.evidence)})
    if found.error:
        # A lookup that failed, timed out or met a limit, even in one of the check's hedged copies, would leave the
        # reply without what the tool would have added; the message gets no row and runs again.
        raise RuntimeError(f"the lookup failed with {found.error}")
    system, text = TESTS[case.test].prompt(case, name, found.context)
    reply, calls, shown, chars, empty = answer(tool, system, text)
    return {"id": case.id, "group": case.group, "kind": case.kind, "subtype": case.subtype, "user": case.user,
            "gold": case.gold, "evidence": list(case.evidence), "query": found.query, "context": found.context,
            "context_items": found.items, "lookup_seconds": round(found.seconds, 3), "lookup_error": found.error,
            "tool_calls": calls, "read_items": shown, "tool_chars": chars, "empty_replies": empty, "answer": reply,
            **TESTS[case.test].grade(case, reply)}


# Stores whose builds make model calls outside a run's own slots, so a run only opens them; --load-only prepares them.
PREPARED = ("mem0", "supermemory")


def supermemory_ready(count):
    """Supermemory's namespace holds all count items and has extracted every one's memories, so opening it makes no
    model calls and no store is missing a session. A failed extraction is not ready, though the month's adapter
    counts it as settled. The server's own calls cannot go through this run's slots."""
    store = tools.supermemory.Supermemory()
    ids = store._documents()
    if len(ids) != count:
        return False
    for doc in ids:
        status = tools.supermemory.api(f"/v3/documents/{doc}", method="GET")
        if status["status"] != "done" or status.get("dreamingStatus") not in ("done", None):
            return False
    return True


def load(name, sample, items, workdir, prepare=False):
    """A store for one conversation. Supermemory's adapter keeps one namespace per server, so each conversation gets
    its own; conversations run one after another, so the namespace holds while its messages run."""
    group = sample["sample_id"]
    if name == "supermemory":
        tools.supermemory.NAMESPACE = f"locomo-{group}"
        if not prepare and not supermemory_ready(len(items)):
            raise RuntimeError(f"Supermemory's store for {group} is not ready; prepare it with --load-only")
    tool = LocomoMem0(sample, LIMITS.send, prepare) if name == "mem0" else tools.make(name)
    folder = os.path.join(STORES if name == "mem0" else workdir, name, group)
    os.makedirs(folder, exist_ok=True)
    LIMITS.wait()
    started = time.monotonic()
    stats = tool.load(items, folder)
    if stats.get("summary_failures"):
        raise RuntimeError(f"{stats['summary_failures']} summaries failed; the store would be missing them")
    return tool, {**stats, "items": len(items), "load_seconds": round(time.monotonic() - started)}


def rows_path(test, name):
    return os.path.join(OUT, test, f"{name}.jsonl")


def export_path(test, name):
    return os.path.join(OUT, "sample", test, f"{name}.jsonl.gz")


def read_rows(test, name):
    """The working rows when this machine has them, else the sampled rows committed with the results."""
    rows = common.read_rows(rows_path(test, name))
    if rows or not os.path.exists(export_path(test, name)):
        return rows
    with gzip.open(export_path(test, name), "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def export(test, name, rows, ids):
    """The sampled rows, sorted and compressed the same way every time, so an unchanged sample is an unchanged file."""
    os.makedirs(os.path.dirname(export_path(test, name)), exist_ok=True)
    text = "".join(json.dumps(rows[i], ensure_ascii=False, sort_keys=True) + "\n" for i in sorted(ids))
    with open(export_path(test, name), "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
        fh.write(text.encode())


def record_mem0_stores():
    """Each kept Mem0 store's build stats, from the loaded.json written when it was built."""
    found = {}
    for group in sorted(os.listdir(os.path.join(STORES, "mem0"))) if os.path.isdir(os.path.join(STORES, "mem0")) else []:
        done = os.path.join(STORES, "mem0", group, "loaded.json")
        if os.path.exists(done):
            with open(done, encoding="utf-8") as fh:
                found[group] = json.load(fh)
    if found:
        with open(os.path.join(OUT, "mem0-stores.json"), "w", encoding="utf-8") as fh:
            json.dump(found, fh, indent=1, sort_keys=True)


def stratum(case):
    """The tests' own categories. Each person's repetition messages are their own stratum, because the repetition
    score is computed over one person's replies."""
    if case.test == "implicit":
        return f"implicit/{case.kind}"
    if case.kind == "diversity":
        return f"op/diversity/{case.group}/{case.user}"
    return f"op/{case.kind}" + (f"/{case.subtype}" if case.subtype else "")


def person(case_id):
    return "/".join(case_id.split("/")[:2])


def draw(cases, fraction, seed):
    """A seeded stratified sample: the same share of every stratum, at least 2 messages from each. OP-Bench's
    message-scored strata also record each person's count, because its code averages each person's task mean."""
    strata = {}
    for c in cases:
        strata.setdefault(stratum(c), []).append(c.id)
    rng = random.Random(seed)
    drawn = {}
    for key, ids in sorted(strata.items()):
        drawn[key] = {"population": len(ids), "ids": sorted(rng.sample(sorted(ids), max(2, round(len(ids) * fraction))))}
        if key.startswith("op/") and not key.startswith("op/diversity/"):
            drawn[key]["people"] = {p: sum(person(i) == p for i in ids) for p in sorted({person(i) for i in ids})}
    return drawn


def sample_path():
    return os.path.join(OUT, "sample.json")


def sample_for(cases, fraction, seed):
    """The recorded sample, or a new one drawn and recorded. A different share or seed is a different sample."""
    drawn = {"fraction": fraction, "seed": seed, "strata": draw(cases, fraction, seed)}
    if os.path.exists(sample_path()):
        with open(sample_path(), encoding="utf-8") as fh:
            recorded = json.load(fh)
        if recorded != drawn:
            raise SystemExit(f"{sample_path()} holds a different sample; move it aside to draw this one")
    with open(sample_path(), "w", encoding="utf-8") as fh:
        json.dump(drawn, fh, indent=1)
    return drawn


def every_case(samples, tests):
    return [c for t in tests for c in TESTS[t].cases(samples)]


def run_tests(tests, names, groups, workers, limit=None, chosen=None):
    samples = locomo.load()
    by_id = {s["sample_id"]: s for s in samples}
    items = {s["sample_id"]: locomo.session_items(s) for s in samples}
    cases = []
    for t in tests:
        every = TESTS[t].cases(samples)
        for group in groups:
            cases += [c for c in every if c.group == group and (chosen is None or c.id in chosen)][:limit]
    common.summarized_sources()
    install_limits()
    workers = min(workers, Limits.PARALLEL)
    done = {(t, n): {r["id"] for r in common.read_rows(rows_path(t, n))} for t in tests for n in names}
    started, failures, stores, lock = time.monotonic(), [], {}, threading.Lock()
    handles = {}
    for t in tests:
        os.makedirs(os.path.join(OUT, t), exist_ok=True)
    try:
        for group in groups:
            if LIMITS.stopped:
                failures.append(f"stopped before {group}: {LIMITS.stopped}")
                break
            todo = {n: [c for c in cases if c.group == group and n in TESTS[c.test].tools
                        and c.id not in done[c.test, n]] for n in names}
            todo = {n: cs for n, cs in todo.items() if cs}
            if not todo:
                continue
            print(f"{group}: {sum(map(len, todo.values()))} messages for {', '.join(todo)}", file=sys.stderr)
            with tempfile.TemporaryDirectory(prefix="bench-outside-") as workdir:
                loaded = {}
                with ThreadPoolExecutor(len(todo)) as pool:
                    futures = {pool.submit(load, n, by_id[group], items[group], workdir): n for n in todo}
                    for future in as_completed(futures):
                        loaded[futures[future]] = future.result()
                        stores.setdefault(futures[future], {})[group] = loaded[futures[future]][1]
                with ThreadPoolExecutor(workers) as pool:
                    futures = {pool.submit(run_case, n, loaded[n][0], c): (n, c) for n, cs in todo.items() for c in cs}
                    for k, future in enumerate(as_completed(futures), 1):
                        n, case = futures[future]
                        try:
                            row = future.result()
                        except Exception as exc:
                            failures.append(f"{n} {case.id}: {type(exc).__name__}: {exc}")
                            print(failures[-1], file=sys.stderr)
                            continue
                        with lock:
                            key = (case.test, n)
                            if key not in handles:
                                handles[key] = open(rows_path(*key), "a", encoding="utf-8")
                            handles[key].write(json.dumps(row, ensure_ascii=False) + "\n")
                            handles[key].flush()
                        if k % 50 == 0:
                            print(f"{group}: {k}/{len(futures)} in {time.monotonic() - started:.0f}s", file=sys.stderr)
    finally:
        for fh in handles.values():
            fh.close()
    record = {"started": datetime.fromtimestamp(time.time() - (time.monotonic() - started), timezone.utc)
              .strftime("%Y-%m-%dT%H:%M:%SZ"), "seconds": round(time.monotonic() - started), "tests": tests,
              "tools": names, "groups": groups, "answer_model": run.ANSWER_MODEL, "judge_model": JUDGE_MODEL,
              "tool_turns": common.TOOL_TURNS, "check_code": check.code_id(), "workers": workers,
              "parallel_model_calls": Limits.PARALLEL, "rate_limits": dict(LIMITS.seen), "stopped": LIMITS.stopped,
              "data": {k: {"url": u, "sha256": h} for k, (u, h) in FILES.items()},
              "conversations": {"revision": locomo.REVISION, "sha256": locomo.SHA256}, "stores": stores,
              "failed": len(failures), "failures": failures[:50]}
    record_mem0_stores()
    meta = os.path.join(OUT, "runs.json")
    runs = json.load(open(meta))["runs"] if os.path.exists(meta) else []
    with open(meta, "w", encoding="utf-8") as fh:
        json.dump({"runs": runs + [record]}, fh, indent=1)
    return record


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def show(value, scale=1):
    return "n/a" if value is None else f"{scale * value:.1f}" if scale != 1 else f"{value:.3f}"


def implicit_table(names):
    kinds = list(locomo.CATEGORIES.values())
    lines = []
    for n in names:
        rows = common.read_rows(rows_path("implicit", n))
        if not rows:
            continue
        reached = [r for r in rows if r["evidence"]]
        lines.append([LABELS[n], len(rows), show(mean(r["fact_score"] for r in rows)),
                      *(show(mean(r["fact_score"] for r in rows if r["kind"] == k)) for k in kinds),
                      common.pct(sum(bool(r["context"]) for r in rows), len(rows)),
                      common.pct(sum(bool(set(r["evidence"]) & set((r["context_items"] or []) + r["read_items"]))
                                     for r in reached), len(reached))])
    return common.markdown(["Tool", "Messages", "Fact used, mean", *kinds, "Lookup added something",
                            "Gold session reached"], lines)


def diversity(rows, embed):
    """scoring.py's repetition score for one person: 1 minus the mean cosine similarity between every pair of
    replies, skipping empty ones."""
    answers = [r["answer"] for r in rows if r["answer"].strip()]
    if len(answers) < 2:
        return 0.0
    vectors = embed(answers)
    pairs = [float(vectors[i] @ vectors[j]) for i in range(len(answers)) for j in range(i + 1, len(answers))]
    return max(0.0, min(1.0, 1.0 - sum(pairs) / len(pairs)))


def op_scores(rows, embed):
    """aggregate_metrics: each person's task is one case scored by the mean over its messages, and the overall score
    is the mean over cases."""
    cases = {}
    for r in rows:
        cases.setdefault((r["user"], r["group"], r["kind"]), []).append(r)
    primary = {key: diversity(rs, embed) if key[2] == "diversity" else mean(r["score"] for r in rs)
               for key, rs in cases.items()}
    by_task = {t: mean(v for k, v in primary.items() if k[2] == t) for t in OP_TASKS}
    subtypes = {s: mean(r["score"] for r in rows if r["kind"] == "sycophancy" and r["subtype"] == s)
                for s in SYCOPHANCY_JUDGES}
    return mean(primary.values()), by_task, subtypes


def op_table(names):
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(EMBEDDER)

    def embed(texts):
        return model.encode(texts, normalize_embeddings=True)

    lines = []
    for n in names:
        rows = common.read_rows(rows_path("op", n))
        if not rows:
            continue
        overall, by_task, subtypes = op_scores(rows, embed)
        lines.append([LABELS[n], len(rows), *(show(by_task[t], 100) for t in OP_TASKS if t != "sycophancy"),
                      *(show(subtypes[s], 100) for s in SYCOPHANCY_JUDGES), show(overall, 100),
                      common.pct(sum(not r["context"] for r in rows), len(rows))])
    return common.markdown(["Tool", "Messages", "irrelevance_easy", "irrelevance_hard", "diversity",
                            *(f"sycophancy, {s}" for s in SYCOPHANCY_JUDGES), "Overall, by OP-Bench's code",
                            "Lookup added nothing"], lines)


BOOTSTRAP = 2000
BOOTSTRAP_SEED = 0


def interval(point, draws):
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return point, lo, hi


def shown(found, scale=1, digits=3):
    point, lo, hi = found
    return f"{scale * point:.{digits}f} [{scale * lo:.{digits}f}, {scale * hi:.{digits}f}]"


def sample_rows(test, names, strata):
    """Each tool's rows for the sampled messages, keeping only messages every tool answered, so every comparison is
    on the same messages."""
    working = {n: bool(common.read_rows(rows_path(test, n))) for n in names}
    rows = {n: {r["id"]: r for r in read_rows(test, n)} for n in names}
    rows = {n: rs for n, rs in rows.items() if rs}
    keep = {k: [i for i in v["ids"] if all(i in rs for rs in rows.values())] for k, v in strata.items()}
    missing = sum(len(v["ids"]) - len(keep[k]) for k, v in strata.items())
    for n, rs in rows.items():
        if working[n]:
            export(test, n, rs, [i for v in keep.values() for i in v])
    return rows, keep, missing


def implicit_sample(names, strata):
    """The fact-used mean over the population, estimated from the sample stratum by stratum, with a 95% interval from
    a bootstrap that resamples each stratum's messages; the same resampled messages score every tool."""
    strata = {k: v for k, v in strata.items() if k.startswith("implicit/")}
    rows, keep, missing = sample_rows("implicit", [n for n in names if n in TESTS["implicit"].tools], strata)
    weight = {k: v["population"] / sum(x["population"] for x in strata.values()) for k, v in strata.items()}
    scores = {n: {k: np.array([rs[i]["fact_score"] for i in keep[k]]) for k in strata} for n, rs in rows.items()}

    def estimate(n, picks):
        return sum(weight[k] * scores[n][k][picks[k]].mean() for k in strata)

    rng = np.random.default_rng(BOOTSTRAP_SEED)
    whole = {k: np.arange(len(keep[k])) for k in strata}
    draws = {n: [] for n in rows}
    for _ in range(BOOTSTRAP):
        picks = {k: rng.integers(0, len(keep[k]), len(keep[k])) for k in strata}
        for n in rows:
            draws[n].append(estimate(n, picks))
    found = {n: interval(estimate(n, whole), draws[n]) for n in rows}
    kinds = list(locomo.CATEGORIES.values())
    lines = [[LABELS[n], sum(map(len, keep.values())), shown(found[n]),
              *(f"{scores[n][f'implicit/{k}'].mean():.3f}" for k in kinds),
              common.pct(sum(bool(rs[i]["context"]) for v in keep.values() for i in v), sum(map(len, keep.values())))]
             for n, rs in rows.items()]
    parts = [common.markdown(["Tool", "Messages", "Fact used, mean [95% CI]", *(f"{k} ({len(keep[f'implicit/{k}'])})"
                                                                                for k in kinds),
                              "Lookup added something"], lines)]
    if "check" in rows:
        diffs = [[f"{LABELS['check']} minus {LABELS[n]}", shown(interval(found["check"][0] - found[n][0],
                                                                          np.subtract(draws["check"], draws[n])))]
                 for n in rows if n != "check"]
        parts.append(common.markdown(["Paired difference", "Fact used [95% CI]"], diffs))
    return parts, missing


def similarity_path():
    return os.path.join(OUT, "sample", "op", "repetition.json")


def replies_hash(rows, keep, people):
    """A SHA-256 per tool over each person's sampled repetition replies, in order, so stored similarities that no
    longer match the committed replies are refused."""
    return {n: hashlib.sha256("\0".join(rs[i]["answer"] for k in people for i in keep[k]).encode()).hexdigest()
            for n, rs in rows.items()}


def similarities(rows, keep, people):
    """For each tool and person, the cosine similarity between every pair of that person's sampled repetition
    replies, rounded to 6 places. Where the working rows are on this machine they are computed with the local
    embedder and written to results/sample/op/repetition.json; elsewhere they are read from it, so the tables
    rebuild from the committed rows without the embedder."""
    if any(common.read_rows(rows_path("op", n)) for n in rows):
        import sentence_transformers
        model = sentence_transformers.SentenceTransformer(EMBEDDER)
        similarity = {}
        for n, rs in rows.items():
            for k in people:
                vectors = model.encode([rs[i]["answer"] for i in keep[k]], normalize_embeddings=True)
                similarity.setdefault(n, {})[k] = np.round(vectors @ vectors.T, 6).tolist()
        found = {"embedder": EMBEDDER, "model_revision": model.model_card_data.base_model_revision,
                 "sentence_transformers": sentence_transformers.__version__, "ids": {k: keep[k] for k in people},
                 "replies_sha256": replies_hash(rows, keep, people), "similarity": similarity}
        os.makedirs(os.path.dirname(similarity_path()), exist_ok=True)
        with open(similarity_path(), "w", encoding="utf-8") as fh:
            json.dump(found, fh, sort_keys=True)
    with open(similarity_path(), encoding="utf-8") as fh:
        found = json.load(fh)
    if found["ids"] != {k: keep[k] for k in people} or found.get("replies_sha256") != replies_hash(rows, keep, people):
        raise SystemExit(f"{similarity_path()} was written for different replies; rebuild it where the rows are")
    return found["similarity"]


def weighted(values, weights, pick):
    return float((values * weights)[pick].sum() / weights[pick].sum())


def op_sample(names, strata):
    """The six OP-Bench columns and the code's overall score, each a 95% interval from a stratified bootstrap.
    Every column is a mean of per-person means, as in the code; sycophancy weighs its subtypes by their share of the
    400 messages, as each person's 20 do."""
    strata = {k: v for k, v in strata.items() if k.startswith("op/")}
    rows, keep, missing = sample_rows("op", [n for n in names if n in TESTS["op"].tools], strata)
    people = [k for k in strata if k.startswith("op/diversity/") and len(keep[k]) >= 2]
    columns = ["op/irrelevance_easy", "op/irrelevance_hard", "op/sycophancy/fact", "op/sycophancy/value",
               "op/sycophancy/memory"]
    scores = {n: {k: np.array([rs[i]["score"] for i in keep[k]]) for k in columns} for n, rs in rows.items()}
    # Each sampled message stands for its person's share of the task, so the mean estimates the mean of each
    # person's task mean, as OP-Bench's aggregate_metrics computes it.
    weights = {k: np.array([1.0 / strata[k]["people"][person(i)] for i in keep[k]]) for k in columns}
    stored = similarities(rows, keep, people)
    similar = {(n, k): (np.array(stored[n][k]), np.array([bool(rows[n][i]["answer"].strip()) for i in keep[k]]))
               for n in rows for k in people}
    syc = {k: strata[k]["population"] for k in columns if k.startswith("op/sycophancy/")}

    def repetition(n, k, pick):
        sims, ok = similar[n, k]
        pick = pick[ok[pick]]
        i, j = np.triu_indices(len(pick), 1)
        pairs = pick[i] != pick[j]
        if not pairs.any():
            return 0.0
        return float(np.clip(1.0 - sims[pick[i][pairs], pick[j][pairs]].mean(), 0.0, 1.0))

    def stats(n, picks):
        col = {k: weighted(scores[n][k], weights[k], picks[k]) for k in columns}
        col["diversity"] = float(np.mean([repetition(n, k, picks[k]) for k in people]))
        sycophancy = sum(syc[k] * col[k] for k in syc) / sum(syc.values())
        col["overall"] = np.mean([col["op/irrelevance_easy"], col["op/irrelevance_hard"], sycophancy, col["diversity"]])
        return col

    rng = np.random.default_rng(BOOTSTRAP_SEED)
    whole = {k: np.arange(len(keep[k])) for k in strata}
    point = {n: stats(n, whole) for n in rows}
    draws = {n: [] for n in rows}
    for _ in range(BOOTSTRAP):
        picks = {k: rng.integers(0, len(keep[k]), len(keep[k])) for k in strata}
        for n in rows:
            draws[n].append(stats(n, picks))
    order = ["op/irrelevance_easy", "op/irrelevance_hard", "diversity", *columns[2:], "overall"]

    def cell(n, key, against=None):
        values = [d[key] - (draws[against][b][key] if against else 0) for b, d in enumerate(draws[n])]
        return shown(interval(point[n][key] - (point[against][key] if against else 0), values), 100, 1)

    counts = {"diversity": sum(len(keep[k]) for k in people), "overall": sum(map(len, keep.values()))}
    head = [f"{k.removeprefix('op/').replace('/', ', ')} ({counts.get(k) or len(keep[k])})" for k in order]
    lines = [[LABELS[n], *(cell(n, k) for k in order),
              common.pct(sum(not rows[n][i]["context"] for v in keep.values() for i in v), counts["overall"])]
             for n in rows]
    parts = [common.markdown(["Tool", *head, "Lookup added nothing"], lines)]
    if "check" in rows:
        parts.append(common.markdown(["Paired difference", *head],
                                     [[f"{LABELS['check']} minus {LABELS[n]}", *(cell("check", k, n) for k in order)]
                                      for n in rows if n != "check"]))
    return parts, missing


def sample_table(tests, names):
    with open(sample_path(), encoding="utf-8") as fh:
        recorded = json.load(fh)
    strata = recorded["strata"]
    parts = [f"A seeded stratified sample: {recorded['fraction']:.0%} of each stratum, seed {recorded['seed']}, "
             f"{BOOTSTRAP} bootstrap draws (seed {BOOTSTRAP_SEED}). Intervals are 95%."]
    for test, build in (("implicit", implicit_sample), ("op", op_sample)):
        if test in tests:
            found, missing = build(names, strata)
            parts += found + ([f"{missing} sampled messages lack a row for some tool and are left out."]
                              if missing else []) + [published(test)]
    text = "\n\n".join(parts) + "\n"
    with open(os.path.join(OUT, "sample-table.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def published(test):
    with open(os.path.join(HERE, "published.json"), encoding="utf-8") as fh:
        found = [p for p in json.load(fh) if p["test"] == test]
    parts = []
    for p in found:
        if "columns" in p:
            body = common.markdown(["System", *p["columns"]], [[k, *v] for k, v in p["scores"].items()])
        else:
            body = common.markdown(["System", "Fact used, mean"], [[k, v] for k, v in p["scores"].items()])
        parts.append(f"Published: {p['source']} ({p['label']}, {p['date']}). {p['setup']}.\n\n{body}")
    return "\n\n".join(parts)


def table(tests, names):
    parts = []
    if "implicit" in tests:
        parts += [implicit_table(names), published("implicit")]
    if "op" in tests:
        parts += [op_table(names), published("op")]
    text = "\n\n".join(parts) + "\n"
    with open(os.path.join(OUT, "table.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def main(argv=None):
    faulthandler.register(signal.SIGUSR1)
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tests", default=",".join(TESTS))
    p.add_argument("--tools", default=",".join(TOOLS))
    p.add_argument("--groups", help="LoCoMo sample ids to run, such as conv-26; default all ten")
    p.add_argument("--workers", type=int, default=Limits.PARALLEL, help="at most 2: the plans' limit per run")
    p.add_argument("--limit", type=int, help="run only the first N messages of each test in each conversation")
    p.add_argument("--table", action="store_true", help="print the tables from the rows already written")
    p.add_argument("--load-only", action="store_true",
                   help="prepare Mem0's and Supermemory's stores, one at a time with nothing else running, and stop")
    p.add_argument("--sample", type=float, help="run a seeded stratified sample, such as 0.2 for 20%% of each stratum")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)
    tests, names = args.tests.split(","), args.tools.split(",")
    if args.table:
        print(sample_table(tests, names) if args.sample else table(tests, names))
        return 0
    groups = args.groups.split(",") if args.groups else [s["sample_id"] for s in locomo.load()]
    if args.load_only:
        install_limits()
        samples = {s["sample_id"]: s for s in locomo.load()}
        for group in groups:
            for name in [n for n in names if n in PREPARED]:
                items = locomo.session_items(samples[group])
                with tempfile.TemporaryDirectory(prefix="bench-outside-") as workdir:
                    try:
                        tool, stats = load(name, samples[group], items, workdir, prepare=True)
                    except RateLimited:
                        LIMITS.wait()
                        tool, stats = load(name, samples[group], items, workdir, prepare=True)
                print(group, name, json.dumps(stats))
        record_mem0_stores()
        return 0
    chosen = None
    if args.sample:
        drawn = sample_for(every_case(locomo.load(), list(TESTS)), args.sample, args.seed)
        chosen = {i for v in drawn["strata"].values() for i in v["ids"]}
    record = run_tests(tests, names, groups, args.workers, args.limit, chosen)
    print(sample_table(tests, names) if args.sample else table(tests, names))
    return common.exit_status(record)


if __name__ == "__main__":
    sys.exit(main())
