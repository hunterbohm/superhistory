"""Model calls for the benchmarks, through any endpoint that speaks Anthropic's Messages API.

BENCH_BASE_URL defaults to a local CLIProxyAPI gateway; BENCH_API_KEY, or the gateway's key file, authenticates.

The benchmarks share their model plans with other work, so a rate limit is never retried: the first HTTP 429, or a
reply that says the model is cooling down, raises RateLimited and sets LIMITED, and the runners start no new question
after that. Run the same command again once the limit has passed; it runs only the questions with no row.
"""
import codecs
import http.client
import io
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
import urllib.response

BASE_URL = os.environ.get("BENCH_BASE_URL", "http://127.0.0.1:8317").rstrip("/")
KEY_FILE = os.path.expanduser("~/.config/cli-gateway/client.key")
ATTEMPTS = 6
# How much of a failed call's reply is read for a rate limit; the messages that quote it stay short.
ERROR_BYTES = 1 << 16
REPLY_BYTES = 1 << 24
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
    """Whether a reply that came back as JSON says the plan is rate limited, as some gateways do with status 200.
    A message, a completion or a token count with no error in it is never read for it, so a model's own words never
    count."""
    if (isinstance(response, dict) and "error" not in response and response.get("type") != "error"
            and (response.get("content") and isinstance(response["content"], list)
                 or response.get("choices") and isinstance(response["choices"], list)
                 or isinstance(response.get("input_tokens"), int))):
        return False
    text = json.dumps(response).lower()
    return "rate_limit" in text or "rate limit" in text or "cooling down" in text


# The longest phrase that names a limit, less one: how much of the text before a new piece is searched with it.
OVERLAP = len("cooling down") - 1


class _Arriving:
    """A reply's text as it arrives, each piece decoded and searched once."""

    def __init__(self):
        self.decode = codecs.getincrementaldecoder("utf-8")(errors="replace").decode
        self.parts, self.recent, self.first = [], "", None

    def add(self, chunk):
        """The newest piece, with the end of the text before it so a phrase across the two is found."""
        new = self.decode(chunk)
        self.parts.append(new)
        if self.first is None and new.strip():
            self.first = new.lstrip()[0]
        window = self.recent + new
        self.recent = window[-OVERLAP:]
        return window

    def text(self):
        return "".join(self.parts)


def _read(reply, limit, watch):
    """Up to `limit` bytes of a reply, and the error that cut it short, if any: what arrived before a read failed is
    kept. `watch` sees each piece as it arrives, so a limit stops the run before the rest comes, or a hedge copy goes
    out."""
    raw, read = b"", getattr(reply, "read1", reply.read)
    while len(raw) < limit:
        try:
            chunk = read(min(1 << 16, limit - len(raw)))
        except (OSError, http.client.HTTPException) as exc:
            return raw + getattr(exc, "partial", b""), exc
        if not chunk:
            break
        raw += chunk
        watch(chunk)
    return raw, None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The gateway never redirects, so a redirect is raised as the HTTP error it is, its reply read like any other,
    rather than followed with a second request."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _open(request, timeout):
    return _OPENER.open(request, timeout=timeout)


def urlopen(request, timeout):
    """urllib's urlopen, except that every reply is read for a rate limit before anything parses it or cuts it short
    for a message, and no redirect is followed. A reply that names no limit is handed on as urllib gave it, still
    readable; a failed read is raised as it was."""
    try:
        reply = _open(request, timeout)
    except urllib.error.HTTPError as e:
        # A 429 is a limit before its reply is read, since a reply that stalls would make it look like a network error.
        if e.code == 429:
            limited(f"HTTP 429 from {request.full_url}")

        error = _Arriving()

        def watch(chunk):
            if is_rate_limit(e.code, error.add(chunk)):
                limited(f"HTTP {e.code} from {request.full_url}: {' '.join(error.text().split())}")
        raw, _ = _read(e, ERROR_BYTES, watch)
        raise urllib.error.HTTPError(e.url, e.code, e.msg, e.hdrs, io.BytesIO(raw)) from None

    arriving = _Arriving()

    def watch(chunk):
        """A model's reply is a JSON object, so text that cannot start one is read by its words as it comes; an object
        counts once it parses, tried only where it could end."""
        window = arriving.add(chunk)
        if arriving.first is None:
            return
        if arriving.first != "{":
            said = is_rate_limit(0, window)
        elif window.rstrip().endswith("}"):
            try:
                said = limited_reply(json.loads(arriving.text()))
            except ValueError:
                said = False
        else:
            said = False
        if said:
            limited(f"from {request.full_url}: {' '.join(arriving.text().split())}")
    with reply:
        raw, failed = _read(reply, REPLY_BYTES, watch)
    text = raw.decode(errors="replace")
    # An object that never parsed, because it was cut short, is read by its words at the end: a model's reply cut
    # short that names a limit stops the run too, the safer way to be wrong.
    if text.lstrip().startswith("{") and is_rate_limit(0, text):
        try:
            json.loads(text)
        except ValueError:
            limited(f"from {request.full_url}: {' '.join(text.split())}")
    if failed:
        raise failed
    return urllib.response.addinfourl(io.BytesIO(raw), reply.headers, reply.url, reply.status)


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
        check_limited()
        last = attempt == ATTEMPTS - 1
        try:
            req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method="POST")
            with urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
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
