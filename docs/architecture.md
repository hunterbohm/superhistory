# How the package is laid out

The Python package is `kb`, the product's earlier command name, and it stays so that imports and history do not move. The
product name lives in one constant, `kb.NAME`, and `kb.COMMAND` is the command it gives. Folder names, job labels and
printed text come from them, so a rename is one edit.

## Modules

| Module | What it owns |
| --- | --- |
| `kb/cli.py` | The `superhistory` command. It is the only module that builds `Paths` and reads the config file. |
| `kb/paths.py` | Every file and folder Superhistory writes for itself, per platform. `SUPERHISTORY_HOME` puts them all in one folder. |
| `kb/config.py` | The TOML config, parsed once into typed settings. On Python 3.9 and 3.10 it reads TOML with the copy of `tomli` in `kb/_vendor/`. |
| `kb/store.py` | The SQLite file: the `docs` table, its full-text index, watermarks and health. |
| `kb/mask.py` | Secret masking and the 2FA filter. Every stored row passes through `mask()`. |
| `kb/gitleaks.py` | Provider token rules taken from gitleaks, with its licence. `tools/vendor_gitleaks.py` writes it from a gitleaks release and says which rules it took and why it left the rest. |
| `kb/search.py` | Keyword search with rank fusion, term coverage and recency. |
| `kb/check.py` | The check: one model call writes a query, search finds candidates, a second call keeps the ones that fit. |
| `kb/summarize.py` | Segment and atom rows for long items. |
| `kb/decisions.py` | The decision pass: who made each decision atom, which newer one replaces it, and instruction lines it contradicts. Its `decisions` table keys a judgment by item and text hash, and search leaves out replaced decisions. It never changes `docs`. A personal install carries a copy of it, which matches this file from the `# BODY` line on. |
| `kb/ingest.py` | The runner that reads every source and stores what changed. |
| `kb/backup.py` | The encrypted backup file (its layout and version are in the module's docstring), and the restore that puts it back. It needs the `openssl` command and no Python package. |
| `kb/ids.py` | The install id and the prefix on every stored `source_id`. |
| `kb/ai/` | AI plug-ins. Each one answers `ask(prompt, timeout)` with the model's text. |
| `kb/dashboard/` | `superhistory dashboard`: a local page over the store, read-only. `catalog.py` finds projects and people, `views.py` shapes each page's data, `server.py` serves it on 127.0.0.1, and `static/` holds the page, its script, style and fonts. |
| `kb/sources/` | Source types. Each module defines `TYPE`, and its name is the type's name. |
| `kb/sources/plugin.py` | The `plugin` type: runs a program from the plug-ins folder and reads its items from JSON lines. The format is in `docs/plugins.md`. |
| `kb/sync/` | Reserved for the Sync client. |

## The dashboard

`superhistory dashboard` serves a page on 127.0.0.1 only, on the port the user picks or a free one, and stops on Ctrl-C.
It opens the store read-only (`mode=ro` and `query_only`), answers only GET and HEAD, refuses a request whose Host is
not its own address (a web page cannot reach it by DNS rebinding), and loads nothing from outside: the fonts, script
and style ship in `kb/dashboard/static/`, and a content-security policy blocks anything else. The fonts are under the
SIL Open Font License, copied in `static/OFL.txt`. A read-only connection to a store in WAL mode can leave an empty
`-wal` or `-shm` file beside it; the store's own file is never written.

Every account on the computer can reach a loopback port, so the data is behind keys made for each run. The printed
address holds a token as `?access=TOKEN`, good for one visit, and the token opens no data. That visit is swapped for a
second, different value, the session key: the page carries it in a meta tag, its script keeps it in `sessionStorage` and
takes the token out of the address, and every request for data sends the session key in the `X-Superhistory-Key`
header. A second `?access=TOKEN` gets 403, and the token sent as the header gets 403, so a copy of the address read from
a process list, a terminal's scrollback or a browser's history is worthless once it has been used. The session key is in
no cookie and no address, so a program listening on another port of the same computer never receives it. The page and
its static files hold no data and need no key, so a reload keeps working in the tab (its `sessionStorage`) and a tab
without the key shows a page that says it has none. The command opens the browser on a file only the user can read, in a
folder only the user can enter (`open.html` in a new `superhistory-*` folder of the temp directory, modes 0600 and
0700), that redirects to the address, so the token is never in the browser's command line. The file is removed as soon
as the address has been used, and when the dashboard stops. Opening the address is the one visit: a link scanner or
prefetcher that fetches it first uses it up, and running the command again makes a new one. The terminal shows the
address, which is the user's own. A program running as the same user can still read the store itself, and so can read
this.

Items are grouped from what sources record, never from a name. `kb/dashboard/catalog.py` explains the rules at its top:
an item's family follows its metadata (`cwd` or `session_id`, `account`, `chat`, `sha`, `repo` with a `kind`), a project
is the folder an agent session ran in or the repository of a commit or pull request (a home folder is no project), and
a person is a text participant or a mail correspondent other than the user's own accounts, keyed by name so that one
person texted and mailed is one page. Senders whose threads all carry Gmail's category labels are listed apart as
lists and notifications. Text shown is masked as `get` masks it: a row of today's mask version as stored, any other
row masked again whole first.

## Where Sync plugs in

The Sync client goes in `kb/sync/`, imported as `kb.sync`.

- Every stored `source_id` starts with the prefix of the install that read it: `<install id>:` for this computer, and
  `<install id>@<host>:` for a host it reads over SSH, where host is the name in `[hosts.NAME]`. The install id is
  `c-` and 13 base32 characters, made once and kept in `install-id` beside the config, mode 600.
  `ids.install_id(paths)` reads it and `ids.owner(source_id)` returns it from any stored id; split on `@` as well as
  `:`, or items read over SSH look like another computer's. `ids.owned_sql` is the same test in SQL.
- `ingest.apply(db, source, prefix, event, host)` is the one write path for read data, and applying an event twice
  changes nothing. A prune deletes only under the prefix it read, and the summarizer summarizes only this install's
  items.
- After `superhistory ingest`, when the config has a `[sync]` table, or a `[cloud]` table (an older name Superhistory still reads), Superhistory calls
  `kb.sync.after_ingest(paths, config, db, log)`, which runs `superhistory sync` and `superhistory push`. `config.extra` holds those
  tables as parsed TOML, and `config.install` holds this install's id. It runs even when no source is configured, so
  a forgotten reader's deletions still go out. A failure, a missing client included, is the `sync` health unit and
  makes `superhistory ingest` exit 1. Everything Superhistory logs is masked, tracebacks included, and Superhistory's records stay out of the
  root logger. So is every error Superhistory raises with text it did not write: a provider's answer, a CLI's, ssh's, gh's
  or git's error output, a `cmd:` secret, a remote line, Gmail's and Google's answers.
- `store.mark_superseded`, `clear_superseded` and `superseded_by` keep one mark per marking install in the
  `supersedes` table, keyed by item, not row, so a mark survives a refresh. A clear keeps the row with no target, and
  deleting an item keeps the marks on it, so the result does not depend on the order records arrive in.
  Of several marks the one with no `v` (not synced yet) wins, then the highest `v`, then the highest install id, and
  a winning clear means the item is not superseded. A rebuild copies every mark and clear to the item's new id with
  its `marked_by`, `at` and `v` as they were, and names the ones whose item the new store lacks.
- For the client and the engine to import: `store.replace_rows`, `search.search_stores`, `check.lookup_with`,
  `gmail.render_thread`, and `summarize.request`, `parse` and `rows`.

Each online account has one reader. Gmail, GitHub, YouTube and Eden read an account, not a computer, so two computers reading one
would store every thread twice under two prefixes. `config.account_keys()` names the account each source reads, one
key however the config spells it (case, a leading `@`, dots in a gmail.com name). With `[sync]` (or `[cloud]`) on, a
source whose config does not name its account, such as GitHub left at `"@me"`, is a config error. The Sync client
refuses a second reader: at `superhistory sync join`, and on every `after_ingest`, because a member can add a
source after joining. An account key holds a mail address, so it goes only where item text goes. To move a reader to
a new computer, run `superhistory forget NAME` on the old one and remove `[sources.NAME]` from its config, then add the source
on the new one. When the old computer is gone, removing it from Sync must clear its items; that is the Sync client's.

What a stored item carries, and so what a synced or pushed item exposes to whoever can read it: the source name;
the `source_id`, whose native part can hold a file path, a mail account or a chat handle such as a phone number;
the title and text, masked for secrets; the metadata, such as a path, a URL, the host name and chat participants;
its summary rows; and supersede marks with the install ids that made them. Treat `source_id` and metadata as
private as the text.

## Where secrets are masked

`mask()` in `kb/mask.py` replaces secret-looking text. Superhistory applies it at every boundary text crosses:

- Into the store: an item's title, text and metadata (only the secret-looking parts, so links and paths still
  work), and the source's id for the item. Summary rows are masked too. `store.replace_rows`, the one way rows
  enter, masks again, so rows another store sent are masked as well, and masks a long item's chunks as one text: a
  store that cut an item before masking it can split a secret in two that neither chunk shows, and so it writes
  an item's chunks all at once or not at all. It refuses a source or item id that holds a secret, as ingest does
  (`store.secret_id`: the host the rows' metadata names and the reader's id apart, then any name of the reader's own
  that the metadata records, such as a Hermes profile, apart from the rest, and any other id whole). The names come
  from the item's own rows, since an older Superhistory's summary rows record only some of them. An id is read more narrowly
  than text, so a note titled "Tokens: 2026 plan", filed under "Ring Bearer duties/" or named "sk-learn-notes.md" is
  kept. It
  records on each row the version of `mask()` it used (`mask.VERSION`, which follows the patterns and `REVISION`). A database trigger clears that version when anything else changes a row's time, title,
  text or metadata, as an older Superhistory would.
- Out of Superhistory: the Superhistory command's stdout and stderr, every log line (through `masked_log`, whatever logger the caller
  passes, plus a filter on Superhistory's own handlers), `check.log`, the remote reader's output, recorded failures, and the
  text of every error Superhistory raises from outside text: AI answers, secrets, ssh, gh and git output, IMAP and Google
  answers. An older Superhistory cut failure lines before masking them, so `store.clean_state()` drops every failure line and
  any state row holding a secret once for each mask version, and until then `store.failing()` shows only when an
  older line's unit failed.

- To the AI plug-in: every prompt, through the model `ai.load` returns and through `ai.hedged` and the summarizer
  for a model a caller made itself. The check's prompt (the user's message, earlier turns, the records) and the Gmail
  sorter's lines are masked, so the plug-in, which can be another provider than the user's agent, never sees a
  secret's value. Superhistory's own instructions in those prompts contain nothing mask() changes.

Rows an older Superhistory wrote record no mask version, as main's did, and the store's trigger clears the version on any row an
older Superhistory changes; Superhistory's own writers stamp it again. An older Superhistory's masking can leave a secret that no pattern finds
afterwards, such as a value whose name its named-value rule took, so Superhistory never reads such a row or masks it again in
place. `store.connect()` refuses a store that holds one (`store.NeedsRebuild`), and a connection opened before an older
writer changed a row shows it to no reader and leaves it to no remask (`store.unversioned`). `superhistory rebuild` ingests every
source into a fresh file under the lock, a source with a window reaching back to its oldest item in the old file and one
with a cap on a run reading past it, and records the configured settings so the next ingest goes on from there. It puts
the fresh file in place of the old one, at a link's target, only once every source was read, every host reached, the
file checks sound, no row lacks a version and no source lacks an item the old file holds, unless `--accept-loss`. Of the
old file it reads only each source's ids and oldest time. A swap that stops puts the old file back, but never over a
store at its path: then the old one stays aside, named, and the next rebuild waits until it is moved. Remask brings rows
of an earlier version of today's mask() up to date. `tests/test_old_rows.py` generates both kinds of rows, an earlier
version's and an older Superhistory's with main's own mask (`tests/legacy_mask.py`), and checks remask over the first and the
refusal over the second.

Text is masked before it is cut, never after: a cut can keep part of a secret too short for mask() to know. A row of
today's version, and for a chunk every chunk of its item, was masked whole, so `superhistory search` cuts it as stored
(`store.current`). Any other row, from before versions or from an older `mask()`, is masked whole when read: search
masks the hit's whole item, its title and its metadata, finds the match again in the masked text with FTS5's own
matching, and leaves out an item whose id holds a secret. `superhistory get` and the summarizer always mask the whole item.

`mask()` masks a private key by its structure, never line by line: from its header through its footer, or, with no
footer, through the end of its paragraph (the next blank line, plain or escaped), an encrypted key's past the blank line
after its DEK-Info field, and a PGP key's past the blank line after its armor headers. PEM, PGP and ssh.com's SSH2 keys
work this way; a PuTTY key file masks from `Private-Lines:` through its paragraph, and an age secret key by its shape. A
PEM private key or a PGP private or secret key block written as base64, as a Kubernetes secret holds one, masks through
the lines that wrap it, their breaks plain or escaped at any depth, as JSON in JSON writes them, and through lines that
each hold its base64 among other text (a quote's or a comment's mark, a line's number, code or a table's cells around
it): in a run of lines that each hold base64, where the longest run on each line, joined, holds a key's word, each line
is read again as that run alone, but for words before it on the lines after the last that carries a mark, and where
a line's run lies after its longest, again with that line's longest; and in every run of two lines or more, again with
each line's run that decodes, after the lines above, to text (or within a key is as long as the line above's, or holds
a key file's header), and all from the key's header to the run's last line of base64 masked where more than one run on
a line of a key's goes on, or, on lines that hold other text, one goes on after a key's base64 ended before its
footer. It is found
where its encoded "PRIVATE KEY" or "SECRET KEY" decodes inside the key's header or footer on a line of its own, so a field's name
or a sentence that names one, or quotes a header, stays. It is read once over the text as written, and again with the
characters a reader does not see taken out, and hidden over what mask()'s passes leave: no reading reads its
placeholder, so what follows the key, such as a certificate's base64, stays, and all the passes hide stays hidden. It
masks from the line that holds its header's first byte through the line with its footer's last byte and the line breaks
after it, so a heading, a certificate above it or a word between two keys stays however the lines are wrapped; a key cut
off before its footer masks while the base64 still decodes to text, and one cut before its header from where it decodes
to text above. A Docker config written as base64 masks too, and so does Docker's "auth" field when it decodes to
"user:password". A footer with no header masks its paragraph up to it. What an older Superhistory's key pattern left after its
placeholder masks through its footer or the end of its paragraph: base64 glued to it, or a key's body from its first
line, which starts as every private key's does (DER's "MII", "MHcC" or "MC4C", or OpenSSH's "b3Bl") or any line of
base64 that mixes cases and digits, as part of a body split or re-wrapped holds, after what can stand before a line's
text (line breaks plain or escaped at any depth, quotes, "#", ">", "+", <br> and spaces). It fails closed: such base64
is shown only when it decodes whole as public material, a certificate, a certificate request, a public key
(SubjectPublicKeyInfo) or an OpenSSH public key, so one cut short stays masked. Where main's reading of the
placeholder's rest takes it, it runs at least as far as main's, through its footer or to its paragraph's end or the
next header, whatever it holds, words included; elsewhere it stops before a line of words ("next steps") or a JSON
object's next field. An item id starts a key's rest with a body's start
alone, as main does. Text after a broken key in its paragraph is masked with it; that is the cost.

Text can arrive escaped. In JSON text a secret right after an escape such as "\n" is masked as at the start of a
line, and a value ends where an escape starts the next "name=" or "name:", so that name keeps its own. A percent
escape such as "%20" counts the same way, and a quote may be escaped, nested in JSON, percent-encoded or an HTML
entity. The Claude Code, Codex and Hermes readers also mask a tool's input field by field with `mask_all()` before
they write it as JSON, and `mask_all()` masks a string under a secret's field name whole.
A secret reads through characters a reader does not see: NUL and the other C0 controls that are not whitespace, and
Unicode's default ignorable code points, such as zero-width spaces and joiners, the word joiner, the soft hyphen and
direction marks. The vertical tab, the form feed and the separators from 0x1c are whitespace and part words. Each pass
reads the text as written, as main does, where invisibles part words as a space does. Beside it, the pass reads the
text without the invisible ones, the view, for the secrets only the view holds: a key split by them, or with one
between every character, even where its first part is a key by itself, the rest of one an older Superhistory masked only up to
one, and a secret's name split by them, whose value goes with it. In the view a token may also start right after a
letter where one stood, as after a space, so one before a key glued to a word reads it (`budget<ZWSP>s<ZWSP>k-...`).
A split token ends before any reading the text as written starts inside it, such as a link's scheme, a command or a
secret's name, and a run of invisibles just before one stays a break. The reading as written never sees what the view
masked, so the view can only add. Each pass reports where every character of its result came from, and where the view
adds, the characters of that result that came from the view's spans are hidden, a placeholder for each run of them but
one beside a placeholder, which takes them in; the rest stays as the pass left it, invisibles and all. A text whose invisible characters split no secret masks as on
main and keeps every one of them, an emoji's joiners too. tests/test_mask_against_main.py holds this to a frozen copy
of main's mask.

Superhistory masks no Sync recovery or pairing codes. The Sync client's egress masks them before anything leaves the computer.

A command line's password is masked by the flag that takes it: a long flag named for a secret ("--api-key X"), and
the short flags of the commands in `mask.COMMAND_FLAGS` (curl's -u, mysql's -p, sshpass -p, a registry login's -p,
redis-cli -a), each read only on its command's own line, so a long line costs time in proportion to its length.
`mask()` repeats until a pass changes nothing, so masked text masks to itself and plain text costs one pass. No
pattern matches a placeholder alone, so it stops; at its limit of eight passes it masks the part still changing whole,
and ingest counts such texts for `superhistory stats`. Each ingest masks older rows again for up to a minute
(`store.remask`), deletes items whose id holds a secret and logs each with its id masked, and records today's version,
so an interrupted run leaves the rest old. The file is rebuilt once none are left, and a rebuild cut short runs again
on the next ingest. The header counts the rows left, and `superhistory remask` does them all at once. An HTTP error body longer
than Superhistory reads is left out of the error.

An item's rows record a hash of its title and text as stored, masked (`store.item_hash`), whatever hash rows another
store sent came with: Sync sends it with the masked text, and a hash of the text before a mask changed it
would let the relay confirm a guess at the secret. When remask changes an item's text, its rows record the new hash, a
summary of that text names it, and no hash of the text before stays: an older summary's hash and every summary's
prefix hash go, so the item's next growth gets a full pass, and a failed summary's mark moves to the new hash.

`tests/test_write_fuzz.py` generates key blocks of every header kind, line width, line break and cut, and runs a few
thousand seeded sequences of the store's writes: ingest, Sync-shaped full and
piecemeal writes, summary rows, an older Superhistory's store_item and remask (`tests/legacy_store.py`), remask cut off at a
random item or in its rebuild, deletes and supersede marks. Canaries cross chunk and call boundaries. After each
sequence no reader may show a piece of a canary, and no row may read as current unless its whole item masks clean.

Records that other code sends through its own loggers to its own handlers are that code's to mask.
`tests/test_canary.py` plants a different secret in every kind of input, runs every command and every library entry
point, and checks that none reaches an output. A path it misses is a new case there.

## Config keys come from dataclasses

A source type's `Options` dataclass is the schema for its `[sources.NAME]` table, and an AI plug-in's `Options` is the
schema for `[ai]`. `config.options_from()` reads the fields, so adding a key means adding a field. An unknown key or a
wrong type is an error that names the key.
