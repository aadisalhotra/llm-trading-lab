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
        model = genai.GenerativeModel(
            model_name=self.model,
            system_instruction=system_prompt,
            generation_config={
                "response_mime_type": "application/json",
                "max_output_tokens": 4096,
            },
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

        # Gemini exposes usage_metadata on responses with prompt_token_count
        # and candidates_token_count. Names differ slightly across SDK
        # versions; tolerate both.
        usage = getattr(response, "usage_metadata", None)
        in_tok = 0
        out_tok = 0
        if usage:
            in_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
            out_tok = int(getattr(usage, "candidates_token_count", 0) or 0)
        cost = compute_call_cost_usd(self.model, in_tok, out_tok)
        metadata: dict[str, Any] = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": cost,
        }
        return text, self.model, metadata  # Gemini SDK doesn't echo the model id back
