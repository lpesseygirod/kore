"""Everything else Pepe remembers: lists, places, notes about the person,
appointments, family birthdays, voice-made reminders, consents.

Plain storage. Who may read or change what is checked in brain/tools.py.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from kore.brain import readback
from kore.brain.language import fold
from kore.memory.db import from_utc_iso, utc_iso
from kore.scheduler.clock import Clock

SHOPPING = "shopping"

# --- consents --------------------------------------------------------------

# Kinds asked at setup, each separately (PEPE.md 2b).
CONSENTS = {
    "health_info",            # keep medicines and appointments
    "family_missed_meds",     # tell family after repeated missed doses
    "emergency_path",         # "Pepe, help" / the button message family without asking
    "share_with_partner",     # the other person may hear my medication status
    "water_nudges",           # category 2
    "movement_nudges",        # category 2
    "conversation_starters",  # category 2
    "listening",              # listens after the wake word or the button (told and agreed at setup)
    "presence_sensor",        # notices when they're up or moving
    "keep_conversations",     # keep what we say by voice, to read on the web page (90 days)
    "lea_sees_day",           # Lea's console: reminders, medicines, notes, feedback (to look after Pepe)
    "lea_reads_conversations",  # Lea's console: the last 7 days of conversations (to fix problems)
    "family_sees_doses",      # the family app: whether today's medicine was taken
}


def set_consent(conn: sqlite3.Connection, clock: Clock, person_id: int, kind: str, granted: bool) -> None:
    if kind not in CONSENTS:
        raise ValueError(kind)
    conn.execute(
        "INSERT INTO consents (person_id, kind, granted, at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (person_id, kind) DO UPDATE SET granted = excluded.granted, at = excluded.at",
        (person_id, kind, int(granted), utc_iso(clock.now())),
    )
    if kind == "keep_conversations" and not granted:  # a no, now or later: what was kept by voice goes
        conn.execute("DELETE FROM chat WHERE person_id = ? AND channel = 'voice'", (person_id,))
    if kind == "lea_reads_conversations" and not granted:  # Lea's notes quoting them go too
        conn.execute("DELETE FROM console_marks WHERE person_id = ?", (person_id,))


def declined(conn: sqlite3.Connection, person_id: int, kind: str) -> bool:
    """They were asked and said no (not just: never asked)."""
    row = conn.execute("SELECT granted FROM consents WHERE person_id = ? AND kind = ?", (person_id, kind)).fetchone()
    return bool(row and not row["granted"])


def sensor_allowed(conn: sqlite3.Connection) -> bool:
    """One presence sensor for the room: used only if nobody at home said no to it."""
    return conn.execute("SELECT 1 FROM consents WHERE kind = 'presence_sensor' AND granted = 0").fetchone() is None


def listening_declined(conn: sqlite3.Connection) -> bool:
    """Someone said no to listening after the wake word: the voice loop (phase 1) must then use the button only."""
    return conn.execute("SELECT 1 FROM consents WHERE kind = 'listening' AND granted = 0").fetchone() is not None


def has_consent(conn: sqlite3.Connection, person_id: int, kind: str) -> bool:
    row = conn.execute("SELECT granted FROM consents WHERE person_id = ? AND kind = ?", (person_id, kind)).fetchone()
    return bool(row and row["granted"])  # default: no


# --- lists -----------------------------------------------------------------


def list_items(conn: sqlite3.Connection, list_name: str = SHOPPING) -> list[str]:
    rows = conn.execute(
        "SELECT item FROM list_items WHERE list_name = ? AND status = 'open' ORDER BY id", (list_name,)
    ).fetchall()
    return [r["item"] for r in rows]


def add_to_list(conn: sqlite3.Connection, clock: Clock, items: list[str], list_name: str = SHOPPING) -> list[str]:
    existing = {fold(i) for i in list_items(conn, list_name)}
    for item in items:
        item = item.strip()
        if item and fold(item) not in existing:
            conn.execute(
                "INSERT INTO list_items (list_name, item, added_at) VALUES (?, ?, ?)",
                (list_name, item, utc_iso(clock.now())),
            )
            existing.add(fold(item))
    return list_items(conn, list_name)


def remove_from_list(conn: sqlite3.Connection, items: list[str], list_name: str = SHOPPING) -> list[str]:
    targets = {fold(i) for i in items}
    for row in conn.execute(
        "SELECT id, item FROM list_items WHERE list_name = ? AND status = 'open'", (list_name,)
    ).fetchall():
        if fold(row["item"]) in targets:
            conn.execute("UPDATE list_items SET status = 'done' WHERE id = ?", (row["id"],))
    return list_items(conn, list_name)


def back_from_shopping(conn: sqlite3.Connection, not_found: list[str]) -> list[str]:
    """Everything bought is done; what they didn't find stays for next time."""
    keep = {fold(i) for i in not_found}
    for row in conn.execute(
        "SELECT id, item FROM list_items WHERE list_name = ? AND status = 'open'", (SHOPPING,)
    ).fetchall():
        if fold(row["item"]) not in keep:
            conn.execute("UPDATE list_items SET status = 'done' WHERE id = ?", (row["id"],))
    return list_items(conn, SHOPPING)


# --- where things are ------------------------------------------------------


def remember_place(conn: sqlite3.Connection, clock: Clock, person_id: int | None, thing: str, place: str) -> int:
    conn.execute("DELETE FROM places WHERE lower(thing) = lower(?)", (thing,))
    cur = conn.execute(
        "INSERT INTO places (person_id, thing, place, updated_at) VALUES (?, ?, ?, ?)",
        (person_id, thing, place, utc_iso(clock.now())),
    )
    return cur.lastrowid


def where_is(conn: sqlite3.Connection, thing: str) -> list[dict]:
    target = fold(thing)
    out = []
    for row in conn.execute("SELECT * FROM places ORDER BY updated_at DESC").fetchall():
        t = fold(row["thing"])
        if target and (target in t or t in target):
            out.append({"thing": row["thing"], "place": row["place"], "since": row["updated_at"][:10]})
    return out


# --- notes about the person ------------------------------------------------

NOTE_KINDS = {"interest", "story", "person", "preference", "health_note", "other"}


def add_note(conn: sqlite3.Connection, clock: Clock, person_id: int, kind: str, text: str) -> int:
    if kind not in NOTE_KINDS:
        kind = "other"
    cur = conn.execute(
        "INSERT INTO notes (person_id, kind, text, created_at) VALUES (?, ?, ?, ?)",
        (person_id, kind, text, utc_iso(clock.now())),
    )
    return cur.lastrowid


def notes(conn: sqlite3.Connection, person_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM notes WHERE person_id = ? ORDER BY id", (person_id,)).fetchall()
    return [{"id": r["id"], "kind": r["kind"], "text": r["text"]} for r in rows]


def forget_note(conn: sqlite3.Connection, person_id: int, note_id: int) -> bool:
    """Really deletes: the note, and every tool_log row that quoted it.

    secure_delete overwrites the freed space; a WAL checkpoint pushes it out
    of the write-ahead log too.
    """
    row = conn.execute("SELECT text FROM notes WHERE id = ? AND person_id = ?", (note_id, person_id)).fetchone()
    if row is None:
        return False
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    # The log stores JSON, where quotes and backslashes are escaped: search both forms.
    escaped = json.dumps(row["text"], ensure_ascii=False)[1:-1]
    for form in {row["text"], escaped}:
        conn.execute("DELETE FROM tool_log WHERE instr(args, ?) > 0 OR instr(result, ?) > 0", (form, form))
    # And any read-back on the web page still holding it (pending, cancelled or done).
    for form in {row["text"], escaped}:
        conn.execute("UPDATE web_pending SET readback = '', payload = '{}' WHERE person_id = ? AND "
                     "(instr(readback, ?) > 0 OR instr(payload, ?) > 0)", (person_id, form, form))
    # And any line of their conversation on the web page that holds it.
    conn.execute("DELETE FROM chat WHERE person_id = ? AND instr(lower(text), lower(?)) > 0", (person_id, row["text"]))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return True


def forget_place(conn: sqlite3.Connection, place_id: int) -> bool:
    cur = conn.execute("DELETE FROM places WHERE id = ?", (place_id,))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return cur.rowcount == 1


# --- appointments, birthdays, reminders ------------------------------------


def add_appointment(
    conn: sqlite3.Connection, clock: Clock, person_id: int | None, title: str, starts_at: datetime, location: str = ""
) -> int:
    cur = conn.execute(
        "INSERT INTO appointments (person_id, title, starts_at_utc, location, told_at) VALUES (?, ?, ?, ?, ?)",
        (person_id, title, utc_iso(starts_at), location, utc_iso(clock.now())),
    )
    return cur.lastrowid


def move_appointment(conn: sqlite3.Connection, clock: Clock, appointment_id: int, starts_at: datetime) -> None:
    conn.execute(
        "UPDATE appointments SET starts_at_utc = ?, told_at = ? WHERE id = ?",
        (utc_iso(starts_at), utc_iso(clock.now()), appointment_id),
    )
    # Reminders for the old time must fire again for the new one.
    conn.execute("DELETE FROM event_log WHERE key LIKE ?", (f"appt:{appointment_id}:%",))


def cancel_appointment(conn: sqlite3.Connection, appointment_id: int) -> None:
    conn.execute("UPDATE appointments SET cancelled = 1 WHERE id = ?", (appointment_id,))


def appointments(conn: sqlite3.Connection, clock: Clock, person_id: int | None = None, upcoming: bool = True) -> list[dict]:
    sql = "SELECT * FROM appointments WHERE cancelled = 0"
    args: list = []
    if upcoming:
        sql += " AND starts_at_utc >= ?"
        args.append(utc_iso(clock.now()))
    if person_id is not None:
        sql += " AND (person_id = ? OR person_id IS NULL)"
        args.append(person_id)
    out = []
    for r in conn.execute(sql + " ORDER BY starts_at_utc", args).fetchall():
        at = from_utc_iso(r["starts_at_utc"], clock.tz)
        out.append(
            {"id": r["id"], "person_id": r["person_id"], "title": r["title"], "location": r["location"],
             "when": readback.when(at, clock.now().date()), "starts_at": at}
        )
    return out


def add_contact(
    conn: sqlite3.Connection, clock: Clock, name: str, relation: str = "", birthday_md: str | None = None,
    help_contact: bool = False, for_person: int | None = None,
) -> int:
    if birthday_md is not None:
        m, d = (int(x) for x in birthday_md.split("-"))
        date(2024, m, d)  # validates, leap day included
        birthday_md = f"{m:02d}-{d:02d}"
    cur = conn.execute(
        "INSERT INTO contacts (name, relation, birthday_md, help_contact, for_person, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, relation, birthday_md, int(help_contact), for_person, utc_iso(clock.now())),
    )
    return cur.lastrowid


def add_reminder(
    conn: sqlite3.Connection, clock: Clock, person_id: int | None, text: str, time_hhmm: str, rule: str, anchor: date
) -> int:
    _check_rule(rule)
    cur = conn.execute(
        "INSERT INTO reminders (person_id, text, time_hhmm, rule, anchor_date, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (person_id, text, time_hhmm, rule, anchor.isoformat(), utc_iso(clock.now())),
    )
    return cur.lastrowid


WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _check_rule(rule: str) -> None:
    if rule in ("once", "daily"):
        return
    if rule.startswith("weekly:") and all(d in WEEKDAYS for d in rule[7:].split(",")):
        return
    if rule.startswith("every:") and rule[6:].isdigit() and int(rule[6:]) >= 1:
        return
    raise ValueError(f"bad rule {rule!r}")


def reminder_occurs_on(rule: str, anchor: date, day: date) -> bool:
    if day < anchor:
        return False
    if rule == "once":
        return day == anchor
    if rule == "daily":
        return True
    if rule.startswith("weekly:"):
        return WEEKDAYS[day.weekday()] in rule[7:].split(",")
    if rule.startswith("every:"):
        return (day - anchor).days % int(rule[6:]) == 0
    return False


def reminders(conn: sqlite3.Connection, person_id: int | None = None) -> list[dict]:
    """Active reminders made by voice or on the web: the person's own and the shared ones (all, if no person)."""
    sql, args = "SELECT * FROM reminders WHERE active = 1", []
    if person_id is not None:
        sql += " AND (person_id = ? OR person_id IS NULL)"
        args.append(person_id)
    return [{"id": r["id"], "person_id": r["person_id"], "text": r["text"], "time": r["time_hhmm"],
             "rule": r["rule"], "first_day": r["anchor_date"]}
            for r in conn.execute(sql + " ORDER BY time_hhmm, id", args).fetchall()]


def stop_reminder(conn: sqlite3.Connection, reminder_id: int) -> bool:
    return conn.execute("UPDATE reminders SET active = 0 WHERE id = ? AND active = 1", (reminder_id,)).rowcount == 1


def birthdays(conn: sqlite3.Connection, person_id: int) -> list[dict]:
    """Birthdays Pepe reminds this person of (theirs and shared), in calendar order."""
    rows = conn.execute("SELECT id, name, relation, birthday_md, for_person FROM contacts WHERE birthday_md IS NOT NULL "
                        "AND birthday_reminders = 1 AND (for_person IS NULL OR for_person = ?) ORDER BY birthday_md, id",
                        (person_id,)).fetchall()
    return [{"id": r["id"], "name": r["name"], "relation": r["relation"], "md": r["birthday_md"],
             "for_person": r["for_person"]} for r in rows]


def stop_birthday(conn: sqlite3.Connection, contact_id: int) -> bool:
    """Pepe stops reminding the birthday. The person stays (they may be the help contact)."""
    return conn.execute("UPDATE contacts SET birthday_reminders = 0 WHERE id = ? AND birthday_reminders = 1",
                        (contact_id,)).rowcount == 1
