"""Deterministic reading of short answers: a clear yes, and "it's me, Carmen".

Code, not Claude, decides whether the person said yes. A confirmation or an
identity only counts when these functions agree. They are deliberately strict:
anything hedged ("yes, but at 10", "I think so") is not a clear yes, and Pepe
simply asks again.

Spanish first (the trial home), English for tests and later markets.
"""

from __future__ import annotations

import re
import unicodedata


def fold(text: str) -> str:
    """Lowercase, strip accents, keep letters/digits/apostrophes as words."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("’", "'")
    return " ".join(re.sub(r"[^a-z0-9']+", " ", text).split())


def _words(text: str) -> list[str]:
    return fold(text).split()


YES_WORDS = {
    "yes", "yeah", "yep", "yup", "sure", "correct", "right", "exactly", "ok", "okay",
    "claro", "vale", "exacto", "correcto", "perfecto", "afirmativo", "venga", "dale",
}
YES_PHRASES = [
    "it's me", "it is me", "that's me", "that's right", "of course", "go ahead",
    "send it", "do it", "please do",
    "soy yo", "eso es", "de acuerdo", "por supuesto", "adelante",
    "mandalo", "envialo", "hazlo", "asi es", "esta bien",
]
NO_WORDS = {
    "no", "not", "don't", "dont", "nope", "never", "cancel", "wrong",
    "nunca", "tampoco", "ni", "cancela", "cancelalo", "incorrecto", "mal", "mentira",
}
# Hedges, corrections, putting it off, asking to repeat: not a clear yes.
HEDGE_WORDS = {
    "but", "wait", "maybe", "perhaps", "think", "unsure", "actually", "instead", "later", "tomorrow",
    "repeat", "again", "what", "pardon", "sorry",
    "pero", "espera", "quizas", "quiza", "creo", "igual", "mejor", "aunque",
    "luego", "manana", "despues", "repite", "repitelo", "repetir", "perdona", "perdon",
}
HEDGE_PHRASES = ["not sure", "no se", "a lo mejor", "i guess", "i suppose", "otra vez", "mas tarde",
                 "otro dia", "que va", "at ten", "a las", "a la"]
# A time or number in the answer means a correction ("sí, a las diez"): ask again.
NUMBER_WORDS = {
    "una", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce",
    "media", "cuarto", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "half", "quarter",
}


def _has_phrase(folded: str, phrases: list[str]) -> bool:
    padded = f" {folded} "
    return any(f" {p} " in padded for p in phrases)


def is_affirmative(text: str) -> bool:
    """True only for a clear, unhedged yes, with nothing added that changes it."""
    folded = fold(text)
    words = folded.split()
    if not words or "?" in text or "¿" in text:
        return False
    if any(w in NO_WORDS or w in HEDGE_WORDS or w in NUMBER_WORDS or w.isdigit() for w in words):
        return False
    if re.search(r"\d", text) or _has_phrase(folded, HEDGE_PHRASES):
        return False
    if any(w in YES_WORDS for w in words) or _has_phrase(folded, YES_PHRASES):
        return True
    # "sí" with its accent is always yes. Plain "si" also means "if", so it
    # only counts as the first word ("Si, ya está") or on its own.
    if re.search(r"(^|\W)sí(\W|$)", text.lower()):
        return True
    return words[0] == "si" and (len(words) == 1 or re.match(r"\s*si\s*[,.!]", text.lower()) is not None)


def is_negative(text: str) -> bool:
    words = _words(text)
    return any(w in NO_WORDS for w in words)


NEGATED = re.compile(r"\b(no|not|nunca|tampoco)\s*$")


def self_identified(text: str, names: dict[int, list[str]], answering: bool = False) -> int | None:
    """Return the person id if the speaker says who they are ("Soy Carmen").

    `names` maps person id → the names they go by. Mentioning or calling a name
    is not enough ("¿Está Carmen?", "¡Carmen!"), and a negation cancels it
    ("No soy Carmen"). A bare name ("Toni") counts only when `answering` an
    identity question Pepe just asked. Returns None if nobody or more than one
    person matches: a wrong identity is worse than asking again.
    """
    folded = fold(text)
    # Negation only reaches within a clause: "No, soy Toni" vs "No soy Toni".
    clauses = [fold(c) for c in re.split(r"[,.;:!?¡¿…]", text.lower()) if fold(c)]
    found = set()
    for pid, aliases in names.items():
        for alias in aliases:
            a = re.escape(fold(alias))
            for clause in clauses:
                for m in re.finditer(rf"\b(soy|i'm|im|i am|it's|its|it is|this is)\s+(la |el )?{a}\b", clause):
                    if not NEGATED.search(clause[: m.start()]):
                        found.add(pid)
            if answering and re.fullmatch(rf"(si |yes |no )?{a}", folded):
                found.add(pid)
    return found.pop() if len(found) == 1 else None
