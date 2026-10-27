# How the package is laid out

The Python package is `kb`, named after the command. The product name lives in one constant, `kb.NAME`, and the
folder names, job labels and printed text come from it, so a rename is one edit.

## Modules

| Module | What it owns |
| --- | --- |
| `kb/cli.py` | The `kb` command. It is the only module that builds `Paths` and reads the config file. |
| `kb/paths.py` | Every file and folder kb writes for itself, per platform. `KB_HOME` puts them all in one folder. |
| `kb/config.py` | The TOML config, parsed once into typed settings. On Python 3.9 and 3.10 it reads TOML with the copy of `tomli` in `kb/_vendor/`. |
| `kb/store.py` | The SQLite file: the `docs` table, its full-text index, watermarks and health. |
| `kb/mask.py` | Secret masking and the 2FA filter. Every stored row passes through `mask()`. |
| `kb/search.py` | Keyword search with rank fusion, term coverage and recency. |
| `kb/check.py` | The check: one model call writes a query, search finds candidates, a second call keeps the ones that fit. |
| `kb/summarize.py` | Segment and atom rows for long items. |
| `kb/ingest.py` | The runner that reads every source and stores what changed. |
| `kb/ai/` | AI plug-ins. Each one answers `ask(prompt, timeout)` with the model's text. |
| `kb/sources/` | Source types. Each module defines `TYPE`, and its name is the type's name. |
| `kb/sync/` | Reserved for the Sync client. |

## Where Sync plugs in

The Sync client goes in `kb/sync/`. It meets the engine at two points.

- `ingest.Reader` reads one source on one host. `LocalReader` reads this computer. A Sync reader can deliver another
  computer's events instead.
- `ingest.apply(db, source, host, event)` is the one write path for read data. It stores rows under
  `<host>:<source_id>`, so two computers never write the same row, and applying an event twice changes nothing.

## Where secrets are masked

`mask()` in `kb/mask.py` replaces secret-looking text. kb applies it at every boundary text crosses:

- Into the store: an item's title, text and metadata (only the secret-looking parts, so links and paths still
  work), and the source's id for the item. Summary rows are masked too. `store.replace_rows`, the one way rows
  enter, masks again, so rows another store sent are masked as well, and masks a long item's chunks as one text: a
  store that cut an item before masking it can split a secret in two that neither chunk shows. `kb remask` does
  the same for rows already stored.
- Out of kb: the kb command's stdout and stderr, every log line (through `masked_log`, whatever logger the caller
  passes, plus a filter on kb's own handlers), `check.log`, the remote reader's output, recorded failures, and the
  text of every error kb raises from outside text: AI answers, secrets, ssh, gh and git output, IMAP and Google
  answers.

- To the AI plug-in: every prompt, through the model `ai.load` returns and through `ai.hedged` and the summarizer
  for a model a caller made itself. The check's prompt (the user's message, earlier turns, the records) and the Gmail
  sorter's lines are masked, so the plug-in, which can be another provider than the user's agent, never sees a
  secret's value. kb's own instructions in those prompts contain nothing mask() changes.

Text is masked before it is cut, never after: a cut can keep part of a secret too short for mask() to know. Readers
of stored text mask it again before cutting it, since a store written before today's patterns may hold what they
find: `kb search` masks a chunk with its neighbors, and `kb get` and the summarizer mask the whole item. An HTTP
error body longer than kb reads is left out of the error.

Records that other code sends through its own loggers to its own handlers are that code's to mask.
`tests/test_canary.py` plants a different secret in every kind of input, runs every command and every library entry
point, and checks that none reaches an output. A path it misses is a new case there.

## Config keys come from dataclasses

A source type's `Options` dataclass is the schema for its `[sources.NAME]` table, and an AI plug-in's `Options` is the
schema for `[ai]`. `config.options_from()` reads the fields, so adding a key means adding a field. An unknown key or a
wrong type is an error that names the key.
