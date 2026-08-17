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
from ..portfolio.settlement import settlement_days, settlement_enforcement_active

logger = logging.getLogger("llmlab.execution")


# Execution-constraint codes. These mark venue-imposed limits, not pipeline
# failures: the linkage worked and the order was correctly not sent. Under the
# registered censoring principle a blocked intention enters no behavioral
# outcome metric — which it cannot, since every behavioral reader filters on
# `executed`, and a blocked purchase is never executed.
CONSTRAINT_UNSETTLED_FUNDS = "UNSETTLED_FUNDS"        # blocked outright
CONSTRAINT_UNSETTLED_FUNDS_CAPPED = "UNSETTLED_FUNDS_CAPPED"  # filled, but smaller


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
    # Set when a venue-level constraint shaped this result. Empty for ordinary
    # fills, holds and skips.
    constraint: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class Executor:
    """Single executor that handles both paper and live modes."""

    def __init__(self) -> None:
        self.settings = load_settings()
        self.mode = self.settings["mode"]
        self._alpaca_client = None
        if self.mode == "live":
            self._alpaca_client = self._init_alpaca()

    # ----- settled-funds constraint (cash-account branch) -----

    def _run_date(self, run_date: str | None) -> str:
        return run_date or datetime.utcnow().strftime("%Y-%m-%d")

    def settlement_enforced(self, run_date: str | None = None) -> bool:
        """Whether the settled-funds constraint binds for this run.

        Inert until the cash branch is chosen and its 2026-09-16 activation
        arrives; see portfolio/settlement.py.
        """
        return settlement_enforcement_active(self.settings, self._run_date(run_date))

    def advance_settlement(self, portfolio: Portfolio, run_date: str | None = None) -> float:
        """Mature any tranche whose T+1 date has arrived. Idempotent."""
        settled = portfolio.settlement.settle_through(self._run_date(run_date))
        if settled:
            logger.info("[%s] Settled %.2f of previously unsettled proceeds",
                        portfolio.model_key, settled)
        return settled

    def _record_proceeds(self, portfolio: Portfolio, amount: float,
                         run_date: str | None) -> None:
        """Book sale proceeds as unsettled when the constraint is active.

        Only tracked under enforcement: outside the cash branch the book has no
        settlement constraint, and carrying a phantom unsettled balance would
        misreport deployable cash in every paper-phase snapshot.
        """
        if not self.settlement_enforced(run_date):
            return
        portfolio.settlement.record_sale(
            amount, self._run_date(run_date), settlement_days(self.settings))

    def _spendable_cash(self, portfolio: Portfolio, run_date: str | None) -> float:
        return (portfolio.settled_cash() if self.settlement_enforced(run_date)
                else portfolio.cash)

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
        run_date: str | None = None,
    ) -> list[ExecutionResult]:
        # Mature settled proceeds before anything is priced against them, so a
        # cycle never blocks a purchase on funds that settled this morning.
        self.advance_settlement(portfolio, run_date)
        results: list[ExecutionResult] = []
        for d in decisions:
            try:
                r = self._execute_one(portfolio, d, prices, run_date)
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
        run_date: str | None = None,
    ) -> list[ExecutionResult]:
        """Force-sell specified tickers (used by risk stops).

        Never gated by the settled-funds constraint. A stop closes a position;
        it does not purchase, so settlement cannot block it — which is exactly
        why the cash branch keeps stops unconditional and is the registered
        Phase B venue shape. Proceeds are booked unsettled like any other sale.
        """
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
                    self._record_proceeds(
                        portfolio, portfolio.sell(ticker, shares, price), run_date)
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
        run_date: str | None = None,
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
            # Cap at deployable cash. Under the cash branch's settled-funds
            # constraint that is the SETTLED balance, not total cash: proceeds
            # from a sale earlier in the session are in the account but are not
            # deployable until T+1.
            spendable = self._spendable_cash(portfolio, run_date)
            constrained_by_settlement = spendable < min(delta_notional, portfolio.cash)
            delta_notional = min(delta_notional, spendable)
            if delta_notional < price:  # can't even buy 1 fractional share meaningfully
                if constrained_by_settlement and portfolio.cash >= price:
                    # Blocked at submission, never sent. The linkage worked; the
                    # constraint is the venue's. Logged as an execution-constraint
                    # event, and — being unexecuted — it enters no behavioral
                    # outcome metric, per the registered censoring principle.
                    logger.info(
                        "[%s] BUY %s blocked: settled cash %.2f < price %.2f "
                        "(unsettled %.2f of %.2f total)",
                        portfolio.model_key, ticker, spendable, price,
                        portfolio.unsettled_cash(), portfolio.cash,
                    )
                    return ExecutionResult(
                        decision=decision, executed=False, side="SKIP",
                        ticker=ticker, shares=0, fill_price=price, notional=0,
                        order_id="EXEC_CONSTRAINT_UNSETTLED_FUNDS",
                        constraint=CONSTRAINT_UNSETTLED_FUNDS,
                        error=(f"Blocked at submission: purchase requires settled funds; "
                               f"settled {spendable:.2f}, unsettled {portfolio.unsettled_cash():.2f}"),
                    )
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
            return self._do_buy(portfolio, ticker, shares, price, decision,
                                constraint=(CONSTRAINT_UNSETTLED_FUNDS_CAPPED
                                            if constrained_by_settlement else ""))

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
            return self._do_sell(portfolio, ticker, shares, price, decision, run_date)

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
            return self._do_short(portfolio, ticker, shares, price, decision, run_date)

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
                price: float, decision: dict[str, Any],
                constraint: str = "") -> ExecutionResult:
        if self.mode == "live":
            order_id, fill_price = self._submit_alpaca_order(ticker, shares, "buy")
            price = fill_price or price
        else:
            order_id = f"PAPER_BUY_{ticker}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        portfolio.buy(ticker, shares, price)
        if constraint:
            logger.info("[%s] BUY %s capped at the settled balance (%.4f shares filled)",
                        portfolio.model_key, ticker, shares)
        return ExecutionResult(decision=decision, executed=True, side="BUY",
                               ticker=ticker, shares=shares, fill_price=price,
                               notional=shares * price, order_id=order_id,
                               constraint=constraint)

    def _do_sell(self, portfolio: Portfolio, ticker: str, shares: float,
                 price: float, decision: dict[str, Any],
                 run_date: str | None = None) -> ExecutionResult:
        if self.mode == "live":
            order_id, fill_price = self._submit_alpaca_order(ticker, shares, "sell")
            price = fill_price or price
        else:
            order_id = f"PAPER_SELL_{ticker}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        proceeds = portfolio.sell(ticker, shares, price)
        self._record_proceeds(portfolio, proceeds, run_date)
        # Sweep any dust left over from this or earlier fractional sells
        # so positions never linger as 0.0001-share ghosts on the dashboard.
        swept = portfolio.sweep_ghost_positions()
        if swept:
            logger.info("Swept ghost positions after %s sell: %s", ticker, swept)
        return ExecutionResult(decision=decision, executed=True, side="SELL",
                               ticker=ticker, shares=shares, fill_price=price,
                               notional=shares * price, order_id=order_id)

    def _do_short(self, portfolio: Portfolio, ticker: str, shares: float,
                  price: float, decision: dict[str, Any],
                  run_date: str | None = None) -> ExecutionResult:
        # Opening/adding a short submits a SELL order on the broker side.
        if self.mode == "live":
            order_id, fill_price = self._submit_alpaca_order(ticker, shares, "sell")
            price = fill_price or price
        else:
            order_id = f"PAPER_SHORT_{ticker}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        proceeds = portfolio.short(ticker, shares, price)
        # A short sale is a sale: its proceeds are unsettled like any other.
        # Unreachable on the cash branch (a cash account cannot short), but
        # correct if the constraint is ever active alongside shorting.
        self._record_proceeds(portfolio, proceeds, run_date)
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
