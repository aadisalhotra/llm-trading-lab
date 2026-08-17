"""Direction-segmented RQ2/RQ3, gross-weight RQ5, full-vocabulary RQ6.

The three code fixes ratified 2026-08-17 (sites 2, 3, 4 of the long-only filter
sweep). Each corrects the code to a registration, never the reverse:

  * site 2 — _executed_trades and the RQ2/RQ3 replays were BUY/SELL-only, so
    every published disposition and calibration figure is a long-segment
    estimate. Segmented here, with outcomes sign-corrected relative to position
    direction (a short's gain is a price decline) and administrative closures
    censored from realization classification.
  * site 3 — _rq5_trade_panel dropped short legs from HHI entirely (long-only
    fallback plus a `shares > 0` filter). Now gross (absolute) weights
    throughout, per the registered RQ5 entry.
  * site 4 — compute_rq6 scored decision sets over {BUY, SELL} only, so HOLD
    was invisible and a BUY-vs-HOLD flip between calls read as agreement.
    The set is now the deployed configuration's full action vocabulary.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analytics import research_metrics as R  # noqa: E402


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _ex(side, ticker, shares, price, *, confidence=None, forced=None):
    d = {"action": side, "ticker": ticker, "target_weight": 0.1}
    if confidence is not None:
        d["confidence"] = confidence
    return {
        "ticker": ticker, "side": side, "executed": True,
        "shares": float(shares), "fill_price": float(price),
        "notional": float(shares) * float(price),
        "order_id": (f"FORCED_{forced}" if forced else f"PAPER_{side}_{ticker}"),
        "decision": d,
        "direction": "short" if side in ("SHORT", "COVER") else "long",
    }


def _hold(ticker, shares, upl_pct, price=100.0):
    return {"ticker": ticker, "shares": float(shares), "avg_cost": price,
            "current_price": price, "market_value": float(shares) * price,
            "direction": "short" if shares < 0 else "long",
            "unrealized_pl_pct": upl_pct}


def _rec(date, executions, holdings=(), total_value=100000.0, ts=None):
    return {
        "date": date, "timestamp": ts or f"{date}T14:00:00", "api_success": True,
        "executions": list(executions),
        "portfolio_after": {"total_value": total_value, "holdings": list(holdings)},
    }


# --------------------------------------------------------------------------- #
# site 2 — segmentation and sign correction
# --------------------------------------------------------------------------- #
def test_executed_trades_segments_by_direction():
    rec = _rec("2026-07-02", [
        _ex("BUY", "AAPL", 10, 100), _ex("SELL", "AAPL", 10, 110),
        _ex("SHORT", "QCOM", 20, 180), _ex("COVER", "QCOM", 20, 170),
    ])
    assert [e["side"] for e in R._executed_trades(rec, "long")] == ["BUY", "SELL"]
    assert [e["side"] for e in R._executed_trades(rec, "short")] == ["SHORT", "COVER"]
    assert len(R._executed_trades(rec, "both")) == 4
    # the default is the registered long segment, so existing call sites keep
    # computing exactly the quantity they were always computing
    assert R._executed_trades(rec) == R._executed_trades(rec, "long")


def test_unfilled_executions_never_counted():
    rec = _rec("2026-07-02", [dict(_ex("SHORT", "QCOM", 20, 180), executed=False)])
    assert R._executed_trades(rec, "short") == []


def test_short_gain_is_a_price_decline():
    """Cover below the short entry is a GAIN; the long rule would score a loss."""
    recs = [
        _rec("2026-07-02", [_ex("SHORT", "QCOM", 20, 180)]),
        _rec("2026-07-03", [_ex("COVER", "QCOM", 20, 170)], holdings=[]),
    ]
    ev = list(R._replay_avg_cost(recs, "short"))
    assert len(ev) == 1
    assert (ev[0]["rg"], ev[0]["rl"]) == (1, 0)


def test_short_loss_is_a_price_rise():
    recs = [
        _rec("2026-07-02", [_ex("SHORT", "QCOM", 20, 180)]),
        _rec("2026-07-03", [_ex("COVER", "QCOM", 20, 195)], holdings=[]),
    ]
    ev = list(R._replay_avg_cost(recs, "short"))
    assert (ev[0]["rg"], ev[0]["rl"]) == (0, 1)


def test_long_sign_convention_unchanged():
    recs = [
        _rec("2026-07-02", [_ex("BUY", "AAPL", 10, 100)]),
        _rec("2026-07-03", [_ex("SELL", "AAPL", 10, 110)], holdings=[]),
    ]
    ev = list(R._replay_avg_cost(recs, "long"))
    assert (ev[0]["rg"], ev[0]["rl"]) == (1, 0)


def test_paper_counts_are_segmented():
    """A short holding must not land in the long segment's paper denominator.

    This is the July contamination: the published pooled figure counted short
    positions' unrealized P&L as long-side paper gains and losses.
    """
    recs = [
        _rec("2026-07-02", [_ex("BUY", "AAPL", 10, 100), _ex("SHORT", "QCOM", 20, 180)]),
        _rec("2026-07-03", [_ex("SELL", "AAPL", 10, 110)],
             holdings=[_hold("MSFT", 5, 0.04), _hold("QCOM", -20, 0.03), _hold("NKE", -5, -0.02)]),
    ]
    long_ev = list(R._replay_avg_cost(recs, "long"))
    # one long paper gain (MSFT); the two short legs belong to the short segment
    assert (long_ev[0]["pg"], long_ev[0]["pl"]) == (1, 0)


def test_administrative_close_is_censored_not_classified():
    """FORCED_ closes move the book but never score as a realization."""
    recs = [
        _rec("2026-07-02", [_ex("SHORT", "META", 10, 600)]),
        _rec("2026-08-03", [_ex("COVER", "META", 10, 589, forced="POSITION_STOP")],
             holdings=[]),
    ]
    assert list(R._replay_avg_cost(recs, "short")) == []
    assert R._closed_trades(recs, "short") == []


def test_chosen_close_after_partial_admin_close_is_still_censored():
    recs = [
        _rec("2026-07-02", [_ex("SHORT", "META", 10, 600)]),
        _rec("2026-07-03", [_ex("COVER", "META", 4, 590, forced="POSITION_STOP")]),
        _rec("2026-07-06", [_ex("COVER", "META", 6, 580)], holdings=[]),
    ]
    # the position's life was contaminated by a closure the model did not choose
    assert R._closed_trades(recs, "short") == []


def test_closed_short_trade_profitability_and_confidence():
    recs = [
        _rec("2026-07-02", [_ex("SHORT", "QCOM", 20, 180, confidence=7)]),
        _rec("2026-07-03", [_ex("COVER", "QCOM", 20, 170)], holdings=[]),
    ]
    closed = R._closed_trades(recs, "short")
    assert len(closed) == 1
    tr = closed[0]
    assert tr["segment"] == "short"
    assert tr["profitable"] == 1                      # covered 10 below entry
    assert tr["realized_pnl"] == pytest.approx(200.0)  # 20 * (180 - 170)
    assert tr["entry_confidence"] == 7


def test_closed_trade_fifo_survives_a_censored_life():
    """A censored life must not shift entry confidence onto the next trade."""
    recs = [
        _rec("2026-07-02", [_ex("SHORT", "META", 10, 600, confidence=3)]),
        _rec("2026-07-03", [_ex("COVER", "META", 10, 590, forced="POSITION_STOP")],
             holdings=[]),
        _rec("2026-07-06", [_ex("SHORT", "META", 10, 500, confidence=9)]),
        _rec("2026-07-07", [_ex("COVER", "META", 10, 480)], holdings=[]),
    ]
    closed = R._closed_trades(recs, "short")
    assert len(closed) == 1
    assert closed[0]["entry_confidence"] == 9          # not the censored 3


def test_partial_trim_never_closes_a_short():
    recs = [
        _rec("2026-07-02", [_ex("SHORT", "QCOM", 20, 180, confidence=6)]),
        _rec("2026-07-03", [_ex("COVER", "QCOM", 5, 175)],
             holdings=[_hold("QCOM", -15, 0.02)]),
    ]
    assert R._closed_trades(recs, "short") == []


def test_replay_rejects_an_unknown_segment():
    with pytest.raises(ValueError):
        list(R._replay_avg_cost([], "sideways"))
    with pytest.raises(ValueError):
        R._closed_trades([], "sideways")


def test_rq2_reports_both_segments_and_aliases_long():
    recs = {"gpt": [
        _rec("2026-07-02", [_ex("BUY", "AAPL", 10, 100), _ex("SHORT", "QCOM", 20, 180)]),
        _rec("2026-07-03", [_ex("SELL", "AAPL", 10, 110), _ex("COVER", "QCOM", 20, 170)],
             holdings=[_hold("MSFT", 5, 0.04), _hold("NKE", -5, -0.02)]),
    ]}
    rmap = {"2026-07-02": R.INSUFFICIENT, "2026-07-03": R.INSUFFICIENT}
    out = R.compute_rq2(recs, rmap, ["gpt"], n_resamples=50)
    assert set(out["segments"]) == {"long", "short"}
    assert out["primary_segment"] == "long"
    # top-level keys alias the confirmatory long segment
    assert out["pooled"] is out["segments"]["long"]["pooled"]
    assert out["per_model"] is out["segments"]["long"]["per_model"]
    assert out["pooled_sign_corrected"]["basis"] == "secondary_descriptive"


def test_rq3_reports_both_segments_and_aliases_long():
    recs = {"gpt": [
        _rec("2026-07-02", [_ex("BUY", "AAPL", 10, 100, confidence=8),
                            _ex("SHORT", "QCOM", 20, 180, confidence=7)]),
        _rec("2026-07-03", [_ex("SELL", "AAPL", 10, 110),
                            _ex("COVER", "QCOM", 20, 170)], holdings=[]),
    ]}
    rmap = {"2026-07-02": R.INSUFFICIENT, "2026-07-03": R.INSUFFICIENT}
    out = R.compute_rq3(recs, rmap, ["gpt"], n_resamples=50)
    assert set(out["segments"]) == {"long", "short"}
    assert out["segments"]["long"]["pooled"]["n_closed_trades"] == 1
    assert out["segments"]["short"]["pooled"]["n_closed_trades"] == 1
    assert out["pooled"] is out["segments"]["long"]["pooled"]


# --------------------------------------------------------------------------- #
# site 3 — RQ5 gross weights
# --------------------------------------------------------------------------- #
def test_gross_hhi_counts_a_short_like_a_long():
    assert R.gross_hhi([100.0, -100.0]) == pytest.approx(0.5)
    assert R.gross_hhi([100.0, 100.0]) == pytest.approx(0.5)


def test_rq5_panel_counts_short_legs_in_concentration():
    """A book that is one long and one equal short is NOT concentrated.

    Under the old long-only HHI the short leg vanished, leaving a single
    remaining long at HHI 1.0 — maximum concentration for a balanced book.
    """
    recs = [
        _rec("2026-07-02", [], holdings=[_hold("AAPL", 100, 0.0, price=100.0)],
             ts="2026-07-02T14:00:00"),
        _rec("2026-07-03", [_ex("SHORT", "QCOM", 100, 100)],
             holdings=[_hold("AAPL", 100, 0.0, price=100.0),
                       _hold("QCOM", -100, 0.0, price=100.0)],
             ts="2026-07-03T14:00:00"),
    ]
    obs = R._rq5_trade_panel(recs, "2026-07-01")
    assert len(obs) == 1
    # post-trade gross book is 50/50 -> HHI 0.5; pre-trade was AAPL alone -> 1.0
    assert obs[0]["dHHI_trade"] == pytest.approx(0.5 - 1.0)


def test_rq5_panel_reprices_a_name_whose_only_activity_was_a_cover():
    """The price map must come from all four sides, not BUY/SELL only."""
    recs = [
        _rec("2026-07-02", [], holdings=[_hold("AAPL", 100, 0.0, price=100.0),
                                         _hold("QCOM", -50, 0.0, price=100.0)],
             ts="2026-07-02T14:00:00"),
        # QCOM fully covered this period, so it is absent from holdings and its
        # only price source is the COVER fill
        _rec("2026-07-03", [_ex("COVER", "QCOM", 50, 100)],
             holdings=[_hold("AAPL", 100, 0.0, price=100.0)],
             ts="2026-07-03T14:00:00"),
    ]
    obs = R._rq5_trade_panel(recs, "2026-07-01")
    assert len(obs) == 1
    # pre-trade gross book = AAPL 10000 + QCOM 5000 -> HHI = (2/3)^2 + (1/3)^2
    pre = (2.0 / 3.0) ** 2 + (1.0 / 3.0) ** 2
    assert obs[0]["dHHI_trade"] == pytest.approx(1.0 - pre)


def test_rq5_panel_long_only_book_is_unchanged_by_the_gross_spec():
    recs = [
        _rec("2026-06-02", [], holdings=[_hold("AAPL", 100, 0.0, price=100.0),
                                         _hold("MSFT", 100, 0.0, price=100.0)],
             ts="2026-06-02T14:00:00"),
        _rec("2026-06-03", [_ex("SELL", "MSFT", 100, 100)],
             holdings=[_hold("AAPL", 100, 0.0, price=100.0)],
             ts="2026-06-03T14:00:00"),
    ]
    obs = R._rq5_trade_panel(recs, "2026-06-01")
    assert obs[0]["dHHI_trade"] == pytest.approx(1.0 - 0.5)


# --------------------------------------------------------------------------- #
# site 4 — RQ6 full action vocabulary
# --------------------------------------------------------------------------- #
def test_rq6_vocabulary_per_configuration():
    assert R.rq6_action_vocabulary("v2") == ("BUY", "SELL", "HOLD")
    assert R.rq6_action_vocabulary("v3") == ("BUY", "SELL", "HOLD", "SHORT", "COVER")
    assert R.rq6_action_vocabulary("v4") == ("BUY", "SELL", "HOLD")


def test_rq6_unknown_configuration_widens_rather_than_narrows():
    assert R.rq6_action_vocabulary("v99") == ("BUY", "SELL", "HOLD", "SHORT", "COVER")


def _write_probe(tmp_path, monkeypatch, rows):
    d = tmp_path / "determinism"
    d.mkdir()
    with open(d / "probe.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    monkeypatch.setattr(R, "DETERMINISM_DIR", d)


def _call(model, ctx, cfg, decisions):
    return {"model_key": model, "context_id": ctx, "prompt_version": cfg,
            "api_success": True,
            "decisions": [{"ticker": t, "action": a} for t, a in decisions]}


def test_rq6_scores_a_buy_vs_hold_flip_as_divergence(tmp_path, monkeypatch):
    """The site-4 defect: dropping HOLD made this pair look identical."""
    _write_probe(tmp_path, monkeypatch, [
        _call("gpt", "c1", "v2", [("AAPL", "BUY"), ("MSFT", "HOLD")]),
        _call("gpt", "c1", "v2", [("AAPL", "BUY"), ("MSFT", "BUY")]),
    ])
    out = R.compute_rq6(["gpt"])
    assert out["status"] == "Testing"
    assert out["per_model"]["gpt"]["Delta_m"] > 0.0


def test_rq6_identical_calls_have_zero_divergence(tmp_path, monkeypatch):
    _write_probe(tmp_path, monkeypatch, [
        _call("gpt", "c1", "v2", [("AAPL", "BUY"), ("MSFT", "HOLD")]),
        _call("gpt", "c1", "v2", [("MSFT", "HOLD"), ("AAPL", "BUY")]),
    ])
    out = R.compute_rq6(["gpt"])
    assert out["per_model"]["gpt"]["Delta_m"] == pytest.approx(0.0)


def test_rq6_includes_short_and_cover_under_v3(tmp_path, monkeypatch):
    _write_probe(tmp_path, monkeypatch, [
        _call("gpt", "c1", "v3", [("QCOM", "SHORT")]),
        _call("gpt", "c1", "v3", [("QCOM", "COVER")]),
    ])
    out = R.compute_rq6(["gpt"])
    assert out["action_vocabulary"]["v3"] == ["BUY", "SELL", "HOLD", "SHORT", "COVER"]
    assert out["per_model"]["gpt"]["Delta_m"] == pytest.approx(1.0)


def test_rq6_never_pools_across_configurations(tmp_path, monkeypatch):
    _write_probe(tmp_path, monkeypatch, [
        _call("gpt", "c1", "v2", [("AAPL", "BUY")]),
        _call("gpt", "c1", "v2", [("AAPL", "HOLD")]),
        _call("gpt", "c2", "v3", [("QCOM", "SHORT")]),
        _call("gpt", "c2", "v3", [("QCOM", "SHORT")]),
    ])
    out = R.compute_rq6(["gpt"])
    entry = out["per_model"]["gpt"]
    assert set(entry["by_configuration"]) == {"v2", "v3"}
    assert entry["Delta_m"] is None            # cross-configuration pooling prohibited
    assert entry["by_configuration"]["v2"]["Delta_m"] > 0.0
    assert entry["by_configuration"]["v3"]["Delta_m"] == pytest.approx(0.0)
    assert out["overall_mean_divergence"] is None
