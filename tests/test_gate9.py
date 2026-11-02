"""The gate 9 re-check's findings (2026-11-02, at main 4d7a130), each masked as a reader stores it: a token behind a
terminal colour code, a password an agent types into a page, and the shapes after them. Made-up secrets only."""
import json
import os
import random
import string
import tempfile
import unittest

from kb import store
from kb.mask import mask, mask_all
from kb.sources import claude_code, codex

TOKENS = ("ghp_Zq7Lm2Xc9Vb4Nn8Kk3Jj6Hh1Gg5Ff0Dd2Ss", "sk_live_51Hq8ZtKx9Lm2Np4Qr6St8Uv", "AKIAQX7RZ2MPL4VN8KTB",
          "sk-proj-Ab3Cd5Ef7Gh9Ij1Kl3Mn5Op7Qr9St1Uv3Wx5",
          "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk")
PASSWORD = "Tr0ub4dor-Zk9q"
# How a terminal colour code reaches kb: raw, escaped in JSON, written as Python writes it, in octal, and as the
# eight-bit introducer.
FORMS = {"raw": "\x1b[", "json": "\\u001b[", "python": "\\x1b[", "octal": "\\033[", "eight-bit": "\x9b"}


def coloured(form, token):
    """The token as grep --color and ripgrep print a match, in one of FORMS."""
    c = FORMS[form]
    return (f"config/old.env:3:GITHUB={c}01;31m{c}K{token}{c}m{c}K\n"
            f"{c}0m{c}35mdeploy/notes.md{c}0m:{c}0m{c}32m12{c}0m:aws id {c}0m{c}1m{c}31m{token}{c}0m\n")


def stored(reader, records):
    """What a reader stores of a session made of `records`, through store_item, as one text."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "rollout-2026-11-01T10-00-00-00000000-0000-0000-0000-000000000001.jsonl"
                            if reader == "codex" else "s-gate9.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n")
        item = codex.render([path], 0) if reader == "codex" else claude_code.render(path, 0)
        db = store.connect(os.path.join(tmp, "kb.sqlite"))
        try:
            store.store_item(db, reader, item.source_id, item.ts, item.title, item.content, item.meta)
            return "".join(r[0] for r in db.execute("SELECT content FROM docs ORDER BY id"))
        finally:
            db.close()


def codex_session(*payloads):
    return [{"type": "session_meta", "timestamp": "2026-11-01T10:00:00Z", "payload": {"id": "c-g9", "cwd": "/tmp/p"}},
            {"type": "response_item", "timestamp": "2026-11-01T10:00:00Z",
             "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "do it"}]}},
            *({"type": "response_item", "timestamp": "2026-11-01T10:00:01Z", "payload": p} for p in payloads)]


def claude_session(*parts, role="assistant"):
    return [{"type": "user", "timestamp": "2026-11-01T10:00:00Z", "cwd": "/tmp/p", "sessionId": "s-g9",
             "message": {"content": "do it"}},
            {"type": role, "timestamp": "2026-11-01T10:00:01Z", "message": {"content": list(parts)}}]


def tool_call(reader, name, args):
    if reader == "codex":
        return codex_session({"type": "function_call", "name": name, "arguments": json.dumps(args)})
    return claude_session({"type": "tool_use", "name": name, "input": args})


class ColourCodeTest(unittest.TestCase):
    """A terminal colour code before a token is a break, as NUL is: the token is masked, the codes stay."""

    def test_a_token_behind_a_colour_code_is_masked_and_the_codes_stay(self):
        for form in FORMS:
            for token in TOKENS:
                with self.subTest(form=form, token=token[:8]):
                    text = coloured(form, token)
                    self.assertEqual(mask(text), text.replace(token, "[masked]"))

    def test_a_coloured_token_is_masked_as_both_readers_store_it(self):
        for form in ("raw", "json"):
            for token in TOKENS:
                out = coloured(form, token)
                for reader, records in (
                        ("codex", codex_session(
                            {"type": "function_call", "name": "exec_command",
                             "arguments": json.dumps({"cmd": "grep -rn --color=always ghp_ config"})},
                            {"type": "function_call_output",
                             "output": "Chunk ID: 1\nProcess exited with code 0\nOutput:\n" + out})),
                        ("claude_code", claude_session({"type": "tool_result", "content": [{"type": "text", "text": out}]},
                                                       role="user"))):
                    with self.subTest(form=form, token=token[:8], reader=reader):
                        text = stored(reader, records)
                        self.assertNotIn(token[:16], text)
                        self.assertIn("aws id", text)

    def test_colour_codes_with_no_token_change_nothing(self):
        for form in FORMS:
            c = FORMS[form]
            text = f"{c}1;32mPASS{c}0m tests/test_mask.py {c}33mwarning{c}0m: 3 skipped\n"
            with self.subTest(form=form):
                self.assertEqual(mask(text), text)


# The labels a page gives a field that takes a secret, and some that take none.
SECRET_LABELS = ("Password", "Password textbox", "Confirm password", "Passcode", "PIN", "Enter your PIN", "Secret key",
                 "API token", "Access token", "One-time code", "passphrase", "current-password")
ORDINARY_LABELS = ("Email", "Search", "Full name", "Message", "Street address", "Username", "Company", "Comment")
ORDINARY_VALUES = ("me@example.com", "pelican plan", "Sam Lee", "12 Harbour Road", "the launch is approved", "sam")


def typing_calls(r, label, value):
    """Each real input shape of a tool that types into a page or fills a form, with `value` typed into a field
    labelled `label`: (tool name, input)."""
    ref = f"e{r.randint(2, 99)}"
    return [("mcp__playwright__browser_type", {"element": f"{label}", "ref": ref, "text": value,
                                               **r.choice(({}, {"submit": True}, {"slowly": False}))}),
            ("mcp__playwright__browser_fill_form",
             {"fields": [{"name": "Email", "type": "textbox", "ref": "e5", "value": "me@example.com"},
                         {"name": label, "type": "textbox", "ref": ref, "value": value}]}),
            ("mcp__puppeteer__puppeteer_fill", {"selector": f"input[name={label.lower().replace(' ', '_')}]",
                                                "value": value})]


class TypedSecretTest(unittest.TestCase):
    """A value an agent types into a field labelled for a secret is masked whole, as each reader stores it."""

    def test_the_audits_three_shapes(self):
        for reader in ("codex", "claude_code"):
            for name, args in (("mcp__playwright__browser_type",
                                {"element": "Password textbox", "ref": "e23", "text": PASSWORD, "submit": True}),
                               ("mcp__playwright__browser_fill_form",
                                {"fields": [{"name": "Email", "type": "textbox", "ref": "e5", "value": "me@x.com"},
                                            {"name": "Password", "type": "textbox", "ref": "e7", "value": PASSWORD}]}),
                               ("mcp__chrome-devtools__fill", {"uid": "1_7", "value": PASSWORD})):
                with self.subTest(reader=reader, name=name):
                    text = stored(reader, tool_call(reader, name, args))
                    self.assertNotIn(PASSWORD, text)
                    self.assertNotIn("Zk9q", text)

    def test_codex_keeps_the_fields_label(self):
        text = stored("codex", tool_call("codex", "mcp__playwright__browser_type",
                                         {"element": "Password textbox", "ref": "e23", "text": PASSWORD}))
        self.assertIn("Password textbox", text)

    def test_every_shape_masks_a_secret_label_and_keeps_an_ordinary_one(self):
        alnum = string.ascii_letters + string.digits
        for seed in range(150):
            r = random.Random(f"typed:{seed}")
            secret = "".join(r.choice(alnum) for _ in range(r.randint(6, 16))) + r.choice("!#%*")
            for label, value, hidden in ((r.choice(SECRET_LABELS), secret, True),
                                         (r.choice(ORDINARY_LABELS), r.choice(ORDINARY_VALUES), False)):
                for name, args in typing_calls(r, label, value):
                    for reader in ("codex", "claude_code"):
                        with self.subTest(seed=seed, reader=reader, name=name, label=label):
                            text = stored(reader, tool_call(reader, name, args))
                            if hidden:
                                self.assertNotIn(value, text)
                            else:
                                self.assertIn(value, text)

    def test_an_unlabelled_fill_masks_a_credential_and_keeps_ordinary_text(self):
        for value, hidden in ((PASSWORD, True), ("q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1", True), ("pelican plan", False),
                              ("me@example.com", False), ("Sam Lee", False), ("2026-11-02", False)):
            with self.subTest(value=value):
                text = stored("claude_code", tool_call("claude_code", "mcp__chrome-devtools__fill",
                                                       {"uid": "1_7", "value": value}))
                self.assertEqual(value not in text, hidden)

    def test_typed_secrets_through_mask_all_alone(self):
        from kb.mask import typed_secrets
        masked = typed_secrets("mcp__playwright__browser_type", {"element": "Password", "ref": "e2", "text": PASSWORD})
        self.assertEqual(masked["text"], "[masked]")
        self.assertEqual(masked["element"], "Password")
        self.assertEqual(typed_secrets("mcp__playwright__browser_type", {"element": "Search", "text": "pelican"}),
                         {"element": "Search", "text": "pelican"})
        self.assertNotIn(PASSWORD, json.dumps(mask_all(typed_secrets("mcp__chrome-devtools__fill_form", {
            "elements": [{"uid": "1_3", "value": "Sam"}, {"uid": "1_7", "value": PASSWORD}]}))))


if __name__ == "__main__":
    unittest.main()
