"""The gate 9 re-check's findings (2026-11-02, at main 4d7a130), each masked as a reader stores it: a token behind a
terminal colour code, a password an agent types into a page, and the shapes after them. Made-up secrets only."""
import json
import os
import random
import string
import tempfile
import time
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
FORMS = {"raw": "\x1b[", "json": "\\u001b[", "python": "\\x1b[", "octal": "\\033[", "eight-bit": "\x9b",
         "eight-bit json": "\\u009b", "eight-bit python": "\\x9b"}


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
        self.assertNotIn("Tr0ub4dor", mask("uid=sa;pwd=Tr0ub4dor-Zk9q\x1b[0m"))  # beside a user (ReviewSevenTest)


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
        # A bare <token> is a tokenizer's or a lexer's as often (ReviewFiveTest); a token's own element is read.
        self.assertEqual(mask("<auth_token>q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1</auth_token>"), "<auth_token>[masked]</auth_token>")
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


class ReviewFiveTest(unittest.TestCase):
    """The fifth review's findings."""

    def test_a_token_or_pin_of_another_kind_needs_a_credential(self):
        for label, value in (("CSS color token", "#ff8800"), ("Map pin", "40.7128,-74.0060"), ("Design token", "spacing-4"),
                             ("Output tokens", "4096"), ("Drop a pin", "Main St"), ("Lexer token", "IDENTIFIER"),
                             ("Tokens", "4096")):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("mcp__playwright__browser_type", {"element": label, "text": value}),
                                 {"element": label, "text": value})
        for label, value in (("Discord token", "q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1"), ("Card PIN", "4821"), ("Bearer token", "abc12"),
                             ("Enter the token", "abc12"), ("PIN", "4821"), ("Your 4-digit PIN", "4821"),
                             ("Personal access token", "abc12"), ("GitHub token", "abc12"), ("HuggingFace token", "abc12"),
                             ("API tokens", "abc12")):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("mcp__playwright__browser_type", {"element": label, "text": value})["text"],
                                 "[masked]")
        args = json.dumps({"element": "CSS color token", "ref": "e3", "text": "#ff8800"})
        self.assertIn("#ff8800", codex._call_args(args, "mcp__playwright__browser_type"))

    def test_a_tokenizers_identifiers_in_xml_stay(self):
        for text in ("<tokens><token>HTTPStatus200</token><token>UTF8Identifier</token></tokens>",
                     "<token>Base64Encoder2026</token>", "<xs:token>IPv6Address42x</xs:token>"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        for text in ("<accessToken>q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1</accessToken>",
                     "<SessionToken>q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1</SessionToken>", "<api_token>q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1</api_token>"):
            with self.subTest(text=text):
                self.assertIn(">[masked]</", mask(text))

    def test_a_coloured_option_after_a_password_flag_is_no_password(self):
        for text in ("mongosh -p \x1b[31m--quiet\x1b[0m", "keytool -list -storepass \x1b[1m-v\x1b[0m",
                     "mongosh -p \\u001b[31m--quiet\\u001b[0m"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        args = {"args": ["mongosh", "-p", "\x1b[31m--quiet\x1b[0m"]}
        self.assertEqual(mask_all(args), args)
        self.assertEqual(mask("mongosh -p \x1b[31mTr0ub4dor-Zk9q\x1b[0m"), "mongosh -p [masked]")

    def test_a_colour_code_after_a_placeholder_is_a_break(self):
        for code in ("\x1b[0m", "\x9b0m", "\\u001b[0m", "\\x1b[0m", "\\033[0m", "\x7f", "\x81"):
            text = f"[masked]{code}README.md"
            with self.subTest(code=code):
                self.assertEqual(mask(text), text)
        self.assertEqual(mask("[masked]\u200bREADME.md"), "[masked].md")  # an older kb's leftover, as main reads it
        with tempfile.TemporaryDirectory() as tmp:
            db = store.connect(os.path.join(tmp, "kb.sqlite"))
            try:
                content = "built [masked]\x1b[0mREADME.md"
                db.execute("INSERT INTO docs(kind,source,source_id,ts,title,content,metadata,masked) "
                           "VALUES(?,?,?,?,?,?,?,?)", ("doc", "notes", "notes:a", "2026-11-01", "Plan", content, "{}", "old"))
                db.commit()
                store.remask(db)
                self.assertEqual(db.execute("SELECT content FROM docs").fetchone()[0], content)
            finally:
                db.close()


class ReviewSixTest(unittest.TestCase):
    """The sixth review's findings."""

    def test_an_older_kbs_leftover_before_a_colour_code_is_masked_as_main_masks_it(self):
        for code in ("\x1b[0m", "\x9b0m", "\\u001b[0m", "\\x1b[0m", "\\033[0m", "\x7f", "\x81"):
            with self.subTest(code=code):
                self.assertEqual(mask(f"[masked]\u200bTr0ub4dor-Zk9q{code}done"), f"[masked]{code}done")
                self.assertEqual(mask(f"[masked]{code}README.md"), f"[masked]{code}README.md")
        self.assertEqual(mask("[masked]\u200b_key: Tr0ub\x1b[0mdone"), "[masked]")

    def test_a_split_name_after_a_command_value_keeps_its_value_masked(self):
        for text in ("mongosh -p Tr0ub4dor-Zk9q\x1b[0mapi_k\u200bey: L4terSecret-Xq9",
                     "mongosh -p Tr0ub4dor-Zk9qapi_k\u200bey: L4terSecret-Xq9",
                     "aws configure set aws_secret_access_key Tr0ub4dor-Zk9qapi_k\u200bey: L4terSecret-Xq9",
                     "keytool -storepass Tr0ub4dor-Zk9q\x1b[0mpass\u200bword=L4terSecret-Xq9"):
            with self.subTest(text=text):
                self.assertNotIn("L4terSecret", mask(text))
                self.assertNotIn("Tr0ub4dor", mask(text))

    def test_a_token_of_another_kind_keeps_an_identifier(self):
        for label, value in (("Lexer token", "HTTPStatus200"), ("Figma token", "Base64Encoder2026"),
                             ("Map pin", "Pier39Entrance2")):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("browser_type", {"element": label, "text": value}),
                                 {"element": label, "text": value})
        call = json.dumps([{"function": {"name": "browser_type",
                                         "arguments": json.dumps({"element": "Lexer token", "text": "HTTPStatus200"})}}])
        self.assertIn("HTTPStatus200", hermes._tool_calls(call)[0])

    def test_an_aws_setting_about_a_secret_is_no_secret(self):
        for text in ("aws configure set password_policy MinimumLength12", "aws configure set token_expiration 3600",
                     "aws configure set web_identity_token_file /var/run/token", "aws configure set token_type Bearer2"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        self.assertEqual(mask("aws configure set aws_session_token Tr0ub4dor-Zk9qL4ter"),
                         "aws configure set aws_session_token [masked]")


class ReviewSevenTest(unittest.TestCase):
    """The seventh review's findings."""

    def test_a_split_name_after_a_placeholder_and_a_colour_code_keeps_its_value_masked(self):
        for code in ("\x1b[0m", "\x9b0m", "\\u001b[0m", "\\x1b[0m", "\\033[0m", "\x7f", "\x81"):
            with self.subTest(code=code):
                self.assertEqual(mask(f"[masked]{code}api_k\u200bey: L4terSecret-Xq9"),
                                 f"[masked]{code}api_k\u200bey: [masked]")

    def test_no_reading_on_at_a_name_about_a_secret(self):
        for text, masked in (("password: password_hash: SHA256", "password: [masked] SHA256"),
                             ("password: password_policy: strict2026", "password: [masked] strict2026"),
                             ("secret: api_key_name: billing", "secret: api_key_name: billing"),
                             ("passwd: Ppasswd: Pw7xYzQ9mK2nL4pR end", "passwd: [masked] [masked] end")):
            with self.subTest(text=text):
                self.assertEqual(mask(text), masked)

    def test_pass_alone_names_a_password_only_beside_a_user(self):
        for text in ('compiler pass="ConvertBF16ToF32" changed=0', "pass=ConvertBF16ToF32", "pass: 'LowerSCF2LLVM'",
                     "pwd=Base64Encoder2026"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        for kept in ({"pass": "ConvertBF16ToF32"}, {"name": "pass", "value": "ConvertBF16ToF32"},
                     ["pass", "ConvertBF16ToF32"], {"pwd": "Base64Encoder2026", "changed": 0}):
            with self.subTest(kept=kept):
                self.assertEqual(mask_all(kept), kept)
        for text, masked in (("user=sam pass=Tr0ub4dor-Zk9q", "user=sam pass=[masked]"),
                             ("auth:\n  user: me@example.com\n  pass: 'Tr0ub4dor-Zk9q'",
                              "auth:\n  user: me@example.com\n  pass: '[masked]'"),
                             ("Server=db;Uid=sa;Pwd=Tr0ub4dor-Zk9q;", "Server=db;Uid=sa;Pwd=[masked];")):
            with self.subTest(text=text):
                self.assertEqual(mask(text), masked)
        self.assertEqual(mask_all({"login": "sam", "pass": "Tr0ub4dor-Zk9q"}), {"login": "sam", "pass": "[masked]"})


class ReviewEightTest(unittest.TestCase):
    """The eighth review's findings."""

    def test_pass_names_a_password_only_as_the_field_next_to_a_user(self):
        for text in ('user=builder compiler pass="ConvertBF16ToF32" changed=0',
                     "user: builder\n\ncompiler pass=ConvertBF16ToF32 changed=1", "login=ci; ran pass=LowerSCF2LLVM"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        for text, masked in (("User ID=sa;PWD=Tr0ub4dor-Zk9q;", "User ID=sa;PWD=[masked];"),
                             ("pass: 'Tr0ub4dor-Zk9q', user: 'me@example.com'", "pass: '[masked]', user: 'me@example.com'"),
                             ('{"user": "sa", "pwd": "Tr0ub4dor-Zk9q"}', '{"user": "sa", "pwd": "[masked]"}')):
            with self.subTest(text=text):
                self.assertEqual(mask(text), masked)

    def test_a_field_that_searches_or_names_secrets_is_no_secrets_field(self):
        for label, value in (("Search secrets", "billing"), ("Filter tokens", "gpt"), ("Find passwords", "bank"),
                             ("Rename token", "deploy-bot"), ("Generate password", "16")):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("browser_type", {"element": label, "text": value}),
                                 {"element": label, "text": value})
        args = json.dumps({"element": "Search secrets", "ref": "e23", "text": "billing"})
        self.assertIn("billing", codex._call_args(args, "mcp__playwright__browser_type"))

    def test_base64_of_ordinary_json_is_no_grafana_key(self):
        import base64
        text = base64.b64encode(b'{"k":"theme","v":"light","description":"ordinary interface preferences"}').decode()
        self.assertEqual(mask(text), text)


class ReviewNineTest(unittest.TestCase):
    """The ninth review's findings."""

    def test_a_blank_line_parts_a_pass_from_a_user(self):
        for text in ('user: builder\n\npass="ConvertBF16ToF32" changed=0', 'pass="ConvertBF16ToF32"\n\nuser: builder',
                     'user: builder\\n\\npass=\\"ConvertBF16ToF32\\"', "user: builder\r\n\r\npass=LowerSCF2LLVM"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        self.assertEqual(mask("user: me@example.com\npass: 'Tr0ub4dor-Zk9q'"), "user: me@example.com\npass: '[masked]'")
        self.assertEqual(mask("user: me@example.com\\npass: 'Tr0ub4dor-Zk9q'"), "user: me@example.com\\npass: '[masked]'")

    def test_a_search_before_a_joining_word_still_searches(self):
        for label, value in (("Search for passwords", "billing"), ("Filter by password", "bank"),
                             ("Look up tokens by name", "deploy")):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("browser_type", {"element": label, "text": value}),
                                 {"element": label, "text": value})
        # A label that names anything besides the secret is about something else (ReviewTwelveTest).
        self.assertEqual(typed_secrets("browser_type", {"element": "Enter password to continue",
                                                        "text": "x7Kq9mZ2"})["text"], "[masked]")

    def test_a_long_label_costs_time_in_proportion(self):
        import time

        def took(label):
            best = None
            for _ in range(3):
                started = time.process_time()
                typed_secrets("browser_type", {"element": label, "text": "billing"})
                spent = time.process_time() - started
                best = spent if best is None else min(best, spent)
            return best

        for unit in ("ordinary ", "password ", "search password ", "token ", "api key "):
            with self.subTest(unit=unit):
                small, large = took(unit * 2000), took(unit * 16000)
                self.assertLess(large, 20 * small + 0.05, (unit, small, large))


class ReviewTenTest(unittest.TestCase):
    """The tenth review's findings."""

    def test_a_search_reads_on_across_a_mark(self):
        for label, value in (("Search (passwords)", "billing"), ("Filter (password)", "bank"), ("Search: password", "x9")):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("browser_type", {"element": label, "text": value}),
                                 {"element": label, "text": value})
        for label in ("Password (required)", "Password: *", "Enter API token (required)"):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("browser_type", {"element": label, "text": "x7Kq9mZ2"})["text"], "[masked]")

    def test_a_name_split_by_a_colour_code_is_read_strictly(self):
        for code, reset in (("\x1b[31m", "\x1b[0m"), ("\x1b[01;31m\x1b[K", "\x1b[m\x1b[K"), ("\\u001b[31m", "\\u001b[0m"),
                            ("\x7f", "\x7f")):
            for text in (f"password_{code}policy{reset}: uppercase-required", f"secret_{code}name{reset}: production-database-v2",
                         f"api_key_{code}name{reset}: billing-service-2026", f"x-auth{code}-flow{reset}: implicit-grant-v2"):
                with self.subTest(text=text):
                    self.assertEqual(mask(text), text)
            for text, secret in ((f"DB_{code}PASS{reset}WORD=Tr0ub4dor-Zk9q", "Tr0ub4dor"),
                                 (f"pass{code}word{reset}=Tr0ub4dor-Zk9q", "Tr0ub4dor"),
                                 (f"api_{code}key{reset}: q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1", "q8Zr2Lm7")):
                with self.subTest(text=text):
                    self.assertNotIn(secret, mask(text))
        self.assertEqual(mask("password_\u200bpolicy: uppercase-required"), "password_\u200bpolicy: [masked]")  # as main

    def test_a_selector_of_repeated_prefixes_costs_time_in_proportion(self):
        import time

        def took(selector):
            best = None
            for _ in range(3):
                started = time.process_time()
                typed_secrets("browser_type", {"selector": selector, "value": "billing"})
                spent = time.process_time() - started
                best = spent if best is None else min(best, spent)
            return best

        for unit in ("data-", "aria-", "name-", "data-x "):
            with self.subTest(unit=unit):
                small, large = took(unit * 1000), took(unit * 8000)
                self.assertLess(large, 20 * small + 0.05, (unit, small, large))


class ReviewElevenTest(unittest.TestCase):
    """The eleventh review's findings."""

    def test_a_token_or_pin_of_another_kind_across_the_label(self):
        for label, value in (("Token (CSS)", "color.primary"), ("Token (lexer)", "IDENT"), ("Pin (map)", "40.71,-74.00"),
                             ("Token - design system", "spacing-4"), ("PIN: map marker", "Pier 39")):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("browser_type", {"element": label, "text": value}),
                                 {"element": label, "text": value})
        for label in ("Token (required)", "PIN (4 digits)", "Your PIN", "Bot token (Discord)"):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("browser_type", {"element": label, "text": "4821"})["text"], "[masked]")

    def test_an_identifier_is_no_credential_in_the_new_readings(self):
        for text in ("x-auth\x1b[31m-flow\x1b[0m: OAuth2AuthorizationCode", "user=ci pass=ConvertBF16ToF32",
                     "user=ci pass=OAuth2AuthorizationCode", "user=ci pass=HTTPStatus200Handler"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        for text, secret in (("user=ci pass=Tr0ub4dor-Zk9q", "Tr0ub4dor"), ("user=ci pass=L4terSecret-Xq9", "L4ter"),
                             ("DB_\x1b[01;31mPASS\x1b[mWORD=L4terSecret-Xq9", "L4ter"),
                             ("api_\x1b[1mkey\x1b[0m: q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1", "q8Zr2Lm7")):
            with self.subTest(text=text):
                self.assertNotIn(secret, mask(text))

    def test_reading_on_only_at_a_name_that_ends_in_a_secret(self):
        for text, masked in (("password: passwordAlgorithm: bcrypt", "password: [masked] bcrypt"),
                             ("password: password_description: strong", "password: [masked] strong"),
                             ("passwd: Ppasswd: Pw7xYzQ9mK2nL4pR end", "passwd: [masked] [masked] end")):
            with self.subTest(text=text):
                self.assertEqual(mask(text), masked)

    def test_an_ordinary_typing_call_renders_as_main_renders_it(self):
        args = {"element": "Search " + "documentation " * 30, "ref": "e15", "text": "billing-invoice-query"}
        self.assertEqual(codex._call_args(args, "browser_type"), "billing-invoice-query")
        self.assertIn("[masked]", codex._call_args({"element": "Password", "ref": "e3", "text": "x7Kq9mZ2"}, "browser_type"))


class ReviewTwelveTest(unittest.TestCase):
    """The twelfth review's findings."""

    def test_a_later_pass_hides_all_main_hides(self):
        # A new reading masks a part of a split password's value in one pass; main masks all of it in the next.
        text = "pass\u00adword=Ab3D|redis://:X7y8@Z9q0Secret"
        for part in ("Ab3D", "X7y8", "Z9q0Secret"):
            self.assertNotIn(part, mask(text))
        import kb.mask as m
        for name in ("pass\u00adword=", "api_k\u200bey: ", "tok\u200ben="):
            for inner in ("redis://:X7y8@", "pass=X7y8;user=me ", "SK0123456789abcdef0123456789abcdef", "<password>X7y8</password>"):
                text = f"{name}Ab3D|{inner}Z9q0Secret end"
                with self.subTest(text=text):
                    hidden = m._main_hidden(text)
                    masked, origin = m.mask_with_origin(text)
                    shown = {c for c in origin if c >= 0}
                    self.assertFalse([i for i in range(len(text)) if hidden[i] and i in shown], masked)
                    self.assertEqual(mask(text), masked)

    def test_a_label_names_a_secret_and_nothing_else(self):
        for label, value in (("Password (algorithm)", "bcrypt"), ("Password (hash)", "sha256"),
                             ("Password (regex)", "^.{8,}$"), ("Password rotation days", "90")):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("browser_type", {"element": label, "text": value}),
                                 {"element": label, "text": value})
        args = json.dumps({"element": "Password (algorithm)", "ref": "e7", "text": "bcrypt"})
        self.assertEqual(codex._call_args(args, "browser_type"), "bcrypt")
        for label in ("Password", "Password (required)", "Confirm new password", "Enter your password here",
                      "Password for admin account", "Enter password to continue", "#login-password",
                      "input.form-control[name=password]"):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("browser_type", {"element": label, "text": "x7Kq9mZ2"})["text"], "[masked]")

    def test_an_identifier_in_a_header_list_stays(self):
        for kept in ({"tags": ["Authorization", "OAuth2AuthorizationCode"]}, {"tags": ["authorization", "HTTPStatus200"]}):
            with self.subTest(kept=kept):
                self.assertEqual(mask_all(kept), kept)
        self.assertEqual(mask_all({"headers": [["Authorization", "Bearer q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1"]]}),
                         {"headers": [["Authorization", "[masked]"]]})


class ReviewThirteenTest(unittest.TestCase):
    """The thirteenth review's findings."""

    def test_a_split_token_before_a_colour_code_is_masked_as_main_masks_it(self):
        from kb.mask import secret_in_id
        for code in ("\x1b[0m", "\x9b0m", "\\u001b[0m", "\\x1b[0m", "\\033[0m", "\x7f"):
            text = f"AKIAQX7RZ2\u200bMPL4VN8KTB{code}Suffix"
            with self.subTest(code=code):
                self.assertEqual(mask(text), f"[masked]{code}Suffix")
                self.assertTrue(secret_in_id(text))

    def test_main_own_view_breaks_only_where_main_reads(self):
        masked = mask("sk-Ab3Cd5\u200bsubscription_key=X7y8Z9q0Secret")
        self.assertNotIn("Ab3Cd5", masked)
        self.assertNotIn("X7y8Z9q0", masked)

    def test_a_word_after_the_secret_makes_it_a_field_about_it(self):
        for label, value in (("Token type", "refresh_token"), ("Password type", "text"), ("Token service", "auth-svc"),
                             ("Token time", "3600"), ("Password recovery", "me@example.com"), ("PIN device", "kiosk-2")):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("browser_type", {"element": label, "text": value}),
                                 {"element": label, "text": value})
        for label in ("Bot token (Discord)", "One-time code", "Secret recovery phrase", "Seed phrase", "Password for admin account",
                      "Discord bot token", "API token", "2FA code", "Secret key"):
            with self.subTest(label=label):
                self.assertEqual(typed_secrets("browser_type", {"element": label, "text": "x7Kq9mZ2"})["text"], "[masked]")


class ReviewFourteenTest(unittest.TestCase):
    """The fourteenth review's findings, under the coordinator's bar."""

    def test_a_quoted_password_holds_a_pipe_or_a_semicolon(self):
        for text, masked in (("az login -u me@example.com -p 'Tr0ub4dor|Zk9q'", "az login -u me@example.com -p '[masked]'"),
                             ('mongosh -u admin -p "Tr0ub4dor;Zk9q"', 'mongosh -u admin -p "[masked]"'),
                             ("keytool -list -storepass 'Tr0ub4dor|Zk9q' -v", "keytool -list -storepass '[masked]' -v")):
            with self.subTest(text=text):
                self.assertEqual(mask(text), masked)
        self.assertEqual(mask("az login -u me -p $AZ_PW | tee login.log"), "az login -u me -p $AZ_PW | tee login.log")

    def test_an_escaped_eight_bit_colour_code_before_a_token(self):
        for printed in (json.dumps("\x9b31mAKIAQX7RZ2MPL4VN8KTB"), repr("\x9b31mAKIAQX7RZ2MPL4VN8KTB")):
            with self.subTest(printed=printed):
                self.assertNotIn("AKIAQX7RZ2MPL4VN8KTB", mask(printed))

    def test_a_name_about_a_subscription_key_keeps_its_value(self):
        for text in ("subscription_key_header_name: Ocp-Apim-Subscription-Key",
                     "subscription_key_name: billing-service-2026", "subscriptionKeyHeader: Ocp-Apim-Subscription-Key"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        for text in ("Ocp-Apim-Subscription-Key: q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1", "subscription_key=q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1",
                     "subscriptionKey: q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1"):
            with self.subTest(text=text):
                self.assertNotIn("q8Zr2Lm7", mask(text))


class ReviewFifteenTest(unittest.TestCase):
    """The fifteenth review's findings, under the coordinator's bar."""

    def test_a_bare_credential_is_judged_as_main_judges_it_too(self):
        self.assertEqual(mask("Authorization: QmXvLpBrTzNy\x1b[0m"), "Authorization: [masked]")
        import kb.mask as m
        for code in FORMS.values():
            text = f"Authorization: QmXvLpBrTzNy{code}0m and more"
            with self.subTest(code=code):
                hidden = m._main_hidden(text)
                _, origin = m.mask_with_origin(text)
                self.assertFalse([i for i in range(len(text)) if hidden[i] and i in set(origin)], mask(text))

    def test_a_json_escaped_quote_opens_a_command_password(self):
        for command, secret in (('az login -p "Tr0ub4dor-Zk9q"', "Tr0ub4dor"), ('mongosh -u admin -p "Tr0ub4dor|Zk9q"', "Zk9q"),
                                ('keytool -list -storepass "Tr0ub4dor-Zk9q" -v', "Tr0ub4dor"),
                                ("az login -p 'Tr0ub4dor|Zk9q'", "Zk9q")):
            with self.subTest(command=command):
                self.assertNotIn(secret, mask(json.dumps(command)))
                self.assertNotIn(secret, mask(command))

    def test_an_escaped_quote_does_not_cross_a_pipe(self):
        for command in ('mongosh --eval "db.stats()" | ssh -p 2222 host', "mongosh --eval 'db.stats()' | ssh -p 2222 host",
                        'twine upload --comment "a|b" dist/* ; ssh -p 2222 host'):
            with self.subTest(command=command):
                self.assertEqual(mask(json.dumps(command)), json.dumps(command))
                self.assertEqual(mask(command), command)

    def test_a_json_command_line_costs_time_in_proportion(self):
        import time

        def took(n):
            text = json.dumps({"cmd": 'mongosh --eval "db.items.insertMany(['
                               + ",".join('{name:\\"item%d\\"}' % i for i in range(n)) + '])"\n'})
            best = None
            for _ in range(3):
                started = time.process_time()
                self.assertEqual(mask(text), text)
                spent = time.process_time() - started
                best = spent if best is None else min(best, spent)
            return best

        small, large = took(500), took(4000)
        self.assertLess(large, 20 * small + 0.05, (small, large))


class ReviewSixteenTest(unittest.TestCase):
    """The sixteenth review's findings, under the coordinator's bar."""

    def test_a_command_line_ends_where_the_shell_ends_it(self):
        for command in ("mongosh --quiet && ssh -p 2222 host", "mongosh --quiet & ssh -p 2222 host",
                        "(mongosh --quiet) ; ssh -p 2222 host", "mongosh --quiet > out.log; ssh -p 2222 host"):
            with self.subTest(command=command):
                self.assertEqual(mask(command), command)
                self.assertEqual(mask(json.dumps(command)), json.dumps(command))
        fields = json.dumps({"cmd": "mongosh --quiet", "next": "ssh -p 2222 host"})
        self.assertEqual(mask(fields), fields)

    def test_a_single_quote_does_not_decide_how_a_line_is_escaped(self):
        masked = mask(json.dumps("az login -u 'me@example.com' -p \"Tr0ub4dor|Zk9q\""))
        self.assertNotIn("Tr0ub4dor", masked)
        self.assertNotIn("Zk9q", masked)

    def test_a_backslash_n_inside_quotes_is_part_of_the_password(self):
        masked = mask("az login -p 'Tr0ub4dor\\new9'")
        self.assertNotIn("Tr0ub4dor", masked)
        self.assertNotIn("ew9", masked)

    def test_an_escaped_invisible_character_splits_a_token(self):
        token = "ghp_Zq7Lm2Xc9Vb4Nn8Kk3Jj6Hh1Gg5Ff0Dd2Ss"
        for ch in ("\x7f", "\u200b", "\u00ad", "\x00", "\u2060", "\x81"):
            split = token[:15] + ch + token[15:]
            for printed in (json.dumps({"output": split}), repr(split)):
                with self.subTest(printed=printed):
                    self.assertNotIn("4Nn8Kk3Jj6", mask(printed))
                    self.assertNotIn("Zq7Lm2Xc9Vb", mask(printed))

    def test_a_form_encoded_private_key(self):
        from urllib.parse import quote_plus
        text = "https://example.test/?data=" + quote_plus("-----BEGIN PRIVATE KEY-----\nQm8Xv2Lp9Br5Tz7Ny3Rf6Ws1\n"
                                                         "-----END PRIVATE KEY-----")
        self.assertNotIn("Qm8Xv2Lp9Br5", mask(text))

    def test_an_app_password_after_a_yaml_colon(self):
        self.assertEqual(mask("APP_PASSWORD: qjrx vnmw kzpt bhfc"), "APP_PASSWORD: [masked]")
        self.assertEqual(mask("smtp:\n  app_password: qjrx vnmw kzpt bhfc  # mail\n"),
                         "smtp:\n  app_password: [masked]  # mail\n")
        # Inside a sentence the four groups are not read whole; main's own reading takes the first word, as before.
        self.assertEqual(mask("I asked about the password: when will they come"),
                         "I asked about the password: [masked] will they come")


class ReviewSeventeenTest(unittest.TestCase):
    """The seventeenth review's findings, under the coordinator's split bar."""

    def test_a_name_that_starts_with_token_is_no_tokenizer(self):
        self.assertEqual(mask("TokenIssuerSigningKey=Qx7rT2mPa9LkW3vZ8nB4"), "TokenIssuerSigningKey=[masked]")
        self.assertEqual(mask("tokenizer: cl100k_base"), "tokenizer: cl100k_base")  # the stated exception
        self.assertEqual(mask("tokenizers=hf2026v1x"), "tokenizers=hf2026v1x")

    def test_a_redirection_does_not_end_a_command(self):
        for text in ("az login 2>/dev/null -u me@example.com -p Tr0ub4dor9", "mongosh < init.js -p Tr0ub4dor9"):
            with self.subTest(text=text):
                self.assertNotIn("Tr0ub4dor9", mask(text))

    def test_a_closing_shell_quote_before_a_colon_is_no_json_end(self):
        for text in ('mongosh --host "$MONGO_HOST":27017 -p Tr0ub4dor9', "mongosh --host '$H':27017 -p Tr0ub4dor9"):
            with self.subTest(text=text):
                self.assertNotIn("Tr0ub4dor9", mask(text))
        fields = json.dumps({"cmd": "mongosh --quiet", "next": "ssh -p 2222 host"})
        self.assertEqual(mask(fields), fields)

    def test_a_sentence_after_a_password_line_is_read_as_main_reads_it(self):
        for text, masked in (("password: read from yaml file", "password: [masked] from yaml file"),
                             ("password: copy into json file", "password: [masked] into json file")):
            with self.subTest(text=text):
                self.assertEqual(mask(text), masked)
        self.assertEqual(mask("APP_PASSWORD: qjrx vnmw kzpt bhfc"), "APP_PASSWORD: [masked]")

    def test_a_netrc_password_of_letters(self):
        self.assertEqual(mask("machine api.example.com login deploy password qjrxvnmwkzptbhfc"),
                         "machine api.example.com login deploy password [masked]")

    def test_an_invisible_character_inside_an_app_password(self):
        for text in ("APP_PASSWORD: qjrx vn\u200bmw kzpt bhfc", 'APP_PASSWORD="qjrx vnmw\u200b kzpt bhfc"',
                     "APP_PASSWORD=qjrx vnmw kz\x1b[31mpt bhfc"):
            with self.subTest(text=text):
                masked = mask(text)
                for group in ("qjrx", "mw", "kz", "bhfc"):
                    self.assertNotIn(group, masked)


class ReviewEighteenTest(unittest.TestCase):
    """The eighteenth review's findings, under the coordinator's split bar."""

    def test_a_name_that_starts_token_is_is_no_tokenizer(self):
        self.assertEqual(mask("TokenIsEncryptionKey=Qx7rT2mPa9LkW3vZ8nB4"), "TokenIsEncryptionKey=[masked]")
        self.assertEqual(mask("tokenizer: cl100k_base"), "tokenizer: cl100k_base")

    def test_repeated_eight_bit_colour_codes_cost_time_in_proportion(self):
        import time
        for unit in ("\x9bm", "\\u009bm", "\x1b[m"):
            took = []
            for n in (64, 512):
                text = "APP_PASSWORD=q" + unit * n
                started = time.process_time()
                mask(text)
                took.append(time.process_time() - started)
            with self.subTest(unit=unit):
                self.assertLess(took[1], 1.0, took)

    def test_a_redirection_or_a_substitution_does_not_end_a_command(self):
        for text in ("az login 2>&1 -u me@example.com -p Tr0ub4dor9", "mongosh --host $(hostname) -p Tr0ub4dor9",
                     "mongosh --host `hostname` -p Tr0ub4dor9", "az login &>/dev/null -u me -p Tr0ub4dor9"):
            with self.subTest(text=text):
                self.assertNotIn("Tr0ub4dor9", mask(text))
        for text in ("mongosh --quiet & ssh -p 2222 host", "mongosh --quiet && ssh -p 2222 host"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)

    def test_a_netrc_password_holds_shell_marks(self):
        for mark in ("&", "|", ";", "(", "`"):
            text = f"machine api.example.com login deploy password qjrx{mark}vnmwkzptbhfc"
            with self.subTest(mark=mark):
                self.assertEqual(mask(text), "machine api.example.com login deploy password [masked]")

    def test_every_random_app_password_is_masked_whole(self):
        r = random.Random(18)
        for _ in range(3000):
            password = " ".join("".join(r.choice(string.ascii_lowercase) for _ in range(4)) for _ in range(4))
            for text in (f"APP_PASSWORD: {password}", f"SMTP_PASS={password}", f'password: "{password}"'):
                masked = mask(text)
                self.assertFalse(any(group in masked for group in password.split()), (text, masked))

    def test_a_colour_code_or_invisible_character_beside_an_app_password(self):
        for text in ("APP_PASSWORD: qjrx vnmw kzpt bhfc\x1b[0m", "APP_PASSWORD: \u200bqjrx vnmw kzpt bhfc",
                     "APP_PASSWORD: qjrx vnmw kzpt bhfc\u200b",
                     "APP_PASSWORD: \x1b[01;31m\x1b[Kqjrx vnmw kzpt bhfc\x1b[m\x1b[K"):
            with self.subTest(text=text):
                masked = mask(text)
                self.assertFalse(any(group in masked for group in ("qjrx", "vnmw", "kzpt", "bhfc")), masked)

    def test_four_common_words_are_read_as_main_reads_them(self):
        for text, masked in (("password: copy yaml into json", "password: [masked] yaml into json"),
                             ('password: "this will work fine"', 'password: "[masked] will work fine"'),
                             ('password="send mail from cron"', 'password="[masked] mail from cron"')):
            with self.subTest(text=text):
                self.assertEqual(mask(text), masked)
        self.assertEqual(mask("SMTP_PASS=abcd efgh ijkl mnop"), "SMTP_PASS=[masked]")

    def test_a_flag_inside_a_quoted_argument_is_not_the_commands(self):
        for text in ("mongosh --eval \"print('ssh -p 2222 host')\"", "mongosh --eval 'db.run(\"ssh -p 2222\")'",
                     "mongosh --host $(ssh -p 2222 host hostname)"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
                self.assertEqual(mask(json.dumps(text)), json.dumps(text))
        self.assertNotIn("Tr0ub4dor9", mask("mongosh --eval 'db.x()' -p Tr0ub4dor9"))


class ReviewNineteenTest(unittest.TestCase):
    """The nineteenth review's findings, under the coordinator's split bar."""

    def test_a_token_in_a_line_grep_colours_whole(self):
        token = "dop_v1_b3178f9d5a06412ce78a6d2a43d98e56f0cab27d91470e31f2dc9b63f8ae5c04"
        for text in (f"\x1b[01;31m\x1b[KERROR {token}\x1b[m\x1b[K\n", f"ERROR {token}\x1b[0m", f"ERROR {token}\u200b done"):
            with self.subTest(text=text):
                self.assertNotIn(token[8:40], mask(text))

    def test_a_comment_holds_no_flag_or_field(self):
        for text in ("mongosh --quiet # ssh -p 2222 host", "machine api.example.com login deploy # password supplied interactively"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)
        for text in ("mongosh -p Tr0ub4dor9 # admin", "mongosh --eval 'a # b' -p Tr0ub4dor9", "mongosh --host db#1 -p Tr0ub4dor9"):
            with self.subTest(text=text):
                self.assertNotIn("Tr0ub4dor9", mask(text))

    def test_a_netrc_password_that_starts_with_a_dash_or_holds_a_backslash_n(self):
        for text in ("machine api.example.com login deploy password -Tr0ub4dor9",
                     "machine api.example.com login deploy password qjrx\\nvnmwkzptbhfc"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), "machine api.example.com login deploy password [masked]")
        two = json.dumps("machine a login u password Tr0ub4dor9\nmachine b login v password X7y8Z9q0w")
        masked = mask(two)
        self.assertNotIn("Tr0ub4dor9", masked)
        self.assertNotIn("X7y8Z9q0w", masked)
        self.assertIn("machine b login v", masked)

    def test_a_long_argument_list_before_its_password_flag(self):
        items = ["mongosh", "--host", "db", "--port", "27017", "--username", "deploy", "--authenticationDatabase", "admin",
                 "--tls", "--tlsCAFile", "/etc/ssl/ca.pem", "-p", "Tr0ub4dor9"]
        self.assertEqual(mask_all(items)[-1], "[masked]")

    def test_an_app_password_with_two_common_words(self):
        self.assertEqual(mask("APP_PASSWORD: hoyp mail htng user"), "APP_PASSWORD: [masked]")
        self.assertEqual(mask("SMTP_PASS=send hoyp mail htng"), "SMTP_PASS=[masked]")
        self.assertEqual(mask("password: copy yaml into toml"), "password: [masked] yaml into toml")

    def test_arithmetic_and_a_nested_command(self):
        for text in ("mongosh --port $((27017 + OFFSET)) -p Tr0ub4dor9",
                     "mongosh --host \"$(mongosh -p Tr0ub4dor9 --eval 'db.host')\"",
                     "mongosh --host $(mongosh -p Tr0ub4dor9 --eval 'db.host') --quiet"):
            with self.subTest(text=text):
                self.assertNotIn("Tr0ub4dor9", mask(text))

    def test_a_command_in_backticks_or_deep_inside_substitutions(self):
        for text in ("mongosh --host `mongosh -p Tr0ub4dor9 --eval x` --quiet",
                     "mongosh --host $(echo $(mongosh --eval \"$(cat q.js)\" -p Tr0ub4dor9)) --quiet",
                     "mongosh " + "$(mongosh " * 63 + "-p Tr0ub4dor9" + ")" * 63):
            with self.subTest(text=text[:60]):
                self.assertNotIn("Tr0ub4dor9", mask(text))

    def test_commands_in_substitutions_cost_time_in_proportion(self):
        def took(text):
            started = time.process_time()
            mask(text)
            return time.process_time() - started

        for name, shape in (("backticks", lambda n: "mongosh " + "`mongosh " * n + "`" * n),
                            ("command words", lambda n: "mongosh $(echo " + "mongosh " * n + ")")):
            with self.subTest(shape=name):
                self.assertLess(took(shape(8000)), 16 * max(took(shape(1000)), 0.01))
        deep = lambda depth: "mongosh " + "$(mongosh " * depth + "x " * 40000 + ")" * depth
        self.assertLess(took(deep(64)), 3 * max(took(deep(8)), 0.01), "a deeper substitution reads what it holds once")

    def test_an_escaped_double_quote_inside_single_quotes_decides_nothing(self):
        text = "mongosh --eval 'db.x.find({name:/\\\"/})' --host \"localhost\" -p Tr0ub4dor9"
        self.assertNotIn("Tr0ub4dor9", mask(text))



class ReviewTwentyTest(unittest.TestCase):
    """The twentieth review's findings, under the coordinator's split bar."""

    def test_a_java_password_that_starts_with_a_dash(self):
        for text in ("keytool -list -storepass -Tr0ub4dor9", "jarsigner -keypass -Zk9qTr0ub4dor app.jar alias",
                     "keytool -list -storepass -qjrxvnmwkzpt",
                     "keytool -importkeystore -deststorepass -Tr0ub4dor9 -srckeystore a.p12"):
            with self.subTest(text=text):
                self.assertNotIn("Tr0ub4dor", mask(text))
        for text in ("keytool -list -storepass -keystore k.jks", "jarsigner -keypass -providerName SunPKCS11 a.jar k",
                     "keytool -list -storepass -J-Xmx512m", "mongosh -p --quiet", "az login -p -h"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text, "an option of the command's own, not a password")

    def test_a_quoted_command_or_one_named_by_its_windows_file(self):
        for text in ('"keytool" -list -storepass Tr0ub4dor9', "'mongosh' -u admin -p Tr0ub4dor9",
                     '"/usr/lib/jvm/bin/keytool" -list -storepass Tr0ub4dor9',
                     '"C:\\Program Files\\Java\\jdk-21\\bin\\keytool.exe" -list -storepass Tr0ub4dor9',
                     "keytool.exe -list -storepass Tr0ub4dor9", "mongosh.exe -u admin -p Tr0ub4dor9"):
            for form in (text, json.dumps(text)):
                with self.subTest(text=form):
                    self.assertNotIn("Tr0ub4dor9", mask(form))

    def test_a_flag_in_a_quoted_script_over_several_lines_is_the_scripts(self):
        for text in ("mongosh --eval 'print(\"ssh -p 2222 host\")\n'", "mongosh --eval \"print('ssh -p 2222 host')\n\"",
                     "mongosh --eval 'db.x.find()\nprint(\"ssh -p 2222 host\")\n'"):
            for form in (text, json.dumps(text)):
                with self.subTest(text=form):
                    self.assertEqual(mask(form), form)
        self.assertNotIn("Tr0ub4dor9", mask("mongosh -p Tr0ub4dor9 --eval '\nprint(1)\n'"))

    def test_a_netrc_password_that_starts_with_a_hash(self):
        for text in ("machine api.example.com login deploy password #Tr0ub4dor9",
                     "machine api.example.com login deploy password #Tr0ub4dor9 # rotated monthly"):
            with self.subTest(text=text):
                self.assertNotIn("Tr0ub4dor9", mask(text))
                self.assertIn("api.example.com login deploy password", mask(text))

    def test_netrc_fields_in_any_order(self):
        for text in ("machine api.example.com password Tr0ub4dor9 login deploy",
                     "machine api.example.com account ops password Tr0ub4dor9 login deploy",
                     "default password Tr0ub4dor9 login deploy"):
            for form in (text, json.dumps(text)):
                with self.subTest(text=form):
                    self.assertNotIn("Tr0ub4dor9", mask(form))
        for text in ("the machine learning password reset flow", "a machine room password sheet"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)

    def test_four_words_of_a_sentence_are_read_as_main_reads_them(self):
        for text, main in (("password: read from smtp logs", "password: [masked] from smtp logs"),
                           ("password: just copy from docs", "password: [masked] copy from docs"),
                           ("password: plan menu soup cake", "password: [masked] menu soup cake")):
            with self.subTest(text=text):
                self.assertEqual(mask(text), main)

    def test_two_keys_masked_alike_keep_both_values(self):
        first, second = "shpat_" + "a1b2c3d4" * 4, "shpat_" + "e5f6a7b8" * 4
        masked = mask_all({first: "first", second: "second", "[masked]": "third"})
        self.assertEqual(sorted(masked.values()), ["first", "second", "third"])
        self.assertFalse(any(first in k or second in k for k in masked), masked)
        self.assertEqual(mask_all({"ghp_" + "Zq7Lm2Xc9Vb4Nn8Kk3Jj6Hh1Gg5Ff0Dd2Ss": 1, "plain": 2})["plain"], 2)
        for reader in READERS:
            with self.subTest(reader=reader):
                text = stored(reader, tool_call(reader, "mcp__store__put", {first: "first", second: "second"}))
                self.assertIn("first", text)
                self.assertIn("second", text)
                self.assertNotIn(first[6:], text)


class ReviewTwentyOneTest(unittest.TestCase):
    """The twenty-first review's findings, under the coordinator's split bar."""

    WEBHOOK_ID = "12345678-1234-1234-1234-123456789abc"
    WEBHOOK = (f"https://a.webhook.office.com/webhookb2/{WEBHOOK_ID}@{WEBHOOK_ID}/IncomingWebhook/"
               + "0123456789abcdef" * 2 + "/" + WEBHOOK_ID)

    def test_a_colour_code_inside_a_link_in_every_form(self):
        url = self.WEBHOOK
        for form, c in FORMS.items():
            for at in (1, 5, 12):
                raw = url[:at] + c + "31m" + url[at:] + c + "0m"
                for text in (raw, repr(raw), json.dumps(raw)) if form in ("raw", "eight-bit") else (raw,):
                    with self.subTest(form=form, at=at, text=text[:40]):
                        masked = mask(text)
                        self.assertNotIn("0123456789abcdef", masked)
                        self.assertNotIn("IncomingWebhook/", masked)

    def test_an_apostrophe_or_a_quote_in_a_quoted_programs_path(self):
        for text in ("\"/opt/team's tools/bin/keytool\" -storepass Tr0ub4dor9",
                     "'/opt/the \"tools\"/bin/mongosh' -u admin -p Tr0ub4dor9",
                     "\"C:\\Users\\dev's laptop\\jdk\\bin\\keytool.exe\" -list -storepass Tr0ub4dor9"):
            for form in (text, json.dumps(text)):
                with self.subTest(text=form):
                    self.assertNotIn("Tr0ub4dor9", mask(form))

    def test_a_quoted_password_flag(self):
        for text in ('mongosh "-p" Tr0ub4dor9', "mongosh '-p' Tr0ub4dor9", 'mongosh -u admin "-p" "Tr0ub4dor9"',
                     'keytool -list "-storepass" Tr0ub4dor9', "jarsigner '-keypass' Tr0ub4dor9 app.jar k",
                     'az login -u me@x.com "-p" Tr0ub4dor9',
                     'aws configure set "aws_secret_access_key" wJx9Rk2Lq7Zp4Mn8Bv3Cx6Tz1Yh5Gf0Ds2Ae7Kq'):
            for form in (text, json.dumps(text)):
                with self.subTest(text=form):
                    masked = mask(form)
                    self.assertNotIn("Tr0ub4dor9", masked)
                    self.assertNotIn("wJx9Rk2Lq7Zp4Mn8", masked)
        for text in ('mongosh "-p" --quiet', "mongosh --eval '\"-p\" is a flag'"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)

    def test_many_keys_masked_alike_cost_time_in_proportion(self):
        def took(n):
            r = random.Random(21)
            keys = {"ghp_" + "".join(r.choices(string.ascii_letters + string.digits, k=36)): True for _ in range(n)}
            started = time.process_time()
            masked = mask_all(keys)
            self.assertEqual(len(masked), n)
            return time.process_time() - started

        self.assertLess(took(8000), 16 * max(took(1000), 0.01))


class ReviewTwentyTwoTest(unittest.TestCase):
    """The twenty-second review's findings, under the coordinator's split bar."""

    def test_a_flag_inside_a_quoted_argument_is_no_quoted_flag(self):
        for text in ('twine upload --comment "-p release-2026" dist/*', "twine upload --comment '-p release-2026' dist/*",
                     'keytool -list -v -alias "-storepass example" -keystore k.jks',
                     'aws configure set region "aws_secret_access_key example"'):
            for form in (text, json.dumps(text)):
                with self.subTest(text=form):
                    self.assertEqual(mask(form), form)

    def test_a_long_quoted_path_to_a_program(self):
        for depth in (25, 100):
            path = "/opt/" + "toolchains/" * depth + "keytool"
            for text in (f'"{path}" -storepass Tr0ub4dor9', f"'{path}' -list -storepass Tr0ub4dor9"):
                for form in (text, json.dumps(text)):
                    with self.subTest(depth=depth, text=form[:20]):
                        self.assertNotIn("Tr0ub4dor9", mask(form))

    def test_quoted_programs_named_again_and_again_cost_time_in_proportion(self):
        def took(text):
            started = time.process_time()
            mask(text)
            return time.process_time() - started

        for name, shape in (("piped", lambda n: '"keytool" -list | ' * n),
                            ("in a substitution", lambda n: "mongosh $(echo " + '"keytool" -list | ' * n + ")"),
                            ("on one line after a long path", lambda n: '"/opt/' + "a/" * 2000 + 'keytool" ' + "x " * n)):
            with self.subTest(shape=name):
                self.assertLess(took(shape(8000)), 16 * max(took(shape(1000)), 0.01))


class ReviewTwentyThreeTest(unittest.TestCase):
    """The twenty-third review's findings, under the coordinator's split bar."""

    def test_a_quoted_first_word_of_a_two_word_command(self):
        for text in ('"az" login -u me@x.com -p Tr0ub4dor9', "'az' login -p Tr0ub4dor9",
                     '"aws" configure set aws_secret_access_key wJx9Rk2Lq7Zp4Mn8Bv3Cx6Tz1Yh5Gf0Ds2Ae7Kq'):
            for form in (text, json.dumps(text), repr(text)):
                with self.subTest(text=form):
                    masked = mask(form)
                    self.assertNotIn("Tr0ub4dor9", masked)
                    self.assertNotIn("wJx9Rk2Lq7Zp4Mn8", masked)

    def test_a_directory_named_as_the_program(self):
        for text in ('"/opt/keytool/bin/keytool" -storepass Tr0ub4dor9', '"/srv/mongosh/bin/mongosh" -p Tr0ub4dor9',
                     "/opt/twine/bin/twine upload -p Tr0ub4dor9 dist/*",
                     '"C:\\jarsigner\\bin\\jarsigner.exe" -keypass Tr0ub4dor9 a.jar k'):
            for form in (text, json.dumps(text), repr(text)):
                with self.subTest(text=form):
                    self.assertNotIn("Tr0ub4dor9", mask(form))
        for text in ("keytool\\n-storepass example", "see /opt/keytool/ for the docs"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)

    def test_an_argument_list_keeps_each_items_bounds(self):
        self.assertEqual(mask_all(["twine", "upload", "--comment", "note: -p release-2026", "dist/*"])[3],
                         "note: -p release-2026")
        self.assertEqual(mask_all(["keytool", "-alias", "team's cert", "-storepass", "Tr0ub4dor9"])[-1], "[masked]")
        self.assertEqual(mask_all(["mongosh", "--eval", "db.x.find({a: '$b'})", "-p", "Tr0ub4dor9"])[-1], "[masked]")
        self.assertEqual(mask_all(["mongosh", "-p", "Tr0ub4dor 9"])[-1], "[masked]")
        for reader in READERS:
            with self.subTest(reader=reader):
                items = ["keytool", "-alias", "team's cert", "-storepass", "Tr0ub4dor9"]
                text = stored(reader, tool_call(reader, "mcp__runner__exec", {"args": items}))
                self.assertNotIn("Tr0ub4dor9", text)
                text = stored(reader, tool_call(reader, "mcp__runner__exec",
                                                {"args": ["twine", "upload", "--comment", "note: -p release-2026"]}))
                self.assertIn("release-2026", text)


class ReviewTwentyFourTest(unittest.TestCase):
    """The twenty-fourth review's findings, under the coordinator's five-item bar."""

    def test_provider_tokens_before_ordinary_punctuation(self):
        from kb import gitleaks
        from tests.test_gitleaks import examples
        names = [rule[0] for rule in gitleaks.RULES]
        for name in ("digitalocean-pat", "databricks-api-token", "adobe-client-secret"):
            for secret in examples(names.index(name), 2):
                for text in (f"{secret},active", f"{secret};{secret}", f"tokens=[{secret}]", f"({secret})", f"{secret}."):
                    with self.subTest(name=name, text=text):
                        self.assertNotIn(secret, mask(text))
                for reader in ("codex", "claude_code"):
                    with self.subTest(name=name, reader=reader):
                        text = stored(reader, tool_call(reader, "Bash", {"command": f"printf '{secret},active\\n'"}))
                        self.assertNotIn(secret, text)

    def test_a_computer_use_type_action(self):
        typed = "q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1"
        for name in ("computer", "mcp__desktop__computer"):
            self.assertEqual(typed_secrets(name, {"action": "type", "text": typed}), {"action": "type", "text": "[masked]"})
        for args in ({"action": "type", "text": "hello from the deploy notes"}, {"action": "key", "text": "ctrl+s"},
                     {"action": "left_click", "coordinate": [12, 34]}):
            with self.subTest(args=args):
                self.assertEqual(typed_secrets("computer", args), args)
        for reader in READERS:
            with self.subTest(reader=reader):
                text = stored(reader, tool_call(reader, "computer", {"action": "type", "text": typed}))
                self.assertNotIn(typed, text)
                self.assertIn("computer", text)

    def test_a_flag_inside_an_argument_list_item_keeps_the_items_text(self):
        masked = mask_all(["curl", "--data-raw", "note: -u feature:preview", "https://example.com"])
        self.assertEqual(masked[3], "https://example.com")
        self.assertTrue(masked[2].startswith("note: -u feature:"), masked)
        self.assertEqual(mask_all(["mongosh", "-p", "Tr0ub4dor 9"])[-1], "[masked]")
        self.assertEqual(mask_all(["keytool", "-alias", "team's cert", "-storepass", "Tr0ub4dor9"])[-1], "[masked]")
        self.assertNotIn("Tr0ub4dor9", mask_all(["curl", "-u", "deploy:Tr0ub4dor9", "https://example.com"])[2])


class ReviewTwentyFiveTest(unittest.TestCase):
    """The twenty-fifth review's findings, under the coordinator's five-item bar."""

    def test_provider_tokens_in_markdown_and_before_an_escape(self):
        secret = "dapi" + "0123456789abcdef" * 2
        for text in (f"**{secret}**", repr(secret + "\tactive"), json.dumps(json.dumps({"cmd": f'echo "{secret}"'})),
                     f"`{secret}`*", repr(f"{secret}\\back")):
            with self.subTest(text=text):
                self.assertNotIn(secret, mask(text))
        for reader in READERS:
            with self.subTest(reader=reader):
                self.assertNotIn(secret, stored(reader, tool_call(reader, "mcp__notes__write", {"text": f"**{secret}**"})))

    def test_prose_about_a_default_login_page_is_no_netrc_line(self):
        for text in ("The default login page password field is empty.", "a default login form password reset link"):
            for form in (text, json.dumps(text)):
                with self.subTest(text=form):
                    self.assertEqual(mask(form), form)
        for text in ("default login deploy password Tr0ub4dor9", "machine h login u password Qz8rLm2x\ndefault login deploy password Tr0ub4dor9",
                     "  default login deploy password Tr0ub4dor9"):
            for form in (text, json.dumps(text)):
                with self.subTest(text=form):
                    self.assertNotIn("Tr0ub4dor9", mask(form))


class ReviewTwentySixTest(unittest.TestCase):
    """The twenty-sixth review's findings, under the coordinator's five-item bar."""

    def test_provider_tokens_in_markdown_emphasis_and_strikeout(self):
        secret = "dapi" + "0123456789abcdef" * 2
        for text in (f"_{secret}_", f"~~{secret}~~", f"__{secret}__", f"*{secret}*", f"see _{secret}_ there"):
            with self.subTest(text=text):
                self.assertNotIn(secret, mask(text))
        for reader in READERS:
            with self.subTest(reader=reader):
                self.assertNotIn(secret, stored(reader, tool_call(reader, "mcp__notes__write", {"text": f"~~{secret}~~"})))

    def test_a_provider_token_ends_at_any_mark_that_is_no_letter_or_digit(self):
        secret = "dapi" + "0123456789abcdef" * 2
        for after in ("/x", "=", "+", "%22", "@host", "#", "(", "-tail", "^", "$", "[0]", "'s"):
            with self.subTest(after=after):
                self.assertNotIn(secret, mask(secret + after))
        self.assertEqual(mask("dapi" + "0123456789abcdef" * 2 + "0"), "dapi" + "0123456789abcdef" * 2 + "0",
                         "a longer run of hex is no Databricks token")

    def test_a_colour_code_as_bash_escapes_it(self):
        token = "ghp_" + "A1b2C3d4E5f6" * 3
        for text in (f"$'\\E[31m{token}'", f"$'\\E[01;31m\\E[K{token}\\E[m\\E[K'", f"\\e[1m{token}\\e[0m"):
            with self.subTest(text=text):
                self.assertNotIn(token, mask(text))
        for reader in READERS:
            with self.subTest(reader=reader):
                self.assertNotIn(token, stored(reader, tool_call(reader, "Bash", {"command": f"echo $'\\E[31m{token}'"})))

    def test_prose_about_login_pages_is_no_netrc_line(self):
        for text in ("Default login page password field is empty.", "The machine learning login page password field is empty.",
                     "machine room login desk password sheet in the drawer"):
            for form in (text, json.dumps(text)):
                with self.subTest(text=form):
                    self.assertEqual(mask(form), form)
        for text in ("machine api.example.com login deploy password Tr0ub4dor9",
                     "machine api.example.com login deploy password Tr0ub4dor9 account ops",
                     "machine api.example.com login deploy password Tr0ub4dor9 # rotated",
                     "machine a.example.com login u password Tr0ub4dor9 machine b.example.com login v password Qz8rLm2x",
                     "default login deploy password Tr0ub4dor9"):
            for form in (text, json.dumps(text), json.dumps(text + "\n")):
                with self.subTest(text=form):
                    self.assertNotIn("Tr0ub4dor9", mask(form))


class ReviewTwentySevenTest(unittest.TestCase):
    """The twenty-seventh review's findings, under the coordinator's five-item bar."""

    def test_a_provider_token_after_a_colour_code_that_follows_letters(self):
        secret = "dapi" + "0123456789abcdef" * 2
        for text in (f"prefix\x1b[31m{secret}", f"prefix\x1b[01;31m\x1b[K{secret}\x1b[m\x1b[K",
                     f"key=\x1b[1m{secret}\x1b[0m", json.dumps(f"prefix\x1b[31m{secret}")):
            with self.subTest(text=text):
                self.assertNotIn(secret, mask(text))
        for reader in READERS:
            with self.subTest(reader=reader):
                self.assertNotIn(secret, stored(reader, tool_call(reader, "Bash", {"command": f"echo prefix\x1b[31m{secret}"})))

    def test_an_azure_client_secret_right_after_an_escape(self):
        secret = "AbC7Q~a1B2c3D4e5F6g7H8i9J0k1L2m3N4p5Q6"
        for text in (json.dumps({"stdout": "ready\n" + secret}), json.dumps({"stdout": "ready\t" + secret}),
                     repr("ready\n" + secret), "ready%0A" + secret):
            with self.subTest(text=text):
                self.assertNotIn(secret, mask(text))

    def test_a_netrc_machine_only_where_a_line_starts(self):
        for text in ("Focus the machine learning login page password field.",
                     "Open the machine room login page password field first."):
            for form in (text, json.dumps(text)):
                with self.subTest(text=form):
                    self.assertEqual(mask(form), form)
        for text in ("machine api.example.com login deploy password Tr0ub4dor9",
                     "  machine api.example.com login deploy password Tr0ub4dor9",
                     "$ cat ~/.netrc\nmachine api.example.com login deploy password Tr0ub4dor9",
                     "machine a.example.com login u password Qz8rLm2xWq machine b.example.com login v password Tr0ub4dor9"):
            for form in (text, json.dumps(text)):
                with self.subTest(text=form):
                    self.assertNotIn("Tr0ub4dor9", mask(form))


class ReviewTwentyEightTest(unittest.TestCase):
    """The twenty-eighth review's findings, under the coordinator's five-item bar."""

    def test_bare_airtable_and_facebook_tokens(self):
        # An Airtable token is read alone. A Facebook token is read where "facebook" is in the text, as gitleaks reads
        # it: alone, its shape is a pipe-separated record's too (ReviewTwentyNineTest), and the coordinator's rule
        # from round 30 counts a token gitleaks' own rule would not find as ON-MAIN.
        airtable = "patAb3Cd5Ef7Gh9Ij." + "0123456789abcdef" * 4
        facebook = "123456789012345|a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5"
        for secret, before in ((airtable, ""), (facebook, "facebook token "),
                               ("123456789012345%7Ca1b2c3d4e5f6g7h8i9j0k1l2m3n4o5", "facebook token ")):
            for text in (before + secret, f"{before}{secret}\n", json.dumps({"stdout": before + secret})):
                with self.subTest(text=text[:30]):
                    self.assertNotIn(secret, mask(text))
            for reader in READERS:
                with self.subTest(reader=reader, secret=secret[:10]):
                    self.assertNotIn(secret, stored(reader, tool_call(reader, "Bash", {"command": f"echo {before}{secret}"})))

    def test_prose_that_starts_a_string_with_a_netrc_keyword(self):
        for text in ("default login page password field.", "machine learning login page password field.",
                     "default login form password reset", "machine room login desk password sheet."):
            for form in (text, json.dumps({"description": text})):
                with self.subTest(text=form):
                    self.assertEqual(mask(form), form)
        for text in ("default login deploy password Tr0ub4dor9", "machine api.example.com login deploy password qjrxvnmwkzptbhfc",
                     "machine api.example.com login me@example.com password sunshine9",
                     # a host's password in small letters is a .netrc file's, not a sentence's
                     "machine api.example.com login deploy password sunshine", "machine localhost login deploy password dragon",
                     "machine 10.0.0.5 login ci password letmein", "machine git.example.com:8443 login ci password letmein"):
            for form in (text, json.dumps({"content": text})):
                with self.subTest(text=form):
                    self.assertNotIn(text.rsplit(" ", 1)[1], mask(form))

class ReviewTwentyNineTest(unittest.TestCase):
    """The twenty-ninth review's findings: a pipe-separated record read as a Facebook token, and a .netrc-like
    sentence whose last word is long."""

    def test_a_pipe_separated_record_with_a_timestamp_and_a_trace_id(self):
        for text in ("1700000000000000|0123456789abcdef0123456789abcdef",
                     "1700000000000000|0123456789abcdef0123456789abcdef|200|GET /health\n",
                     "ts=1700000000000000%7C0123456789abcdef0123456789abcdef&page=2",
                     json.dumps({"row": "1700000000000000|0123456789abcdef0123456789abcdef"})):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)

    def test_netrc_like_prose_with_a_long_last_word(self):
        for text in ("default login page password requirements", "default login page password Requirements.",
                     "machine learning login page password self-service", "default login screen password guidelines",
                     "default login form password autocomplete"):
            for form in (text, json.dumps({"description": text})):
                with self.subTest(text=form):
                    self.assertEqual(mask(form), form)
        for text in ("default login deploy password Tr0ub4dor9", "default login deploy password hx7-Qm2p",
                     "machine api.example.com login deploy password requirements"):
            for form in (text, json.dumps({"content": text})):
                with self.subTest(text=form):
                    self.assertNotIn(text.rsplit(" ", 1)[1], mask(form))


class ReviewThirtyTest(unittest.TestCase):
    """The thirtieth review's finding: a provider's token that an earlier pattern took only part of, as GitLab's shape
    takes a routable token's first part, kept the rest readable; gitleaks' own rule finds the whole token."""

    def test_a_token_an_earlier_pattern_cut_is_masked_whole(self):
        for secret in ("glpat-Ab3Cd5Ef7Gh9Ij1Kl3Mn5Op7Qr9St1Uv.aa1234567",
                       "hvs.Qx7Lm2Vn8A_ghp_Ab3Cd5Ef7Gh9Ij1Kl3Mn5Op7Qr9St1Uv3Wx5_Yz4Bx6Cd8Ef0Gh2Ij4Kl6Mn8Op0Qr2St4Uv6Wx8Yz"):
            tail, head = secret[-9:], secret[:12]
            for text in (secret, f"GITLAB_TOKEN={secret}\n", json.dumps({"stdout": f"GITLAB_TOKEN={secret}\n"}),
                         f"token: {secret} (expires soon)"):
                with self.subTest(text=text[:40]):
                    masked = mask(text)
                    self.assertNotIn(tail, masked)
                    self.assertNotIn(head, masked)
            out = f"HOME=/home/dev\nGITLAB_TOKEN={secret}\nSHELL=/bin/bash\n"  # printenv's output
            for reader, records in (
                    ("codex", codex_session(
                        {"type": "function_call", "name": "exec_command", "arguments": json.dumps({"cmd": "printenv"})},
                        {"type": "function_call_output", "output": "Process exited with code 0\nOutput:\n" + out})),
                    ("claude_code", claude_session({"type": "tool_result", "content": [{"type": "text", "text": out}]},
                                                   role="user"))):
                with self.subTest(reader=reader, secret=secret[:8]):
                    text = stored(reader, records)
                    self.assertNotIn(tail, text)
                    self.assertNotIn(head, text)
                    self.assertIn("SHELL=/bin/bash", text)


class ReviewThirtyOneTest(unittest.TestCase):
    """The thirty-first review's finding: a password typed into a page by code, as Codex's code mode calls a typing
    tool from JavaScript and as Playwright's own calls fill a field, was stored readable."""

    SECRET = "Tr0ub4dor-Zk9q"
    TYPED = (
        'text(await tools.mcp__playwright__browser_type({"element":"Password","ref":"e7","text":"Tr0ub4dor-Zk9q"}));',
        "await tools.mcp__playwright__browser_type({element: 'Password textbox', ref: 'e7', text: 'Tr0ub4dor-Zk9q'});",
        'await tools.mcp__playwright__browser_fill_form({"fields":[{"name":"Email","type":"textbox","ref":"e3",'
        '"value":"me@example.com"},{"name":"Password","type":"textbox","ref":"e7","value":"Tr0ub4dor-Zk9q"}]});',
        "await page.getByLabel('Password').fill('Tr0ub4dor-Zk9q');",
        'await page.getByRole("textbox", { name: "Password" }).fill("Tr0ub4dor-Zk9q");',
        "await page.getByPlaceholder('Enter your password').type('Tr0ub4dor-Zk9q');",
        "await page.fill('#password', 'Tr0ub4dor-Zk9q');",
        "await page.locator('input[type=\"password\"]').fill('Tr0ub4dor-Zk9q');",
        "await page.getByLabel('PIN').pressSequentially('Tr0ub4dor-Zk9q');",
    )
    ORDINARY = (
        "await page.getByLabel('Email').fill('me@example.com');",
        "await page.fill('#search', 'pelican plan');",
        'await tools.mcp__playwright__browser_type({"element":"Search","ref":"e2","text":"pelican plan"});',
        "await page.getByLabel('Password').fill(password);",
        "await page.getByRole('button', { name: 'Sign in' }).click();",
        "const fill = (field, value) => field.fill(value);",
    )

    def test_a_password_typed_by_code_is_masked(self):
        for code in self.TYPED:
            for text in (code, f"TOOL functions.exec: {code}", json.dumps({"code": code})):
                with self.subTest(text=text[:60]):
                    masked = mask(text)
                    self.assertNotIn(self.SECRET, masked)
                    self.assertIn("me@example.com" if "Email" in code else "Password" if "Password" in code else "",
                                  masked)
        records = codex_session({"type": "custom_tool_call", "name": "exec", "input": self.TYPED[0]})
        self.assertNotIn(self.SECRET, stored("codex", records))

    def test_a_facebook_shaped_record_is_masked_only_beside_the_word_facebook(self):
        # Pinned by the coordinator's decision (a): a pipe record of 16 digits and 32 hex has the shape of a Facebook
        # app token, so with "facebook" in the text it is masked, as gitleaks' own rule finds it there (a residual
        # over-masking, in the README's known limits); without the word it stays, as on main.
        record = "1700000000000000|0123456789abcdef0123456789abcdef"
        self.assertEqual(mask(f"{record}|200|GET /health|facebook\n"), "[masked]|200|GET /health|facebook\n")
        for text in (f"{record}|200|GET /health\n", f"{record}|200|GET /health|graph-api\n"):
            self.assertEqual(mask(text), text)

    def test_code_that_types_costs_time_in_proportion(self):
        import time
        for unit in ("getByLabel('Password').fill('", "getByLabel('", ".fill('a', '", 'browser_type({"text":"',
                     "browser_type({element: 'Password', text: 'x'}); ", "getByRole('textbox', { name: '"):
            with self.subTest(unit=unit):
                took = {}
                for size in (1 << 16, 1 << 18):
                    text = unit * (size // len(unit))
                    started = time.process_time()
                    mask(text)
                    took[size] = time.process_time() - started
                self.assertLess(took[1 << 18], 8 * max(took[1 << 16], 0.02), took)

    def test_ordinary_code_that_types_into_a_page_stays(self):
        for code in self.ORDINARY:
            for text in (code, json.dumps({"code": code})):
                with self.subTest(text=text[:60]):
                    self.assertEqual(mask(text), text)


class ReviewThirtyTwoTest(unittest.TestCase):
    """The thirty-second review's findings: a typing call's reading ran past its own bracket, a brace inside a typed
    password, Playwright's Python names, and code laid out over several lines."""

    SECRET = "Tr0ub{4dor}-Zk9q"
    TYPED = (
        'text(await tools.mcp__playwright__browser_type({"element":"Password","ref":"e7","text":"Tr0ub{4dor}-Zk9q"}));',
        'await page.get_by_label("Password").fill("Tr0ub{4dor}-Zk9q")',
        'page.get_by_role("textbox", name="Password").fill("Tr0ub{4dor}-Zk9q")',
        'page.get_by_placeholder("Enter your password").press_sequentially("Tr0ub{4dor}-Zk9q")',
        'await page.getByRole("textbox", {\n  name: "Password"\n}).fill("Tr0ub{4dor}-Zk9q");',
        'await page.getByLabel("Password").fill ("Tr0ub{4dor}-Zk9q");',
        'await page\n  .getByLabel("Password")\n  .fill("Tr0ub{4dor}-Zk9q");',
        'driver.find_element(By.ID, "password").send_keys("Tr0ub{4dor}-Zk9q")',
        'await driver.findElement(By.name("password")).sendKeys("Tr0ub{4dor}-Zk9q");',
        "await page.type('#password', 'Tr0ub{4dor}-Zk9q');",
        "await $('#password').setValue('Tr0ub{4dor}-Zk9q');",
    )
    ORDINARY = (
        'await tools.mcp__playwright__browser_type({element: "Search", text: "release notes"});\n'
        'const validation = {label: "Password", text: "Use at least twelve characters."};',
        'await page.get_by_label("Email").fill("me@example.com")',
        'driver.find_element(By.ID, "search").send_keys("pelican plan")',
        'await page.getByRole("button", {\n  name: "Password reset"\n}).click();',
        'await page.getByLabel("Password").fill(password);',
        'page.get_by_label("Password").fill(os.environ["PASSWORD"])',
    )

    def test_a_password_typed_by_code_in_more_shapes_is_masked(self):
        for code in self.TYPED:
            for text in (code, f"TOOL functions.exec: {code}", json.dumps({"code": code})):
                with self.subTest(text=text[:70]):
                    masked = mask(text)
                    self.assertNotIn(self.SECRET, masked)
                    self.assertNotIn("4dor", masked)

    def test_ordinary_code_stays(self):
        for code in self.ORDINARY:
            for text in (code, json.dumps({"code": code})):
                with self.subTest(text=text[:70]):
                    self.assertEqual(mask(text), text)


class ReviewThirtyThreeTest(unittest.TestCase):
    """The thirty-third review's findings, inside the coordinator's class for code that types: Python's raw and
    triple-quoted strings, a password given by keyword, after a key or in brackets, a password holding "fill(" or
    "${", and an array's fill read as a typing tool's call."""

    TYPED = (
        ('page.get_by_label("Password").fill(r"Tr0ub{4dor}-Zk9q")', "Tr0ub{4dor}-Zk9q"),
        ('page.get_by_label("Password").fill("""Tr0ub{4dor}-Zk9q""")', "Tr0ub{4dor}-Zk9q"),
        ("page.get_by_label('Password').fill('''Tr0ub{4dor}-Zk9q''')", "Tr0ub{4dor}-Zk9q"),
        ('page.get_by_label("Password").fill(value="sunshine")', "sunshine"),
        ('driver.find_element(By.ID, "password").send_keys(Keys.HOME, "Tr0ub{4dor}-Zk9q")', "Tr0ub{4dor}-Zk9q"),
        ('page.get_by_label("Password").fill(("Tr0ub{4dor}-Zk9q"))', "Tr0ub{4dor}-Zk9q"),
        ('await page.getByLabel("Password").fill("Autofill(42)!");', "Autofill(42)!"),
        ('await tools.mcp__playwright__browser_type({element: "Password", text: "Autofill(42)!"});', "Autofill(42)!"),
        ('await page.getByLabel("Password").fill("H4wk${nest}9!");', "H4wk${nest}9!"),
        ("await page.getByLabel('Password').fill('H4wk${nest}9!');", "H4wk${nest}9!"),
    )
    ORDINARY = (
        'Array(3).fill({label: "Password", text: "Use at least twelve characters."})',
        'new Array(8).fill({name: "Password", value: "hint"})',
        "await page.getByLabel('Password').fill(`${password}`);",
        'page.get_by_label("Password").fill(f"{password}")',
        'page.get_by_label("Password").fill(value=password)',
        'driver.find_element(By.ID, "password").send_keys(Keys.ENTER)',
    )

    def test_more_string_and_argument_forms_are_masked(self):
        for code, secret in self.TYPED:
            for text in (code, json.dumps({"code": code})):
                with self.subTest(text=text[:70]):
                    self.assertNotIn(secret, mask(text))

    def test_ordinary_code_stays(self):
        for code in self.ORDINARY:
            for text in (code, json.dumps({"code": code})):
                with self.subTest(text=text[:70]):
                    self.assertEqual(mask(text), text)


class ReviewThirtyFourTest(unittest.TestCase):
    """The thirty-fourth review's findings, inside the coordinator's class for code that types: a buffer's fill read
    as typing, a password holding a call and a quote, a regex label, comments inside a call, keyword arguments in
    any order, a value in brackets in a tool's input, and code in single quotes."""

    TYPED = (
        ("await page.getByLabel(\"Password\").fill(\"Autofill('42')!\");", "Autofill('42')!"),
        ('await page.getByLabel(/Password/i).fill("sunshine");', "sunshine"),
        ('page.get_by_label("Password").fill( # Enter the password\n "sunshine"\n)', "sunshine"),
        ('await tools.mcp__playwright__browser_type({element: "Password", text: /* password */ "sunshine"});', "sunshine"),
        ('page.fill(value="sunshine", selector="#password")', "sunshine"),
        ('await tools.mcp__playwright__browser_type({element: "Password", text: ("sunshine")});', "sunshine"),
        ("python3 -c 'page.get_by_label(\"Password\").fill(\"sunshine\")'", "sunshine"),
        ('await page.keyboard.type("Tr0ub{4dor}-Zk9q");', "Tr0ub{4dor}-Zk9q"),
    )
    ORDINARY = (
        'Buffer.alloc(32).fill("0123456789abcdef")',
        "new Uint8Array(16).fill(0x2a); out.fill('Zq7vRt2LmX9kQ3w')",
        'page.get_by_label("Email").fill( # the address\n "me@example.com"\n)',
        'await page.getByLabel(/Search/i).fill("pelican plan");',
    )

    def test_more_layouts_of_a_typed_password_are_masked(self):
        for code, secret in self.TYPED:
            for text in (code, json.dumps({"code": code})):
                with self.subTest(text=text[:70]):
                    self.assertNotIn(secret, mask(text))

    def test_ordinary_code_stays(self):
        for code in self.ORDINARY:
            for text in (code, json.dumps({"code": code})):
                with self.subTest(text=text[:70]):
                    self.assertEqual(mask(text), text)


class ReviewThirtyFiveTest(unittest.TestCase):
    """The thirty-fifth review's findings, inside the shapes the coordinator froze: a password holding a call after a
    selector argument or a comment, a comment in code escaped twice, and a buffer's fill under a string that names a
    secret."""

    TYPED = (
        ("page.fill(\"#password\", \"Autofill('42')!\")", "Autofill('42')!"),
        ("page.get_by_label(\"Password\").fill( # the password\n \"Autofill('42')!\"\n)", "Autofill('42')!"),
    )

    def test_a_password_holding_a_call_after_a_selector_or_a_comment(self):
        for code, secret in self.TYPED:
            for text in (code, json.dumps({"code": code}), json.dumps(json.dumps(code))):
                with self.subTest(text=text[:70]):
                    self.assertNotIn(secret, mask(text))

    def test_a_comment_in_code_escaped_twice(self):
        code = 'page.get_by_label("Password").fill( # Enter password\n "sunshine"\n)'
        for text in (json.dumps(code), json.dumps(json.dumps(code))):
            with self.subTest(depth=text.count("\\\\")):
                self.assertNotIn("sunshine", mask(text))

    def test_a_buffer_or_other_object_that_is_no_page_keeps_its_value(self):
        for code in ('Buffer.from("password").fill("x")', 'new Array(4).fill("password").fill("x")',
                     'secrets.get("password").type("text/plain")'):
            for text in (code, json.dumps({"code": code})):
                with self.subTest(text=text[:70]):
                    self.assertEqual(mask(text), text)


class ReviewThirtySixTest(unittest.TestCase):
    """The thirty-sixth review's findings, inside the shapes the coordinator froze: a comment holding a bracket or a
    Windows path inside a typing call, Python's string prefix after a line break or tab escaped as JSON holds code, and
    a page whose name is not "page"."""

    TYPED = (
        ("page.get_by_label(\"Password\").fill( # 1) Enter password\n \"Autofill('42')!\"\n)", "Autofill('42')!"),
        ("page.get_by_label(\"Password\").fill( # see C:\\notes\n \"sunshine\"\n)", "sunshine"),
        ("page.get_by_label(\"Password\").fill( # C:\\temp\\run (1)\n \"sunshine\"\n)", "sunshine"),
        ("await page.getByLabel('Password').fill( // step 2) see C:\\notes\n 'sunshine'\n)", "sunshine"),
        ("page.get_by_label(\"Password\").fill(\n\tr\"sunshine\"\n)", "sunshine"),
        ("page.get_by_label(\"Password\").fill(\n    rb'sunshine'\n)", "sunshine"),
        ("const loginPage = await browser.newPage();\nawait loginPage.type(\"#password\", \"sunshine\");", "sunshine"),
        ("await login_page.fill(\"#password\", \"sunshine\")", "sunshine"),
        ("await page2.type(\"#password\", \"sunshine\")", "sunshine"),
        ("await adminFrame.fill(\"input[name=password]\", \"sunshine\")", "sunshine"),
    )

    def test_each_typed_password_is_masked_as_written_and_escaped_once_or_twice(self):
        for code, secret in self.TYPED:
            for text in (code, json.dumps(code), json.dumps({"code": code}), json.dumps(json.dumps(code))):
                with self.subTest(text=text[:80]):
                    self.assertNotIn(secret, mask(text))

    def test_a_comment_keeps_the_code_after_it_read_and_ordinary_code_stays(self):
        for code in ('page.get_by_label("Search").fill( # see C:\\notes\n "sunshine"\n)',
                     'homePage.type("#search", "sunshine")', 'pageCount.fill("x")', 'Buffer.from("password").fill("x")',
                     'subpages.fill("password")', '# see C:\\notes\nprint("sunshine")'):
            for text in (code, json.dumps(code), json.dumps(json.dumps(code))):
                with self.subTest(text=text[:80]):
                    self.assertEqual(mask(text), text)


class ReviewThirtySevenTest(unittest.TestCase):
    """The thirty-seventh review's findings, inside the frozen shapes, which the coordinator's design for typing calls
    masks by itself: a value in extra brackets, a triple-quoted value holding its own escaped delimiter, a comment
    whose escaped quotes make a plain line read as escaped, and a page held in any variable."""

    TYPED = (
        ('page.get_by_label("Password").fill(("Autofill(\'42\')!"))', ("Autofill", "42")),
        ('page.get_by_label("Password").fill("""sun\\"""shine""")', ("sun", "shine")),
        ('page.get_by_label("Password").fill(\n # See C:\\notes and \\"example\\"\n "sunshine"\n)', ("sunshine",)),
        ('const login = await browser.newPage();\nawait login.type("#password", "sunshine");', ("sunshine",)),
    )

    def test_each_is_masked_as_written_and_escaped_once_or_twice(self):
        for code, parts in self.TYPED:
            for text in (code, json.dumps(code), json.dumps({"code": code}), json.dumps(json.dumps(code))):
                masked = mask(text)
                for part in parts:
                    with self.subTest(text=text[:70], part=part):
                        self.assertNotIn(part, masked)


class TypingCallDesignTest(unittest.TestCase):
    """The coordinator's design for code that types: in a typing call whose field names a secret, the mask runs from
    the value's first string through the call's last string that is not its field, and to the end of the line after a
    quote that closes nothing. Its four costs, each pinned to the span and no more, and what must stay."""

    COSTS = (
        # a comment or code between the value and a later string argument
        ('page.fill("#password", "sunshine", # note\n timeout="5s")', 'page.fill("#password", "[masked]")'),
        # a later options string
        ('page.type("#password", "sunshine", {delay: "50"})', 'page.type("#password", "[masked]"})'),
        # a two-string call on an object that is no page, its first string naming a secret
        ('field.type("password", "string")', 'field.type("password", "[masked]")'),
        # the rest of the line after a quote that closes nothing, and nothing past that line
        ('page.fill("#password", "sun"shine"); page.click("#login")\nawait done()',
         'page.fill("#password", "[masked]\nawait done()'),
        # literals joined with "+", read as one value: kept readable before the design, masked by it
        ('await page.getByLabel("Password").fill("sun" + "shine");', 'await page.getByLabel("Password").fill("[masked]");'),
        # a key before the value, as Selenium's send_keys takes them
        ('driver.find_element(By.ID, "password").send_keys(Keys.HOME, "sunshine")',
         'driver.find_element(By.ID, "password").send_keys(Keys.HOME, "[masked]")'),
    )
    STAY = (
        'Buffer.from("password").fill("x")', 'new Array(4).fill("password").fill("x")',
        'homePage.type("#search", "sunshine")', 'secrets.get("password").type("text/plain")', 'subpages.fill("password")',
        'pageCount.fill("x")', 'page.fill("#password", password)', 'page.fill("#password", f"{pw}")',
        'page.fill("#password", `${pw}`)', 'page.fill("#password", os.environ["PW"])',
        'page.get_by_label("Password").fill(decrypt("vault:pw"))', '# see C:\\notes\nprint("sunshine")',
    )

    def test_each_cost_is_the_span_and_no_more_as_written_and_in_json(self):
        for code, expected in self.COSTS:
            with self.subTest(code=code[:60]):
                self.assertEqual(mask(code), expected)
            with self.subTest(code=code[:60], wrapped="string"):
                self.assertEqual(json.loads(mask(json.dumps(code))), expected)
            with self.subTest(code=code[:60], wrapped="object"):
                self.assertEqual(json.loads(mask(json.dumps({"code": code, "cwd": "/srv"}))),
                                 {"code": expected, "cwd": "/srv"})
            with self.subTest(code=code[:60], wrapped="object twice"):
                self.assertEqual(json.loads(json.loads(mask(json.dumps(json.dumps({"code": code, "cwd": "/srv"}))))),
                                 {"code": expected, "cwd": "/srv"})

    def test_what_must_stay_stays(self):
        for code in self.STAY:
            for text in (code, json.dumps(code), json.dumps({"code": code}), json.dumps(json.dumps(code))):
                with self.subTest(text=text[:70]):
                    self.assertEqual(mask(text), text)


class ReviewThirtyEightTest(unittest.TestCase):
    """The thirty-eighth review's findings: a value read as a selector where options follow it in a call whose locator
    already names the field, and many code records in one JSON log costing time in the square of their count."""

    TYPED = (
        'page.getByLabel("Password").fill("sunshine", {timeout: 5000})',
        'page.get_by_label("Password").fill("sunshine", timeout=5000)',
        'driver.find_element(By.ID, "password").send_keys("sunshine", Keys.ENTER)',
        'await page.locator("#password").fill("sunshine", { force: true });',
    )

    def test_a_value_followed_by_options_is_masked(self):
        for code in self.TYPED:
            for text in (code, json.dumps(code), json.dumps({"code": code}), json.dumps(json.dumps(code))):
                with self.subTest(text=text[:70]):
                    self.assertNotIn("sunshine", mask(text))

    def test_a_selector_with_a_variable_and_options_stays(self):
        for code in ('page.fill("#password", password, {timeout: 5000})', 'page.type("#password", pw, {delay: 20})'):
            for text in (code, json.dumps({"code": code})):
                with self.subTest(text=text[:70]):
                    self.assertEqual(mask(text), text)

    def test_many_code_records_in_one_log_cost_time_in_proportion(self):
        import time
        took = {}
        for n in (1000, 4000):
            text = (json.dumps({"code": 'page.getByLabel("Search").fill("release notes")'}) + "\n") * n
            started = time.process_time()
            self.assertEqual(mask(text), text)
            took[n] = time.process_time() - started
        self.assertLess(took[4000], 6 * max(took[1000], 0.05), took)


class ReviewThirtyNineTest(unittest.TestCase):
    """The thirty-ninth review's findings: a call's own selector with its value given by keyword, a named frame before
    a call's own selector, and an unclosed call whose search for a stray quote ran past the code that holds it."""

    TYPED = (
        'page.fill("#password", value="sunshine")',
        'page.frame(name="login").fill("#password", "sunshine")',
    )

    def test_each_value_is_masked(self):
        for code in self.TYPED:
            for text in (code, json.dumps(code), json.dumps({"code": code}), json.dumps(json.dumps(code))):
                with self.subTest(text=text[:70]):
                    self.assertNotIn("sunshine", mask(text))

    def test_an_unclosed_call_never_masks_past_the_code_that_holds_it(self):
        record = json.dumps({"code": 'page.fill("#password", "sunshine"', "cwd": "/srv"})
        for after in ("\nDon't retry.\n", "\nIt's 'done'.\n", ""):
            text = record + after
            with self.subTest(after=after):
                masked = mask(text)
                self.assertNotIn("sunshine", masked)
                self.assertTrue(masked.endswith('"cwd": "/srv"}' + after), masked)


class ReviewFortyTest(unittest.TestCase):
    """The fortieth review's findings: quoted option keys read as a selector's value or as a field's label, and a
    chain read back across a statement into another call's strings."""

    TYPED = (
        'page.getByLabel("Password").fill("sunshine", {"timeout": 5000})',
        'await page.getByRole("textbox", {"name": "Password"}).fill("sunshine");',
        'page.get_by_role("textbox", **{"name": "Password"}).fill("sunshine")',
        'page.get_by_label("Password hint").fill("first pet")\npage.get_by_label("Password").fill("sunshine")',
        'await page.getByLabel("Password hint").fill("first pet"); await page.getByLabel("Password").fill("sunshine");',
    )

    def test_each_password_is_masked(self):
        for code in self.TYPED:
            for text in (code, json.dumps(code), json.dumps({"code": code}), json.dumps(json.dumps(code))):
                with self.subTest(text=text[:70]):
                    self.assertNotIn("sunshine", mask(text))

    def test_a_field_after_another_statement_keeps_its_own_label(self):
        for code in ('print("Password")\npage.get_by_label("Email").fill("me@example.com")',
                     'console.log("Password"); await page.getByLabel("Email").fill("me@example.com");',
                     'page.get_by_label("Password").fill(pw)\npage.get_by_label("Email").fill("me@example.com")'):
            for text in (code, json.dumps(code), json.dumps({"code": code})):
                with self.subTest(text=text[:70]):
                    self.assertEqual(mask(text), text)


class ReviewFortyOneTest(unittest.TestCase):
    """The forty-first review's findings, all in reading the field inside the coordinator's rule: a key typed after
    the value in Selenium's send_keys, a regex inside a locator's options, and brackets around a call's own selector."""

    TYPED = (
        'driver.find_element(By.ID, "password").send_keys("sunshine", "\\n")',
        'driver.find_element(By.ID, "password").send_keys("sunshine", Keys.ENTER, "x")',
        'await page.getByRole("textbox", {name: /Password/i}).fill("sunshine");',
        'await page.getByRole("textbox", { name: /^password$/i, exact: true }).fill("sunshine");',
        'page.fill(("#password"), "sunshine")',
        'page.fill(\n    ("#password"),\n    "sunshine",\n)',
    )

    def test_each_password_is_masked(self):
        for code in self.TYPED:
            for text in (code, json.dumps(code), json.dumps({"code": code}), json.dumps(json.dumps(code))):
                with self.subTest(text=text[:70]):
                    self.assertNotIn("sunshine", mask(text))

    def test_an_element_typed_into_has_no_selector_and_a_page_keeps_one(self):
        self.assertEqual(mask('page.keyboard.type("sunshine", "x")'), 'page.keyboard.type("sunshine", "x")')
        self.assertEqual(mask('page.type("#search", "sunshine")'), 'page.type("#search", "sunshine")')
        self.assertNotIn("sunshine", mask('page.frame(name="login").type("#password", "sunshine")'))


class ReviewFortyTwoTest(unittest.TestCase):
    """The forty-second review's findings, all in reading the field inside the coordinator's rule: a page a browser
    returns, a locator in brackets before its call, and a regex inside a comment in a locator's brackets."""

    TYPED = (
        'browser.new_page().fill("#password", "sunshine")',
        'await browser.newPage().then(p => p)\nawait (await browser.newPage()).type("#password", "sunshine")',
        '(await page.$("#password")).type("sunshine");',
        '(await page.waitForSelector("#password")).type("sunshine");',
    )

    def test_each_password_is_masked(self):
        for code in self.TYPED:
            for text in (code, json.dumps(code), json.dumps({"code": code}), json.dumps(json.dumps(code))):
                with self.subTest(text=text[:70]):
                    self.assertNotIn("sunshine", mask(text))

    def test_a_regex_inside_a_comment_names_no_field(self):
        for code in ('page.getByRole("textbox", {name: "Email", /* name: /Password/, */ exact: true}).fill("me@example.com")',
                     'page.get_by_role("textbox", name="Email",  # name=re.compile("Password")\n).fill("me@example.com")'):
            for text in (code, json.dumps(code), json.dumps({"code": code})):
                with self.subTest(text=text[:70]):
                    self.assertEqual(mask(text), text)


class ReviewFortyThreeTest(unittest.TestCase):
    """The forty-third review's findings: a container's selector read as the field's label, a comment mark inside a
    JavaScript regex read as a comment, and the comments indexed again for each typing call."""

    TYPED = (
        'page.locator("#reset-form").getByLabel("New password").fill("sunshine")',
        'page.locator("form.reset").get_by_label("Password").fill("sunshine")',
        'const path = page.url().split(/#/)[0]; await page.getByLabel("Password").fill("sunshine");',
        'const s = x.replace(/\\/\\//g, "/"); await page.getByLabel("Password").fill("sunshine");',
        'page.frame(name="reset").fill("#password", "sunshine")',
    )

    def test_each_password_is_masked(self):
        for code in self.TYPED:
            for text in (code, json.dumps(code), json.dumps({"code": code}), json.dumps(json.dumps(code))):
                with self.subTest(text=text[:70]):
                    self.assertNotIn("sunshine", mask(text))

    def test_a_containers_name_is_not_its_childs(self):
        for code in ('page.get_by_test_id("password-reset").get_by_label("Email").fill("me@example.com")',):
            for text in (code, json.dumps(code), json.dumps({"code": code})):
                with self.subTest(text=text[:70]):
                    self.assertEqual(mask(text), text)

    def test_a_container_that_asks_for_a_secret_marks_its_child(self):
        """Flipped by the union rule (round 45): a container whose own selector asks for a secret names its fields."""
        code = 'page.locator("#password-form").getByLabel("Email").fill("me@example.com")'
        for wrap in (lambda t: t, json.dumps, lambda t: json.dumps({"code": t})):
            with self.subTest(text=wrap(code)[:70]):
                self.assertEqual(mask(wrap(code)), wrap(code.replace("me@example.com", "[masked]")))

    def test_many_commented_records_cost_time_in_proportion(self):
        import time
        took = {}
        for n in (500, 8000):
            text = "\n".join(json.dumps({"code": f'// Search entry {i}\npage.fill("#search", "garden");'}) for i in range(n))
            started = time.process_time()
            self.assertEqual(mask(text), text)
            took[n] = time.process_time() - started
        self.assertLess(took[8000], 24 * max(took[500], 0.05), took)  # sixteen times the text



class ReviewFortyFourTest(unittest.TestCase):
    """The forty-fourth review's cost finding, and two more of its kind: a regex literal left unclosed after a run of
    backslashes (as node --check prints one), shell globs ("src/*.py") read as comments that never close, and a regex's
    class left unclosed, each tried again from every start."""

    @staticmethod
    def _took(text):
        import time
        started = time.process_time()
        out = mask(text)
        return time.process_time() - started, out

    @staticmethod
    def _node_error(backslashes):
        line = "const pattern = /" + "\\" * backslashes + '; page.fill("#search", "garden");'
        return ("[stdin]:1\n" + line + "\n                ^\n\nSyntaxError: Invalid regular expression: missing /\n"
                "    at wrapSafe (node:internal/modules/cjs/loader:1713:18)\n\nNode.js v22.23.4\n")

    def test_an_unclosed_regex_after_backslashes_costs_little(self):
        for wrap in (lambda t: t, json.dumps, lambda t: json.dumps({"stderr": t})):
            took = {}
            for n in (0, 30):
                text = wrap(self._node_error(n))
                took[n], out = self._took(text)
                self.assertEqual(out, text)
            self.assertLess(took[30], 24 * max(took[0], 0.05), took)

    def test_globs_cost_time_in_proportion(self):
        for wrap in (lambda t: t, json.dumps):
            took = {}
            for n in (500, 8000):
                text = wrap("cp src/*.py dest/\n" * n + 'page.fill("#q", "garden")\n')
                took[n], out = self._took(text)
                self.assertEqual(out, text)
            self.assertLess(took[8000], 24 * max(took[500], 0.05), took)  # sixteen times the text

    def test_unclosed_regex_classes_cost_time_in_proportion(self):
        for wrap in (lambda t: t, json.dumps):
            took = {}
            for n in (500, 8000):
                text = wrap("x =/[" * n + '\npage.fill("#q", "garden")\n')
                took[n], out = self._took(text)
                self.assertEqual(out, text)
            self.assertLess(took[8000], 24 * max(took[500], 0.05), took)

    def test_a_regex_class_holding_a_slash_is_still_one_regex(self):
        code = 'const name = path.match(/[^/#]+$/)[0]; await page.getByLabel("Password").fill("sunshine");'
        for text in (code, json.dumps(code), json.dumps({"code": code}), json.dumps(json.dumps(code))):
            with self.subTest(text=text[:70]):
                self.assertNotIn("sunshine", mask(text))



class UnionFieldTest(unittest.TestCase):
    """The union rule for a code typing call's field (round 45): any one string of a locator call in its chain, or of
    its own selector, that asks for a secret on its own names the field so, and no other string takes that back.
    Strings of other calls in the chain (filter, describe) are not read."""

    WRAPS = (lambda t: t, json.dumps, lambda t: json.dumps({"code": t}), lambda t: json.dumps(json.dumps(t)))

    def masks(self, value, *codes):
        for code in codes:
            for wrap in self.WRAPS:
                with self.subTest(text=wrap(code)[:90]):
                    self.assertEqual(mask(wrap(code)), wrap(code.replace(value, "[masked]")))

    def stays(self, *codes):
        for code in codes:
            for wrap in self.WRAPS:
                with self.subTest(text=wrap(code)[:90]):
                    self.assertEqual(mask(wrap(code)), wrap(code))

    # round 44's findings
    def test_a_strategy_takes_no_secret_back(self):
        self.masks("sunshine", 'driver.find_element("id", "password").send_keys("sunshine")',
                   'driver.find_element("name", "password").send_keys("sunshine")',
                   'driver.find_element(by="id", value="password").send_keys("sunshine")')

    def test_describe_names_no_field(self):
        self.masks("sunshine", 'page.getByLabel("Password").describe("Sign in field").fill("sunshine")')

    def test_stays_a_filter_that_names_a_secret(self):
        self.stays('page.getByLabel("Email").filter({hasNotText: "Password"}).fill("me@example.com")',
                   'page.get_by_label("Email").filter(has_not_text="Password").fill("me@example.com")')

    # three shapes inside the rule no review had reported: a field's own locator under the one that names it
    def test_a_child_locator_under_a_secret_field(self):
        self.masks("sunshine", 'page.getByLabel("Password").locator("input").fill("sunshine")',
                   'page.get_by_role("group", name="Password").get_by_role("textbox").fill("sunshine")',
                   'page.locator("#password-field").locator("input").fill("sunshine")')

    # masked by design
    def test_by_design_a_container_that_asks_for_a_secret_marks_its_fields(self):
        self.masks("me@example.com", 'page.locator("#password-form").getByLabel("Email").fill("me@example.com")')
        self.masks("sam", 'page.locator("#credentials").getByLabel("Username").fill("sam")')

    def test_by_design_an_options_value_in_a_locator_call(self):
        self.masks("me@example.com", 'page.locator("input", {hasNotText: "Password"}).fill("me@example.com")')

    def test_by_design_a_frame_named_for_the_secret(self):
        self.masks("me@example.com", 'page.frame(name="password").fill("#email", "me@example.com")')

    # must stay
    def test_stays_describe_text_on_a_plain_field(self):
        self.stays('page.getByLabel("Email").describe("password field").fill("me@example.com")')

    def test_stays_a_container_named_with_another_word(self):
        self.stays('page.locator(".password-section").getByLabel("Email").fill("me@example.com")',
                   'page.get_by_test_id("password-reset").get_by_label("Email").fill("me@example.com")',
                   'page.locator("#change-password").getByLabel("Email").fill("me@example.com")',
                   'page.getByRole("dialog", {name: "Change password"}).getByLabel("Email").fill("me@example.com")')

    def test_stays_a_strategy_beside_a_plain_field(self):
        self.stays('driver.find_element("id", "username").send_keys("sam")',
                   'driver.find_element("id", "password_hint").send_keys("first pet")')

    def test_stays_a_chain_on_no_page(self):
        self.stays('Buffer.from("password").fill("x")')

    def test_stays_prose(self):
        self.stays('To fill the password field, call page.getByLabel("Password").fill(value) with the value.',
                   'The login form has a Password field and an Email field; fill("your text") types into the focus.')

    def test_ordinary_texts_stay(self):
        from tests import ordinary
        changed = [t for t in ordinary.texts(4000) if mask(t) != t]
        self.assertEqual(changed, [])



class ReviewFortyFiveTest(unittest.TestCase):
    """The forty-fifth review's findings: locator calls the union's list lacked (a frame locator, Selenium 3's
    find_element_by_*), and a receiver in brackets that read the strings of every call inside it, filter's and
    describe's too; with the other calls these libraries name a field by."""

    WRAPS, masks, stays = UnionFieldTest.WRAPS, UnionFieldTest.masks, UnionFieldTest.stays

    def test_each_locator_call_kind_names_its_field(self):
        self.masks("sunshine",
                   'page.frameLocator("#password").getByRole("textbox").fill("sunshine")',
                   'page.frame_locator("#password").get_by_role("textbox").fill("sunshine")',
                   'driver.find_element_by_id("password").send_keys("sunshine")',
                   'driver.find_element_by_css_selector("input[name=password]").send_keys("sunshine")',
                   'driver.find_elements_by_name("password")[0].send_keys("sunshine")',
                   'driver.FindElement(By.Id("password")).SendKeys("sunshine");',
                   '(await page.waitForSelector("#password")).type("sunshine")',
                   'page.wait_for_selector("#password").fill("sunshine")',
                   '(await page.$x("//input[@name=\'password\']"))[0].type("sunshine")')

    def test_stays_a_frame_locator_named_with_another_word(self):
        self.stays('page.frameLocator("#login").getByLabel("Email").fill("me@example.com")',
                   'page.frame_locator("#password-reset").get_by_label("Email").fill("me@example.com")')

    def test_a_receiver_in_brackets_reads_its_own_chain(self):
        self.stays('(page.getByLabel("Email").filter({hasNotText: "Password"})).fill("me@example.com")',
                   '(page.get_by_label("Email").filter(has_not_text="Password")).fill("me@example.com")',
                   '(page.getByLabel("Email").describe("Password")).fill("me@example.com")')
        self.masks("sunshine", '(await page.$("#password")).fill("sunshine")',
                   '(page.getByLabel("Password").filter({hasText: "Sign in"})).fill("sunshine")')



class ReviewFortySixTest(unittest.TestCase):
    """The forty-sixth review's findings: a listed finder on a variable no page check knew, a receiver in brackets after
    a keyword ("return (...)", "await (...)") read as a call to it, and a regex looked for inside a label's quotes."""

    WRAPS, masks, stays = UnionFieldTest.WRAPS, UnionFieldTest.masks, UnionFieldTest.stays

    def test_a_listed_finder_names_a_page_on_any_variable(self):
        self.masks("sunshine",
                   'd.find_element_by_id("password").send_keys("sunshine")',
                   'd.find_elements_by_name("password")[0].send_keys("sunshine")',
                   'd.FindElements(By.Id("password"))[0].SendKeys("sunshine");',
                   '(await p.waitForSelector("#password")).type("sunshine")',
                   'p.wait_for_selector("#password").fill("sunshine")',
                   '(await p.$x("//input[@name=\'password\']"))[0].type("sunshine")')

    def test_a_receiver_in_brackets_after_a_keyword(self):
        self.masks("sunshine",
                   'return (page.getByLabel("Password")).fill("sunshine");',
                   '(await (page.$("#password"))).type("sunshine")',
                   'yield (page.get_by_label("Password")).fill("sunshine")',
                   'void (page.getByLabel("Password")).fill("sunshine");')

    def test_stays_a_regex_inside_a_labels_quotes(self):
        self.stays('page.getByLabel("Search (/password/)").fill("release notes")',
                   "page.getByPlaceholder('Filter, e.g. (/token/i)').fill('release notes')")


class ReviewFortySevenTest(unittest.TestCase):
    """The forty-seventh review's findings and their kin: a dotted and(...) or or(...) read as the keyword, a selector=
    keyword on a variable no page check knew, and finder calls the union's list lacked; and a locator call given as
    the typing call's own field argument (user.type(screen.getByLabelText(...), text))."""

    WRAPS, masks, stays = UnionFieldTest.WRAPS, UnionFieldTest.masks, UnionFieldTest.stays

    def test_a_dotted_and_or_or_is_another_call_in_the_chain(self):
        self.masks("sunshine",
                   'page.getByLabel("Password").and(page.getByRole("textbox")).fill("sunshine")',
                   'page.getByLabel("Password").or(page.getByLabel("Email")).fill("sunshine")',
                   'page.getByLabel("Password").and (page.getByRole("textbox")).fill("sunshine")')

    def test_stays_a_dotted_and_or_or_with_no_secret_field(self):
        self.stays('page.getByLabel("Email").and(page.getByRole("textbox")).fill("me@example.com")',
                   'page.getByLabel("Email").or(page.getByLabel("Username")).fill("me@example.com")')

    def test_a_selector_keyword_on_any_variable(self):
        self.masks("sunshine",
                   'p.fill(value="sunshine", selector="#password")',
                   'p.type(selector="#password", text="sunshine")',
                   'tab.fill(selector="#password", value="sunshine")')

    def test_stays_a_selector_keyword_for_another_field(self):
        self.stays('p.fill(value="me@example.com", selector="#email")',
                   'tab.type(selector="#search", text="release notes")')

    def test_the_finder_calls_the_list_lacked(self):
        self.masks("sunshine",
                   '(await p.waitForXPath(`//*[@id="password"]`)).type("sunshine")',
                   '(await page.waitFor("#password")).type("sunshine")',
                   'browser.shadow$("#password").setValue("sunshine")',
                   'browser.shadow$$("input[name=password]")[0].setValue("sunshine")',
                   'browser.react$("Input", {props: {type: "password"}}).setValue("sunshine")',
                   'browser.react$$("PasswordInput")[0].setValue("sunshine")',
                   'browser.custom$("byTestId", "password").setValue("sunshine")',
                   '$("#password").addValue("sunshine")',
                   '(await screen.findByLabelText("Password")).type("sunshine")',
                   'screen.queryByLabelText("Password").type("sunshine")',
                   'screen.getAllByLabelText("Password")[0].type("sunshine")',
                   '(await page.J("#password")).type("sunshine")',
                   '(await page.JJ("input[name=password]"))[0].type("sunshine")',
                   '(await page.xpath("//input[@name=\'password\']"))[0].type("sunshine")')

    def test_cypress_finders(self):
        self.masks("sunshine",
                   'cy.get("#password").type("sunshine")',
                   "cy.get('input[name=\"password\"]').clear().type('sunshine')",
                   'cy.get("form").find("#password").type("sunshine", {log: false})',
                   'cy.findByLabelText("Password").type("sunshine")',
                   'cy.contains("label", "Password").parent().find("input").type("sunshine")')

    def test_seleniums_explicit_wait(self):
        self.masks("sunshine",
                   'WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "password")))'
                   '.send_keys("sunshine")',
                   'wait.until(ExpectedConditions.visibilityOfElementLocated(By.id("password"))).sendKeys("sunshine");',
                   'await driver.wait(until.elementLocated(By.css("#password")), 5000).sendKeys("sunshine");')

    def test_a_locator_call_as_the_calls_own_field(self):
        self.masks("sunshine",
                   'await user.type(screen.getByLabelText("Password"), "sunshine")',
                   'await userEvent.type(await screen.findByLabelText("Password"), "sunshine")',
                   'ActionChains(driver).send_keys_to_element(driver.find_element(By.ID, "password"), "sunshine")'
                   '.perform()')

    def test_stays_the_kin_on_another_field(self):
        self.stays('cy.get("#email").type("sam@example.com")',
                   'cy.get("#password-reset").find("#email").type("sam@example.com")',
                   'await user.type(screen.getByLabelText("Email"), "me@example.com")',
                   'browser.shadow$("#search").setValue("release notes")',
                   'WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "email")))'
                   '.send_keys("me@example.com")',
                   'settings.get("password_hint").type("first pet")')


class ReviewFortyEightTest(unittest.TestCase):
    """The forty-eighth review's findings: addValue missing from the typing names whose strings are values, never code,
    and a label's closing quote after "Password:" read as a quoted password's opening quote, masking ").fill(" and
    breaking the call the typing reader reads."""

    WRAPS, masks, stays = UnionFieldTest.WRAPS, UnionFieldTest.masks, UnionFieldTest.stays

    def test_what_add_value_types_is_a_value_not_code(self):
        self.stays('browser.$("#editor").addValue(\'page.fill("#password", "release notes")\')',
                   '$("#notes").add_value(\'driver.find_element(By.ID, "password").send_keys("release notes")\')')
        self.masks("Abc.fill('q')Z9!", 'browser.$("#password").addValue("Abc.fill(\'q\')Z9!")')

    def test_a_label_ending_in_a_colon_keeps_its_call(self):
        self.masks("sunshine",
                   'page.getByLabel("Password:").fill("sunshine")',
                   'page.get_by_label("Password: ").fill("sunshine")',
                   "page.getByLabel('New password:').fill('sunshine')")

    def test_stays_a_label_ending_in_a_colon_on_another_field(self):
        self.stays('page.getByLabel("Search passwords:").fill("release notes")',
                   'page.getByPlaceholder("Password hint:").fill("first pet")',
                   'print("Password:", "set in the vault")')

    def test_a_quoted_password_after_its_names_own_closed_quote(self):
        for text, value in (('DB_PASSWORD="Xk9,mP2;qL7v"', "Xk9,mP2;qL7v"),
                            ('{"password":"Xk9,mP2;qL7v"}', "Xk9,mP2;qL7v"),
                            ("password: 'Xk9,mP2;qL7v'", "Xk9,mP2;qL7v"),
                            ("cmd 'db_password=\"Xk9,mP2;qL7v\"'", "Xk9,mP2;qL7v"),
                            ('note "x" password: "Xk9,mP2;qL7v"', "Xk9,mP2;qL7v")):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text.replace(value, "[masked]"))


class ReviewFortyNineTest(unittest.TestCase):
    """The forty-ninth review's findings: a string's prefix (r"Password:", C#'s @"Password:") hid the label's opening
    quote, so its closing quote after "Password:" was read as a quoted password's opening, masking ").fill(" and
    breaking the call the typing reader reads; a word's apostrophe ("B's") stays an apostrophe."""

    WRAPS, masks, stays = UnionFieldTest.WRAPS, UnionFieldTest.masks, UnionFieldTest.stays

    def test_a_prefixed_label_keeps_its_call(self):
        self.masks("sunshine",
                   'page.get_by_label(r"Password:").fill("sunshine")',
                   "page.get_by_label(R'Password:').fill('sunshine')",
                   'page.get_by_label(f"Password:").fill("sunshine")',
                   'page.get_by_label(rf"Password:").fill("sunshine")',
                   'page.get_by_label(u"Password: ").fill("sunshine")',
                   'page.get_by_label(b"Password:").fill("sunshine")',
                   "page.get_by_label(r'New password:').fill('sunshine')")

    def test_stays_a_prefixed_label_on_another_field(self):
        self.stays('page.get_by_label(r"Search passwords:").fill("release notes")',
                   "page.get_by_placeholder(r'Password hint:').fill('first pet')",
                   'print(r"Password:", "set in the vault")',
                   'await page.GetByLabel(@"Search passwords:").FillAsync("release notes");',
                   'await page.GetByLabel($"Search passwords:").FillAsync("release notes");')

    def test_a_prefixed_label_in_code_kb_does_not_type_keeps_its_text(self):
        from tests.test_mask_against_main import main
        for code in ('await page.GetByLabel(@"Password:").FillAsync("sunshine");',
                     'await page.GetByLabel($"Password:").FillAsync("sunshine");',
                     'let label = Some(r#"Password:"#).unwrap_or("x");'):
            for wrap in self.WRAPS:
                with self.subTest(code=wrap(code)):
                    self.assertEqual(mask(wrap(code)), main.mask(wrap(code)))

    def test_an_apostrophe_after_a_letter_still_reads_the_password(self):
        for text in ("B's password: 'Xk9,mP2;qL7v'", "(T's password: 'Xk9,mP2;qL7v')",
                     "note for R's team: password='Xk9,mP2;qL7v'", "Plan B's db_password: 'Xk9,mP2;qL7v'"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text.replace("Xk9,mP2;qL7v", "[masked]"))


class AwsOptionValueTest(unittest.TestCase):
    """Found in #37's twelfth review: in aws configure set, a value of one of aws's options that take one ("--profile
    token") was read as the secret setting's name, masking the setting after it and leaving its value readable."""

    WRAPS, masks, stays = UnionFieldTest.WRAPS, UnionFieldTest.masks, UnionFieldTest.stays

    def test_an_options_value_is_not_the_setting(self):
        self.masks("Zq7vRt2LmX9k",
                   "aws configure set --profile token aws_secret_access_key Zq7vRt2LmX9k",
                   "aws configure set --region us-east-1 --profile secret aws_session_token Zq7vRt2LmX9k",
                   "aws configure set --profile 'token' aws_secret_access_key Zq7vRt2LmX9k",
                   "aws configure set '--profile' token aws_secret_access_key Zq7vRt2LmX9k",
                   "aws configure set --output token --query password default.aws_session_token Zq7vRt2LmX9k",
                   "aws configure set aws_secret_access_key Zq7vRt2LmX9k --profile token",
                   "aws configure set --profile=token aws_secret_access_key Zq7vRt2LmX9k")

    def test_stays_a_setting_after_an_options_value(self):
        self.stays("aws configure set --profile token max_attempts 10",
                   "aws configure set --profile token region us-west-2",
                   "aws configure set --profile ci_token output json")


class ReviewFiftyTest(unittest.TestCase):
    """The fiftieth review's findings: a string's prefix after a space ("( r\"Password:\"") and a label starting with
    "Re-" (r"Re-enter password:"), read as a word's ending, still hid the label's opening quote, and a line's
    continuation between aws's option and its value ("--profile \\" then "token" on the next line) hid the option.
    Found with them: "type" as a label's verb ("Type your password", "Re-type password") read as one about the secret."""

    WRAPS, masks, stays = UnionFieldTest.WRAPS, UnionFieldTest.masks, UnionFieldTest.stays

    def test_a_prefixed_label_after_a_space_or_starting_with_re_keeps_its_call(self):
        self.masks("sunshine",
                   'page.get_by_label( r"Password:").fill("sunshine")',
                   'page.get_by_label( f"New password:").fill("sunshine")',
                   'page.get_by_label(r"Re-enter password:").fill("sunshine")',
                   "page.get_by_label(R'Re-enter password:').fill('sunshine')",
                   'page.get_by_label( r"Re-enter Password:").fill("sunshine")')

    def test_stays_a_prefixed_label_on_another_field_after_a_space(self):
        self.stays('page.get_by_label( r"Search passwords:").fill("release notes")',
                   "page.get_by_placeholder( r'Password hint:').fill('first pet')")

    def test_a_word_ending_after_a_letter_is_still_an_apostrophe(self):
        for text in ("Plan B's password: 'Xk9,mP2;qL7v'", "(T's password: 'Xk9,mP2;qL7v')",
                     "note for R's team, password='Xk9,mP2;qL7v'", "U'd say the password: 'Xk9,mP2;qL7v'"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text.replace("Xk9,mP2;qL7v", "[masked]"))

    def test_an_aws_options_value_on_a_continued_line(self):
        for text in ("aws configure set --profile \\\n token aws_secret_access_key Zq7vRt2LmX9k",
                     "aws configure set --region us-east-1 --profile \\\n  secret aws_session_token Zq7vRt2LmX9k"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text.replace("Zq7vRt2LmX9k", "[masked]"))
        self.stays("aws configure set --profile \\\n token max_attempts 10",
                   "aws configure set --profile \\\n    token region us-west-2")

    def test_a_continued_line_the_shell_does_not_join_masks_at_most_the_value(self):
        """bash joins no line after a backslash before CR LF, and a JSON string's escaped line break is read as the
        line's end: the value there shows as main shows it (ON-MAIN), and nothing else is masked."""
        for code in ("aws configure set --profile \\\n token aws_secret_access_key Zq7vRt2LmX9k",
                     "aws configure set --profile \\\r\n    token aws_secret_access_key Zq7vRt2LmX9k"):
            for wrap in self.WRAPS:
                with self.subTest(text=wrap(code)[:90]):
                    self.assertIn(mask(wrap(code)), (wrap(code), wrap(code.replace("Zq7vRt2LmX9k", "[masked]"))))

    def test_type_as_the_labels_verb_asks_for_the_secret(self):
        self.masks("sunshine",
                   "page.get_by_label(R'Re-type password:').fill('sunshine')",
                   'page.get_by_label("Type your password").fill("sunshine")',
                   'page.get_by_label("Re type password").fill("sunshine")',
                   'page.get_by_placeholder("Type password").fill("sunshine")',
                   'page.locator("#typePassword").fill("sunshine")')

    def test_stays_type_as_the_secrets_kind(self):
        self.stays('page.get_by_label("Password type").fill("release notes")',
                   'page.get_by_label("Type of password").fill("release notes")',
                   'page.get_by_label("Type: password").fill("release notes")',
                   'page.get_by_label("Token types").fill("release notes")',
                   'page.locator("#passwordType").fill("release notes")')


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
