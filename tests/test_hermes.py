import json
import logging
import os
import sqlite3
import tempfile
import time
import unittest

from kb import config, ingest, search, store
from kb.sources import Excludes, Item, Progress, Run
from kb.sources import hermes

LOG = logging.getLogger("varve-test-hermes")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False

SCHEMA = """
CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT, started_at REAL NOT NULL,
  origin_json TEXT, display_name TEXT);
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL,
  content TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL NOT NULL, display_kind TEXT,
  _compressed_summary INTEGER NOT NULL DEFAULT 0);
"""

SKILL_BODY = ('[IMPORTANT: The user has invoked the "portal-intake" skill, indicating they want you to follow it.]'
              '\n\n---\nname: portal-intake\n' + "SKILL TEXT " * 50)
CRON_PREAMBLE = ("[IMPORTANT: You are running as a scheduled cron job. DELIVERY: automatic. SILENT: reply [SILENT] "
                 "to suppress. RECURSION: do not schedule another job.]\n\n")


def make_store(path, sessions):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    for sid, source, title, started, msgs in sessions:
        db.execute("INSERT INTO sessions(id, source, title, started_at) VALUES (?,?,?,?)", (sid, source, title, started))
        for i, (role, content, extra) in enumerate(msgs):
            db.execute(
                "INSERT INTO messages(session_id, role, content, tool_calls, tool_name, timestamp, display_kind, "
                "_compressed_summary) VALUES (?,?,?,?,?,?,?,?)",
                (sid, role, content, extra.get("calls"), extra.get("tool"), started + i + 1, extra.get("kind"),
                 extra.get("summary", 0)))
    db.commit()
    db.close()


def tool_call(name, **args):
    return json.dumps([{"id": "t1", "function": {"name": name, "arguments": json.dumps(args)}}])


def chat(question, answer):
    return [("user", question, {}), ("assistant", answer, {})]


class HermesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "hermes")

    def events(self, since=None, excludes=("tool",), root=None):
        run = Run(hermes.Options(root or self.root), Excludes(excludes), since)
        return list(hermes.extract(run))

    def items(self, **kw):
        return {e.source_id: e for e in self.events(**kw) if isinstance(e, Item)}


class FullTest(HermesCase):
    def setUp(self):
        super().setUp()
        make_store(os.path.join(self.root, "state.db"), [
            ("disc1", "discord", "Season one on the media server", 1000.0, [
                ("user", "[Triggering message id: `99` — use as `message_id` for reply.]\n\n[Sam] get season one", {}),
                ("assistant", "", {"calls": tool_call("terminal", command="ls /media/shows")}),
                ("tool", json.dumps({"output": "Family Show S01"}), {"tool": "terminal"}),
                ("assistant", "Found it, queueing now.", {}),
                ("user", "[System note: Your previous turn was interrupted by a gateway shutdown.]", {}),
                ("assistant", "[CONTEXT COMPACTION — REFERENCE ONLY] summary of earlier turns", {"summary": 1}),
                ("user", SKILL_BODY, {}),
                ("tool", json.dumps({"success": True, "name": "x", "content": "SKILL DUMP"}), {"tool": "skill_view"}),
            ]),
            ("bb1", "bluebubbles", None, 2000.0, chat("are you up", "Yes, here.")),
            ("cron_abc123_20261018_100000", "cron", None, 3000.0, [
                ("user", CRON_PREAMBLE + "Check the unanswered chats.", {}),
                ("assistant", "[SILENT]", {}),
            ]),
            ("cron_def456_20261018_110000", "cron", None, 3500.0, [
                ("user", CRON_PREAMBLE + "Summarize the overnight webhook failures.", {}),
                ("assistant", "Two failures: the calendar sync timed out.", {}),
            ]),
            ("smoke", "tool", None, 4000.0, chat("Reply exactly CHILD_OK", "CHILD_OK")),
        ])
        make_store(os.path.join(self.root, "profiles", "research", "state.db"), [
            ("res1", "oneshot", None, 5000.0, [
                ("user", "Find how texting agents keep replies short", {}),
                ("assistant", "Here is what I found.", {"kind": None}),
                ("assistant", "internal note", {"kind": "internal_notification"}),
            ]),
        ])

    def test_a_line_separator_inside_a_message_is_read_whole(self):
        root = os.path.join(self.tmp.name, "other")
        make_store(os.path.join(root, "state.db"), [("s", "cli", None, 1000.0, chat("one\u2028two\x85three", "ok"))])
        self.assertIn("one\u2028two\x85three", self.items(root=root)["default:s"].content)

    def test_fields_and_one_item_per_session_across_profiles(self):
        items = self.items()
        self.assertEqual(set(items), {"default:disc1", "default:bb1", "default:cron_def456_20261018_110000",
                                      "research:res1"})
        item = items["default:disc1"]
        self.assertRegex(item.ts, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(item.title, "[default/discord] Season one on the media server")
        self.assertEqual(item.meta["path"], os.path.join(self.root, "state.db") + "#disc1")
        self.assertEqual(items["research:res1"].meta["profile"], "research")
        self.assertEqual(items["research:res1"].meta["path"],
                         os.path.join(self.root, "profiles", "research", "state.db") + "#res1")
        self.assertNotIn("host", item.meta)

    def test_bridged_platforms_are_labelled_imessage_and_cron_jobs_named_in_the_header(self):
        items = self.items()
        self.assertIn("[default/imessage]", items["default:bb1"].title)
        self.assertEqual(items["default:bb1"].meta["platform"], "bluebubbles")
        self.assertIn("job def456", items["default:cron_def456_20261018_110000"].content.splitlines()[0])

    def test_conversation_kept_and_tool_calls_rendered(self):
        content = self.items()["default:disc1"].content
        self.assertIn("USER: [Sam] get season one", content)
        self.assertIn("TOOL terminal: ls /media/shows", content)
        self.assertIn("RESULT: Family Show S01", content)
        self.assertIn("ASSISTANT: Found it, queueing now.", content)

    def test_injections_and_noise_dropped(self):
        items = self.items()
        content = items["default:disc1"].content
        for noise in ("Triggering message id", "System note", "CONTEXT COMPACTION", "SKILL TEXT", "SKILL DUMP"):
            self.assertNotIn(noise, content)
        self.assertIn("[skill portal-intake]", content)
        cron = items["default:cron_def456_20261018_110000"].content
        self.assertNotIn("scheduled cron job", cron)
        self.assertIn("Summarize the overnight webhook failures.", cron)
        self.assertNotIn("internal note", items["research:res1"].content)

    def test_silent_cron_sessions_are_skipped(self):
        self.assertNotIn("default:cron_abc123_20261018_100000", self.items())

    def test_the_default_exclude_drops_the_synthetic_tool_platform(self):
        self.assertNotIn("default:smoke", self.items())
        self.assertIn("default:smoke", self.items(excludes=()))

    def test_an_exclude_names_a_profile_and_a_platform(self):
        self.assertEqual(set(self.items(excludes=("research", "tool"))),
                         {"default:disc1", "default:bb1", "default:cron_def456_20261018_110000"})
        self.assertNotIn("default:disc1", self.items(excludes=("default/discord", "tool")))
        self.assertIn("research:res1", self.items(excludes=("default/discord", "tool")))
        self.assertEqual(set(self.items(excludes=("*/oneshot", "cron", "tool"))), {"default:disc1", "default:bb1"})

    def test_a_bridged_platform_is_excluded_by_either_name(self):
        self.assertNotIn("default:bb1", self.items(excludes=("imessage",)))
        self.assertNotIn("default:bb1", self.items(excludes=("bluebubbles",)))

    def test_an_excluded_profile_is_never_opened(self):
        with open(os.path.join(self.root, "profiles", "research", "state.db"), "wb") as fh:
            fh.write(b"this is not a database")
        self.assertIn("default:disc1", self.items(excludes=("research",)))
        with self.assertRaises(sqlite3.DatabaseError):
            self.items()

    def test_the_mark_is_a_time_a_minute_back_and_the_run_ends_with_progress(self):
        before = time.time()
        events = self.events()
        marks = {e.mark for e in events}
        self.assertEqual(len(marks), 1)
        mark = marks.pop()
        self.assertLessEqual(mark, time.time() - 59)
        self.assertGreaterEqual(mark, before - 61)
        self.assertIsInstance(events[-1], Progress)

    def test_a_rerun_with_the_watermark_returns_only_changed_sessions(self):
        mark = max(e.mark for e in self.events())
        self.assertEqual(self.items(since=mark), {})
        db = sqlite3.connect(os.path.join(self.root, "profiles", "research", "state.db"))
        db.execute("INSERT INTO messages(session_id, role, content, timestamp) VALUES ('res1','user','one more',?)",
                   (mark + 5,))
        db.commit()
        db.close()
        again = self.items(since=mark)
        self.assertEqual(set(again), {"research:res1"})
        self.assertIn("USER: one more", again["research:res1"].content)

    def test_an_absent_root_or_database_yields_only_progress(self):
        self.assertEqual([type(e) for e in self.events(root=os.path.join(self.tmp.name, "nowhere"))], [Progress])

    def test_the_databases_are_opened_read_only(self):
        path = os.path.join(self.root, "state.db")
        before = (os.stat(path).st_mtime_ns, os.listdir(self.root))
        self.items()
        self.assertEqual((os.stat(path).st_mtime_ns, os.listdir(self.root)), before)
        self.assertEqual(
            sqlite3.connect(f"file:{path}?mode=ro", uri=True).execute("SELECT count(*) FROM sessions").fetchone()[0], 5)

    def test_a_home_relative_root_is_expanded(self):
        old = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp.name
        try:
            self.assertIn("default:disc1", self.items(root="~/hermes"))
        finally:
            if old is None:
                del os.environ["HOME"]
            else:
                os.environ["HOME"] = old


class WrapperTest(HermesCase):
    def text(self, prompt, role="user"):
        return hermes.clean_text(prompt, role)

    def test_a_sender_tag_stays_on_plain_text_and_goes_before_another_injected_block(self):
        self.assertEqual(self.text("[Sam] get season one"), "[Sam] get season one")
        self.assertEqual(self.text("[Sam] [Triggering message id: `4`]\n\nplease retry"), "please retry")

    def test_a_tag_before_an_injection_marker_does_not_hide_the_marker(self):
        self.assertEqual(self.text("[System note: gateway restarted] [more]"), "")

    def test_out_of_band_messages_are_unwrapped(self):
        wrapped = "[OUT-OF-BAND USER MESSAGE sent mid-turn]\nstop and check the oven\n[/OUT-OF-BAND USER MESSAGE]"
        self.assertEqual(self.text(wrapped), "stop and check the oven")

    def test_long_user_text_is_clipped_and_batches_are_clipped_harder(self):
        self.assertLessEqual(len(self.text("x" * 5000)), hermes.USER_CHARS + 2)
        self.assertLessEqual(len(self.text("## Script Output\n" + "y " * 2000)), hermes.BATCH_CHARS + 2)

    def test_an_injected_prefix_is_dropped_only_for_the_harness_text(self):
        for injected in ("[CONTEXT COMPACTION — REFERENCE ONLY] x", "[SILENT]", "Operation interrupted", "∅"):
            self.assertEqual(self.text(injected, "assistant"), "")
        self.assertEqual(self.text("Operation went fine", "assistant"), "Operation went fine")


class NoiseTest(HermesCase):
    def test_auth_messages_drop_one_at_a_time_and_the_conversation_stays(self):
        make_store(os.path.join(self.root, "state.db"), [
            ("s1", "discord", None, 1000.0, [
                ("user", "log in to the portal", {}),
                ("assistant", "Your verification code is 482913, enter it now.", {}),
                ("tool", json.dumps({"output": "Your one-time code is 771204"}), {"tool": "terminal"}),
                ("user", "done", {}),
                ("assistant", "Logged in.", {}),
            ]),
            ("s2", "discord", None, 2000.0, chat("reset your password please", "Password reset email sent.")),
        ])
        items = self.items()
        self.assertEqual(set(items), {"default:s1"})
        content = items["default:s1"].content
        self.assertNotIn("482913", content)
        self.assertNotIn("771204", content)
        self.assertIn("ASSISTANT: Logged in.", content)
        self.assertIn("USER: done", content)


class CompactionTest(HermesCase):
    """Compaction keeps summarized originals (active=0, compacted=1) and clones the recent tail under new ids."""

    SCHEMA = SCHEMA.replace("origin_json TEXT, display_name TEXT)", "origin_json TEXT, display_name TEXT, last_activity_at REAL)"
                            ).replace("_compressed_summary INTEGER NOT NULL DEFAULT 0)",
                                      "_compressed_summary INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, "
                                      "compacted INTEGER NOT NULL DEFAULT 0)")

    def setUp(self):
        super().setUp()
        os.makedirs(self.root)
        db = sqlite3.connect(os.path.join(self.root, "state.db"))
        db.executescript(self.SCHEMA)
        db.execute("INSERT INTO sessions(id, source, title, started_at, last_activity_at) VALUES ('c1','discord','t',1000,?)",
                   (9000.0,))
        rows = [
            ("user", "first question", 1001, 0, 1), ("assistant", "first answer", 1002, 0, 1),
            ("user", "recent question", 1003, 0, 0), ("assistant", "recent answer", 1004, 0, 0),
            ("user", "recent question", 1003, 1, 0), ("assistant", "recent answer", 1004, 1, 0),
            ("user", "rewound edit", 1005, 0, 0),
        ]
        for role, content, ts, active, compacted in rows:
            db.execute("INSERT INTO messages(session_id, role, content, timestamp, active, compacted) "
                       "VALUES ('c1',?,?,?,?,?)", (role, content, ts, active, compacted))
        db.commit()
        db.close()

    def test_clones_and_rewound_rows_collapse_but_summarized_originals_stay(self):
        content = self.items()["default:c1"].content
        self.assertEqual(content.count("USER: recent question"), 1)
        self.assertEqual(content.count("ASSISTANT: recent answer"), 1)
        self.assertEqual(content.count("USER: first question"), 1)
        self.assertNotIn("rewound edit", content)

    def test_session_with_new_activity_but_old_message_timestamps_is_selected(self):
        self.assertEqual(len(self.items(since=8000.0)), 1)
        self.assertEqual(self.items(since=9500.0), {})

    def test_old_schema_without_the_newer_columns_still_renders(self):
        make_store(os.path.join(self.root, "profiles", "old", "state.db"),
                   [("o1", "discord", None, 100.0, chat("hi", "hello"))])
        self.assertEqual(set(self.items()), {"default:c1", "old:o1"})
        self.assertEqual(set(self.items(since=50.0)), {"default:c1", "old:o1"})
        self.assertEqual(set(self.items(since=150.0)), {"default:c1"})


class IngestHermesTest(HermesCase):
    def parse(self, extra=""):
        return config.parse(f'machine = "m1"\n[sources.agent]\ntype = "hermes"\nroot = {json.dumps(self.root)}\n{extra}')

    def database(self):
        db = store.connect(os.path.join(self.tmp.name, "kb.sqlite"))
        self.addCleanup(db.close)
        return db

    def test_sessions_are_stored_and_found_by_search_and_a_rerun_adds_only_new_ones(self):
        make_store(os.path.join(self.root, "state.db"), [
            ("s1", "discord", "Aquarium plan", 1000.0, chat("plan the aquarium filter", "Use a canister filter.")),
            ("smoke", "tool", None, 1100.0, chat("Reply exactly CHILD_OK narwhal", "CHILD_OK narwhal")),
        ])
        cfg = self.parse()
        db = self.database()
        self.assertEqual(ingest.run(db, cfg, LOG, with_summaries=False), 0)
        hits = search.search(db, "canister", sources=("agent",))
        self.assertEqual([h["source_id"] for h in hits], ["m1:default:s1"])
        self.assertEqual(hits[0]["meta"]["host"], "m1")
        self.assertEqual(search.search(db, "narwhal", sources=("agent",)), [])
        make_store(os.path.join(self.root, "profiles", "work", "state.db"),
                   [("w1", "slack", None, time.time() + 600, chat("draft the standup", "Standup drafted: lemur."))])
        self.assertEqual(ingest.run(db, cfg, LOG, with_summaries=False), 0)
        self.assertEqual([h["source_id"] for h in search.search(db, "lemur", sources=("agent",))], ["m1:work:w1"])

    def test_a_user_exclude_adds_to_the_default(self):
        make_store(os.path.join(self.root, "state.db"), [
            ("s1", "discord", None, 1000.0, chat("tell me about walrus tusks", "They are long.")),
            ("s2", "slack", None, 1000.0, chat("tell me about walrus whiskers", "They are sensitive.")),
        ])
        db = self.database()
        self.assertEqual(ingest.run(db, self.parse('exclude = ["default/slack"]'), LOG, with_summaries=False), 0)
        self.assertEqual([h["source_id"] for h in search.search(db, "walrus", sources=("agent",))], ["m1:default:s1"])


if __name__ == "__main__":
    unittest.main()
