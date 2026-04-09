"""Alert dispatch.

Right now alerts are logged + written to /logs/alerts.jsonl. Email/SMS hooks
are stubbed at the boundary so plugging in SMTP later is a single function
swap. The pipeline calls send_alert() and send_daily_summary() — those are
the stable interfaces.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from ..config_loader import LOGS_DIR

logger = logging.getLogger("llmlab.alerts")

ALERT_LOG = LOGS_DIR / "alerts.jsonl"


def _persist(record: dict[str, Any]) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(ALERT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def send_alert(severity: str, title: str, body: str, context: dict[str, Any] | None = None) -> None:
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
    _maybe_email(record)


def send_daily_summary(summary: dict[str, Any]) -> None:
    """End-of-run digest with per-model results + leaderboard."""
    record = {
        "timestamp": datetime.utcnow().isoformat(),
        "type": "DAILY_SUMMARY",
        "summary": summary,
    }
    _persist(record)
    logger.info("DAILY SUMMARY recorded for %s", summary.get("date"))
    _maybe_email(record)


def _maybe_email(record: dict[str, Any]) -> None:
    """Email hook — only fires if SMTP config is present.

    This is the boundary to swap in real email later. For now it's a no-op
    when SMTP_HOST is unset, which is the default.
    """
    smtp_host = os.getenv("SMTP_HOST")
    if not smtp_host:
        return
    # Stub: real implementation goes here when alerts are turned on.
    # Intentionally left as a logged no-op so the call surface is real but inert.
    logger.debug("Email hook fired (no-op until SMTP wiring is enabled): %s", record.get("title", record.get("type")))
