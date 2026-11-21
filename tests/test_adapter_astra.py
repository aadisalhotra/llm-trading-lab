"""gpt-6-astra adapter path for the refreshed confirmatory arm.

cohort_refresh_ruling.md rev. 2 section 5: no default is documented, but the
API reports "medium" when the level is omitted (probe 2026-11-21), so it is
sent explicitly (hub, pending Research's confirmation); the calibration cap is
the provider maximum, 128,000; non-streaming. Section 6: a refusal is its own
class and is never retried. Hub 2026-11-21: no temperature to a refreshed arm.
The Phase A snapshot gpt-5.4-2026-03-05 keeps its request unchanged
(tests/test_adapter_request_invariance.py proves the bytes).

No provider is contacted: openai.OpenAI is replaced by a recorder.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import openai
import pytest

from src.adapters import get_adapter
from src.adapters import openai_adapter as oa
from src.analytics.cost_rates import compute_call_cost_usd

DECISION = json.dumps({"decisions": [], "overall_reasoning": "ok"})


def _completion(model, content=DECISION, refusal=None, finish="stop", reasoning=900):
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=reasoning + 100,
                            completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
                            prompt_tokens_details=SimpleNamespace(cached_tokens=0))
    msg = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(model=model, choices=[SimpleNamespace(message=msg, finish_reason=finish)],
                           usage=usage, system_fingerprint="fp", service_tier="default")


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def install(reply=lambda kw: _completion(kw["model"])):
        calls: list[dict] = []

        class _Completions:
            def create(self, **kw):
                calls.append(kw)
                return reply(kw)
        client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
        monkeypatch.setattr(openai, "OpenAI", lambda **k: client)
        return calls
    return install


def test_the_level_and_cap_are_as_ruled():
    assert oa.REFRESHED_ARMS == {"gpt-6-astra": {"reasoning_effort": "medium"}}
    assert oa.REFRESHED_MAX_COMPLETION_TOKENS == 128_000


def test_astra_request(fake):
    calls = fake()
    r = get_adapter("openai", "gpt-6-astra").generate_decision("SYS", "USER", images=[b"\x89PNG"])
    assert r.success and r.model_id_returned == "gpt-6-astra"
    (kw,) = calls
    assert kw["model"] == "gpt-6-astra" and kw["reasoning_effort"] == "medium"
    assert kw["max_completion_tokens"] == 128_000
    assert kw["response_format"] == {"type": "json_object"}
    assert set(kw) == {"model", "messages", "response_format", "max_completion_tokens",
                       "reasoning_effort"}
    assert kw["messages"][0] == {"role": "system", "content": "SYS"}
    md = r.metadata
    assert md["thoughts_tokens"] == 900 and md["reasoning_effort_sent"] == "medium"
    assert md["max_tokens_sent"] == 128_000 and md["transport"] == "non-streaming"


def test_snapshot_request_is_unchanged(fake):
    calls = fake()
    r = get_adapter("openai", "gpt-5.4-2026-03-05").generate_decision("S", "U")
    assert r.success
    (kw,) = calls
    assert "reasoning_effort" not in kw and kw["max_completion_tokens"] == 16384
    assert "reasoning_effort_sent" not in r.metadata


def test_no_temperature_to_astra(fake):
    calls = fake()
    get_adapter("openai", "gpt-6-astra", temperature=0.0).generate_decision("S", "U")
    assert "temperature" not in calls[0]
    calls = fake()
    get_adapter("openai", "gpt-5.4-2026-03-05", temperature=0.0).generate_decision("S", "U")
    assert calls[0]["temperature"] == 0.0


def test_a_refusal_is_its_own_class_and_is_not_retried(fake):
    calls = fake(lambda kw: _completion(kw["model"], content=None, refusal="I can't help with that."))
    r = get_adapter("openai", "gpt-6-astra").generate_decision("S", "U")
    assert r.success is False and r.error_class == "REFUSAL" and len(calls) == 1
    assert "I can't help" in r.error


def test_a_snapshot_refusal_keeps_the_old_handling(fake, monkeypatch):
    monkeypatch.setattr("src.adapters.base._RETRY_DELAY_SECONDS", 0)
    calls = fake(lambda kw: _completion(kw["model"], content=None, refusal="no"))
    r = get_adapter("openai", "gpt-5.4-2026-03-05").generate_decision("S", "U")
    assert r.error_class == "PARSE_FAILURE" and len(calls) == 2


def test_budget_stop_is_reported(fake, monkeypatch):
    monkeypatch.setattr("src.adapters.base._RETRY_DELAY_SECONDS", 0)
    fake(lambda kw: _completion(kw["model"], content='{"decisions": [', finish="length"))
    r = get_adapter("openai", "gpt-6-astra").generate_decision("S", "U")
    assert r.metadata["finish_reason"] == "length"


def test_rate():
    assert compute_call_cost_usd("gpt-6-astra", 1_000_000, 1_000_000) == pytest.approx(60.0)
