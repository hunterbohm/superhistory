import contextlib
import io
import json
import logging
import os
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from kb import config, ingest, search, store
from kb.sources import Excludes, Item, Progress, Run, git

DAY = 86400
ME = ("Sam Rivera", "sam@example.com")
OTHER = ("Dana Okoye", "dana@example.org")
LOG = logging.getLogger("varve-test-git")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def items(events):
    return [e for e in events if isinstance(e, Item)]


class GitCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = os.path.realpath(tmp.name)
        gitconfig = os.path.join(self.home, ".gitconfig")
        with open(gitconfig, "w") as fh:
            fh.write(f"[user]\n\tname = {ME[0]}\n\temail = {ME[1]}\n[init]\n\tdefaultBranch = main\n")
        patch = mock.patch.dict(os.environ, {"HOME": self.home, "GIT_CONFIG_GLOBAL": gitconfig,
                                             "GIT_CONFIG_NOSYSTEM": "1"})
        patch.start()
        self.addCleanup(patch.stop)
        self.now = int(time.time())

    def sh(self, cwd, *args, env=None):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                              env={**os.environ, **(env or {})}).stdout.strip()

    def identity(self, who, age_days):
        date = f"{self.now - age_days * DAY} +0000"
        return {"GIT_AUTHOR_NAME": who[0], "GIT_AUTHOR_EMAIL": who[1], "GIT_COMMITTER_NAME": who[0],
                "GIT_COMMITTER_EMAIL": who[1], "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}

    def repo(self, rel, remote=None):
        path = os.path.join(self.home, rel)
        os.makedirs(path, exist_ok=True)
        self.sh(path, "init", "-q")
        if remote:
            self.sh(path, "remote", "add", "origin", remote)
        return path

    def commit(self, path, message, files=("notes.md",), who=ME, age_days=1):
        for name in files:
            full = os.path.join(path, name)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "a") as fh:
                fh.write(message + "\n")
        self.sh(path, "add", "-A")
        self.sh(path, "commit", "-q", "-m", message, env=self.identity(who, age_days))
        return self.sh(path, "rev-parse", "HEAD")

    def read(self, since=None, exclude=git.TYPE.exclude, **options):
        return list(git.TYPE.extract(Run(git.Options(**options), Excludes(exclude), since)))

    def titles(self, **kwargs):
        return {i.title for i in items(self.read(**kwargs))}


class GitSourceTest(GitCase):
    def test_a_commit_becomes_an_item_keyed_by_its_github_repository(self):
        path = self.repo("code/projects/demo", "git@github.com:acme-co/demo.git")
        sha = self.commit(path, "Add pricing page\n\nCustomers asked for annual plans.",
                          files=("site/pricing.md", "README.md"))
        [item, done] = self.read()
        self.assertEqual(item.source_id, f"acme-co/demo@{sha}")
        self.assertEqual(item.title, "acme-co/demo: Add pricing page")
        self.assertEqual(item.ts, iso(self.now - DAY))
        self.assertEqual(item.content, "Add pricing page\n\nCustomers asked for annual plans.\n\n"
                                       "Files changed:\nA\tREADME.md\nA\tsite/pricing.md")
        self.assertEqual(item.meta, {"path": path, "repo": "acme-co/demo", "sha": sha,
                                     "url": f"https://github.com/acme-co/demo/commit/{sha}"})
        self.assertIsInstance(done, Progress)
        self.assertAlmostEqual(done.mark, time.time(), delta=5)
        self.assertEqual(item.mark, done.mark)

    def test_a_line_separator_in_an_authors_name_or_a_file_name_is_kept_whole(self):
        path = self.repo("code/tools/notes-cli")
        self.commit(path, "Draft the intro", who=("Ana\u2028Lee", "ana@example.org"))
        sha = self.commit(path, "Index the notes", files=("notes\u2028draft.md",))
        [item, _] = self.read()
        self.assertTrue(item.source_id.endswith(sha))
        self.assertIn("A\tnotes\u2028draft.md", item.content)

    def test_a_repository_with_no_github_remote_is_named_by_its_folder(self):
        path = self.repo("code/tools/notes-cli")
        sha = self.commit(path, "Index the notes")
        [item, _] = self.read()
        self.assertEqual(item.source_id, f"~/code/tools/notes-cli@{sha}")
        self.assertEqual(item.meta, {"path": path, "repo": "~/code/tools/notes-cli", "sha": sha})

    def test_only_the_users_names_and_addresses_count_as_theirs(self):
        path = self.repo("code/vendor/widgets", "https://github.com/acme-co/widgets-fork.git")
        self.commit(path, "Upstream renderer change", who=OTHER)
        self.commit(path, "Name from git config", who=(ME[0], "new@agency.example"))
        self.commit(path, "Address from git config", who=("S", ME[1]))
        self.commit(path, "Name that differs only by case", who=(ME[0].upper(), "x@agency.example"))
        self.commit(path, "Listed address", who=("sriv", "12345+sriv@users.noreply.example.com"))
        self.commit(path, "Listed name", who=("Sam at Work", "sam@work.example.net"))
        self.assertEqual(self.titles(identities=("12345+SRIV@users.noreply.example.com", "Sam at Work")),
                         {"acme-co/widgets-fork: Name from git config", "acme-co/widgets-fork: Address from git config",
                          "acme-co/widgets-fork: Name that differs only by case",
                          "acme-co/widgets-fork: Listed address", "acme-co/widgets-fork: Listed name"})
        self.assertEqual(self.titles(), {"acme-co/widgets-fork: Name from git config",
                                         "acme-co/widgets-fork: Address from git config",
                                         "acme-co/widgets-fork: Name that differs only by case"})

    def test_clones_and_worktrees_of_one_repository_yield_each_commit_once(self):
        main = self.repo("code/clients/acme/dash", "https://github.com/acme-co/dash")
        sha = self.commit(main, "Ship the radio panel")
        clone = os.path.join(self.home, "code/clients/acme/dash-row-1")
        self.sh(self.home, "clone", "-q", main, clone)
        self.sh(clone, "remote", "set-url", "origin", "https://github.com/acme-co/dash.git")
        self.sh(self.home, "clone", "-q", main, os.path.join(self.home, "code/archive/dash-copy"))
        self.sh(main, "worktree", "add", "-q", "-b", "linked", os.path.join(self.home, "code/.worktrees/dash-linked"))
        local = self.repo("code/tools/kb")
        own = self.commit(local, "Index commits")
        found = {i.source_id: i for i in items(self.read())}
        self.assertEqual(set(found), {f"acme-co/dash@{sha}", f"~/code/tools/kb@{own}"})

    def test_agent_checkpoints_and_stashes_are_not_read_but_tags_are(self):
        path = self.repo("code/projects/app", "https://github.com/acme-co/app.git")
        self.commit(path, "Shipped work")
        tree = self.sh(path, "rev-parse", "HEAD^{tree}")
        for ref in ("refs/t3/checkpoints/abc/ordinal/0", "refs/codex/snapshot"):
            checkpoint = self.sh(path, "commit-tree", tree, "-p", "HEAD", "-m", "agent checkpoint " + ref,
                                  env=self.identity(ME, 0))
            self.sh(path, "update-ref", ref, checkpoint)
        released = self.sh(path, "commit-tree", tree, "-p", "HEAD", "-m", "Release build", env=self.identity(ME, 0))
        self.sh(path, "tag", "v1", released)
        with open(os.path.join(path, "notes.md"), "a") as fh:
            fh.write("work in progress\n")
        self.sh(path, "stash", "-q")
        self.assertIn("refs/stash", self.sh(path, "for-each-ref", "--format=%(refname)"))
        self.assertEqual(self.titles(), {"acme-co/app: Shipped work", "acme-co/app: Release build"})

    def test_remote_tracking_branches_are_read(self):
        upstream = self.repo("elsewhere/upstream")
        sha = self.commit(upstream, "Pushed from another computer")
        clone = os.path.join(self.home, "code/site")
        self.sh(self.home, "clone", "-q", upstream, clone)
        self.sh(clone, "checkout", "-q", "--detach")
        self.sh(clone, "branch", "-q", "-D", "main")
        self.assertEqual(self.sh(clone, "for-each-ref", "--format=%(refname)"), "refs/remotes/origin/HEAD\nrefs/remotes/origin/main")
        self.assertEqual([i.source_id.split("@")[1] for i in items(self.read())], [sha])

    def test_first_run_reads_the_window_and_later_runs_reread_the_overlap(self):
        path = self.repo("code/clients/site", "https://github.com/acme-co/site.git")
        for message, age in (("Too old", 400), ("Spring release", 100), ("Pulled late", 10)):
            self.commit(path, message, age_days=age)
        self.assertEqual(self.titles(), {"acme-co/site: Spring release", "acme-co/site: Pulled late"})
        self.assertEqual(self.titles(window_days=30), {"acme-co/site: Pulled late"})
        self.assertEqual(self.titles(window_days=500), {"acme-co/site: Too old", "acme-co/site: Spring release",
                                                        "acme-co/site: Pulled late"})
        later = [i.title for i in items(self.read(since=self.now - DAY))]
        self.assertEqual(later, ["acme-co/site: Pulled late"])

    def test_only_git_folders_inside_the_roots_are_read_and_symlinks_are_not_followed(self):
        notes = self.repo("notes")
        self.commit(notes, "Journal entry", files=("journal/today.md",))
        os.makedirs(os.path.join(self.home, "code"))
        os.symlink(notes, os.path.join(self.home, "code", "notes-link"))
        self.commit(self.repo("code/app/node_modules/pkg"), "Vendored package")
        self.commit(self.repo("code/a/b/c/d"), "Four levels down")
        self.commit(self.repo("code/a/b/c/d/e"), "Five levels down")
        with open(os.path.join(self.home, "code", "a", ".git"), "w") as fh:
            fh.write("gitdir: /nonexistent/.git/worktrees/a\n")
        self.assertEqual(self.titles(), {"~/code/a/b/c/d: Four levels down"})
        self.assertEqual(self.titles(max_depth=5), {"~/code/a/b/c/d: Four levels down",
                                                    "~/code/a/b/c/d/e: Five levels down"})
        self.assertEqual(self.titles(max_depth=1), set())

    def test_every_root_is_read_and_a_home_prefix_is_expanded(self):
        self.commit(self.repo("code/one"), "In code")
        self.commit(self.repo("clients/two"), "In clients")
        self.assertEqual(self.titles(roots=("~/code", "~/clients")), {"~/code/one: In code", "~/clients/two: In clients"})
        self.assertEqual(self.titles(roots=(os.path.join(self.home, "clients"),)), {"~/clients/two: In clients"})
        self.assertEqual(self.titles(roots=("~/missing",)), set())

    def test_files_changed_stops_at_fifty(self):
        path = self.repo("code/projects/bulk")
        self.commit(path, "Import assets", files=[f"assets/{n:02}.txt" for n in range(53)])
        [item] = items(self.read())
        lines = item.content.split("Files changed:\n", 1)[1].splitlines()
        self.assertEqual(len(lines), 51)
        self.assertEqual(lines[0], "A\tassets/00.txt")
        self.assertEqual(lines[-1], "... and 3 more")

    def test_a_repository_that_fails_is_skipped_and_the_rest_are_read(self):
        broken = self.repo("code/projects/broken", "https://github.com/acme-co/broken.git")
        self.commit(broken, "Unreadable")
        with open(os.path.join(broken, ".git", "refs", "heads", "main"), "w") as fh:
            fh.write("0123456789abcdef0123456789abcdef01234567\n")
        fine = self.repo("code/projects/fine", "https://github.com/acme-co/fine.git")
        self.commit(fine, "Readable")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            titles = [i.title for i in items(self.read())]
        self.assertEqual(titles, ["acme-co/fine: Readable"])
        self.assertIn(f"git: skipped {broken}: git log", err.getvalue())


class GitExcludeTest(GitCase):
    def test_node_modules_and_syncthing_folders_are_skipped_by_default(self):
        self.commit(self.repo("code/app/node_modules/pkg"), "Vendored")
        self.commit(self.repo("code/.stversions/old"), "Sync copy")
        self.commit(self.repo("code/app"), "Real")
        self.assertEqual(self.titles(), {"~/code/app: Real"})
        self.assertEqual(self.titles(exclude=()), {"~/code/app: Real", "~/code/app/node_modules/pkg: Vendored",
                                                   "~/code/.stversions/old: Sync copy"})

    def test_a_repository_folder_written_with_a_tilde_is_skipped_with_everything_under_it(self):
        self.commit(self.repo("code/old-client"), "Finished job")
        self.commit(self.repo("code/old-client/sub"), "Nested")
        self.commit(self.repo("code/archive/2019/proj"), "Ancient")
        self.commit(self.repo("code/current"), "Active")
        self.commit(self.repo("code/old-client-2"), "Different folder")
        found = self.titles(exclude=git.TYPE.exclude + ("~/code/old-client", "~/code/archive/**"))
        self.assertEqual(found, {"~/code/current: Active", "~/code/old-client-2: Different folder"})

    def test_a_pattern_without_a_slash_skips_any_folder_of_that_name(self):
        self.commit(self.repo("code/a/scratch"), "Scratch one")
        self.commit(self.repo("code/b/scratch"), "Scratch two")
        self.commit(self.repo("code/b/keep"), "Kept")
        self.assertEqual(self.titles(exclude=("scratch",)), {"~/code/b/keep: Kept"})

    def test_a_linked_worktree_of_a_skipped_repository_is_skipped_too(self):
        main = self.repo("projects/secret")
        self.commit(main, "Private work")
        self.sh(main, "worktree", "add", "-q", "-b", "linked", os.path.join(self.home, "code/secret-wt"))
        self.commit(self.repo("code/open"), "Public work")
        self.assertEqual(self.titles(exclude=("~/projects/secret",)), {"~/code/open: Public work"})
        self.assertEqual(self.titles(), {"~/projects/secret: Private work", "~/code/open: Public work"})

    def test_a_folder_outside_home_is_matched_by_its_absolute_path(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        path = os.path.join(os.path.realpath(outside.name), "work")
        os.makedirs(path)
        self.sh(path, "init", "-q")
        self.commit(path, "Elsewhere")
        found = self.titles(roots=(os.path.dirname(path),))
        self.assertEqual(found, {f"{path}: Elsewhere"})
        self.assertEqual(self.titles(roots=(os.path.dirname(path),), exclude=(path,)), set())


class GitIngestTest(GitCase):
    def parse(self, extra=""):
        return config.parse(f'machine = "m1"\n[sources.commits]\ntype = "git"\n{extra}')

    def rows(self, db):
        return db.execute("SELECT id, source_id, title FROM docs WHERE source='commits' ORDER BY id").fetchall()

    def test_the_default_config_reads_the_home_code_folder_and_a_second_run_stores_nothing_new(self):
        path = self.repo("code/projects/demo", "https://github.com/acme-co/demo.git")
        sha = self.commit(path, "Teach the narwhal parser to wait")
        self.commit(path, "Not mine", who=OTHER)
        self.commit(self.repo("code/projects/demo/node_modules/dep"), "Vendored")
        self.commit(self.repo("elsewhere/other"), "Outside the roots")
        db = store.connect(os.path.join(self.home, "kb.sqlite"))
        self.addCleanup(db.close)
        cfg = self.parse()
        started = time.time()
        self.assertEqual(ingest.run(db, cfg, LOG, with_summaries=False), 0)
        rows = self.rows(db)
        self.assertEqual([r[1] for r in rows], [f"m1:acme-co/demo@{sha}"])
        mark = json.loads(store.get_watermark(db, "commits", "m1"))
        self.assertGreaterEqual(mark, started)
        hits = search.search(db, "narwhal", sources=("commits",))
        self.assertEqual([h["source_id"] for h in hits], [f"m1:acme-co/demo@{sha}"])
        self.assertEqual(hits[0]["meta"]["host"], "m1")
        self.assertEqual(hits[0]["meta"]["url"], f"https://github.com/acme-co/demo/commit/{sha}")
        self.assertEqual(ingest.run(db, cfg, LOG, with_summaries=False), 0)
        self.assertEqual(self.rows(db), rows, "second run rewrote unchanged commits")
        self.assertGreaterEqual(json.loads(store.get_watermark(db, "commits", "m1")), mark)

    def test_options_and_excludes_come_from_the_config_table(self):
        self.commit(self.repo("work/live"), "Live repo commit")
        self.commit(self.repo("work/old-client"), "Old client commit")
        self.commit(self.repo("work/live/node_modules/dep"), "Vendored")
        self.commit(self.repo("work/live", None), "By a teammate", who=("Sam at Work", "sam@work.example.net"))
        cfg = self.parse('roots = ["~/work"]\nidentities = ["sam@work.example.net"]\nexclude = ["~/work/old-client"]\n')
        db = store.connect(os.path.join(self.home, "kb.sqlite"))
        self.addCleanup(db.close)
        ingest.run(db, cfg, LOG, with_summaries=False)
        self.assertEqual({r[2] for r in self.rows(db)}, {"~/work/live: Live repo commit", "~/work/live: By a teammate"})
        cfg = self.parse('roots = ["~/work"]\nexclude_defaults = false\n')
        ingest.run(db, cfg, LOG, with_summaries=False)
        self.assertIn("~/work/live/node_modules/dep: Vendored", {r[2] for r in self.rows(db)})


if __name__ == "__main__":
    unittest.main()
