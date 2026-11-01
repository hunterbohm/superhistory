import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading

from kb import ai, check, config, store
from kb.paths import Paths

TRAITS = ai.Traits(check_budget=10.0, hedge_after=None, parallel=1, max_prompt_chars=600_000)
STAMP = "2026-10-04T10:00:00Z"


class FakeModel:
    name = "fake"
    model = "fake-1"

    def __init__(self, reply, traits=TRAITS):
        self.reply, self.traits = reply, traits
        self.prompts, self.timeouts = [], []
        self._lock = threading.Lock()

    def ask(self, prompt, timeout):
        with self._lock:
            self.prompts.append(prompt)
            self.timeouts.append(timeout)
        reply = self.reply(prompt) if callable(self.reply) else self.reply
        if isinstance(reply, BaseException):
            raise reply
        return reply

    @property
    def systems(self):
        return [p.system for p in self.prompts]


def unused_model():
    return FakeModel(AssertionError("the model must not be called"))


def plan_and_pick(query, use=None, plan_text=None, pick_text=None, traits=TRAITS):
    def reply(prompt):
        if prompt.system == check.QUERY_SYSTEM:
            return plan_text or json.dumps({"search": True, "query": query})
        if prompt.system != check.PICK_SYSTEM:
            raise AssertionError("unexpected prompt")
        if pick_text is not None:
            return pick_text
        count = prompt.user.split("[Records]\n", 1)[1].count("\n") + 1
        chosen = use if use is not None else range(1, count + 1)
        return json.dumps({"scores": {str(i): 2 for i in chosen}})
    return FakeModel(reply, traits)


def segment(title):
    return {"title": title, "question": f"What happened in {title}?", "summary": f"{title} was done."}


def summary_text(segments=None, atoms=None):
    segments = segments if segments is not None else [
        {"title": "Fix widget import", "question": "Why does pytest raise ModuleNotFoundError for widget?",
         "summary": "The import failed. Adding conftest.py fixed it."}]
    atoms = atoms if atoms is not None else [
        {"type": "error_fix", "text": "ModuleNotFoundError: No module named 'widget'\nadd tests/conftest.py"},
        {"type": "path", "text": "tests/conftest.py"}]
    return json.dumps({"segments": segments, "atoms": atoms})


def parse_config(text=""):
    return config.parse('machine = "test"\n' + text)


def scratch_paths(test):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    return Paths.under(tmp.name)


def open_db(test, paths):
    db = store.connect(paths.db)
    test.addCleanup(db.close)
    return db


def add_item(db, source, source_id, content, title="t", meta=None, ts=STAMP):
    store.store_item(db, source, source_id, ts, title, content, meta or {"path": f"/archive/{source_id}"})
    db.commit()


def commit_and_die(path, sql, *params):
    """Commits `sql` to the store at `path` from another process that then exits without closing it, as a killed
    writer does, so the rows stay in the store's WAL file."""
    script = ("import os, sqlite3, sys\n"
              "db = sqlite3.connect(sys.argv[1])\n"
              "db.execute('PRAGMA journal_mode=WAL')\n"
              "db.execute(sys.argv[2], tuple(sys.argv[3:]))\n"
              "db.commit()\n"
              "os._exit(0)\n")
    subprocess.run([sys.executable, "-c", script, os.fspath(path), sql, *params], check=True)


def store_files(folder):
    """Each file in `folder` and a hash of its bytes, but for the lock and the WAL index (-shm), which every reader
    writes."""
    files = {}
    for name in sorted(os.listdir(folder)):
        if name != "kb.lock" and not name.endswith("-shm"):
            with open(os.path.join(folder, name), "rb") as fh:
                files[name] = (os.path.getsize(os.path.join(folder, name)), hashlib.sha256(fh.read()).hexdigest()[:16])
    return files
