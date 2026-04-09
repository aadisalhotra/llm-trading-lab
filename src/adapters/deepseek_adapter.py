"""DeepSeek adapter — OpenAI-compatible REST endpoint."""
from __future__ import annotations

import os
from typing import Any

import requests

from ..analytics.cost_rates import compute_call_cost_usd
from .base import BaseAdapter


class DeepSeekAdapter(BaseAdapter):
    provider_name = "deepseek"
    supports_vision = False   # deepseek-chat is text-only; deepseek-vl is a separate model
    BASE_URL = "https://api.deepseek.com/v1/chat/completions"

    def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes] | None = None,  # accepted but ignored — text-only model
    ) -> tuple[str, str, dict[str, Any]]:
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
        if not r.ok:
            # Surface the response body so the decision log captures the actual error,
            # not just "400 Bad Request" with no detail.
            raise RuntimeError(f"DeepSeek API {r.status_code}: {r.text[:500]}")
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        returned_id = data.get("model", self.model)

        usage = data.get("usage", {}) or {}
        in_tok = int(usage.get("prompt_tokens", 0) or 0)
        out_tok = int(usage.get("completion_tokens", 0) or 0)
        cost = compute_call_cost_usd(returned_id, in_tok, out_tok)
        metadata: dict[str, Any] = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": cost,
        }
        return text, returned_id, metadata
