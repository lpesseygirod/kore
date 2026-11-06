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

from kore.brain import fallback, tools
from kore.brain.prompt import SYSTEM_PROMPT, state_block
from kore.feedback import store as feedback
from kore.manners import modes
from kore.manners.gate import Item

MODEL = "claude-sonnet-5-5"  # Lea approved 2026-11-06 (experiment 4)
# Thinking off (between_tools is Sonnet 5.5's lowest setting), low effort: spoken replies.
MODEL_CONFIG = {"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}
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

    # --- the two ways a turn starts ---------------------------------------------

    def hear(self, text: str) -> str:
        """The person said `text`. Returns what Pepe says."""
        self.ctx.session.begin_turn(text)
        return self._turn([{"type": "text", "text": f"They say: {text}"}])

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
            spoken += [b["text"] for b in resp["content"] if b.get("type") == "text"]
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
        when = f"{fallback.spoken_date(now.date())}, {now.strftime('%H:%M')}"
        forms = {names[p.id]: self.forms.get(p.name, "usted") for p in s.household}
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
            else:
                text = " ".join(b["text"] for b in m["content"] if b.get("type") == "text").strip()
                if text:
                    out.append(("Pepe", text))
        return out
