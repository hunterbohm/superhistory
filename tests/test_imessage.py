import dataclasses
import logging
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

from kb import config, ingest, search, store
from kb.sources import Excludes, Item, Progress, Run, imessage, load_type

LOG = logging.getLogger("t")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False

SCHEMA = """
CREATE TABLE message(ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB, handle_id INT, date INT,
  is_from_me INT, associated_message_type INT DEFAULT 0, item_type INT DEFAULT 0, cache_has_attachments INT DEFAULT 0);
CREATE TABLE chat(ROWID INTEGER PRIMARY KEY, guid TEXT, style INT, display_name TEXT, service_name TEXT,
  chat_identifier TEXT);
CREATE TABLE handle(ROWID INTEGER PRIMARY KEY, id TEXT);
CREATE TABLE chat_message_join(chat_id INT, message_id INT, message_date INT);
CREATE TABLE chat_handle_join(chat_id INT, handle_id INT);
CREATE TABLE attachment(ROWID INTEGER PRIMARY KEY, transfer_name TEXT, filename TEXT);
CREATE TABLE message_attachment_join(message_id INT, attachment_id INT);
"""

DANA_CHAT = "iMessage;-;+15550100123"
GROUP_CHAT = "any;+;group1"
SMS_CHAT = "SMS;-;+15550100999"


def blob(text):
    raw = text.encode()
    if len(raw) < 0x80:
        size = bytes([len(raw)])
    else:
        size = b"\x81" + len(raw).to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject"
            b"\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + size + raw + b"\x86\x84\x02iI\x01")


def apple_ns(year, month, day, hour, minute):
    return int((datetime(year, month, day, hour, minute).timestamp() - imessage.APPLE_EPOCH) * 1e9)


def add_message(path, rowid, chat, text, body, handle, from_me, date, assoc=0, files=0):
    db = sqlite3.connect(path)
    db.execute("INSERT INTO message VALUES(?,?,?,?,?,?,?,0,?)", (rowid, text, body, handle, date, from_me, assoc, files))
    db.execute("INSERT INTO chat_message_join VALUES(?,?,?)", (chat, rowid, date))
    db.commit()
    db.close()


def build_chat_db(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.executescript("""
        INSERT INTO handle VALUES(1, '+15550100123'), (2, 'desk@example.com'), (3, '+15550100999');
        INSERT INTO chat VALUES(1, 'iMessage;-;+15550100123', 45, '', 'iMessage', '+15550100123');
        INSERT INTO chat VALUES(2, 'any;+;group1', 43, '', 'iMessage', 'chat1');
        INSERT INTO chat VALUES(3, 'SMS;-;+15550100999', 45, '', 'SMS', '+15550100999');
        INSERT INTO chat_handle_join VALUES(1, 1), (2, 1), (2, 2), (3, 3);
    """)
    db.commit()
    db.close()
    add = lambda *args, **kw: add_message(path, *args, **kw)  # noqa: E731
    add(1, 1, "late night walrus", None, 1, 0, apple_ns(2026, 10, 14, 23, 30))
    add(2, 1, None, blob("next morning, decoded"), 1, 0, apple_ns(2026, 10, 15, 8, 5))
    add(3, 1, None, blob("x" * 400), 0, 1, apple_ns(2026, 10, 15, 8, 6))
    add(4, 1, "Loved “next morning”", None, 1, 0, apple_ns(2026, 10, 15, 8, 7), assoc=2000)
    add(5, 1, "Your verification code is 123456", None, 1, 0, apple_ns(2026, 10, 15, 8, 8))
    add(6, 1, "", None, 1, 0, apple_ns(2026, 10, 15, 8, 9))
    add(7, 2, "group hello quokka", None, 2, 0, apple_ns(2026, 10, 15, 9, 0))
    add(8, 3, "Liked it", None, 0, 1, apple_ns(2026, 10, 15, 9, 0), assoc=2001)
    add(9, 3, "Your verification code is 999999", None, 3, 0, apple_ns(2026, 10, 15, 9, 1))
    add(10, 1, "￼", None, 0, 1, apple_ns(2026, 10, 16, 7, 0), files=1)
    db = sqlite3.connect(path)
    db.executescript("""
        INSERT INTO attachment VALUES(1, 'IMG_1.heic', '~/Library/Messages/Attachments/a/IMG_1.heic');
        INSERT INTO attachment VALUES(2, 'x.pluginPayloadAttachment', 'p');
        INSERT INTO message_attachment_join VALUES(10, 1), (10, 2);
    """)
    db.commit()
    db.close()


def build_addressbook(folder):
    book_dir = os.path.join(folder, "Sources", "SRC-1")
    os.makedirs(book_dir)
    book = sqlite3.connect(os.path.join(book_dir, "AddressBook-v22.abcddb"))
    book.executescript("""
        CREATE TABLE ZABCDRECORD(Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZNICKNAME TEXT,
          ZORGANIZATION TEXT);
        CREATE TABLE ZABCDPHONENUMBER(ZOWNER INT, ZFULLNUMBER TEXT);
        CREATE TABLE ZABCDEMAILADDRESS(ZOWNER INT, ZADDRESS TEXT);
        INSERT INTO ZABCDRECORD VALUES(1, 'Dana', 'Park', NULL, NULL), (2, NULL, NULL, NULL, 'Example Co');
        INSERT INTO ZABCDPHONENUMBER VALUES(1, '(555) 010-0123');
        INSERT INTO ZABCDEMAILADDRESS VALUES(2, 'Desk@Example.com');
    """)
    book.commit()
    book.close()


class Case(unittest.TestCase):
    def setUp(self):
        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/Los_Angeles"
        time.tzset()
        self.addCleanup(self.restore_tz, old_tz)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.db_path = os.path.join(self.root, "chat.db")
        self.contacts = os.path.join(self.root, "AddressBook")
        build_chat_db(self.db_path)
        build_addressbook(self.contacts)

    @staticmethod
    def restore_tz(old):
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()

    def options(self, **changes):
        base = imessage.Options(db=self.db_path, contacts=self.contacts, window_days=36500)
        return dataclasses.replace(base, **changes)

    def events(self, since=None, patterns=(), **changes):
        return list(imessage.TYPE.extract(Run(self.options(**changes), Excludes(patterns), since)))

    def items(self, since=None, patterns=(), **changes):
        return {e.source_id: e for e in self.events(since, patterns, **changes) if isinstance(e, Item)}

    def add(self, *args, **kw):
        add_message(self.db_path, *args, **kw)


class ExtractTest(Case):
    def test_decoder_reads_short_and_long_lengths(self):
        self.assertEqual(imessage.decode_attributed(blob("hi there")), "hi there")
        self.assertEqual(imessage.decode_attributed(blob("é" * 300)), "é" * 300)
        self.assertIsNone(imessage.decode_attributed(b"not a typedstream"))
        self.assertIsNone(imessage.decode_attributed(None))

    def test_one_item_per_chat_per_local_day(self):
        self.assertEqual(sorted(self.items()), [
            f"{GROUP_CHAT}:2026-10-15",
            f"{DANA_CHAT}:2026-10-14",
            f"{DANA_CHAT}:2026-10-15",
            f"{DANA_CHAT}:2026-10-16",
        ])

    def test_fields_and_content_with_a_text_that_only_has_an_attributed_body(self):
        item = self.items()[f"{DANA_CHAT}:2026-10-15"]
        self.assertEqual(item.title, "iMessage with Dana Park on 2026-10-15")
        self.assertEqual(item.ts, "2026-10-15T15:06:00Z")
        lines = item.content.splitlines()
        self.assertEqual(lines[0], "08:05 Dana Park: next morning, decoded")
        self.assertEqual(lines[1], "08:06 Me: " + "x" * 400)
        self.assertEqual(len(lines), 2)
        self.assertEqual(item.meta, {"path": self.db_path, "chat": DANA_CHAT, "participants": ["Dana Park"],
                                     "url": "imessage://+15550100123"})

    def test_the_users_own_lines_carry_the_me_option(self):
        item = self.items(me="Sam")[f"{DANA_CHAT}:2026-10-15"]
        self.assertIn("08:06 Sam: " + "x" * 400, item.content)

    def test_group_chat_names_senders_and_has_no_url(self):
        item = self.items()[f"{GROUP_CHAT}:2026-10-15"]
        self.assertEqual(item.title, "iMessage with Dana Park, Example Co on 2026-10-15")
        self.assertEqual(item.content, "09:00 Example Co: group hello quokka")
        self.assertNotIn("url", item.meta)

    def test_a_named_group_is_titled_by_its_name(self):
        db = sqlite3.connect(self.db_path)
        db.execute("UPDATE chat SET display_name = 'Book club' WHERE ROWID = 2")
        db.commit()
        db.close()
        self.assertEqual(self.items()[f"{GROUP_CHAT}:2026-10-15"].title, "iMessage with Book club on 2026-10-15")

    def test_without_an_address_book_the_handles_stand_in_for_names(self):
        item = self.items(contacts=os.path.join(self.root, "no-such-folder"))[f"{GROUP_CHAT}:2026-10-15"]
        self.assertEqual(item.title, "iMessage with +15550100123, desk@example.com on 2026-10-15")

    def test_local_midnight_splits_days(self):
        items = self.items()
        self.assertIn("late night walrus", items[f"{DANA_CHAT}:2026-10-14"].content)
        self.assertNotIn("late night", items[f"{DANA_CHAT}:2026-10-15"].content)

    def test_attachments_are_listed_and_plugin_payloads_dropped(self):
        self.assertEqual(self.items()[f"{DANA_CHAT}:2026-10-16"].content, "07:00 Me: [attachment: IMG_1.heic]")

    def test_tapbacks_auth_codes_and_empty_rows_are_dropped_and_an_empty_day_skipped(self):
        items = self.items()
        everything = "\n".join(i.content for i in items.values())
        self.assertNotIn("verification", everything)
        self.assertNotIn("Loved", everything)
        self.assertFalse([k for k in items if k.startswith("SMS;")])

    def test_an_sms_chat_points_at_the_sms_scheme_and_titles_by_number(self):
        self.add(11, 3, "real text", None, 3, 0, apple_ns(2026, 10, 15, 10, 0))
        item = self.items()[f"{SMS_CHAT}:2026-10-15"]
        self.assertEqual(item.meta["url"], "sms:+15550100999")
        self.assertEqual(item.title, "iMessage with +15550100999 on 2026-10-15")

    def test_a_chat_with_no_handles_falls_back_to_its_identifier(self):
        db = sqlite3.connect(self.db_path)
        db.execute("INSERT INTO chat VALUES(4, 'SMS;-;5550100777', 45, '', 'SMS', '5550100777')")
        db.commit()
        db.close()
        self.add(11, 4, "short code text", None, 0, 0, apple_ns(2026, 10, 15, 11, 0))
        item = self.items()["SMS;-;5550100777:2026-10-15"]
        self.assertEqual(item.meta["url"], "sms:5550100777")
        self.assertEqual(item.content, "11:00 Unknown: short code text")

    def test_the_window_limits_the_first_run(self):
        self.assertEqual(self.items(window_days=1), {})

    def test_the_database_stays_read_only(self):
        before = os.stat(self.db_path).st_mtime_ns
        self.items()
        self.assertEqual(os.stat(self.db_path).st_mtime_ns, before)

    def test_a_database_path_may_hold_spaces_and_a_home_prefix(self):
        folder = os.path.join(self.root, "Application Support")
        os.makedirs(folder)
        moved = os.path.join(folder, "chat.db")
        os.rename(self.db_path, moved)
        with mock.patch.dict(os.environ, {"HOME": self.root}):
            events = self.events(db="~/Application Support/chat.db")
        self.assertEqual(len([e for e in events if isinstance(e, Item)]), 4)


class MarkTest(Case):
    def test_the_mark_is_the_largest_message_rowid_as_an_int(self):
        events = self.events()
        marks = {e.mark for e in events}
        self.assertEqual(marks, {10})
        self.assertIsInstance(events[0].mark, int)

    def test_a_rerun_from_the_mark_has_nothing_to_read(self):
        self.assertEqual(self.events(since=10), [])

    def test_a_new_message_rereads_its_whole_day(self):
        self.add(12, 1, "one more", None, 1, 0, apple_ns(2026, 10, 15, 18, 0))
        again = self.events(since=10)
        self.assertEqual([e.source_id for e in again if isinstance(e, Item)], [f"{DANA_CHAT}:2026-10-15"])
        item = again[0]
        self.assertIn("next morning, decoded", item.content)
        self.assertIn("18:00 Dana Park: one more", item.content)
        self.assertEqual(item.mark, 12)

    def test_a_rebuilt_database_below_the_mark_restarts_ingest(self):
        self.assertEqual(len(self.items(since=5000)), 4)

    def test_noise_alone_still_moves_the_mark(self):
        self.add(12, 1, "Your verification code is 424242", None, 1, 0, apple_ns(2026, 10, 17, 8, 0))
        events = self.events(since=10)
        self.assertEqual(events, [Progress(12)])


class ExcludeTest(Case):
    def test_nothing_is_excluded_by_default(self):
        self.assertEqual(imessage.TYPE.exclude, ())
        self.assertEqual(len(self.items()), 4)

    def test_a_phone_handle_excludes_the_chats_it_is_in(self):
        self.assertEqual(sorted(self.items(patterns=["+15550100123"])), [])

    def test_an_email_handle_excludes_only_chats_with_that_person(self):
        self.assertEqual(sorted(self.items(patterns=["desk@example.com"])), [f"{DANA_CHAT}:{d}" for d in (
            "2026-10-14", "2026-10-15", "2026-10-16")])

    def test_a_contact_name_excludes_the_chats_with_that_person(self):
        self.assertEqual(sorted(self.items(patterns=["Example Co"])), [f"{DANA_CHAT}:{d}" for d in (
            "2026-10-14", "2026-10-15", "2026-10-16")])
        self.assertEqual(self.items(patterns=["Dana Park"]), {})

    def test_a_group_name_excludes_that_group(self):
        db = sqlite3.connect(self.db_path)
        db.execute("UPDATE chat SET display_name = 'Book club' WHERE ROWID = 2")
        db.commit()
        db.close()
        items = self.items(patterns=["Book club"])
        self.assertNotIn(f"{GROUP_CHAT}:2026-10-15", items)
        self.assertEqual(len(items), 3)

    def test_a_pattern_may_use_wildcards(self):
        self.assertEqual(self.items(patterns=["+1555010*"]), {})

    def test_an_excluded_chat_still_moves_the_mark(self):
        events = self.events(patterns=["+15550100123"])
        self.assertEqual(events, [Progress(10)])


class BlockedDatabaseTest(Case):
    def test_a_blocked_database_names_the_python_that_needs_full_disk_access(self):
        refused = sqlite3.OperationalError("unable to open database file")
        with mock.patch.object(imessage.sqlite3, "connect", side_effect=refused), \
                mock.patch.object(imessage.sys, "executable", "/opt/py/bin/python3"):
            with self.assertRaisesRegex(PermissionError, "Full Disk Access to /opt/py/bin/python3"):
                self.events()

    def test_a_missing_database_keeps_the_sqlite_error(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.events(db=self.db_path + ".gone")


class TypeTest(unittest.TestCase):
    def test_the_type_is_found_by_name_and_describes_texts_on_a_mac(self):
        self.assertIs(load_type("imessage"), imessage.TYPE)
        self.assertEqual(imessage.TYPE.platforms, frozenset({"darwin"}))
        self.assertTrue(imessage.TYPE.others_wrote)
        self.assertTrue(imessage.TYPE.summarize)
        self.assertFalse(imessage.TYPE.needs_model)

    def test_the_options_default_to_the_macos_locations(self):
        options = imessage.Options()
        self.assertEqual(options.db, "~/Library/Messages/chat.db")
        self.assertEqual(options.contacts, "~/Library/Application Support/AddressBook")
        self.assertEqual((options.window_days, options.me), (365, "Me"))


class RunnerTest(Case):
    def parse(self, extra=""):
        text = (f'machine = "m1"\n[sources.texts]\ntype = "imessage"\ndb = "{self.db_path}"\n'
                f'contacts = "{self.contacts}"\nwindow_days = 36500\nme = "Sam"\n{extra}')
        return config.parse(text)

    def database(self):
        db = store.connect(os.path.join(self.root, "kb", "kb.db"))
        self.addCleanup(db.close)
        return db

    def stored(self, db):
        return {r[0] for r in db.execute("SELECT DISTINCT source_id FROM docs WHERE source = 'texts'")}

    def test_texts_are_stored_found_by_search_and_the_mark_carries_over(self):
        db = self.database()
        with mock.patch("sys.platform", "darwin"):
            failures = ingest.run(db, self.parse(), LOG, with_summaries=False)
            self.assertEqual(failures, 0)
            self.assertEqual(self.stored(db), {f"m1:{DANA_CHAT}:2026-10-14", f"m1:{DANA_CHAT}:2026-10-15",
                                               f"m1:{DANA_CHAT}:2026-10-16", f"m1:{GROUP_CHAT}:2026-10-15"})
            hits = search.search(db, "quokka", sources=("texts",))
            self.assertEqual([h["source_id"] for h in hits], [f"m1:{GROUP_CHAT}:2026-10-15"])
            self.assertEqual(hits[0]["meta"]["host"], "m1")
            self.assertEqual(hits[0]["meta"]["chat"], GROUP_CHAT)
            hits = search.search(db, "decoded", sources=("texts",))
            self.assertIn("08:06 Sam: ", hits[0]["content"])
            self.add(12, 2, "afterwards narwhal", None, 2, 0, apple_ns(2026, 10, 15, 19, 0))
            ingest.run(db, self.parse(), LOG, with_summaries=False)
            hits = search.search(db, "narwhal", sources=("texts",))
            self.assertEqual([h["source_id"] for h in hits], [f"m1:{GROUP_CHAT}:2026-10-15"])
            self.assertEqual(store.get_watermark(db, "texts", "m1"), "12")

    def test_excluded_chats_never_reach_the_store(self):
        db = self.database()
        with mock.patch("sys.platform", "darwin"):
            ingest.run(db, self.parse('exclude = ["Example Co"]\n'), LOG, with_summaries=False)
        self.assertEqual(self.stored(db), {f"m1:{DANA_CHAT}:2026-10-14", f"m1:{DANA_CHAT}:2026-10-15",
                                           f"m1:{DANA_CHAT}:2026-10-16"})
        self.assertEqual(search.search(db, "quokka", sources=("texts",)), [])

    def test_the_source_fails_on_another_platform(self):
        db = self.database()
        with mock.patch("sys.platform", "linux"):
            failures = ingest.run(db, self.parse(), LOG, with_summaries=False)
        self.assertEqual(failures, 1)
        self.assertEqual(self.stored(db), set())


if __name__ == "__main__":
    unittest.main()
