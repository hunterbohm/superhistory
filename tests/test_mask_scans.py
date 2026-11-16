"""The scans of a long text, made once in a call, with no change in what is masked.

A long agent session is one text that mask() reads many times over: the readings of the text as written, with main's
rules and with this branch's, the tracked passes that follow, the text read again with its wrappings off, and each view
that takes out a colour code or an invisible character. Each of them runs the same patterns over the same text, and a
scan is a function of the pattern and the text alone (what a pattern finds in `text` does not depend on who asks), so a
call now makes each whole-text scan once (kb.mask._first for one match, _scan for all of them) and gives it again where
it is asked for again. The readings of a call (_READINGS) keep the matches in a pool of their own (scan_get, scan_put), held
to a bound of the text's size and weighed by what a match holds, so the readings are kept as they were; a scan that is not
read to its end, or that finds more than one match for each _MATCH_SPACING characters, keeps nothing.

The tests hold the scans to the patterns' own results, the mask to what it is with the scans made each time they are asked
for (which is what main does), the memory to a bound of the text's size, and each of them to a break planted in the
shortcut, which makes one of them fail. tests/test_mask_speed.py holds every output to d512b6d's, as before."""
import array
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


def weight_of(pool, matches):
    """What an entry of the pool with these matches weighs: the entry, each match, and a slot for each."""
    return pool.ENTRY_BYTES + sum(sys.getsizeof(m) + 8 for m in matches)


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
            before_size = len(head._READINGS.get().scans)
            list(head._scan(pattern, short))
            list(head._scan(pattern, short))
            head._first(pattern, short)
            head._first(pattern, short)
            self.assertEqual((pattern.scans, len(head._READINGS.get().scans)), (6, before_size))

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
            self.assertEqual(len(readings.scans), 0)
            self.assertEqual(shown(head._scan(pattern, text)), shown(pattern.finditer(text)))
            self.assertEqual(len(readings.scans), 1)
            self.assertEqual(shown(head._scan(pattern, text)), shown(pattern.finditer(text)))

    def test_a_scan_that_finds_more_than_one_match_for_each_spacing_keeps_nothing(self):
        r = random.Random("spacing")
        pattern = re.compile(r"w\d+")
        for spacing, kept in ((head._MATCH_SPACING + 8, 1), (head._MATCH_SPACING // 2, 0), (20, 0)):
            text = text_with(r, 20_000, spacing)
            with a_call(text):
                readings = head._READINGS.get()
                self.assertEqual(shown(head._scan(pattern, text)), shown(pattern.finditer(text)))
                self.assertEqual(len(readings.scans), kept, spacing)
                self.assertEqual(shown(head._scan(pattern, text)), shown(pattern.finditer(text)))

    def test_what_a_scan_keeps_is_weighed_by_what_its_matches_hold(self):
        text = text_with(random.Random("weigh"), 40_000, head._MATCH_SPACING + 8)
        pattern = re.compile(r"w(\d+)")
        with a_call(text):
            pool = head._READINGS.get()
            found = head._first(pattern, text)
            first = pool.ENTRY_BYTES + sys.getsizeof(found) + 8
            self.assertEqual(pool.scan_size, sys.getsizeof(text) + first)  # the text once
            matches = list(head._scan(pattern, text))
            self.assertGreater(len(matches), 100)
            self.assertEqual(pool.scan_size, sys.getsizeof(text) + first + pool.ENTRY_BYTES
                             + sum(sys.getsizeof(m) + 8 for m in matches))
            self.assertEqual(pool.size, 0)  # none of it is a reading's

    def test_the_pool_counts_at_least_what_the_scans_it_keeps_hold(self):
        # What the memory of a process holds more for the scans (tracemalloc), not their texts, is no more than they weigh,
        # for no match, one and many, of a pattern of no group and of ten.
        text = text_with(random.Random("real"), 40_000, head._MATCH_SPACING + 8)
        for groups in (0, 3, 10):
            pattern = re.compile("(?:" + "(a)" * groups + ")?w\\d+|(?!)")
            for count in (0, 1, 150):
                with self.subTest(groups=groups, count=count), a_call(text):
                    pool = head._READINGS.get()
                    pool.scan_limit = 10 ** 12
                    wanted = tuple(pattern.finditer(text))[:count]
                    gc.collect()
                    tracemalloc.start()
                    try:
                        before = tracemalloc.get_traced_memory()[0]
                        for n in range(100):
                            pool.scan_put((head._ALL_MATCHES, n, text), pattern, tuple(pattern.finditer(text)) if count else ())
                        held = tracemalloc.get_traced_memory()[0] - before
                    finally:
                        tracemalloc.stop()
                    counted = pool.scan_size - sys.getsizeof(text)
                    # (held has the test's own bookkeeping too, the tables of the dict: under 3 KB in 4 to 9 MB)
                    self.assertLessEqual(held, counted + 4096)
                    self.assertTrue(wanted or not count)

    def test_what_is_kept_is_held_to_the_bound_and_the_longest_unused_goes_first(self):
        text = text_with(random.Random("bound"), 40_000, head._MATCH_SPACING + 8)
        patterns = [re.compile(rf"w\d+|x{n}") for n in range(30)]  # each finds the same matches
        with a_call(text):
            pool = head._READINGS.get()
            list(head._scan(patterns[0], text))
            one = pool.scan_size - sys.getsizeof(text)  # an entry
            pool.scan_limit = sys.getsizeof(text) + int(3.5 * one)  # the text and 3 entries
            key = lambda n: (head._ALL_MATCHES, id(patterns[n]), text)
            for n, pattern in enumerate(patterns[1:4], 1):
                list(head._scan(pattern, text))
            self.assertEqual(len(pool.scans), 3)  # 0 has gone: one more than the bound
            self.assertNotIn(key(0), pool.scans)
            list(head._scan(patterns[1], text))  # 1 is used: it goes last
            list(head._scan(patterns[4], text))
            self.assertEqual(list(pool.scans), [key(3), key(1), key(4)][-len(pool.scans):])
            self.assertNotIn(key(2), pool.scans)  # the longest unused
            for n, pattern in enumerate(patterns):
                self.assertEqual(shown(head._scan(pattern, text)), shown(pattern.finditer(text)))
                self.assertLessEqual(pool.scan_size, pool.scan_limit, n)
            self.assertIn(key(29), pool.scans)
            self.assertEqual(pool.size, 0)

    def test_a_scan_that_alone_weighs_more_than_the_bound_is_not_kept_and_its_text_is_not_held(self):
        text = text_with(random.Random("alone"), 40_000, head._MATCH_SPACING + 8)
        pattern = re.compile(r"w\d+")
        with a_call(text):
            pool = head._READINGS.get()
            pool.scan_limit = sys.getsizeof(text)
            self.assertEqual(shown(head._scan(pattern, text)), shown(pattern.finditer(text)))
            self.assertEqual((len(pool.scans), pool.scan_texts, pool.scan_size), (0, {}, 0))
            self.assertEqual(shown(head._scan(pattern, text)), shown(pattern.finditer(text)))

    def test_a_text_is_counted_once_for_the_scans_that_hold_it_and_freed_with_the_last(self):
        text = text_with(random.Random("texts"), 20_000, head._MATCH_SPACING + 8)
        with a_call(text):
            pool = head._READINGS.get()
            for n in range(5):
                head._first(re.compile(rf"w\d+|x{n}"), text)
            self.assertEqual(len(pool.scans), 5)
            self.assertEqual(pool.scan_size, sys.getsizeof(text) + sum(weight_of(pool, found) for _, found, _ in pool.scans.values()))
            self.assertEqual(pool.scan_texts[id(text)][0], 5)
            pool.scan_limit = pool.scan_size - 1
            head._first(re.compile(r"w\d+|y"), text)  # one more: the two oldest of six go
            self.assertEqual(pool.scan_texts[id(text)][0], 4)
            pool.scan_limit = 0
            head._first(re.compile(r"w\d+|z"), text)
            self.assertEqual((pool.scans, pool.scan_texts, pool.scan_size), ({}, {}, 0))

    def test_an_equal_text_that_is_another_object_reads_the_entry_and_is_not_held_again(self):
        text = text_with(random.Random("equal"), 20_000, head._MATCH_SPACING + 8)
        again = "".join([text[:10_000], text[10_000:]])
        self.assertIsNot(text, again)
        self.assertEqual(text, again)
        pattern = Counting(re.compile(r"w\d+"))
        with a_call(text):
            pool = head._READINGS.get()
            want = shown(pattern.pattern.finditer(text))
            for source in (text, again, again, text):
                self.assertEqual(shown(head._scan(pattern, source)), want)
            self.assertEqual(pattern.scans, 1)
            self.assertEqual(pool.scan_texts.keys(), {id(text)})  # the text the entry was put with, once
            (key,) = pool.scans
            self.assertIs(key[2], text)
            pool.scan_limit = 0
            head._first(pattern, again)  # the pool is over its bound: all go, the text with them
            self.assertEqual((pool.scans, pool.scan_texts, pool.scan_size), ({}, {}, 0))

    def test_two_scans_of_one_pattern_and_text_read_side_by_side_are_one_entry(self):
        text = text_with(random.Random("side"), 20_000, head._MATCH_SPACING + 8)
        pattern = re.compile(r"w\d+")
        with a_call(text):
            pool = head._READINGS.get()
            one, two = head._scan(pattern, text), head._scan(pattern, text)
            got = [(a, b) for a, b in zip(one, two)]  # read together until the first is at its end
            self.assertEqual(len(pool.scans), 1)  # the first has kept its matches: the second is not at its end yet
            self.assertEqual(shown(m for _, m in got), shown(pattern.finditer(text)))
            self.assertEqual(list(two), [])  # the second is at its end, and finds an entry kept for the same key
            self.assertEqual(pool.scan_texts[id(text)][0], 1)  # held once: not one entry and another for the same key
            self.assertEqual(shown(m for m, _ in got), shown(pattern.finditer(text)))
            self.assertEqual(len(pool.scans), 1)
            self.assertEqual(pool.scan_size, sys.getsizeof(text) + weight_of(pool, [m for m, _ in got]))
            pool.scan_limit = 0
            head._first(pattern, text)
            self.assertEqual((pool.scans, pool.scan_texts, pool.scan_size), ({}, {}, 0))

    def test_a_text_a_reading_holds_is_not_counted_for_the_scans_until_the_reading_is_dropped(self):
        text = text_with(random.Random("held"), 20_000, head._MATCH_SPACING + 8)
        readings = head._Readings(0)
        readings[(text, 0)] = (None, "masked")  # a reading of the text
        for n in range(2):
            head._READINGS.set(readings)
            try:
                head._first(re.compile(rf"w\d+|x{n}"), text)
            finally:
                head._READINGS.set(None)
        entries = sum(weight_of(readings, found) for _, found, _ in readings.scans.values())
        self.assertEqual(readings.scan_size, entries)  # the matches and the entries: the text is the reading's
        self.assertEqual(readings.scan_texts[id(text)][2:], [False])
        readings.scan_limit = entries + 100
        readings.limit = 100
        readings[(text[::-1], 1)] = (None, "another")  # over the readings' bound: the first reading goes
        self.assertNotIn((text, 0), readings)
        # the text is the scans' now: it and the entries weigh more than the pool's bound, so they go
        self.assertEqual((readings.scans, readings.scan_texts, readings.scan_size), ({}, {}, 0))

    def test_a_text_without_the_runs_of_a_pattern_is_what_the_pattern_s_own_sub_gives(self):
        # _dropped(runs, text) is runs.sub("", text) read from the scan: outside a call, in one, short and long, and
        # again from the entry the first made
        r = random.Random("dropped")
        for runs in (head._INVISIBLE_RUN, re.compile(r"\s+"), re.compile(r"[0-9]{2,}"), re.compile(r"no such thing")):
            for size in (200, 20_000):
                for kind in range(3):
                    text = "".join(r.choice(["a", "b", " ", "12", "\u200b", "\x1b[0m", "\n", "x" * 7]) for _ in range(size // 3))
                    if kind == 1:
                        text = text + "\u200b\u200b"  # a run at its end
                    elif kind == 2:
                        text = "\u200b" + text  # and at its start
                    want = runs.sub("", text)
                    self.assertEqual(head._dropped(runs, text), want, (runs.pattern[:20], size, kind))
                    with a_call(text):
                        for ask in range(2):
                            self.assertEqual(head._dropped(runs, text), want, (runs.pattern[:20], size, kind, ask))

    def test_an_entry_keeps_its_pattern_alive_so_no_other_pattern_has_its_id(self):
        text = code(20_000, "alive")
        with a_call(text):
            pattern = re.compile(r"(?m)^(\w+)")
            want = shown(pattern.finditer(text))
            head._scan(pattern, text), list(head._scan(pattern, text))
            key = (head._ALL_MATCHES, id(pattern), text)
            del pattern
            gc.collect()
            self.assertIs(type(head._READINGS.get().scans[key][0]), re.Pattern)
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
                text = agent_log(60_000, "pieces", kind, **options)
                got = outputs(text)
                with scans_off():
                    self.assertEqual(got, outputs(text), kind)
                self.assertNotEqual(got[0], text)


class ReadingsUnchangedTest(unittest.TestCase):
    """The scans have a pool of their own (kb.mask._Readings.scan_put), so the readings of a call are kept as they were: the
    same ones, dropped at the same time. Kept in the readings' bound, at 16 bytes for each character, the scans of a long
    text (up to 3 bytes for each character and for each pattern) pushed out the readings that are worth a pass, and a text
    of 1 MB took longer than it did without them."""

    def entry(self, r, size, n):
        text = "".join(r.choice("abcdefgh ") for _ in range(size))
        return (text, n, r.random()), (None, text[::-1], array.array("i", range(size // 16)))

    def test_the_readings_kept_are_the_ones_kept_without_the_scans(self):
        r = random.Random("unchanged")
        text = text_with(r, 20_000, head._MATCH_SPACING + 8)
        plain, mixed = head._Readings(0), head._Readings(0)
        plain.limit = mixed.limit = 120_000
        pattern = re.compile(r"w\d+")
        matches = tuple(pattern.finditer(text))
        for n in range(80):
            key, value = self.entry(r, r.choice((300, 2_000, 9_000, 20_000)), n)
            plain[key] = value
            mixed[key] = value
            mixed.scan_put((head._ALL_MATCHES, n, text), pattern, matches)
            mixed.scan_put((head._FIRST_MATCH, n, text), pattern, matches[:1])
            self.assertEqual(list(plain), list(mixed), n)
            self.assertEqual((plain.size, plain.held), (mixed.size, mixed.held), n)
        self.assertLess(len(plain), 80)  # some were dropped

    def stores(self, text, readings=head._Readings):
        """The readings a mask(text) makes (each is a store in the readings), with the floor of the bound off: a bound that
        the call fills, so a reading is dropped and made again."""
        made = [0]

        class Counting(readings):
            def __setitem__(self, key, value):
                made[0] += 1
                super().__setitem__(key, value)
        head._MEMO.kept.clear()
        head._MEMO.size = 0
        head.mask(text)  # what a first call of a text keeps in the module for the next (compiled patterns) is not counted
        with short_floor(), mock.patch.object(head, "_Readings", Counting):
            head._MEMO.kept.clear()
            head._MEMO.size = 0
            head.mask(text)
        return made[0]

    def test_a_call_makes_the_readings_it_makes_without_the_scans_at_a_bound_it_fills_and_at_one_it_does_not(self):
        for kind, options in (("codex", dict(commands=0.5, colour=0.4)), ("claude", dict(commands=0.5, typing=0.2))):
            text = agent_log(40_000, "unchanged", kind, **options)
            for per_char in (16, 160):
                with mock.patch.object(head._Readings, "PER_CHAR", per_char):
                    with scans_off():
                        without = self.stores(text)
                    self.assertEqual(self.stores(text), without, (kind, per_char))
                    self.assertGreater(without, 100)

    def test_scans_kept_in_the_readings_are_found(self):
        class Shared(head._Readings):
            def scan_get(self, key):
                return self.get(key)

            def scan_put(self, key, pattern, matches):
                made = self[key] = (pattern, matches, 0)
                return made
        text = agent_log(40_000, "unchanged", "codex", commands=0.5, colour=0.4)
        with scans_off():
            without = self.stores(text)
        self.assertNotEqual(self.stores(text, Shared), without)


def demand_and_supply(text):
    """(the scans a mask(text) asks for, the scans it makes) of the texts the memo applies to (_CHECKED_FROM characters or
    more): each scan asked for of the call's scans (scan_get), and each that is not answered from what it kept."""
    counts = {"asked": 0, "made": 0}
    real = head._Readings.scan_get

    def get(self, key):
        made = real(self, key)
        counts["asked"] += 1
        counts["made"] += made is None
        return made
    with mock.patch.object(head._Readings, "scan_get", get):
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


def peak(text, runs=3):
    """The least over `runs` calls of the most memory, in bytes, that mask(text) holds at once (tracemalloc), with what it
    keeps for the next call cleared. A peak moves by up to 9 bytes for each character from one call to the next (the cyclic
    collector runs at other times, and garbage is held until it does); the lowest of three is the peak without it."""
    lowest = None
    for _ in range(runs):
        head._MEMO.kept.clear()
        head._MEMO.size = 0
        gc.collect()
        tracemalloc.start()
        try:
            head.mask(text)
            now = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        lowest = now if lowest is None else min(lowest, now)
    return lowest


class MemoryTest(unittest.TestCase):
    """What a call's pool holds is no more than its bound (_Readings.SCAN_PER_CHAR bytes for each character, at least
    SCAN_LEAST), by what it counts (the most it counts after each scan it keeps) and by what the process holds more for it
    (tracemalloc on the same text with the scans kept and with every scan made each time, one after the other: measured
    8 to 11 bytes for each character on the sessions and 1 to 5 on the dense texts, over a bound of 16). With the floor off
    a text of 40 KB stands for a long one."""
    BOUND = 16  # bytes for each character: what the pool may count, and what a call may hold more in real memory
    SIZE = 40_000
    SLOWEST = ("codex mixed", "claude commands", "claude typing")  # the sessions that hold most (8 to 11 bytes for each character)

    def setUp(self):
        head.mask(agent_log(20_000, "warm", "codex", commands=0.4, colour=0.4))

    def counted(self, text, *plants):
        """(the most the pool counts after a scan is kept in mask(text), its bound there) with the floor off."""
        seen = []
        real = head._Readings.scan_put

        def put(pool, key, pattern, matches):
            made = real(pool, key, pattern, matches)
            seen.append((pool.scan_size, pool.scan_limit))
            return made
        with contextlib.ExitStack() as stack:
            stack.enter_context(short_floor())
            stack.enter_context(mock.patch.object(head._Readings, "SCAN_LEAST", 0))
            for plant in plants:
                stack.enter_context(plant)
            stack.enter_context(mock.patch.object(head._Readings, "scan_put", put))
            head._MEMO.kept.clear()
            head._MEMO.size = 0
            head.mask(text)
        return max(size for size, _ in seen), max(limit for _, limit in seen)

    def excess(self, text, *plants):
        with contextlib.ExitStack() as stack:
            stack.enter_context(short_floor())
            stack.enter_context(mock.patch.object(head._Readings, "SCAN_LEAST", 0))
            for plant in plants:
                stack.enter_context(plant)
            ours = peak(text)
        with scans_off(), short_floor():
            was = peak(text)
        return (ours - was) / len(text)

    def dense(self, spacing, size=40_000):
        """A text of 60 KB with a zero-width space at the end of each line of `spacing` characters: a scan of any of the
        patterns that read them finds one match for each line, and there are many such scans of the one text."""
        r = random.Random("dense")
        parts, n = [], 0
        while n < size:
            parts.append(" ".join(r.choice(WORDS) for _ in range(30))[:spacing - 2] + "\u200b")
            n += len(parts[-1]) + 1
        return "\n".join(parts)

    def test_the_bound_is_the_one_this_test_holds_it_to(self):
        self.assertLessEqual(head._Readings.SCAN_PER_CHAR, self.BOUND)

    def test_the_pool_counts_no_more_than_its_bound_on_each_session_and_fills_it_on_some(self):
        filled = 0
        for where, text in list(agent_logs(self.SIZE))[::2]:
            with self.subTest(where):
                most, limit = self.counted(text)
                self.assertEqual(limit, head._Readings.SCAN_PER_CHAR * len(text))
                self.assertLessEqual(most, limit)
                filled += most > 0.9 * limit
        self.assertGreater(filled, 0)

    def test_the_pool_counts_no_more_than_its_bound_on_texts_with_as_many_matches_as_it_keeps_and_more(self):
        for spacing in (head._MATCH_SPACING + 8, head._MATCH_SPACING // 2, 16):
            with self.subTest(spacing=spacing):
                text = self.dense(spacing)
                most, limit = self.counted(text)
                self.assertLessEqual(most, limit)

    def test_the_memo_adds_no_more_than_the_bound_in_real_memory_on_the_sessions_that_hold_most(self):
        for where, text in agent_logs(self.SIZE):
            if where in self.SLOWEST:
                with self.subTest(where):
                    self.assertLess(self.excess(text), self.BOUND)

    def test_the_memo_adds_no_more_than_the_bound_in_real_memory_on_texts_with_as_many_matches_as_it_keeps_and_more(self):
        for spacing in (head._MATCH_SPACING + 8, 16):
            with self.subTest(spacing=spacing):
                self.assertLess(self.excess(self.dense(spacing)), self.BOUND)

    def test_a_memo_with_no_bound_is_found(self):
        free = mock.patch.multiple(head._Readings, SCAN_PER_CHAR=10 ** 9, SCAN_LEAST=10 ** 12)
        text = agent_log(self.SIZE, "free", "claude", commands=0.5, colour=0.4, typing=0.1)
        most, limit = self.counted(text, free)
        self.assertGreater(most, self.BOUND * len(text))

    def test_a_memo_with_no_bound_that_keeps_all_is_found_in_real_memory(self):
        free = mock.patch.multiple(head._Readings, SCAN_PER_CHAR=10 ** 9, SCAN_LEAST=10 ** 12)
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
