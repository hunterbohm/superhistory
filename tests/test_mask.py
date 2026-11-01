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

    def test_a_key_or_a_registry_login_written_as_base64_is_masked(self):
        import base64
        import json
        import textwrap
        pem = "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIWalrusScalar2Secret9BodyPiecesXyzQ2FuYXJ5\n-----END EC PRIVATE KEY-----\n"
        cert = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIUWalrusPublicCertBody\n-----END CERTIFICATE-----\n"
        key64, cert64 = base64.b64encode(pem.encode()).decode(), base64.b64encode(cert.encode()).decode()
        login = base64.b64encode(b"bob:walrus2secret9").decode()
        config64 = base64.b64encode(json.dumps({"auths": {"registry.example.com": {"auth": login}}}).encode()).decode()
        for text in (f"data:\n  tls.crt: {cert64}\n  tls.key: {key64}\n", f"{key64}", "\n".join(textwrap.wrap(key64, 76)),
                     f'{{"tls.key": "{key64}"}}', f".dockerconfigjson: {config64}",
                     json.dumps({"auths": {"registry.example.com": {"auth": login}}}),
                     json.dumps(json.dumps({"auths": {"registry.example.com": {"auth": login}}}))):
            with self.subTest(text=text[:30]):
                masked = mask(text)
                self.assertNotIn(key64[20:40], masked)
                self.assertNotIn(login, masked)
                self.assertNotIn(config64[16:40], masked)
        self.assertIn(cert64, mask(f"data:\n  tls.crt: {cert64}\n  tls.key: {key64}\n"), "a certificate stays")
        for text in ('{"auth": "required"}', '{"auth": "bearer"}', '"auth": "c29tZSBub3Rlcw=="'):
            self.assertEqual(mask(text), text)

    def test_a_base64_file_with_a_private_key_after_other_blocks_is_masked(self):
        import base64
        import textwrap
        key = "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIWalrusScalar2Secret9BodyPiecesXyzQ2FuYXJ5\n-----END EC PRIVATE KEY-----\n"
        cert = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIUWalrusPublicCertBody\n-----END CERTIFICATE-----\n"
        files = ("-----BEGIN EC PARAMETERS-----\nBggqhkjOPQMBBw==\n-----END EC PARAMETERS-----\n" + key,
                 "Bag Attributes\n    localKeyID: 01 02 03\nKey Attributes: <No Attributes>\n" + key,
                 cert + key, "x" + key, "xy" + key)
        for text in files:
            encoded = base64.b64encode(text.encode()).decode()
            for form in (f"tls.key: {encoded}\n", "\n".join(textwrap.wrap(encoded, 76)), f'{{"key": "{encoded}"}}'):
                with self.subTest(file=text[:12], form=form[:10]):
                    self.assertNotIn(encoded[-40:-20], mask(form))
        encoded = base64.b64encode(cert.encode()).decode()
        self.assertEqual(mask(f"tls.crt: {encoded}"), f"tls.crt: {encoded}", "a certificate alone stays")

    def test_a_wrapped_base64_key_is_masked_wherever_its_lines_break(self):
        # Wrapping can split the encoded "PRIVATE KEY" across a line break in both the header and the footer.
        import base64
        import json
        import random
        import textwrap
        r = random.Random(7)

        def body(lines):
            return "\n".join(base64.b64encode(bytes(r.randrange(256) for _ in range(48))).decode() for _ in range(lines))

        cert = f"-----BEGIN CERTIFICATE-----\n{body(8)}\n-----END CERTIFICATE-----\n"
        key = f"-----BEGIN PRIVATE KEY-----\n{body(12)}\n-----END PRIVATE KEY-----\n"
        for pad in range(0, 76, 4):
            before = "#" * pad + cert
            lines = textwrap.wrap(base64.b64encode((before + key).encode()).decode(), 76)
            key_lines = lines[len(before) * 4 // 3 // 76 + 1:]  # the lines that hold the key's bytes alone
            forms = {"lines": "\n".join(lines), "escaped lines": "\\n".join(lines),
                     "YAML": "tls.key: |\n  " + "\n  ".join(lines), "JSON": json.dumps({"tls.key": "\n".join(lines)}),
                     "indented JSON": json.dumps({"tls.key": "\n    ".join(lines)})}
            for form, text in forms.items():
                with self.subTest(pad=pad, form=form):
                    masked = mask(f"the bundle:\n{text}\nThanks, Bob")
                    self.assertEqual([line for line in key_lines if line in masked], [])
                    self.assertTrue(masked.startswith("the bundle:\n") and masked.endswith("\nThanks, Bob"), masked)

    def test_a_base64_key_wrapped_at_any_width_is_masked(self):
        import base64
        import re
        import textwrap
        encoded = base64.b64encode(b"-----BEGIN PRIVATE KEY-----\nFakeKeyBody123456\n-----END PRIVATE KEY-----\n").decode()
        body = encoded[40:60]  # the encoded "FakeKeyBody123456"
        for width in (4, 8, 12, 16, 40, 64, 76, len(encoded)):
            for brk in ("\n", "\\n", "\n  "):
                with self.subTest(width=width, brk=brk):
                    masked = mask("Notes\n" + brk.join(textwrap.wrap(encoded, width)) + "\nThanks, Bob")
                    self.assertNotIn(body, re.sub(r"\s+|\\n", "", masked))
                    self.assertTrue(masked.startswith("Notes\n"), "a heading above the block stays: " + masked)
                    self.assertTrue(masked.endswith("\nThanks, Bob"), masked)

    def test_words_on_their_own_lines_after_a_base64_key_stay(self):
        import base64
        import textwrap
        key = ("-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(bytes(range(200))).decode()
               + "\n-----END PRIVATE KEY-----\n")
        encoded = base64.b64encode(key.encode()).decode()
        for width in (12, 64, 76, len(encoded)):
            lines = textwrap.wrap(encoded, width)
            for after in ("\nThanks\nAlbert", "\nCheers", "\nThanks\nAlbert\n\nnext paragraph", "\nJohn",
                          "\nBest\nJohn"):
                with self.subTest(width=width, after=after):
                    masked = mask("Notes\n" + "\n".join(lines) + after)
                    self.assertEqual(masked, "Notes\n[masked]" + after)

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

    def test_an_authorization_value_with_no_scheme_word_is_masked(self):
        for text in ("use Authorization: Qx7pass2026zzQ9 for the staging API", "Authorization: Qx7pass2026zzQ9 (expires Friday)",
                     "Authorization=SecretGarden99x is on the fridge", "Authorization: SCRAM-SHA-256 Qx7pass2026zzQ9",
                     "Proxy-Authorization: AWS4-HMAC-SHA256 Qx7pass2026zzQ9"):
            with self.subTest(text=text):
                masked = mask(text)
                self.assertNotIn("Qx7pass2026", masked)
                self.assertNotIn("Garden99x", masked)
        self.assertEqual(mask("Authorization: pending review"), "Authorization: pending review")

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
