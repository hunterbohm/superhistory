import json
import logging
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from kb import config, ingest, search, store
from kb.sources import Excludes, Gone, Item, Progress, Run
from kb.sources import claude_code
from kb.sources.claude_code import TYPE, Options

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "claude_code", "projects")
SESSION = "3f1c2d4e-0000-4000-8000-00000000aa01"
LOG = logging.getLogger("varve-test-claude-code")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False


def turns(session, prompt, cwd="/work/app", reply="On it."):
    return [
        {"type": "user", "timestamp": "2026-10-18T10:00:00Z", "sessionId": session, "cwd": cwd,
         "message": {"content": prompt}},
        {"type": "assistant", "timestamp": "2026-10-18T10:01:00Z", "sessionId": session,
         "message": {"content": [{"type": "text", "text": reply}]}},
    ]


class ClaudeCodeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        self.root = os.path.join(self.home, ".claude", "projects")
        os.makedirs(self.root)
        patch = mock.patch.dict(os.environ, {"HOME": self.home})
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, rel, records, mtime=None, root=None):
        path = os.path.join(root or self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in records))
        if mtime:
            os.utime(path, (mtime, mtime))
        return path

    def run_type(self, since=None, patterns=(), **opts):
        opts.setdefault("roots", (self.root,))
        return list(TYPE.extract(Run(Options(**opts), Excludes(patterns), since)))

    def items(self, **kw):
        return {e.source_id: e for e in self.run_type(**kw) if isinstance(e, Item)}


class ReadingTest(ClaudeCodeCase):
    def test_a_session_becomes_one_item_with_its_conversation_and_without_noise(self):
        shutil.copytree(FIXTURES, self.root, dirs_exist_ok=True)
        item = self.items()[SESSION]
        self.assertEqual(item.title, "Fix flaky zebra test")
        self.assertEqual(item.ts, "2026-10-18T10:01:02.000Z")
        self.assertEqual(item.meta, {"path": os.path.join(self.root, "-work-app", SESSION + ".jsonl"),
                                     "cwd": "/work/app", "branch": "fix-zebra", "started": "2026-10-18T10:00:00.000Z"})
        self.assertIsInstance(item.mark, float)
        self.assertLess(item.mark, time.time())
        content = item.content
        self.assertTrue(content.startswith("Claude Code session in /work/app (fix-zebra)\n\n"))
        for kept in ("USER: Fix the flaky zebra test\nit fails on CI", "ASSISTANT: Looking at the test now.",
                     "TOOL Bash: pytest -k zebra", "ERROR: AssertionError: zebra stripes",
                     "TOOL Read: /work/app/tests/test_zebra.py", "RESULT: def test_zebra(): pass"):
            self.assertIn(kept, content)
        for dropped in ("REMINDERBODY", "METACAVEAT", "SIDECHAINTHOUGHT", "QUEUEDSECRET"):
            self.assertNotIn(dropped, content)
        self.assertLess(len(content), 2000)

    def test_the_title_is_the_ai_title_then_a_summary_then_the_first_prompt(self):
        self.write("p/a.jsonl", turns("a", "First line of prompt\nsecond line"))
        self.write("p/b.jsonl", [{"type": "summary", "summary": "A summary"}] + turns("b", "prompt b"))
        self.write("p/c.jsonl", [{"type": "summary", "summary": "A summary"},
                                 {"type": "ai-title", "aiTitle": "Named"}] + turns("c", "prompt c"))
        got = self.items()
        self.assertEqual(got["a"].title, "First line of prompt")
        self.assertEqual(got["b"].title, "A summary")
        self.assertEqual(got["c"].title, "Named")

    def test_the_item_id_is_the_session_id_or_else_the_file_name(self):
        records = turns("inner-id", "hello")
        self.write("p/file-name.jsonl", records)
        for r in records:
            r.pop("sessionId", None)
        self.write("p/nameless.jsonl", records)
        self.assertEqual(set(self.items()), {"inner-id", "nameless"})

    def test_a_session_with_only_sidechain_or_meta_records_or_no_prompt_is_skipped(self):
        records = turns("s", "a sidechain prompt")
        for r in records:
            r["isSidechain"] = True
        self.write("p/side.jsonl", records)
        meta = turns("m", "<command-name>/clear</command-name>")
        meta[0]["isMeta"] = True
        self.write("p/meta.jsonl", meta[:1])
        self.write("p/reminder.jsonl", turns("r", "<system-reminder>only a reminder</system-reminder>"))
        self.write("p/empty.jsonl", [{"type": "file-history-snapshot"}])
        self.assertEqual(self.items(), {})

    def test_only_main_session_files_are_read(self):
        self.write("p/real.jsonl", turns("real", "main session"))
        self.write("p/real/subagents/agent-1.jsonl", turns("sub", "subagent transcript"))
        self.write("p/notes.txt", turns("txt", "not a session"))
        self.write("loose.jsonl", turns("loose", "a file outside any project folder"))
        self.assertEqual(set(self.items()), {"real"})

    def test_long_tool_input_and_output_are_clipped_and_odd_records_are_tolerated(self):
        records = turns("c", "go")
        records.insert(1, {"type": "assistant", "message": {"content": [
            "a bare string part",
            {"type": "tool_use", "name": "Custom", "input": {"alpha": 1}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "echo " + "z" * 1000}},
            {"type": "tool_result", "content": "k" * 1000}]}})
        path = self.write("p/c.jsonl", records)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("{not json\n[1, 2]\n")
        content = self.items()["c"].content
        self.assertIn('TOOL Custom: {"alpha": 1}', content)
        self.assertIn("TOOL Bash: echo " + "z" * 100, content)
        self.assertNotIn("z" * 400, content)
        self.assertNotIn("k" * 400, content)
        self.assertIn(" …", content)

    def test_a_tool_input_written_as_json_shows_no_secret(self):
        # An input with no field kb shows is written as JSON, where a line break reads "\\n" and a quote \\".
        records = turns("j", "go")
        records.insert(1, {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__notes__create", "input": {
                "body": 'deploy notes\nghp_' + "A1" * 12 + '\nconfig {"password": "walrus2wolfpack9"}'}}]}})
        self.write("p/j.jsonl", records)
        content = self.items()["j"].content
        self.assertNotIn("A1A1A1", content)
        self.assertNotIn("walrus2wolfpack9", content)
        self.assertIn('TOOL mcp__notes__create: {"body": "deploy notes\\n[masked]', content)

    def test_a_line_separator_inside_a_message_is_read_whole(self):
        records = turns("s1", "first\u2028second\x85third", reply="done\u2029for now")
        path = os.path.join(self.root, "-work-app", "s1.jsonl")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))  # as JavaScript writes it
        content = self.items()["s1"].content
        self.assertIn("first\u2028second\x85third", content)
        self.assertIn("done\u2029for now", content)

    def test_missing_roots_give_no_items(self):
        self.assertEqual(self.items(roots=(os.path.join(self.tmp.name, "nowhere"),)), {})


class AuthCodeTest(ClaudeCodeCase):
    def test_a_turn_that_hands_over_a_code_is_dropped_and_talk_about_codes_stays(self):
        records = turns("s-auth", "Why does the verification code email never arrive on staging?",
                        reply="The mail queue was paused.")
        records.append({"type": "user", "timestamp": "2026-10-18T10:02:00Z", "sessionId": "s-auth",
                        "message": {"content": "Your verification code is 482913."}})
        self.write("proj/s-auth.jsonl", records)
        content = self.items()["s-auth"].content
        self.assertNotIn("482913", content)
        self.assertIn("verification code email never arrive", content)
        self.assertIn("The mail queue was paused.", content)


class RootsTest(ClaudeCodeCase):
    def test_the_default_root_is_the_claude_projects_folder_in_the_home_folder(self):
        self.assertEqual(Options().roots, ("~/.claude/projects",))
        self.write("p/a.jsonl", turns("a", "in the default root"))
        self.assertEqual(set(self.items(roots=Options().roots)), {"a"})

    def test_every_root_in_the_config_is_read(self):
        other = os.path.join(self.tmp.name, "other-config", "projects")
        self.write("p/a.jsonl", turns("a", "first root"))
        self.write("q/b.jsonl", turns("b", "second root"), root=other)
        self.assertEqual(set(self.items(roots=(self.root, other))), {"a", "b"})


class ChangeTrackingTest(ClaudeCodeCase):
    def setUp(self):
        super().setUp()
        old = time.time() - 3600
        self.write("p/keep.jsonl", turns("keep", "keep me"), mtime=old)
        self.write("p/other.jsonl", turns("other", "another one"), mtime=old)

    def test_a_rerun_from_the_watermark_sees_nothing_new(self):
        events = self.run_type()
        mark = max(e.mark for e in events)
        self.assertEqual({e.source_id for e in events if isinstance(e, Item)}, {"keep", "other"})
        self.assertEqual(self.items(since=mark), {})

    def test_a_session_that_grew_after_the_watermark_is_read_again(self):
        mark = max(e.mark for e in self.run_type())
        path = os.path.join(self.root, "p", "keep.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(turns("keep", "x", reply="a late addition")[1]) + "\n")
        got = self.items(since=mark)
        self.assertEqual(set(got), {"keep"})
        self.assertIn("a late addition", got["keep"].content)

    def test_a_run_always_ends_with_progress_so_the_watermark_moves_when_nothing_is_stored(self):
        events = self.run_type(patterns=["/work/app"])
        self.assertTrue(all(not isinstance(e, Item) for e in events))
        self.assertIsInstance(events[-1], Progress)
        self.assertIsInstance(events[-1].mark, float)


class RacesTest(ClaudeCodeCase):
    def setUp(self):
        super().setUp()
        self.write("p/keep.jsonl", turns("keep", "keep me"))
        self.write("p/gone.jsonl", turns("gone", "deleted mid-scan"))

    def test_a_session_deleted_between_listing_and_opening_is_skipped(self):
        real_open = open

        def open_after_cleanup(path, *args, **kwargs):
            if str(path).endswith("gone.jsonl"):
                os.remove(path)
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", open_after_cleanup):
            self.assertEqual(set(self.items()), {"keep"})

    def test_a_session_deleted_between_listing_and_stat_is_skipped(self):
        project = os.path.join(self.root, "p")
        real_scandir = os.scandir

        def scandir(path):
            entries = list(real_scandir(path))
            if path == project:
                os.remove(os.path.join(project, "gone.jsonl"))
            return iter(entries)

        with mock.patch("os.scandir", scandir):
            self.assertEqual(set(self.items(since=1.0)), {"keep"})


class ExcludeTest(ClaudeCodeCase):
    def setUp(self):
        super().setUp()
        self.write("p/private.jsonl", turns("private", "secret plan", cwd=self.home + "/code/private-client/api"))
        self.write("p/near.jsonl", turns("near", "near miss", cwd=self.home + "/code/private-client-2"))
        self.write("p/scratch.jsonl", turns("scratch", "throwaway", cwd=self.home + "/work/scratch/x"))
        self.write("p/outside.jsonl", turns("outside", "elsewhere", cwd="/srv/private-client"))
        self.write("p/home.jsonl", turns("home", "at home", cwd=self.home))
        self.write("p/nocwd.jsonl", [{"type": "user", "timestamp": "2026-10-18T10:00:00Z", "sessionId": "nocwd",
                                      "message": {"content": "no folder known"}}])

    def test_with_no_excludes_every_session_is_read(self):
        self.assertEqual(set(self.items()), {"private", "near", "scratch", "outside", "home", "nocwd"})

    def test_a_path_pattern_starts_at_the_home_folder_written_as_a_tilde(self):
        got = set(self.items(patterns=["~/code/private-client"]))
        self.assertEqual(got, {"near", "scratch", "outside", "home", "nocwd"})

    def test_a_name_pattern_matches_a_folder_name_anywhere_in_the_working_folder(self):
        self.assertEqual(set(self.items(patterns=["scratch"])), {"private", "near", "outside", "home", "nocwd"})

    def test_a_working_folder_outside_the_home_folder_is_keyed_by_its_full_path(self):
        self.assertEqual(set(self.items(patterns=["/srv/private-client"])),
                         {"private", "near", "scratch", "home", "nocwd"})

    def test_the_home_folder_itself_is_keyed_as_a_tilde_so_a_tilde_pattern_covers_everything_under_it(self):
        self.assertEqual(set(self.items(patterns=["~"])), {"outside", "nocwd"})

    def test_a_session_without_a_working_folder_has_the_empty_key(self):
        self.assertEqual(set(self.items(patterns=["*"])), {"nocwd"})

    def test_an_excluded_session_is_reported_gone_so_an_earlier_copy_is_removed(self):
        events = self.run_type(patterns=["scratch"])
        self.assertIn(Gone("scratch", events[-1].mark), events)
        self.assertEqual([e for e in events if isinstance(e, Gone)], [Gone("scratch", events[-1].mark)])


class RunnerTest(ClaudeCodeCase):
    def setUp(self):
        super().setUp()
        shutil.copytree(FIXTURES, self.root, dirs_exist_ok=True)
        self.write("q/other.jsonl", turns("other", "Plan the giraffe feeder", cwd=self.home + "/code/private-client"))
        self.db = store.connect(os.path.join(self.tmp.name, "kb.sqlite"))
        self.addCleanup(self.db.close)

    def ingest(self, extra=""):
        cfg = config.parse('machine = "m1"\n[sources.claude_code]\nroots = [%s]\n%s' % (json.dumps(self.root), extra))
        self.assertEqual(ingest.run(self.db, cfg, LOG, with_summaries=False), 0)

    def stored(self):
        return {r[0] for r in self.db.execute("SELECT source_id FROM docs WHERE source='claude_code'")}

    def test_sessions_are_stored_and_found_by_search(self):
        self.ingest()
        self.assertEqual(self.stored(), {"m1:" + SESSION, "m1:other"})
        hits = search.search(self.db, "zebra stripes")
        self.assertEqual([h["source_id"] for h in hits], ["m1:" + SESSION])
        self.assertEqual(hits[0]["meta"]["cwd"], "/work/app")
        self.assertEqual(hits[0]["meta"]["host"], "m1")
        self.assertEqual(search.search(self.db, "REMINDERBODY"), [])

    def test_the_config_excludes_keep_a_working_folder_out_and_remove_what_was_stored(self):
        self.ingest()
        self.assertIn("m1:other", self.stored())
        time.sleep(0.05)
        os.utime(os.path.join(self.root, "q", "other.jsonl"))
        self.ingest('exclude = ["~/code/private-client"]\n')
        self.assertEqual(self.stored(), {"m1:" + SESSION})
        self.assertEqual(search.search(self.db, "giraffe"), [])

    def test_running_twice_leaves_the_same_rows(self):
        self.ingest()
        before = self.db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        self.ingest()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM docs").fetchone()[0], before)
