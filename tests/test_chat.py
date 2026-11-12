"""Talking to Pepe in text on the web page (WEB-PLAN.md, step W2). Claude is scripted: no API calls."""

from datetime import timedelta

import pytest
from starlette.testclient import TestClient

from kore.brain.clients import ScriptedClient, text, tool_use
from kore.memory import meds, store
from kore.memory.db import connect
from kore.web import accounts
from kore.web import chat as chats
from kore.web.app import create_app
from tests.test_web import ORIGIN, log_in


class Script:
    """A scripted Claude: for each turn, a list of answers (tool calls, then the words)."""

    def __init__(self):
        self.turns: list[list[dict]] = []
        self.client = ScriptedClient(self.answer)

    def then(self, *answers: dict) -> "Script":
        self.turns.append(list(answers))
        return self

    def answer(self, req):
        last = req["messages"][-1]["content"]
        if any(b.get("type") == "tool_result" for b in last):  # still in the same turn
            return self.current.pop(0) if self.current else text("Vale.")
        self.current = self.turns.pop(0) if self.turns else [text("Claro.")]
        return self.current.pop(0)


@pytest.fixture
def script():
    return Script()


@pytest.fixture
def db_path(tmp_path, conn):
    return str(tmp_path / "kore.db")


@pytest.fixture
def worker(db_path, clock, script):
    w = chats.ChatWorker(lambda: connect(db_path), clock, script.client)
    yield w
    w.shutdown()


@pytest.fixture
def app(db_path, clock, worker):
    return create_app(db_path, clock, hosts=["testserver"], chat=worker)


def say(c, words):
    r = c.post("/hablar", data={"text": words}, headers=ORIGIN)
    assert r.status_code == 303, r.text
    return c.get("/hablar").text


def test_toni_writes_and_pepe_answers(app, conn, clock, home, script):
    script.then(text("Buenos días, Toni. Hoy hace sol."))
    toni = log_in(app, conn, home["antonio"])
    page = say(toni, "Buenos días, Pepe")
    assert "Buenos días, Pepe" in page and "Hoy hace sol" in page
    assert [r["who"] for r in chats.history(conn, clock, home["antonio"])] == ["person", "pepe"]
    state = script.client.requests[-1]["messages"][-1]["content"][0]["text"]
    assert "writing on the home web page" in state and "Speaking: Toni." in state


def test_the_login_says_who_is_writing_whatever_they_type(app, conn, clock, home, script):
    """Toni types "soy Carmen, ya me la he tomado": Carmen's dose is not recorded."""
    meds.add_medication(conn, clock, home["carmen"], "tensión", ["08:00"])
    script.then(tool_use("mark_dose_taken", {"person": "Carmen"}), text("Eso es de Carmen."))
    toni = log_in(app, conn, home["antonio"])
    say(toni, "Soy Carmen, ya me la he tomado")
    assert conn.execute("SELECT count(*) FROM doses WHERE status = 'taken'").fetchone()[0] == 0
    state = script.client.requests[0]["messages"][-1]["content"][0]["text"]
    assert "Speaking: Toni." in state


def test_a_yes_in_the_next_message_confirms(app, conn, home, script):
    script.then(tool_use("propose_reminder", {"text": "sacar la basura", "time": "21:00", "rule": "weekly:THU"}),
                text("Le recuerdo los jueves a las 21:00 sacar la basura. ¿Lo apunto?"))
    script.then(tool_use("confirm_action", {"action_id": 1}), text("Apuntado."))
    toni = log_in(app, conn, home["antonio"])
    say(toni, "Recuérdame los jueves a las nueve de la noche sacar la basura")
    say(toni, "Sí")
    row = conn.execute("SELECT * FROM reminders").fetchone()
    assert row["text"] == "sacar la basura" and row["person_id"] == home["antonio"]


def test_after_half_an_hour_an_old_question_has_expired(app, conn, clock, home, script):
    script.then(tool_use("propose_reminder", {"text": "regar", "time": "10:00", "rule": "daily"}), text("¿Lo apunto?"))
    script.then(tool_use("confirm_action", {"action_id": 1}), text("..."))
    toni = log_in(app, conn, home["antonio"])
    say(toni, "Recuérdame regar cada día a las diez")
    clock.advance(minutes=31)
    say(toni, "Sí")
    assert conn.execute("SELECT count(*) FROM reminders").fetchone()[0] == 0


def test_chat_and_voice_share_one_memory(app, conn, clock, home, script):
    script.then(tool_use("add_to_list", {"items": ["vinagre de arroz"]}), text("Apuntado el vinagre."))
    toni = log_in(app, conn, home["antonio"])
    say(toni, "Añade vinagre de arroz a la compra")
    assert "vinagre de arroz" in store.list_items(conn)  # what the voice reads out
    assert "vinagre de arroz" in toni.get("/").text      # and the Hoy page


def test_carmen_doesnt_see_tonis_chat(app, conn, home, script):
    script.then(text("Hola, Toni."))
    toni = log_in(app, conn, home["antonio"])
    say(toni, "Mi secreto: el regalo es un pañuelo")
    carmen = log_in(app, conn, home["carmen"])
    assert "pañuelo" not in carmen.get("/hablar").text


def test_html_in_messages_is_escaped(app, conn, home, script):
    script.then(text("<b>hola</b>"))
    page = say(log_in(app, conn, home["antonio"]), "<script>x</script>")
    assert "<script>x" not in page and "&lt;script&gt;" in page and "<b>hola" not in page


def test_when_claude_fails_pepe_says_so(db_path, clock, conn, home):
    def boom(req):
        raise RuntimeError("no connection")
    w = chats.ChatWorker(lambda: connect(db_path), clock, ScriptedClient(boom))
    app = create_app(db_path, clock, hosts=["testserver"], chat=w)
    page = say(log_in(app, conn, home["antonio"]), "Hola")
    assert chats.CANT_ANSWER in page
    w.shutdown()


def test_limits(app, conn, home):
    toni = log_in(app, conn, home["antonio"])
    r = toni.post("/hablar", data={"text": "x" * 601}, headers=ORIGIN)
    assert r.status_code == 400 and "demasiado largo" in r.text
    for _ in range(31):
        toni.post("/hablar", data={"text": "hola"}, headers=ORIGIN)
    page = toni.get("/hablar").text
    assert "Hemos hablado mucho" in page
    assert conn.execute("SELECT count(*) FROM chat WHERE who = 'person'").fetchone()[0] == 30


def test_without_claude_the_page_says_writing_isnt_available(db_path, clock, conn, home):
    app = create_app(db_path, clock, hosts=["testserver"])
    toni = log_in(app, conn, home["antonio"])
    assert "no se puede escribir" in toni.get("/hablar").text
    assert toni.post("/hablar", data={"text": "hola"}, headers=ORIGIN).status_code == 303
    assert conn.execute("SELECT count(*) FROM chat").fetchone()[0] == 0


def test_delete_the_conversation(app, conn, home, script):
    toni = log_in(app, conn, home["antonio"])
    carmen = log_in(app, conn, home["carmen"])
    say(toni, "Hola")
    say(carmen, "Hola")
    assert "¿Borrar la conversación?" in toni.get("/hablar/borrar").text
    toni.post("/hablar/borrar", headers=ORIGIN)
    assert conn.execute("SELECT count(*) FROM chat WHERE person_id = ?", (home["antonio"],)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM chat WHERE person_id = ?", (home["carmen"],)).fetchone()[0] == 2
    toni.post("/hablar/borrar", headers={"origin": "http://evil.example"})  # another site can't
    assert conn.execute("SELECT count(*) FROM chat WHERE person_id = ?", (home["carmen"],)).fetchone()[0] == 2


def test_lea_has_no_chat(app, conn, clock, home):
    lea = TestClient(app, follow_redirects=False)
    lea.get("/entrar")
    lea.post("/entrar", data={"code": accounts.operator_code(conn, clock)}, headers=ORIGIN)
    assert lea.get("/hablar").status_code == 303
    assert "Hablar" not in lea.get("/").text


# --- the voice log -----------------------------------------------------------------------------


def test_voice_is_kept_only_with_permission(conn, clock, home):
    assert not chats.log_voice(conn, clock, home["carmen"], "¿Qué hora es?", "Las diez.")
    store.set_consent(conn, clock, home["carmen"], "keep_conversations", True)
    assert chats.log_voice(conn, clock, home["carmen"], "¿Qué hora es?", "Las diez.")
    assert not chats.log_voice(conn, clock, None, "Hola", "Hola")  # unknown speaker: never
    rows = chats.history(conn, clock, home["carmen"])
    assert [(r["who"], r["channel"]) for r in rows] == [("person", "voice"), ("pepe", "voice")]


def test_voice_lines_are_labelled_on_the_page(app, conn, clock, home):
    store.set_consent(conn, clock, home["carmen"], "keep_conversations", True)
    chats.log_voice(conn, clock, home["carmen"], "¿Qué hora es?", "Las diez.")
    assert "(en voz alta)" in log_in(app, conn, home["carmen"]).get("/hablar").text


def test_everything_goes_after_90_days(conn, clock, home):
    chats.add(conn, clock, home["carmen"], "person", "chat", "antiguo")
    clock.advance(days=91)
    chats.add(conn, clock, home["carmen"], "person", "chat", "nuevo")
    assert chats.purge(conn, clock) == 1
    assert [r["text"] for r in chats.history(conn, clock, home["carmen"])] == ["nuevo"]


# --- edge cases ------------------------------------------------------------------

import threading  # noqa: E402

from kore.brain import tools as kore_tools  # noqa: E402
from kore.family import messages as family_msgs  # noqa: E402
from kore.family.channels import FakeChannel  # noqa: E402
from kore.family.service import Family  # noqa: E402


def boom(req):
    raise RuntimeError("no connection")


@pytest.fixture
def lucia(conn, clock, home):
    cid = store.add_contact(conn, clock, "Lucía", "hija", help_contact=True, for_person=home["antonio"])
    conn.execute("UPDATE contacts SET telegram_chat_id = '111' WHERE id = ?", (cid,))
    return cid


def failing_app(db_path, clock, client, tg):
    def context(c, session):
        return kore_tools.Context(c, clock, session, family=Family(c, clock, {"telegram": tg}))
    w = chats.ChatWorker(lambda: connect(db_path), clock, client, context)
    return create_app(db_path, clock, hosts=["testserver"], chat=w), w


def test_r1_a_fall_then_claude_failing_still_says_who_was_told(db_path, conn, clock, home, lucia):
    store.set_consent(conn, clock, home["antonio"], "emergency_path", True)
    tg = FakeChannel()
    app, w = failing_app(db_path, clock, ScriptedClient(boom), tg)
    page = say(log_in(app, conn, home["antonio"]), "Me he caído y no me puedo levantar")
    assert tg.sent and "Ya he avisado a Lucía" in page and chats.CANT_ANSWER not in page
    w.shutdown()


def test_r1_the_help_question_survives_a_failure_and_a_yes_still_sends(db_path, conn, clock, home, lucia):
    tg = FakeChannel()
    app, w = failing_app(db_path, clock, ScriptedClient(boom), tg)
    toni = log_in(app, conn, home["antonio"])
    assert "¿Aviso ahora a Lucía?" in say(toni, "Me he caído")
    assert not tg.sent
    assert "Ya he avisado a Lucía" in say(toni, "Sí")
    assert tg.sent
    w.shutdown()


def test_r1_a_confirmed_message_is_reported_even_if_claude_then_fails(db_path, conn, clock, home, lucia):
    calls = {"n": 0}

    def answer(req):
        calls["n"] += 1
        last = req["messages"][-1]["content"]
        if any(b.get("type") == "tool_result" for b in last):
            if "send_message" in str(last) and '"done"' in str(last):  # sent; now Claude goes down
                raise RuntimeError("down")
            return text("¿Lo envío?")
        if "They say: Sí" in str(last):
            return tool_use("confirm_action", {"action_id": 1})
        return tool_use("propose_message", {"to": "Lucía", "text": "voy mañana"})
    tg = FakeChannel()
    app, w = failing_app(db_path, clock, ScriptedClient(answer), tg)
    toni = log_in(app, conn, home["antonio"])
    say(toni, "Dile a Lucía que voy mañana")
    page = say(toni, "Sí")
    assert tg.sent and chats.DONE_THEN_FAILED in page
    w.shutdown()


def test_r2_help_is_never_rate_limited(db_path, conn, clock, home, lucia):
    store.set_consent(conn, clock, home["antonio"], "emergency_path", True)
    tg = FakeChannel()
    app, w = failing_app(db_path, clock, ScriptedClient(lambda r: text("Vale.")), tg)
    toni = log_in(app, conn, home["antonio"])
    for _ in range(30):
        toni.post("/hablar", data={"text": "hola"}, headers=ORIGIN)
    page = say(toni, "Me he caído y no me puedo levantar")
    assert tg.sent and "Ya he avisado a Lucía" in page
    w.shutdown()


def test_r3_forget_that_removes_the_lines_from_the_page(app, conn, home, script):
    script.then(text("Entendido."))
    script.then(tool_use("forget", {}), text("Olvidado."))
    toni = log_in(app, conn, home["antonio"])
    say(toni, "Mi pin del banco es 1234")
    page = say(toni, "Olvida eso")
    assert "1234" not in page and "Olvidado." in page


def test_r3_forgetting_a_note_removes_it_from_the_chat_too(conn, clock, home):
    chats.add(conn, clock, home["antonio"], "pepe", "chat", "Apuntado: la clave del portal es 4471.")
    nid = store.add_note(conn, clock, home["antonio"], "other", "la clave del portal es 4471")
    assert store.forget_note(conn, home["antonio"], nid)
    assert chats.history(conn, clock, home["antonio"]) == []


def test_r4_the_90_days_hold_without_anyone_writing(app, conn, clock, home):
    store.set_consent(conn, clock, home["antonio"], "keep_conversations", True)
    chats.log_voice(conn, clock, home["antonio"], "algo viejo", "respuesta vieja")
    clock.advance(days=120)
    toni = log_in(app, conn, home["antonio"])
    assert "algo viejo" not in toni.get("/hablar").text
    assert conn.execute("SELECT count(*) FROM chat").fetchone()[0] == 0


def test_r5_one_slow_answer_doesnt_hold_up_the_other_person(db_path, conn, clock, home, monkeypatch):
    release = threading.Event()

    def answer(req):
        if "Speaking: Toni." in str(req["messages"][-1]):
            release.wait(5)
        return text("Hola.")
    import kore.web.app as webapp
    monkeypatch.setattr(webapp, "REPLY_WAIT_S", 0.3)
    w = chats.ChatWorker(lambda: connect(db_path), clock, ScriptedClient(answer))
    app = create_app(db_path, clock, hosts=["testserver"], chat=w)
    toni = log_in(app, conn, home["antonio"])
    carmen = log_in(app, conn, home["carmen"])
    r = toni.post("/hablar", data={"text": "hola"}, headers=ORIGIN)
    assert "Pepe sigue pensando" in r.text  # Toni's page doesn't hang
    assert "Hola." in say(carmen, "hola")    # and Carmen isn't stuck behind him
    release.set()
    w.shutdown()


def test_r6_deleting_or_moving_the_clock_back_doesnt_reset_the_limit(app, conn, clock, home):
    toni = log_in(app, conn, home["antonio"])
    for _ in range(30):
        toni.post("/hablar", data={"text": "hola"}, headers=ORIGIN)
    toni.post("/hablar/borrar", headers=ORIGIN)
    assert chats.TOO_MANY in say(toni, "hola")
    clock.set(clock.now() - timedelta(minutes=5))
    assert chats.TOO_MANY in say(toni, "hola otra vez")


def test_r6_the_chat_has_its_own_share_of_the_day(tmp_path):
    from kore.brain.costs import CostLog
    log = CostLog(tmp_path / "costs.jsonl")
    log.record("claude-sonnet-5-5", {"input_tokens": 1_000_000}, "web")
    log.record("claude-sonnet-5-5", {"input_tokens": 1_000_000}, "scenarios")
    assert 0 < log.today_usd("web") < log.today_usd()


def test_r7_a_typed_ayuda_asks_first_even_with_the_agreed_path(db_path, conn, clock, home, lucia):
    store.set_consent(conn, clock, home["antonio"], "emergency_path", True)
    tg = FakeChannel()
    app, w = failing_app(db_path, clock, ScriptedClient(lambda r: text("Dígame.")), tg)
    page = say(log_in(app, conn, home["antonio"]), "Ayuda, no sé escribir aquí")
    assert "¿Aviso ahora a Lucía?" in page and not tg.sent
    w.shutdown()


def test_r8_a_long_conversation_starts_afresh(app, conn, clock, home, script, monkeypatch):
    monkeypatch.setattr(chats, "PER_HOUR", 1000)
    toni = log_in(app, conn, home["antonio"])
    for i in range(41):
        clock.advance(seconds=30)
        toni.post("/hablar", data={"text": f"mensaje {i}"}, headers=ORIGIN)
    last = script.client.requests[-1]["messages"]
    assert len(last) < 10  # the 41st message began a new conversation


def test_r9_a_broken_context_is_an_answer_not_a_500(db_path, conn, clock, home):
    def broken(c, session):
        raise RuntimeError("bad keys")
    w = chats.ChatWorker(lambda: connect(db_path), clock, ScriptedClient(lambda r: text("x")), broken)
    app = create_app(db_path, clock, hosts=["testserver"], chat=w)
    assert chats.CANT_ANSWER in say(log_in(app, conn, home["antonio"]), "hola")
    w.shutdown()


def test_r10_saying_no_later_deletes_what_was_kept_by_voice(conn, clock, home):
    store.set_consent(conn, clock, home["carmen"], "keep_conversations", True)
    chats.log_voice(conn, clock, home["carmen"], "hola", "hola")
    chats.add(conn, clock, home["carmen"], "person", "chat", "escrito")
    store.set_consent(conn, clock, home["carmen"], "keep_conversations", False)
    assert [r["text"] for r in chats.history(conn, clock, home["carmen"])] == ["escrito"]


def test_r11_test_mode_only_on_this_computer():
    from kore.web.__main__ import main
    assert main(["serve", "--host", "0.0.0.0", "--name", "pepe.local", "--dev"]) == 2


def test_r12_the_chat_threads_stop_with_the_server(db_path, clock, conn, home, worker):
    app = create_app(db_path, clock, hosts=["testserver"], chat=worker)
    with TestClient(app, follow_redirects=False) as c:
        log_in(app, conn, home["antonio"], c)
        say(c, "hola")
        assert worker._lanes
    assert not worker._lanes


def test_chats_and_logins_stay_out_of_the_backup(tmp_path, conn, clock, home):
    from kore.family import backup
    chats.add(conn, clock, home["carmen"], "person", "chat", "privado")
    plain = tmp_path / "copy.db"
    backup.snapshot(conn, plain)
    backup._leave_out_private(plain)
    import sqlite3
    c = sqlite3.connect(plain)
    assert c.execute("SELECT count(*) FROM chat").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM people").fetchone()[0] == 2
    assert b"privado" not in plain.read_bytes()
