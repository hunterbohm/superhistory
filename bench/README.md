# Benchmarks

These benchmarks ask one question: when an agent gets a new message, does the knowledge base's check hand it what it needs, and stay quiet when nothing fits? Every number here comes from a results file with one row per question, and every run can be repeated from this folder.

The product's name lives in `bench/product.py`. This README calls it "the check", after the step it measures, and `kb`, its command.

## Rerun the month

The made-up month is `demo/month/` (see its README). One command loads it into each tool's fresh store, puts the 171 questions through the tool, has one answer model answer and one judge grade, and prints the table:

```bash
python3 bench/run.py month --tools none,oracle,check,check-cards
python3 bench/run.py table            # print the table from the saved rows
python3 bench/run.py rejudge          # grade saved answers again, after a gold or judge change
python3 bench/run.py rejudge --second bench/results/month/second-grading.jsonl   # grade again, keep both verdicts
```

Rows go to `bench/results/month/<tool>.jsonl`, and the table to `bench/results/month/table.md`. `second-grading.jsonl` holds a second grading of the same answers, both verdicts per row, which measures how often the judge agrees with itself. A rerun keeps the rows already written and runs only the missing questions; pass `--fresh` to start over.

Model calls go to any endpoint that speaks Anthropic's Messages API: `BENCH_BASE_URL` (default `http://127.0.0.1:8317`, a local CLIProxyAPI gateway) and `BENCH_API_KEY`. The judge model is a GPT model served through that gateway; with a plain Anthropic key, change `JUDGE_MODEL` in `run.py`.

The check runs from kb's own code until the product's package lands in this repo. `BENCH_KB_CODE` points at a copy of it; the numbers below used `git archive` of kb at 4a884a2, recorded in `run.json` by hash. Without the copy, the check's tools stop with a message saying so.

The rival tools need their own setup, all local, with no account:

| Tool | What to start first |
|---|---|
| `mem0` | A Python with `pip install "mem0ai[nlp,extras]==2.2.1" sentence-transformers` and the spaCy model `en_core_web_sm`. Run `run.py` with that Python. |
| `supermemory` | `docker run -d --name sm-bench --network host -e OPENAI_API_KEY=<gateway key> -e OPENAI_BASE_URL=http://127.0.0.1:8317/v1 -e OPENAI_MODEL=claude-haiku-5-5 -e SUPERMEMORY_DISABLE_TELEMETRY=1 -v <dir>:/data supermemory/supermemory:0.0.9`, then `SUPERMEMORY_KEY_FILE=<dir>/api-key`. |
| `claude-mem` | `HOME=<scratch> npx claude-mem@15.3.0 install --provider claude --no-auto-start`; in `<scratch>/.claude-mem/settings.json` set `CLAUDE_MEM_CLAUDE_AUTH_METHOD` to `gateway` and `CLAUDE_MEM_MODEL` to `claude-haiku-5-5`; put `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` in `<scratch>/.claude-mem/.env`; run `HOME=<scratch> claude-mem start`. |

`python3 bench/run.py latency --tools check,supermemory,claude-mem` times lookups alone, one at a time and alternating between tools, into `bench/results/month/latency.jsonl`.

## How a question runs

1. The tool loads `demo/month/items.jsonl` into a fresh store. The check stores the items with kb's own code and writes kb's summaries for long items, as ingest does.
2. The question arrives as a new message to an agent, with no conversation before it. The tool's lookup returns the text an agent would see before the message, or nothing.
3. The answer model (`claude-sonnet-5-5`) answers from that text. It is told it works for the month's persona and that today is October 1, 2026. When the product gives agents commands to read more, the answer model gets them as tools: `kb search` and `kb get` for the check (its own header tells agents to run them), `search_memory` for Mem0 (OpenMemory MCP's name), a search for Supermemory, and claude-mem's three MCP search tools (`search`, `timeline`, `get_observations`), served by claude-mem's own MCP server with its own schemas and replies. It gets at most 8 tool turns.
4. The judge (`gpt-6.1-sol`, a different model family from the answer model and from the check's own `claude-haiku-5-5`) compares the answer with the gold answer and returns correct or not, with a reason.

The check's own two model calls, and kb's summaries at ingest, run on `claude-haiku-5-5`, as they do in kb. The check's two prompts use the product's wording ("the user") from the package's `kb/check.py`; kb's own copy names its owner, and the query writer then put that name into searches.

## What each column means

| Column | Meaning |
|---|---|
| accuracy | Questions the judge graded correct, of all 171. |
| answerable | The same, for the 131 questions that have an answer in the month. |
| nothing fits | The same, for the 40 questions where nothing in the month fits. Correct means the agent said it didn't know, or answered a general question correctly, without inventing facts. |
| added nothing when nothing fits | Of those 40, how often the tool's lookup returned nothing at all. |
| evidence reached | Of the answerable questions, how often an item that holds the answer reached the agent, by the lookup or the agent's own tool calls. Only tools that report which items they returned have it. |
| tokens added per question | Tokens of lookup text plus tool output that entered the agent's context, counted with the answer model's tokenizer. |
| lookup p50 / p90 s | Seconds the lookup took, per question. |

## How each rival ran

Each rival ran its documented recommended setup, with two changes every self-hosted run needs here: its model is `claude-haiku-5-5` through the gateway, the same small model the check uses, because the defaults need an OpenAI key; and embeddings, where needed, are local.

| Tool | Version | Setup | What differs from its defaults |
|---|---|---|---|
| Mem0 | mem0ai 2.2.1, open source | Each month item added as one user message, oldest first. Lookup is the README's: `search(top_k=3)`, listed under "User Memories:". | LLM `claude-haiku-5-5` (default `gpt-5-mini`); embedder `BAAI/bge-base-en-v1.5` (default `text-embedding-3-small`); `nlp` and `extras` installed so its entity and BM25 search run. Mem0 says its published scores come from its platform, which has optimizations the open-source library lacks. |
| Supermemory | server 0.0.9 (docker), "lite" | Each item posted as one document, default processing; the run waited until all 471 had their memories. Lookup is the quickstart's chat harness: the profile, 5 memories and 3 document chunks. | `OPENAI_MODEL=claude-haiku-5-5` (default `gpt-5.1`); telemetry off. Supermemory says its hosted platform uses its own models for extraction. |
| claude-mem | 15.3.0 | Only agent sessions: the 81 Claude Code, Codex and Hermes items, replayed one at a time through the worker routes its hooks call (each prompt, each tool call with its output, the closing summary). Lookup is its session-start context for a folder outside any repo; per-prompt lookup is off by default, and a second row turns it on. | `CLAUDE_MEM_MODEL=claude-haiku-5-5` (default `claude-haiku-4-5`); telemetry off. Observations carry the replay's time, not the month's dates. |

Load notes from `run.json` and the run logs:

- Mem0 made 1,150 memories from 471 adds in 45 minutes. 22 adds stored nothing, because the model's fact-extraction JSON did not parse. Mem0's telemetry was on for this run, its default; the adapter now turns it off.
- Supermemory finished extracting memories from all 471 documents about two hours after they were posted, at about 3.5 documents a minute. A first run that posted them was stopped by a time limit; the second run reused the posted documents and waited for the rest.
- claude-mem took 142 minutes to compress the 81 sessions into 504 observations and 54 summaries. Replayed all at once, sessions park behind its two agent slots and never finish, so the replay waits for each session.
- claude-mem's agent tools are its own MCP server's, started the way its plugin registers it, so the agent sees the vendor's tool schemas and formatted replies. The rows in this folder come from that setup, against the same store.
