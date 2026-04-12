"""Portfolio state container — holdings, cash, valuation, persistence.

Each model has its own JSON state file in /data/state/{model_key}.json.
The Portfolio object is the single source of truth for "what does this model own
right now"; everything else (executor, risk, prompt builder) reads from it.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..config_loader import STATE_DIR, load_settings

logger = logging.getLogger("llmlab.portfolio")


@dataclass
class Holding:
    ticker: str
    shares: float
    avg_cost: float

    def market_value(self, current_price: float) -> float:
        return self.shares * current_price

    def unrealized_pl(self, current_price: float) -> float:
        return (current_price - self.avg_cost) * self.shares

    def unrealized_pl_pct(self, current_price: float) -> float:
        if self.avg_cost == 0:
            return 0.0
        return (current_price / self.avg_cost) - 1.0


@dataclass
class IntradayState:
    """Per-session intraday counters.

    Persisted alongside the portfolio so the trade cap survives across the
    many runs that happen in a single trading day. Auto-resets when
    `session_date` no longer matches the current ET trading day.
    """
    session_date: str = ""           # YYYY-MM-DD in US/Eastern
    trades_executed_today: int = 0
    runs_today: int = 0
    first_run_at: str = ""           # ISO timestamp
    last_run_at: str = ""            # ISO timestamp


@dataclass
class Portfolio:
    model_key: str
    cash: float
    holdings: dict[str, Holding] = field(default_factory=dict)
    halted: bool = False
    inception_value: float = 0.0
    inception_date: str = ""
    last_updated: str = ""
    intraday: IntradayState = field(default_factory=IntradayState)

    # ----- intraday session helpers -----
    def reset_intraday_if_new_session(self, session_date: str) -> bool:
        """Zero the intraday counters when a new ET trading day begins.

        Returns True if a reset happened (caller may want to log this).
        """
        if self.intraday.session_date != session_date:
            self.intraday = IntradayState(session_date=session_date)
            return True
        return False

    def record_intraday_run(self, timestamp_iso: str, trades_executed: int) -> None:
        """Bump intraday counters after a successful run."""
        if not self.intraday.first_run_at:
            self.intraday.first_run_at = timestamp_iso
        self.intraday.last_run_at = timestamp_iso
        self.intraday.runs_today += 1
        self.intraday.trades_executed_today += trades_executed

    # ----- valuation -----
    def total_value(self, prices: dict[str, float]) -> float:
        value = self.cash
        for ticker, h in self.holdings.items():
            price = prices.get(ticker, h.avg_cost)
            value += h.market_value(price)
        return value

    def snapshot(self, prices: dict[str, float]) -> dict[str, Any]:
        """Dict shape consumed by prompt_builder + dashboard."""
        total = self.total_value(prices)
        holdings_out = []
        for ticker, h in self.holdings.items():
            price = prices.get(ticker, h.avg_cost)
            mv = h.market_value(price)
            holdings_out.append({
                "ticker": ticker,
                "shares": h.shares,
                "avg_cost": h.avg_cost,
                "current_price": price,
                "market_value": mv,
                "weight": (mv / total) if total else 0.0,
                "unrealized_pl": h.unrealized_pl(price),
                "unrealized_pl_pct": h.unrealized_pl_pct(price),
            })
        return {
            "model_key": self.model_key,
            "total_value": total,
            "cash": self.cash,
            "cash_pct": (self.cash / total) if total else 1.0,
            "holdings": holdings_out,
            "halted": self.halted,
            "inception_value": self.inception_value,
            "inception_date": self.inception_date,
            "cumulative_return": (total / self.inception_value - 1.0) if self.inception_value else 0.0,
        }

    # ----- mutations -----
    def buy(self, ticker: str, shares: float, price: float) -> None:
        cost = shares * price
        if cost > self.cash + 1e-6:
            raise ValueError(f"Insufficient cash for {ticker}: need {cost:.2f}, have {self.cash:.2f}")
        if ticker in self.holdings:
            h = self.holdings[ticker]
            new_shares = h.shares + shares
            h.avg_cost = (h.avg_cost * h.shares + price * shares) / new_shares
            h.shares = new_shares
        else:
            self.holdings[ticker] = Holding(ticker=ticker, shares=shares, avg_cost=price)
        self.cash -= cost

    def sell(self, ticker: str, shares: float, price: float) -> float:
        if ticker not in self.holdings:
            raise ValueError(f"Cannot sell {ticker}: not held")
        h = self.holdings[ticker]
        if shares > h.shares + 1e-6:
            shares = h.shares  # clamp
        proceeds = shares * price
        h.shares -= shares
        self.cash += proceeds
        if h.shares <= 1e-6:
            del self.holdings[ticker]
        return proceeds

    def liquidate_all(self, prices: dict[str, float]) -> None:
        for ticker in list(self.holdings.keys()):
            price = prices.get(ticker, self.holdings[ticker].avg_cost)
            self.sell(ticker, self.holdings[ticker].shares, price)


# ----- persistence -----

def _state_path(model_key: str) -> Path:
    return STATE_DIR / f"{model_key}.json"


def init_portfolio(model_key: str, mode: str | None = None) -> Portfolio:
    """Create a fresh portfolio for a model. Used on first run only.

    Inception date is the experiment start (from settings), not the wall-clock
    date the state file happens to be created. This keeps the inception
    pinned to the configured Phase A start even if the pipeline runs earlier
    for build/test purposes.
    """
    settings = load_settings()
    mode = mode or settings["mode"]
    capital = float(settings["starting_capital"][mode])
    today = datetime.utcnow().strftime("%Y-%m-%d")
    inception_date = settings.get("experiment_start_date", today)
    p = Portfolio(
        model_key=model_key,
        cash=capital,
        holdings={},
        halted=False,
        inception_value=capital,
        inception_date=inception_date,
        last_updated=today,
    )
    save_portfolio(p)
    return p


def load_portfolio(model_key: str) -> Portfolio:
    """Load from disk, creating a fresh state file if it doesn't exist."""
    path = _state_path(model_key)
    if not path.exists():
        logger.info("No state for %s — initializing fresh portfolio", model_key)
        return init_portfolio(model_key)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    holdings = {
        t: Holding(ticker=t, shares=h["shares"], avg_cost=h["avg_cost"])
        for t, h in data.get("holdings", {}).items()
    }
    raw_intraday = data.get("intraday") or {}
    intraday = IntradayState(
        session_date=str(raw_intraday.get("session_date", "")),
        trades_executed_today=int(raw_intraday.get("trades_executed_today", 0)),
        runs_today=int(raw_intraday.get("runs_today", 0)),
        first_run_at=str(raw_intraday.get("first_run_at", "")),
        last_run_at=str(raw_intraday.get("last_run_at", "")),
    )
    return Portfolio(
        model_key=data["model_key"],
        cash=float(data["cash"]),
        holdings=holdings,
        halted=bool(data.get("halted", False)),
        inception_value=float(data.get("inception_value", data["cash"])),
        inception_date=data.get("inception_date", ""),
        last_updated=data.get("last_updated", ""),
        intraday=intraday,
    )


def save_portfolio(p: Portfolio) -> None:
    p.last_updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    out = {
        "model_key": p.model_key,
        "cash": p.cash,
        "halted": p.halted,
        "inception_value": p.inception_value,
        "inception_date": p.inception_date,
        "last_updated": p.last_updated,
        "holdings": {t: {"shares": h.shares, "avg_cost": h.avg_cost} for t, h in p.holdings.items()},
        "intraday": {
            "session_date": p.intraday.session_date,
            "trades_executed_today": p.intraday.trades_executed_today,
            "runs_today": p.intraday.runs_today,
            "first_run_at": p.intraday.first_run_at,
            "last_run_at": p.intraday.last_run_at,
        },
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_state_path(p.model_key), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
