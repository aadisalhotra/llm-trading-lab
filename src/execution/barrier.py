"""Cross-book submission barrier (P1).

Registered mechanism: docs/prereg/tier2_novel_sections.md, "Cross-book
submission-order parity" and "Cross-book submission barrier" (2026-09-15
landing, corrected 2026-09-15 for the BUY/COVER vs SELL/SHORT side-grouping
inversion caught while writing this file). Resolves the account-wide
wash-trade collision (`CONSTRAINT_WASH_TRADE_BLOCK` in executor.py) by
serializing only the contended portion of a cycle's submissions rather than
submitting the whole cycle at once.

Three composition rules, implemented here exactly as registered:

  1. Scope    -- engages only for a ticker where >=1 book intends a BUY-side
                 order (BUY, COVER) and >=1 (other) book intends a SELL-side
                 order (SELL, SHORT) in the same cycle. An uncontended ticker
                 is unaffected.
  2. Ordering -- among contended intents on that ticker, sell-side ranks
                 before buy-side, and within a side class the book ranks by
                 a deterministic rotation of the participating books, offset
                 by t mod n (t = the registered ordinal tick, n = number of
                 participating books). No randomness; reproducible from
                 (books, t) alone.
  3. Release  -- every other intent on that ticker is held until the winner's
                 order reaches a terminal broker state (FILLED or CANCELLED);
                 verified to clear the venue's wash-trade block in both cases
                 (CANCELLED: 2026-08-29 spike; FILLED: 2026-09-15 RTH probe).

One `CycleBarrier` instance is shared, by reference, across every book's
thread for one pipeline cycle. It fails open, never closed: a book that
never registers (crashed before reaching execute_decisions) does not
deadlock the others past `registration_timeout`, and a held order whose
winner never reaches terminal (crashed mid-submission) does not strand past
`release_timeout` -- the caller proceeds and a resulting wash-trade
rejection reports normally as `CONSTRAINT_WASH_TRADE_BLOCK`, not a hang.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from dataclasses import dataclass

logger = logging.getLogger("llmlab.execution.barrier")

# Broker-side grouping (src/execution/executor.py: _do_buy/_do_cover submit a
# broker "buy"; _do_sell/_do_short submit a broker "sell" -- verified against
# the actual submission calls, not assumed). This is also the venue's
# wash-trade contention grouping: the constraint is on broker side, not on
# our four-verb action vocabulary.
BUY_SIDE_ACTIONS = ("BUY", "COVER")
SELL_SIDE_ACTIONS = ("SELL", "SHORT")

# Reused rather than invented: the ledger's own inception anchor
# (spy_benchmark_anchor.date, scripts/phase_a_integrity_ledger.json). The
# rotation only needs to be a deterministic, reproducible, monotonically
# increasing sequence -- what it counts from is arbitrary, so anchoring it to
# an already-registered constant is preferable to a second one.
ROTATION_EPOCH = dt.datetime(2026, 4, 9, tzinfo=dt.timezone.utc)
ROTATION_SLOT_SECONDS = 30 * 60


def rotation_tick(at: dt.datetime) -> int:
    """The registered ordinal t: whole 30-minute slots since the inception epoch."""
    if at.tzinfo is None:
        at = at.replace(tzinfo=dt.timezone.utc)
    return int((at - ROTATION_EPOCH).total_seconds() // ROTATION_SLOT_SECONDS)


def submission_order(books: list[str], t: int) -> list[str]:
    """Rule 2 (ordering): deterministic rotation of `books` by t mod len(books).

    `books` must already be in the registered base order (sorted book keys).
    """
    n = len(books)
    if n == 0:
        return []
    offset = t % n
    return books[offset:] + books[:offset]


@dataclass(frozen=True)
class Intent:
    """One book's intended order for the cycle, before submission."""
    book: str
    ticker: str
    action: str  # BUY / SELL / SHORT / COVER

    @property
    def is_buy_side(self) -> bool:
        return self.action in BUY_SIDE_ACTIONS


class CycleBarrier:
    """Coordinates one cycle's cross-book submissions. One instance per cycle."""

    def __init__(self, expected_books: set[str], t: int,
                registration_timeout: float = 120.0,
                release_timeout: float = 90.0) -> None:
        self._expected = frozenset(expected_books)
        self._t = t
        self._registration_timeout = registration_timeout
        self._release_timeout = release_timeout
        self._lock = threading.Lock()
        self._registered: dict[str, list[Intent]] = {}
        self._built = False
        self._plan_ready = threading.Event()
        self._winners: dict[str, tuple[str, str]] = {}                  # ticker -> (book, action)
        self._gates: dict[tuple[str, str, str], threading.Event] = {}   # (ticker, book, action) -> gate

    def register(self, book: str, intents: list[Intent]) -> None:
        """Register this book's intended orders and block until the cycle's
        full plan is built (every expected book registered, or timeout)."""
        with self._lock:
            self._registered[book] = list(intents)
            ready = self._expected <= self._registered.keys()
            if ready and not self._built:
                self._build_plan_locked()
        if self._plan_ready.wait(timeout=self._registration_timeout):
            return
        with self._lock:
            if not self._built:
                missing = sorted(self._expected - self._registered.keys())
                logger.warning(
                    "CycleBarrier: proceeding without %s after %.0fs "
                    "(registered so far: %s)", missing,
                    self._registration_timeout, sorted(self._registered))
                self._build_plan_locked()

    def _build_plan_locked(self) -> None:
        """Rules 1 and 2. Caller must hold `self._lock`; runs exactly once."""
        base = submission_order(sorted(self._registered), self._t)
        by_ticker: dict[str, list[Intent]] = {}
        for intents in self._registered.values():
            for i in intents:
                by_ticker.setdefault(i.ticker, []).append(i)

        for ticker, intents in by_ticker.items():
            buys = [i for i in intents if i.is_buy_side]
            sells = [i for i in intents if not i.is_buy_side]
            if not buys or not sells:
                continue  # rule 1: uncontended, unaffected

            def _rank(i: Intent) -> tuple[int, int]:
                # sell-side first (0 < 1), then rotated book order
                return (0 if not i.is_buy_side else 1, base.index(i.book))

            ordered = sorted(intents, key=_rank)
            winner = ordered[0]
            self._winners[ticker] = (winner.book, winner.action)
            # Conservative simplification: everyone else on this ticker is
            # held, including a same-side sibling that could safely submit
            # alongside the winner (same-side concurrent orders don't collide
            # at the venue). They release together the instant the winner is
            # terminal (see notify_terminal), so the cost is a few seconds of
            # latency, never a wash-trade rejection — acceptable given 3+-way
            # same-ticker contention is rare (Phase A record: max 2 contended
            # tickers per cycle) and correctness, not maximal concurrency, is
            # what rule 3 is registered for.
            held = ordered[1:]
            for later in held:
                self._gates[(ticker, later.book, later.action)] = threading.Event()
            logger.info(
                "CycleBarrier: %s contended -- %s/%s submits first, holding %s",
                ticker, winner.book, winner.action,
                [(i.book, i.action) for i in held])

        self._built = True
        self._plan_ready.set()

    def gate_for(self, book: str, ticker: str, action: str) -> threading.Event | None:
        """Whether (book, ticker, action) is a held order, and its release gate."""
        return self._gates.get((ticker, book, action))

    def wait_to_submit(self, book: str, ticker: str, action: str) -> None:
        """Blocks iff this order is the held side of a contended ticker."""
        gate = self.gate_for(book, ticker, action)
        if gate is None:
            return
        released = gate.wait(timeout=self._release_timeout)
        if released:
            # Observability seam for integration probes: proves, from the
            # log timeline alone, that the held order's submission followed
            # the winner's terminal state rather than racing it.
            logger.info("CycleBarrier: releasing %s %s %s -- contender reached "
                        "terminal state", book, action, ticker)
        else:
            logger.warning(
                "CycleBarrier: releasing %s %s %s after %.0fs without the "
                "contending order reaching terminal state -- proceeding; a "
                "resulting wash-trade rejection reports as "
                "CONSTRAINT_WASH_TRADE_BLOCK, not a hang",
                book, action, ticker, self._release_timeout)

    def notify_terminal(self, book: str, ticker: str, action: str) -> None:
        """Call once an order reaches terminal state. Releases held orders
        iff (book, ticker, action) is the ticker's registered winner."""
        if self._winners.get(ticker) != (book, action):
            return
        gates = [(held_book, held_action) for (t_ticker, held_book, held_action)
                in self._gates if t_ticker == ticker]
        if gates:
            logger.info("CycleBarrier: %s %s %s is terminal -- releasing %s",
                        book, action, ticker, gates)
        for (t_ticker, _held_book, _held_action), gate in self._gates.items():
            if t_ticker == ticker:
                gate.set()
