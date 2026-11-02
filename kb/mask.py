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
# A run of the letters of hex alone, as a token in hex with no digit is: no word is so long in them.
_HEX_LETTERS = re.compile(r"[A-Fa-f]{12,}")
# The name of a header whose value is a credential, just before the scheme word.
_AUTHORIZATION_BEFORE = re.compile(r"(?i)authorization[\"']?[ \t]*(?::|=|%(?:25)?3A)[ \t]*[\"']?$")


def _scheme_words(m):
    """Whether a match of Basic or Bearer and what follows reads as words in a note's path, as in "Basic
    Statistics/notes.md" or "Ring Bearer responsibilities/notes.md", not a credential. That is: no header's value
    ("Authorization: Bearer ..."); part of a file's path (_in_a_path); past the file's extension, no digit, "+" or
    "=", not the letters of hex alone, and parts joined by "/", ".", "_" or "-", each a word (_PATH_WORD) with a vowel
    and only common English letter pairs (_english), which a run of base64 or of random letters seldom passes; one
    part alone has a third of its letters vowels, as a word has. After Basic, not base64 of "user:password" either,
    padded or not, in any single-byte code page or UTF-8. Any other match is no such run."""
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
    return not (head.group().lower().startswith("basic") and (_basic_credential(run) or _decodes_to_login(run)))


def _english(part):
    """Whether a part of a path reads as English words: each word in it, a part run together in capitals ("ApiKey")
    read word by word, holds only common letter pairs and no four vowels in a row."""
    for word in re.findall(r"[A-Z]{2,}(?![a-z])|[A-Z]?[a-z]+|[A-Z]+", part):
        word = word.lower()
        if _VOWEL_RUN.search(word) or any(word[i:i + 2] not in _COMMON_PAIRS for i in range(len(word) - 1)):
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


def _decodes_to_login(run):
    """Whether `run`, padded as base64 is when written without its "=", decodes to "user:password" with no control
    character in it, as a Basic credential does in UTF-8, Latin-1 or any other single-byte code page."""
    run = run[:-1] if len(run) % 4 == 1 else run
    try:
        raw = base64.b64decode(run + "=" * (-len(run) % 4), validate=True)
    except ValueError:
        return False
    return b":" in raw and not re.search(rb"[\x00-\x1f\x7f]", raw)


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
# A secret's name is a whole run of name characters whose last dotted part holds a key word, or one ending in "_pw" or
# the like. Read as that part after the run's last dot, it takes one pass over a long run; read as any characters
# around a key word, it went back over the run once for every key word in it.
_KEY_NAME = (
    r"(?i)(?<![A-Za-z0-9_.-])("
    r"(?:[A-Za-z0-9_.-]*\.)?(?=[A-Za-z0-9_-]*?(?:api[_-]?key|access[_-]?key|private[_-]?key|secret|token|"
    r"credentials?|passw(?:or)?d|passcode|session[_-]?(?:token|key|secret|cookie)|bw_session))[A-Za-z0-9_-]+"
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


# Characters a reader does not see: NUL and the other C0 controls that are not whitespace, as UTF-16 text read as bytes
# holds them, and Unicode's default ignorable code points: zero-width spaces and joiners, direction marks and isolates,
# the soft hyphen, the byte order mark, variation selectors, fillers and tag characters. A secret reads through them,
# and one before a secret parts it from the word before (_once). The vertical tab, the form feed and the four
# separators from 0x1c are whitespace, as str.split reads them, and part words as a line break does: pdftotext puts a
# form feed between pages.
INVISIBLE = re.compile(r"[\x00-\x08\x0e-\x1b\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e"
                       r"\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufff8\U0001bca0-\U0001bca3"
                       r"\U0001d173-\U0001d17a\U000e0000-\U000e0fff]")

# Which mask() a stored row was masked with. A row of another version, or of none, is masked again when read and by
# the next ingest. The patterns change it by themselves; bump REVISION when the code that applies them changes what
# it masks.
REVISION = 9
VERSION = hashlib.sha256(repr((REVISION, [p.pattern for p in SECRET_PATTERNS + PREFIXED_SECRETS], KEYED_SECRET.pattern,
                                ENV_KEY_SECRET.pattern, JSON_SECRET.pattern, FLAG_SECRET.pattern, AUTH_SECRET.pattern,
                                COMMAND_FLAGS, PASSWORD_NAME.pattern, sorted(PLACEHOLDERS), REFERENCE.pattern,
                                _AUTH_FIELD.pattern, INVISIBLE.pattern)).encode()).hexdigest()[:16]


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


def _docker_login_edits(text):
    """Each Docker "auth" login, base64 of "user:password", as an edit that masks it."""
    return [(m.end(1), m.end(), "[masked]") for m in _AUTH_FIELD.finditer(text) if _basic_credential(m.group(2))]


def _command_password_edits(text, line_pattern, flag, always):
    """Each password one row of COMMAND_FLAGS finds, as an edit that masks it. A command's line is read once from its
    first word, so a line that names the command again and again costs time in proportion to its length."""
    return [(m.start(2), m.end(2), "[masked]") for line in line_pattern.finditer(text)
            for m in flag.finditer(text, line.start(), line.end())
            if always or _looks_secret("password", m.group(1), m.group(2))]


def _keyed_span(m):
    """Where a named value's secret is, the whole value, or None when it reads as none."""
    name, sep, value = m.groups()
    return (m.start(3), m.end(3)) if _looks_secret(name, sep, value) else None


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


def _bare_credential(word):
    """The credential-shaped token a header's value starts with, up to an escaped line break or a placeholder, and
    without a closing mark after it; or "". Judged by its shape alone, as a reader sees it, without the characters a
    reader does not see, so base64 that starts with "/" counts and "RFC7235-compli<SHY>ant" does not."""
    head = _CREDENTIAL_END.split(word, 1)[0].rstrip(".,;:!?)")
    return head if _credential_shaped(INVISIBLE.sub("", head)) else ""


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
_TEXT_RULES = (SECRET_PATTERNS, ((JSON_SECRET, _keyed_span), (KEYED_SECRET, _keyed_span), (ENV_KEY_SECRET, _keyed_span),
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


def _run(text, rules, origin=None, named=None):
    """One pass of every pattern, as (the text masked, where each of its characters came from): the secrets known by
    their shape, then an older kb's leftovers, links, cookies and commands, then the patterns that read a value by its
    name. With `origin` None, nothing is tracked and the second is None. With `named` a list, each named value masked
    is added to it as (name start, name end, masked start, masked end) in the first text, which needs `origin`."""
    patterns, keyed, reading = rules
    size = len(text)
    for pattern in patterns:
        text, origin = _edit(text, origin, [(m.start(), m.end(), "[masked]") for m in pattern.finditer(text)
                                            if pattern not in (BASIC, BEARER) or not _scheme_words(m)])
    for edits in (_docker_login_edits, _key_rest_edits, _headless_key_edits,
                  lambda text: [(m.start(), m.end(), "[masked]") for m in _GLUED_RUN.finditer(text)]):
        text, origin = _edit(text, origin, edits(text))
    for pattern in PREFIXED_SECRETS:
        text, origin = _edit(text, origin, [(m.end(1), m.end(), "[masked]") for m in pattern.finditer(text)])
    if _ANY_COMMAND.search(text):
        for command in _COMMANDS:
            text, origin = _edit(text, origin, _command_password_edits(text, *command))
    for pattern, span_of in keyed:
        found = list(_named(pattern, span_of, text, reading))
        if named is not None:
            for m, (start, end) in found:
                if origin[m.start(1)] >= 0 and origin[m.end(1) - 1] >= 0:
                    named.append((origin[m.start(1)], origin[m.end(1) - 1] + 1, *_back(origin, start, end, size)))
        text, origin = _edit(text, origin, [(start, end, "[masked]") for _, (start, end) in found])
    return text, origin


def _pass(text, rules):
    """One pass of every pattern over `text`, as main reads it."""
    return _run(text, rules)[0]


def _named(pattern, span_of, text, reading):
    """Each of `pattern`'s matches, a name, a separator and a value, whose secret `span_of` finds, with that span. In
    text a match that holds none is tried again at its value, which can be the next name, as in "secrets: db_password:
    <value>"; past that the search goes on after the match, as re.sub's does, so a long value is read once. An id reads
    as re.sub reads it."""
    m = pattern.search(text)
    while m:
        span = span_of(m)
        if span:
            yield m, span
            m = pattern.search(text, m.end())
            continue
        inner = pattern.match(text, m.start(3)) if reading == "named" else None
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
           for rules in (_TEXT_RULES, _ID_RULES)}


def _scoped(source):
    """A pattern's source with its leading "(?i)", if any, scoped to it, so it joins others in one pattern."""
    return f"(?i:{source[4:]})" if source.startswith("(?i)") else f"(?:{source})"


# Where a secret's reading starts, as the patterns that read it start one, lookbehinds and all: a secret's name and
# what parts it from its value, a scheme word, a link's scheme, a command a password follows, openssl's "-pass" and a
# key's header.
_READING_START = re.compile("|".join(_scoped(source) for source in (
    KEYED_SECRET.pattern[:-len(_VALUE)], ENV_KEY_SECRET.pattern[:-len(_VALUE)], FLAG_SECRET.pattern[:-len(_VALUE)],
    AUTH_SECRET.pattern[:AUTH_SECRET.pattern.index(_SCHEME_WORD)],
    PREFIXED_SECRETS[0].pattern[:PREFIXED_SECRETS[0].pattern.index(f"(?:{_CLOSE}")],
    _AUTH_FIELD.pattern[:_AUTH_FIELD.pattern.rindex("(")], rf"(?i){_WORD}(?:bearer|basic){_SCHEME_GAP}",
    rf"(?:(?<![A-Za-z0-9+.-])|{_AFTER_ESCAPE})[a-z][a-z0-9+.-]{{0,31}}:(?:\\?/){{2}}",
    rf"(?i)(?:(?<![A-Za-z0-9+.-])|{_AFTER_ESCAPE})[a-z][a-z0-9+.-]{{0,31}}%(?:25)?3A(?:%(?:25)?2F|/){{2}}",
    rf"(?i)(?:(?<![A-Za-z0-9_.-])|{_AFTER_ESCAPE})(?:" + "|".join(command for command, _, _ in COMMAND_FLAGS)
    + r")(?![A-Za-z0-9_.-])",
    r"(?<![A-Za-z0-9_-])-pass(?:in|out)?[ \t]+['\"]?pass:", "-----BEGIN ")))
# Where one could start: a letter, a digit or "-" after anything but a letter or digit, as "7token is" starts a name.
_MAY_START = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9-])")
_INVISIBLE_RUN = re.compile(INVISIBLE.pattern + "+")
# How far a token that starts right after a letter, where an invisible character stood, is read before it counts as a
# longer one: each such start is read once, so repeated starts cost time in proportion to the text.
_GLUED_START_WINDOW = 512


def _mask_once(text, rules=_TEXT_RULES):
    """One pass of every pattern over the text as written, as main reads it, and with it each secret only the text read
    without its invisible characters holds (_split_secrets)."""
    return _once(text, rules)[0]


def _once(text, rules, origin=None):
    """_mask_once, and with `origin`, where each character of the result came from in the first text (_edit). The pass
    reads the text as given, never what the view masked, so what it hides stays hidden: the view only adds. Where it
    adds, the characters of the pass's own result that came from the view's spans are hidden, a placeholder for each
    run of them but one beside a placeholder, which takes them in; the rest of the result stays as the pass left it. A
    text whose invisible characters split no secret masks as main masks it."""
    spans = _split_secrets(text, rules) if INVISIBLE.search(text) else []
    if not spans:
        return _run(text, rules, origin)
    masked, came = _run(text, rules, list(range(len(text))))
    added = bytearray(len(text))
    for start, end in spans:
        added[start:end] = b"\x01" * (end - start)
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
    return masked, None if origin is None else [origin[c] if c >= 0 else -1 for c in came]


def _split_secrets(text, rules):
    """Where in `text` a secret reads only across characters a reader does not see, as ordered (start, end) spans of
    the text as written: a token split or interleaved by them, even where its first part is a token by itself, the
    rest of one an older kb masked only up to them ("[masked]<ZWSP>rest"), or a secret's name split by them, whose
    value is masked. The text read without them is the view. In it a token may start right after a letter where one
    stood, as after a space, so one before a key glued to a word reads it ("budget<ZWSP>s<ZWSP>k-..."). A run of them
    just before where a secret's reading starts stays in the view, as a break. A split token ends before the last of
    them ahead of any reading the text as written starts inside it, such as a link's scheme in the token's tail, and a
    split value ends where such a reading starts: the pass over the text as given reads those."""
    gone = [i for run in _INVISIBLE_RUN.finditer(text) if not _READING_START.match(text, run.end())
            for i in range(run.start(), run.end())]
    if not gone:
        return []
    view = "".join(text[a + 1:b] for a, b in zip([-1] + gone, gone + [len(text)]))
    # The view's i-th character sits after every removed character whose place in the view is i or before.
    places = [i - n for n, i in enumerate(gone)]
    where = sorted(set(places))
    tokens, after_a_break = _TOKENS[id(rules)]
    found = [(m.start(), m.end()) for m in tokens.finditer(view) if m.end() > m.start() and not _scheme_words(m)]
    glued, inside = [], 0
    for place in where:
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

    def split(start, end):  # whether a place lies inside
        n = bisect.bisect_right(where, start)
        return n < len(where) and where[n] < end

    def raw(start, end):  # the same stretch of the text as written, without the invisibles at either edge
        return start + bisect.bisect_right(places, start), end + bisect.bisect_left(places, end)

    def reading_inside(start, end):  # where in text[start:end] the text as written starts a reading, or None
        for m in _MAY_START.finditer(text, start, end):
            if _READING_START.match(text, m.start()):
                return m.start()
        return None

    # What an older kb, masking only up to an invisible character, left of a token glued to its placeholder.
    rests = [(m.start(), m.end()) for m in _GLUED_RUN.finditer(view)]
    removed, spans = set(gone), []

    def before_reading(start, end, first):
        """[start, end) cut before the run of invisible characters last ahead of a reading the text as written starts
        in it past `first`, which stays as a break; None when no such run lies inside."""
        at = reading_inside(first, end)
        if at is None:
            return start, end
        end = gone[bisect.bisect_left(gone, at) - 1] if bisect.bisect_left(gone, at) else start
        while end - 1 in removed:
            end -= 1
        return (start, end) if start < end else None

    for start, end in found + glued + rests:
        if not split(start, end):
            continue
        start, end = raw(start, end)
        cut = before_reading(start, end, gone[bisect.bisect_left(gone, start)] + 1)
        # Still split by one: the text as written reads its first part as main does.
        if cut and bisect.bisect_left(gone, cut[0]) < bisect.bisect_left(gone, cut[1]):
            spans.append(cut)
    for name_start, name_end, start, end in _named_spans(view, rules):
        # Only a split name, or one parted from its separator: the text as written reads a value through invisible
        # characters already, and one past the separator, as on a blank line after "password:", splits no name.
        if not split(name_start, name_end + 1) or start >= end:
            continue
        start, end = raw(start, end)
        at = reading_inside(start, end)
        if at is not None:
            end = at
        if start < end:
            spans.append((start, end))
    merged = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _named_spans(view, rules):
    """The named values a pass masks in `view`, as (name start, name end, masked start, masked end) in the view, read as
    _run reads them: after the secrets known by their shape and an older kb's leftovers are masked, each pattern after
    the one before, so a value ends at a token's placeholder as main ends it."""
    spans = []
    _run(view, rules, list(range(len(view))), spans)
    return spans


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


def mask_with_origin(text):
    """mask(), and where each character of the result came from in `text`, -1 for a placeholder's (kb.mask._edit)."""
    original, origin = text, list(range(len(text)))
    for _ in range(MAX_PASSES):
        masked, came = _once(text, _TEXT_RULES, list(range(len(text))))
        if masked == text:
            return text, origin
        text, origin = masked, [origin[c] if c >= 0 else -1 for c in came]
    # Still changing, as mask() then reads it: the region still changing becomes one placeholder.
    head = len(os.path.commonprefix([original, text]))
    tail = len(os.path.commonprefix([original[head:][::-1], text[head:][::-1]]))
    return (original[:head] + "[masked]" + original[len(original) - tail:],
            list(range(head)) + [-1] * 8 + list(range(len(original) - tail, len(original))))


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
    itself, such as "password", is read apart too: the metadata says it is a name, and an id with no such name
    recorded, such as a note's "password:...", is still read whole."""
    name = names[0] if names else None
    if isinstance(name, str) and name and text.startswith(f"{name}:") and not text.startswith("//", len(name) + 1):
        return secret_in_id(name) or secret_in_id(text[len(name) + 1:], names[1:])
    if any(_basic_credential(m.group(1)) for m in _BASIC_VALUE.finditer(INVISIBLE.sub("", text))):
        return True
    return _mask_once(text, _ID_RULES) != text


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
