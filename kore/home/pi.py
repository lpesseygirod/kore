"""Pepe on the box: the Raspberry Pi's button, presence sensor and light ring, its microphone (kore/home/pi_audio.py)
and ElevenLabs (kore/home/eleven.py), around the home loop. Started by systemd (deploy/pi/kore-home.service):

    python -m kore.home --db /var/lib/kore/kore.db --pi

- A short press of the button: the light shows "listening", Pepe hears one turn and answers.
- Holding the button: help, at once (Home.help_button), ahead of anything else waiting.
- The sensor: moving / still / absent to the home loop (the morning greeting, nobody-home rules).
- The light: listening, thinking, speaking; night or quiet when that's on; error when the voice can't speak.
- systemd: "ready" once started, then "alive" on every tick (a stuck loop is restarted by the watchdog).
The wake word "Pepe" isn't here yet (BUILD-PLAN experiment 3: it needs a model trained and tested against their
TV first); until then the button is the way to talk.
"""

from __future__ import annotations

import threading
import time

from kore.hardware import systemd
from kore.manners import modes

TICK_S = 5.0
PUMP_S = 0.2
CANT_HEAR = "Perdone, ahora no le he podido oír. Pruebe otra vez dentro de un momento."
STUCK_S = 150  # no sign of life for this long: stop telling systemd we're alive, so it restarts Pepe


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
        if not ok:  # not even a kept line: at least a sound, and the light says something is wrong
            self.ring.show("error")
            try:
                self.voice.chime()
            except Exception:
                pass
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
        home.help_waiting = device.help_waiting  # a help press stops a turn in progress

    def talk(self) -> None:
        """A short press: one turn."""
        ring = self.device.ring
        ring.show("listening")
        heard = self.ears.listen()
        if self.device.help_waiting():
            return  # help was pressed while listening: help goes first
        if heard.error or not heard.text:
            ring.show(self._resting())
            if heard.error and heard.error != "no_speech":
                self.home.voice.say(CANT_HEAR, {})
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
        watchdog = threading.Thread(target=self._watchdog, daemon=True)
        watchdog.start()
        ticks = 0
        next_tick = 0.0
        while not self.stop.is_set():
            self._pump()  # help pressed while the last tick ran goes before the next tick
            now = self.monotonic()
            self.home.progress = now
            if now >= next_tick:
                try:
                    self.home.tick()
                    resting = self._resting()
                    if self.lit is not None:
                        self.lit.idle = resting
                    self.device.ring.show(resting)
                except Exception as e:  # logged; help and the next tick still run
                    _log("tick", e)
                ticks += 1
                next_tick = now + TICK_S
                if max_ticks is not None and ticks >= max_ticks:
                    break
            self._pump()
            self.stop.wait(PUMP_S)
        self.home.wait_for_background(2)
        self.device.close()

    def _pump(self) -> None:
        try:
            self.device.pump()
        except Exception as e:
            _log("pump", e)

    def _watchdog(self) -> None:
        """Tell systemd we're alive while the loop shows signs of life (a long turn included); a stuck loop
        stops the pings and gets restarted."""
        while not self.stop.is_set():
            if self.monotonic() - self.home.progress < STUCK_S:
                systemd.alive(self.environ)
            self.stop.wait(10)


def _log(what: str, e: Exception) -> None:
    import sys
    print(f"kore: {what}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
