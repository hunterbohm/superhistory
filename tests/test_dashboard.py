import contextlib
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

from kb import cli, config as config_mod, store, trial
from kb.dashboard import catalog, server, views
from kb.paths import Paths

ROOT = Path(__file__).resolve().parent.parent
MONTH = ROOT / "demo" / "month"
if not (MONTH / "items.jsonl").exists():
    MONTH = Path(trial.month_dir())
HOME_FOLDER = re.compile(r"^(?:~|/(?:Users|home)/[^/]+)/?$")


def last_part(text):
    return [p for p in re.split(r"[\\/]+", text) if p][-1]


class Served(unittest.TestCase):
    """A dashboard server over a store in a folder of its own."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = Paths.under(tmp.name)
        self.db = store.connect(self.paths.db)

    def serve(self, config=None):
        self.db.commit()
        self.db.close()
        srv = server.make_server(self.paths, config or config_mod.Config())
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.srv = srv
        return srv

    def keyed(self):
        """The header the page's script sends: the session key the printed address is swapped for on its one visit."""
        if self.srv.session is None:
            self.assertTrue(self.srv.spend(self.srv.token))
        return {server.KEY_HEADER: self.srv.session}

    def get(self, path, headers=None, key=True):
        """GET as the page's script would, sending the run's key in its header, unless `key` is False."""
        request = urllib.request.Request(self.srv.url.split("?")[0].rstrip("/") + path,
                                         headers={**(self.keyed() if key else {}), **(headers or {})})
        try:
            with urllib.request.urlopen(request) as res:
                return res.status, res.read(), res.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def api(self, path):
        status, body, _ = self.get("/api/" + path)
        self.assertEqual(status, 200, body[:200])
        return json.loads(body)

    def add(self, source, sid, ts, title, content, meta):
        store.store_item(self.db, source, f"here:{sid}", ts, title, content, meta)


class MonthTest(Served):
    """Every project and person the made-up month holds has a page, found from the month's own items."""

    @classmethod
    def setUpClass(cls):
        cls.items = [json.loads(line) for line in (MONTH / "items.jsonl").read_text(encoding="utf-8").splitlines() if line]
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.paths = Paths.under(tmp.name)
        db = store.connect(cls.paths.db)
        trial.load(db, MONTH)
        db.close()
        cls.srv = server.make_server(cls.paths, config_mod.Config())
        thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(thread.join)
        cls.addClassCleanup(cls.srv.server_close)
        cls.addClassCleanup(cls.srv.shutdown)

    def setUp(self):
        pass

    def test_each_project_in_the_month_has_a_page_with_its_count(self):
        expected = Counter()
        for item in self.items:
            meta = item.get("meta", {})
            cwd, repo = meta.get("cwd"), meta.get("repo")
            if cwd and not HOME_FOLDER.match(cwd):
                expected[last_part(cwd)] += 1
            elif repo and item["source"] in ("git", "github"):
                expected[last_part(repo)] += 1
        self.assertGreaterEqual(len(expected), 5)
        listed = {p["name"]: p["count"] for p in self.api("projects")}
        self.assertEqual(listed, dict(expected))
        for name, count in expected.items():
            page = self.api("project?key=" + urllib.request.quote(name))
            self.assertEqual((page["name"], page["total"]), (name, count))
            self.assertEqual(len(page["items"]), min(count, views.LIST_LIMIT))
            self.assertEqual(self.get("/project/" + urllib.request.quote(name))[0], 200)

    def test_each_person_texted_in_the_month_has_a_page_with_its_count(self):
        texts = Counter()
        for item in self.items:
            if "chat" in item.get("meta", {}):
                for name in item["meta"]["participants"]:
                    texts[name] += 1
        self.assertGreaterEqual(len(texts), 8)
        listed = {p["name"]: p for group in self.api("people").values() for p in group}
        for name, count in texts.items():
            self.assertIn(name, listed)
            page = self.api("person?key=" + urllib.request.quote(name))
            self.assertEqual(page["families"].get("text"), count, name)
            self.assertEqual(listed[name]["count"], page["total"])
            self.assertEqual(self.get("/person/" + urllib.request.quote(name))[0], 200)

    def test_each_correspondent_in_the_month_has_a_page_holding_all_their_threads(self):
        threads = [i for i in self.items if "account" in i.get("meta", {})]
        own = {i["meta"]["account"].lower() for i in threads}
        addresses = {a.lower() for i in threads for a in i["meta"]["participants"]} - own
        self.assertGreaterEqual(len(addresses), 20)
        people = [p for group in self.api("people").values() for p in group]
        by_address = {}
        for person in people:
            page = self.api("person?key=" + urllib.request.quote(person["key"]))
            for address in page["addresses"]:
                by_address[address] = page
        self.assertEqual(set(by_address), addresses)
        for address, page in by_address.items():
            theirs = set(page["addresses"])
            held = sum(1 for i in threads if theirs & {a.lower() for a in i["meta"]["participants"]})
            self.assertEqual(page["families"].get("mail"), held, address)

    def test_the_lists_give_every_page_once_and_the_overview_counts_them(self):
        people = self.api("people")
        keys = [p["key"] for group in people.values() for p in group]
        self.assertEqual(len(keys), len(set(keys)))
        overview = self.api("overview")
        self.assertEqual(overview["people_total"], len(keys))
        self.assertEqual(overview["project_total"], len(self.api("projects")))
        self.assertEqual(overview["items"], len(self.items))
        self.assertEqual({s["name"]: s["items"] for s in overview["sources"]}, dict(Counter(i["source"] for i in self.items)))

    def test_a_newsletter_sender_is_listed_apart_from_people(self):
        senders = {p["name"] for p in self.api("people")["senders"]}
        people = {p["name"] for p in self.api("people")["people"]}
        self.assertIn("Quiet Workflow", senders)
        self.assertIn("Dana Whitfield", people)

    def test_a_person_texted_and_mailed_is_one_page(self):
        page = self.api("person?key=Dana%20Whitfield")
        self.assertIn("text", page["families"])
        self.assertIn("mail", page["families"])

    def test_every_item_in_the_month_opens(self):
        for entry in catalog.build(store.connect(self.paths.db, readonly=True)).entries[::7]:
            page = self.api(f"item?id={entry.id}")
            self.assertEqual(page["title"], entry.title)


class GroupingTest(Served):
    def stored(self):
        self.db.commit()
        return catalog.build(self.db)

    def test_a_home_folder_is_no_project_and_a_windows_path_is_named_by_its_last_part(self):
        for n, cwd in enumerate(["/Users/ana", "/home/ana/", "~", "C:\\Users\\ana", "C:\\Users\\ana\\code\\Ledger", "/srv/Ledger"]):
            self.add("claude_code", f"s{n}", f"2026-09-0{n + 1}T10:00:00Z", f"t{n}", "x", {"cwd": cwd, "path": "p"})
        self.add("claude_code", "none", "2026-09-09T10:00:00Z", "no folder", "x", {"cwd": None, "path": "p"})
        found = self.stored()
        self.assertEqual({g.name: g.count for g in found.projects.values()}, {"Ledger": 2})

    def test_sessions_commits_and_pull_requests_of_one_repository_meet(self):
        self.add("claude_code", "s", "2026-09-01T10:00:00Z", "s", "x", {"cwd": "/w/Orders"})
        self.add("git", "c", "2026-09-02T10:00:00Z", "c", "x", {"repo": "acme/orders", "sha": "1", "path": "/w/orders"})
        self.add("github", "p", "2026-09-03T10:00:00Z", "p", "x", {"repo": "acme/orders", "kind": "pr", "number": 1})
        self.add("notes", "n", "2026-09-04T10:00:00Z", "n", "x", {"path": "/n"})
        found = self.stored()
        self.assertEqual([(g.count, g.by_family()) for g in found.projects.values()],
                         [(3, {"agent": 1, "commit": 1, "pr": 1})])

    def test_a_source_the_user_named_is_read_by_what_it_records(self):
        self.add("work-laptop", "s", "2026-09-01T10:00:00Z", "s", "x", {"cwd": "/w/orders", "branch": "main"})
        self.add("my-chats", "c", "2026-09-01T10:00:00Z", "c", "x", {"chat": "g", "participants": ["Bo"]})
        found = self.stored()
        self.assertEqual({e.source: e.family for e in found.entries}, {"work-laptop": "agent", "my-chats": "text"})
        self.assertEqual(list(found.projects), ["orders"])
        self.assertEqual(list(found.people), ["bo"])

    def mail(self, sid, headers, labels=("INBOX",), account="me@own.example"):
        addresses = [a for _, a in re.findall(r"(?:From|To): ?([^<]*)<([^>]+)>", headers)] + [account]
        self.add("gmail", sid, "2026-09-05T10:00:00Z", sid, headers + "\n\nbody",
                 {"account": account, "participants": addresses, "labels": list(labels)})

    def test_one_name_is_one_person_across_addresses_and_across_texts_and_mail(self):
        self.mail("a", "From: Rosa Diaz <rosa@one.example>\nTo: Me <me@own.example>")
        self.mail("b", "From: Rosa Diaz <rosa.diaz@two.example>\nTo: Me <me@own.example>")
        self.add("imessage", "t", "2026-09-06T10:00:00Z", "t", "x", {"chat": "c", "participants": ["Rosa Diaz"]})
        found = self.stored()
        self.assertEqual({g.name: (g.count, sorted(g.addresses)) for g in found.people.values()},
                         {"Rosa Diaz": (3, ["rosa.diaz@two.example", "rosa@one.example"])})

    def test_a_thread_naming_one_person_in_two_cases_counts_once_and_keeps_both_addresses(self):
        self.mail("a", "From: Rosa Diaz <rosa@one.example>\nTo: ROSA DIAZ <rosa@two.example>")
        found = self.stored()
        self.assertEqual({g.name: (g.count, sorted(g.addresses)) for g in found.people.values()},
                         {"Rosa Diaz": (1, ["rosa@one.example", "rosa@two.example"])})
        self.assertEqual(len(found.entries[0].people), 1)

    def test_the_users_own_address_and_name_are_nobody_to_list(self):
        self.mail("a", "From: Me Myself <me@own.example>\nTo: Kay <kay@x.example>")
        self.mail("b", "From: Kay <kay@x.example>\nTo: Me Myself <me@own.example>")
        self.assertEqual([g.name for g in self.stored().people.values()], ["Kay"])

    def test_an_address_with_no_name_keeps_the_address_as_its_name(self):
        self.mail("a", "From: <anon@x.example>\nTo: Me <me@own.example>")
        self.assertEqual([g.name for g in self.stored().people.values()], ["anon@x.example"])

    def test_a_sender_of_category_mail_alone_is_automated_until_a_thread_is_not(self):
        self.mail("a", "From: Shop <shop@x.example>", labels=("INBOX", "CATEGORY_PROMOTIONS"))
        self.mail("b", "From: Kay <kay@x.example>", labels=("INBOX", "CATEGORY_UPDATES"))
        self.mail("c", "From: Kay <kay@x.example>", labels=("INBOX", "IMPORTANT"))
        found = self.stored().people
        self.assertEqual({g.name: g.automated for g in found.values()}, {"Shop": True, "Kay": False})

    def test_a_sender_of_personal_category_mail_is_a_person_not_automated(self):
        self.mail("a", "From: Kay <kay@x.example>", labels=("INBOX", "CATEGORY_PERSONAL"))
        found = self.stored().people
        self.assertEqual({g.name: g.automated for g in found.values()}, {"Kay": False})

    def test_a_sender_of_social_or_forum_mail_alone_is_automated(self):
        self.mail("a", "From: Net <net@x.example>", labels=("INBOX", "CATEGORY_SOCIAL"))
        self.mail("b", "From: Board <board@x.example>", labels=("INBOX", "CATEGORY_FORUMS"))
        found = self.stored().people
        self.assertEqual({g.name: g.automated for g in found.values()}, {"Net": True, "Board": True})

    def test_a_chunked_item_is_one_item(self):
        self.add("claude_code", "long", "2026-09-01T10:00:00Z", "long", "word " * 3000, {"cwd": "/w/orders"})
        found = self.stored()
        self.assertEqual(len(found.entries), 1)
        self.assertEqual(found.projects["orders"].count, 1)

    def test_an_item_masked_by_an_older_version_is_masked_again(self):
        secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
        self.add("notes", "n", "2026-09-01T10:00:00Z", "plain", "hello", {"path": "/n"})
        self.db.execute("UPDATE docs SET title = ?, content = ?", (f"key {secret}", f"hello {secret}"))
        self.db.execute("UPDATE docs SET masked = 'older'")
        self.serve()
        listed = self.api("source?name=notes")["items"][0]
        self.assertNotIn(secret, listed["title"])
        page = self.api(f"item?id={listed['id']}")
        self.assertNotIn(secret, json.dumps(page))
        self.assertNotIn(secret, json.dumps(self.api("overview")))

    def test_an_item_of_an_older_version_whose_source_name_holds_a_secret_is_left_out_of_every_listing(self):
        secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
        self.add("notes", "n", "2026-09-01T10:00:00Z", "plain", "hello", {"path": "/n"})
        self.add("notes", "m", "2026-09-02T10:00:00Z", "other", "hi", {"path": "/m"})
        self.db.execute("UPDATE docs SET source = ?, masked = 'older' WHERE source_id = 'here:n'", (secret,))
        self.serve()
        for path in ("overview", "projects", "people", "source?name=notes", "search?q=hello", "item?id=1"):
            status, body, _ = self.get("/api/" + path)
            self.assertNotIn(secret.encode(), body, path)
        self.assertEqual([i["title"] for i in self.api("source?name=notes")["items"]], ["other"])

    def test_a_long_item_masked_again_opens_a_later_chunk_at_the_passage_in_the_text_as_shown(self):
        secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
        lines = [f"SECRETX {'filler ' * 20}" for _ in range(1500)]
        lines[750] = "needleword in the middle"
        self.add("notes", "long", "2026-09-01T10:00:00Z", "long", "\n".join(lines), {"path": "/n"})
        self.db.execute("UPDATE docs SET content = replace(content, 'SECRETX', ?)", (secret,))
        self.db.execute("UPDATE docs SET masked = 'older'")  # a change to the text clears the version, so it is set after
        chunk = self.db.execute("SELECT id FROM docs WHERE kind = 'raw' AND content LIKE '%needleword%'").fetchone()[0]
        self.serve()
        page = self.api(f"item?id={chunk}&q=needleword")
        self.assertGreater(page["pages"], 3, "the text shown is still several pages")
        self.assertIn("needleword", page["text"])
        self.assertNotIn(secret, json.dumps(page))

    def test_a_summary_of_an_older_version_is_masked_though_its_item_is_current(self):
        secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
        self.add("notes", "n", "2026-09-01T10:00:00Z", "plain", "hello", {"path": "/n"})
        self.db.execute("INSERT INTO docs(kind, source, source_id, ts, title, content, metadata, masked) "
                        "VALUES ('segment', 'notes', 'here:n', '2026-09-01T10:00:00Z', ?, ?, '{}', 'older')",
                        (f"topic {secret}", f"summary {secret}"))
        self.serve()
        page = self.api("item?id=1")
        self.assertEqual(len(page["summaries"]), 1)
        self.assertNotIn(secret, json.dumps(page))


SESSION = ("Claude Code session in /w/orders (main)\n\nUSER: why does the build fail\n\nASSISTANT: I will look.\n\n"
           "TOOL Bash: npm run build\n\nRESULT: ok\nsecond line\n\nTOOL Read: a.txt\n\nERROR: no such file\n\n"
           "TOOL Grep: x\n\nASSISTANT: It is a casing problem.")


class TranscriptTest(Served):
    def test_a_session_reads_as_turns_with_tool_calls_folded(self):
        head, turns = views.parse_turns(SESSION)
        self.assertEqual(head, "Claude Code session in /w/orders (main)")
        self.assertEqual([(t["role"], t.get("name")) for t in turns],
                         [("user", None), ("assistant", None), ("tool", "Bash"), ("tool", "Read"), ("tool", "Grep"),
                          ("assistant", None)])
        self.assertEqual(turns[2]["output"], "ok\nsecond line")
        self.assertFalse(turns[2]["failed"])
        self.assertTrue(turns[3]["failed"])
        self.assertNotIn("output", turns[4])

    def test_calls_made_together_never_get_a_result_that_belongs_to_another(self):
        text = ("Claude Code session in /w/x\n\nUSER: go\n\nTOOL A: one\n\nTOOL B: two\n\nRESULT: for a\n\n"
                "ERROR: for b\n\nASSISTANT: done\n\nTOOL C: three\n\nRESULT: for c")
        _, turns = views.parse_turns(text)
        self.assertEqual([(t["role"], t.get("name")) for t in turns],
                         [("user", None), ("tool", "A"), ("tool", "B"), ("result", None), ("error", None),
                          ("assistant", None), ("tool", "C")])
        self.assertNotIn("output", turns[1])
        self.assertNotIn("output", turns[2])
        self.assertEqual((turns[3]["text"], turns[4]["text"]), ("for a", "for b"))
        self.assertEqual((turns[6]["output"], turns[6]["failed"]), ("for c", False), "a call alone is still paired")

    def test_a_page_that_opens_inside_a_batch_pairs_no_result_with_its_first_call(self):
        _, turns = views.parse_turns("TOOL B: two\n\nRESULT: for a\n\nASSISTANT: x", safe_start=False)
        self.assertEqual([t["role"] for t in turns], ["tool", "result", "assistant"])
        self.assertNotIn("output", turns[0])
        _, turns = views.parse_turns("TOOL B: two\n\nRESULT: for b", safe_start=True)
        self.assertEqual(turns[0]["output"], "for b")

    def test_a_summary_hit_has_the_family_of_its_item(self):
        self.add("claude_code", "s", "2026-09-01T10:00:00Z", "build", "USER: hello", {"cwd": "/w/orders"})
        self.db.execute("INSERT INTO docs(kind, source, source_id, ts, title, content, metadata, masked) "
                        "VALUES ('segment', 'claude_code', 'here:s', '2026-09-01T10:00:00Z', 'topic', 'needleword summary', "
                        "'{}', ?)", (store.VERSION,))
        self.serve()
        hits = self.api("search?q=needleword")
        self.assertIn("segment", {h["kind"] for h in hits})
        self.assertEqual({h["family"] for h in hits}, {"agent"})

    def test_a_page_never_starts_on_a_result_or_on_the_second_call_of_a_batch(self):
        text = ("Claude Code session in /w/x\n\nUSER: go\n\nTOOL A: one\n\nTOOL B: two\n\nRESULT: for a\n\n"
                "ERROR: for b\n\nASSISTANT: done\n\nTOOL C: three\n\nRESULT: for c\n\nTOOL D: four\n\nUSER: more")
        starts = views.turn_starts(text)
        self.assertEqual([text[i:i + 6] for i in starts], ["USER: ", "TOOL A", "ASSIST", "TOOL C", "TOOL D", "USER: "])

    def test_the_item_page_gives_turns_for_a_session_and_text_for_a_note(self):
        self.add("claude_code", "s", "2026-09-01T10:00:00Z", "build", SESSION, {"cwd": "/w/orders"})
        self.add("notes", "n", "2026-09-01T10:00:00Z", "note", "USER: not a session", {"path": "/n"})
        self.serve()
        session = self.api("item?id=1")
        self.assertTrue(session["session"])
        self.assertEqual(session["turns"][0]["text"], "why does the build fail")
        self.assertEqual(session["project"]["name"], "orders")
        note = self.api("item?id=2")
        self.assertFalse(note["session"])
        self.assertEqual(note["text"], "USER: not a session")

    def test_a_long_session_pages_at_turn_starts(self):
        turns = "\n\n".join(f"USER: {'q' * 4000}\n\nASSISTANT: {'a' * 4000}" for _ in range(8))
        text = "Codex session in /w/x\n\n" + turns
        starts = views.page_bounds(text, views.turn_starts(text))
        self.assertGreater(len(starts), 2)
        for start in starts[1:]:
            self.assertRegex(text[start:], r"^(USER|ASSISTANT): ")

    def test_a_page_past_the_end_is_the_last_one_and_an_unknown_item_is_not_found(self):
        self.add("notes", "n", "2026-09-01T10:00:00Z", "note", "line\n" * 20000, {"path": "/n"})
        self.serve()
        last = self.api("item?id=1&page=99")
        self.assertEqual(last["page"], last["pages"] - 1)
        self.assertEqual(last["end"], last["total"])
        self.assertEqual(self.get("/api/item?id=999")[0], 404)
        self.assertEqual(self.get("/api/project?key=nothing")[0], 404)

    def test_a_search_hit_opens_at_its_place(self):
        self.add("notes", "n", "2026-09-01T10:00:00Z", "note", "filler line\n" * 5000 + "the unique needle here", {"path": "/n"})
        self.serve()
        hits = self.api("search?q=needle")
        self.assertEqual(len(hits), 1)
        page = self.api(f"item?id={hits[0]['id']}&q=needle")
        self.assertIn("needle", page["text"])
        self.assertGreater(page["page"], 0)


class SafetyTest(Served):
    def setUp(self):
        super().setUp()
        self.add("notes", "n", "2026-09-01T10:00:00Z", "<img src=x onerror=alert(1)>", "body", {"path": "/n", "url": "javascript:alert(1)"})
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.serve()

    def files(self):
        """The store's file with its bytes and time, and any write-ahead file beside it with its size. A read-only
        connection can leave an empty one, which holds nothing."""
        folder = Path(self.paths.db).parent
        main = folder / "superhistory.sqlite"
        wal = folder / "superhistory.sqlite-wal"
        return (hashlib.sha256(main.read_bytes()).hexdigest(), main.stat().st_mtime_ns,
                wal.stat().st_size if wal.exists() else 0)

    def test_only_a_loopback_address_binds(self):
        for host in ("0.0.0.0", "", "::", "::1", "localhost", "192.168.1.4", "example.com", "8.8.8.8"):
            with self.assertRaises(server.NotLoopback, msg=host):
                server.make_server(self.paths, config_mod.Config(), host=host)
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")
        for host in ("127.0.0.1", "127.0.0.2"):
            other = server.make_server(self.paths, config_mod.Config(), host=host)
            self.assertEqual(other.server_address[0], host)
            other.server_close()

    def test_a_session_of_requests_leaves_the_store_as_it_was(self):
        before = self.files()
        for path in ("/", "/projects", "/people", "/api/overview", "/api/projects", "/api/people", "/api/source?name=notes",
                     "/api/search?q=body", "/api/item?id=1", "/api/item?id=1&page=3", "/search?q=x", "/item/1", "/nope",
                     "/api/bogus", "/api/project?key=x"):
            self.get(path)
        self.assertEqual(self.files(), before)

    def test_nothing_but_get_and_head_is_answered(self):
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            request = urllib.request.Request(self.srv.url.split("?")[0] + "api/overview",
                                             data=b"x" if method != "OPTIONS" else None, method=method,
                                             headers=self.keyed())
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(caught.exception.code, 405, method)
            self.assertEqual(caught.exception.headers["Allow"], "GET, HEAD")
        head = urllib.request.Request(self.srv.url.split("?")[0] + "api/overview", method="HEAD", headers=self.keyed())
        with urllib.request.urlopen(head) as res:
            self.assertEqual((res.status, res.read()), (200, b""))

    def test_the_store_is_opened_read_only(self):
        db = server.Dashboard(self.paths, config_mod.Config()).connect()
        with self.assertRaises(Exception):
            db.execute("DELETE FROM docs")
        db.close()

    def test_data_needs_this_runs_key_in_its_header_and_a_cookie_never_stands_in_for_it(self):
        self.keyed()
        for path in ("/api/overview", "/api/item?id=1", "/api/projects", "/api/search?q=body"):
            self.assertEqual(self.get(path, key=False)[0], 403, path)
            wrong = {server.KEY_HEADER: "0" * 32}
            self.assertEqual(self.get(path, wrong, key=False)[0], 403, path)
            for jar in (f"superhistory-{self.srv.server_address[1]}={self.srv.session}", f"{server.KEY_HEADER}={self.srv.session}",
                        f"access={self.srv.session}", "junk;;="):
                self.assertEqual(self.get(path, {"Cookie": jar}, key=False)[0], 403, (path, jar))
            self.assertEqual(self.get(path)[0], 200, path)
        self.assertEqual(self.get("/api/overview?access=" + self.srv.session, key=False)[0], 403)
        self.assertEqual(self.get("/api/overview?key=" + self.srv.session, key=False)[0], 403)

    def test_the_page_and_its_files_hold_no_data_and_need_no_key(self):
        for path in ("/", "/projects", "/item/1", "/static/app.js", "/static/app.css"):
            status, body, headers = self.get(path, key=False)
            self.assertEqual(status, 200, path)
            self.assertNotIn(self.srv.token.encode(), body)
            self.assertIsNone(headers["Set-Cookie"])

    def test_the_printed_address_is_good_for_one_visit_and_hands_its_key_to_the_page(self):
        self.assertRegex(self.srv.url, r"^http://127\.0\.0\.1:\d+/\?access=[0-9a-f]{32}$")
        address = self.srv.url
        self.assertEqual(self.get("/?access=" + "0" * 32, key=False)[0], 403)
        head = urllib.request.Request(address, method="HEAD")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(head)
        self.assertEqual(caught.exception.code, 403, "a HEAD request must not spend the key")
        self.assertFalse(self.srv.spent.is_set())
        with urllib.request.urlopen(address) as res:
            body = res.read().decode()
            self.assertEqual(res.status, 200)
            self.assertIsNone(res.headers["Set-Cookie"])
        session = re.search(r'<meta name="superhistory-key" content="([0-9a-f]{32})">', body).group(1)
        self.assertEqual(session, self.srv.session)
        self.assertNotEqual(session, self.srv.token, "the page gets a new value, not the address's token")
        self.assertTrue(self.srv.spent.is_set())
        for again in (address, address.replace("/?", "/projects?"), self.srv.url.split("?")[0] + "api/overview?access="
                      + self.srv.token):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(again, headers={server.KEY_HEADER: session}))
            self.assertEqual(caught.exception.code, 403, again)
        status, data, _ = self.get("/api/overview")
        self.assertEqual(status, 200, "the session key works after the address is spent")
        self.assertNotIn(self.srv.token.encode(), data)
        self.assertNotIn(session.encode(), data)

    def test_the_token_in_the_printed_address_opens_no_data_before_or_after_its_visit(self):
        token = self.srv.token
        for when in ("before the visit", "after the visit"):
            for path in ("/api/overview", "/api/projects", "/api/item?id=1"):
                self.assertEqual(self.get(path, {server.KEY_HEADER: token}, key=False)[0], 403, (when, path))
            if when == "before the visit":
                with urllib.request.urlopen(self.srv.url) as res:
                    res.read()

    def test_the_script_keeps_the_key_in_the_tab_and_never_in_a_cookie(self):
        script = (Path(server.STATIC) / "app.js").read_text(encoding="utf-8")
        self.assertIn("sessionStorage", script)
        self.assertIn("X-Superhistory-Key", script)
        self.assertNotIn("document.cookie", script)

    def test_a_request_for_another_host_name_is_refused(self):
        self.assertEqual(self.get("/api/overview", {"Host": "evil.example"})[0], 403)
        self.assertEqual(self.get("/api/overview", {"Host": "127.0.0.1:1"})[0], 403)
        self.assertEqual(self.get("/api/overview", {"Host": f"localhost:{self.srv.server_address[1]}"})[0], 200)

    def test_every_response_forbids_outside_loads_and_scripts_that_are_not_its_own(self):
        for path in ("/", "/static/app.js", "/api/overview"):
            _, _, headers = self.get(path)
            policy = headers["Content-Security-Policy"]
            self.assertIn("default-src 'none'", policy)
            self.assertIn("script-src 'self'", policy)
            self.assertNotRegex(policy, r"https?:|\*")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")
            self.assertEqual(headers["Cross-Origin-Opener-Policy"], "same-origin")

    def test_a_number_that_is_not_plain_digits_is_the_default_and_never_an_error(self):
        for value in ("%C2%B2", "%D9%A3", "1e3", "-1", ""):
            self.assertEqual(self.get(f"/api/item?id={value}&page={value}")[0], 404, value)
            self.assertEqual(self.get(f"/api/source?name=notes&offset={value}")[0], 200, value)

    def test_the_port_is_not_shared_with_another_listener(self):
        self.assertFalse(server.Server.allow_reuse_address)

    def test_the_page_names_no_outside_address_and_ships_its_fonts(self):
        folder = Path(server.STATIC)
        for name in ("index.html", "app.css", "app.js"):
            text = (folder / name).read_text(encoding="utf-8")
            for found in re.findall(r"""(?:src|href|url\()\s*=?\s*["']?(https?:)?//[^"')\s]+""", text):
                self.fail(f"{name} loads from outside: {found}")
            self.assertNotRegex(text, r"@import|fonts\.googleapis|cdn\.", name)
        for name in ("inter-latin-wght.woff2", "jetbrains-mono-latin-400.woff2", "newsreader-latin-500.woff2", "OFL.txt"):
            self.assertEqual(self.get("/static/" + name)[0], 200, name)
        self.assertEqual(self.get("/static/missing.js")[0], 404)
        self.assertEqual(self.get("/static/../server.py")[0], 404)
        self.assertEqual(self.get("/static/%2e%2e/server.py")[0], 404)

    def test_a_link_that_is_not_a_web_or_app_address_is_not_given(self):
        self.assertIsNone(self.api("item?id=1")["url"])
        self.assertIsNone(views.safe_url("javascript:alert(1)"))
        self.assertEqual(views.safe_url("https://x.example/a"), "https://x.example/a")


class HealthTest(Served):
    LOG = ("2026-11-05 09:00:00,100 INFO notes@studio: 3 items read, 3 changed, 0 pruned in 0.1s\n"
           "2026-11-05 09:00:01,200 INFO ingest done: 0 failures\n"
           "2026-11-05 10:00:00,100 ERROR gmail@studio failed\nTraceback (most recent call last):\n  File \"x\", line 1\n"
           "OSError: could not reach the mail server\n"
           "2026-11-05 10:00:02,200 INFO ingest done: 1 failures\n")

    def test_the_log_gives_runs_and_each_failure_with_its_reason(self):
        path = Path(self.paths.db).parent / "ingest.log"
        path.write_text(self.LOG)
        found = views.read_log(path, now=views.datetime(2026, 11, 6, 9, 0))
        self.assertEqual([r["failures"] for r in found["runs"]], [0, 1])
        self.assertEqual(found["runs"][1]["failed"], ["gmail@studio"])
        self.assertEqual([(f["what"], f["reason"]) for f in found["failures"]],
                         [("gmail@studio", "OSError: could not reach the mail server")])
        self.assertEqual(views.read_log(Path(self.paths.db).parent / "missing.log")["log"], False)

    def test_an_old_health_entry_whose_name_holds_a_secret_is_masked_in_the_overview(self):
        secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
        store.record_ok(self.db, f"{secret}@{secret}")
        store.record_ok(self.db, f"notes@{secret}")
        store.set_watermark(self.db, store.STATE, f"fail:{secret}@here", f"{store.now_iso()} no {secret}")
        store.set_watermark(self.db, store.STATE, f"away:{secret}", store.now_iso())
        self.db.commit()
        self.serve()
        self.assertNotIn(secret.encode(), self.get("/api/overview")[1])

    def test_a_failing_source_shows_in_the_overview_with_its_reason(self):
        self.add("notes", "n", "2026-09-01T10:00:00Z", "n", "x", {"path": "/n"})
        self.db.commit()
        store.record_ok(self.db, "notes@here")
        self.db.commit()
        store.record_failure(self.db, "gmail@here", RuntimeError("the server said no"))
        store.set_watermark(self.db, store.STATE, "last_ingest", store.now_iso())
        os.makedirs(self.paths.log_dir)
        (self.paths.log("ingest")).write_text(self.LOG)
        self.serve()
        overview = self.api("overview")
        self.assertEqual([(f["unit"], f["error"]) for f in overview["failing"]], [("gmail@here", "the server said no")])
        by_name = {s["name"]: s for s in overview["sources"]}
        self.assertTrue(by_name["notes"]["last_ok"])
        self.assertEqual(by_name["gmail"]["failing"][0]["error"], "the server said no")
        self.assertEqual(overview["failures"][0]["what"], "gmail@studio")

    def test_an_empty_store_serves_an_empty_overview(self):
        self.serve()
        overview = self.api("overview")
        self.assertEqual((overview["items"], overview["sources"], overview["project_total"]), (0, [], 0))


class CommandTest(unittest.TestCase):
    def run_cli(self, *args, env=None):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"SUPERHISTORY_HOME": home}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args)) if env is None else env(home, args)
        return code, out.getvalue(), err.getvalue()

    def test_without_a_store_it_says_what_to_do_and_listens_nowhere(self):
        with mock.patch.object(server.Server, "serve_forever") as serving:
            code, _, err = self.run_cli("dashboard", "--no-open")
        self.assertEqual(code, 1)
        self.assertIn("No knowledge base yet", err)
        serving.assert_not_called()

    def test_it_prints_its_address_and_stops_cleanly_on_ctrl_c(self):
        def run(home, args):
            paths = Paths.under(home)
            store.connect(paths.db).close()
            with mock.patch.object(server.Server, "serve_forever", side_effect=KeyboardInterrupt), \
                    mock.patch("webbrowser.open") as opened:
                code = cli.main(list(args))
            opened.assert_not_called()
            return code
        code, out, _ = self.run_cli("dashboard", "--no-open", env=run)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"http://127\.0\.0\.1:\d+/\?access=[0-9a-f]{32}\n")
        self.assertIn("Stopped.", out)

    def run_opening(self, during):
        """The command with a browser to open, serve_forever replaced by during(server, seen), and the browser's
        opener recorded in seen["opened"]."""
        seen = {}

        def run(home, args):
            store.connect(Paths.under(home).db).close()

            def serving(srv):
                seen["server"] = srv
                during(srv, seen)
                raise KeyboardInterrupt
            with mock.patch.object(server.Server, "serve_forever", autospec=True, side_effect=serving), \
                    mock.patch("webbrowser.open", return_value=True) as opened:
                seen["opened"] = opened
                return cli.main(list(args))
        code, out, err = self.run_cli("dashboard", env=run)
        return code, out, seen

    @staticmethod
    def handoff_path(seen):
        (argument,), kwargs = seen["opened"].call_args
        assert not kwargs
        return argument, urllib.request.url2pathname(urlparse(argument).path)

    def test_the_browser_gets_a_private_file_and_never_the_key(self):
        def during(srv, seen):
            self.assertEqual(seen["opened"].call_count, 1)
            argument, path = self.handoff_path(seen)
            self.assertTrue(argument.startswith("file://"), argument)
            self.assertNotIn(srv.token, repr(seen["opened"].call_args))
            self.assertNotIn(srv.token, argument)
            folder = os.path.dirname(path)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)
            text = Path(path).read_text(encoding="utf-8")
            self.assertIn('http-equiv="refresh"', text)
            self.assertIn(srv.url, text)
            seen["path"] = path

        code, out, seen = self.run_opening(during)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"http://127\.0\.0\.1:\d+/\?access=[0-9a-f]{32}\n")
        self.assertFalse(os.path.exists(os.path.dirname(seen["path"])), "removed when the dashboard stops")

    def test_the_file_is_removed_once_the_address_has_been_used(self):
        def during(srv, seen):
            _, path = self.handoff_path(seen)
            self.assertTrue(os.path.exists(path))
            self.assertTrue(srv.spend(srv.token))
            for _ in range(100):
                if not os.path.exists(os.path.dirname(path)):
                    break
                time.sleep(0.05)
            seen["gone"] = not os.path.exists(os.path.dirname(path))
        code, _, seen = self.run_opening(during)
        self.assertEqual(code, 0)
        self.assertTrue(seen["gone"], "the file outlived the key it holds")

    def test_no_browser_found_leaves_the_printed_address_and_still_cleans_up(self):
        seen = {}

        def run(home, args):
            store.connect(Paths.under(home).db).close()
            with mock.patch.object(server.Server, "serve_forever", side_effect=KeyboardInterrupt), \
                    mock.patch("webbrowser.open", return_value=False) as opened:
                code = cli.main(list(args))
            seen["path"] = opened.call_args[0][0]
            return code
        code, out, _ = self.run_cli("dashboard", env=run)
        self.assertEqual(code, 0)
        self.assertIn("No browser was found", out)
        self.assertFalse(os.path.exists(os.path.dirname(urllib.request.url2pathname(urlparse(seen["path"]).path))))

    def test_a_port_in_use_is_reported(self):
        def run(home, args):
            paths = Paths.under(home)
            store.connect(paths.db).close()
            taken = server.make_server(paths, config_mod.Config())
            try:
                return cli.main(["dashboard", "--no-open", "--port", str(taken.server_address[1])])
            finally:
                taken.server_close()
        code, _, err = self.run_cli(env=run)
        self.assertEqual(code, 1)
        self.assertIn("cannot listen on port", err)


if __name__ == "__main__":
    unittest.main()
