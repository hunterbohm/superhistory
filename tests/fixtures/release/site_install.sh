#!/bin/sh
# Installs Superhistory for the current user on a Mac or Linux computer. It finds the newest release of the
# open-source repo, downloads that release's source, and runs the installer inside it, which needs Python 3.9 or
# newer and puts the `superhistory` command in ~/.local/bin. Nothing runs as root.
#
#   curl -fsSL https://{{domain}}/install.sh | sh
#
# SUPERHISTORY_REPO installs from another copy of the repo, such as a fork.
set -eu

DEFAULT_REPO="https://example.invalid/none"

say() { printf '%s\n' "$*" >&2; }
fail() { say "install.sh: $*"; exit 1; }

# The whole script is one function called on the last line, so a download cut off partway runs nothing.
main() {
	for tool in curl tar; do
		command -v "$tool" >/dev/null 2>&1 || fail "this needs $tool. Install it, then run this again."
	done

	repo="${SUPERHISTORY_REPO:-$DEFAULT_REPO}"
	repo="${repo%/}"
	# Every request and every redirect stays on https, so the code this runs never travels unencrypted.
	case "$repo" in
		https://*) ;;
		*) fail "the repo address must start with https://." ;;
	esac

	# GitHub answers /releases/latest with a redirect to the newest release, /releases/tag/<tag>.
	latest="$(curl -fsSL --proto =https --proto-redir =https -o /dev/null -w '%{url_effective}' "$repo/releases/latest")" ||
		fail "found no release at $repo/releases/latest."
	case "$latest" in
		*/releases/tag/?*) ;;
		*) fail "found no release at $repo/releases/latest." ;;
	esac
	tag="${latest##*/releases/tag/}"
	case "$tag" in
		*[!A-Za-z0-9._-]*) fail "the newest release's tag has characters other than letters, digits, dots, dashes and underscores." ;;
	esac

	work="$(mktemp -d)"
	trap 'rm -rf "$work"' EXIT
	trap 'exit 130' INT TERM
	say "Downloading $tag from $repo"
	curl -fsSL --proto =https --proto-redir =https "$repo/archive/refs/tags/$tag.tar.gz" -o "$work/release.tar.gz" ||
		fail "could not download $repo/archive/refs/tags/$tag.tar.gz."
	mkdir "$work/src"
	tar -xzf "$work/release.tar.gz" -C "$work/src" || fail "could not unpack release $tag."
	src="$(find "$work/src" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
	[ -n "$src" ] && [ -f "$src/install.sh" ] || fail "release $tag holds no install.sh."
	sh "$src/install.sh" --from "$src" </dev/null
}

main "$@"
