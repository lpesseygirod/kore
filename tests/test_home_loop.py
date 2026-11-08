"""The home loop (kore/home/loop.py) with a fake clock, a fake voice and a scripted Claude: nothing leaves the
computer and nothing is spent."""

import json

import pytest

from kore.brain.clients import ScriptedClient, text
from kore.family import messages as family_msgs
from kore.family.channels import FakeChannel
from kore.home.loop import Home
from kore.home.voice import FakeVoice
from kore.memory import meds, store
from kore.web import chat
from kore.web.console import LOOP_SEEN
from tests.conftest import at


class Claude:
    """Scripted Claude: says `line`, or fails like a dropped connection."""

    def __init__(self, line="Carmen, es la hora de la pastilla de la tensión."):
        self.line = line
        self.down = False
        self.calls = 0
        self.client = ScriptedClient(self._answer)

    def _answer(self, req):
        self.calls += 1
        if self.down:
            raise ConnectionError("no internet")
        return text(self.line)

    def create(self, req):
        return self.client.create(req)


@pytest.fixture
def setup(tmp_path, clock):
    db = str(tmp_path / "kore.db")
    from kore.memory.db import connect

    c = connect(db)
    carmen = meds.add_person(c, clock, "Carmen")
    toni = meds.add_person(c, clock, "Antonio", "Toni")
    c.close()
    return db, carmen, toni


@pytest.fixture
def h(setup, clock, tmp_path):
    db, carmen, toni = setup
    claude, voice, tg = Claude(), FakeVoice(), FakeChannel()
    home = Home(db, clock, claude, voice, {"telegram": tg}, backups=False, turn_log=tmp_path / "turns.jsonl")
    home.carmen, home.toni, home.claude, home.v, home.tg = carmen, toni, claude, voice, tg
    return home


def pill(h, times=("09:00",)):
    return meds.add_medication(h.conn, h.clock, h.carmen, "tensión", list(times), "la pastilla de la tensión")


# --- reminders ------------------------------------------------------------------------------------


def test_a_reminder_is_worded_by_claude_and_said(h):
    pill(h)
    h.clock.set(at(9, 0))
    h.tick()
    assert h.v.said == ["Carmen, es la hora de la pastilla de la tensión."]
    assert h.conn.execute("SELECT status FROM outbox WHERE kind = 'medication_reminder'").fetchone()[0] == "done"


def test_without_claude_the_offline_phrase_is_said(h):
    pill(h)
    h.claude.down = True
    h.clock.set(at(9, 0))
    h.tick()
    assert len(h.v.said) == 1 and "pastilla de la tensión" in h.v.said[0]


def test_a_broken_speaker_keeps_the_reminder_for_later(h):
    pill(h)
    h.v.broken = True
    h.clock.set(at(9, 0))
    h.tick()
    assert h.conn.execute("SELECT status FROM outbox WHERE kind = 'medication_reminder'").fetchone()[0] == "ready"
    h.v.broken = False
    h.clock.advance(seconds=10)
    h.tick()
    assert h.v.said and h.conn.execute("SELECT status FROM outbox WHERE kind = 'medication_reminder'"
                                       ).fetchone()[0] == "done"


def test_pepe_doesnt_interrupt_a_conversation(h):
    pill(h)
    h.clock.set(at(8, 59, ))
    h.clock.advance(seconds=50)
    h.claude.line = "Claro, Carmen."
    h.heard("Pepe, ¿qué día es hoy?")
    h.clock.set(at(9, 0))
    h.tick()
    assert h.v.said == ["Claro, Carmen."]  # not yet
    h.claude.line = "Carmen, es la hora de la pastilla de la tensión."
    h.clock.advance(seconds=40)
    h.tick()
    assert h.v.said[-1] == "Carmen, es la hora de la pastilla de la tensión."


# --- help ----------------------------------------------------------------------------------------------


def test_help_is_said_by_code_before_claude_answers(h):
    ana = store.add_contact(h.conn, h.clock, "Ana", "hija", help_contact=True, for_person=h.carmen)
    h.conn.execute("UPDATE contacts SET telegram_chat_id = '555' WHERE id = ?", (ana,))
    store.set_consent(h.conn, h.clock, h.carmen, "emergency_path", True)
    h.claude.line = "Estoy aquí con usted. ¿Se ha hecho daño?"
    h.heard("Soy Carmen. Pepe, ayuda")
    assert h.v.said[0].startswith("Ya he avisado a Ana") and h.v.said[1] == "Estoy aquí con usted. ¿Se ha hecho daño?"
    assert h.tg.sent


def test_help_still_goes_with_claude_down(h):
    ana = store.add_contact(h.conn, h.clock, "Ana", "hija", help_contact=True)
    h.conn.execute("UPDATE contacts SET telegram_chat_id = '555' WHERE id = ?", (ana,))
    store.set_consent(h.conn, h.clock, h.carmen, "emergency_path", True)
    store.set_consent(h.conn, h.clock, h.toni, "emergency_path", True)
    h.claude.down = True
    h.help_button()
    assert h.tg.sent and h.v.said[0].startswith("Ya he avisado a Ana")


def test_a_family_alert_is_a_message_never_spoken(h):
    ana = store.add_contact(h.conn, h.clock, "Ana", "hija", help_contact=True, for_person=h.carmen)
    h.conn.execute("UPDATE contacts SET telegram_chat_id = '555' WHERE id = ?", (ana,))
    store.set_consent(h.conn, h.clock, h.carmen, "family_missed_meds", True)
    from kore.scheduler.engine import Event
    h.manners.submit([Event("family_alert_missed_meds", 1, [h.carmen], {"missed_in_a_row": 2})])
    h.tick()
    assert h.tg.sent and "no ha contestado" in h.tg.sent[0][1] and h.v.said == []


# --- the conversation -------------------------------------------------------------------------------------


def test_turns_are_kept_only_with_their_yes_and_olvidalo_removes_them(h):
    h.claude.line = "Muy bien, Carmen."
    h.heard("Soy Carmen, ¿qué tal?")
    assert h.conn.execute("SELECT count(*) FROM chat").fetchone()[0] == 0  # no yes yet
    store.set_consent(h.conn, h.clock, h.carmen, "keep_conversations", True)
    h.heard("Hoy he ido al mercado")
    assert h.conn.execute("SELECT count(*) FROM chat WHERE channel = 'voice'").fetchone()[0] == 2
    from kore.brain.clients import tool_use
    h.claude.client = ScriptedClient(lambda r: tool_use("forget", {"what": "conversation"})
                                     if r["messages"][-1]["role"] == "user" and
                                     not any(b.get("type") == "tool_result" for b in r["messages"][-1]["content"])
                                     else text("Olvidado."))
    h.heard("Olvídalo, por favor")
    assert h.conn.execute("SELECT count(*) FROM chat WHERE channel = 'voice'").fetchone()[0] == 0


def test_repeat_requests_are_counted(h):
    h.claude.line = "Le decía que hace buen día."
    h.heard("¿Qué?")
    h.heard("Pepe, repite")
    assert h.conn.execute("SELECT count(*) FROM behaviour_signals WHERE kind = 'repeat_request'").fetchone()[0] == 2
    h.heard("¿Qué hora es?")
    assert h.conn.execute("SELECT count(*) FROM behaviour_signals").fetchone()[0] == 2


def test_no_answer_to_a_nudge_is_noted_as_ignored(h):
    from kore.scheduler.engine import Event
    h.clock.set(at(11, 0))
    h.claude.line = "Carmen, ¿le apetece un vaso de agua?"
    h.manners.submit([Event("water", 2, [h.carmen])])
    h.tick()
    assert h.v.said == ["Carmen, ¿le apetece un vaso de agua?"]
    h.clock.advance(minutes=3)
    h.tick()
    assert [r[0] for r in h.conn.execute("SELECT kind FROM reply_signals")] == ["ignored"]


def test_a_short_answer_to_a_nudge_is_noted_as_brief(h):
    from kore.scheduler.engine import Event
    h.clock.set(at(11, 0))
    h.manners.submit([Event("water", 2, [h.carmen])])
    h.tick()
    h.heard("Ahora no")
    assert [r[0] for r in h.conn.execute("SELECT kind FROM reply_signals")] == ["not_now"]


def test_with_listening_declined_only_the_button_works(h):
    store.set_consent(h.conn, h.clock, h.carmen, "listening", False)
    assert h.heard("Pepe, ¿qué tal?") is None and h.v.said == [] and not h.wake_word_on
    h.claude.line = "Dígame."
    assert h.heard("¿Qué tal?", via="button") == "Dígame."


def test_every_turn_logs_its_timing_but_no_words(h, tmp_path):
    ticks = iter([100.0, 100.8, 101.0, 101.0])
    h.monotonic = lambda: next(ticks)
    h.claude.line = "Hola."
    h.heard("Hola Pepe", ended_at=99.5)
    entry = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[-1])
    assert entry["ms_to_first_word"] == 500 and "Hola" not in json.dumps(entry)


def test_a_new_day_starts_a_new_conversation(h):
    h.claude.line = "Hola."
    h.heard("Hola")
    first = h.brain
    h.clock.advance(days=1)
    h.heard("Hola")
    assert h.brain is not first


# --- the rest of the tick -------------------------------------------------------------------------------------


def test_the_console_sees_the_loop_alive(h):
    h.tick()
    assert family_msgs.get_state(h.conn, LOOP_SEEN)


def test_a_web_login_code_is_said_then_deleted(h):
    h.conn.execute("INSERT INTO say_queue (kind, text, created_at) VALUES ('web_code', 'Carmen, el código es uno, "
                   "dos, tres', 'x')")
    h.tick()
    assert "el código es uno" in h.v.said[0]
    assert h.conn.execute("SELECT count(*) FROM say_queue").fetchone()[0] == 0


def test_a_code_not_said_is_kept(h):
    h.conn.execute("INSERT INTO say_queue (kind, text, created_at) VALUES ('web_code', 'el código', 'x')")
    h.v.broken = True
    h.tick()
    assert h.conn.execute("SELECT count(*) FROM say_queue").fetchone()[0] == 1


def test_old_conversations_are_purged_daily(h):
    store.set_consent(h.conn, h.clock, h.carmen, "keep_conversations", True)
    chat.add(h.conn, h.clock, h.carmen, "person", "voice", "algo viejo")
    h.clock.advance(days=91)
    h.tick()
    assert h.conn.execute("SELECT count(*) FROM chat").fetchone()[0] == 0


def test_the_backup_runs_in_the_background_on_its_own_connection(setup, clock, tmp_path):
    db, carmen, _ = setup
    home = Home(db, clock, Claude(), FakeVoice(), {}, backups=True, backup_cert=tmp_path / "missing.pem")
    clock.set(at(3, 30, day=10))
    home.tick()
    home.wait_for_background()
    notes = [r[0] for r in home.conn.execute("SELECT text FROM family_out WHERE kind = 'operator'")]
    assert any("copia de seguridad" in n for n in notes)  # no key yet: Lea is told, the loop carries on


def test_the_heartbeat_runs_in_the_background(setup, clock):
    db, _, _ = setup
    pings = []

    class Beat:
        def tick(self):
            pings.append(1)

    home = Home(db, clock, Claude(), FakeVoice(), {}, heartbeat=Beat(), backups=False)
    home.tick()
    home.wait_for_background()
    assert pings == [1]
