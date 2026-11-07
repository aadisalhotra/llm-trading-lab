"""The one-share floor is gone, and no skip can be silent again.

Governing text: docs/registrations/executor_sizing_floor_ruling.md. Sections 1, 3
and 5 are what these tests pin.

The registered cases run on BOTH sizing paths by parametrization, because the
floor lived at two sites and both day-one reports said "the floor", singular:

  * broker  -- execute_decisions on a venue book, through _size_for_broker
  * execute_one -- the simulator path, through _execute_one (DeepSeek's path)

A case that only ran on one path would pass with the other still defective.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analytics import execution_metrics as em  # noqa: E402
from src.analytics.intended_vs_expressed import (  # noqa: E402
    session_row,
    write_session_descriptor,
)
from src.execution.broker import BrokerOrder, to_venue_symbol  # noqa: E402
from src.execution.executor import (  # noqa: E402
    CONSTRAINT_BELOW_VENUE_MINIMUM,
    CONSTRAINT_UNCLASSIFIED_SKIP,
    CONSTRAINT_UNSETTLED_FUNDS,
    CONSTRAINT_UNSETTLED_FUNDS_CAPPED,
    REGISTERED_SKIP_CLASSES,
    Executor,
    alarm_unclassified_skips,
    requires_skip_alarm,
)
from src.portfolio.portfolio import Holding, Portfolio  # noqa: E402

ACTIVE = "2026-11-09"
PATHS = ["broker", "execute_one"]


class FillingBroker:
    """A venue that fills everything at once, at reference * (1 + slip)."""

    base_url = "https://paper-api.alpaca.markets"
    fill_deadline_seconds = 300

    def __init__(self, prices, slip=0.0):
        self.prices, self.slip = prices, slip
        self.submissions: list[dict] = []

    def submit_market_order(self, *, ticker, shares, side, client_order_id):
        self.submissions.append({"ticker": ticker, "shares": shares, "side": side})
        return BrokerOrder(
            client_order_id=client_order_id, broker_order_id=f"brk-{client_order_id}",
            symbol=to_venue_symbol(ticker), ticker=ticker, side=side,
            requested_qty=shares, status="pending_new", filled_qty=0.0, filled_avg_price=0.0)

    def await_terminal(self, order, deadline_seconds=None):
        px = self.prices[order.ticker] * (1 + self.slip)
        return BrokerOrder(
            client_order_id=order.client_order_id, broker_order_id=order.broker_order_id,
            symbol=order.symbol, ticker=order.ticker, side=order.side,
            requested_qty=order.requested_qty, status="filled",
            filled_qty=order.requested_qty, filled_avg_price=px), False


def _executor(path, prices, slip=0.0):
    ex = Executor.__new__(Executor)
    mode = "broker_paper" if path == "broker" else "paper"
    ex.settings = {"mode": mode, "settlement": {
        "enforce_settled_funds": True, "activation_date": "2026-10-23", "settlement_days": 1}}
    ex.mode = mode
    ex.broker = FillingBroker(prices, slip) if path == "broker" else None
    ex.venue_books = frozenset({"grok"}) if path == "broker" else frozenset()
    ex.cycle_id = "20261109T143000Z"
    ex._order_seq = {}
    return ex


def _book(cash=2000.0, holdings=None):
    return Portfolio(model_key="grok", cash=cash, holdings=holdings or {},
                     inception_value=2000.0, inception_date="2026-11-06")


def _run(path, decision, prices, book, slip=0.0):
    ex = _executor(path, prices, slip)
    if path == "broker":
        return ex.execute_decisions(book, [decision], prices, run_date=ACTIVE)[0]
    # The site itself, not only the wrapper above it.
    direct = ex._execute_one(book, decision, prices, ACTIVE)
    ex._finalize(book.model_key, [direct], [None])
    return direct


def _buy(ticker, weight):
    return {"action": "BUY", "ticker": ticker, "target_weight": weight, "confidence": 7}


# --------------------------------------------------------------------------- #
# Registered case 1: a target smaller than one share
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", PATHS)
def test_a_2000_book_buys_a_target_smaller_than_one_share(path):
    """Friday's first decision: 8% of $2,000 is $160, NVDA was $192.76."""
    prices = {"NVDA": 192.76}
    book = _book()
    r = _run(path, _buy("NVDA", 0.08), prices, book)
    assert r.executed is True and r.side == "BUY"
    assert r.constraint == ""
    assert 0 < r.shares < 1
    assert r.shares == pytest.approx(160.0 / 192.76, abs=1e-4)
    assert r.notional == pytest.approx(160.0, abs=0.02)
    assert book.holdings["NVDA"].shares == pytest.approx(r.shares)


# --------------------------------------------------------------------------- #
# Registered case 2: a name over $400, bought fractionally under the cap
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("ticker,price", [("COST", 1055.19), ("LLY", 1344.81), ("TMO", 676.39)])
def test_a_name_over_400_is_reachable_under_the_cap(path, ticker, price):
    prices = {ticker: price}
    book = _book()
    r = _run(path, _buy(ticker, 0.20), prices, book)          # the $400 cap itself
    assert r.executed is True
    assert r.shares < 1
    assert r.notional == pytest.approx(400.0, abs=0.15)
    assert r.notional <= 400.0


# --------------------------------------------------------------------------- #
# Registered case 3: the settled-funds cap makes a capped order, not a skip
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", PATHS)
def test_a_settled_funds_cap_produces_a_capped_order(path):
    """$400 wanted; $1,950 of the $2,000 cash is unsettled, $50 is deployable."""
    prices = {"MSFT": 500.0}
    book = _book()
    book.settlement.record_sale(1950.0, ACTIVE)
    r = _run(path, _buy("MSFT", 0.20), prices, book)
    assert r.executed is True
    assert r.constraint == CONSTRAINT_UNSETTLED_FUNDS_CAPPED
    assert r.notional == pytest.approx(50.0, abs=0.06)
    assert r.notional <= 50.0


@pytest.mark.parametrize("path", PATHS)
def test_cash_below_one_share_is_a_capped_order(path):
    """The ruling's own example: a book with $9.60 buys $9.60 of a $150 name.
    These are the 12 of Friday's 140, as a correct executor treats them."""
    prices = {"XOM": 150.0, "PG": 100.0}
    book = _book(cash=9.60, holdings={"PG": Holding("PG", 19.904, 100.0)})
    r = _run(path, _buy("XOM", 0.10), prices, book)
    assert r.executed is True
    assert r.constraint == CONSTRAINT_UNSETTLED_FUNDS_CAPPED
    assert r.notional == pytest.approx(9.60, abs=0.02)
    assert r.notional <= 9.60


# --------------------------------------------------------------------------- #
# Registered case 4: an unclassified skip raises the alarm
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", PATHS)
def test_an_unclassified_skip_raises_the_alarm_and_fails_gate_4(path):
    book = _book()
    r = _run(path, _buy("NOPX", 0.10), {}, book)              # no price: no registered class
    assert r.executed is False and r.side == "SKIP"
    assert r.constraint == CONSTRAINT_UNCLASSIFIED_SKIP
    assert r.decision_id == "grok-20261109T143000Z-d00"

    sent = []
    assert alarm_unclassified_skips(
        "grok", [r], lambda *a, **k: sent.append((a, k)), cycle_id="c1") == 1
    (severity, title, *_), kwargs = sent[0]
    assert severity == "CRITICAL" and "UNCLASSIFIED SKIP" in title
    assert kwargs["kind"] == "unclassified_skip"
    assert r.decision_id in sent[0][0][3]["detail"]

    assert em.classify_execution({
        "side": r.side, "order_id": r.order_id, "constraint": r.constraint,
        "error": r.error, "broker_order": r.broker_order,
    }) == "fail:unclassified_skip"


@pytest.mark.parametrize("path", PATHS)
def test_a_crash_inside_sizing_is_classified_not_dropped(path, monkeypatch):
    prices = {"AAPL": 100.0}
    ex = _executor(path, prices)
    method = "_size_for_broker" if path == "broker" else "_execute_one"

    def boom(*a, **k):
        raise RuntimeError("sizing blew up")

    monkeypatch.setattr(ex, method, boom)
    r = ex.execute_decisions(_book(), [_buy("AAPL", 0.1)], prices, run_date=ACTIVE)[0]
    assert r.side == "SKIP" and r.constraint == CONSTRAINT_UNCLASSIFIED_SKIP
    assert requires_skip_alarm(r)


def test_no_alarm_on_clean_or_registered_results():
    prices = {"AAPL": 100.0}
    r = _run("execute_one", _buy("AAPL", 0.1), prices, _book())
    sent = []
    assert alarm_unclassified_skips("grok", [r], lambda *a, **k: sent.append(a)) == 0
    assert sent == []


# --------------------------------------------------------------------------- #
# The one floor that remains is $1, and it is tagged
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", PATHS)
def test_under_one_dollar_is_below_venue_minimum(path):
    prices = {"XOM": 150.0, "PG": 100.0}
    book = _book(cash=0.60, holdings={"PG": Holding("PG", 20.0, 100.0)})
    r = _run(path, _buy("XOM", 0.10), prices, book)
    assert r.executed is False
    assert r.constraint == CONSTRAINT_BELOW_VENUE_MINIMUM
    assert r.decision_id
    assert not requires_skip_alarm(r)
    assert em.classify_execution({"side": "SKIP", "order_id": r.order_id,
                                  "constraint": r.constraint}) \
        == "ok:constraint_BELOW_VENUE_MINIMUM"


@pytest.mark.parametrize("path", PATHS)
def test_the_minimum_reads_the_truncated_order_not_the_cap(path):
    """$1.00 of a $1,370 name truncates to 0.0007 shares, $0.96: under the minimum."""
    prices = {"LLY": 1370.0, "PG": 100.0}
    book = _book(cash=1.00, holdings={"PG": Holding("PG", 20.0, 100.0)})
    r = _run(path, _buy("LLY", 0.10), prices, book)
    assert r.executed is False and r.constraint == CONSTRAINT_BELOW_VENUE_MINIMUM


@pytest.mark.parametrize("path", PATHS)
def test_a_settled_cap_with_nothing_deployable_keeps_its_settled_funds_tag(path):
    prices = {"MSFT": 500.0}
    book = _book()
    book.settlement.record_sale(2000.0, ACTIVE)
    r = _run(path, _buy("MSFT", 0.20), prices, book)
    assert r.executed is False
    assert r.constraint == CONSTRAINT_UNSETTLED_FUNDS
    assert r.order_id == "EXEC_CONSTRAINT_UNSETTLED_FUNDS"
    assert not requires_skip_alarm(r)


def test_a_reservation_caps_the_second_buy_rather_than_skipping_it():
    """Two buys against $300: the second is capped at what the first left."""
    prices = {"AAA": 250.0, "BBB": 250.0}
    book = _book(cash=300.0, holdings={"PG": Holding("PG", 17.0, 100.0)})
    ex = _executor("broker", {**prices, "PG": 100.0})
    a, b = ex.execute_decisions(book, [_buy("AAA", 0.10), _buy("BBB", 0.10)],
                                {**prices, "PG": 100.0}, run_date=ACTIVE)
    assert a.executed and a.constraint == ""
    assert b.executed and b.constraint == CONSTRAINT_UNSETTLED_FUNDS_CAPPED
    assert a.notional + b.notional <= 300.0 + 1e-9
    assert book.cash >= -1e-9


# --------------------------------------------------------------------------- #
# A confirmed fill always reaches the book
# --------------------------------------------------------------------------- #
def test_a_capped_order_filling_above_reference_books_and_does_not_raise():
    """The Monday failure this push closes. The order is sized to exact cash at
    the reference price and fills 0.1% higher. Before: portfolio.buy raised after
    the venue had filled, the book never saved, and level 2 halted every book."""
    prices = {"XOM": 150.0, "PG": 100.0}
    book = _book(cash=9.60, holdings={"PG": Holding("PG", 19.904, 100.0)})
    r = _run("broker", _buy("XOM", 0.10), prices, book, slip=0.001)
    assert r.executed is True
    assert "XOM" in book.holdings
    assert book.cash < 0                                      # the overrun is visible
    assert book.cash == pytest.approx(9.60 - r.shares * 150.15)


def test_a_book_cannot_overdraw_on_an_intent():
    book = _book(cash=10.0)
    with pytest.raises(ValueError, match="Insufficient cash"):
        book.buy("AAPL", 1.0, 100.0)


@pytest.mark.parametrize("path", PATHS)
def test_an_overdrawn_book_buys_nothing_further(path):
    prices = {"XOM": 150.0, "PG": 100.0}
    book = _book(cash=-0.25, holdings={"PG": Holding("PG", 20.0, 100.0)})
    r = _run(path, _buy("XOM", 0.10), prices, book)
    assert r.executed is False and r.constraint == CONSTRAINT_BELOW_VENUE_MINIMUM


# --------------------------------------------------------------------------- #
# The sweep, pinned: nothing skips past the classifier
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("decision,prices,holdings", [
    ({"action": "SELL", "ticker": "AAPL", "target_weight": 0.0}, {"AAPL": 100.0}, {}),
    ({"action": "COVER", "ticker": "AAPL", "target_weight": 0.0}, {"AAPL": 100.0}, {}),
    ({"action": "SHORT", "ticker": "AAPL", "target_weight": 0.1}, {"AAPL": 100.0},
     {"AAPL": Holding("AAPL", 5.0, 100.0)}),
    ({"action": "WHAT", "ticker": "AAPL", "target_weight": 0.1}, {"AAPL": 100.0}, {}),
    ({"action": "SELL", "ticker": "AAPL", "target_weight": 0.0}, {}, {}),
])
def test_every_path_without_a_class_carries_the_failure_marker(path, decision, prices, holdings):
    ex = _executor(path, prices)
    book = _book(holdings=dict(holdings))
    r = ex.execute_decisions(book, [decision], prices, run_date=ACTIVE)[0]
    assert r.side == "SKIP"
    assert r.constraint == CONSTRAINT_UNCLASSIFIED_SKIP
    assert r.decision_id and requires_skip_alarm(r)


def test_a_stop_that_errors_is_tagged_and_keeps_its_own_alarm():
    ex = _executor("execute_one", {})
    book = _book(cash=0.0, holdings={"AAPL": Holding("AAPL", 1.0, 100.0)})
    ex._record_proceeds = lambda *a: (_ for _ in ()).throw(RuntimeError("no"))
    r = ex.force_liquidate(book, ["AAPL"], {"AAPL": 90.0}, "POSITION_STOP", ACTIVE)[0]
    assert r.side == "SKIP" and r.stop_unprotected is True
    assert r.constraint == CONSTRAINT_UNCLASSIFIED_SKIP
    assert r.decision_id.endswith("-stop-AAPL")
    assert not requires_skip_alarm(r)                         # STOP UNPROTECTED owns it


def test_the_old_floor_and_its_label_are_gone_from_the_source():
    """The floor's text is absent, and both paths still go through the shared sizer."""
    src = (ROOT / "src/execution/executor.py").read_text(encoding="utf-8")
    assert "meaningful position" not in src
    assert "delta_notional < price" not in src
    tree = ast.parse(src)
    executor = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Executor")
    methods = {n.name for n in executor.body if isinstance(n, ast.FunctionDef)}
    assert {"_finalize", "_size_buy", "_size_for_broker", "_execute_one"} <= methods


def test_gate_4_and_the_alarm_read_one_set():
    assert em._LEGIT_CONSTRAINTS is REGISTERED_SKIP_CLASSES


# --------------------------------------------------------------------------- #
# Gate 4 reading Friday's record
# --------------------------------------------------------------------------- #
def test_friday_shaped_skip_is_a_failure_under_the_no_third_state_clause():
    friday = {"side": "SKIP", "order_id": "", "constraint": "",
              "error": "Insufficient cash for meaningful position", "broker_order": None}
    assert em.classify_execution(friday) == "fail:unclassified_skip"


def test_scoring_counts_the_untagged_skip():
    rec = {"cycle_id": "c1", "execution_mode": "broker_paper", "api_success": True,
           "executions": [{"side": "SKIP", "order_id": "", "constraint": "", "broker_order": None}]}
    s = em.score_cycles([rec], {"c1": True})
    assert s["successes"] == 0 and s["failure_cycles_by_reason"] == {"unclassified_skip": 1}


# --------------------------------------------------------------------------- #
# The descriptor
# --------------------------------------------------------------------------- #
def _rec(executions, nav=2000.0, date="2026-11-09"):
    return {"date": date, "portfolio_after": {"total_value": nav}, "executions": executions}


def test_the_descriptor_is_one_subtraction():
    row = session_row([_rec([
        {"side": "BUY", "executed": True, "notional": 150.0, "intended_notional": 200.0,
         "order_id": "x", "constraint": "UNSETTLED_FUNDS_CAPPED"},
        {"side": "SKIP", "executed": False, "notional": 0, "intended_notional": 0.5,
         "order_id": "", "constraint": "BELOW_VENUE_MINIMUM"},
        {"side": "HOLD", "executed": True, "notional": 0, "intended_notional": 0.0,
         "order_id": "HOLD"},
    ])])
    assert (row["intended_usd"], row["expressed_usd"], row["gap_usd"]) == (200.5, 150.0, 50.5)
    assert row["orders_intended"] == 2 and row["orders_expressed"] == 1
    assert row["skips_by_constraint"] == {"BELOW_VENUE_MINIMUM": 1}
    assert row["intended_source"] == "logged"


def test_the_descriptor_catches_friday_from_the_old_record_shape():
    """No intended_notional on the record: target_weight x NAV, and it says so."""
    row = session_row([_rec([
        {"side": "SKIP", "executed": False, "notional": 0, "order_id": "", "constraint": "",
         "decision": {"action": "BUY", "target_weight": 0.08}},
        {"side": "BUY", "executed": True, "notional": 200.0, "order_id": "x",
         "decision": {"action": "BUY", "target_weight": 0.10}},
    ])])
    assert row["intended_usd"] == 360.0 and row["expressed_usd"] == 200.0
    assert row["skips_by_constraint"] == {"UNTAGGED": 1}
    assert row["intended_source"] == "target_weight_x_nav"


def test_forced_orders_are_outside_the_descriptor():
    row = session_row([_rec([
        {"side": "SELL", "executed": True, "notional": 300.0, "order_id": "FORCED_POSITION_STOP",
         "decision": {"action": "SELL", "target_weight": 0.0}},
    ])])
    assert row["intended_usd"] == 0 and row["expressed_usd"] == 0


def test_the_writer_replaces_one_session_and_leaves_the_rest(tmp_path):
    trades, out = tmp_path / "t", tmp_path / "o"
    trades.mkdir()
    line = json.dumps(_rec([{"side": "BUY", "executed": True, "notional": 10.0,
                             "intended_notional": 10.0, "order_id": "x"}]))
    (trades / "grok_2026-11.jsonl").write_text(line + "\n", encoding="utf-8")
    path = write_session_descriptor("2026-11-09", ["grok"], trades, out)
    month = json.loads(path.read_text(encoding="utf-8"))
    month["2026-11-06"] = {"grok": {"keep": True}}
    path.write_text(json.dumps(month), encoding="utf-8")

    with open(trades / "grok_2026-11.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    write_session_descriptor("2026-11-09", ["grok"], trades, out)
    month = json.loads(path.read_text(encoding="utf-8"))
    assert month["2026-11-06"] == {"grok": {"keep": True}}
    assert month["2026-11-09"]["grok"]["cycles"] == 2
    assert month["2026-11-09"]["grok"]["expressed_usd"] == 20.0


def test_a_torn_trades_line_fails_loud(tmp_path):
    (tmp_path / "grok_2026-11.jsonl").write_text("{not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        write_session_descriptor("2026-11-09", ["grok"], tmp_path, tmp_path / "o")
