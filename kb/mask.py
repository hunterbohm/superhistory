import base64
import bisect
import contextvars
import hashlib
import logging
import math
import os
import re
import shlex
import sys
import traceback

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
# around a match), is one too, raw, as its eight-bit introducer or escaped as JSON, Python or C write it ("\u001b[",
# "\x1b[", "\033["), but for ESC before a placeholder, which "[m" would start. A secret reads through them, and one
# before a secret parts it from the word before (_once), though a colour code ends in a letter. The vertical tab, the
# form feed, the four separators from 0x1c and NEL are whitespace, as str.split reads them, and part words as a line
# break does: pdftotext puts a form feed between pages.
_CSI = r"(?:\x1b\[|\x9b|\\(?:u001[bB]|x1[bB]|033)\[|\\(?:u009[bB]|x9[bB]|233))(?!masked\])[0-?]*[ -/]*[@-~]"
_FORMAT_CHARACTERS = (r"\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e\u2060-\u206f"
                      r"\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufff8\U0001bca0-\U0001bca3\U0001d173-\U0001d17a"
                      r"\U000e0000-\U000e0fff")
# The same characters escaped, as JSON ("\u200b", "\u007f") and Python's repr ("\x7f", "\xad", "\u2060") print them.
_ESCAPED_INVISIBLE = (
    r"\\u(?:000[0-8]|000[eEfF]|001[0-9a-bA-B]|007[fF]|008[0-46-9a-fA-F]|009[0-9a-fA-F]|00[aA][dD]|034[fF]|061[cC]"
    r"|115[fF]|1160|17[bB][45]|180[b-fB-F]|200[b-fB-F]|202[a-eA-E]|206[0-9a-fA-F]|3164|[fF][eE]0[0-9a-fA-F]"
    r"|[fF][eE][fF][fF]|[fF][fF][aA]0|[fF][fF][fF][0-8])"
    r"|\\x(?:0[0-8]|0[eEfF]|1[0-9a-bA-B]|7[fF]|8[0-46-9a-fA-F]|9[0-9a-fA-F]|[aA][dD])"
    r"|\\U000(?:1[bB][cC][aA][0-3]|1[dD]17[3-9aA]|[eE]0[0-9a-fA-F]{3})")
INVISIBLE = re.compile(_CSI + rf"|{_ESCAPED_INVISIBLE}|[\x00-\x08\x0e-\x1b\x7f-\x84\x86-\x9f{_FORMAT_CHARACTERS}]")
# Main's own: a lone control or format character, not a colour code, an escaped one, DEL or a C1 control
# (_NEW_INVISIBLE).
_MAIN_INVISIBLE_RUN = re.compile(rf"[\x00-\x08\x0e-\x1b{_FORMAT_CHARACTERS}]+")
_NEW_INVISIBLE = re.compile(_CSI + rf"|{_ESCAPED_INVISIBLE}|[\x7f-\x84\x86-\x9f]")

# Google's app password, four groups of four lower-case letters, after a password's name and "=" or in quotes. A
# character a reader does not see may stand anywhere among its letters, as one may in any value read through them.
# A colour code is read whole, never its introducer alone, as ESC or 0x9B by themselves, so a letter after one is
# read one way only and a run of colour codes costs time in proportion.
_UNSEEN = (rf"(?:{_CSI}|(?!\\(?:u001[bB]|x1[bB]|u009[bB]|x9[bB]))(?:{_ESCAPED_INVISIBLE})"
           rf"|[\x00-\x08\x0e-\x1a\x7f-\x84\x86-\x9a\x9c-\x9f{_FORMAT_CHARACTERS}])*")
_APP_GROUP = _UNSEEN.join(["[a-z]"] * 4)
_APP_GROUPS = rf"({_UNSEEN}{_APP_GROUP}(?:{_UNSEEN} {_UNSEEN}{_APP_GROUP}){{3}}{_UNSEEN})"
# On a line of its own, as YAML writes one, after an unquoted colon too: the four groups and nothing after them but a
# comment. Neither reading takes a sentence of common words (_app_password_span).
APP_PASSWORD_LINE = re.compile(r"(?m)(?:^|(?<=\\n))[ \t]*(?:export[ \t]+|-[ \t]+)?(?i:" + _KEY_NAME[4:] + r")([ \t]*:[ \t]*)"
                               + _APP_GROUPS + r"(?=[ \t]+#[^\r\n]*$|[ \t]*\r?$|\\[nr])")
# Four-letter words common in English and in technical text, defined at the end of this file: four groups holding
# three or more of them are a sentence ("password: read from smtp logs"), which a random app password's groups are
# about once in a million times.
APP_PASSWORD = re.compile(_KEY_NAME + r"""((?:[ \t]*=|[\"']?[ \t]*[:=][ \t]*[\"'])[ \t]*)""" + _APP_GROUPS
                          + r"(?![A-Za-z0-9])")
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


def _quoted(flag):
    """`flag` bare or in quotes that open and close around it, plain or escaped ('"-p"', "\\'-p\\'"), not a quote that
    opens an argument the flag only starts ('--comment "-p release-2026"')."""
    return rf"(?:{flag}|\"{flag}\"|'{flag}'|\\\"{flag}\\\"|\\'{flag}\\')"


# aws configure set's setting about a secret, its key word looked for ahead (Python before 3.12 takes no backslash in
# an f-string's braces, so it stands apart).
_AWS_SETTING = r"(?:[A-Za-z0-9_-]+\.)?(?=[A-Za-z0-9_]*?(?:secret|token|password))[A-Za-z0-9_]+"


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
    (rf"az{_FLAG_QUOTE}\s+login|mongosh|mongo|mongodump|mongorestore|mongoexport|mongoimport|twine",
     rf"\s{_quoted('-p')}[ \t]+{_FLAG_QUOTE}", False),
    (r"keytool|jarsigner", rf"\s{_quoted('-(?:src|dest|new)?(?:storepass|keypass)')}[ \t]+{_FLAG_QUOTE}", "dash"),
    # aws configure set's secret settings, and a .netrc line's password, its fields in any order but with a login
    # among them, so "the machine room password sheet" is no .netrc line, and "default" only where a line or a quoted
    # string starts, as a .netrc file writes it ("the default login page password field" is prose). A setting's name is read once, its key word
    # looked for ahead: read around the key word, a run of key words took time in the square of its length.
    (rf"aws{_FLAG_QUOTE}\s+configure\s+set", rf"\s{_quoted(_AWS_SETTING)}[ \t]+{_FLAG_QUOTE}", False),
    (r"(?:machine[ \t]+[^\s]+|(?:^|(?<=\n)|(?<=\\n)|(?<=[\"']))[ \t]*default)"
     r"(?=(?:[ \t]+(?:password|account)[ \t]+[^\s]+)*[ \t]+login[ \t]+[^\s])",
     r"\spassword[ \t]+(?:\\?['\"])?", "field"),
)
_MAIN_COMMANDS = 5  # main's own rows of COMMAND_FLAGS; the rest are this PR's
# A command's line runs to a pipe, ";" or a line break, plain or escaped (_LINE). For the rows main's own lack, it is
# read as the shell reads it (_command_line), so a quoted password may hold a pipe ("az login -p 'Tr0ub4dor|Zk9q'").
_LINE = r"(?:[^\n|;\\]|\\(?![nr]))*"
# Where the name of one of this PR's commands ends: past a ".exe", and not before a path's next part, as
# "/opt/keytool/bin/keytool" names a folder first ("/", a backslash, or one escaped as "\\\\"), though a backslash
# that escapes a line break, a tab or a quote ends the name.
_PROGRAM_END = r"(?:\.exe)?(?![A-Za-z0-9_.-])(?!/|\\(?![nrt\"']))"
_COMMANDS = [(re.compile(rf"(?i)(?:(?<![A-Za-z0-9_.-])|{_AFTER_ESCAPE})(?:{command})"
                         + (rf"(?![A-Za-z0-9_.-]){_LINE}" if n < _MAIN_COMMANDS else _PROGRAM_END)),
              re.compile(rf"({flag})((?:{_CLOSE}[^ \t\n\r'\"])+)"), always)
             for n, (command, flag, always) in enumerate(COMMAND_FLAGS)]
_ANY_COMMAND = re.compile(r"(?i)curl|mysql|mariadb|sshpass|login|redis-cli|mongo|twine|keytool|jarsigner|aws")
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

# Docker's config holds a registry login in an "auth" field, base64 of "user:password".
_AUTH_FIELD = re.compile(rf"(?i)((?:(?<![A-Za-z0-9_.-])|{_AFTER_ESCAPE})auth{_QUOTE}?{_GAP}[:=]{_GAP}{_QUOTE}?)"
                         r"([A-Za-z0-9+/]{4,}={0,2})")



# Provider tokens kb takes from gitleaks (kb/gitleaks.py), each a token's own shape: compiled ASCII-only, as Go reads
# \w and \b; the group holding the secret; the least entropy it has; keywords one of which a text holds before the rule
# is read; and what marks a secret as none, the rule's and gitleaks' own for every rule.
# gitleaks ends many tokens only at a space, a quote, a backtick, ";" or a line break. kb ends them too at a mark no token
# goes on through, as CSV's comma, a closing bracket, a sentence's full stop, Markdown's "**" and any backslash that
# starts an escape do ("dop_v1_...,active", "**dop_v1_...**", '"dop_v1_...\\"' in JSON in JSON), and lets
# Azure's client secret, whose letters take a full stop, stand inside brackets, braces and Markdown's "**" too.
_TOKEN_EDGES = (
    (r"""(?=[\x60'"\s;]|\\[nr]|$)""", r"""(?=[\x60'"\s;,.:)\]}>|&!?<*\\]|$)"""),
    (r"""(?:^|[\\'"\x60\s>=:(,)])""", r"""(?:^|[\\'"\x60\s>=:(,)\[{*])"""),
    (r"""(?=$|[\\'"\x60\s<),])""", r"""(?=$|[\\'"\x60\s<),\]};|&*])"""),
)


def _kb_edges(source):
    """A gitleaks rule's source with the token's edges kb reads (_TOKEN_EDGES)."""
    for theirs, ours in _TOKEN_EDGES:
        source = source.replace(theirs, ours)
    return source


_GITLEAKS = [(re.compile(_kb_edges(source), re.ASCII), group, entropy, keywords, [re.compile(a) for a in allowed],
              stopwords)
             for _, source, group, entropy, keywords, allowed, stopwords in gitleaks.RULES]
_GITLEAKS_ALLOW = [re.compile(a) for a in gitleaks.ALLOW_REGEXES]
# The same rules with no word boundary before them, for a token right after a letter where an invisible character stood.
_GITLEAKS_AFTER_A_BREAK = [(re.compile(re.sub(r"^((?:\(\?i\))?)(?:\\b|\(\?<!\[A-Za-z0-9\]\))", r"\1", rule[0].pattern),
                                       re.ASCII), *rule[1:]) for rule in _GITLEAKS]


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
REVISION = 10
VERSION = hashlib.sha256(repr((REVISION, [p.pattern for p in SECRET_PATTERNS + PREFIXED_SECRETS], KEYED_SECRET.pattern,
                                ENV_KEY_SECRET.pattern, JSON_SECRET.pattern, FLAG_SECRET.pattern, AUTH_SECRET.pattern,
                                COMMAND_FLAGS, PASSWORD_NAME.pattern, sorted(PLACEHOLDERS), REFERENCE.pattern,
                                _AUTH_FIELD.pattern, INVISIBLE.pattern, gitleaks.VERSION,
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
    if _DATE_OR_TIME.fullmatch(value):
        return False  # a key's expiry or a session's start, as "session_key: 2026-11-01T10:00:00Z" names
    return (len(value) >= 16 and not value.isdigit()) or bool(len(value) >= 8 and mixed)


# A value that is a number, a date or a time, which no key or token is.
_DATE_OR_TIME = re.compile(r"[\d:.+TZ-]+")
_SEP_CHARACTER = re.compile(r"[:=\uff1a\uff1d]|%(?:25)?3[ADad]|u003[AaDd]")


def _prose(sep):
    """Whether a separator reads as prose does, "is" or a space with no "=" or ":" in it, after which a value of
    letters only is a word and not a password."""
    return not _SEP_CHARACTER.search(sep) and ("is" in sep.lower() or sep[-1:].isspace())


def _docker_login_edits(text):
    """Each Docker "auth" login, base64 of "user:password", as an edit that masks it."""
    return [(m.end(1), m.end(), "[masked]") for m in _AUTH_FIELD.finditer(text) if _basic_credential(m.group(2))]


def _command_line(text, start, stop=None, known=None):
    """(where a command's line from `start` ends, by `stop` if given, the stretches of it inside quotes or a substitution at its top level,
    in order, the command substitutions in it that no other holds, in order), as the shell reads it. The shell's contexts are kept on a stack:
    single quotes, which hold everything to the next one; double quotes, which hold a substitution or a backtick; and
    "$(", "(" and backticks, which hold quotes and brackets of their own, so arithmetic's "$((...))" closes as it opens.
    At the top level the line ends at what ends a command or starts another (a pipe, ";", "&" but in a redirection
    such as "2>&1", a bracket), at a comment (a "#" that starts a word), and at a JSON string's end (a '"' that opens no
    quote, before ",", ":", "}" or "]"); anywhere at a line break, plain, or escaped but inside quotes of plain text,
    where quotes still open hold to the line's end, as a script over several lines in quotes holds its own flags. A
    line whose first double quote outside single quotes comes escaped ('\\"'), as JSON holds a command, is read
    unescaped: its escaped quotes are the shell's quotes, an escaped backslash escapes the shell character after it,
    and a bare '"' ends the JSON string. `known` maps where each substitution read before starts to where it ends, which
    the reading adds to and passes over, so a command inside one costs no second reading of what it holds."""
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


_NETRC_FIELD = re.compile(r"(?<!\S)(?:machine|login|password|account|macdef)[ \t]+\Z")


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


def _command_password_edits(text, line_pattern, flag, always, unseen=False):
    """Each password one row of COMMAND_FLAGS finds, as an edit that masks it. A command's line is read once from its
    first word, and a command in a substitution once from its own, so a line that names the command again and again
    costs time in proportion to its length. With `unseen`, as the rows main's own lack are read, the line is read as
    the shell reads it (_command_line), past a quote that closes the command's quoted name, and a value is judged
    without the characters a reader does not see, so a coloured "$STOREPASS" is a variable and a coloured "--quiet"
    the next option."""
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
            end, inside, subs = _command_line(text, begin, held and held[1], known)
        else:
            end = line.end()
        reading.append((end, subs))
        starts = [a for a, _ in inside]
        for m in flag.finditer(text, line.start(), end):
            k = bisect.bisect_right(starts, m.start()) - 1
            if k >= 0 and m.start() < inside[k][1]:
                continue  # a flag inside a quoted argument or a substitution is another program's

            value = INVISIBLE.sub("", m.group(2)) if unseen else m.group(2)
            if always == "field":  # a .netrc line's password, which nothing expands: any value but a placeholder
                if _netrc_password(value):
                    edits.append((m.start(2), m.end(2), "[masked]"))
                continue
            if unseen and (value.startswith("-") and (always != "dash" or value[1:].lower() in _JAVA_OPTIONS
                                                      or value.startswith("-J"))
                           or _ABOUT_A_PASSWORD.search(m.group(1).strip(" \t'\"\\"))):
                continue  # the next option, or a setting about a secret ("aws configure set token_expiration 3600")
            if always is True or _looks_secret("password", m.group(1), value):
                edits.append((m.start(2), m.end(2), "[masked]"))
    return edits


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
    value = INVISIBLE.sub("", value)
    return not _PATH_VALUE.match(value) and _password_shaped(value)


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


def _line_password_span(m):
    """Where a password's line value is, when the line holds more past where _VALUE would end it."""
    return _password_span(m) if re.search(r"[,;<>)\]}].", m.group(3)) else None


def _app_password_span(m):
    """_password_span for an app password's four groups, but where three or four are common words ("password: this
    will work fine", "password: read from smtp logs"), read as main reads them: a random app password's groups hold
    three such words about once in a million times."""
    groups = INVISIBLE.sub("", m.group(3)).split()
    return None if sum(group in _COMMON_WORDS for group in groups) >= 3 else _password_span(m)


def _xml_span(m):
    """Where an XML element's secret is: _keyed_span, but for an element about a password (_ABOUT_A_PASSWORD), and for
    one not named for a password, a value without a credential's shape: an element named "token" or "secret" holds a
    word as often as a secret ("<tokens><token>internationalization</token></tokens>")."""
    name, value = m.group(1), INVISIBLE.sub("", m.group(3))
    if _ABOUT_A_PASSWORD.search(name) or not _XML_PASSWORD.search(name) and not _password_shaped(value):
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
_NUMBER_DATE_OR_TIME = re.compile(r"[\d:.+TZ-]+")


def _random_run(text, shortest=12):
    """Whether `text` reads as random: `shortest` or more characters mixing letters and digits, of both cases when
    shorter than 16, and no plain word."""
    return (len(text) >= shortest and bool(re.search(r"\d", text)) and bool(re.search(r"[A-Za-z]", text))
            and (len(text) >= 16 or bool(re.search(r"[a-z]", text) and re.search(r"[A-Z]", text)))
            and not _PLAIN_WORD.fullmatch(text))


def _credential_shaped(word):
    """Whether a lone token reads as a credential: 12 or more characters with no whitespace, mixing letters and digits,
    not a number, a date or a time, and either holding a random run of 10 or more between its separators ("live_<run>",
    a three-part token), or holding no plain word and reading as random whole (a UUID). An identifier with a plain word
    in it stays: "RFC7235-compliant", "WPA2-Enterprise"."""
    word = word.rstrip(".,;:!?)")
    if len(word) < 12 or not re.search(r"\d", word) or not re.search(r"[A-Za-z]", word) or re.search(r"\s", word):
        return False
    if _NUMBER_DATE_OR_TIME.fullmatch(word):
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
    """Whether a word reads as a code identifier: no digit runs into lower case letters, and at least half its letters
    stand in runs of three or more, read as camel case splits them."""
    if _DIGIT_INTO_A_WORD.search(word):
        return False
    runs = [run for run in _LABEL_WORD.findall(word) if run.isalpha()]
    letters = sum(map(len, runs))
    return bool(letters) and 2 * sum(len(run) for run in runs if len(run) >= 3) >= letters


def _password_shaped(word):
    """_credential_shaped, but no code identifier: for the readings main's own lack, which judge a value by its shape
    alone where main has no reading at all."""
    return _credential_shaped(word) and not _identifier_shaped(word.rstrip(".,;:!?)"))


def _bare_credential(word):
    """The credential-shaped token a header's value starts with, up to an escaped line break or a placeholder, and
    without a closing mark after it; or "". Judged by its shape alone, as a reader sees it, without the characters a
    reader does not see, so base64 that starts with "/" counts and "RFC7235-compli<SHY>ant" does not."""
    head = _CREDENTIAL_END.split(word, 1)[0].rstrip(".,;:!?)")
    # As this view reads it, and as main's own does, without its own invisible characters only.
    shaped = _credential_shaped(INVISIBLE.sub("", head)) or _credential_shaped(_MAIN_INVISIBLE_RUN.sub("", head))
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
# The rest of a key after an older kb's placeholder: base64 glued to it and running to a line break, as main's key
# pattern leaves when it took the line break too; or a key's body from its first line, cut anywhere, after what can
# stand between a line's start and its text: line breaks plain or escaped at any depth, quotes, "#", ">", "+", <br> and
# spaces. main's pattern stops at the first character that is not base64 or a space, so in escaped text, in code with
# a string a line, behind comment or quote marks and where a chunk ended between a header and its line break, it
# leaves exactly that. The body's first line is base64 to its end, or to a quote, a backslash or a cut's "…", so a
# word that only starts like one, followed by more words, stays. Masked through its footer, or through the end of its
# paragraph.
# Each alternative takes a character no other does: quotes are _QUOTE's alone, as a run of them read two ways each
# took time exponential in its length.
_BEFORE_BODY = rf"(?:\\{{1,7}}[nrt]|{_QUOTE}|<br\s*/?>|[\s#>+,;|*\]])"
_KEY_REST = re.compile(rf"\[masked\]((?:[A-Za-z0-9+/=]+[ \t\r]*(?=\n|\\(?:r\\)?n)"
                       rf"|(?={_BEFORE_BODY}{{0,64}}{_KEY_BODY_START}[A-Za-z0-9+/=]{{36,}}[ \t\r]*(?:\n|[\\\"'<…]|\Z)))"
                       rf"(?:(?!{_KEY_END}){_IN_PARAGRAPH})*(?:{_KEY_END})?)")


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


def _key_rest_edits(text):
    """The rest of a key after an older kb's placeholder, as _KEY_REST finds it, as an edit that removes it."""
    return [(m.start(1), m.end(), "") for m in _KEY_REST.finditer(text)]


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
                 *((p, _password_span) for p in QUOTED_PASSWORDS))
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
_NEW_READINGS = frozenset([p for p, _ in _WHOLE_VALUES] + [SHORT_PASSWORD])
_TEXT_RULES = (SECRET_PATTERNS, ((JSON_SECRET, _keyed_span), *_WHOLE_VALUES, (KEYED_SECRET, _keyed_span),
                                 (ENV_KEY_SECRET, _keyed_span), (SHORT_PASSWORD, _short_password_span),
                                 (FLAG_SECRET, _keyed_span), (AUTH_SECRET, _authorization_span)), "named")
_ID_RULES = (_ID_PATTERNS, ((KEYED_ID, _keyed_span), (ENV_KEY_ID, _keyed_span)), "plain")


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


def _run(text, rules, origin=None, named=None, fired=None):
    """One pass of every pattern, as (the text masked, where each of its characters came from): the secrets known by
    their shape, then an older kb's leftovers, links, cookies and commands, then the patterns that read a value by its
    name. With `origin` None, nothing is tracked and the second is None. With `named` a list, each named value masked
    is added to it as (name start, name end, masked start, masked end) in the first text, which needs `origin`. With
    `fired` a list, something is added to it when a reading main's own readings (_MAIN_RULES) lack masks anything:
    with none, this pass masks as theirs does."""
    patterns, keyed, reading = rules
    size = len(text)
    note = fired.append if fired is not None else (lambda _: None)
    for pattern in patterns:
        edits = [(m.start(), m.end(), "[masked]") for m in pattern.finditer(text)
                 if pattern not in (BASIC, BEARER) or not _scheme_words(m)]
        if edits and pattern is ENCODED_PRIVATE_KEY:
            note(pattern)
        text, origin = _edit(text, origin, edits)
    for edits in (_docker_login_edits, _key_rest_edits, _headless_key_edits,
                  lambda text: [(m.start(), m.end(), "[masked]") for m in _GLUED_RUN.finditer(text)]):
        text, origin = _edit(text, origin, edits(text))
    for pattern, main in zip(PREFIXED_SECRETS if reading != "main" else _MAIN_PREFIXED, _MAIN_PREFIXED):
        found = list(pattern.finditer(text))
        if pattern is not main and any(not main.fullmatch(text, m.start(), m.end()) for m in found):
            note(pattern)  # a link with no user name
        text, origin = _edit(text, origin, [(m.end(1), m.end(), "[masked]") for m in found])
    if _ANY_COMMAND.search(text):
        for n, command in enumerate(_COMMANDS[:_MAIN_COMMANDS] if reading == "main" else _COMMANDS):
            edits = _command_password_edits(text, *command, n >= _MAIN_COMMANDS)
            if edits and n >= _MAIN_COMMANDS:
                note(command)
            text, origin = _edit(text, origin, edits)
    if reading == "named" and _SUBSCRIPTION_KEY.search(text):
        note(_SUBSCRIPTION_KEY)  # a name main's own do not read, which moves where their searches go on
    for pattern, span_of in keyed:
        found = list(_named(pattern, span_of, text, reading, note))
        if found and pattern in _NEW_READINGS:
            note(pattern)
        if named is not None:
            for m, (start, end) in found:
                if origin[m.start(1)] >= 0 and origin[m.end(1) - 1] >= 0:
                    named.append((origin[m.start(1)], origin[m.end(1) - 1] + 1, *_back(origin, start, end, size)))
        text, origin = _edit(text, origin, [(start, end, "[masked]") for _, (start, end) in found])
    # gitleaks' rules last, so a named value holding a provider's token is read whole first; and in text only, as an
    # id is read more narrowly (secret_in_id), and an id one took for a token would drop its item.
    if reading == "named":
        for step in (_name_value_edits, _gitleaks_edits):
            edits = step(text)  # read from the text the step before left
            if edits:
                note(edits)
            text, origin = _edit(text, origin, edits)
    return text, origin


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
           for rules in (_TEXT_RULES, _ID_RULES, _MAIN_RULES)}


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
_MAIN_READING_START = _reading_starts(*map(_main_reading, (KEYED_SECRET, ENV_KEY_SECRET, FLAG_SECRET)),
                                      _MAIN_PREFIXED[0], COMMAND_FLAGS[:_MAIN_COMMANDS])  # main's own
# Where one could start: a letter, a digit or "-" after anything but a letter or digit, as "7token is" starts a name.
_MAY_START = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9-])")
_INVISIBLE_RUN = re.compile(f"(?:{INVISIBLE.pattern})+")
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
    if rules is not _TEXT_RULES:
        return _run(text, rules, origin)
    fired = []
    masked, came = _run(text, rules, list(range(len(text))), fired=fired)
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
    split value ends where such a reading starts: the pass over the text as given reads those."""
    # All main's own view hides, the text without its invisible characters only (_main_split_spans): where removing a
    # colour code, DEL or a C1 control too glues a token to a word, or a name to a placeholder, main's still reads it.
    seen_by_main = _main_split_spans(text, rules)
    v = _Split(text, _INVISIBLE_RUN)
    gone, view, places, where, removed = v.gone, v.view, v.places, v.where, v.removed
    if not gone:
        return seen_by_main
    tokens, after_a_break = _TOKENS[id(rules)]
    found = [(m.start(), m.end()) for m in tokens.finditer(view) if m.end() > m.start() and not _scheme_words(m)]
    text_only = rules is _TEXT_RULES  # an id is read without gitleaks' rules, as _run reads it
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
    rules = _MAIN_RULES if rules is _TEXT_RULES else rules
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
    each of its characters came from in `text`, whether it still changed after MAX_PASSES)."""
    origin = list(range(len(text)))
    for _ in range(MAX_PASSES):
        masked, came = step(text, list(range(len(text))))
        if masked == text:
            return text, origin, False
        text, origin = masked, [origin[c] if c >= 0 else -1 for c in came]
    return text, origin, True


def _main_hidden(text):
    """What main's own mask hides of `text`, a mark at each character none of its result came from; None where it
    still changes after MAX_PASSES."""
    _, origin, still = _passes(text, _main_once)
    if still:
        return None
    hidden = bytearray(b"\x01") * len(text)
    for c in origin:
        if c >= 0:
            hidden[c] = 0
    return hidden


def _named_spans(view, rules):
    """The named values a pass masks in `view`, as (name start, name end, masked start, masked end) in the view, read as
    _run reads them: after the secrets known by their shape and an older kb's leftovers are masked, each pattern after
    the one before, so a value ends at a token's placeholder as main ends it. As _read reads the text as written, where
    a reading main's own lack masked anything, theirs read the view too, so none can take a split name first."""
    spans, fired = [], []
    _run(view, rules, list(range(len(view))), spans, fired if rules is _TEXT_RULES else None)
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
            if self.starts is _READING_START and _NEW_INVISIBLE.search(self.text, *self.raw(name_start, name_end + 1)):
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


def _masked(text):
    """mask()'s passes: (the text, where each of its characters came from in `text`, whether it still changed after
    MAX_PASSES). Where a pass read otherwise than main's own could (_once's `novel`), main's own mask is made too
    (_main_hidden), and every character it hides is hidden here: no reading added since can show what main hides, in
    a later pass either, where a placeholder one put down ends a value main reads on through."""
    novel = []
    masked, origin, still = _passes(text, lambda t, o: _once(t, _TEXT_RULES, o, novel))
    if still or not novel:
        return masked, origin, still
    hidden = _main_hidden(text)
    if hidden is None or not any(c >= 0 and hidden[c] for c in origin):
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
    original, novel = text, []
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
        text, _, still = _masked(original)
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
    if any(_basic_credential(m.group(1)) for view in (INVISIBLE.sub("", text), _MAIN_INVISIBLE_RUN.sub("", text))
           for m in _BASIC_VALUE.finditer(view)):
        return True
    return _mask_once(text, _ID_RULES) != text


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
    content, a cookie's "name=" or a credential, or is shaped like a credential under a secret's name."""
    name, value = name.strip(), INVISIBLE.sub("", value).strip()
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
    """mask_all over a list's items, each read alone, and an item after a flag read after the command line before it
    too, up to 64 items back: a command given as its arguments holds a flag and its value in two items, as
    ["login", "--password", "..."] or ["mongosh", "-u", "admin", "-p", "..."] does. The items are joined as a shell
    quotes them, so each keeps its bounds: a flag inside one ("note: -p release-2026") or an apostrophe ("team's
    cert") reads as part of it. Where that line reads more of the item as a secret than the item alone does, the item,
    one argument, is masked whole when what the line masks starts it, as a flag's value; when it starts inside it, as
    a flag the item only quotes does, the item keeps its text up to the mask, as the line reads it."""
    out = [mask_all(v, key) for v in items]
    if len(items) == 2:  # a name and its value, as a list of headers holds them
        out[1] = _pair_value(items[0], items[1], out[1], listed=True)
    for i in range(1, len(items)):
        flag, item = items[i - 1], items[i]
        if not (isinstance(flag, str) and isinstance(item, str) and flag.startswith("-") and len(flag) <= 64 and item):
            continue
        start = i - 1
        while start > 0 and i - start < 64 and isinstance(items[start - 1], str):
            start -= 1
        line, quoted = " ".join(map(shlex.quote, items[start:i])), shlex.quote(item)
        read, head = mask(f"{line} {quoted}"), mask(line) + " "
        if read.endswith(" " + mask(quoted)):
            continue
        tail = read[len(head):] if read.startswith(head) else None
        if tail is None or tail.lstrip("'\"").startswith("[masked]"):
            out[i] = "[masked]"  # the flag's value, one argument, masked whole
            continue
        try:  # a flag inside the item ("note: -u feature:preview"): it keeps its text up to what the line masks
            parts = shlex.split(tail)
        except ValueError:
            parts = []
        out[i] = mask(parts[0]) if len(parts) == 1 else "[masked]"
    return out


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


def _names_a_secret(label):
    """Whether one label names a secret and nothing else: a secret's word or run of words, and besides it only a form's
    own words, joining words, numbers, and a credential's kind (read with a neighbour as well, as camel case splits
    "GitHub") before the secret or after a mark or joining word. One pass, so a long label costs time in proportion to
    its length."""
    words = []  # each word, and "" where a mark stands
    for part in _LABEL_PART.finditer(label):
        if _LABEL_PART_MARK.fullmatch(part.group(0)):
            words.append("")
        else:
            words += [word.lower() for word in _LABEL_WORD.findall(part.group(0))]
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
        elif word not in _FORM_WORDS and not word.isdigit():
            return False
    return secret or kind and "tokens" in words


def _asks_for_a_secret(labels):
    """Whether a field's labels ask for a secret: one names a secret and nothing else (_names_a_secret), and none is
    about one or finds them (_ABOUT_WORDS), read word by word after a selector's own attribute names are taken out
    ("input[name=password]")."""
    asks = False
    for label in labels:
        label = _without_attribute_names(label)
        if _ABOUT_WORDS.intersection(w.lower() for w in _LABEL_WORD.findall(label)):
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
                    secret or not labels and _password_shaped(INVISIBLE.sub("", str(value)))):
                out[key] = "[masked]"
        return out

    out = field(args)
    for key, value in args.items():
        if isinstance(value, list):
            out[key] = [field(item) for item in value]
    return out


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
