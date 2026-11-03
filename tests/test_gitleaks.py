"""kb's copy of gitleaks' provider token rules (kb/gitleaks.py, written by tools/vendor_gitleaks.py). A test per rule,
on examples drawn from the rule's own pattern as gitleaks draws its own (tests/samples.py): each is masked where a
token stands in text, in a link's query, in JSON, after a name, behind a terminal colour code and split by an
invisible character. No rule matches the ordinary-text generator, nor does mask() change any of it. Made-up secrets
only: every example is drawn at random."""
import json
import os
import re
import sys
import time
import unittest

from kb import gitleaks, mask as mask_module
from kb.mask import mask
from tests import ordinary, samples

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import vendor_gitleaks  # noqa: E402


def examples(index, n=6):
    """Up to `n` secrets drawn from rule `index`'s own pattern that the rule reads as one."""
    pattern, group, entropy, _, allowed, stopwords = mask_module._GITLEAKS[index]
    out = []
    for text in samples.samples(pattern, n * 4, gitleaks.RULES[index][0]):
        m = pattern.fullmatch(text)
        span = m and mask_module._gitleaks_secret(m, group, entropy, allowed, stopwords)
        if span and text[span[0]:span[1]] not in out:
            out.append(text[span[0]:span[1]])
            if len(out) == n:
                break
    return out


def places(secret, keywords):
    """Where a token stands in text kb reads. A rule is read only where a keyword of it is in the text, as gitleaks
    reads it, so where the token holds none, one stands before it, as a provider's name does."""
    half = len(secret) // 2
    word = "" if not keywords or any(k in secret.lower() for k in keywords) else keywords[0] + " "
    return tuple(word + text for text in (
        secret, f"token: {secret}\n", f"export DEPLOY_TOKEN={secret}", json.dumps({"value": secret}),
        f"use {secret} for the deploy", f"curl 'https://api.example.com/v1?key={secret}'",
        f"key \x1b[1;31m{secret}\x1b[0m done", f"key {secret[:half]}\u200b{secret[half:]} done",
        f"{secret},active\n", f"tokens: [{secret}]", f"(see {secret})."))


class RuleTest(unittest.TestCase):
    """One test per rule, added below."""


def _rule_test(index):
    def test(self):
        found = examples(index)
        self.assertTrue(found, "the rule's own pattern gives no example")
        for secret in found:
            for text in places(secret, gitleaks.RULES[index][4]):
                with self.subTest(text=text):
                    masked = mask(text)
                    self.assertNotIn(secret, masked)
                    self.assertNotIn(secret[-12:], masked)
    return test


for _index, _rule in enumerate(gitleaks.RULES):
    setattr(RuleTest, "test_" + re.sub(r"\W", "_", _rule[0]), _rule_test(_index))


class TableTest(unittest.TestCase):
    def test_no_rule_matches_ordinary_text_and_the_mask_keeps_it(self):
        texts = ordinary.texts(3000, "gitleaks")
        for text in texts:
            spans = mask_module._gitleaks_spans(text)
            self.assertEqual(spans, [], f"{text!r}: {[text[s:e] for s, e in spans]}")
            self.assertEqual(mask(text), text)

    def test_the_table_names_its_release_and_carries_its_licence(self):
        self.assertRegex(gitleaks.VERSION, r"^v\d+\.\d+\.\d+$")
        self.assertRegex(gitleaks.SOURCE_SHA256, r"^[0-9a-f]{64}$")
        for line in ("MIT License", "Copyright (c) 2019 Zachary Rice", "Permission is hereby granted, free of charge",
                     'THE SOFTWARE IS PROVIDED "AS IS"'):
            self.assertIn(line, gitleaks.__doc__)
        self.assertGreater(len(gitleaks.RULES), 50)
        for rule in gitleaks.RULES:
            re.compile(rule[1], re.ASCII)

    def test_the_audits_unknown_tokens_are_masked(self):
        ids = {rule[0]: i for i, rule in enumerate(gitleaks.RULES)}
        for rule_id in ("pypi-upload-token", "shopify-access-token", "sendgrid-api-token", "digitalocean-pat",
                        "vault-service-token", "sentry-org-token", "databricks-api-token"):
            with self.subTest(rule=rule_id):
                for secret in examples(ids[rule_id], 3):
                    self.assertEqual(mask(f"the {secret} one"), "the [masked] one")

    def test_go_patterns_read_as_python_ones(self):
        python = vendor_gitleaks.python_regex
        self.assertEqual(python(r"\b(p8e-(?i)[a-z0-9]{32})(?:[\x60'\"\s;]|$)"),
                         r"\b(p8e-(?i:[a-z0-9]{32}))(?:[\x60'\"\s;]|$)")
        self.assertEqual(python(r"dp\.pt\.(?i)[a-z0-9]{43}"), r"dp\.pt\.(?i:[a-z0-9]{43})")
        self.assertEqual(python(r"(?i)\bx(?i)[a-z]{3}"), r"(?i)\bx(?i:[a-z]{3})")
        self.assertEqual(python(r"\b(pat[[:alnum:]]{14})\b"), r"\b(pat[A-Za-z0-9]{14})\b")
        self.assertEqual(python(r"(?:[^\w-]|\z)"), r"(?:[^\w-]|\Z)")
        self.assertEqual(python(r"[(?i)]x"), r"[(?i)]x")
        ahead = vendor_gitleaks.terminator_ahead
        self.assertEqual(ahead(r"\bsntrys_[a-z]{4}(?:[^a-zA-Z0-9+/]|\Z)"), r"\bsntrys_[a-z]{4}(?=[^a-zA-Z0-9+/]|\Z)")
        self.assertEqual(ahead(r"\b(x[a-z]{4})(?:$|[\\'<),])"), r"\b(x[a-z]{4})(?=$|[\\'<),])")
        self.assertEqual(ahead(r"x(?:ab|cd)"), r"x(?:ab|cd)")

    def test_texts_full_of_keywords_cost_time_in_proportion(self):
        took = {}
        for times in (300, 2400):
            text = "".join(f"{rule[4][0]} " for rule in gitleaks.RULES if rule[4]) * times
            started = time.process_time()
            mask(text)
            took[times] = time.process_time() - started
        self.assertLess(took[2400], 16 * max(took[300], 0.01))


if __name__ == "__main__":
    unittest.main()
