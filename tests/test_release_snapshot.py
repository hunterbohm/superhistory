import contextlib
import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tools import release_snapshot as rs

REPO = Path(__file__).resolve().parent.parent
INSTALLER = Path(__file__).resolve().parent / "fixtures" / "release" / "site_install.sh"
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


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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
                 "pyproject.toml": "[project]\nname = \"widgetly\"\n", "install.sh": "#!/bin/sh\n"}
        files.update(extra)
        return files

    def with_history(self):
        self.commit(self.base_files(**{"notes.txt": "login is %s\n" % CREDENTIAL}), "first")
        return self.commit({"notes.txt": "nothing here\n"}, "second")

    def run_main(self, *extra, rev="HEAD"):
        argv = [rev, str(self.out), "--source", str(self.src), "--author", AUTHOR, "--patterns", str(self.patterns),
                "--credential", CREDENTIAL, "--repo-name", "widgetly", *extra]
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

    def test_a_link_to_an_allowed_repo_is_not_a_hit(self):
        files = {"README.md": README + "\nSee https://github.com/acmecorp/widgetly for more.\n",
                 "pyproject.toml": 'Homepage = "https://github.com/AcmeCorp/widgetly"\nIssues = "git+https://github.com/acmecorp/widgetly.git"\n'}
        release = self.build(self.commit(self.base_files(**files)), allow_links=["acmecorp/widgetly"])
        found = release.check("patterns")
        self.assertTrue(found.ok, found.findings)
        self.assertEqual(len(found.allowed), 3)

    def test_other_links_and_other_text_on_a_link_line_still_hit(self):
        files = {"a.txt": "https://github.com/acmecorp/other\n",
                 "b.txt": "https://github.com/acmecorp/widgetly-fork\n",
                 "c.txt": "https://github.com/acmecorp/widgetly and boxroom\n",
                 "d.txt": "https://github.com/acmecorp/widgetly\nhttps://example.invalid/acmecorp\n"}
        release = self.build(self.commit(self.base_files(**files)), allow_links=["acmecorp/widgetly"])
        found = release.check("patterns")
        self.assertEqual(sorted(f.where for f in found.findings), ["a.txt:1", "b.txt:1", "c.txt:1", "d.txt:2"])

    def test_an_allowed_file_is_listed_apart_and_does_not_fail(self):
        head = self.commit(self.base_files(LICENSE="Copyright Acmecorp\n"))
        found = self.build(head, allow_files=["LICENSE"]).check("patterns")
        self.assertTrue(found.ok)
        self.assertEqual([f.where for f in found.allowed], ["LICENSE:1"])

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


class CommandTest(SourceCase):
    def test_a_clean_snapshot_exits_zero_and_reports_each_check(self):
        self.commit(self.base_files())
        code, text = self.run_main()
        self.assertEqual(code, 0, text)
        self.assertIn("tag: v1.2.3", text)
        for name in ("history", "credential", "patterns", "archive"):
            self.assertRegex(text, r"(?m)^PASS %s" % name)
        self.assertRegex(text, r"(?m)^SKIP install")
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


@unittest.skipUnless(shutil.which("curl") and shutil.which("tar"), "the installer needs curl and tar")
class InstallTest(SourceCase):
    def setUp(self):
        super().setUp()
        for name in ("kb", "install.sh", "pyproject.toml", "README.md", "LICENSE"):
            origin = REPO / name
            if origin.is_dir():
                shutil.copytree(str(origin), str(self.src / name), ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy(str(origin), str(self.src / name))
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "the real package")

    def test_the_server_redirects_latest_and_serves_the_archive_for_the_tag_only(self):
        release = self.build()
        with rs.serve(self.out, release.tag) as base:
            opened = OPENER.open(base + "/releases/latest")
            self.assertEqual(opened.geturl(), base + "/releases/tag/" + release.tag)
            body = OPENER.open(base + "/archive/refs/tags/%s.tar.gz" % release.tag).read()
            self.assertEqual(body, release.archive.read_bytes())
            for path in ("/archive/refs/tags/v0.0.1.tar.gz", "/releases/tag/v0.0.1", "/RELEASE_NOTES.md", "/repo/README.md"):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    OPENER.open(base + path)
                caught.exception.close()
                self.assertEqual(caught.exception.code, 404, path)

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

    def test_the_command_runs_the_install_check_when_given_an_installer(self):
        code, text = self.run_main("--installer", str(INSTALLER))
        self.assertEqual(code, 0, text)
        self.assertRegex(text, r"(?m)^PASS install")


if __name__ == "__main__":
    unittest.main()
