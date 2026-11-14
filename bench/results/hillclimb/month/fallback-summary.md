# The check's fallback alone, on both halves of the made-up month

`check-fallback` runs the shipped lookup with the plan call refused, so each lookup is the fallback's: up to 8 of the
message's longest keywords, the search, and the picker. `check` is the same code with the plan call allowed; no `check`
lookup used the fallback on either half (its queries are all the model's). Each tool ran once per question.

Run on the mini against the CLI gateway, one question at a time, from a scratch copy of this branch's code (commit
1a1bcceb). Month A: `python3 bench/run.py month --month a --label fallback --tools none,check,check-fallback`
(`none` ran first in a run stopped by a bench bug fixed in 1a1bcceb; `check` and `check-fallback` ran again from the
start). Month B, with `BENCH_HELDOUT_LOG` on a new file and the scratch copy's old `b-baseline` rows removed:
`python3 bench/run.py month --month b --label baseline --tools none,check,check-fallback`, saved here as `b-fallback`.
The fallback's query was not tuned on month B. Month B is spent as a held-out half: this was its one look for the fallback.

| Half | Tool | Accuracy | Answerable | Nothing fits | Added nothing when nothing fits | Lookup p50 / p90 s |
|---|---|---|---|---|---|---|
| A (dev) | none | 41/171 (24.0%) | 1/131 | 40/40 | 40/40 | 0 / 0 |
| A (dev) | check | 162/171 (94.7%) | 122/131 (93.1%) | 40/40 | 36/40 (90.0%) | 3.96 / 4.88 |
| A (dev) | check-fallback | 160/171 (93.6%) | 120/131 (91.6%) | 40/40 | 38/40 (95.0%) | 2.52 / 3.29 |
| B (test) | none | 40/171 (23.4%) | 0/131 | 40/40 | 40/40 | 0 / 0 |
| B (test) | check | 163/171 (95.3%) | 123/131 (93.9%) | 40/40 | 39/40 (97.5%) | 4.28 / 5.10 |
| B (test) | check-fallback | 157/171 (91.8%) | 117/131 (89.3%) | 40/40 | 38/40 (95.0%) | 2.87 / 3.55 |

Questions only `check` got right: 2 on A, 7 on B. Only `check-fallback` right: 0 on A, 1 on B. The judge disagrees with
itself on up to 4 questions per tool (`bench/README.md`). No lookup errored in any run.
