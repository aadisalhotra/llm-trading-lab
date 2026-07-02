"""Shorting build — staged test harness (Phase A, v3, July-1 staged).

Runs the shorting path with shorting_enabled=TRUE injected per-call so PRODUCTION
config (config/settings.json -> portfolio_rules.shorting_enabled) stays FALSE and
nothing live changes. Covers the 8-point test plan:

  1. Open a short      — executes; negative shares, cash credited, negative value.
  2. Caps hold         — gross-short 20%, per-name 20%, 50-position rejected/resized.
  3. Net-short works   — longs≈0 + 20% short = net −20%, permitted; nothing below.
  4. Stop fires        — short up ≥10% above entry auto-covers.
  5. Drawdown halt     — short losses count toward the 30% halt.
  6. P&L correct       — short gains when price falls, loses when it rises.
  7. Conservation      — a full mixed sequence yields zero false discontinuities.
  8. Logs              — direction encoded; utilization/P&L/hit-rate; RQ2/3 by dir.

Plus a guard test that production (flag off) rejects shorts and is unchanged.

Run with: python -m pytest tests/test_shorting.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.adapters.base import BaseAdapter
from src.analytics.short_metrics import (
    short_pnl_summary,
    short_utilization,
    hit_rate_by_direction,
    gross_hhi,
)
from src.config_loader import load_settings
from src.execution.executor import Executor
from src.portfolio.audit import audit_trade_sequence
from src.portfolio.portfolio import Portfolio, Holding
from src.portfolio.risk import (
    validate_decisions,
    check_position_stops,
    check_portfolio_stop,
)

SHORT_ON = True  # injected per-call; never touches production config


def make_portfolio(cash=100_000.0, holdings=None, inception=100_000.0):
    return Portfolio(
        model_key="test_short",
        cash=cash,
        holdings=holdings or {},
        halted=False,
        inception_value=inception,
        inception_date="2026-04-08",
    )


def short_decision(ticker, weight, conf=7):
    return {"action": "SHORT", "ticker": ticker, "target_weight": weight,
            "confidence": conf, "reasoning": "test short"}


def cover_decision(ticker, weight, conf=7):
    return {"action": "COVER", "ticker": ticker, "target_weight": weight,
            "confidence": conf, "reasoning": "test cover"}


# ===================================================================== #
# 1. Open a short
# ===================================================================== #
def test_1_open_short_executes_credits_cash_negative_value():
    p = make_portfolio()
    ex = Executor()
    prices = {"AAPL": 200.0}
    accepted, violations = validate_decisions(
        [short_decision("AAPL", 0.10)], p, prices, shorting_enabled=SHORT_ON)
    assert len(accepted) == 1, violations
    results = ex.execute_decisions(p, accepted, prices)

    assert results[0].executed and results[0].side == "SHORT"
    h = p.holdings["AAPL"]
    assert h.shares < 0, "short must record negative share quantity"
    assert h.is_short and h.direction == "short"
    # 10% of 100k = 20k... no: target 0.10 of 100k equity = 10k gross short.
    assert abs(h.shares) == 50.0  # 10_000 / 200
    assert p.cash == 110_000.0, "short sale credits proceeds to cash"
    assert h.market_value(200.0) == -10_000.0, "short position value is negative"
    snap = p.snapshot(prices)
    assert abs(snap["short_exposure_pct"] - 0.10) < 1e-9
    assert abs(snap["total_value"] - 100_000.0) < 1e-6, "opening a short is equity-neutral"


# ===================================================================== #
# 2. Caps hold (gross-short 20%, per-name 20%, 50-position)
# ===================================================================== #
def test_2a_per_name_cap_resizes_short():
    p = make_portfolio()
    accepted, violations = validate_decisions(
        [short_decision("AAPL", 0.30)], p, {"AAPL": 200.0}, shorting_enabled=SHORT_ON)
    assert len(accepted) == 1
    assert accepted[0]["target_weight"] == 0.20, "per-name cap clamps to 20%"
    assert any(v.rule == "MAX_POSITION_WEIGHT" for v in violations)


def test_2b_gross_short_cap_resizes_then_rejects():
    p = make_portfolio()
    prices = {"AAPL": 200.0, "MSFT": 300.0, "NVDA": 150.0}
    decisions = [
        short_decision("AAPL", 0.15),
        short_decision("MSFT", 0.15),   # only 5% headroom left -> resized
        short_decision("NVDA", 0.05),   # no headroom -> rejected
    ]
    accepted, violations = validate_decisions(
        decisions, p, prices, shorting_enabled=SHORT_ON)
    assert len(accepted) == 2
    # AAPL keeps 0.15, MSFT resized to 0.05 (15% + 5% = 20% gross-short cap)
    assert abs(accepted[0]["target_weight"] - 0.15) < 1e-9
    assert abs(accepted[1]["target_weight"] - 0.05) < 1e-9
    assert any(v.rule == "EXPOSURE_CAP_RESIZED" for v in violations)
    assert any(v.rule == "EXPOSURE_CAP" for v in violations)


def test_2c_fiftieth_position_blocks_new_short():
    holdings = {f"H{i}": Holding(ticker=f"H{i}", shares=1, avg_cost=100) for i in range(50)}
    p = make_portfolio(cash=50_000, holdings=holdings, inception=100_000)
    prices = {f"H{i}": 100.0 for i in range(50)}
    prices["AAPL"] = 200.0
    accepted, violations = validate_decisions(
        [short_decision("AAPL", 0.05)], p, prices, shorting_enabled=SHORT_ON)
    assert len(accepted) == 0
    assert any(v.rule == "MAX_POSITIONS" for v in violations)


def test_2d_gross_exposure_cap_blocks_overlevered_long_plus_short():
    # 100% long already; a short would push gross to 120% (the limit), a further
    # long would breach it. Verify the gross cap is enforced on the long side too.
    holdings = {"AAPL": Holding("AAPL", shares=500, avg_cost=200)}  # 100k = 100% long
    p = make_portfolio(cash=0.0, holdings=holdings, inception=100_000)
    prices = {"AAPL": 200.0, "MSFT": 300.0}
    # Equity = 0 cash + 100k AAPL = 100k. Long headroom = 0 -> BUY rejected.
    buy = {"action": "BUY", "ticker": "MSFT", "target_weight": 0.10,
           "confidence": 7, "reasoning": "x"}
    accepted, violations = validate_decisions(
        [buy], p, prices, shorting_enabled=SHORT_ON)
    assert len(accepted) == 0
    assert any(v.rule == "EXPOSURE_CAP" for v in violations)


# ===================================================================== #
# 3. Net-short works (longs≈0 + 20% short = net −20%)
# ===================================================================== #
def test_3_net_short_to_floor_permitted_and_bounded():
    p = make_portfolio()
    ex = Executor()
    prices = {"AAPL": 200.0, "MSFT": 300.0}
    accepted, _ = validate_decisions(
        [short_decision("AAPL", 0.20)], p, prices, shorting_enabled=SHORT_ON)
    ex.execute_decisions(p, accepted, prices)
    snap = p.snapshot(prices)
    assert abs(snap["long_exposure_pct"]) < 1e-9
    assert abs(snap["short_exposure_pct"] - 0.20) < 1e-9
    assert abs(snap["net_exposure_pct"] - (-0.20)) < 1e-9, "net −20% is permitted"
    # Nothing may push below the −20% floor: another short has no headroom.
    accepted2, violations2 = validate_decisions(
        [short_decision("MSFT", 0.05)], p, prices, shorting_enabled=SHORT_ON)
    assert len(accepted2) == 0
    assert any(v.rule == "EXPOSURE_CAP" for v in violations2)


# ===================================================================== #
# 4. Stop fires (short up ≥10% above entry auto-covers)
# ===================================================================== #
def test_4_short_stop_fires_and_force_covers():
    holdings = {"AAPL": Holding("AAPL", shares=-50, avg_cost=200.0)}
    p = make_portfolio(cash=110_000, holdings=holdings)
    ex = Executor()
    # +9% — not yet triggered.
    assert check_position_stops(p, {"AAPL": 218.0}) == []
    # +10% — triggered.
    triggered = check_position_stops(p, {"AAPL": 220.0})
    assert triggered == ["AAPL"]
    results = ex.force_liquidate(p, triggered, {"AAPL": 220.0}, "POSITION_STOP")
    assert results[0].executed and results[0].side == "COVER"
    assert "AAPL" not in p.holdings, "short auto-covered by the stop"


def test_4b_long_and_short_stops_use_different_thresholds():
    # Long stop at 15% down, short stop at 10% up — same mechanism, inverted.
    holdings = {
        "AAPL": Holding("AAPL", shares=50, avg_cost=200.0),     # long
        "MSFT": Holding("MSFT", shares=-30, avg_cost=300.0),    # short
    }
    p = make_portfolio(holdings=holdings)
    # Long down 12% (not >15) and short up 8% (not >10): neither fires.
    assert check_position_stops(p, {"AAPL": 176.0, "MSFT": 324.0}) == []
    # Long down 16%, short up 11%: both fire.
    fired = set(check_position_stops(p, {"AAPL": 168.0, "MSFT": 333.0}))
    assert fired == {"AAPL", "MSFT"}


# ===================================================================== #
# 5. Drawdown halt — short losses count
# ===================================================================== #
def test_5_short_losses_count_toward_drawdown_halt():
    # Inception 100k. Short 1000 @ $100 (proceeds 100k -> cash 200k, equity 100k).
    # State constructed directly to isolate the halt mechanism (size exceeds the
    # 20% cap on purpose — we are testing that a short LOSS drives the halt).
    holdings = {"XYZ": Holding("XYZ", shares=-1000, avg_cost=100.0)}
    p = make_portfolio(cash=200_000, holdings=holdings, inception=100_000)
    # Price flat: equity 100k, no halt.
    assert not check_portfolio_stop(p, {"XYZ": 100.0})
    # Price +35% -> position MV −135k, equity 65k -> 35% drawdown -> halt.
    assert check_portfolio_stop(p, {"XYZ": 135.0}), "short loss must count toward the 30% halt"
    # Control: if the same name were FLAT it would not have lost — proving the
    # short liability is what produced the drawdown.
    flat = make_portfolio(cash=100_000, holdings={}, inception=100_000)
    assert not check_portfolio_stop(flat, {"XYZ": 135.0})


# ===================================================================== #
# 6. P&L correct (short gains when price falls, loses when it rises)
# ===================================================================== #
def test_6_short_pnl_sign_and_equity():
    h = Holding("AAPL", shares=-50, avg_cost=200.0)
    # Price falls to 180 -> short gains.
    assert h.unrealized_pl(180.0) > 0
    assert h.unrealized_pl_pct(180.0) > 0
    assert abs(h.unrealized_pl(180.0) - 1_000.0) < 1e-9          # (200-180)*50
    assert abs(h.unrealized_pl_pct(180.0) - 0.10) < 1e-9         # +10% position return
    # Price rises to 220 -> short loses.
    assert h.unrealized_pl(220.0) < 0
    assert h.unrealized_pl_pct(220.0) < 0

    # Equity curve: realized gain when covering lower than the short entry.
    p = make_portfolio()
    ex = Executor()
    accepted, _ = validate_decisions(
        [short_decision("AAPL", 0.10)], p, {"AAPL": 200.0}, shorting_enabled=SHORT_ON)
    ex.execute_decisions(p, accepted, {"AAPL": 200.0})           # short 50 @ 200
    # Cover fully at 180.
    ex.execute_decisions(p, [cover_decision("AAPL", 0.0)], {"AAPL": 180.0})
    assert "AAPL" not in p.holdings
    # Net cash gain = 50*(200-180) = 1000 -> equity 101k.
    assert abs(p.total_value({"AAPL": 180.0}) - 101_000.0) < 1e-6


# ===================================================================== #
# 7. Conservation audit clean over a mixed sequence
# ===================================================================== #
def test_7_conservation_audit_clean_with_shorts():
    events = [
        {"action": "BUY",   "ticker": "AAPL", "shares": 100, "price": 150.0},
        {"action": "SHORT", "ticker": "MSFT", "shares": 50,  "price": 300.0},
        {"action": "SHORT", "ticker": "NVDA", "shares": 80,  "price": 100.0},
        {"action": "SELL",  "ticker": "AAPL", "shares": 40,  "price": 160.0},
        {"action": "COVER", "ticker": "MSFT", "shares": 50,  "price": 280.0},
        {"action": "BUY",   "ticker": "AAPL", "shares": 20,  "price": 165.0},
        {"action": "COVER", "ticker": "NVDA", "shares": 80,  "price": 110.0},
    ]
    report = audit_trade_sequence(events, starting_cash=100_000.0)
    assert report.passed, report.violations
    assert not report.violations
    # Every step must be individually clean (no phantom discontinuity).
    assert all(step.ok for step in report.steps)
    # Hand-checked final cash:
    #  -15000 -? start 100000
    #  BUY AAPL 100@150  -> -15000 -> 85000
    #  SHORT MSFT 50@300 -> +15000 -> 100000
    #  SHORT NVDA 80@100 -> +8000  -> 108000
    #  SELL AAPL 40@160  -> +6400  -> 114400
    #  COVER MSFT 50@280 -> -14000 -> 100400
    #  BUY AAPL 20@165   -> -3300  -> 97100
    #  COVER NVDA 80@110 -> -8800  -> 88300
    assert abs(report.final_cash - 88_300.0) < 1e-6


def test_7b_audit_measures_real_cash_flows():
    # Prove the auditor actually measures conservation (right cash/MV deltas per
    # step), not that it rubber-stamps. A SHORT credits cash and opens a negative
    # liability of equal magnitude; a COVER debits cash and retires it.
    events = [
        {"action": "SHORT", "ticker": "AAPL", "shares": 50, "price": 200.0},
        {"action": "COVER", "ticker": "AAPL", "shares": 50, "price": 180.0},
    ]
    report = audit_trade_sequence(events, starting_cash=100_000.0)
    s_short, s_cover = report.steps
    assert s_short.cash_delta == +10_000.0 and s_short.mv_delta == -10_000.0
    assert s_short.discontinuity == 0.0
    assert s_cover.cash_delta == -9_000.0 and s_cover.mv_delta == +9_000.0
    assert s_cover.discontinuity == 0.0
    assert report.passed and abs(report.final_cash - 101_000.0) < 1e-6


# ===================================================================== #
# 8. Logs — direction encoded; utilization / P&L / hit-rate; RQ2/3 by direction
# ===================================================================== #
def test_8a_parser_encodes_direction_and_accepts_short_cover():
    raw = json.dumps({
        "overall_reasoning": "test",
        "decisions": [
            {"action": "SHORT", "ticker": "AAPL", "target_weight": 0.1, "confidence": 6},
            {"action": "COVER", "ticker": "MSFT", "target_weight": 0.0, "confidence": 5},
            {"action": "BUY",   "ticker": "NVDA", "target_weight": 0.1, "confidence": 7},
            {"action": "HOLD",  "ticker": "GOOGL", "target_weight": 0.0, "confidence": 5},
        ],
    })
    parsed = BaseAdapter._parse_response(raw)
    by_ticker = {d["ticker"]: d for d in parsed["decisions"]}
    assert by_ticker["AAPL"]["action"] == "SHORT" and by_ticker["AAPL"]["direction"] == "short"
    assert by_ticker["MSFT"]["action"] == "COVER" and by_ticker["MSFT"]["direction"] == "short"
    assert by_ticker["NVDA"]["direction"] == "long"
    assert by_ticker["GOOGL"]["direction"] == "flat"


def _synthetic_records():
    """Two closed shorts (one win, one loss) + one closed long, as decision-log rows."""
    def execu(side, ticker, shares, price, conf):
        return {"executed": True, "side": side, "ticker": ticker, "shares": shares,
                "fill_price": price, "decision": {"confidence": conf}}
    return [
        {"date": "2026-07-01", "executions": [
            execu("SHORT", "AAPL", 50, 200.0, 8),     # open winning short
            execu("BUY", "NVDA", 100, 100.0, 6),      # open long
        ], "portfolio_after": {"short_exposure_pct": 0.10}},
        {"date": "2026-07-02", "executions": [
            execu("COVER", "AAPL", 50, 180.0, 8),     # close short: +1000 (win)
            execu("SHORT", "MSFT", 20, 300.0, 4),     # open losing short
        ], "portfolio_after": {"short_exposure_pct": 0.16}},
        {"date": "2026-07-03", "executions": [
            execu("COVER", "MSFT", 20, 330.0, 4),     # close short: -600 (loss)
            execu("SELL", "NVDA", 100, 120.0, 6),     # close long: +2000 (win)
        ], "portfolio_after": {"short_exposure_pct": 0.0}},
    ]


def test_8b_short_pnl_and_hitrate():
    recs = _synthetic_records()
    summary = short_pnl_summary(recs)
    assert summary["n_closed_shorts"] == 2
    assert summary["wins"] == 1 and summary["losses"] == 1
    assert abs(summary["total_realized_pnl"] - 400.0) < 1e-6   # +1000 -600
    assert abs(summary["hit_rate"] - 0.5) < 1e-9


def test_8c_short_utilization():
    recs = _synthetic_records()
    util = short_utilization(recs, short_cap_pct=0.20)
    assert util["short_opens"] == 2
    assert util["covers"] == 2
    assert util["n_short_executions"] == 4
    # 4 short execs out of 6 total executed trades (metric rounds to 4 dp).
    assert abs(util["decision_share"] - (4 / 6)) < 1e-3
    assert abs(util["max_short_exposure_pct"] - 0.16) < 1e-9
    assert abs(util["max_utilization_vs_cap"] - 0.80) < 1e-9   # 0.16 / 0.20


def test_8d_outcomes_segmented_by_direction():
    recs = _synthetic_records()
    seg = hit_rate_by_direction(recs)
    assert seg["short"]["n"] == 2
    assert seg["long"]["n"] == 1
    assert seg["short"]["exploratory"] is True
    assert seg["long"]["exploratory"] is False
    assert abs(seg["short"]["hit_rate"] - 0.5) < 1e-9
    assert abs(seg["long"]["hit_rate"] - 1.0) < 1e-9


def test_8e_gross_hhi_counts_shorts():
    # A long-only book and the mirror book with one leg shorted have identical
    # gross concentration — shorts count on absolute weight.
    assert abs(gross_hhi([60.0, 40.0]) - gross_hhi([60.0, -40.0])) < 1e-12
    assert abs(gross_hhi([100.0]) - 1.0) < 1e-12


# ===================================================================== #
# Production-safety guard: flag OFF rejects shorts, long path unchanged
# ===================================================================== #
def test_production_flag_off_rejects_shorts():
    p = make_portfolio()
    accepted, violations = validate_decisions(
        [short_decision("AAPL", 0.10)], p, {"AAPL": 200.0}, shorting_enabled=False)
    assert len(accepted) == 0
    assert any(v.rule == "SHORTING_DISABLED" for v in violations)


def test_production_config_live_cap_invariants():
    # Post-July-1 regime: shorting is live, so this guard no longer pins the
    # flag off. It now pins the ratified risk caps that bound the short book,
    # so a stray config edit can't silently loosen them.
    settings = load_settings()
    pr = settings["portfolio_rules"]
    rc = settings["risk_controls"]
    assert pr["max_gross_short_pct"] == 0.20, \
        "short-exposure cap must stay at 20%"
    assert rc["stop_loss_short_pct"] == 0.10, \
        "short stop-loss must stay at 10%"
    assert pr["max_gross_exposure_pct"] == 1.20, \
        "gross-exposure ceiling must stay at 120%"


def test_long_path_unchanged_when_flag_off():
    # The long-only validator still accepts a normal buy exactly as before.
    p = make_portfolio()
    buy = {"action": "BUY", "ticker": "AAPL", "target_weight": 0.10,
           "confidence": 8, "reasoning": "x"}
    accepted, violations = validate_decisions(
        [buy], p, {"AAPL": 200.0}, shorting_enabled=False)
    assert len(accepted) == 1 and not violations
