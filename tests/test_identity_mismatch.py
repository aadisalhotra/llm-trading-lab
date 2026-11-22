"""IDENTITY_MISMATCH (hub H-OPS-17; xai_probe_followup_ruling_2026-11-22.md
section 2): a refreshed arm's response naming any model not on its accepted
list -- or none -- is a failed decision, not parsed, executed or retried, on
either leg; exact names only. Snapshots unchanged until the handover.
No provider is contacted.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import openai
import pytest
import requests
from google import genai

from src.adapters import anthropic_adapter as aa
from src.adapters import gemini_adapter as ga
from src.adapters import get_adapter
from src.adapters import openai_adapter as oa
from src.adapters import xai_adapter as xa
from src.adapters.base import IdentityMismatch

ROOT = Path(__file__).resolve().parents[1]
DECISION = json.dumps({"decisions": [], "overall_reasoning": "ok"})


def test_each_arm_accepts_its_configured_identifier_only():
    assert aa.ACCEPTED_NAMES == {"claude-sonnet-5-5": ("claude-sonnet-5-5",),
                                 "claude-opus-5-5": ("claude-opus-5-5",)}
    assert oa.ACCEPTED_NAMES == {"gpt-6-astra": ("gpt-6-astra",)}
    assert xa.ACCEPTED_NAMES == {"grok-4.7": ("grok-4.7",)}
    assert ga.ACCEPTED_NAMES == {"gemini-4-argon": ("gemini-4-argon",)}


# ------------------------------------------------------------------- fakes
def _grok(monkeypatch, named, model="grok-4.7"):
    monkeypatch.setenv("XAI_API_KEY", "k")
    calls = []
    data = {"model": named, "choices": [{"message": {"content": DECISION}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or SimpleNamespace(
        ok=True, status_code=200, json=lambda: data, text=""))
    return get_adapter("xai", model), calls


def _astra(monkeypatch, named):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    calls = []
    resp = SimpleNamespace(model=named, system_fingerprint=None, service_tier=None,
                           choices=[SimpleNamespace(message=SimpleNamespace(content=DECISION, refusal=None),
                                                    finish_reason="stop")],
                           usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1,
                                                 completion_tokens_details=None, prompt_tokens_details=None))
    monkeypatch.setattr(openai, "OpenAI", lambda **k: SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=lambda **kw: calls.append(1) or resp))))
    return get_adapter("openai", "gpt-6-astra"), calls


def _argon(monkeypatch, named):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    calls = []
    usage = SimpleNamespace(prompt_token_count=1, candidates_token_count=1, thoughts_token_count=0,
                            cached_content_token_count=None)
    resp = SimpleNamespace(text=DECISION, model_version=named, usage_metadata=usage, prompt_feedback=None,
                           candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"),
                                                       finish_message=None)])
    monkeypatch.setattr(genai, "Client", lambda **k: SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kw: calls.append(1) or resp)))
    return get_adapter("google", "gemini-4-argon"), calls


def _claude(monkeypatch, named):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    calls = []

    class _S:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

        def __iter__(self):
            yield SimpleNamespace(type="message_start", message=SimpleNamespace(
                model=named, usage=SimpleNamespace(input_tokens=1)))
            yield SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn"),
                                  usage=SimpleNamespace(input_tokens=1, output_tokens=2,
                                                        output_tokens_details=None))
            yield SimpleNamespace(type="message_stop")

        def get_final_message(self):
            return SimpleNamespace(model=named, stop_reason="end_turn", stop_sequence=None,
                                   stop_details=None, usage=SimpleNamespace(
                                       input_tokens=1, output_tokens=2, cache_read_input_tokens=0,
                                       cache_creation_input_tokens=0, service_tier="standard"),
                                   content=[SimpleNamespace(type="text", text=DECISION)])
    monkeypatch.setattr(anthropic, "Anthropic", lambda **k: SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **kw: calls.append(1) or _S())))
    return get_adapter("anthropic", "claude-opus-5-5"), calls


# ---------------------------------------------------------- decision leg
@pytest.mark.parametrize("make,ok_name,bad_names", [
    (_grok, "grok-4.7", ["grok-4.3", "grok-4.7-latest", "GROK-4.7", "grok-4.7 ", None]),
    (_astra, "gpt-6-astra", ["gpt-6.2-astra", "gpt-6-astra-2026-09-04", None]),
    (_argon, "gemini-4-argon", ["gemini-4-carbon", "models/gemini-4-argon", None]),
    (_claude, "claude-opus-5-5", ["claude-opus-5", "claude-opus-5-5-20260921", None]),
])
def test_only_an_accepted_name_is_a_decision(monkeypatch, make, ok_name, bad_names):
    a, calls = make(monkeypatch, ok_name)
    assert a.generate_decision("S", "U").success
    for bad in bad_names:
        a, calls = make(monkeypatch, bad)
        r = a.generate_decision("S", "U")
        assert r.success is False and r.error_class == "IDENTITY_MISMATCH", bad
        assert r.raw_response == "" and r.decisions == [] and len(calls) == 1      # not retried
        assert r.metadata["model_named"] == bad


def test_snapshots_are_not_checked(monkeypatch):
    a, _ = _grok(monkeypatch, "grok-4.3", model="grok-4.20-0309-reasoning")
    assert a.generate_decision("S", "U").success


def test_screening_leg_raises(monkeypatch):
    a, _ = _grok(monkeypatch, "grok-4.3")
    with pytest.raises(IdentityMismatch):
        a.generate_raw("S", "U")


# --------------------------------------------------------------- pipeline
def test_pipeline_fails_the_decision_on_a_screening_mismatch_and_warns():
    src = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    one = src[src.index("def run_one_model("):src.index("def run_pipeline(")]
    assert "screen_identity = e" in one
    i = one.index("if screen_identity is not None:")
    j = one.index("decision_result = adapter.generate_decision(", i)
    assert 'identity_failure_result(adapter, screen_identity, "screening")' in one[i:j]  # no call
    assert 'kind="identity_mismatch"' in one and "Candidate identity event:" in one
    assert "error_class" not in one[i:one.index('kind="identity_mismatch"')]  # flow never reads it


def test_the_row_records_the_name_and_leg():
    src = (ROOT / "src" / "logging" / "decision_log.py").read_text(encoding="utf-8")
    assert '"response_model_named"' in src and '"identity_leg"' in src


def test_calibration_counts_a_mismatch_as_invalid_and_lists_names():
    spec = importlib.util.spec_from_file_location(
        "calib", ROOT / "scripts" / "calibration" / "calibrate_refreshed_arms.py")
    cal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cal)
    src = (ROOT / "scripts" / "calibration" / "calibrate_refreshed_arms.py").read_text(encoding="utf-8")
    assert "base.ERROR_CLASS_IDENTITY_MISMATCH: INVALID" in src
    recs = [{"arm": "cb_grok", "input_id": "i0", "leg": "trade", "setting": "omit", "cls": "invalid_finish",
             "position": 1, "reasoning_tokens": None, "total_output": None, "latency_s": 1.0,
             "model_named": "grok-4.3", "error_class": "IDENTITY_MISMATCH", "finish_reason": "stop"}]
    s = cal.summarize_arm("cb_grok", recs, 840)
    assert s["model_names_seen"] == {"grok-4.3": 1} and s["valid_trading_calls"] == 0
