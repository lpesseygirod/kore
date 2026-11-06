"""The reminder engine: plain code that decides *when*. Claude only words the events.

Call tick() often (every ~30 s on the device, every simulated minute in tests).
It reads everything from the database, so a restart after a power cut carries
on where it was: no duplicate reminders, no lost attempts. It never touches
the network, so reminders work with the internet down.

Events carry a category (PEPE.md section 4):
  1 = always (medication, appointments, morning greeting, clock problems)
  2 = only if they said yes (water, movement)
The manners layer (kore/manners/gate.py) decides how and whether to say them now.

A dose with no "I took it" is closed as "missed" only if a reminder was really
said out loud (the voice reports it: meds.mark_spoken) and the person is home;
otherwise "unknown". Only "missed" counts towards a family alert. Medicine
reminders are not attempted at all for someone away for days.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from kore.feedback import store as feedback
from kore.manners import modes
from kore.memory import meds, settings, store
from kore.memory.db import from_utc_iso, utc_iso
from kore.memory.meds import _utc, elapsed
from kore.scheduler.clock import Clock, local_at

# Medication: first reminder at the time, then two retries (at most 3, PEPE.md 4).
ATTEMPT_OFFSETS = [timedelta(0), timedelta(minutes=20), timedelta(minutes=45)]
# With no "I took it" by then, the dose is closed as missed (or unknown).
CLOSE_AFTER = timedelta(hours=2)
# Missed doses in a row (per person) before the family alert, if agreed.
MISSED_IN_A_ROW_FOR_ALERT = 2

APPT_DAY_BEFORE_AT = "20:00"
APPT_DAY_BEFORE_LATEST = timedelta(minutes=90)  # after an outage, not later than 21:30
APPT_SAME_DAY_BEFORE = timedelta(hours=1)
BIRTHDAY_AT = "10:00"
# A voice reminder missed while the device was off still fires up to this late.
REMINDER_CATCH_UP = timedelta(hours=3)

WATER_TIMES = ["11:00", "13:00", "16:00", "18:00"]
WATER_TIMES_HOT = ["11:00", "12:00", "13:00", "16:00", "17:00", "18:00"]
WATER_CATCH_UP = timedelta(minutes=30)

SITTING_NUDGE_AFTER = timedelta(hours=2)
MOVEMENT_MAX_PER_DAY = 2
# Category-2 nudges only in the day, whatever else the manners layer decides.
NUDGE_HOURS = ("10:00", "20:00")
MORNING_FROM = "06:00"
MORNING_UNTIL = "12:00"  # first presence after this is not a "good morning"
# The light "is that better?" a few days after a change, at most one a day (BUILD-PLAN 2).
FEEDBACK_AT = "11:30"


@dataclass
class Event:
    kind: str
    category: int
    person_ids: list[int]
    data: dict = field(default_factory=dict)
    private: bool = False  # don't say it in front of visitors
    key: str | None = None  # a newer event with the same key replaces one not yet said
    expires_at: datetime | None = None  # not worth saying after this
    not_before: datetime | None = None


class Engine:
    def __init__(self, conn: sqlite3.Connection, clock: Clock, hot_day: bool = False):
        self.conn = conn
        self.clock = clock
        self.hot_day = hot_day
        self._presence = "absent"
        self._still_since: datetime | None = None

    # --- main loop --------------------------------------------------------

    def tick(self) -> list[Event]:
        if not self.clock.trusted():
            # Don't fire or log doses on a wrong clock; say why, once (also across restarts).
            if not self._once("clock_untrusted"):
                return []
            return [Event("clock_untrusted", 1, self._everyone(), {"say": meds.ClockUntrusted.say},
                          key="clock_untrusted")]
        self.conn.execute("DELETE FROM event_log WHERE key = 'clock_untrusted'")  # warn again next time
        now = self.clock.now()
        events: list[Event] = []
        events += self._medication(now)
        events += self._appointments(now)
        events += self._birthdays(now)
        events += self._voice_reminders(now)
        events += self._water(now)
        events += self._movement(now)
        events += self._feedback(now)
        return events

    # --- presence sensor --------------------------------------------------

    def on_presence(self, state: str) -> list[Event]:
        """state: 'moving' | 'still' | 'absent'. Returns the morning greeting when due."""
        now = self.clock.now()
        prev, self._presence = self._presence, state
        if state == "still":
            if prev != "still":
                self._still_since = now
        else:
            self._still_since = None
        if state == "absent" or not self.clock.trusted():
            return []
        if not (elapsed(local_at(now.date(), MORNING_FROM, self.clock.tz), now) >= timedelta(0)
                and elapsed(now, local_at(now.date(), MORNING_UNTIL, self.clock.tz)) > timedelta(0)):
            return []
        if not self._once(f"morning:{now.date()}"):
            return []
        plan = self.plan_for_day(now.date())
        plan["rules"] = ("Brief. Mention medicine times only as times: whether a dose was taken comes only from "
                         "the records (get_med_status), never from the time. Don't mention what isn't there.")
        return [Event("morning_greeting", 1, self._everyone(), plan, private="people" in plan,
                      expires_at=local_at(now.date(), MORNING_UNTIL, self.clock.tz))]

    def plan_for_day(self, day: date) -> dict:
        """What today holds, for the greeting. Facts from code only.

        Personal things (medicine times, a doctor's visit) are included only in
        a one-person home: with two, nobody is known to be in the room yet.
        """
        plan = {"date": day.isoformat(), "weekday": day.strftime("%A")}
        people = self.conn.execute("SELECT * FROM people ORDER BY id").fetchall()
        if len(people) == 1:
            p = people[0]
            doses = sorted({t for m in meds.medications(self.conn, p["id"])
                            for t in (s.strftime("%H:%M") for s in meds.slots_on(m, day, self.clock))})
            appts = [a for a in store.appointments(self.conn, self.clock, p["id"], upcoming=False)
                     if a["starts_at"].date() == day and a["person_id"] == p["id"]]
            plan["people"] = {p["call_name"]: {
                "medicine_times": doses,
                "appointments": [{"title": a["title"], "time": a["starts_at"].strftime("%H:%M")} for a in appts],
            }}
        plan["shared_appointments"] = [
            {"title": a["title"], "time": a["starts_at"].strftime("%H:%M")}
            for a in store.appointments(self.conn, self.clock, upcoming=False)
            if a["starts_at"].date() == day and a["person_id"] is None
        ]
        plan["birthdays"] = self._birthdays_on(day)
        return plan

    # --- medication -------------------------------------------------------

    def _medication(self, now: datetime) -> list[Event]:
        due: dict[tuple[int, str], list[dict]] = {}
        closed_missed: list[int] = []
        for med in meds.medications(self.conn):
            floor = from_utc_iso(med["updated_at"], self.clock.tz)
            for slot in meds.slots_between(med, now - timedelta(days=1), now + timedelta(seconds=1), self.clock):
                if elapsed(floor, slot) < timedelta(0) or elapsed(slot, now) < timedelta(0):
                    continue  # before the medicine existed in this form, or not yet due
                if meds.dose_row(self.conn, med["id"], slot) is not None:
                    continue  # resolved
                row = self.conn.execute(
                    "SELECT * FROM reminder_attempts WHERE medication_id = ? AND slot_utc = ?",
                    (med["id"], utc_iso(slot)),
                ).fetchone()
                attempts = row["attempts"] if row else 0
                if elapsed(slot, now) >= CLOSE_AFTER:
                    reminded = (row is not None and row["spoken"] > 0
                                and not modes.not_home(self.conn, self.clock, med["person_id"]))
                    meds.close_slot(self.conn, self.clock, med, slot, reminded=reminded)
                    if reminded:
                        closed_missed.append(med["person_id"])
                    continue
                if attempts >= len(ATTEMPT_OFFSETS) or elapsed(slot, now) < ATTEMPT_OFFSETS[attempts]:
                    continue
                if modes.active(self.conn, self.clock, "away", med["person_id"]):
                    continue  # away for days: nothing to remind; resumes when they're back
                if row and elapsed(from_utc_iso(row["last_attempt_utc"], self.clock.tz), now) < timedelta(minutes=15):
                    continue  # catching up after an outage: one attempt at a time
                self.conn.execute(
                    "INSERT INTO reminder_attempts (medication_id, slot_utc, attempts, last_attempt_utc) "
                    "VALUES (?, ?, 1, ?) ON CONFLICT (medication_id, slot_utc) DO UPDATE SET "
                    "attempts = attempts + 1, last_attempt_utc = excluded.last_attempt_utc",
                    (med["id"], utc_iso(slot), utc_iso(now)),
                )
                key = (med["person_id"], utc_iso(slot))
                due.setdefault(key, []).append(
                    {"id": med["id"], "name": med["name"], "description": med["description"],
                     "attempt": attempts + 1}
                )
        events = []
        for (pid, slot_utc), items in sorted(due.items()):
            slot = from_utc_iso(slot_utc, self.clock.tz)
            events.append(Event(
                "medication_reminder", 1, [pid],
                {"person": self._call_name(pid), "time": slot.strftime("%H:%M"), "slot_utc": slot_utc,
                 "medications": items,
                 "attempt": max(i["attempt"] for i in items), "late": elapsed(slot, now) > timedelta(minutes=50),
                 "rules": "Address them by name. A 'yes' only counts once you know who is speaking. "
                          "A later attempt is shorter and softer than the first: never 'last warning'. "
                          + meds.NEVER_DOUBLE},
                private=True, key=f"med:{pid}:{slot_utc}", expires_at=_utc(slot) + CLOSE_AFTER,
            ))
        for pid in sorted(set(closed_missed)):
            events += self._missed_alert(pid)
        return events

    def _missed_alert(self, pid: int) -> list[Event]:
        # Counted per dose time, not per pill: two pills missed at 09:00 is one missed time.
        rows = self.conn.execute(
            "SELECT slot_utc, min(status = 'missed') AS all_missed FROM doses WHERE person_id = ? "
            "GROUP BY slot_utc ORDER BY slot_utc DESC LIMIT ?",
            (pid, MISSED_IN_A_ROW_FOR_ALERT),
        ).fetchall()
        if len(rows) < MISSED_IN_A_ROW_FOR_ALERT or not all(r["all_missed"] for r in rows):
            return []
        if not store.has_consent(self.conn, pid, "family_missed_meds"):
            return []
        if not self._once(f"missed_alert:{pid}:{rows[0]['slot_utc']}"):
            return []
        return [Event("family_alert_missed_meds", 1, [pid],
                      {"person": self._call_name(pid), "missed_in_a_row": MISSED_IN_A_ROW_FOR_ALERT}, private=True)]

    # --- appointments, birthdays, voice reminders ---------------------------

    def _appointments(self, now: datetime) -> list[Event]:
        events = []
        for a in self.conn.execute(
            "SELECT * FROM appointments WHERE cancelled = 0 AND starts_at_utc > ? AND starts_at_utc < ?",
            (utc_iso(now), utc_iso(now + timedelta(days=2))),
        ):
            starts = from_utc_iso(a["starts_at_utc"], self.clock.tz)
            told = from_utc_iso(a["told_at"], self.clock.tz)
            triggers = {
                "day_before": local_at(starts.date() - timedelta(days=1), APPT_DAY_BEFORE_AT, self.clock.tz),
                "same_day": starts - APPT_SAME_DAY_BEFORE,
            }
            for which, at in triggers.items():
                if elapsed(at, now) < timedelta(0) or elapsed(told, at) < timedelta(0):
                    continue  # not yet, or they told us after this moment (they know)
                if which == "day_before" and elapsed(at, now) > APPT_DAY_BEFORE_LATEST:
                    self._once(f"appt:{a['id']}:{which}")  # too late at night for "tomorrow": drop it
                    continue
                if not self._once(f"appt:{a['id']}:{which}"):
                    continue
                pids = [a["person_id"]] if a["person_id"] else self._everyone()
                events.append(Event(
                    f"appointment_{which}", 1, pids,
                    {"title": a["title"], "location": a["location"], "time": starts.strftime("%H:%M"),
                     "day": starts.strftime("%A"), "person": self._call_name(a["person_id"]) if a["person_id"] else None},
                    private=a["person_id"] is not None, key=f"appt:{a['id']}:{which}", expires_at=starts,
                ))
        return events

    def _birthdays_on(self, day: date) -> list[dict]:
        mds = {day.strftime("%m-%d")}
        if day.month == 2 and day.day == 28 and not _is_leap(day.year):
            mds.add("02-29")
        return [{"name": c["name"], "relation": c["relation"]} for c in self.conn.execute(
            "SELECT * FROM contacts WHERE birthday_reminders = 1 AND birthday_md IN (%s)" % ",".join("?" * len(mds)),
            sorted(mds))]

    def _birthdays(self, now: datetime) -> list[Event]:
        if elapsed(local_at(now.date(), BIRTHDAY_AT, self.clock.tz), now) < timedelta(0):
            return []
        out = []
        for b in self._birthdays_on(now.date()):
            if self._once(f"birthday:{b['name']}:{now.date()}"):
                out.append(Event("birthday", 1, self._everyone(), b,
                                 expires_at=local_at(now.date() + timedelta(days=1), "00:00", self.clock.tz)))
        return out

    def _voice_reminders(self, now: datetime) -> list[Event]:
        out = []
        for r in self.conn.execute("SELECT * FROM reminders WHERE active = 1").fetchall():
            # Yesterday too: a 23:55 reminder missed in an outage still counts after midnight.
            for day in (now.date() - timedelta(days=1), now.date()):
                if not store.reminder_occurs_on(r["rule"], date.fromisoformat(r["anchor_date"]), day):
                    continue
                at = local_at(day, r["time_hhmm"], self.clock.tz)
                late = elapsed(at, now)
                if late < timedelta(0):
                    continue
                key = f"rem:{r['id']}:{day}"
                if late > REMINDER_CATCH_UP:
                    self._once(key)  # too late to be useful: drop it quietly
                    continue
                if self._once(key):
                    pids = [r["person_id"]] if r["person_id"] else self._everyone()
                    out.append(Event("reminder", 1, pids, {"text": r["text"], "time": r["time_hhmm"]},
                                     private=r["person_id"] is not None, key=key,
                                     expires_at=_utc(at) + REMINDER_CATCH_UP))
                    if r["rule"] == "once":
                        self.conn.execute("UPDATE reminders SET active = 0 WHERE id = ?", (r["id"],))
        return out

    # --- category 2: water and movement -----------------------------------

    def _water(self, now: datetime) -> list[Event]:
        people = self._consenting("water_nudges")
        if not people or self._presence == "absent" or not self._daytime(now):
            return []
        out = []
        for t in WATER_TIMES_HOT if self.hot_day else WATER_TIMES:
            at = local_at(now.date(), t, self.clock.tz)
            late = elapsed(at, now)
            if timedelta(0) <= late and self._once(f"water:{now.date()}:{t}"):
                if late <= WATER_CATCH_UP:
                    out.append(Event("water_nudge", 2, people, {"hot_day": self.hot_day},
                                     expires_at=at + WATER_CATCH_UP))
        return out

    def _movement(self, now: datetime) -> list[Event]:
        people = self._consenting("movement_nudges")
        if not people or self._still_since is None or not self._daytime(now):
            return []
        sat = elapsed(self._still_since, now)
        if sat < SITTING_NUDGE_AFTER:
            return []
        self._still_since = now  # next nudge only after another long sit
        if not any(self._once(f"movement:{now.date()}:{n}") for n in range(MOVEMENT_MAX_PER_DAY)):
            return []
        return [Event("movement_nudge", 2, people, {"sitting_minutes": int(sat.total_seconds() // 60)},
                      expires_at=now + timedelta(minutes=30))]

    # --- feedback: the light check later, and suggestions from what Pepe noticed --

    def _feedback(self, now: datetime) -> list[Event]:
        out = []
        at = local_at(now.date(), FEEDBACK_AT, self.clock.tz)
        if elapsed(at, now) >= timedelta(0) and self._daytime(now) and self._once(f"feedback:{now.date()}"):
            row = feedback.due_check(self.conn, self.clock)
            if row is not None:
                who = [row["person_id"]] if row["person_id"] else self._everyone()
                out.append(Event("feedback_check", 2, who, {
                    "person": self._call_name(row["person_id"]) if row["person_id"] else None,
                    "understood": row["understood"], "feedback_id": row["id"]},
                    expires_at=local_at(now.date(), NUDGE_HOURS[1], self.clock.tz)))
            hour = feedback.quiet_pattern(self.conn, self.clock)
            if hour and self._once(f"quiet_suggestion:{now.isocalendar().year}-{now.isocalendar().week}"):
                out.append(Event("quiet_suggestion", 2, self._everyone(), {"around": hour},
                                 expires_at=local_at(now.date(), NUDGE_HOURS[1], self.clock.tz)))
        for pid in self._everyone() if self._daytime(now) else []:
            if (feedback.repeat_requests_today(self.conn, self.clock, pid) >= feedback.REPEATS_FOR_SUGGESTION
                    and settings.get(self.conn, "speech_rate", pid, self.clock) != settings.CHOICES["speech_rate"][0]
                    and self._once(f"slower_suggestion:{pid}:{now.isocalendar().year}-{now.isocalendar().week}")):
                out.append(Event("slower_suggestion", 2, [pid], {"person": self._call_name(pid)},
                                 expires_at=now + timedelta(hours=2)))
        return out

    def _daytime(self, now: datetime) -> bool:
        start, end = (local_at(now.date(), t, self.clock.tz) for t in NUDGE_HOURS)
        return elapsed(start, now) >= timedelta(0) and elapsed(now, end) > timedelta(0)

    # --- helpers ----------------------------------------------------------

    def _once(self, key: str) -> bool:
        """True the first time `key` is seen, ever (stored, survives restarts)."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO event_log (key, fired_at_utc) VALUES (?, ?)", (key, utc_iso(self.clock.now()))
        )
        return cur.rowcount == 1

    def _everyone(self) -> list[int]:
        return [r["id"] for r in self.conn.execute("SELECT id FROM people ORDER BY id")]

    def _consenting(self, kind: str) -> list[int]:
        return [pid for pid in self._everyone() if store.has_consent(self.conn, pid, kind)
                and not modes.not_home(self.conn, self.clock, pid)]

    def _call_name(self, pid: int) -> str:
        return self.conn.execute("SELECT call_name FROM people WHERE id = ?", (pid,)).fetchone()["call_name"]


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
