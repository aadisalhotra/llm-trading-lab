"""DeepSeek adapter — OpenAI-compatible REST endpoint.

Parse-level retry (malformed JSON, empty body) is inherited from
``BaseAdapter.generate_decision`` — DeepSeek was the first provider we
saw that failure mode on, but every provider now gets the same 2-attempt
retry with a 15s cooldown for free.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

from ..analytics.cost_rates import compute_call_cost_usd
from .base import BaseAdapter

logger = logging.getLogger("llmlab.adapter.deepseek")


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

        # deepseek-v4-pro runs in thinking mode at high reasoning effort. Both
        # controls travel in the request body: with the OpenAI SDK they map to
        # the top-level `reasoning_effort` kwarg and
        # `extra_body={"thinking": {"type": "enabled"}}` respectively, which is
        # exactly what these top-level JSON fields produce on the wire (this
        # adapter posts raw). Note the thinking value is the string "enabled",
        # not a boolean.
        #
        # No temperature is sent: DeepSeek thinking mode silently ignores it.
        # Production never sets temperature anyway; the only caller that does is
        # the RQ6 determinism probe (determinism_probe.py:169), which therefore
        # can no longer hold DeepSeek at temperature=0 — its reruns now reflect
        # thinking-mode default sampling.
        #
        # max_tokens is the shared budget for the reasoning trace + the final
        # JSON answer, raised from 4096 to 16384 so a long high-effort trace
        # can't truncate the decision (v4-pro supports far more, and reasoning
        # tokens are billed regardless of the cap, so the headroom is free on
        # short replies).
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 16384,
            "response_format": {"type": "json_object"},
            "reasoning_effort": "high",
            "thinking": {"type": "enabled"},
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
