"""Lea's console (docs/CONSOLE-PLAN.md, from her design canvas): what the operator login shows.

Privacy first (WEB-PLAN decision 5): device health, costs and settings are always shown; anything about a
person only with that person's permission, asked by Pepe at setup:
- `lea_sees_day`: their reminders, medicines and doses, appointments, notes, people in their life, and the
  feedback they gave about how Pepe behaves;
- `lea_reads_conversations`: their conversations of the last 7 days (only what is kept: the web chat, and
  voice turns only with their `keep_conversations` permission).
Shared things (shared appointments, reminders and birthdays, the shopping list) show only when everyone at home
agreed. Feedback that touched safety always reaches Lea (it is held for her), whatever the permissions.

Lea's own actions go through code like everything else: a medicine change or an appointment she proposes is
only a proposal the person confirms (their read-back and yes); undoing feedback uses the feedback store.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from kore.brain.costs import CostLog
from kore.family import backup
from kore.family import messages as family_msgs
from kore.feedback import page as feedback_page
from kore.memory import meds, settings, store
from kore.memory.db import from_utc_iso, utc_iso
from kore.scheduler.clock import Clock
from kore.web import agenda

LEA_DAYS = 7
CONVERSATION_GAP = timedelta(minutes=30)
LOOP_SEEN = "loop_seen_at"  # written by the voice loop (kore.home) on each tick; never written: "not started"
PROPOSAL_LIFE = timedelta(days=7)
PERMISSION_ROWS = [
    ("presence_sensor", "Presence sensor greets them"),
    ("health_info", "Keep medicines and appointments"),
    ("family_missed_meds", "Tell family about missed doses"),
    ("emergency_path", "“Help” messages family without asking"),
    ("share_with_partner", "The other one may hear about their medicine"),
    ("keep_conversations", "Keep conversations to read on their phone (90 days)"),
    ("lea_sees_day", "Lea can see their day, memory and feedback"),
    ("lea_reads_conversations", "Lea can read conversations (7 days)"),
    ("family_sees_doses", "Family can see today’s doses"),
]


# --- who agreed to what ------------------------------------------------------------------------


def people(conn) -> list[dict]:
    out = []
    for r in conn.execute("SELECT id, name, call_name FROM people ORDER BY id"):
        out.append({"id": r["id"], "name": r["call_name"] or r["name"], "full": r["name"],
                    "day": store.has_consent(conn, r["id"], "lea_sees_day"),
                    "chats": store.has_consent(conn, r["id"], "lea_reads_conversations")})
    return out


def _day_ok(conn) -> set[int]:
    return {p["id"] for p in people(conn) if p["day"]}


def _shared_ok(conn) -> bool:
    ps = people(conn)
    return bool(ps) and all(p["day"] for p in ps)


def home_name(conn) -> str:
    names = [p["name"] for p in people(conn)]
    return (" & ".join(names) + "’s home") if names else "The home"


# --- the box ---------------------------------------------------------------------------------------


def costs(log: CostLog, now: datetime) -> dict:
    today = now.astimezone(timezone.utc).date().isoformat()
    month = today[:7]
    day_usd = month_usd = 0.0
    if log.path.exists():
        for line in log.path.read_text().splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("at", "").startswith(month):
                month_usd += entry.get("usd", 0)
                if entry["at"].startswith(today):
                    day_usd += entry.get("usd", 0)
    return {"today": day_usd, "month": month_usd}


def device(conn, clock: Clock, log: CostLog) -> dict:
    seen = family_msgs.get_state(conn, LOOP_SEEN)
    loop = None
    if seen:
        try:
            ago = clock.now() - from_utc_iso(seen, clock.tz)
            loop = {"ago_min": int(ago.total_seconds() // 60), "online": ago < timedelta(minutes=15)}
        except ValueError:
            loop = None
    last_backup = family_msgs.get_state(conn, backup.LAST_SENT)
    return {"loop": loop, "clock_ok": clock.trusted(), "backup": last_backup, "costs": costs(log, clock.now())}


# --- the day and the week ---------------------------------------------------------------------------


def _doses(conn, clock: Clock, pid: int, day: date) -> list[dict]:
    try:
        return meds.status(conn, clock, pid, day)["doses"]
    except meds.ClockUntrusted:
        return []


def today(conn, clock: Clock) -> dict:
    """The Overview's "Today": only for people who agreed."""
    now = clock.now()
    allowed = _day_ok(conn)
    names = {p["id"]: p["name"] for p in people(conn)}
    items, taken, total, nxt = [], 0, 0, None
    for pid in sorted(allowed):
        for d in _doses(conn, clock, pid, now.date()):
            total += 1
            state = d["state"]
            if state == "taken":
                taken += 1
                pill, label = "ok", f"Taken {d.get('taken_at', '')}".strip()
            elif state == "upcoming" or state == "not_recorded_yet":
                pill, label = "wait", "Upcoming" if state == "upcoming" else "Due now"
                nxt = nxt or (d["time"], names[pid])
            else:
                pill, label = "", "Not recorded"
            items.append({"time": d["time"], "what": f"{d['description'] or d['medication']} · {names[pid]}",
                          "pill": pill, "label": label})
    for a in store.appointments(conn, clock, None, upcoming=False):
        if a["starts_at"].date() != now.date():
            continue
        owner = a["person_id"]
        if (owner is None and _shared_ok(conn)) or owner in allowed:
            items.append({"time": a["starts_at"].strftime("%H:%M"),
                          "what": f"{a['title']} · {names.get(owner, 'both')}",
                          "pill": "wait" if a["starts_at"] > now else "", "label":
                          "Upcoming" if a["starts_at"] > now else "Done"})
    items.sort(key=lambda i: i["time"])
    if nxt is None:
        nxt = next(((i["time"], i["what"].split(" · ")[-1]) for i in items if i["pill"] == "wait"), None)
    return {"items": items, "taken": taken, "total": total, "next": nxt, "anyone": bool(allowed)}


def week(conn, clock: Clock, start: date) -> dict:
    """Seven days from Monday `start`: medicines (with what was recorded), appointments, reminders, birthdays."""
    allowed = _day_ok(conn)
    shared = _shared_ok(conn)
    names = {p["id"]: p["name"] for p in people(conn)}
    now = clock.now()
    days = []
    for i in range(7):
        day = start + timedelta(days=i)
        evs = []
        for pid in sorted(allowed):
            for d in _doses(conn, clock, pid, day):
                note = {"taken": f"Taken {d.get('taken_at', '')}".strip(), "upcoming": "Upcoming",
                        "not_recorded_yet": "Due now"}.get(d["state"], "Not recorded")
                evs.append({"kind": "med", "time": d["time"], "text": f"{d['medication']} · {names[pid]}",
                            "note": note, "ok": d["state"] == "taken"})
        for a in store.appointments(conn, clock, None, upcoming=False):
            if a["starts_at"].date() == day and ((a["person_id"] is None and shared) or a["person_id"] in allowed):
                evs.append({"kind": "appt", "time": a["starts_at"].strftime("%H:%M"),
                            "text": f"{a['title']} · {names.get(a['person_id'], 'both')}", "note": a["location"]})
        for r in store.reminders(conn):
            if not ((r["person_id"] is None and shared) or r["person_id"] in allowed):
                continue
            try:
                if store.reminder_occurs_on(r["rule"], date.fromisoformat(r["first_day"]), day):
                    evs.append({"kind": "rem", "time": r["time"], "text": r["text"],
                                "note": agenda.rule_words(r["rule"], date.fromisoformat(r["first_day"]))})
            except ValueError:
                continue
        md = day.strftime("%m-%d")
        for c in conn.execute("SELECT name, relation, for_person FROM contacts WHERE birthday_reminders = 1 AND "
                              "birthday_md = ?", (md,)):
            if (c["for_person"] is None and shared) or c["for_person"] in allowed:
                evs.append({"kind": "bday", "time": "", "text": f"{c['name']}’s birthday", "note": c["relation"]})
        evs.sort(key=lambda e: (e["time"] == "", e["time"]))
        days.append({"date": day, "today": day == now.date(), "events": evs})
    return {"days": days, "start": start, "anyone": bool(allowed)}


def med_schedule(conn) -> list[dict]:
    allowed = _day_ok(conn)
    names = {p["id"]: p["name"] for p in people(conn)}
    return [{"id": m["id"], "person": names[m["person_id"]], "person_id": m["person_id"], "name": m["name"],
             "description": m["description"], "times": json.loads(m["times"])}
            for m in meds.medications(conn) if m["person_id"] in allowed]


def repeating(conn) -> list[str]:
    allowed, shared = _day_ok(conn), _shared_ok(conn)
    out = []
    for r in store.reminders(conn):
        if (r["person_id"] is None and shared) or r["person_id"] in allowed:
            out.append(f"{r['text']} · {agenda.rule_words(r['rule'], date.fromisoformat(r['first_day']))} "
                       f"{r['time']}")
    for c in conn.execute("SELECT name, relation, birthday_md, for_person FROM contacts WHERE birthday_md IS NOT NULL "
                          "AND birthday_reminders = 1 ORDER BY birthday_md"):
        if (c["for_person"] is None and shared) or c["for_person"] in allowed:
            m, d = (int(x) for x in c["birthday_md"].split("-"))
            out.append(f"{c['name']}{' (' + c['relation'] + ')' if c['relation'] else ''} · "
                       f"{date(2024, m, d).strftime('%-d %B')}")
    return out


def shopping(conn) -> list[str] | None:
    return store.list_items(conn) if _shared_ok(conn) else None


# --- Lea's proposals (a medicine change, an appointment): the person decides ----------------------------


def propose_medicine_change(conn, clock: Clock, medication_id: int, times_text: str) -> str:
    row = conn.execute("SELECT * FROM medications WHERE id = ? AND active = 1", (medication_id,)).fetchone()
    if row is None or row["person_id"] not in _day_ok(conn):
        raise agenda.Refused("That medicine isn't there (or they haven't agreed you can see it).")
    try:
        times = meds._valid_times([t.strip() for t in times_text.replace(";", ",").split(",") if t.strip()])
    except ValueError:
        raise agenda.Refused("Write the new times like 09:00, 21:30.") from None
    if not times:
        raise agenda.Refused("Write the new times like 09:00, 21:30.")
    if times == json.loads(row["times"]):
        raise agenda.Refused("Those are already its times.")
    conn.execute("UPDATE operator_proposals SET status = 'withdrawn' WHERE kind = 'medication' AND status = 'open' "
                 "AND json_extract(payload, '$.medication_id') = ?", (medication_id,))
    conn.execute("INSERT INTO operator_proposals (person_id, kind, payload, created_at) VALUES (?, 'medication', ?, ?)",
                 (row["person_id"], json.dumps({"medication_id": medication_id, "name": row["name"], "times": times}),
                  utc_iso(clock.now())))
    return f"Proposed: {row['name']} at {', '.join(times)}. Pepe will ask them; nothing changes until they say yes."


def propose_appointment(conn, clock: Clock, f: dict) -> str:
    agenda._usable_clock(clock)
    allowed = _day_ok(conn)
    target = f.get("for") or ""
    ids = {str(p["id"]): p for p in people(conn)}
    if target == "both":
        if not _shared_ok(conn):
            raise agenda.Refused("Both of them need to have agreed you can see their day.")
        person = None
    elif target in ids and int(target) in allowed:
        person = int(target)
    else:
        raise agenda.Refused("Choose who it's for (someone who agreed you can see their day).")
    title = agenda.clean(f.get("title"), agenda.TITLE_CHARS)
    if not title:
        raise agenda.Refused("Write what it is.")
    try:
        at = agenda._local(agenda._day(f.get("day")), agenda._time(f.get("time")), clock)
        agenda._future(clock, at)
    except agenda.Refused:
        raise agenda.Refused("Choose a date and time in the future (within two years).") from None
    conn.execute("INSERT INTO operator_proposals (person_id, kind, payload, created_at) VALUES (?, 'appointment', ?, ?)",
                 (person, json.dumps({"title": title, "starts_at": at.isoformat(),
                                      "location": agenda.clean(f.get("where"), agenda.TITLE_CHARS)}),
                  utc_iso(clock.now())))
    return f"Proposed: {title}. They'll see it on their page and decide; Pepe saves it only with their yes."


def settle_proposals(conn, clock: Clock) -> None:
    """A medicine change is done once the medicine has those times; anything open for a week lapses."""
    for r in conn.execute("SELECT * FROM operator_proposals WHERE status = 'open'").fetchall():
        p = json.loads(r["payload"])
        if r["kind"] == "medication":
            m = conn.execute("SELECT times, active FROM medications WHERE id = ?", (p["medication_id"],)).fetchone()
            if m and m["active"] and json.loads(m["times"]) == p["times"]:
                conn.execute("UPDATE operator_proposals SET status = 'accepted', answered_at = ? WHERE id = ?",
                             (utc_iso(clock.now()), r["id"]))
                continue
        if clock.now() - from_utc_iso(r["created_at"], clock.tz) > PROPOSAL_LIFE:
            conn.execute("UPDATE operator_proposals SET status = 'lapsed', answered_at = ? WHERE id = ?",
                         (utc_iso(clock.now()), r["id"]))


def lea_proposals(conn, clock: Clock) -> list[dict]:
    settle_proposals(conn, clock)
    names = {p["id"]: p["name"] for p in people(conn)}
    out = []
    for r in conn.execute("SELECT * FROM operator_proposals ORDER BY id DESC LIMIT 10"):
        p = json.loads(r["payload"])
        what = (f"{p['name']} at {', '.join(p['times'])}" if r["kind"] == "medication" else
                f"{p['title']} · {from_utc_iso(utc_iso(datetime.fromisoformat(p['starts_at'])), clock.tz).strftime('%-d %b %H:%M')}")
        out.append({"what": what, "for": names.get(r["person_id"], "both"), "status": r["status"]})
    return out


def open_medicine_proposals(conn, clock: Clock, person_id: int) -> list[dict]:
    """For Pepe's state line and the person's Hoy page."""
    settle_proposals(conn, clock)
    return [json.loads(r["payload"]) | {"id": r["id"]} for r in conn.execute(
        "SELECT * FROM operator_proposals WHERE kind = 'medication' AND status = 'open' AND person_id = ?",
        (person_id,))]


# --- conversations (with lea_reads_conversations) --------------------------------------------------------


def conversations(conn, clock: Clock) -> list[dict]:
    """The last 7 days, cut into conversations at 30 minutes of quiet, for people who agreed."""
    since = utc_iso(clock.now() - timedelta(days=LEA_DAYS))
    allowed = {p["id"]: p["name"] for p in people(conn) if p["chats"]}
    out: list[dict] = []
    for pid, name in allowed.items():
        rows = conn.execute("SELECT * FROM chat WHERE person_id = ? AND at_utc >= ? ORDER BY id",
                            (pid, since)).fetchall()
        current = None
        for r in rows:
            at = from_utc_iso(r["at_utc"], clock.tz)
            if current is None or at - current["last"] > CONVERSATION_GAP:
                current = {"person_id": pid, "person": name, "first": r["id"], "start": at, "last": at,
                           "title": "", "channel": r["channel"], "lines": 0}
                out.append(current)
            current["last"] = at
            current["lines"] += 1
            if not current["title"] and r["who"] == "person" and r["text"]:
                words = r["text"].split()
                current["title"] = " ".join(words[:6]) + ("…" if len(words) > 6 else "")
    for c in out:
        c["title"] = c["title"] or ("A photo" if c["lines"] else "Conversation")
    return sorted(out, key=lambda c: c["start"], reverse=True)


def conversation(conn, clock: Clock, person_id: int, first_id: int) -> dict | None:
    for c in conversations(conn, clock):
        if c["person_id"] == person_id and c["first"] == first_id:
            rows = conn.execute("SELECT * FROM chat WHERE person_id = ? AND id >= ? AND at_utc <= ? ORDER BY id",
                                (person_id, first_id, utc_iso(c["last"]))).fetchall()
            c["rows"] = [{"who": r["who"], "text": r["text"], "channel": r["channel"], "photo": r["photo_id"] is not None,
                          "at": from_utc_iso(r["at_utc"], clock.tz)} for r in rows]
            return c
    return None


def delete_conversation(conn, clock: Clock, person_id: int, first_id: int) -> bool:
    c = conversation(conn, clock, person_id, first_id)
    if c is None:
        return False
    last_id = max(r["id"] for r in conn.execute("SELECT id FROM chat WHERE person_id = ? AND id >= ? AND at_utc <= ?",
                                                (person_id, first_id, utc_iso(c["last"]))))
    conn.execute("DELETE FROM chat WHERE person_id = ? AND id BETWEEN ? AND ?", (person_id, first_id, last_id))
    conn.execute("DELETE FROM console_marks WHERE person_id = ? AND chat_id BETWEEN ? AND ?",
                 (person_id, first_id, last_id))  # Lea's notes about it go too (they may quote it)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return True


def save_as_scenario_draft(conn, clock: Clock, person_id: int, first_id: int, folder: Path) -> Path | None:
    """A draft for a test scenario, in private/ (git-ignored: real words never go in git). Lea rewrites it with
    made-up names before it becomes a scenario."""
    c = conversation(conn, clock, person_id, first_id)
    if c is None:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"draft-{clock.now().strftime('%Y%m%d-%H%M%S')}-{first_id}.json"
    path.write_text(json.dumps({"note": "Real words: rewrite with made-up names before using as a scenario.",
                                "turns": [{"who": r["who"], "text": r["text"]} for r in c["rows"]]},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def mark_problem(conn, clock: Clock, person_id: int, first_id: int, note: str) -> bool:
    if conversation(conn, clock, person_id, first_id) is None:
        return False
    conn.execute("INSERT INTO console_marks (person_id, chat_id, note, at_utc) VALUES (?, ?, ?, ?)",
                 (person_id, first_id, agenda.clean(note, 300) or "A problem", utc_iso(clock.now())))
    return True


def marks(conn, person_id: int, first_id: int) -> list[str]:
    return [r["note"] for r in conn.execute("SELECT note FROM console_marks WHERE person_id = ? AND chat_id = ? "
                                            "ORDER BY id", (person_id, first_id))]


# --- feedback --------------------------------------------------------------------------------------------


def feedback(conn, clock: Clock, who: str = "all", state: str = "all") -> list[dict]:
    allowed = _day_ok(conn)
    out = []
    for r in feedback_page.items(conn):
        about = r["person_id"] if r["person_id"] is not None else r["speaker_id"]
        safety = bool(r["review_first"]) or r["status"] == "held_safety"
        if not safety and about not in allowed and not (about is None and _shared_ok(conn)):
            continue
        if who != "all" and str(about) != who:
            continue
        if state == "active" and r["status"] != "active" or state == "undone" and r["status"] != "undone":
            continue
        follow = None
        if r["follow_up_question"]:
            follow = (r["follow_up_question"], r["follow_up_answer"] or
                      ("no answer, dropped" if r["follow_up_state"] == "dropped" else "…"))
        out.append({"id": r["id"], "who": r["speaker"] or "?", "about": r["person"],
                    "at": from_utc_iso(r["at_utc"], clock.tz), "said": r["said"], "understood": r["understood"],
                    "change": feedback_page.change_text(r), "status": r["status"],
                    "status_words": feedback_page.STATUS_WORDS[r["status"]], "safety": safety,
                    "kind": r["kind"], "follow": follow, "note": r["lea_note"], "misunderstood": bool(r["misunderstood"])})
    return out


def needs_you(conn, clock: Clock) -> int:
    return sum(1 for f in feedback(conn, clock) if f["safety"] and f["status"] == "held_safety")


# --- people and memory (with lea_sees_day) -----------------------------------------------------------------


def memory(conn, clock: Clock) -> list[dict]:
    from kore.feedback import store as fb

    out = []
    for p in people(conn):
        entry = {"id": p["id"], "name": p["name"], "allowed": p["day"]}
        if p["day"]:
            entry["notes"] = store.notes(conn, p["id"])
            entry["contacts"] = [dict(r) for r in conn.execute(
                "SELECT name, relation, birthday_md, help_contact FROM contacts WHERE for_person = ? ORDER BY name",
                (p["id"],))]
            entry["rules"] = [r["rule_text"] for r in fb.rules_for(conn, clock, p["id"])]
            entry["switches"] = {"Conversation starters": store.has_consent(conn, p["id"], "conversation_starters"),
                                 "Family alerts": store.has_consent(conn, p["id"], "family_missed_meds"),
                                 "Help without asking": store.has_consent(conn, p["id"], "emergency_path")}
        out.append(entry)
    return out


def forget_note(conn, person_id: int, note_id: int) -> bool:
    if person_id not in _day_ok(conn):
        return False
    return store.forget_note(conn, person_id, note_id)


def shared_people(conn) -> dict | None:
    if not _shared_ok(conn):
        return None
    return {"contacts": [dict(r) for r in conn.execute(
        "SELECT name, relation, help_contact, telegram_chat_id IS NOT NULL AS tg, phone IS NOT NULL AS sms, "
        "whatsapp_number IS NOT NULL AS wa "
        "FROM contacts WHERE for_person IS NULL ORDER BY name")], "shopping": store.list_items(conn)}


# --- settings ----------------------------------------------------------------------------------------------


def permissions(conn) -> dict:
    ps = people(conn)
    rows = []
    for kind, label in PERMISSION_ROWS:
        cells = []
        for p in ps:
            row = conn.execute("SELECT granted FROM consents WHERE person_id = ? AND kind = ?", (p["id"], kind)).fetchone()
            cells.append("Yes" if row and row["granted"] else "No" if row else "Not asked")
        rows.append((label, cells))
    return {"people": [p["name"] for p in ps], "rows": rows}


def quiet_times(conn) -> dict:
    return {"night_start": settings.get(conn, "night_start"), "night_end": settings.get(conn, "night_end")}


def set_quiet_times(conn, clock: Clock, start: str, end: str) -> None:
    for key, value in (("night_start", start), ("night_end", end)):
        try:
            settings.check(key, value, conn=conn)
        except ValueError:
            raise agenda.Refused("Night must start between 20:00 and 01:00 and end between 06:00 and 10:00 "
                                 "(times like 23:00).") from None
    settings.put(conn, clock, "night_start", start)
    settings.put(conn, clock, "night_end", end)


def contacts(conn) -> list[dict]:
    """Family contacts Lea may see: shared ones when everyone agreed, and those of people who agreed."""
    names = {p["id"]: p["name"] for p in people(conn)}
    allowed, shared = _day_ok(conn), _shared_ok(conn)
    return [{"name": r["name"], "relation": r["relation"], "for": names.get(r["for_person"], "both"),
             "tg": bool(r["telegram_chat_id"]), "sms": bool(r["phone"]), "help": bool(r["help_contact"]),
             "wa": bool(r["whatsapp_number"]),
             "web": conn.execute("SELECT 1 FROM web_logins l JOIN web_accounts a ON a.id = l.account_id WHERE "
                                 "a.role = 'family' AND a.contact_id = ? AND l.revoked_at IS NULL",
                                 (r["id"],)).fetchone() is not None}
            for r in conn.execute("SELECT * FROM contacts WHERE help_contact = 1 OR telegram_chat_id IS NOT NULL OR whatsapp_number IS NOT NULL "
                                  "OR "
                                  "id IN (SELECT contact_id FROM web_accounts WHERE role = 'family') ORDER BY name")
            if (r["for_person"] is None and shared) or r["for_person"] in allowed]


def messages_today(conn, clock: Clock) -> int | None:
    """Only for people who let Lea read their conversations (None: nobody did)."""
    allowed = [p["id"] for p in people(conn) if p["chats"]]
    if not allowed:
        return None
    start = clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return conn.execute("SELECT count(*) FROM chat WHERE who = 'person' AND at_utc >= ? AND person_id IN (%s)"
                        % ",".join("?" * len(allowed)), (utc_iso(start), *allowed)).fetchone()[0]


# --- numbers, events, version ---------------------------------------------------------------------------


def week_numbers(conn, clock: Clock, log: CostLog) -> dict:
    now = clock.now()
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    allowed = _day_ok(conn)
    out = {}
    if allowed:
        marks = ",".join("?" * len(allowed))
        out["Missed doses"] = str(conn.execute(f"SELECT count(*) FROM doses WHERE status = 'missed' AND slot_utc >= ? "
                                               f"AND person_id IN ({marks})", (utc_iso(monday), *allowed)).fetchone()[0])
    else:
        out["Missed doses"] = "Not shared"
    out["Feedback items"] = str(sum(1 for f in feedback(conn, clock) if f["at"] >= monday))
    spent = 0.0
    if log.path.exists():
        start = utc_iso(monday)
        for line in log.path.read_text().splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("at", "") >= start:
                spent += entry.get("usd", 0)
    out["API cost"] = f"${spent:.2f}"
    return out


def events(conn, clock: Clock) -> list[dict]:
    """What the box told Lea (device messages), newest first."""
    return [{"at": from_utc_iso(r["created_at_utc"], clock.tz).strftime("%a %-d %b %H:%M"), "text": r["text"]}
            for r in conn.execute("SELECT created_at_utc, text FROM family_out WHERE kind = 'operator' "
                                  "ORDER BY id DESC LIMIT 10")]


def version() -> str:
    try:
        from importlib.metadata import version as v
        return v("kore")
    except Exception:
        return "?"


# --- "Talk to Pepe as a test": a throwaway copy of the home ------------------------------------------------


class TestChat:
    """One per Lea login. The copy is made when she starts (or switches who she pretends to be) and lives in
    memory only: nothing reaches the device, their memory or their family (no channels)."""

    def __init__(self):
        self.copy = None
        self.brain = None
        self.as_person: int | None = None
        self.lines: list[tuple[str, str]] = []
        self.started = None
        import threading
        self.busy = threading.Lock()

    def prepare(self, src: sqlite3.Connection, clock: Clock, client, as_person: int) -> None:
        """Under the page's lock: check the permission, and copy the home if starting (or switching person)."""
        from kore.brain import tools
        from kore.brain.conversation import Brain
        from kore.brain.session import Person, Session
        from kore.family.service import Family
        from kore.memory.db import connect

        if as_person not in _day_ok(src) or not _shared_ok(src):
            # The copy holds the whole home (shared list, shared appointments, a partner's doses): only when
            # everyone at home agreed Lea may see their day.
            raise agenda.Refused("The test chat needs everyone at home to have agreed you can see their day.")
        if self.brain is None or self.as_person != as_person or clock.now() - self.started > timedelta(minutes=30):
            self.copy = connect(":memory:", shared=True)
            src.backup(self.copy)
            people_ = [Person(r["id"], r["name"], r["call_name"] or r["name"])
                       for r in self.copy.execute("SELECT id, name, call_name FROM people ORDER BY id")]
            session = Session(people_, fixed_speaker=as_person)
            ctx = tools.Context(self.copy, clock, session, family=Family(self.copy, clock, {}))
            self.brain = Brain(ctx, client, channel="chat")
            self.as_person, self.lines, self.started = as_person, [], clock.now()

    def reply(self, text: str) -> None:
        """Outside the page lock (Claude can take seconds): only the copy is touched, one message at a time."""
        text = agenda.clean(text, 600)
        if not text or self.brain is None:
            return
        if not self.busy.acquire(blocking=False):
            return  # a second press while Pepe is answering: ignored
        try:
            self.lines.append(("you", text))
            try:
                answer = self.brain.hear(text)
            except Exception:
                answer = "(Pepe couldn't answer: no connection, or today's budget reached.)"
            self.lines.append(("pepe", answer or "(no answer)"))
        finally:
            self.busy.release()
