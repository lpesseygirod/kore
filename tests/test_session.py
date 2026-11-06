import pytest

from kore.brain.session import (
    ConfirmationRefused,
    Person,
    Session,
    SpeakerMismatch,
    SpeakerUnknown,
)

CARMEN = Person(1, "Carmen", "Carmen")
TONI = Person(2, "Antonio", "Toni")


def two():
    return Session([CARMEN, TONI])


def test_single_person_home_knows_the_speaker():
    s = Session([CARMEN])
    s.begin_turn("Me tomé la pastilla")
    s.require_speaker(1)  # no raise


def test_two_person_home_starts_unknown_and_asks():
    s = two()
    s.begin_turn("Ya me la tomé")
    with pytest.raises(SpeakerUnknown) as e:
        s.require_speaker(1)
    assert e.value.candidate_id == 1
    s.begin_turn("Sí")  # answering "Is that you, Carmen?"
    s.require_speaker(1)


def test_yes_to_identity_counts_only_on_the_next_turn():
    s = two()
    s.begin_turn("Ya me la tomé")
    with pytest.raises(SpeakerUnknown):
        s.require_speaker(1)
    s.begin_turn("¿Qué hora es?")
    s.begin_turn("sí")
    assert s.speaker_id is None


def test_other_person_answering_is_not_counted():
    s = two()
    s.begin_turn("Ya me la tomé")
    with pytest.raises(SpeakerUnknown):
        s.require_speaker(1)
    s.begin_turn("No, soy Toni")
    with pytest.raises(SpeakerMismatch):
        s.require_speaker(1)


def test_self_introduction_sets_speaker_and_conversation_end_forgets_it():
    s = two()
    s.begin_turn("Hola Pepe, soy Carmen")
    s.require_speaker(1)
    s.end_conversation()
    assert s.speaker_id is None


def test_confirmation_needs_a_later_clear_yes():
    s = Session([CARMEN])
    s.begin_turn("Añade la pastilla de la tensión a las 9")
    a = s.propose("add_medication", {}, "Blood pressure pill at 9:00. Is that right?")
    with pytest.raises(ConfirmationRefused, match="must_wait"):
        s.take_confirmed(a.id)  # Claude can't confirm in the same turn
    s.begin_turn("Sí, pero a las diez")
    with pytest.raises(ConfirmationRefused, match="not_a_clear_yes"):
        s.take_confirmed(a.id)
    b = s.propose("add_medication", {}, "Blood pressure pill at 10:00. Is that right?")
    s.begin_turn("Sí")
    assert s.take_confirmed(b.id).status == "done"
    with pytest.raises(ConfirmationRefused):
        s.take_confirmed(b.id)  # only once


def test_a_new_read_back_replaces_the_old_one():
    # Review finding: "No, a las diez" → new proposal → "Sí" confirmed both, saving 09:00 too.
    s = Session([CARMEN])
    s.begin_turn("La pastilla a las 9")
    old = s.propose("add_medication", {"t": "09:00"}, "?")
    s.begin_turn("No, a las diez")
    new = s.propose("add_medication", {"t": "10:00"}, "?")
    s.begin_turn("Sí")
    with pytest.raises(ConfirmationRefused, match="expired"):
        s.take_confirmed(old.id)
    assert s.take_confirmed(new.id).payload == {"t": "10:00"}


def test_yes_must_answer_the_read_back_directly():
    # Review finding: a "vale" two turns later, about something else, confirmed a stop.
    s = Session([CARMEN])
    s.begin_turn("Ya no quiero la pastilla")
    a = s.propose("stop_medication", {}, "?")
    s.begin_turn("¿Qué tiempo hace mañana?")
    s.begin_turn("Vale")
    with pytest.raises(ConfirmationRefused, match="expired"):
        s.take_confirmed(a.id)


def test_confirmation_expires():
    s = Session([CARMEN])
    s.begin_turn("x")
    a = s.propose("k", {}, "?")
    for _ in range(3):
        s.begin_turn("otra cosa")
    s.begin_turn("sí")
    with pytest.raises(ConfirmationRefused, match="expired"):
        s.take_confirmed(a.id)


def test_confirmation_by_the_other_person_is_refused():
    s = two()
    s.begin_turn("Soy Carmen. Manda un mensaje a Lucía")
    a = s.propose("family_message", {}, "?")
    s.begin_turn("Soy Toni, sí")
    with pytest.raises(ConfirmationRefused, match="different_speaker"):
        s.take_confirmed(a.id)
