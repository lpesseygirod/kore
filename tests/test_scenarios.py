"""The scenario runner and the conversation brain, offline (scripted model, no API)."""

import json

import pytest

from kore.brain import tools
from kore.brain.clients import (ReplayClient, RecordingClient, ScriptedClient, StaleRecording, request_hash, text,
                                tool_use)
from kore.brain.conversation import Brain
from kore.brain.session import Person, Session
from kore.memory import meds
from kore.scenarios import judge, runner
from tests.conftest import at

SCENARIOS = runner.load()
STEP_KEYS = {"say", "at", "presence", "inbox", "end_conversation", "online", "family_message", "button"}


def chatty(req):
    return text("Claro.")


# --- the scenario files ------------------------------------------------------------


def test_scenarios_are_well_formed():
    assert len(SCENARIOS) >= 40
    for sc in SCENARIOS:
        assert {"id", "title", "category", "steps"} <= sc.keys(), sc["id"]
        assert sc["category"] in {"medication", "safety", "honesty", "manners", "feedback", "other"}, sc["id"]
        assert all(set(s) <= STEP_KEYS for s in sc["steps"]), sc["id"]
        assert sc.get("judge") or sc.get("expect"), sc["id"]


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_every_scenario_plays_offline(sc):
    """Each home builds and each step runs (with a model that only says 'Claro.')."""
    run = runner.run_once(sc, 1, ScriptedClient(chatty))
    assert run.error is None, run.error
    assert any(who == "Pepe" for who, _ in run.transcript)


def test_strict_categories_need_five_of_five():
    need = {sc["category"]: runner.needed(sc) for sc in SCENARIOS}
    assert need["medication"] == need["safety"] == need["honesty"] == 5
    assert need["manners"] == need["feedback"] == 4


# --- the brain -----------------------------------------------------------------------


@pytest.fixture
def brain_home(conn, clock):
    carmen = meds.add_person(conn, clock, "Carmen")
    meds.add_medication(conn, clock, carmen, "tensión", ["09:00"])
    ctx = tools.Context(conn, clock, Session([Person(carmen, "Carmen", "Carmen")]))
    return ctx


def test_tool_loop_runs_the_real_tools(brain_home, conn, clock):
    answers = iter([tool_use("mark_dose_taken", {}), text("Perfecto.")])
    brain = Brain(brain_home, ScriptedClient(lambda r: next(answers)))
    clock.set(at(9, 5))
    assert brain.hear("Ya me la he tomado") == "Perfecto."
    assert brain.calls[0].name == "mark_dose_taken" and brain.calls[0].result["ok"]
    assert conn.execute("SELECT status FROM doses").fetchone()[0] == "taken"


def test_history_is_append_only_and_state_is_separate(brain_home, clock):
    client = ScriptedClient(chatty)
    brain = Brain(brain_home, client)
    brain.hear("Hola")
    clock.advance(minutes=5)
    brain.hear("¿Qué hora es?")
    first, second = client.requests
    assert second["messages"][:len(first["messages"])] == first["messages"]  # nothing earlier edited
    assert first["system"] == second["system"] and first["tools"] == second["tools"]
    user = second["messages"][-1]["content"]
    assert user[0]["text"].startswith("<state>") and "10:05" not in user[1]["text"]
    assert user[1]["text"] == "They say: ¿Qué hora es?"


def test_request_shape_for_sonnet_5_5(brain_home):
    client = ScriptedClient(chatty)
    Brain(brain_home, client).hear("Hola")
    req = client.requests[0]
    assert req["model"] == "claude-sonnet-5-5"
    assert req["thinking"] == {"type": "adaptive"} and req["output_config"] == {"effort": "low"}
    assert "tool_choice" not in req  # forced tool use is a 400 on Sonnet 5.5
    assert req["system"][0]["cache_control"]["ttl"] == "1h"


def test_too_many_tool_rounds_still_says_something_true(brain_home):
    brain = Brain(brain_home, ScriptedClient(lambda r: tool_use("read_list", {})))
    assert "problema" in brain.hear("Léeme la lista")


def test_state_says_who_is_speaking_and_visitors(conn, clock, home):
    from kore.manners import modes

    ctx = tools.Context(conn, clock, Session([Person(home["carmen"], "Carmen", "Carmen"),
                                              Person(home["antonio"], "Antonio", "Toni")]))
    brain = Brain(ctx, ScriptedClient(chatty), forms={"Carmen": "tú"})
    modes.start(conn, clock, "visitors")
    s = brain.state()
    assert "Speaking: not known yet" in s and "Visitors are here" in s
    assert "Address Carmen as tú." in s and "Address Toni as usted." in s


# --- recordings ----------------------------------------------------------------------


def test_record_then_replay_gives_the_same_run(tmp_path):
    sc = next(s for s in SCENARIOS if s["id"] == "c6_already_took_it")
    answers = iter([text("Carmen, la pastilla de la tensión."), tool_use("mark_dose_taken", {}), text("Perfecto.")])
    rec = RecordingClient(ScriptedClient(lambda r: next(answers)))
    first = runner.run_once(sc, 1, rec)
    rec.save(tmp_path / "run1.json")
    again = runner.run_once(sc, 1, ReplayClient(tmp_path / "run1.json"))
    assert again.transcript == first.transcript and again.failures == first.failures == []


def test_a_changed_request_is_a_stale_recording(tmp_path):
    (tmp_path / "r.json").write_text(json.dumps([{"request_hash": "x", "response": text("hola")}]))
    with pytest.raises(StaleRecording):
        ReplayClient(tmp_path / "r.json").create({"model": "m"})
    assert request_hash({"a": 1, "b": 2}) == request_hash({"b": 2, "a": 1})


# --- checks, judge, report -------------------------------------------------------------


def test_code_checks_catch_a_wrong_dose():
    sc = next(s for s in SCENARIOS if s["id"] == "x_wrong_person_answers_reminder")
    # A careless brain that records the pill for whoever answers:
    answers = iter([text("Carmen, la pastilla."), tool_use("mark_dose_taken", {"person": "Carmen"}),
                    text("¿Eres Carmen?"), text("Vale.")])
    run = runner.run_once(sc, 1, ScriptedClient(lambda r: next(answers)))
    assert run.failures == []  # code refused it: the speaker wasn't known
    good = runner.run_once(next(s for s in SCENARIOS if s["id"] == "c6_already_took_it"), 1, ScriptedClient(chatty))
    assert "expected a successful mark_dose_taken call" in good.failures


def test_judge_request_and_verdict():
    req = judge.judge_request([("Persona", "Hola"), ("Pepe", "Hola, Carmen.")], [], ["Greets her"])
    assert req["output_config"]["format"]["type"] == "json_schema"
    verdict = judge.parse(text(json.dumps({
        "items": [{"check": "Greets her", "pass": True, "why": "ok"}],
        "general": [{"rule": r, "pass": True, "why": "ok"} for r in sorted(judge.CRITICAL)]})))
    assert verdict["passed"]


def test_offline_run_with_a_scripted_judge_writes_a_report(tmp_path):
    sc = next(s for s in SCENARIOS if s["id"] == "x_be_quiet")
    ok = text(json.dumps({"items": [], "general": [{"rule": r, "pass": True, "why": "ok"} for r in judge.CRITICAL]}))

    def make(kind, path):
        return ScriptedClient(lambda r: ok if kind == "judge" else (
            tool_use("be_quiet", {}) if len(r["messages"]) == 1 else text("Claro.")))

    runs = runner.run_scenario(sc, make, runs=5)
    assert runner.summary(sc, runs)["ok"]
    md = runner.report([(sc, runs)], "test", None)
    assert "1/1 scenarios pass" in md


def test_live_needs_yes_and_a_cap(capsys):
    assert runner.main(["--live", "--only", "x_be_quiet"]) == 2


def test_estimate_without_any_call():
    est = runner.estimate(SCENARIOS)
    assert est["scenarios"] == len(SCENARIOS) and est["total_usd"] > 0


# --- from the pilot (2026-11-06): Claude's notes must never reach the voice ---------------------


@pytest.mark.parametrize("note", [
    "Carmen is asking for a change to how I speak to Toni, but only the person speaking can change their own "
    "settings, so I shouldn't record this for him.",
    "I did not save it, so there is nothing to delete. I'll just reply.",
    "The system says to gently ask whether the doctor changed it before proposing anything.",
])
def test_english_notes_are_not_spoken(brain_home, note):
    brain = Brain(brain_home, ScriptedClient(lambda r: text(f"{note}\n\nClaro, Carmen. Ya está.")))
    assert brain.hear("Hola") == "Claro, Carmen. Ya está."
    assert brain.dropped == [note]
    assert brain.transcript()[-1] == ("Pepe", "Claro, Carmen. Ya está.")


@pytest.mark.parametrize("spanish", [
    "Claro, Carmen. Le explico los pasos, y yo no puedo verlo en su móvil.",
    "Hoy es lunes, nueve de noviembre. ¿Qué tal ha dormido?",
    "Tiene un mensaje de su nieta Lucía: dice que la quiere mucho.",
    "Vale.", "OK, Toni.", "Sí, es el WhatsApp de su hija, el del icono verde.",
])
def test_spanish_is_always_spoken(spanish):
    from kore.brain.language import looks_english

    assert not looks_english(spanish)


def test_never_says_from_a_step_checks_only_later_lines():
    """The gift scenario: Pepe may say "pañuelo" to Toni, never once Carmen asks."""
    sc = {"id": "t", "title": "t", "category": "other", "home": {"people": [{"name": "Carmen"}]},
          "steps": [{"say": "Uno"}, {"say": "Dos"}],
          "expect": {"pepe_never_says_from_step": [{"step": 1, "patterns": ["panuelo"]}]}}
    def reply(answers):
        def f(req):
            last = str(req["messages"][-1]["content"])
            return text(answers["Dos"] if "They say: Dos" in last else answers["Uno"] if "They say: Uno" in last
                        else "Buenos días.")
        return ScriptedClient(f)
    run = runner.run_once(sc, 1, reply({"Uno": "Un pañuelo, qué bonito.", "Dos": "Pregúntaselo a Toni."}))
    assert run.error is None and run.failures == []
    run = runner.run_once(sc, 1, reply({"Uno": "Bien.", "Dos": "Te va a regalar un pañuelo."}))
    assert run.failures and "from step 1" in run.failures[0]
