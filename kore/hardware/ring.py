"""The light ring: shows what Pepe is doing, softly.

  idle        off (a box that glows all day is a box that annoys)
  listening   the whole ring soft blue: "I'm listening"
  thinking    a short blue arc going round
  speaking    the whole ring warm white
  reminder    one soft green light: "something for you, when you can"
  quiet       one dim violet light: quiet mode is on
  night       one very dim amber light
  error       the whole ring dim red: something is wrong (the logs say why)

The ring is a WS2812 strip, driven through the Pi's SPI port (spidev, MIT licence; imported only on the Pi):
each bit of colour becomes three SPI bits at 2.4 MHz, which gives the WS2812 the timing it needs. This works on
the Pi 5, where the older PWM/DMA libraries don't. The frames and the encoding are plain code, tested on the Mac.
"""

from __future__ import annotations

import threading
from typing import Protocol

RGB = tuple[int, int, int]

STATES = ("idle", "listening", "thinking", "speaking", "reminder", "quiet", "night", "error")

COLOURS: dict[str, RGB] = {
    "idle": (0, 0, 0),
    "listening": (40, 110, 255),
    "thinking": (40, 110, 255),
    "speaking": (255, 190, 120),
    "reminder": (60, 200, 90),
    "quiet": (120, 60, 200),
    "night": (255, 120, 20),
    "error": (255, 30, 20),
}
NIGHT_BRIGHTNESS = 0.08  # the night light is a tenth or so of the day's
ARC = 3                  # LEDs lit while thinking
SPI_HZ = 2_400_000


def frame(state: str, n: int, brightness: float = 0.35, step: int = 0) -> list[RGB]:
    """The colour of each LED for a state. `step` moves the thinking arc round."""
    if state not in COLOURS:
        raise ValueError(f"unknown ring state: {state}")
    level = NIGHT_BRIGHTNESS if state == "night" else brightness
    level = max(0.0, min(1.0, level))
    on = tuple(round(c * level) for c in COLOURS[state])
    off = (0, 0, 0)
    if state == "idle" or n <= 0:
        return [off] * max(n, 0)
    if state in ("reminder", "quiet", "night"):
        return [on] + [off] * (n - 1)
    if state == "thinking":
        lit = {(step + i) % n for i in range(min(ARC, n))}
        return [on if i in lit else off for i in range(n)]
    return [on] * n


def encode_ws2812_spi(pixels: list[RGB]) -> bytes:
    """GRB order, one LED bit -> three SPI bits (1 -> 110, 0 -> 100), then a long low gap to latch."""
    bits = 0
    nbits = 0
    for r, g, b in pixels:
        for byte in (g, r, b):
            for i in range(7, -1, -1):
                bits = (bits << 3) | (0b110 if (byte >> i) & 1 else 0b100)
                nbits += 3
    data = bits.to_bytes(nbits // 8, "big") if nbits else b""
    return b"\x00" + data + b"\x00" * 84  # >= 280 us low before and after (newer WS2812B need that long)


class Ring(Protocol):
    def show(self, state: str) -> None: ...

    def close(self) -> None: ...


class FakeRing:
    def __init__(self, n: int = 12, brightness: float = 0.35) -> None:
        self.n = n
        self.brightness = brightness
        self.states: list[str] = []
        self.pixels: list[RGB] = [(0, 0, 0)] * n

    @property
    def state(self) -> str | None:
        return self.states[-1] if self.states else None

    def show(self, state: str) -> None:
        self.pixels = frame(state, self.n, self.brightness)
        self.states.append(state)

    def close(self) -> None:
        self.show("idle")


class SpiRing:
    """The real ring on /dev/spidev<bus>.<device>. A small thread turns the arc while thinking."""

    def __init__(self, n: int = 12, brightness: float = 0.35, bus: int = 0, device: int = 0) -> None:
        import spidev  # only on the Pi

        self.n = n
        self.brightness = brightness
        self._spi = spidev.SpiDev()
        self._spi.open(bus, device)
        self._spi.max_speed_hz = SPI_HZ
        self._spi.mode = 0
        self._lock = threading.Lock()
        self._state = "idle"
        self._stop = threading.Event()
        self._spin: threading.Thread | None = None
        self.show("idle")

    def _write(self, state: str, step: int = 0) -> None:
        self._spi.writebytes2(encode_ws2812_spi(frame(state, self.n, self.brightness, step)))

    def show(self, state: str) -> None:
        frame(state, 0)  # check the name before changing anything
        with self._lock:
            self._state = state
            self._write(state)
        if state == "thinking" and not (self._spin and self._spin.is_alive()):
            self._stop.clear()
            self._spin = threading.Thread(target=self._turn, daemon=True)
            self._spin.start()

    def _turn(self) -> None:
        step = 0
        while not self._stop.wait(0.1):
            with self._lock:  # never paint the arc over a state shown meanwhile
                if self._state != "thinking":
                    return
                step += 1
                self._write("thinking", step)

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            self._state = "idle"
            self._write("idle")
        self._spi.close()
