"""Run the home loop on the Mac, in text, until the speakerphone arrives (phase 1).

  uv run --env-file .env --group live python -m kore.home --db data/abuelos.db

Type what they'd say after "Pepe" and press Enter; Pepe's lines are printed. Reminders, the manners gate, family
messages (only those set up in .env), the weather and news run as they will on the box.
  /ayuda      the help button
  /sale       the presence sensor: nobody in the room   (/entra: someone moving, /quieto: someone still)
  /fin        stop
Without ANTHROPIC_API_KEY (or with --offline) a placeholder answers and reminders use the offline phrases.
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
    a = ap.parse_args(argv)

    import os

    from kore.brain.clients import ScriptedClient, text
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
    conn = connect(a.db)
    info = Info(Weather(conn, clock, OpenMeteo()), News(conn, clock, Rss()))
    home = Home(a.db, clock, client, PrintVoice(), channels.from_env(), info=info,
                heartbeat=Heartbeat.from_env(clock), turn_log=Path("data/turns.jsonl"))
    lines: queue.Queue[str | None] = queue.Queue()

    def read():
        for line in sys.stdin:
            lines.put(line.rstrip("\n"))
        lines.put(None)

    threading.Thread(target=read, daemon=True).start()
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
            home.heard(cmd, ended_at=time.monotonic())
    home.wait_for_background(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
