"""Clocks. Everything time-dependent asks a Clock, never datetime.now().

A clock also says whether it can be trusted. After a power cut without the RTC
battery, the time can be wrong; then no dose is fired or logged (BUILD-PLAN 0b.8).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

HOME_TZ = ZoneInfo("Europe/Madrid")

# A time before this can't be real: the clock was reset.
EARLIEST_PLAUSIBLE = datetime(2026, 1, 1, tzinfo=HOME_TZ)


class Clock(Protocol):
    tz: ZoneInfo

    def now(self) -> datetime: ...

    def trusted(self) -> bool: ...


class SystemClock:
    """The real clock. On the Pi, `synced` is fed by NTP/RTC status."""

    def __init__(self, tz: ZoneInfo = HOME_TZ, synced: bool = True):
        self.tz = tz
        self.synced = synced

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def trusted(self) -> bool:
        return self.synced and self.now() >= EARLIEST_PLAUSIBLE


class FakeClock:
    """A clock tests and the simulator move by hand."""

    def __init__(self, start: datetime, tz: ZoneInfo = HOME_TZ, trusted: bool = True):
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz)
        self.tz = tz
        self._now = start.astimezone(tz)
        self._trusted = trusted

    def now(self) -> datetime:
        return self._now

    def trusted(self) -> bool:
        return self._trusted and self._now >= EARLIEST_PLAUSIBLE

    def set(self, when: datetime) -> None:
        if when.tzinfo is None:
            when = when.replace(tzinfo=self.tz)
        self._now = when.astimezone(self.tz)

    def advance(self, **kwargs) -> datetime:
        # Advance in real (UTC) time, so DST changes behave like a wall clock.
        self._now = (self._now.astimezone(ZoneInfo("UTC")) + timedelta(**kwargs)).astimezone(self.tz)
        return self._now

    def set_trusted(self, trusted: bool) -> None:
        self._trusted = trusted


def local_at(day, hhmm: str, tz: ZoneInfo = HOME_TZ) -> datetime:
    """The wall-clock time `hhmm` on `day` in the home's time zone.

    A time that doesn't exist (spring-forward gap) moves forward an hour,
    so a dose at 02:30 on that night still happens, at 03:30.
    """
    h, m = (int(x) for x in hhmm.split(":"))
    naive = datetime(day.year, day.month, day.day, h, m)
    aware = naive.replace(tzinfo=tz)
    roundtrip = aware.astimezone(ZoneInfo("UTC")).astimezone(tz)
    if roundtrip.replace(tzinfo=None) != naive:
        aware = (naive + timedelta(hours=1)).replace(tzinfo=tz)
    return aware
