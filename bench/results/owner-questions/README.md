# Owner questions: an MCP server, and stronger-model facts and notes

Two questions, both measured on month A, the dev month (171 questions, 131 with an answer and 40 with nothing to find). One run of each arm, so one question moves a score by 0.6 points. Answer model `claude-sonnet-5-5`, judge `gpt-6.1-sol`, the check's own calls and the summaries on `claude-haiku-5-5`, as bench/README.md describes. No held-out half was read, and nothing here ran under a test label.

The tables are `part-a/table.md` and `part-b/table.md`, rebuilt from the rows by `python3 bench/owner.py tables`. Rows are in `part-a/` and `part-b/`, one file per arm, one row per question. `ledger.jsonl` holds every model call by stage. `smoke/` holds the real-client test.

## What was measured

**Handled right** is bench/run.py's rule: a correct answer, and for a nothing-to-find question, nothing added before the message. It is vacuous for pull arms, which add nothing before the message by construction, so every table also gives a **strict** reading that counts a tool call that showed the agent a record as added. **Right of the answerable** is correct answers among the 131. **Quiet** is the share of the 40 nothing-to-find questions with nothing added. **Tokens added** are the lookup's cards plus tool output, counted with the answer model's tokenizer. **Latency** is seconds from the question to the answer, the pre-lookup and every tool call included, the judge excluded. Paired columns compare each arm with the reference question by question: the change in points with a 95% interval from 10,000 paired resamples (bench/compare.py's method), and McNemar's exact test on the questions only one of the two got right.

**The spread of one pass.** The same code, run again, moves several points. On month A the committed same-code dev runs (`results/hillclimb/month/a-noise1` to `a-noise3`) scored 159, 158 and 161 handled right of 171 (93.0, 92.4 and 94.2%). In this task, Part A's arm 1 and Part B's arm 1 are the same code on two fresh stores and scored 157 and 160. So a gap of about three points, five questions, is inside the spread.

## Part A: is an MCP server a good idea?

One store, built once by haiku and shared by every arm: 41 of the 42 long items summarized, and one codex session got no summary because the model's reply held no JSON (the shipped summarizer marks it failed and moves on). Arms:

1. **Push and pull**, what ships: the check's cards before the message, plus `kb_search` and `kb_get`.
2. **Pull only**: nothing before the message; `search` and `get` with the descriptions in `kb/mcp.py`, which the pull arms read from the same `TOOLS` table the real server serves and call through the same `kb.mcp.call`.
3. **Pull with lookup**: arm 2 plus a `lookup` tool that runs the check (query writer and picker) on a message the agent writes, defined in `bench/tools/owner.py`.
4. **Push only**: cards, no tools.

The answer model's system prompt is the month's, unchanged for every arm. It says records may come before the message and does not mention tools, so in the pull arms the tool descriptions alone tell the agent the knowledge base exists.

| Arm | Handled right | Right, of the answerable | Quiet, nothing to find | Mean tokens added | Plus the check's model tokens | Latency p50 / mean | Questions with a tool call | Mean tool calls |
|---|---|---|---|---|---|---|---|---|
| 1. Push and pull | 157 (91.8%) | 119 of 131 | 38 of 40 | 2,090 | 2,981 | 9.5 s / 10.4 s | 89.5% | 2.38 |
| 2. Pull only | 160 (93.6%) | 121 of 131 | 40 of 40 | 3,941 | 0 | 8.2 s / 8.9 s | 89.5% | 3.57 |
| 3. Pull with lookup | 162 (94.7%) | 122 of 131 | 40 of 40 | 2,929 | 2,545 | 9.7 s / 10.9 s | 89.5% | 3.39 |
| 4. Push only | 88 (51.5%) | 49 of 131 | 39 of 40 | 277 | 2,823 | 7.5 s / 8.1 s | 0% | 0 |

Against arm 1, in points of handled right, with the 95% interval and the questions only one arm got right:

| Arm | Change | Only the arm vs only arm 1 | McNemar p | Strict change |
|---|---|---|---|---|
| 2. Pull only | +1.8 (−1.2 to +4.7) | 5 vs 2 | 0.45 | +1.2 (−1.8 to +4.1) |
| 3. Pull with lookup | +2.9 (−0.6 to +6.4) | 7 vs 2 | 0.18 | +1.8 (−1.2 to +4.7) |
| 4. Push only | −40.4 (−48.0 to −32.7) | 2 vs 71 | below 0.001 | −28.7 (−38.6 to −18.1) |

**What it shows.**
- **Accuracy.** Pull only and pull with lookup match the shipped arm. Their gaps, +1.8 and +2.9 points, sit inside the one-pass spread and inside their own intervals. The cards alone, without tools, cannot answer: 49 of 131 against 119 to 122. The agent's reads carry the answers, as bench/README.md found.
- **Tokens.** Pull only adds 3,941 tokens against 2,090, 89% more (+70% to +110%) in the agent's prompt, and spends none of the check's roughly 2,900 haiku tokens. "Tokens added" counts each tool output once, so it leaves out the conversation the agent re-reads on every turn and the tool definitions (614 tokens for arm 2, 772 for arm 3, 543 for arm 1, gateway token count). The ledger counts every call's input and output tokens, answers and the check's own calls together, leaving out the judge: arm 1 spent 1,454,726 (509,824 haiku and 944,902 sonnet), arm 2 1,773,413 (all sonnet), arm 3 1,918,918 (435,171 haiku and 1,483,747 sonnet) and arm 4 603,065. That is 8,507, 10,371 and 11,222 per question for arms 1 to 3. So pull only spends 22% more tokens than the shipped arm, and 88% more of them on the answer model, which is the pricier one. These are raw token counts with no discount for cached input and no prices applied, so what each arm costs in money is not measured here.
- **Latency.** Pull only answered fastest of the three that answer well, at a median 8.2 s against 9.5 s, since it skips the check's two model calls (median lookup 3.6 s in arm 1). The agent made one more call on average to make up for it.
- **Quiet.** The shipped definition rewards pull by construction. Read strictly, all three tool arms look equally noisy: on 22 of the 40 nothing-to-find questions the agent searched, got records back and still said it did not know, in arms 1, 2 and 3 alike (strict quiet 18 of 40). Arm 4 added records on 1 of 40. Every arm said it did not know or answered correctly on 39 or 40 of the 40, so the noise cost no wrong answers here.
- **Tool use.** The agent called a tool on 153 of 171 questions in each of arms 1 to 3, the same count by coincidence. Where nothing fits it still called 2.1 to 2.4 times on average.

**Winner.** No arm beat the shipped one on right answers: pull only +1.8 points (−1.2 to +4.7) and pull with lookup +2.9 (−0.6 to +6.4), both inside the spread. On this month an MCP server gives the answers the hook gives, with a faster answer when it replaces the lookup, and more tokens: 22% more in total and 88% more on the answer model. That is parity with a cost trade, not a win. The cards-only arm loses, so the hook's cards are not a substitute for tools.

### Real client smoke test (no scoring)

`bench/owner_smoke.py` ran `claude -p` (Claude Code 2.1.318, `claude-sonnet-5-5`) on 10 dev-month questions against a scratch copy of the month's store through `kb mcp` (tools `search` and `get`, `--mcp-config`, `--strict-mcp-config`). The prompt was the question alone, with a one-line persona appended to the system prompt and nothing about tools. Claude Code ran from an empty folder with its own config folder, its built-in tools off, and none of the owner's settings, hooks, memory or knowledge base. Rows are in `smoke/rows.jsonl`, the summary in `smoke/summary.json`.

- **Unprompted calls.** It called the tools on 9 of 10 questions, 33 calls in all (3.3 per question): a search or two, then `get` on the hits. The tenth, a general-knowledge question, got no call. Every run exited 0 and none ended in an error.
- **Tool definitions.** The first request's input tokens were 3,187 to 3,200 with the server and 2,382 and 2,370 without it on the same two prompts, so the two tools add 818 tokens to every request in Claude Code (the gateway counts 614 for the bare definitions; the rest is Claude Code's `mcp__kb__` names and its framing). The size of the addition suggests Claude Code loaded both definitions directly rather than behind a tool search; the run did not check which.
- **Unmeasured.** Answer quality, because the run is unscored. The tokens `claude -p` spent beyond the first request (the result's usage field covers only its last turn), so they are not in the ledger. The ten questions and the two baseline runs made 43 and 2 model turns; a 1-question trial before them made 6 more.

## Part B: should a stronger model extract facts, and are notes worth it?

Each arm has a fresh store of the same month, asked the shipped way (cards plus `kb_search` and `kb_get`), except the facts-only arms, whose `kb_get` returns the stored fact itself, since no item text is stored.

1. **Shipped**: summaries and atoms by haiku. A new store, 42 items summarized, no failures.
2. **Sonnet** writes the summaries and atoms. A new store, 42 items, no failures.
3. **Notes**: a copy of arm 1's store, then `claude-sonnet-5-5` reads each of the 81 agent sessions (Claude Code, Codex, Hermes) and writes short notes of its decisions and facts. The prompt gives the model only the session's text (`bench/owner_store.py`, `NOTES_SYSTEM`), so the agent under test never writes a note. 516 notes, no failures. A note is stored as an `atom` row marked `atom = "note"`, which search ranks and shows as it does an atom; a real feature would want a kind of its own.
4. **Facts only**: a copy of arm 3's store with every row that is not an atom deleted, so search sees atoms and notes and no raw text and no summaries. 794 rows from 81 items.
4b. **Beyond the brief**: the same, after sonnet also wrote notes for the 390 items that are not agent sessions (896 notes, 4 items got none after unparseable replies), so facts exist for 467 of 471 items. It answers a question the brief's arm 4 cannot: how much of arm 4's loss is coverage.

| Arm | Handled right | Right, of the answerable | Quiet, nothing to find | Mean tokens added | Latency p50 / mean | Questions with a tool call | Mean tool calls |
|---|---|---|---|---|---|---|---|
| 1. Shipped, haiku | 160 (93.6%) | 122 of 131 | 38 of 40 | 2,039 | 9.6 s / 10.2 s | 89.5% | 2.32 |
| 2. Sonnet summaries and atoms | 160 (93.6%) | 122 of 131 | 38 of 40 | 2,027 | 10.3 s / 10.8 s | 89.5% | 2.35 |
| 3. Plus sonnet notes | 158 (92.4%) | 119 of 131 | 39 of 40 | 2,192 | 9.6 s / 10.3 s | 89.5% | 2.37 |
| 4. Facts only | 62 (36.3%) | 24 of 131 | 38 of 40 | 3,098 | 11.0 s / 11.7 s | 89.5% | 3.43 |
| 4b. Facts only, notes for every item | 136 (79.5%) | 101 of 131 | 35 of 40 | 2,017 | 8.5 s / 9.5 s | 89.5% | 2.72 |

Against arm 1:

| Arm | Change in handled right | Only the arm vs only arm 1 | McNemar p | Right of the answerable, change |
|---|---|---|---|---|
| 2. Sonnet | +0.0 (−2.9 to +2.9) | 3 vs 3 | 1.00 | +0.0 (−3.1 to +3.1) |
| 3. Notes | −1.2 (−4.1 to +1.8) | 2 vs 4 | 0.69 | −2.3 (−6.1 to +0.8) |
| 4. Facts only | −57.3 (−64.3 to −49.7) | 0 vs 98 | below 0.001 | −74.8 (−81.7 to −67.2) |
| 4b. Facts only, every item | −14.0 (−20.5 to −8.2) | 4 vs 28 | below 0.001 | −16.0 (−23.7 to −9.2) |

Model tokens at ingest, from the ledger (calls that failed before returning usage are not in it). The month has 471 items, of which 42 are long enough for summaries and 81 are agent sessions. The last column scales a step's total to 1,000 items at the month's mix, assuming one pass per item.

| Step | Model | Calls | Input | Output | Total | Per 1,000 items |
|---|---|---|---|---|---|---|
| 1. Summaries and atoms | haiku | 42 | 144,334 | 37,367 | 181,701 | 385,777 |
| 2. Summaries and atoms | sonnet | 42 | 144,334 | 35,977 | 180,311 | 382,826 |
| 3. Notes on 81 sessions | sonnet | 81 | 172,193 | 47,591 | 219,784 | 466,633 |
| 4b. Notes on 390 other items | sonnet | 390 | 233,241 | 73,432 | 306,673 | 651,110 |

Arm 3 costs arm 1 plus step 3 (401,485 tokens, 852,410 per 1,000 items), and arm 4b adds step 4b on top (708,158, 1,503,520). Per unit: one summary pass over a long item is about 4,300 tokens, a session's notes about 2,700 and another item's notes about 790. Arms 1 and 2 read the same prompts, so the same input tokens; sonnet changes the price per token, not the count. A stronger model run less often (a daily batch instead of each ingest) saves the repeat passes kb makes over items that keep growing, and the saving cannot be read from this month, whose items are each ingested once. Per 1,000 items, the table is the cost of one pass and so the floor.

**What it shows.**
- **Sonnet extraction** changed nothing measurable: 160 and 160, 3 questions each way. Sonnet wrote fewer segments (105 against 121) and more atoms (308 against 278).
- **Notes** did not help: 158 against 160, 2 questions against 4, with a −4.1 to +1.8 interval. The agent's reads, not extra searchable rows, already recover what the notes hold, and the notes cost 220,000 sonnet tokens.
- **Facts alone** lose most of the month: 62 handled right. Only 37 of the 131 answerable questions have any evidence item with a fact row, and the arm got 22 of those 37 and 2 of the other 94. So arm 4 mostly measures coverage. With facts for every item (4b), extraction alone gets 136, 14.0 points (8.2 to 20.5) below the shipped arm, with 28 questions only the shipped arm got right. Extraction alone answers four of five questions here; the raw text carries the rest.

**Winner.** The shipped arm. No variant beat it: sonnet +0.0 points (−2.9 to +2.9), notes −1.2 (−4.1 to +1.8), and the two facts-only arms lose beyond the spread. Sonnet extraction and notes are not worth their extra tokens on this evidence, which has the resolution of one 171-question pass.

## Method, calls and limits

- **Harness.** `bench/owner.py` runs the arms through bench/run.py's own answer loop and judge, with `run_one` replaced to add seconds, the tool calls' model tokens and whether a tool showed records. `bench/owner_store.py` builds the stores and writes the notes. `bench/tools/owner.py` holds the arms. `kb/mcp.py` is the server (`kb mcp`, shipped as `superhistory mcp`), with `tests/test_mcp.py` and `bench/test_owner.py` as tests. These runs used an earlier draft of that server; its search and get descriptions are word for word the ones in the server now, but its argument limits and file locking differ, and the arms were not rerun against the final server. Both parts ran one question at a time with at most two model calls at once.
- **Model calls.** 8,658 calls that returned usage: 13.5 million input tokens and 1.19 million output, by model: haiku 3,038 calls (3.36 million in, 0.53 million out), sonnet 4,069 (9.67 million in, 0.58 million out), gpt-6.1-sol 1,551 (0.49 million in, 0.07 million out). Of these, 57 calls (about 0.10 million tokens) were the 3-question pilot, whose rows are not in the results and whose ledger lines are marked `pilot:`; the pilot also built the month store that Part A used. The ledger lists 4,713 more `count_tokens` calls, which count tokens and generate nothing, and the smoke test's 51 turns through `claude -p` (the trial included) are not in it. Calls that failed or were cut off before returning usage cannot be counted.
- **Rate limits.** None. No run met an HTTP 429 or a cooling-down reply, so the 30-minute wait never ran and every run used two slots.
- **One run each.** No arm ran twice, so the only measured spread is the committed noise runs and the two same-code arm-1 runs. A gap under about five questions is not a finding.
- **The month is ours.** Its questions were written by the same team as the check, and month A is the dev month the check was tuned on.
- **Lookup timeouts.** Two questions had a check lookup past its 10 s budget (`har-07` in arm 4 of Part A and `har-21` in Part B's notes arm); they were kept as rows, since the bench's earlier re-ask rule was not part of the brief.
- **Strict handled right** is this task's addition. The shipped definition cannot tell a pull arm that stayed quiet from one that searched and read.
- **Latency** was measured at one question at a time on a shared gateway, so it moves with the gateway's load and the other jobs on the plans.
- **Not run.** The LongMemEval S and LoCoMo dev halves, and their slices. Both parts would have needed another four-arm pass of about two to three hours each on plans another job shared, for a question about a month of agent work that month A already answers. Their numbers would add conversation-shaped evidence on pull against push, which this task does not have.
- **Not verified.** The full `tests/` suite did not run to the end (the cli and mcp suites, `bench/test_gateway.py`, `bench/test_compare.py` and `bench/test_owner.py` passed). The notes arm's prompt was not tuned, and neither was any prompt here.
- **No gateway address or key** is in any file here; `bench/owner.py` does not write one, and the rows were searched for the address and key before the commit.
