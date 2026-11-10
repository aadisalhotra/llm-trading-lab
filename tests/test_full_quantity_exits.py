"""Full exits send the exact holding; dust no longer forms (ghost ruling §⑦ (c)).

Governing text: docs/registrations/ghost_position_ruling.md §⑦ — a full exit
sells the exact holding at the venue's 9-dp precision, leaving <= 1e-9 share,
which fix A's threshold deletes. Partial sells and BUYs are unchanged.

Every case runs on both sizing paths (the venue path through _size_for_broker
and the simulator twin through _execute_one), because the 4-dp truncation lived
at both sites (executor.py SELL :667/:1123, COVER :705/:1162 before this change).

The venue double here applies the real broker.truncate_qty to what it is sent,
as BrokerClient.submit_market_order does, so the quantity tested is the
quantity the venue would receive.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.execution.broker import BrokerOrder, to_venue_symbol, truncate_qty  # noqa: E402
from src.execution.executor import CONSTRAINT_BELOW_VENUE_MINIMUM, Executor  # noqa: E402
from src.portfolio.portfolio import Holding, Portfolio  # noqa: E402

ACTIVE = "2026-11-10"
PATHS = ["broker", "execute_one"]
NO_POSITION = 1e-9          # fix A: the venue's quantity precision


def _old_partial(delta_notional: float, price: float, held: float) -> float:
    """The pre-§⑦ sizing, kept verbatim as the reference partial sells must match."""
    return min(int(delta_notional / price * 10000) / 10000, held)


class VenueDouble:
    """Fills everything at once, at the reference price, after truncate_qty."""

    base_url = "https://paper-api.alpaca.markets"
    fill_deadline_seconds = 300

    def __init__(self, prices):
        self.prices = prices
        self.submissions: list[dict] = []

    def submit_market_order(self, *, ticker, shares, side, client_order_id):
        qty = truncate_qty(shares)
        self.submissions.append({"ticker": ticker, "shares": shares, "qty": qty, "side": side})
        return BrokerOrder(
            client_order_id=client_order_id, broker_order_id=f"brk-{client_order_id}",
            symbol=to_venue_symbol(ticker), ticker=ticker, side=side,
            requested_qty=qty, status="pending_new", filled_qty=0.0, filled_avg_price=0.0)

    def await_terminal(self, order, deadline_seconds=None):
        return BrokerOrder(
            client_order_id=order.client_order_id, broker_order_id=order.broker_order_id,
            symbol=order.symbol, ticker=order.ticker, side=order.side,
            requested_qty=order.requested_qty, status="filled",
            filled_qty=order.requested_qty, filled_avg_price=self.prices[order.ticker]), False


def _executor(path, prices, book="grok"):
    ex = Executor.__new__(Executor)
    mode = "broker_paper" if path == "broker" else "paper"
    ex.settings = {"mode": mode, "settlement": {
        "enforce_settled_funds": True, "activation_date": "2026-10-23", "settlement_days": 1}}
    ex.mode = mode
    ex.broker = VenueDouble(prices) if path == "broker" else None
    ex.venue_books = frozenset({book}) if path == "broker" else frozenset()
    ex.cycle_id = "20261110T143000Z"
    ex._order_seq = {}
    return ex


def _book(holdings, cash=10.0, key="grok"):
    return Portfolio(model_key=key, cash=cash, holdings=holdings,
                     inception_value=2000.0, inception_date="2026-11-06")


def _run(path, decision, prices, book):
    ex = _executor(path, prices, book.model_key)
    if path == "broker":
        return ex.execute_decisions(book, [decision], prices, run_date=ACTIVE)[0], ex
    direct = ex._execute_one(book, decision, prices, ACTIVE)
    ex._finalize(book.model_key, [direct], [None])
    return direct, ex


def _sell(ticker, weight=0.0):
    return {"action": "SELL", "ticker": ticker, "target_weight": weight, "confidence": 6}


def _left(book, ticker):
    h = book.holdings.get(ticker)
    return abs(h.shares) if h else 0.0


# --------------------------------------------------------------------------- #
# The realized case: Grok's NVDA exit of 2026-11-09
# --------------------------------------------------------------------------- #
NVDA_HELD, NVDA_PX = 1.6081, 173.44000244140625


def test_the_old_sizing_left_the_nvda_remainder():
    """The defect, reproduced: 4-dp truncation of a float round-trip sold 1.608."""
    assert _old_partial(NVDA_HELD * NVDA_PX, NVDA_PX, NVDA_HELD) == 1.608
    assert round(NVDA_HELD - 1.608, 9) == 0.0001


@pytest.mark.parametrize("path", PATHS)
def test_nvda_replay_sells_the_whole_position(path):
    book = _book({"NVDA": Holding("NVDA", NVDA_HELD, 187.93)})
    r, ex = _run(path, _sell("NVDA"), {"NVDA": NVDA_PX}, book)
    assert r.executed is True and r.side == "SELL"
    assert r.shares == NVDA_HELD
    assert "NVDA" not in book.holdings
    if path == "broker":
        assert ex.broker.submissions[0]["qty"] == NVDA_HELD


# --------------------------------------------------------------------------- #
# Every current holding in all six books
# --------------------------------------------------------------------------- #
def _current_holdings():
    out = []
    for f in sorted((ROOT / "data" / "state").glob("*.json")):
        state = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not isinstance(state.get("holdings"), dict):
            continue
        for ticker, h in state["holdings"].items():
            out.append((state.get("model_key") or f.stem, ticker,
                        float(h["shares"]), float(h["avg_cost"])))
    return out


CURRENT = _current_holdings()


def test_the_current_book_census_is_not_empty():
    books = {b for b, *_ in CURRENT}
    assert {"claude", "claude_opus", "gemini", "gpt", "grok", "deepseek"} <= books, books


@pytest.mark.parametrize("path", PATHS)
def test_a_full_exit_of_every_current_holding_leaves_no_remainder(path):
    """Each live holding (data/state), sold to target 0 at two marks: its cost
    and a float-hostile mark. Sub-$1 holdings are pre-existing dust and must be
    blocked as BELOW_VENUE_MINIMUM; everything else must leave <= 1e-9 share."""
    checked = dust = 0
    for book_key, ticker, shares, avg_cost in CURRENT:
        if shares <= 0:
            continue                                   # long-only cohort; shorts covered below
        for px in (avg_cost, avg_cost * 0.9213456789012345):
            book = _book({ticker: Holding(ticker, shares, avg_cost)}, key=book_key)
            r, _ = _run(path, _sell(ticker), {ticker: px}, book)
            if path == "broker" and shares * px < 1.0:
                # The venue floor applies to venue SELLs only; the simulator
                # never had it for sells (pre-existing), so there dust exits too.
                assert r.executed is False and r.constraint == CONSTRAINT_BELOW_VENUE_MINIMUM
                dust += 1
                continue
            assert r.executed is True, (book_key, ticker, r.error, r.constraint)
            assert _left(book, ticker) <= NO_POSITION, (book_key, ticker, shares, px)
            checked += 1
    assert checked > 0


@pytest.mark.parametrize("path", PATHS)
def test_a_full_cover_leaves_no_remainder(path):
    """Gemini's 2026-10-23 boundary leg: ISRG -15.0428 covered at 452.875. The
    old sizing covered 15.0427 and left a 0.0001-share short."""
    assert _old_partial(15.0428 * 452.875, 452.875, 15.0428) == 15.0427
    book = _book({"ISRG": Holding("ISRG", -15.0428, 452.0)}, cash=20000.0)
    r, _ = _run(path, {"action": "COVER", "ticker": "ISRG", "target_weight": 0.0,
                       "confidence": 6}, {"ISRG": 452.875}, book)
    assert r.executed is True, (r.error, r.constraint)
    assert r.shares == 15.0428
    assert _left(book, "ISRG") <= NO_POSITION


def test_existing_dust_is_still_blocked_below_the_venue_minimum():
    """The ruling changes how dust forms, not the venue floor: Grok's existing
    0.0001 NVDA ($0.02) stays unsellable on the venue path and keeps its
    registered tag."""
    book = _book({"NVDA": Holding("NVDA", 0.0001, 187.93)})
    r, _ = _run("broker", _sell("NVDA"), {"NVDA": 171.58}, book)
    assert r.executed is False and r.constraint == CONSTRAINT_BELOW_VENUE_MINIMUM
    assert book.holdings["NVDA"].shares == 0.0001


def test_the_simulator_full_exit_of_dust_leaves_nothing():
    """The simulator applies no venue floor to SELLs (unchanged by this work);
    a full exit there sells the exact holding, so no 1e-4 remainder survives."""
    book = _book({"NVDA": Holding("NVDA", 0.0001, 187.93)}, key="deepseek")
    r, _ = _run("execute_one", _sell("NVDA"), {"NVDA": 171.58}, book)
    assert r.executed is True and "NVDA" not in book.holdings


# --------------------------------------------------------------------------- #
# Partial sells and BUYs are unchanged
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", PATHS)
def test_partial_sells_truncate_exactly_as_before(path):
    rng = random.Random(20261110)
    cases = 0
    for held in (1.6081, 3.475299999, 0.6711, 19.904, 49.5152, 7.6445):
        for px in (NVDA_PX, 50.93, 1344.81, 66.09):
            for _ in range(4):
                target = rng.uniform(0.001, 0.9)
                cash = 50.0
                book = _book({"X": Holding("X", held, px)}, cash=cash)
                total = cash + held * px
                delta = held * px - total * target
                if delta <= 0:
                    continue
                expected = _old_partial(delta, px, held)
                if expected * px < 1.0:
                    continue                              # below the venue floor either way
                r, _ = _run(path, _sell("X", target), {"X": px}, book)
                assert r.executed is True
                assert r.shares == expected, (held, px, target)
                cases += 1
    assert cases > 20


@pytest.mark.parametrize("path", PATHS)
def test_buys_are_unchanged(path):
    book = _book({}, cash=2000.0)
    r, _ = _run(path, {"action": "BUY", "ticker": "NVDA", "target_weight": 0.08,
                       "confidence": 7}, {"NVDA": 192.76}, book)
    assert r.shares == int(160.0 / 192.76 * 10000) / 10000


# --------------------------------------------------------------------------- #
# truncate_qty is exact at 9 dp
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("x,want", [
    (1.043, 1.043), (1.6081, 1.6081), (0.1 + 0.2, 0.3), (3.475299999, 3.475299999),
    (1.0000000019, 1.000000001), (2.9999999999, 2.999999999), (1e-10, 0.0),
    (164.0832, 164.0832), (49.5152, 49.5152),
])
def test_truncate_qty_is_exact_at_nine_decimals(x, want):
    assert truncate_qty(x) == want


def test_truncate_qty_never_rounds_up():
    rng = random.Random(7)
    for _ in range(20000):
        x = rng.uniform(0, 500) / rng.choice([1, 7, 13, 101])
        q = truncate_qty(x)
        assert q <= x
        assert x - q < 1e-9 + 1e-12
