"""The big button. A short press is "talk to Pepe" (like saying his name); holding it is "help".

Holding fires "help" while the finger is still down (after `long_press_s`), so nobody has to guess when to let go.
Lea can change the hold time in one place (pins.py). The real button uses gpiozero (BSD licence), imported only
on the Pi; the Mac and the tests use FakeButton.
"""

from __future__ import annotations

from typing import Callable, Protocol

Callback = Callable[[], None]


class Button(Protocol):
    on_short: Callback | None   # "talk to Pepe"
    on_long: Callback | None    # "help"

    def close(self) -> None: ...


class PressLogic:
    """Turns pressed / held / released into one short or one long press. Shared by the fake and the real button."""

    def __init__(self) -> None:
        self.on_short: Callback | None = None
        self.on_long: Callback | None = None
        self._down = False
        self._held = False

    def pressed(self) -> None:
        self._down = True
        self._held = False

    def held(self) -> None:
        if self._down and not self._held:
            self._held = True
            if self.on_long:
                self.on_long()

    def released(self) -> None:
        if not self._down:
            return  # a release without a press (switch bounce at start-up)
        self._down = False
        if not self._held and self.on_short:
            self.on_short()
        self._held = False


class FakeButton(PressLogic):
    """For tests and the Mac: press it by hand."""

    def __init__(self, long_press_s: float = 2.0) -> None:
        super().__init__()
        self.long_press_s = long_press_s

    def press(self, seconds: float = 0.2) -> None:
        self.pressed()
        if seconds >= self.long_press_s:
            self.held()
        self.released()

    def close(self) -> None:
        pass


class GpioButton(PressLogic):
    """The arcade button between a GPIO pin and GND, using the Pi's own pull-up."""

    def __init__(self, pin: int, long_press_s: float = 2.0) -> None:
        super().__init__()
        from gpiozero import Button as _Button  # only on the Pi

        self._b = _Button(pin, pull_up=True, bounce_time=0.05, hold_time=long_press_s)
        self._b.when_pressed = self.pressed
        self._b.when_held = self.held
        self._b.when_released = self.released

    def close(self) -> None:
        self._b.close()
