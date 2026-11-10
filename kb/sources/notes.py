import os
from dataclasses import dataclass, field
from typing import Dict, Literal, Tuple
from urllib.parse import quote

from kb.sources import SourceType
from kb.sources import _files


@dataclass(frozen=True)
class Options:
    path: str
    extensions: Tuple[str, ...] = (".md",)
    # Pointers open in Obsidian when the folder is a vault (it has .obsidian/), or always, or never.
    links: Literal["auto", "obsidian", "file"] = "auto"
    # Folders kept elsewhere, read as if they were at a path inside this one: {"07 - Agents" = "~/agents"}. Their
    # notes keep that path in their ids, headers and excludes, so a folder moved out of a vault reads as it did, and
    # get only a path, not a link into the vault. A folder still at that path inside is read there instead.
    mounts: Dict[str, str] = field(default_factory=dict)


def _root(options):
    return os.path.expanduser(options.path)


def _url(options):
    root = _root(options)
    if options.links == "file" or (options.links == "auto" and not os.path.isdir(os.path.join(root, ".obsidian"))):
        return None
    vault = quote(os.path.basename(root.rstrip("/")), safe="")
    return lambda rel: f"obsidian://open?vault={vault}&file={quote(os.path.splitext(rel)[0], safe='')}"


def _mounts(options):
    """(folder, the path it is read at) for each mount whose path holds no folder inside this one."""
    root = _root(options)
    for inside, outside in sorted(options.mounts.items()):
        if os.path.isabs(inside) or ".." in inside.split("/") or not inside.strip("/"):
            raise ValueError(f"a mount's path must be a folder inside {options.path}: {inside!r}")
        inside = inside.strip("/")
        if not os.path.isdir(os.path.join(root, inside)):
            yield os.path.expanduser(outside), inside


def extract(run):
    exts, label = tuple(run.options.extensions), os.path.basename(_root(run.options).rstrip("/"))
    yield from _files.extract_tree(_root(run.options), run.since, run.excludes, exts, _url(run.options))
    for folder, inside in _mounts(run.options):
        if not run.excludes(inside) and os.path.isdir(folder):
            yield from _files.extract_tree(folder, run.since, run.excludes, exts, prefix=inside, label=label)


def live_ids(run):
    """Every note's id. A mount whose folder is missing raises, as an unreadable folder does, so its notes are kept."""
    exts = tuple(run.options.extensions)
    ids = _files.live_ids(_root(run.options), run.excludes, exts)
    for folder, inside in _mounts(run.options):
        if not run.excludes(inside):
            ids += _files.live_ids(folder, run.excludes, exts, prefix=inside)
    return ids


TYPE = SourceType(Options, extract, live_ids, exclude=(".*", "node_modules", "*.sync-conflict-*"), summarize=False)
