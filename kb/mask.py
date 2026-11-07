import ast
import base64
import bisect
import calendar
import codecs
import contextlib
import contextvars
import functools
import hashlib
import itertools
import json
import logging
import math
import os
import re
import shlex
import sys
import traceback
import warnings

from kb import gitleaks

# JSON text escapes a line break as "\n" and other characters likewise, and a link as "%0A", once or, encoded twice,
# as "%250A". A secret right after an escape reads as glued to its letter, and a value ends at an escape that starts
# the next "name=" or "name:", as at the line's end the escape stands for, so that name keeps its own value. At most
# four escapes before the name: a longer run read back one escape at a time from every backslash in it would cost
# time in the square of its length.
_AFTER_ESCAPE = r"(?<=\\[nrtbf])|(?<=\\u[0-9A-Fa-f]{4})|(?<=%[0-9A-Fa-f]{2})|(?<=%25[0-9A-Fa-f]{2})"
_NEXT_NAME = r"(?:\\(?:[nrtbf]|u[0-9A-Fa-f]{4})){1,4}(?::?[A-Za-z0-9_.-]+[\"']?[ \t]*(?:[:=]|=>)|-{1,2}[A-Za-z])"
_NB = rf"(?:(?<![A-Za-z0-9])|{_AFTER_ESCAPE})"
# A quote around a name or a value as text writes it: plain, escaped as JSON writes it, once or as JSON inside JSON
# does, percent-encoded as in a link, or as an HTML entity. And "=" or ":" between them, plain or encoded the same ways.
_QUOTE = r"(?:\\{0,7}[\"']|\\{1,7}u00(?:22|27)|%(?:25)?2[27]|&(?:quot|apos|#0{0,3}3[49]|#[xX]0{0,3}2[27]);)"
_SEP = r"(?:[:=\uff1a\uff1d]|=>|%(?:25)?3[ADad]|\\{1,7}u003[AaDd])"
# The space around a separator: spaces, tabs, line breaks, no-break and full-width spaces, and a space a link encodes,
# "%20" or "+".
_SPACE = r"(?:[ \t\r\n\u00a0\u3000+]|%(?:25)?20)"
_GAP = rf"{_SPACE}*"
# The same on one line, for a value that never sits on the next one, as a header's does.
_LINE_SPACE = r"(?:[ \t\u00a0\u3000+]|%(?:25)?20)"
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
# The DEK-Info field is read from the last one on its line: read from each, a line of them took time in the square of
# its length.
_KEY_PARAGRAPH = rf"(?:{_IN_PARAGRAPH}*?DEK-Info:(?:(?!DEK-Info:)[^\r\n\\])*{_BLANK})?{_IN_PARAGRAPH}*"
# A key's footer, looked for in the next 16 KB and before any other header.
_TO_KEY_END = rf"(?:(?!-----BEGIN )[\s\S]){{0,16384}}?{_KEY_END}"
# From an encrypted key's fields, its footer comes after one blank line, the one after its DEK-Info field, so the
# search stops at the next.
_FIELDS_TO_KEY_END = rf"{_IN_PARAGRAPH}{{0,16384}}?(?:{_BLANK}{_IN_PARAGRAPH}{{0,16384}}?)?{_KEY_END}"
# An HTTP authorization scheme's word and its value are parted by spaces, or in a link by "%20" or "+".
_SCHEME_GAP = r"(?:\s|%(?:25)?20|\+)+"
# A provider's "sk-" key. In an item id, only one after a provider's own prefix with a capital or a digit in it, or a
# run of 32 or more letters and digits, counts: "sk-learn-pipeline-experiments.md" and "sk-proj-roadmap.md" name notes.
SK_TOKEN = re.compile(_NB + r"sk-(?:ant-|proj-|or-v1-)?[A-Za-z0-9_-]{20,}")
_SK_ID = re.compile(_NB + r"sk-(?:(?:ant|proj|svcacct|admin|or-v1)-(?=[A-Za-z0-9_-]*?[A-Z0-9])[A-Za-z0-9_-]{20,}"
                    r"|[A-Za-z0-9]{32,}(?![A-Za-z0-9]))")
BEARER = re.compile(rf"(?i){_WORD}bearer{_SCHEME_GAP}[A-Za-z0-9._~+/-]{{16,}}=*")
BASIC = re.compile(rf"(?i){_WORD}basic{_SCHEME_GAP}(?=[A-Za-z0-9+/]*[0-9+/=])[A-Za-z0-9+/]{{12,}}={{0,2}}")
# A secret's value ends at a space, tab or line break, and the space around its "=" is spaces, tabs and line breaks (a
# JSON or YAML value can sit on the next line): \s would also take U+2028, U+2029, U+0085, a vertical tab or a form
# feed, which end a value early or make "password=\u2028password" look like a placeholder.
# A private key percent-encoded, as a link's parameter holds one: its header, its body and its line breaks encoded,
# through its footer or the end of the encoded run.
ENCODED_PRIVATE_KEY = re.compile(r"-----BEGIN(?:(?:%20|\+)[A-Z]+)*(?:%20|\+)PRIVATE(?:%20|\+)KEY-----"
                                 r"(?:%0[ADad]|%2[BFbf]|%3[Dd]|[A-Za-z0-9+/=]){0,16384}"
                                 r"(?:-----END(?:(?:%20|\+)[A-Z]+)*(?:%20|\+)PRIVATE(?:%20|\+)KEY-----)?")
SECRET_PATTERNS = [
    # A private key, by its structure and not read line by line: from its header through its footer, or, with no
    # footer, through the end of its paragraph. So a key cut off, wrapped at any width or followed at once by other
    # text is masked whole; what follows a key without a footer in its paragraph is masked with it, which is the cost.
    re.compile(rf"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:{_TO_KEY_END}|{_KEY_PARAGRAPH})"),
    ENCODED_PRIVATE_KEY,
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
    SK_TOKEN,
    # A Docker config written as base64, as a Kubernetes .dockerconfigjson holds it: '{"auths":' and its logins.
    re.compile(_NB + r"eyJhdXRocyI6[A-Za-z0-9+/=]*"),
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
# In an item id, "Bearer" and "Basic" are words: "Ring Bearer duties/plan.md" names a note, and so does an "sk-" slug. A Basic credential still
# counts there by what it is, "user:password" in base64; a bearer token counts only by a provider's shape above.
_ID_PATTERNS = [_SK_ID if p is SK_TOKEN else p for p in SECRET_PATTERNS if p not in (BEARER, BASIC)]
_SCHEME_HEAD = re.compile(rf"(?i)(?:basic|bearer){_SCHEME_GAP}")
# A word in a note's path: lower case, capitalized, run together in capitals ("ApiKey"), in capitals, or a short
# capital start before lower case ("OAuth"); a file's extension at the end of a run; and what may follow a run in the
# same path to the file's extension, its other folders' and the file's own words.
_PATH_WORD = re.compile(r"[a-z]+|[A-Z][a-z]+|(?:[A-Z][a-z]+){2,}|[A-Z]{2,}|[A-Z]{1,3}[a-z]{2,}")
_EXTENSION = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,4}$")
_PATH_TAIL = re.compile(r"[A-Za-z0-9 _/.-]{0,160}?\.[A-Za-z][A-Za-z0-9]{0,4}(?![A-Za-z0-9])")
# The letter pairs that make up 99% of those in a common English word list, 367 of the 676: every pair in a word in a
# path is one of them, as a random run of letters seldom manages. And four vowels in a row, which no such word has.
_COMMON_PAIRS = frozenset((
    "ab ac ad ae af ag ah ai ak al am an ap ar as at au av aw ax ay az ba bb be bi bl bo br bs bu by ca cc ce "
    "ch ci ck cl co cr cs ct cu cy da db dd de dg dh di dl dm dn do dr ds du dv dw dy ea eb ec ed ee ef eg eh "
    "ei ek el em en eo ep eq er es et eu ev ew ex ey ez fa fe ff fi fl fo fr fs ft fu fy ga ge gg gh gi gl gm "
    "gn go gr gs gu gy ha he hi hl hm hn ho hr hs ht hu hw hy ia ib ic id ie if ig ik il im in io ip iq ir is "
    "it iu iv ix iz ja je ji jo ju ka ke kh ki kl kn ko kr ks ku ky la lb lc ld le lf lg li lk ll lm ln lo lp "
    "ls lt lu lv ly ma mb me mi mm mn mo mp ms mu my na nb nc nd ne nf ng nh ni nj nk nl nm nn no np nq nr ns "
    "nt nu nv nw ny oa ob oc od oe of og oh oi ok ol om on oo op or os ot ou ov ow ox oy oz pa pe ph pi pl po "
    "pp pr ps pt pu py qu ra rb rc rd re rf rg rh ri rk rl rm rn ro rp rr rs rt ru rv rw ry sa sb sc se sf sh "
    "si sk sl sm sn so sp sq ss st su sw sy ta tb tc te tf th ti tl tm tn to tr ts tt tu tw ty tz ua ub uc ud "
    "ue uf ug ui uk ul um un uo up ur us ut uz va ve vi vo wa we wh wi wl wn wo wr ws xa xc xe xi xp xt ya yb "
    "yc yd ye yi yl ym yn yo yp yr ys yt za ze zi zo zz").split())
_VOWEL_RUN = re.compile(r"(?i)[aeiou]{4}")
# A run of the letters of hex alone, as a token's hex body with no digit is, alone or after a provider's prefix: no
# word in a common English word list holds eight of them in a row. And the longest a path's word may be
# ("counterrevolutionaries").
_HEX_LETTERS = re.compile(r"[A-Fa-f]{8,}")
_LONGEST_WORD = 22
# The name of a header whose value is a credential, just before the scheme word.
_AUTHORIZATION_BEFORE = re.compile(r"(?i)authorization[\"']?[ \t]*(?::|=|%(?:25)?3A)[ \t]*[\"']?$")


def _scheme_words(m):
    """Whether a match of Basic or Bearer and what follows reads as words in a note's path, as in "Basic
    Statistics/notes.md" or "Ring Bearer responsibilities/notes.md", not a credential. That is: no header's value
    ("Authorization: Bearer ..."); part of a file's path (_in_a_path); past the file's extension, no digit, "+" or "=",
    not the letters of hex alone, and parts joined by "/", ".", "_" or "-", each a word (_PATH_WORD) with a vowel, no
    long run of hex letters and only common English letter pairs (_english), which a run of base64 or of random letters
    seldom passes; one part alone has a third of its letters vowels, as a word has. After Basic, not base64 of
    "user:password" either, alone or before a "/" (_holds_a_login), padded or not, in any single-byte code page or
    UTF-8. Any other match is no such run."""
    head = _SCHEME_HEAD.match(m.group())
    if not head or _AUTHORIZATION_BEFORE.search(m.string, max(0, m.start() - 64), m.start()):
        return False
    run = m.group()[head.end():]
    if not _in_a_path(m, run):
        return False
    run = _EXTENSION.sub("", run)
    parts = [part for part in re.split(r"[/._-]+", run) if part]
    if not parts or re.search(r"[0-9+=]", run) or _HEX_LETTERS.fullmatch("".join(parts)):
        return False
    if not all(_PATH_WORD.fullmatch(part) and re.search(r"[aeiouyAEIOUY]", part) and _english(part) for part in parts):
        return False
    if len(parts) == 1 and sum(c in "aeiouyAEIOUY" for c in parts[0]) * 3 < len(parts[0]):
        return False
    return not (head.group().lower().startswith("basic") and _holds_a_login(run))


def _holds_a_login(run):
    """Whether a Basic run is, or starts with, base64 of "user:password": the whole run, or its part before any "/",
    as a login written right before a path's words reads ("Basic <login>/weekly.md"), decodes, padded or not, to bytes
    holding ":" and no control byte, as a login in UTF-8, Latin-1 or any single-byte code page does. The run is decoded
    once and each part judged from it, so a run of many "/" costs time in proportion to its length."""
    raw = base64.b64decode(run[:len(run) // 4 * 4]) if re.fullmatch(r"[A-Za-z0-9+/]*", run) else b""
    if not raw and len(run) >= 4:
        return False
    colon = raw.find(b":")
    control = re.search(rb"[\x00-\x1f\x7f]", raw)
    colon, control = (len(raw) + 1 if colon < 0 else colon), (control.start() if control else len(raw) + 1)
    for end in [i for i, c in enumerate(run) if c == "/" and i >= 2] + [len(run)]:
        end -= end % 4 == 1  # a lone last character holds no byte
        blocks, rest = end // 4 * 3, run[end // 4 * 4:end]
        try:
            tail = base64.b64decode(rest + "=" * (-len(rest) % 4)) if rest else b""
        except ValueError:
            continue
        if (colon < blocks or b":" in tail) and not (control < blocks or re.search(rb"[\x00-\x1f\x7f]", tail)):
            return True
    return False


def _english(part):
    """Whether a part of a path reads as English words: each word in it, a part run together in capitals ("ApiKey")
    read word by word, is at most 22 letters, holds no run of hex letters a token's body would (_HEX_LETTERS), only
    common letter pairs, and no four vowels in a row."""
    for word in re.findall(r"[A-Z]{2,}(?![a-z])|[A-Z]?[a-z]+|[A-Z]+", part):
        word = word.lower()
        if len(word) > _LONGEST_WORD or _HEX_LETTERS.search(word) or _VOWEL_RUN.search(word) \
                or any(word[i:i + 2] not in _COMMON_PAIRS for i in range(len(word) - 1)):
            return False
    return True


def _in_a_path(m, run):
    """Whether a scheme word's run is part of a file's path: it ends in the file's extension, or the path goes on from
    it to one (_PATH_TAIL). Past a space, the path goes on only where the run already holds a "/", the next word is
    capitalized, as a folder's or a file's name often is, or the rest reaches a folder's "/": a token followed by prose
    that names a file, as in "Bearer <token> see notes.md", is in no path."""
    if _EXTENSION.search(run):
        return True
    tail = _PATH_TAIL.match(m.string, m.end())
    if not tail:
        return False
    rest = tail.group()
    return " " not in rest or "/" in run or "/" in rest or bool(re.match(r" +[A-Z]", rest))


_BASIC_VALUE = re.compile(rf"(?i){_WORD}basic{_SCHEME_GAP}([A-Za-z0-9+/]{{12,}})")
PREFIXED_SECRETS = [
    re.compile(rf"(?i)({_WORD}(?:set-)?cookie{_QUOTE}?\s*(?::|%(?:25)?3[Aa])\s*{_QUOTE}?)(?:{_CLOSE}[^\"'\n]){{8,}}"),
    # A link's scheme starts a word and is at most 32 characters: read on from every letter of a long run, it took
    # time in the square of the run's length. Its slashes may be escaped, as some JSON writers write them. The user
    # name may be empty, as Redis writes a password alone: "redis://:<password>@host".
    re.compile(rf"((?:(?<![A-Za-z0-9+.-])|{_AFTER_ESCAPE})[a-z][a-z0-9+.-]{{0,31}}:(?:\\?/){{2}}[^/ \t\n\r:@'\"]*:)"
               r"[^/ \t\n\r@'\"]{3,}(?=@)"),
    # The same link percent-encoded, once or twice, as a parameter of another link, its slashes encoded or not.
    re.compile(rf"(?i)((?:(?<![A-Za-z0-9+.-])|{_AFTER_ESCAPE})[a-z][a-z0-9+.-]{{0,31}}%(?:25)?3A(?:%(?:25)?2F|/){{2}}"
               r"(?:(?!%(?:25)?(?:3A|2F|40))[^/ \t\n\r@'\"&])*%(?:25)?3A)(?:(?!%(?:25)?(?:2F|40))[^/ \t\n\r@'\"&]){3,}"
               r"(?=%(?:25)?40)"),
    # openssl's "-passin pass:..." and "-passout pass:...".
    re.compile(rf"((?<![A-Za-z0-9_-])-pass(?:in|out)?[ \t]+['\"]?pass:)(?:(?!{_NEXT_NAME}){_CLOSE}[^ \t\n\r'\"])+"),
]
# A secret's name is a whole run of name characters whose last dotted part holds a key word, or one ending in "_pw" or
# the like. A tokenizer's name is none. Read as that part after the run's last dot, it takes one pass over a long run;
# read as any characters around a key word, it went back over the run once for every key word in it.
_KEY_NAME = (
    r"(?i)(?<![A-Za-z0-9_.-])("
    r"(?:[A-Za-z0-9_.-]*\.)?(?=[A-Za-z0-9_-]*?(?:api[_-]?key|access[_-]?key|private[_-]?key|"
    r"subscription[_-]?key(?![A-Za-z0-9_-])|"
    r"secret|token(?!i[sz](?:e(?:rs?|d|s)?|ing|ation)(?-i:(?![a-z])))|credentials?|passw(?:or)?d|passcode|session[_-]?(?:token|key|secret|cookie)|bw_session))"
    r"[A-Za-z0-9_-]+"
    r"|[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*[_-](?:pass|pwd|pw)"
    r"|x-(?=[A-Za-z0-9-]*?(?<=-)auth(?:-|(?![A-Za-z0-9_.-])))[A-Za-z0-9-]+"
    r")(?![A-Za-z0-9_.-])")
_ENV_KEY_NAME = rf"(?:(?<![A-Za-z0-9_])|{_AFTER_ESCAPE})([A-Z][A-Z0-9_]*_KEY)(?![A-Za-z0-9_])"
def _value(stop=""):
    """A secret's value: a run that stops before a space, a quote, a closing mark, the next name after an escape, or
    what `stop` refuses. It ends inside a placeholder, at its "]", so what follows one is read on its own, as the next
    header after "Token [masked]|" is."""
    return rf"((?:{stop}(?!{_NEXT_NAME}){_CLOSE}[^ \t\n\r\"'`,;<>)\]}}])+)"


_VALUE = _value()
# Where the space after a separator crosses a line break, a value never starts with another secret's name and its
# separator: under a YAML parent key named like a secret, or a key left empty, the next line's name would be taken as
# the value, and the secret after it left. On one line it may: "password: SecretGarden99 is on the fridge".
_NOT_A_NAME = (rf"(?!:?(?:{_KEY_NAME[4:].replace('(?<![A-Za-z0-9_.-])(', '(?<![A-Za-z0-9_.-])(?:', 1)}"
               rf"|{_ENV_KEY_NAME.replace('([A-Z]', '(?:[A-Z]', 1)}){_QUOTE}?[ \t]*(?:{_SEP}|\bis\b))")
_AFTER_SEP = rf"(?:{_LINE_SPACE}*{_QUOTE}?|{_LINE_SPACE}*[\r\n]{_GAP}{_QUOTE}?{_NOT_A_NAME})"
KEYED_SECRET = re.compile(_KEY_NAME + rf"({_QUOTE}?{_GAP}(?:{_SEP}|\bis\b){_AFTER_SEP})" + _VALUE)
ENV_KEY_SECRET = re.compile(_ENV_KEY_NAME + rf"({_QUOTE}?{_GAP}{_SEP}{_AFTER_SEP})" + _VALUE)
# A JSON string value in plain quotes after a secret's name, read as JSON reads it: to the next quote no backslash
# escapes, so a quote inside it is part of it. In JSON written inside JSON, whose quotes are escaped, a value ends at
# the escaped quote, as KEYED_SECRET reads it.
JSON_SECRET = re.compile(r'(?i)(?<=")(?<!\\")' + _KEY_NAME[4:] + r'("[ \t]*:[ \t]*")((?:[^"\\\r\n]|\\[^\r\n]){1,1024}?)(?=")')
# A long flag named for a secret, and its value after a space or a quote: "--api-key ...". A flag that names where the
# secret is, such as "--token-file" or "--password-stdin", takes none, and a value never starts with "-".
FLAG_SECRET = re.compile(rf"(?i)(?:(?<![A-Za-z0-9_.-])|{_AFTER_ESCAPE})(--" + _KEY_NAME[len(r"(?i)(?<![A-Za-z0-9_.-])("):]
                         + r"(?<!-stdin)(?<!-file)(?<!-env)(?<!-path)(?<!-name)(?<!-id)(?<!-cmd)(?<!-command)"
                         r"(?<!-helper)(?<!-length)(?<!-type)(?<!-url)((?:[ \t]|%(?:25)?20)+[\"']?)(?!-)" + _VALUE)
# "pass" and "pwd" alone name a password where a value plainly follows them: before "=", or ":" and a quote, as a
# JavaScript object, YAML or a connection string writes one ("pass: '...'", "Uid=sa;Pwd=...;"), and only as the field
# next to a user's name, as a login gives both (_beside_a_user). Before ":" and a word they are words, or a command: "tests pass:
# 12", "pwd: /home"; and alone, a compiler's pass ("pass=ConvertBF16ToF32").
SHORT_PASSWORD = re.compile(rf"(?i)(?:(?<![A-Za-z0-9_.-])|{_AFTER_ESCAPE})(pass|pwd)"
                            rf"({_QUOTE}?[ \t]*(?::[ \t]*{_QUOTE}|=[ \t]*{_QUOTE}?))" + _VALUE)
# A secret in an XML element, as Maven's settings.xml and NuGet's config hold one: "<password>...</password>". Not a
# bare "<token>", which a tokenizer's or a lexer's list holds ("<tokens><token>HTTPStatus200</token></tokens>").
XML_SECRET = re.compile(r"(?i)<((?:[A-Za-z0-9_.-]+:)?(?:password|passwd|passphrase|passcode|secret|client[_-]?secret|"
                        r"(?:api|access|secret|private|subscription)[_-]?key|(?:access|auth|refresh|api|session|bearer)[_-]?token|"
                        r"storepass|keypass))(>)([^<\s]{1,512}?)(?=</)")
_XML_PASSWORD = re.compile(r"(?i)passw(?:or)?d|passphrase|passcode|storepass|keypass")
# A password's value in quotes, read to the closing quote, so a comma, a semicolon or a bracket inside is part of it
# ('DB_PASSWORD="Xk9,mP2;qL7v"'); a space is not, as quotes may hold a sentence.
QUOTED_PASSWORDS = [re.compile(_KEY_NAME + rf"""([\"']?[ \t]*[:=][ \t]*{quote})([^\s{quote}\\]{{4,256}})(?={quote})""")
                    for quote in ('"', "'")]
# A password's whole line, as a .env file or YAML writes one, "NAME=value" or "name: value" with nothing after it but a
# comment: the value runs to the end of the line, so a comma, a semicolon or a bracket inside is part of it
# ("DB_PASSWORD=Xk9,mP2;qL7v"). Not where a ";", "," or "&" goes on to another name and "=", as a connection string or
# a link's query does.
LINE_PASSWORD = re.compile(
    r"(?im)(?:^|(?<=\\n))[ \t]*(?:export[ \t]+|-[ \t]+)?" + _KEY_NAME[4:] + r"([ \t]*[:=][ \t]*)"
    r"((?:(?![;,&][A-Za-z_][A-Za-z0-9_.-]*=)(?!\\[nr])[^\s\"'`]){1,512})(?=[ \t]+#[^\r\n]*$|[ \t]*\r?$|\\[nr])")
# Characters a reader does not see: NUL and the other C0 controls that are not whitespace, as UTF-16 text read as bytes
# holds them, DEL and the C1 controls, and Unicode's default ignorable code points: zero-width spaces and joiners,
# direction marks and isolates, the soft hyphen, the byte order mark, variation selectors, fillers and tag characters.
# A terminal's colour code, a whole CSI sequence ("ESC[" to its final byte, as grep --color and ripgrep write one
# around a match), is one too, raw, as its eight-bit introducer or escaped as JSON, Python, C or Bash write it
# ("\u001b[", "\x1b[", "\033[", "\E["), but for ESC before a placeholder, which "[m" would start. A secret reads through them, and one
# before a secret parts it from the word before (_once), though a colour code ends in a letter. The vertical tab, the
# form feed, the four separators from 0x1c and NEL are whitespace, as str.split reads them, and part words as a line
# break does: pdftotext puts a form feed between pages.
_CSI = r"(?:\x1b\[|\x9b|(?<!\\)\\+(?:u001[bB]|x1[bB]|033|[eE])\[|(?<!\\)\\+(?:u009[bB]|x9[bB]|233))(?!masked\])[0-?]*[ -/]*[@-~]"
_FORMAT_CHARACTERS = (r"\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e\u2060-\u206f"
                      r"\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufff8\U0001bca0-\U0001bca3\U0001d173-\U0001d17a"
                      r"\U000e0000-\U000e0fff")
# The same characters escaped, as JSON ("\u200b", "\u007f") and Python's repr ("\x7f", "\xad", "\u2060") print them; one
# past U+FFFF as JSON writes it, a surrogate pair ("\udb40\udc01" for U+E0001); and any of them escaped twice, as a
# JSON string inside JSON holds one ("\\u200b"); and JSON's short escape for the backspace, between characters a token
# holds (letters, digits, "_", "-", ".", and base64's "+", "/" and "="), where a regular expression's word boundary
# ("\\btoken\\b", a quote or a space beside it) does not.
_ESCAPED_INVISIBLE = (
    r"\\?(?:\\u(?:000[0-8]|000[eEfF]|001[0-9a-bA-B]|007[fF]|008[0-46-9a-fA-F]|009[0-9a-fA-F]|00[aA][dD]|034[fF]|061[cC]"
    r"|115[fF]|1160|17[bB][45]|180[b-fB-F]|200[b-fB-F]|202[a-eA-E]|206[0-9a-fA-F]|3164|[fF][eE]0[0-9a-fA-F]"
    r"|[fF][eE][fF][fF]|[fF][fF][aA]0|[fF][fF][fF][0-8])"
    r"|\\x(?:0[0-8]|0[eEfF]|1[0-9a-bA-B]|7[fF]|8[0-46-9a-fA-F]|9[0-9a-fA-F]|[aA][dD])"
    r"|\\U000(?:1[bB][cC][aA][0-3]|1[dD]17[3-9aA]|[eE]0[0-9a-fA-F]{3})"
    r"|\\u[dD][bB]4[0-3]\\{1,2}u[dD][c-fC-F][0-9a-fA-F]{2}|\\u[dD]82[fF]\\{1,2}u[dD][cC][aA][0-3]"
    r"|\\u[dD]834\\{1,2}u[dD][dD]7[3-9aA])"
    r"|(?<=[A-Za-z0-9_.+/=-])\\{1,2}b(?=[A-Za-z0-9_.+/=-])")
INVISIBLE = re.compile(_CSI + rf"|{_ESCAPED_INVISIBLE}|[\x00-\x08\x0e-\x1b\x7f-\x84\x86-\x9f{_FORMAT_CHARACTERS}]")
# Main's own: a lone control or format character, not a colour code, an escaped one, DEL or a C1 control
# (_NEW_INVISIBLE).
_MAIN_INVISIBLE_RUN = re.compile(rf"[\x00-\x08\x0e-\x1b{_FORMAT_CHARACTERS}]+")
_NEW_INVISIBLE = re.compile(_CSI + rf"|{_ESCAPED_INVISIBLE}|[\x7f-\x84\x86-\x9f]")
# Main 33c82f6's own INVISIBLE, as the copy of its command code judges a value without it (_todays_password_edits):
# no surrogate pair, nothing escaped twice, no colour code escaped twice and no JSON backspace between a token's
# characters, so "FOO\bBAR", a password to main, is not read as FOOBAR, a name in capitals.
_TODAYS_CSI = r"(?:\x1b\[|\x9b|\\(?:u001[bB]|x1[bB]|033|[eE])\[|\\(?:u009[bB]|x9[bB]|233))(?!masked\])[0-?]*[ -/]*[@-~]"
_TODAYS_ESCAPED_INVISIBLE = (
    r"\\u(?:000[0-8]|000[eEfF]|001[0-9a-bA-B]|007[fF]|008[0-46-9a-fA-F]|009[0-9a-fA-F]|00[aA][dD]|034[fF]|061[cC]"
    r"|115[fF]|1160|17[bB][45]|180[b-fB-F]|200[b-fB-F]|202[a-eA-E]|206[0-9a-fA-F]|3164|[fF][eE]0[0-9a-fA-F]"
    r"|[fF][eE][fF][fF]|[fF][fF][aA]0|[fF][fF][fF][0-8])"
    r"|\\x(?:0[0-8]|0[eEfF]|1[0-9a-bA-B]|7[fF]|8[0-46-9a-fA-F]|9[0-9a-fA-F]|[aA][dD])"
    r"|\\U000(?:1[bB][cC][aA][0-3]|1[dD]17[3-9aA]|[eE]0[0-9a-fA-F]{3})")
_TODAYS_INVISIBLE = re.compile(
    _TODAYS_CSI + rf"|{_TODAYS_ESCAPED_INVISIBLE}|[\x00-\x08\x0e-\x1b\x7f-\x84\x86-\x9f{_FORMAT_CHARACTERS}]")
_TODAYS_NEW_INVISIBLE = re.compile(_TODAYS_CSI + rf"|{_TODAYS_ESCAPED_INVISIBLE}|[\x7f-\x84\x86-\x9f]")

# Google's app password, four groups of four lower-case letters, after a password's name and "=" or in quotes. A
# character a reader does not see may stand anywhere among its letters, as one may in any value read through them.
# A colour code is read whole, never its introducer alone, as ESC or 0x9B by themselves, so a letter after one is
# read one way only and a run of colour codes costs time in proportion.
_UNSEEN = (rf"(?:{_CSI}|(?!\\{{1,2}}(?:u001[bB]|x1[bB]|u009[bB]|x9[bB]))(?:{_ESCAPED_INVISIBLE})"
           rf"|[\x00-\x08\x0e-\x1a\x7f-\x84\x86-\x9a\x9c-\x9f{_FORMAT_CHARACTERS}])*")
_TODAYS_UNSEEN = (rf"(?:{_TODAYS_CSI}|(?!\\(?:u001[bB]|x1[bB]|u009[bB]|x9[bB]))(?:{_TODAYS_ESCAPED_INVISIBLE})"
                  rf"|[\x00-\x08\x0e-\x1a\x7f-\x84\x86-\x9a\x9c-\x9f{_FORMAT_CHARACTERS}])*")  # main 032391d's own


def _app_passwords(unseen):
    """The app password's two readings, with `unseen` the characters read through among its letters: after a password's
    name and "=" or in quotes, and on a line of its own, as YAML writes one, after an unquoted colon too: the four
    groups and nothing after them but a comment. Neither reading takes a sentence of common words
    (_app_password_span): four-letter words common in English and in technical text, defined at the end of this file;
    four groups holding three or more of them are a sentence ("password: read from smtp logs"), which a random app
    password's groups are about once in a million times."""
    group = unseen.join(["[a-z]"] * 4)
    groups = rf"({unseen}{group}(?:{unseen} {unseen}{group}){{3}}{unseen})"
    return (re.compile(_KEY_NAME + r"""((?:[ \t]*=|[\"']?[ \t]*[:=][ \t]*[\"'])[ \t]*)""" + groups + r"(?![A-Za-z0-9])"),
            re.compile(r"(?m)(?:^|(?<=\\n))[ \t]*(?:export[ \t]+|-[ \t]+)?(?i:" + _KEY_NAME[4:] + r")([ \t]*:[ \t]*)"
                       + groups + r"(?=[ \t]+#[^\r\n]*$|[ \t]*\r?$|\\[nr])"))


APP_PASSWORD, APP_PASSWORD_LINE = _app_passwords(_UNSEEN)
_TODAYS_APP_PASSWORD, _TODAYS_APP_PASSWORD_LINE = _app_passwords(_TODAYS_UNSEEN)  # main 032391d's own
# An HTTP Authorization header's credentials: its first word, or after a scheme word ("Token ...", "Bot ...",
# "ApiKey ..."), the next. _authorization_span() reads which is the secret.
# Its first word, the scheme, ends at a space a link encodes too, as in "Authorization%3A%20Token%20<credential>".
_SCHEME_WORD = _value(r"(?!%(?:25)?20)")
AUTH_SECRET = re.compile(rf"(?i)((?:(?<![A-Za-z0-9_.-])|{_AFTER_ESCAPE})(?:proxy-)?authorization)"
                         rf"({_QUOTE}?{_LINE_SPACE}*(?::|=|%(?:25)?3[ADad]){_LINE_SPACE}*{_QUOTE}?)"
                         + _SCHEME_WORD + rf"(?:({_LINE_SPACE}+)" + _VALUE + ")?")
# Commands that take a password after a short flag, which counts only on that command's line: from its first word to a
# pipe, ";" or a line break, plain or escaped. Each row: the command, the flag and what parts it from the password, and
# whether any value counts; otherwise a value of letters only after a space is a word, as after "is" ("docker login -p
# is deprecated"). curl's -u takes "user:password", and mysql's -p takes its password glued, since "-p shop" names a
# database.
_FLAG_QUOTE = r"(?:\\?['\"])?"  # a quote, plain or escaped as JSON holds one, before a flag's value
_WINDOWS_FILE = r"(?:\.exe|\.cmd)?"  # a program's file on Windows, as "keytool.exe", "aws.exe" and "az.cmd" are


def _quoted(flag):
    """`flag` bare or in quotes that open and close around it, plain or escaped ('"-p"', "\\'-p\\'"), not a quote that
    opens an argument the flag only starts ('--comment "-p release-2026"')."""
    return rf"(?:{flag}|\"{flag}\"|'{flag}'|\\\"{flag}\\\"|\\'{flag}\\')"


# aws configure set's setting about a secret, its key word looked for ahead (Python before 3.12 takes no backslash in
# an f-string's braces, so it stands apart).
_AWS_SETTING = r"(?:[A-Za-z0-9_-]+\.)?(?=[A-Za-z0-9_]*?(?:secret|token|password))[A-Za-z0-9_]+"
_AWS_SETTING_NAME = re.compile(_AWS_SETTING + r"\Z")
_AWS_PROGRAM = re.compile(r"aws(?:\.exe|\.cmd)?")  # aws's own name, read from a path's last part
_SHELL = re.compile(r"(?:^|/)(?:ba|z|a|k)?sh\Z")
_SHELL_SCRIPT_FLAG = re.compile(r"-[a-z]*c[a-z]*\Z")


def _aws_sets(items):
    """For each index j, whether "aws", "configure" and "set" come in that order before items[j], with any options
    between them ("aws --profile team configure set --region us-east-1 ..."), in one pass: then a secret-setting name
    at j is aws configure set's, and items[j + 1] the secret, though the name starts with no "-". However many options
    come between, the list is the one command's."""
    found, program, configure, done = [], None, None, None  # done: where the last whole "aws configure set" begins
    for j, item in enumerate(items):
        found.append(done is not None)
        if not isinstance(item, str):
            continue
        word = item.lower()
        if _AWS_PROGRAM.fullmatch(re.split(r"[\\/]", word)[-1]):
            program, configure = j, None
        elif program is not None and configure is None and word == "configure":
            configure = j
        elif configure is not None and word == "set":
            done, program, configure = program, None, None
    return found


COMMAND_FLAGS = (
    (r"curl", r"\s(?:-u|--user)\s*['\"]?[^ \t\n\r:'\"]+:", True),
    (r"mysql|mysqldump|mysqladmin|mariadb|mariadb-dump", r"\s-p(?=[^ \t\n\r'\"])", True),
    (r"sshpass", r"\s-p[ \t]*['\"]?", False),
    (r"(?:docker|podman|nerdctl|buildah|skopeo|oras)\s+login|helm\s+registry\s+login", r"\s-p[ \t]+['\"]?", False),
    (r"redis-cli", r"\s-a[ \t]+['\"]?", False),
    # From here on, rows main's own lack (_MAIN_COMMANDS): a value that starts with "-", read without the characters a
    # reader does not see, is the command's next option ("mongosh -p --quiet" asks for the password), as these
    # commands' parsers read one. keytool and jarsigner take whatever follows as the password ("dash"), so there only
    # one of their own options (_JAVA_OPTIONS: "-keystore", "-v") is the next option.
    # A flag in quotes is the flag ('mongosh "-p" ...'), as the shell takes the quotes away.
    (rf"az{_WINDOWS_FILE}{_FLAG_QUOTE}\s+login|mongosh|mongo|mongodump|mongorestore|mongoexport|mongoimport|twine",
     rf"\s{_quoted('-p')}[ \t]+{_FLAG_QUOTE}", False),
    (r"keytool|jarsigner", rf"\s{_quoted('-(?:src|dest|new)?(?:storepass|keypass)')}[ \t]+{_FLAG_QUOTE}", "dash"),
    # aws configure set's secret settings, and a .netrc line's password, its fields in any order but with a login
    # among them, so "the machine room password sheet" is no .netrc line, "machine" and "default" only where a line or a
    # quoted string starts, and every keyword in small letters, as a .netrc file writes them ("Default login page password
    # field" is prose); a value counts only where a field, a comment or the line's end follows it. A setting's name is read once, its key word
    # looked for ahead: read around the key word, a run of key words took time in the square of its length.
    (rf"aws{_WINDOWS_FILE}{_FLAG_QUOTE}\s+configure\s+set", rf"\s{_quoted(_AWS_SETTING)}[ \t]+{_FLAG_QUOTE}", False),
    (r"(?-i:(?:^|(?<=\n)|(?<=\\n)|(?<=[\"']))[ \t]*(?:machine[ \t]+[^\s]+|default)"
     r"(?=(?:[ \t]+(?:password|account)[ \t]+[^\s]+)*[ \t]+login[ \t]+[^\s]))",
     r"\spassword[ \t]+(?:\\?['\"])?", "field"),
)
_MAIN_COMMANDS = 5  # main's own rows of COMMAND_FLAGS; the rest are this PR's
# A command's line runs to a pipe, ";" or a line break, plain or escaped (_LINE). For the rows main's own lack, it is
# read as the shell reads it (_command_line), so a quoted password may hold a pipe ("az login -p 'Tr0ub4dor|Zk9q'").
_LINE = r"(?:[^\n|;\\]|\\(?![nr]))*"
# Where the name of one of this PR's commands ends: past a ".exe", and not before a path's next part, as
# "/opt/keytool/bin/keytool" names a folder first ("/", a backslash, or one escaped as "\\\\"), though a backslash
# that escapes a line break, a tab or a quote ends the name.
_NAME_END = r"(?![A-Za-z0-9_.-])(?!/|\\(?![nrt\"']))"  # and main's, after its own ".exe"
_PROGRAM_END = _WINDOWS_FILE + _NAME_END


def _command_name(command, end):
    return re.compile(rf"(?i)(?:(?<![A-Za-z0-9_.-])|{_AFTER_ESCAPE})(?:{command})" + end)


_COMMANDS = [(_command_name(command, rf"(?![A-Za-z0-9_.-]){_LINE}" if n < _MAIN_COMMANDS else _PROGRAM_END),
              re.compile(rf"({flag})((?:{_CLOSE}[^ \t\n\r'\"])+)"), always)
             for n, (command, flag, always) in enumerate(COMMAND_FLAGS)]
# Each of this PR's rows whose command this PR spells more ways than main ("az.exe login", "az.cmd login",
# "keytool.cmd"), as main spells it: "az" and "aws" alone, and ".exe" alone after a program's name. A row reads the
# text with main's spelling too, where the text names its command a way main does not (_command_password_edits).
_MAIN_SPELLING = {line: narrower for n, ((command, _, _), (line, _, _)) in enumerate(zip(COMMAND_FLAGS, _COMMANDS))
                  if n >= _MAIN_COMMANDS
                  for narrower in [_command_name(command.replace(_WINDOWS_FILE, ""), r"(?:\.exe)?" + _NAME_END)]
                  if narrower.pattern != line.pattern}
# Each row of COMMAND_FLAGS as main 33c82f6 spells its command, read as main reads it there (_todays_password_edits).
_TODAYS_COMMANDS = [(_MAIN_SPELLING.get(line, line), flag, always) for line, flag, always in _COMMANDS]
_ANY_COMMAND = re.compile(r"(?i)curl|mysql|mariadb|sshpass|login|redis-cli|mongo|twine|keytool|jarsigner|aws")
# aws configure set's row, and aws's options that take a value (its global options): a setting's name right after one
# is that option's value, as "token" is in "aws configure set --profile token ...", not the setting.
_AWS_FLAG = next(row[1] for row, (command, _, _) in zip(_COMMANDS, COMMAND_FLAGS) if command.startswith("aws"))
# A line's continuation as a word of its own: a backslash before the line break, raw or escaped once or more ("\\\\n").
# Each run of backslashes is read one way only, so a long run before another character costs time in proportion.
_CONTINUATION_WORD = re.compile(r"\\+(?:[rn]\\+)*[rn]?")
# A word of a command line, for reading the option before aws configure set's setting.
_LINE_WORD = re.compile(r"\S+")
_AWS_VALUE_OPTIONS = frozenset(("--profile", "--region", "--output", "--endpoint-url", "--query", "--ca-bundle",
                                "--color", "--cli-read-timeout", "--cli-connect-timeout", "--cli-binary-format"))
# In a list of arguments, an item names one of COMMAND_FLAGS' commands where the name stands whole, as a program's
# does, past a path and before a ".exe": "mongo-ca", "KEYTOOL_HOME=/opt/java" and "aws_session_token" name none. A
# command named by two words ("docker login", "az login") is named by its "login".
_COMMAND_NAME = re.compile(rf"(?i)(?:(?<![A-Za-z0-9_.-])|{_AFTER_ESCAPE})(curl|mysql(?:dump|admin)?|mariadb(?:-dump)?|"
                           rf"sshpass|login|redis-cli|mongo(?:sh|dump|restore|export|import)?|twine|keytool|jarsigner|"
                           rf"aws){_PROGRAM_END}")
# A field name that by itself names a secret, as a dict's key does, "pass" and "pwd" too.
_SECRET_FIELD = re.compile(rf"(?:{_KEY_NAME[4:]}|{_ENV_KEY_NAME}|pass|pwd)$", re.I)
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
# A value, as _VALUE cuts it, is a pointer only in a pointer's whole shape: a variable in one case ("$DB_PASSWORD",
# "$api_key"), a substitution or a template ("${...}", "$(...)", "{{...}}", "%(name)s"), a Windows variable, a path of
# two parts or more, a call, stars alone, a placeholder in brackets, a dotted name or a name in capitals.
REFERENCE = re.compile(r"^(?:\$(?:[{(]|[A-Z_][A-Z0-9_]*$|[a-z_][a-z0-9_]*$|env:)|%(?:[A-Za-z_][A-Za-z0-9_]*%$|\()|[{\[<]|"
                       r"\*{2,}|~/|\.\.?/|/[^/\s]+/|/[A-Za-z_.-]+$|os\.|process\.|env\.|getenv|secrets\.|vars\.|self\.|this\.|"
                       r"[a-z]+://|(?:env|file|cmd):|[a-z_][\w.]*\()|"
                       r"^[A-Za-z_]+(?:\.[A-Za-z_]+)+$|^[A-Z][A-Z_]{2,}$|^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")

# A private key in a file written as base64, as a Kubernetes secret holds a TLS key: wherever the file's header or
# footer says "PRIVATE KEY", or a PGP block's "SECRET KEY", the run holds that word's base64 at one of the three byte
# offsets a word can start at.
def _base64_of(word):
    """The base64 characters `word`'s bytes alone decide, at each of the three offsets it can start at."""
    return [base64.b64encode(b"\0" * k + word + b"\0\0\0").decode()[-(-8 * k // 6):8 * (k + len(word)) // 6]
            for k in range(3)]


_KEY_WORDS = (b"PRIVATE KEY", b"SECRET KEY")
_ENCODED_KEY = re.compile("|".join(re.escape(code) for word in _KEY_WORDS for code in _base64_of(word)))
# A line break: any that Python's splitlines reads (a lone carriage return too, as an old Mac file ends its lines),
# escaped once or more as JSON writes it, and JSON in JSON: a line feed or carriage return as \n, \r or \u000a, a form
# feed as \f, and the others as \u000b, \u0085 or  , so a stream's lines read as one block at every depth.
# JSON's other escapes are read by _json_unescaped's views.
_BREAK_CHARACTERS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85  "
# Horizontal space: every character Python reads as space but a line break, as str.split parts words at each: a space,
# a tab, the no-break space and Unicode's other spaces, and the unit separator. And the quotes a string is written in,
# a backtick too, as a template literal or a code span is. Each also escaped as JSON writes it, behind any number of
# backslashes: "\\t", or "\\uXXXX" in either case ("\\u0020", "\\u00A0", "\\u0022").
_HORIZONTAL = frozenset("\t\x1f \xa0   　" + "".join(map(chr, range(0x2000, 0x200b))))
_QUOTES = "\"'`"


_BACKSLASH_PLACE = ("\\", 1, None)


def _literal(characters):
    """The places of a token that is `characters`, each written once."""
    return tuple((c, 1, 1) for c in characters)


def _escaped(character):
    """The places of `character`'s escape as JSON writes it, behind any number of backslashes: a "u" and four hex
    digits, in either case."""
    return (_BACKSLASH_PLACE, ("u", 1, 1)) + tuple((d + d.upper() if d.isalpha() else d, 1, 1)
                                               for d in f"{ord(character):04x}")


# Every token the readers of a line's lead and of a line break accept, each as its places: the characters one place may
# hold, and how many of it in a row, at least and at most (None: any number). Each reader below, forward or back from a
# token's end, in a str or in the bytes a key's base64 decodes to, is built from these tables, and so is each window's
# test of a token its edge cuts (_cuts): a token added here is read, and cut, alike, and the tests cut each one.
# Horizontal space: each character, written; a tab as "\\t"; each escaped. A quote: written behind any number of
# backslashes, escaped, or as a web page's entity, as main reads one (_QUOTE), and a backtick; not percent-encoded, as a
# link writes one: there base64's own "+", "/" and "=" are encoded too, and the digits of "%22" would read as the run's.
_SPACE_TOKENS = (tuple(_literal(c) for c in sorted(_HORIZONTAL)) + ((_BACKSLASH_PLACE, ("t", 1, 1)),)
                 + tuple(_escaped(c) for c in sorted(_HORIZONTAL)))
_QUOTE_TOKENS = (tuple((("\\", 0, None), (q, 1, 1)) for q in _QUOTES) + tuple(_escaped(q) for q in _QUOTES)
                 + tuple(_literal(f"&{name};") for name in ("quot", "apos", "grave"))
                 + tuple(_literal("&#") + (("0", 0, None),) + _literal(f"{code};") for code in ("34", "39", "96"))
                 + tuple(_literal("&#") + (("xX", 1, 1), ("0", 0, None)) + _literal(f"{code};")
                         for code in ("22", "27", "60")))
_LEAD_TOKENS = _SPACE_TOKENS + _QUOTE_TOKENS
# A line break: each written, "\\n", "\\r" and "\\f", and each escaped; a carriage return and a line feed as one.
_BREAK_TOKENS = ((_literal("\r\n"), (_BACKSLASH_PLACE, ("r", 1, 1), _BACKSLASH_PLACE, ("n", 1, 1)),
                  _escaped("\r") + _escaped("\n"))
                 + tuple(_literal(c) for c in _BREAK_CHARACTERS) + tuple((_BACKSLASH_PLACE, (c, 1, 1)) for c in "nrf")
                 + tuple(_escaped(c) for c in _BREAK_CHARACTERS))


def _place(place):
    """A regex of one place of a token."""
    chars, least, most = place
    one = re.escape(chars) if len(chars) == 1 else "[" + "".join(map(re.escape, chars)) + "]"
    return one + {(1, 1): "", (0, 1): "?", (0, None): "*", (1, None): "+"}[least, most]


def _pattern(tokens, searched=False):
    """A regex of any one of `tokens`, as one tree of their places: tokens that start alike are tried once, a longer one
    before one it starts with, and places alike but for their characters, with the same tree after them, are one
    class. `searched`: one that starts with backslashes matches only from the first of them, so a long run of them is
    tried once."""
    tree = {}
    for token in tokens:
        node = tree
        for place in token:
            node = node.setdefault(place, {})
        node[None] = {}

    def alternatives(node, top=False):
        groups = []  # (counts, the tree after, characters)
        for place, after in node.items():
            group = next((g for g in groups if place is not None and g[0] == place[1:] and g[1] == after), None)
            if group:
                group[2].append(place[0])
            elif place is not None:
                groups.append((place[1:], after, [place[0]]))
        out = []
        for (least, most), after, chars in groups:
            rest = "|".join(alternatives(after))
            rest = f"(?:{rest})?" if rest and None in after else f"(?:{rest})" if rest else ""
            guard = "(?<!\\\\)" if searched and top and chars == ["\\"] else ""
            out.append(guard + _place(("".join(chars), least, most)) + rest)
        return out

    return "(?:" + "|".join(alternatives(tree, True)) + ")"


def _reversed(tokens):
    """`tokens` read back from their ends."""
    return tuple(token[::-1] for token in tokens)


def _cuts(tokens, back=False):
    """What a window's edge leaves of one of `tokens` where it cuts it, as tokens: its places before one, whole, then
    fewer of that one, none too; with `back`, the edge before it: fewer of one, then the places after it, whole."""
    out = []
    for token in tokens:
        for i, (chars, _, most) in enumerate(token):
            fewer = ((chars, 0, most),)
            out.append(fewer + token[i + 1:] if back else token[:i] + fewer)
    return tuple(out)


_SPACING = _pattern(_SPACE_TOKENS)
_QUOTE_MARK = _pattern(_QUOTE_TOKENS)
_BREAK = _pattern(_BREAK_TOKENS)
_WRAP = rf"(?:\r|{_SPACING})*{_BREAK}{_SPACING}*"
# A run of base64, with each line after it that it wraps onto: a line that is base64 from its indentation to its end,
# however short, which a line break, a quote, a tag or the text's end ends. A run shorter than 16 starts one only when
# lines follow it. A run can start just after a backslash, so the letter of an escape such as \n can lead it. Which of
# the lines hold a key is _key_range's to say.
_WRAPPED_LINE = rf"{_WRAP}(?:[A-Za-z0-9+/]+={{0,2}}|={{1,2}})(?=(?:\r|{_SPACING})*(?:{_BREAK}|{_QUOTE_MARK}|<|\Z))"
_WRAPPED_BASE64 = re.compile(rf"(?<![A-Za-z0-9+/])(?:[A-Za-z0-9+/]{{16,}}={{0,2}}(?:{_WRAPPED_LINE})*"
                             rf"|[A-Za-z0-9+/]+={{0,2}}(?:{_WRAPPED_LINE})+)")
# The same in the bytes a key's base64 decodes to: each character as UTF-8 writes it, each byte a Latin-1 character of
# the tables, and a no-break space alone as Latin-1 writes it, too (a lone \x85 is cp1252's ellipsis, no line break).
def _in_bytes(tokens):
    """`tokens` with each place past ASCII as the places of its UTF-8 bytes."""
    return tuple(tuple(place for chars, least, most in token
                       for place in (((chars, least, most),) if chars.isascii()
                                     else _literal(chars.encode().decode("latin-1"))))
                 for token in tokens)


def _bytes(pattern):
    return re.compile(pattern.encode("latin-1"))


_LEAD_TOKENS_BYTES = _in_bytes(_LEAD_TOKENS) + (_literal("\xa0"),)
_BREAK_TOKENS_BYTES = _in_bytes(_BREAK_TOKENS)
_SPACE_OR_QUOTE_BYTES = _bytes(_pattern(_LEAD_TOKENS_BYTES))
_SPACE_OR_QUOTE_RUN = _bytes(_pattern(_LEAD_TOKENS_BYTES) + "*")
_BREAK_BYTES = _bytes(_pattern(_BREAK_TOKENS_BYTES))
_BREAK_BYTES_RUN = _bytes(_pattern(_BREAK_TOKENS_BYTES) + "*")
# Searched for: an escape only from the first backslash of its run, so a long run of them is tried once.
_BREAK_BYTES_SEARCH = _bytes(_pattern(_BREAK_TOKENS_BYTES, searched=True))
# A line's lead read back from where it ends, in a text reversed; and what a window's edge leaves of a token it cuts,
# before the run it reads (_lead_start), and before and after a header or footer (_frame).
_LEAD_BACK = {str: re.compile(_pattern(_reversed(_LEAD_TOKENS)) + "*"),
              bytes: _bytes(_pattern(_reversed(_LEAD_TOKENS_BYTES)) + "*")}
_CUT_BACK = {str: re.compile(_pattern(_cuts(_LEAD_TOKENS, back=True))),
             bytes: _bytes(_pattern(_cuts(_LEAD_TOKENS_BYTES, back=True)))}
_CUT_FRAME_BACK = _bytes(_pattern(_cuts(_LEAD_TOKENS_BYTES + _BREAK_TOKENS_BYTES, back=True)))
_CUT_FRAME_AHEAD = _bytes(_pattern(_cuts(_LEAD_TOKENS_BYTES + _BREAK_TOKENS_BYTES)))
# The longest a line break is with each run of backslashes in it one long: a longer one ends with one that long or less.
_BREAK_REACH = max(sum(least for _, least, _ in token) for token in _BREAK_TOKENS)


def _lead_start(text, at):
    """Where the run of horizontal space and quotes, written or escaped (_LEAD_TOKENS), that ends at `at` in `text`
    starts: in a str, or in the bytes a key's base64 decodes to. Read back in windows twice as wide each time while what
    a window holds before the run is all the window's edge leaves of a token it cuts (_CUT_BACK), so a run costs time in
    proportion to its length."""
    kind = str if isinstance(text, str) else bytes
    back, cut, width = _LEAD_BACK[kind], _CUT_BACK[kind], 64
    while True:
        lo = max(at - width, 0)
        start = at - back.match(text[lo:at][::-1]).end()
        if not lo or not cut.fullmatch(text, lo, start):
            return start
        width *= 2
_UNWRAP = re.compile(_WRAP)
_ESCAPE_LETTER = re.compile(r"(?<=\\)(?:[bfnrt]|u[0-9A-Fa-f]{4})")
# Docker's config holds a registry login in an "auth" field, base64 of "user:password".
_AUTH_FIELD = re.compile(rf"(?i)((?:(?<![A-Za-z0-9_.-])|{_AFTER_ESCAPE})auth{_QUOTE}?{_GAP}[:=]{_GAP}{_QUOTE}?)"
                         r"([A-Za-z0-9+/]{4,}={0,2})")



# Provider tokens kb takes from gitleaks (kb/gitleaks.py), each a token's own shape: compiled ASCII-only, as Go reads
# \w and \b; the group holding the secret; the least entropy it has; keywords one of which a text holds before the rule
# is read; and what marks a secret as none, the rule's and gitleaks' own for every rule.
# gitleaks ends many tokens only at a space, a quote, a backtick, ";" or a line break. kb ends them too at a mark no token
# goes on through, as CSV's comma, a closing bracket, a sentence's full stop, Markdown's "**", "_" and "~~" and any
# backslash that
# starts an escape do ("dop_v1_...,active", "**dop_v1_...**", '"dop_v1_...\\"' in JSON in JSON), and lets
# Azure's client secret, whose letters take a full stop, stand inside brackets, braces and Markdown's "**" too.
# Where a token kb reads starts: after anything that is no letter or digit, or right after an escape or an encoded byte.
_TOKEN_START = rf"(?:(?<![A-Za-z0-9])|{_AFTER_ESCAPE})"
_TOKEN_EDGES = (
    (r"""(?=[\x60'"\s;]|\\[nr]|$)""", r"""(?![A-Za-z0-9])"""),
    (r"""(?:^|[\\'"\x60\s>=:(,)])""", rf"""(?:^|[^A-Za-z0-9]|{_AFTER_ESCAPE})"""),
    (r"""(?=$|[\\'"\x60\s<),])""", r"""(?=$|[^A-Za-z0-9])"""),
    (r"""(?=[\x60'"\s;]|\\[nr]|$|\b)""", r"""(?![A-Za-z0-9])"""),
    (r"""(?=[^\w-]|\Z)""", r"""(?![A-Za-z0-9-])"""),
)


def _kb_edges(source):
    """A gitleaks rule's source with the token's edges kb reads (_TOKEN_EDGES): a token that starts at a word boundary
    or after no letter or digit starts after any mark ("_dapi..._") and right after an escape or a percent-encoded
    byte ("%22dapi...%22"), and one that ends at a word boundary ends before any mark."""
    for theirs, ours in _TOKEN_EDGES:
        source = source.replace(theirs, ours)
    source = re.sub(r"^((?:\(\?i\))?)(?:\\b|\(\?<!\[A-Za-z0-9\]\))", lambda m: m.group(1) + _TOKEN_START, source)
    return re.sub(r"\\b(\)*)$", r"(?![A-Za-z0-9])\1", source)  # and end before "_" too ("_AKIA..._")


# A keyword every token of a rule holds, for a rule whose own keyword is only the provider's name: a token alone, as cat
# or printenv prints one, holds no name, so Airtable's is read where its own mark is too. Facebook's is not: its shape,
# digits, "|" and a hex run, is also a pipe-separated record's, so it is read only by "facebook", as gitleaks reads it.
_TOKEN_KEYWORDS = {"airtable-personnal-access-token": ("pat",)}
_GITLEAKS = [(re.compile(_kb_edges(source), re.ASCII), group, entropy, keywords + _TOKEN_KEYWORDS.get(name, ()),
              [re.compile(a) for a in allowed], stopwords)
             for name, source, group, entropy, keywords, allowed, stopwords in gitleaks.RULES]
_GITLEAKS_ALLOW = [re.compile(a) for a in gitleaks.ALLOW_REGEXES]
# The same rules with no word boundary before them, for a token right after a letter where an invisible character stood.
def _after_a_break(pattern):
    """A rule's pattern with nothing asked of what stands before the token: its start edge taken off."""
    head = "(?i)" if pattern.startswith("(?i)") else ""
    body = pattern[len(head):]
    return head + (body[len(_TOKEN_START):] if body.startswith(_TOKEN_START) else body)


_GITLEAKS_AFTER_A_BREAK = [(re.compile(_after_a_break(rule[0].pattern), re.ASCII), *rule[1:]) for rule in _GITLEAKS]


def _entropy(text):
    """Shannon entropy in bits per character, as gitleaks reads a secret's."""
    counts = {}
    for c in text:
        counts[c] = counts.get(c, 0) + 1
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _gitleaks_secret(m, group, entropy, allowed, stopwords):
    """Where a gitleaks rule's match holds its secret, as gitleaks reads it, or None: its group, or for group 1 the
    first group that holds anything; with more entropy than the rule asks; and nothing marks it as none."""
    if group == 1:
        group = next((g for g in range(1, m.re.groups + 1) if m.group(g)), 0)
    start, end = m.span(group)
    secret = m.string[start:end]
    if start < 0 or not secret or entropy and _entropy(secret) <= entropy:
        return None
    lower = secret.lower()
    if any(a.search(secret) for a in allowed) or any(a.search(secret) for a in _GITLEAKS_ALLOW) \
            or any(w in lower for w in stopwords) or any(w in lower for w in gitleaks.STOPWORDS):
        return None
    return start, end


def _gitleaks_spans(text, rules=None):
    """Where gitleaks' rules find a secret in `text`, ordered and merged; each rule read only where a keyword of it
    is in the text."""
    lower, spans = text.lower(), []
    for pattern, group, entropy, keywords, allowed, stopwords in (_GITLEAKS if rules is None else rules):
        if keywords and not any(k in lower for k in keywords):
            continue
        for m in pattern.finditer(text):
            span = _gitleaks_secret(m, group, entropy, allowed, stopwords)
            if span:
                spans.append(span)
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _gitleaks_edits(text):
    """gitleaks' rules' secrets in `text` as edits that mask them."""
    return [(start, end, "[masked]") for start, end in _gitleaks_spans(text)]


# Which mask() a stored row was masked with. A row of another version, or of none, is masked again when read and by
# the next ingest. The patterns change it by themselves; bump REVISION when the code that applies them changes what
# it masks.
REVISION = 39
VERSION = f"r{REVISION}-" + hashlib.sha256(repr((REVISION, [p.pattern for p in SECRET_PATTERNS + PREFIXED_SECRETS], KEYED_SECRET.pattern,
                                ENV_KEY_SECRET.pattern, JSON_SECRET.pattern, FLAG_SECRET.pattern, AUTH_SECRET.pattern,
                                COMMAND_FLAGS, PASSWORD_NAME.pattern, sorted(PLACEHOLDERS), REFERENCE.pattern,
                                _AUTH_FIELD.pattern, INVISIBLE.pattern, _ENCODED_KEY.pattern,
                                _WRAPPED_BASE64.pattern, gitleaks.VERSION,
                                [rule[1] for rule in gitleaks.RULES])).encode()).hexdigest()[:16]


def _looks_secret(name, sep, value):
    """Whether `value`, after a secret's name `name` and the separator `sep`, is a secret and not a word, a placeholder
    or a pointer to one."""
    if value.lower() in PLACEHOLDERS or REFERENCE.search(value):
        return False
    if PASSWORD_NAME.search(name):
        word = value.rstrip(".!?")
        return len(value) >= 4 and (not _prose(sep) or bool(re.search(r"[^A-Za-z]", word)))
    mixed = re.search(r"\d", value) and re.search(r"[A-Za-z]", value)
    if _date_or_time(value):
        return False  # a key's expiry or a session's start, as "session_key: 2026-11-01T10:00:00Z" names
    return (len(value) >= 16 and not value.isdigit()) or bool(len(value) >= 8 and mixed)


# An ISO 8601 date, with a time and an offset after it or none, each field in its range but the day, which
# _date_or_time holds to its month, in ASCII digits. Any other run of digits and ":.+TZ-" may be a key:
# "1234-5678-9012-3456".
_DATE_OR_TIME = re.compile(r"(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])"
                           r"(?:T(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,9})?)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?)?",
                           re.ASCII)
_MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _date_or_time(value):
    """Whether `value` is a real date, or a date and a time, which no key or token is."""
    m = _DATE_OR_TIME.fullmatch(value)
    if not m:
        return False
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return day <= _MONTH_DAYS[month - 1] + (month == 2 and calendar.isleap(year))


# A date and a time as a log or a clock writes one, in ASCII digits: ISO 8601's extended form, its basic form
# ("20261101T100000Z", AWS's X-Amz-Date) or the two mixed, as file names take one ("2026-11-01T100000Z"); by month and
# day or by the day of the year ("2026-305T10:00:00Z"), in a year of four digits, or of six in the extended form, as
# JavaScript writes one past 9999 with its sign, which a reader may leave out of the value (in the basic form, six
# digits and more read as no year: "994595031T222415Z"); a fraction of the last field given; an offset of hours alone,
# without its colon (strftime's "+0000") or with it. Or a time of day alone, in either form, after a T or with none
# ("10:00:00.123456Z").
_TIME = (r"(?P<hour>\d\d)(?:(?P<tsep>:?)(?P<minute>\d\d)(?:(?P=tsep)(?P<second>\d\d))?)?(?:\.(?P<fraction>\d{1,9}))?"
         r"(?:Z|[+-](?:[01]\d|2[0-3])(?::?[0-5]\d)?)?")
_DATE_AND_TIME = re.compile(r"(?P<year>[+-]?\d{6}(?=-)|\d{4})(?P<dsep>-?)"
                            r"(?:(?P<month>\d\d)(?P=dsep)(?P<day>\d\d)|(?P<ordinal>\d{3}))T" + _TIME, re.ASCII)
_TIME_OF_DAY = re.compile(r"(?P<t>T?)" + _TIME, re.ASCII)
# Only these characters, and no longer than a range of two timestamps at their longest
_TIMESTAMP_CHARACTERS = re.compile(r"[\d:.+TZ-]{1,78}")


def _one_timestamp(value, in_a_range=False):
    """Whether `value` is a real date and time, or a time of day, as _DATE_AND_TIME and _TIME_OF_DAY read one: each
    field in its range, a leap second (":60") and the day's end ("24:00:00", naught past it) included. In a range, a
    time of day alone has a T or its colons, so a number after a timestamp is no time: "20261101T100000Z-1234"."""
    m = _DATE_AND_TIME.fullmatch(value)
    if m:
        leap = calendar.isleap(int(m["year"]))
        if m["ordinal"]:
            if not 1 <= int(m["ordinal"]) <= 365 + leap:
                return False
        elif not (1 <= int(m["month"]) <= 12
                  and 1 <= int(m["day"]) <= _MONTH_DAYS[int(m["month"]) - 1] + (m["month"] == "02" and leap)):
            return False
    else:
        m = _TIME_OF_DAY.fullmatch(value)
        if not m or in_a_range and not (m["t"] or m["tsep"]):
            return False
    hour, minute, second = int(m["hour"]), int(m["minute"] or 0), int(m["second"] or 0)
    if hour == 24:
        return minute == second == 0 and not (m["fraction"] or "").strip("0")
    return hour <= 23 and minute <= 59 and second <= 60


def _timestamp(value):
    """Whether `value` is a real date and time or a time of day (_one_timestamp), or a range of two joined by "-" or
    "--". For a value read by its shape alone, which a key's name does not mark as a secret."""
    if not _TIMESTAMP_CHARACTERS.fullmatch(value):
        return False
    if _one_timestamp(value):
        return True
    for sep in ("--", "-"):
        at = value.find(sep, 1)
        while at != -1:
            if _one_timestamp(value[:at], True) and _one_timestamp(value[at + len(sep):], True):
                return True
            at = value.find(sep, at + 1)
    return False


_SEP_CHARACTER = re.compile(r"[:=\uff1a\uff1d]|%(?:25)?3[ADad]|u003[AaDd]")


def _prose(sep):
    """Whether a separator reads as prose does, "is" or a space with no "=" or ":" in it, after which a value of
    letters only is a word and not a password."""
    return not _SEP_CHARACTER.search(sep) and ("is" in sep.lower() or sep[-1:].isspace())


def _wrapped_lines(text, start, end):
    """The (start, end) of each line of the base64 block text[start:end], without line breaks and indentation."""
    lines, at = [], start
    for brk in _UNWRAP.finditer(text, start, end):
        lines.append((at, brk.start()))
        at = brk.end()
    return lines + [(at, end)]


# For each encoding of a key's word, the byte of its base64 group of 4 characters the word starts at, and the character
# its encoding starts at, which fix how the groups of the base64 around it fall.
_KEY_WORD_AT = {code: (k, -(-8 * k // 6)) for word in _KEY_WORDS for k, code in enumerate(_base64_of(word))}
# A key's header or footer, each label main reads written plain: a PEM key's, an OpenPGP key's, and SSH2's, its words
# before "PRIVATE KEY" up to 200 bytes, far past any a key tool writes ("ENCRYPTED", "OPENSSH", "SSH2 ENCRYPTED"):
# each key word reads its label back to the dashes, so a run of label words read whole by each took time in its square.
_LABEL_CHARACTERS = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
_LABEL_WORDS = b"[" + re.escape(_LABEL_CHARACTERS) + b"]{0,200}"
_KEY_FRAME = re.compile(rb"-----(BEGIN|END) (?:" + _LABEL_WORDS + rb"PRIVATE KEY|PGP (?:PRIVATE|SECRET) KEY BLOCK)-----"
                        rb"|---- (BEGIN|END) SSH2 " + _LABEL_WORDS + rb"PRIVATE KEY ----")
# A header's or footer's line up to its key word: its dashes, words and spaces.
_LABEL = b"-" + _LABEL_CHARACTERS
# A control character, which no text holds: any but horizontal space and a line break (_HORIZONTAL, _BREAK_CHARACTERS).
_TEXT_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1b\x7f-\x84\x86-\x9f]")
_CONTINUATION_BYTES = bytes(range(0x80, 0xc0))


def _reads_as_text(data):
    """Whether bytes read as words do: ASCII, or UTF-8 as any language writes it, cut anywhere inside a character at
    either end, with no control character but a tab or a line break."""
    start = len(data) - len(data.lstrip(_CONTINUATION_BYTES))
    if start > 3:
        return False
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(data[start:], final=False)
    except UnicodeDecodeError:
        return False
    return not _TEXT_CONTROL.search(text)


def _letters_past_ascii(data):
    """How many letters past ASCII bytes that read as text (_reads_as_text) hold, as a sentence in a language written
    without spaces between its words does (Japanese, Chinese, Thai)."""
    start = len(data) - len(data.lstrip(_CONTINUATION_BYTES))
    text = codecs.getincrementaldecoder("utf-8")().decode(data[start:], final=False)
    return sum(1 for c in text if c > "\x7f" and c.isalpha())


# Inside a key, past its header: text past ASCII too, as UTF-8 writes it, since an armour header such as GnuPG's
# "Comment:" may hold any letter; another stream's bytes still hold a control character within a line or two.
_NOT_KEY_TEXT = re.compile(rb"[^\t-\r\x1c-\x7e\x80-\xff]")
_BYTE_ORDER_MARK = b"\xef\xbb\xbf"


def _base64_decoded(joined, start, end):
    """What joined[start:end], whole groups of base64, decodes to, or None when it is not base64."""
    try:
        return base64.b64decode(joined[start:end], validate=True)
    except ValueError:
        return None


def _frame(joined, starts, found, refused=False):
    """The key's header or footer that the encoded word `found` is in, as (b"BEGIN" or b"END", where the base64 group
    holding its first byte starts, and the character in it that holds that byte's first bits, the character that holds
    its last byte's last bits, and where the group after its last byte starts), or None when the word is in none, as in
    a field's name or a sentence about keys. A header or footer is a line of its own: after a line break or where the
    encoded text starts (a line of the block, or past another stream's padding), and before a line break or where the
    encoded text ends, with only horizontal space and quotes between, written or escaped (_lead_start,
    _SPACE_OR_QUOTE_RUN), and a line break written or escaped (_BREAK_BYTES), as a key file indented in a document, or
    written in a JSON string, writes one. Its label, and the space before and after it, are read however long: the
    bytes around its word are decoded in windows twice as wide each time while they reach a window's edge, or the edge
    cuts a token there (_CUT_FRAME_BACK, _CUT_FRAME_AHEAD). With `refused`, also a header after text on its decoded
    line, where a line of the block starts or inside one, which alone starts no key (_key_spans)."""
    byte, char = _KEY_WORD_AT[found.group()]
    group = found.start() - char
    back, ahead = 64, 40
    while True:
        lo, hi, opened = max(group - back, group % 4), min(group + ahead, len(joined)), None
        pad = joined.rfind("=", lo, group)  # another stream's end
        if pad >= 0:
            lo = opened = pad + 1 + (group - pad - 1) % 4
        whole = opened is not None or lo == group % 4  # the window starts where the encoded text does
        pad = joined.find("=", group, hi)
        closed = pad >= 0 or hi == len(joined)
        hi = pad + (group - pad) % 4 if pad >= 0 else hi - (hi - group) % 4
        decoded = _base64_decoded(joined, lo, hi) or b""
        at = (group - lo) // 4 * 3 + byte
        m = next((m for m in _KEY_FRAME.finditer(decoded) if m.start() <= at < m.end()), None)
        if m is None:
            if not whole and back < 512 and not decoded[:at].rstrip(_LABEL):
                back *= 2  # its label, up to _KEY_FRAME's 200 bytes, may start before the window
                continue
            return None
        lead = _lead_start(decoded, m.start())
        trail = _SPACE_OR_QUOTE_RUN.match(decoded, m.end()).end()
        brk = _BREAK_BYTES.match(decoded, trail)
        # Past an edge: more of the line's lead, the words before it (_starts_encoded_text), or a token the edge cuts.
        more_back = not whole and (lead <= 8 or bool(_CUT_FRAME_BACK.fullmatch(decoded, 0, lead)))
        more_ahead = not closed and not brk and bool(_CUT_FRAME_AHEAD.fullmatch(decoded, trail))
        if not (more_back or more_ahead):
            break
        back, ahead = back * (1 + more_back), ahead * (1 + more_ahead)
    first = lo + m.start() // 3 * 4
    breaks = [b.end() for b in _BREAK_BYTES_SEARCH.finditer(decoded, 0, lead)]
    line_start = breaks[-1] if breaks else 0
    opens = bool(breaks) and breaks[-1] == lead  # after a line break, past its lead
    opens = opens or _starts_encoded_text(joined, starts, lo, opened, decoded, line_start, m.start())
    opens = opens or lead < m.start() and _starts_encoded_text(joined, starts, lo, opened, decoded, line_start, lead)
    closes = brk is not None or trail == len(decoded) and closed
    if not ((opens or refused) and closes):
        return None
    return (m.group(1) or m.group(2), first, first + m.start() % 3, lo + (8 * m.end() - 1) // 6,
            lo + (m.end() + 2) // 3 * 4)


def _starts_encoded_text(joined, starts, lo, opened, decoded, line_start, at):
    """Whether the encoded text starts at byte `at` of what joined[lo:] decodes to (`decoded`, whose decoded line holding
    `at` starts at `line_start`; `opened`, where another stream's padding ends before it, if it does): a line of the
    block starts there, as a key under a heading's word starts, or it is past another stream's padding; but not where
    the wrapping of encoded text only happens to break, as a sentence that quotes a header shows, its words decoding
    before the header on its line: with a space between them, or in letters past ASCII, as a language written without
    spaces writes them ("説明-----BEGIN ..."). Or past a byte order mark there."""
    start = lo + at // 3 * 4
    line = bisect.bisect_left(starts, start)
    before = decoded[line_start:at]
    # A header that starts a line of the block below a shorter line, another stream's last, starts a stream of its
    # own, so the words before it, from the stream above, quote nothing. A stream's own last line is the shorter
    # one, below a line as wide as its wrapping, as a document's that quotes a header there is.
    own_stream = 0 < line < len(starts) and starts[line] == start and starts[line] - starts[line - 1] < (
        starts[line + 1] if line + 1 < len(starts) else len(joined)) - starts[line]
    continues = bool(before) and _reads_as_text(before) and not own_stream and (
        len(before) >= 8 and (b" " in before or _letters_past_ascii(before) >= 2)
        or before[-1:] in (b" ", b"\t"))  # "Note: -----BEGIN ..."
    # A line of the block starts there only as its wrapping breaks, however short the text before it ("例：",
    # "Ex:"): one stream wrapped at one width writes every line but its last as long as the next, where a key in a
    # stream of its own starts after another's last, shorter line (_wrapping_break). A key there still ends at its
    # footer, which a line break opens.
    # A word alone on each line above, as written ("RENT", "Walrus"), is a heading over a key in a stream of its
    # own, whatever its letters decode to ("DCS"): base64 of a label writes letters of both cases and digits.
    wrapped = bool(before) and _reads_as_text(before) and 0 < line < len(starts) and starts[line] == start and (
        _wrapping_break(starts, line, len(joined))) and not _headings(joined, starts, start - (len(before) + 2) // 3 * 4, line)
    if at % 3 == 0 and not continues and not wrapped and (start == opened or line < len(starts) and starts[line] == start):
        return True
    # or after the byte order mark a file saved on Windows starts with, where the encoded text starts: the mark
    # is a group of its own, the one before the line's. With text decoded before it on its line, as from another
    # stream above or a heading, it starts a key alone after what no text reads in, or a heading's words, and
    # otherwise, as a header after text does, only with its footer after it (`refused`)
    mark = start - 4
    mark_line = bisect.bisect_left(starts, mark)
    words = before[:-len(_BYTE_ORDER_MARK)] if before.endswith(_BYTE_ORDER_MARK) else None
    return words is not None and at % 3 == 0 and (
        mark == opened or mark_line < len(starts) and starts[mark_line] == mark) and (
        not words or not _reads_as_text(words)
        or _headings(joined, starts, mark - (len(words) + 2) // 3 * 4, mark_line))


def _wrapping_break(starts, line, size):
    """Whether the break before line `line` of a joined base64 block (`starts`, each line's start; `size`, the block's
    length) is the block's own wrapping: the line before it is as long as this one, or, for the block's last line,
    which a wrapping leaves shorter, no shorter than it."""
    before = starts[line] - starts[line - 1]
    own = (starts[line + 1] if line + 1 < len(starts) else size) - starts[line]
    return before == own or line + 1 == len(starts) and before > own


_HEADING_WORD = re.compile(r"[a-z]+|[A-Z][a-z]*|[A-Z]+")


def _headings(joined, starts, start, line):
    """Whether each line of a joined base64 block from the one holding `start` up to line `line` is, as written, one
    word in one case ("rent", "Walrus", "SENTRY"), as a heading over a key is."""
    first = max(bisect.bisect_right(starts, max(start, 0)) - 1, 0)
    return first < line and all(_HEADING_WORD.fullmatch(joined, starts[k], starts[k + 1]) for k in range(first, line))


class _Frames:
    """Each key's header and footer in a joined base64 block (_frame), read once, in order of their encoded words: for
    any place, whether a footer comes after it before another place; and each footer's place (footers, footer_bits)."""

    def __init__(self, joined, starts):
        self.at, kinds, self.footers = [], [], []
        for m in _ENCODED_KEY.finditer(joined):
            frame = _frame(joined, starts, m)
            if frame:
                self.at.append(m.start())
                kinds.append(frame[0])
                if frame[0] == b"END":
                    self.footers.append(frame[1:])
        self.footer_bits = [footer[1] for footer in self.footers]
        self.next_end, last = [None] * (len(self.at) + 1), None  # for each frame, where the first footer from it is
        for i in range(len(self.at) - 1, -1, -1):
            last = self.at[i] if kinds[i] == b"END" else last
            self.next_end[i] = last

    def footer_after(self, at, stop):
        """Whether a footer's encoded word starts at `at` or after it and before `stop`."""
        end = self.next_end[bisect.bisect_left(self.at, at)]
        return end is not None and end < stop


class _Below:
    """What a joined base64 block decodes to below a held header's line, its first byte's group starting at `start`
    (_key_spans): whether, up to a place, it is no prose (no_prose). Its whole lines are decoded and read once, however
    many headers below ask, so a block's time stays in proportion to its length."""

    def __init__(self, joined, start):
        self.joined, self.start, self.at = joined, start, start  # `at`: where decoding goes on
        self.tail, self.base = b"", 0  # the bytes decoded past the last whole line read, from this decoded offset
        self.past = False  # whether the held header's own line break is read
        self.key_text = self.letter = False  # what the whole lines read hold

    def no_prose(self, end, lead):
        """Whether what lies from the held header's next line up to the byte `lead` bytes into the group at `end` is
        no prose, as a key's lines between its header and a header written into it are: it holds a control character,
        a run a key's body could be (_token_like), as a body's line does, an armour header's line ("Comment: ...",
        "Proc-Type: ..."), another header's or footer's text, or no word at all. What does not decode in the groups of
        one stream is none either."""
        if self.key_text or (end - self.start) % 4:
            return True
        if end + 4 > self.at:
            more = _base64_decoded(self.joined, self.at, end + 4)
            if more is None:
                self.key_text = True
                return True
            self.tail, self.at = self.tail + more, end + 4
        cut = max((end - self.start) // 4 * 3 + lead - self.base, 0)
        if not self.past:
            brk = _BREAK_BYTES_SEARCH.search(self.tail, 0, cut)
            if brk is None:
                return True
            self.past, self.tail, self.base, cut = True, self.tail[brk.end():], self.base + brk.end(), cut - brk.end()
        whole = 0  # past the last line break
        for brk in _BREAK_BYTES_SEARCH.finditer(self.tail, 0, cut):
            whole = brk.end()
        if whole:
            key_text, letter = self._holds(self.tail[:whole])
            self.key_text, self.letter = key_text, self.letter or letter
            self.tail, self.base, cut = self.tail[whole:], self.base + whole, cut - whole
        key_text, letter = self._holds(self.tail[:cut])  # the line the place is on, read up to it
        return self.key_text or key_text or not (self.letter or letter)

    @staticmethod
    def _holds(lines):
        """Whether whole lines, or a line up to a place, hold what a key's lines hold, and whether a letter."""
        words = lines.decode("utf-8", "replace")
        return (not _reads_as_text(lines) or _holds_token(words, 0, len(words))
                or bool(_ARMOUR_HEADER_LINE.search(lines) or _KEY_FRAME.search(lines))), bool(_LETTER.search(words))


_LETTER = re.compile(r"[^\W\d_]")
_ARMOUR_HEADER_LINE = re.compile(b"(?:^|" + _BREAK_BYTES_SEARCH.pattern + b")" + _SPACE_OR_QUOTE_RUN.pattern
                                 + rb"[A-Za-z][A-Za-z0-9-]*: ")


def _key_range(joined, starts, ends, found, floor, frames, stream_end, held=None):
    """The first and last lines of a base64 block that hold the key whose header or footer holds the encoded word
    `found`, none above line `floor`; None when no header or footer holds it. A key starts at the line with its
    header's first byte. For a footer under a header that starts no key alone, after text on its decoded line (`held`:
    its line, whether it starts a stream of its own, and where its first group starts; _key_spans), it starts there,
    whatever its bytes between: its armour comment may hold a control character. So it does for a header below it,
    where that one starts a stream of its own or what is between them is no prose (_Below): a header
    written into its armour comments or its body starts no other key, where one under a document that quotes a header
    in a sentence above it starts its own.
    With none, as when the block starts inside the key, a line above is the key's while the base64
    through it, in the groups `found` fixes, still decodes to text. A key ends at its footer and the line breaks after
    it: where its stream holds its footer later, everything between header and footer is the key, whatever its bytes
    (an armour comment may hold any). With none, it ends where the text it decodes to ends: a heading, a word between
    two keys or another stream stays, however the lines are wrapped; a footer in a later stream, encoded on its own,
    is no footer of its, so a public key between them stays.
    Its footer is a footer _frame reads, a whole line of its own, with no other footer after it in its stream
    (`stream_end`, _stream_end; `frames`, _Frames): a footer written into an armour comment or the body as a line of its
    own, alone or with a header written after it, has the key's real footer after it, so the key runs on to that one.
    Written into an armour header ("Comment: -----END PGP PRIVATE KEY BLOCK-----") it is no line of its own. Which fails
    closed: a key's real footer with a stray one later in its stream, or another whole key after it there, runs on to
    the last footer, and text between them goes with the key, prose that holds a control character too, at which a
    stray footer's own reading up from it (above) stops. Also where in the block its reading ended, so a key whose
    header shares that line is read too."""
    frame = _frame(joined, starts, found)
    if frame is None:
        return None
    kind, first, bits = frame[:3]
    # where its stream holds its footer later, past headers written into its armour comments or its body
    footer_ahead = kind == b"BEGIN" and frames.footer_after(found.end(), ends[stream_end[bisect.bisect_right(
        ends, first)]])
    phase = first % 4
    down = lambda p: p - (p - phase) % 4  # the group boundary at or before p
    up = lambda p: p + (phase - p) % 4  # the group boundary at or after p
    top = bisect.bisect_right(ends, bits)  # its first byte's first bits: the line above can end inside its group
    if held is not None and floor <= held[0] < top and (kind == b"END" or held[1] or held[2].no_prose(
            first, bits - first)):
        top = held[0]  # a header written between them, into its armour comments or its body, starts no other key
    while kind == b"END" and held is None and top > floor and up(starts[top - 1]) < down(starts[top]):
        above = _base64_decoded(joined, up(starts[top - 1]), down(starts[top]))
        if above is None or not _reads_as_text(above):
            break
        top -= 1
    bottom, at, ended = bisect.bisect_left(ends, found.end()), first, False
    after = bisect.bisect_left(frames.footer_bits, bits)  # the footers from its first byte on, in order
    for line in range(bisect.bisect_right(ends, first), len(ends)):
        end = down(ends[line])
        if end <= at:
            continue
        below = _base64_decoded(joined, at, end)
        if below is None:
            break
        if not ended:
            # from a footer, past the bytes before it in its group: the line it ends may hold a control character
            other = None if footer_ahead else _NOT_KEY_TEXT.search(below, 3 if kind == b"END" and at == first else 0)
            # a footer counts whose first byte comes before the first that no key holds, placed as _frame places one:
            # its group, and its byte in it
            stop = at + other.start() // 3 * 4 + other.start() % 3 if other else end
            footer = None
            while after < len(frames.footers) and frames.footer_bits[after] < stop:
                start, _, last, past = frames.footers[after]
                after += 1
                if start % 4 != phase:
                    continue  # another stream's, whose groups are not these
                if frames.footer_after(past, ends[stream_end[bisect.bisect_right(ends, past - 1)]]):
                    # another footer later in its stream: the key runs on to it, whatever its bytes
                    footer_ahead, other, stop = True, None, end
                    continue
                footer = last, past
                break
            if footer:
                # the key ends with the line that holds its footer's last bits: where a line is no whole number of
                # groups, the next line can start inside that byte's group, with the bits of the bytes after it alone
                bottom, at, ended = max(bottom, bisect.bisect_right(ends, footer[0])), footer[1], True
                # and the line where the group holding its last byte ends, its line break's and padding's, where no
                # other group follows on it
                done = bisect.bisect_left(ends, at)
                if done < len(ends) and down(ends[done]) == at:
                    bottom = max(bottom, done)
                if at >= end:
                    continue
                below = _base64_decoded(joined, at, end)
            elif other:
                break
            else:
                bottom, at = max(bottom, line), end
                continue
        if not _BREAK_BYTES_RUN.fullmatch(below):
            break
        bottom, at = max(bottom, line), end
    return max(top, floor), bottom, at


_BREAK_ENDS = re.compile(rf"(?:{_BREAK})\Z")


def _starts_its_line(text, at):
    """Whether the line of a block that starts at `at` starts its line: after the text's start or a line break,
    written or escaped, with only horizontal space and quotes between, written or escaped, however many (_lead_start).
    No line break ends with one of those characters, so the break, if any, ends where they start, and the characters
    before that, as many as the longest break with one backslash to each run (_BREAK_REACH), read it, as any number of
    backslashes before an escape's letter reads as one."""
    start = _lead_start(text, at)
    return not start or _BREAK_ENDS.search(text, max(start - _BREAK_REACH, 0), start) is not None


def _stream_end(widths, padded, k, cut=False):
    """The last line of the stream that starts at line `k` of a base64 block (`widths`, each line's width; `padded`,
    whether it holds "="). A stream's lines are as wide as its widest so far but its last, which holds its "=" or is
    shorter, so a line of either ends it (a blank line, or one that is not base64, ends the block). But a shorter line
    as wide as the next, with no "=", starts a narrower stream, as a key file wrapped narrower below a stream does, and
    a line wider than those above starts a wider one, as one below a footer encoded alone on a line of its own does:
    there the stream ended on the line before. Where its first line is `cut`, the block's first after text on its
    line, as narrow as what that left of it, a wider line starts one only past two or more of one width."""
    width, run = 0, 0  # the stream's width, and how many of its lines are read
    for j in range(k, len(widths)):
        w = widths[j]
        wider = w > width and (run >= 2 or not cut)
        if width and (wider or w < width and not padded[j] and widths[j + 1:j + 2] == [w]):
            return j - 1
        if padded[j] or w < width:
            return j
        width, run = max(width, w), run + 1
    return len(widths) - 1


def _key_spans(text, lines):
    """The spans to mask in a base64 block: for each key's header or footer word outside the spans before it, its key's
    lines. The block is joined once, and each key's lines are read once."""
    joined = "".join(text[a:b] for a, b in lines)
    ends = list(itertools.accumulate(b - a for a, b in lines))
    starts = [0] + ends[:-1]
    widths = [b - a for a, b in lines]
    padded = ["=" in joined[starts[k]:ends[k]] for k in range(len(lines))]
    stream_end, k = [], 0  # stream_end[k]: the last line of the stream that holds line k (_stream_end)
    cut = not _starts_its_line(text, lines[0][0])
    while k < len(lines):
        last = _stream_end(widths, padded, k, cut and not k)
        stream_end += [last] * (last + 1 - k)
        k = last + 1
    spans, floor, done, held, held_end, frames = [], 0, 0, None, None, _Frames(joined, starts)
    for found in _ENCODED_KEY.finditer(joined):
        line = bisect.bisect_right(ends, found.start())
        if line < floor or found.start() < done:  # inside the key read last, but for a header past its end on its line
            continue
        if held is not None and line > held_end:
            held = None  # a header quoted in a stream that ended above starts no key in a later one
        key = _key_range(joined, starts, ends, found, floor, frames, stream_end, held)
        if key:
            spans.append((lines[key[0]][0], lines[key[1]][1]))
            floor, done, held = key[1], key[2], None
            continue
        # A header after text on its decoded line ("例：-----BEGIN ...", a sentence, or another stream that fills
        # whole lines above), where a line of the block starts or inside one, starts no key alone, as a sentence
        # quoting a header shows one; with a footer after it in its stream, the first such since the last key starts
        # that footer's key, whatever its armour comments hold.
        frame = _frame(joined, starts, found, refused=True)
        if frame and frame[0] == b"BEGIN":
            line = bisect.bisect_right(ends, frame[2])  # the line holding its first byte's first bits
            # Where such a header starts its line, after a byte order mark or none and any horizontal space, and the
            # line above is not as wide, the block's wrapping does not break there (_wrapping_break): a key file
            # wrapped below another stream, in a line as wide as none above and then its short last line, starts its
            # own stream there, though the widths alone read that line as the last of the stream above.
            gap = frame[1] - starts[line]
            lead = _base64_decoded(joined, starts[line], frame[1]) if gap >= 0 and not gap % 4 else None
            marked = lead is not None and lead.startswith(_BYTE_ORDER_MARK)
            own = bool(line) and lead is not None and bool(_SPACE_OR_QUOTE_RUN.fullmatch(
                lead[len(_BYTE_ORDER_MARK) if marked else 0:])) and not _wrapping_break(starts, line, len(joined))
            # One below another, past prose (_Below), takes its place where it starts a stream of its own, by the
            # widths or after a byte order mark, as a key file's header does under a document that quotes one, unless
            # the first starts a stream of its own; a header written after a word into the key below it takes none.
            if held is not None and (held[1] or not (own or line and marked) or held[2].no_prose(
                    frame[1], frame[2] - frame[1])):
                continue
            held, held_end = (line, own, _Below(joined, frame[1])), _stream_end(widths, padded, line) if own else (
                stream_end[line])
    return spans


def _written_key_spans(text):
    """Each private key in a file written as base64 in `text`, whole with the lines it wraps onto, as a (start, end). A
    run on the line after a block's last, which has text after it on its line ("... next steps", or an escape that is
    no line break), is that block's last line, as a stream's last line with words after it is: a header there, quoting
    one in a sentence that the lines above decode to, reads as it does in the stream above."""
    spans, block = [], []
    for m in _WRAPPED_BASE64.finditer(text):
        escape = _ESCAPE_LETTER.match(text, m.start())
        lines = _wrapped_lines(text, escape.end() if escape else m.start(), m.end())
        # a run led by an escape's letter whose line break comes right after it: a blank line escaped ("\\n\\n"),
        # which ends the block above, as one written plain does
        blank = escape and len(lines) > 1 and lines[0][0] == lines[0][1]
        if blank:
            lines = lines[1:]
        if block and not blank and _UNWRAP.fullmatch(text, block[-1][1], lines[0][0]):
            block += lines
            continue
        spans += _key_spans(text, block) if block else []
        block = lines
    return spans + (_key_spans(text, block) if block else [])


# A character a reader does not see, with the backslashes before an escaped one, as JSON in JSON escapes it twice. It
# starts where no backslash stands before it, so a long run of them is tried once, from its start, not at each of its
# backslashes in turn, which took time in the square of its length. A colour code is tried whole first, as _CSI reads
# its escape's own run of backslashes, so the code's "[0m" goes with its ESC.
_UNSEEN_ESCAPED = re.compile(rf"{_CSI}|(?<!\\)\\*(?:{_CSI}|{_ESCAPED_INVISIBLE})|[\x00-\x08\x0e-\x1b\x7f-\x84\x86-\x9f{_FORMAT_CHARACTERS}]")


def _unseen_view(text):
    """(`text` without the characters a reader does not see, at any depth of escaping, and for each of its characters
    where it stands in `text`), so a run one of them splits reads whole."""
    if not INVISIBLE.search(text):
        return text, range(len(text))
    kept, pieces, last = [], [], 0
    for m in _UNSEEN_ESCAPED.finditer(text):
        # An escaped form takes only its own backslashes: one where a run of them is odd, two (an escape escaped
        # again, as JSON in JSON writes one) where it is even; the rest escape each other, as a line break's does
        # in "\\<ZWSP>n" written as JSON.
        run = len(m.group()) - len(m.group().lstrip("\\"))
        start = m.start() + (run - 1 if run % 2 else run - 2) if run else m.start()
        pieces.append(text[last:start])
        kept += range(last, start)
        last = m.end()
    pieces.append(text[last:])
    kept += range(last, len(text))
    return "".join(pieces), kept


# JSON's escapes, a surrogate pair as one character.
_JSON_ESCAPE = re.compile(r"\\(?:u([dD][89abAB][0-9a-fA-F]{2})\\u([dD][c-fC-F][0-9a-fA-F]{2})|u([0-9a-fA-F]{4})|([\"\\/bfnrt]))")
_JSON_LETTER = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def _json_unescaped(text):
    """(`text` with each of JSON's escapes read as the character it writes, and a function from a (start, end) in that
    view to the (start, end) in `text` it was read from), or None when `text` holds no escape. An escaped backslash is
    one backslash, so the view of JSON in JSON is JSON once."""
    pieces, at, marks, last = [], [], [], 0  # at: where each escape's character stands in the view; marks: its escape
    for m in _JSON_ESCAPE.finditer(text):
        high, low, code, letter = m.groups()
        pieces.append(text[last:m.start()])
        at.append(sum(map(len, pieces)) if not at else at[-1] + 1 + m.start() - last)
        marks.append((m.start(), m.end()))
        pieces.append(chr(0x10000 + (int(high, 16) - 0xD800) * 0x400 + int(low, 16) - 0xDC00) if high
                      else chr(int(code, 16)) if code else _JSON_LETTER[letter])
        last = m.end()
    if not marks:
        return None
    pieces.append(text[last:])

    def source(i, first):  # where the view's character i comes from in `text`: its first character, or past its last
        k = bisect.bisect_right(at, i) - 1
        if k < 0:
            return i + (not first)
        if at[k] == i:
            return marks[k][0] if first else marks[k][1]
        return marks[k][1] + i - at[k] - 1 + (not first)

    return "".join(pieces), lambda start, end: (source(start, True), source(end - 1, False))


def _encoded_key_spans(text):
    """Where each private key in a file written as base64 lies in `text`, as ordered (start, end) spans: read as
    written, with JSON's escapes read once and twice (_json_unescaped), as JSON and JSON in JSON write a key with any
    escape (an indentation's tab, a line break as \\u000a, a slash as \\/), and each of these read without the
    characters a reader does not see (_unseen_view), so one inside a line parts nothing."""
    spans, view, source = [], text, None
    for depth in range(3):
        if depth:
            read = "\\" in view and _json_unescaped(view)
            if not read:
                break
            view, inner = read
            source = inner if source is None else (lambda outer, inner: lambda a, b: outer(*inner(a, b)))(source, inner)
        found = _written_key_spans(view)
        unseen, kept = _unseen_view(view)
        if unseen is not view:
            found += [(kept[start], kept[end - 1] + 1) for start, end in _written_key_spans(unseen)]
        spans += found if source is None else [source(start, end) for start, end in found]
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _docker_login_edits(text):
    """Each Docker "auth" login, base64 of "user:password", as an edit that masks it."""
    return [(m.end(1), m.end(), "[masked]") for m in _AUTH_FIELD.finditer(text) if _basic_credential(m.group(2))]


def _command_line(text, start, stop=None, known=None, keyed=True):
    """(where a command's line from `start` ends, by `stop` if given, the stretches of it inside quotes or a substitution at its top level,
    in order, the command substitutions in it that no other holds, in order), as the shell reads it. The shell's contexts are kept on a stack:
    single quotes, which hold everything to the next one; double quotes, which hold a substitution or a backtick; and
    "$(", "(" and backticks, which hold quotes and brackets of their own, so arithmetic's "$((...))" closes as it opens;
    a process substitution, "<(" or ">(", is a substitution as "$(" is, so the flags after it are the command's own.
    At the top level the line ends at what ends a command or starts another (a pipe, ";", "&" but in a redirection
    such as "2>&1", a bracket), at a comment (a "#" that starts a word), and at a JSON string's end (a '"' that opens no
    quote, before ",", ":", "}" or "]"); anywhere at a line break, plain, or escaped but inside quotes of plain text,
    where quotes still open hold to the line's end, as a script over several lines in quotes holds its own flags. A
    line whose first double quote outside single quotes comes escaped ('\\"'), as JSON holds a command, is read
    unescaped: its escaped quotes are the shell's quotes, an escaped backslash escapes the shell character after it,
    and a bare '"' ends the JSON string. `known` maps where each substitution read before starts, with whether the
    line's quotes were found escaped there (None: not yet) where `keyed`, to whether it closed, where (or where a
    reading ended with it still open, which it holds to), and whether they were found escaped after it. The reading
    adds to it and passes over each, so a command inside one costs no second reading of what it holds, even where the
    substitutions no longer close: where `keyed`, only each it comes to in the same state, and on in the state after
    it, so a command inside one reads as it reads alone; else each, as the reading that read it found it."""
    i, n = start, len(text) if stop is None else stop
    known = {} if known is None else known
    # The shell's open contexts, where and in which state each opened, and the state: whether quotes come escaped
    stack, opened, escaped = [], [], None
    inside, subs, held = [], [], 0  # held: how many of the open contexts are substitutions

    def enter(kind, at, after):  # where the reading goes on once `kind` opens at `at`: after it, or past all it holds
        nonlocal held, escaped
        read = known.get((at, escaped) if keyed else at) if kind in ("$(", "`") else None
        end = None
        if read is not None:
            closed, to, then = read
            if closed and to <= n:  # it closes by `n`
                end = to
                if keyed:  # and the reading goes on as the one before went on past it
                    escaped = then
            elif not closed and n <= to:  # still open where a reading before ended: it holds to `n`
                end = n
        if end is not None:
            if not held:
                subs.append((at, end))
            if not stack:
                inside.append((at, end))
            return end
        stack.append(kind)
        opened.append((at, escaped))
        held += kind in ("$(", "`")
        return after

    def ended(at):  # the line ends at `at`, inside what is still open there, which holds to it
        while stack:
            close(at, closed=False)
        return at, inside, subs

    def close(at, closed=True):
        nonlocal held
        kind, (began, state) = stack.pop(), opened.pop()
        if kind in ("$(", "`"):
            held -= 1
            key = (began, state) if keyed else began
            read = known.get(key)
            if closed:
                known[key] = (True, at, escaped)
            elif not (read and read[0]):
                known[key] = (False, max(at, read[1] if read else at), None)
            if not held:
                subs.append((began, at))
        if not stack:
            inside.append((began, at))

    while i < n:
        here, c = i, text[i]
        top = stack[-1] if stack else ""
        if top == "'":  # nothing is special inside single quotes but the closing one
            if c in "\n\r" or c == '"' and escaped:
                return ended(here)
            i += 1
            if c == "'":
                close(i)
            continue
        if c == "\\":
            following = text[i + 1:i + 2]
            if following in ("n", "r"):
                if top == '"' and not escaped:
                    i += 2  # a backslash and a letter inside double quotes of plain text: part of the argument
                    continue
                return ended(here)
            if following == '"' and escaped is not False:
                escaped, c, i = True, following, i + 2
            else:  # the shell's own backslash, and the character it escapes
                i += 2
                if escaped and following == "\\":
                    i += 2 if text[i:i + 1] == "\\" else 1
                continue
        else:
            if c in "\n\r" or c == '"' and escaped:
                return ended(here)
            if c == '"' and not stack and _JSON_STRING_END.match(text, i + 1):
                return ended(here)
            if c == '"' and escaped is None:
                escaped = False
            i += 1
        substitution = c == "$" and text[i:i + 1] == "("
        if top == '"':
            if c == '"':
                close(i)
            elif substitution or c == "`":
                i = enter("$(" if substitution else "`", here, i + substitution)
            continue
        if top == "`" and c == "`" or top in ("$(", "(") and c == ")":
            close(i)
            continue
        if top:  # inside a substitution: its own quotes and brackets
            if c in "'\"`(" or substitution:
                i = enter("$(" if substitution else c, here, i + substitution)
            continue
        if c in "'\"`" or substitution:
            i = enter("$(" if substitution else c, here, i + substitution)
        elif c == "(" and here > start and text[here - 1] in "<>":
            i = enter("$(", here - 1, i)
        elif c in "|;()":
            return ended(here)
        elif c == "&" and not (here > start and text[here - 1] in "<>" or text[i:i + 1] == ">"):
            return ended(here)
        elif c == "#" and (here == start or text[here - 1] in " \t"):
            return ended(here)
    return ended(n)


def _holding(stretches, at):
    """The one of `stretches`, (start, end) pairs in order that do not overlap, that holds `at`, or None."""
    k = bisect.bisect_right(stretches, (at, math.inf)) - 1
    return stretches[k] if k >= 0 and at < stretches[k][1] else None


# A variable or a template's slot standing for a value, as a .netrc file shown with its values left out holds one.
_VARIABLE = re.compile(r"\$[{(A-Za-z_]|%[A-Za-z_]\w*%$|\{\{|<[^>]*>$")


def _netrc_password(value):
    """Whether a .netrc line's value is a password: four or more characters, no placeholder and no variable. A .netrc
    file expands nothing and calls nothing, so letters only, or a bracket, are a password's too."""
    return len(value) >= 4 and value.lower() not in PLACEHOLDERS and not _VARIABLE.match(value)


_NETRC_LINE_END = re.compile(r"\r|\n|(?<!\S)#|\\[nr](?=[ \t]*(?:(?:machine|default|login|password|account|macdef)\b|[\"\\]|$))")


# What follows a .netrc value on its line: another field, a comment, the line's end, or the end of the quoted string
# that holds the file, so "the login page password field is empty" is prose.
_NETRC_AFTER_VALUE = re.compile(r"[ \t]*(?:\Z|[\r\n#\"]|\\[nr\"]|(?:machine|default|login|password|account|macdef|port)"
                                r"(?=[ \t\r\n\\\"]|\Z))")
_NETRC_FIELD = re.compile(r"(?<!\S)(?:machine|login|password|account|macdef)[ \t]+\Z")


_NETRC_LOGIN = re.compile(r"(?<!\S)login[ \t]+(\S+)")
_NETRC_HOST = re.compile(r"[ \t]*machine[ \t]+(?:localhost|\S*[.:0-9])")


def _netrc_prose(line, value):
    """Whether a line that reads as a .netrc entry is a sentence: no host after "machine", and its login and its
    password both words of letters ("default login page password requirements.", "machine learning login page
    password self-service"), where a .netrc file names a host and a user and holds a password."""
    if _NETRC_HOST.match(line):
        return False
    login = _NETRC_LOGIN.search(line)
    return bool(login and re.fullmatch(r"[A-Za-z]+", login.group(1))
                and re.fullmatch(r"[A-Za-z]+(?:['-][A-Za-z]+)*[.,;:!?]?", value))


def _plain_line_end(text, start):
    """Where a .netrc line from `start` ends: at a line break, at a comment (a "#" that starts a word, but for a field's
    value, as "password #Tr0ub4dor9" holds one), or at an escaped line break before the next field or the text's end,
    as a .netrc file shown in JSON holds one; an escaped "n" in a value is part of it, as .netrc reads "\\n" in a
    password."""
    for m in _NETRC_LINE_END.finditer(text, start):
        if not (m.group() == "#" and _NETRC_FIELD.search(text, max(start, m.start() - 64), m.start())):
            return m.start()
    return len(text)


def _past_closing_quote(text, after, start, end):
    """Where a command named at text[start:end] goes on: past the quote right after its name, where that quote closes
    one of its kind opened on its line before the name, and after `after`, where the command before it ended, as a
    quoted path to a program ('"C:\\...\\keytool.exe"') holds it. Each command looks back only to the one before, so a
    text that names many costs time in proportion to its length."""
    quote = next((q for q in ('\\"', "\\'", '"', "'") if text.startswith(q, end)), None)
    if quote is None:
        return end
    after = max(after, text.rfind("\n", after, start) + 1, text.rfind("\r", after, start) + 1)
    at = text.rfind(quote[-1], after, start)  # its own kind: the other may stand inside it ('"/opt/team's tools/..."')
    if at < 0:
        return end
    opened = text[at - 1:at + 1] if at > 0 and text[at - 1] == "\\" else text[at]
    return end + len(quote) if opened == quote else end


# What follows a '"' that ends a JSON string rather than a shell argument.
_JSON_STRING_END = re.compile(r"[ \t]*[,:}\]]")
# keytool's and jarsigner's own options (JDK 8 to 21), and a JVM option ("-keystore", "-v", "-J-Xmx512m").
_JAVA_OPTIONS = frozenset("""
addprovider alias cacerts certchain certreq certs changealias conf delete destalias destkeypass destkeystore
destprotected destprovidername deststorepass deststoretype digestalg dname exportcert ext file gencert gencrl genkey
genkeypair genseckey groupname h help id identitydb import importcert importkeystore importpass infile internalsf
jarfile keyalg keyclone keypass keypasswd keysize keystore list maxsigblock new noprompt outfile printcert printcertreq
printcrl protected providerarg providerclass providername providerpath revcheck rfc sectionsonly selfcert showinfo
sigalg sigfile signedjar signer signerkeypass srcalias srckeypass srckeystore srcprotected srcprovidername
srcstorepass srcstoretype sslserver startdate storepass storepasswd storetype strict tls trustcacerts tsa tsacert
tsadigestalg tsapolicyid v validity verbose verify version
""".split())


def _flag_matches(text, flag, start, end):
    """flag.finditer(text, start, end), but for aws configure set's row not where the setting's name is the value of
    one of aws's options that take one (_AWS_VALUE_OPTIONS), as written or in quotes, plain or escaped, with a line's
    continuation or none between them; the setting may come right after it. The option is the word before the name,
    however many spaces and continuations stand between: the line's words are found once, so a long line costs time in
    proportion."""
    at, words, word_starts = start, None, None
    while True:
        m = flag.search(text, at, end)
        if m is None:
            return
        if flag is _AWS_FLAG:
            if words is None:
                words = [(w.start(), w.end()) for w in _LINE_WORD.finditer(text, start, end)]
                word_starts = [a for a, _ in words]
            k = bisect.bisect_left(word_starts, m.start()) - 1
            while k >= 0 and _CONTINUATION_WORD.fullmatch(text, words[k][0], min(words[k][1], m.start())):
                k -= 1
            if k >= 0 and text[words[k][0]:min(words[k][1], m.start())].strip("'\"\\").lower() in _AWS_VALUE_OPTIONS:
                at = m.start() + 1
                continue
        at = m.end()
        yield m


def _command_password_edits(text, line_pattern, flag, always, unseen=False):
    """Each password one row of COMMAND_FLAGS finds, as edits that mask it, in order (_read_password_edits). Where the
    text names the row's command a way main does not spell it (_MAIN_SPELLING: "az.exe login"), the row reads it with
    main's spelling too, and masks what either reading finds: read as a command, a name main does not read can hold
    another command main reads as one in an argument of its line ('az.exe login failed with "mongosh $(echo "") -p
    ..."'), which then shows nothing main hides."""
    edits = _read_password_edits(text, line_pattern, flag, always, unseen)
    narrower = _MAIN_SPELLING.get(line_pattern)
    if narrower is not None and any(not narrower.match(text, m.start()) for m in line_pattern.finditer(text)):
        edits = _in_order(edits + _read_password_edits(text, narrower, flag, always, unseen))
    return edits


def _read_password_edits(text, line_pattern, flag, always, unseen=False):
    """Each password one row of COMMAND_FLAGS finds, as an edit that masks it (_read_lines). With `unseen`, the text
    is read twice, and what either reading finds is masked: once with each substitution read passed on only to a
    reading that comes to it in the same state, so each command reads as it reads alone, and once with it passed on to
    any, as the reading before found it, where the quotes and backslashes in it mean what they meant there."""
    if not unseen:
        return _read_lines(text, line_pattern, flag, always, unseen, True)
    return _in_order(_read_lines(text, line_pattern, flag, always, unseen, True)
                     + _read_lines(text, line_pattern, flag, always, unseen, False))


def _read_lines(text, line_pattern, flag, always, unseen, keyed):
    """Each password one row of COMMAND_FLAGS finds, as an edit that masks it. A command's line is read once from its
    first word, and a command in a substitution once from its own, so a line that names the command again and again
    costs time in proportion to its length. With `unseen`, as the rows main's own lack are read, the line is read as
    the shell reads it (_command_line), past a quote that closes the command's quoted name, and a value is judged
    without the characters a reader does not see, so a coloured "$STOREPASS" is a variable and a coloured "--quiet"
    the next option, and the substitutions read are passed on as _command_line passes them with `keyed`."""
    edits, reading, known = [], [], {}  # reading: the lines read that hold the text so far, innermost last
    read_to = 0  # where the lines read before the text so far end
    for line in line_pattern.finditer(text):
        while reading and reading[-1][0] <= line.start():
            read_to = max(read_to, reading.pop()[0])
        held = _holding(reading[-1][1], line.start()) if reading else None
        if reading and not held:
            continue  # the command named again inside a line already read, not as a command of its own
        inside, subs = [], []
        if always == "field":
            end = _plain_line_end(text, line.end())
        elif unseen:  # a command inside the substitution of a line read is read as a line of its own, to its end
            begin = _past_closing_quote(text, max(read_to, held[0] if held else 0), line.start(), line.end())
            end, inside, subs = _command_line(text, begin, held and held[1], known, keyed)
        else:
            end = line.end()
        reading.append((end, subs))
        starts = [a for a, _ in inside]
        # Every flag from the start of a stretch that holds to the line's end is inside it: none is read past there.
        last = inside[-1][0] if inside and inside[-1][1] >= end else end
        for m in _flag_matches(text, flag, line.start(), end):
            if m.start() >= last:
                break
            k = bisect.bisect_right(starts, m.start()) - 1
            if k >= 0 and m.start() < inside[k][1]:
                continue  # a flag inside a quoted argument or a substitution is another program's

            value = INVISIBLE.sub("", m.group(2)) if unseen else m.group(2)
            if always == "field":  # a .netrc line's password, which nothing expands: any value but a placeholder
                if _netrc_password(value) and _NETRC_AFTER_VALUE.match(text, m.end(2)) \
                        and not _netrc_prose(text[line.start():end], value):
                    edits.append((m.start(2), m.end(2), "[masked]"))
                continue
            if _flag_value_counts(m, always, unseen):
                edits.append((m.start(2), m.end(2), "[masked]"))
    return _in_order(edits)


def _todays_command_line(text, start, stop=None, known=None):
    """Main 33c82f6's _command_line, as main reads a line today (_todays_password_edits): (where a command's line from
    `start` ends, by `stop` if given, the stretches of it inside quotes or a substitution at its top level, in order,
    the command substitutions in it that no other holds, in order), as the shell reads it. The shell's contexts are kept
    on a stack: single quotes, which hold everything to the next one; double quotes, which hold a substitution or a
    backtick; and "$(", "(" and backticks, which hold quotes and brackets of their own, so arithmetic's "$((...))"
    closes as it opens. At the top level the line ends at what ends a command or starts another (a pipe, ";", "&" but in
    a redirection such as "2>&1", a bracket), at a comment (a "#" that starts a word), and at a JSON string's end (a '"'
    that opens no quote, before ",", ":", "}" or "]"); anywhere at a line break, plain, or escaped but inside quotes of
    plain text, where quotes still open hold to the line's end, as a script over several lines in quotes holds its own
    flags. A line whose first double quote outside single quotes comes escaped ('\\"'), as JSON holds a command, is read
    unescaped: its escaped quotes are the shell's quotes, an escaped backslash escapes the shell character after it, and
    a bare '"' ends the JSON string. `known` maps where each substitution read before starts to where it ends, which the
    reading adds to and passes over, so a command inside one costs no second reading of what it holds."""
    i, n = start, len(text) if stop is None else stop
    known = {} if known is None else known
    stack, opened, escaped = [], [], None  # the shell's open contexts, where each opened, whether quotes come escaped
    inside, subs, held = [], [], 0  # held: how many of the open contexts are substitutions

    def enter(kind, at, after):  # where the reading goes on once `kind` opens at `at`: after it, or past all it holds
        nonlocal held
        end = known.get(at) if kind in ("$(", "`") else None
        if end is not None and end <= n:
            if not held:
                subs.append((at, end))
            if not stack:
                inside.append((at, end))
            return end
        stack.append(kind)
        opened.append(at)
        held += kind in ("$(", "`")
        return after

    def ended(at):  # the line ends at `at`, inside what is still open there, which holds to it
        while stack:
            close(at, closed=False)
        return at, inside, subs

    def close(at, closed=True):
        nonlocal held
        kind, began = stack.pop(), opened.pop()
        if kind in ("$(", "`"):
            held -= 1
            if closed:
                known[began] = at
            if not held:
                subs.append((began, at))
        if not stack:
            inside.append((began, at))

    while i < n:
        here, c = i, text[i]
        top = stack[-1] if stack else ""
        if top == "'":  # nothing is special inside single quotes but the closing one
            if c in "\n\r" or c == '"' and escaped:
                return ended(here)
            i += 1
            if c == "'":
                close(i)
            continue
        if c == "\\":
            following = text[i + 1:i + 2]
            if following in ("n", "r"):
                if top == '"' and not escaped:
                    i += 2  # a backslash and a letter inside double quotes of plain text: part of the argument
                    continue
                return ended(here)
            if following == '"' and escaped is not False:
                escaped, c, i = True, following, i + 2
            else:  # the shell's own backslash, and the character it escapes
                i += 2
                if escaped and following == "\\":
                    i += 2 if text[i:i + 1] == "\\" else 1
                continue
        else:
            if c in "\n\r" or c == '"' and escaped:
                return ended(here)
            if c == '"' and not stack and _JSON_STRING_END.match(text, i + 1):
                return ended(here)
            if c == '"' and escaped is None:
                escaped = False
            i += 1
        substitution = c == "$" and text[i:i + 1] == "("
        if top == '"':
            if c == '"':
                close(i)
            elif substitution or c == "`":
                i = enter("$(" if substitution else "`", here, i + substitution)
            continue
        if top == "`" and c == "`" or top in ("$(", "(") and c == ")":
            close(i)
            continue
        if top:  # inside a substitution: its own quotes and brackets
            if c in "'\"`(" or substitution:
                i = enter("$(" if substitution else c, here, i + substitution)
            continue
        if c in "'\"`" or substitution:
            i = enter("$(" if substitution else c, here, i + substitution)
        elif c in "|;()":
            return ended(here)
        elif c == "&" and not (here > start and text[here - 1] in "<>" or text[i:i + 1] == ">"):
            return ended(here)
        elif c == "#" and (here == start or text[here - 1] in " \t"):
            return ended(here)
    return ended(n)


def _todays_password_edits(text, line_pattern, flag, always, unseen=False):
    """Main 33c82f6's _command_password_edits, kept as main reads each row of COMMAND_FLAGS today, so the mask hides all
    main hides (_TODAYS_RULES): each password one row finds, as an edit that masks it. A command's line is read once
    from its first word, and a command in a substitution once from its own, so a line that names the command again and
    again costs time in proportion to its length. With `unseen`, as the rows main's own lack are read, the line is read
    as the shell reads it (_command_line), past a quote that closes the command's quoted name, and a value is judged
    without the characters a reader does not see, so a coloured "$STOREPASS" is a variable and a coloured "--quiet" the
    next option."""
    edits, reading, known = [], [], {}  # reading: the lines read that hold the text so far, innermost last
    read_to = 0  # where the lines read before the text so far end
    for line in line_pattern.finditer(text):
        while reading and reading[-1][0] <= line.start():
            read_to = max(read_to, reading.pop()[0])
        held = _holding(reading[-1][1], line.start()) if reading else None
        if reading and not held:
            continue  # the command named again inside a line already read, not as a command of its own
        inside, subs = [], []
        if always == "field":
            end = _plain_line_end(text, line.end())
        elif unseen:  # a command inside the substitution of a line read is read as a line of its own, to its end
            begin = _past_closing_quote(text, max(read_to, held[0] if held else 0), line.start(), line.end())
            end, inside, subs = _todays_command_line(text, begin, held and held[1], known)
        else:
            end = line.end()
        reading.append((end, subs))
        starts = [a for a, _ in inside]
        for m in _flag_matches(text, flag, line.start(), end):
            k = bisect.bisect_right(starts, m.start()) - 1
            if k >= 0 and m.start() < inside[k][1]:
                continue  # a flag inside a quoted argument or a substitution is another program's

            value = _TODAYS_INVISIBLE.sub("", m.group(2)) if unseen else m.group(2)
            if always == "field":  # a .netrc line's password, which nothing expands: any value but a placeholder
                if _netrc_password(value) and _NETRC_AFTER_VALUE.match(text, m.end(2)) \
                        and not _netrc_prose(text[line.start():end], value):
                    edits.append((m.start(2), m.end(2), "[masked]"))
                continue
            if unseen and (value.startswith("-") and (always != "dash" or value[1:].lower() in _JAVA_OPTIONS
                                                      or value.startswith("-J"))
                           or _ABOUT_A_PASSWORD.search(m.group(1).strip(" \t'\"\\"))):
                continue  # the next option, or a setting about a secret ("aws configure set token_expiration 3600")
            if always is True or _looks_secret("password", m.group(1), value):
                edits.append((m.start(2), m.end(2), "[masked]"))
    return edits


def _in_order(edits):
    """One row's edits in order, as _edit and _moves read them, those that overlap made one: a row reads a command of
    its own inside a substitution of a line it read after that line ("mongosh $(echo az login -p ...) -p ..."), so its
    edit comes after the line's."""
    out = []
    for start, end, insert in sorted(edits):
        if out and start < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end), out[-1][2])
        else:
            out.append((start, end, insert))
    return out


def _moves(edits):
    """Where one row's ordered edits (start, end, insert) begin and end, and how far each, with those before it, moves
    the text after it: what _carried reads."""
    shift, shifts = 0, []
    for start, end, insert in edits:
        shift += len(insert) - (end - start)
        shifts.append(shift)
    return [start for start, _, _ in edits], [end for _, end, _ in edits], shifts


def _carried(moves, at):
    """Where the character at `at` is once the rows' edits (`moves`, each _moves of one row's, in turn) are made, or
    None where one of them masked it."""
    for starts, ends, shifts in moves:
        i = bisect.bisect_right(starts, at) - 1
        if i >= 0 and at < ends[i]:
            return None
        at += shifts[i] if i >= 0 else 0
    return at


def _before_edits(before, rows, moves):
    """The rows of COMMAND_FLAGS read in turn (`rows`, with `moves`, each row's edits as _moves gives them) each read
    the text the rows before them left, where a value a row before masked can change the line: a command named there
    has no name left to read its flags after ('az.exe login -p "keytool" -storepass ...', "mysql -paws configure set
    ..."), and a value read inside backticks is masked with the closing one, so the line runs on past it and a later
    flag is inside it ("keytool $(mongosh `az.cmd login -p ...` -p ...) -storepass ..."). Each of this PR's rows (from
    _MAIN_COMMANDS on) after a row that masked anything reads `before`, the text as it was before any row masked,
    again, and each password it finds there is masked in the text the rows left, over the characters no row masked, so
    the text loses nothing a row reading it alone hides: one edit for each run, those of two rows that meet made one.
    Returns the edits, and the first row whose reading of `before` masked more."""
    spans, first = [], None
    for k in range(_MAIN_COMMANDS, len(rows)):
        line, flag, always = rows[k]
        if not any(starts for starts, _, _ in moves[:k]):
            continue  # no row before it masked anything
        spans += [(start, end, k) for start, end, _ in _command_password_edits(before, line, flag, always, True)]
    out = []
    for start, end, k in sorted(spans):
        kept = [at for at in (_carried(moves, at) for at in range(start, end)) if at is not None]
        if not kept:
            continue
        first = first or rows[k]
        if out and kept[0] <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], kept[-1] + 1))
        else:
            out.append((kept[0], kept[-1] + 1))
    return [(start, end, "[masked]") for start, end in out], first


def _flag_value_counts(m, always, unseen):
    """Whether the value one row of COMMAND_FLAGS finds after its flag (`m`) is a password, but on a .netrc line."""
    value = INVISIBLE.sub("", m.group(2)) if unseen else m.group(2)
    if unseen and (value.startswith("-") and (always != "dash" or value[1:].lower() in _JAVA_OPTIONS
                                              or value.startswith("-J"))
                   or _ABOUT_A_PASSWORD.search(m.group(1).strip(" \t'\"\\"))):
        return False  # the next option, or a setting about a secret ("aws configure set token_expiration 3600")
    if unseen and REFERENCE.match(value.rstrip(".,;:!?")):
        return False  # a variable at a sentence's end ("az login -p $PASSWORD.")
    return always is True or _looks_secret("password", m.group(1), value)


# A line break after the shell's continuation mark, a backslash, or a backtick (PowerShell) or a caret (cmd) after a
# space, as written and escaped once or twice as JSON writes it: the command goes on past it. The backslashes are a run
# no backslash stands before, as long as the depth's own (one, three, six), so an escaped backslash before an "n", as
# a Windows path escaped as JSON holds one ("C:\\\\new"), is no continuation.
_CONTINUATION = re.compile(r"(?:(?<!\\)\\|(?<=[ \t])[`^])\r?\n"
                           r"|(?:(?<!\\)\\\\\\\\|(?<=[ \t])[`^])(?:\\\\r)?\\\\n"
                           r"|(?:(?<!\\)\\\\|(?<=[ \t])[`^])(?:\\r)?\\n")
# What of a command's line kb reads plainly, as _command_line reads it, from where the command's name ends: words,
# spaces, a redirection, a variable, quotes plain or escaped as JSON holds them, and a substitution with nothing nested.
_SIMPLE_SUBSTITUTION = r"\$\(\([^()'\"`$\\]*\)\)|\$\([^()'\"`$\\]*\)|`[^`'\"$\\]*`|\$\{\w*\}|\$(?![('{\[])"
_DOUBLE_QUOTED = rf"(?:[^\"\\$`]|\\[nrt]|{_SIMPLE_SUBSTITUTION})*"
_PLAIN_ARGUMENTS = re.compile(rf"""(?:[^\s'"`$\\()|;&<>#]+|[ \t]+|\\t|(?<=\S)\#|[<>]+(?!\()|(?<=[<>])&|&(?=>)"""
                              rf"""|{_SIMPLE_SUBSTITUTION}|'[^']*'|"(?![ \t]*(?:[,:}}\]]|\Z)){_DOUBLE_QUOTED}"|"""
                              rf"""\\"{_DOUBLE_QUOTED}\\")*""")
# Where what kb reads plainly ends the command: the text's or the line's end, a pipe, ";", "&", a bracket that closes,
# or a comment. A command inside a JSON string is read in that string decoded as a text of its own (_container_edits), so
# the string's end is that text's end, and the mask never leaves the string.
_LINE_END = r"[\r\n]|(?<!\\)\\+[nr]"
_PLAIN_STOP = re.compile(r"\Z|[|;&)#]|" + _LINE_END)
_SEGMENT_END = re.compile(_LINE_END)
# A JSON string as written, or a Python one as repr and print(dict) write it in single quotes: from a quote no
# backslash escapes to the next such quote of its kind on its line. Its body is group 1 or 2.
# A quote right after a word's letter or digit is its apostrophe ("Couldn't", "it's", "users'"), never a Python
# string's opening quote, as repr writes none there; a string's prefix (b'...', rb'...') still opens one.
_STRING_LITERAL = re.compile(r'''(?<!\\)(?:"((?:[^"\\\r\n]|\\.)*)"'''
                             r'''|(?:(?<!\w)|(?<=(?<!\w)[bBrRuUfF])|(?<=(?<!\w)[bBrRuUfF]{2}))'((?:[^'\\\r\n]|\\.)*)')''')
# An escape in a string's body, as JSON or Python writes one.
_ESCAPE = re.compile(r"\\(?:u([dD][89abAB][0-9a-fA-F]{2})\\u([dD][c-fC-F][0-9a-fA-F]{2})"
                     r"|u([0-9a-fA-F]{4})|U([0-9a-fA-F]{8})|x([0-9a-fA-F]{2})|(.))", re.S)
_SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "a": "\a", "0": "\0"}


def _decoded(text, start, end):
    """text[start:end], a string's body, decoded as JSON or Python escape one: (the decoded text, where each of its
    characters starts in `text`, where each ends)."""
    out, starts, ends, at = [], [], [], start
    for m in _ESCAPE.finditer(text, start, end):
        if m.start() > at:
            out.append(text[at:m.start()])
            starts.extend(range(at, m.start()))
            ends.extend(range(at + 1, m.start() + 1))
        if m.group(1):  # a surrogate pair, one character past U+FFFF
            high, low = int(m.group(1), 16), int(m.group(2), 16)
            out.append(chr(0x10000 + ((high - 0xD800) << 10) + (low - 0xDC00)))
        else:
            code = m.group(3) or m.group(4) or m.group(5)
            value = int(code, 16) if code else None
            out.append(chr(value) if value is not None and value < 0x110000
                       else _SIMPLE_ESCAPES.get(m.group(6), m.group(6) or ""))
        starts.append(m.start())
        ends.append(m.end())
        at = m.end()
    if at < end:
        out.append(text[at:end])
        starts.extend(range(at, end))
        ends.extend(range(at + 1, end + 1))
    return "".join(out), starts, ends


def _back_to(starts, ends, spans):
    """Spans of a decoded text, as the stretches of the text it was decoded from that their characters came from."""
    return [(starts[a], ends[b - 1]) for a, b in spans if b > a]


def _literals(text):
    """The JSON strings `text` holds as written, as (start, end) stretches in order, quotes included."""
    return [(m.start(), m.end()) for m in _STRING_LITERAL.finditer(text)]


def _unread_flag_edits(text, line_pattern, flag, always, done, literals=()):
    """The fail-closed rule, for one of this PR's rows of COMMAND_FLAGS: where a command's line holds a part kb cannot
    read plainly (_PLAIN_ARGUMENTS), as shell grammar it does not follow ("$'team\\'s cert'", "<(...)", an escaped quote
    or a nested substitution escaped twice), the first password its flag finds there that no reading masked (`done`,
    where its value starts) is masked from the value to the end of that line: kb cannot tell where a part it does not
    read ends, so a pipe or ";" there may be quoted. Quotes that open right at the value are the value's own, and the
    mask covers what they hold. A JSON string of `literals` that holds the password and more is the command's own,
    left for that string read decoded (_container_edits), where its end bounds the mask, or another's, which the
    command's line does not reach; either way the mask never leaves the string. Elsewhere it runs to the line's end."""
    edits, covered = [], 0
    for line in line_pattern.finditer(text):
        if line.start() < covered:
            continue
        begin = _past_closing_quote(text, covered, line.start(), line.end())
        plain = _PLAIN_ARGUMENTS.match(text, begin).end()
        if _PLAIN_STOP.match(text, plain):
            covered = plain
            continue
        stop = _SEGMENT_END.search(text, plain)
        covered = end = stop.start() if stop else len(text)
        for m in _flag_matches(text, flag, max(begin, plain - 1), end):
            if m.start(2) in done or text.startswith("[masked]", m.start(2)) or not _flag_value_counts(m, always, True):
                continue
            held = _holding(literals, m.start(2))
            if held is not None and held[0] != m.start(2) - 1:
                break  # a JSON string holds the password and more: the command's own, read decoded, or another's
            stop_at = held[1] - 1 if held is not None else end  # quotes that open at the value: mask what they hold
            edits.append((m.start(2), len(text[:min(end, stop_at)].rstrip(" \t")), "[masked]"))
            break
    return edits


def _command_spans(text, literals, unread_only=False):
    """What COMMAND_FLAGS' rows mask in `text`, the fail-closed rule with them, as (start, end) spans: all of them, or
    with `unread_only` only the fail-closed rule's, as the pass after reads the rest of a text as written."""
    spans = []
    for n, (line, flag, always) in enumerate(_COMMANDS):
        unread = n >= _MAIN_COMMANDS and always != "field"
        found = _command_password_edits(text, line, flag, always, n >= _MAIN_COMMANDS) \
            if unread or not unread_only else []
        if unread:
            more = _unread_flag_edits(text, line, flag, always, {start for start, _, _ in found}, literals)
            found = more if unread_only else found + more
        spans += [(start, end) for start, end, _ in found if not text.startswith("[masked]", start)]
    return spans


@functools.lru_cache(maxsize=1024)
def _listed_flag_values(items):
    """_flag_values of a list of strings in text, which each of mask()'s passes reads again."""
    return _flag_values(items)


# A list or a tuple of strings as JSON or Python writes one, and one of its strings.
_QUOTED_ITEM = r"""(?:"(?:[^"\\\r\n]|\\.)*"|'(?:[^'\\\r\n]|\\.)*')"""
_ARGUMENT_LIST = re.compile(rf"[\[(]\s*{_QUOTED_ITEM}(?:\s*,\s*{_QUOTED_ITEM})+\s*,?\s*[\])]")
_ARGUMENT = re.compile(r""""((?:[^"\\\r\n]|\\.)*)"|'((?:[^'\\\r\n]|\\.)*)'""")
_NEVER = re.compile(r"(?!)")


def _unescaped(levels):
    """What a view of a command leaves out to read it `levels` times unescaped as a string unescapes: the characters
    a reader does not see, and of each run of backslashes before a quote (a double quote JSON escapes, either quote
    Python's repr does), those the unescaping takes: each time, a run of k keeps k // 2, so a quote escaped twice
    (three backslashes) is escaped once (one)."""
    if not levels:
        return re.compile(f"(?:{INVISIBLE.pattern})+")
    takes = []
    for k in range(1, 32):
        kept = k
        for _ in range(levels):
            kept //= 2  # (k - 1) // 2 for an escaped quote, k // 2 for an escaped backslash before a closing one
        takes.append(rf"\\{{{k - kept}}}(?=\\{{{kept}}}[\"'])" if kept else rf"\\{{{k}}}(?=[\"'])")
    return re.compile(rf"(?:{INVISIBLE.pattern})+|(?<!\\)(?:{'|'.join(reversed(takes))})")


# The views of a command, deepest first: unescaped four times down to as written.
_UNESCAPED = [_unescaped(levels) for levels in (4, 3, 2, 1, 0)]
_QUOTE_ESCAPE = re.compile(r'(?<!\\)\\+(?=["'"'"'])')


def _masked_parts(decoded, masked):
    """Where in `decoded`, an item as written, `masked`, the item as _flag_values masks it, hides something: the
    stretches each "[masked]" stands for, the text around them kept as it is. None where they do not line up, as
    when the reading changed the item otherwise; then the whole item is masked, fail-closed."""
    pieces = masked.split("[masked]")
    if len(pieces) == 1:
        return []
    if not decoded.startswith(pieces[0]) or not decoded.endswith(pieces[-1]):
        return None
    parts, at, last = [], len(pieces[0]), len(decoded) - len(pieces[-1])
    for piece in pieces[1:-1]:
        found = decoded.find(piece, at + 1, last)
        if found < 0:
            return None
        parts.append((at, found))
        at = found + len(piece)
    if last <= at:
        return None
    parts.append((at, last))
    return parts


# A run of digits, with single underscores between them as Python allows ("9_999" is 9999), long enough that
# str-to-int is slow on Python 3.9 (no digit limit there), or that no real value holds: a span with one is left for
# the fallback, not parsed. It is looked for only where a run starts, so each run is read once. A number written in
# hexadecimal, octal or binary parses in time in proportion, so its value is held to the same bound once parsed
# (_LONG_NUMBER): past it, printing it beside its field's name, as mask_all does, raises ValueError past Python's
# digit limit.
_DIGIT_RUN = re.compile(r"(?<![0-9])(?<![0-9]_)[0-9](?:_?[0-9]){999,}")
_LONG_NUMBER = 10 ** 999
# A span longer than 1 MiB, the bar's item size, or nested past _CONTAINER_DEPTH, is left for the fallback.
_CONTAINER_MAX = 1 << 20
_CONTAINER_DEPTH = 100
_CLOSERS = {"[": "]", "{": "}", "(": ")"}


_BRACKET_OR_QUOTE = re.compile(r"[\[\]{}()\"']")
_OPENER = re.compile(r"[\[{(]")
_BACKSLASHES = re.compile(r"\\*")


class _Brackets:
    """The balanced bracket spans of one text, each found once. A span opened at text[start] ends at its closer, one
    past it: brackets inside quotes do not count, a backslash in quotes takes the next character with it, and a span
    over _CONTAINER_MAX long, nested past _CONTAINER_DEPTH (counted here, before any parser sees it) or malformed is
    None. A quote's end and the next bracket outside quotes are looked up, and what follows a position outside quotes
    is read once whichever bracket's span reaches it, so finding a text's spans costs time in proportion to it however
    its quotes and brackets interleave, as when every bracket opens inside an earlier one's quotes."""

    def __init__(self, text):
        self.text, self.ends, self.events, self.walks = text, None, {}, {}

    def quote_end(self, at, quote):
        """One past the quote that closes a quote whose text starts at `at`, or None."""
        text = self.text
        if self.ends is None:  # each quote a backslash run of even length precedes, where a quote it is not in may end
            self.ends = {q: [m.end() - 1 for m in re.finditer(r"(?<!\\)(?:\\\\)*" + q, text)] for q in "\"'"}
        run = _BACKSLASHES.match(text, at).end()
        if run >= len(text):
            return None
        if (run - at) % 2 == 0 and text[run] == quote:
            return run + 1
        ends = self.ends[quote]  # past the backslashes at `at` and the character they take or leave, any such quote
        k = bisect.bisect_left(ends, run + 1)
        return ends[k] + 1 if k < len(ends) else None

    def event(self, at):
        """Where the next bracket outside quotes is, read from `at` outside quotes, or None."""
        events, text, seen, found = self.events, self.text, [], None
        while at is not None:
            if at in events:
                found = events[at]
                break
            seen.append(at)
            match = _BRACKET_OR_QUOTE.search(text, at)
            if match is None:
                break
            if match.group() not in "\"'":
                found = match.start()
                break
            at = self.quote_end(match.end(), match.group())
        for position in seen:
            events[position] = found
        return found

    def walk(self, at):
        """From `at`, outside quotes, the closer that ends the bracket level `at` is in, past the spans opened on that
        level: (where it is, how deep those spans nest), or None where one of them is malformed or nothing closes it."""
        walks, text, levels, child = self.walks, self.text, [[at, [], None]], None
        while True:
            level = levels[-1]
            at, seen, opener = level
            if opener is not None:  # the span opened at `opener` was read: it must close with its own closer
                level[2] = None
                if child is not None and text[child[0]] == _CLOSERS[text[opener]]:
                    seen.append((at, 1 + child[1]))
                    level[0] = child[0] + 1
                    continue
                found = None
            elif at in walks:
                found = walks[at]
            else:
                event = self.event(at)
                if event is not None and text[event] in _CLOSERS:
                    level[2] = event
                    levels.append([event + 1, [], None])
                    continue
                found = None if event is None else (event, 0)
            walks[at] = found
            deepest = found[1] if found else 0
            for position, depth in reversed(seen):
                deepest = max(deepest, depth)
                walks[position] = None if found is None else (found[0], deepest)
            found = None if found is None else (found[0], deepest)  # read from where this level began
            levels.pop()
            if not levels:
                return found
            child = found

    def depth(self, start):
        """How deep the span opened at text[start], which match() found, nests: 1 for a span with none inside."""
        found = self.walks.get(start + 1)
        return found[1] + 1 if found else 1

    def match(self, start):
        """Where the balanced span opened at text[start] ends, one past its closer, or None."""
        found = self.walk(start + 1)
        if found is None or self.text[found[0]] != _CLOSERS[self.text[start]] or found[1] + 1 > _CONTAINER_DEPTH:
            return None
        end = found[0] + 1
        return end if end <= min(len(self.text), start + _CONTAINER_MAX) else None


def _containers(text, i=0, n=None, brackets=None):
    """The top-level balanced bracket spans text[i:n] holds, in order, as (start, end), skipping brackets inside
    quotes; `brackets`, the text's _Brackets, shares what one reading found with the next."""
    n = len(text) if n is None else n
    brackets = brackets or _Brackets(text)
    while i < n:
        opener = _OPENER.search(text, i, n)
        if opener is None:
            return
        end = brackets.match(opener.start())
        if end is not None:
            yield opener.start(), end
            i = end
        else:
            i = opener.start() + 1


def _parse_container(source):
    """`source`, a bracketed span, parsed as a list, dict or tuple by json.loads or, failing that, Python's
    ast.literal_eval, or None where it does not parse to one of those or holds a number of 1,000 digits or more in any
    base (_long_number), or more than 64 keys of one dict or members of one set that share one hash (_literal_eval).
    The size, depth and digit guards run before this, and every parser error falls back to None:
    TypeError too, which ast.literal_eval raises for a key or a set member that is a list or a dict ({[]: "keytool"}),
    and OverflowError, which it raises adding an integer too large for a float to an imaginary number (9...9+1j)."""
    for parse in (json.loads, _literal_eval):
        try:
            value = parse(source)
        except (ValueError, SyntaxError, TypeError, OverflowError, RecursionError, MemoryError):
            continue
        if isinstance(value, (list, dict, tuple)):
            return None if _long_number(value) else value
    return None


def _long_number(value):
    """Whether a parsed value holds, at any depth, in a key or a member, an integer of 1,000 digits or more
    (_LONG_NUMBER). Walked with a stack: the depth guard holds the nesting, but a walk needs no recursion."""
    todo = [value]
    while todo:
        item = todo.pop()
        if isinstance(item, dict):
            todo += item.keys()
            todo += item.values()
        elif isinstance(item, (list, tuple, set, frozenset)):
            todo += item
        elif isinstance(item, int) and not isinstance(item, bool) and abs(item) >= _LONG_NUMBER:
            return True
    return False


# Keys of one dict, or members of one set, that may share one hash in a literal read whole (_literal_eval).
_SHARED_HASH = 64


def _literal_eval(source):
    """`source`, a bracketed span, as ast.literal_eval reads it, parsed once; refused (ValueError) where more than
    _SHARED_HASH keys of one dict or members of one set share one hash, as multiples of sys.hash_info.modulus or
    tuples of them do: Python builds such a dict or set, and each reading of it, in time in the square of their
    number. A string's hash is drawn afresh in each process, so no text can choose one."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tree = ast.parse(source, mode="eval")
        for node in ast.walk(tree):
            items = node.keys if isinstance(node, ast.Dict) else node.elts if isinstance(node, ast.Set) else ()
            if len(items) > _SHARED_HASH:
                shared = {}
                for item in items:
                    if item is None or not _hashable(item):
                        raise TypeError("a key or a member no literal can hash")  # as ast.literal_eval raises
                    key = hash(ast.literal_eval(item))  # of a number, a string, bytes or a tuple of them
                    shared[key] = shared.get(key, 0) + 1
                    if shared[key] > _SHARED_HASH:
                        raise ValueError("more keys sharing one hash than a literal read whole may hold")
        return ast.literal_eval(tree)


def _hashable(node):
    """Whether a literal's node can be a dict's key or a set's member: no list, dict or set, at any depth of the tuples
    it is in, so the check reads no part of the literal more than once."""
    todo = [node]
    while todo:
        node = todo.pop()
        if isinstance(node, ast.Tuple):
            todo += node.elts
        elif isinstance(node, (ast.List, ast.Dict, ast.Set, ast.Call)):
            return False
    return True


def _as_text(value):
    """`value` with each byte string read as text, one character a byte (latin-1), as its repr's escapes decode: a list
    of arguments may hold bytes ([b"keytool", b"-storepass", b"..."]), which mask_all reads only as text."""
    if isinstance(value, bytes):
        return value.decode("latin-1")
    if isinstance(value, dict):
        return {_as_text(k): _as_text(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_as_text(item) for item in value)
    return value


def _number_run(text, start, end, literals):
    """Whether text[start:end] holds a run of digits _DIGIT_RUN finds outside its string literals: a number long enough
    that str-to-int is slow on Python 3.9. Digits inside a string ("stdout": "999...") are text, which no parser
    converts."""
    inside = [(m.start(), m.end()) for m in literals]
    for run in _DIGIT_RUN.finditer(text, start, end):
        if _holding(inside, run.start()) is None:
            return True
    return False


def _leaf_pairs(value, masked):
    """(a string of `value`, how `masked` masks it) for each string in `value`, in the order _STRING_LITERAL finds
    them in the source: a dict's keys and values in turn, a list's or tuple's items in turn, a string itself. `masked`
    is mask_all(value), which keeps the order and count of keys and items, so the two walk together."""
    if isinstance(value, str):
        yield value, masked if isinstance(masked, str) else value
    elif isinstance(value, dict) and isinstance(masked, dict):
        for (k, v), (mk, mv) in zip(value.items(), masked.items()):
            if isinstance(k, str):
                yield k, mk if isinstance(mk, str) else k
            yield from _leaf_pairs(v, mv)
    elif isinstance(value, (list, tuple)) and isinstance(masked, (list, tuple)):
        for item, mitem in zip(value, masked):
            yield from _leaf_pairs(item, mitem)


def _read_whole(text, start, end, depth, nest):
    """The masks text[start:end], a bracketed span, takes read whole, in `text`: parsed (_parse_container), its strings
    walked and read as mask_all reads them, a number printed beside its field's name among them, and each mask placed
    back in its string literal. None where the span is not read whole, so the spans inside it are read instead and
    the fail-closed reading runs over it: where it does not parse, its strings do not match the literals the scan
    finds, a mask will not place, or any step raises. This reading only narrows the masks this PR adds, and the mask
    with it switched off hides all it hides (_unwrapped_hidden), so a span an error leaves unread is read as main reads
    it, and a kind of error no guard here foresaw drops no item. KeyboardInterrupt and SystemExit, no Exception, still
    end the run. `depth` and `nest` are what the readings around this one have used (_READ_DEPTH)."""
    try:
        literals = list(_STRING_LITERAL.finditer(text, start, end))
        value = None if _number_run(text, start, end, literals) else _parse_container(text[start:end])
        if value is None:
            return None
        value = _as_text(value)
        token = _READ_DEPTH.set((depth + 1, nest))
        try:
            pairs = list(_leaf_pairs(value, mask_all(value)))
        finally:
            _READ_DEPTH.reset(token)
        if len(literals) != len(pairs):
            return None
        found = []
        for literal, (orig, masked) in zip(literals, pairs):
            if masked == orig:
                continue
            body = 1 if literal.group(1) is not None else 2
            decoded, starts, ends = _decoded(text, literal.start(body), literal.end(body))
            parts = _masked_parts(decoded, masked) if decoded == orig else None
            if parts is None:  # the literal scan disagrees with the parse, or the mask will not place
                return None
            found += _back_to(starts, ends, parts)
        return found
    except Exception:  # any error in reading the span whole, RecursionError among them: read as a span that does not parse
        return None


# What the parse-whole readings this one is inside have used, as mask_all reads a parsed container's strings with
# mask() again and so this reading again: (the string levels they decoded, the bracket depth they hold). Past
# _READ_LEVELS levels, or _CONTAINER_DEPTH brackets, in all, a span is not parsed, and the fail-closed reading runs over
# it, so strings that each hold a deeply nested list neither run Python's recursion out nor cost time past proportion.
# A string is still decoded and its lines read down to _STRING_LEVELS, which costs a pass over each level's text.
_READ_DEPTH = contextvars.ContextVar("kb_mask_read_depth", default=(0, 0))
_READ_LEVELS = 8
_STRING_LEVELS = 16


def _container_edits(text, depth=0):
    """_container_edits_at, with what the readings around this one have used (_READ_DEPTH)."""
    levels, nest = _READ_DEPTH.get()
    return _container_edits_at(text, depth + levels, nest)


@functools.lru_cache(maxsize=256)
def _container_edits_at(text, depth, nest):
    """(the top-level spans of `text` read here, the spans that mask what they hold), as (start, end) in `text`. A
    bracket span (a list, dict or tuple) is parsed whole and read as mask_all reads it; a string is decoded and read a
    level down, as a nested container or a command line, so the text's layers peel exactly as json.loads and ast do.
    A span whose parse fails or whose masks cannot be placed is not read here, so the fail-closed reading still runs
    over it. Read once per text (lru_cache), so no layer is masked twice."""
    if depth > _STRING_LEVELS:
        return (), ()  # deeper than any real nesting; a cap keeps a crafted chain bounded
    spans, masks = [], []
    # A span that does not parse, or whose masks will not place, is not read whole, but the spans inside it are, as a
    # list in a call that is no literal ("Command (argv=[...]) failed") is. What the parsers and these scans read in
    # all is held to a few times the text's length, so a deep nest of spans that never parse costs time in proportion.
    brackets = _Brackets(text)
    todo = list(_containers(text, brackets=brackets))[::-1] if depth <= _READ_LEVELS else []
    budget = 8 * len(text) + (1 << 20)
    while todo:
        start, end = todo.pop()
        if not _ANY_COMMAND.search(text, start, end) or budget < 0:
            continue
        budget -= 2 * (end - start)
        if nest + brackets.depth(start) > _CONTAINER_DEPTH:
            continue  # past the depth guard with the containers around it: read fail-closed, as text
        found = _read_whole(text, start, end, depth, nest + brackets.depth(start))
        if found is None:
            todo += list(_containers(text, start + 1, end - 1, brackets))[::-1]
            continue
        spans.append((start, end))
        masks += found
    spans.sort()
    for literal in _STRING_LITERAL.finditer(text):
        if _holding(spans, literal.start()) is not None:  # inside a parsed container, read there
            continue
        body = 1 if literal.group(1) is not None else 2
        if not _ANY_COMMAND.search(text, literal.start(body), literal.end(body)):
            continue
        decoded, starts, ends = _decoded(text, literal.start(body), literal.end(body))
        if not _ANY_COMMAND.search(decoded):
            continue
        inner_spans, inner_masks = _container_edits_at(decoded, depth + 1, nest)
        # as at depth 0, a line read across a container parsed in the string reads it wrongly; the container's stands
        found = [span for span in _command_spans(decoded, _literals(decoded))
                 if _holding(inner_spans, span[0]) is None] + list(inner_masks)
        spans.append((literal.start(), literal.end()))
        masks += _back_to(starts, ends, found)
    spans.sort()
    return tuple(spans), tuple(masks)



def _view_edits(text, view, split, base):
    """_wrapping_edits' reading of one view, `split` read from `view` or None: (the lists of a command's arguments it
    reads, the spans that mask what they hold, the spans that mask what the lines read hold), in the text as written.
    A view only says where to mask: every span is mapped back to the text as written, and no view's text is kept."""
    read = split.view if split else view
    if not _ANY_COMMAND.search(read):
        return [], [], []
    if base:  # the containers and strings the text holds, read whole, and the fail-closed rule outside them
        lists, listed = _container_edits(read)
        lined = _command_spans(read, _literals(read), unread_only=read == text)
    else:  # an unescaped view only recognises a command (colour in its name, an escaped quote), never a container
        lists, listed, lined = [], [], []
        for n, (line, flag, always) in enumerate(_COMMANDS):
            lined += [(start, end) for start, end, _ in _command_password_edits(read, line, flag, always,
                                                                                n >= _MAIN_COMMANDS)
                      if not read.startswith("[masked]", start)]
    lists = list(lists)
    lined = [span for span in lined if _holding(lists, span[0]) is None]  # inside a span read whole: read exactly
    if not split:
        return lists, list(listed), lined
    # Back to the text as written, a value with the colour codes around it, as its reading masks one.
    unwrapped = {i for m in _QUOTE_ESCAPE.finditer(view) for i in range(m.start(), m.end())}

    def back(start, end):
        start, end = split.raw(start, end)
        while start - 1 in split.removed and start - 1 not in unwrapped:
            start -= 1
        while end in split.removed and end not in unwrapped:
            end += 1
        return start, end

    # A container's masks are exact decoded positions; map them straight, without back()'s extension into removed
    # characters, which is for a value abutting a colour code, not a string's own content.
    return ([split.raw(*span) for span in lists], [split.raw(*span) for span in listed], [back(*span) for span in lined])


def _wrapping_edits(text):
    """Each named command's password the readings before leave, as an edit that masks it, read in views of the text:
    with the shell's continuation marks' line breaks blanked, so a command over several lines reads as one
    (_CONTINUATION), and without the characters a reader does not see and the backslashes before a double quote at any
    depth (_UNESCAPED), so a command coloured by grep, even inside its name, or held in JSON once or many times, reads
    as it was written. A view is read for each depth JSON holds the text at, as written up to four times unescaped,
    since a text holds strings at many depths and only one level at a time keeps each string's own quotes
    (_view_edits). In each, every row of COMMAND_FLAGS reads it, and a list or a tuple of a command's arguments, as
    JSON or Python writes one, is read as mask_all reads the list (_flag_values). The fail-closed rule reads the text
    as written and each JSON string in it decoded as its own text (_container_edits). A view only says where to mask:
    each edit puts "[masked]" over a stretch of the text as written, which keeps every other character."""
    if not (_ANY_COMMAND.search(text) or INVISIBLE.search(text)):
        return []
    view = _CONTINUATION.sub(lambda m: " " * len(m.group()), text)
    lists, listed, lined = [], [], []
    deepest = max((len(m.group()) for m in _QUOTE_ESCAPE.finditer(view)), default=0)
    for levels, runs in zip((4, 3, 2, 1, 0), _UNESCAPED):
        if levels and deepest < 2 ** (levels - 1):
            continue  # no quote escaped so deep: the view is the one a level up
        for found, more in zip((lists, listed, lined), _view_edits(text, view, _Split(view, runs, _NEVER)
                                                                   if runs.search(view) else None, levels == 0)):
            found += more
    # Where a list of arguments is read, it is read exactly; a line read across one, in a view that unescaped its
    # items' quotes with the quotes they hold, reads it wrongly.
    stretches = []  # the lists read, in each view, as stretches that do not overlap
    for start, end in sorted(lists):
        if stretches and start <= stretches[-1][1]:
            stretches[-1] = (stretches[-1][0], max(stretches[-1][1], end))
        else:
            stretches.append((start, end))
    spans = listed + [span for span in lined if _holding(stretches, span[0]) is None]
    merged = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        elif start < end:
            merged.append((start, end))
    return [(start, end, "[masked]") for start, end in merged]


# Where a name is part of an AWS resource's name, as "secretsmanager" and "secret" are in
# "arn:aws:secretsmanager:<region>:<account>:secret:<name>".
_ARN_HEAD = re.compile(r"(?<![A-Za-z0-9])arn:aws(?:-[a-z]+)*:(?:[A-Za-z0-9-]*:){0,4}\Z")
# A file's name of words joined by "-" or "_", with a version or a number among them: "reset-flow-v2.md".
_FILE_NAME = re.compile(r"[a-z]{2,}(?:[-_](?:[a-z]{2,}|v?\d{1,4}))+\.[A-Za-z][A-Za-z0-9]{0,4}")


def _keyed_span(m):
    """Where a named value's secret is, the whole value, or None when it reads as none: nor in an AWS resource's name,
    nor in a file's name of words after a "/", as "notes/password=reset-flow-v2.md" is."""
    name, sep, value = m.groups()
    if not _looks_secret(name, sep, value):
        return None
    start = m.start(1)
    if _ARN_HEAD.search(m.string, max(0, start - 128), start):
        return None
    if start and m.string[start - 1] == "/" and _FILE_NAME.fullmatch(value):
        return None
    return m.start(3), m.end(3)


# A path, as a working directory names one: from the root, home or here, or a Windows drive or variable.
_PATH_VALUE = re.compile(r"(?:/|~|\.\.?[\\/]|[A-Za-z]:[\\/]|%[A-Za-z_]+%)")


def _short_name_secret(value):
    """Whether a value after "pass" or "pwd" alone is a secret: a credential's shape, and no path, read without the
    characters a reader does not see, such as a colour code. Either word alone names much else, a working directory or
    a compiler's pass."""
    return any(not _PATH_VALUE.match(seen) and _password_shaped(seen) for seen in _seen_views(value))


def _short_password_span(m):
    """Where "pass" or "pwd"'s value is, when it is a secret (_short_name_secret) next to a user's name."""
    return _keyed_span(m) if _short_name_secret(m.group(3)) and _beside_a_user(m) else None


# A user's name as a field's name, which a login gives next to its password: "user", "Uid", "User ID", "login".
_USER_NAME = r"(?:user(?:[ _-]?(?:name|id))?|uid|login|e-?mail|account)"
_USER_KEY = re.compile(_USER_NAME, re.I)
# The field right before a name, a user's name and its value with nothing after it but a separator, space and a quote
# ("Uid=sa;Pwd", "user: 'me@example.com', pass", '"user": "sa", "pwd'); and the field right after a value.
# Between the two, at most one line break, plain or escaped: a blank line parts them.
_ONE_BREAK = r"[ \t]*(?:(?:\r?\n|(?:\\r)?\\n)[ \t]*)?"
_USER_BEFORE = re.compile(rf"""(?i)(?<![A-Za-z0-9]){_USER_NAME}["']?[ \t]*(?:[:=]|=>)[ \t]*"""
                          rf"""(?:"[^"\n]{{0,256}}"|'[^'\n]{{0,256}}'|(?:[^\s,;&'"\\]|\\(?![nr])){{1,256}})[ \t]*[,;&]?{_ONE_BREAK}["']?\Z""")
_USER_AFTER = re.compile(rf"""(?i)\\?["']?[ \t]*[,;&]?{_ONE_BREAK}["']?{_USER_NAME}["']?[ \t]*(?:[:=]|=>)""")


def _beside_a_user(m):
    """Whether a named value's field, `m` (name, separator, value), is the one right after or right before a user's
    name given as a field, as a login gives both; not a word away, as "user=ci compiler pass=..." is."""
    at = m.start(1)
    return bool(_USER_BEFORE.search(m.string, max(0, at - 300), at) or _USER_AFTER.match(m.string, m.end()))


# A name that ends in a secret's word, so names the secret itself: "db_password", "Ppasswd", "API_KEY", "authToken".
_ENDS_IN_A_SECRET = re.compile(r"(?i)(?:passw(?:or)?d|passcode|passphrase|pwd|pass|secret|token|key|credentials?)s?$")
# A password's name that names something about it rather than it: its policy, hint, reset, length, rules, file.
_ABOUT_A_PASSWORD = re.compile(
    r"(?i)(?:^|[_.-]|(?-i:(?<=[a-z])(?=[A-Z])))(?:polic(?:y|ies)|hints?|reset|length|min|max|rules?|requirements?|"
    r"strength|expir\w*|change[ds]?|file|path|url|name|type|id|prompt|label|field|regex|pattern|format|hash\w*|"
    r"validat\w*)s?(?=$|[_.-]|(?-i:[A-Z]))")


def _password_span(m):
    """_keyed_span, for a pattern that reads only a password's value whole. A name about a password, as
    "password_policy" or "PASSWORD_HINT" is, is read as KEYED_SECRET reads it."""
    name = m.group(1)
    return _keyed_span(m) if PASSWORD_NAME.search(name) and not _ABOUT_A_PASSWORD.search(name) else None


@functools.lru_cache(maxsize=8)
def _quote_marks(text, quote):
    """Where `text` holds `quote` with no backslash escaping it, in order, and where its lines begin, after a line break
    raw or escaped as JSON holds one."""
    marks = [m.end() - 1 for m in re.finditer(r"(?<!\\)(?:\\\\)*" + quote, text)]
    return marks, [m.end() for m in re.finditer(r"\n|(?<!\\)\\n", text)]


# A string's prefix before its opening quote: Python's r, b, u, f and t and their pairs (rb, fr), C and C++'s L, u8 and
# R, C#'s @ and $, and Rust's r#. It counts where an opening quote would, after a bracket, a comma, "=", ":", a space or
# the line's start, and not where a single quote stands before a word's ending ("B's", "T'd", "U're") and a space or a
# mark, as an apostrophe does; a string's own word ("Re-enter"), or a double quote's ("Re enter"), is no ending.
_STRING_PREFIX = re.compile(r"[rRbBuUfFtT]{1,2}|u8R?|LR?|\$@|@\$|[@$]|b?r#{1,3}")
_WORD_ENDING = re.compile(r"(?i)(?:s|t|d|m|ll|re|ve)(?=[\s.,;!?]|\Z)")


def _opens_a_string(text, at, line):
    """Whether the quote at `at`, on the line starting at `line`, opens a string: at the line's start, or after a
    bracket, a comma, "=", ":" or a space, or after a string's prefix (_STRING_PREFIX) there."""
    if at == line or text[at - 1] in " \t([{,=:":
        return True
    if text[at] == "'" and _WORD_ENDING.match(text, at + 1):
        return False
    for start in range(at - 1, max(line, at - 5) - 1, -1):
        if _STRING_PREFIX.fullmatch(text, start, at) and (start == line or text[start - 1] in " \t([{,=:"):
            return True
    return False


@functools.lru_cache(maxsize=64)
def _typing_brackets(text):
    """Where each typing call's own brackets stand in code (_CODE_FILL_CALL, _CODE_TYPING_CALL), as the typing reader
    reads them: the "(" after its name, and the bracket that closes it where one does, in order."""
    if not _CODE_HINT.search(text):
        return ()
    tokens, _, stretches, _ = _code_tokens(text)
    stretches = _Stretches(text, stretches)
    starts, found = [t[1] for t in tokens], []
    for pattern in (_CODE_FILL_CALL, _CODE_TYPING_CALL):
        for call in pattern.finditer(text):
            k = bisect.bisect_left(starts, call.end() - 1)
            if k < len(tokens) and tokens[k][0] == "(" and tokens[k][1] == call.end() - 1:
                _, close = _call_span(tokens, k, stretches.holding(tokens[k][1])[1])
                found += [tokens[k][1]] + ([tokens[close][1]] if close is not None else [])
    return tuple(sorted(found))


def _crosses_typing_bracket(text, start, end):
    """Whether text[start:end] holds one of a typing call's own brackets (_typing_brackets)."""
    found = _typing_brackets(text)
    k = bisect.bisect_left(found, start)
    return k < len(found) and found[k] < end


def _quoted_password_span(m):
    """_password_span, but not where the value runs across a typing call's own bracket, which the typing reader reads
    (the coordinator's rule after rounds 48 to 51: a label's ":" and closing quote, as in
    page.get_by_label(r'Re enter password:').fill(...), open no value, since a password holds no call's bracket and
    ").fill(" is code), and not for a name inside a string that the quote after its ":" or "=" closes: in
    getByLabel("Password:").fill(...) or get_by_label(r"Password:").fill(...) the name is a label's, and that quote
    opens no value. The name is in such a string when the nearest quote of its kind before it on its line opens one
    (_opens_a_string); one after a letter, a digit or other punctuation closes a string or is an apostrophe ("it's",
    "users'"). A name whose own quote closes before the ":" ('"password": "..."') is read."""
    separator, text = m.group(2), m.string
    if _crosses_typing_bracket(text, m.start(3), m.end(3)):
        return None
    quote = separator[-1]
    if not separator.startswith(quote):
        marks, lines = _quote_marks(text, quote)
        at, k = m.start(1), bisect.bisect_left(marks, m.start(1))
        line = lines[bisect.bisect_right(lines, at) - 1] if lines and lines[0] <= at else 0
        if k and marks[k - 1] >= line and _opens_a_string(text, marks[k - 1], line):
            return None
    return _password_span(m)


def _line_password_span(m):
    """Where a password's line value is, when the line holds more past where _VALUE would end it."""
    return _password_span(m) if re.search(r"[,;<>)\]}].", m.group(3)) else None


def _app_password_span(m):
    """_password_span for an app password's four groups, but where three or four are common words ("password: this
    will work fine", "password: read from smtp logs"), read as main reads them: a random app password's groups hold
    three such words about once in a million times."""
    if all(sum(group in _COMMON_WORDS for group in seen.split()) >= 3 for seen in _seen_views(m.group(3))):
        return None
    return _password_span(m)


def _xml_span(m):
    """Where an XML element's secret is: _keyed_span, but for an element about a password (_ABOUT_A_PASSWORD), and for
    one not named for a password, a value without a credential's shape: an element named "token" or "secret" holds a
    word as often as a secret ("<tokens><token>internationalization</token></tokens>")."""
    name = m.group(1)
    if _ABOUT_A_PASSWORD.search(name) or not _XML_PASSWORD.search(name) and \
            not any(map(_password_shaped, _seen_views(m.group(3)))):
        return None
    return _keyed_span(m)


# The schemes an Authorization header names before its credential, as IANA lists them and services use them.
AUTH_SCHEMES = frozenset(("basic", "bearer", "digest", "dpop", "gnap", "hoba", "mutual", "negotiate", "ntlm", "oauth",
                          "privatetoken", "scram-sha-1", "scram-sha-256", "vapid", "concealed", "aws4-hmac-sha256",
                          "token", "bot", "apikey", "api-key", "key", "mac", "hawk", "signature", "ssws",
                          "googlelogin", "kerberos", "splunk"))


_CREDENTIAL_END = re.compile(r"\\{1,7}[nrt]|\[masked")  # bounded, as _CLOSE is: a run read from every backslash is quadratic


def _credential(name, sep, word):
    """The credential in `word` after Authorization, up to an escaped line break or a placeholder already in it, as
    "Credential=" stands before an AWS key id's; or "" when that is no credential, such as a word of letters, hyphens
    and an end mark like "Organization-level"."""
    head = _CREDENTIAL_END.split(word, 1)[0]
    if not head or re.fullmatch(r"[A-Za-z]+(?:-[A-Za-z]+)*[.,;:!?]?", head) or not _looks_secret(name, sep, head):
        return ""
    return head


_HEADER_SEP = re.compile(r":|%(?:25)?3[Aa]|u003[Aa]")
# Where a header starts: at the text's start, after a space or a line break, plain or escaped, or after a quote or an
# opening mark, as a header line, a curl -H argument or a JSON key does; not inside a path or a value, as in
# "/notes:Authorization:<id>".
_HEADER_START = re.compile(rf"(?:(?<![^\s\"'`{{\[(,;|>])|{_AFTER_ESCAPE})")
# A plain word: letters with a letter past "f", so a hex run such as "dead" in a UUID is none, or capitalized words run
# together with digits after them, as a product's name is ("WindowsServer2022").
_PLAIN_WORD = re.compile(r"(?=.*[g-zG-Z])[A-Za-z][a-z]{3,}|(?:[A-Z][a-z]{2,}){2,}\d*")


def _random_run(text, shortest=12):
    """Whether `text` reads as random: `shortest` or more characters mixing letters and digits, of both cases when
    shorter than 16, and no plain word."""
    return (len(text) >= shortest and bool(re.search(r"\d", text)) and bool(re.search(r"[A-Za-z]", text))
            and (len(text) >= 16 or bool(re.search(r"[a-z]", text) and re.search(r"[A-Z]", text)))
            and not _PLAIN_WORD.fullmatch(text))


# How code's words spell: the cost of each step from a letter, or a word's start ("^"), to the next letter, or its end
# ("$"), as -10 ln P(next | this), over the words of the names in Python's standard library (tools/code_words.txt),
# each once, as tools/code_letter_pairs.py prints it. A row for each of "^a...z", two digits for each of "a...z$".
_CODE_LETTER_PAIRS = """
303425283030323727574831303236275128223035443751565696
783427286053346536704321262789327820271842464640407339
234438422651465926305319434227356431284227756459285925
226144482447532338852729414516506130372336485974538526
274543341643555921626236524529466236324036565556548413
325329274238446051675534372449384721242556434632486916
306744362927565615786728464421415925313229785262497824
386243471256433829786733433139467830294237516767785614
185249411654565024764640384420497632383341656065507618
384030323735337859896926311428386932232273406742893442
275443381954545438545443545419385438353818545454545414
344545371445375430707033593648455142244042543470547012
245343341549625619675827544422387551333337546167407020
183554391351626527814840375025238153313936596581508124
245627242334255533724039504228426052282137406477567721
453728265538446042684328291836278820303029433551686231
185041431943584431845126534924336223353037564684358422
474247423947585847475839583447425839314705475858585823
245435381647396525734541354025458939273042515563418917
405732472149473428664640495135316046311833575261437312
255237431844612923806340444729406425303540534958355417
342939452935488035808023271738266923262260808069698046
134553581045585826695850385034426940474269585869696921
194636342541543021595937462826407022274551544848597024
354726402639454527696933364736246950351545505844375317
464141413439615040727236372848187240272561484046567210
326060440638606024606049494925446044386049606060444927
"""
_PAIR_COST = {(a, b): int(row[2 * i:2 * i + 2]) / 10
              for a, row in zip("^abcdefghijklmnopqrstuvwxyz", _CODE_LETTER_PAIRS.split())
              for i, b in enumerate("abcdefghijklmnopqrstuvwxyz$")}
_CODE_SEPARATOR = re.compile(r"([-_./:])")


def _word_cost(word):
    """How unlikely `word` is as a word of code's names: the sum of its steps' costs (_CODE_LETTER_PAIRS), in nats."""
    word = "^" + word.lower() + "$"
    return sum(_PAIR_COST[a, b] for a, b in zip(word, word[1:]))


def _reads_as_code(word):
    """Whether `word` is likelier as code's words, acronyms, numbers and separators than as random characters.

    Read as camel case and snake case split it (_LABEL_WORD), each piece costs ln 2 to start, and then: a run of small
    letters, after a capital or not (ln 2), what its letters cost as a word of code (_word_cost); a run of capitals the
    least of that and an acronym's cost, a capital each (ln 26) and ln 5, and ln 20 for each past the fourth, as an
    acronym is short; a number ln 10 a digit and ln 5; a separator ln 4; any other character ln 64. Random characters
    from the alphabet the word draws on, letters and digits and "-" and "_" if it holds one of them, cost ln 62 or ln 64
    each. A random token of 20 letters and digits falls in short pieces of letters no word spells, and costs more read
    as code; "isLikelyNpxGlobal", "GNU_PROPERTY_X86_FEATURE_1_IBT" and "Uint8ClampedArray" cost less."""
    cost = 0.0
    for i, piece in enumerate(_CODE_SEPARATOR.split(word)):
        if i % 2:
            cost += math.log(4)
            continue
        covered = 0
        for run in _LABEL_WORD.findall(piece):
            covered += len(run)
            cost += math.log(2)
            if run.isdigit():
                cost += len(run) * math.log(10) + math.log(5)
            elif run.isupper() and len(run) > 1:
                acronym = len(run) * math.log(26) + math.log(5) + max(0, len(run) - 4) * math.log(20)
                cost += min(_word_cost(run), acronym)
            else:
                cost += _word_cost(run) + math.log(2)
        cost += (len(piece) - covered) * math.log(64)
    return cost < len(word) * math.log(64 if re.search(r"[^A-Za-z0-9]", word) else 62)


def _credential_shaped(word):
    """Whether a lone token reads as a credential: 12 or more characters with no whitespace, mixing letters and digits,
    not a real date or time (_date_or_time, _timestamp: any other run of digits and ":.+TZ-" with a T or a Z in it, such
    as "20261101T100000Z1234", is none), and either holding a random run of 10 or more between its separators
    ("live_<run>", a three-part token), or holding no plain word and reading as random whole (a UUID). An identifier
    with a plain word in it stays: "RFC7235-compliant", "WPA2-Enterprise". A token with no digit is one only where it
    reads as random letters (_random_letters), as a random token of letters and digits sometimes draws no digit."""
    word = word.rstrip(".,;:!?)")
    if not re.search(r"\d", word):
        return _random_letters(word)
    if len(word) < 12 or not re.search(r"[A-Za-z]", word) or re.search(r"\s", word):
        return False
    if _date_or_time(word) or _timestamp(word):
        return False
    parts = re.split(r"[-_./:]", word)
    if any(_random_run(part, 10) for part in parts):
        return True
    return not any(_PLAIN_WORD.fullmatch(part) for part in parts) and _random_run(word)


# A code identifier: words and acronyms in camel case with version numbers after them ("ConvertBF16ToF32",
# "OAuth2AuthorizationCode", "HTTPStatus200"). A digit runs straight into lower case letters only in a password
# ("Tr0ub4dor", "L4terSecret"), and a random token's letters fall in short runs.
_DIGIT_INTO_A_WORD = re.compile(r"\d[a-z]")


def _identifier_shaped(word):
    """Whether a word reads as a code identifier: no digit runs into lower case letters, at least half its letters
    stand in runs of three or more, read as camel case splits them, and, 16 characters or longer, its parts between
    separators are each in one case (_one_case_parts) or it reads more as code's words than as random characters
    (_reads_as_code), which many random tokens of letters and digits pass the first two tests without: of 2,000 of 20,
    main 33c82f6 read 317 as identifiers in "<secret>...</secret>" and showed them."""
    if _DIGIT_INTO_A_WORD.search(word):
        return False
    runs = [run for run in _LABEL_WORD.findall(word) if run.isalpha()]
    letters = sum(map(len, runs))
    if not letters or 2 * sum(len(run) for run in runs if len(run) >= 3) < letters:
        return False
    return len(word) < 16 or _one_case_parts(word) or _reads_as_code(word)


def _one_case_parts(word):
    """Whether `word` is two or more parts between separators, each in one case, as snake case and constants are
    ("x86_cpu_VPCLMULQDQ", "SN_kuznyechik_cfb"): random base64url mixes the cases in each of its parts."""
    parts = re.split(r"[-_./:]", word)
    return len(parts) > 1 and all(part.lower() == part or part.upper() == part for part in parts)


def _random_letters(word):
    """Whether a token with no digit reads as random: 16 or more letters of both cases, with "-" and "_" at most, that
    are not code's words (_reads_as_code), as a random token of letters and digits sometimes draws no digit."""
    return (len(word) >= 16 and bool(re.fullmatch(r"[A-Za-z_-]+", word)) and bool(re.search(r"[a-z]", word))
            and bool(re.search(r"[A-Z]", word)) and not _one_case_parts(word) and not _reads_as_code(word))


def _password_shaped(word):
    """_credential_shaped, but no code identifier: for the readings main's own lack, which judge a value by its shape
    alone where main has no reading at all."""
    return _credential_shaped(word) and not _identifier_shaped(word.rstrip(".,;:!?)"))


def _seen_views(value):
    """`value` without the characters a reader does not see, as this branch reads them (INVISIBLE) and as main 032391d
    does (_TODAYS_INVISIBLE), once each where they differ. A reading that judges a value by its shape asks each, and a
    value either finds a secret is one: this branch's INVISIBLE also removes a surrogate pair or a colour code escaped
    twice, so "ZD\\\\udb40\\\\udc01VVVX", a random token to main, alone would read as ZDVVVX, a name in capitals."""
    return dict.fromkeys((INVISIBLE.sub("", value), _TODAYS_INVISIBLE.sub("", value)))


def _bare_credential(word):
    """The credential-shaped token a header's value starts with, up to an escaped line break or a placeholder, and
    without a closing mark after it; or "". Judged by its shape alone, as a reader sees it, without the characters a
    reader does not see, so base64 that starts with "/" counts and "RFC7235-compli<SHY>ant" does not."""
    head = _CREDENTIAL_END.split(word, 1)[0].rstrip(".,;:!?)")
    # As this view reads it, as main 032391d does, and as main's own view does, without its own invisible characters
    # only.
    shaped = any(map(_credential_shaped, (*_seen_views(head), _MAIN_INVISIBLE_RUN.sub("", head))))
    return head if shaped else ""


def _authorization_span(m):
    """Where an Authorization header's credential is: the word after a scheme such as Bearer or AWS4-HMAC-SHA256,
    whose name stays, or, header style ("Authorization: ..."), a credential-shaped first token with no scheme before
    it; None for prose and identifiers such as "ISO-27001 compliance"."""
    name, sep, first, gap, second = m.groups()
    if first.lower() in AUTH_SCHEMES:
        credential = _credential(name, sep, second) if second else ""
        return (m.start(5), m.start(5) + len(credential)) if credential else None
    # With no scheme, header style: a credential-shaped first token, by design even where main keeps it.
    credential = _bare_credential(first) if _HEADER_SEP.search(sep) and _HEADER_START.match(m.string, m.start()) else ""
    return (m.start(3), m.start(3) + len(credential)) if credential else None


KEY_FOOTER = re.compile(_KEY_END)
_BLANK_LINE = re.compile(_BLANK)
# Characters glued to a placeholder are the rest of a secret an older kb masked a chunk at a time, or what its key
# pattern left of the next secret's name, as in "[masked]_key: value": today's patterns take every such character, so
# they never leave one glued. Such a name keeps its value masked with it. A "/" right after a placeholder starts a path
# instead, as an AWS key id's credential scope does, and stays.
_GLUED_RUN = re.compile(r"\[masked\](?:[A-Za-z0-9_.-]+[ \t]*(?:[:=]|=>)[ \t]*[\"']?" + _VALUE[1:-1]
                        + r"|[A-Za-z0-9_+=-][A-Za-z0-9_+/=-]*)")
# How every private key's body starts: DER, a SEQUENCE in long form ("MII...", "MIG...") or in short form before an
# INTEGER or a SEQUENCE ("MHcC...", "MC4C..."), or OpenSSH's "openssh-key-v1" ("b3Bl...").
_KEY_BODY_START = r"(?:MI[E-P][A-Za-z0-9+/]|M[A-H][A-Za-z0-9+/][Cw]|b3Bl)"
# The rest of a key after an older kb's placeholder, which fails closed: what an older mask left of a key, whole or in
# part, stays masked, and only base64 that decodes whole as public material is shown (_public_material). It starts
# with base64 glued to the placeholder and running to a line break, as main's key pattern leaves when it took the line
# break too; or with a line of base64 after what can stand between a line's start and its text (line breaks plain or
# escaped at any depth, quotes, "#", ">", "+", <br>, spaces, or a character a reader does not see): a key's body from
# its first line, or 36 characters or more of both cases and a digit, "+" or "/", as either part of a body split or
# re-wrapped holds. main's pattern stops at the first character that is not base64 or a space, so in escaped text, in
# code with a string a line, behind comment or quote marks and where a chunk ended between a header and its line break,
# it leaves exactly that. The line is base64 to its end, or to a quote, a backslash or a cut's "…", so a word that only
# starts like one, followed by more words, stays; a line that decodes from its start to words holds none, as a line of
# words does not, and the rest starts past such lines where the next one starts it (_encoded_words, in _key_rests). It
# runs through its footer, or to the end of its paragraph, but never into a line of ordinary words (_WORDS_LINE) or a
# JSON object's next field (_NEXT_FIELD).
# Each alternative takes a character no other does: quotes are _QUOTE's alone, as a run of them read two ways each
# took time exponential in its length.
_BEFORE_BODY = rf"(?:\\{{1,7}}[nrt]|{_QUOTE}|<br\s*/?>|[\s#>+,;|*\]])"
_LINE_OF_BASE64 = (rf"(?:{{}})[A-Za-z0-9+/=]{{{{36,}}}}[ \t\r]*(?:\n|[\\\"'<…]|\Z)")
_REST_START = re.compile(rf"[A-Za-z0-9+/=]+[ \t\r]*(?=\n|\\(?:r\\)?n)|(?:{_BEFORE_BODY}|{INVISIBLE.pattern}){{0,64}}"
                         + _LINE_OF_BASE64.format(rf"{_KEY_BODY_START}|(?=[A-Za-z0-9+/=]*[A-Z])(?=[A-Za-z0-9+/=]*[a-z])"
                                                  r"(?=[A-Za-z0-9+/=]*[0-9+/])"))
# An item id is read more narrowly (secret_in_id): there a key's rest starts with a key's body start alone, as on main.
_ID_REST_START = re.compile(rf"[A-Za-z0-9+/=]+[ \t\r]*(?=\n|\\(?:r\\)?n)|(?:{_BEFORE_BODY}){{0,64}}"
                            + _LINE_OF_BASE64.format(_KEY_BODY_START))
# Past lines of words where main's rest from the placeholder runs on, a line of base64 alone on its line starts a key's
# rest at any length, as a stream's last line, shorter than the rest, can: after its line break, plain or escaped, up
# to the next or an escape, a quote or the end. A word alone on its line ("Thanks", "README") is no such line.
_REST_PAST_WORDS = re.compile(r"(?:\r?\n|(?<!\\)\\+(?:r\\+)?n)[A-Za-z0-9+/=]+[ \t\r]*(?=\n|[\\\"'<…]|\Z)")
_WORD_ALONE = re.compile(r"(?:\r?\n|(?<!\\)\\+(?:r\\+)?n)(?:[A-Z]?[a-z]+|[A-Z]+)[ \t\r]*(?=\n|[\\\"'<…]|\Z)")
_PLACEHOLDER_ALONE = re.compile(r"(?:\r?\n|(?<!\\)\\+(?:r\\+)?n)\[masked\][ \t\r]*(?=\n|[\\\"'<…]|\Z)")


# A line with no letter or digit but a placeholder's, and escapes of no line break: a rule ("---"), a list's or a
# quote's marks before a placeholder ("  [masked]", "> [masked]"), a brace or a fence alone.
_MARKS_LINE = re.compile(r"(?:\[masked\]|[^A-Za-z0-9\\\r\n]|\\+(?:[^A-Za-z0-9\\\r\n]|t))*")


def _skipped_line(text, at):
    """Where the line after the line break at `at` ends, if it is a word alone (_WORD_ALONE), a placeholder alone, a
    line of words (_WORDS_LINE) or a line of marks (_MARKS_LINE), which a rest past lines of words passes over; None
    otherwise. Each is read as a whole line: one that holds a run of base64 a key's could be (_holds_token) anywhere in
    it, after an escape that is no line break, a quote, a "<", a "…" or its words, is passed over by none, so the rest
    starts there and takes it, as main's does."""
    brk = _LINE_BREAK.match(text, at)
    if not brk:
        return None
    end = _KEY_LINE_END.search(text, brk.end()).start()
    if _holds_token(text, brk.end(), end):
        return None
    if (_WORD_ALONE.match(text, at) or _PLACEHOLDER_ALONE.match(text, at) or _WORDS_LINE_AT.match(text, at)
            or _MARKS_LINE.fullmatch(text, brk.end(), end)):
        return end
    return None


# A quote as a web page writes it, one of the entities the readers of a line's lead accept (_QUOTE_TOKENS).
_QUOTE_ENTITY = re.compile(_pattern(tuple(token for token in _QUOTE_TOKENS if token[0][0] == "&")))


def _holds_token(text, start, end):
    """Whether text[start:end] holds a run of base64 a key's fragment could be (_token_like), read as every reader of a
    key's fragment reads it (_fragment_view), past placeholders."""
    line = _rest_view(text[start:end]).replace("[masked]", " ")
    return any(_token_like(m.group()) for m in _RUN.finditer(line))


# A line break, plain or escaped, before a line of words, as "next steps", "tls.crt: none" or "- deploy it" are: past
# a list's or a quote's marks, a word (a letter, then small letters, as "tls.crt" or "don't" are), then a space and
# the next word's letter. A line of base64 is no word, even with words after it on its line, as a snippet joins it
# ("MIIEvQ... notes deploy"), or in JSON that runs on into the next field ('...8Vk=", "note": "next steps"'). A key's
# footer, which holds spaces, is no such line.
_WORDS_LINE = (r"(?:\r?\n|(?<!\\)\\+(?:r\\+)?n)(?![ \t\"'#>*]*-----)[ \t\"'#>*-]*"
               r"[A-Za-z][a-z]{0,19}(?:[.'_-][a-z]{1,20})*[,.;:!?)]*[ \t]+[(\"']?[A-Za-z]")
# Where a JSON string that holds a key's rest ends before the object's next field's name or its end, its quotes
# escaped at any depth: the field after it ('", "note": "next steps"}') is no part of the key. A list of strings, one
# line of the body each, goes on, as no field's name follows its comma.
_NEXT_FIELD = r"(?<!\\)\\*\"[ \t]*(?:\}|,[ \t]*\\*\"[\w.-]+\\*\"[ \t]*:)"
_REST = re.compile(rf"((?:(?!{_KEY_END})(?!{_WORDS_LINE})(?!{_NEXT_FIELD}){_IN_PARAGRAPH})*)(?:{_KEY_END})?")
_KEY_END_TEXT = re.compile(_KEY_END)
_PEM_HEADER = re.compile("-----BEGIN ")
# A run of backslashes is tried once, from its start: tried at each of them, a long run took time in its square.
_KEY_LINE_END = re.compile(r"\r?\n|(?<!\\)\\+(?:r\\+)?n|\Z")
_LINE_BREAK = re.compile(r"\r?\n|(?<!\\)\\+(?:r\\+)?n")
_WORDS_LINE_AT = re.compile(_WORDS_LINE)
_BASE64_CHARACTER = re.compile(r"[A-Za-z0-9+/]")
_RUN = re.compile(r"[A-Za-z0-9+/]{2,}={1,2}|[A-Za-z0-9+/]{4,}")
_KEY_ESCAPE = re.compile(r"(?<!\\)\\+(?:[nrt]|u[0-9A-Fa-f]{4})")
# JSON's escape of one of base64's characters, behind any number of backslashes: a slash as PHP writes one ("\\/",
# and "\\\\\\/" in JSON in JSON), or any of them by its code ("\\u002f", "\\u0041"), a character of the run
# it is in. A run of backslashes is tried once, from its start.
_ESCAPED_BASE64 = r"(?<!\\)\\+(?:/|u00(?:2[bBfF]|3[0-9dD]|4[1-9a-fA-F]|5[0-9aA]|6[1-9a-fA-F]|7[0-9aA]))"
# What every reader of a key's fragment reads as another character (_fragment_view): an escaped character of base64's
# as that character; any other escape of a space, a line break or a character (_KEY_ESCAPE), and a quote written as a
# web page's entity (_QUOTE_ENTITY), as a space.
_FRAGMENT_READS = re.compile(rf"({_ESCAPED_BASE64})|(?:{_KEY_ESCAPE.pattern})|(?:{_QUOTE_ENTITY.pattern})")


def _fragment_read(m):
    """What a match of _FRAGMENT_READS reads as."""
    escape = m.group(1)
    return " " if escape is None else "/" if escape[-1] == "/" else chr(int(escape[-2:], 16))


def _fragment_view(text):
    """(`text` as every reader of a key's fragment reads it, and for each of its characters where it starts in `text`,
    then `len(text)`): without the characters a reader does not see, at any depth of escaping (_unseen_view), and with
    each escape _FRAGMENT_READS reads as another character read so. One view for every such reader (_rest_view,
    _holds_token, _token_tail, _encoded_words, _base64_lines, _public_rest, _cut_body_end), so a run one reads whole
    none reads as parted. Read once, in proportion."""
    unseen, kept = _unseen_view(text)
    pieces, at, last = [], [], 0
    for m in _FRAGMENT_READS.finditer(unseen):
        pieces += (unseen[last:m.start()], _fragment_read(m))
        at += kept[last:m.start()]
        at.append(kept[m.start()])
        last = m.end()
    if not pieces:
        return unseen, (range(len(text) + 1) if unseen is text else [*kept, len(text)])
    pieces.append(unseen[last:])
    at += kept[last:]
    at.append(len(text))
    return "".join(pieces), at


def _rest_view(line):
    """A line as a key's rest is read: _fragment_view's text, without its map."""
    return _FRAGMENT_READS.sub(_fragment_read, _unseen_view(line)[0])


def _headless_key_edits(text):
    """Each private key's footer with no header before it, as an older kb's row-by-row remask or a body pasted alone
    leaves one, as an edit that masks it from the start of its paragraph. Whatever else that paragraph holds before the
    footer is masked too, which is the cost, as after a key without a footer."""
    if "-----END" not in text:
        return []
    starts, spans = [m.end() for m in _BLANK_LINE.finditer(text)], []
    for footer in KEY_FOOTER.finditer(text):
        i = bisect.bisect_right(starts, footer.start()) - 1
        start = max(starts[i] if i >= 0 else 0, spans[-1][1] if spans else 0)
        spans.append((start, footer.end()))
    return [(start, end, "[masked]") for start, end in spans]


_PLACEHOLDER = re.compile(r"\[masked\]")
# Whether mask() reads a stored row from a kb before keys were masked whole, which may hold an older kb's rest of a
# key after a placeholder (stored_row): only there does base64 after a placeholder count as a key's rest unless it
# decodes as public material. In text read fresh kb masks a key whole, so a key's rest starts only at a key's body
# start, as on main, and base64 after a password's placeholder stays.
_STORED_ROW = contextvars.ContextVar("kb_mask_stored_row", default=False)


@contextlib.contextmanager
def stored_row(*versions):
    """Within it, mask() reads text as stored rows masked with the mask versions given (each row's `masked`): rows of
    which any was masked before a version carried its revision ("r<revision>-...") are an older kb's."""
    marker = _STORED_ROW.set(any(not (isinstance(v, str) and re.match(r"r\d+-", v)) for v in versions))
    try:
        yield
    finally:
        _STORED_ROW.reset(marker)


# OpenSSH's public key types, a closed list, each with how many strings its key holds, each after its length, its
# type's name first. A certificate's types (...-cert-v01@openssh.com) are none.
_OPENSSH_PUBLIC = {b"ssh-ed25519": 2, b"ssh-rsa": 3, b"ssh-dss": 5, b"ecdsa-sha2-nistp256": 3,
                   b"ecdsa-sha2-nistp384": 3, b"ecdsa-sha2-nistp521": 3, b"sk-ssh-ed25519@openssh.com": 3,
                   b"sk-ecdsa-sha2-nistp256@openssh.com": 4}


def _openssh_end(data, at):
    """Where the OpenSSH public key at data[at] ends, its type's strings, each after its length, as many as the type
    holds (_OPENSSH_PUBLIC); None while `data` is too short to say, or -1 when none starts there."""
    count = None
    while count != 0:
        if at + 4 > len(data):
            return None
        size, at = int.from_bytes(data[at:at + 4], "big"), at + 4
        if count is None:
            if size > 64:  # no type's name is so long
                return -1
            if at + size > len(data):
                return None
            count = _OPENSSH_PUBLIC.get(bytes(data[at:at + size]))
            if count is None:
                return -1
        at, count = at + size, count - 1
    return at


def _element(der, at, end):
    """(tag, where its content starts, where it ends) of the DER element at `at`, which must end by `end`, or None."""
    if at + 2 > end:
        return None
    tag, size, at = der[at], der[at + 1], at + 2
    if size & 0x80:
        width = size & 0x7f
        if not 0 < width < 4 or at + width > end:
            return None
        size, at = int.from_bytes(der[at:at + width], "big"), at + width
    return (tag, at, at + size) if at + size <= end else None


def _elements(der, start, end):
    """The DER elements that fill der[start:end] exactly, or None."""
    out = []
    while start < end:
        element = _element(der, start, end)
        if element is None:
            return None
        out.append(element)
        start = element[2]
    return out


_PKCS7_SIGNED_DATA = bytes.fromhex("2a864886f70d010702")
_PKCS7_DATA = bytes.fromhex("2a864886f70d010701")


def _certificate_bundle(data, start, end):
    """Whether data[start:end], the content of PKCS#7 signed data, is a bundle of certificates as openssl crl2pkcs7
    writes one: a version, no digest algorithm, content that names its type and holds none, certificates or lists of
    revoked ones, each public material but no bundle (_public_material), and no signer. Signed data that holds content,
    as a signed message does, is no bundle, nor is a bundle inside one, which openssl never writes: read as one, a
    bundle nested deep would recurse past Python's limit."""
    inner = _elements(data, start, end)
    if not inner or len(inner) != 1 or inner[0][0] != 0x30:
        return False
    parts = _elements(data, inner[0][1], inner[0][2])
    if not parts or [p[0] for p in parts[:3]] != [0x02, 0x31, 0x30] or parts[-1][0] != 0x31:
        return False
    if parts[1][1] != parts[1][2] or parts[-1][1] != parts[-1][2]:  # no digest algorithm, no signer
        return False
    content = _elements(data, parts[2][1], parts[2][2])
    if not content or len(content) != 1 or content[0][0] != 0x06 or data[content[0][1]:content[0][2]] != _PKCS7_DATA:
        return False
    held = parts[3:-1]
    if [p[0] for p in held] not in ([0xa0], [0xa1], [0xa0, 0xa1]):
        return False
    for _, at, stop in held:
        if at == stop:
            return False
        while at < stop:
            member = _element(data, at, stop)
            if member is None or not _public_material(data[at:member[2]], bundles=False):
                return False
            at = member[2]
    return True


def _public_material(data, bundles=True):
    """Whether bytes decoded from base64 are, whole, public material: an X.509 certificate or a certificate request
    (what is signed, how, and the signature, the first starting with a version or a serial number), a public key
    (SubjectPublicKeyInfo: an algorithm and the key), a PKCS#7 bundle of certificates (_certificate_bundle; with
    `bundles` false, not one), or an OpenSSH public key (_openssh_end: its type's strings, as many as the type holds,
    and no byte after them). No private key's DER has any of these shapes, and one cut short has none; nor has an
    OpenSSH private key's blob ("openssh-key-v1")."""
    if data[:1] != b"\x30":
        return _openssh_end(data, 0) == len(data)
    top = _element(data, 0, len(data))
    parts = _elements(data, top[1], top[2]) if top and top[0] == 0x30 and top[2] == len(data) else None
    if not parts:
        return False
    first = [(_element(data, p[1], p[2]) or (None,))[0] for p in parts]
    tags = [p[0] for p in parts]
    if tags == [0x30, 0x30, 0x03]:
        if first[1] != 0x06:
            return False
        if first[0] in (0xa0, 0x02):
            return True
        # a list of revoked certificates in version 1, which starts with how it is signed, then its issuer and when
        signed = _elements(data, parts[0][1], parts[0][2]) or []
        return [e[0] for e in signed[:3]] in ([0x30, 0x30, 0x17], [0x30, 0x30, 0x18])
    if tags == [0x06, 0xa0]:
        return bundles and data[parts[0][1]:parts[0][2]] == _PKCS7_SIGNED_DATA and _certificate_bundle(data, *parts[1][1:])
    return tags == [0x30, 0x03] and first[0] == 0x06


def _token_like(run):
    """Whether a run of base64 characters could be part of a key's body: one that ends in base64's "=" padding, or
    one of four characters or more that mixes two of capitals, small letters, and digits or "+" and "/" and is no
    word in small letters, with a vowel, after a capital ("Next", but not "Bcsv"). A word in one case does not; a
    camel-case name or a hash does, and is read as one, failing closed, as main's reading hides the whole paragraph."""
    if run.endswith("="):
        return True
    if re.fullmatch(r"[A-Z]{2,}", run) and not re.search(r"[AEIOUY]", run):
        return True  # "BCDF": no word, as an acronym with a vowel ("API") is
    if re.search(r"[+/]", run) or re.fullmatch(r"[0-9]{4,}", run):
        return True  # "0123/567", "20261103": base64's own marks, or a run of digits no word or small number holds
    return (sum(bool(re.search(kind, run)) for kind in ("[A-Z]", "[a-z]", "[0-9+/]")) >= 2
            and not re.fullmatch(r"[A-Z][b-df-hj-np-tv-xz]*[aeiouy][a-z]*", run))  # read one way: in proportion


# a line's run of base64 as long as a rest's start reads one (_LINE_OF_BASE64, its padding counted): a stream's short
# last line of 34 characters and "==" is one
_LINE_RUN = re.compile(r"(?=[A-Za-z0-9+/=]{36})[A-Za-z0-9+/]+={0,2}")


def _encoded_words(found):
    """Whether the line of base64 a key's rest would start with (`found`, as _REST_START matched it) decodes, from its
    start, to words: text a reader sees, with a space, and no run that could be part of a key's body (_token_like) or a
    header's or footer's dashes. Such a line is another stream encoded alone, as words above a key encoded alone are
    ("notes notes ..."), and no part of a key, whose lines decode to its PEM text."""
    runs = _LINE_RUN.findall(_rest_view(found))  # an escaped line break a space, past its "n"
    if not runs:
        return False
    run = runs[-1]
    data = _base64_decoded(run, 0, None if run.endswith("=") else len(run) // 4 * 4)
    try:
        words = re.sub(r"[\t\r\n]", " ", data.decode("utf-8")) if data else ""
    except UnicodeDecodeError:
        return False
    return (" " in words and words.isprintable() and "-----" not in words
            and not any(_token_like(m.group()) for m in _RUN.finditer(words)))


def _line_past(text, found):
    """Where the line that a key's rest's start (`found`) matched ends, so the next line is read from there: at the line
    break, escape or quote that ended it, or past base64 glued to the placeholder, before its line break."""
    end = found.end()
    return end - 1 if end > found.start() and text[end - 1] in "\n\\\"'<…" else end


def _token_tail(text):
    """For a key's rest stopped early, at a line of words or a JSON object's next field, where it ends instead: past
    the last line holding a token (_token_like) before where main's reading of it ended (its paragraph's end, the next
    header, or its footer), so that only a tail of ordinary text stays. Read once for the text: each rest then costs a
    lookup."""
    # runs read as every reader of a key's fragment reads them (_fragment_view): a fragment that the characters a
    # reader does not see or an escaped slash split reads whole, in JSON in JSON too
    view, kept = _fragment_view(text)
    tokens = [kept[m.start()] for m in _RUN.finditer(view) if _token_like(m.group())]
    # and every line that is no line of words (_WORDS_LINE) but holds a run of base64's characters, as a fragment of a
    # key alone on its line ("Bcsv") does
    for m in _LINE_BREAK.finditer(text):
        if not _WORDS_LINE_AT.match(text, m.start()):
            run = _BASE64_CHARACTER.search(text, m.end(), _KEY_LINE_END.search(text, m.end()).start())
            if run:
                tokens.append(run.start())
    tokens.sort()
    ends = sorted([m.start() for m in _BLANK_LINE.finditer(text)] + [m.start() for m in _PEM_HEADER.finditer(text)]
                  + [m.end() for m in _KEY_END_TEXT.finditer(text)])

    # Every line's end, found once when a rest first asks: many rests, each with a token of its own, can end on one long
    # line, and each read on to its end took time in its square. A token's line ends at the first end at or after it;
    # one that starts inside an escaped line break, after its letter, is read from there, once.
    breaks, inside = None, {}

    def line_end(at):
        nonlocal breaks
        if breaks is None:
            found = list(_KEY_LINE_END.finditer(text))
            breaks = [m.start() for m in found], [m.end() for m in found]
        i = bisect.bisect_left(breaks[0], at)
        if i and at < breaks[1][i - 1]:
            if at not in inside:
                inside[at] = _KEY_LINE_END.search(text, at).start()
            return inside[at]
        return breaks[0][i]

    def through(at):
        i = bisect.bisect_left(ends, at)
        last = bisect.bisect_left(tokens, ends[i] if i < len(ends) else len(text)) - 1
        if last < 0 or tokens[last] < at:
            return at
        return line_end(tokens[last])
    return through


# A line of base64 as a key's rest or public material is written, past what can stand around it (quotes, comment and
# quote marks, a list's punctuation), read without the characters a reader does not see.
_BASE64_ONLY_LINE = re.compile(r"[ \t\r\"'#>*,;|\]]*([A-Za-z0-9+/]+=*)?[ \t\r\"',;]*")
# A run of base64 of both cases, 16 characters or more, as a key's fragment is: past the public material a key's rest
# starts with, one takes that material with the rest, failing closed (_key_rests).
_MIXED_RUN = re.compile(r"(?<![A-Za-z0-9+/])(?=[A-Za-z0-9+/]*[A-Z])(?=[A-Za-z0-9+/]*[a-z])[A-Za-z0-9+/]{16,}")


def _mixed_run_starts(text, spans):
    """Where each run of base64 of both cases, 16 characters or more (_MIXED_RUN), starts in `text`, read as a key's
    fragment is (_fragment_view), in order, outside lines that are public material of their own (`spans`,
    _public_lines): a certificate past the next placeholder is no key's fragment. Read once for a text, so many rests
    in one paragraph cost time in proportion."""
    view, kept = _fragment_view(text)
    i, starts = 0, []
    for m in _MIXED_RUN.finditer(view):
        at = kept[m.start()]
        while i < len(spans) and spans[i][1] <= at:
            i += 1
        if i == len(spans) or at < spans[i][0]:
            starts.append(at)
    return starts


def _public_lines(text):
    """Where each run of lines of base64 in `text` that is public material of its own starts and ends, in order: the
    whole public material a block of lines of base64 starts with (_public_prefix), or a run of its lines between lines
    that are none a key's could be ("notes") that is all public material, as round 26's head read them; a line's part
    past a placeholder starts a block of its own, as a rest does, where nothing but what can stand around a line of
    base64 stands before the placeholder: a stretch is whole lines, so a line with more before it (a key's run glued to
    the placeholder, words) is none, and only what decodes as public material stays. Each line is read once, as a
    key's fragment is (_rest_view)."""
    spans, block, at = [], [], 0

    def close():
        lines = [line for line, _, _ in block]
        k = _public_prefix(lines)
        if k:
            spans.append((block[0][1], block[k - 1][2]))
        while k < len(block):
            if not _token_like(lines[k]):
                k += 1
                continue
            q = k
            while q < len(block) and _token_like(lines[q]):
                q += 1
            if _public_prefix(lines[k:q]) == q - k:
                spans.append((block[k][1], block[q - 1][2]))
            k = q
        block.clear()

    for brk in itertools.chain(_LINE_BREAK.finditer(text), [None]):
        stop = brk.start() if brk else len(text)
        line = _rest_view(text[at:stop])
        held = None
        for held in _PLACEHOLDER.finditer(line):
            pass
        if held is not None:
            close()
        found = _BASE64_ONLY_LINE.fullmatch(line, held.end() if held else 0)
        lead = held and _BASE64_ONLY_LINE.fullmatch(line, 0, held.start())
        if found is None or held and (lead is None or lead.group(1)):
            close()
        elif found.group(1):
            block.append((found.group(1), at, stop))
        if brk is None:
            break
        at = brk.end()
    close()
    return spans


def _base64_lines(text, start, end):
    """The lines of base64 that text[start:end] starts with, each as a key's rest or public material is written
    (_BASE64_ONLY_LINE), as (its base64, where it starts, where it ends), up to the first line that is none; a line with
    no base64 (a blank, or a quote alone) is passed. A line that holds a placeholder is none, as cleaning leaves its
    "masked]" (a colour code can end in its "[", no more), so the lines are read only up to the next placeholder: each
    placeholder in a long line read the rest of it, which took time in its square."""
    lines, at = [], start
    held = _PLACEHOLDER.search(text, start, end)
    for brk in itertools.chain(_LINE_BREAK.finditer(text, start, held.start() if held else end), [None]):
        if brk is None and held:
            break  # the line that holds the next placeholder
        stop = brk.start() if brk else end
        line = _BASE64_ONLY_LINE.fullmatch(_rest_view(text[at:stop]))
        if line is None:
            break
        if line.group(1):
            lines.append((line.group(1), at, stop))
        if brk is None:
            break
        at = brk.end()
    return lines


def _element_end(data, at):
    """Where the DER element at data[at] ends, None while `data` is too short to say, or -1 when none starts there."""
    if at + 2 > len(data):
        return None
    size = data[at + 1]
    if not size & 0x80:
        return at + 2 + size
    width = size & 0x7f
    if not 0 < width < 4:
        return -1
    if at + 2 + width > len(data):
        return None
    return at + 2 + width + int.from_bytes(data[at + 2:at + 2 + width], "big")


def _public_prefix(lines):
    """How many of `lines` (each a line's base64), from the first, are whole public material: the most after which one
    element of it (_public_material: DER, or an OpenSSH public key, _openssh_end), or the last of a run of them as a
    chain of certificates is, ends, each stream
    decoded alone (one ends at base64's padding, as each element encoded alone does), where the line after is no base64
    a key's could be (_token_like: a word alone, "notes") or there is none; 0 when none. Decoded as read, and read no
    further than an element that is none."""
    data, whole, pending, at, most = bytearray(), {}, "", 0, 0
    for k, chunk in enumerate(lines):
        pending += chunk
        if chunk.endswith("="):
            decoded = _base64_decoded(pending, 0, None) if len(pending) % 4 == 0 else None
            if decoded is None:
                break
            data += decoded
            pending = ""
        else:
            cut = len(pending) - len(pending) % 4
            data += _base64_decoded(pending, 0, cut)
            pending = pending[cut:]
        if not pending:
            whole[len(data)] = k + 1  # the data is whole groups through this line
        while at < len(data):
            # public material is a DER sequence, or an OpenSSH public key, which is strings, each after its length
            stop = _element_end(data, at) if data[at] == 0x30 else _openssh_end(data, at)
            if stop is None or stop > len(data):
                break
            if stop < 0 or not _public_material(bytes(data[at:stop])):
                return most
            at = stop
            n = whole.get(at)
            if n and (n == len(lines) or not _token_like(lines[n])):
                most = n
    return most


def _public_end(text, start, end):
    """Where the whole lines of base64 that text[start:end], what main reads as a key's rest after a placeholder, starts
    with end, where they decode to public material, one element or a run of them as a chain of certificates is, ending
    where the line after is none a key's could be (_public_prefix); None where they do not. That material is no key's
    rest. A run of base64 lines that holds a private key's bytes too decodes to no such run."""
    lines = _base64_lines(text, start, end)
    n = _public_prefix([line for line, _, _ in lines])
    return lines[n - 1][2] if n else None


def _public_rest(rest):
    """Whether the text after a placeholder decodes whole, as base64, to public material (_public_material)."""
    data = None if _KEY_END_TEXT.search(rest) else _base64_decoded(
        re.sub(r"[^A-Za-z0-9+/=]", "", _rest_view(rest)), 0, None)
    return bool(data) and _public_material(data)


def _rest_ends(text):
    """Where main's reading of a key's rest (_REST) from each place in `text` ends, read once for each stretch: it
    reads a character at a time up to the first place where one of its stops holds, whatever its start, so every start
    before that place ends where the first did, and many placeholders in one paragraph cost time in proportion. Places
    are asked for in order."""
    last = [-1, -1, -1]  # where the last reading started, where its stop holds, where it ended

    def end(at):
        if not last[0] <= at <= last[1]:
            m = _REST.match(text, at)
            stop = m.end()
            if stop == m.end(1) and _WORDS_LINE_AT.match(text, stop):
                stop = _cut_body_end(text, at, stop)
            last[:] = at, m.end(1), stop
        return last[2]
    return end


# A key's body that runs on into a line of words, as a snippet cuts one: where its last line is base64 to its end with
# no "=" and no shorter than a line of base64 above it, the body has not ended there, so the run of base64's characters
# that starts the line of words after it is the body's short last line, however much it looks like a word ("ei harbor
# deploy", "Ynu walrus moved"), and is masked with it; the words after it stay. Where the body's last line is shorter
# than the one above it, or ends with "=", the body can end there, and the line of words stays whole. A line alone
# can be a whole key, or a key cut after it: its next line's first word goes with it, which is the cost. A body line
# after a placeholder is read so too, glued to it ("[masked]MIIC...") or after any run of what is no letter or digit:
# space of any width, quote, comment and list marks, escapes, "<br>" and more placeholders ("[masked] \\t[masked]MIIC"),
# as main's rest reads a body past any of them, and before any such run but "=". Each line is read as a reader sees it
# (_rest_view): JSON's escaped slash ("\\/", as PHP writes JSON) is a slash of the base64, and other escapes are spaces.
_CUT_LEAD = r"\[masked\]|\[(?!masked\])|<br\s*/?>|<(?!br\s*/?>)|[^A-Za-z0-9+/\[<]|[+/](?![A-Za-z0-9+/])"
_CUT_LINE = re.compile(rf"(?:{_CUT_LEAD})*([A-Za-z0-9+/]+)[^A-Za-z0-9+/=]*")
_CUT_ABOVE = re.compile(r"(?<![A-Za-z0-9+/=])([A-Za-z0-9+/]+)[^A-Za-z0-9+/=]*\Z")
_CUT_WORD = re.compile(r"[ \t\"'#>*-]*([A-Za-z0-9+/]+)")


def _cut_body_end(text, start, stop):
    """Where a key's rest read from `start` that stops before a line of words at `stop` ends: past the first run of
    base64's characters on that line where the rest's body has not ended (_CUT_LINE), and the rest is no public
    material (_public_rest); otherwise at `stop`."""
    breaks = []  # the rest's last two line breaks: each stretch's rest is read once (_rest_ends), in proportion
    for m in _LINE_BREAK.finditer(text, start, stop):
        breaks[:] = breaks[-1:] + [m.span()]
    last = _CUT_LINE.fullmatch(_rest_view(text[breaks[-1][1] if breaks else start:stop]))
    if not last:
        return stop
    above = breaks and _CUT_ABOVE.search(_rest_view(text[breaks[-2][1] if len(breaks) > 1 else start:breaks[-1][0]]))
    if above and len(last.group(1)) < len(above.group(1)) or _public_rest(text[start:stop]):
        return stop
    brk = _LINE_BREAK.match(text, stop)
    view, kept = _fragment_view(text[brk.end():_KEY_LINE_END.search(text, brk.end()).start()])
    word = _CUT_WORD.match(view)
    return brk.end() + kept[word.end(1)] if word else stop


def _breaks_start(text, start, end):
    """Where the run of line breaks that text[start:end] ends with starts; `end` where it ends with none."""
    for brk in reversed([*_LINE_BREAK.finditer(text, start, end)]):
        if brk.end() != end:
            break
        end = brk.start()
    return end


def _ends_inside_words(text, start, end):
    """Whether a key's rest read through text[start:end] ends inside a line of words, past its first run, as main's
    reading of a body run on into one does (_cut_body_end)."""
    if _KEY_LINE_END.match(text, end):
        return False
    brk = None
    for brk in _LINE_BREAK.finditer(text, start, end):
        pass
    return brk is not None and bool(_WORDS_LINE_AT.match(text, brk.start()))


def _key_rests(text, starts=_REST_START):
    """Each key's rest after an older kb's placeholder (`starts`, _REST) that main's reading would remove, in order, as
    (where it starts, where it ends, whether it stays): where it starts with public material (_public_end) and no run
    of base64 of both cases, 16 characters or more, follows it through all main's reading takes (_MIXED_RUN) but in
    lines that are public material of their own (_public_lines), that material stays, and so do those lines, and all
    else main's reading takes past it is removed, whatever it holds, as main removes it, failing closed; any other rest
    is removed unless its base64 decodes whole as public material. Base64 glued to a placeholder
    that starts no rest, as before a line break escaped twice, is read so too, but only to stay: such a run's own
    reading (_glued_run_edits) masks it otherwise, as main's does. Lines that decode to words right after a
    placeholder (_encoded_words) stay, and the rest starts past them where their next line starts one, in the
    placeholder's paragraph, as main's ends with it; where main reads no rest from the placeholder, such a rest takes
    none of the tail of tokens main's rest takes, and where main's runs on past its start, it runs on as main's does.
    Where main's rest from the placeholder runs on past them, base64 alone on a line of its
    own at any length starts one too (_REST_PAST_WORDS), as a stream's short last line is, past any word alone on its
    line, and runs on as main's does. Each placeholder outside the core of the rest before it is read once, and each
    core runs once to its end, so a paragraph of many costs time in proportion."""
    core, covered, tail, mixed, spans, ends = 0, 0, None, None, None, _rest_ends(text)
    start = _ID_REST_START if starts is _REST_START and not _STORED_ROW.get() else starts
    for at in _PLACEHOLDER.finditer(text):
        if at.start() < core:
            continue
        begin = at.end()
        rest, main_reach, as_main = start.match(text, begin), None, False
        while rest and _encoded_words(rest.group()):
            if _BLANK_LINE.search(text, begin, rest.end()):
                # past a blank line, the line is the next paragraph's, and so is all after it: main's rest ends there
                rest = None
                break
            # another stream's line of words holds no key's rest, as a line of words does not: the rest, if any,
            # starts past it, with its next line read as one right after the placeholder is
            begin = _line_past(text, rest)
            rest = start.match(text, begin) if begin < len(text) else None
            if rest is None and begin < len(text):
                # or, where main's rest from the placeholder takes it, with its tail, as base64 alone on its line, as a
                # stream's short last line is, past any word alone on its line, placeholder alone on its line or line
                # of words, which main's rest takes too
                if main_reach is None:
                    main_reach = (_ID_REST_START.match(text, at.end()) and ends(at.end())) or -1
                    tail = tail or _token_tail(text)
                    main_reach = tail(main_reach) if main_reach >= 0 else -1
                line = begin
                while main_reach > line and (skip := _skipped_line(text, line)) is not None:
                    line = skip
                # past them, any other line main's rest takes starts one: base64 alone on its line, or a line that
                # holds any other run of base64's characters, with whatever its line holds, as main's rest does
                rest = main_reach > line and (_REST_PAST_WORDS.match(text, line) or _LINE_BREAK.match(text, line)) or None
                if rest is None and main_reach > line and line == begin:
                    # or the rest of a line of encoded words that ended at a quote or an escape that is no line break
                    # (_line_past), read as a whole line is: where it holds a key's run, the rest starts there
                    stop = _KEY_LINE_END.search(text, line).start()
                    rest = _holds_token(text, line, stop) and _BASE64_CHARACTER.search(text, line, stop) or None
                begin, as_main = (line, True) if rest else (begin, False)
        if rest and begin != at.end() and not as_main:
            # a rest that starts past lines of words at a line that starts one, where main's rest from the
            # placeholder runs on past it, runs on as main's does too, with the run on its next line
            if main_reach is None:
                main_reach = (_ID_REST_START.match(text, at.end()) and ends(at.end())) or -1
                tail = tail or _token_tail(text)
                main_reach = tail(main_reach) if main_reach >= 0 else -1
            as_main = main_reach > begin
        if not rest and not _BASE64_CHARACTER.match(text, at.end()):
            continue
        reach = ends(begin)
        tail = tail or _token_tail(text)
        # a placeholder past the core, in the tail, still reads its own rest; past lines of words, main reads none,
        # but for a rest started where main's runs on, which runs on as main's does
        end = tail(reach) if begin == at.end() or as_main else reach
        if end <= covered:  # which ends inside one already masked, read before the public material past it, which
            core = reach if rest else core  # reads on to `end`: many such in one tail took time in its square
            continue
        public = _public_end(text, begin, end)
        if public is not None:
            if mixed is None:
                spans = _public_lines(text)
                mixed = _mixed_run_starts(text, spans)
            i = bisect.bisect_left(mixed, public)
            past = rest and _BASE64_CHARACTER.search(_rest_view(text[public:end]))
            # with no run of a key's past it, the material is no key's rest, unless main's reading ends inside a line
            # of words, at a body's last line run on into it (_cut_body_end): masked past the material, the line's
            # other words would join its last line, so it goes with the rest, as on main
            if (i == len(mixed) or mixed[i] >= end) and not (past and _ends_inside_words(text, public, end)):
                yield begin, public, True
                if not past:
                    continue
                # all main's reading takes past it is masked, as main masks it, whatever it holds, but for lines that
                # are public material of their own, which stay as the material does, each line's lead with it
                begin = public
                j = bisect.bisect_left(spans, (public, public))
                while j < len(spans) and spans[j][0] < end:
                    first, last = spans[j]
                    cut = None
                    for cut in _LINE_BREAK.finditer(text, begin, first):
                        pass
                    if cut is not None and cut.start() > begin:
                        yield begin, cut.start(), False
                    yield first, last, True
                    begin, j = last, j + 1
                # the line breaks it ends with stay, so the next pass reads what follows as this one does: removed, a
                # blank line before a line of words, in JSON inside JSON, left the material's last line run on into it
                stop = _breaks_start(text, begin, end)
                if stop > begin and not _public_rest(text[begin:stop]):
                    yield begin, stop, False
                core, covered = reach, max(covered, end)
                continue
        if not rest:
            continue
        core = reach
        if not _public_rest(text[begin:end]):
            covered = max(covered, end)
            yield begin, end, False


class _Rests(list):
    """_key_rest_edits' edits, and in `stays` where the public material each rest starts with starts and ends."""
    stays = ()


def _key_rest_edits(text, starts=_REST_START):
    """The rest of a key after an older kb's placeholder (_key_rests) as an edit that removes it, unless it stays; the
    stretches of those that stay are its `stays`."""
    edits, stays = _Rests(), []
    for start, end, kept in _key_rests(text, starts):  # in order of their starts; one in a tail can run past it
        if kept:
            stays.append((start, end))
        elif edits and start <= edits[-1][1]:
            edits[-1] = (edits[-1][0], max(edits[-1][1], end), "")
        else:
            edits.append((start, end, ""))
    edits.stays = stays
    return edits


def _glued_run_edits(text, rests):
    """Each run of characters glued to a placeholder (_GLUED_RUN) as an edit that masks it with the placeholder, as
    main's does, but for a run of whole public material (_public_prefix) inside a key's rest that the reading of keys'
    rests in force (`rests`, _key_rest_edits) keeps as public material (its `stays`), from that run's own placeholder
    or one before it: that rest stays only as the run's lines are public material, and the run masked alone left the
    lines after it to read as a key's fragment, which took the rest with it. Such a run stays, and only its placeholder
    is replaced, as main's edit replaces it. A reading that keeps no rest, as main's, masks every such run."""
    edits, stays, i = [], None, 0
    for m in _GLUED_RUN.finditer(text):
        at = m.start() + len("[masked]")
        if stays is None:
            stays = getattr(rests(text), "stays", ())
        while i + 1 < len(stays) and stays[i + 1][0] <= at:  # the last to start by it, which ends last
            i += 1
        if stays and stays[i][0] <= at < stays[i][1] and _public_prefix(
                [line for line, _, _ in _base64_lines(text, at, stays[i][1])]):
            edits.append((m.start(), at, "[masked]"))
        else:
            edits.append((m.start(), m.end(), "[masked]"))
    return edits


def _replace(text, spans, replacement):
    """`text` with each of the ordered spans replaced, built once: one copy per span would be quadratic."""
    out, last = [], 0
    for start, end in spans:
        out += [text[last:start], replacement]
        last = end
    return "".join(out + [text[last:]])


# What a pass reads, in text and in an item id: the secrets known by their shape, then the patterns that read a value
# by its name, each with what masks it and how its matches are read. An id reads "Bearer" and "Basic" as words, and a
# named value only in assignment form, as re.sub reads it; in text a name's value can be the next name (_named).
# The readings that take a password's value whole where KEYED_SECRET would end it early, so they come before it.
_WHOLE_VALUES = ((XML_SECRET, _xml_span), (LINE_PASSWORD, _line_password_span), (APP_PASSWORD, _app_password_span),
                 (APP_PASSWORD_LINE, _app_password_span),
                 *((p, _quoted_password_span) for p in QUOTED_PASSWORDS))
# Main's own readings of text, as gate 9 found them: its token shapes, links, commands and named values, each read as
# main reads it, with the four values gate 9 reads as no secret (a tokenizer's, an ARN's, a date's, a file name's)
# read so here too. _read hides all these hide, so no reading added since can show what main's own readings hide,
# whatever it reads first.
_MAIN_PREFIXED = [PREFIXED_SECRETS[0], *(re.compile(p.pattern.replace("]*:)", "]+:)", 1).replace(
    "&])*%(?:25)?3A)", "&])+%(?:25)?3A)", 1), p.flags) for p in PREFIXED_SECRETS[1:3]), *PREFIXED_SECRETS[3:]]
_SUBSCRIPTION_KEY = re.compile(r"(?i)subscription[_-]?key")


def _main_reading(pattern):
    return re.compile(pattern.pattern.replace("subscription[_-]?key(?![A-Za-z0-9_-])|", ""), pattern.flags)


_MAIN_RULES = ([p for p in SECRET_PATTERNS if p is not ENCODED_PRIVATE_KEY],
               tuple((_main_reading(p), _keyed_span) for p in (JSON_SECRET, KEYED_SECRET, ENV_KEY_SECRET, FLAG_SECRET))
               + ((AUTH_SECRET, _authorization_span),), "main")
# The readings of text main's own lack, read by name.
_NEW_READINGS = frozenset([p for p, _ in _WHOLE_VALUES] + [SHORT_PASSWORD, _TODAYS_APP_PASSWORD, _TODAYS_APP_PASSWORD_LINE])
_TEXT_RULES = (SECRET_PATTERNS, ((JSON_SECRET, _keyed_span), *_WHOLE_VALUES, (KEYED_SECRET, _keyed_span),
                                 (ENV_KEY_SECRET, _keyed_span), (SHORT_PASSWORD, _short_password_span),
                                 (FLAG_SECRET, _keyed_span), (AUTH_SECRET, _authorization_span)), "named")
_ID_RULES = (_ID_PATTERNS, ((KEYED_ID, _keyed_span), (ENV_KEY_ID, _keyed_span)), "plain")
# Main's text readings as main 032391d reads them: these, with its own app password (_TODAYS_APP_PASSWORD), every row
# of COMMAND_FLAGS read as it reads them (_todays_password_edits), no wrappings, and no row reading the text as it was
# before the rows masked.
_TODAYS_RULES = (SECRET_PATTERNS, tuple(({APP_PASSWORD: _TODAYS_APP_PASSWORD, APP_PASSWORD_LINE: _TODAYS_APP_PASSWORD_LINE}
                                         .get(pattern, pattern), span_of) for pattern, span_of in _TEXT_RULES[1]), "today")


def _edit(text, origin, edits):
    """`text` with each of the ordered (start, end, insert) edits made, built once, and with it `origin` carried over:
    where in the first text each character came from, -1 for one an edit inserted. With `origin` None, nothing is
    tracked."""
    if not edits:
        return text, origin
    out, kept, last = [], [] if origin is not None else None, 0
    for start, end, insert in edits:
        out += [text[last:start], insert]
        if kept is not None:
            kept += origin[last:start]
            kept += [-1] * len(insert)
        last = end
    out.append(text[last:])
    if kept is not None:
        kept += origin[last:]
    return "".join(out), kept


def _run(text, rules, origin=None, named=None, fired=None, look=False):
    """One pass of every pattern, as (the text masked, where each of its characters came from): the secrets known by
    their shape, then an older kb's leftovers, links, cookies and commands, then the patterns that read a value by its
    name. With `origin` None, nothing is tracked and the second is None. With `named` a list, each named value masked
    is added to it as (name start, name end, masked start, masked end) in the first text, which needs `origin`. With
    `fired` a list, something is added to it when a reading main's own readings (_MAIN_RULES) lack masks anything:
    with none, this pass masks as theirs does. With `look` too, it is added to also where, with nothing added before,
    this reading's rows main's own lack find nothing but main 33c82f6's (_TODAYS_COMMANDS) find a password: only while
    the passes so far masked as main's do, as main's rows then read the text main's own pass reads."""
    patterns, keyed, reading = rules
    size = len(text)
    note = fired.append if fired is not None else (lambda _: None)
    # gitleaks' rules read the text the pass was given too, before any pattern here cuts a token: GitLab's own shape
    # takes a routable token's first part and leaves ".aa1234567", and a link's reading a webhook's start. Each token
    # found so is hidden whole at the end.
    whole = _gitleaks_spans(text) if reading in ("named", "today") else []
    untracked = whole and origin is None
    if untracked:
        origin = list(range(size))
    given = origin
    for pattern in patterns:
        edits = [(m.start(), m.end(), "[masked]") for m in pattern.finditer(text)
                 if pattern not in (BASIC, BEARER) or not _scheme_words(m)]
        if edits and pattern is ENCODED_PRIVATE_KEY:
            note(pattern)
        text, origin = _edit(text, origin, edits)
    rests = (lambda text: _key_rest_edits(text, _ID_REST_START)) if reading == "plain" else _key_rest_edits
    for edits in (_docker_login_edits, rests, _headless_key_edits, lambda text: _glued_run_edits(text, rests)):
        text, origin = _edit(text, origin, edits(text))
    for pattern, main in zip(PREFIXED_SECRETS if reading != "main" else _MAIN_PREFIXED, _MAIN_PREFIXED):
        found = list(pattern.finditer(text))
        if pattern is not main and any(not main.fullmatch(text, m.start(), m.end()) for m in found):
            note(pattern)  # a link with no user name
        text, origin = _edit(text, origin, [(m.end(1), m.end(), "[masked]") for m in found])
    if reading not in ("main", "today") and _WRAPPINGS.get():
        edits = _wrapping_edits(text)
        if edits:
            note(_WRAPPINGS)  # which _masked looks for: it then hides what the mask without this reading hides too
        text, origin = _edit(text, origin, edits)
    if _ANY_COMMAND.search(text) and reading == "today":  # every row as main reads it today
        for n, command in enumerate(_TODAYS_COMMANDS):
            edits = _todays_password_edits(text, *command, n >= _MAIN_COMMANDS)
            if edits and n >= _MAIN_COMMANDS:
                note(command)
            text, origin = _edit(text, origin, edits)
    elif _ANY_COMMAND.search(text):
        rows = _COMMANDS[:_MAIN_COMMANDS] if reading == "main" else _COMMANDS
        before, moves, mains = text, [], None
        for n, command in enumerate(rows):
            if n == _MAIN_COMMANDS:
                mains = text  # what main's rows from here on read today, the rows before it alike
            edits = _command_password_edits(text, *command, n >= _MAIN_COMMANDS)
            if edits and n >= _MAIN_COMMANDS:
                note(command)
                mains = None
            moves.append(_moves(edits))
            text, origin = _edit(text, origin, edits)
        if look and mains is not None and fired is not None and not fired \
                and any(_todays_password_edits(mains, *row, True) for row in _TODAYS_COMMANDS[_MAIN_COMMANDS:]):
            note(_TODAYS_COMMANDS)  # main reads a password with one of these rows where this reading found none
        if reading != "main":  # where a row before masked, each row reads the text as it was before too
            edits, row = _before_edits(before, rows, moves)
            if edits:
                note(row)
            text, origin = _edit(text, origin, edits)
    if reading == "named" and _SUBSCRIPTION_KEY.search(text):
        note(_SUBSCRIPTION_KEY)  # a name main's own do not read, which moves where their searches go on
    for pattern, span_of in keyed:
        found = list(_named(pattern, span_of, text, "named" if reading == "today" else reading, note))
        if found and pattern in _NEW_READINGS:
            note(pattern)
        if named is not None:
            for m, (start, end) in found:
                if origin[m.start(1)] >= 0 and origin[m.end(1) - 1] >= 0:
                    named.append((origin[m.start(1)], origin[m.end(1) - 1] + 1, *_back(origin, start, end, size)))
        text, origin = _edit(text, origin, [(start, end, "[masked]") for _, (start, end) in found])
    # gitleaks' rules last, so a named value holding a provider's token is read whole first; and in text only, as an
    # id is read more narrowly (secret_in_id), and an id one took for a token would drop its item.
    if reading in ("named", "today"):
        for step in (_name_value_edits, _typed_code_edits, _gitleaks_edits):
            edits = step(text)  # read from the text the step before left
            if edits:
                note(edits)
            text, origin = _edit(text, origin, edits)
    if whole:
        added = bytearray(max([c for c in origin if c >= 0] + [c for c in given if c >= 0], default=-1) + 1)
        for start, end in whole:
            for i in range(start, end):
                if given[i] >= 0:
                    added[given[i]] = 1
        hidden, came = _hide(text, origin, added)
        if hidden != text:
            note(whole)
        text, origin = hidden, came
    return text, None if untracked else origin


def _pass(text, rules):
    """One pass of every pattern over `text`, as main reads it."""
    return _run(text, rules)[0]


# A JSON object written in text, with no object inside it, and its string fields, plain or escaped once as JSON in
# JSON writes them: a field's name and its value.
_FLAT_OBJECT = re.compile(r"\{[^{}]{1,4000}\}")
_FIELDS = (re.compile(r'"([^"\\\n]{1,64})"\s*:\s*"((?:[^"\\\n]|\\.){0,2048})"'),
           re.compile(r'\\"([^"\\\n]{1,64})\\"\s*:\s*\\"((?:(?!\\")[^\n]){0,2048})\\"'))


def _name_value_edits(text):
    """Each value a JSON object in text holds apart from its name, as a request's headers do ({"name": "Cookie",
    "value": ...}), Postman's variables ({"key": "password", "value": ...}) and AWS tags ({"Key": "api_key", "Value":
    ...}), masked whole where it is a secret by that name (_pair_secret): an object's value field, in any case, under
    any of its name or key fields, as {"name": "colour", "key": "password", "value": ...} holds one under its key."""
    edits = []
    if "value" not in text.lower():
        return edits
    for obj in _FLAT_OBJECT.finditer(text):
        for fields in _FIELDS:
            found = list(fields.finditer(obj.group()))
            names = [f.group(2) for f in found if f.group(1).lower() in ("name", "key")]
            masked = [(obj.start() + f.start(2), obj.start() + f.end(2), "[masked]") for f in found
                      if f.group(1).lower() == "value" and f.group(2)
                      and any(_pair_secret(name, f.group(2)) for name in names)]
            if masked:
                edits += masked
                break
    return edits


def _named(pattern, span_of, text, reading, note=lambda _: None):
    """Each of `pattern`'s matches, a name, a separator and a value, whose secret `span_of` finds, with that span. In
    text a match that holds none is tried again at its value, which can be the next name, as in "secrets: db_password:
    <value>"; so is one that holds a secret, where the value it masks is a name and its separator whose own value
    follows, as in "passwd: Ppasswd: <password>". Past that the search goes on after the match, as re.sub's does, so a
    long value is read once. An id reads as re.sub reads it."""
    m = pattern.search(text)
    while m:
        span = span_of(m)
        if span:
            yield m, span
            inner = pattern.match(text, m.start(3)) if reading == "named" else None
            # Only at a name that ends in a secret's word and is about none, as main shows the value of any other:
            # "password: password_hash: SHA256", "password: passwordAlgorithm: bcrypt".
            if inner and inner.start(3) >= span[1] and _ENDS_IN_A_SECRET.search(inner.group(1)) \
                    and not _ABOUT_A_PASSWORD.search(inner.group(1)):
                note(inner)  # read on at a masked value, as main does not
                m = inner
            else:
                m = pattern.search(text, m.end())
            continue
        inner = pattern.match(text, m.start(3)) if reading in ("named", "main") else None
        m = inner if inner else pattern.search(text, m.end())


def _token_shapes(patterns, after_a_break=False):
    """The token shapes in `patterns` as one pattern. `after_a_break` leaves out the word boundary before each start,
    for a match where an invisible character stood, which parts a token from the word before it as a space would."""
    shapes = []
    for pattern in patterns:
        source, flags = pattern.pattern, ""
        if source.startswith("(?i)"):
            source, flags = source[4:], "i"
        for boundary in (_NB, _WORD, r"(?<![\d])") if after_a_break else ():
            if source.startswith(boundary):
                source = source[len(boundary):]
                break
        shapes.append(f"(?{flags}:{source})" if flags else f"(?:{source})")
    return re.compile("|".join(shapes))


_TOKENS = {id(rules): (_token_shapes(rules[0]), _token_shapes(rules[0], after_a_break=True))
           for rules in (_TEXT_RULES, _ID_RULES, _MAIN_RULES, _TODAYS_RULES)}


def _scoped(source):
    """A pattern's source with its leading "(?i)", if any, scoped to it, so it joins others in one pattern."""
    return f"(?i:{source[4:]})" if source.startswith("(?i)") else f"(?:{source})"


# Where a secret's reading starts, as the patterns that read it start one, lookbehinds and all: a secret's name and
# what parts it from its value, a scheme word, a link's scheme, a command a password follows, openssl's "-pass" and a
# key's header.
def _reading_starts(keyed, env, flag, prefixed, commands):
    """A pattern for where a secret's reading starts, given the named-value patterns, the first link pattern and the
    commands read."""
    return re.compile("|".join(_scoped(source) for source in (
        keyed.pattern[:-len(_VALUE)], env.pattern[:-len(_VALUE)], flag.pattern[:-len(_VALUE)],
        AUTH_SECRET.pattern[:AUTH_SECRET.pattern.index(_SCHEME_WORD)],
        prefixed.pattern[:prefixed.pattern.index(f"(?:{_CLOSE}")],
        _AUTH_FIELD.pattern[:_AUTH_FIELD.pattern.rindex("(")], rf"(?i){_WORD}(?:bearer|basic){_SCHEME_GAP}",
        rf"(?:(?<![A-Za-z0-9+.-])|{_AFTER_ESCAPE})[a-z][a-z0-9+.-]{{0,31}}:(?:\\?/){{2}}",
        rf"(?i)(?:(?<![A-Za-z0-9+.-])|{_AFTER_ESCAPE})[a-z][a-z0-9+.-]{{0,31}}%(?:25)?3A(?:%(?:25)?2F|/){{2}}",
        rf"(?i)(?:(?<![A-Za-z0-9_.-])|{_AFTER_ESCAPE})(?:" + "|".join(command for command, _, _ in commands)
        + r")(?![A-Za-z0-9_.-])",
        r"(?<![A-Za-z0-9_-])-pass(?:in|out)?[ \t]+['\"]?pass:", "-----BEGIN ")))


_READING_START = _reading_starts(KEYED_SECRET, ENV_KEY_SECRET, FLAG_SECRET, PREFIXED_SECRETS[0], COMMAND_FLAGS)
# Main 032391d's own, where its rows spell a command as it does: "az" and "aws" alone, with no ".exe" or ".cmd".
_TODAYS_READING_START = _reading_starts(KEYED_SECRET, ENV_KEY_SECRET, FLAG_SECRET, PREFIXED_SECRETS[0], [
    (command.replace(_WINDOWS_FILE, ""), flag, always) for command, flag, always in COMMAND_FLAGS])
_WINDOWS_SPELLING = re.compile(r"(?i)\.(?:exe|cmd)")  # all _TODAYS_READING_START lacks
_ESCAPED_PRINTABLE = re.compile(r"\\u00[2-7][0-9a-fA-F]")  # a printable character as JSON escapes it, as "\u006c"
_MAIN_READING_START = _reading_starts(*map(_main_reading, (KEYED_SECRET, ENV_KEY_SECRET, FLAG_SECRET)),
                                      _MAIN_PREFIXED[0], COMMAND_FLAGS[:_MAIN_COMMANDS])  # main's own
# Where one could start: a letter, a digit or "-" after anything but a letter or digit, as "7token is" starts a name.
_MAY_START = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9-])")
_INVISIBLE_RUN = re.compile(f"(?:{INVISIBLE.pattern})+")
_TODAYS_INVISIBLE_RUN = re.compile(f"(?:{_TODAYS_INVISIBLE.pattern})+")  # main 032391d's own (_split_secrets)
# How far a token that starts right after a letter, where an invisible character stood, is read before it counts as a
# longer one: each such start is read once, so repeated starts cost time in proportion to the text.
_GLUED_START_WINDOW = 512


def _mask_once(text, rules=_TEXT_RULES):
    """One pass of every pattern over the text as written, as main reads it, and with it each secret only the text read
    without its invisible characters holds (_split_secrets)."""
    return _once(text, rules)[0]


def _once(text, rules, origin=None, novel=None):
    """_mask_once, and with `origin`, where each character of the result came from in the first text (_edit). The pass
    reads the text as given (_read), never what the view masked, so what it hides stays hidden: the view only adds.
    Where it adds, the characters of the pass's own result that came from the view's spans are hidden (_hide). A text
    whose invisible characters split no secret masks as _read reads it. With `novel` a list, or by default mask()'s own
    (_NOVEL), something is added to it where the pass may read otherwise than main's own: a reading main's lack fired,
    or the view hid anything."""
    if novel is None:
        novel = _NOVEL.get()
    spans = _split_secrets(text, rules) if INVISIBLE.search(text) else []
    if spans and novel is not None:
        novel.append(spans)
    if not spans:
        return _read(text, rules, origin, novel)
    masked, came = _read(text, rules, list(range(len(text))), novel)
    added = bytearray(len(text))
    for start, end in spans:
        added[start:end] = b"\x01" * (end - start)
    masked, came = _hide(masked, came, added)
    return masked, None if origin is None else [origin[c] if c >= 0 else -1 for c in came]


def _read(text, rules, origin=None, novel=None):
    """The text as written read (_run), and as main's own readings read it (_MAIN_RULES), each character either hides
    hidden: no reading added since can show what main's readings hide by reading a part of it first. An id reads as
    _run reads it."""
    if rules[2] not in ("named", "today"):
        return _run(text, rules, origin)
    fired = []
    masked, came = _run(text, rules, list(range(len(text))), fired=fired, look=novel is not None and not novel)
    if fired and novel is not None:
        novel.append(fired)
    if not fired:  # no reading main's lack masked anything, so theirs mask just as this did
        return masked, None if origin is None else [origin[c] if c >= 0 else -1 for c in came]
    added = bytearray(b"\x01") * len(text)
    for c in _run(text, _MAIN_RULES, list(range(len(text))))[1]:
        if c >= 0:
            added[c] = 0
    masked, came = _hide(masked, came, added)
    return masked, None if origin is None else [origin[c] if c >= 0 else -1 for c in came]


def _hide(masked, came, added):
    """`masked`, a pass's result, with the characters that came from a place `added` marks hidden: a placeholder for
    each run of them but one beside a placeholder, which takes them in; the rest stays as the pass left it."""
    hide = [c >= 0 and added[c] for c in came]
    if any(hide):
        out, kept, k, n = [], [], 0, len(masked)
        while k < n:
            j = k
            while j < n and hide[j] == hide[k]:
                j += 1
            if not hide[k]:
                out.append(masked[k:j])
                kept += came[k:j]
            elif not (k > 0 and came[k - 1] < 0 or j < n and came[j] < 0):
                out.append("[masked]")
                kept += [-1] * 8
            k = j
        masked, came = "".join(out), kept
    return masked, came


def _split_secrets(text, rules):
    """Where in `text` a secret reads only across characters a reader does not see, as ordered (start, end) spans of
    the text as written: a token split or interleaved by them, even where its first part is a token by itself, the
    rest of one an older kb masked only up to them ("[masked]<ZWSP>rest"), or a secret's name split by them, whose
    value is masked. The text read without them is the view. In it a token may start right after a letter where one
    stood, as after a space, so one before a key glued to a word reads it ("budget<ZWSP>s<ZWSP>k-..."). A run of them
    just before where a secret's reading starts stays in the view, as a break. A split token ends before the last of
    them ahead of any reading the text as written starts inside it, such as a link's scheme in the token's tail, and a
    split value ends where such a reading starts: the pass over the text as given reads those. The text is also read
    as main 032391d reads it, with its own copy of every table this branch widened that this reading uses: its
    invisible characters (_TODAYS_INVISIBLE), where its readings start (_TODAYS_READING_START, "az.exe login" none),
    its app password (_TODAYS_RULES) and its commands' rows, and what either reading hides is hidden: a token this
    branch keeps apart from a word, main may read glued to it."""
    # All main's own view hides, the text without its invisible characters only (_main_split_spans): where removing a
    # colour code, DEL or a C1 control too glues a token to a word, or a name to a placeholder, main's still reads it.
    seen_by_main = _main_split_spans(text, rules)
    spans = _split_secrets_seen(text, rules, _INVISIBLE_RUN, _READING_START, seen_by_main)
    if not _todays_view_differs(text):
        return spans
    todays = _TODAYS_RULES if rules[2] in ("named", "today") else rules
    merged = []
    for start, end in sorted(spans + _split_secrets_seen(text, todays, _TODAYS_INVISIBLE_RUN, _TODAYS_READING_START, [])):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _todays_view_differs(text):
    """Whether main 032391d's reading of `text` in _split_secrets can differ from this branch's: where its invisible
    characters make other runs, as a surrogate pair, a JSON backspace or anything escaped twice does, and with it its
    app password; where the text spells a program as only this branch reads it, as "az.exe" (_WINDOWS_FILE), and with it
    where a reading starts; or where its rows of a command, which it reads as main does, or this branch's wrappings may
    read a command's name, in the text read without its invisible characters or in a JSON string it holds escaped."""
    return ([m.span() for m in _INVISIBLE_RUN.finditer(text)] != [m.span() for m in _TODAYS_INVISIBLE_RUN.finditer(text)]
            or _WINDOWS_SPELLING.search(text) or _ANY_COMMAND.search(_INVISIBLE_RUN.sub("", text))
            or _ESCAPED_PRINTABLE.search(text))


def _split_secrets_seen(text, rules, run, starts, seen_by_main):
    """_split_secrets in the view without the characters `run` finds, a run before where `starts` finds a reading
    starting kept, with what main's own view hides (`seen_by_main`), as merged (start, end) spans of the text as
    written."""
    v = _Split(text, run, starts)
    gone, view, places, where, removed = v.gone, v.view, v.places, v.where, v.removed
    if not gone:
        return seen_by_main
    tokens, after_a_break = _TOKENS[id(rules)]
    found = [(m.start(), m.end()) for m in tokens.finditer(view) if m.end() > m.start() and not _scheme_words(m)]
    text_only = rules[2] in ("named", "today")  # an id is read without gitleaks' rules, as _run reads it
    found = sorted(found + (_gitleaks_spans(view) if text_only else []))
    lower = view.lower()
    vendored = [rule for rule in _GITLEAKS_AFTER_A_BREAK
                if text_only and (not rule[3] or any(k in lower for k in rule[3]))]
    glued = _glued_tokens(v, found, after_a_break, vendored)

    spans = list(seen_by_main)

    def glued_on(start):  # whether, as written, the view's text from `start` follows a letter or digit it does not see
        at = start + bisect.bisect_right(places, start)
        return 0 < at and at - 1 in removed and text[at - 1].isalnum()

    def ends_at_removed(end):  # whether, as written, the view's text up to `end` runs into a character it does not see
        at = end + bisect.bisect_left(places, end)
        return at < len(text) and at in removed

    for start, end in found + glued:
        if not v.split(start, end):
            # A colour code ends in a letter, so the text as written reads a token right after one as a longer word,
            # and one right after a token stands where the token's end is read, as grep's colour around a line does.
            if glued_on(start) or ends_at_removed(end):
                spans.append(v.raw(start, end))
            continue
        cut = v.cut(start, end)
        if cut:
            spans.append(cut)
    spans += v.values(_named_spans(view, rules))
    merged = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _glued_tokens(v, found, after_a_break, vendored):
    """The tokens in the view of `v` that start right after a letter or digit where removed characters stood, outside
    `found`: by the shapes `after_a_break` holds, and gitleaks' `vendored` rules, each start read once within a window,
    so repeated starts cost time in proportion."""
    view, glued, inside = v.view, [], 0
    for place in v.where:
        while inside < len(found) and found[inside][1] <= place:
            inside += 1
        if inside < len(found) and found[inside][0] < place:
            continue
        if 0 < place < len(view) and (not glued or place >= glued[-1][1]) and view[place - 1].isalnum():
            stop = min(len(view), place + _GLUED_START_WINDOW)
            token = after_a_break.match(view, place, stop)
            if token and token.end() == stop < len(view):
                token = after_a_break.match(view, place)
            if token and token.end() > place and not _scheme_words(token):
                glued.append((place, token.end()))
                continue
            for pattern, group, entropy, _, allowed, stopwords in vendored:
                m = pattern.match(view, place, stop)
                if m and m.end() == stop < len(view):
                    m = pattern.match(view, place)
                span = m and _gitleaks_secret(m, group, entropy, allowed, stopwords)
                if span and span[1] > place:
                    glued.append((max(place, span[0]), span[1]))
                    break
    return glued


def _main_split_spans(text, rules=_TEXT_RULES):
    """Where main's own pass hides what `text` holds only across main's invisible characters: its view, its token
    shapes, an older kb's leftovers and its own named values, as main's _split_secrets reads them; for text by main's
    own readings (_MAIN_RULES), for an id by its own."""
    rules = _MAIN_RULES if rules[2] in ("named", "today") else rules
    v = _Split(text, _MAIN_INVISIBLE_RUN, _MAIN_READING_START)
    if not v.gone:
        return []
    tokens, after_a_break = _TOKENS[id(rules)]
    found = [(m.start(), m.end()) for m in tokens.finditer(v.view) if m.end() > m.start() and not _scheme_words(m)]
    rests = [(m.start(), m.end()) for m in _GLUED_RUN.finditer(v.view)]
    spans = [cut for start, end in found + _glued_tokens(v, found, after_a_break, []) + rests
             if v.split(start, end) for cut in [v.cut(start, end)] if cut]
    return spans + v.values(_named_spans(v.view, rules))


def _main_once(text, origin):
    """One pass as main's own mask makes it, and where each character of its result came from: main's readings over
    the text as written (_MAIN_RULES), and what its own view holds split (_main_split_spans)."""
    masked, came = _run(text, _MAIN_RULES, list(range(len(text))))
    spans = _main_split_spans(text) if _MAIN_INVISIBLE_RUN.search(text) else []
    if spans:
        added = bytearray(len(text))
        for start, end in spans:
            added[start:end] = b"\x01" * (end - start)
        masked, came = _hide(masked, came, added)
    return masked, [origin[c] if c >= 0 else -1 for c in came]


def _passes(text, step):
    """`step`, a pass that tracks where each character came from, repeated until it changes nothing: (the text, where
    each of its characters came from in `text`, whether it still changed after MAX_PASSES). The pass that changes
    nothing is counted too, as it can put a placeholder down for one, with what was glued to it kept
    (_glued_run_edits)."""
    origin = list(range(len(text)))
    for _ in range(MAX_PASSES):
        masked, came = step(text, list(range(len(text))))
        origin = [origin[c] if c >= 0 else -1 for c in came]
        if masked == text:
            return text, origin, False
        text = masked
    return text, origin, True


def _main_hidden(text):
    """What main's own mask hides of `text`, a mark at each character none of its result came from, and, for a text
    that names a command, all main 33c82f6's mask hides of it too (_todays_hidden), which reads #29's rows of
    COMMAND_FLAGS as this mask does not. Where it still changes after MAX_PASSES, its mask() masks whole all between
    what the text and the last pass share at both ends, and all that is marked too."""
    hidden = _hidden(text, *_passes(text, _main_once))
    return _todays_hidden(text, hidden) if _ANY_COMMAND.search(text) else hidden


def _todays_hidden(text, mains):
    """What main 33c82f6's mask hides of `text`, marked as _main_hidden marks it, given what main's own mask hides of
    it (`mains`): its passes (_TODAYS_RULES, each with what main's own readings hide of it hidden too, as its _read
    hides them), then, as its _masked does, all `mains` marks hidden and the passes made again. That holds all
    `mains` marks."""
    step = lambda t, o: _once(t, _TODAYS_RULES, o, [])
    masked, origin, still = _passes(text, step)
    if not still and any(c >= 0 and mains[c] for c in origin):
        joined, came = _hide(masked, origin, mains)
        masked, after, still = _passes(joined, step)
        origin = [came[c] if c >= 0 else -1 for c in after]
    hidden = _hidden(text, masked, origin, still)
    return bytes(a | b for a, b in zip(hidden, mains))


def _hidden(text, masked, origin, still):
    """A mark at each character of `text` none of `masked` came from (`origin`), and where the passes `still` changed,
    all between what `text` and `masked` share at both ends."""
    hidden = bytearray(b"\x01") * len(text)
    for c in origin:
        if c >= 0:
            hidden[c] = 0
    if still:
        head = len(os.path.commonprefix([text, masked]))
        tail = len(os.path.commonprefix([text[head:][::-1], masked[head:][::-1]]))
        hidden[head:len(text) - tail] = b"\x01" * (len(text) - tail - head)
    return hidden


def _named_spans(view, rules):
    """The named values a pass masks in `view`, as (name start, name end, masked start, masked end) in the view, read as
    _run reads them: after the secrets known by their shape and an older kb's leftovers are masked, each pattern after
    the one before, so a value ends at a token's placeholder as main ends it. As _read reads the text as written, where
    a reading main's own lack masked anything, theirs read the view too, so none can take a split name first."""
    spans, fired = [], []
    _run(view, rules, list(range(len(view))), spans, fired if rules[2] in ("named", "today") else None)
    if fired:
        _run(view, _MAIN_RULES, list(range(len(view))), spans)
    return spans


def _reading_inside(text, start, end, starts=_READING_START, removed=()):
    """Where in text[start:end] the text as written starts a reading, or None; not inside the characters a view
    removed, as the letters of an escaped colour code ("\\x9b31m") would start a link's "x9b31m://"."""
    for m in _MAY_START.finditer(text, start, end):
        if m.start() not in removed and starts.match(text, m.start()):
            return m.start()
    return None


class _Split:
    """`text` read without the runs of characters `runs` finds, but for one right before where a reading starts, which
    stays as a break: the view, and the way from a stretch of it back to the text as written."""

    def __init__(self, text, runs, starts=_READING_START):
        self.text, self.starts = text, starts
        self.gone = [i for run in runs.finditer(text) if not starts.match(text, run.end())
                     for i in range(run.start(), run.end())]
        self.view = "".join(text[a + 1:b] for a, b in zip([-1] + self.gone, self.gone + [len(text)]))
        # The view's i-th character sits after every removed character whose place in the view is i or before.
        self.places = [i - n for n, i in enumerate(self.gone)]
        self.where = sorted(set(self.places))
        self.removed = set(self.gone)

    def split(self, start, end):
        """Whether a removed character lies inside the view's [start, end)."""
        n = bisect.bisect_right(self.where, start)
        return n < len(self.where) and self.where[n] < end

    def raw(self, start, end):
        """The view's [start, end) as the text as written holds it, without the removed characters at either edge."""
        return start + bisect.bisect_right(self.places, start), end + bisect.bisect_left(self.places, end)

    def cut(self, start, end):
        """The view's split [start, end) as written, cut before the run of removed characters last ahead of a reading
        the text as written starts in it past the first run, which stays as a break; None where, so cut, no removed
        character splits it any more, as the text as written then reads its first part as main does."""
        gone = self.gone
        start, end = self.raw(start, end)
        at = _reading_inside(self.text, gone[bisect.bisect_left(gone, start)] + 1, end, self.starts, self.removed)
        if at is not None:
            end = gone[bisect.bisect_left(gone, at) - 1] if bisect.bisect_left(gone, at) else start
            while end - 1 in self.removed:
                end -= 1
            if start >= end:
                return None
        return (start, end) if bisect.bisect_left(gone, start) < bisect.bisect_left(gone, end) else None

    def values(self, named):
        """The values of `named`, _named_spans in the view, as the text as written holds them: only of a split name, or
        one parted from its separator, as the text as written reads a value through invisible characters already, and
        one past the separator, as on a blank line after "password:", splits no name; up to where a reading the text
        as written starts inside; and of a name parted by a character main sees, strictly."""
        out = []
        for name_start, name_end, start, end in named:
            if not self.split(name_start, name_end + 1) or start >= end:
                continue
            parted = {_READING_START: _NEW_INVISIBLE, _TODAYS_READING_START: _TODAYS_NEW_INVISIBLE}.get(self.starts)
            if parted and parted.search(self.text, *self.raw(name_start, name_end + 1)):
                # Parted by a colour code, DEL or a C1 control, which main sees, as grep's colour parts the word it
                # found: read strictly, only a name that ends in a secret's word and is about none
                # ("password_ESC[31mpolicy", "x-authESC[31m-flow" are not), and but for a password's, only a value
                # shaped like one.
                name, value = self.view[name_start:name_end], self.view[start:end]
                if not _ENDS_IN_A_SECRET.search(name) or _ABOUT_A_PASSWORD.search(name) \
                        or not PASSWORD_NAME.search(name) and not _password_shaped(value):
                    continue
            start, end = self.raw(start, end)
            at = _reading_inside(self.text, start, end, self.starts, self.removed)
            if at is not None:
                end = at
            if start < end:
                out.append((start, end))
        return out




def _back(origin, start, end, size):
    """The stretch of the first text, `size` long, that text[start:end] came from (_edit): a placeholder's characters
    stand for the stretch it replaced, from past the character before it to the one after."""
    i = start
    while i > 0 and origin[i] < 0:
        i -= 1
    head = origin[i] if origin[i] >= 0 and i == start else origin[i] + 1 if origin[i] >= 0 else 0
    j = end - 1
    while j < len(origin) - 1 and origin[j] < 0:
        j += 1
    tail = origin[j] + 1 if origin[j] >= 0 and j == end - 1 else origin[j] if origin[j] >= 0 else size
    return head, max(head, tail)


# How many passes mask() makes at most, and how often, in this process, a text still changed at the last one.
MAX_PASSES = 8
capped = 0
# mask()'s note of whether a pass read otherwise than main's own could (_once's `novel`), for its passes alone.
_NOVEL = contextvars.ContextVar("kb_mask_novel", default=None)
# Whether a pass reads the wrappings (_wrapping_edits). Switched off, the mask reads as it did before them, as main
# does, for _unwrapped_hidden.
_WRAPPINGS = contextvars.ContextVar("kb_mask_wrappings", default=True)


def _unwrapped_hidden(text):
    """What the mask hides of `text` with the reading of wrappings switched off, a mark at each character none of its
    result came from; where it still changes after MAX_PASSES, the region still changing is hidden whole, as
    mask_with_origin hides it."""
    token = _WRAPPINGS.set(False)
    try:
        _, origin = mask_with_origin(text)
    finally:
        _WRAPPINGS.reset(token)
    hidden = bytearray(b"\x01") * len(text)
    for c in origin:
        if c >= 0:
            hidden[c] = 0
    return hidden


def _masked(text, keys=None):
    """mask()'s passes, and each private key written as base64 (_encoded_key_spans, or `keys`) hidden over what they
    leave, so everything the passes hide stays hidden. The keys are hidden when the passes are still changing too, so
    the one placeholder the limit then puts down, over what differs from `text`, covers each key. Then the passes
    read the text again, as the next mask() reads it, kb's store among them: an older kb's rest of a key can follow a
    key's placeholder there, so masked text masks to itself. (the text, where each of its characters came from in
    `text`, whether it still changed after MAX_PASSES)."""
    masked, origin, still = _passes_masked(text)
    keys = _encoded_key_spans(text) if keys is None else keys
    if not keys:
        return masked, origin, still
    hidden = bytearray(len(text))
    for start, end in keys:
        hidden[start:end] = b"\x01" * (end - start)
    masked, origin = _hide(masked, origin, hidden)
    if still:
        return masked, origin, still
    again, after, still = _passes_masked(masked)
    return again, [origin[c] if c >= 0 else -1 for c in after], still


def _passes_masked(text):
    """The passes of mask(): (the text, where each of its characters came from in `text`, whether it still changed
    after MAX_PASSES). Where a pass read otherwise than main's own could (_once's `novel`), main's own mask is made too
    (_main_hidden), and every character it hides is hidden here: no reading added since can show what main hides, in
    a later pass either, where a placeholder one put down ends a value main reads on through. Where the reading of
    wrappings masked anything, the mask without it is made too (_unwrapped_hidden), and all it hides is hidden here,
    even where the passes still change: a field of a container read whole and masked as a secret can hold a
    command's name, which the readings of commands after it read across the container ("{'password': 'keytool'}
    -storepass ..."), as main's do."""
    novel = []
    masked, origin, still = _passes(text, lambda t, o: _once(t, _TEXT_RULES, o, novel))
    if not novel:
        return masked, origin, still
    unwrapped = _unwrapped_hidden(text) if any(_WRAPPINGS in fired for fired in novel) else None
    if still:
        if unwrapped is not None:
            masked, origin = _hide(masked, origin, unwrapped)
        return masked, origin, still
    hidden = _main_hidden(text)
    if unwrapped is not None:
        hidden = bytes(a | b for a, b in zip(hidden, unwrapped))
    if not any(c >= 0 and hidden[c] for c in origin):
        return masked, origin, False
    joined, came = _hide(masked, origin, hidden)
    masked, after, still = _passes(joined, lambda t, o: _once(t, _TEXT_RULES, o))
    return masked, [came[c] if c >= 0 else -1 for c in after], still


def mask_with_origin(text):
    """mask(), and where each character of the result came from in `text`, -1 for a placeholder's (kb.mask._edit)."""
    original = text
    text, origin, still = _masked(text)
    if not still:
        return text, origin
    # Still changing, as mask() then reads it: the region still changing becomes one placeholder.
    head = len(os.path.commonprefix([original, text]))
    tail = len(os.path.commonprefix([original[head:][::-1], text[head:][::-1]]))
    return (original[:head] + "[masked]" + original[len(original) - tail:],
            list(range(head)) + [-1] * 8 + list(range(len(original) - tail, len(original))))


def mask(text):
    """The text with every secret replaced by "[masked]". A placeholder can make the text next to it read as a secret
    or as a key's leftovers, so masking repeats until a pass changes nothing: masked text masks to itself, and text
    with no secret costs one pass. No pattern matches a placeholder alone, so it stops; should a text still change at
    the last pass, the region still changing is masked whole, and counted in `capped`. Where a pass read otherwise than
    main's own could, the text is masked again as _masked masks it, all main's own mask hides hidden too."""
    global capped
    original, novel, keys = text, [], _encoded_key_spans(text)
    if keys:  # hidden over the passes, which then need to know where each character came from
        text, _, still = _masked(original, keys)
    else:
        marker = _NOVEL.set(novel)
        try:
            for _ in range(MAX_PASSES):
                masked = _mask_once(text)
                if masked == text:
                    break
                text = masked
            else:
                text = None
        finally:
            _NOVEL.reset(marker)
        still = text is None
        if novel:
            text, _, still = _masked(original, keys)
        elif still:
            text = masked
    if not still:
        return text
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
    itself, such as "password", is read apart too: the metadata says it is a name, and an id with no such name
    recorded, such as a note's "password:...", is still read whole."""
    name = names[0] if names else None
    if isinstance(name, str) and name and text.startswith(f"{name}:") and not text.startswith("//", len(name) + 1):
        return secret_in_id(name) or secret_in_id(text[len(name) + 1:], names[1:])
    if any(_basic_credential(m.group(1)) for view in (*_seen_views(text), _MAIN_INVISIBLE_RUN.sub("", text))
           for m in _BASIC_VALUE.finditer(view)):
        return True
    return _mask_once(text, _ID_RULES) != text or bool(_encoded_key_spans(text))


# The headers kb reads in text as "name: value", a cookie or a credential: a field so named, or a name and a value
# held apart under one ({"name": "Cookie", "value": ...}, ["Authorization", ...]), is read so too. A cookie's own
# content, a name and "=", and a credential's last word, which a list of two words, as tags are, does not hold.
_HEADER_NAMES = frozenset(("cookie", "set-cookie", "authorization", "proxy-authorization"))
_COOKIE_CONTENT = re.compile(r"[^\s=;,]+=[^\s;,]")


def _as_header(name, value):
    """`value` masked as kb masks it after "name: " in text, or None when `name` is no header _HEADER_NAMES holds."""
    if name.strip().lower() not in _HEADER_NAMES:
        return None
    head = f"{name.strip()}: "
    masked = mask(head + value)
    return masked[len(head):] if masked.startswith(head) else "[masked]"


def _pair_secret(name, value, listed=False):
    """Whether a value held apart from its name is a secret by that name: under a header's (_HEADER_NAMES), when the
    header read in text hides any of it, or under a secret's field name, when the field's rule says so (mask_all).
    `listed` for a list of the two, which may be two words, as tags are: then the value holds the header's own
    content, a cookie's "name=" or a credential, or is shaped like a credential under a secret's name. The value is
    judged as each view sees it (_seen_views)."""
    return any(_pair_secret_seen(name.strip(), seen.strip(), listed) for seen in _seen_views(value))


def _pair_secret_seen(name, value, listed):
    """_pair_secret for a value without the characters a reader does not see, as one view reads it."""
    lowered = name.lower()
    if lowered in _HEADER_NAMES:
        if listed and not (_COOKIE_CONTENT.search(value) if "cookie" in lowered else
                           value and _password_shaped(value.split()[-1])):
            return False
        return _as_header(name, value) != value
    if not _SECRET_FIELD.match(name) or lowered in ("pass", "pwd") or _ABOUT_A_PASSWORD.search(name):
        return False  # no secret's name, a word alone, or one about a password, as "password_policy" is
    if listed:
        return _password_shaped(value)
    return _looks_secret(name, "=", value)


def _pair_value(name, value, masked, listed=False):
    """A value held apart from its name, `masked` as mask_all masked it alone: whole, where it is a secret by that name
    (_pair_secret)."""
    if isinstance(name, str) and isinstance(value, str) and _pair_secret(name, value, listed):
        return "[masked]"
    return masked


def mask_all(value, key=None):
    """mask() over every string in a value of dicts, lists and strings, such as an item's metadata. Only the
    secret-looking parts change, so a link or a path still points where it did. A value is masked with its key in
    front, as "key=value", so {"password": "..."} is caught by its field name as it would be in text; and under a
    header's name, as "name: value" too (_as_header), as {"Cookie": "session=..."} is a cookie. A name and a value
    held apart are read together (_pair_value)."""
    if isinstance(value, str):
        if key is None:
            return mask(value)
        if _SECRET_FIELD.match(key) and _looks_secret(key, "=", value.strip()) and (
                key.lower() not in ("pass", "pwd") or _short_name_secret(value.strip())):
            return "[masked]"  # the whole value, which may hold a space or a quote that would end it in text
        masked = mask(f"{key}={value}")
        head = mask(key) + "="
        plain = masked[len(head):] if masked.startswith(head) else mask(value)
        header = _as_header(key, value)
        if header is None or header == value or header == plain:
            return plain
        return header if plain == value else "[masked]"
    if isinstance(value, dict):
        # "pass" and "pwd" name a password only beside a user's name, as in text; alone, its value is read alone.
        user = any(isinstance(k, str) and _USER_KEY.fullmatch(k.strip()) for k in value)
        out, keys, numbered = {}, {}, {}
        for k, v in value.items():
            masked = mask(k) if isinstance(k, str) else k
            if masked in out:  # two keys masked alike, as two tokens are: the later is numbered, so no value is lost
                n = numbered.get(masked, 1) + 1  # going on from the last number given, so many cost no more each
                while f"{masked} ({n})" in out:
                    n += 1
                numbered[masked] = n
                masked = f"{masked} ({n})"
            keys[k] = masked
            out[masked] = mask_all(v, k if isinstance(k, str) and (user or k.lower() not in ("pass", "pwd")) else None)
        names = [k for k in value if isinstance(k, str) and k.lower() in ("name", "key")]
        for field in (k for k in value if isinstance(k, str) and k.lower() == "value"):
            for name in names:  # under any of its name or key fields
                out[keys[field]] = _pair_value(value[name], value[field], out[keys[field]])
        return out
    if isinstance(value, (list, tuple)):
        return _masked_items(value, key)
    if key is not None and isinstance(value, (int, float)) and not isinstance(value, bool) \
            and mask(f"{key}={value}") != f"{mask(key)}={value}":
        return "[masked]"  # a number its field name marks as secret, such as a numeric passcode
    return value


def _masked_items(items, key):
    """mask_all over a list's items, each read alone, and the items a command given as its arguments holds a secret in
    read with the command line before them (_flag_values)."""
    out = [mask_all(v, key) for v in items]
    if len(items) == 2:  # a name and its value, as a list of headers holds them
        out[1] = _pair_value(items[0], items[1], out[1], listed=True)
    for i, masked in _flag_values(items).items():
        out[i] = _joined_masks(items[i], out[i], masked) if isinstance(out[i], str) else masked
    return out


def _joined_masks(original, one, other):
    """`original` with every stretch masked that `one` or `other`, two readings of it, masks: a reading only adds
    masks. Where a reading cannot be placed in the original, the one that masks more of it stands."""
    parts = [_masked_parts(original, version) for version in (one, other)]
    if None in parts:
        return min((one, other), key=lambda version: len(version.replace("[masked]", "")))
    hidden = sorted(parts[0] + parts[1])
    joined = []
    for start, end in hidden:
        if joined and start <= joined[-1][1]:
            joined[-1] = (joined[-1][0], max(joined[-1][1], end))
        else:
            joined.append((start, end))
    out, at = [], 0
    for start, end in joined:
        out += [original[at:start], "[masked]"]
        at = end
    return "".join(out) + original[at:]


_PROBE = "Qz8vRt2LmX9kWp4Ys7Nb3Hc"  # a made-up value shaped like a password, long enough for every reading


@functools.lru_cache(maxsize=4096)
def _may_take_a_secret(flag):
    """Whether a list item `flag` may be a flag whose next item a reading takes for a secret: a one-letter flag, which
    only its command says ("-p"), a flag one of COMMAND_FLAGS' rows reads, or one the readings of text read by itself
    ("--password", "--api-key"). The rest ("-J-Xmx512m", "--port", "-v") never mask the item after them. A one-letter
    flag is read by the same row and probe check, not taken for a password on sight, so a list of many plain one-letter
    options costs no context mask for each; the answer is cached."""
    lines = (f" {flag} {_PROBE}", f" {flag} deploy:{_PROBE}")  # a value, and curl's "user:password"
    if any(row.search(line) for _, row, _ in _COMMANDS for line in lines):
        return True
    return _PROBE not in mask(f"{flag} {_PROBE}") or _PROBE not in mask(f"{flag}={_PROBE}")


def _shells_open(items):
    """For each index m, whether a shell is among items[:m + 1] with only options after it up to m ("bash --noprofile",
    "/bin/sh -eu"), in one pass: a -c flag at m + 1 is that shell's own ("bash --noprofile -c ...")."""
    found, open_ = [], False
    for item in items:
        is_text = isinstance(item, str)
        open_ = bool(is_text and _SHELL.search(item)) or open_ and is_text and item.startswith("-")
        found.append(open_)
    return found


def _flag_values(items):
    """{index: the item masked} for each item of a list that the command line before it reads more of as a secret than
    the item alone: an item after a flag, read after the line of up to 64 items before it and, past those, the items
    around where each command is first and last named before it (_COMMAND_NAME), so an option's value that names one
    ("-alias", "mongo") takes no other command's place: a command given as its arguments holds a flag and its value in
    two items, as ["login", "--password", "..."] or ["mongosh", "-u", "admin", "-p", "..."] does. The items are joined
    as a shell quotes them, so each keeps its bounds: a flag inside one ("note: -p release-2026") or an apostrophe
    ("team's cert") reads as part of it. Where that line reads more of the item as a secret than the item alone does,
    the item, one argument, is masked whole when what the line masks starts it, as a flag's value; when it starts inside
    it, as a flag the item only quotes does, the item keeps its text up to the mask, as the line reads it. An item that
    starts with a flag and holds a space ("-storepass ...") holds a flag and its value in one, but after a flag, whose
    value it may be ("--comment", "-p release-2026"): read unquoted after that line, it keeps its flag."""
    found, named = {}, {}  # named: for each command named before the item read, the first and last items naming it
    shells, aws = _shells_open(items), _aws_sets(items)
    cut = {}  # each long item as the line before a flag reads it, cut once (_lead_item)
    for i in range(1, len(items)):
        flag, item = items[i - 1], items[i]
        if isinstance(flag, str):
            for m in _COMMAND_NAME.finditer(flag):
                named.setdefault(m.group(1).lower(), [i - 1, i - 1])[1] = i - 1
        if not (isinstance(item, str) and item):
            continue
        is_flag = isinstance(flag, str) and flag.startswith("-") and len(flag) <= 64
        # A shell's -c script is a command line of its own, read as one. The shell may be any earlier item, with only
        # options ("--noprofile", "-eu") between it and the -c flag; this scan runs only for a -c flag, few per list.
        if is_flag and _SHELL_SCRIPT_FLAG.match(flag) and i >= 2 and shells[i - 2]:
            read = mask(item)
            if read != item:
                found[i] = read
            continue
        # aws configure set's secret setting names its value a secret though the name starts with no "-", so it
        # counts as a flag.
        after_flag = is_flag and _may_take_a_secret(flag) or isinstance(flag, str) and bool(
            _AWS_SETTING_NAME.match(flag)) and aws[i - 1]
        glued = not is_flag and item.startswith("-") and len(item.split(None, 1)) == 2  # after a flag: its value
        if not (after_flag or glued):
            continue
        start = i
        while start > 0 and i - start < 64 and isinstance(items[start - 1], str):
            start -= 1
        lead = list(items[start:i])  # the 64 items before it whole, as main reads them
        if start > 0 and isinstance(items[start - 1], str):  # past the 64 items, a few around each command's name
            near = sorted({j for k in {k for pair in named.values() for k in pair if k < start}
                           for j in range(max(0, k - 2), min(k + 3, start))})
            lead = [_lead_item(items, j, cut) for j in near if isinstance(items[j], str)] + lead
        line = re.sub(r"[\r\n]", " ", " ".join(map(shlex.quote, lead)))  # the lead is context; a newline in it ends nothing
        head = mask(line) + " "
        if after_flag:
            quoted = shlex.quote(item)
            read = mask(f"{line} {quoted}")
            if not read.endswith(" " + mask(quoted)):
                tail = read[len(head):] if read.startswith(head) else None
                if tail is None or tail.lstrip("'\"").startswith("[masked]"):
                    found[i] = "[masked]"  # the flag's value, one argument, masked whole
                    continue
                try:  # a flag inside the item ("note: -u feature:preview"): it keeps its text up to what the line masks
                    parts = shlex.split(tail)
                except ValueError:
                    parts = []
                found[i] = mask(parts[0]) if len(parts) == 1 else "[masked]"
                continue
        if glued:
            read = mask(f"{line} {item}")
            tail = read[len(head):] if read.startswith(head) else "[masked]"
            if tail != mask(item):
                found[i] = tail
    return found


# Past this many characters, an item read beside where a command is named, past the 64 items before a flag
# (_flag_values), is read as what it changes for a later flag (_lead_item): a payload beside a command's name ("--data"
# and 4 KB) is no flag's context, and read again before every later flag it cost time in the square of the list. The
# 64 items before a flag are read whole, as main reads them, each for at most 64 flags.
_LEAD_ITEM = 256
# What ends the line of a command main's own rows read (_LINE), in an item as the line before a flag holds it, where a
# line break is a space.
_LINE_STOP = re.compile(r"[|;]|\\[nr]")
# Each of COMMAND_FLAGS' commands but a .netrc line's, by a name of it and its flag with a value: put right after a long
# item, it shows whether the command's line, read from where the item names it, runs on past the item (_lead_item).
_LEAD_PROBES = (("curl", ("-u", "probe:Zq7vProbe9x")), ("mysql", ("-pZq7vProbe9x",)), ("sshpass", ("-p", "Zq7vProbe9x")),
                ("docker login", ("-p", "Zq7vProbe9x")), ("redis-cli", ("-a", "Zq7vProbe9x")),
                ("mongosh", ("-p", "Zq7vProbe9x")), ("keytool", ("-storepass", "Zq7vProbe9x")),
                ("aws configure set", ("aws_secret_access_key", "Zq7vProbe9x")))


def _read_after(quoted, probe):
    """Whether `probe`, a flag and its value, reads otherwise right after `quoted` than it does alone, as the line
    before a flag reads the flag's value (_flag_values)."""
    tail = " ".join(map(shlex.quote, probe))
    return not mask(f"{quoted} {tail}").endswith(" " + tail)


def _lead_item(items, j, cut):
    """items[j], beside where a command is named, as the line before a later flag past the 64 items before it reads it:
    whole up to _LEAD_ITEM characters; past that, as what it changes for a later flag, read once for the list (`cut`):
    a ";" where it holds what ends the line of a command main's own rows read across it (_LINE_STOP), then the name of
    each command it names whose flag and a value, put right after it, read otherwise than after a word (_LEAD_PROBES):
    that command's line, read from where the item names it, runs on past the item. For one of main's own rows, its
    line (_LINE) runs from a place the item names it to the item's end too, as another command's line after a line
    stop can read the probe ("sshpass; mongosh ..."). So every command it names takes a later flag as when the item is
    read whole, and none whose line ends inside it ("mongosh ...; exec nc %h %p") does, and each later flag reads a
    few words of it."""
    item = items[j]
    if len(item) <= _LEAD_ITEM:
        return item
    if j not in cut:
        names = []
        if _ANY_COMMAND.search(item):
            quoted = re.sub(r"[\r\n]", " ", shlex.quote(item))  # as the line holds it (_flag_values)
            for n, ((name, probe), (line, _, _)) in enumerate(zip(_LEAD_PROBES, _COMMANDS)):
                if n < _MAIN_COMMANDS and not any(m.end() == len(quoted) for m in line.finditer(quoted)):
                    continue  # its line ends inside the item, wherever another command's line runs on
                if line.search(quoted) and _read_after(quoted, probe) and not _read_after("x", probe):
                    names.append(name)
        cut[j] = "".join(["...", ";" if _LINE_STOP.search(item) else ""] + [" " + name for name in names])
    return cut[j]


# A tool that types into a page or fills a form's fields, as browser automation names one: Playwright's browser_type
# and browser_fill_form, Chrome DevTools' fill and fill_form, Puppeteer's puppeteer_fill and puppeteer_type, and others.
_TYPING_TOOL = re.compile(r"(?i)(?:browser_|puppeteer_|playwright_)?(?:type|type_text|fill|fill_form|fill_field|"
                          r"press_sequentially|input_text|set_value)")
# A field label's words, as a page or a selector writes them: split at anything but a letter or digit, and where
# camel case or a run of capitals turns ("newPassword", "APIToken"), each in lower case.
_LABEL_WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
# A label asks for a secret where it names one and nothing else: a secret's word or run of words, and besides it only
# a form's own words, joining words, numbers, and a credential's kind before the secret, or after a mark or a joining
# word ("Password textbox", "Enter your PIN", "API token (required)", "Password for admin account", "Bot token
# (Discord)"). Any other word, or a kind right after the secret, makes it a field about something else ("Password
# (algorithm)", "Token type", "Token service", "Map pin", "Search secrets", "Max tokens", "One-time donation").
# "tokens" counts them unless a credential's kind names them ("Tokens: 4096", "API tokens"). A run's own words, as
# "one", "time" and "recovery" are, count only in their run ("Token time" is about a token).
_SECRET_WORDS = frozenset(("password", "passwords", "passwd", "pwd", "passcode", "passphrase", "pin", "otp", "secret",
                           "secrets", "token", "tokens", "credential", "credentials", "cvc", "cvv", "2fa", "mfa",
                           "apikey", "accesskey", "privatekey"))
_SECRET_RUNS = (("api", "key"), ("access", "key"), ("private", "key"), ("one", "time", "code"),
                ("one", "time", "password"), ("one", "time", "passcode"), ("one", "time", "pin"),
                ("verification", "code"), ("security", "code"), ("2", "fa"), ("recovery", "phrase"), ("seed", "phrase"))
_JOINING_WORDS = frozenset(("to", "for", "of", "from", "in", "on", "at", "with", "by", "and", "or"))
_FORM_WORDS = frozenset((
    "enter", "retype", "paste", "input", "provide", "confirm", "confirmation", "re", "repeat", "verify", "please",
    "your", "the", "a", "an", "my", "this", "new", "current", "old", "valid", "again", "here", "below", "required",
    "optional", "continue", "login", "log", "sign", "signin", "field", "fields", "textbox", "text", "box", "edit",
    "entry", "area", "form", "control", "value", "key", "keys", "code", "codes", "phrase", "digit", "digits", "four",
    "five", "six", "eight"))
_SECRET_KINDS = frozenset((
    "api", "access", "auth", "authentication", "authorization", "bearer", "personal", "refresh", "session", "security",
    "secret", "bot", "oauth", "csrf", "xsrf", "jwt", "deploy", "deployment", "registry", "service", "app", "application",
    "admin", "webhook", "integration", "account", "cli", "sso", "private", "verification", "user", "github", "gitlab",
    "bitbucket", "npm", "pypi", "slack", "discord", "telegram", "vercel", "cloudflare", "huggingface", "hf", "docker",
    "card", "bank", "atm", "sim", "debit", "credit", "wallet", "phone", "device", "transfer", "transaction", "payment",
    "parental", "voicemail", "email", "mail", "wifi", "wi", "fi", "database", "db", "root", "master", "network", "vault",
    "keystore", "ssh", "gpg", "pgp", "smtp", "ftp", "sudo", "server", "remote"))
# Words that, in any of a field's labels, make it one about a secret or one that finds secrets, as another label may
# say so beside one that names the secret alone (a "password_hint" name beside a "Password" label).
_ABOUT_WORDS = frozenset((
    "name", "names", "description", "descriptions", "label", "labels", "note", "notes", "hint", "hints", "question",
    "questions", "type", "types", "id", "ids", "count", "counts", "length", "expiry", "expiration", "expires", "expiring", "strength",
    "policy", "policies", "requirement", "requirements", "reset", "manager", "size", "format", "usage", "created",
    "updated", "scope", "scopes", "permission", "permissions", "max", "maximum", "min", "minimum", "limit", "limits",
    "budget", "budgets", "quota", "quotas", "per", "total", "number", "remaining", "used", "window", "context", "cost",
    "price", "rate", "rates", "search", "find", "filter", "query", "lookup", "browse", "sort", "show", "hide", "reveal",
    "generate", "copy", "toggle", "rename", "delete", "remove", "revoke", "describe"))
# A selector's attribute and its "=", read from where its name starts only, so a run of names costs time in proportion.
_SELECTOR_ATTRIBUTE = re.compile(r"(?<![\w-])([\w-]+)\s*[~|^$*]?=")
_SELECTOR_NAMES = frozenset(("name", "id", "type", "for", "class", "placeholder", "aria-label"))


def _without_attribute_names(label):
    """A selector with its own attribute names taken out ("input[name=password]" reads as "input[ password]")."""
    return _SELECTOR_ATTRIBUTE.sub(
        lambda m: " " if m.group(1).lower() in _SELECTOR_NAMES or m.group(1).lower().startswith("data-") else m.group(0),
        label)


# A label's words and the marks between them, "-" and "_" joining a compound's words: a mark, or a joining word,
# ends what follows a secret's word.
_LABEL_PART = re.compile(r"[A-Za-z0-9_-]+|[^A-Za-z0-9\s_-]+")
_LABEL_PART_MARK = re.compile(r"[^A-Za-z0-9\s_-]+")


def _label_words(label):
    """A label's words, in lower case, and "" where a mark stands."""
    words = []
    for part in _LABEL_PART.finditer(label):
        if _LABEL_PART_MARK.fullmatch(part.group(0)):
            words.append("")
        else:
            words += [word.lower() for word in _LABEL_WORD.findall(part.group(0))]
    return words


def _typing_verb(words):
    """Where the label's "type" is its verb, a form's word ("Type your password", "Re-type password", "Please type
    your PIN", "#typePassword"), not the secret's kind ("Password type", "Type of password", "Type: password"): after
    only a form's words, with a word after it that is no joining word. -1 where it has none."""
    k = 0
    while k < len(words) and not words[k]:
        k += 1
    while k < len(words) and words[k] in _FORM_WORDS:
        k += 1
    if k + 1 < len(words) and words[k] == "type" and words[k + 1] and words[k + 1] not in _JOINING_WORDS:
        return k
    return -1


def _names_a_secret(label):
    """Whether one label names a secret and nothing else: a secret's word or run of words, and besides it only a form's
    own words, joining words, numbers, and a credential's kind (read with a neighbour as well, as camel case splits
    "GitHub") before the secret or after a mark or joining word. One pass, so a long label costs time in proportion to
    its length."""
    words = _label_words(label)  # each word, and "" where a mark stands
    verb = _typing_verb(words)
    in_run = set()
    for k in range(len(words)):
        for run in _SECRET_RUNS:
            if tuple(words[k:k + len(run)]) == run:
                in_run.update(range(k, k + len(run)))
    secret = kind = after_secret = False
    for k, word in enumerate(words):
        if not word or word in _JOINING_WORDS:  # a mark or a joining word: what follows is the secret's no more
            after_secret = False
            continue
        if word in _SECRET_WORDS or k in in_run:
            secret = secret or word != "tokens"
            kind = kind or word == "secret"
            after_secret = True
            continue
        if (word in _SECRET_KINDS or k and words[k - 1] + word in _SECRET_KINDS
                or k + 1 < len(words) and word + words[k + 1] in _SECRET_KINDS) and not after_secret:
            kind = True
        elif word not in _FORM_WORDS and not word.isdigit() and k != verb:
            return False
    return secret or kind and "tokens" in words


def _asks_for_a_secret(labels):
    """Whether a field's labels ask for a secret: one names a secret and nothing else (_names_a_secret), and none is
    about one or finds them (_ABOUT_WORDS, but "type" as the label's verb, _typing_verb), read word by word after a
    selector's own attribute names are taken out ("input[name=password]")."""
    asks = False
    for label in labels:
        label = _without_attribute_names(label)
        words = _label_words(label)
        verb = _typing_verb(words)
        if any(word in _ABOUT_WORDS and k != verb for k, word in enumerate(words)):
            return False
        asks = asks or _names_a_secret(label)
    return asks


# Where a tool names the field it types into, and where it holds what it types.
_LABEL_KEYS = ("element", "name", "label", "selector", "field", "placeholder", "description", "target", "type",
               "aria_label", "ariaLabel", "title", "id", "locator")
_TYPED_KEYS = ("text", "value")
# A computer-use tool, whose "type" action types its text wherever the screen's focus is: no field names it.
_COMPUTER_TOOL = re.compile(r"(?i)computer(?:_\d+)?")


def typed_secrets(name, args):
    """A typing or form-filling tool's input with what it types into a field labelled for a secret, such as
    "Password textbox", masked whole: a password typed into a page is in no form a mask knows by its shape. A field
    with no label, as DevTools names one by its uid alone, has its value masked when it reads as a credential. Other
    tools' input, and other fields, come back as they are. A computer-use tool's "type" action is read as typing
    into a field with no label."""
    tool = name.rsplit("__", 1)[-1].rsplit(".", 1)[-1] if isinstance(name, str) else ""
    if not isinstance(args, dict) or not (_TYPING_TOOL.fullmatch(tool) or _COMPUTER_TOOL.fullmatch(tool)
                                          and args.get("action") == "type"):
        return args

    def field(item):
        if not isinstance(item, dict):
            return item
        labels = [str(item[key]) for key in _LABEL_KEYS if isinstance(item.get(key), str)]
        secret = _asks_for_a_secret(labels)
        out = dict(item)
        for key in _TYPED_KEYS:
            value = item.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value) and (
                    secret or not labels and any(map(_password_shaped, _seen_views(str(value))))):
                out[key] = "[masked]"
        return out

    out = field(args)
    for key, value in args.items():
        if isinstance(value, list):
            out[key] = [field(item) for item in value]
    return out


# Code that types into a page, as a tool runs it: a typing tool called with its input written as an object, as Codex's
# code mode calls one from JavaScript ('tools.mcp__playwright__browser_type({"element": "Password", ...})'), and a call
# that types a string into a field a locator found (Playwright's fill, type and press_sequentially in JavaScript or
# Python, Selenium's send_keys, WebdriverIO's setValue, Puppeteer's page.type(selector, text)). Read token by token, as
# the code is laid out, with its strings' quotes plain or escaped, as JSON holds code.
_TYPING_NAMES = _TYPING_TOOL.pattern.split(")?", 1)[1]  # the tool's own names, without its library's prefix
# A typing tool's call by its tool's name: an MCP tool's ("tools.mcp__playwright__browser_type") or one with its
# library's prefix ("browser_type"), not any method named fill or type, as an array's is.
_CODE_TYPING_CALL = re.compile(rf"(?i)(?<![\w$])(?:[\w$]+\.)*(?:mcp__[\w-]+__(?:{_TYPING_TOOL.pattern[4:]})"
                               rf"|(?:browser|puppeteer|playwright)_{_TYPING_NAMES})\s*\(")
_CODE_FILL_CALL = re.compile(r"\.\s*(?:fill|type|pressSequentially|press_sequentially|sendKeys|send_keys|SendKeys|setValue"
                             r"|set_value|addValue|add_value|send_keys_to_element|sendKeysToElement)"
                             r"\s*\(")
_CODE_HINT = re.compile(r"(?i)(?:fill|type|press_?sequentially|send_?keys|set_?value|add_?value|input_text)\w*\s*\(")
# A string that holds code, as JSON holds it: a typing call's "(" in it, and a quote of the code's own, plain or
# escaped; "Autofill(42)!", a password, holds no quote.
_CODE_IN_STRING = re.compile(r"(?i)(?:fill|type|press_?sequentially|send_?keys|set_?value|add_?value|input_text)\w*\s*\(")
def _holds_another_quote(text, inner, quote):
    """Whether what a string holds has a quote of the code's own: one other than its own quote, or its own escaped."""
    others = "".join(q for q in "\"'`" if q != quote)
    return bool(re.compile(r"\\+" + re.escape(quote) + "|[" + re.escape(others) + "]").search(text, *inner))
_CODE_NAME = re.compile(r"[A-Za-z_$][\w$]*")
_CODE_FIELD_KEYWORDS = frozenset(("selector", "locator", "label", "name", "placeholder"))
# The names a chain that types into a page holds: a page or frame, a locator's method, Selenium's driver and finders,
# WebdriverIO's "$", Cypress's cy, the keyboard. A chain with none (Buffer.from("password").fill("x")) types into no field.
_CODE_LOCATOR_NAMES = frozenset((
    "page", "frame", "mainFrame", "main_frame", "keyboard", "locator", "$", "$$", "driver", "browser", "element", "cy",
    "getByLabel", "getByPlaceholder", "getByRole", "getByTestId", "getByText", "getByAltText", "getByTitle",
    "get_by_label", "get_by_placeholder", "get_by_role", "get_by_test_id", "get_by_text", "get_by_alt_text",
    "get_by_title", "find_element", "find_elements", "findElement", "findElements", "querySelector", "query_selector"))
_CODE_VALUE_KEYWORDS = frozenset(("value", "text", "keys"))
# What a call's own selector argument needs: a page's or a frame's fill or type (page.fill(selector, value)), not an
# element's, a locator's or the keyboard's, which take the text first (send_keys("...", "\\n"), keyboard.type).
_CODE_SELECTOR_METHODS = frozenset(("fill", "type"))
_CODE_ELEMENT_NAMES = _CODE_LOCATOR_NAMES - {"page", "frame", "mainFrame", "main_frame", "browser", "driver"}
# A variable for a page, a frame or a driver, by its name's last word ("loginPage", "login_page", "page2").
_CODE_PAGE_WORDS = frozenset(("page", "frame", "driver", "browser", "element", "locator", "keyboard"))


def _names_a_page(names):
    """Whether a chain's names hold a page, a frame, a locator, a driver or the keyboard (_CODE_LOCATOR_NAMES), or one
    of the locator calls whose strings name a field (_CODE_FIELD_CALLS), by name or by a variable's last word."""
    return any(name in _CODE_LOCATOR_NAMES or _CODE_FIELD_CALLS.fullmatch(name)
               or re.split(r"_|(?<=[a-z0-9])(?=[A-Z])", name.rstrip("0123456789"))[-1].lower() in _CODE_PAGE_WORDS
               for name in names)


def _holds_a_field_call(text, argument):
    """Whether an argument's tokens end a chain that holds one of the locator calls whose strings name a field, as
    screen.getByLabelText("Password") or await screen.findByLabelText("Password") does."""
    return bool(argument) and argument[-1][0] in ")]" and any(
        a[0] == "n" and b[0] == "(" and _CODE_FIELD_CALLS.fullmatch(text, a[1], a[2])
        for a, b in zip(argument, argument[1:]))


def _names_an_element(names):
    """Whether a chain's names hold an element or a locator (_CODE_ELEMENT_NAMES, or a listed call other than a
    frame's), whose fill or type takes the text first, not a selector."""
    return bool(_CODE_ELEMENT_NAMES.intersection(names)) or any(
        name != "frame" and _CODE_FIELD_CALLS.fullmatch(name) for name in names)


def _string_open(text, at, stop):
    """(where its quote is, its quote, how many backslashes escape it, whether it is tripled) of the string in code
    that opens at `at`, past Python's prefix ("r", "f", "rb") and the backslashes JSON escapes its quote with."""
    j = at
    while j < stop and text[j] in "rRbBuUfF" and j - at < 2:
        j += 1
    depth = 0
    while j < stop and text[j] == "\\":
        j, depth = j + 1, depth + 1
    if j >= stop or text[j] not in "\"'`":
        return None
    unit = "\\" * depth + text[j]  # a quote as escaped as the first
    triple = text[j] != "`" and text.startswith(unit * 3, j - depth)
    return j, text[j], depth, triple


def _code_string_end(text, at, stop):
    """Where the string in code that opens at `at` ends, past its closing quote, or None: its quote plain, tripled, or
    escaped as JSON holds code ('\\"'), when it ends only at its quote escaped as many times."""
    opened = _string_open(text, at, stop)
    if not opened:
        return None
    j, quote, depth, triple = opened
    if triple:
        unit = "\\" * depth + quote
        end = text.find(unit * 3, j - depth + 3 * len(unit), stop)
        return end + 3 * len(unit) if end >= 0 else None
    k = j + 1
    while k < stop:
        if text[k] == "\\":
            run = k
            while run < stop and text[run] == "\\":
                run += 1
            if run < stop and text[run] == quote and run - k == depth:
                return run + 1
            k = run + 1  # an escape inside the string
        elif text[k] == quote and depth == 0:
            return k + 1
        elif text[k] == "\n" and quote != "`":
            return None
        else:
            k += 1
    return None


# A regex literal starts at a "/" after one of these marks and up to two spaces. In it a run of backslashes escapes the
# one character after it, as code escaped any number of times writes "\/", and inside a class a "/" counts only where
# no regex could start, so each try reads text no other try reads: an unclosed one costs time in proportion.
_REGEX_START = r"(?:(?<=[(,=:\[!&|?{};])|(?<=[(,=:\[!&|?{};] )|(?<=[(,=:\[!&|?{};]  ))"
_NO_REGEX_START = r"(?<![(,=:\[!&|?{};])(?<![(,=:\[!&|?{};] )(?<![(,=:\[!&|?{};]  )"
_CODE_TOKEN = re.compile(r"""(?<![\w$\\])[rRbBuUfF]{1,2}\\*["'`]|(?<!\\)\\*["'`]"""
                         r"""|""" + _REGEX_START + r"""/(?![/*])(?:\\+[^\\\n]"""
                         r"""|\[(?:\\+[^\\\n]|""" + _NO_REGEX_START + r"""/|[^\]\\\n/])*\]|[^/\\\n\[])+/[a-z]*"""
                         r"""|#|//|/\*"""
                         r"""|(?<!\\)\\+[nrt]|[{}\[\]():,=;.]|[A-Za-z_$][\w$]*""")
# Python's prefix and the string it opens, after a line break or tab escaped as JSON holds code ("\\tr\\"...\\"").
_PREFIXED_QUOTE = re.compile(r"""[rRbBuUfF]{1,2}\\*["'`]""")
# A typing method's name, as a call that types names it: what it types is a value, never code it holds.
_TYPING_NAME = re.compile(r"(?i)(?:fill|type|press_?sequentially|send_?keys|set_?value|add_?value|input_text)\w*\Z")
_ESCAPED_BREAK = re.compile(r"(?<!\\)(\\+)[nr]")
_QUOTE_RUN = re.compile(r"""(?<!\\)(\\*)["'`]""")


def _comment_end(text, at, stop, depth):
    """Where a comment whose mark ends at `at` ends: at its line's end, or, in code escaped `depth` times (as JSON
    holds code, a quote then has 2 ** depth - 1 backslashes), at the first line break escaped as many times, a run of
    backslashes that ends in one; "C:\\notes" in a comment of code as written is a path, not a break."""
    end = text.find("\n", at, stop)
    end = stop if end < 0 else end
    if depth:
        unit = 1 << (depth - 1)
        for run in _ESCAPED_BREAK.finditer(text, at, end):
            if len(run.group(1)) % (2 * unit) == unit:
                return run.start()
    return end


def _after_a_typed_key(text, tokens, first):
    """Whether a string read after tokens[first:], the tokens of its own stretch of code so far, follows a typed key's
    separator ("value=", "text": ), so it is a typed value itself."""
    j = len(tokens) - 1
    if j >= first and tokens[j][0] == "(":
        j -= 1
    if j - 1 >= first and tokens[j][0] in ":=" and tokens[j - 1][0] in "sn":
        key = tokens[j - 1]
        name = text[slice(*_code_text(text, key))] if key[0] == "s" else text[key[1]:key[2]]
        return name.lower() in ("text", "value")
    return False


def _code_tokens(text):
    """The text read as code, once: (its tokens (kind, start, end), "s" a string, "n" a name, or a bracket or mark
    itself; where a quote opened no string it could close; each stretch read, as (start, stop, how often its code is
    escaped, None for the text's own)).
    A string that itself holds a typing call, as JSON holds code ('{"code": "await page.fill(...)"}'), is read as
    the code it holds. A quote that opens no string on its line is passed over. Each stretch knows how often its code
    is escaped: a string's code once more than the string's quote, and the text's own, outside any string, by the
    first quote on each of its lines, so a comment ends where its code's line does (_comment_end)."""
    tokens, spans, breaks = [], [(0, len(text), None)], None
    depths = {}  # the text's own lines, by where each starts: how often the code on it is escaped
    loose, stretches, comments = [], [], []  # quotes that opened no string, each stretch read, the comments
    while spans:
        i, stop, escaped = spans.pop()
        stretches.append((i, stop, escaped))
        first = len(tokens)
        opened, typing = [], 0  # the stretch's open brackets, each whether a typing call's, and how many of those
        unclosed = False  # whether a "/*" found no "*/" after it in this stretch
        while True:
            m = _CODE_TOKEN.search(text, i, stop)
            if not m:
                break
            c, at = m.group(), m.start()
            if c in ("#", "//"):
                depth = escaped
                if depth is None:
                    if breaks is None:
                        breaks = [b.start() for b in re.finditer("\n", text)]
                    line = breaks[bisect.bisect_left(breaks, at) - 1] + 1 if bisect.bisect_left(breaks, at) else 0
                    if line not in depths:
                        later = bisect.bisect_left(breaks, line)
                        quote = _QUOTE_RUN.search(text, line, breaks[later] if later < len(breaks) else len(text))
                        depths[line] = (len(quote.group(1)) + 1).bit_length() - 1 if quote else 0
                    depth = depths[line]
                i = _comment_end(text, m.end(), stop, depth)
                comments.append((at, i))
                continue
            if c == "/*":  # a comment to the first "*/"; with none after it, none closes after a later "/*" either
                end = -1 if unclosed else text.find("*/", at + 2, stop)
                unclosed = end < 0
                if unclosed:
                    i = at + 1
                    continue
                comments.append((at, end + 2))
                i = end + 2
                continue
            if c[0] == "/":  # a regex literal, as split(/#/) holds one: no comment starts inside it
                i = m.end()
                continue
            if c[0] == "\\" and c[-1] in "nrt":
                i = m.end()  # a line break or tab escaped as JSON holds code: space between tokens, after which
                prefixed = _PREFIXED_QUOTE.match(text, i, stop)  # Python's prefix may open a string
                if not prefixed:
                    continue
                c, at = prefixed.group(), i
            if c[-1] in "\"'`":
                end = _code_string_end(text, at, stop)
                if end:
                    inner = _code_text(text, ("s", at, end))
                    if (_CODE_IN_STRING.search(text, *inner) and _holds_another_quote(text, inner, c[-1])
                            and not typing and not _after_a_typed_key(text, tokens, first)):
                        spans.append(inner + ((_string_open(text, at, end)[2] + 1).bit_length(),))
                    else:
                        tokens.append(("s", at, end))
                    i = end
                else:
                    loose.append(at)
                    i = at + len(c)
                continue
            kind = "n" if c[0].isalpha() or c[0] in "_$" else c
            if kind in "([{":  # inside a typing call's brackets, at any depth, a string is a value, never code
                call = (kind == "(" and len(tokens) > first and tokens[-1][0] == "n"
                        and bool(_TYPING_NAME.search(text, tokens[-1][1], tokens[-1][2])))
                opened.append(call)
                typing += call
            elif kind in ")]}" and opened:
                typing -= opened.pop()
            tokens.append((kind, at, m.end()))
            i = m.end()
    tokens.sort(key=lambda t: t[1])
    return tokens, sorted(loose), stretches, sorted(comments)


def _code_text(text, token):
    """What a string token holds, between its quotes, as (start, end)."""
    _, start, end = token
    j, quote, depth, triple = _string_open(text, start, end)
    if triple:
        width = 3 * (depth + 1)
        return j - depth + width, end - width
    return j + 1, end - depth - 1


def _built_at_run_time(text, token):
    """Whether a string token is built when the code runs: a template with "${...}", or Python's f-string with "{"."""
    j, quote, depth, triple = _string_open(text, token[1], token[2])
    a, b = _code_text(text, token)
    prefix = text[token[1]:j].lower()
    return quote == "`" and "${" in text[a:b] or "f" in prefix and "{" in text[a:b]


def _key_before(tokens, k):
    """The index of the key a string token at k is the value of ("key": "value", key=value, key: ("value")), or None."""
    j = k - 1
    while j >= 0 and tokens[j][0] == "(":
        j -= 1
    return j - 1 if j >= 1 and tokens[j][0] in ":=" and tokens[j - 1][0] in "sn" else None


def _typed_in_objects(text, tokens):
    """The typed values a typing tool's input written in code masks: each object's ("key": "value", or key=value at
    the call's own level) labels and typed values, read with typed_secrets' rule."""
    objects, edits = [[[], []]], []
    for k, token in enumerate(tokens):
        if token[0] == "{":
            objects.append([[], []])
        elif token[0] == "}" and len(objects) > 1:
            edits += _typed_values(text, *objects.pop())
        elif token[0] == "s" and _key_before(tokens, k) is not None:
            name = tokens[_key_before(tokens, k)]
            key = text[slice(*_code_text(text, name))] if name[0] == "s" else text[name[1]:name[2]]
            labels, typed = objects[-1]
            if key in _LABEL_KEYS:
                labels.append(text[slice(*_code_text(text, token))])
            elif key in _TYPED_KEYS:
                typed.append(_code_text(text, token))
    while objects:
        edits += _typed_values(text, *objects.pop())
    return edits


@functools.lru_cache(maxsize=4096)
def _labels_ask_for_a_secret(labels):
    """_asks_for_a_secret for a tuple of labels, kept: code that types into many fields repeats its labels."""
    return _asks_for_a_secret(list(labels))


def _typed_values(text, labels, typed):
    secret = _labels_ask_for_a_secret(tuple(labels))
    return [(a, b, "[masked]") for a, b in typed
            if b > a and (secret or not labels and any(map(_password_shaped, _seen_views(text[a:b]))))]


# A regex literal where a value starts inside a locator's brackets: first, or after ",", ":" or "=".
_REGEX_IN_BRACKETS = re.compile(r"(?<=[(,:=])\s*/((?:\\.|[^/\\\n])+)/[a-z]*(?=\s*(?:[,})\]]|$))")


# Words a value follows, never a function's name: a bracket after one ("return (page.getByLabel(...))", "await (...)")
# is a receiver in brackets, not a call.
_CODE_KEYWORDS = frozenset(("return", "await", "yield", "typeof", "void", "delete", "throw", "case", "in", "of", "new",
                            "not", "and", "or", "is", "if", "elif", "else", "while", "assert", "lambda", "with", "do"))
# The calls whose strings name a field, as the field rule lists them: a locator's getBy*/get_by_*, locator, $, frame,
# find_element and their kin (Testing Library's findBy* and queryBy*, a frame locator, WebdriverIO's shadow$, react$ and
# custom$, Selenium 3's find_element_by_*, C#'s FindElement, Puppeteer's $x and its waits for a selector or an XPath,
# pyppeteer's J, JJ, Jx and xpath, Selenium's explicit wait and until; and Cypress's get, find and contains in a chain
# from cy, _CYPRESS_FIELD_CALLS, as a secret store's get("password") is none). Another call in the chain (filter,
# describe, nth, and, or) names none.
_CODE_FIELD_CALLS = re.compile(r"(?:get|find|query)(?:All)?By\w+|get_by_\w+|locator|frameLocator|frame_locator"
                               r"|(?:shadow|react|custom)?\$\$?|\$x|JJ?|Jx|xpath|frame"
                               r"|find_elements?(?:_by_\w+)?|findElements?|FindElements?|querySelector(?:All)?"
                               r"|query_selector(?:_all)?|waitFor(?:Selector|XPath)?|wait_for_selector|element"
                               r"|wait|until")
_CYPRESS_FIELD_CALLS = frozenset(("get", "find", "contains"))


def _receiver_strings(text, tokens, k, comments=(), limit=200):
    """The strings of the locator calls in the chain before tokens[k], read back over names, dots and whole brackets,
    as 'page.getByRole("textbox", {name: "Password"})' or 'driver.find_element(By.ID, "password")' gives a field's
    labels: each call _CODE_FIELD_CALLS names, a container's as much as the field's own, and a receiver in brackets;
    in a chain from cy, Cypress's get, find and contains too.
    A chain is joined by dots: it reads no further back than a name with no dot before it, or a bracket that follows
    no name or bracket, so another statement's strings are never this field's. An object's key ({"name": ...}) names
    an option, not the field."""
    strings, names, inside, quoted, depth, stop, close, close_k = [], [], [], [], 0, max(-1, k - limit), None, None
    cypress = []  # Cypress's calls' strings, the chain's only if it starts at cy
    in_comment = comments.inside if comments else (lambda at: False)
    k -= 1
    while k > stop:
        kind = tokens[k][0]
        if depth:
            depth += kind in ")]}"
            depth -= kind in "([{"
            if kind == "s":
                quoted.append(tokens[k][1:])  # where a string's quotes hold it: no regex is read in there
                if not (k + 1 < len(tokens) and tokens[k + 1][0] == ":"):
                    inside.append(text[slice(*_code_text(text, tokens[k]))])
            if not depth:
                if k == 0 or tokens[k - 1][0] not in ("n", ")", "]") or tokens[k - 1][0] == "n" and text[
                        tokens[k - 1][1]:tokens[k - 1][2]] in _CODE_KEYWORDS and (k < 2 or tokens[k - 2][0] != "."):
                    # a bracket that calls or indexes nothing starts the chain: a receiver in brackets,
                    # (await page.$("#password")), is a chain of its own, read as one, its calls' strings and names;
                    # a keyword's name after a dot is a method's (locator.and(...), locator.or(...)), a call
                    held, held_names = _receiver_strings(text, tokens, close_k, comments, limit)
                    strings += held
                    names += held_names
                    break
                name = text[tokens[k - 1][1]:tokens[k - 1][2]] if kind == "(" and tokens[k - 1][0] == "n" else ""
                if name and (_CODE_FIELD_CALLS.fullmatch(name) or name in _CYPRESS_FIELD_CALLS):
                    held = strings if _CODE_FIELD_CALLS.fullmatch(name) else cypress
                    held += inside  # a regex literal in the brackets ("getByLabel(/Password/i)") too
                    for regex in _REGEX_IN_BRACKETS.finditer(text, tokens[k][2], close):
                        if not in_comment(regex.start(1)) and not any(a <= regex.start(1) < b for a, b in quoted):
                            # a regex in a comment or in a string's quotes ("Search (/password/)") names no field
                            held.append(re.sub(r"\\.|[\^$.*+?()\[\]{}|]", " ", regex.group(1)).strip())
                inside, quoted = [], []
        elif kind in ")]":
            depth, close, close_k = 1, tokens[k][1], k
        elif kind == "n":
            names.append(text[tokens[k][1]:tokens[k][2]])
            if k == 0 or tokens[k - 1][0] != ".":
                break  # "await page", or a name after another statement: the chain starts at its first name
        elif kind != ".":
            break
        k -= 1
    if names and names[-1] == "cy":
        strings += cypress
    return strings, names


class _Spans:
    """Sorted, separate spans of the text, such as its comments, indexed once to say whether a position is in one."""

    def __init__(self, spans):
        self.spans, self.starts = spans, [a for a, _ in spans]

    def inside(self, at):
        j = bisect.bisect_right(self.starts, at) - 1
        return j >= 0 and self.spans[j][0] <= at < self.spans[j][1]


class _Stretches:
    """The stretches of code _code_tokens read, indexed once, so the one holding a position is found in log time, not
    by reading them all for each typing call (a JSON log of many code records took time in their count's square)."""

    def __init__(self, text, stretches):
        self.text, self.items = text, sorted(stretches, key=lambda s: (s[0], -s[1]))
        self.starts, self.parent, stack = [s[0] for s in self.items], [], []
        for j, (start, _, _) in enumerate(self.items):
            while stack and self.items[stack[-1]][1] <= start:
                stack.pop()
            self.parent.append(stack[-1] if stack else -1)
            stack.append(j)
        self.breaks, self.depths = None, {}

    def holding(self, at):
        """(start, stop, how often its code is escaped) of the innermost stretch that holds `at`."""
        j = bisect.bisect_right(self.starts, at) - 1
        while j > 0 and self.items[j][1] <= at:
            j = self.parent[j]
        return self.items[max(j, 0)]

    def line_end(self, at):
        """Where the line of code holding `at` ends, as often as its code is escaped (_comment_end), and never past
        the end of the stretch that holds it; the text's own lines show their escaping by their first quote."""
        _, stop, depth = self.holding(at)
        if depth is None:
            if self.breaks is None:
                self.breaks = [m.start() for m in re.finditer("\n", self.text)]
            k = bisect.bisect_left(self.breaks, at)
            line = self.breaks[k - 1] + 1 if k else 0
            if line not in self.depths:
                quote = _QUOTE_RUN.search(self.text, line, self.breaks[k] if k < len(self.breaks) else len(self.text))
                self.depths[line] = (len(quote.group(1)) + 1).bit_length() - 1 if quote else 0
            depth = self.depths[line]
        return _comment_end(self.text, at, stop, depth)


def _call_span(tokens, k, bound, limit=400):
    """(the tokens of a call's arguments after its "(" at tokens[k], the index of its closing bracket or None when it
    has none within `limit` tokens or before `bound`, the end of the stretch of code that holds the call: a call in a
    JSON string never reads the fields after that string)."""
    out, depth = [], 0
    for j in range(k + 1, min(len(tokens), k + 1 + limit)):
        if tokens[j][1] >= bound:
            break
        kind = tokens[j][0]
        if kind in "([{":
            depth += 1
        elif kind in ")]}":
            if depth == 0:
                return out, j
            depth -= 1
        out.append(tokens[j])
    return out, None


def _split_arguments(text, arguments):
    """A call's arguments at its own level, each as (its keyword or None, its tokens)."""
    out, depth, current = [], 0, []
    for token in arguments + [(",", None, None)]:
        if token[0] == "," and depth == 0:
            keyword = None
            if len(current) >= 2 and current[0][0] == "n" and current[1][0] == "=":
                keyword, current = text[current[0][1]:current[0][2]], current[2:]
            out.append((keyword, current))
            current = []
            continue
        depth += token[0] in "([{"
        depth -= token[0] in ")]}"
        current.append(token)
    return out


def _typed_code_edits(text):
    """Each password code types into a page, as an edit that masks it: a typing tool's input written in code, read with
    typed_secrets' rule, and a string typed into a field whose locator's strings (its label, placeholder, role's name or
    selector) ask for a secret. A value typed from a variable stays."""
    if not _CODE_HINT.search(text):
        return []
    tokens, loose, stretches, comments = _code_tokens(text)
    comments = _Spans(comments)
    stretches = _Stretches(text, stretches)
    starts = [t[1] for t in tokens]
    edits = []

    def opening(call):  # the index of the token "(" that ends the call's match
        k = bisect.bisect_left(starts, call.end() - 1)
        return k if k < len(tokens) and tokens[k][0] == "(" and tokens[k][1] == call.end() - 1 else None

    for call in _CODE_TYPING_CALL.finditer(text):
        k = opening(call)
        if k is not None:
            edits += _typed_in_objects(text, _call_span(tokens, k, stretches.holding(tokens[k][1])[1])[0])
    for call in _CODE_FILL_CALL.finditer(text):
        k = opening(call)
        if k is None:
            continue
        bound = stretches.holding(tokens[k][1])[1]  # the end of the code that holds the call
        arguments, close = _call_span(tokens, k, bound)
        args = _split_arguments(text, arguments)
        if not any(t[0] == "s" for _, ts in args for t in ts):
            continue
        labels, names = _receiver_strings(text, tokens, bisect.bisect_left(starts, call.start()), comments)
        own = [ts for kw, ts in args if kw in _CODE_FIELD_KEYWORDS]
        positional = [ts for kw, ts in args if kw is None]
        # the call's own selector, page.fill(selector, text) on any page or variable holding one: a lone string first,
        # then a value holding a string, by position or as value=; not fill(text, {options}) or send_keys(text, Keys.ENTER)
        second = positional[1] if len(positional) > 1 else []
        method = text[call.start() + 1:call.end() - 1].strip()
        selector = (method in _CODE_SELECTOR_METHODS and not _names_an_element(names)
                    and bool(positional) and [t[0] for t in positional[0] if t[0] not in "()"] == ["s"]
                    and {t[0] for t in positional[0]} <= {"s", "(", ")"} and (
            bool(second) and (second[0][0] == "s" or second[0][0] == "(" and any(t[0] == "s" for t in second))
            or any(kw in _CODE_VALUE_KEYWORDS and any(t[0] == "s" for t in ts) for kw, ts in args)))
        if selector:
            own.append(positional[0])
        # the call's own selector by its keyword, page.fill(selector="#password", value=...), on any variable
        named = method in _CODE_SELECTOR_METHODS and any(
            kw == "selector" and any(t[0] == "s" for t in ts) for kw, ts in args)
        labels += [text[slice(*_code_text(text, t))] for ts in own for t in ts if t[0] == "s"]  # the call's own field
        # the call's own field as a locator call, user.type(screen.getByLabelText("Password"), text) or Selenium's
        # send_keys_to_element(driver.find_element(By.ID, "password"), text): its listed calls' strings, read as a chain
        field = not selector and len(positional) > 1 and _holds_a_field_call(text, positional[0])
        if field:
            labels += _receiver_strings(text, tokens, bisect.bisect_left(starts, positional[0][-1][1]) + 1, comments)[0]
            own.append(positional[0])
        # the value: a value= keyword's, or the first argument after the field that holds a string (send_keys may
        # take a key first, Keys.HOME)
        typed = [ts for kw, ts in args if kw in _CODE_VALUE_KEYWORDS] or [
            ts for ts in positional[1 if selector or field else 0:] if any(t[0] == "s" for t in ts)][:1]
        strings = [t for ts in typed for t in ts if t[0] == "s"]
        if typed and len(typed[0]) > 1 and typed[0][0][0] == "n" and typed[0][1][0] in "([.":
            continue  # a value the code computes: os.environ["PW"], decrypt("vault:pw"), secrets.pw
        if not strings or not (selector or named or field or _names_a_page(names)):
            continue  # a value from a variable, or a call on no page and no locator
        if not labels:  # only typing into the focused field (page.keyboard.type) is read, by the value's shape
            a, b = _code_text(text, strings[0])
            if "keyboard" in names and b > a and not _built_at_run_time(text, strings[0]) and any(
                    map(_password_shaped, _seen_views(text[a:b]))):
                edits.append((a, b, "[masked]"))
            continue
        # any one string that asks for a secret on its own names the field so: no other string, a container's, a
        # strategy's ("id") or an option's, takes it back
        if not any(_labels_ask_for_a_secret((label,)) for label in labels) or all(
                _built_at_run_time(text, t) for t in strings):
            continue
        # from the value's first string through the call's last string that is not its field, whatever lies
        # between: a comment, a prefix, an escape or a string read wrong cannot hold any of it back
        rest = [t for kw, ts in args if ts is not None and not any(ts is o for o in own) for t in ts if t[0] == "s"]
        start, end = _code_text(text, strings[0])[0], max(_code_text(text, rest[-1])[1], _code_text(text, strings[0])[1])
        stop = tokens[close][1] if close is not None else bound
        stray = loose[bisect.bisect_left(loose, start):][:1]
        if stray and stray[0] < stop:  # a quote that closes nothing: on to the end of its line
            end = max(end, stretches.line_end(stray[0]))
        elif close is None:  # a call read as unclosed: on to the end of its last string's line
            end = max(end, stretches.line_end(end))
        if end > start:
            edits.append((start, end, "[masked]"))
    merged = []
    for start, end, _ in sorted(edits):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end), "[masked]")
        else:
            merged.append((start, end, "[masked]"))
    return merged


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


# Four-letter words common in English and in technical text (_app_password_span): those used in two or more of an
# Ubuntu 24.04 system's English manual pages or in Python 3.12's own comments and docstrings, and everyday words
# besides; 2,812 of the 456,976 strings of four small letters.
_COMMON_WORDS = frozenset("""
aaaa aarp abbe abcd able abrt absf acct ache acid acks acls acme acml acos acpi acre acrn acsc acts adal addi addl addr
adds adel adiw adpm adrc adsl aead afdo afir aged ages agmc agmd agmg agmh ahls aide aids aifc aiff aiir aims airs ajar
akin alas algo algs allf alls allx ally alpe alpn alrm alsa also alto alts alum amax amet amid amin amix amqp andk andy
anew ange anmr anon ansi ante anti ants aout apad apch apcn apco apcs apic apos apps apre apss aptd aptx arbg arcc arcd
arcg arch arcs ardo area aref areg aren areq ares argb argc argg argn argp args argv argz aria arib arin arms army arnr
arpa arpd arrp arts asan asdf asin asis asks asmf aspx asym atag atan atch atof atoi atol atom atop atsc attr atts auid
aunt auth auto auxv avid away awks awry axes axis axjf aybc aybd aybg aybh azmq babs baby back bags bail bait bake bald
ball band bang bank bans bare barf bark barn bars base bash basr bass bath bats baud bbal bbbb bbit bbox bbro bcmp bcsp
beam bean bear beat beds beef been beep beer bees beet bell belt bend bent bert best beta bets bfin bfox bgra bias bidi
bids bike bill bind bins bios bird bite bits blah blew blib blob bloc blog blow blue blum blur boat body boil bold bolt
bomb bond bone book bool boom boon boot bore borg born boss both bout bowl boxh boxw boys bpos brag bras brcc brew brng
brow bsfm bsfs bsod bsum btmp buds buff bufp bugs bulb bulk bull bump bunk burn bury bush busy butt buys buzz byte bzip
cabs caca cafe cage caif cake calf call calm came camp cand cane cans cape caps card care carg carp cars cart casa case
cash cast cats cave cavs cbrt cccc ccmp ccos ccpp ccrt cdda cdef cdma cdot ceil cell cent ceph cerf cert cexp cgid cgit
cgls cgop chap char chat chef chet chew chfn chid chin chip chop chpl chrt chsh chvt cidr cifs cite city clad clam clap
claw clay clev clip clnt clog club clue clut clwb cmap cmds cmit cmov cmoy cmpb cmpl cmse cmsg cmxe cmyk coal coat code
coef coff coil coin cold coll colo cols come comm comp cond cone conf conj conn cons cont conv cook cool cope copy cord
core cork corn coro corp corr cosf cosh cosl cost cosy cots cpan cpfr cpgr cpid cpio cpow cppw cprl cpus crab crda cred
cref crew crib cris crit crlf crls cron crop crow crtc crud csin cssc csum ctan ctlx ctor ctrl ctxt cube cuda cues cuid
cups curb cure curl curr cute cuts cvbr cves cwnd cyan dads daft dame damn damp dare dark dash data date dawn days dbnz
dbus dcbw dccp dcti dctx ddeb ddiv dead deaf deal dean dear debs debt deck decl deed deem deep deer defn defs dell delv
demo deny deps desc dese desi desk dest desx dgst dhcp diag dial diam dice dick dict didn died dies diet diff digs dime
dine dirc dirp dirs dirt disc dish disk disp dist divd dive divf divs divu dlen dlls dmac dmtf dnat dnet dock docs does
doff dogs doit doll dome domo done dont doom door dose dots dove down doze dpdk dpfp dpkg dpms drag draw drem drew drip
drop drpm drug drum dsbt dscp dsos dsth dstw dtls dtor dtps dual duck dude duel dues duid duke dull dumb dump dune dups
dusk dust duty dyad dyld dyne eabi each earn ears ease east easy eats ecdh echo ecma ecmp ecne ecvt eden edge edid edit
edns egea eggs egid eimm elem elif elim elrw else emag emit ends enum envp envy envz epfd epic epzs erbs erfc erff erfl
eric errc erri errp errs erru errx eryq esac escl esin etag etns euid euro eval even ever evex evil evim evrp ewma exam
excl excp exec exim exit expf expl expo expr eyed eyes faan fabi fabs face fact fada fade fail fair fake fall fame fang
fans fare farm fast fate fcoe fcse fcut fcvt fdbg fdce fdim fdiv fdse fdsi fear feat feed feel fees feet fell felt fenv
feof fern fest feud fext ffat ffff ffix ffoo ffsl fgnu fidx fifa fifi fifo fiji file fill film fils filt find fine fini
fipa fips fira fire firm fish fist fits five flac flag flap flat flaw flax flea fled flew flex flip flop flow flra flsa
fltk flto flux fmaf fmal fmax fmem fmin fmla fmod fnew fnon foam foes fold folk fond font food fool foot fopt ford fork
form fort forw foul four fowl fpcc fpch fpcr fpic fpie fpit fplt fpre fprm fptm fpud fpus fqdn frac frag frdy fred free
freg freq frii friz frog from frwh fsca fsck fsel fsfm fsid fssa fsso fsub fsys ftls ftok ftpd ftps ftsp fuel fuey full
fume func fund fuse fuss fuzz fvar fvpt fvtv fweb fwpa fwrx fwry fxsr gabi gain gait gala gale game gang gaps gasp gate
gatt gave gawk gaze gbal gbit gbps gbrp gbtf gbuf gcda gcfn gcno gcov gcse gctf gctx gcvt gear geek gelf gems gene geom
getc gets getw gfdl gfni ggdb ggnu gibi gids gift giga gimp gint ginv girl gist gitk give glad glib glob glow glsl glue
glum gmon gnat goal goat goes gold golf gone good gopt gory gost goto gown gpgv gpus grab grad gram grap gray greg grep
grew grey grid grim grin grip grit grml grog grok grow grpp grub gsar gsbj gsdj gslj gslp gsub gsum gtty guar guid gulf
guns guru gusa gust guts guys gvfs gvim gvms gvrp gwsw gzip hack hadi hadn hail hair half hall halo halt hand hang hann
hard harm hash hasn hast hate haul have hawk hazy hdcd hdlr hdmi hdrp hdtv head heal heap hear heat heel held hell helm
help herb herd here hero hers hevc hfsc hhhh hhmm hide hier high hike hill hint hire hist hitm hits hive hkps hlen hlog
hmac hold hole holy home hood hook hope hops horn hose host hour hpfs hpos hppa hpsa hpux href hrir hsin hsts hsub htab
html htop http hubs hues huge hugs hull hung hunk hunt hurd hurt hush huts hvga hwdb iacr iaid iall iana ibft ibus icap
icbi iche ichl icmp icon idct idea idel idet idev idgm idiv idle idol idtx iech ieee ierr ietf ifap ifup igmp ikey ilme
imag imap imax imin impl imsg inch incl incr indx inet infc info infs init inka insb insl insn inst insw intb intl into
intr ints intv inum ioam iocb iopl iops iota ipaq ipar ipcs ipfs ipip ipns iptr ipvx irda irec irix iron irqs isaa isab
isac iseg isel iseq isig isma isml ismv isra itch item iter itmr itrc ittf ival ivar ivec jack jail jams jane jars java
jazz jbsr jean jeep jfif jinc jiri jmpi jobs joey jogs john join joke jolt joss jpeg json jsri juju jump june junk jury
just kbit kbps kcat kcbt kcmp kctx keen keep kend kent kept kern kext keys kibi kick kids kill kilo kimi kind king kiro
kiss kite kits klog kmem kmod kmsg knee knew knit knob knot know kode konq kptr kslt ksym kwds labr labs lace lack lacp
lady laid lake lamb lame lamp lanc land lane lang laps last late latm latt lavc lavf lawn laws lays lazy lcms ldap ldcd
ldcw ldif ldiv ldpc ldrd lead leaf leak leal lean leap leds left legs lend lens lent leon lerp less lest lets lfoo lgcc
lgpl lhls liar libc libm libs libz lice lick lids lied lies lieu life lift like lilo lima limb lime limp lina line link
lint lion lipo lips lirc lisa lisp list live lkgs llcs lldp llrs llsc llvm lmax lmid lmin lmml load loaf loan lock lofi
loft logb logf logl logo logs lohi lone long look loop lord lore loro lose losi loss lost lots loud loup love lpfr lprm
lqpn lrcp lrsr lsim lsns lsof lsrr ltrt luck luks luma lumi lump lung lure lush lvfs lynx lyon lzip lzma lzop mabi mabm
mabs macc mach mada madd made mads madu madx maes mafd maid mail main maix make maks male mall mamx mand many mapp maps
marc mare mark marm mars mary mash mask masm masq mass mast mate math matt mavx mawk maxk maxp maze mbcs mbeq mbig mbim
mbit mbmi mbmx mbox mbps mbss mbwx mccs mcdx mcet mcix mcld mcom mcpu mcrc mcse mcsr mctx mdar mdat mdaz mdec mdev mdiv
mdll mdmx mdns mdoc mdsp mduc meal mean meat mebi meet mega mejo meld melf melt memb meml memo mems memu mend menu mere
mesa mesg mesh mess meta meth meva mexr mext mfar mfhi mfix mflo mfma mfpr mfpu mfra mgas mgcc mghs mgmt mgnu mgpr mhal
mhle mhtm mice midi midx migh mika mike mild mile milk mill mime mind mine mini minp mint mips mirq misa misc misr miss
mist mitr mjli mjpg mjsr mjtg mkfs mlaf mlam mlev mliw mlow mlpc mlra mlsp mlwp mmac mmad mman mmap mmax mmco mmcu mmio
mmix mmma mmmx mmno mmpy mmsa mmsh mmul mnan mnop moby mock modd mode modf modp mods modu mold mole mone monk mono mood
moof moon moot moov mops more moss most motd moth move movl mpan mpdr mpeg mpic mpid mpku mpls mplt mppc mppe mptx mpwr
mpym mpyu mpyw mrgf mrmw mrop mror mrtd mrtm mrtp msad msbc mscc msda msdn msec msel msep msgh msgp msgs msgx msha msim
msio mspe msse mstd mstm msub msve msys mtab mtas mtbm mtda mtio mtls mtoc mtpf mtrr much muck mule muli muls mult mulu
mulx muse musl must mute mutt mvhd mvis mvle mvms mvrp mvsx mxop mxpa myin mypy myth mzda nail name nand nanf nanl nano
nans navy nawk nbio nbsp ndbm ndex near neat neck necs need neon ness nest netp nets newp news next nfds nfkc nfsd nftw
nhid nice nick nils nine nist nlen nloc nmea nnnn nntp noah node nofp nomc none noon noop nops nopt noqa norc norm noro
nose notb note noun novc nptl nptr nsec nseq nsfs nsid nsse ntds ntfs ntlm ntpd ntsc ntss null numa nuts nval nvim nvme
nxyz nzer oaep oarg oath obex obey objc obmc ochl ocsp odds odgm odor oech oerr offs oids oils okay okey olor olpc omit
omsg once ones only onto oomd oops opal open oper opts opus oral ored orig oseg oseq oslo otmr oucp ough ouid ours oust
outa outb outl outs outv outw oval ovec oven over ovpn owed owes owls owns pace pack pact pads page paid pain pair pale
palm paln pane pans panu papa para park parm parr part pass past path paul pave pays pcap pcie pcms pcpu pcre pcrl pcrs
pctx peak peap pear peas pebi peek peel peer pens peps pere perf perl perm pers peta pets pfds pgid pgrp phil phys pick
pict pids pier pies pigs pigz pike pile pill pine ping pink pins pint pipe pits pitt pity pkcs pkey pkgs pkix pkts plam
plan play pldd plea plen plog plot plow ploy plug plum plus pmap pmem pmqs pmtu pmus pods poem poet poff poke pole poll
polo pond pong pony pool poor popc popd pope pops pork port pose post pots pour powf powl ppdc ppdi ppid pppd pray prec
pred pref preg prep pres prev prey prft prim prio priv prob proc prod prof prog proj prop pros prot psbb psnr psum psyc
ptid ptmx ptsi ptys pubs pull pulp pump punk pure push putc puts putw pvid pwck pwdx pwrx pycs pypi pyro qcam qcif qdri
qdrt qdrw qemu qeth qfoo qmax qmin qoff qpal qpel qsin quad qual quat quit quiz quot quux qvga qwen qxga race rack racy
radd rafl raft rage raid rail rain rake ramp rand rang rank raop rare rarp rash rate rath rats rave rays rbal rcmd rcpc
rctx rdev rdft rdma read real reap rear recp rect recv redo reds reef reel refs regs rela rely rent repo repr reps reqs
resp rest rets revs rext rfcs rfds rgba rgid rhds rhel rhgb riaa rice rich rick ride rift ring rint riot risc rise risk
rite ritz rlcp rlim rlog rlum rmdc rmkx rmso road roam roar robe rock rode roff role roll roof room root rope rori rose
ross roth rotw rows rpcl rpza rseq rset rshd rssi rstp rsum rtld rtmp rtps rtsp rtti ruby rude ruff rugs ruid ruin rule
runc rung runq runs rush rust rvim rwho rwin rwnd rwxr sack sadc sadf safe saga sage said sail sake sale salt same samp
sand sane sang sank sans sasl satd save says sbal sbat sbin sbiw sbrk sbsf sbsl sbsr sbss scan scap scar scev scfi sclk
scon scop scpu scrn scsi sctp sdcc sddm sdif seal seam seas seat secs sect seed seek seem seen sees self sell semi send
sens sent serv sess seth seti sets sevp sewn sftp sfun sgid sgml shar shed shim ship shmp shoe shop shot show shuf shut
sick side sidx sigh sign sigs silk simd simt sinc sine sinf sing sinh sink sinl sins site siti sits size skbs skel skew
skid skin skip slab slam slap sled slen slev slid slim slip slit slli slog slot slow slsr slub slug smac smap smem smgl
smkx smsc smso smtp smux snap snat snip snmp snow snup soak soap soar sock soda sofa soft soil sold sole solo some song
soon sops sore sort soul soup sour soxr spal spam span spdy spec sped spfp spid spin spit spot spre spun spur spwd sqrt
srai srch srcw srec sref srgb srli ssbs sset sshd ssid ssim ssrc ssrr sssd stab stag star stat stay stef stem step stew
stir stld stlf stmt stnd stop strp stsd stts stty stub subj subq subs such suck sudo suid suit sums sung sunk supp sure
surf suse susp sval svcd sven svga svme svml swab swan swap sway swdb swim sxga syms sync syno sysv tabs tack tact tada
tagp tags tail take tale talk tall tame tanf tanh tank tanl tape taps task taxi tbit tbps tcon tcov tcpd tcsh tdfx tdii
tdls team tear tebi tech tell temp tend tens tent tera term tesa test text tfdt tfhd tfnd tftp tgid than that thee them
then ther they thin this thom thou thru thus tick tide tidy tied tier ties tiff tile till tilt time tins tint tiny tipc
tips tire tkip tldr tlen tlog tlsa tmac tmcd tmix tmpl tmux toad todo toes told toll tomb toml tone tons tony took tool
topo tops tore torn toss tour tout town toys tpcs tpic tput trac trae trak tram trap tray tree trek trim trio trip tron
true tsan tset tsid tsig tsns tstr ttls tttt ttyp ttys tube tuck tuna tune turf turn twin twos type typo tzst uapi uazu
ubuf ucfq ucfr uchl ucop udeb udev udta uefi ugen ugly uids uint ulog umac umax umin umul undo uniq unit unix unto unxz
upnp upon upto uref urge uris urls urns usec used user uses uspp util utmp uucp uuid uwin uxga vaes vain vars vary vase
vast vban vbox vbsf vcan vcpu vcsa vdir vdpa vdso vect veil vein vent venv vepa verb verr very vesa vest veth veto vfat
vger vibe vice view vigr vine vipw viro virt visa vitc vlan vlen vliw vlpf vmaf vmap vmax vmin vnet void voip vops vote
vpnc vpos vpre vptr vrep vrrp vsad vsse vsub vtag vtop vudc vvvv wade wage wail wais wait wake walk wall wand want ward
warm warn warp wash wasn wasp watt wave wavy ways wdid weak wear webm webs weed week weep well went weof were west wext
wget what when whim whip whom wide widl wife wifi wiki wild will wilt wind wine wing wink winp wins wipe wire wise wish
with wlan wlog woke wolf wong wood wool word wore work worm worn wpan wrap wsfb wsgi wtmp wtps wvga wwan wxga xavs xbox
xbpf xcam xdcb xdev xdim xdrs xdvi xena xenl xfer xfrm xfwp xgot xhot xiph xkcd xkey xlen xmac xmag xman xmax xmin xmpp
xoff xorg xorl xpdf xpos xprt xrdb xres xrun xset xshm xsub xtra xvid xwud xxxx xyhl yama yaml yank yard yarn yawn yeah
year yell yelp yhot ymax ymin yoga your ypos yres yyyy zago zcat zcmp zero zeta zeus zimg zinc zlib znew zone zoom zope
zpos zstd zulu zzzz
""".split())
