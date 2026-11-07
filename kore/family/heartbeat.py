"""The heartbeat: "I'm alive" every few minutes to a free dead man's switch (Healthchecks.io).

If the pings stop (crash, power cut, no internet), the service alerts Lea, not the
family (BUILD-PLAN 3, "silent failure"). Set the check to period 5 minutes, grace
30 minutes: an alert arrives at most 35 minutes after Pepe stops.

The ping carries nothing about the home. The URL is a secret (anyone with it could
ping): it lives in .env as KORE_HEARTBEAT_URL and is never logged or printed.
"""

from __future__ import annotations

import os
import time
from datetime import timedelta

from kore.family.channels import _request
from kore.scheduler.clock import Clock

URL_VAR = "KORE_HEARTBEAT_URL"
EVERY = timedelta(minutes=5)


class Heartbeat:
    def __init__(self, url: str | None, clock: Clock, every: timedelta = EVERY, request=_request,
                 monotonic=time.monotonic):
        self._url = url
        self._clock = clock  # kept for symmetry; the interval uses a monotonic timer (a clock jump can't stop pings)
        self._every = every.total_seconds()
        self._request = request
        self._monotonic = monotonic
        self._last: float | None = None
        self.last_ok: bool | None = None

    def __repr__(self) -> str:
        return "Heartbeat()"

    @classmethod
    def from_env(cls, clock: Clock, env: dict | None = None) -> "Heartbeat":
        env = os.environ if env is None else env
        return cls((env.get(URL_VAR) or "").strip() or None, clock)

    @property
    def configured(self) -> bool:
        return bool(self._url)

    def tick(self, healthy: bool = True) -> bool:
        """Ping if one is due. healthy=False reports a failed self-test (Lea is alerted at once)."""
        if not self._url:
            return False
        now = self._monotonic()
        if healthy and self._last is not None and now - self._last < self._every:
            return False
        status, _ = self._request(self._url + ("" if healthy else "/fail"), None, {}, 10)
        self.last_ok = 200 <= status < 300
        if self.last_ok:
            self._last = now
        return self.last_ok
