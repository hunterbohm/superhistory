#!/usr/bin/env python3
"""Scans one commit's text files and file names for the lines of a patterns file, apart from the listed exceptions.

    python3 tools/release_scan.py [--exceptions PRIVATE_FILE] PATTERNS_FILE [COMMIT]

PATTERNS_FILE is read as `grep -i -E -f` reads it. It stays outside the repo, because it names what must not ship.
tools/release_exceptions.tsv in COMMIT lists what may match, one row per line, tab-separated: the file's path, the exact
text, and the reason. Blank lines and lines starting with # are skipped. PRIVATE_FILE has the same form and stays
outside the repo beside the patterns file. The tracked list holds every exception that may be named in public, such as
the made-up phone numbers and the repo's own address, and PRIVATE_FILE holds only a text that should not be named in
public, such as an ordinary word a pattern names, so that no tracked file holds it. An exception removes its text from
that file's lines before they are matched again, so a line passes only when nothing else on it matches. The list is
scanned like any file, with every row's text removed from it. A file name is never excepted, and a binary file is not
read. The files that bench/DATASET_ROWS.txt lists, which hold other people's datasets and which bench/README.md says the
public tree leaves out, are not read.

Prints each remaining hit as path:line and the start of the line. The exit code is 0 when there is none and 1 when
there is any.
"""
import subprocess
import sys

LIST = "tools/release_exceptions.tsv"
ROWS = "bench/DATASET_ROWS.txt"
SHOW = 100


def git(*args):
    return subprocess.run(("git",) + args, capture_output=True, check=True).stdout


def rows(text):
    """An exceptions list's rows, as {path: [text, ...]}."""
    found = {}
    for line in text.splitlines():
        if line.strip() and not line.startswith("#"):
            path, string = line.split("\t")[:2]
            found.setdefault(path, []).append(string.encode("utf-8"))
    return found


def exceptions(commit):
    try:
        text = git("show", commit + ":" + LIST)
    except subprocess.CalledProcessError:
        return {}
    return rows(text.decode("utf-8"))


def dataset_rows(commit):
    """The paths bench/README.md says the public tree leaves out: other people's datasets, quoted in results files."""
    try:
        text = git("show", commit + ":" + ROWS)
    except subprocess.CalledProcessError:
        return set()
    return {line.split("\t")[0] for line in text.decode("utf-8").splitlines() if line.strip() and not line.startswith("#")}


def matching(patterns, lines):
    """The indexes of `lines` that match."""
    if not lines:
        return []
    proc = subprocess.run(["grep", "-a", "-n", "-i", "-E", "-f", patterns], input=b"\n".join(lines) + b"\n",
                          capture_output=True, env={"LC_ALL": "C"})
    if proc.returncode > 1:
        sys.exit("release_scan.py: grep failed on the patterns file: " + proc.stderr.decode("utf-8", "replace"))
    return [int(row.split(b":", 1)[0]) - 1 for row in proc.stdout.splitlines()]


def scan(patterns, commit, private=None):
    commit = git("rev-parse", "--verify", commit + "^{commit}").decode().strip()
    allowed, skipped = exceptions(commit), dataset_rows(commit)
    every = [s for strings in allowed.values() for s in strings]  # the tracked list may hold only its own rows' texts
    for path, strings in (private or {}).items():
        allowed.setdefault(path, []).extend(strings)
    found = subprocess.run(["git", "grep", "-I", "-i", "-n", "-z", "-E", "-f", patterns, commit],
                           capture_output=True, env={"LC_ALL": "C"})
    if found.returncode > 1:
        sys.exit("release_scan.py: git grep failed: " + found.stderr.decode("utf-8", "replace"))
    hits = []
    for record in found.stdout.split(b"\n"):
        if record:
            path, number, text = record.split(b"\0", 2)
            path = path.decode("utf-8", "replace")[len(commit) + 1:]
            if path not in skipped:
                hits.append((path, number.decode(), text))
    rest = []
    for path, number, text in hits:
        for string in sorted(every if path == LIST else allowed.get(path, []), key=len, reverse=True):
            text = text.replace(string, b"")
        rest.append(text)
    out = ["%s:%s: %s" % (path, number, text[:SHOW].decode("utf-8", "replace"))
           for path, number, text in (hits[i] for i in matching(patterns, rest))]
    names = git("ls-tree", "-r", "--name-only", "-z", commit).split(b"\0")[:-1]
    out += ["%s: the file name matches" % names[i].decode("utf-8", "replace") for i in matching(patterns, names)]
    return out


def main(argv):
    args, private = argv[1:], None
    if args[:1] == ["--exceptions"] and len(args) > 1:
        with open(args[1], encoding="utf-8") as fh:
            private = rows(fh.read())
        args = args[2:]
    if len(args) not in (1, 2):
        sys.exit(__doc__)
    hits = scan(args[0], args[1] if len(args) == 2 else "HEAD", private)
    for hit in hits:
        print(hit)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
