"""Pepe on the box: the Raspberry Pi's button, presence sensor and light ring, its microphone (kore/home/pi_audio.py)
and ElevenLabs (kore/home/eleven.py), around the home loop. Started by systemd (deploy/pi/kore-home.service):

    python -m kore.home --db /var/lib/kore/kore.db --pi

- A short press of the button: the light shows "listening", Pepe hears one turn and answers.
- Holding the button: help, at once (Home.help_button), ahead of anything else waiting.
- The sensor: moving / still / absent to the home loop (the morning greeting, nobody-home rules).
- The light: listening, thinking, speaking; night or quiet when that's on; error when the voice can't speak.
- systemd: "ready" once started, then "alive" on every tick (a stuck loop is restarted by the watchdog).
The wake word "Pepe" isn't here yet (BUILD-PLAN phase 0.5, experiment 3: a model trained and tested against their
TV first); until then the button is the way to talk.
"""

from __future__ import annotations

import threading
import time

from kore.hardware import systemd
from kore.manners import modes

TICK_S = 5.0
PUMP_S = 0.2


class LitVoice:
    """The voice, with the light ring showing "speaking" while it talks."""

    def __init__(self, voice, ring):
        self.voice = voice
        self.ring = ring
        self.idle = "idle"

    def say(self, text: str, settings: dict) -> bool:
        self.ring.show("speaking")
        try:
            ok = self.voice.say(text, settings)
        finally:
            self.ring.show(self.idle)
        if not ok:
            self.ring.show("error")
        return ok

    def chime(self) -> None:
        self.voice.chime()


class Box:
    def __init__(self, home, device, ears, lit: LitVoice | None = None, monotonic=time.monotonic, environ=None):
        self.home = home
        self.device = device
        self.ears = ears
        self.lit = lit
        self.monotonic = monotonic
        self.environ = environ
        self.stop = threading.Event()
        device.connect(home, talk=self.talk)

    def talk(self) -> None:
        """A short press: one turn."""
        ring = self.device.ring
        ring.show("listening")
        heard = self.ears.listen()
        if heard.error or not heard.text:
            ring.show(self._resting())
            if heard.error and heard.error != "no_speech":
                self.home.voice.say("Perdone, ahora no le he podido oír. Pruebe otra vez dentro de un momento.",
                                    {})
            return
        ring.show("thinking")
        self.home.heard(heard.text, via="button", ended_at=heard.ended_at)
        ring.show(self._resting())

    def _resting(self) -> str:
        if self.home.manners.is_night():
            return "night"
        if modes.active(self.home.conn, self.home.clock, "quiet"):
            return "quiet"
        return "idle"

    def run(self, max_ticks: int | None = None) -> None:
        systemd.ready(self.environ)
        ticks = 0
        next_tick = 0.0
        while not self.stop.is_set():
            now = self.monotonic()
            if now >= next_tick:
                self.home.tick()
                resting = self._resting()
                if self.lit is not None:
                    self.lit.idle = resting
                self.device.ring.show(resting)
                systemd.alive(self.environ)
                ticks += 1
                next_tick = now + TICK_S
                if max_ticks is not None and ticks >= max_ticks:
                    break
            self.device.pump()
            self.stop.wait(PUMP_S)
        self.home.wait_for_background(2)
        self.device.close()
