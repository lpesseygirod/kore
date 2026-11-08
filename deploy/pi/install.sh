#!/usr/bin/env bash
# KORE: set up a Raspberry Pi (Raspberry Pi OS Bookworm, 64-bit) to run Pepe. docs/DEVICE.md explains each step.
#
#   sudo bash install.sh ~/kore-<version>.tar.gz [--rtc-charge]
#
# Safe to run again (after an update of these deploy files, or if something went wrong half-way): every step
# checks what is already there. It never touches the database or the keys file once they exist.
#
#   --rtc-charge   only with the official Raspberry Pi 5 RTC battery (rechargeable): lets the Pi charge it.
#                  Never with an ordinary coin cell.
#
# What it sets up:
#   apt packages (alsa-utils for the speakerphone, build tools for the light ring's library, sqlite3 for repairs)
#   the "kore" system user, which runs Pepe (no login, no password)
#   uv (pinned), in /opt/kore/tools; Python itself goes in /opt/kore/python
#   /opt/kore/releases/<version> and the /opt/kore/current link (the code: read-only for Pepe)
#   /var/lib/kore (the data: database, logs of turns, backups; never inside a release)
#   /etc/kore/kore.env (the keys: root only, mode 600; created empty, Lea types the keys with kore-set-key)
#   kore-set-key, kore-selftest and kore-update in /usr/local/sbin
#   systemd: kore-home, kore-web (start on boot, restart on a crash), kore-update.timer (nights only)
#   persistent logs kept 7 days, the hardware watchdog, SPI on (for the light ring), the time zone Europe/Madrid
set -euo pipefail

UV_VERSION="0.13.7"
OPT=/opt/kore
DATA=/var/lib/kore
ETC=/etc/kore
SBIN=/usr/local/sbin

say() { printf '\n== %s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run it with sudo: sudo bash install.sh ~/kore-<version>.tar.gz"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

tarball=""
rtc_charge=0
for arg in "$@"; do
    case "$arg" in
        --rtc-charge) rtc_charge=1 ;;
        -*) die "unknown option $arg" ;;
        *) tarball="$arg" ;;
    esac
done
if [ -n "$tarball" ]; then
    [ -f "$tarball" ] || die "no file $tarball"
    [ -f "$tarball.sha256" ] || die "the checksum file $tarball.sha256 is missing (send_release.sh --first sends it)"
    case "$(basename "$tarball")" in
        kore-*.tar.gz) ;;
        *) die "expected a file named kore-<version>.tar.gz" ;;
    esac
fi
admin="${SUDO_USER:-}"

# --- packages ----------------------------------------------------------------------------------------------------
say "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
    alsa-utils ca-certificates python3 python3-venv build-essential sqlite3 openssl

# --- the kore user ------------------------------------------------------------------------------------------------
say "The kore user"
if ! id kore >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$DATA" --no-create-home --shell /usr/sbin/nologin kore
fi
for group in audio gpio spi i2c dialout; do
    if getent group "$group" >/dev/null; then
        usermod -aG "$group" kore
    fi
done
if [ -n "$admin" ] && [ "$admin" != "root" ]; then
    usermod -aG kore "$admin"  # so new versions can be copied into /var/lib/kore/incoming
fi

# --- folders -----------------------------------------------------------------------------------------------------
say "Folders"
install -d -o root -g root -m 755 "$OPT" "$OPT/releases"
install -d -o kore -g kore -m 750 "$DATA"
install -d -o kore -g kore -m 2770 "$DATA/incoming"
install -d -o root -g root -m 700 "$ETC"
if [ ! -e "$ETC/kore.env" ]; then
    install -o root -g root -m 600 /dev/null "$ETC/kore.env"
    cat >"$ETC/kore.env" <<'EOF'
# KORE keys. Root only (mode 600). Add each one with:  sudo kore-set-key NAME
# (it asks for the value without showing it). Needed: ANTHROPIC_API_KEY, ELEVENLABS_API_KEY.
# For help and the family: TELEGRAM_BOT_TOKEN, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER,
# KORE_HEARTBEAT_URL. Pins (not secret, optional): KORE_PIN_BUTTON, KORE_PIN_PRESENCE, KORE_LED_COUNT.
EOF
fi
chown root:root "$ETC/kore.env"
chmod 600 "$ETC/kore.env"

# --- uv ----------------------------------------------------------------------------------------------------------
say "uv $UV_VERSION"
if [ ! -x "$OPT/tools/bin/python" ]; then
    python3 -m venv "$OPT/tools"
fi
if [ "$("$OPT/tools/bin/uv" --version 2>/dev/null | awk '{print $2}')" != "$UV_VERSION" ]; then
    "$OPT/tools/bin/pip" install --quiet --disable-pip-version-check "uv==$UV_VERSION"
fi

# --- helper commands ---------------------------------------------------------------------------------------------
say "Commands: kore-update, kore-selftest, kore-set-key"
install -o root -g root -m 755 "$here/update.sh" "$SBIN/kore-update"

cat >"$SBIN/kore-selftest" <<'EOF'
#!/usr/bin/env bash
# The box checks itself, as Pepe runs (the kore user, with the keys loaded). Usage: sudo kore-selftest [options]
set -euo pipefail
exec systemd-run --quiet --wait --pipe --collect --service-type=exec --uid=kore --gid=kore \
    -p EnvironmentFile=/etc/kore/kore.env -p WorkingDirectory=/var/lib/kore \
    /opt/kore/current/.venv/bin/python -m kore.selftest --db /var/lib/kore/kore.db "$@"
EOF
chmod 755 "$SBIN/kore-selftest"

cat >"$SBIN/kore-set-key" <<'EOF'
#!/usr/bin/env bash
# Add or replace one key in /etc/kore/kore.env. The value is typed hidden and never shown or logged.
# Usage: sudo kore-set-key ANTHROPIC_API_KEY
set -euo pipefail
file=/etc/kore/kore.env
name="${1:-}"
if ! [[ "$name" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
    echo "usage: sudo kore-set-key NAME   (a name like ANTHROPIC_API_KEY)" >&2
    exit 2
fi
IFS= read -r -s -p "Paste the value for $name (it stays hidden), then press Enter: " value
echo
value="${value#"${value%%[![:space:]]*}"}"
value="${value%"${value##*[![:space:]]}"}"
if [ -z "$value" ]; then
    echo "Nothing typed: $name not changed." >&2
    exit 1
fi
case "$value" in
    *[\"\'\\]*) echo "That value has quotes or a backslash; check you copied only the key." >&2; exit 1 ;;
esac
tmp="$(mktemp "$file.XXXXXX")"
chmod 600 "$tmp"
grep -v "^$name=" "$file" >"$tmp" || true
printf '%s=%s\n' "$name" "$value" >>"$tmp"
mv -f "$tmp" "$file"
unset value
echo "$name saved. Restart Pepe to use it: sudo systemctl restart kore-home kore-web"
EOF
chmod 755 "$SBIN/kore-set-key"

# --- the system around Pepe --------------------------------------------------------------------------------------
say "Time zone, logs, watchdog, SPI"
timedatectl set-timezone Europe/Madrid || true
install -d -m 755 /etc/systemd/journald.conf.d /etc/systemd/system.conf.d /var/log/journal
cat >/etc/systemd/journald.conf.d/kore.conf <<'EOF'
# KORE: keep logs across power cuts, small, and no longer than transcripts (7 days).
[Journal]
Storage=persistent
SystemMaxUse=200M
MaxRetentionSec=7day
EOF
cat >/etc/systemd/system.conf.d/kore-watchdog.conf <<'EOF'
# KORE: the Pi's hardware watchdog restarts the box if the whole system freezes.
[Manager]
RuntimeWatchdogSec=15s
RebootWatchdogSec=2min
EOF
if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_spi 0  # 0 = on: the light ring is driven through SPI
fi
if [ "$rtc_charge" -eq 1 ]; then
    config=/boot/firmware/config.txt
    if [ -f "$config" ] && ! grep -q '^dtparam=rtc_bbat_vchg=' "$config"; then
        printf '\n# KORE: charge the official RTC battery\ndtparam=rtc_bbat_vchg=3000000\n' >>"$config"
    fi
fi
systemctl restart systemd-journald || true

# --- systemd units -----------------------------------------------------------------------------------------------
say "Services"
for unit in kore-home.service kore-web.service kore-update.service kore-update.timer; do
    install -o root -g root -m 644 "$here/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable kore-home.service kore-web.service kore-update.timer
systemctl start kore-update.timer

# --- the code ----------------------------------------------------------------------------------------------------
if [ -n "$tarball" ]; then
    say "Installing $(basename "$tarball")"
    install -o kore -g kore -m 640 "$tarball" "$DATA/incoming/"
    install -o kore -g kore -m 640 "$tarball.sha256" "$DATA/incoming/"
    "$SBIN/kore-update" --now || die "the code could not be installed (see the lines above)"
fi
[ -L "$OPT/current" ] || die "no version installed yet: run again with the kore-<version>.tar.gz file"

# --- start, or say what's missing --------------------------------------------------------------------------------
if grep -q '^ANTHROPIC_API_KEY=.' "$ETC/kore.env" && grep -q '^ELEVENLABS_API_KEY=.' "$ETC/kore.env"; then
    say "Starting Pepe"
    systemctl restart kore-home.service kore-web.service
    echo "Done. Check in a minute with: sudo kore-selftest"
else
    say "Almost done"
    echo "Type the keys (each one hidden), then start Pepe:"
    echo "  sudo kore-set-key ANTHROPIC_API_KEY"
    echo "  sudo kore-set-key ELEVENLABS_API_KEY"
    echo "  sudo systemctl start kore-home kore-web"
    echo "  sudo kore-selftest"
fi
echo "If SPI or the RTC setting was just changed, restart the box once: sudo reboot"
