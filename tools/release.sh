#!/bin/sh
# Builds a release's three assets from one commit into a folder:
#
#   sh tools/release.sh OUT_DIR [COMMIT]          COMMIT defaults to HEAD; OUT_DIR must be absent or empty
#   sh tools/release.sh --patterns FILE OUT_DIR [COMMIT]
#
#   superhistory.tar.gz   the commit's source under one top folder, as install.sh expects; bench/ is left out, since it
#                         is 46 MB of results that the installer never reads
#   install.sh            the commit's installer
#   SHA256SUMS            both files' sha256, as `sha256sum -c` reads them
#
# The release workflow runs this on a pushed tag, so these names are the ones `releases/latest/download/<name>` serves.
# To try the install against the files, with no network:
#
#   SUPERHISTORY_ARCHIVE_URL=file://OUT_DIR/superhistory.tar.gz sh OUT_DIR/install.sh
#
# --patterns FILE first scans the commit with tools/release_scan.py, which says what it reads. A hit stops the build.
set -eu

PATTERNS=""
if [ "${1:-}" = "--patterns" ]; then
  [ $# -ge 3 ] || { echo "release.sh: --patterns takes a file, then OUT_DIR" >&2; exit 2; }
  PATTERNS="$2"
  shift 2
fi
[ $# -ge 1 ] && [ $# -le 2 ] || { echo "usage: sh tools/release.sh [--patterns FILE] OUT_DIR [COMMIT]" >&2; exit 2; }
OUT="$1"
COMMIT="$(git rev-parse --verify "${2:-HEAD}^{commit}")"

if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then
  echo "release.sh: $OUT is not empty." >&2
  exit 2
fi

if [ -n "$PATTERNS" ]; then
  python3 -I "$(dirname "$0")/release_scan.py" "$PATTERNS" "$COMMIT" || {
    echo "release.sh: the hits above are not in tools/release_exceptions.tsv." >&2
    exit 1
  }
fi

mkdir -p "$OUT"
git archive --format=tar --prefix=superhistory/ "$COMMIT" -- . ':(exclude)bench' | gzip -9n > "$OUT/superhistory.tar.gz"
git show "$COMMIT:install.sh" > "$OUT/install.sh"
(
  cd "$OUT"
  if command -v sha256sum >/dev/null 2>&1; then sums=sha256sum; else sums="shasum -a 256"; fi
  $sums install.sh superhistory.tar.gz > SHA256SUMS
)
echo "Built from $COMMIT into $OUT:"
ls -l "$OUT"
