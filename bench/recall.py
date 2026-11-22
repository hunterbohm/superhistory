#!/usr/bin/env python3
"""Recall on a real store: does plain search put the record that holds the current answer in the top 5, and does an
out-of-date record rank above it?

    python3 bench/recall.py QUESTIONS.jsonl [--db PATH] [--limit 20] [--top 5] [--json]
    python3 bench/recall.py QUESTIONS.jsonl --command "superhistory search"   # the installed command instead
    python3 bench/recall.py QUESTIONS.jsonl --skip 2026-11-21-recall-work   # leave out the sessions that wrote it
    python3 bench/recall.py QUESTIONS.jsonl --rerank [--config PATH]   # the [ai] model orders the hits (kb.rerank)

The store opens read-only, at --db or at the store SUPERHISTORY_HOME names. The question file is yours and stays
outside this repository: it names your own records. One JSON object per line:

    {"q": "what do we charge teams of 26 to 50", "want": [{"path": "offer/SALES-OFFER.md"}],
     "stale": [{"text": ["$10,000", "26"]}], "topic": "price"}

`want` and `stale` are lists of matchers. A hit matches a matcher when every field the matcher gives fits: `id` (the
record id), `source`, `kind`, `path` (part of the item's source id or its file path), `text` (one string or a list,
each found in the title or text, case ignored). Ids change when ingest refreshes an item, so prefer `path` and
`text`. `stale` may be left out for a question whose answer never changed.

Each line printed is one question: whether a wanted record is in the top --top, its rank (- when not in the first
--limit), and "STALE" when a stale record ranks above it. The last lines total them. With --command the ids are read
from the command's printed results and each is looked up in the store, so the same matchers apply; --command reads
the store SUPERHISTORY_HOME names, so it takes no --db. --rerank searches as `superhistory search` does with [search]
rerank on: it reads the config at --config, or the user's, and turns rerank on whatever the file says, so one question
file compares both orders. It calls the [ai] model for each question of two or more words.

The agent sessions that wrote or ran the questions hold their words and land in the store at the next ingest, where
they outrank every real answer. Each --skip drops the hits whose item id, file path or working folder holds the given
text, such as the folder those sessions ran in, before they take a place.
"""
import argparse
import dataclasses
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from kb import rerank, search, store  # noqa: E402


SPARE = 20


def load(path):
    questions = []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if line and not line.startswith("#"):
                q = json.loads(line)
                if not q.get("q") or not q.get("want"):
                    raise SystemExit(f"{path}:{n}: a question needs `q` and `want`")
                questions.append(q)
    return questions


def matches(hit, matcher):
    if "id" in matcher and hit["id"] != matcher["id"]:
        return False
    for field in ("source", "kind"):
        if field in matcher and hit[field] != matcher[field]:
            return False
    if "path" in matcher and matcher["path"] not in hit["source_id"] + " " + str(hit["meta"].get("path", "")):
        return False
    texts = matcher.get("text", [])
    body = (hit["title"] + "\n" + hit["content"]).lower()
    return all(t.lower() in body for t in ([texts] if isinstance(texts, str) else texts))


def skipped(hit, skip):
    where = " ".join((hit["source_id"], str(hit["meta"].get("path", "")), str(hit["meta"].get("cwd", ""))))
    return any(s in where for s in skip)


def first(hits, matchers):
    return next((i for i, h in enumerate(hits, 1) if any(matches(h, m) for m in matchers)), None)


def rows(db, ids):
    """The hits the installed command printed, as search() would return them, in its order."""
    found = {}
    for rid, kind, src, sid, title, content, meta in db.execute(
            f"SELECT id, kind, source, source_id, title, content, metadata FROM docs "
            f"WHERE id IN ({','.join('?' * len(ids))})", ids):
        found[rid] = {"id": rid, "kind": kind, "source": src, "source_id": sid, "title": title, "content": content,
                      "meta": json.loads(meta)}
    return [found[i] for i in ids if i in found]


def run_command(command, db, query, limit):
    out = subprocess.run(shlex.split(command) + [query, "--limit", str(limit)], capture_output=True, text=True,
                         check=True).stdout
    ids = [int(i) for i in re.findall(r"^\d+\. \S+(?: \([^)]*\))?\s+\S+\s+\S+ #(\d+)", out, re.M)]
    return rows(db, ids) if ids else []


def score(question, hits, top):
    want = first(hits, question["want"])
    stale = first(hits, question.get("stale", []))
    return {"q": question["q"], "topic": question.get("topic", ""), "rank": want, "top": bool(want and want <= top),
            "stale_rank": stale, "stale_above": bool(stale and (want is None or stale < want)),
            "changed": bool(question.get("stale"))}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("questions")
    p.add_argument("--db", help="the store file (default: the one SUPERHISTORY_HOME names)")
    p.add_argument("--limit", type=int, default=20, help="results read per question")
    p.add_argument("--top", type=int, default=5, help="the rank that counts as found")
    p.add_argument("--command", help="run this search command instead of the package's search()")
    p.add_argument("--skip", action="append", default=[], help="leave out hits whose path holds this text")
    p.add_argument("--json", action="store_true", help="one JSON result per line")
    p.add_argument("--rerank", action="store_true", help="let the [ai] model order the hits, as kb.rerank does")
    p.add_argument("--config", help="the config file --rerank reads (default: the user's)")
    args = p.parse_args(argv)
    if args.command and (args.db or args.rerank):
        p.error("--command searches the store SUPERHISTORY_HOME names with its own config; drop --db and --rerank")
    from kb import config, paths
    if not args.db:
        args.db = paths.Paths.for_user().db
    settings = None
    if args.rerank:
        settings = config.load(args.config or paths.Paths.for_user().config_file)
        settings = dataclasses.replace(settings, search=dataclasses.replace(settings.search, rerank=True))
    db = store.connect(args.db, readonly=True)
    results = []
    for question in load(args.questions):
        if args.command:
            # Asked for more than --limit, so the skipped hits do not shorten the list.
            hits = run_command(args.command, db, question["q"], args.limit + SPARE)
            hits = [h for h in hits if not skipped(h, args.skip)][:args.limit]
        elif settings:
            hits = rerank.rerank(db, settings, question["q"], limit=args.limit,
                                 keep=lambda h: not skipped(h, args.skip))
        else:
            hits = search.search(db, question["q"], limit=args.limit, keep=lambda h: not skipped(h, args.skip))
        r = score(question, hits, args.top)
        results.append(r)
        if args.json:
            print(json.dumps(r))
        else:
            mark = "yes" if r["top"] else "no "
            print(f"{mark} rank {r['rank'] or '-':>2}  {'STALE ' if r['stale_above'] else '      '}"
                  f"{r['topic'][:14]:<14} {r['q'][:80]}")
    changed = [r for r in results if r["changed"]]
    print(f"top {args.top}: {sum(r['top'] for r in results)} of {len(results)}; "
          f"stale above current: {sum(r['stale_above'] for r in changed)} of {len(changed)} changed questions",
          file=sys.stderr if args.json else sys.stdout)


if __name__ == "__main__":
    main()
