import json
import logging
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from kb import config, ingest, search, store
from kb.sources import Excludes

LOG = logging.getLogger("superhistory-test-notes")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False

MEETING = "---\ntype: meeting\nclient: acme\n---\n# Sync\n\n## Transcript\n\nAda: ship the zebrafish report.\n"


def write(root, rel, text="note", mtime=None):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


def parse_source(path, **keys):
    lines = ['machine = "m1"', "[sources.notes]", f"path = {json.dumps(path)}"]
    lines += [f"{key} = {json.dumps(value)}" for key, value in keys.items()]
    return config.parse("\n".join(lines) + "\n").sources[0]


def iso_local(*args):
    return datetime(*args).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ExcludesTest(unittest.TestCase):
    def test_a_pattern_without_a_slash_matches_any_one_part_at_any_depth(self):
        skip = Excludes(["node_modules", ".*", "*.sync-conflict-*"])
        self.assertTrue(skip("node_modules"))
        self.assertTrue(skip("a/b/node_modules/pkg/readme.md"))
        self.assertTrue(skip(".git/config"))
        self.assertTrue(skip("a/.hidden/x.md"))
        self.assertTrue(skip("a/plan.sync-conflict-20260101-abc.md"))
        self.assertFalse(skip("a/plan.md"))
        self.assertFalse(skip("my_node_modules/x.md"))

    def test_a_part_must_match_whole_and_case_counts(self):
        skip = Excludes(["Journal"])
        self.assertFalse(skip("Journals/x.md"))
        self.assertFalse(skip("My Journal/x.md"))
        self.assertFalse(skip("journal/x.md"))
        self.assertTrue(skip("Work/Journal/x.md"))

    def test_a_pattern_with_a_slash_matches_from_the_start_of_the_key(self):
        skip = Excludes(["Journal/2025"])
        self.assertTrue(skip("Journal/2025"))
        self.assertTrue(skip("Journal/2025/jan.md"))
        self.assertFalse(skip("Journal/2024/jan.md"))
        self.assertFalse(skip("Journal/2025x/jan.md"))
        self.assertFalse(skip("Work/Journal/2025/jan.md"))
        self.assertFalse(skip("Journal/jan.md"))

    def test_a_star_stays_inside_one_part_and_a_question_mark_is_one_character(self):
        skip = Excludes(["Journal/*/draft", "Notes/202?"])
        self.assertTrue(skip("Journal/2025/draft/a.md"))
        self.assertFalse(skip("Journal/2025/jan/draft/a.md"))
        self.assertFalse(skip("Journal/draft/a.md"))
        self.assertTrue(skip("Notes/2025/a.md"))
        self.assertFalse(skip("Notes/20255/a.md"))

    def test_a_double_star_spans_any_number_of_parts(self):
        skip = Excludes(["Archive/**/old", "Inbox/**"])
        self.assertTrue(skip("Archive/old/a.md"))
        self.assertTrue(skip("Archive/2020/old/a.md"))
        self.assertTrue(skip("Archive/2020/q1/old/a.md"))
        self.assertFalse(skip("Archive/2020/new/a.md"))
        self.assertFalse(skip("Other/Archive/old/a.md"))
        self.assertTrue(skip("Inbox/a/b/c.md"))
        self.assertFalse(skip("Other/Inbox/a.md"))

    def test_no_patterns_exclude_nothing(self):
        skip = Excludes([])
        self.assertFalse(skip("anything/at/all.md"))
        self.assertFalse(skip(""))


class NotesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "Field Notes")
        os.makedirs(self.root)

    def items(self, since=None, **keys):
        source = parse_source(self.root, **keys)
        return {i.source_id: i for i in ingest.LocalReader().events(source, since)}

    def live(self, **keys):
        return ingest.LocalReader().live_ids(parse_source(self.root, **keys))

    def database(self):
        db = store.connect(os.path.join(self.tmp.name, "superhistory.sqlite"))
        self.addCleanup(db.close)
        return db

    def stored(self, db):
        return {r[0] for r in db.execute("SELECT source_id FROM docs WHERE source='notes'")}


class ReadingTest(NotesCase):
    def test_item_fields_title_header_and_pointers(self):
        rel = "Projects/Meetings/2026-10-09 1500 Weekly Sync.md"
        write(self.root, rel, MEETING)
        item = self.items()[rel]
        self.assertEqual(item.title, "2026-10-09 1500 Weekly Sync")
        self.assertTrue(item.content.startswith("Field Notes/Projects/Meetings\n\n"))
        self.assertIn("client: acme", item.content)
        self.assertIn("zebrafish", item.content)
        self.assertEqual(item.ts, iso_local(2026, 10, 9, 15, 0))
        self.assertEqual(item.meta["path"], os.path.join(self.root, rel))
        self.assertIsInstance(item.mark, float)
        self.assertLess(item.mark, time.time())

    def test_a_note_at_the_top_of_the_folder_has_no_folder_in_its_header(self):
        write(self.root, "inbox.md", "body")
        self.assertEqual(self.items()["inbox.md"].content, "Field Notes\n\nbody")

    def test_the_date_comes_from_the_name_then_the_front_matter_then_the_file_time(self):
        write(self.root, "Daily/2026-10-01.md", "x")
        write(self.root, "Active/a.md", "---\ncreated: 2026-03-04T10:30\n---\nbody")
        write(self.root, "Active/b.md", "---\ndate: 2026-05-06\n---\nbody")
        write(self.root, "Active/c.md", "plain", mtime=1700000000)
        write(self.root, "Active/2026-13-45 not a date.md", "---\ndate: 2026-07-08\n---\nbody")
        got = self.items()
        self.assertEqual(got["Daily/2026-10-01.md"].ts, "2026-10-01T00:00:00Z")
        self.assertEqual(got["Active/a.md"].ts, iso_local(2026, 3, 4, 10, 30))
        self.assertEqual(got["Active/b.md"].ts, "2026-05-06T00:00:00Z")
        self.assertEqual(got["Active/c.md"].ts, "2023-11-14T22:13:20Z")
        self.assertEqual(got["Active/2026-13-45 not a date.md"].ts, "2026-07-08T00:00:00Z")

    def test_extensions_choose_the_files_and_the_title_drops_the_extension(self):
        write(self.root, "a/notes.txt", "plain text")
        write(self.root, "a/plan.md", "markdown")
        write(self.root, "a/data.csv", "1,2")
        self.assertEqual(set(self.items()), {"a/plan.md"})
        got = self.items(extensions=[".md", ".txt"])
        self.assertEqual(set(got), {"a/plan.md", "a/notes.txt"})
        self.assertEqual(got["a/notes.txt"].title, "notes")

    def test_files_that_are_binary_or_too_large_are_skipped_and_odd_encodings_are_read(self):
        write(self.root, "a/binary.md", "head\0tail")
        write(self.root, "a/huge.md", "x" * 1_000_001)
        write(self.root, "a/ok.md", "fine")
        with open(os.path.join(self.root, "a", "latin.md"), "wb") as fh:
            fh.write(b"caf\xe9 au lait")
        got = self.items()
        self.assertEqual(set(got), {"a/ok.md", "a/latin.md"})
        self.assertIn("au lait", got["a/latin.md"].content)
        self.assertNotIn("a/huge.md", self.live())

    def test_symlinks_are_not_followed(self):
        outside = os.path.join(self.tmp.name, "outside")
        write(outside, "secret.md", "not part of the notes")
        write(self.root, "real.md", "real")
        os.symlink(os.path.join(outside, "secret.md"), os.path.join(self.root, "link.md"))
        os.symlink(outside, os.path.join(self.root, "linked folder"))
        self.assertEqual(set(self.items()), {"real.md"})
        self.assertEqual(self.live(), ["real.md"])

    def test_a_home_relative_path_is_expanded(self):
        write(self.tmp.name, "Home Notes/a.md", "x")
        source = parse_source("~/Home Notes")
        with mock.patch.dict(os.environ, {"HOME": self.tmp.name}):
            self.assertEqual([i.source_id for i in ingest.LocalReader().events(source, None)], ["a.md"])


class LinksTest(NotesCase):
    def test_a_line_separator_inside_a_front_matter_value_does_not_start_a_line(self):
        write(self.root, "Active/c.md", "---\nsummary: moved\u2028date: 2019-01-02\ndate: 2026-05-06\n---\nbody")
        write(self.root, "Active/d.md", "---\ndate\u2028: 2019-01-02\ndate: 2026-05-06\n---\nbody")
        got = self.items()
        self.assertEqual(got["Active/c.md"].ts[:10], "2026-05-06")
        self.assertEqual(got["Active/d.md"].ts[:10], "2026-05-06", "U+2028 is part of the key, not space")

    def test_a_folder_with_an_obsidian_config_gets_obsidian_links(self):
        rel = "Projects/Meetings/2026-10-09 1500 Weekly Sync.md"
        write(self.root, rel, MEETING)
        write(self.root, ".obsidian/app.json", "{}")
        self.assertEqual(self.items()[rel].meta["url"],
                         "obsidian://open?vault=Field%20Notes&file=Projects%2FMeetings%2F2026-10-09%201500%20Weekly%20Sync")

    def test_a_plain_folder_gets_only_a_path(self):
        write(self.root, "a.md")
        self.assertNotIn("url", self.items()["a.md"].meta)

    def test_links_can_force_obsidian_links_or_turn_them_off(self):
        write(self.root, "a.md")
        self.assertIn("url", self.items(links="obsidian")["a.md"].meta)
        write(self.root, ".obsidian/app.json", "{}")
        self.assertNotIn("url", self.items(links="file")["a.md"].meta)


class ExcludeConfigTest(NotesCase):
    def tree(self):
        for rel in ("Areas/keep.md", "Journal/day.md", "Work/Journal/day.md", "Archive/2020/old/a.md",
                    "Archive/2020/new/a.md", "Work/Private/x.md", "Other/Work/Private/x.md",
                    ".obsidian/x.md", ".trash/x.md", "Areas/node_modules/p/README.md", "Areas/hidden/.cache/x.md",
                    "Areas/n.sync-conflict-20260101-abc.md", "Areas/readme.txt"):
            write(self.root, rel)

    def test_the_defaults_skip_hidden_folders_dependencies_and_sync_conflicts(self):
        self.tree()
        expected = {"Areas/keep.md", "Journal/day.md", "Work/Journal/day.md", "Archive/2020/old/a.md",
                    "Archive/2020/new/a.md", "Work/Private/x.md", "Other/Work/Private/x.md"}
        self.assertEqual(set(self.items()), expected)
        self.assertEqual(set(self.live()), expected)

    def test_a_users_excludes_add_to_the_defaults_in_reading_and_listing(self):
        self.tree()
        keys = {"exclude": ["Journal", "Archive/**/old", "Work/Private"]}
        expected = {"Areas/keep.md", "Archive/2020/new/a.md", "Other/Work/Private/x.md"}
        self.assertEqual(set(self.items(**keys)), expected)
        self.assertEqual(set(self.live(**keys)), expected)

    def test_dropping_the_defaults_keeps_only_the_users_excludes(self):
        self.tree()
        got = set(self.items(exclude=["Journal"], exclude_defaults=False))
        self.assertIn(".obsidian/x.md", got)
        self.assertIn("Areas/node_modules/p/README.md", got)
        self.assertIn("Areas/n.sync-conflict-20260101-abc.md", got)
        self.assertNotIn("Journal/day.md", got)
        self.assertNotIn("Work/Journal/day.md", got)
        self.assertNotIn("Areas/readme.txt", got)

    def test_an_excluded_folder_is_not_read_even_when_its_notes_are_new(self):
        write(self.root, "Journal/day.md", "private")
        write(self.root, "Areas/a.md", "public")
        self.assertEqual(set(self.items(exclude=["Journal"])), {"Areas/a.md"})


class ChangeTrackingTest(NotesCase):
    def test_a_rerun_with_the_watermark_sees_only_changes(self):
        write(self.root, "Areas/a.md", "one")
        write(self.root, "Areas/b.md", "two")
        self.assertEqual(len(self.items()), 2)
        since = time.time()
        self.assertEqual(self.items(since), {})
        time.sleep(0.05)
        write(self.root, "Areas/a.md", "changed")
        self.assertEqual(set(self.items(since)), {"Areas/a.md"})

    def test_moved_files_keep_their_old_mtime_but_are_seen_again(self):
        old = time.time() - 3600
        write(self.root, "Areas/old dir/a.md", "one", mtime=old)
        write(self.root, "Areas/loose.md", "two", mtime=old)
        since = time.time()
        time.sleep(0.05)
        os.rename(os.path.join(self.root, "Areas/loose.md"), os.path.join(self.root, "Areas/moved.md"))
        os.rename(os.path.join(self.root, "Areas/old dir"), os.path.join(self.root, "Areas/new dir"))
        self.assertEqual(set(self.items(since)), {"Areas/moved.md", "Areas/new dir/a.md"})


class IngestNotesTest(NotesCase):
    def run_unit(self, db):
        ingest.run_unit(db, parse_source(self.root), "m1", ingest.LocalReader(), LOG, "m1:")

    def test_a_renamed_note_is_found_only_under_its_new_name(self):
        write(self.root, "Areas/old name.md", "walrus facts")
        write(self.root, "Areas/stays.md", "other")
        db = self.database()
        self.run_unit(db)
        self.assertEqual(self.stored(db), {"m1:Areas/old name.md", "m1:Areas/stays.md"})
        os.rename(os.path.join(self.root, "Areas/old name.md"), os.path.join(self.root, "Areas/new name.md"))
        self.run_unit(db)
        self.assertEqual(self.stored(db), {"m1:Areas/new name.md", "m1:Areas/stays.md"})
        hits = search.search(db, "walrus", sources=("notes",))
        self.assertEqual([h["source_id"] for h in hits], ["m1:Areas/new name.md"])
        self.assertEqual(hits[0]["meta"]["host"], "m1")

    def test_a_deleted_note_leaves_the_database(self):
        write(self.root, "a/one.md", "gecko", mtime=time.time() - 3600)
        write(self.root, "a/two.md", "iguana", mtime=time.time() - 3600)
        db = self.database()
        self.run_unit(db)
        os.remove(os.path.join(self.root, "a/one.md"))
        self.run_unit(db)
        self.assertEqual(self.stored(db), {"m1:a/two.md"})

    def test_a_note_added_to_an_excluded_folder_later_is_not_stored(self):
        write(self.root, "Areas/a.md", "public")
        db = self.database()
        self.run_unit(db)
        write(self.root, ".hidden/b.md", "private")
        self.run_unit(db)
        self.assertEqual(self.stored(db), {"m1:Areas/a.md"})

    def test_an_emptied_folder_prunes_nothing(self):
        write(self.root, "Areas/a.md", "one")
        db = self.database()
        self.run_unit(db)
        os.remove(os.path.join(self.root, "Areas/a.md"))
        self.run_unit(db)
        self.assertEqual(self.stored(db), {"m1:Areas/a.md"})

    @unittest.skipIf(os.geteuid() == 0, "root reads every folder")
    def test_an_unreadable_folder_keeps_its_notes_and_new_notes_are_still_stored(self):
        write(self.root, "Areas/a.md", "one")
        write(self.root, "Clients/b.md", "two")
        db = self.database()
        self.run_unit(db)
        os.chmod(os.path.join(self.root, "Clients"), 0)
        self.addCleanup(os.chmod, os.path.join(self.root, "Clients"), 0o755)
        write(self.root, "Areas/c.md", "three")
        failures = ingest.run(db, config.parse(
            f'machine = "m1"\n[sources.notes]\npath = {json.dumps(self.root)}\n'), LOG)
        self.assertEqual(failures, 1)
        self.assertEqual(self.stored(db), {"m1:Areas/a.md", "m1:Clients/b.md", "m1:Areas/c.md"})
        self.assertEqual([unit for unit, _ in store.failing(db)], ["notes@m1"])


if __name__ == "__main__":
    unittest.main()
