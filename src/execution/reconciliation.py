"""Level-2 (broker-authoritative) reconciliation -- G-BOOK.

Registered mechanism: docs/prereg/tier2_novel_sections.md, "Phase B account
structure -- cash branch", partition control (3): "the sum of book positions
and cash reconciles to broker-authoritative account state each cycle;
divergence at either level is a G-BOOK event." Also the second conjunct of
Gate 4's predicate (same document): "...with the book reconciled to
broker-authoritative positions and cash... A cycle fails execution when...
post-cycle reconciliation diverges from broker state; a reconciliation
divergence is additionally a book-integrity event (Gate 2)."

Level 1 (src/portfolio/audit.py) replays fills through our own Portfolio
accounting and never calls the venue -- it catches an internal bookkeeping
bug (a fill that didn't conserve value at the moment it happened). Level 2
here catches a different failure class entirely: a fill our ledger recorded
that the venue disagrees with, a position the venue holds that no book
claims, or drift introduced between a fill landing and the state file being
written. Level 1 passing says nothing about level 2, and this module never
re-derives level 1's per-trade check -- it only compares aggregates.

HALT-never-repair, reused from level 1's own discipline (the monthly
builder's conservation audit HALTS the build on a violation rather than
adjusting a figure to make it pass): a divergence here is reported and
halts the affected books' trading (see `handle_reconciliation_result`);
nothing in this module ever adjusts a book's cash or holdings to match the
broker. Repairing by overwriting the ledger from broker state would hide
the exact bug class this exists to catch.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config_loader import RECONCILIATION_DIR
from ..portfolio.portfolio import Portfolio, save_portfolio
from .broker import BrokerClient, from_venue_symbol

logger = logging.getLogger("llmlab.execution.reconciliation")

# Cash tolerance and the unallocated-cash baseline come from
# config/settings.json -> reconciliation, via config_loader.reconciliation_params
# (PROPOSED 2026-10-04, pending hub registration -- no ledger entry carries them
# yet). The tolerance is NOT slack for bugs: it accommodates regulatory fees
# (SEC Section 31 on sells, FINRA TAF) that the pipeline ledger does not model --
# Portfolio.buy/sell/short/cover compute cash purely from shares * fill price
# (verified by reading them; no fee deduction exists anywhere in src/portfolio
# or src/execution). Tightening it requires modeling fees explicitly first.
#
# The cash comparison is against sum(book cash) + unallocated cash, NOT sum(book
# cash) alone: the venue account holds cash no book owns (live: the $500 master
# reserve; broker_paper: whatever the paper account was funded above the books).
# Comparing against books alone raises a G-BOOK event on cycle one.

# Position tolerance reuses the ledger's own dust threshold rather than
# inventing a second one -- level 1 and level 2 agree on what "no position"
# means.
POSITION_TOLERANCE_SHARES = Portfolio.GHOST_SHARES_EPSILON


@dataclass
class GBookEvent:
    kind: str                  # "cash" or "position"
    ticker: str = ""            # empty for the cash-level event
    ledger_value: float = 0.0
    broker_value: float = 0.0
    delta: float = 0.0
    tolerance: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ticker": self.ticker,
               "ledger_value": self.ledger_value, "broker_value": self.broker_value,
               "delta": self.delta, "tolerance": self.tolerance}


@dataclass
class ReconciliationResult:
    cycle_id: str
    timestamp: str
    books: list[str]
    ledger_cash_total: float     # sum(book cash) + unallocated_cash
    broker_cash: float
    ledger_positions: dict[str, float]     # net signed shares, by ticker
    broker_positions: dict[str, float]
    events: list[GBookEvent] = field(default_factory=list)
    unallocated_cash: float = 0.0

    @property
    def clean(self) -> bool:
        return not self.events

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id, "timestamp": self.timestamp,
            "books": self.books, "clean": self.clean,
            "ledger_cash_total": self.ledger_cash_total, "broker_cash": self.broker_cash,
            "unallocated_cash": self.unallocated_cash,
            "ledger_positions": self.ledger_positions, "broker_positions": self.broker_positions,
            "events": [e.to_dict() for e in self.events],
        }


def reconcile(portfolios: list[Portfolio], broker: BrokerClient,
             cycle_id: str, *, cash_tolerance_usd: float,
             unallocated_cash_usd: float) -> ReconciliationResult:
    """Diff the aggregate pipeline ledger against broker-authoritative state.

    `portfolios` must be every ENABLED book's freshly-loaded (post-fill)
    state for this cycle -- a stale or partial set produces a false G-BOOK
    event, so the caller reloads from disk after every book's thread has
    saved (see `run_post_cycle_reconciliation`).
    """
    # Expected venue cash = what the books own + what the account holds outside
    # any book. Both terms are in the record so a divergence is attributable.
    ledger_cash_total = sum(p.cash for p in portfolios) + unallocated_cash_usd
    ledger_positions: dict[str, float] = {}
    for p in portfolios:
        for ticker, h in p.holdings.items():
            # Constant-price-mark convention reused from level 1: a
            # position's sign IS its direction (negative = short); summing
            # signed share counts across books, not valuing them, is what
            # makes this comparable to the venue's own signed qty.
            ledger_positions[ticker] = ledger_positions.get(ticker, 0.0) + h.shares
    # Dust the aggregate exactly as Portfolio.sweep_ghost_positions does at
    # the book level, so summing several books' fractional shares doesn't
    # fail on float noise around a genuinely flat position.
    ledger_positions = {t: q for t, q in ledger_positions.items()
                        if abs(q) >= POSITION_TOLERANCE_SHARES}

    account = broker.get_account()
    broker_cash = float(account.get("cash") or 0.0)
    broker_positions: dict[str, float] = {}
    for bp in broker.get_positions():
        ticker = from_venue_symbol(str(bp.get("symbol") or ""))
        # Alpaca reports a short position's qty already negative on a margin
        # account (verified 2026-08-29 spike) -- no separate side lookup
        # needed against this venue.
        broker_positions[ticker] = float(bp.get("qty") or 0.0)

    events: list[GBookEvent] = []
    cash_delta = ledger_cash_total - broker_cash
    if abs(cash_delta) > cash_tolerance_usd:
        events.append(GBookEvent(kind="cash", ledger_value=ledger_cash_total,
                                 broker_value=broker_cash, delta=cash_delta,
                                 tolerance=cash_tolerance_usd))

    for ticker in sorted(set(ledger_positions) | set(broker_positions)):
        lv = ledger_positions.get(ticker, 0.0)
        bv = broker_positions.get(ticker, 0.0)
        delta = lv - bv
        if abs(delta) > POSITION_TOLERANCE_SHARES:
            events.append(GBookEvent(kind="position", ticker=ticker,
                                     ledger_value=lv, broker_value=bv, delta=delta,
                                     tolerance=POSITION_TOLERANCE_SHARES))

    return ReconciliationResult(
        cycle_id=cycle_id, timestamp=datetime.now(timezone.utc).isoformat(),
        books=sorted(p.model_key for p in portfolios),
        ledger_cash_total=ledger_cash_total, broker_cash=broker_cash,
        ledger_positions=ledger_positions, broker_positions=broker_positions,
        events=events, unallocated_cash=unallocated_cash_usd,
    )


def record_reconciliation(result: ReconciliationResult,
                          out_dir: Path | None = None) -> Path:
    """Append-only, one line per cycle -- the join target for Gate 4's
    execution_metrics computation (src/analytics/execution_metrics.py),
    keyed by cycle_id against each book's decision-log record."""
    out_dir = out_dir or RECONCILIATION_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    month = result.timestamp[:7]  # YYYY-MM, same tagging convention as trades/
    path = out_dir / f"{month}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result.to_dict()) + "\n")
    return path


def handle_reconciliation_result(result: ReconciliationResult,
                                 portfolios: list[Portfolio],
                                 send_alert_fn=None) -> None:
    """HALT-never-repair. On any G-BOOK event: alert once, halt every
    enabled book (not just the one a divergence might look attributable to
    -- a shared-account cash divergence is not cleanly attributable to one
    book, and continuing ANY book's trading against a ledger already known
    to disagree with the venue is the risk this exists to remove), and save.
    Never adjusts a book's own cash or holdings to match the broker.

    `send_alert_fn` is injectable for tests; defaults to the real alerter.
    """
    if result.clean:
        return
    if send_alert_fn is None:
        from ..alerts.alerter import send_alert as send_alert_fn
    logger.critical("G-BOOK: reconciliation diverged for cycle %s: %s",
                    result.cycle_id, [e.to_dict() for e in result.events])
    send_alert_fn(
        "CRITICAL", f"G-BOOK reconciliation divergence — {len(result.events)} event(s)",
        f"Cycle {result.cycle_id}: " + "; ".join(
            f"{e.kind}{(' ' + e.ticker) if e.ticker else ''} ledger={e.ledger_value:.4f} "
            f"broker={e.broker_value:.4f} delta={e.delta:.4f} (tolerance {e.tolerance:.4f})"
            for e in result.events),
        {"cycle_id": result.cycle_id, "books": result.books},
        kind="gbook_divergence", dedup_key=f"gbook_divergence:{result.cycle_id}",
    )
    for p in portfolios:
        if not p.halted:
            p.halted = True
            save_portfolio(p)


def run_post_cycle_reconciliation(broker: BrokerClient, cycle_id: str,
                                  enabled_book_keys: list[str]) -> ReconciliationResult:
    """The pipeline's post-cycle hook (broker modes only): reload every
    enabled book's just-saved state, reconcile, persist, and halt on
    divergence. Import-local `load_portfolio` avoids a module-level
    portfolio-package import cycle with execution."""
    from ..config_loader import load_settings, reconciliation_params
    from ..portfolio.portfolio import load_portfolio
    params = reconciliation_params(load_settings())
    portfolios = [load_portfolio(k) for k in enabled_book_keys]
    result = reconcile(portfolios, broker, cycle_id, **params)
    record_reconciliation(result)
    handle_reconciliation_result(result, portfolios)
    return result
