"""gemini-4-argon adapter path for the refreshed confirmatory arm.

cohort_refresh_ruling.md rev. 2 section 5: thinking_level "MEDIUM" (the
documented default), sent explicitly, never a thinking budget; thinking counts
against max_output_tokens, so calibration runs at the non-streaming provider
limit, 32,768, and reasoning_allowance_limit_ruling.md decides the cap after.
Section 6: a refusal (here a safety finish or a blocked prompt, Operations'
mapping, flagged for Research) is its own class and is never retried. Hub
2026-11-21: no temperature to a refreshed arm. gemini-3.1-pro-preview (the
Phase A snapshot) keeps its request unchanged (tests/test_adapter_request_
invariance.py proves the bytes). No provider is contacted.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from google import genai

from src.adapters import gemini_adapter as ga
from src.adapters import get_adapter
from src.analytics.cost_rates import compute_call_cost_usd

DECISION = json.dumps({"decisions": [], "overall_reasoning": "ok"})


def _response(model, text=DECISION, finish="STOP", thoughts=3000, block=None):
    usage = SimpleNamespace(prompt_token_count=1000, candidates_token_count=100,
                            thoughts_token_count=thoughts, cached_content_token_count=None)
    cand = SimpleNamespace(finish_reason=SimpleNamespace(name=finish), finish_message=None)
    return SimpleNamespace(text=text, model_version=model, usage_metadata=usage, candidates=[cand],
                           prompt_feedback=SimpleNamespace(block_reason=block) if block else None)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    def install(reply=lambda model, config: _response(model)):
        calls: list[dict] = []

        class _Models:
            def generate_content(self, model, contents, config):
                calls.append({"model": model, "contents": contents, "config": config})
                return reply(model, config)
        monkeypatch.setattr(genai, "Client", lambda **k: SimpleNamespace(models=_Models()))
        return calls
    return install


def test_level_and_cap_are_as_ruled():
    assert ga.REFRESHED_ARMS == {"gemini-4-argon": {"thinking_level": "MEDIUM"}}
    assert ga.REFRESHED_MAX_OUTPUT_TOKENS == 32768


def test_argon_request(fake):
    calls = fake()
    r = get_adapter("google", "gemini-4-argon").generate_decision("SYS", "USER")
    assert r.success and r.model_id_returned == "gemini-4-argon"
    cfg = calls[0]["config"]
    assert cfg.max_output_tokens == 32768 and cfg.system_instruction == "SYS"
    assert str(getattr(cfg.thinking_config.thinking_level, "value",
                       cfg.thinking_config.thinking_level)).upper().endswith("MEDIUM")
    assert cfg.thinking_config.thinking_budget is None          # never a budget
    assert cfg.temperature is None and cfg.response_mime_type == "application/json"
    md = r.metadata
    assert md["thoughts_tokens"] == 3000 and md["reasoning_effort_sent"] == "MEDIUM"
    assert md["max_tokens_sent"] == 32768 and md["transport"] == "non-streaming"
    # Thought tokens bill as output and are priced (Nov rate $2/$10).
    assert md["cost_usd"] is not None


def test_snapshot_request_is_unchanged(fake):
    calls = fake()
    r = get_adapter("google", "gemini-3.1-pro-preview").generate_decision("S", "U")
    assert r.success
    cfg = calls[0]["config"]
    assert cfg.thinking_config is None and cfg.max_output_tokens == 16384
    assert "reasoning_effort_sent" not in r.metadata


def test_no_temperature_to_argon(fake):
    calls = fake()
    get_adapter("google", "gemini-4-argon", temperature=0.0).generate_decision("S", "U")
    assert calls[0]["config"].temperature is None
    calls = fake()
    get_adapter("google", "gemini-3.1-pro-preview", temperature=0.0).generate_decision("S", "U")
    assert calls[0]["config"].temperature == 0.0


@pytest.mark.parametrize("finish,block", [("SAFETY", None), ("PROHIBITED_CONTENT", None),
                                          ("STOP", SimpleNamespace(name="SAFETY"))])
def test_a_refusal_is_its_own_class_and_is_not_retried(fake, finish, block):
    calls = fake(lambda m, c: _response(m, text="", finish=finish, block=block))
    r = get_adapter("google", "gemini-4-argon").generate_decision("S", "U")
    assert r.success is False and r.error_class == "REFUSAL" and len(calls) == 1


def test_a_budget_stop_is_not_a_refusal(fake, monkeypatch):
    monkeypatch.setattr("src.adapters.base._RETRY_DELAY_SECONDS", 0)
    fake(lambda m, c: _response(m, text='{"decisions": [', finish="MAX_TOKENS"))
    r = get_adapter("google", "gemini-4-argon").generate_decision("S", "U")
    assert r.error_class != "REFUSAL" and r.metadata["finish_reason"] == "MAX_TOKENS"


def test_rates_move_on_december_1():
    assert compute_call_cost_usd("gemini-4-argon", 1_000_000, 1_000_000,
                                 on_date="2026-11-30") == pytest.approx(12.0)
    assert compute_call_cost_usd("gemini-4-argon", 1_000_000, 1_000_000,
                                 on_date="2026-12-01") == pytest.approx(24.0)


# ---- hub H-OPS-40: absent is not zero, in calibration and production
def test_reported_thoughts_keeps_zero_and_absent_apart():
    from src.adapters.gemini_adapter import reported_thoughts
    assert reported_thoughts(SimpleNamespace(thoughts_token_count=0)) == 0
    assert reported_thoughts(SimpleNamespace(thoughts_token_count=None)) is None
    assert reported_thoughts(SimpleNamespace()) is None
    assert reported_thoughts(None) is None
    assert reported_thoughts(SimpleNamespace(thoughts_token_count=742)) == 742


@pytest.mark.parametrize("thoughts,expected", [(None, None), (0, 0), (812, 812)])
def test_argon_logs_the_reported_thoughts(fake, thoughts, expected):
    fake(lambda model, config: _response(model, thoughts=thoughts))
    md = get_adapter("google", "gemini-4-argon").generate_decision("SYS", "USER").metadata
    assert md["thoughts_tokens"] == expected
    assert md["cost_usd"] is not None                  # the cost line still prices 0 for absent


@pytest.mark.parametrize("thoughts,expected", [(None, None), (0, 0)])
def test_the_snapshot_logs_the_reported_thoughts_too(fake, thoughts, expected):
    fake(lambda model, config: _response(model, thoughts=thoughts))
    md = get_adapter("google", "gemini-3.1-pro").generate_decision("SYS", "USER").metadata
    assert md.get("thoughts_tokens", "MISSING") == expected

