import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = ["SHA256SUMS", "install.sh", "superhistory.tar.gz"]
LATEST = "https://github.com/hunterbohm/superhistory/releases/latest/download/superhistory.tar.gz"
TAGGED = "https://github.com/hunterbohm/superhistory/releases/download/v0.1.0/superhistory.tar.gz"
# What install.sh and the installed command run, found once; a test's PATH holds these and its own fakes, never a real
# gh or curl, so a machine's own gh (a CI runner has one) can't answer for the fake.
SYSTEM_TOOLS = ["sh", "mktemp", "rm", "mkdir", "mv", "cp", "cat", "ls", "find", "head", "tar", "gzip", "chmod",
                "dirname", "basename", "uname", "sed", "tr", "grep", "env"]
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.repo = os.path.join(self.tmp, "repo")
        for path in ("install.sh", "pyproject.toml", "README.md", "LICENSE", "tools/release.sh",
                     "tools/release_scan.py"):
            self.copy(path)
        shutil.copytree(os.path.join(REPO, "kb"), os.path.join(self.repo, "kb"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.write("bench/results.txt", "numbers\n")
        self.git("init", "-q")
        self.commit()
        self.out = os.path.join(self.tmp, "assets")
        self.bin = os.path.join(self.tmp, "only-python")
        os.makedirs(self.bin)
        os.symlink(sys.executable, os.path.join(self.bin, "python3"))
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.tools = os.path.join(self.tmp, "tools")
        os.makedirs(self.tools)
        for name in SYSTEM_TOOLS:
            found = shutil.which(name, path="/usr/bin:/bin")
            if found:
                os.symlink(found, os.path.join(self.tools, name))
        self.fake = os.path.join(self.tmp, "fake")
        os.makedirs(self.fake)

    def copy(self, path):
        target = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(os.path.join(REPO, path), target)

    def write(self, path, text):
        target = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)

    def git(self, *args):
        return subprocess.run(GIT + list(args), cwd=self.repo, capture_output=True, text=True, check=True).stdout

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "x")

    def release(self, *args, out=None):
        return subprocess.run(["sh", "tools/release.sh", *args, out or self.out], cwd=self.repo,
                              capture_output=True, text=True, timeout=120)

    def install(self, url=None, **extra):
        env = {"HOME": self.home, "PATH": os.pathsep.join([self.bin, "/usr/bin", "/bin"]), **extra}
        if url:
            env["SUPERHISTORY_ARCHIVE_URL"] = url
        return subprocess.run(["sh", os.path.join(self.out, "install.sh")], env=env, capture_output=True, text=True,
                              timeout=120)

    def command(self):
        folder = os.path.join(self.home, ".local", "bin")
        return os.path.join(folder, os.listdir(folder)[0])

    def test_the_three_assets_have_fixed_names_and_sums_that_check(self):
        built = self.release()
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertEqual(sorted(os.listdir(self.out)), ASSETS)
        with open(os.path.join(self.out, "SHA256SUMS")) as fh:
            sums = dict(line.split("  ")[::-1] for line in fh.read().split("\n") if line)
        self.assertEqual(sorted(sums), ["install.sh", "superhistory.tar.gz"])
        for name, digest in sums.items():
            self.assertEqual(sha256(os.path.join(self.out, name)), digest)

    def test_the_archive_is_the_commit_under_one_folder_without_bench(self):
        self.assertEqual(self.release().returncode, 0)
        with tarfile.open(os.path.join(self.out, "superhistory.tar.gz")) as archive:
            names = archive.getnames()
        self.assertEqual({n.split("/")[0] for n in names}, {"superhistory"})
        self.assertIn("superhistory/kb/__init__.py", names)
        self.assertIn("superhistory/install.sh", names)
        self.assertFalse([n for n in names if "bench" in n])

    def test_the_same_commit_builds_the_same_archive(self):
        self.assertEqual(self.release().returncode, 0)
        second = os.path.join(self.tmp, "again")
        self.assertEqual(self.release(out=second).returncode, 0)
        self.assertEqual(sha256(os.path.join(self.out, "superhistory.tar.gz")),
                         sha256(os.path.join(second, "superhistory.tar.gz")))

    def test_a_folder_with_something_in_it_is_refused(self):
        os.makedirs(self.out)
        with open(os.path.join(self.out, "keep"), "w") as fh:
            fh.write("x")
        result = self.release()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(os.listdir(self.out), ["keep"])

    def test_install_sh_installs_from_the_local_assets(self):
        self.assertEqual(self.release().returncode, 0)
        result = self.install(f"file://{self.out}/superhistory.tar.gz")
        self.assertEqual(result.returncode, 0, result.stderr)
        version = subprocess.run([self.command(), "--version"], capture_output=True, text=True, timeout=60,
                                 env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertRegex(version.stdout, r"\d+\.\d+\.\d+")

    def test_an_archive_that_is_not_the_one_summed_installs_nothing(self):
        self.assertEqual(self.release().returncode, 0)
        with open(os.path.join(self.out, "superhistory.tar.gz"), "ab") as fh:
            fh.write(b"tampered")
        result = self.install(f"file://{self.out}/superhistory.tar.gz")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match superhistory.tar.gz in SHA256SUMS", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local")))

    def test_a_release_without_sums_installs_nothing(self):
        self.assertEqual(self.release().returncode, 0)
        os.remove(os.path.join(self.out, "SHA256SUMS"))
        result = self.install(f"file://{self.out}/superhistory.tar.gz")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local")))

    def test_the_download_address_defaults_to_the_latest_release(self):
        self.assertEqual(self.release().returncode, 0)
        self.fake_curl(works=False)
        result = self.install_from_release()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(LATEST, self.calls(self.curl_log)[0])

    def fake_tool(self, name, body):
        path = os.path.join(self.fake, name)
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n" + body)
        os.chmod(path, 0o755)

    def fake_curl(self, works):
        # logs each call; a working curl copies the asset the URL names from the built folder, as GitHub would serve it
        self.curl_log = os.path.join(self.tmp, "curl.log")
        copy = (f'while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; *) url="$1"; shift ;; esac; done\n'
                f'cp "{self.out}/${{url##*/}}" "$out"\n')
        self.fake_tool("curl", f'echo "$@" >> "{self.curl_log}"\n' + (copy if works else
                       'echo "curl: (22) The requested URL returned error: 404" >&2\nexit 22\n'))

    def fake_gh(self, served=None, fails=False):
        # logs its arguments and writes the release's assets into --dir, from its own copy of the built folder
        self.gh_log = os.path.join(self.tmp, "gh.log")
        served = served or self.out
        body = f'echo "$@" >> "{self.gh_log}"\n'
        if fails:
            body += 'echo "HTTP 404: Not Found" >&2\nexit 1\n'
        else:
            body += ('while [ $# -gt 0 ]; do case "$1" in --dir) dir="$2"; shift 2 ;; *) shift ;; esac; done\n'
                     f'cp "{served}/superhistory.tar.gz" "{served}/SHA256SUMS" "$dir/"\n')
        self.fake_tool("gh", body)

    def install_from_release(self, *args, url=None):
        env = {"HOME": self.home, "PATH": os.pathsep.join([self.fake, self.bin, self.tools])}
        if url:
            env["SUPERHISTORY_ARCHIVE_URL"] = url
        return subprocess.run(["sh", os.path.join(self.out, "install.sh"), *args], env=env, capture_output=True,
                              text=True, timeout=120)

    def calls(self, log):
        if not os.path.exists(log):
            return []
        with open(log) as fh:
            return fh.read().splitlines()

    def assert_nothing_installed(self):
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local")))

    def test_a_failed_download_falls_back_to_the_collaborators_own_gh_login(self):
        self.assertEqual(self.release().returncode, 0)
        self.fake_curl(works=False)
        self.fake_gh()
        result = self.install_from_release("--tag", "v0.1.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(TAGGED, self.calls(self.curl_log)[0])
        [call] = self.calls(self.gh_log)
        self.assertRegex(call, r"^release download v0\.1\.0 --repo github.com/hunterbohm/superhistory --pattern "
                               r"superhistory\.tar\.gz --pattern SHA256SUMS --dir /\S+$")
        self.assertIn("your own gh login", result.stderr)
        version = subprocess.run([self.command(), "--version"], capture_output=True, text=True, timeout=60,
                                 env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
        self.assertEqual(version.returncode, 0, version.stderr)

    def test_with_no_tag_both_downloads_ask_for_the_latest_release(self):
        self.assertEqual(self.release().returncode, 0)
        self.fake_curl(works=False)
        self.fake_gh()
        result = self.install_from_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(LATEST, self.calls(self.curl_log)[0])
        [call] = self.calls(self.gh_log)
        self.assertTrue(call.startswith("release download --repo github.com/hunterbohm/superhistory --pattern "), call)

    def test_a_tampered_archive_from_gh_installs_nothing(self):
        self.assertEqual(self.release().returncode, 0)
        served = os.path.join(self.tmp, "served")
        shutil.copytree(self.out, served)
        with open(os.path.join(served, "superhistory.tar.gz"), "ab") as fh:
            fh.write(b"tampered")
        self.fake_curl(works=False)
        self.fake_gh(served)
        result = self.install_from_release("--tag", "v0.1.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match superhistory.tar.gz in SHA256SUMS", result.stderr)
        self.assertIn("Nothing was installed", result.stderr)
        self.assert_nothing_installed()

    def test_a_release_whose_sums_gh_cannot_find_installs_nothing(self):
        self.assertEqual(self.release().returncode, 0)
        served = os.path.join(self.tmp, "served")
        os.makedirs(served)
        shutil.copy2(os.path.join(self.out, "superhistory.tar.gz"), served)
        self.fake_curl(works=False)
        self.fake_tool("gh", 'while [ $# -gt 0 ]; do case "$1" in --dir) dir="$2"; shift 2 ;; *) shift ;; esac; done\n'
                             f'cp "{served}/superhistory.tar.gz" "$dir/"\n')
        result = self.install_from_release("--tag", "v0.1.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Nothing was installed", result.stderr)
        self.assert_nothing_installed()

    def test_with_no_gh_the_failure_says_how_to_sign_in_and_installs_nothing(self):
        self.assertEqual(self.release().returncode, 0)
        self.fake_curl(works=False)
        result = self.install_from_release("--tag", "v0.1.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("gh auth login", result.stderr)
        self.assertIn("Nothing was installed", result.stderr)
        self.assert_nothing_installed()

    def test_when_gh_fails_too_it_says_so_and_installs_nothing(self):
        self.assertEqual(self.release().returncode, 0)
        self.fake_curl(works=False)
        self.fake_gh(fails=True)
        result = self.install_from_release("--tag", "v0.1.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("HTTP 404", result.stderr)
        self.assertIn("gh auth login", result.stderr)
        self.assertIn("Nothing was installed", result.stderr)
        self.assert_nothing_installed()

    def test_a_working_public_download_never_calls_gh(self):
        self.assertEqual(self.release().returncode, 0)
        self.fake_curl(works=True)
        self.fake_gh()
        result = self.install_from_release("--tag", "v0.1.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls(self.curl_log)), 2)
        self.assertEqual(self.calls(self.gh_log), [])

    def test_an_address_given_by_hand_never_falls_back_to_gh(self):
        self.assertEqual(self.release().returncode, 0)
        self.fake_gh()
        os.symlink(shutil.which("curl"), os.path.join(self.fake, "curl"))
        result = self.install_from_release(url=f"file://{self.tmp}/missing/superhistory.tar.gz")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Nothing was installed", result.stderr)
        self.assertEqual(self.calls(self.gh_log), [])
        self.assert_nothing_installed()

    def test_a_tag_that_is_not_a_release_tag_is_refused_before_any_download(self):
        self.assertEqual(self.release().returncode, 0)
        self.fake_curl(works=True)
        self.fake_gh()
        for args in (["--tag"], ["--tag", ""], ["--tag", "../x"], ["--tag=v1 x"], ["--tag", "main"],
                     ["--tag", "-v1"], ["--tag", "v"], ["--tag", "v1/../x"], ["--tag", "v1;x"]):
            result = self.install_from_release(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn("--tag", result.stderr)
        self.assertEqual(self.calls(self.curl_log), [])
        self.assertEqual(self.calls(self.gh_log), [])
        self.assert_nothing_installed()

    def test_the_tag_jobs_run_nothing_in_a_private_repo(self):
        # a private release is made by hand (tools/release.sh, then gh release create), so its tag must bill no job
        with open(os.path.join(REPO, ".github", "workflows", "release.yml"), encoding="utf-8") as fh:
            text = fh.read()
        guards = dict(re.findall(r"^  (\w+):\n(?:    #.*\n)*    if: (.+)$", text, re.M))
        self.assertEqual(guards, {
            "build": "github.event_name == 'pull_request'",
            "test": "github.event_name == 'push' && !github.event.repository.private",
            "release": "github.event_name == 'push' && !github.event.repository.private",
        })


class ScanTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "tools"))
        shutil.copy2(os.path.join(REPO, "tools", "release_scan.py"), os.path.join(self.repo, "tools"))
        shutil.copy2(os.path.join(REPO, "tools", "release.sh"), os.path.join(self.repo, "tools"))
        shutil.copy2(os.path.join(REPO, "install.sh"), self.repo)
        self.patterns = os.path.join(self.tmp, "patterns")
        with open(self.patterns, "w") as fh:
            fh.write("acmecorp\nbox-?room\n")
        self.git("init", "-q")

    def git(self, *args):
        return subprocess.run(GIT + list(args), cwd=self.repo, capture_output=True, text=True, check=True).stdout

    def write(self, path, text):
        target = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)

    def scan(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "x")
        return subprocess.run([sys.executable, "-I", "tools/release_scan.py", self.patterns], cwd=self.repo,
                              capture_output=True, text=True, timeout=60)

    def test_a_clean_tree_passes(self):
        self.write("a.txt", "nothing here\n")
        self.assertEqual(self.scan().returncode, 0)

    def test_a_hit_is_named_by_file_and_line_and_fails(self):
        self.write("a.txt", "one\nsee AcmeCorp now\n")
        result = self.scan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("a.txt:2: see AcmeCorp now", result.stdout)

    def test_a_file_name_that_matches_fails(self):
        self.write("notes-boxroom.txt", "plain\n")
        result = self.scan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("notes-boxroom.txt: the file name matches", result.stdout)

    def test_an_exception_passes_its_own_text_in_its_own_file_only(self):
        self.write("tools/release_exceptions.tsv", "a.txt\tAcmeCorp\ta made-up company\n")
        self.write("a.txt", "see AcmeCorp now\n")
        self.write("b.txt", "see AcmeCorp now\n")
        result = self.scan()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("a.txt", result.stdout)
        self.assertIn("b.txt:1:", result.stdout)

    def test_an_exception_does_not_cover_another_match_on_the_same_line(self):
        self.write("tools/release_exceptions.tsv", "a.txt\tAcmeCorp\ta made-up company\n")
        self.write("a.txt", "AcmeCorp and boxroom\n")
        self.assertEqual(self.scan().returncode, 1)

    def test_the_list_may_hold_only_its_own_texts(self):
        self.write("tools/release_exceptions.tsv", "a.txt\tAcmeCorp\ta made-up company, not boxroom\n")
        self.write("a.txt", "see AcmeCorp now\n")
        result = self.scan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("tools/release_exceptions.tsv:1:", result.stdout)

    def private_exceptions(self, text):
        # beside the patterns file, outside the repo, as a private row must be: no tracked file may hold its text
        path = os.path.join(self.tmp, "exceptions.tsv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_a_private_exception_passes_its_own_text_in_its_own_file_only(self):
        private = self.private_exceptions("a.txt\tAcmeCorp\ta made-up company\n")
        self.write("a.txt", "see AcmeCorp now\n")
        self.write("b.txt", "see AcmeCorp now\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "x")
        result = subprocess.run([sys.executable, "-I", "tools/release_scan.py", "--exceptions", private, self.patterns],
                                cwd=self.repo, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("a.txt", result.stdout)
        self.assertIn("b.txt:1:", result.stdout)
        # without it, the same commit's a.txt is a hit
        result = subprocess.run([sys.executable, "-I", "tools/release_scan.py", self.patterns], cwd=self.repo,
                                capture_output=True, text=True, timeout=60)
        self.assertIn("a.txt:1:", result.stdout)

    def test_the_build_reads_the_private_exceptions(self):
        self.write("a.txt", "acmecorp\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "x")
        private = self.private_exceptions("a.txt\tacmecorp\ta made-up company\n")
        out = os.path.join(self.tmp, "assets")
        result = subprocess.run(["sh", "tools/release.sh", "--patterns", self.patterns, "--exceptions", private, out],
                                cwd=self.repo, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(os.listdir(out)), ASSETS)

    def test_the_dataset_files_are_not_read(self):
        self.write("bench/DATASET_ROWS.txt", "bench/rows.jsonl\tsomeone else's dataset\n")
        self.write("bench/rows.jsonl", "acmecorp\n")
        self.assertEqual(self.scan().returncode, 0)

    def test_a_binary_file_is_not_read(self):
        with open(os.path.join(self.repo, "b.bin"), "wb") as fh:
            fh.write(b"\0acmecorp\0")
        self.assertEqual(self.scan().returncode, 0)

    def test_a_hit_stops_the_build_before_it_writes_anything(self):
        self.write("a.txt", "acmecorp\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "x")
        out = os.path.join(self.tmp, "assets")
        result = subprocess.run(["sh", "tools/release.sh", "--patterns", self.patterns, out], cwd=self.repo,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
