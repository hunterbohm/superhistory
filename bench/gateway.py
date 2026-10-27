"""Model calls for the benchmarks, through any endpoint that speaks Anthropic's Messages API.

BENCH_BASE_URL defaults to a local CLIProxyAPI gateway; BENCH_API_KEY, or the gateway's key file, authenticates.
"""
import json
import os
import random
import re
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BENCH_BASE_URL", "http://127.0.0.1:8317").rstrip("/")
KEY_FILE = os.path.expanduser("~/.config/cli-gateway/client.key")
ATTEMPTS = 6


def _key():
    if os.environ.get("BENCH_API_KEY"):
        return os.environ["BENCH_API_KEY"]
    with open(KEY_FILE, encoding="utf-8") as fh:
        return fh.read().strip()


def post(path, body, timeout=300):
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01",
               "authorization": "Bearer " + _key()}
    data = json.dumps(body).encode()
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        try:
            req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if (e.code < 500 and e.code != 429) or last:
                raise RuntimeError(f"HTTP {e.code} from {BASE_URL}{path}: {e.read()[:300].decode(errors='replace')}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if last:
                raise RuntimeError(f"cannot reach {BASE_URL}: {e}") from None
        time.sleep(min(60.0, 2 ** attempt + random.random()))


def text(response):
    return "".join(b.get("text", "") for b in response.get("content") or [] if b.get("type") == "text").strip()


def ask(model, system, user, max_tokens=2000):
    return post("/v1/messages", {"model": model, "max_tokens": max_tokens, "system": system,
                                 "messages": [{"role": "user", "content": user}]})


def ask_json(model, system, user, max_tokens=2000):
    """The first JSON object in the model's answer, asking up to three times."""
    for _ in range(3):
        found = re.search(r"\{.*\}", text(ask(model, system, user, max_tokens)), re.S)
        try:
            return json.loads(found.group(0))
        except (AttributeError, ValueError):
            continue
    raise ValueError(f"{model} gave no JSON object")


def count_tokens(model, content):
    if not content:
        return 0
    return post("/v1/messages/count_tokens",
                {"model": model, "messages": [{"role": "user", "content": content}]})["input_tokens"]
