"""Share/cash conservation audit — long- and short-aware.

The invariant this enforces: a trade executed at a fair market price is
*value-neutral at the moment it happens*. Equity = cash + Σ position market
value. When you BUY, cash falls by exactly the position value gained; when you
SELL it rises by the value given up; a SHORT credits proceeds to cash exactly
equal to the (negative) liability it opens; a COVER debits cash by exactly the
liability it retires. In every case, valuing the whole book at the fill price,
equity immediately before and after the fill must be identical. Any nonzero
delta is a *conservation violation* — value created or destroyed out of thin
air — which on an equity curve shows up as a phantom discontinuity.

This is the check that a short must pass cleanly: opening a short credits cash
and opens a negative-value position, and those two must cancel to the cent. P&L
only accrues afterward, as the marking price moves — never at the trade itself.

`audit_trade_sequence` replays a list of fills through the real Portfolio
accounting (so it audits the actual `short`/`cover`/`buy`/`sell` code, not a
re-implementation) and reports any step whose equity jumped at constant price.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .portfolio import Portfolio


@dataclass
class AuditStep:
    index: int
    action: str
    ticker: str
    shares: float
    price: float
    cash_before: float
    cash_after: float
    equity_before: float
    equity_after: float
    cash_delta: float
    mv_delta: float
    discontinuity: float   # equity_after - equity_before at the fill price
    ok: bool


@dataclass
class AuditReport:
    steps: list[AuditStep] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    final_cash: float = 0.0
    final_equity: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        status = "PASS" if self.passed else f"FAIL ({len(self.violations)} violations)"
        return (f"Conservation audit: {status} over {len(self.steps)} fills; "
                f"final cash={self.final_cash:,.2f} equity={self.final_equity:,.2f}")


def audit_trade_sequence(
    events: list[dict[str, Any]],
    starting_cash: float,
    *,
    tol: float = 1e-6,
) -> AuditReport:
    """Replay `events` through Portfolio accounting and check conservation.

    Each event is a dict: {action, ticker, shares, price}. `action` is one of
    BUY / SELL / SHORT / COVER / HOLD; `shares` is a positive magnitude; `price`
    is the fill price. Positions not traded on a given step are carried at their
    most recent fill price (a constant-price mark), so any equity change at that
    step can only come from the fill itself — and a conservation-clean fill
    produces none.

    Returns an AuditReport; `report.passed` is True iff no step created or
    destroyed value at its fill price.
    """
    p = Portfolio(
        model_key="__audit__",
        cash=float(starting_cash),
        holdings={},
        inception_value=float(starting_cash),
        inception_date="",
    )
    last_prices: dict[str, float] = {}
    report = AuditReport()

    for i, ev in enumerate(events):
        action = str(ev["action"]).upper()
        ticker = str(ev["ticker"]).upper()
        shares = float(ev.get("shares", 0.0))
        price = float(ev.get("price", 0.0))

        # Mark the whole book at the prevailing prices, overriding the traded
        # name with this fill's price so the only thing that can move equity is
        # the fill.
        marks = {**last_prices, ticker: price}

        cash_before = p.cash
        equity_before = p.total_value(marks)
        mv_before = _ticker_mv(p, ticker, price)

        if action == "BUY":
            p.buy(ticker, shares, price)
        elif action == "SELL":
            p.sell(ticker, shares, price)
        elif action == "SHORT":
            p.short(ticker, shares, price)
        elif action == "COVER":
            p.cover(ticker, shares, price)
        elif action == "HOLD":
            pass
        else:
            report.violations.append(f"step {i}: unknown action {action!r}")
            continue

        cash_after = p.cash
        mv_after = _ticker_mv(p, ticker, price)
        equity_after = p.total_value(marks)
        discontinuity = equity_after - equity_before
        ok = abs(discontinuity) <= tol

        report.steps.append(AuditStep(
            index=i, action=action, ticker=ticker, shares=shares, price=price,
            cash_before=cash_before, cash_after=cash_after,
            equity_before=equity_before, equity_after=equity_after,
            cash_delta=cash_after - cash_before,
            mv_delta=mv_after - mv_before,
            discontinuity=discontinuity, ok=ok,
        ))
        if not ok:
            report.violations.append(
                f"step {i} ({action} {shares:g} {ticker} @ {price:g}): "
                f"equity discontinuity {discontinuity:+.6f} "
                f"(Δcash {cash_after - cash_before:+.6f}, Δmv {mv_after - mv_before:+.6f})"
            )

        last_prices = marks

    report.final_cash = p.cash
    report.final_equity = p.total_value(last_prices)
    return report


def _ticker_mv(p: Portfolio, ticker: str, price: float) -> float:
    h = p.holdings.get(ticker)
    return h.market_value(price) if h else 0.0
