"""The shared model plans' rules for one benchmark run, kept on the same flag as bench/gateway.py's own guard."""
import sys
import threading
import time
import urllib.error

import gateway

# One rate-limit error for the whole bench: gateway.py's, which its guard and the Mem0 adapter raise too.
RateLimited = gateway.RateLimited
# The name a limit goes by when gateway.py's guard saw it first, without saying which model.
GATEWAY = "gateway"


class Stopped(RuntimeError):
    pass


class Limits:
    """The shared model plans' rules for a benchmark run: at most two model calls at once, and after HTTP 429 or
    "cooling down" from any model, no new call for 30 minutes, then work resumes once. A second limit stops the run, so
    nothing retries in a loop. A message that meets a limit gets no row; the next run picks it up.

    The pause is kept in gateway.LIMITED as well, so gateway.py's guard and run.py's check before each question agree
    with it: a limit either side sees starts the pause, and the flag clears once, when the pause is over."""

    PARALLEL = 2
    PAUSE_SECONDS = 1800

    def __init__(self):
        self.slots = threading.BoundedSemaphore(self.PARALLEL)
        self.lock = threading.Lock()
        self.resume_at, self.seen, self.limits, self.resumed, self.stopped = 0.0, {}, 0, False, None

    def limited(self, model):
        now = time.monotonic()
        with self.lock:
            gateway.LIMITED.set()
            if now < self.resume_at:
                return
            self.seen[model] = self.seen.get(model, 0) + 1
            self.limits += 1
            if self.limits > 1:
                self.stopped = f"{model} was rate-limited after the run's 30-minute pause"
            else:
                self.resume_at = now + self.PAUSE_SECONDS
            print(f"{model} rate-limited: {self.stopped or 'no new calls for 30 minutes'}", file=sys.stderr)

    def _noticed(self):
        """A limit gateway.py's guard met on its own, with no pause here yet, starts one."""
        with self.lock:
            unseen = gateway.LIMITED.is_set() and time.monotonic() >= self.resume_at and (
                self.resumed or not self.limits)
        if unseen:
            self.limited(GATEWAY)

    def wait(self):
        """Before a message or a store build starts: wait out the pause, since every message uses the answer model
        and the judge."""
        while True:
            self._noticed()
            with self.lock:
                if self.stopped:
                    raise Stopped(self.stopped)
                left = self.resume_at - time.monotonic()
                if left <= 0:
                    if self.limits and not self.resumed:
                        self.resumed = True
                        gateway.LIMITED.clear()
                    return
            time.sleep(min(30.0, left))

    def send(self, model, call, timeout=None):
        """One model call in one of the two slots. Raises RateLimited, without sending, while the model is paused,
        checked again once a slot is free, since a pause can start while a call waits for one."""
        self._may_start(model)
        if not self.slots.acquire(timeout=timeout):
            raise TimeoutError("no free model slot in time")
        try:
            self._may_start(model)
            return call()
        except RateLimited:
            self.limited(model)
            raise
        except Stopped:
            raise
        except Exception as exc:
            text = exc.read()[:300].decode(errors="replace") if isinstance(exc, urllib.error.HTTPError) else str(exc)
            if (getattr(exc, "status_code", None) or getattr(exc, "code", None)) == 429 or "cooling down" in text:
                self.limited(model)
                raise RateLimited(model) from None
            if isinstance(exc, urllib.error.HTTPError):
                raise RuntimeError(f"HTTP {exc.code}: {text[:300]}") from None
            raise
        finally:
            self.slots.release()

    def _may_start(self, model):
        self._noticed()
        with self.lock:
            if self.stopped:
                raise Stopped(self.stopped)
            if time.monotonic() < self.resume_at:
                raise RateLimited(model)
