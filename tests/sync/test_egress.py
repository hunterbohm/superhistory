"""Egress canary harness. Records are built from templates by walking their shapes: every string leaf gets a canary of
every secret shape, every object gets a canary as a new key, and every list of strings gets one cut across two of its
items. The records go into the store, unmasked as an older kb or a bug in one would leave them, into the outbox, and
into a pack an older client signed. None of their canaries, or their first or last 12 characters, may reach any byte
that Sync, a snapshot or sign-in sends, or the terminal. Tokens split by an invisible character must not go out whole
either."""
import copy
import itertools
import json
import os
import random
import secrets
import shutil
import string
import tempfile
import time
import unicodedata
import unittest

from kb import ids, mask as kbmask
from kb.paths import Paths
from kb.sync import egress, keys, pack, records, state
from kb.sync.client import SyncClient

from .egress_history import EARLIER, at_922fb6b
from .network import Network, run_cli
from .support import put_record

ALNUM = string.ascii_letters + string.digits
INVISIBLE = ("​", "‌", "‍", "⁠", "­", "﻿")


# Every canary comes from this generator, seeded per run, so a failing seed can be run again with EGRESS_SEED=<seed>.
RNG = random.Random()


def pick(chars, n):
    return "".join(RNG.choice(chars) for _ in range(n))


def code(head, nbytes, nchars):
    return keys._show(head, keys._encode(bytes(RNG.getrandbits(8) for _ in range(nbytes)), nchars))


SHAPES = (
    lambda: "sk-ant-api03-" + pick(ALNUM, 40),
    lambda: "ghp_" + pick(string.ascii_letters, 36),
    lambda: "AKIA" + pick(string.ascii_uppercase + string.digits, 16),
    lambda: "password=Pw7" + pick(ALNUM, 13),
    lambda: "xoxb-" + pick(string.digits, 12) + "-" + pick(ALNUM, 24),
    lambda: "Bearer " + pick(ALNUM, 32),
    lambda: code("VRC1", 20, 32),
    lambda: code("VP1", 16, 26),
    lambda: "sk-ant-api03-" + pick(ALNUM, 6) + "VP1" + pick(ALNUM, 31),  # a key holding a run shaped like a code
)


def templates(own, n):
    """An item, its summary and a mark on it, under the id `<own>:v<n>`, with every field the wire carries."""
    key = f"{own}:v{n}"
    return [
        records.Record("item", "notes", key, 1, {"ts": "2026-10-01", "title": "Title", "rows": ["first chunk ", "second chunk"],
                                                 "meta": {"hash": "h", "path": "/notes/x.md", "nested": {"list": ["a", "b"], "key": "v"}}}),
        records.Record("summary", "notes", key, 1, {"of_hash": "h", "rows": [{"kind": "atom", "ts": "2026-10-01", "title": "Fact",
                                                                             "content": "a fact", "metadata": {"of_hash": "h", "note": "n"}}]}),
        records.Record("supersede", "notes", key, 1, {"by": own, "new": ["notes", f"{own}:other"], "at": "2026-10-05"}),
    ]


def plantings(value, path=()):
    """(path, how) for every place in a JSON shape a canary can go: each string leaf and each object as a new key. An
    item's rows are one text cut into pieces, so a canary also goes across the cut between two of them."""
    if isinstance(value, str):
        yield path, "leaf"
    elif isinstance(value, dict):
        yield path, "key"
        for k, v in value.items():
            yield from plantings(v, path + (k,))
    elif isinstance(value, list):
        if path[-1:] == ("rows",) and len(value) > 1 and all(isinstance(v, str) for v in value):
            yield path, "cut"
        for i, v in enumerate(value):
            yield from plantings(v, path + (i,))


def planted(value, path, how, canary):
    value = copy.deepcopy(value)
    """`value` with `canary` at `path`, set off by spaces the way a secret sits in text."""
    if not path:
        return value + f" {canary} " if how == "leaf" else {**value, canary: "x"}
    at = value
    for step in path[:-1]:
        at = at[step]
    if how == "leaf":
        at[path[-1]] = at[path[-1]] + f" {canary} "
    elif how == "key":
        at[path[-1]] = {**at[path[-1]], canary: "a value under a planted key"}
    else:
        items = at[path[-1]]
        items[0], items[1] = items[0] + " " + canary[:8], canary[8:] + " " + items[1]
    return value


# Seed 183 grows, by chance, a key holding a run shaped like a pairing code, which masking codes before kb's mask once
# broke apart; a run before seeds existed found it.
SEEDS = (183, 0, 1, 2, 3)


class Canaries:
    def __init__(self):
        self.planted = {}

    def add(self, where, value):
        self.planted[where] = value
        return value

    def found(self, where, data):
        """(field, path, piece) for every canary in `data`. Shapes with a fixed start, such as sk-ant-api03, share their
        first 12 characters, so one leak of that start names every such field."""
        return [(field, where, piece) for field, value in self.planted.items()
                for piece in (value, value[:12], value[-12:]) if piece.encode() in data]


class EgressTest(unittest.TestCase):
    def fresh(self, seed):
        RNG.seed(seed)
        self.tmp = tempfile.mkdtemp(prefix="superhistory-egress-")
        self.home = os.path.join(self.tmp, "a")
        self.net, self.c, self.outputs = Network(), Canaries(), []
        self.travelling = set()

    def cli(self, *argv, stdin="", home=None):
        code, out = run_cli(home or self.home, *argv, stdin=stdin)
        self.outputs.append((" ".join(argv), out))
        return code, out

    def variants(self, own):
        """Every template with a canary of every shape in every place it can go, as records to send."""
        n, out = 0, []
        for template in templates(own, 0):
            wire = {"source": template.source, "source_id": template.source_id, "body": template.body}
            for path, how in plantings(wire):
                if not path:
                    continue  # the wire's own top level holds no field a record keeps
                for shape in SHAPES:
                    n += 1
                    canary = self.c.add(f"{template.kind} {'/'.join(map(str, path)) or 'record'} {how} {n}", shape())
                    fields = planted({**wire, "source_id": f"{own}:v{n}"}, path, how, canary)
                    rec = records.Record(template.kind, fields["source"], fields["source_id"], n, fields["body"])
                    out.append(rec)
                    stays = path in (("source",), ("source_id",), ("body", "by"), ("body", "new", 0), ("body", "new", 1)) \
                        or path[-1:] == ("kind",)
                    if not stays:
                        self.travelling.add(rec.source_id)
        for ch in INVISIBLE:
            for shape in SHAPES:
                n += 1
                token = self.c.add(f"token split by U+{ord(ch):04X} {n}", shape())
                out.append(records.Record("item", "notes", f"{own}:v{n}", n, {
                    "ts": "2026-10-01", "title": "Split", "meta": {"hash": "h"}, "rows": [f"a {token[:12]}{ch}{token[12:]} b"]}))
                self.travelling.add(f"{own}:v{n}")
        return out

    def plant(self, recs):
        """Each record in the store, as it would be read, and staged in the outbox when it is one a client could stage."""
        client = SyncClient.load(Paths.under(self.home))
        item = templates(client.prefix, 0)[0].body
        try:
            with state.tx(client.db):
                for rec in recs:
                    if rec.kind == "summary":
                        put_record(client.db, records.Record("item", rec.source, rec.source_id, 1, item))
                    put_record(client.db, rec)
                state.add_outbox(client.db, [line for line in map(records.to_json, recs) if self.stageable(line)])
        finally:
            client.close()

    @staticmethod
    def stageable(line):
        try:
            records.from_wire(json.loads(line))
            return True
        except records.BadRecord:
            return False

    def sign_the_old_way(self, recs):
        """A pack an older client signed without the chokepoint and left waiting, as after a failed upload."""
        client = SyncClient.load(Paths.under(self.home))
        try:
            lines = [line for line in map(records.to_json, recs) if self.stageable(line)]
            with state.tx(client.db):
                state.add_outbox(client.db, lines)
            ids = [rid for rid, rec in state.unsigned(client.db, 1 << 30) if rec in lines]
            dseq, prev = client._signing_head()
            header = {"epoch": client.keyring.newest, "author": client.device_id, "dseq": dseq + 1, "prev": prev,
                      "roster_version": client.roster.version, "roster_hash": client.roster.hash, "snapshot": None}
            client._save_pending(ids, dseq + 1, prev, pack.build(header, lines, client.paths.device_ssh))
        finally:
            client.close()

    def test_no_canary_leaves_through_any_outbound_path_or_reaches_the_terminal(self):
        """The fixed seeds and one fresh seed, each named in any failure, so it can be run again."""
        chosen = os.environ.get("EGRESS_SEED")
        for seed in [int(chosen)] if chosen else [*SEEDS, secrets.randbelow(1 << 32)]:
            with self.subTest(seed=seed):
                self.fresh(seed)
                try:
                    self.run_seed(seed)
                finally:
                    shutil.rmtree(self.tmp, ignore_errors=True)

    def run_seed(self, seed):
        again = f"seed {seed}: run it again with EGRESS_SEED={seed}"
        with self.net.wired():
            self.assertEqual(self.cli("sync", "login", "--app", "https://app.test", "--relay", "https://relay.test",
                                      "--label", "computer 1")[0], 0)
            self.assertEqual(self.cli("sync", "init")[0], 0)
            own = ids.install_id(Paths.under(self.home))
            recs = self.variants(own)
            self.plant(recs)
            self.sign_the_old_way(recs[:40])
            self.assertEqual(self.cli("sync")[0], 0)
            client = SyncClient.load(Paths.under(self.home))
            try:
                self.assertTrue(client.compact(force=True), "a snapshot went up")
            finally:
                client.close()

            sent = len(self.net.sent)
            label = self.c.add("label", SHAPES[1]())
            code, _ = self.cli("sync", "login", "--app", "https://app.test", "--relay", "https://relay.test",
                               "--label", f"laptop {label}", home=os.path.join(self.tmp, "b"))
            self.assertNotEqual(code, 0)
            self.assertEqual(len(self.net.sent), sent, "a label holding a secret is refused before anything is sent")

        streams = list(self.net.outbound()) + [(f"output of {argv}", out.encode()) for argv, out in self.outputs]
        leaks = [leak for where, data in streams for leak in self.c.found(where, data)]
        self.assertEqual(leaks, [], again)
        sealed = b"\n".join(self.net.sealed)
        for source_id in sorted(self.travelling):
            with self.subTest(record=source_id):
                self.assertIn(source_id.encode(), sealed, f"the variant went out through Sync; {again}")


class HideInvisibleTest(unittest.TestCase):
    """`egress.hide` strips invisible characters and then masks, so a secret SPLIT by invisibles reassembles and masks
    whole. One shape it cannot close at this layer is a secret GLUED to a word by an invisible: stripping the invisible
    glues the word to the key, and the mask's word boundary then misses it. No single order fixes both — masking before
    stripping would mask a prefix or interior of a split key before it reassembles, leaking the rest. Closing the glue
    shapes takes BOTH halves of launch gate 12, not kb.mask alone: the revendored kb.mask must match THROUGH interior
    invisibles with an invisible as a boundary, AND `egress.hide` must stop stripping invisibles before it masks (it
    strips first today, so a match-through kb.mask would never see the invisibles). These tests pin the shapes hide
    handles today and mark the glue shapes as expected failures, so when both halves of gate 12 land they turn to
    unexpected successes and the suite red, forcing the markers out."""
    KEY = "sk-proj-" + "a1" * 16  # a made-up key in a shape kb's mask knows
    # zero-width space, ZWNJ, ZWJ, word-joiner, soft hyphen, BOM, and NUL — all characters kb's mask reads through, which leave out
    MARKS = ("​", "‌", "‍", "⁠", "­", "﻿", "\x00")

    def test_a_split_key_masks_whole(self):
        # A key split by an invisible between every character masks whole, whether kb's mask reads through the
        # invisible or the strip joins it for the next pass.
        for ch in self.MARKS:
            with self.subTest(mark=f"U+{ord(ch):04X}"):
                out = egress.hide("note " + ch.join(self.KEY) + " end")
                self.assertNotIn(self.KEY, out, "the split key is masked")
                self.assertNotIn(self.KEY, out.replace(ch, ""), "and not recoverable by removing the invisible")

    def test_a_split_key_with_a_github_token_interior_masks_whole(self):
        # Round 2: a provider key split by an invisible whose interior spells a GitHub-token (ghp_…) shape. Masking that
        # interior first would break the key; kb's mask reads through the invisible and masks the key whole.
        out = egress.hide("sk-proj-" + "a1" * 7 + "​ghp_" + "a1" * 16)
        self.assertEqual(out, "[masked]", "the whole key masks; no prefix leaks")
        self.assertNotIn("sk-proj-a1a1", out)

    def test_a_split_key_with_a_key_shaped_prefix_masks_whole(self):
        # Round 2: a key split so its own prefix is key-shaped. Masking the prefix alone would leak the suffix; kb's
        # mask reads through the invisible and masks the whole.
        out = egress.hide("sk-proj-" + "a1" * 16 + "​" + "b2" * 8)
        self.assertEqual(out, "[masked]", "the whole key masks; no suffix leaks")
        self.assertNotIn("b2b2b2", out)

    def test_a_key_split_by_form_feeds_or_vertical_tabs_is_masked_on_the_way_out(self):
        # kb's mask reads a form feed or a vertical tab as a space, so it does not join a key they split. Stripping
        # them on the way out does join it, so hide masks again after it strips.
        for ch in ("\f", "\v"):
            with self.subTest(char=repr(ch)):
                out = egress.hide("note " + ch.join(self.KEY) + " end")
                self.assertNotIn(self.KEY, out)
                self.assertEqual(egress.hide(out), out, "hiding twice changes nothing")

    def test_a_split_key_whose_middle_is_a_secret_masks_whole_whatever_splits_it(self):
        # Masking the text as written alone would mask the GitHub-shaped middle and leave the key's prefix, since kb's
        # mask reads these characters as spaces or keeps them; the text stripped reads the key whole, so it masks whole.
        for ch in ("\f", "\v", "\u2028", "\ue000", "\u200b"):
            with self.subTest(char=f"U+{ord(ch):04X}"):
                text = "sk-proj-" + "a1" * 7 + ch + "ghp_" + "a1" * 16
                self.assertEqual(egress.hide(text), "[masked]")
                self.assertEqual(egress.clean({"content": text}), {"content": "[masked]"})

    def test_a_headless_recovery_code_glued_to_a_word_by_an_invisible_is_masked(self):
        code = "A1G2" * 8  # a made-up code without its head: 32 code characters, a digit and a letter past F
        out = egress.hide("budget\u200b" + code + " paid")
        self.assertNotIn(code, out)
        self.assertEqual(out, "budget[masked] paid")

    def test_a_key_glued_by_one_invisible_and_split_by_others_is_masked(self):
        # A zero-width space glues the word to the key, and form feeds split the key: read with the form feeds gone and
        # the zero-width space kept, kb's mask finds the key at its edge.
        text = "budget\u200bsk-proj-" + "\f".join("a1" * 24) + " paid"
        for out in (egress.hide(text), egress.clean({"content": text})["content"]):
            self.assertEqual(out, "budget[masked] paid")

    def test_removing_a_character_never_joins_what_kbs_mask_read_apart(self):
        # A character kb's mask reads as a break, such as a form feed, becomes a space rather than vanishing, so the
        # text that leaves never holds a key glued together that no reading masked.
        text = "budget\fsk-proj-" + "\f".join("a1" * 24) + " paid"
        out = egress.hide(text)
        self.assertNotIn("a1a1", out)
        self.assertNotIn("budgetsk-proj", out)

    def test_a_key_a_code_frees_is_masked(self):
        # Round 3: hiding a code frees the key behind it, which a form feed splits; each reading masks until it stops
        # changing, as strip-then-mask did, so the key is hidden too.
        text = "VRC1" + "A1G2" * 8 + "sk-proj-\f" + "a1" * 16
        for out in (egress.hide(text), egress.clean({"content": text})["content"]):
            self.assertNotIn("a1a1", out)
            self.assertNotIn("sk-proj", out)

    def test_a_code_split_in_a_group_next_to_a_token_is_masked(self):
        # Round 4: no reading finds the code alone; the one as written hides the token, which gives the code its edge
        # in the text joined. The readings iterate together, so each sees what the others hid.
        c = "A1G2" * 8
        text = c[:26] + "\f" + c[26:] + "\fBearer " + "c3" * 16
        for out in (egress.hide(text), egress.clean({"content": text})["content"]):
            self.assertNotIn("A1G2A1G2", out)
            self.assertNotIn("c3c3", out)

    def test_a_name_one_reading_hides_with_a_value_still_names_the_next_secret(self):
        # Round 5: with the form feeds left out, kb's mask takes `token:` into the password's value, and then no reading
        # sees a token's name. Round 1's order hides the code first, which ends the value before `token:`.
        c = "VP1" + "A1G2" * 6 + "A0"
        text = "pass\fword=1234" + c + "\ftok\fen: Pw7xYzQ9mK2nL4pR"
        for out in (egress.hide(text), egress.clean({"message": text})["message"]):
            self.assertNotIn("Pw7x", out)
            self.assertNotIn("L4pR", out)

    def test_a_secret_with_one_mark_kbs_mask_reads_through_is_masked(self):
        # Round 6: a variation selector or a combining grapheme joiner is a mark, not a format character, so it was read
        # as text and the code left whole; delete it and the code is valid.
        code_ = "VRC1-000G-40R4\ufe0f-0M30-E209-185G-R38E-1W81-24GK"
        texts = ("Recovery code: " + code_, "Recovery code: " + code_.replace("\ufe0f", "\u034f"),
                 "token sk-proj-" + "a1" * 8 + "\ufe0f" + "a1" * 8 + " end", "\u2764\ufe0f" + "sk-proj-" + "a1" * 16,
                 "note \u3164ghp_" + "Ab" * 18 + " end", "pair VP1" + "A1G2" * 3 + "\u034f" + "A1G2" * 3 + "A0 end",
                 "budget\u3164Bearer " + "a1a1" * 8 + " paid", "budget\u115fBearer " + "a1a1" * 8 + " paid")
        for text in texts:
            for out in (egress.hide(text), egress.clean({"content": text})["content"]):
                with self.subTest(text=text):
                    self.assertNotIn("40R4", out)
                    self.assertNotIn("a1a1", out)
                    self.assertNotIn("AbAb", out)
                    self.assertNotIn("A1G2", out)

    def test_honest_text_with_marks_leaves_as_written(self):
        # The marks leave as they are, so an emoji keeps its look.
        for text in ("thanks \u2764\ufe0f see you", "step 1\ufe0f\u20e3 then 2\ufe0f\u20e3", "\u1100\u1161\u115f note",
                     "a\u034fb and \u3164 filler"):
            self.assertEqual(egress.hide(text), text)
            self.assertEqual(egress.visible(text), text)

    def test_a_secret_kbs_mask_finds_only_as_it_leaves_is_masked(self):
        # Read as written, kb's mask takes the second name as the first one's value, past the separator; read with it left
        # out, the names glue. Only the text as it leaves, the separator a space, shows the second password as a value.
        text = "passwd:P\x1cpasswd: Pw7xYzQ9mK2nL4pR"
        for out in (egress.hide(text), egress.clean({"content": text})["content"]):
            self.assertNotIn("Pw7x", out)
            self.assertNotIn("L4pR", out)

    def test_ordinary_text_and_a_plainly_bounded_key(self):
        self.assertEqual(egress.hide("just some ordinary words"), "just some ordinary words")
        self.assertNotIn(self.KEY, egress.hide("budget " + self.KEY + " paid"), "a space-bounded key still masks")

    def test_a_key_glued_to_a_word_by_an_invisible_is_masked(self):
        # Launch gate 12, closed by both of its halves: kb's mask matches through invisibles and takes one as a word's
        # edge (varve #27), and hide masks before it strips, so the mask sees the invisible that bounds the key.
        for ch in self.MARKS:
            out = egress.hide("budget" + ch + self.KEY + " paid")
            self.assertNotIn(self.KEY, out, f"gate 12: a key glued by U+{ord(ch):04X} must not leak")

    def test_a_word_then_an_invisible_then_a_split_key_is_masked(self):
        # Launch gate 12, the combined shape: a word, an invisible, then a key split by invisibles, closed by the same
        # two halves together.
        ch = "​"
        out = egress.hide("budget" + ch + ch.join(self.KEY) + " paid")
        self.assertNotIn(self.KEY, out.replace(ch, ""), "gate 12: the combined shape must not leak the key")


# Characters that sit inside or beside secrets in the property corpus: the invisible ones kb's mask reads through, and
# ones it reads as breaks, alone and mixed.
SEPARATORS = ("", "\u200b", "\f", "\u2028", "\ue000", "\u200b\f", "\f\u200b")
MIXED_PIECES = (("sk-proj-" + "a1" * 7, "ghp_" + "a1" * 16), ("sk-proj-" + "a1" * 16, "b2" * 8),
                ("ghp_" + "a1" * 10, "a1" * 10), ("password", "=hunter2hunter2"), ("A1G2" * 4, "A1G2" * 4))
SPLITTERS = HideInvisibleTest.MARKS + ("\f", "\v", "\u2028", "\ue000", "\x1c", "\ufe0f", "\u034f")


def corpus():
    """The mixed shapes, each pair of pieces with each pair of separators, bare or after a word; then every canary shape
    the fuzz above plants, at its fixed seeds, plain, glued to a word, split everywhere and split once, by each splitter;
    then the classes below, each a shape an earlier round of review found."""
    for (left, right), first, second, word in itertools.product(MIXED_PIECES, SEPARATORS, SEPARATORS, ("", "budget")):
        yield word + first + left + second + right + " end"
    for seed in SEEDS:
        RNG.seed(seed)
        for shape in SHAPES:
            canary = shape()
            yield f"note {canary} end"
            for ch in SPLITTERS:
                yield "note" + ch + canary + " end"
                yield "note " + ch.join(canary) + " end"
                yield "note " + canary[:len(canary) // 2] + ch + canary[len(canary) // 2:] + " end"
    # Codes beside keys, each way round, glued or split by each splitter, so hiding one can free the other.
    RNG.seed(SEEDS[0])
    codes = (code("VRC1", 20, 32), code("VP1", 16, 26), "A1G2" * 8)
    keys_ = ("sk-proj-" + "a1" * 16, "ghp_" + pick(string.ascii_letters, 36), "password=Pw7" + pick(ALNUM, 13))
    for (c, k), ch in itertools.product(itertools.product(codes, keys_), ("",) + SPLITTERS):
        head, tail = k.split("-", 1) if k.startswith("sk-") else (k[:4], k[4:])
        yield c + ch + k + " end"
        yield k + ch + c + " end"
        yield c + head + "-" + ch + tail + " end"
    # Codes split inside a group, beside a token whose masking gives the code its edge in another reading.
    tokens = ("Bearer " + "c3" * 16, "password=Pw7" + pick(ALNUM, 13), "sk-proj-" + "a1" * 16)
    for c, ch, token in itertools.product(codes, SPLITTERS, tokens):
        split = c[:len(c) - 6] + ch + c[len(c) - 6:]
        yield split + ch + token + " end"
        yield token + ch + split + " end"
    # A short value kb's mask reads, past a separator, as taking in the next name, whose own value then reads as one only
    # in the text as it leaves.
    names = ("passwd: ", "passwd:", "password=", "secret: ", "api_key=", "token: ")
    for name, ch, short in itertools.product(names, SPLITTERS, ("P", "x1")):
        yield name + short + ch + name + "Pw7" + pick(ALNUM, 13) + " end"


class HideBarTest(unittest.TestCase):
    """The bar the chokepoint holds, over the corpus, as one property. It hides every character any earlier hide hid as part
    of a secret (egress_history.EARLIER, which only grows) and every one kb's mask hides reading the text as written; no
    invisible or control character but a tab, a line feed or a carriage return leaves; and text with no invisible
    character leaves exactly as the first hide left it. kb's mask hides a text as it leaves too, `visible` giving its
    characters' places."""

    def test_hide_meets_the_bar_on_every_corpus_text(self):
        texts = list(corpus())
        self.assertGreater(len(texts), 2500)
        for text in texts:
            with self.subTest(text=text):
                out, origin = egress.hide_with_origin(text)
                self.assertEqual(out, egress.hide(text))
                shown = {o for o in origin if o >= 0}
                for earlier in EARLIER:
                    self.assertEqual(earlier.replay(text), earlier.hide(text), f"{earlier.name}: replayed as it ran")
                    # What it hid as part of a secret, not the invisible characters it dropped: those leave as nothing
                    # or, if kb's mask reads them as breaks, as a space, which the check below governs.
                    hid = {i for i in earlier.hidden_places(text) if unicodedata.category(text[i]) not in egress.INVISIBLE}
                    self.assertEqual(shown & hid, set(), f"{earlier.name} hid it")
                written, came = kbmask.mask_with_origin(text)
                by_kb = set(range(len(text))) - {c for c in came if c >= 0}
                self.assertEqual(shown & by_kb, set(), "kb's mask hides it in the text as written")
                places = [i for i, ch in enumerate(text) if egress._kind(ch, "\n\t\r") != 1]
                leaving, came = kbmask.mask_with_origin(egress.visible(text))
                by_kb = set(places) - {places[c] for c in came if c >= 0}
                self.assertEqual(shown & by_kb, set(), "kb's mask hides it in the text as it leaves")
                self.assertFalse([ch for ch in out if ch not in "\t\n\r" and unicodedata.category(ch) in egress.INVISIBLE])
                if not any(egress._kind(ch, "\n\t\r") for ch in text):
                    self.assertEqual(out, at_922fb6b.hide(text), "text with no invisible character leaves as before")


class HideTimeTest(unittest.TestCase):
    def test_many_codes_take_time_in_step_with_the_text(self):
        # Cutting each code out of the whole text in turn made four times the codes take about twelve times as long.
        def took(n):
            text = ("A1G2" * 8 + "!") * n + "\u200b"
            start = time.process_time()
            out = egress.hide(text)
            self.assertEqual(out.count("[masked]"), n)
            return time.process_time() - start
        self.assertLess(took(4096), 8 * took(1024))

class NamedValueTest(unittest.TestCase):
    COOKIE = "session=Pw7xYzQ9mK2nL4pR"

    def test_a_header_held_as_a_key_is_masked(self):
        # Round 7: kb's mask reads a header as `Cookie: ...`, and the name was only ever tried as `Cookie=...`.
        for value in ({"Cookie": self.COOKIE}, {"Set-Cookie": self.COOKIE + "; Path=/; HttpOnly"},
                      {"headers": {"Cookie": self.COOKIE}}, {"Authorization": "Zq8Wn3Xv5Ty7Ur9Ps1Lm"}):
            with self.subTest(value=value):
                out = json.dumps(egress.clean(value))
                self.assertNotIn("Pw7x", out)
                self.assertNotIn("Zq8W", out)

    def test_a_header_held_as_a_pair_is_masked(self):
        # The same name and value as a pair, or as a name and a value, as captured requests hold their headers.
        for value in ({"headers": [["Cookie", self.COOKIE]]}, [("Set-Cookie", self.COOKIE)],
                      {"headers": [{"name": "Cookie", "value": self.COOKIE}]},
                      [{"name": "Authorization", "value": "Zq8Wn3Xv5Ty7Ur9Ps1Lm", "comment": "sent"}]):
            with self.subTest(value=value):
                out = json.dumps(egress.clean(value))
                self.assertNotIn("Pw7x", out)
                self.assertNotIn("Zq8W", out)
                self.assertIn("ookie" if "ookie" in json.dumps(value) else "Authorization", out)

    def test_a_secret_held_as_a_key_and_a_value_is_masked(self):
        # Round 8: Postman holds a header or a credential as `key` and `value`, AWS tags as `Key` and `Value`, and
        # Kubernetes an environment variable as `name` and `value`.
        for value in ({"auth": {"type": "basic", "basic": [{"key": "password", "value": "Pw7xYzQ9mK2nL4pR"}]}},
                      {"Tags": [{"Key": "api_key", "Value": "Pw7xYzQ9mK2nL4pR"}]},
                      {"env": [{"name": "DB_PASSWORD", "value": "Pw7xYzQ9mK2nL4pR"}]},
                      {"header": [{"key": "Cookie", "value": self.COOKIE, "type": "text"}]},
                      # Round 9: both names beside one value, the same or not; either naming a secret masks it.
                      {"headers": [{"name": "Cookie", "key": "Cookie", "value": self.COOKIE}]},
                      {"name": "colour", "key": "password", "value": "Pw7xYzQ9mK2nL4pR"}):
            with self.subTest(value=value):
                self.assertNotIn("Pw7x", json.dumps(egress.clean(value)))

    def test_cleaning_twice_is_cleaning_once(self):
        # Round 8: a field's name with an invisible inside reads as `value` only once cleaned, so the first pass kept the
        # cookie and the second masked it, and a pack signed again through the mask never settled.
        value = {"headers": [{"name": "Cookie", "va\u200blue": self.COOKIE}]}
        once = egress.clean(value)
        self.assertNotIn("Pw7x", json.dumps(once))
        self.assertEqual(egress.clean(once), once)
        for text in itertools.islice(corpus(), 0, None, 9):
            for value in ({text: text}, {"headers": [[text, text]]}, {"name": text, "value": text},
                          {"Key": text, "Value": text}):
                with self.subTest(value=value):
                    once = egress.clean(value)
                    self.assertEqual(egress.clean(once), once)

    def test_nested_names_and_values_are_cleaned_once_each(self):
        # Round 10: each level cleaned its child three times, so eight levels took about three seconds. Round 11: under a
        # secret's name each level cleaned its child, then hid it again, so the work grew with the square of the depth.
        from unittest import mock
        for name, depth in itertools.product(("colour", "password"), (8, 30, 60)):
            value = "blue"
            for _ in range(depth):
                value = {"name": name, "value": value}
            with mock.patch.object(egress, "_clean", wraps=egress._clean) as cleaned, \
                    mock.patch.object(egress, "_all_hidden", wraps=egress._all_hidden) as hidden:
                out = egress.clean(value)
            if name == "colour":
                self.assertEqual(out, value)
            else:
                self.assertNotIn("blue", json.dumps(out))
            self.assertLess(cleaned.call_count + hidden.call_count, 20 * depth, f"{name}, {depth} levels")

    def test_past_the_depth_limit_the_work_stays_linear_and_a_secret_still_does_not_leave(self):
        # The coordinator closed depth by construction: nothing past DEPTH levels is walked, it is masked as text.
        from unittest import mock
        for depth in (egress.DEPTH + 1, 4 * egress.DEPTH, 16 * egress.DEPTH, 20_000):
            value = {"password": "Pw7xYzQ9mK2nL4pR", "note": "blue"}
            for level in range(depth):
                value = {"note": value} if level % 2 else [value]
            with mock.patch.object(egress, "_clean", wraps=egress._clean) as cleaned, \
                    mock.patch.object(egress, "hide", wraps=egress.hide) as hidden:
                out = egress.clean(value)
            with self.subTest(depth=depth):
                self.assertNotIn("Pw7x", json.dumps(out))
                self.assertEqual(egress.clean(out), out)
                self.assertLess(cleaned.call_count, 4 * egress.DEPTH, "walked no deeper than DEPTH")
                self.assertLess(sum(len(call.args[0]) for call in hidden.call_args_list), 5_000 + 20 * depth,
                                "masked text in step with the input")

    def test_ordinary_names_and_pairs_leave_as_they_are(self):
        for value in ({"title": "Notes", "ts": "2026-10-01", "meta": {"hash": "h1", "source": "notes"}},
                      [["title", "Notes"], ["host", "tokenbox"]], {"name": "colour", "value": "blue"},
                      {"headers": [{"name": "Accept", "value": "text/html"}, ["Content-Type", "application/json"]]},
                      ("Cookie", "jar"), {"Cookie jar": "three biscuits"}, {"tags": ["secret", "santa"]},
                      ["password", "reset"], {"key": "colour", "value": "blue"}, [{"Key": "team", "Value": "sync"}],
                      {"title": "Passwords", "ts": "2026-10-01", "meta": {"hash": "h1"}}):
            with self.subTest(value=value):
                self.assertEqual(json.dumps(egress.clean(value)), json.dumps(value))


class OneCharacterTest(unittest.TestCase):
    """The bar a merge is held to: a secret with one invisible or control character inside it or beside it, the way
    copy-paste, terminals and editors leave them, is hidden. Every character kb's mask reads through and every control,
    format and separator character, with a private use one, an unassigned one and a surrogate, goes inside and beside
    each kind of secret; no eight of the secret's letters and digits in a row may leave."""

    SECRETS = ("VRC1-000G-40R4-0M30-E209-185G-R38E-1W81-24GK", "VRC1000G40R40M30E209185GR38E1W8124GK",
               "VP1-A1G2-A1G2-A1G2-A1G2-A1G2-A1G2-A0", "000G 40R4 0M30 E209 185G R38E 1W81 24GK",
               "sk-proj-" + "a1B2" * 10, "ghp_" + "Ab3D" * 9, "AKIAIOSFODNN7EXAMPLE", "Bearer " + "c3D4" * 8,
               "password=Pw7xYzQ9mK2nL4pR", "api_key: Zq8Wn3Xv5Ty7Ur9Ps1")
    HEADS = ("password=", "api_key: ", "Bearer ", "VRC1-", "VRC1", "VP1-")

    # Characters the bar tries in every Unicode version Python 3.9 and later ship: NUL, vertical tab, form feed, ESC,
    # 0x1c, NEL, soft hyphen, combining grapheme joiner, zero-width space, non-joiner and joiner, line and paragraph
    # separators, word joiner, Hangul filler, byte order mark, every variation selector and every tag character.
    ALWAYS = ("\x00\x0b\x0c\x1b\x1c\x85\xad\u034f\u200b\u200c\u200d\u2028\u2029\u2060\u3164\ufeff"
              + "".join(chr(cp) for cp in [*range(0xFE00, 0xFE10), *range(0xE0020, 0xE0080), *range(0xE0100, 0xE01F0)]))

    def test_one_invisible_inside_or_beside_a_secret_never_leaves_it_whole(self):
        # The characters are the ones this Python's Unicode tables assign, so how many there are moves with its version.
        known = [chr(cp) for cp in range(0x110000) if unicodedata.category(chr(cp)) != "Cn"]
        chars = {ch for ch in known
                 if kbmask.INVISIBLE.fullmatch(ch) or unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp")}
        self.assertEqual([f"U+{ord(ch):04X}" for ch in self.ALWAYS if ch not in chars], [])
        chars = sorted((chars | {"\ue000", "\U000f0000", "\u0378", "\U000e0002", "\ud800"}) - set("\n\t\r"))
        for secret in self.SECRETS:
            head = next((h for h in self.HEADS if secret.startswith(h)), "")
            letters = "".join(ch for ch in secret[len(head):] if ch.isalnum())
            pieces = {letters[i:i + 8] for i in range(len(letters) - 7)}
            third = len(secret) // 3
            for ch in chars:
                for text in (f"note {secret[:third]}{ch}{secret[third:]} end",
                             f"note {secret[:2 * third]}{ch}{secret[2 * third:]} end",
                             f"budget{ch}{secret} paid", f"budget {secret}{ch}paid"):
                    out = "".join(c for c in egress.hide(text) if c.isalnum())
                    if any(piece in out for piece in pieces):
                        self.fail(f"{text!r} left {egress.hide(text)!r}")


def residual(test):
    """A shape on design.md's residual list: a deliberate construction known to get through. It is expected to fail,
    and fails the suite the day it no longer does, so the list can lose it."""
    return unittest.expectedFailure(test)


class PinnedMaskTest(unittest.TestCase):
    """What kb's mask at the pinned commit, varve #40's merge, hides leaves hidden: shapes #27's mask left, keys of
    digits and separators, which #29's mask showed as dates, and credentials of digits with a T or a Z, which #39's
    showed where it reads a value by its shape alone."""

    def test_a_header_held_as_a_name_and_a_value_in_json_text(self):
        # Round 8's residual: a value whose name sits in another JSON field, as a transcript of a tool call holds
        # captured headers.
        for text in ('{"headers":[{"name":"Cookie","value":"session=Pw7xYzQ9mK2nL4pR"}]}',
                     '{"key":"password","value":"Pw7xYzQ9mK2nL4pR"}'):
            self.assertNotIn("Pw7x", egress.hide(text))

    def test_a_short_value_glued_to_the_next_name(self):
        for text in ("passwd: Ppasswd: Pw7xYzQ9mK2nL4pR end", "passwd: P\u200bpasswd: Pw7xYzQ9mK2nL4pR end"):
            self.assertNotIn("Pw7x", egress.hide(text))

    def test_tokens_after_colour_codes_typed_passwords_and_gitleaks_tokens(self):
        esc = "\x1b"
        shapes = [
            (esc + "[32mghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8" + esc + "[0m done", "A1b2C3d4"),
            ("key " + esc + "[1;33mAKIA" + "QWERTYUIOPASDFGH" + esc + "[0m", "QWERTYUI"),
            ('await page.fill("#password", "' + "Tr0ub4dor&3xq" + '");', "Tr0ub4dor"),
            ('await page.getByLabel("Password").fill("' + "Hx9!kq2Lmz" + '");', "Hx9!kq2L"),
            ('driver.find_element(By.ID, "password").send_keys("' + "Pq8#wErT55" + '")', "Pq8#wErT"),
            ("shpat_" + "0123456789abcdef" * 2, "0123456789ab"),
            ("dop_v1_" + "0123456789abcdef" * 4, "0123456789ab"),
            ("SG." + "aB3dE5gH7jK9mN1pQ2rS4t" + "." + "uV6wX8yZ0aB2cD4eF6gH8iJ0kL2mN4oP6qR8sT0uV2w", "aB3dE5gH"),
        ]
        for text, secret in shapes:
            with self.subTest(text=text):
                self.assertNotIn(secret, egress.clean({"text": text})["text"])

    def test_a_key_of_digits_and_separators_is_no_date(self):
        arabic_indic = "-".join("".join(chr(0x0660 + int(d)) for d in group) for group in ("1234", "5678", "9012"))
        arabic_indic += "-" + "".join(chr(0x0660 + int(d)) for d in "3456")
        for text in ("api_key: 1234-5678-9012-3456", "secret: 4829-1937-5521-0098-7731",
                     "API_TOKEN=1699999999.123456789", "access_key: 8472:1934:5521:0098",
                     "auth_token: 20261101T100000Z1234", "token: " + arabic_indic):
            with self.subTest(text=text):
                self.assertTrue(egress.clean({"text": text})["text"].endswith("[masked]"))

    def test_a_credential_of_digits_with_a_t_or_a_z_read_by_its_shape_alone_is_masked(self):
        value = "20261101T100000Z1234"
        for text in (f"Authorization: {value}", f"GET /v1 HTTP/1.1\nProxy-Authorization: {value}\nAccept: */*",
                     f"<secret>{value}</secret>", f"user=ci pass={value}", f"Server=db;Uid=sa;Pwd={value};"):
            with self.subTest(text=text):
                cleaned = egress.clean({"text": text})["text"]
                self.assertNotIn(value[:12], cleaned)
                self.assertNotIn(value[-12:], cleaned)

    def test_a_real_date_and_time_or_a_time_of_day_read_by_its_shape_alone_is_shown(self):
        for text in ("Authorization: 10:00:00.123456Z", "Authorization: 20261101T100000Z",
                     "Authorization: 2026-11-01T10:00:00Z", "X-Amz-Date: 20261101T100000Z",
                     "<secret>20261101T100000Z</secret>", "user=ci pass=20261101T100000Z"):
            with self.subTest(text=text):
                self.assertEqual(egress.clean({"text": text})["text"], text)


class ResidualTest(unittest.TestCase):
    @residual
    def test_a_code_glued_onto_the_end_of_another(self):
        # kb's mask takes the first code and the second's first six groups as one token; the last two, past a space, are
        # too short to be a code. Plain text leaves them the same way.
        second = "5SVAV3AJ5MLPDHJCNHPRERM4 F2NG2BX5"
        for glue in ("", "\u2060"):
            text = "VRC1PNH766JU9C9GF55UVZSQ4MA9F676Z6Z7" + second[:13] + glue + second[13:]
            self.assertNotIn("F2NG2BX5", egress.hide(text))

    @residual
    def test_a_password_typed_through_a_browser_tool_in_transcript_text(self):
        # kb hides what a browser tool types when its readers ingest the call (typed_secrets), not in its mask, so a row
        # kb stored before varve #29 and has not read again leaves with the password, as on main.
        for text in ('TOOL mcp__playwright__browser_type: {"element": "Password", "text": "Tr0ub4dor&3xq"}',
                     '{"type":"tool_use","name":"mcp__playwright__browser_type",'
                     '"input":{"element":"Password field","ref":"e12","text":"Tr0ub4dor&3xq"}}'):
            self.assertNotIn("Tr0ub4dor", egress.clean({"text": text})["text"])


class NamedOrdersTest(unittest.TestCase):
    def test_each_order_hide_takes_is_the_version_it_names(self):
        # hide hides what each of three orders hides. Two are earlier versions: each hides, of the characters that are
        # not invisible or control characters, what that version hid.
        keep = "\n\t\r"
        shown_kind = lambda places, text: {i for i in places if unicodedata.category(text[i]) not in egress.INVISIBLE}
        texts = [t for t in corpus() if any(egress._kind(ch, keep) for ch in t)]
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(shown_kind(egress._strip_then_mask(text, keep, True), text),
                                 shown_kind(EARLIER[0].hidden_places(text), text), "strip-then-mask")
                self.assertEqual(shown_kind(egress._mask_then_strip(text, keep, True), text),
                                 shown_kind(EARLIER[1].hidden_places(text), text), "round 1's order")


class JointFixpointTest(unittest.TestCase):
    def test_every_round_hides_more_and_the_rounds_end(self):
        # The fixpoint ends because a round never takes back a place it hid. Read with a hard cap on rounds, so a change
        # that un-hid one, and so might never end, fails here instead.
        texts = [t for t in corpus() if any(egress._kind(ch, "\n\t\r") for ch in t)]
        self.assertGreater(len(texts), 2000)
        for text in texts:
            with self.subTest(text=text):
                shown = set(range(len(text)))
                rounds = list(itertools.islice(egress._rounds(text, "\n\t\r", True), egress.PASSES + 2))
                self.assertLessEqual(len(rounds), egress.PASSES + 1, "the rounds end within the cap")
                for n, (current, origin) in enumerate(rounds):
                    self.assertEqual(len(current), len(origin))
                    now = {o for o in origin if o >= 0}
                    self.assertTrue(now <= shown, "a round never shows what an earlier one hid")
                    if n:
                        self.assertLess(now, shown, "each round but the first hides more")
                    shown = now


if __name__ == "__main__":
    unittest.main()
