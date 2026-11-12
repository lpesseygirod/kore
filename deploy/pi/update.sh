#!/usr/bin/env bash
# KORE: install a new version of Pepe on the box, only at night, and undo it by itself if anything is wrong.
#
# Installed by install.sh as /usr/local/sbin/kore-update and run by kore-update.timer every night at 03:30.
#   sudo kore-update          what the timer runs: does nothing outside 03:30-04:30
#   sudo kore-update --now    install the waiting version straight away (by hand)
#
# Where a new version comes from: deploy/send_release.sh on my Mac copies kore-<version>.tar.gz and its
# .sha256 into /var/lib/kore/incoming (over Tailscale). The newest one there is installed:
#   1. check its checksum (a half-copied file is refused)
#   2. unpack it into /opt/kore/releases/<version> and build its Python environment (uv, from uv.lock)
#   3. self-test the new code against the real data (the database is only read; a copy is upgraded)
#   4. keep a snapshot of the database, switch /opt/kore/current to the new version, restart Pepe and the page
#   5. self-test again, with both running
#   6. if 3 fails: nothing was changed. If 5 fails: switch back to the previous version, restart, self-test.
# Every step is logged to the journal: journalctl -u kore-update
#
# Paths and commands can be changed through KORE_* variables only so tests can run this on a Mac in a temporary
# folder; on the box the defaults are always used.
set -euo pipefail
umask 022

OPT="${KORE_OPT:-/opt/kore}"
DATA="${KORE_DATA:-/var/lib/kore}"
ETC="${KORE_ETC:-/etc/kore}"
SYSTEMCTL="${KORE_SYSTEMCTL:-systemctl}"
UV="${KORE_UV:-$OPT/tools/bin/uv}"
SETTLE_S="${KORE_SETTLE_S:-30}"
PYTHON_VERSION="${KORE_PYTHON_VERSION:-3.13}"
# Only on the Pi: the libraries for the button, sensor and light ring (BSD / Unlicense / MIT), pinned.
PI_PACKAGES="${KORE_PI_PACKAGES-gpiozero==2.0.1 lgpio==0.2.2.0 spidev==3.6}"

RELEASES="$OPT/releases"
CURRENT="$OPT/current"
INCOMING="$DATA/incoming"
DB="$DATA/kore.db"
SERVICES="kore-home.service kore-web.service"

log() { echo "kore-update: $*"; }

usage() { echo "usage: kore-update [--now]" >&2; }

now_mode=0
while [ $# -gt 0 ]; do
    case "$1" in
        --now) now_mode=1 ;;
        -h | --help) usage; exit 0 ;;
        *) usage; exit 2 ;;
    esac
    shift
done

in_window() {
    local hhmm
    hhmm="${KORE_CLOCK_HHMM:-$(date +%H%M)}"
    hhmm=$((10#$hhmm))
    [ "$hhmm" -ge 330 ] && [ "$hhmm" -lt 430 ]
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

# Run a command as the kore user with the keys loaded, the way the services run (the keys file is root-only).
as_kore() {
    if [ -n "${KORE_RUN_DIRECT:-}" ]; then
        "$@"
        return
    fi
    systemd-run --quiet --wait --pipe --collect --service-type=exec \
        --uid=kore --gid=kore -p "EnvironmentFile=$ETC/kore.env" -p "WorkingDirectory=$DATA" "$@"
}

selftest() {  # selftest RELEASE_DIR [extra args]
    local rel="$1"
    shift
    as_kore "$rel/.venv/bin/python" -m kore.selftest --db "$DB" "$@"
}

# Point /opt/kore/current at a release, atomically where the system allows it (GNU mv -T on the Pi).
switch_to() {
    ln -sfn "$1" "$CURRENT.new"
    if ! mv -T "$CURRENT.new" "$CURRENT" 2>/dev/null; then
        rm -f "$CURRENT.new"
        ln -sfn "$1" "$CURRENT"
    fi
}

restart_services() {
    # shellcheck disable=SC2086  # SERVICES is a fixed list of words
    "$SYSTEMCTL" restart $SERVICES
}

services_active() {
    local unit
    for unit in $SERVICES; do
        if ! "$SYSTEMCTL" is-active --quiet "$unit"; then
            log "$unit is not running"
            return 1
        fi
    done
}

healthy() {  # healthy RELEASE_DIR: both services up, self-test passes with the page answering, still up after
    sleep "$SETTLE_S"
    services_active || return 1
    selftest "$1" --require-web || return 1
    services_active
}

file_away() {  # file_away TARBALL done|rejected
    mkdir -p "$INCOMING/$2"
    mv -f "$1" "$INCOMING/$2/"
    if [ -e "$1.sha256" ]; then
        mv -f "$1.sha256" "$INCOMING/$2/"
    fi
}

snapshot_db() {  # keep a copy of the database before switching; the three newest are kept
    local rel="$1" version="$2" snap old count
    [ -f "$DB" ] || return 0
    mkdir -p "$DATA/snapshots"
    if [ -z "${KORE_RUN_DIRECT:-}" ]; then
        chown kore:kore "$DATA/snapshots"
        chmod 700 "$DATA/snapshots"
    fi
    snap="$DATA/snapshots/before-$version.db"
    as_kore "$rel/.venv/bin/python" -c \
        'import sqlite3, sys
src = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()' "$DB" "$snap"
    count=0
    for old in "$DATA"/snapshots/before-*.db; do
        [ -e "$old" ] && count=$((count + 1))
    done
    for old in "$DATA"/snapshots/before-*.db; do  # oldest first: names start with the date
        [ "$count" -le 3 ] && break
        rm -f "$old"
        count=$((count - 1))
    done
}

# --- start -------------------------------------------------------------------------------------------------------

if [ "$now_mode" -eq 0 ] && ! in_window; then
    log "outside the night window (03:30-04:30): nothing done"
    exit 0
fi

mkdir -p "$INCOMING"
if command -v flock >/dev/null 2>&1; then
    exec 9>"$DATA/.update.lock"
    if ! flock -n 9; then
        log "another update is running"
        exit 0
    fi
fi

tarball=""
for f in "$INCOMING"/kore-*.tar.gz; do  # sorted by name, and names start with the date: the last is the newest
    [ -e "$f" ] && tarball="$f"
done
if [ -z "$tarball" ]; then
    log "no new version waiting"
    exit 0
fi

name="$(basename "$tarball" .tar.gz)"
version="${name#kore-}"
if ! [[ "$version" =~ ^[A-Za-z0-9._-]+$ ]]; then
    log "refused $name: odd version name"
    file_away "$tarball" rejected
    exit 1
fi
if [ ! -f "$tarball.sha256" ] || [ "$(awk '{print $1}' "$tarball.sha256")" != "$(sha256_of "$tarball")" ]; then
    log "refused $name: checksum missing or wrong (copied only halfway?)"
    file_away "$tarball" rejected
    exit 1
fi

dest="$RELEASES/$version"
previous=""
if [ -L "$CURRENT" ]; then
    previous="$(readlink "$CURRENT")"
fi
if [ "$previous" = "$dest" ]; then
    log "$version is already installed"
    file_away "$tarball" "done"
    exit 0
fi
if [ -e "$dest" ]; then
    log "refused $version: it was installed before and taken back; send a new version"
    file_away "$tarball" rejected
    exit 1
fi

# --- 2. unpack and build -----------------------------------------------------------------------------------------

log "installing $version (now: ${previous:-nothing})"
mkdir -p "$RELEASES"
mkdir "$dest"
discard() {  # the new version never ran: remove it and keep the tarball apart
    log "$1: $version not installed; the box keeps ${previous:-nothing}"
    rm -rf "$dest"
    file_away "$tarball" rejected
    exit 1
}
tar -xzf "$tarball" -C "$dest" --no-same-owner || discard "unpacking failed"
if [ ! -f "$dest/pyproject.toml" ] || [ ! -f "$dest/kore/selftest.py" ]; then
    discard "not a KORE release"
fi
(
    cd "$dest"
    UV_PYTHON_INSTALL_DIR="$OPT/python" UV_CACHE_DIR="$OPT/cache" UV_PYTHON_PREFERENCE=only-managed \
        "$UV" sync --frozen --no-dev --group live --no-editable --compile-bytecode --python "$PYTHON_VERSION"
) || discard "building the Python environment failed (internet down?)"
if [ -n "$PI_PACKAGES" ] && grep -q "Raspberry Pi" /proc/device-tree/model 2>/dev/null; then
    # shellcheck disable=SC2086  # a list of pinned packages
    UV_CACHE_DIR="$OPT/cache" "$UV" pip install --python "$dest/.venv/bin/python" $PI_PACKAGES \
        || discard "installing the button/sensor/light libraries failed"
fi

# --- first install: nothing to compare with or go back to --------------------------------------------------------

if [ -z "$previous" ]; then
    switch_to "$dest"
    file_away "$tarball" "done"
    log "first install of $version done. Self-test (for information; keys and the database may not exist yet):"
    selftest "$dest" || true
    exit 0
fi

# --- 3. self-test the new code before anything changes -----------------------------------------------------------

if ! selftest "$dest"; then
    discard "the self-test failed before switching"
fi

if [ "$now_mode" -eq 0 ] && ! in_window; then
    log "too late tonight to switch; trying again tomorrow night"
    rm -rf "$dest"
    exit 0
fi

# --- 4. switch, 5. check, 6. or go back --------------------------------------------------------------------------

snapshot_db "$dest" "$version" || discard "the database snapshot failed"
switch_to "$dest"
log "switched to $version; restarting"
restart_services || true
if healthy "$dest"; then
    file_away "$tarball" "done"
    log "$version installed and healthy (previous: $(basename "$previous"))"
    exit 0
fi

log "$version is not healthy: going back to $(basename "$previous")"
switch_to "$previous"
restart_services || true
file_away "$tarball" rejected
if healthy "$previous"; then
    log "rolled back to $(basename "$previous"); it is healthy again. $version stays in $RELEASES for a look."
else
    log "rolled back to $(basename "$previous"), but it is STILL not healthy: needs Lea (journalctl -u kore-home)"
fi
exit 1
