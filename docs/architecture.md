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

## Config keys come from dataclasses

A source type's `Options` dataclass is the schema for its `[sources.NAME]` table, and an AI plug-in's `Options` is the
schema for `[ai]`. `config.options_from()` reads the fields, so adding a key means adding a field. An unknown key or a
wrong type is an error that names the key.
