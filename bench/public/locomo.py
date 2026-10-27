#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["nltk==3.9.1", "numpy==2.1.3", "regex==2024.11.6"]
# ///
"""LoCoMo (Maharana et al., ACL 2024) through the check, scored with the paper's token F1 and with the J score.

    uv run bench/public/locomo.py [--summaries auto|on|off] [--sample N [--seed S]] [--limit N] [--workers N]
                                  [--fresh] [--count] [--table]

Each of the 10 conversations gets one store, one item per session with its date. Each question goes through the
check as a new message, the answer model answers with the check's records and kb tools, and two scores grade the
answer: token F1 from LoCoMo's task_eval/evaluation.py, and the J score, Mem0's published LLM judge prompt. Category
5 (adversarial) is left out; DEVIATIONS.md says why. Rows go to bench/results/public/locomo/<run>.jsonl, one per
question; a rerun runs only questions with no row yet. uv installs the packages LoCoMo's F1 code imports.
"""
import argparse
import json
import os
import re
import string
import sys
from collections import Counter
from datetime import datetime

import numpy as np
import regex
from nltk.stem import PorterStemmer

import common

import gateway
import product

JUDGE_MODEL = "gpt-6.1-sol"
OUT = os.path.join(common.RESULTS, "locomo")
REVISION = "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376"
SHA256 = "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"
# Names from LoCoMo's task_eval: evaluation.py splits category 1 into sub-answers as multi-hop, and gpt_utils.py
# asks category 2 for a date.
CATEGORIES = {4: "single-hop", 1: "multi-hop", 2: "temporal", 3: "open-domain"}

ANSWER_SYSTEM = """You are an AI agent. The user's knowledge base holds the chats between {a} and {b}, one record \
per chat session, each dated. Today is {today}. Knowledge base records may come before the user's message. Reply \
with only the answer: a short phrase of at most six words, with no explanation and no formatting, using exact words \
from the records whenever possible. When the answer is a time, work it out from the date of the record it comes \
from: "last year" in a chat on 4 May 2022 means 2021."""

# Copied verbatim from mem0ai/mem0 evaluation/metrics/llm_judge.py at commit aae5989, the last version before the
# folder moved out of that repo. Mem0 ran it with gpt-4o-mini at temperature 0.
ACCURACY_PROMPT = """
Your task is to label an answer to a question as ’CORRECT’ or ’WRONG’. You will be given the following data:
    (1) a question (posed by one user to another user),
    (2) a ’gold’ (ground truth) answer,
    (3) a generated answer
which you will score as CORRECT/WRONG.

The point of the question is to ask about something one user should know about the other user based on their prior conversations.
The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
Question: Do you remember what I got the last time I went to Hawaii?
Gold answer: A shell necklace
The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.

For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like "last Tuesday" or "next month"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., "May 7th" vs "7 May"), consider it CORRECT if it's the same date.

Now it's time for the real question:
Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Just return the label CORRECT or WRONG in a json format with the key as "label".
"""

# Copied verbatim from LoCoMo's task_eval/evaluation.py at commit 3eb6f2c.
ps = PorterStemmer()


def normalize_answer(s):

    s = s.replace(',', "")
    def remove_articles(text):
        # return regex.sub(r'\b(a|an|the)\b', ' ', text)
        return regex.sub(r'\b(a|an|the|and)\b', ' ', text)

    def white_space_fix(text):
        return ' '.join(text.split())

    def remove_punc(text):
        exclude = set(string.punctuation)
        return ''.join(ch for ch in text if ch not in exclude)

    def lower(text):
        return text.lower()

    return white_space_fix(remove_articles(remove_punc(lower(s))))


def f1_score(prediction, ground_truth):
    prediction_tokens = [ps.stem(w) for w in normalize_answer(prediction).split()]
    ground_truth_tokens = [ps.stem(w) for w in normalize_answer(ground_truth).split()]
    common = Counter(prediction_tokens) & Counter(ground_truth_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0
    precision = 1.0 * num_same / len(prediction_tokens)
    recall = 1.0 * num_same / len(ground_truth_tokens)
    f1 = (2 * precision * recall) / (precision + recall)
    # print('# F1 #', prediction, ' | ', ground_truth, ' #', precision, recall, f1)
    # return recall
    return f1


def f1(prediction, ground_truth):
    predictions = [p.strip() for p in prediction.split(',')]
    ground_truths = [g.strip() for g in ground_truth.split(',')]
    # print('# F1 [multi-answer]#', predictions, ' | ', ground_truths, ' #', np.mean([max([f1_score(prediction, gt) for prediction in predictions]) for gt in ground_truths]))
    return np.mean([max([f1_score(prediction, gt) for prediction in predictions]) for gt in ground_truths])


def locomo_f1(category, output, answer):
    """The per-question branch of evaluation.py's eval_question_answering, for categories 1 to 4."""
    if category == 3:
        answer = answer.split(';')[0].strip()
    if category in [2, 3, 4]:
        return float(f1_score(output, answer))
    return float(f1(output, answer))


def load():
    url = f"https://raw.githubusercontent.com/snap-research/locomo/{REVISION}/data/locomo10.json"
    with open(common.fetch(url, os.path.join("locomo", "locomo10.json"), SHA256), encoding="utf-8") as fh:
        return json.load(fh)


def when(date_time):
    return datetime.strptime(date_time, "%I:%M %p on %d %B, %Y")


def session_items(sample):
    conv = sample["conversation"]
    a, b = conv["speaker_a"], conv["speaker_b"]
    items = []
    for n in sorted(int(k.split("_")[1]) for k in conv if re.fullmatch(r"session_\d+", k)):
        lines = [f"{t['speaker']}: {t['text']}" + (f" [shares {t['blip_caption']}]" if t.get("blip_caption") else "")
                 for t in conv[f"session_{n}"]]
        date_time = conv[f"session_{n}_date_time"]
        items.append({"source": common.SOURCE, "source_id": f"{sample['sample_id']}/session_{n}",
                      "ts": when(date_time).strftime("%Y-%m-%dT%H:%M:00Z"),
                      "title": f"Chat between {a} and {b}, session {n}",
                      "content": f"Chat between {a} and {b} at {date_time}\n\n" + "\n\n".join(lines),
                      "meta": {"session": n}})
    return items


def cases_and_groups(samples):
    cases, groups, people = [], {}, {}
    for s in samples:
        sid, conv = s["sample_id"], s["conversation"]
        groups[sid] = session_items(s)
        people[sid] = conv["speaker_a"], conv["speaker_b"]
        last = max(when(conv[k]) for k in conv if k.endswith("_date_time"))
        for i, qa in enumerate(s["qa"]):
            if qa["category"] not in CATEGORIES:
                continue
            sessions = sorted({int(m) for e in qa["evidence"] for m in re.findall(r"D(\d+):", e)})
            cases.append(common.Case(f"{sid}/{i}", sid, CATEGORIES[qa["category"]], qa["question"], str(qa["answer"]),
                                     last.strftime("%-d %B %Y"),
                                     tuple(f"{sid}/session_{n}" for n in sessions)))
    return cases, groups, people


def judge(case, response):
    prompt = ACCURACY_PROMPT.format(question=case.question, gold_answer=case.gold, generated_answer=response)
    for _ in range(3):
        reply = gateway.text(gateway.post("/v1/messages", {"model": JUDGE_MODEL, "max_tokens": 1000, "temperature": 0,
                                                           "messages": [{"role": "user", "content": prompt}]}))
        found = re.search(r"\{[^{}]*\}", reply)
        try:
            label = json.loads(found.group(0))["label"]
        except (AttributeError, ValueError, KeyError, TypeError):
            continue
        if label in ("CORRECT", "WRONG"):
            return label, reply
    raise ValueError(f"{JUDGE_MODEL} gave no CORRECT or WRONG label")


def scorer(case, response):
    label, reply = judge(case, response)
    category = next(k for k, v in CATEGORIES.items() if v == case.kind)
    return {"correct": label == "CORRECT", "f1": locomo_f1(category, response, case.gold), "judge_reply": reply}


def table(names, out=OUT):
    runs = [(n, common.read_rows(os.path.join(out, f"{n}.jsonl"))) for n in names]
    runs = [(n, rows) for n, rows in runs if rows]
    if not runs:
        return "no rows yet\n"
    lines = []
    for n, rows in runs:
        groups = [rows] + [[r for r in rows if r["kind"] == k] for k in CATEGORIES.values()]
        lines.append([f"{product.NAME} {n}", "J", *(common.pct(sum(r["correct"] for r in g), len(g)) for g in groups)])
        lines.append(["", "F1", *(f"{100 * sum(r['f1'] for r in g) / len(g):.1f}" if g else "n/a" for g in groups)])
    counts = [f"{k} ({sum(r['kind'] == k for r in runs[0][1])})" for k in CATEGORIES.values()]
    parts = [common.markdown(["Run", "Score", f"Overall ({len(runs[0][1])})", *counts], lines)]
    checks = [(n, common.work_checks(rows)) for n, rows in runs]
    head = list(checks[0][1])
    parts.append(common.markdown(["Run", *head], [[n, *(c[k] for k in head)] for n, c in checks]))
    misses = []
    for n, rows in runs:
        wrong = [r for r in rows if not r["correct"]]
        shown = sum(bool(set(r["evidence"]) & set((r["context_items"] or []) + r["read_items"])) for r in wrong)
        misses.append([n, len(wrong), len(wrong) - shown, shown])
    parts.append(common.markdown(["Run", "Wrong by J", "Evidence never reached the model",
                                  "Evidence reached, still wrong"], misses))
    parts.append(common.markdown(
        ["System", "Score", "Metric", "Answer model", "Judge", "Label", "Published or checked", "Source"],
        [[r["system"], r["score"], r["set"], r["answer_model"], r["judge"], r["label"], r["date"], r["source"]]
         for r in common.rivals("locomo")]))
    text = "\n\n".join(parts) + "\n"
    with open(os.path.join(out, "table.md" if out == OUT else f"{names[0]}.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--summaries", choices=("auto", "on", "off"), default="auto",
                   help=f"auto: on unless the run needs more than {common.MAX_SUMMARY_CALLS} summary calls")
    p.add_argument("--sample", type=int, help="run a random sample of N questions")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--limit", type=int, help="run only the first N questions")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--fresh", action="store_true", help="drop earlier rows and rerun every question")
    p.add_argument("--count", action="store_true", help="print the model calls the run would make, and stop")
    p.add_argument("--table", action="store_true", help="print the table from the rows already written")
    common.split_args(p)
    args = p.parse_args(argv)
    if args.table:
        print(table(common.run_names(OUT)))
        return 0
    cases, groups, people = cases_and_groups(load())
    cases, suffix = common.in_split("locomo", cases, "", args)
    cases = common.choose(cases, args.sample, args.seed, args.limit)
    planned = common.plan(cases, groups)
    summaries = {"on": True, "off": False}.get(
        args.summaries, planned["summary calls (one per long item)"] <= common.MAX_SUMMARY_CALLS)
    name = common.run_name("locomo10", summaries, args.sample, args.seed, args.limit) + suffix
    out = os.path.join(common.SPLIT_RESULTS, "locomo") if args.split else OUT
    print(f"{name}: {json.dumps(planned)}", file=sys.stderr)
    if args.count:
        return 0
    common.log_held_out("locomo", args, name)

    def system(case):
        a, b = people[case.group]
        return ANSWER_SYSTEM.format(a=a, b=b, today=case.today)

    meta = {"test": "locomo", "data_revision": REVISION, "data_sha256": SHA256, "judge_model": JUDGE_MODEL,
            "questions": len(cases), "categories": list(CATEGORIES.values()), "planned": planned}
    record = common.run_cases(name, cases, groups, out, system, scorer, summaries, args.workers, args.fresh, meta)
    print(table([name], out) if args.split else table(common.run_names(OUT)))
    return common.exit_status(record)


if __name__ == "__main__":
    sys.exit(main())
