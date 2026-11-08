"""Lea's controls for the family links, from the command line.

Keys come from .env (run with `uv run --env-file .env python -m kore.family ...`); they are
never printed. Every command that sends something real needs --yes.

  status                         what is set up, what is missing, what is waiting
  contacts                       the saved family contacts and their links
  add-contact NAME [--relation R] [--help-for PERSON]   e.g. yourself, as a test contact
  pair --contact ID | --lea      link a Telegram chat (or, once set up, a WhatsApp number) with a one-time code
  phone ID +34600111222          the phone number for the SMS in "help"
  test-send ID --yes [--sms]     one test message of each kind to that contact (use your own phone first)
  heartbeat --yes                one ping now
  backup --yes                   one encrypted backup now (sent to your chat if linked)
  restore FILE --key K --cert C --out OUT   decrypt a backup on your computer

--db PATH picks the database (default data/kore.db).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from kore.family import backup, channels, heartbeat
from kore.family import messages as msgs
from kore.family.service import Family
from kore.memory import store
from kore.memory.db import connect
from kore.scheduler.clock import SystemClock


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kore.family")
    ap.add_argument("--db", default="data/kore.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("contacts")
    p = sub.add_parser("pair")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--contact", type=int)
    g.add_argument("--lea", action="store_true")
    p = sub.add_parser("add-contact")
    p.add_argument("name")
    p.add_argument("--relation", default="")
    p.add_argument("--help-for", help="the name of the person at home they are a help contact for")
    p = sub.add_parser("phone")
    p.add_argument("contact", type=int)
    p.add_argument("number")
    p = sub.add_parser("test-send")
    p.add_argument("contact", type=int)
    p.add_argument("--sms", action="store_true")
    p.add_argument("--whatsapp", action="store_true", help="also one by WhatsApp (needs the contact linked there)")
    p.add_argument("--yes", action="store_true")
    for name in ("heartbeat", "backup"):
        sub.add_parser(name).add_argument("--yes", action="store_true")
    p = sub.add_parser("restore")
    p.add_argument("file")
    p.add_argument("--key", required=True)
    p.add_argument("--cert", required=True)
    p.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "restore":
        if Path(a.out).exists():
            print(f"{a.out} already exists: pick a new name (never over the live database).", file=sys.stderr)
            return 1
        try:
            backup.decrypt(Path(a.file), Path(a.key), Path(a.cert), Path(a.out))
        except backup.BackupError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"Restored to {a.out}")
        return 0
    if not Path(a.db).exists() and a.cmd != "add-contact":  # adding the first contact may start a test database
        print(f"No database at {a.db}", file=sys.stderr)
        return 1
    conn, clock = connect(a.db), SystemClock()
    chans = channels.from_env()
    fam = Family(conn, clock, chans)
    try:
        if a.cmd == "status":
            return _status(conn, clock, chans)
        if a.cmd == "contacts":
            for c in conn.execute("SELECT * FROM contacts ORDER BY id"):
                print(f"#{c['id']:<3} {c['name']:12} {c['relation'] or '':10} help={'yes' if c['help_contact'] else 'no':3}"
                      f" telegram={'linked' if c['telegram_chat_id'] else '-':6} whatsapp={'linked' if c['whatsapp_number'] else '-':6}"
                      f" phone={'set' if c['phone'] else '-'}")
            return 0
        if a.cmd == "add-contact":
            pid = None
            if a.help_for:
                row = conn.execute("SELECT id FROM people WHERE name = ? OR call_name = ?",
                                   (a.help_for, a.help_for)).fetchone()
                if row is None:
                    print(f"Nobody called {a.help_for} lives here.", file=sys.stderr)
                    return 1
                pid = row["id"]
            cid = store.add_contact(conn, clock, a.name, a.relation, help_contact=pid is not None, for_person=pid)
            print(f"Saved as contact #{cid}.")
            return 0
        if a.cmd == "phone":
            msgs.set_phone(conn, a.contact, a.number)
            print("Saved.")
            return 0
        if a.cmd == "pair":
            return _pair(conn, clock, chans, None if a.lea else a.contact)
        if not a.yes:
            print("This sends something real. Add --yes to go ahead.", file=sys.stderr)
            return 2
        if a.cmd == "test-send":
            return _test_send(conn, clock, fam, a.contact, a.sms, a.whatsapp)
        if a.cmd == "heartbeat":
            hb = heartbeat.Heartbeat.from_env(clock)
            if not hb.configured:
                print(f"Set {heartbeat.URL_VAR} in .env first.", file=sys.stderr)
                return 1
            print("Ping sent." if hb.tick() else "Ping failed (no connection, or a wrong URL).")
            return 0
        if a.cmd == "backup":
            cert = Path(os.environ.get(backup.CERT_VAR) or backup.DEFAULT_CERT)
            try:
                path = backup.make(conn, clock, cert)
            except backup.BackupError as e:
                print(f"Backup failed: {e}", file=sys.stderr)
                return 1
            chat = msgs.get_state(conn, msgs.OPERATOR_CHAT)
            if chat and "telegram" in chans:
                ok = chans["telegram"].send_document(chat, path.name, path.read_bytes(), "Copia cifrada de Pepe").ok
                print(f"Backup {path} {'sent to your chat' if ok else 'made, but not sent (no connection?)'}.")
            else:
                print(f"Backup {path} made (link your chat with `pair --lea` to receive them).")
            return 0
    finally:
        conn.close()
    return 0


def _status(conn, clock, chans) -> int:
    print("Keys in .env (names only):")
    for var in (channels.TELEGRAM_TOKEN_VAR, channels.TWILIO_SID_VAR, channels.TWILIO_TOKEN_VAR,
                channels.TWILIO_FROM_VAR, channels.WHATSAPP_FROM_VAR, channels.WHATSAPP_TEMPLATE_VAR, heartbeat.URL_VAR):
        print(f"  {var:22} {'set' if os.environ.get(var) else 'missing'}")
    print(f"Lea's chat linked: {'yes' if msgs.get_state(conn, msgs.OPERATOR_CHAT) else 'no'}")
    helpers = conn.execute("SELECT * FROM contacts WHERE help_contact = 1").fetchall()
    if not helpers:
        print("No help contact saved yet (setup asks for one).")
    for c in helpers:
        missing = [w for w, ok in (("Telegram or WhatsApp", msgs.writing_channel(c)), ("phone", c["phone"])) if not ok]
        print(f"Help contact #{c['id']} {c['name']}: " + ("ready" if not missing else "missing " + ", ".join(missing)))
    for r in conn.execute("SELECT kind, status, count(*) AS n FROM family_out GROUP BY kind, status"):
        print(f"  {r['kind']:12} {r['status']:8} {r['n']}")
    print(f"Unread family messages: {conn.execute('SELECT count(*) FROM family_in WHERE read_at_utc IS NULL').fetchone()[0]}")
    print(f"Backup sent in the last two days: {'no' if backup.overdue(conn, clock) else 'yes'}")
    return 0


def _pair(conn, clock, chans, contact_id) -> int:
    wa = chans.get("whatsapp") if contact_id is not None else None  # Lea's own link stays Telegram
    if "telegram" not in chans and wa is None:
        print(f"Set {channels.TELEGRAM_TOKEN_VAR} in .env first.", file=sys.stderr)
        return 1
    code = msgs.start_pairing(conn, clock, contact_id)
    who = "you" if contact_id is None else msgs.contact_name(conn, contact_id)
    where = " or ".join(w for w, on in (("the bot in Telegram", "telegram" in chans),
                                        ("Pepe's WhatsApp number", wa is not None)) if on)
    print(f"Ask {who} to send this code to {where}: {code}  (valid 30 minutes)")
    print("Waiting... (Ctrl-C to stop)")
    deadline = time.time() + 600
    while time.time() < deadline:
        stats = {"linked": 0}
        if "telegram" in chans:
            stats = msgs.poll(conn, clock, chans["telegram"])
        if wa is not None and not stats["linked"]:
            stats = msgs.poll_whatsapp(conn, clock, wa)
        if stats["linked"]:
            print("Linked.")
            return 0
        time.sleep(3)
    print("No code arrived. Run it again for a new code.")
    return 1


def _test_send(conn, clock, fam: Family, contact_id: int, sms: bool, whatsapp: bool = False) -> int:
    name = msgs.contact_name(conn, contact_id)
    if name is None:
        print(f"No contact {contact_id}", file=sys.stderr)
        return 1
    tag = "PRUEBA (no es real): "
    samples = [("help", "telegram", tag + "AYUDA: Carmen ha pedido ayuda a Pepe a las 10:42. Llama en cuanto puedas. "
                "Pepe no es un servicio de emergencias: si no contesta y te preocupa, llama al 112."),
               ("missed_meds", "telegram", tag + "Pepe: Carmen no ha contestado a los recordatorios de sus últimas 2 "
                "tomas de medicación. Quizá puedas llamar."),
               ("stop_request", "telegram", tag + "Pepe: Carmen ha pedido dos días distintos dejar de recibir el "
                "recordatorio de la pastilla de la tensión, y lo he quitado."),
               ("message", "telegram", tag + "Carmen (con Pepe): Me han encantado las fotos.")]
    if sms:
        samples.append(("help", "sms", tag + "AYUDA: Carmen ha pedido ayuda a Pepe a las 10:42. Llama en cuanto puedas."))
    if whatsapp:
        samples.append(("message", "whatsapp", tag + "Carmen (con Pepe): Me han encantado las fotos."))
    ids = [msgs.queue(conn, clock, "test", ch, text, contact_id) for _, ch, text in samples]
    for a, (kind, ch, _) in zip(msgs.deliver(conn, clock, fam.channels, ids), samples):
        print(f"  {kind:12} {ch:8} {'sent' if a.ok else 'NOT sent: ' + str(a.error)}")
    if whatsapp and "whatsapp" in fam.channels:
        time.sleep(8)  # Twilio accepts first and may fail it a few seconds later
        late = msgs.check_whatsapp(conn, clock, fam.channels["whatsapp"])
        print("  whatsapp: " + ("NOT delivered (no WhatsApp on that number, or the template isn't approved)"
                                if late else "no failure reported so far"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
