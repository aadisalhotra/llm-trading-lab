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

from ..config_loader import RECONCILIATION_DIR, TRADES_DIR
from ..portfolio.audit import book_fill_differences
from ..portfolio.portfolio import Portfolio, save_portfolio
from .broker import BrokerClient, from_venue_symbol

logger = logging.getLogger("llmlab.execution.reconciliation")

# Cash tolerance and the unallocated-cash baseline come from
# config/settings.json -> reconciliation, via config_loader.reconciliation_params
# (registered 2026-10-04: ledger
# operational_events.2026-10[level2_reconciliation_parameters_registered]). The tolerance is NOT slack for bugs: it accommodates regulatory fees
# (SEC Section 31 on sells, FINRA TAF) that the pipeline ledger does not model --
# Portfolio.buy/sell/short/cover compute cash purely from shares * fill price
# (verified by reading them; no fee deduction exists anywhere in src/portfolio
# or src/execution). Tightening it requires modeling fees explicitly first.
#
# The cash comparison is against sum(book cash) + unallocated cash, NOT sum(book
# cash) alone: the venue account holds cash no book owns (live: the $500 master
# reserve; broker_paper: whatever the paper account was funded above the books).
# Comparing against books alone raises a G-BOOK event on cycle one.
#
# Every term is its own named field, and the expected venue state is their
# EXPLICIT sum (hub 2026-11-03 ③). Until then the record carried
# `ledger_cash_total` and `ledger_positions`, names that read as "what the books
# hold" but summed in unallocated cash and probe inventory; a reader attributing
# a divergence to the books from those fields would have been misled. No record
# was ever written under the old names (production never ran a broker mode
# before the cutover), so there is no migration.
#
#   expected_cash      = book_cash + unallocated_cash + probe_cash
#   expected_positions = book_positions + probe_positions   (per ticker, dusted)

# Level 1 and level 2 agree on what "no position" means:
# Portfolio.no_position_shares(), venue precision. Whether two non-zero
# quantities diverge is a dollar question, judged per name at the current mark
# (config reconciliation.position_tolerance_usd), because a fixed share count
# means $0.29 on PFE and $13.93 on LLY.
LEVEL1_BASELINE_FILE = "level1_baselines.json"


@dataclass
class GBookEvent:
    kind: str                  # "cash" or "position"
    ticker: str = ""            # empty for the cash-level event
    # The expected side (expected_cash / expected_positions[ticker]): books,
    # unallocated cash and probe together, not the books alone.
    expected_value: float = 0.0
    broker_value: float = 0.0
    delta: float = 0.0          # expected_value - broker_value
    tolerance: float = 0.0      # dollars, for every kind
    # Position and orphan events only. None means no mark could be found, and
    # an unpriced difference is always an event.
    mark: float | None = None
    delta_usd: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ticker": self.ticker,
               "expected_value": self.expected_value, "broker_value": self.broker_value,
               "delta": self.delta, "tolerance": self.tolerance,
               "mark": self.mark, "delta_usd": self.delta_usd}


@dataclass
class ReconciliationResult:
    cycle_id: str
    timestamp: str
    books: list[str]
    # --- cash terms -----------------------------------------------------
    book_cash: float             # sum of the enabled books' own cash, nothing else
    broker_cash: float
    # --- position terms (net signed shares by ticker, dusted) ------------
    book_positions: dict[str, float]       # the enabled books' inventory, nothing else
    broker_positions: dict[str, float]
    events: list[GBookEvent] = field(default_factory=list)
    # Cash the venue holds that no book owns: the registered baseline
    # (config reconciliation.unallocated_cash_usd; live: the master reserve).
    unallocated_cash: float = 0.0
    # Residual cash left by administrative probe orders
    # (src/execution/administrative.py). Its own term, folded into neither of
    # the others, so a divergence can be attributed.
    probe_cash: float = 0.0
    # Probe positions its committed records account for (hub ruling
    # 2026-11-02 ②: attributable, not exempt).
    probe_positions: dict[str, float] = field(default_factory=dict)
    # Broker positions no book and no probe record owns. Empty is expected.
    # ticker -> {shares, mark, usd}.
    orphan_positions: dict[str, dict[str, float | None]] = field(default_factory=dict)
    # Level 1: book -> summed dollar difference between its holdings and its own
    # fills. Compared at book level because the broker side is one account.
    book_fill_aggregate_usd: dict[str, float] | None = None

    @property
    def expected_cash(self) -> float:
        """What the venue should hold: the three cash terms, summed explicitly."""
        return self.book_cash + self.unallocated_cash + self.probe_cash

    @property
    def expected_positions(self) -> dict[str, float]:
        """What the venue should hold, by ticker: book inventory plus probe
        inventory, summed explicitly, then dusted like a book."""
        out = dict(self.book_positions)
        for ticker, q in self.probe_positions.items():
            out[ticker] = out.get(ticker, 0.0) + q
        return _dust(out)

    @property
    def clean(self) -> bool:
        return not self.events

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id, "timestamp": self.timestamp,
            "books": self.books, "clean": self.clean,
            "book_cash": self.book_cash, "unallocated_cash": self.unallocated_cash,
            "probe_cash": self.probe_cash, "expected_cash": self.expected_cash,
            "broker_cash": self.broker_cash,
            "book_positions": self.book_positions, "probe_positions": self.probe_positions,
            "expected_positions": self.expected_positions,
            "broker_positions": self.broker_positions,
            "orphan_positions": self.orphan_positions,
            "book_fill_aggregate_usd": self.book_fill_aggregate_usd,
            "events": [e.to_dict() for e in self.events],
        }


def _dust(positions: dict[str, float]) -> dict[str, float]:
    """Drop |qty| at or below venue precision, as
    Portfolio.sweep_ghost_positions does at the book level, so summing several
    books' fractional shares doesn't fail on float noise around a flat position."""
    limit = Portfolio.no_position_shares()
    return {t: q for t, q in positions.items() if abs(q) > limit}


def _broker_mark(position: dict[str, Any]) -> float | None:
    """The venue's current price for one of its positions, or None."""
    price = float(position.get("current_price") or 0.0)
    if price > 0:
        return price
    qty = float(position.get("qty") or 0.0)
    value = float(position.get("market_value") or 0.0)
    return abs(value / qty) if qty and value else None


def reconcile(portfolios: list[Portfolio], broker: BrokerClient,
             cycle_id: str, *, cash_tolerance_usd: float,
             unallocated_cash_usd: float,
             position_tolerance_usd: float,
             probe_cash_usd: float = 0.0,
             probe_positions: dict[str, float] | None = None,
             ) -> ReconciliationResult:
    """Diff the aggregate pipeline ledger against broker-authoritative state.

    `portfolios` must be every ENABLED book's freshly-loaded (post-fill)
    state for this cycle -- a stale or partial set produces a false G-BOOK
    event, so the caller reloads from disk after every book's thread has
    saved (see `run_post_cycle_reconciliation`).

    `probe_cash_usd` is the residual cash committed probe runs left in the
    account (administrative.administrative_cash_usd). It is cash no book owns,
    so it is an expected-side term of its own rather than a wider tolerance.

    `probe_positions` are the probe positions its committed records account
    for (administrative.administrative_positions). They are an expected-side
    term, so an accounted probe position reconciles and does not halt. A probe
    position the records do NOT account for still diverges and halts.

    Book inventory is dusted before the probe term is added. The probe trades
    only a symbol outside the trading universe (scripts/barrier_probe.SYMBOL,
    pinned by tests/test_barrier_probe.py), so no ticker carries both terms
    and this cannot change an outcome; it is what makes the recorded terms
    sum exactly to expected_positions.

    A position difference is an event at `position_tolerance_usd` or more, per
    name, at the venue's current price (a book's average cost when the venue
    holds none of the name). A broker position nothing owns goes on the orphan
    line: an event at the same dollar bound, a warning below it.
    """
    book_positions: dict[str, float] = {}
    book_marks: dict[str, float] = {}
    for p in portfolios:
        for ticker, h in p.holdings.items():
            # Constant-price-mark convention reused from level 1: a
            # position's sign IS its direction (negative = short); summing
            # signed share counts across books, not valuing them, is what
            # makes this comparable to the venue's own signed qty.
            book_positions[ticker] = book_positions.get(ticker, 0.0) + h.shares
            book_marks.setdefault(ticker, h.avg_cost)

    account = broker.get_account()
    broker_positions: dict[str, float] = {}
    broker_marks: dict[str, float] = {}
    for bp in broker.get_positions():
        ticker = from_venue_symbol(str(bp.get("symbol") or ""))
        # Alpaca reports a short position's qty already negative on a margin
        # account (verified 2026-08-29 spike) -- no separate side lookup
        # needed against this venue.
        broker_positions[ticker] = float(bp.get("qty") or 0.0)
        mark = _broker_mark(bp)
        if mark is not None:
            broker_marks[ticker] = mark

    result = ReconciliationResult(
        cycle_id=cycle_id, timestamp=datetime.now(timezone.utc).isoformat(),
        books=sorted(p.model_key for p in portfolios),
        book_cash=sum(p.cash for p in portfolios),
        broker_cash=float(account.get("cash") or 0.0),
        book_positions=_dust(book_positions), broker_positions=broker_positions,
        unallocated_cash=unallocated_cash_usd, probe_cash=probe_cash_usd,
        probe_positions=dict(probe_positions or {}),
    )

    # The comparison reads the explicit sums, the same properties the record
    # writes, so what is compared and what is recorded cannot disagree.
    expected_cash = result.expected_cash
    cash_delta = expected_cash - result.broker_cash
    if abs(cash_delta) > cash_tolerance_usd:
        result.events.append(GBookEvent(kind="cash", expected_value=expected_cash,
                                        broker_value=result.broker_cash, delta=cash_delta,
                                        tolerance=cash_tolerance_usd))

    expected_positions = result.expected_positions
    no_position = Portfolio.no_position_shares()
    for ticker in sorted(set(expected_positions) | set(broker_positions)):
        ev = expected_positions.get(ticker, 0.0)
        bv = broker_positions.get(ticker, 0.0)
        delta = ev - bv
        if abs(delta) <= no_position:
            continue
        mark = broker_marks.get(ticker) or book_marks.get(ticker)
        delta_usd = abs(delta) * mark if mark else None
        orphan = ticker not in expected_positions
        if orphan:
            result.orphan_positions[ticker] = {"shares": bv, "mark": mark, "usd": delta_usd}
        # The small allowance keeps a difference of exactly the bound from
        # falling under it on float noise.
        if delta_usd is None or delta_usd >= position_tolerance_usd - 1e-9:
            result.events.append(GBookEvent(
                kind="orphan" if orphan else "position", ticker=ticker,
                expected_value=ev, broker_value=bv, delta=delta,
                tolerance=position_tolerance_usd, mark=mark, delta_usd=delta_usd))
    return result


def _book_fills(book: str, since_date: str, exclusive: bool,
                trades_dir: Path) -> list[tuple[str, float, float]]:
    """(ticker, signed shares, price) for each of a book's executed fills."""
    fills: list[tuple[str, float, float]] = []
    for path in sorted(trades_dir.glob(f"{book}_20*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    # A torn line must not silently shrink the fill history.
                    raise ValueError(f"{path.name}:{n} is not valid JSON: {exc}") from exc
                date = rec.get("date", "")
                if date < since_date or (exclusive and date == since_date):
                    continue
                for e in rec.get("executions") or []:
                    if not e.get("executed"):
                        continue
                    sign = {"BUY": 1.0, "COVER": 1.0, "SELL": -1.0, "SHORT": -1.0}.get(e.get("side"))
                    if sign is not None:
                        fills.append((e["ticker"], sign * float(e["shares"]),
                                      float(e["fill_price"])))
    return fills


def book_fill_aggregates(portfolios: list[Portfolio],
                         broker_marks: dict[str, float],
                         trades_dir: Path | None = None,
                         baseline_path: Path | None = None) -> dict[str, float]:
    """Level 1: for each book, the summed dollar difference between its holdings
    and what its own fills say it should hold.

    A book with a baseline entry starts from those positions and counts fills
    after `as_of_date`. That exists for DeepSeek, whose paper-era book does not
    equal the sum of its logged fills (launch-window positions and the old
    0.01-share deletions), so its history would alert on the first cycle.
    """
    trades_dir = trades_dir or TRADES_DIR
    baseline_path = baseline_path or (RECONCILIATION_DIR / LEVEL1_BASELINE_FILE)
    baselines: dict[str, Any] = {}
    if baseline_path.exists():
        with open(baseline_path, encoding="utf-8") as f:
            baselines = json.load(f)
    out: dict[str, float] = {}
    for p in portfolios:
        entry = baselines.get(p.model_key)
        since = entry["as_of_date"] if entry else p.inception_date
        fills = _book_fills(p.model_key, since, bool(entry), trades_dir)
        holdings = {t: h.shares for t, h in p.holdings.items()}
        diffs = book_fill_differences(holdings, fills,
                                      (entry or {}).get("positions"))
        last_fill = {t: px for t, _, px in fills}
        total = 0.0
        for ticker, diff in diffs.items():
            held = p.holdings.get(ticker)
            mark = (broker_marks.get(ticker) or last_fill.get(ticker)
                    or (held.avg_cost if held else 0.0))
            total += abs(diff) * mark
        out[p.model_key] = total
    return out


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

    An orphan under the position tolerance is a warning and halts nothing.

    `send_alert_fn` is injectable for tests; defaults to the real alerter.
    """
    event_tickers = {e.ticker for e in result.events if e.kind == "orphan"}
    small_orphans = {t: o for t, o in result.orphan_positions.items()
                     if t not in event_tickers}
    if result.clean and not small_orphans:
        return
    if send_alert_fn is None:
        from ..alerts.alerter import send_alert as send_alert_fn
    for ticker, orphan in small_orphans.items():
        logger.warning("Orphan broker position in cycle %s: %s %s", result.cycle_id,
                       ticker, orphan)
        send_alert_fn(
            "WARN", f"Orphan broker position: {ticker}",
            f"Cycle {result.cycle_id}: the broker holds {orphan['shares']:g} {ticker} "
            f"(${orphan['usd']:.2f}) that no book owns.",
            {"cycle_id": result.cycle_id, "ticker": ticker},
            kind="orphan_position", dedup_key=f"orphan_position:{ticker}",
        )
    if result.clean:
        return
    logger.critical("G-BOOK: reconciliation diverged for cycle %s: %s",
                    result.cycle_id, [e.to_dict() for e in result.events])
    send_alert_fn(
        "CRITICAL", f"G-BOOK reconciliation divergence — {len(result.events)} event(s)",
        f"Cycle {result.cycle_id}: " + "; ".join(
            f"{e.kind}{(' ' + e.ticker) if e.ticker else ''} expected={e.expected_value:.4f} "
            f"broker={e.broker_value:.4f} delta={e.delta:.4f}"
            f"{f' (${e.delta_usd:.2f})' if e.delta_usd is not None else ''} "
            f"(tolerance ${e.tolerance:.2f})"
            for e in result.events),
        {"cycle_id": result.cycle_id, "books": result.books},
        kind="gbook_divergence", dedup_key=f"gbook_divergence:{result.cycle_id}",
    )
    for p in portfolios:
        if not p.halted:
            p.halted = True
            save_portfolio(p)


def _check_book_fill_aggregates(result: ReconciliationResult, settings: dict[str, Any],
                                broker: BrokerClient, send_alert_fn=None) -> None:
    """Compute and alert on the per-book level-1 aggregate, for every enabled
    book including paper-only ones. Alerts and records; halts nothing. A failure
    here must not cost the cycle its reconciliation record, so it alerts instead
    of raising."""
    from ..alerts.events import _enabled_model_keys
    from ..config_loader import book_aggregate_bound_usd
    from ..portfolio.portfolio import load_portfolio
    if send_alert_fn is None:
        from ..alerts.alerter import send_alert as send_alert_fn
    try:
        bound = book_aggregate_bound_usd(settings)
        marks = {from_venue_symbol(str(bp.get("symbol") or "")): _broker_mark(bp)
                 for bp in broker.get_positions()}
        portfolios = [load_portfolio(k) for k in _enabled_model_keys(settings)]
        aggregates = book_fill_aggregates(portfolios, {t: m for t, m in marks.items() if m})
    except Exception as exc:
        logger.exception("Per-book fill aggregate failed for cycle %s", result.cycle_id)
        send_alert_fn("CRITICAL", "Per-book fill aggregate could not run", str(exc),
                      {"cycle_id": result.cycle_id}, kind="book_fill_aggregate_error",
                      dedup_key=f"book_fill_aggregate_error:{result.cycle_id}")
        return
    result.book_fill_aggregate_usd = aggregates
    for book, usd in aggregates.items():
        if usd >= bound:
            send_alert_fn(
                "CRITICAL", f"Book differs from its own fills: {book}",
                f"Cycle {result.cycle_id}: {book}'s holdings differ from its fills by "
                f"${usd:.2f} in aggregate (bound ${bound:.2f}).",
                {"cycle_id": result.cycle_id, "model": book},
                kind="book_fill_aggregate",
                dedup_key=f"book_fill_aggregate:{book}:{result.cycle_id}",
            )


def run_post_cycle_reconciliation(broker: BrokerClient, cycle_id: str,
                                  enabled_book_keys: list[str]) -> ReconciliationResult:
    """The pipeline's post-cycle hook (broker modes only): reload every
    enabled book's just-saved state, reconcile, persist, and halt on
    divergence. Import-local `load_portfolio` avoids a module-level
    portfolio-package import cycle with execution."""
    from ..config_loader import load_settings, reconciliation_params
    from ..portfolio.portfolio import load_portfolio
    from .administrative import administrative_cash_usd, administrative_positions
    settings = load_settings()
    params = reconciliation_params(settings)
    portfolios = [load_portfolio(k) for k in enabled_book_keys]
    # Keyed on the same mode reconciliation_params resolved the baseline for.
    # Both readers return nothing for any mode but broker_paper, so a live
    # cycle never counts a probe term.
    mode = settings.get("mode", "paper")
    result = reconcile(portfolios, broker, cycle_id, **params,
                       probe_cash_usd=administrative_cash_usd(mode),
                       probe_positions=administrative_positions(mode))
    _check_book_fill_aggregates(result, settings, broker)
    record_reconciliation(result)
    handle_reconciliation_result(result, portfolios)
    return result
