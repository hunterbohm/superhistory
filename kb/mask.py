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
# For a key cut off before its footer: what may follow a line of it on the same line (not "=", ":", "=>" or "is",
# which name the next secret), and where its cut last line may start: right after the header, another of its lines,
# or the blank line after an encrypted key's fields. Such a line holds a capital, a digit or "+/=", as base64 almost
# always does and a word of prose does not.
_NOT_ASSIGNED = r"(?![ \t]*(?:[:=]|=>|is\b))"
_AFTER_KEY_LINE = (r"(?:(?<=[A-Za-z0-9+/=\n-]\n)|(?<=[A-Za-z0-9+/=\n-]\r\n)|(?<=[A-Za-z0-9+/=-]\\n)"
                   r"|(?<=[A-Za-z0-9+/=-]\\r\\n))")
# The base64 lines of a key after its header (or fields), as above, and its footer if there is one.
_KEY_LINES = (r"(?:(?:\s|\\[nr])*(?:[A-Za-z0-9+/=]+(?=[ \t]*(?:[\r\n\"'…\\]|\.\.\.|\Z|-----)"
              r"|(?<=[A-Za-z0-9+/=]{16})[ \t]+" + _NOT_ASSIGNED + r")|" + _AFTER_KEY_LINE
              + r"(?=[a-z]*[A-Z0-9+/=])[A-Za-z0-9+/=]+(?=[ \t]+" + _NOT_ASSIGNED + r")))*"
              r"(?:(?:\s|\\[nr])*-----END [A-Z ]*PRIVATE KEY-----)?")
SECRET_PATTERNS = [
    # A whole key, header to footer, whatever its lines' lengths, with the header fields an encrypted key has. A dash
    # may not start "-----", so a header without a footer stops at the next such line instead of scanning on.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:[A-Za-z0-9+/=\s:,]|-(?!----)|\\[nr])*?"
               r"-----END [A-Z ]*PRIVATE KEY-----"),
    # A key cut off before its footer, its line breaks plain or escaped as in a service account's JSON key or a
    # repr(): the header, an encrypted key's header fields, and each whole line of base64 after them, any length, up to
    # a line that is not one. A line may end in a cut's "…" or "..." or a backslash an escaped break was cut at, and
    # one of a key line's length may run on into words, but not into an assignment: so the name of the next secret,
    # as in "clientSecretToken: X" or "... = X", stays for that secret's own rule.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:(?:\s|\\[nr])*(?:Proc-Type|DEK-Info):[^\r\n\\]*)*" + _KEY_LINES),
    # An encrypted key's body after its DEK-Info line, where an older kb's row-by-row remask took the header and left
    # the rest: the line names the cipher, so the base64 lines after it are a key's.
    re.compile(r"DEK-Info:[ \t]*[A-Z0-9-]+,[0-9A-Fa-f]+" + _KEY_LINES),
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
                      r"passcode|pass|pwd|pw|session[_-]?(?:token|key|secret|cookie)|bw_session|(?:set-)?cookie|"
                      r"\w+_key)")
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
REVISION = 2
VERSION = hashlib.sha256(repr((REVISION, [p.pattern for p in SECRET_PATTERNS + PREFIXED_SECRETS], KEYED_SECRET.pattern,
                                ENV_KEY_SECRET.pattern, PASSWORD_NAME.pattern, sorted(PLACEHOLDERS),
                                REFERENCE.pattern)).encode()).hexdigest()[:16]


def _mask_keyed(m):
    name, sep, value = m.groups()
    if value.lower() in PLACEHOLDERS or REFERENCE.search(value):
        return m.group(0)
    if PASSWORD_NAME.search(name):
        word = value.rstrip(".!?")
        looks_secret = len(value) >= 4 and ("is" not in sep.lower() or re.search(r"[^A-Za-z]", word))
    else:
        mixed = re.search(r"\d", value) and re.search(r"[A-Za-z]", value)
        looks_secret = (len(value) >= 16 and not value.isdigit()) or (len(value) >= 8 and mixed)
    return f"{name}{sep}[masked]" if looks_secret else m.group(0)


KEY_FOOTER = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")
_KEY_BODY = frozenset(string.ascii_letters + string.digits + "+/=")
_KEY_RUN = re.compile(r"[A-Za-z0-9+/=]+")
_KEY_BREAK = re.compile(r"\r?\n|\\(?:r\\)?n")
_PLACEHOLDER = re.compile(r"\[masked\]")
# Characters glued to a placeholder are the rest of a secret an older kb masked a chunk at a time, or what its key
# pattern left of the next secret's name, as in "[masked]_key: value": today's patterns take every such character, so
# they never leave one glued. Such a name keeps its value masked with it.
_GLUED_RUN = re.compile(r"\[masked\](?:[A-Za-z0-9_.-]+[ \t]*(?:[:=]|=>)[ \t]*[\"']?" + _VALUE[1:-1]
                        + r"|[A-Za-z0-9_+/=-]+)")
# A key's body lines are 64 characters; only its last line, next to the footer or at the end, is shorter.
FULL_LINE = 16


def _spaces_end(text, pos):
    while pos < len(text) and text[pos] in " \t\r":
        pos += 1
    return pos


def _after_break(text, pos):
    """Past any spaces and a line break, plain or escaped, from `pos`, and a blank line after it, as an encrypted
    key has between its fields and its body."""
    for _ in range(2):
        brk = _KEY_BREAK.match(text, _spaces_end(text, pos))
        if not brk:
            break
        pos = brk.end()
    return pos


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
    The lines may be of any length, as a key wrapped narrow has them, and the top one may follow other text on its
    line if it is of a key line's length. Lowercase words alone before a footer stay: a key's base64 has a capital, a
    digit or "+/=" in any 16 characters, and a short lowercase line on top of the lines is a word, not a key's."""
    spans = []
    for footer in KEY_FOOTER.finditer(text):
        start, size, line = footer.start(), 0, _key_line_before(text, footer.start())
        while line:
            whole = line[0] == 0 or text.endswith(("\n", "\\n"), 0, line[0])
            short = line[1] - line[0] < FULL_LINE
            above = _key_line_before(text, line[0]) if whole else None
            if short and (not whole or (not above and text[line[0]:line[1]].islower())):
                break
            start, size, line = line[0], size + line[1] - line[0], above
        if size >= FULL_LINE and re.search(r"[A-Z0-9+/=]", text[start:footer.start()]):
            spans.append((start, footer.end()))
    return _replace(text, spans, "[masked]")


def _key_rest(text):
    """The rest of a key's body after a placeholder, as an older kb's row-by-row remask leaves the chunk after the
    header's: the line glued to it, the base64 lines after it, and the footer if there is one. main's key pattern
    stops at a backslash, so a placeholder followed at once by an escaped line break counts as glued too, and the
    lines after a glued placeholder may be of any length. A word glued to a placeholder, with no line of a key's
    shape, stays, and so does one line of a key's shape on the line after an unglued one, such as a hash. The last
    line may run on into words, which stay, but not into an assignment."""
    spans = []
    for glued in _PLACEHOLDER.finditer(text):
        run = _KEY_RUN.match(text, glued.end())
        end = run.end() if run else glued.end()
        first, lines, full = end - glued.end(), 0, 0
        stuck = bool(first) or text.startswith(("\\n", "\\r\\n"), glued.end())
        while _line_ends(text, end):
            run = _KEY_RUN.match(text, _after_break(text, end))
            if not run or run.start() == end:
                break
            length = run.end() - run.start()
            if not _line_ends(text, run.end()):
                base64ish = length >= FULL_LINE or (stuck and not run.group().islower())
                if (base64ish and text[run.end()] in " \t"
                        and not re.match(r"[ \t]*(?:[:=]|=>|is\b)", text[run.end():run.end() + 8])):
                    end, lines, full = run.end(), lines + 1, full + 1
                break
            if length < FULL_LINE and not stuck and not _last_line(text, run.end()):
                break
            end, lines, full = run.end(), lines + 1, full + (length >= FULL_LINE)
        if (lines if stuck else full >= 2) or (first >= FULL_LINE and (_line_ends(text, end) or text[end] in " \t\r")):
            footer = KEY_FOOTER.match(text, _after_break(text, end))
            spans.append((glued.end(), footer.end() if footer else end))
    return _replace(text, spans, "")


def _replace(text, spans, replacement):
    """`text` with each of the ordered spans replaced, built once: one copy per span would be quadratic."""
    out, last = [], 0
    for start, end in spans:
        out += [text[last:start], replacement]
        last = end
    return "".join(out + [text[last:]])


def mask(text):
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[masked]", text)
    text = _GLUED_RUN.sub("[masked]", _headless_keys(_key_rest(text)))
    for pattern in PREFIXED_SECRETS:
        text = pattern.sub(lambda m: m.group(1) + "[masked]", text)
    return ENV_KEY_SECRET.sub(_mask_keyed, KEYED_SECRET.sub(_mask_keyed, text))


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
    masked = text
    for pattern in SECRET_PATTERNS:
        masked = pattern.sub("[masked]", masked)
    masked = _GLUED_RUN.sub("[masked]", _headless_keys(_key_rest(masked)))
    for pattern in PREFIXED_SECRETS:
        masked = pattern.sub(lambda m: m.group(1) + "[masked]", masked)
    return ENV_KEY_ID.sub(_mask_keyed, KEYED_ID.sub(_mask_keyed, masked)) != text


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
