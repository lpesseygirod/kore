#!/usr/bin/env bash
# KORE: send the committed code to the box, from Lea's Mac, over Tailscale. It is installed that night.
#
#   deploy/send_release.sh kore-abuelos            a new version: installed tonight between 03:30 and 04:30
#   deploy/send_release.sh --first kore-abuelos    the very first time (before install.sh): into the home folder
#   deploy/send_release.sh kore-abuelos pi         if the box's user isn't "lea"
#
# What is sent: `git archive HEAD`, the last commit only. Never .env, private/ or data/ (they aren't committed,
# and this script refuses to send if they ever are). Uncommitted changes are not sent.
# Install it straight away instead of waiting for the night:  ssh lea@kore-abuelos sudo kore-update --now
#
# KORE_DRY_RUN=1 only builds the file and prints where it is (used by the tests).
set -euo pipefail

first=0
if [ "${1:-}" = "--first" ]; then
    first=1
    shift
fi
host="${1:-}"
user="${2:-${KORE_PI_USER:-lea}}"
if [ -z "$host" ] && [ -z "${KORE_DRY_RUN:-}" ]; then
    echo "usage: deploy/send_release.sh [--first] BOX-NAME [USER]   (for example: kore-abuelos)" >&2
    exit 2
fi

repo="$(git rev-parse --show-toplevel)"
cd "$repo"

secret_pattern='(^|/)\.env($|\.)|(^|/)(private|data)/|\.db$|\.db-wal$'
if git ls-files | grep -Eq "$secret_pattern"; then
    echo "Refused: a secret or private file is committed:" >&2
    git ls-files | grep -E "$secret_pattern" >&2
    exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Note: there are uncommitted changes; they are NOT in this version (only the last commit is)." >&2
fi

version="$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"
name="kore-$version.tar.gz"
out="$(mktemp -d "${TMPDIR:-/tmp}/kore-release.XXXXXX")"
git archive --format=tar.gz -o "$out/$name" HEAD
if tar -tzf "$out/$name" | grep -Eq "$secret_pattern"; then
    echo "Refused: the archive would contain a secret or private file." >&2
    exit 1
fi
(cd "$out" && shasum -a 256 "$name" >"$name.sha256")

if [ -n "${KORE_DRY_RUN:-}" ]; then
    echo "$out/$name"
    exit 0
fi

target="$user@$host"
if [ "$first" -eq 1 ]; then
    scp "$out/$name" "$out/$name.sha256" "$target:"
    echo
    echo "Sent $name to $host. Now, on the box (ssh $target), paste these two lines:"
    echo "  mkdir -p ~/kore-setup && tar -xzf ~/$name -C ~/kore-setup"
    echo "  sudo bash ~/kore-setup/deploy/pi/install.sh ~/$name"
    exit 0
fi

incoming=/var/lib/kore/incoming
scp "$out/$name.sha256" "$target:$incoming/"
scp "$out/$name" "$target:$incoming/$name.part"
# shellcheck disable=SC2029  # the names are expanded here on purpose
ssh "$target" "mv -f '$incoming/$name.part' '$incoming/$name'"
echo "Sent $name to $host. It is installed tonight between 03:30 and 04:30, after a self-test;"
echo "if anything goes wrong the box goes back to the version it has now. See what happened tomorrow with:"
echo "  ssh $target journalctl -u kore-update --since yesterday"
