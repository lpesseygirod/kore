"""Run Pepe on the Mac, until the box exists (phase 1).

  uv run --env-file .env --group live python -m kore.home --voz           talk to Pepe, out loud
  uv run --env-file .env --group live python -m kore.home                 in text

With --voz: press Enter (the stand-in for Pepe's button), wait for the "tin", speak; Pepe answers out loud with the
Mac's own Spanish voice. You can also type a sentence and press Enter. Without --voz Pepe's lines are printed.
Reminders, the manners gate, family messages (only those set up in .env), weather and news run as on the box.
  /ayuda      the help button
  /sale       the presence sensor: nobody in the room   (/entra: someone moving, /quieto: someone still)
  /fin        stop
Without ANTHROPIC_API_KEY (or with --offline) Pepe uses his offline phrases and nothing is spent.
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from pathlib import Path

TICK_S = 5


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kore.home")
    ap.add_argument("--db", default="data/kore.db")
    ap.add_argument("--offline", action="store_true", help="no Claude: nothing is spent")
    ap.add_argument("--voz", action="store_true", help="the Mac's microphone and voice")
    ap.add_argument("--pi", action="store_true",
                    help="the box: its button, sensor, light ring, microphone and ElevenLabs (started by systemd)")
    a = ap.parse_args(argv)

    import os

    from kore.brain.clients import ScriptedClient
    from kore.family import channels
    from kore.family.heartbeat import Heartbeat
    from kore.home.loop import Home
    from kore.home.voice import PrintVoice
    from kore.info import Info
    from kore.info.news import News, Rss
    from kore.info.weather import OpenMeteo, Weather
    from kore.memory.db import connect
    from kore.scheduler.clock import SystemClock

    clock = SystemClock()
    if a.offline or not os.environ.get("ANTHROPIC_API_KEY"):
        def down(req):
            raise ConnectionError("offline")
        client = ScriptedClient(down)
        print("Sin Claude: Pepe usa sus frases de emergencia. No se gasta nada.", file=sys.stderr)
    else:
        from kore.brain.clients import LiveClient
        client = LiveClient("voice", daily_cap_usd=2.0)
        from kore.brain.conversation import MODEL
        threading.Thread(target=client.warm, args=(MODEL,), daemon=True).start()  # the first turn won't wait
    ears = None
    voice = PrintVoice()
    if a.voz:
        from kore.home import mac
        if not mac.available():
            print("--voz needs a Mac.", file=sys.stderr)
            return 2
        printed = PrintVoice()

        class Both:  # said out loud, and shown, so whoever is beside the Mac can follow
            def say(self, text, settings):
                printed.say(text, settings)
                return mac_voice.say(text, settings)

            def chime(self):
                mac_voice.chime()

        mac_voice = mac.MacVoice()
        voice = Both()
        ears = mac.MacEars(on_ready=lambda: (mac_voice.chime(), print("  (te escucho...)", flush=True)))
    if a.pi:
        return _run_pi(a, clock, client)
    conn = connect(a.db)
    info = Info(Weather(conn, clock, OpenMeteo()), News(conn, clock, Rss()))
    home = Home(a.db, clock, client, voice, channels.from_env(), info=info,
                heartbeat=Heartbeat.from_env(clock), turn_log=Path("data/turns.jsonl"))
    lines: queue.Queue[str | None] = queue.Queue()

    def read():
        for line in sys.stdin:
            lines.put(line.rstrip("\n"))
        lines.put(None)

    threading.Thread(target=read, daemon=True).start()
    if ears:
        print("Pepe está en marcha. Pulsa Enter, espera el «tin» y habla. También puedes escribir.\n"
              "/ayuda (botón de ayuda), /fin para terminar.", flush=True)
    else:
        print("Pepe está en marcha. Escribe lo que dirían (después de «Pepe»). /ayuda, /entra, /sale, /quieto, /fin.")
    next_tick = 0.0
    while True:
        if time.monotonic() >= next_tick:
            home.tick()
            next_tick = time.monotonic() + TICK_S
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            continue
        if line is None or line.strip() == "/fin":
            break
        cmd = line.strip()
        if cmd == "/ayuda":
            home.help_button()
        elif cmd in ("/entra", "/sale", "/quieto"):
            home.presence({"/entra": "moving", "/sale": "absent", "/quieto": "still"}[cmd])
        elif cmd:
            home.heard(cmd, via="button", ended_at=time.monotonic())
        elif ears is not None:
            heard = ears.listen()
            if heard.error:
                from kore.home.mac import ERRORS
                print(f"  ({ERRORS.get(heard.error, heard.error)})", flush=True)
            elif not heard.text:
                print("  (no he oído nada; pulsa Enter otra vez)", flush=True)
            else:
                print(f"Tú: {heard.text}", flush=True)
                home.heard(heard.text, via="button", ended_at=heard.ended_at)
    home.wait_for_background(2)
    return 0


def _run_pi(a, clock, client) -> int:
    import signal

    from kore.family import channels
    from kore.family.heartbeat import Heartbeat
    from kore.hardware.device import open_device
    from kore.home import eleven
    from kore.home.loop import Home
    from kore.home.pi import Box, LitVoice
    from kore.home.pi_audio import PiEars
    from kore.home.voice import PrintVoice
    from kore.info import Info
    from kore.info.news import News, Rss
    from kore.info.weather import OpenMeteo, Weather
    from kore.memory.db import connect

    stt, eleven_voice = eleven.from_env()
    if eleven_voice is None:
        print("ELEVENLABS_API_KEY isn't set: Pepe can't hear or speak yet (kore-set-key ELEVENLABS_API_KEY).",
              file=sys.stderr)
    device = open_device()
    lit = LitVoice(eleven_voice or PrintVoice(), device.ring)
    conn = connect(a.db)
    info = Info(Weather(conn, clock, OpenMeteo()), News(conn, clock, Rss()))
    home = Home(a.db, clock, client, lit, channels.from_env(), info=info, heartbeat=Heartbeat.from_env(clock),
                turn_log=Path(a.db).parent / "turns.jsonl")

    class NoEars:
        def listen(self):
            from kore.home.mac import Heard
            return Heard("", error="stt_bad_key", on_device=False)

    ears = PiEars(stt, on_ready=lit.chime) if stt is not None else NoEars()
    box = Box(home, device, ears, lit)
    signal.signal(signal.SIGTERM, lambda *_: box.stop.set())
    print("Pepe está en marcha en el aparato.", flush=True)
    box.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
