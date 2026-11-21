"""Anthropic / Claude adapter."""
from __future__ import annotations

import base64
import os
import time
from typing import Any

import logging

from ..analytics.cost_rates import compute_call_cost_usd
from . import base as _base
from .base import (API_CALL_TIMEOUT_SECONDS, BaseAdapter, ModelRefusal, StreamDeadline,
                   StreamIncomplete, WallClockTimeout)

logger = logging.getLogger("llmlab.adapters.anthropic")

# The refreshed confirmatory arms (cohort_refresh_ruling.md rev. 2, section 5):
# reasoning cannot be disabled on either identifier, and each runs at its
# provider's documented default level, sent explicitly. Keyed by the exact
# identifier, so the level is a property of the model, never chosen per call.
# Any identifier not listed here (the Phase A snapshots claude-sonnet-4-6 and
# claude-opus-4-6) keeps its request byte for byte: no thinking, no effort.
REFRESHED_ARMS: dict[str, dict[str, str]] = {
    "claude-sonnet-5-5": {"effort": "high"},
    "claude-opus-5-5": {"effort": "medium"},
}
# Output cap for the refreshed arms. Thinking counts against max_tokens on
# these models (adapter probe 2026-11-21: a 64-token cap ended on max_tokens
# with 64 thinking tokens). Section 5 item 1 runs the calibration with the cap
# at the provider maximum, 128,000 for both identifiers (Models API,
# 2026-11-21); the frozen cap, 16,384 plus the measured allowance, replaces
# this value before the first validated session.
REFRESHED_MAX_TOKENS = 128_000


def thinking_tokens(details: Any) -> int | None:
    """usage.output_tokens_details.thinking_tokens, whichever shape the SDK
    hands back: a typed object, or a plain dict where the field is not yet in
    the installed SDK's schema (it then rides along as an extra)."""
    if details is None:
        return None
    if isinstance(details, dict):
        return details.get("thinking_tokens")
    return getattr(details, "thinking_tokens", None)


def read_stream(stream, deadline_seconds: float) -> dict[str, Any]:
    """Consume a Messages stream under a total wall-clock deadline and return
    what the stream itself reports (reasoning_allowance_limit_ruling.md
    section 3): the model from message_start, the stop reason and the usage
    from message_delta (thinking tokens included), and whether message_stop
    arrived. Raises WallClockTimeout at the deadline and StreamIncomplete if
    the stream ends without message_stop; neither is ever parsed."""
    facts: dict[str, Any] = {"model": None, "stop_reason": None, "input_tokens": None,
                             "output_tokens": None, "details": None, "message_stop": False}
    with StreamDeadline(deadline_seconds, stream.close) as deadline:
        try:
            for event in stream:
                kind = getattr(event, "type", None)
                if kind == "message_start":
                    msg = getattr(event, "message", None)
                    facts["model"] = getattr(msg, "model", None)
                    u = getattr(msg, "usage", None)
                    facts["input_tokens"] = getattr(u, "input_tokens", None)
                elif kind == "message_delta":
                    facts["stop_reason"] = getattr(getattr(event, "delta", None), "stop_reason",
                                                   None) or facts["stop_reason"]
                    u = getattr(event, "usage", None)
                    if u is not None:
                        facts["output_tokens"] = getattr(u, "output_tokens", None)
                        if getattr(u, "input_tokens", None) is not None:
                            facts["input_tokens"] = u.input_tokens
                        facts["details"] = getattr(u, "output_tokens_details", None) or facts["details"]
                elif kind == "message_stop":
                    facts["message_stop"] = True
        except Exception as e:
            if deadline.expired:
                raise WallClockTimeout(
                    f"stream exceeded its {deadline.seconds:.0f}s total deadline") from e
            raise
        if deadline.expired:
            raise WallClockTimeout(f"stream exceeded its {deadline.seconds:.0f}s total deadline")
    if not facts["message_stop"]:
        raise StreamIncomplete(
            f"stream ended without message_stop (model={facts['model']}, "
            f"stop_reason={facts['stop_reason']}, output_tokens={facts['output_tokens']})")
    return facts


class AnthropicAdapter(BaseAdapter):
    provider_name = "anthropic"
    supports_vision = True

    def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        # The refreshed arms' wall-clock deadline counts from here: SDK import,
        # client set-up and the wait for the first byte are all in the call.
        started = time.monotonic()
        import anthropic  # local import so other providers still work without this dep

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        client = anthropic.Anthropic(api_key=api_key, timeout=API_CALL_TIMEOUT_SECONDS)

        # Build content blocks. If images are present, send them BEFORE the
        # text so the model has chart context loaded when it reads the
        # numerical block — Anthropic's recommended ordering for multimodal.
        content: list[dict] = []
        if images:
            for img in images:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(img).decode("ascii"),
                    },
                })
        content.append({"type": "text", "text": user_prompt})

        refreshed = REFRESHED_ARMS.get(self.model)
        facts: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        if self.temperature is not None:
            if refreshed:
                # Nothing is sent to a refreshed arm until Research rules
                # (hub 2026-11-21); a value other than 1 is rejected anyway.
                logger.warning("[%s] temperature %s not sent to a refreshed arm",
                               self.model, self.temperature)
            else:
                extra["temperature"] = self.temperature
        if refreshed:
            # Streamed in calibration and production alike (hub 2026-11-21),
            # under reasoning_allowance_limit_ruling.md section 3: only the
            # transport differs from the non-streaming request; the call's
            # timeout is total wall-clock, enforced here; a stream without
            # message_stop is a failed call (StreamIncomplete), never parsed;
            # model, stop reason and usage are read from the stream's events.
            # The deadline is read at call time, so a calibration run can set
            # it (section 5 item 1: 600 s). It runs from before the request is
            # called: opening the stream waits for the response headers (bounded
            # by the same figure), and only the remainder is left for the body.
            deadline_s = float(_base.API_CALL_TIMEOUT_SECONDS)
            with client.messages.stream(
                model=self.model,
                max_tokens=REFRESHED_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": content}],
                thinking={"type": "adaptive"},
                output_config={"effort": refreshed["effort"]},
                timeout=deadline_s,
            ) as stream:
                facts = read_stream(stream, max(0.0, deadline_s - (time.monotonic() - started)))
                response = stream.get_final_message()
        else:
            response = client.messages.create(
                model=self.model,
                # Budget equalization, 4096 -> 16384 (output_budget_equalization_ruling.md, 2026-11-19).
                max_tokens=16384,
                system=system_prompt,
                messages=[{"role": "user", "content": content}],
                **extra,
            )
        # From the stream on the refreshed path; from the Message otherwise.
        stop_reason = facts["stop_reason"] if facts else getattr(response, "stop_reason", None)
        # A refusal is its own class on the refreshed arms only (section 6
        # item 2); the snapshots' response handling is unchanged.
        if refreshed and stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise ModelRefusal(
                f"refusal: model={facts.get('model') or getattr(response, 'model', self.model)} "
                f"category={getattr(details, 'category', None)} "
                f"explanation={getattr(details, 'explanation', None)}")
        # Concatenate text blocks
        parts: list[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        text = "".join(parts)
        returned_id = (facts["model"] or self.model) if facts else getattr(response, "model", self.model)

        # Capture token usage + USD cost — drives the Sonnet vs Opus
        # cost-performance comparison in the daily report's expansion cohort
        # section. Anthropic returns usage on every successful response.
        usage = getattr(response, "usage", None)
        if facts:
            in_tok = int(facts["input_tokens"] or 0)
            out_tok = int(facts["output_tokens"] or 0)
        else:
            in_tok = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
            out_tok = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
        cost = compute_call_cost_usd(returned_id, in_tok, out_tok)
        metadata: dict[str, Any] = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": cost,
        }

        # --- retention audit 2026-09-09 -------------------------------------
        # Anthropic exposes NO build fingerprint and NO reasoning-token counter
        # (extended thinking arrives as `thinking` content blocks, which this
        # adapter does not request), so `model` is the only identity signal for
        # the two Claude cells and there is nothing better to capture. That is
        # a real, disclosable asymmetry against the other four providers, not
        # an omission here. What IS available and was being dropped:
        #
        #   stop_reason  — the finish reason. `max_tokens` vs `end_turn`
        #                  distinguishes a truncated decision from a complete
        #                  one, which is exactly the forensics the July Gemini
        #                  completeness investigation lacked. Under the 4096-token
        #                  cap of 2026-04..11 it bound on 112 pilot responses.
        #   cache_read / cache_creation — Anthropic bills cache reads at a
        #                  discount and cache writes at a premium, and
        #                  cost_rates.py models neither.
        #
        # Diagnostics: read defensively, degrade to None, never raise.
        metadata["finish_reason"] = stop_reason
        # Thinking tokens, reported by the 5.5 models under
        # usage.output_tokens_details.thinking_tokens (and counted inside
        # output_tokens). None where the field is absent (the 4.6 snapshots,
        # which request no thinking).
        details = facts["details"] if facts else (
            getattr(usage, "output_tokens_details", None) if usage else None)
        metadata["thoughts_tokens"] = thinking_tokens(details)
        if refreshed:
            metadata["reasoning_effort_sent"] = refreshed["effort"]
            metadata["max_tokens_sent"] = REFRESHED_MAX_TOKENS
            metadata["transport"] = "stream"
            metadata["call_deadline_seconds"] = _base.API_CALL_TIMEOUT_SECONDS
        # Which stop sequence ended it, when stop_reason == "stop_sequence".
        metadata["finish_detail"] = getattr(response, "stop_sequence", None)
        metadata["service_tier"] = getattr(usage, "service_tier", None) if usage else None
        # Anthropic reports input_tokens EXCLUSIVE of cached reads, so the miss
        # count is input_tokens itself rather than a subtraction — do not copy
        # the OpenAI-shaped arithmetic here.
        cache_read = getattr(usage, "cache_read_input_tokens", None) if usage else None
        metadata["cache_hit_tokens"] = cache_read
        metadata["cache_miss_tokens"] = in_tok if cache_read is not None else None
        metadata["cache_creation_tokens"] = (
            getattr(usage, "cache_creation_input_tokens", None) if usage else None
        )
        return text, returned_id, metadata
