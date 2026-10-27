#!/bin/sh
# Installs kb for the current user on a Mac or Linux: no root, no pip, no virtual environment. It needs Python 3.9 or
# newer, then copies kb into your data folder and puts the `kb` command in ~/.local/bin.
#
#   sh install.sh --from /path/to/a/checkout
#   curl -fsSL <address of install.sh> | sh        (downloads the release archive named below)
set -eu

ARCHIVE_URL="${KB_ARCHIVE_URL:-}"
SRC=""
while [ $# -gt 0 ]; do
  case "$1" in
    --from) SRC="$2"; shift 2 ;;
    --from=*) SRC="${1#--from=}"; shift ;;
    *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
  esac
done

find_python() {
  for py in "${KB_PYTHON:-}" python3 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9; do
    [ -n "$py" ] || continue
    command -v "$py" >/dev/null 2>&1 || continue
    if "$py" -c 'import sqlite3, sys
sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(x)")
sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
      command -v "$py"
      return 0
    fi
  done
  return 1
}

if ! PY="$(find_python)"; then
  echo "install.sh: kb needs Python 3.9 or newer with SQLite full-text search." >&2
  echo "Install Python 3 (on a Mac: xcode-select --install, or brew install python), then run this again." >&2
  exit 1
fi

if [ -z "$SRC" ]; then
  if [ -z "$ARCHIVE_URL" ]; then
    echo "install.sh: this copy has no download address; run it from a checkout with --from DIR." >&2
    exit 1
  fi
  WORK="$(mktemp -d)"
  trap 'rm -rf "$WORK"' EXIT
  curl -fsSL "$ARCHIVE_URL" | tar -xz -C "$WORK"
  SRC="$(find "$WORK" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
fi

if [ ! -f "$SRC/kb/__init__.py" ]; then
  echo "install.sh: $SRC does not hold kb's source (no kb/__init__.py)." >&2
  exit 1
fi

PYTHONPATH="$SRC" "$PY" -m kb._install --from "$SRC"
