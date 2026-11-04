"""The wrappings gate 9 left (mask-wrappings): a supplementary invisible character JSON writes as a surrogate pair, and a
named command's password where kb cannot read the command plainly: shell grammar it does not follow, a line carried on
by a continuation, a program name or flag inside a colour code, and a command given as a list of arguments. Made-up
secrets only."""
import ast
import json
import os
import shlex
import subprocess
import tempfile
import time
import unittest

from kb import mask as masking, store
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


def took_call(fn):
    best = None
    for _ in range(3):
        started = time.process_time()
        fn()
        spent = time.process_time() - started
        best = spent if best is None else min(best, spent)
    return best


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


class ReviewOneTest(unittest.TestCase):
    """#37's first review: a named command kb did not recognise at all, wherever it stands: colour inside its name, its
    Windows file, its quoted name or its list of arguments escaped at any depth, or Python's repr of the list."""

    GREP = "\x1b[01;31m\x1b[K{}\x1b[m\x1b[K"

    def test_colour_inside_a_program_name(self):
        for line, parts in ((f"keytool -list -storepass {PASSWORD}", ("tool", "key", "store", "pass", "eyto")),
                            (f"mongosh -u admin -p {PASSWORD}", ("mongo", "sh", "gos")),
                            (f"az login -u admin@example.com -p {PASSWORD}", ("az", "log", "in")),
                            (f"aws configure set aws_secret_access_key {PASSWORD}", ("aws", "conf", "secret"))):
            for part in parts:
                coloured = line.replace(part, self.GREP.format(part), 1)
                for text in (coloured, json.dumps(coloured).replace("\\u001b", "\\u001b")):
                    with self.subTest(line=line[:10], part=part, text=text[:12]):
                        self.assertNotIn(PASSWORD, mask(text))

    def test_a_token_split_by_an_escaped_backspace(self):
        split = TOKEN[:15] + "\b" + TOKEN[15:]
        for text in (json.dumps(split), json.dumps(json.dumps(split)), json.dumps({"output": f"token {split}"})):
            with self.subTest(text=text[:30]):
                self.assertNotIn(TAIL, mask(text))

    def test_a_word_boundary_in_code_stays(self):
        for text in ('re.compile(r"\\btoken\\b")', json.dumps('pattern = r"\\bpassword\\b"'),
                     json.dumps(r"grep -P '\bsecret\b' notes.md"), "\\bkey\\b and \\bvalue\\b"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)

    def test_a_quote_in_a_plain_line_is_no_json_string_end(self):
        for text in (f"keytool -importcert -file <(jq -r '.[\"cert\"]' cert.json) -storepass {PASSWORD}",
                     f"keytool -importcert -file <(jq -r '.cert, .key' c.json) -storepass {PASSWORD}",
                     f"mongosh -f <(jq '{{\"a\": 1}}' x.json) -u admin -p {PASSWORD}"):
            for form in (text, json.dumps(text), json.dumps({"cmd": text, "cwd": "/srv"})):
                with self.subTest(text=form):
                    self.assertNotIn(PASSWORD, mask(form))
                    self.assertTrue(form.startswith("keytool") or '"cwd": "/srv"}' not in form
                                    or mask(form).endswith('"cwd": "/srv"}'), mask(form))

    def test_a_json_string_keeps_its_quotes_past_the_mask(self):
        line = f"keytool -file <(cat cert.pem) -storepass {PASSWORD}"
        for text in (json.dumps(line), json.dumps(json.dumps(line)), json.dumps(json.dumps(json.dumps(line)))):
            with self.subTest(text=text[:12]):
                masked = mask(text)
                self.assertNotIn(PASSWORD, masked)
                self.assertEqual(masked, text.replace(PASSWORD, "[masked]"))

    def test_a_windows_program_file(self):
        for text in (f"aws.exe configure set aws_secret_access_key {PASSWORD}",
                     f'"C:\\Program Files\\Amazon\\AWSCLIV2\\aws.exe" configure set aws_secret_access_key {PASSWORD}',
                     f"az.exe login -u admin@example.com -p {PASSWORD}", f"az.cmd login -u admin@example.com -p {PASSWORD}",
                     f'"C:\\Program Files\\Microsoft SDKs\\Azure\\CLI2\\wbin\\az.cmd" login -u admin -p {PASSWORD}'):
            for form in (text, json.dumps(text)):
                with self.subTest(text=form[:40]):
                    self.assertNotIn(PASSWORD, mask(form))

    def test_a_quoted_program_name_escaped_at_any_depth(self):
        for line in (f'"keytool" -storepass {PASSWORD}', f"'mongosh' -u admin -p {PASSWORD}",
                     f'"az" login -u admin -p {PASSWORD}', f'"/opt/jdk/bin/keytool" -list -storepass {PASSWORD}'):
            text = line
            for depth in range(1, 4):
                text = json.dumps({"output": text} if depth == 2 else {"command": text})
                with self.subTest(line=line[:12], depth=depth):
                    self.assertNotIn(PASSWORD, mask(text))

    def test_a_list_of_arguments_escaped_at_any_depth(self):
        for items in (["keytool", "-storepass", PASSWORD], ["mongosh", "-u", "admin", "-p", PASSWORD],
                      ["az", "login", "-p", PASSWORD], ["keytool", "-alias", "team's cert", "-storepass", PASSWORD]):
            text = json.dumps(items)
            for depth in range(1, 4):
                text = json.dumps({"output": text})
                with self.subTest(command=items[0], depth=depth):
                    masked = mask(text)
                    self.assertNotIn(PASSWORD, masked)
                    self.assertIn("-storepass" if items[0] == "keytool" else "-p", masked)

    def test_python_prints_of_a_list_of_arguments(self):
        for items in (["keytool", "-storepass", PASSWORD], ["mongosh", "-u", "admin", "-p", PASSWORD],
                      ["keytool", "-alias", "team's cert", "-storepass", PASSWORD]):
            for text in (str(subprocess.CalledProcessError(1, items)), repr(items), repr(tuple(items)),
                         f"args={items!r}", json.dumps(str(subprocess.CalledProcessError(1, items)))):
                with self.subTest(text=text[:40]):
                    masked = mask(text)
                    self.assertNotIn(PASSWORD, masked)
                    self.assertIn("keytool" if items[0] == "keytool" else "mongosh", masked)

    def test_wrapped_ordinary_command_lines_stay(self):
        lines = ("mongosh --eval 'print(\"ssh -p 2222 host\")'", 'twine upload --comment "-p release-2026" dist/*',
                 "keytool -list -v -alias \"-storepass example\" -keystore k.jks",
                 "keytool -list -storepass $STOREPASS", "mongosh -u admin -p --quiet")
        for line in lines:
            for text in (json.dumps({"output": json.dumps({"command": line})}),
                         json.dumps({"output": json.dumps(shlex.split(line))}), repr(shlex.split(line))):
                with self.subTest(text=text[:50]):
                    self.assertEqual(mask(text), text)

    def test_prose_about_the_commands_stays(self):
        for text in ("Run keytool -list to see the entries; keytool asks for the store password.",
                     "az login opens a browser; with -u and -p it signs in a user without one.",
                     "The keytool -storepass option takes the keystore's password.",
                     "Use az login --service-principal -u <app-id> -p <password-or-cert> --tenant <tenant>.",
                     "keytool -storepass:env STOREPASS reads the password from the environment.",
                     "mongosh -p prompts for the password when no value follows it.",
                     "aws configure set region us-east-1 sets the default region",
                     "Run az.cmd login -p $STOREPASS.", "Pass keytool.exe -storepass %STOREPASS%, then -v."):
            for form in (text, json.dumps(text), json.dumps(json.dumps(text))):
                with self.subTest(text=form[:40]):
                    self.assertEqual(mask(form), form)


class ReviewTwoTest(unittest.TestCase):
    """#37's second review: aws configure set's settings in a list, a backspace after "_", a shell's -c script in a
    list, Python's escaped single quotes, colour codes escaped twice, a long JSON string, and long runs of
    backslashes."""

    def test_an_aws_setting_in_a_list_of_arguments(self):
        for items in (["aws", "configure", "set", "aws_secret_access_key", PASSWORD],
                      ["aws", "configure", "set", "default.aws_session_token", PASSWORD]):
            with self.subTest(items=items[3]):
                self.assertEqual(mask_all(items)[-1], "[masked]")
                for text in (json.dumps(items), repr(items), json.dumps({"output": json.dumps(items)})):
                    self.assertNotIn(PASSWORD, mask(text))
        for items in (["aws", "configure", "set", "region", "us-east-1"],
                      ["aws", "configure", "set", "aws_session_token_expiration", "3600"]):
            with self.subTest(items=items[3]):
                self.assertEqual(mask_all(items), items)

    def test_a_backspace_after_an_underscore(self):
        token = "ghp_" + "\b" + TOKEN[4:]
        for text in (json.dumps(token), json.dumps(json.dumps(token)), json.dumps(f"token {TOKEN[:4]}\b{TOKEN[4:]}")):
            with self.subTest(text=text[:20]):
                self.assertNotIn(TOKEN[10:], mask(text))
        for text in (json.dumps(r"re.compile(r'_\bname\b')"), r"pattern = '\b_private\b'", r"x_\bword"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)

    def test_a_shells_script_in_a_list_of_arguments(self):
        for shell in ("bash", "sh", "zsh", "/bin/bash"):
            for script in (f"keytool -list -storepass {PASSWORD}", f"keytool \\\n -storepass {PASSWORD}",
                           f"cd /srv && mongosh -u admin -p {PASSWORD}"):
                items = [shell, "-c", script]
                for text in (str(subprocess.CalledProcessError(1, items)), json.dumps(items), repr(items)):
                    with self.subTest(shell=shell, script=script[:12], text=text[:12]):
                        self.assertNotIn(PASSWORD, mask(text))
        items = ["bash", "-c", "mongosh --eval 'print(\"ssh -p 2222 host\")'"]
        self.assertEqual(mask(repr(items)), repr(items))

    def test_python_escaped_single_quotes(self):
        for args in (f"'keytool' -alias \"team cert\" -storepass {PASSWORD}", f"keytool -alias 'team cert' -storepass {PASSWORD}"):
            process = subprocess.CompletedProcess(args, 1)
            for text in (repr(process), json.dumps(process, default=repr), json.dumps(json.dumps(process, default=repr))):
                with self.subTest(args=args[:10], text=text[:20]):
                    self.assertNotIn(PASSWORD, mask(text))

    def test_colour_codes_escaped_twice(self):
        G = "\x1b[01;31m\x1b[K{}\x1b[m\x1b[K"
        for line, word in ((f"keytool -storepass {PASSWORD}", "keytool"), (f"keytool -storepass {PASSWORD}", "-storepass"),
                           (f"keytool -storepass {PASSWORD}", PASSWORD), (f"mongosh -u admin -p {PASSWORD}", "mongosh")):
            coloured = line.replace(word, G.format(word), 1) + "\n"
            for depth in (2, 3):
                text = coloured
                for _ in range(depth):
                    text = json.dumps(text)
                with self.subTest(word=word[:8], depth=depth):
                    self.assertNotIn(PASSWORD, mask(text))

    def test_the_mask_stays_in_its_json_string(self):
        for prefix in (100, 4100, 20000):
            text = json.dumps({"cmd": f"printf %s {'a' * prefix}; keytool -file <(cat cert.pem) -storepass {PASSWORD}",
                               "cwd": "/srv/certs", "timeout": 30})
            for form in (text, json.dumps({"output": text})):
                with self.subTest(prefix=prefix, nested=form is not text):
                    masked = mask(form)
                    self.assertNotIn(PASSWORD, masked)
                    self.assertEqual(json.loads(masked) if form is text else json.loads(json.loads(masked)["output"]),
                                     {"cmd": f"printf %s {'a' * prefix}; keytool -file <(cat cert.pem) -storepass [masked]",
                                      "cwd": "/srv/certs", "timeout": 30})

    def test_long_runs_of_backslashes_cost_time_in_proportion(self):
        def escaped(n):
            text = f'keytool -alias "team cert" -storepass {PASSWORD}'
            for _ in range(n):
                text = json.dumps(text)
            return text
        small, large = escaped(10), escaped(12)
        self.assertGreater(len(large), 3 * len(small))
        self.assertNotIn(PASSWORD, mask(large))
        self.assertLess(took(large), 6 * took(small) + 0.3)
        self.assertLess(took(large), 1.5)
        run = "\\" * 16000
        self.assertLess(took(f"keytool -storepass {run}\"x"), 1.0)


class ReviewThreeTest(unittest.TestCase):
    """#37's third review: a JSON string whose text holds quotes of its own, a backspace beside "-" or ".", a shell's
    -c script that holds quotes, and a colour code escaped five times or more."""

    def test_the_mask_stays_in_a_string_that_holds_quotes(self):
        for cmd in (f"echo '[\"x\"]'; keytool -file <(cat cert.pem) -storepass {PASSWORD}",
                    f'echo "{{\\"a\\": 1}}"; keytool -file <(cat cert.pem) -storepass {PASSWORD}',
                    f"jq '.[\"k\"], .v' x.json; keytool -file <(jq -r '.[\"cert\"]' c.json) -storepass {PASSWORD}"):
            value = {"cmd": cmd, "cwd": "/srv/certs", "timeout": 30}
            for depth in (1, 2, 3):
                text = json.dumps(value)
                for _ in range(depth - 1):
                    text = json.dumps({"output": text})
                with self.subTest(cmd=cmd[:12], depth=depth):
                    masked = mask(text)
                    self.assertNotIn(PASSWORD, masked)
                    read = json.loads(masked)
                    for _ in range(depth - 1):
                        read = json.loads(read["output"])
                    self.assertEqual(read["cwd"], "/srv/certs")
                    self.assertEqual(read["timeout"], 30)
                    self.assertTrue(read["cmd"].startswith(cmd.split(";")[0]), read["cmd"])

    def test_a_backspace_beside_a_tokens_dash_or_dot(self):
        tokens = ("xoxb-1234567890123-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx",
                  "glpat-Ab3dE6gH9jK2mN5pQ8sT", "sk_live_51Hq8ZtKx9Lm2Np4Qr6St8Uv")
        for token in tokens:
            for at in [i for i, c in enumerate(token) if c in "-._"] + [i + 1 for i, c in enumerate(token) if c in "-._"]:
                split = token[:at] + "\b" + token[at:]
                for text in (json.dumps(split), json.dumps(json.dumps(split))):
                    with self.subTest(token=token[:6], at=at, depth=text.count("\\\\") > 0):
                        self.assertNotIn(token[-8:], mask(text))
        for text in (r"re.compile(r'\btoken\b')", json.dumps(r"grep -P '\bv1.2\b' notes.md"),
                     json.dumps("split on \b-\b or \b.\b"), r"pattern = '-\b' and '\b.'"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)

    def test_a_shells_script_keeps_its_text_past_the_mask(self):
        for script, kept in ((f"keytool -alias 'team cert' -storepass {PASSWORD}", "keytool -alias 'team cert' -storepass "),
                             (f'keytool -alias "team cert" -storepass {PASSWORD} -v', 'keytool -alias "team cert" -storepass '),
                             (f"cd '/srv/my certs' && mongosh -u admin -p {PASSWORD}", "cd '/srv/my certs' && mongosh -u admin -p ")):
            items = ["bash", "-c", script]
            with self.subTest(script=script[:14]):
                masked = mask_all(items)
                self.assertNotIn(PASSWORD, masked[2])
                self.assertTrue(masked[2].startswith(kept), masked[2])
                for text in (str(subprocess.CalledProcessError(1, items)), json.dumps(items), repr(items)):
                    shown = mask(text)
                    self.assertNotIn(PASSWORD, shown)
                    self.assertIn("team cert" if "team" in script else "my certs", shown)

    def test_a_colour_code_escaped_many_times(self):
        coloured = "token \x1b[01;31m\x1b[K" + TOKEN + "\x1b[m\x1b[K"
        for depth in range(1, 9):
            text = coloured
            for _ in range(depth):
                text = json.dumps(text)
            with self.subTest(depth=depth):
                self.assertNotIn(TAIL, mask(text))

    def test_a_colour_code_escaped_many_times_costs_time_in_proportion(self):
        unit = "token \x1b[01;31m\x1b[K" + TOKEN + "\x1b[m\x1b[K"
        for _ in range(7):
            unit = json.dumps(unit)
        small, large = took(unit * (4000 // len(unit) + 1)), took(unit * (16000 // len(unit) + 1))
        self.assertLess(large, 6 * small + 0.3, (small, large))
        self.assertLess(took("\\" * 16000 + "u001b[31m"), 1.0)


class ReviewFourTest(unittest.TestCase):
    """#37's fourth review: a view only says where to mask, so the text kept is the text as written, at any depth; the
    fail-closed rule in unescaped views; aws by its path; and a backspace beside base64's punctuation."""

    def test_a_quoted_program_name_keeps_its_string(self):
        for cmd in (f'"keytool" -file <(cat cert.pem) -storepass {PASSWORD}',
                    f'"/opt/jdk/bin/keytool" -file <(cat c) -storepass {PASSWORD}',
                    f"'mongosh' -f <(cat init.js) -u admin -p {PASSWORD}"):
            value = {"cmd": cmd, "cwd": "/srv/certs", "timeout": 30}
            for depth in (1, 2, 3):
                text = json.dumps(value)
                for _ in range(depth - 1):
                    text = json.dumps({"output": text})
                with self.subTest(cmd=cmd[:12], depth=depth):
                    masked = mask(text)
                    self.assertNotIn(PASSWORD, masked)
                    read = json.loads(masked)
                    for _ in range(depth - 1):
                        read = json.loads(read["output"])
                    self.assertEqual((read["cwd"], read["timeout"]), ("/srv/certs", 30))
                    self.assertTrue(read["cmd"].startswith(cmd.split(" -file")[0].split(" -f ")[0]), read["cmd"])

    def test_a_shells_script_keeps_its_text_at_every_depth(self):
        script = f"keytool -storepass {PASSWORD} -v"
        items = ["bash", "-c", script]
        for depth in (1, 2, 3):
            text = json.dumps(items)
            for _ in range(depth - 1):
                text = json.dumps(text)
            with self.subTest(depth=depth):
                masked = mask(text)
                self.assertNotIn(PASSWORD, masked)
                read = masked
                for _ in range(depth):
                    read = json.loads(read)
                self.assertEqual(read, ["bash", "-c", "keytool -storepass [masked] -v"])

    def test_a_view_writes_nothing_back(self):
        continued = "printf 'first" + "\\" + "\n" + "second'; keytool -storepass " + PASSWORD + " -v"
        coloured = "keytool \x1b[1m-v\x1b[0m -storepass " + PASSWORD + " -alias 'team cert'"
        for script in (continued, coloured):
            for text in (json.dumps(["bash", "-c", script]), repr(["bash", "-c", script]),
                         json.dumps({"cmd": script, "cwd": "/srv"})):
                with self.subTest(script=script[:10], text=text[:8]):
                    masked = mask(text)
                    self.assertNotIn(PASSWORD, masked)
                    self.assertEqual(masked.replace("[masked]", PASSWORD), text)

    def test_the_fail_closed_rule_in_unescaped_views(self):
        for line in (f'"keytool" -file <(cat cert.pem) -storepass {PASSWORD}',
                     f"keytool -alias $'team\\'s cert' -storepass {PASSWORD}",
                     f"mongosh -f <(cat init.js) -u admin -p {PASSWORD}"):
            for depth in (2, 3):
                text = line
                for _ in range(depth):
                    text = json.dumps(text)
                with self.subTest(line=line[:10], depth=depth):
                    masked = mask(text)
                    self.assertNotIn(PASSWORD, masked)
                    read = masked
                    for _ in range(depth):
                        read = json.loads(read)
                    self.assertTrue(read.endswith("[masked]"), read)
        for line in ("mongosh --eval 'print(\"ssh -p 2222 host\")'", "keytool -list -storepass $STOREPASS"):
            text = json.dumps(json.dumps(line))
            with self.subTest(line=line[:10]):
                self.assertEqual(mask(text), text)

    def test_aws_by_its_path_or_windows_file(self):
        for program in ("/usr/local/bin/aws", "aws.exe", "C:\\Program Files\\Amazon\\AWSCLIV2\\aws.exe", "/opt/aws/bin/aws"):
            items = [program, "configure", "set", "aws_secret_access_key", PASSWORD]
            with self.subTest(program=program):
                self.assertEqual(mask_all(items)[-1], "[masked]")
                for text in (json.dumps(items), repr(items), " ".join(f'"{v}"' if " " in v else v for v in items)):
                    self.assertNotIn(PASSWORD, mask(text))
        for items in (["/usr/local/bin/awsume", "configure", "set", "aws_secret_access_key", "x" * 12],
                      ["/usr/local/bin/aws", "configure", "set", "region", "us-east-1"]):
            with self.subTest(items=items[0]):
                self.assertEqual(mask_all(items)[-1], items[-1])

    def test_a_backspace_beside_base64_punctuation(self):
        for token in ("whsec_Ab3dE6gH9jK2mN5p/Q8sT1vW4yZ7bC0eF3hJ6", "whsec_Ab3dE6gH9jK2mN5p+Q8sT1vW4yZ7bC0eF3hJ6"):
            for at in [i for i, c in enumerate(token) if c in "/+="] + [i + 1 for i, c in enumerate(token) if c in "/+="]:
                split = token[:at] + "\b" + token[at:]
                for text in (json.dumps(split), json.dumps(json.dumps(split))):
                    with self.subTest(token=token[22], at=at, twice=text.startswith('"\\')):
                        self.assertNotIn(token[-8:], mask(text))
        for text in (r"re.compile(r'\btoken\b')", json.dumps(r"grep -P '\b/api\b' routes.txt"),
                     json.dumps("a \\b+ b and \\b= c"), r"x = '/\b' + '\b='"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)


class ReviewFiveTest(unittest.TestCase):
    """#37's fifth review: a reading only adds masks; a backslash before an "n" in a path is no continuation, and no item
    is masked whole as a fallback; and a value in its own double quotes is masked."""

    def test_a_reading_only_adds_masks(self):
        script = f"echo {PASSWORD}; keytool -storepass {PASSWORD}"
        for value, key in (({"password": ["bash", "-c", script]}, "password"), ({"token": ["keytool", "-storepass", PASSWORD]}, "token")):
            with self.subTest(key=key):
                masked = mask_all(value)
                self.assertNotIn(PASSWORD, json.dumps(masked))
        self.assertEqual(mask_all({"password": ["bash", "-c", script]})["password"][2], "[masked]")

    def test_a_windows_path_keeps_its_text(self):
        for path in ("C:\\new\\team", "C:\\tmp\\new", "D:\\notes\\readme"):
            script = f'echo "{path}"; keytool -storepass {PASSWORD} -v'
            items = ["bash", "-c", script]
            for depth in (1, 2, 3):
                text = json.dumps(items)
                for _ in range(depth - 1):
                    text = json.dumps(text)
                with self.subTest(path=path, depth=depth):
                    read = mask(text)
                    for _ in range(depth):
                        read = json.loads(read)
                    self.assertEqual(read, ["bash", "-c", script.replace(PASSWORD, "[masked]")])

    def test_a_continuation_is_still_read_at_every_depth(self):
        line = "keytool -list \\\n  -storepass " + PASSWORD + " -v"
        for depth in (0, 1, 2):
            text = line
            for _ in range(depth):
                text = json.dumps(text)
            with self.subTest(depth=depth):
                self.assertNotIn(PASSWORD, mask(text))

    def test_a_value_in_its_own_double_quotes(self):
        for line in (f'keytool -file <(cat c) -storepass "{PASSWORD}"', f'keytool -file <(cat c) -storepass "{PASSWORD}" -v',
                     f'mongosh -f <(cat init.js) -u admin -p "{PASSWORD}"'):
            for depth in (0, 1, 2):
                text = line
                for _ in range(depth):
                    text = json.dumps(text)
                with self.subTest(line=line[-6:], depth=depth):
                    masked = mask(text)
                    self.assertNotIn(PASSWORD, masked)
                    read = masked
                    for _ in range(depth):
                        read = json.loads(read)
                    self.assertTrue(read.startswith(line.split(PASSWORD)[0]), read)

    def test_argument_lists_cost_time_in_proportion(self):
        def make(n, twice):
            items = ["keytool"] + [f"-J-Dopt{i}=v{i}" for i in range(n)] + ["-storepass", PASSWORD]
            text = json.dumps(items)
            return json.dumps(json.dumps(text)) if twice else text
        for twice in (False, True):
            with self.subTest(twice=twice):
                small, large = took(make(200, twice)), took(make(800, twice))
                self.assertLess(large, 6 * small + 0.5, (small, large))


class ReviewSixTest(unittest.TestCase):
    """#37's sixth review: a Python repr string bounds the mask as a JSON string does, and a character past U+FFFF,
    escaped as a surrogate pair, decodes to one character, and a mask one reading cannot place stops no other."""

    def test_the_mask_stays_in_a_python_string(self):
        for cmd in (f"keytool -file <(cat c) -storepass {PASSWORD}", f"keytool -alias 'team cert' -file <(cat c) -storepass {PASSWORD}",
                    f'"keytool" -file <(cat c) -storepass {PASSWORD}', f"mongosh -f <(cat init.js) -u admin -p {PASSWORD}"):
            value = {"cmd": cmd, "cwd": "/srv", "exit": 1}
            for text in (repr(value), str([value]), repr({"output": repr(value)}), f"result={value!r} done"):
                with self.subTest(cmd=cmd[:12], text=text[:14]):
                    masked = mask(text)
                    self.assertNotIn(PASSWORD, masked)
                    start = masked.index("{")
                    read = ast.literal_eval(masked[start:masked.rindex("}") + 1])
                    if "output" in read:
                        read = ast.literal_eval(read["output"])
                    if isinstance(read, list):
                        read = read[0]
                    self.assertEqual((read["cwd"], read["exit"]), ("/srv", 1))
                    self.assertEqual(read["cmd"], cmd.replace(PASSWORD, "[masked]"))

    def test_a_character_past_ffff_before_the_password(self):
        for char in ("\U0001f600", "\U00020000", "\U0001d400"):
            script = f"keytool -file <(cat /srv/{char}.pem) -storepass {PASSWORD}"
            items = ["bash", "-c", script]
            for text in (json.dumps(items), json.dumps(items, ensure_ascii=False), json.dumps(json.dumps(items)),
                         json.dumps({"cmd": script, "cwd": "/srv"})):
                with self.subTest(char=hex(ord(char)), text=text[:16]):
                    masked = mask(text)
                    self.assertNotIn(PASSWORD, masked)
                    read = json.loads(masked)
                    while isinstance(read, str):
                        read = json.loads(read)
                    self.assertIn(script.replace(PASSWORD, "[masked]"), read if isinstance(read, list) else [read["cmd"]])
            self.assertEqual(mask_all(items)[2], script.replace(PASSWORD, "[masked]"))

    def test_python_strings_in_prose_stay(self):
        for text in ("it's the team's cert, isn't it", "keytool's -list shows 'alias' and 'cert' entries",
                     repr({"cmd": "keytool -list -v", "cwd": "/srv"}), repr(["mongosh", "--eval", "print('ssh -p 2222 host')"])):
            with self.subTest(text=text[:20]):
                self.assertEqual(mask(text), text)


class ReviewSevenTest(unittest.TestCase):
    """#37's seventh review: a list, dict or tuple in text is parsed whole and read as mask_all reads it, so prose before
    a printed dict, a newline inside an earlier item, and a shell -c item in a longer list all read right; the parsers
    are guarded, and where a span does not parse the reading only adds."""

    def test_prose_before_a_printed_dict(self):
        value = {"cmd": "keytool -file <(cat /srv/c.pem) -storepass " + PASSWORD, "cwd": "/srv", "exit": 1}
        for text in ("Couldn't run: " + repr(value), "it's broken; " + repr(value), f"log: {value!r}",
                     repr({"err": "can't open", "proc": value})):
            with self.subTest(text=text[:16]):
                masked = mask(text)
                self.assertNotIn(PASSWORD, masked)
                read = ast.literal_eval(masked[masked.index("{"):masked.rindex("}") + 1])
                inner = read.get("proc", read)
                self.assertEqual((inner["cwd"], inner["exit"]), ("/srv", 1))
                self.assertEqual(inner["cmd"], value["cmd"].replace(PASSWORD, "[masked]"))

    def test_a_newline_in_an_earlier_item(self):
        for items in (["mongosh", "--eval", "db.stats();\nprint(1)", "-p", PASSWORD],
                      ["keytool", "-J-Dx=a\nb", "-storepass", PASSWORD],
                      ["az", "login", "--eval", "a;\nb", "-p", PASSWORD]):
            for text in (str(subprocess.CalledProcessError(1, items)), json.dumps(items), repr(items)):
                with self.subTest(items=items[0], text=text[:12]):
                    self.assertNotIn(PASSWORD, mask(text))

    def test_a_shell_c_item_after_other_options(self):
        for items in (["bash", "--noprofile", "-c", "keytool -file <(cat /srv/c.pem) -storepass " + PASSWORD],
                      ["/bin/sh", "-eu", "-c", "mongosh -u admin -p " + PASSWORD],
                      ["zsh", "-lc", "keytool -storepass " + PASSWORD + " -v"]):
            for text in (str(subprocess.CalledProcessError(1, items)), json.dumps(items),
                         json.dumps(json.dumps(items))):
                with self.subTest(items=items[0], text=text[:12]):
                    masked = mask(text)
                    self.assertNotIn(PASSWORD, masked)
                    self.assertIn("keytool" if "keytool" in items[-1] else "mongosh", masked)

    def test_the_parse_matches_mask_all(self):
        for items in (["keytool", "-storepass", PASSWORD, "-v"], ["mongosh", "--eval", "x;\ny", "-p", PASSWORD],
                      ["bash", "-c", "keytool -storepass " + PASSWORD]):
            for depth in (0, 1, 2):
                text = json.dumps(items)
                for _ in range(depth):
                    text = json.dumps(text)
                with self.subTest(items=items[0], depth=depth):
                    read = mask(text)
                    for _ in range(depth + 1):
                        read = json.loads(read)
                    self.assertEqual(read, mask_all(items))

    def test_the_parsers_are_guarded(self):
        deep = "[" * 5000 + "]" * 5000  # deeper than json/ast parse; the depth guard skips it before any parser
        digits = '["keytool", "-storepass", ' + "9" * 80000 + ']'  # a number: str-to-int is quadratic on 3.9; skipped
        for text in (deep, digits, "{" + "0" * 40000 + ": 1}", "[" + "9" * 40000 + "]"):
            with self.subTest(text=text[:12]):
                start = time.process_time()
                out = mask(text)
                self.assertLess(time.process_time() - start, 1.0, len(text))
                self.assertNotIn(PASSWORD, out)
        # a command hidden behind a long digit run, and a large list, do not crash and do not leak (no tight bound,
        # as a text this large costs about the same on #29: it is the base mask cost, not a guard)
        # nor do digits in a string, which no parser converts: the list is read, its long value at the base mask cost
        for text in ('{"cmd": "keytool -storepass ' + PASSWORD + '", ' + "0" * 70000 + "}",
                     "[" + ", ".join('"x"' for _ in range(100000)) + "]",
                     '["keytool", "-storepass", "' + "9" * 80000 + '"]'):
            with self.subTest(text=text[:12]):
                self.assertNotIn(PASSWORD, mask(text))

    def test_a_span_that_does_not_parse_only_adds(self):
        # A span that does not parse falls back to the line readings, which only add: no crash, and the text around a
        # mask is kept. A bare command line inside the brackets is still masked by the fail-closed reading.
        for text in ("run [keytool -storepass " + PASSWORD + "] now", "{ keytool -storepass " + PASSWORD + " }"):
            with self.subTest(text=text[:16]):
                masked = mask(text)
                self.assertNotIn(PASSWORD, masked)
                self.assertTrue(masked.startswith(text.split("keytool")[0]), masked)
        for text in ("a plain [list, of, words] here", "{key: value, other: thing}", "see ['one', 'two', 'three']",
                     "the list [1, 2, 3] and {a: b}"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)


class ArgumentListCostTest(unittest.TestCase):
    """A list's reading costs a small constant per item: four times the list under about six times the time, both as a
    value (mask_all) and as JSON in text, so a long list of plain options does not cost the square of its length."""

    @staticmethod
    def _items(n):
        items = ["keytool"]
        for i in range(n):
            items += ["-v", "opt%d" % i]
        return items + ["-storepass", PASSWORD]

    def test_mask_all_cost(self):
        small, large = self._items(500), self._items(2000)
        self.assertNotIn(PASSWORD, json.dumps(mask_all(small)))
        self.assertNotIn(PASSWORD, json.dumps(mask_all(large)))
        little, lots = took_call(lambda: mask_all(small)), took_call(lambda: mask_all(large))
        self.assertLess(lots, 6 * little + 0.2, (little, lots))  # four times the list, about four times the CPU
        self.assertLess(lots, 3.0, lots)  # a small constant per item: CPU time (process_time), not inflated by load

    def test_json_in_text_cost(self):
        small, large = json.dumps(self._items(500)), json.dumps(self._items(2000))
        self.assertNotIn(PASSWORD, mask(small))
        self.assertNotIn(PASSWORD, mask(large))
        little, lots = took(small), took(large)
        self.assertLess(lots, 6 * little + 0.2, (little, lots))
        self.assertLess(lots, 5.0, lots)


class ReviewEightTest(unittest.TestCase):
    """The eighth review's findings: a printed dict or command after a word's apostrophe, JSON-escaped, lost the text
    after the password, and a list of arguments past the parser's old 200 KB cap kept its password."""

    WRAPS = (lambda t: t, json.dumps, lambda t: json.dumps(json.dumps(t)))
    COMMAND = "keytool -file <(cat /srv/c.pem) -storepass " + PASSWORD

    def masks_only_the_password(self, *texts):
        for text in texts:
            for wrap in self.WRAPS:
                with self.subTest(text=wrap(text)[:90]):
                    self.assertEqual(mask(wrap(text)), wrap(text.replace(PASSWORD, "[masked]")))

    def test_a_printed_dict_keeps_its_fields_escaped_once_and_twice(self):
        self.masks_only_the_password("Couldn't run: " + repr({"cmd": self.COMMAND, "cwd": "/srv", "exit": 1}))

    def test_a_printed_command_after_an_apostrophe_keeps_the_text_after_it(self):
        self.masks_only_the_password("Couldn't run: %r (exit status 1)" % self.COMMAND,
                                     "Couldn't run: %r, see the log" % self.COMMAND,
                                     "It's the team's key: %r" % self.COMMAND)

    def test_a_printed_bytes_value_bounds_its_command(self):
        self.masks_only_the_password("print: %r done" % (self.COMMAND + "\n").encode())

    def test_a_list_past_200_kb_is_read_whole(self):
        items = ["keytool", "-list", "-storepass", PASSWORD] + ["-J-Dprop%d=" % i + "x" * 100 for i in range(2000)]
        masked = ["[masked]" if item == PASSWORD else item for item in items]
        for text, want in ((json.dumps(items), json.dumps(masked)),
                           (str(subprocess.CalledProcessError(1, items)), str(subprocess.CalledProcessError(1, masked)))):
            with self.subTest(text=text[:60], size=len(text)):
                self.assertGreater(len(text), 200000)
                self.assertEqual(mask(text), want)


class ReviewNineTest(unittest.TestCase):
    """The ninth review's findings: a plural possessive's apostrophe read as a Python string's quote, a list inside
    brackets that do not parse, byte strings, a long digit string in a container, and a shell's -c context sought again
    for every -c."""

    WRAPS, COMMAND = ReviewEightTest.WRAPS, ReviewEightTest.COMMAND
    masks_only_the_password = ReviewEightTest.masks_only_the_password

    def test_a_plural_possessive_opens_no_string(self):
        self.masks_only_the_password("Users' command: %r (exit status 1)" % self.COMMAND,
                                     "The teams' runner printed %r, see the log" % self.COMMAND)

    def test_a_list_inside_brackets_that_do_not_parse(self):
        self.masks_only_the_password("Command (argv=%r) failed" % ["keytool", "-storepass", PASSWORD],
                                     "run(%r, check=True) failed" % ["mongosh", "-u", "admin", "-p", PASSWORD])

    def test_byte_string_items(self):
        self.masks_only_the_password(
            str(subprocess.CalledProcessError(1, [b"keytool", b"-storepass", PASSWORD.encode()])),
            str(subprocess.CalledProcessError(1, [b"bash", b"-c", self.COMMAND.encode()])))

    def test_a_digit_string_in_a_container(self):
        self.masks_only_the_password(json.dumps({"argv": ["keytool", "-storepass", PASSWORD], "stdout": "9" * 1000}))

    def test_stays_prose_with_possessives_and_quotes(self):
        for text in ("The users' 'prod' profile and the teams' (shared) keys stay readable.",
                     "Command (argv=['ls', '-la']) failed", str(subprocess.CalledProcessError(1, [b"ls", b"-la"]))):
            for wrap in self.WRAPS:
                with self.subTest(text=wrap(text)[:80]):
                    self.assertEqual(mask(wrap(text)), wrap(text))

    def test_many_shell_options_cost_time_in_proportion(self):
        def items(n):
            return ["git"] + [v for i in range(n) for v in ("-c", "url.https://example.com/repo%d.insteadOf=team%d:" % (i, i))] + [
                "-c", "credential.helper=!aws codecommit credential-helper $@", "ls-remote", "https://example.com/repo"]
        small, large = items(500), items(2000)
        self.assertEqual(mask_all(small), small)
        little, lots = took_call(lambda: mask_all(small)), took_call(lambda: mask_all(large))
        self.assertLess(lots, 6 * little + 0.2, (little, lots))  # four times the list, about four times the CPU
        # Within a few times main's 0.2 s for 2,000 overrides: the list's reading adds little to masking each of its items
        # alone, the cost every list pays, measured in the same run so the machine's load weighs on both alike (alone,
        # 0.70 s against 0.2 s on main; 3.6 s before the fix).
        floor = took_call(lambda: [mask(item) for item in large])
        self.assertLess(lots, 1.5 * floor + 0.2, (floor, lots))
        self.assertLess(lots, 2.0, lots)


def _scanned_span(text, start):
    """The bracket scan as it ran before round 10, kept as the reference: where the balanced span opened at
    text[start] ends, or None."""
    stack, i, n = [], start, min(len(text), start + masking._CONTAINER_MAX)
    while i < n:
        c = text[i]
        if c in "\"'":
            i += 1
            while i < n and text[i] != c:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c in "[{(":
            stack.append({"[": "]", "{": "}", "(": ")"}[c])
            if len(stack) > masking._CONTAINER_DEPTH:
                return None
            i += 1
            continue
        if c in "]})":
            if not stack or stack.pop() != c:
                return None
            i += 1
            if not stack:
                return i
            continue
        i += 1
    return None


def _scanned_containers(text, i=0, n=None):
    n = len(text) if n is None else n
    while i < n:
        if text[i] in "[{(":
            end = _scanned_span(text, i)
            if end is not None:
                yield i, end
                i = end
                continue
        i += 1


class ReviewTenTest(unittest.TestCase):
    """The tenth review's findings: aws configure set's options between "set" and the setting pushed "aws" out of the
    six items read back (options before "configure" are the follow-ups' ON-MAIN item, as in a line of text), and finding the bracket spans scanned again from every bracket, quadratic where each scan
    starts inside an earlier one's quotes."""

    WRAPS = ReviewEightTest.WRAPS
    masks_only_the_password = ReviewEightTest.masks_only_the_password

    AWS = (["aws", "configure", "set", "--profile", "team", "--region", "us-east-1", "aws_secret_access_key", PASSWORD],
           ["/usr/local/bin/aws", "configure", "set", "--profile", "ci", "--debug", "aws_session_token", PASSWORD],
           ["aws.exe", "configure", "set", "--profile", "team", "--output", "json", "--region", "eu-west-1",
            "--no-cli-pager", "default.aws_session_token", PASSWORD])

    def test_aws_options_between_set_and_the_setting(self):
        for items in self.AWS:
            masked = ["[masked]" if item == PASSWORD else item for item in items]
            with self.subTest(items=items):
                self.assertEqual(mask_all(items), masked)
            self.masks_only_the_password(str(subprocess.CalledProcessError(1, items)), json.dumps({"argv": items}),
                                         "run: %r" % (items,))

    def test_stays_aws_lists_with_no_secret_setting(self):
        for items in (["aws", "configure", "set", "--profile", "team", "--region", "us-east-1", "region", "us-west-2"],
                      ["aws", "configure", "get", "--profile", "team", "aws_secret_access_key"],
                      ["aws", "s3", "cp", "--profile", "team", "notes.txt", "s3://bucket/token_notes"]):
            with self.subTest(items=items):
                self.assertEqual(mask_all(items), items)
                text = str(subprocess.CalledProcessError(1, items))
                self.assertEqual(mask(text), text)

    @staticmethod
    def took_uncached(text):
        """took(text), each run reading the text afresh: the container reading is cached per text."""
        def run():
            masking._container_edits.cache_clear()
            mask(text)
        return took_call(run)

    def test_finding_the_spans_costs_time_in_proportion(self):
        shapes = {"escaped lists in a JSON string": lambda n: json.dumps('note ["one"] ' * n + "keytool -list"),
                  "the same escaped twice": lambda n: json.dumps(json.dumps('note ["one"] ' * n + "keytool -list")),
                  "an unclosed quote after each bracket": lambda n: 'x ["a ' * n + "keytool -list",
                  "brackets inside an apostrophe's quote": lambda n: "it's [a] " * n + "keytool -list",
                  "nested openers that never close": lambda n: '([{"' * n + "keytool -list"}
        for name, shape in shapes.items():
            small, large = shape(500), shape(2000)
            with self.subTest(shape=name):
                self.assertEqual(mask(small), small)
                little, lots = self.took_uncached(small), self.took_uncached(large)
                self.assertLess(lots, 6 * little + 0.1, (little, lots))  # four times the text, about four times the CPU
                self.assertLess(lots, 1.0, lots)

    def test_the_spans_found_are_the_scans(self):
        import random
        r = random.Random(10)
        alphabet = '[]{}()""\'\\ ab'
        for case in range(3000):
            text = "".join(r.choice(alphabet) for _ in range(r.randint(0, 60)))
            if case % 3 == 0:
                text = "[" * r.randint(95, 105) + text + "]" * r.randint(95, 105)
            with self.subTest(text=text):
                self.assertEqual(list(masking._containers(text)), list(_scanned_containers(text)))
                for i in range(0, len(text), 7):
                    self.assertEqual(list(masking._containers(text, i, len(text) - i // 2)),
                                     list(_scanned_containers(text, i, len(text) - i // 2)))


class ReviewElevenTest(unittest.TestCase):
    """The eleventh review's findings: past the 64 items read before a value, its command was read back from the last
    item holding a command's name anywhere in it, so an option's value that holds one ("-alias", "mongo-ca") took the
    place of the list's own program, and aws configure set's setting was read only within 64 items of "set"."""

    WRAPS = ReviewEightTest.WRAPS
    masks_only_the_password = ReviewEightTest.masks_only_the_password

    LISTS = (["aws", "configure", "set"] + ["--profile", "ci"] * 31 + ["aws_secret_access_key", PASSWORD],
             ["aws", "configure", "set"] + ["--profile", "ci"] * 200 + ["aws_secret_access_key", PASSWORD],
             ["/usr/local/bin/aws", "configure", "set"] + ["--debug"] * 70 + ["default.aws_session_token", PASSWORD],
             ["keytool", "-list", "-keystore", "/srv/team.jks", "-alias", "mongo-ca"] + ["-v"] * 63
             + ["-storepass", PASSWORD],
             ["keytool", "-list", "-keystore", "/srv/team.jks", "-alias", "keytool-ca"] + ["-v"] * 63
             + ["-storepass", PASSWORD],
             ["keytool", "-list", "-alias", "login"] + ["-v"] * 200 + ["-storepass", PASSWORD],
             ["keytool", "-list", "-alias", "curl"] + ["-v"] * 70 + ["-storepass", PASSWORD],
             ["env", "KEYTOOL_HOME=/opt/java", "LANG=C", "TZ=UTC", "keytool", "-list", "-alias", "keytool-ca"]
             + ["-v"] * 63 + ["-storepass", PASSWORD],
             ["mongosh", "-u", "admin", "--authenticationDatabase", "keytool-db"] + ["--quiet"] * 64 + ["-p", PASSWORD],
             ["aws", "configure", "set", "--profile", "mongo-ci"] + ["--debug"] * 64
             + ["aws_secret_access_key", PASSWORD])

    def test_the_lists_own_command_reads_its_password_past_64_items(self):
        for items in self.LISTS:
            masked = ["[masked]" if item == PASSWORD else item for item in items]
            with self.subTest(items=items[:8], size=len(items)):
                self.assertEqual(mask_all(items), masked)
            self.masks_only_the_password(str(subprocess.CalledProcessError(1, items)), json.dumps({"argv": items}),
                                         "run: %r" % (items,))

    def test_stays_a_long_list_with_no_secret(self):
        for items in (["aws", "configure", "set"] + ["--profile", "ci"] * 40 + ["region", "us-west-2"],
                      ["aws", "configure", "get"] + ["--profile", "ci"] * 40 + ["aws_secret_access_key", "region"],
                      ["keytool", "-list", "-alias", "mongo-ca"] + ["-v"] * 70 + ["-keystore", "/srv/team.jks"],
                      ["keytool", "-list", "-alias", "mongo-ca"] + ["-v"] * 63 + ["-p", "release-2026"],
                      ["echo", "mongo-ca"] + ["--quiet"] * 70 + ["-p", "release-2026"]):
            with self.subTest(items=items[:6], size=len(items)):
                self.assertEqual(mask_all(items), items)
                text = str(subprocess.CalledProcessError(1, items))
                self.assertEqual(mask(text), text)

    def test_many_command_names_cost_time_in_proportion(self):
        names = ["curl", "mysql", "sshpass", "login", "redis-cli", "mongosh", "twine", "keytool", "jarsigner", "aws"]

        # Each command is named twice before the 64 items ahead of the first value, so each value's line is as long as
        # it gets from the start, and every command is named again after it.
        def items(n):
            found = ["keytool"] + [x for name in names * 2 for x in ("-alias", name)] + ["-v"] * 64
            for i in range(n):
                found += ["-alias", names[i % len(names)] + "-%d" % i, names[i % len(names)], "-storepass", PASSWORD]
            return found
        small, large = items(25), items(100)
        self.assertNotIn(PASSWORD, json.dumps(mask_all(small)))
        little, lots = took_call(lambda: mask_all(small)), took_call(lambda: mask_all(large))
        self.assertLess(lots, 6 * little + 0.2, (little, lots))  # four times the list, about four times the CPU
        self.assertLess(lots, 20.0, lots)  # each value after a secret's flag reads its line twice: a constant per value


class ReviewTwelveTest(unittest.TestCase):
    """The twelfth review's findings: in aws configure set, a value of one of aws's options that take one ("--profile
    token") was read as the secret setting's name, masking the setting after it and leaving its value readable, in a
    list as in a line."""

    WRAPS = ReviewEightTest.WRAPS
    masks_only_the_password = ReviewEightTest.masks_only_the_password

    LISTS = (["aws", "configure", "set", "--profile", "token", "aws_secret_access_key", PASSWORD],
             ["aws", "configure", "set", "--region", "us-east-1", "--profile", "secret", "aws_session_token", PASSWORD],
             ["aws.exe", "configure", "set", "--output", "token", "--query", "password", "default.aws_session_token",
              PASSWORD],
             ["aws", "configure", "set", "aws_secret_access_key", PASSWORD, "--profile", "token"])

    def test_an_options_value_is_not_the_setting(self):
        for items in self.LISTS:
            masked = ["[masked]" if item == PASSWORD else item for item in items]
            with self.subTest(items=items):
                self.assertEqual(mask_all(items), masked)
            self.masks_only_the_password(str(subprocess.CalledProcessError(1, items)), json.dumps({"argv": items}),
                                         "run: %r" % (items,), " ".join(items))

    def test_the_fail_closed_rule_keeps_the_setting_after_an_options_value(self):
        self.masks_only_the_password("aws configure set --cli-binary-format $'raw' --profile token aws_secret_access_key "
                                     + PASSWORD)

    def test_stays_a_setting_after_an_options_value(self):
        for items in (["aws", "configure", "set", "--profile", "token", "max_attempts", "10"],
                      ["aws", "configure", "set", "--profile", "token", "region", "us-west-2"]):
            with self.subTest(items=items):
                self.assertEqual(mask_all(items), items)
                for text in (str(subprocess.CalledProcessError(1, items)), json.dumps(items), " ".join(items)):
                    self.assertEqual(mask(text), text)



class ReviewThirteenTest(unittest.TestCase):
    """The thirteenth review's finding: strings that each hold a deeply nested list, encoded once more at each step,
    made the parse-whole reading re-enter mask_all until Python's recursion ran out, so masking raised and the row was
    dropped. The bracket depth is now counted across those steps, and past the guard's depth the span is read as text
    is, fail-closed."""

    @staticmethod
    def nested(innermost, steps, depth):
        s = json.dumps(innermost)
        for _ in range(steps):
            s = "[" * depth + json.dumps(s) + "]" * depth
        return s

    def test_lists_nested_through_encoded_strings_mask_without_raising(self):
        for steps, depth in ((8, 60), (12, 40), (3, 63), (2, 99)):
            for innermost in (["keytool"], ["keytool", "-storepass", PASSWORD]):
                text = self.nested(innermost, steps, depth)
                with self.subTest(steps=steps, depth=depth, innermost=innermost):
                    masked = mask(text)
                    if PASSWORD not in json.dumps(innermost):
                        self.assertEqual(masked, text)
                    self.assertEqual(mask_all({"out": text})["out"], masked)

    def test_many_encoding_steps_cost_time_in_proportion(self):
        # each step's json.dumps doubles the backslashes before, so the text doubles with each step
        for innermost in (["keytool"], ["keytool", "-storepass", PASSWORD]):
            small, large = self.nested(innermost, 13, 1), self.nested(innermost, 14, 1)
            with self.subTest(innermost=innermost):  # twice the text, at most a little over twice the time
                self.assertLess(took(large), 3.5 * took(small) + 0.1)

    def test_such_a_row_is_stored(self):
        text = self.nested(["keytool"], 8, 60)
        with tempfile.TemporaryDirectory() as tmp:
            db = store.connect(os.path.join(tmp, "kb.sqlite"))
            try:
                store.store_item(db, "codex", "c-r13", "2026-11-01T10:00:00Z", "nested", text, {})
                self.assertEqual(db.execute("SELECT count(*) FROM docs").fetchone()[0], 1)
            finally:
                db.close()

    def test_a_password_two_steps_down_still_masks(self):
        for steps, depth in ((2, 1), (2, 3), (3, 2)):
            text = self.nested(["keytool", "-list", "-storepass", PASSWORD], steps, depth)
            with self.subTest(steps=steps, depth=depth):
                self.assertNotIn(PASSWORD, mask(text))


class RevisionTest(unittest.TestCase):
    def test_stored_rows_are_masked_again(self):
        self.assertEqual(masking.REVISION, 27)


if __name__ == "__main__":
    unittest.main()
