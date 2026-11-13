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

from ..config_loader import STATE_DIR, effective_mode, load_settings, starting_capital
from .settlement import SettlementLedger
from ..price_checks import is_valid_price

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
    # positions, no P&L and no state from Phase A or October. (Dates moved by the
    # 2026-09-30 boundary move: the validation books incept at the venue cutover,
    # target 2026-11-02, and the confirmatory books on 2026-12-01; the no-carry
    # rule itself is unchanged.) A carried state
    # file would silently defeat that, so the epoch is stamped here and checked
    # on every load. Empty means "written before this field existed", which is
    # the Phase A simulator era.
    inception_epoch: str = ""

    # Slippage accounting for a negative balance left by FILL_OVERRUN events.
    # While cash is below zero this is what the state audit compares it to: an
    # overrun it covers is a WARN, a balance it does not cover is a CRITICAL.
    # Cleared by save_portfolio once cash is back at or above zero.
    fill_overrun_usd: float = 0.0

    # Per-cycle valuation marks for HELD tickers that have no valid price this
    # cycle: {ticker: (mark, source)}, set by the pipeline from the ingestion
    # guard (src/data/price_validity.py) and never persisted. Each one is a
    # classified MISSING_PRICE event with its mark named; see mark().
    fallback_marks: dict[str, tuple[float, str]] = field(default_factory=dict, repr=False,
                                                          compare=False)

    def mark(self, ticker: str, prices: dict[str, float]) -> float:
        """The price this book values `ticker` at this cycle.

        A valid price if there is one. Otherwise the cycle's classified fallback
        mark (an earlier valid bar of the same session, or average cost), which
        keeps total value finite so one unpriced name cannot freeze the book's
        other orders. Average cost is reached unclassified only when a caller
        passes a prices dict the ingestion guard did not build.
        """
        p = prices.get(ticker)
        if is_valid_price(p):
            return float(p)
        fb = self.fallback_marks.get(ticker)
        if fb is not None:
            return fb[0]
        return self.holdings[ticker].avg_cost

    # ----- settled-cash helpers -----
    def settled_cash(self) -> float:
        """Deployable cash: total cash minus anything still unsettled."""
        return self.settlement.settled_cash(self.cash)

    def unsettled_cash(self) -> float:
        return self.settlement.unsettled_total()

    def settled_balance(self) -> float:
        """Total cash minus unsettled proceeds, NOT clamped at zero.

        settled_cash() clamps for sizing and display, which is right for "what
        may be deployed" and wrong for "did a fill spend more than was settled":
        clamped, a capped BUY that fills above its reference can overspend the
        settled balance while total cash (padded by unsettled proceeds) stays
        positive, and nothing sees it. Sonnet's ORCL fill on 2026-11-11 did
        exactly that: total cash +$60.73, settled balance -$0.2855. Equal to
        `cash` for any book with no unsettled tranches.
        """
        return float(self.cash) - self.settlement.unsettled_total()

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
            price = self.mark(ticker, prices)
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
            price = self.mark(ticker, prices)
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
            price = self.mark(ticker, prices)
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
        # Present only when a held name was valued on a fallback mark, so an
        # ordinary snapshot (and every prompt built from one) is unchanged.
        unpriced = {t: src for t, (_, src) in self.fallback_marks.items()
                    if t in self.holdings and not is_valid_price(prices.get(t))}
        extra = {"unpriced_holdings": unpriced} if unpriced else {}
        return {
            **extra,
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
    def buy(self, ticker: str, shares: float, price: float,
            *, venue_confirmed: bool = False) -> None:
        """Add `shares` to the book at `price`.

        `venue_confirmed` marks a fill the venue has already executed. Those
        always book, even past cash: an order capped at exact cash is sized at
        the reference price and can fill a little above it, and raising then
        would leave a real position out of the book, a G-BOOK halt for every
        book. The overrun stays visible as negative cash, which the state audit
        reports. An intent, the simulator's case, still cannot overdraw.
        """
        if ticker in self.holdings and self.holdings[ticker].is_short:
            # A name held short must be covered (not bought) — mixing both
            # directions in one holding would make avg_cost meaningless.
            raise ValueError(f"Cannot BUY {ticker}: position is short — COVER it first")
        cost = shares * price
        if cost > self.cash + 1e-6:
            if not venue_confirmed:
                raise ValueError(f"Insufficient cash for {ticker}: need {cost:.2f}, have {self.cash:.2f}")
            logger.warning("[%s] confirmed fill on %s overran book cash: cost %.4f, cash %.4f",
                           self.model_key, ticker, cost, self.cash)
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
        if abs(h.shares) <= self.no_position_shares():
            del self.holdings[ticker]
        return cost

    @staticmethod
    def no_position_shares() -> float:
        """The largest share count that means "no position": venue precision.

        Derived from the venue's own QTY_DECIMALS, not chosen. A remainder above
        this is shares the broker still holds, so the book keeps it exactly as
        booked. The 0.01-share threshold this replaces could delete up to $13.93
        of LLY from a book while the broker kept the shares.

        Imported here, not at module level: the execution package imports this
        module while it initializes.
        """
        from ..execution.broker import QTY_DECIMALS
        return 10.0 ** -QTY_DECIMALS

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
        # Signed on purpose: the 1e-6 oversell allowance above can leave a tiny
        # negative remainder, which must close the position, not open a short.
        if h.shares <= self.no_position_shares():
            del self.holdings[ticker]
        return proceeds

    def sweep_ghost_positions(self) -> list[str]:
        """Drop any holding at or below venue precision (float residue only).

        Runs across the whole book after every sell and cover, so it must never
        remove a real position: a small holding in another name is shares the
        broker holds. Returns the list of ticker symbols swept (for logging).
        """
        # Compare on magnitude so live shorts (negative shares) are never
        # mistaken for residue.
        limit = self.no_position_shares()
        ghosts = [t for t, h in self.holdings.items() if abs(h.shares) <= limit]
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
            price = self.mark(ticker, prices)
            if h.is_short:
                self.cover(ticker, abs(h.shares), price)
            else:
                self.sell(ticker, h.shares, price)


# ----- persistence -----

def _state_path(model_key: str) -> Path:
    return STATE_DIR / f"{model_key}.json"


def read_book_inception(model_key: str) -> tuple[str, float] | None:
    """(inception_date, inception_value) from the book's state file, or None.

    Strictly a read, for the reporting path. Unlike load_portfolio it never
    creates a book and never raises InceptionEpochError, so a dashboard build or
    alert sweep can ask where a book's epoch begins without being able to incept
    one. None means no readable state file, i.e. the book has not incepted.
    """
    try:
        with open(_state_path(model_key), "r", encoding="utf-8") as f:
            data = json.load(f)
        return str(data["inception_date"]), float(data["inception_value"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def init_portfolio(model_key: str, mode: str | None = None) -> Portfolio:
    """Create a fresh portfolio for a model. Used at each book's inception.

    Inception date is the book's own first session, from the NYSE calendar: the
    current session if the market is open, otherwise the next one. It is never
    the wall clock, and it is never settings.experiment_start_date, which stays
    a lab-level constant (the experiment's start, not any one book's). Until the
    2026-11-06 cutover this read the lab constant, which was correct only while
    every book had incepted on the experiment's first day; a book incepted at
    the cutover would have recorded April 9.

    Capital comes from the guarded lookup, so a mode whose per-book figure has
    not been confirmed raises instead of seeding six books at a stale default.
    """
    settings = load_settings()
    mode = mode or effective_mode(model_key, settings)
    capital = starting_capital(settings, mode)
    # Local import: the market-data module pulls in yfinance and pandas, which
    # nothing else in this module needs.
    from ..data.market_data import inception_session_date
    today = datetime.utcnow().strftime("%Y-%m-%d")
    inception_date = inception_session_date()
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
    # Per-book: a non-cohort book in a venue mode stays on its paper epoch.
    current = effective_mode(model_key, settings)
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
        fill_overrun_usd=float(data.get("fill_overrun_usd", 0.0)),
    )


def save_portfolio(p: Portfolio) -> None:
    p.last_updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    # The overrun explanation is carried while the SETTLED balance is negative
    # (FILL_OVERRUN measures settled cash); it clears once settlement or a sale
    # brings that balance back to zero. Settled >= 0 implies total cash >= 0.
    if p.settled_balance() >= -1e-6:
        p.fill_overrun_usd = 0.0
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
        "inception_epoch": p.inception_epoch or effective_mode(p.model_key),
    }
    # Present only while a book carries one, so an ordinary state file is unchanged.
    if p.fill_overrun_usd:
        out["fill_overrun_usd"] = p.fill_overrun_usd
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_state_path(p.model_key), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
