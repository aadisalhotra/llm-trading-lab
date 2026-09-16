"""Trade executor — simulator, broker-paper, or live, switched by config.

Three modes, not two (see `execution/broker.py` for the mode taxonomy):

  * `paper`        — the in-process simulator that has run Phase A since April.
                     Fills are simulated at the latest market price and no
                     venue is contacted. Unchanged by the broker work.
  * `broker_paper` — the real order lifecycle against Alpaca's paper endpoint.
                     This is what the registered October validation month runs:
                     the true submission / fill / rejection / reconciliation
                     path with zero capital at risk.
  * `live`         — the same path against the live endpoint, from Nov 1.

The rule that separates them from the old code: in both broker modes the book
is mutated ONLY from a terminal, broker-confirmed fill. Previously the book
moved at intent, so a rejected order still changed the portfolio — a
manufactured reconciliation divergence on every rejection, and, in the stop
path, a book that flattened while the venue position stayed open.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..config_loader import load_settings
from ..portfolio import Portfolio
from ..portfolio.settlement import settlement_days, settlement_enforcement_active
from .barrier import CycleBarrier, Intent
from .broker import (
    BROKER_MODES,
    MIN_ORDER_NOTIONAL,
    BrokerAPIError,
    BrokerClient,
    BrokerOrder,
    build_order_id,
)

logger = logging.getLogger("llmlab.execution")


# Execution-constraint codes. These mark venue-imposed limits, not pipeline
# failures: the linkage worked and the order was correctly not sent. Under the
# registered censoring principle a blocked intention enters no behavioral
# outcome metric — which it cannot, since every behavioral reader filters on
# `executed`, and a blocked purchase is never executed.
CONSTRAINT_UNSETTLED_FUNDS = "UNSETTLED_FUNDS"        # blocked outright
CONSTRAINT_UNSETTLED_FUNDS_CAPPED = "UNSETTLED_FUNDS_CAPPED"  # filled, but smaller

# Venue minimum order size ($1 cost basis, verified 2026-08-29). Fractional
# sizing can produce sub-$1 orders that whole-share sizing never could, so this
# is caught before submission rather than collected as a venue rejection.
#
# Registered 2026-09-15 (docs/prereg/tier2_novel_sections.md, "Below-venue-
# minimum execution constraint"): a legitimate venue limit, therefore
# G-EXEC-successful, by the same reasoning as the settled-funds block.
CONSTRAINT_BELOW_VENUE_MINIMUM = "BELOW_VENUE_MINIMUM"

# Registered 2026-08-29: an order still unfilled at the 5-minute fill deadline
# has its remainder cancelled; partial fills stand. Execution-SUCCESSFUL
# (occasional-unavailability class), and separately a disclosed per-model
# descriptor so chronic slippage cannot hide behind a passing G-EXEC.
CONSTRAINT_UNFILLED_AT_DEADLINE = "UNFILLED_AT_DEADLINE"

# The account-wide opposite-side collision: six books share one account, and
# the venue rejects a SELL on a symbol while any BUY on it is open (HTTP 403,
# verified 2026-08-29; ~7.7% of Phase A trading cycles contain the condition).
# This is an artifact of OUR account structure, not a market constraint, so it
# is deliberately NOT filed with legitimate rejections. This is what the P1
# barrier (below, and .barrier.CycleBarrier) exists to serialize away; a
# rejection still classified under THIS constant means the barrier either
# wasn't running (paper/no-barrier caller) or did not identify the order as
# contended (e.g. a stale open order from outside this cycle).
CONSTRAINT_WASH_TRADE_BLOCK = "WASH_TRADE_BLOCK"

# Registered 2026-09-15 (docs/prereg/tier2_novel_sections.md, Gate 4 amended):
# a wash-trade rejection on an order the P1 barrier DID gate (held pending its
# contender's terminal state) and STILL got rejected once released. This is
# G-EXEC-successful under Gate 4 — expectation ~=0; a nonzero rate is a
# barrier-implementation signal, not a routine venue collision.
CONSTRAINT_WASH_REJECT_POST_BARRIER = "WASH_REJECT_POST_BARRIER"

# Broker-side mapping (verified against the actual submission calls below,
# not assumed): BUY and COVER both submit as a broker "buy"; SELL and SHORT
# both submit as a broker "sell". This is also the wash-trade contention
# grouping the P1 barrier keys on — see .barrier.BUY_SIDE_ACTIONS.
_BROKER_SIDE = {"BUY": "buy", "COVER": "buy", "SELL": "sell", "SHORT": "sell"}


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
    # Broker-authoritative order record, present only in broker modes. This is
    # the per-book side of the registered two-level conservation audit and the
    # tagged-order set G-EXEC computes over.
    broker_order: dict[str, Any] | None = None
    # True when a risk stop could not be placed at the venue. The book is
    # correctly left unchanged, which means the position is STILL OPEN — the
    # loudest possible failure in the system, and the pipeline alerts on it.
    stop_unprotected: bool = False


@dataclass
class _Placement:
    """What the venue actually did with one order."""
    filled_qty: float = 0.0
    fill_price: float = 0.0
    order_id: str = ""
    error: str = ""
    constraint: str = ""
    order: BrokerOrder | None = None

    @property
    def moved(self) -> bool:
        """Whether anything filled. The book moves iff this is True."""
        return self.filled_qty > 0 and self.fill_price > 0


@dataclass
class _SizedOrder:
    """A decision sized into a concrete order, not yet submitted.

    Broker-mode-only intermediate shape (P1): `execute_decisions` sizes every
    decision in the batch first (so the settled-cash reservation accumulator
    can run across them) and only then submits, so no order is placed before
    every book has registered its intents with the cycle barrier.
    """
    decision: dict[str, Any]
    action: str            # BUY / SELL / SHORT / COVER
    ticker: str
    shares: float
    price: float            # reference price the size was computed against
    broker_side: str        # "buy" / "sell"
    constraint: str = ""    # pre-submission constraint, e.g. UNSETTLED_FUNDS_CAPPED


class Executor:
    """Single executor across the simulator, broker-paper and live modes."""

    def __init__(self, cycle_id: str | None = None) -> None:
        self.settings = load_settings()
        self.mode = self.settings["mode"]
        self.broker: BrokerClient | None = None
        # Order tagging. Every order carries its book identity via
        # client_order_id (registered partition control (1)); the cycle id
        # makes the tag unique, which the venue requires permanently — even a
        # cancelled id stays consumed.
        self.cycle_id = cycle_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._order_seq: dict[str, int] = {}
        if self.mode in BROKER_MODES:
            exec_cfg = (self.settings.get("execution") or {})
            self.broker = BrokerClient(
                self.mode,
                fill_deadline_seconds=int(exec_cfg.get("fill_deadline_seconds", 300)),
                poll_interval_seconds=float(exec_cfg.get("poll_interval_seconds", 2.0)),
            )
            logger.info("Broker execution ACTIVE: mode=%s endpoint=%s deadline=%ds",
                        self.mode, self.broker.base_url,
                        self.broker.fill_deadline_seconds)

    @property
    def broker_enabled(self) -> bool:
        """True when orders reach a real venue."""
        return self.broker is not None

    def _next_order_id(self, book: str) -> str:
        seq = self._order_seq.get(book, 0)
        self._order_seq[book] = seq + 1
        return build_order_id(book, self.cycle_id, seq)

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

    # ----- venue placement -----

    def _place(self, book: str, ticker: str, shares: float, side: str,
               reference_price: float) -> _Placement:
        """Submit one order and resolve it to a terminal broker-confirmed state.

        Returns what the venue actually did. The caller mutates the book from
        this and from nothing else — that is the whole point of the method.
        A rejection, a zero fill or a transport failure all return
        `moved == False`, and the book stays where it was.
        """
        assert self.broker is not None
        notional = abs(shares * reference_price)
        if notional < MIN_ORDER_NOTIONAL:
            # Caught before submission so it costs no venue round-trip and
            # never presents as a rejection we caused.
            logger.info("[%s] %s %s skipped: notional %.2f below the venue "
                        "minimum of %.2f", book, side, ticker, notional,
                        MIN_ORDER_NOTIONAL)
            return _Placement(
                constraint=CONSTRAINT_BELOW_VENUE_MINIMUM,
                error=(f"Order notional {notional:.2f} is below the venue "
                       f"minimum of {MIN_ORDER_NOTIONAL:.2f}"))

        client_order_id = self._next_order_id(book)
        try:
            order, hit_deadline = self.broker.place_and_confirm(
                ticker=ticker, shares=shares, side=side,
                client_order_id=client_order_id)
        except BrokerAPIError as e:
            if e.is_wash_trade:
                # Another book has an opposite-side order open on this symbol.
                # Ours is the one that arrived second. Logged at ERROR because
                # it is a partition leak, not a market event.
                logger.error(
                    "[%s] %s %s REJECTED as a wash trade — another book holds "
                    "an open opposite-side order on this symbol. This is the "
                    "account-structure collision; cross-book ordering is P1.",
                    book, side, ticker)
                return _Placement(order_id=client_order_id,
                                  constraint=CONSTRAINT_WASH_TRADE_BLOCK,
                                  error=f"Wash-trade rejection: {e}")
            if e.is_below_minimum:
                return _Placement(order_id=client_order_id,
                                  constraint=CONSTRAINT_BELOW_VENUE_MINIMUM,
                                  error=str(e))
            logger.error("[%s] %s %s submission failed: %s", book, side, ticker, e)
            return _Placement(order_id=client_order_id, error=str(e))

        placement = _Placement(
            filled_qty=order.filled_qty,
            fill_price=order.filled_avg_price,
            order_id=order.client_order_id,
            order=order,
        )
        if hit_deadline and order.filled_qty < order.requested_qty:
            placement.constraint = CONSTRAINT_UNFILLED_AT_DEADLINE
        if not placement.moved:
            placement.error = placement.error or (
                f"No fill: terminal status {order.status!r} "
                f"(filled {order.filled_qty:g} of {order.requested_qty:g})")
        return placement

    # ----- public API -----

    def execute_decisions(
        self,
        portfolio: Portfolio,
        decisions: list[dict[str, Any]],
        prices: dict[str, float],
        run_date: str | None = None,
        barrier: CycleBarrier | None = None,
    ) -> list[ExecutionResult]:
        """Execute one book's decisions for the cycle.

        `barrier` is the P1 cross-book submission barrier (see .barrier),
        shared by reference across every book's thread for this cycle. It is
        used only in broker modes — the simulator has no venue to collide on,
        so paper-mode behavior below is byte-for-byte unchanged from before
        P1 (same loop, same `_execute_one`, no reservation accounting, no
        barrier registration), which is deliberate: Phase A's reproducibility
        depends on the simulator path never changing shape.
        """
        # Mature settled proceeds before anything is priced against them, so a
        # cycle never blocks a purchase on funds that settled this morning.
        self.advance_settlement(portfolio, run_date)
        if self.broker_enabled:
            return self._execute_decisions_via_broker(portfolio, decisions, prices,
                                                       run_date, barrier)
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

    # ----- broker-mode execution (P1: submit-all-then-poll-all, barrier-gated) -----

    def _execute_decisions_via_broker(
        self,
        portfolio: Portfolio,
        decisions: list[dict[str, Any]],
        prices: dict[str, float],
        run_date: str | None,
        barrier: CycleBarrier | None,
    ) -> list[ExecutionResult]:
        """Size every decision first (reservation-aware), THEN submit all of
        this book's orders concurrently and poll them all to terminal,
        instead of the simulator's submit-one-wait-confirm-then-size-the-next
        loop. Replaces the P0 broker path's per-decision `_execute_one` call
        for broker modes only; the simulator path above is untouched.
        """
        # Indexed by ORIGINAL decision position throughout, so the final
        # return preserves that order regardless of which decisions needed a
        # venue order and which resolved immediately (HOLD/SKIP/no-op).
        slots: list[ExecutionResult | None] = [None] * len(decisions)
        sized: list[tuple[int, _SizedOrder]] = []
        # Settled-cash reservation accumulator (P1 ratified constraint #2):
        # concurrent submission means no earlier order in this batch has
        # necessarily filled yet, so sizing the Nth BUY/COVER against
        # `portfolio.cash`/`settled_cash()` alone would let every one of this
        # book's own buys in the same cycle draw on the SAME unspent cash.
        # `reserved` is running-summed here (single-threaded — one book's own
        # decisions are still sized in order) and never applied twice: it is
        # subtracted from spendable at sizing time only, never touches
        # `portfolio.cash` itself, which still moves only on a confirmed fill.
        reserved = 0.0
        for idx, d in enumerate(decisions):
            try:
                outcome = self._size_for_broker(portfolio, d, prices, run_date, reserved)
            except Exception as e:
                logger.exception("Sizing failed for %s: %s", d, e)
                slots[idx] = ExecutionResult(
                    decision=d, executed=False, side="SKIP",
                    ticker=d.get("ticker", ""), shares=0, fill_price=0, notional=0,
                    error=str(e))
                continue
            if isinstance(outcome, ExecutionResult):
                slots[idx] = outcome
                continue
            sized.append((idx, outcome))
            if outcome.broker_side == "buy":
                # BUY and COVER are the cash-consuming sides; reserve both,
                # per the registered rule, even though COVER's own sizing
                # (below) is not itself cash-gated — matching pre-P1 behavior.
                reserved += outcome.shares * outcome.price

        if sized:
            for idx, result in self._submit_and_poll(portfolio, sized, run_date, barrier):
                slots[idx] = result
        elif barrier is not None:
            # Nothing to submit this cycle, but the other books still need
            # this book's (empty) registration to complete the rendezvous.
            barrier.register(portfolio.model_key, [])
        return slots

    def _size_for_broker(
        self,
        portfolio: Portfolio,
        decision: dict[str, Any],
        prices: dict[str, float],
        run_date: str | None,
        reserved: float,
    ) -> ExecutionResult | _SizedOrder:
        """Same sizing math as `_execute_one`, minus the submission call, plus
        the reservation offset on BUY's spendable-cash cap. Kept as a separate
        method (not shared with `_execute_one`) so the simulator's sizing path
        is provably untouched by the reservation accounting added here.
        """
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
            spendable = self._spendable_cash(portfolio, run_date) - reserved
            constrained_by_settlement = spendable < min(delta_notional, portfolio.cash - reserved)
            delta_notional = min(delta_notional, max(spendable, 0.0))
            if delta_notional < price:
                if constrained_by_settlement and portfolio.cash - reserved >= price:
                    logger.info(
                        "[%s] BUY %s blocked: settled cash %.2f (reserved %.2f) < price %.2f",
                        portfolio.model_key, ticker, spendable, reserved, price)
                    return ExecutionResult(
                        decision=decision, executed=False, side="SKIP",
                        ticker=ticker, shares=0, fill_price=price, notional=0,
                        order_id="EXEC_CONSTRAINT_UNSETTLED_FUNDS",
                        constraint=CONSTRAINT_UNSETTLED_FUNDS,
                        error=(f"Blocked at submission: purchase requires settled funds; "
                               f"settled {spendable:.2f} after reservations, "
                               f"unsettled {portfolio.unsettled_cash():.2f}"),
                    )
                return ExecutionResult(decision=decision, executed=False, side="SKIP",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       error="Insufficient cash for meaningful position")
            shares = int(delta_notional / price * 10000) / 10000
            if shares <= 0:
                return ExecutionResult(decision=decision, executed=False, side="SKIP",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       error="Computed share quantity rounded to zero")
            return _SizedOrder(decision=decision, action="BUY", ticker=ticker,
                               shares=shares, price=price, broker_side="buy",
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
            return _SizedOrder(decision=decision, action="SELL", ticker=ticker,
                               shares=shares, price=price, broker_side="sell")

        if action == "SHORT":
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
            shares = int(delta_notional / price * 10000) / 10000
            if shares <= 0:
                return ExecutionResult(decision=decision, executed=False, side="SKIP",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       error="Computed short quantity rounded to zero")
            return _SizedOrder(decision=decision, action="SHORT", ticker=ticker,
                               shares=shares, price=price, broker_side="sell")

        if action == "COVER":
            if ticker not in portfolio.holdings or not portfolio.holdings[ticker].is_short:
                return ExecutionResult(decision=decision, executed=False, side="SKIP",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       error="Not held short")
            h = portfolio.holdings[ticker]
            current_short = -h.market_value(price)
            target_notional = total_value * target_weight
            delta_notional = current_short - target_notional
            if delta_notional <= 0:
                return ExecutionResult(decision=decision, executed=True, side="HOLD",
                                       ticker=ticker, shares=0, fill_price=price, notional=0,
                                       order_id="ALREADY_BELOW_SHORT_TARGET")
            shares = min(int(delta_notional / price * 10000) / 10000, abs(h.shares))
            return _SizedOrder(decision=decision, action="COVER", ticker=ticker,
                               shares=shares, price=price, broker_side="buy")

        return ExecutionResult(decision=decision, executed=False, side="SKIP",
                               ticker=ticker, shares=0, fill_price=price, notional=0,
                               error=f"Unknown action: {action}")

    def _submit_and_poll(
        self,
        portfolio: Portfolio,
        sized: list[tuple[int, _SizedOrder]],
        run_date: str | None,
        barrier: CycleBarrier | None,
    ) -> list[tuple[int, ExecutionResult]]:
        """Rule 2's sequencing (sell-side ids assigned before buy-side),
        then every order runs its own (wait-if-held -> submit -> poll ->
        release-if-winner) lifecycle in its own thread, so one held order
        never blocks an unrelated, uncontended order from this same book.
        Returns (original_decision_index, result) pairs — caller places them
        back into decision order.
        """
        book = portfolio.model_key
        if barrier is not None:
            barrier.register(book, [Intent(book=book, ticker=s.ticker, action=s.action)
                                    for _, s in sized])

        # Sell-side ids before buy-side ids — deterministic and auditable,
        # independent of whichever order's HTTP call actually lands first.
        # Returned results carry their original decision index regardless;
        # only client_order_id ASSIGNMENT follows the sells-first sequence.
        n = len(sized)
        id_order = sorted(range(n),
                          key=lambda i: (1 if sized[i][1].broker_side == "buy" else 0, i))
        client_order_ids: list[str] = [""] * n
        for i in id_order:
            client_order_ids[i] = self._next_order_id(book)

        def _run(s: _SizedOrder, client_order_id: str) -> _Placement:
            if barrier is not None:
                barrier.wait_to_submit(book, s.ticker, s.action)
            try:
                placement = self._submit_one(book, s, client_order_id, barrier)
            finally:
                # Release anything held on this ticker regardless of outcome
                # (filled, cancelled, or rejected at submission) — a rejected
                # order never became open at the venue either, so a held
                # contender must not wait out the full release timeout for it.
                if barrier is not None:
                    barrier.notify_terminal(book, s.ticker, s.action)
            return placement

        placements: dict[int, _Placement] = {}
        with ThreadPoolExecutor(max_workers=max(1, n),
                                thread_name_prefix=f"order-{book}") as pool:
            futures = {pool.submit(_run, sized[i][1], client_order_ids[i]): i
                      for i in range(n)}
            for fut in as_completed(futures):
                placements[futures[fut]] = fut.result()

        return [(sized[i][0], self._apply_placement(portfolio, sized[i][1], placements[i], run_date))
                for i in range(n)]

    def _submit_one(
        self, book: str, s: _SizedOrder, client_order_id: str,
        barrier: CycleBarrier | None,
    ) -> _Placement:
        """Submit one order and resolve it to a terminal broker-confirmed
        state. Split out of `_run` so `notify_terminal` fires from a single
        `finally` regardless of which branch below returns."""
        notional = abs(s.shares * s.price)
        if notional < MIN_ORDER_NOTIONAL:
            logger.info("[%s] %s %s skipped: notional %.2f below the venue "
                        "minimum of %.2f", book, s.action, s.ticker, notional,
                        MIN_ORDER_NOTIONAL)
            return _Placement(
                constraint=CONSTRAINT_BELOW_VENUE_MINIMUM,
                error=(f"Order notional {notional:.2f} is below the venue "
                       f"minimum of {MIN_ORDER_NOTIONAL:.2f}"))
        was_gated = barrier is not None and barrier.gate_for(book, s.ticker, s.action) is not None
        try:
            order = self.broker.submit_market_order(
                ticker=s.ticker, shares=s.shares, side=s.broker_side,
                client_order_id=client_order_id)
        except BrokerAPIError as e:
            return self._classify_rejection(book, s, client_order_id, e, was_gated)
        try:
            order, hit_deadline = self.broker.await_terminal(order)
        except BrokerAPIError as e:
            return self._classify_rejection(book, s, client_order_id, e, was_gated)
        placement = _Placement(filled_qty=order.filled_qty, fill_price=order.filled_avg_price,
                               order_id=order.client_order_id, order=order)
        if hit_deadline and order.filled_qty < order.requested_qty:
            placement.constraint = CONSTRAINT_UNFILLED_AT_DEADLINE
        if not placement.moved:
            placement.error = placement.error or (
                f"No fill: terminal status {order.status!r} "
                f"(filled {order.filled_qty:g} of {order.requested_qty:g})")
        return placement

    def _classify_rejection(
        self, book: str, s: _SizedOrder, client_order_id: str,
        e: BrokerAPIError, was_gated: bool,
    ) -> _Placement:
        if e.is_wash_trade:
            if was_gated:
                # The barrier held this order and released it only once its
                # contender was terminal, and the venue still rejected it —
                # the registered ~=0-expectation case, not the routine
                # un-mediated collision.
                logger.error(
                    "[%s] %s %s REJECTED as a wash trade AFTER the P1 barrier "
                    "released it — barrier-implementation signal, not a "
                    "routine collision.", book, s.action, s.ticker)
                return _Placement(order_id=client_order_id,
                                  constraint=CONSTRAINT_WASH_REJECT_POST_BARRIER,
                                  error=f"Post-barrier wash-trade rejection: {e}")
            logger.error(
                "[%s] %s %s REJECTED as a wash trade — another book holds "
                "an open opposite-side order on this symbol. This is the "
                "un-mediated account-structure collision.", book, s.action, s.ticker)
            return _Placement(order_id=client_order_id,
                              constraint=CONSTRAINT_WASH_TRADE_BLOCK,
                              error=f"Wash-trade rejection: {e}")
        if e.is_below_minimum:
            return _Placement(order_id=client_order_id,
                              constraint=CONSTRAINT_BELOW_VENUE_MINIMUM,
                              error=str(e))
        logger.error("[%s] %s %s submission failed: %s", book, s.action, s.ticker, e)
        return _Placement(order_id=client_order_id, error=str(e))

    def _apply_placement(
        self, portfolio: Portfolio, s: _SizedOrder, placement: _Placement,
        run_date: str | None,
    ) -> ExecutionResult:
        """Mutate the book from a terminal broker placement. Same mutation
        calls as `_do_buy`/`_do_sell`/`_do_short`/`_do_cover` (kept separate
        from those methods rather than shared, since they also carry the
        simulator's non-broker branch that this path must never touch).
        """
        if not placement.moved:
            logger.info("[%s] %s not executed: %s", s.action, s.ticker,
                        placement.error or "no fill")
            return ExecutionResult(
                decision=s.decision, executed=False, side="SKIP", ticker=s.ticker,
                shares=0, fill_price=0, notional=0, order_id=placement.order_id,
                error=placement.error, constraint=placement.constraint,
                broker_order=placement.order.to_dict() if placement.order else None,
            )

        shares, price = placement.filled_qty, placement.fill_price
        constraint = s.constraint or placement.constraint
        broker_order = placement.order.to_dict() if placement.order else None

        if s.action == "BUY":
            portfolio.buy(s.ticker, shares, price)
            if constraint == CONSTRAINT_UNSETTLED_FUNDS_CAPPED:
                logger.info("[%s] BUY %s capped at the settled balance (%.4f shares filled)",
                            portfolio.model_key, s.ticker, shares)
        elif s.action == "SELL":
            proceeds = portfolio.sell(s.ticker, shares, price)
            self._record_proceeds(portfolio, proceeds, run_date)
            swept = portfolio.sweep_ghost_positions()
            if swept:
                logger.info("Swept ghost positions after %s sell: %s", s.ticker, swept)
        elif s.action == "SHORT":
            proceeds = portfolio.short(s.ticker, shares, price)
            self._record_proceeds(portfolio, proceeds, run_date)
        elif s.action == "COVER":
            portfolio.cover(s.ticker, shares, price)
            swept = portfolio.sweep_ghost_positions()
            if swept:
                logger.info("Swept ghost positions after %s cover: %s", s.ticker, swept)

        return ExecutionResult(decision=s.decision, executed=True, side=s.action,
                               ticker=s.ticker, shares=shares, fill_price=price,
                               notional=shares * price, order_id=placement.order_id,
                               constraint=constraint, broker_order=broker_order)

    def force_liquidate(
        self,
        portfolio: Portfolio,
        tickers: list[str],
        prices: dict[str, float],
        reason: str = "RISK_STOP",
        run_date: str | None = None,
    ) -> list[ExecutionResult]:
        """Force-close specified tickers (used by risk stops).

        Never gated by the settled-funds constraint. A stop closes a position;
        it does not purchase, so settlement cannot block it — which is exactly
        why the cash branch keeps stops unconditional and is the registered
        Phase B venue shape. Proceeds are booked unsettled like any other sale.

        In broker modes the stop now places a real order and waits for a
        terminal broker-confirmed state before the book moves. Before this it
        mutated the book and contacted no venue at all, so in live mode the
        book would have flattened while the position stayed open at the broker
        — a risk control that reported success without closing anything.

        A stop that cannot be placed leaves the book untouched and returns
        `stop_unprotected=True`. That is the correct outcome (the book still
        reflects reality) and the most serious event this system can produce:
        the position is open and its safety did not fire.
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
            d = {
                "action": side,
                "ticker": ticker,
                "target_weight": 0.0,
                "confidence": 10,
                "reasoning": f"Forced liquidation: {reason}",
            }
            try:
                if self.broker_enabled:
                    results.append(self._force_close_via_venue(
                        portfolio, ticker, shares, price, side, d, reason, run_date))
                    continue
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
                logger.exception("[%s] stop on %s failed: %s",
                                 portfolio.model_key, ticker, e)
                results.append(ExecutionResult(
                    decision=d, executed=False, side="SKIP", ticker=ticker,
                    shares=0, fill_price=0, notional=0, error=str(e),
                    stop_unprotected=True,
                ))
        return results

    def _force_close_via_venue(
        self, portfolio: Portfolio, ticker: str, shares: float, price: float,
        side: str, decision: dict[str, Any], reason: str,
        run_date: str | None,
    ) -> ExecutionResult:
        """Place a stop at the venue and move the book only on a confirmed fill."""
        broker_side = "buy" if side == "COVER" else "sell"
        placement = self._place(portfolio.model_key, ticker, shares,
                                broker_side, price)
        if not placement.moved:
            logger.critical(
                "[%s] STOP UNPROTECTED — %s %s (%s) did not execute at the "
                "venue: %s. The position remains OPEN and its stop did not "
                "fire. Book left unchanged (correctly): it reflects reality.",
                portfolio.model_key, side, ticker, reason,
                placement.error or "no fill")
            return ExecutionResult(
                decision=decision, executed=False, side="SKIP", ticker=ticker,
                shares=0, fill_price=0, notional=0,
                order_id=placement.order_id, error=placement.error,
                constraint=placement.constraint, stop_unprotected=True,
                broker_order=placement.order.to_dict() if placement.order else None,
            )

        filled, fill_price = placement.filled_qty, placement.fill_price
        if side == "COVER":
            portfolio.cover(ticker, filled, fill_price)
        else:
            self._record_proceeds(
                portfolio, portfolio.sell(ticker, filled, fill_price), run_date)
        portfolio.sweep_ghost_positions()

        # A partial stop fill leaves residual exposure. The book is accurate,
        # but the risk control only partly fired, so it is surfaced with the
        # same flag rather than reported as a clean stop.
        partial = filled < shares - 1e-9
        if partial:
            logger.critical(
                "[%s] STOP PARTIALLY FILLED — %s %s closed %g of %g shares. "
                "Residual exposure remains open.",
                portfolio.model_key, side, ticker, filled, shares)
        return ExecutionResult(
            decision=decision, executed=True, side=side, ticker=ticker,
            shares=filled, fill_price=fill_price, notional=filled * fill_price,
            order_id=placement.order_id, constraint=placement.constraint,
            stop_unprotected=partial,
            broker_order=placement.order.to_dict() if placement.order else None,
        )

    def liquidate_all(
        self,
        portfolio: Portfolio,
        prices: dict[str, float],
        reason: str = "PORTFOLIO_STOP",
        run_date: str | None = None,
    ) -> list[ExecutionResult]:
        """Flatten the whole book through the venue (portfolio-level stop).

        Replaces the direct `Portfolio.liquidate_all` call the pipeline used at
        the 30% drawdown halt, which had the same defect as the position stop:
        it mutated the book and placed no orders, so in a broker mode every
        position would have stayed open at the venue behind a book showing all
        cash. Routing through `force_liquidate` gives the halt the same
        confirmed-fill discipline and the same unprotected-stop reporting.
        """
        return self.force_liquidate(
            portfolio, list(portfolio.holdings.keys()), prices, reason, run_date)

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

    def _rejected(self, decision: dict[str, Any], ticker: str, side: str,
                  placement: _Placement) -> ExecutionResult:
        """A venue order that moved nothing. The book is not touched."""
        logger.info("[%s] %s not executed: %s", side, ticker,
                    placement.error or "no fill")
        return ExecutionResult(
            decision=decision, executed=False, side="SKIP", ticker=ticker,
            shares=0, fill_price=0, notional=0, order_id=placement.order_id,
            error=placement.error, constraint=placement.constraint,
            broker_order=placement.order.to_dict() if placement.order else None,
        )

    def _do_buy(self, portfolio: Portfolio, ticker: str, shares: float,
                price: float, decision: dict[str, Any],
                constraint: str = "") -> ExecutionResult:
        broker_order = None
        if self.broker_enabled:
            placement = self._place(portfolio.model_key, ticker, shares, "buy", price)
            if not placement.moved:
                return self._rejected(decision, ticker, "BUY", placement)
            # Fill facts replace intent: a partial fill buys less than asked,
            # and the fill price is the venue's, not the pre-trade mark.
            shares, price = placement.filled_qty, placement.fill_price
            order_id = placement.order_id
            constraint = constraint or placement.constraint
            broker_order = placement.order.to_dict() if placement.order else None
        else:
            order_id = f"PAPER_BUY_{ticker}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        portfolio.buy(ticker, shares, price)
        if constraint == CONSTRAINT_UNSETTLED_FUNDS_CAPPED:
            logger.info("[%s] BUY %s capped at the settled balance (%.4f shares filled)",
                        portfolio.model_key, ticker, shares)
        return ExecutionResult(decision=decision, executed=True, side="BUY",
                               ticker=ticker, shares=shares, fill_price=price,
                               notional=shares * price, order_id=order_id,
                               constraint=constraint, broker_order=broker_order)

    def _do_sell(self, portfolio: Portfolio, ticker: str, shares: float,
                 price: float, decision: dict[str, Any],
                 run_date: str | None = None) -> ExecutionResult:
        broker_order = None
        constraint = ""
        if self.broker_enabled:
            placement = self._place(portfolio.model_key, ticker, shares, "sell", price)
            if not placement.moved:
                return self._rejected(decision, ticker, "SELL", placement)
            shares, price = placement.filled_qty, placement.fill_price
            order_id = placement.order_id
            constraint = placement.constraint
            broker_order = placement.order.to_dict() if placement.order else None
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
                               notional=shares * price, order_id=order_id,
                               constraint=constraint, broker_order=broker_order)

    def _do_short(self, portfolio: Portfolio, ticker: str, shares: float,
                  price: float, decision: dict[str, Any],
                  run_date: str | None = None) -> ExecutionResult:
        # Opening/adding a short submits a SELL order on the broker side.
        broker_order = None
        constraint = ""
        if self.broker_enabled:
            placement = self._place(portfolio.model_key, ticker, shares, "sell", price)
            if not placement.moved:
                return self._rejected(decision, ticker, "SHORT", placement)
            shares, price = placement.filled_qty, placement.fill_price
            order_id = placement.order_id
            constraint = placement.constraint
            broker_order = placement.order.to_dict() if placement.order else None
        else:
            order_id = f"PAPER_SHORT_{ticker}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        proceeds = portfolio.short(ticker, shares, price)
        # A short sale is a sale: its proceeds are unsettled like any other.
        # Unreachable on the cash branch (a cash account cannot short), but
        # correct if the constraint is ever active alongside shorting.
        self._record_proceeds(portfolio, proceeds, run_date)
        return ExecutionResult(decision=decision, executed=True, side="SHORT",
                               ticker=ticker, shares=shares, fill_price=price,
                               notional=shares * price, order_id=order_id,
                               constraint=constraint, broker_order=broker_order)

    def _do_cover(self, portfolio: Portfolio, ticker: str, shares: float,
                  price: float, decision: dict[str, Any]) -> ExecutionResult:
        # Covering a short submits a BUY order on the broker side.
        broker_order = None
        constraint = ""
        if self.broker_enabled:
            placement = self._place(portfolio.model_key, ticker, shares, "buy", price)
            if not placement.moved:
                return self._rejected(decision, ticker, "COVER", placement)
            shares, price = placement.filled_qty, placement.fill_price
            order_id = placement.order_id
            constraint = placement.constraint
            broker_order = placement.order.to_dict() if placement.order else None
        else:
            order_id = f"PAPER_COVER_{ticker}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        portfolio.cover(ticker, shares, price)
        swept = portfolio.sweep_ghost_positions()
        if swept:
            logger.info("Swept ghost positions after %s cover: %s", ticker, swept)
        return ExecutionResult(decision=decision, executed=True, side="COVER",
                               ticker=ticker, shares=shares, fill_price=price,
                               notional=shares * price, order_id=order_id,
                               constraint=constraint, broker_order=broker_order)
