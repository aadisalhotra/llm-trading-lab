"""Hard programmatic risk controls.

Every decision the LLM produces flows through `validate_decisions` before execution.
The model can't bypass these. They mirror the rules in the prompt but are enforced
independently — defense in depth.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..config_loader import load_settings, universe_symbols
from .portfolio import Portfolio

logger = logging.getLogger("llmlab.risk")


@dataclass
class RiskViolation:
    decision_index: int
    rule: str
    detail: str


def validate_decisions(
    decisions: list[dict[str, Any]],
    portfolio: Portfolio,
    prices: dict[str, float],
) -> tuple[list[dict[str, Any]], list[RiskViolation]]:
    """Filter decisions against the rule book.

    Returns (accepted_decisions, violations). Violations are logged but other
    decisions still execute. Order matters: BUYs that would push the portfolio
    over the 10-position cap or 20% allocation cap are dropped.
    """
    settings = load_settings()
    rules = settings["portfolio_rules"]
    max_positions = int(rules["max_positions"])
    max_position_pct = float(rules["max_position_pct"])
    max_trades_per_day = int(rules["max_trades_per_day"])
    universe = set(universe_symbols())

    accepted: list[dict[str, Any]] = []
    violations: list[RiskViolation] = []

    if portfolio.halted:
        violations.append(RiskViolation(-1, "PORTFOLIO_HALTED", "Portfolio is halted; no trades accepted"))
        return [], violations

    # Project state forward as we accept decisions
    projected_holdings = set(portfolio.holdings.keys())
    trades_today = 0

    for i, d in enumerate(decisions):
        if trades_today >= max_trades_per_day:
            violations.append(RiskViolation(i, "DAILY_TRADE_CAP", f"Already at {max_trades_per_day} trades today"))
            continue

        ticker = d["ticker"]
        action = d["action"]
        target_weight = float(d.get("target_weight", 0))

        if ticker not in universe:
            violations.append(RiskViolation(i, "OFF_UNIVERSE", f"{ticker} not in universe"))
            continue

        if target_weight < 0 or target_weight > max_position_pct + 1e-9:
            violations.append(
                RiskViolation(i, "MAX_POSITION_WEIGHT",
                              f"{ticker} target_weight={target_weight:.4f} exceeds {max_position_pct}")
            )
            # clamp instead of reject
            d["target_weight"] = min(max(target_weight, 0.0), max_position_pct)

        if action == "HOLD":
            accepted.append(d)
            continue

        if action == "BUY":
            # If new position, check position cap
            if ticker not in projected_holdings:
                if len(projected_holdings) >= max_positions:
                    violations.append(
                        RiskViolation(i, "MAX_POSITIONS",
                                      f"Cannot open {ticker}; already at {max_positions}-position cap")
                    )
                    continue
                projected_holdings.add(ticker)
            accepted.append(d)
            trades_today += 1
            continue

        if action == "SELL":
            if ticker not in projected_holdings:
                violations.append(RiskViolation(i, "SELL_NOT_HELD", f"{ticker} not currently held"))
                continue
            # If full exit, remove from projected set
            if d.get("target_weight", 0) <= 1e-9:
                projected_holdings.discard(ticker)
            accepted.append(d)
            trades_today += 1
            continue

        violations.append(RiskViolation(i, "UNKNOWN_ACTION", f"Unrecognized action: {action}"))

    if violations:
        logger.warning("[%s] %d violations: %s", portfolio.model_key, len(violations),
                       [(v.rule, v.detail) for v in violations])
    return accepted, violations


def check_portfolio_stop(portfolio: Portfolio, prices: dict[str, float]) -> bool:
    """Returns True if portfolio drawdown breaches the hard stop and trading should halt."""
    settings = load_settings()
    stop_pct = float(settings["risk_controls"]["stop_loss_portfolio_pct"])
    if not settings["risk_controls"]["halt_on_stop_loss"]:
        return False
    if portfolio.inception_value <= 0:
        return False
    current = portfolio.total_value(prices)
    drawdown = 1.0 - (current / portfolio.inception_value)
    if drawdown >= stop_pct:
        logger.error("[%s] PORTFOLIO STOP TRIGGERED — drawdown %.2f%% ≥ %.2f%%",
                     portfolio.model_key, drawdown * 100, stop_pct * 100)
        return True
    return False


def check_position_stops(portfolio: Portfolio, prices: dict[str, float]) -> list[str]:
    """Returns list of tickers that breached the per-position stop and should be force-sold."""
    settings = load_settings()
    stop_pct = float(settings["risk_controls"]["stop_loss_position_pct"])
    triggered: list[str] = []
    for ticker, h in portfolio.holdings.items():
        price = prices.get(ticker)
        if price is None:
            continue
        loss = 1.0 - (price / h.avg_cost) if h.avg_cost else 0.0
        if loss >= stop_pct:
            logger.warning("[%s] Position stop on %s: loss %.2f%% ≥ %.2f%%",
                           portfolio.model_key, ticker, loss * 100, stop_pct * 100)
            triggered.append(ticker)
    return triggered
