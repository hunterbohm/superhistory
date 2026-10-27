"""The source contract. A source type is a module here that defines TYPE, a SourceType:

    @dataclass(frozen=True)
    class Options:            # the config keys of a [sources.NAME] table, with their defaults
        path: str

    def extract(run):         # yields Item, Gone or Progress events
        ...

    TYPE = SourceType(Options, extract, live_ids=None, exclude=(".*",))

The module's name is the type's name, so adding a type needs no list edit. Sources only read: they never write to,
lock or change what they read, and they drop 2FA and password-reset messages one message at a time.
"""
import fnmatch
import importlib
import os
import pkgutil
import re
from typing import Any, Callable, FrozenSet, Iterable, Iterator, NamedTuple, Optional, Tuple, Union

Mark = Union[int, float, str]

# Whitespace inside one line: \s also matches U+2028, U+2029, U+0085, the control characters that break lines, and
# \x1f, none of which is a space in mail or YAML.
SPACE = r"[^\S\n\r\x0b\x0c\x1c-\x1f\x85\u2028\u2029]"


class Item(NamedTuple):
    source_id: str      # unique within the source; the runner adds "<machine>:"
    ts: str             # ISO 8601 UTC
    title: str
    content: str
    meta: dict          # url and/or path for the pointer, and anything else worth keeping
    mark: Mark          # how far this run read; the largest mark of a run is the next run's `since`


class Gone(NamedTuple):
    """The item left the source, such as a mail thread now skipped."""
    source_id: str
    mark: Mark


class Progress(NamedTuple):
    """Moves the watermark past a stretch with nothing to store."""
    mark: Mark


Event = Union[Item, Gone, Progress]


class Run(NamedTuple):
    options: Any                  # the type's Options, parsed from the config
    excludes: "Excludes"
    since: Optional[Mark]         # the last run's largest mark, or None on the first run
    model: Any = None             # the AI plug-in, for types that need one


class SourceType(NamedTuple):
    Options: type
    extract: Callable[[Run], Iterator[Event]]
    # Every current source_id, so removed items get pruned.
    live_ids: Optional[Callable[[Run], Iterable[str]]] = None
    exclude: Tuple[str, ...] = ()
    summarize: bool = True
    # Text other people wrote (mail, texts): its summaries keep only decisions and paths, never "a command that worked".
    others_wrote: bool = False
    needs_model: bool = False
    platforms: FrozenSet[str] = frozenset({"darwin", "linux"})


def type_names():
    return tuple(sorted(m.name for m in pkgutil.iter_modules(__path__) if not m.name.startswith("_")))


def load_type(name):
    """The SourceType named `name`, or None."""
    if name not in type_names():
        return None
    return importlib.import_module(f"{__name__}.{name}").TYPE


class Excludes:
    """Glob patterns over a slash-separated key, such as a note's path inside its folder. A pattern without a slash
    matches any one part of the key ("node_modules", ".*"). A pattern with one matches from the start of the key, where
    * stays inside one part and ** spans parts ("Journal/2025", "Archive/**/old"). A match on a folder covers
    everything under it."""

    def __init__(self, patterns):
        self.patterns = tuple(patterns)
        self._names = [p for p in self.patterns if "/" not in p.strip("/")]
        self._paths = [_path_regex(p.strip("/")) for p in self.patterns if "/" in p.strip("/")]

    def __call__(self, key):
        parts = [part for part in key.split("/") if part]
        if any(fnmatch.fnmatchcase(part, p.strip("/")) for part in parts for p in self._names):
            return True
        return any(r.fullmatch("/".join(parts[:i])) for r in self._paths for i in range(1, len(parts) + 1))


def home_key(path, home=None):
    """A path with the home folder written as "~", which is what patterns such as "~/code/old-client" match."""
    path = str(path or "")
    home = (home or os.path.expanduser("~")).rstrip("/")
    if home and path.rstrip("/") == home:
        return "~"
    if home and path.startswith(home + "/"):
        return "~" + path[len(home):]
    return path


def _path_regex(pattern):
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))
