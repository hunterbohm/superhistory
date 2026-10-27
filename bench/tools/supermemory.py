"""Supermemory's self-hosted server (docker image supermemory/supermemory), with claude-haiku-5-5 through the
gateway as its LLM and its default local embedder.

Start the server first; SUPERMEMORY_URL and SUPERMEMORY_KEY_FILE point at it.
"""
import json
import os
import time
import urllib.request

from tools import Retrieved, Tool

URL = os.environ.get("SUPERMEMORY_URL", "http://localhost:6767").rstrip("/")
KEY_FILE = os.path.expanduser(os.environ.get("SUPERMEMORY_KEY_FILE", "~/.cache/kb-bench/supermemory/data/api-key"))
NAMESPACE = "jules"
SETTLE_SECONDS = 3 * 3600

SEARCH_TOOL = {
    "name": "search_memories",
    "description": "Search the user's Supermemory (memories extracted from their email, texts, notes, commits, "
                   "GitHub threads and earlier agent sessions, plus document chunks).",
    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}


def api(path, body=None, method="POST"):
    with open(KEY_FILE, encoding="utf-8") as fh:
        key = fh.read().strip()
    req = urllib.request.Request(URL + path, data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"authorization": "Bearer " + key, "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def context_text(profile, memories, chunks):
    """The context block from Supermemory's quickstart harness: profile, related memories, then document chunks."""
    lines = (["## Profile (static)"] + [m["memory"] for m in profile.get("static", [])]
             + ["## Profile (dynamic)"] + [m["memory"] for m in profile.get("dynamic", [])]
             + ["## Related memories"] + [r["memory"] for r in memories if r.get("memory")]
             + ["## Docs"] + [r["chunk"] for r in chunks if r.get("chunk")])
    if len(lines) == 4:
        return ""
    return "\n".join(lines)


class Supermemory(Tool):
    label = "Supermemory self-hosted"
    agent_tools = (SEARCH_TOOL,)

    def load(self, items, workdir):
        """Post the month into an empty server, or reuse it when an earlier run already posted all of it, then wait
        until every document's memories are extracted."""
        started = time.monotonic()
        ids = self._documents()
        reused = len(ids) == len(items)
        if ids and not reused:
            raise RuntimeError(f"the server holds {len(ids)} documents, not 0 or {len(items)}; start it empty")
        for item in [] if reused else sorted(items, key=lambda i: i["ts"]):
            text = f"[{item['ts'][:10]} {item['source']}] {item['title']}\n{item['content']}"
            ids.append(api(f"/ns/{NAMESPACE}/document", {
                "content": text, "metadata": {"source": item["source"], "source_id": item["source_id"]}})["id"])
        pending = set(ids)
        while pending and time.monotonic() - started < SETTLE_SECONDS:
            for doc in list(pending):
                status = api(f"/v3/documents/{doc}", method="GET")
                if status["status"] in ("done", "failed") and status.get("dreamingStatus") in ("done", "failed", None):
                    pending.discard(doc)
            if pending:
                time.sleep(30)
        return {"documents": len(ids), "reused": reused, "unsettled": len(pending),
                "seconds": round(time.monotonic() - started),
                "llm": os.environ.get("SUPERMEMORY_LLM", "claude-haiku-5-5")}

    def _documents(self):
        ids, page = [], 1
        while True:
            batch = api("/v3/documents/list", {"containerTags": [NAMESPACE], "limit": 100, "page": page})["memories"]
            ids += [d["id"] for d in batch]
            if len(batch) < 100:
                return ids
            page += 1

    def _lookup(self, query):
        profile = api(f"/ns/{NAMESPACE}/profile", {}).get("profile", {})
        memories = api(f"/ns/{NAMESPACE}/search", {"query": query, "searchMode": "memories", "limit": 5})["results"]
        chunks = api(f"/ns/{NAMESPACE}/search", {"query": query, "searchMode": "chunks", "limit": 3})["results"]
        ids = [r.get("metadata", {}).get("source_id") for r in memories + chunks]
        return context_text(profile, memories, chunks), [i for i in ids if i]

    def retrieve(self, question):
        started = time.monotonic()
        try:
            text, ids = self._lookup(question["question"])
        except Exception as exc:
            return Retrieved("", [], time.monotonic() - started, type(exc).__name__)
        return Retrieved(text, ids, time.monotonic() - started)

    def call(self, name, args):
        if name != "search_memories":
            raise KeyError(name)
        text, ids = self._lookup(str(args.get("query", "")))
        return text or "No memories found.", ids
