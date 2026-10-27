# Two outside tests: speaking up, and staying quiet

Two tests written by other labs, both built on LoCoMo's ten long conversations, measure what the check is for: putting the right memory in front of an agent when the user didn't ask for it, and adding nothing when memory would make the reply worse. Neither was written by the team that built the check, and both published scores for Mem0.

- **LoCoMo-Conv, implicit style** (Chang and Chen, National Taiwan University; Findings of EMNLP 2026; https://arxiv.org/abs/2609.03467). LoCoMo's questions, rewritten so the user never asks one. "I'm trying to organize my health and wellness journal from last year" should bring up the date of the support group. A judge grades whether the reply conveys the fact: 1, 0.5 or 0. 1,540 messages; LoCoMo's adversarial category is left out, as the paper's judge leaves it out.
- **OP-Bench** (Hu et al., Harbin Institute of Technology, 2026; https://arxiv.org/abs/2601.13722). 1,700 messages from the 20 people in the conversations, where memory should stay out of the reply: questions with nothing to do with the user's past (318), questions that bait a personal answer (100), false facts, values and memories the reply should not flatter (400), and 882 varied messages whose replies should not all sound alike. Higher is better on every column.

The paper behind OP-Bench found that every memory system it tried made replies worse than no memory at all, and that no fix it tried helped without losing points on LoCoMo.

```bash
python3 bench/outside/outside.py --sample 0.2 --seed 0    # the seeded 20% sample behind the numbers below
python3 bench/outside/outside.py --sample 0.2 --table     # its tables, from the rows already written
python3 bench/outside/outside.py                          # every message, every tool, all ten conversations
python3 bench/outside/outside.py --groups conv-26 --limit 5 --tools none,check
```

Run it with the Python that has the rivals' packages, and with a Supermemory server, as for the month (`bench/README.md`). `SUPERMEMORY_URL` and `SUPERMEMORY_KEY_FILE` point the adapter at it. Rows go to `results/<test>/<tool>.jsonl`, one per message; a rerun runs only messages with no row yet. `results/runs.json` records each run's models, data hashes, store stats, rate limits and failures.

A run makes at most two model calls at once, the limit for a benchmark run on the shared model plans. Answers, judges, the check's own calls and its summaries all wait for one of two slots, and the check's hedge, which sends a second copy of a call still running after 3 seconds, sends it only when a slot is free. Mem0's calls go through the same slots, with the OpenAI SDK's own retries off; Supermemory's server makes its own, as below. After an HTTP 429 or "cooling down" from a model, no new call goes to it for 30 minutes, checked again whenever a call gets a slot, then it is tried once more; a second limit stops the run. A message whose lookup fails, times out or meets a limit gets no row and runs again, so no reply is graded without the lookup it should have had.

## Licenses

Neither test's repository has a license. LoCoMo-Conv's repository has no license file. OP-Bench's has a NOTICE saying its owner has yet to choose one. So `outside.py` downloads their data, prompts and judge code at pinned commits on first use, checks each file's hash, and reads the prompts out of their code without running it. None of it is copied into this repository. The result rows hold message ids, not the messages, though the replies and the check's search queries are written from them. LoCoMo's conversations are under CC BY-NC 4.0, the same as for `bench/public/`.

| File | From | Commit |
|---|---|---|
| The implicit jobs, the answer prompts, the fact-used judge | https://github.com/MiuLab/LoCoMo-Conv | 1925e92 |
| The OP-Bench messages, the answer and judge prompts | https://github.com/yulinlp/OP-Bench | 44e7d0e |
| The ten conversations | https://github.com/snap-research/locomo | 3eb6f2c |

## How a message runs

1. Each tool loads the conversation's 19 to 32 sessions into a store. The check and Supermemory take one item per session with its date, the same items as `bench/public/locomo.py`, and Supermemory waits until each one's memories are extracted. Mem0 is loaded the way Mem0's own LoCoMo evaluation loads it (`mem0_locomo.py`): one user id per speaker, two messages per add, each session added once from each speaker's side, the session's date as metadata, and both speakers searched for 10 memories each. Loaded one session per add, as for the month, Mem0's extraction replies failed to parse on 11 of the first conversation's 19 sessions before that load was stopped, and a session whose reply fails stores nothing. Its own method had no such failures on any of the ten conversations (`extraction_parse_errors` in each store's stats). Mem0's and Supermemory's stores are prepared with `--load-only`, one conversation at a time with nothing else running, and kept: Mem0's take 11 to 23 minutes each, and a Mem0 build that meets a limit is not kept. A benchmark run only opens them, and stops if one is not ready, including a Supermemory store with any session whose memories failed to extract.
2. The message goes through the tool's lookup as a new message.
3. The answer model (`claude-sonnet-5-5`) replies from the test's own prompt, with the lookup's text where the prompt puts memory, and with the tool's agent tools: `kb search` and `kb get` for the check, each rival's search. It gets at most 4 tool turns, as in `bench/public/`.
4. The test's own judge prompt grades the reply, run on `gpt-6.1-sol`. OP-Bench's repetition score is 1 minus the mean cosine similarity between every pair of one person's 44 or so replies.

The tools are no memory, the gold sessions (for the implicit test, as a ceiling), the check and Supermemory as set up in `bench/README.md`, and Mem0 as in step 1. claude-mem is left out: it records coding-agent sessions through its hooks, and these are chats between two friends.

## What differs from each paper

| | LoCoMo-Conv | OP-Bench | Here |
|---|---|---|---|
| Answer model | gemma-4-31B-it | gpt-4o-mini | `claude-sonnet-5-5` |
| Judge | gpt-5.4-mini | gpt-4o-mini | `gpt-6.1-sol` |
| Memory per message | top 10 retrieved items | top 5 | what each tool's own lookup returns |
| Agent tools | none | none | each tool's own, up to 4 turns |
| Answer temperature | 0 | 0 | the model's default, as in `bench/public/` |
| Answer length cap | 300 tokens | 512 tokens | 2,000 tokens, since a reply may call tools first |
| Repetition embedder | | text-embedding-3-small | `BAAI/bge-base-en-v1.5`, local |
| A judge call that fails | | scores 0 | asked again up to 3 times when the reply has no score; a failed call leaves the message with no row, and the run reports it |
| Messages | all | all | a seeded 20% sample for now; all once the check is final |
| Gold ceiling | the gold turns | | the gold sessions, whole |

OP-Bench's code uses its plain prompt, with no memory section, when a lookup returns nothing, and its memory prompt otherwise. Here that rule applies to every tool, so a lookup that adds nothing sends the plain prompt. LoCoMo-Conv's code always uses its memory prompt, with "(no memories retrieved)" when a lookup returns nothing, and that is kept too.

OP-Bench's paper reports six columns and an average, AVG.(OP), and does not say how the average is computed. Its released code averages over each person's four tasks. That does not reproduce the paper's averages from the paper's own columns, so the tables compare the six columns and give the code's average beside them.

The local embedder places unrelated replies closer together than OpenAI's small embedding model does, so repetition scores here run lower for every tool, no memory included: about 35 against the paper's 72. Compare tools within the table only.

## The sample

The numbers below come from a seeded stratified sample, `--sample 0.2 --seed 0`, recorded in `results/sample.json` with each stratum's population and the ids drawn. A stratum is one of each test's own categories: LoCoMo's four question categories for the implicit test (307 messages), and for OP-Bench the off-topic, baiting, and fact, value and memory sycophancy messages. Each person's repetition messages form one stratum, because that score is computed over one person's replies (340 OP-Bench messages, 7 to 11 per person). Every tool answers the same sampled messages.

The fact-used mean is estimated stratum by stratum and weighted by each stratum's share of the 1,540 messages. Each interval is the middle 95% of 2,000 bootstrap draws that resample each stratum's messages with replacement. The same resampled messages score every tool, so the paired differences compare tools message by message.

## Results on the sample

These are a baseline. The check will change before release, and these tests will be run again in full with the final check before any of these numbers is published. The Hillclimb track, which changes the check, does not tune on these tests.

All rows behind these numbers are in `results/sample/`, one per tool and message, and `python3 bench/outside/outside.py --sample 0.2 --table` rebuilds the tables in `results/sample-table.md` from them alone.

**Speaking up: LoCoMo-Conv's implicit messages.** Fact used, 0 to 1, over 307 sampled messages.

| Tool | Fact used [95% CI] | single-hop (168) | multi-hop (56) | temporal (64) | open-domain (19) | Lookup added something |
|---|---|---|---|---|---|---|
| No memory | 0.171 [0.135, 0.209] | 0.220 | 0.196 | 0.000 | 0.237 | 0% |
| Gold sessions | 0.891 [0.863, 0.918] | 0.973 | 0.866 | 0.781 | 0.605 | 100% |
| The check | 0.650 [0.601, 0.699] | 0.696 | 0.643 | 0.609 | 0.395 | 60% |
| Mem0 | 0.511 [0.467, 0.553] | 0.696 | 0.518 | 0.094 | 0.263 | 100% |
| Supermemory | 0.685 [0.641, 0.729] | 0.753 | 0.634 | 0.625 | 0.447 | 100% |

The check minus Supermemory is -0.036 [-0.083, 0.016], no measurable difference. The check minus Mem0 is 0.139 [0.090, 0.191].

**Staying quiet: OP-Bench.** Higher is better, x100, over 340 sampled messages.

| Tool | Off-topic (64) | Baiting (20) | Repetition (176) | False fact (20) | False value (20) | False memory (40) | Overall | Lookup added nothing |
|---|---|---|---|---|---|---|---|---|
| No memory | 99.9 [99.6, 100.0] | 99.5 [98.8, 100.0] | 36.5 [35.4, 37.5] | 97.7 [96.6, 98.8] | 97.0 [96.3, 97.7] | 86.0 [77.0, 94.3] | 81.9 [80.7, 83.0] | 100% |
| The check | 97.6 [95.6, 99.2] | 71.0 [58.5, 84.2] | 36.3 [35.1, 37.3] | 96.5 [94.9, 98.0] | 97.3 [96.7, 97.9] | 42.6 [32.0, 53.5] | 68.7 [65.1, 72.2] | 76% |
| Mem0 | 68.3 [62.0, 74.2] | 49.2 [34.5, 64.7] | 31.0 [29.8, 32.2] | 92.4 [90.5, 94.4] | 95.2 [93.8, 96.4] | 14.3 [9.9, 19.6] | 50.6 [46.6, 54.9] | 0% |
| Supermemory | 57.2 [52.0, 62.3] | 24.2 [21.5, 27.0] | 29.5 [28.3, 30.5] | 92.6 [90.5, 94.8] | 94.3 [92.5, 95.6] | 12.2 [9.0, 15.5] | 40.9 [39.4, 42.5] | 0% |

Each column is a mean of per-person means, as OP-Bench's code computes it: a sampled message stands for its person's share of the task, since people have 9 to 21 off-topic messages. The check minus Supermemory is 27.7 [24.2, 31.3] overall, and the check minus Mem0 18.0 [13.4, 22.6]. The check minus no memory is -13.2 [-16.9, -9.5]: -43.4 [-55.5, -30.6] on false memories and -28.5 [-41.2, -15.3] on baiting. The paired differences for every column are in `results/sample-table.md`.

For comparison, OP-Bench's paper reports Mem0 at 46.32 overall and no memory at 83.10, with gpt-4o-mini answering and judging; LoCoMo-Conv's reports mem0 at 0.330 on implicit messages and its oracle at 0.724, with gemma-4-31B-it answering. Both are in `published.json` with their sources. The answer model, judge and setup differ from these runs, so they are context, not a ranking.

## What limits these numbers

- **The sample.** 20% of each stratum, one run per message. The intervals cover resampling the messages, not run-to-run variation in the models. Small strata give wide intervals: 19 open-domain messages, and 20 each for baiting, false facts and false values.
- **The check against Supermemory on implicit messages.** The check's own lookup brought a gold session on 97 of 307 messages, against 206 for Supermemory's and 244 for Mem0's. The check's agent then searched with `kb search` and `kb get` on all 307 and reached one on 261. Supermemory's agent, which sees about 12,000 characters before every message, searched on 61 and reached one on 229. They end up level.
- **Where the check loses points on OP-Bench.** On false memories, such as "remember when I told you...", the check added cards on 19 of 40, and those replies went along with the false memory, scoring 15.5. When it added nothing they scored 67.1. On baiting messages it added cards on 6 of 20, scoring 37.5 against 85.4 without. Even without cards, the agent searched with `kb search` on all 40 false-memory messages.
- **Mem0 on temporal questions.** Mem0's open-source extraction writes the day a memory is made into it, as in "as of October 27, 2026": that date is in 4,475 of the 6,140 memories its lookups returned for sampled implicit messages. That is right in live use, but these chats are from 2022 to 2024, so replies mix the load date with the session dates. That costs Mem0 on temporal questions: 0.094, against 0.696 on single-hop. It is a limit of replaying history, not necessarily of Mem0 in use.
- **Repetition.** As above, the local embedder puts every tool's repetition score lower than the paper's. Compare tools within the table only.
- **Supermemory's own model calls.** Supermemory's server calls its model while it takes in a conversation, and those calls cannot go through a run's slots; how it handles a rate limit is up to it. So it takes in conversations only during `--load-only`, with `SUPERMEMORY_INGEST_CONCURRENCY=2`. The ten conversations behind these numbers were taken in before the two-call limit was set, most of them 8 at a time, and every one of their 272 sessions finished extracting; none failed.
- **Rivals self-hosted.** Mem0 and Supermemory ran their open-source servers on `claude-haiku-5-5`, as for the month. Both say their hosted platforms extract memories better.
- **Runs and failures.** The first three conversations' rows come from two earlier runs with 8 and 6 workers, stopped before they wrote a record when the shared limit of two was set; 5 of their messages met a rate limit and had no row; the one of them in the sample ran again in the sample run. The sample runs, recorded in `results/runs.json`, made at most two calls at once and met no rate limit. 11 messages lost their connection to the gateway and were run again, and 10 of the check's messages whose lookup timed out in that same stretch, so their replies had no cards, were run again too. No sampled message is missing a row. `results/mem0-stores.json` records each Mem0 store's build, with no extraction parse errors and no failed adds in any of the ten.
