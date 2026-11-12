"""Where each part plugs into the Pi. The one place to change a pin.

Numbers are GPIO (BCM) numbers, not the physical pin numbers printed on diagrams; the physical pin is in the
comment, counting on the 40-pin header: pin 1 (3.3 V) is at the end farthest from the USB ports, inner row;
even numbers run along the board's edge.

Defaults, and how to wire them (docs/DEVICE.md has the step-by-step version):

  help button (arcade button)  GPIO17 (physical 11) and any GND (physical 9 or 14). No resistor: the Pi's own
                               pull-up is used; pressing connects the pin to GND.
  presence sensor (LD2410C)    OUT -> GPIO27 (physical 13); VCC -> 5 V (physical 2); GND -> GND (physical 6).
                               The LD2410C's OUT pin is 3.3 V, safe for the Pi. A PIR sensor wires the same way.
  LED ring (WS2812, 12 LEDs)   DIN -> GPIO10 / SPI0 MOSI (physical 19); 5V -> 5 V (physical 4); GND -> GND
                               (physical 20). Driven through SPI, so SPI must be on (install.sh turns it on).

Any of them can be moved without touching code, with these names in /etc/kore/kore.env (not secret):
  KORE_PIN_BUTTON, KORE_PIN_PRESENCE, KORE_LED_COUNT, KORE_LED_SPI_BUS, KORE_LED_SPI_DEVICE, KORE_LED_BRIGHTNESS,
  KORE_LONG_PRESS_S
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Pins:
    button: int = 17             # GPIO17, physical pin 11
    presence: int = 27           # GPIO27, physical pin 13
    led_count: int = 12          # LEDs on the ring
    led_spi_bus: int = 0         # SPI0: data on GPIO10, physical pin 19
    led_spi_device: int = 0      # /dev/spidev0.0
    led_brightness: float = 0.35  # 0..1; the ring sits in a living room, it must never dazzle
    long_press_s: float = 2.0    # holding the button this long is "help" (a short press is "talk to Pepe")

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Pins":
        env = os.environ if env is None else env
        names = {"button": "KORE_PIN_BUTTON", "presence": "KORE_PIN_PRESENCE", "led_count": "KORE_LED_COUNT",
                 "led_spi_bus": "KORE_LED_SPI_BUS", "led_spi_device": "KORE_LED_SPI_DEVICE",
                 "led_brightness": "KORE_LED_BRIGHTNESS", "long_press_s": "KORE_LONG_PRESS_S"}
        values = {}
        for f in fields(cls):
            raw = (env.get(names[f.name]) or "").strip()
            if raw:
                cast = float if f.type in ("float", float) else int
                try:
                    values[f.name] = cast(raw)
                except ValueError:
                    raise ValueError(f"{names[f.name]} must be a number") from None
        pins = cls(**values)
        if not 0 <= pins.led_brightness <= 1:
            raise ValueError("KORE_LED_BRIGHTNESS must be between 0 and 1")
        if pins.button == pins.presence:
            raise ValueError("the button and the presence sensor can't share a pin")
        return pins
