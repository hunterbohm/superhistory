#!/usr/bin/env python3
"""Builds the launch release from one commit: a fresh git repo with no history, its notes, its archive, its checks.

    python3 tools/release_snapshot.py COMMIT OUT_DIR --author "Name <email>" --patterns FILE --credential TEXT
        [--credential TEXT ...] [--allow-link OWNER/NAME ...] [--allow-file PATH ...] [--repo-name NAME]
        [--installer install.sh] [--source REPO]

OUT_DIR must be absent or empty. It gets:

    repo/              a new git repo: COMMIT's tree as one root commit on main, tagged v<version>
    RELEASE_NOTES.md   the README's description and known limits
    v<version>.tar.gz  the source archive, built the way GitHub builds one for a tag
    CHECKS.txt         the report printed on screen

Nothing is pushed, and nothing is created on GitHub. The private values are arguments, so this file holds none: the
snapshot's own check would find them here. The exit code is 0 when every check that ran passed, 1 when one failed
(the folder is still written, for a look), and 2 when the run could not start.

The checks:

    history     the repo holds one commit with no parent, one branch, one tag, no remote
    credential  no object in the repo, the notes or the archive holds a made-up credential, in any case
    patterns    no file's text or name, no symlink target, no note and no commit message matches a line of the
                patterns file, read as `grep -i -E -f` reads it. A link to a repo named by --allow-link is not a
                hit, and neither is a file named by --allow-file; both are listed apart.
    archive     the archive holds the same files as the tree, under one folder, with install.sh in it
    install     --installer: the site's installer, run against a local server of this folder, installs `kb`
"""
import argparse
import contextlib
import dataclasses
import gzip
import http.server
import os
import re
import shutil
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
TAG_REF = "refs/tags/"
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

    def check(self, name):
        return next(c for c in self.checks if c.name == name)

    @property
    def ok(self):
        return all(c.ok for c in self.checks)

    def report(self):
        return report(self)


def result(name, problems, summary_ok, summary_bad, findings=(), allowed=()):
    summary = summary_bad if problems else summary_ok
    return CheckResult(name, "FAIL" if problems else "PASS", summary, list(findings), list(allowed))


def run(cmd, cwd=None, env=None, input=None):
    try:
        done = subprocess.run(cmd, cwd=None if cwd is None else str(cwd), env=env, input=input,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise ReleaseError("could not run %s: %s" % (cmd[0], exc))
    return done


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
    return lines


def parse_author(text):
    found = re.match(r"^\s*([^<>]+?)\s*<([^<>\s]+)>\s*$", text or "")
    if not found:
        raise ReleaseError('--author must look like "Name <email>"')
    return found.group(1), found.group(2)


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


def check_credential(repo, credentials, extra=None, archive=None, tag=None):
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
            findings.append(Finding(show(name), "in the file's name"))
    for sha, kind, body in object_stream(repo):
        scanned += 1
        if kind == "tree" or not has(body):
            continue
        if kind == "blob":
            findings.extend(Finding(where, "in the file's text") for where in sorted(names.get(sha, ["unreferenced " + sha[:8]])))
        else:
            findings.append(Finding("%s %s" % (kind, sha[:8]), "in the %s" % ("commit message or author" if kind == "commit" else "object")))
    for name, text in (extra or {}).items():
        if has(text.encode()):
            findings.append(Finding(name, "in the text"))
    if archive is not None:
        with tarfile.open(str(archive)) as tar:
            for member in tar:
                data = tar.extractfile(member).read() if member.isfile() else b""
                if has(member.name.encode()) or has(data):
                    findings.append(Finding("%s:%s" % (os.path.basename(str(archive)), member.name), "in the archive"))
    where_text = "%d objects%s%s" % (scanned, ", the notes" if extra else "", ", the archive" if archive is not None else "")
    return result("credential", findings, "no trace of %d made-up credential%s in %s, in any case" % (len(needles), "" if len(needles) == 1 else "s", where_text),
                  "a made-up credential is still here: %d place%s" % (len(findings), "" if len(findings) == 1 else "s"), findings)


def link_regex(slugs):
    for slug in slugs:
        if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", slug):
            raise ReleaseError("--allow-link takes OWNER/NAME, not %r" % slug)
    if not slugs:
        return None
    alternatives = b"|".join(re.escape(s.encode()) for s in slugs)
    return re.compile(rb"(?:(?:git\+)?https?://|git@)?github\.com[/:](?:" + alternatives + rb")(?![A-Za-z0-9_-])"
                      rb"(?:\.git)?(?:/[^\s\"'<>)\]`]*)?", re.I)


def grep_command(patterns, *more):
    return ["grep", "-a", "-n", "-i", "-E", "-f", str(patterns), *more]


def grep_run(cmd, cwd=None, input=None):
    done = run(cmd, cwd=cwd, env={**os.environ, "LC_ALL": "C"}, input=input)
    if done.returncode not in (0, 1):
        raise ReleaseError("grep failed: %s" % done.stderr.decode("utf-8", "replace").strip())
    return done.stdout


def grep_lines(patterns, lines):
    if not lines:
        return []
    out = grep_run(grep_command(patterns), input=b"\n".join(lines) + b"\n")
    return [int(row.split(b":", 1)[0]) - 1 for row in out.split(b"\n") if row]


def grep_tree(patterns, root):
    out = grep_run(grep_command(patterns, "-r", "--null", "--exclude-dir=.git", "--", "."), cwd=root)
    hits = []
    for row in out.split(b"\n"):
        if row:
            path, _, rest = row.partition(b"\0")
            number, _, text = rest.partition(b":")
            hits.append((show(path[2:]), int(number), text))
    return hits


def check_patterns(repo, patterns, allow_links=(), allow_files=(), extra=None):
    link = link_regex(list(allow_links))
    allow_files = set(allow_files)
    entries = tree_entries(repo)
    candidates = grep_tree(patterns, repo)
    links = [(show(name), git_bytes(repo, "cat-file", "blob", sha, isolated=True)) for mode, sha, name in entries if mode == "120000"]
    for index in grep_lines(patterns, [target for _, target in links]):
        candidates.append((links[index][0], 0, links[index][1]))
    for name, text in sorted((extra or {}).items()):
        lines = text.encode().splitlines()
        candidates.extend((name, index + 1, lines[index]) for index in grep_lines(patterns, lines))
    paths = [name for _, _, name in entries]
    if any(b"\n" in name for name in paths):
        raise ReleaseError("a file name holds a line break, which this check cannot read")
    bad_paths = [paths[i] for i in grep_lines(patterns, paths)]
    stripped = [(c, link.sub(b" ", c[2]) if link else c[2]) for c in candidates]
    changed = [i for i, (c, text) in enumerate(stripped) if text != c[2]]
    still = {changed[i] for i in grep_lines(patterns, [stripped[i][1] for i in changed])}
    findings, allowed = [], []
    for position, ((path, number, original), text) in enumerate(stripped):
        row = Finding("%s:%d" % (path, number) if number else path, show(original)[:LINE_WIDTH])
        if path in allow_files or (text != original and position not in still):
            allowed.append(row)
        else:
            findings.append(row)
    for name in bad_paths:
        row = Finding(show(name), "(a file name)")
        (allowed if show(name) in allow_files else findings).append(row)
    findings.sort(key=where_key)
    allowed.sort(key=where_key)
    summary_ok = "0 hits in %d files and names" % len(entries)
    if allowed:
        summary_ok += "; %d allowed, listed below" % len(allowed)
    return result("patterns", findings, summary_ok, "%d hits in %d files" % (len(findings), len(group(findings))), findings, allowed)


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


def check_archive(release_repo, archive, prefix):
    problems = []
    entries = tree_entries(release_repo)
    with tarfile.open(str(archive)) as tar:
        names = [m.name for m in tar.getmembers() if not m.isdir()]
    if any(not n.startswith(prefix) for n in names):
        problems.append("a file sits outside %s" % prefix)
    if prefix + "install.sh" not in names:
        problems.append("%sinstall.sh is missing" % prefix)
    if len(names) != len(entries):
        problems.append("%d files in the archive, %d in the tree" % (len(names), len(entries)))
    return result("archive", problems, "%d files under %s, install.sh among them" % (len(names), prefix),
                  "; ".join(problems), [Finding(p) for p in problems])


def serve_handler(out_dir, tag, log):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def route(self, send_body):
            if log is not None:
                log.append(self.path)
            if self.path == "/releases/latest":
                self.send_response(302)
                self.send_header("Location", "http://%s/releases/tag/%s" % (self.headers.get("Host"), tag))
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


@contextlib.contextmanager
def serve(out_dir, tag, log=None):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve_handler(out_dir, tag, log))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def run_install_check(release, installer, timeout=300):
    requests, findings = [], []
    with tempfile.TemporaryDirectory(prefix="release-install-") as tmp:
        home = os.path.join(tmp, "home")
        pybin = os.path.join(tmp, "only-python")
        os.makedirs(home)
        os.makedirs(pybin)
        os.symlink(sys.executable, os.path.join(pybin, "python3"))
        with serve(release.out, release.tag, requests) as base:
            env = {"HOME": home, "PATH": os.pathsep.join([pybin, "/usr/bin", "/bin"]), "TMPDIR": tmp,
                   "SUPERHISTORY_REPO": base}
            try:
                installed = subprocess.run(["sh", str(installer)], env=env, cwd=tmp, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
            except subprocess.TimeoutExpired:
                return CheckResult("install", "FAIL", "the installer ran past %d seconds" % timeout, [Finding("installer")])
        text = show(installed.stdout)
        if installed.returncode:
            findings.append(Finding("installer", "exit %d" % installed.returncode))
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
            helped = subprocess.run([command, "--help"], env=env, cwd=tmp, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
            help_text = show(helped.stdout)
            if helped.returncode or "usage" not in help_text.lower():
                findings.append(Finding("kb --help", "exit %d" % helped.returncode))
                findings.extend(Finding("kb --help", line) for line in help_text.splitlines()[:10])
    if findings:
        return CheckResult("install", "FAIL", "the site's installer ran but the install is not whole", findings)
    return CheckResult("install", "PASS", "the site's installer fetched %s from a local server and `kb --help` ran from ~/.local/bin" % release.tag)


def clear(path, created):
    if created:
        shutil.rmtree(path, ignore_errors=True)
        return
    for child in os.listdir(path):
        full = os.path.join(path, child)
        shutil.rmtree(full, ignore_errors=True) if os.path.isdir(full) else os.remove(full)


def build(source, rev, out, *, author, patterns, credentials, allow_links=(), allow_files=(), repo_name=None,
          installer=None):
    credentials = list(credentials)
    if not credentials or not all(c.strip() for c in credentials):
        raise ReleaseError("give at least one made-up credential to look for, each non-blank")
    read_patterns(patterns)
    link_regex(list(allow_links))
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
    repo_name = repo_name or re.sub(r"[^a-z0-9._-]+", "-", title.lower()).strip("-")
    out = os.path.abspath(str(out))
    if os.path.isdir(out) and os.listdir(out):
        raise ReleaseError("%s is not empty" % out)
    created = not os.path.exists(out)
    os.makedirs(out, exist_ok=True)
    try:
        return assemble(source, commit, out, name, email, tag, title, repo_name, notes_text, patterns, credentials,
                        allow_links, allow_files, installer)
    except BaseException:
        clear(out, created)
        raise


def assemble(source, commit, out, name, email, tag, title, repo_name, notes_text, patterns, credentials, allow_links,
             allow_files, installer):
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
    tree = git(repo, "rev-parse", "%s^{tree}" % commit, isolated=True)
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

    extra = {"RELEASE_NOTES.md": notes_text}
    release = Release(tag, snapshot, Path(repo), Path(notes), Path(archive), Path(out), [])
    release.checks = [
        check_history(repo, tag),
        check_credential(repo, credentials, extra, archive),
        check_patterns(repo, patterns, allow_links, allow_files, dict(extra, **{"commit message": message})),
        check_archive(repo, archive, prefix),
    ]
    if installer:
        release.checks.append(run_install_check(release, installer))
    else:
        release.checks.append(CheckResult("install", "SKIP", "not run: no --installer given"))
    return release


def report(release):
    top = {}
    for _, _, path in tree_entries(release.repo):
        key = show(path).split("/", 1)[0] + ("/" if b"/" in path else "")
        top[key] = top.get(key, 0) + 1
    lines = ["tag: %s" % release.tag, "commit: %s" % release.commit, "repo: %s" % release.repo,
             "notes: %s" % release.notes, "archive: %s (%d bytes)" % (release.archive, os.path.getsize(str(release.archive))),
             "files: %d (%s)" % (sum(top.values()), ", ".join("%s %d" % item for item in sorted(top.items())))]
    for check in release.checks:
        lines.append("%s %s: %s" % (check.status, check.name, check.summary))
        for label, found in (("", check.findings), ("allowed ", check.allowed)):
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
    parser.add_argument("--allow-link", action="append", default=[], metavar="OWNER/NAME",
                        help="a GitHub repo whose links are not hits (repeatable)")
    parser.add_argument("--allow-file", action="append", default=[], metavar="PATH",
                        help="a file whose pattern hits are listed but do not fail (repeatable)")
    parser.add_argument("--repo-name", help="the public repo's name, for the archive's folder (default: the README title)")
    parser.add_argument("--installer", help="the site's install.sh, to run against a local server of the output")
    args = parser.parse_args(argv)
    try:
        release = build(args.source or toplevel(None), args.commit, args.out, author=args.author,
                        patterns=args.patterns, credentials=args.credential, allow_links=args.allow_link,
                        allow_files=args.allow_file, repo_name=args.repo_name, installer=args.installer)
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
