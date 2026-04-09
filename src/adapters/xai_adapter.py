"""xAI / Grok adapter — uses the OpenAI-compatible REST endpoint."""
from __future__ import annotations

import base64
import os

import requests

from .base import BaseAdapter


class XAIAdapter(BaseAdapter):
    provider_name = "xai"
    supports_vision = True   # grok-4 accepts image_url content blocks
    BASE_URL = "https://api.x.ai/v1/chat/completions"

    def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes] | None = None,
    ) -> tuple[str, str]:
        api_key = os.getenv("XAI_API_KEY")
        if not api_key:
            raise RuntimeError("XAI_API_KEY not set")

        # xAI follows the OpenAI chat-completions schema for multimodal:
        # user.content can be a list of {type:image_url|text, ...} blocks.
        if images:
            user_content: list[dict] | str = []
            for img in images:
                b64 = base64.b64encode(img).decode("ascii")
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                })
            user_content.append({"type": "text", "text": user_prompt})
        else:
            user_content = user_prompt

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        r = requests.post(self.BASE_URL, json=payload, headers=headers, timeout=120)
        if not r.ok:
            raise RuntimeError(f"xAI API {r.status_code}: {r.text[:500]}")
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        returned_id = data.get("model", self.model)
        return text, returned_id
