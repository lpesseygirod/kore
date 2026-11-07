"""Messages to and from the family, kept in the database.

Out: every message is stored first, then sent; a power cut or a dead
connection only delays it. Each one is retried until it goes or expires.
Messages a person asked for are followed up out loud: "sent after all" or
"couldn't be sent" (BUILD-PLAN rule: if a message can't be sent, say so).

In: only messages from a family chat linked by pairing (a one-time code Lea
gives the family member) are kept; anything else reaching the bot is ignored.
They are read aloud only when asked, as quoted content.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from kore.family.channels import Channel, Sent
from kore.memory.db import utc_iso
from kore.scheduler.clock import Clock
from kore.scheduler.engine import Event

KINDS = {"help", "missed_meds", "stop_request", "message", "test", "operator"}
# How long each kind keeps being tried.
EXPIRES = {"help": timedelta(hours=2), "message": timedelta(hours=24), "missed_meds": timedelta(hours=24),
           "stop_request": timedelta(hours=24), "test": timedelta(minutes=10), "operator": timedelta(hours=24)}
# A message is given up only after this many real tries too: a clock that jumps forward after a power
# cut (the Pi without its clock battery) must not expire a call for help that was never really tried.
MIN_ATTEMPTS = {"help": 20, "message": 5, "missed_meds": 5, "stop_request": 5, "test": 1, "operator": 5}
HELP_RETRY = timedelta(seconds=30)
MAX_RETRY = timedelta(minutes=30)
PAIRING_VALID = timedelta(minutes=30)
PAIRING_TRIES_PER_CHAT = 3     # wrong codes from one chat before it is ignored
PAIRING_TRIES_IN_ALL = 20      # wrong codes overall before every open code is cancelled
MAX_IN_TEXT = 1000
PHONE = re.compile(r"^\+[1-9]\d{7,14}$")

OPERATOR_CHAT = "operator_chat"   # Lea's own Telegram chat: device messages and backups
TG_OFFSET = "telegram_offset"


@dataclass
class Attempt:
    id: int
    contact: str | None   # contact name, None for Lea
    channel: str
    ok: bool
    error: str | None


# --- out ---------------------------------------------------------------------------


def queue(conn: sqlite3.Connection, clock: Clock, kind: str, channel: str, text: str,
          contact_id: int | None = None, from_person: int | None = None, group_id: int | None = None) -> int:
    """Store a message to send. Messages of one request share a group_id (default: their own id)."""
    if kind not in KINDS:
        raise ValueError(kind)
    now = _utc(clock.now())
    cur = conn.execute(
        "INSERT INTO family_out (created_at_utc, kind, contact_id, channel, text, from_person, status, "
        "next_try_utc, expires_at_utc, group_id) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
        (utc_iso(now), kind, contact_id, channel, text, from_person, utc_iso(now), utc_iso(now + EXPIRES[kind]),
         group_id))
    if group_id is None:
        conn.execute("UPDATE family_out SET group_id = id WHERE id = ?", (cur.lastrowid,))
    if kind in ("help", "message"):
        # Someone is waiting to hear how it went. Until the caller says what they heard, assume
        # "not yet": a power cut mid-send then still ends in "it went" or "it couldn't go".
        conn.execute("UPDATE family_out SET told = 1 WHERE id = ?", (cur.lastrowid,))
    return cur.lastrowid


def told(conn: sqlite3.Connection, group_id: int, settled: bool) -> None:
    """What the person heard straight away: settled (it went) or "not yet, I'll keep trying"."""
    conn.execute("UPDATE family_out SET told = ? WHERE group_id = ?", (2 if settled else 1, group_id))


def address(conn: sqlite3.Connection, contact_id: int | None, channel: str) -> str | None:
    if contact_id is None:
        return get_state(conn, OPERATOR_CHAT) if channel == "telegram" else None
    row = conn.execute("SELECT telegram_chat_id, phone FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if row is None:
        return None
    return row["telegram_chat_id"] if channel == "telegram" else row["phone"]


def deliver(conn: sqlite3.Connection, clock: Clock, channels: dict[str, Channel],
            ids: list[int] | None = None) -> list[Attempt]:
    """Try every pending message that is due (or just `ids`, now). Expired ones are marked failed."""
    now = _utc(clock.now())
    if clock.trusted():  # never give up on anything by a clock in doubt
        for r in conn.execute("SELECT id, kind, attempts FROM family_out WHERE status = 'pending' "
                              "AND expires_at_utc <= ?", (utc_iso(now),)).fetchall():
            if r["attempts"] >= MIN_ATTEMPTS[r["kind"]]:
                conn.execute("UPDATE family_out SET status = 'failed', error = coalesce(error, 'expired') "
                             "WHERE id = ?", (r["id"],))
    if ids is not None:
        rows = conn.execute("SELECT * FROM family_out WHERE status = 'pending' AND id IN (%s) ORDER BY id"
                            % ",".join("?" * len(ids)), ids).fetchall() if ids else []
    else:
        # Due now, or "due" further ahead than any wait we set: the clock jumped back.
        rows = conn.execute("SELECT * FROM family_out WHERE status = 'pending' AND (next_try_utc <= ? OR "
                            "next_try_utc > ?) ORDER BY kind != 'help', id",
                            (utc_iso(now), utc_iso(now + MAX_RETRY))).fetchall()
    out = []
    for r in rows:
        ch = channels.get(r["channel"])
        to = address(conn, r["contact_id"], r["channel"])
        if to is None:
            result = Sent(False, "no_address", permanent=True)
        elif ch is None:
            result = Sent(False, "not_configured")  # Lea may still set it up: keep it until it expires
        else:
            try:
                result = ch.send(to, r["text"])
            except Exception:  # one broken channel must never stop the other (help goes by both)
                result = Sent(False, "channel_error")
        attempts = r["attempts"] + 1
        if result.ok:
            conn.execute("UPDATE family_out SET status = 'sent', attempts = ?, sent_at_utc = ?, error = NULL "
                         "WHERE id = ?", (attempts, utc_iso(now), r["id"]))
        elif result.permanent:
            conn.execute("UPDATE family_out SET status = 'failed', attempts = ?, error = ? WHERE id = ?",
                         (attempts, result.error, r["id"]))
        else:
            wait = HELP_RETRY if r["kind"] == "help" else min(MAX_RETRY, timedelta(minutes=2 ** min(attempts, 5)))
            conn.execute("UPDATE family_out SET attempts = ?, error = ?, next_try_utc = ? WHERE id = ?",
                         (attempts, result.error, utc_iso(now + wait), r["id"]))
        out.append(Attempt(r["id"], contact_name(conn, r["contact_id"]), r["channel"], result.ok, result.error))
    return out


def follow_ups(conn: sqlite3.Connection, clock: Clock, everyone: list[int]) -> list[Event]:
    """Tell people how a message they asked for (or a call for help) went, after they heard "not yet".

    One event per request: "it went after all" once any part is sent, or "it couldn't be sent" once
    every part has failed for good. Family alerts that fail for good become a note to Lea.
    """
    events = []
    for g in conn.execute("SELECT DISTINCT group_id FROM family_out WHERE told = 1").fetchall():
        rows = conn.execute("SELECT * FROM family_out WHERE group_id = ?", (g["group_id"],)).fetchall()
        kind, person = rows[0]["kind"], rows[0]["from_person"]
        sent = sorted({contact_name(conn, r["contact_id"]) or "" for r in rows if r["status"] == "sent"} - {""})
        if any(r["status"] == "sent" for r in rows):
            ev = f"{kind}_sent_late"
        elif all(r["status"] == "failed" for r in rows):
            ev = f"{kind}_not_sent"
        else:
            continue
        to = sent or sorted({contact_name(conn, r["contact_id"]) or "" for r in rows} - {""})
        events.append(Event(ev, 1, [person] if person is not None else everyone, {"to": to},
                            private=kind == "message"))
        told(conn, g["group_id"], settled=True)
    for r in conn.execute("SELECT * FROM family_out WHERE kind IN ('missed_meds', 'stop_request') "
                          "AND status = 'failed' AND told = 0").fetchall():
        conn.execute("UPDATE family_out SET told = 2 WHERE id = ?", (r["id"],))
        queue(conn, clock, "operator", "telegram",
              f"Pepe: un aviso a la familia no se ha podido enviar ({r['error']}). Revisa los contactos "
              "con: uv run python -m kore.family status")
    return events


def contact_name(conn: sqlite3.Connection, contact_id: int | None) -> str | None:
    if contact_id is None:
        return None
    row = conn.execute("SELECT name FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    return row["name"] if row else None


def help_contacts(conn: sqlite3.Connection, person_ids: list[int]) -> list[sqlite3.Row]:
    """The people to contact if they need help: theirs, and the home's shared ones."""
    if not person_ids:
        return conn.execute("SELECT * FROM contacts WHERE help_contact = 1 AND for_person IS NULL "
                            "ORDER BY id").fetchall()
    return conn.execute(
        "SELECT * FROM contacts WHERE help_contact = 1 AND (for_person IS NULL OR for_person IN (%s)) ORDER BY id"
        % ",".join("?" * len(person_ids)), person_ids).fetchall()


# --- in ----------------------------------------------------------------------------


def poll(conn: sqlite3.Connection, clock: Clock, channel) -> dict:
    """Fetch new messages from the bot. Pairing codes link a chat; linked chats' messages are kept."""
    offset = get_state(conn, TG_OFFSET)
    updates, reached = channel.poll(int(offset) if offset else None)
    stats = {"reached": reached, "kept": 0, "linked": 0, "ignored": 0}
    for u in updates:
        if _pair(conn, clock, u.chat_id, u.text or ""):
            stats["linked"] += 1
        else:
            row = conn.execute("SELECT id FROM contacts WHERE telegram_chat_id = ?", (u.chat_id,)).fetchone()
            if row is None:
                stats["ignored"] += 1  # a stranger, or Lea's own chat: never read aloud
            else:
                text = (u.text or "").strip()[:MAX_IN_TEXT] or "[una foto o un audio, que no puedo mostrar]"
                conn.execute("INSERT INTO family_in (received_at_utc, contact_id, text) VALUES (?, ?, ?)",
                             (utc_iso(clock.now()), row["id"], text))
                stats["kept"] += 1
        set_state(conn, TG_OFFSET, str(u.update_id + 1))
    return stats


def unread(conn: sqlite3.Connection, person_id: int | None) -> list[sqlite3.Row]:
    """Unread messages for this person: from their own contacts and the home's shared ones.
    With nobody known (two at home), only the shared ones."""
    sql = ("SELECT family_in.*, contacts.name, contacts.for_person FROM family_in JOIN contacts "
           "ON contacts.id = family_in.contact_id WHERE read_at_utc IS NULL AND ")
    if person_id is None:
        return conn.execute(sql + "contacts.for_person IS NULL ORDER BY family_in.id").fetchall()
    return conn.execute(sql + "(contacts.for_person IS NULL OR contacts.for_person = ?) ORDER BY family_in.id",
                        (person_id,)).fetchall()


def unread_for_others(conn: sqlite3.Connection, person_id: int | None) -> dict[int, int]:
    """{person_id: count} of unread messages that belong to someone else at home (not shown, only counted)."""
    rows = conn.execute("SELECT contacts.for_person AS p, count(*) AS n FROM family_in JOIN contacts ON "
                        "contacts.id = family_in.contact_id WHERE read_at_utc IS NULL AND contacts.for_person "
                        "IS NOT NULL AND contacts.for_person IS NOT ? GROUP BY contacts.for_person",
                        (person_id,)).fetchall()
    return {r["p"]: r["n"] for r in rows}


def recent(conn: sqlite3.Connection, clock: Clock, person_id: int | None, days: int = 7,
           limit: int = 5) -> list[sqlite3.Row]:
    """Already read ones, for 'what did Lucía say yesterday?'."""
    since = utc_iso(_utc(clock.now()) - timedelta(days=days))
    who = "contacts.for_person IS NULL" if person_id is None else "(contacts.for_person IS NULL OR " \
                                                                  "contacts.for_person = ?)"
    args = [since] + ([] if person_id is None else [person_id])
    return conn.execute(
        "SELECT family_in.*, contacts.name FROM family_in JOIN contacts ON contacts.id = family_in.contact_id "
        f"WHERE read_at_utc IS NOT NULL AND received_at_utc >= ? AND {who} ORDER BY family_in.id DESC LIMIT {limit}",
        args).fetchall()[::-1]


def mark_read(conn: sqlite3.Connection, clock: Clock, ids: list[int]) -> None:
    for i in ids:
        conn.execute("UPDATE family_in SET read_at_utc = ? WHERE id = ? AND read_at_utc IS NULL",
                     (utc_iso(clock.now()), i))


# --- linking a family chat or a phone (Lea, from the command line) ------------------------


def start_pairing(conn: sqlite3.Connection, clock: Clock, contact_id: int | None) -> str:
    """A one-time 6-digit code. The family member sends it to the bot; their chat is then linked.
    contact_id None links Lea's own chat (device messages and backups)."""
    if contact_id is not None and contact_name(conn, contact_id) is None:
        raise ValueError(f"no contact {contact_id}")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    expires = _utc(clock.now()) + PAIRING_VALID
    set_state(conn, f"pair:{code}", json.dumps({"contact_id": contact_id, "expires": utc_iso(expires)}))
    return code


def _pair(conn: sqlite3.Connection, clock: Clock, chat_id: str, text: str) -> bool:
    m = re.fullmatch(r"\s*(?:/start\s+)?(\d{6})\s*", text)
    if not m:
        return False
    tries_key = f"pair_wrong:{chat_id}"
    if int(get_state(conn, tries_key) or 0) >= PAIRING_TRIES_PER_CHAT:
        return False  # this chat guessed too often: ignored
    raw = get_state(conn, f"pair:{m.group(1)}")
    if raw is None:
        set_state(conn, tries_key, str(int(get_state(conn, tries_key) or 0) + 1))
        wrong = int(get_state(conn, "pair_wrong_all") or 0) + 1
        set_state(conn, "pair_wrong_all", str(wrong))
        if wrong >= PAIRING_TRIES_IN_ALL:  # someone is guessing: cancel every open code
            conn.execute("DELETE FROM family_state WHERE key LIKE 'pair:%'")
            set_state(conn, "pair_wrong_all", "0")
        return False
    conn.execute("DELETE FROM family_state WHERE key = ?", (f"pair:{m.group(1)}",))  # one use only
    info = json.loads(raw)
    if info["expires"] <= utc_iso(clock.now()):
        return False
    if info["contact_id"] is None:
        set_state(conn, OPERATOR_CHAT, chat_id)
    else:
        # One chat, one contact: a chat linked before is unlinked from the old one.
        conn.execute("UPDATE contacts SET telegram_chat_id = NULL WHERE telegram_chat_id = ?", (chat_id,))
        conn.execute("UPDATE contacts SET telegram_chat_id = ? WHERE id = ?", (chat_id, info["contact_id"]))
    return True


def set_phone(conn: sqlite3.Connection, contact_id: int, phone: str | None) -> None:
    if phone is not None and not PHONE.match(phone):
        raise ValueError("phone must be in international form, e.g. +34600111222")
    if contact_name(conn, contact_id) is None:
        raise ValueError(f"no contact {contact_id}")
    conn.execute("UPDATE contacts SET phone = ? WHERE id = ?", (phone, contact_id))


# --- small values ---------------------------------------------------------------------


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM family_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO family_state (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET "
                 "value = excluded.value", (key, value))


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)
