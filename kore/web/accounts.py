"""Who can log in to the home web page, and how (WEB-PLAN.md section 4).

Logging in needs two things: the page (from the QR code on the box) and a 6-digit code that
Pepe says out loud in the home. Hearing it proves you're there. A code:
- belongs to the browser that asked for it (a ticket cookie), and to one person;
- lasts 10 minutes; 3 wrong tries burn it (every open code the guess was checked against);
- is only spoken when Pepe may speak: not at night, not in quiet mode or rest hours, not with
  visitors, and not when the clock can't be trusted (the night check would mean nothing).

Limits, so nobody on the Wi-Fi can lock the family out or make Pepe talk on and on:
- per device (its address): 3 codes an hour, and 10 wrong tries in 30 minutes lock that device
  for 15 minutes;
- for the whole home: 10 codes an hour, and 50 wrong tries in 30 minutes stop new codes for
  15 minutes and tell Lea. Lea's printed code still works during that pause (it is never
  cancelled by it), and `python -m kore.web unlock` ends it.

After that the phone stays logged in: a random cookie, of which only a hash is kept. Each
login can be closed from the page, by voice (later), or by Lea.

The spoken line holds the code in words, so it is deleted as soon as the code is used, burned,
cancelled or expired (and the voice loop deletes it once said).

Lea's operator code is printed on the box's terminal, never spoken (`python -m kore.web invite`).

Family (W4): a person at home gives a family member access from their own page, with a read-back and a
yes; the code then shows on that person's screen (30 minutes) to pass on, and works in any browser
(like Lea's). Wrong guesses count against it like any other code. During a home-wide pause only Lea's
code works. A family login sees nothing private (web/family.py).

By design (v1, no voice recognition): anyone in the home who hears the code can log in as that
person. Each person's Dispositivos page shows every device logged in with their name.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from kore.family import messages as family_msgs
from kore.info import store as info
from kore.manners import modes
from kore.memory import settings
from kore.memory.db import from_utc_iso, utc_iso
from kore.scheduler.clock import Clock

CODE_LIFE = timedelta(minutes=10)
FAMILY_CODE_LIFE = timedelta(minutes=30)  # passed on by phone or in person
TRIES_PER_CODE = 3
SAME_PERSON_AGAIN = timedelta(minutes=1)    # asking again this soon doesn't make Pepe say a new code
CODES_PER_CLIENT_HOUR = 3
CODES_PER_HOME_HOUR = 10
WINDOW = timedelta(minutes=30)
WRONG_PER_CLIENT = 10
WRONG_PER_HOME = 50
LOCKOUT = timedelta(minutes=15)
LOGIN_IDLE = timedelta(days=180)             # a phone unused for six months is logged out
SEEN_EVERY = timedelta(minutes=10)
LOCK_KEY = "web_locked_until"

DIGITS = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]
TOO_MANY = "Ha habido demasiados intentos. Espere un cuarto de hora y vuelva a probar."


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass
class Account:
    id: int
    role: str
    person_id: int | None
    name: str
    login_id: int
    contact_id: int | None = None  # role family


class Refused(Exception):
    """Why a code can't be made or used, in Spanish, for the page."""


# --- limits ----------------------------------------------------------------------------------


def _count(conn: sqlite3.Connection, clock: Clock, kind: str, since: timedelta, client: str | None = None) -> int:
    sql = "SELECT count(*) FROM web_attempts WHERE kind = ? AND at_utc > ? AND at_utc <= ?"
    now = clock.now()
    args: list = [kind, utc_iso(now - since), utc_iso(now)]
    if client is not None:
        sql += " AND client = ?"
        args.append(digest(client))
    return conn.execute(sql, args).fetchone()[0]


def _note(conn: sqlite3.Connection, clock: Clock, kind: str, client: str) -> None:
    now = clock.now()
    conn.execute("INSERT INTO web_attempts (kind, client, at_utc) VALUES (?, ?, ?)", (kind, digest(client), utc_iso(now)))
    conn.execute("DELETE FROM web_attempts WHERE at_utc < ?", (utc_iso(now - timedelta(days=1)),))


def _home_locked(conn: sqlite3.Connection, clock: Clock) -> bool:
    got = info.get(conn, LOCK_KEY, clock.tz)
    return bool(got) and clock.now() < datetime.fromisoformat(got[0])


def _client_locked(conn: sqlite3.Connection, clock: Clock, client: str) -> bool:
    return _count(conn, clock, "wrong", WINDOW, client) >= WRONG_PER_CLIENT


def unlock(conn: sqlite3.Connection, clock: Clock) -> None:
    """Lea, from the box: end a home-wide pause now."""
    info.put(conn, LOCK_KEY, clock.now().isoformat(), clock.now())
    conn.execute("DELETE FROM web_attempts WHERE kind = 'wrong'")


def _purge(conn: sqlite3.Connection, clock: Clock) -> None:
    """Spoken lines hold codes in words: keep them only while their code can still be used."""
    now = utc_iso(clock.now())
    conn.execute("UPDATE web_codes SET status = 'cancelled' WHERE status = 'open' AND expires_at <= ?", (now,))
    conn.execute("DELETE FROM say_queue WHERE kind = 'web_code' AND (said_at IS NOT NULL OR code_id IS NULL OR "
                 "code_id IN (SELECT id FROM web_codes WHERE status != 'open'))")


# --- codes ---------------------------------------------------------------------------------


def _why_pepe_cant_speak(conn: sqlite3.Connection, clock: Clock, person_id: int) -> str | None:
    if not clock.trusted():
        return "La hora de Pepe no es fiable ahora mismo, así que no dice códigos. Pruebe más tarde."
    t = clock.now().strftime("%H:%M")
    for p in (settings.HOME, person_id):
        if settings.within(t, settings.get(conn, "night_start", p, clock), settings.get(conn, "night_end", p, clock)):
            return "De noche Pepe no dice códigos, para no despertar a nadie. Pruebe por la mañana."
    rest = settings.get(conn, "rest_hours", settings.HOME, clock)
    if rest and settings.within(t, *rest.split("-")):
        return "Es la hora de descanso: Pepe no dice códigos ahora. Pruebe más tarde."
    if modes.active(conn, clock, "visitors"):
        return "Hay visitas en casa: Pepe no dice códigos ahora. Pruebe cuando se hayan ido."
    if modes.active(conn, clock, "quiet") or modes.active(conn, clock, "quiet", person_id):
        return "Pepe está en silencio. Dígale «Pepe, ya puedes hablar» y vuelva a probar."
    return None


def request_code(conn: sqlite3.Connection, clock: Clock, person_id: int, ticket: str, client: str) -> str:
    """A person at home wants to log in from this browser. Pepe will say the code. Returns what the page says."""
    now = clock.now()
    _purge(conn, clock)
    person = conn.execute("SELECT name, call_name FROM people WHERE id = ?", (person_id,)).fetchone()
    if person is None:
        raise Refused("No encuentro a esa persona.")
    if _home_locked(conn, clock) or _client_locked(conn, clock, client):
        raise Refused(TOO_MANY)
    why = _why_pepe_cant_speak(conn, clock, person_id)
    if why:
        raise Refused(why)
    last = conn.execute("SELECT * FROM web_codes WHERE status = 'open' AND person_id = ? AND ticket_hash = ? "
                        "ORDER BY id DESC LIMIT 1", (person_id, digest(ticket))).fetchone()
    if last and timedelta(0) <= now - from_utc_iso(last["created_at"], clock.tz) < SAME_PERSON_AGAIN:
        return "Pepe acaba de decir el código. Escríbalo aquí."
    if (_count(conn, clock, "code", timedelta(hours=1), client) >= CODES_PER_CLIENT_HOUR
            or _count(conn, clock, "code", timedelta(hours=1)) >= CODES_PER_HOME_HOUR):
        raise Refused("Se han pedido muchos códigos. Pruebe dentro de un rato.")
    _note(conn, clock, "code", client)
    conn.execute("UPDATE web_codes SET status = 'cancelled' WHERE status = 'open' AND ticket_hash = ?",
                 (digest(ticket),))  # one open code per browser
    code = f"{secrets.randbelow(10**6):06d}"
    code_id = conn.execute("INSERT INTO web_codes (role, person_id, code_hash, ticket_hash, created_at, expires_at) "
                           "VALUES ('person', ?, ?, ?, ?, ?)",
                           (person_id, digest(code), digest(ticket), utc_iso(now), utc_iso(now + CODE_LIFE))).lastrowid
    spoken = ", ".join(DIGITS[int(c)] for c in code)
    conn.execute("INSERT INTO say_queue (kind, text, created_at, code_id) VALUES ('web_code', ?, ?, ?)",
                 (f"{person['call_name'] or person['name']}, para entrar en la página de Pepe, el código es: "
                  f"{spoken}. Repito: {spoken}.", utc_iso(now), code_id))
    _purge(conn, clock)
    return "Pepe está diciendo un código de seis números. Escríbalo aquí."


def operator_code(conn: sqlite3.Connection, clock: Clock) -> str:
    """For Lea, from the box's terminal: any browser, 10 minutes. Returns the code (printed, never spoken)."""
    now = clock.now()
    code = f"{secrets.randbelow(10**6):06d}"
    conn.execute("INSERT INTO web_codes (role, person_id, code_hash, ticket_hash, created_at, expires_at) "
                 "VALUES ('operator', NULL, ?, NULL, ?, ?)", (digest(code), utc_iso(now), utc_iso(now + CODE_LIFE)))
    return code


def family_code(conn: sqlite3.Connection, clock: Clock, contact_id: int) -> str:
    """After the person's yes: a code for this family member, shown on the person's screen (never spoken)."""
    now = clock.now()
    conn.execute("UPDATE web_codes SET status = 'cancelled' WHERE role = 'family' AND contact_id = ? AND status = 'open'",
                 (contact_id,))
    code = f"{secrets.randbelow(10**6):06d}"
    conn.execute("INSERT INTO web_codes (role, person_id, contact_id, code_hash, ticket_hash, created_at, expires_at) "
                 "VALUES ('family', NULL, ?, ?, NULL, ?, ?)",
                 (contact_id, digest(code), utc_iso(now), utc_iso(now + FAMILY_CODE_LIFE)))
    return code


def _wrong(conn: sqlite3.Connection, clock: Clock, client: str) -> None:
    _note(conn, clock, "wrong", client)
    if _count(conn, clock, "wrong", WINDOW) >= WRONG_PER_HOME and not _home_locked(conn, clock):
        now = clock.now()
        conn.execute("UPDATE web_codes SET status = 'cancelled' WHERE status = 'open' AND role != 'operator'")
        info.put(conn, LOCK_KEY, (now + LOCKOUT).isoformat(), now)
        family_msgs.queue(conn, clock, "operator", "telegram",
                          "Pepe: muchos intentos fallidos de entrar en la página web. Las entradas nuevas quedan en "
                          "pausa 15 minutos. Si no ha sido nadie de casa, revisa quién usa su Wi-Fi. "
                          "Para quitar la pausa: python -m kore.web unlock")
    _purge(conn, clock)


def use_code(conn: sqlite3.Connection, clock: Clock, code: str, ticket: str | None, device: str, client: str) -> str:
    """Log in with a code. Returns the new login's cookie token. Raises Refused."""
    now = clock.now()
    _purge(conn, clock)
    if _client_locked(conn, clock, client):
        raise Refused(TOO_MANY)
    home_locked = _home_locked(conn, clock)
    code = "".join(c for c in (code or "") if c in "0123456789")
    # During a home-wide pause only Lea's printed code works (so she can always get in to see what's going on).
    rows = conn.execute("SELECT * FROM web_codes WHERE status = 'open' AND expires_at > ? AND "
                        "((ticket_hash IS NULL AND role = 'operator') OR (ticket_hash IS NULL AND role = 'family' AND ?) "
                        "OR (ticket_hash = ? AND ?)) ORDER BY id DESC",
                        (utc_iso(now), int(not home_locked), digest(ticket) if ticket else "",
                         int(not home_locked))).fetchall()
    match = None
    for r in rows:  # compare with every candidate, in constant time
        if hmac.compare_digest(r["code_hash"], digest(code)) and len(code) == 6:
            match = match or r
    if match is None:
        for r in rows:  # the guess was checked against each of these: each one counts it
            if r["role"] == "operator":
                continue  # Lea's code: guarded by the per-device and home limits, never burned by strangers
            conn.execute("UPDATE web_codes SET tries = tries + 1, status = CASE WHEN tries + 1 >= ? THEN 'burned' "
                         "ELSE status END WHERE id = ? AND status = 'open'", (TRIES_PER_CODE, r["id"]))
        _wrong(conn, clock, client)
        if home_locked:
            raise Refused(TOO_MANY)
        burned = any(conn.execute("SELECT status FROM web_codes WHERE id = ?", (r["id"],)).fetchone()[0] == "burned"
                     for r in rows if r["ticket_hash"] is not None)  # (a family code burned by others: not "yours")
        if burned:
            raise Refused("Ese código ya no vale. Pida uno nuevo.")
        raise Refused("El código no es correcto. Escuche a Pepe y pruebe otra vez.")
    if conn.execute("UPDATE web_codes SET status = 'used' WHERE id = ? AND status = 'open'",
                    (match["id"],)).rowcount != 1:
        raise Refused("Ese código ya no vale. Pida uno nuevo.")
    account = _account_for(conn, clock, match["role"], match["person_id"], match["contact_id"])
    token = new_token()
    conn.execute("INSERT INTO web_logins (account_id, token_hash, device, created_at, last_seen_at) "
                 "VALUES (?, ?, ?, ?, ?)", (account, digest(token), device[:60], utc_iso(now), utc_iso(now)))
    _purge(conn, clock)
    return token


def _account_for(conn: sqlite3.Connection, clock: Clock, role: str, person_id: int | None,
                 contact_id: int | None = None) -> int:
    if role == "family":
        row = conn.execute("SELECT id FROM web_accounts WHERE role = 'family' AND contact_id = ?",
                           (contact_id,)).fetchone()
        if row:
            return row["id"]
        name = conn.execute("SELECT name FROM contacts WHERE id = ?", (contact_id,)).fetchone()[0]
        return conn.execute("INSERT INTO web_accounts (role, contact_id, name, created_at) VALUES ('family', ?, ?, ?)",
                            (contact_id, name, utc_iso(clock.now()))).lastrowid
    if role == "person":
        row = conn.execute("SELECT id FROM web_accounts WHERE role = 'person' AND person_id = ?", (person_id,)).fetchone()
        if row:
            return row["id"]
        name = conn.execute("SELECT coalesce(call_name, name) FROM people WHERE id = ?", (person_id,)).fetchone()[0]
        return conn.execute("INSERT INTO web_accounts (role, person_id, name, created_at) VALUES ('person', ?, ?, ?)",
                            (person_id, name, utc_iso(clock.now()))).lastrowid
    row = conn.execute("SELECT id FROM web_accounts WHERE role = 'operator'").fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO web_accounts (role, name, created_at) VALUES ('operator', 'Lea', ?)",
                        (utc_iso(clock.now()),)).lastrowid


# --- logged in -------------------------------------------------------------------------------


def who(conn: sqlite3.Connection, clock: Clock, token: str | None) -> Account | None:
    if not token:
        return None
    row = conn.execute("SELECT l.id AS login_id, l.last_seen_at, a.* FROM web_logins l JOIN web_accounts a "
                       "ON a.id = l.account_id WHERE l.token_hash = ? AND l.revoked_at IS NULL",
                       (digest(token),)).fetchone()
    if row is None:
        return None
    now = clock.now()
    seen = from_utc_iso(row["last_seen_at"], clock.tz)
    if now - seen > LOGIN_IDLE:
        conn.execute("UPDATE web_logins SET revoked_at = ? WHERE id = ?", (utc_iso(now), row["login_id"]))
        return None
    if row["role"] == "person" and conn.execute("SELECT 1 FROM people WHERE id = ?",
                                                (row["person_id"],)).fetchone() is None:
        return None
    if row["role"] == "family" and conn.execute("SELECT 1 FROM contacts WHERE id = ?",
                                                (row["contact_id"],)).fetchone() is None:
        return None
    if now - seen > SEEN_EVERY or seen > now:
        conn.execute("UPDATE web_logins SET last_seen_at = ? WHERE id = ?", (utc_iso(now), row["login_id"]))
    return Account(row["id"], row["role"], row["person_id"], row["name"], row["login_id"], row["contact_id"])


def logins(conn: sqlite3.Connection, clock: Clock, account: Account) -> list[dict]:
    """Your own logged-in devices; Lea sees everyone's."""
    sql = ("SELECT l.*, a.name, a.role FROM web_logins l JOIN web_accounts a ON a.id = l.account_id "
           "WHERE l.revoked_at IS NULL")
    args: tuple = ()
    if account.role != "operator":
        sql += " AND l.account_id = ?"
        args = (account.id,)
    out = []
    for r in conn.execute(sql + " ORDER BY l.last_seen_at DESC", args):
        out.append({"id": r["id"], "who": r["name"], "device": r["device"], "this_one": r["id"] == account.login_id,
                    "since": from_utc_iso(r["created_at"], clock.tz), "seen": from_utc_iso(r["last_seen_at"], clock.tz)})
    return out


def close(conn: sqlite3.Connection, clock: Clock, account: Account, login_id: int) -> bool:
    """Log out one device: your own, or any if you're Lea."""
    row = conn.execute("SELECT account_id FROM web_logins WHERE id = ? AND revoked_at IS NULL", (login_id,)).fetchone()
    if row is None or (account.role != "operator" and row["account_id"] != account.id):
        return False
    conn.execute("UPDATE web_logins SET revoked_at = ? WHERE id = ?", (utc_iso(clock.now()), login_id))
    return True


def close_all(conn: sqlite3.Connection, clock: Clock) -> int:
    """Every device logged out (Lea, or later by voice: "Pepe, cierra las sesiones de la web")."""
    return conn.execute("UPDATE web_logins SET revoked_at = ? WHERE revoked_at IS NULL",
                        (utc_iso(clock.now()),)).rowcount


def device_label(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    for key, name in (("iphone", "iPhone"), ("ipad", "iPad"), ("android", "Android"), ("macintosh", "Mac"),
                      ("windows", "Windows"), ("linux", "Linux")):
        if key in ua:
            return name
    return "Otro dispositivo"
