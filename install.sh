#!/bin/sh
# Installs superhistory for the current user on a Mac or Linux: no root, no pip, no virtual environment. It needs Python 3.9 or
# newer, then copies superhistory into your data folder and puts the `superhistory` command in ~/.local/bin.
#
#   sh install.sh --from /path/to/a/checkout
#   curl -fsSL https://github.com/hunterbohm/superhistory/releases/latest/download/install.sh | sh
#   gh release download v0.1.0 --repo github.com/hunterbohm/superhistory --pattern install.sh --output - \
#     | sh -s -- --tag v0.1.0                     (while the repo is private: someone invited to it, signed in to gh)
#
# With no --from it downloads the release's archive, the latest or the one --tag names, and checks it against the
# SHA256SUMS file next to it. If that download fails and GitHub's gh is installed, it asks gh for the same two files,
# which gh fetches with the person's own GitHub login, and checks them the same way.
# SUPERHISTORY_ARCHIVE_URL names another archive, such as file:///path/to/superhistory.tar.gz; its SHA256SUMS must
# be in the same folder, and gh is not asked.
set -eu

REPO="github.com/hunterbohm/superhistory"
SRC=""
TAG=""
TAGGED=""
while [ $# -gt 0 ]; do
  case "$1" in
    --from) SRC="$2"; shift 2 ;;
    --from=*) SRC="${1#--from=}"; shift ;;
    --tag)
      [ $# -ge 2 ] || { echo "install.sh: --tag takes a release tag, such as v0.1.0" >&2; exit 2; }
      TAG="$2"; TAGGED=1; shift 2 ;;
    --tag=*) TAG="${1#--tag=}"; TAGGED=1; shift ;;
    *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
  esac
done
if [ -n "$TAGGED" ]; then
  case "$TAG" in
    ""|[!v]*|v|v[!0-9]*|*[!A-Za-z0-9._-]*) echo "install.sh: --tag takes a release tag, such as v0.1.0" >&2; exit 2 ;;
  esac
  RELEASE="https://$REPO/releases/download/$TAG"
else
  RELEASE="https://$REPO/releases/latest/download"
fi
ARCHIVE_URL="${SUPERHISTORY_ARCHIVE_URL:-$RELEASE/superhistory.tar.gz}"

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
  echo "install.sh: superhistory needs Python 3.9 or newer with SQLite full-text search." >&2
  echo "Install Python 3 (on a Mac: xcode-select --install, or brew install python), then run this again." >&2
  exit 1
fi

if [ -z "$SRC" ]; then
  WORK="$(mktemp -d)"
  trap 'rm -rf "$WORK"' EXIT
  NAME="${ARCHIVE_URL##*/}"
  if curl -fsSL -o "$WORK/archive" "$ARCHIVE_URL" && curl -fsSL -o "$WORK/SHA256SUMS" "${ARCHIVE_URL%/*}/SHA256SUMS"
  then
    :
  elif [ -n "${SUPERHISTORY_ARCHIVE_URL:-}" ]; then
    echo "install.sh: could not download $ARCHIVE_URL and the SHA256SUMS beside it. Nothing was installed." >&2
    exit 1
  elif command -v gh >/dev/null 2>&1; then
    echo "install.sh: the public download failed, so this asks GitHub for the release with your own gh login." >&2
    mkdir "$WORK/gh"
    if ! gh release download ${TAG:+"$TAG"} --repo "$REPO" --pattern "$NAME" --pattern SHA256SUMS --dir "$WORK/gh" ||
      [ ! -f "$WORK/gh/$NAME" ] || [ ! -f "$WORK/gh/SHA256SUMS" ]
    then
      echo "install.sh: gh could not download it either. If you were invited to $REPO, accept the invitation," \
        "run gh auth login with that GitHub account, and run this again. Nothing was installed." >&2
      exit 1
    fi
    mv "$WORK/gh/$NAME" "$WORK/archive"
    mv "$WORK/gh/SHA256SUMS" "$WORK/SHA256SUMS"
  else
    echo "install.sh: the download failed. If $REPO is private and you were invited to it, install GitHub's gh" \
      "(https://cli.github.com), run gh auth login with that GitHub account, and run this again." \
      "Nothing was installed." >&2
    exit 1
  fi
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
  echo "install.sh: $SRC does not hold superhistory's source (no kb/__init__.py)." >&2
  exit 1
fi

PYTHONPATH="$SRC" "$PY" -m kb._install --from "$SRC"
