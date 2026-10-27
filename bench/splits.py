#!/usr/bin/env python3
"""Dev and test halves of every benchmark, fixed before any tuning of the check.

    python3 bench/splits.py           # write bench/splits/*.json from the data
    python3 bench/splits.py --check   # fail if the committed files differ from a fresh split

Tuning reads only dev numbers. Each test half runs at most three times in total, once under each label in RUNS, and
`held_out_run` logs every one in bench/splits/heldout-runs.tsv.

- Month: dev is month A (demo/month); test is month B (bench/month-b), written apart from any tuning.
- LongMemEval S: 250 and 250. Within each question type, abstention questions apart, questions sorted by the
  SHA-256 of their id alternate between dev and test.
- LoCoMo: dev is conversations 1 to 5, test 6 to 10, in the file's order.
"""
import argparse
import csv
import fcntl
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDER = os.path.join(HERE, "splits")
LOG = os.path.join(FOLDER, "heldout-runs.tsv")
SPLITS = ("dev", "test")
RUNS = ("baseline", "midpoint", "final")
LOG_FIELDS = ("date", "test", "label", "run", "code", "commit")


def longmemeval(entries):
    def key(e):
        qid = e["question_id"]
        return e["question_type"], "_abs" in qid, hashlib.sha256(qid.encode()).hexdigest()
    ordered = sorted(entries, key=key)
    return {"dev": sorted(e["question_id"] for e in ordered[0::2]),
            "test": sorted(e["question_id"] for e in ordered[1::2])}


def locomo(samples):
    ids = [s["sample_id"] for s in samples]
    return {"dev": ids[:5], "test": ids[5:]}


def path(test):
    return os.path.join(FOLDER, f"{test}.json")


def read(test, split):
    """The ids in one half of a test: question ids for LongMemEval, conversation ids for LoCoMo."""
    with open(path(test), encoding="utf-8") as fh:
        return set(json.load(fh)[split])


def held_out_run(test, label, code, commit, run):
    """Log `run` on a test half. Raises SystemExit for a label outside RUNS, or for a label already logged with another
    run or other code: rerunning a label only fills in the questions its run has no row for yet."""
    if label not in RUNS:
        raise SystemExit(f"a held-out run is labelled {', '.join(RUNS)}; got {label!r}")
    _hold(test)
    with open(LOG, "a+", encoding="utf-8", newline="") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        rows = list(csv.DictReader(fh, delimiter="\t"))
        earlier = [r for r in rows if r["test"] == test and r["label"] == label]
        if earlier and (earlier[0]["code"], earlier[0]["run"]) != (code, run):
            raise SystemExit(f"{test} {label} already ran as {earlier[0]['run']} with code {earlier[0]['code']}; "
                             f"this is {run} with code {code}")
        if earlier:
            return
        writer = csv.DictWriter(fh, LOG_FIELDS, delimiter="\t", lineterminator="\n")
        if not fh.tell():
            writer.writeheader()
        writer.writerow({"date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "test": test,
                         "label": label, "run": run, "code": code, "commit": commit})


_held = {}


def _hold(test):
    """One held-out run of `test` at a time on this computer, until this process exits: two at once would each run
    the questions that have no row yet."""
    fh = open(os.path.join(tempfile.gettempdir(), f"bench-heldout-{test}.lock"), "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise SystemExit(f"another held-out run of {test} is running; wait for it to end") from None
    _held[test] = fh


def fresh():
    sys.path.insert(0, os.path.join(HERE, "public"))
    import locomo as locomo_test
    import longmemeval as longmemeval_test
    return {"longmemeval-s": longmemeval(longmemeval_test.load("s")), "locomo": locomo(locomo_test.load())}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true")
    args = p.parse_args(argv)
    bad = 0
    for test, halves in fresh().items():
        body = json.dumps(halves, indent=1) + "\n"
        if args.check:
            if not os.path.exists(path(test)) or open(path(test), encoding="utf-8").read() != body:
                print(f"{path(test)} differs from a fresh split", file=sys.stderr)
                bad += 1
            continue
        os.makedirs(FOLDER, exist_ok=True)
        with open(path(test), "w", encoding="utf-8") as fh:
            fh.write(body)
        print(f"{test}: dev {len(halves['dev'])}, test {len(halves['test'])}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
