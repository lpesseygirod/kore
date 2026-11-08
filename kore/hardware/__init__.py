"""The box's own parts (BUILD-PLAN phase 7): the help button, the presence sensor, the light ring.

Each has an interface, a fake (tests, the Mac) and a real one for the Raspberry Pi. The real ones import gpiozero
(BSD licence) or spidev (MIT licence) only when they are built, on the Pi, so nothing here needs them elsewhere.
Pins: kore/hardware/pins.py, the one place to change them.
"""

from kore.hardware.button import FakeButton, GpioButton
from kore.hardware.device import Device, on_pi, open_device
from kore.hardware.pins import Pins
from kore.hardware.presence import FakePresence, GpioPresence, PresenceTracker
from kore.hardware.ring import STATES as RING_STATES
from kore.hardware.ring import FakeRing, SpiRing

__all__ = ["Device", "FakeButton", "FakePresence", "FakeRing", "GpioButton", "GpioPresence", "Pins",
           "PresenceTracker", "RING_STATES", "SpiRing", "on_pi", "open_device"]
