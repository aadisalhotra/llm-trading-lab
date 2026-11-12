"""The price-validity guard (docs/registrations/price_validity_and_source_ruling.md).

2026-11-11: the vendor served NaN closes. A NaN passed every `price <= 0`
check, reached the prompt as "$nan", made sizing raise for every book holding
the name (each order an UNCLASSIFIED_SKIP), disabled both stops (NaN compares
False), and read as signal in the alert sweep. A missing price was quieter
still: valuation substituted average cost without a word.

The guard, per the ruling: validity is checked at ingestion; an unpriced ticker
is not tradable and renders through the existing NO DATA path; a decision on it
is a PRICE_UNAVAILABLE validator drop; each rejected price is a MISSING_PRICE
event; a held unpriced name is valued on a classified mark so the rest of the
book still trades; stops that cannot be evaluated say so; detectors check
finiteness before any threshold; EOD rows carry price_source and refuse NaN.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from src.data import price_validity as pv
from src.portfolio import risk
from src.portfolio.portfolio import Holding, Portfolio

NAN = float("nan")
ET = ZoneInfo("America/New_York")


def _frame(closes):
    n = len(closes)
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                         "Close": closes, "Volume": [1000] * n})


def _book(cash=1000.0, **held):
    p = Portfolio(model_key="book", cash=cash, inception_value=2000.0,
                  inception_date="2026-11-06")
    p.holdings = {t: Holding(ticker=t, shares=s, avg_cost=c) for t, (s, c) in held.items()}
    return p


# --------------------------------------------------------------------------
# Validity predicate: any real number type, finite, positive
# --------------------------------------------------------------------------

@pytest.mark.parametrize("x", [101.5, 100, np.float64(3.2), np.int64(7), np.float32(2.5),
                               Fraction(3, 2)])
def test_any_real_number_type_is_a_valid_price(x):
    assert pv.is_valid_price(x)


@pytest.mark.parametrize("x", [NAN, float("inf"), -float("inf"), np.float64("nan"), 0, 0.0,
                               -1.0, None, "101.5", True, Decimal("1.5")])
def test_invalid_prices_are_rejected(x):
    # Decimal is not numbers.Real; bool is excluded explicitly.
    assert not pv.is_valid_price(x)


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------

def test_nan_last_close_makes_the_ticker_unpriced_with_its_last_valid_bar_kept():
    ing = pv.ingest_market_data({"KO": _frame([96.0, 97.0, NAN]), "PG": _frame([160.0, 165.0])})
    assert ing.prices == {"PG": 165.0}
    assert ing.missing == {"KO": pv.REASON_NON_FINITE}
    assert ing.market_data["KO"].empty           # renders as NO DATA, never as nan
    assert ing.last_valid_bar == {"KO": 97.0}    # a classified mark, never "the price"


def test_empty_frame_is_unpriced_no_data():
    ing = pv.ingest_market_data({"DUK": pd.DataFrame(), "PG": _frame([165.0])})
    assert ing.missing == {"DUK": pv.REASON_NO_DATA}
    assert "DUK" not in ing.prices and "DUK" not in ing.last_valid_bar


def test_interior_nan_bar_is_dropped_so_no_nan_reaches_the_percent_columns():
    ing = pv.ingest_market_data({"KO": _frame([96.0, NAN, 97.0])})
    assert ing.prices == {"KO": 97.0}
    assert list(ing.market_data["KO"]["Close"]) == [96.0, 97.0]


def test_all_finite_frames_pass_through_as_the_same_objects():
    frames = {"KO": _frame([96.0, 97.0]), "PG": _frame([160.0, 165.0])}
    ing = pv.ingest_market_data(frames)
    assert all(ing.market_data[t] is frames[t] for t in frames)
    assert ing.missing == {}


def test_the_golden_fixture_is_untouched_and_the_frozen_prompt_still_matches():
    from src.logging import prompt_golden_fixture as fixture
    from src.logging import prompt_integrity
    frames = fixture.market_data()
    ing = pv.ingest_market_data(frames)
    assert ing.missing == {}
    assert all(ing.market_data[t] is frames[t] for t in frames)
    assert prompt_integrity.evaluate()["status"] == "match"


def test_an_unpriced_ticker_reaches_the_prompt_as_no_data_not_nan():
    from src.prompt_builder import _format_market_data_block
    ing = pv.ingest_market_data({"KO": _frame([96.0, NAN]), "PG": _frame([160.0, 165.0])})
    block = _format_market_data_block(ing.market_data)
    assert "nan" not in block.lower()
    ko_line = next(line for line in block.splitlines() if line.strip().startswith("KO"))
    assert "NO DATA" in ko_line


def test_missing_price_events_name_holders_and_marks():
    ing = pv.ingest_market_data({"KO": _frame([96.0, 97.0, NAN]), "DUK": pd.DataFrame(),
                                 "PG": _frame([165.0])})
    book = _book(KO=(2.0, 95.0), PG=(1.0, 160.0))
    marks = pv.holding_marks(book.holdings, ing)
    assert marks == {"KO": (97.0, pv.MARK_LAST_VALID_BAR)}
    events = pv.missing_price_events(ing, {"KO": ["book"], "PG": ["book"]}, {"book": marks})
    assert events == [
        {"event": "MISSING_PRICE", "ticker": "DUK", "reason": "no_data", "held_by": []},
        {"event": "MISSING_PRICE", "ticker": "KO", "reason": "non_finite", "held_by": ["book"],
         "valuation_mark": {"book": {"mark": 97.0, "source": "last_valid_bar"}}},
    ]


def test_a_held_name_with_no_valid_bar_at_all_is_marked_at_avg_cost_classified():
    ing = pv.ingest_market_data({"KO": pd.DataFrame()})
    assert pv.holding_marks(_book(KO=(2.0, 95.0)).holdings, ing) == {"KO": (95.0, pv.MARK_AVG_COST)}


def test_descriptor_writes_one_line_per_cycle_including_clean_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(pv, "DESCRIPTOR_DIR", tmp_path)
    when = datetime(2026, 11, 12, 9, 35, tzinfo=ET)
    pv.record_data_availability(cycle_id="c1", mode="intraday", run_date=when, n_expected=80, events=[])
    pv.record_data_availability(cycle_id="c2", mode="intraday", run_date=when, n_expected=80,
                                events=[{"event": "MISSING_PRICE", "ticker": "KO"}])
    rows = [json.loads(x) for x in (tmp_path / "2026-11.jsonl").read_text().splitlines()]
    assert [(r["cycle_id"], r["n_missing"]) for r in rows] == [("c1", 0), ("c2", 1)]


# --------------------------------------------------------------------------
# Valuation: classified marks, finite total value (containment)
# --------------------------------------------------------------------------

def test_a_nan_price_never_reaches_valuation():
    book = _book(KO=(2.0, 95.0), WMT=(3.0, 100.0))
    book.fallback_marks = {"KO": (97.0, pv.MARK_LAST_VALID_BAR)}
    prices = {"KO": NAN, "WMT": 101.0}
    assert book.total_value(prices) == pytest.approx(1000 + 2 * 97.0 + 3 * 101.0)
    snap = book.snapshot(prices)
    assert math.isfinite(snap["total_value"]) and math.isfinite(snap["cumulative_return"])
    assert snap["unpriced_holdings"] == {"KO": pv.MARK_LAST_VALID_BAR}


def test_an_ordinary_snapshot_carries_no_unpriced_key():
    book = _book(KO=(2.0, 95.0))
    assert "unpriced_holdings" not in book.snapshot({"KO": 96.0})


# --------------------------------------------------------------------------
# Validator: PRICE_UNAVAILABLE
# --------------------------------------------------------------------------

@pytest.mark.parametrize("shorting", [False, True], ids=["long-only", "shorting"])
def test_a_decision_on_an_unpriced_ticker_is_a_price_unavailable_drop(shorting):
    book = _book(cash=2000.0, KO=(2.0, 95.0), WMT=(3.0, 100.0))
    prices = {"WMT": 101.0, "MSFT": 500.0}            # KO unpriced
    decisions = [
        {"action": "SELL", "ticker": "KO", "target_weight": 0.0},
        {"action": "BUY", "ticker": "KO", "target_weight": 0.03},
        {"action": "HOLD", "ticker": "KO", "target_weight": 0.0},
        {"action": "BUY", "ticker": "MSFT", "target_weight": 0.03},
        {"action": "SELL", "ticker": "WMT", "target_weight": 0.05},
    ]
    accepted, violations = risk.validate_decisions(decisions, book, prices, shorting_enabled=shorting)
    assert [(v.decision_index, v.rule) for v in violations] == [
        (0, risk.PRICE_UNAVAILABLE), (1, risk.PRICE_UNAVAILABLE)]
    assert [(d["action"], d["ticker"]) for d in accepted] == [
        ("HOLD", "KO"), ("BUY", "MSFT"), ("SELL", "WMT")]


# --------------------------------------------------------------------------
# Stops: can't evaluate is classified, never a quiet pass
# --------------------------------------------------------------------------

def test_position_stop_is_not_tested_on_a_missing_or_nan_price_and_is_named():
    book = _book(KO=(2.0, 95.0), WMT=(3.0, 100.0), PG=(1.0, 200.0))
    prices = {"KO": NAN, "PG": 150.0}                  # WMT missing, PG down 25%
    assert risk.check_position_stops(book, prices) == ["PG"]
    assert risk.position_stops_unevaluable(book, prices) == ["KO", "WMT"]


def test_drawdown_halt_unchanged_when_every_name_is_priced():
    book = _book(cash=100.0, KO=(10.0, 100.0))         # inception 2000
    assert risk.evaluate_portfolio_stop(book, {"KO": 150.0}) == risk.STOP_CLEAR    # 1600: -20%
    assert risk.evaluate_portfolio_stop(book, {"KO": 120.0}) == risk.STOP_BREACH   # 1300: -35%
    assert risk.check_portfolio_stop(book, {"KO": 120.0}) is True


def test_drawdown_halt_is_proven_clear_with_an_unpriced_long_valued_at_zero():
    book = _book(cash=1500.0, KO=(1.0, 95.0), WMT=(3.0, 100.0))
    # Floor = 1500 + 3*101 = 1803 with KO at zero: -9.9%, provably clear of -30%.
    assert risk.evaluate_portfolio_stop(book, {"WMT": 101.0}) == risk.STOP_CLEAR


def test_drawdown_halt_unevaluable_when_the_zero_floor_does_not_clear_it():
    book = _book(cash=100.0, KO=(10.0, 150.0), WMT=(3.0, 100.0))
    # Floor = 100 + 303 = 403 with KO at zero; not provably clear. Never a quiet pass.
    assert risk.evaluate_portfolio_stop(book, {"KO": NAN, "WMT": 101.0}) == risk.STOP_UNEVALUABLE
    assert risk.check_portfolio_stop(book, {"KO": NAN, "WMT": 101.0}) is False


def test_drawdown_halt_unevaluable_with_an_unpriced_short():
    book = _book(cash=3000.0, KO=(-2.0, 95.0))
    assert risk.evaluate_portfolio_stop(book, {}) == risk.STOP_UNEVALUABLE


# --------------------------------------------------------------------------
# Executor containment: one bad price must not freeze the book
# --------------------------------------------------------------------------

def test_executor_sizes_other_names_normally_when_a_held_name_is_nan(monkeypatch):
    from src.execution import executor as ex
    from src.execution.broker import BrokerOrder, to_venue_symbol
    from src.config_loader import load_settings

    fills = []

    class Venue:
        base_url = "https://paper-api.alpaca.markets"
        fill_deadline_seconds = 300

        def __init__(self, *a, **k):
            pass

        def submit_market_order(self, *, ticker, shares, side, client_order_id):
            fills.append((ticker, side, shares))
            return BrokerOrder(client_order_id=client_order_id, broker_order_id="b",
                               symbol=to_venue_symbol(ticker), ticker=ticker, side=side,
                               requested_qty=shares, status="pending_new")

        def await_terminal(self, order, deadline_seconds=None):
            px = {"MSFT": 500.0, "WMT": 101.0}[order.ticker]
            return BrokerOrder(client_order_id=order.client_order_id, broker_order_id="b",
                               symbol=order.symbol, ticker=order.ticker, side=order.side,
                               requested_qty=order.requested_qty, status="filled",
                               filled_qty=order.requested_qty, filled_avg_price=px), False

        def place_and_confirm(self, *, ticker, shares, side, client_order_id, deadline_seconds=None):
            return self.await_terminal(self.submit_market_order(
                ticker=ticker, shares=shares, side=side, client_order_id=client_order_id))[0]

    monkeypatch.setattr(ex, "BrokerClient", Venue)
    settings = {**load_settings(), "mode": "broker_paper"}
    executor = ex.Executor("cyc", settings=settings, venue_books={"book"})

    book = _book(cash=2000.0, KO=(2.0, 95.0), WMT=(3.0, 100.0))
    book.fallback_marks = {"KO": (97.0, pv.MARK_LAST_VALID_BAR)}
    # A caller that did not ingest still hands over NaN: the book must not freeze.
    prices = {"KO": NAN, "WMT": 101.0, "MSFT": 500.0}
    results = executor.execute_decisions(book, [
        {"action": "BUY", "ticker": "MSFT", "target_weight": 0.05},
        {"action": "SELL", "ticker": "WMT", "target_weight": 0.0},
    ], prices, run_date="2026-11-12")
    assert [r.side for r in results] == ["BUY", "SELL"]
    assert all(r.executed for r in results)
    assert all(math.isfinite(s) and s > 0 for _, _, s in fills)
    assert ("WMT", "sell", 3.0) in fills


# --------------------------------------------------------------------------
# Alert detectors: finiteness before any threshold
# --------------------------------------------------------------------------

@pytest.fixture
def eod_history(monkeypatch):
    from src.alerts import events
    rows: dict[str, pd.DataFrame] = {}
    monkeypatch.setattr(events, "_enabled_model_keys", lambda s: list(rows))
    monkeypatch.setattr(events, "_display_name", lambda s, k: k)
    monkeypatch.setattr(events, "_eod_history", lambda k: rows[k])
    monkeypatch.setattr(events, "load_state", lambda: {})
    return rows


def _hist(values, cums):
    return pd.DataFrame({"date": [f"2026-11-{i + 1:02d}" for i in range(len(values))],
                         "total_value": values, "cumulative_return": cums})


def test_the_2026_11_11_alerts_do_not_fire_on_nan_rows(eod_history):
    from src.alerts import events
    vals = [2000 + i for i in range(11)]
    eod_history["book"] = _hist(vals + [NAN], [v / 2000 - 1 for v in vals] + [NAN])
    s = {"alerts": {"ath_min_days": 10, "milestone_step_pct": 5}}
    assert events.detect_negative_crossings(s) == []
    assert events.detect_new_ath(s) == []
    assert events.detect_milestones(s) == []


def test_finite_rows_still_alert_as_before(eod_history):
    from src.alerts import events
    vals = [2000 + i for i in range(10)] + [2200.0]
    eod_history["up"] = _hist(vals, [v / 2000 - 1 for v in vals])
    eod_history["down"] = _hist([2000.0, 1990.0], [0.0, -0.005])
    s = {"alerts": {"ath_min_days": 10, "milestone_step_pct": 5}}
    assert [x["title"] for x in events.detect_negative_crossings(s)] == ["down went negative"]
    assert [x["title"] for x in events.detect_new_ath(s)] == ["up hit a new all-time high"]
    assert [x["title"] for x in events.detect_milestones(s)] == ["up crossed +10%"]


def test_a_nan_prior_peak_does_not_make_every_close_an_all_time_high(eod_history):
    from src.alerts import events
    vals = [2000.0] * 5 + [NAN] + [2001.0] * 4 + [2000.5]
    eod_history["book"] = _hist(vals, [0.0] * 11)
    assert events.detect_new_ath({"alerts": {"ath_min_days": 10}}) == []


def test_oversized_trade_skips_a_nan_portfolio_value(monkeypatch):
    from src.alerts import events
    monkeypatch.setattr(events, "_enabled_model_keys", lambda s: ["book"])
    monkeypatch.setattr(events, "_display_name", lambda s, k: k)
    monkeypatch.setattr(events, "_read_today_trade_records", lambda k, d: [
        {"portfolio_after": {"total_value": NAN},
         "executions": [{"executed": True, "side": "BUY", "ticker": "KO", "notional": 50.0}]}])
    assert events.detect_oversized_trades({}, "2026-11-11") == []


# --------------------------------------------------------------------------
# EOD rows: price_source tag, never NaN
# --------------------------------------------------------------------------

@pytest.fixture
def perf_dir(tmp_path, monkeypatch):
    from src.logging import decision_log
    monkeypatch.setattr(decision_log, "PERFORMANCE_DIR", tmp_path)
    monkeypatch.setattr(decision_log, "_inception_date", lambda: "2026-04-09")
    return tmp_path


def _snap(total):
    return {"total_value": total, "cash": 100.0, "cash_pct": 0.05, "holdings": [],
            "cumulative_return": total / 2000 - 1, "halted": False,
            "inception_date": "2026-11-06"}


def test_eod_row_carries_price_source_vendor(perf_dir):
    from src.logging import decision_log
    decision_log.log_daily_snapshot("book", datetime(2026, 11, 12, 17, 5), _snap(2010.0), 760.0)
    row = json.loads((perf_dir / "book.jsonl").read_text())
    assert row["price_source"] == "vendor" and row["total_value"] == 2010.0


@pytest.mark.parametrize("total,bench", [(NAN, 760.0), (2010.0, NAN), (2010.0, None)])
def test_eod_row_with_a_non_finite_value_is_refused(perf_dir, total, bench):
    from src.logging import decision_log
    decision_log.log_daily_snapshot("book", datetime(2026, 11, 12, 17, 5), _snap(total), bench)
    assert not (perf_dir / "book.jsonl").exists()


# --------------------------------------------------------------------------
# Leaderboard snapshot: named by the run's session date
# --------------------------------------------------------------------------

def test_leaderboard_is_named_by_the_run_date_not_the_wall_clock():
    from src.dashboard.build_data import leaderboard_snapshot_name
    # The 2026-11-11 EOD run: 21:03 ET, which is 02:03Z on 11-12.
    late = datetime(2026, 11, 11, 21, 3, tzinfo=ET)
    assert leaderboard_snapshot_name(late) == "2026-11-11.json"
    assert leaderboard_snapshot_name(late.astimezone(ZoneInfo("UTC"))) == "2026-11-11.json"
