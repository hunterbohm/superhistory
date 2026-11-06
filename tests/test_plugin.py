import contextlib
import dataclasses
import io
import json
import logging
import os
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

from kb import cli, config, ingest, paths as paths_module, schedule, search, store
from kb.mask import mask
from kb.paths import Paths
from kb.sources import Gone, Item, Progress, plugin

LOG = logging.getLogger("varve-test-plugin")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False

# Made up. A token of this shape is one the masking reads as a secret.
SECRET = "ghp_" + "Ab3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6"

HEAD = f"#!{sys.executable}\nimport json, os, sys\nreq = json.load(sys.stdin)\n"


def item(sid, title="Weekly sync", text="we shipped the zebrafish report", when="2026-10-04T09:30:00Z", **extra):
    return json.dumps({"id": sid, "time": when, "title": title, "text": text, **extra})


def final(mark):
    return json.dumps({"watermark": mark})


def script(body):
    return HEAD + textwrap.dedent(body)


def printing(*lines, code=0):
    """A plug-in that prints these lines, then exits with `code`."""
    return script(f"""
        for line in {list(lines)!r}:
            print(line)
        sys.exit({code})
        """)


class PluginCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="u7-")
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.paths = Paths.under(os.path.join(self.root, "home"))
        self.dir = self.paths.plugins_dir
        os.makedirs(self.dir, mode=0o700)
        self.paths.data_dir.mkdir(parents=True)
        self.db = store.connect(self.paths.db)
        self.addCleanup(self.db.close)

    def write(self, name, text, mode=0o755):
        path = os.path.join(self.dir, name)
        with open(path, "w") as fh:
            fh.write(text)
        os.chmod(path, mode)
        return path

    def config(self, **sources):
        """sources: name -> (plug-in file, extra TOML lines)."""
        lines = ['machine = "m1"']
        for name, (file, extra) in sources.items():
            lines += [f"[sources.{name}]", 'type = "plugin"', f'plugin = "{file}"', extra]
        return dataclasses.replace(config.parse("\n".join(lines) + "\n"), plugins_dir=str(self.dir))

    def ingest(self, cfg=None, **sources):
        return ingest.run(self.db, cfg or self.config(**sources), LOG, with_summaries=False)

    def titles(self, query):
        return [h["title"] for h in search.search(self.db, query)]

    def failing(self):
        return dict(store.failing(self.db))


class ProtocolTest(PluginCase):
    def test_items_are_stored_and_found_in_search_and_get(self):
        self.write("notes", printing(item("a", url="https://example.com/a", meta={"room": "4"}),
                                     item("b", "Second", "the narwhal budget"), final("w1")))
        self.assertEqual(self.ingest(notes=("notes", "")), 0)
        self.assertEqual(self.titles("zebrafish"), ["Weekly sync"])
        self.assertEqual(self.titles("narwhal"), ["Second"])
        hit = search.search(self.db, "zebrafish")[0]
        self.assertEqual(hit["source"], "notes")
        self.assertEqual(search.pointer(hit["meta"]), "https://example.com/a")
        self.assertEqual(hit["meta"]["room"], "4")
        rows = store.item_rows(self.db, hit["id"])
        self.assertEqual(rows[0][4], "we shipped the zebrafish report")
        self.assertEqual(rows[0][2], "2026-10-04T09:30:00Z")

    def test_the_next_run_gets_the_watermark_and_brings_only_new_items(self):
        seen = os.path.join(self.root, "requests")
        self.write("notes", script(f"""
            with open({seen!r}, "a") as fh:
                fh.write(json.dumps(req["watermark"]) + "\\n")
            if req["watermark"] is None:
                print({item("a")!r})
                print({final("1")!r})
            else:
                print({item("b", "Second", "the narwhal budget")!r})
                print({final("2")!r})
            """))
        cfg = self.config(notes=("notes", ""))
        self.ingest(cfg)
        self.ingest(cfg)
        self.ingest(cfg)
        with open(seen) as fh:
            self.assertEqual(fh.read().split(), ["null", '"1"', '"2"'])
        self.assertEqual(self.titles("zebrafish"), ["Weekly sync"])
        self.assertEqual(self.titles("narwhal"), ["Second"])
        self.assertEqual(self.db.execute("SELECT count(DISTINCT source_id) FROM docs").fetchone()[0], 2)

    def test_the_request_holds_the_settings_and_the_secrets_and_the_secrets_are_not_stored(self):
        key = os.path.join(self.root, "token")
        with open(key, "w") as fh:
            fh.write(SECRET + "\n")
        self.write("notes", script(f"""
            ok = req["protocol"] == 1 and req["settings"] == {{"room": 4, "tags": ["a"]}} and req["secrets"] == {{"token": {SECRET!r}}}
            print(json.dumps({{"id": "a", "time": "2026-10-04", "title": "request " + ("ok" if ok else "wrong"),
                              "text": "carrot", "meta": {{"sent": req["secrets"]["token"]}}}}))
            print({final("1")!r})
            """))
        cfg = self.config(notes=("notes", f'secrets = {{ token = "file:{key}" }}\n[sources.notes.settings]\n'
                                          'room = 4\ntags = ["a"]'))
        self.assertEqual(self.ingest(cfg), 0)
        self.assertEqual(self.titles("carrot"), ["request ok"])
        stored = json.dumps(self.db.execute("SELECT title, content, metadata FROM docs").fetchall())
        self.assertNotIn(SECRET, stored)

    def test_a_path_in_the_metadata_is_text_and_search_still_works(self):
        self.write("notes", printing(item("a", meta={"path": "/notes/a.md"}), final("1")))
        self.assertEqual(self.ingest(notes=("notes", "")), 0)
        hit = search.search(self.db, "zebrafish")[0]
        self.assertEqual(search.pointer(hit["meta"], "m1"), "/notes/a.md")

    def test_a_removed_item_leaves_the_store(self):
        self.write("notes", printing(item("a"), item("b", "Second", "narwhal"), final("1")))
        cfg = self.config(notes=("notes", ""))
        self.ingest(cfg)
        self.write("notes", printing(json.dumps({"id": "a", "gone": True}), final("2")))
        self.ingest(cfg)
        self.assertEqual(self.titles("zebrafish"), [])
        self.assertEqual(self.titles("narwhal"), ["Second"])

    def test_blank_lines_are_allowed_and_a_repeated_id_keeps_the_last(self):
        self.write("notes", printing(item("a", "First", "zebrafish"), "", item("a", "Second", "zebrafish"), final("1")))
        self.assertEqual(self.ingest(notes=("notes", "")), 0)
        self.assertEqual(self.titles("zebrafish"), ["Second"])

    def test_exclude_patterns_skip_items_by_id(self):
        self.write("notes", printing(item("private/a"), item("public/b", "Public", "zebrafish"), final("1")))
        self.assertEqual(self.ingest(notes=("notes", 'exclude = ["private"]')), 0)
        self.assertEqual(self.titles("zebrafish"), ["Public"])

    def test_a_secret_in_an_item_is_masked_before_it_is_stored(self):
        self.write("notes", printing(item("a", f"token {SECRET}", f"deploy with {SECRET} tonight carrot",
                                          meta={"note": SECRET}), final("1")))
        self.assertEqual(self.ingest(notes=("notes", "")), 0)
        self.assertEqual(len(self.titles("carrot")), 1)
        stored = json.dumps(self.db.execute("SELECT title, content, metadata FROM docs").fetchall())
        self.assertNotIn(SECRET, stored)
        self.assertNotIn(SECRET[4:], stored)

    def test_a_rebuild_reads_the_plug_in_again(self):
        home = os.path.join(self.root, "rebuild")
        paths = Paths.under(home)
        os.makedirs(paths.plugins_dir, mode=0o700)
        os.makedirs(paths.data_dir)
        path = os.path.join(paths.plugins_dir, "notes")
        with open(path, "w") as fh:
            fh.write(printing(item("a"), final("1")))
        os.chmod(path, 0o755)
        with open(paths.config_file, "w") as fh:
            fh.write('machine = "m1"\n[sources.notes]\ntype = "plugin"\nplugin = "notes"\n')
        with mock.patch.dict(os.environ, {paths_module.HOME_ENV: home}):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.main(["ingest"]), 0)
                self.assertEqual(cli.main(["rebuild"]), 0)
                self.assertEqual(cli.main(["search", "zebrafish"]), 0)
        self.assertIn("Rebuilt", out.getvalue())
        self.assertIn("Weekly sync", out.getvalue())


class DocsTest(PluginCase):
    def test_the_example_in_the_docs_works_and_a_change_of_settings_starts_it_over(self):
        docs = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "plugins.md")
        with open(docs, encoding="utf-8") as fh:
            text = fh.read()
        example = text.split("```python\n", 1)[1].split("```", 1)[0]
        self.write("notes-feed", example)
        folder = os.path.join(self.root, "feed")
        os.makedirs(folder)
        with open(os.path.join(folder, "sync.txt"), "w") as fh:
            fh.write("we shipped the zebrafish report")
        cfg = self.config(feed=("notes-feed", f"[sources.feed.settings]\nfolder = {json.dumps(folder)}"))
        self.assertEqual(self.ingest(cfg), 0)
        self.assertEqual(self.titles("zebrafish"), ["sync"])
        mark = store.get_watermark(self.db, "feed", "m1")
        self.assertIsNotNone(mark)
        other = os.path.join(self.root, "other")
        os.makedirs(other)
        with open(os.path.join(other, "old.txt"), "w") as fh:
            fh.write("an older narwhal file")
        os.utime(os.path.join(other, "old.txt"), (1, 1))
        again = self.config(feed=("notes-feed", f"[sources.feed.settings]\nfolder = {json.dumps(other)}"))
        self.assertEqual(self.ingest(again), 0)
        self.assertEqual(self.titles("narwhal"), ["old"])


class ParseTest(unittest.TestCase):
    def parse(self, obj):
        return plugin.parse(json.dumps(obj).encode())

    def test_times_are_read_as_utc(self):
        for given, want in (("2026-10-04T09:30:00Z", "2026-10-04T09:30:00Z"),
                            ("2026-10-04T09:30:00+02:00", "2026-10-04T07:30:00Z"),
                            ("2026-10-04T09:30:00-0530", "2026-10-04T15:00:00Z"),
                            ("2026-10-04T09:30:00.123456Z", "2026-10-04T09:30:00Z"),
                            ("2026-10-04T09:30:00.1Z", "2026-10-04T09:30:00Z"),
                            ("2026-10-04 09:30", "2026-10-04T09:30:00Z"),
                            ("2026-10-04", "2026-10-04T00:00:00Z")):
            self.assertEqual(plugin.iso(given), want, given)

    def test_a_time_that_is_not_one_is_refused(self):
        for given in ("2026-10-04T09:30:00+02:99", "2026-10-04T09:30:00+24:00", "2026-10-04T09:30:00-0560",
                      "yesterday", "2026-13-04", "2026-02-30", "2026-10-04T25:00:00Z", 1759570200, None, ""):
            with self.assertRaises(plugin.BadLine, msg=repr(given)):
                plugin.iso(given)

    def test_an_item_needs_an_id_a_time_a_title_and_text(self):
        good = {"id": "a", "time": "2026-10-04", "title": "t", "text": "x"}
        self.assertIsInstance(self.parse(good), Item)
        for key in good:
            with self.assertRaises(plugin.BadLine, msg=key):
                self.parse({k: v for k, v in good.items() if k != key})

    def test_the_wrong_kind_of_value_is_refused(self):
        good = {"id": "a", "time": "2026-10-04", "title": "t", "text": "x"}
        for key, bad in (("id", 7), ("id", ""), ("id", "  "), ("id", "a\nb"), ("id", "x" * 301), ("title", None),
                         ("text", ["x"]), ("url", "not a link"), ("url", 4), ("meta", [1]),
                         ("meta", {"hash": "x"}), ("meta", {"chunk": 1}), ("extra", 1),
                         ("meta", {"url": "https://e.com"}), ("meta", {"path": 5}), ("meta", {"profile": []})):
            with self.assertRaises(plugin.BadLine, msg=f"{key}={bad!r}"):
                self.parse({**good, key: bad})

    def test_a_reason_never_quotes_the_line(self):
        try:
            self.parse({"id": "a", "time": "2026-10-04", "title": "t", "text": "x", SECRET: 1})
        except plugin.BadLine as exc:
            self.assertNotIn(SECRET, str(exc))
        else:
            self.fail("no error")

    def test_the_url_joins_the_metadata(self):
        event = self.parse({"id": "a", "time": "2026-10-04", "title": "t", "text": "x", "url": "https://e.com/a",
                            "meta": {"room": 4}})
        self.assertEqual(event.meta, {"room": 4, "url": "https://e.com/a"})
        self.assertEqual(event.mark, "")

    def test_a_removal_and_a_final_line(self):
        self.assertEqual(self.parse({"id": "a", "gone": True}), Gone("a", ""))
        self.assertEqual(self.parse({"watermark": "w"}), plugin.Final("w"))
        for bad in ({"id": "a", "gone": False}, {"id": "a", "gone": True, "title": "t"}, {"watermark": ""},
                    {"watermark": 5}, {"watermark": "a\nb"}, {"watermark": "w", "id": "a"},
                    {"watermark": "x" * 201}):
            with self.assertRaises(plugin.BadLine, msg=repr(bad)):
                self.parse(bad)

    def test_text_that_is_not_a_json_object_is_refused(self):
        for raw in (b"hello", b"[1]", b'"x"', b"{", b"\xff\xfe", b"[" * 100000):
            with self.assertRaises(plugin.BadLine, msg=raw[:10]):
                plugin.parse(raw)


class IsolationTest(PluginCase):
    def failure_of(self, name):
        found = [line for unit, line in store.failing(self.db) if unit == f"{name}@m1"]
        return found[0] if found else None

    def test_a_plug_in_that_hangs_is_stopped_and_so_is_what_it_started(self):
        pids = os.path.join(self.root, "pids")
        self.write("slow", script(f"""
            import subprocess, time
            child = subprocess.Popen(["sleep", "300"])
            with open({pids!r}, "w") as fh:
                fh.write(f"{{os.getpid()}} {{child.pid}}")
            print({item("a")!r}, flush=True)
            time.sleep(300)
            """))
        started = time.monotonic()
        self.assertEqual(self.ingest(slow=("slow", "timeout_seconds = 2")), 1)
        self.assertLess(time.monotonic() - started, 30)
        self.assertIn("2 s and was stopped", self.failure_of("slow"))
        with open(pids) as fh:
            for pid in map(int, fh.read().split()):
                self.assertFalse(self.alive(pid), pid)
        self.assertEqual(self.titles("zebrafish"), [])
        self.assertIsNone(store.get_watermark(self.db, "slow", "m1"))

    @staticmethod
    def alive(pid):
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            time.sleep(0.1)
        return True

    def test_garbage_fails_the_source_stores_nothing_and_leaves_the_watermark(self):
        self.write("junk", printing(item("a"), "this is not json", '{"id": 5}', item("b", "Second", "narwhal"),
                                    final("1")))
        self.assertEqual(self.ingest(junk=("junk", "")), 1)
        message = self.failure_of("junk")
        self.assertIn("printed 2 lines that", message)
        self.assertIn("line 2: not a JSON object", message)
        self.assertEqual(self.titles("zebrafish"), [])
        self.assertEqual(self.titles("narwhal"), [])
        self.assertIsNone(store.get_watermark(self.db, "junk", "m1"))

    def test_a_clean_run_after_a_failed_one_starts_from_the_same_watermark(self):
        seen = os.path.join(self.root, "requests")
        flag = os.path.join(self.root, "fixed")
        self.write("notes", script(f"""
            with open({seen!r}, "a") as fh:
                fh.write(json.dumps(req["watermark"]) + "\\n")
            print({item("a")!r})
            print("garbage" if not os.path.exists({flag!r}) else {final("2")!r})
            """))
        cfg = self.config(notes=("notes", ""))
        self.assertEqual(self.ingest(cfg), 1)
        open(flag, "w").close()
        self.assertEqual(self.ingest(cfg), 0)
        self.assertEqual(self.ingest(cfg), 0)
        with open(seen) as fh:
            self.assertEqual(fh.read().split(), ["null", "null", '"2"'])
        self.assertEqual(self.titles("zebrafish"), ["Weekly sync"])
        self.assertEqual(self.failure_of("notes"), None)

    def test_a_non_zero_exit_fails_the_source_even_after_a_final_line(self):
        self.write("bad", script(f"""
            print({item("a")!r})
            print({final("1")!r})
            sys.stderr.write("the calendar said no\\n")
            sys.exit(3)
            """))
        self.assertEqual(self.ingest(bad=("bad", "")), 1)
        message = self.failure_of("bad")
        self.assertIn("exited with 3", message)
        self.assertIn("the calendar said no", message)
        self.assertEqual(self.titles("zebrafish"), [])
        self.assertIsNone(store.get_watermark(self.db, "bad", "m1"))

    def test_a_group_writable_file_or_folder_does_not_run(self):
        marker = os.path.join(self.root, "ran")
        self.write("grp", script(f"open({marker!r}, 'w').close()\nprint({final('1')!r})\n"), mode=0o775)
        self.assertEqual(self.ingest(grp=("grp", "")), 1)
        self.assertIn("can be written by anyone", self.failure_of("grp"))
        os.chmod(os.path.join(self.dir, "grp"), 0o755)
        os.chmod(self.dir, 0o770)
        self.addCleanup(os.chmod, self.dir, 0o700)
        self.assertEqual(self.ingest(grp=("grp", "")), 1)
        self.assertIn("can be written by anyone", self.failure_of("grp"))
        self.assertFalse(os.path.exists(marker))

    def test_an_item_the_store_cannot_take_fails_the_source_and_nothing_is_kept(self):
        many = [item(f"n{i}", f"Note {i}", "carrot") for i in range(450)]
        bad = item("zz", "Bad", "x", meta={"profile": []})
        self.write("odd", printing(*many, bad, final("1")))
        self.assertEqual(self.ingest(odd=("odd", "")), 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM docs").fetchone()[0], 0)
        self.assertIsNone(store.get_watermark(self.db, "odd", "m1"))
        self.assertIn("odd@m1", self.failing())
        self.write("odd", printing(*many, final("1")))
        self.assertEqual(self.ingest(odd=("odd", "")), 0)
        self.assertEqual(self.db.execute("SELECT count(DISTINCT source_id) FROM docs").fetchone()[0], 450)

    def test_an_id_that_looks_like_a_secret_fails_the_source_instead_of_being_lost(self):
        self.write("keyed", printing(item(SECRET, "Named by a token", "carrot"), final("1")))
        self.assertEqual(self.ingest(keyed=("keyed", "")), 1)
        self.assertIsNone(store.get_watermark(self.db, "keyed", "m1"))
        self.assertNotIn(SECRET, self.failing()["keyed@m1"])
        self.assertEqual(self.titles("carrot"), [])

    def test_a_private_key_printed_over_several_lines_is_masked_in_the_log(self):
        body = ["MIIEpAIBAAKCAQEA1234567890abcdefghijklmnopqrstuvwxyzABCDEFGHIJKL",
                "QRSTuvwxyz0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQR",
                "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZab"]
        pem = "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(body) + "\n-----END RSA PRIVATE KEY-----\n"
        self.write("pem", script(f"""
            sys.stderr.write({pem!r})
            sys.stderr.write("done\\n")
            sys.exit(2)
            """))
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record.getMessage())
        log = logging.getLogger("kb.plugin")
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        self.addCleanup(log.removeHandler, handler)
        self.assertEqual(self.ingest(pem=("pem", "")), 1)
        text = "\n".join(records) + self.failing()["pem@m1"]
        for line in body:
            self.assertNotIn(line[10:40], text)
        self.assertIn("done", text)

    def test_a_secret_cut_by_the_stderr_limit_does_not_reach_the_log_in_part(self):
        self.write("cut", script(f"""
            sys.stderr.write("z" * 150 + "\\n" + {SECRET!r} + "\\n")
            sys.exit(2)
            """))
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record.getMessage())
        log = logging.getLogger("kb.plugin")
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        self.addCleanup(log.removeHandler, handler)
        with mock.patch.object(plugin, "STDERR_BYTES", 170):
            self.assertEqual(self.ingest(cut=("cut", "")), 1)
        text = "\n".join(records) + self.failing()["cut@m1"]
        self.assertNotIn(SECRET[:12], text)
        self.assertIn("zzzz", text)

    def test_a_completed_line_over_the_limit_is_refused_even_when_it_arrives_with_its_newline(self):
        self.write("fat", script("""
            sys.stdout.write("x" * 3000 + "\\n")
            sys.stdout.flush()
            """))
        with mock.patch.object(plugin, "MAX_LINE_BYTES", 1000):
            self.assertEqual(self.ingest(fat=("fat", "")), 1)
        self.assertIn("line longer than", self.failing()["fat@m1"])

    def test_no_final_line_fails_the_source(self):
        self.write("short", printing(item("a")))
        self.assertEqual(self.ingest(short=("short", "")), 1)
        self.assertIn("without a final line", self.failure_of("short"))
        self.assertEqual(self.titles("zebrafish"), [])

    def test_a_line_after_the_final_line_fails_the_source(self):
        self.write("more", printing(item("a"), final("1"), item("b")))
        self.assertEqual(self.ingest(more=("more", "")), 1)
        self.assertIn("a line after the final watermark line", self.failure_of("more"))

    def test_a_watermark_that_looks_like_a_secret_fails_the_source(self):
        self.write("keyed", printing(item("a"), final(SECRET)))
        self.assertEqual(self.ingest(keyed=("keyed", "")), 1)
        self.assertIn("looks like a secret", self.failure_of("keyed"))
        self.assertNotIn(SECRET, self.failure_of("keyed"))

    def test_too_much_output_stops_the_run(self):
        self.write("loud", script(f"""
            line = json.dumps({{"id": "a", "time": "2026-10-04", "title": "t", "text": "x" * 100000}})
            for _ in range(100):
                print(line, flush=True)
            """))
        started = time.monotonic()
        self.assertEqual(self.ingest(loud=("loud", "max_output_mb = 1")), 1)
        self.assertLess(time.monotonic() - started, 30)
        self.assertIn("more than 1 MB", self.failure_of("loud"))
        self.assertEqual(self.db.execute("SELECT count(*) FROM docs").fetchone()[0], 0)

    def test_one_line_that_never_ends_stops_the_run(self):
        self.write("wide", script("""
            sys.stdout.write("x" * 20_000_000)
            sys.stdout.flush()
            """))
        with mock.patch.object(plugin, "MAX_LINE_BYTES", 1_000_000):
            self.assertEqual(self.ingest(wide=("wide", "max_output_mb = 100")), 1)
        self.assertIn("line longer than", self.failure_of("wide"))

    def test_one_plug_in_failing_does_not_stop_the_others(self):
        self.write("bad", printing(item("a"), code=1))
        self.write("good", printing(item("b", "Second", "narwhal"), final("1")))
        self.write("also", printing(item("c", "Third", "walrus"), final("1")))
        self.assertEqual(self.ingest(bad=("bad", ""), good=("good", ""), also=("also", "")), 1)
        self.assertEqual(self.titles("narwhal"), ["Second"])
        self.assertEqual(self.titles("walrus"), ["Third"])
        self.assertEqual(list(self.failing()), ["bad@m1"])

    def test_the_standard_error_goes_to_the_log_masked_and_within_a_limit(self):
        self.write("chatty", script(f"""
            for i in range(500):
                sys.stderr.write(f"step {{i}} with {SECRET}\\n")
            print({item("a")!r})
            print({final("1")!r})
            """))
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record.getMessage())
        log = logging.getLogger("kb.plugin")
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        self.addCleanup(log.removeHandler, handler)
        self.assertEqual(self.ingest(chatty=("chatty", "")), 0)
        text = "\n".join(records)
        self.assertNotIn(SECRET, text)
        self.assertIn("plugin chatty: step 0 with", text)
        self.assertLessEqual(sum(1 for r in records if "step " in r), plugin.STDERR_LINES)
        self.assertIn("the rest of its standard error was not logged", text)

    def test_a_plug_in_that_ignores_its_input_still_runs(self):
        self.write("deaf", f"#!{sys.executable}\nimport sys\nprint({item('a')!r})\nprint({final('1')!r})\n")
        self.assertEqual(self.ingest(deaf=("deaf", "[sources.deaf.settings]\ntext = " + json.dumps("x" * 400_000))), 0)
        self.assertEqual(self.titles("zebrafish"), ["Weekly sync"])

    def test_the_plug_in_gets_a_small_environment(self):
        self.write("env", script("""
            print(json.dumps({"id": "a", "time": "2026-10-04", "title": "env", "text": " ".join(sorted(os.environ))}))
            print(json.dumps({"watermark": "1"}))
            """))
        with mock.patch.dict(os.environ, {"SOME_OTHER_API_KEY": "swordfish9", "PATH": os.environ["PATH"]}):
            self.assertEqual(self.ingest(env=("env", "")), 0)
        text = self.db.execute("SELECT content FROM docs").fetchone()[0]
        self.assertNotIn("SOME_OTHER_API_KEY", text)
        self.assertIn("PATH", text)

    def test_a_timeout_does_not_leave_a_run_waiting_on_a_program_that_exited_but_left_its_output_open(self):
        self.write("daemon", script(f"""
            import subprocess
            subprocess.Popen(["sleep", "30"])
            print({item("a")!r})
            print({final("1")!r})
            """))
        started = time.monotonic()
        self.assertEqual(self.ingest(daemon=("daemon", "")), 0)
        self.assertLess(time.monotonic() - started, 15)


class RulesTest(PluginCase):
    def failure_of(self, name):
        return dict(store.failing(self.db)).get(f"{name}@m1")

    def test_a_file_in_the_folder_runs_only_when_the_config_names_it(self):
        marker = os.path.join(self.root, "ran")
        self.write("stray", script(f"open({marker!r}, 'w').close()\nprint({final('1')!r})\n"))
        self.write("named", printing(item("a"), final("1")))
        self.assertEqual(self.ingest(named=("named", "")), 0)
        self.assertFalse(os.path.exists(marker))

    def test_a_plug_in_that_is_missing_or_not_executable_or_open_to_all_does_not_run(self):
        marker = os.path.join(self.root, "ran")
        body = script(f"open({marker!r}, 'w').close()\nprint({final('1')!r})\n")
        self.write("plain", body, mode=0o644)
        self.write("open", body, mode=0o757)
        for name, words in (("absent", "no plug-in named absent"), ("plain", "not executable"),
                            ("open", "can be written by anyone")):
            self.assertEqual(self.ingest(**{name: (name, "")}), 1, name)
            self.assertIn(words, self.failure_of(name))
        self.assertFalse(os.path.exists(marker))

    def test_a_link_in_the_folder_does_not_run_even_to_a_file_of_the_users(self):
        marker = os.path.join(self.root, "ran")
        elsewhere = os.path.join(self.root, "elsewhere")
        os.makedirs(elsewhere, mode=0o700)
        target = os.path.join(elsewhere, "real")
        with open(target, "w") as fh:
            fh.write(script(f"open({marker!r}, 'w').close()\nprint({final('1')!r})\n"))
        os.chmod(target, 0o755)
        os.symlink(target, os.path.join(self.dir, "linked"))
        self.assertEqual(self.ingest(linked=("linked", "")), 1)
        self.assertIn("is a link", self.failure_of("linked"))
        self.assertFalse(os.path.exists(marker))

    def test_a_folder_that_anyone_can_write_does_not_run_its_plug_ins(self):
        self.write("notes", printing(item("a"), final("1")))
        os.chmod(self.dir, 0o777)
        self.addCleanup(os.chmod, self.dir, 0o700)
        self.assertEqual(self.ingest(notes=("notes", "")), 1)
        self.assertIn("can be written by anyone", self.failure_of("notes"))

    def test_a_config_with_no_plug_ins_folder_fails_the_source(self):
        cfg = dataclasses.replace(self.config(notes=("notes", "")), plugins_dir="")
        self.assertEqual(self.ingest(cfg), 1)
        self.assertIn("no plug-ins folder", self.failure_of("notes"))

    def test_a_plug_in_name_cannot_leave_the_folder(self):
        for name in ("../notes", "sub/notes", ".notes", "no tes", ""):
            with self.assertRaises(config.ConfigError, msg=name):
                self.config(x=(name, ""))

    def test_the_config_checks_the_keys_and_values(self):
        def problems(extra):
            with self.assertRaises(config.ConfigError) as caught:
                config.parse(f'[sources.x]\ntype = "plugin"\nplugin = "x"\n{extra}\n')
            return "\n".join(caught.exception.problems)

        self.assertIn("sources.x.timeout_seconds", problems("timeout_seconds = 0"))
        self.assertIn("sources.x.max_output_mb", problems("max_output_mb = 5000"))
        self.assertIn("sources.x.secrets", problems('secrets = { Token = "env:X" }'))
        self.assertIn('expected "env:NAME"', problems('secrets = { token = "plain" }'))
        self.assertIn("sources.x.colour: unknown key", problems('colour = "red"'))
        with self.assertRaises(config.ConfigError) as caught:
            config.parse('[sources.x]\ntype = "plugin"\n')
        self.assertIn("sources.x.plugin: required", str(caught.exception))

    def test_a_plug_in_source_cannot_run_on_another_computer(self):
        with self.assertRaises(config.ConfigError) as caught:
            config.parse('machine = "m1"\n[hosts.other]\nssh = "me@other"\n[sources.x]\ntype = "plugin"\n'
                         'plugin = "x"\nhosts = ["other"]\n')
        self.assertIn("runs only on this computer", str(caught.exception))

    def test_the_settings_digest_changes_with_the_settings_and_not_with_a_secret(self):
        base = 'type = "plugin"\nplugin = "x"\n'
        a = config.parse(f'[sources.x]\n{base}secrets = {{ token = "env:A" }}\n[sources.x.settings]\nroom = 1\n')
        b = config.parse(f'[sources.x]\n{base}secrets = {{ token = "env:A" }}\n[sources.x.settings]\nroom = 2\n')
        self.assertNotEqual(ingest.settings_digest(a.sources[0]), ingest.settings_digest(b.sources[0]))
        with mock.patch.dict(os.environ, {"A": SECRET}):
            self.assertNotIn(SECRET, json.dumps(dataclasses.asdict(a.sources[0].options), default=str))


class CommandTest(PluginCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {paths_module.HOME_ENV: os.path.dirname(self.paths.config_file)}):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def write_config(self, text):
        with open(self.paths.config_file, "w") as fh:
            fh.write(text)

    def test_list_shows_each_plug_in_and_who_uses_it(self):
        self.write("ready", printing(final("1")))
        self.write("plain", printing(final("1")), mode=0o644)
        self.write_config('machine = "m1"\n[sources.cal]\ntype = "plugin"\nplugin = "ready"\n'
                          '[sources.gone]\ntype = "plugin"\nplugin = "lost"\n')
        code, out, _ = self.run_cli("plugins", "list")
        self.assertEqual(code, 0)
        self.assertIn(str(self.dir), out)
        self.assertRegex(out, r"ready\s+ready; used by cal")
        self.assertRegex(out, r"plain\s+will not run: .*is not executable.*; used by no source yet")
        self.assertRegex(out, r"lost\s+missing; used by gone")

    def test_test_runs_the_plug_in_once_from_the_start_and_stores_nothing(self):
        self.write("ready", printing(item("a"), json.dumps({"id": "b", "gone": True}), final("w7")))
        self.write_config('machine = "m1"\n[sources.cal]\ntype = "plugin"\nplugin = "ready"\n')
        code, out, err = self.run_cli("plugins", "test", "cal")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("watermark   w7", out)
        self.assertIn("1 item would be stored", out)
        self.assertIn("2026-10-04  Weekly sync", out)
        self.assertEqual(store.connect(self.paths.db).execute("SELECT count(*) FROM docs").fetchone()[0], 0)
        self.assertIsNone(store.get_watermark(store.connect(self.paths.db), "cal", "m1"))

    def test_test_fails_for_what_the_store_would_refuse_as_ingest_does(self):
        for line in (item(SECRET, "Named by a token", "carrot"), item("a", meta={"profile": []})):
            self.write("ready", printing(line, final("w7")))
            self.write_config('machine = "m1"\n[sources.cal]\ntype = "plugin"\nplugin = "ready"\n')
            code, out, err = self.run_cli("plugins", "test", "cal")
            self.assertEqual(code, 1, line[:40])
            self.assertIn("Nothing was stored", err)
            self.assertNotIn(SECRET, out + err)

    def test_test_says_what_failed(self):
        self.write("ready", printing(item("a"), "nonsense", final("w7")))
        self.write_config('machine = "m1"\n[sources.cal]\ntype = "plugin"\nplugin = "ready"\n')
        code, out, err = self.run_cli("plugins", "test", "cal")
        self.assertEqual(code, 1)
        self.assertIn("line 2: not a JSON object", err)
        self.assertIn("Nothing was stored", err)

    def test_test_needs_a_plug_in_source(self):
        self.write_config('machine = "m1"\n[sources.n]\ntype = "notes"\npath = "/tmp"\n')
        for argv in (("plugins", "test"), ("plugins", "test", "n"), ("plugins", "test", "other")):
            self.assertEqual(self.run_cli(*argv)[0], 1, argv)

    def test_paths_names_the_plug_ins_folder(self):
        self.assertIn(str(self.dir), self.run_cli("paths")[1])


class ScheduleTest(PluginCase):
    def test_the_scheduled_job_runs_ingest_every_15_minutes_and_ingest_reads_plug_ins(self):
        cfg = self.config(notes=("notes", ""))
        self.assertEqual(cfg.schedule.every_minutes, 15)
        self.assertIn("OnUnitActiveSec=15min", schedule._timer_text(cfg.schedule.every_minutes))
        exec_lines = [l for l in schedule._service_text("/bin/tool").splitlines() if l.startswith("ExecStart=")]
        self.assertTrue(exec_lines and exec_lines[0].endswith(" ingest"), exec_lines)
        self.write("notes", printing(item("a"), final("1")))
        home = os.path.dirname(self.paths.config_file)
        with open(self.paths.config_file, "w") as fh:
            fh.write('machine = "m1"\n[sources.notes]\ntype = "plugin"\nplugin = "notes"\n')
        with mock.patch.dict(os.environ, {paths_module.HOME_ENV: home}):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["ingest"]), 0)
                self.write("notes", printing(item("a"), item("b", "Second", "narwhal"), final("2")))
                self.assertEqual(cli.main(["ingest"]), 0)
        db = store.connect(self.paths.db)
        self.addCleanup(db.close)
        self.assertEqual([h["title"] for h in search.search(db, "narwhal")], ["Second"])


if __name__ == "__main__":
    unittest.main()
