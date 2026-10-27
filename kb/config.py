"""The config file: TOML parsed once into typed settings.

    machine = "studio"            # this computer's name in stored ids; set it once
    [ai]
    plugin = "anthropic"          # a name from kb.ai.PLUGINS; the other keys are that plug-in's options
    [check]
    budget_seconds = 10           # default: the plug-in's
    [summarize]
    per_run = 150
    [schedule]
    every_minutes = 15
    [sources.notes]               # one table per source; its name is what `kb search --source` takes
    type = "notes"                # default: the table name
    path = "~/Notes"
    exclude = ["Journal"]         # added to the type's default excludes
    summarize = ["Meetings/**"]   # true, false, or the globs whose items get summaries
    hosts = ["laptop"]            # where to read it; default: this computer
    [hosts.laptop]                # another computer, read over SSH
    ssh = "sam@laptop.local"

A source type's Options dataclass, and a plug-in's, is the only schema for its keys: options_from() reads its fields.
"""
import dataclasses
import os
import re
import subprocess
import typing
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from kb.mask import holds_secret, mask

# Names go into every stored id and into the logs, so one that looks like a key is refused; the message is masked.
SECRET_NAME = "{where}: this name looks like a secret; use a plain name"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
SOURCE_KEYS = {"type", "enabled", "exclude", "exclude_defaults", "summarize", "hosts"}
MAX_CHECK_BUDGET = 25.0
SECRET_TIMEOUT = 60.0


class ConfigError(ValueError):
    """Every problem in the file, each with its key path, such as `sources.notes.pth: unknown key`."""

    def __init__(self, problems):
        self.problems = tuple(mask(p) for p in problems)
        super().__init__("\n".join(self.problems))


class SecretError(RuntimeError):
    """Its message is masked, because a `cmd:` reference can hold a token in the command itself."""

    def __init__(self, message):
        super().__init__(mask(message))


@dataclass(frozen=True)
class Secret:
    """Where a key or password lives, never the value: "env:NAME", "file:PATH" or "cmd:COMMAND". reveal() reads it at
    the moment of use."""
    ref: str

    def __post_init__(self):
        kind, _, rest = self.ref.partition(":")
        if kind not in ("env", "file", "cmd") or not rest:
            raise ValueError('expected "env:NAME", "file:PATH" or "cmd:COMMAND"')

    def reveal(self):
        kind, _, rest = self.ref.partition(":")
        if kind == "env":
            value = os.environ.get(rest, "")
        elif kind == "file":
            try:
                with open(os.path.expanduser(rest), encoding="utf-8") as fh:
                    value = fh.read()
            except OSError as exc:
                raise SecretError(f"cannot read {rest}: {exc.strerror}") from None
        else:
            try:
                proc = subprocess.run(rest, shell=True, capture_output=True, text=True, timeout=SECRET_TIMEOUT)
            except subprocess.TimeoutExpired:
                raise SecretError(f"`{rest}` gave no answer in {SECRET_TIMEOUT:g} s") from None
            except (OSError, subprocess.SubprocessError) as exc:
                raise SecretError(f"`{rest}` did not run: {type(exc).__name__}") from None
            if proc.returncode:
                raise SecretError(f"`{rest}` exited {proc.returncode}")
            value = proc.stdout
        # An environment value is used as it is; a file or a command's output loses only its trailing line breaks.
        if kind != "env":
            value = value.rstrip("\r\n")
        if not value.strip(" \t\r\n"):
            raise SecretError(f"{self.ref.split(':', 1)[0]}:{rest} is empty")
        return value

    def __repr__(self):
        return f"Secret({self.ref})"


@dataclass(frozen=True)
class Summarize:
    """Which long items of a source get summaries: all, none, or those whose id matches a glob."""
    every: bool
    globs: Tuple[str, ...] = ()

    def covers(self, source_id):
        from kb.sources import Excludes
        return self.every or (bool(self.globs) and Excludes(self.globs)(source_id))


@dataclass(frozen=True)
class Host:
    """Another computer, reached with `ssh <ssh> <python> -`. It needs Python 3.9 or newer and nothing installed."""
    name: str
    ssh: str
    python: str = "python3"


@dataclass(frozen=True)
class Source:
    name: str
    type_name: str
    type: Any
    options: Any
    exclude: Tuple[str, ...]
    summarize: Summarize
    hosts: Tuple[str, ...]


@dataclass(frozen=True)
class AISettings:
    plugin: str
    options: Any


@dataclass(frozen=True)
class CheckSettings:
    enabled: bool = True
    budget_seconds: Optional[float] = None


@dataclass(frozen=True)
class SummarizeSettings:
    enabled: bool = True
    per_run: int = 150


@dataclass(frozen=True)
class Schedule:
    every_minutes: int = 15


@dataclass(frozen=True)
class Config:
    machine: str = "local"
    ai: Optional[AISettings] = None
    check: CheckSettings = CheckSettings()
    summarize: SummarizeSettings = SummarizeSettings()
    schedule: Schedule = Schedule()
    sources: Tuple[Source, ...] = ()
    hosts: Tuple[Host, ...] = ()

    def source(self, name):
        return next((s for s in self.sources if s.name == name), None)

    def host(self, name):
        return next((h for h in self.hosts if h.name == name), None)

    def units(self):
        """Every health unit this config owns."""
        return tuple(f"{s.name}@{h}" for s in self.sources for h in s.hosts) + ("summarize",)


def read_toml(text):
    try:
        import tomllib
    except ImportError:
        from kb._vendor import tomli as tomllib
    return tomllib.loads(text)


def load(path):
    """The parsed config, or the defaults when the file does not exist yet. Raises ConfigError."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return Config()
    except UnicodeDecodeError as exc:
        raise ConfigError([f"not valid UTF-8: {exc.reason} at byte {exc.start}"]) from None
    return parse(text)


def parse(text):
    try:
        raw = read_toml(text)
    except ValueError as exc:
        raise ConfigError([f"not valid TOML: {exc}"]) from None
    problems = []
    machine = raw.pop("machine", "local")
    if not isinstance(machine, str) or not NAME_RE.match(machine):
        problems.append("machine: use lowercase letters, digits, - and _, up to 40 characters")
        machine = "local"
    elif holds_secret(machine):
        problems.append(SECRET_NAME.format(where="machine"))
        machine = "local"
    tables = {key: raw.pop(key, {}) for key in ("ai", "check", "summarize", "schedule", "hosts", "sources")}
    problems += [f"{key}: unknown key" for key in raw]
    for key, value in tables.items():
        if not isinstance(value, dict):
            problems.append(f"{key}: expected a table")
            tables[key] = {}
    check = options_from(CheckSettings, tables["check"], "check", problems)
    if check and check.budget_seconds is not None and not 0 < check.budget_seconds <= MAX_CHECK_BUDGET:
        problems.append(f"check.budget_seconds: must be above 0 and at most {MAX_CHECK_BUDGET:g}")
    summarize = options_from(SummarizeSettings, tables["summarize"], "summarize", problems)
    schedule = options_from(Schedule, tables["schedule"], "schedule", problems)
    ai = _ai(tables["ai"], problems)
    hosts = []
    for name, table in tables["hosts"].items():
        if name == machine or not NAME_RE.match(name):
            problems.append(f"hosts.{name}: a host name differs from machine and uses lowercase letters, digits, - and _")
        elif holds_secret(name):
            problems.append(SECRET_NAME.format(where=f"hosts.{name}"))
        elif not isinstance(table, dict):
            problems.append(f"hosts.{name}: expected a table")
        else:
            fields = options_from(_HostFields, table, f"hosts.{name}", problems)
            if fields:
                hosts.append(Host(name, fields.ssh, fields.python))
    known = {machine} | {h.name for h in hosts}
    sources = []
    for name, table in tables["sources"].items():
        source = _source(name, table, machine, known, problems)
        if source:
            sources.append(source)
    if problems:
        raise ConfigError(problems)
    return Config(machine, ai, check, summarize, schedule, tuple(sources), tuple(hosts))


def _ai(table, problems):
    if not table:
        return None
    from kb import ai
    plugin = table.get("plugin")
    spec = ai.PLUGINS.get(plugin) if isinstance(plugin, str) else None
    if spec is None:
        problems.append(f"ai.plugin: expected one of {', '.join(sorted(ai.visible_plugins()))}")
        return None
    if spec.hidden and table.get("allow_unconfirmed") is not True:
        problems.append(f"ai.plugin: {plugin} is off until its maker confirms this use is allowed")
        return None
    rest = {k: v for k, v in table.items() if k not in ("plugin", "allow_unconfirmed")}
    options = options_from(spec.Options, rest, "ai", problems)
    if options is None:
        return None
    for name in secret_looking(options):
        problems.append(f"ai.{name}: this looks like a secret; give a key as key = \"env:...\", file: or cmd:")
    return AISettings(plugin, options)


def secret_looking(options):
    """The names of plain text settings that look like secrets. They go to the plug-in's provider as they are (a
    model name in the request), so only `key`, a Secret reference, may hold one."""
    return [f.name for f in dataclasses.fields(options)
            if isinstance(getattr(options, f.name), str) and holds_secret(getattr(options, f.name))]


def _source(name, table, machine, known_hosts, problems):
    from kb import sources
    where = f"sources.{name}"
    if not NAME_RE.match(name):
        problems.append(f"{where}: a source name uses lowercase letters, digits, - and _, up to 40 characters")
        return None
    if holds_secret(name):
        problems.append(SECRET_NAME.format(where=where))
        return None
    if not isinstance(table, dict):
        problems.append(f"{where}: expected a table")
        return None
    if table.get("enabled", True) is False:
        return None
    type_name = table.get("type", name)
    kind = sources.load_type(type_name) if isinstance(type_name, str) else None
    if kind is None:
        problems.append(f"{where}.type: expected one of {', '.join(sources.type_names())}")
        return None
    common = options_from(_Common, {k: v for k, v in table.items() if k in SOURCE_KEYS - {"type"}}, where, problems)
    options = options_from(kind.Options, {k: v for k, v in table.items() if k not in SOURCE_KEYS}, where, problems)
    if common is None or options is None:
        return None
    if common.summarize is None:
        summarize = Summarize(kind.summarize)
    elif isinstance(common.summarize, bool):
        summarize = Summarize(common.summarize)
    else:
        summarize = Summarize(False, common.summarize)
    exclude = (kind.exclude if common.exclude_defaults else ()) + common.exclude
    hosts = common.hosts or (machine,)
    for host in hosts:
        if host not in known_hosts:
            problems.append(f"{where}.hosts: {host} is neither machine nor a [hosts.{host}] table")
        elif host != machine and not remote_ok(kind):
            problems.append(f"{where}.hosts: a {type_name} source runs only on this computer, because it holds a "
                            "secret or calls the AI")
    return Source(name, type_name, kind, options, exclude, summarize, hosts)


def remote_ok(kind):
    """A type may run on another computer when it needs no AI and its options hold no secret, so no key leaves here."""
    hints = typing.get_type_hints(kind.Options)
    return not kind.needs_model and not any(Secret in (hint, *typing.get_args(hint)) for hint in hints.values())


@dataclass(frozen=True)
class _Common:
    enabled: bool = True
    exclude: Tuple[str, ...] = ()
    exclude_defaults: bool = True
    summarize: typing.Union[bool, Tuple[str, ...], None] = None
    hosts: Tuple[str, ...] = ()


@dataclass(frozen=True)
class _HostFields:
    ssh: str
    python: str = "python3"


def options_from(cls, raw, where, problems):
    """An instance of the frozen dataclass `cls` built from TOML values by its field types (str, int, float, bool,
    Literal, Secret, Tuple[str, ...], Optional and Union of those). Problems go to `problems` with their key path;
    returns None when there were any."""
    hints = typing.get_type_hints(cls)
    fields = {f.name: f for f in dataclasses.fields(cls)}
    found = len(problems)
    values = {}
    for key, value in raw.items():
        if key not in fields:
            problems.append(f"{where}.{key}: unknown key")
            continue
        try:
            values[key] = _coerce(hints[key], value)
        except (TypeError, ValueError) as exc:
            problems.append(f"{where}.{key}: {exc}")
    for name, f in fields.items():
        if name not in values and f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            problems.append(f"{where}.{name}: required")
    if len(problems) > found:
        return None
    return cls(**values)


def _coerce(hint, value):
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if origin is typing.Union:
        for option in args:
            if option is type(None):
                continue
            try:
                return _coerce(option, value)
            except (TypeError, ValueError):
                pass
        raise TypeError(f"expected {_describe(hint)}")
    if origin is typing.Literal:
        if value not in args:
            raise ValueError(f"expected one of {', '.join(map(repr, args))}")
        return value
    if origin is tuple:
        if not isinstance(value, list):
            raise TypeError(f"expected {_describe(hint)}")
        return tuple(_coerce(args[0], v) for v in value)
    if hint is Secret:
        if not isinstance(value, str):
            raise TypeError('expected "env:NAME", "file:PATH" or "cmd:COMMAND"')
        return Secret(value)
    if hint is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if hint is bool or hint is int:
        if type(value) is not hint:
            raise TypeError(f"expected {_describe(hint)}")
        return value
    if not isinstance(value, hint):
        raise TypeError(f"expected {_describe(hint)}")
    return value


def _describe(hint):
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if origin is typing.Union:
        return " or ".join(_describe(a) for a in args if a is not type(None))
    if origin is tuple:
        return f"a list of {_describe(args[0])}s"
    return {str: "text", int: "a whole number", float: "a number", bool: "true or false"}.get(hint, "a value")
