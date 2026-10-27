# Two outside tests: speaking up, and staying quiet

Two tests written by other labs, both built on LoCoMo's ten long conversations, measure what the check is for: putting the right memory in front of an agent when the user didn't ask for it, and adding nothing when memory would make the reply worse. Neither was written by the team that built the check, and both published scores for Mem0.

- **LoCoMo-Conv, implicit style** (Chang and Chen, National Taiwan University; Findings of EMNLP 2026; https://arxiv.org/abs/2609.03467). LoCoMo's questions, rewritten so the user never asks one. "I'm trying to organize my health and wellness journal from last year" should bring up the date of the support group. A judge grades whether the reply conveys the fact: 1, 0.5 or 0. 1,540 messages; LoCoMo's adversarial category is left out, as the paper's judge leaves it out.
- **OP-Bench** (Hu et al., Harbin Institute of Technology, 2026; https://arxiv.org/abs/2601.13722). 1,700 messages from the 20 people in the conversations, where memory should stay out of the reply: questions with nothing to do with the user's past (318), questions that bait a personal answer (100), false facts, values and memories the reply should not flatter (400), and 882 varied messages whose replies should not all sound alike. Higher is better on every column.

The paper behind OP-Bench found that every memory system it tried made replies worse than no memory at all, and that no fix it tried helped without losing points on LoCoMo.

```bash
python3 bench/outside/run.py                  # both tests, every tool, all ten conversations
python3 bench/outside/run.py --groups conv-26 --limit 5 --tools none,check
python3 bench/outside/run.py --table          # the tables from the rows already written
```

Run it with the Python that has the rivals' packages, and with a Supermemory server, as for the month (`bench/README.md`). `SUPERMEMORY_URL` and `SUPERMEMORY_KEY_FILE` point the adapter at it. Rows go to `results/<test>/<tool>.jsonl`, one per message; a rerun runs only messages with no row yet. `results/runs.json` records each run's models, data hashes, store stats and failures.

## Licenses

Neither test's repository has a license. LoCoMo-Conv's repository has no license file. OP-Bench's has a NOTICE saying its owner has yet to choose one. So `run.py` downloads their data, prompts and judge code at pinned commits on first use, checks each file's hash, and reads the prompts out of their code without running it. None of it is copied into this repository, and the result rows hold message ids, not the messages. LoCoMo's conversations are under CC BY-NC 4.0, the same as for `bench/public/`.

| File | From | Commit |
|---|---|---|
| The implicit jobs, the answer prompts, the fact-used judge | https://github.com/MiuLab/LoCoMo-Conv | 1925e92 |
| The OP-Bench messages, the answer and judge prompts | https://github.com/yulinlp/OP-Bench | 44e7d0e |
| The ten conversations | https://github.com/snap-research/locomo | 3eb6f2c |

## How a message runs

1. Each tool loads the conversation's 19 to 32 sessions into a fresh store, one item per session with its date, the same items as `bench/public/locomo.py`. The rivals load them the way they load the month: Mem0 adds each session, and Supermemory posts each one and waits until its memories are extracted.
2. The message goes through the tool's lookup as a new message.
3. The answer model (`claude-sonnet-5-5`) replies from the test's own prompt, with the lookup's text where the prompt puts memory, and with the tool's agent tools: `kb search` and `kb get` for the check, each rival's search. It gets at most 4 tool turns, as in `bench/public/`.
4. The test's own judge prompt grades the reply, run on `gpt-6.1-sol`. OP-Bench's repetition score is 1 minus the mean cosine similarity between every pair of one person's 44 or so replies.

The tools are no memory, the gold sessions (for the implicit test, as a ceiling), the check, Mem0 and Supermemory, each set up as in `bench/README.md`. claude-mem is left out: it records coding-agent sessions through its hooks, and these are chats between two friends.

## What differs from each paper

| | LoCoMo-Conv | OP-Bench | Here |
|---|---|---|---|
| Answer model | gemma-4-31B-it | gpt-4o-mini | `claude-sonnet-5-5` |
| Judge | gpt-5.4-mini | gpt-4o-mini | `gpt-6.1-sol` |
| Memory per message | top 10 retrieved items | top 5 | what each tool's own lookup returns |
| Agent tools | none | none | each tool's own, up to 4 turns |
| Repetition embedder | | text-embedding-3-small | `BAAI/bge-base-en-v1.5`, local |
| A judge call that fails | | scores 0 | retried 3 times, then the message has no row and the run reports it |
| Gold ceiling | the gold turns | | the gold sessions, whole |

OP-Bench's code uses its plain prompt, with no memory section, when a lookup returns nothing, and its memory prompt otherwise. Here that rule applies to every tool, so a lookup that adds nothing sends the plain prompt. LoCoMo-Conv's code always uses its memory prompt, with "(no memories retrieved)" when a lookup returns nothing, and that is kept too.

OP-Bench's paper reports six columns and an average, AVG.(OP), and does not say how the average is computed. Its released code averages over each person's four tasks. That does not reproduce the paper's averages from the paper's own columns, so the tables compare the six columns and give the code's average beside them.
