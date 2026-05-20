"""Gmail SMTP transport for the alerting layer.

This is the single low-level send path. Everything above it (event alerts,
daily digest) builds an HTML string and hands it here. The contract:

  - `send_email()` NEVER raises. A failed send is logged and returns False,
    so a flaky SMTP connection can never crash a trading tick.
  - Every attempt — success, failure, or skipped-for-missing-config — is
    written to /data/alerts/email_log.jsonl AND the standard pipeline log.
  - Credentials come from the environment (GMAIL_ADDRESS / GMAIL_APP_PASSWORD),
    recipients from config/settings.json ("alert_recipients"). If either the
    credentials or the recipient list is missing, the send is skipped (logged,
    not fatal) — the pipeline behaves exactly as it did before email wiring.

Gmail specifics: we connect over implicit TLS on port 465 with SMTP_SSL and
authenticate with a 16-character Google App Password (not the account login
password). 2-Step Verification must be enabled on the account to mint one.
"""
from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate
from typing import Any

from ..config_loader import ALERTS_DIR, force_utf8_console, load_settings

logger = logging.getLogger("llmlab.alerts.email")

EMAIL_LOG = ALERTS_DIR / "email_log.jsonl"

SMTP_HOST = "smtp.gmail.com"
SMTP_SSL_PORT = 465
SMTP_TIMEOUT = 20  # seconds — keep tight so a stalled connection can't hang a tick
FROM_NAME = "LLM Trading Lab"


def get_recipients(settings: dict[str, Any] | None = None) -> list[str]:
    """Recipient list from settings.json. Empty list disables email."""
    if settings is None:
        try:
            settings = load_settings()
        except Exception:
            logger.exception("Could not load settings for alert recipients")
            return []
    recips = settings.get("alert_recipients") or []
    # De-dupe while preserving order; drop blanks.
    seen: set[str] = set()
    out: list[str] = []
    for r in recips:
        r = (r or "").strip()
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _log_email(record: dict[str, Any]) -> None:
    """Append one send attempt to /data/alerts/email_log.jsonl. Best-effort."""
    try:
        ALERTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(EMAIL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        # Logging the log failure is the most we can do — never propagate.
        logger.exception("Failed to write email_log.jsonl record")


def _html_to_text(html: str) -> str:
    """Crude HTML→text fallback for the multipart/alternative plain part.

    Not a full renderer — just enough so a text-only client shows something
    readable instead of raw markup. Drops tags, collapses whitespace, and
    turns common block tags into line breaks.
    """
    if not html:
        return ""
    text = re.sub(r"(?i)<\s*br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</\s*(p|tr|div|h[1-6]|li)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    # Decode the handful of entities we actually emit.
    for ent, ch in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                    ("&gt;", ">"), ("&middot;", "·"), ("&mdash;", "—")):
        text = text.replace(ent, ch)
    lines = [ln.strip() for ln in text.splitlines()]
    # Collapse runs of blank lines to a single blank line.
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def send_email(
    subject: str,
    html_body: str,
    recipients: list[str] | None = None,
    *,
    text_body: str | None = None,
    alert_type: str = "event",
    trigger: str = "",
    settings: dict[str, Any] | None = None,
) -> bool:
    """Send one HTML email via Gmail SMTP. Returns True on success.

    Wrapped end-to-end in try/except: any failure is logged (pipeline log +
    email_log.jsonl) and returns False. Callers never need to guard this.

    `alert_type` ("daily_digest" | "event" | "system") and `trigger` (a short
    machine tag for what fired it) are recorded in the log for auditing.
    """
    if settings is None:
        try:
            settings = load_settings()
        except Exception:
            settings = {}
    if recipients is None:
        recipients = get_recipients(settings)

    sender = (os.getenv("GMAIL_ADDRESS") or "").strip()
    password = (os.getenv("GMAIL_APP_PASSWORD") or "").strip()
    # App passwords are often displayed with spaces ("abcd efgh ijkl mnop") —
    # Gmail accepts them with or without, but strip to be safe.
    password = password.replace(" ", "")

    ts = datetime.now(timezone.utc).isoformat()
    base_record = {
        "timestamp": ts,
        "type": alert_type,
        "trigger": trigger,
        "subject": subject,
        "recipients": recipients,
    }

    # --- Pre-flight: config gates that make this a no-op rather than a failure ---
    if not (settings.get("alerts", {}) or {}).get("enabled", True):
        logger.info("Email alerts disabled in settings — skipping send: %s", subject)
        _log_email({**base_record, "status": "skipped", "reason": "alerts_disabled"})
        return False
    if not recipients:
        logger.warning("No alert_recipients configured — skipping email: %s", subject)
        _log_email({**base_record, "status": "skipped", "reason": "no_recipients"})
        return False
    if not sender or not password:
        logger.warning(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set — skipping email: %s", subject
        )
        _log_email({**base_record, "status": "skipped", "reason": "no_credentials"})
        return False

    # --- Build the MIME message (plain + HTML alternative) ---
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((FROM_NAME, sender))
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    plain = text_body if text_body is not None else _html_to_text(html_body)
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # --- Send ---
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_SSL_PORT, timeout=SMTP_TIMEOUT,
                              context=context) as server:
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        logger.info("Email sent [%s] '%s' → %s", alert_type, subject, ", ".join(recipients))
        _log_email({**base_record, "status": "sent"})
        return True
    except Exception as e:  # noqa: BLE001 — a failed email must never crash the pipeline
        logger.exception("Email send failed [%s] '%s': %s", alert_type, subject, e)
        _log_email({**base_record, "status": "failed", "error": str(e)})
        return False


def _send_test_email() -> bool:
    """Fire a one-off test email to the configured recipients.

    Run after adding GMAIL_ADDRESS / GMAIL_APP_PASSWORD to verify the wiring:
        python -m src.alerts.email_alerts
    """
    from ..config_loader import load_env
    load_env()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;'
        'max-width:520px;margin:auto;padding:20px;border:1px solid #e5e7eb;border-radius:8px;">'
        '<h2 style="margin:0 0 8px;color:#1f2937;">LLM Trading Lab — email test</h2>'
        f'<p style="color:#374151;">If you can read this, Gmail SMTP alerting is wired up correctly.</p>'
        f'<p style="color:#9ca3af;font-size:12px;">Sent {now}</p></div>'
    )
    ok = send_email(
        "[ALERT] LLM Trading Lab — email wiring test", html,
        text_body=f"Gmail SMTP alerting is wired up correctly. Sent {now}.",
        alert_type="system", trigger="manual_test",
    )
    print("Test email", "SENT" if ok else "NOT sent — see log above (check credentials/recipients)")
    return ok


if __name__ == "__main__":
    import logging as _logging
    force_utf8_console()
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(name)s %(message)s")
    raise SystemExit(0 if _send_test_email() else 1)
