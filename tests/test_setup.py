"""Phase 5: the setup conversation, code side.

'Done when': simulated setups (talkative person, short answers, changes mind
about a name, stops halfway) all end with correct data saved. Each line is what
the person says plus the tool calls a good Claude would make; code decides what
is saved. (Whether Claude makes those calls is the live half: scenarios.)
"""

import json

import pytest

from kore.brain import tools
from kore.brain.session import Session
from kore.memory import meds, settings, store
from kore.memory.db import connect
from kore.scheduler.clock import FakeClock
from kore.scheduler.engine import Engine
from kore.setup import progress
from tests.conftest import START, at


class Setup:
    def __init__(self, clock):
        self.conn = connect(":memory:")
        self.clock = clock
        self.session = Session([])
        self.ctx = tools.Context(self.conn, clock, self.session)
        self.results: list[dict] = []

    def say(self, text, *calls):
        """They say `text`; Claude makes `calls` (name, args); 'LAST' in args = the last action_id."""
        self.session.begin_turn(text)
        out = []
        for name, args in calls:
            args = {k: (self.last_action() if v == "LAST" else v) for k, v in args.items()}
            if name == "propose_person":
                args.setdefault("is_speaker", True)
            r = tools.call(self.ctx, name, args)
            self.results.append(r)
            out.append(r)
        return out[-1] if out else None

    def last_action(self):
        return next(r["action_id"] for r in reversed(self.results) if "action_id" in r)

    def next(self):
        return tools.call(self.ctx, "setup_next", {})

    def done(self, step, **kw):
        return ("setup_step_done", {"step": step, **kw})

    def person(self, name):
        return self.conn.execute("SELECT * FROM people WHERE name = ?", (name,)).fetchone()


@pytest.fixture
def s(clock):
    return Setup(clock)


def yes_to(kind):
    return ("propose_permission", {"kind": kind})


CONFIRM = ("confirm_action", {"action_id": "LAST"})


def welcome_and_name(s, name="Carmen", call=None, text=None):
    s.say("Hola", s.done("welcome"))
    s.say(text or f"Me llamo {name}", ("propose_person", {"name": name, **({"call_name": call} if call else {})}))
    s.say("Sí", CONFIRM)


# --- the four simulated setups ---------------------------------------------------------


def test_talkative_person_everything_saved(s):
    assert s.next()["step"] == "welcome"
    s.say("Hola, Pepe. Sí, vamos a conocernos, que tengo toda la mañana.", s.done("welcome"))
    s.say("Pues me llamo Carmen Ruiz, pero todo el mundo me llama Carmen, y vivo sola desde que murió mi marido.",
          ("propose_person", {"name": "Carmen Ruiz", "call_name": "Carmen"}))
    s.say("Sí, eso es.", CONFIRM, s.done("people"))
    assert s.next()["step"] == "address"
    s.say("De tú, hijo, de tú, que me haces mayor.", ("setup_setting", {"setting": "address", "value": "tú"}),
          s.done("address"))
    s.say("Un poquito más despacio, que hablas rápido.", ("setup_setting", {"setting": "speech_rate", "value": "slower"}),
          s.done("hearing"))
    s.say("Pues un poco de todo: que me recuerdes cosas y que me hagas compañía.",
          ("remember_about_person", {"kind": "preference", "text": "Quiere recordatorios y compañía"}), s.done("wants"))
    s.say("Sí, sí, háblame cuando quieras.", yes_to("conversation_starters"))
    s.say("Sí.", CONFIRM, s.done("chattiness"))
    s.say("Lo del agua sí, que se me olvida beber.", yes_to("water_nudges"))
    s.say("Sí", CONFIRM, ("record_permission_no", {"kind": "movement_nudges"}), s.done("nudges"))
    s.say("Me acuesto a las once y me levanto a las ocho. La siesta de cuatro a cinco.",
          ("setup_setting", {"setting": "night_start", "value": "23:00"}),
          ("setup_setting", {"setting": "rest_hours", "value": "16:00-17:00"}), s.done("quiet_times"))
    s.say("Claro, para eso estás.", yes_to("health_info"))
    s.say("Sí", CONFIRM, s.done("health_info"))
    s.say("La de la tensión a las nueve y la del colesterol por la noche, a las nueve también.",
          ("propose_medication", {"name": "tensión", "times": ["09:00"], "description": "la pastilla de la tensión"}))
    s.say("Sí, eso.", CONFIRM, ("propose_medication", {"name": "colesterol", "times": ["21:00"]}))
    s.say("Sí", CONFIRM, s.done("medicines"))
    s.say("El cumpleaños de mi nieta Lucía es el catorce de marzo.",
          ("propose_contact", {"name": "Lucía", "relation": "nieta", "birthday": "03-14"}))
    s.say("Sí", CONFIRM, s.done("dates"))
    s.say("Mi hija Elena, ella vive cerca.", ("propose_contact", {"name": "Elena", "relation": "hija", "help": True}))
    s.say("Sí", CONFIRM, s.done("help_contact"))
    for kind, step in (("listening", "listening"), ("presence_sensor", "presence"),
                       ("family_missed_meds", "family_alerts"), ("emergency_path", "emergency")):
        s.say("Sí, claro.", yes_to(kind))
        s.say("Sí", CONFIRM, s.done(step))
    s.say("Pepe, ¿qué hora es?", s.done("test"))
    s.say("Gracias a ti.", s.done("finish"))

    assert progress.status(s.conn) == "complete" and s.next().get("complete")
    c = s.person("Carmen Ruiz")
    assert c["call_name"] == "Carmen"
    assert settings.get(s.conn, "address", c["id"]) == "tu"
    assert settings.get(s.conn, "speech_rate", c["id"]) == "0.9"
    assert (settings.get(s.conn, "night_start"), settings.get(s.conn, "rest_hours")) == ("23:00", "16:00-17:00")
    assert sorted(m["name"] for m in meds.medications(s.conn)) == ["colesterol", "tensión"]
    granted = {k for k in store.CONSENTS if store.has_consent(s.conn, c["id"], k)}
    assert granted == {"conversation_starters", "water_nudges", "health_info", "listening", "presence_sensor",
                       "family_missed_meds", "emergency_path"}
    assert store.declined(s.conn, c["id"], "movement_nudges")
    contacts = {r["name"]: r for r in s.conn.execute("SELECT * FROM contacts")}
    assert contacts["Lucía"]["birthday_md"] == "03-14" and contacts["Elena"]["help_contact"] == 1


def test_short_answers_and_noes(s):
    s.say("Vale.", s.done("welcome"))
    s.say("Antonio.", ("propose_person", {"name": "Antonio"}))
    s.say("Sí.", CONFIRM, s.done("people"))
    s.say("Usted.", ("setup_setting", {"setting": "address", "value": "usted"}), s.done("address"))
    s.say("Bien.", s.done("hearing"))
    s.say("Lo que sea.", s.done("wants", skipped=True))
    s.say("No.", ("record_permission_no", {"kind": "conversation_starters"}),
          ("setup_setting", {"setting": "chattiness", "value": "none"}), s.done("chattiness"))
    s.say("No.", ("record_permission_no", {"kind": "water_nudges"}), ("record_permission_no", {"kind": "movement_nudges"}),
          s.done("nudges"))
    s.say("Las doce y las nueve.", ("setup_setting", {"setting": "night_start", "value": "00:00"}),
          ("setup_setting", {"setting": "night_end", "value": "09:00"}), s.done("quiet_times"))
    s.say("No.", ("record_permission_no", {"kind": "health_info"}), s.done("health_info"))
    r = s.say("Una para el azúcar.", ("propose_medication", {"name": "metformina", "times": ["09:00"]}))
    assert r["error"] == "bad_input"  # he said no to keeping his medicines: nothing saved
    s.say("Nada más.", s.done("medicines", skipped=True))
    s.say("No.", s.done("dates", skipped=True))
    s.say("No.", s.done("help_contact", skipped=True))
    for kind, step in (("listening", "listening"), ("presence_sensor", "presence"),
                       ("family_missed_meds", "family_alerts"), ("emergency_path", "emergency")):
        s.say("No.", ("record_permission_no", {"kind": kind}), s.done(step))
    s.say("Vale.", s.done("test", skipped=True))
    s.say("Vale.", s.done("finish"))
    a = s.person("Antonio")
    assert progress.status(s.conn) == "complete"
    assert meds.medications(s.conn) == []
    assert not any(store.has_consent(s.conn, a["id"], k) for k in store.CONSENTS)
    assert settings.get(s.conn, "chattiness", a["id"]) == "none"
    assert settings.get(s.conn, "night_start") == "00:00"


def test_changes_mind_about_the_name(s):
    s.say("Hola", s.done("welcome"))
    # Thinking out loud: the final answer is "Mari", not "Carmen".
    r = s.say("Ay, pues... llámame Carmen. Bueno no, mejor Mari, como me llamaba mi madre.",
              ("propose_person", {"name": "Carmen", "call_name": "Mari"}))
    assert "Mari" in r["say_and_wait"]
    s.say("Sí, Mari.", CONFIRM, s.done("people"))
    s.say("De tú.", ("setup_setting", {"setting": "address", "value": "tu"}), s.done("address"))
    # Later, again: "actually, call me Carmela".
    s.say("Oye, ¿sabes qué? Mejor llámame Carmela.", ("propose_call_name", {"call_name": "Carmela"}))
    s.say("Sí.", CONFIRM)
    p = s.person("Carmen")
    assert p["call_name"] == "Carmela" and s.session.person(p["id"]).call_name == "Carmela"
    assert s.next()["step"] == "hearing"  # carries on where it was


def test_a_hedged_yes_saves_nothing(s):
    welcome_and_name(s)
    s.say("Hmm", yes_to("family_missed_meds"))
    r = s.say("Bueno, no sé, ya veremos.", CONFIRM)
    assert r["error"] == "not_confirmed"
    assert not store.has_consent(s.conn, s.person("Carmen")["id"], "family_missed_meds")


def test_stops_halfway_and_picks_up_the_next_day(clock):
    path_clock = FakeClock(START)
    s = Setup(path_clock)
    welcome_and_name(s)
    s.say("Sí", s.done("people"))
    for step in ("address", "hearing", "wants", "chattiness", "nudges", "quiet_times", "health_info"):
        s.say("Vale", s.done(step))
    s.say("Uy, lo seguimos mañana, que viene mi vecina.", ("setup_pause", {}))
    assert progress.status(s.conn) == "paused"
    # Same day: no new offer. Next morning, when she's up: offered again, once.
    e = Engine(s.conn, path_clock)
    assert [ev.kind for ev in e.on_presence("moving") if ev.kind == "setup_offer"] == []
    path_clock.set(at(9, 0, day=10))
    e.on_presence("absent")
    assert "setup_offer" in [ev.kind for ev in e.on_presence("moving")]
    e.on_presence("absent")
    assert "setup_offer" not in [ev.kind for ev in e.on_presence("moving")]
    # A new conversation the next day: picks up exactly where it stopped.
    s.session.end_conversation()
    assert s.next()["step"] == "medicines"


# --- two people, and the welcome -------------------------------------------------------


def test_two_people_each_do_their_own_part(s):
    welcome_and_name(s)
    s.say("Y vive conmigo mi marido, Antonio, pero le llamamos Toni.",
          ("propose_person", {"name": "Antonio", "call_name": "Toni", "is_speaker": False}))
    s.say("Sí", CONFIRM, s.done("people"))
    assert s.session.speaker_id == s.person("Carmen")["id"]  # still Carmen talking
    for step in ("address", "hearing", "wants", "chattiness", "nudges", "quiet_times", "health_info", "medicines",
                 "dates", "help_contact", "listening", "presence", "family_alerts", "emergency", "share"):
        assert s.next()["step"] == step
        s.say("Vale", s.done(step))
    r = s.next()
    assert r["done_for_now"] and "Toni" in r["say"]  # the closing steps wait for Toni's part
    s.session.end_conversation()
    assert s.next()["error"] == "speaker_unknown"  # two at home: who is it?
    s.say("Soy Toni")
    assert s.next()["step"] == "address" and s.next()["for"] == "Toni"
    r = s.say("Que Carmen no tome nada de azúcar", ("setup_setting", {"setting": "address", "value": "tu"}))
    assert r["ok"] and settings.stored(s.conn, "address", s.person("Carmen")["id"]) is None  # his own only


def test_three_people_is_beyond_v1(s):
    welcome_and_name(s)
    s.say("Y Antonio", ("propose_person", {"name": "Antonio", "is_speaker": False}))
    s.say("Sí", CONFIRM)
    assert s.say("Y mi nieto", ("propose_person", {"name": "Pablo", "is_speaker": False}))["error"] == "bad_input"


def test_welcome_on_the_first_presence_not_at_night(clock):
    conn = connect(":memory:")
    e = Engine(conn, clock)
    clock.set(at(23, 30))
    assert [ev.kind for ev in e.on_presence("moving")] == []  # night: don't spend today's offer
    clock.set(at(17, 0, day=10))
    e.on_presence("absent")
    assert [ev.kind for ev in e.on_presence("moving")] == ["setup_offer"]


def test_homes_set_up_another_way_are_never_offered_setup(conn, clock, home):
    assert progress.status(conn) == "complete"
    assert "setup_offer" not in [ev.kind for ev in Engine(conn, clock).on_presence("moving")]


def test_night_that_would_cover_a_medicine_is_refused_at_setup(s):
    welcome_and_name(s)
    s.say("Sí", yes_to("health_info"))
    s.say("Sí", CONFIRM)
    s.say("A las nueve", ("propose_medication", {"name": "tensión", "times": ["09:00"]}))
    s.say("Sí", CONFIRM)
    r = s.say("Hasta las diez no me hables", ("setup_setting", {"setting": "night_end", "value": "10:00"}))
    assert r["error"] == "bad_input" and settings.get(s.conn, "night_end") == "08:00"


def test_setup_state_reaches_claude():
    from kore.brain.clients import ScriptedClient, text
    from kore.brain.conversation import Brain

    s = Setup(FakeClock(START))
    welcome_and_name(s)
    state = Brain(s.ctx, ScriptedClient(lambda r: text("Hola"))).state()
    assert "Setup isn't finished" in state and "Address Carmen as usted." in state
    s.say("De tú", ("setup_setting", {"setting": "address", "value": "tu"}))
    assert "Address Carmen as tú." in Brain(s.ctx, ScriptedClient(lambda r: text("Hola"))).state()
    assert json.dumps(tools.schemas())  # all tool schemas still serialisable


# --- regressions from the independent review of phase 5 (2026-11-06) ----------------------


def test_name_before_the_welcome_keeps_setup_open(s):
    s.say("Hola Pepe, soy Carmen", ("propose_person", {"name": "Carmen", "is_speaker": True}))
    s.say("Sí", CONFIRM)
    assert progress.status(s.conn) == "in_progress"
    assert s.next()["step"] == "welcome"


def test_the_person_speaking_is_saved_first(s):
    r = s.say("Aquí vive mi marido Antonio, y yo", ("propose_person", {"name": "Antonio", "is_speaker": False}))
    assert r["error"] == "bad_input"
    r = tools.call(s.ctx, "propose_person", {"name": "Carmen"})  # Claude left out is_speaker
    assert r["error"] == "bad_input"


def test_someone_told_about_never_becomes_the_speaker(s):
    welcome_and_name(s)
    s.say("Y mi marido Antonio", ("propose_person", {"name": "Antonio", "is_speaker": False}))
    s.say("Sí", CONFIRM)
    carmen = s.person("Carmen")["id"]
    assert s.session.speaker_id == carmen
    s.session.end_conversation()
    assert s.session.speaker_id is None  # two at home now: unknown until they say


def test_steps_only_in_order_and_finish_waits_for_both(s):
    welcome_and_name(s)
    s.say("Y Antonio", ("propose_person", {"name": "Antonio", "is_speaker": False}))
    s.say("Sí", CONFIRM)
    assert s.say("Ya está", s.done("finish"))["error"] == "bad_input"
    assert s.say("Probemos", s.done("test"))["error"] == "bad_input"
    s.say("Sí", s.done("people"))
    assert s.say("Vale", s.done("medicines"))["error"] == "bad_input"  # not the next one
    assert progress.status(s.conn) == "in_progress"


def test_pause_never_reopens_a_finished_or_family_set_up_home(conn, clock, home):
    ctx = tools.Context(conn, clock, Session([]))
    tools.call(ctx, "setup_pause", {})
    assert progress.status(conn) == "complete"
    assert not progress.offer_due(conn, clock)


def test_after_not_now_no_setup_hint_that_day(s):
    from kore.brain.clients import ScriptedClient, text
    from kore.brain.conversation import Brain

    welcome_and_name(s)
    s.say("Lo seguimos otro día", ("setup_pause", {}))
    assert "Setup isn't finished" not in Brain(s.ctx, ScriptedClient(lambda r: text("Hola"))).state()


def test_answers_close_their_own_steps(s):
    welcome_and_name(s)
    s.say("Sí", s.done("people"))
    s.say("De tú", ("setup_setting", {"setting": "address", "value": "tu"}))
    assert s.next()["step"] == "hearing"
    for step in ("hearing", "wants"):
        s.say("Vale", s.done(step))
    s.say("Sí", yes_to("conversation_starters"))
    s.say("Sí", CONFIRM)
    assert s.next()["step"] == "nudges"


def test_a_second_person_added_later_gets_their_own_setup(conn, clock):
    s = Setup(clock)
    s.conn, s.ctx = conn, tools.Context(conn, clock, s.session)
    welcome_and_name(s)
    progress.set_status(conn, clock, "complete")
    s.say("Ahora vive conmigo mi marido Antonio", ("propose_person", {"name": "Antonio", "is_speaker": False}))
    s.say("Sí", CONFIRM)
    assert progress.status(conn) == "in_progress"


def test_no_to_the_sensor_means_no_sensor(s, clock):
    welcome_and_name(s)
    s.say("No, eso no", ("record_permission_no", {"kind": "presence_sensor"}))
    e = Engine(s.conn, clock)
    clock.set(at(9, 0, day=10))
    assert e.on_presence("moving") == []
    assert store.listening_declined(s.conn) is False


@pytest.mark.parametrize("first,second", [("María", "María José"), ("María José", "José")])
def test_names_must_be_told_apart(s, first, second):
    welcome_and_name(s, name=first)
    r = s.say("Y otro", ("propose_person", {"name": second, "is_speaker": False}))
    assert r["error"] == "bad_input"


def test_pepe_is_not_a_name_for_a_person(s):
    s.say("Hola", s.done("welcome"))
    assert s.say("Me llamo José, pero llámame Pepe",
                 ("propose_person", {"name": "José", "call_name": "Pepe"}))["error"] == "bad_input"


def test_health_no_also_covers_medicines_as_voice_reminders(s):
    welcome_and_name(s)
    s.say("No", ("record_permission_no", {"kind": "health_info"}))
    r = s.say("Recuérdame la pastilla del azúcar a las diez",
              ("propose_reminder", {"text": "tomar la pastilla del azúcar", "time": "10:00", "rule": "daily"}))
    assert r["error"] == "bad_input"
    r = s.say("Recuérdame regar las plantas", ("propose_reminder", {"text": "regar las plantas", "time": "10:00",
                                                                     "rule": "daily"}))
    assert r["ok"]


def test_setup_next_says_complete_in_a_home_set_up_another_way(conn, clock, home):
    ctx = tools.Context(conn, clock, Session([]))
    assert tools.call(ctx, "setup_next", {})["complete"] is True


def test_a_permission_already_on_record_is_not_asked_again(s):
    """Live run 2026-11-07: health_info was already given, yet setup asked for it three times."""
    welcome_and_name(s)
    s.say("Vale", s.done("people"))
    for step in ("address", "hearing", "wants", "chattiness", "nudges", "quiet_times"):
        s.say("Vale", s.done(step))
    carmen = s.person("Carmen")["id"]
    store.set_consent(s.conn, s.clock, carmen, "health_info", True)   # given earlier, outside setup
    store.set_consent(s.conn, s.clock, carmen, "listening", False)    # a no counts as an answer too
    assert s.next()["step"] == "medicines"
    for step in ("medicines", "dates", "help_contact"):
        s.say("Vale", s.done(step))
    assert s.next()["step"] == "presence"
