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


# Request headers the transport leaves to urllib, which sets its own; it asks for no compression, which urllib leaves
# undone.
OWN_HEADERS = {"host", "content-length", "accept-encoding", "connection", "transfer-encoding"}


def gateway_client():
    """An OpenAI client whose calls go through gateway.urlopen, which reads each reply for a rate limit before the
    client sees it: the client retried a 429 twice by itself, and a body that stalled before it consulted any hook.
    A limit raises gateway.RateLimited, which the client does not retry; a busy server or a dropped connection is
    still retried as the client always did."""
    import http.client
    import urllib.error
    import urllib.request

    import httpx2
    import openai

    class Transport(httpx2.BaseTransport):
        def handle_request(self, request):
            gateway.check_limited()
            timeout = (request.extensions.get("timeout") or {}).get("read") or 600
            headers = {k: v for k, v in request.headers.items() if k.lower() not in OWN_HEADERS}
            sent = urllib.request.Request(str(request.url), data=request.read() or None, headers=headers,
                                          method=request.method)
            try:
                with gateway.urlopen(sent, timeout) as reply:
                    status, got, body = reply.status, reply.headers, reply.read()
            except urllib.error.HTTPError as e:
                status, got, body = e.code, e.headers, e.read()
            except (TimeoutError, OSError, http.client.HTTPException) as e:
                error = httpx2.ReadTimeout if isinstance(e, TimeoutError) else httpx2.ConnectError
                raise error(str(e), request=request) from e
            kept = [(k, v) for k, v in (got or {}).items() if k.lower() not in OWN_HEADERS | {"content-encoding"}]
            return httpx2.Response(status, headers=kept, content=body, request=request)

    return openai.OpenAI(api_key=gateway._key(), base_url=gateway.base_url() + "/v1",
                         http_client=httpx2.Client(transport=Transport()))


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
                "model": LLM, "openai_base_url": gateway.base_url() + "/v1", "api_key": gateway._key(),
                "temperature": 0}},
            "embedder": {"provider": "huggingface", "config": {"model": EMBEDDER}},
            "vector_store": {"provider": "qdrant", "config": {
                "path": os.path.join(workdir, "mem0-qdrant"), "collection_name": "month",
                "embedding_model_dims": 768, "on_disk": True}},
            "history_db_path": os.path.join(workdir, "mem0-history.db"),
        })
        self.memory.llm.client = gateway_client()
        started, failed = time.monotonic(), 0
        for item in sorted(items, key=lambda i: i["ts"]):
            gateway.check_limited()
            text = f"[{item['ts'][:10]} {item['source']}] {item['title']}\n{item['content']}"
            try:
                self.memory.add([{"role": "user", "content": text}], user_id=USER,
                                metadata={"source": item["source"], "source_id": item["source_id"]})
            except Exception:
                # A rate limit inside Mem0's model call reaches here wrapped in Mem0's own error; it stops the load.
                gateway.check_limited()
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
