"""Escalation alerting for the competitor paper monitor.

Fires an active email ONLY on ESCALATE-tier hits from the weekly scan. Never
on heartbeats, never on digest-tier or silent-tier rows — an alert channel that
fires on routine output stops being read, which is the same failure as a
channel nobody reads at all.

**Why email and not a GitHub Issue.** Both mechanisms were assessed. Opening an
Issue is less code and gets notification for free, but this repository is
public: the Issue title, the paper, and the two-line overlap assessment naming
the threatened RQ(s) would all be world-readable. That publishes, ahead of our
own deposit, exactly which work the lab regards as a threat and which research
questions it is worried about — pre-publication research strategy, in the open,
attached to a competitor's paper. Email keeps the assessment private, and the
Gmail SMTP transport in this package has been carrying the daily digest and
event alerts in production since 2026-05-20, so it is also the *less* new
machinery of the two.

**Recipient handling — public-repo boundary.** The address comes from the
COMPETITOR_ALERT_TO environment variable (a repository secret in CI), NOT from
config/settings.json. settings.json is committed, so anything in it is public.
With no recipient configured the send is skipped and logged rather than
falling back to a committed list.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from .email_alerts import send_email

logger = logging.getLogger("llmlab.alerts.competitor_escalation")

TRIGGER = "competitor_escalation"


def get_escalation_recipients() -> list[str]:
    """Recipients from the secret-backed env var only.

    Comma-separated. Deliberately does NOT fall back to
    settings.alert_recipients: that list is committed to a public repo, and a
    silent fallback would defeat the point of sourcing this from a secret.
    """
    raw = (os.getenv("COMPETITOR_ALERT_TO") or "").strip()
    return [a.strip() for a in raw.split(",") if a.strip()]


def _rows_html(escalations: list[dict[str, Any]]) -> str:
    blocks = []
    for e in escalations:
        rqs = ", ".join(e.get("threatened_rqs") or []) or "none named — read before ruling"
        crit = "; ".join(e.get("triage_criteria") or [])
        blocks.append(
            "<div style='margin:0 0 22px 0;padding:14px 16px;border-left:3px solid #b3261e;"
            "background:#faf7f7'>"
            f"<div style='font-size:15px;font-weight:600;margin-bottom:8px'>{_esc(e.get('title'))}</div>"
            f"<div style='font-size:13px;line-height:1.7'>"
            f"<b>Link:</b> <a href='{_esc(e.get('url'))}'>{_esc(e.get('url'))}</a><br>"
            f"<b>Venue:</b> {_esc(e.get('venue'))} &nbsp;·&nbsp; <b>Date:</b> {_esc(e.get('date'))}<br>"
            f"<b>Criteria met:</b> {_esc(crit)}<br>"
            f"<b>Threatened RQ(s):</b> {_esc(rqs)}<br>"
            f"<b>Assessment:</b> {_esc(e.get('assessment'))}"
            "</div></div>"
        )
    return "".join(blocks)


def _rows_text(escalations: list[dict[str, Any]]) -> str:
    out = []
    for e in escalations:
        rqs = ", ".join(e.get("threatened_rqs") or []) or "none named — read before ruling"
        out.append(
            f"{e.get('title')}\n"
            f"  Link      : {e.get('url')}\n"
            f"  Venue     : {e.get('venue')}   Date: {e.get('date')}\n"
            f"  Criteria  : {'; '.join(e.get('triage_criteria') or [])}\n"
            f"  Threatens : {rqs}\n"
            f"  Assessment: {e.get('assessment')}\n"
        )
    return "\n".join(out)


def _esc(v: Any) -> str:
    s = "" if v is None else str(v)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def send_escalation_alert(escalations: list[dict[str, Any]],
                          week_tag: str,
                          generated_at: datetime,
                          *,
                          is_test: bool = False) -> bool:
    """Email the ESCALATE-tier hits. Returns True only if a mail was sent.

    No escalations -> no mail, and that is the normal weekly outcome. The
    heartbeat covers "the monitor ran and found nothing"; this channel exists
    solely so a genuine threat does not wait for someone to open a digest.
    """
    if not escalations:
        logger.info("competitor escalation alert: nothing to send (0 escalations)")
        return False

    recipients = get_escalation_recipients()
    if not recipients:
        logger.warning(
            "competitor escalation alert: COMPETITOR_ALERT_TO is unset — %d escalation(s) "
            "NOT delivered. Set the repository secret.", len(escalations))
        return False

    n = len(escalations)
    prefix = "[TEST] " if is_test else ""
    subject = (f"{prefix}Competitor ESCALATION — {n} paper{'s' if n != 1 else ''} "
               f"(week {week_tag})")

    note = ("<p style='font-size:13px;color:#5f6368'>This is a <b>test</b> alert fired to "
            "verify the channel end to end. No action needed.</p>" if is_test else "")

    html = (
        "<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:720px'>"
        f"<h2 style='margin:0 0 4px 0;font-size:18px'>Competitor escalation — week {week_tag}</h2>"
        f"<p style='margin:0 0 16px 0;font-size:13px;color:#5f6368'>"
        f"Scanned {generated_at.strftime('%Y-%m-%d %H:%M UTC')} · "
        f"{n} ESCALATE-tier hit{'s' if n != 1 else ''} · same-day relay to the hub.</p>"
        f"{note}"
        f"{_rows_html(escalations)}"
        "<p style='font-size:12px;color:#80868b'>Escalation criteria (a)-(d) per the "
        "2026-08-13 Research triage spec. Digest- and silent-tier hits are not alerted; "
        "see the weekly digest in reports/.</p>"
        "</div>"
    )
    text = (f"{prefix}Competitor escalation — week {week_tag}\n"
            f"Scanned {generated_at.strftime('%Y-%m-%d %H:%M UTC')} · {n} hit(s)\n\n"
            + _rows_text(escalations))

    ok = send_email(subject, html, recipients=recipients, text_body=text,
                    alert_type="event", trigger=TRIGGER)
    logger.info("competitor escalation alert: %d hit(s), sent=%s", n, ok)
    return ok


def test_payload(week_tag: str) -> list[dict[str, Any]]:
    """A synthetic ESCALATE row for proving the channel works.

    Modelled on a real hit so the test exercises the same rendering path as a
    live escalation rather than a degenerate one.
    """
    return [{
        "title": "CHANNEL TEST — synthetic escalation, no action required",
        "url": "https://arxiv.org/abs/0000.00000",
        "venue": "arXiv (q-fin.TR)",
        "date": week_tag,
        "triage_criteria": ["(a) >=2 LLMs on identical inputs, cross-model agreement/"
                            "convergence/herding"],
        "threatened_rqs": ["RQ1 (cross-model decision convergence)"],
        "assessment": ("Meets (a) of the escalation criteria on title+abstract. "
                       "Threatens RQ1 (cross-model decision convergence)."),
    }]
