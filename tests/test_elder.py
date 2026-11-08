"""Carmen and Antonio's app, from Lea's design: Inicio, Pepe, Agenda, Pastillas, Familia, Ayuda, Ajustes."""

import re

import pytest
from starlette.testclient import TestClient

from kore.family import messages as family_msgs
from kore.family.channels import FakeChannel
from kore.memory import meds, settings, store
from kore.web.app import create_app
from tests.conftest import at
from tests.test_web import ORIGIN, log_in


@pytest.fixture
def db_path(tmp_path, conn):
    return str(tmp_path / "kore.db")


@pytest.fixture
def tg():
    return FakeChannel()


@pytest.fixture
def app(db_path, clock, tg):
    return create_app(db_path, clock, hosts=["testserver"], family_links={"telegram": tg})


@pytest.fixture
def ana(conn, clock, home):
    cid = store.add_contact(conn, clock, "Ana", "hija", help_contact=True, for_person=home["carmen"])
    conn.execute("UPDATE contacts SET telegram_chat_id = '555', phone = '+34600111222' WHERE id = ?", (cid,))
    return cid


def pending_id(page):
    return re.search(r'action="/confirmar"><input type="hidden" name="id" value="(\d+)"', page)[1]


# --- pills ---------------------------------------------------------------------------------------


def test_the_pill_button_records_only_the_open_dose_and_never_twice(app, conn, clock, home):
    mid = meds.add_medication(conn, clock, home["carmen"], "tensión", ["09:00", "21:30"], "la blanca")
    clock.set(at(9, 5))
    c = log_in(app, conn, home["carmen"])
    page = c.get("/pastillas").text
    assert page.count("Ya me la he tomado") == 1 and "Pendiente" in page and "No tome otra" in page
    r = c.post("/pastillas/tomada", data={"id": str(mid)}, headers=ORIGIN)
    assert "Apuntada" in r.text and "Tomada a las 09:05" in r.text and "Ya me la he tomado" not in r.text
    r = c.post("/pastillas/tomada", data={"id": str(mid)}, headers=ORIGIN)
    assert "ya estaba apuntada" in r.text
    assert conn.execute("SELECT count(*) FROM doses WHERE status = 'taken'").fetchone()[0] == 1
    assert "1 de 2 tomadas" in c.get("/").text and "la próxima a las 21:30" in c.get("/").text


def test_toni_cant_record_carmens_pill(app, conn, clock, home):
    mid = meds.add_medication(conn, clock, home["carmen"], "tensión", ["09:00"])
    clock.set(at(9, 5))
    toni = log_in(app, conn, home["antonio"])
    r = toni.post("/pastillas/tomada", data={"id": str(mid)}, headers=ORIGIN)
    assert "No se ha encontrado" in r.text and conn.execute("SELECT count(*) FROM doses").fetchone()[0] == 0
    assert toni.post("/pastillas/tomada", data={"id": str(mid)}).status_code == 403  # and not from another site


def test_with_an_unreliable_clock_no_pill_buttons(app, conn, clock, home):
    meds.add_medication(conn, clock, home["carmen"], "tensión", ["08:30"])
    c = log_in(app, conn, home["carmen"])
    clock.set_trusted(False)
    page = c.get("/pastillas").text
    assert "no es fiable" in page and "Ya me la he tomado" not in page


# --- help ----------------------------------------------------------------------------------------


def test_pedir_ayuda_sends_on_the_yes_even_without_the_agreed_path(app, conn, clock, home, ana, tg):
    c = log_in(app, conn, home["carmen"])
    page = c.get("/ayuda").text
    assert "Sí, avisar a Ana" in page and "No, ha sido sin querer" in page and 'href="tel:112"' in page
    assert tg.sent == []  # opening the page sends nothing
    r = c.post("/ayuda", headers=ORIGIN)
    assert len(tg.sent) == 1 and "Ya he avisado a Ana" in r.text
    assert "AYUDA: Carmen" in tg.sent[0][1]
    r = c.post("/ayuda", headers=ORIGIN)  # pressed again: not sent twice
    assert len(tg.sent) == 1 and "hace un momento" in r.text


def test_help_cant_be_sent_from_another_site(app, conn, home, ana, tg):
    c = log_in(app, conn, home["carmen"])
    assert c.post("/ayuda").status_code == 403
    assert c.post("/ayuda", headers={"origin": "http://evil.example"}).status_code == 403
    assert tg.sent == []


def test_with_no_one_to_tell_the_page_says_so(app, conn, home):
    page = log_in(app, conn, home["antonio"]).get("/ayuda").text
    assert "no hay nadie apuntado" in page and "112" in page


# --- family ----------------------------------------------------------------------------------------


def test_a_family_message_shows_and_a_reply_goes_after_the_yes(app, conn, clock, home, ana, tg):
    conn.execute("INSERT INTO family_in (received_at_utc, contact_id, text) VALUES ('2026-11-09T07:00:00+00:00', ?, "
                 "'¡Mamá! Mañana os llamo.')", (ana,))
    c = log_in(app, conn, home["carmen"])
    assert "1 mensaje de la familia" in c.get("/").text
    page = c.get("/mi-familia").text
    assert "Mensaje nuevo de Ana" in page and "Mañana os llamo" in page
    assert 'href="tel:+34600111222"' in page and "https://wa.me/34600111222" in page
    assert family_msgs.unread(conn, home["carmen"]) == []  # seen on the page: Pepe won't announce it again
    page = c.post("/mi-familia/responder", data={"contact": str(ana), "text": "Vale, hija. Un beso."},
                  headers=ORIGIN).text
    assert "Le mando a Ana: «Vale, hija. Un beso.»" in page and "Sí, envíalo" in page
    assert tg.sent == []
    r = c.post("/confirmar", data={"id": pending_id(page)}, headers=ORIGIN)
    assert r.headers["location"] == "/mi-familia?hecho=mandado"
    assert tg.sent and "Carmen (con Pepe): Vale, hija. Un beso." in tg.sent[0][1]


def test_toni_cant_write_as_carmen_to_her_contact(app, conn, home, ana, tg):
    toni = log_in(app, conn, home["antonio"])
    assert "Ana" not in toni.get("/mi-familia").text
    r = toni.post("/mi-familia/responder", data={"contact": str(ana), "text": "hola"}, headers=ORIGIN)
    assert r.status_code == 400 and tg.sent == []


def test_a_reply_to_a_family_member_with_only_the_family_app_reaches_her_page(app, conn, clock, home):
    from tests.test_w4 import family_login, give_access
    marcos = store.add_contact(conn, clock, "Marcos", "nieto", for_person=home["carmen"])
    carmen = log_in(app, conn, home["carmen"])
    m = family_login(app, give_access(app, conn, carmen, marcos))
    page = carmen.post("/mi-familia/responder", data={"contact": str(marcos), "text": "¿Vienes el domingo?"},
                       headers=ORIGIN).text
    carmen.post("/confirmar", data={"id": pending_id(page)}, headers=ORIGIN)
    assert "¿Vienes el domingo?" in m.get("/").text


# --- agenda, settings ------------------------------------------------------------------------------------


def test_agenda_shows_four_days(app, conn, clock, home):
    store.add_appointment(conn, clock, home["carmen"], "Dentista", at(10, 0, day=10))
    store.add_contact(conn, clock, "Lucía", "nieta", "11-11")
    c = log_in(app, conn, home["carmen"])
    page = c.get("/agenda").text
    assert ">Hoy<br>9<" in page and ">Mar<br>10<" in page and "Dentista" not in page
    assert "Dentista" in c.get("/agenda?dia=1").text
    assert "Cumpleaños de Lucía" in c.get("/agenda?dia=2").text
    assert c.get("/agenda?dia=99999999999").status_code == 200


def test_text_size_and_tu_follow_the_person(app, conn, clock, home):
    settings.put(conn, clock, "address", "tu", home["carmen"])
    c = log_in(app, conn, home["carmen"])
    c.post("/ajustes/letra", data={"size": "enorme"}, headers=ORIGIN)
    page = c.get("/ayuda").text
    assert 'class="size-enorme"' in page and "¿Necesitas ayuda, Carmen?" in page
    toni = log_in(app, conn, home["antonio"]).get("/ayuda").text
    assert 'class="size-grande"' in toni and "¿Necesita ayuda, Toni?" in toni
    c.post("/ajustes/letra", data={"size": "<script>"}, headers=ORIGIN)
    assert settings.get(conn, "text_size", home["carmen"]) == "enorme"


def test_who_sees_what(app, conn, clock, home):
    store.set_consent(conn, clock, home["carmen"], "lea_sees_day", True)
    page = log_in(app, conn, home["carmen"]).get("/lo-que-ven").text
    assert "Lea, que cuida de que Pepe funcione" in page and page.count(">Sí<") == 1


def test_lea_and_family_get_none_of_these(app, conn, clock, home):
    from kore.web import accounts
    lea = TestClient(app, follow_redirects=False)
    lea.get("/entrar")
    lea.post("/entrar", data={"code": accounts.operator_code(conn, clock)}, headers=ORIGIN)
    for path in ("/agenda", "/pastillas", "/mi-familia", "/ayuda", "/ajustes", "/lo-que-ven", "/fotos"):
        assert lea.get(path).status_code == 303, path
