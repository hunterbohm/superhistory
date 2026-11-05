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
        self.assertEqual(masking.REVISION, 11)


if __name__ == "__main__":
    unittest.main()
