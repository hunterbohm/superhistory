#!/usr/bin/env python3
"""Build the made-up month: demo/month/items.jsonl and demo/month/questions.jsonl.

    python3 bench/month/build.py              # write the files
    python3 bench/month/build.py --check      # rebuild in memory, validate, and fail if the files differ
    python3 bench/month/build.py --deny FILE  # also fail on any line of FILE (names that must not appear)

The same seed always writes the same bytes.
"""
import argparse
import importlib
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import model

sys.path.insert(0, os.path.dirname(HERE))
import product

ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "demo", "month")
SEED = 2026
MODULES = ("stories.copperline", "stories.fernbrook", "stories.harbor", "stories.quill", "stories.studio",
           "stories.personal", "noise.mail", "noise.sessions", "noise.life", "noise.code", "stories.none")
KINDS = ("single", "multi", "update", "temporal", "none")
FIRST, LAST = "2026-09-01T06:00:00Z", "2026-10-01T06:00:00Z"
PHONE = re.compile(r"\+1\d{10}\b|\(\d{3}\) \d{3}-\d{4}\b|\b\d{3}-\d{3}-\d{4}\b")


def collect(seed):
    model.configure(seed)
    items, questions = [], []
    for name in MODULES:
        story = importlib.import_module(name).build()
        items += story.items
        questions += story.questions
    return items, questions


def problems(items, questions, deny=()):
    found = []
    keys = Counter(i.key for i in items)
    ids = Counter((i.source, i.source_id) for i in items)
    found += [f"duplicate item key {k}" for k, n in keys.items() if n > 1]
    found += [f"duplicate source id {k}" for k, n in ids.items() if n > 1]
    by_key = {i.key: i for i in items}
    for i in items:
        if not FIRST <= i.ts < LAST:
            found.append(f"{i.key}: ts {i.ts} is outside the month")
        if not i.title.strip() or not i.content.strip():
            found.append(f"{i.key}: empty title or content")
    text = "\n".join(f"{i.title}\n{i.content}\n{json.dumps(i.meta)}" for i in items).lower()
    found += [f"phone number outside the 555-01xx range: {n}" for n in sorted(set(PHONE.findall(text)))
              if re.sub(r"\D", "", n)[-7:-2] != "55501"]
    for word in (product.NAME, *deny):
        if re.search(r"(?<![a-z])" + re.escape(word.lower()) + r"(?![a-z])", text):
            found.append(f"denied word in the month: {word!r}")
    qids = Counter(q.id for q in questions)
    found += [f"duplicate question id {k}" for k, n in qids.items() if n > 1]
    for q in questions:
        if q.kind not in KINDS:
            found.append(f"{q.id}: unknown kind {q.kind}")
        if (q.kind == "none") != (not q.evidence):
            found.append(f"{q.id}: kind none must have no evidence, and every other kind needs some")
        missing = [k for k in q.evidence if k not in by_key]
        if missing:
            found.append(f"{q.id}: evidence keys not in the month: {missing}")
            continue
        cited = "\n".join(by_key[k].title + "\n" + by_key[k].content for k in q.evidence).lower()
        found += [f"{q.id}: evidence lacks {p!r}" for p in q.evidence_has if p.lower() not in cited]
        found += [f"{q.id}: the month mentions {p!r}" for p in q.month_lacks
                  if re.search(rf"\b{re.escape(p.lower())}\b", text)]
        if q.kind != "none" and not q.evidence_has:
            found.append(f"{q.id}: no `evidence_has` phrases to ground the answer")
    n = len(questions)
    nones = sum(q.kind == "none" for q in questions)
    if not 150 <= n <= 250:
        found.append(f"{n} questions; want 150 to 250")
    if n and nones / n < 0.2:
        found.append(f"{nones} of {n} questions are kind none; want at least 20%")
    return found


def render(items, questions):
    ids = {i.key: i.source_id for i in items}
    rows = sorted((i.row() for i in items), key=lambda r: (r["ts"], r["source"], r["source_id"]))
    lines = lambda objs: "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in objs)
    return {"items.jsonl": lines(rows), "questions.jsonl": lines(q.row(ids) for q in questions)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--out", default=OUT)
    p.add_argument("--check", action="store_true", help="validate and compare with the files in --out")
    p.add_argument("--deny", help="a file with one denied name per line")
    args = p.parse_args(argv)
    deny = ()
    if args.deny:
        with open(args.deny, encoding="utf-8") as fh:
            deny = tuple(line.strip() for line in fh if line.strip())
    items, questions = collect(args.seed)
    bad = problems(items, questions, deny)
    files = render(items, questions)
    if args.check:
        for name, body in files.items():
            path = os.path.join(args.out, name)
            if not os.path.exists(path) or open(path, encoding="utf-8").read() != body:
                bad.append(f"{path} differs from a fresh build; run bench/month/build.py")
    for line in bad:
        print("problem:", line, file=sys.stderr)
    if bad:
        return 1
    if not args.check:
        os.makedirs(args.out, exist_ok=True)
        for name, body in files.items():
            with open(os.path.join(args.out, name), "w", encoding="utf-8") as fh:
                fh.write(body)
    kinds = Counter(q.kind for q in questions)
    sources = Counter(i.source for i in items)
    print(f"{len(items)} items ({', '.join(f'{s} {n}' for s, n in sorted(sources.items()))}), "
          f"{sum(len(i.content) for i in items):,} characters")
    print(f"{len(questions)} questions ({', '.join(f'{k} {kinds[k]}' for k in KINDS)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
