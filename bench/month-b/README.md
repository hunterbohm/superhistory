# Month B, the held-out month

A second made-up September for the same persona as `demo/month`, used only as the test half of the month benchmark. Changes to the check are tuned on month A and never read month B. It runs at most three times, as `baseline`, `midpoint` and `final`; `bench/splits/heldout-runs.tsv` logs each run.

Month B keeps month A's persona and background items (`bench/month/noise/`). Its stories and questions are new: other clients, people, facts and questions, written for this month by writers who never saw the check's tuning (`bench/month/stories_b/`). The questions come in month A's counts and kinds:

| Kind | Questions |
|---|---|
| `single` | 105 |
| `multi` | 10 |
| `update` | 9 |
| `temporal` | 7 |
| `none` | 40 |

`items.jsonl` holds 480 items and `questions.jsonl` 171 questions, in the shapes `demo/month/README.md` describes. `SHA256SUMS` fixes both files. Month B is frozen: any change to it needs the coordinator's agreement, with the reason logged.


```bash
python3 bench/month/build.py --month b           # write bench/month-b/*.jsonl
python3 bench/month/build.py --month b --check   # rebuild in memory, validate, and fail if the files differ
```

## Changes

- 2026-10-27, agreed with the coordinator. A review found two gold answers that left out part of what their evidence says, so an auditor who saw only the questions, gold answers and cited items checked all 171. Six changed: three gold answers now name an exception the evidence states (b2-06, b2-10, b2-11), two questions now name which person or shop they mean (b5-14, b6-13), and one `none` question whose subject appeared in the month asks about something else (b7-11). The items did not change. The held-out baseline's answers to the five were graded again against the corrected gold, and b7-11's row was run again; `bench/results/regrades.tsv` holds before and after.
