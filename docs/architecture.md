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
  store that cut an item before masking it can split a secret in two that neither chunk shows, and so it writes
  an item's chunks all at once or not at all. It refuses a source or item id that holds a secret, as ingest does
  (`store.secret_id`: the host the rows' metadata names and the reader's id apart, any other id whole, and an id read
  more narrowly than text, so a note titled "Tokens: 2026 plan" is kept), and records on each row the version of `mask()` it used (`mask.VERSION`, which
  follows the patterns). A database trigger clears that version when anything else changes a row's time, title,
  text or metadata, as an older kb would.
- Out of kb: the kb command's stdout and stderr, every log line (through `masked_log`, whatever logger the caller
  passes, plus a filter on kb's own handlers), `check.log`, the remote reader's output, recorded failures, and the
  text of every error kb raises from outside text: AI answers, secrets, ssh, gh and git output, IMAP and Google
  answers.

- To the AI plug-in: every prompt, through the model `ai.load` returns and through `ai.hedged` and the summarizer
  for a model a caller made itself. The check's prompt (the user's message, earlier turns, the records) and the Gmail
  sorter's lines are masked, so the plug-in, which can be another provider than the user's agent, never sees a
  secret's value. kb's own instructions in those prompts contain nothing mask() changes.

Text is masked before it is cut, never after: a cut can keep part of a secret too short for mask() to know. A row of
today's version, and for a chunk every chunk of its item, was masked whole, so `kb search` cuts it as stored
(`store.current`). Any other row, from before versions or from an older `mask()`, is masked whole when read: search
masks the hit's whole item, its title and its metadata, finds the match again in the masked text with FTS5's own
matching, and leaves out an item whose id holds a secret. `kb get` and the summarizer always mask the whole item.

`mask()` masks a private key by its structure, never line by line: from its header through its footer, or, with no
footer, through the end of its paragraph (the next blank line, plain or escaped), an encrypted key's past the blank
line after its DEK-Info field. A footer with no header masks its paragraph up to it, and what an older kb's
row-by-row remask left after its placeholder (base64 glued to it, or an escaped break and a key line) masks through
its footer or the end of its paragraph. Text after a broken key in its paragraph is masked with it; that is the cost.
`mask()` repeats until a pass changes nothing, so masked text masks to itself. Each ingest masks older rows again for up to a minute
(`store.remask`), deletes items whose id holds a secret and logs each with its id masked, and records today's version,
so an interrupted run leaves the rest old. The file is rebuilt once none are left, and a rebuild cut short runs again
on the next ingest. The header counts the rows left, and `kb remask` does them all at once. An HTTP error body longer
than kb reads is left out of the error.

`tests/test_write_fuzz.py` generates key blocks of every header kind, line width, line break and cut, and runs a few
thousand seeded sequences of the store's writes: ingest, Sync-shaped full and
piecemeal writes, summary rows, an older kb's store_item and remask (`tests/legacy_store.py`), remask cut off at a
random item or in its rebuild, deletes and supersede marks. Canaries cross chunk and call boundaries. After each
sequence no reader may show a piece of a canary, and no row may read as current unless its whole item masks clean.

Records that other code sends through its own loggers to its own handlers are that code's to mask.
`tests/test_canary.py` plants a different secret in every kind of input, runs every command and every library entry
point, and checks that none reaches an output. A path it misses is a new case there.

## Config keys come from dataclasses

A source type's `Options` dataclass is the schema for its `[sources.NAME]` table, and an AI plug-in's `Options` is the
schema for `[ai]`. `config.options_from()` reads the fields, so adding a key means adding a field. An unknown key or a
wrong type is an error that names the key.
