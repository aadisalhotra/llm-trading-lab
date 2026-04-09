"""Adapter tests — focused on the JSON parser. No network calls."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

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
