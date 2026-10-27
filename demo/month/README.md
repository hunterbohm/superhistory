# The made-up month

A month of invented data for trying the knowledge base and for benchmarking it. Nothing here is about a real person.

Jules Marlow runs Marlow Works, a one-person shop in Denver that builds automations for small businesses. In
September 2026 Jules works with Claude Code and Codex, talks to Hermes on the phone, and emails and texts four
clients: Fernbrook Bakery, Copperline Freight, Harbor Lane Dental and Quill & Ledger. Facts that an agent needs
are spread across sources. Claude finds a bug that Codex fixes a week later, a client changes a business rule by
text, and a phone call lands in a Hermes chat that the next coding session ignores.

## Files

`items.jsonl` holds one item per line, in the shape the connectors produce, plus the source name:

```json
{"source": "imessage", "source_id": "...", "ts": "2026-09-11T15:31:12Z", "title": "...", "content": "...", "meta": {...}}
```

| Source | Items |
|---|---|
| `claude_code` | 34 |
| `codex` | 26 |
| `hermes` | 21 |
| `gmail` | 80 |
| `imessage` | 79 |
| `notes` | 47 |
| `git` | 159 |
| `github` | 25 |

`questions.jsonl` holds one question per line:

```json
{"id": "har-01", "question": "...", "answer": "...", "evidence": ["<source_id>", "..."], "kind": "update"}
```

`evidence` lists the `source_id` of each item that holds the answer. `kind` is one of these:

| Kind | Questions | Meaning |
|---|---|---|
| `single` | 105 | One item answers it. |
| `multi` | 10 | The answer needs two or more items. |
| `update` | 9 | A later item changed an earlier fact. The answer is the latest one. |
| `temporal` | 7 | The answer needs dates: when something happened, or how long between two events. |
| `none` | 40 | Nothing in the month fits. The right lookup adds nothing. |

Each question arrives as a new message to an agent, with no conversation before it. Ask them as of October 1,
2026.

## Rebuild the month

`bench/month/build.py` writes both files from the stories in `bench/month/stories/` and the background items in
`bench/month/noise/`. The same seed always writes the same bytes.

```bash
python3 bench/month/build.py           # write demo/month/*.jsonl
python3 bench/month/build.py --check   # rebuild in memory, validate, and fail if the files differ
```

The build fails when an evidence item lacks a phrase its answer depends on, when a `none` question's subject shows
up anywhere in the month, when two items share an id, or when a denied word appears. To also check against a list
of real names kept outside the repository, pass `--deny FILE`.

## Changes

- 2026-10-27, agreed with the coordinator. Two auditors who saw only the questions, gold answers and cited items
  checked all 171 questions. The first found nothing contradicted or ambiguous. The second looked for gold answers
  that leave out a qualification their own evidence states, and two gained one: har-01 (a reminder that would land on
  a Sunday goes out Saturday at 10:00) and cop-22 (Bluestem also needs `vertical_strategy: lines`). Every saved
  run's answers to them were graded again; `bench/results/regrades.tsv` holds before and after.
