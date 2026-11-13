"""This branch's mask held to main's mask as it is today, frozen byte for byte in tests/fixtures/main_mask_68506d9.py:
main's kb/mask.py at 68506d9, where #29 merged, whose git blob, 774a4a2, a test here checks, so it is never edited.
tests/test_mask_against_main.py holds the mask to main at 3be191e, from before #27 and #29; this holds it to main now.
Every character today's main hides, this branch hides: over the same texts as that comparison (SUPERHISTORY_DIFF_CASES of each
kind, each as written and without its invisible characters), the shapes this branch's reviews found shown that main
hides, and texts generated around them. Positions are exact: each mask reports where every character of its result
came from (mask_with_origin).

This branch (#34) reads a key's rest more narrowly than main, by design and with the coordinator's exception for it:
past a key's rest, ordinary text (a line of words, a JSON object's next field) and base64 that decodes whole as public
material stay, which main's reading takes (tests/test_mask_against_main.py's key_rest_changes). A difference from
today's main counts as by design only where today's main's reading of a key's rest, swapped in, explains it, no run
of base64 past public material shows four characters or more, and each run of text shown so, masked alone by today's
main, comes back unchanged, so no password or token today's main would hide among those words shows (by_design).
Anything else fails."""
import hashlib
import importlib.util
import json
import os
import random
import re
import unittest

from kb import mask as head
from kb.mask import INVISIBLE
from tests.test_mask_against_main import CASES, corpus, runs

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "main_mask_68506d9.py")
BLOB = "774a4a260caa9a6ddc166adf7336f45c50392c50"  # git rev-parse 68506d9:kb/mask.py
PASSWORD = "Zq7vRt2LmX9k"


def _main():
    spec = importlib.util.spec_from_file_location("main_mask_68506d9", FIXTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


main = _main()

# The sixteenth review's shapes: a command's name in a container's field, which this branch read whole and masked
# first, and the command's option after the container, which main's command readings read across it.
HOLDERS = (lambda key, name: str({key.encode(): name.encode()}), lambda key, name: str({key: name}),
           lambda key, name: json.dumps({key: name}), lambda key, name: str({key: [name, "x"]}),
           lambda key, name: json.dumps(json.dumps({key: name}))[1:-1], lambda key, name: str([name]),
           lambda key, name: str((key, name)))
OPTIONS = (("keytool", " -storepass "), ("jarsigner", " -keypass "), ("mongosh", " -p "), ("twine", " -p "),
           ("aws configure set", " aws_secret_access_key "), ("az login", " -p "))
MORE_OPTIONS = (("curl", " -u admin:"), ("mysql", " -p"), ("sshpass", " -p "), ("redis-cli", " -a "),
                ("docker login", " -p "), ("mongodump", " -p "))
KEYS = ("password", "pw", "cmd", "tool", "secret", "name", "token")


def found():
    """The sixteenth review's 36 fixed shapes, and 1,000 generated around them."""
    for n, holder in enumerate((HOLDERS[0], HOLDERS[1], HOLDERS[2], None, HOLDERS[3], HOLDERS[4])):
        for name, option in OPTIONS:
            held = holder("password", name) if holder else json.dumps({"token": name})
            yield f"review 16 shape {n} {name}", held + option + PASSWORD
    r = random.Random("today:16")
    for n in range(1000):
        name, option = r.choice(OPTIONS + MORE_OPTIONS)
        between = r.choice(("", "", ":", "\\n", " |")) if r.random() < 0.3 else ""
        yield f"review 16 generated {n}", r.choice(HOLDERS)(r.choice(KEYS), name) + between + option + PASSWORD


def hidden(module, text):
    """The positions of `text` that `module`'s mask hides."""
    _, origin = module.mask_with_origin(text)
    return set(range(len(text))) - {c for c in origin if c >= 0}


_BASE64_RUN = re.compile(r"(?<![A-Za-z0-9+/])(?=[A-Za-z0-9+/]*[A-Z])(?=[A-Za-z0-9+/]*[a-z])[A-Za-z0-9+/]{16,}={0,2}")


def by_design(text, shown):
    """Of `shown`, positions this branch shows that today's main hides, those it shows by design (the module's
    docstring): where today's main's reading of a key's rest, swapped in for this branch's, hides them too; but none of
    a run of base64 past public material of which four characters or more show, nor of a run of them that today's
    main, masking it alone, changes."""
    saved, head._key_rest_edits = head._key_rest_edits, main._key_rest_edits
    try:
        explained = set(shown) & hidden(head, text)
    finally:
        head._key_rest_edits = saved
    public = set()
    for block in head._WRAPPED_BASE64.finditer(text):
        decoded = head._base64_decoded(re.sub(r"[^A-Za-z0-9+/=]", "", head._KEY_ESCAPE.sub("", block.group())), 0, None)
        if decoded and head._public_material(decoded):
            public.update(range(block.start(), block.end()))
    for m in _BASE64_RUN.finditer(text):
        seen = explained.intersection(range(m.start(), m.end())) - public
        if len(seen) >= 4:
            explained -= seen
    for start, end in runs(sorted(explained)):
        if main.mask(text[start:end]) != text[start:end]:
            explained -= set(range(start, end))
    return explained


def texts():
    for where, text in corpus():
        yield where, text
        bare = INVISIBLE.sub("", text)
        if bare != text:
            yield f"{where}, its invisible characters removed", bare
    yield from found()


class AgainstTodaysMainTest(unittest.TestCase):
    def test_the_fixture_is_mains_mask_byte_for_byte(self):
        with open(FIXTURE, "rb") as f:
            data = f.read()
        self.assertEqual(hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(), BLOB)

    def test_every_character_todays_main_hides_stays_hidden(self):
        problems, checked = [], 0
        for where, text in texts():
            shown = hidden(main, text) - hidden(head, text)
            shown = sorted(shown - by_design(text, shown))
            if shown:
                problems.append(f"{where}: shows what main hides at {runs(shown)[:3]}: {head.mask(text)!r} against "
                                f"main's {main.mask(text)!r} (text {text!r})")
            checked += 1
        self.assertGreater(checked, CASES * 8)
        self.assertEqual(problems[:5], [], f"{len(problems)} of {checked} texts show what today's main hides")


class ByDesignTest(unittest.TestCase):
    """The exception for this branch's reading of a key's rest holds only ordinary text: a key's rest shown, or a
    secret today's main would hide among the words after it, still fails."""

    REST = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7walrusQ2xVb9mN4pL6tR8sZ1uY3wE5gH7jK0"

    def kept(self, words):
        text = f"[masked]\n{self.REST}\n{words}\nnext steps"
        shown = hidden(main, text)  # as if this branch showed all that today's main hides
        return text, by_design(text, shown)

    def test_ordinary_words_after_a_keys_rest_are_by_design(self):
        text, kept = self.kept("today plan notes deploy")
        at = text.index("today")
        self.assertTrue(set(range(at, at + len("today plan notes deploy"))) <= kept)

    def test_a_keys_rest_shown_still_fails(self):
        text, kept = self.kept("today plan notes deploy")
        at = text.index(self.REST)
        self.assertFalse(kept & set(range(at, at + len(self.REST))))

    def test_a_password_among_the_words_after_a_keys_rest_still_fails(self):
        text, kept = self.kept("today plan password=Zq7vRt2LmX9k notes deploy")
        at = text.index("Zq7vRt2LmX9k")
        self.assertFalse(kept & set(range(at, at + 12)))


if __name__ == "__main__":
    unittest.main()
