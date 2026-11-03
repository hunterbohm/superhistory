import contextlib
import copy
import io
import os
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tools import release_snapshot as rs

REPO = Path(__file__).resolve().parent.parent
INSTALLER = Path(__file__).resolve().parent / "fixtures" / "release" / "site_install.sh"
CURRENT = "%d.%d" % sys.version_info[:2]
EXCEPTIONS = "tools/release_exceptions.tsv"
ROWS = "bench/DATASET_ROWS.txt"
CREDENTIAL = "zq-4417-kettle"
AUTHOR = "Pat Example <pat@example.invalid>"
PATTERNS = "\\bacmecorp\\b\n(^|[^-a-z0-9])boxroom\\b\n"
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

    GW = "cli" + "-gateway"
    PORT = "83" + "17"
    PROXY = "CLIPr" + "oxyAPI"
    GATEWAY_SPELLINGS = [GW, "CLI" + "-Gateway", "cli" + "_gateway", "CLI" + "GATEWAY", PORT, PROXY, PROXY.lower(),
                         "~/.config/" + GW + "/client.key", "$HOME/.config/" + GW, "${HOME}/.config/" + GW,
                         "/home/someone/.config/" + GW, "/Users/someone/.config/" + GW,
                         "$XDG_CONFIG_HOME/" + GW + "/client.key", "~/.config//cli" + "_gateway/client.key"]

    def test_the_releases_own_forbidden_strings_hit_whatever_the_patterns_file_holds(self):
        text = "".join("see %s here\n" % spelling for spelling in self.GATEWAY_SPELLINGS)
        found = self.build(self.commit(self.base_files(**{"docs/a.md": text}))).check("patterns")
        self.assertFalse(found.ok)
        self.assertEqual([f.where for f in found.findings], ["docs/a.md:%d" % n for n in range(1, len(self.GATEWAY_SPELLINGS) + 1)])

    def test_words_near_the_forbidden_ones_do_not_hit(self):
        text = "an api gateway\nthe cli tool\nport 8318 and 18316\nclient.key alone\n"
        found = self.build(self.commit(self.base_files(**{"docs/a.md": text}))).check("patterns")
        self.assertTrue(found.ok, found.findings)

    def test_the_forbidden_strings_hit_in_file_names_the_commit_message_and_the_notes(self):
        release = self.build(self.commit(self.base_files(**{"docs/%s.md" % self.GW: "fine\n"})))
        self.assertEqual([f.where for f in release.check("patterns").findings], ["docs/%s.md" % self.GW])
        found = rs.check_patterns(release.repo, self.patterns, extra={"commit message": "uses " + self.PORT, "RELEASE_NOTES.md": self.PROXY + "\n"})
        self.assertEqual(sorted(f.where for f in found.findings), ["RELEASE_NOTES.md:1", "commit message:1", "docs/%s.md" % self.GW])

    def test_the_forbidden_strings_can_still_be_excepted_by_an_exact_entry(self):
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
        self.assertEqual([f.where for f in found.findings], ["hidden.txt:2"])

    def test_the_committed_blob_is_scanned_not_the_checkout(self):
        release = self.build(self.commit(self.base_files(**{"docs/a.md": "see Acmecorp\n", "docs/b.md": "fine\n"})))
        os.remove(str(release.repo / "docs" / "a.md"))
        (release.repo / "docs" / "b.md").write_text("see Acmecorp now\n")
        found = rs.check_patterns(release.repo, self.patterns)
        self.assertEqual([f.where for f in found.findings], ["docs/a.md:1"])

    def test_an_archive_member_that_differs_from_the_tree_is_scanned_by_name_and_contents(self):
        release = self.build(self.commit(self.base_files(**{"docs/a.md": "fine\n"})))
        prefix = "widgetly-1.2.3/"
        path = self.tmp / "tampered.tar.gz"
        with tarfile.open(str(release.archive)) as tar:
            members = [(m, tar.extractfile(m).read() if m.isfile() else None) for m in tar.getmembers()]
        with tarfile.open(str(path), "w:gz") as out:
            for info, data in members:
                if info.name == prefix + "docs/a.md":
                    data = b"fine\nsee Acmecorp\n"
                    info.size = len(data)
                out.addfile(info, io.BytesIO(data) if data is not None else None)
            extra = tarfile.TarInfo(prefix + "boxroom.txt")
            extra.size = 1
            out.addfile(extra, io.BytesIO(b"x"))
        found = rs.check_patterns(release.repo, self.patterns, archive=path, prefix=prefix)
        self.assertEqual(sorted(f.where for f in found.findings), ["boxroom.txt", "docs/a.md:2"])

    def test_the_same_hit_in_the_tree_and_in_the_archive_is_reported_once(self):
        release = self.build(self.commit(self.base_files(**{"docs/a.md": "see Acmecorp\n"})))
        found = rs.check_patterns(release.repo, self.patterns, archive=release.archive, prefix="widgetly-1.2.3/")
        self.assertEqual([f.where for f in found.findings], ["docs/a.md:1"])

    def test_a_symlink_target_is_checked(self):
        write(self.src, self.base_files())
        os.symlink("acmecorp.txt", self.src / "link")
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "link")
        found = self.build().check("patterns")
        self.assertFalse(found.ok)

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
                                 [("a.md", "Acmecorp", "a made-up firm"), ("a.md", "boxroom", "a made-up room")])
        self.assertTrue(found.ok, found.findings)

    def test_a_file_name_cannot_be_excepted(self):
        found = self.patterns_of({"acmecorp-notes.txt": "fine\n"},
                                 [("acmecorp-notes.txt", "acmecorp-notes.txt", "no")])
        self.assertEqual([f.where for f in found.findings], ["acmecorp-notes.txt"])

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
        self.assertTrue(any("snapshot changed" in f.text and "working tree" in f.text for f in found.findings), found.findings)

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

    def test_the_command_runs_the_install_check_when_given_an_installer(self):
        code, text = self.run_main("--installer", str(INSTALLER))
        self.assertEqual(code, 0, text)
        self.assertRegex(text, r"(?m)^PASS install")


if __name__ == "__main__":
    unittest.main()
