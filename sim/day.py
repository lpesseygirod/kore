"""'A day with Pepe' (PEPE.md section 5), simulated with a fake clock.

The brain is scripted here (no Claude, no network): each line the person says
comes with the tool calls Claude is expected to make. Events from the engine
go through the manners gate (quiet mode, visitors, night) and are worded with
the offline fallback phrases. This checks the deterministic half of the day;
the real brain comes with phase 1.

Run:  uv run python -m sim.day
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta

from kore.brain import fallback, tools
from kore.brain.session import Person, Session
from kore.manners.gate import Manners
from kore.memory import meds, store
from kore.memory.db import connect
from kore.scheduler.clock import HOME_TZ, FakeClock
from kore.scheduler.engine import Engine

DAY = datetime(2026, 11, 9, tzinfo=HOME_TZ)  # a Monday


@dataclass
class Line:
    time: str
    who: str
    text: str


def setup(conn, clock):
    carmen = meds.add_person(conn, clock, "Carmen")
    antonio = meds.add_person(conn, clock, "Antonio", "Toni")
    meds.add_medication(conn, clock, carmen, "tensión", ["09:00", "21:30"], "la pastilla de la tensión")
    meds.add_medication(conn, clock, carmen, "vitamina D", ["09:00"])
    store.add_to_list(conn, clock, ["leche"])
    store.add_appointment(conn, clock, None, "visita de tu hermana Rosa", DAY.replace(hour=17))
    store.add_appointment(conn, clock, carmen, "el médico", DAY.replace(hour=11) + timedelta(days=1), "el centro de salud")
    for pid in (carmen, antonio):
        store.set_consent(conn, clock, pid, "water_nudges", pid == carmen)
        store.set_consent(conn, clock, pid, "movement_nudges", pid == carmen)
        store.set_consent(conn, clock, pid, "conversation_starters", pid == carmen)
    return Session([Person(carmen, "Carmen", "Carmen"), Person(antonio, "Antonio", "Toni")])


# What the person says, and the tool calls the brain should make in reply.
# A reply of None means: use the tool result's "say" (the question code asked for).
SCRIPT = {
    "08:30": [("presence", "moving")],
    "09:02": [("say", "Sí, ya me las he tomado", [("mark_dose_taken", {"person": "Carmen"})])],
    "09:03": [("say", "Sí", [("mark_dose_taken", {"person": "Carmen"})])],
    "10:30": [("say", "Pepe, añade pan y huevos a la lista", [("add_to_list", {"items": ["pan", "huevos"]})])],
    "12:15": [("say", "Me voy a la compra", [("going_shopping", {})])],
    "12:17": [("presence", "absent")],
    "13:00": [("presence", "moving"), ("say", "Ya estoy aquí. No había huevos", [("back_from_shopping", {"not_found": ["huevos"]})])],
    "13:30": [("presence", "still")],
    "16:00": [("presence", "moving"), ("say", "Pepe, cállate, que quiero descansar", [("be_quiet", {})])],
    "17:00": [("say", "Ha llegado mi hermana", [("visitors_arrived", {})])],
    "19:00": [("say", "Ya se ha ido Rosa", [("visitors_left", {})])],
    "21:31": [("say", "Soy Carmen, ya está", [("mark_dose_taken", {"person": "Carmen"})])],
}


def word(tool_name: str, result: dict) -> str:
    """Stand-in for Claude's wording of a tool result."""
    if result.get("error") == "speaker_unknown":
        return "¿Eres Carmen?"
    if not result.get("ok"):
        return result.get("say", "Perdona, no lo he entendido.")
    if tool_name == "mark_dose_taken":
        return "Perfecto." if result["marked"] else "Ya lo tenía apuntado."
    if tool_name == "add_to_list":
        return f"Hecho. La lista tiene {fallback._join(result['items'])}."
    if tool_name == "going_shopping":
        return f"Recuerda: {fallback._join(result['items'])}."
    if tool_name == "be_quiet":
        return "Claro."
    if tool_name in ("visitors_arrived", "visitors_left"):
        return None  # nothing to say
    if tool_name == "back_from_shopping":
        return f"Dejo {fallback._join(result['kept_for_next_time'])} en la lista para la próxima vez."
    return "Hecho."


def run_day(path=":memory:") -> tuple[list[Line], dict]:
    conn = connect(path)
    clock = FakeClock(DAY.replace(hour=6))
    session = setup(conn, clock)
    ctx = tools.Context(conn, clock, session)
    engine = Engine(conn, clock)
    manners = Manners(conn, clock)
    transcript: list[Line] = []

    def pepe(text):
        if text:
            transcript.append(Line(clock.now().strftime("%H:%M"), "Pepe", text))

    end = DAY.replace(hour=23, minute=59)
    while clock.now() < end:
        clock.advance(minutes=1)
        hhmm = clock.now().strftime("%H:%M")
        for step in SCRIPT.get(hhmm, []):
            if step[0] == "presence":
                manners.submit(engine.on_presence(step[1]))
                manners.on_presence(step[1])
            else:
                _, text, calls = step
                session.begin_turn(text)
                transcript.append(Line(hhmm, "Persona", text))
                for name, args in calls:
                    pepe(word(name, tools.call(ctx, name, args)))
        manners.submit(engine.tick())
        manners.tick()
        for item in manners.next_items():
            pepe(fallback.phrase(item.event, item.manner))
            manners.mark_done(item)
    doses = {f"{r['name']} {r['slot_utc']}": r["status"] for r in conn.execute(
        "SELECT m.name, d.slot_utc, d.status FROM doses d JOIN medications m ON m.id = d.medication_id")}
    return transcript, {"doses": doses, "list": store.list_items(conn)}


if __name__ == "__main__":
    t0 = _time.perf_counter()
    transcript, state = run_day()
    for line in transcript:
        print(f"{line.time}  {line.who:8} {line.text}")
    print(f"\nDoses: {state['doses']}\nList: {state['list']}")
    print(f"Simulated a full day in {_time.perf_counter() - t0:.2f} s")
