"""P1 — the cross-book submission barrier (src/execution/barrier.py).

Registered mechanism: docs/prereg/tier2_novel_sections.md, "Cross-book
submission-order parity" and "Cross-book submission barrier" (2026-09-15
landing). Three rules under test: scope (only a ticker with both a buy-side
and a sell-side intent engages the barrier), ordering (deterministic
rotation, sell-side before buy-side), and release (held orders wait for the
winner's terminal state, not a hang).
"""
from __future__ import annotations

import datetime as dt
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.execution.barrier import (
    ROTATION_EPOCH,
    CycleBarrier,
    Intent,
    rotation_tick,
    submission_order,
)


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------
def test_rotation_tick_is_zero_at_the_epoch():
    assert rotation_tick(ROTATION_EPOCH) == 0


def test_rotation_tick_increments_once_per_thirty_minutes():
    assert rotation_tick(ROTATION_EPOCH + dt.timedelta(minutes=30)) == 1
    assert rotation_tick(ROTATION_EPOCH + dt.timedelta(minutes=59)) == 1
    assert rotation_tick(ROTATION_EPOCH + dt.timedelta(minutes=60)) == 2


def test_rotation_tick_is_reproducible_from_the_timestamp_alone():
    at = ROTATION_EPOCH + dt.timedelta(days=37, minutes=90)
    assert rotation_tick(at) == rotation_tick(at)


def test_submission_order_visits_each_book_once_per_full_rotation():
    books = ["claude", "claude_opus", "deepseek", "gemini", "gpt", "grok"]
    firsts = [submission_order(books, t)[0] for t in range(len(books))]
    assert sorted(firsts) == sorted(books), "every book must lead exactly once per 6-tick window"


def test_submission_order_is_a_pure_rotation_not_a_reorder():
    books = ["a", "b", "c", "d"]
    assert submission_order(books, 0) == ["a", "b", "c", "d"]
    assert submission_order(books, 1) == ["b", "c", "d", "a"]
    assert submission_order(books, 4) == ["a", "b", "c", "d"]  # wraps at n


def test_submission_order_empty_books_is_empty():
    assert submission_order([], 5) == []


# ---------------------------------------------------------------------------
# CycleBarrier — plan construction, exercised the way production does: each
# book's `register()` runs in its own thread and blocks for the rendezvous.
# ---------------------------------------------------------------------------
def _register_all(barrier: CycleBarrier, per_book_intents: dict[str, list[Intent]]) -> None:
    threads = [threading.Thread(target=barrier.register, args=(book, intents))
              for book, intents in per_book_intents.items()]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=5.0)
        assert not th.is_alive(), "register() must not hang once every book has registered"


def test_uncontended_ticker_is_never_gated():
    b = CycleBarrier(expected_books={"alpha", "beta"}, t=0,
                     registration_timeout=5.0, release_timeout=2.0)
    _register_all(b, {
        "alpha": [Intent("alpha", "AAPL", "BUY")],
        "beta": [Intent("beta", "MSFT", "SELL")],
    })
    assert b.gate_for("alpha", "AAPL", "BUY") is None
    assert b.gate_for("beta", "MSFT", "SELL") is None


def test_contended_ticker_sell_side_wins_regardless_of_rotation():
    # t=0 rotates nothing (alpha, beta unchanged) -- pick a t where alpha
    # would lead the rotation, and confirm the SELL still wins anyway.
    b = CycleBarrier(expected_books={"alpha", "beta"}, t=0,
                     registration_timeout=5.0, release_timeout=2.0)
    _register_all(b, {
        "alpha": [Intent("alpha", "PFE", "BUY")],
        "beta": [Intent("beta", "PFE", "SELL")],
    })
    assert b.gate_for("beta", "PFE", "SELL") is None, "the sell-side winner is never gated"
    assert b.gate_for("alpha", "PFE", "BUY") is not None, "the buy-side loses to any sell-side"


def test_contended_ticker_same_side_class_breaks_by_rotation():
    b = CycleBarrier(expected_books={"alpha", "beta", "gamma"}, t=1,
                     registration_timeout=5.0, release_timeout=2.0)
    # base sorted order = [alpha, beta, gamma]; t=1 -> rotated = [beta, gamma, alpha]
    _register_all(b, {
        "alpha": [Intent("alpha", "XYZ", "SELL")],
        "beta": [Intent("beta", "XYZ", "SELL")],
        "gamma": [Intent("gamma", "XYZ", "BUY")],
    })
    assert b.gate_for("beta", "XYZ", "SELL") is None, "beta leads the rotated order among sellers"
    assert b.gate_for("alpha", "XYZ", "SELL") is not None
    assert b.gate_for("gamma", "XYZ", "BUY") is not None


def test_registration_proceeds_without_a_missing_book_after_timeout():
    b = CycleBarrier(expected_books={"alpha", "beta"}, t=0,
                     registration_timeout=0.2, release_timeout=1.0)
    start = time.monotonic()
    b.register("alpha", [Intent("alpha", "AAPL", "BUY")])  # beta never registers
    assert time.monotonic() - start < 2.0, "must return after the timeout, not hang"
    assert b.gate_for("alpha", "AAPL", "BUY") is None  # uncontended without beta


def test_a_held_order_that_never_releases_times_out_rather_than_hangs():
    b = CycleBarrier(expected_books={"alpha", "beta"}, t=0,
                     registration_timeout=5.0, release_timeout=0.2)
    _register_all(b, {
        "alpha": [Intent("alpha", "PFE", "BUY")],
        "beta": [Intent("beta", "PFE", "SELL")],
    })
    start = time.monotonic()
    b.wait_to_submit("alpha", "PFE", "BUY")  # beta never calls notify_terminal
    assert time.monotonic() - start < 1.0, "must time out, not hang forever"


# ---------------------------------------------------------------------------
# CycleBarrier — real concurrency: hold, then release on terminal
# ---------------------------------------------------------------------------
def test_held_order_releases_the_instant_the_winner_is_terminal():
    b = CycleBarrier(expected_books={"alpha", "beta"}, t=0,
                     registration_timeout=5.0, release_timeout=2.0)
    released_order = []

    def loser_thread():
        b.register("alpha", [Intent("alpha", "PFE", "BUY")])
        b.wait_to_submit("alpha", "PFE", "BUY")
        released_order.append("alpha_submitted")

    def winner_thread():
        b.register("beta", [Intent("beta", "PFE", "SELL")])
        b.wait_to_submit("beta", "PFE", "SELL")  # never gated, returns immediately
        released_order.append("beta_submitted")
        time.sleep(0.1)  # simulate the winner's order still being in flight
        released_order.append("beta_terminal")
        b.notify_terminal("beta", "PFE", "SELL")

    t1 = threading.Thread(target=loser_thread)
    t2 = threading.Thread(target=winner_thread)
    t1.start(); t2.start()
    t1.join(timeout=5.0); t2.join(timeout=5.0)

    assert released_order.index("beta_terminal") < released_order.index("alpha_submitted"), \
        "the held order must not submit before the winner reaches terminal state"
    assert released_order.index("beta_submitted") < released_order.index("alpha_submitted")
