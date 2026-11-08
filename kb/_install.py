# The superhistory command runs superhistory with this exact Python: macOS ties Full Disk Access (iMessage) to that one binary.
import argparse
import os
import shlex
import shutil
import sys
from pathlib import Path

import kb
from kb import COMMAND
from kb.paths import Paths

SHIM = f"""#!/bin/sh
# Written by {COMMAND}'s installer: runs {COMMAND} from {{app}} with the Python chosen at install time.
exec {{python}} -I -c 'import sys; sys.path.insert(0, sys.argv.pop(1)); from kb.cli import main; sys.exit(main())' \\
  {{app}} "$@"
"""


def install(src, app_root, bin_dir, python=sys.executable):
    src, app_root, bin_dir = Path(src).resolve(), Path(app_root), Path(bin_dir)
    version_dir = app_root / kb.__version__
    staging = app_root / f".{kb.__version__}.{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(src / "kb", staging / "kb", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    month = src / "demo" / "month"
    if month.is_dir():
        shutil.copytree(month, staging / "demo" / "month")
    if version_dir.exists():
        old = app_root / f".{kb.__version__}.old.{os.getpid()}"
        os.replace(version_dir, old)
        shutil.rmtree(old, ignore_errors=True)
    os.replace(staging, version_dir)
    link = app_root / f".current.{os.getpid()}"
    if link.is_symlink() or link.exists():
        link.unlink()
    os.symlink(kb.__version__, link)
    os.replace(link, app_root / "current")
    for other in app_root.iterdir():
        if other.is_dir() and not other.is_symlink() and other.name not in (kb.__version__,):
            shutil.rmtree(other, ignore_errors=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    command = bin_dir / COMMAND
    staged = bin_dir / f".{COMMAND}.{os.getpid()}"
    staged.write_text(SHIM.format(python=shlex.quote(python), app=shlex.quote(str(app_root / "current"))))
    staged.chmod(0o755)
    os.replace(staged, command)
    return command


def main(argv=None):
    p = argparse.ArgumentParser(prog="install.sh")
    p.add_argument("--from", dest="src", required=True)
    p.add_argument("--bin", default=os.path.expanduser("~/.local/bin"))
    args = p.parse_args(argv)
    paths = Paths.for_user()
    command = install(args.src, paths.data_dir / "app", args.bin)
    print(f"Installed {COMMAND} {kb.__version__} as {command}, run by Python {sys.version.split()[0]} at {sys.executable}.")
    if sys.platform == "darwin":
        print(f"To read iMessage, give Full Disk Access to {os.path.realpath(sys.executable)} "
              "in System Settings > Privacy & Security.")
    first = shutil.which(COMMAND)
    if first and Path(first).resolve() != command.resolve():
        print(f"Another {COMMAND} comes first on your PATH: {first}. Run {command}, "
              f"or put {command.parent} ahead of it in PATH.")
    elif not first:
        print(f"{command.parent} is not on your PATH. Add this line to your shell's startup file:\n"
              f'  export PATH="{command.parent}:$PATH"')
    print(f"Next: {COMMAND} try (a made-up month, nothing of yours), or {COMMAND} setup (your own sources).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
