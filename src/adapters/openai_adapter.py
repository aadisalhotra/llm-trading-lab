"""OpenAI / GPT adapter."""
from __future__ import annotations

import base64
import os
from typing import Any

from ..analytics.cost_rates import compute_call_cost_usd
from .base import BaseAdapter


class OpenAIAdapter(BaseAdapter):
    provider_name = "openai"
    supports_vision = True

    def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

        client = OpenAI(api_key=api_key)

        # Build user content. With images, content becomes a list of typed
        # blocks (text + image_url). Without images, we keep the simple
        # string form so older / cheaper text-only deployments still work.
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

        # Newer GPT models reject `max_tokens` and require `max_completion_tokens`.
        # The OpenAI SDK accepts the new param across all current chat models.
        extra: dict[str, Any] = {}
        if self.temperature is not None:
            extra["temperature"] = self.temperature
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=4096,
            **extra,
        )
        text = response.choices[0].message.content or ""
        returned_id = getattr(response, "model", self.model)

        # OpenAI exposes usage on every successful chat completion
        usage = getattr(response, "usage", None)
        in_tok = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        out_tok = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        cost = compute_call_cost_usd(returned_id, in_tok, out_tok)
        metadata: dict[str, Any] = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": cost,
        }

        # --- retention audit 2026-09-09 -------------------------------------
        # Everything below was already on the response and was being dropped.
        # `model` is only an alias echo, so it cannot see a provider swapping
        # the model behind a constant string — the failure mode the DeepSeek
        # 2026-09-10 substitution made concrete. Fields are diagnostics: read
        # defensively, degrade to None, never raise. The decision itself has
        # already parsed by the time we get here.
        choices = list(getattr(response, "choices", None) or [])
        first = choices[0] if choices else None
        # Build identifier. Moves when OpenAI changes backend configuration,
        # independently of the model string.
        metadata["system_fingerprint"] = getattr(response, "system_fingerprint", None)
        metadata["finish_reason"] = getattr(first, "finish_reason", None) if first else None
        # Priority/Flex tiers bill differently and cost_rates.py does not model
        # them; logging the tier is what makes that assumption checkable.
        metadata["service_tier"] = getattr(response, "service_tier", None)

        cdet = getattr(usage, "completion_tokens_details", None) if usage else None
        pdet = getattr(usage, "prompt_tokens_details", None) if usage else None
        # Reasoning tokens bill inside completion_tokens, the same semantics as
        # Gemini's thoughts_token_count, so they land on the same log field.
        metadata["thoughts_tokens"] = getattr(cdet, "reasoning_tokens", None) if cdet else None
        # Prompt-cache split. cost_rates.py prices every call at the full input
        # rate; this is what would let that conservatism be measured.
        cached = getattr(pdet, "cached_tokens", None) if pdet else None
        metadata["cache_hit_tokens"] = cached
        metadata["cache_miss_tokens"] = (
            max(0, in_tok - int(cached)) if cached is not None else None
        )
        return text, returned_id, metadata
