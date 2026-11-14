"""What a typing tool types is hidden unless its field is on a short list of harmless ones.

kb.mask used to hide a typed value only where the field's label asks for a secret (_asks_for_a_secret), so a value
typed under a label the list did not hold ("Auth code", "Backup code", "Reset code", "Pass phrase", "Security answer",
a made-up one) showed, in JSON, escaped JSON, JSON in a JSON string, Python's repr, a log line and a fence: 480 of 5,760
forms of one probe. The rule is turned around: a typed value is hidden, at ingest (typed_secrets) and in text, unless
every label of its field is on the short list (_harmless_labels), and a field with no label hides it too.
"""
import json
import random
import unittest

from kb import mask as masking
from kb.mask import mask, typed_secrets
from kb.sync import egress
from tests.test_gate9 import READERS, stored, tool_call

# Made up, none a real secret. Each is longer than a word of the text around it, so a value shows where it is found.
VALUES = ("Zq7!walrusLedger93", "483920", "Fluffy", "correct-pelican-battery-staple", "tR9#vK2m", "7714",
          "mauve ostrich lamp", "Qx5wLm8Rt2Yp", "plumber77k", "A1b2C3d4")
KNOWN = ("Password", "Passcode", "PIN", "Secret key", "API token", "Passphrase", "Login password", "Your password",
         "Confirm password", "Recovery phrase", "Master password", "One-time code", "Verification code", "OTP")
# The labels the first probe found shown on 7dc7c1b, and others a page might give a field that takes a secret.
MISSED = ("Auth code", "Authentication code", "Access code", "Authorization code", "Backup code", "Reset code",
          "Pass phrase", "Security answer", "Invite code", "Promo code", "Mother maiden name", "Enter your backup code",
          "Backup code (8 digits)", "Recovery key", "Unlock code", "Gate code", "Licence key", "Activation code",
          "Answer", "Safe word", "Magic word", "Wifi key", "Door code", "Vault phrase", "Keyword", "Voucher",
          "Serial number", "Pairing code", "Login", "Credentials")
# Made up: a list of the labels that ask for a secret cannot hold these, so the rule must not need to.
MADE_UP = ("Zorblax", "Frumious input", "Glimmer", "Quux code", "Snark field", "Wibble", "Mimsy answer", "Slithy toves",
           "Borogove", "Jabberwock number", "Vorpal", "Tove entry", "Callooh", "Beamish boy", "Tulgey wood",
           "Uffish thought", "x", "", "   ")
# Ordinary fields that are on no list of secrets and are still not on the short list of harmless ones.
ORDINARY = ("Email", "Full name", "Message", "Street address", "Username", "Company", "Comment", "Token name",
            "Password hint", "Secret question", "API key description", "Token expiration")
HARMLESS = ("Search", "search", "Search box", "Search field", "Query", "Filter", "URL", "Url", "Address bar",
            "Search or enter address", "Search Google or type a URL", "Address and search bar", "Filter results",
            "searchBox", "search-input", "Search 2", "q", "omnibox", "Your search")
# A harmless label with a word of another kind, whatever it is: the field is then about something else.
LOOKALIKES = ("Search backup code", "Search passwords", "Filter by token", "Query key", "URL token", "Address PIN",
              "Search answer", "Search phrase", "Search otp", "Search secret", "Search code", "Search credentials",
              "Filter by password", "Search or enter password", "Search Google or type your passphrase",
              "Address bar secret", "Search auth", "search-token", "searchPassword", "Search for answer",
              "Search пароль", "Sеarch", "Searchа", "Search é", "Query 7 code")
LABELS = KNOWN + MISSED + MADE_UP + ORDINARY + LOOKALIKES


def shows(out, value):
    return value in out or (len(value) > 12 and (value[:8] in out or value[-8:] in out))


def calls(label, field, value):
    """Each way a typing tool's input names a field and what it types: (tool name, input)."""
    slug = "#" + label.lower().replace(" ", "-")
    return [("mcp__playwright__browser_type", {"element": label, "ref": "e12", field: value}),
            ("mcp__playwright__browser_type", {field: value, "ref": "e12", "element": label + " field"}),
            ("browser_type", {"ref": "e7", "placeholder": label, field: value, "submit": True}),
            ("mcp__playwright__browser_fill_form",
             {"fields": [{"name": "Email", "type": "textbox", "ref": "e5", "value": "me@example.com"},
                         {"name": label, "type": "textbox", "ref": "e3", field: value}]}),
            ("mcp__puppeteer__puppeteer_fill", {"selector": slug, field: value}),
            ("mcp__x__fill_field", {"aria_label": label, field: value}),
            ("mcp__other__type_text", {"label": label, field: value})]


def writings(name, args):
    """One call as text writes it, in each form a transcript holds one."""
    once = json.dumps({"type": "function_call", "name": name, "arguments": json.dumps(args)})
    block = {"type": "tool_use", "id": "toolu_01", "name": name, "input": args}
    return {"TOOL line": f"TOOL {name}: {json.dumps(args)}",
            "json": json.dumps(args), "json pretty": json.dumps(args, indent=2),
            "json once": json.dumps(json.dumps(args))[1:-1], "json twice": json.dumps(json.dumps(json.dumps(args)))[1:-1],
            "json in a json string": json.dumps({"stored": json.dumps(args)}),
            "arguments string": once, "escaped twice": json.dumps(once),
            "python repr": repr(args), "repr of a block": str(block), "content block": json.dumps(block),
            "log line": "tool call: " + json.dumps(args) + " -> ok",
            "markdown fence": "```json\n" + json.dumps(args) + "\n```"}


class FailClosedAtIngestTest(unittest.TestCase):
    def test_a_value_typed_under_any_label_but_a_harmless_one_is_hidden(self):
        for k, label in enumerate(LABELS):
            for field in ("text", "value"):
                value = VALUES[k % len(VALUES)]
                for name, args in calls(label, field, value):
                    with self.subTest(label=label, name=name, field=field):
                        out = typed_secrets(name, args)
                        self.assertFalse(shows(json.dumps(out), value))
                        self.assertEqual(typed_secrets(name, out), out, "hiding again changes nothing")

    def test_a_value_typed_into_a_harmless_field_stays(self):
        for k, label in enumerate(HARMLESS):
            for name, args in calls(label, "text", "pelican plan"):
                if name == "mcp__playwright__browser_fill_form":
                    continue  # its other field, an email, is not harmless
                with self.subTest(label=label, name=name):
                    self.assertEqual(typed_secrets(name, args), args)

    def test_each_of_the_fields_a_typing_tool_types_into_is_read(self):
        for key in ("text", "value", "keys", "input", "content", "string"):
            with self.subTest(key=key):
                args = {"element": "Backup code", "ref": "e2", key: "Zq7!walrusLedger93"}
                self.assertEqual(typed_secrets("mcp__playwright__browser_type", args)[key], "[masked]")

    def test_a_field_with_no_label_hides_what_is_typed(self):
        for value in VALUES:
            for name, args in (("mcp__chrome-devtools__fill", {"uid": "1_7", "value": value}),
                               ("mcp__playwright__browser_type", {"ref": "e9", "text": value}),
                               ("mcp__chrome-devtools__type_text", {"text": value, "submitKey": "Enter"}),
                               ("mcp__chrome-devtools__fill_form", {"elements": [{"uid": "1_3", "value": value}]}),
                               ("computer", {"action": "type", "text": value}),
                               ("computer_20250124", {"action": "type", "text": value})):
                with self.subTest(name=name, value=value):
                    self.assertFalse(shows(json.dumps(typed_secrets(name, args)), value))

    def test_a_number_typed_is_hidden_too(self):
        self.assertEqual(typed_secrets("mcp__playwright__browser_type", {"element": "Backup code", "text": 483920})["text"],
                         "[masked]")
        self.assertEqual(typed_secrets("mcp__playwright__browser_type", {"element": "Search", "text": 2048})["text"], 2048)

    def test_a_field_nested_deeper_than_a_list_is_read(self):
        value = "Zq7!walrusLedger93"
        for args in ({"form": {"fields": [{"name": "Backup code", "value": value}]}},
                     {"fields": [[{"name": "Backup code", "value": value}]]},
                     {"element": "Search", "ref": "e1", "text": "pelican", "options": {"value": value}}):
            with self.subTest(args=args):
                self.assertNotIn(value, json.dumps(typed_secrets("mcp__playwright__browser_fill_form", args)))

    def test_a_label_names_its_field_only_by_its_own_words(self):
        # label keys other than the first still count: a harmless one never takes back another label that is not
        for args in ({"element": "Search", "placeholder": "Backup code", "text": "Zq7!walrusLedger93"},
                     {"element": "Search", "name": "code", "text": "Zq7!walrusLedger93"},
                     {"selector": "#search", "type": "password", "text": "Zq7!walrusLedger93"},
                     {"element": "Search", "target": "Reset code", "text": "Zq7!walrusLedger93"}):
            with self.subTest(args=args):
                self.assertEqual(typed_secrets("mcp__playwright__browser_type", args)["text"], "[masked]")

    def test_each_reader_hides_a_value_under_a_made_up_label(self):
        for reader in READERS:
            for label in ("Backup code", "Zorblax", "Security answer"):
                for name, args in calls(label, "text", "Zq7!walrusLedger93")[:2]:
                    with self.subTest(reader=reader, label=label, name=name):
                        text = stored(reader, tool_call(reader, name, args))
                        self.assertNotIn("Zq7!walrusLedger93", text)
                        self.assertIn(label.strip() or "ref", text)
        for reader in READERS:
            text = stored(reader, tool_call(reader, "mcp__playwright__browser_type",
                                            {"element": "Search", "ref": "e1", "text": "pelican plan"}))
            self.assertIn("pelican plan", text)


class FailClosedInTextTest(unittest.TestCase):
    def test_a_value_typed_under_any_label_but_a_harmless_one_is_hidden_in_every_form(self):
        r = random.Random("typed-fields:forms")
        for k, label in enumerate(LABELS):
            value = VALUES[k % len(VALUES)]
            field = r.choice(("text", "value", "input", "content", "string", "keys"))
            for name, args in calls(label, field, value):
                for form, text in writings(name, args).items():
                    if (k + len(name) + len(form)) % 3:
                        continue  # each tool and each form meets a third of the labels
                    with self.subTest(label=label, name=name, field=field, form=form):
                        for out in (mask(text), egress.hide(text)):
                            self.assertFalse(shows(out, value))
                        self.assertEqual(mask(mask(text)), mask(text), "masking again changes nothing")

    def test_every_value_and_every_field_name_under_a_label_the_list_misses(self):
        for label in ("Auth code", "Backup code", "Reset code", "Pass phrase", "Security answer", "Zorblax"):
            for value in VALUES:
                for field in ("text", "value", "input", "content", "string", "keys"):
                    name, args = calls(label, field, value)[(len(value) + len(field)) % 7]
                    for form, text in writings(name, args).items():
                        with self.subTest(label=label, value=value, field=field, form=form):
                            self.assertFalse(shows(mask(text), value))

    def test_a_value_typed_into_a_harmless_field_stays_in_every_form(self):
        for label in HARMLESS:
            for name, args in calls(label, "text", "pelican plan")[:3] + calls(label, "text", "pelican plan")[4:]:
                for form, text in writings(name, args).items():
                    with self.subTest(label=label, name=name, form=form):
                        self.assertEqual(mask(text), text)

    def test_a_label_that_looks_harmless_hides_what_is_typed(self):
        for label in LOOKALIKES:
            for name, args in calls(label, "value", "Qx5wLm8Rt2Yp"):
                for form in ("json", "python repr", "json once", "log line"):
                    with self.subTest(label=label, name=name, form=form):
                        self.assertFalse(shows(mask(writings(name, args)[form]), "Qx5wLm8Rt2Yp"))

    def test_a_label_written_with_an_escape_or_a_hidden_character_is_not_on_the_list(self):
        for label in ("Se\\u0061rch backup", "\\u0053earch", "Sea​rch", "Sea\\u200brch", "Search\\u0020code",
                      "Sear⁠ch", "Ѕearch"):
            for text in (json.dumps({"ref": "e1", "label": label, "text": "Zq7!walrusLedger93"}),
                         '{"ref": "e1", "label": "%s", "text": "Zq7!walrusLedger93"}' % label):
                with self.subTest(label=label, text=text):
                    self.assertNotIn("Zq7!walrusLedger93", mask(text))

    def test_an_object_with_no_label_hides_what_is_typed_where_a_typing_tool_is_named_or_a_key_says_so(self):
        for value in VALUES:
            for text in (f'TOOL mcp__chrome-devtools__fill: {{"uid": "1_7", "value": "{value}"}}',
                         f'{{"uid": "1_7", "value": "{value}"}}',
                         f'{{"ref": "e9", "text": "{value}"}}',
                         f'TOOL mcp__playwright__browser_type: {{"text": "{value}", "submit": true}}',
                         f'TOOL mcp__chrome-devtools__fill_form: {{"elements": [{{"uid": "1_3", "value": "{value}"}}]}}',
                         f'TOOL computer: {{"action": "type", "text": "{value}"}}',
                         f"{{'action': 'type', 'text': '{value}'}}",
                         f'{{"name": "browser_type", "arguments": {{"text": "{value}"}}}}',
                         json.dumps(json.dumps({"ref": "e9", "value": value}))):
                with self.subTest(text=text):
                    self.assertFalse(shows(mask(text), value))

    def test_the_text_and_the_ingest_read_a_call_the_same_way(self):
        # A row a reader stored, and a row it stored before it hid the value and the mask reads again, are the same.
        for k, label in enumerate(LABELS + HARMLESS):
            value = VALUES[k % len(VALUES)]
            for name, args in calls(label, "text", value):
                with self.subTest(label=label, name=name):
                    at_ingest = shows(json.dumps(typed_secrets(name, args)), value)
                    in_text = shows(mask(f"TOOL {name}: {json.dumps(args)}"), value)
                    self.assertEqual(at_ingest, in_text)

    def test_an_object_none_of_a_typing_tools_keys_names_is_read_as_before(self):
        # An object in text cannot be told from a call's input; one with none of the keys a call holds (a ref, a uid, a
        # selector, an element, a label, a placeholder, ...) is a setting, a message or a note, and stays unless its
        # name asks for a secret, as before.
        for kept in ('{"name": "timeout", "value": "30"}', '{"type": "text", "text": "hello there"}',
                     '{"title": "Notes", "content": "remember the milk"}',
                     '[{"name": "Content-Type", "value": "application/json"}]',
                     '{"id": 7, "text": "pelican plan"}', '{"description": "the launch", "value": "approved"}',
                     '{"value": "Zk9qWm2x-Tr0ub4dor"}', 'TOOL other: {"value": "Zk9qWm2x-Tr0ub4dor"}',
                     '{"role": "user", "content": [{"type": "text", "text": "hello"}]}'):
            with self.subTest(kept=kept):
                self.assertEqual(mask(kept), kept)
        for hidden in ('{"name": "API_TOKEN", "value": "Zq7!walrusLedger93"}',
                       '{"name": "Password", "text": "Zq7!walrusLedger93"}'):
            with self.subTest(hidden=hidden):
                self.assertNotIn("Zq7!walrusLedger93", mask(hidden))

    def test_each_key_a_typing_call_holds_makes_an_object_one_alone(self):
        # With no other label or key, no typing tool's name before it and no action: each of these keys alone reads an
        # object as a call's input, so what it types under a label that is not harmless is hidden.
        for key in ("ref", "uid", "element", "label", "selector", "placeholder", "aria_label", "ariaLabel", "locator",
                    "field", "submit", "slowly"):
            for value in ("Zq7!walrusLedger93", "483920", "Fluffy"):
                objects = [{key: "Zorblax", "text": value}, {"value": value, key: "Zorblax"}] if key not in (
                    "ref", "uid", "submit", "slowly") else [{key: "e9", "text": value}, {"value": value, key: True}]
                for o in objects:
                    for text in (json.dumps(o), repr(o), json.dumps(json.dumps(o))[1:-1]):
                        with self.subTest(text=text):
                            self.assertFalse(shows(mask(text), value))

    def test_an_object_with_only_a_weak_key_for_a_label_is_read_by_its_name_alone_as_before(self):
        # "name", "type", "id", "title", "description" and "target" label settings and notes as well as fields. An object
        # that has one of them and none of a call's own keys hides what it holds where the label asks for a secret,
        # read through each escape and each hidden character, and keeps it otherwise, a text that mentions a password
        # or a key besides (so it is scanned) included.
        for key in ("name", "type", "id", "title", "description", "target"):
            for label in ("Pass\\u200bword", "\\u0050assword", "Pass\u200bword", "API\\u005ftoken", "Pass\ufeffword"):
                text = '{"%s": "%s", "value": "Zq7!walrusLedger93"}' % (key, label)
                with self.subTest(text=text):
                    self.assertNotIn("Zq7!walrusLedger93", mask(text))
            for kept in ('{"%s": "timeout", "value": "30", "hint": "key"}' % key,
                         '{"%s": "Notes", "text": "remember the milk", "about": "password rotation"}' % key,
                         '{"%s": "Backup code", "text": "Zq7!walrusLedger93", "about": "a secret"}' % key):
                with self.subTest(kept=kept):
                    self.assertEqual(mask(kept), kept)

    def test_a_clipped_call_and_a_call_in_a_longer_text(self):
        for label in ("Backup code", "Zorblax"):
            for text in ('TOOL mcp__playwright__browser_type: {"element": "%s", "ref": "e1", "text": "Zq7!walrusLedger93' % label,
                         'I typed it. TOOL mcp__playwright__browser_type: {"element": "%s", "ref": "e1", "text": "Zq7!walrusLedger93"} '
                         "and it's done" % label,
                         json.dumps({"role": "assistant", "content": [{"type": "tool_use", "name": "browser_type", "input": {
                             "element": label, "ref": "e1", "text": "Zq7!walrusLedger93"}}, {"type": "text", "text": "ok"}]})):
                with self.subTest(text=text):
                    self.assertNotIn("Zq7!walrusLedger93", mask(text))


class FailClosedInCodeTest(unittest.TestCase):
    """A call written as code, as a script or Codex's code mode holds it, reads the same rule."""

    @staticmethod
    def scripts(label, value):
        slug = label.lower().replace(" ", "-")
        return [f'tools.mcp__playwright__browser_type({{"element": "{label}", "ref": "e1", "text": "{value}"}})',
                f'await page.getByLabel("{label}").fill("{value}");',
                f'await page.fill("#{slug}", "{value}");',
                f'page.get_by_label("{label}").fill("{value}")',
                f'page.fill("#{slug}", "{value}")',
                f'await page.getByPlaceholder("{label}").type("{value}");',
                f'driver.find_element(By.ID, "{slug}").send_keys("{value}")',
                f'await page.getByRole("textbox", {{ name: "{label}" }}).fill("{value}");']

    def test_a_string_typed_into_a_field_but_a_harmless_one_is_hidden(self):
        for k, label in enumerate(("Auth code", "Backup code", "Security answer", "Pass phrase", "Zorblax", "Password",
                                   "Search backup code", "Email")):
            value = VALUES[k % len(VALUES)]
            for script in self.scripts(label, value):
                with self.subTest(script=script):
                    self.assertFalse(shows(mask(script), value))

    def test_a_string_typed_into_a_harmless_field_stays(self):
        for label in ("Search", "Query", "Address bar", "Filter"):
            for script in self.scripts(label, "pelican plan"):
                with self.subTest(script=script):
                    self.assertEqual(mask(script), script)

    def test_a_string_typed_with_no_field_named_is_hidden(self):
        for value in VALUES:
            for script in (f'await page.keyboard.type("{value}");', f'page.keyboard.type("{value}")',
                           f'tools.mcp__playwright__browser_type({{"ref": "e1", "text": "{value}"}})',
                           f'tools.mcp__chrome_devtools__fill({{"uid": "1_7", "value": "{value}"}})',
                           f'await page.locator(selector).fill("{value}");', f'page.locator(sel).type("{value}")'):
                with self.subTest(script=script):
                    self.assertFalse(shows(mask(script), value))

    def test_a_call_written_with_keywords_hides_what_it_types_under_any_label_but_a_harmless_one(self):
        # A typing tool called as a function, its input given as keywords (Python), or as an object of unquoted keys
        # (JavaScript): read at the call's own level, with no field named too.
        for k, label in enumerate(("Backup code", "Auth code", "Zorblax", "Security answer", "")):
            value = VALUES[k % len(VALUES)]
            for text in (f'browser_type(element="{label}", ref="e1", text="{value}")',
                         f"mcp__playwright__browser_type(text='{value}', element='{label}')",
                         f"browser_fill(selector='#{label}', value='{value}')",
                         f'await tools.mcp__playwright__browser_type({{element: "{label}", ref: "e1", text: "{value}"}})',
                         f'browser_type(ref="e1", text="{value}")', f'browser_type(text="{value}")',
                         f'await tools.mcp__playwright__browser_type({{ref: "e1", text: "{value}"}})'):
                with self.subTest(text=text):
                    self.assertFalse(shows(mask(text), value))
        for text in ('browser_type(element="Search", ref="e1", text="pelican plan")',
                     'await tools.mcp__playwright__browser_type({element: "Search box", text: "pelican plan"})'):
            with self.subTest(kept=text):
                self.assertEqual(mask(text), text)

    def test_a_value_from_a_variable_or_a_call_on_no_page_stays(self):
        for kept in ('await page.getByLabel("Backup code").fill(code);', 'Buffer.from("x").fill("y")',
                     'await page.getByLabel("Backup code").fill(process.env.CODE);',
                     'new Array(3).fill("pelican plan")'):
            with self.subTest(kept=kept):
                self.assertEqual(mask(kept), kept)


class HarmlessListTest(unittest.TestCase):
    def test_the_short_list(self):
        for label in HARMLESS:
            with self.subTest(label=label):
                self.assertTrue(masking._harmless_labels([label]))
        for label in LOOKALIKES + ORDINARY + KNOWN + MISSED + MADE_UP:
            with self.subTest(label=label):
                self.assertFalse(masking._harmless_labels([label]))

    def test_a_field_with_no_label_is_not_on_it(self):
        self.assertFalse(masking._harmless_labels([]))
        self.assertFalse(masking._harmless_labels([""]))

    def test_a_label_that_is_filler_only_is_not_on_it_and_helps_a_harmless_one(self):
        self.assertFalse(masking._harmless_labels(["textbox"]))
        self.assertFalse(masking._harmless_labels(["text", "input"]))
        self.assertTrue(masking._harmless_labels(["Search", "textbox"]))
        self.assertTrue(masking._harmless_labels(["Search", "text", "#q"]))

    def test_one_label_that_is_not_harmless_takes_the_field_off_the_list(self):
        for labels in (["Search", "Backup code"], ["Query", "password"], ["Filter", "type=password"],
                       ["Search", "Search", "Zorblax"], ["URL", "Reset code"]):
            with self.subTest(labels=labels):
                self.assertFalse(masking._harmless_labels(labels))

    def test_a_selector_is_read_by_its_words_without_its_attribute_names(self):
        for selector in ("input[name=q]", "input[type=search]", "#search-input", "input.search-box", "[placeholder=Search]"):
            with self.subTest(selector=selector):
                self.assertTrue(masking._harmless_labels([selector]))
        for selector in ("input[type=password]", "#search-token", "input[name=backup_code]", "#login-form input"):
            with self.subTest(selector=selector):
                self.assertFalse(masking._harmless_labels([selector]))


if __name__ == "__main__":
    unittest.main()
