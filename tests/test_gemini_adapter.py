"""Gemini adapter tests — mocked google-genai client, no network calls.

The ④b failure-forensics fields (finish_reason, thoughts_tokens), the
model_version echo, and the exact request config had to survive the
google-generativeai → google-genai SDK migration (2026-08): the Phase B
validation gates and the post-migration continuity diagnostic read them.
These tests pin both directions — what we SEND (only the three intended
config fields; no sampling params, no thinking_config) and what we CAPTURE
(finish_reason / thoughts / model_version off the real google-genai
response types).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from google.genai import types as genai_types

from src.adapters.gemini_adapter import GeminiAdapter

MODEL = "gemini-3.1-pro-preview"


def _response(
    text: str | None = '{"decisions": [], "overall_reasoning": "x"}',
    finish: genai_types.FinishReason | None = genai_types.FinishReason.STOP,
    model_version: str | None = "gemini-3.1-pro-002",
    prompt_tokens: int = 1200,
    visible_tokens: int = 660,
    thoughts_tokens: int = 2100,
) -> genai_types.GenerateContentResponse:
    """A real google-genai response object, shaped like a live reply."""
    content = None
    if text is not None:
        content = genai_types.Content(
            role="model", parts=[genai_types.Part(text=text)],
        )
    candidate = genai_types.Candidate(content=content, finish_reason=finish)
    return genai_types.GenerateContentResponse(
        model_version=model_version,
        candidates=[candidate],
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=visible_tokens,
            thoughts_token_count=thoughts_tokens,
            total_token_count=prompt_tokens + visible_tokens + thoughts_tokens,
        ),
    )


class _StubClient:
    """Stands in for genai.Client; records the request, returns a canned reply."""

    captured: dict[str, Any] = {}
    reply: genai_types.GenerateContentResponse = _response()

    def __init__(self, api_key: str | None = None, **kwargs: Any):
        _StubClient.captured["api_key_present"] = bool(api_key)
        self.models = self

    def generate_content(self, *, model: str, contents: Any, config: Any):
        _StubClient.captured.update(model=model, contents=contents, config=config)
        return _StubClient.reply


@pytest.fixture()
def stub_client(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr("google.genai.Client", _StubClient)
    _StubClient.captured = {}
    _StubClient.reply = _response()
    return _StubClient


def test_request_carries_only_the_three_intended_fields(stub_client):
    """Wire semantics: system_instruction + JSON mime + max_output_tokens=16384,
    and nothing else — no sampling params, no thinking_config (provider-default
    reasoning). None-valued config fields are omitted from the request."""
    adapter = GeminiAdapter(MODEL)
    adapter._call_api("SYS", "USER")
    cfg = stub_client.captured["config"]
    sent = cfg.model_dump(exclude_none=True)
    assert set(sent) == {"system_instruction", "response_mime_type", "max_output_tokens"}
    assert sent["max_output_tokens"] == 16384
    assert sent["response_mime_type"] == "application/json"
    assert cfg.thinking_config is None
    assert cfg.temperature is None
    assert stub_client.captured["model"] == MODEL


def test_rq6_probe_temperature_passthrough(stub_client):
    """Only the determinism probe sets temperature; it must reach the config
    (0.0 is a real value, not dropped as falsy)."""
    adapter = GeminiAdapter(MODEL, temperature=0.0)
    adapter._call_api("SYS", "USER")
    assert stub_client.captured["config"].temperature == 0.0


def test_image_parts_precede_text(stub_client):
    """Vision assembly parity: image parts first, prompt text last, one turn."""
    adapter = GeminiAdapter(MODEL)
    adapter._call_api("SYS", "USER", images=[b"\x89PNG-one", b"\x89PNG-two"])
    contents = stub_client.captured["contents"]
    assert len(contents) == 3
    assert contents[0].inline_data.data == b"\x89PNG-one"
    assert contents[0].inline_data.mime_type == "image/png"
    assert contents[1].inline_data.data == b"\x89PNG-two"
    assert contents[2] == "USER"


def test_forensics_captured_on_clean_stop(stub_client):
    adapter = GeminiAdapter(MODEL)
    text, returned_id, meta = adapter._call_api("SYS", "USER")
    assert text == '{"decisions": [], "overall_reasoning": "x"}'
    assert returned_id == "gemini-3.1-pro-002"  # model_version echo, not the alias
    assert meta["finish_reason"] == "STOP"
    assert meta["input_tokens"] == 1200
    assert meta["output_tokens"] == 660  # visible only; thoughts counted apart
    assert meta["thoughts_tokens"] == 2100


def test_forensics_captured_on_max_tokens_truncation(stub_client):
    _StubClient.reply = _response(
        text='{"decisions": [{"action": "BU',  # cut mid-string
        finish=genai_types.FinishReason.MAX_TOKENS,
        visible_tokens=150,
        thoughts_tokens=16234,
    )
    adapter = GeminiAdapter(MODEL)
    text, _rid, meta = adapter._call_api("SYS", "USER")
    assert meta["finish_reason"] == "MAX_TOKENS"
    assert meta["thoughts_tokens"] == 16234
    assert text.endswith('"BU')


def test_empty_soft_stop_returns_blank_with_forensics(stub_client):
    """google-genai's .text is None (not an exception) when the response has
    no usable part. The adapter must return "" so the base retry path runs —
    and the finish_reason forensics must still be attached (the legacy SDK
    raised from .text here, losing them to a fail-fast API error)."""
    _StubClient.reply = _response(text=None, finish=genai_types.FinishReason.SAFETY)
    adapter = GeminiAdapter(MODEL)
    text, _rid, meta = adapter._call_api("SYS", "USER")
    assert text == ""
    assert meta["finish_reason"] == "SAFETY"


def test_model_version_fallback_to_configured_id(stub_client):
    """If the response omits model_version, drift is unobservable — fall back
    to the configured alias rather than fabricating an id."""
    _StubClient.reply = _response(model_version=None)
    adapter = GeminiAdapter(MODEL)
    _text, returned_id, _meta = adapter._call_api("SYS", "USER")
    assert returned_id == MODEL


def test_zero_thoughts_omitted(stub_client):
    """thoughts_tokens is only recorded when nonzero, matching the pre-migration
    metadata shape (the legacy SDK reported 0/absent)."""
    _StubClient.reply = _response(thoughts_tokens=0)
    adapter = GeminiAdapter(MODEL)
    _text, _rid, meta = adapter._call_api("SYS", "USER")
    assert "thoughts_tokens" not in meta
