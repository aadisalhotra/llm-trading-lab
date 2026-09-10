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
        # Indexed, not .get() — a response missing this path is malformed, and
        # the resulting KeyError is what feeds BaseAdapter's parse-level retry.
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

        # --- provider-substitution telemetry (added 2026-09-09) -------------
        # `returned_id` is only the alias echo, so detect_version_transition is
        # blind to a swap of the model BEHIND a constant alias — which is
        # exactly what the 2026-09-10T04:00Z V4-Pro -> V4.1-Flash forced
        # substitution is. Everything below was already on the wire and was
        # being discarded; a 2026-09-10T01:13Z pre-boundary probe confirmed all
        # four fields are populated on a live deepseek-v4-pro response.
        #
        # Read defensively (the fields are not contractual) — a missing one
        # must degrade to None, never raise, because these are diagnostics and
        # the decision itself already parsed.
        choice = (data.get("choices") or [{}])[0] or {}
        # The build identifier. Constant a307abda487cd1b463329ccb945ce396 across
        # the pre-boundary probe; a change here is the sharpest evidence of a
        # model swap under an unchanged alias, and unlike cost it does not
        # depend on our own rate table.
        metadata["system_fingerprint"] = data.get("system_fingerprint")
        # STOP vs a length cutoff — same role the Gemini adapter's finish_reason
        # plays for the completeness gates.
        metadata["finish_reason"] = choice.get("finish_reason")
        # Reasoning-trace tokens. Billed inside completion_tokens and counted
        # against max_tokens, the same semantics as Gemini's thoughts_tokens,
        # so it lands on the existing log field. ~84-86% of completion tokens
        # on the pre-boundary probe — a reasoning-tier change should move it.
        details = usage.get("completion_tokens_details") or {}
        metadata["thoughts_tokens"] = details.get("reasoning_tokens")
        # Prompt-cache split. cost_rates.py prices every call at the cache-MISS
        # rate (the conservative upper bound); logging the split is what will
        # eventually let that assumption be measured instead of assumed.
        metadata["cache_hit_tokens"] = usage.get("prompt_cache_hit_tokens")
        metadata["cache_miss_tokens"] = usage.get("prompt_cache_miss_tokens")

        return text, returned_id, metadata
