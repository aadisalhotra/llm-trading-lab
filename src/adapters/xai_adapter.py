"""xAI / Grok adapter — uses the OpenAI-compatible REST endpoint."""
from __future__ import annotations

import base64
import os
from typing import Any

import logging

import requests

from ..analytics.cost_rates import compute_call_cost_usd
from .base import (API_CALL_TIMEOUT_SECONDS, FINISH_ABNORMAL, FINISH_NORMAL, FINISH_REFUSAL,
                   FINISH_TRUNCATED, BaseAdapter, raise_for_finish)

logger = logging.getLogger("llmlab.adapters.xai")

# The refreshed confirmatory arm (cohort_refresh_ruling.md rev. 2, section 5).
# grok-4.7's documented default is "high" ("If not specified, reasoning_effort
# defaults to 'high'. Reasoning cannot be disabled." -- xai_reasoning.html,
# saved 2026-11-21), sent explicitly. Keyed by the exact identifier;
# grok-4.20-0309-reasoning (the Phase A snapshot) keeps its request byte for
# byte. Chat Completions works for grok-4.7 (adapter probe 2026-11-21).
REFRESHED_ARMS: dict[str, dict[str, str]] = {
    "grok-4.7": {"reasoning_effort": "high"},
}
# Reasoning does NOT count against max_tokens on grok-4.7 (probe: 9,432
# reasoning tokens under a 64-token cap, finish stop), and the provider states
# no text output limit. max_tokens therefore bounds only the answer, and stays
# the registered answer budget (Tier 1, T1.4) in calibration and after; the
# allowance rule has no cap to set for this arm.
REFRESHED_MAX_TOKENS = 16384
# The finish-reason rule (OpenAI-compatible values): "stop" is normal; "length"
# is a budget stop; "content_filter" and a message refusal are refusals;
# anything else is ABNORMAL_FINISH.
FINISH_CLASSES: dict[str, str] = {"stop": FINISH_NORMAL, "length": FINISH_TRUNCATED,
                                  "content_filter": FINISH_REFUSAL}


class XAIAdapter(BaseAdapter):
    provider_name = "xai"
    supports_vision = True   # grok-4 accepts image_url content blocks
    BASE_URL = "https://api.x.ai/v1/chat/completions"

    @property
    def refreshed(self) -> bool:
        return self.model in REFRESHED_ARMS

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
            # Budget equalization, 4096 -> 16384 (output_budget_equalization_ruling.md, 2026-11-19).
            "max_tokens": 16384,
            "response_format": {"type": "json_object"},
        }
        refreshed = REFRESHED_ARMS.get(self.model)
        if self.temperature is not None:
            if refreshed:
                # Nothing is sent to a refreshed arm until Research rules
                # (hub 2026-11-21).
                logger.warning("[%s] temperature %s not sent to a refreshed arm",
                               self.model, self.temperature)
            else:
                payload["temperature"] = self.temperature
        if refreshed:
            payload["reasoning_effort"] = refreshed["reasoning_effort"]
            payload["max_tokens"] = REFRESHED_MAX_TOKENS
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # Raised from 120s (Hub registration, 2026-09-17) -- four of grok's
        # own historical failures are an exact "read timeout=120" ceiling,
        # including a same-day 2026-09-17 occurrence, i.e. this was already
        # clipping real in-flight calls, not just a defensive bound.
        r = requests.post(self.BASE_URL, json=payload, headers=headers,
                          timeout=self.transport_timeout(API_CALL_TIMEOUT_SECONDS))
        if not r.ok:
            raise RuntimeError(f"xAI API {r.status_code}: {r.text[:500]}")
        data = r.json()
        message = data["choices"][0]["message"]
        refusal = message.get("refusal") if refreshed else None
        text = message["content"]
        returned_id = data.get("model", self.model)

        usage = data.get("usage", {}) or {}
        in_tok = int(usage.get("prompt_tokens", 0) or 0)
        out_tok = int(usage.get("completion_tokens", 0) or 0)
        reasoning_tok = int(((usage.get("completion_tokens_details") or {})
                             .get("reasoning_tokens", 0)) or 0)
        # grok-4.7 reports reasoning OUTSIDE completion_tokens (total_tokens =
        # prompt + completion + reasoning) and bills it as output, so its cost
        # prices both. The snapshot's cost line is left as it was.
        cost = compute_call_cost_usd(returned_id, in_tok,
                                     out_tok + (reasoning_tok if refreshed else 0))
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

        # --- search provenance (logging package 2026-11-15) -----------------
        # The adapter sends no search tools, so Grok as deployed has no live
        # X/web access (xAI: "no access to realtime events without search tools
        # enabled"; a live call returned num_sources_used 0 and no citations).
        # Nothing recorded that, so the claim could not be checked from the
        # log. These make it checkable per call. Raw values, None when absent:
        # 0 means xAI reported zero sources, None means it reported nothing.
        metadata["num_sources_used"] = usage.get("num_sources_used")
        if refreshed:
            metadata["reasoning_effort_sent"] = refreshed["reasoning_effort"]
            metadata["max_tokens_sent"] = REFRESHED_MAX_TOKENS
            metadata["transport"] = "non-streaming"
            # xAI's own figure, in 1e-10 USD ticks, beside the modelled cost.
            ticks = usage.get("cost_in_usd_ticks")
            metadata["provider_cost_usd"] = (ticks / 1e10) if isinstance(ticks, (int, float)) else None
            # The finish-reason rule, before any text leaves the adapter.
            fr = metadata.get("finish_reason")
            fc = FINISH_REFUSAL if refusal else FINISH_CLASSES.get(str(fr), FINISH_ABNORMAL)
            metadata["finish_class"] = fc
            raise_for_finish(fc, f"{fc} finish: finish_reason={fr} model={returned_id}"
                                 + (f" refusal={str(refusal)[:300]}" if refusal else ""), metadata)
        metadata["citations"] = data.get("citations")
        return text, returned_id, metadata
