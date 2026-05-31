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

# All adapters inherit a single targeted retry on parse-level failure.
# Empirically the "model returned invalid JSON / empty response" failure
# mode clears on a short cooldown across every provider we've seen it on
# (DeepSeek, Gemini). HTTP/network/auth errors are NOT retried — those
# usually indicate rate limits or bad keys that a quick retry can't fix,
# and retrying risks a second billing event for the same failure.
_RETRY_DELAY_SECONDS = 15
_MAX_ATTEMPTS = 2


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


def _str_or_none(value: Any) -> str | None:
    """Coerce a value to a stripped string, or None when missing/blank.

    Used for the v2 nullable reasoning fields (`cash_rationale`,
    `reversal_justification`, `no_trade_reason`): a model that omits the field,
    sends JSON null, or sends an empty string all collapse to None so downstream
    "field present?" checks are unambiguous.
    """
    if value is None:
        return None
    s = str(value).strip()
    return s or None


@dataclass
class DecisionResult:
    """Parsed decision returned by a model.

    `decisions` is a list of {action, ticker, target_weight, confidence, summary,
    reasoning, confidence_justification, why_now, reversal_justification} objects
    exactly as the model produced them, with light validation/coercion.
    `raw_response` is preserved for debugging and decision logging.

    The v2 prompt adds three period-level reasoning fields alongside
    `overall_reasoning`: `cash_rationale`, `position_reviews`, and
    `no_trade_reason`. They are None/[] under v1 (the model never emits them).
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
    # v2 period-level reasoning fields (additive; None/[] under v1).
    cash_rationale: str | None = None
    position_reviews: list[dict[str, Any]] = field(default_factory=list)
    no_trade_reason: str | None = None


class BaseAdapter(ABC):
    """All provider adapters subclass this and implement `_call_api`.

    Vision-capable adapters override `supports_vision = True` and accept an
    `images` argument in `_call_api`. Text-only adapters ignore the images
    list — the caller can blindly pass images to every adapter without
    branching on capability.
    """

    provider_name: str = "base"
    supports_vision: bool = False

    def __init__(self, model: str, temperature: float | None = None):
        self.model = model
        # Optional sampling temperature. ``None`` (the default for the live
        # pipeline) leaves the provider default untouched — adapters omit the
        # parameter entirely. The determinism probe (RQ6) sets this to 0.0 to
        # request greedy decoding for its reruns. Only the probe ever sets it,
        # so the production decision path is unchanged.
        self.temperature = temperature

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

        Retry policy: HTTP / network / auth errors from ``_call_api`` fail
        fast (no retry) — those usually need human intervention, and a
        second call risks a duplicate billing event. ``ValueError`` from
        ``_parse_response`` (empty response, invalid JSON even after
        ``repair_json``, or a response missing the ``decisions`` list)
        triggers ONE retry after ``_RETRY_DELAY_SECONDS``. Every returned
        ``DecisionResult.metadata`` includes an ``attempt`` key (1 or 2)
        so the pipeline log can surface retry activity.
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
                logger.exception(
                    "%s call failed (attempt %d): %s",
                    self.provider_name, attempt, e,
                )
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
                        "%s parse failure on attempt %d/%d: %s — retrying in %ds",
                        self.provider_name, attempt, _MAX_ATTEMPTS, e,
                        _RETRY_DELAY_SECONDS,
                    )
                    time.sleep(_RETRY_DELAY_SECONDS)
                    continue
                latency = time.perf_counter() - start
                logger.error(
                    "%s parse failure on final attempt %d/%d: %s",
                    self.provider_name, attempt, _MAX_ATTEMPTS, e,
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

            latency = time.perf_counter() - start
            if attempt > 1:
                logger.info(
                    "%s recovered on retry (attempt %d/%d)",
                    self.provider_name, attempt, _MAX_ATTEMPTS,
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
                cash_rationale=parsed.get("cash_rationale"),
                position_reviews=parsed.get("position_reviews", []),
                no_trade_reason=parsed.get("no_trade_reason"),
            )

        # Defensive fallthrough — the loop always returns above.
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
            error=last_error or "retries exhausted",
            metadata={**last_metadata, "attempt": _MAX_ATTEMPTS},
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
                # v2 per-trade reasoning fields. Default blank/None so a v1-shaped
                # decision (or a model that omits them) still normalizes cleanly.
                "confidence_justification": str(d.get("confidence_justification", "")).strip(),
                "why_now": str(d.get("why_now", "")).strip(),
                "reversal_justification": _str_or_none(d.get("reversal_justification")),
            })

        # v2 period-level position reviews — one per open position down >=5%.
        # Normalize ticker/status casing; keep any entry that names a ticker.
        position_reviews: list[dict[str, Any]] = []
        raw_reviews = data.get("position_reviews")
        if isinstance(raw_reviews, list):
            for r in raw_reviews:
                if not isinstance(r, dict):
                    continue
                rt = str(r.get("ticker", "")).upper().strip()
                if not rt:
                    continue
                position_reviews.append({
                    "ticker": rt,
                    "thesis_status": str(r.get("thesis_status", "")).strip().lower(),
                    "implication": str(r.get("implication", "")).strip(),
                })

        return {
            "decisions": normalized,
            "overall_reasoning": str(data.get("overall_reasoning", "")).strip(),
            # v2 period-level reasoning fields (absent under v1 -> None/[]).
            "cash_rationale": _str_or_none(data.get("cash_rationale")),
            "position_reviews": position_reviews,
            "no_trade_reason": _str_or_none(data.get("no_trade_reason")),
        }
