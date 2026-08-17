"""Settled-funds execution constraint — the cash-account branch's T+1 ledger.

Registered 2026-08-17 (seam ruling): the paper simulator enforces settled cash
from the v4 activation on 2026-09-16, so the constraint's bugs surface in the
September smoke segment rather than during October's validation month.

What these tests pin:
  * the gate is doubly conditional — flag AND date — so the machinery ships now
    and binds only at the branch;
  * settled = cash - unsettled, with sale proceeds unsettled until T+1;
  * purchases cap at the settled balance, and a purchase that cannot be met
    from settled funds is blocked at submission rather than sent;
  * a blocked purchase enters no behavioral outcome metric;
  * stops stay unconditional — the property that makes the cash branch the
    registered venue shape in the first place.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analytics import research_metrics as R  # noqa: E402
from src.execution.executor import (  # noqa: E402
    CONSTRAINT_UNSETTLED_FUNDS,
    CONSTRAINT_UNSETTLED_FUNDS_CAPPED,
    Executor,
)
from src.portfolio.portfolio import Holding, Portfolio  # noqa: E402
from src.portfolio.settlement import (  # noqa: E402
    SettlementLedger,
    next_trading_day,
    settlement_enforcement_active,
)

ACTIVE = "2026-09-16"
BEFORE = "2026-09-15"


def _book(cash=10000.0, holdings=None):
    return Portfolio(model_key="gpt", cash=cash, holdings=holdings or {},
                     inception_value=cash, inception_date="2026-04-09")


def _executor(*, enforce=True, activation=ACTIVE, mode="paper"):
    ex = Executor.__new__(Executor)          # bypass load_settings() in __init__
    ex.settings = {"mode": mode, "settlement": {
        "enforce_settled_funds": enforce,
        "activation_date": activation,
        "settlement_days": 1,
    }}
    ex.mode = mode
    ex._alpaca_client = None
    return ex


def _buy(ticker, weight):
    return {"action": "BUY", "ticker": ticker, "target_weight": weight,
            "confidence": 7, "reasoning": "test"}


def _sell(ticker, weight=0.0):
    return {"action": "SELL", "ticker": ticker, "target_weight": weight,
            "confidence": 7, "reasoning": "test"}


# --------------------------------------------------------------------------- #
# activation gating — built now, binds at the branch
# --------------------------------------------------------------------------- #
def test_gate_requires_both_flag_and_date():
    on = {"settlement": {"enforce_settled_funds": True, "activation_date": ACTIVE}}
    off = {"settlement": {"enforce_settled_funds": False, "activation_date": ACTIVE}}
    assert settlement_enforcement_active(on, ACTIVE) is True
    assert settlement_enforcement_active(on, "2026-10-01") is True
    assert settlement_enforcement_active(on, BEFORE) is False       # before activation
    assert settlement_enforcement_active(off, "2026-10-01") is False  # branch not chosen


def test_gate_is_off_by_default_in_committed_settings():
    """The committed config must not bind the constraint before the branch."""
    from src.config_loader import load_settings
    s = load_settings()
    assert s["settlement"]["enforce_settled_funds"] is False
    assert s["settlement"]["activation_date"] == ACTIVE
    assert settlement_enforcement_active(s, "2026-12-31") is False


def test_missing_settlement_config_is_inert():
    assert settlement_enforcement_active({}, "2026-12-31") is False


# --------------------------------------------------------------------------- #
# the ledger
# --------------------------------------------------------------------------- #
def test_next_trading_day_skips_the_weekend():
    assert next_trading_day("2026-09-18") == "2026-09-21"   # Friday -> Monday


def test_settled_is_cash_minus_unsettled():
    led = SettlementLedger()
    led.record_sale(2500.0, "2026-09-16")
    assert led.unsettled_total() == pytest.approx(2500.0)
    assert led.settled_cash(10000.0) == pytest.approx(7500.0)


def test_settlement_matures_on_the_next_session_and_is_idempotent():
    led = SettlementLedger()
    led.record_sale(2500.0, "2026-09-16")            # settles 2026-09-17
    assert led.settle_through("2026-09-16") == 0.0   # same day: still unsettled
    assert led.settle_through("2026-09-17") == pytest.approx(2500.0)
    assert led.settle_through("2026-09-17") == 0.0   # idempotent
    assert led.settled_cash(10000.0) == pytest.approx(10000.0)


def test_only_proceeds_create_unsettled_balance():
    led = SettlementLedger()
    assert led.record_sale(0.0, "2026-09-16") is None
    assert led.record_sale(-50.0, "2026-09-16") is None
    assert led.unsettled_total() == 0.0


def test_settled_cash_never_goes_negative():
    led = SettlementLedger()
    led.record_sale(500.0, "2026-09-16")
    assert led.settled_cash(100.0) == 0.0


def test_ledger_round_trips_and_drops_malformed_tranches():
    led = SettlementLedger()
    led.record_sale(100.0, "2026-09-16")
    back = SettlementLedger.from_list(led.to_list())
    assert back.unsettled_total() == pytest.approx(100.0)
    assert SettlementLedger.from_list(None).unsettled_total() == 0.0
    assert SettlementLedger.from_list([{"amount": 0, "settles_on": ""}]).tranches == []


def test_portfolio_state_round_trips_the_ledger(tmp_path, monkeypatch):
    import src.portfolio.portfolio as P
    monkeypatch.setattr(P, "STATE_DIR", tmp_path)
    p = _book()
    p.settlement.record_sale(1500.0, "2026-09-16")
    P.save_portfolio(p)
    back = P.load_portfolio("gpt")
    assert back.unsettled_cash() == pytest.approx(1500.0)
    assert back.settled_cash() == pytest.approx(p.cash - 1500.0)


def test_legacy_state_without_the_field_loads_as_fully_settled(tmp_path, monkeypatch):
    import json

    import src.portfolio.portfolio as P
    monkeypatch.setattr(P, "STATE_DIR", tmp_path)
    (tmp_path / "gpt.json").write_text(json.dumps({
        "model_key": "gpt", "cash": 5000.0, "halted": False,
        "inception_value": 5000.0, "inception_date": "2026-04-09", "holdings": {},
    }), encoding="utf-8")
    back = P.load_portfolio("gpt")
    assert back.unsettled_cash() == 0.0
    assert back.settled_cash() == pytest.approx(5000.0)


def test_snapshot_exposes_the_split():
    p = _book(cash=10000.0)
    p.settlement.record_sale(4000.0, "2026-09-16")
    snap = p.snapshot({})
    assert snap["cash"] == pytest.approx(10000.0)
    assert snap["unsettled_cash"] == pytest.approx(4000.0)
    assert snap["settled_cash"] == pytest.approx(6000.0)


# --------------------------------------------------------------------------- #
# enforcement in the executor
# --------------------------------------------------------------------------- #
def test_sale_proceeds_are_unsettled_and_block_same_day_redeployment():
    """The registered behaviour: sell at cycle N, cannot redeploy until T+1."""
    ex = _executor()
    p = _book(cash=0.0, holdings={"AAPL": Holding("AAPL", 50.0, 100.0)})
    prices = {"AAPL": 100.0, "MSFT": 100.0}

    ex.execute_decisions(p, [_sell("AAPL")], prices, run_date=ACTIVE)
    assert p.cash == pytest.approx(5000.0)          # proceeds are in the account
    assert p.settled_cash() == pytest.approx(0.0)   # but not deployable

    res = ex.execute_decisions(p, [_buy("MSFT", 0.5)], prices, run_date=ACTIVE)[0]
    assert res.executed is False
    assert res.constraint == CONSTRAINT_UNSETTLED_FUNDS
    assert res.order_id == "EXEC_CONSTRAINT_UNSETTLED_FUNDS"
    assert "MSFT" not in p.holdings


def test_proceeds_are_deployable_on_the_next_session():
    ex = _executor()
    p = _book(cash=0.0, holdings={"AAPL": Holding("AAPL", 50.0, 100.0)})
    prices = {"AAPL": 100.0, "MSFT": 100.0}
    ex.execute_decisions(p, [_sell("AAPL")], prices, run_date="2026-09-16")

    res = ex.execute_decisions(p, [_buy("MSFT", 0.5)], prices, run_date="2026-09-17")[0]
    assert res.executed is True
    assert res.constraint == ""
    assert p.holdings["MSFT"].shares > 0


def test_purchase_caps_at_the_settled_balance_rather_than_total_cash():
    ex = _executor()
    p = _book(cash=10000.0)
    p.settlement.record_sale(6000.0, ACTIVE)        # settles 2026-09-17
    res = ex.execute_decisions(p, [_buy("MSFT", 1.0)], {"MSFT": 100.0},
                               run_date=ACTIVE)[0]
    assert res.executed is True
    assert res.constraint == CONSTRAINT_UNSETTLED_FUNDS_CAPPED
    # only the 4000 settled could be spent, not the full 10000 of cash
    assert res.notional == pytest.approx(4000.0, abs=100.0)
    assert p.cash == pytest.approx(10000.0 - res.notional)


def test_no_enforcement_before_activation_date():
    ex = _executor()
    p = _book(cash=0.0, holdings={"AAPL": Holding("AAPL", 50.0, 100.0)})
    prices = {"AAPL": 100.0, "MSFT": 100.0}
    ex.execute_decisions(p, [_sell("AAPL")], prices, run_date=BEFORE)
    assert p.unsettled_cash() == 0.0                # nothing tracked pre-activation
    res = ex.execute_decisions(p, [_buy("MSFT", 0.5)], prices, run_date=BEFORE)[0]
    assert res.executed is True                     # same-day redeployment allowed


def test_no_enforcement_when_the_branch_flag_is_off():
    ex = _executor(enforce=False)
    p = _book(cash=0.0, holdings={"AAPL": Holding("AAPL", 50.0, 100.0)})
    prices = {"AAPL": 100.0, "MSFT": 100.0}
    ex.execute_decisions(p, [_sell("AAPL")], prices, run_date="2026-12-01")
    assert p.unsettled_cash() == 0.0
    assert ex.execute_decisions(p, [_buy("MSFT", 0.5)], prices,
                                run_date="2026-12-01")[0].executed is True


def test_genuine_insufficient_cash_is_not_labelled_a_settlement_block():
    ex = _executor()
    p = _book(cash=10.0)
    res = ex.execute_decisions(p, [_buy("MSFT", 1.0)], {"MSFT": 100.0},
                               run_date=ACTIVE)[0]
    assert res.executed is False
    assert res.constraint == ""
    assert "Insufficient cash" in res.error


def test_stops_stay_unconditional_under_the_constraint():
    """The whole point of the cash branch: a stop can always close a position."""
    ex = _executor()
    p = _book(cash=0.0, holdings={"AAPL": Holding("AAPL", 50.0, 100.0),
                                  "MSFT": Holding("MSFT", 10.0, 100.0)})
    prices = {"AAPL": 100.0, "MSFT": 80.0}
    # exhaust settled cash with an earlier sale in the same session
    ex.execute_decisions(p, [_sell("AAPL")], prices, run_date=ACTIVE)
    assert p.settled_cash() == pytest.approx(0.0)

    forced = ex.force_liquidate(p, ["MSFT"], prices, "POSITION_STOP", run_date=ACTIVE)
    assert len(forced) == 1 and forced[0].executed is True
    assert "MSFT" not in p.holdings
    # the stop's own proceeds are unsettled like any other sale
    assert p.unsettled_cash() == pytest.approx(5000.0 + 800.0)


# --------------------------------------------------------------------------- #
# the censoring guarantee
# --------------------------------------------------------------------------- #
def test_blocked_purchase_enters_no_behavioral_outcome_metric():
    """A blocked intention is censored, not behaviour — it is never executed,
    so every behavioral reader filters it out by construction."""
    ex = _executor()
    p = _book(cash=0.0, holdings={"AAPL": Holding("AAPL", 50.0, 100.0)})
    prices = {"AAPL": 100.0, "MSFT": 100.0}
    ex.execute_decisions(p, [_sell("AAPL")], prices, run_date=ACTIVE)
    blocked = ex.execute_decisions(p, [_buy("MSFT", 0.5)], prices, run_date=ACTIVE)[0]

    rec = {"date": ACTIVE, "timestamp": f"{ACTIVE}T14:00:00", "api_success": True,
           "executions": [{
               "ticker": blocked.ticker, "side": blocked.side,
               "executed": blocked.executed, "shares": blocked.shares,
               "fill_price": blocked.fill_price, "order_id": blocked.order_id,
               "constraint": blocked.constraint,
           }],
           "portfolio_after": {"total_value": 5000.0, "holdings": []}}
    for seg in ("long", "short", "both"):
        assert R._executed_trades(rec, seg) == []
    assert list(R._replay_avg_cost([rec], "long")) == []
    assert R._closed_trades([rec], "long") == []
