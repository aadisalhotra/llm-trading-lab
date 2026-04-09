"""DeepSeek adapter — OpenAI-compatible REST endpoint."""
from __future__ import annotations

import os

import requests

from .base import BaseAdapter


class DeepSeekAdapter(BaseAdapter):
    provider_name = "deepseek"
    BASE_URL = "https://api.deepseek.com/v1/chat/completions"

    def _call_api(self, system_prompt: str, user_prompt: str) -> tuple[str, str]:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not set")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        r = requests.post(self.BASE_URL, json=payload, headers=headers, timeout=120)
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        returned_id = data.get("model", self.model)
        return text, returned_id
