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

## How a question runs

1. The tool loads `demo/month/items.jsonl` into a fresh store. The check stores the items with kb's own code and writes kb's summaries for long items, as ingest does.
2. The question arrives as a new message to an agent, with no conversation before it. The tool's lookup returns the text an agent would see before the message, or nothing.
3. The answer model (`claude-sonnet-5-5`) answers from that text. It is told it works for the month's persona and that today is October 1, 2026. When the product gives agents commands to read more, the answer model gets them as tools: for the check, `kb search` and `kb get`, which the check's own header tells agents to run. It gets at most 8 tool turns.
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
