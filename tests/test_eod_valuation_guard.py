"""The end-of-day valuation guard (2026-11-11 NaN-close incident).

On 2026-11-11 the vendor served the day's daily bar with Close = NaN. The EOD
run valued every book at NaN, wrote NaN performance and intraday rows, a NaN
daily report and leaderboard, and its threshold sweep emailed six false "went
negative" alerts and a false all-time high (reverted in c67c15418/ed7d825ce).

The guard: if any close the valuation needs (every held ticker, plus the
benchmark) is missing or non-finite, the EOD run writes no valuation output,
runs no threshold sweep, emits exactly one CRITICAL, and still does its
housekeeping (book save with the run counted, level-2 reconciliation, the
session descriptor, the budget check) and exits 0 so the workflow commits it.

These tests drive the real run_pipeline and run_one_model; only I/O is stubbed.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import src.pipeline as pipeline
from src.portfolio.portfolio import Holding, IntradayState, Portfolio

ET = ZoneInfo("America/New_York")
WHEN = datetime(2026, 11, 12, 17, 5, tzinfo=ET)  # chain EOD slot, a trading day
NAN = float("nan")

# Two books. A holds KO and WMT; B holds PG. The benchmark is SPY. MSFT is in
# the universe but held by nobody.
HOLDINGS = {"book_a": {"KO": (2.0, 95.0), "WMT": (3.0, 100.0)},
            "book_b": {"PG": (1.5, 160.0)}}
CLEAN = {"KO": 96.0, "WMT": 101.0, "PG": 165.0, "SPY": 750.0, "MSFT": 520.0}


def _frame(close):
    return pd.DataFrame({"Open": [close], "High": [close], "Low": [close],
                         "Close": [close], "Volume": [1]})


def _book(key):
    p = Portfolio(model_key=key, cash=100.0, inception_value=2000.0,
                  inception_date="2026-11-06")
    p.holdings = {t: Holding(ticker=t, shares=s, avg_cost=c)
                  for t, (s, c) in HOLDINGS[key].items()}
    p.intraday = IntradayState(session_date="2026-11-12", runs_today=12)
    return p


@pytest.fixture
def eod(monkeypatch):
    """Run the EOD pass with the given closes; return what it did."""
    def run(closes):
        calls = {k: [] for k in ("alerts", "daily_rows", "intraday_rows", "saved",
                                 "recon", "descriptor", "dashboard", "report",
                                 "leaderboard", "summary", "budget", "index")}
        books = {k: _book(k) for k in HOLDINGS}

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return WHEN if tz is None else WHEN.astimezone(tz)

        settings = {
            "mode": "broker_paper", "phase": "test", "benchmark_ticker": "SPY",
            "prompt_version": "v4",
            "models": {k: {"provider": "fake", "model": f"fake-{k}", "enabled": True}
                       for k in HOLDINGS},
            "portfolio_rules": {"max_trades_per_day": 150},
        }
        sp = monkeypatch.setattr
        sp(pipeline, "datetime", Frozen)
        sp(pipeline, "load_env", lambda: None)
        sp(pipeline, "ensure_dirs", lambda: None)
        sp(pipeline, "configure_logging", lambda: pipeline.logging.getLogger("t"))
        sp(pipeline, "load_settings", lambda: settings)
        sp(pipeline, "alert_preflight", lambda **k: None)
        sp(pipeline, "is_market_open_today", lambda *_: True)
        sp(pipeline, "_eod_skip_reason", lambda *_: None)
        sp(pipeline, "universe_symbols", lambda: ["KO", "WMT", "PG", "MSFT"])
        sp(pipeline, "fetch_universe_data",
           lambda symbols: {s: (pd.DataFrame() if closes.get(s, "missing") == "missing"
                                else _frame(closes[s])) for s in symbols})
        sp(pipeline, "fetch_news", lambda **k: {})
        sp(pipeline, "scan_macro_events", lambda **k: None)
        sp(pipeline, "load_universe", lambda: {"tickers": []})
        sp(pipeline, "load_portfolio", lambda k: books[k])
        sp(pipeline, "save_portfolio", lambda p: calls["saved"].append(
            (p.model_key, p.intraday.runs_today)))
        sp(pipeline, "check_position_stops", lambda *_: [])
        sp(pipeline, "get_adapter", lambda *a: SimpleNamespace())
        sp(pipeline, "hash_inputs", lambda *_: "h")
        sp(pipeline, "log_intraday_snapshot", lambda **k: calls["intraday_rows"].append(k["model_key"]))
        sp(pipeline, "log_daily_snapshot", lambda mk, *a, **k: calls["daily_rows"].append((mk, a[1]["total_value"])))
        sp(pipeline, "Executor", lambda: SimpleNamespace(broker_enabled=True, broker="fake-broker",
                                                         cycle_id="cyc", settings=settings))
        sp(pipeline, "CycleBarrier", lambda **k: None)
        sp(pipeline, "rotation_tick", lambda *_: 0)
        sp(pipeline, "venue_book_keys", lambda keys, s: keys)
        sp(pipeline, "run_post_cycle_reconciliation", lambda *a: calls["recon"].append(a[1]))
        sp(pipeline, "write_session_descriptor", lambda *a: calls["descriptor"].append(a[0]))
        sp(pipeline, "build_dashboard_payload", lambda **k: calls["dashboard"].append(1))
        sp(pipeline, "fetch_index_data", lambda **k: calls["index"].append(1) or {})
        sp(pipeline, "generate_daily_report", lambda **k: calls["report"].append(1))
        sp(pipeline, "build_leaderboard", lambda keys: calls["leaderboard"].append(1) or [])
        sp(pipeline, "send_daily_summary", lambda s: calls["summary"].append(s))
        sp(pipeline, "compute_budget_status", lambda s: calls["budget"].append(1) or
           {"any_critical": False, "any_warn": False, "providers": {}, "warn_threshold_pct": 0.8})
        sp(pipeline, "send_heartbeat", lambda **k: None)
        sp(pipeline, "send_alert", lambda sev, title, *a, **k: calls["alerts"].append(
            (sev, title, k.get("kind"))))
        rc = pipeline.run_pipeline(mode="end-of-day")
        return rc, calls
    return run


def _assert_aborted(rc, calls, n_bad):
    assert rc == 0                                   # exits 0 so the workflow commits
    # No valuation output of any kind
    assert calls["daily_rows"] == []
    assert calls["intraday_rows"] == []
    assert calls["dashboard"] == []
    assert calls["report"] == [] and calls["index"] == []
    assert calls["leaderboard"] == []
    assert calls["summary"] == []                    # digest + its threshold sweep
    # Exactly one alert, and it is the CRITICAL abort
    assert calls["alerts"] == [(
        "CRITICAL",
        f"End-of-day valuation aborted: non-finite closes for {n_bad} names",
        "eod_valuation_aborted")]
    # Housekeeping still ran
    assert sorted(calls["saved"]) == [("book_a", 13), ("book_b", 13)]
    assert calls["recon"] == ["cyc"]
    assert calls["descriptor"] == ["2026-11-12"]
    assert calls["budget"] == [1]


def test_nan_close_on_a_held_name_aborts_valuation_but_keeps_housekeeping(eod):
    rc, calls = eod({**CLEAN, "KO": NAN})
    _assert_aborted(rc, calls, 1)


def test_the_2026_11_11_shape_every_close_nan(eod):
    rc, calls = eod({t: NAN for t in CLEAN})
    _assert_aborted(rc, calls, 4)                    # KO, WMT, PG, SPY; MSFT unheld


def test_missing_close_on_a_held_name_aborts(eod):
    rc, calls = eod({**CLEAN, "PG": "missing"})
    _assert_aborted(rc, calls, 1)


def test_nan_benchmark_aborts(eod):
    rc, calls = eod({**CLEAN, "SPY": NAN})
    _assert_aborted(rc, calls, 1)


@pytest.mark.parametrize("closes", [CLEAN, {**CLEAN, "MSFT": NAN}],
                         ids=["clean", "nan-on-unheld-name"])
def test_clean_valuation_runs_the_full_eod_unchanged(eod, closes):
    rc, calls = eod(closes)
    assert rc == 0
    assert calls["alerts"] == []
    assert sorted(calls["daily_rows"]) == [
        ("book_a", pytest.approx(100.0 + 2 * 96.0 + 3 * 101.0)),
        ("book_b", pytest.approx(100.0 + 1.5 * 165.0))]
    assert sorted(calls["intraday_rows"]) == ["book_a", "book_b"]
    assert calls["dashboard"] == [1]
    assert calls["report"] == [1] and calls["index"] == [1]
    assert calls["leaderboard"] == [1]
    assert len(calls["summary"]) == 1
    assert all(m["status"] == "OK" for m in calls["summary"][0]["models"])
    assert sorted(calls["saved"]) == [("book_a", 13), ("book_b", 13)]
    assert calls["recon"] == ["cyc"] and calls["descriptor"] == ["2026-11-12"]
    assert calls["budget"] == [1]
