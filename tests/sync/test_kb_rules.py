"""Where Sync meets kb's rules for an id. A record leaves only if kb's own `store.secret_id` also passes its item, and a
record kb's store refuses on write is skipped like any bad record."""
import random
import unittest
from unittest import mock

from kb import store
from kb.sync import records

from .support import World

ITEM = {"ts": "2026-10-01", "title": "Note", "meta": {"hash": "h"}, "rows": ["made-up text"]}


class KbRulesTest(unittest.TestCase):
    def setUp(self):
        self.world = World()
        (self.a, self.b), _ = self.world.group("computer 1", "computer 2")

    def tearDown(self):
        self.world.close()

    def test_an_id_kb_refuses_does_not_leave_even_when_the_client_would_pass_it(self):
        kept = self.a.item("notes", "kept", "made-up text")
        held = self.a.item("notes", "held", "made-up text")
        self.assertFalse(records._ids_hold_secret(records.Record("item", "notes", held, 0, ITEM)), "the client's own rule passes it")
        real = store.secret_id

        def refuse_held(source, source_id, names=()):
            return source_id == held or real(source, source_id, names)

        with mock.patch.object(store, "secret_id", refuse_held):
            out = records.outgoing_record(records.Record("item", "notes", held, 0, self.a.text("notes", held)), self.a.db)
            self.assertIsNone(out)
            ok = records.outgoing_record(records.Record("item", "notes", kept, 0, self.a.text("notes", kept)), self.a.db)
            self.assertIsNotNone(ok)
            self.assertNotIn(("item", "notes", held, ""), records._own_digests(self.a.db, self.a.prefix))
            self.assertIn(("item", "notes", kept, ""), records._own_digests(self.a.db, self.a.prefix))
            self.assertIsNone(self.a.sync().stalled)
        self.b.sync()
        self.assertTrue(self.b.has("notes", kept))
        self.assertFalse(self.b.has("notes", held), "the item left although kb refuses its id")

    def test_no_id_found_that_the_client_passes_and_kb_refuses(self):
        """A seeded search, not a proof: shapes of id the mask has rules for, with and without an install prefix, a host
        and a profile name, joined at random."""
        pieces = ["password=", "token:", "secret=", "API_KEY=", "Authorization: ", "Basic ", "Bearer ", "cookie: ",
                  "tiger7xpw", "Pw7abcDEF12345xyz", "ghp_" + "A" * 36, "sk-ant-api03-" + "B" * 40, "dXNlcjp0aWdlcjd4cHc=",
                  "https://u:pw12345@h.example/", "postgres://:pw12345@h/", "u:p@", "AKIA" + "C" * 16, "x", "/", ".", "-",
                  " ", "​", "note.md", "2026Q4 plan", "the password is changed"]
        rnd = random.Random(11)
        own = self.a.prefix
        for _ in range(4000):
            rest = "".join(rnd.choice(pieces) for _ in range(rnd.randrange(1, 6)))
            host, profile = rnd.choice([None, "tokenbox", "laptop", "password"]), rnd.choice([None, "secret-project", "pp"])
            form = rnd.choice(["{o}:{r}", "{o}@{h}:{r}", "{o}:{p}:{r}", "{h}:{r}", "{h}:{p}:{r}", "{r}"])
            source_id = form.format(o=own, r=rest, h=host or "box", p=profile or "pp")
            if store.secret_id("notes", source_id, (host, profile)):
                self.assertTrue(records.id_holds_secret(source_id, host), repr(source_id))

    def test_a_record_kb_refuses_to_write_is_skipped_by_kbs_own_store(self):
        secret_id = "c-bbbbbbbbbbbbb:ghp_" + "A" * 36
        recs = [records.Record("item", "notes", "c-bbbbbbbbbbbbb:fine", 1, ITEM),
                records.Record("item", "notes", secret_id, 1, ITEM)]
        with mock.patch.object(records, "_ids_hold_secret", lambda rec: False):  # reach kb's refusal itself
            self.assertEqual(records.apply(self.a.db, recs, self.a.prefix), 1)
        self.assertEqual([r[0] for r in self.a.db.execute("SELECT DISTINCT source_id FROM docs")], ["c-bbbbbbbbbbbbb:fine"])


if __name__ == "__main__":
    unittest.main()
