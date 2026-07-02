"""SPY benchmark hygiene invariants.

Pins the fix for the 2026-07-01 inception-anchor drift: the canonical SPY series
is deterministic and ledger-anchored, and the writer/reader refuse the
contamination (pre-inception rows, duplicate dates) that caused it. Same guard
discipline as the risk-cap invariant in test_shorting.py.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from src.analytics.performance import (
    canonical_spy_series,
    canonical_spy_return,
    _spy_inception_anchor,
    _require_unique_spy_dates,
)
from src.config_loader import load_settings
from src.logging import decision_log

ANCHOR_DATE = "2026-04-09"
ANCHOR_VALUE = 680.4000244140625

# inception -> 2026-07-01 SPY buy-and-hold, anchored at 680.40. Kept in sync with
# the July perf logs; assert to 4dp so a benign later tick doesn't break it.
EXPECTED_INCEPTION_RETURN_07_01 = 0.0961


def _snapshot(total: float = 100_000.0) -> dict:
    return {
        "total_value": total, "cash": total, "cash_pct": 1.0, "holdings": [],
        "cumulative_return": 0.0, "halted": False,
    }


# ---- Anchor pin ------------------------------------------------------------ #

def test_ledger_pins_spy_inception_anchor():
    anchor = _spy_inception_anchor()
    assert anchor is not None, "spy_benchmark_anchor missing from the Phase-A ledger"
    date, value = anchor
    assert date == ANCHOR_DATE
    assert value == ANCHOR_VALUE


# ---- Deterministic, clean series ------------------------------------------- #

def test_canonical_spy_series_clean_and_anchored():
    ser = canonical_spy_series(load_settings())
    assert ser is not None and len(ser) >= 2
    assert (ser["date_str"] >= ANCHOR_DATE).all(), "pre-inception rows leaked into the series"
    assert ser["date_str"].is_unique, "series has duplicate dates"
    assert float(ser.iloc[0]["benchmark_value"]) == ANCHOR_VALUE, "inception not pinned to the anchor"


def test_canonical_spy_series_deterministic():
    s = load_settings()
    a = [tuple(r) for r in canonical_spy_series(s).to_numpy().tolist()]
    b = [tuple(r) for r in canonical_spy_series(s).to_numpy().tolist()]
    assert a == b, "canonical SPY series is not reproducible across rebuilds"


def test_inception_return_is_anchored_961bp():
    r = canonical_spy_return(load_settings())
    assert r is not None
    assert round(r, 4) == EXPECTED_INCEPTION_RETURN_07_01, (
        f"inception->07-01 SPY should be {EXPECTED_INCEPTION_RETURN_07_01:.4f} "
        f"(anchor 680.40); got {r:.4f}"
    )


# ---- Reader invariant: halt on duplicate dates ----------------------------- #

def test_reader_halts_on_duplicate_dates():
    dup = pd.DataFrame({
        "date_str": ["2026-04-09", "2026-04-10", "2026-04-10"],
        "benchmark_value": [680.4, 679.46, 679.46],
    })
    with pytest.raises(ValueError, match="duplicate dates"):
        _require_unique_spy_dates(dup)


# ---- Writer invariants ----------------------------------------------------- #

def test_writer_refuses_pre_inception_row(tmp_path, monkeypatch):
    monkeypatch.setattr(decision_log, "PERFORMANCE_DIR", tmp_path)
    with pytest.raises(ValueError, match="pre-inception"):
        decision_log.log_daily_snapshot("testmodel", datetime(2026, 4, 8), _snapshot(), 676.01)
    assert not (tmp_path / "testmodel.jsonl").exists(), "pre-inception row must not be written"


def test_writer_noops_on_duplicate_date(tmp_path, monkeypatch):
    monkeypatch.setattr(decision_log, "PERFORMANCE_DIR", tmp_path)
    d = datetime(2026, 5, 1)
    decision_log.log_daily_snapshot("testmodel", d, _snapshot(), 700.0)
    decision_log.log_daily_snapshot("testmodel", d, _snapshot(), 700.0)  # duplicate same-date
    lines = [ln for ln in (tmp_path / "testmodel.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1, "duplicate-date row must not be persisted (idempotent no-op)"


# ---- Regime SPY snapshot: analysis reads the frozen artifact, not a live fetch ---- #

def test_regime_classification_prefers_frozen_snapshot(tmp_path, monkeypatch):
    import pandas as pd
    from src.analytics import regime_classifier as rc

    # A synthetic committed snapshot (deterministic ramp, enough rows for the
    # trailing 20/60-day windows).
    dates = pd.bdate_range("2026-01-02", periods=120)
    closes = [600.0 + i * 0.5 for i in range(len(dates))]
    snap_csv = tmp_path / "spy_daily.csv"
    pd.DataFrame({"date": dates, "close": closes}).to_csv(snap_csv, index=False)
    monkeypatch.setattr(rc, "SPY_SNAPSHOT_CSV", snap_csv)

    # If a snapshot exists, the LIVE fetch must not be called at all.
    def _boom(*a, **k):
        raise AssertionError("live yfinance fetch must not run when a snapshot exists")
    monkeypatch.setattr(rc, "fetch_spy_daily", _boom)

    assert rc.load_spy_snapshot() is not None
    df = rc.classify_regimes(start="2026-02-01", end="2026-06-30", use_snapshot=True)
    assert not df.empty and "regime" in df.columns
