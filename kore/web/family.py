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
