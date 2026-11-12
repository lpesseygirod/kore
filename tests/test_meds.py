import sqlite3
from datetime import timedelta

import pytest

from kore.memory import meds
from tests.conftest import at


@pytest.fixture
def pills(conn, clock, home):
    c = home["carmen"]
    return {
        "tension": meds.add_medication(conn, clock, c, "tensión", ["09:00", "21:30"], "white, round"),
        "vitamin": meds.add_medication(conn, clock, c, "vitamina D", ["09:00"]),
        "lunch": meds.add_medication(conn, clock, c, "omeprazol", ["14:00"]),
        "toni": meds.add_medication(conn, clock, home["antonio"], "sintrom", ["20:00"]),
    }


def test_took_my_morning_pills_marks_all_nine_oclock_doses(conn, clock, home, pills):
    clock.set(at(9, 10))
    r = meds.mark_taken(conn, clock, home["carmen"])
    assert sorted(d["medication"] for d in r["marked"]) == ["tensión", "vitamina D"]
    assert r["already_taken"] == []
    assert "Never suggest" in r["rules"]


def test_a_dose_can_never_be_logged_twice(conn, clock, home, pills):
    clock.set(at(9, 10))
    meds.mark_taken(conn, clock, home["carmen"])
    clock.set(at(9, 40))
    r = meds.mark_taken(conn, clock, home["carmen"])
    assert r["marked"] == []
    assert {d["taken_at"] for d in r["already_taken"]} == {"09:10"}
    assert conn.execute("SELECT count(*) FROM doses").fetchone()[0] == 2


def test_database_itself_rejects_a_duplicate_dose(conn, clock, home, pills):
    clock.set(at(9, 10))
    meds.mark_taken(conn, clock, home["carmen"], pills["vitamin"])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO doses (medication_id, person_id, slot_utc, status, logged_at_utc) "
            "SELECT medication_id, person_id, slot_utc, 'taken', logged_at_utc FROM doses"
        )


def test_early_window_counts_for_the_coming_dose(conn, clock, home, pills):
    clock.set(at(8, 15))
    r = meds.mark_taken(conn, clock, home["carmen"])
    assert {d["slot"] for d in r["marked"]} == {"09:00"}


def test_too_early_is_not_guessed(conn, clock, home, pills):
    clock.set(at(7, 0))  # before the 9:00 window opens; yesterday's dose is too long ago
    r = meds.mark_taken(conn, clock, home["carmen"], pills["vitamin"])
    assert r["error"] == "no_dose_due"


def test_adjacent_times_are_ambiguous_not_guessed(conn, clock, home):
    # Bug: at 08:05 "ya me la he tomado" marked the 08:30 pill, not the 08:00 one.
    meds.add_medication(conn, clock, home["antonio"], "Eutirox", ["08:00"])
    enalapril = meds.add_medication(conn, clock, home["antonio"], "Enalapril", ["08:30"])
    clock.set(at(8, 5))
    r = meds.mark_taken(conn, clock, home["antonio"])
    assert r["error"] == "ambiguous"
    assert {o["medication"] for o in r["options"]} == {"Eutirox", "Enalapril"}
    assert conn.execute("SELECT count(*) FROM doses").fetchone()[0] == 0
    assert meds.mark_taken(conn, clock, home["antonio"], enalapril)["marked"]


def test_early_pill_today_does_not_rewrite_yesterdays_missed_dose(conn, clock, home):
    # Bug: at 06:30 "I took it" turned yesterday's missed 08:00 into "taken".
    mid = meds.add_medication(conn, clock, home["carmen"], "x", ["08:00"])
    med = meds.medications(conn, home["carmen"])[0]
    meds.close_slot(conn, clock, med, at(8, 0), reminded=True)
    clock.set(at(6, 30, day=10))
    r = meds.mark_taken(conn, clock, home["carmen"], mid)
    assert r["error"] == "no_dose_due"
    assert meds.dose_row(conn, mid, at(8, 0))["status"] == "missed"


def test_ambiguous_medicine_name_is_not_guessed(conn, clock, home):
    meds.add_medication(conn, clock, home["carmen"], "Sintrom", ["20:00"], "la pastilla rosa")
    meds.add_medication(conn, clock, home["carmen"], "Adiro", ["09:00"], "la pastilla blanca")
    assert len(meds.find_medications(conn, home["carmen"], "pastilla")) == 2
    assert [m["name"] for m in meds.find_medications(conn, home["carmen"], "la rosa")] == ["Sintrom"]
    assert [m["name"] for m in meds.find_medications(conn, home["carmen"], "la verde")] == []
    assert [m["name"] for m in meds.find_medications(conn, home["carmen"], "pastilla rosa")] == ["Sintrom"]
    assert [m["name"] for m in meds.find_medications(conn, home["carmen"], "sintrom")] == ["Sintrom"]


def test_taken_late_is_flagged_and_never_suggests_doubling(conn, clock, home, pills):
    clock.set(at(12, 30))
    r = meds.mark_taken(conn, clock, home["carmen"], pills["vitamin"])
    assert r["marked"][0]["late"] is True
    assert "doubling" in r["rules"]


def test_each_person_has_their_own_doses(conn, clock, home, pills):
    clock.set(at(20, 5))
    r = meds.mark_taken(conn, clock, home["antonio"])
    assert [d["medication"] for d in r["marked"]] == ["sintrom"]
    s = meds.status(conn, clock, home["carmen"])
    assert all(d["medication"] != "sintrom" for d in s["doses"])


def test_status_says_only_what_is_recorded(conn, clock, home, pills):
    clock.set(at(9, 10))
    meds.mark_taken(conn, clock, home["carmen"], pills["tension"])
    clock.set(at(15, 0))
    doses = {(d["medication"], d["time"]): d["state"] for d in meds.status(conn, clock, home["carmen"])["doses"]}
    assert doses[("tensión", "09:00")] == "taken"
    assert doses[("vitamina D", "09:00")] == "not_recorded"
    assert doses[("omeprazol", "14:00")] == "not_recorded_yet"
    assert doses[("tensión", "21:30")] == "upcoming"


def test_untrusted_clock_refuses_to_log_or_read(conn, clock, home, pills):
    clock.set(at(9, 10))
    clock.set_trusted(False)
    with pytest.raises(meds.ClockUntrusted):
        meds.mark_taken(conn, clock, home["carmen"])
    with pytest.raises(meds.ClockUntrusted):
        meds.status(conn, clock, home["carmen"])
    assert conn.execute("SELECT count(*) FROM doses").fetchone()[0] == 0


def test_clock_reset_to_the_past_is_untrusted(conn, clock, home, pills):
    from datetime import datetime

    from kore.scheduler.clock import HOME_TZ

    clock.set(datetime(1970, 1, 1, 9, 10, tzinfo=HOME_TZ))
    assert not clock.trusted()


def test_missed_dose_can_still_be_marked_taken_later(conn, clock, home, pills):
    clock.set(at(9, 0))
    med = meds.medications(conn, home["carmen"])[1]
    meds.close_slot(conn, clock, med, at(9, 0), reminded=True)
    clock.set(at(11, 0))
    r = meds.mark_taken(conn, clock, home["carmen"], med["id"])
    assert r["marked"] and r["marked"][0]["late"] is False
    assert meds.dose_row(conn, med["id"], at(9, 0))["status"] == "taken"


def test_bad_times_are_rejected(conn, clock, home):
    with pytest.raises(ValueError):
        meds.add_medication(conn, clock, home["carmen"], "x", ["25:00"])
    with pytest.raises(ValueError):
        meds.add_medication(conn, clock, home["carmen"], "x", [])


def test_dst_change_keeps_wall_clock_times(conn, clock, home):
    # Spain falls back on Sunday 25 October 2026 at 03:00 → 02:00.
    m = meds.add_medication(conn, clock, home["carmen"], "x", ["09:00"])
    med = meds.medications(conn, home["carmen"])[0]
    assert med["id"] == m
    before = meds.slots_on(med, at(9, day=24, month=10).date(), clock)[0]
    after = meds.slots_on(med, at(9, day=26, month=10).date(), clock)[0]
    assert before.hour == after.hour == 9
    assert meds.elapsed(before, after) == timedelta(days=2, hours=1)  # an extra real hour in between


def test_time_in_the_spring_gap_still_happens(conn, clock, home):
    from datetime import date

    from kore.scheduler.clock import local_at

    t = local_at(date(2026, 3, 29), "02:30")  # doesn't exist in Madrid
    assert (t.hour, t.minute) == (3, 30)
