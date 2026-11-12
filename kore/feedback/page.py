"""My feedback page and weekly summary (BUILD-PLAN 2, "My feedback page").

Every item: what was said, how Pepe understood it, what changed, whether it's
still active. Anything that touched medicine, alerts or night rules comes first.
Only the feedback sentence is shown, never the conversation around it.

The console shows the same items (kore/web/console.py). The static page in data/
(git-ignored) and the weekly summary come from kore/feedback/__main__.py; nothing
sends the summary automatically yet.
"""

from __future__ import annotations

import html
import sqlite3
from datetime import datetime

from kore.memory.db import from_utc_iso, utc_iso
from kore.scheduler.clock import Clock

STATUS_WORDS = {
    "active": "active", "undone": "undone", "replaced": "replaced by a newer one",
    "held_safety": "NOT applied: safety (for you to look at)", "not_applied": "not applied: about the other person",
    "noted": "noted, nothing to change",
}


def items(conn: sqlite3.Connection, since: datetime | None = None) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT f.*, p.call_name AS person, s.call_name AS speaker FROM feedback f "
        "LEFT JOIN people p ON p.id = f.person_id LEFT JOIN people s ON s.id = f.speaker_id "
        "WHERE f.at_utc >= ? ORDER BY f.review_first DESC, f.at_utc DESC, f.id DESC",
        (utc_iso(since) if since else "",),
    ).fetchall()


def change_text(r: sqlite3.Row) -> str:
    if r["status"] in ("held_safety", "not_applied"):
        asked = f": {r['setting_key']} {r['new_value'] or ''}".rstrip() if r["setting_key"] else ""
        return f"nothing changed{asked}" + (f" (rule asked: “{r['rule_text']}”)" if r["rule_text"] else "")
    if r["kind"] == "setting":
        when = {"now": " for half an hour", "today": " for today", "always": ""}[r["scope"]]
        return f"{r['setting_key']} → {r['new_value']}{when}"
    if r["kind"] == "rule":
        when = " (today only)" if r["scope"] == "today" else " (just now)" if r["scope"] == "now" else ""
        return f"rule: “{r['rule_text']}”{when}"
    return "—"


def render_html(conn: sqlite3.Connection, clock: Clock) -> str:
    rows = []
    for r in items(conn):
        when = from_utc_iso(r["at_utc"], clock.tz).strftime("%d/%m %H:%M")
        who = r["speaker"] or "?"
        if r["person"] and r["person"] != r["speaker"]:
            who += f" (about {r['person']})"
        follow = ""
        if r["follow_up_question"]:
            answer = r["follow_up_answer"] or ("no answer, dropped" if r["follow_up_state"] == "dropped" else "…")
            follow = f"<div class=fu>Asked: {_e(r['follow_up_question'])} — {_e(answer)}</div>"
        flags = " ⚠ review" if r["review_first"] else ""
        flags += " · misunderstood" if r["misunderstood"] else ""
        note = f"<div class=fu>Note: {_e(r['lea_note'])}</div>" if r["lea_note"] else ""
        rows.append(
            f"<tr class='{_e(r['status'])}{' review' if r['review_first'] else ''}'>"
            f"<td>#{r['id']}<br>{_e(when)}</td><td>{_e(who)}</td>"
            f"<td>“{_e(r['said'])}”<div class=fu>Understood: {_e(r['understood'])}</div>{follow}{note}</td>"
            f"<td>{_e(change_text(r))}</td><td>{_e(STATUS_WORDS[r['status']])}{_e(flags)}</td></tr>"
        )
    body = "\n".join(rows) or "<tr><td colspan=5>No feedback yet.</td></tr>"
    generated = clock.now().strftime("%d/%m/%Y %H:%M")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pepe feedback</title>
<style>
:root {{ --bg:#fbfaf7; --fg:#1f1d1a; --muted:#6b665e; --line:#e4e0d8; --warn:#fff3d6; --off:#9a958c; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#191816; --fg:#ece8e1; --muted:#a39e95; --line:#34312c; --warn:#3b3220; --off:#77736b; }}
}}
body {{ background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, sans-serif; margin:0; padding:24px 16px; }}
main {{ max-width:1000px; margin:0 auto; }}
h1 {{ font-size:22px; margin:0 0 4px; }} p {{ color:var(--muted); margin:0 0 16px; }}
table {{ width:100%; border-collapse:collapse; }}
th, td {{ text-align:left; vertical-align:top; padding:10px 8px; border-bottom:1px solid var(--line); }}
th {{ font-size:13px; color:var(--muted); font-weight:600; }}
tr.review {{ background:var(--warn); }}
tr.undone, tr.replaced {{ color:var(--off); }}
.fu {{ font-size:13px; color:var(--muted); margin-top:4px; }}
code {{ font-size:13px; }}
@media (max-width:640px) {{ th:nth-child(1), td:nth-child(1) {{ display:none; }} }}
</style></head><body><main>
<h1>Pepe feedback</h1>
<p>Generated {generated}. Highlighted rows touch medicine, alerts or night hours. To undo one:
<code>uv run python -m kore.feedback undo ID</code> (or <code>misunderstood ID</code>, <code>reword ID "text"</code>).</p>
<table><thead><tr><th>When</th><th>Who</th><th>What they said</th><th>Change</th><th>Status</th></tr></thead>
<tbody>
{body}
</tbody></table></main></body></html>
"""


def weekly_summary(conn: sqlite3.Connection, clock: Clock, since: datetime) -> str:
    """Plain text, for the weekly message (printed by the command line for now)."""
    rows = items(conn, since)
    if not rows:
        return "Pepe: no feedback this week."
    lines = [f"Pepe: {len(rows)} piece(s) of feedback this week."]
    for r in rows:
        mark = "⚠ " if r["review_first"] else ""
        lines.append(f"{mark}#{r['id']} {r['speaker'] or '?'}: “{r['said']}” → {change_text(r)} "
                     f"[{STATUS_WORDS[r['status']]}]")
    return "\n".join(lines)


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""))
