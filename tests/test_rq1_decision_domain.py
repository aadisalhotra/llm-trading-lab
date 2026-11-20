"""RQ1 decision domain and truncated-record exclusion (hub F4, 2026-11-19).

Registered: docs/registrations/rq1_cohort_reduction_and_decision_domain.md
§§1, 3 and output_budget_equalization_ruling.md §3.1. These tests pin:
  * the default call is unchanged from the implementation before this change
    (e800658c0), bit for bit, permutation stream included;
  * the carried-holdings domain adds carried longs as HOLD, and nothing else
    (not shorts, not dust, not a ticker listed with SHORT/COVER);
  * the carried state is the previous record's portfolio_after, whatever that
    record was (failed call, administrative row, outside the window);
  * the truncation predicate and the exclusion switch;
  * the survivor set uses only its members' pairs, from the same draws.
"""
from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest

from src.analytics import research_metrics as rm

ROOT = Path(__file__).resolve().parents[1]
BASE = "e800658c0"


def _old_module():
    src = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE}:src/analytics/research_metrics.py"],
                         capture_output=True, text=True, check=True, encoding="utf-8").stdout
    mod = types.ModuleType("src.analytics._rm_before_domain")
    mod.__package__ = "src.analytics"
    exec(compile(src, "research_metrics@base", "exec"), mod.__dict__)
    return mod


def _hold(t, w=0.05):
    return {"ticker": t, "action": "HOLD", "target_weight": w}


def _buy(t, w=0.05):
    return {"ticker": t, "action": "BUY", "target_weight": w}


def _sell(t):
    return {"ticker": t, "action": "SELL", "target_weight": 0.0}


def _holding(t, shares, price=100.0, direction="long"):
    return {"ticker": t, "shares": shares, "current_price": price,
            "market_value": shares * price, "direction": direction}


def _rec(date, h, raw, holdings=(), ok=True, **kw):
    r = {"date": date, "timestamp": f"{date}T15:00:00", "api_success": ok,
         "data_inputs_hash": h, "raw_decisions": list(raw),
         "portfolio_after": {"holdings": list(holdings)}}
    r.update(kw)
    return r


def _world():
    """Three models over four ticks with a mix of listed rows and holdings."""
    A = ["AAA", "BBB", "CCC", "DDD"]
    hold_all = [_holding(t, 10) for t in A]
    recs = {
        "gpt": [
            _rec("2026-09-01", "h0", [_buy(t) for t in A], hold_all),
            _rec("2026-09-02", "h1", [_sell("AAA")], hold_all[1:]),
            _rec("2026-09-03", "h2", [], hold_all[1:]),
            _rec("2026-09-04", "h3", [_hold("BBB"), _hold("CCC"), _sell("DDD")], hold_all[1:3]),
        ],
        "gemini": [
            _rec("2026-09-01", "h0", [_buy(t) for t in A], hold_all),
            _rec("2026-09-02", "h1", [_sell("AAA"), _hold("BBB")], hold_all[1:]),
            _rec("2026-09-03", "h2", [_sell("BBB")], hold_all[2:]),
            _rec("2026-09-04", "h3", [_hold("CCC"), _hold("DDD")], hold_all[2:]),
        ],
        "grok": [
            _rec("2026-09-01", "h0", [_buy(t) for t in A], hold_all),
            _rec("2026-09-02", "h1", [], hold_all),
            _rec("2026-09-03", "h2", [_sell("CCC"), _hold("DDD"), _hold("AAA"), _hold("BBB")],
                 [hold_all[0], hold_all[1], hold_all[3]]),
            _rec("2026-09-04", "h3", [_sell("AAA")], hold_all[1:2] + hold_all[3:]),
        ],
    }
    return recs, ["gpt", "gemini", "grok"]


def test_default_call_is_bit_identical_to_the_base_implementation():
    old = _old_module()
    recs, keys = _world()
    o = old.compute_rq1(recs, {}, keys, n_permutations=40, n_resamples=50)
    n = rm.compute_rq1(recs, {}, keys, n_permutations=40, n_resamples=50)
    assert {k: n[k] for k in o} == o


def test_carried_domain_adds_carried_longs_as_hold():
    recs, keys = _world()
    ex = rm.compute_rq1(recs, {}, keys, n_permutations=5, n_resamples=20)
    ca = rm.compute_rq1(recs, {}, keys, n_permutations=5, n_resamples=20, domain="carried")
    assert ca["n_pairwise_observations"] > ex["n_pairwise_observations"]
    # gpt at h2 lists nothing but carries BBB, CCC, DDD: it enters only under carried
    assert ca["decision_domain"] == "carried" and ex["decision_domain"] == "explicit"


def _domain_of(recs, key, h, **kw):
    """The action map compute_rq1 builds for one model at one tick, read back
    through a two-model world in which the partner lists every ticker."""
    partner = [_rec(r["date"], r["data_inputs_hash"],
                    [_hold(t) for t in ("AAA", "BBB", "CCC", "DDD", "EEE")]) for r in recs]
    out = rm.compute_rq1({key: recs, "partner": partner}, {}, [key, "partner"],
                         n_permutations=1, n_resamples=10, **kw)
    return out


def test_shorts_dust_and_short_cover_rows_stay_outside_the_carried_domain():
    prev = _rec("2026-09-01", "p0", [], [
        _holding("AAA", 10), _holding("BBB", -5, direction="short"),
        _holding("CCC", 0.004, price=100.0),          # $0.40: dust
        _holding("DDD", 3), _holding("EEE", 4)])
    cur = _rec("2026-09-02", "t1", [{"ticker": "EEE", "action": "COVER"}], [])
    out = _domain_of([prev, cur], "m", "t1", domain="carried")
    # m's domain at t1: AAA, DDD (carried longs). Not BBB (short), CCC (dust),
    # EEE (listed with COVER). Two shared tickers < min_shared 3: no pair.
    assert out["n_pairwise_observations"] == 0
    prev["portfolio_after"]["holdings"].append(_holding("FFF", 2))
    cur2 = _rec("2026-09-02", "t1", [{"ticker": "EEE", "action": "COVER"}, _buy("FFF")], [])
    # FFF listed explicitly (BUY) joins AAA, DDD: three tickers, but the
    # partner lists only AAA-EEE, so shared = AAA, DDD: still no pair.
    assert _domain_of([prev, cur2], "m", "t1", domain="carried")["n_pairwise_observations"] == 0
    prev["portfolio_after"]["holdings"].append(_holding("BBB", 1, direction="long"))
    # now AAA, BBB(long), DDD carried: three shared with the partner -> one pair
    assert _domain_of([prev, cur], "m", "t1", domain="carried")["n_pairwise_observations"] == 1


def test_carried_state_comes_from_the_previous_record_whatever_it_was():
    seed_rec = _rec("2026-08-01", "x0", [], [_holding(t, 5) for t in ("AAA", "BBB", "CCC")])
    failed = _rec("2026-09-01", "x1", [], [_holding(t, 5) for t in ("AAA", "BBB", "CCC")], ok=False)
    cur = _rec("2026-09-02", "t1", [], [])
    out = _domain_of([seed_rec, failed, cur], "m", "t1", domain="carried",
                     window=("2026-09-01", "2026-09-30"))
    # the window excludes x0 and the failure is not a decision record, but
    # their portfolios still define what m carried into t1
    assert out["n_pairwise_observations"] == 1


@pytest.mark.parametrize("rec,key,expected", [
    ({"api_finish_reason": "max_tokens", "output_tokens": 100}, "claude", True),
    ({"api_finish_reason": "length"}, "gpt", True),
    ({"api_finish_reason": "MAX_TOKENS"}, "gemini", True),
    ({"api_finish_reason": "end_turn", "output_tokens": 4096}, "claude", False),
    ({"api_finish_reason": None, "output_tokens": 4096, "date": "2026-08-01"}, "claude", True),
    ({"output_tokens": 4095, "date": "2026-08-01"}, "claude_opus", False),
    ({"output_tokens": 5000, "date": "2026-11-24"}, "claude", False),     # budget 16384 then
    ({"output_tokens": 9000, "date": "2026-08-01"}, "gemini", False),     # no token rule
])
def test_truncation_predicate(rec, key, expected):
    assert rm.is_truncated_record(rec, key) is expected


def test_exclusion_switch_drops_truncated_records_and_counts_them():
    recs, keys = _world()
    recs["gpt"][1]["api_finish_reason"] = "length"
    on = rm.compute_rq1(recs, {}, keys, n_permutations=5, n_resamples=20, exclude_truncated=True)
    off = rm.compute_rq1(recs, {}, keys, n_permutations=5, n_resamples=20)
    assert on["n_truncated_records_excluded"] == {"gpt": 1}
    assert off["n_truncated_records_excluded"] == {}


def test_survivor_set_uses_only_member_pairs_from_the_same_draws():
    recs, keys = _world()
    full = rm.compute_rq1(recs, {}, keys, n_permutations=30, n_resamples=20, domain="carried",
                          survivor_set=["gemini", "grok"])
    s = full["survivor_set"]
    assert {p["pair"] for p in s["per_pair"]} <= {"gemini/grok"}
    match = [p for p in full["per_pair"] if p["pair"] == "gemini/grok"]
    if match:   # same draws: the pair's null is the same in both tables
        assert s["per_pair"][0]["null_concordance_mean"] == pytest.approx(
            match[0]["null_concordance_mean"])
    # the per-pair table sums to the headline
    tot = sum(p["n_observations"] for p in full["per_pair"])
    assert tot == full["n_pairwise_observations"]


def test_unknown_domain_is_refused():
    with pytest.raises(ValueError):
        rm.compute_rq1({}, {}, [], domain="universe")
