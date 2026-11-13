"""Ghost-position ruling §⑧ (Research, delivered inline 2026-11-12): position
stops evaluate only holdings with notional >= BEHAVIOURAL_NOTIONAL_FLOOR ($1,
the venue minimum) at the stop-evaluation mark. Dust is excluded from stop
evaluation and logged in the stop record; STOP UNPROTECTED fires only for an
actionable position; the drawdown halt is unchanged.

The case: Grok's 0.0001 NVDA ($0.02) breached its 15% stop at 09:33 ET on
2026-11-12, the forced SELL was refused as BELOW_VENUE_MINIMUM, and it raised
STOP UNPROTECTED (CRITICAL) and failed that cycle's Gate 4 score.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from src import pipeline  # noqa: E402
from src.adapters.base import DecisionResult  # noqa: E402
from src.execution import broker  # noqa: E402
from src.execution.broker import BEHAVIOURAL_NOTIONAL_FLOOR, MIN_ORDER_NOTIONAL  # noqa: E402
from src.logging import decision_log  # noqa: E402
from src.portfolio import risk  # noqa: E402
from src.portfolio.portfolio import Holding, Portfolio  # noqa: E402

NAN = float("nan")
GROK_COST, GROK_MARK = 187.93, 152.84           # 2026-11-12 14:32:50Z fetch, -18.67%


def _held(cash=1000.0, **positions) -> Portfolio:
    """positions: ticker=(shares, avg_cost)."""
    return Portfolio(model_key="grok", cash=cash,
                     holdings={t: Holding(t, s, c) for t, (s, c) in positions.items()},
                     inception_value=2000.0, inception_date="2026-11-06")


class _Executor:
    """Records force_liquidate calls; places nothing."""

    cycle_id = "20261112T143314Z"

    def __init__(self):
        self.forced: list[list[str]] = []

    def force_liquidate(self, portfolio, tickers, prices, reason, run_date=None):
        self.forced.append(list(tickers))
        return [f"forced:{t}" for t in tickers]


@pytest.fixture
def sweep(monkeypatch, caplog):
    alerts: list[tuple] = []
    monkeypatch.setattr(pipeline, "send_alert", lambda *a, **k: alerts.append((a, k)))
    caplog.set_level(logging.INFO)

    def run(book, prices):
        ex = _Executor()
        forced, dust = pipeline._position_stop_sweep("grok", book, prices, ex, "2026-11-12",
                                                    logging.getLogger("test.stop_sweep"))
        dust_lines = [r for r in caplog.records if "is dust" in r.getMessage()]
        return ex, forced, dust, alerts, dust_lines

    return run


# --------------------------------------------------------------------------- #
# The two cases the hub named
# --------------------------------------------------------------------------- #
def test_grok_nvda_dust_no_forced_order_no_alert_one_dust_line(sweep):
    book = _held(NVDA=(0.0001, GROK_COST))
    assert 1.0 - GROK_MARK / GROK_COST >= 0.15             # it IS past the stop line
    ex, forced, dust, alerts, lines = sweep(book, {"NVDA": GROK_MARK})
    assert ex.forced == [] and forced == []
    assert alerts == []
    assert len(lines) == 1 and "NVDA" in lines[0].getMessage()
    assert dust == [{"ticker": "NVDA", "shares": 0.0001, "mark": GROK_MARK,
                     "notional": pytest.approx(0.0001 * GROK_MARK)}]


def test_a_50_dollar_holding_at_the_same_loss_still_triggers(sweep):
    shares = 50.0 / GROK_MARK                               # $50 at the 09:33 mark
    book = _held(NVDA=(shares, GROK_COST))
    ex, forced, dust, alerts, lines = sweep(book, {"NVDA": GROK_MARK})
    assert ex.forced == [["NVDA"]] and forced == ["forced:NVDA"]
    assert [a[0][1] for a in alerts] == ["Position stop on grok"]
    assert dust == [] and lines == []


# --------------------------------------------------------------------------- #
# Domain, not level
# --------------------------------------------------------------------------- #
def test_the_floor_is_the_venue_minimum_derived_not_a_new_constant():
    assert BEHAVIOURAL_NOTIONAL_FLOOR is MIN_ORDER_NOTIONAL == 1.00
    src = Path(broker.__file__).read_text(encoding="utf-8")
    assert "BEHAVIOURAL_NOTIONAL_FLOOR = MIN_ORDER_NOTIONAL" in src


def test_evaluated_at_or_above_the_floor_exempt_below_it():
    """Same share count both sides of $1: the boundary is notional at the mark."""
    under = _held(XYZ=(0.01, 120.0))                       # $0.99 at 99.0
    at = _held(XYZ=(0.01, 120.0))                          # $1.00 at 100.0
    assert risk.check_position_stops(under, {"XYZ": 99.0}) == []
    assert risk.check_position_stops(at, {"XYZ": 100.0}) == ["XYZ"]


def test_the_threshold_reads_the_mark_not_the_cost():
    """$1.20 at cost, $0.95 at the mark: dust, because the stop evaluates at the
    mark (and deliberately not §⑤'s fill-price anchoring)."""
    book = _held(XYZ=(0.01, 120.0))
    assert risk.check_position_stops(book, {"XYZ": 95.0}) == []
    assert [d["ticker"] for d in risk.stop_dust_holdings(book, {"XYZ": 95.0})] == ["XYZ"]


def test_short_dust_is_exempt_from_the_10pct_short_stop():
    book = _held(AAPL=(-0.001, 200.0))                     # $0.24 short, +20% against
    assert risk.check_position_stops(book, {"AAPL": 240.0}) == []
    (d,) = risk.stop_dust_holdings(book, {"AAPL": 240.0})
    assert d["shares"] == -0.001 and d["notional"] == pytest.approx(0.24)


def test_a_real_position_beside_dust_still_triggers():
    book = _held(NVDA=(0.0001, GROK_COST), PG=(1.0, 200.0))
    prices = {"NVDA": GROK_MARK, "PG": 150.0}
    assert risk.check_position_stops(book, prices) == ["PG"]
    assert [d["ticker"] for d in risk.stop_dust_holdings(book, prices)] == ["NVDA"]


def test_dust_is_logged_whether_or_not_it_breached():
    """Not evaluated means not evaluated: unbreached dust is in the record too."""
    book = _held(NVDA=(0.0001, GROK_COST))
    assert [d["ticker"] for d in risk.stop_dust_holdings(book, {"NVDA": 190.0})] == ["NVDA"]


def test_unpriced_dust_is_dust_at_the_books_fallback_mark_not_unevaluable():
    book = _held(NVDA=(0.0001, GROK_COST), KO=(2.0, 95.0))
    prices = {"NVDA": NAN}                                  # both unpriced
    assert risk.position_stops_unevaluable(book, prices) == ["KO"]
    assert [d["ticker"] for d in risk.stop_dust_holdings(book, prices)] == ["NVDA"]


def test_the_drawdown_halt_still_counts_dust_in_nav():
    """§⑧ amends the stop's domain only; dust stays in NAV for the halt. With
    the registered 30% halt on a $2,000 book, cash of 1399.99 alone is a breach
    (-30.0005%); the $0.0153 of NVDA dust keeps the book clear (-29.9997%)."""
    book = _held(cash=1399.99, NVDA=(0.0001, GROK_COST))
    assert book.total_value({"NVDA": GROK_MARK}) == pytest.approx(1399.99 + 0.0001 * GROK_MARK)
    assert risk.evaluate_portfolio_stop(book, {"NVDA": GROK_MARK}) == risk.STOP_CLEAR
    book.holdings.clear()
    assert risk.evaluate_portfolio_stop(book, {"NVDA": GROK_MARK}) == risk.STOP_BREACH


# --------------------------------------------------------------------------- #
# The stop record lives on the decision log, never in executions
# --------------------------------------------------------------------------- #
def _log(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(decision_log, "TRADES_DIR", tmp_path)
    result = DecisionResult(provider="xai", model_id_configured="m", model_id_returned="m",
                            decisions=[], overall_reasoning="", raw_response="",
                            latency_seconds=0.0, success=True)
    decision_log.log_decision_run(
        model_key="grok", run_date=datetime(2026, 11, 13, 14, 33), decision_result=result,
        accepted_decisions=[], violations=[], execution_results=[],
        portfolio_snapshot_after={}, prompt_version="v4", data_inputs_hash="h",
        execution_mode="broker_paper", inception_date="2026-11-06", **kw)
    (line,) = (tmp_path / "grok_2026-11.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads(line)


def test_the_decision_log_carries_stop_dust_exempt(tmp_path, monkeypatch):
    dust = risk.stop_dust_holdings(_held(NVDA=(0.0001, GROK_COST)), {"NVDA": GROK_MARK})
    rec = _log(tmp_path, monkeypatch, stop_dust_exempt=dust)
    assert rec["stop_dust_exempt"] == [{"ticker": "NVDA", "shares": 0.0001, "mark": GROK_MARK,
                                        "notional": pytest.approx(0.0001 * GROK_MARK)}]
    assert rec["executions"] == []


def test_no_dust_is_an_empty_list_not_an_absent_key(tmp_path, monkeypatch):
    assert _log(tmp_path, monkeypatch)["stop_dust_exempt"] == []


def test_both_decision_log_calls_in_the_pipeline_pass_it():
    src = Path(pipeline.__file__).read_text(encoding="utf-8")
    assert src.count("log_decision_run(") == 2
    assert src.count("stop_dust_exempt=stop_dust_exempt,") == 2
