"""Immediate CRITICAL on an ACCOUNT_STATE failure (rossi_review_rulings.md section 2.4).

Rule 4 of the registration: the class is preventable, so any nonzero count is an
operational incident, and the alarm exists from the cutover. This module is the
alarm. It is the ONLY reader of `error_class` outside the decision log writer,
and it only sends an alert: the pipeline calls it unconditionally in its
failed-call branch (src/pipeline.py, `if not decision_result.success`), which
runs before any order exists, and the pipeline's flow is the same whatever this
function decides. It never raises into the caller.

One CRITICAL per book per cycle: "any occurrence raises" (hub, 2026-11-16), so
the dedup key carries the cycle id rather than the day.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

PROVIDER_NAMES = {
    "deepseek": "DeepSeek",
    "xai": "xAI",
    "google": "Google (Gemini)",
    "anthropic": "Anthropic",
    "openai": "OpenAI",
}


def alert_if_account_state(model_key: str, decision_result: Any, cycle_id: str,
                           send_alert_fn: Callable[..., Any] | None = None) -> bool:
    """Send the CRITICAL if `decision_result` failed with ACCOUNT_STATE.
    Returns whether an alert was sent. Never raises."""
    try:
        from ..adapters.base import ERROR_CLASS_ACCOUNT_STATE
        if getattr(decision_result, "error_class", None) != ERROR_CLASS_ACCOUNT_STATE:
            return False
        provider = getattr(decision_result, "provider", "") or ""
        name = PROVIDER_NAMES.get(provider, provider or "unknown provider")
        reason = getattr(decision_result, "error_reason", None) or "unspecified"
        fix_hint = {"billing": "fund the account (balance, credits or spending limit)",
                    "quota": "the per-day request quota is exhausted: tier, pacing or "
                             "retry policy"}.get(reason, "check the account")
        if send_alert_fn is None:
            from .alerter import send_alert as send_alert_fn
        send_alert_fn(
            "CRITICAL",
            f"ACCOUNT_STATE ({reason}): {model_key} — {name} refused the call",
            (f"Book {model_key}: {name} rejected the decision call on the lab's own "
             f"account standing, not provider availability. Reason: {reason} -- "
             f"{fix_hint}. This book's leg is missing until it is fixed. "
             f"Cycle {cycle_id}. Provider error: {(decision_result.error or '')[:300]}"),
            {"model": model_key, "provider": provider, "cycle_id": cycle_id,
             "reason": reason},
            kind="account_state",
            dedup_key=f"account_state:{model_key}:{cycle_id}",
        )
        return True
    except Exception:  # noqa: BLE001 — an alarm must never break the tick
        logger.exception("[%s] ACCOUNT_STATE alert could not be sent", model_key)
        return False
