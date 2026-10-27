"""Reading a source on another computer. The package travels to it as the program `ssh <host> python3 -` runs, and is
imported there from memory, so that computer needs Python 3.9 or newer and nothing else, and keeps no copy."""
import dataclasses
import functools
import importlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile

import kb
from kb.sources import Gone, Item, Progress

# What ssh prints when the computer is off the network, as when a laptop sleeps. A refused login is a fault.
OFF_NETWORK = re.compile(r"Operation timed out|Connection timed out|No route to host|Host is down|Network is unreachable")
STDERR_TAIL = 600
BOOTSTRAP = """
import importlib.abc, importlib.util, sys


class _Memory(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name in SOURCES:
            return importlib.util.spec_from_loader(name, self, is_package=SOURCES[name][0])
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        is_package, text = SOURCES[module.__name__]
        exec(compile(text, module.__name__.replace(".", "/") + (".py" if not is_package else "/__init__.py"), "exec"),
             module.__dict__)


sys.meta_path.insert(0, _Memory())
from kb.remote import serve
sys.exit(serve(REQUEST))
"""


class HostAway(RuntimeError):
    """ssh could not reach the computer. Its sources catch up from their watermarks when it is back."""


@functools.lru_cache(maxsize=1)
def package_sources():
    """module name -> (is a package, source text) for every module of the package except the vendored TOML parser,
    which only reads this computer's config."""
    root = pathlib.Path(kb.__file__).parent
    found = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root.parent).with_suffix("")
        if "_vendor" in rel.parts:
            continue
        is_package = rel.name == "__init__"
        name = ".".join(rel.parts[:-1] if is_package else rel.parts)
        found[name] = (is_package, path.read_text(encoding="utf-8"))
    return found


def script(request):
    return (f"SOURCES = {package_sources()!r}\nREQUEST = {request!r}\n{BOOTSTRAP}").encode("utf-8")


def encode(event):
    if isinstance(event, Item):
        return json.dumps(event._asdict())
    if isinstance(event, Gone):
        return json.dumps({"source_id": event.source_id, "delete": True, "mark": event.mark})
    return json.dumps({"mark": event.mark})


def decode(line):
    """An event from one line a remote run printed. Raises ValueError on anything else."""
    data = json.loads(line)
    if not isinstance(data, dict) or "mark" not in data:
        raise ValueError(f"not an event: {line[:200]!r}")
    if set(data) == {"mark"}:
        return Progress(data["mark"])
    if data.get("delete") is True and isinstance(data.get("source_id"), str):
        return Gone(data["source_id"], data["mark"])
    try:
        return Item(**data)
    except TypeError:
        raise ValueError(f"not an event: {line[:200]!r}") from None


def serve(request):
    """The far side: run one source with the request's options and print its events or ids, one JSON line each."""
    if sys.version_info < (3, 9):
        sys.stderr.write("kb needs Python 3.9 or newer on this computer\n")
        return 3
    from kb import config
    from kb.sources import Excludes, Run
    kind = importlib.import_module(f"kb.sources.{request['type']}").TYPE
    if sys.platform not in kind.platforms:
        sys.stderr.write(f"the {request['type']} source runs only on {', '.join(sorted(kind.platforms))}\n")
        return 3
    problems = []
    options = config.options_from(kind.Options, request["options"], "options", problems)
    if problems:
        sys.stderr.write("\n".join(problems) + "\n")
        return 3
    run = Run(options, Excludes(request["exclude"]), request["since"])
    if request["verb"] == "live_ids":
        lines = (json.dumps(sid) for sid in kind.live_ids(run))
    else:
        lines = (encode(event) for event in kind.extract(run))
    for line in lines:
        sys.stdout.write(line + "\n")
    sys.stdout.flush()
    return 0


class SshReader:
    """Runs sources on one other computer over ssh."""

    def __init__(self, host):
        self.host = host

    def events(self, source, since):
        for line in self._run(source, "extract", since):
            yield decode(line)

    def live_ids(self, source):
        ids = [json.loads(line) for line in self._run(source, "live_ids", None)]
        if not all(isinstance(sid, str) for sid in ids):
            raise ValueError("a remote listing returned something other than ids")
        return ids

    def _run(self, source, verb, since):
        request = {"verb": verb, "type": source.type_name, "since": since, "exclude": list(source.exclude),
                   "options": json.loads(json.dumps(dataclasses.asdict(source.options)))}
        with tempfile.TemporaryFile() as program, tempfile.TemporaryFile() as err:
            program.write(script(request))
            program.seek(0)
            proc = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.host.ssh,
                                     self.host.python, "-"], stdin=program, stdout=subprocess.PIPE, stderr=err,
                                    text=True, encoding="utf-8", errors="replace")
            try:
                yield from proc.stdout
            except BaseException:
                proc.kill()
                proc.wait()
                raise
            finally:
                proc.stdout.close()
            if proc.wait() != 0:
                err.seek(0)
                stderr = err.read().decode(errors="replace")[-STDERR_TAIL:]
                away = proc.returncode == 255 and OFF_NETWORK.search(stderr)
                raise (HostAway if away else RuntimeError)(f"ssh {self.host.name} exited {proc.returncode}: {stderr}")
