"""The home loop: Pepe's day on the box, apart from the microphone and the speaker themselves.

What the ears hand over (words after the wake word or the button, the help button, the presence sensor) comes in
through `heard`, `help_button` and `presence`; `tick` runs every few seconds. What Pepe says goes out through a
`Voice` (kore.home.voice): fake in tests, printed on the Mac, the real speaker in phase 1.

Each tick (BUILD-PLAN phases 1 and 7; docs/reports/state.md "Exact next step" 3):
- tells Lea's console the loop is alive (family_state "loop_seen_at");
- reminders and events from the engine, and how family messages went, go through the manners gate; what the gate
  says to say now is worded by Claude (the offline phrase if Claude can't be reached) and spoken. An item is marked
  done only once the voice has really said it, so a power cut or a broken speaker means it's said later, not lost;
- family alerts from the gate go out as messages, never spoken;
- lines code wrote for Pepe to say (a web login code) are said, then deleted;
- in the background, never holding up the loop: weather and news, the nightly backup, the heartbeat.
Pepe doesn't start talking while someone is in the middle of a conversation (except how a call for help went).

Each turn:
- code's own lines (help) are said at once, before Claude answers (Brain.say_now);
- "¿qué?", "¿cómo?", "repite" are counted for the feedback (too fast, too quiet?);
- how they answered something Pepe started (warm, brief, "not now", or no answer) is told to the gate;
- the turn is kept in their history only with their yes (keep_conversations); "olvídalo" removes this
  conversation's lines and Claude's copy of it;
- the time from the end of their words to Pepe's first word, and the cost, are logged (no words, only numbers).
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kore.brain import fallback, tools
from kore.brain.conversation import Brain
from kore.brain.session import Person, Session
from kore.family import backup
from kore.family import messages as family_msgs
from kore.family.service import Family
from kore.feedback import store as feedback
from kore.manners.gate import FAMILY, HELP_NEWS, SIGNAL, Item, Manners
from kore.memory import store
from kore.memory.db import connect, utc_iso
from kore.scheduler.clock import Clock
from kore.scheduler.engine import Engine
from kore.web import chat
from kore.web.console import LOOP_SEEN

IN_CONVERSATION = timedelta(seconds=30)  # Pepe doesn't start talking this soon after the last words
END_SILENCE = timedelta(minutes=2)       # then the conversation is over: who was speaking is forgotten
NEW_CONVERSATION = timedelta(minutes=30) # Claude's copy of the conversation starts afresh (and on a new day)
MAX_TURNS = 40
REPLY_WAIT = timedelta(minutes=2)        # no answer to something Pepe started: "ignored"
LOOKING = "A ver."

REPEAT = re.compile(r"(?:pepe[,\s]*)?(?:¿\s*)?(?:qué|que|cómo|como|eh|perdona|perdón|repite|repítelo|repítemelo|"
                    r"no te he oído|no te he entendido|no te oigo|otra vez)(?:\s*\?)?[\s.!?]*", re.I)
NOT_NOW = re.compile(r"\b(ahora no|luego|más tarde|mas tarde|déjame|dejame|no me apetece|otro rato|otro día)\b", re.I)


class Home:
    def __init__(self, db_path: str, clock: Clock, client, voice, channels: dict | None = None, info=None,
                 heartbeat=None, backups: bool = True, backup_cert: Path | None = None,
                 turn_log: Path | None = None, monotonic=time.monotonic):
        self.db_path = db_path
        self.clock = clock
        self.client = client
        self.voice = voice
        self.channels = channels or {}
        self.info = info
        self.heartbeat = heartbeat
        self.backups = backups
        self.backup_cert = backup_cert
        self.turn_log = turn_log
        self.monotonic = monotonic
        self.conn = connect(db_path)
        self.family = Family(self.conn, clock, self.channels)
        self.engine = Engine(self.conn, clock)
        self.manners = Manners(self.conn, clock)
        self.last_words: datetime | None = None   # someone spoke, or Pepe did
        self.awaiting: tuple[Item, datetime] | None = None
        self._day: str | None = None
        self._background: dict[str, threading.Thread] = {}
        self._turn_lines: list[str] = []
        self._in_turn = False
        self._t0: float | None = None
        self._first_word_ms: int | None = None
        self._new_conversation()

    # --- what the ears hand over --------------------------------------------------------------

    @property
    def wake_word_on(self) -> bool:
        """Someone said no to listening after the wake word: the button only (store.listening_declined)."""
        return not store.listening_declined(self.conn)

    def heard(self, text: str, via: str = "wake", ended_at: float | None = None) -> str | None:
        """Their words, after "Pepe" (via="wake") or the button (via="button"). ended_at: when they stopped
        speaking (monotonic seconds), for the timing. Returns what Pepe said."""
        if via == "wake" and not self.wake_word_on:
            return None  # the wake word is off: these words were never meant for Pepe
        text = " ".join(text.split())
        if not text:
            return None
        self._start_turn(ended_at)
        if self.awaiting is not None:
            self.manners.note_reply("not_now" if NOT_NOW.search(text) else
                                    "brief" if len(text.split()) <= 3 else "warm")
            self.awaiting = None
        try:
            reply = self.brain.hear(text)
        except Exception:  # no connection, the day's budget reached, an API error: say something true
            reply = fallback.CONNECTION_PROBLEM
            self._broken = True
        if REPEAT.fullmatch(text):
            feedback.note_signal(self.conn, self.clock, self.session.speaker_id, "repeat_request")
        self._say(reply)
        self._end_turn(text)
        return " ".join(self._turn_lines)

    def help_button(self, ended_at: float | None = None) -> str:
        """The help button. Code acts at once (Brain.button); Claude keeps them company after."""
        self._start_turn(ended_at)
        try:
            reply = self.brain.button()
        except Exception:
            reply = ""  # code's own line was already said; Claude's part is the one that failed
            self._broken = True
        self._say(reply)
        self._end_turn("")
        return " ".join(self._turn_lines)

    def presence(self, state: str) -> None:
        """The sensor: 'moving' | 'still' | 'absent'."""
        self.manners.submit(self.engine.on_presence(state))
        self.manners.on_presence(state)

    # --- every few seconds ------------------------------------------------------------------------

    def tick(self) -> None:
        now = self.clock.now()
        family_msgs.set_state(self.conn, LOOP_SEEN, utc_iso(now))
        self._daily(now)
        if self.info is not None:
            try:
                self.info.tick()  # never blocks: a finished fetch is stored next tick
            except Exception:
                pass
        if self.backups:
            self._in_background("backup", self._backup)
        if self.heartbeat is not None:
            self._in_background("heartbeat", self.heartbeat.tick)
        if self.awaiting is not None and now - self.awaiting[1] > REPLY_WAIT:
            self.manners.note_reply("ignored")
            self.awaiting = None
        self.manners.submit(self.engine.tick())
        self.manners.submit(self.family.tick())
        self.manners.tick()
        self._say_queue()
        self._speak_items(now)

    def _speak_items(self, now: datetime) -> None:
        talking = self.last_words is not None and timedelta(0) <= now - self.last_words < IN_CONVERSATION
        for item in self.manners.next_items():
            if item.manner == FAMILY:
                self.family.handle_item(item)  # a message, kept until sent
                self.manners.mark_done(item)
                continue
            if talking and item.event.kind not in HELP_NEWS:
                continue  # not in the middle of a conversation; it stays ready
            if item.manner == SIGNAL:
                self.voice.chime()
                words = fallback.phrase(item.event, SIGNAL)  # private: no words about what it is
            else:
                try:
                    words = self.brain.speak_event(item)
                except Exception:
                    words = None
                    self._broken = True
                words = words or fallback.phrase(item.event, item.manner)
            if words and not self.voice.say(words, self._voice_settings(item.event.person_ids)):
                break  # not said (speaker broken, or talked over): it stays ready, said on a later tick
            self.manners.mark_done(item)
            if words:
                self.last_words = now
                if item.event.category == 2:
                    self.awaiting = (item, now)

    def _say_queue(self) -> None:
        """Lines code wrote for Pepe to say (a web login code), then deleted: one holds a code in words."""
        for row in self.conn.execute("SELECT id, text FROM say_queue WHERE said_at IS NULL ORDER BY id").fetchall():
            if not self.voice.say(row["text"], self._voice_settings([])):
                return
            self.conn.execute("DELETE FROM say_queue WHERE id = ?", (row["id"],))

    def _daily(self, now: datetime) -> None:
        today = now.date().isoformat()
        if self._day == today:
            return
        self._day = today
        chat.purge(self.conn, self.clock)  # the 90 days hold even if nobody opens the page
        weather = getattr(self.info, "weather", None)
        if weather is not None:
            try:
                self.engine.hot_day = weather.hot_today()  # more water reminders on a hot day
            except Exception:
                self.engine.hot_day = False

    def _in_background(self, name: str, job) -> None:
        running = self._background.get(name)
        if running is not None and running.is_alive():
            return

        def run():
            try:
                job()
            except Exception:
                pass  # the heartbeat stays the backstop: a stuck box stops pinging

        t = threading.Thread(target=run, name=f"kore-{name}", daemon=True)
        self._background[name] = t
        t.start()

    def _backup(self) -> None:
        conn = connect(self.db_path)  # its own connection: SQLite connections stay in their thread
        try:
            backup.nightly(conn, self.clock, Family(conn, self.clock, self.channels), self.backup_cert)
        finally:
            conn.close()

    def wait_for_background(self, timeout: float = 10) -> None:
        """Tests and shutdown."""
        for t in list(self._background.values()):
            t.join(timeout)

    # --- conversations ------------------------------------------------------------------------------

    def _new_conversation(self) -> None:
        people = [Person(r["id"], r["name"], r["call_name"] or r["name"])
                  for r in self.conn.execute("SELECT id, name, call_name FROM people ORDER BY id")]
        self.session = Session(people)
        ctx = tools.Context(self.conn, self.clock, self.session, family=self.family, info=self.info)
        self.brain = Brain(ctx, self.client)
        self.brain.say_now = self._say  # code's own lines (help) are said before Claude is even asked
        self.brain.on_tool_round = self._looking_it_up
        self.started = self.clock.now()
        self.turns = 0
        self._broken = False
        row = self.conn.execute("SELECT max(id) FROM chat").fetchone()[0]
        self._chat_mark = row or 0  # this conversation's lines in their history come after this

    def _start_turn(self, ended_at: float | None) -> None:
        now = self.clock.now()
        pending = any(p.status == "pending" for p in self.session.pending.values())
        last = self.last_words
        gap = now - last if last is not None else None
        stale = gap is None or not timedelta(0) <= gap <= NEW_CONVERSATION
        if self._broken or self.turns >= MAX_TURNS or now.date() != self.started.date() or (stale and not pending):
            self._new_conversation()
        elif gap is not None and gap > END_SILENCE:
            self.session.end_conversation()  # who was speaking, and any open question, are forgotten
        self._t0 = ended_at if ended_at is not None else self.monotonic()
        self._first_word_ms = None
        self._turn_lines = []
        self._in_turn = True
        self._spent_before = getattr(self.client, "spent", None)
        self.turns += 1

    def _looking_it_up(self) -> None:
        """Claude needs a tool before answering: if nothing has been said yet in this turn, a short "A ver."
        now, so they know they were heard (only in their own turns, not when Pepe speaks first)."""
        if self._t0 is not None and not self._turn_lines and self._in_turn:
            self._say(LOOKING)

    def _say(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if self._first_word_ms is None and self._t0 is not None:
            self._first_word_ms = int((self.monotonic() - self._t0) * 1000)
        self._turn_lines.append(text)
        speaker = self.session.speaker_id
        self.voice.say(text, self._voice_settings([speaker] if speaker is not None else []))

    def _end_turn(self, heard: str) -> None:
        self._in_turn = False
        now = self.clock.now()
        self.last_words = now
        said = " ".join(self._turn_lines)
        ctx = self.brain.ctx
        if ctx.forget_up_to_turn is not None:
            # "Olvídalo": this conversation's lines leave their history, and Claude's copy goes too.
            self.conn.execute("DELETE FROM chat WHERE channel = 'voice' AND id > ?", (self._chat_mark,))
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            ctx.forget_up_to_turn = None
            self._broken = True  # a fresh conversation next time
        else:
            chat.log_voice(self.conn, self.clock, self.session.speaker_id, heard, said)
        self._log_turn(now)

    def _log_turn(self, now: datetime) -> None:
        if self.turn_log is None:
            return
        spent = getattr(self.client, "spent", None)
        entry = {"at": utc_iso(now.astimezone(timezone.utc)), "ms_to_first_word": self._first_word_ms,
                 "usd": round(spent - self._spent_before, 5) if spent is not None and self._spent_before is not None
                 else None, "known_speaker": self.session.speaker_id is not None}
        self.turn_log.parent.mkdir(parents=True, exist_ok=True)
        with self.turn_log.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def _voice_settings(self, person_ids: list[int]) -> dict:
        return feedback.voice_settings(self.conn, self.clock, [p for p in person_ids if p is not None])
