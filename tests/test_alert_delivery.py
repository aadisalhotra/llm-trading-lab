"""Alert delivery: bounded SMTP retry, and event sends kept off the caller's thread.

Hub dispatch 2026-11-04. Persistent conditions self-heal by re-detection on the
next cycle; one-off events do not, so one transient SMTP failure used to lose
them (observed that evening: WinError 10051 on the first controlled send, the
retry a minute later delivered). And the send ran inline on the calling model
thread, ahead of that book's barrier registration, so a stalled SMTP server
delayed every book. These tests pin both fixes.
"""
from __future__ import annotations

import json
import smtplib
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from src.alerts import alert_state, email_alerts, events

REPO = Path(__file__).resolve().parents[1]
SETTINGS = {"alerts": {"enabled": True, "max_event_alerts_per_day": 10}}


# ---------------------------------------------------------------- transport --

@pytest.fixture
def smtp_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setattr(email_alerts, "ALERTS_DIR", tmp_path)
    monkeypatch.setattr(email_alerts, "EMAIL_LOG", tmp_path / "email_log.jsonl")
    sleeps: list[float] = []
    monkeypatch.setattr(email_alerts.time, "sleep", lambda s: sleeps.append(s))
    return tmp_path, sleeps


def _send():
    return email_alerts.send_email("[ALERT] t", "<p>b</p>", ["a@example.com"],
                                   text_body="b", trigger="t", settings=SETTINGS)


def _log(tmp_path: Path) -> list[dict]:
    return [json.loads(l) for l in (tmp_path / "email_log.jsonl").read_text().splitlines()]


def _script(monkeypatch, outcomes):
    """Patch one SMTP session to follow `outcomes`: an exception to raise or True."""
    calls = []

    def once(*a, **k):
        calls.append(1)
        o = outcomes[len(calls) - 1]
        if isinstance(o, BaseException):
            raise o
        return o
    monkeypatch.setattr(email_alerts, "_smtp_send_once", once)
    return calls


def test_transient_failures_are_retried_until_delivered(smtp_env, monkeypatch):
    tmp_path, sleeps = smtp_env
    calls = _script(monkeypatch, [OSError(10051, "unreachable network"),
                                  smtplib.SMTPServerDisconnected("dropped"), True])
    assert _send() is True
    assert len(calls) == 3
    assert sleeps == list(email_alerts.RETRY_BACKOFF_SECONDS)
    rows = _log(tmp_path)
    assert len(rows) == 1                      # one record per email, not per attempt
    assert rows[0]["status"] == "sent" and rows[0]["attempts"] == 3


def test_attempts_are_bounded_and_failure_logged_exactly_as_before(smtp_env, monkeypatch):
    tmp_path, _ = smtp_env
    calls = _script(monkeypatch, [OSError("down")] * 10)
    assert _send() is False
    assert len(calls) == email_alerts.SEND_ATTEMPTS == 3
    rows = _log(tmp_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "failed" and rows[0]["attempts"] == 3
    assert rows[0]["error"] == "down"


@pytest.mark.parametrize("err", [
    smtplib.SMTPAuthenticationError(535, b"bad credentials"),
    smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"no such user")}),
    smtplib.SMTPDataError(554, b"rejected"),
])
def test_permanent_failures_are_not_retried(smtp_env, monkeypatch, err):
    tmp_path, sleeps = smtp_env
    calls = _script(monkeypatch, [err, True, True])
    assert _send() is False
    assert len(calls) == 1 and sleeps == []
    assert _log(tmp_path)[0]["attempts"] == 1


def test_smtp_4xx_is_transient():
    assert email_alerts._is_transient(smtplib.SMTPDataError(421, b"try later"))
    assert not email_alerts._is_transient(smtplib.SMTPDataError(550, b"no"))
    assert not email_alerts._is_transient(ValueError("a bug, not the network"))


def test_error_after_acceptance_is_not_resent(monkeypatch):
    """QUIT failing after sendmail returned must not trigger a duplicate."""
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            raise smtplib.SMTPServerDisconnected("connection closed during QUIT")

        def login(self, *a):
            pass

        def sendmail(self, *a):
            sent.append(a)
    monkeypatch.setattr(email_alerts.smtplib, "SMTP_SSL", FakeSMTP)
    assert email_alerts._smtp_send_once("s", "p", ["r"], "payload") is True
    assert len(sent) == 1


def test_error_before_acceptance_propagates(monkeypatch):
    class FakeSMTP:
        def __init__(self, *a, **k):
            raise OSError(10051, "unreachable network")
    monkeypatch.setattr(email_alerts.smtplib, "SMTP_SSL", FakeSMTP)
    with pytest.raises(OSError):
        email_alerts._smtp_send_once("s", "p", ["r"], "payload")


# --------------------------------------------------------------- dispatcher --

@pytest.fixture
def state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(alert_state, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(events, "ASYNC_SEND", True)
    yield tmp_path / "state.json"
    assert events.wait_for_pending_sends(timeout=10)


def _daily():
    return alert_state.load_state()["daily"]


def _dispatch(key="gbook:cyc1", severity="CRITICAL"):
    return events.dispatch_event("gbook_reconciliation_error", severity, "t", "b",
                                 dedup_key=key, settings=SETTINGS)


def test_caller_returns_before_the_send_finishes(state_file, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(events, "send_email", lambda *a, **k: release.wait(10) or True)
    t0 = time.monotonic()
    assert _dispatch() == "queued"
    assert time.monotonic() - t0 < 1.0        # the caller never waits on SMTP
    d = _daily()
    assert d["sent_count"] == 1 and "gbook:cyc1" in d["dedup_keys"]   # reserved
    assert d["fired_log"][-1]["disposition"] == "queued"
    assert _dispatch() == "deduped"           # a concurrent repeat sees the reservation
    release.set()
    assert events.wait_for_pending_sends(timeout=10)
    d = _daily()
    assert d["fired_log"][-1]["disposition"] == "sent"
    assert d["sent_count"] == 1


def test_failed_background_send_gives_back_slot_and_key(state_file, monkeypatch):
    monkeypatch.setattr(events, "send_email", lambda *a, **k: False)
    assert _dispatch() == "queued"
    assert events.wait_for_pending_sends(timeout=10)
    d = _daily()
    assert d["fired_log"][-1]["disposition"] == "send_failed"
    assert d["sent_count"] == 0 and "gbook:cyc1" not in d["dedup_keys"]
    # ...so a later tick can fire it again.
    monkeypatch.setattr(events, "send_email", lambda *a, **k: True)
    assert _dispatch() == "queued"
    assert events.wait_for_pending_sends(timeout=10)
    assert _daily()["fired_log"][-1]["disposition"] == "sent"


def test_concurrent_dispatches_respect_the_cap(state_file, monkeypatch):
    monkeypatch.setattr(events, "send_email", lambda *a, **k: time.sleep(0.2) or True)
    settings = {"alerts": {"enabled": True, "max_event_alerts_per_day": 3}}
    out = []
    threads = [threading.Thread(target=lambda i=i: out.append(events.dispatch_event(
        "x", "WARN", f"t{i}", "b", dedup_key=f"k{i}", settings=settings))) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(out) == ["overflow"] * 3 + ["queued"] * 3
    assert events.wait_for_pending_sends(timeout=10)
    assert _daily()["sent_count"] == 3


def test_process_exit_waits_for_an_in_flight_send(tmp_path):
    """CI commits data/ in a later step, after the process exits. The send
    thread is non-daemon, so the process must not exit before it settles."""
    state = tmp_path / "state.json"
    code = textwrap.dedent(f"""
        import time
        from pathlib import Path
        from src.alerts import alert_state, events
        alert_state.STATE_FILE = Path({str(state)!r})
        events.send_email = lambda *a, **k: time.sleep(1.5) or True
        assert events.dispatch_event("x", "CRITICAL", "t", "b", dedup_key="k",
            settings={{"alerts": {{"enabled": True}}}}) == "queued"
        # no wait: falling off the end of the script is the test
    """)
    subprocess.run([sys.executable, "-c", code], cwd=REPO, check=True, timeout=60)
    daily = json.loads(state.read_text(encoding="utf-8"))["daily"]
    assert daily["fired_log"][-1]["disposition"] == "sent"
    assert daily["sent_count"] == 1
