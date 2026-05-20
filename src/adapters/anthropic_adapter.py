"""Anthropic / Claude adapter."""
from __future__ import annotations

import base64
import os
from typing import Any

from ..analytics.cost_rates import compute_call_cost_usd
from .base import BaseAdapter


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

        client = anthropic.Anthropic(api_key=api_key)

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

        extra: dict[str, Any] = {}
        if self.temperature is not None:
            extra["temperature"] = self.temperature
        response = client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
            **extra,
        )
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
        return text, returned_id, metadata
