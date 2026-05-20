"""Unit tests for the email alerting layer. Fully offline — SMTP and all
network calls are stubbed. Run with: python -m pytest tests/test_alerts.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.alerts import alert_state, events, digest


SETTINGS = {
    "experiment_start_date": "2026-04-09",
    "experiment_end_date": "2027-11-01",
    "phase": "Phase A - Paper Trading",
    "mode": "paper",
    "portfolio_rules": {"max_positions": 50, "max_position_pct": 0.20},
    "alert_recipients": ["a@example.com", "b@example.com"],
    "alerts": {
        "enabled": True,
        "max_event_alerts_per_day": 3,
        "milestone_step_pct": 5,
        "news_sentiment_threshold": 0.7,
        "news_min_holders": 4,
        "oversized_trade_pct": 0.15,
        "ath_min_days": 10,
    },
    "models": {
        "claude": {"display_name": "Claude", "enabled": True},
        "gpt": {"display_name": "GPT", "enabled": True},
    },
}


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate alert state + email transport for a test."""
    monkeypatch.setattr(alert_state, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(events, "TRADES_DIR", tmp_path / "trades")
    (tmp_path / "trades").mkdir()

    sent: list[dict] = []

    def fake_send(subject, html, recipients=None, **kwargs):
        sent.append({"subject": subject, "html": html, "kwargs": kwargs})
        return True

    monkeypatch.setattr(events, "send_email", fake_send)
    monkeypatch.setattr(digest, "send_email", fake_send)
    return sent


def test_warn_emails_info_does_not(iso):
    d_warn = events.dispatch_event("api_failure", "WARN", "t", "b",
                                   {"model": "claude"}, settings=SETTINGS)
    d_info = events.dispatch_event("model_transition", "INFO", "t", "b",
                                   {"model": "claude"}, settings=SETTINGS)
    assert d_warn == "sent"
    assert d_info == "logged"      # INFO is log-only by default
    assert len(iso) == 1


def test_dedup_same_key_once_per_day(iso):
    d1 = events.dispatch_event("api_failure", "WARN", "t", "b",
                               {"model": "claude"},
                               dedup_key="api_failure:claude", settings=SETTINGS)
    d2 = events.dispatch_event("api_failure", "WARN", "t2", "b2",
                               {"model": "claude"},
                               dedup_key="api_failure:claude", settings=SETTINGS)
    assert d1 == "sent"
    assert d2 == "deduped"
    assert len(iso) == 1


def test_cap_then_overflow(iso):
    # cap is 3 in SETTINGS
    disps = [
        events.dispatch_event("oversized_trade", "WARN", f"t{i}", "b",
                              {"model": "claude"}, dedup_key=f"k{i}", settings=SETTINGS)
        for i in range(5)
    ]
    assert disps[:3] == ["sent", "sent", "sent"]
    assert disps[3:] == ["overflow", "overflow"]
    assert len(iso) == 3  # only 3 emails actually sent

    state = alert_state.load_state()
    assert len(state["daily"]["overflow"]) == 2
    assert state["daily"]["sent_count"] == 3


def test_milestone_ledger_marks_once_ever(iso):
    events.dispatch_event("milestone", "INFO", "GPT +10%", "b",
                          {"model": "gpt"}, email=True,
                          dedup_key="milestone:gpt:10",
                          mark_milestones=("gpt", [5, 10]), settings=SETTINGS)
    state = alert_state.load_state()
    assert state["milestones_fired"]["gpt"] == [5, 10]
    assert len(iso) == 1


def test_send_failure_does_not_consume_dedup(iso, monkeypatch):
    def failing_send(*a, **k):
        return False
    monkeypatch.setattr(events, "send_email", failing_send)
    d1 = events.dispatch_event("api_failure", "WARN", "t", "b",
                               {"model": "gpt"}, dedup_key="api_failure:gpt",
                               settings=SETTINGS)
    assert d1 == "send_failed"
    # A later retry (now succeeding) should go through, not be deduped.
    monkeypatch.setattr(events, "send_email", lambda *a, **k: True)
    d2 = events.dispatch_event("api_failure", "WARN", "t", "b",
                               {"model": "gpt"}, dedup_key="api_failure:gpt",
                               settings=SETTINGS)
    assert d2 == "sent"


def test_detect_oversized_trades(iso):
    # Write a synthetic trade-log row: a $20k order on a $100k book = 20% > 15%.
    trade_dir = events.TRADES_DIR
    rec = {
        "date": "2026-05-20",
        "timestamp": "2026-05-20T14:00:00",
        "portfolio_after": {"total_value": 100_000.0},
        "executions": [
            {"executed": True, "side": "BUY", "ticker": "NVDA",
             "notional": 20_000.0, "fill_price": 100.0, "timestamp": "2026-05-20T14:00:00"},
            {"executed": True, "side": "BUY", "ticker": "AAPL",
             "notional": 5_000.0, "fill_price": 200.0, "timestamp": "2026-05-20T14:00:00"},
        ],
    }
    with open(trade_dir / "claude_2026-05.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")

    specs = events.detect_oversized_trades(SETTINGS, "2026-05-20")
    assert len(specs) == 1
    assert specs[0]["kind"] == "oversized_trade"
    assert "NVDA" in specs[0]["title"]


def test_digest_builds_offline(iso, monkeypatch):
    # Avoid the yfinance index fetch.
    import src.data.market_data as md
    monkeypatch.setattr(md, "fetch_index_data", lambda lookback_days=5: {})

    summary = {
        "date": "2026-05-20",
        "mode": "paper",
        "models": [
            {"model_key": "claude", "status": "OK", "trades_today": 4,
             "total_value": 111000.0, "cumulative_return": 0.11},
            {"model_key": "gpt", "status": "OK", "trades_today": 6,
             "total_value": 116000.0, "cumulative_return": 0.16},
        ],
        "leaderboard": [
            {"model_key": "gpt", "rank": 1, "daily_pnl_pct": 0.012,
             "cumulative_return": 0.16, "alpha_vs_spy": 0.07,
             "halted": False, "last_api_success": True},
            {"model_key": "claude", "rank": 2, "daily_pnl_pct": -0.004,
             "cumulative_return": 0.11, "alpha_vs_spy": 0.03,
             "halted": False, "last_api_success": True},
        ],
    }
    subject, html = digest.build_digest(summary, SETTINGS)
    assert subject == "LLM Trading Lab — Daily Digest 2026-05-20"
    assert "Daily Digest" in html
    assert "GPT" in html and "Claude" in html
    assert "Total trades today" in html
    assert "Day " in html and "/ 572" in html       # dynamic day counter
    assert "All systems nominal" in html             # no halts / failures


def test_digest_health_flags_halt(iso, monkeypatch):
    import src.data.market_data as md
    monkeypatch.setattr(md, "fetch_index_data", lambda lookback_days=5: {})
    summary = {
        "date": "2026-05-20",
        "models": [{"model_key": "claude", "status": "OK", "trades_today": 0}],
        "leaderboard": [
            {"model_key": "claude", "rank": 1, "daily_pnl_pct": 0.0,
             "cumulative_return": 0.0, "alpha_vs_spy": 0.0,
             "halted": True, "last_api_success": True},
        ],
    }
    _, html = digest.build_digest(summary, SETTINGS)
    assert "HALTED" in html
    assert "All systems nominal" not in html


def test_digest_send_is_idempotent(iso, monkeypatch):
    import src.data.market_data as md
    monkeypatch.setattr(md, "fetch_index_data", lambda lookback_days=5: {})
    monkeypatch.setattr(md, "is_market_open_today", lambda ref=None: True)
    summary = {
        "date": "2026-05-20",
        "models": [{"model_key": "claude", "status": "OK", "trades_today": 1}],
        "leaderboard": [{"model_key": "claude", "rank": 1, "daily_pnl_pct": 0.0,
                         "cumulative_return": 0.0, "alpha_vs_spy": 0.0,
                         "halted": False, "last_api_success": True}],
    }
    first = digest.send_daily_digest(summary, SETTINGS)
    second = digest.send_daily_digest(summary, SETTINGS)
    assert first is True
    assert second is False          # already sent today
    assert len(iso) == 1
