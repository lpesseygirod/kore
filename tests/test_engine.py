from datetime import date, timedelta

import pytest

from kore.memory import meds, store
from kore.scheduler.engine import Engine
from tests.conftest import at


def run(engine, clock, until, step=timedelta(minutes=1)):
    """Tick every simulated minute until `until`; return (time, event) pairs."""
    out = []
    while clock.now() < until:
        clock.advance(seconds=step.total_seconds())
        out += [(clock.now().strftime("%H:%M"), e) for e in engine.tick()]
    return out


def kinds(events):
    return [(t, e.kind) for t, e in events]


@pytest.fixture
def carmen_pills(conn, clock, home):
    meds.add_medication(conn, clock, home["carmen"], "tensión", ["09:00", "21:30"])
    meds.add_medication(conn, clock, home["carmen"], "vitamina D", ["09:00"])
    return home


def test_medication_reminder_with_two_retries_then_missed(conn, clock, carmen_pills):
    e = Engine(conn, clock)
    events = run(e, clock, at(12, 0))
    meds_events = [(t, ev) for t, ev in events if ev.kind == "medication_reminder"]
    assert [t for t, _ in meds_events] == ["09:00", "09:20", "09:45"]
    first = meds_events[0][1]
    assert {m["name"] for m in first.data["medications"]} == {"tensión", "vitamina D"}  # one reminder, both pills
    assert first.private and first.category == 1
    statuses = {r["status"] for r in conn.execute("SELECT status FROM doses")}
    assert statuses == {"missed"}


def test_taken_stops_the_retries(conn, clock, carmen_pills, home):
    e = Engine(conn, clock)
    run(e, clock, at(9, 5))
    meds.mark_taken(conn, clock, home["carmen"])
    later = run(e, clock, at(12, 0))
    assert [k for _, k in kinds(later)] == []


def test_restart_after_power_cut_neither_repeats_nor_loses(conn, clock, carmen_pills):
    run(Engine(conn, clock), clock, at(9, 21))  # attempts at 9:00 and 9:20
    clock.set(at(9, 30))  # power back, new process
    later = run(Engine(conn, clock), clock, at(10, 0))
    assert [t for t, k in kinds(later) if k == "medication_reminder"] == ["09:45"]


def test_device_off_during_dose_records_unknown_not_missed(conn, clock, carmen_pills):
    clock.set(at(11, 30))  # it was off from before 9 until now
    run(Engine(conn, clock), clock, at(11, 31))
    assert {r["status"] for r in conn.execute("SELECT status FROM doses")} == {"unknown"}


def test_late_catch_up_fires_once_not_three_times(conn, clock, carmen_pills):
    clock.set(at(10, 0))  # off from 8:59 to 10:00
    events = run(Engine(conn, clock), clock, at(10, 10))
    assert [t for t, k in kinds(events) if k == "medication_reminder"] == ["10:01"]


def test_untrusted_clock_fires_no_doses_and_says_why_once(conn, clock, carmen_pills):
    clock.set_trusted(False)
    events = run(Engine(conn, clock), clock, at(12, 0))
    assert [k for _, k in kinds(events)] == ["clock_untrusted"]
    assert conn.execute("SELECT count(*) FROM doses").fetchone()[0] == 0


def test_no_retroactive_reminder_for_a_medicine_added_after_its_time(conn, clock, home):
    clock.set(at(10, 0))
    meds.add_medication(conn, clock, home["antonio"], "sintrom", ["09:00", "20:00"])
    events = run(Engine(conn, clock), clock, at(20, 1))
    assert [t for t, k in kinds(events) if k == "medication_reminder"] == ["20:00"]


def test_family_alert_only_after_missed_in_a_row_and_with_consent(conn, clock, carmen_pills, home):
    e = Engine(conn, clock)
    events = run(e, clock, at(23, 59))
    assert "family_alert_missed_meds" not in [k for _, k in kinds(events)]  # no consent
    store.set_consent(conn, clock, home["carmen"], "family_missed_meds", True)
    clock.set(at(8, 0, day=10))
    events = run(e, clock, at(12, 0, day=10))
    assert [k for _, k in kinds(events)].count("family_alert_missed_meds") == 1


def test_appointment_day_before_and_same_day(conn, clock, home):
    store.add_appointment(conn, clock, home["carmen"], "médico", at(11, day=10), "centro de salud")
    events = run(Engine(conn, clock), clock, at(12, 0, day=10))
    assert kinds(events) == [("20:00", "appointment_day_before"), ("10:00", "appointment_same_day")]


def test_appointment_moved_reminds_again_for_new_time(conn, clock, home):
    aid = store.add_appointment(conn, clock, None, "fontanero", at(11, day=10))
    e = Engine(conn, clock)
    run(e, clock, at(20, 30))
    store.move_appointment(conn, clock, aid, at(12, day=10))
    events = run(e, clock, at(12, 0, day=10))
    assert kinds(events) == [("11:00", "appointment_same_day")]


def test_appointment_made_this_evening_is_not_re_announced(conn, clock, home):
    clock.set(at(21, 0))
    store.add_appointment(conn, clock, home["carmen"], "médico", at(11, day=10))
    events = run(Engine(conn, clock), clock, at(11, 0, day=10))
    assert kinds(events) == [("10:00", "appointment_same_day")]


def test_birthday_on_the_day_and_leap_day(conn, clock, home):
    store.add_contact(conn, clock, "Lucía", "nieta", "11-10")
    store.add_contact(conn, clock, "Pablo", "nieto", "02-29")
    events = run(Engine(conn, clock), clock, at(12, 0, day=10))
    assert [(t, e.data["name"]) for t, e in events] == [("10:00", "Lucía")]
    clock.set(at(9, 0, day=28, month=2).replace(year=2027))
    events = run(Engine(conn, clock), clock, at(11, 0, day=28, month=2).replace(year=2027))
    assert [e.data["name"] for _, e in events] == ["Pablo"]


def test_weekly_voice_reminder(conn, clock, home):
    store.add_reminder(conn, clock, None, "sacar la basura", "20:00", "weekly:THU", date(2026, 11, 9))
    events = run(Engine(conn, clock), clock, at(23, 0, day=19))
    assert [(e.data["text"], t) for t, e in events] == [("sacar la basura", "20:00")] * 2
    days = [e for _, e in events]
    assert len(days) == 2  # Thursdays 12 and 19


def test_water_nudges_only_with_consent_and_presence(conn, clock, home):
    e = Engine(conn, clock)
    e.on_presence("moving")
    assert run(e, clock, at(19, 0)) == []
    store.set_consent(conn, clock, home["carmen"], "water_nudges", True)
    clock.set(at(8, 0, day=10))
    events = run(e, clock, at(19, 0, day=10))
    assert [t for t, _ in events] == ["11:00", "13:00", "16:00", "18:00"]
    assert all(ev.person_ids == [home["carmen"]] and ev.category == 2 for _, ev in events)


def test_morning_greeting_once_with_the_days_plan(conn, clock, carmen_pills):
    store.add_appointment(conn, clock, None, "visita de Rosa", at(17, day=9))
    e = Engine(conn, clock)
    clock.set(at(5, 30))
    assert e.on_presence("moving") == []  # too early
    clock.set(at(8, 30))
    [g] = e.on_presence("moving")
    assert g.kind == "morning_greeting"
    assert g.data["shared_appointments"] == [{"title": "visita de Rosa", "time": "17:00"}]
    assert e.on_presence("still") == [] and e.on_presence("moving") == []


def test_movement_nudge_after_two_hours_still(conn, clock, home):
    store.set_consent(conn, clock, home["carmen"], "movement_nudges", True)
    e = Engine(conn, clock)
    clock.set(at(13, 30))
    e.on_presence("still")
    events = run(e, clock, at(16, 0))
    assert kinds(events) == [("15:30", "movement_nudge")]


def test_reminders_work_with_no_network(conn, clock, carmen_pills, monkeypatch):
    import socket

    def no_network(*a, **k):
        raise OSError("network is down")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    events = run(Engine(conn, clock), clock, at(9, 1))
    assert kinds(events) == [("09:00", "medication_reminder")]


# --- review findings ----------------------------------------------------------


def test_one_missed_morning_with_two_pills_is_not_two_in_a_row(conn, clock, carmen_pills, home):
    store.set_consent(conn, clock, home["carmen"], "family_missed_meds", True)
    e = Engine(conn, clock)
    events = run(e, clock, at(12, 0))  # 09:00 tensión + vitamina D both missed: one dose time
    assert "family_alert_missed_meds" not in [k for _, k in kinds(events)]
    events = run(e, clock, at(23, 59))  # 21:30 missed too: now two dose times in a row
    assert [k for _, k in kinds(events)].count("family_alert_missed_meds") == 1


def test_no_good_morning_in_the_evening(conn, clock, carmen_pills):
    e = Engine(conn, clock)
    clock.set(at(21, 0))
    assert e.on_presence("moving") == []


def test_no_tomorrow_reminder_late_at_night(conn, clock, home):
    store.add_appointment(conn, clock, home["carmen"], "médico", at(11, day=10))
    clock.set(at(23, 50))  # device back on late
    events = run(Engine(conn, clock), clock, at(23, 59))
    assert events == []


def test_voice_reminder_just_before_midnight_survives_a_short_outage(conn, clock, home):
    store.add_reminder(conn, clock, None, "cerrar la puerta", "23:55", "daily", date(2026, 11, 9))
    clock.set(at(0, 30, day=10))  # off from 23:50 to 00:30
    events = run(Engine(conn, clock), clock, at(0, 31, day=10))
    assert [e.data["text"] for _, e in events] == ["cerrar la puerta"]


def test_greeting_in_a_two_person_home_keeps_personal_things_private(conn, clock, carmen_pills):
    e = Engine(conn, clock)
    clock.set(at(8, 30))
    [g] = e.on_presence("moving")
    assert "people" not in g.data  # nobody known yet: only shared things


def test_greeting_in_a_one_person_home_has_the_plan(conn, clock):
    pid = meds.add_person(conn, clock, "Carmen")
    meds.add_medication(conn, clock, pid, "tensión", ["09:00"])
    e = Engine(conn, clock)
    clock.set(at(8, 30))
    [g] = e.on_presence("moving")
    assert g.data["people"]["Carmen"]["medicine_times"] == ["09:00"]
