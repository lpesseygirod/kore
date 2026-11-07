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


# Words only English uses (none of them a Spanish word), and common Spanish ones, to
# catch Claude's own notes written in English before the voice says them.
ENGLISH_WORDS = {
    "the", "is", "are", "was", "were", "i", "i'll", "i'm", "it's", "don't", "can't", "shouldn't", "this", "that",
    "to", "so", "and", "of", "for", "not", "should", "asking", "says", "said", "did", "nothing", "there", "just",
    "reply", "before", "whether", "changed", "will", "need", "they", "their", "them", "my", "with", "what", "she",
    "his", "her", "we", "you", "your", "about", "request", "user", "system", "tool", "call", "it", "be", "has",
    "have", "would", "which", "because", "but", "if", "or", "only", "can", "now", "here", "here's", "let", "let's",
}
SPANISH_WORDS = {
    "el", "la", "los", "las", "de", "del", "que", "y", "es", "un", "una", "por", "para", "con", "su", "sus", "lo",
    "le", "les", "se", "ya", "muy", "pero", "como", "mi", "te", "usted", "esta", "este", "eso", "al", "hoy", "si",
}


def looks_english(text: str) -> bool:
    """True for a passage that is clearly English (Pepe only speaks Spanish)."""
    words = re.findall(r"[a-záéíóúüñ']+", text.lower())
    en = sum(w in ENGLISH_WORDS for w in words)
    es = sum(fold(w) in SPANISH_WORDS for w in words)
    return en >= 3 and en > 2 * es


# --- "Pepe, ayuda": the emergency phrase, recognised by code (BUILD-PLAN 0b.2) -----------------
#
# What counts sends a message at once if they agreed at setup, so both mistakes matter: a real call
# missed, or "AYUDA" sent to the family for a picture falling off the wall. Rules:
# - a help word near the start ("Pepe, ayuda, me duele el pecho", "¡Que alguien me ayude!"), unless it's
#   a noun ("qué ayuda", "la ayuda a domicilio") or a task follows ("ayúdame con la compra", "ayuda a
#   buscar las gafas"); "¿me ayuda?" is a question about something, not a call.
# - "socorro"/"auxilio" only as a cry ("¡socorro!", "socorro, Pepe"), never "Ha venido Socorro".
# - a fall said in the first person, or a person at home falling ("Toni se ha caído", "mi marido se ha
#   caído"); never a thing ("el cuadro se ha caído"), a denial ("no me he caído") or an old story.
# Anything else urgent ("no puedo respirar") is for Claude, which can only ask before anything is sent.

HELP_WORDS = {"ayuda", "ayudame", "ayudeme", "ayude", "ayudenme", "ayudadme", "ayudarme", "help"}
CRIES = {"socorro", "auxilio"}
# Before the help word: "Pepe", greetings of panic, "que alguien me", "necesito", "soy Carmen".
BEFORE_OK = {"pepe", "por", "favor", "que", "alguien", "me", "ya", "oh", "ay", "dios", "mio", "madre", "necesito",
             "pide", "pidele", "llama", "avisa", "a", "mi", "hija", "hijo", "familia", "soy", "corre", "rapido",
             "please", "i", "need", "urgente", "socorro", "auxilio"}
NOUN_BEFORE = {"una", "la", "tu", "su", "mucha", "gran", "buena", "esta", "esa", "de", "sin", "con", "tanta",
               "poca", "otra", "tus", "sus", "mas"}
TASK_AFTER = {"con", "para", "en"}  # "ayúdame con la compra"; "a" is a task unless it's getting up
CRY_AFTER_OK = {"pepe", "socorro", "auxilio", "ayuda", "ayudame", "por", "favor", "que", "me", "he", "caido",
                "alguien", "ya", "rapido", "corre"}
FALL_FIRST = ["me he caido", "me acabo de caer", "me he ido al suelo", "estoy en el suelo", "i fell",
              "i've fallen", "i have fallen"]
CANT_GET_UP = ["no me puedo levantar", "no puedo levantarme", "can't get up", "cannot get up"]
NOT_NOW_AFTER = {"temprano", "pronto", "tan", "como", "sin", "de", "antes", "tarde", "bien", "solo", "sola"}
FALL_NEGATIONS = {"no", "casi", "nunca", "not", "never", "almost", "si", "cuando", "vez"}
SPOUSE = {"marido", "mujer", "esposo", "esposa", "pareja"}


def help_requested(text: str, names: list[str] | None = None) -> str | None:
    """'help' for the emergency phrase, 'fall' for a fall, else None. `names`: how people at home are called."""
    words = fold(text).split()
    if not words:
        return None
    if _fall(words, {w for n in (names or []) for w in fold(n).split()}):
        return "fall"
    for i, w in enumerate(words):
        if w in CRIES:
            rest = words[i + 1:]
            if (i == 0 or all(b in BEFORE_OK for b in words[:i])) and all(x in CRY_AFTER_OK for x in rest[:3]):
                return "help"
        if w in HELP_WORDS and i <= 6:
            before = words[:i]
            after = words[i + 1:]
            if before and (before[-1] in NOUN_BEFORE or (before[-1] == "que" and w == "ayuda")):
                return None  # "una ayuda", "qué ayuda": the noun
            if not all(b in BEFORE_OK or (k > 0 and before[k - 1] == "soy") for k, b in enumerate(before)):
                continue
            if w in ("ayuda", "help") and before and before[-1] == "me" and "que" not in before:
                return None  # "¿me ayuda?" / "¿me ayuda a…?": a question, not a call
            if after and after[0] in TASK_AFTER:
                return None
            if after and after[0] == "a" and not (len(after) > 1 and after[1].startswith("levant")):
                return None
            return "help"
    return None


def _fall(words: list[str], names: set[str]) -> bool:
    padded = " " + " ".join(words) + " "
    for phrases, check_after in ((FALL_FIRST, False), (CANT_GET_UP, True)):
        for p in phrases:
            i = padded.find(f" {p} ")
            if i < 0:
                continue
            before = padded[:i].split()
            after = padded[i + len(p) + 2:].split()
            if not p.startswith("no ") and before and before[-1] in FALL_NEGATIONS:
                continue
            if check_after and after and after[0] in NOT_NOW_AFTER:
                continue  # "ya no me puedo levantar temprano"
            if p == "me he caido" and after[:2] in (["alguna", "vez"], ["muchas", "veces"]):
                continue
            return True
    for p in ("se ha caido", "se acaba de caer"):
        i = padded.find(f" {p} ")
        if i < 0:
            continue
        before = padded[:i].split()
        after = padded[i + len(p) + 2:].split()
        if before and (before[-1] in names or before[-1] in SPOUSE):
            return True
        if not before and after and after[0] in names:  # "Se ha caído Toni"
            return True
    return False
