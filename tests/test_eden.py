import json
import os
import threading
import types
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from kb import config
from kb.config import Secret
from kb.sources import Excludes, Item, Progress, Run, eden

DAY = 86400
KEY = "eden-test-key-0123456789"


def ms(epoch):
    return int(epoch * 1000)


class FakeEden:
    """Answers eden.call the way Eden's tools do, from made-up posts, and records every call."""

    def __init__(self):
        self.posts = []          # newest first, as Eden's "recent" order returns them
        self.heads = set()       # ids of posts that start a thread
        self.threads = {}        # id -> later posts, each (id, author, body)
        self.whole = {}          # id -> whole body
        self.calls = []

    def add(self, pid, at, text, cut=False, platform="twitter", kind="tweet", title=None):
        post = {"contentId": f"c-{pid}", "platform": platform, "contentType": kind, "title": title,
                "contentUrl": f"https://x.com/sam/status/{pid}", "bodyPreview": text, "bodyTruncated": cut,
                "publishedAt": ms(at)}
        self.posts.append(post)
        self.posts.sort(key=lambda p: -p["publishedAt"])
        return post

    def __call__(self, key, tool, body):
        self.calls.append((tool, body))
        assert key.reveal() == KEY
        if tool == "eden_search_social_content":
            after = body.get("publishedAfter")
            posts = [p for p in self.posts if not after or datetime.fromtimestamp(
                p["publishedAt"] / 1000, timezone.utc).strftime("%Y-%m-%d") >= after]
            page = posts[body["offset"]:body["offset"] + body["limit"]]
            more = body["offset"] + body["limit"] < len(posts)
            return {"ok": True, "results": page, "hasMore": more,
                    "nextOffset": body["offset"] + body["limit"] if more else None}
        if tool == "eden_list_analytics_posts":
            return {"ok": True, "posts": [{"url": f"https://twitter.com/123/status/{pid}", "threadTotals": {"views": 1}}
                                          for pid in self.heads] + [{"url": "https://twitter.com/123/status/9"}]}
        if tool == "eden_read_social_post":
            pid = body["contentId"][2:]
            post = next(p for p in self.posts if p["contentId"] == body["contentId"])
            thread = {"isThread": pid in self.threads, "posts": [
                {"platformContentId": pid, "authorPlatformId": "a1", "body": post["bodyPreview"]}] + [
                {"platformContentId": cid, "authorPlatformId": author, "body": text}
                for cid, author, text in self.threads.get(pid, ())]}
            return {"ok": True, "contentId": body["contentId"], "body": self.whole.get(pid, post["bodyPreview"]),
                    "bodyTruncated": False, "thread": thread, "transcript": None}
        raise AssertionError(tool)

    def tools(self):
        return [tool for tool, _ in self.calls]

    def reads(self):
        return [body["contentId"][2:] for tool, body in self.calls if tool == "eden_read_social_post"]


class EdenCase(unittest.TestCase):
    def setUp(self):
        self.eden = FakeEden()
        self.now = float(int(datetime(2026, 11, 7, 12, tzinfo=timezone.utc).timestamp()))
        for name, value in (("call", self.eden), ("time", types.SimpleNamespace(time=lambda: self.now))):
            patcher = mock.patch.object(eden, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, {"EDEN_KEY": KEY})
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self, since=None, exclude=(), **options):
        options.setdefault("username", "@Sam")
        options.setdefault("key", Secret("env:EDEN_KEY"))
        return list(eden.TYPE.extract(Run(eden.Options(**options), Excludes(exclude), since)))

    def items(self, events):
        return [e for e in events if isinstance(e, Item)]


class EdenSourceTest(EdenCase):
    def setUp(self):
        super().setUp()
        self.upto = self.now - eden.SETTLE  # the newest publish time a run reads

    def test_first_run_pages_the_whole_history_and_renders_each_post(self):
        for n in range(eden.PAGE + 3):
            self.eden.add(str(100 + n), self.upto - (n + 1) * 3600, f"post {n} &amp; more")
        events = self.read()
        searches = [body for tool, body in self.eden.calls if tool == "eden_search_social_content"]
        self.assertEqual([s["offset"] for s in searches], [0, eden.PAGE])
        self.assertTrue(all("publishedAfter" not in s for s in searches))
        self.assertEqual(searches[0]["creatorRef"], {"platform": "twitter", "username": "Sam"})
        items = self.items(events)
        self.assertEqual(len(items), eden.PAGE + 3)
        first = items[0]
        self.assertEqual(first.source_id, "twitter/100")
        self.assertEqual(first.title, "@Sam on X: post 0 & more")
        self.assertEqual(first.content, "X tweet by @Sam\n\npost 0 & more")
        self.assertEqual(first.ts, "2026-11-07T05:00:00Z")
        self.assertEqual(first.meta, {"url": "https://x.com/sam/status/100", "network": "X", "kind": "tweet"})
        self.assertTrue(all(e.mark == self.upto for e in events))
        self.assertEqual(events[-1], Progress(self.upto))
        analytics = [body for tool, body in self.eden.calls if tool == "eden_list_analytics_posts"]
        self.assertEqual(analytics, [{"platform": "twitter", "limit": 100}], "all the history Eden keeps")

    def test_a_post_younger_than_settle_waits_for_a_later_run(self):
        self.eden.add("1", self.upto - 60, "settled")
        self.eden.add("2", self.upto + 60, "too new")
        self.assertEqual([i.source_id for i in self.items(self.read(since=self.upto - DAY))], ["twitter/1"])
        self.now += DAY
        self.assertEqual([i.source_id for i in self.items(self.read(since=self.upto))], ["twitter/2"])

    def test_a_check_sooner_than_every_hours_asks_eden_nothing(self):
        self.assertEqual(self.read(since=self.upto - 23 * 3600), [])
        self.assertEqual(self.eden.calls, [])
        self.assertEqual(self.read(since=self.upto - 2 * 3600, every_hours=1), [Progress(self.upto)])

    def test_later_runs_ask_from_the_last_run_and_read_only_posts_after_it(self):
        self.eden.add("1", self.upto - 10 * DAY, "old")
        self.eden.add("2", self.upto - 2.5 * DAY, "same day as the last run, before it")
        self.eden.add("3", self.upto - 3 * 3600, "new")
        since = self.upto - 2 * DAY
        events = self.read(since=since)
        search = next(body for tool, body in self.eden.calls if tool == "eden_search_social_content")
        self.assertEqual(search["publishedAfter"], datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%d"))
        self.assertEqual([i.source_id for i in self.items(events)], ["twitter/3"])
        analytics = next(body for tool, body in self.eden.calls if tool == "eden_list_analytics_posts")
        self.assertEqual(analytics["days"], 4)

    def test_no_new_post_needs_no_analytics(self):
        self.eden.add("1", self.upto - 2.5 * DAY, "seen before")
        self.assertEqual(self.read(since=self.upto - 2 * DAY), [Progress(self.upto)])
        self.assertEqual(self.eden.tools(), ["eden_search_social_content"])

    def test_a_thread_stored_whole_is_never_stored_again_by_its_start(self):
        self.eden.add("1", self.upto - 3600, "thread start")
        self.eden.heads = {"1"}
        self.eden.threads["1"] = [("2", "a1", "second post")]
        events = self.read(since=self.upto - DAY)
        self.assertEqual(self.items(events)[0].meta["kind"], "thread")
        self.now += DAY
        self.eden.heads = set()
        self.assertEqual(self.read(since=events[-1].mark), [Progress(self.now - eden.SETTLE)])

    def test_new_cut_posts_are_read_whole_newest_first_up_to_full_reads(self):
        for n in range(4):
            self.eden.add(str(n), self.upto - (4 - n) * 3600, f"start {n}", cut=True)
            self.eden.whole[str(n)] = f"start {n} and the whole rest"
        items = self.items(self.read(since=self.upto - DAY, full_reads=2))
        self.assertEqual(self.eden.reads(), ["3", "2"])
        texts = {i.source_id: i.content for i in items}
        self.assertEqual(texts["twitter/3"], "X tweet by @Sam\n\nstart 3 and the whole rest")
        self.assertEqual(texts["twitter/1"], "X tweet by @Sam\n\nstart 1\n\n(Eden returned only the start of this post.)")

    def test_a_thread_keeps_the_authors_later_posts_only(self):
        self.eden.add("1", self.upto - 3600, "i built my own")
        self.eden.heads = {"1"}
        self.eden.threads["1"] = [("2", "a1", "how i timed it"), ("3", "b2", "cool!"), ("4", "a1", "repo &gt; here")]
        [item] = self.items(self.read(since=self.upto - DAY))
        self.assertEqual(item.content, "X thread by @Sam\n\ni built my own\n\n---\n\nhow i timed it\n\n---\n\nrepo > here")
        self.assertEqual(item.meta["kind"], "thread")

    def test_a_thread_left_unread_says_so(self):
        self.eden.add("1", self.upto - 3600, "i built my own")
        self.eden.heads = {"1"}
        [item] = self.items(self.read(since=self.upto - DAY, full_reads=0))
        self.assertEqual(item.content, "X thread by @Sam\n\ni built my own\n\n(The rest of this thread was not read.)")

    def test_excludes_match_the_post_id(self):
        self.eden.add("1", self.upto - 3600, "keep")
        self.eden.add("2", self.upto - 1800, "skip", cut=True)
        items = self.items(self.read(since=self.upto - DAY, exclude=("2",)))
        self.assertEqual([i.source_id for i in items], ["twitter/1"])
        self.assertEqual(self.eden.reads(), [])

    def test_a_titled_post_on_another_network(self):
        self.eden.add("abc", self.upto - 3600, "A walk through my setup", platform="youtube", kind="video",
                      title="Too many agents?")
        [item] = self.items(self.read(since=self.upto - DAY, platform="youtube"))
        self.assertEqual(item.source_id, "youtube/abc")
        self.assertEqual(item.title, "@Sam on YouTube: Too many agents?")
        self.assertEqual(item.content, "YouTube video by @Sam\n\nToo many agents?\n\nA walk through my setup")


class CallTest(unittest.TestCase):
    """eden.call against a local server standing in for Eden."""

    def setUp(self):
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                test.seen.append((self.path, self.headers.get("authorization"),
                                  json.loads(self.rfile.read(int(self.headers["content-length"])))))
                status, body = test.answer
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *args):
                pass

        self.seen = []
        self.answer = (200, '{"ok": true, "posts": []}')
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        patcher = mock.patch.object(eden, "API", f"http://127.0.0.1:{server.server_address[1]}/v1/tools/")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, {"EDEN_KEY": KEY})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sends_the_key_as_a_bearer_token(self):
        self.assertEqual(eden.call(Secret("env:EDEN_KEY"), "eden_list_analytics_posts", {"days": 2}),
                         {"ok": True, "posts": []})
        self.assertEqual(self.seen, [("/v1/tools/eden_list_analytics_posts", f"Bearer {KEY}", {"days": 2})])

    def test_an_http_error_is_masked(self):
        self.answer = (401, '{"error": "bad key", "detail": "password=hunter2hunter2"}')
        with self.assertRaises(RuntimeError) as caught:
            eden.call(Secret("env:EDEN_KEY"), "eden_search_social_content", {})
        self.assertIn("Eden eden_search_social_content answered HTTP 401", str(caught.exception))
        self.assertNotIn("hunter2hunter2", str(caught.exception))

    def test_a_refusal_names_its_status(self):
        self.answer = (200, '{"ok": false, "status": "out_of_credits", "message": "No credits left"}')
        with self.assertRaisesRegex(RuntimeError, "Eden eden_read_social_post refused: out_of_credits No credits left"):
            eden.call(Secret("env:EDEN_KEY"), "eden_read_social_post", {})

    def test_an_answer_that_is_not_an_object(self):
        self.answer = (200, "[]")
        with self.assertRaisesRegex(RuntimeError, "something other than an object"):
            eden.call(Secret("env:EDEN_KEY"), "eden_read_social_post", {})
        self.answer = (200, "<html>")
        with self.assertRaisesRegex(RuntimeError, "cannot reach Eden"):
            eden.call(Secret("env:EDEN_KEY"), "eden_read_social_post", {})


class ConfigTest(unittest.TestCase):
    def test_the_account_key_names_network_and_user(self):
        cfg = config.parse('[sources.x]\ntype = "eden"\nusername = "@TheSam"\nkey = "env:EDEN_KEY"\n')
        self.assertEqual(cfg.account_keys(), {"x": "eden:twitter:thesam"})

    def test_the_key_is_required_and_keeps_the_source_on_this_computer(self):
        with self.assertRaisesRegex(config.ConfigError, "sources.x.key: required"):
            config.parse('[sources.x]\ntype = "eden"\nusername = "sam"\n')
        with self.assertRaisesRegex(config.ConfigError, "runs only on this computer"):
            config.parse('[hosts.laptop]\nssh = "laptop"\n[sources.x]\ntype = "eden"\nusername = "sam"\n'
                         'key = "env:EDEN_KEY"\nhosts = ["laptop"]\n')


if __name__ == "__main__":
    unittest.main()
