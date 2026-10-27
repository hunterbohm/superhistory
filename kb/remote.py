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
import traceback

import kb
from kb.mask import holds_secret, mask, mask_all
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
    """ssh could not reach the computer."""


@functools.lru_cache(maxsize=1)
def package_sources():
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
        return json.dumps({**event._asdict(), "ts": mask(str(event.ts)), "title": mask(event.title),
                           "content": mask(event.content), "meta": mask_all(event.meta)})
    if isinstance(event, Gone):
        return json.dumps({"source_id": event.source_id, "delete": True, "mark": event.mark})
    return json.dumps({"mark": event.mark})


def decode(line):
    """An event from one line a remote run printed. Raises ValueError on anything else."""
    data = json.loads(line)
    if not isinstance(data, dict) or "mark" not in data:
        raise ValueError("not an event: " + repr(mask_all(data))[:200])
    if set(data) == {"mark"}:
        return Progress(data["mark"])
    if data.get("delete") is True and isinstance(data.get("source_id"), str):
        return Gone(data["source_id"], data["mark"])
    try:
        return Item(**data)
    except TypeError:
        raise ValueError("not an event: " + repr(mask_all(data))[:200]) from None


def serve(request):
    """The far side: run one source with the request's options and print its events or ids, one JSON line each. A
    failure prints a masked traceback, because the reading side quotes this computer's stderr."""
    try:
        return _serve(request)
    except Exception:
        sys.stderr.write(mask(traceback.format_exc()))
        return 1


def _serve(request):
    if sys.version_info < (3, 9):
        sys.stderr.write("kb needs Python 3.9 or newer on this computer\n")
        return 3
    from kb import config
    from kb.sources import Excludes, Run
    kind = importlib.import_module(f"kb.sources.{request['type']}").TYPE
    if sys.platform not in kind.platforms:
        sys.stderr.write(mask(f"the {request['type']} source runs only on {', '.join(sorted(kind.platforms))}\n"))
        return 3
    problems = []
    options = config.options_from(kind.Options, request["options"], "options", problems)
    if problems:
        sys.stderr.write(mask("\n".join(problems)) + "\n")
        return 3
    run = Run(options, Excludes(request["exclude"]), request["since"])
    # Items whose id looks like a secret never leave this computer; the reading side would not store them either.
    if request["verb"] == "live_ids":
        lines = (json.dumps(sid) for sid in kind.live_ids(run) if not holds_secret(sid))
    else:
        lines = (encode(event) for event in kind.extract(run) if not holds_secret(json.dumps(event.mark, default=str))
                 and (isinstance(event, Progress) or not holds_secret(event.source_id)))
    for line in lines:
        sys.stdout.write(line + "\n")
    sys.stdout.flush()
    return 0


class SshReader:
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
            try:
                proc = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.host.ssh,
                                         self.host.python, "-"], stdin=program, stdout=subprocess.PIPE, stderr=err,
                                        text=True, encoding="utf-8", errors="replace")
            except OSError as exc:
                raise RuntimeError(mask(f"ssh did not start: {exc}")) from None
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
                stderr = mask(err.read().decode(errors="replace"))[-STDERR_TAIL:]
                away = proc.returncode == 255 and OFF_NETWORK.search(stderr)
                failed = f"ssh {self.host.name} exited {proc.returncode}: {stderr}"
                raise (HostAway if away else RuntimeError)(failed)
