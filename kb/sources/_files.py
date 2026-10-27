import os
import re
import time
from datetime import datetime, timezone

from kb.sources import SPACE, Item

MAX_BYTES = 1_000_000
FILENAME_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T_-](\d{2})(\d{2}))?(?!\d)")
FRONT_DATE = re.compile(rf"^(?:date|created){SPACE}*:{SPACE}*(.*)$", re.I)
ANY_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?")


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(m):
    y, mo, d, hh, mm = m.groups()
    try:
        if hh is None:
            return _iso(datetime(int(y), int(mo), int(d), tzinfo=timezone.utc))
        return _iso(datetime(int(y), int(mo), int(d), int(hh), int(mm)))
    except ValueError:
        return None


def frontmatter(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end > 0 else ""


def item_ts(stem, text, mtime):
    m = FILENAME_DATE.match(stem)
    if m:
        ts = _stamp(m)
        if ts:
            return ts
    for line in frontmatter(text).split("\n"):  # YAML breaks lines at line feeds, not at U+2028
        fm = FRONT_DATE.match(line)
        m = fm and ANY_DATE.search(fm.group(1))
        if m:
            ts = _stamp(m)
            if ts:
                return ts
    return _iso(datetime.fromtimestamp(mtime, timezone.utc))


def walk(root, excluded, exts, since=None, complete=False):
    """Yield (absolute path, relative posix path, stat, changed) for every wanted file.

    A rename keeps a file's mtime, and renaming a folder touches none of the files inside, so a file also counts
    as changed when its own ctime or any ancestor folder's mtime/ctime is newer than `since`.
    A complete walk raises on a folder it cannot read.
    """
    def newer(st, floor):
        return floor is None or max(st.st_mtime, st.st_ctime) > floor

    def scan(path, rel, touched):
        try:
            entries = list(os.scandir(path))
        except OSError:
            if complete:
                raise
            return
        for e in entries:
            erel = f"{rel}/{e.name}" if rel else e.name
            if e.is_symlink() or excluded(erel):
                continue
            if e.is_dir():
                yield from scan(e.path, erel, touched or newer(e.stat(), since))
            elif e.name.endswith(exts):
                st = e.stat()
                if st.st_size <= MAX_BYTES:
                    yield e.path, erel, st, touched or newer(st, since)
    yield from scan(root, "", newer(os.stat(root), since))


def extract_tree(root, since, excluded, exts, url=None):
    started = time.time() - 60
    label = os.path.basename(root.rstrip("/"))
    for path, rel, st, changed in walk(root, excluded, exts, since):
        if not changed:
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if "\0" in text:
            continue
        stem = os.path.splitext(os.path.basename(rel))[0]
        folder = rel.rpartition("/")[0]
        meta = {"path": path}
        if url:
            meta["url"] = url(rel)
        yield Item(rel, item_ts(stem, text, st.st_mtime), stem, f"{label}/{folder}".rstrip("/") + "\n\n" + text,
                   meta, started)


def live_ids(root, excluded, exts):
    return [rel for _, rel, _, _ in walk(root, excluded, exts, complete=True)]
