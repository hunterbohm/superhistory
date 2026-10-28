# What each tool picks up by itself

The month benchmark hands every tool all 471 items. In real use a tool only knows what its own plugins and connectors collect. This estimate scores each tool on the month's 131 questions with an answer, counting a question only when the tool would have collected every record the answer needs.

It is an estimate built from each tool's documentation, not a live install test. `capture.json` lists, for each setup, which of the month's eight sources it collects and which it skips, each with the vendor page and the date it was read. The verdicts are the month run's, from `bench/results/month/<tool>.jsonl`.

```bash
python3 bench/pickup/score.py      # writes results/rows.jsonl and results/table.md
```

## Results

| Setup | Per month | Answer within reach, of 131 | Right from what it collects | Right when handed everything |
|---|---|---|---|---|
| The check | $0 | 131 | 122 | 122 |
| Supermemory, self-hosted run, with the sources its Max plan lists, notes kept in Notion (hosted plan not tested) | $100 | 88 | 79 | 121 |
| Supermemory, self-hosted run, with the sources its free plan lists (hosted plan not tested) | $0 | 13 | 11 | 121 |
| Mem0, self-hosted run, with the sources its free plan's agent plugins list (hosted plan not tested) | $0 | 13 | 10 | 102 |
| claude-mem, self-hosted run, with the sources its README lists (its plugins not tested live) | $0 | 13 | 8 | 19 |

By their published lists, the three rivals' plugins all collect Claude Code, Codex and Hermes sessions. Supermemory's Max plan lists Gmail, and its Notion connector covers notes if they are kept in Notion. No rival lists texts, commits, or GitHub issues and pull requests. None of this was tested on the hosted products.

## How a count is derived

Each line of `results/rows.jsonl` is one setup and one question:

- `needs` is the set of sources of the items in the question's gold evidence, from `demo/month/`.
- `within_reach` is true when every source in `needs` is under the setup's `reads` in `capture.json`.
- `correct` is copied from the row with the same `id` in the file named by `verdict_from`.

"Right from what it collects" counts the rows where both are true. For example, self-hosted Supermemory with the sources its Max plan lists reads `claude_code`, `codex`, `hermes`, `gmail` and `notes`. Of the 131 questions, 88 need only those sources, and its month rows grade 79 of those 88 correct. To recount without the script:

```bash
python3 - <<'EOF'
import json
rows = [json.loads(line) for line in open("bench/pickup/results/rows.jsonl")]
for s in dict.fromkeys(r["setup"] for r in rows):
    mine = [r for r in rows if r["setup"] == s]
    print(s, sum(r["within_reach"] for r in mine), sum(r["within_reach"] and r["correct"] for r in mine))
EOF
```

`results/table.md` also counts how many questions need each source, so a reader who weighs the sources differently can redo the sums.

## What limits this estimate

- No rival was installed with its plugins or connectors. Their verdicts come from the month run, where each was loaded through its API as described in `bench/README.md`. Mem0's Claude Code and Codex plugins need its cloud; the verdicts are from open-source Mem0.
- A question counts as out of reach when any one record it needs is uncollected, even if a partial answer might pass.
- A tool that holds fewer items may answer the questions within its reach differently. That is not measured.
- The Max-plan row assumes the notes live in Notion, which favours Supermemory. Supermemory's pricing page does not say which plan includes Notion. Outside summaries put it on the $19 Pro plan, which has no Gmail.
- The 40 questions with nothing to find are left out. A tool that collects less has less to add to them, and this estimate cannot model that.
- The month's mix of sources was chosen by the team that built the check.
- The verdicts follow the month's rows, so `score.py` runs again whenever they change. After the Hillclimb track's gold audits graded the month again on 2026-10-27 (`bench/results/regrades.tsv`), har-03 flipped for both rivals. Self-hosted Supermemory went from 80 to 79 with its Max plan's sources and from 122 to 121 when handed everything, and self-hosted Mem0 from 103 to 102 when handed everything.
