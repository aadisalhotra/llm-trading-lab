"""Startup assertion for the alerting configuration.

The defect this exists to prevent, in one line: **wired code, absent secret,
silent skip.**

On 2026-08-14 the recipient list was moved out of the committed `settings.json`
and into the `ALERT_RECIPIENTS` repository secret. The code shipped; the secret
did not exist yet. `send_email()` is designed never to crash a trading tick, so
every send would have skipped with a logged warning inside an otherwise green
workflow run — the alerting channel off, and nothing anywhere saying so. The
same hole existed for `COMPETITOR_ALERT_TO`.

The circularity is what makes this class dangerous: the mechanism that would
normally tell you an alert failed *is the alert channel*. A missing recipient
list disables its own failure notification. So the signal has to be something
that does not depend on email at all — a non-zero exit that reds the CI run.

Design constraints, both load-bearing:

  * **A missing recipient must never stop trading.** The experiment is the
    point; email is instrumentation. `check()` therefore only reports, and the
    trading pipeline logs at CRITICAL and carries on. The loud failure lives in
    a *separate* CI job whose failure cannot break the `run` -> `chain`
    self-chain (`chain` gates on `success()` of `run`).
  * **A machine with no email configured is not misconfigured.** A developer
    checkout with no `GMAIL_ADDRESS` has deliberately not wired alerting, and
    must not fail. "Wired" means the transport credentials are present — that
    is the signal that somebody intended mail to work.

Channels are declared by the caller rather than sniffed from the environment.
The intraday workflow passes `ALERT_RECIPIENTS` and not `COMPETITOR_ALERT_TO`;
the competitor workflow does the reverse. Sniffing could not tell "this channel
is not used here" apart from "this channel's secret is missing", which is
precisely the distinction that matters.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .email_alerts import get_recipients
from .competitor_escalation import get_escalation_recipients

logger = logging.getLogger("llmlab.alerts.preflight")

DAILY = "daily_alerts"
COMPETITOR = "competitor_escalation"
ALL_CHANNELS = (DAILY, COMPETITOR)

# channel -> (env var holding the recipients, resolver, human description)
_CHANNELS: dict[str, tuple[str, Any, str]] = {
    DAILY: (
        "ALERT_RECIPIENTS",
        get_recipients,
        "daily digest, event alerts, staleness monitor, failure notifier",
    ),
    COMPETITOR: (
        "COMPETITOR_ALERT_TO",
        get_escalation_recipients,
        "competitor ESCALATE-tier alerts",
    ),
}


class AlertingConfigError(RuntimeError):
    """An alerting channel is wired but cannot deliver."""


def transport_is_wired() -> bool:
    """True when Gmail SMTP credentials are present.

    This is the "somebody intended mail to work here" test. With no credentials
    the whole email path is a deliberate no-op and an empty recipient list is
    not a defect.
    """
    return bool((os.getenv("GMAIL_ADDRESS") or "").strip()
                and (os.getenv("GMAIL_APP_PASSWORD") or "").strip())


def check(channels: tuple[str, ...] = ALL_CHANNELS,
          settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return one problem dict per wired-but-undeliverable channel.

    An empty list means every requested channel can deliver, or that the
    transport is not wired at all (in which case nothing is claimed).
    """
    for ch in channels:
        if ch not in _CHANNELS:
            raise ValueError(f"unknown alerting channel: {ch!r}")

    if not transport_is_wired():
        logger.info(
            "alerting preflight: GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set — email "
            "is deliberately off here; recipient checks skipped")
        return []

    # `alerts.enabled: false` is a deliberate switch-off, not a misconfiguration.
    if settings is not None and not (settings.get("alerts", {}) or {}).get("enabled", True):
        logger.info("alerting preflight: alerts.enabled is false — recipient checks skipped")
        return []

    problems: list[dict[str, Any]] = []
    for ch in channels:
        env_var, resolver, purpose = _CHANNELS[ch]
        try:
            recipients = resolver()
        except Exception as e:  # noqa: BLE001 — a resolver blowing up is itself a problem
            problems.append({
                "channel": ch, "env_var": env_var, "purpose": purpose,
                "reason": f"recipient resolver raised: {e}",
            })
            continue
        if not recipients:
            problems.append({
                "channel": ch, "env_var": env_var, "purpose": purpose,
                "reason": (
                    f"{env_var} is unset or empty while Gmail credentials ARE set. "
                    f"Every {purpose} send will be skipped with a logged warning and "
                    f"no other symptom. Set the {env_var} repository secret "
                    f"(comma-separated addresses)."
                ),
            })
    return problems


def assert_configured(channels: tuple[str, ...] = ALL_CHANNELS,
                      settings: dict[str, Any] | None = None,
                      strict: bool = False) -> list[dict[str, Any]]:
    """Check, log at CRITICAL, and optionally raise.

    `strict=False` (the trading pipeline) logs and returns — a missing recipient
    list must not stop the experiment. `strict=True` (the competitor monitor,
    the CI preflight) raises, because a scan that cannot alert has no reason to
    run and there is no trading at stake.
    """
    problems = check(channels, settings)
    for p in problems:
        logger.critical("ALERTING MISCONFIGURED [%s]: %s", p["channel"], p["reason"])
    if problems and strict:
        raise AlertingConfigError(
            "; ".join(f"{p['env_var']} missing ({p['purpose']})" for p in problems))
    return problems
