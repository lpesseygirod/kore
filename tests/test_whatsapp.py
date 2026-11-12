"""WhatsApp as a message channel (built, but switched off).

Off until TWILIO_WHATSAPP_FROM is set. Fake accounts only: nothing leaves the computer.
"""

import json
import sqlite3
import threading
import urllib.parse
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from kore.family import channels
from kore.family import messages as msgs
from kore.family.channels import FakeChannel, TwilioWhatsApp
from kore.memory import store
from kore.memory.db import connect
from tests.test_family import Home

LUCIA_WA = "+34600000001"


@pytest.fixture
def h(conn, clock, home):
    h = Home(conn, clock, home)
    h.wa = FakeChannel("whatsapp")
    h.family.channels["whatsapp"] = h.wa
    return h


def link(h, number=LUCIA_WA, contact=None):
    code = msgs.start_pairing(h.conn, h.clock, contact or h.lucia)
    h.wa.arrive(number, code)
    return msgs.poll_whatsapp(h.conn, h.clock, h.wa)


# --- switched off until it's set up -------------------------------------------------------------


def test_whatsapp_is_off_without_its_number():
    twilio = {"TWILIO_ACCOUNT_SID": "s", "TWILIO_AUTH_TOKEN": "a"}
    assert "whatsapp" not in channels.from_env(twilio)
    got = channels.from_env({**twilio, "TWILIO_WHATSAPP_FROM": " +34911000000\n", "TWILIO_WHATSAPP_TEMPLATE": "HX1"})
    assert set(got) == {"whatsapp"} and got["whatsapp"]._from == "whatsapp:+34911000000"
    assert "a" not in repr(got["whatsapp"]).replace("TwilioWhatsApp", "")


def test_nothing_changes_for_telegram_contacts(h):
    h.session.speaker_id = h.carmen
    h.family.send_message(h.lucia, "Hola", h.carmen)
    assert h.tg.sent and h.wa.sent == []


# --- linking a number --------------------------------------------------------------------------------


def test_the_pairing_code_links_her_number_and_then_her_messages_are_kept(h):
    assert link(h)["linked"] == 1
    row = h.conn.execute("SELECT whatsapp_number, telegram_chat_id FROM contacts WHERE id = ?", (h.lucia,)).fetchone()
    assert row["whatsapp_number"] == LUCIA_WA and row["telegram_chat_id"]  # Telegram stays for help
    h.wa.arrive(LUCIA_WA, "¡Hola mamá!")
    h.wa.arrive("+34699999999", "Soy un desconocido")
    stats = msgs.poll_whatsapp(h.conn, h.clock, h.wa)
    assert (stats["kept"], stats["ignored"]) == (1, 1)
    assert [r["text"] for r in msgs.unread(h.conn, h.carmen)] == ["¡Hola mamá!"]
    assert msgs.poll_whatsapp(h.conn, h.clock, h.wa)["kept"] == 0  # the same messages aren't kept twice


def test_a_photo_or_voice_note_is_said_as_such(h):
    link(h)
    h.wa.arrive(LUCIA_WA, None)
    msgs.poll_whatsapp(h.conn, h.clock, h.wa)
    assert "una foto o un audio" in msgs.unread(h.conn, h.carmen)[0]["text"]


def test_leas_own_link_is_not_made_by_whatsapp_and_her_code_still_works_on_telegram(h):
    code = msgs.start_pairing(h.conn, h.clock, None)
    h.wa.arrive("+34611111111", code)
    assert msgs.poll_whatsapp(h.conn, h.clock, h.wa)["linked"] == 0
    h.tg.push("999", code)
    assert msgs.poll(h.conn, h.clock, h.tg)["linked"] == 1


def test_one_number_one_contact(h):
    link(h)
    ana = store.add_contact(h.conn, h.clock, "Ana", "nieta", for_person=h.carmen)
    link(h, contact=ana)
    nums = dict(h.conn.execute("SELECT id, whatsapp_number FROM contacts WHERE id IN (?, ?)", (h.lucia, ana)).fetchall())
    assert nums == {h.lucia: None, ana: LUCIA_WA}


# --- sending -----------------------------------------------------------------------------------------------


def test_once_linked_her_messages_go_by_whatsapp_as_text_then_as_template(h):
    link(h)
    h.session.speaker_id = h.carmen
    assert h.family.send_message(h.lucia, "Un beso", h.carmen)["sent"]
    assert h.wa.sent == [(LUCIA_WA, "Carmen (con Pepe): Un beso")] and h.tg.sent == []
    h.clock.advance(hours=24)  # she hasn't written for a day: WhatsApp only takes the approved template
    h.family.send_message(h.lucia, "Otro", h.carmen)
    assert h.wa.windows == [True, False]


def test_help_goes_every_way_and_whatsapp_down_doesnt_stop_it(h):
    link(h)
    h.session.speaker_id = h.carmen
    h.wa.online = False
    assert h.brain.hear("Pepe, ayuda").startswith("Ya he avisado a Lucía")
    ways = {r["channel"]: r["status"] for r in h.out("help")}
    assert ways == {"telegram": "sent", "sms": "sent", "whatsapp": "pending"}


def test_missed_medicine_alert_goes_by_whatsapp_once_linked(h):
    link(h)
    h.family.missed_meds(h.carmen, 2)
    assert h.wa.sent and "no ha contestado" in h.wa.sent[0][1] and h.tg.sent == []


def test_a_message_accepted_then_undelivered_is_said_as_not_sent(h):
    h.conn.execute("UPDATE contacts SET telegram_chat_id = NULL WHERE id = ?", (h.lucia,))  # WhatsApp only
    link(h)
    h.session.speaker_id = h.carmen
    assert h.family.send_message(h.lucia, "Un beso", h.carmen)["sent"]
    ref = h.out("message")[0]["provider_ref"]
    h.wa.statuses[ref] = "undelivered"
    events = h.family.tick()
    assert [e.kind for e in events] == ["message_not_sent"] and events[0].person_ids == [h.carmen]
    assert h.out("message")[0]["status"] == "failed"
    assert any("no ha llegado" in r["text"] for r in h.out("operator"))
    assert h.family.tick() == []  # said once


def test_r_a_message_whatsapp_failed_goes_again_by_telegram_silently(h):
    link(h)
    h.session.speaker_id = h.carmen
    h.family.send_message(h.lucia, "Un beso", h.carmen)  # they heard "sent"
    h.wa.statuses[h.out("message")[0]["provider_ref"]] = "undelivered"
    assert h.family.tick() == []
    assert h.family.tick() == [] and h.tg.sent == [("111", "Carmen (con Pepe): Un beso")]


def test_r_and_if_telegram_fails_too_they_hear_it_couldnt_go(h):
    link(h)
    h.session.speaker_id = h.carmen
    h.family.send_message(h.lucia, "Un beso", h.carmen)
    h.wa.statuses[h.out("message")[0]["provider_ref"]] = "undelivered"
    h.tg.reject.add("111")
    h.family.tick()
    assert [e.kind for e in h.family.tick()] == ["message_not_sent"]


def test_r1_help_heard_as_sent_by_whatsapp_alone_is_said_failed_when_all_fails(h):
    link(h)
    h.session.speaker_id = h.carmen
    h.tg.online = h.sms.online = False
    assert h.brain.hear("Pepe, ayuda").startswith("Ya he avisado a Lucía")  # only WhatsApp took it
    wa_row = [r for r in h.out("help") if r["channel"] == "whatsapp"][0]
    h.wa.statuses[wa_row["provider_ref"]] = "undelivered"
    assert h.family.tick() == []  # Telegram and SMS are still being tried
    h.tg.online = h.sms.online = True
    h.tg.reject.add("111")
    h.sms.reject.add(LUCIA_WA)
    h.clock.advance(minutes=1)
    assert [e.kind for e in h.family.tick()] == ["help_not_sent"]


def test_r1_and_if_telegram_gets_through_later_nothing_more_is_said(h):
    link(h)
    h.session.speaker_id = h.carmen
    h.tg.online = h.sms.online = False
    h.brain.hear("Pepe, ayuda")
    wa_row = [r for r in h.out("help") if r["channel"] == "whatsapp"][0]
    h.wa.statuses[wa_row["provider_ref"]] = "undelivered"
    h.family.tick()
    h.tg.online = h.sms.online = True
    h.clock.advance(minutes=1)
    assert h.family.tick() == [] and h.tg.sent


def test_r3_a_missed_medicine_alert_whatsapp_failed_goes_by_telegram(h):
    link(h)
    h.family.missed_meds(h.carmen, 2)
    h.wa.statuses[h.out("missed_meds")[0]["provider_ref"]] = "failed"
    h.family.tick()
    h.family.tick()
    assert h.tg.sent and "no ha contestado" in h.tg.sent[0][1]
    assert not any("no se ha podido enviar" in r["text"] for r in h.out("operator"))


def test_r6_sent_late_waits_until_whatsapp_confirms(h):
    h.conn.execute("UPDATE contacts SET telegram_chat_id = NULL WHERE id = ?", (h.lucia,))
    link(h)
    h.session.speaker_id = h.carmen
    h.wa.online = False
    assert not h.family.send_message(h.lucia, "Un beso", h.carmen)["sent"]  # they heard "not yet"
    h.wa.online = True
    h.clock.advance(minutes=5)
    h.wa.statuses = _Pending()  # Twilio says "sent" (on its way), not yet "delivered"
    assert h.family.tick() == []  # accepted, not yet arrived: nothing said
    h.wa.statuses = dict(h.wa.statuses)  # now it says what was recorded: delivered
    assert [e.kind for e in h.family.tick()] == ["message_sent_late"]


class _Pending(dict):
    def get(self, key, default=None):
        return "sent"


def test_r2_without_whatsapp_set_up_telegram_contacts_get_telegram(h):
    link(h)
    del h.family.channels["whatsapp"]  # the number was taken out of .env
    h.session.speaker_id = h.carmen
    assert h.family.send_message(h.lucia, "Un beso", h.carmen)["sent"] and h.tg.sent
    h.family.missed_meds(h.carmen, 2)
    assert len(h.tg.sent) == 2


def test_r2_whatsapp_needs_its_template_to_be_on():
    env = {"TWILIO_ACCOUNT_SID": "s", "TWILIO_AUTH_TOKEN": "a", "TWILIO_WHATSAPP_FROM": "+34911000000"}
    assert "whatsapp" not in channels.from_env(env)


def test_r4_the_24_hours_count_from_when_she_wrote_not_when_the_box_read_it(h):
    from email.utils import format_datetime
    link(h)
    h.clock.advance(hours=24)
    when = format_datetime(h.clock.now().astimezone(__import__("datetime").timezone.utc) - timedelta(hours=20))
    h.wa.incoming.append(channels.Incoming("SMlate", LUCIA_WA, "Escrito hace 20 horas", when))
    msgs.poll_whatsapp(h.conn, h.clock, h.wa)
    h.clock.advance(hours=4)  # 24 hours after she wrote
    h.session.speaker_id = h.carmen
    h.family.send_message(h.lucia, "Hola", h.carmen)
    assert h.wa.windows[-1] is False


def test_a_delivered_message_stops_being_checked(h):
    link(h)
    h.session.speaker_id = h.carmen
    h.family.send_message(h.lucia, "Un beso", h.carmen)
    h.family.tick()
    assert h.out("message")[0]["provider_ref"] is None and h.out("message")[0]["status"] == "sent"


def test_help_that_got_through_by_telegram_is_not_called_failed_when_whatsapp_fails_later(h):
    link(h)
    h.session.speaker_id = h.carmen
    h.brain.hear("Pepe, ayuda")
    wa_row = [r for r in h.out("help") if r["channel"] == "whatsapp"][0]
    h.wa.statuses[wa_row["provider_ref"]] = "failed"
    assert [e.kind for e in h.family.tick()] == []


# --- the web pages ---------------------------------------------------------------------------------------


def test_carmen_can_write_to_a_whatsapp_only_contact_from_her_page(h):
    from kore.web import elder

    ana = store.add_contact(h.conn, h.clock, "Ana", "nieta", for_person=h.carmen)
    assert not [c for c in elder.contacts(h.conn, h.carmen) if c["name"] == "Ana"]
    link(h, "+34622222222", ana)
    assert [c["can_write"] for c in elder.contacts(h.conn, h.carmen) if c["name"] == "Ana"] == [True]
    elder.propose_reply(h.conn, h.clock, h.carmen, ana, "Hola")  # no "has to connect first"


# --- the old database ---------------------------------------------------------------------------------------


def test_an_old_database_takes_whatsapp_and_keeps_its_messages(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE family_out (id INTEGER PRIMARY KEY, created_at_utc TEXT NOT NULL, kind TEXT NOT NULL,
          contact_id INTEGER, channel TEXT NOT NULL CHECK (channel IN ('telegram', 'sms')), text TEXT NOT NULL,
          from_person INTEGER, status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
          attempts INTEGER NOT NULL DEFAULT 0, next_try_utc TEXT NOT NULL, expires_at_utc TEXT NOT NULL,
          sent_at_utc TEXT, error TEXT, group_id INTEGER, told INTEGER NOT NULL DEFAULT 0);
        INSERT INTO family_out (created_at_utc, kind, channel, text, status, next_try_utc, expires_at_utc)
          VALUES ('2026-11-01T10:00:00+00:00', 'operator', 'telegram', 'hola', 'sent', 'x', 'y');
    """)
    raw.close()
    conn = connect(path)
    assert conn.execute("SELECT text, provider_ref FROM family_out").fetchone()[:] == ("hola", None)
    conn.execute("INSERT INTO family_out (created_at_utc, kind, channel, text, status, next_try_utc, expires_at_utc) "
                 "VALUES ('t', 'message', 'whatsapp', 'x', 'pending', 'x', 'y')")
    conn.close()
    assert connect(path).execute("SELECT count(*) FROM family_out").fetchone()[0] == 2  # second open: no copy again


# --- the real Twilio channel, against a server on this computer --------------------------------------------------


class Twilio(BaseHTTPRequestHandler):
    seen: list = []
    reply: dict = {}

    def _answer(self, body):
        data = json.dumps(body).encode()
        self.send_response(201 if self.command == "POST" else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        Twilio.seen.append((self.path, dict(urllib.parse.parse_qsl(self.rfile.read(n).decode())),
                            self.headers["Authorization"]))
        self._answer({"sid": "SM123", "status": "queued"})

    def do_GET(self):
        Twilio.seen.append((self.path, None, self.headers["Authorization"]))
        self._answer(Twilio.reply)

    def log_message(self, *a):
        pass


@pytest.fixture
def twilio():
    Twilio.seen = []
    srv = HTTPServer(("127.0.0.1", 0), Twilio)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield TwilioWhatsApp("AC1", "SECRET", "+34911000000", "HXabc", base=f"http://127.0.0.1:{srv.server_port}",
                         timeout=2)
    srv.shutdown()


def test_real_send_plain_text_in_the_window_and_the_template_outside(twilio):
    r = twilio.send(LUCIA_WA, "Hola", in_window=True)
    assert r.ok and r.ref == "SM123"
    path, fields, auth = Twilio.seen[-1]
    assert path == "/2010-04-01/Accounts/AC1/Messages.json" and auth.startswith("Basic ")
    assert fields == {"From": "whatsapp:+34911000000", "To": "whatsapp:" + LUCIA_WA, "Body": "Hola"}
    twilio.send(LUCIA_WA, "Línea uno\n\nlínea   dos", in_window=False)
    fields = Twilio.seen[-1][1]
    assert "Body" not in fields and fields["ContentSid"] == "HXabc"
    assert json.loads(fields["ContentVariables"]) == {"1": "Línea uno línea dos"}


def test_real_no_template_outside_the_window_is_a_plain_failure_and_nothing_is_sent(twilio):
    twilio._template = ""
    r = twilio.send(LUCIA_WA, "Hola")
    assert (r.ok, r.error, r.permanent) == (False, "no_template", True) and Twilio.seen == []


def test_real_status_and_incoming(twilio):
    Twilio.reply = {"status": "undelivered"}
    assert twilio.status("SM123") == "undelivered" and Twilio.seen[-1][0].endswith("/Messages/SM123.json")
    Twilio.reply = {"messages": [
        {"sid": "SM3", "from": "whatsapp:" + LUCIA_WA, "body": "segundo", "direction": "inbound", "num_media": "0"},
        {"sid": "SM2", "from": "whatsapp:+34911000000", "body": "de Pepe", "direction": "outbound-api"},
        {"sid": "SM1", "from": "whatsapp:" + LUCIA_WA, "body": "", "direction": "inbound", "num_media": "1"},
        {"sid": "SM0", "from": "+34600000009", "body": "un SMS", "direction": "inbound"}]}
    got, reached = twilio.received("2026-11-08")
    assert reached and [(m.ref, m.text) for m in got] == [("SM1", None), ("SM3", "segundo")]
    q = urllib.parse.parse_qs(urllib.parse.urlparse(Twilio.seen[-1][0]).query)
    assert q == {"To": ["whatsapp:+34911000000"], "DateSent>": ["2026-11-08"], "PageSize": ["100"]}


def test_real_no_connection_is_a_plain_failure_without_the_key():
    wa = TwilioWhatsApp("AC1", "SECRET", "+34911000000", "HX", base="http://127.0.0.1:9", timeout=1)
    r = wa.send(LUCIA_WA, "hola", in_window=True)
    assert (r.ok, r.error, r.permanent) == (False, "no_connection", False)
    assert "SECRET" not in repr(wa) and "SECRET" not in repr(r)
    assert wa.received("2026-11-08") == ([], False) and wa.status("SM1") is None


def test_r5_two_programs_opening_an_old_database_at_once_both_start(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE contacts (id INTEGER PRIMARY KEY, name TEXT NOT NULL, relation TEXT NOT NULL DEFAULT '',
          birthday_md TEXT, birthday_reminders INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE family_out (id INTEGER PRIMARY KEY, created_at_utc TEXT NOT NULL, kind TEXT NOT NULL,
          contact_id INTEGER, channel TEXT NOT NULL CHECK (channel IN ('telegram', 'sms')), text TEXT NOT NULL,
          from_person INTEGER, status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
          attempts INTEGER NOT NULL DEFAULT 0, next_try_utc TEXT NOT NULL, expires_at_utc TEXT NOT NULL,
          sent_at_utc TEXT, error TEXT, group_id INTEGER, told INTEGER NOT NULL DEFAULT 0);
    """)
    raw.close()
    errors = []

    def open_it():
        try:
            connect(path).close()
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=open_it) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
