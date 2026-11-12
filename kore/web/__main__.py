"""My controls for the home web page, from the command line (on the box, or the Mac for now).

  serve [--host H] [--port P] [--dev]   run the page; --dev prints what Pepe would say (no speaker yet).
                                        Writing to Pepe needs: uv run --env-file .env --group live ...
  invite                                a 10-minute code for my own login (printed, never spoken)
  qr URL                                the QR code for the box's sticker (data/web/qr.svg and .png)
  logins                                every logged-in device
  close-all                             log every device out
  unlock                                end a pause after many wrong codes

--db PATH picks the database (default data/kore.db).
On the Mac, `serve` listens only on this computer (127.0.0.1). On the box it will listen on the
home Wi-Fi, with https once the domain exists (WEB-PLAN.md section 9).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kore.memory.db import connect
from kore.scheduler.clock import SystemClock
from kore.web import accounts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kore.web")
    ap.add_argument("--db", default="data/kore.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--dev", action="store_true")
    p.add_argument("--voz", nargs="?", const="Eddy (Spanish (Spain))", default=None,
                   help="the Mac reads Pepe's replies and login codes aloud (a voice from `say -v ?`)")
    p.add_argument("--name", action="append", default=[], help="an extra host name the page answers to")
    sub.add_parser("invite")
    p = sub.add_parser("qr")
    p.add_argument("url")
    sub.add_parser("logins")
    sub.add_parser("close-all")
    sub.add_parser("unlock")
    a = ap.parse_args(argv)
    clock = SystemClock()

    if a.cmd == "serve":
        import uvicorn

        from kore.web.app import DEFAULT_HOSTS, create_app
        if a.dev and a.host not in ("127.0.0.1", "localhost"):
            print("--dev is only for this computer (it prints login codes).", file=sys.stderr)
            return 2
        if a.host not in ("127.0.0.1", "localhost") and not a.name:
            print("Listening beyond this computer needs --name (the box's address), so other names are refused.",
                  file=sys.stderr)
            return 2
        from kore.family import channels
        from kore.web import app as web_app
        worker = _chat_worker(a.db, clock, a.dev)
        if a.voz:
            if sys.platform != "darwin":
                print("--voz uses the Mac's own voice.", file=sys.stderr)
                return 2
            web_app.MAC_VOICE = a.voz
            if worker is not None:
                worker.on_reply = lambda person_id, reply: web_app.mac_say(reply)
        app = create_app(a.db, clock, secure=False, hosts=DEFAULT_HOSTS + a.name, dev=a.dev,
                         chat=worker,
                         family_links={} if a.dev else channels.from_env())  # test mode never sends anything real
        uvicorn.run(app, host=a.host, port=a.port, log_level="warning", server_header=False)
        return 0
    conn = connect(a.db)
    if a.cmd == "invite":
        code = accounts.operator_code(conn, clock)
        print(f"Your code (10 minutes): {code}\nOpen the page, press «¿Tiene ya un código?» and type it.")
        return 0
    if a.cmd == "qr":
        import segno

        if not a.url.startswith(("http://", "https://")):
            print("The address must start with http:// or https://", file=sys.stderr)
            return 2
        out = Path("data/web")
        out.mkdir(parents=True, exist_ok=True)
        q = segno.make(a.url, error="m")
        q.save(out / "qr.svg", scale=10, border=2)
        q.save(out / "qr.png", scale=12, border=2)
        print(f"Saved {out / 'qr.svg'} and {out / 'qr.png'} for {a.url}")
        return 0
    if a.cmd == "logins":
        rows = conn.execute("SELECT l.id, a.name, a.role, l.device, l.created_at, l.last_seen_at FROM web_logins l "
                            "JOIN web_accounts a ON a.id = l.account_id WHERE l.revoked_at IS NULL "
                            "ORDER BY l.last_seen_at DESC").fetchall()
        for r in rows:
            print(f"{r['id']:4}  {r['name']:12} {r['role']:9} {r['device']:16} since {r['created_at'][:10]}  "
                  f"seen {r['last_seen_at'][:16]}")
        print(f"{len(rows)} logged in")
        return 0
    if a.cmd == "unlock":
        accounts.unlock(conn, clock)
        print("Logins open again.")
        return 0
    print(f"Logged out: {accounts.close_all(conn, clock)}")
    return 0


def _chat_worker(db: str, clock, dev: bool = False):
    """Talking to Claude from the page needs the key (run with --env-file .env --group live). Without it the
    Hablar page says writing isn't available now, or, with --dev, a placeholder answers (nothing is spent)."""
    import os

    client = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from kore.brain.clients import LiveClient
            client = LiveClient("web", daily_cap_usd=1.0)  # the chat's own share of the day
        except ImportError:
            print("The live group isn't installed (uv run --group live).", file=sys.stderr)
    if client is None and dev:
        from kore.brain.clients import ScriptedClient, text
        client = ScriptedClient(lambda req: text("(Modo de prueba: aquí contestaría Pepe. No se ha gastado nada.)"))
        print("Test mode: a placeholder answers in Hablar.", file=sys.stderr)
    if client is None:
        print("No ANTHROPIC_API_KEY: the Hablar page will say writing isn't available.", file=sys.stderr)
        return None
    from kore.brain import tools
    from kore.family import channels
    from kore.family.service import Family
    from kore.info import Info
    from kore.info.news import News, Rss
    from kore.info.weather import OpenMeteo, Weather
    from kore.web.chat import ChatWorker

    links = {} if dev else channels.from_env()  # test mode never sends anything real

    def context(conn, session):
        return tools.Context(conn, clock, session, family=Family(conn, clock, links),
                             info=Info(Weather(conn, clock, OpenMeteo()), News(conn, clock, Rss())))
    from kore.web import photos
    return ChatWorker(lambda: connect(db), clock, client, context, photo_dir=photos.folder(db))


if __name__ == "__main__":
    sys.exit(main())
