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
from kb.sources.codex import TYPE, Options

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "codex", "sessions")
SID = "019e0000-0000-7000-8000-000000000001"
SEGMENT = "019e0000-0000-7000-8000-0000000000aa"
LOG = logging.getLogger("superhistory-test-codex")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False


def rec(ordinal, kind, payload, ts="2026-06-03T19:34:42.000Z"):
    return json.dumps({"timestamp": ts, "ordinal": ordinal, "type": kind, "payload": payload}, separators=(",", ":"))


def msg(role, text, part="input_text"):
    return {"type": "message", "role": role, "content": [{"type": part, "text": text}]}


def conversation(sid=SID, cwd="/work/app", prompt="Fix the flaky zebra test", source="vscode", extra=None):
    meta = {"id": sid, "timestamp": "2026-06-03T19:34:00.000Z", "cwd": cwd, "source": source}
    meta.update(extra or {})
    return [rec(0, "session_meta", meta), rec(1, "response_item", msg("user", prompt)),
            rec(2, "response_item", msg("assistant", "Looking at it now.", "output_text"))]


class CodexCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        self.sessions = os.path.join(self.home, ".codex", "sessions")
        self.archived = os.path.join(self.home, ".codex", "archived_sessions")
        os.makedirs(self.home)
        patch = mock.patch.dict(os.environ, {"HOME": self.home})
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, rel, lines, mtime=None, root=None):
        path = os.path.join(root or os.path.join(self.home, ".codex"), rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        if mtime:
            os.utime(path, (mtime, mtime))
        return path

    def run_type(self, since=None, patterns=(), **opts):
        opts.setdefault("roots", (self.sessions, self.archived))
        return list(TYPE.extract(Run(Options(**opts), Excludes(patterns), since)))

    def items(self, **kw):
        return {e.source_id: e for e in self.run_type(**kw) if isinstance(e, Item)}

    def only_item(self, **kw):
        got = self.items(**kw)
        self.assertEqual(len(got), 1)
        return next(iter(got.values()))


class ReadingTest(CodexCase):
    def test_a_session_becomes_one_item_with_its_conversation_and_without_noise(self):
        shutil.copytree(FIXTURES, self.sessions)
        item = self.only_item()
        path = os.path.join(self.sessions, "2026", "06", "03", f"rollout-2026-06-03T14-34-00-{SID}.jsonl")
        self.assertEqual(item.source_id, SID)
        self.assertEqual(item.title, "Fix the flaky zebra test")
        self.assertEqual(item.ts, "2026-06-03T19:41:00.000Z")
        self.assertEqual(item.meta, {"path": path, "cwd": "/work/app", "branch": "feature-x",
                                     "started": "2026-06-03T19:34:00.000Z"})
        self.assertIsInstance(item.mark, float)
        self.assertLess(item.mark, time.time())
        content = item.content
        self.assertTrue(content.startswith("Codex session in /work/app (feature-x)"))
        for kept in ("USER: Fix the flaky zebra test", "ASSISTANT: Looking at the test now.",
                     "TOOL exec_command: pytest -k zebra", "ERROR: AssertionError: zebra stripes",
                     "TOOL exec: text(await", "RESULT: Script completed"):
            self.assertIn(kept, content)
        for dropped in ("DEVELOPERSECRET", "AGENTSBODY", "ENVBODY", "ENCRYPTEDBLOB", "BASEPROMPT", "Chunk ID"):
            self.assertNotIn(dropped, content)
        self.assertLess(len(content), 2500)

    def test_a_line_separator_inside_a_message_is_read_whole(self):
        lines = [json.dumps(json.loads(line), ensure_ascii=False, separators=(",", ":"))  # raw, as Codex may write it
                 for line in conversation(prompt="Fix the zebra test\u2028then the\x85lint")]
        self.assertIn("Fix the zebra test\u2028then the\x85lint", self.write_and_read(lines).content)

    def test_a_request_marker_in_the_first_prompt_gives_the_title_its_text(self):
        prompt = "Context the harness added\n## My request for Codex:\n\nRename the giraffe module\nthen test"
        self.assertEqual(self.write_and_read(conversation(prompt=prompt)).title, "Rename the giraffe module")

    def write_and_read(self, lines):
        self.write(f"sessions/2026/06/03/rollout-a-{SID}.jsonl", lines)
        return self.only_item()

    def test_the_title_falls_back_to_an_agent_message_then_the_file_name(self):
        meta = conversation()[0]
        agent = rec(1, "response_item", {"type": "agent_message", "author": "root", "recipient": "worker",
                                         "content": "<task>Survey the aardvark logs</task>"})
        item = self.write_and_read([meta, agent])
        self.assertEqual(item.title, "Survey the aardvark logs")
        self.assertIn("AGENT root -> worker: <task>Survey the aardvark logs</task>", item.content)

    def test_a_session_with_only_tool_calls_is_titled_by_its_file_name(self):
        lines = [conversation()[0], rec(1, "response_item", {"type": "function_call", "name": "shell",
                                                             "arguments": json.dumps({"cmd": "ls"})})]
        self.assertEqual(self.write_and_read(lines).title, f"rollout-a-{SID}.jsonl")

    def test_web_searches_and_other_tool_arguments_are_shown(self):
        lines = conversation() + [
            rec(3, "response_item", {"type": "web_search_call", "action": {"query": "zebra stripe genetics"}}),
            rec(4, "response_item", {"type": "function_call", "name": "apply_patch", "arguments": "{broken"}),
            rec(5, "response_item", {"type": "function_call", "name": "read", "arguments": json.dumps({"path": "a.py"})}),
            rec(6, "response_item", {"type": "function_call", "name": "shell",
                                     "arguments": json.dumps({"command": ["ls", "-la"]})}),
            rec(7, "response_item", {"type": "function_call_output", "call_id": "x",
                                     "output": "Chunk ID: 1\nProcess exited with code 0\nOutput:\nall good"}),
        ]
        content = self.write_and_read(lines).content
        for expected in ("TOOL web_search: zebra stripe genetics", "TOOL apply_patch: {broken", "TOOL read: a.py",
                         "TOOL shell: ls -la", "RESULT: all good"):
            self.assertIn(expected, content)

    def test_tool_arguments_written_as_json_show_no_secret(self):
        # Arguments with no field superhistory shows are written as JSON, where a line break reads "\\n" and a quote \\".
        body = 'deploy notes\nghp_' + "A1" * 12 + '\nconfig {"password": "walrus2wolfpack9"}'
        content = self.write_and_read(conversation() + [
            rec(3, "response_item", {"type": "function_call", "name": "notes", "arguments": json.dumps({"body": body})})
        ]).content
        self.assertNotIn("A1A1A1", content)
        self.assertNotIn("walrus2wolfpack9", content)
        self.assertIn('TOOL notes: {"body": "deploy notes\\n[masked]', content)

    def test_long_tool_input_and_output_are_clipped(self):
        lines = conversation() + [
            rec(3, "response_item", {"type": "function_call", "name": "exec_command",
                                     "arguments": json.dumps({"cmd": "echo " + "z" * 1000})}),
            rec(4, "response_item", {"type": "function_call_output", "call_id": "x", "output": "k" * 1000}),
        ]
        content = self.write_and_read(lines).content
        self.assertNotIn("z" * 400, content)
        self.assertNotIn("k" * 400, content)
        self.assertIn(" …", content)

    def test_noise_records_in_every_known_layout_are_dropped(self):
        noise = [
            rec(3, "response_item", {"type": "reasoning", "encrypted_content": "BLOBONE"}),
            rec(4, "event_msg", {"type": "token_count", "info": {}}),
            rec(5, "turn_context", {"note": "TURNCTX"}),
            rec(6, "world_state", {"note": "WORLDSTATE"}),
            rec(7, "compacted", {"note": "COMPACTEDHISTORY"}),
            rec(8, "token_usage_record", {"note": "USAGE"}),
            rec(9, "inter_agent_communication_metadata", {"note": "COMMS"}),
            rec(10, "response_item", msg("user", "<permissions instructions>PERMSBODY</permissions instructions>")),
            rec(11, "response_item", msg("user", "<skills_instructions>SKILLBODY</skills_instructions>")),
            rec(12, "response_item", msg("user", "<turn_aborted>ABORTED</turn_aborted>")),
            rec(13, "response_item", msg("system", "SYSTEMROLE")),
            "{not json",
            "[1, 2]",
        ]
        content = self.write_and_read(conversation() + noise).content
        for dropped in ("BLOBONE", "TURNCTX", "WORLDSTATE", "COMPACTEDHISTORY", "USAGE", "COMMS", "PERMSBODY",
                        "SKILLBODY", "ABORTED", "SYSTEMROLE"):
            self.assertNotIn(dropped, content)
        self.assertIn("USER: Fix the flaky zebra test", content)

    def test_a_session_without_a_conversation_is_skipped(self):
        self.write(f"sessions/2026/06/03/rollout-a-{SID}.jsonl", conversation()[:1])
        self.assertEqual(self.run_type()[:-1], [])

    def test_a_guardian_review_session_is_skipped(self):
        by_source = conversation(source={"subagent": {"other": "guardian"}})
        self.write(f"sessions/2026/06/03/rollout-a-{SID}.jsonl", by_source)
        other = "019e0000-0000-7000-8000-000000000002"
        self.write(f"sessions/2026/06/03/rollout-b-{other}.jsonl",
                   conversation(sid=other, extra={"thread_source": "guardian_review"}))
        self.assertEqual(self.items(), {})

    def test_missing_roots_and_bad_lines_are_harmless(self):
        self.assertEqual(self.items(), {})
        lines = conversation()
        lines.insert(1, "{not json")
        self.write(f"sessions/2026/06/03/rollout-a-{SID}.jsonl", lines)
        self.assertEqual(set(self.items()), {SID})


class AuthCodeTest(CodexCase):
    def test_a_turn_that_hands_over_a_code_is_dropped_and_talk_about_codes_stays(self):
        lines = conversation(prompt="Why does the verification code email never arrive on staging?")
        lines.append(rec(3, "response_item", msg("user", "Your verification code is 482913.")))
        self.write(f"sessions/2026/06/03/rollout-a-{SID}.jsonl", lines)
        content = self.only_item().content
        self.assertNotIn("482913", content)
        self.assertIn("verification code email never arrive", content)


class SessionShapeTest(CodexCase):
    def test_a_forked_file_drops_the_replayed_parent_history(self):
        parent = "019e0000-0000-7000-8000-0000000000ff"
        lines = [
            rec(0, "session_meta", {"id": SID, "timestamp": "2026-06-03T19:34:00.000Z", "cwd": "/work/app",
                                    "forked_from_id": parent, "subagent_history_start_ordinal": 3}),
            rec(1, "session_meta", {"id": parent, "timestamp": "2026-06-03T19:00:00.000Z", "cwd": "/elsewhere"}),
            rec(2, "response_item", msg("user", "parent request about aardvarks")),
            rec(3, "event_msg", {"type": "thread_settings_applied"}),
            rec(4, "response_item", msg("user", "child task about badgers")),
        ]
        self.write(f"sessions/2026/06/03/rollout-a-{SID}.jsonl", lines)
        item = self.only_item()
        self.assertEqual(item.source_id, SID)
        self.assertEqual(item.title, "child task about badgers")
        self.assertEqual(item.meta["cwd"], "/work/app")
        self.assertNotIn("aardvarks", item.content)

    def test_resumed_segments_become_one_item(self):
        self.write(f"archived_sessions/rollout-2026-06-03T14-34-00-{SID}.jsonl", conversation())
        later = [
            rec(20, "session_meta", {"id": SID, "timestamp": "2026-06-04T09:00:00.000Z", "cwd": "/work/app"}),
            rec(21, "response_item", msg("user", "continue with the giraffe fix"), ts="2026-06-04T09:01:00.000Z"),
        ]
        self.write(f"archived_sessions/rollout-2026-06-04T04-00-00-{SID}_{SEGMENT}.jsonl", later, mtime=time.time() - 3600)
        item = self.only_item()
        self.assertEqual(item.title, "Fix the flaky zebra test")
        self.assertEqual(item.ts, "2026-06-04T09:01:00.000Z")
        self.assertIn("USER: Fix the flaky zebra test", item.content)
        self.assertIn("USER: continue with the giraffe fix", item.content)

    def test_a_file_in_both_folders_counts_once_and_the_newer_copy_wins(self):
        now = time.time()
        self.write(f"sessions/2026/06/03/rollout-a-{SID}.jsonl", conversation(), mtime=now - 100)
        self.write(f"archived_sessions/rollout-a-{SID}.jsonl", conversation(), mtime=now - 10)
        self.assertIn("archived_sessions", self.only_item().meta["path"])

    def test_the_item_id_is_the_session_id_or_else_the_file_name(self):
        lines = conversation()
        lines[0] = rec(0, "session_meta", {"timestamp": "2026-06-03T19:34:00.000Z", "cwd": "/work/app"})
        self.write("sessions/rollout-plain.jsonl", lines)
        self.assertEqual(set(self.items()), {"rollout-plain"})


class RootsTest(CodexCase):
    def test_the_default_roots_are_the_codex_session_folders_in_the_home_folder(self):
        self.assertEqual(Options().roots, ("~/.codex/sessions", "~/.codex/archived_sessions"))
        other = "019e0000-0000-7000-8000-000000000002"
        self.write(f"sessions/2026/06/03/rollout-a-{SID}.jsonl", conversation())
        self.write(f"archived_sessions/rollout-b-{other}.jsonl", conversation(sid=other))
        self.assertEqual(set(self.items(roots=Options().roots)), {SID, other})

    def test_every_root_in_the_config_is_read(self):
        extra = os.path.join(self.tmp.name, "other-codex")
        self.write(f"rollout-a-{SID}.jsonl", conversation(), root=extra)
        self.assertEqual(set(self.items(roots=(extra,))), {SID})


class ChangeTrackingTest(CodexCase):
    def test_a_rerun_from_the_watermark_sees_nothing_new(self):
        self.write(f"sessions/2026/06/03/rollout-a-{SID}.jsonl", conversation(), mtime=time.time() - 3600)
        events = self.run_type()
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(self.items(since=max(e.mark for e in events)), {})

    def test_a_session_that_grew_after_the_watermark_is_read_again(self):
        path = self.write(f"sessions/2026/06/03/rollout-a-{SID}.jsonl", conversation(), mtime=time.time() - 3600)
        mark = max(e.mark for e in self.run_type())
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(rec(14, "response_item", msg("assistant", "late addition", "output_text")) + "\n")
        self.assertIn("late addition", self.only_item(since=mark).content)

    def test_a_new_segment_of_an_old_session_brings_back_the_whole_session(self):
        old = time.time() - 3600
        self.write(f"archived_sessions/rollout-a-{SID}.jsonl", conversation(), mtime=old)
        mark = max(e.mark for e in self.run_type())
        later = [rec(20, "session_meta", {"id": SID, "timestamp": "2026-06-04T09:00:00.000Z", "cwd": "/work/app"}),
                 rec(21, "response_item", msg("user", "and one more thing"))]
        self.write(f"archived_sessions/rollout-b-{SID}_{SEGMENT}.jsonl", later)
        item = self.only_item(since=mark)
        self.assertIn("USER: Fix the flaky zebra test", item.content)
        self.assertIn("USER: and one more thing", item.content)

    def test_a_file_deleted_between_listing_and_opening_is_skipped(self):
        other = "019e0000-0000-7000-8000-000000000002"
        self.write(f"sessions/2026/06/03/rollout-a-{SID}.jsonl", conversation())
        self.write(f"sessions/2026/06/03/rollout-b-{other}.jsonl", conversation(sid=other))
        real_open = open

        def open_after_cleanup(path, *args, **kwargs):
            if str(path).endswith(f"{other}.jsonl"):
                os.remove(path)
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", open_after_cleanup):
            self.assertEqual(set(self.items()), {SID})

    def test_a_run_always_ends_with_progress_so_the_watermark_moves_when_nothing_is_stored(self):
        self.write(f"sessions/2026/06/03/rollout-a-{SID}.jsonl", conversation())
        events = self.run_type(patterns=["/work/app"])
        self.assertTrue(all(not isinstance(e, Item) for e in events))
        self.assertIsInstance(events[-1], Progress)


class ExcludeTest(CodexCase):
    IDS = {name: f"019e0000-0000-7000-8000-00000000010{i}"
           for i, name in enumerate(["private", "near", "scratch", "outside", "home"])}

    def setUp(self):
        super().setUp()
        folders = {"private": self.home + "/code/private-client/api", "near": self.home + "/code/private-client-2",
                   "scratch": self.home + "/work/scratch/x", "outside": "/srv/private-client", "home": self.home}
        for name, cwd in folders.items():
            sid = self.IDS[name]
            self.write(f"sessions/2026/06/03/rollout-{name}-{sid}.jsonl", conversation(sid=sid, cwd=cwd, prompt=name))
        nocwd = "019e0000-0000-7000-8000-000000000199"
        self.IDS["nocwd"] = nocwd
        self.write(f"sessions/2026/06/03/rollout-nocwd-{nocwd}.jsonl",
                   [rec(0, "session_meta", {"id": nocwd, "timestamp": "2026-06-03T19:34:00.000Z"}),
                    rec(1, "response_item", msg("user", "no folder known"))])

    def kept(self, **kw):
        ids = {v: k for k, v in self.IDS.items()}
        return {ids[i] for i in self.items(**kw)}

    def test_with_no_excludes_every_session_is_read(self):
        self.assertEqual(self.kept(), {"private", "near", "scratch", "outside", "home", "nocwd"})

    def test_a_path_pattern_starts_at_the_home_folder_written_as_a_tilde(self):
        self.assertEqual(self.kept(patterns=["~/code/private-client"]), {"near", "scratch", "outside", "home", "nocwd"})

    def test_a_name_pattern_matches_a_folder_name_anywhere_in_the_working_folder(self):
        self.assertEqual(self.kept(patterns=["scratch"]), {"private", "near", "outside", "home", "nocwd"})

    def test_a_working_folder_outside_the_home_folder_is_keyed_by_its_full_path(self):
        self.assertEqual(self.kept(patterns=["/srv/private-client"]), {"private", "near", "scratch", "home", "nocwd"})

    def test_the_home_folder_itself_is_keyed_as_a_tilde_so_a_tilde_pattern_covers_everything_under_it(self):
        self.assertEqual(self.kept(patterns=["~"]), {"outside", "nocwd"})

    def test_a_session_without_a_working_folder_has_the_empty_key(self):
        self.assertEqual(self.kept(patterns=["*"]), {"nocwd"})

    def test_an_excluded_session_is_reported_gone_so_an_earlier_copy_is_removed(self):
        events = self.run_type(patterns=["scratch"])
        self.assertEqual([e for e in events if isinstance(e, Gone)], [Gone(self.IDS["scratch"], events[-1].mark)])


class RunnerTest(CodexCase):
    def setUp(self):
        super().setUp()
        shutil.copytree(FIXTURES, self.sessions)
        other = "019e0000-0000-7000-8000-000000000002"
        self.other = other
        self.write(f"archived_sessions/rollout-b-{other}.jsonl",
                   conversation(sid=other, cwd=self.home + "/code/private-client", prompt="Plan the giraffe feeder"))
        self.db = store.connect(os.path.join(self.tmp.name, "superhistory.sqlite"))
        self.addCleanup(self.db.close)

    def ingest(self, extra=""):
        cfg = config.parse('machine = "m1"\n[sources.codex]\n%s' % extra)
        self.assertEqual(ingest.run(self.db, cfg, LOG, with_summaries=False), 0)

    def stored(self):
        return {r[0] for r in self.db.execute("SELECT source_id FROM docs WHERE source='codex'")}

    def test_sessions_from_the_default_folders_are_stored_and_found_by_search(self):
        self.ingest()
        self.assertEqual(self.stored(), {"m1:" + SID, "m1:" + self.other})
        hits = search.search(self.db, "zebra stripes")
        self.assertEqual([h["source_id"] for h in hits], ["m1:" + SID])
        self.assertEqual(hits[0]["meta"]["cwd"], "/work/app")
        self.assertEqual(hits[0]["meta"]["host"], "m1")
        self.assertEqual(search.search(self.db, "ENCRYPTEDBLOB"), [])

    def test_the_config_excludes_keep_a_working_folder_out_and_remove_what_was_stored(self):
        self.ingest()
        self.assertIn("m1:" + self.other, self.stored())
        time.sleep(0.05)
        os.utime(os.path.join(self.archived, f"rollout-b-{self.other}.jsonl"))
        self.ingest('exclude = ["~/code/private-client"]\n')
        self.assertEqual(self.stored(), {"m1:" + SID})
        self.assertEqual(search.search(self.db, "giraffe"), [])

    def test_running_twice_leaves_the_same_rows(self):
        self.ingest()
        before = self.db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        self.ingest()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM docs").fetchone()[0], before)
