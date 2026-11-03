"""The wrappings gate 9 left (mask-wrappings): a supplementary invisible character JSON writes as a surrogate pair, and a
named command's password where kb cannot read the command plainly: shell grammar it does not follow, a line carried on
by a continuation, a program name or flag inside a colour code, and a command given as a list of arguments. Made-up
secrets only."""
import json
import time
import unittest

from kb import mask as masking
from kb.mask import mask, mask_all

PASSWORD = "Zq7vRt2LmX9k"
TOKEN = "ghp_Ab3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6"
TAIL = TOKEN[15:]

# The commands gate 9 reads as the shell reads them, each with its password flag.
COMMANDS = (("keytool -list", "-storepass"), ("jarsigner -verify app.jar", "-storepass"),
            ("az login -u admin@example.com", "-p"), ("mongosh --host db -u admin", "-p"),
            ("twine upload dist/*", "-p"), ("aws configure set", "aws_secret_access_key"))

# Shell grammar kb's reading of a command line does not follow, each before the password flag.
UNREAD = {
    "ANSI-C quotes": "-alias $'team\\'s cert'",
    "process substitution": "-file <(cat cert.pem)",
    "output substitution": "-file >(tee cert.log)",
    "extended glob": "-file @(a|b).pem",
    "PowerShell escape": '-alias "team`"s cert"',
    "escaped quote": '-alias team\\"s',
}

# Shell grammar that, escaped twice, as a JSON string inside JSON holds it, kb does not follow either.
UNREAD_TWICE = {
    "nested substitution": '-alias "$(echo "$(hostname)")"',
    "bracket in a substitution's quotes": "-alias \"$(echo ')')\"",
    "escaped quote in quotes": '-alias "team \\"x\\" cert"',
    "parameter default": "-alias \"${ALIAS:-team's}\"",
    "pipe in quotes": '-alias "a|b"',
}


def forms(line):
    return {"raw": line, "json": json.dumps(line), "json twice": json.dumps(json.dumps(line))}


def took(text):
    best = None
    for _ in range(3):
        started = time.process_time()
        mask(text)
        spent = time.process_time() - started
        best = spent if best is None else min(best, spent)
    return best


class SurrogatePairTest(unittest.TestCase):
    """JSON writes a character past U+FFFF as two escapes, a surrogate pair: U+E0001 is "\\udb40\\udc01"."""

    INVISIBLES = ("\U000e0001", "\U000e0020", "\U000e007f", "\U000e0100", "\U000e01ef", "\U0001bca0", "\U0001d173")

    def test_a_token_split_by_an_escaped_surrogate_pair(self):
        for invisible in self.INVISIBLES:
            split = TOKEN[:15] + invisible + TOKEN[15:]
            for text in (split, f"export GITHUB_TOKEN={split}", f"the token is {split} now"):
                for name, form in (("json", json.dumps(text)), ("json twice", json.dumps(json.dumps(text))),
                                   ("in an object", json.dumps({"text": text})),
                                   ("upper-case hex", json.dumps(text).replace("\\udb40", "\\uDB40")
                                    .replace("\\udc", "\\uDC").replace("\\ud8", "\\uD8"))):
                    with self.subTest(invisible=hex(ord(invisible)), form=name, text=text[:20]):
                        self.assertNotIn(TAIL, mask(form))

    def test_a_token_split_by_an_escape_written_twice(self):
        for invisible in ("\u200b", "\u2060", "\ufeff", "\x7f", "\x00"):
            text = json.dumps(json.dumps(f"token {TOKEN[:15]}{invisible}{TOKEN[15:]}"))
            with self.subTest(invisible=hex(ord(invisible))):
                self.assertNotIn(TAIL, mask(text))

    def test_a_pair_for_a_character_one_sees_stays(self):
        for text in ("deploy \U0001f600 done", "release \U0001f680 ok", "budget \U00020000 plan", "x\U0001d400y"):
            for form in (json.dumps(text), json.dumps(json.dumps(text))):
                with self.subTest(text=form):
                    self.assertEqual(mask(form), form)


class UnreadCommandTest(unittest.TestCase):
    """The fail-closed rule: where a named command's password flag sits in a part of its line kb cannot read plainly,
    everything from the value after the flag to the end of that line is masked."""

    def test_a_password_after_grammar_kb_does_not_follow(self):
        for command, flag in COMMANDS:
            for name, part in UNREAD.items():
                line = f"{command} {part} {flag} {PASSWORD}"
                for form_name, form in forms(line).items():
                    with self.subTest(command=command, grammar=name, form=form_name):
                        masked = mask(form)
                        self.assertNotIn(PASSWORD, masked)
                        self.assertIn(command.split()[0], masked)

    def test_a_password_after_grammar_escaped_twice(self):
        for command, flag in COMMANDS:
            for name, part in UNREAD_TWICE.items():
                text = json.dumps(json.dumps(f"{command} {part} {flag} {PASSWORD}"))
                with self.subTest(command=command, grammar=name):
                    self.assertNotIn(PASSWORD, mask(text))

    def test_a_password_in_a_command_held_in_json_fields(self):
        for command, flag in COMMANDS:
            line = f"{command} -file <(cat cert.pem) {flag} {PASSWORD}"
            text = json.dumps({"cmd": line, "cwd": "/srv/certs", "timeout": 30})
            with self.subTest(command=command):
                masked = mask(text)
                self.assertNotIn(PASSWORD, masked)
                self.assertIn('"cwd": "/srv/certs", "timeout": 30}', masked)

    def test_the_mask_runs_to_the_line_end_only(self):
        text = f"keytool -list -alias $'team\\'s cert' -storepass {PASSWORD} -v\nls -la /srv/certs\n"
        masked = mask(text)
        self.assertNotIn(PASSWORD, masked)
        self.assertTrue(masked.startswith("keytool -list -alias $'team\\'s cert' -storepass [masked]"), masked)
        self.assertTrue(masked.endswith("\nls -la /srv/certs\n"), masked)

    def test_an_option_or_a_variable_after_the_flag_stays(self):
        for text in ("keytool -list -file <(cat cert.pem) -storepass $STOREPASS",
                     'keytool -list -file <(cat cert.pem) -storepass "$STOREPASS" -v',
                     "mongosh -f <(cat init.js) -u admin -p --quiet",
                     "aws configure set region <(cat r) aws_session_token_expiration 3600"):
            for form in (text, json.dumps(text)):
                with self.subTest(text=form):
                    self.assertEqual(mask(form), form)

    def test_ordinary_command_lines_stay(self):
        lines = ("keytool -list -v -keystore ~/.keystore -alias $'team\\'s cert'",
                 "keytool -importcert -file <(openssl s_client -connect host:443 </dev/null) -alias host",
                 'mongosh --eval "$(cat <<EOF\nprint(1)\nEOF\n)" --quiet',
                 "az login --identity # managed identity, no -p needed",
                 "twine upload dist/@(*.whl|*.tar.gz) --verbose",
                 "mongosh --port $((27000 + OFFSET)) --eval 'db.stats()' | ssh -p 2222 host",
                 "aws configure set region us-east-1 && ssh -p 2222 host",
                 "keytool " + " ".join(f"-J-Dopt{i}=v{i}" for i in range(80)) + " -list",
                 "mongosh --eval 'print(\"ssh -p 2222 host\")\n'",
                 "mongosh --quiet # ssh -p 2222 host")
        for line in lines:
            for form_name, form in forms(line).items():
                with self.subTest(line=line[:40], form=form_name):
                    self.assertEqual(mask(form), form)


class ContinuationTest(unittest.TestCase):
    """A command over several lines, each but the last ending in the shell's continuation mark."""

    LINES = (("curl https://api.example.com/v1/items", f"-u deploy:{PASSWORD}", "-H 'Accept: application/json'"),
             ("mysql -h db.example.com", f"-u root -p{PASSWORD}", "shop"),
             ("docker login registry.example.com", f"-u ci -p {PASSWORD}", "--tls-verify"),
             ("redis-cli -h cache", f"-a {PASSWORD}", "ping"),
             ("mongosh --host db", f"-u admin -p {PASSWORD}", "--quiet"),
             ("keytool -list", f"-storepass {PASSWORD}", "-v"),
             ("az login", f"-u admin@example.com -p {PASSWORD}", "--allow-no-subscriptions"))

    def test_a_password_on_a_continued_line(self):
        for first, second, third in self.LINES:
            for mark, newline in (("\\", "\n"), ("\\", "\r\n"), ("`", "\n"), ("^", "\r\n")):
                text = f"{first} {mark}{newline}  {second} {mark}{newline}  {third}"
                for form_name, form in forms(text).items():
                    with self.subTest(command=first, mark=mark, form=form_name):
                        masked = mask(form)
                        self.assertNotIn(PASSWORD, masked)
                        self.assertIn(third, masked)

    def test_a_line_that_ends_without_a_mark_ends_the_command(self):
        for text in ("curl https://example.com/health\n-u is the flag that sets a user",
                     "keytool -list\n-storepass is read on the next run"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)


class ColouredCommandTest(unittest.TestCase):
    """A command line as grep --color prints a match in it: the program's name, its flag or its value coloured."""

    LINES = ((f"curl -u deploy:{PASSWORD} https://api.example.com", ("curl", "-u", "deploy")),
             (f"mysql -u root -p{PASSWORD} shop", ("mysql",)),
             (f"docker login -u ci -p {PASSWORD} registry.example.com", ("docker", "-p")),
             (f"keytool -list -storepass {PASSWORD} -v", ("keytool", "-storepass")),
             (f"mongosh -u admin -p {PASSWORD} --quiet", ("mongosh", "-p")),
             (f"az login -u admin@example.com -p {PASSWORD}", ("az", "login", "-p")))

    def test_a_password_in_a_coloured_command_line(self):
        for line, words in self.LINES:
            for word in words + (PASSWORD,):
                for c in ("\x1b[", "\\u001b["):
                    coloured = line.replace(word, f"{c}01;31m{c}K{word}{c}m{c}K", 1)
                    for text in (coloured, f"history:12:{coloured}", f"{c}32m$ {c}0m{line}"):
                        with self.subTest(line=line[:12], word=word, form=repr(c), text=text[:12]):
                            masked = mask(text)
                            self.assertNotIn(PASSWORD, masked)
                            self.assertIn(word if word != PASSWORD else line.split()[0], masked)


class ArgumentListTest(unittest.TestCase):
    """A command given as a list of its arguments, in text as JSON writes the list, and in an item's metadata."""

    LISTS = (["keytool", "-list", "-alias", "team's cert", "-storepass", PASSWORD],
             ["az", "login", "-u", "admin@example.com", "-p", PASSWORD],
             ["mongosh", "--host", "db", "-u", "admin", "-p", PASSWORD],
             ["/usr/lib/jvm/bin/keytool", "-list", "-storepass", PASSWORD, "-v"],
             ["C:\\Program Files\\Java\\bin\\keytool.exe", "-list", "-storepass", PASSWORD])

    def test_a_list_of_arguments_in_text(self):
        for items in self.LISTS:
            for text in (json.dumps(items), json.dumps({"tool": "exec", "argv": items, "cwd": "/srv"}),
                         json.dumps(items, indent=2)):
                with self.subTest(text=text[:40]):
                    masked = mask(text)
                    self.assertNotIn(PASSWORD, masked)
                    self.assertEqual(json.loads(masked) if text.startswith("[") else json.loads(masked)["argv"],
                                     ["[masked]" if v == PASSWORD else v for v in items])

    def test_a_long_list_of_arguments(self):
        for items in (["mongosh", *(f"--opt{i}" for i in range(100)), "-p", PASSWORD],
                      ["keytool", "-list", *(f"-J-Dopt{i}=v{i}" for i in range(100)), "-storepass", PASSWORD]):
            with self.subTest(command=items[0]):
                self.assertEqual(mask_all(items)[-1], "[masked]")
                self.assertNotIn(PASSWORD, mask(json.dumps(items)))

    def test_a_flag_and_its_value_in_one_item(self):
        for items in (["keytool", "-alias", "team's cert", f"-storepass {PASSWORD}"],
                      ["mongosh", "-u", "admin", f"-p {PASSWORD}"], ["az", "login", f"-p {PASSWORD}"]):
            with self.subTest(items=items[0]):
                masked = mask_all(items)
                self.assertNotIn(PASSWORD, json.dumps(masked))
                self.assertEqual(masked[:-1], items[:-1])
                self.assertTrue(masked[-1].startswith(items[-1].split()[0]), masked)
                self.assertNotIn(PASSWORD, mask(json.dumps(items)))

    def test_ordinary_lists_stay(self):
        for items in (["twine", "upload", "--comment", "note: -p release-2026", "dist/*"],
                      ["keytool", "-list", "-v", "-keystore", "team's.jks"], ["ssh", "-p", "2222", "host"],
                      ["mongosh", "--eval", "db.stats()", "--quiet"], ["git", "commit", "-m", "-p is the port"],
                      ["keytool", "-storepass:env", "STOREPASS", "-list"]):
            with self.subTest(items=items):
                self.assertEqual(mask_all(items), items)
                self.assertEqual(mask(json.dumps(items)), json.dumps(items))


class CostTest(unittest.TestCase):
    """Each new reading costs time in proportion to the text."""

    UNITS = ("keytool -list -alias $'a\\'b' -storepass Zq7vRt2LmX9k\n", "keytool -file <(cat c) -storepass x ",
             "curl https://example.com \\\n  -u deploy:Zq7vRt2LmX9k \\\n", "mongosh -u admin `\n",
             "\x1b[01;31m\x1b[Kkeytool\x1b[m\x1b[K -storepass Zq7vRt2LmX9k\n", "$'\\'", "<(<(",
             json.dumps(["keytool", "-storepass", "Zq7vRt2LmX9k"]) + ",", '["keytool", ', "\\udb40\\udc01",
             "ghp_Ab3dE6gH9jK\\\\udb40\\\\udc01")

    def test_each_reading_costs_time_in_proportion(self):
        for unit in self.UNITS:
            with self.subTest(unit=unit[:24]):
                small, large = took(unit * (2000 // len(unit) + 1)), took(unit * (16000 // len(unit) + 1))
                self.assertLess(large, 20 * small + 0.05, (small, large))


class RevisionTest(unittest.TestCase):
    def test_stored_rows_are_masked_again(self):
        self.assertEqual(masking.REVISION, 11)


if __name__ == "__main__":
    unittest.main()
