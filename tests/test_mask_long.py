"""A long text masked in pieces (kb.mask._long) masks as the text read whole, wherever a cut falls, and the memo
(kb.mask.memo) gives back what masking worked out before.

Every test of equality compares with the mask of the text read whole (_mask_whole, _mask_with_origin_whole), and puts
a cut inside a secret: at each of its characters for short ones, and across key blocks of full size at each of their
line breaks and every few hundred characters."""
import base64
import contextlib
import hashlib
import os
import random
import re
import stat
import tempfile
import time
import unittest
from unittest import mock

from kb import mask as masking
from kb.mask import mask, mask_with_origin
from tests import samples
from tests.speed import scaled

FILLER_WORDS = ("the quick brown fox jumps over a lazy dog while we read notes about lunch plans and the weather "
                "in spring with friends from the old office near the river").split()


def filler(size, seed, breaks=True):
    """Plain prose of `size` characters that masks to itself, in lines of about 80 when `breaks`."""
    r, out, line, total = random.Random(seed), [], [], 0
    while total < size:
        word = r.choice(FILLER_WORDS)
        line.append(word)
        total += len(word) + 1
        if breaks and sum(len(w) + 1 for w in line) > 80:
            out.append(" ".join(line) + "\n")
            line = []
    out.append(" ".join(line) + " ")
    return "".join(out)[:size]


@contextlib.contextmanager
def sizes(whole, piece, reach):
    with mock.patch.object(masking, "WHOLE", whole), mock.patch.object(masking, "ALONE", whole), \
            mock.patch.object(masking, "PIECE", piece), mock.patch.object(masking, "REACH", reach):
        yield


def in_pieces():
    """Long texts read in pieces, as where the memo keeps them (memo())."""
    return mock.patch.object(masking, "ALONE", masking.WHOLE)


@contextlib.contextmanager
def cut_at(*cuts):
    with mock.patch.object(masking, "_piece_cuts", lambda text: list(cuts)):
        yield


def fresh_memo():
    masking._MEMO.kept.clear()
    masking._MEMO.size = 0


# Secrets as agents' sessions hold them, many across lines, where a line break is a place to cut.
CASES = [
    "export GITHUB_TOKEN=ghp_" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2",
    "curl -u admin:Sup3rS3cretPassw0rd https://api.example.com/v1/items",
    "mysql -h db.internal -u root -pQ7vR2mX9kL4w orders",
    '{"password": "correct-horse-battery-staple-91"}',
    '{\n  "client_secret":\n    "q8Zr2Lm4Np6Ks0Tv3Wx5Yb7"\n}',
    "password:\n  hunter2-but-longer-9x",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "postgres://app_user:Pg5ecretPa55w0rd@db.example.com:5432/app",
    "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "docker login -u builder -p dckr_pat_" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1",
    "sk-ant-api03-" + "Zq7Wx3Ec5Rv2Tb8Yn4Um6Ik1Ol9Pa0Sd3Fg5Hj7Kl2Zx4Cv6Bn8Mq1We3Rt5Yu7Io9Pa2Sd4Fg6Hj8Kl0" + "-AbCdEf",
    "xoxb-" + "123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx",
    "mysql \\\n  --user=root \\\n  --password=n0tInTheD1ct10nary \\\n  shop",
    "PGPASSWORD='s3cr3t-and-long-enough' psql -h localhost",
    "token=AKIA" + "IOSFODNN7EXAMPLE secret=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLE",
    '"api_key\\": \\"ZmFrZS1hcGkta2V5LWZvci10ZXN0cy0xMjM0NQ\\"',
    "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\nQyNTUxOQAAACDm7vNQ2v1Xq7Z\n-----END OPENSSH PRIVATE KEY-----",
]


def pattern_cases():
    """A few secrets drawn from each of kb's own patterns, as tests/samples.py draws them."""
    out = []
    for i, pattern in enumerate(masking.SECRET_PATTERNS + masking.PREFIXED_SECRETS):
        for text in samples.samples(pattern, 2, 1000 + i):
            if masking._mask_whole(text) != text and len(text) < 400:
                out.append(text)
    return out


class Equal(unittest.TestCase):
    """Small sizes, so the tests run fast; every secret here reads within REACH of itself."""

    def check(self, text, cuts):
        with sizes(4096, 2048, 512), cut_at(*cuts):
            fresh_memo()
            whole = masking._mask_whole(text)
            self.assertEqual(mask(text), whole)
            fresh_memo()
            self.assertEqual(mask_with_origin(text), masking._mask_with_origin_whole(text))
        return whole

    def test_a_secret_cut_at_each_character_masks_as_whole(self):
        for n, secret in enumerate(CASES):
            left = filler(2500, n) + "\n"
            text = left + secret + "\n" + filler(2500, n + 50)
            for k in range(1, len(secret)):
                with self.subTest(case=n, cut=k):
                    whole = self.check(text, [len(left) + k])
                    self.assertNotEqual(whole, text, "the case must hold a secret")

    def test_secrets_of_every_pattern_cut_in_three_places(self):
        cases = pattern_cases()
        self.assertGreater(len(cases), 20)
        for n, secret in enumerate(cases):
            left = filler(2500, n)
            text = left + " " + secret + " " + filler(2500, n + 99)
            for k in sorted({1, len(secret) // 2, len(secret) - 1}):
                with self.subTest(secret=secret[:30], cut=k):
                    self.check(text, [len(left) + 1 + k])

    def test_a_keyword_far_from_its_token_still_reads_the_rule(self):
        # gitleaks' rules are read only where the text holds a keyword of theirs; here it is two pieces away.
        rule = next(r for r in masking._GITLEAKS if r[3] == ("facebook",))  # its secret holds no keyword
        token = samples.samples(rule[0], 1, 7)[0]
        self.assertNotEqual(masking._mask_whole("facebook " + token), "facebook " + token)
        self.assertEqual(masking._mask_whole("id " + token), "id " + token)
        text = "see facebook docs\n" + filler(6000, 3) + "\n" + token + "\n" + filler(3000, 4)
        whole = self.check(text, [4000, text.index(token) + 10])
        self.assertNotIn(token, whole)

    def test_a_run_to_the_end_across_a_cut_masks_as_whole(self):
        # A key's paragraph with no footer runs on to the text's end: the cut inside it is read as one piece.
        body = "\n".join("MII" + "Q" * 61 for _ in range(80))
        text = filler(3000, 5) + "\n-----BEGIN PRIVATE KEY-----\n" + body
        whole = self.check(text, [3000 + 400, 3000 + 3000])
        self.assertNotIn("QQQQ", whole)

    def test_a_footer_with_no_header_masks_its_paragraph_across_a_cut(self):
        body = "\n".join("MII" + "Q" * 61 for _ in range(80))
        text = filler(3000, 6) + "\n\n" + body + "\n-----END PRIVATE KEY-----\n" + filler(3000, 7)
        whole = self.check(text, [3000 + 300, 3000 + 2500])
        self.assertNotIn("QQQQ", whole)

    def test_cuts_in_long_lines_and_in_many_places(self):
        r = random.Random(11)
        parts = []
        for i in range(40):
            parts.append(filler(r.randint(100, 900), i, breaks=bool(i % 3)))
            parts.append(r.choice(CASES) + r.choice(["\n", " ", ""]))
        text = "".join(parts)
        for trial in range(6):
            cuts = sorted(r.sample(range(100, len(text) - 100), 8))
            with self.subTest(trial=trial):
                self.check(text, cuts)

    def test_a_text_the_mask_hides_most_of_is_masked_whole_once_merging_runs_long(self):
        text = ("[masked]" + "A" * 64 + "\n\n") * 400
        calls, patch = counting()
        with sizes(4096, 2048, 512), mock.patch.object(masking, "MERGED", 8192), patch, \
                mock.patch.object(masking, "_mask_whole", wraps=masking._mask_whole) as whole:
            fresh_memo()
            self.assertEqual(mask(text), "[masked]\n\n" * 400)
        whole.assert_called_once_with(text)
        self.assertLess(sum(calls), 4 * len(text))

    def test_cuts_as_chosen(self):
        text = filler(20000, 9) + filler(9000, 10, breaks=False) + filler(5000, 12)
        with sizes(4096, 2048, 512):
            cuts = masking._piece_cuts(text)
        self.assertGreater(len(cuts), 8)
        for a, b in zip([0] + cuts, cuts):
            self.assertGreaterEqual(b - a, 2048)
            self.assertLessEqual(b - a, 2 * 2048)
        for c in cuts:
            self.assertTrue(text[c - 1] in "\n \t" or text.find("\n", c - 2048, c) < 0)
        grown = text + filler(7000, 13)
        with sizes(4096, 2048, 512):
            self.assertEqual(masking._piece_cuts(grown)[:len(cuts) - 1], cuts[:-1])


class FullSize(unittest.TestCase):
    """Key blocks as long as the mask's longest readings, at the sizes the package runs with, cut across."""

    def setUp(self):
        patch = in_pieces()
        patch.start()
        self.addCleanup(patch.stop)

    def check(self, text, cuts):
        with cut_at(*cuts):
            fresh_memo()
            whole = masking._mask_whole(text)
            self.assertEqual(mask(text), whole)
        return whole

    def blocks(self):
        r = random.Random(3)
        b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

        def lines(n):
            return "\n".join("".join(r.choice(b64) for _ in range(64)) for _ in range(n))
        pem = "-----BEGIN RSA PRIVATE KEY-----\n" + lines(25) + "\n-----END RSA PRIVATE KEY-----"
        encrypted = ("-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,0123456789ABCDEF"
                     "0123456789ABCDEF\n\n" + lines(240) + "\n-----END RSA PRIVATE KEY-----")
        pgp = "-----BEGIN PGP PRIVATE KEY BLOCK-----\n\n" + lines(180) + "\n=AbCd\n-----END PGP PRIVATE KEY BLOCK-----"
        ssh2 = "---- BEGIN SSH2 ENCRYPTED PRIVATE KEY ----\nComment: \"rsa-key\"\n" + lines(30) + \
            "\n---- END SSH2 ENCRYPTED PRIVATE KEY ----"
        encoded = base64.b64encode(pem.encode()).decode()
        return {"pem": pem, "encrypted": encrypted, "pgp": pgp, "ssh2": ssh2, "base64": encoded}

    def test_key_blocks_cut_at_their_breaks(self):
        for name, block in self.blocks().items():
            left = filler(masking.REACH * 3, 21)
            text = left + "\n" + block + "\n" + filler(masking.REACH * 3, 22)
            start = len(left) + 1
            breaks = [start + m.end() for m in re.finditer("\n", block)]
            places = sorted(set(breaks[:3] + breaks[-3:] + list(range(start + 1, start + len(block), 4000))))
            for cut in places:
                with self.subTest(block=name, cut=cut - start):
                    whole = self.check(text, [cut])
                    self.assertNotEqual(whole, text)

    def test_a_long_text_with_keys_masks_as_whole_as_cut_by_itself(self):
        blocks = list(self.blocks().values())
        text = "".join(filler(40000, i) + "\n" + blocks[i % len(blocks)] + "\n" for i in range(12))
        self.assertGreater(len(text), masking.WHOLE)
        fresh_memo()
        self.assertEqual(mask(text), masking._mask_whole(text))
        fresh_memo()
        self.assertEqual(mask_with_origin(text), masking._mask_with_origin_whole(text))


def sprinkled(size, seed):
    """Prose with a secret every twenty lines or so, as an agent's session holds them."""
    r, out, total = random.Random(seed), [], 0
    while total < size:
        line = filler(r.randint(800, 2400), r.random()) + "\n" + r.choice(CASES) + "\n"
        out.append(line)
        total += len(line)
    return "".join(out)


def counting():
    """A patch of kb.mask._masked that counts the characters it reads."""
    calls, real = [], masking._masked
    return calls, mock.patch.object(masking, "_masked", side_effect=lambda t, *a: calls.append(len(t)) or real(t, *a))


class Speed(unittest.TestCase):
    """A session grows all day and is masked again each time it is read: only where it grew is masked again."""

    def setUp(self):
        patch = in_pieces()
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_grown_text_masks_again_only_where_it_grew(self):
        text = sprinkled(900_000, 3)
        fresh_memo()
        started = time.process_time()
        first = mask(text)
        cold = time.process_time() - started
        grown = text + sprinkled(20_000, 4)
        calls, patch = counting()
        with patch:
            started = time.process_time()
            again = mask(grown)
            warm = time.process_time() - started
        self.assertEqual(again[:len(first) - 2 * masking.PIECE], first[:len(first) - 2 * masking.PIECE])
        self.assertLess(sum(calls), 3 * masking.PIECE + 4 * masking.REACH + 20_000)
        self.assertLess(warm, cold / 2)

    def test_a_keyword_new_far_away_masks_again_only_where_its_rule_finds_a_secret(self):
        text = sprinkled(900_000, 5)
        self.assertNotIn("facebook", text.lower())
        fresh_memo()
        mask(text)
        calls, patch = counting()
        with patch:
            mask(text + "\nsee the facebook docs\n")
        self.assertLess(sum(calls), 3 * masking.PIECE + 4 * masking.REACH)


class FarKeywords(unittest.TestCase):
    """gitleaks reads a rule only where the text holds a keyword of it, which may be pieces away from its secret."""

    def test_a_keyword_that_comes_later_masks_a_secret_pieces_before_it(self):
        rule = next(r for r in masking._GITLEAKS if r[3] == ("facebook",))  # its secret holds no keyword
        token = samples.samples(rule[0], 1, 7)[0]
        text = filler(3000, 31) + "\n" + token + "\n" + filler(9000, 32)
        with sizes(4096, 2048, 512):
            fresh_memo()
            self.assertIn(token, mask(text))
            grown = text + "\nsee the facebook docs\n"
            masked = mask(grown)  # the memo holds the piece with the token, as read with no keyword
            self.assertNotIn(token, masked)
            self.assertEqual(masked, masking._mask_whole(grown))
            self.assertIn(token, mask(text))

    def test_what_every_match_of_a_rule_holds(self):
        # A rule is skipped in a piece that lacks all its keywords only where the piece lacks what each match holds.
        checked = 0
        for i, rule in enumerate(masking._GITLEAKS + masking._GITLEAKS_AFTER_A_BREAK):
            needed = masking._NEEDED[rule[0]]
            if needed is None:
                continue
            for drawn in samples.samples(rule[0], 8, 500 + i):
                found = rule[0].search(drawn)
                self.assertTrue(any(n in found.group(0).lower() for n in needed), (needed, drawn))
                checked += 1
        self.assertGreater(checked, 500)
        self.assertGreater(sum(v is not None for v in masking._NEEDED.values()), 170)


class Memo(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "cache", "mask-memo.sqlite")
        fresh_memo()

    def tearDown(self):
        fresh_memo()
        self.dir.cleanup()

    def test_a_later_run_finds_what_an_earlier_one_masked(self):
        text = sprinkled(400_000, 5)
        with masking.memo(self.path):
            first = mask(text)
            short = mask("password=" + "x7Rt9Lm2" * 200)
        fresh_memo()
        with mock.patch.object(masking, "_masked") as read, mock.patch.object(masking, "_mask_whole") as whole:
            with masking.memo(self.path):
                self.assertEqual(mask(text), first)
                self.assertEqual(mask("password=" + "x7Rt9Lm2" * 200), short)
        read.assert_not_called()
        whole.assert_not_called()

    def test_without_the_memo_nothing_short_is_kept(self):
        mask("password=" + "x7Rt9Lm2" * 200)
        self.assertEqual(len(masking._MEMO.kept), 0)
        self.assertFalse(os.path.exists(self.path))

    def test_the_file_holds_no_text_and_no_plain_hash_of_it(self):
        secret = "Sup3rS3cretPassw0rd-unique-4417"
        text = "curl -u admin:" + secret + " https://example.com\n" + filler(5000, 8)
        with masking.memo(self.path):
            self.assertNotIn(secret, mask(text))
        with open(self.path, "rb") as fh:
            data = fh.read()
        for value in (text, secret):
            for digest in (hashlib.sha256(value.encode()).digest(), hashlib.sha1(value.encode()).digest()):
                self.assertNotIn(digest, data)
            self.assertNotIn(value.encode(), data)
        for path in (self.path, self.path + ".key"):
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode) & 0o077, 0, path)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode) & 0o077, 0)

    def test_a_new_mask_version_reads_nothing_old(self):
        text = "password=" + "x7Rt9Lm2" * 200
        with masking.memo(self.path):
            mask(text)
        fresh_memo()
        with mock.patch.object(masking, "VERSION", masking.VERSION + "-next"), \
                mock.patch.object(masking, "_mask_whole", return_value="new") as whole:
            with masking.memo(self.path):
                self.assertEqual(mask(text), "new")
        whole.assert_called_once()

    def test_a_stored_row_is_kept_apart(self):
        text = "password=" + "x7Rt9Lm2" * 200
        with masking.memo(self.path):
            plain = mask(text)
            with masking.stored_row("2023-01-01"):
                with mock.patch.object(masking, "_mask_whole", return_value="as stored") as whole:
                    self.assertEqual(mask(text), "as stored")
                whole.assert_called_once()
            self.assertEqual(mask(text), plain)


if __name__ == "__main__":
    unittest.main()
