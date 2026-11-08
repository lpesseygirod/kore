"""Lea's controls for the home web page, from the command line (on the box, or the Mac for now).

  serve [--host H] [--port P] [--dev]   run the page; --dev prints what Pepe would say (no speaker yet)
  invite                                a 10-minute code for Lea's own login (printed, never spoken)
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
        if a.host not in ("127.0.0.1", "localhost") and not a.name:
            print("Listening beyond this computer needs --name (the box's address), so other names are refused.",
                  file=sys.stderr)
            return 2
        app = create_app(a.db, clock, secure=False, hosts=DEFAULT_HOSTS + a.name, dev=a.dev)
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


if __name__ == "__main__":
    sys.exit(main())
