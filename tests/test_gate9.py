"""The gate 9 re-check's findings (2026-11-02, at main 4d7a130), each masked as a reader stores it: a token behind a
terminal colour code, a password an agent types into a page, and the shapes after them. Made-up secrets only."""
import json
import os
import random
import string
import tempfile
import unittest

from kb import store
from kb.mask import mask, mask_all, typed_secrets
from kb.sources import claude_code, codex, hermes

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


READERS = ("codex", "claude_code", "hermes")


def stored(reader, records):
    """What a reader stores of a session made of `records`, through store_item, as one text. Hermes's records are a
    message's tool calls."""
    with tempfile.TemporaryDirectory() as tmp:
        if reader == "hermes":
            item = hermes.Item("h-g9", "2026-11-01T10:00:01Z", "do it",
                               "\n".join(hermes._tool_calls(json.dumps(records))), {}, 0)
        else:
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
    if reader == "hermes":
        return [{"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]
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
ORDINARY_LABELS = ("Email", "Search", "Full name", "Message", "Street address", "Username", "Company", "Comment",
                   "Token name", "Password hint", "Secret question", "API key description", "Token expiration")
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
        for reader in READERS:
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

    def test_each_reader_keeps_the_fields_label(self):
        for reader in READERS:
            with self.subTest(reader=reader):
                text = stored(reader, tool_call(reader, "mcp__playwright__browser_type",
                                                {"element": "Password textbox", "ref": "e23", "text": PASSWORD}))
                self.assertIn("Password textbox", text)
                self.assertIn("[masked]", text)

    def test_every_shape_masks_a_secret_label_and_keeps_an_ordinary_one(self):
        alnum = string.ascii_letters + string.digits
        for seed in range(150):
            r = random.Random(f"typed:{seed}")
            secret = "".join(r.choice(alnum) for _ in range(r.randint(6, 16))) + r.choice("!#%*")
            for label, value, hidden in ((r.choice(SECRET_LABELS), secret, True),
                                         (r.choice(ORDINARY_LABELS), r.choice(ORDINARY_VALUES), False)):
                for name, args in typing_calls(r, label, value):
                    for reader in READERS:
                        with self.subTest(seed=seed, reader=reader, name=name, label=label):
                            text = stored(reader, tool_call(reader, name, args))
                            if hidden:
                                self.assertNotIn(value, text)
                            else:
                                self.assertIn(value, text)

    def test_an_unlabelled_fill_masks_a_credential_and_keeps_ordinary_text(self):
        for value, hidden in ((PASSWORD, True), ("q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1", True), ("pelican plan", False),
                              ("me@example.com", False), ("Sam Lee", False), ("2026-11-02", False)):
            for reader in READERS:
                with self.subTest(value=value, reader=reader):
                    text = stored(reader, tool_call(reader, "mcp__chrome-devtools__fill", {"uid": "1_7", "value": value}))
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


SUB = "q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1"
AWS = "wJx9Rk2Lq7Zp4Mn8Bv3Cx6Tz1Yh5Gf0Ds2Ae7Kq"


class SmallerShapesTest(unittest.TestCase):
    """The re-check's P2 and P3 shapes, each masked to the secret alone, and the ordinary text beside them kept."""

    MASKED = (
        ("REDIS_URL=redis://:p4ssW0rdXyz9@cache:6379/0", "REDIS_URL=redis://:[masked]@cache:6379/0"),
        ("amqp://:Tr0ub4dor-Zk9q@mq.internal:5672", "amqp://:[masked]@mq.internal:5672"),
        ("redis%3A%2F%2F%3Ap4ssW0rdXyz9%40cache%3A6379", "redis%3A%2F%2F%3A[masked]%40cache%3A6379"),
        ("auth: { user: 'me@example.com', pass: 'Tr0ub4dor-Zk9q' }", "auth: { user: 'me@example.com', pass: '[masked]' }"),
        ("Server=db;Uid=sa;Pwd=Tr0ub4dor-Zk9q;", "Server=db;Uid=sa;Pwd=[masked];"),
        ('{"user": "sa", "pwd": "Tr0ub4dor-Zk9q"}', '{"user": "sa", "pwd": "[masked]"}'),
        (f"aws configure set aws_secret_access_key {AWS}", "aws configure set aws_secret_access_key [masked]"),
        ("aws configure set default.aws_session_token Tr0ub4dor-Zk9q-Qm8x",
         "aws configure set default.aws_session_token [masked]"),
        ("machine api.heroku.com login me@x.com password 3f1c9a2e-7b4d-4e8a-9c2f-1a2b3c4d5e6f",
         "machine api.heroku.com login me@x.com password [masked]"),
        ("az login -u me@x.com -p Tr0ub4dor-Zk9q", "az login -u me@x.com -p [masked]"),
        ("mongosh -u admin -p Tr0ub4dor-Zk9q --authenticationDatabase admin",
         "mongosh -u admin -p [masked] --authenticationDatabase admin"),
        ("twine upload -u sam -p Tr0ub4dor-Zk9q dist/*", "twine upload -u sam -p [masked] dist/*"),
        ("keytool -list -keystore k.jks -storepass Tr0ub4dor9", "keytool -list -keystore k.jks -storepass [masked]"),
        ("jarsigner -keystore k.jks -storepass Tr0ub4dor9 -keypass Zk9qWm2x app.jar",
         "jarsigner -keystore k.jks -storepass [masked] -keypass [masked] app.jar"),
        (f"Ocp-Apim-Subscription-Key: {SUB}", "Ocp-Apim-Subscription-Key: [masked]"),
        ("<server><id>repo</id><username>me</username><password>Tr0ub4dor-Zk9q</password></server>",
         "<server><id>repo</id><username>me</username><password>[masked]</password></server>"),
        ("<password>Xk9,mP2;qL7v</password>", "<password>[masked]</password>"),
        ("DB_PASSWORD=Xk9,mP2;qL7v", "DB_PASSWORD=[masked]"),
        ("export DB_PASSWORD=Xk9,mP2;qL7v # prod\nNEXT=1", "export DB_PASSWORD=[masked] # prod\nNEXT=1"),
        ('DB_PASSWORD="Xk9,mP2;qL7v"', 'DB_PASSWORD="[masked]"'),
        ("db_password = 'P@ss)w0rd;99x'", "db_password = '[masked]'"),
        ("password: P@ss)w0rd99x", "password: [masked]"),
        ("  - password: P@ss)w0rd99x\n", "  - password: [masked]\n"),
        ("SMTP_PASS=abcd efgh ijkl mnop", "SMTP_PASS=[masked]"),
        ('SMTP_PASSWORD="abcd efgh ijkl mnop"', 'SMTP_PASSWORD="[masked]"'),
        ("-----BEGIN%20PRIVATE%20KEY-----%0AMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7Zq7Lm2Xc9Vb4",
         "[masked]"),
        # Four words after a password's name and a colon are a sentence, read as main reads it, not an app password.
        ("password: this will work fine", "password: [masked] will work fine"),
    )
    KEPT = (
        "PWD=/home/sam/code", "OLDPWD=/home/sam", "pwd: /home/sam/code", "run pwd and pass the path", "pass: the build",
        "tests pass: 12 of 12", "pass = 3", "the -p flag sets the port", "psql -h db -p 5432 -U sam", "ssh -p 2222 host",
        "mongosh --port 27017 shop", "keytool -list -storepass:env STOREPASS", "machine learning login page password",
        "http://[::1]:8080/", "git@github.com:org/repo.git", "ws://:443/socket", "<password>${env.DEPLOY_PASSWORD}</password>",
        "<token>your token here</token>", "subscription_key: premium-plan", "passwords: see the vault",
        'password: "see the vault"', "password=x;user=sam", "Password=ab;Server=db;Database=shop",
        "the password is on, off and on again", "password (see below)",
        "aws configure set region us-east-1", "aws configure set output json",
        # The re-check's over-masking: names and values that are no secret.
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:prod/db-AbCdEf", "tokenizer: cl100k_base",
        "tokenizer=o200k_base", "session_key: 2026-11-01T10:00:00Z", "notes/password=reset-flow-v2.md",
        "access_token_expires: 2026-11-01T10:00:00+00:00",
    )

    def test_each_shape_masks_its_secret_alone(self):
        for text, masked in self.MASKED:
            with self.subTest(text=text):
                self.assertEqual(mask(text), masked)

    def test_ordinary_text_beside_them_is_kept(self):
        for text in self.KEPT:
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)

    def test_fields_named_pass_and_pwd_and_a_subscription_key(self):
        self.assertEqual(mask_all({"user": "sa", "pwd": PASSWORD}), {"user": "sa", "pwd": "[masked]"})
        self.assertEqual(mask_all({"auth": {"user": "me", "pass": PASSWORD}}), {"auth": {"user": "me", "pass": "[masked]"}})
        self.assertEqual(mask_all({"headers": {"Ocp-Apim-Subscription-Key": SUB}}),
                         {"headers": {"Ocp-Apim-Subscription-Key": "[masked]"}})
        self.assertEqual(mask_all({"env": {"REDIS_URL": f"redis://:{PASSWORD}@cache:6379"}}),
                         {"env": {"REDIS_URL": "redis://:[masked]@cache:6379"}})
        for kept in ({"pwd": "/home/sam"}, {"pass": True}, {"pass": 3}, {"pass": "yes"}, {"result": "pass"}):
            with self.subTest(kept=kept):
                self.assertEqual(mask_all(kept), kept)


class NameAfterAValueTest(unittest.TestCase):
    """A named value that is itself a name and its separator, as in "passwd: Ppasswd: <password>", is masked, and the
    name's own value after it is read and masked too (found by the Cloud track)."""

    def test_the_cloud_tracks_text(self):
        self.assertEqual(mask("passwd: Ppasswd: Pw7xYzQ9mK2nL4pR end"), "passwd: [masked] [masked] end")

    def test_every_name_and_separator(self):
        names = ("passwd", "password", "db_password", "PASSWORD", "api_key", "token", "client_secret", "Ppasswd")
        for seed in range(400):
            r = random.Random(f"name-after:{seed}")
            secret = "".join(r.choice(string.ascii_letters + string.digits) for _ in range(r.randint(12, 24))) + "7"
            sep = r.choice((": ", "=", " = ", ":"))
            first, second = r.choice(names[:3] + names[7:]), r.choice(names)
            text = f"{first}{sep}{second}{sep}{secret} end"
            with self.subTest(text=text):
                masked = mask(text)
                self.assertNotIn(secret, masked)
                self.assertTrue(masked.endswith(" end"), masked)


class ReviewOneTest(unittest.TestCase):
    """The first review's findings: each text masks as main masks it, or keeps what main keeps."""

    def test_a_password_holding_a_provider_token_is_masked_whole(self):
        self.assertEqual(mask("password=SK0123456789abcdef0123456789abcdef!tail"), "password=[masked]")
        self.assertEqual(mask("api_key: dop_v1_" + "0a1b2c3d" * 8 + "~rest"), "api_key: [masked]")

    def test_a_provider_shape_inside_a_word_is_no_token_and_no_id_holds_one(self):
        from kb.mask import secret_in_id
        for text in ("TASK0123456789abcdef0123456789abcdef", "DISK0123456789abcdef0123456789abcdef.log",
                     "notes/TASK0123456789abcdef0123456789abcdef.md"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
                self.assertFalse(secret_in_id(text))

    def test_an_id_shaped_like_a_provider_token_keeps_its_row(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db = store.connect(os.path.join(tmp, "kb.sqlite"))
            try:
                sid = "notes:TASK0123456789abcdef0123456789abcdef"
                db.execute("INSERT INTO docs(kind,source,source_id,ts,title,content,metadata,masked) "
                           "VALUES(?,?,?,?,?,?,?,?)", ("doc", "notes", sid, "2026-11-01", "Plan", "task notes", "{}", "old"))
                db.commit()
                store.remask(db)
                self.assertEqual(db.execute("SELECT count(*) FROM docs").fetchone()[0], 1)
            finally:
                db.close()

    def test_secret_words_in_a_command_cost_time_in_proportion(self):
        import time
        for head in ("aws configure set ", "keytool -list -storepass", "machine x login y password"):
            took = {}
            for times in (1000, 8000):
                text = head + "secret" * times
                started = time.process_time()
                mask(text)
                took[times] = time.process_time() - started
            with self.subTest(head=head):
                self.assertLess(took[8000], 16 * max(took[1000], 0.01))

    def test_a_label_about_a_secret_keeps_what_is_typed(self):
        for label in ("Secret key description", "Tokenizer", "Secretary", "Tokens count", "API token name",
                      "Password policy text", "secretary_email"):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("mcp__playwright__browser_type", {"element": label, "text": "used by staging"}),
                                 {"element": label, "text": "used by staging"})
        for label in ("userPassword", "input[name=password]", "#passwordField", "Enter PIN", "Secret key"):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("mcp__playwright__browser_type", {"element": label, "text": "x7Kq9mZ2"})["text"],
                                 "[masked]")

    def test_pass_and_pwd_alone_keep_paths_and_words(self):
        for text in ("PWD=/workspace1", "PWD=C:\\Users\\sam\\work1", "pwd=~/code2", "pass=constant-propagation",
                     "OLDPWD=/srv2"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        for kept in ({"pwd": "/workspace1"}, {"pass": "constant-propagation"}):
            with self.subTest(kept=kept):
                self.assertEqual(mask_all(kept), kept)
        self.assertEqual(mask("Server=db;Uid=sa;Pwd=Tr0ub4dor-Zk9q;"), "Server=db;Uid=sa;Pwd=[masked];")

    def test_a_variable_after_a_command_flag_stays(self):
        for text in ("keytool -list -storepass $STOREPASS", "aws configure set aws_secret_access_key $AWS_SECRET_ACCESS_KEY",
                     "mongosh -u admin -p $MONGO_PASSWORD", "machine x login y password ${NETRC_PW}"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        self.assertEqual(mask_all({"args": ["keytool", "-storepass", "$STOREPASS"]}),
                         {"args": ["keytool", "-storepass", "$STOREPASS"]})

    def test_a_name_about_a_password_reads_as_main_reads_it(self):
        self.assertEqual(mask("password_policy=lowercase,uppercase,digits"), "password_policy=[masked],uppercase,digits")
        self.assertEqual(mask('PASSWORD_HINT="some nice kind word"'), 'PASSWORD_HINT="[masked] nice kind word"')
        self.assertEqual(mask('password_reset_url="https://x.example/r"'), 'password_reset_url="https://x.example/r"')


class ReviewTwoTest(unittest.TestCase):
    """The second review's findings, and the class the first stood for: a new reading never shows what main's own
    readings hide, whatever it reads first."""

    def test_a_short_name_inside_a_flags_value_leaves_the_value_whole(self):
        self.assertEqual(mask("--api-key K!pwd=aZ7xB9mK4qT2"), "--api-key [masked]")

    def test_every_new_shape_inside_mains_readings_hides_all_main_hides(self):
        from tests.test_mask_against_main import main, reading
        inner = ("pwd=aZ7xB9mK4qT2", "pass='Tr0ub4dor-Zk9q'", "SK0123456789abcdef0123456789abcdef", "<password>Xk9mP2qL</password>",
                 "redis://:p4ssW0rdXyz9@cache", 'password="Xk9,mP2;qL7v"', "dop_v1_" + "0a1b2c3d" * 8,
                 "secret: Ppasswd: Pw7xYzQ9mK2nL4pR", "Ocp-Apim-Subscription-Key: q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1")
        outer = ("--api-key {}", "--password {}", "api_key={}", "API_KEY={}", '{{"token": "{}"}}', "password: {}",
                 "Authorization: Token {}", "Cookie: sid={}", "curl -u sam:{} x", "token is {}")
        for o in outer:
            for i in inner:
                for glue in ("", "K!", "x,", "a:"):
                    text = o.format(glue + i) + " end"
                    with self.subTest(text=text):
                        _, main_hidden = reading(text, main.mask(text))
                        _, head_hidden = reading(text, mask(text))
                        self.assertEqual(main_hidden - head_hidden, set(), (mask(text), main.mask(text)))

    def test_an_id_split_by_an_invisible_character_reads_no_provider_token(self):
        from kb.mask import secret_in_id
        for sid in ("SK0123456789abcdef\u200b0123456789abcdef.md", "notes/dop_v1_" + "0a1b2c3d" * 4 + "\u200b" + "0a1b2c3d" * 4):
            with self.subTest(sid=sid):
                self.assertFalse(secret_in_id(sid))
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db = store.connect(os.path.join(tmp, "kb.sqlite"))
            try:
                db.execute("INSERT INTO docs(kind,source,source_id,ts,title,content,metadata,masked) VALUES(?,?,?,?,?,?,?,?)",
                           ("doc", "notes", "SK0123456789abcdef\u200b0123456789abcdef.md", "2026-11-01", "Plan", "notes",
                            "{}", "old"))
                db.commit()
                store.remask(db)
                self.assertEqual(db.execute("SELECT count(*) FROM docs").fetchone()[0], 1)
            finally:
                db.close()

    def test_with_no_new_reading_fired_the_pass_is_mains_own(self):
        # _read reads main's own readings besides only when a reading theirs lack masked something: else both agree.
        import kb.mask as m
        from tests import ordinary
        from tests.test_mask_against_main import Generated
        texts = ordinary.texts(1500, "fired") + [Generated(random.Random(f"fired:{n}")).text() for n in range(1500)]
        texts += [t for t, _ in SmallerShapesTest.MASKED] + list(SmallerShapesTest.KEPT)
        quiet = 0
        for text in texts:
            fired = []
            full = m._run(text, m._TEXT_RULES, list(range(len(text))), fired=fired)
            if not fired:
                quiet += 1
                with self.subTest(text=text):
                    self.assertEqual(full, m._run(text, m._MAIN_RULES, list(range(len(text)))))
        self.assertGreater(quiet, 2000)

    def test_labels_in_capitals_and_camel_case(self):
        for label in ("TOKENIZER", "SECRETARY", "TokenName", "PasswordHint", "SECRET_KEY_NAME", "tokenCount",
                      "PASSWORD_POLICY", "apiKeyDescription"):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("mcp__playwright__browser_type", {"element": label, "text": "used by staging"}),
                                 {"element": label, "text": "used by staging"})
        for label in ("PASSWORD", "SECRET_KEY", "userPassword", "apiKey", "ACCESS_TOKEN", "OneTimeCode", "pinCode",
                      "input[name=newPassword]"):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("mcp__playwright__browser_type", {"element": label, "text": "x7Kq9mZ2"})["text"],
                                 "[masked]")


HEADER_SECRET = "Pw7xYzQ9mK2nL4pR"
# HTTP header and form field names, and other field names made of ordinary words, for the check that only a header
# kb reads as "name: value" changes how its value is masked.
HTTP_HEADERS = ("Accept", "Accept-Encoding", "Accept-Language", "Age", "Allow", "Cache-Control", "Connection",
                "Content-Disposition", "Content-Encoding", "Content-Language", "Content-Length", "Content-Type", "Date",
                "ETag", "Expires", "Host", "If-Modified-Since", "If-None-Match", "Last-Modified", "Link", "Location",
                "Origin", "Pragma", "Range", "Referer", "Retry-After", "Server", "Strict-Transport-Security", "Upgrade",
                "User-Agent", "Vary", "Via", "Warning", "WWW-Authenticate", "X-Forwarded-For", "X-Request-Id",
                "X-Frame-Options", "X-Content-Type-Options", "Sec-Fetch-Mode", "Sec-Fetch-Site", "Sec-CH-UA", "DNT",
                "Forwarded", "Keep-Alive", "TE", "Trailer", "Transfer-Encoding", "Content-Security-Policy",
                "Access-Control-Allow-Origin", "Access-Control-Allow-Headers", "Cookie2", "X-Cookie-Consent",
                "Authorization-Scope", "Cookies", "cookie_consent", "authorization_url", "cookie_name")
HEADER_NAMES = {"cookie", "set-cookie", "authorization", "proxy-authorization"}


def field_names():
    from tests.ordinary import WORDS
    words = sorted(set(WORDS) | {"plan", "status", "message", "title", "path", "user", "email", "phone", "address",
                                 "query", "page", "limit", "offset", "sort", "filter", "lang", "region", "zone", "mode"})
    names = set(HTTP_HEADERS) | {n.lower() for n in HTTP_HEADERS} | set(HEADER_NAMES)
    names |= {"Set-Cookie", "SET-COOKIE", "Cookie", "COOKIE", "Authorization", "Proxy-Authorization"}
    for a in words:
        names |= {a, a.title(), a.upper()}
        for b in words:
            names |= {f"{a}_{b}", f"{a.title()}-{b.title()}", f"{a}{b.title()}"}
    return sorted(names)


class HeaderFieldTest(unittest.TestCase):
    """A header held as a field's key, or as a name and a value held apart, is masked as kb masks the header in text,
    "name: value" (found in the Cloud egress review); no other field name changes."""

    CASES = (
        ({"Cookie": f"session={HEADER_SECRET}"}, {"Cookie": "[masked]"}),
        ({"Set-Cookie": f"sid={HEADER_SECRET}; Path=/; HttpOnly"}, {"Set-Cookie": "[masked]"}),
        ({"cookie": f"a=1; session={HEADER_SECRET}"}, {"cookie": "[masked]"}),
        ({"Authorization": f"{HEADER_SECRET}9q"}, {"Authorization": "[masked]"}),
        ({"Proxy-Authorization": f"{HEADER_SECRET}9q"}, {"Proxy-Authorization": "[masked]"}),
        ({"headers": [{"name": "Cookie", "value": f"session={HEADER_SECRET}"}]},
         {"headers": [{"name": "Cookie", "value": "[masked]"}]}),
        ({"headers": [{"key": "Authorization", "value": f"{HEADER_SECRET}9q"}]},
         {"headers": [{"key": "Authorization", "value": "[masked]"}]}),
        ({"headers": [["Cookie", f"session={HEADER_SECRET}"]]}, {"headers": [["Cookie", "[masked]"]]}),
        ({"headers": [["Authorization", f"{HEADER_SECRET}9q"]]}, {"headers": [["Authorization", "[masked]"]]}),
        ({"headers": [("set-cookie", f"sid={HEADER_SECRET}")]}, {"headers": [["set-cookie", "[masked]"]]}),
    )

    def test_each_shape_is_masked(self):
        for value, masked in self.CASES:
            with self.subTest(value=value):
                self.assertEqual(mask_all(value), masked)

    def test_through_the_readers(self):
        for value, _ in self.CASES:
            for reader in READERS:
                with self.subTest(reader=reader, value=value):
                    text = stored(reader, tool_call(reader, "mcp__http__request", {"url": "https://api.example.com",
                                                                                    "request": value}))
                    self.assertNotIn(HEADER_SECRET, text)

    def test_only_a_header_name_changes_how_a_value_is_masked(self):
        from unittest import mock
        import kb.mask as mask_module
        values = (f"session={HEADER_SECRET}", f"{HEADER_SECRET}9q", f"Bearer {HEADER_SECRET}xYz", "text/html; charset=utf-8",
                  "en-US,en;q=0.9", "gzip, deflate", "no-cache", "1024", "keep-alive", "the launch plan for Dana")
        names = field_names()
        self.assertGreater(len(names), 10000)
        changed = set()
        for name in names:
            for value in values:
                now = mask_all({name: value})
                with mock.patch.object(mask_module, "_HEADER_NAMES", frozenset()):
                    before = mask_all({name: value})
                if now != before:
                    changed.add(name.lower())
        self.assertEqual(changed - HEADER_NAMES, set())
        self.assertEqual(changed, {"cookie", "set-cookie", "authorization", "proxy-authorization"})


class NameValueTextTest(unittest.TestCase):
    """JSON written in text that holds a name and its value in separate fields, as a request's headers, Postman's
    variables and AWS tags do, is read as the name and its value (found in the Cloud egress review); a list of two
    words, as tags are, stays."""

    S = HEADER_SECRET
    MASKED = (
        ('{"headers":[{"name":"Cookie","value":"session=%s"}]}' % S, '{"headers":[{"name":"Cookie","value":"[masked]"}]}'),
        ('{"key":"password","value":"Tr0ub4dor-Zk9q"}', '{"key":"password","value":"[masked]"}'),
        ('{"Key":"api_key","Value":"q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1"}', '{"Key":"api_key","Value":"[masked]"}'),
        ('{"value": "session=%s", "name": "Cookie"}' % S, '{"value": "[masked]", "name": "Cookie"}'),
        ('{"name":"Cookie","path":"/","secure":true,"value":"sid=%s"}' % S,
         '{"name":"Cookie","path":"/","secure":true,"value":"[masked]"}'),
        ('{"name": "Authorization", "value": "%s9q"}' % S, '{"name": "Authorization", "value": "[masked]"}'),
        ('{"NAME":"Set-Cookie","VALUE":"sid=%s; Path=/"}' % S, '{"NAME":"Set-Cookie","VALUE":"[masked]"}'),
        ('{\\"name\\":\\"Cookie\\",\\"value\\":\\"session=%s\\"}' % S,
         '{\\"name\\":\\"Cookie\\",\\"value\\":\\"[masked]\\"}'),
    )
    KEPT = ('["cookie","chocolate chip"]', '{"tags":["password","reset"]}', '{"Key":"Name","Value":"web-server-01"}',
            '{"key":"baseUrl","value":"https://api.example.com/v1"}', '{"name":"Accept","value":"text/html"}',
            '{"name":"token_type","value":"Bearer"}', '{"name":"secret_name","value":"prod-db-creds"}',
            '{"name":"Cookie","value":"yes"}', '{"key":"password","value":"string"}', '[["Cookie", "chocolate chip"]]',
            '{"name":"cookie consent","value":"accepted all"}')

    def test_a_name_and_its_value_held_apart_in_text(self):
        for text, masked in self.MASKED:
            with self.subTest(text=text):
                self.assertEqual(mask(text), masked)

    def test_ordinary_pairs_and_tags_stay(self):
        for text in self.KEPT:
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)

    def test_through_the_readers_tool_output(self):
        out = 'network: {"url":"https://api.example.com","headers":[{"name":"Cookie","value":"session=%s"}]}' % self.S
        for reader, records in (
                ("codex", codex_session({"type": "function_call", "name": "mcp__playwright__browser_network_requests",
                                         "arguments": "{}"}, {"type": "function_call_output", "output": out})),
                ("claude_code", claude_session({"type": "tool_result", "content": [{"type": "text", "text": out}]},
                                               role="user"))):
            with self.subTest(reader=reader):
                self.assertNotIn(self.S, stored(reader, records))

    def test_every_name_or_key_field_is_tried(self):
        # Cloud's round 9: with both a name and a key field, the value was left whole.
        for obj, masked_value in (({"name": "Cookie", "key": "Cookie", "value": f"session={self.S}"}, True),
                                  ({"name": "colour", "key": "password", "value": "Tr0ub4dor-Zk9q"}, True),
                                  ({"Name": "Authorization", "KEY": "x-trace", "Value": f"{self.S}9q"}, True),
                                  ({"name": "colour", "key": "shade", "value": "teal"}, False),
                                  ({"name": "colour", "key": "password", "value": "reset"}, True)):
            for compact in (True, False):
                text = json.dumps(obj, separators=(",", ":") if compact else None)
                value = next(v for k, v in obj.items() if k.lower() == "value")
                with self.subTest(text=text):
                    self.assertEqual(value not in mask(text), masked_value)
            with self.subTest(dict=obj):
                out = mask_all(obj)
                self.assertEqual(next(v for k, v in out.items() if k.lower() == "value") == "[masked]", masked_value)

    def test_fields_in_any_case_and_two_word_lists(self):
        self.assertEqual(mask_all({"Key": "api_key", "Value": "q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1"}),
                         {"Key": "api_key", "Value": "[masked]"})
        self.assertEqual(mask_all({"KEY": "password", "VALUE": "Tr0ub4dor-Zk9q"}), {"KEY": "password", "VALUE": "[masked]"})
        for kept in (["cookie", "chocolate chip"], ["Cookie", "oatmeal raisin"], ["password", "reset"],
                     ["authorization", "pending review"], {"tags": [["Cookie", "chocolate chip"]]}):
            with self.subTest(kept=kept):
                self.assertEqual(mask_all(kept), kept)
        self.assertEqual(mask_all(["Cookie", f"session={self.S}"]), ["Cookie", "[masked]"])


class ReviewThreeTest(unittest.TestCase):
    """The third review's findings."""

    def test_text_after_a_masked_pair_and_a_token_stays(self):
        text = json.dumps({"key": "password", "value": "SK0123456789abcdef0123456789abcdef"}, separators=(",", ":"))
        tail = " preserve these ordinary notes after the object"
        self.assertEqual(mask(text + tail), '{"key":"password","value":"[masked]"}' + tail)
        self.assertEqual(mask(json.dumps({"key": "api_key", "value": "x"}) + " then glpat-" + "a1b2" * 5 + " end"),
                         json.dumps({"key": "api_key", "value": "x"}) + " then [masked] end")

    def test_a_pair_about_a_password_stays(self):
        for obj in ({"key": "password_policy", "value": "lowercase,uppercase,digits"},
                    {"name": "PASSWORD_HINT", "value": "some nice kind word"}, {"key": "token_type", "value": "Bearer"},
                    {"name": "passwordResetUrl", "value": "https://x.example/reset"}):
            with self.subTest(obj=obj):
                text = json.dumps(obj, separators=(",", ":"))
                self.assertEqual(mask(text), text)
                self.assertEqual(mask_all(obj), obj)

    def test_a_label_about_a_count_keeps_the_number(self):
        for label in ("Max tokens", "Token budget", "Token limit", "Tokens per minute", "Maximum token count",
                      "max_tokens", "tokenQuota", "Context window tokens"):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("mcp__playwright__browser_type", {"element": label, "text": "2048"}),
                                 {"element": label, "text": "2048"})
        self.assertEqual(typed_secrets("mcp__playwright__browser_type", {"element": "PIN", "text": "2048"})["text"],
                         "[masked]")

    def test_colour_codes_in_a_value_do_not_make_it_a_credential(self):
        for form in FORMS:
            c = FORMS[form]
            for text in (f"pass=constant-{c}31mpropagation{c}0m", f"PWD={c}1m/workspace1{c}0m", f"pwd=/srv/app{c}0m"):
                with self.subTest(text=text):
                    self.assertEqual(mask(text), text)
        # A colour code glued to a value goes with it, as main's own reading of "password=" takes it.
        self.assertNotIn("Tr0ub4dor", mask("pwd=Tr0ub4dor-Zk9q\x1b[0m"))


class ReviewFourTest(unittest.TestCase):
    """The fourth review's findings."""

    def test_a_label_asks_for_a_secret_by_its_last_word(self):
        for label, value in (("One-time donation", "50"), ("Secret key algorithm", "RSA"), ("Token decimals", "18"),
                             ("Password strength meter", "strong"), ("Token symbol", "ETH"), ("PIN length", "6"),
                             ("Secret Santa list", "Dana"), ("Access token lifetime", "3600")):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("mcp__playwright__browser_type", {"element": label, "text": value}),
                                 {"element": label, "text": value})
        for label in ("One-time code", "One time password", "Secret key", "PIN code", "2FA code", "Password field",
                      "Enter your password here", "API token (required)", "OTP", "Confirm new password",
                      "input[name=secret_key]", "pinCode", "Password edit box", "Secret recovery phrase", "Seed phrase"):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("mcp__playwright__browser_type", {"element": label, "text": "x7Kq9mZ2"})["text"],
                                 "[masked]")

    def test_code_properties_are_no_vault_token(self):
        for text in ("let s = {}; s.normalizedIdentifier2026 = true;", "s.ConfigurationVersion2026",
                     "if (s.initializationProgress42) {"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        self.assertNotIn("hvs.", mask("vault token hvs." + "CAESIa1B2c3D4e5Fg6H7i8J9k0LmNoPq" * 3 + " end"))

    def test_ordinary_words_in_xml_elements_stay(self):
        for text in ("<tokens><token>internationalization</token></tokens>", "<secret>unbelievably-ordinary</secret>",
                     "<token>counterrevolutionaries</token>"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        self.assertEqual(mask("<token>q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1</token>"), "<token>[masked]</token>")
        self.assertEqual(mask("<password>Tr0ub4dor-Zk9q</password>"), "<password>[masked]</password>")

    def test_an_option_after_a_password_flag_is_no_password(self):
        for text in ("mongosh -p --quiet", "mongosh -u admin -p --authenticationDatabase admin", "az login -p --help",
                     "keytool -list -storepass -v"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        self.assertEqual(mask_all({"args": ["mongosh", "-p", "--quiet"]}), {"args": ["mongosh", "-p", "--quiet"]})

    def test_a_coloured_variable_after_a_command_flag_stays(self):
        for text in ("keytool -storepass \x1b[31m$STOREPASS\x1b[0m -list",
                     "aws configure set aws_secret_access_key \x1b[1m$AWS_SECRET_ACCESS_KEY\x1b[0m"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)


# Commands as an argument list, each flag's value its own item: (the list, the items masked).
ARGV = (
    (["login", "--password", PASSWORD], {2}), (["--token", SUB], {1}), (["--api-key", SUB, "--verbose"], {1}),
    (["mongosh", "-u", "admin", "-p", PASSWORD], {4}), (["docker", "login", "-u", "sam", "-p", PASSWORD, "ghcr.io"], {5}),
    (["az", "login", "-u", "me@x.com", "-p", PASSWORD], {5}), (["keytool", "-list", "-storepass", PASSWORD], {3}),
    (["redis-cli", "-h", "cache", "-a", PASSWORD, "ping"], {4}), (["sshpass", "-p", PASSWORD, "ssh", "host"], {2}),
    (["--message", "hello world"], set()), (["--password-file", "/run/secrets/db"], set()), (["-p", "8080"], set()),
    (["git", "commit", "-m", "fix the token refresh"], set()), (["--token", "$GITHUB_TOKEN"], set()),
    (["psql", "-h", "db", "-p", "5432"], set()), (["ssh", "-p", "2222", "host"], set()), (["--token"], set()),
    (["-H", f"Authorization: Bearer {SUB}"], "header"),
)


def argv(r):
    """A command as an argument list with a secret flag's value, or none, and which items are secret."""
    secret = "".join(r.choice(string.ascii_letters + string.digits) for _ in range(r.randint(10, 24))) + "7"
    head, flag = r.choice(((["login"], "--password"), (["run"], "--api-key"), (["deploy"], "--client-secret"),
                           (["mongosh", "-u", "admin"], "-p"), (["docker", "login", "-u", "sam"], "-p"),
                           (["keytool", "-list"], "-storepass"), (["sync"], "--token"), (["az", "login"], "-p")))
    plain = r.choice((["--port", "8080"], ["-v"], ["--name", "pelican"], ["-m", "a plan"], ["--token-file", "/t"]))
    if r.random() < 0.3:
        return head + plain, set()
    items = head + [flag, secret] + (plain if r.random() < 0.5 else [])
    return items, {len(head) + 1}


class ArgumentListTest(unittest.TestCase):
    """A flag and its value in two items of a list, as a tool's input or metadata holds a command: the item after a
    secret flag is masked whole, and nothing else."""

    def check(self, items, secret):
        masked = mask_all({"args": items})["args"]
        if secret == "header":
            self.assertEqual(masked, ["-H", "Authorization: [masked]"], "as main masks it")
            return
        self.assertEqual([i for i, item in enumerate(masked) if item != items[i]], sorted(secret), masked)
        for i in secret:
            self.assertEqual(masked[i], "[masked]")

    def test_the_audits_lists_and_others(self):
        for items, secret in ARGV:
            with self.subTest(items=items):
                self.check(items, secret)

    def test_generated_argument_lists(self):
        for seed in range(500):
            items, secret = argv(random.Random(f"argv:{seed}"))
            with self.subTest(items=items):
                self.check(items, secret)

    def test_both_readers_store_the_list_masked(self):
        for reader in READERS:
            for items, secret in ARGV[:9]:
                with self.subTest(reader=reader, items=items):
                    text = stored(reader, tool_call(reader, "mcp__runner__exec", {"args": items}))
                    for i in secret:
                        self.assertNotIn(items[i], text)
                    self.assertIn(items[0], text)

if __name__ == "__main__":
    unittest.main()
