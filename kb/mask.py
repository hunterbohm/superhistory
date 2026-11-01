import base64
import bisect
import hashlib
import logging
import os
import re
import sys
import traceback

# JSON text escapes a line break as "\n" and other characters likewise, and a link as "%0A", once or, encoded twice,
# as "%250A". A secret right after an escape reads as glued to its letter, and a value ends at an escape that starts
# the next "name=" or "name:", as at the line's end the escape stands for, so that name keeps its own value. At most
# four escapes before the name: a longer run read back one escape at a time from every backslash in it would cost
# time in the square of its length.
_AFTER_ESCAPE = r"(?<=\\[nrtbf])|(?<=\\u[0-9A-Fa-f]{4})|(?<=%[0-9A-Fa-f]{2})|(?<=%25[0-9A-Fa-f]{2})"
_NEXT_NAME = r"(?:\\(?:[nrtbf]|u[0-9A-Fa-f]{4})){1,4}[A-Za-z0-9_.-]+[\"']?[ \t]*(?:[:=]|=>)"
_NB = rf"(?:(?<![A-Za-z0-9])|{_AFTER_ESCAPE})"
# A quote around a name or a value as text writes it: plain, escaped as JSON writes it, once or as JSON inside JSON
# does, percent-encoded as in a link, or as an HTML entity. And "=" or ":" between them, plain or encoded the same ways.
_QUOTE = r"(?:\\{0,7}[\"']|\\{1,7}u00(?:22|27)|%(?:25)?2[27]|&(?:quot|apos|#0{0,3}3[49]|#[xX]0{0,3}2[27]);)"
_SEP = r"(?:[:=\uff1a\uff1d]|=>|%(?:25)?3[ADad]|\\{1,7}u003[AaDd])"
# The space around a separator: spaces, tabs, line breaks, no-break and full-width spaces, and a space a link encodes,
# "%20" or "+".
_SPACE = r"(?:[ \t\r\n\u00a0\u3000+]|%(?:25)?20)"
_GAP = rf"{_SPACE}*"
# A value stops before a quote escaped to close it, so the escape stays with the quote. At most seven backslashes, as
# JSON three times inside JSON writes one: an unbounded run read ahead from every backslash would cost time in the
# square of its length.
_CLOSE = r"(?!\\{1,7}[\"']|&(?:quot|apos|#0{0,3}3[49]|#[xX]0{0,3}2[27]);)"
_WORD = rf"(?:\b|{_AFTER_ESCAPE})"
# A blank line ends a paragraph: an empty line, with any spaces, its breaks plain or escaped as in JSON.
_BLANK = r"(?:\r?\n[ \t]*\r?\n|\\(?:r\\)?n[ \t]*\\(?:r\\)?n)"
_PGP_KEY = r"PGP (?:PRIVATE|SECRET) KEY BLOCK"
_KEY_END = rf"-----END (?:[A-Z ]*PRIVATE KEY|{_PGP_KEY})-----"
# The rest of a paragraph, from a key's header or an encrypted key's fields on: past the blank line after a
# DEK-Info field, where an encrypted key's body starts, then to the next blank line, the next key's header, or the
# end of the text. Stopping at the next header leaves that key its own match.
_IN_PARAGRAPH = rf"(?:(?!{_BLANK})(?!-----BEGIN )[\s\S])"
_KEY_PARAGRAPH = rf"(?:{_IN_PARAGRAPH}*?DEK-Info:[^\r\n\\]*{_BLANK})?{_IN_PARAGRAPH}*"
# A key's footer, looked for in the next 16 KB and before any other header.
_TO_KEY_END = rf"(?:(?!-----BEGIN )[\s\S]){{0,16384}}?{_KEY_END}"
# From an encrypted key's fields, its footer comes after one blank line, the one after its DEK-Info field, so the
# search stops at the next.
_FIELDS_TO_KEY_END = rf"{_IN_PARAGRAPH}{{0,16384}}?(?:{_BLANK}{_IN_PARAGRAPH}{{0,16384}}?)?{_KEY_END}"
# An HTTP authorization scheme's word and its value are parted by spaces, or in a link by "%20" or "+".
_SCHEME_GAP = r"(?:\s|%(?:25)?20|\+)+"
BEARER = re.compile(rf"(?i){_WORD}bearer{_SCHEME_GAP}[A-Za-z0-9._~+/-]{{16,}}=*")
BASIC = re.compile(rf"(?i){_WORD}basic{_SCHEME_GAP}(?=[A-Za-z0-9+/]*[0-9+/=])[A-Za-z0-9+/]{{12,}}={{0,2}}")
# A secret's value ends at a space, tab or line break, and the space around its "=" is spaces, tabs and line breaks (a
# JSON or YAML value can sit on the next line): \s would also take U+2028, U+2029, U+0085, a vertical tab or a form
# feed, which end a value early or make "password=\u2028password" look like a placeholder.
SECRET_PATTERNS = [
    # A private key, by its structure and not read line by line: from its header through its footer, or, with no
    # footer, through the end of its paragraph. So a key cut off, wrapped at any width or followed at once by other
    # text is masked whole; what follows a key without a footer in its paragraph is masked with it, which is the cost.
    re.compile(rf"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:{_TO_KEY_END}|{_KEY_PARAGRAPH})"),
    # A PGP armored private key the same way, whose body comes after a blank line, after any armor headers.
    re.compile(rf"-----BEGIN {_PGP_KEY}-----(?:{_TO_KEY_END}|{_IN_PARAGRAPH}*(?:{_BLANK}{_IN_PARAGRAPH}*)?)"),
    # A PuTTY key file's private lines and its MAC, through the end of their paragraph.
    re.compile(rf"Private-Lines: *\d+{_IN_PARAGRAPH}*"),
    re.compile(_NB + r"AGE-SECRET-KEY-1[0-9A-Z]{20,}"),
    # ssh.com's SSH2 key files, from the header through the footer, or through the end of the paragraph.
    re.compile(rf"---- BEGIN SSH2 [A-Z ]*PRIVATE KEY ----(?:(?!---- BEGIN )[\s\S]){{0,16384}}?"
               rf"---- END SSH2 [A-Z ]*PRIVATE KEY ----|---- BEGIN SSH2 [A-Z ]*PRIVATE KEY ----{_IN_PARAGRAPH}*"),
    # An encrypted key whose header an older kb's row-by-row remask took: from its fields, the same way, or from what
    # that remask left of them, "[masked],ENCRYPTED".
    re.compile(rf"(?:Proc-Type: *4,ENCRYPTED|\[masked\],ENCRYPTED|DEK-Info:)"
               rf"(?:{_FIELDS_TO_KEY_END}|{_KEY_PARAGRAPH}(?:{_BLANK}{_IN_PARAGRAPH}*)?)"),
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
    BEARER,
    BASIC,
]
# In an item id, "Bearer" and "Basic" are words: "Ring Bearer duties/plan.md" names a note. A Basic credential still
# counts there by what it is, "user:password" in base64; a bearer token counts only by a provider's shape above.
_ID_PATTERNS = [p for p in SECRET_PATTERNS if p not in (BEARER, BASIC)]
_BASIC_VALUE = re.compile(rf"(?i){_WORD}basic{_SCHEME_GAP}([A-Za-z0-9+/]{{12,}})")
PREFIXED_SECRETS = [
    re.compile(rf"(?i)({_WORD}(?:set-)?cookie{_QUOTE}?\s*(?::|%(?:25)?3[Aa])\s*{_QUOTE}?)(?:{_CLOSE}[^\"'\n]){{8,}}"),
    # A link's scheme starts a word and is at most 32 characters: read on from every letter of a long run, it took
    # time in the square of the run's length. Its slashes may be escaped, as some JSON writers write them.
    re.compile(rf"((?:(?<![A-Za-z0-9+.-])|{_AFTER_ESCAPE})[a-z][a-z0-9+.-]{{0,31}}:(?:\\?/){{2}}[^/ \t\n\r:@'\"]+:)"
               r"[^/ \t\n\r@'\"]{3,}(?=@)"),
    # The same link percent-encoded, once or twice, as a parameter of another link, its slashes encoded or not.
    re.compile(rf"(?i)((?:(?<![A-Za-z0-9+.-])|{_AFTER_ESCAPE})[a-z][a-z0-9+.-]{{0,31}}%(?:25)?3A(?:%(?:25)?2F|/){{2}}"
               r"(?:(?!%(?:25)?(?:3A|2F|40))[^/ \t\n\r@'\"&])+%(?:25)?3A)(?:(?!%(?:25)?(?:2F|40))[^/ \t\n\r@'\"&]){3,}"
               r"(?=%(?:25)?40)"),
    # openssl's "-passin pass:..." and "-passout pass:...".
    re.compile(rf"((?<![A-Za-z0-9_-])-pass(?:in|out)?[ \t]+['\"]?pass:)(?:(?!{_NEXT_NAME}){_CLOSE}[^ \t\n\r'\"])+"),
]
_KEY_NAME = (
    r"(?i)(?<![A-Za-z0-9_.-])("
    r"[A-Za-z0-9_.-]*?(?:api[_-]?key|access[_-]?key|private[_-]?key|secret|token|credentials?|passw(?:or)?d|passcode|"
    r"session[_-]?(?:token|key|secret|cookie)|bw_session)[A-Za-z0-9_-]*?"
    r"|[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*[_-](?:pass|pwd|pw)"
    r"|x-(?:[A-Za-z0-9]+-)*auth(?:-[A-Za-z0-9]+)*"
    r")(?![A-Za-z0-9_.-])")
_ENV_KEY_NAME = rf"(?:(?<![A-Za-z0-9_])|{_AFTER_ESCAPE})([A-Z][A-Z0-9_]*_KEY)(?![A-Za-z0-9_])"
_VALUE = rf"((?:(?!{_NEXT_NAME}){_CLOSE}[^ \t\n\r\"'`,;<>)\]}}])+)"
KEYED_SECRET = re.compile(_KEY_NAME + rf"({_QUOTE}?{_GAP}(?:{_SEP}|\bis\b){_GAP}{_QUOTE}?)" + _VALUE)
ENV_KEY_SECRET = re.compile(_ENV_KEY_NAME + rf"({_QUOTE}?{_GAP}{_SEP}{_GAP}{_QUOTE}?)" + _VALUE)
# A long flag named for a secret, and its value after a space or a quote: "--api-key ...". A flag that names where the
# secret is, such as "--token-file" or "--password-stdin", takes none, and a value never starts with "-".
FLAG_SECRET = re.compile(rf"(?i)(?:(?<![A-Za-z0-9_.-])|{_AFTER_ESCAPE})(--" + _KEY_NAME[len(r"(?i)(?<![A-Za-z0-9_.-])("):]
                         + r"(?<!-stdin)(?<!-file)(?<!-env)(?<!-path)(?<!-name)(?<!-id)(?<!-cmd)(?<!-command)"
                         r"(?<!-helper)(?<!-length)(?<!-type)(?<!-url)((?:[ \t]|%(?:25)?20)+[\"']?)(?!-)" + _VALUE)
# An HTTP Authorization header's credentials, after any scheme word: "Token ...", "Bot ...", "ApiKey ...".
AUTH_SECRET = re.compile(rf"(?i)((?:(?<![A-Za-z0-9_.-])|{_AFTER_ESCAPE})(?:proxy-)?authorization)"
                         rf"({_QUOTE}?[ \t]*(?::|=|%(?:25)?3[ADad])[ \t]*{_QUOTE}?(?:[A-Za-z][A-Za-z0-9_-]*[ \t]+)?)"
                         + _VALUE)
# Commands that take a password after a short flag, which counts only on that command's line: from its first word to a
# pipe, ";" or a line break, plain or escaped. Each row: the command, the flag and what parts it from the password, and
# whether any value counts; otherwise a value of letters only after a space is a word, as after "is" ("docker login -p
# is deprecated"). curl's -u takes "user:password", and mysql's -p takes its password glued, since "-p shop" names a
# database.
COMMAND_FLAGS = (
    (r"curl", r"\s(?:-u|--user)\s*['\"]?[^ \t\n\r:'\"]+:", True),
    (r"mysql|mysqldump|mysqladmin|mariadb|mariadb-dump", r"\s-p(?=[^ \t\n\r'\"])", True),
    (r"sshpass", r"\s-p[ \t]*['\"]?", False),
    (r"(?:docker|podman|nerdctl|buildah|skopeo|oras)\s+login|helm\s+registry\s+login", r"\s-p[ \t]+['\"]?", False),
    (r"redis-cli", r"\s-a[ \t]+['\"]?", False),
)
_COMMANDS = [(re.compile(rf"(?i)(?:(?<![A-Za-z0-9_.-])|{_AFTER_ESCAPE})(?:{command})(?![A-Za-z0-9_.-])"
                         r"(?:[^\n|;\\]|\\(?![nr]))*"),
              re.compile(rf"({flag})((?:{_CLOSE}[^ \t\n\r'\"])+)"), always) for command, flag, always in COMMAND_FLAGS]
_ANY_COMMAND = re.compile(r"(?i)curl|mysql|mariadb|sshpass|login|redis-cli")
# A field name that by itself names a secret, as a dict's key does.
_SECRET_FIELD = re.compile(rf"(?:{_KEY_NAME[4:]}|{_ENV_KEY_NAME})$", re.I)
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
REVISION = 3
VERSION = hashlib.sha256(repr((REVISION, [p.pattern for p in SECRET_PATTERNS + PREFIXED_SECRETS], KEYED_SECRET.pattern,
                                ENV_KEY_SECRET.pattern, FLAG_SECRET.pattern, AUTH_SECRET.pattern, COMMAND_FLAGS,
                                PASSWORD_NAME.pattern, sorted(PLACEHOLDERS), REFERENCE.pattern)).encode()).hexdigest()[:16]


def _looks_secret(name, sep, value):
    """Whether `value`, after a secret's name `name` and the separator `sep`, is a secret and not a word, a placeholder
    or a pointer to one."""
    if value.lower() in PLACEHOLDERS or REFERENCE.search(value):
        return False
    if PASSWORD_NAME.search(name):
        word = value.rstrip(".!?")
        return len(value) >= 4 and (not _prose(sep) or bool(re.search(r"[^A-Za-z]", word)))
    mixed = re.search(r"\d", value) and re.search(r"[A-Za-z]", value)
    return (len(value) >= 16 and not value.isdigit()) or bool(len(value) >= 8 and mixed)


_SEP_CHARACTER = re.compile(r"[:=\uff1a\uff1d]|%(?:25)?3[ADad]|u003[AaDd]")


def _prose(sep):
    """Whether a separator reads as prose does, "is" or a space with no "=" or ":" in it, after which a value of
    letters only is a word and not a password."""
    return not _SEP_CHARACTER.search(sep) and ("is" in sep.lower() or sep[-1:].isspace())


def _command_passwords(text):
    """`text` with each password COMMAND_FLAGS finds masked. A command's line is read once from its first word, so a
    line that names the command again and again costs time in proportion to its length."""
    if not _ANY_COMMAND.search(text):
        return text
    for line_pattern, flag, always in _COMMANDS:
        spans = [(m.start(2), m.end(2)) for line in line_pattern.finditer(text)
                 for m in flag.finditer(text, line.start(), line.end())
                 if always or _looks_secret("password", m.group(1), m.group(2))]
        text = _replace(text, spans, "[masked]")
    return text


def _mask_keyed(m):
    name, sep, value = m.groups()
    return f"{name}{sep}[masked]" if _looks_secret(name, sep, value) else m.group(0)


KEY_FOOTER = re.compile(_KEY_END)
_BLANK_LINE = re.compile(_BLANK)
# Characters glued to a placeholder are the rest of a secret an older kb masked a chunk at a time, or what its key
# pattern left of the next secret's name, as in "[masked]_key: value": today's patterns take every such character, so
# they never leave one glued. Such a name keeps its value masked with it.
_GLUED_RUN = re.compile(r"\[masked\](?:[A-Za-z0-9_.-]+[ \t]*(?:[:=]|=>)[ \t]*[\"']?" + _VALUE[1:-1]
                        + r"|[A-Za-z0-9_+/=-]+)")
# How every private key's body starts: DER, a SEQUENCE in long form ("MII...", "MIG...") or in short form before an
# INTEGER or a SEQUENCE ("MHcC...", "MC4C..."), or OpenSSH's "openssh-key-v1" ("b3Bl...").
_KEY_BODY_START = r"(?:MI[E-P][A-Za-z0-9+/]|M[A-H][A-Za-z0-9+/][Cw]|b3Bl)"
# The rest of a key after an older kb's placeholder: base64 glued to it and running to a line break, as main's key
# pattern leaves when it took the line break too; or a key's body from its first line, cut anywhere, after what can
# stand between a line's start and its text: line breaks plain or escaped at any depth, quotes, "#", ">", "+", <br> and
# spaces. main's pattern stops at the first character that is not base64 or a space, so in escaped text, in code with
# a string a line, behind comment or quote marks and where a chunk ended between a header and its line break, it
# leaves exactly that. Masked through its footer, or through the end of its paragraph.
_BEFORE_BODY = r"(?:\\{1,7}[nrt]|<br\s*/?>|[\s\"'#>+,;|*])"
_KEY_REST = re.compile(rf"\[masked\]((?:[A-Za-z0-9+/=]+[ \t\r]*(?=\n|\\(?:r\\)?n)"
                       rf"|(?={_BEFORE_BODY}{{0,64}}{_KEY_BODY_START}))"
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
    text = _command_passwords(text)
    for pattern in (KEYED_SECRET, ENV_KEY_SECRET, FLAG_SECRET, AUTH_SECRET):
        text = pattern.sub(_mask_keyed, text)
    return text


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


def _basic_credential(value):
    """Whether base64 decodes to "user:password" text, as a Basic credential does."""
    try:
        decoded = base64.b64decode(value[:len(value) // 4 * 4], validate=True).decode("utf-8")
    except ValueError:  # not base64, or not text
        return False
    return ":" in decoded and decoded.isprintable()


# Fields of an item's metadata that name where its reader read it, and that its reader's id starts with, joined by a
# colon: a Hermes profile, as in "secret-project:20261028_101500_a1b2c3", which would read as a secret's name and value.
ID_NAMES = ("profile",)


def id_names(meta):
    """The values an item's metadata records for ID_NAMES, in order."""
    return tuple(meta.get(field) for field in ID_NAMES) if isinstance(meta, dict) else ()


def secret_in_id(text, names=()):
    """Whether an item id holds a secret, so that kb does not store the item: a masked id could stand for two items.
    An id is a name someone chose, so it is read more narrowly than text: a provider's token shape or a credential in
    a link still counts, but a value its key names counts only in assignment form, and a Basic value only as base64
    of "user:password". A title such as "Tokens: 2026Q4 plan" or "the password is changed" is a name, which mask()
    would mask in text. `names` are what the id starts with, each joined by a colon, as the item's metadata records
    them (id_names): each is read by itself and the rest of the id apart from it. A name that is a secret's word by
    itself, such as "password", is not read apart, so "password:..." still counts."""
    name = names[0] if names else None
    if (isinstance(name, str) and name and text.startswith(f"{name}:") and not KEY_WORD.fullmatch(name)
            and not text.startswith("//", len(name) + 1)):
        return secret_in_id(name) or secret_in_id(text[len(name) + 1:], names[1:])
    if any(_basic_credential(m.group(1)) for m in _BASIC_VALUE.finditer(text)):
        return True
    masked = text
    for pattern in _ID_PATTERNS:
        masked = pattern.sub("[masked]", masked)
    masked = _GLUED_RUN.sub("[masked]", _headless_keys(_key_rest(masked)))
    for pattern in PREFIXED_SECRETS:
        masked = pattern.sub(lambda m: m.group(1) + "[masked]", masked)
    masked = _command_passwords(masked)
    return ENV_KEY_ID.sub(_mask_keyed, KEYED_ID.sub(_mask_keyed, masked)) != text


def mask_all(value, key=None):
    """mask() over every string in a value of dicts, lists and strings, such as an item's metadata. Only the
    secret-looking parts change, so a link or a path still points where it did. A value is masked with its key in
    front, as "key=value", so {"password": "..."} is caught by its field name as it would be in text."""
    if isinstance(value, str):
        if key is None:
            return mask(value)
        if _SECRET_FIELD.match(key) and _looks_secret(key, "=", value.strip()):
            return "[masked]"  # the whole value, which may hold a space or a quote that would end it in text
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
