"""grok-4.7 adapter path for the refreshed confirmatory arm.

cohort_refresh_ruling.md rev. 2 section 5: the documented default "high" sent
explicitly; reasoning does not count against max_tokens on grok-4.7 (probe
2026-11-21), so max_tokens stays the registered answer budget, 16,384.
Section 6: a refusal is its own class and is never retried. Hub 2026-11-21:
no temperature to a refreshed arm. grok-4.20-0309-reasoning (the Phase A
snapshot) keeps its request unchanged (tests/test_adapter_request_invariance.py
proves the bytes). No provider is contacted: requests.post is replaced.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import requests

from src.adapters import get_adapter
from src.adapters import xai_adapter as xa
from src.analytics.cost_rates import compute_call_cost_usd

DECISION = json.dumps({"decisions": [], "overall_reasoning": "ok"})


def _body(model, content=DECISION, refusal=None, reasoning=5000, finish="stop"):
    return {"model": model,
            "choices": [{"message": {"content": content, "refusal": refusal}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100,
                      "total_tokens": 1100 + reasoning,
                      "completion_tokens_details": {"reasoning_tokens": reasoning},
                      "prompt_tokens_details": {"cached_tokens": 0},
                      "cost_in_usd_ticks": 3_220_000_000}}


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key")

    def install(reply=lambda body: _body(body["model"])):
        calls: list[dict] = []

        def post(url, json=None, headers=None, timeout=None):
            calls.append(json)
            data = reply(json)
            return SimpleNamespace(ok=True, status_code=200, json=lambda: data, text="")
        monkeypatch.setattr(requests, "post", post)
        return calls
    return install


def test_level_and_cap_are_as_ruled():
    assert xa.REFRESHED_ARMS == {"grok-4.7": {"reasoning_effort": "high"}}
    assert xa.REFRESHED_MAX_TOKENS == 16384


def test_grok_4_7_request(fake):
    calls = fake()
    r = get_adapter("xai", "grok-4.7").generate_decision("SYS", "USER", images=[b"\x89PNG"])
    assert r.success and r.model_id_returned == "grok-4.7"
    (body,) = calls
    assert body["reasoning_effort"] == "high" and body["max_tokens"] == 16384
    assert set(body) == {"model", "messages", "max_tokens", "response_format", "reasoning_effort"}
    md = r.metadata
    assert md["thoughts_tokens"] == 5000 and md["reasoning_effort_sent"] == "high"
    assert md["transport"] == "non-streaming" and md["provider_cost_usd"] == pytest.approx(0.322)
    # Reasoning is reported outside completion_tokens and billed as output.
    assert md["cost_usd"] == pytest.approx((1000 * 2 + (100 + 5000) * 6) / 1e6)


def test_snapshot_request_is_unchanged(fake):
    calls = fake()
    r = get_adapter("xai", "grok-4.20-0309-reasoning").generate_decision("S", "U")
    assert r.success
    (body,) = calls
    assert "reasoning_effort" not in body and body["max_tokens"] == 16384
    assert "reasoning_effort_sent" not in r.metadata and "provider_cost_usd" not in r.metadata


def test_no_temperature_to_grok_4_7(fake):
    calls = fake()
    get_adapter("xai", "grok-4.7", temperature=0.0).generate_decision("S", "U")
    assert "temperature" not in calls[0]
    calls = fake()
    get_adapter("xai", "grok-4.20-0309-reasoning", temperature=0.0).generate_decision("S", "U")
    assert calls[0]["temperature"] == 0.0


def test_a_refusal_is_its_own_class_and_is_not_retried(fake):
    calls = fake(lambda b: _body(b["model"], content=None, refusal="declined"))
    r = get_adapter("xai", "grok-4.7").generate_decision("S", "U")
    assert r.success is False and r.error_class == "REFUSAL" and len(calls) == 1


def test_rate():
    assert compute_call_cost_usd("grok-4.7", 1_000_000, 1_000_000) == pytest.approx(8.0)
