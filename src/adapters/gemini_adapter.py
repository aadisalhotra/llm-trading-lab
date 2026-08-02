"""Google Gemini adapter."""
from __future__ import annotations

import os
from typing import Any

from ..analytics.cost_rates import compute_call_cost_usd
from .base import BaseAdapter


class GeminiAdapter(BaseAdapter):
    provider_name = "google"
    supports_vision = True

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
        returned_id = getattr(response, "model_version", None) or self.model

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
        cost = compute_call_cost_usd(self.model, in_tok, out_tok)
        metadata: dict[str, Any] = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": cost,
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
        return text, returned_id, metadata
