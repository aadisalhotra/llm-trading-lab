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


# ---- Trigger #11: market-wide events --------------------------------------

def test_match_macro_category_and_false_positive_guards():
    m = events._match_macro_category
    assert m("Russia launches invasion, missiles strike Kyiv") == "Geopolitical shock"
    assert m("Fed announces emergency rate cut in unscheduled meeting") == "Monetary policy surprise"
    assert m("Wall Street crashes, circuit breaker halts trading") == "Macro crisis"
    assert m("Moody downgrades US sovereign credit rating") == "Macro crisis"
    assert m("WHO declares a global pandemic") == "Systemic event"
    assert m("US economy enters recession as GDP contracts") == "Severe economic data"
    # Substring false positives must NOT match.
    assert m("Coupon startup raises a funding round") is None      # 'coup' in 'coupon'
    assert m("Warner Bros forward guidance warms investors") is None  # 'war' in warner/forward/warms
    assert m("Apple unveils a new iPhone with a faster chip") is None


def _macro_news(title, summary="", source="Test", dt="2026-05-20T14:00:00Z"):
    return {"macro": [{"title": title, "summary": summary, "source": source, "datetime": dt}]}


def _stub_macro(monkeypatch, tmp_path, portfolios, score):
    """Isolate the detector: empty on-disk macro caches, fixed positioning,
    and a deterministic sentiment score (no VADER/network in the unit path)."""
    monkeypatch.setattr(events, "NEWS_CACHE_DIR", tmp_path / "news_cache")
    monkeypatch.setattr(events, "_read_dashboard_portfolios", lambda: portfolios)
    monkeypatch.setattr("src.data.sentiment.score_macro_headline", lambda text: score)


def test_detect_macro_event_fires_with_positioning(iso, monkeypatch, tmp_path):
    _stub_macro(monkeypatch, tmp_path, [
        {"model_key": "claude", "cash": 50000.0, "total_value": 100000.0,
         "holdings": [{"ticker": "NVDA"}, {"ticker": "AAPL"}]},
        {"model_key": "gpt", "cash": 30000.0, "total_value": 100000.0,
         "holdings": [{"ticker": "NVDA"}]},
    ], score=-0.92)

    specs = events.detect_macro_market_events(
        SETTINGS, news_data=_macro_news("Russia launches full-scale invasion", "missiles strike"))
    assert len(specs) == 1
    s = specs[0]
    assert s["kind"] == "macro_event"
    assert s["severity"] == "CRITICAL"
    assert s["title"] == "Major Market Event Detected"          # exact subject after [ALERT]
    assert s["dedup_key"] == "macro_event:Geopolitical shock"
    assert s["context"]["numbers"]["Category"] == "Geopolitical shock"
    # Positioning: total cash (50k+30k)/(200k) = 40%; NVDA held by 2/2, AAPL 1/2.
    assert "40.0%" in s["body"]
    assert "NVDA (2/2)" in s["body"]
    assert "all model portfolios" in s["body"]


def test_macro_event_requires_keyword_and_sentiment(iso, monkeypatch, tmp_path):
    # Keyword matches but sentiment is too weak → no fire.
    _stub_macro(monkeypatch, tmp_path, [], score=-0.4)
    assert events.detect_macro_market_events(
        SETTINGS, news_data=_macro_news("Market crash feared by a few analysts")) == []

    # Strong sentiment but no high-severity keyword → no fire.
    _stub_macro(monkeypatch, tmp_path, [], score=-0.95)
    assert events.detect_macro_market_events(
        SETTINGS, news_data=_macro_news("Beloved CEO retires after a stellar decade")) == []


def test_scan_macro_events_dedup_once_per_category_per_day(iso, monkeypatch, tmp_path):
    _stub_macro(monkeypatch, tmp_path, [
        {"model_key": "claude", "cash": 0.0, "total_value": 100000.0, "holdings": [{"ticker": "NVDA"}]},
    ], score=-0.9)
    news = _macro_news("Stocks crash as circuit breaker halts Wall Street trading", source="CNBC")

    d1 = events.scan_macro_events(news_data=news, settings=SETTINGS)
    d2 = events.scan_macro_events(news_data=news, settings=SETTINGS)
    assert d1.get("sent") == 1
    assert d2.get("deduped") == 1            # same category — fires once per day
    assert len(iso) == 1
    assert iso[0]["subject"] == "[ALERT] Major Market Event Detected"


def test_macro_event_distinct_categories_both_fire(iso, monkeypatch, tmp_path):
    _stub_macro(monkeypatch, tmp_path, [
        {"model_key": "claude", "cash": 0.0, "total_value": 100000.0, "holdings": []},
    ], score=-0.9)
    news = {"macro": [
        {"title": "Russia launches invasion with missile strikes", "summary": "", "source": "AP", "datetime": "t"},
        {"title": "Fed makes emergency rate cut in unscheduled meeting", "summary": "", "source": "WSJ", "datetime": "t"},
    ]}
    specs = events.detect_macro_market_events(SETTINGS, news_data=news)
    cats = {s["dedup_key"] for s in specs}
    assert cats == {"macro_event:Geopolitical shock", "macro_event:Monetary policy surprise"}


def test_macro_event_real_vader_gate(iso, monkeypatch, tmp_path):
    # Exercises the real crisis-augmented VADER scorer (no stub) to guard the
    # lexicon + threshold against regressions. Skipped if the VADER lexicon
    # isn't available offline, so CI stays network-free.
    try:
        import nltk
        nltk.data.find("sentiment/vader_lexicon.zip")
    except Exception:
        pytest.skip("VADER lexicon not available offline")
    monkeypatch.setattr(events, "NEWS_CACHE_DIR", tmp_path / "news_cache")
    monkeypatch.setattr(events, "_read_dashboard_portfolios",
                        lambda: [{"model_key": "claude", "cash": 0.0,
                                  "total_value": 100000.0, "holdings": []}])
    fire = _macro_news("Wall Street crashes as circuit breaker halts trading", "Stocks plunge 8%")
    routine = _macro_news("S&P 500 edges higher on light volume", "Quiet pre-holiday session")
    assert len(events.detect_macro_market_events(SETTINGS, news_data=fire)) == 1
    assert events.detect_macro_market_events(SETTINGS, news_data=routine) == []


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
