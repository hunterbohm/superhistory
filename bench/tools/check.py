"""The check as an agent gets it, run from this repo's package against scratch databases.

Each store is a folder laid out as `Paths.under(folder)`, so kb's own `search` and `get` commands open it. Their
printed output is caught per thread. The check's model is the package's Anthropic-compatible plug-in, pointed at the
benchmark's endpoint.
"""
import hashlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import gateway
import product
from tools import Retrieved, Tool

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from kb import check, cli, search, store, summarize  # noqa: E402
from kb.ai import api  # noqa: E402
from kb.config import Config, Secret, Summarize  # noqa: E402
from kb.paths import Paths  # noqa: E402
from kb.sources import load_type  # noqa: E402

MODEL = "claude-haiku-5-5"
PARALLEL = 2
CONFIG = Config()

AGENT_TOOLS = (
    {"name": "kb_search",
     "description": "Run `kb search QUERY`: a keyword search over the user's knowledge base (email, texts, notes, "
                    "commits, GitHub threads and earlier agent sessions). Prints up to 10 hits, each with its id.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "kb_get",
     "description": "Run `kb get ID`: print the stored text of the item a hit belongs to, 20,000 characters a "
                    "page. Pass `offset` for later pages.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}, "offset": {"type": "integer"}},
                      "required": ["id"]}},
)

# Fragments of source_ids or pointers that searches must not return, such as the sessions that wrote a question set.
HIDE = []
_local = threading.local()


def code_id():
    """The package code the check runs, by hash, so uncommitted changes show."""
    digest = hashlib.sha256()
    for path in sorted(Path(ROOT, "kb").rglob("*.py")):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def model():
    """The package's plug-in, with the summarizer held to PARALLEL calls at once: the benchmark shares its plans."""
    key = Secret("env:BENCH_API_KEY" if os.environ.get("BENCH_API_KEY") else f"file:{gateway.KEY_FILE}")
    plugin = api.Anthropic("anthropic-compatible",
                           api.AnthropicCompatibleOptions(key=key, model=MODEL, base_url=gateway.BASE_URL))
    plugin.traits = plugin.traits._replace(parallel=min(plugin.traits.parallel, PARALLEL))
    return plugin


_post = api.post


def _counted_post(url, body, headers, timeout):
    """The gateway caches every prompt, and its `input_tokens` leaves the cached part out, so all three count."""
    response = _post(url, body, headers, timeout)
    sink = getattr(_local, "sink", None)
    if sink is not None:
        usage = response.get("usage") or {}
        sink.add(sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                   "cache_read_input_tokens", "output_tokens")))
    return response


api.post = _counted_post


class Counted:
    """A model that adds up the tokens of the calls it answers, hedged copies included. A call cut off at its deadline
    returns no usage, so it cannot be counted."""

    def __init__(self, inner):
        self.inner, self.name, self.model, self.traits = inner, inner.name, inner.model, inner.traits
        self.tokens = self.calls = self.running = 0
        self.changed = threading.Condition()

    def add(self, tokens):
        with self.changed:
            self.tokens += tokens
            self.calls += 1

    def ask(self, prompt, timeout):
        with self.changed:
            self.running += 1
        _local.sink = self
        try:
            return self.inner.ask(prompt, timeout)
        finally:
            _local.sink = None
            with self.changed:
                self.running -= 1
                self.changed.notify_all()

    def settle(self, timeout):
        """The tokens once every call has returned, such as a hedged copy that lost the race."""
        with self.changed:
            self.changed.wait_for(lambda: not self.running, timeout)
            return self.tokens


_search = search.search


def _visible_search(db, query, after=None, sources=(), limit=10, before=None, keep=None):
    if not HIDE:
        return _search(db, query, after, sources, limit, before, keep)

    def visible(hit):
        where = hit["source_id"] + " " + json.dumps(hit["meta"])
        return not any(part in where for part in HIDE) and (keep is None or keep(hit))
    return _search(db, query, after, sources, limit, before, visible)


search.search = _visible_search


class _ThreadStdout:
    def write(self, text):
        return (getattr(_local, "out", None) or sys.__stdout__).write(text)

    def flush(self):
        (getattr(_local, "out", None) or sys.__stdout__).flush()


sys.stdout = _ThreadStdout()


def scope(sources):
    """Which long items get summaries: each source as its type ships. Chat history from the public tests has no type
    of its own, so it takes an agent session's settings."""
    rules = {}
    for name in sources:
        kind = load_type(name)
        every, others_wrote = (kind.summarize, kind.others_wrote) if kind else (True, False)
        if every:
            rules[name] = (Summarize(True), summarize.OTHERS_ATOMS if others_wrote else summarize.ATOM_TYPES)
    return summarize.Scope(rules)


class Store:
    """One knowledge base, at kb's own paths."""

    def __init__(self, paths):
        self.paths = paths

    @classmethod
    def at(cls, db):
        """An existing kb.sqlite, read-only."""
        db = Path(db).expanduser()
        if db.name != "kb.sqlite":
            raise SystemExit(f"{db}: kb's commands open a file named kb.sqlite")
        scratch = Path(tempfile.mkdtemp(prefix="bench-live-"))
        return cls(Paths(scratch / "config.toml", db.parent, scratch / "logs", scratch / "cache"))

    @classmethod
    def build(cls, items, folder, summaries=True):
        paths = Paths.under(folder)
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(f"{paths.db}{suffix}"):
                os.remove(f"{paths.db}{suffix}")
        started = time.monotonic()
        db = store.connect(paths.db)
        for i in items:
            store.store_item(db, i["source"], i["source_id"], i["ts"], i["title"], i["content"], i["meta"])
        store.set_watermark(db, store.STATE, "last_ingest", store.now_iso())
        db.commit()
        stats = {"store_seconds": round(time.monotonic() - started, 2), "summarized": 0, "summary_failures": 0}
        if summaries:
            rules = scope(sorted({i["source"] for i in items}))
            done, failed = summarize.summarize(db, summarize.pending(db, rules), rules, model(),
                                               logging.getLogger("summarize"))
            stats.update(summarized=done, summary_failures=failed,
                         summarize_seconds=round(time.monotonic() - started - stats["store_seconds"], 1))
        stats["rows"] = dict(db.execute("SELECT kind, count(*) FROM docs GROUP BY kind").fetchall())
        db.close()
        return cls(paths), stats

    def lookup(self, message, before=()):
        counted = Counted(model())
        started = time.monotonic()
        db = store.connect(self.paths.db, readonly=True)
        try:
            found = check.lookup(db, message, list(before), "", set(), counted, counted.traits.check_budget,
                                 counted.traits.hedge_after)
            seconds = time.monotonic() - started
            return Retrieved(check.render(found.hits, CONFIG.machine), [h["source_id"] for h in found.hits], seconds,
                             None, counted.settle(counted.traits.check_budget), found.query)
        except Exception as exc:
            # The hook prints nothing when the check fails, so the agent sees nothing.
            seconds = time.monotonic() - started
            return Retrieved("", [], seconds, type(exc).__name__, counted.settle(counted.traits.check_budget))
        finally:
            db.close()

    def call(self, name, args):
        """Run `kb search` or `kb get` against this store. Returns the printed text and the source_ids shown."""
        if name == "kb_search":
            argv = ["search", "--", str(args.get("query", ""))]
        elif name == "kb_get":
            offset = args.get("offset")
            argv = ["get", str(int(args.get("id", 0)))] + ([] if offset is None else ["--offset", str(int(offset))])
        else:
            raise KeyError(name)
        ns = cli.parser().parse_args(argv)
        _local.out = _Buffer()
        try:
            ns.fn(self.paths, CONFIG, ns)
        except cli.Fail as exc:
            print(f"kb: {exc}")
        finally:
            printed, _local.out = _local.out.text(), None
        return printed, self._shown(name, args, printed)

    def _shown(self, name, args, printed):
        db = store.connect(self.paths.db, readonly=True)
        try:
            ids = [int(args["id"])] if name == "kb_get" else [
                int(word[1:]) for word in printed.split() if word.startswith("#") and word[1:].isdigit()]
            return [row[0] for rid in ids
                    for row in db.execute("SELECT source_id FROM docs WHERE id = ?", (rid,)).fetchall()]
        finally:
            db.close()


class _Buffer:
    def __init__(self):
        self.parts = []

    def write(self, text):
        self.parts.append(text)
        return len(text)

    def flush(self):
        pass

    def text(self):
        return "".join(self.parts)


class Check(Tool):
    label = f"{product.NAME} check, with kb search and kb get"
    agent_tools = AGENT_TOOLS
    _shared = {}
    _lock = threading.Lock()

    def load(self, items, workdir):
        with Check._lock:
            shared = Check._shared.get(workdir)
            if shared is None:
                shared = Check._shared[workdir] = Store.build(items, os.path.join(workdir, "check"))
        self.store, stats = shared
        return {**stats, "code": code_id()}

    def retrieve(self, question):
        return self.store.lookup(question["question"])

    def call(self, name, args):
        return self.store.call(name, args)


class Cards(Check):
    label = f"{product.NAME} check, cards only"
    agent_tools = ()
