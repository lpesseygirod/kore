from datetime import date

import pytest

from kore.memory import store
from kore.memory.db import connect
from tests.conftest import at


def test_shopping_trip_keeps_what_wasnt_found(conn, clock):
    store.add_to_list(conn, clock, ["leche"])
    assert store.add_to_list(conn, clock, ["pan", "huevos", "Pan"]) == ["leche", "pan", "huevos"]
    assert store.back_from_shopping(conn, not_found=["Huevos"]) == ["huevos"]


def test_remove_from_list(conn, clock):
    store.add_to_list(conn, clock, ["leche", "pan"])
    assert store.remove_from_list(conn, ["leche"]) == ["pan"]


def test_where_are_my_keys(conn, clock, home):
    store.remember_place(conn, clock, home["carmen"], "llaves de repuesto", "el cajón azul")
    store.remember_place(conn, clock, home["carmen"], "llaves de repuesto", "la entrada")
    found = store.where_is(conn, "llaves")
    assert [f["place"] for f in found] == ["la entrada"]


def test_forget_that_really_deletes(tmp_path, clock, home):
    path = tmp_path / "forget.db"
    conn = connect(path)
    from kore.memory import meds

    pid = meds.add_person(conn, clock, "Carmen")
    secret = "mi hermana Rosario tiene deudas"
    nid = store.add_note(conn, clock, pid, "person", secret)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # the note is in the main file now
    assert secret.encode() in path.read_bytes()
    assert store.forget_note(conn, pid, nid)
    assert store.notes(conn, pid) == []
    conn.close()
    raw = path.read_bytes()
    wal = path.with_name(path.name + "-wal")
    if wal.exists():
        raw += wal.read_bytes()
    assert secret.encode() not in raw


def test_forget_only_your_own_notes(conn, clock, home):
    nid = store.add_note(conn, clock, home["carmen"], "interest", "le gusta la zarzuela")
    assert not store.forget_note(conn, home["antonio"], nid)
    assert len(store.notes(conn, home["carmen"])) == 1


def test_consent_defaults_to_no(conn, clock, home):
    assert not store.has_consent(conn, home["carmen"], "emergency_path")
    store.set_consent(conn, clock, home["carmen"], "emergency_path", True)
    assert store.has_consent(conn, home["carmen"], "emergency_path")
    with pytest.raises(ValueError):
        store.set_consent(conn, clock, home["carmen"], "made_up", True)


def test_appointments_are_personal_or_shared(conn, clock, home):
    store.add_appointment(conn, clock, home["carmen"], "médico", at(11, day=12))
    store.add_appointment(conn, clock, None, "comida familiar", at(14, day=15))
    store.add_appointment(conn, clock, home["antonio"], "dentista", at(10, day=13))
    titles = [a["title"] for a in store.appointments(conn, clock, home["carmen"])]
    assert titles == ["médico", "comida familiar"]


@pytest.mark.parametrize(
    "rule,anchor,day,expected",
    [
        ("weekly:THU", date(2026, 11, 9), date(2026, 11, 12), True),
        ("weekly:THU", date(2026, 11, 9), date(2026, 11, 13), False),
        ("every:3", date(2026, 11, 9), date(2026, 11, 15), True),
        ("every:3", date(2026, 11, 9), date(2026, 11, 14), False),
        ("once", date(2026, 11, 9), date(2026, 11, 9), True),
        ("once", date(2026, 11, 9), date(2026, 11, 10), False),
        ("daily", date(2026, 11, 9), date(2026, 11, 8), False),
    ],
)
def test_recurring_rules(rule, anchor, day, expected):
    assert store.reminder_occurs_on(rule, anchor, day) is expected


def test_bad_rule_rejected(conn, clock):
    with pytest.raises(ValueError):
        store.add_reminder(conn, clock, None, "x", "10:00", "weekly:FUNDAY", date(2026, 11, 9))


def test_birthday_validation(conn, clock):
    store.add_contact(conn, clock, "Lucía", "nieta", "2-29")
    with pytest.raises(ValueError):
        store.add_contact(conn, clock, "X", "", "02-30")
