"""The box's parts together: button, presence sensor, light ring, and how they reach the home loop.

The pins' callbacks run on gpiozero's own threads, and the home loop isn't built for several threads at once. So a
press or a sensor change is only queued here; the loop's own thread calls `pump()` (every half second or so, and on
every tick) and that calls the Home. "Help" jumps the queue.

    device = open_device()                       # the real parts on the Pi, fakes elsewhere
    device.connect(home, talk=listen_once)       # talk: what a short press does (listen, then home.heard)
    ... in the loop: device.pump(); device.ring.show("listening") ...
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kore.hardware.button import Button, FakeButton, GpioButton
from kore.hardware.pins import Pins
from kore.hardware.presence import FakePresence, GpioPresence, Presence
from kore.hardware.ring import FakeRing, Ring, SpiRing

MODEL_FILE = Path("/proc/device-tree/model")


def on_pi(model_file: Path = MODEL_FILE) -> bool:
    try:
        return "Raspberry Pi" in model_file.read_text(errors="replace")
    except OSError:
        return False


@dataclass
class Device:
    button: Button
    presence: Presence
    ring: Ring

    def __post_init__(self) -> None:
        self._events: deque[tuple] = deque()
        self._lock = threading.Lock()
        self._home = None
        self._talk: Callable[[], None] | None = None

    def connect(self, home, talk: Callable[[], None]) -> None:
        """home: has help_button() and presence(state). talk: run on a short press, on the loop's thread."""
        self._home = home
        self._talk = talk
        self.button.on_short = lambda: self._queue(("talk",))
        self.button.on_long = lambda: self._queue(("help",), urgent=True)
        self.presence.on_change = lambda state: self._queue(("presence", state))

    def _queue(self, event: tuple, urgent: bool = False) -> None:
        with self._lock:
            if urgent:
                self._events.appendleft(event)
            else:
                self._events.append(event)

    def help_waiting(self) -> bool:
        """A help press is queued: whatever the loop is doing stops for it."""
        with self._lock:
            return any(e[0] == "help" for e in self._events)

    def pump(self, now: float | None = None) -> int:
        """Hand queued events to the Home, on the caller's thread. Returns how many. One event failing never
        stops the others (help most of all)."""
        try:
            self.presence.poll(now)  # 'still' and 'absent' come from time passing, not from the pin
        except Exception as e:
            _log("presence", e)
        done = 0
        while True:
            with self._lock:
                if not self._events:
                    return done
                event = self._events.popleft()
            done += 1
            try:
                if event[0] == "help":
                    self._home.help_button()
                elif event[0] == "talk":
                    if self._talk:
                        self._talk()
                elif event[0] == "presence":
                    self._home.presence(event[1])
            except Exception as e:  # logged; the loop carries on (systemd restarts it only if it's stuck)
                _log(event[0], e)

    def close(self) -> None:
        for part in (self.button, self.presence, self.ring):
            try:
                part.close()
            except Exception:
                pass


def open_device(pins: Pins | None = None, real: bool | None = None) -> Device:
    """The real parts on a Raspberry Pi; fakes anywhere else (or when real=False). A part that fails to start
    (a loose light ring, a missing library) is replaced by its fake and logged: the rest of Pepe, help included,
    keeps working (the self-test reports it)."""
    pins = pins or Pins.from_env()
    real = on_pi() if real is None else real
    if not real:
        return Device(FakeButton(pins.long_press_s), FakePresence(), FakeRing(pins.led_count, pins.led_brightness))
    return Device(_part("button", lambda: GpioButton(pins.button, pins.long_press_s),
                        lambda: FakeButton(pins.long_press_s)),
                  _part("presence", lambda: GpioPresence(pins.presence), FakePresence),
                  _part("ring", lambda: SpiRing(pins.led_count, pins.led_brightness, pins.led_spi_bus,
                                                pins.led_spi_device),
                        lambda: FakeRing(pins.led_count, pins.led_brightness)))


def _part(name: str, real, fake):
    try:
        return real()
    except Exception as e:
        _log(f"{name} not started", e)
        return fake()


def _log(what: str, e: Exception) -> None:
    import sys
    print(f"kore: {what}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
