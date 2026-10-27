#!/usr/bin/env python3
"""LongMemEval (Wu et al., ICLR 2025) through the check, scored with LongMemEval's own judge prompts.

    python3 bench/public/longmemeval.py --set s|oracle [--summaries auto|on|off] [--sample N [--seed S]]
                                        [--limit N] [--workers N] [--fresh] [--count] [--table]

Each question gets a fresh store of its haystack sessions, one item per session. The question goes through the
check as a new message, the answer model answers with the check's records and kb tools, and the judge grades the
answer with the prompt LongMemEval's evaluate_qa.py uses for that question type. Rows go to
bench/results/public/longmemeval/<run>.jsonl, one per question; a rerun runs only questions with no row yet.
--count prints the model calls the run would make, and makes none.
"""
import argparse
import json
import os
import statistics
import sys
from datetime import datetime

import common

import gateway
import product

JUDGE_MODEL = "gpt-6.1-sol"
OUT = os.path.join(common.RESULTS, "longmemeval")
REVISION = "98d7416c24c778c2fee6e6f3006e7a073259d48f"
SETS = {
    "s": ("longmemeval_s_cleaned.json", "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"),
    "oracle": ("longmemeval_oracle.json", "821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c"),
}
TYPES = ("single-session-user", "single-session-assistant", "single-session-preference", "multi-session",
         "knowledge-update", "temporal-reasoning")

ANSWER_SYSTEM = """You are an AI assistant that has chatted with this user many times before. The current date is \
{today}. Your earlier chats with the user are in their knowledge base, and knowledge base records may come before \
the user's message. Answer the message in at most three sentences. Use the records when they help. If neither the \
records nor general knowledge answer it, say that you don't know. Never guess facts about the user's life."""


# Copied verbatim from LongMemEval's src/evaluation/evaluate_qa.py at commit 9e0b455.
def get_anscheck_prompt(task, question, answer, response, abstention=False):
    if not abstention:
        if task in ['single-session-user', 'single-session-assistant', 'multi-session']:
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
            prompt = template.format(question, answer, response)
        elif task == 'temporal-reasoning':
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
            prompt = template.format(question, answer, response)
        elif task == 'knowledge-update':
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
            prompt = template.format(question, answer, response)
        elif task == 'single-session-preference':
            template = "I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
            prompt = template.format(question, answer, response)
        else:
            raise NotImplementedError
    else:
        template = "I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes if the model correctly identifies the question as unanswerable. The model could say that the information is incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? Answer yes or no only."
        prompt = template.format(question, answer, response)
    return prompt


def load(name):
    filename, sha256 = SETS[name]
    url = f"https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/{REVISION}/{filename}"
    with open(common.fetch(url, os.path.join("longmemeval", filename), sha256), encoding="utf-8") as fh:
        return json.load(fh)


def iso(date):
    return datetime.strptime(date, "%Y/%m/%d (%a) %H:%M").strftime("%Y-%m-%dT%H:%M:00Z")


def session_item(sid, date, turns):
    """One haystack session in the line style of the agent-session connectors."""
    first = next((t["content"] for t in turns if t["role"] == "user"), "")
    return {"source": common.SOURCE, "source_id": sid, "ts": iso(date),
            "title": first.strip().split("\n")[0][:100],
            "content": "Chat session\n\n" + "\n\n".join(f"{t['role'].upper()}: {t['content']}" for t in turns),
            "meta": {"session": sid}}


def cases_and_groups(entries):
    cases, groups = [], {}
    for e in entries:
        qid = e["question_id"]
        groups[qid] = [session_item(*s) for s in zip(e["haystack_session_ids"], e["haystack_dates"],
                                                       e["haystack_sessions"])]
        cases.append(common.Case(qid, qid, e["question_type"], e["question"], str(e["answer"]),
                                 e["question_date"], tuple(e["answer_session_ids"])))
    return cases, groups


def system(case):
    return ANSWER_SYSTEM.format(today=case.today)


def score(case, response):
    prompt = get_anscheck_prompt(case.kind, case.question, case.gold, response, abstention="_abs" in case.id)
    reply = gateway.text(gateway.post("/v1/messages", {"model": JUDGE_MODEL, "max_tokens": 10, "temperature": 0,
                                                       "messages": [{"role": "user", "content": prompt}]}))
    # evaluate_qa.py's own rule.
    return {"correct": "yes" in reply.lower(), "judge_reply": reply}


def accuracy(rows):
    by_type = {t: [r["correct"] for r in rows if r["kind"] == t] for t in TYPES}
    abstention = [r["correct"] for r in rows if "_abs" in r["id"]]
    cells = [len(rows), common.pct(sum(r["correct"] for r in rows), len(rows)),
             f"{100 * statistics.mean(statistics.mean(v) for v in by_type.values() if v):.1f}%"]
    cells += [common.pct(sum(v), len(v)) for v in by_type.values()]
    return cells + [common.pct(sum(abstention), len(abstention))]


def misses(rows):
    """Wrong answers split by whether an evidence session ever reached the answer model."""
    wrong = [r for r in rows if not r["correct"] and "_abs" not in r["id"]]
    shown = [r for r in wrong if set(r["evidence"]) & set((r["context_items"] or []) + r["read_items"])]
    unparsed = sum(r["judge_reply"].strip().lower().rstrip(".") not in ("yes", "no") for r in rows)
    return [len(wrong), len(wrong) - len(shown), len(shown), unparsed]


def table(names):
    runs = [(n, common.read_rows(os.path.join(OUT, f"{n}.jsonl"))) for n in names]
    runs = [(n, rows) for n, rows in runs if rows]
    if not runs:
        return "no rows yet\n"
    parts = [common.markdown(["Run", "Questions", "Overall", "Task-averaged", *TYPES, "Abstention"],
                             [[f"{product.NAME} {n}", *accuracy(rows)] for n, rows in runs])]
    checks = [(n, common.work_checks(rows)) for n, rows in runs]
    head = list(checks[0][1])
    parts.append(common.markdown(["Run", *head], [[n, *(c[k] for k in head)] for n, c in checks]))
    parts.append(common.markdown(
        ["Run", "Wrong (answerable)", "Evidence never reached the model", "Evidence reached, still wrong",
         "Judge replies not yes or no"], [[n, *misses(rows)] for n, rows in runs]))
    parts.append(common.markdown(
        ["System", "Score", "Set", "Answer model", "Judge", "Label", "Published or checked", "Source"],
        [[r["system"], r["score"], r["set"], r["answer_model"], r["judge"], r["label"], r["date"], r["source"]]
         for r in common.rivals("longmemeval")]))
    text = "\n\n".join(parts) + "\n"
    with open(os.path.join(OUT, "table.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--set", choices=sorted(SETS), default="s")
    p.add_argument("--summaries", choices=("auto", "on", "off"), default="auto",
                   help=f"auto: on unless the set needs more than {common.MAX_SUMMARY_CALLS} summary calls")
    p.add_argument("--sample", type=int, help="run a random sample of N questions")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--limit", type=int, help="run only the first N questions")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--fresh", action="store_true", help="drop earlier rows and rerun every question")
    p.add_argument("--count", action="store_true", help="print the model calls the run would make, and stop")
    p.add_argument("--table", action="store_true", help="print the table from the rows already written")
    args = p.parse_args(argv)
    if args.table:
        print(table(common.run_names(OUT)))
        return 0
    cases, groups = cases_and_groups(load(args.set))
    cases = common.choose(cases, args.sample, args.seed, args.limit)
    planned = common.plan(cases, groups)
    summaries = {"on": True, "off": False}.get(
        args.summaries, planned["summary calls (one per long item)"] <= common.MAX_SUMMARY_CALLS)
    name = common.run_name(args.set, summaries, args.sample, args.seed, args.limit)
    print(f"{name}: {json.dumps(planned)}", file=sys.stderr)
    if args.count:
        return 0
    meta = {"test": "longmemeval", "set": args.set, "data_revision": REVISION, "data_sha256": SETS[args.set][1],
            "judge_model": JUDGE_MODEL, "questions": len(cases), "planned": planned}
    record = common.run_cases(name, cases, groups, OUT, system, score, summaries, args.workers, args.fresh, meta)
    print(table(common.run_names(OUT)))
    return common.exit_status(record)


if __name__ == "__main__":
    sys.exit(main())
