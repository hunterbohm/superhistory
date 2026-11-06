import unittest

from kb import store

from .support import World, marks, put_summary, rows


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.world = World()

    def tearDown(self):
        self.world.close()

    def test_two_stores_converge(self):
        a = self.world.computer("computer 1")
        code, first = a.client.init(self.world.browser())
        self.assertRegex(code, r"^VRC1-")
        session = a.item("claude_code", "s1", "user: hi\n")
        long_key = a.item("notes", "long", "".join(f"paragraph {i} about the relay\n" for i in range(300)))
        long_hash = a.text("notes", long_key)["meta"]["hash"]
        put_summary(a.db, "notes", long_key, long_hash, [("segment", "Part", "the first part"),
                                                                     ("atom", "Fact", "relays cannot read")])
        a.sync()
        b = self.world.computer("computer 2")
        self.world.pair(a, b)
        self.assertGreater(len(a.text("notes", long_key)["rows"]), 1, "the long item is stored as raw chunks")
        self.assertEqual(rows(a.db), rows(b.db))

        theirs = b.item("git", "c1", "commit: fix pairing")
        b.sync()
        a.sync()
        self.assertTrue(a.has("git", theirs))
        store.mark_superseded(a.db, "git", theirs, ("notes", long_key), a.prefix, "2026-10-05")
        store.delete_item(a.db, "notes", long_key)
        a.item("claude_code", "s1", "user: hi\nassistant: hello\n" + "more words\n" * 600)
        a.sync()
        b.sync()
        self.assertEqual(rows(a.db), rows(b.db))
        self.assertEqual(marks(a.db), marks(b.db))
        self.assertFalse(b.has("notes", long_key))
        self.assertEqual(store.superseded_by(b.db, "git", theirs), ("notes", long_key))
        self.assertIn("assistant: hello", "".join(b.text("claude_code", session)["rows"]))

    def test_a_newer_clear_outranks_an_older_mark(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        target = a.item("notes", "target", "the item that gets superseded")
        first = a.item("notes", "first", "one replacement")
        second = a.item("notes", "second", "another replacement")
        a.sync()
        b.sync()
        store.mark_superseded(a.db, "notes", target, ("notes", first), a.prefix, "2026-10-05")
        a.sync()
        b.sync()
        store.mark_superseded(b.db, "notes", target, ("notes", second), b.prefix, "2026-10-06")
        b.sync()
        a.sync()
        self.assertEqual(store.superseded_by(a.db, "notes", target), ("notes", second))
        store.clear_superseded(b.db, "notes", target, b.prefix, "2026-10-07")
        b.sync()
        a.sync()
        self.assertIsNone(store.superseded_by(a.db, "notes", target), "the newest choice is a clear")
        self.assertIsNone(store.superseded_by(b.db, "notes", target))
        self.assertEqual(marks(a.db), marks(b.db))


if __name__ == "__main__":
    unittest.main()
