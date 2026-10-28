import bisect
import hashlib
import logging
import os
import re
import sys
import traceback

_NB = r"(?<![A-Za-z0-9])"
# A blank line ends a paragraph: an empty line, with any spaces, its breaks plain or escaped as in JSON.
_BLANK = r"(?:\r?\n[ \t]*\r?\n|\\(?:r\\)?n[ \t]*\\(?:r\\)?n)"
_KEY_END = r"-----END [A-Z ]*PRIVATE KEY-----"
# The rest of a paragraph, from a key's header or an encrypted key's fields on: past the blank line after a
# DEK-Info field, where an encrypted key's body starts, then to the next blank line, the next key's header, or the
# end of the text. Stopping at the next header leaves that key its own match.
_IN_PARAGRAPH = rf"(?:(?!{_BLANK})(?!-----BEGIN )[\s\S])"
_KEY_PARAGRAPH = rf"(?:{_IN_PARAGRAPH}*?DEK-Info:[^\r\n\\]*{_BLANK})?{_IN_PARAGRAPH}*"
# A key's footer, looked for in the next 16 KB and before any other header.
_TO_KEY_END = rf"(?:(?!-----BEGIN )[\s\S]){{0,16384}}?{_KEY_END}"
# A secret's value ends at a space, tab or line break, and the space around its "=" is spaces, tabs and line breaks (a
# JSON or YAML value can sit on the next line): \s would also take U+2028, U+2029, U+0085, a vertical tab or a form
# feed, which end a value early or make "password=\u2028password" look like a placeholder.
SECRET_PATTERNS = [
    # A private key, by its structure and not read line by line: from its header through its footer, or, with no
    # footer, through the end of its paragraph. So a key cut off, wrapped at any width or followed at once by other
    # text is masked whole; what follows a key without a footer in its paragraph is masked with it, which is the cost.
    re.compile(rf"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:{_TO_KEY_END}|{_KEY_PARAGRAPH})"),
    # An encrypted key whose header an older kb's row-by-row remask took: from its fields, the same way, or from what
    # that remask left of them, "[masked],ENCRYPTED".
    re.compile(rf"(?:Proc-Type: *4,ENCRYPTED|\[masked\],ENCRYPTED|DEK-Info:)"
               rf"(?:{_TO_KEY_END}|{_KEY_PARAGRAPH}(?:{_BLANK}{_IN_PARAGRAPH}*)?)"),
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


KEY_FOOTER = re.compile(_KEY_END)
_BLANK_LINE = re.compile(_BLANK)
# Characters glued to a placeholder are the rest of a secret an older kb masked a chunk at a time, or what its key
# pattern left of the next secret's name, as in "[masked]_key: value": today's patterns take every such character, so
# they never leave one glued. Such a name keeps its value masked with it.
_GLUED_RUN = re.compile(r"\[masked\](?:[A-Za-z0-9_.-]+[ \t]*(?:[:=]|=>)[ \t]*[\"']?" + _VALUE[1:-1]
                        + r"|[A-Za-z0-9_+/=-]+)")
# The rest of a key after an older kb's placeholder: base64 glued to it and running to a line break, or, where main's
# key pattern stopped at a backslash, an escaped line break right after it and a key line, 16 characters of base64 or
# more as real keys' are. Masked through its footer, or through the end of its paragraph.
_KEY_REST = re.compile(rf"\[masked\]((?:[A-Za-z0-9+/=]+[ \t\r]*(?=\n|\\(?:r\\)?n)"
                       rf"|(?=\\(?:r\\)?n[A-Za-z0-9+/=]{{16}}))"
                       rf"(?:(?!{_KEY_END}){_IN_PARAGRAPH})*(?:{_KEY_END})?)")


def _headless_keys(text):
    """A private key's footer with no header before it, as an older kb's row-by-row remask or a body pasted alone
    leaves one: masked from the start of its paragraph through it. Whatever else that paragraph holds before the
    footer is masked too, which is the cost, as after a key without a footer."""
    if "-----END" not in text:
        return text
    starts, spans = [m.end() for m in _BLANK_LINE.finditer(text)], []
    for footer in KEY_FOOTER.finditer(text):
        i = bisect.bisect_right(starts, footer.start()) - 1
        start = max(starts[i] if i >= 0 else 0, spans[-1][1] if spans else 0)
        spans.append((start, footer.end()))
    return _replace(text, spans, "[masked]")


def _key_rest(text):
    """The rest of a key after an older kb's placeholder, as _KEY_REST finds it, removed after the placeholder."""
    return _replace(text, [(m.start(1), m.end()) for m in _KEY_REST.finditer(text)], "")


def _replace(text, spans, replacement):
    """`text` with each of the ordered spans replaced, built once: one copy per span would be quadratic."""
    out, last = [], 0
    for start, end in spans:
        out += [text[last:start], replacement]
        last = end
    return "".join(out + [text[last:]])


def _mask_once(text):
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[masked]", text)
    text = _GLUED_RUN.sub("[masked]", _headless_keys(_key_rest(text)))
    for pattern in PREFIXED_SECRETS:
        text = pattern.sub(lambda m: m.group(1) + "[masked]", text)
    return ENV_KEY_SECRET.sub(_mask_keyed, KEYED_SECRET.sub(_mask_keyed, text))


# How many passes mask() makes at most, and how often, in this process, a text still changed at the last one.
MAX_PASSES = 8
capped = 0


def mask(text):
    """The text with every secret replaced by "[masked]". A placeholder can make the text next to it read as a secret
    or as a key's leftovers, so masking repeats until a pass changes nothing: masked text masks to itself, and text
    with no secret costs one pass. No pattern matches a placeholder alone, so it stops; should a text still change at
    the last pass, the region still changing is masked whole, and counted in `capped`."""
    global capped
    original = text
    for _ in range(MAX_PASSES):
        masked = _mask_once(text)
        if masked == text:
            return text
        text = masked
    # Still changing: everything between what the original and the last pass share at both ends becomes one mask.
    head = len(os.path.commonprefix([original, text]))
    tail = len(os.path.commonprefix([original[head:][::-1], text[head:][::-1]]))
    capped += 1
    return text[:head] + "[masked]" + text[len(text) - tail:]


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
