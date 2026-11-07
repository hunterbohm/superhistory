import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("BENCH_BASE_URL", "http://unused.invalid")

import owner
import owner_store
from tools import check as ct, owner as arms

from kb import mcp, store
from kb.paths import Paths


def row(kind, correct, context="", tools=False):
    return {"kind": kind, "correct": correct, "context": context, "records_from_tools": tools}


class HandledTest(unittest.TestCase):
    def test_strict_counts_records_a_tool_call_showed(self):
        quiet_pull = row("none", True, "", True)
        self.assertTrue(owner.handled(quiet_pull))
        self.assertFalse(owner.handled(quiet_pull, strict=True))
        self.assertFalse(owner.handled(row("none", True, "cards")))
        self.assertTrue(owner.handled(row("single", True, "cards", True), strict=True))
        self.assertFalse(owner.handled(row("single", False)))


class NotesTest(unittest.TestCase):
    def test_notes_parse_and_unknown_types_become_facts(self):
        notes = owner_store.parse_notes('prose {"notes": [{"type": "decision", "text": " Use X "}, '
                                        '{"type": "zzz", "text": "Y"}, {"text": ""}]}')
        self.assertEqual(notes, [("decision", "Use X"), ("fact", "Y")])
        with self.assertRaises(owner_store.BadAnswer):
            owner_store.parse_notes('{"notes": []}')

    def test_facts_only_keeps_atoms_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Paths.under(os.path.join(tmp, "base"))
            db = store.connect(base.db)
            store.store_item(db, "codex", "s1", "2026-09-01T10:00:00Z", "t", "pangolin session", {})
            store.replace_rows(db, "codex", "s1", [{"kind": "atom", "ts": "2026-09-01T10:00:00Z", "title": "note: x",
                                                     "content": "pangolin launches Friday",
                                                     "metadata": {"atom": "note"}}], ())
            db.commit()
            db.close()
            cut, stats = owner_store.facts_only(ct.Store(base), os.path.join(tmp, "facts"))
            self.assertEqual(stats["rows"], {"atom": 1})
            tool = arms.Facts()
            tool.store = cut
            text, ids = tool.call("kb_get", {"id": 2})
            self.assertIn("pangolin launches Friday", text)
            self.assertEqual(ids, ["s1"])


class PullToolsTest(unittest.TestCase):
    def test_pull_arms_offer_the_servers_own_descriptions(self):
        names = [t["name"] for t in arms.Pull.agent_tools]
        self.assertEqual(names, [t["name"] for t in mcp.TOOLS])
        self.assertEqual([t["description"] for t in arms.Pull.agent_tools], [t["description"] for t in mcp.TOOLS])
        self.assertEqual([t["name"] for t in arms.PullLookup.agent_tools], names + ["lookup"])
        self.assertEqual(arms.Pull().retrieve({"question": "q"}).context, "")


if __name__ == "__main__":
    unittest.main()
