"""Anthropic / Claude adapter."""
from __future__ import annotations

import os

from .base import BaseAdapter


class AnthropicAdapter(BaseAdapter):
    provider_name = "anthropic"

    def _call_api(self, system_prompt: str, user_prompt: str) -> tuple[str, str]:
        import anthropic  # local import so other providers still work without this dep

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        # Concatenate text blocks
        parts: list[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        text = "".join(parts)
        returned_id = getattr(response, "model", self.model)
        return text, returned_id
