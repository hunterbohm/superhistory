"""Mem0's open-source library (pip mem0ai), self-hosted. Its default models need an OpenAI key, so the LLM is
claude-haiku-5-5 through the gateway's OpenAI-style endpoint and the embedder is a local model.

Run the harness with a Python that has mem0ai and sentence-transformers installed.
"""
import importlib.metadata
import json
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


def rate_limit(exc):
    """Whether Mem0's error, or one it wraps, is a rate limit. Its LLMError wraps the OpenAI client's, which keeps
    the reply in `body`, not in its text."""
    while exc is not None:
        if isinstance(exc, gateway.RateLimited) or gateway.is_rate_limit(
                getattr(exc, "status_code", None), f"{exc} {getattr(exc, 'body', '')}"):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def no_retry_on_limits(client):
    """The OpenAI client retries a 429 twice by itself, and hands Mem0 a 200 that says the model is cooling down as a
    completion with no choices, which Mem0 fails to parse and counts as one failed add. Both now raise RateLimited; a
    busy server is still retried."""
    should_retry = client._should_retry

    def retry(response):
        return not gateway.is_rate_limit(response.status_code, response.text) and should_retry(response)
    client._should_retry = retry
    create = client.chat.completions.create

    def checked(*args, **kwargs):
        reply = create(*args, **kwargs)
        raw = reply.model_dump() if hasattr(reply, "model_dump") else reply
        if not (isinstance(raw, dict) and raw.get("choices")) and gateway.limited_reply(raw):
            gateway.limited(f"Mem0's model call: {json.dumps(raw, default=str)[:300]}")
        return reply
    client.chat.completions.create = checked


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
        no_retry_on_limits(self.memory.llm.client)
        started, failed = time.monotonic(), 0
        for item in sorted(items, key=lambda i: i["ts"]):
            gateway.check_limited()
            text = f"[{item['ts'][:10]} {item['source']}] {item['title']}\n{item['content']}"
            try:
                self.memory.add([{"role": "user", "content": text}], user_id=USER,
                                metadata={"source": item["source"], "source_id": item["source_id"]})
            except Exception as exc:
                if rate_limit(exc):
                    gateway.limited(f"Mem0's model call: {exc}")
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
