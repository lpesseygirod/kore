"""The presence sensor: is someone moving in the room, sitting still, or is nobody there?

The sensor (an LD2410C mmWave radar, or a PIR) has one OUT pin: high while it senses someone. One pin can't say
"moving" or "still" by itself, so PresenceTracker works it out from time:
  - the pin goes high (someone arrives or starts moving)           -> 'moving'
  - it stays high with no new rise for `still_after_s`              -> 'still'
  - it stays low for `absent_after_s` (short drops are ignored)     -> 'absent'
The home loop gets only changes (`Home.presence(state)`). Nothing is recorded about the room beyond that.

The real sensor uses gpiozero (BSD licence), imported only on the Pi. `poll()` must be called regularly (the loop's
tick does it), because 'still' and 'absent' happen when nothing on the pin changes.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Protocol

States = ("moving", "still", "absent")


class Presence(Protocol):
    on_change: Callable[[str], None] | None

    def poll(self, now: float | None = None) -> str | None: ...

    def close(self) -> None: ...


class PresenceTracker:
    def __init__(self, still_after_s: float = 60.0, absent_after_s: float = 120.0) -> None:
        self.still_after_s = still_after_s
        self.absent_after_s = absent_after_s
        self.on_change: Callable[[str], None] | None = None
        self.state: str | None = None    # unknown until the sensor says something
        self._level = False
        self._last_rise: float | None = None
        self._last_fall: float | None = None
        self._lock = threading.Lock()    # the pin's callbacks come from gpiozero's thread

    def level(self, high: bool, now: float | None = None) -> str | None:
        """The pin changed (or was read at start-up)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            rose = high and not self._level
            if rose:
                self._last_rise = now
            elif not high and (self._level or self._last_fall is None):
                self._last_fall = now   # it fell, or it was low from the start
            self._level = high
        if rose:
            return self._set("moving")
        return self.poll(now)

    def poll(self, now: float | None = None) -> str | None:
        now = time.monotonic() if now is None else now
        with self._lock:
            new = None
            if self._level and self._last_rise is not None and now - self._last_rise >= self.still_after_s:
                new = "still"
            elif not self._level and self._last_fall is not None and now - self._last_fall >= self.absent_after_s:
                new = "absent"
        return self._set(new) if new else None

    def _set(self, state: str) -> str | None:
        with self._lock:
            if state == self.state:
                return None
            self.state = state
        if self.on_change:
            self.on_change(state)
        return state


class FakePresence(PresenceTracker):
    """For tests and the Mac: set the pin by hand, move time by hand."""

    def close(self) -> None:
        pass


class GpioPresence(PresenceTracker):
    def __init__(self, pin: int, still_after_s: float = 60.0, absent_after_s: float = 120.0) -> None:
        super().__init__(still_after_s, absent_after_s)
        from gpiozero import DigitalInputDevice  # only on the Pi

        # The LD2410C drives OUT itself (push-pull): no pull-up. Its own hold time already smooths flicker.
        self._d = DigitalInputDevice(pin, pull_up=None, active_state=True, bounce_time=0.2)
        self._d.when_activated = lambda: self.level(True)
        self._d.when_deactivated = lambda: self.level(False)
        self.level(bool(self._d.value))

    def close(self) -> None:
        self._d.close()
