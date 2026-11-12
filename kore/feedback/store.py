"""Feedback about how Pepe behaves (BUILD-PLAN 2, "The feedback loop").

Claude spots feedback and says what it understood; this code decides what
happens to it, so the rules hold whatever Claude says:

- Applied at once, as a stored and reversible change: a setting (speed,
  volume, answer length, how chatty, quiet times, night hours) or a short
  written rule added to that person's prompt.
- For the person speaking only. Never for the other person.
- Scope: "now" (half an hour), "today" (until midnight) or "always" (the default).
- At most one follow-up question, and it can be left unanswered.
- Never weakens a safety rule: anything about medicines, doses, alerts, the
  doctor or emergencies is held for Lea, not applied. (Code-level safety -
  reminders, alerts - is never a setting or a rule in the first place.)
- Every item is listed for Lea, who can undo it, reword a rule or mark it
  misunderstood; undoing works from the next turn.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from kore.brain.language import fold
from kore.memory import settings
from kore.memory.db import from_utc_iso, utc_iso
from kore.scheduler.clock import Clock, local_at

NOW_LASTS = timedelta(minutes=30)
FOLLOW_UP_WITHIN = timedelta(minutes=30)  # an answer days later is not an answer
MAX_RULES_PER_PERSON = 10
MAX_RULE_CHARS = 200
CHECK_AFTER = timedelta(days=3)
SUGGEST_EVERY = timedelta(days=7)
QUIET_PATTERN_DAYS = 3
REPEATS_FOR_SUGGESTION = 3

KINDS = {"setting", "rule", "praise", "safety", "other"}
SCOPES = {"now", "today", "always"}

# Words that make a rule (or a setting request) a safety matter, in Spanish and
# English, matched on folded whole-word prefixes. False alarms only mean Lea
# looks at it first; a miss could mean Pepe stops mentioning someone's pills.
SAFETY_WORDS = [
    "pastill", "medic", "dosis", "dose", "pill", "tablet", "comprimid", "jarabe", "insulin", "sintrom",
    "alert", "alarm", "aviso", "avisa", "avises", "avisar", "emergenc", "urgenc", "socorro", "ayuda", "help",
    "112", "teleasistencia", "doctor", "medico", "farmac", "pharmac", "recordatori", "reminder", "remind",
    "caida", "caigo", "caer", "fall", "meds", "pildor", "inhal", "parche", "gotas", "recuerd", "record",
    "familia", "hija", "hijo", "nieta", "nieto", "ambulan", "hospital", "urgent", "emergency",
    # Scams (SR-09): "no me avises de estafas" must not switch off the warnings without Lea seeing it.
    "estaf", "timo", "timad", "engan", "fraud", "scam", "codig", "clave", "contrasen", "password", "banco",
    "bancari", "tarjeta", "bizum", "transferen",
]
# Matched as whole words only: as prefixes they would also catch everyday words ("pintar", "pinchar").
SAFETY_WHOLE_WORDS = {"pin", "pins"}
NIGHT_KEYS = {"night_start", "night_end"}


class FeedbackError(ValueError):
    pass


def touches_safety(*texts: str | None) -> bool:
    words = " ".join(fold(t or "") for t in texts).split()
    return any(w in SAFETY_WHOLE_WORDS or any(w.startswith(s) for s in SAFETY_WORDS) for w in words)


# --- recording -----------------------------------------------------------------


def record(conn: sqlite3.Connection, clock: Clock, *, speaker_id: int | None, about_id: int | None,
           said: str, understood: str, kind: str, setting_key: str | None = None, value: str | None = None,
           rule: str | None = None, scope: str = "always", replaces_rule_id: int | None = None,
           follow_up_question: str | None = None, home_wide: bool = False) -> dict:
    """Store one piece of feedback and apply it if allowed. Returns what happened."""
    if kind not in KINDS:
        raise FeedbackError(f"kind must be one of {sorted(KINDS)}")
    if scope not in SCOPES:
        raise FeedbackError(f"scope must be one of {sorted(SCOPES)}")
    said, understood = said.strip(), understood.strip()
    if not said or not understood:
        raise FeedbackError("said and understood are required")
    if kind in ("setting", "rule") and speaker_id is None:
        raise FeedbackError("a change needs to know who is speaking")
    base = dict(speaker_id=speaker_id, said=said, understood=understood, kind=kind, scope=scope,
                follow_up_question=follow_up_question, setting_key=setting_key, rule_text=rule)
    safety_words = touches_safety(rule, understood, said)

    # Never for the other person.
    if about_id is not None and speaker_id is not None and about_id != speaker_id:
        fid = _insert(conn, clock, person_id=about_id, status="not_applied", review_first=int(safety_words), **base)
        return {"id": fid, "applied": False, "status": "not_applied",
                "say": "Each person changes their own things. Say kindly that the other person can tell you "
                       "themselves."}

    # Never weaken a safety rule. Most settings can't touch safety by construction (none
    # of them is about medicines or alerts); a written rule, night hours or a rest time
    # could, so they're checked.
    if kind == "safety" or (kind == "rule" and safety_words):
        return _hold(conn, clock, base, about_id, HOLD_SAY_SAFETY)
    if kind == "setting":
        if setting_key not in settings.DEFAULTS:
            # Logged all the same, so Lea sees it (often "switch off the medicine reminders").
            if safety_words:
                return _hold(conn, clock, base, about_id, HOLD_SAY_SAFETY)
            fid = _insert(conn, clock, person_id=about_id, status="noted", **base)
            return {"id": fid, "applied": False, "status": "noted", "error": "unknown_setting",
                    "known": sorted(settings.DEFAULTS)}
        if setting_key in NIGHT_KEYS | {"rest_hours"} and safety_words:
            return _hold(conn, clock, base, about_id, HOLD_SAY_SAFETY)
        return _apply_setting(conn, clock, base, about_id, setting_key, value, scope, home_wide)
    if kind == "rule":
        return _apply_rule(conn, clock, base, about_id, rule, scope, replaces_rule_id)
    fid = _insert(conn, clock, person_id=about_id, status="noted", review_first=int(safety_words), **base)
    return {"id": fid, "applied": False, "status": "noted"}


HOLD_SAY_SAFETY = ("This touches their medicines, their safety or their night, so nothing changes now. Answer "
                   "kindly that it stays as it is for now (never promise reminders will be shorter or quieter), and "
                   "suggest mentioning it to their doctor or their family. Stopping a medicine goes through propose_medication_change.")
HOLD_SAY_NIGHT_SHARED = ("They share the room: a shorter night would wake the other person. Say kindly that you'll "
                         "keep it as it is for now, and they can agree it together.")
HOLD_SAY_NIGHT_MEDICINE = ("A medicine time would fall in the night, and night reminders are only said once, "
                           "softly. Keep it as it is; if the medicine time should change, that goes through "
                           "propose_medication_change.")


def _hold(conn, clock, base, about_id, say) -> dict:
    fid = _insert(conn, clock, person_id=about_id, status="held_safety", review_first=1,
                  **{**base, "kind": "safety"} if base["kind"] != "setting" else base)
    return {"id": fid, "applied": False, "status": "held_safety", "say": say}


def _apply_setting(conn, clock, base, about_id, key, value, scope, home_wide) -> dict:
    person = settings.HOME if (home_wide or key in settings.HOME_ONLY) else about_id
    if value in settings.STEPS:
        step_key, direction = settings.STEPS[value]
        if step_key != key:
            raise FeedbackError(f"{value!r} is a step for {step_key}, not {key}")
        # From the lasting value for "always" (not from a "just now" one), from what's in force otherwise.
        current = settings.get(conn, key, person, clock if scope != "always" else None)
        value = settings.step(key, current, direction)
    if value is None:
        raise FeedbackError("a setting needs a value")
    settings.check(key, value, person, conn)
    if key in NIGHT_KEYS:
        why = _night_problem(conn, key, value)
        if why:
            return _hold(conn, clock, base, about_id, why)
    person_id = None if person == settings.HOME else person
    review = int(key in settings.HOME_ONLY or home_wide)
    if scope == "always":
        previous = settings.stored(conn, key, person)
        fid = _insert(conn, clock, person_id=person_id, status="active", new_value=value, previous_value=previous,
                      review_first=review, **base | {"setting_key": key})
        _replace_older(conn, fid, key, person_id)
        settings.put(conn, clock, key, value, person)
    else:
        until = _until(clock, scope)
        fid = _insert(conn, clock, person_id=person_id, status="active", new_value=value, until_utc=utc_iso(until),
                      review_first=review, **base | {"setting_key": key})
        settings.put_for_now(conn, key, value, person, until, fid)
    return {"id": fid, "applied": True, "status": "active", "setting": key, "value": value, "scope": scope,
            "say": "Confirm in one short sentence and do it from now on."}


def _night_problem(conn, key, value) -> str | None:
    """Why a new night can't be applied by itself, or None."""
    old = {k: settings.get(conn, k) for k in NIGHT_KEYS}
    new = {**old, key: value}
    for times in conn.execute("SELECT m.times FROM medications m WHERE m.active = 1"):
        for t in json.loads(times[0]):
            if settings.within(t, new["night_start"], new["night_end"]) and not settings.within(
                    t, old["night_start"], old["night_end"]):
                return HOLD_SAY_NIGHT_MEDICINE
    people = conn.execute("SELECT count(*) FROM people").fetchone()[0]
    longer = (settings.from_noon(new["night_start"]) <= settings.from_noon(old["night_start"])
              and settings.from_noon(new["night_end"]) >= settings.from_noon(old["night_end"]))
    if people > 1 and not longer:
        return HOLD_SAY_NIGHT_SHARED
    return None


def _apply_rule(conn, clock, base, about_id, rule, scope, replaces_rule_id) -> dict:
    rule = (rule or "").strip()
    if not rule:
        raise FeedbackError("a rule needs its text")
    if len(rule) > MAX_RULE_CHARS:
        raise FeedbackError(f"keep the rule under {MAX_RULE_CHARS} characters")
    if about_id is None:
        raise FeedbackError("a rule is for a person: it needs to know who is speaking")
    lasting = [r for r in rules_for(conn, clock, about_id) if r["scope"] == "always"]
    if replaces_rule_id is not None:
        if scope != "always":
            raise FeedbackError("a rule for now or today doesn't replace a lasting one: leave replaces_rule_id out")
        if replaces_rule_id not in {r["id"] for r in lasting}:
            raise FeedbackError("replaces_rule_id is not one of this person's active rules")
    if scope == "always" and replaces_rule_id is None and len(lasting) >= MAX_RULES_PER_PERSON:
        return {"applied": False, "error": "too_many_rules",
                "rules": [{"id": r["id"], "rule": r["rule_text"]} for r in lasting],
                "say": "Merge this with the closest existing rule: call again with replaces_rule_id."}
    until = utc_iso(_until(clock, scope)) if scope != "always" else None
    fid = _insert(conn, clock, person_id=about_id, status="active", until_utc=until, **base | {"rule_text": rule})
    if replaces_rule_id is not None:
        conn.execute("UPDATE feedback SET status = 'replaced', replaced_by = ? WHERE id = ?", (fid, replaces_rule_id))
    return {"id": fid, "applied": True, "status": "active", "rule": rule, "scope": scope,
            "say": "Confirm in one short sentence and keep doing it."}


def follow_up(conn: sqlite3.Connection, clock: Clock, feedback_id: int, answer: str | None,
              scope: str | None = None) -> dict:
    """The answer to the one follow-up question (None: they didn't want to say). One only, and soon."""
    row = _row(conn, feedback_id)
    if row["follow_up_state"] != "asked":
        raise FeedbackError("only one follow-up question, and only if one was asked")
    if scope is not None and scope not in SCOPES:
        raise FeedbackError(f"scope must be one of {sorted(SCOPES)}")
    if _since(row["at_utc"], clock) > FOLLOW_UP_WITHIN:
        conn.execute("UPDATE feedback SET follow_up_state = 'dropped' WHERE id = ?", (feedback_id,))
        raise FeedbackError("that question was too long ago: it was dropped")
    if answer is None or not answer.strip():
        conn.execute("UPDATE feedback SET follow_up_state = 'dropped' WHERE id = ?", (feedback_id,))
        return {"ok": True, "say": "Drop it. Don't ask again."}
    conn.execute("UPDATE feedback SET follow_up_state = 'answered', follow_up_answer = ? WHERE id = ?",
                 (answer.strip(), feedback_id))
    if scope and scope != row["scope"] and row["status"] == "active":
        _rescope(conn, clock, row, scope)
    return {"ok": True}


def _rescope(conn, clock, row, scope) -> None:
    """'Just today' after all (or 'always'): put the change back the way it was, then apply it anew."""
    _revert(conn, clock, row)
    until = _until(clock, scope) if scope != "always" else None
    if row["kind"] == "setting":
        person = row["person_id"] if row["person_id"] is not None else settings.HOME
        if scope == "always":
            previous = settings.stored(conn, row["setting_key"], person)
            _replace_older(conn, row["id"], row["setting_key"], row["person_id"])
            settings.put(conn, clock, row["setting_key"], row["new_value"], person)
            conn.execute("UPDATE feedback SET previous_value = ? WHERE id = ?", (previous, row["id"]))
        else:
            settings.put_for_now(conn, row["setting_key"], row["new_value"], person, until, row["id"])
    conn.execute("UPDATE feedback SET scope = ?, until_utc = ?, status = 'active' WHERE id = ?",
                 (scope, utc_iso(until) if until else None, row["id"]))


# --- Lea's controls -------------------------------------------------------------


def undo(conn: sqlite3.Connection, clock: Clock, feedback_id: int, misunderstood: bool = False,
         note: str | None = None) -> None:
    row = _row(conn, feedback_id)
    if row["status"] not in ("active", "replaced"):
        if misunderstood or note:  # nothing to undo, but Lea's remark is kept
            conn.execute("UPDATE feedback SET misunderstood = ?, lea_note = coalesce(?, lea_note) WHERE id = ?",
                         (int(misunderstood or row["misunderstood"]), note, feedback_id))
            return
        raise FeedbackError(f"#{feedback_id} is {row['status']}: nothing to undo")
    if row["status"] == "active":
        if _revert(conn, clock, row):
            # Whatever this replaced is in force again.
            conn.execute("UPDATE feedback SET status = 'active', replaced_by = NULL WHERE replaced_by = ? "
                         "AND status = 'replaced'", (feedback_id,))
    elif row["kind"] == "setting" and row["replaced_by"]:
        # Undoing an older link of a chain: the newer one must no longer put this value back.
        conn.execute("UPDATE feedback SET previous_value = ? WHERE id = ?", (row["previous_value"], row["replaced_by"]))
        conn.execute("UPDATE feedback SET replaced_by = ? WHERE replaced_by = ? AND status = 'replaced'",
                     (row["replaced_by"], feedback_id))
    conn.execute("UPDATE feedback SET status = 'undone', misunderstood = ?, lea_note = coalesce(?, lea_note) "
                 "WHERE id = ?", (int(misunderstood or row["misunderstood"]), note, feedback_id))


def reword(conn: sqlite3.Connection, feedback_id: int, text: str) -> None:
    row = _row(conn, feedback_id)
    if row["kind"] != "rule" or row["status"] != "active":
        raise FeedbackError("only an active rule can be reworded")
    text = text.strip()
    if not text or len(text) > MAX_RULE_CHARS:
        raise FeedbackError("empty or too long")
    conn.execute("UPDATE feedback SET rule_text = ?, lea_note = ? WHERE id = ?",
                 (text, f"reworded from: {row['rule_text']}", feedback_id))


def _revert(conn, clock, row) -> bool:
    """Put back what this change replaced. Returns whether this change was the one in force."""
    if row["kind"] != "setting":
        return True
    person = row["person_id"] if row["person_id"] is not None else settings.HOME
    if row["scope"] == "always":
        # Only if nothing newer changed it since.
        if settings.stored(conn, row["setting_key"], person) != row["new_value"]:
            return False
        settings.put(conn, clock, row["setting_key"], row["previous_value"], person)
        return True
    conn.execute("DELETE FROM setting_overrides WHERE feedback_id = ?", (row["id"],))
    return True


# --- what the rest of Pepe reads ------------------------------------------------


def rules_for(conn: sqlite3.Connection, clock: Clock, person_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM feedback WHERE kind = 'rule' AND status = 'active' AND person_id = ? "
        "AND (until_utc IS NULL OR until_utc > ?) ORDER BY id",
        (person_id, utc_iso(clock.now())),
    ).fetchall()


ANSWER_LENGTH_LINES = {
    "short": "Keep answers very short with {name}: one or two sentences.",
    "long": "{name} enjoys fuller answers: you can say a little more.",
}


def prompt_lines(conn: sqlite3.Connection, clock: Clock, people: dict[int, str]) -> list[str]:
    """What the feedback adds to the state block, for the people in the conversation."""
    lines = []
    for pid, name in people.items():
        length = settings.get(conn, "answer_length", pid, clock)
        if length in ANSWER_LENGTH_LINES:
            lines.append(ANSWER_LENGTH_LINES[length].format(name=name))
        lines += [f"{name}: {r['rule_text']}" for r in rules_for(conn, clock, pid)]
    return lines


def voice_settings(conn: sqlite3.Connection, clock: Clock, person_ids: list[int]) -> dict:
    """For the voice. Talking to several people (or nobody known): the slowest and loudest anyone asked for."""
    ids = person_ids or [r["id"] for r in conn.execute("SELECT id FROM people")] or [settings.HOME]
    rates = [settings.get(conn, "speech_rate", p, clock) for p in ids]
    volumes = [settings.get(conn, "volume", p, clock) for p in ids]
    return {"speech_rate": min(rates, key=settings.CHOICES["speech_rate"].index),
            "volume": max(volumes, key=settings.CHOICES["volume"].index)}


# --- later: the light check, and things noticed ------------------------------------


def note_signal(conn: sqlite3.Connection, clock: Clock, person_id: int | None, kind: str) -> None:
    conn.execute("INSERT INTO behaviour_signals (at_utc, person_id, kind) VALUES (?, ?, ?)",
                 (utc_iso(clock.now()), person_id, kind))


def due_check(conn: sqlite3.Connection, clock: Clock) -> sqlite3.Row | None:
    """One change, a few days old and still active, to check on lightly. Marked as checked."""
    now = clock.now()
    if conn.execute("SELECT 1 FROM feedback WHERE checked_at_utc >= ?",
                    (utc_iso(local_at(now.date(), "00:00", clock.tz)),)).fetchone():
        return None  # one such check a day, for the whole home
    row = conn.execute(
        "SELECT * FROM feedback WHERE status = 'active' AND kind IN ('setting', 'rule') AND scope = 'always' "
        "AND checked_at_utc IS NULL AND at_utc <= ? ORDER BY id LIMIT 1",
        (utc_iso(_utc(now) - CHECK_AFTER),),
    ).fetchone()
    if row:
        conn.execute("UPDATE feedback SET checked_at_utc = ? WHERE id = ?", (utc_iso(now), row["id"]))
    return row


def quiet_pattern(conn: sqlite3.Connection, clock: Clock) -> str | None:
    """'Be quiet' around the same hour on each of the last few days: the hour ('16:00'), else None."""
    if settings.get(conn, "rest_hours"):
        return None
    today = clock.now().date()
    hours = []
    for back in range(1, QUIET_PATTERN_DAYS + 1):  # full days: today's afternoon may not have come yet
        day = today - timedelta(days=back)
        start, end = local_at(day, "00:00", clock.tz), local_at(day + timedelta(days=1), "00:00", clock.tz)
        rows = conn.execute(
            "SELECT started_at_utc FROM modes WHERE kind = 'quiet' AND started_at_utc >= ? AND started_at_utc < ?",
            (utc_iso(start), utc_iso(end))).fetchall()
        hours.append({from_utc_iso(r["started_at_utc"], clock.tz).hour for r in rows})
    common = set.intersection(*hours)
    return f"{min(common):02d}:00" if common else None


def repeat_requests_today(conn: sqlite3.Connection, clock: Clock, person_id: int | None) -> int:
    start = utc_iso(local_at(clock.now().date(), "00:00", clock.tz))
    return conn.execute("SELECT count(*) FROM behaviour_signals WHERE kind = 'repeat_request' "
                        "AND person_id IS ? AND at_utc >= ?", (person_id, start)).fetchone()[0]


# --- helpers ----------------------------------------------------------------------


def _insert(conn, clock, *, person_id, status, review_first=0, new_value=None, previous_value=None,
            until_utc=None, speaker_id, said, understood, kind, scope, follow_up_question, setting_key,
            rule_text) -> int:
    return conn.execute(
        "INSERT INTO feedback (at_utc, speaker_id, person_id, said, understood, kind, setting_key, new_value, "
        "previous_value, rule_text, scope, until_utc, follow_up_question, follow_up_state, status, review_first) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (utc_iso(clock.now()), speaker_id, person_id, said, understood, kind, setting_key, new_value,
         previous_value, rule_text, scope, until_utc, follow_up_question,
         "asked" if follow_up_question else "none", status, review_first),
    ).lastrowid


def _replace_older(conn, fid, key, person_id) -> None:
    conn.execute("UPDATE feedback SET status = 'replaced', replaced_by = ? WHERE status = 'active' "
                 "AND kind = 'setting' AND scope = 'always' AND id != ? AND setting_key = ? AND person_id IS ?",
                 (fid, fid, key, person_id))


def _row(conn, feedback_id) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
    if row is None:
        raise FeedbackError(f"no feedback {feedback_id}")
    return row


def _until(clock: Clock, scope: str) -> datetime:
    now = clock.now()
    if scope == "now":
        return _utc(now) + NOW_LASTS
    return local_at(now.date() + timedelta(days=1), "00:00", clock.tz)  # "today": until midnight


def _since(iso: str, clock: Clock) -> timedelta:
    return _utc(clock.now()) - datetime.fromisoformat(iso)


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)

