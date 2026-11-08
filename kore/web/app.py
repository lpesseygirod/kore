"""The home web page's server (WEB-PLAN.md; step W1: logging in, Hoy, Dispositivos).

Starlette, no scripts on the pages, forms only. Guards on every request:
- the Host header must be one of the box's own names (no DNS-rebinding tricks);
- every POST must come from the page itself (Origin or Referer checked), and cookies are
  SameSite=Strict and HttpOnly;
- strict security headers: nothing loaded from outside, no framing, no caching of pages;
- form bodies are read by hand and size-limited (no extra parsing library).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import urllib.parse
from datetime import timedelta

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from kore.family import backup
from kore.family import messages as family_msgs
from kore.info import news, weather
from kore.memory.db import connect, utc_iso
from kore.scheduler.clock import Clock, SystemClock
from kore.setup import progress
from kore.web import accounts, pages, today
from kore.web import chat as chats

SESSION = "kore"
TICKET = "kore_ticket"
SESSION_AGE = int(timedelta(days=365).total_seconds())
TICKET_AGE = int(timedelta(minutes=30).total_seconds())
MAX_FORM = 4096
REPLY_WAIT_S = 90
DEFAULT_HOSTS = ["localhost", "127.0.0.1"]  # plus the box's own name(s), given when serving

HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; form-action 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def create_app(db_path: str, clock: Clock | None = None, secure: bool = False, hosts: list[str] | None = None,
               dev: bool = False, chat: chats.ChatWorker | None = None) -> Starlette:
    """chat: the worker that talks to Claude (None: the Hablar page says writing isn't available now)."""
    clock = clock or SystemClock()
    lock = threading.Lock()
    holder: dict = {}

    def db():
        if "conn" not in holder:
            holder["conn"] = connect(db_path, shared=True)
        return holder["conn"]

    def html(body: str, status: int = 200) -> HTMLResponse:
        return HTMLResponse(body, status_code=status, headers=HEADERS)

    def login_cookie(resp: Response, token: str) -> None:
        resp.set_cookie(SESSION, token, max_age=SESSION_AGE, httponly=True, samesite="strict", secure=secure, path="/")

    async def form(request: Request) -> dict[str, str] | None:
        """The form's fields, or None if it isn't from this page or is too big (never read past the limit)."""
        if not _same_origin(request):
            return None
        try:
            if int(request.headers.get("content-length") or 0) > MAX_FORM:
                return None
        except ValueError:
            return None
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_FORM:
                return None
        return {k: v[0] for k, v in urllib.parse.parse_qs(body.decode("utf-8", "replace")).items()}

    def client(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def account(request: Request):
        return accounts.who(db(), clock, request.cookies.get(SESSION))

    def household():
        return [{"id": r["id"], "call_name": r["call_name"] or r["name"]}
                for r in db().execute("SELECT id, name, call_name FROM people ORDER BY id")]

    async def home(request: Request):
        with lock:
            a = account(request)
            if a is None:
                return RedirectResponse("/entrar", 303, headers=HEADERS)
            if a.role == "operator":
                return html(pages.operator_home(a, _status(db(), clock)))
            return html(pages.today(a, today.for_person(db(), clock, a.person_id)))

    async def entrar(request: Request):
        with lock:
            if account(request):
                return RedirectResponse("/", 303, headers=HEADERS)
            resp = html(pages.who_are_you(household()))
        if not request.cookies.get(TICKET):
            resp.set_cookie(TICKET, accounts.new_token(), max_age=TICKET_AGE, httponly=True, samesite="strict",
                            secure=secure, path="/entrar")
        return resp

    async def ask_code(request: Request):
        f = await form(request)
        if f is None:
            return html(pages.message("No se ha podido", "Vuelva a la página de Pepe y pruebe otra vez."), 403)
        ticket = request.cookies.get(TICKET)
        if not ticket:
            return RedirectResponse("/entrar", 303, headers=HEADERS)
        person = _small_int(f.get("person"))
        if person is None:
            return RedirectResponse("/entrar", 303, headers=HEADERS)
        with lock:
            try:
                note = accounts.request_code(db(), clock, person, ticket, client(request))
            except accounts.Refused as r:
                return html(pages.who_are_you(household(), str(r), bad=True), 429)
            if dev:
                _speak_in_terminal(db())
        return html(pages.enter_code(note))

    async def code_page(request: Request):
        return html(pages.enter_code())

    async def use_code(request: Request):
        f = await form(request)
        if f is None:
            return html(pages.message("No se ha podido", "Vuelva a la página de Pepe y pruebe otra vez."), 403)
        with lock:
            try:
                token = accounts.use_code(db(), clock, f.get("code", "")[:20], request.cookies.get(TICKET),
                                          accounts.device_label(request.headers.get("user-agent", "")),
                                          client(request))
            except accounts.Refused as r:
                return html(pages.enter_code(str(r), bad=True), 400)
        resp = RedirectResponse("/", 303, headers=HEADERS)
        login_cookie(resp, token)
        resp.delete_cookie(TICKET, path="/entrar")
        return resp

    async def devices(request: Request):
        with lock:
            a = account(request)
            if a is None:
                return RedirectResponse("/entrar", 303, headers=HEADERS)
            return html(pages.devices(a, accounts.logins(db(), clock, a)))

    async def close_device(request: Request):
        f = await form(request)
        with lock:
            a = account(request)
            if a is None or f is None:
                return RedirectResponse("/entrar", 303, headers=HEADERS)
            login = _small_int(f.get("login"))
            if login is not None:
                accounts.close(db(), clock, a, login)
        return RedirectResponse("/dispositivos", 303, headers=HEADERS)

    async def logout(request: Request):
        f = await form(request)
        if f is None:  # not from this page: change nothing, not even the cookie
            return html(pages.message("No se ha podido", "Vuelva a la página de Pepe y pruebe otra vez."), 403)
        with lock:
            a = account(request)
            if a is not None:
                accounts.close(db(), clock, a, a.login_id)
        resp = RedirectResponse("/entrar", 303, headers=HEADERS)
        resp.delete_cookie(SESSION, path="/")
        return resp

    async def talk_page(request: Request):
        with lock:
            a = account(request)
            if a is None or a.role != "person":
                return RedirectResponse("/entrar" if a is None else "/", 303, headers=HEADERS)
            rows = chats.history(db(), clock, a.person_id)
        return html(pages.chat(a, rows, chat is not None))

    async def talk(request: Request):
        f = await form(request)
        with lock:
            a = account(request)
        if a is None or a.role != "person":
            return RedirectResponse("/entrar", 303, headers=HEADERS)
        if f is None:
            return html(pages.message("No se ha podido", "Vuelva a la página de Pepe y pruebe otra vez."), 403)
        text = (f.get("text") or "").strip()
        if chat is not None and text:
            if len(text) > chats.MAX_CHARS:
                with lock:
                    rows = chats.history(db(), clock, a.person_id)
                return html(pages.chat(a, rows, True, "El mensaje es demasiado largo. Escríbalo en dos partes."), 400)
            try:  # the page waits; other pages and the other person don't
                await asyncio.wait_for(asyncio.wrap_future(chat.submit(a.person_id, text)), REPLY_WAIT_S)
            except asyncio.TimeoutError:
                with lock:
                    rows = chats.history(db(), clock, a.person_id)
                return html(pages.chat(a, rows, True, "Pepe sigue pensando. Vuelva a mirar en un momento."))
        return RedirectResponse("/hablar#escribir", 303, headers=HEADERS)

    async def delete_page(request: Request):
        with lock:
            a = account(request)
        if a is None or a.role != "person":
            return RedirectResponse("/entrar", 303, headers=HEADERS)
        return html(pages.confirm_delete(a))

    async def delete(request: Request):
        f = await form(request)
        with lock:
            a = account(request)
            if a is not None and a.role == "person" and f is not None:
                chats.delete_all(db(), a.person_id)
        return RedirectResponse("/hablar", 303, headers=HEADERS)

    async def health(request: Request):
        return PlainTextResponse("ok", headers=HEADERS)

    routes = [
        Route("/", home), Route("/entrar", entrar), Route("/entrar/codigo", ask_code, methods=["POST"]),
        Route("/entrar/codigo", code_page), Route("/entrar", use_code, methods=["POST"]),
        Route("/dispositivos", devices), Route("/dispositivos/cerrar", close_device, methods=["POST"]),
        Route("/salir", logout, methods=["POST"]), Route("/salud", health),
        Route("/hablar", talk_page), Route("/hablar", talk, methods=["POST"]),
        Route("/hablar/borrar", delete_page), Route("/hablar/borrar", delete, methods=["POST"]),
    ]
    @contextlib.asynccontextmanager
    async def lifespan(app):
        yield
        if chat is not None:
            chat.shutdown()

    return Starlette(routes=routes, middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=hosts or DEFAULT_HOSTS)],
                     lifespan=lifespan, max_body_size=MAX_FORM * 4)


def _small_int(value) -> int | None:
    """An id from a form: plain digits, at most 9 of them (a 30-digit number would crash SQLite)."""
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 9:
        return int(value)
    return None


def _same_origin(request: Request) -> bool:
    """A POST must come from this page (cross-site forms are refused even before SameSite cookies)."""
    host = request.headers.get("host", "")
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value:
            parsed = urllib.parse.urlsplit(value)
            return parsed.scheme in ("http", "https") and parsed.netloc == host
    return False


def _status(conn, clock: Clock) -> dict:
    """Lea's view: the box's health, nothing private."""
    w = weather.Weather(conn, clock, weather.OpenMeteo())
    place = w.place()
    return {
        "Personas en casa": conn.execute("SELECT count(*) FROM people").fetchone()[0],
        "Configuración inicial": {"complete": "terminada", "in_progress": "a medias", "paused": "en pausa",
                                  "not_started": "sin empezar"}.get(progress.status(conn), "?"),
        "Dispositivos con sesión": conn.execute("SELECT count(*) FROM web_logins WHERE revoked_at IS NULL").fetchone()[0],
        "Última copia enviada": family_msgs.get_state(conn, backup.LAST_SENT) or "ninguna",
        "Chat de Lea enlazado": "sí" if family_msgs.get_state(conn, family_msgs.OPERATOR_CHAT) else "no",
        "Lugar del tiempo": place.name if place else "sin poner",
        "Periódico": ", ".join(f["source"] for f in news.News(conn, clock, news.Rss()).feeds()),
        "Hora fiable": "sí" if clock.trusted() else "no",
    }


def _speak_in_terminal(conn) -> None:
    """Development on the Mac, with no speaker yet: what Pepe would say is printed instead."""
    for row in conn.execute("SELECT id, text FROM say_queue WHERE said_at IS NULL ORDER BY id").fetchall():
        print(f"[Pepe diría] {row['text']}", flush=True)
        conn.execute("UPDATE say_queue SET said_at = ? WHERE id = ?", (utc_iso(SystemClock().now()), row["id"]))
