"""Step W3 (WEB-PLAN.md): the calendar, the shopping list and "lo que sabe de mí" on the web page.

Done when: something added on the web is reminded out loud, and the other way round; nothing is
saved without a yes.
"""

import re
from datetime import timedelta

import pytest

from kore.brain import tools
from kore.brain.session import Person, Session
from kore.memory import store
from kore.scheduler.engine import Engine
from kore.web import agenda
from kore.web import chat as chats
from tests.conftest import at
from tests.test_engine import run
from tests.test_web import ORIGIN, app, client, db_path, log_in  # noqa: F401 (fixtures)


def appointments(conn):
    return conn.execute("SELECT * FROM appointments WHERE cancelled = 0").fetchall()


def pending_id(page: str) -> str:
    return re.search(r'action="/confirmar"><input type="hidden" name="id" value="(\d+)"', page)[1]


def propose(c, path, **data):
    r = c.post(path, data=data, headers=ORIGIN)
    assert r.status_code == 200, r.text
    return r.text


def yes(c, page):
    return c.post("/confirmar", data={"id": pending_id(page)}, headers=ORIGIN)


def voice_ctx(conn, clock, home, speaker):
    s = Session([Person(home["carmen"], "Carmen", "Carmen"), Person(home["antonio"], "Antonio", "Toni")],
                fixed_speaker=home[speaker])
    return tools.Context(conn, clock, s)


# --- the "done when" ---------------------------------------------------------------------------


def test_an_appointment_added_on_the_web_is_reminded_out_loud(app, conn, clock, home):
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/calendario/nueva-cita", title="Médico de cabecera", day="2026-11-12", time="10:30",
                   where="centro de salud")
    assert "Cita: «Médico de cabecera», el jueves 12 de noviembre a las 10:30, en centro de salud." in page
    assert "Sí, apúntalo" in page
    assert appointments(conn) == []  # the form alone saves nothing
    r = yes(c, page)
    assert r.status_code == 303 and r.headers["location"] == "/calendario?hecho=apuntado"
    [a] = appointments(conn)
    assert a["person_id"] == home["carmen"] and a["title"] == "Médico de cabecera"
    events = run(Engine(conn, clock), clock, at(10, 0, day=12))
    said = [(e.kind, e.data["title"], e.person_ids) for _, e in events
            if e.kind.startswith("appointment")]
    assert [k for k, *_ in said] == ["appointment_day_before", "appointment_same_day"]
    assert all(e.private for _, e in events if e.kind.startswith("appointment"))


def test_an_appointment_said_by_voice_shows_on_the_web(app, conn, clock, home):
    ctx = voice_ctx(conn, clock, home, "antonio")
    ctx.session.begin_turn("Apunta el dentista el viernes a las once")
    r = tools.call(ctx, "propose_appointment", {"title": "Dentista", "starts_at": "2026-11-13T11:00"})
    ctx.session.begin_turn("Sí")
    assert tools.call(ctx, "confirm_action", {"action_id": r["action_id"]})["ok"]
    toni = log_in(app, conn, home["antonio"])
    page = toni.get("/calendario").text
    assert "Dentista" in page and "viernes 13 de noviembre a las 11:00" in page
    carmen = log_in(app, conn, home["carmen"])
    assert "Dentista" not in carmen.get("/calendario").text  # Toni's own: not on Carmen's page


def test_a_weekly_reminder_from_the_web_is_said_on_its_days(app, conn, clock, home):
    c = log_in(app, conn, home["antonio"])
    page = propose(c, "/calendario/nuevo-recordatorio", text="Sacar la basura", time="21:00", how="weekly",
                   d_MON="1", d_THU="1", shared="1")
    assert "Pepe le recordará «Sacar la basura» los lunes y jueves a las 21:00. Para los dos." in page
    assert store.reminders(conn) == []
    yes(c, page)
    events = [e for _, e in run(Engine(conn, clock), clock, at(23, 0, day=12)) if e.kind == "reminder"]
    assert [(e.data["text"], e.data["time"]) for e in events] == [("Sacar la basura", "21:00")] * 2  # Mon 9, Thu 12
    assert set(events[0].person_ids) == {home["carmen"], home["antonio"]}


def test_a_reminder_said_by_voice_can_be_stopped_on_the_web_and_the_other_way_round(app, conn, clock, home):
    ctx = voice_ctx(conn, clock, home, "carmen")
    ctx.session.begin_turn("Recuérdame regar las plantas cada día a las diez")
    r = tools.call(ctx, "propose_reminder", {"text": "regar las plantas", "time": "10:00", "rule": "daily"})
    ctx.session.begin_turn("Sí")
    tools.call(ctx, "confirm_action", {"action_id": r["action_id"]})
    c = log_in(app, conn, home["carmen"])
    page = c.get("/calendario").text
    assert "regar las plantas" in page and "todos los días a las 10:00" in page
    rid = store.reminders(conn)[0]["id"]
    page = propose(c, "/calendario/recordatorio/quitar", id=str(rid))
    assert "Pepe dejará de recordarle «regar las plantas»." in page and store.reminders(conn)
    yes(c, page)
    assert store.reminders(conn) == []
    # By voice: one added on the web is stopped with a yes.
    page = propose(c, "/calendario/nuevo-recordatorio", text="llamar a Lucía", time="18:00", how="daily")
    yes(c, page)
    ctx.session.begin_turn("Ya no me recuerdes lo de llamar a Lucía")
    [rem] = tools.call(ctx, "get_reminders", {})["reminders"]
    r = tools.call(ctx, "propose_reminder_stop", {"reminder_id": rem["id"]})
    assert store.reminders(conn)  # not without the yes
    ctx.session.begin_turn("Sí, quítalo")
    assert tools.call(ctx, "confirm_action", {"action_id": r["action_id"]})["ok"]
    assert store.reminders(conn) == []


def test_the_voice_cant_stop_the_other_persons_reminder(conn, clock, home):
    store.add_reminder(conn, clock, home["carmen"], "la crema", "22:00", "daily", clock.now().date())
    ctx = voice_ctx(conn, clock, home, "antonio")
    ctx.session.begin_turn("Quita lo de la crema")
    r = tools.call(ctx, "propose_reminder_stop", {"reminder_id": store.reminders(conn)[0]["id"]})
    assert r["error"] == "speaker_is_someone_else"
    assert tools.call(ctx, "get_reminders", {})["reminders"] == []  # and he doesn't see it


def test_a_birthday_from_the_web_is_said_that_day_and_can_be_stopped(app, conn, clock, home):
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/calendario/nuevo-cumple", name="Ana", relation="nieta", day="10", month="11", shared="1")
    assert "Cumpleaños de Ana (nieta), el 10 de noviembre." in page
    yes(c, page)
    events = [e for _, e in run(Engine(conn, clock), clock, at(11, 0, day=10)) if e.kind == "birthday"]
    assert [e.data["name"] for e in events] == ["Ana"]
    cid = store.birthdays(conn, home["antonio"])[0]["id"]
    toni = log_in(app, conn, home["antonio"])
    assert "Ana" in toni.get("/calendario").text  # shared
    yes(toni, propose(toni, "/calendario/cumple/quitar", id=str(cid)))
    assert store.birthdays(conn, home["carmen"]) == []
    assert conn.execute("SELECT name FROM contacts").fetchone()["name"] == "Ana"  # the person stays


# --- nothing saved without a yes -----------------------------------------------------------------


def test_no_saves_nothing_and_says_so(app, conn, home):
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/calendario/nueva-cita", title="Podólogo", day="2026-11-20", time="09:00")
    r = c.post("/confirmar/no", data={"id": pending_id(page), "back": "/calendario"}, headers=ORIGIN)
    assert r.headers["location"] == "/calendario?hecho=anulado"
    assert "No se ha cambiado nada." in c.get(r.headers["location"]).text
    assert yes(c, page).status_code == 409 and appointments(conn) == []


def test_no_goes_back_only_to_our_own_pages(app, conn, home):
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/calendario/nueva-cita", title="Podólogo", day="2026-11-20", time="09:00")
    r = c.post("/confirmar/no", data={"id": pending_id(page), "back": "https://evil.example"}, headers=ORIGIN)
    assert r.headers["location"] == "/calendario?hecho=anulado"


def test_a_yes_must_come_from_this_page(app, conn, home):
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/calendario/nueva-cita", title="Podólogo", day="2026-11-20", time="09:00")
    r = c.post("/confirmar", data={"id": pending_id(page)})  # no Origin: a form on another site
    assert r.status_code == 403 and appointments(conn) == []
    r = c.post("/confirmar", data={"id": pending_id(page)}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403 and appointments(conn) == []


def test_a_late_yes_or_an_older_proposal_is_refused(app, conn, clock, home):
    c = log_in(app, conn, home["carmen"])
    first = propose(c, "/calendario/nueva-cita", title="Podólogo", day="2026-11-20", time="09:00")
    second = propose(c, "/calendario/nueva-cita", title="Oculista", day="2026-11-21", time="09:00")
    r = yes(c, first)
    assert r.status_code == 409 and "Vuelva a escribirlo" in r.text  # only the latest read-back counts
    clock.advance(minutes=16)
    assert yes(c, second).status_code == 409
    assert appointments(conn) == []


def test_a_yes_counts_once(app, conn, home):
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/calendario/nueva-cita", title="Podólogo", day="2026-11-20", time="09:00")
    assert yes(c, page).status_code == 303
    r = yes(c, page)
    assert r.status_code == 409 and "ya estaba hecho" in r.text
    assert len(appointments(conn)) == 1


def test_the_other_person_cant_say_yes_for_you(app, conn, home):
    carmen = log_in(app, conn, home["carmen"])
    page = propose(carmen, "/calendario/nueva-cita", title="Podólogo", day="2026-11-20", time="09:00")
    toni = log_in(app, conn, home["antonio"])
    assert yes(toni, page).status_code == 409
    assert appointments(conn) == []
    assert yes(carmen, page).status_code == 303  # and Toni's try didn't spoil Carmen's


def test_things_are_checked_again_at_the_yes(app, conn, clock, home):
    """Between the read-back and the yes, the appointment was cancelled by voice."""
    aid = store.add_appointment(conn, clock, None, "Notario", at(12, 0, day=16))
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/calendario/cita", id=str(aid), day="2026-11-17", time="12:00")
    assert "La cita «Notario» pasa a el martes 17 de noviembre a las 12:00." in page
    store.cancel_appointment(conn, aid)
    r = yes(c, page)
    assert r.status_code == 409 and "ya no está en el calendario" in r.text
    assert conn.execute("SELECT starts_at_utc FROM appointments").fetchone()[0].startswith("2026-11-16")


def test_moving_an_appointment_resets_its_reminders(app, conn, clock, home):
    aid = store.add_appointment(conn, clock, home["carmen"], "Analítica", at(9, 0, day=10))
    e = Engine(conn, clock)
    assert [ev.kind for _, ev in run(e, clock, at(20, 30))] == ["appointment_day_before"]
    c = log_in(app, conn, home["carmen"])
    yes(c, propose(c, "/calendario/cita", id=str(aid), day="2026-11-12", time="09:00"))
    kinds = [ev.kind for _, ev in run(e, clock, at(9, 0, day=12)) if ev.kind.startswith("appointment")]
    assert kinds == ["appointment_day_before", "appointment_same_day"]


def test_cancelling_on_the_web_stops_the_reminders(app, conn, clock, home):
    aid = store.add_appointment(conn, clock, None, "Notario", at(12, 0, day=10))
    c = log_in(app, conn, home["antonio"])
    page = propose(c, "/calendario/cita/anular", id=str(aid))
    assert "Anular la cita «Notario» de mañana a las 12:00." in page and "Sí, anúlala" in page
    assert yes(c, page).headers["location"] == "/calendario?hecho=quitado"
    assert [ev for _, ev in run(Engine(conn, clock), clock, at(13, 0, day=10)) if ev.kind.startswith("appoint")] == []


# --- whose is what -----------------------------------------------------------------------------


def test_only_the_owner_changes_a_personal_appointment(app, conn, clock, home):
    aid = store.add_appointment(conn, clock, home["carmen"], "Ginecólogo", at(9, 0, day=20))
    toni = log_in(app, conn, home["antonio"])
    assert "Ginecólogo" not in toni.get("/calendario").text
    assert toni.get(f"/calendario/cita?id={aid}").status_code == 404
    r = toni.post("/calendario/cita/anular", data={"id": str(aid)}, headers=ORIGIN)
    assert r.status_code == 400 and "ya no está en el calendario" in r.text and "Ginecólogo" not in r.text
    # The same words as for an id that doesn't exist: his answer doesn't tell him the id is hers.
    r2 = toni.post("/calendario/cita/anular", data={"id": "999"}, headers=ORIGIN)
    assert re.search(r'role="status">([^<]*)', r.text)[1] == re.search(r'role="status">([^<]*)', r2.text)[1]
    assert len(appointments(conn)) == 1


def test_a_no_to_keeping_appointments_holds_on_the_web(app, conn, clock, home):
    store.set_consent(conn, clock, home["carmen"], "health_info", False)
    c = log_in(app, conn, home["carmen"])
    r = c.post("/calendario/nueva-cita", data={"title": "Cardiólogo", "day": "2026-11-20", "time": "09:00"},
               headers=ORIGIN)
    assert r.status_code == 400 and "Usted dijo que Pepe no guarde sus citas" in r.text
    assert 'value="Cardiólogo"' in r.text  # the form comes back filled in
    page = propose(c, "/calendario/nueva-cita", title="Cena con los vecinos", day="2026-11-20", time="21:00",
                   shared="1")
    assert yes(c, page).status_code == 303
    r = c.post("/calendario/nuevo-recordatorio", data={"text": "la pastilla", "time": "09:00", "how": "daily"},
               headers=ORIGIN)
    assert r.status_code == 400


def test_dates_in_the_past_or_far_away_are_refused(app, conn, clock, home):
    c = log_in(app, conn, home["carmen"])
    for day, time, why in [("2026-11-09", "07:00", "ya han pasado"), ("2026-11-01", "10:00", "ya han pasado"),
                           ("2029-01-01", "10:00", "dos años"), ("2026-02-30", "10:00", "Falta el día"),
                           ("2026-11-20", "25:00", "Falta la hora"), ("", "10:00", "Falta el día")]:
        r = c.post("/calendario/nueva-cita", data={"title": "X", "day": day, "time": time}, headers=ORIGIN)
        assert r.status_code == 400 and why in r.text, (day, time)
    r = c.post("/calendario/nuevo-recordatorio", data={"text": "X", "time": "10:00", "how": "weekly"}, headers=ORIGIN)
    assert "Marque qué días" in r.text
    r = c.post("/calendario/nuevo-cumple", data={"name": "Ana", "day": "31", "month": "2"}, headers=ORIGIN)
    assert "no existe esa fecha" in r.text
    assert c.get("/calendario").status_code == 200 and appointments(conn) == []


def test_with_an_unreliable_clock_the_calendar_doesnt_change(app, conn, clock, home):
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/calendario/nueva-cita", title="Podólogo", day="2026-11-20", time="09:00")
    clock.set_trusted(False)
    assert "no es fiable" in c.get("/calendario").text
    r = yes(c, page)
    assert r.status_code == 409 and "no es fiable" in r.text and appointments(conn) == []
    r = c.post("/calendario/nueva-cita", data={"title": "X", "day": "2026-11-20", "time": "09:00"}, headers=ORIGIN)
    assert r.status_code == 400


def test_text_from_the_forms_is_plain_and_escaped(app, conn, home):
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/calendario/nueva-cita", title='<script>alert(1)</script>\x00\nCita', day="2026-11-20",
                   time="9.05")
    assert "<script>" not in page and "&lt;script&gt;" in page
    yes(c, page)
    assert appointments(conn)[0]["title"] == "<script>alert(1)</script> Cita"
    assert "<script>" not in c.get("/calendario").text
    assert "a las 09:05" in c.get("/calendario").text


def test_the_operator_and_strangers_get_none_of_these_pages(app, conn, clock, home):
    stranger = client(app)
    for path in ("/calendario", "/compra", "/sabe", "/calendario/nueva-cita"):
        assert stranger.get(path).headers["location"] == "/entrar"
    assert stranger.post("/compra/anadir", data={"item": "whisky"}, headers=ORIGIN).status_code == 303
    assert store.list_items(conn) == []
    from kore.web import accounts
    code = accounts.operator_code(conn, clock)
    lea = client(app)
    lea.get("/entrar")
    lea.post("/entrar", data={"code": code}, headers=ORIGIN)
    assert lea.get("/").status_code == 200
    for path in ("/calendario", "/compra", "/sabe"):
        assert lea.get(path).headers["location"] == "/entrar"


# --- the shopping list ---------------------------------------------------------------------------


def test_the_shopping_list_is_the_same_by_voice_and_on_the_web(app, conn, clock, home):
    c = log_in(app, conn, home["antonio"])
    assert c.post("/compra/anadir", data={"item": "  leche  "}, headers=ORIGIN).headers["location"] == "/compra"
    ctx = voice_ctx(conn, clock, home, "carmen")
    ctx.session.begin_turn("¿Qué hay en la lista?")
    assert tools.call(ctx, "read_list", {})["items"] == ["leche"]
    tools.call(ctx, "add_to_list", {"items": ["pan"]})
    page = c.get("/compra").text
    assert "leche" in page and "pan" in page
    c.post("/compra/anadir", data={"item": "Leche"}, headers=ORIGIN)
    assert store.list_items(conn) == ["leche", "pan"]  # not twice


def test_an_item_ticked_off_can_be_put_back(app, conn, clock, home):
    store.add_to_list(conn, clock, ["huevos", "aceite"])
    c = log_in(app, conn, home["carmen"])
    iid = conn.execute("SELECT id FROM list_items WHERE item = 'huevos'").fetchone()[0]
    r = c.post("/compra/quitar", data={"id": str(iid)}, headers=ORIGIN)
    assert r.headers["location"] == f"/compra?quitado={iid}"
    page = c.get(r.headers["location"]).text
    assert "Quitado: huevos" in page and "Volver a ponerlo" in page
    assert store.list_items(conn) == ["aceite"]
    c.post("/compra/volver", data={"id": str(iid)}, headers=ORIGIN)
    assert sorted(store.list_items(conn)) == ["aceite", "huevos"]
    c.post("/compra/volver", data={"id": str(iid)}, headers=ORIGIN)
    assert sorted(store.list_items(conn)) == ["aceite", "huevos"]  # pressed twice: still once


def test_the_shopping_list_has_limits(app, conn, home):
    c = log_in(app, conn, home["carmen"])
    assert c.post("/compra/anadir", data={"item": "   "}, headers=ORIGIN).status_code == 400
    c.post("/compra/anadir", data={"item": "x" * 500}, headers=ORIGIN)
    assert store.list_items(conn) == ["x" * agenda.ITEM_CHARS]
    assert c.post("/compra/quitar", data={"id": "99999999999999999999"}, headers=ORIGIN).status_code == 303


# --- what Pepe knows ---------------------------------------------------------------------------


def test_notes_are_shown_only_to_their_person_and_added_with_a_yes(app, conn, clock, home):
    store.add_note(conn, clock, home["carmen"], "interest", "le gusta el pasodoble")
    carmen = log_in(app, conn, home["carmen"])
    toni = log_in(app, conn, home["antonio"])
    assert "pasodoble" in carmen.get("/sabe").text and "pasodoble" not in toni.get("/sabe").text
    page = propose(toni, "/sabe/anadir", text="Fui ferroviario en Zaragoza", kind="story")
    assert "Pepe apuntará sobre usted (una historia): «Fui ferroviario en Zaragoza»." in page
    assert store.notes(conn, home["antonio"]) == []
    assert yes(toni, page).headers["location"] == "/sabe?hecho=apuntado"
    assert [n["text"] for n in store.notes(conn, home["antonio"])] == ["Fui ferroviario en Zaragoza"]
    # And Pepe knows it by voice.
    ctx = voice_ctx(conn, clock, home, "antonio")
    ctx.session.begin_turn("¿Qué sabes de mí?")
    assert tools.call(ctx, "what_do_you_know_about_me", {})["notes"][0]["text"] == "Fui ferroviario en Zaragoza"


def test_forget_on_the_web_really_forgets(app, conn, clock, home):
    nid = store.add_note(conn, clock, home["carmen"], "health_note", "me duele la rodilla")
    chats.add(conn, clock, home["carmen"], "person", "chat", "Me duele la rodilla, ¿sabes?")
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/sabe/olvidar", id=str(nid))
    assert "Pepe olvidará para siempre: «me duele la rodilla»." in page and "Sí, olvídalo" in page
    assert store.notes(conn, home["carmen"])  # not before the yes
    assert yes(c, page).headers["location"] == "/sabe?hecho=olvidado"
    assert store.notes(conn, home["carmen"]) == []
    assert conn.execute("SELECT count(*) FROM chat").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM web_pending WHERE instr(readback, 'rodilla') > 0").fetchone()[0] == 0


def test_the_other_person_cant_forget_your_notes(app, conn, clock, home):
    nid = store.add_note(conn, clock, home["carmen"], "interest", "le gusta el pasodoble")
    toni = log_in(app, conn, home["antonio"])
    r = toni.post("/sabe/olvidar", data={"id": str(nid)}, headers=ORIGIN)
    assert r.status_code == 400 and "pasodoble" not in r.text
    assert store.notes(conn, home["carmen"])


def test_pending_changes_are_not_backed_up(conn):
    from kore.family import backup
    assert "web_pending" in backup.NOT_BACKED_UP


def test_menu_shows_the_new_pages(app, conn, home):
    page = log_in(app, conn, home["carmen"]).get("/").text
    for href in ("/hablar", "/agenda", "/mi-familia", "/ajustes", "/ayuda"):
        assert f'href="{href}"' in page
    agenda_page = log_in(app, conn, home["carmen"]).get("/agenda").text
    for href in ("/calendario", "/compra"):
        assert f'href="{href}"' in agenda_page


@pytest.mark.parametrize("rule, words", [
    ("daily", "todos los días"), ("weekly:SAT,SUN", "los sábados y domingos"),
    ("weekly:MON,WED,FRI", "los lunes, miércoles y viernes"), ("every:3", "cada 3 días")])
def test_rules_in_words(rule, words):
    from datetime import date
    assert agenda.rule_words(rule, date(2026, 11, 9)) == words


def test_old_proposals_are_deleted_after_a_day(conn, clock, home):
    agenda.propose_note(conn, clock, home["carmen"], {"text": "algo"})
    clock.advance(days=1, minutes=1)
    agenda.propose_note(conn, clock, home["antonio"], {"text": "otra cosa"})
    assert conn.execute("SELECT count(*) FROM web_pending").fetchone()[0] == 1
    assert timedelta(minutes=15) == agenda.PENDING_LIFE


# --- from the fresh review (W3) ------------------------------------------------------------------


def test_r1_a_note_forgotten_by_voice_leaves_no_web_read_back(app, conn, clock, home):
    c = log_in(app, conn, home["carmen"])
    yes(c, propose(c, "/sabe/anadir", text="Tengo diabetes desde 2010", kind="health_note"))
    propose(c, "/sabe/anadir", text="Tengo diabetes desde 2010, tipo 2")  # a later one, left unanswered
    [n] = store.notes(conn, home["carmen"])
    ctx = voice_ctx(conn, clock, home, "carmen")
    ctx.session.begin_turn("Olvida lo de la diabetes")
    assert tools.call(ctx, "forget", {"note_id": n["id"]})["ok"]
    left = conn.execute("SELECT readback, payload FROM web_pending").fetchall()
    assert left and all("diabetes" not in r["readback"] + r["payload"] for r in left)


def test_r1_old_proposals_go_when_a_page_is_shown(app, conn, clock, home):
    c = log_in(app, conn, home["carmen"])
    propose(c, "/sabe/anadir", text="algo mío")
    clock.advance(days=1, minutes=1)
    c = log_in(app, conn, home["carmen"])
    c.get("/sabe")
    assert conn.execute("SELECT count(*) FROM web_pending").fetchone()[0] == 0


def test_r2_a_birthday_for_one_person_is_said_to_them_only(app, conn, clock, home):
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/calendario/nuevo-cumple", name="Pedro", day="10", month="11")
    assert "Solo para usted." in page
    yes(c, page)
    store.add_contact(conn, clock, "Ana", "nieta", "11-10")  # shared
    events = [e for _, e in run(Engine(conn, clock), clock, at(11, 0, day=10)) if e.kind == "birthday"]
    said = {e.data["name"]: (e.person_ids, e.private) for e in events}
    assert said == {"Pedro": ([home["carmen"]], True), "Ana": ([home["carmen"], home["antonio"]], False)}


def test_r3_a_new_daily_reminder_after_its_time_starts_tomorrow(app, conn, clock, home):
    clock.set(at(12, 0))
    c = log_in(app, conn, home["carmen"])
    yes(c, propose(c, "/calendario/nuevo-recordatorio", text="Regar las plantas", time="10:00", how="daily"))
    yes(c, propose(c, "/calendario/nuevo-recordatorio", text="Merendar", time="17:00", how="daily"))
    events = [(e.data["text"], t) for t, e in run(Engine(conn, clock), clock, at(11, 0, day=10)) if e.kind == "reminder"]
    assert events == [("Merendar", "17:00"), ("Regar las plantas", "10:00")]  # later today; the passed one tomorrow


def test_r3_a_one_off_reminder_whose_time_passes_before_the_yes_is_refused(app, conn, clock, home):
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/calendario/nuevo-recordatorio", text="Llamar al fontanero", time="08:05", how="once",
                   day="2026-11-09")
    clock.advance(minutes=10)
    r = yes(c, page)
    assert r.status_code == 409 and "ya han pasado" in r.text and store.reminders(conn) == []


def test_r3_by_voice_a_one_off_reminder_in_the_past_is_refused(conn, clock, home):
    ctx = voice_ctx(conn, clock, home, "carmen")
    ctx.session.begin_turn("Recuérdame a las siete lo del fontanero")
    r = tools.call(ctx, "propose_reminder", {"text": "fontanero", "time": "07:00", "rule": "once"})
    assert r["error"] == "bad_input" and "passed" in r["detail"]


def test_r4_odd_digits_and_huge_numbers_are_refused_not_crashes(app, conn, home):
    c = log_in(app, conn, home["carmen"])
    for path, data in [("/calendario/nueva-cita", {"title": "X", "day": "2026-11-20", "time": "10:\u0663\u0660"}),
                       ("/calendario/nuevo-recordatorio", {"text": "X", "time": "10:\u0663\u0660", "how": "daily"}),
                       ("/calendario/nuevo-cumple", {"name": "A", "day": "99999999999999999999", "month": "1"}),
                       ("/calendario/nuevo-cumple", {"name": "A", "day": "\u0663", "month": "1"}),
                       ("/calendario/nuevo-cumple", {"name": "A", "day": "-1", "month": "1"})]:
        assert c.post(path, data=data, headers=ORIGIN).status_code == 400, data
    assert store.reminders(conn) == [] and appointments(conn) == []


def test_r5_the_spring_forward_gap_is_read_back_as_saved(app, conn, clock, home):
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/calendario/nueva-cita", title="Tren", day="2027-03-28", time="02:30")
    assert "a las 03:30" in page and "02:30" not in page
    yes(c, page)
    assert "a las 03:30" in c.get("/calendario").text


def test_r6_birthdays_dont_change_with_an_unreliable_clock(app, conn, clock, home):
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/calendario/nuevo-cumple", name="Ana", day="3", month="3")
    clock.set_trusted(False)
    assert yes(c, page).status_code == 409
    r = c.post("/calendario/nuevo-cumple", data={"name": "Ana", "day": "3", "month": "3"}, headers=ORIGIN)
    assert r.status_code == 400 and store.birthdays(conn, home["carmen"]) == []


def test_small_one_o_clock_is_a_la_una(app, conn, home):
    c = log_in(app, conn, home["carmen"])
    page = propose(c, "/calendario/nuevo-recordatorio", text="Tomar el aire", time="13:00", how="daily")
    assert "a las 13:00" in page
    page = propose(c, "/calendario/nueva-cita", title="Llamada", day="2026-11-20", time="01:15")
    assert "a la 01:15" in page


def test_small_a_29_february_birthday_shows_on_hoy_on_the_28th(conn, clock, home):
    from kore.web import today
    store.add_contact(conn, clock, "Luis", "primo", "02-29")
    clock.set(at(9, 0, day=26, month=2).replace(year=2027))
    assert [b["name"] for b in today.for_person(conn, clock, home["carmen"])["birthdays"]] == ["Luis"]
