"""Adapter tests — focused on the JSON parser. No network calls."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from src.adapters import base as adapter_base
from src.adapters.base import BaseAdapter


def test_parse_clean_json():
    raw = '{"overall_reasoning": "ok", "decisions": [{"action": "BUY", "ticker": "AAPL", "target_weight": 0.1, "confidence": 8, "reasoning": "earnings"}]}'
    out = BaseAdapter._parse_response(raw)
    assert len(out["decisions"]) == 1
    assert out["decisions"][0]["ticker"] == "AAPL"
    assert out["decisions"][0]["confidence"] == 8


def test_parse_strips_code_fence():
    raw = '```json\n{"overall_reasoning": "x", "decisions": []}\n```'
    out = BaseAdapter._parse_response(raw)
    assert out["decisions"] == []


def test_parse_with_preamble():
    raw = 'Sure, here are my decisions:\n{"overall_reasoning": "x", "decisions": [{"action": "HOLD", "ticker": "MSFT", "target_weight": 0, "confidence": 5, "reasoning": "wait"}]}'
    out = BaseAdapter._parse_response(raw)
    assert len(out["decisions"]) == 1
    assert out["decisions"][0]["action"] == "HOLD"


def test_parse_clamps_confidence_and_weight():
    raw = '{"overall_reasoning": "x", "decisions": [{"action": "BUY", "ticker": "NVDA", "target_weight": 5.0, "confidence": 99, "reasoning": "yolo"}]}'
    out = BaseAdapter._parse_response(raw)
    d = out["decisions"][0]
    assert d["target_weight"] == 1.0  # clamped to [0,1] at parse layer; risk layer enforces 0.20
    assert d["confidence"] == 10


def test_parse_rejects_empty():
    with pytest.raises(ValueError):
        BaseAdapter._parse_response("")


def test_parse_rejects_missing_decisions_field():
    with pytest.raises(ValueError):
        BaseAdapter._parse_response('{"overall_reasoning": "x"}')


# --- Retry loop in BaseAdapter.generate_decision ---------------------------
#
# Every provider adapter inherits a 2-attempt / 15s-cooldown retry whenever
# _parse_response raises ValueError (empty response, invalid JSON even after
# repair, or missing `decisions` list). HTTP/network errors from _call_api
# are NOT retried — those usually need human intervention and a second call
# risks a duplicate billing event. These tests pin that contract.


class _StubAdapter(BaseAdapter):
    """Drives generate_decision with a scripted sequence of _call_api
    results (str, or Exception instance to raise)."""
    provider_name = "stub"
    supports_vision = False

    def __init__(self, script: list[Any]):
        super().__init__("stub-model")
        self._script = list(script)
        self.call_count = 0

    def _call_api(self, system_prompt, user_prompt, images=None):
        self.call_count += 1
        result = self._script.pop(0)
        if isinstance(result, Exception):
            raise result
        return result, "stub-model-returned", {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0}


_VALID_JSON = '{"overall_reasoning": "ok", "decisions": [{"action": "HOLD", "ticker": "AAPL", "target_weight": 0, "confidence": 5, "reasoning": "wait"}]}'


@pytest.fixture
def no_retry_sleep(monkeypatch):
    """Neutralize time.sleep so retry tests don't actually wait 15s."""
    monkeypatch.setattr(adapter_base.time, "sleep", lambda *_a, **_kw: None)


def test_generate_decision_first_try_success_reports_attempt_1(no_retry_sleep):
    adapter = _StubAdapter([_VALID_JSON])
    result = adapter.generate_decision("sys", "user")
    assert result.success is True
    assert result.metadata["attempt"] == 1
    assert adapter.call_count == 1
    assert result.decisions[0]["ticker"] == "AAPL"


def test_generate_decision_retries_on_parse_failure_and_recovers(no_retry_sleep):
    adapter = _StubAdapter(["not json at all", _VALID_JSON])
    result = adapter.generate_decision("sys", "user")
    assert result.success is True
    assert result.metadata["attempt"] == 2
    assert adapter.call_count == 2


def test_generate_decision_gives_up_after_two_parse_failures(no_retry_sleep):
    adapter = _StubAdapter(["not json", "still not json"])
    result = adapter.generate_decision("sys", "user")
    assert result.success is False
    assert result.metadata["attempt"] == 2
    assert adapter.call_count == 2
    assert "JSON" in (result.error or "")


def test_generate_decision_retries_on_empty_response(no_retry_sleep):
    # Empty-string responses are the OTHER failure mode we've seen in prod
    # (DeepSeek, Gemini) — _parse_response raises ValueError("Empty response...")
    # and the retry loop should treat that the same as malformed JSON.
    adapter = _StubAdapter(["", _VALID_JSON])
    result = adapter.generate_decision("sys", "user")
    assert result.success is True
    assert result.metadata["attempt"] == 2
    assert adapter.call_count == 2


def test_generate_decision_does_not_retry_on_http_error(no_retry_sleep):
    # HTTP / auth / network errors must fail fast — retrying a 401 or a
    # rate-limited call just doubles the failure cost.
    adapter = _StubAdapter([RuntimeError("API 429: rate limited")])
    result = adapter.generate_decision("sys", "user")
    assert result.success is False
    assert result.metadata["attempt"] == 1
    assert adapter.call_count == 1
    assert "429" in (result.error or "")


def test_generate_decision_retries_on_missing_decisions_field(no_retry_sleep):
    # A schema-shaped JSON that's missing the `decisions` list still trips
    # ValueError inside _parse_response — that's our retry target too.
    bad_shape = '{"overall_reasoning": "x"}'
    adapter = _StubAdapter([bad_shape, _VALID_JSON])
    result = adapter.generate_decision("sys", "user")
    assert result.success is True
    assert result.metadata["attempt"] == 2
    assert adapter.call_count == 2


# --- v2 schema fields ------------------------------------------------------
#
# v2 adds period-level reasoning fields (cash_rationale, position_reviews,
# no_trade_reason) and per-trade reasoning fields (confidence_justification,
# why_now, reversal_justification) to the parser WITHOUT renaming any v1 field.
# These pin that the new fields are captured, normalized, and surfaced on
# DecisionResult — and that v1-shaped JSON still parses with them defaulted.


def test_parse_v2_full_decision_captures_new_fields():
    raw = json.dumps({
        "overall_reasoning": "Risk-on tape.",
        "cash_rationale": "Holding 8% dry powder for pending catalysts.",
        "position_reviews": [
            {"ticker": "nvda", "thesis_status": "Weakened", "implication": "Trim if it loses the 100d."}
        ],
        "decisions": [
            {
                "action": "buy", "ticker": "aapl", "target_weight": 0.1, "confidence": 8,
                "summary": "Adding AAPL on the breakout.",
                "reasoning": "Volume-confirmed.",
                "confidence_justification": "Strong band: the earnings beat is the named driver.",
                "why_now": "The gap-up confirms today, not later.",
                "reversal_justification": None,
            }
        ],
        "no_trade_reason": None,
    })
    out = BaseAdapter._parse_response(raw)
    assert out["cash_rationale"].startswith("Holding 8%")
    assert out["no_trade_reason"] is None
    assert out["position_reviews"] == [
        {"ticker": "NVDA", "thesis_status": "weakened", "implication": "Trim if it loses the 100d."}
    ]
    d = out["decisions"][0]
    assert d["ticker"] == "AAPL"
    assert d["confidence_justification"].startswith("Strong band")
    assert d["why_now"].startswith("The gap-up")
    assert d["reversal_justification"] is None


def test_parse_v2_reversal_justification_captured():
    raw = json.dumps({
        "overall_reasoning": "Defensive.",
        "decisions": [
            {"action": "sell", "ticker": "TSLA", "target_weight": 0.0, "confidence": 7,
             "summary": "Exiting TSLA.", "reasoning": "Thesis broke.",
             "confidence_justification": "Strong: the guidance cut is the driver.",
             "why_now": "Act before the close.",
             "reversal_justification": "Bought 2 days ago; the guidance cut is new since then."},
        ],
    })
    out = BaseAdapter._parse_response(raw)
    d = out["decisions"][0]
    assert d["reversal_justification"].startswith("Bought 2 days ago")
    # absent top-level v2 fields default cleanly
    assert out["cash_rationale"] is None
    assert out["position_reviews"] == []
    assert out["no_trade_reason"] is None


def test_parse_v1_shaped_still_parses_with_defaulted_v2_fields():
    # A v1-era response (no v2 fields at all) must still parse; the new fields
    # default to None/[]/"" so v1 history and any v1 retry remain valid.
    raw = '{"overall_reasoning": "x", "decisions": [{"action": "BUY", "ticker": "MSFT", "target_weight": 0.05, "confidence": 6, "reasoning": "ok"}]}'
    out = BaseAdapter._parse_response(raw)
    assert out["cash_rationale"] is None
    assert out["no_trade_reason"] is None
    assert out["position_reviews"] == []
    d = out["decisions"][0]
    assert d["confidence_justification"] == ""
    assert d["why_now"] == ""
    assert d["reversal_justification"] is None


def test_parse_v2_blank_nullables_collapse_to_none():
    raw = json.dumps({
        "overall_reasoning": "x",
        "cash_rationale": "   ",
        "no_trade_reason": "",
        "decisions": [],
    })
    out = BaseAdapter._parse_response(raw)
    assert out["cash_rationale"] is None
    assert out["no_trade_reason"] is None


def test_parse_v2_position_reviews_skips_malformed_entries():
    raw = json.dumps({
        "overall_reasoning": "x",
        "position_reviews": [
            {"ticker": "AAPL", "thesis_status": "intact", "implication": "hold"},
            {"thesis_status": "weakened"},   # no ticker -> dropped
            "not a dict",                      # dropped
        ],
        "decisions": [],
    })
    out = BaseAdapter._parse_response(raw)
    assert out["position_reviews"] == [
        {"ticker": "AAPL", "thesis_status": "intact", "implication": "hold"}
    ]


def test_generate_decision_surfaces_v2_fields_on_result(no_retry_sleep):
    payload = json.dumps({
        "overall_reasoning": "ok",
        "cash_rationale": "Deliberately ~100% deployed; opportunity set is rich.",
        "position_reviews": [{"ticker": "AMD", "thesis_status": "invalidated", "implication": "Exit in full."}],
        "decisions": [{"action": "HOLD", "ticker": "AAPL", "target_weight": 0, "confidence": 5,
                       "summary": "Hold AAPL.", "reasoning": "wait",
                       "confidence_justification": "Moderate band — mixed signals.",
                       "why_now": "No edge to act on now.", "reversal_justification": None}],
        "no_trade_reason": None,
    })
    adapter = _StubAdapter([payload])
    result = adapter.generate_decision("sys", "user")
    assert result.success is True
    assert result.cash_rationale.startswith("Deliberately")
    assert result.position_reviews[0]["thesis_status"] == "invalidated"
    assert result.no_trade_reason is None
    assert result.decisions[0]["why_now"] == "No edge to act on now."
