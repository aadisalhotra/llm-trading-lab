"""Anthropic / Claude adapter."""
from __future__ import annotations

import base64
import os
from typing import Any

import logging

from ..analytics.cost_rates import compute_call_cost_usd
from .base import API_CALL_TIMEOUT_SECONDS, BaseAdapter, ModelRefusal

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


class AnthropicAdapter(BaseAdapter):
    provider_name = "anthropic"
    supports_vision = True

    def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
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
        # The streamed final message drops usage.output_tokens_details (SDK
        # accumulator); the message_delta event carries it, so it is read there.
        stream_details = None
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
            # Streamed: a non-streaming request at this cap is refused by the
            # SDK (it expects more than its 10-minute non-streaming limit).
            # get_final_message() returns the same Message as create().
            with client.messages.stream(
                model=self.model,
                max_tokens=REFRESHED_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": content}],
                thinking={"type": "adaptive"},
                output_config={"effort": refreshed["effort"]},
            ) as stream:
                for event in stream:
                    if getattr(event, "type", None) == "message_delta":
                        delta_usage = getattr(event, "usage", None)
                        stream_details = (getattr(delta_usage, "output_tokens_details", None)
                                          or stream_details)
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
        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            raise ModelRefusal(
                f"refusal: model={getattr(response, 'model', self.model)} "
                f"category={getattr(details, 'category', None)} "
                f"explanation={getattr(details, 'explanation', None)}")
        # Concatenate text blocks
        parts: list[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        text = "".join(parts)
        returned_id = getattr(response, "model", self.model)

        # Capture token usage + USD cost — drives the Sonnet vs Opus
        # cost-performance comparison in the daily report's expansion cohort
        # section. Anthropic returns usage on every successful response.
        usage = getattr(response, "usage", None)
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
        metadata["finish_reason"] = getattr(response, "stop_reason", None)
        # Thinking tokens, reported by the 5.5 models under
        # usage.output_tokens_details.thinking_tokens (and counted inside
        # output_tokens). None where the field is absent (the 4.6 snapshots,
        # which request no thinking).
        details = (getattr(usage, "output_tokens_details", None) if usage else None) or stream_details
        metadata["thoughts_tokens"] = thinking_tokens(details)
        if refreshed:
            metadata["reasoning_effort_sent"] = refreshed["effort"]
            metadata["max_tokens_sent"] = REFRESHED_MAX_TOKENS
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
