"""When Pepe speaks first (PEPE.md 4): the manners gate. Plain code, no Claude.

Every event from the engine goes into the outbox with a decision, the "manner":
  say     say it normally
  short   very short and soft (quiet mode, night, visitors)
  signal  a soft sound and at most their name, nothing about what it is
          (something private, with visitors there)
  family  a message for the family channel, never spoken
or it is held (said later, once the reason has passed) or dropped.

Items not yet said are decided again on every tick, so a change ("be quiet",
visitors arriving, going out) applies to what is already waiting. An item is
only done once the voice has said it (mark_done), so a power cut in between
means it is said after the restart rather than lost. mark_done also tells the
medicine records that a reminder was really said: only then can a dose with no
answer count as "missed" (and towards a family alert).

The rules, in order:
  1. Family alerts are messages, not speech. How a call for help went is said at
     once, whatever the time or mode (an emergency, PEPE.md 4).
  2. Nothing for people who are away. For someone out: their medicine reminder
     waits only while the room is empty (they may be back without saying so);
     other things for them wait until they're back.
  3. Category 2 (nudges, follow-ups) only when nothing else is going on, with
     gaps between them, a daily limit on conversation starters, and less after
     short answers, "not now" or silence. A follow-up waits for a better moment;
     a nudge is simply dropped.
  4. Private things in front of visitors: a signal, no words.
  5. Nobody in the room: wait (medicine reminders are said anyway; the sensor
     can be wrong and the engine retries).
  6. Night: medicine and the reminders they set themselves, short; the rest waits.
  7. Visitors: short. Quiet mode: urgent things short; the rest waits.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from kore.manners import modes
from kore.memory import meds, settings, store
from kore.memory.db import utc_iso
from kore.scheduler.clock import Clock, local_at
from kore.scheduler.engine import Event

SAY, SHORT, SIGNAL, FAMILY = "say", "short", "signal", "family"
READY, HELD, DONE, DROPPED = "ready", "held", "done", "dropped"

CAT2_MIN_GAP = timedelta(hours=1)
CAT2_MIN_GAP_AFTER_BRIEF = timedelta(hours=2)
IGNORED_WAIT = timedelta(hours=4)  # after a check-in is ignored, wait much longer
STARTERS_PER_DAY = 2  # PEPE.md 4: "two or three", whatever the setting
# Conversation starters, under the daily limit (and "how chatty" from feedback).
STARTERS = {"visit_follow_up", "feedback_check", "quiet_suggestion", "slower_suggestion"}

VISIT_FOLLOW_UP_AFTER = timedelta(minutes=30)
VISIT_FOLLOW_UP_WINDOW = timedelta(hours=3)
QUIET_OVER_WINDOW = timedelta(minutes=30)
WELCOME_WINDOW = timedelta(hours=12)
AWAY_CHECK_WINDOW = timedelta(minutes=30)

# Still said (short) in quiet mode; everything else waits until it ends.
URGENT = {"medication_reminder", "appointment_same_day", "appointment_day_before", "reminder",
          "clock_untrusted", "away_presence_check", "morning_greeting", "message_not_sent"}
# How a call for help went (kore/family): said at once, even at night or in quiet mode.
HELP_NEWS = {"help_sent_late", "help_not_sent"}
# Asked of an empty-looking home, or after someone comes back: not blocked by "away".
IGNORES_AWAY = {"away_presence_check", "welcome_back"}

REPLY_KINDS = {"warm", "brief", "not_now", "ignored"}


@dataclass
class Snapshot:
    """What the gate needs to know about the home right now."""
    night: bool = False
    quiet: bool = False
    visitors: bool = False
    presence: str = "unknown"  # moving | still | absent | unknown (after a restart)
    away: set[int] = field(default_factory=set)
    out: set[int] = field(default_factory=set)
    cat2_block: str | None = None  # why category 2 can't speak now, if it can't
    starters_left: int = STARTERS_PER_DAY


def decide(ev: Event, s: Snapshot) -> tuple[str, str | None, str]:
    """(status, manner, reason) for one event. Pure: the rules, nothing else."""
    k = ev.kind
    if k.startswith("family_"):
        return READY, FAMILY, "a message, not speech"
    if k in HELP_NEWS:
        return READY, SAY, "help"

    targets = set(ev.person_ids)
    if targets and k not in IGNORES_AWAY:
        if targets <= s.away:
            return DROPPED, None, "away"
        if targets <= s.away | s.out:
            if ev.category == 2:
                return DROPPED, None, "out"
            if k == "medication_reminder":
                if s.presence == "absent":
                    return HELD, None, "out"
                # Someone is in: maybe them, back without saying so. Fall through.
            else:
                return HELD, None, "out"

    if ev.category == 2:
        for blocked, why in ((s.night, "night"), (s.quiet, "quiet"), (s.visitors, "visitors"),
                             (s.presence == "absent", "nobody here")):
            if blocked:
                # "If people are still there, it waits and asks later" (PEPE.md 4).
                return (HELD if k in STARTERS else DROPPED), None, why
        if s.cat2_block:
            return DROPPED, None, s.cat2_block
        if k in STARTERS and s.starters_left <= 0:
            return DROPPED, None, "daily limit"
        return READY, SAY, ""

    if s.visitors and ev.private:
        return READY, SIGNAL, "visitors"
    if s.presence == "absent" and k != "medication_reminder":
        return HELD, None, "nobody here"
    if s.night and k != "clock_untrusted":  # "night" itself comes from the clock in doubt
        if k == "medication_reminder":
            if ev.data.get("attempt", 1) == 1:
                return READY, SHORT, "night"
            return DROPPED, None, "night: no retries"
        if k in ("reminder", "appointment_same_day"):
            return READY, SHORT, "night"
        if k == "appointment_day_before":
            return DROPPED, None, "night: the hour-before reminder will come"
        return HELD, None, "night"
    if s.visitors:
        if k == "quiet_over":
            return DROPPED, None, "visitors"
        return READY, SHORT, "visitors"
    if s.quiet:
        if k in URGENT:
            return READY, SHORT, "quiet"
        return HELD, None, "quiet"
    return READY, SAY, ""


@dataclass
class Item:
    id: int
    event: Event
    manner: str


class Manners:
    def __init__(self, conn: sqlite3.Connection, clock: Clock):
        self.conn = conn
        self.clock = clock
        self.presence = "unknown"

    # --- input ------------------------------------------------------------

    def submit(self, events: list[Event]) -> None:
        """Put engine events in the outbox, each with its decision."""
        for ev in events:
            if ev.key:
                self.conn.execute(
                    "UPDATE outbox SET status = 'dropped', reason = 'replaced' "
                    "WHERE key = ? AND status IN ('ready', 'held')", (ev.key,))
            cur = self.conn.execute(
                "INSERT INTO outbox (created_at_utc, kind, category, person_ids, data, private, key, "
                "not_before_utc, expires_at_utc, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'held')",
                (utc_iso(self.clock.now()), ev.kind, ev.category, json.dumps(ev.person_ids),
                 json.dumps(ev.data, ensure_ascii=False), int(ev.private), ev.key,
                 _iso(ev.not_before), _iso(ev.expires_at)),
            )
            self._redecide(self._row(cur.lastrowid))

    def on_presence(self, state: str) -> None:
        """Sensor: 'moving' | 'still' | 'absent'."""
        if not store.sensor_allowed(self.conn):
            self.presence = "unknown"  # someone said no to the sensor
            return
        prev, self.presence = self.presence, state
        if state == "absent" or not self.clock.trusted():
            return
        people = self._everyone()
        if prev == "absent" and len(people) == 1 and modes.active(self.conn, self.clock, "out", people[0]):
            # One person lives here: if someone came in, it's them.
            modes.end(self.conn, self.clock, "out", people[0], how="presence")
        if state == "moving" and not self.is_night():  # never wake anyone to ask
            self._check_away(people)

    def _check_away(self, people: list[int]) -> None:
        """Someone is moving at home while someone is meant to be away. Pausing medicine
        by mistake is worse than resuming it by mistake, so ask rather than wait."""
        now = self.clock.now()
        today = now.date()
        away = {p: r for p in people if (r := modes.active(self.conn, self.clock, "away", p))}
        if not away:
            return
        expires = _utc(now) + AWAY_CHECK_WINDOW
        if len(away) == len(people):
            due = [p for p, r in away.items() if r["expected_back"] and r["expected_back"] <= today.isoformat()]
            if due:
                # Someone at home on or after the day they said they'd be back: it's them.
                for p in due:
                    modes.end(self.conn, self.clock, "away", p, how="presence")
            elif self._once(f"away_check:{today}"):
                # Could be a carer: ask once a day.
                self.submit([Event("away_presence_check", 1, people, expires_at=expires)])
            return
        for p, r in away.items():
            # The other one is home: ask them about this person once a day, from the day they
            # said they'd be back (or from the third day, if they didn't say).
            started = modes.started_at(r, self.clock.tz).date()
            if (r["expected_back"] or (started + timedelta(days=2)).isoformat()) > today.isoformat():
                continue
            if self._once(f"away_check:{p}:{today}"):
                name = self.conn.execute("SELECT call_name FROM people WHERE id = ?", (p,)).fetchone()[0]
                self.submit([Event("away_presence_check", 1, [p], {"person": name}, expires_at=expires)])

    def note_reply(self, kind: str) -> None:
        """How they answered something Pepe started (from the conversation loop)."""
        if kind not in REPLY_KINDS:
            raise ValueError(kind)
        self.conn.execute("INSERT INTO reply_signals (at_utc, kind) VALUES (?, ?)",
                          (utc_iso(self.clock.now()), kind))

    # --- the loop ---------------------------------------------------------

    def tick(self) -> None:
        """Turn mode changes into events, then decide every open item again."""
        if not self.clock.trusted():
            # A wrong clock would end modes and expire items: leave them. Only the warning
            # itself is decided again (it waits for someone to be in the room).
            for row in self.conn.execute("SELECT * FROM outbox WHERE kind = 'clock_untrusted' "
                                         "AND status IN ('ready', 'held')").fetchall():
                self._redecide(row)
            return
        self.conn.execute("UPDATE outbox SET status = 'dropped', reason = 'clock fixed' "
                          "WHERE kind = 'clock_untrusted' AND status IN ('ready', 'held')")
        now = self.clock.now()
        modes.expire(self.conn, self.clock)
        for r in modes.ended_since(self.conn, _utc(now) - max(VISIT_FOLLOW_UP_WINDOW, WELCOME_WINDOW)):
            end = _utc(modes.ended_at(r, self.clock.tz))
            if r["kind"] == "quiet" and r["ended_how"] == "time" and self._once(f"quiet_over:{r['id']}"):
                # Time's up (not "you can talk now", and not a new "be quiet"): come back gently.
                self.submit([Event("quiet_over", 1, self._everyone(), expires_at=end + QUIET_OVER_WINDOW)])
            if r["kind"] == "visitors" and r["ended_how"] == "said" and self._once(f"visit_follow_up:{r['id']}"):
                people = [p for p in self._everyone() if store.has_consent(self.conn, p, "conversation_starters")]
                if people:
                    self.submit([Event("visit_follow_up", 2, people,
                                       not_before=end + VISIT_FOLLOW_UP_AFTER,
                                       expires_at=end + VISIT_FOLLOW_UP_WINDOW)])
            if r["kind"] == "away" and r["ended_how"] in ("said", "presence") and self._once(f"welcome:{r['id']}"):
                self.submit([Event("welcome_back", 1, [r["person_id"]], expires_at=end + WELCOME_WINDOW)])
        if self.presence == "moving" and not self.is_night():
            self._check_away(self._everyone())  # at most once a day each (stored keys)
        for row in self.conn.execute("SELECT * FROM outbox WHERE status IN ('ready', 'held') ORDER BY id").fetchall():
            self._redecide(row)

    def next_items(self) -> list[Item]:
        """What to say now, most important first. Call mark_done once each is said."""
        now = utc_iso(self.clock.now())
        rows = self.conn.execute(
            "SELECT * FROM outbox WHERE status = 'ready' AND (not_before_utc IS NULL OR not_before_utc <= ?) "
            "AND (expires_at_utc IS NULL OR expires_at_utc > ?) ORDER BY category, id", (now, now)).fetchall()
        return [Item(r["id"], _event(r), r["manner"]) for r in rows]

    def mark_done(self, item: Item) -> None:
        """The voice has said (or signalled) this item, in the manner it was given."""
        self.conn.execute(
            "UPDATE outbox SET status = 'done', manner = ?, done_at_utc = ? WHERE id = ? AND status IN ('ready', 'held')",
            (item.manner, utc_iso(self.clock.now()), item.id))
        ev = item.event
        if (ev.kind == "medication_reminder" and item.manner in (SAY, SHORT) and "slot_utc" in ev.data
                and not any(modes.not_home(self.conn, self.clock, p) for p in ev.person_ids)):
            # Said out loud with the medicine named, to someone known to be home: now a
            # missing answer can count as "missed". (Said while she was out, only her
            # partner may have heard it.)
            meds.mark_spoken(self.conn, [m["id"] for m in ev.data["medications"]], ev.data["slot_utc"])

    # --- state ------------------------------------------------------------

    def is_night(self, person_ids: list[int] | None = None) -> bool:
        """Night for any of these people (default: anyone at home). Each can have their own hours."""
        t = self.clock.now().strftime("%H:%M")  # wall clock: night hours are local times
        for p in person_ids or self._everyone() or [settings.HOME]:
            start = settings.get(self.conn, "night_start", p, self.clock)
            end = settings.get(self.conn, "night_end", p, self.clock)
            if settings.within(t, start, end):
                return True
        return False

    def is_rest_time(self) -> bool:
        hours = settings.get(self.conn, "rest_hours", settings.HOME, self.clock)
        return bool(hours) and settings.within(self.clock.now().strftime("%H:%M"), *hours.split("-"))

    def snapshot(self, targets: list[int] | None = None, exclude_id: int | None = None) -> Snapshot:
        people = self._everyone()
        block, left = self._category_2(targets or people, exclude_id)
        return Snapshot(
            night=self.is_night(targets),
            quiet=modes.active(self.conn, self.clock, "quiet") is not None or self.is_rest_time(),
            visitors=modes.active(self.conn, self.clock, "visitors") is not None,
            presence=self.presence,
            away={p for p in people if modes.active(self.conn, self.clock, "away", p)},
            out={p for p in people if modes.active(self.conn, self.clock, "out", p)},
            cat2_block=block,
            starters_left=left,
        )

    def _category_2(self, targets: list[int], exclude_id: int | None) -> tuple[str | None, int]:
        now = self.clock.now()
        day_start = utc_iso(local_at(now.date(), "00:00", self.clock.tz))
        signals = self.conn.execute("SELECT kind, at_utc FROM reply_signals WHERE at_utc >= ?",
                                    (day_start,)).fetchall()
        kinds = [s["kind"] for s in signals]
        allowed = self.conn.execute(
            "SELECT kind, allowed_at_utc FROM outbox WHERE category = 2 AND allowed_at_utc >= ? "
            "AND status IN ('ready', 'done') AND id IS NOT ?", (day_start, exclude_id)).fetchall()
        # How chatty they asked Pepe to be ("habla menos"): the least chatty of them wins.
        base = min((settings.STARTERS_FOR_CHATTINESS[settings.get(self.conn, "chattiness", p, self.clock)]
                    for p in targets), default=STARTERS_PER_DAY)
        left = (base + ("warm" in kinds and base > 0) - ("brief" in kinds)
                - sum(a["kind"] in STARTERS for a in allowed))
        if "not_now" in kinds:
            return "not now, today", 0
        ignored = [s["at_utc"] for s in signals if s["kind"] == "ignored"]
        if ignored and _since(max(ignored), now) < IGNORED_WAIT:
            return "ignored earlier", left
        gap = CAT2_MIN_GAP_AFTER_BRIEF if "brief" in kinds else CAT2_MIN_GAP
        if allowed and _since(max(a["allowed_at_utc"] for a in allowed), now) < gap:
            return "too soon after the last one", left
        return None, left

    # --- helpers ----------------------------------------------------------

    def _redecide(self, row: sqlite3.Row) -> None:
        now = self.clock.now()
        if row["expires_at_utc"] and row["expires_at_utc"] <= utc_iso(now):
            status, manner, reason = DROPPED, None, "expired"
        elif row["not_before_utc"] and row["not_before_utc"] > utc_iso(now):
            status, manner, reason = HELD, None, "not yet"
        else:
            ev = _event(row)
            status, manner, reason = decide(ev, self.snapshot(ev.person_ids, exclude_id=row["id"]))
        allowed = row["allowed_at_utc"]
        if status == READY and allowed is None:
            allowed = utc_iso(now)
        self.conn.execute("UPDATE outbox SET status = ?, manner = ?, reason = ?, allowed_at_utc = ? WHERE id = ?",
                          (status, manner, reason, allowed, row["id"]))

    def _row(self, item_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM outbox WHERE id = ?", (item_id,)).fetchone()

    def _once(self, key: str) -> bool:
        cur = self.conn.execute("INSERT OR IGNORE INTO event_log (key, fired_at_utc) VALUES (?, ?)",
                                (key, utc_iso(self.clock.now())))
        return cur.rowcount == 1

    def _everyone(self) -> list[int]:
        return [r["id"] for r in self.conn.execute("SELECT id FROM people ORDER BY id")]


def _event(row: sqlite3.Row) -> Event:
    return Event(row["kind"], row["category"], json.loads(row["person_ids"]), json.loads(row["data"]),
                 bool(row["private"]), row["key"])


def _iso(dt: datetime | None) -> str | None:
    return utc_iso(dt) if dt else None


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)  # real-time maths across DST


def _since(iso: str, now: datetime) -> timedelta:
    return now.astimezone(timezone.utc) - datetime.fromisoformat(iso)
