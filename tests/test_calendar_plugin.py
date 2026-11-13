import json
import os
import unittest

from kb import search, store
from kb.sources import plugin
from tests.fake_http import FakeServer
from tests.test_plugin import PluginCase, SECRET

NAME = "cal"


def event(eid, summary, updated="2026-11-01T10:00:00.000Z", **extra):
    return {"id": eid, "status": "confirmed", "summary": summary, "updated": updated,
            "htmlLink": f"https://calendar.example/event/{eid}",
            "start": {"dateTime": "2026-11-10T09:00:00-06:00"}, "end": {"dateTime": "2026-11-10T10:00:00-06:00"},
            **extra}


class CalendarCase(PluginCase):
    def setUp(self):
        super().setUp()
        plugin.install(self.dir, "google-calendar")
        for name, value in (("client-secret", "client-secret-1"), ("refresh-token", "refresh-1")):
            with open(os.path.join(self.root, name), "w") as fh:
                fh.write(value + "\n")
        self.token_status = (200, {"access_token": "access-1"})
        self.pages = {None: ([], None)}
        self.queue = []
        self.server = FakeServer(self.answer)
        self.server.__enter__()
        self.addCleanup(self.server.__exit__)

    def answer(self, seen):
        if seen.path == "/token":
            return self.token_status
        if self.queue:
            return self.queue.pop(0)
        if seen.headers["Authorization"] != "Bearer access-1":
            return 401, {"error": {"errors": [{"reason": "authError"}]}}
        items, more = self.pages[(seen.query.get("pageToken") or [None])[0]]
        return 200, {"items": items, **({"nextPageToken": more} if more else {})}

    def cfg(self, settings=""):
        return self.config(**{NAME: ("google-calendar", "\n".join([
            f"[sources.{NAME}.settings]", 'client_id = "id-1.apps.example.com"',
            f'base_url = "{self.server.url}"', f'token_url = "{self.server.url}/token"', settings,
            f"[sources.{NAME}.secrets]",
            f'client_secret = "file:{os.path.join(self.root, "client-secret")}"',
            f'refresh_token = "file:{os.path.join(self.root, "refresh-token")}"']))})

    def event_requests(self):
        return [r for r in self.server.requests if r.path.endswith("/events")]

    def watermark(self):
        return json.loads(store.get_watermark(self.db, NAME, "m1"))


class ReadingTest(CalendarCase):
    def test_a_first_run_stores_events_found_by_what_they_hold_with_a_link_and_a_date(self):
        self.pages[None] = ([event(
            "e1", "Quarterly planning", location="Room 4, Elm Street",
            organizer={"displayName": "Maya Ortiz", "email": "maya@example.com"},
            attendees=[{"displayName": "Priya Nair", "email": "priya@example.com", "responseStatus": "accepted"},
                       {"email": "lee@example.com"}],
            description="<p>Agenda:</p><b>budget</b> and the <a href=\"https://docs.example/q\">plan</a><br>zebrafish")],
            None)
        self.assertEqual(self.ingest(self.cfg()), 0)
        for words in ("Quarterly planning", "Priya Nair", "priya@example.com", "Elm Street", "Maya Ortiz",
                      "zebrafish", "Tuesday"):
            self.assertEqual(self.titles(words), ["Quarterly planning"], words)
        hit = search.search(self.db, "zebrafish")[0]
        self.assertEqual(hit["source"], NAME)
        self.assertEqual(search.pointer(hit["meta"]), "https://calendar.example/event/e1")
        text = store.item_rows(self.db, hit["id"])[0][4]
        self.assertIn("Attendees: Priya Nair <priya@example.com> (accepted), lee@example.com", text)
        self.assertIn("plan (https://docs.example/q)", text)
        self.assertNotIn("<b>", text)
        self.assertEqual(store.item_rows(self.db, hit["id"])[0][2], "2026-11-10T15:00:00Z")
        first = self.event_requests()[0]
        self.assertNotIn("updatedMin", first.query)
        self.assertEqual(first.query["singleEvents"], ["true"])
        self.assertEqual(self.server.requests[0].body["refresh_token"], ["refresh-1"])
        self.assertEqual(self.server.requests[0].body["client_secret"], ["client-secret-1"])

    def test_an_all_day_event_is_dated_by_its_day(self):
        self.pages[None] = ([event("d1", "Offsite", start={"date": "2026-12-01"}, end={"date": "2026-12-02"})], None)
        self.ingest(self.cfg())
        hit = search.search(self.db, "Offsite")[0]
        self.assertEqual(store.item_rows(self.db, hit["id"])[0][2], "2026-12-01T00:00:00Z")

    def test_every_page_is_read(self):
        self.pages = {None: ([event("a", "First alpha")], "p2"), "p2": ([event("b", "Second beta")], "p3"),
                      "p3": ([event("c", "Third gamma")], None)}
        self.assertEqual(self.ingest(self.cfg()), 0)
        self.assertEqual(self.titles("alpha"), ["First alpha"])
        self.assertEqual(self.titles("beta"), ["Second beta"])
        self.assertEqual(self.titles("gamma"), ["Third gamma"])
        self.assertEqual([r.query.get("pageToken") for r in self.event_requests()], [None, ["p2"], ["p3"]])

    def test_each_listed_calendar_is_read(self):
        self.pages[None] = ([event("a", "Shared holiday")], None)
        self.ingest(self.cfg('calendar_ids = ["primary", "team@example.com"]'))
        self.assertEqual(sorted(r.path for r in self.event_requests()),
                         ["/calendars/primary/events", "/calendars/team%40example.com/events"])


class WatermarkTest(CalendarCase):
    def test_the_next_run_asks_only_for_what_changed_since_the_newest_update(self):
        self.pages[None] = ([event("a", "Old standup", updated="2026-11-01T10:00:00.000Z"),
                             event("b", "Newer review", updated="2026-11-03T08:30:00.250Z")], None)
        cfg = self.cfg()
        self.ingest(cfg)
        self.assertEqual(self.watermark(), "2026-11-03T08:30:00.250Z")
        self.pages[None] = ([event("c", "Fresh retro", updated="2026-11-05T09:00:00.000Z")], None)
        self.ingest(cfg)
        self.assertEqual(self.event_requests()[-1].query["updatedMin"], ["2026-11-03T08:30:00.250Z"])
        self.assertEqual(self.watermark(), "2026-11-05T09:00:00.000Z")
        self.assertEqual(self.titles("standup"), ["Old standup"])
        self.assertEqual(self.titles("retro"), ["Fresh retro"])

    def test_a_run_that_finds_nothing_keeps_the_watermark(self):
        self.pages[None] = ([event("a", "Old standup", updated="2026-11-01T10:00:00.000Z")], None)
        cfg = self.cfg()
        self.ingest(cfg)
        self.pages[None] = ([], None)
        self.ingest(cfg)
        self.assertEqual(self.watermark(), "2026-11-01T10:00:00.000Z")

    def test_an_empty_first_run_still_sets_a_watermark_so_the_next_run_is_incremental(self):
        cfg = self.cfg()
        self.assertEqual(self.ingest(cfg), 0)
        self.assertRegex(self.watermark(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.ingest(cfg)
        self.assertIn("updatedMin", self.event_requests()[-1].query)

    def test_an_edited_event_replaces_its_item(self):
        self.pages[None] = ([event("a", "Planning", updated="2026-11-01T10:00:00.000Z")], None)
        cfg = self.cfg()
        self.ingest(cfg)
        self.pages[None] = ([event("a", "Planning moved", updated="2026-11-02T10:00:00.000Z", location="Narwhal Hall")],
                            None)
        self.ingest(cfg)
        self.assertEqual(self.titles("Narwhal"), ["Planning moved"])
        self.assertEqual(self.titles("Planning"), ["Planning moved"])


class CancelledTest(CalendarCase):
    def test_a_cancelled_event_leaves_the_knowledge_base(self):
        self.pages[None] = ([event("a", "Zebrafish sync"), event("b", "Keeper lunch")], None)
        cfg = self.cfg()
        self.ingest(cfg)
        self.assertEqual(self.titles("zebrafish"), ["Zebrafish sync"])
        self.pages[None] = ([{"id": "a", "status": "cancelled", "updated": "2026-11-04T10:00:00.000Z"}], None)
        self.assertEqual(self.ingest(cfg), 0)
        self.assertEqual(self.titles("zebrafish"), [])
        self.assertEqual(self.titles("lunch"), ["Keeper lunch"])
        self.assertEqual(self.event_requests()[-1].query["showDeleted"], ["true"])


class LimitsAndFailuresTest(CalendarCase):
    def test_a_429_waits_and_tries_again(self):
        self.pages[None] = ([event("a", "Zebrafish sync")], None)
        self.queue = [(429, {"error": {"errors": [{"reason": "rateLimitExceeded"}]}}, {"Retry-After": "0"})]
        self.assertEqual(self.ingest(self.cfg()), 0)
        self.assertEqual(self.titles("zebrafish"), ["Zebrafish sync"])
        self.assertEqual(len(self.event_requests()), 2)

    def test_googles_rate_limit_403_is_waited_out_too(self):
        self.pages[None] = ([event("a", "Zebrafish sync")], None)
        self.queue = [(403, {"error": {"errors": [{"reason": "userRateLimitExceeded"}]}}, {"Retry-After": "0"})]
        self.assertEqual(self.ingest(self.cfg()), 0)
        self.assertEqual(self.titles("zebrafish"), ["Zebrafish sync"])

    def test_a_429_that_asks_for_too_long_a_wait_fails_the_run_and_stores_nothing(self):
        self.pages[None] = ([event("a", "Zebrafish sync")], None)
        self.queue = [(429, {}, {"Retry-After": "100000"})]
        self.assertEqual(self.ingest(self.cfg()), 1)
        self.assertIn("try again later", self.failing()[f"{NAME}@m1"])
        self.assertEqual(self.titles("zebrafish"), [])

    def test_a_refused_refresh_token_says_to_sign_in_again_and_stores_nothing(self):
        self.pages[None] = ([event("a", "Zebrafish sync")], None)
        self.token_status = (400, {"error": "invalid_grant", "error_description": "Token has been revoked."})
        self.assertEqual(self.ingest(self.cfg()), 1)
        reason = self.failing()[f"{NAME}@m1"]
        self.assertIn("superhistory login", reason)
        self.assertIn("invalid_grant", reason)
        self.assertNotIn("refresh-1", reason)
        self.assertEqual(self.titles("zebrafish"), [])
        self.assertIsNone(store.get_watermark(self.db, NAME, "m1"))

    def test_missing_settings_say_what_is_needed(self):
        cfg = self.config(**{NAME: ("google-calendar", "")})
        self.assertEqual(self.ingest(cfg), 1)
        self.assertIn("needs client_id", self.failing()[f"{NAME}@m1"])

    def test_an_unknown_calendar_is_named(self):
        self.queue = [(404, {"error": {"errors": [{"reason": "notFound"}]}})]
        self.assertEqual(self.ingest(self.cfg('calendar_ids = ["nobody@example.com"]')), 1)
        self.assertIn("nobody@example.com", self.failing()[f"{NAME}@m1"])

    def test_a_secret_in_an_event_is_masked_before_it_is_stored(self):
        self.pages[None] = ([event("a", "Deploy call", description=f"use the key {SECRET} for the zebrafish deploy",
                                   location=f"bridge {SECRET}")], None)
        self.assertEqual(self.ingest(self.cfg()), 0)
        self.assertEqual(self.titles("zebrafish"), ["Deploy call"])
        stored = json.dumps(self.db.execute("SELECT title, content, metadata FROM docs").fetchall())
        self.assertNotIn(SECRET, stored)
        self.assertNotIn(SECRET, json.dumps(search.search(self.db, "zebrafish")))


if __name__ == "__main__":
    unittest.main()
