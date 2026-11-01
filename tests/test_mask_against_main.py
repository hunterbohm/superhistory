"""This branch's mask held to main's, frozen in tests/fixtures/main_mask.py (main at 3be191e).

A pass reads the text as written as main does, after masking in place only the secrets the text read without its
invisible characters holds (mask._split_secrets). So:
1. A text with no invisible character masks as on main, byte for byte, but for the credential-shaped token after a
   header-style Authorization with no scheme word, which this branch masks by design. The reading of the text as
   written differs from main's by that token alone, for any text.
2. Every character main hides, this branch hides, but for an invisible character, which no secret is made of.
3. Every character this branch hides past its reading of the text as written sits by an invisible character the text
   holds: in the run of characters it is in, or in a value whose name, on the same line just before it, holds one.

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


def as_written(text):
    """This branch's reading of the text as written alone: its passes with no view, until one changes nothing."""
    for _ in range(head.MAX_PASSES):
        masked = head._pass(text, head._TEXT_RULES)
        if masked == text:
            return text
        text = masked
    return text


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
    return bool(INVISIBLE.search(text, max(line, start - 64, 0), min(end + 64, stop)))


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


def corpus(cases=CASES):
    """(where it came from, the text) for every text the properties are checked on."""
    for n in range(cases * 2):
        yield f"generated {n}", Generated(random.Random(f"diff:{n}")).text()
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
        ours, theirs, written = head.mask(text), main.mask(text), as_written(text)
        shown, hidden = reading(text, ours)
        main_shown, main_hidden = reading(text, theirs)
        written_shown, _ = reading(text, written)
        allowed = authorization_tokens(text)
        problems = []
        # 1. The text as written reads as main reads it, but for the Authorization token; with no invisible
        # character, that reading is the mask.
        if not INVISIBLE.search(text):
            if ours != written:
                problems.append(f"with no invisible character, mask() is not the reading as written: {ours!r}")
            if not allowed and ours != theirs:
                problems.append(f"with no invisible character, {ours!r} is not main's {theirs!r}")
        _, written_hidden = reading(text, written)
        extra = sorted((written_hidden & main_shown) - allowed)
        if extra:
            problems.append(f"read as written, hides what main shows at {runs(extra)[:3]}: {written!r} vs {theirs!r}")
        # 2. Nothing main hides shows, but for an invisible character, which no secret is made of.
        leaked = sorted(i for i in main_hidden & shown if not INVISIBLE.match(text, i))
        if leaked:
            problems.append(f"shows what main hides at {runs(leaked)[:3]}: {ours!r} vs main's {theirs!r}")
        # 3. What the view adds sits by an invisible character.
        far = [run for run in runs(hidden & written_shown) if not by_an_invisible(text, *run)]
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
