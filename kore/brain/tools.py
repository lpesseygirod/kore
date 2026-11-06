"""The tools Claude can call. Every safety rule lives here or below, never in the prompt.

- Personal tools need a known speaker (session.require_speaker).
- Medications, appointments and reminders are only saved through
  propose_* → confirm_action, and confirm_action only works when the person's
  next words are a clear yes (checked by code).
- Outside text (news, family messages) taints the turn: after a tool returns
  it, no tool that changes anything can run in that turn.
- Every call is logged in tool_log.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from kore.brain.session import ConfirmationRefused, Session, SpeakerMismatch, SpeakerUnknown
from kore.manners import modes
from kore.manners.gate import Manners
from kore.memory import meds, settings, store
from kore.memory.db import from_utc_iso, utc_iso
from kore.scheduler.clock import Clock, local_at


@dataclass
class Context:
    conn: sqlite3.Connection
    clock: Clock
    session: Session
    tainted_turn: int | None = None  # the turn in which outside text was read


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    handler: Callable[[Context, dict], dict]
    writes: bool = False
    required: list[str] = field(default_factory=list)
    outside_text: bool = False  # returns text from outside the home

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {"type": "object", "properties": self.params, "required": self.required},
        }


TOOLS: dict[str, Tool] = {}


def tool(name: str, description: str, params: dict | None = None, *, writes=False, required=(), outside_text=False):
    def register(fn):
        TOOLS[name] = Tool(name, description, params or {}, fn, writes, list(required), outside_text)
        return fn

    return register


def schemas() -> list[dict]:
    return [t.schema() for t in TOOLS.values()]


def call(ctx: Context, name: str, args: dict) -> dict:
    """Run one tool call from Claude. Never raises: errors come back as data Claude can word."""
    t = TOOLS.get(name)
    if t is None:
        result = {"ok": False, "error": "unknown_tool"}
    elif t.writes and ctx.tainted_turn == ctx.session.turn:
        result = {
            "ok": False,
            "error": "outside_text_this_turn",
            "say": "Outside text was read this turn, so nothing can be changed now. "
            "If the person wants something done, they can ask in their own words.",
        }
    else:
        try:
            result = t.handler(ctx, args)
        except SpeakerUnknown as e:
            who = ctx.session.person(e.candidate_id).call_name if e.candidate_id else None
            result = {
                "ok": False,
                "error": "speaker_unknown",
                "say": f"Ask, kindly: 'Is that you, {who}?'" if who else "Ask kindly who is speaking.",
            }
        except SpeakerMismatch as e:
            result = {
                "ok": False,
                "error": "speaker_is_someone_else",
                "speaker": ctx.session.person(e.speaker_id).call_name,
                "say": "This is personal to the other person. Don't record or reveal it for them.",
            }
        except ConfirmationRefused as e:
            result = {"ok": False, "error": "not_confirmed", "reason": e.reason}
        except meds.ClockUntrusted as e:
            result = {"ok": False, "error": "clock_untrusted", "say": e.say}
        except (ValueError, KeyError) as e:
            result = {"ok": False, "error": "bad_input", "detail": str(e)}
        if t.outside_text and result.get("ok"):
            ctx.tainted_turn = ctx.session.turn
    ctx.conn.execute(
        "INSERT INTO tool_log (at_utc, turn, speaker_id, tool, args, result) VALUES (?, ?, ?, ?, ?, ?)",
        (utc_iso(ctx.clock.now()), ctx.session.turn, ctx.session.speaker_id, name,
         json.dumps(args, ensure_ascii=False), json.dumps(result, ensure_ascii=False, default=str)),
    )
    return result


# --- helpers ----------------------------------------------------------------

PERSON = {"type": "string", "description": "Name of the person in the home. Omit to mean whoever is speaking."}


def _person_id(ctx: Context, args: dict) -> int:
    """The person a personal tool is about. Requires that they are the one speaking."""
    name = args.get("person")
    s = ctx.session
    if name:
        p = s.find_person(name)
        if p is None:
            raise ValueError(f"no one called {name!r} lives here")
        pid = p.id
    elif s.speaker_id is not None:
        pid = s.speaker_id
    else:
        s.ask_identity(None)
        raise SpeakerUnknown(None)
    s.require_speaker(pid)
    return pid


def _person_or_shared(ctx: Context, args: dict) -> int | None:
    return None if args.get("shared") else _person_id(ctx, args)


# --- who is speaking --------------------------------------------------------


@tool("ask_who_is_speaking", "Use when you need to know who is talking. Say the returned question exactly.",
      {"candidate": {"type": "string", "description": "The person you think it is, if any."}}, writes=True)
def _ask_who(ctx: Context, args: dict) -> dict:
    p = ctx.session.find_person(args["candidate"]) if args.get("candidate") else None
    return {"ok": True, "ask": ctx.session.ask_identity(p.id if p else None)}


# --- medication -------------------------------------------------------------


@tool("get_med_status", "What is recorded about today's doses for a person. The only source for "
      "'Did I take my pill?'.", {"person": PERSON})
def _med_status(ctx: Context, args: dict) -> dict:
    s = ctx.session
    name = args.get("person")
    target = s.find_person(name) if name else None
    if target and s.speaker_id is not None and s.speaker_id != target.id:
        # Asking about the other person: only if they agreed to share.
        if not store.has_consent(ctx.conn, target.id, "share_with_partner"):
            return {"ok": False, "error": "private", "say": f"That's {target.call_name}'s to tell. Offer to ask them."}
        return meds.status(ctx.conn, ctx.clock, target.id)
    return meds.status(ctx.conn, ctx.clock, _person_id(ctx, args))


@tool("mark_dose_taken", "Record that the person took their current dose ('I took it', 'I already took it'). "
      "Code picks the dose; it can't be recorded twice.",
      {"person": PERSON, "medication": {"type": "string", "description": "Only if they named one."}}, writes=True)
def _mark_taken(ctx: Context, args: dict) -> dict:
    pid = _person_id(ctx, args)
    med_id = None
    if args.get("medication"):
        med = _one_medication(ctx, pid, args["medication"])
        if isinstance(med, dict):
            return med
        med_id = med["id"]
    return meds.mark_taken(ctx.conn, ctx.clock, pid, med_id)


def _one_medication(ctx: Context, pid: int, name: str):
    """The single medicine `name` means, or an error dict asking which."""
    found = meds.find_medications(ctx.conn, pid, name)
    if len(found) == 1:
        return found[0]
    if not found:
        return {"ok": False, "error": "unknown_medication", "say": "Ask which medicine they mean. Never guess."}
    return {"ok": False, "error": "ambiguous_medication",
            "options": [f"{m['name']} ({m['description']})" if m["description"] else m["name"] for m in found],
            "say": "Ask which one they mean. Never guess."}


@tool("propose_medication", "Prepare to save a new medicine. Nothing is saved until the person says yes "
      "to the read-back and you call confirm_action.",
      {"person": PERSON, "name": {"type": "string"}, "times": {"type": "array", "items": {"type": "string"},
       "description": "HH:MM, 24h"}, "description": {"type": "string", "description": "e.g. 'the white one'"}},
      writes=True, required=["name", "times"])
def _propose_med(ctx: Context, args: dict) -> dict:
    pid = _person_id(ctx, args)
    times = meds._valid_times(args["times"])
    who = ctx.session.person(pid).call_name
    readback = f"{args['name']} for {who} at {', '.join(times)}. Is that right?"
    a = ctx.session.propose("add_medication", {"person_id": pid, "name": args["name"], "times": times,
                                               "description": args.get("description", "")}, readback)
    return {"ok": True, "action_id": a.id, "say_and_wait": readback}


@tool("propose_medication_change", "Prepare to change a medicine's times, or stop reminding it "
      "(times empty). Needs the person's yes. If they say they stopped taking it, gently ask whether the "
      "doctor changed it before proposing.",
      {"person": PERSON, "medication": {"type": "string"},
       "times": {"type": "array", "items": {"type": "string"}}}, writes=True, required=["medication", "times"])
def _propose_med_change(ctx: Context, args: dict) -> dict:
    pid = _person_id(ctx, args)
    med = _one_medication(ctx, pid, args["medication"])
    if isinstance(med, dict):
        return med
    if args["times"]:
        times = meds._valid_times(args["times"])
        readback = f"{med['name']} at {', '.join(times)} from now on. Is that right?"
        kind = "change_medication"
    else:
        times = []
        readback = f"I'll stop reminding you about {med['name']}. Is that right?"
        kind = "stop_medication"
    a = ctx.session.propose(kind, {"medication_id": med["id"], "times": times}, readback)
    return {"ok": True, "action_id": a.id, "say_and_wait": readback}


# --- appointments and reminders ---------------------------------------------


@tool("propose_appointment", "Prepare to save an appointment. Needs the person's yes.",
      {"person": PERSON, "shared": {"type": "boolean", "description": "For both people at home."},
       "title": {"type": "string"}, "starts_at": {"type": "string", "description": "Local time, YYYY-MM-DDTHH:MM"},
       "location": {"type": "string"}}, writes=True, required=["title", "starts_at"])
def _propose_appt(ctx: Context, args: dict) -> dict:
    pid = _person_or_shared(ctx, args)
    starts = datetime.fromisoformat(args["starts_at"])
    starts = starts.replace(tzinfo=ctx.clock.tz) if starts.tzinfo is None else starts.astimezone(ctx.clock.tz)
    readback = f"{args['title']} on {starts.strftime('%A %d %B at %H:%M')}. Is that right?"
    a = ctx.session.propose("add_appointment", {"person_id": pid, "title": args["title"],
                                                "starts_at": starts.isoformat(),
                                                "location": args.get("location", "")}, readback)
    return {"ok": True, "action_id": a.id, "say_and_wait": readback}


@tool("propose_reminder", "Prepare a reminder made by voice ('every Thursday it's rubbish day'). "
      "rule: once | daily | weekly:MON,THU | every:N (days). Needs the person's yes.",
      {"person": PERSON, "shared": {"type": "boolean"}, "text": {"type": "string"},
       "time": {"type": "string", "description": "HH:MM"}, "rule": {"type": "string"},
       "first_day": {"type": "string", "description": "YYYY-MM-DD, default today"}},
      writes=True, required=["text", "time", "rule"])
def _propose_reminder(ctx: Context, args: dict) -> dict:
    pid = _person_or_shared(ctx, args)
    store._check_rule(args["rule"])
    time = meds._valid_times([args["time"]])[0]
    first = date.fromisoformat(args["first_day"]) if args.get("first_day") else ctx.clock.now().date()
    readback = f"I'll remind you: '{args['text']}' at {time} ({args['rule']}). Is that right?"
    a = ctx.session.propose("add_reminder", {"person_id": pid, "text": args["text"], "time": time,
                                             "rule": args["rule"], "anchor": first.isoformat()}, readback)
    return {"ok": True, "action_id": a.id, "say_and_wait": readback}


@tool("get_appointments", "Upcoming appointments for the speaker (and shared ones).", {"person": PERSON})
def _get_appts(ctx: Context, args: dict) -> dict:
    pid = _person_id(ctx, args)
    return {"ok": True, "appointments": [
        {k: v for k, v in a.items() if k != "starts_at"} for a in store.appointments(ctx.conn, ctx.clock, pid)]}


# --- confirming -------------------------------------------------------------

EXECUTORS: dict[str, Callable[[Context, dict], dict]] = {}


def executor(kind):
    def register(fn):
        EXECUTORS[kind] = fn
        return fn

    return register


@executor("add_medication")
def _exec_add_med(ctx, p):
    return {"medication_id": meds.add_medication(ctx.conn, ctx.clock, p["person_id"], p["name"], p["times"],
                                                 p["description"])}


@executor("change_medication")
def _exec_change_med(ctx, p):
    meds.change_medication_times(ctx.conn, ctx.clock, p["medication_id"], p["times"])
    return {}


@executor("stop_medication")
def _exec_stop_med(ctx, p):
    if meds.request_stop(ctx.conn, ctx.clock, p["medication_id"]):
        person = ctx.conn.execute("SELECT person_id FROM medications WHERE id = ?", (p["medication_id"],)).fetchone()
        # Phase 6 sends it; the person agreed to family alerts at setup.
        return {"stopped": True, "tell_family": store.has_consent(ctx.conn, person["person_id"], "family_missed_meds")}
    return {"stopped": False,
            "say": "The reminder stays for now. Say kindly you'll keep it short and quiet, suggest mentioning it "
                   "to their doctor, and that if they still want it stopped on another day, they can tell you again."}


@executor("add_appointment")
def _exec_add_appt(ctx, p):
    starts = datetime.fromisoformat(p["starts_at"])
    return {"appointment_id": store.add_appointment(ctx.conn, ctx.clock, p["person_id"], p["title"], starts,
                                                    p["location"])}


@executor("go_away")
def _exec_go_away(ctx, p):
    back_on = date.fromisoformat(p["back_on"]) if p["back_on"] else None
    for pid in p["person_ids"]:
        modes.start(ctx.conn, ctx.clock, "away", pid, expected_back=back_on)
    return {"say": "Wish them a good trip."}


@executor("add_reminder")
def _exec_add_reminder(ctx, p):
    return {"reminder_id": store.add_reminder(ctx.conn, ctx.clock, p["person_id"], p["text"], p["time"], p["rule"],
                                              date.fromisoformat(p["anchor"]))}


@tool("confirm_action", "Carry out a proposed action after the person clearly said yes to the read-back. "
      "Code checks their words; if it refuses, ask again simply.",
      {"action_id": {"type": "integer"}}, writes=True, required=["action_id"])
def _confirm(ctx: Context, args: dict) -> dict:
    action = ctx.session.take_confirmed(int(args["action_id"]))
    return {"ok": True, "done": action.kind, **EXECUTORS[action.kind](ctx, action.payload)}


@tool("cancel_action", "Drop a proposed action (they said no, or corrected it: then propose again).",
      {"action_id": {"type": "integer"}}, writes=True, required=["action_id"])
def _cancel(ctx: Context, args: dict) -> dict:
    ctx.session.cancel(int(args["action_id"]))
    return {"ok": True}


# --- lists and places (shared, no confirmation: just say "Done") ------------

ITEMS = {"type": "array", "items": {"type": "string"}}
LIST = {"type": "string", "description": "Default: shopping. Others: e.g. 'for the doctor'."}


@tool("read_list", "Read a list.", {"list": LIST})
def _read_list(ctx: Context, args: dict) -> dict:
    return {"ok": True, "items": store.list_items(ctx.conn, args.get("list") or store.SHOPPING)}


@tool("add_to_list", "Add items to a list.", {"items": ITEMS, "list": LIST}, writes=True, required=["items"])
def _add_list(ctx: Context, args: dict) -> dict:
    return {"ok": True, "items": store.add_to_list(ctx.conn, ctx.clock, args["items"],
                                                   args.get("list") or store.SHOPPING)}


@tool("remove_from_list", "Remove items from a list.", {"items": ITEMS, "list": LIST}, writes=True,
      required=["items"])
def _remove_list(ctx: Context, args: dict) -> dict:
    return {"ok": True, "items": store.remove_from_list(ctx.conn, args["items"], args.get("list") or store.SHOPPING)}


@tool("going_shopping", "They said they're going shopping: returns the list to read to them.")
def _going(ctx: Context, args: dict) -> dict:
    # Things for them wait while they're out, but only if we know who went: with
    # two at home and nobody named, nothing changes (the other can say "not mine").
    # Reading the list is fine after outside text; changing the mode is not.
    if ctx.session.speaker_id is not None and ctx.tainted_turn != ctx.session.turn:
        modes.start(ctx.conn, ctx.clock, "out", ctx.session.speaker_id)
    return {"ok": True, "items": store.list_items(ctx.conn)}


@tool("back_from_shopping", "They're back and said what they didn't find. Everything else leaves the list.",
      {"not_found": ITEMS}, writes=True, required=["not_found"])
def _back(ctx: Context, args: dict) -> dict:
    if ctx.session.speaker_id is not None:
        modes.end(ctx.conn, ctx.clock, "out", ctx.session.speaker_id)
    return {"ok": True, "kept_for_next_time": store.back_from_shopping(ctx.conn, args["not_found"])}


# --- manners: quiet, visitors, away -------------------------------------------


@tool("be_quiet", "They asked you to be quiet. Say only 'Claro.' Medicine and appointment reminders still "
      "come, very short. Give minutes or until if they said how long; otherwise two hours.",
      {"minutes": {"type": "integer"},
       "until": {"type": "string", "description": "HH:MM, or 'night' for 'until tonight'."}}, writes=True)
def _be_quiet(ctx: Context, args: dict) -> dict:
    now = ctx.clock.now()
    until = None
    if args.get("minutes"):
        minutes = int(args["minutes"])
        if minutes <= 0:
            raise ValueError("minutes must be positive")
        until = now.astimezone(timezone.utc) + timedelta(minutes=minutes)  # real minutes, even across DST
    elif args.get("until"):
        hhmm = args["until"]
        if hhmm == "night":
            # Said at night already: until the morning, not until tomorrow night.
            hhmm = settings.get(ctx.conn, "night_end" if Manners(ctx.conn, ctx.clock).is_night() else "night_start")
        until = local_at(now.date(), hhmm, ctx.clock.tz)
        if until <= now:
            until = local_at(now.date() + timedelta(days=1), hhmm, ctx.clock.tz)
    modes.start(ctx.conn, ctx.clock, "quiet", until=until)
    row = modes.active(ctx.conn, ctx.clock, "quiet")
    return {"ok": True, "quiet_until": from_utc_iso(row["until_utc"], ctx.clock.tz).strftime("%H:%M"),
            "say": "Only 'Claro.' Nothing else."}


@tool("can_talk_again", "They said you can talk again before the quiet time was up.", writes=True)
def _can_talk(ctx: Context, args: dict) -> dict:
    return {"ok": modes.end(ctx.conn, ctx.clock, "quiet")}


@tool("visitors_arrived", "They said they have visitors (or are on the phone). Stay discreet: nothing private "
      "out loud until they say the visitors have left.", writes=True)
def _visitors(ctx: Context, args: dict) -> dict:
    modes.start(ctx.conn, ctx.clock, "visitors")
    return {"ok": True}


@tool("visitors_left", "They said the visitors have gone.", writes=True)
def _visitors_left(ctx: Context, args: dict) -> dict:
    return {"ok": modes.end(ctx.conn, ctx.clock, "visitors")}


def _who_goes(ctx: Context, args: dict) -> list[int]:
    """Only someone who is going can say so, and we must know who is speaking: an
    unknown voice can't pause anyone's medicine reminders."""
    s = ctx.session
    if s.speaker_id is None:
        s.ask_identity(None)
        raise SpeakerUnknown(None)
    if args.get("everyone") or len(s.household) == 1:
        return [p.id for p in s.household]
    return [_person_id(ctx, args)]


@tool("propose_going_away", "They're going away for days (hospital, a holiday, a relative's home). Reminders "
      "pause for them until they're back, so this needs their yes to the read-back, then confirm_action.",
      {"person": PERSON, "everyone": {"type": "boolean", "description": "Everyone in the home is going."},
       "back_on": {"type": "string", "description": "YYYY-MM-DD, if they said when they'll be back."}},
      writes=True)
def _propose_away(ctx: Context, args: dict) -> dict:
    back_on = date.fromisoformat(args["back_on"]) if args.get("back_on") else None
    if back_on is not None and back_on < ctx.clock.now().date():
        raise ValueError("back_on is in the past")
    people = _who_goes(ctx, args)
    names = " and ".join(ctx.session.person(p).call_name for p in people)
    readback = (f"{names} away{' until ' + back_on.isoformat() if back_on else ''}: reminders pause until "
                "you tell me you're back. Is that right?")
    a = ctx.session.propose("go_away", {"person_ids": people, "back_on": back_on.isoformat() if back_on else None},
                            readback)
    return {"ok": True, "action_id": a.id, "say_and_wait": readback}


@tool("back_home", "Someone is back home after being away for days (they say so, or the other person "
      "does: 'Carmen ya ha vuelto').", {"person": PERSON, "everyone": {"type": "boolean"}}, writes=True)
def _back_home(ctx: Context, args: dict) -> dict:
    # Anyone known may say someone else is back: it only resumes reminders (the safe side).
    s = ctx.session
    if s.speaker_id is None:
        s.ask_identity(None)
        raise SpeakerUnknown(None)
    if args.get("everyone"):
        people = [p.id for p in s.household]
    elif args.get("person"):
        found = s.find_person(args["person"])
        if found is None:
            raise ValueError(f"no one called {args['person']!r} lives here")
        people = [found.id]
    else:
        people = [s.speaker_id]
    back = [p for p in people if modes.end(ctx.conn, ctx.clock, "away", p)]
    return {"ok": bool(back), "back": [ctx.session.person(p).call_name for p in back],
            **({} if back else {"error": "not_away"})}


@tool("remember_place", "Remember where something was put.", {"thing": {"type": "string"},
      "place": {"type": "string"}}, writes=True, required=["thing", "place"])
def _remember_place(ctx: Context, args: dict) -> dict:
    return {"ok": True, "place_id": store.remember_place(ctx.conn, ctx.clock, ctx.session.speaker_id,
                                                         args["thing"], args["place"])}


@tool("where_is", "Where something was put, if they told you.", {"thing": {"type": "string"}}, required=["thing"])
def _where(ctx: Context, args: dict) -> dict:
    found = store.where_is(ctx.conn, args["thing"])
    return {"ok": True, "found": found, **({} if found else {"say": "Say you don't know; never guess."})}


# --- memory of the person ---------------------------------------------------


@tool("remember_about_person", "Save something they told you about themselves, for later conversations. "
      "kind: interest | story | person | preference | health_note | other.",
      {"person": PERSON, "kind": {"type": "string"}, "text": {"type": "string"}}, writes=True,
      required=["kind", "text"])
def _remember(ctx: Context, args: dict) -> dict:
    pid = _person_id(ctx, args)
    return {"ok": True, "note_id": store.add_note(ctx.conn, ctx.clock, pid, args["kind"], args["text"])}


@tool("what_do_you_know_about_me", "Everything stored about the speaker, to read back simply.", {"person": PERSON})
def _what_known(ctx: Context, args: dict) -> dict:
    pid = _person_id(ctx, args)
    return {
        "ok": True,
        "notes": store.notes(ctx.conn, pid),
        "medications": [{"name": m["name"], "times": json.loads(m["times"])} for m in meds.medications(ctx.conn, pid)],
        "appointments": [a["title"] + " " + a["when"] for a in store.appointments(ctx.conn, ctx.clock, pid)],
    }


@tool("forget", "'Forget that': delete a note for good. Use the note_id you saved or read.",
      {"person": PERSON, "note_id": {"type": "integer"}}, writes=True, required=["note_id"])
def _forget(ctx: Context, args: dict) -> dict:
    pid = _person_id(ctx, args)
    gone = store.forget_note(ctx.conn, pid, int(args["note_id"]))
    return {"ok": gone, **({} if gone else {"error": "not_found"})}
