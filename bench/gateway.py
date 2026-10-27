"""Model calls for the benchmarks, through any endpoint that speaks Anthropic's Messages API.

BENCH_BASE_URL defaults to a local CLIProxyAPI gateway; BENCH_API_KEY, or the gateway's key file, authenticates.

The benchmarks share their model plans with other work, so a rate limit is never retried: the first HTTP 429, or a
reply that says the model is cooling down, raises RateLimited and sets LIMITED, and the runners start no new question
after that. Run the same command again once the limit has passed; it runs only the questions with no row.
"""
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BENCH_BASE_URL", "http://127.0.0.1:8317").rstrip("/")
KEY_FILE = os.path.expanduser("~/.config/cli-gateway/client.key")
ATTEMPTS = 6
LIMITED = threading.Event()
WAIT_MINUTES = 30


class RateLimited(RuntimeError):
    """A model plan said no more for now. Nothing retries it."""


def limited(detail):
    """Record a rate limit and raise RateLimited."""
    LIMITED.set()
    raise RateLimited(f"rate limited ({detail.strip()[:200]}); start nothing new for {WAIT_MINUTES} minutes, then run "
                      "the same command once to fill in the questions with no row")


def check_limited():
    """Raises RateLimited when this process has seen a rate limit, so a runner starts no new question."""
    if LIMITED.is_set():
        raise RateLimited(f"not started: this run hit a rate limit; run the same command again in {WAIT_MINUTES} "
                          "minutes")


def is_rate_limit(code, detail):
    return code == 429 or "cooling down" in detail.lower()


def limited_reply(response):
    """Whether a reply that came back as JSON is an error saying the plan is rate limited, as some gateways send
    with status 200."""
    if not isinstance(response, dict) or (response.get("type") != "error" and "error" not in response):
        return False
    text = json.dumps(response.get("error", response))
    return "rate_limit" in text or "cooling down" in text.lower()


def _key():
    if os.environ.get("BENCH_API_KEY"):
        return os.environ["BENCH_API_KEY"]
    with open(KEY_FILE, encoding="utf-8") as fh:
        return fh.read().strip()


def post(path, body, timeout=300):
    check_limited()
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01",
               "authorization": "Bearer " + _key()}
    data = json.dumps(body).encode()
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        try:
            req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                reply = json.load(resp)
            if limited_reply(reply):
                limited(f"from {BASE_URL}{path}: {json.dumps(reply)[:300]}")
            return reply
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            if is_rate_limit(e.code, detail):
                limited(f"HTTP {e.code} from {BASE_URL}{path}: {detail}")
            if e.code < 500 or last:
                raise RuntimeError(f"HTTP {e.code} from {BASE_URL}{path}: {detail}")
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
