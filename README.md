# Varve

The knowledge base your agents check first. The command is `kb`.

Varve collects what already happened on your computer: your Claude Code, Codex and Hermes sessions, your email and
texts, your notes, your commits and GitHub threads. It keeps them in one SQLite file. When you send a prompt to an
agent, a hook asks `kb` for anything that fits. A small model writes a search, a second pass keeps only the records
about what you asked, and the agent gets those few records, or nothing.

Memory tools save notes while an agent works, and the agent decides what to remember. Varve saves nothing on an
agent's behalf, and agents never write to it. It reads the record you already have, and it adds nothing when nothing
fits.

## Install

You need a Mac or Linux and Python 3.9 or newer. A Mac's `python3` from the Command Line Tools is enough. The
installer needs no root, pip or virtual environment.

```sh
git clone <this repository> kb-source
sh kb-source/install.sh --from kb-source
```

It copies kb into your data folder and writes the `kb` command to `~/.local/bin`. If that folder is not on your
`PATH`, the installer prints the line to add. If another program named `kb` comes first on your `PATH` (the
`kb-manager` package installs one), the installer says so.

You can also install with `pipx install ./kb-source` or `uv tool install ./kb-source`.

## Try it before you connect anything

```sh
kb try
```

This loads a made-up month (sessions, mail, texts, notes, commits) into a database of its own and asks two sample
questions. For a question the month can answer, you see what search finds and the records the check keeps. For a
question nothing fits, you see the check add nothing. Without an AI plug-in, `kb try` shows the search and stops
before the pick. Run `kb --try search "words"` to search the made-up month yourself. Your own data is never touched.

## Set it up

```sh
kb setup
```

`kb setup` finds the sources on your computer and asks which to add, then which AI plug-in to use. It writes the
config file, offers to install the hooks, offers to read your sources every 15 minutes, and offers to read them now.
Every choice is in one file you can edit later. Run `kb paths` to see where it is.

```toml
machine = "studio"          # this computer's name in stored ids; set it once

[ai]
plugin = "chatgpt"          # kb ai list shows the choices

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
| `kb search "words"` | Searches everything. `--source`, `--after` and `--limit` narrow it. |
| `kb get ID` | Prints the stored text of a search hit, a page at a time. |
| `kb ingest` | Reads every source now, then summarizes new long items. |
| `kb stats` | Shows how many records each source has and which sources failed. |
| `kb rebuild` | Reads every source into a fresh knowledge base and puts it in place of this one once it checks out. |
| `kb schedule on`, `off`, `status` | Reads your sources in the background, with launchd on a Mac or a systemd user timer on Linux. |
| `kb hooks install`, `uninstall`, `status` | Adds or removes the hook in each agent found here. |
| `kb ai list`, `kb ai test` | Lists the AI plug-ins, or makes one tiny call and says which plug-in answered. |
| `kb setup`, `kb try` | Sets kb up, or shows it on a made-up month. |
| `kb login NAME` | Signs a Gmail source in with your own Google OAuth client. |

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

Add as many sources of one type as you like, each in its own `[sources.NAME]` table with `type = "..."`. The table's
name is what `kb search --source` takes.

Every source only reads. Mail and texts drop 2FA codes and password resets one message at a time, and every record
passes through a mask that replaces API keys, tokens and passwords with `[masked]` before it is stored.

To read another computer, name it and list it in a source's `hosts`. That computer needs only Python 3.9 and SSH. kb
sends itself over `ssh <host> python3 -`, and nothing is installed or left there.

```toml
[hosts.laptop]
ssh = "sam@laptop.local"

[sources.claude_code]
hosts = ["studio", "laptop"]
```

iMessage needs Full Disk Access for the Python that runs kb. The installer prints which one.

Gmail needs an app password, which needs 2-Step Verification on your Google account. `kb setup` saves the password
in a file only you can read. To use your own Google OAuth client instead, set `auth = "oauth"`, `client_id` and
`client_secret`, then run `kb login NAME`.

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

A CLI plug-in runs the program you already signed in to. kb never reads, relays or stores that login. Each call runs
in a throwaway folder with no tools and no saved session, without the variables that could send it to a paid key.
API plug-ins read their key only from the place the config names, never from whatever environment kb runs in.

A CLI starts a process for every call, so with a CLI plug-in the check waits up to 20 seconds instead of 10. An API
key is the fast path.

## Agent hooks

`kb hooks install` adds the hook to each agent it finds.

| Agent | How |
| --- | --- |
| Claude Code | a `UserPromptSubmit` hook in `~/.claude/settings.json` |
| Codex | a `UserPromptSubmit` hook in `~/.codex/hooks.json`, trusted in `~/.codex/config.toml` |
| Hermes | a `pre_llm_call` plugin in `~/.hermes/plugins/kb-context` |
| OpenCode | a `chat.message` plugin in `~/.config/opencode/plugins` |
| GitHub Copilot CLI | a `userPromptSubmitted` hook in `~/.copilot/config.json`, built from the format Copilot CLI 1.0.79 is reported to use and not yet run on a live install |

Cursor's documented prompt hook cannot add context, and Grok drops what a prompt hook prints, so neither gets a hook.
Grok runs Claude Code's hooks too, and kb's hook stays quiet there.

Each hook waits at most 30 seconds. When the check finds nothing that fits, or fails, the agent gets nothing.

These files are yours, so kb edits them carefully. Before it writes, it works out the new file and checks that the
file still reads back and that nothing but kb's own entry changed. If anything else would change, it writes nothing and
tells you how to make the change by hand. Every edit keeps a backup beside the file. In `~/.codex/config.toml` the check
also makes sure every comment on a line of its own survives. A comment at the end of one of your settings lines is kept
because kb never edits that line, but the check itself does not look at it.

## Privacy

Everything stays in one file on your computer. The only thing that leaves it is the AI call you chose: the plug-in
gets your message, the two turns before it, and the start of a few candidate records, and the summarizer sends long
items to it. With no plug-in, kb still searches but adds nothing to your prompts and writes no summaries.

Known limits:

- A secret typed into a file name in a form like "password: X" or "password is X" stays in the item's id. The file's
  text is still masked. A bearer token in a file name is caught only by a provider's shape, such as a GitHub token,
  since "Bearer" is a word in names like "Ring Bearer duties".
- A password of letters only, after "is" or after a flag and a space ("--password correcthorse"), reads as a word and
  is not masked, so "Use the --password flag" stays readable. With a digit or a symbol, or after "=" or ":", it is
  masked.
- In JSON written inside other JSON text, a password with an escaped quote in it is masked only up to that quote, and
  not at all when fewer than four characters come before it. In plain JSON, and in a tool's input that kb writes as
  JSON itself, the whole value is masked.
- After "Authorization:" or "Proxy-Authorization:", the credential is the word after a scheme such as Bearer, Token or
  AWS4-HMAC-SHA256. With no scheme, header style (the name at a line's start, after a space, a quote or an opening
  mark, not inside a path), the single token right after the colon is masked when it is shaped like a credential: 12
  or more characters with no whitespace, mixing letters and digits, not a real date and time, a time of day or a range
  of two as ISO 8601 or a log writes one ("2026-11-01T10:00:00Z", "20261101T100000Z", "2026-11-01T10:00:00+0000",
  "10:00:00.123456Z"), and holding either a random run of 10 or more characters between its separators or no plain
  word. So a random token there is masked, digits with a T or a Z among them included ("20261101T100000Z1234"), and
  "ISO-27001", "RFC7235-compliant", "WPA2-Enterprise" and "WindowsServer2022" stay.
- In escaped text, such as a tool's input stored as JSON, a named value reads on through "\n" into the next line's
  first word and masks it too: "password: <pw>\nnext line" becomes "password: [masked] line". That over-masking is
  kept on purpose, since what follows "\n" in the same run may be the rest of the secret.
- A secret is read through characters you cannot see, such as a zero-width space or a NUL, but not through a vertical
  tab, a form feed or the separators from 0x1c, which part words as a line break does. A key split by one of those is
  not masked whole. Such a character is read raw, escaped as JSON or Python writes it ("\u200b", "\x7f", the
  surrogate pair JSON writes for one past U+FFFF, "\udb40\udc01", and JSON's "\b" for the backspace between letters
  or digits), and escaped twice, as JSON inside JSON holds it.
- Provider tokens kb does not know by itself are masked by rules taken from gitleaks (kb/gitleaks.py, gitleaks v8.30.1,
  MIT licence), where gitleaks finds one in text, as a word of its own. An Airtable token is masked alone too, where
  gitleaks needs the provider's name near it; a Facebook token only with "facebook" in the text, as gitleaks reads it,
  since alone its shape is also a pipe-separated record's (a timestamp and a trace id). So with "facebook" anywhere in
  the text, such a record of 16 digits, "|" and 32 hex digits is masked too. An item's id is read more narrowly,
  without these rules.
  gitleaks reads Netlify's "nfp_" tokens only by the word "netlify" near a value and has no rule for Resend's "re_"
  tokens, so kb does not mask them by themselves.
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
- kb reads a secret that one colour code or one invisible character splits, or glues to a word, and still hides all
  that kb's earlier mask hid. Shapes built from more than that are not all read: a secret split by two kinds of
  invisible character at once ("pass<soft hyphen>word=..." with a link inside the value), what an older kb left
  glued to a placeholder ("[masked]<zero-width space>rest"), letters glued onto a key after a colour code
  ("AKIA...<ESC>[0mSuffix"), or a secret's name a colour code splits ("DB_<ESC>[31mPASS<ESC>[0mWORD=..."). Where
  kb's earlier mask hid such a secret, this one does too.
- Where kb reads a value by its shape alone (after "pass" or "pwd" next to a user's name, in an XML element not named
  for a password, under a secret's name in a two-item list, in a field with no label, or after a name a colour code
  splits), a code identifier ("ConvertBF16ToF32", "OAuth2AuthorizationCode") is no credential. So a password made of
  words and numbers ("Summer2024Garden") reads as an identifier there and stays, and an identifier with a digit run
  into small letters ("Utf8to16Codec") reads as a password and is masked.
- Masking costs time in proportion to the text, but a text in which kb's newer readings fire is masked a second time
  beside kb's earlier mask, so a long run of built shapes is slow: 4,000 soft-hyphen-split passwords with a link
  inside each (160 KB) take about 10 s, 30,000 command lines with a password each (900 KB) about 14 s, and 256 KB of
  nothing but Playwright fill calls with a password each about 8 s.
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
- A private key written as base64 (as a Kubernetes secret holds one) is masked from the line with its header's first
  byte through the line with its footer's. Its header and footer are each read as a line of their own past any
  horizontal space and quotes around them, written, escaped as JSON escapes them or as a web page writes a quote
  ("&quot;"), and up to any line break Python reads, so a key file indented in a document, or held in a JSON or .env
  file, encoded whole, is masked, and so is an SSH2 key file.
  A footer ends it only as a whole line with no other footer later in the
  same encoded stream, so text between a key's footer and a stray footer later in that stream is masked with the key,
  prose holding a control character included, and so is text between two whole keys in one stream. A footer encoded on
  its own in a later stream ends no key of an earlier stream's, so a public key between them stays. A footer encoded
  alone on a line of its own, narrower than the lines below it, is a stream of its own too, so a public key below it
  whose lines fill the width stays. In a row an older kb stored, encoded text after a key in its stream that does not
  decode from its line's start to words reads as an older kb's rest of a key after the key's placeholder, and is
  masked too. A header after text on its decoded line, as a sentence quoting one writes it, starts a key only with a
  footer later in its stream, and then the key starts on that line, with the words before the header and whatever lies
  between it and the footer. A header that starts a line below a shorter one, the last of another encoded stream,
  starts a stream of its own, so the words above it quote nothing and it starts its key, footer or none. A key file's
  header below it, past prose, starts the key instead, on a line of its own, or, where the text runs on into the file
  with no line break, after a byte order mark or where the line widths change; past no prose (a run a key's body could
  be, an armour header such as "Comment: ...", another header's text, a control character, or nothing), the key still
  starts on the quote's line. Cut off before its footer, it is masked while its base64 decodes to text, so a word on
  the next line that happens to decode to text goes with it; cut off before its header, the same holds for the line
  above. Base64 of a short label glued to a quoted header, where the label's own base64 is one word in one case as
  long as the block's lines ("ABC" written "QUJD" at width 4), reads as a heading over a key, so the quote after it is
  masked; a key there is masked either way. Any line break wraps it, as written or escaped, and it is read with JSON's
  escapes read once and twice too, as JSON and JSON in JSON write a key with any escape (a tab, \u000a, \/). A field
  name whose base64 ends in "=" before a value is read as main reads it, which leaves that value readable. After a
  placeholder, a key's body on the placeholder's own line followed by words ("[masked] MC4C... notes deploy") shows,
  and so does a fragment that is itself a plain word inside a line of words after a key. In a row an older kb stored,
  an OpenSSH private key's private section, re-encoded alone after a placeholder, reads as public material and shows.
  Lines that each start with the same quote or comment mark ("> ", "# ", "// ", "-- ", "; ", "* ", "| ", a quote
  character, written or escaped) are read as no block, so a key encoded in them shows, as on main. Whether the words before a header on its decoded line make a
  sentence that quotes it is read from the few dozen bytes before it: a byte no text holds there starts a key where
  the words alone would quote it, and a space further back is not seen, so a header after a long word starts its key.
  This masks more.
  A header or footer whose words before "PRIVATE KEY" run past 200 bytes, far past any a key tool writes, is read as
  none, so a key encoded with one shows, as on main.
- A piece of a private key's base64 with neither its header nor its footer shows, as on main, unless a key's rest
  after a placeholder, as one where a key was masked, takes it. A rest starts with base64 glued to the placeholder, or
  with a line right after it of base64 alone, 40 characters or more, starting as a key's body does ("MIIE..."); in a
  row an older kb stored, 36 characters or more with a capital, a small letter and a digit, "+" or "/" are enough, and
  lines there that decode to words are passed over first. Past them, where main's rest from the placeholder runs on,
  with its tail, it starts at the next line that is no word alone, placeholder alone, line of words or line with no
  letter or digit but a placeholder's ("---", "  [masked]"), or that holds anywhere in it a run of base64 a key's
  could be; the lines it passes over stay. It starts there whatever that line holds (base64 alone at any length, as a
  stream's short last line is, or any other line main's rest takes), and it runs on as main's does. Only that first
  line counts: a piece of 34 characters after "[masked] ", or of lines 16 wide after another key's footer, shows, even
  with a longer line further down. A rest that starts right at its placeholder runs on past lines of words to the last
  line of its paragraph holding a token, as main's does; one that starts past lines that decode to words, where main
  reads none, ends where its lines of base64 end, so a piece further down shows. As on main, a rest starts only past
  what main reads before a key's body: after a no-break or other wide space escaped in JSON, a backtick or "- ", or
  with JSON's escaped slash ("\/") in the first 40 characters of its first line, a body after a placeholder shows.
- After a placeholder where a key was masked, public material (a certificate, a public key) and lines of words in the
  same paragraph are masked with it where a line of base64 past them reads as a possible fragment of a key, base64 of
  words included ("bm90ZXMg..."); in JSON inside JSON, a blank line does not end that paragraph. Main keeps them only
  as it leaves a key written as base64 readable; after a key in plain text it masks them too. This masks more, and
  shows nothing.
- A knowledge base that an older kb wrote, before each row recorded how it was masked, does not open: an older kb's
  masking can leave a secret that no pattern finds afterwards. kb says so and names `kb rebuild`, which reads your
  sources into a fresh one, each as far back as its oldest item in the old one. When a source no longer has some of
  the old one's items, such as sessions an agent has since deleted, the rebuild stops, keeps the old one and says how
  many each source would lose; `kb rebuild --accept-loss` replaces it anyway and loses them for good.
- A command's password flag is read on a line the shell carries on with a continuation mark ("\" at a line's end,
  or "`" or "^" after a space), through a colour code anywhere in the line, inside the program's name too, and in a
  list or a tuple of the command's arguments, as an item's metadata, JSON or Python's repr holds one, however long.
  The command is read by its name, its Windows file ("keytool.exe", "aws.exe", "az.cmd"), a folder path or quotes,
  as written or in JSON escaped up to four times. Where keytool, jarsigner, az login, mongosh and its tools, twine or
  aws configure set has a password flag past shell grammar kb does not follow ("$'...'", "<(...)", an extended
  glob, an escaped quote, a substitution nested or escaped twice), kb masks from the value after that flag to the
  end of the line, or of the JSON string holding it, so the rest of that line, a pipe to another command included,
  is masked with it. A newline escaped inside "$'...'" ends that line early, and the password after it shows; so
  does a list item holding a flag and its value together ("-p ...") right after another option, whose value it may
  be.
- A list of a command's arguments that also holds a number of a thousand digits or more, with or without underscores
  between them, is not read as a list, since such a number takes long to read on Python 3.9. Its text is read
  instead, as kb read such lists before, so a password after a flag in it can show.
- A YAML list of secrets under a secret's name (`tokens:` followed by `- value` lines) is masked only where an item
  holds a provider's token shape.

## Why there are no embeddings

On a set of real messages from the author's own use, adding embeddings to the search (a local embedding model over a
year of summaries) moved the share of messages handled right from 90.5% to 91.2%. That gap is inside run-to-run noise:
three runs of the embeddings version scored 89.5%, 90.5% and 93.5%. Embeddings found no more useful records, so they
were dropped (October 2026). What moved the number was the second pass that adds nothing when nothing fits. A version
without it handled 51% of messages right, and with it 90%. So kb searches with SQLite's full-text index and spends the
model call on judging, not on vectors.

## Develop

```sh
python3 -m unittest discover -s tests -t .
```

The tests use temp folders only. `KB_HOME=/some/folder` puts the config, database, logs and cache in one folder.
`docs/architecture.md` maps the modules.

## License

MIT. See [LICENSE](LICENSE).
