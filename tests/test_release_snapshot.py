import contextlib
import copy
import gzip
import hashlib
import io
import os
import re
import shutil
import signal
import ssl
import struct
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import unittest
import zlib
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tools import release_snapshot as rs

REPO = Path(__file__).resolve().parent.parent
INSTALLER = Path(__file__).resolve().parent / "fixtures" / "release" / "site_install.sh"
CURRENT = "%d.%d" % sys.version_info[:2]
GW, PORT, PROXY = "acme-gateway", "4242", "AcmeProxyAPI"
EXCEPTIONS = "tools/release_exceptions.tsv"
ROWS = "bench/DATASET_ROWS.txt"
CREDENTIAL = "zq-4417-kettle"
SECRET = ("see Acmecorp " + CREDENTIAL + " " + GW + " " + PORT + " " + PROXY + "\n").encode()
AUTHOR = "Pat Example <pat@example.invalid>"
PATTERNS = "\\bacmecorp\\b\n(^|[^-a-z0-9])boxroom\\b\nacme[-_]?gateway\n4242\nacmeproxyapi\n"
README = """# Widgetly

A small tool made up for tests. The command is `wgt`.

It does one thing,
across two lines.

## Install

Run it.

## Privacy

Known limits:

- The first limit
  wraps onto a second line.
- The second limit.

## Develop

Run the tests.
"""
GIT_ENV = {"GIT_AUTHOR_NAME": "Src Author", "GIT_AUTHOR_EMAIL": "src@example.invalid",
           "GIT_COMMITTER_NAME": "Src Author", "GIT_COMMITTER_EMAIL": "src@example.invalid"}


def git(cwd, *args, env=None):
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                            env={**os.environ, **GIT_ENV, **(env or {})})
    if result.returncode:
        raise AssertionError("git %s: %s" % (" ".join(args), result.stderr))
    return result.stdout.strip()


def write(root, files):
    for name, text in files.items():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


class SourceCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.out = self.tmp / "out"
        self.patterns = self.tmp / "patterns.txt"
        self.patterns.write_text(PATTERNS)
        git(self.src, "init", "-q", "-b", "main")

    def commit(self, files, message="change", remove=()):
        write(self.src, files)
        for name in remove:
            (self.src / name).unlink()
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", message)
        return git(self.src, "rev-parse", "HEAD")

    def base_files(self, **extra):
        files = {"README.md": README, "kb/__init__.py": '__version__ = "1.2.3"\n',
                 "pyproject.toml": "[project]\nname = \"widgetly\"\n", "install.sh": "#!/bin/sh\n", ROWS: ""}
        files.update(extra)
        return files

    def with_history(self):
        self.commit(self.base_files(**{"notes.txt": "login is %s\n" % CREDENTIAL}), "first")
        return self.commit({"notes.txt": "nothing here\n"}, "second")

    def run_main(self, *extra, rev="HEAD", suites=False):
        argv = [rev, str(self.out), "--source", str(self.src), "--author", AUTHOR, "--patterns", str(self.patterns),
                "--credential", CREDENTIAL, "--repo-name", "widgetly", *extra]
        if not suites:
            argv.append("--no-suites")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = rs.main(argv)
        return code, stdout.getvalue()

    def build(self, rev="HEAD", **kw):
        args = dict(author=AUTHOR, patterns=self.patterns, credentials=[CREDENTIAL], repo_name="widgetly")
        args.update(kw)
        return rs.build(self.src, rev, self.out, **args)


class SnapshotTest(SourceCase):
    def test_the_snapshot_is_one_root_commit_holding_the_commits_tree(self):
        head = self.with_history()
        release = self.build(head)
        repo = release.repo
        self.assertEqual(git(repo, "rev-list", "--all", "--count"), "1")
        self.assertEqual(git(repo, "rev-list", "--parents", "-n1", "HEAD").split(), [git(repo, "rev-parse", "HEAD")])
        self.assertEqual(git(repo, "rev-parse", "HEAD^{tree}"), git(self.src, "rev-parse", head + "^{tree}"))
        self.assertEqual(git(repo, "symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(git(repo, "status", "--porcelain"), "")
        self.assertEqual(git(repo, "fsck", "--full", "--strict"), "")
        self.assertFalse((repo / ".git" / "objects" / "info" / "alternates").exists())
        self.assertEqual(git(repo, "remote"), "")

    def test_the_snapshot_commit_has_the_given_author_and_the_commits_date(self):
        head = self.with_history()
        release = self.build(head)
        self.assertEqual(git(release.repo, "log", "-1", "--format=%an <%ae>|%cn <%ce>"), AUTHOR + "|" + AUTHOR)
        self.assertEqual(git(release.repo, "log", "-1", "--format=%cI"), git(self.src, "log", "-1", "--format=%cI", head))
        self.assertEqual(git(release.repo, "log", "-1", "--format=%s"), "Widgetly v1.2.3")

    def test_the_tag_is_the_version_in_the_commit_not_in_the_working_tree(self):
        head = self.with_history()
        write(self.src, {"kb/__init__.py": '__version__ = "9.9.9"\n'})
        release = self.build(head)
        self.assertEqual(release.tag, "v1.2.3")
        self.assertEqual(git(release.repo, "rev-parse", "v1.2.3^{commit}"), git(release.repo, "rev-parse", "HEAD"))
        self.assertEqual(git(release.repo, "tag", "--list"), "v1.2.3")

    def test_a_commit_without_a_version_is_refused(self):
        head = self.commit({"README.md": README})
        with self.assertRaisesRegex(rs.ReleaseError, "__version__"):
            self.build(head)
        self.assertFalse(self.out.exists())

    def test_an_unknown_commit_is_refused(self):
        self.with_history()
        with self.assertRaisesRegex(rs.ReleaseError, "no such commit"):
            self.build("no-such-branch")

    def test_the_commit_can_be_a_branch_a_tag_or_a_short_hash(self):
        head = self.with_history()
        git(self.src, "tag", "-a", "-m", "t", "named")
        tree = git(self.src, "rev-parse", head + "^{tree}")
        for n, rev in enumerate(["main", "named", head[:8]]):
            out = self.tmp / ("out%d" % n)
            release = rs.build(self.src, rev, out, author=AUTHOR, patterns=self.patterns, credentials=[CREDENTIAL])
            self.assertEqual(git(release.repo, "rev-parse", "HEAD^{tree}"), tree)

    def test_a_folder_that_is_not_empty_is_refused(self):
        head = self.with_history()
        self.out.mkdir()
        (self.out / "keep.txt").write_text("mine")
        with self.assertRaisesRegex(rs.ReleaseError, "not empty"):
            self.build(head)
        self.assertEqual((self.out / "keep.txt").read_text(), "mine")

    def test_the_source_repo_is_left_as_it_was(self):
        head = self.with_history()
        before = (git(self.src, "count-objects", "-v"), git(self.src, "for-each-ref"), git(self.src, "status", "--porcelain"))
        self.build(head)
        after = (git(self.src, "count-objects", "-v"), git(self.src, "for-each-ref"), git(self.src, "status", "--porcelain"))
        self.assertEqual(before, after)

    def test_files_keep_their_mode_and_symlinks_stay_links(self):
        write(self.src, self.base_files(**{"run.sh": "#!/bin/sh\n"}))
        os.chmod(self.src / "run.sh", 0o755)
        os.symlink("run.sh", self.src / "link")
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "modes")
        release = self.build()
        modes = dict(line.split("\t")[::-1] for line in git(release.repo, "ls-tree", "-r", "HEAD").splitlines())
        self.assertTrue(modes["run.sh"].startswith("100755"))
        self.assertTrue(modes["link"].startswith("120000"))


class CredentialTest(SourceCase):
    def test_history_holds_the_credential_and_the_snapshot_does_not(self):
        head = self.with_history()
        self.assertIn(head, git(self.src, "log", "--format=%H", "-S" + CREDENTIAL, "--all", "--", "notes.txt") + head)
        self.assertTrue(git(self.src, "log", "--format=%H", "-S" + CREDENTIAL))
        release = self.build(head)
        self.assertTrue(release.check("credential").ok, release.check("credential").summary)
        self.assertTrue(release.ok)
        objects = subprocess.run(["git", "cat-file", "--batch-all-objects", "--batch"], cwd=str(release.repo),
                                 capture_output=True).stdout
        self.assertNotIn(CREDENTIAL.encode(), objects.lower())

    def test_a_commit_that_still_holds_the_credential_fails_the_check_and_names_the_file(self):
        head = self.commit(self.base_files(**{"docs/old.txt": "token: %s\n" % CREDENTIAL.upper()}))
        release = self.build(head)
        found = release.check("credential")
        self.assertFalse(found.ok)
        self.assertFalse(release.ok)
        self.assertIn("docs/old.txt", [f.where for f in found.findings])

    def test_the_credential_is_found_whatever_case_it_is_given_in(self):
        head = self.commit(self.base_files(**{"docs/old.txt": "token: %s\n" % CREDENTIAL}))
        found = self.build(head, credentials=[CREDENTIAL.title()]).check("credential")
        self.assertIn("docs/old.txt", [f.where for f in found.findings])

    def test_a_credential_in_a_file_name_fails_the_check(self):
        head = self.commit(self.base_files(**{"notes/%s.txt" % CREDENTIAL: "x\n"}))
        found = self.build(head).check("credential")
        self.assertFalse(found.ok)
        self.assertTrue(found.findings)

    def test_a_credential_in_the_readme_reaches_the_release_notes_and_fails_both(self):
        head = self.commit(self.base_files(**{"README.md": README.replace("It does one thing,", "It does one thing with " + CREDENTIAL + ",")}))
        release = self.build(head)
        places = {f.where for f in release.check("credential").findings}
        self.assertIn("README.md", places)
        self.assertIn("RELEASE_NOTES.md", places)

    def test_a_credential_is_required(self):
        head = self.with_history()
        with self.assertRaisesRegex(rs.ReleaseError, "credential"):
            self.build(head, credentials=[])
        with self.assertRaisesRegex(rs.ReleaseError, "credential"):
            self.build(head, credentials=[""])

    def test_a_credential_that_is_not_ascii_is_refused(self):
        head = self.with_history()
        with self.assertRaisesRegex(rs.ReleaseError, "ASCII"):
            self.build(head, credentials=["fictional-\u00e9-secret"])


class HistoryCheckTest(SourceCase):
    def test_a_repo_with_history_fails(self):
        self.with_history()
        found = rs.check_history(self.src, "v1.2.3")
        self.assertFalse(found.ok)
        self.assertIn("2 commits", found.summary)

    def test_an_older_commit_left_in_the_object_store_fails(self):
        self.with_history()
        old = git(self.src, "rev-parse", "HEAD~1")
        tree = git(self.src, "rev-parse", "HEAD^{tree}")
        root = git(self.src, "commit-tree", tree, "-m", "root")
        git(self.src, "checkout", "-q", "-b", "fresh", root)
        git(self.src, "branch", "-q", "-D", "main")
        git(self.src, "tag", "v1.2.3")
        found = rs.check_history(self.src, "v1.2.3")
        self.assertIn(old, git(self.src, "cat-file", "--batch-all-objects", "--batch-check"))
        self.assertFalse(found.ok)

    def test_a_remote_or_an_extra_ref_fails(self):
        head = self.with_history()
        release = self.build(head)
        self.assertTrue(rs.check_history(release.repo, release.tag).ok)
        git(release.repo, "remote", "add", "origin", "https://example.invalid/x.git")
        self.assertFalse(rs.check_history(release.repo, release.tag).ok)
        git(release.repo, "remote", "remove", "origin")
        git(release.repo, "branch", "extra")
        self.assertFalse(rs.check_history(release.repo, release.tag).ok)


class PatternTest(SourceCase):
    def test_a_file_matching_a_pattern_fails_with_its_path_and_line(self):
        head = self.commit(self.base_files(**{"docs/a.md": "fine\nsee Acmecorp today\n"}))
        found = self.build(head).check("patterns")
        self.assertFalse(found.ok)
        self.assertEqual([(f.where, f.text) for f in found.findings], [("docs/a.md:2", "see Acmecorp today")])

    def test_a_path_matching_a_pattern_fails(self):
        head = self.commit(self.base_files(**{"acmecorp-notes.txt": "fine\n"}))
        found = self.build(head).check("patterns")
        self.assertFalse(found.ok)
        self.assertEqual([f.where for f in found.findings], ["acmecorp-notes.txt"])

    def test_the_patterns_read_as_extended_regular_expressions_without_regard_to_case(self):
        head = self.commit(self.base_files(**{"a.txt": "BOXROOM\nmy-boxroom\nthe boxroom.\nboxrooms\n"}))
        found = self.build(head).check("patterns")
        self.assertEqual([f.where for f in found.findings], ["a.txt:1", "a.txt:3"])

    def test_a_clean_tree_passes(self):
        release = self.build(self.commit(self.base_files()))
        self.assertTrue(release.check("patterns").ok)

    GW, PORT, PROXY = GW, PORT, PROXY
    SPELLINGS = [GW, "ACME-Gateway", "acme_gateway", "ACMEGATEWAY", PORT, PROXY, PROXY.lower(),
                 "~/.config/acme-gateway/client.key", "$HOME/.config/acme-gateway", "${HOME}/.config/acme-gateway",
                 "/home/someone/.config/acme-gateway", "/Users/someone/.config/acme-gateway",
                 "$XDG_CONFIG_HOME/acme-gateway/client.key", "~/.config//acme_gateway/client.key"]

    def test_every_spelling_of_a_name_a_port_and_a_key_path_in_the_patterns_file_hits(self):
        text = "".join("see %s here\n" % spelling for spelling in self.SPELLINGS)
        found = self.build(self.commit(self.base_files(**{"docs/a.md": text}))).check("patterns")
        self.assertFalse(found.ok)
        self.assertEqual([f.where for f in found.findings], ["docs/a.md:%d" % n for n in range(1, len(self.SPELLINGS) + 1)])

    def test_the_scan_uses_only_the_patterns_file_it_is_given(self):
        given = self.tmp / "only.txt"
        given.write_text("\\bacmecorp\\b\n")
        used, real = set(), rs.grep_run

        def spy(cmd, cwd=None, input=None):
            if "-f" in cmd:
                used.update(line for line in Path(cmd[cmd.index("-f") + 1]).read_bytes().split(b"\n") if line)
            return real(cmd, cwd=cwd, input=input)

        release = self.build(self.commit(self.base_files(**{"docs/a.md": "see Acmecorp\n"})), patterns=given)
        with mock.patch.object(rs, "grep_run", side_effect=spy):
            found = rs.check_patterns(release.repo, given)
        self.assertEqual([f.where for f in found.findings], ["docs/a.md:1"])
        self.assertEqual(used, {b"\\bacmecorp\\b"})

    def test_words_near_the_ones_in_the_patterns_file_do_not_hit(self):
        text = "an api gateway\nthe acme tool\nport 4243 and 14241\nclient.key alone\n"
        found = self.build(self.commit(self.base_files(**{"docs/a.md": text}))).check("patterns")
        self.assertTrue(found.ok, found.findings)

    def test_the_patterns_hit_in_file_names_the_commit_message_and_the_notes(self):
        release = self.build(self.commit(self.base_files(**{"docs/%s.md" % self.GW: "fine\n"})))
        self.assertEqual([f.where for f in release.check("patterns").findings], ["docs/%s.md" % self.GW])
        found = rs.check_patterns(release.repo, self.patterns, extra={"commit message": "uses " + self.PORT, "RELEASE_NOTES.md": self.PROXY + "\n"})
        self.assertEqual(sorted(f.where for f in found.findings), ["RELEASE_NOTES.md:1", "commit message:1", "docs/%s.md" % self.GW])

    def test_a_hit_on_a_port_number_can_be_excepted_by_an_exact_entry(self):
        score = "0.%s229747772217" % self.PORT
        files = {"data.json": "[%s]\n" % score, EXCEPTIONS: tsv(("data.json", score, "a similarity score"))}
        self.assertTrue(self.build(self.commit(self.base_files(**files))).check("patterns").ok)

    def test_a_working_tree_encoding_cannot_hide_a_hit_from_the_scan(self):
        write(self.src, self.base_files())
        (self.src / ".gitattributes").write_text("hidden.txt working-tree-encoding=UTF-16\n")
        (self.src / "hidden.txt").write_bytes("fine\nsee Acmecorp today\n".encode("utf-16"))
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "encoded")
        release = self.build()
        self.assertNotIn(b"Acmecorp", (release.repo / "hidden.txt").read_bytes())
        found = release.check("patterns")
        self.assertIn("hidden.txt:2", [f.where for f in found.findings])
        self.assertFalse(release.check("archive").ok)

    def test_the_committed_blob_is_scanned_not_the_checkout(self):
        release = self.build(self.commit(self.base_files(**{"docs/a.md": "see Acmecorp\n", "docs/b.md": "fine\n"})))
        os.remove(str(release.repo / "docs" / "a.md"))
        (release.repo / "docs" / "b.md").write_text("see Acmecorp now\n")
        found = rs.check_patterns(release.repo, self.patterns)
        self.assertEqual([f.where for f in found.findings], ["docs/a.md:1"])

    def test_a_symlink_target_is_checked(self):
        write(self.src, self.base_files())
        os.symlink("acmecorp.txt", self.src / "link")
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "link")
        found = self.build().check("patterns")
        self.assertFalse(found.ok)

    def test_a_missing_or_empty_patterns_file_stops_the_run_before_anything_is_written(self):
        head = self.commit(self.base_files())
        empty = self.tmp / "empty.txt"
        empty.write_text("")
        for path, why in ((self.tmp / "none.txt", "cannot read the patterns file"), (empty, "is empty")):
            with self.assertRaisesRegex(rs.ReleaseError, why):
                self.build(head, patterns=path)
            self.assertFalse(self.out.exists())

    @unittest.skipIf(os.geteuid() == 0, "root reads a file whatever its mode")
    def test_an_unreadable_patterns_file_stops_the_run(self):
        head = self.commit(self.base_files())
        locked = self.tmp / "locked.txt"
        locked.write_text("acmecorp\n")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o644)
        with self.assertRaisesRegex(rs.ReleaseError, "cannot read the patterns file"):
            self.build(head, patterns=locked)

    def test_the_command_needs_a_patterns_file(self):
        self.commit(self.base_files())
        argv = ["HEAD", str(self.out), "--source", str(self.src), "--author", AUTHOR, "--credential", CREDENTIAL, "--no-suites"]
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
            rs.main(argv)
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("--patterns", err.getvalue())

    def test_a_blank_line_in_the_patterns_file_is_refused(self):
        head = self.commit(self.base_files())
        self.patterns.write_text("acmecorp\n\n")
        with self.assertRaisesRegex(rs.ReleaseError, "blank line"):
            self.build(head)

    def test_the_commit_message_is_checked(self):
        release = self.build(self.commit(self.base_files()))
        self.assertTrue(release.check("patterns").ok)
        found = rs.check_patterns(release.repo, self.patterns, extra={"commit message": "Acmecorp 1"})
        self.assertEqual([f.where for f in found.findings], ["commit message:1"])


class NotesTest(SourceCase):
    def test_the_notes_sit_beside_the_repo_and_carry_the_readmes_description_and_limits(self):
        release = self.build(self.with_history())
        self.assertEqual(release.notes, self.out / "RELEASE_NOTES.md")
        self.assertEqual(release.repo, self.out / "repo")
        self.assertNotIn("RELEASE_NOTES.md", git(release.repo, "ls-files"))
        notes = release.notes.read_text()
        self.assertTrue(notes.startswith("# Widgetly v1.2.3\n"))
        self.assertIn("A small tool made up for tests. The command is `wgt`.\n\nIt does one thing, across two lines.\n", notes)
        self.assertIn("## Known limits\n\n- The first limit wraps onto a second line.\n- The second limit.\n", notes)
        self.assertNotIn("Run it.", notes)
        self.assertNotIn("Run the tests.", notes)

    def test_a_readme_without_known_limits_is_refused(self):
        head = self.commit(self.base_files(**{"README.md": README.replace("Known limits:", "Caveats:")}))
        with self.assertRaisesRegex(rs.ReleaseError, "Known limits"):
            self.build(head)

    def test_the_known_limits_of_the_real_readme_come_out_whole(self):
        text = (REPO / "README.md").read_text()
        notes = rs.release_notes(text, "v0.0.0")
        bullets = re.findall(r"^- ", notes, re.M)
        listed = text.split("Known limits:")[1].split("\n## ")[0]
        self.assertEqual(len(bullets), len(re.findall(r"^- ", listed, re.M)))


class ArchiveTest(SourceCase):
    def test_the_archive_is_what_git_archive_builds_for_the_tag(self):
        release = self.build(self.with_history())
        self.assertEqual(release.archive, self.out / "v1.2.3.tar.gz")
        with tarfile.open(str(release.archive)) as tar:
            names = tar.getnames()
        self.assertTrue(all(n.split("/")[0] == "widgetly-1.2.3" for n in names), names)
        expected = subprocess.run(["git", "archive", "--format=tar", "--prefix=widgetly-1.2.3/", "v1.2.3"],
                                  cwd=str(release.repo), capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(expected)) as ref, tarfile.open(str(release.archive)) as tar:
            self.assertEqual(tar.getnames(), ref.getnames())
            for member in ref.getmembers():
                if member.isfile():
                    self.assertEqual(tar.extractfile(member.name).read(), ref.extractfile(member).read())
                    self.assertEqual(tar.getmember(member.name).mode, member.mode)

    def test_the_archive_is_the_same_bytes_each_time(self):
        head = self.with_history()
        first = self.build(head).archive.read_bytes()
        shutil.rmtree(str(self.out))
        self.assertEqual(self.build(head).archive.read_bytes(), first)

    def test_the_archive_check_needs_the_installer_inside(self):
        release = self.build(self.commit(self.base_files()))
        self.assertTrue(release.check("archive").ok)
        bare = {k: v for k, v in self.base_files().items() if k != "install.sh"}
        shutil.rmtree(str(self.out))
        self.assertFalse(self.build(self.commit(bare, remove=["install.sh"])).check("archive").ok)

    def test_export_ignore_leaves_files_and_folders_out_and_the_archive_check_still_passes(self):
        files = self.base_files(**{".gitattributes": "notes.txt export-ignore\ndocs/ export-ignore\n", "notes.txt": "x\n",
                                   "docs/a.md": "y\n", "docs/sub/b.md": "z\n", "keep/c.md": "w\n"})
        release = self.build(self.commit(files))
        with tarfile.open(str(release.archive)) as tar:
            names = tar.getnames()
        self.assertFalse([n for n in names if "notes.txt" in n or "/docs" in n], names)
        self.assertIn("widgetly-1.2.3/keep/c.md", names)
        found = release.check("archive")
        self.assertTrue(found.ok, found.findings)

    def test_export_subst_fails_the_archive_check_with_its_reason(self):
        files = self.base_files(**{".gitattributes": "a.txt export-subst\n", "a.txt": "$Format:%H$\n"})
        found = self.build(self.commit(files)).check("archive")
        self.assertFalse(found.ok)
        self.assertTrue(any(f.where.endswith("a.txt") and "export-subst" in f.text for f in found.findings), found.findings)


class ArchiveExactTest(SourceCase):
    def setUp(self):
        super().setUp()
        write(self.src, self.base_files(**{"docs/a.md": "alpha\n", "docs/b.md": "bravo\n", "bin/run.sh": "#!/bin/sh\n"}))
        os.chmod(self.src / "bin" / "run.sh", 0o755)
        os.symlink("../docs/a.md", self.src / "bin" / "link")
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "files")
        self.release = self.build()
        self.prefix = "widgetly-1.2.3/"

    def tampered(self, change):
        with tarfile.open(str(self.release.archive)) as tar:
            members = [(m, tar.extractfile(m).read() if m.isfile() else None) for m in tar.getmembers()]
        path = self.tmp / "tampered.tar.gz"
        with tarfile.open(str(path), "w:gz") as out:
            for info, data in change(members):
                out.addfile(info, io.BytesIO(data) if data is not None else None)
        return path

    def edit(self, member, **fields):
        def change(members):
            result = []
            for info, data in members:
                if info.name == self.prefix + member:
                    info = copy.copy(info)
                    info.name = fields.get("name", info.name)
                    info.mode = fields.get("mode", info.mode)
                    if "data" in fields:
                        data = fields["data"]
                        info.size = len(data)
                    if "type" in fields:
                        info.type, info.linkname, data, info.size = fields["type"], fields.get("linkname", ""), None, 0
                result.append((info, data))
            return result
        return change

    def verdict(self, change):
        return rs.check_archive(self.release.repo, self.tampered(change), self.prefix)

    def test_the_archive_git_built_passes(self):
        found = rs.check_archive(self.release.repo, self.release.archive, self.prefix)
        self.assertTrue(found.ok, found.findings)

    def test_bytes_after_the_gzip_stream_or_the_tar_stream_fail(self):
        raw = self.release.archive.read_bytes()
        path = self.tmp / "junk.tar.gz"
        path.write_bytes(raw + b"junk")
        found = rs.check_archive(self.release.repo, path, self.prefix)
        self.assertFalse(found.ok)
        self.assertIn("clean gzip", "\n".join(f.text for f in found.findings))
        import gzip
        inner = gzip.decompress(raw)
        path.write_bytes(gzip.compress(inner + b"\0" * 1024 + b"tail"))
        self.assertFalse(rs.check_archive(self.release.repo, path, self.prefix).ok)
        path.write_bytes(b"not an archive")
        self.assertFalse(rs.check_archive(self.release.repo, path, self.prefix).ok)

    def test_a_file_swapped_for_another_of_the_same_count_fails(self):
        found = self.verdict(self.edit("docs/b.md", name=self.prefix + "docs/c.md"))
        self.assertFalse(found.ok)
        text = "\n".join(f.where + " " + f.text for f in found.findings)
        self.assertIn("docs/b.md", text)
        self.assertIn("docs/c.md", text)

    def test_a_file_with_other_contents_fails(self):
        found = self.verdict(self.edit("docs/b.md", data=b"not bravo\n"))
        self.assertFalse(found.ok)
        self.assertIn("docs/b.md", "\n".join(f.where for f in found.findings))

    def test_a_file_with_another_mode_fails(self):
        for name, mode in (("bin/run.sh", 0o664), ("docs/a.md", 0o775), ("docs/a.md", 0o666)):
            self.assertFalse(self.verdict(self.edit(name, mode=mode)).ok, (name, oct(mode)))

    def test_a_link_turned_into_a_file_or_pointed_elsewhere_fails(self):
        self.assertFalse(self.verdict(self.edit("bin/link", type=tarfile.SYMTYPE, linkname="../docs/b.md")).ok)

        def as_file(members):
            result = []
            for info, data in members:
                if info.name.endswith("bin/link"):
                    info = tarfile.TarInfo(info.name)
                    info.mode, data, info.size = 0o777, b"../docs/a.md", len(b"../docs/a.md")
                result.append((info, data))
            return result
        self.assertFalse(self.verdict(as_file).ok)

    def test_an_extra_missing_or_doubled_member_fails(self):
        def extra(members):
            info = tarfile.TarInfo(self.prefix + "extra.txt")
            info.size = 1
            return members + [(info, b"x")]
        self.assertFalse(self.verdict(extra).ok)
        self.assertFalse(self.verdict(lambda members: [m for m in members if not m[0].name.endswith("docs/b.md")]).ok)
        self.assertFalse(self.verdict(lambda members: members + [m for m in members if m[0].name.endswith("docs/b.md")]).ok)

    def test_a_member_outside_the_prefix_or_under_another_one_fails(self):
        found = self.verdict(self.edit("docs/b.md", name="widgetly-9.9.9/docs/b.md"))
        self.assertFalse(found.ok)
        self.assertFalse(self.verdict(self.edit("docs/b.md", name="docs/b.md")).ok)

    def test_a_missing_or_extra_folder_member_fails(self):
        self.assertFalse(self.verdict(lambda members: [m for m in members if m[0].name != self.prefix + "docs"]).ok)

        def extra(members):
            info = tarfile.TarInfo(self.prefix + "nothing/")
            info.type = tarfile.DIRTYPE
            info.mode = 0o775
            return members + [(info, None)]
        self.assertFalse(self.verdict(extra).ok)


class AwkwardNamesTest(SourceCase):
    def commit_awkward(self, name, text="fine\n", link=None):
        write(self.src, self.base_files())
        if link:
            os.symlink(link, self.src / name)
        else:
            (self.src / name).write_text(text)
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "awkward")

    def test_a_symlink_whose_target_holds_a_line_break_is_scanned_as_text(self):
        self.commit_awkward("link", link="safe\n" + PORT)
        release = self.build()
        found = release.check("patterns")
        self.assertEqual([f.where for f in found.findings], ["link:2"])
        self.assertTrue(release.check("archive").ok, release.check("archive").findings)

    def test_a_symlink_target_without_a_hit_passes(self):
        self.commit_awkward("link", link="safe\nalso safe")
        self.assertTrue(self.build().check("patterns").ok)

    def test_a_file_name_with_a_line_break_does_not_crash_any_check(self):
        self.commit_awkward("x\ny", "fine\n")
        release = self.build()
        self.assertTrue(release.ok, release.report())

    def test_a_hit_in_a_name_with_a_line_break_is_reported_with_the_break_spelled_out(self):
        self.commit_awkward("fine\nacmecorp.txt", "fine\n")
        release = self.build()
        found = release.check("patterns")
        self.assertEqual([f.where for f in found.findings], ["fine\\x0aacmecorp.txt"])
        self.assertNotIn("fine\nacmecorp", release.report())

    def test_the_report_and_the_checks_file_come_out_whole_for_awkward_names(self):
        self.commit_awkward("x\ny", "see Acmecorp\n")
        code, text = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("x\\x0ay:1", text)
        self.assertEqual((self.out / "CHECKS.txt").read_text(), text)

    def test_a_check_that_raises_is_reported_as_a_failure_and_the_rest_still_run(self):
        self.commit(self.base_files())
        with mock.patch.object(rs, "check_credential", side_effect=RuntimeError("boom on purpose")):
            release = self.build()
        found = release.check("credential")
        self.assertFalse(found.ok)
        self.assertIn("RuntimeError: boom on purpose", "\n".join(f.text for f in found.findings))
        self.assertTrue(release.check("history").ok)
        self.assertFalse(release.ok)
        self.assertIn("FAIL credential", release.report())

    def test_the_command_writes_checks_when_a_check_raises(self):
        self.commit(self.base_files())
        with mock.patch.object(rs, "check_patterns", side_effect=ValueError("bad on purpose")):
            code, text = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("ValueError: bad on purpose", text)
        self.assertEqual((self.out / "CHECKS.txt").read_text(), text)


def gz(data):
    return gzip.compress(data, mtime=0)


def png_bytes(width=32, height=32):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    rows = b"".join(b"\0" + bytes(v % 256 for x in range(width) for v in (x * 8, y * 8, (x + y) * 4)) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class FilesTest(SourceCase):
    BINARIES = "tools/release_binaries.tsv"
    NOT_TEXT = {"x/notes.gz": gz(b"fine\n"), "x/chart.png": png_bytes(),
                "x/wide.txt": "fine words\n".encode("utf-16"), "x/latin.txt": b"caf\xe9\n", "x/nul.txt": b"a\0b\n"}

    def commit_bytes(self, files, listed=None, links=()):
        for name, data in files.items():
            path = self.src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data.encode() if isinstance(data, str) else data)
        extra = {self.BINARIES: listed} if listed is not None else {}
        write(self.src, self.base_files(**extra))
        for name, target in links:
            (self.src / name).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, str(self.src / name))
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "files")

    def wheres(self, found):
        return sorted(f.where for f in found.findings)

    def test_a_file_that_is_not_utf8_text_without_a_nul_fails_unless_it_is_listed(self):
        self.commit_bytes(dict(self.NOT_TEXT, **{"x/plain.txt": "plain words, caf\u00e9\n"}))
        release = self.build()
        found = release.check("files")
        self.assertFalse(found.ok)
        self.assertFalse(release.ok)
        self.assertEqual(self.wheres(found), sorted(self.NOT_TEXT))
        self.assertTrue(all("not UTF-8 text" in f.text for f in found.findings), found.findings)

    def test_a_listed_binary_with_its_sha256_passes(self):
        listed = tsv(*[(name, sha256(data), "a made-up binary for a test") for name, data in sorted(self.NOT_TEXT.items())])
        self.commit_bytes(self.NOT_TEXT, listed)
        found = self.build().check("files")
        self.assertTrue(found.ok, found.findings)
        self.assertIn("%d listed binaries" % len(self.NOT_TEXT), found.summary)

    def test_a_listed_binary_changed_after_it_was_listed_fails(self):
        before = png_bytes()
        after = png_bytes(width=33)
        self.commit_bytes({"x/chart.png": after}, tsv(("x/chart.png", sha256(before), "a made-up chart")))
        found = self.build().check("files")
        self.assertEqual(self.wheres(found), ["x/chart.png"])
        self.assertIn(sha256(after), found.findings[0].text)

    def test_a_list_entry_that_names_no_file_or_a_text_file_fails(self):
        listed = tsv(("x/gone.png", sha256(b"gone"), "removed since"), ("README.md", sha256(README.encode()), "not binary"))
        self.commit_bytes({}, listed)
        found = self.build().check("files")
        self.assertEqual(self.wheres(found), [self.BINARIES + ": README.md", self.BINARIES + ": x/gone.png"])

    def test_a_list_line_that_is_not_a_path_a_sha256_and_a_reason_stops_the_run(self):
        for listed in ("x/a.png\tnot-a-hash\ta reason\n", "x/a.png\t%s\n" % sha256(b"a")):
            self.commit_bytes({"x/a.png": b"a"}, listed)
            with self.assertRaisesRegex(rs.ReleaseError, self.BINARIES):
                self.build()

    def test_a_link_that_points_out_of_the_tree_fails(self):
        inside = [("docs/up", "../README.md"), ("top", "docs/a.md"), ("docs/self", "."), ("dangling", "docs/none.md")]
        outside = [("docs/out", "../../etc/passwd"), ("abs", "/etc/passwd"), ("deep/x/y", "../../../outside")]
        self.commit_bytes({"docs/a.md": "fine\n"}, links=inside + outside)
        found = self.build().check("links")
        self.assertEqual(self.wheres(found), sorted(name for name, _ in outside))
        self.assertTrue(all("outside the tree" in f.text for f in found.findings), found.findings)

    def test_a_path_that_is_not_utf8_fails_and_exceptions_match_names_exactly(self):
        probe = self.src / os.fsdecode(b"\xff.probe")
        try:
            probe.write_bytes(b"")
        except OSError as error:  # macOS's file system refuses such a name; the release is built on Linux
            self.skipTest("this file system refuses a file name that is not UTF-8: " + error.strerror)
        probe.unlink()
        self.commit_bytes({os.fsdecode(b"\xff.txt"): "Acmecorp\n", "\ufffd.txt": "Acmecorp\n",
                           EXCEPTIONS: tsv(("\ufffd.txt", "Acmecorp", "a made-up firm"))})
        release = self.build()
        self.assertEqual(self.wheres(release.check("files")), ["\\xff.txt"])
        found = release.check("patterns")
        self.assertEqual([f.where for f in found.findings], ["\\xff.txt:1"])
        self.assertEqual([f.where for f in found.allowed], ["\ufffd.txt:1"])

    def test_only_a_refused_file_name_skips_the_test_of_one_that_is_not_utf8(self):
        # any other failure to write the file is the test's error, never a skip
        result = unittest.TestResult()
        with mock.patch.object(Path, "write_bytes", side_effect=OSError(5, "Input/output error")):
            FilesTest("test_a_path_that_is_not_utf8_fails_and_exceptions_match_names_exactly").run(result)
        self.assertEqual((len(result.errors), len(result.skipped)), (1, 0), result.skipped)

    def test_a_submodule_entry_fails(self):
        self.commit_bytes({})
        git(self.src, "update-index", "--add", "--cacheinfo", "160000,%s,sub" % git(self.src, "rev-parse", "HEAD"))
        git(self.src, "commit", "-q", "-m", "submodule")
        found = self.build().check("files")
        self.assertEqual(self.wheres(found), ["sub"])


class ForbiddenNamesTest(SourceCase):
    GW, PROXY = GW, PROXY

    def test_a_forbidden_string_in_the_repo_name_fails_the_release_and_does_not_flood_the_report(self):
        name = "%s-%s-%s" % (self.GW, PORT, self.PROXY)
        release = self.build(self.commit(self.base_files()), repo_name=name)
        self.assertFalse(release.ok)
        found = release.check("patterns")
        wheres = [f.where for f in found.findings]
        self.assertIn("repo name:1", wheres)
        self.assertTrue(any("archive folder" in w for w in wheres), wheres)
        self.assertLess(len(found.findings), 6, wheres)

    def test_the_credential_in_the_repo_name_fails_the_credential_check(self):
        release = self.build(self.commit(self.base_files()), repo_name=CREDENTIAL)
        found = release.check("credential")
        self.assertFalse(found.ok)
        self.assertIn("repo name", [f.where for f in found.findings])

    def test_a_forbidden_string_in_the_tag_fails_the_release(self):
        files = self.base_files(**{"kb/__init__.py": '__version__ = "1.2.3-%s"\n' % PORT})
        release = self.build(self.commit(files))
        self.assertFalse(release.ok)
        self.assertIn("tag:1", [f.where for f in release.check("patterns").findings])

    def test_a_clean_repo_name_and_tag_pass(self):
        release = self.build(self.commit(self.base_files()))
        self.assertTrue(release.check("patterns").ok)
        self.assertTrue(release.check("credential").ok)


class IdentityTest(SourceCase):
    def test_a_forbidden_string_or_a_pattern_in_the_author_fails_the_patterns_check(self):
        head = self.commit(self.base_files())
        for author in (GW + " <test@example.invalid>", "Builds <acme" + "corp@example.invalid>"):
            shutil.rmtree(str(self.out), ignore_errors=True)
            found = self.build(head, author=author).check("patterns")
            self.assertEqual([f.where for f in found.findings], ["commit identity:1", "commit identity:2"], author)

    def test_the_credential_in_the_author_fails_the_credential_check(self):
        found = self.build(self.commit(self.base_files()), author="Builds <%s@example.invalid>" % CREDENTIAL).check("credential")
        self.assertIn("commit identity", [f.where for f in found.findings])

    def test_an_exact_entry_for_the_whole_identity_excuses_it(self):
        author = "Acme" + "corp Builds <builds@example.invalid>"
        files = self.base_files(**{EXCEPTIONS: tsv(("commit identity", author, "the made-up author"))})
        found = self.build(self.commit(files), author=author).check("patterns")
        self.assertTrue(found.ok, found.findings)
        self.assertEqual([f.where for f in found.allowed], ["commit identity:1", "commit identity:2"])
        self.assertEqual(found.notes, [])


class TextLabelTest(SourceCase):
    """An exception for one of the texts the tool checks besides the files (the commit identity, the notes, the commit
    message) covers that text only, never a committed file that happens to share its name."""

    def test_a_file_named_like_a_checked_text_takes_none_of_its_exceptions(self):
        author = "Acme" + "corp Builds <builds@example.invalid>"
        for name in ("commit identity", "commit message", "tag"):
            files = self.base_files(**{EXCEPTIONS: tsv(("commit identity", author, "the made-up author"), (name, author, "x")),
                                       name: author + "\n"})
            shutil.rmtree(str(self.out), ignore_errors=True)
            found = self.build(self.commit(files), author=author).check("patterns")
            with self.subTest(name=name):
                self.assertFalse(found.ok)
                self.assertIn("%s:1" % name, [f.where for f in found.findings])

    def test_the_same_string_in_a_file_with_its_own_entry_is_excused(self):
        author = "Acme" + "corp Builds <builds@example.invalid>"
        files = self.base_files(**{EXCEPTIONS: tsv(("commit identity", author, "the made-up author"),
                                                   ("docs/credits.md", author, "the made-up author in the credits")),
                                   "docs/credits.md": author + "\n"})
        found = self.build(self.commit(files), author=author).check("patterns")
        self.assertTrue(found.ok, found.findings)


class GitFailureTest(SourceCase):
    def shimmed(self, body):
        folder = self.tmp / "shim"
        folder.mkdir(exist_ok=True)
        script = folder / "git"
        script.write_text('#!/bin/sh\ncase "$*" in\n  *"--batch-all-objects --batch") %s ;;\nesac\nexec %s "$@"\n'
                          % (body, shutil.which("git")))
        script.chmod(0o755)
        return mock.patch.dict(os.environ, {"PATH": "%s%s%s" % (folder, os.pathsep, os.environ["PATH"])})

    def credential(self, body):
        release = self.build(self.commit(self.base_files()))
        with self.shimmed(body):
            return rs.guarded("credential", rs.check_credential, release.repo, [CREDENTIAL])

    def test_an_object_reader_that_fails_fails_the_check_with_its_detail(self):
        found = self.credential("echo reader broke on purpose >&2; exit 3")
        self.assertFalse(found.ok)
        self.assertIn("reader broke on purpose", "\n".join(f.text for f in found.findings))

    def test_an_object_reader_that_reads_fewer_objects_than_the_repo_holds_fails(self):
        found = self.credential("exit 0")
        self.assertFalse(found.ok)
        self.assertIn("read 0 objects", "\n".join(f.text for f in found.findings))

    def test_a_git_command_that_runs_past_its_time_fails(self):
        started = time.time()
        with mock.patch.object(rs, "COMMAND_TIMEOUT", 2):
            found = self.credential("exec sleep 30")
        self.assertLess(time.time() - started, 25)
        self.assertFalse(found.ok)
        self.assertIn("ran past 2 seconds", "\n".join(f.text for f in found.findings))


class CommandTest(SourceCase):
    def test_a_clean_snapshot_exits_zero_and_reports_each_check(self):
        self.commit(self.base_files())
        code, text = self.run_main()
        self.assertEqual(code, 0, text)
        self.assertIn("tag: v1.2.3", text)
        for name in ("history", "credential", "patterns", "dataset", "archive"):
            self.assertRegex(text, r"(?m)^PASS %s" % name)
        self.assertRegex(text, r"(?m)^SKIP install")
        self.assertRegex(text, r"(?m)^SKIP suites")
        self.assertEqual((self.out / "CHECKS.txt").read_text(), text)

    VERIFY = ('cd "$OUT" && shasum -a 256 -c RELEASE.sha256 && test "$(git -C repo rev-parse HEAD \'HEAD^{tree}\' | paste -sd\' \' -)" '
              '= "$(cat RELEASE.ids)" && echo "release verified"')

    def verified(self):
        done = subprocess.run(["bash", "-c", self.VERIFY], env=dict(os.environ, OUT=str(self.out)), capture_output=True, text=True)
        return done.returncode == 0 and done.stdout.endswith("release verified\n")

    def test_a_passing_run_pins_the_notes_the_archive_the_commit_and_the_tree_and_prints_the_pins_last(self):
        self.commit(self.base_files())
        code, text = self.run_main()
        self.assertEqual(code, 0, text)
        pins = (self.out / "RELEASE.sha256").read_text() + (self.out / "RELEASE.ids").read_text()
        self.assertTrue(text.endswith("RESULT: PASS\n" + pins), text)
        self.assertRegex(pins, r"^[0-9a-f]{64}  RELEASE_NOTES\.md\n[0-9a-f]{64}  v1\.2\.3\.tar\.gz\n[0-9a-f]{40} [0-9a-f]{40}\n$")
        self.assertTrue(self.verified())
        for path in (self.out / "RELEASE_NOTES.md", self.out / "v1.2.3.tar.gz"):
            data = path.read_bytes()
            path.write_bytes(data + b"x")
            self.assertFalse(self.verified(), path)
            path.write_bytes(data)
        self.assertTrue(self.verified())
        git(self.out / "repo", "commit", "-q", "--allow-empty", "-m", "moved")
        self.assertFalse(self.verified())

    def test_a_failing_run_writes_and_prints_no_pins(self):
        self.commit(self.base_files(**{"a.txt": "acmecorp\n"}))
        code, text = self.run_main()
        self.assertEqual(code, 1)
        self.assertTrue(text.endswith("RESULT: FAIL\n"), text)
        self.assertFalse((self.out / "RELEASE.sha256").exists() or (self.out / "RELEASE.ids").exists())

    def test_a_failed_check_exits_one_and_still_writes_the_folder(self):
        self.commit(self.base_files(**{"a.txt": "acmecorp\n"}))
        code, text = self.run_main()
        self.assertEqual(code, 1)
        self.assertRegex(text, r"(?m)^FAIL patterns")
        self.assertIn("a.txt:1", text)
        self.assertTrue((self.out / "v1.2.3.tar.gz").exists())
        self.assertTrue((self.out / "CHECKS.txt").exists())

    def test_the_report_caps_how_many_hits_it_prints_per_file(self):
        self.commit(self.base_files(**{"big.txt": "acmecorp\n" * 50}))
        code, text = self.run_main()
        self.assertIn("big.txt: 50 hits", text)
        self.assertLess(text.count("big.txt:"), 10)

    def test_a_refusal_prints_a_message_and_exits_two(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = rs.main(["nope", str(self.out), "--source", str(self.src), "--author", AUTHOR,
                            "--patterns", str(self.patterns), "--credential", CREDENTIAL])
        self.assertEqual(code, 2)
        self.assertIn("release_snapshot:", stderr.getvalue())


def tsv(*rows):
    return "".join("\t".join(row) + "\n" for row in rows)


class ExceptionsTest(SourceCase):
    def patterns_of(self, files, entries, **kw):
        files = dict(files)
        files[EXCEPTIONS] = tsv(*entries) if not isinstance(entries, str) else entries
        return self.build(self.commit(self.base_files(**files)), **kw).check("patterns")

    def test_a_hit_is_excepted_where_its_file_and_its_string_match(self):
        found = self.patterns_of({"docs/a.md": "fine\nsee Acmecorp today\n"}, [("docs/a.md", "Acmecorp", "a made-up firm")])
        self.assertTrue(found.ok, found.findings)
        self.assertEqual([f.where for f in found.allowed], ["docs/a.md:2"])
        self.assertEqual(found.notes, [])

    def test_the_same_string_in_another_file_still_fails(self):
        files = {"docs/a.md": "Acmecorp\n", "docs/b.md": "Acmecorp\n"}
        found = self.patterns_of(files, [("docs/a.md", "Acmecorp", "a made-up firm")])
        self.assertEqual([f.where for f in found.findings], ["docs/b.md:1"])

    def test_another_string_in_the_same_file_still_fails(self):
        files = {"docs/a.md": "Acmecorp\nboxroom\n"}
        found = self.patterns_of(files, [("docs/a.md", "Acmecorp", "a made-up firm")])
        self.assertEqual([f.where for f in found.findings], ["docs/a.md:2"])

    def test_the_string_is_literal_and_exact(self):
        files = {"a.md": "see Acmecorp today\n"}
        for string in ("acmecorp", "Acme.*", "Acmecorp today!", "Acmecorp.", "[A]cmecorp"):
            shutil.rmtree(str(self.out), ignore_errors=True)
            found = self.patterns_of(files, [("a.md", string, "no")])
            self.assertEqual([f.where for f in found.findings], ["a.md:1"], string)
            self.assertEqual(len(found.notes), 1, string)

    def test_other_text_on_the_line_that_hits_still_fails(self):
        found = self.patterns_of({"a.md": "Acmecorp and boxroom\n"}, [("a.md", "Acmecorp", "a made-up firm")])
        self.assertEqual([f.where for f in found.findings], ["a.md:1"])
        self.assertEqual(found.notes, [])

    def test_every_occurrence_of_the_string_on_a_line_is_covered(self):
        found = self.patterns_of({"a.md": "Acmecorp, then Acmecorp\n"}, [("a.md", "Acmecorp", "a made-up firm")])
        self.assertTrue(found.ok)
        self.assertEqual(len(found.allowed), 1)

    def test_more_than_one_entry_can_cover_a_line(self):
        found = self.patterns_of({"a.md": "Acmecorp and boxroom\n"},
                                 [("a.md", "Acmecorp", "a made-up firm"), ("a.md", " boxroom", "a made-up room")])
        self.assertTrue(found.ok, found.findings)

    def test_an_entry_for_part_of_a_match_does_not_excuse_the_whole_match(self):
        found = self.patterns_of({"a.txt": "the port is %s\n" % PORT}, [("a.txt", PORT[:2], "the start of a number")])
        self.assertEqual([f.where for f in found.findings], ["a.txt:1"])
        self.assertEqual(found.notes, ["stale: a.txt: %s" % PORT[:2]])

    def test_an_entry_covers_a_match_only_where_its_string_sits_over_it(self):
        found = self.patterns_of({"a.txt": "%s and later %s\n" % (PORT[:2], PORT)}, [("a.txt", PORT[:2], "the start of a number")])
        self.assertEqual([f.where for f in found.findings], ["a.txt:1"])

    def test_one_entry_may_cover_a_match_inside_a_longer_string(self):
        score = "0.%s229747772217" % PORT
        found = self.patterns_of({"a.txt": "[%s, 1.0, %s]\n" % (score, score)}, [("a.txt", score, "a similarity score")])
        self.assertTrue(found.ok, found.findings)
        self.assertEqual(found.notes, [])

    def test_a_string_beside_a_match_must_cover_the_character_the_pattern_took_with_it(self):
        found = self.patterns_of({"a.txt": "my boxroom\n"}, [("a.txt", "boxroom", "a made-up room")])
        self.assertEqual([f.where for f in found.findings], ["a.txt:1"])
        shutil.rmtree(str(self.out))
        found = self.patterns_of({"a.txt": "my boxroom\n"}, [("a.txt", " boxroom", "a made-up room")])
        self.assertTrue(found.ok, found.findings)

    def test_two_entries_cannot_split_one_match_between_them(self):
        found = self.patterns_of({"a.txt": "see Acmecorp\n"}, [("a.txt", "Acme", "the start"), ("a.txt", "corp", "the end")])
        self.assertEqual([f.where for f in found.findings], ["a.txt:1"])

    def with_patterns(self, *lines):
        self.patterns.write_text("".join(line + "\n" for line in lines))

    def test_an_exception_for_one_match_does_not_hide_one_that_overlaps_it(self):
        self.with_patterns("abc", "bcd")
        found = self.patterns_of({"a.txt": "abcd\n"}, [("a.txt", "abc", "the first pattern")])
        self.assertEqual([f.where for f in found.findings], ["a.txt:1"])
        self.assertEqual(found.notes, [])

    def test_each_overlapping_match_is_checked_against_the_exceptions_on_its_own(self):
        self.with_patterns("abc", "bcd")
        both = self.patterns_of({"a.txt": "abcd\n"}, [("a.txt", "abc", "one"), ("a.txt", "bcd", "two")])
        self.assertTrue(both.ok, both.findings)
        shutil.rmtree(str(self.out))
        union = self.patterns_of({"a.txt": "abcd\n"}, [("a.txt", "abcd", "both at once")])
        self.assertTrue(union.ok, union.findings)

    def test_a_match_inside_a_longer_one_is_found_too(self):
        self.with_patterns("abcd", "bc")
        inner = self.patterns_of({"a.txt": "abcd\n"}, [("a.txt", "bc", "the inner one")])
        self.assertEqual([f.where for f in inner.findings], ["a.txt:1"])
        shutil.rmtree(str(self.out))
        outer = self.patterns_of({"a.txt": "abcd\n"}, [("a.txt", "abcd", "the outer one")])
        self.assertTrue(outer.ok, outer.findings)

    def test_one_pattern_matching_itself_overlapped_is_found_at_every_position(self):
        self.with_patterns("abab")
        part = self.patterns_of({"a.txt": "xababab\n"}, [("a.txt", "xabab", "the first match only")])
        self.assertEqual([f.where for f in part.findings], ["a.txt:1"])
        shutil.rmtree(str(self.out))
        whole = self.patterns_of({"a.txt": "xababab\n"}, [("a.txt", "ababab", "both matches")])
        self.assertTrue(whole.ok, whole.findings)

    def test_finding_the_overlaps_adds_no_matches_that_are_not_there(self):
        self.with_patterns("(^|[^-a-z0-9])boxroom\\b", "\\bacmecorp\\b")
        found = self.patterns_of({"a.txt": "my boxroom and Acmecorp, not unboxroom or x-boxroom\n"},
                                 [("a.txt", " boxroom", "a room"), ("a.txt", "Acmecorp", "a firm")])
        self.assertTrue(found.ok, found.findings)

    def test_a_match_past_an_exception_among_many_overlapping_ones_fails(self):
        # 65 overlapping runs of 65 digits; the exception covers all but the last digit, and occurs once
        self.with_patterns("[0-9]{65}")
        digits = "8" + "7" * 128
        found = self.patterns_of({"a.txt": digits + "\n"}, [("a.txt", digits[:-1], "all but the last digit")])
        self.assertFalse(found.ok)
        self.assertEqual([f.where for f in found.findings], ["a.txt:1"])
        shutil.rmtree(str(self.out))
        whole = self.patterns_of({"a.txt": digits + "\n"}, [("a.txt", digits, "every digit")])
        self.assertTrue(whole.ok, whole.findings)

    def test_an_overlap_search_that_stops_at_its_limit_fails_and_says_so(self):
        self.with_patterns("[0-9]{6}")
        with mock.patch.object(rs, "OVERLAP_CHECKS", 3):
            found = self.patterns_of({"a.txt": "7" * 12 + "\n"}, [("a.txt", "7" * 12, "every digit")])
        self.assertFalse(found.ok)
        self.assertEqual([f.where for f in found.findings], ["a.txt:1", EXCEPTIONS + ":1"])
        self.assertTrue(all("stopped" in f.text for f in found.findings), found.findings)

    def test_a_match_that_runs_into_a_later_match_is_found(self):
        # grep -o reports "ab" and then "cd"; "bcd" starts inside the first and runs into the second
        self.with_patterns("ab|bcd|cd")
        found = self.patterns_of({"a.txt": "xabcd\n"}, [("a.txt", "ab", "one"), ("a.txt", "cd", "two")])
        self.assertFalse(found.ok)
        self.assertEqual([f.where for f in found.findings], ["a.txt:1"])
        shutil.rmtree(str(self.out))
        whole = self.patterns_of({"a.txt": "xabcd\n"}, [("a.txt", "abcd", "all three")])
        self.assertTrue(whole.ok, whole.findings)

    def test_a_match_read_with_the_byte_before_it_as_context(self):
        # "bc" matches only where no word boundary stands before it, as inside "abc"
        self.with_patterns("ab|\\Bbc")
        found = self.patterns_of({"a.txt": "abc\n"}, [("a.txt", "ab", "the first match only")])
        self.assertFalse(found.ok)
        self.assertEqual([f.where for f in found.findings], ["a.txt:1"])

    def test_a_pattern_with_a_backreference_stops_the_run(self):
        self.with_patterns("(ab)\\1")
        with self.assertRaisesRegex(rs.ReleaseError, "backreference"):
            self.patterns_of({"a.txt": "abab\n"}, [])

    def test_blanking_a_match_finds_no_match_the_text_does_not_hold(self):
        self.with_patterns("quux|[^q]uuxzz")
        found = self.patterns_of({"a.txt": "quuxzz\n"}, [("a.txt", "quux", "the only match")])
        self.assertTrue(found.ok, found.findings)
        self.assertEqual([f.where for f in found.allowed], ["a.txt:1"])

    def test_a_file_name_cannot_be_excepted(self):
        found = self.patterns_of({"acmecorp-notes.txt": "fine\n"},
                                 [("acmecorp-notes.txt", "acmecorp-notes.txt", "no")])
        self.assertEqual([f.where for f in found.findings], ["acmecorp-notes.txt", EXCEPTIONS + ":1"])

    def test_an_entry_that_matches_nothing_is_reported_as_stale_and_does_not_fail(self):
        files = {"a.md": "Acmecorp\nhello\n", "b.md": "hello\n"}
        entries = [("a.md", "Acmecorp", "a made-up firm"), ("a.md", "hello", "not a hit"),
                   ("b.md", "Acmecorp", "wrong file"), ("gone.md", "Acmecorp", "no such file")]
        found = self.patterns_of(files, entries)
        self.assertTrue(found.ok, found.findings)
        self.assertEqual(found.notes, ["stale: a.md: hello", "stale: b.md: Acmecorp", "stale: gone.md: Acmecorp"])
        self.assertIn("3 stale", found.summary)

    def test_an_entry_for_a_line_that_still_fails_is_not_stale(self):
        found = self.patterns_of({"a.md": "Acmecorp boxroom\n"}, [("a.md", "Acmecorp", "a made-up firm")])
        self.assertFalse(found.ok)
        self.assertEqual(found.notes, [])

    def test_the_lists_comments_paths_and_reasons_are_scanned_and_only_each_entrys_string_is_left_out(self):
        listed = "# about acme" + "corp\n" + tsv(("a.md", "Acmecorp", "a made-up firm"), ("a.md", "zzz", "the boxroom too"))
        found = self.patterns_of({"a.md": "Acmecorp\n"}, listed)
        self.assertEqual([f.where for f in found.findings], [EXCEPTIONS + ":1", EXCEPTIONS + ":3"])
        self.assertEqual([f.where for f in found.allowed], ["a.md:1"])

    def test_the_lists_own_lines_are_not_scanned_but_its_name_still_is(self):
        found = self.patterns_of({"a.md": "Acmecorp\n"}, [("a.md", "Acmecorp", "a made-up firm")])
        self.assertTrue(found.ok)
        self.assertFalse([f for f in found.findings + found.allowed if f.where.startswith(EXCEPTIONS)])

    def test_the_list_is_read_from_the_commit_not_the_working_tree(self):
        head = self.commit(self.base_files(**{"a.md": "Acmecorp\n", EXCEPTIONS: tsv(("a.md", "Acmecorp", "a made-up firm"))}))
        write(self.src, {EXCEPTIONS: ""})
        self.assertTrue(self.build(head).check("patterns").ok)

    def test_blank_lines_and_comment_lines_are_skipped(self):
        text = "# the reviewed exceptions\n\n" + tsv(("a.md", "Acmecorp", "a made-up firm")) + "\n"
        self.assertTrue(self.patterns_of({"a.md": "Acmecorp\n"}, text).ok)

    def test_a_reason_may_hold_spaces_and_a_string_may_hold_spaces(self):
        found = self.patterns_of({"a.md": "see Acmecorp Ltd today\n"}, [("a.md", "Acmecorp Ltd", "a made-up firm, named in full")])
        self.assertTrue(found.ok, found.findings)

    def test_a_list_that_is_not_a_path_a_string_and_a_reason_stops_the_run(self):
        for n, text in enumerate(["a.md\tAcmecorp\n", "a.md\tAcmecorp\t\n", "\tAcmecorp\treason\n", "a.md\t\treason\n",
                                  "a.md\tAcmecorp\treason\textra\n", "a.md Acmecorp reason\n"]):
            self.out = self.tmp / ("out%d" % n)
            head = self.commit(self.base_files(**{EXCEPTIONS: text}))
            with self.assertRaisesRegex(rs.ReleaseError, r"release_exceptions.tsv.*line 1"):
                self.build(head)
            self.assertFalse(self.out.exists())

    def test_the_report_names_excepted_hits_and_stale_entries(self):
        self.commit(self.base_files(**{"a.md": "Acmecorp\n", EXCEPTIONS: tsv(("a.md", "Acmecorp", "a made-up firm"), ("b.md", "x", "stale"))}))
        code, text = self.run_main()
        self.assertEqual(code, 0, text)
        self.assertRegex(text, r"(?m)^PASS patterns: .*1 excepted.*1 stale")
        self.assertIn("excepted a.md: 1 hit", text)
        self.assertIn("stale: b.md: x", text)

    def test_the_old_allow_options_are_gone(self):
        self.commit(self.base_files())
        for option in ("--allow-link", "--allow-file"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.run_main(option, "x")


class DatasetRowsTest(SourceCase):
    def rows(self, *lines):
        return "".join(lines)

    def with_rows(self, rows, **files):
        return self.commit(self.base_files(**dict(files, **{ROWS: rows})))

    def test_a_missing_list_stops_the_run_before_anything_is_written(self):
        head = self.commit(self.base_files(), remove=[ROWS])
        with self.assertRaisesRegex(rs.ReleaseError, "DATASET_ROWS.txt"):
            self.build(head)
        self.assertFalse(self.out.exists())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_main(rev=head)[0], 2)

    def test_listed_files_are_left_out_and_everything_else_stays(self):
        head = self.with_rows("bench/rows/a.jsonl\tquotes LoCoMo\nbench/rows/b.jsonl\tquotes LongMemEval\n",
                              **{"bench/rows/a.jsonl": "{}\n", "bench/rows/b.jsonl": "{}\n", "bench/rows/c.md": "table\n",
                                 "bench/table.md": "numbers\n"})
        release = self.build(head)
        kept = git(release.repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
        every = git(self.src, "ls-tree", "-r", "--name-only", head).splitlines()
        self.assertEqual(kept, [n for n in every if n not in ("bench/rows/a.jsonl", "bench/rows/b.jsonl")])
        self.assertFalse((release.repo / "bench" / "rows" / "a.jsonl").exists())
        self.assertTrue((release.repo / "bench" / "rows" / "c.md").exists())
        self.assertEqual(release.left_out, ["bench/rows/a.jsonl", "bench/rows/b.jsonl"])
        with tarfile.open(str(release.archive)) as tar:
            self.assertFalse([n for n in tar.getnames() if n.endswith(".jsonl")])

    def test_the_list_itself_ships_and_the_source_is_untouched(self):
        head = self.with_rows("bench/a.jsonl\tquotes LoCoMo\n", **{"bench/a.jsonl": "{}\n"})
        before = git(self.src, "count-objects", "-v")
        release = self.build(head)
        self.assertIn(ROWS, git(release.repo, "ls-files").splitlines())
        self.assertEqual(git(self.src, "count-objects", "-v"), before)
        self.assertEqual(git(self.src, "ls-tree", "-r", "--name-only", head).count("bench/a.jsonl"), 1)

    def test_a_leftover_index_is_not_left_in_the_repo(self):
        head = self.with_rows("bench/a.jsonl\tquotes LoCoMo\n", **{"bench/a.jsonl": "{}\n"})
        release = self.build(head)
        self.assertEqual(sorted(p.name for p in (release.repo / ".git").iterdir() if "index" in p.name), ["index"])
        self.assertEqual(git(release.repo, "status", "--porcelain"), "")

    def test_pattern_and_credential_hits_in_a_left_out_file_do_not_count(self):
        head = self.with_rows("bench/a.jsonl\tquotes LoCoMo\n",
                              **{"bench/a.jsonl": "Acmecorp %s\n" % CREDENTIAL})
        release = self.build(head)
        self.assertTrue(release.ok, release.report())

    def test_the_dataset_check_passes_and_says_how_many_were_left_out(self):
        head = self.with_rows("bench/a.jsonl\tquotes LoCoMo\n", **{"bench/a.jsonl": "{}\n"})
        found = self.build(head).check("dataset")
        self.assertTrue(found.ok)
        self.assertIn("1 file", found.summary)

    def test_the_dataset_check_fails_if_a_listed_file_is_in_the_tree_or_the_archive(self):
        head = self.with_rows("bench/a.jsonl\tquotes LoCoMo\n", **{"bench/a.jsonl": "{}\n"})
        release = self.build(head)
        self.assertFalse(rs.check_dataset(self.src, release.archive, ["bench/a.jsonl"], "widgetly-1.2.3/").ok)
        self.assertFalse(rs.check_dataset(release.repo, release.archive, ["README.md"], "widgetly-1.2.3/").ok)
        self.assertTrue(rs.check_dataset(release.repo, release.archive, ["bench/a.jsonl"], "widgetly-1.2.3/").ok)

    def test_a_listed_path_that_is_not_a_file_in_the_commit_stops_the_run(self):
        for n, listed in enumerate(["bench/missing.jsonl", "bench"]):
            self.out = self.tmp / ("out%d" % n)
            head = self.with_rows("%s\treason\n" % listed, **{"bench/a.jsonl": "{}\n"})
            with self.assertRaisesRegex(rs.ReleaseError, "not a file"):
                self.build(head)
            self.assertFalse(self.out.exists())

    def test_a_path_that_is_not_exact_stops_the_run(self):
        for n, listed in enumerate(["/etc/passwd", "../x", "bench/../README.md", "bench//a.jsonl", "bench/a.jsonl/", "bench/*.jsonl", " bench/a.jsonl"]):
            self.out = self.tmp / ("out%d" % n)
            head = self.with_rows("%s\treason\n" % listed, **{"bench/a.jsonl": "{}\n"})
            with self.assertRaisesRegex(rs.ReleaseError, "DATASET_ROWS.txt"):
                self.build(head)

    def test_a_line_without_a_path_a_tab_and_a_reason_stops_the_run(self):
        for n, line in enumerate(["bench/a.jsonl\n", "bench/a.jsonl\t\n", "\treason\n", "bench/a.jsonl reason\n"]):
            self.out = self.tmp / ("out%d" % n)
            head = self.with_rows(line, **{"bench/a.jsonl": "{}\n"})
            with self.assertRaisesRegex(rs.ReleaseError, r"DATASET_ROWS.txt.*line 1"):
                self.build(head)

    def test_blank_and_comment_lines_are_skipped_and_a_path_may_repeat(self):
        head = self.with_rows("# rows that quote a dataset\n\nbench/a.jsonl\tr\nbench/a.jsonl\tr again\n", **{"bench/a.jsonl": "{}\n"})
        self.assertEqual(self.build(head).left_out, ["bench/a.jsonl"])

    def test_the_list_is_read_from_the_commit_not_the_working_tree(self):
        head = self.with_rows("bench/a.jsonl\tr\n", **{"bench/a.jsonl": "{}\n"})
        write(self.src, {ROWS: ""})
        self.assertEqual(self.build(head).left_out, ["bench/a.jsonl"])

    def test_the_report_says_what_was_left_out(self):
        self.with_rows("bench/a.jsonl\tr\n", **{"bench/a.jsonl": "{}\n"})
        code, text = self.run_main()
        self.assertEqual(code, 0, text)
        self.assertIn("left out: 1 (bench/a.jsonl)", text)


class SuitesTest(SourceCase):
    TESTS = """import os
import unittest


class T(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)
%s
"""

    def suites(self, repo):
        return rs.default_suites(repo, interpreters=((CURRENT, (sys.executable,)),))

    def built(self, body="", files=None, rows="", **kw):
        files = dict(files or {})
        files.setdefault("tests/test_t.py", self.TESTS % body)
        files.setdefault("tests/__init__.py", "")
        files[ROWS] = rows
        head = self.commit(self.base_files(**files))
        return self.build(head, suites=self.suites, **kw)

    def test_suites_that_pass_or_skip_with_a_reason_pass_and_the_skips_are_listed(self):
        body = "    @unittest.skip('needs a network')\n    def test_net(self):\n        pass\n"
        found = self.built(body).check("suites")
        self.assertTrue(found.ok, found.findings)
        self.assertIn("2 tests", found.summary)
        self.assertIn("1 skipped", found.summary)
        self.assertTrue(any("needs a network" in n for n in found.notes), found.notes)

    def test_a_failing_test_fails_the_release(self):
        found = self.built("    def test_bad(self):\n        self.fail('broken on purpose')\n")
        self.assertFalse(found.ok)
        self.assertFalse(found.check("suites").ok)
        self.assertTrue(any("test_bad" in f.text for f in found.check("suites").findings))

    def test_a_skip_without_a_reason_fails(self):
        found = self.built("    @unittest.skip('')\n    def test_quiet(self):\n        pass\n").check("suites")
        self.assertFalse(found.ok)
        self.assertTrue(any("without a reason" in f.text for f in found.findings), found.findings)

    def test_a_suite_that_runs_no_tests_fails(self):
        found = self.built(files={"tests/test_t.py": "x = 1\n"}).check("suites")
        self.assertFalse(found.ok)

    def test_a_test_that_runs_a_runner_of_its_own_is_counted_by_the_outer_summary(self):
        # a test may run unittest itself, which prints its own "Ran 0 tests" before the suite's own summary
        body = ("    def test_empty_suite(self):\n        result = unittest.TextTestRunner().run(unittest.TestSuite())\n"
                "        self.assertEqual(result.testsRun, 0)\n\n"
                "    @unittest.skip('a reason')\n    def test_skipped(self):\n        pass\n")
        found = self.built(body).check("suites")
        self.assertTrue(found.ok, found.findings)
        self.assertIn("skipped", found.summary)

    OUTER = """import unittest


class Outer(unittest.TestCase):
    def test_outer(self):
        class Inner(unittest.TestCase):
            @unittest.skip('an inner reason')
            def test_inner(self):
                pass

        unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Inner))
%s
"""

    def test_skips_inside_a_tests_own_runner_are_not_the_suites(self):
        found = self.built(files={"tests/test_t.py": self.OUTER % ""}).check("suites")
        self.assertTrue(found.ok, found.findings)
        self.assertIn("1 suite, 1 tests, 0 skipped", found.summary)
        self.assertFalse(any("an inner reason" in n for n in found.notes), found.notes)

    def test_an_outer_skip_is_listed_with_its_reason_beside_a_tests_own_runner(self):
        outer = "\n    @unittest.skip('an outer reason')\n    def test_skipped(self):\n        pass\n"
        found = self.built(files={"tests/test_t.py": self.OUTER % outer}).check("suites")
        self.assertTrue(found.ok, found.findings)
        self.assertIn("1 suite, 2 tests, 1 skipped", found.summary)
        self.assertTrue(any("skipped 1: an outer reason" in n for n in found.notes), found.notes)
        self.assertFalse(any("an inner reason" in n for n in found.notes), found.notes)

    def test_a_suite_that_writes_no_result_fails_and_says_so(self):
        found = self.built("    def test_quit(self):\n        os._exit(0)\n").check("suites")
        self.assertFalse(found.ok)
        self.assertTrue(any("result" in f.text for f in found.findings), found.findings)

    def test_the_tests_run_inside_the_snapshot_where_the_left_out_files_are_absent(self):
        body = "    def test_absent(self):\n        self.assertFalse(os.path.exists('bench/rows.jsonl'))\n" \
               "        self.assertTrue(os.path.exists('README.md'))\n"
        found = self.built(body, files={"bench/rows.jsonl": "{}\n"}, rows="bench/rows.jsonl\tquotes a dataset\n").check("suites")
        self.assertTrue(found.ok, found.findings)

    def tamper_body(self, snapshot, *extra):
        return ("    def test_tamper(self):\n        import subprocess\n        snap = %r\n"
                "        os.makedirs(os.path.join(snap, 'bench'), exist_ok=True)\n"
                "        with open(os.path.join(snap, 'bench', 'rows.jsonl'), 'w') as fh:\n"
                "            fh.write('Acme' + 'corp ' + %r + %r + '\\n')\n"
                "        ident = ['-c', 'user.name=x', '-c', 'user.email=x@example.invalid']\n"
                "        for args in (%s):\n"
                "            subprocess.run(['git', '-C', snap] + ident + args, check=True)\n"
                % (snapshot, CREDENTIAL[:6], CREDENTIAL[6:], ", ".join(repr(a) for a in extra)))

    def test_a_test_that_restores_a_listed_file_and_commits_works_on_a_copy_and_leaves_the_snapshot_alone(self):
        marker = self.tmp / "cwd.txt"
        body = ("    def test_where(self):\n        open(%r, 'w').write(os.getcwd())\n"
                "        open('bench/rows.jsonl', 'w').write('Acme' + 'corp ' + %r + %r + '\\n')\n"
                "        import subprocess\n"
                "        ident = ['-c', 'user.name=x', '-c', 'user.email=x@example.invalid']\n"
                "        subprocess.run(['git'] + ident + ['add', '-A'], check=True)\n"
                "        subprocess.run(['git'] + ident + ['commit', '-q', '-m', 'restore'], check=True)\n") % (str(marker), CREDENTIAL[:6], CREDENTIAL[6:])
        release = self.built(body, files={"bench/rows.jsonl": "{}\n"}, rows="bench/rows.jsonl\tquotes a dataset\n")
        self.assertTrue(release.check("suites").ok, release.check("suites").findings)
        self.assertNotEqual(marker.read_text(), str(release.repo))
        self.assertEqual(git(release.repo, "rev-list", "--all", "--count"), "1")
        self.assertFalse((release.repo / "bench" / "rows.jsonl").exists())
        self.assertTrue(release.ok, release.report())

    def test_a_test_that_changes_the_snapshot_itself_fails_the_suites_and_every_check_run_after_them(self):
        snapshot = str(self.out / "repo")
        body = self.tamper_body(snapshot, ["add", "-A"], ["commit", "-q", "-m", "restore"])
        release = self.built(body, files={"bench/rows.jsonl": "{}\n"}, rows="bench/rows.jsonl\tquotes a dataset\n")
        self.assertFalse(release.ok)
        suites = release.check("suites")
        self.assertFalse(suites.ok)
        self.assertTrue(any("snapshot changed" in f.text and "refs" in f.text and "objects" in f.text for f in suites.findings), suites.findings)
        for name in ("history", "credential", "patterns", "dataset"):
            self.assertFalse(release.check(name).ok, name)

    def test_any_change_to_the_snapshot_during_the_suites_is_named(self):
        snapshot = str(self.out / "repo")
        body = ("    def test_tamper(self):\n        import subprocess\n        snap = %r\n"
                "        subprocess.run(['git', '-C', snap, 'tag', 'extra-tag'], check=True)\n" % snapshot)
        found = self.built(body).check("suites")
        self.assertFalse(found.ok)
        self.assertTrue(any("snapshot changed" in f.text and "refs" in f.text for f in found.findings), found.findings)
        self.out = self.tmp / "out2"
        body = ("    def test_tamper(self):\n        open(%r, 'a').write('edited\\n')\n" % str(self.out / "repo" / "README.md"))
        found = self.built(body).check("suites")
        self.assertFalse(found.ok)
        self.assertTrue(any("snapshot changed" in f.text and "repo/README.md" in f.text for f in found.findings), found.findings)

    def tampered(self, snippet):
        snap = str(self.out / "repo")
        body = "    def test_tamper(self):\n        import os, subprocess\n        snap = %r\n%s" % (snap, textwrap.indent(textwrap.dedent(snippet), "        "))
        return self.built(body).check("suites")

    def assert_changed(self, found, what):
        self.assertFalse(found.ok, found.findings)
        self.assertTrue(any("snapshot changed" in f.text and what in f.text for f in found.findings), found.findings)

    def test_git_update_index_assume_unchanged_cannot_hide_an_edit_to_a_snapshot_file(self):
        found = self.tampered("""
            subprocess.run(['git', '-C', snap, 'update-index', '--assume-unchanged', 'README.md'], check=True)
            open(os.path.join(snap, 'README.md'), 'a').write('zq-4417' + '-kettle\\n')
            """)
        self.assert_changed(found, "repo/README.md")

    def test_a_new_loose_object_in_the_snapshot_is_a_change(self):
        found = self.tampered("""
            subprocess.run(['git', '-C', snap, 'hash-object', '-w', '--stdin'], input=b'stowaway', check=True)
            """)
        self.assert_changed(found, "objects")

    def test_a_ref_written_by_hand_is_a_change(self):
        found = self.tampered("""
            head = open(os.path.join(snap, '.git', 'refs', 'heads', 'main')).read()
            open(os.path.join(snap, '.git', 'refs', 'heads', 'extra'), 'w').write(head)
            """)
        self.assert_changed(found, "refs")

    def test_a_mode_change_and_a_new_empty_folder_are_changes(self):
        self.assert_changed(self.tampered("os.chmod(os.path.join(snap, 'README.md'), 0o755)"), "repo/README.md")
        self.out = self.tmp / "out2"
        self.assert_changed(self.tampered("os.makedirs(os.path.join(snap, 'empty', 'folder'))"), "repo/empty/folder")

    def test_a_folder_link_pointed_at_another_folder_is_a_change(self):
        os.symlink("target", str(self.src / "link"))
        snap = str(self.out / "repo")
        body = ("    def test_tamper(self):\n        link = os.path.join(%r, 'link')\n"
                "        os.remove(link)\n        os.symlink('tests', link)\n" % snap)
        found = self.built(body, files={"target/a.txt": "fine\n"}).check("suites")
        self.assert_changed(found, "repo/link")

    def test_a_new_hook_a_mode_change_on_the_notes_or_assume_unchanged_is_a_change(self):
        self.assert_changed(self.tampered("""
            os.makedirs(os.path.join(snap, '.git', 'hooks'), exist_ok=True)
            open(os.path.join(snap, '.git', 'hooks', 'pre-commit'), 'w').write('#!/bin/sh\\n')
            """), "repo/.git/hooks/pre-commit")
        self.out = self.tmp / "out2"
        self.assert_changed(self.tampered("os.chmod(os.path.join(os.path.dirname(snap), 'RELEASE_NOTES.md'), 0o755)"), "RELEASE_NOTES.md")
        self.out = self.tmp / "out3"
        self.assert_changed(self.tampered("subprocess.run(['git', '-C', snap, 'update-index', '--assume-unchanged', 'README.md'], check=True)"), "repo/.git/index")

    def test_a_link_out_of_the_tree_stops_the_installer_and_the_suites_before_they_run(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        os.symlink(str(outside), str(self.src / "cache-link"))
        installer = self.tmp / "marker.sh"
        installer.write_text("#!/bin/sh\ntouch %s\n" % (outside / "installed"))
        body = "    def test_write(self):\n        open(os.path.join('cache-link', 'marker.txt'), 'w').write('x')\n"
        release = self.built(body, installer=installer)
        self.assertFalse(release.ok)
        self.assertEqual([f.where for f in release.check("links").findings], ["cache-link"])
        for name in ("install", "suites"):
            found = release.check(name)
            self.assertEqual(found.status, "SKIP", found.findings)
            self.assertIn("not run", found.summary)
        self.assertEqual(os.listdir(str(outside)), [])

    def test_a_suite_that_edits_the_notes_fails_the_credential_check_on_what_is_on_disk(self):
        notes = str(self.out / "RELEASE_NOTES.md")
        body = ("    def test_edit(self):\n        open(%r, 'a').write(%r + %r + '\\n')\n" % (notes, CREDENTIAL[:6], CREDENTIAL[6:]))
        release = self.built(body)
        self.assertFalse(release.check("credential").ok, release.report())
        self.assertIn("RELEASE_NOTES.md", [f.where for f in release.check("credential").findings])
        self.assertFalse(release.check("suites").ok)
        self.assertFalse(release.ok)

    def test_every_check_runs_after_the_installer_and_the_suites(self):
        calls = []

        def around(name, original):
            def wrapper(*args, **kw):
                calls.append(name + " start")
                try:
                    return original(*args, **kw)
                finally:
                    calls.append(name + " end")
            return wrapper

        with mock.patch.object(rs, "run_suites", around("suites", rs.run_suites)), \
                mock.patch.object(rs, "check_history", around("history", rs.check_history)), \
                mock.patch.object(rs, "check_credential", around("credential", rs.check_credential)), \
                mock.patch.object(rs, "check_patterns", around("patterns", rs.check_patterns)), \
                mock.patch.object(rs, "check_dataset", around("dataset", rs.check_dataset)), \
                mock.patch.object(rs, "check_archive", around("archive", rs.check_archive)):
            self.built()
        done = calls.index("suites end")
        for name in ("history", "credential", "patterns", "dataset", "archive"):
            self.assertGreater(calls.index(name + " start"), done, calls)

    def test_the_tests_run_with_a_home_that_is_not_the_real_one(self):
        body = "    def test_home(self):\n        self.assertNotEqual(os.environ['HOME'], %r)\n" % os.path.expanduser("~")
        self.assertTrue(self.built(body).check("suites").ok)

    def test_the_tests_still_find_the_packages_installed_for_the_user(self):
        import site
        body = "    def test_site(self):\n        import site\n        self.assertEqual(site.getuserbase(), %r)\n" % site.getuserbase()
        found = self.built(body).check("suites")
        self.assertTrue(found.ok, found.findings)

    def test_a_suite_that_leaves_files_in_the_snapshot_fails(self):
        body = "    def test_mess(self):\n        open('leftover.txt', 'w').close()\n"
        found = self.built(body).check("suites")
        self.assertFalse(found.ok)
        self.assertTrue(any("leftover.txt" in f.text for f in found.findings), found.findings)

    def test_an_interpreter_that_is_missing_fails_that_suite_only(self):
        self.commit(self.base_files(**{"tests/test_t.py": self.TESTS % "", "tests/__init__.py": ""}))
        both = ((("9.9", ("no-such-python-9.9",)), (CURRENT, (sys.executable,))))
        release = self.build(suites=lambda repo: rs.default_suites(repo, interpreters=both))
        found = release.check("suites")
        self.assertFalse(found.ok)
        self.assertTrue(any("no-such-python-9.9" in f.text and "PATH" in f.text for f in found.findings), found.findings)
        self.assertIn("python%s tests: 1 tests, 0 skipped, passed" % CURRENT, found.notes)

    def test_bench_tests_run_one_folder_at_a_time(self):
        files = {"bench/test_a.py": self.TESTS % "", "bench/sub/test_b.py": self.TESTS % "", "bench/data.py": "x = 1\n"}
        found = self.built(files=files).check("suites")
        self.assertTrue(found.ok, found.findings)
        labels = [n.split(":")[0] for n in found.notes if n.startswith("bench")]
        self.assertEqual(labels, ["bench", "bench/sub"])

    def test_a_failing_bench_test_fails_the_release(self):
        files = {"bench/test_a.py": self.TESTS % "    def test_bad(self):\n        self.fail('x')\n"}
        self.assertFalse(self.built(files=files).check("suites").ok)

    def test_the_default_suites_name_the_main_suite_for_each_interpreter_and_python3_for_bench(self):
        self.commit(self.base_files(**{"bench/test_a.py": "x = 1\n", "tests/test_t.py": "x = 1\n"}))
        release = self.build()
        found = rs.default_suites(release.repo)
        self.assertEqual([(s.label, s.want) for s in found],
                         [("python3.12 tests", ("3.12", ("python3.12", "python3"))),
                          ("python3.9 tests", ("3.9", ("python3.9", "python3"))),
                          ("bench", ("3.12", ("python3.12", "python3")))])
        self.assertEqual(found[0].argv[1:], ["-m", "unittest", "discover", "-v", "-s", "tests", "-t", "."])

    def fake_pythons(self, **claims):
        folder = self.tmp / "fake-bin"
        folder.mkdir(exist_ok=True)
        for tool in ("git", "grep", "sh"):
            if not (folder / tool).exists():
                (folder / tool).symlink_to(shutil.which(tool))
        for name, version in claims.items():
            script = folder / name
            script.write_text('#!/bin/sh\nif [ "$1" = "-c" ]; then echo %s; exit 0; fi\nexec %s "$@"\n' % (version, sys.executable))
            script.chmod(0o755)
        return str(folder)

    def run_default(self, path):
        self.commit(self.base_files(**{"tests/test_t.py": self.TESTS % "", "tests/__init__.py": ""}))
        with mock.patch.dict(os.environ, {"PATH": path}):
            return self.build(suites=rs.default_suites).check("suites")

    def test_an_interpreter_of_another_version_than_the_slot_needs_fails_and_says_both(self):
        self.commit(self.base_files(**{"tests/test_t.py": self.TESTS % "", "tests/__init__.py": ""}))
        wrong = (("2.7", (sys.executable,)),)
        found = self.build(suites=lambda repo: rs.default_suites(repo, interpreters=wrong)).check("suites")
        self.assertFalse(found.ok)
        self.assertTrue(any("is %s, not 2.7" % CURRENT in f.text for f in found.findings), found.findings)

    def test_a_python3_that_is_3_9_cannot_stand_in_for_3_12(self):
        found = self.run_default(self.fake_pythons(python3="3.9", **{"python3.12": "3.9"}))
        self.assertFalse(found.ok)
        self.assertTrue(any("3.12" in f.text and "3.9" in f.text for f in found.findings), found.findings)
        self.assertIn("python3.9 tests: 1 tests, 0 skipped, passed", found.notes)

    def test_both_interpreters_missing_fails_and_names_both(self):
        found = self.run_default(self.fake_pythons())
        self.assertFalse(found.ok)
        text = "\n".join(f.text for f in found.findings)
        self.assertIn("Python 3.12", text)
        self.assertIn("Python 3.9", text)

    def test_one_interpreter_of_each_version_passes(self):
        found = self.run_default(self.fake_pythons(**{"python3.12": "3.12", "python3.9": "3.9"}))
        self.assertTrue(found.ok, found.findings)
        self.assertIn("2 suites", found.summary)

    def test_the_slot_takes_the_first_candidate_that_has_the_version(self):
        found = self.run_default(self.fake_pythons(python3="3.12", **{"python3.9": "3.9"}))
        self.assertTrue(found.ok, found.findings)

    def test_no_suites_means_the_check_is_skipped_not_passed(self):
        found = self.build(self.commit(self.base_files())).check("suites")
        self.assertEqual(found.status, "SKIP")
        self.assertTrue(found.ok)


@unittest.skipUnless(shutil.which("curl") and shutil.which("tar") and shutil.which("openssl"),
                     "the installer needs curl and tar, and the https server needs openssl for a throwaway certificate")
class InstallTest(SourceCase):
    def setUp(self):
        super().setUp()
        write(self.src, {ROWS: ""})
        for name in ("kb", "install.sh", "pyproject.toml", "README.md", "LICENSE"):
            origin = REPO / name
            if origin.is_dir():
                shutil.copytree(str(origin), str(self.src / name), ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy(str(origin), str(self.src / name))
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "the real package")

    def trusting(self, cafile):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                           urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(cafile))))

    def test_the_server_redirects_latest_and_serves_the_archive_for_the_tag_only(self):
        release = self.build()
        with rs.serve(self.out, release.tag) as served:
            base, opener = served.url, self.trusting(served.cafile)
            self.assertRegex(base, r"^https://127\.0\.0\.1:\d+$")
            opened = opener.open(base + "/releases/latest")
            self.assertEqual(opened.geturl(), base + "/releases/tag/" + release.tag)
            body = opener.open(base + "/archive/refs/tags/%s.tar.gz" % release.tag).read()
            self.assertEqual(body, release.archive.read_bytes())
            for path in ("/archive/refs/tags/v0.0.1.tar.gz", "/releases/tag/v0.0.1", "/RELEASE_NOTES.md", "/repo/README.md"):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    opener.open(base + path)
                caught.exception.close()
                self.assertEqual(caught.exception.code, 404, path)

    def test_the_server_speaks_https_only_with_a_certificate_that_only_its_own_file_vouches_for(self):
        release = self.build()
        with rs.serve(self.out, release.tag) as served:
            plain = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with self.assertRaises(urllib.error.URLError):
                plain.open(served.url + "/releases/latest")
            with self.assertRaises(Exception):
                plain.open(served.url.replace("https://", "http://") + "/releases/latest")

    def test_the_sites_installer_installs_kb_from_the_served_release(self):
        release = self.build()
        result = rs.run_install_check(release, INSTALLER)
        self.assertTrue(result.ok, result.summary + "\n" + "\n".join(f.text for f in result.findings))
        self.assertIn("kb --help", result.summary)

    def test_the_install_check_fails_when_the_installer_fails(self):
        release = self.build()
        broken = self.tmp / "broken.sh"
        broken.write_text("#!/bin/sh\necho no >&2\nexit 3\n")
        result = rs.run_install_check(release, broken)
        self.assertFalse(result.ok)
        self.assertIn("no", "\n".join(f.text for f in result.findings))

    def test_the_install_never_touches_the_real_home(self):
        release = self.build()
        before = Path.home().joinpath(".local", "bin", "kb")
        existed = before.exists()
        rs.run_install_check(release, INSTALLER)
        self.assertEqual(before.exists(), existed)

    def fake_installer(self, kb_body):
        script = self.tmp / "fake_installer.sh"
        script.write_text("#!/bin/sh\nmkdir -p \"$HOME/.local/bin\"\ncat > \"$HOME/.local/bin/kb\" <<'KB'\n%s\nKB\nchmod +x \"$HOME/.local/bin/kb\"\n" % kb_body)
        return script

    def scripted(self, steps, kb_body):
        script = self.tmp / "scripted.sh"
        script.write_text('#!/bin/sh\nset -e\nrepo="$SUPERHISTORY_REPO"\n%s\nmkdir -p "$HOME/.local/bin"\n'
                          'cat > "$HOME/.local/bin/kb" <<\'KB\'\n%s\nKB\nchmod +x "$HOME/.local/bin/kb"\n' % (steps, kb_body))
        return script

    def test_an_installer_that_never_downloads_the_whole_archive_fails(self):
        release = self.build()
        steps = ('curl -fsS -o /dev/null "$repo/releases/latest"\n'
                 'curl -fsSI -o /dev/null "$repo/archive/refs/tags/%s.tar.gz"' % release.tag)
        result = rs.run_install_check(release, self.scripted(steps, "#!/bin/sh\necho usage: kb"), timeout=60)
        self.assertFalse(result.ok)
        self.assertTrue(any("whole archive" in f.text for f in result.findings), result.findings)

    def test_a_kb_that_prints_another_version_than_the_snapshots_code_fails(self):
        release = self.build()
        steps = ('curl -fsS -o /dev/null "$repo/releases/latest"\n'
                 'curl -fsS -o "$TMPDIR/a.tar.gz" "$repo/archive/refs/tags/%s.tar.gz"' % release.tag)
        kb = '#!/bin/sh\ncase "$1" in --version) echo "Varve 9.9.9" ;; *) echo "usage: kb" ;; esac'
        result = rs.run_install_check(release, self.scripted(steps, kb), timeout=60)
        self.assertFalse(result.ok)
        self.assertEqual([f.where for f in result.findings], ["kb --version"], result.findings)

    def test_a_kb_that_hangs_fails_the_check_instead_of_raising(self):
        release = self.build()
        started = time.time()
        result = rs.run_install_check(release, self.fake_installer("#!/bin/sh\nsleep 30"), timeout=2)
        self.assertLess(time.time() - started, 25)
        self.assertFalse(result.ok)
        self.assertTrue(any(f.where == "kb --help" and "ran past 2 seconds" in f.text for f in result.findings), result.findings)

    def test_a_kb_that_cannot_run_fails_the_check_with_the_reason(self):
        release = self.build()
        result = rs.run_install_check(release, self.fake_installer("not a program"), timeout=5)
        self.assertFalse(result.ok)
        self.assertTrue(any(f.where == "kb --help" for f in result.findings), result.findings)

    def test_a_kb_that_is_not_executable_fails_the_check_with_the_reason(self):
        release = self.build()
        script = self.tmp / "plain.sh"
        script.write_text("#!/bin/sh\nmkdir -p \"$HOME/.local/bin\"\necho '#!/bin/sh' > \"$HOME/.local/bin/kb\"\n")
        result = rs.run_install_check(release, script, timeout=5)
        self.assertFalse(result.ok)
        self.assertTrue(any("Permission" in f.text or "not executable" in f.text for f in result.findings), result.findings)

    def test_a_certificate_that_cannot_be_made_fails_the_check_with_the_reason(self):
        release = self.build()
        with mock.patch.object(rs, "make_certificate", side_effect=rs.ReleaseError("no openssl here")):
            result = rs.run_install_check(release, INSTALLER)
        self.assertFalse(result.ok)
        self.assertIn("no openssl here", "\n".join(f.text for f in result.findings))

    def test_an_installer_that_cannot_start_fails_the_check_with_the_reason(self):
        release = self.build()
        with mock.patch.object(rs.subprocess, "Popen", side_effect=OSError("exec failed on purpose")):
            result = rs.run_install_check(release, INSTALLER)
        self.assertFalse(result.ok)
        self.assertIn("exec failed on purpose", "\n".join(f.text for f in result.findings))

    def test_a_group_that_empties_on_its_own_as_it_is_checked_is_no_leftover(self):
        calls = []

        def killpg(pgid, sig):
            calls.append(sig)
            if sig:
                raise ProcessLookupError(3, "No such process")

        with mock.patch.object(rs.os, "killpg", side_effect=killpg):
            self.assertEqual(rs.run_group(["true"], timeout=10), (0, b""))
        self.assertEqual(calls[0], 0)

    def test_a_process_list_that_fails_fails_the_check_and_still_stops_the_group(self):
        marker = self.tmp / "left.pid"
        real_run = rs.subprocess.run

        def failing_ps(argv, *args, **kw):
            if argv and argv[0] == "ps":
                return subprocess.CompletedProcess(argv, 1, b"", b"ps: process list unavailable")
            return real_run(argv, *args, **kw)

        with mock.patch.object(rs.subprocess, "run", side_effect=failing_ps):
            with self.assertRaisesRegex(rs.ReleaseError, "could not list"):
                rs.run_group(["sh", "-c", "sleep 60 >/dev/null 2>&1 & echo $! > %s" % marker], timeout=10)
        pid = int(marker.read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail("the process left in the group was not stopped")

    def test_a_command_that_runs_past_its_time_loses_its_whole_process_group(self):
        marker = self.tmp / "grandchild.pid"
        started = time.time()
        with self.assertRaises(subprocess.TimeoutExpired):
            rs.run_group(["sh", "-c", "sleep 60 & echo $! > %s; wait" % marker], timeout=1)
        self.assertLess(time.time() - started, 20)
        pid = int(marker.read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            self.fail("the command's own child outlived the timeout")

    def wrapped_installer(self, before):
        script = self.tmp / "wrapped.sh"
        script.write_text("#!/bin/sh\n%s\nexec sh %s \"$@\"\n" % (before, INSTALLER))
        return script

    def test_a_report_planted_by_the_installer_is_a_change_and_is_never_written_through(self):
        victim = self.tmp / "victim.txt"
        victim.write_text("keep me\n")
        for target in (self.out / "RELEASE_NOTES.md", victim):
            shutil.rmtree(str(self.out), ignore_errors=True)
            planted = self.wrapped_installer("ln -s '%s' '%s'" % (target, self.out / "CHECKS.txt"))
            code, text = self.run_main("--installer", str(planted))
            self.assertNotEqual(code, 0, text)
            self.assertIn("CHECKS.txt", text)
            self.assertEqual(victim.read_text(), "keep me\n")
            self.assertTrue((self.out / "RELEASE_NOTES.md").read_text().startswith("# "))
            self.assertTrue(os.path.islink(str(self.out / "CHECKS.txt")))

    def test_a_process_the_installer_leaves_in_its_group_is_stopped_and_fails_the_check(self):
        marker = self.tmp / "left.pid"
        planted = self.wrapped_installer("( sleep 60 ) > /dev/null 2>&1 < /dev/null &\necho $! > '%s'" % marker)
        release = self.build(installer=planted)
        pid = int(marker.read_text())

        def stop():
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        self.addCleanup(stop)
        found = release.check("install")
        self.assertFalse(found.ok)
        self.assertIn(str(pid), "\n".join(f.text for f in found.findings))
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail("the process the installer left is still running")

    def test_an_installer_that_adds_the_credential_to_the_notes_fails_the_credential_and_install_checks(self):
        notes = self.out / "RELEASE_NOTES.md"
        wrapped = self.wrapped_installer("printf '%%s\\n' '%s' >> '%s'" % (CREDENTIAL, notes))
        release = self.build(installer=wrapped)
        self.assertFalse(release.check("credential").ok, release.report())
        self.assertIn("RELEASE_NOTES.md", [f.where for f in release.check("credential").findings])
        found = release.check("install")
        self.assertFalse(found.ok)
        self.assertTrue(any("snapshot changed" in f.text and "RELEASE_NOTES.md" in f.text for f in found.findings), found.findings)
        self.assertFalse(release.ok)

    def test_an_installer_that_replaces_the_archive_fails_the_archive_and_install_checks(self):
        wrapped = self.wrapped_installer("for f in '%s'/*.tar.gz; do printf junk >> \"$f\"; done" % self.out)
        release = self.build(installer=wrapped)
        self.assertFalse(release.check("archive").ok)
        self.assertFalse(release.check("install").ok)

    def test_every_check_runs_after_the_installer(self):
        calls = []

        def around(name, original):
            def wrapper(*args, **kw):
                calls.append(name)
                return original(*args, **kw)
            return wrapper

        with mock.patch.object(rs, "install_check", around("install", rs.install_check)), \
                mock.patch.object(rs, "check_credential", around("credential", rs.check_credential)), \
                mock.patch.object(rs, "check_patterns", around("patterns", rs.check_patterns)), \
                mock.patch.object(rs, "check_archive", around("archive", rs.check_archive)):
            self.build(installer=INSTALLER)
        self.assertEqual(calls[0], "install", calls)

    def test_the_command_runs_the_install_check_when_given_an_installer(self):
        code, text = self.run_main("--installer", str(INSTALLER))
        self.assertEqual(code, 0, text)
        self.assertRegex(text, r"(?m)^PASS install")


if __name__ == "__main__":
    unittest.main()
