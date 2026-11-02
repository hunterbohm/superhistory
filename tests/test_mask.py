import unittest
from unittest import mock

from kb.mask import INVISIBLE, is_auth_code, is_auth_noise, mask, secret_in_id


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

    def test_a_value_counts_as_a_pointer_only_in_a_pointers_whole_shape(self):
        for text in ("password=$ecretP4ss99", "password=~Zx9Kq2Lm4Np", '"password": "Abc(123)xyzQ"',
                     '"api_key": "*Gh7kL9mPq2Rs4Tv6"', "password=/Zx9Kq2Lm4Np"):
            with self.subTest(text=text):
                self.assertIn("[masked]", mask(text))
        for value in ("$DB_PASSWORD", "${DB_PASSWORD}", "$(cat pw.txt)", "%APPDATA%", "%(password)s", "{{ secrets.db }}",
                      "${{ secrets.TOKEN }}", "~/.secrets/db", "/run/secrets/db_password", "./secret.txt",
                      "os.environ['X']", "process.env.TOKEN", "getpass()", "env:DB_PASSWORD", "****", "[REDACTED]",
                      "config.db.password", "$api_key"):
            with self.subTest(value=value):
                self.assertNotIn("[masked]", mask(f"password={value}"))

    def test_keyed_mask_keeps_the_key_name(self):
        self.assertEqual(mask("password: sw0rdfish9"), "password: [masked]")

    def test_all_caps_secret_is_masked_but_env_var_names_are_not(self):
        self.assertEqual(mask("password: ABCDEF123456"), "password: [masked]")
        self.assertEqual(mask("api_key: ZX81QWERTY99"), "api_key: [masked]")
        for text in ("password: DB_PASSWORD", "api_key = OPENAI_API_KEY2", "token: GITHUB_TOKEN"):
            with self.subTest(text):
                self.assertEqual(mask(text), text)

    def test_text_that_starts_like_a_sync_code_reads_as_any_other(self):
        # kb masks no Sync codes: Cloud's egress masks them before anything leaves the computer.
        self.assertEqual(mask('{"VP1ApplicationPassword12":"walrus2secret9"}'), '{"VP1ApplicationPassword12":"[masked]"}')
        for text in ("VP1 sent them back next week", "VRC2 rollout plan for the team"):
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


class OtherKeyFormatTest(unittest.TestCase):
    """Private keys that are not PEM's "PRIVATE KEY": PGP's armored blocks, PuTTY's key files and age's secret keys."""
    PGP_BODY = ("lQOYBGcW1aEBCADTkzY3c2VjcmV0cGdwYm9keWxpbmUxMjM0NTY3ODkw", "WalrusPgpBodySecond2Line3456789abcdefghij")

    def test_a_pgp_block_is_masked_whole_cut_or_footer_alone(self):
        body = "\n".join(self.PGP_BODY)
        for kind in ("PRIVATE", "SECRET"):
            for brk in ("\n", "\r\n", "\\n"):
                block = (f"-----BEGIN PGP {kind} KEY BLOCK-----\nVersion: GnuPG v2\n\n{body}\n=Ab3D\n"
                         f"-----END PGP {kind} KEY BLOCK-----").replace("\n", brk)
                for text in (block, block[:block.index("=Ab3D")], block[block.index(self.PGP_BODY[0]):]):
                    with self.subTest(kind=kind, brk=brk, text=text[:20]):
                        masked = mask(f"first{brk}{brk}{text}{brk}{brk}last paragraph")
                        self.assertNotIn("Walrus", masked)
                        self.assertNotIn("lQOYBGcW", masked)
                        self.assertTrue(masked.endswith(f"{brk}{brk}last paragraph"), masked)

    def test_a_putty_key_files_private_lines_are_masked(self):
        text = ("PuTTY-User-Key-File-3: ssh-ed25519\nEncryption: none\nComment: eddsa-key\nPublic-Lines: 2\n"
                "AAAAC3NzaC1lZDI1NTE5AAAAIHB1YmxpY3B1YmxpYw==\nPrivate-Lines: 1\n"
                "AAAAIFdhbHJ1c1B1dHR5UHJpdmF0ZUJvZHlTZWNyZXQ5\nPrivate-MAC: 3f2a9c00112233445566778899aabbcc")
        for brk in ("\n", "\\n"):
            masked = mask(text.replace("\n", brk) + f"{brk}{brk}last paragraph")
            self.assertNotIn("AAAAIFdhbHJ1", masked)
            self.assertIn("Public-Lines: 2", masked)
            self.assertTrue(masked.endswith("last paragraph"))

    def test_an_age_secret_key_is_masked(self):
        key = "AGE-SECRET-KEY-1QYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGP0ABCDE"
        for text in (key, f"# created\n{key}\n", f"identity\\n{key}", key[:40]):
            with self.subTest(text=text[:20]):
                self.assertNotIn("QYQSZQGPQYQS", mask(text))

    def test_an_ssh2_key_file_is_masked(self):
        body = "P2/56wAAAi4AAAA3aWYtbW9kbntzaWduWalrusSsh2BodyLine"
        for kind in ("ENCRYPTED PRIVATE KEY", "PRIVATE KEY"):
            block = f"---- BEGIN SSH2 {kind} ----\nComment: \"rsa-key\"\n{body}\n---- END SSH2 {kind} ----"
            for text in (block, block[:block.index("\n---- END")]):
                with self.subTest(kind=kind, cut=text is not block):
                    masked = mask(f"first\n\n{text}\n\nlast paragraph")
                    self.assertNotIn("WalrusSsh2", masked)
                    self.assertEqual(masked, "first\n\n[masked]\n\nlast paragraph")

    def test_a_registry_login_written_as_base64_is_masked(self):
        import base64
        import json
        login = base64.b64encode(b"bob:walrus2secret9").decode()
        config64 = base64.b64encode(json.dumps({"auths": {"registry.example.com": {"auth": login}}}).encode()).decode()
        for text in (f".dockerconfigjson: {config64}", json.dumps({"auths": {"registry.example.com": {"auth": login}}}),
                     json.dumps(json.dumps({"auths": {"registry.example.com": {"auth": login}}}))):
            with self.subTest(text=text[:30]):
                masked = mask(text)
                self.assertNotIn(login, masked)
                self.assertNotIn(config64[16:40], masked)
        for text in ('{"auth": "required"}', '{"auth": "bearer"}', '"auth": "c29tZSBub3Rlcw=="'):
            self.assertEqual(mask(text), text)

    def test_prose_about_those_formats_stays(self):
        for text in ("PGP keys are signed in a block", "Private-Lines counts the lines", "an AGE-SECRET-KEY-1 identity",
                     "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nmQENBGcW1aEBCAD\n-----END PGP PUBLIC KEY BLOCK-----"):
            with self.subTest(text=text[:20]):
                self.assertEqual(mask(text), text)


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
        for text in ("x [masked] and Ag==\nwords", "apiKey: [masked]\nconnectionTimeoutMillis: 3000\nnext"):
            self.assertEqual(mask(text), text, "a word, or a line that is not a key's, after a placeholder stays")
        # A chunk that ended between a key's header and its line break left a line break, then the body.
        for after in ("", "\n-----END PRIVATE KEY-----"):
            text = f"notes [masked]\n{self.LINE}\n{self.LINE}{after}\n\nnext paragraph"
            self.assertEqual(mask(text), "notes [masked]\n\nnext paragraph")

    def test_after_a_placeholder_and_a_line_break_a_whole_key_line_starts_the_rest_of_a_key(self):
        # An older kb's key pattern stopped at a backslash, so in escaped text it masked a key's header and left its
        # body from its first line, which starts as a real key's DER or OpenSSH body does and is base64 to its end.
        for first in ("MIIEvQIBADANBgkqhkiG9w0BAQEFAASC", "MHcCAQEEIJQyFkwn", "MC4CAQAwBQYDK2Vw", "MIGkAgEBBDAqCr+V",
                      "MEcCAQAwBQYDK2Vx", "b3BlbnNzaC1rZXkt"):
            line = (first + self.LINE)[:64]
            for brk in ("\\n", "\\r\\n", "\n", "\r\n"):
                for rest in (line + brk + self.LINE[:20] + " …", line + brk + self.LINE + brk + "-----END PRIVATE KEY-----"):
                    with self.subTest(first=first, brk=brk, rest=rest[-12:]):
                        text = f"walrus ledger [masked]{brk}{rest}{brk}{brk}next paragraph"
                        self.assertEqual(mask(text), f"walrus ledger [masked]{brk}{brk}next paragraph")

    def test_after_a_placeholder_lines_that_only_start_like_a_key_stay(self):
        for text in ("DB_PASSWORD=[masked]\nMINIO_ENDPOINT=http://minio.internal:9000\nMINIO_BUCKET=walrus-uploads",
                     "token=[masked] MINUTES of the meeting\nnext line", "[masked]\nMIGRATION plan for the walrus\nmore",
                     "[masked]\n\"MILESTONE\" is done", "api_key: [masked]\n# MIGRATE the walrus-uploads bucket",
                     "[masked]\nMIGRATION2026UseTheNewCustomerAccountTable is the example name.\nKeep this sentence.",
                     "[masked]\\nMIGRATION2026UseTheNewCustomerAccountTable is the example name.\\nKeep this sentence.",
                     "token: [masked] MIGRATION2026UseTheNewCustomerAccountTable, as named"):
            with self.subTest(text=text[:30]):
                self.assertEqual(mask(text), text)

    def test_quotes_after_a_placeholder_cost_time_in_proportion(self):
        import time
        for quote in ('"', "'", "\"'"):
            with self.subTest(quote=quote):
                started = time.process_time()
                mask("[masked]" + quote * (24 // len(quote)) + "MIGRATION plan")
                self.assertLess(time.process_time() - started, 0.5)

    def test_after_a_placeholder_and_a_line_break_other_lines_stay(self):
        for brk in ("\\n", "\n"):
            for line in ("/usr/local/bin/python3 deploy.py --env prod", "3f2a9c" + "0" * 34, "MCP server started",
                         "Make the deploy", "PATH=/usr/local/bin:/usr/bin", "MD5 checksums", "QUJDREVGR0hJSktM"):
                with self.subTest(brk=brk, line=line):
                    text = f"export GH=[masked]{brk}{line}{brk}then open it"
                    self.assertEqual(mask(text), text)
        text = "export GH=ghp_" + "A1" * 12 + "\\n/usr/local/bin/python3 deploy.py\\nthen open it"
        self.assertEqual(mask(text), "export GH=[masked]\\n/usr/local/bin/python3 deploy.py\\nthen open it")

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


class InvisibleTest(unittest.TestCase):
    """A secret split by characters a reader does not see reads whole, so it is masked; the rest of the text keeps them."""
    INVISIBLE = ("\u200b", "\u200c", "\u200d", "\u2060", "\u00ad", "\ufeff", "\u200e", "\u202e", "\u2066",
                 "\ufe0f", "\u034f", "\U000e0041")

    def test_a_secret_split_by_an_invisible_character_is_masked(self):
        for mark in self.INVISIBLE:
            for secret in ("sk-ant-api03-" + "Z" * 12 + mark + "Z" * 12, "gh" + mark + "p_" + "A1" * 12,
                           "pass" + mark + "word=walrus2secret9", "AKIA" + "B" * 8 + mark + mark + "B" * 8,
                           "-----BEGIN PRIVATE" + mark + " KEY-----\nMIIEvQIBADAN\nMIIEvQIBADAN"):
                with self.subTest(mark=mark, secret=secret):
                    masked = mask(f"note {secret} end")
                    self.assertIn("[masked]", masked)
                    for piece in ("ZZZZ", "A1A1", "walrus", "BBBB", "MIIE"):
                        self.assertNotIn(piece, masked)
                    self.assertEqual(mask(masked), masked)

    def test_invisible_characters_between_two_secrets_stay(self):
        family = "\U0001f468\u200d\U0001f469"
        for text, masked in (("ghp_" + "A1" * 12 + f" family {family} then ghp_" + "B2" * 12, f"[masked] family {family} then [masked]"),
                             ("sk-proj-" + "a1" * 8 + "\u200b" + "a1" * 8 + f" co\u00adoperate {family} AKIA" + "QW3RTY7UIOPAS2DF",
                              f"[masked] co\u00adoperate {family} [masked]"),
                             ("ghp_" + "A1" * 12 + f" family {family} ghp_" + "B2" * 12 + "[masked]",
                              f"[masked] family {family} [masked][masked]"),
                             (f"[masked][masked] {family} ghp_" + "B2" * 12 + f" {family} [masked] [masked]",
                              f"[masked][masked] {family} [masked] {family} [masked] [masked]"),
                             # Two placeholders a pass made side by side, after a change before the joiner.
                             ("ghp_" + "A1" * 12 + f" family {family} Authorization: Token walrus2secret9|ghp_" + "B2" * 12,
                              f"[masked] family {family} Authorization: Token [masked][masked]"),
                             ("ghp_" + "A1" * 12 + f" co\u00adoperate {family} Authorization: Token walrus2secret9|ghp_"
                              + "B2" * 12 + "|ghp_" + "C3" * 12 + f" {family}",
                              f"[masked] co\u00adoperate {family} Authorization: Token [masked][masked]|[masked] {family}"),
                             ("ghp_" + "A1" * 12 + f" {family} Authorization: Token walrus2secret9|ghp_" + "B2" * 12
                              + f" {family} Authorization: Token tapir7secret3|ghp_" + "C3" * 12,
                              f"[masked] {family} Authorization: Token [masked][masked] {family} Authorization: Token "
                              "[masked][masked]")):
            with self.subTest(text=text[:12]):
                self.assertEqual(mask(text), masked)

    def test_joiners_and_soft_hyphens_in_words_between_secrets_stay_whatever_the_secrets(self):
        import random
        import string
        alnum = string.ascii_letters + string.digits
        family, soft = "\U0001f468\u200d\U0001f469", "co\u00adoperate"

        def run(r, n):
            return "".join(r.choice(alnum) for _ in range(n)) + r.choice("0123456789")

        for seed in range(600):
            r = random.Random(seed)
            secrets = (lambda: "ghp_" + run(r, r.randint(24, 36)), lambda: "sk-proj-" + run(r, r.randint(20, 40)),
                       lambda: "AKIA" + "".join(r.choice(string.ascii_uppercase + string.digits) for _ in range(16)),
                       lambda: "password=" + run(r, 12), lambda: "Authorization: Token " + run(r, 14),
                       lambda: "Authorization: Token " + run(r, 12) + "|ghp_" + run(r, 30),
                       lambda: "password=" + run(r, 12) + "|ghp_" + run(r, 30) + "[masked]",
                       lambda: "ghp_" + run(r, 30) + "[masked]", lambda: "[masked]", lambda: "[masked][masked]")
            words = (lambda: family, lambda: soft, lambda: r.choice(("plan", "the", "ledger", "walrus")))
            pieces = [r.choice(r.choice((secrets, words)))() for _ in range(r.randint(3, 12))]
            text = " ".join(pieces)
            with self.subTest(seed=seed):
                masked = mask(text)
                self.assertEqual((masked.count("\u200d"), masked.count("\u00ad")), (text.count("\u200d"), text.count("\u00ad")),
                                 ascii(text))
                self.assertEqual(mask(masked), masked)

    # What follows a token and an invisible character, and how main masks it there with the character kept.
    READINGS = (("Authorization: Token walrus2secret9", "Authorization: Token [masked]"),
                ("Proxy-Authorization: Bearer walrus2secret9abcdef", "Proxy-Authorization: [masked]"),
                ("password=walrus2secret9", "password=[masked]"), ("API_KEY=walrus2secret9", "API_KEY=[masked]"),
                ("--api-key walrus2secret9", "--api-key [masked]"), ("token is walrus2secret9", "token is [masked]"),
                ("Cookie: sid=walrus2secret9abc", "Cookie: [masked]"), ("Bearer walrus2secret9abcdef", "[masked]"),
                ("Basic c2FtOndhbHJ1czJzZWNyZXQ5", "[masked]"),
                ("https://sam:walrus2secret9@example.com", "https://sam:[masked]@example.com"),
                ("https%3A%2F%2Fsam%3Awalrus2secret9%40example.com", "https%3A%2F%2Fsam%3A[masked]%40example.com"),
                ("curl -u sam:walrus2secret9 x", "curl -u sam:[masked] x"), ("mysql -pwalrus2secret9 shop", "mysql -p[masked] shop"),
                ("sshpass -p walrus2secret9 ssh x", "sshpass -p [masked] ssh x"),
                ("-passin pass:walrus2secret9", "-passin pass:[masked]"),
                ('auth: "c2FtOndhbHJ1czJzZWNyZXQ5"', 'auth: "[masked]"'),
                ("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----", "[masked]"))

    def test_an_invisible_character_before_a_secrets_name_parts_it_from_a_token(self):
        for token in ("ghp_" + "A1" * 12, "sk-ant-api03-" + "Z" * 24, "xoxb-" + "1234567890-" * 2 + "abcdefABCDEF1234"):
            for mark in self.INVISIBLE + ("\x00", "\x1b"):
                for text, masked in self.READINGS:
                    with self.subTest(token=token[:4], mark=hex(ord(mark)), text=text[:12]):
                        self.assertEqual(mask(token + mark + text), "[masked]" + mark + masked)

    def test_names_after_invisible_characters_cost_time_in_proportion(self):
        import time
        took = {}
        for times in (500, 4000):
            text = ("ghp_" + "A1" * 12 + "\u200b" + "\u200b".join(("password", "x-api-auth", "curl")) + " ") * times
            started = time.process_time()
            mask(text)
            took[times] = time.process_time() - started
        self.assertLess(took[4000], 16 * max(took[500], 0.01))

    def test_whitespace_among_the_c0_controls_parts_words_as_a_line_break_does(self):
        for space in ("\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x1f"):
            with self.subTest(space=hex(ord(space))):
                text = f"Authorization: ISO-27001{space}compliant"
                self.assertEqual(mask(text), text)
                self.assertEqual(mask("ghp_" + "A1" * 12 + space + "next page"), "[masked]" + space + "next page")

    def test_text_whose_invisible_characters_split_no_secret_masks_as_written(self):
        # Main's reading of the text as written: what a token, a name or a command parts, it parts.
        for text, masked in (("sk-proj-" + "C3" * 12 + "\u200dplain_https://sam:walrus2secret9@example.com",
                              "[masked]\u200dplain_https://sam:[masked]@example.com"),
                             ("ghp_" + "A1" * 12 + "_co\u00adoperate/curl -u sam:walrus2secret9 x",
                              "[masked]\u00adoperate/curl -u sam:[masked] x"),
                             ("ghp_" + "A1" * 12 + "_co\u00adoperate/Proxy-Authorization: Token walrus2secret9",
                              "[masked]\u00adoperate/Proxy-Authorization: Token [masked]"),
                             ("password=walrus2secret9 co\u00adoperate password=walrus2secret9|ghp_" + "B2" * 12,
                              "password=[masked] co\u00adoperate password=[masked]]")):
            with self.subTest(text=text[:16]):
                self.assertEqual(mask(text), masked)

    def test_invisible_characters_outside_every_secret_change_nothing_main_reads(self):
        import random
        import string
        from kb.mask import _TEXT_RULES, _pass
        alnum = string.ascii_letters + string.digits

        def as_written(text):  # main's mask: passes over the text as written until one changes nothing
            for _ in range(8):
                masked = _pass(text, _TEXT_RULES)
                if masked == text:
                    return text
                text = masked
            return text

        for seed in range(800):
            r = random.Random(seed)
            run = lambda n: "".join(r.choice(alnum) for _ in range(n)) + r.choice("0123456789")
            inv = lambda: r.choice(("\u200b", "\u200d", "\u2060", "\u00ad", "\x00", "\ufeff"))
            secrets = (lambda: "ghp_" + run(30), lambda: "sk-proj-" + run(30), lambda: "password=" + run(12),
                       lambda: "Authorization: Token " + run(14), lambda: "https://sam:" + run(12) + "@example.com",
                       lambda: "curl -u sam:" + run(12) + " x", lambda: "Cookie: sid=" + run(12),
                       lambda: "password=" + run(12) + "|ghp_" + run(30), lambda: "[masked]")
            words = (lambda: "co" + inv() + "operate", lambda: "\U0001f468\u200d\U0001f469",
                     lambda: r.choice(("plain_", "the", "x-", "notes/", "budget")) + inv() + r.choice(("plan", "ok", "")))
            pieces = [(kind, r.choice(kind)()) for kind in (r.choice((secrets, words)) for _ in range(r.randint(2, 10)))]
            # Invisibles only after a word, and a separator no token runs on through: past one right after a secret,
            # or a "_" that glues a word to it, the token a reader sees runs on (gate 12).
            text = "".join(piece + (inv() if kind is words and r.random() < 0.5 else r.choice((" ", "|", ", ")))
                           for kind, piece in pieces)
            with self.subTest(seed=seed):
                self.assertEqual(mask(text), as_written(text), ascii(text))

    def test_the_rest_of_a_token_an_older_kb_masked_up_to_an_invisible_character_is_masked(self):
        for mark in self.GATE_12:
            with self.subTest(mark=hex(ord(mark))):
                self.assertEqual(mask("note [masked]" + mark + "Q" * 20 + " end"), "note [masked] end")
                # Token characters glued to it through one are its rest, but the rest ends before a reading the text as
                # written starts in it, so the password after curl is masked.
                self.assertEqual(mask("[masked]" + mark + "_co" + mark + "operate/curl -u sam:walrus2secret9 x"),
                                 "[masked]" + mark + "operate/curl -u sam:[masked] x")

    def test_words_that_also_sit_inside_masked_secrets_stay_where_the_view_adds_one(self):
        key = "-----BEGIN PRIVATE KEY----- foo -----END PRIVATE KEY-----"
        split = "ghp_" + "A1" * 6 + "\u200b" + "B2" * 6
        self.assertEqual(mask(f"{key} foo {key} {split}"), "[masked] foo [masked] [masked]")

    def test_a_split_token_after_any_text_adds_only_its_own_placeholder_and_changes_nothing_else(self):
        import random
        from tests.test_mask_against_main import Generated
        split = "ghp_" + "A1" * 6 + "\u200b" + "B2" * 6
        for seed in range(400):
            # Ended by a word and then a blank line: a cookie's value reads on to the end of its line, and the rest of a
            # key an older kb masked reads on through a line break, as main reads them.
            text = INVISIBLE.sub("", Generated(random.Random(f"tail:{seed}")).text()).rstrip() + " end"
            with self.subTest(seed=seed):
                self.assertEqual(mask(f"{text}\n\n{split}"), mask(text) + "\n\n[masked]", ascii(text))

    def test_repeated_token_starts_between_invisible_characters_cost_time_in_proportion(self):
        import time
        took = {}
        for times in (4000, 16000):
            text = "eyJabcdefghijk\u200b" * times
            started = time.process_time()
            self.assertEqual(mask(text), text)
            took[times] = time.process_time() - started
        self.assertLess(took[16000], 8 * max(took[4000], 0.02))

    def test_the_rest_of_the_text_keeps_its_invisible_characters(self):
        family, soft = "\U0001f468\u200d\U0001f469", "co\u00adoperate"
        text = f"{family} {soft} then ghp_" + "A1" * 6 + "\u2060" + "A1" * 6 + f" then {family}"
        self.assertEqual(mask(text), f"{family} {soft} then [masked] then {family}")
        self.assertEqual(mask(f"{family} {soft} plain"), f"{family} {soft} plain")

    # Gate 12: NUL and the C0 controls that are not whitespace, the zero-width set, the word joiner and the soft hyphen.
    # Tab, LF, CR, the vertical tab, the form feed and the four separators from 0x1c are whitespace, as str.split reads them.
    GATE_12 = ("\x00", "\x01", "\x07", "\x08", "\x0e", "\x1a", "\x1b", "\u200b", "\u200c", "\u200d",
               "\u2060", "\u00ad", "\ufeff")
    KEYS = ("sk-proj-" + "a1" * 16, "ghp_" + "A1b2" * 9, "AKIA" + "QW3RTY7UIOPAS2DF", "sk-ant-api03-" + "Zq7wLm4N" * 4,
            "glpat-" + "Ab3dE5fG7hJ9kL2mN4pQ", "xoxb-" + "1234567890-" + "a1B2c3D4e5F6g7H8")

    def readable(self, text):
        import re
        return re.sub("[\x00-\x08\x0e-\x1b\u00ad\u200b-\u200d\u2060\ufeff]", "", text)

    def assert_masked(self, secret, text):
        masked = mask(text)
        readable = self.readable(masked)
        body = self.readable(secret)
        leaked = [body[i:i + 8] for i in range(4, len(body) - 7) if body[i:i + 8] in readable]
        self.assertEqual(leaked, [], repr(masked))
        self.assertEqual(mask(masked), masked)
        return readable

    def test_gate_12_a_secret_reads_through_invisible_characters_and_one_before_it_parts_it_from_a_word(self):
        for ch in self.GATE_12:
            for key in self.KEYS:
                cut = len(key) - 4  # the part before the cut is a key itself
                shapes = {"glue": "budget" + ch + key, "interleaved": ch.join(key),
                          "word then interleaved": "budget" + ch + ch.join(key),
                          "split": key[:len(key) // 2] + ch + key[len(key) // 2:],
                          "split, a key before the cut": key[:cut] + ch + key[cut:],
                          "glue then split": "budget" + ch + key[:10] + ch + key[10:]}
                for shape, secret in shapes.items():
                    with self.subTest(ch=hex(ord(ch)), key=key[:6], shape=shape):
                        readable = self.assert_masked(secret, f"{secret} paid")
                        self.assertTrue(readable.endswith(" paid"), readable)
                        if shape.startswith("glue") or shape.startswith("word"):
                            self.assertTrue(readable.startswith("budget"), readable)
            # Cloud's two: a key whose interior is a key, and a key whose tail runs past a whole key.
            with self.subTest(ch=hex(ord(ch)), shape="a key inside a key"):
                self.assert_masked("sk-proj-" + "a1" * 7 + "ghp_" + "a1" * 16,
                                   "sk-proj-" + "a1" * 7 + ch + "ghp_" + "a1" * 16)
            with self.subTest(ch=hex(ord(ch)), shape="a tail past a whole key"):
                self.assert_masked("sk-proj-" + "a1" * 16 + "b2" * 8, "sk-proj-" + "a1" * 16 + ch + "b2" * 8)

    def test_gate_12_a_named_value_glued_to_a_word_by_an_invisible_is_masked(self):
        for ch in self.GATE_12:
            with self.subTest(ch=hex(ord(ch))):
                self.assertNotIn("walrus2secret9", mask("budget" + ch + "password=walrus2secret9 paid"))
                self.assertNotIn("walrus2secret9", self.readable(mask("budget" + ch + "\x00".join("password=walrus2secret9"))))

    def test_gate_12_text_read_as_utf16_masks_and_costs_time_in_proportion(self):
        import time
        line = "notes for the walrus team, token=" + "Zq7wLm4Np8Rt2Kd9Vh3B" + " and the plan\n"
        took = {}
        for times in (100, 800):
            text = "\x00".join(line * times)
            started = time.process_time()
            masked = mask(text)
            took[times] = time.process_time() - started
            self.assertNotIn("Zq7wLm4N", self.readable(masked))
            self.assertIn("walrus team", self.readable(masked))
        self.assertLess(took[800], 16 * max(took[100], 0.01))

    def test_an_id_with_a_split_secret_counts_and_one_without_does_not(self):
        self.assertTrue(secret_in_id("key-sk-ant-\u200bapi03-" + "Q" * 24 + ".md"))
        self.assertTrue(secret_in_id("pass\u200bword=walrus2secret9.md"))
        self.assertFalse(secret_in_id("notes\u200b\u200d.md"))


class BareAuthorizationTest(unittest.TestCase):
    """After "Authorization:" or "Proxy-Authorization:", header style, a single credential-shaped token is masked even
    with no scheme word before it. Prose and identifiers there stay."""
    PROSE = ("pending", "Organization-level", "Implementation-specific,", "ISO-27001", "CVE-2024-12345", "RFC-7235",
             "SHA-256", "UTF-8", "x86_64", "Q4-2026", "v2.1.3", "HTTP/1.1", "1234567890123", "3.14159265358",
             "2026-11-01", "2026-11-01T10:00:00Z", "10:00:00.000Z", "RFC7235-compliant", "OAuth2.0-based-flow",
             "SHA256-checksums", "GDPR-2018-compliant", "PCI-DSS-v4.0-ready", "well-known", "re-authorization",
             "Kerberos5-tickets", "ISO-27001:2022", "IEEE-802.1X", "WPA2-Enterprise", "AES-256-GCM",
             "X.509v3-certificates", "S3-bucket-policies", "ec2-instance-role", "k8s-namespace-admins",
             "WindowsServer2022", "WindowsServer2022-compatible", "/usr/local/bin/python3", "HTTP/2-only")

    def credentials(self, r):
        import base64
        import string
        import uuid
        letters_digits = string.ascii_letters + string.digits

        def mixed(n, alphabet):
            while True:
                token = "".join(r.choice(alphabet) for _ in range(n))
                if any(c.isdigit() for c in token) and any(c.isalpha() for c in token):
                    return token

        def encoded(encode, n):
            while True:
                token = encode(bytes(r.randrange(256) for _ in range(n))).decode().rstrip("=" if encode is
                                                                                          base64.urlsafe_b64encode else "")
                if any(c.isdigit() for c in token) and any(c.isalpha() for c in token):
                    return token

        return {"letters and digits": mixed(r.randint(12, 64), letters_digits),
                "hex": mixed(r.randint(16, 64), "0123456789abcdef"),
                "base64": encoded(base64.b64encode, r.randint(12, 48)),
                "base64url": encoded(base64.urlsafe_b64encode, r.randint(12, 48)),
                "uuid": str(uuid.UUID(int=r.getrandbits(128), version=4)),
                "prefixed": r.choice(("live_", "tok_", "key_", "pat_", "api_")) + mixed(r.randint(16, 40), letters_digits),
                "three parts": ".".join(mixed(r.randint(10, 30), letters_digits + "-_") for _ in range(3))}

    def test_a_credential_shaped_token_after_authorization_is_masked(self):
        import random
        for seed in range(400):
            for shape, token in self.credentials(random.Random(seed)).items():
                for text in (f"Authorization: {token}", f"proxy-authorization: {token} for the staging API",
                             f'curl -H "AUTHORIZATION: {token}" https://api.example.com'):
                    with self.subTest(seed=seed, shape=shape, text=text[:20]):
                        self.assertNotIn(token, mask(text))

    def test_words_joined_by_a_space_of_another_kind_are_no_token(self):
        for space in ("\u00a0", "\u2009", "\u3000", "\u2002"):
            text = f"Authorization: request{space}approved{space}2026"
            with self.subTest(space=hex(ord(space))):
                self.assertEqual(mask(text), text)

    def test_prose_and_identifiers_after_authorization_stay(self):
        for word in self.PROSE:
            for text in (f"Authorization: {word} applies to the team", f"Authorization: {word}"):
                with self.subTest(text=text):
                    self.assertEqual(mask(text), text)


class EscapeTest(unittest.TestCase):
    """JSON text, such as a tool's input stored as JSON, escapes a line break as "\\n": a secret right after one, or on
    the next line after another's value, is masked as at the start of a line."""
    ESCAPES = ("\\n", "\\r\\n", "\\t", "\\u00a0")
    SECRETS = ("sk-ant-api03-" + "Z" * 24, "ghp_" + "A1" * 12, "AKIA" + "B" * 16, "xoxb-" + "1" * 12,
               "eyJabcdefghijkl.abcdefghijkl.abcdefghijkl", "Bearer walrusbearer2token9", "cookie: walrus2cookie9",
               "curl -u me:walrus2secret9", "STRIPE_KEY=walrus2secret9", "password: walrus2secret9")

    def test_a_secret_right_after_an_escape_is_masked(self):
        for escape in self.ESCAPES:
            for secret in self.SECRETS:
                with self.subTest(escape=escape, secret=secret):
                    masked = mask(f"output{escape}{secret}")
                    self.assertIn("[masked]", masked)
                    self.assertNotIn(secret[-9:], masked)

    def test_a_value_ends_where_an_escape_starts_the_next_name(self):
        for escape in self.ESCAPES:
            text = (f"secret_token=walrus1secret1{escape}api_key: walrus2secret2{escape}PASSWORD = walrus3secret3"
                    f"{escape}curl -u me:walrus4secret4{escape}DB_KEY=walrus5secret5")
            with self.subTest(escape=escape):
                masked = mask(text)
                self.assertNotIn("walrus", masked)
                self.assertIn(f"{escape}api_key: [masked]", masked)

    def test_a_percent_encoded_secret_or_one_in_escaped_quotes_is_masked(self):
        aws, jwt = "AKIA" + "B" * 16, "eyJabcdefghijkl.abcdefghijkl.abcdefghijkl"
        for text, secret in ((f"key%3D{aws}&x=1", aws), ("q=x%20AIza" + "B" * 35, "B" * 35), (f"%0A{aws}", aws),
                             (f"Bearer%20{jwt}", jwt), ("Bearer%20walrusbearer2token9", "walrusbearer2token9"),
                             ("Basic+d2FscnVzOndhbHJ1czJzZWNyZXQ5", "d2FscnVzOndhbHJ1czJzZWNyZXQ5"),
                             ("password%3Dwalrus2secret9&next=1", "walrus2secret9"),
                             ("%22password%22%3A%22walrus2secret9%22", "walrus2secret9"),
                             ('{\\"password\\": \\"walrus2secret9\\"}', "walrus2secret9"),
                             ('{\\\\\\"api_key\\\\\\": \\\\\\"walrus2secret9\\\\\\"}', "walrus2secret9"),
                             ("{&quot;password&quot;: &quot;walrus2secret9&quot;}", "walrus2secret9"),
                             ('{\\"STRIPE_KEY\\": \\"walrus2secret9\\"}', "walrus2secret9"),
                             ('{\\"cookie\\": \\"sid=walrus2cookie9\\"}', "walrus2cookie9")):
            with self.subTest(text=text):
                masked = mask(text)
                self.assertNotIn(secret, masked)
                self.assertIn("[masked]", masked)
        self.assertEqual(mask('{\\"password\\": \\"walrus2secret9\\"}'), '{\\"password\\": \\"[masked]\\"}',
                         "the escaped quote after a value stays")
        for link in ("next=https%3A%2F%2Fbob%3Awalrus2secret9%40example.com%2Fx",
                     "next=https%253A%252F%252Fbob%253Awalrus2secret9%2540example.com"):
            with self.subTest(link=link):
                self.assertNotIn("walrus2secret9", mask(link))
                self.assertIn("example.com", mask(link))
        for text in ("hello%20world%3Dfine", "100%25 done%2C next", "a%2Fb%2Fc.md", "&quot;password&quot; is a word",
                     "next=https%3A%2F%2Fexample.com%3A8443%2Fpath%40v2"):
            self.assertEqual(mask(text), text)

    def test_an_encoded_space_beside_a_separator_and_flags_after_an_escape_are_read(self):
        for text in ("password%3A%20walrus2secret9", "aws_secret_access_key%20%3D%20walrus2secret9xyz",
                     "q=%7B%22user%22%3A%20%22bob%22%2C%20%22password%22%3A%20%22walrus2secret9%22%7D",
                     "password+%3D+walrus2secret9", "password%253A%2520walrus2secret9",
                     "run it\\n--password walrus2secret9\\nAuthorization: Token walrus2secret9x\\ndone",
                     "%0A--api-key%20walrus2secret9", "Authorization%3A%20Token%20walrus2secret9x", "https:\\/\\/bob:walrus2secret9@example.com/x",
                     "next=https%3A//bob%3Awalrus2secret9%40example.com/x", "smb://CORP\\\\bob:walrus2secret9@files/x"):
            with self.subTest(text=text):
                self.assertNotIn("walrus2secret9", mask(text))
        for text in ("hello%20world%3Dfine", "a + b = c", "x%20%3D%20y", "https:\\/\\/example.com\\/path"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)

    def test_a_long_run_of_a_secrets_word_costs_time_in_proportion(self):
        # A secret's name was read around its key word, back over the run once for each key word in it.
        import time
        for unit in ("token", '"token', "password", "a.token.", "api_key_", "x-a-auth-", "x-auth-auth"):
            with self.subTest(unit=unit):
                took = {}
                for size in (1 << 12, 1 << 15):
                    text = unit * (size // len(unit))
                    started = time.process_time()
                    mask(text)
                    took[size] = time.process_time() - started
                self.assertLess(took[1 << 15], 24 * max(took[1 << 12], 0.01), took)

    def test_a_long_run_of_letters_and_digits_costs_time_in_proportion(self):
        # A link's scheme was read on from every letter of a run, as in contract bytecode or a hash's hex.
        import time
        for unit in ("0123456789abcdef", "abcdefghij", "a.b+c-d"):
            with self.subTest(unit=unit):
                took = {}
                for size in (1 << 13, 1 << 17):
                    text = unit * (size // len(unit))
                    started = time.process_time()
                    mask(text)
                    took[size] = time.process_time() - started
                self.assertLess(took[1 << 17], 40 * max(took[1 << 13], 0.01), took)

    def test_a_json_value_with_an_escaped_quote_is_masked_whole(self):
        for text in ('{"password": "ab\\"cdefgh9"}', '{"password": "walrus\\"2secret9", "user": "bob"}',
                     '{"api_key":"walrus\\\\path\\"2secret9x"}', '{\n  "db_password": "a\\"b\\"c2secret9"\n}'):
            with self.subTest(text=text):
                masked = mask(text)
                self.assertNotIn("cdefgh9", masked)
                self.assertNotIn("2secret9", masked)
                self.assertNotIn("c2secret", masked)
        self.assertEqual(mask('{"password": "walrus\\"2secret9", "user": "bob"}'), '{"password": "[masked]", "user": "bob"}')

    def test_a_name_with_no_value_does_not_take_the_next_lines_name(self):
        # A YAML parent key named like a secret, or a key left empty, sits above the line that holds the secret.
        for text in ("credentials:\n  token: Zq7wLm4Np8Rt2Kd9Vh3B", "secrets:\n  db_password: walrus2secret9",
                     "api_key:\napi_key: Zq7wLm4Np8Rt2Kd9Vh3B", "password:\npassword: walrus2secret9",
                     "STRIPE_KEY:\n  api_key: Zq7wLm4Np8Rt2Kd9Vh3B", "auth_token: \n\tsecret: walrus2secret9"):
            with self.subTest(text=text):
                masked = mask(text)
                self.assertNotIn("walrus2secret9", masked)
                self.assertNotIn("Zq7wLm4Np8Rt", masked)

    def test_an_authorization_header_reads_its_own_line(self):
        for text in ("Authorization: Toke\nAuthorization: Token Zq7wLm4Np8Rt2Kd9Vh3B",
                     "Authorization: Token \nAuthorization: Token Zq7wLm4Np8Rt2Kd9Vh3B"):
            with self.subTest(text=text):
                self.assertNotIn("Zq7wLm4Np8Rt", mask(text))

    def test_a_name_that_holds_a_secrets_word_after_another_on_its_line_is_read_too(self):
        for text in ("secrets: db_password: Qx7RedisBytes2026zz", "credentials = api_key = Qx7RedisBytes2026zz",
                     "TOKENS: GITHUB_TOKEN=Qx7RedisBytes2026zz", '"secrets": "password: Qx7RedisBytes2026zz"'):
            with self.subTest(text=text):
                self.assertNotIn("Qx7RedisBytes", mask(text))

    def test_a_value_after_a_carriage_return_is_masked(self):
        for text in ("password:\r\r\n  walrus2pw9", "password:\rwalrus2pw9", "password:\r \n  walrus2pw9",
                     "API_KEY=\rabcd1234efgh5678", "Password is\rwalrus2!x", "db:\r\r\n  password:\r\r\n    walrus2pw9"):
            with self.subTest(text=text):
                self.assertNotIn("walrus2", mask(text))
                self.assertNotIn("abcd1234", mask(text))

    def test_values_left_as_they_were_cost_time_in_proportion(self):
        import time
        for unit in ("token=/a/", "next_token=https://a.example/cb?"):
            with self.subTest(unit=unit):
                took = {}
                for size in (4_000, 32_000):
                    started = time.process_time()
                    mask(unit * (size // len(unit)))
                    took[size] = time.process_time() - started
                self.assertLess(took[32_000], 16 * max(took[4_000], 0.01))

    def test_a_value_that_holds_a_secrets_word_is_still_a_value(self):
        # A value is refused as the next line's name only when the space after its separator crossed a line break.
        for text in ("the wifi password: SecretGarden99 is on the fridge", "DB_PASSWORD=Tokyo-Token-2026 is what I set",
                     "password=secret:walrus2", "admin_password=SecretPass:42", "passcode: Token4471 is valid until Friday",
                     "export MYSQL_PWD=Secret:Xq7z"):
            with self.subTest(text=text):
                self.assertIn("[masked]", mask(text))
                self.assertNotIn("Garden99", mask(text))
                self.assertNotIn("Token-2026", mask(text))
                self.assertNotIn("walrus2", mask(text))

    def test_an_authorization_credential_after_a_scheme_is_masked_and_prose_stays(self):
        # The credential is the word after a known scheme, or a first word with no scheme that reads as random: long,
        # with digits and letters, no plain word inside it, not a date. Prose such as "ISO-27001 compliance" stays.
        for text in ("Authorization: SCRAM-SHA-256 Qx7pass2026zzQ9", "Proxy-Authorization: AWS4-HMAC-SHA256 Qx7pass2026zzQ9",
                     "use Authorization: Token Qx7pass2026zzQ9 for the staging API", "Authorization=Bearer SecretGarden99x",
                     "use Authorization: Qx7pass2026zzQ9 for the staging API", "Authorization: Qx7pass2026zzQ9 (expires Friday)",
                     "Authorization: SecretGarden99x is on the fridge", "Authorization: abcd1234efgh5678wxyz",
                     'curl -H "Authorization: Qx7pass2026zzQ9" https://api.example.com'):
            with self.subTest(text=text):
                masked = mask(text)
                self.assertNotIn("Qx7pass2026", masked)
                self.assertNotIn("Garden99x", masked)
        # In escaped text a value reads on through "\\n"; the credential ends there, before what follows it.
        for text, masked in (("Authorization: Token Zq03154022Wm4Np8Rt2Kd9\\n[masked]\\n[masked]",
                              "Authorization: Token [masked]\\n[masked]\\n[masked]"),
                             ("Authorization: Token Zq03154022Wm4Np8Rt2Kd9\\nnext line",
                              "Authorization: Token [masked]\\nnext line")):
            self.assertEqual(mask(text), masked)
        for text in ("Authorization: pending review", "Authorization: Organization-level approval needed",
                     "Authorization: Implementation-specific, see RFC 7235.", "Authorization: ISO-27001 compliance required.",
                     "Authorization: CVE-2024-12345 review pending", "Authorization: v2.1 of the policy applies",
                     "Authorization: RFC7235-compliant tokens only", "Authorization: OAuth2.0-based-flow applies",
                     "Authorization: 2026-11-01T10:00:00Z granted", "Authorization: SHA256 checksums attached"):
            self.assertEqual(mask(text), text)
        aws = "Authorization: AWS4-HMAC-SHA256 Credential=" + "AKIA" + "QW3RTY7UIOPAS2DF" + "/20261101/us-east-1/s3/aws4_request"
        self.assertEqual(mask(aws), "Authorization: AWS4-HMAC-SHA256 Credential=[masked]/20261101/us-east-1/s3/aws4_request")

    def test_a_named_value_in_escaped_text_is_masked_through_the_next_lines_first_word_as_on_main(self):
        # A known over-masking, kept on purpose: what follows "\\n" in the value's run may be the rest of the secret.
        for text, masked in (("password: Qx7pass2026zzQ9\\nnext line", "password: [masked] line"),
                             ("password: Qx7pass2026\\nab12cd34 and more", "password: [masked] and more")):
            with self.subTest(text=text):
                self.assertEqual(mask(text), masked)

    def test_a_run_of_backslashes_in_an_authorization_value_costs_time_in_proportion(self):
        import time
        took = {}
        for n in (2_000, 16_000):
            started = time.process_time()
            mask("Authorization: Token Qx7" + "\\" * n + "x")
            took[n] = time.process_time() - started
        self.assertLess(took[16_000], 16 * max(took[2_000], 0.01))

    def test_a_value_ends_where_an_escape_starts_a_flag(self):
        for text in ('{"command": "export API_TOKEN=abc123def456\\n--api-key s3cr3tV4lu3XYZ99"}',
                     "password: walrus2secret1\\n--token s3cr3tV4lu3XYZ99", "--token=abc123def456ghi\\n--api-key s3cr3tV4lu3XYZ99"):
            with self.subTest(text=text):
                self.assertNotIn("V4lu3XYZ99", mask(text))

    def test_a_name_with_no_value_does_not_take_a_symbol_name_on_the_next_line(self):
        for text in ('secrets:\n:password => "walrus2secret9"', '{"private_key":\n:token => "Zq7wLm4Np8Rt2Kd9Vh3B"'):
            with self.subTest(text=text):
                masked = mask(text)
                self.assertNotIn("walrus2secret9", masked)
                self.assertNotIn("Zq7wLm4Np8Rt", masked)

    def test_a_value_ends_where_an_escape_starts_a_symbol_name(self):
        text = '{"body": "api_key: Zq7wLm4Np8Rt2Kd9Vh3B\\n:password => \\"walrus2secret9\\"\\n:token => \\"Zx9Kq2Lm4Np8Rt2Kd9\\""}'
        masked = mask(text)
        self.assertNotIn("walrus2secret9", masked)
        self.assertNotIn("Zx9Kq2Lm4Np8", masked)
        self.assertIn(":password => ", masked)

    def test_a_password_with_a_backslash_in_it_is_masked_whole(self):
        for password in ("Ab3\\nQz9kLm", "C:\\temp\\walrus9", "pa\\u00e4ss9word"):
            with self.subTest(password=password):
                self.assertEqual(mask(f"password={password} next"), "password=[masked] next")

    def test_a_tool_input_stored_as_json_is_masked(self):
        import json
        stored = json.dumps({"content": "first line\nghp_" + "A1" * 12, "env": "A=1\nAPI_KEY=walrus2secret9\nB=2"})
        masked = mask(stored)
        self.assertNotIn("A1A1A1", masked)
        self.assertNotIn("walrus2secret9", masked)
        self.assertEqual(json.loads(masked)["content"], "first line\n[masked]")

    def test_a_long_run_of_escapes_after_a_value_costs_time_in_proportion(self):
        import time
        for head, unit in (("password=", "\\r\\n"), ("token=x", "\\n"), ("", "\\ncookie: "), ("password=", "\\"),
                           ('{\\"cookie\\": \\"', "\\"), ("x", "&#0")):
            with self.subTest(unit=unit):
                took = {}
                for size in (1 << 16, 1 << 20):
                    text = head + unit * (size // len(unit))
                    started = time.process_time()
                    mask(text)
                    took[size] = time.process_time() - started
                self.assertLess(took[1 << 20], 40 * max(took[1 << 16], 0.01), took)


class RepeatTest(unittest.TestCase):
    """mask() repeats until a pass changes nothing, so masked text masks to itself, and stops: no pattern matches a
    placeholder alone, and a text still changing at the pass limit has its changing part masked whole."""

    def test_a_value_that_holds_a_placeholder_leaves_the_next_header_to_its_own_reading(self):
        for text, masked in (("Authorization: Token [masked]|Authorization: Token walrus2secret9",
                              "Authorization: Token [masked]|Authorization: Token [masked]"),
                             ("password: [masked]|Authorization: Token walrus2secret9",
                              "password: [masked]|Authorization: Token [masked]"),
                             ("Authorization: [masked]|Authorization: Token walrus2secret9",
                              "Authorization: [masked]|Authorization: Token [masked]"),
                             ("password: [masked]|password=walrus2secret9", "password: [masked]|password=[masked]")):
            with self.subTest(text=text):
                self.assertEqual(mask(text), masked)

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


class LinearTimeTest(unittest.TestCase):
    """A long run of one shape costs time in proportion to its length: sixteen times the text, about sixteen times the
    time, where a pattern that reads on from every character would take some two hundred and fifty."""
    RUNS = ("0123456789abcdef", "token", "abcdefghij", "ABCDEFGHIJ", "QUJDRGVm+/9z", "a:", "password=", "a_pw ",
            "set-cookie", "a.", "token.", "x-", "token ", "_KEY_", "curl -u a:", "-----BEGIN ", "[masked]A",
            "a\u200b", "ghp_\u200b" + "A1" * 12 + " ", "vp1 abcd ", "V R C 1 ", "VRC1-0123-", "curl ", "password",
            "a.token.")
    SIZES = (1 << 16, 1 << 20)

    def test_every_shape_is_linear_up_to_a_megabyte(self):
        import time
        for unit in self.RUNS + ("a" * 64 + "://",):
            with self.subTest(unit=unit):
                took = {}
                for size in self.SIZES:
                    text = unit * (size // len(unit))
                    started = time.process_time()
                    mask(text)
                    took[size] = time.process_time() - started
                self.assertLess(took[self.SIZES[1]], 40 * max(took[self.SIZES[0]], 0.01), took)


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

    def test_nothing_from_a_header_to_its_footer_or_paragraph_end_survives(self):
        """The text masks as if everything from the header through the footer, or with no footer through the end of
        the paragraph (an encrypted key's past the blank line after its DEK-Info field), were one placeholder: no
        character of that span can survive. A paragraph after it stays."""
        import os
        import random
        import re
        from tests.test_write_fuzz import Planted
        blank = re.compile(r"\r?\n[ \t]*\r?\n|\\(?:r\\)?n[ \t]*\\(?:r\\)?n")
        footer = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")
        for seq in range(int(os.environ.get("KB_KEY_CASES", "3000"))):
            block = Planted(random.Random(seq), seq).key()
            for after in (" after", "\nafter", "", "\nclientSecretToken: walrus2secret9", " …", " word, api_key = x",
                          "\n\nthe next paragraph", "\\n\\nthe next paragraph", "\r\n\r\nthe next paragraph"):
                text = f"note {block}{after}"
                start = text.index("-----BEGIN ")
                end = footer.search(text, start)
                if end:
                    end = end.end()
                else:
                    end = start
                    fields = re.compile(r"DEK-Info:[^\r\n\\]*").search(text, start)
                    if fields and blank.match(text, fields.end()):
                        end = blank.match(text, fields.end()).end()
                    stop = blank.search(text, end)
                    end = stop.start() if stop else len(text)
                masked = mask(text)
                self.assertTrue(masked.startswith("note [masked]"), f"seq {seq}: {text!r}")
                self.assertEqual(masked, mask(text[:start] + "[masked]" + text[end:]), f"seq {seq}: {text!r}")
                if after.endswith("the next paragraph") and text[end:] in (after, after[1:]):
                    self.assertEqual(masked, "note [masked]" + text[end:], f"seq {seq}: {text!r}")

    def test_encrypted_key_fields_cost_time_near_prose(self):
        # An encrypted key's footer comes after one blank line, the one after its DEK-Info field, so a field's search
        # for it stops at the next.
        import time

        def took(text):
            started = time.process_time()
            mask(text)
            return time.process_time() - started

        size = 1 << 18
        prose = took("the walrus ledger notes plan\n\n" * (size // 30))
        for unit in ("DEK-Info: AES-128-CBC,00112233\n\n", "Proc-Type: 4,ENCRYPTED\n\n", "[masked],ENCRYPTED\n\n",
                     "DEK-Info: x\\n\\n"):
            with self.subTest(unit=unit):
                self.assertLess(took(unit * (size // len(unit))), 4 * max(prose, 0.01))

    def test_many_fields_on_one_line_cost_time_in_proportion(self):
        # An email can be written to hold them; each was read on to the end of its line.
        import time
        for unit in ("DEK-Info:", "DEK-Info: x ", "Proc-Type: 4,ENCRYPTED DEK-Info: "):
            with self.subTest(unit=unit):
                took = {}
                for size in (1 << 13, 1 << 16):
                    text = unit * (size // len(unit))
                    started = time.process_time()
                    mask(text)
                    took[size] = time.process_time() - started
                self.assertLess(took[1 << 16], 24 * max(took[1 << 13], 0.01), took)

    def test_paragraphs_after_a_broken_key_stay(self):
        for key in ("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADAN", "-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\n"
                    "DEK-Info: AES-256-CBC,0A1B\n\nMIIEvQIBADAN", "abc\n-----END PRIVATE KEY-----"):
            for brk in ("\n\n", "\r\n\r\n", "\\n\\n"):
                self.assertEqual(mask(f"{key}{brk}the next paragraph"), f"[masked]{brk}the next paragraph")


class CommandLineTest(unittest.TestCase):
    """A password given on a command line: after a long flag named for it, or after a short flag of the command that
    takes it."""
    PW = "walrus2wolfpack9"

    def test_a_password_a_command_line_gives_is_masked(self):
        pw = self.PW
        for text in (f"docker login -u bob --password {pw}", f"docker login -p {pw} registry.example.com",
                     f"podman login --username bob -p {pw} quay.example.com", f"mysql -u root -p{pw} shop",
                     "mysql -u root -proot shop", f"mysqldump -p{pw} shop > shop.sql", f"redis-cli -h cache -a {pw} ping",
                     f"sshpass -p {pw} ssh deploy@build", f"sshpass -p{pw} scp a b",
                     f"openssl rsa -in k.pem -passin pass:{pw}", f"openssl pkcs12 -export -passout pass:{pw}",
                     f"tool --api-key {pw}", f"tool --token {pw}", f"tool --secret '{pw}'", f"tool --passwd {pw}",
                     f"tool --client-secret {pw}", f"curl -H 'Authorization: Token {pw}' https://api.example.com",
                     f"X-Auth: {pw}", f"X-Auth-Key: {pw}", f"password\u00a0=\u00a0{pw}", f"password\uff1a{pw}",
                     f"password\uff1d{pw}", f"curl -s -X POST https://api.example.com/v1/items -u bob:{pw} -d x",
                     f"run\\ncurl -u bob:{pw}\\nnext"):
            with self.subTest(text=text):
                masked = mask(text)
                self.assertNotIn(pw, masked)
                self.assertNotIn("-proot", masked)

    def test_prose_about_those_flags_stays(self):
        for text in ("Use the --password flag to set it.", "mysql -p dbname", "docker login -p is deprecated",
                     "pass --token instead", "the --secret-name flag", "redis-cli -a is unsafe",
                     "docker run -u 1000:1000 alpine", "git push -u origin main", "Authorization: pending review",
                     "docker login --password-stdin registry.example.com", "tool --token-file ~/.config/token",
                     "tool --api-key $API_KEY", "X-Authenticated: true"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)

    def test_a_long_run_of_command_words_costs_time_in_proportion(self):
        import time
        for unit in ("curl ", "mysql ", "sshpass ", "docker login ", "redis-cli ", "--password ", "curl -u ",
                     "Authorization: ", "curl\\n"):
            with self.subTest(unit=unit):
                took = {}
                for size in (1 << 14, 1 << 18):
                    text = unit * (size // len(unit))
                    started = time.process_time()
                    mask(text)
                    took[size] = time.process_time() - started
                self.assertLess(took[1 << 18], 40 * max(took[1 << 14], 0.01), took)


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

    def test_a_name_with_basic_or_bearer_in_it_does_not(self):
        for name in ("Courses/Basic Statistics/lecture-01.md", "Wedding/Ring Bearer duties/wedding-2026-plan.md",
                     "Spanish/Basic Phrases/week1.md", "Basic Training/2025-06-01.md", "Basic Kubernetes101.md",
                     "Ring Bearer instructions-for-the-wedding-2026.md", "Basic Statistics/Probability 101.md"):
            with self.subTest(name=name):
                self.assertFalse(secret_in_id(name))
        # A real Basic credential is "user:password" in base64.
        for name in ("curl Basic d2FscnVzOndhbHJ1czJzZWNyZXQ5.md", "h=Basic%20d2FscnVzOndhbHJ1czJzZWNyZXQ5"):
            with self.subTest(name=name):
                self.assertTrue(secret_in_id(name))

    def test_an_sk_key_whose_first_capital_comes_late_is_refused(self):
        self.assertTrue(secret_in_id("sk-proj-" + "a" * 129 + "Z"))
        self.assertTrue(secret_in_id("notes/sk-proj-" + "a" * 300 + "Q7.md"))

    def test_a_name_starting_with_an_sk_slug_does_not(self):
        for name in ("sk-proj-roadmap-and-quarterly-goals.md", "sk-admin-dashboard-redesign-notes.md",
                     "sk-learn-pipeline-experiments.md", "Beauty/sk-ii-facial-treatment-essence-review.md",
                     "i18n/sk-SK_translation-notes-2026.md", "sk-learn-tutorial-notebooks@" + "3f2a9c" * 6 + "e1d4",
                     "acme/sk-planner-experiments-2026#12"):
            with self.subTest(name=name):
                self.assertFalse(secret_in_id(name))
        for name in ("notes/sk-ant-api03-" + "Z" * 24 + ".md", "sk-proj-" + "Ab1" * 16, "sk-" + "Ab3dE" * 10,
                     "sk-svcacct-" + "Ab1" * 16, "sk-or-v1-" + "a1" * 32, "keys/sk-" + "Ab3dE" * 10 + "-backup.txt",
                     "keys/sk-" + "Ab3dE" * 10 + "_old.txt", "deepseek-sk-" + "a1b2" * 8 + "-2.md"):
            with self.subTest(name=name):
                self.assertTrue(secret_in_id(name))

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
