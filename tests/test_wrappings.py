"""The wrappings gate 9 left (mask-wrappings): a supplementary invisible character JSON writes as a surrogate pair, and a
named command's password where kb cannot read the command plainly: shell grammar it does not follow, a line carried on
by a continuation, a program name or flag inside a colour code, and a command given as a list of arguments. Made-up
secrets only."""
import json
import shlex
import subprocess
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


class RevisionTest(unittest.TestCase):
    def test_stored_rows_are_masked_again(self):
        self.assertEqual(masking.REVISION, 11)


if __name__ == "__main__":
    unittest.main()
