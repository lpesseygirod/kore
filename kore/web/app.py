"""The home web page's server (WEB-PLAN.md; W1: logging in, Hoy, Dispositivos; W2: Hablar; W3: Calendario,
La compra, Sobre mí).

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
from datetime import date, timedelta
from pathlib import Path

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
from kore.brain.costs import CostLog
from kore.web import accounts, agenda, console, elder, icons, pages, pages_console, photos, today
from kore.web import family as fam
from kore.web import chat as chats

SESSION = "kore"
TICKET = "kore_ticket"
SESSION_AGE = int(timedelta(days=365).total_seconds())
TICKET_AGE = int(timedelta(minutes=30).total_seconds())
MAX_FORM = 4096
REPLY_WAIT_S = 90
DEFAULT_HOSTS = ["localhost", "127.0.0.1"]  # plus the box's own name(s), given when serving

HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; manifest-src 'self'; "
                               "font-src 'self'; "
                               "form-action 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def create_app(db_path: str, clock: Clock | None = None, secure: bool = False, hosts: list[str] | None = None,
               dev: bool = False, chat: chats.ChatWorker | None = None, family_links: dict | None = None) -> Starlette:
    """chat: the worker that talks to Claude (None: the Hablar page says writing isn't available now).
    family_links: the channels to the family (Telegram, SMS) for "Pedir ayuda" and replies; None: nothing real is
    sent (messages wait, and the page says so)."""
    clock = clock or SystemClock()
    lock = threading.Lock()
    holder: dict = {}
    photo_dir = photos.folder(db_path)
    cost_log = CostLog()
    test_chats: dict[int, console.TestChat] = {}
    if chat is not None and chat.photo_dir is None:
        chat.photo_dir = photo_dir

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

    async def upload(request: Request) -> dict | None:
        """The chat form: text and maybe a photo (multipart). None if it isn't from this page or is too big."""
        if not _same_origin(request):
            return None
        limit = photos.MAX_UPLOAD + 64 * 1024
        try:
            if int(request.headers.get("content-length") or 0) > limit:
                return None
        except ValueError:
            return None
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > limit:
                return None
        kind = request.headers.get("content-type", "")
        if kind.lower().startswith("multipart/form-data"):
            return await asyncio.to_thread(photos.parse_multipart, kind, body)
        if len(body) > MAX_FORM:
            return None
        return {k: v[0] for k, v in urllib.parse.parse_qs(body.decode("utf-8", "replace")).items()}

    def client(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def account(request: Request):
        a = accounts.who(db(), clock, request.cookies.get(SESSION))
        if a is not None and a.role == "person":  # their letters and tú/usted, for this request's pages
            pages.SIZE.set(elder.text_size(db(), a.person_id))
            pages.TU.set(elder.tu(db(), a.person_id))
        return a

    def household():
        return [{"id": r["id"], "call_name": r["call_name"] or r["name"]}
                for r in db().execute("SELECT id, name, call_name FROM people ORDER BY id")]

    async def home(request: Request):
        with lock:
            a = account(request)
            if a is None:
                return RedirectResponse("/entrar", 303, headers=HEADERS)
            if a.role == "operator":
                return html(pages_console.overview(overview_data()))
            if a.role == "family":
                return html(pages.family_home(a, {
                    "date": elder.long_date(clock.now().date()), "people": fam.their_people(db(), a.contact_id),
                    "no_alerts": fam.no_alerts_today(db(), clock, a.contact_id),
                    "doses": fam.doses_shared(db(), clock, a.contact_id),
                    "today": fam.days_ahead(db(), clock, a.contact_id, 1)[0]["items"]}))
            return html(pages.elder_home(a, elder_home_data(a)))

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
            return html(pages.devices(a, accounts.logins(db(), clock, a),
                                      fam.people_to_invite(db(), a.person_id) if a.role == "person" else None))

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
            rows = chats.history(db(), clock, a.person_id, photo_dir=photo_dir)
        return html(pages.chat(a, rows, chat is not None))

    async def talk(request: Request):
        with lock:
            a = account(request)
        if a is None or a.role != "person":
            return RedirectResponse("/entrar", 303, headers=HEADERS)
        f = await upload(request)
        if f is None:
            return html(pages.message("No se ha podido", "Vuelva a la página de Pepe y pruebe otra vez. Si mandaba una "
                                      "foto, quizá era demasiado grande."), 403)
        text = f.get("text") if isinstance(f.get("text"), str) else ""
        text = text.strip()
        data = f.get("photo") if isinstance(f.get("photo"), bytes) else b""

        def again(note: str, status: int = 400):
            with lock:
                rows = chats.history(db(), clock, a.person_id, photo_dir=photo_dir)
            return html(pages.chat(a, rows, chat is not None, note), status)
        if chat is not None and (text or data):
            if len(text) > chats.MAX_CHARS:
                return again("El mensaje es demasiado largo. Escríbalo en dos partes.")
            photo = None
            if data:
                try:
                    jpeg, w, h = await asyncio.to_thread(photos.shrink, data)  # outside the lock: it takes a moment
                    with lock:
                        photo = (photos.save(db(), clock, photo_dir, a.person_id, jpeg, w, h), jpeg)
                except photos.Refused as r:
                    return again(str(r))
            try:  # the page waits; other pages and the other person don't
                await asyncio.wait_for(asyncio.wrap_future(chat.submit(a.person_id, text, photo)), REPLY_WAIT_S)
            except asyncio.TimeoutError:
                return again("Pepe sigue pensando. Vuelva a mirar en un momento.", 200)
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
                chats.delete_all(db(), a.person_id, clock, photo_dir)
                if chat is not None:
                    chat.forget(a.person_id)
        return RedirectResponse("/hablar", 303, headers=HEADERS)

    # --- W3: calendar, shopping list, what Pepe knows ---------------------------------------------

    BAD_FORM = "Vuelva a la página de Pepe y pruebe otra vez."
    BACKS = ("/calendario", "/sabe", "/mi-familia", "/fotos")

    def elder_home_data(a) -> dict:
        now = clock.now()
        fam._purge_photos(db(), clock, photo_dir)
        day = today.for_person(db(), clock, a.person_id)
        w = day.get("weather") or {}
        sky = (w.get("rest_of_today") or {}).get("sky") or w.get("sky")
        return {"date": elder.long_date(now.date()), "time": now.strftime("%H:%M"),
                "weather": f"{sky.capitalize()}, {w.get('max_c')}°" if sky and w.get("max_c") is not None else "",
                "next": elder.next_up(db(), clock, a.person_id), "pills": elder.pills(db(), clock, a.person_id),
                "unread": elder.unread_count(db(), a.person_id), "proposals": day.get("family_proposals") or [],
                "shopping": day.get("shopping") or [],
                "medicine_proposals": elder.medicine_proposals(db(), a.person_id),
                "new_photos": fam.new_photos(db(), a.person_id)}

    def person(request: Request):
        """The logged-in person, or None (operator and strangers get no personal pages)."""
        a = account(request)
        return a if a is not None and a.role == "person" else None

    def go(where: str) -> RedirectResponse:
        return RedirectResponse(where, 303, headers=HEADERS)

    def today_iso() -> str:
        return clock.now().date().isoformat()

    async def calendar_page(request: Request):
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            note = pages.DONE.get(request.query_params.get("hecho", ""), "")
            return html(pages.calendar(a, agenda.calendar(db(), clock, a.person_id), note))

    def proposing(make, page_again, back: str = "/calendario"):
        """A form that proposes a change: the read-back page, or the form again with what's wrong."""
        async def handler(request: Request):
            f = await form(request)
            with lock:
                a = person(request)
                if a is None:
                    return go("/entrar")
                if f is None:
                    return html(pages.message("No se ha podido", BAD_FORM, back), 403)
                try:
                    proposal = make(a, f)
                except agenda.Refused as r:
                    if page_again is None:
                        return html(pages.calendar(a, agenda.calendar(db(), clock, a.person_id), str(r), bad=True)
                                    if back == "/calendario" else
                                    pages.known(a, agenda.known(db(), clock, a.person_id), str(r), bad=True), 400)
                    return html(page_again(a, f, str(r)), 400)
                return html(pages.confirm(a, proposal, back))
        return handler

    def form_page(render):
        async def handler(request: Request):
            with lock:
                a = person(request)
            return go("/entrar") if a is None else html(render(a))
        return handler

    def the_id(f: dict) -> int:
        i = _small_int(f.get("id"))
        if i is None:
            raise agenda.Refused("No se ha encontrado. Vuelva a la lista.")
        return i

    async def appointment_page(request: Request):
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            try:
                appt = agenda.appointment(db(), clock, a.person_id, _small_int(request.query_params.get("id")) or 0)
            except agenda.Refused as r:
                return html(pages.calendar(a, agenda.calendar(db(), clock, a.person_id), str(r), bad=True), 404)
            return html(pages.change_appointment(a, appt, today_iso()))

    def move_again(a, f, note):
        try:
            appt = agenda.appointment(db(), clock, a.person_id, _small_int(f.get("id")) or 0)
        except agenda.Refused as r:
            return pages.calendar(a, agenda.calendar(db(), clock, a.person_id), str(r), bad=True)
        return pages.change_appointment(a, appt, today_iso(), note)

    async def confirm(request: Request):
        f = await form(request)
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            if f is None:
                return html(pages.message("No se ha podido", BAD_FORM), 403)
            pending_id = _small_int(f.get("id")) or 0
            sending = agenda.kind_of(db(), a.person_id, pending_id) in ("send_message", "send_photo")
        if sending:  # to family: outside the page lock (see own_connection)
            try:
                where = await own_connection(
                    lambda c: agenda.confirm(c, clock, a.person_id, pending_id, family_links, photo_dir))
            except agenda.Refused as r:
                with lock:
                    return html(pages.message("No se ha guardado", str(r), "/mi-familia"), 409)
            except Exception:
                with lock:
                    return html(pages.message("No se ha enviado", "Algo ha fallado y no se ha enviado nada. Pruebe "
                                              "otra vez, o dígaselo a Pepe en voz alta.", "/mi-familia"), 500)
            return go(where)
        with lock:
            try:
                where = agenda.confirm(db(), clock, a.person_id, pending_id, family_links, photo_dir)
                if where.startswith("code:"):  # family access: the code is shown once, here
                    _, code, name = where.split(":", 2)
                    return html(pages.family_code(a, name, code))
                if where.startswith("/sabe?hecho=olvidado") and chat is not None:
                    chat.forget(a.person_id)  # nor in the conversation Claude still holds
                return go(where)
            except agenda.Refused as r:
                return html(pages.message("No se ha guardado", str(r), "/calendario"), 409)
            except Exception:
                return html(pages.message("No se ha guardado", "Algo ha fallado y no se ha guardado nada. Pruebe "
                                          "otra vez, o dígaselo a Pepe en voz alta.", "/calendario"), 500)

    async def confirm_no(request: Request):
        f = await form(request)
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            if f is not None:
                dropped = agenda.cancel(db(), a.person_id, _small_int(f.get("id")) or 0)
                if dropped and dropped["kind"] == "send_photo" and "/" not in dropped["file"]:
                    (photo_dir / dropped["file"]).unlink(missing_ok=True)
        back = (f or {}).get("back")
        return go((back if back in BACKS else "/calendario") + "?hecho=anulado")

    async def shopping_page(request: Request):
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            removed = None
            gone = _small_int(request.query_params.get("quitado"))
            if gone is not None:
                item = agenda.shopping_item(db(), gone)
                removed = item if item and item["status"] == "done" else None
            return html(pages.shopping(a, agenda.shopping(db()), removed=removed))

    def shopping_change(change):
        async def handler(request: Request):
            f = await form(request)
            with lock:
                a = person(request)
                if a is None:
                    return go("/entrar")
                if f is None:
                    return html(pages.message("No se ha podido", BAD_FORM, "/compra"), 403)
                try:
                    return go(change(f) or "/compra")
                except agenda.Refused as r:
                    return html(pages.shopping(a, agenda.shopping(db()), str(r), bad=True), 400)
        return handler

    def shop_add(f):
        agenda.shopping_add(db(), clock, f.get("item"))

    def shop_remove(f):
        i = _small_int(f.get("id"))
        if i is not None and agenda.shopping_remove(db(), i) is not None:
            return f"/compra?quitado={i}"

    def shop_back(f):
        i = _small_int(f.get("id"))
        if i is not None:
            agenda.shopping_put_back(db(), clock, i)

    async def known_page(request: Request):
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            note = pages.DONE.get(request.query_params.get("hecho", ""), "")
            return html(pages.known(a, agenda.known(db(), clock, a.person_id), note))

    c, k = db, clock  # short names for the proposals below

    # --- Lea's console ----------------------------------------------------------------------------

    def lea(request: Request):
        a = account(request)
        return a if a is not None and a.role == "operator" else None

    def base() -> dict:
        return {"home": console.home_name(db()), "people": console.people(db())}

    def overview_data() -> dict:
        now = clock.now()
        dev = console.device(db(), clock, cost_log)
        need = console.needs_you(db(), clock)
        summary = ("Something needs you." if need else "KORE's voice isn't running: start it with Pepe.command or on the box." if not dev["loop"]
                   else "Everything is running normally." if dev["loop"]["online"] else "The box hasn't been seen lately.")
        return {**base(), "hour": now.hour, "date": now.strftime("%A %-d %B"), "summary": summary, "device": dev,
                "today": console.today(db(), clock), "messages_today": console.messages_today(db(), clock),
                "needs_you": need, "week_numbers": console.week_numbers(db(), clock, cost_log),
                "shopping": console.shopping(db())}

    def console_page(render):
        async def handler(request: Request):
            with lock:
                a = lea(request)
                if a is None:
                    return go("/entrar")
                return html(render(a, request))
        return handler

    def console_action(act, back: str):
        """A console form: Lea's own change. `act(a, f)` returns a note for the page, or raises Refused."""
        async def handler(request: Request):
            f = await form(request)
            with lock:
                a = lea(request)
                if a is None:
                    return go("/entrar")
                if f is None:
                    return html(pages.message("Not done", "Go back to the page and try again.", back), 403)
                try:
                    result = act(a, f)
                except agenda.Refused as r:
                    return html(PAGES[back](a, None, str(r), True), 400)
                if isinstance(result, Response):
                    return result
                return html(PAGES[back](a, None, result or "Done.", False))
        return handler

    def week_start(request) -> date:
        try:
            day = date.fromisoformat(request.query_params.get("week", ""))
            if not 2000 <= day.year <= 2100:
                raise ValueError(day)
        except ValueError:
            day = clock.now().date()
        return day - timedelta(days=day.weekday())

    def calendar_view(a, request, note="", bad=False):
        start = week_start(request) if request is not None else clock.now().date() - timedelta(days=clock.now().weekday())
        return pages_console.calendar({**base(), "week": console.week(db(), clock, start), "meds": console.med_schedule(db()),
                                       "repeating": console.repeating(db()), "shared_ok": console._shared_ok(db()),
                                       "proposals": console.lea_proposals(db(), clock)}, note, bad)

    def chat_view(a, request, note="", bad=False):
        d = {**base(), "conversations": console.conversations(db(), clock),
             "anyone": any(p["chats"] for p in console.people(db()))}
        if request is not None:
            pid, first = _small_int(request.query_params.get("p")), _small_int(request.query_params.get("c"))
            if pid is not None and first is not None:
                d["open"] = console.conversation(db(), clock, pid, first)
                if d["open"]:
                    d["open"]["marks"] = console.marks(db(), pid, first)
        t = test_chats.get(a.login_id)
        d["test"] = {"available": chat is not None, "live": not dev, "lines": t.lines if t else [],
                     "as": t.as_person if t else None}
        return pages_console.chat(d, note, bad)

    def feedback_view(a, request, note="", bad=False):
        f = (request.query_params.get("f") if request is not None else None) or "all"
        who, state = (f, "all") if f.isdigit() else ("all", f if f in ("active", "undone") else "all")
        return pages_console.feedback({**base(), "items": console.feedback(db(), clock, who, state),
                                       "filter": f if f in ("all", "active", "undone") or f.isdigit() else "all"},
                                      note, bad)

    def people_view(a, request, note="", bad=False):
        return pages_console.people({**base(), "memory": console.memory(db(), clock),
                                     "shared": console.shared_people(db())}, note)

    def device_view(a, request, note="", bad=False):
        return pages_console.device({**base(), "device": console.device(db(), clock, cost_log),
                                     "events": console.events(db(), clock), "version": console.version()})

    def settings_view(a, request, note="", bad=False):
        return pages_console.settings({**base(), "permissions": console.permissions(db()),
                                       "quiet": console.quiet_times(db()), "contacts": console.contacts(db())}, note, bad)

    PAGES = {"/console/calendar": calendar_view, "/console/chat": chat_view, "/console/feedback": feedback_view,
             "/console/people": people_view, "/console/settings": settings_view}

    def act_medicine(a, f):
        return console.propose_medicine_change(db(), clock, _small_int(f.get("id")) or 0, f.get("times") or "")

    def act_appointment(a, f):
        return console.propose_appointment(db(), clock, f)

    def chat_ref(f):
        pid, first = _small_int(f.get("p")), _small_int(f.get("c"))
        if pid is None or first is None:
            raise agenda.Refused("That conversation isn't there any more.")
        return pid, first

    def act_mark(a, f):
        pid, first = chat_ref(f)
        if not console.mark_problem(db(), clock, pid, first, f.get("note") or ""):
            raise agenda.Refused("That conversation isn't there any more.")
        return go(f"/console/chat?p={pid}&c={first}")

    def act_scenario(a, f):
        pid, first = chat_ref(f)
        path = console.save_as_scenario_draft(db(), clock, pid, first, Path(db_path).resolve().parent.parent
                                              / "private" / "scenario_drafts")
        if path is None:
            raise agenda.Refused("That conversation isn't there any more.")
        return f"Saved as a draft in private/scenario_drafts/{path.name} (kept out of git). Rewrite it with made-up names."

    def act_delete(a, f):
        pid, first = chat_ref(f)
        if not console.delete_conversation(db(), clock, pid, first):
            raise agenda.Refused("That conversation isn't there any more.")
        if chat is not None:
            chat.forget(pid)
        return "Deleted."

    def act_undo(a, f):
        from kore.feedback import store as fb
        fid = _small_int(f.get("id"))
        if fid is None or not any(x["id"] == fid for x in console.feedback(db(), clock)):
            raise agenda.Refused("That feedback isn't there (or isn't shared with you).")
        try:
            fb.undo(db(), clock, fid, misunderstood=bool(f.get("misunderstood")))
        except fb.FeedbackError as err:
            raise agenda.Refused(str(err)) from None
        return "Undone. It takes effect on the next turn."

    def act_reword(a, f):
        from kore.feedback import store as fb
        fid = _small_int(f.get("id"))
        if fid is None or not any(x["id"] == fid for x in console.feedback(db(), clock)):
            raise agenda.Refused("That feedback isn't there (or isn't shared with you).")
        try:
            fb.reword(db(), fid, f.get("text") or "")
        except fb.FeedbackError as err:
            raise agenda.Refused(str(err)) from None
        return "Reworded."

    def act_quiet(a, f):
        console.set_quiet_times(db(), clock, (f.get("start") or "").strip(), (f.get("end") or "").strip())
        return "Saved."

    async def forget_note(request: Request):
        f = await form(request)
        with lock:
            a = lea(request)
            if a is None or f is None:
                return go("/entrar")
            pid, nid = _small_int(f.get("person")), _small_int(f.get("id"))
            row = db().execute("SELECT n.id, n.text, p.call_name FROM notes n JOIN people p ON p.id = n.person_id "
                               "WHERE n.id = ? AND n.person_id = ?", (nid or 0, pid or 0)).fetchone()
            if row is None or pid not in {p["id"] for p in console.people(db()) if p["day"]}:
                return html(people_view(a, None, "That isn't there any more."), 400)
            if not f.get("sure"):
                return html(pages_console.people(base(), confirm={"person_id": pid, "id": nid, "text": row["text"],
                                                                  "person": row["call_name"]}))
            console.forget_note(db(), pid, nid)
        if chat is not None:
            chat.forget(pid)
        return go("/console/people")

    async def test_talk(request: Request):
        f = await form(request)
        with lock:
            a = lea(request)
            if a is None:
                return go("/entrar")
            if f is None or chat is None:
                return html(chat_view(a, None, "The test chat isn't available.", True), 400)
            t = test_chats.setdefault(a.login_id, console.TestChat())
            try:
                t.prepare(db(), clock, chat.client, _small_int(f.get("as")) or 0)
            except agenda.Refused as r:
                return html(chat_view(a, None, str(r), True), 400)
        await asyncio.to_thread(t.reply, f.get("text") or "")
        return go("/console/chat#test-msg")

    # --- Carmen and Antonio's app ---------------------------------------------------------------

    async def agenda_page(request: Request):
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            chosen = _small_int(request.query_params.get("dia")) or 0
            days = elder.agenda_days(clock)
            chosen = chosen if chosen < len(days) else 0
            return html(pages.elder_agenda(a, days, chosen, elder.day_items(db(), clock, a.person_id,
                                                                              days[chosen]["date"])))

    async def pills_page(request: Request):
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            return html(pages.elder_pills(a, elder.pills(db(), clock, a.person_id)))

    async def take_pill(request: Request):
        f = await form(request)
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            if f is None:
                return html(pages.message("No se ha podido", BAD_FORM, "/pastillas"), 403)
            note = elder.take_pill(db(), clock, a.person_id, _small_int(f.get("id")) or 0)
            return html(pages.elder_pills(a, elder.pills(db(), clock, a.person_id), note))

    async def family_page(request: Request):
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            note = pages.DONE.get(request.query_params.get("hecho", ""), "")
            return html(pages.elder_family(a, elder.new_messages(db(), clock, a.person_id),
                                           elder.contacts(db(), a.person_id), note,
                                           bad=request.query_params.get("hecho") == "pendiente"))

    def family_again(a, f, note):
        return pages.elder_family(a, [], elder.contacts(db(), a.person_id), note, bad=True)

    async def decline_lea(request: Request):
        f = await form(request)
        with lock:
            a = person(request)
            if a is None or f is None:
                return go("/entrar")
            elder.decline_medicine_proposal(db(), clock, a.person_id, _small_int(f.get("id")) or 0)
        return go("/")

    def own_connection(work):
        """Sending (Telegram, SMS) can take seconds: done on its own database connection, without the page lock,
        so no other phone in the house waits for it."""
        def run():
            c = connect(db_path)
            try:
                return work(c)
            finally:
                c.close()
        return asyncio.to_thread(run)

    async def help_page(request: Request):
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            return html(pages.elder_help(a, elder.help_names(db(), a.person_id)))

    async def ask_help(request: Request):
        f = await form(request)
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            if f is None:  # a form from elsewhere can't call for help in their name
                return html(pages.message("No se ha podido", BAD_FORM, "/ayuda"), 403)
        said = await own_connection(lambda c: elder.ask_for_help(c, clock, a.person_id, family_links))
        with lock:
            return html(pages.elder_help(a, elder.help_names(db(), a.person_id), said))

    async def settings_page(request: Request):
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            return html(pages.elder_settings(a, elder.text_size(db(), a.person_id)))

    async def set_size(request: Request):
        f = await form(request)
        with lock:
            a = person(request)
            if a is None or f is None:
                return go("/entrar")
            elder.set_text_size(db(), clock, a.person_id, f.get("size") or "")
        return go("/ajustes")

    async def who_sees_page(request: Request):
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            return html(pages.elder_who_sees(a, elder.who_sees_what(db(), a.person_id)))

    async def photos_page(request: Request):
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            fam._purge_photos(db(), clock, photo_dir)
            mine = [r["id"] for r in db().execute("SELECT id FROM photos WHERE person_id = ? ORDER BY id DESC LIMIT 30",
                                                  (a.person_id,))]
            family_photos = fam.photos_for_person(db(), clock, a.person_id)
            fam.mark_photos_seen(db(), clock, [x["id"] for x in family_photos])
            hecho = request.query_params.get("hecho", "")
            return html(pages.elder_photos(a, family_photos, mine, elder.contacts(db(), a.person_id),
                                           pages.DONE.get(hecho, ""), bad=hecho == "pendiente"))

    async def talk_about_photo(request: Request):
        f = await form(request)
        with lock:
            a = person(request)
            if a is None or f is None or chat is None:
                return go("/entrar" if a is None else "/fotos")
            fid = _small_int(f.get("id")) or 0
            data = fam.photo_file(db(), photo_dir, fid, person_id=a.person_id)
            row = db().execute("SELECT c.name FROM family_photos p JOIN contacts c ON c.id = p.contact_id WHERE p.id = ?",
                               (fid,)).fetchone()
            if data is None or row is None:
                return go("/fotos")
            try:
                w, h = (1, 1)
                pid = photos.save(db(), clock, photo_dir, a.person_id, data, w, h)
            except photos.Refused as r:
                return html(pages.message("No se ha podido", str(r), "/fotos"), 400)
        try:
            await asyncio.wait_for(asyncio.wrap_future(
                chat.submit(a.person_id, f"Mira esta foto que me ha mandado {row['name']}.", (pid, data))), REPLY_WAIT_S)
        except asyncio.TimeoutError:
            pass
        return go("/hablar#escribir")

    async def send_photo(request: Request):
        with lock:
            a = person(request)
        if a is None:
            return go("/entrar")
        f = await upload(request)
        data = f.get("photo") if f and isinstance(f.get("photo"), bytes) else b""
        if f is None or not data:
            return html(pages.message("No se ha podido", "No ha llegado la foto. Pruebe otra vez.", "/fotos"), 400)
        try:
            jpeg, _, _ = await asyncio.to_thread(photos.shrink, data)
            with lock:
                proposal = fam.propose_photo_to_family(db(), clock, photo_dir, a.person_id,
                                                       _small_int(f.get("to") if isinstance(f.get("to"), str) else "")
                                                       or 0, jpeg,
                                                       f.get("caption") if isinstance(f.get("caption"), str) else "")
                return html(pages.confirm(a, proposal, "/fotos"))
        except (photos.Refused, agenda.Refused) as r:
            return html(pages.message("No se ha podido", str(r), "/fotos"), 400)

    # --- W4: photos, family ---------------------------------------------------------------------

    async def photo(request: Request):
        with lock:
            a = person(request)
            if a is None:
                return Response(status_code=404, headers=HEADERS)
            data = photos.read(db(), photo_dir, a.person_id, _small_int(request.query_params.get("id")) or 0)
        if data is None:
            return Response(status_code=404, headers=HEADERS)
        return Response(data, media_type="image/jpeg", headers={**HEADERS, "Content-Disposition": "inline"})

    def relative(request: Request):
        a = account(request)
        return a if a is not None and a.role == "family" else None

    async def invite_page(request: Request):
        with lock:
            a = person(request)
            if a is None:
                return go("/entrar")
            return html(pages.invite(a, fam.people_to_invite(db(), a.person_id)))

    async def take_access(request: Request):
        f = await form(request)
        with lock:
            a = person(request)
            if a is None or f is None:
                return go("/entrar")
            cid = _small_int(f.get("contact"))
            if cid is not None:
                try:
                    fam.take_access(db(), clock, a.person_id, cid)
                except agenda.Refused:
                    pass
        return go("/dispositivos")

    async def decline(request: Request):
        f = await form(request)
        with lock:
            a = person(request)
            if a is None or f is None:
                return go("/entrar")
            try:
                fam.decline_any(db(), clock, a.person_id, fam.proposal_ref(f.get("id")))
            except agenda.Refused:
                pass
        return go("/calendario?hecho=anulado")

    def family_form(f: dict | None = None, note: str = "", bad: bool = False, a=None):
        return pages.family_propose(a, fam.household(db(), a.contact_id), fam.my_proposals(db(), clock, a.contact_id),
                                    today_iso(),
                                    f, note, bad)

    async def family_cita_page(request: Request):
        with lock:
            a = relative(request)
            if a is None:
                return go("/entrar")
            return html(family_form(note=pages.DONE.get(request.query_params.get("hecho", ""), ""), bad=False, a=a))

    def family_change(change, page):
        async def handler(request: Request):
            f = await form(request)
            with lock:
                a = relative(request)
                if a is None:
                    return go("/entrar")
                if f is None:
                    return html(pages.message("No se ha podido", BAD_FORM), 403)
                try:
                    where = change(a, f)
                except agenda.Refused as r:
                    return html(page(a, f, str(r)), 400)
                return go(where)  # a redirect: reloading the page doesn't send it twice
        return handler

    def cita_page(a, f, note, bad=True):
        return family_form(f, note, bad, a)

    def thread_with_photos(a):
        fam._purge_photos(db(), clock, photo_dir)
        return sorted(fam.thread(db(), clock, a.contact_id) + fam.photos_in_thread(db(), clock, a.contact_id),
                      key=lambda m: m["at"])[-60:]

    def home_page(a, f, note, bad=True):
        return pages.family_messages(a, thread_with_photos(a), fam.household(db(), a.contact_id), note, bad)

    async def family_messages_page(request: Request):
        with lock:
            a = relative(request)
            if a is None:
                return go("/entrar")
            return html(home_page(a, None, pages.DONE.get(request.query_params.get("hecho", ""), ""), False))

    async def family_calendar_page(request: Request):
        with lock:
            a = relative(request)
            if a is None:
                return go("/entrar")
            return html(pages.family_calendar(a, fam.days_ahead(db(), clock, a.contact_id)))

    async def family_alerts_page(request: Request):
        with lock:
            a = relative(request)
            if a is None:
                return go("/entrar")
            return html(pages.family_alerts(a, fam.alerts(db(), clock, a.contact_id)))

    async def family_photo_upload(request: Request):
        with lock:
            a = relative(request)
        if a is None:
            return go("/entrar")
        f = await upload(request)
        data = f.get("photo") if f and isinstance(f.get("photo"), bytes) else b""
        if f is None or not data:
            with lock:
                return html(home_page(a, None, "No ha llegado la foto. Prueba otra vez."), 400)
        try:
            jpeg, _, _ = await asyncio.to_thread(photos.shrink, data)
            with lock:
                people_ids = {str(p["id"]): p["id"] for p in fam.household(db(), a.contact_id)}
                target = f.get("for") if isinstance(f.get("for"), str) else ""
                if target == "both" and len(people_ids) > 1:
                    pid = None
                elif target in people_ids:
                    pid = people_ids[target]
                else:
                    raise agenda.Refused("Elige para quién es.")
                fam.save_photo(db(), clock, photo_dir, a.contact_id, pid, "to_home", jpeg,
                               f.get("caption") if isinstance(f.get("caption"), str) else "")
        except (photos.Refused, agenda.Refused) as r:
            with lock:
                return html(home_page(a, None, str(r)), 400)
        return go("/familia/mensajes?hecho=foto")

    async def family_photo(request: Request):
        with lock:
            a = account(request)
            pid = _small_int(request.query_params.get("id")) or 0
            data = None
            if a is not None and a.role == "person":
                data = fam.photo_file(db(), photo_dir, pid, person_id=a.person_id)
            elif a is not None and a.role == "family":
                data = fam.photo_file(db(), photo_dir, pid, contact_id=a.contact_id)
        if data is None:
            return Response(status_code=404, headers=HEADERS)
        return Response(data, media_type="image/jpeg", headers={**HEADERS, "Content-Disposition": "inline"})

    def fam_write(a, f):
        fam.write(db(), clock, a.contact_id, f.get("text"))
        return "/familia/mensajes?hecho=enviado"

    def fam_propose(a, f):
        fam.propose(db(), clock, a.contact_id, f)
        return "/familia/cita?hecho=propuesta"

    def fam_withdraw(a, f):
        i = _small_int(f.get("id"))
        if i is not None:
            fam.withdraw(db(), clock, a.contact_id, i)
        return "/familia/cita"

    # Pepe's icon and the manifest: public (the phone fetches them before anyone logs in), the same for everyone.
    cached = {**HEADERS, "Cache-Control": "public, max-age=86400"}

    async def manifest(request: Request):
        return Response(icons.manifest(), media_type="application/manifest+json", headers=cached)

    def icon(size: int):
        async def serve(request: Request):
            return Response(icons.png(size), media_type="image/png", headers=cached)
        return serve

    async def font(request: Request):
        name = request.path_params["name"]
        if name not in icons.FONTS:
            return PlainTextResponse("Not found", status_code=404, headers=HEADERS)
        return Response(icons.font(name), media_type="font/woff2",
                        headers={**cached, "Cache-Control": "public, max-age=2592000"})

    async def health(request: Request):
        return PlainTextResponse("ok", headers=HEADERS)

    routes = [
        Route("/", home), Route("/entrar", entrar), Route("/entrar/codigo", ask_code, methods=["POST"]),
        Route("/entrar/codigo", code_page), Route("/entrar", use_code, methods=["POST"]),
        Route("/dispositivos", devices), Route("/dispositivos/cerrar", close_device, methods=["POST"]),
        Route("/salir", logout, methods=["POST"]), Route("/salud", health),
        Route("/manifest.webmanifest", manifest), Route("/fuentes/{name}", font), *[Route(f"/icono-{s}.png", icon(s)) for s in icons.SIZES],
        Route("/hablar", talk_page), Route("/hablar", talk, methods=["POST"]),
        Route("/hablar/borrar", delete_page), Route("/hablar/borrar", delete, methods=["POST"]),
        Route("/calendario", calendar_page),
        Route("/calendario/nueva-cita", form_page(lambda a: pages.new_appointment(a, today_iso()))),
        Route("/calendario/nueva-cita", proposing(
            lambda a, f: agenda.propose_appointment(c(), k, a.person_id, f),
            lambda a, f, n: pages.new_appointment(a, today_iso(), f, n)), methods=["POST"]),
        Route("/calendario/cita", appointment_page),
        Route("/calendario/cita", proposing(
            lambda a, f: agenda.propose_move(c(), k, a.person_id, the_id(f), f), move_again), methods=["POST"]),
        Route("/calendario/cita/anular", proposing(
            lambda a, f: agenda.propose_cancel(c(), k, a.person_id, the_id(f)), None), methods=["POST"]),
        Route("/calendario/nuevo-recordatorio", form_page(lambda a: pages.new_reminder(a, today_iso()))),
        Route("/calendario/nuevo-recordatorio", proposing(
            lambda a, f: agenda.propose_reminder(c(), k, a.person_id, f),
            lambda a, f, n: pages.new_reminder(a, today_iso(), f, n)), methods=["POST"]),
        Route("/calendario/recordatorio/quitar", proposing(
            lambda a, f: agenda.propose_reminder_stop(c(), k, a.person_id, the_id(f)), None), methods=["POST"]),
        Route("/calendario/nuevo-cumple", form_page(lambda a: pages.new_birthday(a))),
        Route("/calendario/nuevo-cumple", proposing(
            lambda a, f: agenda.propose_birthday(c(), k, a.person_id, f),
            lambda a, f, n: pages.new_birthday(a, f, n)), methods=["POST"]),
        Route("/calendario/cumple/quitar", proposing(
            lambda a, f: agenda.propose_birthday_stop(c(), k, a.person_id, the_id(f)), None), methods=["POST"]),
        Route("/confirmar", confirm, methods=["POST"]), Route("/confirmar/no", confirm_no, methods=["POST"]),
        Route("/compra", shopping_page), Route("/compra/anadir", shopping_change(shop_add), methods=["POST"]),
        Route("/compra/quitar", shopping_change(shop_remove), methods=["POST"]),
        Route("/compra/volver", shopping_change(shop_back), methods=["POST"]),
        Route("/foto", photo),
        Route("/console/calendar", console_page(lambda a, r: calendar_view(a, r))),
        Route("/console/calendar/medicine", console_action(act_medicine, "/console/calendar"), methods=["POST"]),
        Route("/console/calendar/add", console_action(act_appointment, "/console/calendar"), methods=["POST"]),
        Route("/console/chat", console_page(lambda a, r: chat_view(a, r))),
        Route("/console/chat/mark", console_action(act_mark, "/console/chat"), methods=["POST"]),
        Route("/console/chat/scenario", console_action(act_scenario, "/console/chat"), methods=["POST"]),
        Route("/console/chat/delete", console_action(act_delete, "/console/chat"), methods=["POST"]),
        Route("/console/chat/test", test_talk, methods=["POST"]),
        Route("/console/feedback", console_page(lambda a, r: feedback_view(a, r))),
        Route("/console/feedback/undo", console_action(act_undo, "/console/feedback"), methods=["POST"]),
        Route("/console/feedback/reword", console_action(act_reword, "/console/feedback"), methods=["POST"]),
        Route("/console/people", console_page(lambda a, r: people_view(a, r))),
        Route("/console/people/forget", forget_note, methods=["POST"]),
        Route("/console/device", console_page(lambda a, r: device_view(a, r))),
        Route("/console/settings", console_page(lambda a, r: settings_view(a, r))),
        Route("/console/settings/quiet", console_action(act_quiet, "/console/settings"), methods=["POST"]),
        Route("/agenda", agenda_page), Route("/pastillas", pills_page),
        Route("/propuesta-lea/no", decline_lea, methods=["POST"]),
        Route("/pastillas/tomada", take_pill, methods=["POST"]),
        Route("/mi-familia", family_page),
        Route("/mi-familia/responder", proposing(
            lambda a, f: elder.propose_reply(c(), k, a.person_id, _small_int(f.get("contact")) or 0, f.get("text")),
            family_again, "/mi-familia"), methods=["POST"]),
        Route("/ayuda", help_page), Route("/ayuda", ask_help, methods=["POST"]),
        Route("/ajustes", settings_page), Route("/ajustes/letra", set_size, methods=["POST"]),
        Route("/lo-que-ven", who_sees_page), Route("/fotos", photos_page),
        Route("/familia/invitar", invite_page),
        Route("/familia/invitar", proposing(lambda a, f: fam.propose_access(c(), k, a.person_id, f),
                                            lambda a, f, n: pages.invite(a, fam.people_to_invite(c(), a.person_id), n)),
              methods=["POST"]),
        Route("/familia/quitar", take_access, methods=["POST"]),
        Route("/calendario/propuesta", proposing(
            lambda a, f: fam.propose_accept_any(c(), k, a.person_id, fam.proposal_ref(f.get("id"))), None),
              methods=["POST"]),
        Route("/calendario/propuesta/no", decline, methods=["POST"]),
        Route("/familia/escribir", family_change(fam_write, home_page), methods=["POST"]),
        Route("/familia/mensajes", family_messages_page), Route("/familia/calendario", family_calendar_page),
        Route("/familia/avisos", family_alerts_page),
        Route("/familia/foto", family_photo_upload, methods=["POST"]), Route("/foto-familia", family_photo),
        Route("/fotos/hablar", talk_about_photo, methods=["POST"]), Route("/fotos/enviar", send_photo, methods=["POST"]),
        Route("/familia/cita", family_cita_page),
        Route("/familia/cita", family_change(fam_propose, cita_page), methods=["POST"]),
        Route("/familia/cita/retirar", family_change(fam_withdraw, cita_page), methods=["POST"]),
        Route("/sabe", known_page),
        Route("/sabe/anadir", proposing(lambda a, f: agenda.propose_note(c(), k, a.person_id, f),
                                        None, "/sabe"), methods=["POST"]),
        Route("/sabe/olvidar", proposing(lambda a, f: agenda.propose_forget(c(), k, a.person_id, the_id(f)),
                                         None, "/sabe"), methods=["POST"]),
    ]
    @contextlib.asynccontextmanager
    async def lifespan(app):
        yield
        if chat is not None:
            chat.shutdown()

    return Starlette(routes=routes, middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=hosts or DEFAULT_HOSTS)],
                     lifespan=lifespan, max_body_size=photos.MAX_UPLOAD + 64 * 1024)


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


MAC_VOICE: str | None = None  # serve --voz: the Mac also says it out loud (its own Spanish voice)


def mac_say(text: str) -> None:
    """Say it with the Mac's built-in voice, without waiting (Mac only; development until the box's speaker)."""
    import subprocess

    if MAC_VOICE:
        subprocess.Popen(["say", "-v", MAC_VOICE, "-r", "165", text], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def _speak_in_terminal(conn) -> None:
    """Development on the Mac, with no speaker yet: what Pepe would say is printed (and said, with --voz)."""
    for row in conn.execute("SELECT id, text FROM say_queue WHERE said_at IS NULL ORDER BY id").fetchall():
        print(f"[Pepe diría] {row['text']}", flush=True)
        mac_say(row["text"])
        conn.execute("UPDATE say_queue SET said_at = ? WHERE id = ?", (utc_iso(SystemClock().now()), row["id"]))
