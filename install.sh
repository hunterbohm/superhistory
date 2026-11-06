#!/bin/sh
# Installs kb for the current user on a Mac or Linux: no root, no pip, no virtual environment. It needs Python 3.9 or
# newer, then copies kb into your data folder and puts the `kb` command in ~/.local/bin.
#
#   sh install.sh --from /path/to/a/checkout
#   curl -fsSL https://github.com/hunterbohm/superhistory/releases/latest/download/install.sh | sh
#
# With no --from it downloads the latest release's archive and checks it against the SHA256SUMS file next to it.
# SUPERHISTORY_ARCHIVE_URL names another archive, such as file:///path/to/superhistory.tar.gz; its SHA256SUMS must
# be in the same folder.
set -eu

ARCHIVE_URL="${SUPERHISTORY_ARCHIVE_URL:-https://github.com/hunterbohm/superhistory/releases/latest/download/superhistory.tar.gz}"
SRC=""
while [ $# -gt 0 ]; do
  case "$1" in
    --from) SRC="$2"; shift 2 ;;
    --from=*) SRC="${1#--from=}"; shift ;;
    *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
  esac
done

find_python() {
  for py in "${SUPERHISTORY_PYTHON:-}" python3 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9; do
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
  NAME="${ARCHIVE_URL##*/}"
  curl -fsSL -o "$WORK/archive" "$ARCHIVE_URL"
  curl -fsSL -o "$WORK/SHA256SUMS" "${ARCHIVE_URL%/*}/SHA256SUMS"
  "$PY" -I - "$WORK/archive" "$WORK/SHA256SUMS" "$NAME" <<'PYEOF' || exit 1
import hashlib, sys
archive, sums, name = sys.argv[1:]
with open(archive, "rb") as fh:
    got = hashlib.sha256(fh.read()).hexdigest()
want = [line.split()[0] for line in open(sums, encoding="utf-8") if line.split()[1:] == [name]]
if want != [got]:
    sys.exit("install.sh: the download does not match %s in SHA256SUMS (got %s). Nothing was installed." % (name, got))
PYEOF
  mkdir "$WORK/src"
  tar -xzf "$WORK/archive" -C "$WORK/src"
  SRC="$(find "$WORK/src" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
fi

if [ ! -f "$SRC/kb/__init__.py" ]; then
  echo "install.sh: $SRC does not hold kb's source (no kb/__init__.py)." >&2
  exit 1
fi

PYTHONPATH="$SRC" "$PY" -m kb._install --from "$SRC"
