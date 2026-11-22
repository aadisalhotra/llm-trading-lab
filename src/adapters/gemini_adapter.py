"""Google Gemini adapter."""
from __future__ import annotations

import os
from typing import Any

import logging

from ..analytics.cost_rates import compute_call_cost_usd
from . import base as _base
from .base import (API_CALL_TIMEOUT_SECONDS, FINISH_ABNORMAL, FINISH_NORMAL, FINISH_REFUSAL,
                   FINISH_TRUNCATED, BaseAdapter, raise_for_finish)

logger = logging.getLogger("llmlab.adapters.gemini")

# The refreshed confirmatory arm (cohort_refresh_ruling.md rev. 2, section 5).
# gemini-4-argon's documented default thinking_level is MEDIUM (Google Cloud
# thinking table, google_cloud_thinking.html saved 2026-11-21), sent
# explicitly as thinking_level, never as a thinking budget (a budget returns
# 400 on Gemini 4). Keyed by the exact identifier; gemini-3.1-pro-preview (the
# Phase A snapshot) keeps its request byte for byte: no thinking_config.
REFRESHED_ARMS: dict[str, dict[str, str]] = {
    "gemini-4-argon": {"thinking_level": "MEDIUM"},
}
# Thinking counts against max_output_tokens on Argon (probe 2026-11-21: a
# 64-token cap ended MAX_TOKENS with 61 thought tokens). The provider limit for
# this request shape (non-streaming) is 32,768 (Models API outputTokenLimit;
# release notes: 65,536 per streaming segment). Calibration runs at 32,768;
# reasoning_allowance_limit_ruling.md then decides the cap: rung 1 or 2 keeps
# this shape (L = 32,768, H = 16,384, so rung 1 needs R <= 8,192); rung 3 moves
# to streaming under that ruling's section 3 conditions.
REFRESHED_MAX_OUTPUT_TOKENS = 32768
# The finish-reason rule for Argon (send_omit_measure_and_finish_reason_ruling
# section 3): STOP is the normal completion; MAX_TOKENS is a budget stop; a
# blocked prompt (any block reason) and every content-rule stop are refusals;
# every other value -- OTHER, LANGUAGE, NO_IMAGE, IMAGE_OTHER, the tool-call
# stops and unknown ones included -- is ABNORMAL_FINISH. The content-rule stops
# are the ruling's five plus the image siblings (hub H-OPS-14: the section 3.4
# definition governs over its example list).
REFUSAL_FINISH_REASONS = frozenset({"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII",
                                    "RECITATION", "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT",
                                    "IMAGE_RECITATION"})


def finish_class(finish_reason: str | None, blocked: bool) -> str:
    if blocked or finish_reason in REFUSAL_FINISH_REASONS:
        return FINISH_REFUSAL
    if finish_reason == "STOP":
        return FINISH_NORMAL
    if finish_reason == "MAX_TOKENS":
        return FINISH_TRUNCATED
    return FINISH_ABNORMAL


class GeminiAdapter(BaseAdapter):
    provider_name = "google"
    supports_vision = True

    @property
    def refreshed(self) -> bool:
        return self.model in REFRESHED_ARMS

    def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        from google import genai
        from google.genai import types as genai_types

        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY not set")

        client = genai.Client(api_key=api_key)
        config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            # Budget-equity raise, 4096 -> 16384 (Research ruling 2026-07-27).
            # max_output_tokens is a SHARED budget: gemini-3.1-pro's hidden
            # thinking spends against it before the visible JSON answer is
            # written, so at 4096 most calls truncated mid-string at ~150
            # visible tokens with finish_reason=MAX_TOKENS — the May/June/July
            # completeness epidemic. Same principle and headroom as the
            # DeepSeek adapter's 2026-05-21 raise: the reasoning trace must
            # never be able to truncate the decision. Complete answers average
            # ~660 tokens, so the extra headroom is free on normal replies.
            # Under google-genai (SDK migration 2026-08) this serializes to the
            # same REST field the legacy SDK sent — generationConfig.
            # maxOutputTokens — so the effective decision budget is unchanged.
            max_output_tokens=16384,
            # None fields are omitted from the request entirely, so with the
            # production temperature=None the wire body carries ONLY the three
            # fields above: no sampling params, no thinking_config. Reasoning
            # stays at the provider default (RQ6 config row) — thinking_level
            # is deliberately NOT set.
            temperature=self.temperature,
            # Client-side deadline (Hub registration, 2026-09-17). Without
            # this the call was bounded only by an undocumented SDK/transport
            # default that empirically sits at 600s and fails with no usage
            # metadata at all -- a stall, not a slow generation. HttpOptions
            # takes milliseconds; API_CALL_TIMEOUT_SECONDS is the shared
            # cross-adapter constant in seconds.
            http_options=genai_types.HttpOptions(
                timeout=API_CALL_TIMEOUT_SECONDS * 1000,
            ),
        )

        # Gemini takes one user turn whose parts are the chart images first,
        # then the prompt text. The SDK groups a mixed list of Parts/strings
        # into a single user Content, preserving order.
        contents: Any
        if images:
            parts: list[Any] = [
                genai_types.Part.from_bytes(data=img, mime_type="image/png")
                for img in images
            ]
            parts.append(user_prompt)
            contents = parts
        else:
            contents = user_prompt

        refreshed = REFRESHED_ARMS.get(self.model)
        if refreshed:
            if self.temperature is not None:
                # Nothing is sent to a refreshed arm until Research rules
                # (hub 2026-11-21).
                logger.warning("[%s] temperature %s not sent to a refreshed arm",
                               self.model, self.temperature)
            config = genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                max_output_tokens=REFRESHED_MAX_OUTPUT_TOKENS,
                thinking_config=(genai_types.ThinkingConfig(
                    thinking_level=refreshed["thinking_level"]) if self.level_sent else None),
                http_options=genai_types.HttpOptions(
                    timeout=int(self.transport_timeout(_base.API_CALL_TIMEOUT_SECONDS) * 1000)),
            )

        response = client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )

        # response.text concatenates the visible text parts and is None — not
        # an exception — when the response has no usable part. An empty
        # soft-stop therefore surfaces as "" and flows through the base
        # adapter's parse-failure retry path WITH the finish_reason forensics
        # below attached (the legacy SDK raised from .text on that shape,
        # discarding the forensics as a fail-fast API error).
        text = response.text or ""

        # Real model identifier for drift detection. The Gemini REST API returns
        # a `modelVersion` field naming the dated snapshot that actually served
        # the request (the alias gemini-3.1-pro-preview resolves to a pinned
        # build), and the SDK surfaces it as response.model_version. We capture
        # it when present so a provider-side alias repoint shows up in
        # model_id_returned, falling back to the configured id only when the
        # response doesn't expose it. If model_version is absent, Gemini drift
        # is NOT observable via the echo and must be tracked another way.
        #
        # Logging package 2026-11-15: the fallback above makes "absent" and
        # "echoed the alias" look identical in model_id_returned (the
        # migration probe showed the field present and verbatim
        # "gemini-3.1-pro-preview"). model_version_raw below keeps the raw
        # field, None when absent, so the two cases separate in the record.
        model_version_raw = getattr(response, "model_version", None)
        returned_id = model_version_raw or self.model

        # Gemini exposes usage_metadata on responses with prompt_token_count
        # and candidates_token_count (visible output only — thoughts are
        # counted separately below). Tolerate absent fields.
        usage = getattr(response, "usage_metadata", None)
        in_tok = 0
        out_tok = 0
        if usage:
            in_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
            out_tok = int(getattr(usage, "candidates_token_count", 0) or 0)
        # Cost stays keyed on the configured id (unchanged) — pricing is out of scope.
        # On the refreshed arm thought tokens, billed as output but reported
        # outside candidates_token_count, are priced too; the snapshot's cost
        # line is left as it was.
        thought_tok = int(getattr(usage, "thoughts_token_count", 0) or 0) if usage else 0
        cost = compute_call_cost_usd(self.model, in_tok,
                                     out_tok + (thought_tok if refreshed else 0))
        metadata: dict[str, Any] = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": cost,
            "model_version_raw": model_version_raw,
        }

        # Failure forensics (Operations 2026-07-27): finish_reason separates a
        # token-budget stop (MAX_TOKENS) from a safety/recitation soft-stop or
        # a clean STOP — the July completeness investigation stalled because it
        # was never persisted. thoughts_token_count matters with it: reasoning
        # models bill hidden thought tokens against max_output_tokens, so a
        # MAX_TOKENS stop with few visible output tokens is thinking overrun,
        # not a long answer. (The legacy SDK reported thoughts_token_count as
        # 0/absent; google-genai populates it, so this field carries real
        # values from the migration boundary onward.)
        candidates = list(getattr(response, "candidates", None) or [])
        if candidates:
            fr = getattr(candidates[0], "finish_reason", None)
            if fr is not None:
                metadata["finish_reason"] = getattr(fr, "name", str(fr))
        if usage:
            thoughts = int(getattr(usage, "thoughts_token_count", 0) or 0)
            if thoughts:
                metadata["thoughts_tokens"] = thoughts

        # --- retention audit 2026-09-09 -------------------------------------
        # Gemini exposes no build fingerprint; model_version above is the
        # identity signal and is already captured. Three diagnostics were still
        # being dropped, all of them the kind that made July's 113 Gemini
        # failures unautopsiable:
        #
        #   finish_message  — the free-text companion to finish_reason. When a
        #                     candidate stops for a reason the enum flattens,
        #                     this is the only place the detail exists.
        #   block_reason    — PROMPT-level, and the sharpest gap of the three:
        #                     a blocked prompt returns NO candidates at all, so
        #                     the finish_reason branch above never fires and the
        #                     run currently looks like an empty response with no
        #                     stated cause. Deliberately NOT folded into
        #                     finish_reason: the Phase B MAX_TOKENS gate reads
        #                     that field, and synthesising values into a series
        #                     a live gate consumes would change what it counts.
        #   cached_content_token_count — cache split; cost_rates.py prices
        #                     every call at the full input rate.
        fm = getattr(candidates[0], "finish_message", None) if candidates else None
        metadata["finish_detail"] = fm
        feedback = getattr(response, "prompt_feedback", None)
        br = getattr(feedback, "block_reason", None) if feedback else None
        metadata["block_reason"] = getattr(br, "name", str(br)) if br is not None else None
        cached = getattr(usage, "cached_content_token_count", None) if usage else None
        if refreshed:
            metadata["thoughts_tokens"] = thought_tok
            metadata["reasoning_effort_sent"] = (refreshed["thinking_level"]
                                                 if self.level_sent else None)
            metadata["max_tokens_sent"] = REFRESHED_MAX_OUTPUT_TOKENS
            metadata["transport"] = "non-streaming"
        metadata["cache_hit_tokens"] = cached
        metadata["cache_miss_tokens"] = (
            max(0, in_tok - int(cached)) if cached is not None else None
        )
        if refreshed:
            # The finish-reason rule, before any text leaves the adapter.
            fr_name = metadata.get("finish_reason")
            blocked = metadata.get("block_reason") is not None
            fc = finish_class(fr_name, blocked)
            metadata["finish_class"] = fc
            raise_for_finish(fc, f"{fc} finish: finish_reason={fr_name} "
                                 f"block_reason={metadata.get('block_reason')} model={returned_id}",
                             metadata)
        return text, returned_id, metadata
