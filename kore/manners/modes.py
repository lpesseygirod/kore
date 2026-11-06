"""Home modes: quiet, visitors (whole home), out of the house, away for days (per person).

Stored in the database so a restart keeps them. Every mode ends on its own if
nobody ends it, except "away", which ends only when they say they're back or the
sensor sees someone at home (the manners layer then asks): pausing medicine
reminders by mistake is worse than resuming them by mistake, so "out" times out.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

from kore.memory.db import from_utc_iso, utc_iso
from kore.scheduler.clock import Clock

QUIET_DEFAULT = timedelta(hours=2)
QUIET_MAX = timedelta(hours=24)
VISITORS_MAX = timedelta(hours=6)
OUT_MAX = timedelta(hours=3)

HOME_KINDS = {"quiet", "visitors"}
PERSON_KINDS = {"out", "away"}


def start(conn: sqlite3.Connection, clock: Clock, kind: str, person_id: int | None = None,
          until: datetime | None = None, expected_back: date | None = None) -> int:
    """Start a mode (replacing an active one of the same kind for the same person).

    until: quiet only (default two hours, at most a day); the others time out on
    their own, except away. expected_back: away only, when they said they'd return.
    """
    if kind in HOME_KINDS and person_id is not None:
        raise ValueError(f"{kind} is for the whole home")
    if kind in PERSON_KINDS and person_id is None:
        raise ValueError(f"{kind} needs a person")
    # UTC: Python compares same-zone times by wall clock, wrong across a DST change.
    now = clock.now().astimezone(timezone.utc)
    until = until.astimezone(timezone.utc) if until is not None else None
    if until is not None and (kind != "quiet" or until <= now):
        raise ValueError("until is only for quiet, and must be in the future")
    if expected_back is not None and kind != "away":
        raise ValueError("expected_back is only for away")
    if kind == "quiet":
        until = min(until or now + QUIET_DEFAULT, now + QUIET_MAX)
    elif kind == "visitors":
        until = now + VISITORS_MAX
    elif kind == "out":
        until = now + OUT_MAX
    # A crash between these two leaves the old mode ended and no new one: the safe side.
    _close(conn, kind, person_id, now, "replaced")
    cur = conn.execute(
        "INSERT INTO modes (kind, person_id, started_at_utc, until_utc, expected_back) VALUES (?, ?, ?, ?, ?)",
        (kind, person_id, utc_iso(now), utc_iso(until) if until else None,
         expected_back.isoformat() if expected_back else None),
    )
    return cur.lastrowid


def end(conn: sqlite3.Connection, clock: Clock, kind: str, person_id: int | None = None,
        how: str = "said") -> bool:
    """End the active mode. Returns whether one was active."""
    expire(conn, clock)
    return _close(conn, kind, person_id, clock.now(), how) > 0


def active(conn: sqlite3.Connection, clock: Clock, kind: str, person_id: int | None = None) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM modes WHERE kind = ? AND person_id IS ? AND ended_at_utc IS NULL "
        "AND (until_utc IS NULL OR until_utc > ?) ORDER BY id DESC LIMIT 1",
        (kind, person_id, utc_iso(clock.now())),
    ).fetchone()


def not_home(conn: sqlite3.Connection, clock: Clock, person_id: int) -> bool:
    return bool(active(conn, clock, "out", person_id) or active(conn, clock, "away", person_id))


def expire(conn: sqlite3.Connection, clock: Clock) -> list[sqlite3.Row]:
    """Close modes whose time is up (ended_at = their until). Returns them, once."""
    now = utc_iso(clock.now())
    rows = conn.execute(
        "SELECT * FROM modes WHERE ended_at_utc IS NULL AND until_utc IS NOT NULL AND until_utc <= ?", (now,)
    ).fetchall()
    for r in rows:
        conn.execute("UPDATE modes SET ended_at_utc = until_utc, ended_how = 'time' WHERE id = ?", (r["id"],))
    return rows


def ended_since(conn: sqlite3.Connection, since: datetime) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM modes WHERE ended_at_utc IS NOT NULL AND ended_at_utc >= ? ORDER BY id", (utc_iso(since),)
    ).fetchall()


def ended_at(row: sqlite3.Row, tz) -> datetime:
    return from_utc_iso(row["ended_at_utc"], tz)


def started_at(row: sqlite3.Row, tz) -> datetime:
    return from_utc_iso(row["started_at_utc"], tz)


def _close(conn: sqlite3.Connection, kind: str, person_id: int | None, now: datetime, how: str) -> int:
    return conn.execute(
        "UPDATE modes SET ended_at_utc = ?, ended_how = ? WHERE kind = ? AND person_id IS ? "
        "AND ended_at_utc IS NULL AND (until_utc IS NULL OR until_utc > ?)",
        (utc_iso(now), how, kind, person_id, utc_iso(now)),
    ).rowcount
