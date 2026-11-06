import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import kb
from kb import _install

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTH = os.path.join(REPO, "kb", "sample_month")


class InstallTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.bin = os.path.join(self.tmp, "only-python")
        os.makedirs(self.bin)
        os.symlink(sys.executable, os.path.join(self.bin, "python3"))

    def env(self, **extra):
        path = os.pathsep.join([self.bin, "/usr/bin", "/bin"])
        return {"HOME": self.home, "PATH": path, "SUPERHISTORY_HOME": os.path.join(self.tmp, "kbhome"), **extra}

    def install_sh(self, src=REPO, **extra):
        return subprocess.run(["sh", os.path.join(REPO, "install.sh"), "--from", src], env=self.env(**extra),
                              capture_output=True, text=True, timeout=120)

    def kb(self, *args, **extra):
        return subprocess.run([os.path.join(self.home, ".local", "bin", "superhistory"), *args], env=self.env(**extra),
                              capture_output=True, text=True, timeout=120, cwd=self.tmp)

    def test_install_sh_writes_a_kb_command_that_runs_from_any_folder(self):
        result = self.install_sh()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Installed superhistory {kb.__version__}", result.stdout)
        self.assertIn("is not on your PATH", result.stdout)
        version = self.kb("--version")
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertIn(kb.__version__, version.stdout)
        tried = self.kb("try")
        self.assertEqual(tried.returncode, 0, tried.stderr)
        # Whichever month the checkout ships: the Benchmarks month in demo/month, or the small one inside superhistory.
        self.assertRegex(tried.stdout, r"Loaded \d+ made-up records")

    def test_the_command_ignores_the_checkout_and_pythonpath(self):
        self.assertEqual(self.install_sh().returncode, 0)
        decoy = os.path.join(self.tmp, "decoy")
        os.makedirs(os.path.join(decoy, "kb"))
        with open(os.path.join(decoy, "kb", "__init__.py"), "w") as fh:
            fh.write("raise SystemExit('the decoy was imported')\n")
        result = self.kb("--version", PYTHONPATH=decoy)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_installing_again_replaces_the_version_and_keeps_one_copy(self):
        self.assertEqual(self.install_sh().returncode, 0)
        self.assertEqual(self.install_sh().returncode, 0)
        app = os.path.join(self.tmp, "kbhome", "data", "app")
        self.assertEqual(sorted(os.listdir(app)), [kb.__version__, "current"])
        self.assertEqual(self.kb("--version").returncode, 0)

    def test_the_made_up_month_ships_with_the_install_when_the_checkout_has_one(self):
        src = os.path.join(self.tmp, "src")
        shutil.copytree(os.path.join(REPO, "kb"), os.path.join(src, "kb"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(MONTH, os.path.join(src, "demo", "month"))
        self.assertEqual(self.install_sh(src).returncode, 0)
        tried = self.kb("try")
        self.assertEqual(tried.returncode, 0, tried.stderr)
        self.assertIn("Loaded 8 made-up records", tried.stdout)

    def test_another_kb_first_on_path_is_named(self):
        other = os.path.join(self.tmp, "other-bin")
        os.makedirs(other)
        with open(os.path.join(other, "superhistory"), "w") as fh:
            fh.write("#!/bin/sh\necho other\n")
        os.chmod(os.path.join(other, "superhistory"), 0o755)
        result = subprocess.run(["sh", os.path.join(REPO, "install.sh"), "--from", REPO],
                                env={**self.env(), "PATH": os.pathsep.join([other, self.bin, "/usr/bin", "/bin"])},
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Another superhistory comes first on your PATH: {other}/superhistory", result.stdout)

    def test_a_folder_without_kb_is_refused(self):
        result = self.install_sh(self.tmp)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not hold superhistory's source", result.stderr)

    def test_no_download_address_means_install_from_a_checkout(self):
        result = subprocess.run(["sh", os.path.join(REPO, "install.sh")], env=self.env(), capture_output=True,
                                text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--from DIR", result.stderr)

    def test_install_function_writes_a_shim_pinned_to_one_python(self):
        command = _install.install(REPO, os.path.join(self.tmp, "app"), os.path.join(self.tmp, "bin"), "/opt/py 3/python3")
        text = open(command).read()
        self.assertIn("exec '/opt/py 3/python3' -I -c", text)
        self.assertTrue(os.access(command, os.X_OK))


if __name__ == "__main__":
    unittest.main()
