"""This branch's mask held to main's, frozen in tests/fixtures/main_mask.py (main at 3be191e).

A pass reads the text as written as main does, after masking in place only the secrets the text read without its
invisible characters holds (mask._split_secrets). So:
1. A text with no invisible character masks as on main, byte for byte, but for the credential-shaped token after a
   header-style Authorization with no scheme word, which this branch masks by design. The reading of the text as
   written differs from main's by that token alone, for any text.
2. Every character main hides, this branch hides, but for an invisible character, which no secret is made of. The
   positions are exact: each pass reports where every character of its result came from.
3. Every character this branch hides past its reading of the text as written sits by an invisible character the text
   holds: in the run of characters it is in, or in a value whose name, on the same line just before it, holds one.

Gate 9's readings, new names and shapes, and the four values it reads as no secret, are this branch's only other
changes. With them switched off (gate9()), the reading as written is held to main's by 1. With the readings on and
the four off, it hides all it did and more only where a new reading reads; with the four on too, it shows past that
only the values they name.

The texts: a generator of secrets, words and invisible characters that splits some secrets and not others, the write
fuzz's planted texts and titles, and the old-row cases with what older kbs' masks left of them. Each text runs as
written and without its invisible characters. KB_DIFF_CASES sets how many of each.
"""
import importlib.util
import os
import random
import re
import string
import unittest

from kb import mask as head
from kb.mask import INVISIBLE
from tests import legacy_mask, legacy_store
from tests.test_old_rows import Case
from tests.test_write_fuzz import Planted

CASES = int(os.environ.get("KB_DIFF_CASES", "300"))
PLACEHOLDER = "[masked]"


def _main():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "main_mask.py")
    spec = importlib.util.spec_from_file_location("main_mask", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


main = _main()


# Gate 9's new readings, its new names, gitleaks' rules, and the four values it reads as no secret (a name in an AWS
# resource's name, a file's name of words after "/", a date or a time after a key's name, a tokenizer's name), each
# switched off.
_NEVER = re.compile(r"(?!)")
_NAME_OFF = (("subscription[_-]?key|", ""),)
_TOKENIZER_OFF = (("token(?!i[sz])", "token"),)


def _keyed(rules, fragments, drop=()):
    """`rules`' keyed patterns, without `drop`, each recompiled with `fragments` of its source replaced."""
    out = []
    for pattern, span_of in rules[1]:
        if pattern in drop:
            continue
        source = pattern.pattern
        for old, new in fragments:
            source = source.replace(old, new)
        out.append((re.compile(source, pattern.flags), span_of))
    return tuple(out)


_NEW_READINGS = tuple(p for p, _ in head._WHOLE_VALUES) + (head.SHORT_PASSWORD,)
_ALL_OFF = ([p for p in head.SECRET_PATTERNS if p is not head.ENCODED_PRIVATE_KEY],
            _keyed(head._TEXT_RULES, _NAME_OFF + _TOKENIZER_OFF, _NEW_READINGS), "named")
_FOUR_OFF = (head.SECRET_PATTERNS, _keyed(head._TEXT_RULES, _TOKENIZER_OFF), "named")


def _main_named(pattern, span_of, text, reading):
    """mask._named as main reads: after a masked value the search goes on past it, never at it."""
    m = pattern.search(text)
    while m:
        span = span_of(m)
        if span:
            yield m, span
            m = pattern.search(text, m.end())
            continue
        inner = pattern.match(text, m.start(3)) if reading == "named" else None
        m = inner if inner else pattern.search(text, m.end())


class gate9:
    """Within it, mask._run reads as with gate 9's readings off ("all") or the four values it reads as no secret off
    ("four"): its rules to pass to _run, and the module's own patterns swapped."""

    def __init__(self, what):
        self.what = what

    def __enter__(self):
        swap = {"_ARN_HEAD": _NEVER, "_FILE_NAME": _NEVER, "_DATE_OR_TIME": _NEVER}
        if self.what == "all":
            swap["_GITLEAKS"] = []
            swap["_named"] = _main_named
            links = [re.compile(p.pattern.replace("]*:)", "]+:)").replace("&])*%(?:25)?3A)", "&])+%(?:25)?3A)"),
                                p.flags) for p in head.PREFIXED_SECRETS[1:3]]
            swap.update(PREFIXED_SECRETS=[head.PREFIXED_SECRETS[0], *links, *head.PREFIXED_SECRETS[3:]],
                        _COMMANDS=head._COMMANDS[:5], _ANY_COMMAND=main._ANY_COMMAND)
        self.saved = {name: getattr(head, name) for name in swap}
        for name, value in swap.items():
            setattr(head, name, value)
        return _ALL_OFF if self.what == "all" else _FOUR_OFF

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(head, name, value)


def new_readings(text):
    """Where gate 9's new readings and names read a secret in `text`, as positions: what this branch may hide past
    its reading with them off."""
    out = set()
    # Read as written and as the pass gives them the text, past the secrets known by their shape.
    shaped, origin = head._run(text, (head.SECRET_PATTERNS, (), "named"), list(range(len(text))))
    for pattern in _NEW_READINGS:
        for m in pattern.finditer(text):
            out.update(range(m.start(3), m.end(3)))
        for m in pattern.finditer(shaped):
            out.update(range(*head._back(origin, m.start(3), m.end(3), len(text))))
    for start, end in head._gitleaks_spans(text):
        out.update(range(start, end))
    for start, end in head._gitleaks_spans(shaped):
        out.update(range(*head._back(origin, start, end, len(text))))
    for m in head.ENCODED_PRIVATE_KEY.finditer(text):
        out.update(range(m.start(), m.end()))
    for pattern in head.PREFIXED_SECRETS[1:3]:
        for m in pattern.finditer(text):
            out.update(range(m.end(1), m.end()))
    for line_pattern, flag, _ in head._COMMANDS[5:]:
        for line in line_pattern.finditer(text):
            for m in flag.finditer(text, line.start(), line.end()):
                out.update(range(m.start(2), m.end(2)))
    for pattern, _ in head._TEXT_RULES[1]:
        if pattern is not head.AUTH_SECRET:
            for source in (text, shaped):
                for m in pattern.finditer(source):
                    if source is text and re.search(r"(?i)subscription[_-]?key", m.group(1)):
                        out.update(range(m.start(3), m.end(3)))
                    # A masked value that is a name and its separator, and that name's own value after it.
                    inner = pattern.match(source, m.start(3))
                    if inner and inner.start(3) >= m.end(3):
                        span = (inner.start(3), inner.end(3))
                        out.update(range(*span) if source is text else range(*head._back(origin, *span, len(text))))
    return out


def no_secret_values(text):
    """Where a value gate 9 reads as no secret sits in `text`: after a name in an AWS resource's name, a file's name of
    words after "/", a date or a time, or after a tokenizer's name. Positions."""
    out = set()
    for pattern, _ in _ALL_OFF[1]:
        if pattern is head.AUTH_SECRET:
            continue
        for m in pattern.finditer(text):
            name, value = m.group(1), m.group(3)
            if (head._ARN_HEAD.search(text, max(0, m.start(1) - 128), m.start(1))
                    or m.start(1) and text[m.start(1) - 1] == "/" and head._FILE_NAME.fullmatch(value)
                    or head._DATE_OR_TIME.fullmatch(value) or re.search(r"(?i)tokeni[sz]", name)):
                out.update(range(m.start(3), m.end(3)))
    return out


def as_written(text):
    """This branch's reading of the text as written alone: its passes with no view, until one changes nothing."""
    return tracked(text, head._run)[0]


def tracked(text, step, rules=None):
    """(passes of `step` over `text` until one changes nothing, the positions of `text` the result shows), each pass
    reporting where every character of its result came from (mask._edit)."""
    origin = list(range(len(text)))
    for _ in range(head.MAX_PASSES):
        masked, came = step(text, rules or head._TEXT_RULES, list(range(len(text))))
        if masked == text:
            break
        text, origin = masked, [origin[c] if c >= 0 else -1 for c in came]
    return text, {i for i in origin if i >= 0}


def _fit(text, pieces, brackets):
    """Which characters of `text` the pieces between placeholders show, each at its first place: 1 or 0 for each, or
    None when they do not fit. A piece after a placeholder that starts with "]" reads that "]" as the text's own when
    `brackets` and it fits so, else as what a pattern left of the placeholder."""
    shown = bytearray(len(text))
    if not text.startswith(pieces[0]):
        return None
    shown[:len(pieces[0])] = b"\x01" * len(pieces[0])
    at = len(pieces[0])
    for n, piece in enumerate(pieces[1:], 1):
        options = ([piece] if brackets else []) + [piece.lstrip("]")]
        for option in options:
            if n == len(pieces) - 1:
                i = len(text) - len(option)
                if i >= at and text.endswith(option):
                    break
            else:
                i = text.find(option, at)
                if i >= 0:
                    break
        else:
            return None
        shown[i:i + len(option)] = b"\x01" * len(option)
        at = i + len(option)
    return shown


def reading(text, masked):
    """(the characters of `text` that `masked` surely shows, those it surely hides), as sets of positions: shown or
    hidden however its pieces are placed, read from the left and from the right, and with a "]" right after a
    placeholder read as the text's own where it fits so or as what a pattern left of the placeholder."""
    if PLACEHOLDER not in masked:
        if masked != text:
            raise AssertionError(f"cannot read {masked!r} against {text!r}")
        return set(range(len(text))), set()
    # A placeholder the text held reads as one a pass made, in every output alike.
    pieces = masked.split(PLACEHOLDER)
    fits = []
    for brackets in (True, False):
        fits.append(_fit(text, pieces, brackets))
        right = _fit_right(text, pieces, brackets)
        fits.append(right)
    fits = [fit for fit in fits if fit is not None]
    if not fits:
        raise AssertionError(f"cannot read {masked!r} against {text!r}")
    return ({i for i in range(len(text)) if all(fit[i] for fit in fits)},
            {i for i in range(len(text)) if not any(fit[i] for fit in fits)})


def _fit_right(text, pieces, brackets):
    """_fit with each piece at its last place, read from the right."""
    shown = bytearray(len(text))
    last = pieces[-1]
    options = ([last] if brackets else []) + [last.lstrip("]")] if len(pieces) > 1 else [last]
    for option in options:
        if text.endswith(option):
            break
    else:
        return None
    end = len(text) - len(option)
    shown[end:] = b"\x01" * len(option)
    for piece in reversed(pieces[1:-1]):
        for option in ([piece] if brackets else []) + [piece.lstrip("]")]:
            i = text.rfind(option, len(pieces[0]), end)
            if i >= 0:
                break
        else:
            return None
        shown[i:i + len(option)] = b"\x01" * len(option)
        end = i
    if not text.startswith(pieces[0]) or len(pieces[0]) > end:
        return None
    shown[:len(pieces[0])] = b"\x01" * len(pieces[0])
    return shown


def authorization_tokens(text):
    """Where the words of each Authorization header's value sit, read at each "Authorization": what this branch may
    mask where main keeps it. Its credential-shaped first token with no scheme word is masked by design, and that
    changes where main's next search starts, so a later header's credential main passed over may be masked too."""
    out = set()
    for name in re.finditer(r"(?i)(?:proxy-)?authorization", text):
        m = head.AUTH_SECRET.match(text, name.start())
        if m:
            for group in (3, 5):
                if m.group(group):
                    out.update(range(m.start(group), m.end(group)))
    return out


def runs(positions):
    """Maximal runs of consecutive positions, as (start, end)."""
    out = []
    for i in sorted(positions):
        if out and out[-1][1] == i:
            out[-1][1] = i + 1
        else:
            out.append([i, i + 1])
    return [tuple(run) for run in out]


def by_an_invisible(text, start, end):
    """Whether an invisible character sits in text[start:end], right beside it, or on its line within 64 characters
    of it: in a split name before the value it masks, or after a scheme word in the token it starts."""
    line, stop = text.rfind("\n", 0, start) + 1, text.find("\n", end)
    stop = len(text) if stop < 0 else stop
    # A colour code is one, several characters long, and counts where any of it is near.
    near = max(line, start - 64, 0)
    return any(m.end() > near for m in INVISIBLE.finditer(text, line, min(end + 64, stop)))


class Generated:
    """Texts of secrets, words and invisible characters, some secrets split by one, glued to a word by one, or with
    one between every character."""
    ALNUM = string.ascii_letters + string.digits
    MARKS = ("​", "‌", "‍", "⁠", "­", "﻿", "\x00", "\x1b", "͏")

    def __init__(self, r):
        self.r = r

    def run(self, n):
        return "".join(self.r.choice(self.ALNUM) for _ in range(n)) + self.r.choice("0123456789")

    def secret(self):
        r, run = self.r, self.run
        return r.choice((
            lambda: "ghp_" + run(r.randint(24, 36)), lambda: "sk-proj-" + run(r.randint(20, 40)),
            lambda: "AKIA" + "".join(r.choice(string.ascii_uppercase + string.digits) for _ in range(16)),
            lambda: "xoxb-" + "1234567890-" * 2 + run(16), lambda: "glpat-" + run(20),
            lambda: "password=" + run(12), lambda: "api_key: " + run(14), lambda: "token is " + run(12),
            lambda: '{"password": "' + run(12) + '"}', lambda: "Authorization: Token " + run(14),
            lambda: "Authorization: " + run(r.randint(12, 30)), lambda: "Proxy-Authorization: Bearer " + run(20),
            lambda: "https://sam:" + run(12) + "@example.com", lambda: "curl -u sam:" + run(12) + " x",
            lambda: "Cookie: sid=" + run(12), lambda: "API_KEY=" + run(12), lambda: "--api-key " + run(12),
            lambda: "password=" + run(12) + "|ghp_" + run(30), lambda: "[masked]" + r.choice(("", "_", run(6))),
            lambda: "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADAN" + run(60) + "\n-----END PRIVATE KEY-----"))()

    def word(self):
        r = self.r
        return r.choice(("plan", "the", "ledger", "walrus", "budget", "plain_", "notes/", "x-", "co­operate",
                         "\U0001f468‍\U0001f469", "ISO-27001", "RFC7235-compliant", "Token", "plain_https"))

    def split(self, secret):
        r, mark = self.r, self.r.choice(self.MARKS)
        how = r.random()
        if how < 0.4:
            cut = r.randint(1, len(secret) - 1)
            return secret[:cut] + mark + secret[cut:]
        if how < 0.55:
            return mark.join(secret)
        if how < 0.75:
            return r.choice(("budget", "x", "plain_")) + mark + secret
        return secret + mark + r.choice(("tail", "plain_https://sam:" + self.run(10) + "@example.com", "Authorization: "
                                         "Token " + self.run(12), "_co" + mark + "operate/curl -u sam:" + self.run(9)))

    def text(self):
        r, parts = self.r, []
        for _ in range(r.randint(1, 8)):
            if r.random() < 0.55:
                secret = self.secret()
                parts.append(self.split(secret) if r.random() < 0.5 else secret)
            else:
                word = self.word()
                parts.append(word + r.choice(self.MARKS) + self.word() if r.random() < 0.3 else word)
        return "".join(part + r.choice((" ", " ", "\n", "|", "_", "/", ", ", "", r.choice(self.MARKS))) for part in parts)


class Coloured(Generated):
    """Generated's texts with a terminal's colour codes, raw and escaped, DEL and C1 controls for its invisible
    characters."""
    MARKS = ("\x1b[0m", "\x1b[01;31m\x1b[K", "\\u001b[32m", "\\x1b[1m", "\\033[0;35m", "\x9b31m", "\x7f", "\x9d")


# Texts the reviews of this branch found it masked wrong, each a shape the generator may not reach.
FOUND = ("sk-proj-" + "C3" * 12 + "\u200dplain_https://sam:walrus2secret9@example.com",
         "ghp_" + "A1" * 12 + "_co\u00adoperate/curl -u sam:walrus2secret9 x",
         "ghp_" + "A1" * 12 + "_co\u00adoperate/Proxy-Authorization: Token walrus2secret9",
         "password=walrus2secret9 co\u00adoperate password=walrus2secret9|ghp_" + "B2" * 12,
         "ghp_" + "A1" * 12 + "\u200bAuthorization: Token walrus2secret9",
         "Authorization: Token [masked]|Authorization: Token walrus2secret9",
         "Authorization: ISO-27001\fcompliant",
         "ghp_" + "A1" * 12 + " family \U0001f468\u200d\U0001f469 ghp_" + "B2" * 12 + "[masked]",
         "ghp_" + "A1" * 12 + " family \U0001f468\u200d\U0001f469 Authorization: Token walrus2secret9|ghp_" + "B2" * 12,
         "-----BEGIN PRIVATE KEY----- foo -----END PRIVATE KEY----- foo -----BEGIN PRIVATE KEY----- foo -----END "
         "PRIVATE KEY----- ghp_" + "A1" * 6 + "\u200b" + "B2" * 6)


def corpus(cases=CASES):
    """(where it came from, the text) for every text the properties are checked on."""
    for n, text in enumerate(FOUND):
        yield f"found {n}", text
    for n in range(cases * 2):
        yield f"generated {n}", Generated(random.Random(f"diff:{n}")).text()
    for n in range(cases):
        yield f"coloured {n}", Coloured(random.Random(f"diff-colour:{n}")).text()
    for seq in range(cases):
        planted = Planted(random.Random(f"diff-fuzz:{seq}"), seq)
        yield f"fuzz {seq} text", planted.text()
        yield f"fuzz {seq} title", planted.title()
    for n in range(cases):
        r = random.Random(f"diff-old:{n}")
        text = Case(r, n).text
        size = r.randint(40, 400)
        yield f"old row {n}", text
        yield f"old row {n}, an older kb's mask", legacy_mask.mask(text)
        yield f"old row {n}, main's token mask", legacy_store.token_mask(text)
        yield f"old row {n}, masked a chunk at a time", "".join(legacy_mask.mask(text[i:i + size])
                                                                 for i in range(0, len(text), size))


class AgainstMainTest(unittest.TestCase):
    def check(self, where, text):
        ours, ours_shown = tracked(text, head._once)
        written, written_shown = tracked(text, head._run)
        with gate9("all") as rules:
            base, base_shown = tracked(text, head._run, rules)
        with gate9("four") as rules:
            added, added_shown = tracked(text, head._run, rules)
        theirs = main.mask(text)
        problems = []
        # 1. The text as written reads as main reads it, with gate 9's readings off, but for the Authorization rule;
        # with no invisible character, the reading as written is the mask.
        if not INVISIBLE.search(text) and ours != written:
            problems.append(f"with no invisible character, mask() is not the reading as written: {ours!r}")
        if not re.search(r"(?i)authorization", text):
            if base != theirs:
                problems.append(f"read as written, gate 9 off, {base!r} is not main's {theirs!r}")
        else:  # where they may differ, nothing main surely hides shows, and only the Authorization rule adds, each
            # output read against the text alike (reading)
            main_shown, main_hidden = reading(text, theirs)
            read_shown, read_hidden = reading(text, base)
            shown_there = sorted(i for i in main_hidden & read_shown if not INVISIBLE.match(text, i))
            if shown_there:
                problems.append(f"read as written, shows what main hides at {runs(shown_there)[:3]}: {written!r}")
            extra = sorted(read_hidden & main_shown - authorization_tokens(text))
            if extra:
                problems.append(f"read as written, hides what main shows at {runs(extra)[:3]}: {written!r} vs {theirs!r}")
        # 4. Gate 9's readings only add, where they read; the four values it reads as no secret are all it shows.
        lost = sorted(added_shown - base_shown)
        if lost:
            problems.append(f"gate 9's readings show what they did not at {runs(lost)[:3]}: {added!r} vs {base!r}")
        reads = new_readings(text)
        far = [run for run in runs(base_shown - added_shown) if not reads.intersection(range(*run))]
        if far:
            problems.append(f"gate 9's readings hide away from where they read at {far[:3]}: {added!r} vs {base!r}")
        shown = sorted(written_shown - added_shown - no_secret_values(text))
        if shown:
            problems.append(f"shows past the four values at {runs(shown)[:3]}: {written!r} vs {added!r}")
        # 2. Nothing the reading as written hides shows, but an invisible character, which no secret is made of.
        leaked = sorted(i for i in ours_shown - written_shown if not INVISIBLE.match(text, i))
        if leaked:
            problems.append(f"shows what main's reading hides at {runs(leaked)[:3]}: {ours!r} vs {written!r}")
        # 3. What the view adds sits by an invisible character.
        far = [run for run in runs(written_shown - ours_shown) if not by_an_invisible(text, *run)]
        if far:
            problems.append(f"hides away from any invisible character at {far[:3]}: {ours!r} vs {written!r}")
        return [f"{where}: {problem} (text {text!r})" for problem in problems]

    def test_this_branch_masks_all_main_masks_and_adds_only_by_invisible_characters(self):
        problems, checked = [], 0
        for where, text in corpus():
            for variant, t in ((where, text), (f"{where}, its invisible characters removed", INVISIBLE.sub("", text))):
                if variant != where and t == text:
                    continue
                problems += self.check(variant, t)
                checked += 1
        self.assertGreater(checked, CASES * 8)
        self.assertEqual(problems[:5], [], f"{len(problems)} of {checked} texts break a property")

    def test_an_id_with_no_invisible_character_counts_as_on_main(self):
        for where, text in corpus(CASES // 3):
            text = INVISIBLE.sub("", text)[:200]
            with self.subTest(where=where):
                self.assertEqual(head.secret_in_id(text), main.secret_in_id(text), repr(text))


if __name__ == "__main__":
    unittest.main()
