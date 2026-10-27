"""The shared model plans' rules for one benchmark run."""
import sys
import threading
import time
import urllib.error


class RateLimited(RuntimeError):
    pass


class Stopped(RuntimeError):
    pass


class Limits:
    """The shared model plans' rules for a benchmark run: at most two model calls at once, and after HTTP 429 or
    "cooling down" from a model, no new call to it for 30 minutes, then one more try. A second limit on the same model
    stops the run, so nothing retries in a loop. A message that meets a limit gets no row; the next run picks it up."""

    PARALLEL = 2
    PAUSE_SECONDS = 1800

    def __init__(self):
        self.slots = threading.BoundedSemaphore(self.PARALLEL)
        self.lock = threading.Lock()
        self.resume_at, self.seen, self.stopped = {}, {}, None

    def limited(self, model):
        now = time.monotonic()
        with self.lock:
            if now < self.resume_at.get(model, 0):
                return
            self.seen[model] = self.seen.get(model, 0) + 1
            if self.seen[model] > 1:
                self.stopped = f"{model} was rate-limited again after its 30-minute pause"
            else:
                self.resume_at[model] = now + self.PAUSE_SECONDS
            print(f"{model} rate-limited: {self.stopped or 'no new calls for 30 minutes'}", file=sys.stderr)

    def wait(self):
        """Before a message or a store build starts: wait out any model's pause, since every message uses the answer
        model and the judge."""
        while True:
            if self.stopped:
                raise Stopped(self.stopped)
            with self.lock:
                ready = max(self.resume_at.values(), default=0)
            if time.monotonic() >= ready:
                return
            time.sleep(min(30.0, ready - time.monotonic()))

    def send(self, model, call, timeout=None):
        """One model call in one of the two slots. Raises RateLimited, without sending, while the model is paused,
        checked again once a slot is free, since a pause can start while a call waits for one."""
        self._may_start(model)
        if not self.slots.acquire(timeout=timeout):
            raise TimeoutError("no free model slot in time")
        try:
            self._may_start(model)
            return call()
        except (RateLimited, Stopped):
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
        with self.lock:
            if self.stopped:
                raise Stopped(self.stopped)
            if time.monotonic() < self.resume_at.get(model, 0):
                raise RateLimited(model)
