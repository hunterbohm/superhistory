#!/usr/bin/env python3
"""The final held-out pass: the final code and the baseline's code on each test half at the same time, in the same
question order, so both share whatever the gateway and its models do meanwhile.

    python3 bench/paired.py                        # every test half, one pair after another
    python3 bench/paired.py --tests month
    python3 bench/paired.py --dry-run              # every check, and the commands, with nothing logged or run

The final code is this checkout, committed and pushed. The baseline runs from a second checkout of the same commit
whose kb/ is the baseline's, so the harness, the answer loop and the judge are the same and only the check differs;
its code id must equal the one the baseline's own held-out runs logged. Before either run starts, both are logged in
bench/splits/heldout-runs.tsv and bench/splits/paired-freeze.json records both commits and code ids; a later start
with other code refuses. A pair stopped by a rate limit is resumed by the same command, which fills in only the
questions with no row. Then `python3 bench/compare.py final baseline-paired --split test --paired`.
"""
import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import splits  # noqa: E402

OUT = os.path.join(HERE, "results", "hillclimb")
FREEZE = os.path.join(splits.FOLDER, "paired-freeze.json")
CHECKOUT = os.path.expanduser(os.environ.get("BENCH_PAIRED_BASELINE", "~/.cache/kb-bench/paired-baseline"))
LOGS = os.path.expanduser(os.environ.get("BENCH_LOGS", "~/.cache/kb-bench/logs/hillclimb"))
LABELS = ("final", "baseline-paired")
# Per test half: the command, the run name it logs, and the results it writes, as each runner names them.
TESTS = {
    "month": (["run.py", "month", "--month", "b", "--tools", "check"], "b-{label}:check", ["month/b-{label}"]),
    "longmemeval-s": (["public/longmemeval.py", "--set", "s", "--split", "test"], "s-without-summaries-test-{label}",
                      [f"longmemeval/s-without-summaries-test-{{label}}{ext}" for ext in (".jsonl", ".md", ".run.json")]),
    "locomo": (["public/locomo.py", "--split", "test"], "locomo10-with-summaries-test-{label}",
               [f"locomo/locomo10-with-summaries-test-{{label}}{ext}" for ext in (".jsonl", ".md", ".run.json")]),
}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def code_id(cwd):
    """The code id the checkout's own harness logs for its kb/."""
    return subprocess.run([sys.executable, "-c", "import sys; sys.path[:0] = ['bench']; from tools import check; "
                           "print(check.code_id())"], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def logged_baseline():
    """The code id and commit every baseline held-out run logged; they must agree."""
    with open(splits.LOG, encoding="utf-8") as fh:
        found = {(r["code"], r["commit"]) for r in csv.DictReader(fh, delimiter="\t") if r["label"] == "baseline"}
    if len(found) != 1:
        raise SystemExit(f"the baseline runs logged {len(found)} code ids or commits, not one: {sorted(found)}")
    return found.pop()


def baseline_checkout(folder, head, base, expected, code=code_id, repo=ROOT):
    """A checkout of `head` whose kb/ is `base`'s, files `base` lacks removed. Reused when it is already that, and
    refused when its kb/ does not hash to `expected`."""
    if not os.path.exists(folder):
        git(repo, "worktree", "add", "--detach", folder, head)
        git(folder, "checkout", base, "--", "kb")
        extra = set(git(folder, "ls-files", "kb").split()) - set(git(folder, "ls-tree", "-r", "--name-only", base,
                                                                      "--", "kb").split())
        if extra:
            git(folder, "rm", "-q", "--", *sorted(extra))
    if git(folder, "rev-parse", "HEAD") != head:
        raise SystemExit(f"{folder} is at another commit; remove it and run again")
    changed = git(folder, "diff", "--name-only", "HEAD").split()
    if any(not path.startswith("kb/") for path in changed):
        raise SystemExit(f"{folder} differs from {head[:7]} outside kb/: {changed}")
    for cache in (os.path.join(dirpath, d) for dirpath, dirs, _ in os.walk(os.path.join(folder, "kb"))
                  for d in dirs if d == "__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    found = code(folder)
    if found != expected:
        raise SystemExit(f"the baseline checkout's kb/ hashes to {found}, not the {expected} its runs logged")
    return found


def freeze(head, final, base, baseline):
    """Record both codes once; refuse a later start whose code differs."""
    record = {"final_commit": head, "final_code": final, "baseline_kb_commit": base, "baseline_code": baseline,
              "harness_commit": head, "labels": list(LABELS)}
    if os.path.exists(FREEZE):
        with open(FREEZE, encoding="utf-8") as fh:
            earlier = json.load(fh)
        if {k: v for k, v in earlier.items() if k != "date"} != record:
            raise SystemExit(f"the code differs from the frozen code in {FREEZE}; nothing runs")
        return
    with open(FREEZE, "w", encoding="utf-8") as fh:
        json.dump({"date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **record}, fh, indent=1)
        fh.write("\n")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tests", default=",".join(TESTS))
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    tests = args.tests.split(",")
    if set(tests) - set(TESTS):
        raise SystemExit(f"tests are {', '.join(TESTS)}")
    if git(ROOT, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("commit the final code first: the checkout has changes")
    head = git(ROOT, "rev-parse", "HEAD")
    if not git(ROOT, "branch", "-r", "--contains", head):
        raise SystemExit("push the final code first: no remote branch holds this commit")
    expected, base = logged_baseline()
    baseline = baseline_checkout(CHECKOUT, head, base, expected)
    final = code_id(ROOT)
    python = os.environ.get("BENCH_PYTHON", sys.executable)
    commands = {test: [(label, folder, [python, os.path.join(folder, "bench", TESTS[test][0][0]), *TESTS[test][0][1:],
                                        "--label", label, "--workers", str(args.workers)])
                       for label, folder in zip(LABELS, (ROOT, CHECKOUT))] for test in tests}
    if args.dry_run:
        print(f"final {final} at {head}; baseline-paired {baseline}, kb from {base}, in {CHECKOUT}")
        for test in tests:
            for label, folder, command in commands[test]:
                print(f"{test} {label} logs {TESTS[test][1].format(label=label)}: {' '.join(command)}")
        return 0
    freeze(head, final, base, baseline)
    for test in tests:
        command, name, _ = TESTS[test]
        for label, code in zip(LABELS, (final, baseline)):
            splits.held_out_run(test, label, code, head, name.format(label=label), hold=False)
    env = {**os.environ, "BENCH_HELDOUT_LOG": os.path.abspath(splits.LOG)}
    os.makedirs(LOGS, exist_ok=True)
    for test in tests:
        results = TESTS[test][2]
        runs = []
        for label, folder, command in commands[test]:
            log = open(os.path.join(LOGS, f"paired-{test}-{label}.log"), "a", encoding="utf-8")
            runs.append((label, log, subprocess.Popen(command, cwd=folder, env=env, stdout=log,
                                                      stderr=subprocess.STDOUT)))
        failed = []
        for label, log, proc in runs:
            if proc.wait():
                failed.append(label)
            log.close()
        for path in results:
            source = os.path.join(CHECKOUT, "bench", "results", "hillclimb", path.format(label=LABELS[1]))
            target = os.path.join(OUT, path.format(label=LABELS[1]))
            if os.path.isdir(source):
                shutil.copytree(source, target, dirs_exist_ok=True)
            elif os.path.exists(source):
                shutil.copy(source, target)
        print(f"{test}: " + ("done" if not failed else f"{', '.join(failed)} stopped; see {LOGS}/paired-{test}-*.log "
                             "and run the same command again once the cause has passed"))
        if failed:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
