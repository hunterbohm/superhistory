"""Each tool loads the month into a fresh store, then answers `retrieve(question)` with the text an agent would
see before the user's message. A tool may also offer the agent tools it can call, as the product does."""
import importlib
from typing import NamedTuple, Optional


class Retrieved(NamedTuple):
    context: str                    # "" means the tool added nothing
    items: Optional[list]           # source_ids behind the context, or None when the tool can't say
    seconds: float
    error: Optional[str] = None
    model_tokens: int = 0           # tokens the tool's own model calls used for this lookup
    query: Optional[str] = None


class Tool:
    agent_tools = ()

    def load(self, items, workdir):
        return {}

    def retrieve(self, question):
        raise NotImplementedError

    def call(self, name, args):
        """Run one agent tool call. Returns its text and the source_ids it showed."""
        raise KeyError(name)


REGISTRY = {
    "none": ("tools.baselines", "Nothing"),
    "oracle": ("tools.baselines", "Oracle"),
    "check": ("tools.check", "Check"),
    "check-cards": ("tools.check", "Cards"),
}


def kind(name):
    module, cls = REGISTRY[name]
    return getattr(importlib.import_module(module), cls)


def make(name):
    return kind(name)()
