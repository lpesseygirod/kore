"""The box's self-test: it gates every night update, so it must fail when something is really wrong."""

import contextlib
import http.server
import shutil
import threading
from collections import namedtuple
from datetime import datetime

import pytest

from kore import selftest
from kore.memory import meds
from kore.memory.db import connect
from kore.scheduler.clock import HOME_TZ, FakeClock


@pytest.fixture
def db(tmp_path, clock):
    path = tmp_path / "kore.db"
    conn = connect(path)
    pid = meds.add_person(conn, clock, "Carmen")
    meds.add_medication(conn, clock, pid, "x", ["09:00"])
    conn.close()
    return path


def test_a_good_database_passes(db):
    r = selftest.check_database(db)
    assert r.status == "ok" and "1 people" in r.detail and "1 medicines" in r.detail


def test_the_check_never_writes_to_the_live_file(db):
    before = db.read_bytes()
    selftest.check_database(db)
    assert db.read_bytes() == before


def test_a_missing_database_fails(tmp_path):
    assert selftest.check_database(tmp_path / "nope.db").failed


def test_a_corrupt_database_fails(db, tmp_path):
    bad = tmp_path / "bad.db"
    data = bytearray(db.read_bytes())
    for i in range(4096, len(data), 7):  # trample every page after the first
        data[i] = 0xA5
    bad.write_bytes(bytes(data))
    assert selftest.check_database(bad).failed


def test_a_database_without_the_dose_log_fails(db):
    import sqlite3
    c = sqlite3.connect(db)
    c.execute("PRAGMA foreign_keys = OFF")
    c.execute("DROP TABLE doses")
    c.commit()
    c.close()
    r = selftest.check_database(db)
    assert r.failed and "doses" in r.detail


def test_a_database_from_a_newer_version_warns(db):
    import sqlite3
    c = sqlite3.connect(db)
    c.execute("PRAGMA user_version = 999")
    c.commit()
    c.close()
    assert selftest.check_database(db).status == "warn"


def test_clock_trusted_untrusted_and_reset():
    good = FakeClock(datetime(2026, 11, 9, 8, 0, tzinfo=HOME_TZ))
    assert selftest.check_clock((True, "set from the internet"), lambda s: good).status == "ok"
    rtc = selftest.check_clock((True, "from the RTC battery (no internet time yet)"), lambda s: good)
    assert rtc.status == "warn" and not rtc.failed
    untrusted = FakeClock(datetime(2026, 11, 9, 8, 0, tzinfo=HOME_TZ), trusted=False)
    assert selftest.check_clock((False, "no internet time and no RTC battery"), lambda s: untrusted).failed
    reset = FakeClock(datetime(2000, 1, 1, tzinfo=HOME_TZ))
    r = selftest.check_clock((True, "set from the internet"), lambda s: reset)
    assert r.failed and "reset" in r.detail


def test_clock_sync_probe_on_linux(tmp_path):
    no_rtc = tmp_path / "rtc0"
    assert selftest.clock_synced(lambda cmd: (0, "yes\n"), "linux", no_rtc) == (True, "set from the internet")
    assert selftest.clock_synced(lambda cmd: (0, "no\n"), "linux", no_rtc)[0] is False
    no_rtc.mkdir()
    assert selftest.clock_synced(lambda cmd: (0, "no\n"), "linux", no_rtc)[0] is True
    assert selftest.clock_synced(lambda cmd: (1, ""), "darwin", no_rtc)[0] is True


def test_disk_space(tmp_path):
    Usage = namedtuple("Usage", "total used free")
    assert selftest.check_disk(tmp_path, 1024, lambda p: Usage(0, 0, 5 * 1024**3)).status == "ok"
    assert selftest.check_disk(tmp_path / "a" / "b.db", 1024, lambda p: Usage(0, 0, 100 * 1024**2)).failed


APLAY = """**** List of PLAYBACK Hardware Devices ****
card 0: vc4hdmi0 [vc4-hdmi-0], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
card 2: USB [Jabra SPEAK 510 USB], device 0: USB Audio [USB Audio]
"""
ARECORD = """**** List of CAPTURE Hardware Devices ****
card 2: USB [Jabra SPEAK 510 USB], device 0: USB Audio [USB Audio]
"""


def test_audio_finds_the_speakerphone():
    run = lambda cmd: (0, APLAY if cmd[0] == "aplay" else ARECORD)  # noqa: E731
    r = selftest.check_audio(run, "linux")
    assert r.status == "ok" and "Jabra" in r.detail


def test_audio_hdmi_alone_is_not_a_speakerphone():
    only_hdmi = APLAY.splitlines()[0] + "\n" + APLAY.splitlines()[1]
    run = lambda cmd: (0, only_hdmi if cmd[0] == "aplay" else "")  # noqa: E731
    assert selftest.check_audio(run, "linux").failed


def test_audio_without_alsa_tools_fails_and_is_skipped_off_linux():
    assert selftest.check_audio(lambda cmd: (127, ""), "linux").failed
    assert selftest.check_audio(lambda cmd: (127, ""), "darwin").status == "skip"


def test_env_reports_names_never_values():
    secret = "sk-ant-THIS-MUST-NOT-APPEAR"
    env = {"ANTHROPIC_API_KEY": secret, "ELEVENLABS_API_KEY": "  "}
    r = selftest.check_env(env)
    assert r.failed and "ELEVENLABS_API_KEY" in r.detail and secret not in r.detail
    full = {n: secret for n in selftest.REQUIRED_ENV}
    r = selftest.check_env(full)
    assert r.status == "warn" and secret not in r.detail and "TELEGRAM_BOT_TOKEN" in r.detail
    r = selftest.check_env({n: secret for n in selftest.REQUIRED_ENV + selftest.OPTIONAL_ENV})
    assert r.status == "ok" and secret not in r.detail


class _Handler(http.server.BaseHTTPRequestHandler):
    code = 200

    def do_GET(self):
        self.send_response(self.code)
        self.end_headers()

    def log_message(self, *a):
        pass


@contextlib.contextmanager
def _server(code):
    handler = type("H", (_Handler,), {"code": code})
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def test_web_answers_errors_and_absence():
    with _server(200) as port:
        assert selftest.check_web(port).status == "ok"
    with _server(303) as port:   # a redirect to the login page still means the page is up
        assert not selftest.check_web(port).failed
    with _server(500) as port:
        assert selftest.check_web(port).failed
    with _server(200) as port:
        free = port
    assert selftest.check_web(free).status == "skip"
    assert selftest.check_web(free, require=True).failed


def test_main_report_and_exit_code(db, capsys, monkeypatch):
    for n in selftest.REQUIRED_ENV:
        monkeypatch.setenv(n, "VALUE-NEVER-PRINTED")
    code = selftest.main(["--db", str(db), "--skip", "audio", "--skip", "web", "--min-free-mb", "1"])
    out = capsys.readouterr().out
    assert code == 0 and out.startswith("KORE self-test: OK") and "VALUE-NEVER-PRINTED" not in out
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    assert selftest.main(["--db", str(db), "--skip", "audio", "--skip", "web", "--min-free-mb", "1"]) == 1
    assert "FAILED (env)" in capsys.readouterr().out


def test_a_crashing_check_is_a_failure_not_a_crash(db, monkeypatch):
    monkeypatch.setattr(selftest, "check_disk", lambda *a: 1 / 0)
    results = selftest.run_checks(db, skip={"audio", "web", "env"})
    disk = next(r for r in results if r.name == "disk")
    assert disk.failed and "ZeroDivisionError" in disk.detail


def test_runs_as_a_module(db, tmp_path):
    import subprocess
    import sys
    copy = tmp_path / "copy.db"
    shutil.copy(db, copy)
    p = subprocess.run([sys.executable, "-m", "kore.selftest", "--db", str(copy), "--skip", "env", "--skip", "audio",
                        "--skip", "web", "--min-free-mb", "1"], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
