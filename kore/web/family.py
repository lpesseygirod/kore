"""Family logins on the home web page (WEB-PLAN.md sections 3 and 4, step W4).

A family member (a saved contact: Lucía, hija) gets in only after a person at home says yes on their
own page; the code then shows on that person's screen. What a family login can do:
- read the messages Pepe sent them from home, and write their own (they arrive like a Telegram message:
  Pepe mentions them and reads them when asked; they are outside text and can't make Pepe do anything);
- propose an appointment, which the person sees on their Calendario and saves only with their own
  read-back and "Sí" ("Cita propuesta por Lucía: …"), or turns down.
It sees nothing private: no medicines, appointments, notes, lists or conversations, not even whether a
proposal clashes with something. It learns only whether its own proposals were saved.

Who gives access: a contact of their own, or one shared by the home. The same people can take it away
(every device of that family member is logged out at once).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from kore.memory import store
from kore.memory.db import from_utc_iso, utc_iso
from kore.scheduler.clock import Clock
from kore.web import accounts, agenda

MAX_MESSAGE = 500
MESSAGES_PER_HOUR = 20
OPEN_PROPOSALS = 10
SHOWN = 50
OUT_KINDS = ("message", "help", "missed_meds", "stop_request")  # what Pepe sent them from home


def _contact(conn, person_id: int, contact_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if row is None or (row["for_person"] is not None and row["for_person"] != person_id):
        raise agenda.Refused("No se ha encontrado a esa persona.")
    return row


def _with_access(conn, contact_id: int) -> bool:
    """Logged in somewhere, or holding a code not used yet (which can be cancelled too)."""
    return conn.execute("SELECT 1 FROM web_logins l JOIN web_accounts a ON a.id = l.account_id WHERE a.role = "
                        "'family' AND a.contact_id = ? AND l.revoked_at IS NULL UNION SELECT 1 FROM web_codes WHERE "
                        "role = 'family' AND contact_id = ? AND status = 'open'",
                        (contact_id, contact_id)).fetchone() is not None


# --- the person's side: giving and taking access ------------------------------------------------


def people_to_invite(conn, person_id: int) -> list[dict]:
    rows = conn.execute("SELECT id, name, relation FROM contacts WHERE for_person IS NULL OR for_person = ? "
                        "ORDER BY name", (person_id,)).fetchall()
    return [{"id": r["id"], "name": r["name"], "relation": r["relation"], "access": _with_access(conn, r["id"])}
            for r in rows]


def propose_access(conn, clock: Clock, person_id: int, f: dict) -> agenda.Proposal:
    cid = f.get("contact")
    if cid and cid.isascii() and cid.isdigit() and len(cid) <= 9:
        c = _contact(conn, person_id, int(cid))
        name, relation, payload = c["name"], c["relation"], {"contact_id": c["id"]}
    else:
        name, relation = agenda.clean(f.get("name"), 60), agenda.clean(f.get("relation"), 40)
        if not name:
            raise agenda.Refused("Elija a quién, o escriba su nombre.")
        payload = {"contact_id": None, "name": name, "relation": relation, "for_person": person_id}
    who = name + (f" ({relation})" if relation else "")
    readback = (f"{who} podrá entrar en la página de Pepe para escribirle mensajes y proponerle citas. No verá sus "
                "medicinas, citas, notas ni conversaciones. Le daremos un código para que entre.")
    return agenda._save(conn, clock, person_id, "family_access", payload, readback, "Sí, dale acceso")


def grant(conn, clock: Clock, person_id: int, p: dict) -> tuple[str, str]:
    """Carry out the yes: (the code to pass on, their name)."""
    if p.get("contact_id") is None:
        cid = store.add_contact(conn, clock, p["name"], p["relation"], for_person=p["for_person"])
    else:
        cid = _contact(conn, person_id, p["contact_id"])["id"]
    name = conn.execute("SELECT name FROM contacts WHERE id = ?", (cid,)).fetchone()["name"]
    return accounts.family_code(conn, clock, cid), name


def take_access(conn, clock: Clock, person_id: int, contact_id: int) -> int:
    """Every device of this family member logged out, any code not yet used cancelled, and their proposals
    not yet answered withdrawn."""
    _contact(conn, person_id, contact_id)
    conn.execute("UPDATE web_codes SET status = 'cancelled' WHERE role = 'family' AND contact_id = ? AND "
                 "status = 'open'", (contact_id,))
    conn.execute("UPDATE family_proposals SET status = 'withdrawn', answered_at = ? WHERE contact_id = ? AND "
                 "status = 'open'", (utc_iso(clock.now()), contact_id))
    return conn.execute("UPDATE web_logins SET revoked_at = ? WHERE revoked_at IS NULL AND account_id IN "
                        "(SELECT id FROM web_accounts WHERE role = 'family' AND contact_id = ?)",
                        (utc_iso(clock.now()), contact_id)).rowcount


# --- the family member's side ---------------------------------------------------------------------


def thread(conn, clock: Clock, contact_id: int) -> list[dict]:
    """Messages Pepe sent them from home, and theirs to the home, oldest first."""
    out = [{"who": "home", "text": r["text"], "at": from_utc_iso(r["created_at_utc"], clock.tz),
            "state": {"sent": "", "pending": "por enviar", "failed": "no se pudo enviar"}.get(r["status"], "")}
           for r in conn.execute("SELECT * FROM family_out WHERE contact_id = ? AND kind IN (%s) ORDER BY id DESC "
                                 "LIMIT ?" % ",".join("?" * len(OUT_KINDS)), (contact_id, *OUT_KINDS, SHOWN))]
    out += [{"who": "me", "text": r["text"], "at": from_utc_iso(r["received_at_utc"], clock.tz),
             "state": "Pepe ya se lo ha leído" if r["read_at_utc"] else "Pepe se lo leerá cuando se lo pidan"}
            for r in conn.execute("SELECT * FROM family_in WHERE contact_id = ? ORDER BY id DESC LIMIT ?",
                                  (contact_id, SHOWN))]
    return sorted(out, key=lambda m: m["at"])[-SHOWN:]


def write(conn, clock: Clock, contact_id: int, text: str | None) -> None:
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in (text or "")).split())
    if not text:
        raise agenda.Refused("Escriba el mensaje.")
    if len(text) > MAX_MESSAGE:
        raise agenda.Refused(f"El mensaje es demasiado largo (como mucho {MAX_MESSAGE} letras).")
    since = utc_iso(clock.now() - timedelta(hours=1))
    if conn.execute("SELECT count(*) FROM family_in WHERE contact_id = ? AND received_at_utc > ?",
                    (contact_id, since)).fetchone()[0] >= MESSAGES_PER_HOUR:
        raise agenda.Refused("Ha mandado muchos mensajes esta hora. Pruebe un poco más tarde.")
    conn.execute("INSERT INTO family_in (received_at_utc, contact_id, text) VALUES (?, ?, ?)",
                 (utc_iso(clock.now()), contact_id, text))


def household(conn, contact_id: int | None = None) -> list[dict]:
    """Who a family member may propose to: the person whose contact they are, or (a contact shared by the home)
    everyone. A contact of Carmen's never reaches Toni, not even by name."""
    owner = None
    if contact_id is not None:
        row = conn.execute("SELECT for_person FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        owner = row["for_person"] if row else None
    return [{"id": r["id"], "call_name": r["call_name"] or r["name"]}
            for r in conn.execute("SELECT id, name, call_name FROM people ORDER BY id")
            if owner is None or r["id"] == owner]


def propose(conn, clock: Clock, contact_id: int, f: dict) -> str:
    """A family member proposes an appointment. Returns for whom ("both" or their name)."""
    agenda._usable_clock(clock)
    title = agenda.clean(f.get("title"), agenda.TITLE_CHARS)
    if not title:
        raise agenda.Refused("Falta qué es: por ejemplo «Médico de cabecera».")
    where = agenda.clean(f.get("where"), agenda.TITLE_CHARS)
    people = {str(p["id"]): p for p in household(conn, contact_id)}
    target = f.get("for") or ""
    if (target != "both" or len(people) < 2) and target not in people:
        raise agenda.Refused("Elija para quién es.")
    at = agenda._local(agenda._day(f.get("day")), agenda._time(f.get("time")), clock)
    agenda._future(clock, at)
    if conn.execute("SELECT count(*) FROM family_proposals WHERE contact_id = ? AND status = 'open'",
                    (contact_id,)).fetchone()[0] >= OPEN_PROPOSALS:
        raise agenda.Refused("Ya hay muchas citas propuestas sin contestar. Espere a que las miren.")
    conn.execute("INSERT INTO family_proposals (contact_id, person_id, title, starts_at_utc, location, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (contact_id, None if target == "both" else int(target), title,
                                               utc_iso(at), where, utc_iso(clock.now())))
    return "both" if target == "both" else people[target]["call_name"]


def my_proposals(conn, clock: Clock, contact_id: int) -> list[dict]:
    names = {p["id"]: p["call_name"] for p in household(conn, contact_id)}
    today = clock.now().date()
    return [{"id": r["id"], "title": r["title"], "where": r["location"],
             "when": agenda._when_words(from_utc_iso(r["starts_at_utc"], clock.tz), today),
             "for": "los dos" if r["person_id"] is None else names.get(r["person_id"], "?"),
             "state": {"open": "sin contestar", "accepted": "apuntada", "declined": "no apuntada",
                       "withdrawn": "retirada"}[r["status"]], "open": r["status"] == "open"}
            for r in conn.execute("SELECT * FROM family_proposals WHERE contact_id = ? ORDER BY id DESC LIMIT 20",
                                  (contact_id,))]


def withdraw(conn, clock: Clock, contact_id: int, proposal_id: int) -> None:
    conn.execute("UPDATE family_proposals SET status = 'withdrawn', answered_at = ? WHERE id = ? AND contact_id = ? "
                 "AND status = 'open'", (utc_iso(clock.now()), proposal_id, contact_id))


# --- the person's side: proposals waiting for them ------------------------------------------------


def waiting(conn, clock: Clock, person_id: int) -> list[dict]:
    """Open proposals for this person (or for both), still in the future: the family's, then Lea's (id "lea-N")."""
    today = clock.now().date()
    out = []
    for r in conn.execute("SELECT * FROM operator_proposals WHERE kind = 'appointment' AND status = 'open' AND "
                          "(person_id = ? OR person_id IS NULL)", (person_id,)):
        p = json.loads(r["payload"])
        at = datetime.fromisoformat(p["starts_at"])
        if at > clock.now():
            out.append({"id": f"lea-{r['id']}", "from": "Lea", "title": p["title"], "where": p["location"],
                        "when": agenda._when_words(at, today), "shared": r["person_id"] is None})
    for r in conn.execute("SELECT p.*, c.name FROM family_proposals p JOIN contacts c ON c.id = p.contact_id "
                          "WHERE p.status = 'open' AND (p.person_id = ? OR p.person_id IS NULL) AND p.starts_at_utc > ? "
                          "ORDER BY p.starts_at_utc", (person_id, utc_iso(clock.now()))):
        out.append({"id": r["id"], "from": r["name"], "title": r["title"], "where": r["location"],
                    "when": agenda._when_words(from_utc_iso(r["starts_at_utc"], clock.tz), today),
                    "shared": r["person_id"] is None})
    return out


def _waiting_row(conn, clock: Clock, person_id: int, proposal_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT p.*, c.name FROM family_proposals p JOIN contacts c ON c.id = p.contact_id "
                       "WHERE p.id = ? AND p.status = 'open'", (proposal_id,)).fetchone()
    if row is None or (row["person_id"] is not None and row["person_id"] != person_id) or \
            from_utc_iso(row["starts_at_utc"], clock.tz) <= clock.now():
        raise agenda.Refused("Esa propuesta ya no está.")
    return row


def _lea_row(conn, clock: Clock, person_id: int, proposal_id: int) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM operator_proposals WHERE id = ? AND kind = 'appointment' AND status = 'open'",
                     (proposal_id,)).fetchone()
    if r is None or (r["person_id"] is not None and r["person_id"] != person_id) or \
            datetime.fromisoformat(json.loads(r["payload"])["starts_at"]) <= clock.now():
        raise agenda.Refused("Esa propuesta ya no está.")
    return r


def proposal_ref(value: str | None) -> tuple[str, int] | None:
    """A proposal's id from a form: "12" (family) or "lea-12" (Lea)."""
    value = value or ""
    lea = value.startswith("lea-")
    digits = value[4:] if lea else value
    if digits.isascii() and digits.isdigit() and len(digits) <= 9:
        return ("lea" if lea else "family", int(digits))
    return None


def propose_accept_any(conn, clock: Clock, person_id: int, ref: tuple[str, int] | None) -> agenda.Proposal:
    if ref is None:
        raise agenda.Refused("Esa propuesta ya no está.")
    if ref[0] == "family":
        return propose_accept(conn, clock, person_id, ref[1])
    agenda._usable_clock(clock)
    r = _lea_row(conn, clock, person_id, ref[1])
    p = json.loads(r["payload"])
    agenda._health_ok(conn, r["person_id"])
    at = datetime.fromisoformat(p["starts_at"])
    readback = (f"Cita propuesta por Lea: «{p['title']}», {agenda._when_words(at, clock.now().date())}"
                f"{', en ' + p['location'] if p['location'] else ''}. {agenda._for_whom(r['person_id'])}")
    return agenda._save(conn, clock, person_id, "add_appointment",
                        {"person_id": r["person_id"], "title": p["title"], "starts_at": at.isoformat(),
                         "location": p["location"], "operator_proposal_id": r["id"]}, readback)


def decline_any(conn, clock: Clock, person_id: int, ref: tuple[str, int] | None) -> None:
    if ref is None:
        raise agenda.Refused("Esa propuesta ya no está.")
    if ref[0] == "family":
        return decline(conn, clock, person_id, ref[1])
    _lea_row(conn, clock, person_id, ref[1])
    conn.execute("UPDATE operator_proposals SET status = 'declined', answered_at = ? WHERE id = ?",
                 (utc_iso(clock.now()), ref[1]))


def propose_accept(conn, clock: Clock, person_id: int, proposal_id: int) -> agenda.Proposal:
    """The person wants it: the usual read-back, saying who proposed it."""
    agenda._usable_clock(clock)
    r = _waiting_row(conn, clock, person_id, proposal_id)
    agenda._health_ok(conn, r["person_id"])
    at = from_utc_iso(r["starts_at_utc"], clock.tz)
    readback = (f"Cita propuesta por {r['name']}: «{r['title']}», {agenda._when_words(at, clock.now().date())}"
                f"{', en ' + r['location'] if r['location'] else ''}. {agenda._for_whom(r['person_id'])}")
    return agenda._save(conn, clock, person_id, "add_appointment",
                        {"person_id": r["person_id"], "title": r["title"], "starts_at": at.isoformat(),
                         "location": r["location"], "family_proposal_id": r["id"]}, readback)


def decline(conn, clock: Clock, person_id: int, proposal_id: int) -> None:
    _waiting_row(conn, clock, person_id, proposal_id)
    conn.execute("UPDATE family_proposals SET status = 'declined', answered_at = ? WHERE id = ?",
                 (utc_iso(clock.now()), proposal_id))


# --- the family app (Lea's design: Inicio, Calendario, Mensajes, Avisos) -----------------------------------

PHOTOS_KEEP = timedelta(days=90)
PHOTOS_PER_DAY = 20


def their_people(conn, contact_id: int) -> list[dict]:
    """The people at home this family member belongs to: the contact's person, or everyone for a shared one."""
    return household(conn, contact_id)


def doses_shared(conn, clock: Clock, contact_id: int) -> list[dict]:
    """Per person: today's doses taken, only with that person's `family_sees_doses`; else "No compartido"."""
    from kore.memory import meds, store

    out = []
    for p in their_people(conn, contact_id):
        if not store.has_consent(conn, p["id"], "family_sees_doses"):
            out.append({"name": p["call_name"], "shared": False})
            continue
        try:
            doses = meds.status(conn, clock, p["id"])["doses"]
        except meds.ClockUntrusted:
            out.append({"name": p["call_name"], "shared": True, "unknown": True})
            continue
        out.append({"name": p["call_name"], "shared": True, "taken": sum(d["state"] == "taken" for d in doses),
                    "total": len(doses), "times": [d["time"] for d in doses]})
    return out


def days_ahead(conn, clock: Clock, contact_id: int, days: int = 4) -> list[dict]:
    """What the family may see coming: birthdays, and appointments they proposed that were saved (and the dose
    times of people who share them). Nothing else from the home's calendar."""
    from kore.memory import store

    people = their_people(conn, contact_id)
    ids = {p["id"] for p in people}
    names = {p["id"]: p["call_name"] for p in people}
    today = clock.now().date()
    shared_doses = {d["name"]: d for d in doses_shared(conn, clock, contact_id) if d.get("shared") and d.get("times")}
    accepted = conn.execute("SELECT title, starts_at_utc, person_id FROM family_proposals WHERE contact_id = ? AND "
                            "status = 'accepted'", (contact_id,)).fetchall()
    out = []
    for i in range(days):
        day = today + timedelta(days=i)
        items = []
        for c in conn.execute("SELECT name, relation, for_person FROM contacts WHERE birthday_md = ? AND "
                              "birthday_reminders = 1", (day.strftime("%m-%d"),)):
            if c["for_person"] is None or c["for_person"] in ids:
                items.append({"time": "", "text": f"Cumpleaños de {c['name']}"})
        for r in accepted:
            at = from_utc_iso(r["starts_at_utc"], clock.tz)
            if at.date() == day and (r["person_id"] is None or r["person_id"] in ids):
                items.append({"time": at.strftime("%H:%M"), "text": f"{r['title']} · "
                              f"{names.get(r['person_id'], 'los dos')}", "note": "La propusiste tú: está apuntada"})
        if i == 0:
            for name, d in shared_doses.items():
                for t in d["times"]:
                    items.append({"time": t, "text": f"Pastilla · {name}"})
        items.sort(key=lambda x: (x["time"] == "", x["time"]))
        out.append({"date": day, "items": items})
    return out


def alerts(conn, clock: Clock, contact_id: int) -> dict:
    """What this family member gets alerts for (their people's choices), and the last week's alerts to them."""
    from kore.memory import store

    c = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    kinds = []
    if c and c["help_contact"]:
        for p in their_people(conn, contact_id):
            kinds.append(f"«Pepe, ayuda», una caída o el botón · {p['call_name']}"
                         + ("" if store.has_consent(conn, p["id"], "emergency_path") else " (Pepe pregunta antes)"))
            if store.has_consent(conn, p["id"], "family_missed_meds"):
                kinds.append(f"Pastillas olvidadas varias veces seguidas · {p['call_name']}")
    since = utc_iso(clock.now() - timedelta(days=7))
    words = {"help": "Petición de ayuda", "missed_meds": "Pastillas olvidadas", "stop_request": "Quiere dejar una pastilla"}
    recent = [{"what": words.get(r["kind"], r["kind"]), "text": r["text"],
               "at": from_utc_iso(r["created_at_utc"], clock.tz),
               "state": {"sent": "Enviado", "pending": "Enviándose", "failed": "No se pudo enviar"}[r["status"]]}
              for r in conn.execute("SELECT * FROM family_out WHERE contact_id = ? AND kind IN ('help', 'missed_meds', "
                                    "'stop_request') AND created_at_utc >= ? ORDER BY id DESC", (contact_id, since))]
    return {"kinds": kinds, "recent": recent}


def all_good(conn, clock: Clock, contact_id: int) -> bool:
    since = utc_iso(clock.now().replace(hour=0, minute=0, second=0, microsecond=0))
    return conn.execute("SELECT count(*) FROM family_out WHERE contact_id = ? AND kind IN ('help', 'missed_meds') AND "
                        "created_at_utc >= ?", (contact_id, since)).fetchone()[0] == 0


# --- photos between the family and the home -------------------------------------------------------------


def _purge_photos(conn, clock: Clock, folder) -> None:
    """Family photos after 90 days, and photos chosen to send that never got their yes (after a day)."""
    import time
    if folder.exists():
        kept = {r["file"] for r in conn.execute("SELECT file FROM family_photos WHERE file LIKE 'p%'")}
        for f in folder.glob("p*.jpg"):
            try:
                if f.name not in kept and time.time() - f.stat().st_mtime > 86400:
                    f.unlink(missing_ok=True)
            except OSError:
                continue
    for r in conn.execute("SELECT id, file FROM family_photos WHERE created_at < ?",
                          (utc_iso(clock.now() - PHOTOS_KEEP),)).fetchall():
        try:
            (folder / r["file"]).unlink(missing_ok=True)
        except OSError:
            continue
        conn.execute("DELETE FROM family_photos WHERE id = ?", (r["id"],))


def save_photo(conn, clock: Clock, folder, contact_id: int, person_id: int | None, direction: str, jpeg: bytes,
               caption: str) -> int:
    import secrets

    since = utc_iso(clock.now() - timedelta(days=1))
    if conn.execute("SELECT count(*) FROM family_photos WHERE contact_id = ? AND direction = ? AND created_at > ?",
                    (contact_id, direction, since)).fetchone()[0] >= PHOTOS_PER_DAY:
        raise agenda.Refused("Hoy ya se han mandado muchas fotos. Mañana se pueden mandar más.")
    folder.mkdir(parents=True, exist_ok=True)
    name = "f" + secrets.token_hex(16) + ".jpg"
    pid = conn.execute("INSERT INTO family_photos (contact_id, person_id, direction, file, caption, created_at) "
                       "VALUES (?, ?, ?, ?, ?, ?)", (contact_id, person_id, direction, name,
                                                     agenda.clean(caption, 120), utc_iso(clock.now()))).lastrowid
    try:
        (folder / name).write_bytes(jpeg)
    except OSError:
        conn.execute("DELETE FROM family_photos WHERE id = ?", (pid,))
        raise agenda.Refused("No se ha podido guardar la foto.") from None
    _purge_photos(conn, clock, folder)
    return pid


def photos_for_person(conn, clock: Clock, person_id: int) -> list[dict]:
    """Photos the family sent to this person (or to both), newest first; seen once shown."""
    rows = conn.execute("SELECT p.*, c.name FROM family_photos p JOIN contacts c ON c.id = p.contact_id WHERE "
                        "p.direction = 'to_home' AND (p.person_id = ? OR p.person_id IS NULL) AND "
                        "(c.for_person IS NULL OR c.for_person = ?) ORDER BY p.id DESC LIMIT 30",
                        (person_id, person_id)).fetchall()
    return [{"id": r["id"], "from": r["name"], "caption": r["caption"], "new": r["seen_at"] is None,
             "at": from_utc_iso(r["created_at"], clock.tz)} for r in rows]


def mark_photos_seen(conn, clock: Clock, ids: list[int]) -> None:
    for i in ids:
        conn.execute("UPDATE family_photos SET seen_at = ? WHERE id = ? AND seen_at IS NULL", (utc_iso(clock.now()), i))


def new_photos(conn, person_id: int) -> list[dict]:
    rows = conn.execute("SELECT p.caption, c.name FROM family_photos p JOIN contacts c ON c.id = p.contact_id WHERE "
                        "p.direction = 'to_home' AND p.seen_at IS NULL AND (p.person_id = ? OR p.person_id IS NULL) "
                        "AND (c.for_person IS NULL OR c.for_person = ?)", (person_id, person_id)).fetchall()
    return [{"from": r["name"], "caption": r["caption"]} for r in rows]


def photo_file(conn, folder, photo_id: int, *, person_id: int | None = None, contact_id: int | None = None):
    """The file, only for someone who may see it: the person it was sent to (or from), or the family member."""
    r = conn.execute("SELECT p.*, c.for_person FROM family_photos p JOIN contacts c ON c.id = p.contact_id "
                     "WHERE p.id = ?", (photo_id,)).fetchone()
    if r is None:
        return None
    if contact_id is not None:
        ok = r["contact_id"] == contact_id
    else:
        ok = (r["for_person"] is None or r["for_person"] == person_id) and (
            r["person_id"] is None or r["person_id"] == person_id)
    path = folder / r["file"]
    return path.read_bytes() if ok and path.is_file() else None


def photos_in_thread(conn, clock: Clock, contact_id: int) -> list[dict]:
    names = {p["id"]: p["call_name"] for p in household(conn)}
    return [{"who": "me" if r["direction"] == "to_home" else "home", "photo_id": r["id"], "text": r["caption"],
             "at": from_utc_iso(r["created_at"], clock.tz),
             "state": "" if r["direction"] == "to_family" else ("La ha visto" if r["seen_at"] else "Todavía no la ha visto"),
             "from": names.get(r["person_id"], "")}
            for r in conn.execute("SELECT * FROM family_photos WHERE contact_id = ? ORDER BY id DESC LIMIT 30",
                                  (contact_id,))]


def propose_photo_to_family(conn, clock: Clock, folder, person_id: int, contact_id: int, jpeg: bytes,
                            caption: str) -> agenda.Proposal:
    """Carmen chose a photo and who gets it: stored aside, sent only after "Sí, envíala"."""
    from kore.web import elder

    c = _contact(conn, person_id, contact_id)
    if not c["telegram_chat_id"] and not elder._has_web(conn, contact_id):
        raise agenda.Refused(f"Todavía no se pueden mandar fotos a {c['name']}: tiene que conectarse primero.")
    import secrets

    folder.mkdir(parents=True, exist_ok=True)
    name = "p" + secrets.token_hex(16) + ".jpg"  # waiting for the yes; deleted if it never comes (a day)
    (folder / name).write_bytes(jpeg)
    return agenda._save(conn, clock, person_id, "send_photo",
                        {"contact_id": contact_id, "file": name, "caption": agenda.clean(caption, 120)},
                        f"Le mando a {c['name']} esta foto" + (f": «{agenda.clean(caption, 120)}»" if caption else "")
                        + ".", "Sí, envíala")
