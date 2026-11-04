"""Controlled end-to-end CRITICAL delivery test.

Fires one real CRITICAL through the production alert path and exits non-zero
unless the email was accepted for delivery:

    prompt_integrity.evaluate -> prompt_integrity._alert -> alerter.send_alert
      -> events.dispatch_event (background send) -> email_alerts.send_email
      (bounded retry) -> Gmail SMTP -> ALERT_RECIPIENTS

Only the INPUT is controlled. The check runs against a copy of the frozen
reference with one component hash zeroed, so the real render reports a
mismatch. The copy's `frozen_commit` is replaced with a CONTROLLED-DELIVERY-TEST
marker naming where the test ran, which the alert body quotes. The SUBJECT is
prefixed with SUBJECT_PREFIX (hub ruling 2026-11-04), by wrapping the
send_email name that events.dispatch_event calls. Delivery is what is under
test, not the subject string. An inbox that learns "Frozen prompt drift" is
sometimes a test would hesitate on the one that is not.

Nothing is written to a tracked path. The alert log, alert state and email log
are redirected to a temporary directory, so today's dedup keys and daily cap
are untouched. The prompt-integrity log is never written: the script calls
`evaluate` and `_alert`, not `run_prompt_integrity_check`.

Run locally (`python scripts/alert_delivery_test.py`) or from CI
(.github/workflows/alert_delivery_test.yml). Prints one JSON summary line.
Inbox arrival is not something this script can see; confirm it in the mailbox.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.config_loader import force_utf8_console, load_env  # noqa: E402

logger = logging.getLogger("llmlab.alert_delivery_test")
SUBJECT_PREFIX = "[TEST - NOT A REAL ALERT] "
WAIT_SECONDS = 180   # 3 attempts x up to ~3 x SMTP_TIMEOUT each, plus backoff


def where() -> str:
    run_id = os.getenv("GITHUB_RUN_ID")
    if run_id:
        return f"GitHub Actions run {run_id} (attempt {os.getenv('GITHUB_RUN_ATTEMPT', '1')})"
    return "local machine"


def main() -> int:
    force_utf8_console()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_env()
    scratch = Path(tempfile.mkdtemp(prefix="alert_delivery_test_"))

    from src.alerts import alert_state, alerter, email_alerts, events
    alerter.LOGS_DIR = scratch
    alerter.ALERT_LOG = scratch / "alerts.jsonl"
    alert_state.ALERTS_DIR = scratch
    alert_state.STATE_FILE = scratch / "state.json"
    email_alerts.ALERTS_DIR = scratch
    email_alerts.EMAIL_LOG = scratch / "email_log.jsonl"
    from src.logging import prompt_integrity as pi

    real_send = email_alerts.send_email

    def marked_send(subject, *args, **kwargs):
        return real_send(SUBJECT_PREFIX + subject, *args, **kwargs)
    events.send_email = marked_send

    recipients = email_alerts.get_recipients()
    reference = json.loads(pi.REFERENCE_PATH.read_text(encoding="utf-8"))
    baseline = pi.evaluate(reference)
    if baseline.get("status") != "match":
        logger.error("Unperturbed check did not match (%s); refusing to induce on top of it.",
                     baseline.get("status"))
        return 2

    perturbed = copy.deepcopy(reference)
    component = sorted(perturbed["component_sha256"])[0]
    perturbed["component_sha256"][component] = "0" * 64
    perturbed["frozen_commit"] = (f"CONTROLLED-DELIVERY-TEST from {where()}: induced, "
                                  "production reference untouched, not a real drift")
    record = pi.evaluate(perturbed)
    if record.get("status") != "mismatch":
        logger.error("Induction failed: status %s", record.get("status"))
        return 2
    record["boundary"] = "controlled-delivery-test"

    pi._alert(record)
    drained = events.wait_for_pending_sends(timeout=WAIT_SECONDS)

    rows = []
    if email_alerts.EMAIL_LOG.exists():
        rows = [json.loads(l) for l in email_alerts.EMAIL_LOG.read_text(encoding="utf-8").splitlines()
                if l.strip()]
    last = rows[-1] if rows else {}
    fired = (json.loads(alert_state.STATE_FILE.read_text(encoding="utf-8"))["daily"]["fired_log"]
             if alert_state.STATE_FILE.exists() else [])
    summary = {
        "where": where(),
        "induced_component": component,
        "send_thread_drained": drained,
        "email_status": last.get("status"),
        "attempts": last.get("attempts"),
        "error": last.get("error"),
        "recipient_count": len(recipients),
        "recipients_fingerprint": email_alerts.recipients_fingerprint(recipients),
        "subject": last.get("subject"),
        "disposition": fired[-1].get("disposition") if fired else None,
        "timestamp": last.get("timestamp"),
    }
    print("ALERT_DELIVERY_TEST " + json.dumps(summary, sort_keys=True))
    ok = (drained and last.get("status") == "sent" and summary["disposition"] == "sent"
          and str(last.get("subject", "")).startswith(SUBJECT_PREFIX))
    if not ok:
        logger.error("CRITICAL was NOT accepted for delivery: %s", summary)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
