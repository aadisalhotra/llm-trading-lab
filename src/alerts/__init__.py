"""Email alerting layer.

`send_alert` and `send_daily_summary` are the stable interfaces the pipeline
calls. Underneath: Gmail SMTP transport (email_alerts), event detection +
dedup/cap dispatch (events), the daily HTML digest (digest), and the
persistent dedup/milestone state (alert_state).

`send_heartbeat` is separate from the email path: it pings an external
dead-man's-switch so an independent monitor can catch decision-making absence
(not just workflow failure). See heartbeat.py.
"""
from .alerter import send_alert, send_daily_summary
from .email_alerts import send_email, get_recipients
from .events import dispatch_event, run_eod_alert_sweep, scan_macro_events
from .digest import build_digest, send_daily_digest
from .heartbeat import send_heartbeat

__all__ = [
    "send_alert",
    "send_daily_summary",
    "send_email",
    "get_recipients",
    "dispatch_event",
    "run_eod_alert_sweep",
    "scan_macro_events",
    "build_digest",
    "send_daily_digest",
    "send_heartbeat",
]
