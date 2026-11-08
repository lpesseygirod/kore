"""The nightly encrypted backup (BUILD-PLAN 0b.9): the database, off the device, every night.

1. A consistent copy of the database (SQLite's online backup, safe while Pepe runs).
2. Encrypted to Lea's public certificate with OpenSSL (CMS, AES-256-GCM). The device
   holds only the public half: it can make backups but can't read them. Lea keeps the
   private key on her own computer (see `python -m kore.family` for the steps).
3. Sent to Lea's own Telegram chat as a file, and the last 7 kept on the device.

OpenSSL is already on the Raspberry Pi and the Mac (Apache-2.0 licence): no new
dependency. The plain copy only ever exists in a private temporary folder, and is
removed as soon as it is encrypted.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kore.family import messages as msgs
from kore.memory.db import utc_iso
from kore.scheduler.clock import Clock

CERT_VAR = "KORE_BACKUP_CERT"          # path to Lea's public certificate (not secret)
DEFAULT_CERT = "private/backup_cert.pem"
OPENSSL_VAR = "KORE_OPENSSL"           # which openssl to use, if not the first on PATH
DIR = Path("data/backups")
KEEP = 7
AT = "03:30"                           # local time; reminders are quiet then
LAST = "backup_last_day"


class BackupError(Exception):
    pass


def openssl() -> str:
    exe = os.environ.get(OPENSSL_VAR) or shutil.which("openssl")
    if not exe:
        raise BackupError("openssl not found")
    return exe


def snapshot(conn: sqlite3.Connection, dest: Path) -> None:
    """A consistent copy of the live database."""
    out = sqlite3.connect(str(dest))
    try:
        conn.backup(out)
    finally:
        out.close()


# Not in the backup: the web conversations (each person's own; Lea's view never shows them, and "deleted after
# 90 days" must hold), and the web's codes, logins and attempts (a restored box makes everyone log in again).
NOT_BACKED_UP = ("chat", "chat_turns", "say_queue", "web_codes", "web_attempts", "web_logins")


def _leave_out_private(path: Path) -> None:
    c = sqlite3.connect(str(path))
    try:
        c.execute("PRAGMA secure_delete = ON")
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for t in NOT_BACKED_UP:
            if t in tables:
                c.execute(f"DELETE FROM {t}")
        c.commit()
        c.execute("VACUUM")  # rewrite the file: nothing deleted stays in free pages
    finally:
        c.close()


def encrypt(src: Path, cert: Path, dest: Path) -> None:
    r = subprocess.run([openssl(), "cms", "-encrypt", "-binary", "-aes-256-gcm", "-in", str(src), "-out", str(dest),
                        "-outform", "DER", "-recip", str(cert)], capture_output=True)
    if r.returncode != 0 or not dest.exists():
        raise BackupError("encryption failed: " + r.stderr.decode(errors="replace")[:200])


def decrypt(src: Path, key: Path, cert: Path, dest: Path) -> None:
    """For Lea, on her own computer: back to a database file."""
    # Not captured: OpenSSL asks Lea for her key's passphrase in the terminal.
    r = subprocess.run([openssl(), "cms", "-decrypt", "-binary", "-inform", "DER", "-in", str(src), "-out", str(dest),
                        "-inkey", str(key), "-recip", str(cert)])
    if r.returncode != 0:
        raise BackupError("decryption failed (wrong key or passphrase?)")


def make(conn: sqlite3.Connection, clock: Clock, cert: Path, folder: Path = DIR, keep: int = KEEP) -> Path:
    """Write one encrypted backup into `folder`; keep the newest `keep`. Returns its path."""
    if not cert.exists():
        raise BackupError(f"no certificate at {cert}")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / f"kore-{clock.now().strftime('%Y%m%d-%H%M')}.db.cms"
        with tempfile.TemporaryDirectory(prefix="kore-backup-") as tmp:  # private to this user, removed after
            plain = Path(tmp) / "kore.db"
            snapshot(conn, plain)
            _leave_out_private(plain)
            encrypt(plain, cert, out)
        for old in sorted(folder.glob("kore-*.db.cms"))[:-keep]:
            old.unlink()  # the device's own old backups
    except BackupError:
        raise
    except (OSError, sqlite3.Error, subprocess.SubprocessError) as e:  # a wrong openssl path, a full disk...
        raise BackupError(type(e).__name__) from None
    return out


SEND_RETRY = timedelta(minutes=30)


def nightly(conn: sqlite3.Connection, clock: Clock, family, cert: Path | None = None,
            folder: Path = DIR) -> Path | None:
    """Call every tick. Once a day after AT (or later that day, if the device was off): make the backup;
    then send it to Lea's chat, retrying every 30 minutes until it goes. Returns a file made now, else None.

    Failures go to Lea once a day (Telegram; the heartbeat stays the backstop).
    """
    now = clock.now()
    today = now.date().isoformat()
    if not clock.trusted() or now.strftime("%H:%M") < AT:
        return None
    made = None
    if msgs.get_state(conn, LAST) != today:
        cert = cert or Path(os.environ.get(CERT_VAR) or DEFAULT_CERT)
        try:
            made = make(conn, clock, cert, folder)
        except BackupError as e:
            _tell_lea_once(conn, clock, today, f"Pepe: la copia de seguridad de hoy ha fallado ({e}).")
            return None
        msgs.set_state(conn, LAST, today)
        msgs.set_state(conn, LAST_FILE, str(made))
    _send(conn, clock, family, today)
    return made


LAST_FILE = "backup_last_file"
LAST_SENT = "backup_last_sent"
LAST_TRY = "backup_last_try"


def _send(conn: sqlite3.Connection, clock: Clock, family, today: str) -> None:
    if msgs.get_state(conn, LAST_SENT) == today:
        return
    chat = msgs.get_state(conn, msgs.OPERATOR_CHAT)
    tg = family.channels.get("telegram") if family else None
    path = Path(msgs.get_state(conn, LAST_FILE) or "")
    if not chat or tg is None or not hasattr(tg, "send_document") or not path.is_file():
        return  # nowhere to send it yet: status shows it as overdue
    now = clock.now().astimezone(timezone.utc)
    last = msgs.get_state(conn, LAST_TRY)
    if last and now - datetime.fromisoformat(last) < SEND_RETRY and datetime.fromisoformat(last) <= now:
        return
    msgs.set_state(conn, LAST_TRY, utc_iso(now))
    try:
        ok = tg.send_document(chat, path.name, path.read_bytes(), "Copia cifrada de Pepe").ok
    except Exception:
        ok = False
    if ok:
        msgs.set_state(conn, LAST_SENT, today)


def _tell_lea_once(conn: sqlite3.Connection, clock: Clock, today: str, text: str) -> None:
    if msgs.get_state(conn, LAST + "_error") != today:
        msgs.set_state(conn, LAST + "_error", today)
        msgs.queue(conn, clock, "operator", "telegram", text)


def overdue(conn: sqlite3.Connection, clock: Clock) -> bool:
    """No backup sent off the device for two days: worth a note in status."""
    last = msgs.get_state(conn, LAST_SENT)
    return not last or last < (clock.now().date() - timedelta(days=2)).isoformat()
