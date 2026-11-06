"""This client never sends a record whose id holds a secret, but an older client could. A computer skips such a record,
counts it, and keeps and shows its id nowhere, from a change pack and from a snapshot alike. kb's own store refuses such
an item with ValueError, and that refusal skips the one record too."""
import contextlib
import io
import os
import secrets
import string
import unittest
from unittest import mock

from kb import store
from kb.sync import cli
from kb.sync import records

from .support import World

ITEM = {"ts": "2026-10-01", "title": "Note", "meta": {"hash": "h"}, "rows": ["made-up text"]}


def token():
    return "ghp_" + "".join(secrets.choice(string.ascii_letters) for _ in range(36))


class SecretIdTest(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.secret = token()

    def tearDown(self):
        self.world.close()

    def assert_nowhere(self, computer, out=""):
        self.assertNotIn(self.secret, out)
        for folder, _, names in os.walk(computer.home):
            for name in names:
                with open(os.path.join(folder, name), "rb") as f:
                    self.assertNotIn(self.secret.encode(), f.read(), os.path.relpath(os.path.join(folder, name),
                                                                                     computer.home))

    def test_a_change_pack_record_whose_id_holds_a_secret_is_skipped_counted_and_shown_nowhere(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        plain = a.key("plain")
        self.world.forge(a, [
            records.Record("item", "notes", a.key(f"keys/{self.secret}"), 1, ITEM),
            records.Record("item", "notes", plain, 1, ITEM),
            records.Record("supersede", "notes", plain, 2, {"by": a.prefix, "new": ["notes", a.key(self.secret)],
                                                            "at": "2026-10-05"}),
        ])
        self.world.tick()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_sync(b.client, None), None)
        self.assertIn("Skipped 2 records from other computers whose names look like they hold a secret.", out.getvalue())
        self.assertTrue(b.has("notes", plain), "the rest of the pack is applied")
        self.assertIsNone(store.superseded_by(b.db, "notes", plain))
        b.client.close()
        b.client = None
        self.assert_nowhere(b, out.getvalue())

    def test_a_snapshot_record_whose_id_holds_a_secret_is_skipped_and_counted(self):
        (a,), _ = self.world.group("computer 1")
        a.item("notes", f"keys/{self.secret}", "made-up text")
        a.item("notes", "plain", "made-up text")
        with mock.patch.object(records, "holds_secret", lambda *texts: False):  # as an older client would
            a.sync()
            self.assertTrue(a.client.compact(force=True))
        b = self.world.computer("computer 2")
        report = self.world.pair(a, b)
        self.assertEqual(report.skipped, 1)
        self.assertTrue(b.has("notes", a.key("plain")))
        b.client.close()
        b.client = None
        self.assert_nowhere(b)

    def test_an_own_record_whose_id_holds_a_secret_is_skipped_and_its_id_kept_nowhere(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        self.world.forge(a, [records.Record("item", "notes", a.key(f"keys/{self.secret}"), 1, ITEM)])
        report = a.sync()
        self.assertEqual(report.skipped, 1, "a pack an older copy of this computer signed")
        a.client.close()
        a.client = None
        self.assert_nowhere(a)

    def test_a_host_that_kb_would_not_take_never_splits_an_id_around_its_secret(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        value = f"Pw{secrets.token_hex(8)}"
        self.secret = value
        forged = [(f"{a.prefix}:password", a.key(f"password:{value}")),
                  ("password", a.key(f"password:{value}")),
                  ("token", f"{a.prefix}@token:{value}"),
                  ("mail", a.key(f"//user:{value}@example.org/inbox"))]
        plain = a.key("plain")
        self.world.forge(a, [records.Record("item", "notes", sid, 1, {**ITEM, "meta": {"hash": "h", "host": host}})
                             for host, sid in forged]
                         + [records.Record("item", "notes", plain, 1, {**ITEM, "meta": {"hash": "h", "host": "tokenbox"}})])
        self.world.tick()
        self.assertEqual(b.sync().skipped, len(forged))
        self.assertTrue(b.has("notes", plain), "a host kb takes still reads as a host")
        b.client.close()
        b.client = None
        self.assert_nowhere(b)

    def test_a_host_that_is_not_text_stops_the_pull_at_its_pack_and_says_why(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        plain = a.item("notes", "plain", "made-up text")
        a.sync()
        b.sync()
        row = {"kind": "atom", "ts": "2026-10-01", "title": "t", "content": "c", "metadata": {"host": []}}
        self.world.forge(a, [records.Record("summary", "notes", plain, 1, {"of_hash": "h", "rows": [row]})])
        self.world.tick()
        report = b.sync()
        self.assertRegex(report.stalled or "", "bad record")
        for bad in ([], {}, 5):
            for rec in (records.Record("summary", "notes", plain, 1, {"of_hash": "h", "rows": [
                            {**row, "metadata": {"host": bad}}]}),
                        records.Record("item", "notes", a.key("other"), 1, {**ITEM, "meta": {"hash": "h", "host": bad}})):
                with self.subTest(kind=rec.kind, host=bad), self.assertRaises(records.BadRecord):
                    records.from_wire(records.to_wire(rec))

    def test_a_record_the_store_refuses_is_skipped_and_the_rest_are_applied(self):
        (a,), _ = self.world.group("computer 1")
        recs = [records.Record("item", "notes", f"c-bbbbbbbbbbbbb:{n}", 1, ITEM) for n in ("one", "two", "three")]

        def item(db, rec):
            if rec.source_id.endswith(":two"):
                raise ValueError("refused for a reason this client does not check first")
            records._WRITE["item"](db, rec)
        self.assertEqual(records.apply(a.db, recs, a.prefix, write={**records._WRITE, "item": item}), 1)
        self.assertEqual(sorted(r[0] for r in a.db.execute("SELECT DISTINCT source_id FROM docs")),
                         ["c-bbbbbbbbbbbbb:one", "c-bbbbbbbbbbbbb:three"])

        summary = records.Record("summary", "notes", "c-bbbbbbbbbbbbb:four", 1, {"of_hash": "h", "rows": [
            {"kind": "atom", "ts": "2026-10-01", "title": "t", "content": "c", "metadata": {"of_hash": "h"}}]})
        self.assertEqual(records.apply(a.db, [summary], a.prefix), 0, "the summary waits for its item")

        def refusing(db, rec):
            raise ValueError("refused for a reason this client does not check first")
        later = records.Record("item", "notes", "c-bbbbbbbbbbbbb:four", 1, ITEM)
        self.assertEqual(records.apply(a.db, [later], a.prefix, write={**records._WRITE, "summary": refusing}), 1,
                         "the waiting summary, refused once its item arrives, is counted")


if __name__ == "__main__":
    unittest.main()
