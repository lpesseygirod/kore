"""Every reminder line the box may have to say without the internet, so it can be recorded while online.

On the box the voice is ElevenLabs, which needs the internet. Offline, code speaks its own fallback phrases
(kore/brain/fallback.py) and the voice can only play what it recorded earlier. PEPE.md 6b: "medication reminders
continue offline". So, while online, the box records each phrase the scheduler could need in the coming days:
every medicine time for each person (first and later attempts, normal, short and visitors' signal), the
appointments of the next week, voice reminders, birthdays, and the morning greeting.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

from kore.brain import fallback
from kore.memory.db import from_utc_iso, utc_iso
from kore.scheduler.clock import Clock
from kore.scheduler.engine import Event

AHEAD_DAYS = 8
MANNERS = ("say", "short", "signal")


def _phrases(event: Event) -> list[str]:
    out = []
    for manner in MANNERS:
        line = fallback.phrase(event, manner)
        if line:
            out.append(line)
    return out


def reminder_lines(conn: sqlite3.Connection, clock: Clock) -> list[str]:
    now = clock.now()
    lines: list[str] = ["Buenos días."]
    people = {r["id"]: (r["call_name"] or r["name"]) for r in conn.execute("SELECT id, name, call_name FROM people")}

    # Medicines: per person and time, the medicines due together (as the engine groups them).
    by_slot: dict[tuple[int, str], list[dict]] = {}
    for m in conn.execute("SELECT person_id, name, description, times FROM medications WHERE active = 1"):
        for t in json.loads(m["times"]):
            by_slot.setdefault((m["person_id"], t), []).append({"name": m["name"], "description": m["description"]})
    for (pid, t), items in sorted(by_slot.items()):
        person = people.get(pid, "")
        for attempt, late in ((1, False), (2, False), (1, True)):
            lines += _phrases(Event("medication_reminder", 1, [pid], {
                "person": person, "time": t, "medications": items, "attempt": attempt, "late": late}))

    # Appointments in the coming days: the day before, and on the day.
    end = now + timedelta(days=AHEAD_DAYS)
    for a in conn.execute("SELECT * FROM appointments WHERE cancelled = 0 AND starts_at_utc >= ? AND starts_at_utc <= ?",
                          (utc_iso(now), utc_iso(end))):
        starts = from_utc_iso(a["starts_at_utc"], clock.tz)
        data = {"title": a["title"], "location": a["location"], "time": starts.strftime("%H:%M"),
                "day": fallback.WEEKDAYS[starts.weekday()], "person": people.get(a["person_id"])}
        for kind in ("appointment_day_before", "appointment_same_day"):
            lines += _phrases(Event(kind, 1, [], data))

    # Reminders made by voice, and birthdays in the coming days.
    for r in conn.execute("SELECT text, time_hhmm FROM reminders WHERE active = 1"):
        lines += _phrases(Event("reminder", 1, [], {"text": r["text"], "time": r["time_hhmm"]}))
    days = {(now.date() + timedelta(days=i)).strftime("%m-%d") for i in range(AHEAD_DAYS)}
    for c in conn.execute("SELECT name, birthday_md FROM contacts WHERE birthday_md IS NOT NULL AND "
                          "birthday_reminders = 1"):
        if c["birthday_md"] in days:
            lines += _phrases(Event("birthday", 1, [], {"name": c["name"]}))
    # The visitors' signal for each person ("Carmen, un recordatorio para cuando pueda.").
    for name in people.values():
        lines.append(f"{name}, un recordatorio para cuando pueda.")
    seen, out = set(), []
    for line in lines:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out

