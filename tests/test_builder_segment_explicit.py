"""The monthly builder must name its RQ2/RQ3 direction segment explicitly.

Regression cover for the silent-default coupling described in
docs/RQ2-paper-leg-contamination.md.

`_replay_avg_cost` and `_closed_trades` gained a ``segment`` parameter in
6998c10d with a ``"long"`` default. `scripts/build_monthly_data_layer.py` called
both positionally with no segment, so the meaning of the published monthly RQ2
block changed - from "long realized leg over a direction-blind paper leg" to
"long segment" - with no diff in the builder and no signal at build time.

The fix makes ``segment`` a required positional argument of `_rq2_month` and
`_rq3_month`. These tests pin that, so a future re-introduction of a default
fails loudly here instead of silently changing a published number.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.research_metrics import (  # noqa: E402
    get_model_keys,
    load_decision_records,
)

JULY_START, JULY_END = "2026-07-01", "2026-07-31"
# Small B: these assertions are on point estimates and counts, never on the
# bootstrap interval, so the resample count only has to be legal.
B = 200


def _builder():
    spec = importlib.util.spec_from_file_location(
        "bmdl_under_test", ROOT / "scripts" / "build_monthly_data_layer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bmdl():
    return _builder()


@pytest.fixture(scope="module")
def records():
    keys = get_model_keys()
    return keys, {k: load_decision_records(k) for k in keys}


# ----------------------------------------------------------------------
# The guard itself
# ----------------------------------------------------------------------

def test_rq2_month_requires_an_explicit_segment(bmdl, records):
    keys, full = records
    with pytest.raises(TypeError):
        bmdl._rq2_month(full, keys, JULY_START, JULY_END, B)


def test_rq3_month_requires_an_explicit_segment(bmdl, records):
    keys, full = records
    with pytest.raises(TypeError):
        bmdl._rq3_month(full, keys, JULY_START, JULY_END, B)


def test_segment_is_echoed_in_the_returned_block(bmdl, records):
    keys, full = records
    for seg in ("long", "short"):
        assert bmdl._rq2_month(full, keys, JULY_START, JULY_END, B, seg)["segment"] == seg
        assert bmdl._rq3_month(full, keys, JULY_START, JULY_END, B, seg)["segment"] == seg


def test_an_invalid_segment_is_rejected_by_the_helper(bmdl, records):
    keys, full = records
    with pytest.raises(ValueError):
        bmdl._rq2_month(full, keys, JULY_START, JULY_END, B, "sideways")


# ----------------------------------------------------------------------
# The segments are genuinely different, and match the registered figures
# ----------------------------------------------------------------------

def test_july_long_segment_matches_the_registered_clean_figure(bmdl, records):
    keys, full = records
    pooled = bmdl._rq2_month(full, keys, JULY_START, JULY_END, B, "long")["pooled"]
    # The clean long-segment recomputation registered in the RQ2 scoping text.
    assert pooled["disposition_difference"] == pytest.approx(-0.10730611196712891, abs=1e-12)
    assert pooled["n_sale_records"] == 547
    assert pooled["realized_gains"] == 313
    assert pooled["realized_losses"] == 345
    # The paper leg is what the fix corrected: short holdings are out of it.
    assert pooled["paper_gains"] == 6767
    assert pooled["paper_losses"] == 1932


def test_july_long_segment_is_not_the_published_hybrid(bmdl, records):
    """The published -0.1031 was a long numerator over a direction-blind
    denominator. The corrected long segment must differ from it."""
    keys, full = records
    pooled = bmdl._rq2_month(full, keys, JULY_START, JULY_END, B, "long")["pooled"]
    assert pooled["disposition_difference"] != pytest.approx(-0.10308579739847612, abs=1e-9)


def test_july_short_segment_matches_the_registered_exploratory_figure(bmdl, records):
    keys, full = records
    pooled = bmdl._rq2_month(full, keys, JULY_START, JULY_END, B, "short")["pooled"]
    assert pooled["disposition_difference"] == pytest.approx(-0.033816425120772986, abs=1e-12)
    assert pooled["realized_gains"] == 8
    assert pooled["realized_losses"] == 22


def test_rq3_long_segment_reproduces_the_published_value_exactly(bmdl, records):
    """RQ3 took no numerical damage from the silent default - a closed trade is
    built from one segment's own vocabulary, so shorts were absent rather than
    mixed in. The registration is a relabeling, and this pins that claim."""
    keys, full = records
    pooled = bmdl._rq3_month(full, keys, JULY_START, JULY_END, B, "long")["pooled"]
    assert pooled["confidence_outcome_corr"] == 0.19357739190316292
    assert pooled["n_closed_trades"] == 236


def test_rq3_short_segment_is_a_separate_exploratory_output(bmdl, records):
    keys, full = records
    pooled = bmdl._rq3_month(full, keys, JULY_START, JULY_END, B, "short")["pooled"]
    assert pooled["n_closed_trades"] == 26
    assert pooled["confidence_outcome_corr"] == pytest.approx(-0.20370370370370378, abs=1e-12)


# ----------------------------------------------------------------------
# Pre-shorting months are unaffected, which is why reproduce-May stays exact
# ----------------------------------------------------------------------

def test_no_short_activity_before_shorting_activation(bmdl, records):
    """Shorting activated 2026-07-01. No pre-July month can carry a short
    position, so no pre-July layer is exposed to the defect - and the month
    gate on the new keys is what keeps May and June byte-reproducible."""
    keys, full = records
    pooled = bmdl._rq2_month(full, keys, "2026-04-09", "2026-06-30", B, "short")["pooled"]
    assert pooled["n_sale_records"] == 0


def test_pre_july_long_segment_equals_the_unsegmented_paper_leg(bmdl, records):
    """Before shorting, segmenting the paper leg is a no-op: there are no short
    holdings to exclude. This is the formal reason the fix cannot move a
    published pre-July figure."""
    keys, full = records
    seg = bmdl._rq2_month(full, keys, "2026-04-09", "2026-06-30", B, "long")["pooled"]
    from src.analytics.research_metrics import _pgr_plr, _executed_trades, _is_administrative
    from src.analytics.research_metrics import SHARES_EPSILON
    from collections import defaultdict

    def replay_unsegmented(recs):
        shares, avg_cost = defaultdict(float), {}
        for rec in recs:
            closes, closed_tickers = [], set()
            for ex in _executed_trades(rec, "long"):
                t = ex["ticker"]
                price = float(ex.get("fill_price") or 0.0)
                qty = float(ex.get("shares") or 0.0)
                if ex["side"] == "BUY":
                    new = shares[t] + qty
                    if new > 0:
                        avg_cost[t] = (avg_cost.get(t, price) * shares[t] + price * qty) / new
                    shares[t] = new
                else:
                    ac = avg_cost.get(t)
                    if ac is not None and price > 0 and not _is_administrative(ex):
                        closes.append((price > ac, price < ac))
                        closed_tickers.add(t)
                    shares[t] = max(0.0, shares[t] - qty)
                    if shares[t] < SHARES_EPSILON:
                        shares[t] = 0.0
            if closes:
                pg = pl = 0
                for h in (rec.get("portfolio_after") or {}).get("holdings", []):
                    if h.get("ticker") in closed_tickers:
                        continue
                    upl = h.get("unrealized_pl_pct")          # no segment filter
                    if upl is None:
                        continue
                    pg, pl = (pg + 1, pl) if upl > 0 else ((pg, pl + 1) if upl < 0 else (pg, pl))
                d = rec.get("date", "")
                if "2026-04-09" <= d <= "2026-06-30":
                    yield {"rg": sum(1 for g, _ in closes if g),
                           "rl": sum(1 for _, ll in closes if ll), "pg": pg, "pl": pl}

    ev = [e for k in keys for e in replay_unsegmented(full.get(k, []))]
    pgr, plr, *_ = _pgr_plr(ev)
    assert seg["PGR"] == pytest.approx(pgr, abs=1e-12)
    assert seg["PLR"] == pytest.approx(plr, abs=1e-12)
