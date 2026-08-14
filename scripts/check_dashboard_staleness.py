"""Public-dashboard staleness monitor.

The intraday ``pages`` job deploys the dashboard to GitHub Pages. That job is
now ``continue-on-error`` — a Pages deploy timeout is a cosmetic publishing
hiccup and must not red the workflow or fire a CRITICAL alert. The cost of that
decision is that a GENUINE, persistent publishing outage would otherwise go
silent forever. This script is the symptom-level control that closes that gap:
it fetches the LIVE public dashboard JSON, reads its ``generated_at``, and pages
us only if the public artifact has fallen more than ``STALE_THRESHOLD_HOURS``
behind the freshness we *expect* — the dashboard build the pipeline has already
committed locally — during an established trading session.

Contract (mirrors the alerting layer's "never crash a tick" rule):
  * NEVER raises out of ``main`` and NEVER exits non-zero in monitor mode. A
    monitor that can fail its host (the ``chain``) job would ironically trip the
    very CRITICAL alert we just scoped away. Every path returns 0.
  * Tolerant by construction: the 3-hour threshold rides straight through one or
    two failed deploys (30–60 min gaps). It only fires on a sustained outage.
  * Session-guarded: it only alerts once the session is at least 3h old
    (>= 12:30 ET) and before the close (< 16:00 ET), so the legitimately-stale
    overnight dashboard at the open is never a false positive.
  * Expected-freshness gated (holiday-safe): "stale" is measured against the
    locally-committed dashboard build, not wall-clock alone. On a market
    holiday, weekend, or pre-open the pipeline commits no new tick, so the local
    build is exactly as old as the public one — nothing failed to publish — and
    the monitor stays quiet. This self-adapts to half-days and any future
    schedule change with NO market-calendar dependency, which also matters
    because the ``chain`` job runs no ``pip install``: a calendar import would
    silently degrade to a weekday-only check and reintroduce the very holiday
    false positive this guard exists to kill.

Dependencies are stdlib only (plus ``src.config_loader`` for recipients/paths),
so the ``chain`` job needs no ``pip install``.

Usage:
  Monitor (CI ``chain`` job):  python scripts/check_dashboard_staleness.py
  Self-test (no net/no email): python scripts/check_dashboard_staleness.py --self-test
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import sys
import urllib.request
from datetime import datetime, time, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config_loader import force_utf8_console, load_env  # noqa: E402
from src.alerts.email_alerts import get_recipients  # noqa: E402

logger = logging.getLogger("llmlab.monitor.staleness")

# --- Tunables -----------------------------------------------------------------
DASHBOARD_URL = "https://llmtradinglab.ai/data/dashboard.json"
# The pipeline's committed dashboard build — the freshness we *expect* to be
# live. The Pages layer publishes exactly this file, so comparing the live
# artifact against it (rather than wall-clock) is what makes the monitor
# holiday-safe without any market calendar. Present in every checkout because
# it is committed by the `run` job.
LOCAL_DASHBOARD = ROOT / "data" / "dashboard.json"
STALE_THRESHOLD_HOURS = 3.0
FETCH_TIMEOUT_SECONDS = 15

EASTERN = ZoneInfo("America/New_York")
NYSE_OPEN = time(9, 30)
NYSE_CLOSE = time(16, 0)
# Only alert once the session is at least STALE_THRESHOLD_HOURS old. Before this
# point the dashboard may legitimately still show the prior day's close, so a
# raw "> 3h old" check would false-positive every morning at the open.
SESSION_GUARD_START = time(12, 30)  # 09:30 ET open + 3h

# Gmail SMTP (same transport the pipeline's alerter uses).
SMTP_HOST = "smtp.gmail.com"
SMTP_SSL_PORT = 465
SMTP_TIMEOUT = 20
FROM_NAME = "LLM Trading Lab"


# --- Pure decision logic (unit-testable, no I/O) ------------------------------
def parse_generated_at(value: str) -> datetime:
    """Parse the dashboard's ``generated_at`` into an aware UTC datetime.

    The generator writes a naive ISO timestamp in UTC (verified: its naive
    values match the ``+00:00`` timestamps in email_log.jsonl). A naive value is
    therefore treated as UTC; an explicit offset is honored and normalized.
    """
    dt = datetime.fromisoformat(value.strip())
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hours_stale(generated_at: datetime, now: datetime) -> float:
    """Whole hours the live dashboard trails wall-clock (may be negative)."""
    return (now - generated_at).total_seconds() / 3600.0


def in_alert_window(now: datetime) -> bool:
    """True only during an established trading session (weekday, 12:30–16:00 ET).

    This is the session guard: it suppresses the overnight-stale false positive
    at the open and any weekend/after-hours run.
    """
    now_et = now.astimezone(EASTERN)
    if now_et.weekday() >= 5:  # Sat/Sun
        return False
    return SESSION_GUARD_START <= now_et.timetz().replace(tzinfo=None) < NYSE_CLOSE


def decide(generated_at: datetime, now: datetime,
           threshold: float = STALE_THRESHOLD_HOURS,
           expected_generated_at: datetime | None = None) -> dict:
    """Decide whether to alert. Pure: same inputs → same output.

    ``generated_at`` is the LIVE public dashboard's timestamp, ``now`` is
    wall-clock, and ``expected_generated_at`` is the locally-committed build's
    timestamp — the freshness we expect to be live (see module docstring).

    An alert requires all three of:
      1. ``in_window``       — inside an established trading session.
      2. ``is_stale``        — the public artifact trails wall-clock by > threshold.
      3. ``behind_expected`` — the public artifact ALSO trails the locally-built
         dashboard by > threshold, i.e. there is genuinely newer committed data
         that failed to publish. On a holiday / weekend / pre-open no new tick is
         committed, so live ≈ local and this gate stays shut — the holiday-safe
         guard, with no market calendar.

    When ``expected_generated_at`` is None (local build unreadable, or a pure
    unit-test call) the monitor falls back to the wall-clock signal alone:
    ``behind_expected`` collapses to ``is_stale`` and behaviour is unchanged.

    Returns a dict with the decision and every input that fed it, so the CI log
    and the alert body can explain *why* it did or didn't fire.
    """
    stale_h = hours_stale(generated_at, now)
    windowed = in_alert_window(now)
    is_stale = stale_h > threshold

    # Expected-freshness gate: how far the public artifact trails the data we've
    # already built and committed (positive = public is behind the local build).
    # None → fall back to the wall-clock signal alone.
    if expected_generated_at is not None:
        behind_expected_h = hours_stale(generated_at, expected_generated_at)
        behind_expected = behind_expected_h > threshold
    else:
        behind_expected_h = None
        behind_expected = is_stale

    alert = bool(windowed and is_stale and behind_expected)
    if not windowed:
        reason = "outside alert window (not an established session)"
    elif not is_stale:
        reason = f"fresh enough ({stale_h:.2f}h <= {threshold:.0f}h)"
    elif not behind_expected:
        reason = (f"stale {stale_h:.2f}h vs wall-clock but matches the local build "
                  f"({behind_expected_h:.2f}h behind committed data <= {threshold:.0f}h) "
                  f"— no committed tick to publish (holiday/weekend/pre-open)")
    elif behind_expected_h is not None:
        reason = (f"STALE {stale_h:.2f}h > {threshold:.0f}h during session; "
                  f"publish is {behind_expected_h:.2f}h behind committed data")
    else:
        reason = f"STALE {stale_h:.2f}h > {threshold:.0f}h during session"
    return {
        "alert": alert,
        "hours_stale": stale_h,
        "in_window": windowed,
        "is_stale": is_stale,
        "behind_expected": behind_expected,
        "hours_behind_expected": behind_expected_h,
        "reason": reason,
        "generated_at": generated_at.isoformat(),
        "expected_generated_at": (expected_generated_at.isoformat()
                                  if expected_generated_at is not None else None),
        "now": now.isoformat(),
        "threshold_hours": threshold,
    }


# --- I/O ----------------------------------------------------------------------
def fetch_live_generated_at(url: str = DASHBOARD_URL,
                            timeout: int = FETCH_TIMEOUT_SECONDS) -> datetime | None:
    """Fetch the live dashboard JSON and return its ``generated_at`` (UTC).

    Returns None on any failure (network, HTTP, JSON, missing/invalid field).
    A fetch blip is treated as tolerable, not an outage — we do not alert on it,
    matching the "tolerant of one-off slowness" intent.
    """
    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                                   "User-Agent": "llmlab-staleness-monitor"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URL)
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — a fetch blip must not crash the monitor
        logger.warning("Could not fetch live dashboard (%s): %s", url, e)
        return None

    raw = payload.get("generated_at")
    if not raw:
        logger.warning("Live dashboard JSON has no 'generated_at' field")
        return None
    try:
        return parse_generated_at(str(raw))
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not parse generated_at %r: %s", raw, e)
        return None


def read_local_generated_at(path: Path = LOCAL_DASHBOARD) -> datetime | None:
    """Return ``generated_at`` from the locally-committed dashboard build (UTC).

    This is the *expected* freshness: the data the pipeline has actually built
    and committed on disk, which the Pages layer is responsible for publishing.
    Comparing the live public artifact against THIS — rather than wall-clock —
    is what makes the monitor self-adapt to holidays, half-days, and any future
    schedule change with no market-calendar dependency.

    Returns None if the file is missing / unreadable / malformed, in which case
    the caller falls back to the wall-clock staleness signal alone.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — a read blip must not crash the monitor
        logger.warning("Could not read local dashboard build (%s): %s", path, e)
        return None
    raw = payload.get("generated_at")
    if not raw:
        logger.warning("Local dashboard build has no 'generated_at' field")
        return None
    try:
        return parse_generated_at(str(raw))
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not parse local generated_at %r: %s", raw, e)
        return None


def _build_alert(decision: dict) -> tuple[str, str, str]:
    """Return (subject, html_body, text_body) for the staleness alert."""
    stale_h = decision["hours_stale"]
    gen = decision["generated_at"]
    now = decision["now"]
    expected = decision.get("expected_generated_at") or "unavailable"
    behind = decision.get("hours_behind_expected")
    behind_txt = (f"Behind local build: {behind:.1f} hours\n" if behind is not None else "")
    subject = "[ALERT] Public dashboard stale — Pages deploy not publishing"
    text = (
        "The live public dashboard is behind the data the pipeline has committed.\n\n"
        f"Live generated_at:  {gen}\n"
        f"Locally-built:      {expected}\n"
        f"Now (UTC):          {now}\n"
        f"Stale by:           {stale_h:.1f} hours (threshold {decision['threshold_hours']:.0f}h)\n"
        f"{behind_txt}\n"
        "Trading, data, and the self-chain are unaffected — this is the GitHub\n"
        "Pages publish layer. The `pages` job is continue-on-error, so check the\n"
        "Actions tab for red `pages` jobs / deploy-pages timeouts, and check\n"
        "https://www.githubstatus.com for a Pages incident.\n"
        f"Source: {DASHBOARD_URL}\n"
    )
    html = f"""\
<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:16px;"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;border-radius:8px;overflow:hidden;border:1px solid #e4e4e7;">
  <tr><td style="background:#b45309;padding:14px 20px;">
    <div style="color:#fff;font-size:12px;letter-spacing:.08em;text-transform:uppercase;opacity:.85;">WARNING &middot; Publishing</div>
    <div style="color:#fff;font-size:18px;font-weight:700;margin-top:2px;">Public dashboard is stale</div>
  </td></tr>
  <tr><td style="padding:18px 20px;color:#222;font-size:14px;line-height:1.6;">
    The live dashboard hasn't refreshed in <b>{stale_h:.1f} hours</b> (threshold {decision['threshold_hours']:.0f}h).
    Trading, data, and the self-chain are unaffected — this is the GitHub Pages publish layer
    (the <code>pages</code> job is <code>continue-on-error</code>). Check the Actions tab for red
    <code>pages</code> jobs and <a href="https://www.githubstatus.com">GitHub Status</a> for a Pages incident.
    <table role="presentation" cellpadding="0" cellspacing="0" style="font-size:13px;margin-top:12px;">
      <tr><td style="padding:3px 12px 3px 0;color:#666;">Live generated_at</td><td style="padding:3px 0;color:#111;">{gen}</td></tr>
      <tr><td style="padding:3px 12px 3px 0;color:#666;">Locally-built (expected)</td><td style="padding:3px 0;color:#111;">{expected}</td></tr>
      <tr><td style="padding:3px 12px 3px 0;color:#666;">Now (UTC)</td><td style="padding:3px 0;color:#111;">{now}</td></tr>
      <tr><td style="padding:3px 12px 3px 0;color:#666;">Source</td><td style="padding:3px 0;"><a href="{DASHBOARD_URL}">{DASHBOARD_URL}</a></td></tr>
    </table>
  </td></tr>
  <tr><td style="padding:12px 20px;background:#fafafa;border-top:1px solid #eee;color:#9ca3af;font-size:11px;">
    LLM Trading Lab dashboard-staleness monitor
  </td></tr>
</table></td></tr></table></body></html>"""
    return subject, html, text


def send_staleness_alert(decision: dict) -> bool:
    """Send the staleness alert via Gmail SMTP. Never raises; returns success.

    Recipients come from ALERT_RECIPIENTS (comma-separated, secret-backed);
    credentials from GMAIL_ADDRESS / GMAIL_APP_PASSWORD. Missing config makes
    this a logged no-op, exactly like the pipeline's own send path.

    The recipient list is read through src.alerts.email_alerts.get_recipients
    rather than re-parsed here, so there is exactly one place that decides where
    recipients come from. That module is stdlib-only, so it is safe to import
    from this job, which runs no ``pip install`` (see the module docstring).
    """
    subject, html_body, text_body = _build_alert(decision)
    recipients = get_recipients()
    sender = (os.getenv("GMAIL_ADDRESS") or "").strip()
    password = (os.getenv("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()

    if not recipients:
        logger.warning("ALERT_RECIPIENTS not set — staleness alert not sent")
        return False
    if not sender or not password:
        logger.warning("GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set — staleness alert not sent")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((FROM_NAME, sender))
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_SSL_PORT, timeout=SMTP_TIMEOUT,
                              context=context) as server:
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        logger.info("Staleness alert sent → %s", ", ".join(recipients))
        return True
    except Exception as e:  # noqa: BLE001 — a failed alert must not crash the monitor
        logger.exception("Staleness alert send failed: %s", e)
        return False


def run_monitor() -> int:
    """Fetch → decide → maybe alert. Always returns 0 (monitoring is non-fatal)."""
    generated_at = fetch_live_generated_at()
    if generated_at is None:
        logger.info("No usable generated_at — skipping staleness check (no alert)")
        return 0

    expected = read_local_generated_at()
    now = datetime.now(timezone.utc)
    decision = decide(generated_at, now, expected_generated_at=expected)
    logger.info("Dashboard staleness: %s", decision["reason"])
    if decision["alert"]:
        send_staleness_alert(decision)
    return 0


# --- Self-test ----------------------------------------------------------------
def _self_test() -> int:
    """Prove the control fires when it should and stays quiet when it shouldn't.

    Pure decision logic only — no network, no email. Returns 0 iff all cases
    pass so it can gate CI / manual verification.
    """
    from datetime import timedelta

    tue = datetime(2026, 7, 7, tzinfo=EASTERN)   # Tuesday — a trading day
    sat = datetime(2026, 7, 11, tzinfo=EASTERN)  # Saturday
    assert tue.weekday() == 1 and sat.weekday() == 5, "test-date sanity"

    def at(base, h, m):
        """A UTC instant corresponding to h:m Eastern on the base date."""
        return base.replace(hour=h, minute=m).astimezone(timezone.utc)

    now_mid = at(tue, 14, 0)   # 14:00 ET — established session (past 12:30 guard)
    now_open = at(tue, 10, 0)  # 10:00 ET — session too young
    now_wknd = at(sat, 14, 0)  # Saturday afternoon

    # A holiday mirrors the real 2026-07-03 15:32 ET false positive: a weekday
    # inside the session window, the live dashboard hours-old vs wall-clock, but
    # the LOCAL build is exactly as old — no tick committed today, so nothing
    # failed to publish.
    holiday_stale = now_mid - timedelta(hours=17)  # yesterday's close, still live

    checks = [
        # name, live generated_at (UTC), now (UTC), expected/local build, expect_alert
        # --- wall-clock path (expected=None): existing behaviour, unchanged ---
        ("stale 5h, mid-session → ALERT",
         now_mid - timedelta(hours=5),    now_mid, None, True),
        ("fresh 20m, mid-session → quiet",
         now_mid - timedelta(minutes=20), now_mid, None, False),
        ("stale 16h, at the open → quiet (morning guard)",
         now_open - timedelta(hours=16), now_open, None, False),
        ("stale 6h, weekend → quiet",
         now_wknd - timedelta(hours=6),   now_wknd, None, False),
        ("boundary 3h01m, mid-session → ALERT",
         now_mid - timedelta(hours=3, minutes=1), now_mid, None, True),
        ("boundary 2h59m, mid-session → quiet",
         now_mid - timedelta(hours=2, minutes=59), now_mid, None, False),
        # --- expected-freshness gate (option b, holiday-safe) ---
        # Holiday weekday, in session, live 17h stale vs wall-clock BUT the local
        # build is identical (no committed tick today) → publish isn't behind →
        # quiet, with no market calendar. Regression guard for the 2026-07-03 FP.
        ("holiday weekday, live == local build → quiet",
         holiday_stale, now_mid, holiday_stale, False),
        # Trading day with ticks committing (local build fresh, 20m old) but the
        # public artifact is stuck 6h behind → genuine publish outage → ALERT.
        # Regression guard for the correct-fire path.
        ("trading day w/ fresh ticks, live stuck 6h → ALERT",
         now_mid - timedelta(hours=6), now_mid, now_mid - timedelta(minutes=20), True),
    ]
    all_ok = True
    print("dashboard-staleness self-test")
    print("-" * 60)
    for name, gen, now, expected, expect in checks:
        d = decide(gen, now, expected_generated_at=expected)
        ok = d["alert"] is expect
        all_ok = all_ok and ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        print(f"         alert={d['alert']} expected={expect} "
              f"({d['hours_stale']:.2f}h stale, in_window={d['in_window']}, "
              f"behind_expected={d['hours_behind_expected']})")
    print("-" * 60)
    print("RESULT:", "ALL PASS" if all_ok else "FAILURES PRESENT")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    force_utf8_console()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    load_env()
    argv = sys.argv[1:] if argv is None else argv
    if "--self-test" in argv:
        return _self_test()
    try:
        return run_monitor()
    except Exception as e:  # noqa: BLE001 — monitoring must never fail its host job
        logger.exception("Staleness monitor crashed (non-fatal): %s", e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
