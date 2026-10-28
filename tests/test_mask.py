import unittest
from unittest import mock

from kb.mask import is_auth_code, is_auth_noise, mask, secret_in_id


class MaskTest(unittest.TestCase):
    def assert_masked(self, text, secret):
        out = mask(text)
        self.assertNotIn(secret, out)
        self.assertIn("[masked]", out)

    def test_known_key_formats(self):
        cases = {
            "anthropic": "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
            "openai": "sk-proj1234567890abcdefghijklmnop",
            "stripe live": "sk_live_4eC39HqLyjWDarjtT1zdp7dc",
            "stripe restricted": "rk_live_4eC39HqLyjWDarjtT1zdp7dc",
            "github": "ghp_" + "a1B2c3D4e5" * 4,
            "github fine grained": "github_pat_11ABCDEFG0abcdefghijklmnop",
            "slack": "xoxb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx",
            "aws": "AKIAIOSFODNN7EXAMPLE",
            "google": "AIza" + "SyA-9fGhIjKlMnOpQrStUvWxYz0123456_x",
            "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
            "bearer": "Bearer abcdEFGH1234567890xyzXYZ.-_~+/",
        }
        for name, secret in cases.items():
            with self.subTest(name):
                self.assert_masked(f"here you go {secret} thanks", secret.split(" ")[-1])

    def test_pem_private_key_block(self):
        pem = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA7vbqajDw\nabc123def456\n"
               "-----END RSA PRIVATE KEY-----")
        out = mask(f"before\n{pem}\nafter")
        self.assertEqual(out, "before\n[masked]\nafter")

    def test_keyed_secrets(self):
        cases = [
            ("password: sw0rdfish9", "sw0rdfish9"),
            ("api_key=abcd1234efgh", "abcd1234efgh"),
            ('"client_secret": "Zx81kqPmWn30"', "Zx81kqPmWn30"),
            ("the password is Tr0ub4dor3", "Tr0ub4dor3"),
            ("API-KEY = 0123456789abcdef0123", "0123456789abcdef0123"),
            ("token: abcdefghijklmnopqrstuvwx", "abcdefghijklmnopqrstuvwx"),
        ]
        for text, secret in cases:
            with self.subTest(text):
                out = mask(text)
                self.assertNotIn(secret, out)
                self.assertIn("[masked]", out)

    def test_leaks_found_in_review(self):
        cases = [
            ("export OPENAI_API_KEY=Qx7rT2mPa9LkW3vZ8nB4", "Qx7rT2mPa9LkW3vZ8nB4"),
            ("GITHUB_TOKEN=abcdef1234567890abcdef1234567890", "abcdef1234567890abcdef1234567890"),
            ("DATABASE_PASSWORD=pa55w0rd!", "pa55w0rd!"),
            ("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI/K7MDENG"),
            ('"GITHUB_TOKEN": "f00ba4f00ba4f00ba4f00ba4"', "f00ba4f00ba4f00ba4f00ba4"),
            ("CF_TOKEN=Zq9yXw8vUt7sRq6pOn5mLk4jIh3gFe2dCb1a", "Zq9yXw8vUt7sRq6pOn5mLk4jIh3gFe2dCb1a"),
            ("BLUEBUBBLES_PASSWORD=ab1x", "ab1x"),
            ('BW_SESSION="Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA=="', "Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA=="),
            ("Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==", "dXNlcjpwYXNzd29yZDEyMw=="),
            ("Cookie: session=abc123def456; csrftoken=zzz999", "abc123def456"),
            ("-H 'set-cookie: auth=Zz9Yy8Xx7Ww6Vv5'", "Zz9Yy8Xx7Ww6Vv5"),
            ("postgresql://admin:Sup3rS3cret@db.example.com:5432/app", "Sup3rS3cret"),
            ("curl -u sam:sw0rdfish9 https://api.example.com", "sw0rdfish9"),
            ("bot 1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1 ready", "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1"),
            ("https://discord.com/api/webhooks/123456789012345678/AbCdEf-GhIjKl_MnOp", "AbCdEf-GhIjKl_MnOp"),
            ("https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXXXXXX", "XXXXXXXXXXXXXXXX"),
            ("hf_" + "AbCdEfGhIjKlMnOpQrStUvWxYz012345", "AbCdEfGhIjKlMnOpQrStUvWxYz012345"),
            ("gsk_" + "a1" * 26, "a1" * 26),
            ("npm_" + "B2" * 18, "B2" * 18),
            ("tskey-auth-kAbc123CNTRL-XyZ789qwe", "kAbc123CNTRL-XyZ789qwe"),
            ("xai-" + "Q9" * 40, "Q9" * 40),
            ("st.0a1b2c3d-1111-2222-3333-444455556666.0123456789abcdef0123.fedcba9876543210fedc",
             "0123456789abcdef0123"),
            ("db_pw: abc1", "abc1"),
            ("ZAI_API_KEY=0a1b2c3d4e5f60718293a4b5c6d7e8f9.AbCdEfGh12345678", "AbCdEfGh12345678"),
            ("PORTAL_COMMAND_SIGNING_KEY=dev-1-signing-key-local", "dev-1-signing-key-local"),
        ]
        for text, secret in cases:
            with self.subTest(text):
                out = mask(text)
                self.assertNotIn(secret, out)
                self.assertIn("[masked]", out)

    def test_clipped_private_key_without_end_line(self):
        out = mask("RESULT: -----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ …\n\nUSER: next")
        self.assertNotIn("b3BlbnNzaC1rZXktdjEAAAAABG5vbmU", out)
        self.assertIn("USER: next", out)

    def test_mask_is_idempotent(self):
        text = "password: sw0rdfish9 and GITHUB_TOKEN=abcdef1234567890abcdef1234567890 Cookie: a=b1234567"
        once = mask(text)
        self.assertEqual(mask(once), once)

    def test_prose_and_code_near_secret_words_untouched(self):
        safe = [
            "the password is incorrect",
            "max_tokens: 8000",
            "input_tokens: 1234567890123456",
            "tokenizer: porter",
            "password: string;",
            "api_key=api_key",
            "permissionMode: bypassPermissions",
            "basic understanding of the system",
            "sessionId: 3d9e96c5-9b4f-4892-b70d-0f8ca9d12277",
            "https://mail.google.com/mail/u/?authuser=sam%40example.com#search",
            "token = self.token",
            "date -u '+%Y-%m-%dT%H:%M:%SZ'",
            "NOTICE:  PASS: lockout after five attempts",
            "Sturgeon Bay second pass: 85-90%",
            '"credentials_path": "/Users/sam/Library/Application Support/gcloud"',
            "ERROR: empty keychain secret: GOOGLE_API_KEY",
            "integration_confirmation_bounded_pass.md:360: source text",
            "passages: list[str] = []",
            "The email or password is incorrect. Please try again.",
            "cache_key = user123abc",
            "NEXT_PUBLIC_SUPABASE_ANON_KEY=process.env.X",
        ]
        for text in safe:
            with self.subTest(text):
                self.assertEqual(mask(text), text)

    def test_keyed_mask_keeps_the_key_name(self):
        self.assertEqual(mask("password: sw0rdfish9"), "password: [masked]")

    def test_all_caps_secret_is_masked_but_env_var_names_are_not(self):
        self.assertEqual(mask("password: ABCDEF123456"), "password: [masked]")
        self.assertEqual(mask("api_key: ZX81QWERTY99"), "api_key: [masked]")
        for text in ("password: DB_PASSWORD", "api_key = OPENAI_API_KEY2", "token: GITHUB_TOKEN"):
            with self.subTest(text):
                self.assertEqual(mask(text), text)

    def test_ordinary_text_and_code_untouched(self):
        safe = [
            "token: string",
            "password=os.environ['DB_PASSWORD']",
            'password = os.environ["DB_PASSWORD"]',
            "const token = process.env.GITHUB_TOKEN",
            "api_key = $OPENAI_API_KEY",
            "secret: ${SECRET_NAME}",
            "the password is required for login",
            "Please reset the token before the meeting tomorrow.",
            "A task-based risk-assessment of the disk-usage plan.",
            "Meeting at 3pm about the Q4 roadmap, bring the Bearer of good news.",
            "git commit -m 'fix auth token refresh'",
        ]
        for text in safe:
            with self.subTest(text):
                self.assertEqual(mask(text), text)

    def test_surrounding_text_survives(self):
        out = mask("key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWx and more words")
        self.assertEqual(out, "key [masked] and more words")


class HeadlessKeyTest(unittest.TestCase):
    """A key's body without its header, as an older kb's row-by-row remask leaves the chunk after the header's."""
    LINE = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7"

    def test_body_lines_up_to_the_footer_are_masked(self):
        for brk in ("\n", "\r\n", "\\n", "\\r\\n"):
            with self.subTest(brk=brk):
                text = f"[masked]{self.LINE}{brk}{self.LINE}{brk}-----END PRIVATE KEY-----{brk}after"
                self.assertEqual(mask(text), f"[masked]{brk}after")
                # A footer with no header masks its paragraph up to it; a blank line ends that paragraph.
                self.assertEqual(mask(text.replace("[masked]", "notes\n")), f"[masked]{brk}after")
                self.assertEqual(mask(text.replace("[masked]", "notes\n\n")), f"notes\n\n[masked]{brk}after")

    def test_a_short_last_line_and_a_placeholder_glued_body_are_masked(self):
        for text in (f"[masked]{self.LINE}\n{self.LINE}\nAg==\n-----END PRIVATE KEY-----",
                     f"{self.LINE}\nAg==\n-----END PRIVATE KEY-----", f"[masked]rem\n{self.LINE}\n{self.LINE} \nAg==",
                     f"[masked]{self.LINE}\\n{self.LINE}\\nAg==\\n\""):
            with self.subTest(text=text[:30]):
                self.assertNotIn(self.LINE[:12], mask(text))
                self.assertNotIn("Ag==", mask(text))
        for text in ("x [masked] and Ag==\nwords", "token=[masked]\n3f2a9c" + "0" * 34 + "\nnext"):
            self.assertEqual(mask(text), text, "a word, or a hash on the next line, after a placeholder stays")

    def test_the_rest_of_a_token_masked_a_chunk_at_a_time_is_masked(self):
        # An older kb masked "sk-ant-..." in one chunk; the token's last characters began the next.
        self.assertEqual(mask("see [masked]QQQQQQQQQQQQQQQ and more"), "see [masked] and more")
        # Its key pattern ran on into the next line and took the start of a secret's name.
        self.assertEqual(mask("[masked]_key: walrus2secret9 next"), "[masked] next")

    def test_a_footer_alone_masks_its_paragraph_and_only_that(self):
        for text in (f"notes before\n{self.LINE}\n-----END PRIVATE KEY-----", "ordinary words here\n-----END PRIVATE KEY-----"):
            self.assertEqual(mask(f"first paragraph\n\n{text}\n\nlast paragraph"),
                             "first paragraph\n\n[masked]\n\nlast paragraph")

    def test_many_placeholders_with_key_lines_cost_one_pass(self):
        import time
        took = {}
        for n in (2000, 32000):
            text = ("[masked]" + "A" * 64 + "\n\n") * n
            started = time.process_time()
            self.assertEqual(mask(text), "[masked]\n\n" * n)
            took[n] = time.process_time() - started
        # Sixteen times the input: about sixteen times the time when linear, some eighty when each span copies the text.
        self.assertLess(took[32000], 40 * max(took[2000], 0.005), took)
        header = "-----BEGIN PRIVATE KEY-----"
        for name, text in (("many footers", ("A" * 40 + "\n-----END PRIVATE KEY-----\n") * 5000),
                           ("many headers in one paragraph", (header + "\nAAAA\n") * 20000),
                           ("many headers, a paragraph each", (header + "\nAAAA\n\n") * 20000),
                           ("one header, a long paragraph", header + "\n" + "AAAA word " * 100000),
                           ("many fields", ("DEK-Info: AES-256-CBC,0A\n") * 20000)):
            started = time.monotonic()
            mask(text)
            self.assertLess(time.monotonic() - started, 3, name)

    def test_a_long_run_without_a_footer_costs_one_pass(self):
        import time
        started = time.monotonic()
        mask(("A" * 40 + "\n") * 5000)
        self.assertLess(time.monotonic() - started, 2)


class RepeatTest(unittest.TestCase):
    """mask() repeats until a pass changes nothing, so masked text masks to itself, and stops: no pattern matches a
    placeholder alone, and a text still changing at the pass limit has its changing part masked whole."""

    def test_no_pattern_matches_a_placeholder_alone(self):
        from kb import mask as mask_module
        patterns = (mask_module.SECRET_PATTERNS + mask_module.PREFIXED_SECRETS
                    + [mask_module.KEYED_SECRET, mask_module.ENV_KEY_SECRET, mask_module._GLUED_RUN,
                       mask_module._KEY_REST])
        for text in ("[masked]", "[masked] [masked]\n[masked]\n\n[masked]", "a [masked], b [masked].", '"[masked]"'):
            with self.subTest(text=text):
                for pattern in patterns:
                    self.assertIsNone(pattern.search(text), pattern.pattern[:60])
                self.assertEqual(mask_module._mask_once(text), text)

    def test_masked_text_masks_to_itself(self):
        import random
        from tests.test_write_fuzz import Planted
        for seq in range(2000):
            text = Planted(random.Random(seq), seq).text()
            once = mask(text)
            self.assertEqual(mask(once), once, f"seq {seq}")
        for text in ("Order ref VP1" + "A" * 26 + "ghp_" + "B" * 30 + " thanks", "x passwd y set-cookie: basic "):
            self.assertEqual(mask(mask(text)), mask(text))

    def test_text_still_changing_at_the_limit_is_masked_whole_and_counted(self):
        from kb import mask as mask_module
        before = mask_module.capped
        with mock.patch.object(mask_module, "_mask_once", lambda text: text[:6] + "x" + text[6:]):
            self.assertEqual(mask_module.mask("keep: secret part"), "keep: [masked]secret part")
        self.assertEqual(mask_module.capped, before + 1)

    def test_plain_text_costs_one_pass(self):
        import time
        from kb import mask as mask_module
        plain = "walrus ledger notes deploy plan " * 20000
        with mock.patch.object(mask_module, "_mask_once", wraps=mask_module._mask_once) as once:
            mask(plain)
            self.assertEqual(once.call_count, 1)
            mask(plain + " password=walrus2secret9")
            self.assertEqual(once.call_count, 3, "a text with a secret: one pass that masks, one that confirms")
        took = {}
        for name, fn in (("mask", mask), ("one pass", mask_module._mask_once)):
            started = time.process_time()
            fn(plain)
            took[name] = time.process_time() - started
        self.assertLess(took["mask"], 1.5 * took["one pass"] + 0.02, took)


class KeyShapeTest(unittest.TestCase):
    """Keys as they come: any header kind, an encrypted key's fields, lines of 1 to 100 characters, LF, CRLF or
    escaped breaks, whole or cut anywhere, with words, a newline, a cut's mark or the next secret after them."""

    def test_no_key_shape_leaves_its_body_readable(self):
        import random
        import re
        from tests.test_write_fuzz import Planted
        for seq in range(3000):
            planted = Planted(random.Random(seq), seq)
            block = planted.key()
            body = planted.bodies[0]
            for after in (" after", "\nafter", "", "\nclientSecretToken: walrus2secret9", " …"):
                masked = mask(f"note {block}{after}")
                readable = re.sub(r"\s+|\\[nr]", "", masked)
                leaked = [body[i:i + 12] for i in range(0, len(body) - 11, 4) if body[i:i + 12] in readable]
                self.assertEqual(leaked, [], f"seq {seq}: {block[-60:]!r}{after!r}")
                self.assertNotIn("walrus2secret9", masked)

    def test_paragraphs_after_a_broken_key_stay(self):
        for key in ("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADAN", "-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\n"
                    "DEK-Info: AES-256-CBC,0A1B\n\nMIIEvQIBADAN", "abc\n-----END PRIVATE KEY-----"):
            for brk in ("\n\n", "\r\n\r\n", "\\n\\n"):
                self.assertEqual(mask(f"{key}{brk}the next paragraph"), f"[masked]{brk}the next paragraph")


class SecretInIdTest(unittest.TestCase):
    """An item id is a name someone chose, read more narrowly than text so a real item is not dropped."""

    def test_a_token_or_a_credential_in_a_name_counts(self):
        for name in ("key-sk-ant-api03-" + "Z" * 24 + ".md", "ghp_" + "A1" * 18 + ".md", "password=walrus2secret9.md",
                     "password=>walrus2secret9.md", 'token=>"abcd1234efgh5678"', "api_key=>'walrus2secret9'",
                     "token:abcd1234efgh5678.md", "OPENAI_API_KEY=where-to-find.md",
                     "https://user:walrus9pw@example.com/notes"):
            with self.subTest(name=name):
                self.assertTrue(secret_in_id(name))

    def test_a_title_naming_a_secret_does_not(self):
        for name in ("Tokens: 2026Q4-plan.md", "Meeting - token: budget2026review.md", "Wifi password is changed.md",
                     "passcode is 4 digits.md", "Secrets: rotation plan.md", "api_key rotation.md",
                     "Areas/Security/password manager migration 2026-10.md", "Session token expiry bug.md"):
            with self.subTest(name=name):
                self.assertFalse(secret_in_id(name))
        for name in ("Tokens: 2026Q4-plan.md", "Meeting - token: budget2026review.md"):
            self.assertNotEqual(mask(name), name, "in text the same words are still masked")

    def test_machine_made_ids_do_not(self):
        for name in ("acme/app@" + "3f2a9c" * 6 + "e1d4", "1781000000000000001", "<CAFx3k+Q2Fu_ZXJ5=@mail.gmail.com>",
                     "iMessage;-;+15555550123:2026-10-27", "I_kwDOAbCdEf5Ghij", "default:20260923_121000_71bf95",
                     "rollout-2026-10-27T20-18-36-01a17f03-c62d-7001-a10a-09292ceb8a27"):
            with self.subTest(name=name):
                self.assertFalse(secret_in_id(name))


class AuthNoiseTest(unittest.TestCase):
    def test_auth_messages_are_noise(self):
        noisy = [
            "Your verification code is 482913",
            "Use this code to sign in: 123456. It expires in 10 minutes.",
            "Reset your password. We received a request to reset the password for your account.",
            "Password reset requested for sam@example.com",
            "Your Google 2-Step Verification code is 774411",
            "482913 is your Instagram code. Don't share it.",
            "Click the magic link below to log in",
            "Two-factor authentication code: 553201",
            "Your one-time passcode is 90210",
            "Your passcode is 4417",
            "Your Chase passcode: 4417",
            "OTP 123456 for your transaction",
            "Sign in to your account to continue",
        ]
        for text in noisy:
            with self.subTest(text):
                self.assertTrue(is_auth_noise(text))

    def test_normal_mail_is_kept(self):
        normal = [
            "Lunch tomorrow at noon? I booked the corner table.",
            "Invoice 4021 attached, due on the 30th.",
            "Re: contract review. Two changes in section 4, otherwise fine.",
            "Your order has shipped and arrives Thursday.",
            "Notes on the password manager migration plan for the team",
            "Microsoft Teams meeting\nJoin: https://teams.microsoft.com/meet/1\nMeeting ID: 245 933 726\nPasscode: nz39UA3p",
            "Join Zoom Meeting\nhttps://zoom.us/j/81234567890\nMeeting ID: 812 3456 7890\nPasscode: 123456",
            "",
        ]
        for text in normal:
            with self.subTest(text):
                self.assertFalse(is_auth_noise(text))

    def test_only_the_opening_is_checked(self):
        self.assertFalse(is_auth_noise("a" * 700 + " verification code"))
        self.assertTrue(is_auth_noise("a" * 100 + " verification code"))


class AuthCodeTest(unittest.TestCase):
    def test_a_message_that_hands_over_a_code_is_an_auth_code(self):
        for text in ("Your verification code is 482913.", "Your code is 4829", "Use this code: 12345678 to sign in",
                     "Your verification code is 482913, valid for 10 minutes.", "Verification code:482913"):
            with self.subTest(text):
                self.assertTrue(is_auth_code(text))

    def test_talk_about_codes_dates_and_versions_is_not(self):
        for text in ("the verification code email never arrives", "login code broke in v2.1234.5",
                     "reset your password on 2026-10-27", "reset your password by 10/27/2026", "order 482913 shipped",
                     "the sign-in code path takes 3.14159 seconds"):
            with self.subTest(text):
                self.assertFalse(is_auth_code(text))


if __name__ == "__main__":
    unittest.main()
