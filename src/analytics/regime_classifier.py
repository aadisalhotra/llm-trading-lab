"""Post-hoc market-regime labeling for regime-stratified RQ analysis.

Every inferential metric in the paper is stratified by market regime so a
finding ("models converge", "models are well calibrated", ...) can be read
as conditional on the market state rather than averaged over a single
18-month tape. Regimes are assigned *post hoc* from SPY daily prices using
the pre-registered quantitative criteria below — no discretion, no
look-ahead beyond the trailing windows each rule names.

Pre-registered criteria (docs/PRE_REGISTRATION.md §Regime stratification):

  * Bull-trending : SPY 20-day return > +3%  AND 20-day annualized vol < 15%
  * Range-bound   : SPY 20-day return in [-2%, +2%] AND 20-day vol in [10%, 20%]
  * Vol-spike     : SPY 5-day realized annualized vol > 25%
  * Drawdown      : SPY > 5% below its trailing 60-day peak (EOD close)

A single day can satisfy more than one rule (e.g. a vol-spike inside a
drawdown). To produce mutually-exclusive strata we apply a fixed precedence:

      vol-spike > drawdown > bull-trending > range-bound > neutral

Rationale: the acute states (a volatility spike, then a sustained drawdown)
dominate the directional/quiet labels because they are what RQ5 and the
robustness checks care about. The raw per-rule booleans are also returned,
so anyone can re-stratify under a different precedence without re-running.

NOTE: this SPY "drawdown" *regime* (>5% off a 60-day peak) is a market-state
label and is deliberately distinct from the RQ5 *portfolio* drawdown trigger
(a model >=10% off its own 60-day equity peak). Different series, different
thresholds, different purpose. See PRE_REGISTRATION.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from ..config_loader import DATA_DIR

logger = logging.getLogger("llmlab.regime")

REGIMES_DIR = DATA_DIR / "regimes"
_SPY_CACHE = REGIMES_DIR / "spy_daily.csv"

# Labels (exported so callers don't hard-code strings)
BULL = "bull_trending"
RANGE = "range_bound"
VOLSPIKE = "vol_spike"
DRAWDOWN = "drawdown"
NEUTRAL = "neutral"
INSUFFICIENT = "insufficient_history"

# Mutually-exclusive assignment precedence (highest first)
REGIME_PRECEDENCE = [VOLSPIKE, DRAWDOWN, BULL, RANGE, NEUTRAL]

ALL_REGIMES = [BULL, RANGE, VOLSPIKE, DRAWDOWN, NEUTRAL]


@dataclass
class RegimeCriteria:
    """Pre-registered thresholds. Defaults are locked in PRE_REGISTRATION.md."""
    trend_window: int = 20
    vol_window: int = 20
    short_vol_window: int = 5
    peak_window: int = 60
    periods_per_year: int = 252

    bull_min_return: float = 0.03         # > +3% over trend_window
    bull_max_vol: float = 0.15            # annualized < 15%
    range_return_band: float = 0.02       # |return| <= 2%
    range_vol_low: float = 0.10           # 10%..20% annualized
    range_vol_high: float = 0.20
    volspike_threshold: float = 0.25      # 5-day annualized vol > 25%
    drawdown_threshold: float = -0.05     # <= -5% off 60-day peak

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# SPY price loading (date-range, cached)
# --------------------------------------------------------------------------

def fetch_spy_daily(
    start: str = "2015-01-01",
    end: str | None = None,
    use_cache: bool = True,
) -> pd.Series:
    """Return a SPY daily *close* series indexed by date.

    Caches to data/regimes/spy_daily.csv. If the cache already covers the
    requested ``end`` it is reused; otherwise the full range is refetched and
    the cache overwritten. On a network failure we fall back to whatever cache
    exists (possibly empty) so downstream stratification degrades gracefully
    rather than crashing the report.
    """
    end = end or datetime.utcnow().strftime("%Y-%m-%d")
    REGIMES_DIR.mkdir(parents=True, exist_ok=True)

    cached: pd.Series | None = None
    if use_cache and _SPY_CACHE.exists():
        try:
            c = pd.read_csv(_SPY_CACHE, parse_dates=["date"]).set_index("date")["close"]
            c = c[~c.index.duplicated(keep="last")].sort_index()
            cached = c
            # Reuse only if the cache covers BOTH ends of the request — a
            # prior narrow fetch must not shadow a later wide one.
            covers_end = (not c.empty) and c.index.max() >= pd.Timestamp(end) - pd.Timedelta(days=4)
            covers_start = (not c.empty) and c.index.min() <= pd.Timestamp(start) + pd.Timedelta(days=4)
            if covers_end and covers_start:
                return c.loc[start:end]
        except Exception:
            logger.exception("Failed to read SPY regime cache; refetching")

    try:
        import yfinance as yf
        df = yf.download("SPY", start=start,
                         end=(pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                         progress=False, auto_adjust=False)
        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            series = df["Close"].astype(float)
            series.index = pd.to_datetime(series.index).tz_localize(None)
            series.name = "close"
            series = series[~series.index.duplicated(keep="last")].sort_index()
            # Merge with any existing cache so we never shrink the stored
            # history; freshly fetched values win on overlapping dates.
            if cached is not None and not cached.empty:
                merged = pd.concat([cached, series])
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            else:
                merged = series
            out = merged.copy()
            out.index.name = "date"
            out.reset_index().rename(columns={"index": "date"}).to_csv(_SPY_CACHE, index=False)
            return out.loc[start:end]
    except Exception:
        logger.exception("SPY daily fetch failed")

    if cached is not None:
        logger.warning("Using stale SPY cache for regime classification")
        return cached.loc[:end]
    logger.error("No SPY data available — regime stratification will be empty")
    return pd.Series(dtype=float, name="close")


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

def classify_regime_series(
    closes: pd.Series,
    criteria: RegimeCriteria | None = None,
) -> pd.DataFrame:
    """Label each trading day with a single mutually-exclusive regime.

    Returns a DataFrame indexed by date with the diagnostic series
    (ret_NNd, vol_NNd_annual, vol_5d_annual, drawdown_60d), the per-rule
    boolean flags, and the final ``regime`` column.
    """
    crit = criteria or RegimeCriteria()
    closes = closes.dropna().sort_index()
    if closes.empty:
        return pd.DataFrame()

    df = pd.DataFrame({"close": closes.astype(float)})
    daily_ret = df["close"].pct_change()

    sqrt_year = np.sqrt(crit.periods_per_year)
    df["ret_trend"] = df["close"].pct_change(crit.trend_window)
    df["vol_trend"] = daily_ret.rolling(crit.vol_window).std(ddof=1) * sqrt_year
    df["vol_short"] = daily_ret.rolling(crit.short_vol_window).std(ddof=1) * sqrt_year
    df["peak_60d"] = df["close"].rolling(crit.peak_window, min_periods=1).max()
    df["drawdown_60d"] = df["close"] / df["peak_60d"] - 1.0

    # Per-rule booleans
    df["is_bull"] = (df["ret_trend"] > crit.bull_min_return) & (df["vol_trend"] < crit.bull_max_vol)
    df["is_range"] = (
        (df["ret_trend"].abs() <= crit.range_return_band)
        & (df["vol_trend"] >= crit.range_vol_low)
        & (df["vol_trend"] <= crit.range_vol_high)
    )
    df["is_volspike"] = df["vol_short"] > crit.volspike_threshold
    df["is_drawdown"] = df["drawdown_60d"] <= crit.drawdown_threshold

    # Need at least trend_window history for a real label
    have_history = daily_ret.rolling(crit.trend_window).count() >= crit.trend_window

    def _assign(row: pd.Series) -> str:
        if not bool(have_history.loc[row.name]):
            return INSUFFICIENT
        if row["is_volspike"]:
            return VOLSPIKE
        if row["is_drawdown"]:
            return DRAWDOWN
        if row["is_bull"]:
            return BULL
        if row["is_range"]:
            return RANGE
        return NEUTRAL

    df["regime"] = df.apply(_assign, axis=1)
    return df


def classify_regimes(
    start: str = "2015-01-01",
    end: str | None = None,
    criteria: RegimeCriteria | None = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Fetch SPY and classify regimes over [start, end]."""
    closes = fetch_spy_daily(start=start, end=end, use_cache=use_cache)
    return classify_regime_series(closes, criteria)


def label_for_dates(
    dates: list[str] | list[datetime],
    regime_df: pd.DataFrame | None = None,
) -> dict[str, str]:
    """Map each requested date (YYYY-MM-DD) to its regime label.

    Non-trading dates resolve to the most recent prior trading day's regime
    (forward-fill). Dates outside the SPY history resolve to ``insufficient``.
    """
    norm = [d if isinstance(d, str) else d.strftime("%Y-%m-%d") for d in dates]
    if not norm:
        return {}
    if regime_df is None:
        lo = min(norm)
        start = (pd.Timestamp(lo) - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
        regime_df = classify_regimes(start=start, end=max(norm))
    if regime_df is None or regime_df.empty:
        return {d: INSUFFICIENT for d in norm}

    reg = regime_df["regime"].copy()
    reg.index = pd.to_datetime(reg.index).tz_localize(None)
    reg = reg.sort_index()
    out: dict[str, str] = {}
    for d in norm:
        ts = pd.Timestamp(d)
        prior = reg.loc[:ts]
        out[d] = str(prior.iloc[-1]) if len(prior) else INSUFFICIENT
    return out


def summarize_regimes(regime_df: pd.DataFrame) -> dict[str, Any]:
    """Counts and date coverage per regime — drives the docs/report tables."""
    if regime_df is None or regime_df.empty:
        return {"total_days": 0, "counts": {}, "first_date": None, "last_date": None}
    counts = regime_df["regime"].value_counts().to_dict()
    return {
        "total_days": int(len(regime_df)),
        "counts": {k: int(v) for k, v in counts.items()},
        "pct": {k: round(v / len(regime_df), 4) for k, v in counts.items()},
        "first_date": regime_df.index.min().strftime("%Y-%m-%d"),
        "last_date": regime_df.index.max().strftime("%Y-%m-%d"),
    }


def main() -> int:
    import argparse
    from ..config_loader import configure_logging, load_settings

    configure_logging()
    parser = argparse.ArgumentParser(description="Classify SPY market regimes")
    parser.add_argument("--start", default=None, help="YYYY-MM-DD (default: experiment start)")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    start = args.start
    if start is None:
        try:
            start = load_settings().get("experiment_start_date", "2026-04-09")
        except Exception:
            start = "2026-04-09"

    df = classify_regimes(start=start, end=args.end, use_cache=not args.no_cache)
    if df.empty:
        print("No SPY data available.")
        return 1
    summary = summarize_regimes(df)
    print(f"SPY regime calendar {summary['first_date']} -> {summary['last_date']} "
          f"({summary['total_days']} trading days)\n")
    for regime in REGIME_PRECEDENCE + [INSUFFICIENT]:
        n = summary["counts"].get(regime, 0)
        if n:
            print(f"  {regime:<22} {n:>4} days  ({summary['pct'].get(regime, 0)*100:5.1f}%)")
    print("\nMost recent 15 sessions:")
    tail = df.tail(15)
    for date, row in tail.iterrows():
        print(f"  {date.strftime('%Y-%m-%d')}  {row['regime']:<22} "
              f"ret20={row['ret_trend']*100:+6.2f}%  vol20={row['vol_trend']*100:5.1f}%  "
              f"dd60={row['drawdown_60d']*100:+6.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
