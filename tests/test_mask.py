import unittest

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


class SecretInIdTest(unittest.TestCase):
    """An item id is a name someone chose, read more narrowly than text so a real item is not dropped."""

    def test_a_token_or_a_credential_in_a_name_counts(self):
        for name in ("key-sk-ant-api03-" + "Z" * 24 + ".md", "ghp_" + "A1" * 18 + ".md", "password=walrus2secret9.md",
                     "token:abcd1234efgh5678.md", "OPENAI_API_KEY=where-to-find.md",
                     "https://user:hunter9pw@example.com/notes"):
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
