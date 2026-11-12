"""Fixed Spanish phrases for when Claude can't be reached (BUILD-PLAN 2, "offline fallback").

Normally Claude words every event. With no internet, these templates are
spoken instead (and can be pre-recorded on the device). Phrased without tú or
usted, so they fit either.
"""

from __future__ import annotations

from datetime import date

WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]
NUMBERS = ["doce", "una", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once"]

CONNECTION_PROBLEM = "Tengo un problema de conexión. Los recordatorios siguen funcionando."
# Too many tool rounds in one turn: the connection is fine, so don't blame it (SR-07). Nothing waits for a yes.
LOST_THE_THREAD = "Perdone, me he liado. ¿Me lo dice otra vez, por favor?"
CLOCK_UNTRUSTED = (
    "No sé la hora con certeza, así que no puedo llevar la cuenta de las medicinas "
    "hasta que se arregle mi reloj. Mejor mirar el pastillero."
)


def spoken_time(hhmm: str) -> str:
    """'09:00' → 'las nueve', '13:30' → 'la una y media', '21:15' → 'las nueve y cuarto'."""
    h, m = (int(x) for x in hhmm.split(":"))
    word = NUMBERS[h % 12]
    art = "la" if h % 12 == 1 else "las"
    minutes = {0: "", 15: " y cuarto", 30: " y media"}.get(m, f" y {m}")
    if m == 45:
        nxt = (h + 1) % 12
        return f"{'la' if nxt == 1 else 'las'} {NUMBERS[nxt]} menos cuarto"
    return f"{art} {word}{minutes}"


def spoken_date(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day} de {MONTHS[d.month - 1]}"


def _join(items: list[str]) -> str:
    if not items:
        return "la familia"
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]


def phrase(event, manner: str = "say") -> str | None:
    """The fallback sentence for an event, or None if nothing should be said aloud.

    manner comes from the manners gate: "short" is for quiet mode, night and
    visitors; "signal" (private, with visitors there) is a soft sound and at most
    their name; "family" is a message, never words.
    """
    d = event.data
    k = event.kind
    if manner == "family":
        return None
    if manner == "signal":
        if k == "morning_greeting":
            return "Buenos días."
        return f"{d['person']}, un recordatorio para cuando pueda." if d.get("person") else None
    if manner == "short":
        return _short(event)
    if k == "medication_reminder":
        what = _join([m["description"] or m["name"] for m in d["medications"]])
        if d["attempt"] == 1 and not d["late"]:
            return f"{d['person']}, son {spoken_time(d['time'])}. Toca {what}."
        return f"{d['person']}, recordatorio de {what}, de {spoken_time(d['time'])}."
    if k == "appointment_day_before":
        where = f", en {d['location']}" if d["location"] else ""
        return f"Mañana a {spoken_time(d['time'])}: {d['title']}{where}."
    if k == "appointment_same_day":
        where = f", en {d['location']}" if d["location"] else ""
        return f"Dentro de una hora: {d['title']}{where}."
    if k == "birthday":
        return f"Hoy es el cumpleaños de {d['name']}."
    if k == "reminder":
        return f"Recordatorio: {d['text']}."
    if k == "water_nudge":
        return "¿Un vaso de agua?"
    if k == "movement_nudge":
        return "¿Un pequeño paseo por casa?"
    if k == "clock_untrusted":
        return CLOCK_UNTRUSTED
    if k == "morning_greeting":
        # Only shared things: we don't know who is in the room yet.
        text = f"Buenos días. Hoy es {spoken_date(date.fromisoformat(d['date']))}."
        for a in d["shared_appointments"]:
            text += f" A {spoken_time(a['time'])}: {a['title']}."
        return text
    if k == "quiet_over":
        return "Hola otra vez. ¿Todo bien?"
    if k == "visit_follow_up":
        return "¿Qué tal ha ido la visita?"
    if k == "welcome_back":
        return "Hola de nuevo. Desde ahora vuelvo con los recordatorios."
    if k == "feedback_check":
        who = f"{d['person']}, " if d.get("person") else ""
        return f"{who}una pregunta rápida: con el cambio de hace unos días, ¿va mejor así?"
    if k == "quiet_suggestion":
        return (f"He notado que muchas tardes toca descanso sobre {spoken_time(d['around'])}. "
                "¿Me quedo en silencio a esa hora cada día?")
    if k == "slower_suggestion":
        return f"{d['person']}, si viene bien, puedo hablar más despacio o más alto. Basta con decírmelo."
    if k == "away_presence_check":
        if d.get("person"):
            return f"Por cierto, ¿ha vuelto ya {d['person']}? Si es así, retomo sus recordatorios."
        return "Hola. Si ya hay alguien de vuelta en casa, basta con decírmelo y retomo los recordatorios."
    if k == "help_sent_late":
        return f"Ya he podido avisar a {_join(d['to'])}."
    if k == "help_not_sent":
        return ("Sigo sin poder avisar a nadie. Hay que llamar con el teléfono "
                "o pulsar el botón de la teleasistencia.")
    if k == "message_sent_late":
        return f"Ya he podido mandar el mensaje a {_join(d['to'])}."
    if k == "message_not_sent":
        return f"Al final no he podido mandar el mensaje a {_join(d['to'])}. Se puede volver a intentar más tarde."
    return None  # family alerts etc. are messages, not speech


def _short(event) -> str | None:
    """Very short and soft: just enough to know what it's about."""
    d = event.data
    k = event.kind
    if k == "medication_reminder":
        return f"{d['person']}, {_join([m['description'] or m['name'] for m in d['medications']])}."
    if k in ("appointment_same_day", "appointment_day_before"):
        when = "En una hora" if k == "appointment_same_day" else f"Mañana a {spoken_time(d['time'])}"
        return f"{when}: {d['title']}."
    if k == "reminder":
        return f"{d['text'][:1].upper()}{d['text'][1:]}."
    if k == "birthday":
        return f"Hoy cumple años {d['name']}."
    if k == "morning_greeting":
        return "Buenos días."
    if k == "clock_untrusted":
        return "No sé la hora con certeza. Mejor mirar el pastillero."
    if k == "away_presence_check" and not d.get("person"):
        return "Hola. ¿Hay alguien de vuelta?"
    return phrase(event)
