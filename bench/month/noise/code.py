import hashlib

import cast
from model import Story, Post, commit, thread

ME = cast.GH_ME
CL, FB, HR = cast.COPPERLINE_REPO, cast.FERNBROOK_REPO, cast.HARBOR_REPO
SITE, PINCH, DOT = cast.SITE_REPO, cast.PINCH_REPO, cast.DOTFILES_REPO

CLP, FBP, HRP = "src/copperline", "src/fernbrook", "src/harbor"

# (repo, day, [(time, subject, "status path; status path", optional body)])
PLAN = [
    (FB, "09-01", [
        ("09:12", "chore: add pre-commit with ruff and mypy",
         "A .pre-commit-config.yaml; M pyproject.toml",
         "Hooks run ruff (lint and format) and mypy on staged files.\n"
         "CI runs the same checks, so a skipped hook does not slip through."),
        ("09:48", "style: apply ruff format across the package",
         f"M {FBP}/app.py; M {FBP}/sms.py; M {FBP}/parse.py; M {FBP}/sheet.py; M tests/test_parse.py"),
        ("10:31", "fix: clear ruff lint findings",
         f"M {FBP}/sheet.py; M {FBP}/sms.py",
         "Unused imports, one bare except in sheet.py and a mutable default\nargument in sms.py."),
        ("14:05", "refactor(parse): separate prompt building from response handling",
         f"M {FBP}/parse.py; M tests/test_parse.py"),
        ("16:40", "test: share a client fixture and cover empty and non-text payloads",
         "M tests/test_app.py; A tests/conftest.py"),
    ]),
    (CL, "09-02", [
        ("08:50", "chore: add ruff config and fix import order",
         f"M pyproject.toml; M {CLP}/cli.py; M {CLP}/extract/pdf.py"),
        ("09:36", "test: add generated sample pdfs as fixtures",
         "A tests/conftest.py; A tests/make_fixtures.py; A tests/fixtures/README.md",
         "Fixtures are synthetic documents built by make_fixtures.py. Nothing\nfrom a real shipment is committed."),
        ("11:14", "refactor(extract): pull page iteration out of pdf.py",
         f"M {CLP}/extract/pdf.py; A {CLP}/extract/pages.py; M tests/test_pdf.py",
         "pdf.py was mixing file handling with the per-page loop."),
        ("14:20", "feat(cli): add --verbose flag and a logging config file",
         f"M {CLP}/cli.py; A config/logging.toml",
         "-v switches the root logger to DEBUG. The default stays at INFO."),
        ("16:45", "test(cli): smoke tests for --help and a missing input path",
         "M tests/test_cli.py"),
    ]),
    (SITE, "09-03", [
        ("09:22", "fix: broken anchor link in the footer", "M src/components/Footer.astro"),
        ("09:51", "content: fix typos on the services page", "M src/pages/services.astro"),
        ("16:18", "chore: set site url in astro config for sitemap output", "M astro.config.mjs",
         "Sitemap entries were relative before."),
    ]),
    (HR, "09-03", [
        ("13:12", "chore: add ruff and mypy to dev dependencies", "M pyproject.toml"),
        ("13:50", "style: ruff format",
         f"M {HRP}/importer.py; M {HRP}/schedule.py; M {HRP}/send.py"),
        ("14:31", "refactor(importer): parse rows into a dataclass",
         f"M {HRP}/importer.py; M tests/test_importer.py",
         "Callers get attribute access instead of dict keys, which mypy can check."),
        ("15:44", "test(importer): fixture export with quoted commas and blank lines",
         "M tests/test_importer.py; A tests/conftest.py; A tests/fixtures/sample_export.csv",
         "All names and numbers in the fixture are made up."),
        ("17:08", "docs: README section on running the import locally", "M README.md"),
    ]),
    (SITE, "09-04", [
        ("08:41", "feat: show reading time on blog post cards",
         "A src/utils/readingTime.ts; M src/components/PostCard.astro",
         "Counts words at 200 per minute, rounded up."),
        ("09:30", "test: add vitest and a test for readingTime",
         "A src/utils/readingTime.test.ts; M package.json; M package-lock.json"),
        ("10:55", "style: tighten spacing in the post list on mobile",
         "M src/styles/global.css; M src/components/PostCard.astro"),
        ("13:40", "blog: start a draft on idempotent webhooks",
         "A src/content/blog/idempotent-webhooks.md",
         "draft: true for now, so it stays out of the build output."),
        ("16:02", "fix: og image path in BaseHead", "M src/components/BaseHead.astro"),
    ]),
    (PINCH, "09-05", [
        ("10:14", "feat(trim): collapse runs of identical lines with a repeat count",
         "M src/pinch/trim.py; M tests/test_trim.py; M CHANGELOG.md",
         "A run of identical lines becomes the first line plus a marker such as\n"
         "'[previous line repeated 38 times]'. Lines that differ at all are kept."),
        ("10:52", "fix(trim): keep the last line of a collapsed run",
         "M src/pinch/trim.py; M tests/test_trim.py",
         "The marker needs a line of context on both sides."),
        ("11:40", "docs: README example for repeated-line collapsing", "M README.md"),
    ]),
    (DOT, "09-06", [
        ("20:12", "zsh: add fzf history search binding", "M zsh/.zshrc"),
        ("20:40", "zsh: move aliases to their own file", "A zsh/aliases.zsh; M zsh/.zshrc"),
        ("21:05", "git: add alias for a compact log", "M git/.gitconfig"),
    ]),
    (DOT, "09-07", [
        ("11:02", "tmux: reduce escape time and enable mouse", "M tmux/.tmux.conf",
         "Escape delay was making vim feel laggy inside tmux."),
        ("11:34", "nvim: switch statusline plugin", "M nvim/lua/plugins.lua"),
        ("12:20", "brew: add jq and ripgrep to the Brewfile", "M Brewfile"),
    ]),
    (HR, "09-08", [
        ("09:05", "feat(send): dry-run mode that logs messages instead of sending",
         f"M {HRP}/send.py; M tests/test_send.py",
         "Useful for checking a batch before real sends are switched on."),
        ("10:22", "fix(send): raise a clear error when credentials are missing", f"M {HRP}/send.py"),
        ("13:47", "refactor(send): inject the client so tests need no network",
         f"M {HRP}/send.py; M tests/test_send.py; M tests/conftest.py"),
        ("16:15", "feat(send): retry transient provider errors with backoff",
         f"M {HRP}/send.py; M tests/test_send.py",
         "Three attempts, doubling the delay each time. 4xx responses fail fast."),
    ]),
    (CL, "09-09", [
        ("10:05", "fix(extract): close pdf handles after each document",
         f"M {CLP}/extract/pdf.py; M tests/test_pdf.py",
         "Long batch runs were holding file descriptors until exit."),
        ("11:40", "refactor(export): split row building from the csv writer",
         f"M {CLP}/export/csv_writer.py; A {CLP}/export/rows.py"),
        ("14:15", "test(export): round-trip test for the csv writer", "A tests/test_csv_writer.py"),
        ("16:30", "chore: add pytest-cov and run it in CI", "M pyproject.toml; M .github/workflows/ci.yml",
         "Coverage is reported but not enforced yet."),
    ]),
    (PINCH, "09-09", [
        ("18:05", "refactor(redact): compile patterns once at import time",
         "M src/pinch/redact.py; M src/pinch/patterns.py"),
        ("18:40", "feat(redact): mask bearer tokens in Authorization headers",
         "M src/pinch/patterns.py; M tests/test_redact.py; M CHANGELOG.md",
         "Matches 'Authorization: Bearer ...' and swaps the token for a placeholder."),
        ("19:12", "test(redact): parametrize token pattern cases", "M tests/test_redact.py"),
        ("19:48", "chore: add ruff rule set and fix findings",
         "M pyproject.toml; M src/pinch/cli.py; M src/pinch/trim.py"),
    ]),
    (FB, "09-10", [
        ("11:20", "feat(sheet): retry sheet writes on 429",
         f"M {FBP}/sheet.py; A tests/test_sheet.py",
         "Exponential backoff with jitter, five attempts, then raise. Other API\nerrors are not retried."),
        ("11:58", "test(sheet): fake client that returns rate-limit errors",
         "M tests/test_sheet.py; M tests/conftest.py"),
        ("15:12", "docs: document local setup and environment variables",
         "M README.md; A .env.example",
         "Covers running the webhook locally behind a tunnel and where the\nsheet credentials file goes."),
    ]),
    (SITE, "09-11", [
        ("09:12", "feat: tag pages for the blog",
         "A src/pages/blog/tags/[tag].astro; M src/content/config.ts",
         "Adds a tags array to the blog schema and one page per tag."),
        ("10:08", "feat: show tags on post cards", "M src/components/PostCard.astro"),
        ("11:35", "fix: sort posts by date on tag pages", "M src/pages/blog/tags/[tag].astro"),
        ("14:50", "content: add tags to existing posts",
         "M src/content/blog/boring-stacks.md; M src/content/blog/trimming-logs-for-chat.md; "
         "M src/content/blog/sheets-quotas.md; M src/content/blog/idempotent-webhooks.md"),
        ("16:44", "chore: lint with eslint-plugin-astro",
         "A eslint.config.js; M package.json; M package-lock.json"),
    ]),
    (PINCH, "09-12", [
        ("09:42", "fix(cli): exit quietly on broken pipe", "M src/pinch/cli.py; M tests/test_cli.py",
         "Piping into head used to end with a BrokenPipeError traceback."),
        ("10:25", "test(cli): cover broken pipe and empty input", "M tests/test_cli.py"),
        ("11:30", "chore: add py.typed and annotate cli", "A src/pinch/py.typed; M src/pinch/cli.py"),
        ("14:06", "ci: test on python 3.11, 3.12 and 3.13", "M .github/workflows/ci.yml"),
    ]),
    (DOT, "09-13", [
        ("16:15", "install.sh: skip steps that already ran", "M install.sh",
         "Re-running the script on a configured machine now exits quickly\ninstead of reinstalling everything."),
        ("16:47", "readme: list what install.sh sets up", "M README.md"),
    ]),
    (HR, "09-14", [
        ("08:44", "ci: only run the workflow for src, tests and pyproject changes", "M .github/workflows/ci.yml"),
        ("09:30", "refactor(types): annotate the schedule module", f"M {HRP}/schedule.py"),
        ("11:05", "refactor(schedule): make selection a pure function",
         f"M {HRP}/schedule.py; M tests/test_schedule.py",
         "Takes the current time as an argument so tests do not need to freeze\nthe clock."),
        ("14:18", "test(schedule): add hypothesis tests for the selection function",
         "M tests/test_schedule.py; M pyproject.toml"),
        ("16:52", "chore: pin dev tool versions", "M pyproject.toml"),
    ]),
    (FB, "09-15", [
        ("08:55", "ci: run mypy in the workflow", "M .github/workflows/ci.yml"),
        ("09:30", "refactor(types): annotate request handlers in app.py and sms.py",
         f"M {FBP}/app.py; M {FBP}/sms.py",
         "No behavior change. Return types on the FastAPI routes are explicit now."),
        ("10:44", "refactor(types): add models module for parsed orders",
         f"A {FBP}/models.py; M {FBP}/parse.py; M tests/test_parse.py"),
        ("13:02", "refactor(app): move configuration into settings.py",
         f"A {FBP}/settings.py; M {FBP}/app.py; M {FBP}/sheet.py; M tests/conftest.py",
         "Reads the environment once at startup instead of inside each module."),
        ("14:36", "fix(types): satisfy mypy for optional webhook fields", f"M {FBP}/app.py"),
        ("17:15", "ci: cache dependencies between runs", "M .github/workflows/ci.yml"),
    ]),
    (CL, "09-16", [
        ("08:35", "refactor(types): add py.typed and annotate the extract package",
         f"A {CLP}/py.typed; M {CLP}/extract/pdf.py; M {CLP}/extract/fields.py; M {CLP}/extract/tables.py",
         "Library code first. cli.py and the tests come later."),
        ("09:20", "refactor(types): annotate the export package",
         f"M {CLP}/export/csv_writer.py; M {CLP}/export/rows.py"),
        ("10:48", "chore: turn on strict mypy for the export package", "M pyproject.toml"),
        ("13:10", "fix(types): narrow optional page text before splitting lines", f"M {CLP}/extract/pages.py"),
        ("15:33", "feat(cli): --output-dir option that creates the directory",
         f"M {CLP}/cli.py; M tests/test_cli.py",
         "Fails with a clear message if the path exists and is a file."),
        ("17:50", "docs: usage examples for the extract command", "M README.md"),
    ]),
    (FB, "09-17", [
        ("09:18", "feat(app): add request ids to log lines",
         f"A {FBP}/log.py; M {FBP}/app.py",
         "A middleware tags each log line with a request id, so one webhook\n"
         "call can be followed through parse and the sheet write."),
        ("10:02", "chore(sms): log provider status codes at warning level on failures", f"M {FBP}/sms.py"),
        ("13:40", "test: assert the request id is present on error logs", "M tests/test_app.py"),
        ("16:20", "fix(log): stop logging full message bodies", f"M {FBP}/app.py; M {FBP}/log.py",
         "Log the length instead. Bodies can contain customer names and phone\nnumbers and do not belong in log files."),
    ]),
    (SITE, "09-18", [
        ("10:02", "a11y: add a skip link and visible focus styles",
         "M src/layouts/Base.astro; M src/styles/global.css"),
        ("11:15", "a11y: label the nav landmark and mark the current page", "M src/components/Header.astro",
         "aria-current on the active link."),
        ("14:08", "fix: raise contrast on muted text", "M src/styles/global.css",
         "Muted text was below 4.5:1 against the background."),
        ("15:36", "perf: preload the primary font", "M src/components/BaseHead.astro"),
    ]),
    (PINCH, "09-19", [
        ("09:30", "fix(cli): replace undecodable bytes instead of aborting",
         "M src/pinch/cli.py; M tests/test_cli.py",
         "Fixes #22. A stray invalid byte in a log used to end the whole run\nwith a UnicodeDecodeError."),
        ("09:58", "test(cli): invalid utf-8 bytes are replaced in the output", "M tests/test_cli.py"),
        ("10:40", "docs: changelog entry for the decoding fix", "M CHANGELOG.md"),
        ("11:55", "refactor(trim): split line scoring into small functions", "M src/pinch/trim.py",
         "No behavior change. Each scoring rule is its own function now."),
        ("13:20", "test(trim): edge cases for single-line input", "M tests/test_trim.py"),
    ]),
    (DOT, "09-20", [
        ("19:30", "nvim: enable pyright for python files", "M nvim/init.lua; M nvim/lua/plugins.lua"),
        ("19:58", "git: ignore .DS_Store globally", "M git/ignore"),
    ]),
    (SITE, "09-21", [
        ("10:20", "feat: case study layout with a summary sidebar",
         "M src/pages/work/[slug].astro; A src/components/CaseSummary.astro"),
        ("11:02", "content: frontmatter schema for case studies", "M src/content/config.ts"),
        ("11:48", "docs: note how to add a case study in the README", "M README.md",
         "Frontmatter fields, image folder and how the summary sidebar is filled."),
        ("14:30", "fix: case study images overflow on narrow screens", "M src/styles/global.css"),
    ]),
    (HR, "09-21", [
        ("15:20", "feat(importer): log a summary line with row counts",
         f"M {HRP}/importer.py; M tests/test_importer.py"),
        ("15:58", "fix(importer): close the csv file on parse errors",
         f"M {HRP}/importer.py; M tests/test_importer.py"),
        ("17:02", "chore: add a small logging config module", f"A {HRP}/log.py",
         "One format for console and file output."),
    ]),
    (CL, "09-22", [
        ("09:55", "refactor(cli): use logging instead of print for progress",
         f"M {CLP}/cli.py; M tests/test_cli.py"),
        ("11:32", "feat(extract): per-document timeout",
         f"M {CLP}/extract/pdf.py; M config/default.toml",
         "Raises a DocumentTimeout error that the caller can catch."),
        ("15:08", "test: parametrize pdf tests over the fixture set", "M tests/test_pdf.py"),
    ]),
    (FB, "09-22", [
        ("09:41", "refactor(sms): extract send helper", f"M {FBP}/sms.py; M tests/test_sms.py"),
        ("10:15", "feat(sms): configurable request timeout", f"M {FBP}/sms.py; M {FBP}/settings.py",
         "Defaults to ten seconds. Set SMS_TIMEOUT_SECONDS to override."),
        ("11:03", "fix(app): return 422 for malformed webhook bodies", f"M {FBP}/app.py; M tests/test_app.py"),
        ("14:27", "test(parse): table-driven cases for blank and very long messages", "M tests/test_parse.py"),
        ("16:02", "docs: add a short architecture note to the README", "M README.md",
         "Webhook in, parse, sheet write, reply out. One paragraph per module."),
    ]),
    (HR, "09-23", [
        ("08:50", "feat: add __main__ entry point for the nightly job",
         f"A {HRP}/__main__.py; M pyproject.toml"),
        ("09:34", "feat(log): structured log lines with a run id", f"M {HRP}/log.py; M {HRP}/__main__.py",
         "Every line from one run shares an id, which makes grepping a night's\noutput easier."),
        ("10:40", "test: smoke test for the entry point against the sample export",
         "A tests/test_main.py; M tests/conftest.py"),
        ("13:05", "fix(send): do not log full phone numbers", f"M {HRP}/send.py; M tests/test_send.py",
         "Mask everything except the last two digits in log lines."),
        ("15:22", "refactor(types): fix mypy errors in importer", f"M {HRP}/importer.py"),
        ("17:40", "docs: add a troubleshooting section to the README", "M README.md",
         "How to read the run log and what the exit codes mean."),
    ]),
    (CL, "09-24", [
        ("08:58", "ci: split lint and test into separate jobs", "M .github/workflows/ci.yml"),
        ("09:42", "ci: test on python 3.11 and 3.12", "M .github/workflows/ci.yml"),
        ("11:20", "chore: add Makefile targets for lint, test and run", "A Makefile",
         "make check runs ruff, mypy and pytest in that order."),
        ("14:03", "fix(cli): include the file name in extraction errors",
         f"M {CLP}/cli.py; M {CLP}/extract/pdf.py; M tests/test_cli.py",
         "The old message only said extraction failed, with no path."),
        ("16:25", "chore: raise the pdfplumber lower bound", "M pyproject.toml"),
    ]),
    (SITE, "09-25", [
        ("08:55", "fix: rss feed items missing descriptions", "M src/pages/rss.xml.js"),
        ("09:40", "fix: rss item links use the site url", "M src/pages/rss.xml.js; M astro.config.mjs"),
        ("10:35", "ci: check links after the build and open an issue on failure",
         "A scripts/check-links.mjs; M package.json; M .github/workflows/ci.yml",
         "Runs on pushes to main only. The workflow opens the issue with gh and\n"
         "skips it if a link-check issue is already open."),
        ("11:50", "fix(ci): pin the node version in the workflow", "M .github/workflows/ci.yml"),
        ("13:30", "fix: replace a dead external link in a blog post", "M src/content/blog/boring-stacks.md",
         "Found by the new link check. The article moved, so this points at the\narchived copy."),
        ("16:15", "chore: add .nvmrc matching the CI node version", "A .nvmrc"),
    ]),
    (PINCH, "09-26", [
        ("10:08", "docs: FAQ entry on piping container logs", "M README.md",
         "Answers #25. docker logs writes the container's stderr to stderr, so\nthe pipe needs 2>&1."),
        ("10:44", "chore: tidy gitignore", "M .gitignore"),
    ]),
    (DOT, "09-27", [
        ("11:12", "zsh: lazy-load nvm to speed up shell startup", "M zsh/.zshrc",
         "Startup went from about 0.6s to 0.2s."),
        ("11:40", "tmux: set copy-mode keys", "M tmux/.tmux.conf"),
    ]),
    (PINCH, "09-27", [
        ("14:02", "chore: pin dev dependencies", "M pyproject.toml"),
        ("14:40", "refactor(cli): extract argument parser into build_parser",
         "M src/pinch/cli.py; M tests/test_cli.py"),
        ("15:25", "test(cli): argument parsing tests via build_parser", "M tests/test_cli.py"),
        ("16:10", "style: ruff format",
         "M src/pinch/cli.py; M src/pinch/redact.py; M src/pinch/trim.py; M tests/test_cli.py"),
    ]),
    (FB, "09-28", [
        ("08:40", "chore: pin ruff and mypy in the dev group", "M pyproject.toml"),
        ("09:25", "fix(sheet): close the client session on shutdown", f"M {FBP}/sheet.py; M {FBP}/app.py"),
        ("11:10", "refactor(sheet): one place for A1 range helpers", f"M {FBP}/sheet.py; M tests/test_sheet.py",
         "Range strings were being built in three places."),
        ("13:55", "test: cover app startup and shutdown hooks", "M tests/test_app.py"),
        ("15:48", "chore: remove unused imports left over from the refactor", f"M {FBP}/parse.py; M {FBP}/sheet.py"),
    ]),
    (SITE, "09-28", [
        ("15:44", "blog: copy edits on the idempotent webhooks draft", "M src/content/blog/idempotent-webhooks.md"),
        ("16:20", "content: update alt text for the about page photo", "M src/pages/about.astro"),
        ("17:05", "fix: wrong canonical url on paginated blog pages", "M src/components/BaseHead.astro",
         "Page 2 and up pointed at the first page."),
    ]),
    (CL, "09-29", [
        ("09:10", "refactor(extract): dataclass for extraction results",
         f"M {CLP}/extract/fields.py; M {CLP}/export/rows.py",
         "Replaces the loose dicts passed between extract and export. Fields\nare unchanged."),
        ("10:26", "test(extract): cover result dataclass defaults", "M tests/test_fields.py"),
        ("12:40", "style: ruff format",
         f"M {CLP}/extract/fields.py; M {CLP}/export/rows.py; M tests/test_fields.py"),
        ("14:55", "docs: contributing notes for running the tests locally", "A CONTRIBUTING.md"),
        ("17:10", "chore: remove unused keys from the default config", "M config/default.toml"),
    ]),
    (HR, "09-30", [
        ("09:12", "chore: turn on import sorting in the ruff config", "M pyproject.toml"),
        ("09:50", "style: sort imports",
         f"M {HRP}/__main__.py; M {HRP}/importer.py; M {HRP}/log.py; M {HRP}/schedule.py; M {HRP}/send.py"),
        ("11:38", "refactor(schedule): rename helper functions for clarity",
         f"M {HRP}/schedule.py; M tests/test_schedule.py",
         "No behavior change. Names now say what each helper returns."),
        ("14:20", "test(importer): cover an empty export file", "M tests/test_importer.py"),
        ("16:05", "ci: upload the coverage report as an artifact", "M .github/workflows/ci.yml"),
    ]),
]

COMPARE = {"pip": "pip", "npm": "npm_and_yarn", "actions": "github_actions"}


def files(spec):
    return ["\t".join(part.split()) for part in spec.split(";")]


def bump_body(name, url, old, new, eco):
    sha = hashlib.sha1(f"{name}:{new}".encode()).hexdigest()
    tag = (lambda v: f"v{v}") if eco == "actions" else (lambda v: v)
    return f"""Bumps [{name}]({url}) from {old} to {new}.
<details>
<summary>Commits</summary>
<ul>
<li><a href="{url}/commit/{sha}"><code>{sha[:7]}</code></a> Release {new}</li>
<li>See full diff in <a href="{url}/compare/{tag(old)}...{tag(new)}">compare view</a></li>
</ul>
</details>
<br />

[![Dependabot compatibility score](https://dependabot-badges.githubapp.com/badges/compatibility_score?dependency-name={name}&package-manager={COMPARE[eco]}&previous-version={old}&new-version={new})](https://docs.github.com/en/github/managing-security-vulnerabilities/about-dependabot-security-updates#about-compatibility-scores)

Dependabot will resolve any conflicts with this PR as long as you don't alter it yourself. You can also trigger a rebase manually by commenting `@dependabot rebase`.

<details>
<summary>Dependabot commands and options</summary>
<br />

You can trigger Dependabot actions by commenting on this PR:
- `@dependabot rebase` will rebase this PR
- `@dependabot recreate` will recreate this PR, overwriting any edits that have been made to it
- `@dependabot merge` will merge this PR after your CI passes on it
- `@dependabot squash and merge` will squash and merge this PR after your CI passes on it
- `@dependabot cancel merge` will cancel a previously requested merge and block automerging
- `@dependabot reopen` will reopen this PR if it is closed
- `@dependabot close` will close this PR and stop Dependabot recreating it. You can achieve the same result by closing it manually
- `@dependabot ignore this major version` will close this PR and stop Dependabot creating any more for this major version (unless you reopen the PR or upgrade to it yourself)
- `@dependabot ignore this minor version` will close this PR and stop Dependabot creating any more for this minor version (unless you reopen the PR or upgrade to it yourself)
- `@dependabot ignore this dependency` will close this PR and stop Dependabot creating any more for this dependency (unless you reopen the PR or upgrade to it yourself)

</details>"""


def bump(s, key, repo, number, title, name, url, old, new, eco, created, merged, branch, posts=(), dev=False):
    labels = ("dependencies", {"pip": "python", "npm": "javascript", "actions": "github_actions"}[eco])
    folder = {"pip": "pip", "npm": "npm_and_yarn", "actions": "github_actions"}[eco]
    slug = name.replace("@", "")
    s.add(thread(key, repo, number, "PR", title, "dependabot[bot]", created,
                 bump_body(name, url, old, new, eco), posts=posts, state="merged", merged=merged,
                 branches=f"dependabot/{folder}/{slug}-{new} -> main", labels=labels))


def build():
    s = Story()
    n = 0
    for repo, day, rows in PLAN:
        for row in rows:
            time, subject, spec = row[:3]
            body = row[3] if len(row) > 3 else ""
            n += 1
            message = subject + (f"\n\n{body}" if body else "")
            s.add(commit(f"nc-c{n:03d}", repo, f"{day} {time}", message, files(spec)))

    bump(s, "nc-t01", CL, 1, "build(deps): bump pdfplumber from 0.11.4 to 0.11.7", "pdfplumber",
         "https://github.com/jsvine/pdfplumber", "0.11.4", "0.11.7", "pip", "09-07 06:03", "09-08 09:15", "",
         posts=[Post(ME, "09-08 09:12", "Tests pass on the fixture set. Merging.", "APPROVED")])
    bump(s, "nc-t02", CL, 2, "build(deps-dev): bump ruff from 0.6.9 to 0.7.1", "ruff",
         "https://github.com/astral-sh/ruff", "0.6.9", "0.7.1", "pip", "09-14 06:02", "09-15 10:05", "",
         posts=[Post(ME, "09-15 10:04", "Lint is clean with the new version, no config changes needed.")])
    bump(s, "nc-t03", CL, 3, "build(deps): bump actions/setup-python from 5 to 6", "actions/setup-python",
         "https://github.com/actions/setup-python", "5", "6", "actions", "09-21 06:04", "09-22 09:48", "")
    bump(s, "nc-t04", FB, 1, "build(deps): bump fastapi from 0.115.14 to 0.116.1", "fastapi",
         "https://github.com/fastapi/fastapi", "0.115.14", "0.116.1", "pip", "09-07 06:05", "09-08 09:20", "",
         posts=[Post(ME, "09-08 09:19", "CI is green. Merging.", "APPROVED")])
    bump(s, "nc-t05", FB, 2, "build(deps): bump httpx from 0.27.2 to 0.28.1", "httpx",
         "https://github.com/encode/httpx", "0.27.2", "0.28.1", "pip", "09-14 06:01", "09-15 08:52", "",
         posts=[Post(ME, "09-14 16:30", "Checking the sms client still passes its timeout through before I merge."),
                Post(ME, "09-15 08:51", "Timeouts behave the same. Merging.")])
    bump(s, "nc-t06", FB, 3, "build(deps-dev): bump mypy from 1.11.2 to 1.13.0", "mypy",
         "https://github.com/python/mypy", "1.11.2", "1.13.0", "pip", "09-21 06:06", "09-22 09:35", "")
    bump(s, "nc-t07", HR, 1, "build(deps-dev): bump pytest from 8.3.3 to 8.3.4", "pytest",
         "https://github.com/pytest-dev/pytest", "8.3.3", "8.3.4", "pip", "09-14 06:02", "09-14 08:30", "",
         posts=[Post(ME, "09-14 08:29", "Merging.", "APPROVED")])
    bump(s, "nc-t08", HR, 2, "build(deps-dev): bump hypothesis from 6.112.0 to 6.115.3", "hypothesis",
         "https://github.com/HypothesisWorks/hypothesis", "6.112.0", "6.115.3", "pip", "09-21 06:05",
         "09-22 09:30", "",
         posts=[Post(ME, "09-22 09:29", "Ran the schedule tests twice, no new failures. Merging.")])
    bump(s, "nc-t09", SITE, 3, "Bump astro from 5.13.4 to 5.13.9", "astro", "https://github.com/withastro/astro",
         "5.13.4", "5.13.9", "npm", "09-07 06:03", "09-08 09:41", "",
         posts=[Post(ME, "09-08 09:40", "Build passes and the preview looks the same. Merging.", "APPROVED")])
    bump(s, "nc-t10", SITE, 5, "Bump @astrojs/rss from 4.0.11 to 4.0.12", "@astrojs/rss",
         "https://github.com/withastro/astro", "4.0.11", "4.0.12", "npm", "09-14 06:02", "09-14 09:07", "")
    bump(s, "nc-t11", SITE, 7, "Bump vitest from 3.2.3 to 3.2.4", "vitest", "https://github.com/vitest-dev/vitest",
         "3.2.3", "3.2.4", "npm", "09-21 06:04", "09-21 09:58", "",
         posts=[Post(ME, "09-21 09:57", "Merging.")])
    bump(s, "nc-t12", SITE, 9, "Bump actions/setup-node from 4 to 5", "actions/setup-node",
         "https://github.com/actions/setup-node", "4", "5", "actions", "09-28 06:03", "09-29 08:55", "",
         posts=[Post(ME, "09-29 08:54", "Node version is pinned in the workflow, so this is just the action.", "APPROVED")])
    bump(s, "nc-t13", PINCH, 20, "build(deps): bump actions/checkout from 4 to 5", "actions/checkout",
         "https://github.com/actions/checkout", "4", "5", "actions", "09-14 06:03", "09-14 11:21", "")

    s.add(thread("nc-t14", SITE, 8, "issue", "Link check failed on main", "github-actions[bot]", "09-25 11:12",
                 """The link check found broken links after the build.

Commit: main
Workflow: ci / links
Run: https://github.com/marlow-works/marlowworks-site/actions/runs/10298431127

Broken links (1):
- https://dev-notes.example/posts/boring-stacks  404 Not Found  (src/content/blog/boring-stacks.md:41)

This issue is opened automatically and closes when the next run passes.""",
                 posts=[Post(ME, "09-25 11:40", "The article moved. I'll point the link at the archived copy."),
                        Post(ME, "09-25 13:41", "Fixed on main. The run after the fix is green, closing.")],
                 state="closed", closed="09-25 13:42", labels=("ci",)))

    s.add(thread("nc-t15", PINCH, 18, "PR", "docs: fix typos in README", "tabitha-reyes", "09-03 19:42",
                 "Found two typos while reading the quickstart ('recieve' and 'seperate'). Docs only, no code changes.",
                 posts=[Post(ME, "09-04 08:52", "Thanks for catching these.", "APPROVED")],
                 state="merged", merged="09-04 08:55", branches="tabitha-reyes:fix-readme-typos -> main",
                 labels=("documentation",)))

    s.add(thread("nc-t16", PINCH, 19, "issue", "Feature request: cap output size (--max-chars)", "ostrander-k",
                 "09-08 21:07",
                 """I use pinch before pasting logs into a chat window that rejects anything over a fixed size. Right now I pipe the result through `head -c`, which cuts off the end of the log, and the end is usually the interesting part.

Would you consider a flag that caps the output size and drops lines from the middle instead? Happy to try a PR if you have an opinion on where it should live.""",
                 posts=[Post(ME, "09-09 07:46",
                             "Thanks for the suggestion. Dropping from the middle makes sense to me. Before I think about the shape: should the cap count characters or bytes, and should a marker line show where lines were dropped?"),
                        Post("ostrander-k", "09-09 20:30",
                             "Characters are fine for me. A marker line would help, something like `... 212 lines omitted ...` so the reader knows something is missing."),
                        Post(ME, "09-10 08:15",
                             "That works as a starting point. I'm labelling this as an enhancement and leaving it open. If you want to send a PR, trim.py is where line selection lives and tests/test_trim.py shows the test style.")],
                 labels=("enhancement",)))

    s.add(thread("nc-t17", PINCH, 22, "issue", "UnicodeDecodeError when the log contains non-UTF-8 bytes", "dkessler",
                 "09-17 20:15",
                 """Running pinch on a log from an old service crashes instead of printing anything.

Steps:
1. `cat app.log | pinch`
2. Crash

```
Traceback (most recent call last):
  File "/home/dkessler/.local/bin/pinch", line 8, in <module>
    sys.exit(main())
             ^^^^^^
  File "/home/dkessler/.local/share/pipx/venvs/pinch/lib/python3.12/site-packages/pinch/cli.py", line 74, in main
    text = sys.stdin.read()
           ^^^^^^^^^^^^^^^^
  File "<frozen codecs>", line 322, in decode
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9 in position 1873: invalid continuation byte
```

Installed with pipx, Python 3.12, Ubuntu 24.04. The same file is fine with `less`.""",
                 posts=[Post(ME, "09-18 07:58",
                             "Thanks for the traceback. I can reproduce it with a file that has a single 0xe9 byte. stdin is decoded as strict UTF-8 and the error is never caught. I'll make it replace undecodable bytes instead of aborting."),
                        Post("dkessler", "09-18 08:40",
                             "Replacing sounds right. A replacement character in the output is much better than losing the whole run."),
                        Post(ME, "09-19 09:44",
                             "Fixed on main: undecodable bytes become U+FFFD now. If you install from git you can check it against your log. Otherwise it ships with the next release."),
                        Post("dkessler", "09-19 12:05", "Confirmed, works on my log. Thanks!")],
                 state="closed", closed="09-19 12:10", labels=("bug",)))

    s.add(thread("nc-t18", PINCH, 25, "issue", "Does pinch work with docker logs output?", "mireille-k", "09-24 21:34",
                 """I run `docker logs web | pinch` and my terminal fills with the raw log while pinch prints nothing. Am I holding it wrong?""",
                 posts=[Post(ME, "09-25 08:21",
                             "`docker logs` replays the container's stderr on your stderr, and a pipe only carries stdout. Redirect first: `docker logs web 2>&1 | pinch`."),
                        Post("mireille-k", "09-25 12:50", "That fixed it, thank you."),
                        Post(ME, "09-26 10:30", "Added a FAQ entry to the README so the next person finds it. Closing.")],
                 state="closed", closed="09-26 10:31", labels=("question",)))
    return s
