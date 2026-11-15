"""The scans of a long text, made once in a call, with no change in what is masked.

A long agent session is one text that mask() reads many times over: the readings of the text as written, with main's
rules and with this branch's, the tracked passes that follow, the text read again with its wrappings off, and each view
that takes out a colour code or an invisible character. Each of them runs the same patterns over the same text, and a
scan is a function of the pattern and the text alone (what a pattern finds in `text` does not depend on who asks), so a
call now makes each whole-text scan once (kb.mask._first for one match, _scan for all of them) and gives it again where
it is asked for again. The readings of a call (_READINGS) keep the matches, weighed by what a match holds (_Matches), and
a scan that is not read to its end, or that finds more than one match for each _MATCH_SPACING characters, keeps nothing.

The tests hold the scans to the patterns' own results, the mask to what it is with the scans made each time they are asked
for (which is what main does), the memory to a bound of the text's size, and each of them to a break planted in the
shortcut, which makes one of them fail. tests/test_mask_speed.py holds every output to d512b6d's, as before."""
import base64
import contextlib
import gc
import json
import random
import re
import sys
import tracemalloc
import unittest
from unittest import mock

from kb import mask as head
from tests.test_mask_speed import code, in_pieces, short_floor
from tests.test_mask_split import a_call, outputs, split_texts

LEAST = head._CHECKED_FROM
WORDS = ("the build fails on the second run because the cache path is wrong so I moved it under the project folder and "
         "reran the tests which now pass except one timing test that depends on load we should check the config file "
         "first then the deploy script and the release notes before tagging update handler request session token_count "
         "render widget layout column header footer buffer stream filter reduce mapper loader parser lexer").split()
SECRET_COMMANDS = ["curl -s -H 'Authorization: Bearer {v}' https://api.example.test/v1/items?limit=20",
                   "docker login -u ci-bot -p {v} registry.example.test", "export API_TOKEN={v} && ./run.sh --verbose",
                   "psql postgres://app:{v}@db.example.test:5432/app -c 'select count(*) from items'",
                   "az login --service-principal -u 11111111-2222-3333-4444-555555555555 -p {v} --tenant example",
                   "mysql -h db.example.test -u app -p{v} app -e 'select 1'", "aws configure set aws_secret_access_key {v}"]
COMMANDS = ["git status --short", "git diff --stat", "rg -n 'TODO|FIXME' src tests", "sed -n '1,200p' src/app/main.py",
            "python3 -m pytest -q tests/test_store.py -x", "ls -la src", "cat package.json", "make test"]
TYPING = ['{"element": "Password textbox", "ref": "e23", "text": "%s", "submit": true}',
          '{"element": "Search box", "ref": "e24", "text": "pelican plan"}']
TYPING_CODE = ["await page.getByLabel('Password').fill('%s'); await page.click('button[type=submit]');",
               "await page.getByLabel('Search').fill('pelican plan');"]
COLOURS = ["\x1b[31m", "\x1b[32m", "\x1b[1;34m", "\x1b[36m"]


def agent_log(size, seed, kind="codex", commands=0.3, colour=0.3, typing=0.1):
    """About `size` characters of a made-up agent session as a source stores one: a JSON object on each line, every string
    JSON-escaped (line breaks as \\n, colour codes as \\u001b), a tool call's arguments a JSON string in the JSON, long runs
    of base64 for a reasoning's blob. `commands`, `colour`, `typing`: the share of the tool calls that run a command with a
    password in it, of the outputs that hold colour codes, and of the calls that type into a page."""
    r = random.Random(f"{kind}:{size}:{seed}:{commands}:{colour}:{typing}")
    words = lambda k: " ".join(r.choice(WORDS) for _ in range(k))
    token = lambda: "".join(r.choice("abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(r.randint(14, 28)))
    lines, total = [], 0
    while total < size:
        k, n = r.random(), len(lines)
        if k < 0.1:
            text = words(r.randint(8, 80))
            row = ({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": text}]}} if kind == "codex" else
                   {"type": "user", "message": {"role": "user", "content": text}})
        elif k < 0.16:
            blob = base64.b64encode(r.randbytes(r.randint(900, 5000))).decode()
            row = ({"type": "response_item", "payload": {"type": "reasoning", "encrypted_content": blob}} if kind == "codex"
                   else {"type": "assistant", "message": {"content": [{"type": "thinking", "signature": blob}]}})
        elif k < 0.6:
            if r.random() < typing:
                if r.random() < 0.5:
                    name, args = "mcp__browser__browser_type", r.choice(TYPING)
                else:
                    name, args = "exec_command", json.dumps({"cmd": "node -e \"(async()=>{ " + r.choice(TYPING_CODE)
                                                                     + " })()\""})
                args = args.replace("%s", token())
            elif r.random() < commands:
                name, args = "exec_command", json.dumps({"cmd": r.choice(SECRET_COMMANDS).format(v=token())})
            else:
                name, args = "exec_command", json.dumps({"cmd": r.choice(COMMANDS)})
            if kind == "codex":
                row = {"type": "response_item", "payload": {"type": "function_call", "name": name, "arguments": args,
                                                            "call_id": f"call_{n}"}}
            else:
                row = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"toolu_{n}", "name": name,
                                                                      "input": {"command": args}}]}}
        else:
            out = code(r.randint(300, 2_500), f"{seed}:{n}")
            if r.random() < colour:
                out = "\n".join(r.choice(COLOURS) + line + "\x1b[0m" if r.random() < 0.2 else line
                                for line in out.split("\n"))
            row = ({"type": "response_item", "payload": {"type": "function_call_output", "call_id": f"call_{n}", "output": out}}
                   if kind == "codex" else
                   {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"toolu_{n}",
                                                              "content": out}]}})
        lines.append(json.dumps(row))
        total += len(lines[-1]) + 1
    return "\n".join(lines)


def agent_logs(size=30_000):
    """Made-up sessions of both sources and of each mix of what slows a mask: nothing, commands, colour, typing."""
    for kind in ("codex", "claude"):
        for name, options in (("quiet", dict(commands=0, colour=0, typing=0)), ("commands", dict(commands=0.6, colour=0)),
                              ("colour", dict(commands=0.05, colour=0.8, typing=0)), ("typing", dict(commands=0, typing=0.6)),
                              ("mixed", dict(commands=0.3, colour=0.4, typing=0.2))):
            yield f"{kind} {name}", agent_log(size, name, kind, **options)


@contextlib.contextmanager
def scans_off():
    """Every scan made each time it is asked for, as main makes it."""
    with mock.patch.object(head, "_scan", lambda pattern, text: pattern.finditer(text)), \
            mock.patch.object(head, "_first", lambda pattern, text: pattern.search(text)):
        yield


def shown(matches):
    return [(m.span(), m.groups(), m.lastindex) for m in matches]


class Counting:
    """A pattern that counts the scans made with it."""

    def __init__(self, pattern):
        self.pattern, self.scans = pattern, 0

    def finditer(self, text, *bounds):
        self.scans += 1
        return self.pattern.finditer(text, *bounds)

    def search(self, text, *bounds):
        self.scans += 1
        return self.pattern.search(text, *bounds)


def text_with(r, size, spacing):
    """`size` characters, a word of letters and a number every `spacing` characters, so a pattern of one word finds one
    match for each `spacing` characters."""
    parts, n = [], 0
    while n < size:
        parts.append(f"w{r.randint(0, 9999)}" + " " * (spacing - 6 - len(str(r.randint(0, 9999)))))
        n += len(parts[-1])
    return "".join(parts)


class ScanTest(unittest.TestCase):
    """kb.mask._scan and _first give what the pattern's own finditer and search give, and make the scan once in a call."""

    def patterns(self):
        return [head._ANY_COMMAND, head._WRAPPED_BASE64, head._UNSEEN_ESCAPED, head._QUOTE_ESCAPE, head.INVISIBLE,
                head._INVISIBLE_RUN, head._CODE_TYPING_CALL, head._TYPING_NAME_AT, head._CONTINUATION,
                re.compile(r"x*"), re.compile(r"(?m)^(\w)(\w*)(?: (\d+))?"), re.compile(r"no such thing")]

    def texts(self):
        yield "empty-ish", "y" * LEAST
        for name, text in list(agent_logs(20_000))[::3]:
            yield name, text
        yield "coloured", "\x1b[31mred\x1b[0m pelican plan " * 900 + "​" * 30

    def test_the_matches_are_the_patterns_own_each_time_they_are_asked_for(self):
        checked = 0
        for where, text in self.texts():
            with a_call(text):
                for pattern in self.patterns():
                    want = shown(pattern.finditer(text))
                    for ask in range(3):
                        self.assertEqual(shown(head._scan(pattern, text)), want, (where, pattern.pattern[:40], ask))
                        found = head._first(pattern, text)
                        self.assertEqual(found and shown([found])[0], want[0] if want else None, (where, ask))
                    checked += 1
        self.assertGreater(checked, 40)

    def test_a_scan_is_made_once_in_a_call_and_again_in_the_next(self):
        text = text_with(random.Random("scan once"), 20_000, head._MATCH_SPACING + 8)
        pattern = Counting(re.compile(r"w\d+"))
        for calls in (1, 2):
            with a_call(text):
                for _ in range(4):
                    self.assertTrue(list(head._scan(pattern, text)))
                    self.assertTrue(head._first(pattern, text))
            self.assertEqual(pattern.scans, 2 * calls)  # one scan and one search in each call

    def test_a_scan_outside_a_call_or_of_a_short_text_is_made_each_time(self):
        pattern = Counting(re.compile(r"\w+"))
        long, short = code(20_000, "outside"), code(LEAST - 100, "short")
        list(head._scan(pattern, long))
        list(head._scan(pattern, long))
        self.assertEqual(pattern.scans, 2)
        with a_call(short):
            before_size = len(head._READINGS.get())
            list(head._scan(pattern, short))
            list(head._scan(pattern, short))
            head._first(pattern, short)
            head._first(pattern, short)
            self.assertEqual((pattern.scans, len(head._READINGS.get())), (6, before_size))

    def test_two_patterns_or_two_texts_do_not_share_an_entry(self):
        a, b = Counting(re.compile(r"w\d+")), Counting(re.compile(r"w\d+"))  # the same pattern, two objects
        text = text_with(random.Random("two"), 20_000, head._MATCH_SPACING + 8)
        other = "w1" + text[2:]  # as long, and a first match of its own
        with a_call(text):
            for pattern, source in ((a, text), (b, text), (a, other), (b, other), (a, text), (b, other)):
                list(head._scan(pattern, source))
                head._first(pattern, source)
        self.assertEqual((a.scans, b.scans), (4, 4))  # a scan and a search of each of its two texts

    def test_a_scan_not_read_to_its_end_keeps_nothing_and_the_next_is_whole(self):
        text = text_with(random.Random("partial"), 20_000, head._MATCH_SPACING + 8)
        pattern = re.compile(r"w\d+")
        with a_call(text):
            readings = head._READINGS.get()
            for m in head._scan(pattern, text):
                break
            head._scan(pattern, text)  # asked for, and not read at all
            self.assertEqual(len(readings), 0)
            self.assertEqual(shown(head._scan(pattern, text)), shown(pattern.finditer(text)))
            self.assertEqual(len(readings), 1)
            self.assertEqual(shown(head._scan(pattern, text)), shown(pattern.finditer(text)))

    def test_a_scan_that_finds_more_than_one_match_for_each_spacing_keeps_nothing(self):
        r = random.Random("spacing")
        pattern = re.compile(r"w\d+")
        for spacing, kept in ((head._MATCH_SPACING + 8, 1), (head._MATCH_SPACING // 2, 0), (20, 0)):
            text = text_with(r, 20_000, spacing)
            with a_call(text):
                readings = head._READINGS.get()
                self.assertEqual(shown(head._scan(pattern, text)), shown(pattern.finditer(text)))
                self.assertEqual(len(readings), kept, spacing)
                self.assertEqual(shown(head._scan(pattern, text)), shown(pattern.finditer(text)))

    def test_what_a_scan_keeps_is_weighed_by_what_its_matches_hold(self):
        text = text_with(random.Random("weigh"), 40_000, head._MATCH_SPACING + 8)
        pattern = re.compile(r"w\d+")
        with a_call(text):
            readings = head._READINGS.get()
            head._first(pattern, text)
            one = readings.size  # the text, once, and a match
            list(head._scan(pattern, text))
            count = len(list(pattern.finditer(text)))
            self.assertGreater(count, 100)
            self.assertGreaterEqual(readings.size - one, count * head._Matches.MATCH_BYTES + head._Matches.ENTRY_BYTES)
            self.assertLess(readings.size - one, count * (head._Matches.MATCH_BYTES + 8) + head._Matches.ENTRY_BYTES + 1_000)

    def test_a_match_holds_no_more_than_the_weight_counts(self):
        groups = max(p.groups for p in vars(head).values() if isinstance(p, re.Pattern))
        for n in range(groups + 1):
            match = re.compile("(a)" * n + "a").match("a" * (n + 1))
            self.assertLessEqual(sys.getsizeof(match) + 8, head._Matches.MATCH_BYTES, n)  # the match and its slot in the tuple

    def test_what_is_kept_is_held_to_the_bound_and_the_oldest_goes_first(self):
        text = text_with(random.Random("bound"), 40_000, head._MATCH_SPACING + 8)
        patterns = [re.compile(rf"w\d+|x{n}") for n in range(30)]  # each finds the same matches
        with a_call(text):
            readings = head._READINGS.get()
            list(head._scan(patterns[0], text))
            readings.limit = readings.size + int(2.5 * (readings.size - sys.getsizeof(text)))  # the text and 3 scans
            readings.clear()
            readings.weights.clear()
            readings.held.clear()
            readings.size = 0
            for n, pattern in enumerate(patterns):
                self.assertEqual(shown(head._scan(pattern, text)), shown(pattern.finditer(text)))
                self.assertTrue(readings.size <= readings.limit or len(readings) == 1, n)
            self.assertIn((head._ALL_MATCHES, id(patterns[-1]), text), readings)
            self.assertNotIn((head._ALL_MATCHES, id(patterns[0]), text), readings)
            self.assertLessEqual(len(readings), 4)
            self.assertEqual(shown(head._scan(patterns[0], text)), shown(patterns[0].finditer(text)))  # made again

    def test_an_entry_keeps_its_pattern_alive_so_no_other_pattern_has_its_id(self):
        text = code(20_000, "alive")
        with a_call(text):
            pattern = re.compile(r"(?m)^(\w+)")
            want = shown(pattern.finditer(text))
            head._scan(pattern, text), list(head._scan(pattern, text))
            key = (head._ALL_MATCHES, id(pattern), text)
            del pattern
            gc.collect()
            self.assertIs(type(head._READINGS.get()[key][0]), re.Pattern)
            for n in range(300):
                other = re.compile(rf"(?m)^(\w{{{n % 7 + 1}}})")
                self.assertEqual(shown(head._scan(other, text)), shown(other.finditer(text)), n)
        self.assertTrue(want)


class SameOutputTest(unittest.TestCase):
    """mask(), mask_with_origin() and `capped` are what they are with every scan made each time it is asked for."""

    def test_each_made_up_session_gives_the_output_it_gives_without_the_memo(self):
        checked = changed = 0
        for where, text in agent_logs(30_000):
            got = outputs(text)
            with scans_off():
                self.assertEqual(got, outputs(text), where)
            checked += 1
            changed += got[0] != text
        self.assertEqual(checked, 10)
        self.assertGreaterEqual(changed, 6)  # all but the sessions that hold nothing to mask

    def test_each_text_that_splits_a_secret_gives_the_output_it_gives_without_the_memo(self):
        checked = changed = 0
        for where, text in split_texts():
            got = outputs(text)
            with scans_off():
                self.assertEqual(got, outputs(text), where)
            checked += 1
            changed += got[0] != text
        self.assertGreater(checked, 60)
        self.assertGreater(changed, 40)

    def test_a_long_session_read_in_pieces_gives_the_output_it_gives_without_the_memo(self):
        with in_pieces(40_000, 12_000, 3_000):
            for kind, options in (("codex", dict(commands=0.4, colour=0.4)), ("claude", dict(commands=0.4, typing=0.3))):
                text = agent_log(100_000, "pieces", kind, **options)
                got = outputs(text)
                with scans_off():
                    self.assertEqual(got, outputs(text), kind)
                self.assertNotEqual(got[0], text)


def demand_and_supply(text):
    """(the scans a mask(text) asks for, the scans it makes) of the texts the memo applies to (_CHECKED_FROM characters or
    more): each call of _scan or _first, and each scan that is not answered from what the call kept."""
    counts = {"asked": 0, "made": 0}
    scan, first, scanned = head._scan, head._first, head._scanned
    made_first = head._Readings.__setitem__

    def asking(real):
        def ask(*args):
            counts["asked"] += len(args[1]) >= head._CHECKED_FROM  # a shorter text is never kept: it is not asked for here
            return real(*args)
        return ask

    def made(*args):
        counts["made"] += 1
        return scanned(*args)

    def storing(self, key, value):
        counts["made"] += key[0] is head._FIRST_MATCH
        return made_first(self, key, value)
    with mock.patch.object(head, "_scan", asking(scan)), mock.patch.object(head, "_first", asking(first)), \
            mock.patch.object(head, "_scanned", made), mock.patch.object(head._Readings, "__setitem__", storing):
        head._MEMO.kept.clear()
        head._MEMO.size = 0
        head.mask(text)
    return counts["asked"], counts["made"]


class ReadsLessTest(unittest.TestCase):
    def test_a_session_with_commands_is_scanned_for_a_part_of_what_it_asks_for(self):
        for kind in ("codex", "claude"):
            text = agent_log(60_000, "reads less", kind, commands=0.5, colour=0.4, typing=0.1)
            asked, made = demand_and_supply(text)
            self.assertGreater(asked, 300, kind)
            self.assertLess(made, 0.65 * asked, (kind, asked, made))

    def test_a_session_with_nothing_to_mask_is_scanned_less_too(self):
        text = agent_log(60_000, "quiet", "codex", commands=0, colour=0, typing=0)
        asked, made = demand_and_supply(text)
        self.assertLess(made, asked, (asked, made))


def peak(text):
    """The most memory, in bytes, that mask(text) holds at once (tracemalloc), with what it keeps for the next call cleared."""
    head._MEMO.kept.clear()
    head._MEMO.size = 0
    gc.collect()
    tracemalloc.start()
    try:
        head.mask(text)
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


class MemoryTest(unittest.TestCase):
    """A call's peak memory with the scans kept is no more than a bound of the text's size over what it is with every scan
    made each time (the bound of the readings, _Readings.PER_CHAR, is the one the rest of what a call keeps is held to; with
    its floor off a text of 100 KB stands for a long one). Both are measured on the same text one after the other."""
    BOUND = 16  # bytes for each character: the readings' own bound (PER_CHAR), over what a call holds without the memo

    def setUp(self):
        head.mask(agent_log(20_000, "warm", "codex", commands=0.4, colour=0.4))

    def excess(self, text, *plants):
        with contextlib.ExitStack() as stack:
            stack.enter_context(short_floor())
            for plant in plants:
                stack.enter_context(plant)
            ours = peak(text)
        with scans_off(), short_floor():
            was = peak(text)
        return (ours - was) / len(text)

    def dense(self, spacing, size=60_000):
        """A text of 60 KB with a zero-width space at the end of each line of `spacing` characters: a scan of any of the
        patterns that read them finds one match for each line, and there are many such scans of the one text."""
        r = random.Random("dense")
        parts, n = [], 0
        while n < size:
            parts.append(" ".join(r.choice(WORDS) for _ in range(30))[:spacing - 2] + "\u200b")
            n += len(parts[-1]) + 1
        return "\n".join(parts)

    def test_the_memo_adds_no_more_than_the_bound_of_the_text_s_size_on_each_session(self):
        for where, text in list(agent_logs(60_000))[::2]:
            with self.subTest(where):
                self.assertLess(self.excess(text), self.BOUND)

    def test_the_memo_adds_no_more_than_the_bound_on_texts_with_as_many_matches_as_it_keeps_and_more(self):
        for spacing in (head._MATCH_SPACING + 8, head._MATCH_SPACING // 2, 16):
            with self.subTest(spacing=spacing):
                self.assertLess(self.excess(self.dense(spacing)), self.BOUND)

    def test_a_memo_that_counts_nothing_for_what_it_keeps_and_keeps_all_is_found(self):
        free = mock.patch.multiple(head._Matches, MATCH_BYTES=0, ENTRY_BYTES=0)
        every = mock.patch.object(head, "_MATCH_SPACING", 1)
        self.assertGreater(self.excess(self.dense(16), free, every), self.BOUND)


class PlantedBreakTest(unittest.TestCase):
    """A shortcut broken where it is made makes a test above fail."""

    def check_scan(self, scan):
        """The checks of ScanTest that a scan taken out of the module must pass, with `scan` in the place of _scan."""
        r = random.Random("planted")
        text = text_with(r, 20_000, head._MATCH_SPACING + 8)
        other = text[::-1]  # as long as it, and matches of its own
        a, b, c = re.compile(r"w\d+"), re.compile(r"[a-z]\d"), re.compile(r"\d+")
        with a_call(text), mock.patch.object(head, "_scan", scan):
            for m in head._scan(c, text):  # read in part
                break
            if shown(head._scan(c, text)) != shown(c.finditer(text)):
                return False
            for pattern, source in ((a, text), (b, text), (a, other), (b, other), (a, text)):
                if shown(head._scan(pattern, source)) != shown(pattern.finditer(source)):
                    return False
            return True

    def broken_scan(self, key_of, partial=False):
        def scan(pattern, text):
            readings = head._READINGS.get()
            key = key_of(pattern, text)
            made = readings.get(key)
            if made is not None:
                return iter(made[1])

            def read():
                kept = []
                for m in pattern.finditer(text):
                    kept.append(m)
                    if partial:
                        readings[key] = (pattern, tuple(kept))  # kept before the scan is read to its end
                    yield m
                readings[key] = (pattern, tuple(kept))
            return read()
        return scan

    def test_the_planted_scan_that_is_right_passes(self):
        self.assertTrue(self.check_scan(self.broken_scan(lambda p, t: (head._ALL_MATCHES, id(p), t))))

    def test_a_scan_kept_for_the_text_and_not_the_pattern_is_found(self):
        self.assertFalse(self.check_scan(self.broken_scan(lambda p, t: (head._ALL_MATCHES, t))))

    def test_a_scan_kept_for_the_pattern_and_not_the_text_is_found(self):
        self.assertFalse(self.check_scan(self.broken_scan(lambda p, t: (head._ALL_MATCHES, id(p), len(t)))))

    def test_a_scan_kept_before_it_is_read_to_its_end_is_found(self):
        self.assertFalse(self.check_scan(self.broken_scan(lambda p, t: (head._ALL_MATCHES, id(p), t), partial=True)))

    def test_a_scan_kept_for_the_text_and_not_the_pattern_changes_the_mask_s_output(self):
        # In the mask itself, where a text is scanned with many patterns: it is not what the mask gives.
        text = agent_log(30_000, "planted", "codex", commands=0.6, colour=0.5)
        want = outputs(text)
        self.assertNotEqual(want[0], text)
        with scans_off():
            self.assertEqual(outputs(text), want)
        kept = {}
        with mock.patch.object(head, "_scan", lambda pattern, text: iter(kept.setdefault(text, tuple(pattern.finditer(text))))):
            try:
                got = outputs(text)
            except Exception:
                return  # a reading that fails on what it is given is found too
        self.assertNotEqual(got, want)
