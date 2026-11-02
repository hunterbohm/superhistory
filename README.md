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
- After "Authorization:" or "Proxy-Authorization:", the credential is the word after a scheme such as Bearer, Token
  or AWS4-HMAC-SHA256. With no scheme, header style (the name at a line's start, after a space, a quote or an
  opening mark, not inside a path), the single token right after the colon is masked when it is shaped like a
  credential: 12 or more characters with no whitespace, mixing letters and digits, not a number, a date or a time,
  and holding either a random run of 10 or more characters between its separators or no plain word. So a random
  token there is masked, and "ISO-27001", "RFC7235-compliant", "WPA2-Enterprise" and "WindowsServer2022" stay.
- In escaped text, such as a tool's input stored as JSON, a named value reads on through "\n" into the next line's
  first word and masks it too: "password: <pw>\nnext line" becomes "password: [masked] line". That over-masking is
  kept on purpose, since what follows "\n" in the same run may be the rest of the secret.
- A secret is read through characters you cannot see, such as a zero-width space or a NUL, but not through a vertical
  tab, a form feed or the separators from 0x1c, which part words as a line break does. A key split by one of those is
  not masked whole.
- A token right after a terminal color code, and a private key written percent-encoded or as base64 (as a Kubernetes
  secret holds one), are not masked.
- A few rare file names are refused as if they held a secret, such as one that names a command's password flag with a
  value ("redis-cli -a auth2.md") or reads as a field ("Cookie: chocolate chip.md").
- A private key is masked by its structure: from its header through its footer, or, if the footer is missing, through
  the end of its paragraph. So whatever follows a key cut off before its footer, up to the next blank line, is masked
  with it, and so is the paragraph before a footer with no header.
- A knowledge base that an older kb wrote, before each row recorded how it was masked, does not open: an older kb's
  masking can leave a secret that no pattern finds afterwards. kb says so and names `kb rebuild`, which reads your
  sources into a fresh one, each as far back as its oldest item in the old one. When a source no longer has some of
  the old one's items, such as sessions an agent has since deleted, the rebuild stops, keeps the old one and says how
  many each source would lose; `kb rebuild --accept-loss` replaces it anyway and loses them for good.
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
