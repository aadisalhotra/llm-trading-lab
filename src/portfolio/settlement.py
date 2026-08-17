"""Per-book settled-cash ledger with T+1 settlement.

Registered as the *settled-funds execution constraint (cash-account branch)*:
Phase B books are cash accounts, sale proceeds settle T+1 and are not deployable
until settled, and enforcement is pipeline-level — each book carries a
settled-cash ledger and purchase orders are capped at the settled balance.

Why the constraint exists. On a cash account there is no pattern-day-trader
rule, so the 15% position stop stays unconditional: a same-day stop-close of a
same-day open is legal. That is what makes the cash branch the registered Phase
B venue shape. The price is redeployment latency, which this module models.

Why enforcement is structural rather than reactive. A purchase exceeding
settled funds is blocked *at submission* and never sent, so the venue never
observes a free-riding violation and the 90-day-restriction class is
unreachable. The ledger is therefore a pre-trade gate, not a post-trade audit.

Activation. Built now, binds at the branch: enforcement requires BOTH the
`settlement.enforce_settled_funds` flag (flipped at the signed-off 2026-09-16
pre-market activation, same discipline as the 2026-07-01 shorting flip) AND the
run date being on or after `settlement.activation_date`. Until the September 15
branch decision picks the cash branch, the flag stays false and every code path
here is inert.

Accounting note. Paper fills credit sale proceeds to `Portfolio.cash`
immediately, exactly as a broker does — the money is in the account, it is just
not yet *deployable*. So the ledger tracks the unsettled portion and the settled
balance is a derived quantity: settled = cash - unsettled. It never holds cash
of its own, which means it cannot drift out of sync with the book's real
balance.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger("llmlab.settlement")

# T+1 is the current US equity settlement cycle (SEC Rule 15c6-1, since 2024).
DEFAULT_SETTLEMENT_DAYS = 1
# The registered activation of the cash branch's enforcement.
DEFAULT_ACTIVATION_DATE = "2026-09-16"


def _as_date(d: str | date | datetime) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def next_trading_day(start: str | date | datetime, days: int = 1) -> str:
    """The date `days` NYSE sessions after `start`, as YYYY-MM-DD.

    Uses the NYSE calendar when available so settlement skips holidays as well
    as weekends. Falls back to a weekday skip if pandas_market_calendars is not
    installed — fail-open in the same direction as `is_market_open_today`: the
    fallback can only settle proceeds *later* than a holiday-aware calendar
    would on a holiday week, never earlier, so it never lets unsettled funds be
    spent early.
    """
    d0 = _as_date(start)
    if days <= 0:
        return d0.isoformat()
    try:
        import pandas_market_calendars as mcal

        nyse = mcal.get_calendar("NYSE")
        # Look ahead far enough to clear a long holiday weekend plus slack.
        sched = nyse.schedule(start_date=(d0 + timedelta(days=1)).isoformat(),
                              end_date=(d0 + timedelta(days=14 + days * 4)).isoformat())
        sessions = [s.date().isoformat() for s in sched.index]
        if len(sessions) >= days:
            return sessions[days - 1]
        logger.warning("next_trading_day: NYSE schedule too short from %s; using weekday fallback", d0)
    except ImportError:
        pass
    except Exception:
        logger.exception("next_trading_day: NYSE calendar failed from %s; using weekday fallback", d0)

    d = d0
    remaining = days
    while remaining > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            remaining -= 1
    return d.isoformat()


def settlement_enforcement_active(settings: dict[str, Any], on_date: str | date | datetime) -> bool:
    """True when the settled-funds constraint binds for `on_date`.

    Requires both the branch flag and the activation date, so the machinery can
    ship now and start binding only when the cash branch is chosen and reaches
    its registered activation.
    """
    cfg = (settings or {}).get("settlement") or {}
    if not cfg.get("enforce_settled_funds", False):
        return False
    activation = str(cfg.get("activation_date") or DEFAULT_ACTIVATION_DATE)
    return _as_date(on_date).isoformat() >= activation


def settlement_days(settings: dict[str, Any]) -> int:
    cfg = (settings or {}).get("settlement") or {}
    try:
        return max(0, int(cfg.get("settlement_days", DEFAULT_SETTLEMENT_DAYS)))
    except (TypeError, ValueError):
        return DEFAULT_SETTLEMENT_DAYS


@dataclass
class UnsettledTranche:
    """Sale proceeds credited to cash but not yet deployable."""
    amount: float
    trade_date: str      # YYYY-MM-DD the sale filled
    settles_on: str      # YYYY-MM-DD the proceeds become deployable

    def to_dict(self) -> dict[str, Any]:
        return {"amount": self.amount, "trade_date": self.trade_date,
                "settles_on": self.settles_on}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "UnsettledTranche":
        return cls(amount=float(d.get("amount") or 0.0),
                   trade_date=str(d.get("trade_date") or ""),
                   settles_on=str(d.get("settles_on") or ""))


@dataclass
class SettlementLedger:
    """The unsettled portion of a book's cash, as dated tranches."""

    tranches: list[UnsettledTranche] = field(default_factory=list)

    # ----- queries -----
    def unsettled_total(self) -> float:
        return float(sum(t.amount for t in self.tranches))

    def settled_cash(self, cash: float) -> float:
        """Deployable balance = cash minus everything still unsettled.

        Clamped at zero: a book cannot have negative deployable cash, and a
        rounding tail must never present as a spendable balance.
        """
        return max(0.0, float(cash) - self.unsettled_total())

    # ----- mutations -----
    def record_sale(self, amount: float, trade_date: str | date | datetime,
                    days: int = DEFAULT_SETTLEMENT_DAYS) -> UnsettledTranche | None:
        """Book sale proceeds as unsettled until T+`days`.

        Zero and negative amounts are ignored — only proceeds create an
        unsettled balance, and a cover or a buy consumes cash rather than
        producing it.
        """
        amount = float(amount)
        if amount <= 0:
            return None
        td = _as_date(trade_date).isoformat()
        tranche = UnsettledTranche(amount=amount, trade_date=td,
                                   settles_on=next_trading_day(td, days))
        self.tranches.append(tranche)
        return tranche

    def settle_through(self, on_date: str | date | datetime) -> float:
        """Mature every tranche whose settlement date has arrived.

        Idempotent: matured tranches are removed, so calling it twice in one
        session settles nothing the second time. Returns the amount settled.
        """
        today = _as_date(on_date).isoformat()
        matured = [t for t in self.tranches if t.settles_on <= today]
        if not matured:
            return 0.0
        self.tranches = [t for t in self.tranches if t.settles_on > today]
        total = float(sum(t.amount for t in matured))
        logger.debug("Settled %d tranche(s) totalling %.2f as of %s", len(matured), total, today)
        return total

    # ----- persistence -----
    def to_list(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self.tranches]

    @classmethod
    def from_list(cls, raw: Any) -> "SettlementLedger":
        if not raw:
            return cls()
        out: list[UnsettledTranche] = []
        for d in raw:
            try:
                t = UnsettledTranche.from_dict(d)
            except (TypeError, ValueError, AttributeError):
                logger.warning("Discarding malformed settlement tranche: %r", d)
                continue
            if t.amount > 0 and t.settles_on:
                out.append(t)
        return cls(tranches=out)
