"""Killing the process mid-write must never lose a committed dose or corrupt the file.

A process kill is not a real power cut (the OS still flushes its cache);
the real test is pulling the plug on the Pi 10 times. This catches transaction bugs early.
"""

import signal
import subprocess
import sys
import textwrap
import time

from kore.memory.db import connect

WRITER = textwrap.dedent(
    """
    import sys
    from datetime import datetime, timedelta
    from kore.memory import meds
    from kore.memory.db import connect
    from kore.scheduler.clock import FakeClock, HOME_TZ

    clock = FakeClock(datetime(2026, 11, 9, 8, 0, tzinfo=HOME_TZ))
    conn = connect(sys.argv[1])
    pid = meds.add_person(conn, clock, "Carmen")
    mid = meds.add_medication(conn, clock, pid, "x", ["09:00"])
    print("ready", flush=True)
    day = 0
    while True:
        clock.set(datetime(2026, 11, 9, 9, 5, tzinfo=HOME_TZ) + timedelta(days=day))
        r = meds.mark_taken(conn, clock, pid)
        print(r["marked"][0]["day"], flush=True)
        day += 1
    """
)


def test_kill_mid_write_keeps_every_committed_dose(tmp_path):
    db = tmp_path / "crash.db"
    proc = subprocess.Popen([sys.executable, "-c", WRITER, str(db)], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "ready"
    time.sleep(0.5)
    proc.send_signal(signal.SIGKILL)
    out, _ = proc.communicate()
    committed = [line for line in out.split() if line]
    assert len(committed) > 5

    conn = connect(db)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    rows = conn.execute("SELECT count(*), count(DISTINCT slot_utc) FROM doses WHERE status = 'taken'").fetchone()
    assert rows[0] == rows[1]  # no double logs
    assert rows[0] >= len(committed)  # nothing reported as committed was lost
