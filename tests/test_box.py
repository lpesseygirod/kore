"""The box's loop (kore/home/pi.py) with fake button, sensor, light ring, ears and voice."""

import pytest

from kore.hardware.device import open_device
from kore.home.loop import Home
from kore.home.mac import Heard
from kore.home.pi import Box, LitVoice
from kore.home.voice import FakeVoice
from kore.memory import store
from tests.test_home_loop import Claude, setup  # noqa: F401


class Ears:
    def __init__(self, heard):
        self.heard, self.calls = heard, 0

    def listen(self):
        self.calls += 1
        return self.heard


@pytest.fixture
def box(setup, clock):
    db, carmen, toni = setup
    device = open_device(real=False)
    voice = FakeVoice()
    lit = LitVoice(voice, device.ring)
    home = Home(db, clock, Claude("Dígame, Carmen."), lit, {}, backups=False)
    b = Box(home, device, Ears(Heard("Soy Carmen, hola", 1.0)), lit, environ={})
    b.voice, b.carmen = voice, carmen
    return b


def test_a_short_press_is_one_turn(box):
    box.device.button.press(0.2)
    box.device.pump()
    assert box.voice.said == ["Dígame, Carmen."] and box.ears.calls == 1
    assert box.device.ring.state == "idle"


def test_holding_the_button_is_help(box):
    ana = store.add_contact(box.home.conn, box.home.clock, "Ana", "hija", help_contact=True)
    from kore.family.channels import FakeChannel
    tg = FakeChannel()
    box.home.family.channels["telegram"] = tg
    box.home.conn.execute("UPDATE contacts SET telegram_chat_id = '9' WHERE id = ?", (ana,))
    for p in (box.carmen, 2):
        store.set_consent(box.home.conn, box.home.clock, p, "emergency_path", True)
    box.device.button.press(2.5)
    box.device.pump()
    assert tg.sent and box.voice.said[0].startswith("Ya he avisado a Ana")
    assert box.ears.calls == 0


def test_nothing_heard_says_nothing_and_a_broken_ear_says_so(box):
    box.ears.heard = Heard("", None)
    box.talk()
    assert box.voice.said == []
    box.ears.heard = Heard("", error="stt_no_connection")
    box.talk()
    assert "no le he podido oír" in box.voice.said[0]


def test_the_light_shows_speaking_then_rest_and_error_when_the_voice_fails(box):
    shown = []
    real_show = box.device.ring.show
    box.device.ring.show = lambda s: (shown.append(s), real_show(s))
    box.talk()
    assert shown[:3] == ["listening", "thinking", "speaking"] and shown[-1] == "idle"
    box.voice.broken = True
    box.talk()
    assert "error" in shown


def test_the_loop_ticks_and_tells_systemd(box, monkeypatch):
    sent = []
    from kore.hardware import systemd
    monkeypatch.setattr(systemd, "ready", lambda env=None: sent.append("ready"))
    monkeypatch.setattr(systemd, "alive", lambda env=None: sent.append("alive"))
    box.run(max_ticks=1)
    assert sent == ["ready", "alive"]
