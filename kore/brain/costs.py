"""What each live API call costs, and the daily test budget (at most 5 euros a day).

Every live call is appended to data/costs.jsonl (git-ignored). Before a live
run, check_budget() refuses to start if today's spend has reached the limit.
The limit is counted in US dollars, which is slightly stricter than euros.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

DAILY_LIMIT_USD = 5.0
DEFAULT_LOG = Path(__file__).resolve().parents[2] / "data" / "costs.jsonl"

# USD per million tokens. Cache writes use the 1-hour TTL (2x input).
PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01},  # prompts up to 100K tokens
}
CACHE_WRITE_1H = 2.0


class BudgetExceeded(Exception):
    pass


def cost_usd(model: str, usage) -> float:
    """Cost of one response from its `usage` (an SDK object or a dict)."""
    get = usage.get if isinstance(usage, dict) else lambda k, d=0: getattr(usage, k, d)
    p = PRICES[model]
    tokens = {
        "input": get("input_tokens", 0) or 0,
        "output": get("output_tokens", 0) or 0,
        "cache_read": get("cache_read_input_tokens", 0) or 0,
        "cache_write": get("cache_creation_input_tokens", 0) or 0,
    }
    return (
        tokens["input"] * p["input"]
        + tokens["output"] * p["output"]
        + tokens["cache_read"] * p["cache_read"]
        + tokens["cache_write"] * p["input"] * CACHE_WRITE_1H
    ) / 1_000_000


class CostLog:
    def __init__(self, path: Path = DEFAULT_LOG):
        self.path = Path(path)

    def record(self, model: str, usage, purpose: str, **extra) -> float:
        usd = cost_usd(model, usage)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": model,
                 "purpose": purpose, "usd": round(usd, 6), **extra}
        with self.path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        return usd

    def today_usd(self, purpose: str | None = None) -> float:
        if not self.path.exists():
            return 0.0
        today = datetime.now(timezone.utc).date().isoformat()
        total = 0.0
        for line in self.path.read_text().splitlines():
            entry = json.loads(line)
            if entry["at"].startswith(today) and (purpose is None or entry.get("purpose") == purpose):
                total += entry["usd"]
        return total

    def check_budget(self, limit: float = DAILY_LIMIT_USD) -> float:
        spent = self.today_usd()
        if spent >= limit:
            raise BudgetExceeded(f"today's live API spend is ${spent:.2f}, limit ${limit:.2f}")
        return limit - spent
