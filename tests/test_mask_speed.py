"""What one mask() call costs in readings, and its outputs held byte for byte to the mask from before the work on that
cost.

A mask() or mask_with_origin() call read the same text with the same rules again and again: mask()'s fast loop, then
_masked's tracked passes, _read's second reading with main's rules and _main_once's, and the split view's readings.
Each text is now read once with each set of rules in a call (kb.mask._run keeps the readings of the call, _READINGS),
and the matches of each name pattern, gitleaks' rules and each split view likewise. A pattern is not searched for in a
text that lacks what it needs (kb.mask._may_find), each need read from sre's own parse of the pattern.

tests/fixtures/main_mask_d512b6d.py is kb/mask.py at d512b6d (#92), before that work. mask() and mask_with_origin()
here give that module's results, character for character and origin for origin, over every family of texts the
comparisons with main build, the sixteenth review's shapes, typing calls, made-up agent sessions of each shape, and long
texts read in pieces, and they count the same texts masked whole at the pass limit (`capped`). The cost tests time both
on the same text in this process, so they hold under load. A test checks the fixture's git blob, so it is never edited:
one word of its list of code words is written across a line continuation, which Python reads as the same string, and
with that undone the file is d512b6d's blob."""
import contextlib
import hashlib
import importlib.util
import json
import os
import random
import re
import time
import unittest
from unittest import mock

from kb import mask as head
from tests import samples
from tests.test_mask_against_main import Coloured, Generated, corpus
from tests.test_mask_against_today import found

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
FIXTURE = "main_mask_d512b6d.py"
BLOB = "bf241a6f43859299c78bf51c7865e927a3247f02"
MAIN_BLOB = "04e90e4d2a2e2ef32f0a73816c5bb3e0c8faea88"  # git rev-parse d512b6d:kb/mask.py
SPLIT = b"mi\\\nni"  # the word of the list of code words written across a line continuation
PASSWORD = "Tr0ub4dor-Zk9q"
PLAIN = "pelican plan"


def _load(name):
    spec = importlib.util.spec_from_file_location(name[:-3], os.path.join(FIXTURES, name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


before = _load(FIXTURE)


def blob(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def took(text, module=head):
    """`module`'s mask(text), process time, best of 3, with what it keeps for the next call cleared."""
    best = None
    for _ in range(3):
        fresh_memo(module)
        started = time.process_time()
        module.mask(text)
        spent = time.process_time() - started
        best = spent if best is None else min(best, spent)
    return best


def fresh_memo(*modules):
    for module in modules or (head, before):
        module._MEMO.kept.clear()
        module._MEMO.size = 0


@contextlib.contextmanager
def in_pieces(whole, piece, reach):
    """Both masks read a text longer than `whole` in pieces of about `piece`, as where the memo keeps them."""
    with contextlib.ExitStack() as stack:
        for module in (head, before):
            for name, value in (("WHOLE", whole), ("ALONE", whole), ("PIECE", piece), ("REACH", reach)):
                stack.enter_context(mock.patch.object(module, name, value))
        yield


def code(size, seed="speed:code"):
    """About `size` characters of made-up code, with no secret in it."""
    r = random.Random(seed)
    lines, n = [], 0
    while n < size:
        i = len(lines)
        line = r.choice((f"    total_{i} = compute(values[{i % 97}], scale={i % 7})",
                         f"    if row_{i}.status == 'ready':", f"        queue.append(row_{i % 53}.name)",
                         f"def handle_{i}(event, context):", f"    log.info('step %d done', {i})", "")) + "\n"
        lines.append(line)
        n += len(line)
    return "".join(lines)


# Each typing call the typing reader takes, as (with a password typed into its field, with a search typed instead).
TYPING = {
    "Playwright fill": ("await page.getByLabel('Password').fill('{v}');", "await page.getByLabel('Search').fill('{v}');"),
    "Selenium send_keys": ('driver.find_element(By.ID, "password").send_keys("{v}")',
                           'driver.find_element(By.ID, "search").send_keys("{v}")'),
    "WebdriverIO setValue": ("await $('#password').setValue('{v}');", "await $('#search').setValue('{v}');"),
    "Puppeteer page.type": ("await page.type('#password', '{v}');", "await page.type('#search', '{v}');"),
    "keyboard.type": ("await page.click('#password'); await page.keyboard.type('{v}');",
                      "await page.click('#search'); await page.keyboard.type('{v}');"),
    "a tool's input": ('browser_type({{"element":"Password textbox","ref":"e23","text":"{v}"}})',
                       'browser_type({{"element":"Search box","ref":"e23","text":"{v}"}})'),
}


def typing_texts(size):
    """(name, the text with the password typed, the same text with a search typed) for each typing call, at the top of
    `size` characters of code."""
    body = code(size)
    for name, (secret, plain) in TYPING.items():
        yield name, secret.format(v=PASSWORD) + "\n" + body, plain.format(v=PLAIN) + "\n" + body


WORDS = ("the build fails on the second run because the cache path is wrong so I moved it under the project folder and "
         "reran the tests which now pass except one timing test that depends on load we should check the config file "
         "first then the deploy script and the release notes before tagging").split()
COMMANDS = ["git status --short", "python3 -m pytest -q tests/test_store.py",
            "curl -s -H 'Authorization: Bearer [masked]' https://api.example.test/v1/items?limit=20",
            "docker login -u ci-bot -p [masked] registry.example.test", "npm run build -- --mode production",
            "aws configure set aws_secret_access_key [masked]", "export API_TOKEN=[masked] && ./run.sh --verbose",
            "psql postgres://app:[masked]@db.example.test:5432/app -c 'select count(*) from items'"]
SNIPPETS = ["def load(path):\n    with open(path) as fh:\n        return json.load(fh)",
            "headers = {\"Authorization\": f\"Bearer {token}\", \"Content-Type\": \"application/json\"}",
            "DATABASE_URL=postgres://app:[masked]@localhost:5432/app\nSECRET_KEY=[masked]\nDEBUG=false"]
RESULTS = ["{\"id\": 42, \"name\": \"widget\", \"tags\": [\"a\", \"b\"], \"token\": \"[masked]\", \"ok\": true}",
           "On branch main\\nYour branch is up to date with 'origin/main'.\\n\\nnothing to commit, working tree clean",
           "  PASSED tests/test_store.py::test_put\\n  FAILED tests/test_store.py::test_timing", "Login Succeeded"]
TYPED = ('{"element": "Password field", "ref": "e17", "text": "[masked]", "submit": true}',
         "await page.fill('#password', process.env.PASSWORD); await page.click('button[type=submit]')")
MARKS = ("\x1b[31m", "\\u001b[32m", "​", "\x1b[0m")
HEX = "0123456789abcdef"


def session(size, seed, shape="plain"):
    """About `size` characters of a made-up agent session as the sources store one: USER, ASSISTANT, TOOL, RESULT and
    ERROR lines joined by blank lines. `shape`: plain; typing (a typing call every 40 lines); oneline (the whole
    session JSON-escaped on one line); onecode (one colour code in the first result); colour (a tenth of the results
    hold a colour code, an escaped one or a zero-width space); hash (a hash and a base64 run in a third of them)."""
    r = random.Random(f"{shape}:{size}:{seed}")
    words = lambda k: " ".join(r.choice(WORDS) for _ in range(k))
    parts, total, i = ["Claude Code session in ~/code/example-app (main)"], 0, 0
    while total < size:
        k = r.random()
        if shape == "typing" and i % 40 == 7:
            part = "TOOL browser_type: " + TYPED[0] if r.random() < 0.5 else "ASSISTANT: " + TYPED[1]
        elif k < 0.15:
            part = "USER: " + words(r.randint(8, 60))
        elif k < 0.35:
            part = "ASSISTANT: " + words(r.randint(10, 80))
            if r.random() < 0.5:
                part += "\n\n```\n" + "\n".join(r.choice(SNIPPETS) for _ in range(r.randint(1, 3))) + "\n```"
        elif k < 0.65:
            part = f"TOOL Bash: {r.choice(COMMANDS)} # {i}"[:300]
        elif k < 0.95:
            part = ("RESULT: " + " ".join(r.choice(RESULTS) for _ in range(r.randint(1, 3))))[:308]
            if shape == "colour" and r.random() < 0.1:
                part = part.replace("RESULT:", "RESULT: " + r.choice(MARKS), 1)
            if shape == "hash" and r.random() < 0.3:
                part += " sha256:" + "".join(r.choice(HEX) for _ in range(64))
        else:
            part = f"ERROR: Traceback (most recent call last):\n  File \"app.py\", line {i}, in run\nRuntimeError: " \
                   + words(30)
        if shape == "onecode" and i == 3:
            part = part.replace(" ", " \x1b[32m", 1)
        parts.append(part)
        total += len(part) + 2
        i += 1
    text = "\n\n".join(parts)
    return json.dumps(text) if shape == "oneline" else text


SHAPES = ("plain", "typing", "oneline", "onecode", "colour", "hash")


def texts():
    """Every family of texts the comparisons with main build, the sixteenth review's shapes, typing calls (plain,
    as JSON, coloured), made-up sessions of each shape, and long generated and coloured texts."""
    yield from corpus()
    for n, (where, text) in enumerate(found()):
        yield where, text
    for name, secret, plain in typing_texts(400):
        for label, text in (("password", secret), ("search", plain)):
            yield f"typing {name} {label}", text
            yield f"typing {name} {label} as JSON", json.dumps(text)
            yield f"typing {name} {label} coloured", "\x1b[32m" + text.replace("\n", "\x1b[0m\n", 1)
    for shape in SHAPES:
        for size in (3_000, 10_000):
            yield f"session {shape} {size}", session(size, "equal", shape)
    for n in range(8):
        yield f"generated long {n}", "\n".join(Generated(random.Random(f"speed:{n}:{k}")).text() for k in range(12))
        yield f"coloured long {n}", "\n".join(Coloured(random.Random(f"speed-colour:{n}:{k}")).text() for k in range(12))


class SameAsBeforeTest(unittest.TestCase):
    def test_the_fixture_is_the_mask_it_names_byte_for_byte(self):
        with open(os.path.join(FIXTURES, FIXTURE), "rb") as f:
            data = f.read()
        self.assertEqual(blob(data), BLOB)
        self.assertEqual(data.count(SPLIT), 1)
        self.assertEqual(blob(data.replace(SPLIT, b"mi" + b"ni")), MAIN_BLOB)

    def test_every_output_and_origin_is_the_one_from_before(self):
        # The mask now also hides what a typing call written as an object in text types into a field that is not on the
        # short list of harmless ones (kb.mask._typed_json_edits), and what code types with no field named or into
        # a field not on it (kb.mask._typed_code_edits, which d512b6d hid by the label's asking for a secret), which
        # d512b6d showed. A text that differs is checked with the first switched off and the second d512b6d's own: it
        # must give d512b6d's result then, character for character and origin for origin, so those two are the only
        # difference, and they only hide more (the characters they leave are d512b6d's).
        problems, checked, differ, extra = [], 0, [], 0
        ours, theirs = head.capped, before.capped
        for where, text in texts():
            checked += 1
            for name in ("mask", "mask_with_origin"):
                got, want = getattr(head, name)(text), getattr(before, name)(text)
                if got != want:
                    before_off = head.capped
                    with mock.patch.object(head, "_typed_json_edits", lambda text: []), \
                            mock.patch.object(head, "_typed_code_edits", before._typed_code_edits):
                        off = getattr(head, name)(text)
                    extra += head.capped - before_off
                    if off != want:
                        problems.append(f"{where}: {name} gives {got!r}, before {want!r} (text {text!r})")
                    else:
                        differ.append(where)
        self.assertGreater(checked, 3000)
        self.assertEqual(problems[:3], [])
        # Each text counts once for mask() and once for mask_with_origin(). 4 were allowed (a typing call written as an
        # object, one text). REVISION 44 hides what is typed into the focused field (page.keyboard.type) whatever it is,
        # so the 3 texts of a search typed there differ now (plain, as JSON, coloured): 2 more entries each, 10 in all.
        self.assertLessEqual(len(differ), 4 + 2 * 3, "only a typing call differs")
        self.assertEqual(head.capped - ours - extra, before.capped - theirs)

    def test_a_long_text_read_in_pieces_is_the_one_from_before_and_keeps_what_it_did(self):
        # Pieces and windows each keep what they found in the memo (_part): the keywords a piece's reading needed, as
        # well as its result. A piece repeated reads as it did before, in the memo too.
        block = session(9_000, "block", "colour")
        longs = [session(100_000, "long", "colour"), session(100_000, "long", "plain"), (block + "\n\n") * 9,
                 code(60_000).replace("\n", "\n\x1b[32mok\x1b[0m ", 1)]
        # (The memo's keys hold the mask's VERSION, which REVISIONs 43 and 44 changed: head reads under d512b6d's here, so the
        # keys are the same and what the memo kept is compared.)
        with in_pieces(40_000, 12_000, 3_000), mock.patch.object(head, "VERSION", before.VERSION):
            for text in longs:
                for name in ("mask", "mask_with_origin"):
                    fresh_memo()
                    got, want = getattr(head, name)(text), getattr(before, name)(text)
                    self.assertEqual(got, want, name)
                    self.assertEqual(head._MEMO.kept, before._MEMO.kept, name)


class CostTest(unittest.TestCase):
    """Each text is masked by this mask and the one from before in this process, so a slow machine slows both."""
    BAR = 0.75  # the time it took, at most

    def test_a_masked_typing_call_costs_less_than_it_did(self):
        for name, secret, plain in typing_texts(100_000):
            with self.subTest(name):
                self.assertNotIn(PASSWORD, head.mask(secret))
                if name == "keyboard.type":
                    # Repinned (REVISION 44): typing into the focused field names no field, and what is typed with no
                    # field named is hidden, a search too. It was kept, as the shape of a search is no credential's.
                    self.assertNotIn(PLAIN, head.mask(plain))
                else:
                    self.assertIn(PLAIN, head.mask(plain))
                self.assertLess(took(secret), self.BAR * took(secret, before))

    def test_one_colour_code_in_a_long_text_costs_what_it_did(self):
        # A colour code makes the mask read the text without it too, as main's and this mask's views and today's main's
        # take it (_split_secrets): each is another string, read whole once, as it was. Where a text holds one in a
        # session, the readings of the text as written, made again and again, are the cost the sessions below lose.
        text = code(100_000).replace("\n", "\n\x1b[32mok\x1b[0m ", 1)
        self.assertLess(took(text), 1.2 * took(text, before) + 0.05)

    def test_a_session_costs_less_than_it_did(self):
        for shape in ("plain", "colour", "oneline", "onecode"):
            with self.subTest(shape):
                text = session(60_000, "cost", shape)
                self.assertLess(took(text), self.BAR * took(text, before))

    def test_a_text_with_nothing_to_read_twice_costs_what_it_did(self):
        text = code(100_000)
        self.assertLess(took(text), 1.2 * took(text, before) + 0.05)

    def test_a_text_four_times_as_long_costs_at_most_eight_times_as_much(self):
        typed = f'browser_type({{"element":"Password textbox","ref":"e23","text":"{PASSWORD}"}})\n'
        for text in (code(25_000).replace("\n", "\n\x1b[32mok\x1b[0m ", 1), typed + code(25_000)):
            with self.subTest(text[:20]):
                long = text + code(75_000, "speed:more")
                self.assertLess(took(long), 8 * took(text) + 0.3)


def patterns():
    """Every compiled text pattern the mask module holds, in its lists, tuples and dicts of them."""
    seen = {}

    def walk(value, depth=0):
        if isinstance(value, re.Pattern):
            if isinstance(value.pattern, str):
                seen[id(value)] = value
        elif depth < 6 and isinstance(value, (tuple, list)):
            for item in value:
                walk(item, depth + 1)
        elif depth < 6 and isinstance(value, dict):
            for item in (*value, *value.values()):
                walk(item, depth + 1)
    for value in vars(head).values():
        walk(value)
    return list(seen.values())


def spelled(whole):
    """Each text as written, as JSON, escaped twice, in capitals, and with the letters sre takes for ASCII ones (ı, ſ,
    the Kelvin sign, İ) in place of ASCII ones."""
    for text in whole:
        yield text
        yield json.dumps(text)
        yield json.dumps(json.dumps(text))
        yield text.upper()
        yield text.replace("i", "\u0131").replace("s", "\u017f").replace("k", "\u212a").replace("I", "\u0130")


def skipped_but_found(pattern, whole):
    """The texts of `whole` in which `pattern` finds something, and which _may_find says it finds nothing in."""
    return [text for text in whole if not head._may_find(pattern, text) and pattern.search(text) is not None]


def fuzz(seed, count=1500):
    """Patterns made up from literals, classes, groups, branches, repeats and lookarounds, with and without ignoring
    case, each tried on texts of letters, marks and the characters past ASCII that sre takes for ASCII ones: (the
    pattern, flags and text that _may_find says finds nothing where something is found, patterns made, pairs skipped,
    pairs where something is found)."""
    r = random.Random(seed)
    letters = "abcikqsx=_-1 "

    def atom(depth):
        k = r.random()
        if k < 0.4 or depth > 2:
            return re.escape(r.choice(letters + letters.upper()))
        if k < 0.5:
            return r.choice(("[a-c]", "[ik]", "[^a]", r"\d", r"\w", ".", "[a-z]", "[A-Z0-9]", r"\s", "[sS]"))
        if k < 0.6:
            return "(" + sequence(depth + 1) + ")"
        if k < 0.7:
            return "(?:" + "|".join(sequence(depth + 1) for _ in range(r.randint(2, 3))) + ")"
        if k < 0.8:
            return "(?=" + sequence(depth + 1) + ")" if r.random() < 0.5 else "(?!" + sequence(depth + 1) + ")"
        if k < 0.85:
            return "(?<=" + re.escape(r.choice(letters)) + ")"
        return r.choice(("(?:" + atom(depth + 1) + ")?", atom(depth + 1) + "*", atom(depth + 1) + "+",
                         atom(depth + 1) + "{2}", atom(depth + 1) + "{0,3}", atom(depth + 1) + "+?"))

    def sequence(depth):
        return "".join(atom(depth) for _ in range(r.randint(1, 4)))
    alphabet = letters + letters.upper() + "\u0131\u0130\u017f\u212a\n"
    wrong, made, skips, hits = [], 0, 0, 0
    for _ in range(count):
        source = sequence(0)
        for flags in (0, re.I):
            try:
                pattern = re.compile(source, flags)
            except re.error:
                continue
            made += 1
            for _ in range(12):
                text = "".join(r.choice(alphabet) for _ in range(r.randint(0, 24)))
                found_here = pattern.search(text) is not None
                may = head._may_find(pattern, text)
                skips += not may
                hits += found_here
                if found_here and not may:
                    wrong.append((source, flags, text))
    return wrong, made, skips, hits


def folds_missed():
    """The characters past ASCII that sre takes for an ASCII letter, ignoring case, which _may_find does not read as it:
    a pattern of that letter, alone and in a class, finds a text holding the character where it is searched."""
    missed = []
    for ch, letter in (("\u0130", "i"), ("\u0131", "i"), ("\u017f", "s"), ("\u212a", "k")):
        for source in (f"q{letter}z", f"q[{letter}]z", f"q[{letter}x]z", f"q(?:{letter}|y)z"):
            pattern, text = re.compile(source, re.I), "x" * 4 + "Q" + ch + "Z" + "x" * 4
            if pattern.search(text) is not None and not head._may_find(pattern, text):
                missed.append((source, text))
    return missed


class NeedsTest(unittest.TestCase):
    """_may_find says a pattern can find nothing in a text only where that is so, for the patterns the module holds, over
    the texts the comparisons with main build and the ones a fuzz makes up."""

    def setUp(self):
        checked = mock.patch.object(head, "_CHECKED_FROM", 0)
        checked.start()
        self.addCleanup(checked.stop)

    def test_the_fold_holds_every_character_sre_takes_for_an_ascii_one(self):
        # What the ASCII letters and classes of a pattern find ignoring case, over every code point past ASCII.
        every = "".join(chr(x) for x in range(128, 0x110000) if not 0xd800 <= x < 0xe000)
        taken = {}
        for letter in "abcdefghijklmnopqrstuvwxyz":
            for pattern in (letter, letter.upper(), f"[{letter}]", f"[{letter.upper()}]"):
                for ch in re.compile(pattern, re.I).findall(every):
                    taken.setdefault(ch, set()).add(letter)
        self.assertEqual({ch: "".join(letters) for ch, letters in taken.items()}, head._ASCII_FOLDS)
        for ranged in ("[a-z]", "[A-Z]", "[a-zA-Z]", "[a-z0-9_]", "[A-F]"):
            for ch in re.compile(ranged, re.I).findall(every):
                self.assertIn(ch, head._ASCII_FOLDS, ranged)
        for ch, letter in head._ASCII_FOLDS.items():
            self.assertEqual(head._folded(f"a{ch}B"), f"a{letter}b")
        # No character past ASCII is taken for a digit or a mark, which a literal in a need may be.
        for char in "0123456789_-=:/.@ ":
            self.assertEqual(re.compile(re.escape(char), re.I).findall(every), [])

    def test_a_need_is_read_from_the_parse(self):
        for source, needs in ((r"ghp_[A-Za-z0-9]{36}", (("ghp_",),)), (r"\bmysql\b(?:\s+-p\S+)?", (("mysql",),)),
                              (r"(?:foo|bar)baz", (("barbaz", "foobaz"),)), (r"(?:abc)?def", (("def",),)),
                              (r"(?!abc)xyz", (("xyz",),)), (r"(?<=key=)\w+", (("key=",),)),
                              (r"(?i:token)\s*=\s*\w+", (("token",), ("=",))), (r"a|b", (("a", "b"),)),
                              (r"[^a]+", None), (r"\d+", None), ("\u00e9+", None), (r"x*y?", None)):
            with self.subTest(source):
                self.assertEqual(head._required(re.compile(source, re.I)), needs)
        self.assertIsNone(head._required(re.compile(b"abc")))

    def test_a_text_shorter_than_the_least_is_never_skipped(self):
        with mock.patch.object(head, "_CHECKED_FROM", 10):
            self.assertTrue(head._may_find(re.compile("qqqq"), "x" * 9))
            self.assertFalse(head._may_find(re.compile("qqqq"), "x" * 10))

    def test_a_pattern_said_to_find_nothing_finds_nothing(self):
        # A tenth of the corpus and of the texts found, as each is written and in the forms `spelled` gives; the whole of
        # them, over every pattern, is the check the pull request names.
        whole = [text for _, text in list(corpus())[::10] + list(found())[::10]]
        for _, secret, plain in typing_texts(2_000):
            whole += [secret, plain]
        whole += [session(20_000, "needs", shape) for shape in ("plain", "colour", "typing", "hash")]
        whole = list(spelled(whole))
        every, skips = patterns(), 0
        self.assertGreater(len(every), 400)
        for pattern in every:
            self.assertEqual(skipped_but_found(pattern, whole), [], pattern.pattern[:80])
            skips += sum(not head._may_find(pattern, text) for text in whole[:300])
        self.assertGreater(skips, 20_000)  # most pairs are skipped: the check does something

    def test_a_pattern_a_fuzz_makes_up_is_not_skipped_where_it_finds_something(self):
        wrong, made, skips, hits = fuzz("needs-fuzz")
        self.assertEqual(wrong[:3], [])
        self.assertEqual(folds_missed(), [])
        self.assertGreater(made, 2000)
        self.assertGreater(skips, 5_000)
        self.assertGreater(hits, 1_000)

    def test_a_need_that_is_wrong_is_found(self):
        # Each break plants a wrong need or fold: the check over the module's patterns must name a text a pattern finds
        # something in that _may_find says it does not.
        whole = [text for _, text in list(corpus())[::25]]
        whole += ["token ghp_" + "A1b2C3d4" * 5, "mysql -pTr0ub4dorZk9q app", "db_password=Tr0ub4dor-Zk9q", "KEY \u212aEY"]
        whole = list(spelled(whole))
        every = patterns()
        real_required, real_item, folds = head._required, head._item_needs, head._ASCII_FOLDS
        c = head._sre

        def plant(label, required=None, item=None, fold=None):
            with contextlib.ExitStack() as stack:
                for name, value in (("_required", required), ("_item_needs", item), ("_ASCII_FOLDS", fold)):
                    if value is not None:
                        stack.enter_context(mock.patch.object(head, name, value))
                head._NEEDS.clear()
                try:
                    wrong = folds_missed() if fold is not None else \
                        [p.pattern[:40] for p in every if skipped_but_found(p, whole)]
                finally:
                    head._NEEDS.clear()
            self.assertTrue(wrong, label)

        def one_more_letter(pattern):  # a need that holds a letter no text need hold
            got = real_required(pattern)
            return tuple(tuple(literal + "x" for literal in need) for need in got) if got else got

        def one_way_of_each(pattern):  # an alternative of each need lost
            got = real_required(pattern)
            return tuple(need[:1] for need in got) if got else got

        def optional_kept(op, av):  # what a repeat of nothing or more holds taken for a need
            return real_item(op, (1, *av[1:]) if op in (c.MAX_REPEAT, c.MIN_REPEAT) and av[0] == 0 else av)

        def none_kept(op, av):  # what a lookahead that must not hold taken for a need
            return real_item(c.ASSERT if op is c.ASSERT_NOT else op, av)
        plant("a letter too many", required=one_more_letter)
        plant("an alternative lost", required=one_way_of_each)
        plant("an optional part needed", item=optional_kept)
        plant("a negative lookahead needed", item=none_kept)
        plant("no fold of the long s", fold={"\u0130": "i", "\u0131": "i", "\u212a": "k"})
        plant("no fold of the dotless i", fold={"\u0130": "i", "\u017f": "s", "\u212a": "k"})
        plant("no fold of the dotted I", fold={"\u0131": "i", "\u017f": "s", "\u212a": "k"})
        plant("no fold at all", fold={})

    def test_every_output_and_origin_is_the_one_from_before_with_every_text_checked(self):
        # Every fourth text of the families above, read with the check made on texts of any length (it is made on the
        # long ones only, `_CHECKED_FROM`): the whole of them, so, is the offline check the pull request names.
        problems = []
        for where, text in list(texts())[::4]:
            for name in ("mask", "mask_with_origin"):
                got, want = getattr(head, name)(text), getattr(before, name)(text)
                if got != want:
                    problems.append(f"{where}: {name} gives {got!r}, before {want!r} (text {text!r})")
        self.assertEqual(problems[:3], [])


class PlantedBreakTest(unittest.TestCase):
    """A wrong skip of a family of patterns, where it is planted at the check, makes the outputs differ from the ones
    from before on a text only that family reads, so the comparison above is one that catches it. Each case also
    gives the outputs from before with no break planted."""

    def setUp(self):
        checked = mock.patch.object(head, "_CHECKED_FROM", 0)
        checked.start()
        self.addCleanup(checked.stop)

    def plant(self, family):
        real = head._may_find
        head._NEEDS.clear()
        planted = mock.patch.object(head, "_may_find", lambda pattern, text: False if any(
            pattern is member for member in family) else real(pattern, text))
        return planted

    def case(self, label, text, family):
        want = (before.mask(text), before.mask_with_origin(text))
        self.assertEqual((head.mask(text), head.mask_with_origin(text)), want, f"{label} unplanted")
        self.assertNotEqual(want[0], text, f"{label}: the text holds a secret")
        with self.plant(family):
            self.assertNotEqual((head.mask(text), head.mask_with_origin(text)), want, f"{label} planted")

    def test_a_wrong_skip_of_a_token_shape_is_found(self):
        shapes = [p for rules in (head._TEXT_RULES, head._MAIN_RULES, head._TODAYS_RULES, head._ID_RULES) for p in rules[0]]
        self.case("shape", "token ghp_" + "A1b2C3d4" * 5, shapes)

    def test_a_wrong_skip_of_a_name_pattern_is_found(self):
        keyed = [p for rules in (head._TEXT_RULES, head._MAIN_RULES, head._TODAYS_RULES, head._ID_RULES)
                 for p, _ in rules[1]]
        self.case("name", "db_password=Tr0ub4dor-Zk9q", keyed)
        self.case("short name", "user=ci pass=Tr0ub4dor-Zk9q", keyed)

    def test_a_wrong_skip_of_the_docker_auth_field_is_found(self):
        self.case("docker auth", '{"auths": {"r.example.test": {"auth": "dXNlcjpUcjB1YjRkb3ItWms5cQ=="}}}',
                  [head._AUTH_FIELD])

    def test_a_wrong_skip_of_the_word_any_command_needs_is_found(self):
        self.case("any command", "mysql -pTr0ub4dorZk9q app", [head._ANY_COMMAND])

    def test_a_wrong_skip_of_the_command_rows_is_found(self):
        rows = [row[0] for row in (*head._COMMANDS, *head._TODAYS_COMMANDS)] + list(head._MAIN_SPELLING.values())
        for label, text in (("mysql", "mysql -pTr0ub4dorZk9q app"), ("curl", "curl -u admin:Tr0ub4dor-Zk9q https://x.test"),
                            ("sshpass", "sshpass -p Tr0ub4dor-Zk9q ssh host"),
                            ("docker login", "docker login -u ci -p Tr0ub4dor-Zk9q r.example.test"),
                            ("redis-cli", "redis-cli -a Tr0ub4dor-Zk9q ping"),
                            ("aws", "aws configure set aws_secret_access_key Tr0ub4dor-Zk9q"),
                            ("netrc", "machine h.example.test login ci password Tr0ub4dor-Zk9q")):
            with self.subTest(label):
                self.case(label, text, rows)

    def test_a_wrong_skip_of_a_link_s_password_is_found(self):
        prefixed = [*head.PREFIXED_SECRETS, *head._MAIN_PREFIXED, head._SUBSCRIPTION_KEY]
        shapes = [p for rules in (head._TEXT_RULES, head._MAIN_RULES, head._TODAYS_RULES, head._ID_RULES) for p in rules[0]]
        keyed = [p for rules in (head._TEXT_RULES, head._MAIN_RULES, head._TODAYS_RULES, head._ID_RULES)
                 for p, _ in rules[1]]
        # Another reading of each such text masks the password too, so this plant skips every reading of it.
        self.case("link", "git clone https://ci:Tr0ub4dor-Zk9q@host.example.test/x.git", prefixed + shapes + keyed)


class ReadingsTest(unittest.TestCase):
    def spy(self):
        """Every reading made (head._reading), as what it was given, until the test ends."""
        made, real = [], head._reading

        def reading(text, rules, origin=None, named=None, fired=None, look=False):
            made.append((text, id(rules), origin is None, named is None, fired is None, bool(fired), look,
                         head._WRAPPINGS.get(), head._READ_DEPTH.get(), head._STORED_ROW.get(),
                         head._KEY_PLACES.get(), head._KEY_HERE.get()))
            return real(text, rules, origin, named, fired, look)
        head._reading = reading
        self.addCleanup(setattr, head, "_reading", real)
        return made

    def test_one_call_makes_each_reading_once(self):
        made = self.spy()
        for name, secret, plain in typing_texts(5_000):
            for text in (secret, plain, "\x1b[32m" + secret, json.dumps(secret)):
                for call in (head.mask, head.mask_with_origin):
                    del made[:]
                    call(text)
                    self.assertTrue(made)
                    self.assertEqual(len(made), len(set(made)), f"{name}, {call.__name__}")

    def test_a_masked_typing_call_is_read_in_four_readings_at_most(self):
        made = self.spy()
        for name, secret, plain in typing_texts(5_000):
            del made[:]
            head.mask(secret)
            self.assertLessEqual(len(made), 4, name)

    def test_the_readings_last_one_call(self):
        head.mask("token ghp_" + "A1b2C3d4" * 5)
        self.assertIsNone(head._READINGS.get())
        head.mask_with_origin("token ghp_" + "A1b2C3d4" * 5)
        self.assertIsNone(head._READINGS.get())
        with self.assertRaises(RecursionError):
            with mock.patch.object(head, "_mask_whole", side_effect=RecursionError):
                head.mask("x")
        self.assertIsNone(head._READINGS.get())

    def test_a_reading_made_again_adds_and_counts_what_the_first_did(self):
        def reading(text, rules, origin=None, named=None, fired=None, look=False):
            head.capped += 1  # as a header's value masked whole at the pass limit counts it
            named.append((0, 1, 2, 3))
            fired.append("fired")
            return text[1:], origin[1:]
        real = head._reading
        head._reading = reading
        self.addCleanup(setattr, head, "_reading", real)
        token = head._READINGS.set({})
        self.addCleanup(head._READINGS.reset, token)
        start = head.capped
        for n in range(2):
            named, fired = [], []
            masked, came = head._run("abc", head._TEXT_RULES, head._fresh("abc"), named, fired)
            self.assertEqual((masked, came, named, fired), ("bc", [1, 2], [(0, 1, 2, 3)], ["fired"]))
            self.assertEqual(head.capped - start, n + 1)

    def test_an_origin_an_edit_carried_over_is_read_each_time(self):
        made = self.spy()
        token = head._READINGS.set({})
        self.addCleanup(head._READINGS.reset, token)
        for _ in range(2):
            head._run("token ghp_" + "A1b2C3d4" * 5, head._TEXT_RULES, list(range(45)))
        self.assertEqual(len(made), 2)
        for _ in range(2):
            head._run("token ghp_" + "A1b2C3d4" * 5, head._TEXT_RULES, head._fresh("x" * 45))
        self.assertEqual(len(made), 3)

    def test_a_result_is_never_the_readings_own_copy(self):
        token = head._READINGS.set({})
        self.addCleanup(head._READINGS.reset, token)
        text = "token ghp_" + "A1b2C3d4" * 5
        first = head._run(text, head._TEXT_RULES, head._fresh(text))
        first[1].append(99)
        again = head._run(text, head._TEXT_RULES, head._fresh(text))
        self.assertEqual(again, head._reading(text, head._TEXT_RULES, head._fresh(text)))
        self.assertIsInstance(again[1], list)
        again[1].append(98)
        self.assertEqual(head._run(text, head._TEXT_RULES, head._fresh(text)), head._reading(
            text, head._TEXT_RULES, head._fresh(text)))

    def context(self):
        return (head._WRAPPINGS.get(), head._READ_DEPTH.get(), head._STORED_ROW.get(), head._KEY_PLACES.get(),
                head._KEY_HERE.get(), head._SEEN_KEYWORDS.get(), head._FAR.get() is None)

    def spy_on(self, name, key):
        """Each call of head.<name> as key(*what it was given) and the context it was made in, until the test ends."""
        made, real = [], getattr(head, name)

        def spied(*args):
            made.append((key(*args), self.context()))
            return real(*args)
        setattr(head, name, spied)
        self.addCleanup(setattr, head, name, real)
        return made

    def sessions(self):
        """Texts that make the mask read each text again with each view of it: a command that names a cloud tool, a colour
        code, a zero-width space, a typing call."""
        for shape in ("plain", "colour", "onecode", "typing"):
            yield session(6_000, "memo", shape)
        yield from (secret for _, secret, _ in typing_texts(3_000))
        yield "aws configure set aws_secret_access_key " + PASSWORD + "\n" + code(3_000).replace("\n", "\n\x1b[32mok\x1b[0m ", 1)

    READS = {  # what is read in a call, with what it is read from
        "_named": lambda pattern, span_of, text, reading, *_: (id(pattern), id(span_of), text, reading),
        "_gitleaks_read": lambda text, rules: (text, rules),
        "_split_secrets_read": lambda text, rules: (text, id(rules)),
        "_main_split_spans_read": lambda text, rules: (text, id(rules)),
        "_split_secrets_seen_read": lambda text, rules, run, starts, seen: (
            text, id(rules), id(run), id(starts), tuple(map(tuple, seen)))}

    def reads(self, *names):
        """{name: [what each call of head.<name> was read from, in what context]} for every call of mask() and
        mask_with_origin() over `self.sessions()`, as (calls, repeated reads) a name."""
        made = {name: self.spy_on(name, self.READS[name]) for name in names}
        calls, repeats = dict.fromkeys(names, 0), dict.fromkeys(names, 0)
        for text in self.sessions():
            for call in (head.mask, head.mask_with_origin):
                for each in made.values():
                    del each[:]
                call(text)
                for name, each in made.items():
                    calls[name] += len(each)
                    repeats[name] += len(each) - len(set(each))
        return calls, repeats

    def without_memos(self):
        """The patches that make each read as it was before the memos: every time it is asked for."""
        return (mock.patch.object(head, "_named_found", lambda *args: list(head._named(*args))),
                mock.patch.object(head, "_read_once", lambda kind, read, text, rules, *more: read(text, rules, *more)),
                mock.patch.object(head, "_gitleaks_spans", lambda text, rules=None: head._gitleaks_read(text, rules)))

    def test_one_call_reads_each_text_once_with_each_name_pattern_gitleaks_rules_and_split_view(self):
        names = tuple(self.READS)
        calls, repeats = self.reads(*names)
        for name in names:
            self.assertGreater(calls[name], 10, name)  # the texts do make each read
            self.assertEqual(repeats[name], 0, name)

    def test_without_the_memos_a_call_reads_a_text_again_with_each(self):
        # The same texts, with the memos left out: each read is made again, so the test above can fail.
        with contextlib.ExitStack() as stack:
            for patch in self.without_memos():
                stack.enter_context(patch)
            calls, repeats = self.reads(*self.READS)
        for name in self.READS:
            self.assertGreater(repeats[name], 0, name)

    def test_a_split_view_made_again_gives_the_first_s_spans_and_counts_what_it_did(self):
        calls = []

        def read(text, rules, extra):
            calls.append(text)
            head.capped += 2
            return [(0, 1), (2, 3)] if extra else extra
        token = head._READINGS.set({})
        self.addCleanup(head._READINGS.reset, token)
        start = head.capped
        first = head._read_once(head._SPLIT_SECRETS, read, "abc", head._TEXT_RULES, [(5, 6)])
        again = head._read_once(head._SPLIT_SECRETS, read, "abc", head._TEXT_RULES, [(5, 6)])
        self.assertEqual((calls, head.capped - start), (["abc"], 4))
        self.assertEqual((first, again), ([(0, 1), (2, 3)], [(0, 1), (2, 3)]))
        again.append("changed")
        first.append("changed too")
        self.assertEqual(head._read_once(head._SPLIT_SECRETS, read, "abc", head._TEXT_RULES, [(5, 6)]),
                         [(0, 1), (2, 3)])
        # Another text, other rules, another of what it is given or another kind of reading is another reading.
        head._read_once(head._SPLIT_SECRETS, read, "abd", head._TEXT_RULES, [(5, 6)])
        head._read_once(head._SPLIT_SECRETS, read, "abc", head._MAIN_RULES, [(5, 6)])
        head._read_once(head._SPLIT_SECRETS, read, "abc", head._TEXT_RULES, [(5, 7)])
        head._read_once(head._MAIN_SPLIT, read, "abc", head._TEXT_RULES, [(5, 6)])
        self.assertEqual(len(calls), 5)

    def test_a_split_view_made_in_no_call_is_read_each_time(self):
        calls = []
        self.assertIsNone(head._READINGS.get())
        for _ in range(2):
            head._read_once(head._SPLIT_SECRETS, lambda text, rules: calls.append(text) or [], "abc", head._TEXT_RULES)
        self.assertEqual(calls, ["abc", "abc"])

    def test_a_name_pattern_read_again_gives_the_first_s_matches_notes_and_count(self):
        pattern = head.KEYED_SECRET
        text = "db_password=Tr0ub4dor-Zk9q"
        token = head._READINGS.set({})
        self.addCleanup(head._READINGS.reset, token)
        calls = []

        def named(pattern, span_of, text, reading, note):
            calls.append(reading)
            head.capped += 3
            note("noted")
            yield pattern.search(text), (3, 5)
        real = head._named
        head._named = named
        self.addCleanup(setattr, head, "_named", real)
        span_of = head._TEXT_RULES[1][0][1]
        start, notes = head.capped, []
        first = head._named_found(pattern, span_of, text, "named", notes.append)
        again = head._named_found(pattern, span_of, text, "named", notes.append)
        self.assertEqual((calls, notes, head.capped - start), (["named"], ["noted", "noted"], 6))
        self.assertEqual([span for _, span in first], [(3, 5)])
        self.assertEqual(first, again)
        again.clear()
        self.assertEqual(len(head._named_found(pattern, span_of, text, "named", notes.append)), 1)
        head._named_found(pattern, span_of, text, "main", notes.append)
        head._named_found(pattern, span_of, text + " ", "named", notes.append)
        self.assertEqual(len(calls), 3)

    def test_gitleaks_rules_read_again_give_the_first_spans_as_a_copy(self):
        text = "x" * 20
        token = head._READINGS.set({})
        self.addCleanup(head._READINGS.reset, token)
        calls = []

        def read(text, rules):
            calls.append(text)
            return [(1, 2)]
        real = head._gitleaks_read
        head._gitleaks_read = read
        self.addCleanup(setattr, head, "_gitleaks_read", real)
        first, again = head._gitleaks_spans(text), head._gitleaks_spans(text)
        again.append((9, 9))
        self.assertEqual((first, head._gitleaks_spans(text), calls), ([(1, 2)], [(1, 2)], [text]))
        head._gitleaks_spans(text, head._GITLEAKS[:1])  # given rules, it reads them
        head._gitleaks_spans(text + "y")
        self.assertEqual(len(calls), 3)

    def test_a_piece_notes_what_its_own_reading_found(self):
        # What a piece's reading finds of gitleaks' rules is noted for that piece alone (_FAR, _SAW): the same text read
        # again as another piece, in the same call, is read again, and notes it there.
        rule = next(r for r in head._GITLEAKS if r[3] == ("facebook",))  # its secret holds no keyword
        text = "\n".join(["a plain line"] * 5 + [samples.samples(rule[0], 1, 7)[0]])

        def far():
            return json.loads(head._MEMO.get("far", text))["far"]
        seen, readings = head._SEEN_KEYWORDS.set(frozenset(["facebook"])), head._READINGS.set({})
        self.addCleanup(head._SEEN_KEYWORDS.reset, seen)
        self.addCleanup(head._READINGS.reset, readings)
        fresh_memo()
        head._part(text)
        first = far()
        fresh_memo()
        head._part(text)
        self.assertEqual(first, [["facebook"]])
        self.assertEqual(far(), first)
        self.assertEqual(head._READINGS.get(), {})


if __name__ == "__main__":
    unittest.main()
