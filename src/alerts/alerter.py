"""Alert dispatch — the stable interface the pipeline calls.

Two entry points, unchanged in signature so existing call sites keep working:

  - `send_alert(severity, title, body, context)` — any non-routine event.
    Logs to /logs/alerts.jsonl + the pipeline log (as before), then routes
    to the email layer via `events.dispatch_event`, which applies de-dup, the
    per-day cap, and overflow-into-digest. WARN/CRITICAL email by default;
    INFO is log-only unless `email=True` is passed.

  - `send_daily_summary(summary)` — the EOD digest trigger. Logs the summary,
    runs the end-of-day detection sweep (milestones, ATH, negative crossings,
    oversized trades, news impact, state anomalies, missed runs), then sends
    the once-per-day HTML digest (which bundles any capped overflow alerts).

Email failures never propagate: the whole email path is wrapped so a flaky
SMTP connection can't crash a trading tick.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from ..config_loader import LOGS_DIR

logger = logging.getLogger("llmlab.alerts")

ALERT_LOG = LOGS_DIR / "alerts.jsonl"


def _persist(record: dict[str, Any]) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(ALERT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


# Fallback classifier for the machine `kind` tag when a caller doesn't pass
# one explicitly. Keeps the email layer's grouping/labels sensible even for
# ad-hoc alerts.
def _infer_kind(title: str) -> str:
    t = (title or "").lower()
    if "position stop" in t:
        return "position_stop"
    if "portfolio stop" in t or "halt" in t:
        return "portfolio_halt"
    if "market data" in t:
        return "market_data_failure"
    if "unhandled error" in t:
        return "pipeline_error"
    if "dashboard" in t:
        return "dashboard_failure"
    if "report" in t:
        return "report_failure"
    if "budget" in t:
        return "budget"
    if "transition" in t:
        return "model_transition"
    if "fail" in t or "api" in t:
        return "api_failure"
    return "event"


def send_alert(
    severity: str,
    title: str,
    body: str,
    context: dict[str, Any] | None = None,
    *,
    kind: str | None = None,
    dedup_key: str | None = None,
    email: bool | None = None,
) -> None:
    """Single point of dispatch for any non-routine event.

    severity: INFO | WARN | CRITICAL
    """
    record = {
        "timestamp": datetime.utcnow().isoformat(),
        "severity": severity,
        "title": title,
        "body": body,
        "context": context or {},
    }
    _persist(record)
    logger.log(
        {"INFO": logging.INFO, "WARN": logging.WARNING, "CRITICAL": logging.ERROR}.get(severity, logging.INFO),
        "ALERT [%s] %s — %s", severity, title, body,
    )
    try:
        from .events import dispatch_event
        dispatch_event(
            kind=kind or _infer_kind(title),
            severity=severity,
            title=title,
            body=body,
            context=context or {},
            dedup_key=dedup_key,
            email=email,
        )
    except Exception:
        logger.exception("Alert email dispatch failed for: %s", title)


def send_daily_summary(summary: dict[str, Any]) -> None:
    """End-of-run digest with per-model results + leaderboard.

    Records the summary, runs the EOD event-detection sweep, then sends the
    once-per-day HTML digest email.
    """
    record = {
        "timestamp": datetime.utcnow().isoformat(),
        "type": "DAILY_SUMMARY",
        "summary": summary,
    }
    _persist(record)
    logger.info("DAILY SUMMARY recorded for %s", summary.get("date"))

    # Detection sweep first so any capped events are queued before the digest
    # renders (the digest bundles the overflow).
    try:
        from .events import run_eod_alert_sweep
        run_eod_alert_sweep(summary)
    except Exception:
        logger.exception("EOD alert sweep failed")

    try:
        from .digest import send_daily_digest
        send_daily_digest(summary)
    except Exception:
        logger.exception("Daily digest send failed")
