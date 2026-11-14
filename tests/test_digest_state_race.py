"""The end-of-day digest must not overwrite alert-state writes made while it sends.

Production, every end-of-day run since at least 2026-11-10: the EOD sweep
dispatches its events (each email on a background thread), then the digest
runs. send_daily_digest read the state file, sent the digest over SMTP, then
saved its stale copy with digest_sent = True -- outside _DISPATCH_LOCK. Every
event thread that settled during that send (fired_log 'sent', reported_once,
milestones_fired) was overwritten. Committed state shows it: the EOD events of
11-10, 11-11, 11-12 and 11-13 all read 'queued', and reported_once stayed {},
so GPT's explained negative cash never folded and emailed every evening.

These tests run the real interleaving with real threads: the event's send is
held open until the digest's SMTP send is in flight, then allowed to settle.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_alerts import SETTINGS, iso  # noqa: E402,F401  (iso is a fixture)

from src.alerts import alert_state, digest, events  # noqa: E402

DAY = "2026-11-13"
SUMMARY = {
    "date": DAY,
    "models": [{"model_key": "gpt", "status": "OK", "trades_today": 0}],
    "leaderboard": [{"model_key": "gpt", "rank": 1, "daily_pnl_pct": 0.0,
                     "cumulative_return": 0.0, "alpha_vs_spy": 0.0,
                     "halted": False, "last_api_success": True}],
}
REPORT_ONCE = ("state_cash:gpt", "-0.0105|0.012300")


def _race(iso, monkeypatch, *, mark_milestones=None):
    """Dispatch one async event whose send completes only while the digest's
    own send is in flight -- the production order on 2026-11-13 22:07Z."""
    import src.data.market_data as md
    monkeypatch.setattr(md, "fetch_index_data", lambda lookback_days=5: {})
    monkeypatch.setattr(md, "is_market_open_today", lambda ref=None: True)
    monkeypatch.setattr(events, "session_date", lambda: DAY)
    monkeypatch.setattr(events, "ASYNC_SEND", True)

    release_event = threading.Event()
    digest_in_flight = threading.Event()

    def event_send(subject, html, recipients=None, **kwargs):
        assert release_event.wait(10), "digest never started sending"
        iso.append({"subject": subject})
        return True

    def digest_send(subject, html, recipients=None, **kwargs):
        digest_in_flight.set()
        release_event.set()
        # The event thread settles (under the dispatch lock) while the digest's
        # SMTP send is still open, exactly as in production.
        assert events.wait_for_pending_sends(timeout=10)
        iso.append({"subject": subject})
        return True

    monkeypatch.setattr(events, "send_email", event_send)
    monkeypatch.setattr(digest, "send_email", digest_send)

    disposition = events.dispatch_event(
        "state_anomaly", "WARN", "State anomaly (cash): GPT-5.4", "b", {"model": "gpt"},
        dedup_key="state_cash:gpt", report_once=REPORT_ONCE,
        mark_milestones=mark_milestones, settings=SETTINGS)
    assert disposition == "queued"
    assert digest.send_daily_digest(SUMMARY, SETTINGS) is True
    assert digest_in_flight.is_set()
    return alert_state.load_state()


def test_an_event_settled_during_the_digest_send_survives(iso, monkeypatch):
    state = _race(iso, monkeypatch)
    daily = state["daily"]
    assert daily["digest_sent"] is True
    assert [e["disposition"] for e in daily["fired_log"]] == ["sent"]
    assert state["reported_once"] == {REPORT_ONCE[0]: REPORT_ONCE[1]}
    assert len(iso) == 2                                   # the event and the digest


def test_milestones_settled_during_the_digest_send_survive(iso, monkeypatch):
    state = _race(iso, monkeypatch, mark_milestones=("deepseek", [25]))
    assert state["milestones_fired"].get("deepseek") == [25]


def test_the_next_evening_folds_after_a_raced_first_send(iso, monkeypatch):
    # The end-to-end promise of 353a75260: one email, then the digest.
    _race(iso, monkeypatch)
    monkeypatch.setattr(events, "ASYNC_SEND", False)
    monkeypatch.setattr(events, "session_date", lambda: "2026-11-16")
    d = events.dispatch_event(
        "state_anomaly", "WARN", "State anomaly (cash): GPT-5.4", "b", {"model": "gpt"},
        dedup_key="state_cash:gpt", report_once=REPORT_ONCE, settings=SETTINGS)
    assert d == "continuing"


def test_digest_send_failure_still_writes_nothing(iso, monkeypatch):
    import src.data.market_data as md
    monkeypatch.setattr(md, "fetch_index_data", lambda lookback_days=5: {})
    monkeypatch.setattr(md, "is_market_open_today", lambda ref=None: True)
    monkeypatch.setattr(digest, "send_email", lambda *a, **k: False)
    assert digest.send_daily_digest(SUMMARY, SETTINGS) is False
    assert not alert_state.load_state()["daily"].get("digest_sent")
