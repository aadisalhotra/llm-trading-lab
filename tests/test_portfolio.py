"""Unit tests for portfolio + risk logic. No network. Run with: python -m pytest tests/"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.portfolio.portfolio import Portfolio, Holding
from src.portfolio.risk import validate_decisions


def make_portfolio(cash=100_000.0, holdings=None):
    return Portfolio(
        model_key="test",
        cash=cash,
        holdings=holdings or {},
        halted=False,
        inception_value=cash,
        inception_date="2026-04-08",
    )


def test_buy_then_sell_full_exit():
    p = make_portfolio()
    p.buy("AAPL", 10, 200.0)
    assert "AAPL" in p.holdings
    assert abs(p.cash - 98_000.0) < 1e-6
    p.sell("AAPL", 10, 220.0)
    assert "AAPL" not in p.holdings
    assert abs(p.cash - 100_200.0) < 1e-6


def test_buy_averages_cost():
    p = make_portfolio()
    p.buy("MSFT", 10, 100.0)
    p.buy("MSFT", 10, 200.0)
    assert abs(p.holdings["MSFT"].avg_cost - 150.0) < 1e-6
    assert p.holdings["MSFT"].shares == 20


def test_total_value_with_holdings():
    p = make_portfolio(cash=50_000)
    p.holdings["NVDA"] = Holding(ticker="NVDA", shares=100, avg_cost=500)
    val = p.total_value({"NVDA": 600})
    assert val == 50_000 + 60_000


def test_risk_rejects_off_universe():
    p = make_portfolio()
    decisions = [{"action": "BUY", "ticker": "FAKE", "target_weight": 0.1, "confidence": 8, "reasoning": "x"}]
    accepted, violations = validate_decisions(decisions, p, {})
    assert len(accepted) == 0
    assert any(v.rule == "OFF_UNIVERSE" for v in violations)


def test_risk_clamps_oversize_weight():
    p = make_portfolio()
    decisions = [{"action": "BUY", "ticker": "AAPL", "target_weight": 0.50, "confidence": 8, "reasoning": "x"}]
    accepted, violations = validate_decisions(decisions, p, {"AAPL": 200})
    assert len(accepted) == 1
    assert accepted[0]["target_weight"] == 0.20
    assert any(v.rule == "MAX_POSITION_WEIGHT" for v in violations)


def test_risk_blocks_eleventh_position():
    holdings = {f"T{i}": Holding(ticker=f"T{i}", shares=1, avg_cost=100) for i in range(50)}
    p = Portfolio(model_key="t", cash=10_000, holdings=holdings, halted=False,
                  inception_value=20_000, inception_date="2026-04-08")
    # Use a real universe ticker that's not already held
    decisions = [{"action": "BUY", "ticker": "AAPL", "target_weight": 0.10, "confidence": 8, "reasoning": "x"}]
    accepted, violations = validate_decisions(decisions, p, {"AAPL": 200})
    assert len(accepted) == 0
    assert any(v.rule == "MAX_POSITIONS" for v in violations)


def test_halted_portfolio_rejects_all():
    p = make_portfolio()
    p.halted = True
    decisions = [{"action": "BUY", "ticker": "AAPL", "target_weight": 0.10, "confidence": 8, "reasoning": "x"}]
    accepted, violations = validate_decisions(decisions, p, {"AAPL": 200})
    assert len(accepted) == 0
    assert any(v.rule == "PORTFOLIO_HALTED" for v in violations)
