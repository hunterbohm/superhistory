import json
import os
import unittest

from kb import search, store
from kb.sources import plugin
from tests.fake_http import FakeServer
from tests.test_plugin import PluginCase, SECRET

NAME = "notion"
TOKEN = "ntn_test_token_value_1"


def page(pid, title, edited, **extra):
    return {"object": "page", "id": pid, "created_time": "2026-10-01T08:00:00.000Z", "last_edited_time": edited,
            "archived": False, "url": f"https://www.notion.so/{pid}",
            "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}}, **extra}


def para(text, bid=None, **extra):
    return {"id": bid or "b-" + text[:6], "type": "paragraph", "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": text}]}, **extra}


class NotionCase(PluginCase):
    LIST_SIZE = 2

    def setUp(self):
        super().setUp()
        plugin.install(self.dir, "notion")
        self.token = os.path.join(self.root, "notion.token")
        with open(self.token, "w") as fh:
            fh.write(TOKEN + "\n")
        self.pages = []
        self.blocks = {}
        self.queue = []
        self.block_queue = []
        self.server = FakeServer(self.answer)
        self.server.__enter__()
        self.addCleanup(self.server.__exit__)

    def answer(self, seen):
        if self.queue:
            return self.queue.pop(0)
        if seen.path.startswith("/blocks") and self.block_queue:
            return self.block_queue.pop(0)
        if seen.headers["Authorization"] != f"Bearer {TOKEN}":
            return 401, {"object": "error", "code": "unauthorized"}
        if seen.path == "/search":
            ordered = sorted(self.pages, key=lambda p: p["last_edited_time"], reverse=True)
            start = int(seen.body.get("start_cursor") or 0)
            chunk = ordered[start:start + self.LIST_SIZE]
            more = start + self.LIST_SIZE < len(ordered)
            return 200, {"results": chunk, "has_more": more, "next_cursor": str(start + self.LIST_SIZE) if more else None}
        block_id = seen.path.split("/")[2]
        if block_id not in self.blocks:
            return 404, {"object": "error", "code": "object_not_found"}
        blocks = self.blocks[block_id]
        start = int(seen.query.get("start_cursor", ["0"])[0])
        more = start + self.LIST_SIZE < len(blocks)
        return 200, {"results": blocks[start:start + self.LIST_SIZE], "has_more": more,
                     "next_cursor": str(start + self.LIST_SIZE) if more else None}

    def cfg(self, settings=""):
        return self.config(**{NAME: ("notion", "\n".join([
            f"[sources.{NAME}.settings]", f'base_url = "{self.server.url}"', "min_interval_seconds = 0", settings,
            f"[sources.{NAME}.secrets]", f'token = "file:{self.token}"']))})

    def searches(self):
        return [r for r in self.server.requests if r.path == "/search"]

    def watermark(self):
        return json.loads(store.get_watermark(self.db, NAME, "m1"))

    def stored(self, text):
        hit = search.search(self.db, text)
        return [store.item_rows(self.db, h["id"])[0][4] for h in hit]


class ReadingTest(NotionCase):
    def test_a_first_run_stores_a_page_with_its_title_blocks_and_properties_found_by_search(self):
        row = page("p1", "Launch plan", "2026-11-03T10:15:00.000Z", properties={
            "Name": {"type": "title", "title": [{"plain_text": "Launch plan"}]},
            "Status": {"type": "status", "status": {"name": "Narwhal review"}},
            "Tags": {"type": "multi_select", "multi_select": [{"name": "alpha"}, {"name": "beta"}]}})
        self.pages = [row]
        self.blocks["p1"] = [
            {"id": "h", "type": "heading_1", "has_children": False, "heading_1": {"rich_text": [{"plain_text": "Goals"}]}},
            para("ship the zebrafish report"),
            {"id": "t", "type": "to_do", "has_children": True,
             "to_do": {"rich_text": [{"plain_text": "write notes"}], "checked": True}},
            {"id": "c", "type": "child_page", "has_children": True, "child_page": {"title": "Sub page title"}},
            {"id": "r", "type": "table_row", "has_children": False,
             "table_row": {"cells": [[{"plain_text": "left"}], [{"plain_text": "right"}]]}}]
        self.blocks["t"] = [para("nested quokka detail")]
        self.assertEqual(self.ingest(self.cfg()), 0)
        for words in ("Launch plan", "Goals", "zebrafish", "quokka", "Narwhal", "alpha", "left right", "Sub page"):
            self.assertEqual(self.titles(words), ["Launch plan"], words)
        hit = search.search(self.db, "zebrafish")[0]
        self.assertEqual(search.pointer(hit["meta"]), "https://www.notion.so/p1")
        rows = store.item_rows(self.db, hit["id"])
        self.assertEqual(rows[0][2], "2026-11-03T10:15:00Z")
        self.assertIn("[x] write notes\n  nested quokka detail", rows[0][4])
        self.assertIn("Status: Narwhal review", rows[0][4])
        self.assertNotIn("p1", "".join(self.server.paths()[1:]).replace("/blocks/p1/children", ""))
        self.assertEqual(self.server.requests[0].body["sort"],
                         {"direction": "descending", "timestamp": "last_edited_time"})
        self.assertEqual(self.server.requests[0].body["filter"], {"property": "object", "value": "page"})
        self.assertEqual(self.server.requests[0].headers["Notion-Version"], "2022-06-28")

    def test_the_search_and_a_pages_blocks_are_read_to_the_last_page(self):
        self.pages = [page(f"p{i}", f"Topic number{i}", f"2026-11-0{i}T10:00:00.000Z") for i in range(1, 6)]
        self.blocks = {f"p{i}": [para(f"first{i} line"), para(f"second{i} line"), para(f"third{i} line")]
                       for i in range(1, 6)}
        self.assertEqual(self.ingest(self.cfg()), 0)
        for i in range(1, 6):
            self.assertEqual(self.titles(f"number{i}"), [f"Topic number{i}"])
            self.assertEqual(self.titles(f"third{i}"), [f"Topic number{i}"])
        self.assertEqual(len(self.searches()), 3)
        self.assertEqual(self.searches()[1].body["start_cursor"], "2")
        cursors = [r.query.get("start_cursor") for r in self.server.requests if r.path == "/blocks/p1/children"]
        self.assertEqual(cursors, [None, ["2"]])

    def test_a_page_with_no_title_or_blocks_is_still_kept(self):
        self.pages = [page("p1", "", "2026-11-03T10:15:00.000Z")]
        self.blocks["p1"] = []
        self.assertEqual(self.ingest(self.cfg()), 0)
        self.assertEqual(self.titles("untitled"), ["(untitled)"])


class WatermarkTest(NotionCase):
    def test_the_next_run_stops_listing_at_the_pages_it_has_read(self):
        self.pages = [page(f"p{i}", f"Topic number{i}", f"2026-11-0{i}T10:00:00.000Z") for i in range(1, 7)]
        self.blocks = {f"p{i}": [para(f"body{i}")] for i in range(1, 7)}
        cfg = self.cfg()
        self.ingest(cfg)
        self.assertEqual(self.watermark(), "2026-11-06T09:50:00.000Z")
        before = len(self.server.requests)
        self.pages.append(page("p7", "Brand new thing", "2026-11-08T12:00:00.000Z"))
        self.blocks["p7"] = [para("quokka arrival")]
        self.ingest(cfg)
        later = self.server.requests[before:]
        self.assertEqual([r.path for r in later],
                         ["/search", "/search", "/blocks/p6/children", "/blocks/p7/children"])
        self.assertEqual(self.titles("quokka"), ["Brand new thing"])
        self.assertEqual(self.titles("number1"), ["Topic number1"])
        self.assertEqual(self.watermark(), "2026-11-08T11:50:00.000Z")

    def test_the_newest_pages_are_read_again_once_in_case_the_search_was_late_to_show_an_edit(self):
        self.pages = [page("p1", "Late edit", "2026-11-03T10:05:00.000Z")]
        self.blocks["p1"] = [para("version one")]
        cfg = self.cfg()
        self.ingest(cfg)
        self.blocks["p1"] = [para("version two quokka")]
        self.ingest(cfg)
        self.assertEqual(self.titles("quokka"), ["Late edit"])

    def test_an_edited_page_replaces_its_item(self):
        self.pages = [page("p1", "Plan", "2026-11-03T10:00:00.000Z")]
        self.blocks["p1"] = [para("old wording")]
        cfg = self.cfg()
        self.ingest(cfg)
        self.pages = [page("p1", "Plan", "2026-11-09T10:00:00.000Z")]
        self.blocks["p1"] = [para("new wording quokka")]
        self.ingest(cfg)
        self.assertEqual(self.titles("quokka"), ["Plan"])
        self.assertEqual(self.titles("old"), [])

    def test_run_cap_reads_the_oldest_pages_first_and_the_next_run_goes_on_from_there(self):
        self.pages = [page(f"p{i}", f"Topic number{i}", f"2026-11-0{i}T10:00:00.000Z") for i in range(1, 6)]
        self.blocks = {f"p{i}": [para(f"body{i}")] for i in range(1, 6)}
        cfg = self.cfg("run_cap = 2")
        self.ingest(cfg)
        self.assertEqual(self.watermark(), "2026-11-02T10:00:00.000Z")
        self.assertEqual(self.titles("number1"), ["Topic number1"])
        self.assertEqual(self.titles("number3"), [])
        self.ingest(cfg)
        self.assertEqual(self.watermark(), "2026-11-04T10:00:00.000Z")
        self.assertEqual(self.titles("number4"), ["Topic number4"])
        self.assertEqual(self.titles("number5"), [])
        self.ingest(cfg)
        self.assertEqual(self.titles("number5"), ["Topic number5"])
        self.assertEqual(self.watermark(), "2026-11-05T09:50:00.000Z")


class TieAtTheCapTest(NotionCase):
    def test_pages_edited_in_the_same_minute_as_the_last_one_read_are_read_with_it(self):
        self.pages = [page(f"p{i}", f"Topic number{i}", "2026-11-03T10:00:00.000Z") for i in range(1, 4)] + [
            page("p4", "Topic number4", "2026-11-04T10:00:00.000Z")]
        self.blocks = {f"p{i}": [para(f"body{i}")] for i in range(1, 5)}
        cfg = self.cfg("run_cap = 1")
        self.ingest(cfg)
        self.assertEqual([self.titles(f"number{i}") != [] for i in range(1, 5)], [True, True, True, False])
        self.assertEqual(self.watermark(), "2026-11-03T10:00:00.000Z")
        self.ingest(cfg)
        self.assertEqual(self.titles("number4"), ["Topic number4"])


class RemovedTest(NotionCase):
    def test_an_archived_page_leaves_the_knowledge_base(self):
        self.pages = [page("p1", "Zebrafish notes", "2026-11-03T10:00:00.000Z"),
                      page("p2", "Keeper notes", "2026-11-02T10:00:00.000Z")]
        self.blocks = {"p1": [para("zebrafish")], "p2": [para("keeper")]}
        cfg = self.cfg()
        self.ingest(cfg)
        self.assertEqual(self.titles("zebrafish"), ["Zebrafish notes"])
        self.pages = [page("p1", "Zebrafish notes", "2026-11-09T10:00:00.000Z", archived=True)]
        self.assertEqual(self.ingest(cfg), 0)
        self.assertEqual(self.titles("zebrafish"), [])
        self.assertEqual(self.titles("keeper"), ["Keeper notes"])

    def test_a_page_whose_blocks_cannot_be_read_is_dropped_and_the_others_are_kept(self):
        self.pages = [page("p1", "Hidden page", "2026-11-03T10:00:00.000Z"),
                      page("p2", "Visible page", "2026-11-04T10:00:00.000Z")]
        self.blocks = {"p2": [para("quokka")]}
        self.assertEqual(self.ingest(self.cfg()), 0)
        self.assertEqual(self.titles("hidden"), [])
        self.assertEqual(self.titles("quokka"), ["Visible page"])


class LimitsAndFailuresTest(NotionCase):
    def test_a_429_waits_for_retry_after_and_tries_again(self):
        self.pages = [page("p1", "Zebrafish notes", "2026-11-03T10:00:00.000Z")]
        self.blocks = {"p1": [para("zebrafish")]}
        self.queue = [(429, {"object": "error", "code": "rate_limited"}, {"Retry-After": "0"})]
        self.assertEqual(self.ingest(self.cfg()), 0)
        self.assertEqual(self.titles("zebrafish"), ["Zebrafish notes"])
        self.assertEqual(len(self.searches()), 2)

    def test_a_429_while_reading_blocks_is_waited_out_too(self):
        self.pages = [page("p1", "Zebrafish notes", "2026-11-03T10:00:00.000Z")]
        self.blocks = {"p1": [para("zebrafish")]}
        self.block_queue = [(429, {"object": "error", "code": "rate_limited"}, {"Retry-After": "0"})]
        self.assertEqual(self.ingest(self.cfg()), 0)
        self.assertEqual(self.titles("zebrafish"), ["Zebrafish notes"])
        self.assertEqual(len([r for r in self.server.requests if r.path.startswith("/blocks")]), 2)

    def test_a_refused_token_says_so_without_the_token_and_stores_nothing(self):
        self.pages = [page("p1", "Zebrafish notes", "2026-11-03T10:00:00.000Z")]
        self.blocks = {"p1": [para("zebrafish")]}
        with open(self.token, "w") as fh:
            fh.write("ntn_some_other_token_9\n")
        self.assertEqual(self.ingest(self.cfg()), 1)
        reason = self.failing()[f"{NAME}@m1"]
        self.assertIn("Notion refused the token", reason)
        self.assertNotIn("ntn_some_other_token_9", reason)
        self.assertEqual(self.titles("zebrafish"), [])
        self.assertIsNone(store.get_watermark(self.db, NAME, "m1"))

    def test_a_missing_token_says_what_is_needed(self):
        cfg = self.config(**{NAME: ("notion", f'secrets = {{ other = "file:{self.token}" }}')})
        self.assertEqual(self.ingest(cfg), 1)
        self.assertIn("needs a token", self.failing()[f"{NAME}@m1"])

    def test_a_secret_in_a_page_body_is_masked_before_it_is_stored(self):
        self.pages = [page("p1", "Deploy notes", "2026-11-03T10:00:00.000Z")]
        self.blocks = {"p1": [para(f"the key is {SECRET} for the zebrafish deploy"),
                              {"id": "k", "type": "code", "has_children": False,
                               "code": {"rich_text": [{"plain_text": f"export KEY={SECRET}"}]}}]}
        self.assertEqual(self.ingest(self.cfg()), 0)
        self.assertEqual(self.titles("zebrafish"), ["Deploy notes"])
        stored = json.dumps(self.db.execute("SELECT title, content, metadata FROM docs").fetchall())
        self.assertNotIn(SECRET, stored)

    def test_the_token_is_not_in_the_stored_rows_or_the_failure(self):
        self.pages = [page("p1", "Notes", "2026-11-03T10:00:00.000Z")]
        self.blocks = {"p1": [para("quokka")]}
        self.ingest(self.cfg())
        stored = json.dumps(self.db.execute("SELECT * FROM docs").fetchall())
        self.assertNotIn(TOKEN, stored)


if __name__ == "__main__":
    unittest.main()
