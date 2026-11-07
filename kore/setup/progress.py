"""Where the setup conversation stands: which step is next, for whom; pausing and picking up again.

Stored in the database, so "let's finish tomorrow" (or a power cut) resumes where it stopped.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from kore.memory.db import from_utc_iso, utc_iso
from kore.scheduler.clock import Clock
from kore.setup.steps import BY_ID, PERMISSION_STEPS, STEPS, Step

HOME = 0
STATUSES = {"not_started", "in_progress", "paused", "complete"}


@dataclass
class Next:
    step: Step | None              # None: nothing for this speaker now
    person_id: int | None = None   # for person steps
    need_speaker: bool = False     # a person step is next, but we don't know who's talking
    waiting_for: list[int] | None = None  # the other person still has their part to do
    done: int = 0
    total: int = 0


def status(conn: sqlite3.Connection) -> str:
    """not_started | in_progress | paused | complete. A home whose people were added
    another way (by the family, or in tests) and that never started setup counts as complete."""
    row = conn.execute("SELECT status FROM setup_state WHERE id = 1").fetchone()
    if row:
        return row["status"]
    has_people = conn.execute("SELECT 1 FROM people LIMIT 1").fetchone() is not None
    return "complete" if has_people else "not_started"


def set_status(conn: sqlite3.Connection, clock: Clock, value: str) -> None:
    if value not in STATUSES:
        raise ValueError(value)
    conn.execute("INSERT INTO setup_state (id, status, changed_at) VALUES (1, ?, ?) ON CONFLICT (id) DO UPDATE SET "
                 "status = excluded.status, changed_at = excluded.changed_at", (value, utc_iso(clock.now())))


def done_steps(conn: sqlite3.Connection) -> set[tuple[int, str]]:
    return {(r["person_id"], r["step"]) for r in conn.execute("SELECT person_id, step FROM setup_steps")}


def mark(conn: sqlite3.Connection, clock: Clock, step_id: str, person_id: int | None, skipped: bool = False) -> None:
    step = BY_ID.get(step_id)
    if step is None:
        raise ValueError(f"unknown step {step_id!r}")
    if step.scope == "person" and person_id is None:
        raise ValueError(f"{step_id} is answered by each person: who is speaking?")
    pid = HOME if step.scope == "home" else person_id
    conn.execute("INSERT INTO setup_steps (person_id, step, status, at_utc) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT (person_id, step) DO UPDATE SET status = excluded.status, at_utc = excluded.at_utc",
                 (pid, step_id, "skipped" if skipped else "done", utc_iso(clock.now())))
    if status(conn) in ("not_started", "paused"):
        set_status(conn, clock, "in_progress")
    if step_id == "finish":
        set_status(conn, clock, "complete")


def answered_permissions(conn: sqlite3.Connection) -> set[tuple[int, str]]:
    """Permission steps already answered outside the step itself (a yes or a no on record)."""
    return {(r["person_id"], PERMISSION_STEPS[r["kind"]]) for r in conn.execute("SELECT person_id, kind FROM consents")
            if r["kind"] in PERMISSION_STEPS}


def next_step(conn: sqlite3.Connection, speaker_id: int | None, people: list[int]) -> Next:
    """The next step for whoever is speaking, in the script's order.

    Each person answers their own steps; the closing steps (test, thanks) wait until
    everyone has done their part, which the other person can do another day.
    """
    done = done_steps(conn) | answered_permissions(conn)
    applicable = [s for s in STEPS if not (s.two_people_only and len(people) < 2)]
    total = sum(1 if s.scope == "home" else max(1, len(people)) for s in applicable)
    count = len([d for d in done if d[1] in {s.id for s in applicable}])
    if speaker_id is None and len(people) == 1:
        speaker_id = people[0]
    for s in applicable:
        if s.scope == "home":
            if (HOME, s.id) in done:
                continue
            if s.id in ("test", "finish"):
                waiting = [p for p in people
                           if any(t.scope == "person" and (p, t.id) not in done for t in applicable)]
                if waiting:  # (the speaker's own steps come earlier in the list, so these are others)
                    return Next(None, waiting_for=waiting, done=count, total=total)
            return Next(s, done=count, total=total)
        if speaker_id is None:
            if any((p, s.id) not in done for p in people):
                return Next(s, need_speaker=True, done=count, total=total)
            continue
        if (speaker_id, s.id) not in done:
            return Next(s, speaker_id, done=count, total=total)
    return Next(None, done=count, total=total)


def offer_due(conn: sqlite3.Connection, clock: Clock) -> bool:
    """Not finished, not offered yet today, and nothing done on it today: offer to start or carry on."""
    if status(conn) == "complete":
        return False
    today = clock.now().date().isoformat()
    row = conn.execute("SELECT offered_on, changed_at FROM setup_state WHERE id = 1").fetchone()
    if row and row["offered_on"] == today:
        return False
    last = conn.execute("SELECT max(at_utc) FROM setup_steps").fetchone()[0]
    if last and from_utc_iso(last, clock.tz).date().isoformat() == today:
        return False  # they're in the middle of it today
    return True


def mark_offered(conn: sqlite3.Connection, clock: Clock) -> None:
    if conn.execute("SELECT 1 FROM setup_state WHERE id = 1").fetchone() is None:
        set_status(conn, clock, "not_started")
    conn.execute("UPDATE setup_state SET offered_on = ? WHERE id = 1", (clock.now().date().isoformat(),))
