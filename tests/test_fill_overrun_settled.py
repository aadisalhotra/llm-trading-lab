"""Hub Thursday close 2026-11-12, item 3a: FILL_OVERRUN measures the settled
balance, not total cash. Sonnet's ORCL fill on 2026-11-11 overspent settled
funds by $0.2855 while unsettled proceeds kept total cash at +$60.73, and no
event fired.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_executor_sizing_floor import ACTIVE, _book, _buy, _run  # noqa: E402

from src.execution.executor import (  # noqa: E402
    CONSTRAINT_UNSETTLED_FUNDS_CAPPED,
    EVENT_FILL_OVERRUN,
)
from src.portfolio.portfolio import Holding, load_portfolio, save_portfolio  # noqa: E402
from src.portfolio.settlement import next_trading_day  # noqa: E402


# --------------------------------------------------------------------------- #
# 3a. FILL_OVERRUN on the settled balance
# --------------------------------------------------------------------------- #
def _sonnet_shaped_book():
    """$2,000 cash of which $1,900 is unsettled: $100 settled, all deployable."""
    book = _book(cash=2000.0)
    book.settlement.record_sale(1900.0, ACTIVE)
    assert book.settled_cash() == pytest.approx(100.0)
    return book


def test_a_capped_buy_that_overspends_settled_cash_is_a_fill_overrun():
    book = _sonnet_shaped_book()
    r = _run("broker", _buy("ORCL", 0.20), {"ORCL": 128.0}, book, slip=0.004)
    assert r.constraint == CONSTRAINT_UNSETTLED_FUNDS_CAPPED
    assert book.cash > 1800.0                               # total cash never went negative
    ev = r.fill_overrun
    assert ev is not None and ev["event"] == EVENT_FILL_OVERRUN
    assert ev["settled_balance_after"] == pytest.approx(book.settled_balance())
    assert ev["settled_balance_after"] < 0
    assert ev["amount_usd"] == pytest.approx(-book.settled_balance())
    assert ev["book_cash_after"] == pytest.approx(book.cash)
    assert ev["explained"] is True
    assert book.settled_cash() == 0.0                       # the clamp that hid it


def test_no_unsettled_proceeds_means_the_old_measure_exactly():
    book = _book(cash=9.60, holdings={"PG": Holding("PG", 19.904, 100.0)})
    assert book.settled_balance() == book.cash
    r = _run("broker", _buy("XOM", 0.10), {"XOM": 150.0, "PG": 100.0}, book, slip=0.001)
    assert r.fill_overrun["settled_balance_after"] == pytest.approx(book.cash)
    assert r.fill_overrun["book_cash_after"] == pytest.approx(book.cash)


def test_a_fill_at_reference_within_settled_cash_has_no_overrun():
    book = _sonnet_shaped_book()
    r = _run("broker", _buy("ORCL", 0.20), {"ORCL": 128.0}, book, slip=0.0)
    assert r.fill_overrun is None and book.settled_balance() >= -1e-6


def test_the_explanation_is_carried_until_the_proceeds_settle():
    book = _sonnet_shaped_book()
    book.inception_epoch = "paper"
    _run("broker", _buy("ORCL", 0.20), {"ORCL": 128.0}, book, slip=0.004)
    carried = book.fill_overrun_usd
    assert carried > 0 and book.cash > 0
    save_portfolio(book)
    assert load_portfolio("grok").fill_overrun_usd == pytest.approx(carried)

    book.settlement.settle_through(next_trading_day(ACTIVE))
    assert book.settled_balance() > 0
    save_portfolio(book)
    assert load_portfolio("grok").fill_overrun_usd == 0.0
