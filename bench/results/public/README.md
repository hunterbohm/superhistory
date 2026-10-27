# The check on LongMemEval and LoCoMo

Each question goes to the check as a new message, the way the product's hook sends one. The check adds what it finds ahead of the question. The answer model then answers, and it can run `kb search` and `kb get` for up to 4 turns. Each test's own scoring grades the answer. `bench/public/DEVIATIONS.md` lists every difference from the published setups, and `table.md` in each folder holds the full tables with the rivals' published numbers.

## Commands

```
python3 bench/public/longmemeval.py --set oracle
python3 bench/public/longmemeval.py --set s
python3 bench/public/longmemeval.py --set s --sample 50 --seed 0
uv run bench/public/locomo.py
```

The check runs from this repo's package; each run records the code's hash and commit in its `.run.json`. The rows in this folder ran from a copy of kb's code, before the package landed. A run where any question failed exits 1. `--count` prints the model calls a run would make and makes none. `--table` prints the table from the rows already written. A rerun runs only questions with no row yet, unless `--fresh`.

## Results

Answer model `claude-sonnet-5-5`, judge `gpt-6.1-sol`. Each question ran once.

| Test | Questions | Score |
|---|---|---|
| LongMemEval S, without summaries | 500 | 92.6% (463), task-averaged 93.6% |
| LongMemEval S, random sample with summaries | 50 | 84.0% (42). The same 50 without summaries also scored 42 |
| LongMemEval oracle, with summaries | 500 | 94.8% (474), task-averaged 96.0% |
| LoCoMo, categories 1 to 4, with summaries | 1,540 | J 90.4%, token F1 65.8 |

LongMemEval by question type:

| Set | single-session user | single-session assistant | single-session preference | multi-session | knowledge update | temporal | abstention |
|---|---|---|---|---|---|---|---|
| S | 100.0% | 100.0% | 90.0% | 85.0% | 93.6% | 93.2% | 83.3% |
| oracle | 100.0% | 100.0% | 96.7% | 89.5% | 94.9% | 94.7% | 93.3% |

LoCoMo by category:

| Score | single-hop (841) | multi-hop (282) | temporal (321) | open-domain (96) |
|---|---|---|---|---|
| J | 93.1% | 91.8% | 87.5% | 71.9% |
| F1 | 72.0 | 54.5 | 67.1 | 39.4 |

## Next to the rivals

No rival's number here comes from the same answer model and judge, so the gaps below compare setups, not memory layers alone.

- LongMemEval S. Mem0 reports 94.4% with gpt-5 answering and judging, and Zep reports 90.2% with gpt-5.4. Supermemory reports 85.2% with gemini-3-pro, and a rival vendor's rerun of Supermemory got 71.3%. All of these are vendor numbers. The benchmark's authors measured full context with gpt-4o at 60.6%, on the release before the cleanup.
- LoCoMo J. Mem0's 92.5% uses a newer judge prompt that passes one correct item out of several and dates within 14 days. Zep reports 94.7%. Under the 2025 judge prompt this run uses, Mem0's paper reports 68.44% for Mem0's graph variant and 72.90% for full context, with gpt-4o-mini. Letta's filesystem agent, an agent with search tools like this one, reports 74.0% with gpt-4o-mini.

## Did the work happen

| Run | Stores | Summaries written | Summary failures | Check searched | Check made no search | Lookup errors | Lookup added records | Empty answers | Evidence in lookup | Evidence in lookup or tools |
|---|---|---|---|---|---|---|---|---|---|---|
| LongMemEval S | 500 | 0 | 0 | 488 | 11 | 1 | 326 | 0 | 63.8% | 100.0% |
| LongMemEval S sample | 50 | 2,045 | 1 | 47 | 3 | 0 | 40 | 0 | 78.0% | 100.0% |
| LongMemEval oracle | 500 | 925 | 0 | 489 | 11 | 0 | 382 | 0 | 76.4% | 99.8% |
| LoCoMo | 10 | 55 | 0 | 1,502 | 38 | 0 | 1,206 | 0 | 54.2% | 96.5% |

"Check made no search" counts questions with fewer than 3 search words, which the check skips as shipped. Every LongMemEval judge reply was "yes" or "no". Every LoCoMo judge reply gave a CORRECT or WRONG label, and no question failed.

The first runs got an empty final answer on 55 questions: 51 in LoCoMo, 3 in S and 1 in oracle. The answer model had ended its turn after a thinking block with no text, and asking again unchanged got the same reply. The loop now asks again with one added line, "Reply with your answer now." Those 55 rows were rerun, and 39 of them needed the added line. None of the 55 had been judged correct.

## Volume and time

The four sets ran partly at the same time on one gateway, with 8 to 10 workers each. Lookup times were measured under that load.

| Run | Summary calls | Check calls | kb tool calls by the answer model | Judge calls | Wall time |
|---|---|---|---|---|---|
| LongMemEval S | 0 (20,554 needed, over the 3,000 limit) | at most 976 | 1,625 | 505 | 13 min, plus 1 min of reruns |
| LongMemEval S sample | 2,046 | at most 94 | 169 | 50 | 7.5 min |
| LongMemEval oracle | 925 | at most 978 | 1,587 | 501 | 14 min, plus 1 min of reruns |
| LoCoMo | 55, and 55 again for the reruns | at most 3,004 | 4,139 | 1,591 or more | 39 min, plus 3 min of reruns |

Each question made 1 to 5 answer-model calls. Rows record tool calls, not turns. Check and tool counts cover the rows kept, not the 55 empty-answer rows that were replaced. A LoCoMo judge call that gave no label was asked up to three times, so its judge count is a floor.

## What limits each number

- **LongMemEval S, 92.6%.** For every question, an evidence session reached the answer model, through the lookup or a tool result. Retrieval is not the limit. The lookup alone surfaced evidence for 63.8% of questions, and the agent's own searches found the rest. Of the 37 wrong answers, 19 are multi-session questions, mostly counts where the model found some of the items and missed others. Another 8 are temporal date arithmetic, 5 are abstention questions it answered anyway, 3 are preference questions and 2 are knowledge updates.
- **Summaries on S.** On the 50-question sample, summaries raised evidence in the lookup from 28 to 39 questions, but accuracy stayed at 42. The tools recover what the lookup misses. The sample's 84% against the full set's 92.6% comes from the sample, not from summaries.
- **LongMemEval oracle, 94.8%.** Of the 26 wrong answers, evidence reached the model in all but 1. 13 are multi-session counts, 7 are temporal, and 3 are knowledge updates where the model kept an older value. I read all 26 and disagree with the judge on 1, a list of three weddings that matches the gold.
- **LoCoMo J, 90.4%.** In 25 of 148 wrong answers the evidence session never reached the model. In 24 of those 25, the lookup had added records from other sessions, and in 19 the model answered without a search of its own. In the other 123, the evidence reached the model and the answer was still wrong. Open-domain is the weakest category at 71.9%, because its questions ask for inferences ("What might John's financial status be?") against short, subjective gold answers.
- **LoCoMo F1, 65.8.** F1 counts word overlap with the gold answer, so a right answer in other words scores low. 347 answers that the judge passed score under 0.5 F1. Answers average 5.9 words.
- **The judge.** In 10 random LongMemEval rows (seed 10), I agree with 9 verdicts. The judge passed one answer that opens with the wrong device and then corrects itself. In 10 random LoCoMo rows (seed 11), I agree with all 10, though two pass only because the J prompt grades generously.

## Not verified

- Each question ran once, so there is no run-to-run spread. A gap of a point or two between runs, or against a rival, may be noise.
- No one has measured how often `gpt-6.1-sol` agrees with GPT-4o or with people on these prompts. LongMemEval's over-97% human agreement is for its GPT-4o judge.
- None of the rivals' numbers was reproduced here.
- LoCoMo's category names follow LoCoMo's code and Mem0's mapping. Some published pages use other names for the same numbers.
- The check ran from a copy of kb's code with the product's prompt wording, not from the product package itself.
