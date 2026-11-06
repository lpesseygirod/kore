"""Fixed Spanish phrases for when Claude can't be reached (BUILD-PLAN 2, "offline fallback").

Normally Claude words every event. With no internet, these templates are
spoken instead (and can be pre-recorded on the device). Phrased without tú or
usted, so they fit either; the choice between the two is still Lea's.
"""

from __future__ import annotations

from datetime import date

WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]
NUMBERS = ["doce", "una", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once"]

CONNECTION_PROBLEM = "Tengo un problema de conexión. Los recordatorios siguen funcionando."
CLOCK_UNTRUSTED = (
    "No estoy seguro de la hora, así que no puedo llevar la cuenta de las medicinas "
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
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]


def phrase(event) -> str | None:
    """The fallback sentence for an engine Event, or None if nothing should be said aloud."""
    d = event.data
    k = event.kind
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
    return None  # family alerts etc. are messages, not speech
