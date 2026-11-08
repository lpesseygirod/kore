"""Ways to reach Claude. Each has create(request: dict) -> response dict.

- LiveClient: the real API. Checks the daily test budget before every call and
  logs the cost of every call (data/costs.jsonl). Needs the `live` dependency
  group and ANTHROPIC_API_KEY (read by the SDK; never by this code).
- RecordingClient: wraps another client and keeps every (request, response)
  pair, to save as a recording.
- ReplayClient: plays a saved recording back, free and offline. If the code now
  sends a different request (a prompt or tool changed), it stops with
  StaleRecording instead of pretending: the recording must be made again.
- ScriptedClient: a fake for tests, answering from a function.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from kore.brain.costs import CostLog


class StaleRecording(Exception):
    pass


class BudgetCap(Exception):
    pass


def request_hash(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class LiveClient:
    def __init__(self, purpose: str, cost_log: CostLog | None = None, run_cap_usd: float | None = None,
                 daily_cap_usd: float | None = None):
        import anthropic  # only for live runs (dependency group "live")

        # A call that hangs must not stall a run (or, later, a conversation): give up after a minute.
        self.sdk = anthropic.Anthropic(timeout=60.0, max_retries=2)
        self.purpose = purpose
        self.costs = cost_log or CostLog()
        self.run_cap_usd = run_cap_usd
        self.daily_cap_usd = daily_cap_usd  # this purpose's own share of the day (the web chat can't eat the rest)
        self.spent = 0.0

    def warm(self, model: str) -> bool:
        """Open the connection before the first turn (the first call otherwise waits for it): a token count,
        which is free. False if it couldn't (no internet): the first turn will just be slower."""
        try:
            self.sdk.messages.count_tokens(model=model, messages=[{"role": "user", "content": "hola"}])
            return True
        except Exception:
            return False

    def create(self, request: dict) -> dict:
        self.costs.check_budget()  # the daily limit, across everything
        if self.daily_cap_usd is not None and self.costs.today_usd(self.purpose) >= self.daily_cap_usd:
            raise BudgetCap(f"today's {self.purpose} spend reached its cap of ${self.daily_cap_usd:.2f}")
        if self.run_cap_usd is not None and self.spent >= self.run_cap_usd:
            raise BudgetCap(f"this run reached its cap of ${self.run_cap_usd:.2f}")
        resp = self.sdk.messages.create(**request)
        out = resp.model_dump(mode="json", exclude_none=True)
        self.spent += self.costs.record(request["model"], out.get("usage", {}), self.purpose)
        return {k: out[k] for k in ("content", "stop_reason", "usage", "model") if k in out}


class RecordingClient:
    def __init__(self, inner):
        self.inner = inner
        self.pairs: list[dict] = []

    def create(self, request: dict) -> dict:
        resp = self.inner.create(request)
        self.pairs.append({"request_hash": request_hash(request), "response": resp})
        return resp

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.pairs, ensure_ascii=False, indent=1), encoding="utf-8")


class ReplayClient:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.pairs = json.loads(self.path.read_text(encoding="utf-8"))
        self.i = 0

    def create(self, request: dict) -> dict:
        if self.i >= len(self.pairs):
            raise StaleRecording(f"{self.path.name}: more calls than recorded")
        pair = self.pairs[self.i]
        if pair["request_hash"] != request_hash(request):
            raise StaleRecording(f"{self.path.name}: call {self.i + 1} differs from the recording "
                                 "(prompt, tools or scenario changed): record again")
        self.i += 1
        return pair["response"]


class ScriptedClient:
    """For tests: `answer(request) -> response dict`. Keeps the requests it saw."""

    def __init__(self, answer: Callable[[dict], dict]):
        self.answer = answer
        self.requests: list[dict] = []

    def create(self, request: dict) -> dict:
        self.requests.append(request)
        return self.answer(request)


def text(t: str) -> dict:
    """A plain text answer, for scripted clients."""
    return {"content": [{"type": "text", "text": t}], "stop_reason": "end_turn", "usage": {}}


def tool_use(name: str, args: dict, id_: str = "t1", say: str | None = None) -> dict:
    """An answer that calls one tool, for scripted clients."""
    content = ([{"type": "text", "text": say}] if say else []) + [
        {"type": "tool_use", "id": id_, "name": name, "input": args}]
    return {"content": content, "stop_reason": "tool_use", "usage": {}}
