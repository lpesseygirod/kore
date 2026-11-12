"""My console (design: Console 1–7): I see nothing private without the person's yes."""

import json
import re

import pytest
from starlette.testclient import TestClient

from kore.brain.clients import ScriptedClient, text, tool_use
from kore.brain.conversation import Brain
from kore.brain import tools
from kore.brain.session import Person, Session
from kore.memory import meds, settings, store
from kore.memory.db import connect, utc_iso
from kore.web import accounts
from kore.web import chat as chats
from kore.web.app import create_app
from tests.conftest import at
from tests.test_web import ORIGIN, log_in


@pytest.fixture
def db_path(tmp_path, conn):
    return str(tmp_path / "kore.db")


@pytest.fixture
def script():
    from tests.test_chat import Script
    return Script()


@pytest.fixture
def worker(db_path, clock, script):
    w = chats.ChatWorker(lambda: connect(db_path), clock, script.client)
    yield w
    w.shutdown()


@pytest.fixture
def app(db_path, clock, worker):
    return create_app(db_path, clock, hosts=["testserver"], chat=worker, dev=True)


@pytest.fixture
def lea(app, conn, clock):
    c = TestClient(app, follow_redirects=False)
    c.get("/entrar")
    assert c.post("/entrar", data={"code": accounts.operator_code(conn, clock)}, headers=ORIGIN).status_code == 303
    return c


@pytest.fixture
def day(conn, clock, home):
    meds.add_medication(conn, clock, home["carmen"], "sintrom", ["09:00", "21:00"], "la rosa")
    meds.add_medication(conn, clock, home["antonio"], "metformina", ["08:30"])
    store.add_appointment(conn, clock, home["carmen"], "Ginecólogo", at(11, 0, day=10))
    store.add_appointment(conn, clock, home["antonio"], "Urólogo", at(12, 0, day=10))
    store.add_note(conn, clock, home["carmen"], "interest", "le encanta leer novelas")
    store.add_note(conn, clock, home["antonio"], "story", "fue carpintero 40 años")
    store.add_to_list(conn, clock, ["panecillos"])
    return home


def everything(lea) -> str:
    return "".join(lea.get(p).text for p in ("/", "/console/calendar", "/console/chat", "/console/feedback",
                                             "/console/people", "/console/device", "/console/settings"))


def test_without_their_yes_lea_sees_only_the_box(lea, day):
    page = everything(lea)
    for private in ("sintrom", "la rosa", "metformina", "Ginecólogo", "Urólogo", "novelas", "carpintero", "panecillos"):
        assert private not in page, private
    assert "Not shared" in page and "hasn’t agreed" in page and "Not started" in page


def test_with_carmens_yes_lea_sees_carmens_day_only(lea, conn, clock, day):
    store.set_consent(conn, clock, day["carmen"], "lea_sees_day", True)
    page = everything(lea)
    for seen in ("sintrom", "Ginecólogo", "novelas"):
        assert seen in page, seen
    for private in ("metformina", "Urólogo", "carpintero"):
        assert private not in page, private
    assert "panecillos" not in lea.get("/console/people").text  # shared: only when both agreed
    store.set_consent(conn, clock, day["antonio"], "lea_sees_day", True)
    assert "panecillos" in lea.get("/console/people").text and "Urólogo" in lea.get("/console/calendar?week=2026-11-09").text


def test_the_week_grid_moves_by_weeks(lea, conn, clock, day):
    store.set_consent(conn, clock, day["carmen"], "lea_sees_day", True)
    page = lea.get("/console/calendar").text
    assert "MON 9 · TODAY" in page and "Ginecólogo" in page
    assert "Ginecólogo" not in lea.get("/console/calendar?week=2026-11-16").text
    assert lea.get("/console/calendar?week=not-a-date").status_code == 200


def test_conversations_only_with_their_yes_and_only_7_days(lea, conn, clock, day):
    chats.add(conn, clock, day["carmen"], "person", "chat", "Hola Pepe, ¿qué tal?")
    chats.add(conn, clock, day["carmen"], "pepe", "chat", "Muy bien, Carmen.")
    chats.add(conn, clock, day["antonio"], "person", "chat", "Pepe, cuéntame el fútbol")
    assert "qué tal" not in lea.get("/console/chat").text
    store.set_consent(conn, clock, day["carmen"], "lea_reads_conversations", True)
    page = lea.get("/console/chat").text
    assert "Hola Pepe" in page and "fútbol" not in page
    first = conn.execute("SELECT min(id) FROM chat WHERE person_id = ?", (day["carmen"],)).fetchone()[0]
    page = lea.get(f"/console/chat?p={day['carmen']}&c={first}").text
    assert "Muy bien, Carmen." in page
    clock.advance(days=8)
    assert "Hola Pepe" not in lea.get("/console/chat").text


def test_lea_marks_saves_a_draft_out_of_git_and_deletes(lea, conn, clock, day, db_path, tmp_path):
    chats.add(conn, clock, day["carmen"], "person", "chat", "No me acuerdo de la pastilla")
    store.set_consent(conn, clock, day["carmen"], "lea_reads_conversations", True)
    first = conn.execute("SELECT min(id) FROM chat").fetchone()[0]
    ref = {"p": str(day["carmen"]), "c": str(first)}
    lea.post("/console/chat/mark", data={**ref, "note": "answered too fast"}, headers=ORIGIN)
    assert "Marked: answered too fast" in lea.get(f"/console/chat?p={day['carmen']}&c={first}").text
    r = lea.post("/console/chat/scenario", data=ref, headers=ORIGIN)
    assert "private/scenario_drafts/" in r.text
    drafts = list((tmp_path.parent / "private" / "scenario_drafts").glob("draft-*.json"))
    assert drafts and "pastilla" in drafts[-1].read_text()
    lea.post("/console/chat/delete", data=ref, headers=ORIGIN)
    assert conn.execute("SELECT count(*) FROM chat").fetchone()[0] == 0
    # Toni's conversation can't be touched without his yes, even by id.
    chats.add(conn, clock, day["antonio"], "person", "chat", "secreto")
    tid = conn.execute("SELECT id FROM chat WHERE person_id = ?", (day["antonio"],)).fetchone()[0]
    r = lea.post("/console/chat/delete", data={"p": str(day["antonio"]), "c": str(tid)}, headers=ORIGIN)
    assert r.status_code == 400 and conn.execute("SELECT count(*) FROM chat").fetchone()[0] == 1


def test_leas_medicine_change_is_only_a_proposal_pepe_asks_about(lea, app, conn, clock, day):
    store.set_consent(conn, clock, day["carmen"], "lea_sees_day", True)
    mid = meds.medications(conn, day["carmen"])[0]["id"]
    r = lea.post("/console/calendar/medicine", data={"id": str(mid), "times": "08:00, 20:00"}, headers=ORIGIN)
    assert "nothing changes until they say yes" in r.text
    assert json.loads(meds.medications(conn, day["carmen"])[0]["times"]) == ["09:00", "21:00"]
    carmen = log_in(app, conn, day["carmen"])
    assert "Lea propone tomar sintrom a las 08:00, 20:00" in carmen.get("/").text
    s = Session([Person(day["carmen"], "Carmen", "Carmen")], fixed_speaker=day["carmen"])
    state = Brain(tools.Context(conn, clock, s), ScriptedClient(lambda r: text(""))).state()
    assert "Lea (who looks after this KORE) proposed changing sintrom to 08:00, 20:00" in state
    meds.change_medication_times(conn, clock, mid, ["08:00", "20:00"])  # what her yes to Pepe does
    assert "accepted" in lea.get("/console/calendar").text


def test_lea_cant_propose_for_someone_who_didnt_agree(lea, conn, clock, day):
    mid = meds.medications(conn, day["antonio"])[0]["id"]
    r = lea.post("/console/calendar/medicine", data={"id": str(mid), "times": "08:00"}, headers=ORIGIN)
    assert r.status_code == 400
    r = lea.post("/console/calendar/add", data={"for": str(day["antonio"]), "title": "X", "day": "2026-11-20",
                                                 "time": "10:00"}, headers=ORIGIN)
    assert r.status_code == 400
    assert conn.execute("SELECT count(*) FROM operator_proposals").fetchone()[0] == 0


def test_leas_appointment_is_saved_only_with_the_persons_yes(lea, app, conn, clock, day):
    store.set_consent(conn, clock, day["carmen"], "lea_sees_day", True)
    lea.post("/console/calendar/add", data={"for": str(day["carmen"]), "title": "Podólogo", "day": "2026-11-20",
                                            "time": "10:00"}, headers=ORIGIN)
    carmen = log_in(app, conn, day["carmen"])
    assert "Lea le propone una cita" in carmen.get("/").text
    pid = conn.execute("SELECT id FROM operator_proposals").fetchone()[0]
    toni = log_in(app, conn, day["antonio"])
    assert toni.post("/calendario/propuesta", data={"id": f"lea-{pid}"}, headers=ORIGIN).status_code == 400
    page = carmen.post("/calendario/propuesta", data={"id": f"lea-{pid}"}, headers=ORIGIN).text
    assert "Cita propuesta por Lea: «Podólogo»" in page
    assert conn.execute("SELECT count(*) FROM appointments WHERE title = 'Podólogo'").fetchone()[0] == 0
    yes_id = re.search(r'action="/confirmar"><input type="hidden" name="id" value="(\d+)"', page)[1]
    carmen.post("/confirmar", data={"id": yes_id}, headers=ORIGIN)
    assert conn.execute("SELECT person_id FROM appointments WHERE title = 'Podólogo'").fetchone()[0] == day["carmen"]
    assert conn.execute("SELECT status FROM operator_proposals").fetchone()[0] == "accepted"


def _feedback(conn, clock, pid, status="active", kind="rule", review=0, said="Habla más despacio"):
    return conn.execute(
        "INSERT INTO feedback (at_utc, speaker_id, person_id, said, understood, kind, rule_text, scope, status, "
        "review_first) VALUES (?, ?, ?, ?, 'speak slower', ?, 'Speak slowly.', 'always', ?, ?)",
        (utc_iso(clock.now()), pid, pid, said, kind, status, review)).lastrowid


def test_feedback_safety_always_reaches_lea_the_rest_with_their_yes(lea, conn, clock, day):
    _feedback(conn, clock, day["antonio"], said="Habla más despacio")
    _feedback(conn, clock, day["carmen"], status="held_safety", kind="safety", review=1,
              said="Deja de recordarme la pastilla blanca")
    page = lea.get("/console/feedback").text
    assert "Deja de recordarme la pastilla blanca" in page and "Needs you" in page
    assert "Habla más despacio" not in page
    assert "1 safety item" in lea.get("/").text


def test_lea_undoes_and_rewords_feedback(lea, conn, clock, day):
    store.set_consent(conn, clock, day["antonio"], "lea_sees_day", True)
    fid = _feedback(conn, clock, day["antonio"])
    r = lea.post("/console/feedback/reword", data={"id": str(fid), "text": "Speak a little slower."}, headers=ORIGIN)
    assert "Reworded" in r.text
    r = lea.post("/console/feedback/undo", data={"id": str(fid), "misunderstood": "1"}, headers=ORIGIN)
    assert "Undone" in r.text
    row = conn.execute("SELECT status, misunderstood, rule_text FROM feedback WHERE id = ?", (fid,)).fetchone()
    assert (row["status"], row["misunderstood"], row["rule_text"]) == ("undone", 1, "Speak a little slower.")
    other = _feedback(conn, clock, day["carmen"])  # Carmen didn't agree: not mine to undo
    assert lea.post("/console/feedback/undo", data={"id": str(other)}, headers=ORIGIN).status_code == 400


def test_lea_forgets_a_note_only_after_confirming(lea, conn, clock, day):
    store.set_consent(conn, clock, day["carmen"], "lea_sees_day", True)
    nid = store.notes(conn, day["carmen"])[0]["id"]
    page = lea.post("/console/people/forget", data={"person": str(day["carmen"]), "id": str(nid)}, headers=ORIGIN).text
    assert "Forget this for good?" in page and store.notes(conn, day["carmen"])
    lea.post("/console/people/forget", data={"person": str(day["carmen"]), "id": str(nid), "sure": "1"}, headers=ORIGIN)
    assert store.notes(conn, day["carmen"]) == []
    toni_note = store.notes(conn, day["antonio"])[0]["id"]
    lea.post("/console/people/forget", data={"person": str(day["antonio"]), "id": str(toni_note), "sure": "1"},
             headers=ORIGIN)
    assert store.notes(conn, day["antonio"])  # no yes from Toni: untouched


def test_quiet_times_are_checked(lea, conn):
    assert lea.post("/console/settings/quiet", data={"start": "23:00", "end": "08:30"}, headers=ORIGIN).status_code == 200
    assert settings.get(conn, "night_start") == "23:00"
    assert lea.post("/console/settings/quiet", data={"start": "12:00", "end": "13:00"}, headers=ORIGIN).status_code == 400
    assert settings.get(conn, "night_start") == "23:00"


def test_the_test_chat_never_touches_the_real_home(lea, conn, clock, day, script):
    store.set_consent(conn, clock, day["carmen"], "lea_sees_day", True)
    r = lea.post("/console/chat/test", data={"as": str(day["carmen"]), "text": "hola"}, headers=ORIGIN)
    assert r.status_code == 400  # the copy holds the whole home: Toni must have agreed too
    store.set_consent(conn, clock, day["antonio"], "lea_sees_day", True)
    script.then(tool_use("add_to_list", {"items": ["leche"]}), text("Apuntada la leche."))
    r = lea.post("/console/chat/test", data={"as": str(day["carmen"]), "text": "Apunta leche"}, headers=ORIGIN)
    assert r.status_code == 303
    page = lea.get("/console/chat").text
    assert "Apunta leche" in page and "Apuntada la leche." in page
    assert store.list_items(conn) == ["panecillos"]  # the real list is untouched


def test_only_lea_gets_the_console_and_only_from_its_own_pages(app, lea, conn, clock, day):
    carmen = log_in(app, conn, day["carmen"])
    for path in ("/console/calendar", "/console/chat", "/console/feedback", "/console/people", "/console/device",
                 "/console/settings"):
        assert carmen.get(path).status_code == 303
        assert TestClient(app, follow_redirects=False).get(path).status_code == 303
    r = lea.post("/console/settings/quiet", data={"start": "23:00", "end": "08:30"})  # no Origin
    assert r.status_code == 403


# --- edge cases ------------------------------------------------------------


def test_r2_settings_hide_contacts_of_someone_who_didnt_agree(lea, conn, clock, day):
    store.add_contact(conn, clock, "Pedro Secreto", "amigo", help_contact=True, for_person=day["antonio"])
    assert "Pedro Secreto" not in lea.get("/console/settings").text
    store.set_consent(conn, clock, day["antonio"], "lea_sees_day", True)
    assert "Pedro Secreto" in lea.get("/console/settings").text


def test_r3_message_counts_only_with_their_yes(lea, conn, clock, day):
    for _ in range(3):
        chats.add(conn, clock, day["antonio"], "person", "chat", "hola")
    page = lea.get("/").text
    assert "3 today" not in page and "Not shared" in page


def test_r4_lea_marks_go_with_the_conversation(lea, conn, clock, day, app):
    chats.add(conn, clock, day["carmen"], "person", "chat", "me duele el pecho")
    store.set_consent(conn, clock, day["carmen"], "lea_reads_conversations", True)
    first = conn.execute("SELECT min(id) FROM chat").fetchone()[0]
    ref = {"p": str(day["carmen"]), "c": str(first)}
    lea.post("/console/chat/mark", data={**ref, "note": "said: me duele el pecho"}, headers=ORIGIN)
    lea.post("/console/chat/delete", data=ref, headers=ORIGIN)
    assert conn.execute("SELECT count(*) FROM console_marks").fetchone()[0] == 0
    chats.add(conn, clock, day["carmen"], "person", "chat", "otra")
    first = conn.execute("SELECT min(id) FROM chat").fetchone()[0]
    lea.post("/console/chat/mark", data={"p": str(day["carmen"]), "c": str(first), "note": "x"}, headers=ORIGIN)
    store.set_consent(conn, clock, day["carmen"], "lea_reads_conversations", False)
    assert conn.execute("SELECT count(*) FROM console_marks").fetchone()[0] == 0


def test_r7_odd_weeks_dont_crash(lea):
    for week in ("9999-12-31", "0001-01-01", "2026-13-01"):
        assert lea.get(f"/console/calendar?week={week}").status_code == 200


def test_r8_a_no_stops_pepe_asking_and_identical_times_are_refused(lea, app, conn, clock, day):
    store.set_consent(conn, clock, day["carmen"], "lea_sees_day", True)
    mid = meds.medications(conn, day["carmen"])[0]["id"]
    assert lea.post("/console/calendar/medicine", data={"id": str(mid), "times": "09:00, 21:00"},
                    headers=ORIGIN).status_code == 400
    lea.post("/console/calendar/medicine", data={"id": str(mid), "times": "08:00"}, headers=ORIGIN)
    pid = conn.execute("SELECT id FROM operator_proposals").fetchone()[0]
    s = Session([Person(day["carmen"], "Carmen", "Carmen")], fixed_speaker=day["carmen"])
    ctx = tools.Context(conn, clock, s)
    s.begin_turn("No, prefiero preguntar a mi médico")
    assert tools.call(ctx, "decline_lea_proposal", {"proposal_id": pid})["ok"]
    assert "proposed changing" not in Brain(ctx, ScriptedClient(lambda r: text(""))).state()
    lea.post("/console/calendar/medicine", data={"id": str(mid), "times": "08:30"}, headers=ORIGIN)
    carmen = log_in(app, conn, day["carmen"])
    pid2 = conn.execute("SELECT max(id) FROM operator_proposals").fetchone()[0]
    carmen.post("/propuesta-lea/no", data={"id": str(pid2)}, headers=ORIGIN)
    assert "Lea propone" not in carmen.get("/").text
    toni = log_in(app, conn, day["antonio"])
    s2 = Session([Person(day["antonio"], "Antonio", "Toni")], fixed_speaker=day["antonio"])
    s2.begin_turn("no")
    lea.post("/console/calendar/medicine", data={"id": str(mid), "times": "07:00"}, headers=ORIGIN)
    pid3 = conn.execute("SELECT max(id) FROM operator_proposals").fetchone()[0]
    r = tools.call(tools.Context(conn, clock, s2), "decline_lea_proposal", {"proposal_id": pid3})
    assert r["error"] == "speaker_is_someone_else"
    toni.post("/propuesta-lea/no", data={"id": str(pid3)}, headers=ORIGIN)
    assert conn.execute("SELECT status FROM operator_proposals WHERE id = ?", (pid3,)).fetchone()[0] == "open"
