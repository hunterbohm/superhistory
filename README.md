# Superhistory

The knowledge base your agents check first. The command is `superhistory`.

Superhistory collects what already happened on your computer: your Claude Code, Codex and Hermes sessions, your email
and texts, your notes, your commits and GitHub threads. It keeps them in one SQLite file. When you send a prompt to an
agent, a hook asks `superhistory` for anything that fits. A small model writes a search, a second pass keeps only the records
about what you asked, and the agent gets those few records, or nothing.

Memory tools save notes while an agent works, and the agent decides what to remember. Superhistory saves nothing on an
agent's behalf, and agents never write to it. It reads the record you already have, and it adds nothing when nothing
fits.

## Install

You need a Mac or Linux and Python 3.9 or newer. A Mac's `python3` from the Command Line Tools is enough. The
installer needs no root, pip or virtual environment.

```sh
git clone <this repository> superhistory-source
sh superhistory-source/install.sh --from superhistory-source
```

It copies Superhistory into your data folder and writes the `superhistory` command to `~/.local/bin`. If that folder is not on your
`PATH`, the installer prints the line to add. If another program named `superhistory` comes first on your `PATH`, the
installer says so.

You can also install with `pipx install ./superhistory-source` or `uv tool install ./superhistory-source`. The package and the command are both named
`superhistory`.

Superhistory is free and open source to self-host, plus Sync at $10 a month.

## Try it before you connect anything

```sh
superhistory try
```

This loads a made-up month (sessions, mail, texts, notes, commits) into a database of its own and asks two sample
questions. For a question the month can answer, you see what search finds and the records the check keeps. For a
question nothing fits, you see the check add nothing. Without an AI plug-in, `superhistory try` shows the search and stops
before the pick. Run `superhistory --try search "words"` to search the made-up month yourself. Your own data is never touched.

## Set it up

```sh
superhistory setup
```

`superhistory setup` finds the sources on your computer and asks which to add, then which AI plug-in to use. It writes the
config file, offers to install the hooks, offers to read your sources every 15 minutes, and offers to read them now.
Every choice is in one file you can edit later. Run `superhistory paths` to see where it is.

```toml
machine = "studio"          # this computer's name in stored ids; set it once

[ai]
plugin = "chatgpt"          # superhistory ai list shows the choices

[sources.notes]
path = "~/Notes"
exclude = ["Journal"]       # added to the type's default excludes
summarize = ["Meetings/**"] # true, false, or the notes whose summaries are worth writing

[sources.claude_code]
[sources.git]
roots = ["~/code"]
```

## Commands

| Command | What it does |
| --- | --- |
| `superhistory search "words"` | Searches everything. `--source`, `--after` and `--limit` narrow it. `--history` also shows decisions a newer one replaced. |
| `superhistory get ID` | Prints the stored text of a search hit, a page at a time. |
| `superhistory ingest` | Reads every source now, then summarizes new long items. |
| `superhistory stats` | Shows how many records each source has and which sources failed. |
| `superhistory rebuild` | Reads every source into a fresh knowledge base and puts it in place of this one once it checks out. |
| `superhistory schedule on`, `off`, `status` | Reads your sources in the background, with launchd on a Mac or a systemd user timer on Linux. |
| `superhistory hooks install`, `uninstall`, `status` | Adds or removes the hook in each agent found here. |
| `superhistory mcp` | Serves `search` and `get` to an MCP app over stdio, read-only. |
| `superhistory ai list`, `superhistory ai test` | Lists the AI plug-ins, or makes one tiny call and says which plug-in answered. |
| `superhistory setup`, `superhistory try` | Sets Superhistory up, or shows it on a made-up month. |
| `superhistory login NAME` | Signs a Gmail source in with your own Google OAuth client. |
| `superhistory decisions run` | Judges new decision records: whether they are your own words or an agent's report, and which older decision each one replaces. Ingest runs it once a day when `[decisions] owner` is set. |
| `superhistory decisions review`, `confirm ID`, `reject ID` | Lists agent-reported decisions that matter and that your own words don't back, and records your answer. |
| `superhistory decisions drift` | Lists lines in your agents' instruction files that a newer decision of yours contradicts, each with a fix. It changes nothing. |
| `superhistory backup [PATH]` | Writes your knowledge base, config and install id to one encrypted file. |
| `superhistory restore PATH` | Puts a backup back, after checking it, and asks before it replaces anything. |

## Backup

Your history is one file on one disk, and some of it no source can give again: Claude Code deletes sessions after 30 days.
`superhistory backup` writes an encrypted copy, so a lost disk does not lose it.

```sh
superhistory backup ~/Documents          # asks for a passphrase twice; writes superhistory-DATE.backup there
superhistory restore ~/Documents/superhistory-2026-11-06.backup
```

The copy is taken with SQLite's backup API, so an ingest can run meanwhile. The file holds the knowledge base, `config.toml`
and the install id, so a restored computer goes on as the same install. It does not hold the sign-in keys in the `keys`
folder next to the config: run `superhistory login` again. The passphrase must be at least 12 characters. Give it on
the terminal, or in a file only you can read with `--passphrase-file FILE` (`-` reads standard input); it is never taken
from the command line or the environment, where other programs can read it, and Superhistory does not keep it. If you
lose the passphrase, nothing can open the backup: there is no reset. A wrong passphrase, a damaged file and a file cut
short each stop with a plain message and change nothing.

A backup is read back whole before it appears under its name. A restore opens and checks the backup first. If there is
already a knowledge base, config or install id, it names them and asks before replacing them (`--yes` skips the
question). They are set aside while the restored store is checked where it now stands, and deleted once it passes; if
anything fails, they are put back.

The encryption is the `openssl` command, which a Mac has and most Linux systems have (OpenSSL 1.1.1 or LibreSSL 3.1 or
newer): AES-256 in counter mode, the key made from your passphrase with 600,000 rounds of PBKDF2-HMAC-SHA256, and an
HMAC-SHA256 over everything, checked before anything is decrypted. `age` was left out because a Mac does not have it and
its passphrase mode needs a terminal. The file starts with its format number, so a later Superhistory can still read
this one. The layout is at the top of `kb/backup.py`, and with the passphrase the file can be opened by hand with
`openssl`, `gunzip` and `tar`.

## Sources

| Type | Reads | Exclude patterns match |
| --- | --- | --- |
| `claude_code` | Claude Code sessions in `~/.claude/projects` | the session's working folder, as `~/code/client` |
| `codex` | Codex sessions in `~/.codex/sessions` and `~/.codex/archived_sessions` | the session's working folder |
| `hermes` | Hermes conversations in `~/.hermes`, every profile | `<profile>/<platform>` |
| `gmail` | Gmail over IMAP, with an app password or your own Google OAuth client | the sender's address |
| `imessage` | Messages on a Mac (`~/Library/Messages/chat.db`), with names from Contacts | a handle or a contact name |
| `notes` | A folder of Markdown or text files, such as an Obsidian vault | the note's path in the folder |
| `git` | Your commits in the repositories under `roots` | the repository's folder |
| `github` | Issues and pull requests you are involved in, through `gh` | `owner/repo` |
| `youtube` | A channel's videos, shorts and streams with their captions, through `yt-dlp` and the channel's feed | the kind and id, as `short/abc123` |
| `eden` | Your own posts on X or another network, through Eden (eden.so) and your Eden API key | the post's id |

Add as many sources of one type as you like, each in its own `[sources.NAME]` table with `type = "..."`. The table's
name is what `superhistory search --source` takes.

Every source only reads. Mail and texts drop 2FA codes and password resets one message at a time, and every record
passes through a mask that replaces API keys, tokens and passwords with `[masked]` before it is stored.

To read another computer, name it and list it in a source's `hosts`. That computer needs only Python 3.9 and SSH. Superhistory
sends itself over `ssh <host> python3 -`, and nothing is installed or left there.

```toml
[hosts.laptop]
ssh = "sam@laptop.local"

[sources.claude_code]
hosts = ["studio", "laptop"]
```

iMessage needs Full Disk Access for the Python that runs Superhistory. The installer prints which one.

Gmail needs an app password, which needs 2-Step Verification on your Google account. `superhistory setup` saves the password
in a file only you can read. To use your own Google OAuth client instead, set `auth = "oauth"`, `client_id` and
`client_secret`, then run `superhistory login NAME`.

YouTube needs `yt-dlp` and no key. `channel` is an `@handle`, a channel URL or a channel id. The first run reads
every video; later runs read the channel's public feed and only the videos published since. A new video waits up to a
day for its captions.

```toml
[sources.youtube]
channel = "@yourhandle"
languages = ["en"]          # captions in the first of these the video has
```

Eden reads your posts without an X API plan, but each call spends Eden research credits: 0.5 for a page of 50 posts,
0.2 for your analytics and 1 for a whole post, by Eden's price list in November 2026. So it asks Eden at most once
every `every_hours` and reads whole only new posts that Eden cut short or that start a thread, at most `full_reads` a
run. A day's check with one long post costs about 1.7 credits. The first run reads your whole history at 0.5 per 50
posts. Thread detection reads your Eden analytics, so it covers only accounts connected to your Eden workspace, and on the first run only your newest 100 posts, the most Eden's analytics return.

```toml
[sources.x]
type = "eden"
username = "yourhandle"
key = "env:EDEN_API_KEY"    # or "file:..." or "cmd:..."
platform = "twitter"        # or linkedin, threads, instagram, tiktok, youtube, substack
every_hours = 24
full_reads = 5
```

## AI plug-ins

The check, the summarizer and the Gmail sorter use one AI plug-in, the one in `[ai]`.

| Plug-in | What it runs | Notes |
| --- | --- | --- |
| `claude-code` | your own Claude Code: `claude -p` with your login | |
| `chatgpt` | Sign in with ChatGPT: `codex exec` with your login | |
| `grok` | `grok` with your login | About 10 seconds a call, so the check takes about 20 seconds. |
| `copilot` | `copilot -p` with your login | |
| `anthropic`, `openai`, `openrouter`, `opencode-go`, `google` | the service's API with your key | `key = "env:NAME"`, `"file:PATH"` or `"cmd:COMMAND"` |
| `anthropic-compatible`, `openai-compatible` | any server that speaks one of those APIs, at `base_url` | |

A CLI plug-in runs the program you already signed in to. Superhistory never reads, relays or stores that login. Each call runs
in a throwaway folder with no tools and no saved session, without the variables that could send it to a paid key.
API plug-ins read their key only from the place the config names, never from whatever environment Superhistory runs in.

A CLI starts a process for every call, so with a CLI plug-in the check waits up to 20 seconds instead of 10. An API
key is the fast path.

## Agent hooks

`superhistory hooks install` adds the hook to each agent it finds.

| Agent | How |
| --- | --- |
| Claude Code | a `UserPromptSubmit` hook in `~/.claude/settings.json` |
| Codex | a `UserPromptSubmit` hook in `~/.codex/hooks.json`, trusted in `~/.codex/config.toml` |
| Hermes | a `pre_llm_call` plugin in `~/.hermes/plugins/superhistory` |
| OpenCode | a `chat.message` plugin in `~/.config/opencode/plugins` |
| GitHub Copilot CLI | a `userPromptSubmitted` hook in `~/.copilot/config.json`, built from the format Copilot CLI 1.0.79 is reported to use and not yet run on a live install |

Cursor's documented prompt hook cannot add context, and Grok drops what a prompt hook prints, so neither gets a hook. They can ask through the MCP server below instead.
Grok runs Claude Code's hooks too, and Superhistory's hook stays quiet there.

Each hook waits at most 30 seconds. When the check finds nothing that fits, or fails, the agent gets nothing.

These files are yours, so Superhistory edits them carefully. Before it writes, it works out the new file and checks that the
file still reads back and that nothing but Superhistory's own entry changed. If anything else would change, it writes nothing and
tells you how to make the change by hand. Every edit keeps a backup beside the file. In `~/.codex/config.toml` the check
also makes sure every comment on a line of its own survives. A comment at the end of one of your settings lines is kept
because Superhistory never edits that line, but the check itself does not look at it.

## Ask from other apps (MCP)

Hooks only work in agents that let a hook add text to your prompt. Claude Desktop, Cursor, Grok and the ChatGPT desktop
app do not, so they get nothing from the hook. `superhistory mcp` lets them ask on purpose ("what did I promise that
client?"). In Claude Code and Codex, keep the hooks: an agent often does not call a tool it has to choose to call, and
a hook does not wait for it to.

The server is a program the app starts and talks to over its standard input and output. It opens no network port, so
nothing else on your network or the internet can reach it. It has two tools. `search` finds records by keywords, and
`get` reads one record's stored text a page at a time, masked as everywhere else. It opens the database read-only and
never writes to it. Normally it leaves no file beside the database and copies nothing, whether or not the folder is one
you can write to, and a lookup sees what a running ingest has stored so far. There are two exceptions. If an ingest ends
in the instant between the server looking at the database and reading it, SQLite may leave its own empty `-wal` and
`-shm` files beside the database, which hold none of your records and which the next ingest takes over. And if an ingest
died and left a `-wal` file with records in it, SQLite makes an `-shm` file beside the database to read them, or, in a
folder you cannot write to, the server reads a private copy of the two files from your cache folder, which only you can
read; the server removes it when it ends, by any signal but SIGKILL, and the next server removes one that SIGKILL left.
Every reply starts with a line saying the records are stored data, not instructions, since a record can hold text
someone else wrote. The app, not Superhistory, decides what an agent may do with a reply, so do not put a server that
can send mail or fetch web pages in the same session without approvals turned on. Run `superhistory --try mcp` in its
place to serve the made-up month from `superhistory try`.

Each app starts the server by running `superhistory mcp`. An app that does not read your shell's `PATH`, which
includes the desktop apps, needs the full path from `which superhistory` in place of `superhistory`.

| App | How |
| --- | --- |
| Claude Code | `claude mcp add --scope user superhistory -- superhistory mcp` |
| Claude Desktop (Mac) | in `~/Library/Application Support/Claude/claude_desktop_config.json`, then quit and reopen the app: `{"mcpServers": {"superhistory": {"command": "superhistory", "args": ["mcp"]}}}` |
| Cursor | the same JSON in `~/.cursor/mcp.json`, or `.cursor/mcp.json` for one project |
| Codex | `codex mcp add superhistory -- superhistory mcp`, or in `~/.codex/config.toml`: `[mcp_servers.superhistory]` with `command = "superhistory"` and `args = ["mcp"]` |
| ChatGPT desktop app | the same entry as Codex: OpenAI's documentation says the desktop app shares Codex's MCP configuration |
| Grok | `grok mcp add superhistory -- superhistory mcp` |
| Gemini CLI | the same JSON as Cursor's, in its `settings.json` |
| Zed | in its settings file: `{"context_servers": {"superhistory": {"command": "superhistory", "args": ["mcp"]}}}` |

Any other app that runs a local stdio server takes the command `superhistory` and the argument `mcp`. The same lines
are in `superhistory mcp --help`.

What does not work: claude.ai and ChatGPT on the web, and any app that reaches a server only over the internet. They
connect from the vendor's servers, which cannot reach a program on your computer, and Superhistory opens no port for
them. Grok's own documentation lists local stdio servers, and OpenAI's lists them for the ChatGPT desktop app, but
whether either app calls the tools on its own for a given question is for you to find out. Superhistory's own checks
ran against Claude Code, the MCP Inspector and the Python SDK. Claude Desktop, Cursor, Codex, the ChatGPT desktop app,
Grok, Gemini CLI and Zed have not been run against it yet, and their lines come from their documentation.

The server speaks MCP's `2026-07-28` version, where each request carries its version and `server/discover` replaces the
opening handshake, and the `initialize` handshake of `2025-11-25` and the three versions before it.

## Privacy

Everything stays in one file on your computer. The only thing that leaves it is the AI call you chose: the plug-in
gets your message, the two turns before it, and the start of a few candidate records, and the summarizer sends long
items to it. With no plug-in, Superhistory still searches but adds nothing to your prompts and writes no summaries.

Known limits:

- A secret typed into a file name in a form like "password: X" or "password is X" stays in the item's id. The file's
  text is still masked. A bearer token in a file name is caught only by a provider's shape, such as a GitHub token,
  since "Bearer" is a word in names like "Ring Bearer duties".
- A password of letters only, after "is" or after a flag and a space ("--password correcthorse"), reads as a word and
  is not masked, so "Use the --password flag" stays readable. With a digit or a symbol, or after "=" or ":", it is
  masked.
- In JSON written inside other JSON text, a password with an escaped quote in it is masked only up to that quote, and
  not at all when fewer than four characters come before it. In plain JSON, and in a tool's input that Superhistory writes as
  JSON itself, the whole value is masked.
- After "Authorization:" or "Proxy-Authorization:", the credential is the word after a scheme such as Bearer, Token or
  AWS4-HMAC-SHA256. With no scheme, header style (the name at a line's start, after a space, a quote or an opening
  mark, not inside a path), the single token right after the colon is masked when it is shaped like a credential: 12
  or more characters with no whitespace, mixing letters and digits, not a real date and time, a time of day or a range
  of two as ISO 8601 or a log writes one ("2026-11-01T10:00:00Z", "20261101T100000Z", "2026-11-01T10:00:00+0000",
  "10:00:00.123456Z"), and holding either a random run of 10 or more characters between its separators or no plain
  word. So a random token there is masked, digits with a T or a Z among them included ("20261101T100000Z1234"), and
  "ISO-27001", "RFC7235-compliant", "WPA2-Enterprise" and "WindowsServer2022" stay. Two kinds of credential still show.
  After a scheme word Superhistory does not know ("Custom", "Custom-Scheme"), the credential is not masked. With no
  scheme, standard base64 of 32 random bytes (44 characters ending in "=") shows about 22 times in 20,000: a "+" or a "/"
  splits it into parts, and when every part reads as a name, it shows.
- Where a value is judged by its shape alone (after "Authorization:" with no scheme, after "pass" beside a user, in an
  XML element not named for a password, and the other readers listed below), a real timestamp is masked, though it holds
  no secret, when it is written in another script's digits (fullwidth or Arabic-Indic) or when its fraction has more
  than nine digits. Under a key's name, such as "api_key:" or "session_key:", a date and time in ISO 8601's extended
  form is left shown ("2026-11-01T10:00:00Z", with a fraction or a "+00:00" offset too), as is a time of day with no
  zone ("10:00:00"). The same moment in the basic form ("20261101T100000Z") or with a "+0000" offset, a time of day in
  UTC ("10:00:00Z"), a quoted date and time with a space for the T, and a range of two ("20261101T100000Z-20261101T120000Z"),
  basic form included, are masked there.
- In escaped text, such as a tool's input stored as JSON, a named value reads on through "\n" into the next line's
  first word and masks it too: "password: <pw>\nnext line" becomes "password: [masked] line". That over-masking is
  kept on purpose, since what follows "\n" in the same run may be the rest of the secret.
- A secret is read through characters you cannot see, such as a zero-width space or a NUL, but not through a vertical
  tab, a form feed or the separators from 0x1c, which part words as a line break does. A key split by one of those is
  not masked whole. Such a character is read raw, escaped as JSON or Python writes it ("\u200b", "\x7f", the
  surrogate pair JSON writes for one past U+FFFF, "\udb40\udc01", and JSON's "\b" for the backspace between letters
  or digits), and escaped twice, as JSON inside JSON holds it.
- A private key written as base64 (as a Kubernetes secret holds one) is not masked.
- Provider tokens Superhistory does not know by itself are masked by rules taken from gitleaks (kb/gitleaks.py, gitleaks v8.30.1,
  MIT licence), where gitleaks finds one in text, as a word of its own. An Airtable token is masked alone too, where
  gitleaks needs the provider's name near it; a Facebook token only with "facebook" in the text, as gitleaks reads it,
  since alone its shape is also a pipe-separated record's (a timestamp and a trace id). So with "facebook" anywhere in
  the text, such a record of 16 digits, "|" and 32 hex digits is masked too. An item's id is read more narrowly,
  without these rules.
  gitleaks reads Netlify's "nfp_" tokens only by the word "netlify" near a value and has no rule for Resend's "re_"
  tokens, so Superhistory does not mask them by themselves.
- A .netrc line is read where it starts a line or a quoted string. One with no host after "machine" (or "default"),
  whose login and password are both words of letters, reads as a sentence and is shown: "default login page password
  requirements" stays, and so does "default login deploy password sunshine". With a host, or a digit or a symbol in
  the password, it is masked.
- A password an agent types into a web page through a browser tool is masked when a label of the field names a
  password, passcode, secret, token, PIN or the like and nothing else but a form's own words, a kind of credential
  and numbers ("Password", "API token (required)", "Your 4-digit PIN"). A label that says more keeps what is typed,
  even for a password ("Type your password", "Password to view your account"), and an ordinary field labelled only
  "Password" or "PIN" has what is typed masked. With no label, as Chrome DevTools names a field by its id alone, it
  is masked only when it is shaped like a credential and not like a code identifier. Code that types into a page is
  read the same way: a typing tool called with its input written as an object, as Codex's code mode calls one, and a
  string typed into a field a locator found (Playwright's fill, type and press_sequentially in JavaScript or Python,
  Selenium's send_keys, WebdriverIO's setValue, Puppeteer's page.type), whose locator's strings or regex name the
  field, with comments and keyword arguments read past. With no label, only typing into the focused field
  (keyboard.type) is read, by the value's shape. A value typed from a variable, or built at run time (joined,
  templated), stays, and other ways of writing such code are not read.
- Superhistory reads a secret that one colour code or one invisible character splits, or glues to a word, and still hides all
  that Superhistory's earlier mask hid. Shapes built from more than that are not all read: a secret split by two kinds of
  invisible character at once ("pass<soft hyphen>word=..." with a link inside the value), what an older Superhistory left
  glued to a placeholder ("[masked]<zero-width space>rest"), letters glued onto a key after a colour code
  ("AKIA...<ESC>[0mSuffix"), or a secret's name a colour code splits ("DB_<ESC>[31mPASS<ESC>[0mWORD=..."). Where
  Superhistory's earlier mask hid such a secret, this one does too.
- Where Superhistory reads a value by its shape alone (after "pass" or "pwd" next to a user's name, in an XML element not named
  for a password, under a secret's name in a two-item list, in a field with no label, or after a name a colour code
  splits), a code identifier ("ConvertBF16ToF32", "OAuth2AuthorizationCode") is no credential. So a password made of
  words and numbers ("Summer2024Garden") reads as an identifier there and stays, and an identifier with a digit run
  into small letters ("Utf8to16Codec") reads as a password and is masked.
- A token of 16 or more characters reads as a name there only when each part between separators is in one case, or when
  it looks more like the words of code than like random characters. That check is statistical, in both directions. In
  20,000 random tokens of letters and digits in an XML element, 18 of 16 characters still show, 6 of 20, and none of 32;
  the other readers, and tokens of 40 base64url characters or of 20 letters with no digit, leak at similar small rates,
  up to 16 in 20,000 in the tests of the change that added this check. And a real name that looks random
  is masked: about 1 real name in 17,000 (11 of 187,134 names from C headers and TypeScript types, such as
  "RTCRtcpMuxPolicy" and "NI_10MHzRefClock").
- Commands that take a password (`aws configure set`, `az login`, `mongosh`, `twine`, `keytool`, `jarsigner`, `sshpass`,
  `mysql`, `curl`, `docker login`, `redis-cli`) are read by their flags, and some ways of writing them are not read, so
  the password shows:
  - `aws configure set` with the value after a quoted query that looks like an option, after a line continuation (a
    backslash, a CR LF, or an escaped line break in JSON) between the setting and its value, with a `profile.NAME.`
    setting name, with a profile named by a lone backslash, or with `aws`'s own options (`--profile prod`) before
    `configure`;
  - a short option's value glued to the flag (`-pSECRET`) after `mongosh`, `az login` or `twine`, and in a list of a
    command's arguments after any command (`mysql`, `curl -u`);
  - a list of arguments that puts `az`'s own options before `login`;
  - a command quoted inside another shell command's quotes, quoted twice;
  - a password that is itself the name of `curl`, `mysql`, `sshpass`, `docker login` or `redis-cli`, which hides that
    command's later flag;
  - a command written as a list in code with a member that cannot be read as a value (a dict keyed by a list, a chain of
    100,000 signs, an integer of a thousand digits or more).
  The opposite error also happens, since a flag's value is read across more text than it should be: in prose, "For
  mongosh, connect through ssh -p 2222 host" masks 2222; a `keytool` or `jarsigner` flag's value is read across a
  continuation backslash, or in JSON across an escaped line break, so the backslash, or the break and the next line's
  first word, is masked with it; and in a list of arguments, an `az login` line is read across a ";" item, so a later
  `ssh -p` value is masked.
- Code that types into a page leaves more shapes unread than the ones listed above: a typing method called as a method
  of a browser, driver or client object with the selector as its first argument (`browser.setValue(selector, value)`,
  `driver.sendKeys(selector, value)`); a method called through brackets (`page.getByLabel("Password")["fill"](...)`);
  "Type" as a form's verb inside a selector's attribute (`input[placeholder="Type your password"]`), where the field
  reads as one about the type of a secret; three or more spaces between a mark and a regular expression, or a regular
  expression that ends in an escaped backslash, which keep a typing call after them from being read; and values built at
  run time, or typed by other automation libraries and recorder formats.
- Escaping hides some secrets from the readers. A provider's token split by an invisible character and written as JSON
  inside JSON inside JSON shows a fragment. A password line of base64 after a placeholder and lines of base64 that
  decode to words, written as JSON escaped twice, shows, though it is masked when plain or escaped once.
- Masking costs time in proportion to the text, but a text in which Superhistory's newer readings fire is masked a second time
  beside Superhistory's earlier mask, so a long run of built shapes is slow: 4,000 soft-hyphen-split passwords with a link
  inside each (160 KB) take about 10 s, 30,000 command lines with a password each (900 KB) about 14 s, and 256 KB of
  nothing but Playwright fill calls with a password each about 8 s.
  One very long line that nests command substitutions of the commands above (`keytool`, `az login`, `twine`,
  `aws configure set`) costs more than in proportion: about 3 s at 16 KB and 11 s at 32 KB for one shape, and about 47 s
  at 16 KB for another, on a loaded machine. Typing calls cost about 30 microseconds a byte of text that holds one: 19 s
  for 640 KB.
- Where a later pass cuts into a placeholder, a bracket can be left beside it ("[masked]]"), or two placeholders stand
  side by side ("[masked][masked]"); no character of the secret is left.
- A password with a comma, a semicolon or a bracket in it is masked whole in quotes, in an XML element or on a line of
  its own, as a .env or YAML file writes one; inside a sentence it is masked up to that mark. One with a space, a
  backtick, "<", ">" or a quote in it is masked up to that, but for Google's app passwords (four groups of four
  letters).
- In a note's path, the words after "Basic" or "Bearer" read as words, not a credential, so a note in "Basic
  Statistics/" keeps its path: the words, or the path they are in, end in a file's extension, and each part is a word
  with a vowel made of common English letter pairs. A header's value, a run with a digit, "+" or "=", hex letters
  alone and base64 of "user:password" are masked as before. A run of letters alone that happens to read as English
  shows too. A field after such a path, as "Cookie: ...", is masked as it is anywhere else.
- A few rare file names are refused as if they held a secret, such as one that names a command's password flag with a
  value ("redis-cli -a auth2.md") or reads as a field ("Cookie: chocolate chip.md").
- A private key is masked by its structure: from its header through its footer, or, if the footer is missing, through
  the end of its paragraph. So whatever follows a key cut off before its footer, up to the next blank line, is masked
  with it, and so is the paragraph before a footer with no header.
- A knowledge base that an older Superhistory wrote, before each row recorded how it was masked, does not open: an older Superhistory's
  masking can leave a secret that no pattern finds afterwards. Superhistory says so and names `superhistory rebuild`, which reads your
  sources into a fresh one, each as far back as its oldest item in the old one. When a source no longer has some of
  the old one's items, such as sessions an agent has since deleted, the rebuild stops, keeps the old one and says how
  many each source would lose; `superhistory rebuild --accept-loss` replaces it anyway and loses them for good.
- A command's password flag is read on a line the shell carries on with a continuation mark ("\" at a line's end,
  or "`" or "^" after a space), through a colour code anywhere in the line, inside the program's name too, and in a
  list or a tuple of the command's arguments, as an item's metadata, JSON or Python's repr holds one, however long.
  The command is read by its name, its Windows file ("keytool.exe", "aws.exe", "az.cmd"), a folder path or quotes,
  as written or in JSON escaped up to four times. Where keytool, jarsigner, az login, mongosh and its tools, twine or
  aws configure set has a password flag past shell grammar Superhistory does not follow ("$'...'", "<(...)", an extended
  glob, an escaped quote, a substitution nested or escaped twice), Superhistory masks from the value after that flag to the
  end of the line, or of the JSON string holding it, so the rest of that line, a pipe to another command included,
  is masked with it. A newline escaped inside "$'...'" ends that line early, and the password after it shows; so
  does a list item holding a flag and its value together ("-p ...") right after another option, whose value it may
  be.
- A list of a command's arguments that also holds a number of a thousand digits or more, with or without underscores
  between them, is not read as a list, since such a number takes long to read on Python 3.9. Its text is read
  instead, as Superhistory read such lists before, so a password after a flag in it can show.
- A YAML list of secrets under a secret's name (`tokens:` followed by `- value` lines) is masked only where an item
  holds a provider's token shape.

## Why there are no embeddings

On a set of real messages from the author's own use, adding embeddings to the search (a local embedding model over a
year of summaries) moved the share of messages handled right from 90.5% to 91.2%. That gap is inside run-to-run noise:
three runs of the embeddings version scored 89.5%, 90.5% and 93.5%. Embeddings found no more useful records, so they
were dropped (October 2026). What moved the number was the second pass that adds nothing when nothing fits. A version
without it handled 51% of messages right, and with it 90%. So Superhistory searches with SQLite's full-text index and spends the
model call on judging, not on vectors.

## Develop

```sh
python3 -m unittest discover -s tests -t .
```

The tests use temp folders only. `SUPERHISTORY_HOME=/some/folder` puts the config, database, logs and cache in one folder.
`docs/architecture.md` maps the modules.

## License

MIT. See [LICENSE](LICENSE).
