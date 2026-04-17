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


def repair_json(text: str) -> str:
    """Best-effort fix for common JSON defects in LLM output.

    Not a general JSON fixer — just enough to recover from the failure
    modes we've actually seen in real model responses:
      * Python literals (``True`` / ``False`` / ``None``)
      * Trailing commas before ``}`` or ``]``
      * Missing commas between adjacent ``}{`` / ``][`` / ``}[`` / ``]{``
      * Unclosed brackets at the end (model got cut off mid-output)

    Returns the repaired text. Unchanged if no repair heuristics fired.
    The caller should re-attempt ``json.loads`` on the result — if the
    repair missed, fall through to the usual error path.
    """
    if not text:
        return text

    # Python literal coercion (word-bounded so we don't clobber keys like
    # "isTrueStrength" or substrings inside words).
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)

    # Balance brackets FIRST by walking the text once, counting opens vs
    # closes outside of string literals. If the model was cut off
    # mid-output the closer counts will be short; append whatever's
    # missing before we run the regex passes. Doing this first lets the
    # trailing-comma pass also clean up the dangling comma that typically
    # sits right before where the model stopped.
    open_curly = 0
    open_square = 0
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            open_curly += 1
        elif ch == "}":
            open_curly -= 1
        elif ch == "[":
            open_square += 1
        elif ch == "]":
            open_square -= 1

    if open_square > 0:
        text += "]" * open_square
    if open_curly > 0:
        text += "}" * open_curly

    # Trailing commas before a closer: ``{"a": 1,}`` → ``{"a": 1}``
    # Also cleans up the ``,}`` / ``,]`` pairs that the bracket
    # balancer just produced on cut-off responses.
    text = re.sub(r",(\s*[}\]])", r"\1", text)

    # Missing commas between adjacent objects/arrays. LLMs sometimes
    # produce ``[{"x":1} {"y":2}]`` or ``{"a":1}{"b":2}`` — insert the
    # comma so json.loads can proceed.
    text = re.sub(r"(\})(\s*)(\{)", r"\1,\2\3", text)
    text = re.sub(r"(\])(\s*)(\[)", r"\1,\2\3", text)
    text = re.sub(r"(\})(\s*)(\[)", r"\1,\2\3", text)
    text = re.sub(r"(\])(\s*)(\{)", r"\1,\2\3", text)

    return text


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
    """All provider adapters subclass this and implement `_call_api`.

    Vision-capable adapters override `supports_vision = True` and accept an
    `images` argument in `_call_api`. Text-only adapters ignore the images
    list — the caller can blindly pass images to every adapter without
    branching on capability.
    """

    provider_name: str = "base"
    supports_vision: bool = False

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        """Make the actual provider API call.

        `images` is an optional list of raw PNG bytes. Adapters that support
        vision should attach them to the user message in their provider's
        native format. Text-only adapters should ignore the argument.

        Returns (raw_text_response, model_id_returned_by_api, metadata).
        `metadata` is a free-form dict where adapters can stash usage info
        (input_tokens, output_tokens, cost_usd, etc.) for the decision log.
        Must raise on failure.
        """

    def generate_raw(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, float, dict[str, Any]]:
        """Call the model and return (raw_text, latency_seconds, metadata).

        No JSON parsing — use this for prompts (like screening) whose
        response schema differs from the trading-decision format.
        """
        start = time.perf_counter()
        raw, _returned_id, metadata = self._call_api(system_prompt, user_prompt)
        latency = time.perf_counter() - start
        return raw, latency, metadata or {}

    def generate_decision(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes] | None = None,
    ) -> DecisionResult:
        """Top-level call. Times the API hit, parses JSON, returns DecisionResult.

        Never raises — failures are returned as `success=False` so one bad
        provider can't kill the daily run for the others.
        """
        start = time.perf_counter()
        try:
            raw, returned_id, metadata = self._call_api(system_prompt, user_prompt, images)
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
                metadata=metadata or {},
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
            # Last-ditch repair for common LLM defects (trailing commas,
            # Python literals, missing commas between adjacent objects,
            # unclosed brackets). If the repair changes the text AND the
            # repaired version parses, we recover silently. Otherwise we
            # raise the original error so the adapter records a failure.
            repaired = repair_json(text)
            if repaired != text:
                try:
                    data = json.loads(repaired)
                    logger.info("parse: JSON repair succeeded (original: %s)", e)
                except json.JSONDecodeError as e2:
                    raise ValueError(
                        f"Model response was not valid JSON even after repair: {e2}"
                    ) from e2
            else:
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
            # `summary` is the new one-sentence plain-English explanation
            # that surfaces in the dashboard trade feed and report breakdown.
            # Models that don't produce one (older prompt versions, retried
            # calls, etc.) get a defaulted blank — the rest of the pipeline
            # tolerates an empty summary.
            summary = str(d.get("summary", "")).strip()
            normalized.append({
                "action": action,
                "ticker": ticker,
                "target_weight": max(0.0, min(1.0, target_weight)),
                "confidence": confidence,
                "summary": summary,
                "reasoning": str(d.get("reasoning", "")).strip(),
            })

        return {
            "decisions": normalized,
            "overall_reasoning": str(data.get("overall_reasoning", "")).strip(),
        }
