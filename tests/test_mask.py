import json
import unittest
from unittest import mock

from kb.mask import INVISIBLE, is_auth_code, is_auth_noise, mask, secret_in_id


def _der(tag, *parts):
    """A DER element: its tag, its length and `parts` as its content."""
    body = b"".join(parts)
    width = (len(body).bit_length() + 7) // 8
    size = bytes([len(body)]) if len(body) < 0x80 else bytes([0x80 | width]) + len(body).to_bytes(width, "big")
    return bytes([tag]) + size + body


def _rsa_algorithm():
    return _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010101")), _der(0x05))


def made_up_public_key(r):
    """A SubjectPublicKeyInfo of an RSA key with made-up numbers."""
    numbers = _der(0x30, _der(0x02, b"\0" + bytes(r.randrange(256) for _ in range(256))), _der(0x02, b"\x01\x00\x01"))
    return _der(0x30, _rsa_algorithm(), _der(0x03, b"\0" + numbers))


def made_up_certificate(r, request=False):
    """An X.509 certificate, or a certificate request, whose numbers and signature are made up."""
    signed_with = _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d01010b")), _der(0x05))
    name = _der(0x30, _der(0x31, _der(0x30, _der(0x06, bytes.fromhex("550403")), _der(0x0c, b"walrus.example"))))
    if request:
        info = _der(0x30, _der(0x02, b"\0"), name, made_up_public_key(r), _der(0xa0))
    else:
        validity = _der(0x30, _der(0x17, b"261101000000Z"), _der(0x17, b"261201000000Z"))
        info = _der(0x30, _der(0xa0, _der(0x02, b"\x02")), _der(0x02, bytes(r.randrange(1, 128) for _ in range(16))),
                    signed_with, name, validity, name, made_up_public_key(r))
    return _der(0x30, info, signed_with, _der(0x03, b"\0" + bytes(r.randrange(256) for _ in range(256))))


def made_up_private_key(r):
    """A PKCS#8 private key's DER with a made-up key inside."""
    return _der(0x30, _der(0x02, b"\0"), _rsa_algorithm(), _der(0x04, bytes(r.randrange(256) for _ in range(600))))


def as_older_row(test):
    """The test read as a stored row from a kb before keys were masked whole (a mask version with no revision mark), as
    remask reads one: there an older kb may have left a key's rest after a placeholder, which fails closed."""
    def run(self):
        from kb import mask as mask_module
        with mask_module.stored_row("0123456789abcdef"):
            test(self)
    run.__name__, run.__doc__ = test.__name__, test.__doc__
    return run


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


class ModuleTest(unittest.TestCase):
    def test_no_name_in_the_mask_module_is_defined_twice(self):
        # A second definition replaces the first for every reader that runs after it, as "_HEADER_START" once did.
        import ast
        import collections
        import kb.mask
        with open(kb.mask.__file__, encoding="utf-8") as source:
            tree = ast.parse(source.read())
        names = collections.Counter()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names[node.name] += 1
            elif isinstance(node, ast.Assign):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        self.assertEqual([name for name, n in names.items() if n > 1], [])


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
        import re
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
                    shown = set(re.split(r'\\n|\s+|"', masked))  # a certificate above stays, and may hold a short line
                    self.assertEqual([line for line in key_lines if line in shown], [])
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

    def test_words_between_two_base64_keys_stay(self):
        import base64
        import textwrap
        one = "-----BEGIN PRIVATE KEY-----\nFakeKeyBody123456\n-----END PRIVATE KEY-----\n"
        two = ("-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(bytes(range(200))).decode()
               + "\n-----END PRIVATE KEY-----\n")
        for key in (one, two):
            encoded = base64.b64encode(key.encode()).decode()
            for width in (12, 64, 76, len(encoded)):
                block = "\n".join(textwrap.wrap(encoded, width))
                for between in ("\nThanks\nJohn\n", "\nNotes\n", "\nBest\nJohn\nNext key:\n"):
                    with self.subTest(key=len(key), width=width, between=between):
                        self.assertEqual(mask(block + between + block), "[masked]" + between + "[masked]")

    def test_a_field_name_holding_encoded_key_words_keeps_its_name_and_masks_its_value(self):
        import base64
        for name in ("private_key_", "password_", "secret_", "api_key_"):
            for words in (b"PRIVATE KEYX", b"SECRET KEYXY", b"the PRIVATE KEY"):
                for sep in (": ", "="):
                    field = name + base64.b64encode(words).decode()
                    with self.subTest(field=field, sep=sep):
                        self.assertEqual(mask(f"{field}{sep}Abcdefgh12345678"), f"{field}{sep}[masked]")

    def test_a_heading_above_a_base64_key_stays_at_any_width(self):
        import base64
        import textwrap
        from tests.ordinary import WORDS
        key = ("-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(bytes(range(90))).decode()
               + "\n-----END PRIVATE KEY-----\n")
        encoded = base64.b64encode(key.encode()).decode()
        for word in ("rent",) + WORDS:
            for heading in (word, word.title(), word.upper()):
                for width in sorted({4, len(heading), 64, 76}):
                    with self.subTest(heading=heading, width=width):
                        block = "\n".join(textwrap.wrap(encoded, width))
                        self.assertEqual(mask(f"{heading}\n{block}\nnext steps"), f"{heading}\n[masked]\nnext steps")

    def test_base64_of_text_about_keys_or_of_a_public_block_stays(self):
        import random
        import textwrap
        from tests import ordinary
        for seed in range(300):
            encoded = ordinary.encoded(random.Random(f"encoded:{seed}"))
            for width in sorted({1, 4, 64, 76, len(encoded)}):
                text = "\n".join(textwrap.wrap(encoded, width))
                with self.subTest(seed=seed, width=width):
                    self.assertEqual(mask(f"note: {text}\nnext steps"), f"note: {text}\nnext steps")

    def test_a_base64_pgp_secret_key_block_is_masked(self):
        import base64
        import json
        import random
        import textwrap
        r = random.Random(5)
        body = "\n".join(textwrap.wrap(base64.b64encode(bytes(r.randrange(256) for _ in range(150))).decode(), 64))
        for label in ("PGP SECRET KEY BLOCK", "PGP PRIVATE KEY BLOCK"):
            armored = (f"-----BEGIN {label}-----\nVersion: GnuPG v2\n\n{body}\n=AbCd\n-----END {label}-----\n")
            encoded = base64.b64encode(armored.encode()).decode()
            for text, masked in ((f"key: {encoded}\nnext steps", "key: [masked]\nnext steps"),
                                 ("Plan\n" + "\n".join(textwrap.wrap(encoded, 76)) + "\nnext steps",
                                  "Plan\n[masked]\nnext steps"),
                                 (json.dumps({"key": "\n".join(textwrap.wrap(encoded, 64))}), '{"key": "[masked]"}')):
                with self.subTest(label=label, text=text[:12]):
                    self.assertEqual(mask(text), masked)

    def test_a_base64_key_hides_all_the_mask_hides_without_its_reading(self):
        # Read first, the key's placeholder cut short a quoted password's reading ("[masked],Rest" stayed).
        import base64
        import random
        import textwrap
        from unittest import mock
        import kb.mask as m
        r = random.Random(17)
        body = base64.b64encode(bytes(r.randrange(256) for _ in range(48))).decode()
        key = base64.b64encode(f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----\n".encode()).decode()
        wrapped = "\n".join(textwrap.wrap(key, 76))
        self.assertEqual(mask(f'password="{key},WalrusFixture9"'), 'password="[masked]"')

        def hidden(text):
            masked, origin = m.mask_with_origin(text)
            return set(range(len(text))) - {c for c in origin if c >= 0}

        for k in (key, wrapped):
            for form in ('password="{k},WalrusFixture9"', "password='{k},WalrusFixture9'", '{{"password": "{k},Wal9"}}',
                         "password={k},WalrusFixture9", "--password {k} next", "secret: {k},WalrusFixture9 done",
                         "token={k}&next=1", "Authorization: Bearer {k}", "api_key = '{k}' # plan"):
                text = form.format(k=k)
                with self.subTest(form=form, wrapped=k is wrapped):
                    with mock.patch.object(m, "_encoded_key_spans", lambda text: []):
                        without = hidden(text)
                    self.assertEqual(without - hidden(text), set(), mask(text))

    def test_a_base64_key_with_an_invisible_character_inside_is_masked_whole(self):
        import base64
        import json
        import random
        import textwrap
        r = random.Random(19)
        body = "\n".join(textwrap.wrap(base64.b64encode(bytes(r.randrange(256) for _ in range(300))).decode(), 64))
        pem = f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----\n"
        key = base64.b64encode(pem.encode()).decode()
        lines = textwrap.wrap(key, 76)
        block = "\n".join(lines)
        for invisible in ("\u200b", "\u00ad", "\u2060", "\ufeff", "\x00", "\x1b[0m"):
            for at in range(40, len(block) - 40, 37):
                if block[at - 1] == "\n" or block[at] == "\n":
                    continue
                text = block[:at] + invisible + block[at:]
                for form, written, masked in (("lines", text, "[masked]"),
                                              ("JSON", json.dumps({"k": text}), '{"k": "[masked]"}'),
                                              ("JSON in JSON", json.dumps(json.dumps({"k": text})),
                                               json.dumps(json.dumps({"k": "[masked]"}))),
                                              ("repr", repr(text), "'[masked]'")):
                    with self.subTest(invisible=invisible, at=at, form=form):
                        self.assertEqual(mask(f"Plan\n{written}\nnext steps"), f"Plan\n{masked}\nnext steps")

    def test_base64_after_a_base64_key_stays(self):
        # A certificate's DER starts as a key's body does ("MII..."), but follows the key's footer.
        import base64
        import json
        import random
        import textwrap
        r = random.Random(23)
        body = base64.b64encode(bytes(r.randrange(256) for _ in range(48))).decode()
        key = base64.b64encode(f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----\n".encode()).decode()
        cert = base64.b64encode(made_up_certificate(r)).decode()
        for k in (key, "\n".join(textwrap.wrap(key, 76))):
            public = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwalrus"
            for after in (cert, "\n".join(textwrap.wrap(cert, 76)), public):
                with self.subTest(wrapped="\n" in k, after=after[:12]):
                    # A public key cut short is no public material (_public_material): on its own line after the
                    # key's placeholder it reads as the rest of a key, as the next mask reads it (kb's display masks
                    # stored text again) and as main reads such a line after any placeholder. So this mask hides it
                    # too, and masked text masks to itself. A certificate whole stays, and so does a JSON field.
                    shown = "" if after is public else f"{after}\n"
                    once = mask(f"{k}\n{after}\nnext steps")
                    # a public key cut short is a key's rest with no "=", so its next first token goes with it
                    # (coordinator's ruling, Nov 6); the words after it stay
                    self.assertEqual(once, "[masked] steps" if after is public else f"[masked]\n{shown}next steps")
                    self.assertEqual(mask(once), once)
                    self.assertEqual(mask(json.dumps({"k": k, "c": after})), json.dumps({"k": "[masked]", "c": after}))

    def test_a_masked_base64_key_and_the_base64_after_it_mask_to_themselves(self):
        # kb masks stored text again when it shows it; the key's placeholder then stood before the certificate.
        import base64
        import random
        import textwrap
        r = random.Random(29)
        body = base64.b64encode(bytes(r.randrange(256) for _ in range(48))).decode()
        key = base64.b64encode(f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----\n".encode()).decode()
        cert = base64.b64encode(made_up_certificate(r)).decode()
        for after in (cert, "\n".join(textwrap.wrap(cert, 76))):
            for k in (key, "\n".join(textwrap.wrap(key, 76))):
                with self.subTest(wrapped="\n" in k, after="\n" in after):
                    once = mask(f"{k}\n{after}\nnext steps")
                    self.assertEqual(once, f"[masked]\n{after}\nnext steps")
                    self.assertEqual(mask(once), once)

    @as_older_row
    def test_after_a_placeholder_base64_stays_masked_unless_it_decodes_whole_as_public_material(self):
        # An older kb's mask left a key's body after its placeholder, whole or in part. Only base64 that decodes whole
        # as a certificate, a certificate request, a public key or an OpenSSH public key is shown.
        import base64
        import random
        import struct
        import textwrap
        r = random.Random(31)
        public = {"certificate": made_up_certificate(r), "request": made_up_certificate(r, request=True),
                  "public key": made_up_public_key(r)}
        openssh = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(r.randrange(256)
                                                                                    for _ in range(32))
        for name, der in [*public.items(), ("OpenSSH public key", openssh)]:
            encoded = base64.b64encode(der).decode()
            for lines in (encoded, "\n".join(textwrap.wrap(encoded, 64)), "\n".join(textwrap.wrap(encoded, 76))):
                with self.subTest(public=name, lines=lines.count("\n")):
                    text = f"[masked]\n{lines}\nnext steps"
                    self.assertEqual(mask(text), text)
            cut = base64.b64encode(der[:len(der) * 2 // 3]).decode()
            with self.subTest(cut_short=name):
                # a cut with no "=" can run on: its next first token goes with it (coordinator's ruling, Nov 6)
                self.assertEqual(mask(f"[masked]\n{cut}\nnext steps"),
                                 "[masked]\nnext steps" if cut.endswith("=") else "[masked] steps")
        noise = base64.b64encode(bytes(r.randrange(256) for _ in range(200))).decode()
        for name, lines in (("random", noise), ("random, starting as a body does", "MIIE" + noise),
                            ("random, wrapped", "\n".join(textwrap.wrap(noise, 64)))):
            with self.subTest(neither=name):
                self.assertEqual(mask(f"[masked]\n{lines}\nnext steps"), "[masked]\nnext steps")

    @as_older_row
    def test_each_part_of_a_private_keys_body_after_a_placeholder_stays_masked(self):
        import base64
        import json
        import random
        import textwrap
        r = random.Random(37)
        lines = textwrap.wrap(base64.b64encode(made_up_private_key(r)).decode(), 64)
        half = len(lines) // 2
        first, second = lines[:half], lines[half:]
        joined = "".join(second)
        parts = {"first half": "\n".join(first), "second half": "\n".join(second),
                 "second half, with its footer": "\n".join(second) + "\n-----END PRIVATE KEY-----",
                 "second half, re-wrapped": "\n".join(textwrap.wrap(joined, 76)),
                 "second half, one line": joined}
        for name, part in parts.items():
            for before in ("\n", "\u200b", "\n\u200b"):
                with self.subTest(part=name, before=before):
                    masked = mask(f"[masked]{before}{part}\nnext steps")
                    readable = masked.replace("\n", "").replace("\u200b", "")
                    self.assertEqual([joined[i:i + 12] for i in range(0, len(joined) - 12, 12)
                                      if joined[i:i + 12] in readable], [])
                    self.assertEqual([line for line in first if line in masked], [])
                    # the first half's last line can run on, so the next first token goes with it (coordinator's
                    # ruling, Nov 6); the words after it stay
                    self.assertTrue(masked.startswith("[masked]")
                                    and masked.endswith(" steps" if name == "first half" else "\nnext steps"), masked)
            with self.subTest(part=name, form="JSON"):
                masked = mask(json.dumps({"k": f"[masked]\n{part}", "note": "next steps"}))
                self.assertEqual([line for line in lines if line in masked], [])
                self.assertIn('"note": "next steps"', masked)

    def test_ordinary_text_after_a_keys_rest_stays(self):
        import base64
        import random
        import textwrap
        r = random.Random(43)
        body = "\n".join(textwrap.wrap(base64.b64encode(made_up_private_key(r)).decode(), 64))
        for after in ("next steps", "The rest of the note is here.", "tls.crt: none", "  - deploy the ledger"):
            for brk in ("\n", "\r\n"):
                with self.subTest(after=after, brk=brk):
                    self.assertEqual(mask(f"[masked]{brk}{body.replace(chr(10), brk)}{brk}{after}"),
                                     f"[masked]{brk}{after}")
        self.assertEqual(mask("[masked]\nnext steps"), "[masked]\nnext steps")

    def test_a_body_line_with_words_after_it_on_its_line_stays_masked(self):
        # A snippet joins a key's lines, so its last line can carry the next words ("MIIEvQ... notes deploy").
        import base64
        import random
        r = random.Random(53)
        line = base64.b64encode(made_up_private_key(r)).decode()[:64]
        for text in (f"[masked]{line[20:]}\n{line} notes deploy harbor", f"[masked]\n{line}\n{line} notes deploy"):
            with self.subTest(text=text[:20]):
                self.assertNotIn(line[8:40], mask(text))

    @as_older_row
    def test_a_placeholder_inside_another_keys_rest_reads_its_own(self):
        # A rest that stops at a JSON string's end runs on through the last token before its footer; a placeholder it
        # passes on that line still reads its own rest, which here runs on to the next line.
        import base64
        import random
        import textwrap
        r = random.Random(61)
        lines = textwrap.wrap(base64.b64encode(made_up_private_key(r)).decode(), 64)
        text = (f'{{"key": "[masked]\\n{lines[0]}\\n{lines[1]}"}} the plan [masked]<br>{lines[2]}<br>{lines[3]}<br>'
                f"-----END PRIVATE KEY----- notes # [masked]# {lines[4]}\n# {lines[5][:32]}")
        masked = mask(text)
        self.assertEqual([line[:32] for line in lines[:6] if line[:32] in masked], [], masked)

    def test_many_key_rests_in_one_paragraph_cost_time_in_proportion(self):
        import base64
        import random
        import textwrap
        import time
        body = "\n".join(textwrap.wrap(base64.b64encode(made_up_private_key(random.Random(67))).decode(), 64)[:3])
        for unit in (f"[masked]\n{body}\nnext steps\n", "[masked]x\nnext steps\n[masked] Zq00195081Wm4Np8Rt2Kd9\n"):
            with self.subTest(unit=unit[:12]):
                took = {}
                for n in (500, 2000):
                    started = time.process_time()
                    mask(unit * n)
                    took[n] = time.process_time() - started
                self.assertLess(took[2000], 6 * max(took[500], 0.02), took)

    def test_an_id_reads_a_keys_rest_as_main_does(self):
        # An id that reads as holding a secret is refused, with its item: only a key's body start begins its rest.
        import base64
        import random
        r = random.Random(59)
        line = base64.b64encode(bytes(r.randrange(256) for _ in range(48))).decode()
        self.assertFalse(secret_in_id(f"plan [masked]\n{line}"))
        self.assertTrue(secret_in_id(f"plan [masked]\nMIIE{line}"))

    def test_a_pem_key_then_an_unarmoured_certificate_and_text_shows_both(self):
        # main and #29 took the certificate and the text after it for the key's rest.
        import base64
        import random
        import textwrap
        r = random.Random(47)
        body = "\n".join(textwrap.wrap(base64.b64encode(made_up_private_key(r)).decode(), 64))
        pem = f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----\n"
        cert = base64.b64encode(made_up_certificate(r)).decode()
        for after in (cert, "\n".join(textwrap.wrap(cert, 64)), "\n".join(textwrap.wrap(cert, 76))):
            with self.subTest(lines=after.count("\n")):
                once = mask(f"{pem}{after}\nnext steps")
                self.assertEqual(once, f"[masked]\n{after}\nnext steps")
                self.assertEqual(mask(once), once)

    def test_base64_of_prose_that_quotes_a_key_delimiter_stays(self):
        import base64
        import textwrap
        for sentence in (b"PEM files use -----BEGIN PRIVATE KEY----- as their opening delimiter.",
                         b"A key file ends with -----END RSA PRIVATE KEY----- on its last line.",
                         b"gpg writes -----BEGIN PGP SECRET KEY BLOCK----- first, then the armor headers.",
                         b"Look for '-----BEGIN OPENSSH PRIVATE KEY-----' in the file.\nThen check its mode."):
            encoded = base64.b64encode(sentence).decode()
            for width in range(1, len(encoded) + 1):
                text = "\n".join(textwrap.wrap(encoded, width))
                with self.subTest(sentence=sentence[:20], width=width):
                    self.assertEqual(mask(f"note: {text}\nnext steps"), f"note: {text}\nnext steps")

    def test_many_keys_in_one_block_cost_time_in_proportion(self):
        import base64
        import time
        key = base64.b64encode(b"-----BEGIN PRIVATE KEY-----\nFakeKeyBody123456\n-----END PRIVATE KEY-----\n").decode()
        took = {}
        for n in (2000, 8000):
            started = time.process_time()
            masked = mask((key + "\nThanks\nJohn\n") * n)
            took[n] = time.process_time() - started
            self.assertEqual((masked.count("[masked]"), masked.count("Thanks")), (n, n))
        self.assertLess(took[8000], 6 * max(took[2000], 0.02))

    def test_prose_about_those_formats_stays(self):
        for text in ("PGP keys are signed in a block", "Private-Lines counts the lines", "an AGE-SECRET-KEY-1 identity",
                     "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nmQENBGcW1aEBCAD\n-----END PGP PUBLIC KEY BLOCK-----"):
            with self.subTest(text=text[:20]):
                self.assertEqual(mask(text), text)


class ReviewTwoTest(unittest.TestCase):
    """#34's second review: a short fragment of a key's body after a line of words in its rest, and the cost of
    reading invisible characters in text escaped as JSON many times."""

    def test_a_short_fragment_after_a_line_of_words_stays_masked(self):
        import base64
        import random
        import textwrap
        r = random.Random(71)
        lines = textwrap.wrap(base64.b64encode(made_up_private_key(r)).decode(), 64)
        last = lines[-1]
        for fragment in (last[-12:], last[-8:], last[-4:], "wbZksUpjdgM=", "Zk9=", "aB3c"):
            for words in ("next steps", "see the runbook"):
                text = f"[masked]\n{lines[0]}\n{words}\n{fragment}\n"
                with self.subTest(fragment=fragment, words=words):
                    masked = mask(text)
                    self.assertNotIn(fragment, masked)
                    self.assertNotIn(lines[0], masked)
        # the words after a lone body line's first token stay (coordinator's ruling, Nov 6: that token goes with the key)
        self.assertEqual(mask(f"[masked]\n{lines[0]}\nnext steps\nsee the runbook\n"),
                         "[masked] steps\nsee the runbook\n")

    def test_text_escaped_as_json_many_times_costs_time_in_proportion(self):
        import json
        import time
        took = {}
        for n in (10, 12):
            text = {"path": r"C:\tmp\file", "note": "\u200b"}
            for _ in range(n):
                text = json.dumps(text)
            started = time.process_time()
            self.assertEqual(mask(text), text)
            took[n] = time.process_time() - started
        # each level doubles the backslashes: 12 levels are four times the text of 10
        self.assertLess(took[12], 8 * max(took[10], 0.05), took)


class ReviewThreeTest(unittest.TestCase):
    """#34's third review: a key's fragment shaped like a capitalised word after a line of words, and base64 after a
    password's placeholder read as a key's rest."""

    def test_a_fragment_shaped_like_a_word_after_a_line_of_words_stays_masked(self):
        body = "MC4CAQAwBQYDK2VwBCIEIB8fZU0y2GY4kmBqqFAZvcDVa7p01PZM4AlhlsBc"
        for fragment in ("Bcsv", "Bc", "MIIE", "kqzt", "2026", "x/Yz", "QmFz"):
            for words in ("next steps", "see the runbook"):
                with self.subTest(fragment=fragment, words=words):
                    masked = mask(f"[masked]\n{body}\n{words}\n{fragment}\n")
                    self.assertNotIn(fragment, masked.replace(words, ""))
                    self.assertNotIn(body, masked)
        # the words after a lone body line's first token stay (coordinator's ruling, Nov 6: that token goes with the key)
        self.assertEqual(mask(f"[masked]\n{body}\nnext steps\nSee the runbook.\n"),
                         "[masked] steps\nSee the runbook.\n")

    def test_base64_after_a_passwords_placeholder_is_no_keys_rest(self):
        import base64
        encoded = base64.b64encode(b'{"note":"next steps","enabled":true,"mode":"normal"}').decode()
        for line in ("password: TestValue9!", "PASSWORD=TestValue9!", '"password": "TestValue9!",', "api_key=Zq7vRt2LmX9kQ"):
            text = f"{line}\n{encoded}\nnext steps"
            with self.subTest(line=line):
                masked = mask(text)
                self.assertNotIn("TestValue9!", masked)
                self.assertIn(encoded, masked)
                self.assertIn("next steps", masked)
        # a key's own body line after such a placeholder is still a key's rest, as on main
        line = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj"
        self.assertEqual(mask(f"private_key: [masked]\n{line}\n"), "private_key: [masked]")


class ReviewFourTest(unittest.TestCase):
    """#34's fourth review: a key's fragment that decodes to printable text after an older kb's placeholder, encoded
    JSON before a one-word line after a password's placeholder, a long run of backslashes in the tail's reading, and a
    character a reader does not see between an escaped line break's backslash and its "n"."""

    def test_a_fragment_that_decodes_as_text_after_an_older_placeholder_stays_masked(self):
        # an older kb's row: its placeholder came with the text
        for text in ("[masked]\nMHcwIG1hZGUgdXAgcHJpdmF0ZSBzZWVkIDEyMzQ1\n",
                     "notes\n[masked]\nMHcwIG1hZGUgdXAgcHJpdmF0ZSBzZWVkIDEyMzQ1\nnext steps\n"):
            with self.subTest(text=text[:20]):
                self.assertNotIn("MHcwIG1hZGUgdXAgcHJpdmF0ZSBzZWVkIDEyMzQ1", mask(text))

    def test_encoded_json_after_a_passwords_placeholder_stays_before_any_line(self):
        import base64
        encoded = base64.b64encode(b'{"note":"next steps","enabled":true,"mode":"normal"}').decode()
        for after in ("Done", "next steps", "ok", "2026-11-03"):
            text = f"password: TestValue9!\n{encoded}\n{after}\n"
            with self.subTest(after=after):
                self.assertEqual(mask(text), f"password: [masked]\n{encoded}\n{after}\n")

    def test_a_long_run_of_backslashes_after_a_keys_rest_costs_time_in_proportion(self):
        import json
        import time
        took = {}
        for n in (9, 12):
            value = {"path": r"C:\tmp\file", "note": "\u200b"}
            for _ in range(n):
                value = json.dumps(value)
            text = "[masked]\nMC4CAQAwBQYDK2VwBCIEIB8fZU0y2GY4kmBqqFAZvcDVa7p01PZM4AlhlsBc\n\n" + value
            started = time.process_time()
            self.assertEqual(mask(text), "[masked]\n\n" + value)
            took[n] = time.process_time() - started
        # each level doubles the backslashes: 12 levels are eight times the text of 9
        self.assertLess(took[12], 16 * max(took[9], 0.05), took)

    def test_an_invisible_character_inside_an_escaped_line_break(self):
        import base64
        import json
        import textwrap
        pem = (b"-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(bytes.fromhex("302e020100300506032b657004220420")
               + bytes(range(32))) + b"\n-----END PRIVATE KEY-----\n")
        lines = textwrap.wrap(base64.b64encode(pem).decode(), 64)
        text = json.dumps("\n".join(lines))
        at = text.rfind("\\n") + 1
        text = text[:at] + "\u200b" + text[at:]
        masked = mask(text)
        for line in lines:
            self.assertNotIn(line[8:40], masked)


class ReviewFiveTest(unittest.TestCase):
    """#34's fifth review and the coordinator's rule after it: a key's rest after a placeholder fails closed only in a
    stored row from a kb before keys were masked whole; in text read fresh a key is masked whole, so nothing of it
    follows its placeholder."""

    def pem(self, body_lines):
        return "-----BEGIN PRIVATE KEY-----\n" + "\n".join(body_lines) + "\n-----END PRIVATE KEY-----\n"

    def test_a_split_or_rewrapped_key_read_fresh_is_masked_whole_in_one_pass(self):
        import base64
        import random
        import textwrap
        from kb import mask as mask_module
        body = base64.b64encode(made_up_private_key(random.Random(83))).decode()
        half = len(body) // 2
        shapes = {"split by a zero-width space": textwrap.wrap(body[:half] + "\u200b" + body[half:], 64),
                  "re-wrapped at 76": textwrap.wrap(body, 76), "on one line": [body],
                  "re-wrapped, then split": textwrap.wrap(body[:half], 40) + ["\u200b" + body[half:]]}
        for name, lines in shapes.items():
            text = self.pem(lines)
            for form in (text, json.dumps(text)):
                with self.subTest(shape=name, json=form != text):
                    once = mask_module._mask_once(form)
                    for piece in (body[:24], body[half - 12:half], body[half:half + 24], body[-24:]):
                        self.assertNotIn(piece, once)
                    # the key whole, its footer's own line break kept
                    self.assertEqual(mask(form), json.dumps("[masked]\n") if form != text else "[masked]\n")

    def test_base64_json_after_a_passwords_placeholder_stays_through_storage(self):
        import base64
        import tempfile
        from kb import store
        encoded = base64.b64encode(b'{"note":"next steps","enabled":true,"mode":"normal"}').decode()
        text = f"password: TestValue9!\n{encoded}\nDone\n"
        with tempfile.TemporaryDirectory() as folder:
            db = store.connect(folder + "/kb.sqlite")
            try:
                store.store_item(db, "notes", "example", "2026-11-03", "Plan", text, {})
                content = db.execute("SELECT content FROM docs").fetchone()[0]
            finally:
                db.close()
        self.assertEqual(content, f"password: [masked]\n{encoded}\nDone\n")

    def test_an_older_rows_placeholder_still_fails_closed(self):
        from kb import mask as mask_module
        rest = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ejAxMjM0"
        with mask_module.stored_row("0123456789abcdef"):  # a version from before keys were masked whole
            self.assertNotIn(rest, mask(f"[masked]\n{rest}\n"))
        with mask_module.stored_row(mask_module.VERSION):
            self.assertIn(rest, mask(f"[masked]\n{rest}\n"))

    def test_remask_reads_an_older_kbs_row_fail_closed_and_its_own_rows_as_main(self):
        import base64
        import tempfile
        from kb import store
        rest = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ejAxMjM0"
        encoded = base64.b64encode(b'{"note":"next steps","enabled":true,"mode":"normal"}').decode()
        for version, text, kept in (("0123456789abcdef", f"[masked]\n{rest}\nnext steps\n", False),
                                    ("r20-0123456789abcdef", f"password: [masked]\n{encoded}\nDone\n", True)):
            with tempfile.TemporaryDirectory() as folder, self.subTest(version=version):
                db = store.connect(folder + "/kb.sqlite")
                try:
                    store.store_item(db, "notes", "example", "2026-11-03", "Plan", "placeholder text", {})
                    db.execute("UPDATE docs SET content=?", (text,))
                    db.execute("UPDATE docs SET masked=?", (version,))  # after the trigger that clears it
                    store.remask(db)
                    content = db.execute("SELECT content FROM docs WHERE kind='doc'").fetchone()[0]
                finally:
                    db.close()
                self.assertEqual((rest in content) or (encoded in content), kept, content)

    def test_an_all_capitals_fragment_with_no_vowel_inside_a_line_of_words(self):
        text = "[masked]\nMC4CAQAwBQYDK2VwBCIEIAAABCDFAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\nnotes BCDF"
        self.assertNotIn("BCDF", mask(text))

    def test_an_escaped_invisible_character_inside_an_escaped_line_break(self):
        import base64
        import io
        import textwrap
        from kb.mask import MaskedStream
        body = base64.b64encode(bytes.fromhex("302e020100300506032b657004220420") + bytes(range(32)))
        pem = b"-----BEGIN PRIVATE KEY-----\n" + body + b"\n-----END PRIVATE KEY-----\n"
        lines = textwrap.wrap(base64.b64encode(pem).decode(), 64)
        joined = "\\n".join(lines)
        at = joined.rfind("\\n") + 1
        text = json.dumps(joined[:at] + "\u200b" + joined[at:])
        out = io.StringIO()
        MaskedStream(out).write(text)
        for line in lines:
            self.assertNotIn(line[8:40], out.getvalue())
            self.assertNotIn(line[8:40], mask(text))

    def test_a_long_run_of_backslashes_in_a_keys_rest_costs_time_in_proportion(self):
        import functools
        import time
        took = {}
        for n in (11, 14):
            value = functools.reduce(lambda x, _: json.dumps(x), range(n), {"path": r"C:\tmp\file"})
            text = "[masked]\nMC4CAQAwBQYDK2VwBCIEIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f\n" + value
            started = time.process_time()
            mask(text)
            took[n] = time.process_time() - started
        # each level doubles the backslashes: 14 levels are eight times the text of 11
        self.assertLess(took[14], 16 * max(took[11], 0.05), took)


class ReviewSixTest(unittest.TestCase):
    """#34's sixth review: a fragment of digits and "/" after a line of words, and base64 of a sentence that quotes a
    key's header where its wrapping happens to break."""

    def test_a_fragment_of_digits_and_slashes_after_a_line_of_words_stays_masked(self):
        import base64
        tail = base64.b64decode("0123/567")
        body = base64.b64encode(bytes.fromhex("302e020100300506032b657004220420") + bytes(range(26)) + tail).decode()
        for fragment in (body[-8:], "0123+567", "20261103", "ab/cd"):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, mask(f"[masked]\n{body[:-8]}\nnotes deploy {fragment}"))

    def test_base64_of_a_sentence_quoting_a_header_at_a_wrap_stays(self):
        import base64
        import textwrap
        for sentence in (b"PEM files often start with this exact opening delimiter: -----BEGIN PRIVATE KEY-----\n"
                         b"Read the documentation before importing.\n",
                         b"A key file's last line is the closing delimiter, -----END PRIVATE KEY-----\nas shown.\n"):
            encoded = base64.b64encode(sentence).decode()
            for width in range(40, 81, 4):
                text = "\n".join(textwrap.wrap(encoded, width))
                with self.subTest(sentence=sentence[:20], width=width):
                    self.assertEqual(mask(text), text)


class ReviewSevenTest(unittest.TestCase):
    """#34's seventh review: a key's fragment split by an invisible character inside a line of words, and base64 of
    documentation whose quoted header follows a short label at a wrap."""

    def test_a_fragment_split_by_an_invisible_character_after_a_line_of_words_stays_masked(self):
        head = "[masked]\nMC4CAQAwBQYDK2VwBCIEIAABAgMEBQYHCAkKCwwNDg8QERIT\nnotes deploy "
        for fragment in ("ABCDEFGH​abcdefgh", "Zq7v​Rt2L", "abcd­EFGH1234"):
            with self.subTest(fragment=repr(fragment)):
                masked = mask(head + fragment)
                self.assertNotIn(fragment.split("​")[0].split("­")[0], masked)

    def test_base64_of_documentation_quoting_a_header_after_a_short_label_stays(self):
        import base64
        import textwrap
        for label in (b"Note: ", b"See: ", b"e.g. ", b"Tip:\t"):
            doc = (b"The following example shows the opening delimiter.\n" + label
                   + b"-----BEGIN PRIVATE KEY-----\nRead the documentation before importing.\n")
            encoded = base64.b64encode(doc).decode()
            for width in range(40, 81, 4):
                text = "\n".join(textwrap.wrap(encoded, width))
                with self.subTest(label=label, width=width):
                    self.assertEqual(mask(text), text)


class ReviewEightTest(unittest.TestCase):
    """#34's eighth review: a key's fragment split by an invisible character after a line of words, written as JSON in
    JSON, and a key file that starts with a byte order mark, written as base64."""

    def test_a_split_fragment_after_a_line_of_words_stays_masked_in_json_at_any_depth_and_in_a_stored_row(self):
        import json
        from kb import mask as mask_module
        head = "[masked]\nMC4CAQAwBQYDK2VwBCIEIAABAgMEBQYHCAkKCwwNDg8QERIT\nnotes deploy "
        for fragment in ("ABCDEFGH\u200babcdefgh", "Zq7v\u200bRt2L", "abcd\u00adEFGH1234"):
            for depth in range(3):
                text = head + fragment
                for _ in range(depth):
                    text = json.dumps(text)
                half = fragment.split("\u200b")[0].split("\u00ad")[0]
                with self.subTest(fragment=repr(fragment), depth=depth):
                    self.assertNotIn(half, mask(text))
                with self.subTest(fragment=repr(fragment), depth=depth, row="stored"):
                    with mask_module.stored_row("0123456789abcdef"):
                        self.assertNotIn(half, mask(text))

    def test_a_key_file_with_a_byte_order_mark_is_masked_whole_as_base64(self):
        import base64
        import textwrap
        ec = bytes.fromhex("30310201010420") + bytes(range(32)) + bytes.fromhex("a00a06082a8648ce3d030107")
        pkcs8 = bytes.fromhex("302e020100300506032b657004220420") + bytes(range(32))
        for kind, der in ((b"EC PRIVATE KEY", ec), (b"PRIVATE KEY", pkcs8)):
            pem = (b"\xef\xbb\xbf-----BEGIN " + kind + b"-----\n" + base64.b64encode(der)
                   + b"\n-----END " + kind + b"-----\n")
            encoded = base64.b64encode(pem).decode()
            for width in (64, 76, len(encoded)):
                block = "\n".join(textwrap.wrap(encoded, width))
                for text in (block, "tls.key: " + block, "Here is the key file:\n\n" + block + "\n\nThanks"):
                    with self.subTest(kind=kind, width=width, text=text[:12]):
                        masked = mask(text)
                        self.assertIn("[masked]", masked)
                        for line in block.splitlines():
                            self.assertNotIn(line, masked)
                        self.assertEqual(masked.startswith("tls.key: "), text.startswith("tls.key: "))
                        self.assertEqual(masked.endswith("Thanks"), text.endswith("Thanks"))

    def test_base64_of_a_text_file_with_a_byte_order_mark_that_quotes_a_header_stays(self):
        import base64
        import textwrap
        for label in (b"Note: ", b"The header reads "):
            doc = (b"\xef\xbb\xbf" + label + b"-----BEGIN PRIVATE KEY-----\nRead the documentation before importing.\n")
            encoded = base64.b64encode(doc).decode()
            for width in (40, 64, 76):
                text = "\n".join(textwrap.wrap(encoded, width))
                with self.subTest(label=label, width=width):
                    self.assertEqual(mask(text), text)


class ReviewNineTest(unittest.TestCase):
    """#34's ninth review: many key rests before one long line, as a JSON log of old fragments with a base64
    attachment, each read to that line's end."""

    def test_many_key_rests_before_one_long_line_cost_time_in_proportion(self):
        import base64
        import time
        took = {}
        for n in (100, 400):
            text = json.dumps({"events": [{"key": "[masked]\nMC4CAQAwBQYDK2VwBCIEIAABAgMEBQYHCAkKCwwNDg8QERIT",
                                           "status": "next steps"}] * n,
                               "image": base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * (n * 2)).decode()})
            started = time.process_time()
            self.assertEqual(mask(text), '{"events": [{"key": "[masked]')
            took[n] = time.process_time() - started
        # four times the text
        self.assertLess(took[400], 6 * max(took[100], 0.05), took)


class ReviewTenTest(unittest.TestCase):
    """#34's tenth review: a PGP secret key block whose armour comment holds a letter past ASCII, as GnuPG's
    --comment writes one, written as base64."""

    @staticmethod
    def block(comment):
        return ("-----BEGIN PGP PRIVATE KEY BLOCK-----\nComment: " + comment + "\n\n" + "Ab3d" * 40
                + "\n=Ab3d\n-----END PGP PRIVATE KEY BLOCK-----\n").encode()

    def test_a_pgp_block_with_a_comment_past_ascii_is_masked_whole_as_base64(self):
        import base64
        import textwrap
        for comment in ("fixture caf\u00e9", "\u00fcbung \u2013 test", "\u65e5\u672c"):
            encoded = base64.b64encode(self.block(comment)).decode()
            for width in (64, 76, len(encoded)):
                block = "\n".join(textwrap.wrap(encoded, width))
                for text in (block, "key.asc: " + block, "Exported:\n\n" + block + "\n\nThanks"):
                    with self.subTest(comment=comment, width=width, text=text[:10]):
                        masked = mask(text)
                        for line in block.splitlines():
                            self.assertNotIn(line, masked)
                        self.assertEqual(masked.startswith(("key.asc: ", "Exported:")), text.startswith(("key.asc: ", "Exported:")))
                        self.assertEqual(masked.endswith("Thanks"), text.endswith("Thanks"))

    def test_a_key_still_ends_where_binary_data_after_it_starts(self):
        import base64
        import textwrap
        pem = (b"-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(bytes.fromhex("302e020100300506032b657004220420")
               + bytes(range(32))) + b"\n")  # no footer
        after = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)) * 2
        for width in (64, 76):
            lines = textwrap.wrap(base64.b64encode(pem + after).decode(), width)
            masked = mask("\n".join(lines))
            with self.subTest(width=width):
                self.assertIn(lines[-1], masked)


class ReviewElevenTest(unittest.TestCase):
    """#34's eleventh review: a PGP secret key block whose armour comment holds a control byte, as GnuPG exports one.
    Between a key's header and its footer, whatever the bytes, is the key."""

    @staticmethod
    def block(comment):
        return (b"-----BEGIN PGP PRIVATE KEY BLOCK-----\nComment: " + comment + b"\n\n" + b"Ab3d" * 40
                + b"\n=Ab3d\n-----END PGP PRIVATE KEY BLOCK-----\n")

    def test_a_block_with_any_byte_in_its_comment_is_masked_whole_to_its_footer(self):
        import base64
        import textwrap
        for comment in (b"fixture\x7fcomment", b"fixture\x00", b"tab\tand\x1b[0m", b"\xff\xfe bytes", b"fixture caf\xc3\xa9"):
            encoded = base64.b64encode(self.block(comment)).decode()
            for width in (64, 76, len(encoded)):
                block = "\n".join(textwrap.wrap(encoded, width))
                for text in (block, "key.asc: " + block, "Exported:\n\n" + block + "\n\nThanks"):
                    with self.subTest(comment=comment, width=width, text=text[:10]):
                        masked = mask(text)
                        for line in block.splitlines():
                            self.assertNotIn(line, masked)
                        self.assertEqual(masked.endswith("Thanks"), text.endswith("Thanks"))

    def test_text_after_the_footer_in_the_same_block_stays(self):
        import base64
        import textwrap
        after = b"Notes for the team: rotate this key every quarter and keep the passphrase offline.\n" * 3
        data = self.block(b"fixture\x7fcomment") + after
        encoded = base64.b64encode(data).decode()
        lines = textwrap.wrap(encoded, 76)
        masked = mask("\n".join(lines))
        self.assertIn(lines[-1], masked)


class ReviewTwelveTest(unittest.TestCase):
    """#34's twelfth review: a base64-encoded key split by one invisible character, which JSON writes as an escape (a
    surrogate pair past U+FFFF), in JSON once and twice. The key is masked whole."""

    def test_a_key_split_by_an_escaped_invisible_character_is_masked_whole(self):
        import base64
        import textwrap
        pem = (b"-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(bytes.fromhex("302e020100300506032b657004220420")
               + bytes(range(32))) + b"\n-----END PRIVATE KEY-----\n")
        encoded = base64.b64encode(pem).decode()
        lines = textwrap.wrap(encoded, 76)
        block = "\n".join(lines)
        for invisible in ("\U000e0020", "\U000e0001", "\u200b"):
            for times in (1, 2):
                text = block[:100] + invisible + block[100:]
                for _ in range(times):
                    text = json.dumps(text)
                with self.subTest(invisible=hex(ord(invisible)), times=times):
                    masked = mask(text)
                    for line in lines:
                        for at in range(0, len(line) - 11, 4):
                            self.assertNotIn(line[at:at + 12], masked)


class Base64ReviewThirteenTest(unittest.TestCase):
    """#34's thirteenth review: a base64-encoded sentence past ASCII that quotes a key's header lost the text after it;
    a base64 key wrapped with lone carriage returns, and one in JSON written with escaped tabs, \\u000a line breaks or
    escaped slashes, showed lines. Prose in any UTF-8 reads as prose, a lone carriage return is a line break, and a
    key is read with JSON's escapes read once and twice too."""

    @staticmethod
    def pem(cr=False):
        import base64
        import random
        der = bytes.fromhex("302e020100300506032b657004220420") + bytes(random.Random(13).randrange(256) for _ in range(32))
        text = (b"-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(der) + b"\n-----END PRIVATE KEY-----\n")
        return text.replace(b"\n", b"\r") if cr else text

    @staticmethod
    def pgp():
        return ("-----BEGIN PGP PRIVATE KEY BLOCK-----\nComment: " + "??? " * 32 + "\n\n" + "YWJjMTIz" * 20
                + "\n=abcd\n-----END PGP PRIVATE KEY BLOCK-----\n").encode()

    @staticmethod
    def lines(data, width):
        import base64
        import textwrap
        return textwrap.wrap(base64.b64encode(data).decode(), width)

    def assertNoLineShown(self, lines, masked):
        for line in lines:
            for at in range(0, len(line) - 11, 4):
                piece = line[at:at + 12]
                self.assertNotIn(piece, masked)
                self.assertNotIn(piece.replace("/", "\\/"), masked)

    def test_a_base64_sentence_past_ascii_that_quotes_a_header_is_kept(self):
        import base64
        import textwrap
        for sentence in ("The documentation for café shows this opening delimiter -----BEGIN PRIVATE KEY-----\n"
                         "Read the documentation before importing.\n",
                         "Naïve copies show -----BEGIN RSA PRIVATE KEY-----\nand nothing after it.\n",
                         "この文書は見出し -----BEGIN PRIVATE KEY-----\nを引用します。\n",
                         "Le guide cité montre -----BEGIN OPENSSH PRIVATE KEY-----\npuis la suite du texte.\n"):
            for width in (60, 64, 76):
                # pads 0 to 2, and the one that puts the header's first byte where a wrapped line starts
                aligned = -len(sentence.encode().split(b"-----")[0]) % (width // 4 * 3)
                for pad in sorted({0, 1, 2, aligned}):
                    data = ("x" * pad + sentence).encode()
                    block = "\n".join(textwrap.wrap(base64.b64encode(data).decode(), width))
                    for text in (block, json.dumps(block)):
                        with self.subTest(sentence=sentence[:20], pad=pad, width=width, json=text is not block):
                            self.assertEqual(mask(text), text)

    def test_a_key_wrapped_with_lone_carriage_returns_is_masked_whole(self):
        for width in (64, 76):
            lines = self.lines(self.pem(), width)
            for block in ("\r".join(lines), "\r".join(lines) + "\rnext steps", "notes\r" + "\r".join(lines) + "\r"):
                for times in range(3):
                    text = block
                    for _ in range(times):
                        text = json.dumps(text)
                    with self.subTest(width=width, block=block[:6], times=times):
                        self.assertNoLineShown(lines, mask(text))

    def test_a_key_whose_own_lines_end_in_carriage_returns_is_masked_whole(self):
        for width in (64, 76):
            lines = self.lines(self.pem(cr=True), width)
            for brk in ("\n", "\r", "\r\n"):
                for times in range(3):
                    text = brk.join(lines)
                    for _ in range(times):
                        text = json.dumps(text)
                    with self.subTest(width=width, brk=repr(brk), times=times):
                        self.assertNoLineShown(lines, mask(text))

    def test_a_key_in_json_with_escaped_tabs_line_breaks_and_slashes_is_masked_whole(self):
        for data in (self.pem(), self.pgp()):
            for width in (64, 76):
                lines = self.lines(data, width)
                block = "\n".join(lines)
                once = {
                    "tabs": json.dumps("\t" + "\n\t".join(lines)),
                    "unicode line feed": json.dumps(block).replace("\\n", "\\u000a"),
                    "unicode carriage return and line feed": json.dumps(block).replace("\\n", "\\u000d\\u000a"),
                    "escaped slashes": json.dumps(block).replace("/", "\\/"),
                    "tabs and escaped slashes": json.dumps("\t" + "\n\t".join(lines)).replace("/", "\\/"),
                }
                for name, text in once.items():
                    self.assertEqual(json.loads(text).replace("\t", "").replace("\r\n", "\n"), block)
                    for times in (1, 2):
                        with self.subTest(data=data[11:20], width=width, form=name, times=times):
                            self.assertNoLineShown(lines, mask(text if times == 1 else json.dumps(text)))

    def test_a_key_escaped_three_and_four_times_with_an_invisible_character_is_masked_whole(self):
        # the views read JSON's escapes twice, and the readings as written take a line break and an invisible
        # character at any depth, so a key escaped three and four times reads whole too
        for width in (64, 76):
            lines = self.lines(self.pem(), width)
            block = "\n".join(lines)
            for at in (width // 2, width + 3, 2 * width + 40, len(block) - 20):
                for invisible in ("\u200b", "\U000e0020", "\u2060", "\ufeff"):
                    for times in (3, 4):
                        text = block[:at] + invisible + block[at:]
                        for _ in range(times):
                            text = json.dumps(text)
                        with self.subTest(width=width, at=at, invisible=hex(ord(invisible)), times=times):
                            self.assertNoLineShown(lines, mask(text))

    def test_ordinary_json_with_escapes_is_kept(self):
        for value in ("C:\\Users\\build\\notes\\README.txt", "line one\n\tindented two\r\nthree", "a/b/c and d\\/e",
                      "café\u2028naïve \u00e9t\u00e9",
                      "QUJDREVGR0hJSktMTU5PUA==\n\tQUJDREVGR0hJSktMTU5PUA=="):
            for text in (json.dumps(value), json.dumps(value).replace("/", "\\/"), json.dumps(json.dumps(value))):
                with self.subTest(text=text[:30]):
                    self.assertEqual(mask(text), text)

    def test_carriage_returns_and_escapes_cost_time_in_proportion(self):
        import time

        def took(text):
            started = time.process_time()
            mask(text)
            return time.process_time() - started
        for shape in (lambda n: "QUJDREVGR0hJSktMTU5P" + "\r" * n + "x", lambda n: ("QUJD\r" * n) + "x",
                      lambda n: "QUJDREVGR0hJSktMTU5P" + "\\r" * n + "x", lambda n: "\\" * n + "QUJD",
                      lambda n: "QUJD\\/" * n, lambda n: "QUJD\\t\\n" * n):
            small, large = shape(20000), shape(40000)
            with self.subTest(shape=small[:12]):  # twice the text, at most a little over twice the time
                self.assertLess(took(large), 3.5 * took(small) + 0.1)


class Base64ReviewFourteenTest(unittest.TestCase):
    """#34's fourteenth review: a base64-encoded sentence in a language written without spaces that quotes a key's
    header where a wrapped line starts lost the header and the text after it, as only words with a space read as
    prose; and a base64 key before a PKCS#7 certificate bundle kept the bundle once, but the next mask, as kb's store
    makes, read the key's placeholder as an older kb's and removed the bundle. Words past ASCII read as prose too, a
    certificate bundle is public material, and masked text masks to itself."""

    SENTENCES = ("この文書では秘密鍵の開始マーカーを説明-----BEGIN PRIVATE KEY-----\nRead the documentation before importing.\n",
                 "本文档说明私钥的开始标记-----BEGIN RSA PRIVATE KEY-----\n导入前请阅读文档。\n",
                 "เอกสารนี้อธิบายเครื่องหมายเริ่มต้น-----BEGIN OPENSSH PRIVATE KEY-----\nโปรดอ่านก่อนนำเข้า\n",
                 "注意：-----BEGIN PRIVATE KEY-----\nの行は引用です。\n")

    @staticmethod
    def bundle(r):
        """A PKCS#7 certificate bundle as openssl crl2pkcs7 -nocrl writes one: signed data with certificates, and no
        content, digest algorithm or signer."""
        signed = _der(0x30, _der(0x02, b"\x01"), _der(0x31), _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010701"))),
                      _der(0xa0, made_up_certificate(r), made_up_certificate(r)), _der(0x31))
        return _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010702")), _der(0xa0, signed))

    @staticmethod
    def key64():
        import base64
        return base64.b64encode(Base64ReviewThirteenTest.pem()).decode()

    def test_a_base64_sentence_without_spaces_that_quotes_a_header_is_kept(self):
        import base64
        import textwrap
        for sentence in self.SENTENCES:
            for width in (60, 64, 76):
                aligned = -len(sentence.encode().split(b"-----")[0]) % (width // 4 * 3)
                for pad in sorted({0, 1, 2, aligned}):
                    block = "\n".join(textwrap.wrap(base64.b64encode(("x" * pad + sentence).encode()).decode(), width))
                    for text in (block, json.dumps(block)):
                        with self.subTest(sentence=sentence[:8], pad=pad, width=width, json=text is not block):
                            self.assertEqual(mask(text), text)

    def test_a_key_after_such_a_sentence_is_still_masked_whole(self):
        import base64
        import textwrap
        pem = Base64ReviewThirteenTest.pem()
        for sentence in self.SENTENCES:
            head = sentence.split("-----")[0].encode() + b"\n"
            for width in (64, 76):
                data = head + pem
                lines = textwrap.wrap(base64.b64encode(data).decode(), width)
                masked = mask("\n".join(lines))
                for n, line in enumerate(lines):
                    first = n * width // 4 * 3
                    if first + len(base64.b64decode(line)) > len(head):  # a line holding the key's bytes
                        with self.subTest(sentence=sentence[:8], width=width, line=n):
                            self.assertNotIn(line, masked)

    def test_a_base64_key_before_a_certificate_bundle_keeps_the_bundle_and_masks_to_itself(self):
        import base64
        import random
        import textwrap
        bundle = base64.b64encode(self.bundle(random.Random(14))).decode()
        key = self.key64()
        for k in (key, "\n".join(textwrap.wrap(key, 76))):
            for after in (bundle, "\n".join(textwrap.wrap(bundle, 64)), "\n".join(textwrap.wrap(bundle, 76))):
                with self.subTest(wrapped="\n" in k, after=after.count("\n")):
                    once = mask(f"{k}\n{after}\nnext steps")
                    self.assertEqual(once, f"[masked]\n{after}\nnext steps")
                    self.assertEqual(mask(once), once)

    def test_a_certificate_bundle_after_a_placeholder_is_kept(self):
        import base64
        import random
        import textwrap
        from kb import mask as mask_module
        bundle = base64.b64encode(self.bundle(random.Random(15))).decode()
        for after in (bundle, "\n".join(textwrap.wrap(bundle, 64))):
            text = f"[masked]\n{after}\nnext steps"
            for older in (False, True):
                with self.subTest(lines=after.count("\n"), older=older):
                    if older:
                        with mask_module.stored_row("0123456789abcdef"):
                            self.assertEqual(mask(text), text)
                    else:
                        self.assertEqual(mask(text), text)
        # a bundle cut short, or signed data that holds content, is no bundle: it stays masked after a placeholder
        der = self.bundle(random.Random(16))
        for name, data in (("cut short", der[:len(der) * 2 // 3]),
                           ("with content", der.replace(bytes.fromhex("300b06092a864886f70d010701"),
                                                        bytes.fromhex("301206092a864886f70d010701a00504035a71"), 1))):
            with self.subTest(neither=name):
                self.assertEqual(mask(f"[masked]\n{base64.b64encode(data).decode()}\nnext steps"), "[masked]\nnext steps")

    def test_a_bundle_inside_a_bundle_is_no_bundle_at_any_depth(self):
        # openssl writes no bundle inside a bundle; read as one, a bundle nested deep would recurse past Python's limit
        import base64
        import random

        def holding(member):
            signed = _der(0x30, _der(0x02, b"\x01"), _der(0x31),
                          _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010701"))), _der(0xa0, member), _der(0x31))
            return _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010702")), _der(0xa0, signed))
        der = made_up_certificate(random.Random(18))
        for depth in range(1, 1501):
            der = holding(der)
            if depth in (2, 3, 1500):
                with self.subTest(depth=depth):
                    encoded = base64.b64encode(der).decode()
                    # a body with no "=" can run on: its next first token goes with it (coordinator's ruling, Nov 6)
                    self.assertEqual(mask(f"[masked]\n{encoded}\nnext steps"),
                                     "[masked]\nnext steps" if encoded.endswith("=") else "[masked] steps")

    def test_such_a_row_is_stored_with_its_bundle(self):
        import base64
        import os
        import random
        import tempfile
        from kb import store
        bundle = base64.b64encode(self.bundle(random.Random(17))).decode()
        with tempfile.TemporaryDirectory() as tmp:
            db = store.connect(os.path.join(tmp, "kb.sqlite"))
            try:
                store.store_item(db, "notes", "r14", "2026-11-04T10:00:00Z", "tls", f"{self.key64()}\n{bundle}\nnext steps", {})
                kept = "\n".join(row[0] for row in db.execute("SELECT content FROM docs"))
                self.assertIn(bundle, kept)
                self.assertNotIn(self.key64()[:40], kept)
            finally:
                db.close()

    def test_masked_text_with_a_key_masks_to_itself(self):
        import base64
        import random
        import textwrap
        r = random.Random(1414)
        key = self.key64()
        follows = (lambda: base64.b64encode(made_up_certificate(r)).decode(),
                   lambda: base64.b64encode(self.bundle(r)).decode(),
                   lambda: base64.b64encode(made_up_public_key(r)).decode(),
                   lambda: base64.b64encode(made_up_private_key(r)).decode(),
                   lambda: base64.b64encode(bytes(r.randrange(256) for _ in range(120))).decode(),
                   lambda: "MIIE" + base64.b64encode(bytes(r.randrange(256) for _ in range(90))).decode(),
                   lambda: "next steps: deploy it", lambda: "password=Zq7vRt2LmX9k",
                   lambda: "ghp_Ab3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6", lambda: key)
        for n in range(300):
            k = key if r.random() < 0.5 else "\n".join(textwrap.wrap(key, r.choice((64, 76))))
            after = r.choice(follows)()
            if r.random() < 0.5:
                after = "\n".join(textwrap.wrap(after, r.choice((64, 76))))
            text = r.choice(("", "notes\n")) + k + r.choice(("\n", "\n\n")) + after + r.choice(("", "\nnext steps"))
            once = mask(text)
            with self.subTest(n=n, text=text[:30]):
                self.assertEqual(mask(once), once)
                self.assertNotIn(key[:40], once)


def made_up_crl(r, version):
    """A list of revoked certificates (CertificateList) with made-up numbers: version 1 starts with how it is signed,
    as openssl ca -gencrl writes one with no extensions; version 2 with its version and a CRL number."""
    signed_with = _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d01010b")), _der(0x05))
    name = _der(0x30, _der(0x31, _der(0x30, _der(0x06, bytes.fromhex("550403")), _der(0x0c, b"walrus.example"))))
    revoked = _der(0x30, _der(0x30, _der(0x02, bytes(r.randrange(1, 128) for _ in range(8))), _der(0x17, b"261101000000Z")))
    fields = [signed_with, name, _der(0x17, b"261101000000Z"), _der(0x17, b"261102000000Z"), revoked]
    if version == 2:
        number = _der(0x30, _der(0x06, bytes.fromhex("551d14")), _der(0x04, _der(0x02, b"\x01")))
        fields = [_der(0x02, b"\x01")] + fields + [_der(0xa0, _der(0x30, number))]
    return _der(0x30, _der(0x30, *fields), signed_with, _der(0x03, b"\0" + bytes(r.randrange(256) for _ in range(256))))


class Base64ReviewFifteenTest(unittest.TestCase):
    """#34's fifteenth review: a short label before a quoted header ("例："), where a narrow wrapping only happened to
    break before the header, read as a key's start and lost the header and the text after it; a key's placeholder,
    then a whole certificate and a line of words with a number or an acronym in it ("port 8080", "HTTP"), lost the
    certificate and the words, read as the rest of a key through that line; and a certificate bundle holding a list of
    revoked certificates in version 1, which starts with how it is signed, was no public material and was lost."""

    BODIES = ("これは説明です\n", "see the docs before importing.\n", "")
    LABELS = ("例：", "注：", "Ex:", "例", "→", "key=", "Note:")

    def test_a_label_before_a_header_where_the_wrapping_only_happens_to_break_is_kept(self):
        import base64
        import textwrap
        for label in self.LABELS:
            for width in (8, 12, 16, 60, 64, 76):
                group = width // 4 * 3
                for pad in sorted({0, -len(label.encode()) % group, -len(label.encode()) % 3 + group}):
                    for body in self.BODIES:
                        data = ("x" * pad + label + "-----BEGIN PRIVATE KEY-----\n" + body).encode()
                        block = "\n".join(textwrap.wrap(base64.b64encode(data).decode(), width))
                        for text in (block, json.dumps(block)):
                            with self.subTest(label=label, width=width, pad=pad, body=body[:4], json=text is not block):
                                self.assertEqual(mask(text), text)

    def test_a_key_in_its_own_stream_under_a_heading_in_another_is_still_masked(self):
        # Two streams, each wrapped at its own width: the heading's, glued to the key's as its bytes come out whole
        # groups, ends shorter than the key's lines, so the key starts a stream there, with its footer or cut before it.
        import base64
        import random
        import textwrap
        der = made_up_private_key(random.Random(1515))
        body = textwrap.wrap(base64.b64encode(der).decode(), 64)
        for heading in ("例：", "KEY:", "secret"):
            for footer in (True, False):
                pem = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(body) + "\n" + ("-----END PRIVATE KEY-----\n" if footer else "")
                for width in (64, 76):
                    key = textwrap.wrap(base64.b64encode(pem.encode()).decode(), width)
                    text = base64.b64encode(heading.encode()).decode() + "\n" + "\n".join(key) + "\nnext steps"
                    masked = mask(text)
                    with self.subTest(heading=heading, footer=footer, width=width):
                        for line in key[1:-1]:  # every line that holds only the key's bytes
                            self.assertNotIn(line, masked)
                        self.assertTrue(masked.endswith("\nnext steps"))

    @staticmethod
    def certificate(r):
        import base64
        return base64.b64encode(made_up_certificate(r)).decode()

    def test_a_certificate_and_a_line_of_words_after_a_key_are_kept_and_mask_to_themselves(self):
        import base64
        import random
        import textwrap
        r = random.Random(1515)
        key = Base64ReviewFourteenTest.key64()
        publics = (self.certificate(r), base64.b64encode(Base64ReviewFourteenTest.bundle(r)).decode(),
                   base64.b64encode(made_up_public_key(r)).decode())
        for public in publics:
            for after in (public, "\n".join(textwrap.wrap(public, 64)), "\n".join(textwrap.wrap(public, 76))):
                for words in ("deploy on port 8080", "use HTTP for health checks", "build 20261104 shipped",
                              "see RFC5280 and X509 notes", "next steps"):
                    for k in (key, "\n".join(textwrap.wrap(key, 76))):
                        with self.subTest(public=public[:6], lines=after.count("\n"), words=words, wrapped="\n" in k):
                            once = mask(f"{k}\n{after}\n{words}")
                            self.assertEqual(once, f"[masked]\n{after}\n{words}")
                            self.assertEqual(mask(once), once)

    def test_a_chain_of_certificates_and_a_line_of_words_after_a_key_are_kept(self):
        import base64
        import random
        import textwrap
        r = random.Random(1519)
        key = Base64ReviewFourteenTest.key64()
        chain = made_up_certificate(r) + made_up_certificate(r)
        # one stream, or one per element, the first ending in base64's padding (a bundle, 2 bytes past whole groups)
        streams = ([base64.b64encode(chain).decode()],
                   [base64.b64encode(Base64ReviewFourteenTest.bundle(r)).decode(), self.certificate(r)])
        for parts in streams:
            for after in ("\n".join(parts), "\n".join(line for part in parts for line in textwrap.wrap(part, 64))):
                for words in ("deploy on port 8080", "next steps"):
                    with self.subTest(lines=after.count("\n"), words=words, streams=len(parts)):
                        once = mask(f"{key}\n{after}\n{words}")
                        self.assertEqual(once, f"[masked]\n{after}\n{words}")
                        self.assertEqual(mask(once), once)

    def test_after_a_placeholder_a_certificate_then_a_private_keys_lines_stay_masked(self):
        import base64
        import random
        import textwrap
        r = random.Random(1516)
        private = textwrap.wrap(base64.b64encode(made_up_private_key(r)).decode(), 64)
        certificate = textwrap.wrap(self.certificate(r), 64)
        for words in ("deploy on port 8080", "next steps"):
            text = "[masked]\n" + "\n".join(certificate + private) + "\n" + words
            masked = mask(text)
            with self.subTest(words=words):
                for line in private[:-1]:
                    self.assertNotIn(line, masked)

    def test_after_public_material_and_words_a_run_of_base64_stays_masked(self):
        # A security review of 3581a21: public material ends the reading of a key's rest only where nothing past it,
        # through all main's reading would hide, holds a run of base64 of both cases, as a key's fragment does.
        import base64
        import random
        import textwrap
        r = random.Random(1520)
        key = Base64ReviewFourteenTest.key64()
        fragment = base64.b64encode(made_up_private_key(r)).decode()[100:148]
        for public in (self.certificate(r), "\n".join(textwrap.wrap(self.certificate(r), 64))):
            for after in (f"see also\n{fragment}\nmore", f"deploy on port 8080\n{fragment[:20]}", f"notes {fragment[:24]} end"):
                text = f"{key}\n{public}\n{after}"
                with self.subTest(after=after[:12], lines=public.count("\n")):
                    once = mask(text)
                    self.assertNotIn(fragment[:16], once)
                    self.assertEqual(mask(once), once)
                    self.assertNotIn(fragment[:16], mask(f"[masked]\n{public}\n{after}"))

    def test_a_key_in_its_own_stream_after_text_that_fills_whole_lines_is_masked_by_its_footer(self):
        # A security review of 3581a21: text encoded alone that ends exactly at the wrapping's width, then a key encoded
        # alone at that width, reads as one stream wrapped there; the key's header starts no key, and its footer does.
        import base64
        import random
        import textwrap
        der = made_up_private_key(random.Random(1521))
        pem = ("-----BEGIN PRIVATE KEY-----\n" + "\n".join(textwrap.wrap(base64.b64encode(der).decode(), 64))
               + "\n-----END PRIVATE KEY-----\n").encode()
        for width in (64, 76):
            text_bytes = ("notes" * 40)[:width // 4 * 3].encode()  # exactly one full line, no padding
            heading = base64.b64encode(text_bytes).decode()
            key = textwrap.wrap(base64.b64encode(pem).decode(), width)
            masked = mask(heading + "\n" + "\n".join(key) + "\nnext steps")
            with self.subTest(width=width):
                for line in key[1:-1]:
                    self.assertNotIn(line, masked)
                self.assertTrue(masked.endswith("\nnext steps"))

    def test_a_bundle_or_a_list_of_revoked_certificates_in_either_version_is_kept(self):
        import base64
        import random
        import textwrap
        r = random.Random(1517)
        key = Base64ReviewFourteenTest.key64()
        for version in (1, 2):
            crl = made_up_crl(r, version)
            signed = _der(0x30, _der(0x02, b"\x01"), _der(0x31),
                          _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010701"))),
                          _der(0xa0, made_up_certificate(r)), _der(0xa1, crl), _der(0x31))
            bundle = _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010702")), _der(0xa0, signed))
            for public in (base64.b64encode(bundle).decode(), base64.b64encode(crl).decode()):
                for after in (public, "\n".join(textwrap.wrap(public, 64))):
                    with self.subTest(version=version, bundle=len(public) > 800, lines=after.count("\n")):
                        once = mask(f"{key}\n{after}\nnext steps")
                        self.assertEqual(once, f"[masked]\n{after}\nnext steps")
                        self.assertEqual(mask(once), once)
                        self.assertEqual(mask(f"[masked]\n{after}\nnext steps"), f"[masked]\n{after}\nnext steps")

    @staticmethod
    def probed_labels():
        """The labels probed before a quoted header (the coordinator's ruling on 5accfea): words with a mark or none,
        marks in other scripts, and 400 made-up words."""
        import random
        r = random.Random(5)
        words = ["Ex", "Note", "key", "ABC", "DCS", "see", "eg", "Tip", "NB", "warn", "INFO", "cfg", "x", "Q", "id",
                 "pem", "KEY", "Doc"]
        labels = [w + s for w in words for s in ("", ":", "=", "-", ">")] + ["例：", "注：", "例", "→", "※"]
        letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        return labels + ["".join(r.choice(letters) for _ in range(r.randrange(1, 9))) + r.choice(["", ":", "="])
                         for _ in range(400)]

    @classmethod
    def label_cases(cls, after):
        """Each probed label, base64 wrapped at each width with the header where a wrapped line starts (a pad of x's
        between them puts it there), and the label's own lines."""
        import base64
        import textwrap
        for label in cls.probed_labels():
            for width in (4, 8, 12, 16, 20, 60, 64, 76):
                group = width // 4 * 3
                for pad in range(group):
                    lead = (label + "x" * pad).encode()
                    if len(lead) % group:
                        continue
                    lines = textwrap.wrap(base64.b64encode(lead + b"-----BEGIN PRIVATE KEY-----\n" + after).decode(),
                                          width)
                    yield label, width, pad, lines[:len(lead) // group], lines

    def test_a_label_whose_base64_is_one_word_reads_as_a_heading_and_a_key_after_it_stays_masked(self):
        """The limit the coordinator granted: base64 of a label glued to a quoted header, where the label's own base64
        is one word in one case as long as the block's lines ("ABC" is "QUJD" at width 4), is byte for byte a heading
        over a key encoded alone, and reads as one. Under either reading a key there stays masked."""
        import base64
        import re
        import textwrap
        body = base64.b64encode(bytes(range(256)) * 2).decode()
        key = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(textwrap.wrap(body, 64)) + "\n-----END PRIVATE KEY-----\n"
        after = key[len("-----BEGIN PRIVATE KEY-----\n"):].encode()
        quoted = lost = 0
        for label, width, pad, heading, lines in self.label_cases(after):
            with self.subTest(label=label, width=width, pad=pad):
                text = "\n".join(lines) + "\nnext steps"
                if heading:  # the same string read as a heading over the key encoded alone
                    alone = "\n".join(textwrap.wrap(base64.b64encode(key.encode()).decode(), width))
                    self.assertEqual(text, "\n".join(heading) + "\n" + alone + "\nnext steps")
                kept = [line for line in mask(text).split("\n") if line != "[masked]"]
                self.assertTrue(set(kept) <= set(heading) | {"next steps"}, kept[:4])
        for quote in ("これは説明です\n", "see the docs before importing.\n"):  # the probe's two quotes
            for label, width, pad, heading, lines in self.label_cases(quote.encode()):
                text = "\n".join(lines)
                quoted += 1
                if mask(text) != text:
                    lost += 1
                    with self.subTest(label=label, width=width, pad=pad):  # only where the label's lines are words
                        self.assertEqual(width, 4)
                        self.assertTrue(all(re.fullmatch(r"[a-z]+|[A-Z][a-z]*|[A-Z]+", line) for line in heading))
        self.assertEqual((quoted, lost), (7920, 8))  # the limit's measure: 8 of 7,920, all at width 4

    def test_many_keys_each_before_a_certificate_and_words_in_one_paragraph_cost_time_in_proportion(self):
        import base64
        import random
        import textwrap
        import time
        from kb import mask as mask_module
        r = random.Random(1516)
        keys = [base64.b64encode(made_up_private_key(r)).decode() for _ in range(200)]
        certificates = [self.certificate(r) for _ in range(200)]
        for width in (None, 64, 12):
            def wrapped(text):
                return "\n".join(textwrap.wrap(text, width)) if width else text

            for older in (False, True):
                took = {}
                for n in (50, 200):
                    if older:  # an older kb's row: its keys already placeholders
                        text = "".join(f"[masked]\n{wrapped(c)}\ndeploy on port 8080\n" for c in certificates[:n])
                    else:
                        text = "".join("-----BEGIN PRIVATE KEY-----\n" + "\n".join(textwrap.wrap(k, 64))
                                       + f"\n-----END PRIVATE KEY-----\n{wrapped(c)}\ndeploy on port 8080\n"
                                       for k, c in zip(keys[:n], certificates[:n]))
                    started = time.process_time()
                    if older:
                        with mask_module.stored_row("0123456789abcdef"):
                            out = mask(text)
                    else:
                        out = mask(text)
                    took[n] = time.process_time() - started
                    with self.subTest(width=width, older=older, n=n):
                        self.assertFalse([k for k in keys[:n] if k[64:128] in out])
                with self.subTest(width=width, older=older):
                    # four times the text
                    self.assertLess(took[200], 6 * max(took[50], 0.05), took)

    def test_such_rows_are_stored_whole(self):
        import base64
        import os
        import random
        import tempfile
        from kb import store
        r = random.Random(1518)
        key = Base64ReviewFourteenTest.key64()
        texts = (f"{key}\n{self.certificate(r)}\ndeploy on port 8080",
                 f"{key}\n{base64.b64encode(made_up_crl(r, 1)).decode()}\nnext steps",
                 base64.b64encode("例：-----BEGIN PRIVATE KEY-----\nこれは説明です\n".encode()).decode())
        with tempfile.TemporaryDirectory() as tmp:
            db = store.connect(os.path.join(tmp, "kb.sqlite"))
            try:
                for n, text in enumerate(texts):
                    store.store_item(db, "notes", f"r15-{n}", "2026-11-04T10:00:00Z", "tls", text, {})
                for n, text in enumerate(texts):
                    kept = "".join(c for (c,) in db.execute(
                        "SELECT content FROM docs WHERE source_id = ? ORDER BY id", (f"r15-{n}",)))
                    self.assertEqual(kept, text.replace(key, "[masked]"))
            finally:
                db.close()



class Base64ReviewSixteenTest(unittest.TestCase):
    """#34's sixteenth review: a key whose header a block's wrapping refused (another stream fills the line above) was
    read from its footer up while its lines decoded to text, so a control character in its armour comment left its
    header and the lines below shown; a certificate after a key, then a word alone on its line ("notes") or another
    certificate past a line of words, was lost with the key, the word read as more base64 or the certificate as a
    key's fragment; and a placeholder before each of many certificates in one paragraph read the rest of the paragraph
    each time, in time in the square of its length."""

    @staticmethod
    def pgp(r, comment):
        import base64
        import textwrap
        body = "\n".join(textwrap.wrap(base64.b64encode(bytes(r.randrange(256) for _ in range(300))).decode(), 64))
        return (f"-----BEGIN PGP PRIVATE KEY BLOCK-----\nComment: {comment}\n\n{body}\n=Zq7v\n"
                "-----END PGP PRIVATE KEY BLOCK-----\n").encode()

    def test_a_key_whose_header_the_wrapping_refused_is_masked_from_its_header_to_its_footer_whatever_its_bytes(self):
        import base64
        import random
        import textwrap
        r = random.Random(1601)
        for comment in ("review\x01fixture", "build \x1b[0m notes", "tab\x7fdel", "plain words"):
            pem = self.pgp(r, comment)
            for width in (64, 76):
                for lead in (b"x" * (width // 4 * 3), ("notes " * 40).encode()[:width // 4 * 6]):  # whole lines
                    before = textwrap.wrap(base64.b64encode(lead).decode(), width)
                    key = textwrap.wrap(base64.b64encode(pem).decode(), width)
                    text = "\n".join(before + key) + "\nnext steps"
                    for depth, written in enumerate((text, json.dumps(text), json.dumps(json.dumps(text)))):
                        masked = mask(written)
                        with self.subTest(comment=comment, width=width, lead=len(lead), depth=depth):
                            self.assertEqual([line for line in key[:-1] if line in masked], [])
                            self.assertEqual([line for line in before if line not in masked], [])  # the other stream
                            self.assertIn("next steps", masked)

    @staticmethod
    def certificate(r, width=None, der=None):
        import base64
        import textwrap
        text = base64.b64encode(der or made_up_certificate(r)).decode()
        return "\n".join(textwrap.wrap(text, width)) if width else text

    def test_a_certificate_then_a_word_alone_or_another_certificate_past_words_after_a_key_is_kept(self):
        import random
        r = random.Random(1602)
        key = Base64ReviewFourteenTest.key64()
        # a certificate (702 bytes: no padding), a request (610: "=="), a list of revoked ones (383: "=")
        for kind, der in (("certificate", None), ("request", made_up_certificate(r, request=True)),
                          ("list", made_up_crl(r, 1))):
            for width in (None, 64):
                first, second = self.certificate(r, width, der), self.certificate(r, width)
                for after in ("notes", "next", "TODO", "Staging", f"next steps\n{second}", f"notes\n{second}",
                              f"next steps\n{second}\nnotes", f"see\n{second}\nnext steps"):
                    with self.subTest(kind=kind, width=width, after=after[:12]):
                        once = mask(f"{key}\n{first}\n{after}")
                        self.assertEqual(once, f"[masked]\n{first}\n{after}")
                        self.assertEqual(mask(once), once)
                        self.assertEqual(mask(f"[masked]\n{first}\n{after}"), f"[masked]\n{first}\n{after}")

    def test_past_a_certificate_a_line_that_could_be_a_keys_stays_masked(self):
        # where the word past the material could be a key's fragment (_token_like), it is read with the key, as before
        import base64
        import random
        r = random.Random(1603)
        key = Base64ReviewFourteenTest.key64()
        fragment = base64.b64encode(made_up_private_key(r)).decode()[200:264]
        for width in (None, 64):
            first, second = self.certificate(r, width), self.certificate(r, width)
            for after, hidden in (("Bcsv", "Bcsv"), ("x9Kq", "x9Kq"), (f"notes\n{fragment}", fragment[:16]),
                                  (f"next steps\n{second}\n{fragment}", fragment[:16]),
                                  (f"next steps\n{fragment}\n{second}", fragment[:16])):
                with self.subTest(width=width, after=after[:12]):
                    self.assertNotIn(hidden, mask(f"{key}\n{first}\n{after}"))
                    self.assertNotIn(hidden, mask(f"[masked]\n{first}\n{after}"))

    def test_many_placeholders_each_before_a_certificate_in_one_paragraph_cost_time_in_proportion(self):
        import base64
        import random
        import textwrap
        import time
        from kb import mask as mask_module
        r = random.Random(1604)
        certificate = "\n".join(textwrap.wrap(base64.b64encode(made_up_certificate(r)).decode(), 12))
        key = base64.b64encode(Base64ReviewThirteenTest.pem()).decode()
        for lead, older in (("[masked]", True), ("[masked]\n", True), (key, False)):
            took = {}
            for n in (100, 400):
                text = (lead + certificate + "\ndeploy on port 8080\n") * n
                started = time.process_time()
                if older:
                    with mask_module.stored_row("0123456789abcdef"):
                        out = mask(text)
                else:
                    out = mask(text)
                took[n] = time.process_time() - started
                with self.subTest(lead=lead[:9], n=n):
                    self.assertNotIn(key[64:128], out)
            with self.subTest(lead=lead[:9]):
                self.assertLess(took[400], 6 * max(took[100], 0.05), took)  # four times the text

    def test_a_certificate_glued_to_a_placeholder_past_public_material_after_a_key_is_kept_whole(self):
        # The run glued to the placeholder is the certificate's first line. Masked alone, it left the lines after it
        # to read as a key's fragment, which took the public material before them with it.
        import random
        from kb import mask as mask_module
        r = random.Random(1605)
        key = Base64ReviewFourteenTest.key64()
        request = self.certificate(r, None, made_up_certificate(r, request=True))
        for width in (None, 12, 64, 76):
            certificate = self.certificate(r, width)
            for lead in (f"{key}\n{request}\n", f"[masked]\n{request}\n", ""):
                text = f"{lead}[masked]{certificate}\nnext steps"
                kept = text.replace(key, "[masked]")
                for depth, written in enumerate((text, json.dumps(text), json.dumps(json.dumps(text)))):
                    with self.subTest(width=width, lead=len(lead), depth=depth):
                        masked = mask(written)
                        self.assertEqual([line for line in kept.split("\n") if line not in masked], [])
                        self.assertNotIn(key[64:128], masked)
                with self.subTest(width=width, lead=len(lead), older=True):
                    with mask_module.stored_row("0123456789abcdef"):
                        self.assertEqual(mask(text), kept)
                with self.subTest(width=width, lead=len(lead)):
                    self.assertEqual(mask(text), kept)
                    self.assertEqual(mask(kept), kept)

    def test_many_placeholders_each_before_a_certificate_with_nothing_between_cost_time_in_proportion(self):
        # A certificate kept as public material left its stretch unread, so each placeholder read on to the end of
        # the paragraph.
        import random
        import time
        from kb import mask as mask_module
        r = random.Random(1606)
        for width in (12, 64):
            certificate = self.certificate(r, width)
            for lead, older in (("[masked]", False), ("[masked]\n", False), ("[masked]", True)):
                took = {}
                for n in (100, 400):
                    text = (lead + certificate + "\n") * n
                    started = time.process_time()
                    if older:
                        with mask_module.stored_row("0123456789abcdef"):
                            mask(text)
                    else:
                        mask(text)
                    took[n] = time.process_time() - started
                with self.subTest(width=width, lead=lead, older=older):
                    self.assertLess(took[400], 6 * max(took[100], 0.05), took)  # four times the text

    def test_a_line_of_another_stream_that_decodes_to_words_after_a_placeholder_starts_no_keys_rest(self):
        # A key, a line of another stream encoded alone that decodes to words, a second key, then a certificate: the
        # line stays, as the sixteenth review asked, and in an older kb's row it came right after the first key's
        # placeholder, so it read as the start of that key's rest, which took the certificate past the second key.
        import base64
        import random
        import textwrap
        from kb import mask as mask_module
        r = random.Random(1607)
        for width in (64, 76):
            first = textwrap.wrap(base64.b64encode(Base64ReviewThirteenTest.pem()).decode(), width)
            second = textwrap.wrap(base64.b64encode(self.pgp(r, "plain words")).decode(), width)
            certificate = self.certificate(r, width)
            for words in (b"notes " * 20, b"deploy the service, then check the logs. " * 3):
                other = base64.b64encode(words[:width // 4 * 3]).decode()  # fills its line, as one stream does
                keys = [line for line in first + second if len(line) >= 16]
                for text in ("\n".join(first + [other] + second) + f"\n{certificate}\nnext steps",
                             "\n".join(first) + f"\n{other}\n{certificate}\nnext steps",
                             f"[masked]\n{other}\n{certificate}\nnext steps",
                             f"[masked]\n{other}\n[masked]\n{certificate}\nnext steps"):
                    kept = [other] + certificate.split("\n") + ["next steps"]
                    for older in (False, True):
                        for depth, written in enumerate((text, json.dumps(text), json.dumps(json.dumps(text)))):
                            with self.subTest(width=width, words=words[:6], text=text[:9], older=older, depth=depth):
                                if older:
                                    with mask_module.stored_row("0123456789abcdef"):
                                        masked = mask(written)
                                        again = mask(masked)
                                else:
                                    masked = mask(written)
                                    again = mask(masked)
                                self.assertEqual([line for line in kept if line not in masked], [])
                                self.assertEqual([line for line in keys if line in masked], [])
                                self.assertEqual(again, masked)

    def test_an_encoded_keys_lines_after_a_placeholder_still_read_as_its_rest(self):
        # Guard: a line of an encoded key decodes to its PEM text, base64 runs and a header or footer, no words. Its
        # last line can hold the footer alone, no key's bytes. Past lines that decode to words, the next line is read
        # as a rest's start, so a key's lines there, with no footer to mark them, are still its rest, and the words
        # stay.
        import base64
        import random
        import textwrap
        from kb import mask as mask_module
        r = random.Random(1608)
        body = "\n".join(textwrap.wrap(base64.b64encode(made_up_private_key(r)).decode(), 64))
        pem = f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----\n".encode()
        for width in (64, 76):
            lines = textwrap.wrap(base64.b64encode(pem).decode(), width)
            words = base64.b64encode((b"notes " * 40)[:width // 4 * 3]).decode()
            for cut in (1, 2, len(lines) // 2):
                for before, piece in (([], lines[cut:]), ([words], lines[cut:-3]), ([words, words], lines[cut:-3])):
                    text = "\n".join(["[masked]"] + before + piece) + "\nnext steps"
                    for depth, written in enumerate((text, json.dumps(text), json.dumps(json.dumps(text)))):
                        with self.subTest(width=width, cut=cut, words=len(before), depth=depth):
                            with mask_module.stored_row("0123456789abcdef"):
                                masked = mask(written)
                            self.assertEqual([line for line in lines[cut:-1] if line in piece and line in masked], [])
                            # the rest's next first token can go with it (coordinator's ruling, Nov 6)
                            self.assertIn("steps", masked)
                            if before:
                                self.assertIn(words, masked)

    def test_past_lines_that_decode_to_words_a_keys_rest_takes_no_tail_main_does_not(self):
        # Found by a comparison over random texts: a placeholder, a line of another stream that decodes to words, a
        # key encoded alone or a key's lines with no footer, "next steps", then a public key wrapped 16 wide. Main
        # starts no rest past the line of words, so it keeps the public key; in an older kb's row, the rest read past
        # that line took main's tail of tokens with it, and the public key went.
        import base64
        import random
        import textwrap
        from kb import mask as mask_module
        r = random.Random(1611)
        body = base64.b64encode(made_up_private_key(r)).decode()
        pem = ("-----BEGIN PRIVATE KEY-----\n" + "\n".join(textwrap.wrap(body, 64)) + "\n-----END PRIVATE KEY-----\n")
        public = textwrap.wrap(base64.b64encode(made_up_public_key(r)).decode(), 16)
        for width in (64, 76):
            words = base64.b64encode((b"notes " * 40)[:width // 4 * 3]).decode()
            encoded = textwrap.wrap(base64.b64encode(pem.encode()).decode(), width)
            cut = textwrap.wrap(body[40:], width)  # from inside the body: no footer, and no key's body start
            for key, fresh in ((encoded, True), (cut, False)):
                for gap in ("\n", "\n\n"):
                    text = "[masked]" + gap + "\n".join([words] + key + ["next steps"] + public)
                    for older in (False, True):
                        for depth, written in enumerate((text, json.dumps(text), json.dumps(json.dumps(text)))):
                            with self.subTest(width=width, key=key[0][:8], gap=len(gap), older=older, depth=depth):
                                if older:
                                    with mask_module.stored_row("0123456789abcdef"):
                                        masked = mask(written)
                                        again = mask(masked)
                                else:
                                    masked = mask(written)
                                    again = mask(masked)
                                self.assertEqual([line for line in public if line not in masked], [])
                                self.assertIn(words, masked)
                                self.assertIn("next steps", masked)
                                # past a blank line, a piece with neither its header nor its footer is the next
                                # paragraph's, which no rest reaches: it shows, as on main
                                if fresh or older and gap == "\n":
                                    self.assertEqual([line for line in key[:-1] if line in masked], [])
                                self.assertEqual(again, masked)


class Base64KeyPieceLimitTest(unittest.TestCase):
    """The README's limit: a piece of a key's base64 with neither its header nor its footer shows, as on main, unless
    a key's rest after a placeholder takes it. A rest starts with base64 glued to the placeholder, or with a line right
    after it of base64 alone, 40 characters or more, starting as a key's body does ("MII..."); in a row an older kb
    stored, 36 characters or more with a capital, a small letter and a digit, "+" or "/" are enough, and lines there
    that decode to words are passed over first, past which, where main's rest runs on, a line of base64 alone starts
    it at any length. A longer line further down does not count. A rest that starts right at its placeholder runs on past lines of words, as
    main's does; one that starts past lines that decode to words does not."""

    @staticmethod
    def masks(text, older):
        from kb import mask as mask_module
        if older:
            with mask_module.stored_row("0123456789abcdef"):
                return mask(text)
        return mask(text)

    def check(self, text, lines, fresh, older, kept="next steps"):
        for is_older, outcome in ((False, fresh), (True, older)):
            for depth, written in enumerate((text, json.dumps(text), json.dumps(json.dumps(text)))):
                with self.subTest(text=text[:40], older=is_older, depth=depth):
                    masked = self.masks(written, is_older)
                    if outcome == "shows":
                        self.assertEqual([line for line in lines if line not in masked], [])
                    else:
                        self.assertEqual([line[:16] for line in lines if line[:16] in masked], [])
                    # a rest's next first token can go with it (coordinator's ruling, Nov 6); the words after it stay
                    self.assertIn(kept if outcome == "shows" else kept.split(" ", 1)[-1], masked)

    def test_a_piece_after_a_placeholder_shows_until_its_first_line_starts_a_rest(self):
        # 34 characters after "[masked] ", as the comparison met one; at 36, read as an older kb's rest; at 40, which
        # is a key's body start and 36 more, read fresh too; glued to the placeholder, at any length
        import base64
        import random
        r = random.Random(1609)
        body = base64.b64encode(made_up_private_key(r)).decode()
        self.assertTrue(body.startswith("MII"))
        for size, glue, fresh, older in ((34, " ", "shows", "shows"), (36, " ", "shows", "masked"),
                                         (40, " ", "masked", "masked"), (34, "", "masked", "masked")):
            self.check(f"[masked]{glue}{body[:size]}\nnext steps", [body[:size]], fresh, older)

    def test_a_piece_after_another_keys_footer_shows_until_its_first_line_starts_a_rest(self):
        # 16 wide right after a key encoded as base64, as the comparison met two; the key's placeholder comes before it
        import base64
        import random
        import textwrap
        r = random.Random(1610)
        key = textwrap.wrap(Base64ReviewFourteenTest.key64(), 64)
        body = base64.b64encode(made_up_private_key(r)).decode()
        inner, start = body[1:241], body[:240]  # from inside the body ("IIC..."), and from its start ("MIIC...")
        for lines, fresh, older in ((textwrap.wrap(inner, 16), "shows", "shows"),
                                    ([inner[:16], inner[16:64]] + textwrap.wrap(inner[64:], 16), "shows", "shows"),
                                    ([inner[:48]] + textwrap.wrap(inner[48:], 16), "shows", "masked"),
                                    ([start[:48]] + textwrap.wrap(start[48:], 16), "masked", "masked")):
            text = "\n".join(key + lines) + "\nnext steps"
            self.check(text, lines, fresh, older)
            for depth, written in enumerate((text, json.dumps(text))):
                with self.subTest(text=lines[0], depth=depth, key=True):
                    self.assertEqual([line for line in key[:-1] if line in mask(written)], [])


    def test_a_piece_further_down_goes_with_a_rest_that_starts_at_its_placeholder_alone(self):
        # Past a line of words, a piece further down the paragraph goes with a rest that starts right at its
        # placeholder, as main's reading takes it, and shows after one that starts past lines that decode to words,
        # where main reads none (which in fresh text starts no rest at all).
        import base64
        import random
        import textwrap
        r = random.Random(1612)
        body = base64.b64encode(made_up_private_key(r)).decode()
        first, later = textwrap.wrap(body[:192], 64), textwrap.wrap(body[400:592], 64)
        for width in (64, 76):
            words = base64.b64encode((b"notes " * 40)[:width // 4 * 3]).decode()
            at = "\n".join(["[masked]"] + first + ["next steps"] + later) + "\nmore notes"
            past = "\n".join(["[masked]", words] + first + ["next steps"] + later) + "\nmore notes"
            self.check(at, first + later, "masked", "masked", kept="more notes")
            self.check(past, later, "shows", "shows", kept="more notes")
            self.check(past, first, "shows", "masked", kept=words)

class Base64RestScanToNextPlaceholderTest(unittest.TestCase):
    """Each placeholder's reading of the lines of base64 after it, as public material or a key's rest, stops at the
    next placeholder: a line that holds one is no line of base64, so no line past it is read. Many placeholders in one
    line cost time in proportion to it, and a key's rest that runs on past the next placeholder still goes whole."""

    def test_many_placeholders_glued_to_base64_in_one_line_cost_time_in_proportion(self):
        import time
        from kb import mask as mask_module
        for unit in ("[masked]A", "[masked]Ab+/9 "):
            for older in (False, True):
                with self.subTest(unit=unit, older=older):
                    took = {}
                    for size in (16, 32, 64):  # KB; each placeholder read the whole rest of the line, in its square
                        text = unit * (size * 1024 // len(unit))
                        started = time.process_time()
                        if older:
                            with mask_module.stored_row("0123456789abcdef"):
                                mask(text)
                        else:
                            mask(text)
                        took[size] = time.process_time() - started
                        if size > 16:  # twice the text, about twice the time: three times at most, or eight at 64
                            self.assertLess(took[size], (3 if size == 32 else 8) * max(took[16], 0.02), took)

    def test_a_keys_rest_that_runs_on_past_the_next_placeholder_is_masked_whole(self):
        # The first placeholder's reading of lines stops at the line holding the next one; the rest main reads runs on
        # past it to the paragraph's end all the same, and the next placeholder reads its own: the key goes whole, in
        # fresh text and in an older kb's row, raw and as JSON, and so does public material before the next one
        import base64
        import random
        import textwrap
        from kb import mask as mask_module
        r = random.Random(1612)
        lines = textwrap.wrap(base64.b64encode(made_up_private_key(r)).decode(), 64)[:10]
        public = textwrap.wrap(base64.b64encode(made_up_public_key(r)).decode(), 64)
        bodies = []
        for at in (1, 4, 8):
            bodies += ["\n".join(lines[:at] + ["[masked]" + lines[at]] + lines[at + 1:]),  # glued to a line of the key
                       "\n".join(lines[:at] + ["[masked]"] + lines[at:]),  # on a line of its own
                       "\n".join(lines[:at] + [lines[at][:30] + " [masked] " + lines[at][30:]] + lines[at + 1:])]
        bodies.append("\n".join(public + ["[masked]"] + lines))
        # in JSON inside JSON the rest takes one of the two escaped breaks after it, as the branch's did before this
        # change; main's takes the rest of the text
        expected = ("notes [masked]\n\nnext steps", ) * 2 + ("notes [masked]\nnext steps", )
        for n, body in enumerate(bodies):
            text = f"notes [masked]\n{body}\n\nnext steps"
            for older in (False, True):
                for depth, written in enumerate((text, json.dumps(text), json.dumps(json.dumps(text)))):
                    with self.subTest(n=n, older=older, depth=depth):
                        if older:
                            with mask_module.stored_row("0123456789abcdef"):
                                masked = mask(written)
                        else:
                            masked = mask(written)
                        self.assertEqual(masked, (expected[0], json.dumps(expected[1]),
                                                  json.dumps(json.dumps(expected[2])))[depth])


class Base64ReviewSeventeenTest(unittest.TestCase):
    """#34's seventeenth review: a header quoted inside one encoded stream ("notes ... -----BEGIN PRIVATE KEY-----" in
    a document) was held for a later stream's footer, so that key ran from the document and took the public key
    between; and after a placeholder glued to an encoded stream whose first line decodes to words, the stream's last
    line, base64 of a password with no line break after it, started no rest and showed, which main masks."""

    @staticmethod
    def encoded(data, width=64):
        import base64
        import textwrap
        return "\n".join(textwrap.wrap(base64.b64encode(data).decode(), width))

    @staticmethod
    def pem(r):
        import base64
        der = bytes.fromhex("302e020100300506032b657004220420") + bytes(r.randrange(256) for _ in range(32))
        return b"-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(der) + b"\n-----END PRIVATE KEY-----\n"

    @staticmethod
    def whole_lines(data, width):
        """`data` with spaces before its last line break, so its base64 fills whole lines: no "=", no short line."""
        size = width // 4 * 3
        return data[:-1] + b" " * (-len(data) % size) + data[-1:]

    def test_a_header_quoted_in_one_stream_starts_no_key_for_a_later_streams_footer(self):
        import random
        r = random.Random(1701)
        public = bytes.fromhex("302a300506032b6570032100") + bytes(r.randrange(256) for _ in range(32))
        for width in (64, 76):
            for quoted in (b"Read the documentation before importing.\n", b"see the runbook\n" * 3):
                document = b"notes " * 8 + b"-----BEGIN PRIVATE KEY-----\n" + quoted
                for full, between in ((False, True), (True, True), (True, False)):
                    # the document's stream ends with "=" or a short line, or (full) fills its last line, which only
                    # the public key's stream after it, ending with "=", or nothing, follows
                    lines = self.encoded(self.whole_lines(document, width) if full else document, width)
                    kept = [lines] + ([self.encoded(public, width), self.encoded(b"notes " * 8, width)] if between else [])
                    private = self.encoded(self.pem(r), width)
                    text = "\n".join(kept + [private])
                    for depth, written in enumerate((text, json.dumps(text), json.dumps(json.dumps(text)))):
                        masked = mask(written)
                        with self.subTest(width=width, quoted=quoted[:8], full=full, between=between, depth=depth):
                            self.assertEqual([line for part in kept for line in part.splitlines()
                                              if line not in masked], [])
                            self.assertEqual([line for line in private.splitlines()
                                              if len(line) >= 16 and line in masked], [])

    def test_base64_past_lines_of_words_glued_to_a_placeholder_goes_where_main_takes_it(self):
        # main masks the lines after the placeholder as a key's rest, plain and JSON-escaped once; JSON-escaped twice
        # it shows them, and so may this branch
        from kb import mask as masking
        from tests.test_mask_against_today import main as today
        password = b"password=walrussecret9"
        shown = self.encoded(password)
        for words in (b"notes " * 8, b"notes " * 16):
            for brk in ("\n", "\r\n"):
                for end in ("", brk, " ", " " + brk, brk + "next steps"):
                    text = "[masked]" + self.encoded(words + password).replace("\n", brk) + end
                    for older in (False, True):
                        for depth, written in enumerate((text, json.dumps(text), json.dumps(json.dumps(text)))):
                            with self.subTest(words=len(words), brk=brk, end=end, older=older, depth=depth):
                                if older:
                                    with masking.stored_row(None):
                                        masked = mask(written)
                                else:
                                    masked = mask(written)
                                if depth < 2 or shown not in today.mask(written):
                                    self.assertNotIn(shown, masked)


class Base64ReviewEighteenTest(unittest.TestCase):
    """#34's checks for its eighteenth review. Texts the comparison against d4e3c63 met: a document's stream that
    quotes a header, a public key's stream, then key pieces that d4e3c63 hid only as its key ran from the document's
    header over all of them; the pieces have neither a header nor a footer of their own, or are a PGP key encoded in
    one stream after "例：" (the README's limits), and show as they do with the document gone, as on main. And a text
    where a key's rest past lines of words, started where main's runs on, took in the next placeholder without the
    tail main's takes. Past lines of words, a word alone on its line stays, and a rest starts only where main's rest
    from the placeholder runs on."""

    @staticmethod
    def masks(text, older):
        from kb import mask as masking
        if older:
            with masking.stored_row("0123456789abcdef"):
                return mask(text)
        return mask(text)

    @staticmethod
    def written(text, depth):
        """`text` as a JSON string holding it, escaped `depth` times, writes it."""
        for _ in range(depth):
            text = json.dumps(text)[1:-1]
        return text

    @staticmethod
    def as_json(text, depth):
        for _ in range(depth):
            text = json.dumps(text)
        return text

    def test_texts_the_comparison_met_keep_the_document_and_show_no_more_of_the_pieces(self):
        import os
        with open(os.path.join(os.path.dirname(__file__), "fixtures", "base64_overrun_r18.json"), encoding="utf-8") as f:
            cases = json.load(f)
        for n, case in enumerate(cases):
            depth = {1: 1, 2: 2}.get(case["form"], 0)
            others = "\n".join(text for kind, text, _ in case["parts"] if kind != "private")
            windows = set()
            for kind, text, own in case["parts"]:
                for line in (own or text).replace("[masked]", " ").split("\n") if kind == "private" else ():
                    line = line.strip()
                    windows |= {line[i:i + 16] for i in range(0, max(len(line) - 15, 0), 8)} - {""}
            windows = {w for w in windows if w not in others}
            kept = [line for kind, text, _ in case["parts"] if kind == "keep" for line in text.split("\n") if len(line) >= 12]
            for older in (False, True):
                with self.subTest(case=n, older=older):
                    masked = self.masks(case["text"], older)
                    self.assertEqual([line for line in kept if self.written(line, depth) not in masked], [])
                    shown = [w for w in windows if self.written(w, depth) in masked]
                    self.assertLessEqual(len(shown), case["shown_at_most"], shown[:3])

    def test_a_word_alone_on_its_line_after_a_stream_of_words_glued_to_a_placeholder_stays(self):
        # plain words are base64 characters; main's rest takes the word, d4e3c63 kept it, and so does this branch
        encoded = Base64ReviewSeventeenTest.encoded
        password = encoded(b"pw=Tq9sVz7Yp2mRx")
        for word in ("Thanks", "ok", "README"):
            for words in (b"notes " * 8, b"notes " * 16):
                for brk in ("\n", "\r\n"):
                    for end in ("", " ", brk + "next steps", brk + password):
                        text = "[masked]" + encoded(words).replace("\n", brk) + brk + word + end
                        for older in (False, True):
                            for depth in range(3):
                                with self.subTest(word=word, words=len(words), brk=brk, end=end[-6:], older=older,
                                                  depth=depth):
                                    masked = self.masks(self.as_json(text, depth), older)
                                    self.assertIn(self.written(brk + word, depth), masked)
                                    if end.endswith(password) and depth < 2:  # main's rest takes it
                                        self.assertNotIn(password, masked)

    def test_past_lines_of_words_a_rest_starts_only_where_mains_rest_from_the_placeholder_runs_on(self):
        # an older row's placeholder on a line of its own, where main reads no rest: a short line past the lines of
        # words starts none, so a document's stream and a public key's below it stay
        import random
        r = random.Random(1801)
        encoded = Base64ReviewSeventeenTest.encoded
        public = encoded(bytes.fromhex("302a300506032b6570032100") + bytes(r.randrange(256) for _ in range(32)))
        document = encoded(b"notes " * 8 + b"-----BEGIN PRIVATE KEY-----\nsee the runbook\n")
        for words in (b"notes " * 8, b"notes " * 16):
            for brk in ("\n", "\r\n"):
                text = brk.join(["[masked]", encoded(words), encoded(b"pw=Tq9sVz7Yp2mRx"), document, public,
                                 "next steps"])
                kept = (document + "\n" + public).split("\n")
                for older in (False, True):
                    for depth in range(3):
                        with self.subTest(words=len(words), brk=brk, older=older, depth=depth):
                            masked = self.masks(self.as_json(text, depth), older)
                            self.assertEqual([line for line in kept if line not in masked], [])


class Base64ReviewEighteenFindingsTest(unittest.TestCase):
    """#34's eighteenth review: a key file encoded alone, wrapped narrower than the stream above it, had its first,
    shorter line read as that stream's last, so the header held for its footer (its armour comment holds a control
    character, where the footer's reading upward stops) was dropped, and its lines down to that character showed; and
    a key file that starts with a byte order mark, encoded below another stream or a heading, opened only where all the
    text decoded before its header was the mark, so its footer read up over a document above it, or its first line
    showed under a heading."""

    masks = staticmethod(Base64ReviewEighteenTest.masks)
    written = staticmethod(Base64ReviewEighteenTest.written)
    as_json = staticmethod(Base64ReviewEighteenTest.as_json)
    encoded = staticmethod(Base64ReviewSeventeenTest.encoded)
    MARK = b"\xef\xbb\xbf"

    @classmethod
    def keys(cls):
        import base64
        import random
        import textwrap
        r = random.Random(1802)
        body = "\n".join(textwrap.wrap(base64.b64encode(bytes(r.randrange(256) for _ in range(300))).decode(), 64))
        pgp = (b"-----BEGIN PGP PRIVATE KEY BLOCK-----\nComment: team\x01key\n\n" + body.encode()
               + b"\n=Ab3d\n-----END PGP PRIVATE KEY BLOCK-----\n")
        der = bytes.fromhex("302e020100300506032b657004220420") + bytes(r.randrange(256) for _ in range(32))
        pem = b"-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(der) + b"\n-----END PRIVATE KEY-----\n"
        return {"pgp": pgp, "pem": pem}

    def check(self, text, block, kept, label):
        """No line of the key's `block` shows, and each text in `kept` stays, at JSON depths 0 to 2, fresh and as an
        older row."""
        for older in (False, True):
            for depth in range(3):
                with self.subTest(label, older=older, depth=depth):
                    masked = self.masks(self.as_json(text, depth), older)
                    self.assertEqual([part for part in kept if self.written(part, depth) not in masked], [])
                    # what is left once the kept text is taken out, as a document above may quote the key's header
                    for part in kept:
                        masked = masked.replace(self.written(part, depth), "", 1)
                    self.assertEqual([line for line in block.splitlines()
                                      if len(line) >= 12 and self.written(line, depth) in masked], [])

    def test_a_key_file_encoded_alone_below_a_stream_of_another_width_shows_no_line(self):
        for above_width, width in ((64, 32), (76, 64), (32, 64), (64, 76), (64, 64), (76, 76)):
            for lead in (b"notes " * 8, b"notes " * 9, b"notes " * 16):
                for name, key in self.keys().items():
                    for brk in ("\n", "\r\n"):
                        above, block = self.encoded(lead, above_width), self.encoded(key, width)
                        text = brk.join([above.replace("\n", brk), block.replace("\n", brk), "next steps"])
                        self.check(text, block, [above.replace("\n", brk) + brk, brk + "next steps"],
                                   (above_width, width, len(lead), name, brk))

    def test_a_stream_above_that_ends_on_a_line_as_wide_as_the_key_below_with_a_header_held_in_it(self):
        # the stream above, a document that quotes a header, ends on a short line exactly as wide as the key's lines
        header = b"-----BEGIN PRIVATE KEY-----\n"
        for above_width, width, size, at in ((64, 32, 120, 48), (76, 64, 162, 48), (76, 64, 105, 60)):
            # `size` bytes whose base64 ends on a line `width` wide; the quoted header from byte `at`: on a line above
            # the last, or (105, 60) on the last, short line itself
            filler = b"notes " * (at // 6) + b"note"[:at % 6]
            tail = b"see the runbook"
            document = filler + header + tail + b" " * (size - len(filler) - len(header) - len(tail) - 1) + b"\n"
            above = self.encoded(document, above_width)
            assert len(above.splitlines()[-1]) == width, (above_width, width, size)
            for name, key in self.keys().items():
                block = self.encoded(key, width)
                text = "\n".join([above, block, "next steps"])
                self.check(text, block, [above + "\n", "\nnext steps"], (above_width, width, size, at, name))
        # the document's text runs to its end, with no line break, and the key file below starts with a byte order mark
        for above_width, width, at, end in ((76, 64, 48, b"walrus ledger"), (76, 64, 60, b"x"),
                                            (76, 32, 24, b"walrus ledger"), (76, 32, 36, b"x")):
            above = self.encoded(b"notes " * (at // 6) + header + b" see the runbook" + end, above_width)
            assert len(above.splitlines()[-1]) == width, (above_width, width, at)
            for name, key in self.keys().items():
                block = self.encoded(self.MARK + key, width)
                text = "\n".join([above, block, "next steps"])
                self.check(text, block, [above + "\n", "\nnext steps"], (above_width, width, at, end, name))

    def test_a_key_file_with_a_byte_order_mark_at_the_start_after_another_stream_and_under_a_heading(self):
        for width in (64, 76):
            for name, key in self.keys().items():
                block = self.encoded(self.MARK + key, width)
                for before in ("", self.encoded(b"notes " * 8, width), self.encoded(b"notes " * 9, width),
                               self.encoded(b"notes " * 8 + b"x", width), self.encoded(b"notes " * 8, 32),
                               "Plan", "RENT", "Walrus\nKEYS"):
                    for brk in ("\n", "\r\n"):
                        parts = ([before.replace("\n", brk)] if before else []) + [block.replace("\n", brk), "next steps"]
                        kept = ([before.replace("\n", brk) + brk] if before else []) + [brk + "next steps"]
                        self.check(brk.join(parts), block, kept, (width, name, before[:12], brk))


class Base64ReviewNineteenTest(unittest.TestCase):
    """#34's checks for its nineteenth review. Texts the comparison against 321c89f met: after a placeholder glued to an
    encoded stream of words, a placeholder alone on its line or a line of words, then another stream of words whose
    last line is base64 of a password: the rest past the lines of words stopped at that line, so the password's line
    showed, which main's rest from the placeholder, with its tail, masks. And the limit the coordinator granted: past
    an encoded key's placeholder, public material and lines of words in the same paragraph are masked where a line of
    base64 past them, base64 of words included, reads as a possible fragment of a key."""

    encoded = staticmethod(Base64ReviewSeventeenTest.encoded)

    def test_a_password_line_past_a_placeholder_or_a_line_of_words_goes_where_main_takes_it(self):
        # main masks it plain and JSON-escaped once; JSON-escaped twice main reads no rest there, and may show it
        from kb import mask as masking
        from tests.test_mask_against_today import main as today
        for words in (b"notes " * 8, b"notes " * 16):
            for password in (b"password=walrussecret9", b"pw=Tq9sVz7Yp2mRx"):
                stream = self.encoded(words + password)
                shown = stream.splitlines()[-1]
                for between in ("[masked]", "deploy on port 8080", "[masked]\ndeploy on port 8080",
                                "deploy on port 8080\n[masked]"):
                    for brk in ("\n", "\r\n"):
                        for end in ("", brk + "next steps", brk + brk + "next paragraph"):
                            body = "\n".join(["[masked]" + self.encoded(words), between, stream]).replace("\n", brk)
                            text = "intro notes" + brk + body + end
                            for older in (False, True):
                                for depth, written in enumerate((text, json.dumps(text), json.dumps(json.dumps(text)))):
                                    with self.subTest(words=len(words), password=password[:3], between=between,
                                                      brk=brk, end=end, older=older, depth=depth):
                                        if older:
                                            with masking.stored_row(None):
                                                masked = mask(written)
                                        else:
                                            masked = mask(written)
                                        if depth < 2 or shown not in today.mask(written):
                                            self.assertNotIn(shown, masked)
                                        self.assertIn("intro notes", masked)
                                        if "paragraph" in end:
                                            self.assertIn("next paragraph", masked)

    def test_public_material_past_an_encoded_keys_placeholder_goes_with_a_line_of_base64_past_it(self):
        # the limit: public material and lines of words after the key, in its paragraph, are masked with a line of
        # base64 past them, base64 of words too, which reads as a possible fragment; alone, after a word alone or
        # before a blank line they stay
        import base64
        import random
        from kb import mask as masking
        r = random.Random(19)
        pem = (b"-----BEGIN PRIVATE KEY-----\n" + self.encoded(made_up_private_key(r)).encode()
               + b"\n-----END PRIVATE KEY-----\n")
        key = base64.b64encode(pem).decode()
        public = self.encoded(made_up_public_key(r))
        notes = self.encoded(b"notes " * 8)
        for after, kept in (([], True), (["notes"], True), (["", notes], True), ([notes], False),
                            (["deploy on port 8080", notes], False), ([notes, "", "next paragraph"], False)):
            for older in (False, True):
                for depth in (0, 1):
                    text = "\n".join(["intro notes", key, public] + after)
                    written = json.dumps(text) if depth else text
                    with self.subTest(after=[line[:12] for line in after], older=older, depth=depth):
                        if older:
                            with masking.stored_row(None):
                                masked = mask(written)
                        else:
                            masked = mask(written)
                        self.assertNotIn(key[:40], masked)
                        self.assertIn("intro notes", masked)
                        self.assertEqual(all(line in masked for line in public.splitlines()), kept)
                        if "next paragraph" in after:
                            self.assertIn("next paragraph", masked)


class Base64ReviewNineteenFindingsTest(unittest.TestCase):
    """#34's nineteenth review's findings, with keys as OpenSSL and GnuPG write them (fixtures/base64_keys_r19.json): a
    key wrapped below a wider stream of words, on a line as wide as none above or below it and then its short last line,
    whose header line was read as that stream's last line; a password past a line with no base64 but a placeholder's,
    such as "---" or an indented placeholder; a stream quoting a header whose last line has text after it, or a line
    break escaped as \\u000b; and a key between JSON's escaped form feeds."""

    @staticmethod
    def fixture(name):
        import base64
        import os
        with open(os.path.join(os.path.dirname(__file__), "fixtures", "base64_keys_r19.json"), encoding="utf-8") as f:
            return base64.b64decode(json.load(f)[name])

    @staticmethod
    def written(text, depth):
        for _ in range(depth):
            text = json.dumps(text)
        return text

    def test_a_key_in_two_lines_below_a_wider_stream_is_masked_and_the_stream_stays(self):
        import base64
        import textwrap
        from kb import mask as masking
        for name in ("openssl_ed25519", "openssl_p256"):
            for lead in (b"", b"\xef\xbb\xbf"):
                for crlf in (False, True):
                    pem = self.fixture(name)
                    data = lead + (pem.replace(b"\n", b"\r\n") if crlf else pem)
                    encoded = base64.b64encode(data).decode()
                    for width in range(len(encoded) // 2 + 4, len(encoded), 8):
                        lines = textwrap.wrap(encoded, width)
                        document = base64.b64encode(b"notes " * (width // 8 + 6)).decode()
                        for brk in ("\n", "\r\n"):
                            text = brk.join([document] + lines)
                            for older in (False, True):
                                for depth in range(3):
                                    written = self.written(text, depth)
                                    with self.subTest(name=name, lead=lead, crlf=crlf, width=width, brk=brk,
                                                      older=older, depth=depth):
                                        if older:
                                            with masking.stored_row(None):
                                                masked = mask(written)
                                        else:
                                            masked = mask(written)
                                        self.assertIn(document, masked)
                                        for line in lines:
                                            self.assertNotIn(line, masked)

    def test_a_gnupg_key_with_a_control_byte_in_its_comment_below_a_wider_stream_is_masked(self):
        import base64
        import textwrap
        encoded = base64.b64encode(self.fixture("gnupg_control_comment")).decode()
        for width in (512, 400, 336, 256, 76, 64, 56):
            lines = textwrap.wrap(encoded, width)
            for words in (64, 100, 192):
                document = base64.b64encode(b"notes " * words).decode()
                for depth in range(3):
                    written = self.written("\n".join([document] + lines), depth)
                    with self.subTest(width=width, words=words, depth=depth):
                        masked = mask(written)
                        self.assertIn(document, masked)
                        for line in lines:
                            self.assertNotIn(line, masked)

    def test_a_password_past_a_line_with_no_base64_but_a_placeholders_goes_where_main_takes_it(self):
        import base64
        from kb import mask as masking
        from tests.test_mask_against_today import main as today
        password = base64.b64encode(b"pw=Fixture9value").decode()
        marks = ("  [masked]", "\t[masked]", "---", "    ---", "***", "```", "- [masked]", "> [masked]", "{", "}", "#",
                 "[masked] [masked]")
        others = ("x = 1", "token: [masked]", "--- notes", "  " + base64.b64encode(b"pw=Other9secretv").decode() + " # old")
        for between in marks + others:
            for words in (b"notes " * 8, b"notes " * 16):
                text = "intro notes\n[masked]" + base64.b64encode(words).decode() + "\n" + between + "\n" + password
                for older in (False, True):
                    for depth in range(3):
                        written = self.written(text, depth)
                        with self.subTest(between=between, words=len(words), older=older, depth=depth):
                            if older:
                                with masking.stored_row(None):
                                    masked = mask(written)
                            else:
                                masked = mask(written)
                            if password not in today.mask(written):
                                self.assertNotIn(password, masked)
                            self.assertIn("intro notes", masked)
                            if between in marks:
                                kept = between
                                for _ in range(depth):
                                    kept = json.dumps(kept)[1:-1]
                                self.assertIn(kept, masked)

    def test_a_keys_rest_in_an_older_row_ends_with_its_placeholders_paragraph(self):
        # Found by comparing the fix for a password past a line of marks with main: the password went, and so the
        # placeholder came to stand before a blank line, then a paragraph of base64 that decodes to words and quotes a
        # header, and a certificate. In a row an older kb stored, the rest read its first line as a line of words to
        # pass over, across the blank line, and took the lines after it; main's rest ends with its placeholder's
        # paragraph. In JSON in JSON a blank line does not end a paragraph, so depth 2 is not read here.
        import base64
        import random
        import textwrap
        from kb import mask as masking
        document = textwrap.wrap(base64.b64encode(b"notes " * 8 + b"-----BEGIN PRIVATE KEY-----\nsee the runbook\n"
                                                  b"see the runbook\n").decode(), 64)
        certificate = textwrap.wrap(base64.b64encode(made_up_certificate(random.Random(34))).decode(), 64)
        words = base64.b64encode(b"notes " * 8).decode()
        password = base64.b64encode(b"pw=Fixture9value").decode()
        for lead in ("[masked]", "- [masked]", "notes [masked]", "[masked]" + words, "[masked]\n" + words,
                     "[masked]" + words + "\nx = 1\n" + password, "[masked]" + words + "\n***\n" + password):
            for blank in ("\n\n", "\r\n\r\n", "\n \n"):
                brk = "\r\n" if "\r" in blank else "\n"
                for below in (document, document + certificate):
                    text = lead.replace("\n", brk) + blank + brk.join(below)
                    for depth in (0, 1):
                        written = self.written(text, depth)
                        with self.subTest(lead=lead[:12], blank=blank, lines=len(below), depth=depth):
                            with masking.stored_row(None):
                                masked = mask(written)
                            self.assertEqual([line[:16] for line in below if line not in masked], [])
                            self.assertNotIn(password, masked)

    def test_a_stream_quoting_a_header_stays_with_text_after_its_last_line(self):
        import base64
        import textwrap
        lines = textwrap.wrap(base64.b64encode(b"notes " * 8 + b"-----BEGIN PRIVATE KEY-----\nsee the runbook\n")
                              .decode(), 64)
        for tail in ("\vnext steps", " next steps", "\fnext steps", " next steps", ", next steps", "…"):
            for depth in range(3):
                text = self.written("\n".join(lines) + tail, depth)
                with self.subTest(tail=tail, depth=depth):
                    self.assertEqual(mask(text), text)
        # and its lines indented with tabs, which JSON escapes ("\n\t"), and its lines' breaks escaped as JSON may
        # write them, \u000a, or \u000d\u000a
        for indent in ("\t", "\t\t", " \t"):
            for tail in ("", " next steps", "\nnext steps"):
                for depth in (1, 2):
                    text = self.written(("\n" + indent).join(lines) + tail, depth)
                    with self.subTest(indent=indent, tail=tail, depth=depth):
                        self.assertEqual(mask(text), text)
        for brk in ("\\u000a", "\\u000d\\u000a", "\\u000b", "\\f"):
            for tail in ("", brk + "next steps", " next steps"):
                text = '"' + brk.join(lines) + tail + '"'
                for depth in (0, 1):
                    written = json.dumps(text) if depth else text
                    with self.subTest(brk=brk, tail=tail, depth=depth):
                        self.assertEqual(mask(written), written)

    def test_a_key_between_escaped_form_feeds_is_masked_and_its_json_stays_json(self):
        import base64
        key = (b"-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(bytes.fromhex("302e020100300506032b657004220420")
               + bytes(range(32))) + b"\n-----END PRIVATE KEY-----\n")
        for key_data in (key, self.fixture("openssl_ed25519"), self.fixture("openssl_p256")):
            encoded = base64.b64encode(key_data).decode()
            for before in ("\f", "\n"):  # a backspace before it is a character no reader sees: "Plan" goes with it
                for after in ("\f", "\b", "\n"):
                    for depth in (1, 2):
                        written = self.written("Plan" + before + encoded + after + "next steps", depth)
                        with self.subTest(key=key_data[-30:-26], before=before, after=after, depth=depth):
                            masked = mask(written)
                            read = masked
                            for _ in range(depth):
                                read = json.loads(read)
                            self.assertNotIn(encoded[:40], masked)
                            self.assertTrue(read.startswith("Plan"), read)
                            self.assertTrue(read.endswith("next steps"), read)


class Base64KeyCutBeforeWordsTest(unittest.TestCase):
    """#34 against main 032391d before its twentieth review: a private key with its header and no footer, its body
    quoted with "> ", whose short last line runs on into words ("ei harbor deploy", "Ynu walrus moved"). Where an older
    kb had masked the header alone, that line read as a line of words, so the body's last characters showed, which main
    masks with the rest of the paragraph. Made-up keys only."""

    @staticmethod
    def bodies():
        import base64
        import random
        r = random.Random(83)
        openssh = b"openssh-key-v1\0" + bytes(r.randrange(256) for _ in range(380))
        return {"PRIVATE KEY": (base64.b64encode(made_up_private_key(r)).decode(), 64),
                "OPENSSH PRIVATE KEY": (base64.b64encode(openssh).decode(), 70)}

    @staticmethod
    def text(header, lines, fragment, q="> ", brk="\n"):
        return (f"intro notes{brk}{q}{header}{brk}" + brk.join(q + line for line in lines)
                + f"{brk}{q}{fragment} walrus moved today{brk}{brk}next paragraph")

    def test_no_character_of_a_body_that_runs_on_into_words_survives(self):
        import textwrap
        from kb import mask as masking
        for kind, (body, width) in self.bodies().items():
            for header in (f"-----BEGIN {kind}-----", "[masked]"):
                for full in (1, 2, 3):
                    lines = textwrap.wrap(body, width)[:full]
                    for fragment in ("ei", "Ynu", "k", "Abc"):
                        for q in ("> ", ""):
                            for brk in ("\n", "\r\n"):
                                text = self.text(header, lines, fragment, q, brk)
                                for depth in (0, 1, 2):
                                    written = text
                                    for _ in range(depth):
                                        written = json.dumps(written)
                                    for older in (False, True):
                                        with self.subTest(kind=kind, header=header[:9], full=full, fragment=fragment,
                                                          q=q, brk=brk, depth=depth, older=older):
                                            # JSON escapes no character of base64 or of these words, so the masked
                                            # text is read as written; a key masked to its paragraph's end may take
                                            # an escape's backslash with it, so it is not parsed back
                                            if older:
                                                with masking.stored_row(None):
                                                    masked = mask(written)
                                            else:
                                                masked = mask(written)
                                            self.assertNotIn(f"{fragment} walrus", masked)
                                            self.assertEqual([line[i:i + 8] for line in lines
                                                              for i in range(0, len(line) - 8, 4)
                                                              if line[i:i + 8] in masked], [])
                                            self.assertIn("intro notes", masked)
                                            if depth < 2 or header == "[masked]":
                                                # escaped twice, a header masks to the text's end, as on main
                                                self.assertIn("next paragraph", masked)

    def test_the_words_after_the_tail_still_show(self):
        # allowed, not required: where a line splits safely, the words after the body's last characters stay
        import textwrap
        for kind, (body, width) in self.bodies().items():
            for full in (1, 2):
                with self.subTest(kind=kind, full=full):
                    masked = mask(self.text("[masked]", textwrap.wrap(body, width)[:full], "Ynu"))
                    self.assertNotIn("Ynu", masked)
                    self.assertIn("walrus moved today", masked)

    def test_a_line_of_words_after_a_body_that_can_end_there_stays_whole(self):
        # a last line shorter than the one above it, or ending in "=", can be the body's last
        import textwrap
        for kind, (body, width) in self.bodies().items():
            lines = textwrap.wrap(body, width)
            for kept in (lines[:2] + [lines[2][:20]], lines[:2] + [lines[2][:19] + "="]):
                with self.subTest(kind=kind, last=kept[-1][-1]):
                    self.assertEqual(mask("[masked]\n" + "\n".join(kept) + "\nnext steps"), "[masked]\nnext steps")


class Base64ReviewTwentyFindingsTest(unittest.TestCase):
    """#34's twentieth review: a key's footer written into one of its armour headers ("Comment: -----END PGP PRIVATE
    KEY BLOCK-----"), with the real footer below, ended the key at the forged one, and the lines past it down to a
    control byte in a later comment showed; and a key's rest whose last line is a placeholder glued to a body line
    ("[masked]MIIC...") kept the short last line that runs on into words, which main masks. Made-up keys only."""
    FOOTERS = ("-----END PGP PRIVATE KEY BLOCK-----", "-----END PGP SECRET KEY BLOCK-----", "-----END PRIVATE KEY-----")

    @staticmethod
    def forged(footer, where):
        """The control-comment fixture with `footer` written into an armour header: a comment of its own before the
        fixture's, one after it, or inside a comment with text around it."""
        block = Base64ReviewNineteenFindingsTest.fixture("gnupg_control_comment").split(b"\n")
        at = next(i for i, line in enumerate(block) if line.startswith(b"Comment:"))
        header = {"before": b"Comment: " + footer.encode(), "after": b"Comment: " + footer.encode(),
                  "inside": b"Comment: see " + footer.encode() + b" below"}[where]
        block.insert(at + (where == "after"), header)
        return b"\n".join(block)

    def test_a_footer_in_an_armour_header_ends_no_key(self):
        import base64
        import textwrap
        from kb import mask as masking
        for footer in self.FOOTERS:
            for where in ("before", "after", "inside"):
                encoded = base64.b64encode(self.forged(footer, where)).decode()
                for width in (16, 32, 48, 64, 76, len(encoded)):
                    lines = textwrap.wrap(encoded, width)
                    for lead in ("", "intro notes\n", "key: "):
                        text = lead + "\n".join(lines) + "\n\nnext paragraph"
                        for depth in range(3):
                            written = Base64ReviewNineteenFindingsTest.written(text, depth)
                            for older in (False, True):
                                with self.subTest(footer=footer[9:20], where=where, width=width, lead=lead[:5],
                                                  depth=depth, older=older):
                                    if older:
                                        with masking.stored_row(None):
                                            masked = mask(written)
                                    else:
                                        masked = mask(written)
                                    self.assertEqual([i for i, line in enumerate(lines) if line in masked], [])
                                    self.assertIn("next paragraph", masked)
                                    if lead:
                                        self.assertIn(lead.split()[0].rstrip(":"), masked)

    def test_a_footer_that_starts_its_own_line_still_ends_the_key(self):
        # the forged footer moves no real one: the fixture as GnuPG wrote it, and a key file, still end where they end
        import base64
        import textwrap
        for name in ("gnupg_control_comment", "openssl_ed25519"):
            encoded = base64.b64encode(Base64ReviewNineteenFindingsTest.fixture(name)).decode()
            for width in (64, 76):
                lines = textwrap.wrap(encoded, width)
                with self.subTest(name=name, width=width):
                    self.assertEqual(mask("intro notes\n" + "\n".join(lines) + "\nnext steps"),
                                     "intro notes\n[masked]\nnext steps")

    def test_a_short_last_line_after_a_placeholder_glued_to_a_body_line_is_masked(self):
        import textwrap
        from kb import mask as masking
        for kind, (body, width) in Base64KeyCutBeforeWordsTest.bodies().items():
            lines = textwrap.wrap(body, width)
            for start in ("[masked]", f"-----BEGIN {kind}-----"):
                for above in (lines[:1], lines[:2], []):
                    glued = lines[len(above)]
                    for fragment in ("ei", "Ynu", "k"):
                        for q in ("> ", ""):
                            for brk in ("\n", "\r\n"):
                                text = (f"intro notes{brk}{q}{start}{brk}" + "".join(q + line + brk for line in above)
                                        + f"{q}[masked]{glued}{brk}{q}{fragment} walrus moved today{brk}{brk}"
                                        "next paragraph")
                                for depth in (0, 1, 2):
                                    written = Base64ReviewNineteenFindingsTest.written(text, depth)
                                    for older in (False, True):
                                        with self.subTest(kind=kind, start=start[:9], above=len(above),
                                                          fragment=fragment, q=q, brk=brk, depth=depth, older=older):
                                            if older:
                                                with masking.stored_row(None):
                                                    masked = mask(written)
                                            else:
                                                masked = mask(written)
                                            self.assertNotIn(f"{fragment} walrus", masked)
                                            key = above + [glued]
                                            self.assertEqual([line[i:i + 8] for line in key
                                                              for i in range(0, len(line) - 8, 4)
                                                              if line[i:i + 8] in masked], [])
                                            self.assertIn("intro notes", masked)
                                            if depth < 2 or start == "[masked]":
                                                self.assertIn("next paragraph", masked)


class Base64ReviewTwentyOneFindingsTest(unittest.TestCase):
    """#34's twenty-first review: a footer written into an armour header after a line break (a carriage return in a
    comment, or a line of its own above the comments), with text after it or none, still ended the key, and the lines
    past it down to a control byte in a later comment showed; and a placeholder glued to a body line with a space, a tab
    or another placeholder between kept the short last line that runs on into words. Made-up keys only."""
    FORMS = {"after a carriage return, with text": b"Comment: x\r%s suffix", "with spaces and text": b"Comment: x\r%s  tail",
             "between carriage returns": b"Comment: x\r%s\rmore", "a line after a comment": b"Comment: x\n%s",
             "after a CRLF": b"Comment: x\r\n%s\r", "a line of its own": b"%s"}

    @staticmethod
    def forged(footer, form):
        block = Base64ReviewNineteenFindingsTest.fixture("gnupg_control_comment").split(b"\n")
        at = next(i for i, line in enumerate(block) if line.startswith(b"Comment:"))
        block.insert(at, form % footer.encode())
        return b"\n".join(block)

    def test_a_footer_that_is_no_whole_line_or_has_another_below_ends_no_key(self):
        import base64
        import textwrap
        from kb import mask as masking
        for footer in Base64ReviewTwentyFindingsTest.FOOTERS:
            for form_name, form in self.FORMS.items():
                data = self.forged(footer, form)
                encoded = base64.b64encode(data).decode()
                # also each width that ends a line of the block right where the forged footer's bytes end
                end = data.index(footer.encode()) + len(footer)
                ends_there = [w for w in range(16, 80, 4) if end % 3 == 0 and (end // 3 * 4) % w == 0]
                for width in sorted({16, 32, 48, 64, 76, len(encoded), *ends_there}):
                    lines = textwrap.wrap(encoded, width)
                    for lead in ("", "intro notes\n", "key: "):
                        text = lead + "\n".join(lines) + "\n\nnext paragraph"
                        for depth in range(3):
                            written = Base64ReviewNineteenFindingsTest.written(text, depth)
                            for older in (False, True):
                                with self.subTest(footer=footer[9:20], form=form_name, width=width, lead=lead[:5],
                                                  depth=depth, older=older):
                                    if older:
                                        with masking.stored_row(None):
                                            masked = mask(written)
                                    else:
                                        masked = mask(written)
                                    self.assertEqual([i for i, line in enumerate(lines) if line in masked], [])
                                    self.assertIn("next paragraph", masked)

    def test_a_short_last_line_after_a_placeholder_and_a_gap_before_a_body_line_is_masked(self):
        import textwrap
        from kb import mask as masking
        for kind, (body, width) in Base64KeyCutBeforeWordsTest.bodies().items():
            lines = textwrap.wrap(body, width)
            for start in ("[masked]", f"-----BEGIN {kind}-----"):
                for above in (lines[:1], lines[:2]):
                    glued = lines[len(above)]
                    for gap in (" ", "  ", "\t", " [masked] ", "[masked]", " - "):
                        for fragment in ("ei", "Ynu"):
                            for q in ("> ", ""):
                                for brk in ("\n", "\r\n"):
                                    text = (f"intro notes{brk}{q}{start}{brk}" + "".join(q + line + brk for line in above)
                                            + f"{q}[masked]{gap}{glued}{brk}{q}{fragment} walrus moved today{brk}{brk}"
                                            "next paragraph")
                                    for depth in (0, 1, 2):
                                        written = Base64ReviewNineteenFindingsTest.written(text, depth)
                                        for older in (False, True):
                                            with self.subTest(kind=kind, start=start[:9], above=len(above), gap=gap,
                                                              fragment=fragment, q=q, brk=brk, depth=depth, older=older):
                                                if older:
                                                    with masking.stored_row(None):
                                                        masked = mask(written)
                                                else:
                                                    masked = mask(written)
                                                self.assertNotIn(f"{fragment} walrus", masked)
                                                key = above + [glued]
                                                self.assertEqual([line[i:i + 8] for line in key
                                                                  for i in range(0, len(line) - 8, 4)
                                                                  if line[i:i + 8] in masked], [])
                                                self.assertIn("intro notes", masked)
                                                if depth < 2 or start == "[masked]":
                                                    self.assertIn("next paragraph", masked)


class Base64KeyFooterWholeLineTest(unittest.TestCase):
    """#34 round 22, after the twenty-first review: a footer ends a key only as a whole line with no other footer after
    it in its stream; prose between a key's real footer and a stray one later in its stream, and headers and footers
    written into a key under a header that text before it on its line holds back. Made-up keys only."""
    PROSE = {"plain": b"Plan notes: deploy the harbor ledger today.\nThe walrus moved next steps.\n",
             "UTF-8": "Notes: d\u00e9ployer le registre aujourd'hui.\n\u4eca\u65e5\u306f\u5730\u56f3\u3092\u66f4\u65b0\u3057\u305f\u3002\n".encode(),
             "a heading": b"RENT\nPlan notes today.\n"}
    STRAYS = (b"-----END PGP PRIVATE KEY BLOCK-----\n", b"-----END PRIVATE KEY-----\n")

    @staticmethod
    def masked(text, older):
        from kb import mask as masking
        if older:
            with masking.stored_row(None):
                return mask(text)
        return mask(text)

    def stray_forms(self, prose):
        import base64
        import textwrap
        for name in ("openssl_p256", "gnupg_control_comment"):
            key = Base64ReviewNineteenFindingsTest.fixture(name)
            for stray in self.STRAYS:
                encoded = base64.b64encode(key.rstrip(b"\n") + b"\n" + prose + stray).decode()
                for width in (64, 76):
                    lines = textwrap.wrap(encoded, width)
                    text = "intro notes\n" + "\n".join(lines) + "\n\nnext paragraph"
                    for depth in range(3):
                        for older in (False, True):
                            yield (name, stray[9:20], width, depth, older), lines, self.masked(
                                Base64ReviewNineteenFindingsTest.written(text, depth), older)

    def test_prose_between_a_keys_footer_and_a_stray_footer_keeps_what_it_kept(self):
        # A stray footer's reading up from it took the prose above it, which decodes to text, as before this rule: so
        # plain and UTF-8 prose, every line of which went, still goes. Under a heading, the stray footer's own last line
        # ("----", "KEY BLOCK-----") stayed before, and no word of the prose: it goes now too.
        import base64
        for prose_name, prose in self.PROSE.items():
            for form, lines, masked in self.stray_forms(prose):
                with self.subTest(prose=prose_name, form=form):
                    kept = [line for line in lines if line in masked]
                    if prose_name == "a heading":
                        stray = next(s for s in self.STRAYS if s[9:20] == form[1])
                        self.assertEqual([line for line in kept if not stray.endswith(base64.b64decode(line))], [])
                    else:
                        self.assertEqual(kept, [])
                    self.assertIn("intro notes", masked)
                    if form[3] < 2:
                        self.assertIn("next paragraph", masked)

    def test_prose_holding_a_control_character_between_a_keys_footer_and_a_stray_footer_goes_with_the_key(self):
        # A listed limit (README): a footer with another after it in its stream and no header between is read as one
        # written into the key, so the key runs on to the later one, and prose between them goes with it, here also
        # prose with a control character, at which a stray footer's reading up from it stops.
        prose = b"Plan notes: deploy\x01 the ledger today.\nThe walrus moved.\n"
        for form, lines, masked in self.stray_forms(prose):
            with self.subTest(form=form):
                self.assertEqual([i for i, line in enumerate(lines) if line in masked], [])
                self.assertIn("intro notes", masked)

    FORGED = {
        "a header written into the body": ("openssl_p256", 2, b"-----BEGIN PRIVATE KEY-----"),
        "a header with spaces written into the body": ("openssl_p256", 2, b"-----BEGIN EC PRIVATE KEY----- \t"),
        "a header written into the armour comments": ("gnupg_control_comment", 1, b"-----BEGIN PGP PRIVATE KEY BLOCK-----"),
        "a footer right after the header, above a control character": (
            "gnupg_control_comment", 1, b"-----END RSA PRIVATE KEY----- \t"),
        "a footer and a header written into the comments": (
            "gnupg_control_comment", 1, b"-----END PGP SECRET KEY BLOCK-----\n-----BEGIN PRIVATE KEY-----"),
    }

    def test_a_header_or_footer_written_into_a_key_under_a_held_back_header_ends_or_starts_no_key(self):
        # Below a stream of encoded words that ends with no line break and no padding, the key's header has text before
        # it on its decoded line, so it starts no key alone, and the key starts there once its footer is read; a header
        # or footer written whole into the key between them, and a control character in an armour comment, cut it
        # short no more.
        import base64
        import textwrap
        words = base64.b64encode(b"notes " * 30).decode()
        for form_name, (name, at, piece) in self.FORGED.items():
            block = Base64ReviewNineteenFindingsTest.fixture(name).split(b"\n")
            block.insert(at, piece)
            encoded = base64.b64encode(b"\n".join(block)).decode()
            for width in (16, 32, 48, 64, 76, len(encoded)):
                lines = textwrap.wrap(encoded, width)
                for lead in ("", "intro notes\n", words + "\n"):
                    text = lead + "\n".join(lines) + "\n\nnext paragraph"
                    for depth in range(3):
                        for older in (False, True):
                            with self.subTest(form=form_name, width=width, lead=lead[:5], depth=depth, older=older):
                                masked = self.masked(Base64ReviewNineteenFindingsTest.written(text, depth), older)
                                self.assertEqual([i for i, line in enumerate(lines) if line in masked], [])
                                if lead:
                                    self.assertIn(lead[:11], masked)
                                if depth < 2:
                                    self.assertIn("next paragraph", masked)

    IN_STREAM = {
        "no header or footer written into it": ("gnupg_control_comment", 1, None),
        "a header written after an armour comment": ("gnupg_control_comment", 2, b"-----BEGIN PRIVATE KEY-----"),
        "a header written after a header quoted in a sentence": (
            "openssl_p256", 1, b"x -----BEGIN OPENSSH PRIVATE KEY-----\n-----BEGIN PGP PRIVATE KEY BLOCK-----"),
        "an encrypted key's field, then a header": (
            "openssl_p256", 1, b"Proc-Type: 4,ENCRYPTED\n-----BEGIN EC PRIVATE KEY-----"),
    }

    def test_a_header_written_into_a_key_whose_header_follows_text_in_its_stream_starts_no_key(self):
        # The key's header follows words on its decoded line inside one stream, where a line of the block starts or
        # inside one, so it starts no key alone, as a header quoted in a sentence does. With its footer after it in its
        # stream, the key starts there, whatever its armour comments hold. A header written whole into the key below
        # it, with no prose between them (a line of its body, an armour header's line, another header's text, a
        # control character, or nothing), starts no other key: the key starts at the first. With prose between them,
        # as a document quoting a header above a key file has, the later header starts its own key
        # (Base64ReviewSeventeenTest).
        import base64
        import textwrap
        for form_name, (name, at, piece) in {**self.FORGED, **self.IN_STREAM}.items():
            block = Base64ReviewNineteenFindingsTest.fixture(name).split(b"\n")
            if name == "gnupg_control_comment" and at > 1:  # past its comment, with no control character in it
                block = [line.replace(b"\x01", b"-") for line in block]
            if piece:
                block.insert(at, piece)
            for notes in (b"notes " * 8, b"notes " * 8 + b"x", b"notes " * 8 + b"xy"):
                data = notes + b"\n".join(block)
                encoded = base64.b64encode(data).decode()
                for width in (16, 21, 32, 48, 64, 76):
                    lines = textwrap.wrap(encoded, width)
                    # by their bits: a line can end inside the base64 group that holds the header's first byte
                    ahead = [line for j, line in enumerate(lines) if (j + 1) * width * 6 <= len(notes) * 8]
                    key = [i for i, line in enumerate(lines) if (i + 1) * width * 6 > len(notes) * 8]
                    text = "\n".join(lines) + "\n\nnext paragraph"
                    for depth in range(3):
                        for older in (False, True):
                            with self.subTest(form=form_name, lead=len(notes), width=width, depth=depth, older=older):
                                masked = self.masked(Base64ReviewNineteenFindingsTest.written(text, depth), older)
                                self.assertEqual([i for i in key if lines[i] in masked], [])
                                if depth == 0:
                                    self.assertEqual([line for line in ahead if line not in masked], [])
                                if depth < 2:
                                    self.assertIn("next paragraph", masked)

    def pinned(self, data, at):
        """Each width's lines of `data` encoded as one stream, and the lines that hold bits from byte `at` on."""
        import base64
        import textwrap
        encoded = base64.b64encode(data).decode()
        for width in (16, 21, 32, 64, 76):
            lines = textwrap.wrap(encoded, width)
            ahead = [line for j, line in enumerate(lines) if (j + 1) * width * 6 <= at * 8]
            yield width, lines, ahead, [i for i, line in enumerate(lines) if (i + 1) * width * 6 > at * 8]

    def test_text_between_a_header_quoted_after_words_and_a_footer_in_its_stream_goes_with_the_key(self):
        # A listed limit (README): a header after text on its decoded line, with a footer later in its stream, starts
        # that key on its line, whatever lies between them; here a line of prose with a control character, at which
        # the footer's own reading up stops.
        key = Base64ReviewNineteenFindingsTest.fixture("openssl_p256").split(b"\n")
        for lead in (b"notes " * 8 + b"see ", b"notes " * 8 + b"x"):
            data = lead + key[0] + b"\nPlan notes:\x01 deploy the ledger today.\n" + b"\n".join(key[1:])
            for width, lines, ahead, held in self.pinned(data, len(lead)):
                text = "\n".join(lines) + "\n\nnext paragraph"
                for depth in range(3):
                    for older in (False, True):
                        with self.subTest(lead=len(lead), width=width, depth=depth, older=older):
                            masked = self.masked(Base64ReviewNineteenFindingsTest.written(text, depth), older)
                            self.assertEqual([i for i in held if lines[i] in masked], [])
                            if depth == 0:
                                self.assertEqual([line for line in ahead if line not in masked], [])

    def test_a_document_quoting_a_header_above_a_key_file_with_no_prose_between_goes_with_the_key(self):
        # A listed limit (README): below a header after text on its decoded line, a key file's header with no prose
        # between them (here an armour header's line) starts no key of its own, so the key starts on the quote's line,
        # with the words before the quote. With prose between them the document stays, as round 17's does.
        key = Base64ReviewNineteenFindingsTest.fixture("openssl_p256")
        lead = b"notes " * 8 + b"see "
        quote = b"-----BEGIN PRIVATE KEY-----\n"
        for between, goes in ((b"Note: paste it below.\n", True), (b"Read the documentation before importing.\n", False)):
            data = lead + quote + between + key
            for width, lines, ahead, held in self.pinned(data, len(lead)):
                document = [i for i, line in enumerate(lines) if (i + 1) * width * 6 <= (len(data) - len(key)) * 8]
                text = "\n".join(lines) + "\n\nnext paragraph"
                for depth in range(3):
                    for older in (False, True):
                        with self.subTest(between=between[:4], width=width, depth=depth, older=older):
                            masked = self.masked(Base64ReviewNineteenFindingsTest.written(text, depth), older)
                            if goes:
                                self.assertEqual([i for i in held if lines[i] in masked], [])
                            else:
                                # the key's lines from its own header go, and the document's whole lines stay
                                self.assertEqual([i for i in held if i not in document and lines[i] in masked], [])
                                if depth == 0:
                                    self.assertEqual([i for i in document if lines[i] not in masked], [])
                            if depth == 0:
                                self.assertEqual([line for line in ahead if line not in masked], [])

    def test_a_header_after_a_word_written_below_a_held_one_takes_no_place(self):
        # The key's header follows words on its decoded line, so it is held. A header written on the next line after a
        # word ("see -----BEGIN ...") is held back too, and where it started a line of the block it took the first's
        # place past that word, so the key started there and the line above showed. Only one that starts a stream of
        # its own, by the widths or after a byte order mark, takes a held header's place
        # (Base64ReviewEighteenFindingsTest, a key file under a document that runs on into it).
        import base64
        import textwrap
        key = Base64ReviewNineteenFindingsTest.fixture("openssl_p256").split(b"\n")
        for lead in (b"see the key: ", b"Notes: the harbor ledger for today. ", b"x"):
            for piece in (b"see -----BEGIN EC PRIVATE KEY-----", b"x -----BEGIN PGP SECRET KEY BLOCK----- "):
                data = lead + b"\n".join([key[0], piece] + key[1:])
                encoded = base64.b64encode(data).decode()
                for width in range(16, 77):
                    lines = textwrap.wrap(encoded, width)
                    ahead = [line for j, line in enumerate(lines) if (j + 1) * width * 6 <= len(lead) * 8]
                    held = [i for i, line in enumerate(lines) if (i + 1) * width * 6 > len(lead) * 8]
                    text = "\n".join(lines) + "\n\nnext paragraph"
                    for depth in range(3):
                        for older in (False, True):
                            with self.subTest(lead=lead[:6], piece=piece[:2], width=width, depth=depth, older=older):
                                masked = self.masked(Base64ReviewNineteenFindingsTest.written(text, depth), older)
                                self.assertEqual([i for i in held if lines[i] in masked], [])
                                if depth == 0:
                                    self.assertEqual([line for line in ahead if line not in masked], [])

    def test_many_headers_after_words_below_one_held_back_cost_time_in_proportion(self):
        # Each header after words that starts a line of the block, below a held one, asks what lies between them (here
        # a line a key's body could be, so none takes its place); those lines are read once, so four times the text
        # takes about four times as long.
        import base64
        import textwrap
        import time
        unit = b"-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSj\nnotes notes notes notes not"
        took = {}
        for n in (200, 800):
            data = b"notes " * 8 + unit * n + b"\n-----END PRIVATE KEY-----\n"
            text = "\n".join(textwrap.wrap(base64.b64encode(data).decode(), 64)) + "\n\nnext paragraph"
            started = time.process_time()
            masked = mask(text)
            took[n] = time.process_time() - started
            self.assertIn("next paragraph", masked)
        self.assertLess(took[800], 6 * max(took[200], 0.05), took)

    def test_a_key_starts_at_the_line_with_its_headers_first_bits(self):
        # Where a line of the block is no whole number of base64 groups, the line before a key's header can end inside
        # the group that holds the header's first byte, with only the bits of the bytes before it: it stays.
        import base64
        import textwrap
        key = Base64ReviewNineteenFindingsTest.fixture("openssl_p256")
        for lead in (b"see the key: ", b"Notes for today.\n", b"Plan: deploy the harbor ledger today.\n", b"x", b"xy"):
            data = lead + key
            encoded = base64.b64encode(data).decode()
            for width in range(16, 77):
                lines = textwrap.wrap(encoded, width)
                ahead = [line for j, line in enumerate(lines) if (j + 1) * width * 6 <= len(lead) * 8]
                held = [i for i, line in enumerate(lines) if (i + 1) * width * 6 > len(lead) * 8]
                text = "intro notes\n" + "\n".join(lines) + "\n\nnext paragraph"
                for depth in range(3):
                    for older in (False, True):
                        with self.subTest(lead=lead[:8], width=width, depth=depth, older=older):
                            masked = self.masked(Base64ReviewNineteenFindingsTest.written(text, depth), older)
                            self.assertEqual([i for i in held if lines[i] in masked], [])
                            if depth == 0:
                                self.assertEqual([line for line in ahead if line not in masked], [])

    def test_a_control_character_in_the_group_before_a_footer_cuts_its_key_short_no_more(self):
        # A footer's own reading starts with the base64 group that holds its first byte, which can hold the end of the
        # line above it: a control character there is no part of what follows the footer.
        import base64
        import textwrap
        words = base64.b64encode(b"notes " * 30).decode()
        key = Base64ReviewNineteenFindingsTest.fixture("openssl_p256").rstrip(b"\n").split(b"\n")
        for pad in range(3):
            block = key[:-1] + [b"x" * pad + b"\x01", b"-----END PGP SECRET KEY BLOCK-----", key[-1]]
            encoded = base64.b64encode(b"\n".join(block) + b"\n").decode()
            for width in (16, 28, 32, 64, 76):
                lines = textwrap.wrap(encoded, width)
                for lead in ("", words + "\n"):
                    text = lead + "\n".join(lines) + "\n\nnext paragraph"
                    for depth in range(3):
                        for older in (False, True):
                            with self.subTest(pad=pad, width=width, lead=lead[:5], depth=depth, older=older):
                                masked = self.masked(Base64ReviewNineteenFindingsTest.written(text, depth), older)
                                self.assertEqual([i for i, line in enumerate(lines) if line in masked], [])


    def test_text_between_two_whole_keys_in_one_encoded_stream_goes_with_them(self):
        # A listed limit (README): a footer with another footer later in its stream reads as one written into the key,
        # so a key runs on to the last, and text between two whole keys in one stream goes with them. Before, the first
        # key ended at its own footer, and where the second's header shared its last line, a line of it showed.
        import base64
        import textwrap
        first = Base64ReviewNineteenFindingsTest.fixture("openssl_p256").rstrip(b"\n") + b"\n"
        second = Base64ReviewNineteenFindingsTest.fixture("gnupg_control_comment")
        for prose in (b"Plan notes: deploy the harbor ledger today.\nThe walrus moved next steps.\n",
                      b"Plan\x01 notes today.\n", b""):
            encoded = base64.b64encode(first + prose + second).decode()
            for width in (16, 32, 64, 76):
                lines = textwrap.wrap(encoded, width)
                text = "intro notes\n" + "\n".join(lines) + "\n\nnext paragraph"
                for depth in range(3):
                    for older in (False, True):
                        with self.subTest(prose=prose[:9], width=width, depth=depth, older=older):
                            masked = self.masked(Base64ReviewNineteenFindingsTest.written(text, depth), older)
                            self.assertEqual([i for i, line in enumerate(lines) if line in masked], [])
                            self.assertIn("intro notes", masked)

    def test_a_key_whose_header_shares_the_line_where_the_key_above_ends_is_masked(self):
        # Each key read is passed over whole, but a header past its end on its last line starts its own: a second key
        # cut before its footer, after a whole one, showed a line where its header shared the first's last line.
        import base64
        import textwrap
        first = Base64ReviewNineteenFindingsTest.fixture("openssl_p256").rstrip(b"\n") + b"\n"
        second = Base64ReviewNineteenFindingsTest.fixture("openssl_ed25519").rstrip(b"\n").split(b"\n")
        for gap in (b"", b"ok.\n", b"ok\x01.\n"):
            data = first + gap + b"\n".join(second[:-1]) + b"\n"
            start = len(first + gap)
            encoded = base64.b64encode(data).decode()
            for width in range(16, 80, 4):
                lines = textwrap.wrap(encoded, width)
                text = "intro notes\n" + "\n".join(lines) + "\n\nnext paragraph"
                for depth in range(3):
                    for older in (False, True):
                        with self.subTest(gap=gap, width=width, depth=depth, older=older):
                            masked = self.masked(Base64ReviewNineteenFindingsTest.written(text, depth), older)
                            self.assertEqual([i for i, line in enumerate(lines)
                                              if (i * width * 6) // 8 >= start and line in masked], [])

    def test_the_line_after_a_keys_footer_that_holds_none_of_its_bits_stays(self):
        # Where a line is no whole number of base64 groups, the group that holds a footer's last byte can end on the
        # next line, which then holds bits of the text after the key alone: it stays, in a row read fresh.
        import base64
        import textwrap
        for lead in (b"", b"a\n", b"ab\n"):  # each place in its group the footer's last byte can take
            key = lead + Base64ReviewNineteenFindingsTest.fixture("openssl_p256").rstrip(b"\n") + b"\n"
            self.check_bits_after(key, b"Next steps: move the walrus.\n")

    def check_bits_after(self, key, after):
        import base64
        import textwrap
        encoded = base64.b64encode(key + after).decode()
        end = len(key) - 1  # the footer's line break
        for width in range(16, 77):
            lines = textwrap.wrap(encoded, width)
            text = "intro notes\n" + "\n".join(lines) + "\n\nnext paragraph"
            for depth in range(2):
                with self.subTest(lead=key[:2], width=width, depth=depth):
                    masked = self.masked(Base64ReviewNineteenFindingsTest.written(text, depth), False)
                    self.assertEqual([i for i, line in enumerate(lines)
                                      if -(-((i + 1) * width * 6) // 8) <= end and line in masked], [])
                    self.assertEqual([i for i, line in enumerate(lines)
                                      if (i * width * 6) // 8 >= end and line.strip("=") and line not in masked], [])

    def test_in_an_older_row_encoded_text_after_a_key_in_its_stream_goes_as_a_keys_rest(self):
        # A listed limit (README): masked text masks to itself, so the passes read it again, and in a row stored by a
        # kb before keys were masked whole, base64 after a placeholder is an older kb's rest of a key unless it decodes
        # as public material: the key's own placeholder is read so too, and encoded text after the key in its stream
        # goes. Read fresh, it stays.
        import base64
        import textwrap
        key = Base64ReviewNineteenFindingsTest.fixture("openssl_p256").rstrip(b"\n") + b"\n"
        data = key + b"Plan notes for the harbor deploy.\n"
        encoded = base64.b64encode(data).decode()
        # widths where the text's line after the key, a line of base64 after the key's placeholder, does not decode
        # from its start to words (its first group holds the key's last bits, or it is short): a line that does stays
        for width in (46, 47, 55, 65, 66):
            lines = textwrap.wrap(encoded, width)
            text = "intro notes\n" + "\n".join(lines) + "\n\nnext paragraph"
            after = [line for i, line in enumerate(lines) if (i * width * 6) // 8 >= len(key) and line.strip("=")]
            self.assertTrue(after)
            for older in (False, True):
                with self.subTest(width=width, older=older):
                    masked = self.masked(text, older)
                    if older:
                        self.assertEqual([line for line in after if line in masked], [])
                    else:
                        self.assertEqual([line for line in after if line in masked], after)
                    self.assertIn("next paragraph", masked)

class KeyRestPastWordsWholeLineTest(unittest.TestCase):
    """#34 round 22's audit of the key readers' line recognizers. Past a line of base64 right after a placeholder that
    decodes to words, where main's reading takes a key's rest on, a word alone, a placeholder alone or a line of words
    was passed over as read by its start: up to an escape that is no line break (an escaped tab), a quote, a "<" or a
    "…", or by its first two words, so a key's line or fragment later on it showed, which main hides; as did the rest of
    the line of words itself past such a quote or escape. Each is read whole now. Made-up keys only."""

    @staticmethod
    def parts():
        import base64
        import random
        import textwrap
        body = textwrap.wrap(base64.b64encode(made_up_private_key(random.Random(67))).decode(), 64)
        words = base64.b64encode(b"open the harbor door and walk the ledger today, notes for the walrus").decode()
        return body, words

    def test_a_line_past_encoded_words_with_a_keys_run_later_on_it_is_masked(self):
        from kb import mask as masking
        body, words = self.parts()
        key = body[2]
        forms = {
            "a word alone, a tab": "\nThanks\t" + key, "a word alone, a quote": "\nThanks' " + key,
            "a word alone, a <": "\nThanks<" + key, "a word alone, an ellipsis": "\nThanks\u2026 " + key,
            "a line of words": "\nnext steps " + key, "a line of words, a fragment": "\nnext steps " + key[:20],
            "a placeholder alone, a tab": "\n[masked]\t" + key, "a placeholder alone, a quote": "\n[masked]' " + key[:30],
            "the words line, a quote": "' " + key[:20], "the words line, an escape": "\\x " + key[:20],
            "two words lines, then a line of words": "\n" + words + "\nnext steps " + key[:20],
        }
        for name, form in forms.items():
            text = "[masked]\n" + words + form + "\n\nnext paragraph"
            for depth in range(3):
                written = Base64ReviewNineteenFindingsTest.written(text, depth)
                for older in (False, True):
                    with self.subTest(form=name, depth=depth, older=older):
                        if older:
                            with masking.stored_row(None):
                                masked = mask(written)
                        else:
                            masked = mask(written)
                        self.assertEqual([key[i:i + 16] for i in range(0, len(key) - 15, 4) if key[i:i + 16] in masked], [])
                        if depth < 2:
                            self.assertIn("next paragraph", masked)

    def test_a_word_alone_or_a_line_of_words_past_encoded_words_still_stays(self):
        # Guard: a line with no run a key's could be is still passed over, and stays.
        from kb import mask as masking
        _, words = self.parts()
        for name, form in {"a word alone": "\nThanks", "a line of words": "\nnext steps for the walrus",
                           "a word alone, a quote": "\nThanks' said the walrus"}.items():
            text = "[masked]\n" + words + form + "\n\nnext paragraph"
            for depth in range(2):
                written = Base64ReviewNineteenFindingsTest.written(text, depth)
                for older in (False, True):
                    with self.subTest(form=name, depth=depth, older=older):
                        if older:
                            with masking.stored_row(None):
                                masked = mask(written)
                        else:
                            masked = mask(written)
                        self.assertIn(form.strip("\n").split("'")[0], masked)
                        self.assertIn(words, masked)


class Base64ReviewTwentySecondTest(unittest.TestCase):
    """#34's twenty-second review. A key's fragment split by an invisible character, written as an escape ("\\u200b"),
    on a line past encoded words after a placeholder, was read as two ordinary halves, the escape taken for a space, so
    the line was passed over and the fragment showed, which main masks. A footer in a later stream, encoded on its own,
    let a header with no footer in its own stream read on through every byte to it, so a public key between them was
    masked, which main keeps. And checking found a key with no footer, encoded alone below a shorter stream of encoded
    words, read as a sentence quoting its header, so all its lines showed. Made-up keys only."""

    @staticmethod
    def masks(written, older):
        from kb import mask as masking
        if older:
            with masking.stored_row(None):
                return mask(written)
        return mask(written)

    def test_a_keys_fragment_split_by_an_escaped_invisible_character_past_encoded_words_is_masked(self):
        body, words = KeyRestPastWordsWholeLineTest.parts()
        fragments = {"a key's line": body[2][:20], "the review's": "ABCDEFGHabcdefgh"}
        for name, fragment in fragments.items():
            half = len(fragment) // 2
            for unseen in ("\u200b", "\u2060", "\ufeff", "\u00ad", "\u200d"):
                for form in ("\nnext steps {}", "\n{}", "\nThanks\t{}"):
                    # written as the character itself, escaped once and twice by the JSON around it, and as an escape
                    # the text itself holds
                    split = fragment[:half] + unseen + fragment[half:]
                    texts = {depth: Base64ReviewNineteenFindingsTest.written(
                        "[masked]\n" + words + form.format(split) + "\n\nnext paragraph", depth) for depth in range(3)}
                    texts["as written"] = "[masked]\n" + words + form.format(
                        fragment[:half] + "\\u%04x" % ord(unseen) + fragment[half:]) + "\n\nnext paragraph"
                    for depth, written in texts.items():
                        for older in (False, True):
                            with self.subTest(fragment=name, unseen=hex(ord(unseen)), form=form[1:7], depth=depth,
                                              older=older):
                                masked = self.masks(written, older)
                                self.assertEqual([piece for piece in (fragment[:half], fragment[half:])
                                                  if piece in masked], [])
                                if depth != 2:
                                    self.assertIn("next paragraph", masked)

    def test_a_footer_in_a_later_stream_carries_no_header_through_a_public_key(self):
        import base64
        import random
        r = random.Random(2201)
        encoded = Base64ReviewSeventeenTest.encoded
        publics = {"ed25519": bytes.fromhex("302a300506032b6570032100") + bytes(r.randrange(256) for _ in range(32)),
                   "rsa": made_up_public_key(r)}
        line = base64.b64encode(bytes(r.randrange(256) for _ in range(48)))
        for width in (64, 76):
            for lines in (1, 2):
                cut = encoded(b"-----BEGIN PRIVATE KEY-----\n" + (line + b"\n") * lines, width)
                for footer in (b"-----END PRIVATE KEY-----\n", b"\n-----END PRIVATE KEY-----\n",
                               b"notes\n-----END PRIVATE KEY-----\n"):
                    for name, public in publics.items():
                        kept = encoded(public, width)
                        text = "\n".join([cut, kept, encoded(footer, width)]) + "\n\nnext paragraph"
                        for depth in range(3):
                            written = Base64ReviewNineteenFindingsTest.written(text, depth)
                            with self.subTest(width=width, lines=lines, footer=footer[:6], public=name, depth=depth):
                                masked = mask(written)
                                self.assertEqual([k for k in kept.splitlines() if k not in masked], [])
                                self.assertEqual([k for k in cut.splitlines() if len(k) >= 16 and k in masked], [])

    def test_a_key_with_no_footer_below_a_shorter_stream_of_encoded_words_is_masked(self):
        import base64
        import random
        r = random.Random(2202)
        encoded = Base64ReviewSeventeenTest.encoded
        line = base64.b64encode(bytes(r.randrange(256) for _ in range(48)))
        for width in (64, 72, 76):
            for said in (b"notes " * 3, b"notes " * 5, b"notes " * 7, b"notes " * 11, b"see the runbook for the harbor"):
                words = encoded(said, width)
                for lines in (1, 2, 3):
                    cut = encoded(b"-----BEGIN PRIVATE KEY-----\n" + (line + b"\n") * lines, width)
                    for brk in ("\n", "\r\n"):
                        text = (words + "\n" + cut).replace("\n", brk) + brk + brk + "next paragraph"
                        for depth in range(3):
                            written = Base64ReviewNineteenFindingsTest.written(text, depth)
                            for older in (False, True):
                                with self.subTest(width=width, said=said[:12], lines=lines, brk=brk, depth=depth,
                                                  older=older):
                                    masked = self.masks(written, older)
                                    self.assertEqual([k for k in cut.splitlines() if len(k) >= 16 and k in masked], [])
                                    self.assertEqual([k for k in words.splitlines() if k not in masked], [])

    def test_in_an_older_row_a_keys_run_past_encoded_words_goes_where_mains_rest_takes_it(self):
        # Found by round 23's generator. Past "[masked]" glued to a line of encoded words, a stream's short last line
        # (34 characters and "==", or 35 and "=") was not read as words, as only 36 before the padding were, so it
        # started a rest of its own; and a rest that starts there, or at a key's line, took none of the run on the next
        # line, which main's rest from the placeholder takes. (On its own line, main reads no rest there, and the run
        # shows on both: Base64KeyPieceLimitTest.)
        import base64
        import random
        import textwrap
        r = random.Random(2203)
        key_line = base64.b64encode(made_up_private_key(r)).decode()[64:128]
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        run = "".join(r.choice(alphabet) for _ in range(26)) + "7Qa"
        for size in (25, 26):
            words = textwrap.wrap(base64.b64encode((b"notes for the harbor ledger today " * 3)[:48 + size]).decode(), 64)
            for name, lines in (("a short last line of words", words), ("a key's line", words[:1] + [key_line])):
                for brk in ("\n", "\r\n"):
                    text = "[masked]" + brk.join(lines) + brk + "next steps " + run + brk + brk + "next paragraph"
                    for depth in range(2):
                        written = Base64ReviewNineteenFindingsTest.written(text, depth)
                        with self.subTest(size=size, between=name, brk=brk, depth=depth):
                            masked = self.masks(written, True)
                            self.assertEqual([piece for piece in (run[:12], run[12:]) if piece in masked], [])
                            self.assertIn("next paragraph", masked)

    def test_a_blank_line_escaped_in_json_ends_a_keys_paragraph_as_one_written_plain_does(self):
        # Found by round 23's generator: in JSON, after a line of a key's base64 that ends with its padding, a blank
        # line escaped ("\\n\\n") began the next run at the first escape's letter, so the block read on past it as
        # past one line break, and a key with no footer above took the encoded words of the next paragraph, which a
        # blank line written plain keeps. Main keeps them.
        import base64
        import random
        r = random.Random(2204)
        encoded = Base64ReviewSeventeenTest.encoded
        line = base64.b64encode(bytes(r.randrange(256) for _ in range(48)))
        words = encoded(b"notes walrus notes moved ledger plan the plan harbor notes approved today notes today")
        tops = {"a header alone": encoded(b"-----BEGIN PRIVATE KEY-----\n"),
                "a key with no footer": encoded(b"-----BEGIN PRIVATE KEY-----\n" + line + b"\n"),
                "a key with no footer, unpadded": encoded(b"-----BEGIN PRIVATE KEY-----\n" + line + b"\n" + line[:7] + b"\n")}
        for name, top in tops.items():
            for blank in ("\n\n", "\r\n\r\n", "\n \n", "\n\t\n"):
                text = top + blank + words + "\n\nnext paragraph"
                for depth in range(2):
                    written = Base64ReviewNineteenFindingsTest.written(text, depth)
                    with self.subTest(top=name, blank=blank, depth=depth):
                        masked = mask(written)
                        self.assertEqual([k for k in words.splitlines() if k not in masked], [])
                        self.assertEqual([k for k in top.splitlines()[1:] if len(k) >= 16 and k in masked], [])

    def test_a_narrower_line_of_its_own_ends_its_stream_above_a_public_key_that_fills_whole_lines(self):
        # Found by round 23's cross-stream generator: a footer encoded alone on one line of its own, narrower than the
        # lines below it, was read as the first line of the stream below, a public key whose lines fill the width (no
        # short last line, no "=") and a whole key after it, so with that key's footer later in its stream, the lone
        # footer's key ran on to it through the public key. Main keeps the public key.
        import base64
        import random
        r = random.Random(2205)
        encoded = Base64ReviewSeventeenTest.encoded
        line = base64.b64encode(bytes(r.randrange(256) for _ in range(48)))
        publics = {"certificate": (made_up_certificate(r), (52, 72)), "rsa": (made_up_public_key(r), (49, 56))}
        for name, (public, widths) in publics.items():
            for width in widths:
                kept = encoded(public, width)
                whole = encoded(b"-----BEGIN PRIVATE KEY-----\n" + line + b"\n" + line + b"\n-----END PRIVATE KEY-----\n",
                                width)
                for footer in (b"\n-----END PRIVATE KEY-----\n", b"-----END RSA PRIVATE KEY-----\n"):
                    # at the block's start, below a line of words, and below a stream that ends with its "="
                    for above in ("", "Notes from today:\n", encoded(b"notes on the harbor ledger", width) + "\n"):
                        text = above + "\n".join([encoded(footer, width), kept, whole]) + "\n\nnext paragraph"
                        for brk in ("\n", "\r\n"):
                            for depth in range(3):
                                written = Base64ReviewNineteenFindingsTest.written(text.replace("\n", brk), depth)
                                with self.subTest(public=name, width=width, footer=footer[:6], above=above[:6],
                                                  brk=brk, depth=depth):
                                    masked = mask(written)
                                    self.assertEqual([k for k in kept.splitlines() if k not in masked], [])
                                    self.assertEqual([k for k in whole.splitlines()[1:-1] if k in masked], [])


class Base64ReviewTwentyThirdTest(unittest.TestCase):
    """#34's twenty-third review. Whether a block's first line starts its line was read only 64 characters back, so past
    longer indentation a footer encoded alone on a narrower line read as one stream with a public key below it and a
    whole key after that, and the public key was masked, which main keeps. And many key rests on one long line, each
    stopped early with a token of its own to end at, each read on to that line's end, which took time in its square.
    Made-up keys only."""

    def test_a_narrower_line_of_its_own_ends_its_stream_past_any_indentation(self):
        import base64
        import random
        r = random.Random(2301)
        encoded = Base64ReviewSeventeenTest.encoded
        line = base64.b64encode(bytes(r.randrange(256) for _ in range(48)))
        publics = {"certificate": (made_up_certificate(r), 52), "rsa": (made_up_public_key(r), 56)}
        for name, (public, width) in publics.items():
            kept = encoded(public, width)
            whole = encoded(b"-----BEGIN PRIVATE KEY-----\n" + line + b"\n" + line + b"\n-----END PRIVATE KEY-----\n",
                            width)
            for indent in (" " * 64, " " * 65, " " * 200, "\t" * 70, "Notes from today:\n" + " \t" * 60):
                text = indent + "\n".join([encoded(b"\n-----END PRIVATE KEY-----\n", width), kept, whole]) + (
                    "\n\nnext paragraph")
                for brk in ("\n", "\r\n"):
                    for depth in range(3):
                        written = Base64ReviewNineteenFindingsTest.written(text.replace("\n", brk), depth)
                        with self.subTest(public=name, indent=repr(indent[-3:]), size=len(indent), brk=brk,
                                          depth=depth):
                            masked = mask(written)
                            self.assertEqual([k for k in kept.splitlines() if k not in masked], [])
                            self.assertEqual([k for k in whole.splitlines()[1:-1] if k in masked], [])

    def test_many_rests_on_one_long_line_each_with_a_token_of_its_own_cost_time_in_proportion(self):
        # The review's repro: single-line JSON objects, each with a key's rest that stops at the next field and a
        # token of its own after it, many on one line.
        import time
        from kb import mask as masking
        unit = '{"k":"[masked] MIIE' + "A" * 36 + '","n":"AbCdEfGhAbCdEfGh-----BEGIN CERTIFICATE-----"}'
        for older in (False, True):
            took = {}
            for n in (250, 2000):
                text = ",".join([unit] * n)
                started = time.process_time()
                if older:
                    with masking.stored_row(None):
                        mask(text)
                else:
                    mask(text)
                took[n] = time.process_time() - started
            with self.subTest(older=older):
                # eight times the text
                self.assertLess(took[2000], 24 * max(took[250], 0.05), took)

class Base64ReviewTwentyFourthTest(unittest.TestCase):
    """#34's twenty-fourth review and the audit after it, of each window and each list of characters in the key
    readers. A footer was read as a line of its own only within 40 characters past its word, so eight spaces or more
    before its line break hid it and a key whose armour comment holds a control byte showed from there; a line's lead
    was read as spaces, tabs and quotes, plain or as "\\t", so a JSON string that starts with "\\u0020" read its first
    line as cut, and a footer encoded alone on a narrower line ran on through a public key below it. The audit: a header
    or footer was read only right after a written line break, so a key file indented in a document, or in a JSON or .env
    file, encoded whole, showed; a block's lines were joined past spaces and tabs only, so a key whose lines are indented
    with no-break spaces showed; a backtick ended no line and led none; and SSH2's frames were not read. Made-up keys
    only."""

    @staticmethod
    def lines_within(data, width, start, end):
        """The lines of `data`'s base64 wrapped at `width` whose bits all come from data[start:end]."""
        import base64
        encoded = base64.b64encode(data).decode()
        # character i holds bits 6i to 6i + 5: of bytes 6i // 8 to (6i + 5) // 8
        return [encoded[at:at + width] for at in range(0, len(encoded), width)
                if start <= 6 * at // 8 and (6 * min(at + width, len(encoded)) - 1) // 8 < end]

    @staticmethod
    def in_key_spans(text, lines):
        """Which of `lines` lie inside a key's span (_encoded_key_spans) in `text`: what follows a key in its paragraph
        may be masked with it (a placeholder's rest, the granted limit), so whether a key runs on is read there. Each
        line read once in the text, and 16 characters or more, so where it lies is known."""
        from kb import mask as masking
        spans = masking._encoded_key_spans(text)
        return [line for line in lines if len(line) >= 16 and text.count(line) == 1
                and any(a < text.find(line) + len(line) and text.find(line) < b for a, b in spans)]

    @staticmethod
    def pem(r):
        return (b"-----BEGIN PRIVATE KEY-----\n" + Base64ReviewSeventeenTest.encoded(made_up_private_key(r)).encode()
                + b"\n-----END PRIVATE KEY-----\n")

    @staticmethod
    def certificate(r):
        return (b"-----BEGIN CERTIFICATE-----\n" + Base64ReviewSeventeenTest.encoded(made_up_certificate(r)).encode()
                + b"\n-----END CERTIFICATE-----\n")

    def test_a_footer_with_any_run_of_space_before_its_line_break_ends_its_key(self):
        import random
        r = random.Random(2401)
        keys = {"gnupg": Base64ReviewNineteenFindingsTest.fixture("gnupg_control_comment"), "pem": self.pem(r)}
        certificate = self.certificate(r)
        forms = {"space": b" ", "tab": b"\t", "escaped tab": b"\\t", "escaped space": b"\\u0020",
                 "no-break space": " ".encode(), "Latin-1 no-break space": b"\xa0",
                 "ideographic space": "　".encode(), "mixed": " \t  ".encode()}
        written, encoded, i = Base64ReviewNineteenFindingsTest.written, Base64ReviewSeventeenTest.encoded, 0
        for name, key in keys.items():
            for size in (8, 40, 64, 200):
                for form, space in forms.items():
                    for brk in (b"\n", b"\r\n"):
                        file = key.replace(b"\n", brk)
                        end = file.rindex(b"-----") + 5
                        public = certificate.replace(b"\n", brk)
                        data = file[:end] + space * size + file[end:] + public
                        for width in (64, 76):
                            depth, i = i % 3, i + 1
                            with self.subTest(key=name, size=size, form=form, brk=brk, width=width, depth=depth):
                                text = written(encoded(data, width) + "\n\nnext paragraph", depth)
                                masked = mask(text)
                                self.assertEqual([k for k in self.lines_within(data, width, 0, end) if k in masked], [])
                                self.assertEqual(self.in_key_spans(text, self.lines_within(
                                    data, width, len(data) - len(public) + 4, len(data))), [])
                                self.assertIn("next paragraph", masked)

    def test_a_lines_lead_of_any_space_or_quote_written_or_escaped_starts_its_line(self):
        import base64
        import random
        r = random.Random(2402)
        encoded = Base64ReviewSeventeenTest.encoded
        line = base64.b64encode(bytes(r.randrange(256) for _ in range(48)))
        publics = {"certificate": (made_up_certificate(r), 52), "rsa": (made_up_public_key(r), 56)}
        escapes = ["\\u0020", "\\u0020" * 4, "\\u0020" * 11, "\\u0009", "\\u0009" * 12, "\\u0022", "\\u0027",
                   "\\u0060", "\\u00a0", "\\u00A0", "\\u3000", "\\u2003\\u0020", "\\u001f", "\\\\u0020", "\\t\\u0020\\\""]
        written = []
        for name, (public, width) in publics.items():
            kept = encoded(public, width)
            whole = encoded(b"-----BEGIN PRIVATE KEY-----\n" + line + b"\n" + line + b"\n-----END PRIVATE KEY-----\n",
                            width)
            body = "\n".join([encoded(b"\n-----END PRIVATE KEY-----\n", width), kept, whole]) + "\n\nnext paragraph"
            for lead in escapes:  # at the start of a JSON string, and of JSON in JSON
                once = '"' + lead + json.dumps(body)[1:]
                written += [(name, kept, whole, lead, 1, once), (name, kept, whole, lead, 2, json.dumps(once))]
            for lead in (" ", " " * 70, "　", "  \t", "\x1f", "`", "   "):
                for depth in range(3):
                    text = Base64ReviewNineteenFindingsTest.written(lead + body, depth)
                    written.append((name, kept, whole, lead, depth, text))
        for name, kept, whole, lead, depth, text in written:
            with self.subTest(public=name, lead=lead, depth=depth):
                masked = mask(text)
                self.assertEqual([k for k in kept.splitlines() if k not in masked], [])
                self.assertEqual([k for k in whole.splitlines()[1:-1] if k in masked], [])

    def test_a_key_file_indented_in_a_document_and_encoded_whole_is_masked(self):
        import random
        r = random.Random(2403)
        keys = {"pem": self.pem(r), "gnupg": Base64ReviewNineteenFindingsTest.fixture("gnupg_control_comment")}
        indents = {"2 spaces": b"  ", "4 spaces": b"    ", "tab": b"\t", "20 spaces": b" " * 20,
                   "no-break spaces": "  ".encode(), "Latin-1 no-break spaces": b"\xa0\xa0",
                   "ideographic space": "　".encode()}
        written, encoded, i = Base64ReviewNineteenFindingsTest.written, Base64ReviewSeventeenTest.encoded, 0
        for name, key in keys.items():
            for indent_name, indent in indents.items():
                block = b"".join(indent + line + b"\n" for line in key[:-1].split(b"\n"))
                for doc, data in (("yaml", b"tls:\n  key: |\n" + block + b"  verify: true\n"), ("indented", block)):
                    for brk in (b"\n", b"\r\n"):
                        file = data.replace(b"\n", brk)
                        start, end = file.index(b"-----BEGIN"), file.rindex(b"-----") + 5
                        for width in (64, 76):
                            depth, i = i % 3, i + 1
                            with self.subTest(key=name, indent=indent_name, doc=doc, brk=brk, width=width,
                                              depth=depth):
                                masked = mask(written(encoded(file, width) + "\n\nnext paragraph", depth))
                                self.assertEqual([k for k in self.lines_within(file, width, start, end)
                                                  if k in masked], [])
                                self.assertIn("next paragraph", masked)

    def test_a_key_in_a_json_or_env_file_encoded_whole_is_masked(self):
        import base64
        import random
        r = random.Random(2404)
        pem = self.pem(r).decode()
        account = {"type": "service_account", "project_id": "walrus-ledger", "private_key_id": "0123456789abcdef" * 2,
                   "private_key": pem, "client_email": "ledger@walrus-ledger.example"}
        files = {"json": json.dumps(account), "json, indented": json.dumps(account, indent=2),
                 "json, escaped as \\u000a": json.dumps(account).replace("\\n", "\\u000a"),
                 "json in json": json.dumps({"config": json.dumps(account)}),
                 "json, CRLF": json.dumps(dict(account, private_key=pem.replace("\n", "\r\n"))),
                 ".env": 'PRIVATE_KEY="' + pem.replace("\n", "\\n") + '"\nNEXT_STEP=deploy\n'}
        written, encoded, i = Base64ReviewNineteenFindingsTest.written, Base64ReviewSeventeenTest.encoded, 0
        for name, file in files.items():
            data = file.encode()
            start, end = data.index(b"-----BEGIN"), data.rindex(b"-----") + 5
            for width in (0, 64, 76):
                depth, i = i % 3, i + 1
                with self.subTest(file=name, width=width, depth=depth):
                    if width:
                        masked = mask(written(encoded(data, width) + "\n\nnext paragraph", depth))
                        shown = [k for k in self.lines_within(data, width, start, end) if k in masked]
                    else:  # on one line
                        whole = base64.b64encode(data).decode()
                        masked = mask(written(whole + "\n\nnext paragraph", depth))
                        key = whole[-(-start // 3) * 4:end // 3 * 4]
                        shown = [key[k:k + 16] for k in range(0, len(key) - 16, 16) if key[k:k + 16] in masked]
                    self.assertEqual(shown, [])
                    self.assertIn("next paragraph", masked)

    def test_an_ssh2_key_file_encoded_is_masked(self):
        import base64
        import random
        import textwrap
        r = random.Random(2405)
        body = "\n".join(textwrap.wrap(base64.b64encode(bytes(r.randrange(256) for _ in range(600))).decode(), 70))
        certificate = self.certificate(r)
        written, encoded, i = Base64ReviewNineteenFindingsTest.written, Base64ReviewSeventeenTest.encoded, 0
        for label in (b"SSH2 ENCRYPTED PRIVATE KEY", b"SSH2 PRIVATE KEY"):
            key = (b"---- BEGIN " + label + b" ----\nComment: \"walrus ledger key\"\n" + body.encode()
                   + b"\n---- END " + label + b" ----\n")
            for brk in (b"\n", b"\r\n"):
                data = (key + certificate).replace(b"\n", brk)
                end = data.index(b" ----", data.index(b"---- END")) + 5
                for width in (64, 76):
                    depth, i = i % 3, i + 1
                    with self.subTest(label=label, brk=brk, width=width, depth=depth):
                        text = written(encoded(data, width) + "\n\nnext paragraph", depth)
                        self.assertEqual([k for k in self.lines_within(data, width, 0, end) if k in mask(text)], [])
                        self.assertEqual(self.in_key_spans(text, self.lines_within(
                            data, width, end + len(brk) + 4, len(data))), [])

    def test_a_keys_lines_indented_with_any_space_read_as_one_block(self):
        import random
        r = random.Random(2407)
        lines = Base64ReviewSeventeenTest.encoded(self.pem(r)).splitlines()
        forms = []
        for indent in (" " * 2, "　", "  ", " ", "\x1f", "  \t"):
            text = "\n".join(indent + line for line in lines) + "\n\nnext paragraph"
            forms += [(indent, depth, Base64ReviewNineteenFindingsTest.written(text, depth)) for depth in range(3)]
        for indent in ("\\u0020" * 4, "\\u00a0\\u00a0", "\\t\\u0020"):  # escapes as JSON may write them
            once = '"' + "\\n".join(indent + line for line in lines) + '\\n\\nnext paragraph"'
            forms += [(indent, 1, once), (indent, 2, json.dumps(once))]
        for indent, depth, text in forms:
            with self.subTest(indent=indent, depth=depth):
                masked = mask(text)
                self.assertEqual([k for k in lines[1:-1] if k in masked], [])
                self.assertIn("next paragraph", masked)

    def test_a_key_in_a_template_literal_or_a_code_span_is_masked(self):
        import base64
        import random
        r = random.Random(2408)
        # a key cut before its footer, whose base64's short last line is the block's only where its end is read as one
        der = base64.b64encode(made_up_private_key(r))
        cut = next(k for k in range(600, 700) if 4 <= len(base64.b64encode(b"-----BEGIN PRIVATE KEY-----\n" + der[:k]))
                   % 76 <= 12)
        lines = Base64ReviewSeventeenTest.encoded(b"-----BEGIN PRIVATE KEY-----\n" + der[:cut], 76).splitlines()
        for text in ("const key = `" + "\n".join(lines) + "`;\nnext paragraph",
                     "`\n" + "\n".join(lines) + "\n`\n\nnext paragraph", "`" + "\n".join(lines) + "`\n\nnext paragraph"):
            for depth in range(3):
                with self.subTest(text=text[:16], depth=depth):
                    masked = mask(Base64ReviewNineteenFindingsTest.written(text, depth))
                    self.assertEqual([k for k in lines[1:] if k in masked], [])

    def test_a_key_whose_lines_break_at_any_line_break_python_reads_is_masked(self):
        import random
        r = random.Random(2409)
        body = Base64ReviewSeventeenTest.encoded(made_up_private_key(r)).encode().split(b"\n")
        certificate = self.certificate(r)
        written, encoded, i = Base64ReviewNineteenFindingsTest.written, Base64ReviewSeventeenTest.encoded, 0
        for brk in ("\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029", "\\n", "\\r\\n", "\\u000a",
                    "\\f", "\\\\n"):
            b = brk.encode()
            for shape in ("whole", "no footer", "no header"):
                lines = [b"-----BEGIN PRIVATE KEY-----"] + body + [b"-----END PRIVATE KEY-----"]
                lines = lines[:-1] if shape == "no footer" else lines[1:] if shape == "no header" else lines
                key = b.join(lines) + b
                data = (b"Notes from today.\n\n" if shape == "no header" else b"") + key + (
                    b"\n" + certificate if shape == "whole" else b"")
                start = data.index(body[0])
                end = data.index(body[-1]) + len(body[-1])
                for width in (64, 76):
                    depth, i = i % 3, i + 1
                    with self.subTest(brk=brk, shape=shape, width=width, depth=depth):
                        text = written(encoded(data, width) + "\n\nnext paragraph", depth)
                        self.assertEqual([k for k in self.lines_within(data, width, start, end) if k in mask(text)], [])
                        if shape == "whole":
                            self.assertEqual(self.in_key_spans(text, self.lines_within(
                                data, width, len(data) - len(certificate) + 4, len(data))), [])

    def test_a_quote_written_as_a_web_page_writes_it_leads_and_ends_a_line(self):
        import base64
        import random
        r = random.Random(2410)
        encoded = Base64ReviewSeventeenTest.encoded
        line = base64.b64encode(bytes(r.randrange(256) for _ in range(48)))
        public = made_up_certificate(r)
        kept = encoded(public, 52)
        whole = encoded(b"-----BEGIN PRIVATE KEY-----\n" + line + b"\n" + line + b"\n-----END PRIVATE KEY-----\n", 52)
        body = "\n".join([encoded(b"\n-----END PRIVATE KEY-----\n", 52), kept, whole]) + "\n\nnext paragraph"
        quotes = ("&quot;", "&#34;", "&#034;", "&apos;", "&#39;", "&#x27;", "&#X22;", "&grave;", "&#96;", "&#x60;")
        for quote in quotes:
            with self.subTest(lead=quote):
                masked = mask(quote + body)
                self.assertEqual([k for k in kept.splitlines() if k not in masked], [])
                self.assertEqual([k for k in whole.splitlines()[1:-1] if k in masked], [])
        # and after a key's short last line: a key cut before its footer, whose last line is the block's only where
        # its end is read as one
        der = base64.b64encode(made_up_private_key(r))
        cut = next(k for k in range(600, 700) if 4 <= len(base64.b64encode(b"-----BEGIN PRIVATE KEY-----\n" + der[:k]))
                   % 76 <= 12)
        lines = encoded(b"-----BEGIN PRIVATE KEY-----\n" + der[:cut], 76).splitlines()
        for quote in quotes:
            with self.subTest(end=quote):
                masked = mask("<div data-key=" + quote + "\n".join(lines) + quote + ">next paragraph</div>")
                self.assertEqual([k for k in lines[1:] if k in masked], [])

    def test_a_body_cut_into_words_keeps_none_of_its_characters_whatever_leads_its_lines_or_escapes_its_slashes(self):
        # The tail rule read a body's last line only after spaces, tabs, quotes and "#>*-", and its base64 only
        # without JSON's escaped slash ("\/", as PHP writes JSON), so its short last line glued to words showed.
        import textwrap
        from kb import mask as masking
        for kind, (body, width) in Base64KeyCutBeforeWordsTest.bodies().items():
            for full in (1, 3):
                lines = textwrap.wrap(body, width)[:full]
                for lead in ("", "> ", "| ", "+ ", ", ", "; ", "] ", "\t", "<br>", "\xa0", "　"):
                    for q in ("", "> ", "- "):
                        text = ("intro notes\n" + q + "[masked]\n" + "\n".join(lead + line for line in lines)
                                + "\n" + q + "ei walrus moved today\n\nnext paragraph")
                        writings = {"plain": text}
                        if "/" not in lines[0][:40]:  # where main starts a rest: past 40 characters of base64
                            writings["slashes escaped"] = text.replace("/", "\\/")
                            if lead.isascii():  # and past no escaped wide space
                                writings["json, slashes escaped"] = json.dumps(text).replace("/", "\\/")
                                writings["json in json, slashes escaped"] = json.dumps(
                                    writings["json, slashes escaped"]).replace("/", "\\/")
                        for writing, written in writings.items():
                            for older in (False, True):
                                with self.subTest(kind=kind, full=full, lead=lead, q=q, writing=writing, older=older):
                                    if older:
                                        with masking.stored_row(None):
                                            masked = mask(written)
                                    else:
                                        masked = mask(written)
                                    self.assertNotIn("ei walrus", masked)
                                    self.assertEqual([line[i:i + 8] for line in lines for i in range(0, len(line) - 8)
                                                      if "/" not in line[i:i + 8] and line[i:i + 8] in masked], [])
                                    self.assertIn("intro notes", masked)

    def test_a_run_split_by_jsons_escaped_slash_after_a_placeholder_reads_whole(self):
        # A run that JSON's escaped slash ("\/", as PHP writes JSON) splits read as two, each too short to be a key's
        # fragment: on a line a rest passes over, and past public material, where main's rest takes it.
        import base64
        import random
        import textwrap
        body, words = KeyRestPastWordsWholeLineTest.parts()
        texts = {}
        for name, fragment in {"a key's line": body[2][:20], "the review's": "ABCDEFGHabcdefgh"}.items():
            half = len(fragment) // 2
            for slash in ("\\/", "\\\\/"):
                for form in ("\nnext steps {}", "\n{}", "\nThanks\t{}"):
                    split = fragment[:half] + slash + fragment[half:]
                    texts[(name, slash, form[1:7])] = ("[masked]\n" + words + form.format(split) + "\n\nnext paragraph",
                                                        [fragment[:half], fragment[half:]])
        cert = "\n".join(textwrap.wrap(base64.b64encode(made_up_certificate(random.Random(2413))).decode(), 64))
        pieces = ["Abcdefghijk", "Lmnopqrstuv", "Wxyzabcdefg", "Hijklmnopqr", "Stuvwxyzabc"]
        for slash in ("\\/", "\\\\/"):
            texts[("past public material", slash, "")] = (
                "notes\n[masked]\n" + cert + "\n" + slash.join(pieces) + "\n\nnext paragraph", pieces)
        for (name, slash, form), (text, parts) in texts.items():
            for older in (False, True):
                with self.subTest(fragment=name, slash=slash, form=form, older=older):
                    masked = Base64ReviewEighteenTest.masks(text, older)
                    self.assertEqual([part for part in parts if part in masked], [])
                    self.assertIn("next paragraph", masked)

    def test_long_runs_of_a_keys_label_words_cost_time_in_proportion(self):
        # each key word read its label back to where it starts, so words in one long run took time in its square
        import time
        for place in ("words", "one label", "one SSH2 label"):
            took = {}
            for size in (1 << 14, 1 << 16):
                words = b"PRIVATE KEY " * (size // 12)
                data = {"words": words, "one label": b"-----BEGIN " + words + b"PRIVATE KEY-----\n",
                        "one SSH2 label": b"---- BEGIN SSH2 " + words + b"PRIVATE KEY ----\n"}[place]
                text = Base64ReviewSeventeenTest.encoded(data, 76) + "\n\nnext paragraph"
                started = time.process_time()
                mask(text)
                took[size] = time.process_time() - started
            with self.subTest(place=place):
                # four times the text
                self.assertLess(took[1 << 16], 8 * max(took[1 << 14], 0.05), took)

    def test_a_key_written_plain_with_a_label_of_any_length_is_masked(self):
        # the 200-byte bound on a label is the base64 frame reader's alone: main's patterns read a plain key's label
        # however long
        import base64
        import random
        import textwrap
        body = "\n".join(textwrap.wrap(base64.b64encode(made_up_private_key(random.Random(2414))).decode(), 64))
        for size in (201, 1000, 1 << 16):
            label = ("ENCRYPTED " * (size // 10 + 1))[:size].rstrip() + " "
            for name, text in {
                    "pem": f"-----BEGIN {label}PRIVATE KEY-----\n{body}\n-----END {label}PRIVATE KEY-----",
                    "no footer": f"-----BEGIN {label}PRIVATE KEY-----\n{body}",
                    "ssh2": f"---- BEGIN SSH2 {label}PRIVATE KEY ----\n{body}\n---- END SSH2 {label}PRIVATE KEY ----",
            }.items():
                with self.subTest(size=size, form=name):
                    masked = mask("notes\n" + text + "\n\nnext paragraph")
                    self.assertEqual([line for line in body.split("\n") if line in masked], [])
                    self.assertIn("next paragraph", masked)

    def test_a_long_lead_on_a_bodys_last_line_before_words_costs_time_in_proportion(self):
        import time
        line = "MIICbgIBADANBgkqhkiG9w0BAQEFAASCAlgwggJUAgEAAoGBAMN4aZbeK6nVJu1j"
        for unit in ("\\", "\\/", "+ ", "[", "<", "[masked]"):
            took = {}
            for size in (1 << 16, 1 << 18):
                text = (f"notes\n[masked]\n{line}\n" + unit * (size // len(unit)) + line
                        + "\nei walrus moved today\n\nnext paragraph")
                started = time.process_time()
                mask(text)
                took[size] = time.process_time() - started
            with self.subTest(unit=unit):
                # four times the text
                self.assertLess(took[1 << 18], 8 * max(took[1 << 16], 0.05), took)

    @unittest.expectedFailure
    def test_a_keys_lines_each_after_one_quote_or_comment_mark_are_masked(self):
        # The listed limit, as on main: lines that each start with the same mark ("> ", "# ", "// ", "-- ", "; ", "* ",
        # "| ", a quote, written or as a web page's entity) read as no block, so a key encoded in them shows.
        import random
        data = self.pem(random.Random(9))
        start, end = data.index(b"\n") + 1, data.rindex(b"\n-----END")
        lines = Base64ReviewSeventeenTest.encoded(data, 64).splitlines()
        for prefix in ("> ", "# ", "// ", "-- ", "; ", "* ", "| ", '"', "'", "`", "&quot;", "&#39;"):
            with self.subTest(prefix=prefix):
                masked = mask("Notes:\n" + "\n".join(prefix + line for line in lines) + "\n\nnext paragraph")
                self.assertEqual([k for k in self.lines_within(data, 64, start, end) if k in masked], [])

    def test_horizontal_space_and_line_breaks_are_every_one_python_reads(self):
        import re
        from kb import mask as masking
        every = [chr(c) for c in range(0x110000)]
        breaks = {c for c in every if len(("a" + c + "b").splitlines()) == 2}
        spaces = {c for c in every if c.isspace()} - breaks
        self.assertEqual({c for c in every if re.fullmatch(masking._BREAK, c)}, breaks)
        self.assertEqual({c for c in every if re.fullmatch(masking._SPACING, c)}, spaces)
        # in what a key's base64 decodes to: as UTF-8 writes each, and as JSON escapes it, once or more
        for c in spaces | {'"', "'", "`"}:
            for form in (c.encode(), b"\\u%04x" % ord(c), b"\\u%04X" % ord(c), b"\\\\u%04x" % ord(c)):
                self.assertTrue(masking._SPACE_OR_QUOTE_BYTES.fullmatch(form), form)
        for c in breaks:
            for form in (c.encode(), b"\\u%04x" % ord(c), b"\\u%04X" % ord(c), b"\\\\u%04x" % ord(c)):
                self.assertTrue(masking._BREAK_BYTES.fullmatch(form), form)

    def test_a_megabyte_of_space_around_a_header_or_footer_costs_time_in_proportion(self):
        import random
        import time
        r = random.Random(2406)
        key = self.pem(r)
        for place in ("after its footer", "before its header"):
            took = {}
            for size in (1 << 18, 1 << 20):
                data = key[:-1] + b" " * size + b"\n" if place == "after its footer" else b"notes\n" + b" " * size + key
                start, end = data.index(b"-----BEGIN"), data.rindex(b"-----") + 5
                text = Base64ReviewSeventeenTest.encoded(data, 76) + "\n\nnext paragraph"
                started = time.process_time()
                masked = mask(text)
                took[size] = time.process_time() - started
                with self.subTest(place=place, size=size):
                    self.assertEqual([k for k in self.lines_within(data, 76, start, end) if k in masked], [])
            with self.subTest(place=place):
                # four times the text
                self.assertLess(took[1 << 20], 8 * max(took[1 << 18], 0.05), took)


class Base64ReviewTwentyFifthTest(unittest.TestCase):
    """#34's twenty-fifth review: a web page's quote entity, after a footer's spaces, that the edge of the window
    decoded around the footer cut was read as no token, so a key whose footer line ends in spaces and an entity showed
    from there; and a quote entity padded with zeros past 8 characters, cut where a window reading a line's lead back
    starts, was read as text before the lead, so a lone footer's line ran on through a public key below it. Each
    window's test of a token its edge cuts was a second list, kept by hand, of the tokens the readers accept; each now
    comes from the readers' own table, and a test generated from that table cuts every token at every place. Made-up
    keys only."""

    ENTITIES = ("&quot;", "&apos;", "&grave;", "&#34;", "&#39;", "&#96;", "&#x22;", "&#X27;", "&#x60;", "&#0000000034;",
                "&#x00000000022;")
    PADDED = ("&quot;", "&#0000000034;", "&#x00000000022;", "&#x0000000000000000000027;")

    def test_a_quote_entity_after_a_footers_space_ends_its_key_wherever_the_window_cuts_it(self):
        gnupg = Base64ReviewNineteenFindingsTest.fixture("gnupg_control_comment")
        written, encoded, i = Base64ReviewNineteenFindingsTest.written, Base64ReviewSeventeenTest.encoded, 0
        for entity in self.ENTITIES:
            for n in range(90):  # the entity across the first two edges of the window decoded past the footer
                for brk in (b"\n", b"\r\n"):
                    file = gnupg.replace(b"\n", brk)
                    end = file.rindex(b"-----") + 5
                    data = file[:end] + b" " * n + entity.encode() + file[end:]
                    width, depth, i = (64, 76)[i % 2], i % 3, i + 1
                    with self.subTest(entity=entity, n=n, brk=brk, width=width, depth=depth):
                        masked = mask(written("notes\n" + encoded(data, width) + "\n\nnext paragraph", depth))
                        self.assertEqual([k for k in Base64ReviewTwentyFourthTest.lines_within(data, width, 0, end)
                                          if k in masked], [])
                        self.assertIn("next paragraph", masked)

    def test_a_lines_lead_of_a_quote_entity_padded_with_zeros_starts_its_line_wherever_the_window_cuts_it(self):
        import base64
        import random
        r = random.Random(2502)
        encoded = Base64ReviewSeventeenTest.encoded
        line = base64.b64encode(bytes(r.randrange(256) for _ in range(48)))
        publics = {"certificate": (made_up_certificate(r), 52), "rsa": (made_up_public_key(r), 56)}
        i = 0
        for name, (public, width) in publics.items():
            kept = encoded(public, width)
            whole = encoded(b"-----BEGIN PRIVATE KEY-----\n" + line + b"\n" + line + b"\n-----END PRIVATE KEY-----\n",
                            width)
            body = "\n".join([encoded(b"\n-----END PRIVATE KEY-----\n", width), kept, whole]) + "\n\nnext paragraph"
            for entity in self.PADDED:
                for n in range(140):  # the entity across the first two edges of the window reading the lead back
                    depth, i = i % 3, i + 1
                    with self.subTest(public=name, entity=entity, n=n, depth=depth):
                        masked = mask(Base64ReviewNineteenFindingsTest.written(entity + " " * n + body, depth))
                        self.assertEqual([k for k in kept.splitlines() if k not in masked], [])
                        self.assertEqual([k for k in whole.splitlines()[1:-1] if k in masked], [])

    def test_a_header_after_a_cut_quote_entity_starts_its_key_and_a_lone_footer_after_one_masks_no_public_key(self):
        import base64
        import random
        import textwrap
        r = random.Random(2503)
        line = base64.b64encode(bytes(r.randrange(256) for _ in range(48)))
        key = b"-----BEGIN PRIVATE KEY-----\n" + line + b"\n" + line[::-1] + b"\n-----END PRIVATE KEY-----\n"
        certificate = made_up_certificate(r)
        footer = base64.b64encode(b"-----END PRIVATE KEY-----\n").decode()
        for entity in self.PADDED:
            for n in range(80):
                data = b"notes " + entity.encode() + b" " * n + key
                start, end = data.index(b"-----BEGIN"), data.rindex(b"-----") + 5
                for width in (64, 76):
                    with self.subTest(entity=entity, n=n, width=width):
                        masked = mask("intro\n" + "\n".join(textwrap.wrap(base64.b64encode(data).decode(), width))
                                      + "\n\nnext paragraph")
                        self.assertEqual([k for k in Base64ReviewTwentyFourthTest.lines_within(
                            data, width, start, end) if k in masked], [])
                kept = textwrap.wrap(base64.b64encode(certificate).decode(), 52)
                for depth in (0, 1):
                    with self.subTest(entity=entity, n=n, depth=depth):
                        text = "intro\n" + entity + " " * n + footer + "\n" + "\n".join(kept) + "\n\nnext paragraph"
                        masked = mask(Base64ReviewNineteenFindingsTest.written(text, depth))
                        self.assertEqual([k for k in kept if k not in masked], [])

    def test_a_megabyte_of_zeros_in_a_quote_entity_around_a_header_or_footer_costs_time_in_proportion(self):
        import random
        import time
        from kb import mask as masking
        key = Base64ReviewTwentyFourthTest.pem(random.Random(2504))
        for place in ("after its footer", "before its header"):
            took = {}
            for size in (1 << 18, 1 << 20):
                padded = b"&#x" + b"0" * size + b"22;"
                data = (key[:-1] + b"  " + padded + b"\n" if place == "after its footer"
                        else b"notes\n" + padded + b"  " + key)
                start, end = data.index(b"-----BEGIN"), data.rindex(b"-----") + 5
                text = Base64ReviewSeventeenTest.encoded(data, 76) + "\n\nnext paragraph"
                started = time.process_time()
                masked = mask(text)
                took[size] = time.process_time() - started
                with self.subTest(place=place, size=size):
                    self.assertEqual([k for k in Base64ReviewTwentyFourthTest.lines_within(data, 76, start, end)
                                      if k in masked], [])
            with self.subTest(place=place):
                # four times the text
                self.assertLess(took[1 << 20], 8 * max(took[1 << 18], 0.05), took)
        for kind in (str, bytes):  # a line's lead, read back
            took = {}
            for size in (1 << 18, 1 << 20):
                text = "x&#x" + "0" * size + "22;  A"
                text = text if kind is str else text.encode()
                started = time.process_time()
                lead = masking._lead_start(text, len(text) - 1)
                took[size] = time.process_time() - started
                with self.subTest(kind=kind.__name__, size=size):
                    self.assertEqual(lead, 1)
            with self.subTest(kind=kind.__name__):
                self.assertLess(took[1 << 20], 8 * max(took[1 << 18], 0.01), took)

    def test_a_body_line_led_by_a_quote_written_as_a_web_pages_entity_takes_the_characters_glued_below_it(self):
        # Found by round 26's generators: a key's body line after an older kb's placeholder, led by a quote as a web
        # page writes one, its body cut into the line of words below; main masks the glued characters, with the words.
        import base64
        import random
        r = random.Random(2505)
        line = base64.b64encode(made_up_private_key(r)).decode()[:64]
        for entity in ("&quot;", "&apos;", "&#34;", "&#39;", "&#x22;", "&#X27;"):  # those main's rest reads past
            for lead in ("", "# ", "> ", "  "):
                text = ("intro notes\n" + lead + "[masked]\n" + entity + line + "\n" + lead + "Uq walrus moved today"
                        "\n\nnext paragraph")
                for depth in range(3):
                    with self.subTest(entity=entity, lead=lead, depth=depth):
                        masked = mask(Base64ReviewNineteenFindingsTest.written(text, depth))
                        self.assertNotIn(line[8:40], masked)
                        self.assertNotIn("Uq walrus", masked)
                        self.assertIn("walrus moved today", masked)

    @staticmethod
    def instances(tokens, kind):
        """Each of `tokens`, as kb/mask.py's table writes one (its places: the characters each may hold, and how many in
        a row, at least and at most), written out: each place at its least with its first character, and a run of any
        length 12 long with its last; then as JSON writes those, once and twice. In bytes, as UTF-8 writes them."""
        out = set()
        for token in tokens:
            for padded in (False, True):
                one = "".join(chars[-1 if padded else 0] * (12 if padded and most is None else max(least, padded))
                              for chars, least, most in token)
                if kind is bytes:
                    out.add(one.encode("latin-1"))  # the table's bytes: each a Latin-1 character
                    continue
                for depth in range(3):
                    out.add(one)
                    one = json.dumps(one)[1:-1]
        return sorted(out, key=lambda t: (len(t), t))

    def kinds(self):
        """The tokens of a line's lead and of its line break, written out (instances), in a str and in bytes: the bytes
        table's, and the str's as UTF-8 writes them."""
        from kb import mask as masking
        lead = self.instances(masking._LEAD_TOKENS, str)
        breaks = self.instances(masking._BREAK_TOKENS, str)
        lead_bytes = sorted({t.encode() for t in lead} | set(self.instances(masking._LEAD_TOKENS_BYTES, bytes)),
                            key=lambda t: (len(t), t))
        breaks_bytes = sorted({t.encode() for t in breaks} | set(self.instances(masking._BREAK_TOKENS_BYTES, bytes)),
                              key=lambda t: (len(t), t))
        return lead, breaks, lead_bytes, breaks_bytes

    def test_every_token_the_readers_accept_is_one_token_of_them(self):
        import re
        from kb import mask as masking
        lead, breaks, lead_bytes, breaks_bytes = self.kinds()
        self.assertEqual([t for t in lead if not re.fullmatch(f"{masking._SPACING}|{masking._QUOTE_MARK}", t)], [])
        self.assertEqual([t for t in breaks if not re.fullmatch(masking._BREAK, t)], [])
        self.assertEqual([t for t in lead_bytes if not masking._SPACE_OR_QUOTE_BYTES.fullmatch(t)], [])
        self.assertEqual([t for t in breaks_bytes if not masking._BREAK_BYTES.fullmatch(t)], [])
        self.assertGreater(min(len(lead), len(breaks), len(lead_bytes), len(breaks_bytes)), 50)

    def test_every_token_of_a_lines_lead_cut_at_every_place_reads_as_uncut(self):
        from kb import mask as masking
        lead, breaks, lead_bytes, breaks_bytes = self.kinds()
        for kind, tokens, line_breaks in ((str, lead, breaks), (bytes, lead_bytes, breaks_bytes)):
            x, space, a = ("x", " ", "A") if kind is str else (b"x", b" ", b"A")
            missed, lines = [], []
            for token in tokens:
                for n in range(144 + len(token)):  # the token across each place of the first two windows' edges
                    text = x + token + space * n + a
                    if masking._lead_start(text, len(text) - 1) != 1:
                        missed.append((token, n))
                if kind is str:
                    for n in (0, 70, 140):
                        text = x + "\n" + token + space * n + a
                        if not masking._starts_its_line(text, len(text) - 1):
                            lines.append((token, n))
                        plain = x + token + space * n + a
                        if masking._starts_its_line(plain, len(plain) - 1):
                            lines.append((token, n, "no break"))
            if kind is str:
                for brk in line_breaks:
                    for n in (0, 70, 140):
                        text = x + brk + space * n + a
                        if not masking._starts_its_line(text, len(text) - 1):
                            lines.append((brk, n))
            with self.subTest(kind=kind.__name__):
                self.assertEqual(missed[:20], [], len(missed))
                self.assertEqual(lines[:20], [], len(lines))

    @staticmethod
    def frame_read(data):
        """What _frame reads at the first key word in `data`'s base64, read whole."""
        import base64
        from kb import mask as masking
        joined = base64.b64encode(data).decode()
        return masking._frame(joined, [0], masking._ENCODED_KEY.search(joined))

    @staticmethod
    def uncut(data, frame):
        """What _frame reads of `frame` in `data`, a header or footer on a line of its own, wherever its windows' edges
        fall: the kind, where the group holding its first byte starts and the character that holds that byte's first
        bits, the character holding its last byte's last bits, and where the group after its last byte starts."""
        start = data.index(frame)
        end = start + len(frame)
        kind = b"BEGIN" if b"BEGIN" in frame else b"END"
        return kind, start // 3 * 4, start // 3 * 4 + start % 3, (8 * end - 1) // 6, (end + 2) // 3 * 4

    def test_every_token_around_a_header_or_footer_cut_at_every_place_reads_as_uncut(self):
        lead, breaks, lead_bytes, breaks_bytes = self.kinds()
        header, footer = b"-----BEGIN PRIVATE KEY-----", b"-----END PRIVATE KEY-----"
        ahead = [(token, b"\n") for token in lead_bytes] + [(b"", brk) for brk in breaks_bytes]
        back = [(b"\n", token) for token in lead_bytes] + [(brk, b"") for brk in breaks_bytes]
        missed = {"ahead": [], "back": []}
        for token, brk in ahead:
            for n in range(120 + len(token + brk)):  # across each place of the first two edges past the footer
                data = b"notes\n" + footer + b" " * n + token + brk + b"more\n"
                if self.frame_read(data) != self.uncut(data, footer):
                    missed["ahead"].append((token, brk, n))
        for brk, token in back:
            for n in range(200 + len(token + brk)):  # across each place of the first two edges before the header
                data = b"notes" + brk + token + b" " * n + header + b"\nMIIB\n"
                if self.frame_read(data) != self.uncut(data, header):
                    missed["back"].append((brk, token, n))
        for side, cases in missed.items():
            with self.subTest(side=side):
                self.assertEqual(cases[:20], [], len(cases))

    def test_a_label_cut_at_every_place_reads_as_uncut(self):
        from kb import mask as masking
        letters = masking._LABEL_CHARACTERS * 8
        missed = []
        for size in range(201):  # the label across each place of the first three windows' back edges
            label = letters[:size]
            for opening, closing in ((b"-----BEGIN ", b"PRIVATE KEY-----"), (b"---- BEGIN SSH2 ", b"PRIVATE KEY ----")):
                for lead in (b"", b"  "):
                    data = b"notes\n" + lead + opening + label + closing + b"\nMIIB\n"
                    if self.frame_read(data) != self.uncut(data, opening + label + closing):
                        missed.append((opening, size, lead))
        self.assertEqual(missed[:20], [], len(missed))


class Base64ReviewTwentySixthTest(unittest.TestCase):
    """#34's twenty-sixth review: the reading of a key's rest that keeps a tail of ordinary text read its tokens with
    JSON's escaped slash as written, so a fragment PHP's JSON wrote ("Tavlemor\\/q\\/z...") read as a word and pieces
    too short for a token, and showed where main masks it. Each reader of a key's fragment read a view of its own; every
    one now reads the one view (_fragment_view), and a test generated from the readers writes each escape of a slash at
    every place, in JSON at every depth, against the plain read. Made-up keys only."""

    # "/" as JSON writes it: as itself, escaped as PHP writes it, escaped again as JSON in JSON holds that, by its code
    SLASHES = ("/", "\\/", "\\\\\\/", "\\u002f", "\\u002F", "\\\\u002f")
    FRAGMENT = "Tavlemor/q/z/x/w/v/k/j/p"

    @staticmethod
    def written(text, depth, slash, only=None):
        """`text` in a JSON string escaped `depth` times, as Python writes one, with its slashes, or only the one at
        index `only` among them, then written as `slash`."""
        for _ in range(depth):
            text = json.dumps(text)[1:-1]
        if only is None:
            return text.replace("/", slash)
        at = [i for i, c in enumerate(text) if c == "/"][only]
        return text[:at] + slash + text[at + 1:]

    @staticmethod
    def slashes(text, slash):
        """`text` with each `slash` read back as the slash it writes."""
        return text.replace(slash, "/")

    def test_a_fragment_with_jsons_escaped_slash_after_a_placeholder_stays_masked(self):
        # The review's repro: "Tavlemor" reads as a word, and each "\/q" read apart is too short for a token.
        nl = chr(10)
        text = "[masked]" + nl + "MIIE" + "A" * 60 + nl + "notes deploy " + self.FRAGMENT
        for depth in range(3):
            for slash in self.SLASHES:
                written = self.written(text, depth, slash)
                for older in (False, True):
                    with self.subTest(depth=depth, slash=slash, older=older):
                        masked = Base64ReviewEighteenTest.masks(written, older)
                        self.assertEqual([piece for piece in ["Tavlemor"] + [slash + c for c in "qzxwvkjp"]
                                          if piece in masked], [])

    @staticmethod
    def fragments():
        """The fragment with its slashes, and with one slash at every place inside it."""
        letters = "Tavlemorqzxwvkjp"
        yield "Tavlemor/q/z/x/w/v/k/j/p"
        for i in range(1, len(letters)):
            yield letters[:i] + "/" + letters[i:]

    @staticmethod
    def encoded_words():
        """Lines of base64 of words, each with slashes at other places, as an older kb's rest passes over."""
        import base64
        out = []
        for lead in ("", "a ", "an ", "and ", "notes ", "so the "):
            for words in ("notes? the harbor? door, walk? the ledger? today", "what? the walrus? moved, why? notes"):
                line = base64.b64encode((lead + words).encode()).decode()
                if "/" in line and len(line) >= 40 and not line.endswith("="):
                    out.append(line)
        return out

    @staticmethod
    def certificate():
        import base64
        import random
        import textwrap
        return textwrap.wrap(base64.b64encode(made_up_certificate(random.Random(2601))).decode(), 64)

    @staticmethod
    def body():
        import base64
        import random
        import textwrap
        for seed in range(2602, 2700):
            lines = textwrap.wrap(base64.b64encode(made_up_private_key(random.Random(seed))).decode(), 64)[:3]
            if all("/" in line for line in lines[1:]):
                return lines
        raise AssertionError("no made-up key with a slash on each line")

    def reads(self):
        """Each reader of a key's fragment, as (its name, a plain text with slashes, how many of them, how it reads a
        text): a read is a value, or the text up to the place it reads, with its slashes read back."""
        from kb import mask as masking
        nl = chr(10)
        for fragment in self.fragments():
            line = "notes " + fragment + " today"
            yield "_rest_view", line, lambda text: masking._rest_view(text)
            yield "_holds_token", line, lambda text: masking._holds_token(text, 0, len(text))
            rest = "[masked]" + nl + "MIIE" + "A" * 60 + nl + "notes deploy " + fragment + nl + "more words" + nl + nl + "next"

            def tail(text):
                through = masking._token_tail(text)
                return [text[:through(m.start())] for m in masking._LINE_BREAK.finditer(text)]
            yield "_token_tail", rest, tail
        for words in self.encoded_words():
            yield "_encoded_words", nl + words + nl, lambda text: masking._encoded_words(text)
        cert = nl.join(self.certificate())
        yield "_base64_lines", "[masked]" + nl + cert + nl + "notes deploy today", lambda text: [
            line for line, _, _ in masking._base64_lines(text, len("[masked]"), len(text))]
        yield "_public_rest", cert, lambda text: masking._public_rest(text)

        def past(text):
            first = text.index("notes")
            return masking._runs_past(text)(first, len(text))
        yield "_runs_past", "[masked]" + nl + cert + nl + "notes" + nl + cert + nl + "more" + nl + nl + "next", past
        yield ("_runs_past", "[masked]" + nl + cert + nl + "notes " + self.FRAGMENT + nl + nl + "next", past)
        body = self.body()

        def cut(text):
            words = text.index("ei walrus")
            stop = [m.start() for m in masking._LINE_BREAK.finditer(text) if m.end() == words][0]
            return text[:masking._cut_body_end(text, len("[masked]"), stop)]
        yield "_cut_body_end", "[masked]" + nl + nl.join(body) + nl + "ei walrus moved today", cut

    def test_every_reader_of_a_keys_fragment_reads_each_escape_of_a_slash_at_every_place_as_the_slash(self):
        def read_back(value, slash):
            if isinstance(value, str):
                return self.slashes(value, slash)
            if isinstance(value, list):
                return [read_back(v, slash) for v in value]
            return value
        failed = {}
        for name, text, read in self.reads():
            count = text.count("/")
            self.assertTrue(count, name)
            for depth in range(3):
                plain = read(self.written(text, depth, "/"))
                for slash in self.SLASHES[1:]:
                    for only in [None] + list(range(count)):
                        written = self.written(text, depth, slash, only)
                        if read_back(read(written), slash) != plain:
                            failed.setdefault(name, []).append((depth, slash, only, text[:40]))
        self.assertEqual({name: len(cases) for name, cases in failed.items()}, {})
        self.assertEqual(failed, {})

    def test_the_readers_of_a_keys_fragment_read_one_view(self):
        # every reader of a line of a key's rest reads it as _fragment_view does, its characters mapped back
        from kb import mask as masking
        nl = chr(10)
        for slash in self.SLASHES:
            for depth in range(3):
                text = self.written("[masked]" + nl + "notes " + self.FRAGMENT + " &quot;x" + chr(0x200b) + "y"
                                    + nl + "next", depth, slash)
                with self.subTest(slash=slash, depth=depth):
                    view, kept = masking._fragment_view(text)
                    self.assertEqual(view, masking._rest_view(text))
                    self.assertEqual(len(kept), len(view) + 1)
                    self.assertEqual(kept[-1], len(text))
                    self.assertEqual([i for i in range(len(view)) if view[i].isalnum() and text[kept[i]] != view[i]
                                      and not text.startswith(("\\", "&"), kept[i])], [])

    def test_where_main_reads_the_text_as_written_a_slash_escaped_reads_as_main_reads_it(self):
        # The readers of where a key's rest starts and of a run glued to a placeholder read the text as written, as
        # main's do: a rest with an escaped slash in its body's first 40 characters starts none, and a run glued to a
        # placeholder ends at a backslash, on main too. Read through the fragment view, they would mask what main
        # shows; read as written, the output is main's.
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location(
            "main_mask_68506d9", os.path.join(os.path.dirname(__file__), "fixtures", "main_mask_68506d9.py"))
        main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main)
        nl = chr(10)
        forms = {"a run glued to a placeholder": "notes" + nl + "[masked]Abcd/efgh/ijkl notes deploy" + nl + nl + "next",
                 "a body with a slash in its first 40 characters": "notes" + nl + "[masked]" + nl + "MIIEAb9/"
                 + "Qz7x" * 14 + nl + "notes deploy" + nl + nl + "next"}
        for name, text in forms.items():
            for depth in range(3):
                for slash in self.SLASHES[1:]:
                    written = self.written(text, depth, slash)
                    for older in (False, True):
                        with self.subTest(form=name, depth=depth, slash=slash, older=older):
                            self.assertEqual(Base64ReviewEighteenTest.masks(written, older), main.mask(written))

    def test_a_key_encoded_whole_reads_each_escape_of_its_slashes_as_its_plain_lines_do(self):
        # The readers of a key's header and footer and of its lines' lead read JSON's escapes at every depth in views
        # of their own (_encoded_key_spans): each escape of a slash reads as the slash there, so they keep them. A
        # key's PEM text encodes to no slash; a "?" in the words around it, at every third byte, writes one, on the
        # lines that hold its header and its footer.
        import base64
        import random
        nl = chr(10)
        key = Base64ReviewTwentyFourthTest.pem(random.Random(2603))
        for width in (64, 76):
            for n in range(width * 3):
                for m in range(3):
                    data = b"x" * n + b"notes? why? what?" + nl.encode() + key + b"ok" * m + b"next? why? ok?" + nl.encode()
                    start, end = data.index(b"-----BEGIN"), data.rindex(b"-----") + 5
                    encoded = base64.b64encode(data).decode()
                    lines = [encoded[at:at + width] for at in range(0, len(encoded), width)
                             if 6 * at // 8 < end and start <= (6 * min(at + width, len(encoded)) - 1) // 8]
                    if "/" in lines[0] and "/" in lines[-1]:
                        break
                else:
                    continue
                break
            self.assertTrue("/" in lines[0] and "/" in lines[-1])
            text = Base64ReviewSeventeenTest.encoded(data, width) + nl + nl + "next paragraph"
            for depth in range(3):
                for slash in self.SLASHES:
                    written = self.written(text, depth, slash)
                    for older in (False, True):
                        with self.subTest(width=width, depth=depth, slash=slash, older=older):
                            masked = Base64ReviewEighteenTest.masks(written, older)
                            self.assertEqual([line for line in lines
                                              if self.written(line, depth, slash) in masked], [])
                            self.assertIn("next paragraph", masked)

    def test_a_megabyte_of_escaped_slashes_after_a_placeholder_costs_time_in_proportion(self):
        import time
        from kb import mask as masking
        nl = chr(10)
        for slash in ("\\/", "\\u002f"):
            took, viewed, joined = {}, {}, {}
            for size in (1 << 18, 1 << 20):
                words = ("notes deploy Tavlemor" + slash + "q today ") * (size // 30)
                text = "[masked]" + nl + "MIIE" + "A" * 60 + nl + words + nl + nl + "next paragraph"
                started = time.process_time()
                masked = mask(text)
                took[size] = time.process_time() - started
                started = time.process_time()
                masking._fragment_view(text)
                viewed[size] = time.process_time() - started
                started = time.process_time()
                masking._rest_view(text, joined=True)
                joined[size] = time.process_time() - started
                with self.subTest(slash=slash, size=size):
                    self.assertNotIn("Tavlemor", masked)
            with self.subTest(slash=slash):
                # four times the text
                self.assertLess(took[1 << 20], 8 * max(took[1 << 18], 0.05), took)
                self.assertLess(viewed[1 << 20], 8 * max(viewed[1 << 18], 0.01), viewed)
                self.assertLess(joined[1 << 20], 8 * max(joined[1 << 18], 0.01), joined)

    # A fresh review of this round's fix, before it was pushed, found the check of a run past public material
    # (_runs_past) reading the one view, where an escape of no base64 character reads as a space: a key's piece that
    # such an escape splits read as two runs too short to be one, past a certificate after a placeholder, and showed,
    # where main and 369b575 hide it. That check fails closed, so it reads the view joined: an escape of no base64
    # character, a quote's entity and a backslash read as nothing there.
    SPLITS = tuple(s.replace("@", chr(92)) for s in (
        "@t", "@r", "@u00e9", "@u0020", "@u00a0", "@@@u200b", "@@@@u200b", "&quot;", "&#34;", "@"))
    # characters written as themselves that JSON escapes: at depths 1 and 2 they are escapes
    ESCAPED_BY_JSON = (chr(9), chr(13), chr(0xe9), chr(0xa0))

    @staticmethod
    def split_written(text, between, depth):
        """`text` in JSON `depth` times, each U+E001 in it then written as `between` is, escaped with it."""
        mark = chr(0xE001)
        for _ in range(depth):
            text, mark, between = (json.dumps(s)[1:-1] for s in (text, mark, between))
        return text.replace(mark, between)

    def splits(self):
        """Each way of splitting a run that the check reads joined, with the depths it is written at."""
        for between in self.SPLITS:
            yield between, range(3)
        for between in self.ESCAPED_BY_JSON:
            yield between, range(1, 3)

    def test_the_check_of_a_run_past_public_material_reads_an_escape_entity_or_backslash_as_nothing(self):
        from kb import mask as masking
        for between, depths in self.splits():
            for depth in depths:
                with self.subTest(between=between, depth=depth):
                    text = self.split_written("Qw7Zk3PmXa" + chr(0xE001) + "Rt5Hv9LbNe", between, depth)
                    self.assertEqual(masking._rest_view(text, joined=True), "Qw7Zk3PmXaRt5Hv9LbNe")
        for slash in self.SLASHES:  # an escape of a base64 character reads as that character, joined too
            for depth in range(3):
                with self.subTest(slash=slash, depth=depth):
                    text = self.written("Qw7Zk3PmXa/Rt5Hv9LbNe", depth, slash)
                    self.assertEqual(masking._rest_view(text, joined=True), "Qw7Zk3PmXa/Rt5Hv9LbNe")

    def test_a_keys_piece_an_escape_splits_past_a_certificate_stays_masked(self):
        nl = chr(10)
        cert = nl.join(self.certificate())
        for between, depths in self.splits():
            for depth in depths:
                for tail in ("", nl + nl + "next paragraph"):
                    text = self.split_written("[masked]" + nl + cert + nl + "Qw7Zk3PmXa" + chr(0xE001) + "Rt5Hv9LbNe"
                                              + tail, between, depth)
                    for older in (False, True):
                        with self.subTest(between=between, depth=depth, tail=bool(tail), older=older):
                            masked = Base64ReviewEighteenTest.masks(text, older)
                            self.assertEqual([p for p in ("Qw7Zk3PmXa", "Rt5Hv9LbNe") if p in masked], [])
                            if tail:
                                self.assertIn("next paragraph", masked)

    def test_a_keys_body_an_escape_splits_every_12_characters_past_a_certificate_stays_masked(self):
        import base64
        import random
        import textwrap
        nl = chr(10)
        body = textwrap.wrap(base64.b64encode(made_up_private_key(random.Random(2711))).decode(), 64)
        pieces = [line[i:i + 12] for line in body for i in range(0, len(line), 12)]
        split = nl.join(chr(0xE001).join(line[i:i + 12] for i in range(0, len(line), 12)) for line in body)
        text = "[masked]" + nl + nl.join(self.certificate()) + nl + split + nl + nl + "next paragraph"
        for between in (chr(92) + "t", "&quot;", chr(9)):
            for depth in range(1 if between == chr(9) else 0, 3):
                written = self.split_written(text, between, depth)
                for older in (False, True):
                    with self.subTest(between=between, depth=depth, older=older):
                        masked = Base64ReviewEighteenTest.masks(written, older)
                        self.assertEqual([p for p in pieces if len(p) >= 8 and p in masked], [])
                        self.assertIn("next paragraph", masked)

    # The coordinator's ruling on round 27's own review: past a certificate after a placeholder, a key's run cut into
    # pieces shorter than the 16 characters the check reads, and joined by any separator written as itself (a space, a
    # hyphen, a comma, an entity outside the quote table, a tab or a no-break space as written), showed, on 369b575
    # too, where main hides it; a key's whole body cut so showed. The 16-character exception is for a stray short
    # piece, not a key cut into short pieces, so the check reads a run across any separator between pieces of it.
    # and, by a second fresh review, a line break, an HTML tag written or as entities, and a short word or number
    JOINERS = (" ", "-", ",", ", ", ".", "_", " | ", "&amp;", "&nbsp;", chr(9), chr(0xe9), chr(0xa0), chr(10), "<br>",
               "&lt;br&gt;", "&lt;br/&gt;", "&lt;wbr&gt;", "&lt;/b&gt;", "&lt;/span&gt;&lt;span&gt;", " and ", " or ",
               " a ", " 1 ") + SPLITS

    def test_a_keys_pieces_joined_by_any_separator_past_a_certificate_stay_masked(self):
        nl = chr(10)
        cert = nl.join(self.certificate())
        for between in self.JOINERS:
            for depth in range(3):
                text = self.split_written("[masked]" + nl + cert + nl + "Qw7Zk3PmXa" + chr(0xE001) + "Rt5Hv9LbNe" + nl
                                          + nl + "next paragraph", between, depth)
                for older in (False, True):
                    with self.subTest(between=between, depth=depth, older=older):
                        masked = Base64ReviewEighteenTest.masks(text, older)
                        self.assertEqual([p for p in ("Qw7Zk3PmXa", "Rt5Hv9LbNe") if p in masked], [])
                        self.assertIn("next paragraph", masked)

    def test_a_keys_body_cut_at_every_width_and_joined_by_any_separator_past_a_certificate_stays_masked(self):
        # generated: each separator, each width from 4 to 16, JSON depths 0 to 2, fresh and in an older kb's row; the
        # pieces on lines of their own, and on a line of words
        import base64
        import random
        import textwrap
        nl, mark = chr(10), chr(0xE001)
        body = textwrap.wrap(base64.b64encode(made_up_private_key(random.Random(2712))).decode(), 64)[1:4]
        cert = nl.join(self.certificate())
        failed = {}
        for width in range(4, 17):
            cut = [[line[i:i + width] for i in range(0, len(line), width)] for line in body]
            forms = {"lines": (nl.join(mark.join(pieces) for pieces in cut), cut),
                     "words": ("notes deploy " + mark.join(cut[0]) + " today", cut[:1])}
            for shape, (joined, lines) in forms.items():
                pieces = [piece for line in lines for piece in line if len(piece) >= 4]
                for between in self.JOINERS:
                    for depth in range(3):
                        text = self.split_written("[masked]" + nl + cert + nl + joined + nl + nl + "next paragraph",
                                                  between, depth)
                        for older in (False, True):
                            masked = Base64ReviewEighteenTest.masks(text, older)
                            if ([piece for piece in pieces if text.count(piece) == 1 and piece in masked]
                                    or "next paragraph" not in masked):
                                failed.setdefault((shape, between), []).append((width, depth, older))
        self.assertEqual({key: len(cases) for key, cases in failed.items()}, {})

    def test_a_keys_pieces_with_one_that_reads_as_a_word_past_a_certificate_stay_masked(self):
        # A second fresh review: a piece of four characters that reads as a word ("kmpt", "Next"), as about one in
        # fifteen of a key's do, ended the chain, so 20 characters cut every 4 showed.
        nl, mark = chr(10), chr(0xE001)
        cert = nl.join(self.certificate())
        for word in ("kmpt", "Xyzw", "ABCE", "Next"):
            pieces = ["Qw7Z", word, "Rt5H", "9LbN", "Pq2R"]
            for between in self.JOINERS:
                for depth in range(3):
                    text = self.split_written("[masked]" + nl + cert + nl + "notes deploy " + mark.join(pieces)
                                              + " today" + nl + nl + "next paragraph", between, depth)
                    for older in (False, True):
                        with self.subTest(word=word, between=between, depth=depth, older=older):
                            masked = Base64ReviewEighteenTest.masks(text, older)
                            self.assertEqual([p for p in pieces if text.count(p) == 1 and p in masked], [])
                            self.assertIn("next paragraph", masked)

    def test_ordinary_words_after_a_key_encoded_whole_and_a_certificate_stay_as_on_main(self):
        # A second fresh review: a key encoded whole, which main leaves readable and this branch masks, then a
        # certificate: what follows reads as that placeholder's rest, so a chain of names in camel case or a link's
        # path ("iPhone, GitHub, macOS", "example.com/Docs/Index") masked words main keeps. A piece that reads as words
        # is no key's. (A run of 16 or more of both cases, as in a longer path, is masked there as before: listed.)
        import random
        nl = chr(10)
        key = Base64ReviewSeventeenTest.encoded(Base64ReviewTwentyFourthTest.pem(random.Random(2713)))
        cert = nl.join(self.certificate())
        words = ["We use iPhone, GitHub, macOS and YouTube today", "Visit https://example.com/Docs/Index for more",
                 "PostgreSQL JavaScript TypeScript OAuth2 WebSocket", "Kubernetes, OpenShift, DigitalOcean, LinkedIn, PayPal",
                 "AbortController, useEffect, useState and setTimeout",
                 "request id 123e4567-e89b-12d3-a456-426614174000 at 2026-11-07T12:00Z",
                 "uses SHA256 with abc123 and IPv6 for Win11", "the iOS17 and macOS14 builds of XcodeKit",
                 "deploy on port 8080 with HTTP/2 and TCP/IP, build 20261104, v1.2.3"]
        for line in words:
            for depth in range(3):
                text = self.split_written("notes" + nl + key + nl + cert + nl + line + nl + nl + "next paragraph", "",
                                          depth)
                for older in (False, True):
                    with self.subTest(line=line, depth=depth, older=older):
                        masked = Base64ReviewEighteenTest.masks(text, older)
                        self.assertIn(self.split_written(line, "", depth), masked)

    def test_ids_hashes_and_dates_after_a_key_encoded_whole_and_a_certificate_stay_as_on_main(self):
        # The comparison texts read past such a key and a certificate: a hash or an id in one case (hex, a chat's
        # channel id, a ULID) and a date's digits read as a key's pieces with a piece of the other case near them, so
        # lines main keeps were masked. A key's piece of 8 characters or more holds both cases (but about 3 in 100);
        # its digits alone, about 1 in 1,700 of 4.
        import random
        nl = chr(10)
        key = Base64ReviewSeventeenTest.encoded(Base64ReviewTwentyFourthTest.pem(random.Random(2713)))
        cert = nl.join(self.certificate())
        lines = ["vpc-04e33e4a378dfd251 2026-11-02T13:27:00Z INFO took 76ms",
                 "channel C0HXMYO0FF user U0TOM6TJ3U, 0179d4b9a7fb95b24e87d1db08e56f9ae63dc298 dist",
                 "ULID 01SKMVR4M7C957VR8SY1BYQGGF sha256:60be6050f7dfb2e31d951afbaa7982f1"]
        for line in lines:
            for depth in range(3):
                text = self.split_written("notes" + nl + key + nl + cert + nl + line + nl + nl + "next paragraph", "",
                                          depth)
                for older in (False, True):
                    with self.subTest(line=line, depth=depth, older=older):
                        masked = Base64ReviewEighteenTest.masks(text, older)
                        self.assertIn(self.split_written(line, "", depth), masked)

    def test_a_keys_pieces_of_letters_alone_or_in_a_tags_attributes_past_a_certificate_stay_masked(self):
        # An automated review of eb1f4ab: a row of a key's pieces had to hold a digit, "+" or "/", which a fragment of
        # letters alone (about 1 in 28 of 16 characters) lacks, though _MIXED_RUN masks one run on its cases alone;
        # and a tag's attributes read as part of the separator, so a piece in one ("title") was passed over.
        nl, mark = chr(10), chr(0xE001)
        cert = nl.join(self.certificate())
        letters = "QwZRtHLbNPqRkXvMjWgT"
        cuts = {f"letters every {w}": [letters[i:i + w] for i in range(0, 20, w)] for w in (4, 5, 10)}
        quote = chr(34)
        for between in (" ", "-", "<br>", "&lt;br&gt;", chr(9)):
            for name, pieces in cuts.items():
                for depth in range(3):
                    text = self.split_written("[masked]" + nl + cert + nl + "notes deploy " + mark.join(pieces)
                                              + " today" + nl + nl + "next paragraph", between, depth)
                    for older in (False, True):
                        with self.subTest(cut=name, between=between, depth=depth, older=older):
                            masked = Base64ReviewEighteenTest.masks(text, older)
                            self.assertEqual([p for p in pieces if p in masked], [])
                            self.assertIn("next paragraph", masked)
        for tag in ("<span title=@{}@>", "&lt;span title=&quot;{}&quot;&gt;", "<a data-x=@{}@ class=@hl@>"):
            tag = tag.replace("@", quote)
            words = "notes Qw7Z" + tag.format("Rt5H9LbN") + "Pq2R" + tag.format("Xy8kMn3B") + " today"
            for depth in range(3):
                text = self.split_written("[masked]" + nl + cert + nl + words + nl + nl + "next paragraph", "", depth)
                for older in (False, True):
                    with self.subTest(tag=tag, depth=depth, older=older):
                        masked = Base64ReviewEighteenTest.masks(text, older)
                        self.assertEqual([p for p in ("Qw7Z", "Rt5H9LbN", "Pq2R", "Xy8kMn3B") if p in masked], [])
                        self.assertIn("next paragraph", masked)

    def test_ordinary_words_with_numbers_and_acronyms_past_a_certificate_stay(self):
        # the exception's own: past public material, words with a port, a version, a date, acronyms, paths and a
        # request id in small letters are no key's pieces
        nl = chr(10)
        words = ["deploy on port 8080 with HTTP/2 and TCP/IP, build 20261104, v1.2.3",
                 "request id 123e4567-e89b-12d3-a456-426614174000 at 2026-11-07T12:00Z",
                 "see the README for SHA256 and RSA notes"]
        text = "[masked]" + nl + nl.join(self.certificate()) + nl + nl.join(words) + nl + nl + "next paragraph"
        for depth in range(3):
            written = self.split_written(text, "", depth)
            for older in (False, True):
                with self.subTest(depth=depth, older=older):
                    masked = Base64ReviewEighteenTest.masks(written, older)
                    self.assertEqual([line for line in words if self.split_written(line, "", depth) not in masked], [])

    def test_a_megabyte_of_short_pieces_past_a_certificate_costs_time_in_proportion(self):
        import time
        from kb import mask as masking
        nl = chr(10)
        cert = nl.join(self.certificate())
        # pieces a key's could be that never make 16 characters between words, pieces in small letters only, and pieces
        # between an escaped tag and a name in camel case
        for unit in ("Qw7Z notes deploy ", "ab12 cd34 ef56 ", "Qw7Z&lt;br&gt;GitHub "):
            took, read = {}, {}
            for size in (1 << 18, 1 << 20):
                text = "[masked]" + nl + cert + nl + unit * (size // len(unit)) + nl + nl + "next paragraph"
                started = time.process_time()
                mask(text)
                took[size] = time.process_time() - started
                started = time.process_time()
                masking._joined_pieces(text)
                read[size] = time.process_time() - started
            with self.subTest(unit=unit):
                # four times the text
                self.assertLess(took[1 << 20], 8 * max(took[1 << 18], 0.05), took)
                self.assertLess(read[1 << 20], 8 * max(read[1 << 18], 0.01), read)

    def test_a_long_run_of_letters_after_a_placeholder_costs_time_in_proportion(self):
        # A check of this round's: a capital, small letters and a digit glued to a placeholder or on the next line
        # ("[masked]Baaa...a1") were read against a word in time that grows with the square of their length
        # (_token_like), 5 seconds for 32,000 characters on 369b575, where main takes 0.05. The same runs, and runs
        # of letters read against words in camel case (_reads_as_words), past a certificate.
        import time
        from kb import mask as masking
        nl = chr(10)
        runs = {"word then a digit": lambda n: "B" + "a" * n + "1", "small then a capital": lambda n: "a" * n + "B",
                "camel case then a capital": lambda n: "Ab" * (n // 2) + "X"}
        shapes = {"glued": "[masked]@", "next line": "[masked]" + nl + "@",
                  "words past a certificate": "[masked]" + nl + nl.join(self.certificate()) + nl + "notes @ today"}
        for name, run in runs.items():
            read, joined = {}, {}
            for size in (1 << 13, 1 << 15):
                started = time.process_time()
                masking._token_like(run(size))
                read[size] = time.process_time() - started
                started = time.process_time()
                masking._joined_pieces("notes " + run(size) + " today")
                joined[size] = time.process_time() - started
            with self.subTest(run=name):
                # four times the run
                self.assertLess(read[1 << 15], 8 * max(read[1 << 13], 0.01), read)
                self.assertLess(joined[1 << 15], 8 * max(joined[1 << 13], 0.01), joined)
            for shape, around in shapes.items():
                for older in (False, True):
                    took = {}
                    for size in (1 << 13, 1 << 15):
                        text = around.replace("@", run(size))
                        started = time.process_time()
                        Base64ReviewEighteenTest.masks(text, older)
                        took[size] = time.process_time() - started
                    with self.subTest(run=name, shape=shape, older=older):
                        self.assertLess(took[1 << 15], 8 * max(took[1 << 13], 0.05), took)


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

    def test_a_split_names_value_ends_where_main_ends_it_at_a_token(self):
        self.assertEqual(mask("pass\u200bword=ghp_" + "A1" * 12 + "|notes"), "pass\u200bword=[masked]|notes")

    def test_an_invisible_character_past_a_names_separator_splits_no_name(self):
        for text in ("password:\n\u200b\n\nNext paragraph is kept.", "api_key:\u200b\n\nNext paragraph is kept."):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        self.assertNotIn("walrus2secret9", mask("password\u200b=walrus2secret9"), "an invisible before the = still splits")

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

    def test_a_tokens_invisible_characters_count_for_nothing(self):
        for text in ("Authorization: aB1cD2eF3g\u200b\u200b", "Authorization: RFC7235-compli\u00adant",
                     "Authorization: ISO-2700\u200b1"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        self.assertNotIn("H4", mask("Authorization: aB1cD2eF3gH4\u200b"), "twelve characters a reader sees still count")

    def test_authorization_inside_a_path_or_a_value_is_no_header(self):
        for text in ("password=/notes:Authorization:A1b2C3d4E5f6/data", "see /docs/Authorization:A1b2C3d4E5f6Qx7",
                     "x:Authorization: A1b2C3d4E5f6Qx7"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        for text in ("Authorization: A1b2C3d4E5f6Qx7", "{\"Authorization\": \"A1b2C3d4E5f6Qx7\"}",
                     "-H 'Authorization: A1b2C3d4E5f6Qx7'", "x\nAuthorization: A1b2C3d4E5f6Qx7", "a; Authorization: A1b2C3d4E5f6Qx7"):
            with self.subTest(text=text):
                self.assertNotIn("A1b2C3d4E5f6Qx7", mask(text))

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
                    + [mask_module.KEYED_SECRET, mask_module.ENV_KEY_SECRET, mask_module._GLUED_RUN])
        for text in ("[masked]", "[masked] [masked]\n[masked]\n\n[masked]", "a [masked], b [masked].", '"[masked]"'):
            with self.subTest(text=text):
                for pattern in patterns:
                    self.assertIsNone(pattern.search(text), pattern.pattern[:60])
                self.assertEqual(mask_module._key_rest_edits(text), [])
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

    def test_a_base64_key_in_text_still_changing_at_the_limit_is_masked(self):
        # the key is hidden over what the passes leave even when they are still changing, so the one placeholder the
        # limit puts down covers it, wherever it stands
        import base64
        import textwrap
        from kb import mask as mask_module
        pem = (b"-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(bytes.fromhex("302e020100300506032b657004220420")
               + bytes(range(32))) + b"\n-----END PRIVATE KEY-----\n")
        lines = textwrap.wrap(base64.b64encode(pem).decode(), 64)
        block = "\n".join(lines)

        def still_changing(text):  # a pass that changes the text's end each time, as one still changing at the limit
            return (text[:-1] + "x" + text[-1:], list(range(len(text) - 1)) + [-1, len(text) - 1], True)
        for text in (block + "\nnotes deploy", "notes deploy\n" + block + "\nnext", "head\n" + block):
            with self.subTest(text=text[:12]):
                before = mask_module.capped
                with mock.patch.object(mask_module, "_passes_masked", still_changing):
                    masked = mask_module.mask(text)
                    shown, _ = mask_module.mask_with_origin(text)
                for line in lines:
                    self.assertNotIn(line[:12], masked)
                    self.assertNotIn(line[:12], shown)
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

    def test_a_base64_key_wrapped_at_every_width_is_masked_whole_in_place(self):
        """Each kind of key file, alone or after a certificate, base64 wrapped at every width from 1 to its length, in a
        field's value, a JSON string, one in JSON, under a heading and after prose: everything from the line that holds
        its header's first byte through its last line is one placeholder, and the text around it stays. One kind and
        one line break for each width; KB_KEY_CASES of 20000 or more reads every kind with every break."""
        import base64
        import json
        import os
        import random
        import textwrap
        r = random.Random(13)

        def body(n):
            return "\n".join(textwrap.wrap(base64.b64encode(bytes(r.randrange(256) for _ in range(n))).decode(), 64))

        def pem(label):
            return f"-----BEGIN {label}-----\n{body(r.randint(60, 120))}\n-----END {label}-----\n"

        def pgp(label):
            return (f"-----BEGIN {label}-----\nVersion: GnuPG v2\n\n{body(r.randint(60, 120))}\n"
                    f"={base64.b64encode(bytes(r.randrange(256) for _ in range(3))).decode()}\n-----END {label}-----\n")

        cert = f"-----BEGIN CERTIFICATE-----\n{body(48)}\n-----END CERTIFICATE-----\n"
        encrypted = ("-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\n"
                     f"DEK-Info: AES-128-CBC,00112233445566778899AABBCCDDEEFF\n\n{body(96)}\n"
                     "-----END RSA PRIVATE KEY-----\n")
        files = [("", pem("PRIVATE KEY")), ("", pem("RSA PRIVATE KEY")), ("", pem("EC PRIVATE KEY")),
                 ("", pem("OPENSSH PRIVATE KEY")), ("", pem("ENCRYPTED PRIVATE KEY")), ("", encrypted),
                 ("", pgp("PGP PRIVATE KEY BLOCK")), ("", pgp("PGP SECRET KEY BLOCK")), (cert, pem("PRIVATE KEY")),
                 ("\n", pem("PRIVATE KEY")), ("a\n", pem("EC PRIVATE KEY"))]
        breaks = ("\n", "\r\n", "\\n", "\n  ")
        every = int(os.environ.get("KB_KEY_CASES", "3000")) >= 20000

        def placed(lines, brk, cut):
            """(the text, what it masks to) for the block's lines in each place, the key's from line `cut` on."""
            head = brk.join(lines[:cut] + [""])
            line_end = {"\\n": "\\n", "\r\n": "\r\n"}.get(brk, "\n")
            block, masked = brk.join(lines), head + "[masked]"
            yield "field", f"tls.key: {block}{line_end}ca.crt: none", f"tls.key: {masked}{line_end}ca.crt: none"
            if brk != "\\n":
                value = {"tls.key": block, "note": "rotate after the review"}
                hidden = dict(value, **{"tls.key": masked})
                yield "JSON", json.dumps(value), json.dumps(hidden)
                yield "JSON in JSON", json.dumps(json.dumps(value)), json.dumps(json.dumps(hidden))
            yield "heading", f"Plan{line_end}{block}{line_end}next steps", f"Plan{line_end}{masked}{line_end}next steps"
            yield ("prose", f"here is the key {block}{line_end}it rotates after the review",
                   f"here is the key {masked}{line_end}it rotates after the review")

        for n, (before, key) in enumerate(files):
            encoded = base64.b64encode((before + key).encode()).decode()
            header = len(before.encode()) // 3 * 4  # where the group holding the header's first byte starts
            for width in range(1, len(encoded) + 1):
                if not every and width % len(files) != n:
                    continue
                lines = textwrap.wrap(encoded, width)
                cut = header // width
                for brk in breaks if every else (breaks[width // len(files) % len(breaks)],):
                    for place, text, masked in placed(lines, brk, cut):
                        with self.subTest(file=n, width=width, brk=brk, place=place):
                            self.assertEqual(mask(text), masked)

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
