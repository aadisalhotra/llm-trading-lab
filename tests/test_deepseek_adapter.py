"""DeepSeek adapter tests — mocked REST endpoint, no network calls.

These pin the provider-substitution telemetry added 2026-09-09 ahead of the
2026-09-10T04:00Z forced substitution, in which every `deepseek-v4-pro` request
routes to V4.1 Flash while the model string on the wire stays `deepseek-v4-pro`.

`model_id_returned` is only that alias echo, so `detect_version_transition`
cannot fire on the swap. `system_fingerprint` can: the provider sets it, and
unlike cost-per-call it does not depend on our own rate table (registering a
new rate period would make the cost signal circular). A pre-boundary probe on
2026-09-10T01:13Z confirmed all four captured fields are populated on a live
`deepseek-v4-pro` response, with fingerprint a307abda487cd1b463329ccb945ce396
constant across samples and reasoning_tokens at 84-86% of completion_tokens.

The fields are diagnostics, not contract, so the second half of this file pins
that a response missing any of them still yields a successful decision with
None in their place — a telemetry gap must never cost us a trading decision.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from src.adapters.deepseek_adapter import DeepSeekAdapter

MODEL = "deepseek-v4-pro"
VALID_JSON = '{"decisions": [], "overall_reasoning": "Flat tape, no edge."}'

# Shaped from the real 2026-09-10T01:13Z pre-boundary probe response.
FINGERPRINT = "a307abda487cd1b463329ccb945ce396"


def _response_body(
    *,
    content: str = VALID_JSON,
    model: str | None = MODEL,
    fingerprint: str | None = FINGERPRINT,
    finish_reason: str | None = "stop",
    prompt_tokens: int = 559,
    completion_tokens: int = 8373,
    reasoning_tokens: int | None = 7163,
    cache_hit: int | None = 0,
    cache_miss: int | None = 559,
) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if reasoning_tokens is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    if cache_hit is not None:
        usage["prompt_cache_hit_tokens"] = cache_hit
    if cache_miss is not None:
        usage["prompt_cache_miss_tokens"] = cache_miss
    body: dict[str, Any] = {
        "id": "977c3c02-6175-4701-8860-6796b4b4661e",
        "object": "chat.completion",
        "created": 1789002826,
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": usage,
    }
    if model is not None:
        body["model"] = model
    if fingerprint is not None:
        body["system_fingerprint"] = fingerprint
    return body


class _StubResponse:
    def __init__(self, body: dict[str, Any], status: int = 200):
        self._body = body
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = "stub error body"

    def json(self) -> dict[str, Any]:
        return self._body


@pytest.fixture
def post(monkeypatch):
    """Patch requests.post inside the adapter module; record what we sent."""
    captured: dict[str, Any] = {}
    holder: dict[str, Any] = {"body": _response_body(), "status": 200}

    def _post(url, json=None, headers=None, timeout=None):  # noqa: A002
        captured.update(url=url, payload=json, headers=headers, timeout=timeout)
        return _StubResponse(holder["body"], holder["status"])

    monkeypatch.setattr("src.adapters.deepseek_adapter.requests.post", _post)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")
    return captured, holder


def _call(adapter: DeepSeekAdapter):
    return adapter._call_api("sys", "user")


@pytest.fixture
def adapter():
    return DeepSeekAdapter(model=MODEL)


# --- what we CAPTURE -------------------------------------------------------


def test_captures_substitution_telemetry(post, adapter):
    _captured, _holder = post
    _text, returned_id, md = _call(adapter)

    assert returned_id == MODEL
    assert md["system_fingerprint"] == FINGERPRINT
    assert md["finish_reason"] == "stop"
    assert md["thoughts_tokens"] == 7163
    assert md["cache_hit_tokens"] == 0
    assert md["cache_miss_tokens"] == 559
    # The pre-existing fields must be untouched by the addition.
    assert md["input_tokens"] == 559
    assert md["output_tokens"] == 8373
    assert md["cost_usd"] is not None


def test_fingerprint_moves_independently_of_the_alias_echo(post, adapter):
    """The whole point: the alias can hold still while the build changes.

    This is the 2026-09-10 substitution's exact shape — same model string,
    different model underneath — and the field that must notice it.
    """
    _captured, holder = post
    holder["body"] = _response_body(model=MODEL, fingerprint="ffffffffffffffffffffffffffffffff")
    _text, returned_id, md = _call(adapter)

    assert returned_id == MODEL, "alias echo unchanged, as the provider announced"
    assert md["system_fingerprint"] != FINGERPRINT, "but the build fingerprint moved"


def test_reasoning_share_is_recoverable_from_the_log_fields(post, adapter):
    # thoughts_tokens is billed inside completion_tokens (DeepSeek counts the
    # reasoning trace there), so the share is thoughts/output, not
    # thoughts/(thoughts+output). Pinning it stops a future reader from
    # double-counting the trace.
    _captured, _holder = post
    _text, _rid, md = _call(adapter)
    assert md["thoughts_tokens"] < md["output_tokens"]
    assert 0.80 < md["thoughts_tokens"] / md["output_tokens"] < 0.90


# --- what we SEND ----------------------------------------------------------


def test_request_params_unchanged(post, adapter):
    captured, _holder = post
    _call(adapter)
    payload = captured["payload"]
    assert payload["model"] == MODEL
    assert payload["max_tokens"] == 16384
    assert payload["reasoning_effort"] == "high"
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["response_format"] == {"type": "json_object"}
    # Thinking mode silently ignores temperature; production never sends one.
    assert "temperature" not in payload


# --- degradation: telemetry is diagnostics, never a decision blocker --------


@pytest.mark.parametrize(
    "kwargs, missing_key",
    [
        ({"fingerprint": None}, "system_fingerprint"),
        ({"finish_reason": None}, "finish_reason"),
        ({"reasoning_tokens": None}, "thoughts_tokens"),
        ({"cache_hit": None}, "cache_hit_tokens"),
        ({"cache_miss": None}, "cache_miss_tokens"),
    ],
)
def test_missing_telemetry_field_degrades_to_none(post, adapter, kwargs, missing_key):
    _captured, holder = post
    holder["body"] = _response_body(**kwargs)
    text, returned_id, md = _call(adapter)

    assert text == VALID_JSON, "the decision still comes back"
    assert returned_id == MODEL
    assert md[missing_key] is None
    assert md["input_tokens"] == 559


def test_usage_block_absent_entirely(post, adapter):
    _captured, holder = post
    body = _response_body()
    del body["usage"]
    holder["body"] = body
    text, _rid, md = _call(adapter)

    assert text == VALID_JSON
    assert md["input_tokens"] == 0
    assert md["output_tokens"] == 0
    assert md["thoughts_tokens"] is None
    assert md["cache_hit_tokens"] is None
    # finish_reason lives on the choice, not on usage, so it survives.
    assert md["finish_reason"] == "stop"


def test_malformed_choices_still_raises_for_the_retry_loop(post, adapter):
    """The parse-level retry depends on this raising — don't soften it.

    BaseAdapter.generate_decision retries on exceptions from _call_api's
    content extraction. Reading the content defensively would swallow a
    malformed response into a successful empty decision.
    """
    _captured, holder = post
    body = _response_body()
    body["choices"] = [{}]
    holder["body"] = body
    with pytest.raises((KeyError, TypeError)):
        _call(adapter)


def test_http_error_surfaces_body(post, adapter):
    _captured, holder = post
    holder["status"] = 429
    with pytest.raises(RuntimeError) as exc:
        _call(adapter)
    assert "429" in str(exc.value)
