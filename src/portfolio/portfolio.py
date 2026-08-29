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

from ..config_loader import STATE_DIR, load_settings, starting_capital
from .settlement import SettlementLedger

logger = logging.getLogger("llmlab.portfolio")


@dataclass
class Holding:
    ticker: str
    shares: float          # negative for a short position
    avg_cost: float        # entry price (always positive), magnitude-weighted

    @property
    def is_short(self) -> bool:
        return self.shares < 0

    @property
    def direction(self) -> str:
        return "short" if self.shares < 0 else "long"

    def market_value(self, current_price: float) -> float:
        # Naturally negative for a short (shares < 0): a short position is a
        # liability, so it subtracts from total equity.
        return self.shares * current_price

    def unrealized_pl(self, current_price: float) -> float:
        # Sign-correct for both directions: (price - cost) * shares. For a short
        # (shares < 0) this flips, so a falling price yields a positive P&L.
        return (current_price - self.avg_cost) * self.shares

    def unrealized_pl_pct(self, current_price: float) -> float:
        """Return on the position, expressed relative to its direction.

        Long: gains when price rises. Short: gains when price falls — so the
        sign is inverted relative to the asset's own return. This is the
        position's P&L%, not the asset's, so a winning short reads positive.
        """
        if self.avg_cost == 0:
            return 0.0
        asset_return = (current_price / self.avg_cost) - 1.0
        return -asset_return if self.is_short else asset_return


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
    # Unsettled sale proceeds (T+1). Empty and inert until the cash branch's
    # settled-funds enforcement activates; see portfolio/settlement.py.
    settlement: SettlementLedger = field(default_factory=SettlementLedger)

    # The execution mode this book was incepted under. Registered ruling
    # (2026-08-29): both phase boundaries re-incept fresh and nothing carries —
    # Oct 1 validation books incept in broker-paper at registered capital, Nov 1
    # confirmatory books incept from broker-authoritative funded reality with no
    # positions, no P&L and no state from Phase A or October. A carried state
    # file would silently defeat that, so the epoch is stamped here and checked
    # on every load. Empty means "written before this field existed", which is
    # the Phase A simulator era.
    inception_epoch: str = ""

    # ----- settled-cash helpers -----
    def settled_cash(self) -> float:
        """Deployable cash: total cash minus anything still unsettled."""
        return self.settlement.settled_cash(self.cash)

    def unsettled_cash(self) -> float:
        return self.settlement.unsettled_total()

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

    # ----- exposure accounting (long-only and short-aware) -----
    def exposures(self, prices: dict[str, float]) -> dict[str, float]:
        """Gross/net exposure breakdown as fractions of total equity.

        For a long-only book this is just {long=invested, short=0,
        net=long, gross=long} — unchanged from before shorting existed. With
        shorts, gross_long = Σ long MV, gross_short = Σ |short MV|, net =
        gross_long − gross_short, gross = gross_long + gross_short. All ratios
        are taken against current equity (total_value), so they move with P&L.
        """
        gross_long = 0.0
        gross_short = 0.0
        for ticker, h in self.holdings.items():
            price = prices.get(ticker, h.avg_cost)
            mv = h.market_value(price)
            if mv >= 0:
                gross_long += mv
            else:
                gross_short += -mv
        total = self.total_value(prices)
        eq = total if total else 0.0
        ratio = (lambda v: (v / eq) if eq else 0.0)
        return {
            "gross_long_value": gross_long,
            "gross_short_value": gross_short,
            "gross_value": gross_long + gross_short,
            "net_value": gross_long - gross_short,
            "long_exposure_pct": ratio(gross_long),
            "short_exposure_pct": ratio(gross_short),
            "gross_exposure_pct": ratio(gross_long + gross_short),
            "net_exposure_pct": ratio(gross_long - gross_short),
        }

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
                # Signed weight: negative for shorts (mv is negative). The
                # absolute value is the gross weight used for the per-name cap.
                "weight": (mv / total) if total else 0.0,
                "gross_weight": (abs(mv) / total) if total else 0.0,
                "direction": h.direction,
                "unrealized_pl": h.unrealized_pl(price),
                "unrealized_pl_pct": h.unrealized_pl_pct(price),
            })
        exp = self.exposures(prices)
        return {
            "model_key": self.model_key,
            "total_value": total,
            "cash": self.cash,
            "cash_pct": (self.cash / total) if total else 1.0,
            # Settled/unsettled split. Zero unsettled for a book not running
            # under the T+1 constraint, so pre-cash-branch snapshots are
            # unchanged in substance: settled_cash == cash.
            "settled_cash": self.settled_cash(),
            "unsettled_cash": self.unsettled_cash(),
            "settled_cash_pct": (self.settled_cash() / total) if total else 1.0,
            "holdings": holdings_out,
            "halted": self.halted,
            "inception_value": self.inception_value,
            "inception_date": self.inception_date,
            "cumulative_return": (total / self.inception_value - 1.0) if self.inception_value else 0.0,
            # Exposure block — short utilization is share of equity held short
            # (capped at max_gross_short_pct by the risk engine). Zero for a
            # long-only book, so existing reports are unaffected until shorts
            # go live.
            "gross_long_value": exp["gross_long_value"],
            "gross_short_value": exp["gross_short_value"],
            "gross_value": exp["gross_value"],
            "net_value": exp["net_value"],
            "long_exposure_pct": exp["long_exposure_pct"],
            "short_exposure_pct": exp["short_exposure_pct"],
            "gross_exposure_pct": exp["gross_exposure_pct"],
            "net_exposure_pct": exp["net_exposure_pct"],
            "short_utilization": exp["short_exposure_pct"],
            "num_shorts": sum(1 for h in self.holdings.values() if h.is_short),
        }

    # ----- mutations -----
    def buy(self, ticker: str, shares: float, price: float) -> None:
        if ticker in self.holdings and self.holdings[ticker].is_short:
            # A name held short must be covered (not bought) — mixing both
            # directions in one holding would make avg_cost meaningless.
            raise ValueError(f"Cannot BUY {ticker}: position is short — COVER it first")
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

    # ----- short-side mutations -----
    def short(self, ticker: str, shares: float, price: float) -> float:
        """Open or add to a short position. `shares` is a positive magnitude.

        A short sale credits the sale proceeds to cash and records the position
        as a negative share count. The position's market value is therefore
        negative — it is a liability that subtracts from equity. avg_cost is the
        magnitude-weighted short entry price. No borrow is modeled (Phase A).
        Returns the proceeds credited.
        """
        if shares <= 0:
            raise ValueError(f"Cannot short {ticker}: shares must be positive, got {shares}")
        if ticker in self.holdings and not self.holdings[ticker].is_short:
            raise ValueError(f"Cannot SHORT {ticker}: position is long — SELL it first")
        proceeds = shares * price
        if ticker in self.holdings:
            h = self.holdings[ticker]
            existing = abs(h.shares)
            h.avg_cost = (h.avg_cost * existing + price * shares) / (existing + shares)
            h.shares -= shares  # more negative
        else:
            self.holdings[ticker] = Holding(ticker=ticker, shares=-shares, avg_cost=price)
        self.cash += proceeds
        return proceeds

    def cover(self, ticker: str, shares: float, price: float) -> float:
        """Buy back (cover) part or all of a short. `shares` is a positive magnitude.

        Covering debits the buy-back cost from cash and shrinks the negative
        share count toward zero. A fully covered position is swept off the
        books. Returns the cost paid. Covering always completes (it reduces
        risk), clamped to the open short size.
        """
        if ticker not in self.holdings:
            raise ValueError(f"Cannot cover {ticker}: not held")
        h = self.holdings[ticker]
        if not h.is_short:
            raise ValueError(f"Cannot COVER {ticker}: position is long, not short")
        shares = min(shares, abs(h.shares))  # clamp to the open short
        cost = shares * price
        h.shares += shares  # toward zero
        self.cash -= cost
        if abs(h.shares) < self.GHOST_SHARES_EPSILON:
            del self.holdings[ticker]
        return cost

    # Ghost-position threshold: any residual smaller than this gets swept
    # off the books after a sell. Catches dust like 0.0001 shares left
    # behind by float rounding in fractional executions.
    GHOST_SHARES_EPSILON = 0.01

    def sell(self, ticker: str, shares: float, price: float) -> float:
        if ticker not in self.holdings:
            raise ValueError(f"Cannot sell {ticker}: not held")
        h = self.holdings[ticker]
        if h.is_short:
            # Reducing a short is a COVER, not a SELL — guard so a SELL can
            # never flip the sign of a short holding.
            raise ValueError(f"Cannot SELL {ticker}: position is short — COVER it instead")
        if shares > h.shares + 1e-6:
            shares = h.shares  # clamp
        proceeds = shares * price
        h.shares -= shares
        self.cash += proceeds
        if h.shares < self.GHOST_SHARES_EPSILON:
            del self.holdings[ticker]
        return proceeds

    def sweep_ghost_positions(self) -> list[str]:
        """Drop any holding with fewer than GHOST_SHARES_EPSILON shares.

        These are dust positions left behind by fractional rounding — they
        clutter the dashboard and confuse the model. Returns the list of
        ticker symbols that were swept (for logging).
        """
        # Compare on magnitude so live shorts (negative shares) are never
        # mistaken for dust — only positions within ±EPSILON of flat are swept.
        ghosts = [t for t, h in self.holdings.items()
                  if abs(h.shares) < self.GHOST_SHARES_EPSILON]
        for t in ghosts:
            del self.holdings[t]
        return ghosts

    def liquidate_all(self, prices: dict[str, float]) -> None:
        """Flatten the book in memory. BOOK-ONLY — places no orders.

        Use `Executor.liquidate_all` for a risk halt. This method reaches no
        venue, so in a broker mode it produces a book showing all cash while
        every position stays open at the broker — a halt that reports success
        without closing anything. That was the live defect until P0, and the
        guard below stops it being reintroduced by a future caller rather than
        relying on this docstring being read.
        """
        if load_settings().get("mode", "paper") != "paper":
            raise RuntimeError(
                "Portfolio.liquidate_all places no orders and must not be used "
                "to halt a book in a broker mode — the positions would stay "
                "open at the venue. Use Executor.liquidate_all, which confirms "
                "terminal fills before the book moves.")
        for ticker in list(self.holdings.keys()):
            h = self.holdings[ticker]
            price = prices.get(ticker, h.avg_cost)
            if h.is_short:
                self.cover(ticker, abs(h.shares), price)
            else:
                self.sell(ticker, h.shares, price)


# ----- persistence -----

def _state_path(model_key: str) -> Path:
    return STATE_DIR / f"{model_key}.json"


def init_portfolio(model_key: str, mode: str | None = None) -> Portfolio:
    """Create a fresh portfolio for a model. Used on first run only.

    Inception date is the experiment start (from settings), not the wall-clock
    date the state file happens to be created. This keeps the inception
    pinned to the configured Phase A start even if the pipeline runs earlier
    for build/test purposes.

    Capital comes from the guarded lookup, so a mode whose per-book figure has
    not been confirmed raises instead of seeding six books at a stale default.
    """
    settings = load_settings()
    mode = mode or settings["mode"]
    capital = starting_capital(settings, mode)
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
        inception_epoch=mode,
    )
    save_portfolio(p)
    return p


class InceptionEpochError(RuntimeError):
    """A state file from a different execution mode was found on disk.

    Registered ruling (2026-08-29): the Oct 1 and Nov 1 boundaries both
    re-incept fresh and nothing carries across them. The failure this guards is
    silent, not loud — a leftover Phase A state file loads perfectly well and
    the run continues with a $100,000 paper book quietly standing in for a
    freshly funded one. Archiving is deliberately a human step: deleting book
    state is not something a pipeline should do on its own initiative.
    """


def _assert_epoch(model_key: str, path: Path, data: dict[str, Any]) -> None:
    settings = load_settings()
    current = settings.get("mode", "paper")
    stored = data.get("inception_epoch") or "paper"
    if stored == current:
        return
    raise InceptionEpochError(
        f"State file {path.name} was incepted under mode {stored!r} but the "
        f"pipeline is running in mode {current!r}. Books do not carry across a "
        f"phase boundary: archive data/state read-only and let the run incept "
        f"fresh at the registered capital for {current!r}. Refusing to load "
        f"{model_key}'s {stored!r} book into a {current!r} run.")


def load_portfolio(model_key: str) -> Portfolio:
    """Load from disk, creating a fresh state file if it doesn't exist.

    The filename is the authoritative identity of the portfolio — we always
    use the `model_key` argument on the returned Portfolio, never the
    `model_key` field stored inside the JSON. A mismatch means the file was
    mis-seeded (e.g. copied from another model's state without updating the
    field), which previously caused `save_portfolio` to route writes to the
    wrong path and silently clobber the other model's state.
    """
    path = _state_path(model_key)
    if not path.exists():
        logger.info("No state for %s — initializing fresh portfolio", model_key)
        return init_portfolio(model_key)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _assert_epoch(model_key, path, data)
    stored_key = data.get("model_key")
    if stored_key and stored_key != model_key:
        logger.warning(
            "State file %s has model_key='%s' but expected '%s' — "
            "overriding from filename (file will be rewritten on next save)",
            path.name, stored_key, model_key,
        )
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
        model_key=model_key,
        cash=float(data["cash"]),
        holdings=holdings,
        halted=bool(data.get("halted", False)),
        inception_value=float(data.get("inception_value", data["cash"])),
        inception_date=data.get("inception_date", ""),
        last_updated=data.get("last_updated", ""),
        intraday=intraday,
        # Absent from every state file written before the settled-funds build;
        # an empty ledger means "all cash settled", which is exactly right for
        # a book that has never run under the constraint.
        settlement=SettlementLedger.from_list(data.get("unsettled_cash")),
        inception_epoch=data.get("inception_epoch") or "paper",
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
        "unsettled_cash": p.settlement.to_list(),
        # Stamped so a book cannot silently survive a phase boundary; see
        # InceptionEpochError.
        "inception_epoch": p.inception_epoch or load_settings().get("mode", "paper"),
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_state_path(p.model_key), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
