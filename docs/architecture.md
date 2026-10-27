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
| `kb/ids.py` | The install id and the prefix on every stored `source_id`. |
| `kb/ai/` | AI plug-ins. Each one answers `ask(prompt, timeout)` with the model's text. |
| `kb/sources/` | Source types. Each module defines `TYPE`, and its name is the type's name. |
| `kb/sync/` | Reserved for the Sync client. |

## Where Sync plugs in

The Sync client goes in `kb/sync/`, imported as `kb.sync`.

- Every stored `source_id` starts with the prefix of the install that read it: `<install id>:` for this computer, and
  `<install id>@<host>:` for a host it reads over SSH, where host is the name in `[hosts.NAME]`. The install id is
  `c-` and 13 base32 characters, made once and kept in `install-id` beside the config, mode 600.
  `ids.install_id(paths)` reads it and `ids.owner(source_id)` returns it from any stored id; split on `@` as well as
  `:`, or items read over SSH look like another computer's.
- `ingest.apply(db, source, prefix, event, host)` is the one write path for read data, and applying an event twice
  changes nothing. A prune deletes only under the prefix it read, and the summarizer summarizes only this install's
  items.
- After `kb ingest`, when the config has a `[sync]` or `[cloud]` table, kb calls
  `kb.sync.after_ingest(paths, config, db, log)`, which runs `kb sync` and `kb push`. `config.extra` holds those
  tables as parsed TOML, and `config.install` holds this install's id. It runs even when no source is configured, so
  a forgotten reader's deletions still go out. A failure, a missing client included, is the `sync` health unit and
  makes `kb ingest` exit 1. Everything kb logs is masked, tracebacks included.
- `store.mark_superseded`, `clear_superseded` and `superseded_by` keep one mark per marking install in the
  `supersedes` table, keyed by item, not row, so a mark survives a refresh. A clear keeps the row with no target.
  Of several marks the one with no `v` (not synced yet) wins, then the highest `v`, then the highest install id, and
  a winning clear means the item is not superseded.
- For the client and the engine to import: `store.replace_rows`, `search.search_stores`, `check.lookup_with`,
  `gmail.render_thread`, and `summarize.request`, `parse` and `rows`.

Each online account has one reader. Gmail and GitHub read an account, not a computer, so two computers reading one
would store every thread twice under two prefixes. `config.account_keys()` names the account each source reads, one
key however the config spells it (case, a leading `@`, dots in a gmail.com name). With `[sync]` or `[cloud]` on, a
source whose config does not name its account, such as GitHub left at `"@me"`, is a config error. The Sync client
refuses a second reader: at `kb sync join`, and on every `after_ingest`, because a member can add a
source after joining. An account key holds a mail address, so it goes only where item text goes. To move a reader to
a new computer, run `kb forget NAME` on the old one and remove `[sources.NAME]` from its config, then add the source
on the new one. When the old computer is gone, removing it from Sync must clear its items; that is the Sync client's.

What a stored item carries, and so what a synced or pushed item exposes to whoever can read it: the source name;
the `source_id`, whose native part can hold a file path, a mail account or a chat handle such as a phone number;
the title and text, masked for secrets; the metadata, such as a path, a URL, the host name and chat participants;
its summary rows; and supersede marks with the install ids that made them. Treat `source_id` and metadata as
private as the text.

## Config keys come from dataclasses

A source type's `Options` dataclass is the schema for its `[sources.NAME]` table, and an AI plug-in's `Options` is the
schema for `[ai]`. `config.options_from()` reads the fields, so adding a key means adding a field. An unknown key or a
wrong type is an error that names the key.
