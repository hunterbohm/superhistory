"""AI plug-ins.

Every plug-in answers one call, model.ask(Prompt, timeout) -> the model's text, in one attempt. A plug-in is a row in
PLUGINS: its config keys (an Options dataclass, read from the [ai] table) and a function that builds the model from
them.
"""
import json
import queue
import random
import threading
import time
from typing import Any, Callable, NamedTuple, Optional

from kb import COMMAND
from kb.mask import mask


class Prompt(NamedTuple):
    system: str
    user: str
    max_tokens: int = 2000


class Traits(NamedTuple):
    check_budget: float            # seconds the check gives its two calls together
    hedge_after: Optional[float]   # seconds before a slow check call gets an identical copy; None: never
    parallel: int                  # calls the summarizer may run at once
    max_prompt_chars: int          # the summarizer caps an item's text to fit


class AIError(Exception):
    """Its message is masked, because it can quote a provider's answer or a CLI's error output."""

    def __init__(self, message="", *args):
        super().__init__(mask(str(message)), *args)


class NotReady(AIError):
    """A setup problem: no key, signed out, not installed, wrong address. No call will work until the user acts."""


class Busy(AIError):
    """Try again later: a timeout, a rate limit, a server error, the network."""

    def __init__(self, message, retry_after=0.0):
        super().__init__(message)
        self.retry_after = retry_after


class Refused(AIError):
    """This prompt will never be answered: the model refused it, or the service rejected the request."""


class BadAnswer(AIError):
    """An answer came, but not the JSON the caller asked for."""


class PluginSpec(NamedTuple):
    name: str
    label: str
    Options: type                    # the [ai] keys besides `plugin`
    make: Callable[[Any], Any]       # Options -> a model with name, model, traits and ask()
    hidden: bool = False


def _plugins():
    from kb.ai import api, cli
    return {spec.name: spec for spec in api.PLUGINS + cli.PLUGINS}


PLUGINS = _plugins()


def visible_plugins():
    return [name for name, spec in PLUGINS.items() if not spec.hidden]


def masked(prompt):
    """The prompt with secrets masked. The configured model can be another provider than the user's agent, so it
    never sees a secret's value, only that one was there."""
    return prompt._replace(system=mask(prompt.system), user=mask(prompt.user))


class _Masked:
    """A plug-in's model that masks every prompt before it goes out."""

    def __init__(self, model):
        self._model = model

    def ask(self, prompt, timeout):
        return self._model.ask(masked(prompt), timeout)

    def __getattr__(self, name):
        return getattr(self._model, name)


def load(settings):
    """The model for the config's [ai] table, masking what it is sent. Raises NotReady when there is none."""
    if settings is None:
        raise NotReady(f"no AI plug-in is set up; run `{COMMAND} setup`")
    from kb.config import secret_looking
    if secret_looking(settings.options):
        raise NotReady(f"[ai] {', '.join(secret_looking(settings.options))} looks like a secret; give a key as key")
    return _Masked(PLUGINS[settings.plugin].make(settings.options))


def hedged(model, prompt, timeout, hedge_after=None):
    """The first good answer before the deadline. With hedge_after set, a call still running then, or one that fails
    first, gets an identical copy. Raises TimeoutError at the deadline, else the last call's error."""
    deadline = time.monotonic() + timeout
    answers = queue.Queue()
    prompt = masked(prompt)  # also for a model a caller made itself, not through load()

    def call():
        try:
            answers.put((model.ask(prompt, max(deadline - time.monotonic(), 0.01)), None))
        except Exception as exc:
            answers.put((None, exc))

    stages = (deadline,) if hedge_after is None else (min(deadline, time.monotonic() + hedge_after), deadline)
    running, error = 0, None
    for wait_until in stages:
        threading.Thread(target=call, daemon=True).start()
        running += 1
        while running:
            try:
                answer, error = answers.get(timeout=max(wait_until - time.monotonic(), 0))
            except queue.Empty:
                break
            running -= 1
            if error is None:
                return answer
            if wait_until < deadline:
                break
    if running or error is None:
        raise TimeoutError("no model answer in time")
    raise error


def with_retries(call, attempts=6, sleep=time.sleep):
    """call() again after a Busy, with exponential backoff and jitter up to a minute. Other errors pass through."""
    for attempt in range(attempts):
        try:
            return call()
        except Busy as exc:
            if attempt == attempts - 1:
                raise
            sleep(max(min(60.0, 2 ** attempt + random.random()), exc.retry_after))


def find_json(text, *keys):
    """The first JSON object in the text that has one of `keys` (any object when none are given). Prose, code fences
    and thinking around it do not matter. Raises BadAnswer."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start >= 0:
        try:
            found, _ = decoder.raw_decode(text, start)
        except ValueError:
            found = None
        if isinstance(found, dict) and (not keys or any(k in found for k in keys)):
            return found
        start = text.find("{", start + 1)
    raise BadAnswer("no JSON object in the answer" + (f" with {' or '.join(keys)}" if keys else ""))


def ask_json(model, prompt, timeout, *keys, hedge_after=None):
    return find_json(hedged(model, prompt, timeout, hedge_after), *keys)
