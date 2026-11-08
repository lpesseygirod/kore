"""Chatting with Pepe in text on the web page (WEB-PLAN.md section 6, step W2).

The same Brain, tools, memory and rules as the voice: a reminder added here is said out loud,
and the other way round. Only the door changes:
- the login says who is writing, and nothing typed changes it (Session.fixed_speaker);
- in text, a typed "ayuda" always asks first ("¿Aviso ahora a Lucía?"): on a web page it often
  means "help me with this page". A fall, or a second "ayuda" answering that question, goes
  through the help path as by voice;
- a conversation lasts while they keep writing; it ends after 30 minutes of quiet, after 40
  messages, after 2 hours or at midnight (never in the middle of an open question);
- each person has their own worker thread with its own database connection, so a slow answer
  for Toni never holds up Carmen, and the page waits at most 90 seconds;
- whatever happens, what code did (a help message sent, a question asked, a confirmed action)
  is shown: if Claude fails afterwards, the page says what was done and that Pepe can't go on;
- limits: 600 characters a message, 30 messages an hour per person (a separate counter that
  deleting the conversation doesn't reset); help is never limited.

What is stored (the `chat` table): their messages and Pepe's answers here, always (it is the
conversation they see), and voice turns only with their "keep_conversations" permission
(`log_voice`, called by the voice loop). Deleted after 90 days (checked whenever the page is
shown, and by the voice loop daily). "Olvídalo" deletes the conversation's lines too. Each
person can delete theirs from the page. Chats are left out of the nightly backup.
"""

from __future__ import annotations

import queue
import sqlite3
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from kore.brain import tools
from kore.brain.conversation import Brain
from kore.brain.language import help_requested
from kore.brain.session import Person, Session
from kore.memory import store
from kore.memory.db import from_utc_iso, utc_iso
from kore.scheduler.clock import Clock

KEEP = timedelta(days=90)
CONVERSATION_GAP = timedelta(minutes=30)
CONVERSATION_TURNS = 40
CONVERSATION_AGE = timedelta(hours=2)
MAX_CHARS = 600
PER_HOUR = 30
SHOWN = 60
CANT_ANSWER = "Ahora no puedo contestar por escrito. Pruebe un poco más tarde, o hábleme en voz alta."
DONE_THEN_FAILED = "Lo que me ha confirmado ya está hecho, pero ahora no puedo seguir contestando por escrito."
TOO_MANY = "Hemos hablado mucho esta hora. Sigamos un poco más tarde, o hábleme en voz alta."


# --- storage ---------------------------------------------------------------------------------


def add(conn: sqlite3.Connection, clock: Clock, person_id: int, who: str, channel: str, text: str) -> int:
    return conn.execute("INSERT INTO chat (person_id, who, channel, text, at_utc) VALUES (?, ?, ?, ?, ?)",
                        (person_id, who, channel, text, utc_iso(clock.now()))).lastrowid


def log_voice(conn: sqlite3.Connection, clock: Clock, person_id: int | None, heard: str, said: str) -> bool:
    """The voice loop, after each turn: kept only for a known speaker who said yes to keep_conversations."""
    if person_id is None or not store.has_consent(conn, person_id, "keep_conversations"):
        return False
    if heard:
        add(conn, clock, person_id, "person", "voice", heard)
    if said:
        add(conn, clock, person_id, "pepe", "voice", said)
    return True


def history(conn: sqlite3.Connection, clock: Clock, person_id: int, limit: int = SHOWN) -> list[dict]:
    purge(conn, clock)  # the 90 days hold even if nobody writes
    rows = conn.execute("SELECT * FROM chat WHERE person_id = ? ORDER BY id DESC LIMIT ?", (person_id, limit)).fetchall()
    return [{"who": r["who"], "channel": r["channel"], "text": r["text"],
             "at": from_utc_iso(r["at_utc"], clock.tz)} for r in reversed(rows)]


def delete_all(conn: sqlite3.Connection, person_id: int) -> int:
    n = conn.execute("DELETE FROM chat WHERE person_id = ?", (person_id,)).rowcount
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # secure_delete is on; this clears the write-ahead log too
    return n


def purge(conn: sqlite3.Connection, clock: Clock) -> int:
    now = clock.now()
    conn.execute("DELETE FROM chat_turns WHERE at_utc < ?", (utc_iso(now - timedelta(days=1)),))
    return conn.execute("DELETE FROM chat WHERE at_utc < ?", (utc_iso(now - KEEP),)).rowcount


def turns_last_hour(conn: sqlite3.Connection, clock: Clock, person_id: int) -> int:
    """No upper bound: a clock moved back doesn't hide recent turns."""
    return conn.execute("SELECT count(*) FROM chat_turns WHERE person_id = ? AND at_utc > ?",
                        (person_id, utc_iso(clock.now() - timedelta(hours=1)))).fetchone()[0]


# --- the worker ------------------------------------------------------------------------------


@dataclass
class _Conversation:
    brain: Brain
    started: datetime
    last: datetime
    first_row: int | None = None
    turns: int = 0
    said: list[str] = field(default_factory=list)  # code's own lines this turn (help path)


class _Lane:
    """One person's thread: their turns, one after another. A daemon: never holds up shutdown."""

    def __init__(self, run: Callable[[int, str], str], person_id: int):
        self.jobs: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._loop, args=(run, person_id), daemon=True,
                                       name=f"kore-chat-{person_id}")
        self.thread.start()

    def _loop(self, run, person_id):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            text, fut = job
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(run(person_id, text))
            except BaseException as e:  # never kill the lane
                fut.set_exception(e)


@dataclass
class ChatWorker:
    """`connect()` opens a connection for each person's thread; `make_context(conn, session)` builds the tools'
    Context (family links, weather and news); `client` is Claude (or a fake in tests)."""
    connect: Callable[[], sqlite3.Connection]
    clock: Clock
    client: object
    make_context: Callable[[sqlite3.Connection, Session], tools.Context] | None = None
    _lanes: dict[int, _Lane] = field(default_factory=dict)
    _local: threading.local = field(default_factory=threading.local)
    _talks: dict[int, _Conversation] = field(default_factory=dict)
    _guard: threading.Lock = field(default_factory=threading.Lock)

    def submit(self, person_id: int, text: str) -> Future:
        fut: Future = Future()
        with self._guard:
            lane = self._lanes.get(person_id)
            if lane is None:
                lane = self._lanes[person_id] = _Lane(self._turn, person_id)
        lane.jobs.put((text, fut))
        return fut

    def shutdown(self) -> None:
        with self._guard:
            for lane in self._lanes.values():
                lane.jobs.put(None)
            self._lanes.clear()

    def _conn(self) -> sqlite3.Connection:
        if getattr(self._local, "conn", None) is None:
            self._local.conn = self.connect()
        return self._local.conn

    def _turn(self, person_id: int, text: str) -> str:
        try:
            return self._turn_inner(person_id, text)
        except Exception:  # a database error, a broken context: say so, never a broken page
            try:
                add(self._conn(), self.clock, person_id, "pepe", "chat", CANT_ANSWER)
            except Exception:
                pass
            self._talks.pop(person_id, None)
            return CANT_ANSWER

    def _turn_inner(self, person_id: int, text: str) -> str:
        conn = self._conn()
        now = self.clock.now()
        text = " ".join(text.split())[:MAX_CHARS]
        if not text:
            return ""
        talk = self._talks.get(person_id)
        help_pending = bool(talk and any(p.status == "pending" and p.kind == "send_help"
                                         for p in talk.brain.ctx.session.pending.values()))
        names = [a for r in conn.execute("SELECT name, call_name FROM people") for a in (r["call_name"], r["name"]) if a]
        is_help = help_requested(text, names) is not None or help_pending
        if not is_help and turns_last_hour(conn, self.clock, person_id) >= PER_HOUR:
            last = conn.execute("SELECT text FROM chat WHERE person_id = ? ORDER BY id DESC LIMIT 1",
                                (person_id,)).fetchone()
            if not last or last["text"] != TOO_MANY:  # said once, not after every message
                add(conn, self.clock, person_id, "pepe", "chat", TOO_MANY)
            return TOO_MANY
        conn.execute("INSERT INTO chat_turns (person_id, at_utc) VALUES (?, ?)", (person_id, utc_iso(now)))
        row = add(conn, self.clock, person_id, "person", "chat", text)
        if talk is None or self._should_end(talk, now):
            talk = self._new(conn, person_id, now)
        talk.first_row = talk.first_row or row
        talk.last = now
        talk.turns += 1
        talk.said.clear()
        brain = talk.brain
        calls_before = len(brain.calls)
        try:
            claude = brain.hear(text)
            failed = False
        except Exception:  # budget reached, no connection, an API error
            claude, failed = "", True
        code_lines = " ".join(talk.said)
        if failed:
            confirmed = any(c.name == "confirm_action" and c.result.get("ok") for c in brain.calls[calls_before:])
            tail = DONE_THEN_FAILED if confirmed else CANT_ANSWER
            reply = f"{code_lines} {tail}".strip() if not code_lines or confirmed else code_lines
            pending = any(p.status == "pending" for p in brain.ctx.session.pending.values())
            if not pending:
                self._talks.pop(person_id, None)  # start afresh next time; but an open question stays answerable
        else:
            reply = f"{code_lines} {claude}".strip() or CANT_ANSWER
        if brain.ctx.forget_up_to_turn is not None:  # "olvídalo": this conversation's lines go too
            conn.execute("DELETE FROM chat WHERE person_id = ? AND id >= ?", (person_id, talk.first_row))
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            brain.ctx.forget_up_to_turn = None
            talk.first_row = None
        add(conn, self.clock, person_id, "pepe", "chat", reply)
        purge(conn, self.clock)
        return reply

    @staticmethod
    def _should_end(talk: _Conversation, now: datetime) -> bool:
        if any(p.status == "pending" for p in talk.brain.ctx.session.pending.values()) and \
                timedelta(0) <= now - talk.last <= CONVERSATION_GAP:
            return False  # never in the middle of an open question
        return (not timedelta(0) <= now - talk.last <= CONVERSATION_GAP or talk.turns >= CONVERSATION_TURNS
                or now - talk.started > CONVERSATION_AGE or now.date() != talk.started.date())

    def _new(self, conn: sqlite3.Connection, person_id: int, now: datetime) -> _Conversation:
        people = [Person(r["id"], r["name"], r["call_name"] or r["name"])
                  for r in conn.execute("SELECT id, name, call_name FROM people ORDER BY id")]
        session = Session(people, fixed_speaker=person_id)
        ctx = self.make_context(conn, session) if self.make_context else tools.Context(conn, self.clock, session)
        talk = _Conversation(Brain(ctx, self.client, channel="chat"), now, now)
        talk.brain.say_now = talk.said.append  # code's lines are kept apart, so a later failure can't hide them
        self._talks[person_id] = talk
        return talk
