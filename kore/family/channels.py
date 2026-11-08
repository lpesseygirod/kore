"""The ways a message leaves the home: one interface, swappable (BUILD-PLAN 0b.12).

- Telegram (a bot): free, two-way. Family messages, help alerts, missed-medicine alerts.
- SMS (Twilio): only for "help", as a second path.
- WhatsApp (Twilio's WhatsApp service, Lea 2026-11-08: "build it, switched off"): a number of the box's own.
  Off until Lea sets TWILIO_WHATSAPP_FROM and TWILIO_WHATSAPP_TEMPLATE. Twilio, not Meta's own Cloud API, because Meta delivers replies only
  to a web address reachable from the internet, and the box stays on the home Wi-Fi: Twilio keeps them, and the
  box asks for them, like Telegram. Outside 24 hours since the family member last wrote, WhatsApp only takes a
  template Meta approved (TWILIO_WHATSAPP_TEMPLATE, one body variable). Help never depends on it.
- FakeChannel: for tests and the simulator. Nothing leaves the computer.

Plain standard library (urllib): no new dependency. Keys come from environment
variables, whose names are below; their values are never logged, printed or put
in an error (the Telegram token is part of the URL, so errors never include it).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from base64 import b64encode
from dataclasses import dataclass, field
from typing import Protocol

TELEGRAM_TOKEN_VAR = "TELEGRAM_BOT_TOKEN"
TWILIO_SID_VAR = "TWILIO_ACCOUNT_SID"
TWILIO_TOKEN_VAR = "TWILIO_AUTH_TOKEN"
TWILIO_FROM_VAR = "TWILIO_FROM_NUMBER"
WHATSAPP_FROM_VAR = "TWILIO_WHATSAPP_FROM"          # the box's WhatsApp number, +34...
WHATSAPP_TEMPLATE_VAR = "TWILIO_WHATSAPP_TEMPLATE"  # the approved template's id (HX...), one variable: the text

WHATSAPP_OK = frozenset({"delivered", "read"})                   # how a WhatsApp message ended (Twilio)
WHATSAPP_BAD = frozenset({"failed", "undelivered", "canceled"})

TIMEOUT_S = 10
MAX_TEXT = 1000  # Telegram allows 4096; family messages read aloud stay short anyway


@dataclass
class Sent:
    ok: bool
    error: str | None = None  # no_connection | rejected | not_configured | busy
    permanent: bool = False   # trying again won't help (wrong chat, wrong number, bad key)
    ref: str | None = None    # the provider's id for it, when it can still fail later (WhatsApp)


@dataclass
class Update:
    """One message that reached the bot."""
    update_id: int
    chat_id: str
    text: str | None          # None: a photo, a voice note, a sticker...
    from_name: str = ""


@dataclass
class Incoming:
    """One WhatsApp message to the box's number."""
    ref: str                  # Twilio's message id: each is kept once
    number: str               # +34...
    text: str | None          # None: a photo, a voice note...
    sent_at_utc: str = ""


class Channel(Protocol):
    name: str

    def send(self, address: str, text: str) -> Sent: ...


# --- fakes (tests, simulator) ------------------------------------------------------


@dataclass
class FakeChannel:
    """Records what would have been sent. `online = False` is the internet unplugged."""
    name: str = "telegram"
    online: bool = True
    sent: list[tuple[str, str]] = field(default_factory=list)
    documents: list[tuple[str, str, bytes]] = field(default_factory=list)
    updates: list[Update] = field(default_factory=list)
    reject: set[str] = field(default_factory=set)  # addresses that don't exist
    # WhatsApp only: how each went (free text or template), what arrived, and what failed after being accepted
    windows: list[bool] = field(default_factory=list)
    incoming: list[Incoming] = field(default_factory=list)
    statuses: dict[str, str] = field(default_factory=dict)

    def send(self, address: str, text: str, in_window: bool | None = None) -> Sent:
        if not self.online:
            return Sent(False, "no_connection")
        if address in self.reject:
            return Sent(False, "rejected", permanent=True)
        self.sent.append((address, text))
        if in_window is not None:
            self.windows.append(in_window)
            ref = f"SM{len(self.sent):032d}"
            self.statuses.setdefault(ref, "delivered")
            return Sent(True, ref=ref)
        return Sent(True)

    def status(self, ref: str) -> str | None:
        return self.statuses.get(ref) if self.online else None

    def received(self, since_utc_date: str) -> tuple[list[Incoming], bool]:
        if not self.online:
            return [], False
        return list(self.incoming), True

    def arrive(self, number: str, text: str | None) -> str:
        """A WhatsApp message reaches the box (test helper)."""
        ref = f"SMin{len(self.incoming):030d}"
        self.incoming.append(Incoming(ref, number, text))
        return ref

    def send_document(self, address: str, filename: str, data: bytes, caption: str = "") -> Sent:
        if not self.online:
            return Sent(False, "no_connection")
        self.documents.append((address, filename, data))
        return Sent(True)

    def poll(self, offset: int | None) -> tuple[list[Update], bool]:
        if not self.online:
            return [], False
        out = [u for u in self.updates if offset is None or u.update_id >= offset]
        return out, True

    def push(self, chat_id: str, text: str | None, from_name: str = "") -> None:
        """A message arrives at the bot (test helper)."""
        uid = (max((u.update_id for u in self.updates), default=0)) + 1
        self.updates.append(Update(uid, str(chat_id), text, from_name))


# --- real ones ---------------------------------------------------------------------


def _request(url: str, data: bytes | None, headers: dict, timeout: float) -> tuple[int, dict | None]:
    """(HTTP status, JSON body). 0 means no connection (or a malformed key or URL). Never raises,
    never echoes the URL: the Telegram token and the heartbeat URL are secrets."""
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, _json(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = _json(e.read())
        except Exception:
            body = None
        return e.code, body
    except Exception:  # no connection, a bad URL, a key with a stray character: no details, ever
        return 0, None


def _json(raw: bytes) -> dict | None:
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _result(status: int) -> Sent:
    if 200 <= status < 300:
        return Sent(True)
    if status == 0:
        return Sent(False, "no_connection")
    if status in (401, 404):
        return Sent(False, "not_configured", permanent=True)  # bad key (Telegram answers 404 to a bad token)
    if status in (400, 403):
        return Sent(False, "rejected", permanent=True)  # chat not found, bot blocked, bad number
    return Sent(False, "busy")  # 429, 5xx: try again later


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str, base: str = "https://api.telegram.org", timeout: float = TIMEOUT_S):
        self._url = f"{base}/bot{token}/"
        self._timeout = timeout

    def __repr__(self) -> str:  # never show the token
        return "TelegramChannel()"

    def send(self, address: str, text: str) -> Sent:
        body = json.dumps({"chat_id": address, "text": text[:MAX_TEXT]}).encode()
        status, _ = _request(self._url + "sendMessage", body, {"Content-Type": "application/json"}, self._timeout)
        return _result(status)

    def send_document(self, address: str, filename: str, data: bytes, caption: str = "") -> Sent:
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (("chat_id", address), ("caption", caption)):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        status, _ = _request(self._url + "sendDocument", b"".join(parts),
                             {"Content-Type": f"multipart/form-data; boundary={boundary}"}, 60)
        return _result(status)

    def poll(self, offset: int | None) -> tuple[list[Update], bool]:
        """New messages to the bot since `offset` (no waiting). (updates, reached Telegram)."""
        q = urllib.parse.urlencode({"timeout": 0, **({"offset": offset} if offset is not None else {})})
        status, body = _request(self._url + "getUpdates?" + q, None, {}, self._timeout)
        if status != 200 or not body or not body.get("ok"):
            return [], False
        out = []
        for u in body.get("result", []):
            msg = u.get("message") or {}
            chat = msg.get("chat") or {}
            if "id" not in chat:
                continue
            sender = msg.get("from") or {}
            out.append(Update(int(u["update_id"]), str(chat["id"]), msg.get("text"),
                              sender.get("first_name", "")))
        return out, True


class TwilioSms:
    name = "sms"

    def __init__(self, account_sid: str, auth_token: str, from_number: str, base: str = "https://api.twilio.com",
                 timeout: float = TIMEOUT_S):
        self._url = f"{base}/2010-04-01/Accounts/{urllib.parse.quote(account_sid)}/Messages.json"
        self._auth = "Basic " + b64encode(f"{account_sid}:{auth_token}".encode()).decode()
        self._from = from_number
        self._timeout = timeout

    def __repr__(self) -> str:
        return "TwilioSms()"

    def send(self, address: str, text: str) -> Sent:
        body = urllib.parse.urlencode({"From": self._from, "To": address, "Body": text[:600]}).encode()
        status, _ = _request(self._url, body, {"Authorization": self._auth,
                                               "Content-Type": "application/x-www-form-urlencoded"}, self._timeout)
        return _result(status)


class TwilioWhatsApp:
    """WhatsApp through Twilio. Twilio accepts a message first and may fail it later (a number without
    WhatsApp, a closed window), so `send` returns its id and `status` tells how it ended."""
    name = "whatsapp"

    def __init__(self, account_sid: str, auth_token: str, from_number: str, template: str = "",
                 base: str = "https://api.twilio.com", timeout: float = TIMEOUT_S):
        self._base = f"{base}/2010-04-01/Accounts/{urllib.parse.quote(account_sid)}/Messages"
        self._auth = "Basic " + b64encode(f"{account_sid}:{auth_token}".encode()).decode()
        self._from = "whatsapp:" + from_number
        self._template = template
        self._timeout = timeout

    def __repr__(self) -> str:
        return "TwilioWhatsApp()"

    def send(self, address: str, text: str, in_window: bool | None = False) -> Sent:
        """in_window: the person wrote to the box in the last 24 hours, so plain text is allowed."""
        fields = {"From": self._from, "To": "whatsapp:" + address}
        if in_window:
            fields["Body"] = text[:MAX_TEXT]
        elif self._template:
            # Template variables can't hold line breaks or long runs of spaces (Meta's rule).
            flat = " ".join(text.split())[:900]
            fields["ContentSid"] = self._template
            fields["ContentVariables"] = json.dumps({"1": flat}, ensure_ascii=False)
        else:
            return Sent(False, "no_template", permanent=True)
        status, body = _request(self._base + ".json", urllib.parse.urlencode(fields).encode(),
                                {"Authorization": self._auth, "Content-Type": "application/x-www-form-urlencoded"},
                                self._timeout)
        out = _result(status)
        if out.ok:
            out.ref = (body or {}).get("sid")
        return out

    def status(self, ref: str) -> str | None:
        """queued, sent, delivered, read, failed, undelivered...; None if Twilio can't be reached."""
        status, body = _request(f"{self._base}/{urllib.parse.quote(ref)}.json", None,
                                {"Authorization": self._auth}, self._timeout)
        return (body or {}).get("status") if status == 200 else None

    def received(self, since_utc_date: str) -> tuple[list[Incoming], bool]:
        """Messages to the box's number since that day (YYYY-MM-DD, UTC). (messages, reached Twilio)."""
        q = urllib.parse.urlencode({"To": self._from, "DateSent>": since_utc_date, "PageSize": 100})
        status, body = _request(f"{self._base}.json?{q}", None, {"Authorization": self._auth}, self._timeout)
        if status != 200 or not body:
            return [], False
        out = []
        for m in body.get("messages") or []:
            sender = str(m.get("from") or "")
            if m.get("direction") != "inbound" or not sender.startswith("whatsapp:") or not m.get("sid"):
                continue
            media = str(m.get("num_media") or "0") != "0"
            out.append(Incoming(str(m["sid"]), sender[len("whatsapp:"):], None if media and not m.get("body")
                                else m.get("body"), str(m.get("date_sent") or "")))
        return out[::-1], True  # Twilio lists newest first


def from_env(env: dict | None = None) -> dict[str, Channel]:
    """The channels whose keys Lea has set (in .env). Missing ones are simply absent."""
    env = os.environ if env is None else env
    v = {k: (env.get(k) or "").strip() for k in (TELEGRAM_TOKEN_VAR, TWILIO_SID_VAR, TWILIO_TOKEN_VAR,
                                                 TWILIO_FROM_VAR, WHATSAPP_FROM_VAR,
                                                 WHATSAPP_TEMPLATE_VAR)}  # a stray space or newline from pasting
    out: dict[str, Channel] = {}
    if v[TELEGRAM_TOKEN_VAR]:
        out["telegram"] = TelegramChannel(v[TELEGRAM_TOKEN_VAR])
    if all(v[k] for k in (TWILIO_SID_VAR, TWILIO_TOKEN_VAR, TWILIO_FROM_VAR)):
        out["sms"] = TwilioSms(v[TWILIO_SID_VAR], v[TWILIO_TOKEN_VAR], v[TWILIO_FROM_VAR])
    # Off until Lea sets the number and the approved template (without it, most messages couldn't go).
    if all(v[k] for k in (TWILIO_SID_VAR, TWILIO_TOKEN_VAR, WHATSAPP_FROM_VAR, WHATSAPP_TEMPLATE_VAR)):
        out["whatsapp"] = TwilioWhatsApp(v[TWILIO_SID_VAR], v[TWILIO_TOKEN_VAR], v[WHATSAPP_FROM_VAR],
                                         v[WHATSAPP_TEMPLATE_VAR])
    return out
