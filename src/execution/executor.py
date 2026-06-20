"""Trade executor — paper or live, switched by config.

In paper mode, fills are simulated against the latest market price (no slippage
modeling for now — added later if needed). In live mode, orders submit through
Alpaca's REST API and the resulting fill price is recorded.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..config_loader import load_settings
from ..portfolio import Portfolio

logger = logging.getLogger("llmlab.execution")


@dataclass
class ExecutionResult:
    decision: dict[str, Any]
    executed: bool
    side: str          # BUY / SELL / SHORT / COVER / HOLD / SKIP
    ticker: str
    shares: float
    fill_price: float
    notional: float
    order_id: str = ""
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class Executor:
    """Single executor that handles both paper and live modes."""

    def __init__(self) -> None:
        self.settings = load_settings()
        self.mode = self.settings["mode"]
        self._alpaca_client = None
        if self.mode == "live":
            self._alpaca_client = self._init_alpaca()

    def _init_alpaca(self):
        try:
            from alpaca.trading.client import TradingClient
        except ImportError as e:
            raise RuntimeError("alpaca-py not installed") from e
        api_key = os.getenv("ALPACA_API_KEY")
        secret = os.getenv("ALPACA_SECRET_KEY")
        if not api_key or not secret:
            raise RuntimeError("Alpaca credentials not set")
        # paper=False for live mode
        return TradingClient(api_key, secret, paper=False)

    # ----- public API -----

    def execute_decisions(
        self,
        portfolio: Portfolio,
        decisions: list[dict[str, Any]],
        prices: dict[str, float],
    ) -> list[ExecutionResult]:
        results: list[ExecutionResult] = []
        for d in decisions:
            try:
                r = self._execute_one(portfolio, d, prices)
            except Exception as e:
                logger.exception("Execution failed for %s: %s", d, e)
                r = ExecutionResult(
                    decision=d, executed=False, side="SKIP",
                    ticker=d.get("ticker", ""), shares=0, fill_price=0, notional=0,
                    error=str(e),
                )
            results.append(r)
        return results

    def force_liquidate(
        self,
        portfolio: Portfolio,
        tickers: list[str],
        prices: dict[str, float],
        reason: str = "RISK_STOP",
    ) -> list[ExecutionResult]:
        """Force-sell specified tickers (used by risk stops)."""
        results: list[ExecutionResult] = []
        for ticker in tickers:
            if ticker not in portfolio.holdings:
                continue
            h = portfolio.holdings[ticker]
            price = prices.get(ticker, h.avg_cost)
            # A short is closed by covering (buy-back); a long by selling.
            is_short = h.is_short
            shares = abs(h.shares)
            side = "COVER" if is_short else "SELL"
            action = "COVER" if is_short else "SELL"
            d = {
                "action": action,
                "ticker": ticker,
                "target_weight": 0.0,
                "confidence": 10,
                "reasoning": f"Forced liquidation: {reason}",
            }
            try:
                if is_short:
                    portfolio.cover(ticker, shares, price)
                else:
                    portfolio.sell(ticker, shares, price)
                results.append(ExecutionResult(
                    decision=d, executed=True, side=side, ticker=ticker,
                    shares=shares, fill_price=price, notional=shares * price,
                    order_id=f"FORCED_{reason}",
                ))
            except Exception as e:
                results.append(ExecutionResult(
                    decision=d, executed=False, side="SKIP", ticker=ticker,
                    shares=0, fill_price=0, notional=0, error=str(e),
                ))
        return results

    # ----- internals -----

    def _execute_one(
        self,
        portfolio: Portfolio,
        decision: dict[str, Any],
        prices: dict[str, float],
    ) -> ExecutionResult:
        action = decision["action"]
        ticker = decision["ticker"]

        if action == "HOLD":
            return ExecutionResult(decision=decision, executed=True, side="HOLD",
                                   ticker=ticker, shares=0, fill_price=0, notional=0,
                                   order_id="HOLD")

        price = prices.get(ticker)
        if price is None or price <= 0:
            return ExecutionResult(decision=decision, executed=False, side="SKIP",
                                   ticker=ticker, shares=0, fill_price=0, notional=0,
                                   error="No price available")

        target_weight = float(decision.get("target_weight", 0))
        total_value = portfolio.total_value(prices)

        if action == "BUY":
            target_notional = total_value * target_weight
            current_notional = portfolio.holdings[ticker].market_value(price) if ticker in portfolio.holdings else 0
            delta_notional = target_notional - current_notional
            if delta_notional <= 0:
                return ExecutionResult(decision=decision, executed=True, side="HOLD",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       order_id="ALREADY_AT_TARGET")
            # Cap at available cash
            delta_notional = min(delta_notional, portfolio.cash)
            if delta_notional < price:  # can't even buy 1 fractional share meaningfully
                return ExecutionResult(decision=decision, executed=False, side="SKIP",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       error="Insufficient cash for meaningful position")
            # Truncate (not round) to 4 decimal places so shares * price is
            # guaranteed <= delta_notional. round() can round up and trip the
            # insufficient-cash check on the very last buy in a sequence.
            shares = int(delta_notional / price * 10000) / 10000
            if shares <= 0:
                return ExecutionResult(decision=decision, executed=False, side="SKIP",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       error="Computed share quantity rounded to zero")
            return self._do_buy(portfolio, ticker, shares, price, decision)

        if action == "SELL":
            if ticker not in portfolio.holdings:
                return ExecutionResult(decision=decision, executed=False, side="SKIP",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       error="Not held")
            h = portfolio.holdings[ticker]
            current_notional = h.market_value(price)
            target_notional = total_value * target_weight
            delta_notional = current_notional - target_notional
            if delta_notional <= 0:
                return ExecutionResult(decision=decision, executed=True, side="HOLD",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       order_id="ALREADY_BELOW_TARGET")
            shares = min(int(delta_notional / price * 10000) / 10000, h.shares)
            return self._do_sell(portfolio, ticker, shares, price, decision)

        if action == "SHORT":
            # target_weight is the desired GROSS short weight (|short value| / equity).
            # Mirror of BUY: grow the short toward the target, never beyond it.
            if ticker in portfolio.holdings and not portfolio.holdings[ticker].is_short:
                return ExecutionResult(decision=decision, executed=False, side="SKIP",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       error="Held long — cannot short; SELL first")
            target_notional = total_value * target_weight
            current_short = -portfolio.holdings[ticker].market_value(price) if ticker in portfolio.holdings else 0.0
            delta_notional = target_notional - current_short
            if delta_notional <= 0:
                return ExecutionResult(decision=decision, executed=True, side="HOLD",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       order_id="ALREADY_AT_SHORT_TARGET")
            # No cash gate: a short sale generates proceeds, it does not consume cash.
            shares = int(delta_notional / price * 10000) / 10000
            if shares <= 0:
                return ExecutionResult(decision=decision, executed=False, side="SKIP",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       error="Computed short quantity rounded to zero")
            return self._do_short(portfolio, ticker, shares, price, decision)

        if action == "COVER":
            if ticker not in portfolio.holdings or not portfolio.holdings[ticker].is_short:
                return ExecutionResult(decision=decision, executed=False, side="SKIP",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       error="Not held short")
            h = portfolio.holdings[ticker]
            current_short = -h.market_value(price)              # positive magnitude
            target_notional = total_value * target_weight       # desired remaining short
            delta_notional = current_short - target_notional    # amount to cover
            if delta_notional <= 0:
                return ExecutionResult(decision=decision, executed=True, side="HOLD",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       order_id="ALREADY_BELOW_SHORT_TARGET")
            shares = min(int(delta_notional / price * 10000) / 10000, abs(h.shares))
            return self._do_cover(portfolio, ticker, shares, price, decision)

        return ExecutionResult(decision=decision, executed=False, side="SKIP",
                               ticker=ticker, shares=0, fill_price=price, notional=0,
                               error=f"Unknown action: {action}")

    def _do_buy(self, portfolio: Portfolio, ticker: str, shares: float,
                price: float, decision: dict[str, Any]) -> ExecutionResult:
        if self.mode == "live":
            order_id, fill_price = self._submit_alpaca_order(ticker, shares, "buy")
            price = fill_price or price
        else:
            order_id = f"PAPER_BUY_{ticker}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        portfolio.buy(ticker, shares, price)
        return ExecutionResult(decision=decision, executed=True, side="BUY",
                               ticker=ticker, shares=shares, fill_price=price,
                               notional=shares * price, order_id=order_id)

    def _do_sell(self, portfolio: Portfolio, ticker: str, shares: float,
                 price: float, decision: dict[str, Any]) -> ExecutionResult:
        if self.mode == "live":
            order_id, fill_price = self._submit_alpaca_order(ticker, shares, "sell")
            price = fill_price or price
        else:
            order_id = f"PAPER_SELL_{ticker}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        portfolio.sell(ticker, shares, price)
        # Sweep any dust left over from this or earlier fractional sells
        # so positions never linger as 0.0001-share ghosts on the dashboard.
        swept = portfolio.sweep_ghost_positions()
        if swept:
            logger.info("Swept ghost positions after %s sell: %s", ticker, swept)
        return ExecutionResult(decision=decision, executed=True, side="SELL",
                               ticker=ticker, shares=shares, fill_price=price,
                               notional=shares * price, order_id=order_id)

    def _do_short(self, portfolio: Portfolio, ticker: str, shares: float,
                  price: float, decision: dict[str, Any]) -> ExecutionResult:
        # Opening/adding a short submits a SELL order on the broker side.
        if self.mode == "live":
            order_id, fill_price = self._submit_alpaca_order(ticker, shares, "sell")
            price = fill_price or price
        else:
            order_id = f"PAPER_SHORT_{ticker}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        portfolio.short(ticker, shares, price)
        return ExecutionResult(decision=decision, executed=True, side="SHORT",
                               ticker=ticker, shares=shares, fill_price=price,
                               notional=shares * price, order_id=order_id)

    def _do_cover(self, portfolio: Portfolio, ticker: str, shares: float,
                  price: float, decision: dict[str, Any]) -> ExecutionResult:
        # Covering a short submits a BUY order on the broker side.
        if self.mode == "live":
            order_id, fill_price = self._submit_alpaca_order(ticker, shares, "buy")
            price = fill_price or price
        else:
            order_id = f"PAPER_COVER_{ticker}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        portfolio.cover(ticker, shares, price)
        swept = portfolio.sweep_ghost_positions()
        if swept:
            logger.info("Swept ghost positions after %s cover: %s", ticker, swept)
        return ExecutionResult(decision=decision, executed=True, side="COVER",
                               ticker=ticker, shares=shares, fill_price=price,
                               notional=shares * price, order_id=order_id)

    def _submit_alpaca_order(self, ticker: str, shares: float, side: str) -> tuple[str, float]:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        order = MarketOrderRequest(
            symbol=ticker,
            qty=shares,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        submitted = self._alpaca_client.submit_order(order)
        order_id = str(submitted.id)
        fill_price = float(getattr(submitted, "filled_avg_price", 0) or 0)
        return order_id, fill_price
