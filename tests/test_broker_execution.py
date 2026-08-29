"""P0 — broker execution path: stops reach the venue, and only confirmed fills
move the book.

Two defects motivate this file, both live in `main` before this package:

  1. `force_liquidate` and `Portfolio.liquidate_all` mutated the book and
     placed no order at all. In a broker mode the book would flatten while the
     position stayed open at the venue — a risk control that reported success
     without closing anything. The unconditional stop is the entire registered
     justification for the cash branch, so this is the one that matters most.
  2. The book moved at intent rather than at fill, so a rejected order still
     changed the portfolio, and the fill price silently fell back to the
     pre-trade simulated mark (the venue returns `filled_avg_price: null`
     before anything fills — verified against the paper endpoint 2026-08-29).

Every venue fact asserted here was measured in that spike, not assumed.

No network: a fake broker stands in for the venue and records what it was asked
to do, which is exactly what these tests are about.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.execution import executor as ex_mod
from src.execution.broker import (
    BrokerAPIError,
    BrokerOrder,
    build_order_id,
    book_from_order_id,
    from_venue_symbol,
    to_venue_symbol,
    truncate_qty,
)
from src.execution.executor import (
    CONSTRAINT_BELOW_VENUE_MINIMUM,
    CONSTRAINT_UNFILLED_AT_DEADLINE,
    CONSTRAINT_WASH_TRADE_BLOCK,
    Executor,
)
from src.portfolio.portfolio import Holding, Portfolio


# --------------------------------------------------------------------------
# Fake venue
# --------------------------------------------------------------------------
class FakeBroker:
    """Stands in for BrokerClient. Records submissions; returns scripted fills."""

    base_url = "https://paper-api.alpaca.markets"
    fill_deadline_seconds = 300

    def __init__(self, *, fill_ratio=1.0, fill_price=None, raise_error=None,
                 hit_deadline=False):
        self.fill_ratio = fill_ratio
        self.fill_price = fill_price
        self.raise_error = raise_error
        self.hit_deadline = hit_deadline
        self.submissions: list[dict] = []

    def place_and_confirm(self, *, ticker, shares, side, client_order_id,
                          deadline_seconds=None):
        self.submissions.append({"ticker": ticker, "shares": shares,
                                 "side": side, "client_order_id": client_order_id})
        if self.raise_error is not None:
            raise self.raise_error
        filled = shares * self.fill_ratio
        price = self.fill_price if self.fill_price is not None else 100.0
        order = BrokerOrder(
            client_order_id=client_order_id, broker_order_id="brk-1",
            symbol=to_venue_symbol(ticker), ticker=ticker, side=side,
            requested_qty=shares,
            status="filled" if self.fill_ratio >= 1.0 else "canceled",
            filled_qty=filled, filled_avg_price=price if filled else 0.0,
        )
        return order, self.hit_deadline


def _executor(broker, mode="broker_paper"):
    """An Executor wired to a fake venue without touching config or network."""
    e = Executor.__new__(Executor)
    e.settings = {"mode": mode, "settlement": {"enforce_settled_funds": False}}
    e.mode = mode
    e.broker = broker
    e.cycle_id = "20261006T1430Z"
    e._order_seq = {}
    return e


def _book(cash=10_000.0, holdings=None):
    return Portfolio(model_key="grok", cash=cash, holdings=holdings or {},
                     inception_value=10_000.0, inception_date="2026-04-09")


def _long(ticker="AAPL", shares=10.0, cost=100.0):
    return {ticker: Holding(ticker=ticker, shares=shares, avg_cost=cost)}


def _short(ticker="AAPL", shares=10.0, cost=100.0):
    return {ticker: Holding(ticker=ticker, shares=-shares, avg_cost=cost)}


# --------------------------------------------------------------------------
# Venue facts (spike-verified)
# --------------------------------------------------------------------------
def test_symbol_mapping_covers_the_one_universe_name_that_differs():
    # BRK-B does not exist at the venue; it is spelled BRK.B. 78 of the 79
    # universe names match exactly, so this single mapping is the whole gap.
    assert to_venue_symbol("BRK-B") == "BRK.B"
    assert from_venue_symbol("BRK.B") == "BRK-B"
    assert to_venue_symbol("AAPL") == "AAPL"
    assert from_venue_symbol("AAPL") == "AAPL"


def test_every_universe_symbol_round_trips_through_the_mapping():
    import json
    universe = json.load(open(Path(__file__).resolve().parent.parent
                              / "config" / "universe.json", encoding="utf-8"))
    for t in universe["tickers"]:
        sym = t["symbol"]
        assert from_venue_symbol(to_venue_symbol(sym)) == sym, sym


def test_quantity_truncates_to_the_venue_precision_never_rounds_up():
    # The venue silently truncates past 9 dp; we truncate first so our record
    # and theirs agree. Rounding up could push notional past the checked cash.
    assert truncate_qty(0.1234567891) == 0.123456789
    assert truncate_qty(0.9999999999) == 0.999999999
    assert truncate_qty(2.0) == 2.0


def test_order_id_carries_the_book_tag_and_an_attempt_suffix():
    oid = build_order_id("grok", "20261006T1430Z", 3)
    assert oid == "grok-20261006T1430Z-03-a0"
    assert book_from_order_id(oid) == "grok"
    # client_order_id uniqueness is permanent and survives cancellation, so a
    # retry must carry a different id; the attempt suffix is what provides it.
    assert build_order_id("grok", "20261006T1430Z", 3, attempt=1) != oid


def test_over_long_order_id_raises_rather_than_truncating_the_book_tag():
    # Truncation would silently destroy book attribution, which is the join key
    # for every per-book metric.
    with pytest.raises(ValueError, match="exceeds the venue limit"):
        build_order_id("grok", "C" * 200, 1)


# --------------------------------------------------------------------------
# The book moves only on confirmed fills
# --------------------------------------------------------------------------
def test_rejected_buy_does_not_move_the_book():
    broker = FakeBroker(raise_error=BrokerAPIError("nope", status=422, code=42210000))
    e = _executor(broker)
    p = _book(cash=10_000.0)
    r = e._do_buy(p, "AAPL", 10.0, 100.0, {"action": "BUY", "ticker": "AAPL"})
    assert r.executed is False
    assert p.cash == 10_000.0          # untouched
    assert "AAPL" not in p.holdings


def test_zero_fill_does_not_move_the_book():
    broker = FakeBroker(fill_ratio=0.0)
    e = _executor(broker)
    p = _book(cash=10_000.0)
    r = e._do_buy(p, "AAPL", 10.0, 100.0, {"action": "BUY", "ticker": "AAPL"})
    assert r.executed is False
    assert p.cash == 10_000.0
    assert "AAPL" not in p.holdings


def test_partial_fill_moves_the_book_by_the_filled_quantity_only():
    broker = FakeBroker(fill_ratio=0.4, fill_price=100.0)
    e = _executor(broker)
    p = _book(cash=10_000.0)
    r = e._do_buy(p, "AAPL", 10.0, 100.0, {"action": "BUY", "ticker": "AAPL"})
    assert r.executed is True
    assert r.shares == pytest.approx(4.0)
    assert p.holdings["AAPL"].shares == pytest.approx(4.0)
    assert p.cash == pytest.approx(9_600.0)


def test_book_uses_the_venue_fill_price_not_the_pre_trade_mark():
    # The exact defect: filled_avg_price is null pre-fill, so the old code fell
    # back to the simulated price and recorded a fill that never happened at
    # that price.
    broker = FakeBroker(fill_price=103.25)
    e = _executor(broker)
    p = _book(cash=10_000.0)
    r = e._do_buy(p, "AAPL", 10.0, 100.0, {"action": "BUY", "ticker": "AAPL"})
    assert r.fill_price == 103.25
    assert p.holdings["AAPL"].avg_cost == 103.25
    assert p.cash == pytest.approx(10_000.0 - 1_032.50)


def test_sub_dollar_order_is_blocked_before_submission():
    broker = FakeBroker()
    e = _executor(broker)
    p = _book(cash=10_000.0)
    r = e._do_buy(p, "AAPL", 0.001, 100.0, {"action": "BUY", "ticker": "AAPL"})
    assert r.executed is False
    assert r.constraint == CONSTRAINT_BELOW_VENUE_MINIMUM
    assert broker.submissions == []       # never reached the venue
    assert p.cash == 10_000.0


def test_unfilled_at_deadline_is_flagged_on_a_partial_fill():
    broker = FakeBroker(fill_ratio=0.5, hit_deadline=True)
    e = _executor(broker)
    p = _book(cash=10_000.0)
    r = e._do_buy(p, "AAPL", 10.0, 100.0, {"action": "BUY", "ticker": "AAPL"})
    assert r.executed is True
    assert r.constraint == CONSTRAINT_UNFILLED_AT_DEADLINE
    assert r.shares == pytest.approx(5.0)


def test_wash_trade_rejection_is_classified_distinctly():
    # Not filed with ordinary rejections: it is an artifact of six books
    # sharing one account, and its Gate 4 class is an open question.
    err = BrokerAPIError("potential wash trade detected. use complex orders",
                         status=403, code=40310000)
    assert err.is_wash_trade is True
    e = _executor(FakeBroker(raise_error=err))
    p = _book(cash=10_000.0, holdings=_long())
    r = e._do_sell(p, "AAPL", 5.0, 100.0, {"action": "SELL", "ticker": "AAPL"})
    assert r.executed is False
    assert r.constraint == CONSTRAINT_WASH_TRADE_BLOCK
    assert p.holdings["AAPL"].shares == 10.0     # book untouched


def test_orders_carry_the_book_tag_and_increment_per_book():
    broker = FakeBroker()
    e = _executor(broker)
    p = _book(cash=10_000.0)
    e._do_buy(p, "AAPL", 10.0, 100.0, {"action": "BUY", "ticker": "AAPL"})
    e._do_buy(p, "MSFT", 10.0, 100.0, {"action": "BUY", "ticker": "MSFT"})
    ids = [s["client_order_id"] for s in broker.submissions]
    assert ids == ["grok-20261006T1430Z-00-a0", "grok-20261006T1430Z-01-a0"]
    assert all(book_from_order_id(i) == "grok" for i in ids)


# --------------------------------------------------------------------------
# Stops reach the venue — the P0 headline
# --------------------------------------------------------------------------
def test_position_stop_places_a_real_sell_order():
    broker = FakeBroker(fill_price=85.0)
    e = _executor(broker)
    p = _book(cash=0.0, holdings=_long(shares=10.0, cost=100.0))
    results = e.force_liquidate(p, ["AAPL"], {"AAPL": 85.0}, "POSITION_STOP")
    assert len(broker.submissions) == 1
    assert broker.submissions[0]["side"] == "sell"
    assert broker.submissions[0]["shares"] == 10.0
    assert results[0].executed is True
    assert "AAPL" not in p.holdings
    assert p.cash == pytest.approx(850.0)


def test_stop_on_a_short_places_a_buy_order_to_cover():
    broker = FakeBroker(fill_price=110.0)
    e = _executor(broker)
    p = _book(cash=5_000.0, holdings=_short(shares=10.0, cost=100.0))
    e.force_liquidate(p, ["AAPL"], {"AAPL": 110.0}, "POSITION_STOP")
    assert broker.submissions[0]["side"] == "buy"
    assert "AAPL" not in p.holdings


def test_a_stop_that_cannot_be_placed_leaves_the_book_UNCHANGED_and_screams():
    # The critical regression. The old code flattened the book here while the
    # venue position stayed open, so the book claimed protection it did not
    # have. The position must remain visible, and the result must say so.
    broker = FakeBroker(raise_error=BrokerAPIError("venue down", status=500))
    e = _executor(broker)
    p = _book(cash=0.0, holdings=_long(shares=10.0, cost=100.0))
    results = e.force_liquidate(p, ["AAPL"], {"AAPL": 85.0}, "POSITION_STOP")
    assert results[0].executed is False
    assert results[0].stop_unprotected is True
    assert p.holdings["AAPL"].shares == 10.0     # STILL OPEN, and shown as such
    assert p.cash == 0.0


def test_a_stop_blocked_by_another_books_order_is_reported_unprotected():
    # The wash-trade collision reaching the stop path is the worst case the
    # spike surfaced: one book's ordinary buy can block another book's stop.
    err = BrokerAPIError("potential wash trade detected. use complex orders",
                         status=403, code=40310000)
    e = _executor(FakeBroker(raise_error=err))
    p = _book(cash=0.0, holdings=_long(shares=10.0, cost=100.0))
    results = e.force_liquidate(p, ["AAPL"], {"AAPL": 85.0}, "POSITION_STOP")
    assert results[0].stop_unprotected is True
    assert results[0].constraint == CONSTRAINT_WASH_TRADE_BLOCK
    assert p.holdings["AAPL"].shares == 10.0


def test_partially_filled_stop_is_reported_unprotected():
    broker = FakeBroker(fill_ratio=0.3, fill_price=85.0)
    e = _executor(broker)
    p = _book(cash=0.0, holdings=_long(shares=10.0, cost=100.0))
    results = e.force_liquidate(p, ["AAPL"], {"AAPL": 85.0}, "POSITION_STOP")
    assert results[0].executed is True
    assert results[0].stop_unprotected is True   # residual exposure remains
    assert p.holdings["AAPL"].shares == pytest.approx(7.0)


def test_portfolio_halt_liquidates_through_the_venue():
    # The portfolio-level stop had the same defect as the position stop: the
    # pipeline called Portfolio.liquidate_all, which places no orders.
    broker = FakeBroker(fill_price=90.0)
    e = _executor(broker)
    holdings = {**_long("AAPL", 10.0, 100.0), **_long("MSFT", 5.0, 200.0)}
    p = _book(cash=0.0, holdings=holdings)
    results = e.liquidate_all(p, {"AAPL": 90.0, "MSFT": 90.0})
    assert len(broker.submissions) == 2
    assert {s["ticker"] for s in broker.submissions} == {"AAPL", "MSFT"}
    assert p.holdings == {}
    assert all(r.executed for r in results)


# --------------------------------------------------------------------------
# The simulator path is untouched
# --------------------------------------------------------------------------
def test_simulator_mode_places_no_orders_and_behaves_as_before():
    e = _executor(None, mode="paper")
    assert e.broker_enabled is False
    p = _book(cash=10_000.0)
    r = e._do_buy(p, "AAPL", 10.0, 100.0, {"action": "BUY", "ticker": "AAPL"})
    assert r.executed is True
    assert r.order_id.startswith("PAPER_BUY_AAPL_")
    assert p.holdings["AAPL"].shares == 10.0
    assert p.cash == pytest.approx(9_000.0)


def test_simulator_stop_still_flattens_without_a_venue():
    e = _executor(None, mode="paper")
    p = _book(cash=0.0, holdings=_long(shares=10.0, cost=100.0))
    results = e.force_liquidate(p, ["AAPL"], {"AAPL": 85.0}, "POSITION_STOP")
    assert results[0].executed is True
    assert results[0].order_id == "FORCED_POSITION_STOP"
    assert "AAPL" not in p.holdings


def test_the_book_only_liquidate_is_blocked_in_broker_modes(monkeypatch):
    # The defect must not be reintroducible: Portfolio.liquidate_all reaches no
    # venue, so a future caller using it as a halt would recreate exactly the
    # failure P0 removed.
    from src.portfolio import portfolio as pf_mod
    p = _book(cash=0.0, holdings=_long(shares=10.0, cost=100.0))
    monkeypatch.setattr(pf_mod, "load_settings", lambda: {"mode": "broker_paper"})
    with pytest.raises(RuntimeError, match="places no orders"):
        p.liquidate_all({"AAPL": 85.0})
    assert p.holdings["AAPL"].shares == 10.0

    monkeypatch.setattr(pf_mod, "load_settings", lambda: {"mode": "paper"})
    p.liquidate_all({"AAPL": 85.0})          # simulator path still works
    assert p.holdings == {}
