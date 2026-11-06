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
    "soy yo", "eso es", "de acuerdo", "por supuesto", "por favor", "adelante",
    "mandalo", "envialo", "hazlo", "asi es", "esta bien",
]
NO_WORDS = {
    "no", "not", "don't", "dont", "nope", "never", "cancel", "wrong",
    "nunca", "tampoco", "ni", "cancela", "cancelalo", "incorrecto", "mal",
}
# Hedges and corrections: not a clear yes.
HEDGE_WORDS = {
    "but", "wait", "maybe", "perhaps", "think", "unsure", "actually", "instead",
    "pero", "espera", "quizas", "quiza", "creo", "igual", "mejor", "aunque",
}
HEDGE_PHRASES = ["not sure", "no se", "a lo mejor", "i guess", "i suppose"]


def _has_phrase(folded: str, phrases: list[str]) -> bool:
    padded = f" {folded} "
    return any(f" {p} " in padded for p in phrases)


def is_affirmative(text: str) -> bool:
    """True only for a clear, unhedged yes."""
    folded = fold(text)
    words = folded.split()
    if not words:
        return False
    if any(w in NO_WORDS for w in words) or any(w in HEDGE_WORDS for w in words):
        return False
    if _has_phrase(folded, HEDGE_PHRASES):
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


def self_identified(text: str, names: dict[int, list[str]]) -> int | None:
    """Return the person id if the speaker says who they are ("Soy Carmen").

    `names` maps person id → the names they go by. Mentioning a name is not
    enough ("Did Carmen take hers?"): it must be a self-introduction.
    Returns None if nobody or more than one person matches.
    """
    folded = fold(text)
    found = set()
    for pid, aliases in names.items():
        for alias in aliases:
            a = re.escape(fold(alias))
            patterns = [
                rf"\b(soy|i'm|im|i am|it's|its|it is|this is|habla|aqui)\s+(la |el )?{a}\b",
                rf"\b{a}\s+(aqui|here|speaking|al habla)\b",
                rf"^(si |yes |no )?{a}$",  # answering "who is it?" with just a name
            ]
            if any(re.search(p, folded) for p in patterns):
                found.add(pid)
    return found.pop() if len(found) == 1 else None
