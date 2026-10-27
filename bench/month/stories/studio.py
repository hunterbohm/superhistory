import cast
from cast import DOTFILES_REPO, GAIL, GH_ME, JULES, PINCH_REPO, SITE_REPO, WREN_NAME, WREN_PHONE
from model import Mail, Post, Q, Story, claude, codex, commit, email, note, texts, thread

HAL = "Hal Becker <hal@smallbiztechnight.example>"


def build():
    s = Story()
    s.add(claude("st-claude-agents", "09-06 15:20", 45, DOTFILES_REPO, "Shared AGENTS.md for all repos", """
U: I keep repeating the same instructions to claude and codex in every repo. write one AGENTS.md I can keep in dotfiles and symlink into each repo. rules: python repos use uv (uv run pytest, uv add), never pip install. ruff for lint+format. conventional commits. never commit .env or anything in secrets/. ask me before adding any new dependency. don't push to main on client repos, open a PR. keep it short
T Write: /Users/jules/dotfiles/agents/AGENTS.md
R: File created successfully
T Bash: cat ~/dotfiles/agents/AGENTS.md
R: # Working in Jules's repos
- Python: use uv. `uv run pytest`, `uv add <pkg>`. Never `pip install`.
- Lint and format with ruff before committing.
- Commit messages follow Conventional Commits (`fix: ...`, `feat: ...`).
- Never commit `.env` or anything under `secrets/`.
- Ask before adding a new dependency.
- Client repos: never push to main. Open a PR.
T Write: /Users/jules/dotfiles/bin/link-agents
R: File created successfully
T Bash: ~/dotfiles/bin/link-agents ~/code/copperline-docs ~/code/fernbrook-orders ~/code/harbor-reminders ~/code/marlowworks-site ~/code/pinch
R: linked copperline-docs/AGENTS.md -> ~/dotfiles/agents/AGENTS.md
linked fernbrook-orders/AGENTS.md -> ~/dotfiles/agents/AGENTS.md
linked harbor-reminders/AGENTS.md -> ~/dotfiles/agents/AGENTS.md
linked marlowworks-site/AGENTS.md -> ~/dotfiles/agents/AGENTS.md
linked pinch/AGENTS.md -> ~/dotfiles/agents/AGENTS.md
A: Done. The rules in `~/dotfiles/agents/AGENTS.md`:
1. Python: uv only (`uv run pytest`, `uv add`), never pip install.
2. Lint and format with ruff.
3. Conventional Commits.
4. Never commit `.env` or anything under `secrets/`.
5. Ask before adding a new dependency.
6. Client repos: never push to main, open a PR.

`~/dotfiles/agents/AGENTS.md` holds them, and `bin/link-agents` symlinks it into each repo you pass, so one edit updates all of them. Claude Code reads `CLAUDE.md`, so the script also links `CLAUDE.md` to the same file. Add `AGENTS.md` and `CLAUDE.md` to each client repo's `.gitignore` if you don't want the symlinks committed.
"""))

    s.add(commit("st-commit-agents", DOTFILES_REPO, "09-06 16:10", """agents: one AGENTS.md for every repo

bin/link-agents symlinks it (and CLAUDE.md) into the repos you pass.""",
                 ["A\tagents/AGENTS.md", "A\tbin/link-agents"]))

    s.add(codex("st-codex-analytics", "09-12 10:30", 35, SITE_REPO, """
U: take google analytics off the site and switch to plausible. then the cookie banner can go too
T exec_command: rg -n "gtag|googletagmanager|CookieBanner" src
R: src/layouts/Base.astro:14:    <script async src="https://www.googletagmanager.com/gtag/js?id=G-...">
src/layouts/Base.astro:22:    <CookieBanner />
src/components/CookieBanner.astro:1:---
T apply_patch: *** Begin Patch
*** Update File: src/layouts/Base.astro
@@
-    <script async src="https://www.googletagmanager.com/gtag/js?id=G-..."></script>
+    <script defer data-domain="marlowworks.dev" src="https://plausible.io/js/script.js"></script>
@@
-    <CookieBanner />
*** End Patch
R: Success. Updated the following files:
M src/layouts/Base.astro
T exec_command: git rm src/components/CookieBanner.astro && npm run build
R: rm 'src/components/CookieBanner.astro'
 build  12 page(s) built in 2.31s
A: Google Analytics is gone and Plausible's script is in the base layout. Plausible sets no cookies, so the cookie banner and its component are removed. The build passes with 12 pages.
"""))

    s.add(thread("st-gh-31", PINCH_REPO, 31, "issue", "Windows paths break redaction", "ptarmigan-dev", "09-13 18:40", """
On Windows, any log line with a path like `C:\\Users\\me\\app\\logs` makes pinch crash or redact the wrong text.
Looks like the backslashes end up inside a regex.

```
re.error: bad escape \\U at position 2
```

pinch 0.3.2, Python 3.12, Windows 11.
""", [
        Post(GH_ME, "09-14 09:12", "Thanks, reproduced. The user's own paths get turned into patterns without "
                                   "escaping. Will fix after 0.4.0 ships."),
        Post(GH_ME, "09-24 20:05", "Fixed in 0.4.1 (#33): paths are escaped with re.escape before they become "
                                   "patterns."),
    ], state="closed", closed="09-24 20:06", labels=("bug", "windows")))

    s.add(commit("st-commit-040", PINCH_REPO, "09-15 21:30", """release: 0.4.0

Adds --redact-emails, which replaces email addresses with <email>.""",
                 ["M\tCHANGELOG.md", "M\tpyproject.toml", "M\tsrc/pinch/cli.py", "M\tsrc/pinch/redact.py",
                  "A\ttests/test_redact_emails.py"]))

    s.add(thread("st-gh-33", PINCH_REPO, 33, "PR", "Escape Windows paths before building patterns", GH_ME,
                 "09-24 19:10", """
Fixes #31. Paths from the user's environment were joined into a regex unescaped, so backslashes in Windows paths
became escape sequences. Escape them with `re.escape`. Adds Windows path cases to the tests. Releases 0.4.1.
""", [Post("ptarmigan-dev", "09-24 19:55", "Works on my machine now, thanks!", "review approved")],
        state="merged", merged="09-24 20:04", branches="fix/windows-paths -> main"))

    s.add(commit("st-commit-041", PINCH_REPO, "09-24 20:04", """release: 0.4.1

Fixes Windows paths being read as regex escapes (#31).""",
                 ["M\tCHANGELOG.md", "M\tpyproject.toml", "M\tsrc/pinch/redact.py", "M\ttests/test_paths.py"]))

    s.add(email("st-taxes", "Q3 estimated taxes", [
        Mail(GAIL, JULES, "09-09 11:15", """Hi Jules,

Reminder that your Q3 estimated tax payments are due Tuesday, September 15. Based on your income through August:

- Federal: $4,100 (IRS Direct Pay)
- Colorado: $890 (Revenue Online)

Send me the confirmation numbers when they're done.

Gail Porter, CPA
Porter Books"""),
        Mail(JULES, GAIL, "09-14 20:40", """Hi Gail, paid both today. Confirmations attached.

Jules""", attachments=("IRS Direct Pay confirmation.pdf", "Colorado Revenue Online confirmation.pdf")),
    ]))

    s.add(note("st-rates", "Areas/Business/Rates.md", """# Rates

Last changed Sep 2026.

- Fixed-price projects for anything with a clear scope. 40 to 50% deposit, rest on delivery.
- Change requests and small jobs: **$150 an hour**.
- Day rate (workshops, on-site): **$1,100**.
- Retainer: **$1,500 a month** for up to 10 hours, unused hours don't roll over.
- Hosting + monitoring after launch: priced per client, covers my costs plus a bit.

Don't discount the hourly rate. Cut scope instead.
""", "09-05 08:40"))

    s.add(email("st-talk", "Speaking at Small Business Tech Night?", [
        Mail(HAL, JULES, "09-16 13:00", """Hi Jules,

I organize Small Business Tech Night, a monthly meetup for Denver shop owners who want practical tech. Benji from
Fernbrook told us about the text ordering you built. Would you give a short talk at our October 8 event? It's at
Larimer Hall, doors at 6pm, talks start at 6:30. Each talk is 15 minutes plus questions.

Hal Becker"""),
        Mail(JULES, HAL, "09-16 15:45", """Hi Hal, I'd love to. Title: "Text ordering for a three-store bakery".
I'll check with Dana that she's happy for me to use Fernbrook's numbers.

Jules"""),
        Mail(HAL, JULES, "09-17 09:20", "Great, you're on first at 6:30 on Thursday October 8. Hal"),
    ]))

    s.add(texts("st-texts-wren", WREN_NAME, WREN_PHONE, "09-25", """
09:55 Jules: Hey! Fernbrook might want a wholesale portal (cafés ordering weekly). Would you be up for the design if it goes ahead?
10:20 Wren Castillo: ooh yes. I'm booked until mid October though. earliest start is Monday Oct 19
10:21 Wren Castillo: rate is $95/hr, I'd guess ~20 hours for something like that incl. a round of revisions
10:30 Jules: Perfect, I'll tell Dana. Thanks!
"""))

    s.add(claude("st-claude-casestudy", "09-29 14:00", 70, SITE_REPO, "Fernbrook case study page", """
U: dana said I can use fernbrook's name. write a case study page for the site at /work/fernbrook. numbers from my note: 212 text orders the first week, 4% went to a person, launched on sep 19 for the fall menu. quote from benji: "the kitchen sheet is a lifesaver". plain, short, no hype
T Read: /Users/jules/code/marlowworks-site/src/content/work/_template.md
R: ---
title:
client:
summary:
published:
---
T Write: /Users/jules/code/marlowworks-site/src/content/work/fernbrook.md
R: File created successfully
T Bash: npm run build && npm run preview -- --port 4321 & sleep 3 && curl -s localhost:4321/work/fernbrook | grep -o "<h1>[^<]*"
R: build  13 page(s) built in 2.40s
<h1>Text ordering for a three-store bakery
A: The page is at `src/content/work/fernbrook.md` and builds at /work/fernbrook. It's under 400 words: the problem (orders by phone and Instagram DMs), what was built, launch on September 19, the first week's 212 orders with 4% needing a person, and Benji's quote. I left out prices.
U: ship it tomorrow morning
"""))

    s.add(commit("st-commit-casestudy", SITE_REPO, "09-30 08:45", """work: Fernbrook case study

Published at /work/fernbrook with Dana's OK.""",
                 ["A\tsrc/content/work/fernbrook.md", "M\tsrc/pages/work/index.astro"]))

    s.ask(
        Q("st-01", "single", "What do my agent instructions say about adding a new dependency?",
          "Ask Jules before adding any new dependency.", ("st-claude-agents",),
          evidence_has=("Ask before adding a new dependency",)),
        Q("st-02", "single", "Which commit message style do my repos use?",
          "Conventional Commits (fix: ..., feat: ...).", ("st-claude-agents",), evidence_has=("Conventional Commits",)),
        Q("st-03", "single", "Where does my shared AGENTS.md live, and how does it get into each repo?",
          "~/dotfiles/agents/AGENTS.md; bin/link-agents symlinks it (and CLAUDE.md) into each repo.",
          ("st-claude-agents", "st-commit-agents"), evidence_has=("dotfiles/agents/AGENTS.md", "link-agents")),
        Q("st-04", "single", "Can agents push straight to main on my client repos?",
          "No. On client repos they must open a PR instead.", ("st-claude-agents",), evidence_has=("never push to main",)),
        Q("st-05", "single", "What did pinch 0.4.0 add, and when was it released?",
          "The --redact-emails flag, which replaces email addresses with <email>; released the evening of "
          "September 15, Denver time (the commit is stamped early September 16 UTC).",
          ("st-commit-040",), evidence_has=("--redact-emails",)),
        Q("st-06", "single", "Which pinch version fixed Windows paths, and what was the bug?",
          "0.4.1, released the evening of September 24, Denver time (stamped early September 25 UTC): backslashes "
          "in Windows paths were joined into a regex unescaped and read as escape sequences; they're now escaped "
          "with re.escape.", ("st-gh-31", "st-gh-33", "st-commit-041"),
          evidence_has=("0.4.1", "re.escape")),
        Q("st-07", "single", "Who reported the Windows path bug in pinch?",
          "GitHub user ptarmigan-dev, in issue #31.", ("st-gh-31",), evidence_has=("ptarmigan-dev",)),
        Q("st-08", "single", "How much were my Q3 estimated tax payments?",
          "$4,100 federal and $890 to Colorado.", ("st-taxes",), evidence_has=("$4,100", "$890")),
        Q("st-09", "temporal", "When did I pay my Q3 estimated taxes, and when were they due?",
          "Paid on September 14 (the \"paid both today\" reply to Gail went out that evening, Denver time, stamped "
          "September 15 UTC), a day before the September 15 due date.", ("st-taxes",),
          evidence_has=("September 15", "paid both today")),
        Q("st-10", "single", "What's my hourly rate for change requests?",
          "$150 an hour.", ("st-rates",), evidence_has=("$150 an hour",)),
        Q("st-11", "single", "What's my day rate for workshops?",
          "$1,100.", ("st-rates",), evidence_has=("$1,100",)),
        Q("st-12", "single", "What does my monthly retainer cost and include?",
          "$1,500 a month for up to 10 hours; unused hours don't roll over.", ("st-rates",),
          evidence_has=("$1,500 a month",)),
        Q("st-13", "single", "When was the Fernbrook case study published on my site, and at what path?",
          "Yes, published on September 30 at /work/fernbrook.", ("st-claude-casestudy", "st-commit-casestudy"),
          evidence_has=("/work/fernbrook",)),
        Q("st-14", "single", "Which analytics does my website use now?",
          "Plausible. Google Analytics was removed on September 12, along with the cookie banner.",
          ("st-codex-analytics",), evidence_has=("plausible", "cookie banner")),
        Q("st-15", "single", "When and where is my Small Business Tech Night talk?",
          "Thursday October 8 at Larimer Hall; I'm on first at 6:30pm, 15 minutes plus questions.",
          ("st-talk",), evidence_has=("October 8", "Larimer Hall", "6:30")),
        Q("st-16", "single", "What's the title of my Small Business Tech Night talk?",
          "\"Text ordering for a three-store bakery\".", ("st-talk",),
          evidence_has=("Text ordering for a three-store bakery",)),
        Q("st-17", "single", "When could Wren start designing Fernbrook's wholesale portal, and what would it cost?",
          "Monday October 19 at the earliest; $95 an hour, about 20 hours including a round of revisions.",
          ("st-texts-wren",), evidence_has=("Oct 19", "$95/hr", "20 hours")),
        Q("st-18", "multi", "Which client invoices were paid in September?",
          "All four: MW-1038 (Fernbrook deposit, Sep 4), MW-1039 (Copperline deposit, Sep 17), MW-1040 (Harbor "
          "Lane deposit, check that arrived the week of Sep 21) and MW-1041 (Fernbrook final, Sep 29).",
          ("fern-deposit", "cop-paid", "har-golive", "fern-final", "har-proposal"),
          evidence_has=("MW-1038", "MW-1039", "MW-1040", "MW-1041")),
        Q("st-19", "multi", "How much did clients pay me in total in September?",
          "$10,850: $1,920 (Fernbrook deposit) + $3,250 (Copperline deposit) + $2,800 (Harbor Lane deposit) + "
          "$2,880 (Fernbrook final).", ("fern-deposit", "cop-paid", "har-proposal", "har-golive", "fern-final"),
          evidence_has=("$1,920", "$3,250", "$2,800", "$2,880")),
        Q("st-20", "multi", "What client dates do I have in the first half of October?",
          "Copperline phase 1 demo Friday Oct 2 at 10am; Copperline phase 2 starts Oct 5; the Tech Night talk "
          "Thursday Oct 8 at 6:30pm; Quill & Ledger starts Monday Oct 12; Harbor Lane goes live Tuesday Oct 13.",
          ("cop-demo-move", "cop-phase2-quote", "st-talk", "ql-scope-cut", "har-golive"),
          evidence_has=("October 2", "October 5", "October 8", "October 12", "October 13")),
    )
    return s
