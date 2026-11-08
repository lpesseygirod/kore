"""The home web page, step W1 (WEB-PLAN.md): logging in with a code Pepe says, Hoy, Dispositivos."""

import re
from datetime import timedelta

import pytest
from starlette.testclient import TestClient

from kore.memory import meds, store
from kore.web import accounts
from kore.web.app import SESSION, create_app

ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def db_path(tmp_path, conn):
    return str(tmp_path / "kore.db")  # the same file as the `conn` fixture


@pytest.fixture
def app(db_path, clock):
    return create_app(db_path, clock, hosts=["testserver"])


def client(app):
    return TestClient(app, follow_redirects=False)


def spoken_code(conn) -> str:
    """What Pepe said, turned back into digits (the test is the person listening)."""
    text = conn.execute("SELECT text FROM say_queue ORDER BY id DESC LIMIT 1").fetchone()["text"]
    words = text.split("el código es: ", 1)[1].split(". Repito")[0].split(", ")
    return "".join(str(accounts.DIGITS.index(w)) for w in words)


def log_in(app, conn, person_id, c=None):
    c = c or client(app)
    c.get("/entrar")
    r = c.post("/entrar/codigo", data={"person": person_id}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    r = c.post("/entrar", data={"code": spoken_code(conn)}, headers=ORIGIN)
    assert r.status_code == 303 and r.headers["location"] == "/"
    return c


# --- logging in ------------------------------------------------------------------------------


def test_the_page_asks_who_you_are_and_pepe_says_a_code(app, conn, home):
    c = client(app)
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/entrar"
    page = c.get("/entrar").text
    assert "Soy Carmen" in page and "Soy Toni" in page
    c.post("/entrar/codigo", data={"person": home["antonio"]}, headers=ORIGIN)
    said = conn.execute("SELECT text FROM say_queue").fetchone()["text"]
    assert said.startswith("Toni, para entrar en la página de Pepe, el código es: ") and "Repito" in said
    assert not re.search(r"\d", said)  # said as words, so the voice reads it clearly


def test_carmen_sees_her_own_day_and_not_tonis(app, conn, clock, home):
    meds.add_medication(conn, clock, home["carmen"], "tensión", ["09:00"], "la pastilla de la tensión")
    meds.add_medication(conn, clock, home["antonio"], "azúcar", ["08:30"], "la del azúcar")
    store.add_to_list(conn, clock, ["pan"])
    c = log_in(app, conn, home["carmen"])
    page = c.get("/").text
    assert "Hola, Carmen" in page and "Pastillas de hoy" in page and "pan" in page
    pills = c.get("/pastillas").text
    assert "la pastilla de la tensión" in pills and "azúcar" not in page + pills
    toni = log_in(app, conn, home["antonio"])
    assert "la del azúcar" in toni.get("/pastillas").text and "tensión" not in toni.get("/pastillas").text


def test_a_code_works_only_in_the_browser_that_asked(app, conn, home):
    asker = client(app)
    asker.get("/entrar")
    asker.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN)
    code = spoken_code(conn)
    other = client(app)
    other.get("/entrar")
    assert other.post("/entrar", data={"code": code}, headers=ORIGIN).status_code == 400
    assert asker.post("/entrar", data={"code": code}, headers=ORIGIN).status_code == 303


def test_three_wrong_tries_burn_the_code(app, conn, home):
    c = client(app)
    c.get("/entrar")
    c.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN)
    code = spoken_code(conn)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(3):
        r = c.post("/entrar", data={"code": wrong}, headers=ORIGIN)
    assert "ya no vale" in r.text
    assert c.post("/entrar", data={"code": code}, headers=ORIGIN).status_code == 400


def test_ten_wrong_tries_lock_that_device_only(app, conn, clock, home):
    bad = TestClient(app, follow_redirects=False, client=("192.168.1.66", 5000))
    bad.get("/entrar")
    for _ in range(10):
        bad.post("/entrar", data={"code": "123456"}, headers=ORIGIN)
    r = bad.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN)
    assert r.status_code == 429 and "demasiados intentos" in r.text
    log_in(app, conn, home["carmen"])  # Carmen's own phone still gets in
    clock.advance(minutes=31)
    assert bad.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN).status_code == 200


def test_many_wrong_tries_across_the_home_pause_codes_and_tell_lea(app, conn, clock, home):
    lea_code = accounts.operator_code(conn, clock)
    for i in range(50):
        c = TestClient(app, follow_redirects=False, client=(f"192.168.1.{i + 10}", 5000))
        c.post("/entrar", data={"code": "123456"}, headers=ORIGIN)
    c = client(app)
    c.get("/entrar")
    assert c.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN).status_code == 429
    told = conn.execute("SELECT text FROM family_out WHERE kind = 'operator'").fetchone()
    assert told and "intentos fallidos" in told["text"]
    lea = client(app)
    lea.get("/entrar")
    assert lea.post("/entrar", data={"code": lea_code}, headers=ORIGIN).status_code == 303  # Lea can still get in
    accounts.unlock(conn, clock)
    assert c.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN).status_code == 200


def test_codes_expire(app, conn, clock, home):
    c = client(app)
    c.get("/entrar")
    c.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN)
    code = spoken_code(conn)
    clock.advance(minutes=11)
    assert c.post("/entrar", data={"code": code}, headers=ORIGIN).status_code == 400


def test_no_codes_said_at_night(app, conn, clock, home):
    clock.set(clock.now().replace(hour=23, minute=30))
    c = client(app)
    c.get("/entrar")
    r = c.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN)
    assert r.status_code == 429 and "De noche" in r.text
    assert conn.execute("SELECT count(*) FROM say_queue").fetchone()[0] == 0


def test_asking_again_at_once_doesnt_make_pepe_repeat(app, conn, home):
    c = client(app)
    c.get("/entrar")
    for _ in range(3):
        c.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN)
    assert conn.execute("SELECT count(*) FROM say_queue").fetchone()[0] == 1


def test_code_limits_per_device_and_home(app, conn, clock, home):
    for i in range(4):  # a new browser each time, same device
        c = TestClient(app, follow_redirects=False, client=("192.168.1.66", 5000))
        c.get("/entrar")
        r = c.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN)
    assert r.status_code == 429 and conn.execute("SELECT count(*) FROM web_attempts WHERE kind = 'code'").fetchone()[0] == 3
    for i in range(8):
        c = TestClient(app, follow_redirects=False, client=(f"192.168.1.{i + 100}", 5000))
        c.get("/entrar")
        r = c.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN)
    assert r.status_code == 429  # 10 an hour for the whole home
    clock.advance(minutes=61)
    assert c.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN).status_code == 200


def test_forms_from_another_site_are_refused(app, conn, home):
    c = client(app)
    c.get("/entrar")
    assert c.post("/entrar/codigo", data={"person": home["carmen"]},
                  headers={"origin": "http://evil.example"}).status_code == 403
    assert c.post("/entrar/codigo", data={"person": home["carmen"]}).status_code == 403  # no origin at all
    assert conn.execute("SELECT count(*) FROM say_queue").fetchone()[0] == 0


def test_other_host_names_are_refused(app):
    assert TestClient(app, base_url="http://attacker.example").get("/entrar").status_code == 400


def test_security_headers_and_cookie_flags(app, conn, home):
    c = client(app)
    r = c.get("/entrar")
    assert "default-src 'none'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
    c.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN)
    r = c.post("/entrar", data={"code": spoken_code(conn)}, headers=ORIGIN)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_only_a_hash_of_the_login_is_kept(app, conn, home):
    c = log_in(app, conn, home["carmen"])
    token = c.cookies[SESSION]
    stored = [r[0] for r in conn.execute("SELECT token_hash FROM web_logins")]
    assert token not in stored and accounts.digest(token) in stored


def test_html_from_the_database_is_escaped(app, conn, clock, home):
    store.add_to_list(conn, clock, ['<script>alert("x")</script>'])
    page = log_in(app, conn, home["carmen"]).get("/").text
    assert "<script>" not in page and "&lt;script&gt;" in page


# --- devices -------------------------------------------------------------------------------


def test_devices_close_one_and_log_out(app, conn, home):
    phone = log_in(app, conn, home["carmen"])
    tablet = log_in(app, conn, home["carmen"])
    page = phone.get("/dispositivos").text
    other = [int(x) for x in re.findall(r'name="login" value="(\d+)"', page)]
    assert len(other) == 1  # the tablet; this phone is "este"
    phone.post("/dispositivos/cerrar", data={"login": other[0]}, headers=ORIGIN)
    assert tablet.get("/").status_code == 303  # the tablet is logged out
    phone.post("/salir", headers=ORIGIN)
    assert phone.get("/").status_code == 303


def test_you_cant_close_someone_elses_login(app, conn, home):
    carmen = log_in(app, conn, home["carmen"])
    toni = log_in(app, conn, home["antonio"])
    tonis = conn.execute("SELECT l.id FROM web_logins l JOIN web_accounts a ON a.id = l.account_id "
                         "WHERE a.person_id = ?", (home["antonio"],)).fetchone()[0]
    carmen.post("/dispositivos/cerrar", data={"login": tonis}, headers=ORIGIN)
    assert toni.get("/").status_code == 200


def test_an_unused_phone_is_logged_out_after_six_months(app, conn, clock, home):
    c = log_in(app, conn, home["carmen"])
    clock.advance(days=181)
    assert c.get("/").status_code == 303


# --- Lea -----------------------------------------------------------------------------------


def test_lea_logs_in_with_a_printed_code_and_sees_status_only(app, conn, clock, home):
    meds.add_medication(conn, clock, home["carmen"], "tensión", ["09:00"], "la pastilla de la tensión")
    code = accounts.operator_code(conn, clock)
    c = client(app)
    c.get("/entrar")
    assert c.post("/entrar", data={"code": code}, headers=ORIGIN).status_code == 303
    page = c.get("/").text
    assert "Estado de Pepe" in page and "Personas en casa" in page and "tensión" not in page
    assert conn.execute("SELECT count(*) FROM say_queue").fetchone()[0] == 0  # never spoken


def test_lea_sees_and_closes_every_login(app, conn, clock, home):
    carmen = log_in(app, conn, home["carmen"])
    lea = client(app)
    lea.get("/entrar")
    lea.post("/entrar", data={"code": accounts.operator_code(conn, clock)}, headers=ORIGIN)
    page = lea.get("/dispositivos").text
    assert "Carmen" in page
    ids = [int(x) for x in re.findall(r'name="login" value="(\d+)"', page)]
    for i in ids:
        lea.post("/dispositivos/cerrar", data={"login": i}, headers=ORIGIN)
    assert carmen.get("/").status_code == 303


def test_close_all(conn, clock, home, app):
    c = log_in(app, conn, home["carmen"])
    assert accounts.close_all(conn, clock) == 1
    assert c.get("/").status_code == 303


def test_a_deleted_person_cant_log_in_with_an_old_cookie(app, conn, home):
    c = log_in(app, conn, home["carmen"])
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM people WHERE id = ?", (home["carmen"],))
    assert c.get("/").status_code == 303


def test_the_qr_code(tmp_path, monkeypatch):
    from kore.web.__main__ import main
    monkeypatch.chdir(tmp_path)
    assert main(["--db", str(tmp_path / "k.db"), "qr", "ftp://x"]) == 2
    assert main(["--db", str(tmp_path / "k.db"), "qr", "http://pepe.local:8080/"]) == 0
    assert (tmp_path / "data/web/qr.svg").read_text().startswith("<?xml")


def test_serving_beyond_this_computer_needs_a_name():
    from kore.web.__main__ import main
    assert main(["serve", "--host", "0.0.0.0"]) == 2


# --- the security review of 2026-11-08 --------------------------------------------------------


@pytest.mark.parametrize("why", ["quiet", "visitors", "rest", "clock"])
def test_r3_pepe_doesnt_say_codes_when_he_shouldnt_speak(app, conn, clock, home, why):
    from kore.manners import modes
    from kore.memory import settings
    if why == "quiet":
        modes.start(conn, clock, "quiet")
    elif why == "visitors":
        modes.start(conn, clock, "visitors")
    elif why == "rest":
        settings.put(conn, clock, "rest_hours", "07:00-09:00")
    else:
        clock.set_trusted(False)
    c = client(app)
    c.get("/entrar")
    assert c.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN).status_code == 429
    assert conn.execute("SELECT count(*) FROM say_queue").fetchone()[0] == 0


def test_r2_a_huge_body_is_refused_before_it_is_read(app, home):
    c = client(app)
    r = c.post("/entrar", content=b"code=" + b"1" * 10_000, headers={**ORIGIN, "content-type":
                                                                    "application/x-www-form-urlencoded"})
    assert r.status_code == 403


def test_r4_each_open_code_counts_each_wrong_try(app, conn, home):
    c = client(app)
    c.get("/entrar")
    c.post("/entrar/codigo", data={"person": home["carmen"]}, headers=ORIGIN)
    first = spoken_code(conn)
    c.post("/entrar/codigo", data={"person": home["antonio"]}, headers=ORIGIN)  # a second code cancels the first
    assert c.post("/entrar", data={"code": first}, headers=ORIGIN).status_code == 400
    assert conn.execute("SELECT count(*) FROM web_codes WHERE status = 'open'").fetchone()[0] == 1


def test_r4_strangers_cant_burn_leas_code(app, conn, clock, home):
    lea_code = accounts.operator_code(conn, clock)
    for i in range(5):
        c = TestClient(app, follow_redirects=False, client=(f"192.168.1.{i + 10}", 5000))
        c.post("/entrar", data={"code": "123456" if lea_code != "123456" else "654321"}, headers=ORIGIN)
    lea = client(app)
    lea.get("/entrar")
    assert lea.post("/entrar", data={"code": lea_code}, headers=ORIGIN).status_code == 303


def test_r5_huge_numbers_dont_crash(app, conn, home):
    c = log_in(app, conn, home["carmen"])
    assert c.post("/dispositivos/cerrar", data={"login": "9" * 30}, headers=ORIGIN).status_code == 303
    other = client(app)
    other.get("/entrar")
    assert other.post("/entrar/codigo", data={"person": "9" * 30}, headers=ORIGIN).status_code == 303


def test_r6_another_site_cant_log_you_out(app, conn, home):
    c = log_in(app, conn, home["carmen"])
    r = c.post("/salir", headers={"origin": "http://evil.example"})
    assert r.status_code == 403 and "set-cookie" not in r.headers
    assert c.get("/").status_code == 200


def test_r7_the_spoken_code_doesnt_stay_in_the_database(app, conn, clock, home):
    log_in(app, conn, home["carmen"])
    assert conn.execute("SELECT count(*) FROM say_queue").fetchone()[0] == 0  # used: its line is gone
    c = client(app)
    c.get("/entrar")
    c.post("/entrar/codigo", data={"person": home["antonio"]}, headers=ORIGIN)
    assert conn.execute("SELECT count(*) FROM say_queue").fetchone()[0] == 1
    clock.advance(minutes=11)
    c.post("/entrar", data={"code": "000000"}, headers=ORIGIN)  # any later request tidies up
    assert conn.execute("SELECT count(*) FROM say_queue").fetchone()[0] == 0


def test_r8_only_the_boxs_own_names(db_path, clock):
    app = create_app(db_path, clock)
    assert TestClient(app).get("/entrar").status_code == 400  # "testserver" isn't allowed outside the tests
    assert TestClient(app, base_url="http://localhost").get("/entrar").status_code == 200
