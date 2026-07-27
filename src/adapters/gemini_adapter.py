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
        import google.generativeai as genai

        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY not set")

        genai.configure(api_key=api_key)
        generation_config: dict[str, Any] = {
            "response_mime_type": "application/json",
            "max_output_tokens": 4096,
        }
        if self.temperature is not None:
            generation_config["temperature"] = self.temperature
        model = genai.GenerativeModel(
            model_name=self.model,
            system_instruction=system_prompt,
            generation_config=generation_config,
        )

        # Gemini takes a list of parts where each part is a dict with either
        # "text" or "inline_data": {"mime_type", "data"}. The SDK accepts raw
        # bytes for inline_data and handles base64 encoding internally.
        if images:
            parts: list = []
            for img in images:
                parts.append({"mime_type": "image/png", "data": img})
            parts.append(user_prompt)
            response = model.generate_content(parts)
        else:
            response = model.generate_content(user_prompt)

        text = getattr(response, "text", "") or ""

        # Real model identifier for drift detection. The Gemini REST API returns
        # a `modelVersion` field naming the dated snapshot that actually served
        # the request (the alias gemini-3.1-pro-preview resolves to a pinned
        # build), and the Python SDK surfaces it as response.model_version. We
        # capture it when present so a provider-side alias repoint shows up in
        # model_id_returned, falling back to the configured id only when the
        # response doesn't expose it. If model_version is absent, Gemini drift
        # is NOT observable via the echo and must be tracked another way.
        returned_id = getattr(response, "model_version", None) or self.model

        # Gemini exposes usage_metadata on responses with prompt_token_count
        # and candidates_token_count. Names differ slightly across SDK
        # versions; tolerate both.
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
        # not a long answer.
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
