"""Changing things from the web page (WEB-PLAN.md section 5, step W3): the calendar (appointments,
reminders, birthdays) and what Pepe knows about the person.

The same rule as by voice: nothing is saved without a yes. A form never saves anything itself; it
makes a proposal, and the page shows the read-back ("Cita: «Médico», el jueves 12 de noviembre a
las 10:30. ¿Lo apunto?") with "Sí, apúntalo" and "No". Only then is the change made, by the same
code the voice uses (tools.EXECUTORS), so a web appointment is reminded out loud like any other.

Code checks everything, at the proposal and again at the yes (things may have changed meanwhile):
- personal things only for their owner; shared ones (person_id NULL) for anyone at home;
- someone who said no to keeping medicines and appointments can't add their own here either;
- no dates in the past, nothing more than two years ahead, short plain texts;
- with an unreliable clock nothing is changed (dates would be wrong, and reminders don't fire).
Only the latest proposal of each person can be confirmed, within 15 minutes, once.

The shopping list is like by voice: shared, and changed at once (the button is the yes); a
removed item can be put back from the page.
Medicines are not changed from the web: only by voice, with the read-back (BUILD-PLAN rule).
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from kore.brain import tools
from kore.brain.fallback import MONTHS, WEEKDAYS, spoken_date
from kore.brain.session import Person, Session
from kore.memory import store
from kore.memory.db import from_utc_iso, utc_iso
from kore.scheduler.clock import Clock, local_at

PENDING_LIFE = timedelta(minutes=15)
AHEAD = timedelta(days=730)
TITLE_CHARS = 120
NOTE_CHARS = 300
ITEM_CHARS = 60
DAY_CODES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
NOTE_KINDS = {"interest": "Le gusta", "story": "Una historia", "person": "Una persona", "preference": "Prefiere",
              "health_note": "Salud", "other": "Otra cosa"}


class Refused(Exception):
    """Said to the person as it is (Spanish, plain)."""


@dataclass
class Proposal:
    id: int
    kind: str
    readback: str
    question: str  # the button: "Sí, apúntalo"


# --- small checks ----------------------------------------------------------------------------


def clean(text: str | None, limit: int) -> str:
    """One line of plain text: no control characters, spaces collapsed, cut to `limit`."""
    text = "".join(ch if ch.isprintable() else " " for ch in (text or ""))
    return " ".join(text.split())[:limit]


def _day(value: str | None) -> date:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        raise Refused("Falta el día, o no se entiende. Elíjalo en el calendario del formulario.") from None


def _time(value: str | None) -> str:
    m = re.fullmatch(r"\s*([0-9]{1,2})[:.]([0-9]{2})\s*", value or "")
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise Refused("Falta la hora, o no se entiende. Escríbala así: 10:30.")
    return f"{int(m[1]):02d}:{m[2]}"


def _usable_clock(clock: Clock) -> None:
    if not clock.trusted():
        raise Refused("La hora de Pepe no es fiable ahora mismo, así que no se puede cambiar el calendario. "
                      "Pruebe más tarde.")


def _health_ok(conn: sqlite3.Connection, person_id: int | None) -> None:
    if person_id is not None and store.declined(conn, person_id, "health_info"):
        raise Refused("Usted dijo que Pepe no guarde sus citas ni sus medicinas. Si cambia de idea, dígaselo a Pepe "
                      "en voz alta. Puede apuntar una cita «para los dos».")


def _mine(row, owner: int | None, person_id: int, gone: str):
    """The row if it is there and theirs (or shared). The other person's things look like missing ones: the same
    words, so their ids can't be guessed from the answer."""
    if row is None or (owner is not None and owner != person_id):
        raise Refused(gone)
    return row


def _at(hhmm: str) -> str:
    return ("a la " if hhmm.startswith("01:") else "a las ") + hhmm


def _local(day: date, hhmm: str, clock: Clock) -> datetime:
    """The wall-clock time; one that doesn't exist (the spring-forward night) moves an hour on, and the read-back
    says the moved time, so what is read is what is saved."""
    return local_at(day, hhmm, clock.tz)


def _when_words(at: datetime, today: date) -> str:
    if at.date() == today:
        day = "hoy"
    elif at.date() == today + timedelta(days=1):
        day = "mañana"
    else:
        day = "el " + spoken_date(at.date())
    return f"{day} {_at(at.strftime('%H:%M'))}"


def _for_whom(person_id: int | None) -> str:
    return "Para los dos." if person_id is None else "Solo para usted."


def rule_words(rule: str, first: date) -> str:
    if rule == "once":
        return "el " + spoken_date(first)
    if rule == "daily":
        return "todos los días"
    if rule.startswith("weekly:"):
        names = [WEEKDAYS[DAY_CODES.index(d)] for d in rule[7:].split(",")]
        names = [n if n.endswith("s") else n + "s" for n in names]  # lunes, sábados
        return "los " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " y " + names[-1])
    if rule.startswith("every:"):
        n = int(rule[6:])
        return "todos los días" if n == 1 else f"cada {n} días"
    return rule


def _birthday_words(md: str) -> str:
    m, d = (int(x) for x in md.split("-"))
    return f"el {d} de {MONTHS[m - 1]}"


# --- proposals ---------------------------------------------------------------------------------


def propose_appointment(conn, clock, person_id: int, f: dict) -> Proposal:
    _usable_clock(clock)
    title = clean(f.get("title"), TITLE_CHARS)
    if not title:
        raise Refused("Falta qué es: por ejemplo «Médico de cabecera».")
    where = clean(f.get("where"), TITLE_CHARS)
    owner = None if f.get("shared") else person_id
    _health_ok(conn, owner)
    at = _local(_day(f.get("day")), _time(f.get("time")), clock)
    _future(clock, at)
    readback = f"Cita: «{title}», {_when_words(at, clock.now().date())}{', en ' + where if where else ''}. " \
               f"{_for_whom(owner)}"
    return _save(conn, clock, person_id, "add_appointment",
                 {"person_id": owner, "title": title, "starts_at": at.isoformat(), "location": where}, readback)


def propose_move(conn, clock, person_id: int, appointment_id: int, f: dict) -> Proposal:
    _usable_clock(clock)
    row = _appointment(conn, clock, person_id, appointment_id)
    at = _local(_day(f.get("day")), _time(f.get("time")), clock)
    _future(clock, at)
    readback = f"La cita «{row['title']}» pasa a {_when_words(at, clock.now().date())}."
    return _save(conn, clock, person_id, "move_appointment", {"appointment_id": row["id"],
                                                               "starts_at": at.isoformat()}, readback)


def propose_cancel(conn, clock, person_id: int, appointment_id: int) -> Proposal:
    _usable_clock(clock)
    row = _appointment(conn, clock, person_id, appointment_id)
    at = from_utc_iso(row["starts_at_utc"], clock.tz)
    readback = f"Anular la cita «{row['title']}» de {_when_words(at, clock.now().date())}. Pepe ya no la recordará."
    return _save(conn, clock, person_id, "cancel_appointment", {"appointment_id": row["id"]}, readback,
                 "Sí, anúlala")


def propose_reminder(conn, clock, person_id: int, f: dict) -> Proposal:
    _usable_clock(clock)
    text = clean(f.get("text"), TITLE_CHARS)
    if not text:
        raise Refused("Falta qué quiere que le recuerde: por ejemplo «sacar la basura».")
    owner = None if f.get("shared") else person_id
    if tools._is_medicine(text):
        _health_ok(conn, owner)
    at = _time(f.get("time"))
    how = f.get("how") or ""
    today = clock.now().date()
    if how == "once":
        first, rule = _day(f.get("day")), "once"
        _future(clock, _local(first, at, clock))
    elif how == "daily":
        first, rule = today, "daily"
    elif how == "weekly":
        days = [d for d in DAY_CODES if f.get("d_" + d)]
        if not days:
            raise Refused("Marque qué días de la semana.")
        first, rule = today, "weekly:" + ",".join(days)
    else:
        raise Refused("Elija cuándo: un día, todos los días o algunos días de la semana.")
    readback = f"Pepe le recordará «{text}» {rule_words(rule, first)} {_at(at)}. {_for_whom(owner)}"
    return _save(conn, clock, person_id, "add_reminder", {"person_id": owner, "text": text, "time": at, "rule": rule,
                                                          "anchor": first.isoformat()}, readback)


def propose_reminder_stop(conn, clock, person_id: int, reminder_id: int) -> Proposal:
    _usable_clock(clock)
    row = _reminder(conn, person_id, reminder_id)
    readback = f"Pepe dejará de recordarle «{row['text']}»."
    return _save(conn, clock, person_id, "stop_reminder", {"reminder_id": row["id"]}, readback, "Sí, quítalo")


def propose_birthday(conn, clock, person_id: int, f: dict) -> Proposal:
    _usable_clock(clock)
    name = clean(f.get("name"), 60)
    if not name:
        raise Refused("Falta el nombre.")
    relation = clean(f.get("relation"), 40)
    try:
        md_date = date(2024, _number(f.get("month")), _number(f.get("day")))  # 2024: 29 February is allowed
    except ValueError:
        raise Refused("Falta el día o el mes, o no existe esa fecha.") from None
    md = md_date.strftime("%m-%d")
    owner = None if f.get("shared") else person_id
    readback = (f"Cumpleaños de {name}{' (' + relation + ')' if relation else ''}, {_birthday_words(md)}. "
                f"Pepe lo recordará ese día. {_for_whom(owner)}")
    return _save(conn, clock, person_id, "add_contact", {"name": name, "relation": relation, "birthday": md,
                                                         "help": False, "for_person": owner}, readback)


def _number(value) -> int:
    value = (value or "").strip()
    if not (value.isascii() and value.isdigit() and len(value) <= 2):
        raise ValueError(value)
    return int(value)


def propose_birthday_stop(conn, clock, person_id: int, contact_id: int) -> Proposal:
    _usable_clock(clock)
    row = _birthday(conn, person_id, contact_id)
    readback = f"Pepe dejará de recordar el cumpleaños de {row['name']}. {row['name']} sigue apuntado en Pepe."
    return _save(conn, clock, person_id, "stop_birthday", {"contact_id": row["id"]}, readback, "Sí, quítalo")


def propose_note(conn, clock, person_id: int, f: dict) -> Proposal:
    text = clean(f.get("text"), NOTE_CHARS)
    if not text:
        raise Refused("Falta lo que quiere que Pepe sepa.")
    kind = f.get("kind") if f.get("kind") in NOTE_KINDS else "other"
    readback = f"Pepe apuntará sobre usted ({NOTE_KINDS[kind].lower()}): «{text}». Solo usted lo verá aquí."
    return _save(conn, clock, person_id, "add_note", {"person_id": person_id, "kind": kind, "text": text}, readback)


def propose_forget(conn, clock, person_id: int, note_id: int) -> Proposal:
    row = conn.execute("SELECT * FROM notes WHERE id = ? AND person_id = ?", (note_id, person_id)).fetchone()
    if row is None:
        raise Refused("Eso ya no está apuntado.")
    readback = f"Pepe olvidará para siempre: «{row['text']}»."
    return _save(conn, clock, person_id, "forget_note", {"note_id": row["id"]}, readback, "Sí, olvídalo")


def _future(clock: Clock, at: datetime) -> None:
    now = clock.now()
    if at <= now:
        raise Refused("Esa fecha y hora ya han pasado.")
    if at - now > AHEAD:
        raise Refused("Solo se puede apuntar hasta dentro de dos años.")


def _appointment(conn, clock, person_id: int, appointment_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM appointments WHERE id = ? AND cancelled = 0", (appointment_id,)).fetchone()
    gone = "Esa cita ya no está en el calendario."
    if row is not None and from_utc_iso(row["starts_at_utc"], clock.tz) <= clock.now():
        raise Refused(gone)
    return _mine(row, row and row["person_id"], person_id, gone)


def _reminder(conn, person_id: int, reminder_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM reminders WHERE id = ? AND active = 1", (reminder_id,)).fetchone()
    return _mine(row, row and row["person_id"], person_id, "Ese recordatorio ya no está.")


def _birthday(conn, person_id: int, contact_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM contacts WHERE id = ? AND birthday_md IS NOT NULL AND birthday_reminders = 1",
                       (contact_id,)).fetchone()
    return _mine(row, row and row["for_person"], person_id, "Ese cumpleaños ya no está.")


# --- pending changes and the yes ----------------------------------------------------------------


def _save(conn, clock, person_id: int, kind: str, payload: dict, readback: str, question: str = "Sí, apúntalo"
          ) -> Proposal:
    now = utc_iso(clock.now())
    conn.execute("UPDATE web_pending SET status = 'expired' WHERE person_id = ? AND status = 'open'", (person_id,))
    purge(conn, clock)
    pid = conn.execute("INSERT INTO web_pending (person_id, kind, payload, readback, created_at) VALUES (?, ?, ?, ?, ?)",
                       (person_id, kind, json.dumps({**payload, "question": question}, ensure_ascii=False), readback,
                        now)).lastrowid
    return Proposal(pid, kind, readback, question)


def purge(conn, clock) -> None:
    """Proposals go after a day (also called whenever a page of the person is shown)."""
    conn.execute("DELETE FROM web_pending WHERE created_at < ? OR created_at > ?",
                 (utc_iso(clock.now() - timedelta(days=1)), utc_iso(clock.now() + timedelta(days=1))))


def pending(conn, clock, person_id: int, pending_id: int) -> Proposal | None:
    """The open proposal, if it is this person's latest and still fresh (for showing it again)."""
    row = _open(conn, clock, person_id, pending_id)
    return Proposal(row["id"], row["kind"], row["readback"], json.loads(row["payload"])["question"]) if row else None


def _open(conn, clock, person_id: int, pending_id: int) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM web_pending WHERE id = ? AND person_id = ? AND status = 'open'",
                       (pending_id, person_id)).fetchone()
    if row is None:
        return None
    age = clock.now() - from_utc_iso(row["created_at"], clock.tz)
    if not timedelta(0) <= age <= PENDING_LIFE:
        conn.execute("UPDATE web_pending SET status = 'expired' WHERE id = ?", (row["id"],))
        return None
    return row


def cancel(conn, person_id: int, pending_id: int) -> None:
    conn.execute("UPDATE web_pending SET status = 'cancelled' WHERE id = ? AND person_id = ? AND status = 'open'",
                 (pending_id, person_id))


def confirm(conn, clock, person_id: int, pending_id: int, links: dict | None = None) -> str:
    """The person pressed "Sí": check again, then do it. Returns where the page goes next.
    Raises Refused (with what to say) if it can't be done."""
    row = _open(conn, clock, person_id, pending_id)
    if row is None:
        done = conn.execute("SELECT status FROM web_pending WHERE id = ? AND person_id = ?",
                            (pending_id, person_id)).fetchone()
        if done and done["status"] == "done":
            raise Refused("Eso ya estaba hecho.")
        raise Refused("Ha pasado mucho rato, o ha hecho otro cambio después. Vuelva a escribirlo, por favor.")
    kind, p = row["kind"], json.loads(row["payload"])
    conn.execute("UPDATE web_pending SET status = 'failed' WHERE id = ?", (row["id"],))  # until it's really done
    _check_again(conn, clock, person_id, kind, p)
    if kind == "forget_note":  # its log checkpoint can't run inside a transaction
        conn.execute("UPDATE web_pending SET status = 'done' WHERE id = ?", (row["id"],))
        store.forget_note(conn, person_id, p["note_id"])  # also blanks the read-backs here that held it
        return "/sabe?hecho=olvidado"
    if kind == "family_access":  # the code is shown on the page that answers this yes, never stored
        from kore.web import family
        conn.execute("BEGIN IMMEDIATE")
        try:
            code, name = family.grant(conn, clock, person_id, p)
            conn.execute("UPDATE web_pending SET status = 'done' WHERE id = ?", (row["id"],))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return f"code:{code}:{name}"
    if kind == "send_message":  # to family: sent (and retried) outside a transaction, like by voice
        return _send_to_family(conn, clock, person_id, row["id"], p, links)
    ctx = tools.Context(conn, clock, _session(conn, person_id))
    conn.execute("BEGIN IMMEDIATE")
    try:
        if kind in tools.EXECUTORS:
            tools.EXECUTORS[kind](ctx, p)
            if p.get("family_proposal_id"):
                conn.execute("UPDATE family_proposals SET status = 'accepted', answered_at = ? WHERE id = ?",
                             (utc_iso(clock.now()), p["family_proposal_id"]))
            if p.get("operator_proposal_id"):
                conn.execute("UPDATE operator_proposals SET status = 'accepted', answered_at = ? WHERE id = ?",
                             (utc_iso(clock.now()), p["operator_proposal_id"]))
        elif kind == "stop_birthday":
            store.stop_birthday(conn, p["contact_id"])
        elif kind == "add_note":
            store.add_note(conn, clock, person_id, p["kind"], p["text"])
        else:
            raise Refused("Eso no se puede hacer desde aquí.")
        conn.execute("UPDATE web_pending SET status = 'done' WHERE id = ?", (row["id"],))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return "/sabe?hecho=apuntado" if kind == "add_note" else "/calendario?hecho=" + (
        "quitado" if kind in ("cancel_appointment", "stop_reminder", "stop_birthday") else "apuntado")


def _send_to_family(conn, clock, person_id: int, pending_row: int, p: dict, links: dict | None) -> str:
    """Their yes to "Le mando a Ana: «…»". By Telegram if Ana has it (as by voice); a family member who only uses the
    family app reads it there, so it is stored as delivered to her page."""
    from kore.family import messages as family_msgs
    from kore.family.service import Family

    conn.execute("UPDATE web_pending SET status = 'done' WHERE id = ?", (pending_row,))
    c = conn.execute("SELECT * FROM contacts WHERE id = ?", (p["contact_id"],)).fetchone()
    if c is None:
        raise Refused("No se ha encontrado a esa persona.")
    if c["telegram_chat_id"]:
        ctx = tools.Context(conn, clock, _session(conn, person_id), family=Family(conn, clock, links or {}))
        r = tools.EXECUTORS["send_message"](ctx, p)
        return "/mi-familia?hecho=" + ("mandado" if r.get("sent") else "pendiente")
    name = conn.execute("SELECT call_name FROM people WHERE id = ?", (person_id,)).fetchone()["call_name"]
    i = family_msgs.queue(conn, clock, "message", "telegram", f"{name} (con Pepe): {p['text']}", c["id"], person_id)
    conn.execute("UPDATE family_out SET status = 'sent', sent_at_utc = ?, told = 2, error = 'family app only' "
                 "WHERE id = ?", (utc_iso(clock.now()), i))
    return "/mi-familia?hecho=mandado"


def _check_again(conn, clock, person_id: int, kind: str, p: dict) -> None:
    if kind in ("add_appointment", "move_appointment", "cancel_appointment", "add_reminder", "stop_reminder",
                "add_contact", "stop_birthday"):
        _usable_clock(clock)
    if kind == "add_appointment":
        _health_ok(conn, p["person_id"])
        _future(clock, datetime.fromisoformat(p["starts_at"]))
        if p.get("family_proposal_id"):
            row = conn.execute("SELECT person_id FROM family_proposals WHERE id = ? AND status = 'open'",
                               (p["family_proposal_id"],)).fetchone()
            _mine(row, row and row["person_id"], person_id, "Esa propuesta ya no está.")
        if p.get("operator_proposal_id"):
            row = conn.execute("SELECT person_id FROM operator_proposals WHERE id = ? AND status = 'open'",
                               (p["operator_proposal_id"],)).fetchone()
            _mine(row, row and row["person_id"], person_id, "Esa propuesta ya no está.")
    elif kind == "move_appointment":
        _appointment(conn, clock, person_id, p["appointment_id"])
        _future(clock, datetime.fromisoformat(p["starts_at"]))
    elif kind == "cancel_appointment":
        _appointment(conn, clock, person_id, p["appointment_id"])
    elif kind == "add_reminder":
        if tools._is_medicine(p["text"]):
            _health_ok(conn, p["person_id"])
        if p["rule"] == "once":
            _future(clock, _local(date.fromisoformat(p["anchor"]), p["time"], clock))
    elif kind == "stop_reminder":
        _reminder(conn, person_id, p["reminder_id"])
    elif kind == "stop_birthday":
        _birthday(conn, person_id, p["contact_id"])
    elif kind == "send_message":
        c = conn.execute("SELECT for_person FROM contacts WHERE id = ?", (p["contact_id"],)).fetchone()
        _mine(c, c and c["for_person"], person_id, "No se ha encontrado a esa persona.")


def _session(conn, person_id: int) -> Session:
    people = [Person(r["id"], r["name"], r["call_name"] or r["name"])
              for r in conn.execute("SELECT id, name, call_name FROM people ORDER BY id")]
    return Session(people, fixed_speaker=person_id)


# --- what the pages show ---------------------------------------------------------------------


def calendar(conn, clock, person_id: int) -> dict:
    purge(conn, clock)
    today = clock.now().date()
    appts = [{"id": a["id"], "title": a["title"], "where": a["location"], "shared": a["person_id"] is None,
              "when": _when_words(a["starts_at"], today), "day": a["starts_at"].date().isoformat(),
              "time": a["starts_at"].strftime("%H:%M")}
             for a in store.appointments(conn, clock, person_id)]
    rems = []
    for r in store.reminders(conn, person_id):
        try:
            words = rule_words(r["rule"], date.fromisoformat(r["first_day"]))
        except (ValueError, IndexError):
            continue
        rems.append({"id": r["id"], "text": r["text"], "when": f"{words} {_at(r['time'])}",
                     "shared": r["person_id"] is None})
    bdays = [{"id": b["id"], "name": b["name"], "relation": b["relation"], "when": _birthday_words(b["md"]),
              "shared": b["for_person"] is None, "next": _next_birthday(b["md"], today)}
             for b in store.birthdays(conn, person_id)]
    bdays.sort(key=lambda b: b["next"])
    from kore.web import family
    return {"appointments": appts, "reminders": rems, "birthdays": bdays, "clock_ok": clock.trusted(),
            "proposals": family.waiting(conn, clock, person_id),
            "today": today.isoformat()}


def _next_birthday(md: str, today: date) -> date:
    m, d = (int(x) for x in md.split("-"))
    for year in (today.year, today.year + 1, today.year + 2, today.year + 3, today.year + 4):
        try:
            day = date(year, m, d)
        except ValueError:  # 29 February in a normal year: Pepe says it on the 28th
            day = date(year, 2, 28)
        if day >= today:
            return day
    return today


def appointment(conn, clock, person_id: int, appointment_id: int) -> dict:
    """One appointment, to change (Refused if it isn't theirs or has gone)."""
    row = _appointment(conn, clock, person_id, appointment_id)
    at = from_utc_iso(row["starts_at_utc"], clock.tz)
    return {"id": row["id"], "title": row["title"], "where": row["location"], "day": at.date().isoformat(),
            "time": at.strftime("%H:%M"), "when": _when_words(at, clock.now().date())}


# --- the shopping list (shared, like by voice: changed at once) ----------------------------------


def shopping(conn) -> list[dict]:
    return [{"id": r["id"], "item": r["item"]} for r in conn.execute(
        "SELECT id, item FROM list_items WHERE list_name = ? AND status = 'open' ORDER BY id", (store.SHOPPING,))]


def shopping_add(conn, clock, text: str | None) -> str:
    item = clean(text, ITEM_CHARS)
    if not item:
        raise Refused("Escriba qué hay que comprar.")
    if len(shopping(conn)) >= 100:
        raise Refused("La lista ya es muy larga. Quite lo que ya esté comprado.")
    store.add_to_list(conn, clock, [item])
    return item


def shopping_remove(conn, item_id: int) -> str | None:
    row = conn.execute("SELECT item FROM list_items WHERE id = ? AND list_name = ? AND status = 'open'",
                       (item_id, store.SHOPPING)).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE list_items SET status = 'done' WHERE id = ?", (item_id,))
    return row["item"]


def shopping_put_back(conn, clock, item_id: int) -> None:
    row = conn.execute("SELECT item FROM list_items WHERE id = ? AND list_name = ? AND status = 'done'",
                       (item_id, store.SHOPPING)).fetchone()
    if row is not None:
        store.add_to_list(conn, clock, [row["item"]])  # not twice if it was written again meanwhile


def shopping_item(conn, item_id: int) -> dict | None:
    row = conn.execute("SELECT id, item, status FROM list_items WHERE id = ? AND list_name = ?",
                       (item_id, store.SHOPPING)).fetchone()
    return {"id": row["id"], "item": row["item"], "status": row["status"]} if row else None


# --- what Pepe knows about the person ----------------------------------------------------------


def known(conn, clock, person_id: int) -> list[dict]:
    purge(conn, clock)
    return [{"id": n["id"], "kind": NOTE_KINDS.get(n["kind"], NOTE_KINDS["other"]), "text": n["text"]}
            for n in store.notes(conn, person_id)]
