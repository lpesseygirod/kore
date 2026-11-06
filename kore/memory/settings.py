"""Settings for the home and for each person, with defaults.

Setup (phase 5) and feedback (phase 4b) change these; code reads them. Person 0
means the whole home. A person's own value wins over the home's; a temporary
value ("just now", "today") wins over both until it runs out.

None of these is a safety rule: medicine reminders, family alerts and the
emergency path are not settings, so feedback can't switch them off here.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime

from kore.memory.db import utc_iso
from kore.scheduler.clock import Clock

HOME = 0

DEFAULTS = {
    "night_start": "22:30",     # no talking unprompted from here...
    "night_end": "08:00",       # ...until here (PEPE.md 4, "Night")
    "rest_hours": "",           # a daily quiet time they asked for, "16:00-18:00"
    "quiet_minutes": "120",     # "be quiet" with no time said
    "speech_rate": "1.0",
    "volume": "normal",
    "answer_length": "normal",
    "chattiness": "normal",     # how many conversation starters a day
}

# Settings that only exist for the whole home (one speaker, one room: night for
# one of them is night for both).
HOME_ONLY = {"rest_hours", "quiet_minutes", "night_start", "night_end"}

# Bounds, so no setting can make it "night" most of the day or "rest" all afternoon.
NIGHT_HOURS = (6, 13)              # how long a night can be
NIGHT_START_RANGE = ("19:00", "02:00")
NIGHT_END_RANGE = ("05:00", "12:00")
REST_MAX_HOURS = 4

# Ordered choices, so "slower", "louder", "less" are one step along.
CHOICES = {
    "speech_rate": ["0.8", "0.9", "1.0", "1.1", "1.2"],
    "volume": ["soft", "normal", "loud", "very_loud"],
    "answer_length": ["short", "normal", "long"],
    "chattiness": ["none", "less", "normal", "more"],
}
STEPS = {
    "slower": ("speech_rate", -1), "faster": ("speech_rate", +1),
    "softer": ("volume", -1), "louder": ("volume", +1),
    "shorter": ("answer_length", -1), "longer": ("answer_length", +1),
    "less": ("chattiness", -1), "more": ("chattiness", +1),
}
STARTERS_FOR_CHATTINESS = {"none": 0, "less": 1, "normal": 2, "more": 3}

_HHMM = r"([01]\d|2[0-3]):[0-5]\d"


def _minutes(v: str) -> bool:
    return v.isdigit() and 15 <= int(v) <= 240


VALIDATORS = {
    "night_start": re.compile(_HHMM).fullmatch,
    "night_end": re.compile(_HHMM).fullmatch,
    "rest_hours": lambda v: v == "" or re.fullmatch(f"{_HHMM}-{_HHMM}", v),
    "quiet_minutes": _minutes,
    **{k: (lambda v, c=c: v in c) for k, c in CHOICES.items()},
}


def check(key: str, value: str, person_id: int = HOME, conn: sqlite3.Connection | None = None) -> None:
    if key not in DEFAULTS:
        raise ValueError(f"unknown setting {key!r}")
    if key in HOME_ONLY and person_id != HOME:
        raise ValueError(f"{key} is for the whole home")
    if not VALIDATORS[key](value):
        raise ValueError(f"bad value for {key}: {value!r}")
    if key in ("night_start", "night_end") and conn is not None:
        start = value if key == "night_start" else get(conn, "night_start")
        end = value if key == "night_end" else get(conn, "night_end")
        if not night_ok(start, end):
            raise ValueError(f"night {start}-{end} is out of bounds")
    if key == "rest_hours" and value:
        start, end = value.split("-")
        if not 0 < (to_minutes(end) - to_minutes(start)) % 1440 <= REST_MAX_HOURS * 60:
            raise ValueError(f"rest time is at most {REST_MAX_HOURS} hours")


def to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def from_noon(hhmm: str) -> int:
    """Minutes after 12:00, so a night (22:30-08:00) is one straight range."""
    return (to_minutes(hhmm) - 720) % 1440


def within(t: str, start: str, end: str) -> bool:
    """t in [start, end), for local HH:MM times; the range can cross midnight."""
    return (start <= t or t < end) if start > end else start <= t < end


def night_ok(start: str, end: str) -> bool:
    length = (to_minutes(end) - to_minutes(start)) % 1440
    return (NIGHT_HOURS[0] * 60 <= length <= NIGHT_HOURS[1] * 60
            and within(start, NIGHT_START_RANGE[0], NIGHT_START_RANGE[1])
            and NIGHT_END_RANGE[0] <= end <= NIGHT_END_RANGE[1])


def get(conn: sqlite3.Connection, key: str, person_id: int = HOME, clock: Clock | None = None) -> str:
    """The value in force: temporary (if a clock is given), then the person's, then the home's."""
    if clock is not None:
        row = conn.execute(
            "SELECT value FROM setting_overrides WHERE person_id IN (?, ?) AND key = ? AND until_utc > ? "
            "ORDER BY person_id = ? DESC, id DESC LIMIT 1",
            (person_id, HOME, key, utc_iso(clock.now()), person_id),
        ).fetchone()
        if row:
            return row["value"]
    row = conn.execute("SELECT value FROM settings WHERE person_id = ? AND key = ?", (person_id, key)).fetchone()
    if row is None and person_id != HOME:
        return get(conn, key, HOME)
    return row["value"] if row else DEFAULTS[key]


def stored(conn: sqlite3.Connection, key: str, person_id: int = HOME) -> str | None:
    """Exactly what is stored for this person (None: they use the home's or the default)."""
    row = conn.execute("SELECT value FROM settings WHERE person_id = ? AND key = ?", (person_id, key)).fetchone()
    return row["value"] if row else None


def put(conn: sqlite3.Connection, clock: Clock, key: str, value: str | None, person_id: int = HOME) -> None:
    """Store a value; None removes the person's own value (back to the home's or the default)."""
    if value is None:
        conn.execute("DELETE FROM settings WHERE person_id = ? AND key = ?", (person_id, key))
        return
    check(key, value, person_id, conn)
    conn.execute(
        "INSERT INTO settings (person_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (person_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (person_id, key, value, utc_iso(clock.now())),
    )


def put_for_now(conn: sqlite3.Connection, key: str, value: str, person_id: int, until: datetime,
                feedback_id: int | None = None) -> int:
    """A temporary value, until `until` (aware time)."""
    check(key, value, person_id, conn)
    return conn.execute(
        "INSERT INTO setting_overrides (person_id, key, value, until_utc, feedback_id) VALUES (?, ?, ?, ?, ?)",
        (person_id, key, value, utc_iso(until), feedback_id),
    ).lastrowid


def step(key: str, current: str, direction: int) -> str:
    """One step along the choices, stopping at the ends."""
    choices = CHOICES[key]
    i = max(0, min(len(choices) - 1, choices.index(current) + direction))
    return choices[i]
