"""OpenAI / GPT adapter."""
from __future__ import annotations

import base64
import os
from typing import Any

import logging

from ..analytics.cost_rates import compute_call_cost_usd
from . import base as _base
from .base import (API_CALL_TIMEOUT_SECONDS, FINISH_ABNORMAL, FINISH_NORMAL, FINISH_REFUSAL,
                   FINISH_TRUNCATED, BaseAdapter, raise_for_finish)

logger = logging.getLogger("llmlab.adapters.openai")

# The refreshed confirmatory arm (cohort_refresh_ruling.md rev. 2, section 5).
# gpt-6-astra cannot run without reasoning ("none" is not supported). No
# default is documented on its pages, but the API reports one: the Responses
# API returns reasoning.effort "medium" when the field is omitted (adapter
# probe 2026-11-21). Section 5 sends a reported default explicitly; the hub
# ruled "medium" explicit pending Research's confirmation. Keyed by the exact
# identifier; gpt-5.4-2026-03-05 (the Phase A snapshot) keeps its request
# byte for byte.
REFRESHED_ARMS: dict[str, dict[str, str]] = {
    "gpt-6-astra": {"reasoning_effort": "medium"},
}
# Reasoning counts against max_completion_tokens (probe: a 64-token cap ended
# on "length" with 64 reasoning tokens). Calibration runs at the provider
# maximum, 128,000 output tokens (model page saved 2026-11-21); the frozen cap,
# 16,384 plus the measured allowance, replaces it before the first validated
# session. Non-streaming: the request shape changes only if the allowance
# ladder reaches rung 3 (reasoning_allowance_limit_ruling.md).
REFRESHED_MAX_COMPLETION_TOKENS = 128_000
# The response model names each refreshed arm accepts
# (xai_probe_followup_ruling_2026-11-22.md section 2): the configured
# identifier, plus any build string the provider returns for it once recorded
# in calibration and the configuration verification. Listed by name, matched
# exactly. Names are added only by hub ruling.
ACCEPTED_NAMES: dict[str, tuple[str, ...]] = {
    "gpt-6-astra": ("gpt-6-astra",),
}
# The finish-reason rule: "stop" is the normal completion; "length" is a budget
# stop; "content_filter" (the provider withheld output under its content rule)
# and a message refusal are refusals; anything else is ABNORMAL_FINISH.
FINISH_CLASSES: dict[str, str] = {"stop": FINISH_NORMAL, "length": FINISH_TRUNCATED,
                                  "content_filter": FINISH_REFUSAL}


class OpenAIAdapter(BaseAdapter):
    provider_name = "openai"
    supports_vision = True

    @property
    def refreshed(self) -> bool:
        return self.model in REFRESHED_ARMS

    def accepted_names(self) -> tuple[str, ...]:
        return ACCEPTED_NAMES.get(self.model, ())

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

        client = OpenAI(api_key=api_key, timeout=self.transport_timeout(_base.API_CALL_TIMEOUT_SECONDS))

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
        refreshed = REFRESHED_ARMS.get(self.model)
        extra: dict[str, Any] = {}
        if self.temperature is not None:
            if refreshed:
                # Nothing is sent to a refreshed arm until Research rules
                # (hub 2026-11-21); a value other than 1 is rejected anyway.
                logger.warning("[%s] temperature %s not sent to a refreshed arm",
                               self.model, self.temperature)
            else:
                extra["temperature"] = self.temperature
        if refreshed and self.level_sent:
            extra["reasoning_effort"] = refreshed["reasoning_effort"]
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            # Budget equalization, 4096 -> 16384 (output_budget_equalization_ruling.md, 2026-11-19).
            max_completion_tokens=REFRESHED_MAX_COMPLETION_TOKENS if refreshed else 16384,
            **extra,
        )
        message = response.choices[0].message
        refusal = getattr(message, "refusal", None) if self.tick_rules else None
        text = message.content or ""
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
        if refreshed:
            metadata["reasoning_effort_sent"] = (refreshed["reasoning_effort"]
                                                 if self.level_sent else None)
            metadata["max_tokens_sent"] = REFRESHED_MAX_COMPLETION_TOKENS
            metadata["transport"] = "non-streaming"
            self.check_identity(getattr(response, "model", None), metadata)
        if self.tick_rules:
            # The finish-reason rule, before any text leaves the adapter
            # (snapshot from the handover, H-OPS-14).
            fr = metadata.get("finish_reason")
            fc = FINISH_REFUSAL if refusal else FINISH_CLASSES.get(str(fr), FINISH_ABNORMAL)
            metadata["finish_class"] = fc
            raise_for_finish(fc, f"{fc} finish: finish_reason={fr} model={returned_id}"
                                 + (f" refusal={str(refusal)[:300]}" if refusal else ""), metadata)
        # Prompt-cache split. cost_rates.py prices every call at the full input
        # rate; this is what would let that conservatism be measured.
        cached = getattr(pdet, "cached_tokens", None) if pdet else None
        metadata["cache_hit_tokens"] = cached
        metadata["cache_miss_tokens"] = (
            max(0, in_tok - int(cached)) if cached is not None else None
        )
        return text, returned_id, metadata
