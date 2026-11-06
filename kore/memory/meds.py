"""Medications and the dose log. Code decides what is true here, never Claude.

Rules enforced in this module:
- A dose slot can be logged as taken once only (UNIQUE key in the database).
- "I took it" goes to the slot whose window we're in; nothing is guessed
  across slots, and nothing ever suggests catching up or doubling.
- If the clock can't be trusted, nothing is logged (ClockUntrusted).
- Adding or changing a medication only happens through a confirmed pending
  action (brain/tools.py); these functions are the executors.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from kore.memory.db import from_utc_iso, utc_iso
from kore.scheduler.clock import Clock, local_at

# "I took it" up to this long before the scheduled time counts for that dose.
EARLY_WINDOW = timedelta(minutes=60)
# Taken this long after the scheduled time counts as "late".
LATE_AFTER = timedelta(hours=2)

NEVER_DOUBLE = (
    "Never suggest catching up, taking another, or doubling a dose. "
    "If they're unsure, suggest checking the pill box or asking the pharmacist or doctor."
)


class ClockUntrusted(Exception):
    """The time can't be trusted: don't fire or log doses, and say why."""

    say = (
        "I'm not sure what time it is right now, so I can't keep track of your "
        "medicines until my clock is fixed. Please check your pill box."
    )


def _check_clock(clock: Clock) -> datetime:
    if not clock.trusted():
        raise ClockUntrusted()
    return clock.now()


def _valid_times(times: list[str]) -> list[str]:
    out = []
    for t in times:
        h, m = (int(x) for x in t.split(":"))
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError(f"bad time {t!r}")
        out.append(f"{h:02d}:{m:02d}")
    if not out:
        raise ValueError("a medication needs at least one time")
    return sorted(set(out))


# --- people and medications ------------------------------------------------


def add_person(conn: sqlite3.Connection, clock: Clock, name: str, call_name: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO people (name, call_name, created_at) VALUES (?, ?, ?)",
        (name, call_name or name, utc_iso(clock.now())),
    )
    return cur.lastrowid


def add_medication(
    conn: sqlite3.Connection, clock: Clock, person_id: int, name: str, times: list[str], description: str = ""
) -> int:
    now = utc_iso(clock.now())
    cur = conn.execute(
        "INSERT INTO medications (person_id, name, description, times, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (person_id, name, description, json.dumps(_valid_times(times)), now, now),
    )
    return cur.lastrowid


def change_medication_times(conn: sqlite3.Connection, clock: Clock, medication_id: int, times: list[str]) -> None:
    conn.execute(
        "UPDATE medications SET times = ?, updated_at = ? WHERE id = ?",
        (json.dumps(_valid_times(times)), utc_iso(clock.now()), medication_id),
    )


def stop_medication(conn: sqlite3.Connection, clock: Clock, medication_id: int) -> None:
    conn.execute(
        "UPDATE medications SET active = 0, updated_at = ? WHERE id = ?", (utc_iso(clock.now()), medication_id)
    )


def medications(conn: sqlite3.Connection, person_id: int | None = None, active: bool = True) -> list[sqlite3.Row]:
    sql = "SELECT * FROM medications WHERE active = ?"
    args: list = [int(active)]
    if person_id is not None:
        sql += " AND person_id = ?"
        args.append(person_id)
    return conn.execute(sql + " ORDER BY id", args).fetchall()


def find_medication(conn: sqlite3.Connection, person_id: int, name: str) -> sqlite3.Row | None:
    from kore.brain.language import fold

    target = fold(name)
    for med in medications(conn, person_id):
        if target and (target in fold(med["name"]) or target in fold(med["description"])):
            return med
    return None


# --- slots -----------------------------------------------------------------


def slots_on(med: sqlite3.Row, day: date, clock: Clock) -> list[datetime]:
    return [local_at(day, t, clock.tz) for t in json.loads(med["times"])]


def slots_between(med: sqlite3.Row, start: datetime, end: datetime, clock: Clock) -> list[datetime]:
    out = []
    day = (start - timedelta(days=1)).date()
    while day <= end.date() + timedelta(days=1):
        out.extend(s for s in slots_on(med, day, clock) if _utc(start) <= _utc(s) < _utc(end))
        day += timedelta(days=1)
    return sorted(out, key=_utc)


def _utc(dt: datetime) -> datetime:
    # Aware datetimes in the same zone compare and subtract by wall time in
    # Python, which is wrong across a DST change. Real-time maths goes via UTC.
    return dt.astimezone(timezone.utc)


def elapsed(start: datetime, end: datetime) -> timedelta:
    return _utc(end) - _utc(start)


def current_slot(med: sqlite3.Row, now: datetime, clock: Clock) -> datetime | None:
    """The dose whose window we're in: the latest slot that has opened (slot − EARLY_WINDOW)."""
    candidates = slots_between(med, now - timedelta(days=2), now + EARLY_WINDOW + timedelta(seconds=1), clock)
    opened = [s for s in candidates if _utc(s) - EARLY_WINDOW <= _utc(now)]
    return opened[-1] if opened else None


def dose_row(conn: sqlite3.Connection, medication_id: int, slot: datetime) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM doses WHERE medication_id = ? AND slot_utc = ?", (medication_id, utc_iso(slot))
    ).fetchone()


# --- logging doses ---------------------------------------------------------


def mark_taken(conn: sqlite3.Connection, clock: Clock, person_id: int, medication_id: int | None = None) -> dict:
    """Log "I took it" for the person's current dose(s).

    Without a medication, it covers every medication due at the most recent
    dose time ("I took my morning pills"). Never logs a dose twice.
    """
    now = _check_clock(clock)
    meds = medications(conn, person_id)
    if medication_id is not None:
        meds = [m for m in meds if m["id"] == medication_id]
        if not meds:
            return {"ok": False, "error": "unknown_medication"}

    current = [(m, current_slot(m, now, clock)) for m in meds]
    current = [(m, s) for m, s in current if s is not None]
    if not current:
        return {"ok": False, "error": "no_dose_due", "rules": NEVER_DOUBLE}
    if medication_id is None:
        latest = max(s for _, s in current)
        current = [(m, s) for m, s in current if s == latest]

    marked, already = [], []
    conn.execute("BEGIN IMMEDIATE")
    try:
        for med, slot in current:
            row = dose_row(conn, med["id"], slot)
            entry = {"medication": med["name"], "slot": slot.strftime("%H:%M"), "day": slot.date().isoformat()}
            if row is not None and row["status"] == "taken":
                entry["taken_at"] = from_utc_iso(row["taken_at_utc"], clock.tz).strftime("%H:%M")
                already.append(entry)
                continue
            late = elapsed(slot, now) > LATE_AFTER
            conn.execute(
                "INSERT INTO doses (medication_id, person_id, slot_utc, status, taken_at_utc, logged_at_utc, note) "
                "VALUES (?, ?, ?, 'taken', ?, ?, ?) "
                "ON CONFLICT (medication_id, slot_utc) DO UPDATE SET "
                "status = 'taken', taken_at_utc = excluded.taken_at_utc, "
                "logged_at_utc = excluded.logged_at_utc, note = excluded.note "
                "WHERE doses.status != 'taken'",
                (med["id"], person_id, utc_iso(slot), utc_iso(now), utc_iso(now), "late" if late else ""),
            )
            entry["late"] = late
            marked.append(entry)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"ok": True, "marked": marked, "already_taken": already, "rules": NEVER_DOUBLE}


def close_slot(conn: sqlite3.Connection, clock: Clock, med: sqlite3.Row, slot: datetime, reminded: bool) -> None:
    """The dose window ended with no "I took it": record it honestly.

    'missed' means we reminded and got no answer; 'unknown' means we never
    reminded (device off, clock untrusted), so we can't say anything.
    """
    conn.execute(
        "INSERT OR IGNORE INTO doses (medication_id, person_id, slot_utc, status, logged_at_utc) "
        "VALUES (?, ?, ?, ?, ?)",
        (med["id"], med["person_id"], utc_iso(slot), "missed" if reminded else "unknown", utc_iso(clock.now())),
    )


def status(conn: sqlite3.Connection, clock: Clock, person_id: int, day: date | None = None) -> dict:
    """Every dose for the person on `day` (default today): what is recorded, nothing guessed."""
    now = _check_clock(clock)
    day = day or now.date()
    doses = []
    for med in medications(conn, person_id):
        for slot in slots_on(med, day, clock):
            row = dose_row(conn, med["id"], slot)
            entry = {"medication": med["name"], "description": med["description"], "time": slot.strftime("%H:%M")}
            if row is not None and row["status"] == "taken":
                entry["state"] = "taken"
                entry["taken_at"] = from_utc_iso(row["taken_at_utc"], clock.tz).strftime("%H:%M")
            elif row is not None:
                entry["state"] = "not_recorded"
            elif _utc(slot) - EARLY_WINDOW <= _utc(now):
                entry["state"] = "not_recorded_yet" if elapsed(slot, now) < LATE_AFTER else "not_recorded"
            else:
                entry["state"] = "upcoming"
            doses.append(entry)
    doses.sort(key=lambda d: d["time"])
    return {
        "ok": True,
        "day": day.isoformat(),
        "doses": doses,
        "rules": "Only say what is recorded here. 'not_recorded' means you don't know, not that they "
        "didn't take it. " + NEVER_DOUBLE,
    }
