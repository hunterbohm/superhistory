import json
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from kb import cli, config, ingest, remote, search, store
from kb.sources import Gone, Item, Progress

LOG = logging.getLogger("kb-test")
# Stands in for ssh: records the alias, then runs the program it was sent with an isolated Python from "/", like a
# computer with no copy of kb. Some aliases act out a sleeping or refusing computer.
FAKE_SSH = """#!{python}
import subprocess, sys
args = sys.argv[1:]
while args[0] == "-o":
    args = args[2:]
alias = args[0]
program = sys.stdin.buffer.read()
with open({calls!r}, "a") as calls:
    calls.write(alias + "\\n")
if alias == "asleep":
    sys.stderr.write("ssh: connect to host asleep port 22: Operation timed out\\n")
    sys.exit(255)
if alias == "refused":
    sys.stderr.write("ssh: Permission denied (publickey).\\n")
    sys.exit(255)
out = subprocess.run([{python!r}, "-I", "-"], input=program, stdout=subprocess.PIPE, cwd="/")
listing = b"'verb': 'live_ids'" in program
if alias == "drops" and listing:
    sys.stdout.buffer.write(out.stdout.splitlines(True)[0])
    sys.stderr.write("client_loop: send disconnect: Broken pipe\\n")
    sys.exit(255)
sys.stdout.buffer.write(out.stdout)
if alias == "flaky":
    sys.stderr.write("connection reset by peer\\n")
    sys.exit(3)
sys.exit(out.returncode)
"""


class RemoteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.calls = os.path.join(self.tmp, "calls")
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        with open(os.path.join(bin_dir, "ssh"), "w") as fh:
            fh.write(FAKE_SSH.format(python=sys.executable, calls=self.calls))
        os.chmod(os.path.join(bin_dir, "ssh"), 0o755)
        patcher = mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"]})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.notes = os.path.join(self.tmp, "notes")
        os.makedirs(self.notes)
        self.db = store.connect(os.path.join(self.tmp, "kb.sqlite"))
        self.addCleanup(self.db.close)

    def note(self, rel, text):
        path = os.path.join(self.notes, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)

    def config(self, hosts=("laptop",), aliases=None, extra=""):
        tables = "".join(f'[hosts.{h}]\nssh = "{(aliases or {}).get(h, h)}"\npython = {json.dumps(sys.executable)}\n'
                         for h in hosts)
        return config.parse(f'machine = "desk"\n{tables}[sources.notes]\npath = {json.dumps(self.notes)}\n'
                            f'hosts = {json.dumps(list(hosts))}\n{extra}')

    def ssh_aliases(self):
        with open(self.calls) as fh:
            return fh.read().split()

    def stored_ids(self):
        return sorted(r[0] for r in self.db.execute("SELECT DISTINCT source_id FROM docs"))

    def test_a_source_runs_on_a_computer_with_no_copy_of_kb(self):
        self.note("Projects/pelican.md", "Dana approved the pelican quote.")
        failures = ingest.run(self.db, self.config(), LOG, with_summaries=False)
        self.assertEqual(failures, 0)
        self.assertEqual(self.stored_ids(), ["laptop:Projects/pelican.md"])
        hit = search.search(self.db, "pelican quote")[0]
        self.assertEqual(hit["meta"]["host"], "laptop")
        self.assertEqual(search.pointer(hit["meta"], "desk"), f"laptop:{self.notes}/Projects/pelican.md")
        self.assertIn("notes@laptop", dict(store.succeeded(self.db)))

    def test_the_watermark_reaches_the_remote_run_and_only_changes_come_back(self):
        self.note("a.md", "first")
        ingest.run(self.db, self.config(), LOG, with_summaries=False)
        since = ingest.load_mark(self.db, "notes", "laptop")
        self.assertIsInstance(since, float)
        source = self.config().source("notes")
        events = list(remote.SshReader(self.config().host("laptop")).events(source, since + 120))
        self.assertEqual([e for e in events if isinstance(e, Item)], [])

    def test_excludes_and_options_travel_with_the_request(self):
        self.note("Journal/day.md", "private day")
        self.note("Work/plan.txt", "plan text")
        self.note("Work/plan.md", "plan markdown")
        cfg = self.config(extra='exclude = ["Journal"]\nextensions = [".txt"]\n')
        ingest.run(self.db, cfg, LOG, with_summaries=False)
        self.assertEqual(self.stored_ids(), ["laptop:Work/plan.txt"])

    def test_a_renamed_remote_note_is_found_only_under_its_new_name(self):
        self.note("old.md", "budget memo")
        ingest.run(self.db, self.config(), LOG, with_summaries=False)
        os.rename(os.path.join(self.notes, "old.md"), os.path.join(self.notes, "new.md"))
        ingest.run(self.db, self.config(), LOG, with_summaries=False)
        self.assertEqual(self.stored_ids(), ["laptop:new.md"])

    def test_an_empty_remote_listing_deletes_nothing(self):
        self.note("keep.md", "keep me")
        ingest.run(self.db, self.config(), LOG, with_summaries=False)
        os.remove(os.path.join(self.notes, "keep.md"))
        ingest.run(self.db, self.config(), LOG, with_summaries=False)
        self.assertEqual(self.stored_ids(), ["laptop:keep.md"])

    def test_a_listing_cut_off_midway_deletes_nothing_and_keeps_what_was_read(self):
        self.note("a.md", "one")
        self.note("b.md", "two")
        failures = ingest.run(self.db, self.config(("drops",)), LOG, with_summaries=False)
        self.assertEqual(failures, 1)
        self.assertEqual(self.stored_ids(), ["drops:a.md", "drops:b.md"])
        self.assertIn("notes@drops", dict(store.failing(self.db)))

    def test_an_unreadable_remote_folder_deletes_nothing(self):
        self.note("locked/a.md", "one")
        ingest.run(self.db, self.config(), LOG, with_summaries=False)
        os.chmod(os.path.join(self.notes, "locked"), 0)
        self.addCleanup(os.chmod, os.path.join(self.notes, "locked"), 0o755)
        if os.access(os.path.join(self.notes, "locked"), os.R_OK):
            self.skipTest("running as a user who can read any folder")
        failures = ingest.run(self.db, self.config(), LOG, with_summaries=False)
        self.assertEqual(failures, 1)
        self.assertEqual(self.stored_ids(), ["laptop:locked/a.md"])

    def test_an_asleep_computer_is_tried_once_a_run_and_reported_only_after_days(self):
        self.note("a.md", "one")
        cfg = self.config(("asleep",), extra='[sources.more]\ntype = "notes"\npath = "/nowhere"\nhosts = ["asleep"]\n')
        failures = ingest.run(self.db, cfg, LOG, with_summaries=False)
        self.assertEqual(failures, 0)
        self.assertEqual(self.ssh_aliases(), ["asleep"])
        self.assertEqual([host for host, _ in store.away(self.db)], ["asleep"])
        store.set_watermark(self.db, store.STATE, "last_ingest", store.now_iso())
        self.db.commit()
        self.assertNotIn("Not refreshing", cli.header(self.db, cfg))
        days_ago = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        store.set_watermark(self.db, store.STATE, "away:asleep", days_ago)
        self.db.commit()
        self.assertIn("asleep (unreachable since", cli.header(self.db, cfg))

    def test_a_computer_back_from_sleep_clears_its_away_mark(self):
        self.note("a.md", "one")
        store.record_away(self.db, "laptop")
        self.db.commit()
        ingest.run(self.db, self.config(), LOG, with_summaries=False)
        self.assertEqual(store.away(self.db), [])

    def test_a_refused_login_is_a_failure_not_sleep(self):
        failures = ingest.run(self.db, self.config(("refused",)), LOG, with_summaries=False)
        self.assertEqual(failures, 1)
        self.assertEqual(store.away(self.db), [])
        self.assertIn("Permission denied", dict(store.failing(self.db))["notes@refused"])

    def test_a_failing_computer_does_not_stop_the_next(self):
        self.note("a.md", "one")
        failures = ingest.run(self.db, self.config(("refused", "laptop")), LOG, with_summaries=False)
        self.assertEqual(failures, 1)
        self.assertEqual(self.stored_ids(), ["laptop:a.md"])

    def test_a_nonzero_exit_after_output_still_fails_and_moves_no_watermark(self):
        self.note("a.md", "one")
        failures = ingest.run(self.db, self.config(("flaky",)), LOG, with_summaries=False)
        self.assertEqual(failures, 1)
        self.assertIsNone(ingest.load_mark(self.db, "notes", "flaky"))
        self.assertIn("connection reset", dict(store.failing(self.db))["notes@flaky"])

    def test_one_source_on_this_computer_and_another(self):
        self.note("a.md", "one")
        cfg = config.parse(f'machine = "desk"\n[hosts.laptop]\nssh = "laptop"\npython = {json.dumps(sys.executable)}\n'
                           f'[sources.notes]\npath = {json.dumps(self.notes)}\nhosts = ["desk", "laptop"]\n')
        self.assertEqual(cfg.units(), ("notes@desk", "notes@laptop", "summarize"))
        self.assertEqual(ingest.run(self.db, cfg, LOG, with_summaries=False), 0)
        self.assertEqual(self.stored_ids(), ["desk:a.md", "laptop:a.md"])
        self.assertEqual(self.ssh_aliases(), ["laptop", "laptop"])


class WireTest(unittest.TestCase):
    def test_events_round_trip(self):
        for event in (Item("a", "2026-10-01T00:00:00Z", "t", "c\ud800", {"path": "/x"}, 3), Gone("b", 4.5),
                      Progress("2026-10-01")):
            with self.subTest(event):
                line = remote.encode(event)
                self.assertTrue(line.isascii())
                self.assertEqual(remote.decode(line), event)

    def test_malformed_lines_are_rejected(self):
        for line in ('{"source_id": "a"}', '[1, 2]', '{"source_id": "a", "mark": 1, "extra": 2}', "not json"):
            with self.subTest(line), self.assertRaises(ValueError):
                remote.decode(line)

    def test_the_vendored_parser_stays_home(self):
        names = remote.package_sources()
        self.assertIn("kb.sources.notes", names)
        self.assertTrue(names["kb.sources"][0])
        self.assertFalse(any(name.startswith("kb._vendor") for name in names))


class HostConfigTest(unittest.TestCase):
    def problems(self, text):
        with self.assertRaises(config.ConfigError) as caught:
            config.parse(text)
        return "\n".join(caught.exception.problems)

    def test_a_source_on_an_unknown_host_is_a_problem(self):
        self.assertIn("sources.notes.hosts: nowhere",
                      self.problems('[sources.notes]\npath = "/n"\nhosts = ["nowhere"]\n'))

    def test_a_host_needs_ssh_and_a_name_other_than_this_machine(self):
        joined = self.problems('machine = "desk"\n[hosts.desk]\nssh = "x"\n[hosts.laptop]\npython = "python3"\n')
        self.assertIn("hosts.desk", joined)
        self.assertIn("hosts.laptop.ssh: required", joined)

    def test_a_type_holding_a_secret_never_runs_remotely(self):
        from dataclasses import dataclass
        from typing import Optional
        from kb.sources import SourceType

        @dataclass(frozen=True)
        class WithSecret:
            password: Optional[config.Secret] = None

        kind = SourceType(WithSecret, lambda run: iter(()))
        self.assertFalse(config.remote_ok(kind))
        self.assertFalse(config.remote_ok(kind._replace(Options=type("NoOptions", (), {"__dataclass_fields__": {}}),
                                                        needs_model=True)))
        from kb.sources import notes
        self.assertTrue(config.remote_ok(notes.TYPE))


if __name__ == "__main__":
    unittest.main()
