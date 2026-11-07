"""The home's link to the family: "Pepe, ayuda", the button, messages they ask for, alerts.

Plain code, no Claude (BUILD-PLAN 0b.2): who is contacted, when, and what is
said out loud about it. Claude only keeps them company afterwards.

- Help (the phrase, a fall, the button): if they agreed to the emergency path at
  setup, their help contacts get a message at once, by Telegram and SMS. If not,
  Pepe asks first ("¿Aviso ahora a Lucía?") and a clear yes sends it.
  If nothing could be sent, Pepe says so straight away and points them to their
  phone or their teleasistencia button (0b.7); it keeps trying, and says when it got through.
- Missed medicine: only if they agreed; Telegram only, no details beyond the fact.
- Lea (the operator) gets only device messages.

Spoken lines avoid tú and usted, like the other fixed phrases.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta, timezone

from kore.brain.session import Session
from kore.family import messages as msgs
from kore.family.channels import Channel
from kore.memory import store
from kore.memory.db import utc_iso
from kore.scheduler.clock import Clock

PHONE_OR_TELEASISTENCIA = "Hay que llamar con el teléfono o pulsar el botón de la teleasistencia."
NO_ONE = "No tengo a nadie a quien avisar todavía. " + PHONE_OR_TELEASISTENCIA
NOT_SENT = "No he podido avisar a nadie: no hay conexión. " + PHONE_OR_TELEASISTENCIA + " Yo sigo intentándolo."
NOT_REACHED = "No he podido avisar a nadie. " + PHONE_OR_TELEASISTENCIA
RECENT_HELP = timedelta(minutes=3)  # a second "¡ayuda!" within this doesn't send twice to the same people
MAX_WORDS_SENT = 120


@dataclass
class HelpOutcome:
    say: str                      # said by code, at once
    sent_to: list[str] = field(default_factory=list)
    trying: bool = False          # nothing went yet; still trying
    asked: bool = False           # someone still to be asked about ("¿Aviso a Lucía?")
    action_id: int | None = None
    none: bool = False            # nobody to contact
    failed: bool = False          # nothing went, and retrying won't help (wrong number, no key)

    def summary(self) -> str:
        parts = []
        if self.none:
            return "there is no help contact set up"
        if self.sent_to:
            parts.append(f"a message reached {', '.join(self.sent_to)}")
        elif self.trying:
            parts.append("no message could be sent yet (no connection); still trying")
        elif self.failed:
            parts.append("no message could be sent (not a connection problem)")
        if self.asked:
            parts.append("Pepe asked whether to tell someone (they hadn't agreed to messages without asking)")
        return "; ".join(parts)


class Family:
    def __init__(self, conn: sqlite3.Connection, clock: Clock, channels: dict[str, Channel] | None = None):
        self.conn = conn
        self.clock = clock
        self.channels = channels or {}

    # --- help ------------------------------------------------------------------------

    def call_for_help(self, session: Session, source: str, words: str = "", ask_first: bool = False) -> HelpOutcome:
        """source: help (the phrase) | fall | button. `words`: what they said, for a fall.
        ask_first: always ask, even with the agreed path (help Claude noticed, not the agreed phrase)."""
        people = [session.speaker_id] if session.speaker_id is not None else [p.id for p in session.household]
        reachable = self._reachable(msgs.help_contacts(self.conn, people))
        if not reachable:
            msgs.queue(self.conn, self.clock, "operator", "telegram",
                       "Pepe: alguien ha pedido ayuda y no hay ningún contacto de ayuda con Telegram o teléfono. "
                       "Revisa: uv run python -m kore.family status")
            msgs.deliver(self.conn, self.clock, self.channels)
            return HelpOutcome(NO_ONE, none=True)
        recent = self._recent_help()  # contact id -> statuses of their help rows in the last few minutes
        new = [c for c in reachable if c["id"] not in recent]
        if not new:
            sent = sorted(c["name"] for c in reachable if "sent" in recent[c["id"]])
            if sent:
                return HelpOutcome(f"Ya he avisado a {_join(sent)} hace un momento. Me quedo aquí.", sent_to=sent)
            if any("pending" in recent[c["id"]] for c in reachable):
                return HelpOutcome("Sigo intentando avisar. " + PHONE_OR_TELEASISTENCIA, trying=True)
            return HelpOutcome(NOT_REACHED, failed=True)
        agreed = [] if ask_first else [p for p in people if store.has_consent(self.conn, p, "emergency_path")]
        agreed_ids = {c["id"] for c in self._reachable(msgs.help_contacts(self.conn, agreed))} if agreed else set()
        now = [c["id"] for c in new if c["id"] in agreed_ids]
        ask = [c for c in new if c["id"] not in agreed_ids]
        out = self.send_help(now, source, words, session.speaker_id) if now else HelpOutcome("")
        if ask:
            names = _join([c["name"] for c in ask])
            question = f"¿Aviso {'también ' if now else ''}ahora a {names}?"
            a = session.propose("send_help", {"contacts": [c["id"] for c in ask], "source": source, "words": words,
                                              "from_person": session.speaker_id}, question)
            a.speaker_id = None  # whoever is at home may say yes to calling for help
            out.say = f"{out.say} {question}".strip()
            out.asked, out.action_id = True, a.id
        return out

    def send_help(self, contact_ids: list[int], source: str, words: str, from_person: int | None) -> HelpOutcome:
        """Send the help message to these help contacts, now: the agreed path, or after a yes."""
        contacts = [c for c in self._reachable(self.conn.execute("SELECT * FROM contacts").fetchall())
                    if c["id"] in contact_ids]
        if not contacts:
            return HelpOutcome(NO_ONE, none=True)
        text = self._help_text(source, words, from_person)
        group = None
        ids = []
        for c in contacts:
            for channel, addr in (("telegram", c["telegram_chat_id"]), ("sms", c["phone"])):
                if addr:
                    i = msgs.queue(self.conn, self.clock, "help", channel, text, c["id"], from_person, group)
                    group = group or i
                    ids.append(i)
        attempts = msgs.deliver(self.conn, self.clock, self.channels, ids)
        sent = sorted({a.contact for a in attempts if a.ok and a.contact})
        msgs.told(self.conn, group, settled=bool(sent))
        if sent:
            return HelpOutcome(f"Ya he avisado a {_join(sent)} con un mensaje. Me quedo aquí.", sent_to=sent)
        pending = self.conn.execute("SELECT count(*) FROM family_out WHERE group_id = ? AND status = 'pending'",
                                    (group,)).fetchone()[0]
        if pending:
            return HelpOutcome(NOT_SENT, trying=True)
        msgs.told(self.conn, group, settled=True)  # nothing more will happen: they've been told
        msgs.queue(self.conn, self.clock, "operator", "telegram",
                   "Pepe: alguien ha pedido ayuda y el mensaje no se ha podido enviar (contacto o clave incorrectos). "
                   "Revisa: uv run python -m kore.family status")
        msgs.deliver(self.conn, self.clock, self.channels)
        return HelpOutcome(NOT_REACHED, failed=True)

    def _help_text(self, source: str, words: str, from_person: int | None) -> str:
        # Without a known speaker, never guess who it was: name the home.
        home = [r["call_name"] for r in self.conn.execute("SELECT call_name FROM people ORDER BY id")]
        who = self._call_name(from_person) if from_person is not None else (
            home[0] if len(home) == 1 else f"Alguien en casa ({' y '.join(home)})")
        what = {"help": "ha pedido ayuda a Pepe", "button": "ha pulsado el botón de ayuda de Pepe",
                "fall": "ha dicho a Pepe"}[source]
        if source == "fall":
            what += f": «{' '.join(words.split()[:MAX_WORDS_SENT])[:200]}»"
        when = f" a las {self.clock.now().strftime('%H:%M')}" if self.clock.trusted() else ""
        return (f"AYUDA: {who} {what}{when}. Llama en cuanto puedas. Pepe no es un servicio de emergencias: "
                "si no contesta y te preocupa, llama al 112.")

    def _recent_help(self) -> dict[int, set[str]]:
        since = utc_iso(self.clock.now().astimezone(timezone.utc) - RECENT_HELP)
        out: dict[int, set[str]] = {}
        for r in self.conn.execute("SELECT contact_id, status FROM family_out WHERE kind = 'help' AND "
                                   "created_at_utc >= ?", (since,)):
            out.setdefault(r["contact_id"], set()).add(r["status"])
        return out

    @staticmethod
    def _reachable(contacts: list[sqlite3.Row]) -> list[sqlite3.Row]:
        return [c for c in contacts if c["telegram_chat_id"] or c["phone"]]

    # --- messages they ask for -------------------------------------------------------------

    def send_message(self, contact_id: int, text: str, from_person: int) -> dict:
        """A message they dictated, after their clear yes. Sent now if possible, else kept and retried."""
        body = f"{self._call_name(from_person)} (con Pepe): {text}"
        i = msgs.queue(self.conn, self.clock, "message", "telegram", body, contact_id, from_person)
        ok = any(a.ok for a in msgs.deliver(self.conn, self.clock, self.channels, [i]))
        msgs.told(self.conn, i, settled=ok)
        name = msgs.contact_name(self.conn, contact_id)
        if ok:
            return {"sent": True, "to": name}
        return {"sent": False, "to": name,
                "say": "Say plainly it couldn't be sent right now; you'll keep trying and tell them when it goes."}

    # --- alerts agreed at setup ---------------------------------------------------------------

    def missed_meds(self, person_id: int, missed_in_a_row: int) -> list[int]:
        """Tell their help contacts (Telegram) that doses went unanswered. Only called if they agreed."""
        name = self._call_name(person_id)
        text = (f"Pepe: {name} no ha contestado a los recordatorios de sus últimas {missed_in_a_row} tomas de "
                f"medicación. {name} aceptó que te avisara en estos casos. Quizá puedas llamar.")
        return self._to_help_contacts("missed_meds", person_id, text)

    def stop_request(self, person_id: int, medication: str) -> list[int]:
        """They asked on two days to stop a medicine reminder, and it was stopped (PEPE.md 4b)."""
        name = self._call_name(person_id)
        text = (f"Pepe: {name} ha pedido dos días distintos dejar de recibir el recordatorio de {medication}, "
                f"y lo he quitado. Quizá quieras hablarlo con {name} o con su médico.")
        return self._to_help_contacts("stop_request", person_id, text)

    def _to_help_contacts(self, kind: str, person_id: int, text: str) -> list[int]:
        contacts = [c for c in msgs.help_contacts(self.conn, [person_id]) if c["telegram_chat_id"]]
        if not contacts:
            ids = [msgs.queue(self.conn, self.clock, "operator", "telegram",
                              f"Pepe: un aviso a la familia ({kind}) no tiene contacto con Telegram. "
                              "Revisa: uv run python -m kore.family status")]
        else:
            ids = [msgs.queue(self.conn, self.clock, kind, "telegram", text, c["id"]) for c in contacts]
        msgs.deliver(self.conn, self.clock, self.channels, ids)
        return ids

    def handle_item(self, item) -> None:
        """A "family" item from the manners gate (never spoken). The loop marks it done afterwards:
        it's kept in family_out until sent."""
        ev = item.event
        if ev.kind == "family_alert_missed_meds":
            for p in ev.person_ids:
                if store.has_consent(self.conn, p, "family_missed_meds"):  # checked again: they may have changed it
                    self.missed_meds(p, ev.data.get("missed_in_a_row", 2))

    # --- the loop --------------------------------------------------------------------------

    def tick(self) -> list:
        """Retry what's due; fetch family messages; return events to tell people how things went."""
        msgs.deliver(self.conn, self.clock, self.channels)
        tg = self.channels.get("telegram")
        if tg is not None and hasattr(tg, "poll"):
            msgs.poll(self.conn, self.clock, tg)
        everyone = [r["id"] for r in self.conn.execute("SELECT id FROM people ORDER BY id")]
        return msgs.follow_ups(self.conn, self.clock, everyone)

    def _call_name(self, person_id: int) -> str:
        row = self.conn.execute("SELECT call_name FROM people WHERE id = ?", (person_id,)).fetchone()
        return row["call_name"] if row else "?"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]
