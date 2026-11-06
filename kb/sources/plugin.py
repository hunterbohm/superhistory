"""A source that is a program of the user's, found in the plug-ins folder and named in the config:

    [sources.calendar]
    type = "plugin"
    plugin = "calendar"           # a file in the plug-ins folder
    [sources.calendar.settings]   # given to the program as it is
    calendar_id = "primary"
    [sources.calendar.secrets]    # where each secret lives; the value is read when the program starts
    client_secret = "file:~/.config/superhistory/keys/google.client-secret"

Each ingest starts the program once with a request on its standard input and reads JSON lines from its standard
output. docs/plugins.md is the format. The program runs only if the user put it in the folder and named it here;
nothing is downloaded. One run has a time limit and a size limit, and its standard error goes to the log. A run
either ends cleanly, and then its items and its watermark are stored together, or it fails and stores nothing: the
items wait in memory, within the size limit, until the program has exited with a watermark and no line kb could not
read. So a source marked failing has changed nothing, and the next run starts from the same watermark.
"""
import json
import logging
import os
import re
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, NamedTuple, Optional

from kb import NAME
from kb.config import Secret
from kb.mask import holds_secret, mask
from kb.sources import Gone, Item, Progress, SourceType

PROTOCOL = 1
COMMAND = NAME.lower()
LOG = logging.getLogger("kb.plugin")

FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
SECRET_NAME = re.compile(r"[a-z0-9][a-z0-9_]{0,39}")
MAX_ID = 300
MAX_URL = 2000
MAX_META_BYTES = 16_000
MAX_WATERMARK = 200
MAX_LINE_BYTES = 8_000_000
MAX_TIMEOUT_SECONDS = 3600
MAX_OUTPUT_MB = 200
STDERR_LINES = 200
STDERR_BYTES = 64_000
ERROR_TAIL = 400
GRACE_SECONDS = 2.0
# Keys kb keeps in an item's metadata for itself.
RESERVED_META = ("hash", "chunk", "chunks", "host", "atom", "of_hash", "of_chars", "prefix")
ITEM_KEYS = {"id", "time", "title", "text", "url", "meta"}
TIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|z|[+-]\d{2}:?\d{2})?)?")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Options:
    plugin: str
    settings: dict = field(default_factory=dict)
    secrets: Dict[str, Secret] = field(default_factory=dict)
    timeout_seconds: int = 300
    max_output_mb: int = 50

    def __post_init__(self):
        if not FILE_NAME.fullmatch(self.plugin):
            raise ValueError("plugin: a file name in the plug-ins folder, with letters, digits, - _ and . only")
        for name in self.secrets:
            if not SECRET_NAME.fullmatch(name):
                raise ValueError(f"secrets.{name}: use lowercase letters, digits and _, up to 40 characters")
        if not 1 <= self.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds: between 1 and {MAX_TIMEOUT_SECONDS}")
        if not 1 <= self.max_output_mb <= MAX_OUTPUT_MB:
            raise ValueError(f"max_output_mb: between 1 and {MAX_OUTPUT_MB}")
        try:
            json.dumps(self.settings, default=str)
        except (TypeError, ValueError):
            raise ValueError("settings: only text, numbers, true or false, lists and tables") from None


class PluginError(RuntimeError):
    """What went wrong with a run, in words for the health line. It never quotes the plug-in's output."""


class Final(NamedTuple):
    watermark: str


class BadLine(ValueError):
    pass


def executable(plugins_dir, name):
    """The path of the plug-in `name`, or a PluginError saying why it will not run. The file must be the user's own:
    owned by the user running kb and not writable by others, in a folder others cannot write either."""
    if not plugins_dir:
        raise PluginError("this config has no plug-ins folder")
    folder = os.path.abspath(plugins_dir)
    path = os.path.join(folder, name)
    try:
        folder_stat, stat = os.stat(folder), os.stat(path)
    except FileNotFoundError:
        raise PluginError(f"there is no plug-in named {name} in {folder}") from None
    except OSError as exc:
        raise PluginError(f"cannot read the plug-in {name} in {folder}: {exc.strerror}") from None
    if not os.path.isfile(path):
        raise PluginError(f"{path} is not a file")
    if not os.access(path, os.X_OK):
        raise PluginError(f"{path} is not executable; run `chmod u+x {path}`")
    for what, where, st in (("the plug-ins folder", folder, folder_stat), ("the plug-in", path, stat)):
        if st.st_uid != os.geteuid():
            raise PluginError(f"{what} {where} belongs to another user, so {COMMAND} will not run it")
        if st.st_mode & 0o022:
            raise PluginError(f"{what} {where} can be written by anyone but you; run `chmod go-w {where}`")
    return path


def request(options, since):
    """The one JSON object the plug-in reads from its standard input."""
    secrets = {name: secret.reveal() for name, secret in options.secrets.items()}
    return {"protocol": PROTOCOL, "watermark": since, "settings": options.settings, "secrets": secrets}


def environment():
    """What the plug-in inherits: enough to find programs and a home folder, and none of kb's own environment, which
    may hold keys for other things."""
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR")
    return {key: os.environ[key] for key in keep if key in os.environ}


def _stop(proc):
    """Ends the plug-in's whole process group, the one kb started. While anything of it lives, the system keeps its
    number from any other process, so this reaches nothing else."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


class _Stderr:
    """Keeps the plug-in's standard error, up to a limit, and writes it to the log masked as one text, so a secret
    that spans lines, such as a private key, is masked with its surrounding block and not a line at a time."""

    def __init__(self, name):
        self.name, self.data, self.cut, self.finished = name, b"", False, False

    def feed(self, data):
        room = STDERR_BYTES - len(self.data)
        self.data += data[:max(room, 0)]
        self.cut = self.cut or len(data) > room

    def text(self):
        return mask(self.data.decode("utf-8", errors="replace").replace("\r\n", "\n"))

    def finish(self):
        if self.finished:
            return
        self.finished = True
        shown = self.text().splitlines()
        for line in shown[:STDERR_LINES]:
            LOG.info("plugin %s: %s", self.name, line[:2000])
        if len(shown) > STDERR_LINES or self.cut:
            LOG.info("plugin %s: the rest of its standard error was not logged", self.name)

    def last(self):
        text = " / ".join(line for line in self.text().splitlines()[-5:] if line.strip())
        return f": {text[-ERROR_TAIL:]}" if text else ""


def lines(argv, body, options, cwd, name):
    """Yields each line the program prints, as bytes, until it ends. Raises PluginError at once on a run that is too
    slow or too large, and after its output when it exits with an error. The program and anything it started are
    stopped whenever this ends, however it ends."""
    limit = options.max_output_mb * 1_000_000
    deadline = time.monotonic() + options.timeout_seconds
    err = _Stderr(name)
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
                                env=environment(), start_new_session=True, close_fds=True)
    except OSError as exc:
        raise PluginError(f"{name} did not start: {exc.strerror}") from None
    sel = selectors.DefaultSelector()
    pending = memoryview(body)
    try:
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            os.set_blocking(stream.fileno(), False)
        sel.register(proc.stdin, selectors.EVENT_WRITE, "in")
        sel.register(proc.stdout, selectors.EVENT_READ, "out")
        sel.register(proc.stderr, selectors.EVENT_READ, "err")
        buf, total, gone_at = b"", 0, None
        while any(key.data != "in" for key in sel.get_map().values()):
            now = time.monotonic()
            if now >= deadline:
                raise PluginError(f"{name} gave no end in {options.timeout_seconds} s and was stopped{err.last()}")
            if gone_at is not None and now >= gone_at:
                break  # it exited, but something it started still holds its output open
            for key, _ in sel.select(min(deadline - now, 0.5)):
                if key.data == "in":
                    try:
                        pending = pending[os.write(key.fileobj.fileno(), pending[:65536]):]
                    except BlockingIOError:
                        continue
                    except OSError:  # it closed its input without reading all of the request
                        pending = pending[len(pending):]
                    if not pending:
                        sel.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                try:
                    data = os.read(key.fileobj.fileno(), 65536)
                except (BlockingIOError, InterruptedError):
                    continue
                if not data:
                    sel.unregister(key.fileobj)
                    continue
                if key.data == "err":
                    err.feed(data)
                    continue
                total += len(data)
                if total > limit:
                    raise PluginError(f"{name} printed more than {options.max_output_mb} MB and was stopped"
                                      f"{err.last()}")
                buf += data
                *whole, buf = buf.split(b"\n")
                for line in whole:
                    if len(line) > MAX_LINE_BYTES:
                        raise PluginError(f"{name} printed a line longer than {MAX_LINE_BYTES // 1_000_000} MB and "
                                          f"was stopped{err.last()}")
                    yield line
                if len(buf) > MAX_LINE_BYTES:
                    raise PluginError(f"{name} printed a line longer than {MAX_LINE_BYTES // 1_000_000} MB and was "
                                      f"stopped{err.last()}")
            if gone_at is None and proc.poll() is not None:
                gone_at = time.monotonic() + GRACE_SECONDS
        if len(buf) > MAX_LINE_BYTES:
            raise PluginError(f"{name} printed a line longer than {MAX_LINE_BYTES // 1_000_000} MB and was stopped"
                              f"{err.last()}")
        if buf:
            yield buf
        try:
            code = proc.wait(timeout=max(deadline - time.monotonic(), 0.1))
        except subprocess.TimeoutExpired:
            raise PluginError(f"{name} gave no end in {options.timeout_seconds} s and was stopped{err.last()}") from None
        err.finish()
        if code:
            raise PluginError(f"{name} exited with {code}{err.last()}")
    finally:
        _stop(proc)
        sel.close()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass
        err.finish()


def iso(text):
    """An ISO 8601 time as UTC "YYYY-MM-DDTHH:MM:SSZ". A time with no zone is UTC; a date alone is its midnight."""
    m = TIME.fullmatch(text) if isinstance(text, str) else None
    if not m:
        raise BadLine('"time" must be an ISO 8601 date or time, such as 2026-10-04T09:30:00Z')
    y, mo, d, hh, mm, ss, zone = m.groups()
    try:
        moment = datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0), tzinfo=timezone.utc)
        if zone and zone not in "Zz":
            sign = 1 if zone[0] == "+" else -1
            digits = zone[1:].replace(":", "")
            moment -= sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    except (ValueError, OverflowError):
        raise BadLine('"time" is not a real date or time') from None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(obj, key, limit=None, required=True):
    value = obj.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise BadLine(f'"{key}" must be text')
    if limit and len(value) > limit:
        raise BadLine(f'"{key}" is longer than {limit} characters')
    return value


def parse(raw):
    """One output line as an Item (with mark ""), a Gone, or a Final. Raises BadLine with a reason that quotes none of
    the line."""
    try:
        obj = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise BadLine("not UTF-8 text") from None
    except (ValueError, RecursionError):
        raise BadLine("not a JSON object") from None
    if not isinstance(obj, dict):
        raise BadLine("not a JSON object")
    if "watermark" in obj:
        if set(obj) != {"watermark"}:
            raise BadLine('the final line holds "watermark" and nothing else')
        mark = obj["watermark"]
        if not isinstance(mark, str) or not mark or len(mark) > MAX_WATERMARK or CONTROL.search(mark):
            raise BadLine(f'"watermark" must be non-empty text of up to {MAX_WATERMARK} characters on one line')
        return Final(mark)
    source_id = _text(obj, "id", MAX_ID)
    if not source_id.strip() or CONTROL.search(source_id):
        raise BadLine('"id" must be text with something in it, on one line')
    if "gone" in obj:
        if set(obj) != {"id", "gone"} or obj["gone"] is not True:
            raise BadLine('a removed item is {"id": ..., "gone": true} and nothing else')
        return Gone(source_id, "")
    unknown = sorted(set(obj) - ITEM_KEYS)
    if unknown:
        raise BadLine(f"unknown key {mask(unknown[0])[:40]!r}; an item's keys are id, time, title, text, url, meta")
    missing = [key for key in ("time", "title", "text") if key not in obj]
    if missing:
        raise BadLine(f'"{missing[0]}" is missing')
    when, title, text = iso(obj["time"]), _text(obj, "title"), _text(obj, "text")
    url = _text(obj, "url", MAX_URL, required=False)
    if url is not None and not re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*:\S+", url):
        raise BadLine('"url" must be a link with no spaces, such as https://example.com/page')
    meta = obj.get("meta", {})
    if not isinstance(meta, dict):
        raise BadLine('"meta" must be an object')
    reserved = [key for key in RESERVED_META if key in meta]
    if reserved:
        raise BadLine(f'"meta" may not use the key {reserved[0]!r}')
    if len(json.dumps(meta)) > MAX_META_BYTES:
        raise BadLine(f'"meta" is longer than {MAX_META_BYTES} characters')
    if url is not None:
        meta = {**meta, "url": url}
    return Item(source_id, when, title, text, meta, "")


def extract(run):
    """The plug-in's items, yielded once the run has ended cleanly. Its final watermark line is the run's only mark,
    as a Progress at the end. A line that is not valid fails the run, after the rest has been read so the message can
    count them: skipping it and moving the watermark on would lose that item for good."""
    options = run.options
    name = options.plugin
    path = executable(run.plugins_dir, name)
    body = json.dumps(request(options, run.since), default=str).encode("utf-8") + b"\n"
    bad, first, final, events = 0, "", None, []
    for number, raw in enumerate(lines([path], body, options, os.path.dirname(path), name), 1):
        if not raw.strip():
            continue
        try:
            if final is not None:
                raise BadLine("a line after the final watermark line")
            event = parse(raw)
        except BadLine as exc:
            bad += 1
            first = first or f"line {number}: {exc}"
            continue
        if isinstance(event, Final):
            final = event.watermark
        elif not run.excludes(event.source_id):
            events.append(event)
    if bad:
        raise PluginError(f"{name} printed {bad} line{'s' if bad != 1 else ''} that {COMMAND} could not read, "
                          f"so its watermark stays where it was; the first, {first}")
    if final is None:
        raise PluginError(f"{name} ended without a final line like " + '{"watermark": "..."}' +
                          ", so its watermark stays where it was")
    if holds_secret(json.dumps(final)):
        raise PluginError(f"{name}'s watermark looks like a secret, so {COMMAND} will not keep it; use a time or a "
                          "counter instead")
    yield from events
    yield Progress(final)


TYPE = SourceType(Options, extract, summarize=True, others_wrote=True, all_or_nothing=True)
