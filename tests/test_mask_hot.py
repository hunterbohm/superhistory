"""The readers a long text spends its time in, held to the mask from before: five shortcuts that skip work whose result
is known, and the tests that hold each to main's result.

On long made-up texts (code, listings, JSON, wrapped base64, key files in each of them) most of mask()'s time went in
three readers of private keys written as base64 and of secrets split by an invisible character (kb.mask._encoded_key_spans,
_marked_key_spans and _split_secrets). Each shortcut reads less where what it skips cannot change the result:
1. _chained_key_lines does not decode a run of base64 that no key's line can be (_takes_no_key_line): after a line none
   went on from, a run is read only if it is 16 characters or more and decodes to ASCII, or holds a key file's header
   (_ENCODED_BEGIN). A run that starts no key file's header, and whose first three groups of four characters do not
   decode to ASCII (_ASCII_START), or that is shorter than 16, is not read; its first 12 characters are all it asks.
2. _marked_key_spans reads the lines of a run for a key's word (_ENCODED_KEY) only where the run's text spells one,
   each of its characters found after the one before (_spells_key): the base64 each reading joins is stretches of that
   text in order, so a word in it is in the text so. A run whose text spells none has no key's line (_chained_key_lines),
   reads no key from its joined lines and cuts no column (_column_cuts).
3. _marked_key_spans reads a run for where a key file starts (_file_starts) only where it holds a key file's header
   in base64 (_may_start_key_file): a key file starts only on a line that holds one.
4. _key_spans reads a block only where its text holds a key's word in base64 (_holds_key_word): each key starts at one.
5. _gitleaks_read runs a rule's pattern only where the text holds what a match of it needs (_may_find, as the name patterns).

tests/fixtures/main_mask_d512b6d.py holds the readers as main has them (the functions here are the same code), so
each reader here is held to that module's result on the texts below, as spans and as what it reads through, and the
whole mask to that module's output and origin, character for character. Each shortcut is also held to what it asks, by
property tests (against the reading it stands in for), and a break planted in each (a gate that skips what it must
not) makes one of those tests fail."""
import base64
import contextlib
import json
import random
import textwrap
import unittest
from unittest import mock

from kb import gitleaks, mask as head
from tests import samples
from tests.test_mask_speed import before, code, fresh_memo, in_pieces, took

B64 = head._BASE64_DIGITS
WORDS = ["notes", "harbor", "ledger", "deploy", "walrus", "today", "plan", "moved", "private", "secret", "key", "vault"]


def lines_of(text):
    """The (start, end) of each line of `text`, as _marked_key_spans reads the lines of a run."""
    out, at = [], 0
    for line in text.splitlines(keepends=True):
        out.append((at, at + len(line.rstrip("\r\n"))))
        at += len(line)
    return out


def readers(module, text):
    """What `module`'s readers of keys give on `text` as a whole run of lines: each key written as base64 and the keys
    read through their footers, those among marks and other text (the lines it reads and what it reads through), the
    base64 each line of the run goes on with, and the spans _encoded_key_spans gives."""
    through, again = [], []
    read = module._written_key_spans(text, through)
    marked = module._marked_key_spans(text, read, again)
    return read, through, marked, again, module._chained_key_lines(text, lines_of(text)), module._encoded_key_spans(text)


def pem(word, body=300):
    r = random.Random(word)
    data = base64.b64encode(r.randbytes(body)).decode()
    return f"-----BEGIN {word}-----\n" + "\n".join(textwrap.wrap(data, 64)) + f"\n-----END {word}-----\n"


# What stands before a key file's header in the bytes the lines decode to: nothing, one or two bytes (as the header's
# word then falls at another place in a group of four characters), a byte order mark, and a preface of words (as a
# file from `openssl pkcs12` has), after which no line is a key file's start but its lines read from the header on.
LEADS = {"none": b"", "one": b" ", "two": b"  ", "bom": b"\xef\xbb\xbf",
         "preface": b"Bag Attributes\n    friendlyName: ledger\n", "preface one": b"Bag Attributes\n    friendlyName: ledger \n",
         "preface two": b"Bag Attributes\n    friendlyName: ledger  \n"}
# What each line of a key's base64 is written as: bare, after a mark, with a comment or a label, in a table, with a count.
FORMS = {
    "bare": lambda n, line: line, "mark >": lambda n, line: "> " + line, "mark #": lambda n, line: "# " + line,
    "mark //": lambda n, line: "//" + line, "quote": lambda n, line: '"' + line, "comment": lambda n, line: f"    {line}  // {n}",
    "numbered": lambda n, line: f"{n:6}\t{line}  # part", "slashes": lambda n, line: ("///" if n % 3 == 0 else "//") + line,
    "plus": lambda n, line: "+" + line, "table": lambda n, line: f"| {line} |", "label": lambda n, line: f"line {n}: {line}",
}
# Two columns on each line, the key's in one and words' base64 (or a second key's) in the other.
COLUMNS = {
    "other tab key": lambda line, other: f"{other}\t+{line}", "key bar other": lambda line, other: f"//{line}  |  {other}",
    "other slashes key": lambda line, other: f"{other} ///{line}", "key other": lambda line, other: f"{line} {other}",
}


def key_texts(words=("RSA PRIVATE KEY", "SECRET KEY"), widths=(30, 64)):
    """(label, text) of each made-up key file written as base64 in lines of each width, among words, in each form."""
    other = base64.b64encode(("Deploy notes for the ledger service, rotate after the review. " * 30).encode()).decode()
    for word in words:
        for lead, bytes_before in LEADS.items():
            encoded = base64.b64encode(bytes_before + pem(word).encode()).decode()
            for width in widths:
                chunks, wide = textwrap.wrap(encoded, width), textwrap.wrap(other, width)
                for form, write in FORMS.items():
                    yield (word, lead, width, form), "Notes:\n" + "\n".join(
                        write(n, line) for n, line in enumerate(chunks)) + "\n\nnext paragraph"
                for form, write in COLUMNS.items():
                    yield (word, lead, width, form), "Notes:\n" + "\n".join(
                        write(line, wide[n % len(wide)]) for n, line in enumerate(chunks)) + "\n\nnext paragraph"


def a_run(r, kind):
    """One run of base64 of a kind a line of a key's, or the start of one, may or may not be."""
    if kind == "short":
        return "".join(r.choice(B64) for _ in range(r.randint(1, 20)))
    if kind == "random":
        return "".join(r.choice(B64) for _ in range(r.randint(8, 80)))
    if kind == "ascii":  # base64 of words, which decodes to ASCII
        data = " ".join(r.choice(WORDS) for _ in range(r.randint(2, 20))).encode()
        return base64.b64encode(data).decode()[:r.randint(12, 90)]
    if kind == "header":  # a key file's start, after nothing or the bytes that come before one
        lead = r.choice([b"", b"", b"\xef\xbb\xbf", b"\n", b"\xff", b"Bag Attributes\n", b"\x00\x01", b"x"])
        data = lead + ("-----BEGIN RSA PRIVATE KEY-----\n" + "".join(r.choice(B64) for _ in range(60)) + "\n").encode()
        return base64.b64encode(data).decode()[:r.randint(8, 90)]
    if kind == "prefix":  # a prefix of a key file's start, whole or with other characters after it
        start = r.choice(head._FILE_STARTS)[:r.randint(1, 13)]
        return start + "".join(r.choice(B64) for _ in range(r.randint(0, 30))) if r.random() < 0.5 else start
    if kind == "begin":  # a header's base64 whole at some alignment, with a few characters around it, or alone
        return ("".join(r.choice(B64) for _ in range(r.choice([0, 0, 1, 2, 4, 6]))) + r.choice(head._ENCODED_BEGIN_CODES)
                + "".join(r.choice(B64) for _ in range(r.choice([0, 0, 1, 2, 4]))))
    if kind == "padded":
        return "".join(r.choice(B64) for _ in range(r.randint(4, 40) // 4 * 4 + r.choice([2, 3]))) + "=" * r.choice([1, 2])
    return r.choice(["/", "//", "+", "///", "+/+"]) + a_run(r, r.choice(["ascii", "random", "header"]))  # signs first


RUNS = ["short", "random", "ascii", "header", "prefix", "padded", "signs", "begin"]


def chain_text(r):
    """Lines of made-up base64 runs of each kind, with marks and words around, two to a line, wrapped stretches of a key
    file's base64, and words."""
    width = r.choice([16, 20, 24, 32, 48, 64, 76])
    stream = base64.b64encode(("-----BEGIN RSA PRIVATE KEY-----\n" + "".join(r.choice(B64) for _ in range(r.randint(100, 400)))
                               + "\n-----END RSA PRIVATE KEY-----\n").encode()).decode()
    lines = []
    for _ in range(r.randint(2, 14)):
        k = r.random()
        if k < 0.45:
            lines.append(a_run(r, r.choice(RUNS)))
        elif k < 0.6:
            lines.append(r.choice(["> ", "# ", "// ", ""]) + a_run(r, r.choice(RUNS)) + r.choice(["", " // 3", " # part"]))
        elif k < 0.7:
            lines.append(a_run(r, r.choice(RUNS)) + " " + a_run(r, r.choice(RUNS)))
        elif k < 0.9:
            at = r.randrange(0, max(1, len(stream) - 2 * width))
            lines.append(stream[at:at + width + r.choice([0, 0, -3, 5])])
        else:
            lines.append(" ".join(r.choice(WORDS) for _ in range(r.randint(1, 6))))
    return "\n".join(lines) + "\n"


def chain_texts(count, seed="hot chain"):
    r = random.Random(seed)
    return [chain_text(r) for _ in range(count)]


class ReadersAreMainsTest(unittest.TestCase):
    def test_each_reader_gives_what_it_gave_on_a_key_in_each_of_its_forms(self):
        checked = 0
        for label, text in key_texts():
            checked += 1
            self.assertEqual(readers(head, text), readers(before, text), label)
        self.assertGreater(checked, 400)

    def test_the_lines_a_key_goes_on_with_are_those_it_went_on_with_on_runs_of_each_kind(self):
        r = random.Random("hot slices")
        for text in chain_texts(1500):
            lines = lines_of(text)
            self.assertEqual(head._chained_key_lines(text, lines), before._chained_key_lines(text, lines), text)
            first = r.randrange(len(lines))
            part = lines[first:first + r.randint(1, 6)]  # a run starts and ends anywhere in the lines
            self.assertEqual(head._chained_key_lines(text, part), before._chained_key_lines(text, part), text)
            self.assertEqual(readers(head, text), readers(before, text), text)

    def test_every_mask_and_origin_is_main_s_on_each_form_of_a_key(self):
        for n, (label, text) in enumerate(key_texts(widths=(48,))):
            if n % 4 == 0:
                with self.subTest(label):
                    self.assertEqual(head.mask(text), before.mask(text))
                    self.assertEqual(head.mask_with_origin(text), before.mask_with_origin(text))

    def test_every_mask_and_origin_is_main_s_on_long_texts_of_each_shape_whole_and_in_pieces(self):
        for name in SHAPES:
            text = shape(name, 24_000)
            for label, reading in (("whole", contextlib.nullcontext), ("pieces", lambda: in_pieces(8_000, 3_000, 1_000))):
                with self.subTest(f"{name} {label}"), reading():
                    fresh_memo()
                    self.assertEqual(head.mask(text), before.mask(text))
                    self.assertEqual(head.mask_with_origin(text), before.mask_with_origin(text))

    def test_the_secrets_of_gitleaks_rules_are_masked_as_on_main(self):
        # _gitleaks_read now runs a rule's pattern only where the text can hold a match of it (_may_find).
        checked = 0
        for i, (pattern, group, entropy, keywords, allowed, stopwords) in enumerate(head._GITLEAKS):
            for example in samples.samples(pattern, 3, gitleaks.RULES[i][0]):
                m = pattern.fullmatch(example)
                span = m and head._gitleaks_secret(m, group, entropy, allowed, stopwords)
                if not span:
                    continue
                secret = example[span[0]:span[1]]
                word = "" if not keywords or any(k in secret.lower() for k in keywords) else keywords[0] + " "
                for text in (word + secret, f"token: {word}{secret}\n", json.dumps({"value": word + secret}),
                             f"use {word}{secret} for the deploy " + code(300)):
                    checked += 1
                    self.assertEqual(head.mask(text), before.mask(text), text)
                    self.assertEqual(head.mask_with_origin(text), before.mask_with_origin(text), text)
        self.assertGreater(checked, 400)


def listing(r, size):
    out, n = [], 0
    while n < size:
        line = f"src/{r.choice(WORDS)}/{r.choice(WORDS)}_{r.randint(0, 999)}.{r.choice(['py', 'ts', 'md', 'json'])}"
        out.append(line)
        n += len(line) + 1
    return "\n".join(out) + "\n"


def json_pretty(r, size):
    out, n = [], 0
    while n < size:
        rec = json.dumps({"id": r.randint(1, 10 ** 6), "name": r.choice(WORDS), "ok": r.random() < 0.5,
                          "tags": [r.choice(WORDS) for _ in range(r.randint(1, 4))]}, indent=2)
        out.append(rec)
        n += len(rec) + 1
    return "\n".join(out) + "\n"


def wrapped(r, size, width=76):
    raw = base64.b64encode(r.randbytes(max(3, size * 3 // 4))).decode()
    return "\n".join(raw[i:i + width] for i in range(0, len(raw), width)) + "\n"


def key_file_in_base64(r, size=0):
    """A made-up key file written as base64 in lines of 64."""
    body = base64.b64encode(r.randbytes(1200)).decode()
    file = ("-----BEGIN OPENSSH PRIVATE KEY-----\n" + "\n".join(body[i:i + 70] for i in range(0, len(body), 70))
            + "\n-----END OPENSSH PRIVATE KEY-----\n").encode()
    data = base64.b64encode(file).decode()
    return "\n".join(data[i:i + 64] for i in range(0, len(data), 64)) + "\n"


def marked_key_file(r, size=0):
    """A made-up key file quoted or commented, a mark before each line."""
    body = base64.b64encode(r.randbytes(700)).decode()
    mark = r.choice(["> ", "# ", "// ", "| "])
    lines = ["-----BEGIN RSA PRIVATE KEY-----"] + [body[i:i + 64] for i in range(0, len(body), 64)] + [
        "-----END RSA PRIVATE KEY-----"]
    return "".join(f"{mark}{line}\n" for line in lines)


def blocks(r, size, makers):
    out, n = [], 0
    while n < size:
        block = r.choice(makers)(r, 2000)
        out.append(block)
        n += len(block)
    return "".join(out)[:size]


def painted(text, r):
    """`text` with a colour code around a seventh of its lines."""
    colours = ["\x1b[31m", "\x1b[32m", "\x1b[0m", "\x1b[1;34m"]
    return "\n".join(r.choice(colours) + line + "\x1b[0m" if r.random() < 1 / 7 else line for line in text.split("\n"))


SHAPES = ("code", "listing", "json", "wrapped", "key files", "marked key files", "painted", "mixed keys", "painted keys")


def shape(name, size):
    """About `size` characters of a made-up text of a shape that drives the three readers; no secret but the key files'."""
    r = random.Random(f"hot shape {name} {size}")
    plain = [lambda r, n: code(n, f"hot {r.random()}"), listing, json_pretty]
    keys = [key_file_in_base64, marked_key_file]
    return {
        "code": lambda: code(size, "hot code"), "listing": lambda: listing(r, size), "json": lambda: json_pretty(r, size),
        "wrapped": lambda: blocks(r, size, plain + [wrapped]),
        "key files": lambda: blocks(r, size, plain + [key_file_in_base64]),
        "marked key files": lambda: blocks(r, size, plain + [marked_key_file]),
        "painted": lambda: painted(blocks(r, size, plain + [listing]), r),
        "mixed keys": lambda: blocks(r, size, plain + [wrapped] + keys),
        "painted keys": lambda: painted(blocks(r, size, plain + [wrapped] + keys), r),
    }[name]()


def is_subsequence(word, text):
    it = iter(text)
    return all(character in it for character in word)


class GatesAskWhatTheyStandInForTest(unittest.TestCase):
    def test_the_ascii_start_is_the_first_three_groups_of_four_decoding_to_ascii(self):
        # Each group c1 c2 c3 c4 writes three bytes, each under 0x80 where c1 is under 32, c2's third bit is 0 and
        # c3's two low bits are under 2, whatever c4 is. Every group of the three, in each place (the others "A").
        def ascii_group(group):
            return base64.b64decode(group, validate=True).isascii()
        r = random.Random("ascii start")
        for c1 in B64:
            for c2 in B64:
                for c3 in B64:
                    for c4 in "A/":
                        group = c1 + c2 + c3 + c4
                        self.assertEqual(bool(head._ASCII_START.match(group + "AAAAAAAA")), ascii_group(group), group)
        for place in (1, 2):
            for _ in range(20000):
                group = "".join(r.choice(B64) for _ in range(4))
                text = "AAAA" * (place - 1) + group + "AAAA" * (3 - place)
                self.assertEqual(bool(head._ASCII_START.match(text)), ascii_group(group), text)
                self.assertEqual(bool(head._ASCII_START.match(text + "x")), ascii_group(group), text)
        self.assertIsNone(head._ASCII_START.match("AAAAAAAAAAA"), "11 characters are not three groups")

    def test_a_run_a_file_s_start_begins_is_what_asks_for_it(self):
        starts = head._FILE_STARTS
        r = random.Random("file starts")
        pieces = [start[:n] + tail for start in starts for n in range(len(start) + 1) for tail in ("", "A", "AAAA", "x/+")]
        pieces += ["".join(r.choice(B64) for _ in range(r.randint(0, 20))) for _ in range(2000)]
        pieces += [start[:n] + r.choice(B64) + start[n + 1:] for start in starts for n in range(len(start)) for _ in range(20)]
        for piece in pieces:
            self.assertEqual(piece[:13] in head._FILE_START_PREFIXES, any(start.startswith(piece[:13]) for start in starts),
                             piece)

    def test_the_shortest_header_is_the_shortest_one_found(self):
        self.assertEqual(head._ENCODED_BEGIN_LEAST, min(len(code) for code in head._ENCODED_BEGIN_CODES))
        for code in head._ENCODED_BEGIN_CODES:
            self.assertIsNotNone(head._ENCODED_BEGIN.search(code))
            self.assertIsNone(head._ENCODED_BEGIN.search(code[:-1]))

    def test_a_run_the_gate_takes_for_no_key_s_line_is_read_as_none_by_the_reader_it_stands_in_for(self):
        # A run of base64 alone on a line, with no sign before it (so read whole, as it stands), is a key's line to
        # the reader from before only if the gate lets it through: where the gate takes it, the reader from before
        # goes on from no line of it, and the gate asks nothing of a run that starts a key file's header.
        r = random.Random("gate pieces")
        pieces, taken = [], 0
        for text in chain_texts(3000, "gate pieces"):
            pieces += [m.group() for line in text.split("\n") for m in head._MARKED_BASE64.finditer(line)
                       if not m.group().startswith(("+", "/"))]
        for piece in pieces:
            header = piece[:13] in head._FILE_START_PREFIXES
            if head._takes_no_key_line(piece) and not header:
                taken += 1
                self.assertEqual(before._chained_key_lines(piece, [(0, len(piece))]), [None], piece)
                self.assertEqual(head._chained_key_lines(piece, [(0, len(piece))]), [None], piece)
        self.assertGreater(taken, 1000, "the gate takes a run in two")
        self.assertGreater(len(pieces), 5000)

    def test_a_gate_takes_a_run_of_no_header_that_is_short_and_one_that_starts_beyond_ascii(self):
        self.assertTrue(head._takes_no_key_line("QmFnIEF0dHJpYnV"))  # words, 15 characters
        self.assertFalse(head._takes_no_key_line("QmFnIEF0dHJpYnVA"))  # words, 16
        self.assertTrue(head._takes_no_key_line("77u/" * 6))  # a byte order mark, then ascii: not ASCII at the start
        self.assertTrue(head._takes_no_key_line("//8A" + "QmFn" * 6))  # bytes past ASCII
        for code in head._ENCODED_BEGIN_CODES:  # a header's base64, alone or among others, is read
            for around in ("", "AAAA", "77u/", "//8A"):
                self.assertFalse(head._takes_no_key_line(around + code), around + code)
                self.assertFalse(head._takes_no_key_line(around + code + around), around + code)

    def test_a_key_word_spelled_across_a_run_s_text_is_what__spells_key_finds(self):
        r = random.Random("spells key")
        noise = "ab xyz\n/+01AZ "
        for _ in range(4000):
            code = r.choice(head._ENCODED_KEY_CODES)
            if r.random() < 0.5:  # spelled in order, with noise between, or with one character left out or turned
                spelled = list(code)
                if r.random() < 0.5:
                    del spelled[r.randrange(len(spelled))]
                elif r.random() < 0.5:
                    i = r.randrange(len(spelled) - 1)
                    spelled[i], spelled[i + 1] = spelled[i + 1], spelled[i]
                text = "".join(c + "".join(r.choice(noise) for _ in range(r.choice([0, 0, 1, 3]))) for c in spelled)
            else:
                text = "".join(r.choice(noise + code) for _ in range(r.randint(0, 60)))
            low = r.randint(0, len(text))
            high = r.randint(low, len(text))
            padded = "Q" * 3 + text + "Q" * 3
            self.assertEqual(head._spells_key(padded, low + 3, high + 3), any(is_subsequence(c, text[low:high])
                                                                                for c in head._ENCODED_KEY_CODES),
                             (text, low, high))
        for code in head._ENCODED_KEY_CODES:  # at each edge, whole or a character short or past it
            n = len(code)
            self.assertTrue(head._spells_key(code, 0, n))
            self.assertTrue(head._spells_key("zz" + code + "zz", 2, 2 + n))
            self.assertFalse(head._spells_key("zz" + code + "zz", 3, 2 + n))
            self.assertFalse(head._spells_key("zz" + code + "zz", 2, 1 + n))
            self.assertFalse(head._spells_key(code[::-1], 0, n) and code != code[::-1])

    def test_a_run_whose_lines_read_a_key_or_a_column_spells_the_key_s_word(self):
        # The sound claim of _spells_key: the base64 _marked_key_spans joins from a run (the lines' runs picked, each
        # column's) is stretches of the run's text in order, so a word in it is spelled by the run's text.
        spelled = silent = 0
        for text in [t for _, t in key_texts()] + chain_texts(1500, "hot spelled"):
            lines = lines_of(text)
            if not lines:
                continue
            low, high = lines[0][0], lines[-1][1]
            picks = [pick for pick in before._chained_key_lines(text, lines) if pick]
            joined = "".join(text[start:stop] for start, stop in picks)
            read = before._ENCODED_KEY.search(joined) is not None or bool(before._column_cuts(text, lines))
            if read:
                spelled += 1
                self.assertTrue(head._spells_key(text, low, high), text)
            else:
                silent += 1
        self.assertGreater(spelled, 300)
        self.assertGreater(silent, 300)

    def test_a_run_with_no_header_in_base64_starts_no_key_file_and_a_block_with_no_key_word_has_no_key(self):
        starts = keyless = checked = 0
        for text in [t for _, t in key_texts()] + chain_texts(1500, "hot header"):
            lines = lines_of(text)
            if not lines:
                continue
            checked += 1
            low, high = lines[0][0], lines[-1][1]
            if not head._may_start_key_file(text, low, high):
                starts += 1
                self.assertEqual(before._file_starts(text, lines, set()), [], text)
            if not head._holds_key_word("".join(text[a:b] for a, b in lines)):
                keyless += 1
                self.assertEqual(before._key_spans(text, lines, []), [], text)
        self.assertGreater(starts, 100)
        self.assertGreater(keyless, 100)
        self.assertGreater(checked, 1500)


def calls(module, text, names):
    """How many times mask(text) in `module` calls each of the functions `names` of it."""
    counts = dict.fromkeys(names, 0)

    def counting(name, real):
        def count(*args, **kwargs):
            counts[name] += 1
            return real(*args, **kwargs)
        return count
    with mock.patch.multiple(module, **{name: counting(name, getattr(module, name)) for name in names}):
        fresh_memo(module)
        module.mask(text)
    return counts


class ShortcutsReadLessTest(unittest.TestCase):
    NAMES = ("_reads_on", "_chained_key_lines", "_column_cuts", "_file_starts")

    def test_a_listing_of_code_is_decoded_for_no_key_s_line_in_runs_of_a_few_lines(self):
        # Groups of four lines between blank ones: each a run of lines of words (all base64's characters) that spells no
        # key's word, so none of its readings is made, where the mask from before made them all.
        r = random.Random("short runs")
        text = "\n\n".join("\n".join(f"{r.choice(WORDS)}_{r.randint(0, 99)} = {r.choice(WORDS)}({r.choice(WORDS)})"
                                      for _ in range(4)) for _ in range(300))
        now, was = calls(head, text, self.NAMES), calls(before, text, self.NAMES)
        for name in self.NAMES:
            self.assertGreater(was[name], 100, name)
            self.assertEqual(now[name], 0, name)

    def test_code_with_longer_runs_decodes_a_fifth_of_what_it_did(self):
        text = code(60_000, "hot decode")
        now, was = calls(head, text, ("_reads_on",)), calls(before, text, ("_reads_on",))
        self.assertGreater(was["_reads_on"], 2000)
        self.assertLess(now["_reads_on"], was["_reads_on"] / 5)


class CostTest(unittest.TestCase):
    """The mask and the one from before, on the same text in this process, so a slow machine slows both."""

    def test_a_text_of_each_shape_costs_no_more_than_it_did(self):
        for name in ("code", "listing", "wrapped", "mixed keys", "painted keys"):
            with self.subTest(name):
                text = shape(name, 60_000)
                self.assertLessEqual(took(text), 1.2 * took(text, before) + 0.05)

    def test_a_text_four_times_as_long_costs_at_most_eight_times_as_much(self):
        for name in ("code", "mixed keys"):
            with self.subTest(name):
                text, long = shape(name, 20_000), shape(name, 80_000)
                self.assertLess(took(long), 8 * took(text) + 0.3)


class PlantedBreakTest(unittest.TestCase):
    """A shortcut that skips what it must not, planted where it is made, makes a test above fail: the readers give
    another result than the ones from before on some text, so the comparisons catch it. Each case also gives the result
    from before with no break planted."""

    @classmethod
    def setUpClass(cls):
        cls.keys = [text for _, text in key_texts()]
        cls.chains = chain_texts(300, "hot chain")
        cls.want_keys = {}

    def differs(self, texts):
        """Whether `head`'s readers give a result other than the one from before on some text of `texts`."""
        for text in texts:
            if readers(head, text) != readers(before, text):
                return True
        return False

    def planted(self, texts, attribute, value):
        self.assertFalse(self.differs(texts[:40]), f"{attribute} unplanted")
        with mock.patch.object(head, attribute, value):
            self.assertTrue(self.differs(texts), f"{attribute} planted")

    def test_a_gate_that_takes_each_run_for_no_key_line_is_found(self):
        self.planted(self.chains, "_takes_no_key_line", lambda piece: True)

    def test_a_gate_that_leaves_out_a_header_s_base64_is_found(self):
        def no_header(piece):
            return len(piece) < 16 or not head._ASCII_START.match(piece)
        self.planted(self.chains, "_takes_no_key_line", no_header)
        self.planted(self.chains, "_ENCODED_BEGIN_LEAST", 99)
        self.planted(self.chains, "_ENCODED_BEGIN_LEAST", head._ENCODED_BEGIN_LEAST + 1)

    def test_a_gate_that_asks_for_more_characters_is_found(self):
        for least in (17, 24):
            def asks(piece, least=least):
                if len(piece) >= head._ENCODED_BEGIN_LEAST and head._ENCODED_BEGIN.search(piece):
                    return False
                return len(piece) < least or not head._ASCII_START.match(piece)
            with self.subTest(least):
                self.planted(self.chains, "_takes_no_key_line", asks)

    def test_a_start_that_asks_for_more_of_the_first_groups_is_found(self):
        import re
        for group, keep in ((0, lambda v: v < 16), (1, lambda v: not v & 12), (2, lambda v: v & 3 < 1)):
            keeps = [lambda v: v < 32, lambda v: not v & 8, lambda v: v & 3 < 2, lambda v: True]
            keeps[group] = keep
            tighter = re.compile("".join("[" + "][".join("".join(c for v, c in enumerate(B64) if k(v)) for k in keeps)
                                         + "]" for _ in range(3)))
            with self.subTest(group):
                self.planted(self.chains, "_ASCII_START", tighter)

    def test_a_file_start_a_prefix_short_of_a_full_one_is_found(self):
        prefixes = head._FILE_START_PREFIXES
        self.planted(self.chains, "_FILE_START_PREFIXES", frozenset(p for p in prefixes if len(p) < 13))
        self.planted(self.chains, "_FILE_START_PREFIXES", frozenset(p for p in prefixes if len(p) < 5))

    def test_a_run_that_spells_no_key_word_where_it_spells_one_is_found(self):
        self.planted(self.keys, "_spells_key", lambda text, low, high: False)
        self.planted(self.keys, "_ENCODED_KEY_CODES", [])

    def test_a_key_word_left_out_of_what_a_run_spells_is_found(self):
        codes = head._ENCODED_KEY_CODES
        for i, code in enumerate(codes):
            with self.subTest(code):
                self.assertTrue(head._spells_key("zz" + code + "zz", 2, 2 + len(code)))
                with mock.patch.object(head, "_ENCODED_KEY_CODES", codes[:i] + codes[i + 1:]):
                    self.assertFalse(head._spells_key("zz" + code + "zz", 2, 2 + len(code)),
                                     "so a check against what a run spells fails")

    def test_a_run_that_starts_no_key_file_where_one_starts_is_found(self):
        self.planted(self.keys, "_may_start_key_file", lambda text, low, high: False)

    def test_a_block_that_holds_no_key_word_where_it_holds_one_is_found(self):
        self.planted(self.keys, "_holds_key_word", lambda joined: False)

    def test_a_rule_of_gitleaks_that_finds_nothing_where_it_finds_something_is_found(self):
        real = head._may_find
        patterns = [rule[0] for rule in head._GITLEAKS]
        found = 0
        for i, (pattern, group, entropy, keywords, allowed, stopwords) in enumerate(head._GITLEAKS[:12]):
            for example in samples.samples(pattern, 2, gitleaks.RULES[i][0]):
                m = pattern.fullmatch(example)
                span = m and head._gitleaks_secret(m, group, entropy, allowed, stopwords)
                if not span:
                    continue
                secret = example[span[0]:span[1]]
                text = ("" if not keywords or any(k in secret.lower() for k in keywords) else keywords[0] + " ") + secret
                head._NEEDS.clear()
                want = (before.mask(text), before.mask_with_origin(text))
                self.assertEqual((head.mask(text), head.mask_with_origin(text)), want, f"{text} unplanted")
                with mock.patch.object(head, "_may_find", lambda p, t: False if any(p is q for q in patterns) else real(p, t)):
                    head._NEEDS.clear()
                    found += (head.mask(text), head.mask_with_origin(text)) != want
                head._NEEDS.clear()
        self.assertGreater(found, 5)


if __name__ == "__main__":
    unittest.main()
