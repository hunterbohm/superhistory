# Writing a source plug-in

A source plug-in is a small program that prints items. Superhistory runs it on every ingest, stores what it prints
the way it stores any source's items, and masks every item first. Anyone can add a source this way, and so can their
agent: write one file, put it in the plug-ins folder, name it in the config.

This page is the whole format. An agent that reads it has what it needs.

## Where it goes

The plug-ins folder is `plugins` beside the config file: `~/.config/superhistory/plugins` on a Mac and on Linux.
`superhistory paths` prints it as `plug-ins`. Put the program there as one executable file (`chmod u+x`) and name it in
the config:

```toml
[sources.notes-feed]
type = "plugin"
plugin = "notes-feed"         # the file's name in the plug-ins folder
timeout_seconds = 300         # default 300, at most 3600
max_output_mb = 50            # default 50, at most 200
[sources.notes-feed.settings] # given to the program as it is
folder = "~/Notes"
[sources.notes-feed.secrets]  # where each secret lives, never the secret
token = "file:~/.config/superhistory/keys/notes-feed.token"
```

A source's name is what `superhistory search --source` takes. `exclude` globs work as for any source, over item ids.
`summarize` works too; the default is on, with the narrow kind of summary used for text other people wrote.

A secret is `env:NAME`, `file:PATH` or `cmd:COMMAND`, as for Gmail's password. Superhistory reads it when the
program starts and puts it in the request. Keep a secret out of `settings`, which is plain text in the config.

The program runs only if the file is in the folder and a source names it. Nothing is downloaded or installed from
anywhere. It must belong to you and not be writable by your group or others, in a folder that is not writable by them either, or
Superhistory fails the source and says so. A source runs on this computer only, never over SSH.

## What it receives

On its standard input, one JSON object on one line, then the end of input:

```json
{"protocol": 1, "watermark": null, "settings": {"folder": "~/Notes"}, "secrets": {"token": "..."}}
```

- `watermark` is the string the program printed as its last line on its last good run, or `null` on the first run, after
  a rebuild, and after the source's settings changed. Read only what is newer than it.
- `settings` and `secrets` are the config's tables, as JSON. A secret that is not in `secrets` is not needed.

Its environment has only `PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TMPDIR`, `TZ`, `SSL_CERT_FILE` and
`SSL_CERT_DIR`, and it starts in the plug-ins folder. It must not leave anything running after it exits.

## What it prints

On its standard output, UTF-8 JSON, one object per line. There are three kinds of line.

An item:

```json
{"id": "2026-10-04-sync", "time": "2026-10-04T09:30:00Z", "title": "Weekly sync", "text": "Notes from the sync...", "url": "https://example.com/notes/2026-10-04-sync", "meta": {"room": "4"}}
```

| Key | Needed | Meaning |
| --- | --- | --- |
| `id` | yes | Text of up to 300 characters on one line. It names the item in your source for good: the same id replaces the item, a new id adds one. Do not put a secret in it. |
| `time` | yes | ISO 8601: `2026-10-04T09:30:00Z`, `2026-10-04T09:30:00+02:00`, `2026-10-04 09:30` or `2026-10-04`. A time with no zone is UTC, and a date alone is its midnight. This is the item's date in search. |
| `title` | yes | Text. Cut at 300 characters. |
| `text` | yes | Text, any length. Long text is split into chunks, as for any source. This is what search finds and `superhistory get` prints. |
| `url` | no | A link with a scheme and no spaces. Search prints it as the place to open the original. |
| `meta` | no | An object of anything JSON can hold, up to 16,000 characters. It is stored with the item. The keys `hash`, `chunk`, `chunks`, `host`, `atom`, `of_hash`, `of_chars` and `prefix` are Superhistory's own and are refused. |

No other key is allowed, so a misspelled key fails loudly.

An item that is gone (deleted, cancelled), which removes it from the store:

```json
{"id": "2026-10-04-sync", "gone": true}
```

The final line, exactly one, last:

```json
{"watermark": "2026-10-04T09:30:00Z"}
```

The watermark is non-empty text of up to 200 characters on one line. It is opaque to Superhistory: a time, a counter or a
cursor, whatever lets the program read only what is new. Use something that does not look like a secret, so not a
long random token; a time or a counter is best. Superhistory gives it back on the next run exactly as printed.

Blank lines are ignored. Print one item with the same id twice and the last one wins.

## When it fails

A run either succeeds, and then its items and its watermark are stored together, or it fails and stores nothing.
That keeps one rule for the watermark: it moves only past items that were stored. A run fails when:

- the program exits with anything but 0, even after it printed a final line;
- it prints no final watermark line, or a line after it;
- any line is not valid: not JSON, a key missing or misspelled, a wrong kind of value. Valid lines in the same run are
  not stored either, because moving the watermark on would lose the invalid item for good. The message counts the
  lines and names the first, without quoting it;
- it runs longer than `timeout_seconds` (the program and everything it started are stopped), prints more than
  `max_output_mb`, or prints one line longer than 8 MB;
- the store refuses an item (for example an `id` that looks like a secret, or a `meta` value it cannot hold): the whole run is rolled back, so no item is lost behind a moved watermark;
- its watermark looks like a secret, or it does not start (missing, not executable, writable by its group or others).

A failure is recorded for that source only, shown by `superhistory stats` as FAILING with its reason, and makes the
ingest exit 1. The other sources still run. The next ingest starts from the old watermark again.

Its standard error goes to the ingest log when the run ends, masked as one text (so a key printed over several lines is masked whole), as `plugin NAME: ...`: up to 200 lines and 64,000 bytes a run. The
last few lines also go into the failure message. Print progress and reasons there, never on standard output.

## Masking

Every item passes through masking before it is stored: its title, its text and its `meta`. A secret in an item is
replaced, the same as for a note or a mail. Masking is not a reason to print secrets; read only what should be in the
knowledge base.

## Try it

```
superhistory plugins list           # the folder's files, whether each will run, and which sources use them
superhistory plugins test notes-feed  # runs it once from the start, prints what it found, stores nothing
superhistory ingest --only notes-feed # the real run
superhistory search "weekly sync" --source notes-feed
```

`plugins test` prints the first items, the watermark, and on a failure the same message the ingest would record. A
rebuild (`superhistory rebuild`) runs every plug-in from a `null` watermark, so a plug-in must be able to read
everything it has.

The scheduled ingest (`superhistory schedule on`, every 15 minutes unless `[schedule] every_minutes` says otherwise) is
the same ingest, so it runs plug-ins too.

## An example

This reads text files from a folder and prints the ones changed since the last run. It uses only Python's standard
library; any language that can print JSON lines will do.

```python
#!/usr/bin/env python3
import json, os, sys
from datetime import datetime, timezone

request = json.load(sys.stdin)
folder = os.path.expanduser(request["settings"]["folder"])
since = float(request["watermark"] or 0)
newest = since
for name in sorted(os.listdir(folder)):
    path = os.path.join(folder, name)
    modified = os.path.getmtime(path)
    if not name.endswith(".txt") or modified <= since:
        continue
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    when = datetime.fromtimestamp(modified, timezone.utc)
    print(json.dumps({"id": name, "time": when.isoformat(), "title": name[:-4], "text": text,
                      "url": "file://" + path}))
    newest = max(newest, modified)
print(json.dumps({"watermark": str(newest)}))
```

Save it as `notes-feed` in the plug-ins folder, `chmod u+x` it, and add the table at the top of this page.
