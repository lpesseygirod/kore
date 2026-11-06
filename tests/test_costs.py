import json
from types import SimpleNamespace

import pytest

from kore.brain.costs import BudgetExceeded, CostLog, cost_usd


def test_cost_counts_cache_reads_and_1h_writes():
    usage = {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000,
             "cache_creation_input_tokens": 0}
    # 100*2 + 50*10 + 1000*0.2 = 900 per million
    assert cost_usd("claude-sonnet-5-5", usage) == pytest.approx(900 / 1e6)
    write = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 1000}
    assert cost_usd("claude-opus-5-5", write) == pytest.approx(1000 * 4 * 2 / 1e6)


def test_budget_guard_stops_live_runs(tmp_path):
    log = CostLog(tmp_path / "costs.jsonl")
    assert log.check_budget(limit=0.01) == pytest.approx(0.01)
    big = {"input_tokens": 0, "output_tokens": 1000, "cache_read_input_tokens": 0}
    log.record("claude-opus-5-5", big, "test")  # $0.02
    with pytest.raises(BudgetExceeded):
        log.check_budget(limit=0.01)
    entry = json.loads((tmp_path / "costs.jsonl").read_text())
    assert entry["purpose"] == "test" and "key" not in json.dumps(entry).lower()


class FakeStream:
    def __init__(self, text, model):
        self.text_stream = iter([text[:4], text[4:]])
        self.model = model

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        usage = SimpleNamespace(input_tokens=20, output_tokens=15, cache_read_input_tokens=800,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(usage=usage, stop_reason="end_turn", model=self.model)


class FakeClient:
    def __init__(self):
        self.calls = []
        self.messages = SimpleNamespace(stream=self._stream)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **req):
        self.calls.append(req)
        return FakeStream("Buenos días, Carmen. ¿Qué tal?", req["model"])


def test_experiment_loop_runs_end_to_end_without_network(tmp_path, monkeypatch):
    from experiments import personality_chat as pc

    monkeypatch.setattr(pc, "OUT", tmp_path)
    client = FakeClient()
    path = pc.run_model(client, CostLog(tmp_path / "c.jsonl"), "claude-opus-5-5", pc.SCRIPT[:3])
    text = path.read_text()
    assert text.count("**Pepe:**") == 3 and "## Summary" in text
    first = client.calls[0]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert first["fallbacks"] == "default"
    assert len(client.calls[2]["messages"]) == 5  # history grows: u, a, u, a, u
