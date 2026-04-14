"""DeepSeek adapter — OpenAI-compatible REST endpoint."""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

from ..analytics.cost_rates import compute_call_cost_usd
from .base import BaseAdapter, DecisionResult

logger = logging.getLogger("llmlab.adapter.deepseek")

# DeepSeek has intermittently returned empty strings and malformed JSON
# (missing commas, trailing text) that the other providers don't. A
# single targeted retry after a 15s cooldown has empirically cleared
# both failure modes in local testing. Only parse-level failures trigger
# the retry — HTTP/network errors fall through to the base class handling
# and are NOT retried, since those usually indicate rate limits or bad
# keys that a quick retry can't fix.
_RETRY_DELAY_SECONDS = 15
_MAX_ATTEMPTS = 2


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

    def generate_decision(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes] | None = None,
    ) -> DecisionResult:
        """Override the base ``generate_decision`` with a single targeted retry.

        Flow per attempt:
          1. ``_call_api`` — HTTP call. Network / HTTP errors fail fast
             (no retry) and return the same shape the base class would.
          2. ``_parse_response`` — JSON parse + repair + schema check.
             A ``ValueError`` here is our retry target: empty response,
             invalid JSON (even after ``repair_json``), or missing
             ``decisions`` list.

        On a parse failure we wait 15s and retry once. The returned
        ``DecisionResult.metadata`` always includes an ``attempt`` key so
        the decision log and runtime logger can record whether the tick
        executed on first attempt or retry.
        """
        start = time.perf_counter()
        last_error: str | None = None
        last_raw: str = ""
        last_metadata: dict[str, Any] = {}

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                raw, returned_id, call_meta = self._call_api(
                    system_prompt, user_prompt, images,
                )
            except Exception as e:
                # HTTP / network / auth failure — not the retry target.
                latency = time.perf_counter() - start
                logger.exception("deepseek call failed (attempt %d): %s", attempt, e)
                return DecisionResult(
                    provider=self.provider_name,
                    model_id_configured=self.model,
                    model_id_returned=self.model,
                    decisions=[],
                    overall_reasoning="",
                    raw_response="",
                    latency_seconds=latency,
                    success=False,
                    error=str(e),
                    metadata={"attempt": attempt},
                )

            last_raw = raw
            last_metadata = call_meta or {}

            try:
                parsed = self._parse_response(raw)
            except ValueError as e:
                last_error = str(e)
                if attempt < _MAX_ATTEMPTS:
                    logger.warning(
                        "deepseek parse failure on attempt %d/%d: %s "
                        "— retrying in %ds",
                        attempt, _MAX_ATTEMPTS, e, _RETRY_DELAY_SECONDS,
                    )
                    time.sleep(_RETRY_DELAY_SECONDS)
                    continue
                # Final attempt — surface the failure
                latency = time.perf_counter() - start
                logger.error(
                    "deepseek parse failure on final attempt %d/%d: %s",
                    attempt, _MAX_ATTEMPTS, e,
                )
                return DecisionResult(
                    provider=self.provider_name,
                    model_id_configured=self.model,
                    model_id_returned=self.model,
                    decisions=[],
                    overall_reasoning="",
                    raw_response=raw,
                    latency_seconds=latency,
                    success=False,
                    error=last_error,
                    metadata={**last_metadata, "attempt": attempt},
                )

            # Success path
            latency = time.perf_counter() - start
            if attempt > 1:
                logger.info(
                    "deepseek recovered on retry (attempt %d/%d)",
                    attempt, _MAX_ATTEMPTS,
                )
            return DecisionResult(
                provider=self.provider_name,
                model_id_configured=self.model,
                model_id_returned=returned_id or self.model,
                decisions=parsed.get("decisions", []),
                overall_reasoning=parsed.get("overall_reasoning", ""),
                raw_response=raw,
                latency_seconds=latency,
                success=True,
                metadata={**last_metadata, "attempt": attempt},
            )

        # Loop exit without return — defensive only
        latency = time.perf_counter() - start
        return DecisionResult(
            provider=self.provider_name,
            model_id_configured=self.model,
            model_id_returned=self.model,
            decisions=[],
            overall_reasoning="",
            raw_response=last_raw,
            latency_seconds=latency,
            success=False,
            error=last_error or "deepseek: retries exhausted",
            metadata={**last_metadata, "attempt": _MAX_ATTEMPTS},
        )
