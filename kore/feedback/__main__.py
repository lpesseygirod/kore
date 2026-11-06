"""Lea's controls for Pepe's feedback, from the command line.

  uv run python -m kore.feedback list
  uv run python -m kore.feedback page            # writes data/feedback.html
  uv run python -m kore.feedback undo ID
  uv run python -m kore.feedback misunderstood ID ["note"]
  uv run python -m kore.feedback reword ID "new rule text"
  uv run python -m kore.feedback summary         # the last 7 days, as the weekly message

--db PATH picks the database (default data/kore.db). Undoing works from Pepe's next turn.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

from kore.feedback import page, store
from kore.memory.db import connect
from kore.scheduler.clock import SystemClock


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kore.feedback")
    ap.add_argument("--db", default="data/kore.db")
    ap.add_argument("command", choices=["list", "page", "undo", "misunderstood", "reword", "summary"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    if not Path(a.db).exists():
        print(f"No database at {a.db}", file=sys.stderr)
        return 1
    conn, clock = connect(a.db), SystemClock()
    try:
        if a.command == "list":
            for r in page.items(conn):
                flag = "!" if r["review_first"] else " "
                print(f"{flag} #{r['id']:<4} {r['speaker'] or '?':10} “{r['said']}” → {page.change_text(r)} "
                      f"[{page.STATUS_WORDS[r['status']]}]")
        elif a.command == "page":
            out = Path(a.db).parent / "feedback.html"
            out.write_text(page.render_html(conn, clock), encoding="utf-8")
            print(out)
        elif a.command == "summary":
            print(page.weekly_summary(conn, clock, clock.now() - timedelta(days=7)))
        elif a.command == "undo":
            store.undo(conn, clock, int(a.args[0]))
        elif a.command == "misunderstood":
            store.undo(conn, clock, int(a.args[0]), misunderstood=True, note=a.args[1] if len(a.args) > 1 else None)
        elif a.command == "reword":
            store.reword(conn, int(a.args[0]), a.args[1])
    except (store.FeedbackError, IndexError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
