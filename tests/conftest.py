from datetime import datetime

import pytest

from kore.memory import meds
from kore.memory.db import connect
from kore.scheduler.clock import HOME_TZ, FakeClock

# A Monday.
START = datetime(2026, 11, 9, 8, 0, tzinfo=HOME_TZ)


@pytest.fixture
def clock():
    return FakeClock(START)


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "kore.db")


@pytest.fixture
def home(conn, clock):
    """Two people, like the trial home. Fictional names."""
    carmen = meds.add_person(conn, clock, "Carmen")
    antonio = meds.add_person(conn, clock, "Antonio", "Toni")
    return {"carmen": carmen, "antonio": antonio}


def at(hh, mm=0, day=9, month=11):
    return datetime(2026, month, day, hh, mm, tzinfo=HOME_TZ)
