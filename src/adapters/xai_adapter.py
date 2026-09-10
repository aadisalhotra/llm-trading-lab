"""xAI / Grok adapter — uses the OpenAI-compatible REST endpoint."""
from __future__ import annotations

import base64
import os
from typing import Any

import requests

from ..analytics.cost_rates import compute_call_cost_usd
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
    ) -> tuple[str, str, dict[str, Any]]:
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
        if self.temperature is not None:
            payload["temperature"] = self.temperature
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

        usage = data.get("usage", {}) or {}
        in_tok = int(usage.get("prompt_tokens", 0) or 0)
        out_tok = int(usage.get("completion_tokens", 0) or 0)
        cost = compute_call_cost_usd(returned_id, in_tok, out_tok)
        metadata: dict[str, Any] = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": cost,
        }

        # --- retention audit 2026-09-09 -------------------------------------
        # xAI follows the OpenAI chat-completions response schema, so the same
        # four fields the DeepSeek and OpenAI adapters were dropping are here
        # too. `model` is only an alias echo and cannot see a swap of the model
        # behind a constant string. Diagnostics: read defensively, degrade to
        # None, never raise.
        choice = (data.get("choices") or [{}])[0] or {}
        metadata["system_fingerprint"] = data.get("system_fingerprint")
        metadata["finish_reason"] = choice.get("finish_reason")
        # grok-4.20-*-reasoning bills its reasoning trace inside
        # completion_tokens, the same semantics as Gemini's thoughts_token_count.
        cdet = usage.get("completion_tokens_details") or {}
        pdet = usage.get("prompt_tokens_details") or {}
        metadata["thoughts_tokens"] = cdet.get("reasoning_tokens")
        cached = pdet.get("cached_tokens")
        metadata["cache_hit_tokens"] = cached
        metadata["cache_miss_tokens"] = (
            max(0, in_tok - int(cached)) if cached is not None else None
        )
        return text, returned_id, metadata
