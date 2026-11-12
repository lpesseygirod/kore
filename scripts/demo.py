"""Offline demo of the home web page: made-up people, no keys, nothing sent.

    uv run python scripts/demo.py [--port 8080]

Seeds a throwaway database with Carmen and Antonio (made-up), their medicines,
appointments, family and shopping list, then serves the page in test mode
(the chat answers with a placeholder; nothing reaches Claude, SMS or Telegram).
Log in as Carmen (Pepe "says" her code in this terminal) or open the admin
console with the code it prints.
The clock is fixed at 11:00 today, home time, so the page looks the same
whenever you run it (at night Pepe would refuse to say login codes).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kore.memory import meds, store  # noqa: E402
from kore.memory.db import connect  # noqa: E402
from kore.scheduler.clock import HOME_TZ, FakeClock  # noqa: E402
from kore.web import accounts  # noqa: E402

CONSENTS = ("health_info", "share_with_partner", "keep_conversations", "lea_sees_day", "family_sees_doses",
            "emergency_path", "listening")


def seed(db: str, clock: FakeClock) -> int:
    conn = connect(db)
    carmen = meds.add_person(conn, clock, "Carmen")
    antonio = meds.add_person(conn, clock, "Antonio", "Toni")
    for p in (carmen, antonio):
        for c in CONSENTS:
            store.set_consent(conn, clock, p, c, True)
    tension = meds.add_medication(conn, clock, carmen, "tensión", ["09:00"], "la pastilla de la tensión")
    meds.add_medication(conn, clock, carmen, "sintrom", ["20:00"], "el sintrom")
    meds.add_medication(conn, clock, antonio, "azúcar", ["09:00", "21:00"], "la pastilla del azúcar")
    meds.mark_taken(conn, clock, carmen, tension)
    today = clock.now().replace(minute=0, second=0, microsecond=0)
    store.add_appointment(conn, clock, carmen, "Médico de cabecera", today + timedelta(days=2, hours=-1),
                          "Centro de salud")
    store.add_appointment(conn, clock, antonio, "Podólogo", today + timedelta(days=5, hours=6))
    store.add_reminder(conn, clock, None, "Regar las plantas", "18:00", "weekly:MON,THU", today.date())
    store.add_contact(conn, clock, "Lucía", "nieta", "03-14", help_contact=True)
    store.add_contact(conn, clock, "Rosa", "hija", "07-22", help_contact=True)
    store.add_to_list(conn, clock, ["leche", "pan", "fruta", "aceite"])
    store.add_note(conn, clock, carmen, "interest", "Le gusta hacer crucigramas por la mañana")
    conn.commit()
    return carmen


def admin_code(db: str, clock: FakeClock) -> str:
    """A 10-minute code for the admin console (on the box this comes from `python -m kore.web invite`)."""
    conn = connect(db)
    code = accounts.operator_code(conn, clock)
    conn.commit()
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()

    import uvicorn

    from kore.web.__main__ import _chat_worker
    from kore.web.app import create_app

    db = str(Path(tempfile.mkdtemp(prefix="kore-demo-")) / "kore.db")
    eleven = datetime.now(HOME_TZ).replace(hour=11, minute=0, second=0, microsecond=0)
    clock = FakeClock(eleven)
    seed(db, clock)
    admin = admin_code(db, clock)
    app = create_app(db, clock, secure=False, dev=True, chat=_chat_worker(db, clock, dev=True), family_links={})
    print(f"KORE demo on http://127.0.0.1:{a.port}  (made-up people, test mode, nothing is sent)")
    print("  Carmen's app:   choose Carmen and ask for a code; what Pepe would say aloud prints below")
    print(f"  Admin console:  open /entrar and type {admin} (valid 10 minutes)")
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
