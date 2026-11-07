"""Small key-value storage for the information sources: the home's place, the feeds, the caches."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from kore.memory.db import from_utc_iso, utc_iso


def get(conn: sqlite3.Connection, key: str, tz) -> tuple[object, datetime] | None:
    row = conn.execute("SELECT value, at_utc FROM info WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return json.loads(row["value"]), from_utc_iso(row["at_utc"], tz)


def put(conn: sqlite3.Connection, key: str, value: object, at: datetime) -> None:
    conn.execute("INSERT INTO info (key, value, at_utc) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET "
                 "value = excluded.value, at_utc = excluded.at_utc",
                 (key, json.dumps(value, ensure_ascii=False), utc_iso(at)))
