"""A value made only of digits and ":.+TZ-" under a key's or a token's name: a real ISO 8601 date or time, a key's
expiry or a session's start, is shown, and every other such value is read as a secret, as before #29 read none as a
date. Made-up secrets only."""
import importlib.util
import json
import os
import re
import tempfile
import unittest
from unittest import mock

from kb import mask as masking
from kb import store
from kb.mask import mask, mask_all

# Each shows on main 68506d9, which reads any value of these characters as a date or a time.
SHAPES = (("api_key", ": ", "1234-5678-9012-3456"), ("secret", ": ", "4829-1937-5521-0098-7731"),
          ("API_TOKEN", "=", "1699999999.123456789"), ("access_key", ": ", "8472:1934:5521:0098"),
          ("auth_token", ": ", "20261101T100000Z1234"))
NAMES = ("api_key", "secret", "API_TOKEN", "access_key", "auth_token", "client_secret", "session_key")
DATES = ("2026-11-01", "2026-11-01T10:00", "2026-11-01T10:00:00", "2026-11-01T10:00:00Z", "2026-11-01T23:59:59.123Z",
         "2026-11-01T10:00:00.123456789+05:30", "2026-11-01T10:00:00-08:00", "2024-02-29T10:00:00Z")
# Shaped as one, with a field out of its range.
NOT_DATES = ("2026-13-01T10:00:00Z", "2026-02-30T10:00:00Z", "2025-02-29T10:00:00Z", "2026-11-00T10:00:00Z",
             "2026-11-01T24:00:00Z", "2026-11-01T10:60:00Z", "2026-11-01T10:00:61Z", "2026-11-01T10:00:00+24:00",
             "2026-11-01T10:00:00.1234567890Z", "2026-11-01T10:00:00Z1234", "2026-11-01-1234-5678")
# Seconds since 1970 under a token's name: masked where the mask before #29 masked them, 16 characters or more.
EPOCHS = ("1699999999.123456789", "1699999999.123456", "1699999999123.4567", "1699999999+0000.12")
SHORT_NUMBERS = ("1699999999", "1699999999.123", "1699999999123")


def forms(name, sep, value):
    """The named value as a line of text, inside lines, as a JSON field, and escaped in JSON once and twice, with any
    character past ASCII written as it is and as "\\u" escapes."""
    plain = f"{name}{sep}{value}"
    field = json.dumps({name: value})
    out = {"plain": plain, "lines": f"config\n{plain}\nend", "after words": f"export {plain} && make deploy",
           "json": field, "json once": json.dumps(plain)[1:-1], "json twice": json.dumps(json.dumps(plain))[1:-1],
           "json field once": json.dumps({"text": field}), "json field twice": json.dumps({"t": json.dumps({"text": field})})}
    if not value.isascii():
        raw = json.dumps({name: value}, ensure_ascii=False)
        out.update({"json as written": raw, "json once as written": json.dumps(plain, ensure_ascii=False)[1:-1],
                    "json field once as written": json.dumps({"text": raw}, ensure_ascii=False)})
    return out


def before_29():
    """The mask before #29 (main at 3be191e), which read no value as a date."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "main_mask.py")
    spec = importlib.util.spec_from_file_location("main_mask_dates", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NumberShapedSecretTest(unittest.TestCase):
    def assertHidden(self, name, sep, value):
        for form, text in forms(name, sep, value).items():
            with self.subTest(name=name, value=value, form=form):
                self.assertNotIn(value, mask(text))
        with self.subTest(name=name, value=value, form="mask_all"):
            self.assertEqual(mask_all({name: value}), {name: "[masked]"})

    def assertShown(self, name, sep, value):
        for form, text in forms(name, sep, value).items():
            with self.subTest(name=name, value=value, form=form):
                self.assertEqual(mask(text), text)
        with self.subTest(name=name, value=value, form="mask_all"):
            self.assertEqual(mask_all({name: value}), {name: value})

    def test_the_five_shapes_are_masked(self):
        for name, sep, value in SHAPES:
            self.assertHidden(name, sep, value)
        self.assertEqual(mask("api_key: 1234-5678-9012-3456"), "api_key: [masked]")
        self.assertEqual(mask("API_TOKEN=1699999999.123456789"), "API_TOKEN=[masked]")

    def test_each_shape_under_each_name_is_masked(self):
        for name in NAMES:
            for _, sep, value in SHAPES:
                self.assertHidden(name, sep, value)

    def test_a_real_date_or_time_under_a_key_name_is_shown(self):
        for name, sep, value in (("expires_at", ": ", "2026-11-01T10:00:00Z"), ("token_expiry", ": ", "2026-11-01"),
                                 ("session_key", ": ", "2026-11-01T10:00:00Z"),
                                 ("access_token_expires", ": ", "2026-11-01T10:00:00+00:00")):
            self.assertShown(name, sep, value)
        for name in NAMES:
            for value in DATES:
                self.assertShown(name, "=", value)

    def test_a_date_with_a_field_out_of_range_is_masked(self):
        for name in NAMES:
            for value in NOT_DATES:
                self.assertHidden(name, ": ", value)

    def test_seconds_since_1970_are_masked_as_before_29(self):
        for value in EPOCHS:
            self.assertHidden("API_TOKEN", "=", value)
            self.assertHidden("auth_token", ": ", value)
        older = before_29()
        for value in EPOCHS + SHORT_NUMBERS:
            for name, sep in (("API_TOKEN", "="), ("auth_token", ": "), ("access_key", ": ")):
                for form, text in forms(name, sep, value).items():
                    with self.subTest(value=value, name=name, form=form):
                        self.assertEqual(mask(text), older.mask(text))

    def test_a_date_in_other_digits_is_judged_as_before_29(self):
        # Python's \d takes any script's digits; an ISO 8601 date's are ASCII.
        values = ("\uff12\uff10\uff12\uff16-11-01T10:00:00Z", "2\u0660\u0662\u0666-11-01T10:00:00Z",
                  "2026-11-01T1\uff10:00:00Z", "2026-11-01T10:0\u0660:00.12\u0663Z", "2026-11-01T10:00:00+0\uff15:30",
                  "\uff12\uff10\uff12\uff16-\uff11\uff11-\uff10\uff11")
        for value in values[:5]:
            self.assertHidden("api_key", "=", value)
        # Main 68506d9 read each as a date, as any run of \d and ":.+TZ-" was one, and showed it.
        with mock.patch.object(masking, "_date_or_time", lambda value: bool(re.fullmatch(r"[\d:.+TZ-]+", value))):
            for value in values:
                for form, text in forms("api_key", "=", value).items():
                    if form in ("plain", "lines", "after words") or form.endswith("as written"):
                        with self.subTest(value=value, form=form, read="as main"):
                            self.assertEqual(mask(text), text)
        older = before_29()
        for value in values:
            for name, sep in (("api_key", "="), ("auth_token", ": ")):
                for form, text in forms(name, sep, value).items():
                    with self.subTest(value=value, name=name, form=form):
                        self.assertEqual(mask(text), older.mask(text))


# After "Authorization:" with no scheme word, a credential made only of digits and ":.+TZ-", with a T or a Z in it:
# main 2ec4d8c read each as a number, a date or a time, as it reads any such run in a lone token, and showed it. From
# "20261301T100000Z" on, each is shaped as a date and a time, a time or a range of two, with a field out of its range or
# more joined to it.
AUTHORIZATION_SHAPES = ("20261101T100000Z1234", "2026-11-01T10:00:00Z1234", "1234-5678T9012Z3456", "T1234567890Z12345",
                        "2026-13-01T10:00:00Z", "2026-11-01T10:00:00.1234567890Z", "20261301T100000Z",
                        "20261101T250000Z", "2026-366T10:00:00Z", "2026-11-01T24:00:01Z", "2026-11-01T10:00:00+2400",
                        "10:00:61.123456Z", "25:00:00.123456Z", "24:00:00.123456Z", "10:00:00.123456Z1234",
                        "20261101T100000Z-1234", "10:00:00Z-11:00:61Z", "2026-11-01T10:00Z-1234-5678")
AUTHORIZATION_DATES = ("2026-11-01T10:00:00Z", "2026-11-01T23:59:59.123Z", "2026-11-01T10:00:00.123456789+05:30",
                       "2024-02-29T10:00:00Z", "2026-11-01T10:00", "2026-11-01")
# A date and a time as logs and clocks write them besides ISO 8601's extended form: its basic form (AWS's X-Amz-Date),
# an offset without its colon or of hours alone (strftime's %z), a leap second, the day's end and an ordinal date; the
# two forms mixed, as file names take one, a fraction of the last field given, a year past 9999 as JavaScript writes
# it; a time of day alone, in either form; and a range of two, joined by "-" or "--".
TIMESTAMPS = ("20261101T100000Z", "20261101T100000.123Z", "20261101T100000+0530", "20261101T100000-08",
              "2026-11-01T10:00:00+0000", "2026-11-01T10:00:00.000+0000", "2026-11-01T10:00:00+00",
              "2016-12-31T23:59:60Z", "2026-11-01T24:00:00Z", "2026-305T10:00:00Z", "2024-366T10:00:00.5Z",
              "2026305T100000Z", "2026-11-01T100000Z", "20261101T10:00:00.5Z", "2026-11-01T10:30.5Z",
              "+002026-11-01T10:00:00Z", "10:00:00.123456Z", "T10:00:00.123456+05:30", "T100000.123456789Z",
              "100000.123456789Z", "23:59:60.123456Z", "24:00:00.000000Z", "10:00:00Z-11:00:00Z",
              "2026-11-01T10:00Z--2026-11-01T11:00Z", "2026-11-01T10:00:00Z-2026-11-02T10:00:00Z",
              "2026-11-01T10:00:00Z-11:00:00Z")
# Numbers agents' logs hold, none with a letter, so none is shaped like a credential, here or on main.
EVERYDAY = ("192.168.10.254", "192.168.10.254:8080", "10.0.19045.3803", "1.2.3+20261101", "2.0.0-rc.1",
            "+44-20-7946-0958", "+1-555-0100", "00:01:23.456", "1793527200", "1793527200123", "1793527200.123456",
            "1,299.00", "12,345,678.90", "41.8781,-87.6298", "112-4567890-1234567", "2026-000123-4567-8890", "+05:30",
            "-08:00", "+0530")


def header_forms(value):
    """An Authorization header with no scheme as a line, in a request, as a curl argument in either quotes, as a JSON
    field, escaped in JSON once and twice, as Proxy-Authorization, and as a value typed into the focused field."""
    line = f"Authorization: {value}"
    return {"line": line, "request": f"GET /v1 HTTP/1.1\nHost: api.example.com\n{line}\nAccept: */*",
            "curl": f'curl -H "{line}" https://api.example.com/v1', "curl single": f"curl -H '{line}' https://x/v1",
            "json": json.dumps({"Authorization": value}), "json once": json.dumps(line)[1:-1],
            "json twice": json.dumps(json.dumps(line))[1:-1], "proxy": f"Proxy-Authorization: {value}",
            "typed": f'await page.keyboard.type("{value}")'}


class AuthorizationNumberShapedTest(unittest.TestCase):
    def test_a_credential_of_digits_and_t_or_z_after_authorization_is_masked(self):
        for value in AUTHORIZATION_SHAPES:
            for form, text in header_forms(value).items():
                with self.subTest(value=value, form=form):
                    self.assertNotIn(value, mask(text))
            with self.subTest(value=value, form="mask_all"):
                self.assertEqual(mask_all(["curl", "-H", f"Authorization: {value}", "https://x/v1"]),
                                 ["curl", "-H", "Authorization: [masked]", "https://x/v1"])
                self.assertEqual(mask_all({"headers": {"Authorization": value}}), {"headers": {"Authorization": "[masked]"}})
        self.assertEqual(mask("Authorization: 20261101T100000Z1234"), "Authorization: [masked]")

    def test_a_real_date_after_authorization_is_shown_as_on_main(self):
        for value in AUTHORIZATION_DATES:
            for form, text in header_forms(value).items():
                with self.subTest(value=value, form=form):
                    self.assertEqual(mask(text), text)
            with self.subTest(value=value, form="mask_all"):
                self.assertEqual(mask_all({"headers": {"Authorization": value}}), {"headers": {"Authorization": value}})


def shape_alone_forms(value):
    """The places kb reads a value by its shape alone, as the README lists them: after "pass" or "pwd" next to a user's
    name, in an XML element not named for a password, after a name a colour code splits, under a header's or a secret's
    name in a two-item list, and in a field with no label."""
    texts = {"pass": f"user=ci pass={value}", "pwd": f"Server=db;Uid=sa;Pwd={value};", "xml": f"<secret>{value}</secret>",
             "xml key": f"<api_key>{value}</api_key>", "colour code": f"api_\x1b[1mkey\x1b[0m: {value}"}
    held = {"header list": ["Authorization", value], "secret list": ["api_key", value],
            "pass beside a user": {"user": "ci", "pass": value}}
    return texts, held


class ShapeAloneNumberShapedTest(unittest.TestCase):
    # Each reads the value by _credential_shaped, which main 2ec4d8c read as no credential when it was only digits and
    # ":.+TZ-", and so showed.
    def test_a_credential_of_digits_and_t_or_z_read_by_its_shape_alone_is_masked(self):
        for value in AUTHORIZATION_SHAPES:
            texts, held = shape_alone_forms(value)
            for form, text in texts.items():
                with self.subTest(value=value, form=form):
                    self.assertNotIn(value, mask(text))
            for form, item in held.items():
                with self.subTest(value=value, form=form):
                    self.assertNotIn(value, json.dumps(mask_all(item)))
            with self.subTest(value=value, form="field with no label"):
                self.assertEqual(masking.typed_secrets("browser_type", {"text": value}), {"text": "[masked]"})
        self.assertEqual(mask("<secret>20261101T100000Z1234</secret>"), "<secret>[masked]</secret>")

    def test_a_real_date_read_by_its_shape_alone_is_shown_as_on_main(self):
        for value in AUTHORIZATION_DATES:
            texts, held = shape_alone_forms(value)
            for form, text in texts.items():
                with self.subTest(value=value, form=form):
                    self.assertEqual(mask(text), text)
            for form, item in held.items():
                with self.subTest(value=value, form=form):
                    self.assertEqual(mask_all(item), item)
            with self.subTest(value=value, form="field with no label"):
                self.assertEqual(masking.typed_secrets("browser_type", {"text": value}), {"text": value})

    def assertShownByShape(self, value):
        texts, held = shape_alone_forms(value)
        texts.update(header_forms(value))
        for form, text in texts.items():
            with self.subTest(value=value, form=form):
                self.assertEqual(mask(text), text)
        held["headers"] = {"headers": {"Authorization": value}}
        for form, item in held.items():
            with self.subTest(value=value, form=form):
                self.assertEqual(mask_all(item), item)
        with self.subTest(value=value, form="field with no label"):
            self.assertEqual(masking.typed_secrets("browser_type", {"text": value}), {"text": value})

    def test_a_date_and_time_as_logs_write_it_read_by_its_shape_alone_is_shown_as_on_main(self):
        # Read by its shape alone, a real date and time is no credential in any form ISO 8601 or a clock writes it;
        # under a key's name only the extended form is, as #39 has it.
        for value in TIMESTAMPS:
            self.assertShownByShape(value)
        self.assertEqual(mask("api_key: 20261101T100000Z"), "api_key: [masked]")

    def test_everyday_numbers_read_by_their_shape_alone_are_shown_as_on_main(self):
        for value in EVERYDAY:
            self.assertShownByShape(value)


class StoredRowTest(unittest.TestCase):
    """A row main 68506d9 masked, which showed these values, is masked again."""
    MAIN = "ed98bfebca16cee4"  # the version main 68506d9 records on its rows

    def test_a_row_main_stored_is_masked_again(self):
        text = "\n".join(f"{name}{sep}{value}" for name, sep, value in SHAPES) + "\nexpires_at: 2026-11-01T10:00:00Z\n"
        with tempfile.TemporaryDirectory() as tmp:
            db = store.connect(os.path.join(tmp, "kb.sqlite"))
            try:
                db.execute("INSERT INTO docs(kind,source,source_id,ts,title,content,metadata,masked) "
                           "VALUES(?,?,?,?,?,?,?,?)", ("doc", "notes", "notes:plan.md", "2026-11-01", "Plan", text, "{}",
                                                      self.MAIN))
                db.commit()
                store.remask(db)
                content, version = db.execute("SELECT content, masked FROM docs").fetchone()
            finally:
                db.close()
        for _, _, value in SHAPES:
            self.assertNotIn(value, content)
        self.assertIn("expires_at: 2026-11-01T10:00:00Z", content)
        self.assertEqual(version, store.VERSION)
        self.assertEqual(masking.REVISION, 38)

    def test_a_row_main_2ec4d8c_stored_with_an_authorization_of_digits_is_masked_again(self):
        text = "Authorization: 20261101T100000Z1234\nAuthorization: 2026-11-01T10:00:00Z\n"
        with tempfile.TemporaryDirectory() as tmp:
            db = store.connect(os.path.join(tmp, "kb.sqlite"))
            try:
                db.execute("INSERT INTO docs(kind,source,source_id,ts,title,content,metadata,masked) "
                           "VALUES(?,?,?,?,?,?,?,?)", ("doc", "notes", "notes:plan.md", "2026-11-01", "Plan", text, "{}",
                                                      "6d34023992abc2fa"))  # the version main 2ec4d8c records
                db.commit()
                store.remask(db)
                content, version = db.execute("SELECT content, masked FROM docs").fetchone()
            finally:
                db.close()
        self.assertEqual(content, "Authorization: [masked]\nAuthorization: 2026-11-01T10:00:00Z\n")
        self.assertEqual(version, store.VERSION)


if __name__ == "__main__":
    unittest.main()
