"""Carmen and Antonio's app (Lea's design, "App for Carmen & Antonio"): what each screen shows, from the records.

Same rules as everywhere: code decides what is true; a dose is recorded only by code (`meds.mark_taken`, which
picks the dose and can't record it twice), a message to family only after a read-back and their "Sí", and
"Pedir ayuda" goes through the help path the voice uses: pressing "Sí, avisar a Ana" is their clear yes.

Not built, because the home page can't do them without new services or Lea's decisions (listed in
docs/CONSOLE-PLAN.md): talking to Pepe from the phone ("Pulsa y habla"), reading answers aloud, the phone's
calendar and contacts, WhatsApp, and alerts outside the home Wi-Fi.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta

from kore.brain import tools
from kore.brain.fallback import MONTHS, WEEKDAYS
from kore.family import messages as family_msgs
from kore.family.service import Family
from kore.memory import meds, settings, store
from kore.memory.db import from_utc_iso, utc_iso
from kore.scheduler.clock import Clock
from kore.web import agenda

AGENDA_DAYS = 4
SHORT_DAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def tu(conn, person_id: int) -> bool:
    return settings.get(conn, "address", person_id) == "tu"


def text_size(conn, person_id: int) -> str:
    return settings.get(conn, "text_size", person_id)


def set_text_size(conn, clock: Clock, person_id: int, size: str) -> None:
    if size in settings.CHOICES["text_size"]:
        settings.put(conn, clock, "text_size", size, person_id)


def long_date(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()].capitalize()} {d.day} de {MONTHS[d.month - 1]}"


# --- pills -----------------------------------------------------------------------------------------


def pills(conn, clock: Clock, person_id: int) -> dict:
    """Today's doses, and for each whether its "Ya me la he tomado" button shows (its dose is open now and not
    recorded as taken: the same window `mark_taken` uses)."""
    try:
        doses = meds.status(conn, clock, person_id)["doses"]
    except meds.ClockUntrusted:
        return {"clock_ok": False, "doses": [], "taken": 0, "next": None}
    now = clock.now()
    open_now = {}
    for m in meds.medications(conn, person_id):
        slot = meds.current_slot(m, now, clock)
        if slot is None or meds.elapsed(slot, now) > meds.REACH_BACK or slot.date() != now.date():
            continue
        row = meds.dose_row(conn, m["id"], slot)
        if row is None or row["status"] != "taken":
            open_now[(m["name"], slot.strftime("%H:%M"))] = m["id"]
    out = []
    for d in doses:
        out.append({"time": d["time"], "name": d["description"] or d["medication"], "state": d["state"],
                    "taken_at": d.get("taken_at"), "med_id": open_now.get((d["medication"], d["time"]))})
    upcoming = [d["time"] for d in out if d["state"] == "upcoming"]
    return {"clock_ok": True, "doses": out, "taken": sum(d["state"] == "taken" for d in out),
            "next": upcoming[0] if upcoming else None}


def take_pill(conn, clock: Clock, person_id: int, medication_id: int) -> str:
    """The button: code records that dose, exactly like "ya me la he tomado" said to Pepe (never twice)."""
    if conn.execute("SELECT 1 FROM medications WHERE id = ? AND person_id = ? AND active = 1",
                    (medication_id, person_id)).fetchone() is None:
        return "No se ha encontrado esa pastilla."
    try:
        r = meds.mark_taken(conn, clock, person_id, medication_id)
    except meds.ClockUntrusted:
        return "La hora de Pepe no es fiable ahora: no se puede apuntar. Mira el pastillero."
    if r.get("ok") and r.get("marked"):
        return "Apuntada. ¡Muy bien!"
    if r.get("ok"):
        return "Esa ya estaba apuntada. No tomes otra."
    return "Ahora no hay ninguna pastilla que apuntar. No tomes otra; si dudas, mira el pastillero."


# --- the day ---------------------------------------------------------------------------------------


def day_items(conn, clock: Clock, person_id: int, day: date) -> list[dict]:
    """Everything on one day for this person: their doses, their and shared appointments and reminders,
    birthdays Pepe reminds them of."""
    items = []
    p = pills(conn, clock, person_id) if day == clock.now().date() else None
    if p is not None:
        for d in p["doses"]:
            items.append({"time": d["time"], "text": d["name"], "kind": "med",
                          "note": "tomada" if d["state"] == "taken" else "", "done": d["state"] == "taken"})
    elif clock.trusted():
        for m in meds.medications(conn, person_id):
            for slot in meds.slots_on(m, day, clock):
                items.append({"time": slot.strftime("%H:%M"), "text": m["description"] or m["name"], "kind": "med",
                              "note": "", "done": False})
    for a in store.appointments(conn, clock, person_id, upcoming=False):
        if a["starts_at"].date() == day:
            items.append({"time": a["starts_at"].strftime("%H:%M"), "text": a["title"], "kind": "appt",
                          "note": a["location"], "done": a["starts_at"] < clock.now()})
    for r in store.reminders(conn, person_id):
        try:
            if store.reminder_occurs_on(r["rule"], date.fromisoformat(r["first_day"]), day):
                items.append({"time": r["time"], "text": r["text"], "kind": "rem", "note": "", "done": False})
        except ValueError:
            continue
    for b in store.birthdays(conn, person_id):
        if b["md"] == day.strftime("%m-%d"):
            items.append({"time": "", "text": f"Cumpleaños de {b['name']}", "kind": "bday", "note": b["relation"],
                          "done": False})
    items.sort(key=lambda i: (i["time"] != "", i["time"]))
    return items


def agenda_days(clock: Clock) -> list[dict]:
    today = clock.now().date()
    return [{"n": i, "label": "Hoy" if i == 0 else SHORT_DAYS[(today + timedelta(days=i)).weekday()],
             "day": (today + timedelta(days=i)).day, "date": today + timedelta(days=i)} for i in range(AGENDA_DAYS)]


def next_up(conn, clock: Clock, person_id: int) -> dict | None:
    """The next thing today (not a pill: those have their own card); a birthday today if nothing else."""
    now = clock.now()
    hhmm = now.strftime("%H:%M")
    items = day_items(conn, clock, person_id, now.date())
    timed = [i for i in items if i["kind"] in ("appt", "rem") and i["time"] >= hhmm]
    bday = next((i for i in items if i["kind"] == "bday"), None)
    if timed:
        return timed[0]
    if bday:
        name = bday["text"].replace("Cumpleaños de ", "")
        phone = conn.execute("SELECT phone FROM contacts WHERE name = ? AND phone IS NOT NULL", (name,)).fetchone()
        return {**bday, "text": f"Hoy es el cumpleaños de {name}", "call": name, "phone": phone["phone"] if phone else None}
    return None


# --- family ----------------------------------------------------------------------------------------


def contacts(conn, person_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM contacts WHERE (for_person IS NULL OR for_person = ?) AND "
                        "(phone IS NOT NULL OR telegram_chat_id IS NOT NULL OR help_contact = 1 OR id IN "
                        "(SELECT contact_id FROM web_accounts WHERE role = 'family')) ORDER BY name",
                        (person_id,)).fetchall()
    return [{"id": r["id"], "name": r["name"], "relation": r["relation"], "phone": _tel(r["phone"]),
             "can_write": bool(r["telegram_chat_id"]) or _has_web(conn, r["id"])} for r in rows]


def _tel(phone: str | None) -> str | None:
    if not phone:
        return None
    clean = re.sub(r"[^0-9+]", "", phone)
    return clean if re.fullmatch(r"\+?[0-9]{6,15}", clean) else None


def _has_web(conn, contact_id: int) -> bool:
    return conn.execute("SELECT 1 FROM web_logins l JOIN web_accounts a ON a.id = l.account_id WHERE a.role = "
                        "'family' AND a.contact_id = ? AND l.revoked_at IS NULL", (contact_id,)).fetchone() is not None


def new_messages(conn, clock: Clock, person_id: int) -> list[dict]:
    """Unread family messages for them; shown on the page = read (Pepe won't mention them again)."""
    rows = family_msgs.unread(conn, person_id)
    family_msgs.mark_read(conn, clock, [r["id"] for r in rows])
    return [{"from": r["name"], "contact_id": r["contact_id"], "text": r["text"],
             "at": from_utc_iso(r["received_at_utc"], clock.tz)} for r in rows]


def unread_count(conn, person_id: int) -> int:
    return len(family_msgs.unread(conn, person_id))


def propose_reply(conn, clock: Clock, person_id: int, contact_id: int, text: str | None) -> agenda.Proposal:
    row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if row is None or (row["for_person"] is not None and row["for_person"] != person_id):
        raise agenda.Refused("No se ha encontrado a esa persona.")
    words = agenda.clean(text, tools.MAX_MESSAGE)
    if not words:
        raise agenda.Refused("Escribe el mensaje.")
    if not row["telegram_chat_id"] and not _has_web(conn, contact_id):
        raise agenda.Refused(f"Todavía no se pueden mandar mensajes a {row['name']}: la familia tiene que "
                             "conectarse primero.")
    return agenda._save(conn, clock, person_id, "send_message",
                        {"contact_id": contact_id, "text": words, "from_person": person_id},
                        f"Le mando a {row['name']}: «{words}».", "Sí, envíalo")


# --- help ------------------------------------------------------------------------------------------


def help_names(conn, person_id: int) -> list[str]:
    return [c["name"] for c in family_msgs.help_contacts(conn, [person_id]) if c["telegram_chat_id"] or c["phone"]]


def ask_for_help(conn, clock: Clock, person_id: int, links: dict | None) -> str:
    """They pressed "Sí, avisar": the same help path as the voice. Their press is the yes, so a question the path
    would ask ("¿Aviso a Ana?") is answered by it at once. Returns what to tell them."""
    from kore.brain.session import Person, Session

    people = [Person(r["id"], r["name"], r["call_name"] or r["name"])
              for r in conn.execute("SELECT id, name, call_name FROM people ORDER BY id")]
    session = Session(people, fixed_speaker=person_id)
    family = Family(conn, clock, links or {})
    out = family.call_for_help(session, "button")
    said = out.say
    if out.asked:
        action = next(p for p in session.pending.values() if p.status == "pending" and p.kind == "send_help")
        ctx = tools.Context(conn, clock, session, family=family)
        said = (said.split("¿")[0] + " " + tools.run_by_code(ctx, action)["say"]).strip()
    return said


# --- what others see -------------------------------------------------------------------------------


def who_sees_what(conn, person_id: int) -> dict:
    def yes(kind):
        return store.has_consent(conn, person_id, kind)
    return {
        "family": [("Si tomas las pastillas de hoy", yes("family_sees_doses")),
                   ("Un aviso si te olvidas varias veces seguidas", yes("family_missed_meds")),
                   ("Un aviso enseguida si pides ayuda", yes("emergency_path"))],
        "lea": [("Tus recordatorios, pastillas, notas y lo que le dices a Pepe sobre cómo hablar", yes("lea_sees_day")),
                ("Tus conversaciones de los últimos 7 días", yes("lea_reads_conversations"))],
        "partner": [("Si te has tomado la pastilla, si te lo pregunta a Pepe", yes("share_with_partner"))],
    }
