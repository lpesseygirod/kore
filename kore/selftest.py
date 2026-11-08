"""The box checks itself: after an update (and before), at install, or whenever Lea wants to know.

  python -m kore.selftest --db /var/lib/kore/kore.db            on the box: kore-selftest does this for you
  uv run --env-file .env python -m kore.selftest --db data/kore.db --skip audio      on the Mac

Checks: the database opens, passes SQLite's integrity check, has its key tables, and a copy of it opens and
updates with this version's code (the live file is only read); the clock can be trusted; there is free disk space;
the speakerphone is there (only on Linux); the needed keys are set (names only, never values); the web page
answers on its port if it's running (or must be, with --require-web).

Prints one line per check. Exit code 0 when nothing failed, 1 otherwise. A warning doesn't fail.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kore.family.backup import CERT_VAR
from kore.family.channels import TELEGRAM_TOKEN_VAR, TWILIO_FROM_VAR, TWILIO_SID_VAR, TWILIO_TOKEN_VAR
from kore.family.heartbeat import URL_VAR as HEARTBEAT_VAR

# Pepe can't talk without these.
REQUIRED_ENV = ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY")
# Help and the family links need these; missing ones are a warning (the setup may not have them yet).
OPTIONAL_ENV = (TELEGRAM_TOKEN_VAR, TWILIO_SID_VAR, TWILIO_TOKEN_VAR, TWILIO_FROM_VAR, HEARTBEAT_VAR, CERT_VAR)
# Tables whose loss would mean lost doses or lost setup.
KEY_TABLES = ("people", "medications", "doses", "reminders", "contacts", "settings", "event_log")
CHECKS = ("database", "clock", "disk", "audio", "env", "web")
MIN_FREE_MB = 1024


@dataclass
class Result:
    name: str
    status: str   # ok | warn | fail | skip
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "fail"


Runner = Callable[[list[str]], tuple[int, str]]


def run_command(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return p.returncode, p.stdout


# --- the database -----------------------------------------------------------------------------------------------

def check_database(db: Path) -> Result:
    from kore.memory.db import SCHEMA_VERSION, connect

    if not db.is_file():
        return Result("database", "fail", f"no database at {db}")
    try:
        live = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)  # only read
    except sqlite3.Error as e:
        return Result("database", "fail", f"doesn't open: {e}")
    notes = []
    try:
        bad = [r[0] for r in live.execute("PRAGMA integrity_check").fetchall()]
        if bad != ["ok"]:
            return Result("database", "fail", f"integrity check: {'; '.join(bad[:3])}")
        tables = {r[0] for r in live.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = [t for t in KEY_TABLES if t not in tables]
        if missing:
            return Result("database", "fail", f"tables missing: {', '.join(missing)}")
        counts = {t: live.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("people", "medications",
                                                                                    "doses")}
        version = live.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            notes.append(f"written by a newer version ({version} > {SCHEMA_VERSION})")
        if live.execute("PRAGMA foreign_key_check").fetchone() is not None:
            notes.append("some rows point at missing rows")
        # This version's code must open (and if needed update) the real data: try it on a copy.
        with tempfile.TemporaryDirectory(prefix="kore-selftest-") as tmp:
            copy = sqlite3.connect(Path(tmp) / "copy.db")
            with copy:
                live.backup(copy)
            copy.close()
            trial = connect(Path(tmp) / "copy.db")
            ok = trial.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            trial.close()
            if not ok:
                return Result("database", "fail", "a copy doesn't survive this version's update")
    except sqlite3.Error as e:
        return Result("database", "fail", f"error: {e}")
    finally:
        live.close()
    detail = (f"ok, {counts['people']} people, {counts['medications']} medicines, {counts['doses']} doses logged, "
              f"schema {version}")
    if notes:
        return Result("database", "warn", detail + "; " + "; ".join(notes))
    return Result("database", "ok", detail)


# --- the clock --------------------------------------------------------------------------------------------------

def clock_synced(run: Runner = run_command, platform: str = sys.platform,
                 rtc: Path = Path("/sys/class/rtc/rtc0")) -> tuple[bool, str]:
    """(synced, how). On the Pi: internet time (NTP), else the RTC battery's clock."""
    if not platform.startswith("linux"):
        return True, "not the box: the computer's own clock"
    code, out = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    if code == 0 and out.strip() == "yes":
        return True, "set from the internet"
    if rtc.exists():
        return True, "from the RTC battery (no internet time yet)"
    return False, "no internet time and no RTC battery"


def check_clock(synced: tuple[bool, str] | None = None, clock_factory=None) -> Result:
    from kore.scheduler.clock import SystemClock

    ok, how = synced if synced is not None else clock_synced()
    clock = (clock_factory or (lambda s: SystemClock(synced=s)))(ok)
    now = clock.now().strftime("%Y-%m-%d %H:%M %Z")
    if not clock.trusted():
        why = how if not ok else "the time is before 2026: the clock was reset"
        return Result("clock", "fail", f"not trusted ({why}); it says {now}. No dose is fired or logged.")
    status = "warn" if "RTC" in how else "ok"
    return Result("clock", status, f"{now}, {how}")


# --- disk, audio, keys, web -------------------------------------------------------------------------------------

def check_disk(path: Path, min_free_mb: int = MIN_FREE_MB, usage=shutil.disk_usage) -> Result:
    where = path if path.exists() else path.parent
    while not where.exists() and where != where.parent:
        where = where.parent
    free_mb = usage(where).free // (1024 * 1024)
    if free_mb < min_free_mb:
        return Result("disk", "fail", f"only {free_mb} MB free at {where} (needs {min_free_mb})")
    return Result("disk", "ok", f"{free_mb} MB free")


def check_audio(run: Runner = run_command, platform: str = sys.platform, match: str | None = "USB") -> Result:
    """match: the card's line must contain it. "USB", so the Pi's own HDMI sound doesn't count as a speaker."""
    if not platform.startswith("linux"):
        return Result("audio", "skip", "not the box")
    found = {}
    for kind, tool in (("speaker", "aplay"), ("microphone", "arecord")):
        code, out = run([tool, "-l"])
        if code == 127:
            return Result("audio", "fail", f"{tool} isn't installed (apt install alsa-utils)")
        cards = [ln for ln in out.splitlines() if ln.startswith("card ")]
        if match:
            cards = [ln for ln in cards if match.lower() in ln.lower()]
        if not cards:
            what = f"no {kind} matching '{match}'" if match else f"no {kind} found"
            return Result("audio", "fail", f"{what}: is the speakerphone plugged in?")
        found[kind] = cards[0].split(":", 1)[1].strip()[:60]
    return Result("audio", "ok", f"speaker: {found['speaker']}; microphone: {found['microphone']}")


def check_env(environ: dict | None = None, required=REQUIRED_ENV, optional=OPTIONAL_ENV) -> Result:
    """Names only. A value is never printed, logged or even measured beyond 'empty or not'."""
    env = os.environ if environ is None else environ
    missing = [n for n in required if not (env.get(n) or "").strip()]
    missing_opt = [n for n in optional if not (env.get(n) or "").strip()]
    if missing:
        return Result("env", "fail", "missing: " + ", ".join(missing))
    if missing_opt:
        return Result("env", "warn", "set: the needed keys; not set: " + ", ".join(missing_opt))
    return Result("env", "ok", "every key is set")


def check_web(port: int = 8080, require: bool = False, opener=urllib.request.urlopen) -> Result:
    url = f"http://127.0.0.1:{port}/"
    try:
        with opener(urllib.request.Request(url, headers={"Host": "localhost"}), timeout=5) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except (urllib.error.URLError, OSError) as e:
        if require:
            return Result("web", "fail", f"no answer on port {port} ({getattr(e, 'reason', e)})")
        return Result("web", "skip", f"not running on port {port}")
    if code >= 500:
        return Result("web", "fail", f"the page answers with an error ({code})")
    return Result("web", "ok", f"answers on port {port} ({code})")


# --- all together -----------------------------------------------------------------------------------------------

def run_checks(db: Path, skip=(), min_free_mb: int = MIN_FREE_MB, web_port: int = 8080, require_web: bool = False,
               audio_match: str | None = "USB") -> list[Result]:
    steps = {
        "database": lambda: check_database(db),
        "clock": lambda: check_clock(),
        "disk": lambda: check_disk(db, min_free_mb),
        "audio": lambda: check_audio(match=audio_match),
        "env": lambda: check_env(),
        "web": lambda: check_web(web_port, require_web),
    }
    out = []
    for name, step in steps.items():
        if name in skip:
            out.append(Result(name, "skip", "skipped"))
            continue
        try:
            out.append(step())
        except Exception as e:  # a check that crashes is a failed check, never a crashed self-test
            out.append(Result(name, "fail", f"the check itself broke: {type(e).__name__}: {e}"))
    return out


def report(results: list[Result]) -> str:
    lines = [f"  [{r.status:4}] {r.name:9} {r.detail}" for r in results]
    failed = [r.name for r in results if r.failed]
    head = "KORE self-test: FAILED (" + ", ".join(failed) + ")" if failed else "KORE self-test: OK"
    return "\n".join([head, *lines])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kore.selftest", description="The box checks itself.")
    ap.add_argument("--db", default="data/kore.db", type=Path)
    ap.add_argument("--skip", action="append", default=[], choices=CHECKS, help="a check to leave out")
    ap.add_argument("--min-free-mb", type=int, default=MIN_FREE_MB)
    ap.add_argument("--web-port", type=int, default=8080)
    ap.add_argument("--require-web", action="store_true", help="fail if the web page isn't answering")
    ap.add_argument("--audio-match", default="USB", help="the speakerphone's line in `aplay -l` must contain this")
    a = ap.parse_args(argv)
    results = run_checks(a.db, set(a.skip), a.min_free_mb, a.web_port, a.require_web, a.audio_match)
    print(report(results), flush=True)
    return 1 if any(r.failed for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
