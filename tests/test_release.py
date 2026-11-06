import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = ["SHA256SUMS", "install.sh", "superhistory.tar.gz"]
LATEST = "https://github.com/hunterbohm/superhistory/releases/latest/download/superhistory.tar.gz"
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
        fake = os.path.join(self.tmp, "fake")
        os.makedirs(fake)
        log = os.path.join(self.tmp, "curl.log")
        with open(os.path.join(fake, "curl"), "w") as fh:
            fh.write(f'#!/bin/sh\necho "$@" >> {log}\nexit 22\n')
        os.chmod(os.path.join(fake, "curl"), 0o755)
        env = {"HOME": self.home, "PATH": os.pathsep.join([fake, self.bin, "/usr/bin", "/bin"])}
        result = subprocess.run(["sh", os.path.join(self.out, "install.sh")], env=env, capture_output=True, text=True,
                                timeout=60)
        self.assertNotEqual(result.returncode, 0)
        with open(log) as fh:
            self.assertIn(LATEST, fh.read())


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
