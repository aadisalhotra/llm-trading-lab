"""CI failure notifier — emails when a pipeline workflow run fails.

Invoked by an `if: failure()` job in the GitHub Actions workflow so that a
hard failure the running pipeline can't self-report (the process crashed, the
runner died, a deploy step failed) still reaches the inbox. This covers the
spec's "pipeline fails to run / cron failure / deployment failure / dashboard
build failure" cases that the in-process `send_alert` path can't catch.

Reads the standard GitHub Actions context from the environment and sends a
short [ALERT] email via the same Gmail transport the pipeline uses. Exits 0
regardless of send outcome — a failed alert email must not turn a failure
notification step into a second failure.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config_loader import force_utf8_console, load_env  # noqa: E402
from src.alerts.email_alerts import send_email  # noqa: E402


def main() -> int:
    force_utf8_console()
    load_env()

    workflow = os.getenv("GITHUB_WORKFLOW", "pipeline")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    sha = (os.getenv("GITHUB_SHA", "") or "")[:7]
    ref = os.getenv("GITHUB_REF_NAME", "")
    mode = os.getenv("RUN_MODE", "")
    failed_jobs = os.getenv("FAILED_CONTEXT", "")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else server
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    title = f"Pipeline workflow failed: {workflow}"
    detail = f" ({mode})" if mode else ""
    body_text = (
        f"A GitHub Actions run of '{workflow}'{detail} failed.\n\n"
        f"Repo:   {repo}\n"
        f"Branch: {ref}\n"
        f"Commit: {sha}\n"
        f"Time:   {ts}\n"
        f"Run:    {run_url}\n"
    )
    if failed_jobs:
        body_text += f"Context: {failed_jobs}\n"

    html = f"""\
<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:16px;"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;border-radius:8px;overflow:hidden;border:1px solid #e4e4e7;">
  <tr><td style="background:#c0392b;padding:14px 20px;">
    <div style="color:#fff;font-size:12px;letter-spacing:.08em;text-transform:uppercase;opacity:.85;">CRITICAL &middot; System</div>
    <div style="color:#fff;font-size:18px;font-weight:700;margin-top:2px;">{title}{detail}</div>
  </td></tr>
  <tr><td style="padding:18px 20px;color:#222;font-size:14px;line-height:1.6;">
    A GitHub Actions run of <b>{workflow}</b> failed{detail}. The trading pipeline may not have
    completed — check the run log.
    <table role="presentation" cellpadding="0" cellspacing="0" style="font-size:13px;margin-top:12px;">
      <tr><td style="padding:3px 12px 3px 0;color:#666;">Repo</td><td style="padding:3px 0;color:#111;">{repo}</td></tr>
      <tr><td style="padding:3px 12px 3px 0;color:#666;">Branch</td><td style="padding:3px 0;color:#111;">{ref}</td></tr>
      <tr><td style="padding:3px 12px 3px 0;color:#666;">Commit</td><td style="padding:3px 0;color:#111;">{sha}</td></tr>
      <tr><td style="padding:3px 12px 3px 0;color:#666;">Time</td><td style="padding:3px 0;color:#111;">{ts}</td></tr>
      <tr><td style="padding:3px 12px 3px 0;color:#666;">Run</td><td style="padding:3px 0;"><a href="{run_url}">{run_url}</a></td></tr>
    </table>
  </td></tr>
  <tr><td style="padding:12px 20px;background:#fafafa;border-top:1px solid #eee;color:#9ca3af;font-size:11px;">
    LLM Trading Lab CI failure notifier
  </td></tr>
</table></td></tr></table></body></html>"""

    sent = send_email(f"[ALERT] {title}{detail}", html, text_body=body_text,
                      alert_type="system", trigger="ci_failure")
    print(f"CI failure notification {'sent' if sent else 'NOT sent (see log)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
