"""The home's SQLite database.

Set up to survive sudden power loss (BUILD-PLAN 0b.9): WAL journal with
synchronous=FULL, so a committed dose is on disk before the call returns.
secure_delete overwrites deleted rows, so "forget that" really forgets.

Times are stored as UTC ISO strings, so they sort and compare as text.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    call_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consents (
    person_id INTEGER NOT NULL REFERENCES people(id),
    kind TEXT NOT NULL,
    granted INTEGER NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (person_id, kind)
);

CREATE TABLE IF NOT EXISTS medications (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    times TEXT NOT NULL,              -- JSON list of "HH:MM", local time
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- One row per scheduled dose that has an outcome. The UNIQUE key is what
-- makes a double log impossible.
CREATE TABLE IF NOT EXISTS doses (
    id INTEGER PRIMARY KEY,
    medication_id INTEGER NOT NULL REFERENCES medications(id),
    person_id INTEGER NOT NULL REFERENCES people(id),
    slot_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('taken', 'missed', 'unknown')),
    taken_at_utc TEXT,
    logged_at_utc TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    UNIQUE (medication_id, slot_utc)
);

-- "Stop reminding me": needs a clear yes on two different days (BUILD-PLAN 2).
CREATE TABLE IF NOT EXISTS med_stop_requests (
    medication_id INTEGER PRIMARY KEY REFERENCES medications(id),
    first_yes_day TEXT NOT NULL       -- local YYYY-MM-DD
);

CREATE TABLE IF NOT EXISTS reminder_attempts (
    medication_id INTEGER NOT NULL REFERENCES medications(id),
    slot_utc TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    last_attempt_utc TEXT NOT NULL,
    spoken INTEGER NOT NULL DEFAULT 0,  -- times the voice actually said it (phase 4)
    PRIMARY KEY (medication_id, slot_utc)
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY,
    person_id INTEGER REFERENCES people(id),   -- NULL: shared
    title TEXT NOT NULL,
    starts_at_utc TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    cancelled INTEGER NOT NULL DEFAULT 0,
    told_at TEXT NOT NULL             -- when they last told us (made or moved)
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT '',
    birthday_md TEXT,                 -- "MM-DD"
    birthday_reminders INTEGER NOT NULL DEFAULT 1,
    help_contact INTEGER NOT NULL DEFAULT 0,  -- the person to contact if they need help
    for_person INTEGER REFERENCES people(id),
    created_at TEXT NOT NULL,
    telegram_chat_id TEXT,            -- linked by pairing (python -m kore.family pair)
    phone TEXT                        -- for the SMS in "help", E.164 (+34...)
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    person_id INTEGER REFERENCES people(id),   -- NULL: shared
    text TEXT NOT NULL,
    time_hhmm TEXT NOT NULL,
    rule TEXT NOT NULL,               -- once | daily | weekly:MON,THU | every:3
    anchor_date TEXT NOT NULL,        -- YYYY-MM-DD, first occurrence
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS list_items (
    id INTEGER PRIMARY KEY,
    list_name TEXT NOT NULL,
    item TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY,
    person_id INTEGER REFERENCES people(id),
    thing TEXT NOT NULL,
    place TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Events that must fire once only (appointment and birthday reminders...).
CREATE TABLE IF NOT EXISTS event_log (
    key TEXT PRIMARY KEY,
    fired_at_utc TEXT NOT NULL
);

-- Quiet, visitors (whole home), out of the house, away for days (per person). Phase 4.
CREATE TABLE IF NOT EXISTS modes (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('quiet', 'visitors', 'out', 'away')),
    person_id INTEGER REFERENCES people(id),  -- NULL for the whole home
    started_at_utc TEXT NOT NULL,
    until_utc TEXT,                            -- NULL: until they say so
    expected_back TEXT,                        -- away: the day they said they'd be back
    ended_at_utc TEXT,
    ended_how TEXT CHECK (ended_how IN ('said', 'time', 'presence', 'replaced'))
);

-- Settings changed by setup or feedback. person_id 0 = the whole home.
CREATE TABLE IF NOT EXISTS settings (
    person_id INTEGER NOT NULL DEFAULT 0,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (person_id, key)
);

-- "Just now" and "today" values, which run out on their own.
CREATE TABLE IF NOT EXISTS setting_overrides (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL DEFAULT 0,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    until_utc TEXT NOT NULL,
    feedback_id INTEGER REFERENCES feedback(id)
);

-- Feedback about how Pepe behaves (phase 4b). Only the feedback sentence is
-- stored, never the conversation around it. Lea sees and can undo every row.
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    at_utc TEXT NOT NULL,
    speaker_id INTEGER REFERENCES people(id),
    person_id INTEGER REFERENCES people(id),   -- who it applies to; NULL = the whole home
    said TEXT NOT NULL,
    understood TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('setting', 'rule', 'praise', 'safety', 'other')),
    setting_key TEXT,
    new_value TEXT,
    previous_value TEXT,                       -- what was stored before (NULL: nothing of their own)
    rule_text TEXT,
    scope TEXT NOT NULL CHECK (scope IN ('now', 'today', 'always')),
    until_utc TEXT,                            -- for 'now' and 'today'
    follow_up_question TEXT,
    follow_up_answer TEXT,
    follow_up_state TEXT NOT NULL DEFAULT 'none'
        CHECK (follow_up_state IN ('none', 'asked', 'answered', 'dropped')),
    status TEXT NOT NULL CHECK (status IN
        ('active', 'undone', 'replaced', 'held_safety', 'not_applied', 'noted')),
    replaced_by INTEGER REFERENCES feedback(id),
    review_first INTEGER NOT NULL DEFAULT 0,   -- touches medicine, alerts or night: top of Lea's page
    misunderstood INTEGER NOT NULL DEFAULT 0,
    lea_note TEXT,
    checked_at_utc TEXT                        -- the light "is that better?" a few days later
);

-- Things Pepe notices but never acts on alone ("be quiet" every afternoon, "¿qué?" often).
CREATE TABLE IF NOT EXISTS behaviour_signals (
    id INTEGER PRIMARY KEY,
    at_utc TEXT NOT NULL,
    person_id INTEGER REFERENCES people(id),
    kind TEXT NOT NULL CHECK (kind IN ('repeat_request'))
);

-- The setup conversation (phase 5): which steps are done, for the home (0) or each person.
CREATE TABLE IF NOT EXISTS setup_steps (
    person_id INTEGER NOT NULL,
    step TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('done', 'skipped')),
    at_utc TEXT NOT NULL,
    PRIMARY KEY (person_id, step)
);
CREATE TABLE IF NOT EXISTS setup_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    offered_on TEXT
);

-- What Pepe has to say, and what the manners layer decided. A row is only
-- 'done' once the voice has said it, so a restart in between says it again.
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY,
    created_at_utc TEXT NOT NULL,
    kind TEXT NOT NULL,
    category INTEGER NOT NULL,
    person_ids TEXT NOT NULL,
    data TEXT NOT NULL,
    private INTEGER NOT NULL,
    key TEXT,
    not_before_utc TEXT,
    expires_at_utc TEXT,
    status TEXT NOT NULL CHECK (status IN ('ready', 'held', 'done', 'dropped')),
    manner TEXT CHECK (manner IN ('say', 'short', 'signal', 'family')),
    reason TEXT,
    allowed_at_utc TEXT,
    done_at_utc TEXT
);
CREATE INDEX IF NOT EXISTS outbox_open ON outbox(status) WHERE status IN ('ready', 'held');

-- How they answered something Pepe started (warm, brief, not_now, ignored).
CREATE TABLE IF NOT EXISTS reply_signals (
    id INTEGER PRIMARY KEY,
    at_utc TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('warm', 'brief', 'not_now', 'ignored'))
);

CREATE TABLE IF NOT EXISTS tool_log (
    id INTEGER PRIMARY KEY,
    at_utc TEXT NOT NULL,
    turn INTEGER NOT NULL,
    speaker_id INTEGER,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    result TEXT NOT NULL
);

-- Phase 6: messages to the family (and device messages to Lea). Kept until sent,
-- so a power cut or a dead connection only delays them.
CREATE TABLE IF NOT EXISTS family_out (
    id INTEGER PRIMARY KEY,
    created_at_utc TEXT NOT NULL,
    kind TEXT NOT NULL,               -- help | missed_meds | stop_request | message | test | operator
    contact_id INTEGER REFERENCES contacts(id),   -- NULL: to Lea (the operator)
    channel TEXT NOT NULL CHECK (channel IN ('telegram', 'sms')),
    text TEXT NOT NULL,
    from_person INTEGER REFERENCES people(id),    -- who asked for it, to tell them how it went
    status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_try_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL,
    sent_at_utc TEXT,
    error TEXT,
    group_id INTEGER,                 -- one request (a call for help goes by Telegram and SMS)
    told INTEGER NOT NULL DEFAULT 0   -- 0 nothing to tell, 1 they heard "not yet", 2 settled
);
CREATE INDEX IF NOT EXISTS family_out_pending ON family_out(status) WHERE status = 'pending';

-- Messages from family (Telegram), read aloud only when asked. Quoted content.
CREATE TABLE IF NOT EXISTS family_in (
    id INTEGER PRIMARY KEY,
    received_at_utc TEXT NOT NULL,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    text TEXT NOT NULL,
    read_at_utc TEXT
);

-- Small values: the Telegram update offset, Lea's chat, pairing codes.
CREATE TABLE IF NOT EXISTS family_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- The home web page (WEB-PLAN.md): who can log in, their logged-in devices, the one-time login codes.
CREATE TABLE IF NOT EXISTS web_accounts (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('person', 'family', 'operator')),
    person_id INTEGER REFERENCES people(id),     -- role person
    contact_id INTEGER REFERENCES contacts(id),  -- role family
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS web_logins (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES web_accounts(id),
    token_hash TEXT NOT NULL UNIQUE,              -- only a hash of the cookie is kept
    device TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS web_codes (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,
    person_id INTEGER REFERENCES people(id),
    code_hash TEXT NOT NULL,
    ticket_hash TEXT,                             -- the browser that asked for it; NULL: any (Lea's, from the box)
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'used', 'burned', 'cancelled')),
    contact_id INTEGER REFERENCES contacts(id)   -- role family: who it lets in
);

-- The conversation a person sees on the web page: what they wrote in the chat and Pepe's answers, and (only
-- with their "keep_conversations" permission) what was said by voice. Deleted after 90 days.
CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    who TEXT NOT NULL CHECK (who IN ('person', 'pepe')),
    channel TEXT NOT NULL CHECK (channel IN ('chat', 'voice')),
    text TEXT NOT NULL,
    at_utc TEXT NOT NULL,
    photo_id INTEGER REFERENCES photos(id)      -- a photo they sent with this line (W4)
);
CREATE INDEX IF NOT EXISTS chat_person ON chat (person_id, at_utc);

-- One row per message written to Pepe, for the hourly limit (deleting the conversation doesn't reset it).
CREATE TABLE IF NOT EXISTS chat_turns (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    at_utc TEXT NOT NULL
);

-- Login attempts and code requests, by client (a hash of its address), for the limits.
CREATE TABLE IF NOT EXISTS web_attempts (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('wrong', 'code')),
    client TEXT NOT NULL,
    at_utc TEXT NOT NULL
);

-- Lines code wants Pepe to say out loud (a login code). The voice loop speaks them, then deletes them;
-- a login code's line is also deleted once the code is used, burned, cancelled or expired.
CREATE TABLE IF NOT EXISTS say_queue (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    said_at TEXT,
    code_id INTEGER REFERENCES web_codes(id)
);

-- Photos sent to Pepe on the web page (W4): the file (shrunk, without location or other metadata) is in the
-- photos folder next to the database, named by `file`. Deleted with the conversation line that holds it.
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    file TEXT NOT NULL UNIQUE,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

-- One row per photo sent, for the daily limit (deleting the conversation doesn't reset it). Two days kept.
CREATE TABLE IF NOT EXISTS photo_log (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    at_utc TEXT NOT NULL
);

-- An appointment a family member proposed on the web page (W4). Never saved by itself: the person sees it and
-- confirms it like their own (read-back and "Sí"), or says no. Family words are outside text.
CREATE TABLE IF NOT EXISTS family_proposals (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    person_id INTEGER REFERENCES people(id),    -- for whom; NULL: for both
    title TEXT NOT NULL,
    starts_at_utc TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'accepted', 'declined', 'withdrawn')),
    answered_at TEXT
);

-- Lea's console: a medicine change or an appointment she proposes. It changes nothing by itself: the person sees
-- it (and Pepe mentions a medicine change) and saves it only with their own read-back and yes.
CREATE TABLE IF NOT EXISTS operator_proposals (
    id INTEGER PRIMARY KEY,
    person_id INTEGER REFERENCES people(id),    -- NULL: for both
    kind TEXT NOT NULL CHECK (kind IN ('medication', 'appointment')),
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'accepted', 'declined', 'withdrawn', 'lapsed')),
    answered_at TEXT
);

-- Lea's console: "Mark a problem" on a conversation she may read. Deleted with it, never backed up.
CREATE TABLE IF NOT EXISTS console_marks (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    chat_id INTEGER NOT NULL,
    note TEXT NOT NULL,
    at_utc TEXT NOT NULL
);

-- A change made on the web page, waiting for the person's "Sí" (the read-back page). Only the latest one per
-- person can be confirmed; it lapses after 15 minutes and is deleted after a day.
CREATE TABLE IF NOT EXISTS web_pending (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    readback TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'cancelled', 'expired', 'failed'))
);

-- Weather and news (phase 6c): the home's place, the feeds, and the last answers fetched.
CREATE TABLE IF NOT EXISTS info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    at_utc TEXT NOT NULL
);
"""


def connect(path: str | Path = ":memory:", shared: bool = False) -> sqlite3.Connection:
    """shared: the connection may be used from several threads (the web server), one at a time (the caller
    holds a lock)."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=not shared)  # explicit transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA secure_delete = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA busy_timeout = 5000")  # the voice loop and the web page share the file
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Columns added after a table existed (CREATE IF NOT EXISTS doesn't add them)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(reminder_attempts)")}
    if "spoken" not in cols:
        conn.execute("ALTER TABLE reminder_attempts ADD COLUMN spoken INTEGER NOT NULL DEFAULT 0")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(contacts)")}
    if "help_contact" not in cols:
        conn.execute("ALTER TABLE contacts ADD COLUMN help_contact INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE contacts ADD COLUMN for_person INTEGER REFERENCES people(id)")
    if "telegram_chat_id" not in cols:
        conn.execute("ALTER TABLE contacts ADD COLUMN telegram_chat_id TEXT")
        conn.execute("ALTER TABLE contacts ADD COLUMN phone TEXT")
    if "contact_id" not in {r["name"] for r in conn.execute("PRAGMA table_info(web_codes)")}:
        conn.execute("ALTER TABLE web_codes ADD COLUMN contact_id INTEGER REFERENCES contacts(id)")
    if "photo_id" not in {r["name"] for r in conn.execute("PRAGMA table_info(chat)")}:
        conn.execute("ALTER TABLE chat ADD COLUMN photo_id INTEGER REFERENCES photos(id)")


def utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime: always pass an aware time")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_utc_iso(s: str, tz) -> datetime:
    return datetime.fromisoformat(s).astimezone(tz)
