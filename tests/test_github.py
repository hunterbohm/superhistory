import json
import logging
import os
import stat
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from kb import config, ingest, search, store
from kb.sources import Excludes, Item, Progress, Run, github

DAY = 86400
REPO = "acme-co/dash"
HERE = os.path.dirname(os.path.abspath(__file__))
FAKE_GH = os.path.join(HERE, "fixtures", "github", "fake_gh.py")
LOG = logging.getLogger("varve-test-github")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def comment(login, at, body, minimized=False):
    return {"author": {"login": login}, "body": body, "createdAt": iso(at), "isMinimized": minimized}


def review(login, at, state, body):
    return {"author": {"login": login}, "body": body, "state": state, "submittedAt": iso(at) if at else None}


class FakeGh:
    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, "gh")
        with open(FAKE_GH) as fh:
            body = fh.read()
        with open(self.path, "w") as fh:
            fh.write(f"#!{sys.executable}\n{body}")
        os.chmod(self.path, os.stat(self.path).st_mode | stat.S_IXUSR)
        self.threads = {}
        self.save()

    def add(self, number, updated, repo=REPO, pr=False, title="Thread", body="", state="OPEN", author="sam",
            created=None, closed=None, merged=None, head="feature", base="main", labels=(), comments=(), reviews=()):
        view = {"title": title, "body": body, "state": state, "author": {"login": author},
                "createdAt": created or iso(updated - 3600), "updatedAt": iso(updated), "closedAt": closed,
                "labels": [{"name": name} for name in labels], "comments": list(comments),
                "url": f"https://github.com/{repo}/{'pull' if pr else 'issues'}/{number}"}
        if pr:
            view.update(mergedAt=merged, reviews=list(reviews), headRefName=head, baseRefName=base)
        self.threads[(repo, number)] = {"repo": repo, "number": number, "pr": pr, "view": view}
        self.save()

    def save(self):
        with open(os.path.join(self.folder, "state.json"), "w") as fh:
            json.dump({"threads": list(self.threads.values())}, fh)

    def calls(self):
        try:
            with open(os.path.join(self.folder, "calls.jsonl")) as fh:
                return [json.loads(line) for line in fh]
        except FileNotFoundError:
            return []

    def searches(self):
        ranges = [next(a for a in call if a.startswith("--updated=")).split("=", 1)[1].split("..")
                  for call in self.calls() if call[:2] == ["search", "issues"]]
        return [tuple(datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
                      for t in r) for r in ranges]

    def views(self):
        return [int(call[2]) for call in self.calls() if call[1:2] == ["view"]]


class GitHubCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = os.path.realpath(tmp.name)
        self.gh = FakeGh(self.tmp)
        self.now = float(int(time.time()))
        self.sleeps = []
        for patch in (mock.patch.object(github.time, "sleep", self.sleeps.append),
                      mock.patch.object(github.time, "time", lambda: self.now)):
            patch.start()
            self.addCleanup(patch.stop)

    def read(self, since=None, exclude=github.TYPE.exclude, **options):
        options.setdefault("gh", self.gh.path)
        return list(github.TYPE.extract(Run(github.Options(**options), Excludes(exclude), since)))


class GitHubSourceTest(GitHubCase):
    def test_pr_renders_its_description_comments_and_submitted_reviews_in_order(self):
        at = self.now - DAY
        self.gh.add(12, at, pr=True, title="Fix audio", body="Wait for the microphone.", state="MERGED",
                    created=iso(at - 7200), closed=iso(at - 60), merged=iso(at - 60), head="w/issue-11",
                    base="development", labels=("audio", "p1"),
                    comments=[comment("ci-bot", at - 7000, "Preview ready"),
                              comment("spammer", at - 6900, "buy followers", minimized=True),
                              comment("sam", at - 3000, "r1 fixes both findings.")],
                    reviews=[review("priya", at - 5000, "CHANGES_REQUESTED", "Hold: the ack starts early."),
                             review("priya", at - 4000, "COMMENTED", ""),
                             review("priya", at - 120, "APPROVED", ""),
                             review("sam", None, "PENDING", "unsent draft")])
        [item] = self.read(since=self.now - 2 * DAY)
        self.assertIsInstance(item, Item)
        self.assertEqual(item.source_id, f"{REPO}#12")
        self.assertEqual(item.title, f"{REPO}#12 [PR merged]: Fix audio")
        self.assertEqual(item.ts, iso(at))
        self.assertEqual(item.mark, at)
        self.assertEqual(item.content, "\n\n---\n\n".join([
            f"PR merged | author sam | created {iso(at - 7200)} | merged {iso(at - 60)} | "
            "w/issue-11 -> development | labels audio, p1\n\nWait for the microphone.",
            f"ci-bot ({iso(at - 7000)}): Preview ready",
            f"priya ({iso(at - 5000)}, review changes requested): Hold: the ack starts early.",
            f"sam ({iso(at - 3000)}): r1 fixes both findings.",
            f"priya ({iso(at - 120)}, review approved):"]))
        self.assertEqual(item.meta, {"url": f"https://github.com/{REPO}/pull/12", "repo": REPO, "number": 12,
                                     "kind": "pr", "state": "merged"})

    def test_titles_name_the_kind_and_state_and_issues_are_read_as_issues(self):
        at = self.now - DAY
        self.gh.add(1, at, title="Mic stays muted")
        self.gh.add(2, at + 60, title="Old crash", state="CLOSED", closed=iso(at))
        self.gh.add(3, at + 120, pr=True, title="Draft fix")
        self.gh.add(4, at + 180, pr=True, title="Abandoned", state="CLOSED", closed=iso(at))
        found = self.read(since=self.now - 2 * DAY)
        self.assertEqual([i.title for i in found], [
            f"{REPO}#1 [issue open]: Mic stays muted", f"{REPO}#2 [issue closed]: Old crash",
            f"{REPO}#3 [PR open]: Draft fix", f"{REPO}#4 [PR closed]: Abandoned"])
        self.assertEqual(found[1].content, f"issue closed | author sam | created {iso(at - 3540)} | "
                                           f"closed {iso(at)}\n\n(no description)")
        self.assertEqual(found[2].content.split("\n", 1)[0],
                         f"PR open | author sam | created {iso(at - 3480)} | feature -> main")
        self.assertEqual([i.meta["kind"] for i in found], ["issue", "issue", "pr", "pr"])

    def test_first_run_searches_the_window_in_spans_and_an_empty_one_still_moves_the_mark(self):
        found = self.read()
        searches = self.gh.searches()
        starts = [lo for lo, _ in searches]
        self.assertEqual(starts[0], self.now - 365 * DAY)
        self.assertEqual([hi for _, hi in searches][:-1], starts[1:], "windows are contiguous")
        self.assertEqual(searches[-1][1], self.now)
        self.assertEqual({hi - lo for lo, hi in searches[:-1]}, {github.SPAN_DAYS * DAY})
        self.assertEqual(found, [Progress(self.now)])

    def test_the_window_days_option_sets_how_far_back_the_first_run_reaches(self):
        self.read(window_days=45)
        self.assertEqual(self.gh.searches(), [(self.now - 45 * DAY, self.now - 15 * DAY), (self.now - 15 * DAY, self.now)])

    def test_the_involves_option_reaches_gh_and_the_default_is_me(self):
        self.read(since=self.now - DAY)
        self.read(since=self.now - DAY, involves="priya")
        flags = [next(a for a in call if a.startswith("--involves=")) for call in self.gh.calls()]
        self.assertEqual(flags, ["--involves=@me", "--involves=priya"])

    def test_a_thread_on_a_window_boundary_is_read_once(self):
        boundary = self.now - 365 * DAY + github.SPAN_DAYS * DAY
        self.gh.add(7, boundary, title="On the edge")
        found = self.read()
        self.assertEqual([i.source_id for i in found if isinstance(i, Item)], [f"{REPO}#7"])
        self.assertEqual(self.gh.views(), [7])

    def test_a_window_that_fills_a_search_is_split_in_half(self):
        since = self.now - 2 * DAY
        for n, at in enumerate((since - 600, self.now - DAY, self.now - 600), 1):
            self.gh.add(n, at, title=f"T{n}")
        with mock.patch.object(github, "SEARCH_CAP", 3):
            found = self.read(since=since)
        lo, hi, mid = since - github.OVERLAP, self.now, (since - github.OVERLAP + self.now) // 2
        self.assertEqual(self.gh.searches(), [(lo, hi), (lo, mid), (mid, hi)])
        self.assertEqual([i.title for i in found], [f"{REPO}#1 [issue open]: T1", f"{REPO}#2 [issue open]: T2",
                                                    f"{REPO}#3 [issue open]: T3"])
        self.assertEqual(sorted(self.gh.views()), [1, 2, 3])

    def test_later_runs_search_from_an_hour_before_the_watermark(self):
        since = self.now - 2 * DAY
        self.gh.add(5, since - 1800, title="Indexed late")
        self.gh.add(6, since - 7200, title="Already stored")
        found = self.read(since=since)
        self.assertEqual(self.gh.searches(), [(since - github.OVERLAP, self.now)])
        self.assertEqual([i.source_id for i in found], [f"{REPO}#5"])

    def test_a_search_waits_out_the_pages_the_one_before_it_used(self):
        start = self.now - 365 * DAY
        for n in range(5):
            self.gh.add(n + 1, start + 3600 * (n + 1))
        with mock.patch.object(github, "PAGE", 2), mock.patch.object(github, "SEARCH_PER_MINUTE", 60):
            self.read()
        self.assertEqual(self.sleeps[:3], [0.0, 3.0, 1.0])

    def test_long_text_is_clipped(self):
        self.gh.add(9, self.now - DAY, pr=True, body="b" * 30, comments=[comment("ann", self.now - DAY, "c" * 30)])
        with mock.patch.object(github, "BODY_CHARS", 10):
            [item] = self.read(since=self.now - 2 * DAY)
        self.assertIn("\n\nbbbbbbbbbb …\n\n---\n\n", item.content)
        self.assertTrue(item.content.endswith("): cccccccccc …"))

    def test_a_failing_gh_stops_the_run_with_its_message(self):
        self.gh.add(3, self.now - DAY)
        with self.assertRaisesRegex(RuntimeError, "Unknown JSON field"):
            with mock.patch.object(github, "ISSUE_FIELDS", "title,nonsense"):
                self.read(since=self.now - 2 * DAY)

    def test_excluded_repositories_are_not_fetched(self):
        at = self.now - DAY
        self.gh.add(1, at, title="Mine to keep")
        self.gh.add(2, at, repo="acme-co/internal", title="Skipped by name")
        self.gh.add(3, at, repo="other-org/site", title="Skipped by owner")
        self.gh.add(4, at, repo="other-org2/site", title="Kept")
        found = self.read(since=self.now - 2 * DAY, exclude=("acme-co/internal", "other-org/*"))
        self.assertEqual([i.title for i in found], [f"{REPO}#1 [issue open]: Mine to keep",
                                                    "other-org2/site#4 [issue open]: Kept"])
        self.assertEqual(sorted(self.gh.views()), [1, 4])

    def test_when_every_hit_is_excluded_the_mark_still_moves(self):
        self.gh.add(2, self.now - DAY, repo="acme-co/internal")
        self.assertEqual(self.read(since=self.now - 2 * DAY, exclude=("acme-co/internal",)), [Progress(self.now)])
        self.assertEqual(self.gh.views(), [])

    def test_a_missing_gh_binary_is_an_error(self):
        with self.assertRaisesRegex(RuntimeError, "gh did not run"):
            self.read(since=self.now - DAY, gh=os.path.join(self.tmp, "no-such-gh"))


class GitHubIngestTest(GitHubCase):
    def parse(self, extra=""):
        return config.parse(f'machine = "m1"\n[sources.threads]\ntype = "github"\ngh = {json.dumps(self.gh.path)}\n'
                            f'{extra}')

    def test_an_updated_thread_replaces_the_stored_one_and_the_watermark_follows_it(self):
        db = store.connect(os.path.join(self.tmp, "kb.sqlite"))
        self.addCleanup(db.close)
        cfg = self.parse()
        first = self.now - 3 * 3600
        self.gh.add(20, first, title="Pick a database", comments=[comment("ann", first, "Postgres?")])
        self.assertEqual(ingest.run(db, cfg, LOG, with_summaries=False), 0)
        self.assertEqual(json.loads(store.get_watermark(db, "threads", "m1")), first)
        later = self.now - 600
        self.gh.add(20, later, title="Pick a database",
                    comments=[comment("ann", first, "Postgres?"), comment("sam", later, "Decided: Postgres.")])
        self.assertEqual(ingest.run(db, cfg, LOG, with_summaries=False), 0)
        rows = db.execute("SELECT source_id, content FROM docs WHERE source='threads'").fetchall()
        self.assertEqual([r[0] for r in rows], [f"m1:{REPO}#20"])
        self.assertIn("Decided: Postgres.", rows[0][1])
        self.assertEqual(json.loads(store.get_watermark(db, "threads", "m1")), later)
        hits = search.search(db, "Postgres", sources=("threads",))
        self.assertEqual([h["source_id"] for h in hits], [f"m1:{REPO}#20"])
        self.assertEqual(hits[0]["meta"]["url"], f"https://github.com/{REPO}/issues/20")
        self.assertEqual(hits[0]["meta"]["host"], "m1")

    def test_config_options_and_excludes_reach_the_source(self):
        at = self.now - DAY
        self.gh.add(1, at, title="Kept")
        self.gh.add(2, at, repo="acme-co/internal", title="Hidden")
        db = store.connect(os.path.join(self.tmp, "kb.sqlite"))
        self.addCleanup(db.close)
        self.assertEqual(ingest.run(db, self.parse('involves = "priya"\nwindow_days = 30\n'
                                                   'exclude = ["acme-co/internal"]\n'), LOG, with_summaries=False), 0)
        ids = [r[0] for r in db.execute("SELECT source_id FROM docs WHERE source='threads'")]
        self.assertEqual(ids, [f"m1:{REPO}#1"])
        self.assertTrue(all("--involves=priya" in call for call in self.gh.calls() if call[0] == "search"))
        self.assertEqual(self.gh.searches()[0][0], self.now - 30 * DAY)


if __name__ == "__main__":
    unittest.main()
