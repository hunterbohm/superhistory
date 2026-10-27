"""Commits the user authored in the git repositories under the `roots` folders: one item per commit.

Many folders are separate clones of one GitHub repository, so an item's id is the remote's owner/repo plus the full
sha, and a run reads each sha once. A repository with no GitHub remote is named by its folder. Only branches,
remote-tracking branches and tags are walked: agent tools such as T3 Code and Codex keep checkpoints under refs/t3
and refs/codex, and a stash is not shipped work.

A commit is the user's when its author name or email is git's global user.name or user.email, or one of the
`identities` option.

Exclude key: the repository's folder with the home folder written as "~", such as "~/code/old-client", or the
absolute path outside home. A folder that matches is not entered, so everything under it is skipped too.
"""
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NamedTuple, Tuple

from kb.mask import mask
from kb.sources import Item, Progress, SourceType, home_key

# A commit made on another computer reaches a clone here when it next fetches, with its older committer date.
OVERLAP = 14 * 86400
MAX_FILES = 50
SHOW_BATCH = 200
GITHUB = re.compile(r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
# The user's own display settings must not change the output this module parses.
GIT_FLAGS = ("-c", "core.quotePath=false", "-c", "log.showSignature=false")
GIT_TIMEOUT = 300


@dataclass(frozen=True)
class Options:
    roots: Tuple[str, ...] = ("~/code",)
    # Author names or emails counted as the user's, besides git's global user.name and user.email.
    identities: Tuple[str, ...] = ()
    max_depth: int = 4
    window_days: int = 365


class Repo(NamedTuple):
    path: str
    id: str
    github: bool


class Commit(NamedTuple):
    repo: Repo
    sha: str
    epoch: int
    message: str
    files: Tuple[str, ...]


def run_git(args, cwd):
    proc = subprocess.run(["git", *GIT_FLAGS, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}, timeout=GIT_TIMEOUT)
    if proc.returncode:
        raise RuntimeError(mask(f"git {' '.join(args[:2])} in {cwd} exited {proc.returncode}: "
                                f"{proc.stderr.strip()[-300:]}"))
    return proc.stdout


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _identity(options):
    known = {value.casefold() for value in options.identities}
    for key in ("user.name", "user.email"):
        try:
            value = run_git(["config", "--global", "--get", key], os.path.expanduser("~")).strip()
        except RuntimeError:
            continue
        if value:
            known.add(value.casefold())
    return known


def _walk(path, home, excluded, max_depth, depth=0):
    try:
        entries = list(os.scandir(path))
    except OSError:
        return
    if any(e.name == ".git" for e in entries):
        yield path
    if depth < max_depth:
        for e in entries:
            if e.name != ".git" and e.is_dir(follow_symlinks=False) and not excluded(home_key(e.path, home)):
                yield from _walk(e.path, home, excluded, max_depth, depth + 1)


def _repos(options, excluded):
    home = os.path.realpath(os.path.expanduser("~"))
    found = {}
    for root in options.roots:
        root = os.path.realpath(os.path.expanduser(root))
        if excluded(home_key(root, home)):
            continue
        for path in _walk(root, home, excluded, options.max_depth):
            try:
                common = run_git(["rev-parse", "--path-format=absolute", "--git-common-dir"], path).strip()
            except RuntimeError:
                continue  # a linked worktree whose repository is gone
            try:
                remote = run_git(["config", "--get", "remote.origin.url"], path).strip()
            except RuntimeError:
                remote = ""
            main = os.path.dirname(common) if os.path.basename(common) == ".git" else path
            if excluded(home_key(main, home)):
                continue
            m = GITHUB.search(remote)
            found[common] = Repo(main, f"{m.group(1)}/{m.group(2)}" if m else home_key(main, home), bool(m))
    return sorted(found.values(), key=lambda r: (not r.github, r.path))


def _own_shas(repo, after, identity):
    out = run_git(["log", "--branches", "--remotes", "--tags", "--no-merges", f"--since={_iso(after)}",
                   "--format=%H%x1f%an%x1f%ae"], repo.path)
    return [sha for sha, name, email in (line.split("\x1f") for line in out.splitlines())
            if name.casefold() in identity or email.casefold() in identity]


def _commits(repo, shas):
    commits = []
    for i in range(0, len(shas), SHOW_BATCH):
        out = run_git(["show", "--no-color", "--name-status", "--format=%x1e%H%x1f%ct%x1f%B%x1f",
                       *shas[i:i + SHOW_BATCH]], repo.path)
        for record in out.split("\x1e")[1:]:
            sha, epoch, rest = record.split("\x1f", 2)
            message, files = rest.rsplit("\x1f", 1)
            commits.append(Commit(repo, sha, int(epoch), message.strip(),
                                  tuple(line for line in files.splitlines() if line.strip())))
    return commits


def _item(commit, mark):
    repo = commit.repo
    subject = commit.message.partition("\n")[0]
    files = list(commit.files[:MAX_FILES])
    if len(commit.files) > MAX_FILES:
        files.append(f"... and {len(commit.files) - MAX_FILES} more")
    meta = {"path": repo.path, "repo": repo.id, "sha": commit.sha}
    if repo.github:
        meta["url"] = f"https://github.com/{repo.id}/commit/{commit.sha}"
    return Item(f"{repo.id}@{commit.sha}", _iso(commit.epoch), f"{repo.id}: {subject}",
                commit.message + ("\n\nFiles changed:\n" + "\n".join(files) if files else ""), meta, mark)


def extract(run):
    started = time.time()
    after = started - run.options.window_days * 86400 if run.since is None else run.since - OVERLAP
    identity = _identity(run.options)
    seen = set()
    for repo in _repos(run.options, run.excludes):
        try:
            shas = [sha for sha in _own_shas(repo, after, identity) if sha not in seen]
            commits = _commits(repo, shas)
        except RuntimeError as exc:
            print(f"git: skipped {repo.path}: {exc}", file=sys.stderr)
            continue
        seen.update(shas)
        for commit in commits:
            yield _item(commit, started)
    yield Progress(started)


TYPE = SourceType(Options, extract, exclude=("node_modules", ".stversions"))
