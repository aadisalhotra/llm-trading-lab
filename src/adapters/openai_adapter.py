"""OpenAI / GPT adapter."""
from __future__ import annotations

import os

from .base import BaseAdapter


class OpenAIAdapter(BaseAdapter):
    provider_name = "openai"

    def _call_api(self, system_prompt: str, user_prompt: str) -> tuple[str, str]:
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

        client = OpenAI(api_key=api_key)
        # Newer GPT models reject `max_tokens` and require `max_completion_tokens`.
        # The OpenAI SDK accepts the new param across all current chat models.
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=4096,
        )
        text = response.choices[0].message.content or ""
        returned_id = getattr(response, "model", self.model)
        return text, returned_id
