"""The split view's readings, and the readings a long text spends its time in, made cheaper with no change in what is
masked: three shortcuts, and the tests that hold each to the reading it stands in for.

A text that holds a colour code or an invisible character is read again with it taken out (kb.mask._split_secrets), and a
long made-up session spent most of its time there (the views are read with each set of rules: main's own, this one's and
today's main's). Each shortcut skips only what cannot find anything, or reads once what it read again:
1. A pattern that reads a value after a secret's name (all start with _KEY_NAME) is tried only where a run of name
   characters starts that holds one of the words a name needs (_name_search, _name_starts), not at each run of a text:
   _KEY_NAME starts at the start of a run and takes the run to its end, so its match is a run that holds a word.
2. The token shapes of a view are one pattern, of the shapes that can find something in it (_token_spans, _may_find):
   where a first of several shapes that match is taken, one that matches nowhere is never it.
3. The edits of a step of a reading that reads nothing but its text and its row (one row of the commands, one of today's,
   the docker login, the typing steps) are made once for each text in a call (_read_once), as the readings are.

Each is held to the reading it stands in for on texts a fuzz makes up, whole outputs, origins and `capped` to what they
are with the three shortcuts off (which is what main reads), in whole and in pieces, and a break planted in each makes one
of those tests fail. tests/test_mask_speed.py holds every output to d512b6d's, as before."""
import ast
import contextlib
import contextvars
import random
import re
import time
import unittest
from unittest import mock

from kb import mask as head
from tests import samples
from tests.test_mask_hot import calls
from tests.test_mask_speed import before, code, fresh_memo, in_pieces, session, took

NAME_CLASS = re.compile(r"[A-Za-z0-9_.-]", re.I)
# Names a secret has, and names it has not, written as they are in code, JSON, env files and notes: with each word _KEY_NAME
# reads (and the characters past ASCII sre takes for ASCII ones), dotted, dashed and in capitals.
NAMES = ["password", "PASSWORD", "passwd", "passcode", "api_key", "API-KEY", "apiKey", "access_key", "private-key",
         "subscription_key", "client_secret", "SECRET", "auth_token", "token", "credentials", "my.app.secret", "db_pass",
         "db-pwd", "app_pw", "x-auth", "x-api-auth-token", "bw_session", "session_cookie", "session-key", "tokenizer",
         "tokenization", "tokens", "secretary", "a.b.c.password", "pa\u017fsword", "to\u212aen", "api_\u212aey",
         "ap\u0130_key", "passw\u0131rd", "PASSWORD\u0131", "pass.word", "pass_word", "p4ssword", ".password", "-token",
         "foo.token.", "x-authors", "db_pass.", "pw", "a-pw", "_pwd"]
QUIET = ["pelican", "plan", "build", "cache", "path", "tests", "main.py", "src/app.js", "v1.2.3", "node_modules", "ok", "x",
         "k", "---", "...", "a-b-c", "snake_case_name", "CamelCaseName", "2024-11-14", "\u0442\u0435\u0441\u0442",
         "\u043f\u0430\u0440\u043e\u043b\u044c", "\uff41\uff50\uff49", "e\u0301", "\u65e5\u672c\u8a9e", "i", "s", "K"]
VALUES = ["ladder-Q7x2", "Tr0ub4dor-Zk9q", "[masked]", "abc123def456", "'quoted value'", '"double quoted"', "", "p@ss w0rd",
          "\\n", "${VAR}", "true"]
JOINERS = [" ", "\n", ": ", "=", " = ", '": "', "': '", '"', ", ", "{", "}", "(", ")", "/", ";", "<", ">", "\\n", "\t",
           "\u200b", "\x1b[32m", " -> ", "  ", ":", "'"]
LEAST = head._CHECKED_FROM


def name_text(r, size, named=0.3):
    """About `size` characters of runs of name characters, a fraction of them secrets' names with a value."""
    parts, n = [], 0
    while n < size:
        k = r.random()
        if k < 1 - named:
            part = r.choice(QUIET)
        elif k < 1 - named * 0.3:
            part = r.choice(NAMES) + r.choice(JOINERS) + r.choice(VALUES)
        elif k < 1 - named * 0.1:
            part = f'"{r.choice(NAMES)}": "{r.choice(VALUES)}"'
        else:
            part = r.choice(NAMES).upper() + "=" + r.choice(VALUES)
        part += r.choice(JOINERS)
        parts.append(part)
        n += len(part)
    return "".join(parts)


def name_texts(count, seed="split names", size=(LEAST, 3 * LEAST)):
    r = random.Random(seed)
    for n in range(count):
        yield name_text(r, r.randint(*size), r.choice((0.05, 0.3, 0.6)))


def keyed_patterns():
    """The patterns of the four sets of rules that read a value by its name, each once."""
    seen = {}
    for rules in (head._TEXT_RULES, head._ID_RULES, head._MAIN_RULES, head._TODAYS_RULES):
        for pattern, _ in rules[1]:
            seen[id(pattern)] = pattern
    return list(seen.values())


def name_patterns():
    return [p for p in keyed_patterns() if head._starts_with_a_name(p)]


def spans(search, limit=100_000):
    """Every match a search finds one after another, as _named reads them: from the start, then from where one ended."""
    out, m = [], search()
    while m and len(out) < limit:
        out.append((m.span(), m.groups()))
        m = search(m.end()) if m.end() > m.start() else None
    return out


def plain_search(pattern, text):
    """What _named read with before: _search from the start, pattern.search from where a match ended."""
    return lambda pos=0: pattern.search(text, pos) if pos else head._search(pattern, text)


def whole_spans(rules, view):
    """What _split_secrets read before: every match of all the shapes, as one pattern."""
    return [(m.start(), m.end()) for m in head._TOKENS[id(rules)][0].finditer(view)
            if m.end() > m.start() and not head._scheme_words(m)]


@contextlib.contextmanager
def a_call(text):
    """Inside a call of the mask, which keeps its readings (_READINGS)."""
    token = head._READINGS.set(head._Readings(len(text)))
    try:
        yield
    finally:
        head._READINGS.reset(token)


@contextlib.contextmanager
def shortcuts_off(names=True, tokens=True, steps=True):
    """The three shortcuts off, where each is made: the name patterns searched as they were, every token shape in one
    pattern, and each step of a reading made each time it is asked for."""
    real = head._read_once
    kinds = (head._COMMAND_ROW, head._TODAYS_ROW, head._TEXT_STEP)

    def direct(kind, read, text, rules, *more):
        return read(text, rules, *more) if kind in kinds else real(kind, read, text, rules, *more)
    with contextlib.ExitStack() as stack:
        if names:
            stack.enter_context(mock.patch.object(head, "_name_search", plain_search))
        if tokens:
            stack.enter_context(mock.patch.object(head, "_token_spans", whole_spans))
        if steps:
            stack.enter_context(mock.patch.object(head, "_read_once", direct))
        yield


def outputs(text):
    """mask(text), mask_with_origin(text) and the texts masked whole at the pass limit, in a call with nothing kept."""
    fresh_memo(head)
    caps = head.capped
    return head.mask(text), head.mask_with_origin(text), head.capped - caps


class NameStartsTest(unittest.TestCase):
    def test_the_name_class_is_the_one_sre_takes_for_every_character(self):
        # A run of name characters is what lies between the characters of the fold that are not [a-z0-9_.-]: each
        # character must be one in the fold exactly where sre, ignoring case, takes it for one, or a start would be
        # put inside a run, or past its start. Every code point; the fold keeps a text as long as it was.
        wrong = []
        for x in range(0x110000):
            ch = chr(x)
            fold = head._folded(ch)
            if len(fold) != 1 or bool(NAME_CLASS.match(ch)) != (head._NAME_NOT.search(fold) is None):
                wrong.append(x)
        self.assertEqual(wrong[:5], [])
        for ch in "\u0130\u0131\u017f\u212a":
            self.assertIsNotNone(NAME_CLASS.match(ch), ch)

    def test_every_word_is_name_characters_alone_and_every_name_holds_one(self):
        words = head._name_words()
        self.assertGreaterEqual(len(words), 10)
        for word in words:
            self.assertIsNone(head._NAME_NOT.search(word), word)
            self.assertEqual(word, word.lower())
        every = re.compile(head._KEY_NAME)
        r = random.Random("split words")
        pieces = [*NAMES, *QUIET, "x-", "-", "_", ".", "key", "id", "a", "api", "pass", "pw", "pwd", "auth", "secret"]
        checked = 0
        for _ in range(60_000):
            run = "".join(r.choice(pieces) for _ in range(r.randint(1, 4)))
            if every.fullmatch(run):
                checked += 1
                self.assertTrue(any(word in head._folded(run) for word in words), run)
        self.assertGreater(checked, 3_000)

    def test_the_patterns_that_read_a_name_are_the_ones_that_start_with_it(self):
        found = name_patterns()
        self.assertGreaterEqual(len(found), 7)  # 4 of text's, 1 of id's, main's and today's
        self.assertTrue(all(p.flags & re.I and p.pattern.startswith("(?i)(?<![A-Za-z0-9_.-])") for p in found))
        for pattern in keyed_patterns():
            if pattern not in found:
                self.assertFalse(pattern.pattern.startswith(head._KEY_NAME), pattern.pattern[:60])

    def test_the_starts_hold_the_start_of_every_match(self):
        # A match of a pattern that starts with a name starts where a run of name characters does, and only the
        # runs that hold a word are marked: every position a pattern matches at is one.
        patterns, matches = name_patterns(), 0
        for text in name_texts(8, "split cover", (LEAST, LEAST + 3_000)):
            starts = head._name_starts_read(text)
            self.assertEqual(len(starts), len(text))
            for pattern in patterns:
                for i in range(len(text)):
                    if pattern.match(text, i):
                        matches += 1
                        self.assertEqual(starts[i], 1, (pattern.pattern[:40], text[max(0, i - 20):i + 30]))
        self.assertGreater(matches, 500)

    def test_a_search_gives_the_match_the_pattern_s_own_search_gives(self):
        patterns, found, r = name_patterns(), 0, random.Random("split search")
        for n, text in enumerate(name_texts(24, "split search")):
            with contextlib.ExitStack() as stack:
                if n % 2:
                    stack.enter_context(a_call(text))  # the starts kept in the call, as the mask reads them
                for pattern in patterns:
                    want = spans(plain_search(pattern, text))
                    found += len(want)
                    self.assertEqual(spans(head._name_search(pattern, text)), want, pattern.pattern[:40])
                    for pos in [r.randrange(len(text)) for _ in range(6)] + [0, len(text), len(text) + 5]:
                        got, want = head._name_search(pattern, text)(pos), pattern.search(text, pos)
                        self.assertEqual(got and (got.span(), got.groups()), want and (want.span(), want.groups()))
        self.assertGreater(found, 1_000)

    def test_texts_of_one_length_in_a_call_have_starts_of_their_own(self):
        r = random.Random("split one length")
        a, b = name_text(r, LEAST + 500, 0.3), name_text(r, LEAST + 500, 0.3)
        a, b = a[:min(len(a), len(b))], b[:min(len(a), len(b))]
        self.assertNotEqual(a, b)
        with a_call(a):
            for text in (a, b, a, "".join(list(b))):
                self.assertEqual(head._name_starts(text), head._name_starts_read(text))
                for pattern in name_patterns():
                    self.assertEqual(spans(head._name_search(pattern, text)), spans(plain_search(pattern, text)))

    def test_a_pattern_that_starts_with_no_name_and_a_short_text_are_searched_as_they_were(self):
        r = random.Random("split other")
        others = [p for p in keyed_patterns() if not head._starts_with_a_name(p)]
        self.assertGreaterEqual(len(others), 6)
        found = 0
        for text in (*name_texts(6, "split other"), *(name_text(r, LEAST - 200) for _ in range(4))):
            for pattern in others if len(text) >= LEAST else keyed_patterns():
                want = spans(plain_search(pattern, text))
                found += len(want)
                self.assertEqual(spans(head._name_search(pattern, text)), want, pattern.pattern[:40])
        self.assertGreater(found, 100)

    def test_a_pattern_is_searched_from_the_starts_of_runs_that_hold_a_word_alone(self):
        # Where the text holds few of them: the starts are a small part of the runs (a few in a hundred, on a made-up
        # session), and a text of runs none of which holds one has none.
        text = session(100_000, "split starts", "plain")
        starts, runs = head._name_starts_read(text), len(re.findall(r"[A-Za-z0-9_.-]+", text))
        self.assertGreater(starts.count(1), 20)
        self.assertLess(starts.count(1), runs / 20)
        quiet = " ".join(["pelican", "plan", "build"] * 4000)
        self.assertEqual(head._name_starts_read(quiet).count(1), 0)
        self.assertEqual(spans(head._name_search(name_patterns()[0], quiet)), [])

    def test_a_degenerate_text_costs_what_it_did(self):
        # One run of 100,000 name characters that holds the word at its end, a word at the start of each of 25,000 runs,
        # and a word in a run of 4,000 dotted parts: each in the time of the search from before, not the square of it.
        for text in ("a" * 100_000 + "password: ladder-Q7x2", "key password " * 8_000, ("x.password" * 4_000 + " ") * 6,
                     "a.password" * 12_000 + ": ladder-Q7x2"):
            with self.subTest(text[:20]):
                for pattern in name_patterns():
                    got, want = spans(head._name_search(pattern, text)), spans(plain_search(pattern, text))
                    self.assertEqual(got, want)
        text = "key password " * 8_000
        self.assertLess(took(text), 1.5 * took(text, before) + 0.5)


class TokenSpansTest(unittest.TestCase):
    RULES = (("text", head._TEXT_RULES), ("id", head._ID_RULES), ("main", head._MAIN_RULES),
             ("today", head._TODAYS_RULES))

    @classmethod
    def setUpClass(cls):
        cls.tokens = {name: [t for i, shape in enumerate(rules[0]) for t in samples.samples(shape, 3, f"split:{name}:{i}")]
                      for name, rules in cls.RULES}
        assert all(len(tokens) > 30 for tokens in cls.tokens.values())

    def views(self, name, count, seed):
        """Views of 3 to 40 thousand characters (and some short ones) holding no token shape's text, one, or several."""
        r = random.Random(seed)
        tokens = self.tokens[name]
        for n in range(count):
            body = name_text(r, r.choice((200, 5_000, LEAST + 100, 20_000, 40_000)), 0.2)
            for _ in range(r.choice((0, 0, 1, 1, 2, 3, 6))):
                at = r.randrange(len(body))
                body = body[:at] + r.choice((" ", "\n", "\u200b", "")) + r.choice(tokens) + r.choice((" ", "\n", "")) \
                    + body[at:]
            yield body

    def test_the_shapes_hold_no_group_to_number_or_to_name(self):
        # The shapes join as one pattern: a group numbered among others, or named, or read back, would read another
        # way where only some of the shapes are joined.
        for name, rules in self.RULES:
            for pattern in rules[0]:
                self.assertEqual(pattern.groups, 0, (name, pattern.pattern[:40]))
                self.assertIsNone(re.search(r"\\[1-9]|\(\?P|\(\?\(", pattern.pattern), pattern.pattern[:40])

    def test_the_spans_of_the_shapes_that_can_find_something_are_those_of_all_of_them(self):
        found, pruned = 0, 0
        for name, rules in self.RULES:
            for view in self.views(name, 60, f"split tokens {name}"):
                want = whole_spans(rules, view)
                found += len(want)
                self.assertEqual(head._token_spans(rules, view), want, name)
                if len(view) >= LEAST:
                    kept = sum(head._may_find(shape, view) for shape in rules[0])
                    pruned += kept < len(rules[0])
        self.assertGreater(found, 250)
        self.assertGreater(pruned, 100)

    def test_shapes_joined_are_kept_by_which_they_are_and_not_by_how_many(self):
        # Two views that need other shapes, as many as each other: each is read with the shapes it needs.
        rules = head._TEXT_RULES
        by = {i: got[0] for i, shape in enumerate(rules[0]) if (got := samples.samples(shape, 1, f"split one:{i}"))}
        pad = "pelican plan build cache " * 400
        pairs = 0
        for i in by:
            for j in by:
                if i < j:
                    view = pad + by[i] + " " + pad + by[j] + " " + pad
                    other = pad + by[j] + " " + pad
                    for text in (view, other, view, other):
                        self.assertEqual(head._token_spans(rules, text), whole_spans(rules, text), (i, j))
                    pairs += 1
        self.assertGreater(pairs, 100)
        self.assertTrue(any(len(shapes) < len(rules[0]) for shapes, _ in head._TOKEN_SUBSETS.values()))
        # Each is the shapes it joins in the order the set holds them, as the first that matches is the one taken.
        for shapes, _ in head._TOKEN_SUBSETS.values():
            self.assertTrue(any([s for s in rules[0] if s in shapes] == list(shapes)
                                for rules in (head._TEXT_RULES, head._ID_RULES, head._MAIN_RULES, head._TODAYS_RULES)))

    def test_a_view_that_holds_what_no_shape_needs_has_no_token(self):
        self.assertEqual(head._token_spans(head._TEXT_RULES, "pelican plan build " * 1000), [])
        self.assertEqual(head._token_spans(head._TEXT_RULES, ""), [])

    def test_the_view_is_read_in_half_the_time_or_less(self):
        view = session(100_000, "split view", "plain") + " ghp_" + "A1b2C3d4E5" * 3 + "6"
        rules = head._TEXT_RULES

        def best(read):
            return min(took_once(lambda: read(rules, view)) for _ in range(3))
        self.assertEqual(head._token_spans(rules, view), whole_spans(rules, view))
        self.assertLess(best(head._token_spans), 0.5 * best(whole_spans))


def took_once(read):
    started = time.process_time()
    read()
    return time.process_time() - started


def split_texts():
    """Texts with a secret split by an invisible character or a colour code in each place a step of a reading finds one,
    among 12,000 characters of code, and made-up sessions of each shape: every view is read with all three sets of rules."""
    body = code(12_000, "split body")
    made = [got[0] for i in range(8) if (got := samples.samples(head._TEXT_RULES[0][i], 1, f"split case:{i}"))]
    cases = [
        "docker login -u ci-bot -p Tr0ub4dor\u200b-Zk9q registry.example.test",
        "mysql -u root -pTr0ub4dor\x1b[0mZk9q app",
        "export API\u200b_KEY=Tr0ub4dor-Zk9q",
        'DB_PASS\u200bWORD="Tr0ub4dor-Zk9q"',
        '{"name": "Pass\u200bword", "value": "Tr0ub4dor-Zk9q"}',
        "await page.getByLabel('Pass\u200bword').fill('Tr0ub4dor-Zk9q');",
        'browser_type({"element":"Password\x1b[0m textbox","ref":"e23","text":"Tr0ub4dor-Zk9q"})',
        "curl -u admin:Tr0ub4d\u200bor-Zk9q https://example.test/\naws configure set aws_secret_access_key Tr0ub4dor\u200bZk9q",
        "psql postgres://app:Tr0ub4d\x1b[32mor-Zk9q@db.example.test:5432/app",
        "secret=" + "\u200b".join("Tr0ub4dor-Zk9q"),
        "\x1b[31mtoken\x1b[0m: Tr0ub4dor-Zk9q # pass\u200bword = ladder-Q7x2",
    ]
    for t in made:
        cases.append(t[:len(t) // 2] + "\u200b" + t[len(t) // 2:])
        cases.append(f"key: {t[:5]}\x1b[0m{t[5:]}")
    for n, case in enumerate(cases):
        yield f"case {n}", case + "\n" + body
        yield f"case {n} in the middle", body[:6_000] + "\n" + case + "\n" + body[6_000:]
    for shape in ("plain", "colour", "hash", "typing", "oneline", "onecode"):
        yield f"session {shape}", session(30_000, "split equal", shape)
    yield "names", name_text(random.Random("split names text"), 30_000, 0.4).replace("\n", "\x1b[0m\n", 1)
    yield "coloured names", "\u200b".join(name_texts(1, "split coloured")).replace("\n", "\n\x1b[32m")


class SameWithTheShortcutsOffTest(unittest.TestCase):
    """mask(), mask_with_origin() and `capped` are what they are with the three shortcuts off, which read as main does."""

    def test_every_output_and_origin_is_the_one_read_without_the_shortcuts(self):
        checked, changed = 0, 0
        for where, text in split_texts():
            checked += 1
            got = outputs(text)
            with shortcuts_off():
                want = outputs(text)
            self.assertEqual(got, want, where)
            changed += got[0] != text
        self.assertGreater(checked, 60)
        self.assertGreater(changed, 40)

    def test_a_long_text_read_in_pieces_is_the_one_read_without_the_shortcuts(self):
        with in_pieces(40_000, 12_000, 3_000):
            for shape in ("colour", "hash", "oneline"):
                text = session(100_000, "split pieces", shape)
                got = outputs(text)
                with shortcuts_off():
                    self.assertEqual(got, outputs(text), shape)

    def test_each_shortcut_alone_is_the_one_read_without_it(self):
        # The comparison above with all three off could hide two that cancel: each alone, on the texts that need one.
        texts = [text for where, text in split_texts() if where in ("case 0", "case 5", "case 8", "case 11 in the middle",
                                                                      "session colour", "coloured names")]
        self.assertEqual(len(texts), 6)
        for text in texts:
            want = outputs(text)
            for off in ({"names": True, "tokens": False, "steps": False}, {"names": False, "tokens": True, "steps": False},
                        {"names": False, "tokens": False, "steps": True}):
                with shortcuts_off(**off):
                    self.assertEqual(outputs(text), want, off)


class StepMemoTest(unittest.TestCase):
    STEPS = ("_command_password_edits", "_todays_password_edits", "_docker_login_edits", "_typed_code_edits",
             "_typed_json_edits")

    @staticmethod
    def reach(root, defs):
        seen, stack = set(), [root]
        while stack:
            name = stack.pop()
            if name in seen or name not in defs:
                continue
            seen.add(name)
            stack += [n.id for n in ast.walk(defs[name]) if isinstance(n, ast.Name)]
        return seen

    def test_a_step_made_once_reads_nothing_but_its_text_and_its_row(self):
        # A reading made once is made for the text, the rules and the context in its key (_read_once): nothing a step
        # reaches may read what is not in the key (a context variable, `capped`) or read a text by the mask itself.
        with open(head.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        impure = {name for name, value in vars(head).items() if isinstance(value, contextvars.ContextVar)}
        impure |= {name for n in ast.walk(tree) if isinstance(n, ast.Global) for name in n.names}
        impure |= {"_reading", "_run", "_read_once", "_named_found", "_gitleaks_spans", "_one_call", "mask",
                   "mask_with_origin", "_mask_whole", "_masked", "_split_secrets"}
        self.assertGreaterEqual(len(impure), 14)
        for step in self.STEPS:
            reached = self.reach(step, defs)
            self.assertGreater(len(reached), 5, step)
            self.assertEqual(sorted(name for f in reached for name in {n.id for n in ast.walk(defs[f])
                                                                        if isinstance(n, ast.Name)} & impure), [], step)
        # The same reading finds what the check is for: the one step of those a reading makes that is not made once reaches
        # the mask itself (a pair's name is read as a value), and reads what is not in a key.
        reached = self.reach("_name_value_edits", defs)
        self.assertTrue({n.id for f in reached for n in ast.walk(defs[f]) if isinstance(n, ast.Name)} & impure)

    def test_a_step_is_made_once_for_a_text_row_and_context_and_each_asker_has_a_list_of_its_own(self):
        text = "x " * 5_000 + "mysql -u root -pTr0ub4dor-Zk9q app"
        command = next((c for c in head._COMMANDS if head._command_row(text, c, False)), None)
        self.assertIsNotNone(command, "a row reads the text")
        real = head._command_password_edits
        with a_call(text), mock.patch.object(head, "_command_password_edits", mock.Mock(wraps=real)) as step:
            first = head._read_once(head._COMMAND_ROW, head._command_row, text, command, False)
            second = head._read_once(head._COMMAND_ROW, head._command_row, "".join(list(text)), command, False)
            self.assertEqual(step.call_count, 1)
            self.assertEqual(first, real(text, *command, False))
            self.assertEqual(second, first)
            self.assertIsNot(second, first)
            second.clear()
            self.assertEqual(head._read_once(head._COMMAND_ROW, head._command_row, text, command, False), first)
            self.assertEqual(step.call_count, 1)
            others = [c for c in head._COMMANDS if c is not command]
            for c in others:
                head._read_once(head._COMMAND_ROW, head._command_row, text, c, False)
            self.assertEqual(step.call_count, 1 + len(others), "a row of its own is read")
            head._read_once(head._COMMAND_ROW, head._command_row, text + " ", command, False)
            head._read_once(head._COMMAND_ROW, head._command_row, text, command, True)
            self.assertEqual(step.call_count, 3 + len(others), "so is a text of its own, and the other flag")

    def test_a_step_made_once_is_the_step_itself(self):
        # What each wrapper makes is what the step gives for the same arguments: a row of the commands with each flag (a
        # text a row reads differently with it and without it is among them), today's rows, and the steps that read a text.
        rows = [("{b'password': b'keytool'} -storepass Zq7vRt2LmX9k", 6), ("{b'password': b'mongosh'} -p Zq7vRt2LmX9k", 5),
                ("{b'password': b'aws configure set'} aws_secret_access_key Zq7vRt2LmX9k", 7),
                ("mysql -u root -pTr0ub4dor-Zk9q app", 0), ("psql postgres://app:Tr0ub4dor-Zk9q@db.example.test/app", 2)]
        differ = 0
        for text, index in rows:
            for command in head._COMMANDS:
                for flag in (False, True):
                    self.assertEqual(head._command_row(text, command, flag), head._command_password_edits(text, *command, flag))
                    self.assertEqual(head._todays_row(text, command, flag), head._todays_password_edits(text, *command, flag))
            command = head._COMMANDS[index]
            differ += head._command_row(text, command, True) != head._command_row(text, command, False)
        self.assertGreaterEqual(differ, 3, "the flag changes what a row reads on some of the texts")
        for step, text in ((head._docker_login_edits, '"auth": "Y2ktYm90OlRyMHViNGRvci1aazlx"'),
                           (head._typed_json_edits, '{"element":"Password textbox","ref":"e23","text":"Tr0ub4dor-Zk9q"}'),
                           (head._typed_code_edits, "await page.getByLabel('Password').fill('Tr0ub4dor-Zk9q');"),
                           (head._typed_code_edits, "await page.type('#password', 'Tr0ub4dor-Zk9q');")):
            self.assertTrue(step(text), (step.__name__, text))
            for end in range(len(text) - 30, len(text) + 1):  # a text that ends at the secret's last character, one that ends after
                self.assertEqual(head._step_read(text[:end], step), step(text[:end]), (step.__name__, end))
            with a_call(text):
                self.assertEqual(head._read_once(head._TEXT_STEP, head._step_read, text, step), step(text))

    def test_a_row_is_read_with_the_flag_of_its_place_in_the_list(self):
        # The rows from _MAIN_COMMANDS on read a text twice (`unseen`), the rows before it and every row of main's own
        # reading once: the flag is what a row's place says, in the rows and in today's, as it was read before.
        seen = {"command": set(), "today": set()}
        real = {"command": head._command_row, "today": head._todays_row}

        def spy(kind, rows, name):
            def row(text, command, unseen):
                index = next(i for i, c in enumerate(rows) if c is command)
                seen[kind].add((index, unseen))
                return real[kind](text, command, unseen)
            return row
        text = session(40_000, "split flags", "colour") + "\nmysql -u root -pTr0ub4dor\u200b-Zk9q app\n"
        with mock.patch.object(head, "_command_row", spy("command", head._COMMANDS, "")), \
                mock.patch.object(head, "_todays_row", spy("today", head._TODAYS_COMMANDS, "")):
            fresh_memo(head)
            head.mask(text)
        for kind, rows in (("command", head._COMMANDS), ("today", head._TODAYS_COMMANDS)):
            self.assertTrue(any(i < head._MAIN_COMMANDS for i, _ in seen[kind]), kind)
            self.assertTrue(any(i >= head._MAIN_COMMANDS for i, _ in seen[kind]), kind)
            self.assertEqual({(i, u) for i, u in seen[kind]} - {(i, i >= head._MAIN_COMMANDS) for i in range(len(rows))},
                             set(), kind)

    def test_a_split_view_makes_each_step_fewer_times_and_never_more(self):
        for shape, least in (("colour", 0.9), ("typing", 0.9), ("oneline", 0.9)):
            text = session(60_000, "split calls", shape)
            calls(head, text, self.STEPS)  # what a first call keeps for the next (_NEEDS, caches) is read already
            now = calls(head, text, self.STEPS)
            with shortcuts_off(names=False, tokens=False):
                calls(head, text, self.STEPS)
                was = calls(head, text, self.STEPS)
            with self.subTest(shape):
                for name in self.STEPS:
                    self.assertLessEqual(now[name], was[name], name)
                self.assertLess(sum(now.values()), least * sum(was.values()))
                for name in ("_command_password_edits", "_docker_login_edits", "_typed_code_edits", "_typed_json_edits"):
                    self.assertLess(now[name], was[name], name)

    def test_the_memo_is_bounded_with_the_readings(self):
        # A text read again is held once (the keys hold the texts the rows left, which the readings hold already) and the
        # memo is dropped at the end of the call, as the readings are.
        text = session(60_000, "split bound", "colour")
        readings = []
        real = head._Readings.__setitem__

        def keep(self, key, value):
            readings.append(self)
            real(self, key, value)
        with mock.patch.object(head._Readings, "__setitem__", keep):
            fresh_memo(head)
            head.mask(text)
        self.assertTrue(readings)
        for r in readings:
            self.assertTrue(r.size <= r.limit or len(r) == 1)
        self.assertIsNone(head._READINGS.get())


class CostTest(unittest.TestCase):
    """Each text is masked with the shortcuts and without them in this process, so a slow machine slows both."""

    def test_a_session_with_a_view_costs_less_than_without_the_shortcuts(self):
        for shape in ("colour", "oneline", "typing"):
            with self.subTest(shape):
                text = session(60_000, "split cost", shape)
                with shortcuts_off():
                    was = took(text)
                self.assertLess(took(text), 0.9 * was)

    def test_a_text_with_an_invisible_character_costs_less_than_it_did(self):
        text = session(60_000, "split zwsp", "plain").replace("\n\n", "\n\n\u200b", 40)
        with shortcuts_off():
            was = took(text)
        self.assertLess(took(text), 0.9 * was)

    def test_a_text_four_times_as_long_costs_at_most_eight_times_as_much(self):
        for shape in ("colour", "hash"):
            with self.subTest(shape):
                text, long = session(20_000, "split grow", shape), session(80_000, "split grow", shape)
                self.assertLess(took(long), 8 * took(text) + 0.3)

    def test_a_text_with_nothing_to_read_twice_costs_what_it_did(self):
        text = code(100_000, "split nothing")
        self.assertLess(took(text), 1.2 * took(text, before) + 0.05)


class PlantedBreakTest(unittest.TestCase):
    """A shortcut that skips what it must not, planted where it is made, makes a test above fail: each case gives what is
    read with no break planted, and what a break gives."""

    @classmethod
    def setUpClass(cls):
        cls.texts = list(name_texts(24, "split planted"))
        cls.keyed = [f"{filler}{name}: ladder-Q7x2 {filler}" for name in ("foo.password", "x-auth", "db_pass", "token", "a-pw")
                     for filler in (" ".join(["pelican", "plan"] * 1_500),)]

    def search_differs(self, texts=None, patterns=None):
        for text in texts or [*self.keyed, *self.texts]:
            for pattern in patterns or name_patterns():
                if spans(head._name_search(pattern, text)) != spans(plain_search(pattern, text)):
                    return True
        return False

    def planted_search(self, attribute, value, texts=None):
        self.assertFalse(self.search_differs(texts), f"{attribute} unplanted")
        with mock.patch.object(head, attribute, value):
            self.assertTrue(self.search_differs(texts), f"{attribute} planted")

    def test_a_start_put_at_the_word_and_not_at_the_start_of_its_run_is_found(self):
        def at_the_word(text):
            words, fold = head._name_words(), head._folded(text)
            marks = bytearray(len(text))
            for word in words:
                at = fold.find(word)
                while at >= 0:
                    marks[at] = 1
                    at = fold.find(word, at + 1)
            return bytes(marks)
        self.planted_search("_name_starts_read", at_the_word)

    def test_a_run_cut_at_a_dot_is_found(self):
        self.planted_search("_NAME_NOT", re.compile(r"[^a-z0-9_-]"))

    def test_a_run_not_cut_at_a_quote_is_found(self):
        self.planted_search("_NAME_NOT", re.compile(r"[^a-z0-9_.\-\"']"))

    def test_a_word_left_out_is_found(self):
        # A name that holds that word and no other: the starts of its run are not marked and its match is lost.
        witnesses = {"-pass": "db-pass", "-pw": "db-pw", "-pwd": "db-pwd", "_pass": "db_pass", "_pw": "db_pw",
                     "_pwd": "db_pwd", "access": "access_key", "api": "api_key", "auth": "x-auth",
                     "credential": "credentials", "passcode": "passcode", "passw": "password", "private": "private_key",
                     "secret": "my_secret", "session": "session_key", "subscription": "subscription_key",
                     "token": "my_token"}
        words = head._name_words()
        pad = " ".join(["pelican", "plan"] * 1_500)
        redundant = []
        for i, word in enumerate(words):
            with self.subTest(word):
                names = [n for n in witnesses.values() if word in n and [w for w in words if w in n] == [word]]
                if not names:
                    redundant.append(word)  # every name that holds it holds another word of the list
                    continue
                texts = [f"{pad}{names[0]}: ladder-Q7x2 {pad}", f"{pad}\n{names[0].upper()}=Tr0ub4dor-Zk9q"]
                self.assertTrue(any(head._name_search(p, t)() for p in name_patterns() for t in texts), "is found")
                self.assertFalse(self.search_differs(texts), "unplanted")
                with mock.patch.object(head, "_NAME_WORDS", [words[:i] + words[i + 1:]]):
                    self.assertTrue(self.search_differs(texts), "planted")
        # A word that holds another of the list is one every name that holds it holds another of: leaving it out finds
        # nothing, and the words that are not that are all found.
        self.assertTrue(redundant and all(any(w != word and w in word for w in words) for word in redundant), redundant)
        self.assertLess(len(redundant), len(words) / 2)

    def test_a_shortcut_for_every_pattern_and_not_those_that_start_with_a_name_is_found(self):
        self.planted_search("_starts_with_a_name", lambda pattern: True)

    def token_views(self):
        rules = head._TEXT_RULES
        tokens = [got[0] for i, shape in enumerate(rules[0]) if (got := samples.samples(shape, 1, f"split planted:{i}"))]
        pad = "pelican plan build cache " * 400
        views = [pad + a + " " + pad + b + " " + pad for a in tokens for b in tokens[::3]]
        # A token two shapes read, as one is a longer read of the other's start (the first shape that matches is taken).
        views += [pad + "sk-ant-api03-" + "Ab1" * 30 + " " + pad, pad + "sk-" + "Ab1" * 20 + "T3BlbkFJ" + "Ab1" * 20 + pad]
        return rules, views

    def token_differs(self, read):
        rules, views = self.token_views()
        return any(read(rules, view) != whole_spans(rules, view) for view in views)

    def pruned(self, pick=lambda shapes: shapes, key=lambda shapes: tuple(map(id, shapes))):
        """_token_spans as it is made, with one line of it planted."""
        def read(rules, view):
            shapes = pick(tuple(shape for shape in rules[0] if head._may_find(shape, view)))
            if not shapes:
                return []
            entry = self.cache.get(key(shapes))
            if entry is None:
                entry = self.cache[key(shapes)] = head._token_shapes(shapes)
            return [(m.start(), m.end()) for m in entry.finditer(view) if m.end() > m.start()
                    and not head._scheme_words(m)]
        self.cache = {}
        return read

    def test_the_pruned_pattern_as_it_is_made_is_the_whole_one(self):
        self.assertFalse(self.token_differs(self.pruned()))
        self.assertFalse(self.token_differs(head._token_spans))

    def test_a_pattern_of_the_first_shape_that_can_find_something_alone_is_found(self):
        self.assertTrue(self.token_differs(self.pruned(pick=lambda shapes: shapes[:1])))

    def test_a_pattern_kept_by_how_many_shapes_it_has_is_found(self):
        self.assertTrue(self.token_differs(self.pruned(key=len)))

    def test_a_pattern_of_the_shapes_that_can_find_nothing_is_found(self):
        self.assertTrue(self.token_differs(self.pruned(pick=lambda shapes: shapes[:0] or ())))

    def test_a_pattern_that_leaves_out_a_shape_that_finds_something_is_found(self):
        real = head._may_find
        rules, views = self.token_views()
        self.assertFalse(self.token_differs(head._token_spans))
        with mock.patch.object(head, "_may_find", lambda p, t: False if p is rules[0][0] else real(p, t)):
            self.assertTrue(self.token_differs(head._token_spans))

    def test_a_step_kept_by_its_text_alone_is_found(self):
        real = head._read_once
        kinds = (head._COMMAND_ROW, head._TODAYS_ROW, head._TEXT_STEP)

        def by_the_text(kind, read, text, rules, *more):
            if kind not in kinds:
                return real(kind, read, text, rules, *more)
            return real(kind, lambda t, _: read(text, rules, *more), text, kind)
        self.assertStepsDiffer(by_the_text)

    def test_a_step_kept_by_the_start_of_its_text_is_found(self):
        real = head._read_once
        kinds = (head._COMMAND_ROW, head._TODAYS_ROW, head._TEXT_STEP)

        def by_the_start(kind, read, text, rules, *more):
            if kind not in kinds:
                return real(kind, read, text, rules, *more)
            return real(kind, lambda t, r, *m: read(text, r, *m), text[:50], rules, *more)
        self.assertStepsDiffer(by_the_start)

    def test_a_step_kept_by_its_row_but_not_its_text_is_found(self):
        real = head._read_once
        kinds = (head._COMMAND_ROW, head._TODAYS_ROW, head._TEXT_STEP)

        def by_the_row(kind, read, text, rules, *more):
            if kind not in kinds:
                return real(kind, read, text, rules, *more)
            return real(kind, lambda t, r, *m: read(text, r, *m), "", rules, *more)
        self.assertStepsDiffer(by_the_row)

    def test_a_step_that_gives_the_memo_s_own_list_is_found(self):
        # What a step gives is a list of the asker's own: _edit and the rows' moves read it, and a later reading of the
        # text must find the first one's edits as they were made.
        text = "x " * 5_000 + "mysql -u root -pTr0ub4dor-Zk9q app"
        command = next(c for c in head._COMMANDS if head._command_row(text, c, False))
        with a_call(text):
            first = head._read_once(head._COMMAND_ROW, head._command_row, text, command, False)
            kept = list(first)
            first.append((0, 1, "[masked]"))
            self.assertEqual(head._read_once(head._COMMAND_ROW, head._command_row, text, command, False), kept)

    def assertStepsDiffer(self, planted):
        texts = [text for where, text in split_texts() if where in ("case 0", "case 1", "case 5", "case 6", "case 8",
                                                                      "session typing", "session colour")]
        self.assertEqual(len(texts), 7)
        want = [self.reference(text) for text in texts]
        for text, wanted in zip(texts, want):
            self.assertEqual(outputs(text), wanted)
        with mock.patch.object(head, "_read_once", planted):
            self.assertTrue(any(outputs(text) != wanted for text, wanted in zip(texts, want)))

    def reference(self, text):
        with shortcuts_off():
            return outputs(text)


if __name__ == "__main__":
    unittest.main()
