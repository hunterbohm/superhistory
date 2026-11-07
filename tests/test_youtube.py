import json
import os
import stat
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

from kb import config
from kb.sources import Excludes, Item, Run, youtube

DAY = 86400
CID = "UC" + "a" * 22
HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(HERE, "fixtures", "youtube", "fake_yt_dlp.py")

AUTO_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.080 --> 00:00:01.990 align:start position:0%

With<00:00:00.320><c> new</c><00:00:00.480><c> models</c>

00:00:01.990 --> 00:00:02.000 align:start position:0%
With new models


00:00:02.000 --> 00:00:04.470 align:start position:0%
With new models
every<00:00:02.240><c> lab &amp; team</c>

00:00:04.470 --> 00:00:04.480 align:start position:0%
every lab &amp; team

"""


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class FakeChannel:
    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, "yt-dlp")
        with open(FAKE) as fh:
            body = fh.read()
        with open(self.path, "w") as fh:
            fh.write(f"#!{sys.executable}\n{body}")
        os.chmod(self.path, os.stat(self.path).st_mode | stat.S_IXUSR)
        self.tabs = {"videos": [], "streams": []}
        self.videos = {}
        self.published = {}
        self.save()

    def add(self, vid, at, tab="videos", kind="video", title=None, description="", captions=None, live="not_live",
            readable=True, in_feed=True):
        self.tabs.setdefault(tab, []).insert(0, vid)
        if readable:
            self.videos[vid] = {"info": {"id": vid, "title": title or f"Video {vid}", "description": description,
                                         "timestamp": int(at), "duration": 615, "channel": "Sam Makes",
                                         "media_type": kind, "live_status": live,
                                         "webpage_url": f"https://www.youtube.com/watch?v={vid}"},
                                "captions": captions if captions is not None else {"en": AUTO_VTT}}
        if in_feed:
            self.published[vid] = at
        self.save()

    def save(self):
        with open(os.path.join(self.folder, "state.json"), "w") as fh:
            json.dump({"tabs": self.tabs, "videos": self.videos}, fh)

    def feed(self, size=youtube.FEED_SIZE):
        newest = sorted(self.published.items(), key=lambda kv: -kv[1])[:size]
        entries = "".join(f"<entry><yt:videoId>{vid}</yt:videoId><published>{iso(at)}</published></entry>"
                          for vid, at in newest)
        return ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
                f'xmlns:yt="http://www.youtube.com/xml/schemas/2015">{entries}</feed>')

    def calls(self):
        try:
            with open(os.path.join(self.folder, "calls.jsonl")) as fh:
                return [json.loads(line) for line in fh]
        except FileNotFoundError:
            return []

    def listings(self):
        return [c[-1].rsplit("/", 1)[-1] for c in self.calls() if "--flat-playlist" in c]

    def reads(self):
        return [c[c.index("--") + 1:] for c in self.calls() if "--" in c]


class YouTubeCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.channel = FakeChannel(os.path.realpath(tmp.name))
        self.now = float(int(datetime(2026, 11, 7, 12, tzinfo=timezone.utc).timestamp()))
        self.fetched = []
        self.page = f'<link rel="alternate" type="application/rss+xml" title="RSS" ' \
                    f'href="https://www.youtube.com/feeds/videos.xml?channel_id={CID}">'
        for name, value in (("time", types.SimpleNamespace(time=lambda: self.now)), ("fetch", self.fetch)):
            patcher = mock.patch.object(youtube, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fetch(self, url):
        self.fetched.append(url)
        if url == youtube.FEED.format(CID):
            return self.channel.feed()
        return self.page

    def read(self, since=None, exclude=(), **options):
        options.setdefault("channel", "@sam")
        options.setdefault("yt_dlp", self.channel.path)
        return list(youtube.TYPE.extract(Run(youtube.Options(**options), Excludes(exclude), since)))


class YouTubeSourceTest(YouTubeCase):
    def test_first_run_lists_every_tab_and_renders_title_description_and_transcript(self):
        self.channel.add("v1", self.now - 30 * DAY, title="How I plan", description="Links &amp; notes")
        self.channel.add("s1", self.now - 20 * DAY, tab="shorts", kind="short", captions={})
        self.channel.add("l1", self.now - 10 * DAY, tab="streams", kind="livestream", live="was_live")
        found = self.read()
        self.assertEqual([i.source_id for i in found], ["v1", "s1", "l1"], "oldest first")
        self.assertEqual(self.channel.listings(), ["videos", "shorts", "streams"])
        first = found[0]
        self.assertIsInstance(first, Item)
        self.assertEqual(first.title, "How I plan")
        self.assertEqual(first.ts, datetime.fromtimestamp(self.now - 30 * DAY, timezone.utc)
                         .strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(first.mark, self.now - 30 * DAY)
        self.assertEqual(first.content, "YouTube video on Sam Makes | 10 min\n\nLinks &amp; notes\n\n"
                                        "Transcript:\nWith new models every lab & team")
        self.assertEqual(first.meta, {"url": "https://www.youtube.com/watch?v=v1", "channel": "Sam Makes",
                                      "kind": "video", "duration": 615})
        self.assertNotIn("Transcript:", found[1].content, "a short with no captions, past the wait, has none")
        self.assertEqual(found[2].meta["kind"], "livestream")

    def test_a_tab_the_channel_lacks_is_skipped(self):
        self.channel.add("v1", self.now - 3 * DAY)
        self.assertEqual([i.source_id for i in self.read()], ["v1"])
        self.assertEqual(self.channel.listings(), ["videos", "shorts", "streams"])

    def test_later_runs_read_only_feed_entries_published_since_the_last(self):
        self.channel.add("v1", self.now - 30 * DAY)
        self.channel.add("v2", self.now - 3 * DAY)
        self.channel.add("v3", self.now - 2 * DAY)
        found = self.read(since=self.now - 3 * DAY)
        self.assertEqual([i.source_id for i in found], ["v3"])
        self.assertEqual(self.channel.listings(), [], "the feed is enough")
        self.assertEqual(self.channel.reads(), [["v3"]])

    def test_nothing_new_reads_no_video(self):
        self.channel.add("v1", self.now - 3 * DAY)
        self.assertEqual(self.read(since=self.now - 3 * DAY), [])
        self.assertEqual(self.channel.calls(), [])

    def test_a_feed_of_only_new_entries_lists_the_whole_channel(self):
        for n in range(youtube.FEED_SIZE + 2):
            self.channel.add(f"v{n:02}", self.now - (40 - n) * DAY)
        since = self.now - 41 * DAY
        found = self.read(since=since)
        self.assertEqual(len(found), youtube.FEED_SIZE + 2, "the two that fell off the feed are read too")
        self.assertEqual(self.channel.listings(), ["videos", "shorts", "streams"])

    def test_a_channel_url_or_id_names_the_channel(self):
        self.channel.add("v1", self.now - 3 * DAY)
        self.read(channel=CID)
        self.assertEqual(self.fetched, [youtube.FEED.format(CID)], "an id needs no page")
        self.fetched.clear()
        self.read(channel="https://www.youtube.com/@sam/")
        self.assertEqual(self.fetched[0], "https://www.youtube.com/@sam")
        self.assertEqual(youtube.channel_url("@sam"), "https://www.youtube.com/@sam")
        self.assertEqual(youtube.channel_url("sam"), "https://www.youtube.com/@sam")

    def test_a_page_naming_its_id_another_way_still_gives_the_id(self):
        self.page = f'<meta itemprop="identifier" content="{CID}">'
        self.assertEqual(youtube.channel_id("@sam"), CID)
        self.page = "<html>nothing</html>"
        with self.assertRaisesRegex(RuntimeError, "found no YouTube channel at https://www.youtube.com/@sam"):
            youtube.channel_id("@sam")

    def test_excludes_match_kind_and_id(self):
        self.channel.add("v1", self.now - 30 * DAY)
        self.channel.add("s1", self.now - 20 * DAY, tab="shorts", kind="short")
        self.assertEqual([i.source_id for i in self.read(exclude=("short",))], ["v1"])
        self.assertEqual([i.source_id for i in self.read(exclude=("video/v1",))], ["s1"])

    def test_captions_off_asks_for_none(self):
        self.channel.add("v1", self.now - 3 * DAY)
        [item] = self.read(captions=False)
        self.assertNotIn("Transcript:", item.content)
        self.assertNotIn("--write-auto-subs", self.channel.calls()[-1])

    def test_languages_pick_the_first_the_video_has(self):
        self.channel.add("v1", self.now - 3 * DAY, captions={"de-orig": "WEBVTT\n\n00:00.000 --> 00:01.000\nHallo",
                                                             "en": "WEBVTT\n\n00:00.000 --> 00:01.000\nHello"})
        [item] = self.read(languages=("fr", "de", "en"))
        self.assertTrue(item.content.endswith("Transcript:\nHallo"))
        self.assertIn("fr,fr-orig,de,de-orig,en,en-orig", self.channel.calls()[-1])


class WaitTest(YouTubeCase):
    def test_a_new_video_without_captions_waits_and_holds_back_newer_ones(self):
        self.channel.add("v1", self.now - 5 * DAY)
        self.channel.add("v2", self.now - 3 * 3600, captions={})
        self.channel.add("v3", self.now - 3600)
        found = self.read(since=self.now - 6 * DAY)
        self.assertEqual([i.source_id for i in found], ["v1"], "the watermark stays before v2")
        self.channel.videos["v2"]["captions"] = {"en": AUTO_VTT}
        self.channel.save()
        self.assertEqual([i.source_id for i in self.read(since=found[-1].mark)], ["v2", "v3"])

    def test_a_video_past_the_wait_is_stored_without_captions(self):
        self.channel.add("v1", self.now - 2 * DAY, captions={})
        [item] = self.read(since=self.now - 3 * DAY)
        self.assertNotIn("Transcript:", item.content)

    def test_a_live_or_upcoming_stream_waits_then_is_skipped(self):
        self.channel.add("l1", self.now - 3600, tab="streams", kind="livestream", live="is_live")
        self.assertEqual(self.read(since=self.now - DAY), [])
        self.now += 2 * DAY
        self.assertEqual(self.read(since=self.now - 3 * DAY), [], "never finished: skipped, not stored")

    def test_an_unreadable_video_waits_while_new_then_fails_the_run_until_excluded(self):
        self.channel.add("v1", self.now - 3600, readable=False)
        self.channel.add("v2", self.now - 1800)
        self.assertEqual(self.read(since=self.now - DAY), [])
        self.now += 2 * DAY
        with self.assertRaisesRegex(RuntimeError, "yt-dlp could not read video v1; if it stays unreadable"):
            self.read(since=self.now - 3 * DAY)
        self.assertEqual([i.source_id for i in self.read(since=self.now - 3 * DAY, exclude=("v1",))], ["v2"])

    def test_the_only_new_video_unreadable_waits_then_fails_until_excluded_by_id(self):
        self.channel.add("v0", self.now - 9 * DAY)
        self.channel.add("v1", self.now - 3600, readable=False)
        self.assertEqual(self.read(since=self.now - 2 * DAY), [], "waits, without failing")
        self.now += 2 * DAY
        with self.assertRaisesRegex(RuntimeError, "could not read video v1"):
            self.read(since=self.now - 4 * DAY)
        calls = len(self.channel.calls())
        self.assertEqual(self.read(since=self.now - 4 * DAY, exclude=("v1",)), [])
        self.assertEqual(self.channel.calls()[calls:], [], "an excluded id is not even asked for")

    def test_a_video_published_in_the_same_second_as_a_waiting_one_waits_with_it(self):
        self.channel.add("a1", self.now - 3600)
        self.channel.add("b1", self.now - 3600, captions={})
        self.channel.add("a0", self.now - 7200)
        self.assertEqual([i.source_id for i in self.read(since=self.now - DAY)], ["a0"])

    def test_an_unreadable_old_video_off_the_feed_does_not_stop_the_first_run(self):
        self.channel.add("v0", self.now - 90 * DAY, readable=False, in_feed=False)
        self.channel.add("v1", self.now - 30 * DAY)
        self.assertEqual([i.source_id for i in self.read()], ["v1"])


class FailureTest(YouTubeCase):
    def test_yt_dlp_failing_raises_its_error_masked(self):
        script = os.path.join(self.channel.folder, "broken")
        with open(script, "w") as fh:
            fh.write(f"#!{sys.executable}\nimport sys\nsys.stderr.write('WARNING: slow\\nERROR: blocked "
                     f"password=hunter2hunter2\\n')\nsys.exit(1)\n")
        os.chmod(script, 0o755)
        self.channel.add("v1", self.now - 3 * DAY)
        with self.assertRaises(RuntimeError) as caught:
            self.read(yt_dlp=script)
        self.assertIn("yt-dlp exited 1: ERROR: blocked", str(caught.exception))
        self.assertNotIn("hunter2hunter2", str(caught.exception))
        self.assertNotIn("WARNING", str(caught.exception))

    def test_a_missing_yt_dlp_says_so(self):
        self.channel.add("v1", self.now - 3 * DAY)
        with self.assertRaisesRegex(RuntimeError, "yt-dlp did not run"):
            self.read(yt_dlp=os.path.join(self.channel.folder, "absent"))

    def test_an_unreadable_feed_says_so(self):
        self.channel.feed = lambda size=0: "<html>"
        with self.assertRaisesRegex(RuntimeError, "the YouTube feed for UC"):
            self.read()


class TranscriptTest(unittest.TestCase):
    def test_rolling_automatic_captions_keep_each_line_once(self):
        self.assertEqual(youtube.transcript(AUTO_VTT), "With new models every lab & team")

    def test_manual_captions_with_cue_numbers_and_notes(self):
        vtt = "WEBVTT\n\nNOTE made by hand\n\n1\n00:00.000 --> 00:01.000\nFirst line\n\n2\n00:01.000 --> 00:02.000\n" \
              "<v Sam>Second</v> line\n"
        self.assertEqual(youtube.transcript(vtt), "First line Second line")


class ConfigTest(unittest.TestCase):
    def test_the_account_key_is_the_channel_however_it_is_written(self):
        keys = {config.parse(f'[sources.yt]\ntype = "youtube"\nchannel = "{c}"\n').account_keys()["yt"]
                for c in ("@Sam", "sam", "https://www.youtube.com/@sam/")}
        self.assertEqual(keys, {"youtube:sam"})
        self.assertEqual(config.parse(f'[sources.yt]\ntype = "youtube"\nchannel = "{CID}"\n').account_keys(),
                         {"yt": f"youtube:{CID}"})


if __name__ == "__main__":
    unittest.main()
