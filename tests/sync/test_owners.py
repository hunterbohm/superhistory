import os
import unittest

from kb import store
from kb.sync import state

from .support import EMAIL, World, put_item, rows


def ssh_item(comp, name, text):
    """An item `comp` read on another host over SSH, stored under "<install id>@<host>:"."""
    sid = f"{comp.prefix}@laptop:{name}"
    put_item(comp.db, "notes", sid, "2026-10-01", name, text, {})
    return sid


class ItemsReadOverSshTest(unittest.TestCase):
    """An item a computer reads over SSH is its own, so it syncs, and no other computer prunes or overwrites it."""

    def setUp(self):
        self.world = World()

    def tearDown(self):
        self.world.close()

    def test_they_sync_under_their_owner(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        sid = ssh_item(a, "remote.md", "read on the laptop")
        self.assertEqual(a.sync().sent, 1)
        self.assertEqual(b.sync().received, 1)
        self.assertEqual(b.text("notes", sid)["rows"], ["read on the laptop"])
        self.assertEqual(b.sync().sent, 0, "computer 2 never sends computer 1's item as its own")

        store.delete_item(b.db, "notes", sid)
        self.assertEqual(b.sync().sent, 0, "a loss on computer 2 never goes out as a Gone")
        a.sync()
        self.assertTrue(a.has("notes", sid))

        ssh_item(a, "remote.md", "edited on the laptop")
        a.sync()
        b.sync()
        self.assertEqual(b.text("notes", sid)["rows"], ["edited on the laptop"])
        store.delete_item(a.db, "notes", sid)
        a.sync()
        b.sync()
        self.assertFalse(b.has("notes", sid), "the owner's delete reaches computer 2")

    def test_a_snapshot_neither_overwrites_nor_deletes_them(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        sid = ssh_item(a, "remote.md", "first")
        a.sync()
        b.sync()
        self.assertTrue(b.client.compact(force=True))
        self.assertLess(state.get(a.db, "cursor", 0), self.world.fake.group(EMAIL).snapshot[1], "a reads the snapshot")

        ssh_item(a, "remote.md", "second, not sent yet")
        fresh = ssh_item(a, "new.md", "read after the snapshot")
        self.assertIsNone(a.client.pull().stalled)
        self.assertEqual(a.text("notes", sid)["rows"], ["second, not sent yet"], "its own record in the snapshot is skipped")
        self.assertTrue(a.has("notes", fresh), "the snapshot's cleanup keeps an own item the snapshot lacks")

        a.sync()
        b.sync()
        self.assertEqual(b.text("notes", sid)["rows"], ["second, not sent yet"])
        self.assertEqual(rows(a.db), rows(b.db))

    def test_a_rebuilt_store_takes_them_back_as_its_own(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        sid = ssh_item(a, "remote.md", "kept on the relay")
        a.sync()
        b.sync()
        a.client.close()
        a.client = None
        os.remove(a.store)
        a.open()
        report = a.sync()
        self.assertEqual(a.text("notes", sid)["rows"], ["kept on the relay"])
        self.assertEqual(report.sent, 0, "the rebuilt store knows the item is its own and already sent")


if __name__ == "__main__":
    unittest.main()
