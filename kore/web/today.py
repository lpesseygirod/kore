"""What the "Hoy" page shows a person: their own day, from the records (never guessed).

Personal things stay personal: their own medicines and appointments, plus shared ones. The
partner's medicines are not shown (that comes with the "share with partner" permission, W3).
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from kore.brain.fallback import spoken_date
from kore.info.weather import OpenMeteo, Weather
from kore.memory import meds, store
from kore.memory.db import from_utc_iso
from kore.scheduler.clock import Clock

STATE_WORDS = {"taken": "tomada", "not_recorded": "sin apuntar", "not_recorded_yet": "pendiente",
               "upcoming": "más tarde"}


def for_person(conn: sqlite3.Connection, clock: Clock, person_id: int) -> dict:
    now = clock.now()
    today = now.date()
    out: dict = {"date": spoken_date(today), "clock_ok": clock.trusted()}
    try:
        out["medicines"] = [{"time": d["time"], "name": d["description"] or d["medication"],
                             "state": STATE_WORDS.get(d["state"], ""),
                             "taken_at": d.get("taken_at")} for d in meds.status(conn, clock, person_id)["doses"]]
    except meds.ClockUntrusted:
        out["medicines"] = None  # the page says the time isn't reliable now, like Pepe does
    out["appointments"] = [
        {"time": a["when"].strftime("%H:%M"), "title": a["title"],
         "where": a.get("location", ""), "shared": a.get("person_id") is None}
        for a in _appointments_today(conn, clock, person_id, today)]
    out["reminders"] = _reminders_today(conn, person_id, today)
    out["birthdays"] = _birthdays(conn, person_id, today)
    out["shopping"] = store.list_items(conn)
    w = Weather(conn, clock, OpenMeteo()).brief_today()  # from the cache only: the page never waits
    out["weather"] = w
    return out


def _appointments_today(conn: sqlite3.Connection, clock: Clock, person_id: int, today: date) -> list[dict]:
    rows = conn.execute("SELECT * FROM appointments WHERE cancelled = 0 AND (person_id = ? OR person_id IS NULL)",
                        (person_id,)).fetchall()
    out = []
    for r in rows:
        when = from_utc_iso(r["starts_at_utc"], clock.tz)
        if when.date() == today:
            out.append({"when": when, "title": r["title"], "location": r["location"], "person_id": r["person_id"]})
    return sorted(out, key=lambda a: a["when"])


def _reminders_today(conn: sqlite3.Connection, person_id: int, today: date) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM reminders WHERE active = 1 AND (person_id = ? OR person_id IS NULL)",
                          (person_id,)):
        try:
            if store.reminder_occurs_on(r["rule"], date.fromisoformat(r["anchor_date"]), today):
                out.append({"time": r["time_hhmm"], "text": r["text"]})
        except ValueError:
            continue
    return sorted(out, key=lambda r: r["time"])


def _birthdays(conn: sqlite3.Connection, person_id: int, today: date) -> list[dict]:
    soon = {}
    for i in range(8):
        day = today + timedelta(days=i)
        soon[day.strftime("%m-%d")] = i
        if day.month == 2 and day.day == 28 and not _leap(day.year):
            soon["02-29"] = i  # said on the 28th in a normal year, like the engine does
    out = []
    for c in conn.execute("SELECT name, relation, birthday_md FROM contacts WHERE birthday_md IS NOT NULL AND "
                          "birthday_reminders = 1 AND "
                          "(for_person IS NULL OR for_person = ?)", (person_id,)):
        if c["birthday_md"] in soon:
            out.append({"name": c["name"], "relation": c["relation"], "in_days": soon[c["birthday_md"]]})
    return sorted(out, key=lambda b: b["in_days"])


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
