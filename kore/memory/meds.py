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
# "I took it" never reaches further back than this: an early pill today must
# not rewrite yesterday's missed dose.
REACH_BACK = timedelta(hours=6)

NEVER_DOUBLE = (
    "Never suggest catching up, taking another, or doubling a dose. "
    "If they're unsure, suggest checking the pill box or asking the pharmacist or doctor."
)


class ClockUntrusted(Exception):
    """The time can't be trusted: don't fire or log doses, and say why."""

    # Said as it is (Spanish, neutral about KORE's gender): the same line as the offline phrase.
    say = (
        "No sé la hora con certeza, así que no puedo llevar la cuenta de las medicinas "
        "hasta que se arregle mi reloj. Mejor mirar el pastillero."
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


def request_stop(conn: sqlite3.Connection, clock: Clock, medication_id: int) -> bool:
    """A confirmed "stop reminding me". Stops only on the second confirmed day; returns whether it stopped."""
    today = clock.now().date().isoformat()
    row = conn.execute(
        "SELECT first_yes_day FROM med_stop_requests WHERE medication_id = ?", (medication_id,)
    ).fetchone()
    if row is not None and row["first_yes_day"] < today:
        stop_medication(conn, clock, medication_id)
        conn.execute("DELETE FROM med_stop_requests WHERE medication_id = ?", (medication_id,))
        return True
    conn.execute("INSERT OR IGNORE INTO med_stop_requests (medication_id, first_yes_day) VALUES (?, ?)",
                 (medication_id, today))
    return False


def medications(conn: sqlite3.Connection, person_id: int | None = None, active: bool = True) -> list[sqlite3.Row]:
    sql = "SELECT * FROM medications WHERE active = ?"
    args: list = [int(active)]
    if person_id is not None:
        sql += " AND person_id = ?"
        args.append(person_id)
    return conn.execute(sql + " ORDER BY id", args).fetchall()


# Words that don't identify a medicine: "la de la tensión" means "tensión".
FILLER_WORDS = {"la", "el", "las", "los", "de", "del", "mi", "mis", "lo", "the", "my", "of", "one", "pill", "pills"}


def find_medications(conn: sqlite3.Connection, person_id: int, name: str) -> list[sqlite3.Row]:
    """Every medicine the words could mean. More than one means: ask, never pick."""
    from kore.brain.language import fold

    words = set(fold(name).split()) - FILLER_WORDS
    if not words:
        return []
    return [m for m in medications(conn, person_id)
            if words <= set(fold(m["name"] + " " + m["description"]).split())]


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

    Without a medication, it covers the medicines whose dose is open now, if
    they all share one time ("I took my morning pills"). If open doses have
    different times (08:00 and 08:30), nothing is logged: Pepe asks which.
    Never logs a dose twice, never reaches back more than REACH_BACK.
    """
    now = _check_clock(clock)
    meds = medications(conn, person_id)
    if medication_id is not None:
        meds = [m for m in meds if m["id"] == medication_id]
        if not meds:
            return {"ok": False, "error": "unknown_medication"}

    open_doses, already = [], []
    for med in meds:
        slot = current_slot(med, now, clock)
        if slot is None or elapsed(slot, now) > REACH_BACK:
            continue
        row = dose_row(conn, med["id"], slot)
        entry = {"medication": med["name"], "slot": slot.strftime("%H:%M"), "day": slot.date().isoformat()}
        if row is not None and row["status"] == "taken":
            entry["taken_at"] = from_utc_iso(row["taken_at_utc"], clock.tz).strftime("%H:%M")
            already.append(entry)
        else:
            open_doses.append((med, slot, entry))
    if not open_doses and not already:
        return {"ok": False, "error": "no_dose_due", "rules": NEVER_DOUBLE,
                "say": "Say you have no dose due now to note. Never guess which one."}
    if len({_utc(s) for _, s, _ in open_doses}) > 1:
        return {"ok": False, "error": "ambiguous", "options": [e for _, _, e in open_doses],
                "say": "Ask which medicine they took. Never guess.", "rules": NEVER_DOUBLE}

    marked = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        for med, slot, entry in open_doses:
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


def mark_spoken(conn: sqlite3.Connection, medication_ids: list[int], slot_utc: str) -> None:
    """The voice said this reminder out loud (called by the manners layer)."""
    for mid in medication_ids:
        conn.execute("UPDATE reminder_attempts SET spoken = spoken + 1 WHERE medication_id = ? AND slot_utc = ?",
                     (mid, slot_utc))


def close_slot(conn: sqlite3.Connection, clock: Clock, med: sqlite3.Row, slot: datetime, reminded: bool) -> None:
    """The dose window ended with no "I took it": record it honestly.

    'missed' means a reminder was said out loud and got no answer; 'unknown'
    means it never was (device off, clock untrusted, visitors, out or away), so
    we can't say anything. Only 'missed' counts towards a family alert.
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
