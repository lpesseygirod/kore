"""The box's parts (phase 7): button, presence sensor, light ring, all without a Pi.

The real classes are checked against stand-in gpiozero / spidev modules: no real GPIO is touched.
"""

import os
import socket
import subprocess
import sys
import tempfile
import types

import pytest

from kore.hardware import systemd
from kore.hardware.button import FakeButton, GpioButton
from kore.hardware.device import Device, on_pi, open_device
from kore.hardware.pins import Pins
from kore.hardware.presence import FakePresence, GpioPresence
from kore.hardware.ring import STATES, FakeRing, SpiRing, encode_ws2812_spi, frame


def test_importing_the_package_needs_no_pi_libraries():
    code = ("import sys, kore.hardware, kore.hardware.device; "
            "assert 'gpiozero' not in sys.modules and 'spidev' not in sys.modules")
    subprocess.run([sys.executable, "-c", code], check=True)


# --- pins -------------------------------------------------------------------------------------------------------

def test_pin_defaults_and_overrides():
    p = Pins.from_env({})
    assert (p.button, p.presence, p.led_count, p.long_press_s) == (17, 27, 12, 2.0)
    q = Pins.from_env({"KORE_PIN_BUTTON": "22", "KORE_LED_BRIGHTNESS": "0.2", "KORE_LED_COUNT": " 16 "})
    assert (q.button, q.led_brightness, q.led_count) == (22, 0.2, 16)


@pytest.mark.parametrize("env", [{"KORE_PIN_BUTTON": "x"}, {"KORE_LED_BRIGHTNESS": "3"},
                                 {"KORE_PIN_BUTTON": "27"}])
def test_bad_pins_are_refused(env):
    with pytest.raises(ValueError):
        Pins.from_env(env)


# --- button -----------------------------------------------------------------------------------------------------

def test_short_press_talks_long_press_is_help():
    b = FakeButton(long_press_s=2.0)
    got = []
    b.on_short = lambda: got.append("talk")
    b.on_long = lambda: got.append("help")
    b.press(0.3)
    b.press(2.5)
    assert got == ["talk", "help"]


def test_help_fires_once_while_still_held_and_release_adds_nothing():
    b = FakeButton()
    got = []
    b.on_short = lambda: got.append("talk")
    b.on_long = lambda: got.append("help")
    b.pressed()
    b.held()
    b.held()
    assert got == ["help"]
    b.released()
    b.released()  # bounce
    assert got == ["help"]


class _StubPin:
    instances: list = []

    def __init__(self, pin, **kw):
        self.pin, self.kw, self.value, self.closed = pin, kw, 0, False
        self.when_pressed = self.when_held = self.when_released = None
        self.when_activated = self.when_deactivated = None
        _StubPin.instances.append(self)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_gpiozero(monkeypatch):
    _StubPin.instances = []
    mod = types.ModuleType("gpiozero")
    mod.Button = _StubPin
    mod.DigitalInputDevice = _StubPin
    monkeypatch.setitem(sys.modules, "gpiozero", mod)
    return _StubPin


def test_gpio_button_wires_gpiozero(fake_gpiozero):
    b = GpioButton(17, long_press_s=2.0)
    pin = fake_gpiozero.instances[0]
    assert pin.pin == 17 and pin.kw["pull_up"] is True and pin.kw["hold_time"] == 2.0
    got = []
    b.on_short = lambda: got.append("talk")
    b.on_long = lambda: got.append("help")
    pin.when_pressed()
    pin.when_released()
    pin.when_pressed()
    pin.when_held()
    pin.when_released()
    assert got == ["talk", "help"]
    b.close()
    assert pin.closed


# --- presence ---------------------------------------------------------------------------------------------------

def test_presence_moving_still_absent():
    p = FakePresence(still_after_s=60, absent_after_s=120)
    seen = []
    p.on_change = seen.append
    assert p.level(True, now=0) == "moving"
    assert p.poll(now=30) is None
    assert p.poll(now=61) == "still"
    assert p.poll(now=70) is None          # only changes are reported
    p.level(False, now=100)
    assert p.poll(now=200) is None         # a short gap is not "absent"
    assert p.poll(now=221) == "absent"
    assert seen == ["moving", "still", "absent"]


def test_presence_short_drop_is_not_absent_and_repeat_high_is_not_moving():
    p = FakePresence(still_after_s=60, absent_after_s=120)
    p.level(True, now=0)
    p.poll(now=61)
    assert p.level(True, now=62) is None   # already high: still 'still'
    p.level(False, now=70)
    assert p.level(True, now=80) == "moving"  # back within the gap: they moved
    assert p.poll(now=500) == "still"


def test_presence_low_at_start_becomes_absent():
    p = FakePresence(absent_after_s=120)
    assert p.level(False, now=0) is None
    assert p.poll(now=121) == "absent"


def test_gpio_presence_reads_the_pin_at_start(fake_gpiozero):
    _StubPin.instances = []

    class High(_StubPin):
        def __init__(self, pin, **kw):
            super().__init__(pin, **kw)
            self.value = 1

    sys.modules["gpiozero"].DigitalInputDevice = High
    p = GpioPresence(27)
    pin = fake_gpiozero.instances[0]
    assert pin.pin == 27 and p.state == "moving"
    seen = []
    p.on_change = seen.append
    pin.when_deactivated()
    pin.when_activated()
    assert seen == []                      # low then high again: still 'moving'


# --- ring -------------------------------------------------------------------------------------------------------

def test_every_state_has_a_frame_and_idle_is_dark():
    for s in STATES:
        assert len(frame(s, 12)) == 12
    assert frame("idle", 12) == [(0, 0, 0)] * 12
    assert sum(1 for px in frame("quiet", 12) if px != (0, 0, 0)) == 1
    assert all(px != (0, 0, 0) for px in frame("listening", 12))
    with pytest.raises(ValueError):
        frame("disco", 12)


def test_night_is_dimmer_than_day_and_brightness_is_capped():
    day = frame("speaking", 12, brightness=0.35)[0]
    night = frame("night", 12, brightness=0.35)[0]
    assert max(night) < max(day)
    assert max(frame("listening", 12, brightness=5)[0]) <= 255


def test_thinking_arc_moves():
    a, b = frame("thinking", 12, step=0), frame("thinking", 12, step=1)
    assert a != b and sum(px != (0, 0, 0) for px in a) == 3


def test_ws2812_spi_encoding():
    data = encode_ws2812_spi([(0, 255, 0)])  # GRB: green first
    body = data[1:1 + 9]
    assert body[:3] == bytes([0xDB, 0x6D, 0xB6])        # 8 ones: 110 x8
    assert body[3:] == bytes([0x92, 0x49, 0x24]) * 2    # 8 zeros: 100 x8
    assert data[0] == 0 and data[10:] == b"\x00" * 84
    assert len(encode_ws2812_spi([(1, 2, 3)] * 12)) == 1 + 12 * 9 + 84


def test_spi_ring_writes_frames(monkeypatch):
    written = []

    class Spi:
        def open(self, bus, dev):
            self.where = (bus, dev)

        def writebytes2(self, data):
            written.append(bytes(data))

        def close(self):
            pass

    mod = types.ModuleType("spidev")
    mod.SpiDev = Spi
    monkeypatch.setitem(sys.modules, "spidev", mod)
    r = SpiRing(n=12)
    r.show("listening")
    assert written[-1] == encode_ws2812_spi(frame("listening", 12, 0.35))
    r.show("speaking")
    r.close()
    assert written[-1] == encode_ws2812_spi(frame("idle", 12))


# --- the parts together -----------------------------------------------------------------------------------------

class FakeHome:
    def __init__(self):
        self.calls = []

    def help_button(self):
        self.calls.append("help")

    def presence(self, state):
        self.calls.append(("presence", state))


def test_device_queues_and_help_jumps_the_queue():
    d = open_device(Pins(), real=False)
    home = FakeHome()
    d.connect(home, talk=lambda: home.calls.append("talk"))
    d.presence.level(True, now=0)
    d.button.press(0.2)
    d.button.press(3)
    assert home.calls == []                # nothing happens on the pins' threads
    assert d.pump(now=1) == 3
    assert home.calls == ["help", ("presence", "moving"), "talk"]
    d.pump(now=61)                         # time alone makes 'still'
    assert home.calls[-1] == ("presence", "still")
    d.ring.show("listening")
    assert d.ring.state == "listening"
    d.close()


def test_fakes_off_the_pi(tmp_path):
    model = tmp_path / "model"
    assert not on_pi(model)
    model.write_text("Raspberry Pi 5 Model B Rev 1.0\x00")
    assert on_pi(model)
    d = open_device(Pins(), real=False)
    assert isinstance(d, Device) and isinstance(d.ring, FakeRing)


# --- systemd notify ---------------------------------------------------------------------------------------------

def test_notify_is_silent_outside_systemd():
    assert systemd.ready({}) is False and systemd.alive({}) is False


def test_notify_sends_to_the_socket():
    short = tempfile.mkdtemp(prefix="kn", dir="/tmp")  # unix socket paths must be short (macOS: 104 bytes)
    path = os.path.join(short, "n.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as srv:
        srv.bind(path)
        assert systemd.ready({"NOTIFY_SOCKET": path})
        assert systemd.alive({"NOTIFY_SOCKET": path})
        assert srv.recv(64) == b"READY=1"
        assert srv.recv(64) == b"WATCHDOG=1"
    os.unlink(path)
    os.rmdir(short)
