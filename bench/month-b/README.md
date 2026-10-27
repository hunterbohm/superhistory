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
