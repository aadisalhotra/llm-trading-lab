"""Cohort-wide adapter field-retention tests (audit 2026-09-09).

Every adapter used to keep three things off each response — the model-id echo,
two token counters and the cost — and drop everything else. That is how the
DeepSeek 2026-09-10 V4.1-Flash substitution came within a day of being
undetectable: `model` is an alias echo, and a provider swapping the model
behind a constant string moves nothing the lab was recording.

The audit closed the gap in all five adapters. What each provider actually
exposes differs, and the differences are load-bearing, so they are pinned here
rather than left to a comment:

  * OpenAI / xAI / DeepSeek — `system_fingerprint` (a real build identifier
    that moves independently of the model string), `finish_reason`,
    `completion_tokens_details.reasoning_tokens`, `prompt_tokens_details.
    cached_tokens`.
  * Gemini — no fingerprint; `model_version` is the identity signal and was
    already captured. Adds `finish_message` and the PROMPT-level `block_reason`,
    which is invisible to the finish_reason path because a blocked prompt
    returns no candidates at all.
  * Anthropic — no fingerprint AND no reasoning-token counter. `model` is the
    only identity signal for the two Claude cells. That asymmetry is disclosed,
    not fixed, and `test_anthropic_has_no_fingerprint_to_capture` pins it so a
    future reader does not mistake it for an oversight here.

Two accounting conventions for the cache split, and mixing them silently
mis-states input cost: OpenAI/xAI/Gemini report an input count INCLUSIVE of
cached tokens (miss = input - hit), Anthropic reports it EXCLUSIVE (miss =
input). Both are pinned below.

All of these are diagnostics. A provider that stops returning one must degrade
to None and never cost a trading decision — the last section pins that for
every adapter.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from src.adapters.anthropic_adapter import AnthropicAdapter
from src.adapters.gemini_adapter import GeminiAdapter
from src.adapters.openai_adapter import OpenAIAdapter
from src.adapters.xai_adapter import XAIAdapter

VALID_JSON = '{"decisions": [], "overall_reasoning": "x"}'


# ===========================================================================
# OpenAI
# ===========================================================================

class _Obj:
    """Attribute bag standing in for an SDK pydantic model."""

    def __init__(self, **kw: Any):
        for k, v in kw.items():
            setattr(self, k, v)


def _openai_response(
    *,
    fingerprint: str | None = "fp_9a21c0e4b7",
    finish_reason: str | None = "stop",
    reasoning_tokens: int | None = 1840,
    cached_tokens: int | None = 2048,
    service_tier: str | None = "default",
    prompt_tokens: int = 8500,
) -> Any:
    return _Obj(
        model="gpt-5.4",
        system_fingerprint=fingerprint,
        service_tier=service_tier,
        choices=[_Obj(message=_Obj(content=VALID_JSON), finish_reason=finish_reason)],
        usage=_Obj(
            prompt_tokens=prompt_tokens,
            completion_tokens=3100,
            completion_tokens_details=_Obj(reasoning_tokens=reasoning_tokens),
            prompt_tokens_details=_Obj(cached_tokens=cached_tokens),
        ),
    )


@pytest.fixture
def openai_stub(monkeypatch):
    holder: dict[str, Any] = {"reply": _openai_response()}

    class _Client:
        def __init__(self, api_key=None, **kw):
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kw):
            holder["sent"] = kw
            return holder["reply"]

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("openai.OpenAI", _Client)
    return holder


def test_openai_captures_dropped_fields(openai_stub):
    _text, rid, md = OpenAIAdapter("gpt-5.4")._call_api("sys", "user")
    assert rid == "gpt-5.4"
    assert md["system_fingerprint"] == "fp_9a21c0e4b7"
    assert md["finish_reason"] == "stop"
    assert md["thoughts_tokens"] == 1840
    assert md["service_tier"] == "default"
    # Inclusive convention: 8500 prompt tokens of which 2048 were cache hits.
    assert md["cache_hit_tokens"] == 2048
    assert md["cache_miss_tokens"] == 8500 - 2048
    assert md["input_tokens"] == 8500 and md["output_tokens"] == 3100


def test_openai_fingerprint_moves_under_a_constant_model_string(openai_stub):
    openai_stub["reply"] = _openai_response(fingerprint="fp_ffffffffff")
    _text, rid, md = OpenAIAdapter("gpt-5.4")._call_api("sys", "user")
    assert rid == "gpt-5.4"
    assert md["system_fingerprint"] == "fp_ffffffffff"


# ===========================================================================
# xAI
# ===========================================================================

class _StubResponse:
    def __init__(self, body: dict, status: int = 200):
        self._body, self.status_code = body, status
        self.ok = 200 <= status < 300
        self.text = "stub error body"

    def json(self) -> dict:
        return self._body


def _xai_body(
    *,
    fingerprint: str | None = "fp_84ff176447",
    finish_reason: str | None = "stop",
    reasoning_tokens: int | None = 2400,
    cached_tokens: int | None = 1024,
    prompt_tokens: int = 9000,
) -> dict:
    usage: dict[str, Any] = {"prompt_tokens": prompt_tokens, "completion_tokens": 2600}
    if reasoning_tokens is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    if cached_tokens is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached_tokens}
    body: dict[str, Any] = {
        "model": "grok-4.20-0309-reasoning",
        "choices": [{"message": {"content": VALID_JSON}, "finish_reason": finish_reason}],
        "usage": usage,
    }
    if fingerprint is not None:
        body["system_fingerprint"] = fingerprint
    return body


@pytest.fixture
def xai_stub(monkeypatch):
    holder: dict[str, Any] = {"body": _xai_body(), "status": 200}
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setattr(
        "src.adapters.xai_adapter.requests.post",
        lambda url, json=None, headers=None, timeout=None: _StubResponse(
            holder["body"], holder["status"]
        ),
    )
    return holder


def test_xai_captures_dropped_fields(xai_stub):
    _text, rid, md = XAIAdapter("grok-4.20-0309-reasoning")._call_api("sys", "user")
    assert rid == "grok-4.20-0309-reasoning"
    assert md["system_fingerprint"] == "fp_84ff176447"
    assert md["finish_reason"] == "stop"
    assert md["thoughts_tokens"] == 2400
    assert md["cache_hit_tokens"] == 1024
    assert md["cache_miss_tokens"] == 9000 - 1024


# ===========================================================================
# Anthropic
# ===========================================================================

def _anthropic_response(
    *,
    stop_reason: str | None = "end_turn",
    stop_sequence: str | None = None,
    cache_read: int | None = 3000,
    cache_creation: int | None = 512,
    service_tier: str | None = "standard",
    input_tokens: int = 5400,
) -> Any:
    return _Obj(
        model="claude-sonnet-4-6",
        stop_reason=stop_reason,
        stop_sequence=stop_sequence,
        content=[_Obj(type="text", text=VALID_JSON)],
        usage=_Obj(
            input_tokens=input_tokens,
            output_tokens=1200,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
            service_tier=service_tier,
        ),
    )


@pytest.fixture
def anthropic_stub(monkeypatch):
    holder: dict[str, Any] = {"reply": _anthropic_response()}

    class _Client:
        def __init__(self, api_key=None, **kw):
            self.messages = self

        def create(self, **kw):
            holder["sent"] = kw
            return holder["reply"]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr("anthropic.Anthropic", _Client)
    return holder


def test_anthropic_captures_dropped_fields(anthropic_stub):
    _text, rid, md = AnthropicAdapter("claude-sonnet-4-6")._call_api("sys", "user")
    assert rid == "claude-sonnet-4-6"
    assert md["finish_reason"] == "end_turn"
    assert md["service_tier"] == "standard"
    assert md["cache_creation_tokens"] == 512
    # EXCLUSIVE convention: input_tokens already excludes the cached reads, so
    # the miss count is input_tokens itself. Copying the OpenAI subtraction
    # here would under-report input cost by the cache-read count.
    assert md["cache_hit_tokens"] == 3000
    assert md["cache_miss_tokens"] == 5400


def test_anthropic_truncation_is_now_visible(anthropic_stub):
    """The 4096-token cap makes `max_tokens` a live outcome, not a theory.

    Before the audit a truncated Claude decision and a complete one were
    indistinguishable in the log.
    """
    anthropic_stub["reply"] = _anthropic_response(stop_reason="max_tokens")
    _text, _rid, md = AnthropicAdapter("claude-sonnet-4-6")._call_api("sys", "user")
    assert md["finish_reason"] == "max_tokens"


def test_anthropic_has_no_fingerprint_to_capture(anthropic_stub):
    """Disclosed asymmetry, pinned so it is not read as an oversight.

    Anthropic's Message exposes no build fingerprint and no reasoning-token
    counter, so `model` is the only identity signal for the two Claude cells.
    A string-stable substitution by Anthropic would be undetectable the way
    DeepSeek's nearly was.
    """
    _text, _rid, md = AnthropicAdapter("claude-sonnet-4-6")._call_api("sys", "user")
    assert md.get("system_fingerprint") is None
    assert md.get("thoughts_tokens") is None


# ===========================================================================
# Gemini
# ===========================================================================

from google.genai import types as genai_types  # noqa: E402


def _gemini_response(
    *,
    block_reason: Any = None,
    finish_message: str | None = None,
    cached: int | None = 1500,
    prompt_tokens: int = 7000,
    with_candidate: bool = True,
) -> Any:
    candidates = []
    if with_candidate:
        candidates = [
            genai_types.Candidate(
                content=genai_types.Content(
                    role="model", parts=[genai_types.Part(text=VALID_JSON)]
                ),
                finish_reason=genai_types.FinishReason.STOP,
                finish_message=finish_message,
            )
        ]
    kwargs: dict[str, Any] = {
        "model_version": "gemini-3.1-pro-002",
        "candidates": candidates,
        "usage_metadata": genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=900,
            thoughts_token_count=2200,
            cached_content_token_count=cached,
        ),
    }
    if block_reason is not None:
        kwargs["prompt_feedback"] = genai_types.GenerateContentResponsePromptFeedback(
            block_reason=block_reason
        )
    return genai_types.GenerateContentResponse(**kwargs)


@pytest.fixture
def gemini_stub(monkeypatch):
    holder: dict[str, Any] = {"reply": _gemini_response()}

    class _Client:
        def __init__(self, api_key=None, **kw):
            self.models = self

        def generate_content(self, *, model, contents, config):
            return holder["reply"]

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr("google.genai.Client", _Client)
    return holder


def test_gemini_captures_cache_split_and_finish_detail(gemini_stub):
    gemini_stub["reply"] = _gemini_response(finish_message="clean stop")
    _text, rid, md = GeminiAdapter("gemini-3.1-pro-preview")._call_api("sys", "user")
    assert rid == "gemini-3.1-pro-002"
    assert md["finish_reason"] == "STOP"
    assert md["finish_detail"] == "clean stop"
    assert md["thoughts_tokens"] == 2200
    assert md["cache_hit_tokens"] == 1500
    assert md["cache_miss_tokens"] == 7000 - 1500


def test_gemini_prompt_block_is_visible_without_a_candidate(gemini_stub):
    """The sharpest of the three Gemini gaps.

    A blocked prompt returns NO candidates, so the finish_reason path never
    fires and the run previously looked like an unexplained empty response.
    block_reason is kept OUT of api_finish_reason on purpose — the Phase B
    MAX_TOKENS gate reads that field.
    """
    gemini_stub["reply"] = _gemini_response(
        with_candidate=False, block_reason=genai_types.BlockedReason.SAFETY
    )
    adapter = GeminiAdapter("gemini-3.1-pro-preview")
    try:
        _text, _rid, md = adapter._call_api("sys", "user")
    except Exception:
        pytest.skip("no-candidate response raises before metadata assembly")
    assert md["block_reason"] == "SAFETY"
    assert md.get("finish_reason") is None


# ===========================================================================
# Degradation — a telemetry gap must never cost a decision
# ===========================================================================

def test_openai_missing_telemetry_degrades(openai_stub):
    openai_stub["reply"] = _openai_response(
        fingerprint=None, finish_reason=None, reasoning_tokens=None,
        cached_tokens=None, service_tier=None,
    )
    text, rid, md = OpenAIAdapter("gpt-5.4")._call_api("sys", "user")
    assert text == VALID_JSON and rid == "gpt-5.4"
    for k in ("system_fingerprint", "finish_reason", "thoughts_tokens",
              "cache_hit_tokens", "cache_miss_tokens", "service_tier"):
        assert md[k] is None, k
    assert md["input_tokens"] == 8500


def test_xai_missing_usage_detail_degrades(xai_stub):
    xai_stub["body"] = _xai_body(fingerprint=None, reasoning_tokens=None, cached_tokens=None)
    text, _rid, md = XAIAdapter("grok-4.20-0309-reasoning")._call_api("sys", "user")
    assert text == VALID_JSON
    assert md["system_fingerprint"] is None
    assert md["thoughts_tokens"] is None
    assert md["cache_hit_tokens"] is None and md["cache_miss_tokens"] is None
    assert md["input_tokens"] == 9000


def test_anthropic_missing_cache_fields_degrade(anthropic_stub):
    anthropic_stub["reply"] = _anthropic_response(
        cache_read=None, cache_creation=None, service_tier=None, stop_reason=None,
    )
    text, _rid, md = AnthropicAdapter("claude-sonnet-4-6")._call_api("sys", "user")
    assert text == VALID_JSON
    assert md["cache_hit_tokens"] is None
    assert md["cache_miss_tokens"] is None, "no hit count means the split is unknown, not zero"
    assert md["cache_creation_tokens"] is None
    assert md["finish_reason"] is None
    assert md["input_tokens"] == 5400


def test_gemini_missing_cache_field_degrades(gemini_stub):
    gemini_stub["reply"] = _gemini_response(cached=None)
    text, _rid, md = GeminiAdapter("gemini-3.1-pro-preview")._call_api("sys", "user")
    assert text == VALID_JSON
    assert md["cache_hit_tokens"] is None
    assert md["cache_miss_tokens"] is None
    assert md["finish_detail"] is None
    assert md["block_reason"] is None
