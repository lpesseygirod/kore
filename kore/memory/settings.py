"""Settings for the home and for each person, with defaults.

Setup (phase 5) and feedback (phase 4b) change these; code reads them. Person 0
means the whole home (both share one speaker, so night hours are the home's).
"""

from __future__ import annotations

import re
import sqlite3

from kore.memory.db import utc_iso
from kore.scheduler.clock import Clock

HOME = 0

DEFAULTS = {
    "night_start": "22:30",  # no talking unprompted from here...
    "night_end": "08:00",    # ...until here (PEPE.md 4, "Night")
}

_HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
VALIDATORS = {
    "night_start": _HHMM.fullmatch,
    "night_end": _HHMM.fullmatch,
}


def get(conn: sqlite3.Connection, key: str, person_id: int = HOME) -> str:
    row = conn.execute("SELECT value FROM settings WHERE person_id = ? AND key = ?", (person_id, key)).fetchone()
    if row is None and person_id != HOME:
        return get(conn, key, HOME)
    return row["value"] if row else DEFAULTS[key]


def put(conn: sqlite3.Connection, clock: Clock, key: str, value: str, person_id: int = HOME) -> None:
    if key not in DEFAULTS:
        raise ValueError(f"unknown setting {key!r}")
    if not VALIDATORS[key](value):
        raise ValueError(f"bad value for {key}: {value!r}")
    conn.execute(
        "INSERT INTO settings (person_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (person_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (person_id, key, value, utc_iso(clock.now())),
    )
