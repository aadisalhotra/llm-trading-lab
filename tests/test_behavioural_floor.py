"""Ghost-position ruling section 5 and 5(b): metrics measure behaviour with a
$1 notional floor at the fill price, never a fixed share constant.

  * dust_threshold_shares(p) = BEHAVIOURAL_NOTIONAL_FLOOR / p, derived from the
    venue minimum (no second constant);
  * closure: the closing fill takes residual notional below $1;
  * entry: a fill opens a position when the prior residual notional is below $1;
  * the realized leg is computed over the closed portion; the sub-$1 residual
    is dust, not charged to the position;
  * defab_clean_window: dust and oversell thresholds derived at the fill price.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.analytics import research_metrics as rm
from src.execution.broker import BEHAVIOURAL_NOTIONAL_FLOOR, MIN_ORDER_NOTIONAL

ROOT = Path(__file__).resolve().parents[1]


def _rec(date, *fills):
    return {"date": date, "timestamp": f"{date}T15:00:00", "api_success": True,
            "executions": [{"ticker": t, "side": s, "shares": q, "fill_price": p, "executed": True,
                            "order_id": f"x-{i}", "decision": {"confidence": 7}}
                           for i, (t, s, q, p) in enumerate(fills)]}


def test_threshold_is_the_venue_minimum_at_the_fill_price():
    assert BEHAVIOURAL_NOTIONAL_FLOOR == MIN_ORDER_NOTIONAL == 1.00
    assert rm.dust_threshold_shares(1370.0) == pytest.approx(1 / 1370.0)
    assert rm.dust_threshold_shares(10.0) == pytest.approx(0.1)


@pytest.mark.parametrize("bad", [0.0, -5.0, float("nan"), float("inf"), None])
def test_no_price_means_nothing_is_dust(bad):
    assert rm.dust_threshold_shares(bad) == 0.0


def test_the_rulings_lly_example_is_a_position_not_dust():
    """0.007 LLY at ~$1,370 is $9.60: a behavioural position. Under the old
    0.01-share constant it read as closed."""
    recs = [_rec("2026-11-10", ("LLY", "BUY", 0.017, 1370.0)),
            _rec("2026-11-11", ("LLY", "SELL", 0.010, 1380.0))]   # leaves 0.007 = $9.66
    assert rm._closed_trades(recs, "long") == []


def test_closure_below_one_dollar_residual():
    recs = [_rec("2026-11-10", ("LLY", "BUY", 0.0170, 1370.0)),
            _rec("2026-11-11", ("LLY", "SELL", 0.0166, 1380.0))]  # leaves 0.0004 = $0.55
    (ct,) = rm._closed_trades(recs, "long")
    assert (ct["entry_date"], ct["exit_date"]) == ("2026-11-10", "2026-11-11")


def test_realized_is_over_the_closed_portion_not_charged_the_dust():
    recs = [_rec("2026-11-10", ("LLY", "BUY", 0.0170, 1370.0)),
            _rec("2026-11-11", ("LLY", "SELL", 0.0166, 1380.0))]
    (ct,) = rm._closed_trades(recs, "long")
    closed_cost = 0.0170 * 1370.0 * (0.0166 / 0.0170)
    assert ct["realized_pnl"] == pytest.approx(0.0166 * 1380.0 - closed_cost)
    assert ct["realized_pnl"] == pytest.approx(0.0166 * 10.0)      # +$10/share on what was sold
    assert ct["profitable"] == 1


def test_entry_resets_only_when_prior_residual_is_below_one_dollar():
    recs = [_rec("2026-11-10", ("LLY", "BUY", 0.0170, 1370.0)),
            _rec("2026-11-11", ("LLY", "SELL", 0.0100, 1380.0)),   # $9.66 left: still open
            _rec("2026-11-12", ("LLY", "BUY", 0.0050, 1360.0)),    # adds to the open position
            _rec("2026-11-13", ("LLY", "SELL", 0.0120, 1400.0))]   # closes everything
    (ct,) = rm._closed_trades(recs, "long")
    assert ct["entry_date"] == "2026-11-10"


def test_no_fixed_share_threshold_remains_in_the_ruled_sites():
    rmsrc = (ROOT / "src" / "analytics" / "research_metrics.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in rmsrc.splitlines() if not l.lstrip().startswith("#"))
    assert not re.search(r"^SHARES_EPSILON\s*=", code, re.M)
    assert "< SHARES_EPSILON" not in code
    defab = (ROOT / "scripts" / "defab_clean_window.py").read_text(encoding="utf-8")
    dcode = "\n".join(l for l in defab.splitlines() if not l.lstrip().startswith("#"))
    for gone in ("GHOST_SHARES", "OVERSELL_SHARES_MIN"):
        assert gone not in dcode, gone


def test_defab_thresholds_are_derived_at_the_fill_price():
    import importlib
    defab = importlib.import_module("scripts.defab_clean_window")
    assert defab._dust_shares(250.0) == pytest.approx(1 / 250.0)
    from src.config_loader import load_settings
    defab._per_name_tolerance_usd.cache_clear()
    assert defab._per_name_tolerance_usd() == float(
        load_settings()["reconciliation"]["position_tolerance_usd"]) == 1.0
