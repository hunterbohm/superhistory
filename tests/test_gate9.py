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
