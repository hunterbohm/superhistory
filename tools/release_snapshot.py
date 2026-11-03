#!/usr/bin/env python3
"""Builds the launch release from one commit: a fresh git repo with no history, its notes, its archive, its checks.

    python3 tools/release_snapshot.py COMMIT OUT_DIR --author "Name <email>" --patterns FILE --credential TEXT
        [--credential TEXT ...] [--repo-name NAME] [--installer install.sh] [--no-suites] [--source REPO]

OUT_DIR must be absent or empty. It gets:

    repo/              a new git repo: COMMIT's tree as one root commit on main, tagged v<version>
    RELEASE_NOTES.md   the README's description and known limits
    v<version>.tar.gz  the source archive, built the way GitHub builds one for a tag
    CHECKS.txt         the report printed on screen

Nothing is pushed, and nothing is created on GitHub. The private values are arguments, so this file holds none: the
snapshot's own check would find them here. The exit code is 0 when every check that ran passed, 1 when one failed
(the folder is still written, for a look), and 2 when the run could not start.

Three lists ship in COMMIT and steer the run. Each is tab-separated, and a blank line or a line starting with # is
skipped:

    bench/DATASET_ROWS.txt         <path><TAB><reason>. Every listed file is left out of the snapshot. The run does not
                                   start without this file, or when a line is not an exact path to a file in COMMIT.
    tools/release_exceptions.tsv   <path><TAB><string><TAB><reason>. A pattern hit is excepted only where its file
                                   equals the path and the line's matching text is the exact string, never a pattern.
    tools/release_binaries.tsv     <path><TAB><sha256><TAB><reason>. The only files allowed not to be UTF-8 text: each
                                   listed path must be a file that is not text, with exactly that sha256 of its bytes.

The checks:

    history     the repo holds one commit with no parent, one branch, one tag, no remote
    files       every file and symlink target in the tree is UTF-8 text with no NUL byte, or is listed in
                release_binaries.tsv with its sha256; a listed entry that names no such file fails. Every symlink
                stays inside the tree, followed through the tree's other links, and the tree holds no submodule.
    credential  no file name, blob or commit object in the repo, nor the notes, the repo name, the tag or the
                archive's folder name holds a made-up credential, in any case
    patterns    no file's text or name, no symlink target, no note, no commit message, nor the repo name, the tag or
                the archive's folder name matches a line of the patterns file, read as `grep -i -E -f` reads it,
                unless the exceptions list excepts the hit. Every match is found, overlapping ones too; a search
                that reaches its round limit fails. The list's own lines are not scanned. An entry that matches
                nothing is reported as stale. Whatever the patterns file holds, the release's own forbidden
                strings always count too: the local gateway's name in any spelling (so also in the key file's
                path under any home), its old port number and its proxy's name, in any case. The scans read the
                tree's bytes as they are and decode nothing: a binary file's contents are only as safe as the
                review behind its release_binaries.tsv entry.
    dataset     no file named in DATASET_ROWS.txt is in the tree or the archive
    archive     the archive holds the same files as the tree, member by member, under one folder, with install.sh in
                it. The tool builds it from the tree, so its tar headers and its compression are not scanned.
    install     --installer: the site's installer, run against a local https server of this folder (a throwaway
                certificate from openssl, trusted through CURL_CA_BUNDLE), installs `kb`
    suites      the snapshot's own tests, run inside the snapshot: tests/ under python3 and python3.9, and each
                folder of bench/ that holds test files, under python3. Every test must pass or skip with a reason,
                and the run must leave the snapshot as it found it. --no-suites skips this check.
"""
import argparse
import concurrent.futures
import contextlib
import dataclasses
import functools
import gzip
import hashlib
import http.server
import io
import os
import posixpath
import re
import shutil
import signal
import site
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
from pathlib import Path

SHOWN_FILES = 40
SHOWN_LINES = 3
LINE_WIDTH = 160
VERSION_FILE = "kb/__init__.py"
EXCEPTIONS_PATH = "tools/release_exceptions.tsv"
ROWS_PATH = "bench/DATASET_ROWS.txt"
SUITE_TIMEOUT = 3600
BINARIES_PATH = "tools/release_binaries.tsv"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
OVERLAP_CHECKS = 4096  # positions inside one line's matches read for one pattern before the search fails
MAX_LINK_HOPS = 40
ARCHIVE_MODES = {"100644": 0o664, "100755": 0o775, "120000": 0o777}
ARCHIVE_DIR = 0o775
INTERPRETERS = (("3.12", ("python3.12", "python3")), ("3.9", ("python3.9", "python3")))
VERSION_PROBE = "import sys; print('%d.%d' % sys.version_info[:2])"
SKIPPED = re.compile(r"\.\.\. skipped ('.*'|\".*\")\s*$", re.M)
TAG_REF = "refs/tags/"
FORBIDDEN = ["cli[-_]?" + "gateway", "83" + "17", "cli" + "proxy" + "api"]
ISOLATED = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


class ReleaseError(Exception):
    pass


@dataclasses.dataclass
class Finding:
    where: str
    text: str = ""


@dataclasses.dataclass
class CheckResult:
    name: str
    status: str
    summary: str
    findings: list = dataclasses.field(default_factory=list)
    allowed: list = dataclasses.field(default_factory=list)
    notes: list = dataclasses.field(default_factory=list)

    @property
    def ok(self):
        return self.status != "FAIL"


@dataclasses.dataclass
class Release:
    tag: str
    commit: str
    repo: object
    notes: object
    archive: object
    out: object
    checks: list
    left_out: list = dataclasses.field(default_factory=list)

    def check(self, name):
        return next(c for c in self.checks if c.name == name)

    @property
    def ok(self):
        return all(c.ok for c in self.checks)

    def report(self):
        return report(self)


def result(name, problems, summary_ok, summary_bad, findings=(), allowed=(), notes=()):
    summary = summary_bad if problems else summary_ok
    return CheckResult(name, "FAIL" if problems else "PASS", summary, list(findings), list(allowed), list(notes))


def guarded(name, check, *args):
    try:
        return check(*args)
    except Exception as exc:
        return CheckResult(name, "FAIL", "the check could not finish", [Finding(name, failure(exc))])


def run(cmd, cwd=None, env=None, input=None):
    try:
        done = subprocess.run(cmd, cwd=None if cwd is None else str(cwd), env=env, input=input,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise ReleaseError("could not run %s: %s" % (cmd[0], exc))
    return done


def run_group(argv, timeout, cwd=None, env=None):
    proc = subprocess.Popen(argv, cwd=None if cwd is None else str(cwd), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except BaseException:
        os.killpg(proc.pid, signal.SIGKILL)
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        raise
    return proc.returncode, out


def failure(exc):
    return "%s: %s" % (type(exc).__name__, exc)


def git_bytes(cwd, *args, env=None, isolated=False, input=None):
    full = dict(os.environ)
    if isolated:
        full.update(ISOLATED)
    full.update(env or {})
    done = run(["git", *args], cwd=cwd, env=full, input=input)
    if done.returncode:
        raise ReleaseError("git %s failed: %s" % (" ".join(args[:2]), done.stderr.decode("utf-8", "replace").strip()))
    return done.stdout


def git(cwd, *args, **kw):
    return git_bytes(cwd, *args, **kw).decode("utf-8", "replace").strip()


def read_patterns(path):
    try:
        with open(str(path), "rb") as fh:
            lines = fh.read().split(b"\n")
    except OSError as exc:
        raise ReleaseError("cannot read the patterns file: %s" % exc)
    if lines and lines[-1] == b"":
        lines.pop()
    if not lines:
        raise ReleaseError("the patterns file %s is empty" % path)
    for number, line in enumerate(lines, 1):
        if not line:
            raise ReleaseError("the patterns file has a blank line at line %d, which grep reads as matching every line" % number)
        if re.search(rb"\\[1-9]", line):
            raise ReleaseError("the patterns file uses a backreference at line %d, which the search for overlapping "
                               "matches cannot read" % number)
    return lines


def parse_author(text):
    found = re.match(r"^\s*([^<>]+?)\s*<([^<>\s]+)>\s*$", text or "")
    if not found:
        raise ReleaseError('--author must look like "Name <email>"')
    return found.group(1), found.group(2)


def commit_file(source, commit, path):
    listed = git_bytes(source, "ls-tree", "-z", commit, "--", path)
    if not listed:
        return None
    return git_bytes(source, "cat-file", "blob", "%s:%s" % (commit, path)).decode("utf-8", "replace")


def table_rows(text, name, width):
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != width or not all(f.strip() for f in fields):
            raise ReleaseError("%s line %d is not %d tab-separated, non-empty fields" % (name, number, width))
        rows.append(fields)
    return rows


def read_exceptions(text):
    return [tuple(row) for row in table_rows(text or "", EXCEPTIONS_PATH, 3)]


def read_rows(text, source, commit):
    files = {show(name) for mode, sha, name in tree_entries(source, commit)}
    listed = []
    for path, reason in table_rows(text, ROWS_PATH, 2):
        exact = path == posixpath.normpath(path) and not path.startswith("/") and ".." not in path.split("/")
        if not exact or path not in files:
            raise ReleaseError("%s lists %r, which is not a file in the commit (a path must be exact, from the top)" % (ROWS_PATH, path))
        if path not in listed:
            listed.append(path)
    return sorted(listed)


def read_version(source, commit):
    done = run(["git", "show", "%s:%s" % (commit, VERSION_FILE)], cwd=source)
    found = re.search(r"(?m)^__version__\s*=\s*[\"']([A-Za-z0-9._+-]+)[\"']", done.stdout.decode("utf-8", "replace"))
    if done.returncode or not found:
        raise ReleaseError("%s has no %s with a __version__" % (commit[:8], VERSION_FILE))
    return found.group(1)


def unwrap(lines):
    blocks, current, fenced = [], [], False

    def flush():
        if current:
            blocks.append(" ".join(current))
            current.clear()

    for line in lines:
        fence = line.lstrip().startswith("```")
        if fence or fenced or re.match(r"\s*([|>#<!]|[-*] |\d+\. )", line):
            flush()
            blocks.append(line)
            fenced = fenced != fence
        elif line.strip():
            current.append(line.strip())
        else:
            flush()
            blocks.append("")
    flush()
    return blocks


def readme_parts(readme):
    lines = readme.splitlines()
    heading = next((i for i, line in enumerate(lines) if line.startswith("# ")), None)
    if heading is None:
        raise ReleaseError("the README has no title line")
    title = lines[heading][2:].strip()
    stop = next((i for i in range(heading + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    description = unwrap(lines[heading + 1:stop])
    while description and not description[0]:
        description.pop(0)
    while description and not description[-1]:
        description.pop()
    if not description:
        raise ReleaseError("the README has no description between its title and its first section")
    start = next((i for i, line in enumerate(lines) if re.match(r"Known limits\b", line)), None)
    if start is None:
        raise ReleaseError("the README has no 'Known limits' list")
    bullets = []
    for line in lines[start + 1:]:
        if line.startswith("- "):
            bullets.append(line[2:].strip())
        elif bullets and re.match(r"\s+\S", line):
            bullets[-1] += " " + line.strip()
        elif line.strip() and not bullets:
            raise ReleaseError("the README's 'Known limits' is not followed by a list")
        elif line.strip() or bullets:
            break
    if not bullets:
        raise ReleaseError("the README's 'Known limits' list is empty")
    return title, description, bullets


def release_notes(readme, tag):
    title, description, bullets = readme_parts(readme)
    body = "\n".join(description)
    limits = "\n".join("- " + bullet for bullet in bullets)
    return ("# %s %s\n\n%s\n\n## Known limits\n\n%s\n\nThe README in the source archive covers install and every "
            "command.\n") % (title, tag, body, limits)


def archive_prefix(repo_name, tag):
    version = re.sub(r"^v(?=[0-9])", "", tag)
    return "%s-%s/" % (repo_name, version)


def object_stream(repo):
    proc = subprocess.Popen(["git", "cat-file", "--batch-all-objects", "--batch"], cwd=str(repo),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, **ISOLATED})
    try:
        while True:
            header = proc.stdout.readline()
            if not header:
                break
            sha, kind, size = header.split()
            body = proc.stdout.read(int(size))
            proc.stdout.read(1)
            yield sha.decode(), kind.decode(), body
    finally:
        proc.stdout.close()
        proc.wait()
        proc.stderr.close()


def tree_entries(repo, rev="HEAD"):
    raw = git_bytes(repo, "ls-tree", "-r", "-z", rev, isolated=True)
    entries = []
    for record in raw.split(b"\0"):
        if record:
            meta, _, name = record.partition(b"\t")
            mode, kind, sha = meta.split()
            entries.append((mode.decode(), sha.decode(), name))
    return entries


def show(raw):
    return raw.decode("utf-8", "replace")


def esc(raw):
    text = raw if isinstance(raw, str) else show(raw)
    return re.sub(r"[\x00-\x1f\x7f]", lambda m: "\\x%02x" % ord(m.group()), text)


def check_history(repo, tag):
    problems = []
    kinds = git(repo, "cat-file", "--batch-all-objects", "--batch-check=%(objecttype)", isolated=True).split()
    commits = kinds.count("commit")
    reachable = int(git(repo, "rev-list", "--all", "--count", isolated=True) or 0)
    if commits != 1 or reachable != 1:
        problems.append("%d commits in the object store, %d reachable" % (commits, reachable))
    if "tag" in kinds:
        problems.append("holds an annotated tag object")
    refs = git(repo, "for-each-ref", "--format=%(refname)", isolated=True).split()
    if sorted(refs) != sorted(["refs/heads/main", TAG_REF + tag]):
        problems.append("refs are %s, not main and %s" % (", ".join(refs) or "none", tag))
    if git(repo, "remote", isolated=True):
        problems.append("a remote is set")
    if os.path.exists(os.path.join(str(repo), ".git", "objects", "info", "alternates")):
        problems.append("the repo borrows objects from another")
    if problems:
        return result("history", problems, "", "; ".join(problems), [Finding(p) for p in problems])
    return result("history", problems, "1 commit with no parent, refs main and %s, no remote" % tag, "")


def is_text(data):
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def read_binaries(text):
    listed = {}
    for path, digest, _reason in table_rows(text or "", BINARIES_PATH, 3):
        if not SHA256.match(digest):
            raise ReleaseError("%s lists %r with %r, which is not a sha256 in lowercase hex" % (BINARIES_PATH, path, digest))
        if path in listed:
            raise ReleaseError("%s lists %r more than once" % (BINARIES_PATH, path))
        listed[path] = digest
    return listed


def link_end(links, name):
    """Follows the link at name through the tree's other links: "outside" when it leaves the tree, "loop" when it never ends, else None."""
    stack, queue, hops = name.split(b"/")[:-1], links[name].split(b"/"), 0
    if links[name].startswith(b"/"):
        return "outside"
    while queue:
        part = queue.pop(0)
        if part in (b"", b"."):
            continue
        if part == b"..":
            if not stack:
                return "outside"
            stack.pop()
            continue
        stack.append(part)
        target = links.get(b"/".join(stack))
        if target is not None:
            hops += 1
            if hops > MAX_LINK_HOPS:
                return "loop"
            if target.startswith(b"/"):
                return "outside"
            stack.pop()
            queue = target.split(b"/") + queue
    return None


def check_files(repo, binaries):
    entries = tree_entries(repo)
    blobs = read_blobs(repo, {sha for mode, sha, name in entries if mode in ARCHIVE_MODES})
    links = {name: blobs[sha] for mode, sha, name in entries if mode == "120000"}
    findings, seen, text = [], set(), 0
    for mode, sha, name in entries:
        where = esc(name)
        if mode not in ARCHIVE_MODES:
            findings.append(Finding(where, "is a %s (mode %s), not a file or a link" % ("submodule" if mode == "160000" else "tree entry", mode)))
            continue
        data = blobs[sha]
        if mode == "120000":
            end = link_end(links, name)
            if end:
                findings.append(Finding(where, "is a link to %s, %s" % (esc(data), "outside the tree" if end == "outside" else
                                                                      "which loops through more than %d links" % MAX_LINK_HOPS)))
        if is_text(data):
            text += 1
            continue
        path, digest = show(name), hashlib.sha256(data).hexdigest()
        seen.add(path)
        if path not in binaries:
            findings.append(Finding(where, "is not UTF-8 text without a NUL byte, and %s does not list it" % BINARIES_PATH))
        elif binaries[path] != digest:
            findings.append(Finding(where, "has sha256 %s, but %s lists %s: it changed after it was listed" % (digest, BINARIES_PATH, binaries[path])))
    findings.extend(Finding("%s: %s" % (BINARIES_PATH, esc(path)), "names no binary file in the tree")
                    for path in sorted(set(binaries) - seen))
    return result("files", findings, "%d files and links: %d UTF-8 text, %d listed binaries matching their sha256, every link inside the tree"
                  % (len(entries), text, len(entries) - text), "%d problem%s with the files" % (len(findings), "" if len(findings) == 1 else "s"),
                  findings)


def check_credential(repo, credentials, extra=None):
    needles = [c.lower().encode() for c in credentials]
    entries = tree_entries(repo)
    names = {}
    for mode, sha, name in entries:
        names.setdefault(sha, []).append(show(name))
    findings, scanned = [], 0

    def has(blob):
        low = blob.lower()
        return any(n in low for n in needles)

    for mode, sha, name in entries:
        if has(name):
            findings.append(Finding(esc(name), "in the file's name"))
    for sha, kind, body in object_stream(repo):
        scanned += 1
        if kind == "tree":
            continue
        if kind == "blob":
            if has(body):
                findings.extend(Finding(esc(where), "in the file's text") for where in sorted(names.get(sha, ["unreferenced " + sha[:8]])))
        elif has(body):
            findings.append(Finding("%s %s" % (kind, sha[:8]), "in the %s" % ("commit message or author" if kind == "commit" else "object")))
    for name, text in (extra or {}).items():
        if has(text.encode()):
            findings.append(Finding(name, "in the text"))
    where_text = "%d objects%s" % (scanned, ", the notes and the names" if extra else "")
    return result("credential", findings, "no trace of %d made-up credential%s in %s, in any case" % (len(needles), "" if len(needles) == 1 else "s", where_text),
                  "a made-up credential is still here: %d place%s" % (len(findings), "" if len(findings) == 1 else "s"), findings)


def grep_command(patterns, *more):
    return ["grep", "-a", "-n", "-i", "-E", "-f", str(patterns), *more]


def grep_run(cmd, cwd=None, input=None):
    done = run(cmd, cwd=cwd, env={**os.environ, "LC_ALL": "C"}, input=input)
    if done.returncode not in (0, 1):
        raise ReleaseError("grep failed: %s" % done.stderr.decode("utf-8", "replace").strip())
    return done.stdout


def grep_text(patterns, data):
    if not data:
        return []
    hits = []
    for row in grep_run(grep_command(patterns), input=data).split(b"\n"):
        if row:
            number, _, text = row.partition(b":")
            hits.append((int(number), text))
    return hits


@functools.lru_cache(maxsize=8)
def pattern_lines(path):
    with open(path, "rb") as fh:
        return tuple(line for line in fh.read().split(b"\n") if line)


def one_pattern_spans(pattern, text):
    out = grep_run(["grep", "-a", "-i", "-E", "-o", "-b", "-e", pattern], input=text + b"\n")
    spans = set()
    for row in out.split(b"\n"):
        if row:
            offset, _, match = row.partition(b":")
            spans.add((int(offset), int(offset) + len(match)))
    return spans


def match_at(pattern, line, start):
    """The longest match of pattern that starts at `start` (1 or more) in line, read on the line as it is with the byte
    before it as context, so a word boundary or an anchor reads as it does in the line; None if none starts there."""
    out = grep_run(["grep", "-a", "-i", "-E", "-o", "-b", "-e", b"^.(" + pattern + b")"], input=line[start - 1:] + b"\n")
    for row in out.split(b"\n"):
        offset, _, match = row.partition(b":")
        if row and int(offset) == 0 and len(match) > 1:
            return start, start - 1 + len(match)
    return None


def every_span(patterns, line):
    """Every match of every pattern in line, overlapping ones included, and whether the search finished. grep -o
    reports matches that do not overlap, each the longest at its start, so any other match starts inside one found:
    each position inside a found match is read on the line itself, and a search that would read more than
    OVERLAP_CHECKS positions for one pattern stops and says so."""
    spans, finished = set(), True
    for pattern in pattern_lines(patterns):
        found = one_pattern_spans(pattern, line)
        todo, read = sorted({p for s, e in found for p in range(s + 1, e)}, reverse=True), set()
        while todo:
            at = todo.pop()
            if at in read:
                continue
            if len(read) >= OVERLAP_CHECKS:
                finished = False
                break
            read.add(at)
            span = match_at(pattern, line, at)
            if span and span not in found:
                found.add(span)
                todo.extend(p for p in range(span[0] + 1, span[1]) if p not in read)
        spans |= found
    return sorted(spans), finished


def grep_matches(patterns, data):
    return [(number, line) + tuple(every_span(patterns, line)) for number, line in grep_text(patterns, data)]


def read_blobs(repo, shas):
    if not shas:
        return {}
    out = git_bytes(repo, "cat-file", "--batch", isolated=True, input=("\n".join(sorted(shas)) + "\n").encode())
    blobs, at = {}, 0
    while at < len(out):
        end = out.index(b"\n", at)
        header = out[at:end].split()
        if len(header) != 3:
            raise ReleaseError("git cat-file could not read %s" % show(out[at:end]))
        size = int(header[2])
        blobs[header[0].decode()] = out[end + 1:end + 1 + size]
        at = end + 1 + size + 1
    return blobs


def check_patterns(repo, patterns, exceptions=(), extra=None):
    with tempfile.TemporaryDirectory(prefix="release-patterns-") as folder:
        combined = os.path.join(folder, "patterns.txt")
        with open(combined, "wb") as fh:
            fh.write(b"\n".join(read_patterns(patterns) + [p.encode() for p in FORBIDDEN]) + b"\n")
        return scan_patterns(repo, combined, exceptions, extra)


def occurrences(line, string):
    at = line.find(string)
    while at >= 0:
        yield at, at + len(string)
        at = line.find(string, at + 1)


def scan_patterns(repo, patterns, exceptions, extra):
    entries = tree_entries(repo)
    blobs = read_blobs(repo, {sha for mode, sha, name in entries if mode in ARCHIVE_MODES})
    candidates = []
    for mode, sha, name in entries:
        if mode in ARCHIVE_MODES and show(name) != EXCEPTIONS_PATH:
            candidates.extend((show(name),) + hit for hit in grep_matches(patterns, blobs[sha]))
    for name, text in sorted((extra or {}).items()):
        candidates.extend((name,) + hit for hit in grep_matches(patterns, text.encode()))
    bad_paths = [name for _, _, name in entries if grep_text(patterns, name)]
    strings = {}
    for path, string, _ in exceptions:
        strings.setdefault(path, []).append(string)
    used, findings, allowed = set(), [], []
    for path, number, line, spans, finished in candidates:
        covers = [(start, end, (path, string)) for string in strings.get(path, ())
                  for start, end in occurrences(line, string.encode())]
        uncovered = not spans or not finished
        for start, end in spans:
            over = [key for s, e, key in covers if s <= start and end <= e]
            used.update(over)
            uncovered = uncovered or not over
        text = show(line)[:LINE_WIDTH]
        if not finished:
            text = "the search for overlapping matches stopped after %d positions, so not every match was checked: %s" % (OVERLAP_CHECKS, text)
        (findings if uncovered else allowed).append(Finding("%s:%d" % (esc(path), number), text))
    findings.extend(Finding(esc(name), "(a file name)") for name in bad_paths)
    findings.sort(key=where_key)
    allowed.sort(key=where_key)
    stale = ["stale: %s: %s" % (path, string) for path, string, _ in exceptions if (path, string) not in used]
    tail = "".join(["; %d excepted" % len(allowed) if allowed else "", "; %d stale" % len(stale) if stale else ""])
    return result("patterns", findings, "0 hits outside the exceptions list in %d files and names%s" % (len(entries), tail),
                  "%d hits outside the exceptions list in %d files%s" % (len(findings), len(group(findings)), tail),
                  findings, allowed, stale)


def where_key(finding):
    path, number = split_where(finding.where)
    return (path, number)


def split_where(where):
    found = re.match(r"^(.*):(\d+)$", where)
    return (found.group(1), int(found.group(2))) if found else (where, 0)


def group(findings):
    grouped = {}
    for finding in findings:
        grouped.setdefault(split_where(finding.where)[0], []).append(finding)
    return grouped


def blob_id(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def member_key(raw):
    return raw.decode("utf-8", "surrogateescape")


def check_archive(release_repo, archive, prefix):
    entries = [e for e in tree_entries(release_repo) if e[0] in ARCHIVE_MODES]
    wanted = {prefix + member_key(name): (mode, sha) for mode, sha, name in entries}
    folders = {prefix[:-1]}
    for path in wanted:
        parts = path.split("/")[:-1]
        folders.update("/".join(parts[:n]) for n in range(len(prefix.split("/")) - 1, len(parts) + 1))
    problems, seen = [], set()

    def problem(where, text):
        problems.append(Finding(esc(where), text))

    try:
        with open(str(archive), "rb") as fh:
            packed = gzip.decompress(fh.read())
    except (OSError, EOFError, ValueError) as exc:
        problem(os.path.basename(str(archive)), "is not a clean gzip file: %s" % failure(exc))
        return result("archive", problems, "", "the archive cannot be read", problems)
    with tarfile.open(fileobj=io.BytesIO(packed)) as tar:
        for member in tar:
            name = member.name.rstrip("/")
            if name in seen:
                problem(name, "appears more than once in the archive")
            seen.add(name)
            if member.isdir():
                if name not in folders:
                    problem(name, "is a folder that is not in the tree")
                elif member.mode != ARCHIVE_DIR:
                    problem(name, "has mode %s, git writes %s" % (oct(member.mode), oct(ARCHIVE_DIR)))
                continue
            if name not in wanted:
                problem(name, "is in the archive but not in the tree under %s" % prefix)
                continue
            mode, sha = wanted[name]
            if member.isreg():
                data, kind = tar.extractfile(member).read(), "100644 or 100755"
                good_type = mode in ("100644", "100755")
            elif member.issym():
                data, kind = member.linkname.encode("utf-8", "surrogateescape"), "120000"
                good_type = mode == "120000"
            else:
                problem(name, "is neither a file nor a link")
                continue
            if not good_type:
                problem(name, "is a %s in the archive and mode %s in the tree" % ("file" if member.isreg() else "link", mode))
            elif blob_id(data) != sha:
                problem(name, "has contents that differ from the tree's")
            elif member.mode != ARCHIVE_MODES[mode]:
                problem(name, "has mode %s, the tree's %s is written as %s" % (oct(member.mode), mode, oct(ARCHIVE_MODES[mode])))
        if packed[tar.offset:].strip(b"\0"):
            problem(os.path.basename(str(archive)), "holds bytes after the end of the tar stream")
    for name in sorted((set(wanted) | folders) - seen):
        problem(name, "is in the tree but missing from the archive")
    if prefix + "install.sh" not in wanted:
        problem(prefix + "install.sh", "is not in the tree")
    return result("archive", problems, "%d files and %d folders match the tree exactly under %s, install.sh among them" % (len(wanted), len(folders), prefix),
                  "%d differences between the archive and the tree" % len(problems), problems)


def check_dataset(repo, archive, listed, prefix):
    present = {show(name) for _, _, name in tree_entries(repo)}
    with tarfile.open(str(archive)) as tar:
        packed = {n[len(prefix):] for n in tar.getnames() if n.startswith(prefix)}
    back = [p for p in listed if p in present or p in packed]
    count = "%d file%s" % (len(listed), "" if len(listed) == 1 else "s")
    return result("dataset", back, "%s named in %s left out of the tree and the archive" % (count, ROWS_PATH),
                  "%d listed file%s still in the snapshot" % (len(back), "" if len(back) == 1 else "s"),
                  [Finding(p, "listed in %s" % ROWS_PATH) for p in back])


def serve_handler(out_dir, tag, log):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def route(self, send_body):
            if log is not None:
                log.append(self.path)
            if self.path == "/releases/latest":
                self.send_response(302)
                self.send_header("Location", "https://%s/releases/tag/%s" % (self.headers.get("Host"), tag))
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif self.path == "/releases/tag/" + tag:
                self.reply(b"<html><body>release %s</body></html>" % tag.encode(), "text/html", send_body)
            elif self.path == "/archive/refs/tags/%s.tar.gz" % tag:
                with open(os.path.join(str(out_dir), tag + ".tar.gz"), "rb") as fh:
                    self.reply(fh.read(), "application/gzip", send_body)
            else:
                self.send_error(404)

        def reply(self, body, kind, send_body):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def do_GET(self):
            self.route(True)

        def do_HEAD(self):
            self.route(False)

    return Handler


@dataclasses.dataclass
class Served:
    url: str
    cafile: str


def make_certificate(folder):
    config = os.path.join(folder, "openssl.cnf")
    with open(config, "w") as fh:
        fh.write("[req]\ndistinguished_name = dn\nprompt = no\nx509_extensions = ext\n[dn]\nCN = 127.0.0.1\n"
                 "[ext]\nsubjectAltName = IP:127.0.0.1\nbasicConstraints = critical,CA:TRUE\n"
                 "keyUsage = critical,digitalSignature,keyCertSign\n")
    cert, key = os.path.join(folder, "cert.pem"), os.path.join(folder, "key.pem")
    done = run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-config", config,
                "-keyout", key, "-out", cert])
    if done.returncode:
        raise ReleaseError("could not make a throwaway certificate with openssl: %s" % done.stderr.decode("utf-8", "replace").strip())
    return cert, key


@contextlib.contextmanager
def serve(out_dir, tag, log=None):
    with tempfile.TemporaryDirectory(prefix="release-cert-") as folder:
        cert, key = make_certificate(folder)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve_handler(out_dir, tag, log))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield Served("https://127.0.0.1:%d" % server.server_address[1], cert)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def run_install_check(release, installer, timeout=300):
    try:
        return install_check(release, installer, timeout)
    except Exception as exc:
        return CheckResult("install", "FAIL", "the install check could not finish", [Finding("install", failure(exc))])


def install_check(release, installer, timeout):
    requests, findings = [], []
    with tempfile.TemporaryDirectory(prefix="release-install-") as tmp:
        home = os.path.join(tmp, "home")
        pybin = os.path.join(tmp, "only-python")
        os.makedirs(home)
        os.makedirs(pybin)
        os.symlink(sys.executable, os.path.join(pybin, "python3"))
        with serve(release.out, release.tag, requests) as served:
            env = {"HOME": home, "PATH": os.pathsep.join([pybin, "/usr/bin", "/bin"]), "TMPDIR": tmp,
                   "SUPERHISTORY_REPO": served.url, "CURL_CA_BUNDLE": served.cafile}
            try:
                code, output = run_group(["sh", str(installer)], timeout, cwd=tmp, env=env)
            except subprocess.TimeoutExpired:
                return CheckResult("install", "FAIL", "the installer ran past %d seconds" % timeout, [Finding("installer", "ran past %d seconds" % timeout)])
        text = show(output)
        if code:
            findings.append(Finding("installer", "exit %d" % code))
            findings.extend(Finding("installer", line) for line in text.splitlines()[-10:])
            return CheckResult("install", "FAIL", "the site's installer failed", findings)
        wanted = ["/releases/latest", "/archive/refs/tags/%s.tar.gz" % release.tag]
        missed = [w for w in wanted if w not in requests]
        command = os.path.join(home, ".local", "bin", "kb")
        if missed:
            findings.append(Finding("installer", "never asked the server for %s" % ", ".join(missed)))
        if not os.path.exists(command):
            findings.append(Finding(command[len(home) + 1:], "the installer left no kb command"))
        else:
            try:
                code, output = run_group([command, "--help"], timeout, cwd=tmp, env=env)
            except subprocess.TimeoutExpired:
                findings.append(Finding("kb --help", "ran past %d seconds" % timeout))
            except OSError as exc:
                findings.append(Finding("kb --help", failure(exc)))
            else:
                help_text = show(output)
                if code or "usage" not in help_text.lower():
                    findings.append(Finding("kb --help", "exit %d" % code))
                    findings.extend(Finding("kb --help", line) for line in help_text.splitlines()[:10])
    if findings:
        return CheckResult("install", "FAIL", "the site's installer ran but the install is not whole", findings)
    return CheckResult("install", "PASS", "the site's installer fetched %s from a local https server and `kb --help` ran from ~/.local/bin" % release.tag)


@dataclasses.dataclass
class Suite:
    label: str
    argv: list
    want: tuple = None


def default_suites(repo, interpreters=INTERPRETERS):
    suites = [Suite("python%s tests" % version, ["python", "-m", "unittest", "discover", "-v", "-s", "tests", "-t", "."],
                    (version, candidates)) for version, candidates in interpreters]
    folders = {posixpath.dirname(show(name)) for _, _, name in tree_entries(repo)
               if show(name).startswith("bench/") and re.search(r"(^|/)test_[^/]*\.py$", show(name))}
    suites.extend(Suite(folder, ["python", "-m", "unittest", "discover", "-v", "-s", folder, "-p", "test_*.py"], interpreters[0])
                  for folder in sorted(folders))
    return suites


def find_python(version, candidates, env):
    seen = []
    for name in candidates:
        exe = shutil.which(name)
        if exe is None:
            seen.append("%s is not on PATH" % name)
            continue
        try:
            code, output = run_group([exe, "-c", VERSION_PROBE], 60, env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            seen.append("%s did not run: %s" % (name, failure(exc)))
            continue
        found = show(output).strip()
        if not code and found == version:
            return exe, None
        seen.append("%s is %s, not %s" % (name, found or "unreadable", version))
    return None, "needs Python %s: %s" % (version, "; ".join(seen))


def run_suite(suite, repo, env, timeout):
    exe, why = find_python(suite.want[0], suite.want[1], env)
    if exe is None:
        return suite.label, None, 0, [], [why]
    try:
        code, output = run_group([exe] + suite.argv[1:], timeout, cwd=repo, env=env)
    except subprocess.TimeoutExpired:
        return suite.label, None, 0, [], ["ran past %d seconds" % timeout]
    except OSError as exc:
        return suite.label, None, 0, [], [failure(exc)]
    text = output.decode("utf-8", "replace")
    ran = re.search(r"(?m)^Ran (\d+) tests? in", text)
    count = int(ran.group(1)) if ran else 0
    reasons = [m.group(1)[1:-1] for m in SKIPPED.finditer(text)]
    counted = re.search(r"\(.*skipped=(\d+)", text)
    problems = []
    if code:
        problems.append("exit %d" % code)
        lines = [l for l in text.splitlines() if re.match(r"(FAIL|ERROR): ", l)]
        problems.extend(lines[:10] or [l for l in text.splitlines() if l.strip()][-5:])
    if not count:
        problems.append("ran no tests")
    if any(not r.strip() for r in reasons):
        problems.append("%d skipped without a reason" % sum(1 for r in reasons if not r.strip()))
    if counted and int(counted.group(1)) > len(reasons):
        problems.append("%d skipped with no reason shown" % (int(counted.group(1)) - len(reasons)))
    return suite.label, code, count, reasons, problems


def run_suites(repo, suites, timeout=SUITE_TIMEOUT):
    with tempfile.TemporaryDirectory(prefix="release-suites-") as home:
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "KB_HOME")}
        env.setdefault("PYTHONUSERBASE", site.getuserbase())
        env.update({"HOME": home, "PYTHONDONTWRITEBYTECODE": "1"})
        copy = os.path.join(home, "repo")
        shutil.copytree(str(repo), copy, symlinks=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(suites))) as pool:
            runs = list(pool.map(lambda suite: run_suite(suite, copy, env, timeout), suites))
        left = git_bytes(copy, "status", "--porcelain", "-z", isolated=True)
    findings, notes, total, skipped = [], [], 0, 0
    for label, code, count, reasons, problems in runs:
        total += count
        skipped += len(reasons)
        notes.append("%s: %d tests, %d skipped, %s" % (label, count, len(reasons), "failed" if problems else "passed"))
        tally = {}
        for reason in reasons:
            tally[reason] = tally.get(reason, 0) + 1
        notes.extend("  %s skipped %d: %s" % (label, n, r) for r, n in sorted(tally.items(), key=lambda kv: -kv[1])[:15])
        findings.extend(Finding(label, p) for p in problems)
    left = [show(entry) for entry in left.split(b"\0") if entry]
    findings.extend(Finding("copy", "left behind: %s" % esc(entry)) for entry in left)
    failed = sum(1 for run_ in runs if run_[4])
    count = "%d suite%s, %d tests, %d skipped, each with a reason" % (len(runs), "" if len(runs) == 1 else "s", total, skipped)
    return result("suites", findings, count + ", 0 failed", "%s; %d failed%s" % (count, failed, ", files left behind in the copy" if left else ""),
                  findings, (), notes)


def sha256_of(path):
    with open(str(path), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def digest_tree(root, skip=()):
    digest = hashlib.sha256()
    for folder, names, files in os.walk(root):
        names[:] = sorted(n for n in names if os.path.relpath(os.path.join(folder, n), root) not in skip)
        relative = os.path.relpath(folder, root)
        digest.update(b"dir %s %o\0" % (relative.encode("utf-8", "surrogateescape"), os.lstat(folder).st_mode & 0o7777))
        for name in sorted(files + [n for n in names if os.path.islink(os.path.join(folder, n))]):
            full = os.path.join(folder, name)
            info = os.lstat(full)
            label = os.path.relpath(full, root).encode("utf-8", "surrogateescape")
            if os.path.islink(full):
                body = os.readlink(full).encode("utf-8", "surrogateescape")
            else:
                with open(full, "rb") as fh:
                    body = fh.read()
            digest.update(b"file %s %o %s\0" % (label, info.st_mode & 0o7777, hashlib.sha256(body).hexdigest().encode()))
    return digest.hexdigest()


def fingerprint(release):
    repo = str(release.repo)
    git_dir = os.path.join(repo, ".git")
    return {"refs": "%s %s %s" % (digest_tree(os.path.join(git_dir, "refs")), sha256_of(os.path.join(git_dir, "HEAD")),
                                  sha256_of(os.path.join(git_dir, "packed-refs")) if os.path.exists(os.path.join(git_dir, "packed-refs")) else "none"),
            "objects": digest_tree(os.path.join(git_dir, "objects")),
            "git settings": sha256_of(os.path.join(git_dir, "config")),
            "tree": git(repo, "rev-parse", "HEAD^{tree}", isolated=True),
            "working tree": digest_tree(repo, skip=(".git",)),
            "archive": sha256_of(release.archive), "notes": sha256_of(release.notes)}


def watched(release, run_step, step):
    before = fingerprint(release)
    check = run_step()
    after = fingerprint(release)
    changed = [key for key in before if before[key] != after[key]]
    if changed:
        check.status = "FAIL"
        check.findings.append(Finding("snapshot", "the snapshot changed while the %s ran: %s" % (step, ", ".join(changed))))
        check.summary += "; the snapshot changed (%s)" % ", ".join(changed)
    return check


def clear(path, created):
    if created:
        shutil.rmtree(path, ignore_errors=True)
        return
    for child in os.listdir(path):
        full = os.path.join(path, child)
        shutil.rmtree(full, ignore_errors=True) if os.path.isdir(full) else os.remove(full)


def build(source, rev, out, *, author, patterns, credentials, repo_name=None, installer=None, suites=None):
    credentials = list(credentials)
    if not credentials or not all(c.strip() for c in credentials):
        raise ReleaseError("give at least one made-up credential to look for, each non-blank")
    read_patterns(patterns)
    name, email = parse_author(author)
    done = run(["git", "rev-parse", "--verify", "--quiet", rev + "^{commit}"], cwd=source)
    if done.returncode:
        raise ReleaseError("no such commit: %s" % rev)
    commit = done.stdout.decode().strip()
    version = read_version(source, commit)
    tag = "v" + version
    shown = run(["git", "show", "%s:README.md" % commit], cwd=source)
    if shown.returncode:
        raise ReleaseError("%s has no README.md" % commit[:8])
    readme = shown.stdout.decode("utf-8", "replace")
    title = readme_parts(readme)[0]
    notes_text = release_notes(readme, tag)
    exceptions = read_exceptions(commit_file(source, commit, EXCEPTIONS_PATH))
    read_binaries(commit_file(source, commit, BINARIES_PATH))
    rows = commit_file(source, commit, ROWS_PATH)
    if rows is None:
        raise ReleaseError("%s is missing from the commit: it names the bench files whose rows quote LoCoMo or "
                           "LongMemEval text, and the run does not start without it" % ROWS_PATH)
    left_out = read_rows(rows, source, commit)
    repo_name = repo_name or re.sub(r"[^a-z0-9._-]+", "-", title.lower()).strip("-")
    out = os.path.abspath(str(out))
    if os.path.isdir(out) and os.listdir(out):
        raise ReleaseError("%s is not empty" % out)
    created = not os.path.exists(out)
    os.makedirs(out, exist_ok=True)
    try:
        return assemble(source, commit, out, name, email, tag, title, repo_name, notes_text, patterns, credentials,
                        exceptions, left_out, installer, suites)
    except BaseException:
        clear(out, created)
        raise


def snapshot_tree(repo, commit, left_out):
    if not left_out:
        return git(repo, "rev-parse", "%s^{tree}" % commit, isolated=True)
    env = {"GIT_INDEX_FILE": os.path.join(repo, ".git", "release-index")}
    git(repo, "read-tree", commit, env=env, isolated=True)
    git(repo, "update-index", "--force-remove", "-z", "--stdin", env=env, isolated=True,
        input=b"".join(path.encode() + b"\0" for path in left_out))
    tree = git(repo, "write-tree", env=env, isolated=True)
    os.remove(env["GIT_INDEX_FILE"])
    return tree


def assemble(source, commit, out, name, email, tag, title, repo_name, notes_text, patterns, credentials, exceptions,
             left_out, installer, suites):
    repo = os.path.join(out, "repo")
    git(out, "init", "-q", "--template=", repo, isolated=True)
    git(repo, "symbolic-ref", "HEAD", "refs/heads/main", isolated=True)
    objects = os.path.join(os.path.abspath(str(source)), git(source, "rev-parse", "--git-path", "objects"))
    info = os.path.join(repo, ".git", "objects", "info")
    os.makedirs(info, exist_ok=True)
    alternates = os.path.join(info, "alternates")
    with open(alternates, "w") as fh:
        fh.write(objects + "\n")
    stamp = git(source, "show", "-s", "--format=%cI", commit)
    ident = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": stamp,
             "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email, "GIT_COMMITTER_DATE": stamp}
    message = "%s %s" % (title, tag)
    tree = snapshot_tree(repo, commit, left_out)
    snapshot = git(repo, "commit-tree", tree, "-m", message, env=ident, isolated=True)
    git(repo, "update-ref", "refs/heads/main", snapshot, isolated=True)
    git(repo, "tag", tag, snapshot, isolated=True)
    git(repo, "reset", "-q", "--hard", isolated=True)
    git(repo, "repack", "-a", "-d", "-q", isolated=True)
    os.remove(alternates)
    shutil.rmtree(os.path.join(repo, ".git", "logs"), ignore_errors=True)
    git(repo, "fsck", "--full", "--strict", isolated=True)

    notes = os.path.join(out, "RELEASE_NOTES.md")
    with open(notes, "w") as fh:
        fh.write(notes_text)

    prefix = archive_prefix(repo_name, tag)
    archive = os.path.join(out, tag + ".tar.gz")
    tar = git_bytes(repo, "archive", "--format=tar", "--prefix=" + prefix, tag, isolated=True)
    with open(archive, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as packed:
            packed.write(tar)

    release = Release(tag, snapshot, Path(repo), Path(notes), Path(archive), Path(out), [], left_out)
    install_result = (guarded("install", watched, release, lambda: run_install_check(release, installer), "installer")
                      if installer else CheckResult("install", "SKIP", "not run: no --installer given"))
    suites_result = (guarded("suites", watched, release, lambda: run_suites(release.repo, suites(release.repo)), "suites")
                     if suites else CheckResult("suites", "SKIP", "not run: --no-suites given"))

    def final_text():
        extra = {"RELEASE_NOTES.md": Path(notes).read_bytes().decode("utf-8", "replace"), "repo name": repo_name, "tag": tag,
                 "archive folder": prefix.rstrip("/")}
        return extra, dict(extra, **{"commit message": git(repo, "log", "-1", "--format=%B", isolated=True)})

    def final_exceptions():
        return read_exceptions(commit_file(repo, "HEAD", EXCEPTIONS_PATH))

    def final_binaries():
        return read_binaries(commit_file(repo, "HEAD", BINARIES_PATH))

    def final_rows():
        return sorted({row[0] for row in table_rows(commit_file(repo, "HEAD", ROWS_PATH) or "", ROWS_PATH, 2)})

    release.checks = [
        guarded("history", lambda: check_history(repo, tag)),
        guarded("files", lambda: check_files(repo, final_binaries())),
        guarded("credential", lambda: check_credential(repo, credentials, final_text()[0])),
        guarded("patterns", lambda: check_patterns(repo, patterns, final_exceptions(), final_text()[1])),
        guarded("dataset", lambda: check_dataset(repo, archive, final_rows(), prefix)),
        guarded("archive", lambda: check_archive(repo, archive, prefix)),
        install_result,
        suites_result,
    ]
    return release


def report(release):
    top = {}
    for _, _, path in tree_entries(release.repo):
        key = esc(show(path).split("/", 1)[0] + ("/" if b"/" in path else ""))
        top[key] = top.get(key, 0) + 1
    lines = ["tag: %s" % release.tag, "commit: %s" % release.commit, "repo: %s" % release.repo,
             "notes: %s" % release.notes, "archive: %s (%d bytes)" % (release.archive, os.path.getsize(str(release.archive))),
             "files: %d (%s)" % (sum(top.values()), ", ".join("%s %d" % item for item in sorted(top.items()))),
             "left out: %d%s" % (len(release.left_out), " (%s%s)" % (", ".join(release.left_out[:5]), ", ..." if len(release.left_out) > 5 else "") if release.left_out else "")]
    for check in release.checks:
        lines.append("%s %s: %s" % (check.status, check.name, check.summary))
        for label, found in (("", check.findings), ("excepted ", check.allowed)):
            grouped = group(found)
            if check.name != "patterns" and not label:
                lines.extend("    %s%s%s" % (f.where, ": " if f.text else "", f.text) for f in found[:SHOWN_LINES * 3])
                continue
            for path in sorted(grouped)[:SHOWN_FILES]:
                rows = grouped[path]
                lines.append("    %s%s: %d hit%s" % (label, path, len(rows), "" if len(rows) == 1 else "s"))
                lines.extend("        %s: %s" % (r.where, r.text) for r in rows[:SHOWN_LINES])
            if len(grouped) > SHOWN_FILES:
                lines.append("    %s... and %d more files" % (label, len(grouped) - SHOWN_FILES))
        lines.extend("    " + note for note in check.notes)
    lines.append("RESULT: %s" % ("PASS" if release.ok else "FAIL"))
    return "\n".join(lines) + "\n"


def toplevel(path):
    done = run(["git", "rev-parse", "--show-toplevel"], cwd=path)
    if done.returncode:
        raise ReleaseError("%s is not inside a git repo; name one with --source" % (path or os.getcwd()))
    return done.stdout.decode().strip()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="release_snapshot", description=__doc__.split("\n\n")[0])
    parser.add_argument("commit", help="the commit to snapshot: a branch, a tag or a hash in --source")
    parser.add_argument("out", help="the folder to write: absent or empty")
    parser.add_argument("--source", help="the repo that holds the commit (default: the one around the current folder)")
    parser.add_argument("--author", required=True, help='who the snapshot commit is by, as "Name <email>"')
    parser.add_argument("--patterns", required=True, help="a file of grep -E patterns that nothing public may match")
    parser.add_argument("--credential", action="append", default=[], required=True,
                        help="a made-up credential that must not appear anywhere in the snapshot (repeatable)")
    parser.add_argument("--repo-name", help="the public repo's name, for the archive's folder (default: the README title)")
    parser.add_argument("--installer", help="the site's install.sh, to run against a local server of the output")
    parser.add_argument("--no-suites", action="store_true", help="skip the check that runs the snapshot's own tests")
    args = parser.parse_args(argv)
    try:
        release = build(args.source or toplevel(None), args.commit, args.out, author=args.author,
                        patterns=args.patterns, credentials=args.credential, repo_name=args.repo_name,
                        installer=args.installer, suites=None if args.no_suites else default_suites)
    except ReleaseError as exc:
        print("release_snapshot: %s" % exc, file=sys.stderr)
        return 2
    text = release.report()
    with open(os.path.join(release.out, "CHECKS.txt"), "w") as fh:
        fh.write(text)
    print(text, end="")
    return 0 if release.ok else 1


if __name__ == "__main__":
    sys.exit(main())
