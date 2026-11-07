"""One network fetch at a time, in a background thread, so a voice turn never waits long.

The thread only fetches; the caller (the main loop, which owns the database) collects the
result and stores it. A turn waits at most a few seconds and otherwise answers from the cache;
a slow server, a slow DNS lookup or a feed sent one byte at a time can't hold a turn.
"""

from __future__ import annotations

import threading
from typing import Callable

IN_TURN_WAIT_S = 3.0


class Fetcher:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._result: object = None
        self._done = threading.Event()

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def start(self, fn: Callable[[], object]) -> None:
        if self._thread is not None:
            return
        self._done.clear()
        self._result = None

        def run():
            try:
                self._result = fn()
            except Exception:
                self._result = None
            self._done.set()

        self._thread = threading.Thread(target=run, name="kore-info", daemon=True)  # never holds up shutdown
        self._thread.start()

    def collect(self, wait: float = 0.0) -> tuple[bool, object]:
        """(finished, result). Waits up to `wait` seconds for a running fetch."""
        if self._thread is None:
            return False, None
        if not self._done.wait(wait):
            return False, None
        self._thread = None
        return True, self._result
