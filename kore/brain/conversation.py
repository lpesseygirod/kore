"""The brain of the conversation loop: one turn = the person's words (or an event
Pepe has to say) → Claude with the tools → what Pepe says.

- Facts come from code. Each turn carries a state block built by code (time,
  who lives here and who is speaking, how to address them, quiet or visitors,
  the lines from their feedback), marked as state, separate from their words.
- The history is append-only: Sonnet 5.5 ties its thinking blocks to the
  conversation, so nothing earlier is edited; the system prompt never changes
  within a conversation.
- Tools run through kore.brain.tools.call, so every rule in code applies
  (confirmations, known speaker, outside text).
- The client is anything with create(request) -> response dict (kore.brain.clients):
  live, recording, replaying saved answers, or a fake for tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from kore.brain import fallback, tools
from kore.brain.language import help_requested, is_affirmative, looks_english
from kore.family import messages as family_msgs
from kore.brain.prompt import SYSTEM_PROMPT, state_block
from kore.feedback import store as feedback
from kore.memory import settings
from kore.setup import progress as setup
from kore.manners import modes
from kore.manners.gate import Item

MODEL = "claude-sonnet-5-5"  # Lea approved 2026-11-06 (experiment 4)
# Adaptive thinking at low effort: Claude can think privately (in thinking blocks the
# voice never sees) and skips it on simple turns. With thinking off ("between_tools"),
# the pilot showed it sometimes writing its notes as text, which the voice would read
# out. Speed to be measured in phase 1 (experiment 4 timed "between_tools").
MODEL_CONFIG = {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}
MAX_TOKENS = 1024
MAX_TOOL_ROUNDS = 6
CACHE = {"type": "ephemeral", "ttl": "1h"}

MANNER_NOTES = {
    "say": "Say it now, naturally and briefly.",
    "short": "Say it now, very short and soft: just their name and what it is.",
    "signal": "Visitors are here: say only their name and that there's a reminder for when they can. "
              "Nothing about what it is.",
}


@dataclass
class ToolCall:
    turn: int
    name: str
    args: dict
    result: dict


@dataclass
class Brain:
    ctx: tools.Context
    client: object
    forms: dict[str, str] = field(default_factory=dict)  # name -> "tú" | "usted"
    model: str = MODEL
    messages: list[dict] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    responses: list[dict] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # text kept from the voice (notes in English), for Lea
    # The voice loop sets this to speak code's own lines (help) at once, before Claude answers.
    say_now: Callable[[str], None] | None = None

    # --- the ways a turn starts ---------------------------------------------------

    def hear(self, text: str) -> str:
        """The person said `text`. Returns what Pepe says."""
        s = self.ctx.session
        s.begin_turn(text)
        said, note = self._help_first(text)
        content = [{"type": "text", "text": f"They say: {text}"}]
        if note:
            content.append({"type": "text", "text": note})
        return self._with(said, self._turn(content))

    def button(self) -> str:
        """The help button was pressed. Code acts at once; Claude then keeps them company."""
        s = self.ctx.session
        s.begin_turn("")
        pending = [p for p in s.pending.values() if p.status == "pending" and p.kind == "send_help"]
        if pending:  # pressing the button answers "¿Aviso a Lucía?"
            said = tools.run_by_code(self.ctx, pending[-1])["say"]
            note = (f"The help button was pressed after Pepe asked whether to tell someone; the help path (code) "
                    f"ran. Pepe already said: «{said}». Don't repeat it. Stay calm and with them.")
            return self._with(said, self._turn([{"type": "text", "text": note}]))
        out = self.ctx.link().call_for_help(s, "button")
        note = (f"The help button was pressed. Help path (code): {out.summary()}. Pepe already said: «{out.say}». "
                "Don't repeat it. Stay calm and with them; ask simply how they are.")
        return self._with(out.say, self._turn([{"type": "text", "text": note}]))

    def _help_first(self, text: str) -> tuple[str, str]:
        """'Pepe, ayuda', a fall, or the yes to "¿Aviso a Lucía?": done by code before Claude sees the turn."""
        s = self.ctx.session
        pending = [p for p in s.pending.values() if p.status == "pending" and p.kind == "send_help"]
        kind = help_requested(text, [a for p in s.household for a in p.aliases])
        if pending and (is_affirmative(text) or kind):  # "¡ayuda!" answers "¿Aviso a Lucía?" too
            said = tools.run_by_code(self.ctx, pending[-1])["say"]
            return said, (f"They said yes, and the help path (code) ran. Pepe already said: «{said}». "
                          "Don't repeat it. Stay calm and with them.")
        if kind is None:
            return "", ""
        out = self.ctx.link().call_for_help(s, kind, text)
        return out.say, (f"Help path (code), after their words: {out.summary()}. Pepe already said: «{out.say}». "
                         "Don't repeat it or send anything else. "
                         + ("Wait for their answer to that question. " if out.asked else "")
                         + "Stay calm and with them; ask simply how they are.")

    def _with(self, said: str, reply: str) -> str:
        if not said:
            return reply
        if self.say_now is not None:
            self.say_now(said)
            return reply
        return f"{said} {reply}".strip()

    def speak_event(self, item: Item) -> str | None:
        """Pepe speaks first: word an event from the scheduler, in the manner the gate chose."""
        if item.manner == "family":
            return None  # a message (phase 6), never spoken
        ev = item.event
        note = (f"Event from the scheduler (code, not the person): {ev.kind}. Facts: "
                f"{json.dumps(ev.data, ensure_ascii=False)}. {MANNER_NOTES[item.manner]}")
        return self._turn([{"type": "text", "text": note}])

    # --- the loop ------------------------------------------------------------------

    def _turn(self, content: list[dict]) -> str:
        self.messages.append({"role": "user", "content": [{"type": "text", "text": self.state()}, *content]})
        spoken: list[str] = []
        for _ in range(MAX_TOOL_ROUNDS):
            resp = self.client.create(self.request())
            self.responses.append(resp)
            self.messages.append({"role": "assistant", "content": resp["content"]})
            spoken += [self._speakable(b["text"]) for b in resp["content"] if b.get("type") == "text"]
            uses = [b for b in resp["content"] if b.get("type") == "tool_use"]
            if resp.get("stop_reason") != "tool_use" or not uses:
                break
            results = []
            for u in uses:
                result = tools.call(self.ctx, u["name"], u.get("input") or {})
                self.calls.append(ToolCall(self.ctx.session.turn, u["name"], u.get("input") or {}, result))
                results.append({"type": "tool_result", "tool_use_id": u["id"],
                                "content": json.dumps(result, ensure_ascii=False, default=str)})
            self.messages.append({"role": "user", "content": results})
        else:
            spoken.append(fallback.CONNECTION_PROBLEM)  # too many tool rounds: say something true
        return " ".join(s.strip() for s in spoken if s.strip())

    def _speakable(self, text: str) -> str:
        kept, dropped = speakable(text)
        self.dropped += dropped
        return kept

    def request(self) -> dict:
        return {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": CACHE}],
            "tools": self.tool_schemas(),
            "cache_control": CACHE,  # automatic caching of the growing conversation
            "messages": list(self.messages),
            **MODEL_CONFIG,
        }

    def tool_schemas(self) -> list[dict]:
        return tools.schemas()

    # --- the state block, from code --------------------------------------------------

    def state(self) -> str:
        ctx, s = self.ctx, self.ctx.session
        now = ctx.clock.now()
        names = {p.id: p.call_name for p in s.household}
        lines = ["Lives here: " + ", ".join(
            p.name if p.name == p.call_name else f"{p.name} (called {p.call_name})" for p in s.household) + "."]
        if s.speaker_id is not None:
            lines.append(f"Speaking: {names[s.speaker_id]}.")
        elif len(s.household) > 1:
            lines.append("Speaking: not known yet. Anything personal needs to know who it is.")
        if modes.active(ctx.conn, ctx.clock, "visitors"):
            lines.append("Visitors are here: nothing private out loud.")
        quiet = modes.active(ctx.conn, ctx.clock, "quiet")
        if quiet:
            lines.append("They asked for quiet: answer only what they ask, very briefly.")
        people = {s.speaker_id: names[s.speaker_id]} if s.speaker_id is not None else names
        lines += feedback.prompt_lines(ctx.conn, ctx.clock, people)
        unread = family_msgs.unread(ctx.conn, s.speaker_id)
        if unread and not modes.active(ctx.conn, ctx.clock, "visitors"):
            senders = sorted({r["name"] for r in unread})
            lines.append(f"Unread messages from family ({', '.join(senders)}): mention it once if it fits; "
                         "read them with read_messages only when they ask.")
        when = f"{fallback.spoken_date(now.date())}, {now.strftime('%H:%M')}"
        forms = {names[p.id]: _form(settings.stored(ctx.conn, "address", p.id) or self.forms.get(p.name, "usted"))
                 for p in s.household}
        if setup.status(ctx.conn) in ("not_started", "in_progress"):  # paused: wait for the next day's offer
            nxt = setup.next_step(ctx.conn, s.speaker_id, [p.id for p in s.household])
            if nxt.step is not None or nxt.need_speaker:
                lines.append("Setup isn't finished: when it fits (not in the middle of something else), "
                             "call setup_next to continue it, one question at a time.")
        return "<state>\n" + state_block(when, "\n".join(lines), forms) + "\n</state>"

    def transcript(self) -> list[tuple[str, str]]:
        """(who, text) pairs for reports and the judge: the person's words, events, and Pepe."""
        out = []
        for m in self.messages:
            if m["role"] == "user":
                for b in m["content"]:
                    if b.get("type") == "text" and b["text"].startswith("They say: "):
                        out.append(("Persona", b["text"][len("They say: "):]))
                    elif b.get("type") == "text" and b["text"].startswith("Event from the scheduler"):
                        out.append(("Evento", b["text"]))
                    elif b.get("type") == "text" and "Pepe already said: «" in b["text"]:
                        if b["text"].startswith("The help button"):
                            out.append(("Persona", "[pulsa el botón de ayuda]"))
                        said = b["text"].split("Pepe already said: «", 1)[1].split("»", 1)[0]
                        out.append(("Pepe", said))  # code's own line (help)
            else:
                text = " ".join(speakable(b["text"])[0] for b in m["content"] if b.get("type") == "text").strip()
                if text:
                    out.append(("Pepe", text))
        return out


def speakable(text: str) -> tuple[str, list[str]]:
    """Safety net: a line that is clearly English is Claude's note, not speech. (kept, dropped)."""
    keep, dropped = [], []
    for line in text.split("\n"):
        (dropped if looks_english(line) else keep).append(line)
    return "\n".join(keep).strip(), dropped


def _form(value: str) -> str:
    return {"tu": "tú"}.get(value, value)
