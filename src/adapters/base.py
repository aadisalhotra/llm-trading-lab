"""Abstract base for LLM trading adapters."""
from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("llmlab.adapter")


@dataclass
class DecisionResult:
    """Parsed decision returned by a model.

    `decisions` is a list of {action, ticker, target_weight, confidence, reasoning}
    objects exactly as the model produced them, with light validation/coercion.
    `raw_response` is preserved for debugging and decision logging.
    """
    provider: str
    model_id_configured: str
    model_id_returned: str
    decisions: list[dict[str, Any]]
    overall_reasoning: str
    raw_response: str
    latency_seconds: float
    success: bool
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseAdapter(ABC):
    """All provider adapters subclass this and implement `_call_api`."""

    provider_name: str = "base"

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    def _call_api(self, system_prompt: str, user_prompt: str) -> tuple[str, str]:
        """Make the actual provider API call.

        Returns (raw_text_response, model_id_returned_by_api).
        Must raise on failure.
        """

    def generate_decision(self, system_prompt: str, user_prompt: str) -> DecisionResult:
        """Top-level call. Times the API hit, parses JSON, returns DecisionResult.

        Never raises — failures are returned as `success=False` so one bad
        provider can't kill the daily run for the others.
        """
        start = time.perf_counter()
        try:
            raw, returned_id = self._call_api(system_prompt, user_prompt)
            latency = time.perf_counter() - start
            parsed = self._parse_response(raw)
            return DecisionResult(
                provider=self.provider_name,
                model_id_configured=self.model,
                model_id_returned=returned_id or self.model,
                decisions=parsed.get("decisions", []),
                overall_reasoning=parsed.get("overall_reasoning", ""),
                raw_response=raw,
                latency_seconds=latency,
                success=True,
            )
        except Exception as e:
            latency = time.perf_counter() - start
            logger.exception("Adapter %s failed: %s", self.provider_name, e)
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
            )

    @staticmethod
    def _parse_response(raw: str) -> dict[str, Any]:
        """Extract JSON from a model response.

        Models occasionally wrap JSON in ```json fences or include preamble.
        We strip those before json.loads. Anything that can't be parsed
        cleanly raises ValueError so the adapter records a failure.
        """
        if not raw or not raw.strip():
            raise ValueError("Empty response from model")

        # Strip code fences if present
        text = raw.strip()
        fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()
        else:
            # Find first { and last } to slice out JSON if there's preamble
            first = text.find("{")
            last = text.rfind("}")
            if first != -1 and last != -1 and last > first:
                text = text[first : last + 1]

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Model response was not valid JSON: {e}") from e

        if "decisions" not in data or not isinstance(data["decisions"], list):
            raise ValueError("Model response missing required 'decisions' list")

        # Light coercion + validation per decision
        normalized: list[dict[str, Any]] = []
        for d in data["decisions"]:
            if not isinstance(d, dict):
                continue
            action = str(d.get("action", "")).upper()
            if action not in ("BUY", "SELL", "HOLD"):
                continue
            ticker = str(d.get("ticker", "")).upper().strip()
            if not ticker:
                continue
            try:
                target_weight = float(d.get("target_weight", 0))
            except (TypeError, ValueError):
                target_weight = 0.0
            try:
                confidence = int(d.get("confidence", 5))
            except (TypeError, ValueError):
                confidence = 5
            confidence = max(1, min(10, confidence))
            normalized.append({
                "action": action,
                "ticker": ticker,
                "target_weight": max(0.0, min(1.0, target_weight)),
                "confidence": confidence,
                "reasoning": str(d.get("reasoning", "")).strip(),
            })

        return {
            "decisions": normalized,
            "overall_reasoning": str(data.get("overall_reasoning", "")).strip(),
        }
