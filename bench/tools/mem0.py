"""Mem0's open-source library (pip mem0ai), self-hosted. Its default models need an OpenAI key, so the LLM is
claude-haiku-5-5 through the gateway's OpenAI-style endpoint and the embedder is a local model.

Run the harness with a Python that has mem0ai and sentence-transformers installed.
"""
import importlib.metadata
import os
import time

import gateway
from tools import Retrieved, Tool

LLM = "claude-haiku-5-5"
EMBEDDER = "BAAI/bge-base-en-v1.5"
USER = "jules"
# Mem0's README example searches with top_k=3 and lists the memories under "User Memories:".
TOP_K = 3

SEARCH_TOOL = {
    "name": "search_memory",
    "description": "Search the user's stored memories (facts Mem0 extracted from their email, texts, notes, "
                   "commits, GitHub threads and earlier agent sessions). Returns the most relevant memories.",
    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}


def memories_text(results):
    if not results:
        return ""
    return "User Memories:\n" + "\n".join(f"- {r['memory']}" for r in results)


class Mem0(Tool):
    label = "Mem0 open source"
    agent_tools = (SEARCH_TOOL,)

    def load(self, items, workdir):
        os.environ.setdefault("MEM0_TELEMETRY", "False")
        from mem0 import Memory
        self.memory = Memory.from_config({
            "llm": {"provider": "openai", "config": {
                "model": LLM, "openai_base_url": gateway.BASE_URL + "/v1", "api_key": gateway._key(),
                "temperature": 0}},
            "embedder": {"provider": "huggingface", "config": {"model": EMBEDDER}},
            "vector_store": {"provider": "qdrant", "config": {
                "path": os.path.join(workdir, "mem0-qdrant"), "collection_name": "month",
                "embedding_model_dims": 768, "on_disk": True}},
            "history_db_path": os.path.join(workdir, "mem0-history.db"),
        })
        started, failed = time.monotonic(), 0
        for item in sorted(items, key=lambda i: i["ts"]):
            text = f"[{item['ts'][:10]} {item['source']}] {item['title']}\n{item['content']}"
            try:
                self.memory.add([{"role": "user", "content": text}], user_id=USER,
                                metadata={"source": item["source"], "source_id": item["source_id"]})
            except Exception:
                failed += 1
        stored = self.memory.get_all(filters={"user_id": USER}, top_k=100_000)["results"]
        return {"version": importlib.metadata.version("mem0ai"), "llm": LLM, "embedder": EMBEDDER,
                "memories": len(stored), "add_failures": failed, "seconds": round(time.monotonic() - started)}

    def _search(self, query):
        return self.memory.search(query, filters={"user_id": USER}, top_k=TOP_K)["results"]

    def retrieve(self, question):
        started = time.monotonic()
        try:
            results = self._search(question["question"])
        except Exception as exc:
            return Retrieved("", [], time.monotonic() - started, type(exc).__name__)
        ids = [r.get("metadata", {}).get("source_id") for r in results]
        return Retrieved(memories_text(results), [i for i in ids if i], time.monotonic() - started)

    def call(self, name, args):
        if name != "search_memory":
            raise KeyError(name)
        results = self._search(str(args.get("query", "")))
        ids = [r.get("metadata", {}).get("source_id") for r in results]
        return memories_text(results) or "No memories found.", [i for i in ids if i]
