"""Phase 3 'done when': A day with Pepe (PEPE.md 5) runs in under a minute and
matches the table. Since phase 4 the events go through the manners gate, so the
quiet (16:00), visitors (17:00) and visit follow-up (19:30) rows are covered too."""

import time

import pytest

from kore.brain.fallback import phrase, spoken_time
from sim.day import run_day


@pytest.fixture(scope="module")
def day():
    t0 = time.perf_counter()
    transcript, state = run_day()
    return transcript, state, time.perf_counter() - t0


def pepe_at(transcript, hhmm):
    return [l.text for l in transcript if l.time == hhmm and l.who == "Pepe"]


def test_runs_in_under_a_minute(day):
    assert day[2] < 60


@pytest.mark.parametrize(
    "hhmm,expected",
    [
        ("08:30", "Buenos días"),                    # sensor notices they're up
        ("09:00", "son las nueve"),                  # morning pills
        ("10:30", "leche, pan y huevos"),            # list
        ("11:00", "agua"),                           # water
        ("12:15", "Recuerda: leche, pan y huevos"),  # going shopping
        ("13:00", "huevos en la lista"),             # back, no eggs
        ("15:30", "paseo"),                          # sitting two hours
        ("16:00", "Claro."),                         # be quiet
        ("19:30", "visita"),                         # quiet again: how was the visit?
        ("20:00", "Mañana a las once: el médico"),   # doctor tomorrow
        ("21:30", "nueve y media"),                  # evening pill
    ],
)
def test_matches_the_table(day, hhmm, expected):
    assert any(expected in t for t in pepe_at(day[0], hhmm)), pepe_at(day[0], hhmm)


def test_yes_from_unknown_speaker_was_checked_first(day):
    assert pepe_at(day[0], "09:02") == ["¿Eres Carmen?"]


def test_all_doses_taken_once_and_list_kept_eggs(day):
    _, state, _ = day
    assert sorted(state["doses"].values()) == ["taken"] * 3
    assert state["list"] == ["huevos"]


def test_quiet_then_discreet_with_the_visitor(day):
    # After "be quiet": only the short appointment reminder, then nothing while Rosa is there.
    said = [(l.time, l.text) for l in day[0] if l.who == "Pepe" and "16:00" <= l.time < "19:30"]
    assert said == [("16:00", "Claro."), ("16:00", "En una hora: visita de tu hermana Rosa.")]


def test_nothing_unprompted_at_night(day):
    late = [l for l in day[0] if l.who == "Pepe" and l.time >= "21:45"]
    assert late == []


@pytest.mark.parametrize(
    "hhmm,words",
    [("09:00", "las nueve"), ("13:30", "la una y media"), ("21:15", "las nueve y cuarto"),
     ("00:45", "la una menos cuarto"), ("12:45", "la una menos cuarto"), ("10:05", "las diez y 5")],
)
def test_spoken_time(hhmm, words):
    assert spoken_time(hhmm) == words


def test_family_alert_is_never_spoken():
    from kore.scheduler.engine import Event

    assert phrase(Event("family_alert_missed_meds", 1, [1], {"person": "Carmen"})) is None
