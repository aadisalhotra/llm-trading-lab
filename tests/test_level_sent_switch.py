"""The level_sent switch: production sends each refreshed arm's ruled level;
only calibration omits it (streaming_and_send_omit_ruling_2026-11-21.md
section 2.3 item 1). Omitting drops exactly the level field, nothing else.
No provider is contacted.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic
import openai
import pytest
import requests
from google import genai

from src.adapters import get_adapter

DECISION = json.dumps({"decisions": [], "overall_reasoning": "ok"})


def test_production_default_is_sent():
    for prov, model in (("anthropic", "claude-sonnet-5-5"), ("openai", "gpt-6-astra"),
                        ("xai", "grok-4.7"), ("google", "gemini-4-argon")):
        assert get_adapter(prov, model).level_sent is True


@pytest.mark.parametrize("sent", [True, False])
def test_claude(monkeypatch, sent):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    seen = []

    class _S:
        def __init__(self, kw):
            seen.append(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

        def __iter__(self):
            yield SimpleNamespace(type="message_start", message=SimpleNamespace(
                model="claude-opus-5-5", usage=SimpleNamespace(input_tokens=1)))
            yield SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn"),
                                  usage=SimpleNamespace(input_tokens=1, output_tokens=2,
                                                        output_tokens_details=None))
            yield SimpleNamespace(type="message_stop")

        def get_final_message(self):
            return SimpleNamespace(model="claude-opus-5-5", stop_reason="end_turn", stop_sequence=None,
                                   stop_details=None, usage=SimpleNamespace(
                                       input_tokens=1, output_tokens=2, cache_read_input_tokens=0,
                                       cache_creation_input_tokens=0, service_tier="standard"),
                                   content=[SimpleNamespace(type="text", text=DECISION)])
    monkeypatch.setattr(anthropic, "Anthropic", lambda **k: SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **kw: _S(kw))))
    a = get_adapter("anthropic", "claude-opus-5-5")
    a.level_sent = sent
    r = a.generate_decision("S", "U")
    assert ("output_config" in seen[0]) is sent and seen[0]["thinking"] == {"type": "adaptive"}
    assert r.metadata["reasoning_effort_sent"] == ("medium" if sent else None)


@pytest.mark.parametrize("sent", [True, False])
def test_astra(monkeypatch, sent):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    seen = []
    resp = SimpleNamespace(model="gpt-6-astra", system_fingerprint=None, service_tier=None,
                           choices=[SimpleNamespace(message=SimpleNamespace(content=DECISION, refusal=None),
                                                    finish_reason="stop")],
                           usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1,
                                                 completion_tokens_details=None,
                                                 prompt_tokens_details=None))
    monkeypatch.setattr(openai, "OpenAI", lambda **k: SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=lambda **kw: seen.append(kw) or resp))))
    a = get_adapter("openai", "gpt-6-astra")
    a.level_sent = sent
    a.generate_decision("S", "U")
    assert ("reasoning_effort" in seen[0]) is sent and seen[0]["max_completion_tokens"] == 128_000


@pytest.mark.parametrize("sent", [True, False])
def test_grok(monkeypatch, sent):
    monkeypatch.setenv("XAI_API_KEY", "k")
    seen = []
    data = {"model": "grok-4.7", "choices": [{"message": {"content": DECISION}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    monkeypatch.setattr(requests, "post", lambda url, json=None, **k: seen.append(json) or SimpleNamespace(
        ok=True, status_code=200, json=lambda: data, text=""))
    a = get_adapter("xai", "grok-4.7")
    a.level_sent = sent
    a.generate_decision("S", "U")
    assert ("reasoning_effort" in seen[0]) is sent and seen[0]["max_tokens"] == 16384


@pytest.mark.parametrize("sent", [True, False])
def test_argon(monkeypatch, sent):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    seen = []
    usage = SimpleNamespace(prompt_token_count=1, candidates_token_count=1, thoughts_token_count=0,
                            cached_content_token_count=None)
    resp = SimpleNamespace(text=DECISION, model_version="gemini-4-argon", usage_metadata=usage,
                           candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"),
                                                       finish_message=None)], prompt_feedback=None)
    monkeypatch.setattr(genai, "Client", lambda **k: SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kw: seen.append(kw["config"]) or resp)))
    a = get_adapter("google", "gemini-4-argon")
    a.level_sent = sent
    a.generate_decision("S", "U")
    assert (seen[0].thinking_config is not None) is sent and seen[0].max_output_tokens == 32768
