"""Mem0 loaded and searched the way Mem0's own LoCoMo evaluation does it (mem0ai/mem0 at aae5989,
evaluation/src/memzero/add.py and search.py), on the month adapter's open-source setup.

Each speaker gets a user id. Each session is added two messages at a time, once with that speaker's lines as the user
and the other's as the assistant, and once the other way round, with the session's date as metadata. A lookup
searches both speakers, 10 memories each, and lists each memory after its date. The month's one add per session
loses about one LoCoMo session in five to extraction replies that do not parse; these smaller adds are Mem0's own
answer to that.
"""
import json
import logging
import os
import re
import shutil
import threading
import time

from limits import RateLimited
from tools import Retrieved
from tools.mem0 import Mem0

BATCH = 2       # add.py's default batch size
TOP_K = 10      # search.py's default
PARSE_ERROR = "Error parsing extraction response"


class _Count(logging.Handler):
    """Counts Mem0's extraction replies that did not parse; logging holds the handler's lock around emit."""

    def __init__(self):
        super().__init__()
        self.n = 0

    def emit(self, record):
        if PARSE_ERROR in record.getMessage():
            self.n += 1


class LocomoMem0(Mem0):
    label = "Mem0 open source, loaded as in Mem0's LoCoMo evaluation"

    def __init__(self, sample, send=None, build=True):
        """send(model, call) makes one model call under the run's limits; build=False only opens a built store."""
        conv = sample["conversation"]
        self.sample, self.users = sample, (conv["speaker_a"], conv["speaker_b"])
        self.send, self.build, self.limited = send, build, 0

    def load(self, items, folder):
        """Build the store in folder, or open it when an earlier run finished building it there."""
        done = os.path.join(folder, "loaded.json")
        if not os.path.exists(done) and not self.build:
            raise RuntimeError(f"Mem0's store for {self.sample['sample_id']} is not built; build it with --load-only")
        if not os.path.exists(done) and os.path.isdir(folder):
            shutil.rmtree(folder)
        os.makedirs(folder, exist_ok=True)
        stats = super().load([], folder)
        if self.send:
            self._gate()
        if os.path.exists(done):
            with open(done, encoding="utf-8") as fh:
                return {**json.load(fh), "reused": True}
        count = _Count()
        logging.getLogger("mem0.memory.main").addHandler(count)
        started, failed = time.monotonic(), []
        try:
            views = [threading.Thread(target=self._add_view, args=(user, failed)) for user in self.users]
            for view in views:
                view.start()
            for view in views:
                view.join()
        finally:
            logging.getLogger("mem0.memory.main").removeHandler(count)
        if self.limited:
            # Mem0 may log a failed model call and store nothing, so a build that met a limit is not kept.
            shutil.rmtree(folder)
            raise RateLimited(f"{self.limited} of Mem0's model calls met a rate limit; the store was not kept")
        stored = sum(len(self.memory.get_all(filters={"user_id": u}, top_k=100_000)["results"]) for u in self.users)
        stats = {**stats, "users": list(self.users), "batch": BATCH, "top_k": TOP_K, "memories": stored,
                 "add_failures": len(failed), "extraction_parse_errors": count.n,
                 "seconds": round(time.monotonic() - started)}
        with open(done, "w", encoding="utf-8") as fh:
            json.dump(stats, fh)
        return stats

    def _add_view(self, user, failed):
        conv = self.sample["conversation"]
        for n in sorted(int(k.split("_")[1]) for k in conv if re.fullmatch(r"session_\d+", k)):
            when = conv[f"session_{n}_date_time"]
            messages = [{"role": "user" if t["speaker"] == user else "assistant",
                         "content": f"{t['speaker']}: {t['text']}"} for t in conv[f"session_{n}"]]
            meta = {"timestamp": when, "source_id": f"{self.sample['sample_id']}/session_{n}"}
            for i in range(0, len(messages), BATCH):
                if self.limited:
                    return
                for attempt in range(3):
                    try:
                        self.memory.add(messages[i:i + BATCH], user_id=user, metadata=meta)
                        break
                    except RateLimited:
                        return
                    except Exception:
                        if attempt == 2:
                            failed.append(f"{user} session {n} batch {i // BATCH}")
                        time.sleep(1)

    def _gate(self):
        """Send Mem0's model calls through the run's limits, with the OpenAI SDK's own retries off, so a rate limit
        is never retried in a loop."""
        client = self.memory.llm.client.with_options(max_retries=0)
        create = client.chat.completions.create

        def gated(**kwargs):
            try:
                return self.send(kwargs["model"], lambda: create(**kwargs))
            except RateLimited:
                self.limited += 1
                raise

        client.chat.completions.create = gated
        self.memory.llm.client = client

    def _both(self, query):
        found, sections = [], []
        for user in self.users:
            results = self.memory.search(query, filters={"user_id": user}, top_k=TOP_K)["results"]
            found += results
            lines = [f"{(r.get('metadata') or {}).get('timestamp', '')}: {r['memory']}" for r in results]
            sections.append(f"Memories for user {user}:\n" + "\n".join(lines))
        ids = [(r.get("metadata") or {}).get("source_id") for r in found]
        return ("\n\n".join(sections) if found else ""), list(dict.fromkeys(i for i in ids if i))

    def retrieve(self, question):
        started = time.monotonic()
        try:
            text, ids = self._both(question["question"])
        except Exception as exc:
            return Retrieved("", [], time.monotonic() - started, type(exc).__name__)
        return Retrieved(text, ids, time.monotonic() - started)

    def call(self, name, args):
        if name != "search_memory":
            raise KeyError(name)
        text, ids = self._both(str(args.get("query", "")))
        return text or "No memories found.", ids
