"""The home's SQLite database.

Set up to survive sudden power loss (BUILD-PLAN 0b.9): WAL journal with
synchronous=FULL, so a committed dose is on disk before the call returns.
secure_delete overwrites deleted rows, so "forget that" really forgets.

Times are stored as UTC ISO strings, so they sort and compare as text.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    call_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consents (
    person_id INTEGER NOT NULL REFERENCES people(id),
    kind TEXT NOT NULL,
    granted INTEGER NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (person_id, kind)
);

CREATE TABLE IF NOT EXISTS medications (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    times TEXT NOT NULL,              -- JSON list of "HH:MM", local time
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- One row per scheduled dose that has an outcome. The UNIQUE key is what
-- makes a double log impossible.
CREATE TABLE IF NOT EXISTS doses (
    id INTEGER PRIMARY KEY,
    medication_id INTEGER NOT NULL REFERENCES medications(id),
    person_id INTEGER NOT NULL REFERENCES people(id),
    slot_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('taken', 'missed', 'unknown')),
    taken_at_utc TEXT,
    logged_at_utc TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    UNIQUE (medication_id, slot_utc)
);

CREATE TABLE IF NOT EXISTS reminder_attempts (
    medication_id INTEGER NOT NULL REFERENCES medications(id),
    slot_utc TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    last_attempt_utc TEXT NOT NULL,
    PRIMARY KEY (medication_id, slot_utc)
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY,
    person_id INTEGER REFERENCES people(id),   -- NULL: shared
    title TEXT NOT NULL,
    starts_at_utc TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    cancelled INTEGER NOT NULL DEFAULT 0,
    told_at TEXT NOT NULL             -- when they last told us (made or moved)
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT '',
    birthday_md TEXT,                 -- "MM-DD"
    birthday_reminders INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    person_id INTEGER REFERENCES people(id),   -- NULL: shared
    text TEXT NOT NULL,
    time_hhmm TEXT NOT NULL,
    rule TEXT NOT NULL,               -- once | daily | weekly:MON,THU | every:3
    anchor_date TEXT NOT NULL,        -- YYYY-MM-DD, first occurrence
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS list_items (
    id INTEGER PRIMARY KEY,
    list_name TEXT NOT NULL,
    item TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY,
    person_id INTEGER REFERENCES people(id),
    thing TEXT NOT NULL,
    place TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Events that must fire once only (appointment and birthday reminders...).
CREATE TABLE IF NOT EXISTS event_log (
    key TEXT PRIMARY KEY,
    fired_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_log (
    id INTEGER PRIMARY KEY,
    at_utc TEXT NOT NULL,
    turn INTEGER NOT NULL,
    speaker_id INTEGER,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    result TEXT NOT NULL
);
"""


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)  # explicit transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA secure_delete = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


def utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime: always pass an aware time")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_utc_iso(s: str, tz) -> datetime:
    return datetime.fromisoformat(s).astimezone(tz)
