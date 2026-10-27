import os
from dataclasses import dataclass
from typing import Literal, Tuple
from urllib.parse import quote

from kb.sources import SourceType
from kb.sources import _files


@dataclass(frozen=True)
class Options:
    path: str
    extensions: Tuple[str, ...] = (".md",)
    # Pointers open in Obsidian when the folder is a vault (it has .obsidian/), or always, or never.
    links: Literal["auto", "obsidian", "file"] = "auto"


def _root(options):
    return os.path.expanduser(options.path)


def _url(options):
    root = _root(options)
    if options.links == "file" or (options.links == "auto" and not os.path.isdir(os.path.join(root, ".obsidian"))):
        return None
    vault = quote(os.path.basename(root.rstrip("/")), safe="")
    return lambda rel: f"obsidian://open?vault={vault}&file={quote(os.path.splitext(rel)[0], safe='')}"


def extract(run):
    return _files.extract_tree(_root(run.options), run.since, run.excludes, tuple(run.options.extensions),
                               _url(run.options))


def live_ids(run):
    return _files.live_ids(_root(run.options), run.excludes, tuple(run.options.extensions))


TYPE = SourceType(Options, extract, live_ids, exclude=(".*", "node_modules", "*.sync-conflict-*"), summarize=False)
