import pytest

from kore.brain.language import is_affirmative, self_identified

CLEAR_YES = [
    "Sí", "sí, mándalo", "Si.", "Claro", "vale", "Sí, claro que sí", "Eso es", "De acuerdo",
    "yes", "Yes please", "yeah, send it", "of course", "Por supuesto", "Sí, soy yo",
]
NOT_A_CLEAR_YES = [
    # found in review: repeats, corrections, questions back, putting it off
    "Por favor, repítelo", "Perdona, ¿qué? Por favor otra vez", "Ay, por favor, qué lío",
    "Eso es a las diez", "Claro, a las diez", "Sí, a las diez", "¿Está bien eso?",
    "Vale, luego lo vemos", "Está bien, mañana lo hablamos", "Qué va, eso es mentira",
    "Venga, ¿qué me decías?", "yes, at 10", "ok, later",
    "", "no", "No, a las diez", "Sí, pero a las diez", "creo que sí", "no sé", "a lo mejor",
    "yes but at ten", "I think so", "not sure", "wait", "Si quieres", "maybe", "No, no lo mandes",
    "Sí... no, espera", "hmm", "¿qué?",
]


@pytest.mark.parametrize("text", CLEAR_YES)
def test_clear_yes(text):
    assert is_affirmative(text)


@pytest.mark.parametrize("text", NOT_A_CLEAR_YES)
def test_not_a_clear_yes(text):
    assert not is_affirmative(text)


NAMES = {1: ["Carmen"], 2: ["Toni", "Antonio"]}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Soy Carmen", 1),
        ("soy la Carmen", 1),
        ("Hola Pepe, soy Toni", 2),
        ("It's Antonio", 2),
        ("Toni", 2),
        ("No, Toni", 2),
        ("¿Se ha tomado Carmen la pastilla?", None),  # mentions, doesn't introduce
        ("Dile a Carmen que venga", None),
        ("Me tomé la pastilla", None),
    ],
)
def test_self_identification(text, expected):
    assert self_identified(text, NAMES, answering=True) == expected


@pytest.mark.parametrize(
    "text",
    [
        # found in review: negations, mentions, calling someone
        "No soy Carmen", "Yo no soy Carmen", "¿Está aquí Carmen?", "Carmen aquí no está",
        "Las gafas de Carmen, aquí en la mesa", "¡Carmen!", "¿Habla Carmen con su hija hoy?",
        "Soy la hija de Carmen", "I'm not Carmen",
    ],
)
def test_not_a_self_introduction(text):
    assert self_identified(text, NAMES) is None


def test_lone_name_counts_only_as_an_answer():
    assert self_identified("Carmen", NAMES) is None
    assert self_identified("Carmen", NAMES, answering=True) == 1
