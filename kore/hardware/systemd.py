"""Telling systemd "I'm up" and "I'm still alive" (sd_notify), with nothing but the standard library.

Outside systemd (the Mac, tests) NOTIFY_SOCKET isn't set and every call does nothing.
To switch the watchdog on for kore-home: call `ready()` once the loop runs and `alive()` on every tick, then set
`Type=notify` and `WatchdogSec=60` in deploy/pi/kore-home.service (the lines are there, commented). If the loop
then hangs for a minute, systemd kills and restarts it.
"""

from __future__ import annotations

import os
import socket


def notify(message: str, environ: dict | None = None) -> bool:
    env = os.environ if environ is None else environ
    path = env.get("NOTIFY_SOCKET")
    if not path:
        return False
    if path.startswith("@"):  # an abstract socket
        path = "\0" + path[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(path)
            s.sendall(message.encode())
        return True
    except OSError:
        return False


def ready(environ: dict | None = None) -> bool:
    return notify("READY=1", environ)


def alive(environ: dict | None = None) -> bool:
    return notify("WATCHDOG=1", environ)


def status(text: str, environ: dict | None = None) -> bool:
    """A one-line status shown by `systemctl status` (never put anything personal in it)."""
    return notify("STATUS=" + text.replace("\n", " ")[:200], environ)
