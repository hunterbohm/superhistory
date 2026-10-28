import hashlib
import logging
import re
import string
import sys
import traceback

_NB = r"(?<![A-Za-z0-9])"
# A secret's value ends at a space, tab or line break, and the space around its "=" is spaces, tabs and line breaks (a
# JSON or YAML value can sit on the next line): \s would also take U+2028, U+2029, U+0085, a vertical tab or a form
# feed, which end a value early or make "password=\u2028password" look like a placeholder.
SECRET_PATTERNS = [
    # The body may come with its line breaks escaped, as in a service account's JSON key or a repr(). It is taken a
    # base64 line at a time, so a key cut off before its footer does not run on into the words after it, such as the
    # name of the next secret; only its last line, next to the footer or at the end of the text, may be short.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:(?:\s|\\[nr])*[A-Za-z0-9+/=]{16,})*"
               r"(?:(?:\s|\\[nr])*[A-Za-z0-9+/=]{1,15}(?=(?:\s|\\[nr])*(?:-----END|\Z)))?"
               r"(?:(?:\s|\\[nr])*-----END [A-Z ]*PRIVATE KEY-----)?"),
    re.compile(_NB + r"sk-(?:ant-|proj-|or-v1-)?[A-Za-z0-9_-]{20,}"),
    re.compile(_NB + r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(_NB + r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(_NB + r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(_NB + r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])"),
    re.compile(_NB + r"(?:AIza[0-9A-Za-z_-]{35}|ya29\.[0-9A-Za-z_-]{20,}|GOCSPX-[A-Za-z0-9_-]{20,})"),
    re.compile(_NB + r"(?:(?:hf|gsk|npm|ntn|sbp)_[A-Za-z0-9]{30,}|lin_api_[A-Za-z0-9]{30,}|xai-[A-Za-z0-9]{40,}|"
               r"tskey-[a-z]+-[A-Za-z0-9-]{10,}|glpat-[A-Za-z0-9_-]{20,}|whsec_[A-Za-z0-9+/=]{20,}|fc-[a-f0-9]{32}|"
               r"st\.[A-Za-z0-9-]{20,}\.[A-Za-z0-9]{20,}\.[A-Za-z0-9]{20,})"),
    re.compile(r"(?<![\d])\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"),
    re.compile(r"https://(?:[\w-]+\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+"),
    re.compile(r"https://hooks\.slack\.com/(?:services|workflows|triggers)/[\w/-]+"),
    re.compile(_NB + r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*"),
    re.compile(r"(?i)\bbasic\s+(?=[A-Za-z0-9+/]*[0-9+/=])[A-Za-z0-9+/]{12,}={0,2}"),
]
# The sync client's own recovery and pairing codes, as its keys module writes them ("VRC1-" or "VP1-" and groups of
# four Crockford base32 characters) and as its parser takes them typed: any case, I, L and O for 1 and 0, and dashes or
# spaces between groups, or dashes anywhere.
_CODE = r"[0-9A-TV-Z]"
SYNC_CODES = [
    re.compile(rf"(?i)(?<![0-9A-Z])V[-\s]?R[-\s]?C[-\s]?1(?:(?:[-\s]{{0,3}}{_CODE}{{4}}){{8}}|(?:-*{_CODE}){{32}})"),
    re.compile(rf"(?i)(?<![0-9A-Z])V[-\s]?P[-\s]?1(?:(?:[-\s]{{0,3}}{_CODE}{{4}}){{6}}[-\s]{{0,3}}{_CODE}{{2}}"
               rf"|(?:-*{_CODE}){{26}})"),
]
# Characters an agent reading text does not see, so a token split by one reads whole: zero-width spaces and joiners,
# the word joiner and invisible operators, the soft hyphen, the byte order mark, and direction marks.
INVISIBLE = re.compile(r"[\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
PREFIXED_SECRETS = [
    re.compile(r"(?i)(\b(?:set-)?cookie[\"']?\s*:\s*)[^\"'\n]{8,}"),
    re.compile(r"([a-z][a-z0-9+.-]*://[^/ \t\n\r:@'\"]+:)[^/ \t\n\r@'\"]{3,}(?=@)"),
    re.compile(r"(\bcurl\b[^\n|;]*?\s(?:-u|--user)\s+['\"]?[^ \t\n\r:'\"]+:)[^ \t\n\r'\"]+"),
]
_KEY_NAME = (
    r"(?i)(?<![A-Za-z0-9_.-])("
    r"[A-Za-z0-9_.-]*?(?:api[_-]?key|access[_-]?key|private[_-]?key|secret|token|credentials?|passw(?:or)?d|passcode|"
    r"session[_-]?(?:token|key|secret|cookie)|bw_session)[A-Za-z0-9_-]*?"
    r"|[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*[_-](?:pass|pwd|pw)"
    r")(?![A-Za-z0-9_.-])")
_ENV_KEY_NAME = r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]*_KEY)(?![A-Za-z0-9_])"
_VALUE = r"([^ \t\n\r\"'`,;<>)\]}]+)"
KEYED_SECRET = re.compile(_KEY_NAME + r"((?:[\"'])?[ \t\r\n]*(?:[:=]|=>|\bis\b)[ \t\r\n]*[\"']?)" + _VALUE)
ENV_KEY_SECRET = re.compile(_ENV_KEY_NAME + r"((?:[\"'])?[ \t\r\n]*[:=][ \t\r\n]*[\"']?)" + _VALUE)
# In an item id, a value counts by its key's name only in assignment form, "token=...", "token=>..." or "token:...",
# with no space.
KEYED_ID = re.compile(_KEY_NAME + r"((?:[\"'])?(?:=>|[:=])[\"']?)" + _VALUE)
ENV_KEY_ID = re.compile(_ENV_KEY_NAME + r"((?:[\"'])?(?:=>|[:=])[\"']?)" + _VALUE)
# A name that is a secret's key word by itself, such as "password" or "cookie". No host may be named so, which lets an
# id's first colon be read as kb's own "<host>:" only when what comes before it could be a host.
KEY_WORD = re.compile(r"(?i)(?:api[_-]?key|access[_-]?key|private[_-]?key|secret|token|credentials?|passw(?:or)?d|"
                      r"passcode|pass|pwd|pw|session[_-]?(?:token|key|secret|cookie)|bw_session|(?:set-)?cookie|\w+_key)")
PASSWORD_NAME = re.compile(r"(?i)passw(?:or)?d|passcode|bw_session|[_-](?:pass|pwd|pw)$")
PLACEHOLDERS = {"string", "str", "none", "null", "nil", "true", "false", "undefined", "required", "optional",
                "xxx", "xxxx", "****", "...", "redacted", "<redacted>", "secret", "password", "token"}
# A pointer to a secret, not a secret: a variable, a path, a call, or one of kb's own env:, file: and cmd: references
# (a token written inside a cmd: is still caught by SECRET_PATTERNS).
REFERENCE = re.compile(r"^(?:[$%{<(\[*/~]|\.\.?/|os\.|process\.|env\.|getenv|secrets\.|vars\.|self\.|this\.|[a-z]+://|"
                       r"(?:env|file|cmd):)|"
                       r"^[A-Za-z_]+(?:\.[A-Za-z_]+)+$|^[A-Z][A-Z_]{2,}$|^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$|\(")

# Which mask() a stored row was masked with. A row of another version, or of none, is masked again when read and by
# the next ingest. The patterns change it by themselves; bump REVISION when the code that applies them changes what
# it masks.
REVISION = 3
VERSION = hashlib.sha256(repr((REVISION, [p.pattern for p in SECRET_PATTERNS + SYNC_CODES + PREFIXED_SECRETS],
                                INVISIBLE.pattern, KEYED_SECRET.pattern,
                                ENV_KEY_SECRET.pattern, PASSWORD_NAME.pattern, sorted(PLACEHOLDERS),
                                REFERENCE.pattern)).encode()).hexdigest()[:16]


def _keyed_secret(m):
    """Whether a value its key names, matched by KEYED_SECRET or a pattern like it, looks like a secret."""
    name, sep, value = m.groups()
    if value.lower() in PLACEHOLDERS or REFERENCE.search(value):
        return False
    if PASSWORD_NAME.search(name):
        word = value.rstrip(".!?")
        return bool(len(value) >= 4 and ("is" not in sep.lower() or re.search(r"[^A-Za-z]", word)))
    mixed = re.search(r"\d", value) and re.search(r"[A-Za-z]", value)
    return bool((len(value) >= 16 and not value.isdigit()) or (len(value) >= 8 and mixed))


KEY_FOOTER = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")
_KEY_BODY = frozenset(string.ascii_letters + string.digits + "+/=")
_KEY_RUN = re.compile(r"[A-Za-z0-9+/=]+")
_KEY_BREAK = re.compile(r"\r?\n|\\(?:r\\)?n")
_PLACEHOLDER = re.compile(r"\[masked\]")
# Characters glued to a placeholder are the rest of a secret an older kb masked a chunk at a time, or what its key
# pattern left of the next secret's name, as in "[masked]_key: value": today's patterns take every such character, so
# they never leave one glued. Such a name keeps its value masked with it.
_GLUED_RUN = re.compile(r"\[masked\](?:[A-Za-z0-9_.-]+[ \t]*(?:[:=]|=>)[ \t]*[\"']?" + _VALUE[1:-1] + r"|[A-Za-z0-9_+/=-]+)")
# A key's body lines are 64 characters; only its last line, next to the footer or at the end, is shorter.
FULL_LINE = 16


def _spaces_end(text, pos):
    while pos < len(text) and text[pos] in " \t":
        pos += 1
    return pos


def _after_break(text, pos):
    """Past any spaces and one line break, plain or escaped, from `pos`."""
    pos = _spaces_end(text, pos)
    brk = _KEY_BREAK.match(text, pos)
    return brk.end() if brk else pos


def _line_ends(text, pos):
    """Whether a key's line can end at `pos`: at a line break or the end, after any spaces, or at its footer."""
    pos = _spaces_end(text, pos)
    return pos == len(text) or bool(_KEY_BREAK.match(text, pos)) or text.startswith("-----END", pos)


def _last_line(text, end):
    """Whether a short base64 line ending at `end` can be a key's last: padded, or followed by its footer, the end of
    the text, or a line that is not base64."""
    pos = _after_break(text, end)
    return (text[end - 1] == "=" or pos == len(text) or text.startswith("-----END", pos)
            or text[pos] not in _KEY_BODY)


def _key_line_before(text, end):
    """(start, stop) of the base64 line whose line break, plain or escaped, ends at `end`, or None."""
    for brk in ("\r\n", "\n", "\\r\\n", "\\n"):
        if text.endswith(brk, 0, end):
            stop = end - len(brk)
            break
    else:
        return None
    start = stop
    while start > 0 and text[start - 1] in _KEY_BODY and not text.endswith("\\n", 0, start):
        start -= 1
    return (start, stop) if start < stop else None


def _headless_keys(text):
    """A private key's body lines up to its footer when its header is gone, cut off or masked apart by an older
    kb's row-by-row remask. Found from each footer back, a line at a time, so text without a footer costs one search.
    The line next to the footer may be short; a word alone before a footer stays."""
    spans = []
    for footer in KEY_FOOTER.finditer(text):
        line = _key_line_before(text, footer.start())
        if not line:
            continue
        start, full = line[0], line[1] - line[0] >= FULL_LINE
        line = _key_line_before(text, start)
        while line and line[1] - line[0] >= FULL_LINE:
            start, full, line = line[0], True, _key_line_before(text, line[0])
        if full:
            spans.append((start, footer.end(), "[masked]"))
    return spans


def _key_rest(text):
    """The rest of a key's body after a placeholder, as an older kb's row-by-row remask leaves the chunk after the
    header's: the line glued to it, the base64 lines after it, and the footer if there is one. A word glued to a
    placeholder, with no line of a key's shape, stays, and so does one line of a key's shape on the line after it,
    such as a hash. The last line may run on into words, which stay."""
    spans = []
    for glued in _PLACEHOLDER.finditer(text):
        run = _KEY_RUN.match(text, glued.end())
        end = run.end() if run else glued.end()
        first, lines, full = end - glued.end(), 0, 0
        while _line_ends(text, end):
            run = _KEY_RUN.match(text, _after_break(text, end))
            if not run or run.start() == end:
                break
            length = run.end() - run.start()
            if not _line_ends(text, run.end()):
                if length >= FULL_LINE and text[run.end()] in " \t":
                    end, lines, full = run.end(), lines + 1, full + 1
                break
            if length < FULL_LINE and not _last_line(text, run.end()):
                break
            end, lines, full = run.end(), lines + 1, full + (length >= FULL_LINE)
        if (lines if first else full >= 2) or (first >= FULL_LINE and (_line_ends(text, end) or text[end] in " \t")):
            footer = KEY_FOOTER.match(text, _after_break(text, end))
            spans.append((glued.end(), footer.end() if footer else end, ""))
    return spans


def _found(pattern):
    return lambda text: [(m.start(), m.end(), "[masked]") for m in pattern.finditer(text)]


def _after_prefix(pattern):
    return lambda text: [(m.end(1), m.end(), "[masked]") for m in pattern.finditer(text)]


def _keyed(pattern):
    return lambda text: [(m.start(3), m.end(3), "[masked]") for m in pattern.finditer(text) if _keyed_secret(m)]


# Each stage finds (start, end, replacement) spans in the text the stage before it left.
_SHAPES = [*map(_found, SECRET_PATTERNS + SYNC_CODES), _key_rest, _headless_keys, _found(_GLUED_RUN),
           *map(_after_prefix, PREFIXED_SECRETS)]
TEXT_STAGES = _SHAPES + [_keyed(KEYED_SECRET), _keyed(ENV_KEY_SECRET)]
ID_STAGES = _SHAPES + [_keyed(KEYED_ID), _keyed(ENV_KEY_ID)]


def _apply(text, spans):
    out, last = [], 0
    for start, end, replacement in spans:
        out += [text[last:start], replacement]
        last = end
    out.append(text[last:])
    return "".join(out)


def _run(stages, text):
    """`text` with every stage's spans replaced. Text with invisible characters is matched as it reads, without
    them, and only the spans that changed are replaced in the text as written, the invisible characters inside them
    with them; the rest keeps its invisible characters."""
    if not INVISIBLE.search(text):
        for stage in stages:
            spans = stage(text)
            if spans:
                text = _apply(text, spans)
        return text
    where = [i for i, ch in enumerate(text) if not INVISIBLE.match(ch)]
    view = "".join(text[i] for i in where)
    pieces, current = [(0, len(view), None)], view  # (start, end, None) for view[start:end], or (0, 0, new text)
    for stage in stages:
        spans = stage(current)
        if spans:
            pieces = _splice(pieces, spans)
            current = "".join(view[a:b] if new is None else new for a, b, new in pieces)
    if pieces == [(0, len(view), None)]:
        return text
    out = []
    for a, b, new in pieces:
        if new is not None:
            out.append(new)
        else:  # with the invisible characters before it, and after it at the end
            out.append(text[where[a - 1] + 1 if a else 0:where[b - 1] + 1 if b < len(view) else len(text)])
    return "".join(out)


def _size(piece):
    a, b, new = piece
    return b - a if new is None else len(new)


def _cut(piece, k):
    a, b, new = piece
    return ((a, a + k, None), (a + k, b, None)) if new is None else ((0, 0, new[:k]), (0, 0, new[k:]))


def _splice(pieces, spans):
    """`pieces` with each span, given in the text they make, replaced."""
    out, pieces, pos, i = [], list(pieces), 0, 0
    for start, end, replacement in spans:
        while i < len(pieces) and pos + _size(pieces[i]) <= start:
            out.append(pieces[i])
            pos, i = pos + _size(pieces[i]), i + 1
        if i < len(pieces) and pos < start:
            head, pieces[i] = _cut(pieces[i], start - pos)
            out.append(head)
            pos = start
        while i < len(pieces) and pos + _size(pieces[i]) <= end:
            pos, i = pos + _size(pieces[i]), i + 1
        if i < len(pieces) and pos < end:
            pieces[i] = _cut(pieces[i], end - pos)[1]
            pos = end
        if replacement:
            out.append((0, 0, replacement))
    return out + [p for p in pieces[i:] if _size(p)]


def mask(text):
    return _run(TEXT_STAGES, text)


def clip(text, limit, more=" …"):
    """The text masked whole, then cut to `limit` characters. Cutting first could leave the start of a token too
    short for mask() to know, or drop a secret's field name and keep its value."""
    text = mask(text)
    return text if len(text) <= limit else text[:limit] + more


def holds_secret(text):
    """Whether mask() would change this text, such as a config name."""
    return mask(text) != text


def secret_in_id(text):
    """Whether an item id holds a secret, so that kb does not store the item: a masked id could stand for two items.
    An id is a name someone chose, so it is read more narrowly than text: a provider's token shape or a credential in
    a link still counts, but a value its key names counts only in assignment form. A title such as "Tokens: 2026Q4
    plan" or "the password is changed" is a name, which mask() would mask in text."""
    return _run(ID_STAGES, text) != text


def mask_all(value, key=None):
    """mask() over every string in a value of dicts, lists and strings, such as an item's metadata. Only the
    secret-looking parts change, so a link or a path still points where it did. A value is masked with its key in
    front, as "key=value", so {"password": "..."} is caught by its field name as it would be in text."""
    if isinstance(value, str):
        if key is None:
            return mask(value)
        masked = mask(f"{key}={value}")
        head = mask(key) + "="
        return masked[len(head):] if masked.startswith(head) else mask(value)
    if isinstance(value, dict):
        return {mask(k) if isinstance(k, str) else k: mask_all(v, k if isinstance(k, str) else None)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_all(v, key) for v in value]
    if key is not None and isinstance(value, (int, float)) and not isinstance(value, bool) \
            and mask(f"{key}={value}") != f"{mask(key)}={value}":
        return "[masked]"  # a number its field name marks as secret, such as a numeric passcode
    return value


def _exc_text(exc_info):
    if exc_info is True:
        exc_info = sys.exc_info()
    elif isinstance(exc_info, BaseException):
        exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
    return "".join(traceback.format_exception(*exc_info)).rstrip() if exc_info and exc_info[0] else ""


class MaskedLog(logging.LoggerAdapter):
    """Logs through any logger, the caller's own included, with the message and any traceback masked first, so a
    token in an error never reaches a handler."""

    def __init__(self, logger):
        super().__init__(logger, {})

    def log(self, level, msg, *args, exc_info=None, stack_info=False, **kwargs):
        if not self.isEnabledFor(level):
            return
        text = str(msg) % args if args else str(msg)
        trace = _exc_text(exc_info)
        self.logger.log(level, mask(text + ("\n" + trace if trace else "")), **kwargs)


def masked_log(log):
    return log if isinstance(log, MaskedLog) else MaskedLog(log)


class MaskFilter(logging.Filter):
    """Masks a record before a handler writes it: on kb's own handlers it also catches records from other code."""

    def filter(self, record):
        text = record.getMessage()
        if record.exc_info:
            text += "\n" + _exc_text(record.exc_info)
        if record.stack_info:
            text += "\n" + record.stack_info
        record.msg, record.args, record.exc_info, record.exc_text, record.stack_info = mask(text), None, None, None, None
        return True


class MaskedStream:
    """A text stream that masks each write, for the kb command's stdout and stderr."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        return self._stream.write(mask(text))

    def __getattr__(self, name):
        return getattr(self._stream, name)


def clean(text):
    return text.encode("utf-8", "replace").decode("utf-8")


AUTH_NOISE = re.compile(
    r"(?i)(verification code|security code|confirmation code|one[- ]time (?:pass)?code|\botp\b|"
    r"2-step|two[- ]factor|\b2fa\b|sign[- ]in code|login code|log-in code|passcode is|your [\w ]{0,20}passcode|"
    r"magic link|"
    r"reset (?:your )?password|password reset|forgot (?:your )?password|your code is|"
    r"is your [\w ]{0,20}code|use this code|sign in to your account)")


def is_auth_noise(text):
    return bool(AUTH_NOISE.search(text[:600]))


# A code of 4 to 8 digits standing alone, not part of a date, version, decimal or path.
CODE = re.compile(r"(?<![\d./-])\d{4,8}(?![\d/-]|[.,]\d)")


def is_auth_code(text):
    """A message that hands over a code: auth wording and the code itself. Agent sessions drop such turns, but keep
    turns that only talk about codes, as when someone builds a sign-in flow."""
    return is_auth_noise(text) and bool(CODE.search(text[:600]))
