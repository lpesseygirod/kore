"""Read-backs in spoken Spanish of Spain: what KORE says before a change, word for word, and the person says yes
to. Code writes them (not the model), so the time, the day and the name in the sentence that gets the yes are
exactly what will be saved.

Rules (training base, 01-identity-and-voice §5): times and dates in words, with the part of the day; no digits;
neutral about KORE's own gender; usted or tú from the person's setting; the question is last.
"""

from __future__ import annotations

from datetime import date, datetime

from kore.brain.fallback import MONTHS, WEEKDAYS

UNITS = ["cero", "una", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce",
         "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuna",
         "veintidós", "veintitrés", "veinticuatro", "veinticinco", "veintiséis", "veintisiete", "veintiocho",
         "veintinueve"]
TENS = {30: "treinta", 40: "cuarenta", 50: "cincuenta"}
DAY_WORDS = ["", "uno", *UNITS[2:21], "veintiuno", *UNITS[22:30], "treinta", "treinta y uno"]
WEEKDAY_CODES = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
PLURAL_WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábados", "domingos"]


def number(n: int) -> str:
    """0–59 in words, feminine where it matters ('una', 'veintiuna': las horas, las pastillas)."""
    if n < 30:
        return UNITS[n]
    tens, unit = divmod(n, 10)
    return TENS[tens * 10] + (f" y {UNITS[unit]}" if unit else "")


def part_of_day(hour: int) -> str:
    if hour == 0:
        return "de la noche"
    if hour < 6:
        return "de la madrugada"
    if hour < 12:
        return "de la mañana"
    if hour < 15:
        return "del mediodía"
    if hour < 21:
        return "de la tarde"
    return "de la noche"


def clock(hhmm: str) -> str:
    """'09:00' → 'las nueve de la mañana'; '20:45' → 'las nueve menos cuarto de la noche'; '13:10' → 'la una y diez
    del mediodía'; '12:00' → 'las doce del mediodía'."""
    h, m = (int(x) for x in hhmm.split(":"))
    shown, minutes = h, ""
    if m == 15:
        minutes = " y cuarto"
    elif m == 30:
        minutes = " y media"
    elif m == 45:
        shown, minutes = (h + 1) % 24, " menos cuarto"
    elif m > 30:
        shown, minutes = (h + 1) % 24, f" menos {number(60 - m)}"
    elif m:
        minutes = f" y {number(m)}"
    word = number(shown % 12 or 12)
    art = "la" if shown % 12 == 1 else "las"
    return f"{art} {word}{minutes} {part_of_day(shown if m <= 30 else h)}"


def at(hhmm: str) -> str:
    """'a las nueve de la mañana', 'a la una del mediodía'."""
    return "a " + clock(hhmm)


def times(values: list[str]) -> str:
    return join([at(t) for t in values])


def day(d: date, today: date | None = None) -> str:
    """'hoy', 'mañana', 'pasado mañana', or 'el martes once de noviembre' (the year only if it isn't this one)."""
    if today is not None:
        delta = (d - today).days
        if delta == 0:
            return "hoy"
        if delta == 1:
            return "mañana"
        if delta == 2:
            return "pasado mañana"
    text = f"el {WEEKDAYS[d.weekday()]} {DAY_WORDS[d.day]} de {MONTHS[d.month - 1]}"
    if today is not None and d.year != today.year:
        text += f" de {d.year}"
    return text


def when(dt: datetime, today: date | None = None) -> str:
    """'mañana a las diez de la mañana', 'el martes once de noviembre a las cinco y media de la tarde'."""
    return f"{day(dt.date(), today)} {at(dt.strftime('%H:%M'))}"


def of(text: str) -> str:
    """'el martes …' → 'del martes …'; 'mañana …' → 'de mañana …'."""
    return "del " + text[3:] if text.startswith("el ") else "de " + text


def birthday(mm_dd: str) -> str:
    m, d = (int(x) for x in mm_dd.split("-"))
    return f"el {DAY_WORDS[d]} de {MONTHS[m - 1]}"


def rule(value: str, first: date, today: date | None = None) -> str:
    """How often, for a reminder: once | daily | weekly:MON,THU | every:N."""
    if value == "once":
        return day(first, today)
    if value == "daily":
        return "todos los días"
    if value.startswith("weekly:"):
        names = [PLURAL_WEEKDAYS[WEEKDAY_CODES[c]] for c in value.split(":", 1)[1].split(",")]
        return "los " + join(names) if len(names) > 1 else "todos los " + names[0]
    if value.startswith("every:"):
        n = int(value.split(":", 1)[1])
        return "todos los días" if n <= 1 else "cada " + days(n)
    return value


def days(n: int) -> str:
    """'un día', 'tres días', 'veintiún días' (masculine, unlike hours)."""
    special = {1: "un día", 21: "veintiún días", 31: "treinta y un días"}
    if n in special:
        return special[n]
    return f"{DAY_WORDS[n]} días" if n < len(DAY_WORDS) else f"{n} días"


def join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]


def you(form: str, usted: str, tu: str) -> str:
    """Pick the usted or tú wording for the person KORE is talking to."""
    return tu if form in ("tú", "tu") else usted
