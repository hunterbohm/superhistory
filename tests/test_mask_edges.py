"""mask_head(text, n) and mask_tail(text, n) (kb.mask) are mask(text)[:n] and mask(text)[-n:], read from a window of a
long text, and the text masked whole wherever the window does not hold.

Every test of equality compares with the text read whole (_mask_whole), at sizes small enough to run fast (as
tests/test_mask_long.py does), and says whether the answer came from a window: a test that puts a secret where a
window could go wrong must see the window refused, and one that puts it where it holds must see it used."""
import contextlib
import random
import unittest
from unittest import mock

from kb import mask as masking
from tests import samples
from tests.test_mask_long import CASES, counting, filler, fresh_memo, sizes

N = 300
KEY_LINES = "\n".join("MII" + "Q" * 61 for _ in range(80))
PEM = "-----BEGIN PRIVATE KEY-----\n" + KEY_LINES + "\n-----END PRIVATE KEY-----"


def paragraphs(size, seed, each=300):
    """Plain prose of `size` characters that masks to itself, in paragraphs of about `each`."""
    r, out, total = random.Random(seed), [], 0
    while total < size:
        out.append(filler(r.randint(each // 2, 2 * each), r.random()).rstrip() + "\n\n")
        total += len(out[-1])
    return "".join(out)[:size]


def dense(size, seed, each=(3000, 9000)):
    """Paragraphs of prose with a secret after each, as an agent's session holds them, and blank lines between."""
    r, out, total = random.Random(seed), [], 0
    while total < size:
        out.append(paragraphs(r.randint(*each), r.random()))
        out.append("\n" + r.choice(CASES) + "\n\n")
        total += len(out[-2]) + len(out[-1])
    return "".join(out)


class Edges(unittest.TestCase):
    """The packages' own sizes divided by 64: REACH 512, PIECE 1536, a window of N characters at least 1,324 long."""

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(sizes(3072, 1536, 512))
        fresh_memo()

    def read(self, text, n=N, whole=None):
        """(mask_head, mask_tail, whether each came from a window), after checking both are the text read whole's."""
        whole = masking._mask_whole(text) if whole is None else whole
        results, real = [], masking._edge
        with mock.patch.object(masking, "_edge", side_effect=lambda *a: results.append(real(*a)) or results[-1]):
            fresh_memo()
            head, tail = masking.mask_head(text, n), masking.mask_tail(text, n)
        self.assertEqual(head, whole[:n], "head")
        self.assertEqual(tail, whole[-n:], "tail")
        return head, tail, [found is not None for found in results]

    def test_a_short_text_is_masked_whole(self):
        text = paragraphs(N + 4 * masking.REACH - 40, 1) + "password=" + "x7Rt9Lm2" * 2
        with mock.patch.object(masking, "_edge") as edge:
            self.assertEqual(masking.mask_head(text, N), masking.mask(text)[:N])
            self.assertEqual(masking.mask_tail(text, N), masking.mask(text)[-N:])
            self.assertEqual(masking.mask_head(text, 10 * len(text)), masking.mask(text))
            self.assertEqual((masking.mask_head(text, 0), masking.mask_tail(text, 0)), ("", masking.mask(text)[-0:]))
        edge.assert_not_called()

    def test_only_a_window_is_read_of_a_long_text(self):
        text = paragraphs(9000, 2)
        calls, patch = counting()
        with patch:
            head, tail, windowed = self.read(text, whole=text)
        self.assertEqual(windowed, [True, True])
        self.assertLessEqual(max(calls), N + 2 * masking.REACH + masking.PIECE)
        self.assertLess(sum(calls), len(text))

    def test_a_secret_at_the_kept_edge(self):
        for k, secret in enumerate(CASES):
            head_text = paragraphs(700, k) + "\n" + secret + "\n\n" + paragraphs(7000, k + 1)
            tail_text = paragraphs(7000, k) + "\n\n" + secret + "\n" + filler(260, k + 1)
            head_whole, tail_whole = masking._mask_whole(head_text), masking._mask_whole(tail_text)
            self.assertNotEqual(head_whole, head_text, "the case must hold a secret")
            first, last = head_whole.index("[masked]"), tail_whole.rindex("[masked]")
            # A secret with REACH // 8 characters and no three letters in a row could be a gap (_wordless), at these
            # small sizes: a window that reaches it may be refused, and the text is then masked whole, still right.
            wordless = masking._wordless(secret)
            for off in (0, 1, 4, 8):
                with self.subTest(case=k, off=off):
                    head = self.read(head_text, first + off, head_whole)[2][0]
                    tail = self.read(tail_text, len(tail_whole) - last - off, tail_whole)[2][1]
                    self.assertTrue(wordless or (head and tail))

    def test_a_secret_across_the_cut(self):
        accepted = []
        for k, secret in enumerate(CASES):
            text = paragraphs(2400, k) + secret + "\n\n" + paragraphs(5000, k + 1)
            at = text.index(secret)
            for cut in (at + 1, at + len(secret) // 2, at + len(secret) - 1):
                with self.subTest(case=k, cut=cut - at):
                    with mock.patch.object(masking, "_edge_cut", return_value=cut):
                        accepted += self.read(text)[2]
        # A window cut in a secret is used where its edge character is not one the secret hides, and always right.
        self.assertGreater(sum(accepted), len(accepted) // 4)

    def test_a_field_any_gap_apart_from_its_value(self):
        # A field and its value may stand any gap apart: a window or a piece cut in that gap would keep the value
        # without the field that makes it a secret. Each ends as the text read whole does; where that hides the value,
        # no window is used.
        gaps = ("\n\n" * masking.REACH, "\n" * (3 * masking.REACH), "\n\n\n" + "\t" * (3 * masking.REACH),
                "+\n\n" * masking.REACH, "%20\n\n" * masking.REACH, "%2520\n\n" * masking.REACH,
                "\\n\\n\n\n" * masking.REACH, "\\u0020\n\n" * masking.REACH, "\\ufeff\n\n" * masking.REACH,
                "\ufeff\u200b\n\n" * masking.REACH)
        hidden = 0
        for gap in gaps:
            for field in ("password:", "Bearer"):
                with self.subTest(gap=gap[:6], field=field):
                    tail_text = paragraphs(3000, 4) + field + gap + "swordfishswordfish\n"
                    head_text = field + gap + "swordfishswordfish\n\n" + paragraphs(5000, 5)
                    tail_whole = masking._mask_whole(tail_text)
                    windowed = self.read(tail_text, N, tail_whole)[2]
                    self.read(head_text, len(head_text) - 5000)
                    if "swordfish" not in tail_whole:
                        hidden += 1
                        self.assertEqual(windowed[1], False)
        self.assertGreater(hidden, 0, "some gap must make the value a secret")

    def test_a_cut_near_the_kept_part_is_refused(self):
        text = paragraphs(7000, 3)
        near = lambda text, n, head: N + 100 if head else len(text) - N - 100
        with mock.patch.object(masking, "_edge_cut", side_effect=near):
            self.assertEqual(self.read(text)[2], [False, False])

    def test_a_key_paragraph_that_runs_on_to_the_end_of_the_window(self):
        # A key with no footer is masked to the end of its paragraph: where the window ends inside it, the window's last
        # character is hidden, the blank lines before the key notwithstanding, and the text is masked whole.
        text = (paragraphs(1300, 3, each=100) + "-----BEGIN PRIVATE KEY-----\n" + KEY_LINES + "\n\n"
                + paragraphs(6000, 4))
        cut = text.index("-----BEGIN") + 2000
        with mock.patch.object(masking, "_edge_cut", return_value=cut):
            head, tail, windowed = self.read(text)
        self.assertEqual(windowed[0], False)
        whole = masking._mask_whole(text)
        self.assertNotIn("QQQQ", whole)
        self.read(text, 3000, whole)

    def test_a_key_paragraph_that_comes_in_over_the_tails_cut(self):
        # The window starts in the key's body, past the header that makes it a key: it reads the body as prose, and
        # only the blank lines it must hold between the cut and the kept part keep the text from being read so.
        text = paragraphs(1800, 5) + "-----BEGIN PRIVATE KEY-----\n" + KEY_LINES
        cut = text.index("-----BEGIN") + 1200
        whole = masking._mask_whole(text)
        self.assertTrue(whole.endswith("[masked]"))
        naive = masking._part(text[cut:])[0]
        self.assertNotEqual(naive[-N:], whole[-N:], "a window that starts in the body reads it as it is")
        with mock.patch.object(masking, "_edge_cut", return_value=cut):
            self.assertEqual(self.read(text, whole=whole)[2], [False, False])
        self.read(text, whole=whole)

    def test_a_footer_with_no_header_after_the_heads_cut(self):
        # Masked back from the footer to the start of its paragraph, where no blank line comes first.
        lines = "\n".join(filler(70, i).strip() for i in range(90)) + "\n"
        text = lines + "-----END PRIVATE KEY-----\n\n" + paragraphs(5000, 6)
        whole = masking._mask_whole(text)
        self.assertTrue(whole.startswith("[masked]"))
        cut = text.index("-----END") - 700
        naive = masking._part(text[:cut])[0]
        self.assertNotEqual(naive[:N], whole[:N], "a window that ends before it reads it as it is")
        with mock.patch.object(masking, "_edge_cut", return_value=cut):
            self.assertEqual(self.read(text, whole=whole)[2][0], False)
        self.read(text, whole=whole)

    def test_a_key_that_hides_the_start_is_masked_whole(self):
        # The first N characters of the mask come from past the key, and the blank lines are counted from 2 * N on.
        text = PEM + "\n\n" + paragraphs(8000, 7)
        head, tail, windowed = self.read(text)
        self.assertEqual(windowed[0], False)
        self.assertTrue(head.startswith("[masked]"))

    def test_a_text_the_mask_hides_all_of_is_masked_whole(self):
        text = "-----BEGIN PRIVATE KEY-----\n" + KEY_LINES + "\n" + KEY_LINES
        self.assertEqual(self.read(text, whole="[masked]"), ("[masked]", "[masked]", [False, False]))

    def test_a_text_with_too_few_blank_lines_is_masked_whole(self):
        for text in (filler(9000, 8), filler(4000, 9) + "\n\n" + filler(3000, 10) + "\n\n" + filler(3000, 11)):
            with self.subTest(text[:10]):
                self.assertEqual(self.read(text)[2], [False, False])

    def test_blank_lines_far_away_widen_the_window(self):
        text = filler(5000, 13) + "\n\n\n\n\n\n" + filler(2000, 14)
        head, tail, windowed = self.read(text)
        self.assertEqual(windowed, [False, True])
        with mock.patch.object(masking, "PIECE", 8192):
            self.assertEqual(self.read(text)[2], [True, True])

    def test_a_window_that_still_changes_is_refused(self):
        text = paragraphs(9000, 15)
        real = masking._part_record

        def still(window):
            (result, origin, _), record = real(window)
            return (result, origin, True), record
        with mock.patch.object(masking, "_part_record", side_effect=still):
            self.assertEqual(self.read(text)[2], [False, False])

    def test_inside_a_mask_the_text_is_read_whole(self):
        text = paragraphs(9000, 16)
        token = masking._TOP.set(False)
        try:
            self.assertEqual(self.read(text)[2], [False, False])
        finally:
            masking._TOP.reset(token)

    def test_a_keyword_only_outside_the_window(self):
        # gitleaks' rule for a Facebook secret reads a text only where it holds "facebook", which may be far from it.
        rule = next(r for r in masking._GITLEAKS if r[3] == ("facebook",))
        token = samples.samples(rule[0], 1, 7)[0]
        self.assertEqual(masking._mask_whole("id " + token), "id " + token)
        head_text = paragraphs(300, 17) + "\n" + token + "\n\n" + paragraphs(7000, 18) + "see facebook docs\n\n"
        tail_text = "see facebook docs\n\n" + paragraphs(7000, 19) + "\n" + token + "\n\n" + filler(150, 20)
        # The keyword is in a view of the text only the far text makes, which _keywords_in does not model: the window
        # that lacks the keyword and finds the secret cannot be read as the text is. The other end holds the keyword.
        for text, unmodelled in ((head_text, [False, True]), (tail_text, [True, False])):
            with self.subTest(text[:12]):
                whole = masking._mask_whole(text)
                self.assertNotIn(token, whole)
                self.assertEqual(self.read(text, 400, whole)[2], [True, True])
                with mock.patch.object(masking, "_keywords_in", return_value=frozenset()):
                    self.assertEqual(self.read(text, 400, whole)[2], unmodelled)

    def test_a_keyword_the_window_holds_needs_no_look_at_the_text(self):
        rule = next(r for r in masking._GITLEAKS if r[3] == ("facebook",))
        token = samples.samples(rule[0], 1, 7)[0]
        text = paragraphs(300, 21) + "facebook " + token + "\n\n" + paragraphs(7000, 22)
        whole = masking._mask_whole(text)
        self.assertNotIn(token, whole)
        with mock.patch.object(masking, "_keywords_in", side_effect=AssertionError("looked at the text")):
            self.assertEqual(masking.mask_head(text, 400), whole[:400])

    def test_random_texts(self):
        encrypted = "Proc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,0123456789ABCDEF\n\n" + KEY_LINES
        blocks = [PEM, "-----BEGIN PRIVATE KEY-----\n" + KEY_LINES, KEY_LINES + "\n-----END PRIVATE KEY-----",
                  "-----BEGIN PGP PRIVATE KEY BLOCK-----\n\n" + KEY_LINES, encrypted]
        windowed = []
        for seed in range(14):
            r = random.Random(seed)
            parts = []
            for _ in range(r.randint(6, 12)):
                kind = r.random()
                parts.append(paragraphs(r.randint(200, 1400), r.random()) if kind < 0.45 else
                             filler(r.randint(100, 800), r.random(), breaks=r.random() < 0.7) if kind < 0.65 else
                             r.choice(CASES) + r.choice(["\n", "\n\n", " "]) if kind < 0.9 else
                             r.choice(blocks) + r.choice(["\n", "\n\n"]))
            text = "".join(parts)
            if len(text) <= 3000:
                text += paragraphs(3000, seed)
            for n in (150, 300, 450):
                with self.subTest(seed=seed, n=n):
                    windowed += self.read(text, n)[2]
        self.assertGreater(sum(windowed), len(windowed) // 4, "the windows must be used often, not only refused")


class FullSize(unittest.TestCase):
    """The package's own sizes: a window of the 4,000 characters a message keeps is 76,000 long."""

    def test_the_two_ends_of_a_long_text_read_as_the_text_read_whole(self):
        text = dense(170_000, 23)
        message = text[:3980] + "\npassword: " + "x7Rt9Lm2Kq4" * 4 + "\n" + text[3980:]
        whole = masking._mask_whole(message)
        self.assertIn("[masked]", whole[3900:4100])
        fresh_memo()
        self.assertEqual(masking.mask_head(message, 4000), whole[:4000])
        self.assertEqual(masking.mask_head(message, 600), whole[:600])
        self.assertEqual(masking.mask_tail(message, 1500), whole[-1500:])

    def test_a_very_long_text_is_not_read_whole(self):
        text = dense(600_000, 24)
        calls, patch = counting()
        with patch, mock.patch.object(masking, "_mask_whole", side_effect=AssertionError("read whole")):
            fresh_memo()
            head, tail = masking.mask_head(text, 4000), masking.mask_tail(text, 1500)
        self.assertEqual((len(head), len(tail)), (4000, 1500))
        self.assertLessEqual(max(calls), 4000 + 2 * masking.REACH + masking.PIECE)


if __name__ == "__main__":
    unittest.main()
