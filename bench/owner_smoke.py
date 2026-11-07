#!/usr/bin/env python3
"""A real-client smoke test of `kb mcp`: does Claude Code call the tools unprompted, and what do they cost?

    python3 bench/owner_smoke.py [--questions 10] [--out DIR]

Ten dev-month questions, no scoring. `claude -p` runs against a scratch copy of the dev month's store, through the
server in kb/mcp.py, with the question as the whole prompt. Claude Code runs from an empty folder with a config
folder of its own, so none of the owner's settings, hooks, memory or knowledge base reach it, its built-in tools are
off and only the server's two tools remain. The model endpoint comes from the same BENCH_ variables as the rest of
the bench, passed to the child through its environment only. The first request's input tokens with the server and
without it, on the same prompt, give what the tool definitions add.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gateway
import run

ROOT = os.path.dirname(HERE)
SCRATCH = os.path.expanduser(os.environ.get("OWNER_SCRATCH", "~/.cache/kb-bench/owner-questions"))
OUT = os.path.join(HERE, "results", "owner-questions", "smoke")
MODEL = "claude-sonnet-5-5"
PERSONA = "You work for Jules Marlow, who runs Marlow Works, a one-person automation business in Denver. Today is " \
          "Thursday, October 1, 2026."
TIMEOUT = 600


def usage_total(usage):
    return sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def claude(prompt, workdir, home, mcp_config=None, tools=()):
    """One `claude -p` run. Returns the parsed stream: events and the final result."""
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", MODEL,
           "--no-session-persistence", "--tools", "", "--append-system-prompt", PERSONA,
           "--permission-mode", "dontAsk"]
    if mcp_config:
        cmd += ["--mcp-config", mcp_config, "--strict-mcp-config", "--allowedTools", *tools]
    env = {"PATH": os.environ["PATH"], "HOME": workdir, "CLAUDE_CONFIG_DIR": home,
           "ANTHROPIC_BASE_URL": gateway.base_url(), "ANTHROPIC_AUTH_TOKEN": gateway._key(),
           "DISABLE_TELEMETRY": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    done = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=TIMEOUT)
    events = []
    for line in done.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return events, done.returncode, done.stderr[-300:].replace(gateway.base_url(), "<gateway>")


def summarize(events):
    uses, first_usage, result = [], None, None
    for e in events:
        if e.get("type") == "assistant":
            message = e.get("message") or {}
            if first_usage is None and message.get("usage"):
                first_usage = message["usage"]
            uses += [b["name"] for b in message.get("content") or [] if b.get("type") == "tool_use"]
        if e.get("type") == "result":
            result = e
    return {"tool_calls": uses, "first_request_input_tokens": usage_total(first_usage) if first_usage else None,
            "turns": (result or {}).get("num_turns"), "answer": (result or {}).get("result"),
            "is_error": (result or {}).get("is_error"),
            "result_usage": {k: v for k, v in ((result or {}).get("usage") or {}).items()
                             if isinstance(v, (int, float))}}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--questions", type=int, default=10)
    p.add_argument("--out", default=OUT)
    args = p.parse_args(argv)
    gateway.require()
    questions = run.read_jsonl(os.path.join(run.MONTH, "questions.jsonl"))
    picked = questions[::max(1, len(questions) // args.questions)][:args.questions]
    store_dir = os.path.join(SCRATCH, "stores", "a-haiku")
    work = tempfile.mkdtemp(prefix="owner-smoke-")
    try:
        kb_home = os.path.join(work, "kb")
        shutil.copytree(store_dir, kb_home, ignore=shutil.ignore_patterns("stats.json"))
        config = os.path.join(work, "mcp.json")
        with open(config, "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": {"kb": {"command": sys.executable, "args": ["-m", "kb", "mcp"],
                                             "env": {"KB_HOME": kb_home, "PYTHONPATH": ROOT}}}}, fh)
        empty, home = os.path.join(work, "empty"), os.path.join(work, "claude-home")
        os.makedirs(empty)
        os.makedirs(home)
        os.makedirs(args.out, exist_ok=True)
        rows = []
        tools = ("mcp__kb__search", "mcp__kb__get")
        for q in picked:
            events, code, err = claude(q["question"], empty, home, config, tools)
            row = {"id": q["id"], "kind": q["kind"], "question": q["question"], "exit": code, **summarize(events)}
            if err and code:
                row["stderr"] = err
            rows.append(row)
            print(q["id"], code, row["tool_calls"], row["first_request_input_tokens"], file=sys.stderr)
        plain = []
        for q in picked[:2]:
            events, code, err = claude(q["question"], empty, home)
            plain.append({"id": q["id"], "exit": code, **summarize(events)})
        with open(os.path.join(args.out, "rows.jsonl"), "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows + [{"without_server": r} for r in plain])
        with_server = [r for r in rows if r["id"] in {x["id"] for x in plain}]
        added = [w["first_request_input_tokens"] - n["first_request_input_tokens"]
                 for w in with_server for n in plain
                 if w["id"] == n["id"] and w["first_request_input_tokens"] and n["first_request_input_tokens"]]
        summary = {"questions": len(rows), "called_any_tool": sum(bool(r["tool_calls"]) for r in rows),
                   "calls": sum(len(r["tool_calls"]) for r in rows),
                   "mean_calls": sum(len(r["tool_calls"]) for r in rows) / len(rows),
                   "tool_definition_tokens_added_first_request": added, "model": MODEL}
        with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=1)
        print(json.dumps(summary, indent=1))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
