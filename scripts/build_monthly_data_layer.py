"""Canonical monthly-report DATA LAYER builder (read-only source of truth).

Produces ONE JSON artifact — reports/monthly/YYYY-MM/data_layer.json — that the
Reports/Design chat lays out into the 5-page monthly report. Every rendered
element of every page (including charts) renders from this layer; Design re-pulls
nothing from the raw logs.

DESIGN PRINCIPLES (locked; the quarterly aggregates three months of this file, so
the schema must be identical every month):

  * FIXED SCHEMA — every field is populated, set null, or thin-flagged. Fields are
    never added or dropped by month. Candidate metrics are reported separately, not
    added ad hoc.
  * READ-ONLY — nothing in data/ is mutated. Only the output JSON is written.
  * CANONICAL DEFINITIONS — every RQ metric comes from the existing compute_rqX /
    helper functions in src.analytics.research_metrics. Nothing is redefined,
    reimplemented, or approximated.
  * WINDOWS (applied exactly):
      - Monthly behavioral metrics + RQ1/RQ2/RQ3 point estimates: the target
        CALENDAR MONTH only (e.g. May 1-31 trading days).
      - Cumulative return + the equity-curve series: anchored to the 2026-04-09
        inception at $100,000 deployed capital (April 23-30 pilot data enters ONLY
        these inception-anchored series, never the monthly behavioral/RQ window).
      - RQ4/RQ5/RQ6: RAW ACCUMULATING INPUTS for quarterly pooling, not monthly
        point estimates — computed over their canonical accumulating windows and
        tagged as such.
      - RQ2/RQ3 are path-dependent: they replay the FULL position history (correct
        cost basis / entry confidence) and attribute events by calendar-month
        sale-date / exit-date — the same windowing discipline as
        scripts/behavioral_summary.py. Truncating the replay to month-only records
        mis-attributes positions straddling the month boundary.

Run:  python -m scripts.build_monthly_data_layer --month 2026-05
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analytics.performance import load_performance_history  # noqa: E402
from src.analytics.regime_classifier import (  # noqa: E402
    classify_regimes,
    label_for_dates,
    summarize_regimes,
)
from src.analytics.research_metrics import (  # noqa: E402
    RQ5_PHASE_A_PILOT_START,
    MIN_CLOSED_TRADES_RQ3,
    _attach_entry_confidence,  # noqa: F401 (kept for parity / documentation)
    _bootstrap_index_ci,
    _calibration_stats,
    _closed_trades,
    _daily_portfolio_features,
    _executed_trades,
    _pgr_plr,
    _replay_avg_cost,
    compute_rq1,
    compute_rq4,
    compute_rq5,
    compute_rq6,
    get_model_keys,
    load_decision_records,
)
from src.analytics.statistical_corrections import (  # noqa: E402
    DEFAULT_BLOCK_LENGTH,
    DEFAULT_N_RESAMPLES,
)
from src.config_loader import (  # noqa: E402
    PROJECT_ROOT,
    REPORTS_DIR,
    TRADES_DIR,
    configure_logging,
    load_settings,
)

logger = logging.getLogger("llmlab.data_layer")

INCEPTION_DATE = "2026-04-09"
INCEPTION_CAPITAL = 100_000.0

# Phase-A chart disclosure caption for the re-anchored equity curve (Research
# ruling). Emitted verbatim into charts.equity_curve.caption. The dates here are
# the fixed Phase-A clean-window anchor (clean_window_start) and the excluded
# launch/shakedown window; the re-anchor LOGIC reads the date from the ledger.
EQUITY_CURVE_CAPTION = (
    "Equity curve anchored at the clean-window start (April 23, 2026), consistent "
    "with cumulative_return_clean. The April 9–22 launch/shakedown window is "
    "excluded as non-estimable; see Data Integrity."
)

# Phase-A persistent integrity ledger (the carried facts that do not recompute
# monthly). The builder reads this and combines it with the month's decision logs
# and the de-fabricated clean-window series to emit the integrity layer natively.
INTEGRITY_LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "phase_a_integrity_ledger.json")
SCHEMA_DOC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "docs", "REPORT_SCHEMA.md")


def _load_ledger() -> dict:
    with open(INTEGRITY_LEDGER, encoding="utf-8") as f:
        return json.load(f)


# ==========================================================================
# Small utilities
# ==========================================================================

def _f(x):
    """Cast to plain float (JSON-safe), or None."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _source_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(PROJECT_ROOT),
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


def _eod_series(model_key: str, cap: str | None = None):
    """Inception-anchored EOD series for one model.

    Dedup the performance log to one row per date (last row = EOD), keep dates
    >= INCEPTION_DATE (drops the pre-inception 2026-04-08 seed rows). When `cap`
    is given (the report month-end), dates after it are dropped so the monthly
    report is frozen at its own month — later-month EOD rows never leak into a
    prior month's cumulative return or equity curve. Returns a list of
    (date, total_value) sorted by date.
    """
    df = load_performance_history(model_key)
    if df.empty:
        return []
    eod = OrderedDict()
    for _, r in df.iterrows():
        d = r["date"].strftime("%Y-%m-%d")
        eod[d] = float(r["total_value"])  # last write per date wins (chronological)
    return [(d, v) for d, v in sorted(eod.items())
            if d >= INCEPTION_DATE and (cap is None or d <= cap)]


def _spy_eod_series(cap: str | None = None):
    """Shared SPY EOD series from benchmark_value across all perf logs.

    All models log the same SPY price per tick; the union (last non-null per
    date) gives the full SPY series. Anchored to INCEPTION_DATE; capped at the
    report month-end when `cap` is given (same freeze rule as _eod_series).
    """
    spy = {}
    for fp in sorted((TRADES_DIR.parent / "performance").glob("*.jsonl")):
        df = load_performance_history(fp.stem)
        if df.empty or "benchmark_value" not in df.columns:
            continue
        for _, r in df.iterrows():
            bv = r.get("benchmark_value")
            if bv is None or (isinstance(bv, float) and bv != bv):
                continue
            d = r["date"].strftime("%Y-%m-%d")
            spy[d] = float(bv)  # last write per date wins
    return [(d, v) for d, v in sorted(spy.items())
            if d >= INCEPTION_DATE and (cap is None or d <= cap)]


def _max_drawdown(values):
    """Most-negative drawdown of a value series vs its own running peak."""
    if len(values) < 1:
        return None
    peak = values[0]
    mdd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return mdd


def _underwater(values):
    """Per-point drawdown series vs running peak (<= 0)."""
    out = []
    peak = float("-inf")
    for v in values:
        peak = max(peak, v)
        out.append(_f(v / peak - 1.0) if peak > 0 else 0.0)
    return out


def _reanchor_equity_series(series, base_date, clean_start):
    """Re-anchor an inception-anchored EOD (date, value) series onto the Phase-A
    clean window for the DISPLAYED equity curve (Research ruling).

    The launch/shakedown window (inception .. base_date) is excluded: only points
    dated >= clean_start are shown. indexed_return rebases to index_base 100 at the
    clean-window base close (base_date == the last shakedown day, 2026-04-22), so
    the month-end index == (1 + raw clean-window return) * 100 and the curve is
    consistent with cumulative_return_clean (computed from the same 4/22 base).
    portfolio_value stays the raw EOD book value. base_value falls back to the last
    value strictly before clean_start when base_date has no own EOD row.

    The inception-anchored cumulative_return is computed separately and is NOT
    affected by this re-anchor. Returns (points, base_value)."""
    by_date = dict(series)
    base_value = by_date.get(base_date)
    if base_value is None:
        prior = [v for d, v in series if d < clean_start]
        base_value = prior[-1] if prior else None
    points = []
    for d, v in series:
        if d < clean_start:
            continue
        points.append({
            "date": d,
            "portfolio_value": _f(v),
            "indexed_return": _f(v / base_value * 100.0) if base_value else None,
        })
    return points, base_value


def _ann_vol(daily_returns):
    if len(daily_returns) < 2:
        return None
    return _f(float(np.std(daily_returns, ddof=1)) * np.sqrt(252))


def _descriptive_sharpe(daily_returns, annual_rf):
    """Descriptive (NOT deflated) annualized Sharpe over the month's daily EOD
    returns, given a pinned annualized risk-free rate.

    Sharpe = mean(daily_return - daily_rf) / std(daily_return, ddof=1) * sqrt(252),
    with daily_rf = annual_rf / 252. Returns None if rf is unpinned or there are
    fewer than two daily returns / zero variance. This is the descriptive Sharpe
    only — explicitly not the Deflated Sharpe Ratio (v1.json sharpe_reporting)."""
    if annual_rf is None or len(daily_returns) < 2:
        return None
    arr = np.asarray(daily_returns, dtype=float)
    std = float(arr.std(ddof=1))
    if std == 0 or std != std:
        return None
    daily_rf = float(annual_rf) / 252.0
    return _f(float((arr - daily_rf).mean()) / std * np.sqrt(252))


# ==========================================================================
# Charts: pairwise decision-correlation matrix (RQ1 join methodology,
# computed as a SEPARATE chart field — not sourced from the RQ1 block)
# ==========================================================================

_ACTION_CODE = {"BUY": 0, "SELL": 1, "HOLD": 2}


def _correlation_matrix(may_records, model_keys, min_shared=3):
    """Full pairwise decision matrices over the calendar-month ticks.

    Uses the RQ1 join (data_inputs_hash + raw_decisions, >=3 shared tickers),
    but emitted as its own chart field so a page-2 heatmap never depends on the
    RQ1 block. action_concordance grand mean == the RQ1 headline scalar.
    """
    # tick hash -> {model: {ticker: (action_code, weight)}}
    ticks = defaultdict(dict)
    for key in model_keys:
        for rec in may_records.get(key, []):
            if not rec.get("api_success"):
                continue
            h = rec.get("data_inputs_hash")
            raw = rec.get("raw_decisions") or []
            if not h or not raw:
                continue
            actions, weights = {}, {}
            for d in raw:
                t = str(d.get("ticker", "")).upper().strip()
                a = str(d.get("action", "")).upper()
                if not t or a not in _ACTION_CODE:
                    continue
                actions[t] = _ACTION_CODE[a]
                try:
                    weights[t] = float(d.get("target_weight", 0.0))
                except (TypeError, ValueError):
                    weights[t] = 0.0
            if actions:
                ticks[h][key] = (actions, weights)

    conc = {a: {b: [] for b in model_keys} for a in model_keys}
    wcorr = {a: {b: [] for b in model_keys} for a in model_keys}
    nshared = {a: {b: 0 for b in model_keys} for a in model_keys}
    for _h, per_model in ticks.items():
        present = [k for k in model_keys if k in per_model]
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                ka, kb = present[i], present[j]
                a, wa = per_model[ka]
                b, wb = per_model[kb]
                shared = [t for t in a if t in b]
                if len(shared) < min_shared:
                    continue
                c = float(np.mean([1.0 if a[t] == b[t] else 0.0 for t in shared]))
                conc[ka][kb].append(c)
                conc[kb][ka].append(c)
                nshared[ka][kb] += 1
                nshared[kb][ka] += 1
                xa = np.array([wa[t] for t in shared], dtype=float)
                xb = np.array([wb[t] for t in shared], dtype=float)
                if xa.std() > 0 and xb.std() > 0:
                    r = float(np.corrcoef(xa, xb)[0, 1])
                    wcorr[ka][kb].append(r)
                    wcorr[kb][ka].append(r)

    def _mat(d, diag):
        m = {}
        for a in model_keys:
            m[a] = {}
            for b in model_keys:
                if a == b:
                    m[a][b] = diag
                else:
                    vals = d[a][b]
                    m[a][b] = _f(np.mean(vals)) if vals else None
        return m

    return {
        "models": model_keys,
        "action_concordance": _mat(conc, 1.0),
        "weight_correlation": _mat(wcorr, 1.0),
        "n_shared_ticks": {a: {b: nshared[a][b] for b in model_keys} for a in model_keys},
        "methodology_ref": ("RQ1 join: data_inputs_hash + raw_decisions, >=3 shared "
                            "tickers; action_concordance = 3-way BUY/SELL/HOLD exact-match "
                            "rate; weight_correlation = Pearson r of target_weight vectors. "
                            "The RQ1 headline scalar is the OBSERVATION-WEIGHTED pooled mean of "
                            "the off-diagonal cells (each cell weighted by its n_shared_ticks); "
                            "the unweighted mean of the cells differs and is not the headline. "
                            "Thin cells (low n_shared_ticks, e.g. Gemini pairs reduced by its "
                            "open-bell failures) should be read with their n_shared_ticks."),
    }


# ==========================================================================
# RQ2 / RQ3 calendar-month point estimates (canonical helpers, full replay,
# event attribution by sale-date / exit-date in the window)
# ==========================================================================

def _rq2_month(full_records, model_keys, win_start, win_end, n_resamples):
    """Disposition (PGR-PLR) over sale records DATED in the window.

    Full-history avg-cost replay (_replay_avg_cost) for correct realized sign;
    events filtered to the window; pooled + per-model via the canonical _pgr_plr
    and _bootstrap_index_ci (exactly compute_rq2's internal pipeline)."""
    def _inwin(d):
        return bool(d) and win_start <= d <= win_end

    per_model = {}
    pooled = []
    for key in model_keys:
        ev = [e for e in _replay_avg_cost(full_records.get(key, [])) if _inwin(e["date"])]
        pooled.extend(ev)
        pgr, plr, RG, RL, PG, PL = _pgr_plr(ev)
        entry = {
            "n_sale_records": len(ev),
            "realized_gains": RG, "realized_losses": RL,
            "paper_gains": PG, "paper_losses": PL,
            "PGR": _f(pgr), "PLR": _f(plr),
            "disposition_difference": _f(pgr - plr) if (pgr is not None and plr is not None) else None,
            "disposition_ratio": _f(pgr / plr) if (pgr is not None and plr is not None and plr > 0) else None,
            "ci_difference": None, "p_value": None,
        }
        if len(ev) >= 5 and entry["disposition_difference"] is not None:
            boot = _bootstrap_index_ci(
                len(ev),
                lambda idx, _e=ev: (lambda r: (r[0] - r[1]) if (r[0] is not None and r[1] is not None) else 0.0)(_pgr_plr(_e, idx)[:2]),
                n_resamples=n_resamples)
            entry["ci_difference"] = {"low": _f(boot["ci_low"]), "high": _f(boot["ci_high"])}
            entry["p_value"] = _f(boot["p_value"])
        per_model[key] = entry

    pgr, plr, RG, RL, PG, PL = _pgr_plr(pooled)
    pooled_block = {
        "n_sale_records": len(pooled),
        "realized_gains": RG, "realized_losses": RL, "paper_gains": PG, "paper_losses": PL,
        "PGR": _f(pgr), "PLR": _f(plr),
        "disposition_difference": _f(pgr - plr) if (pgr is not None and plr is not None) else None,
        "disposition_ratio": _f(pgr / plr) if (pgr is not None and plr is not None and plr > 0) else None,
        "ci_difference": None, "p_value": None,
    }
    if len(pooled) >= 5 and pooled_block["disposition_difference"] is not None:
        boot = _bootstrap_index_ci(
            len(pooled),
            lambda idx, _e=pooled: (lambda r: (r[0] - r[1]) if (r[0] is not None and r[1] is not None) else 0.0)(_pgr_plr(_e, idx)[:2]),
            n_resamples=n_resamples)
        pooled_block["ci_difference"] = {"low": _f(boot["ci_low"]), "high": _f(boot["ci_high"])}
        pooled_block["p_value"] = _f(boot["p_value"])
    return {"pooled": pooled_block, "per_model": per_model}


def _rq3_month(full_records, model_keys, win_start, win_end, n_resamples):
    """Confidence calibration over trades CLOSED (full exit) in the window.

    Full-history replay (_closed_trades) for correct entry confidence / cost
    basis; trades filtered to exit_date in window; calibration via the canonical
    _calibration_stats; correlation CI via _bootstrap_index_ci (compute_rq3's
    pipeline)."""
    def _inwin(d):
        return bool(d) and win_start <= d <= win_end

    per_model = {}
    pooled = []
    for key in model_keys:
        closed = [t for t in _closed_trades(full_records.get(key, [])) if _inwin(t.get("exit_date", ""))]
        pooled.extend(closed)
        stats = _calibration_stats(closed)
        n = stats.get("n", 0)
        entry = {
            "n_closed_trades": len(closed),
            "n_usable_confidence": n,
            "min_required": MIN_CLOSED_TRADES_RQ3,
            "sufficient": n >= MIN_CLOSED_TRADES_RQ3,
            "confidence_outcome_corr": _f(stats.get("confidence_outcome_corr")),
            "brier_score": _f(stats.get("brier_score")),
            "expected_calibration_error": _f(stats.get("expected_calibration_error")),
            "overall_hit_rate": _f(stats.get("overall_hit_rate")),
            "corr_ci": None, "p_value": None,
        }
        usable = [t for t in closed if t.get("entry_confidence") is not None]
        if len(usable) >= 5:
            confs = np.array([t["entry_confidence"] for t in usable], dtype=float)
            outs = np.array([t["profitable"] for t in usable], dtype=float)

            def _corr(idx, _c=confs, _o=outs):
                c, o = _c[idx], _o[idx]
                if c.std() == 0 or o.std() == 0:
                    return 0.0
                return float(np.corrcoef(c, o)[0, 1])

            boot = _bootstrap_index_ci(len(usable), _corr, n_resamples=n_resamples)
            entry["corr_ci"] = {"low": _f(boot["ci_low"]), "high": _f(boot["ci_high"])}
            entry["p_value"] = _f(boot["p_value"])
        per_model[key] = entry

    pstats = _calibration_stats(pooled)
    pn = pstats.get("n", 0)
    pooled_block = {
        "n_closed_trades": len(pooled),
        "n_usable_confidence": pn,
        "confidence_outcome_corr": _f(pstats.get("confidence_outcome_corr")),
        "brier_score": _f(pstats.get("brier_score")),
        "expected_calibration_error": _f(pstats.get("expected_calibration_error")),
        "overall_hit_rate": _f(pstats.get("overall_hit_rate")),
        "calibration_curve": pstats.get("buckets"),
        "corr_ci": None, "p_value": None,
    }
    usable = [t for t in pooled if t.get("entry_confidence") is not None]
    if len(usable) >= 5:
        confs = np.array([t["entry_confidence"] for t in usable], dtype=float)
        outs = np.array([t["profitable"] for t in usable], dtype=float)

        def _corr(idx, _c=confs, _o=outs):
            c, o = _c[idx], _o[idx]
            if c.std() == 0 or o.std() == 0:
                return 0.0
            return float(np.corrcoef(c, o)[0, 1])

        boot = _bootstrap_index_ci(len(usable), _corr, n_resamples=n_resamples)
        pooled_block["corr_ci"] = {"low": _f(boot["ci_low"]), "high": _f(boot["ci_high"])}
        pooled_block["p_value"] = _f(boot["p_value"])
    return {"pooled": pooled_block, "per_model": per_model}


# ==========================================================================
# Behavioral evidence metrics (calendar month) via canonical helpers
# ==========================================================================

def _behavioral_evidence(full_records, model_keys, win_start, win_end):
    """Per-model May behavioral evidence: trade activity, reversal/flip rate,
    holding period, and the four RQ5 daily descriptive metrics averaged over the
    month (HHI/concentration, turnover, avg position size, cash). All via the
    canonical research_metrics helpers."""
    def _inwin(d):
        return bool(d) and win_start <= d <= win_end

    out = {}
    for key in model_keys:
        recs = full_records.get(key, [])
        win = [r for r in recs if _inwin(r.get("date", ""))]
        active_days = len({r["date"] for r in win if r.get("date")})
        n_buys = n_sells = 0
        notional = 0.0
        for r in win:
            for ex in _executed_trades(r):
                if ex["side"] == "BUY":
                    n_buys += 1
                else:
                    n_sells += 1
                notional += abs(float(ex.get("notional") or 0.0))
        n_trades = n_buys + n_sells

        # reversal/flip rate: in-window trade reverses prior executed side on
        # same ticker (full-history prior-side tracking; behavioral_summary def).
        last_side = {}
        flips = pairs = 0
        for r in recs:
            d = r.get("date", "")
            for ex in _executed_trades(r):
                t, side = ex["ticker"], ex["side"]
                if t in last_side and _inwin(d):
                    pairs += 1
                    if side != last_side[t]:
                        flips += 1
                last_side[t] = side
        flip_rate = (flips / pairs) if pairs else None

        # holding period: trades closed (full exit) in window, full replay for
        # correct entry date.
        closed_win = [t for t in _closed_trades(recs) if _inwin(t.get("exit_date", ""))]
        holds = []
        same_day = 0
        for t in closed_win:
            try:
                ed = datetime.strptime(t["entry_date"], "%Y-%m-%d")
                xd = datetime.strptime(t["exit_date"], "%Y-%m-%d")
                h = (xd - ed).days
                holds.append(h)
                if h == 0:
                    same_day += 1
            except (ValueError, KeyError):
                pass
        avg_hold = _f(np.mean(holds)) if holds else None
        med_hold = _f(np.median(holds)) if holds else None

        # RQ5 daily descriptive metrics over the window (canonical helper).
        feats = _daily_portfolio_features(win)
        fdates = sorted(feats.keys())
        mean_hhi = _f(np.mean([feats[d]["hhi"] for d in fdates])) if fdates else None
        mean_turnover = _f(np.mean([feats[d]["turnover"] for d in fdates])) if fdates else None
        mean_cash = _f(np.mean([feats[d]["cash_pct"] for d in fdates])) if fdates else None
        mean_avg_pos = _f(np.mean([feats[d]["avg_position_size"] for d in fdates])) if fdates else None

        out[key] = {
            "active_trading_days": active_days,
            "trade_count": n_trades,
            "buy_count": n_buys,
            "sell_count": n_sells,
            "trades_per_active_day": _f(n_trades / active_days) if active_days else None,
            "executed_notional_total": _f(notional),
            "reversal_flip_rate": _f(flip_rate),
            "n_flips": flips,
            "n_reversal_pairs": pairs,
            "n_closed_trades": len(closed_win),
            "avg_hold_days": avg_hold,
            "median_hold_days": med_hold,
            "same_day_exits": same_day,
            "mean_daily_hhi_concentration": mean_hhi,
            "mean_daily_turnover": mean_turnover,
            "mean_daily_cash_pct": mean_cash,
            "mean_daily_avg_position_size": mean_avg_pos,
        }
    return out


def _err_type(msg):
    """Objective classification of an API/JSON error string."""
    m = (msg or "").lower()
    if "json" in m:
        return "json_parse_error"
    if "429" in m or "quota" in m or "rate limit" in m or "rate-limit" in m:
        return "rate_limit"
    if "504" in m or "deadline" in m or "timeout" in m:
        return "deadline_timeout"
    if "499" in m or "cancel" in m:
        return "cancelled"
    return "api_error"


def _notable_events(full_records, model_keys, win_start, win_end):
    """Objective per-model event extraction over the calendar month.

    No notability judgment / no narrative — only objectively-defined events:
      * top 3 executed trades by absolute USD value  {ticker, action, value, date}
      * every position stop-loss force-sell          {ticker, date, trigger}
      * every portfolio drawdown-halt trigger         {date, level}
      * every API/JSON error event                    {timestamp, type}
    Empty arrays where none occurred. Fixed field, every model, every month."""
    def _inwin(d):
        return bool(d) and win_start <= d <= win_end

    out = {}
    for key in model_keys:
        recs = full_records.get(key, [])
        win = [r for r in recs if _inwin(r.get("date", ""))]

        trades = []
        stop_loss = []
        halts = []
        errors = []
        prev_halted = False
        for r in win:
            # API / JSON error events
            if not r.get("api_success"):
                errors.append({"timestamp": r.get("timestamp"),
                               "type": _err_type(r.get("api_error"))})
            # trades + stop-loss force-sells
            for ex in (r.get("executions") or []):
                if not ex.get("executed") or ex.get("side") not in ("BUY", "SELL"):
                    continue
                notional = abs(float(ex.get("notional") or 0.0))
                trades.append({"ticker": ex.get("ticker"), "action": ex.get("side"),
                               "value": _f(round(notional, 2)), "date": r.get("date")})
                if str(ex.get("order_id", "")).startswith("FORCED"):
                    stop_loss.append({"ticker": ex.get("ticker"), "date": r.get("date"),
                                      "trigger": "position_stop_loss"})
            # drawdown-halt trigger: portfolio_after.halted flips False -> True
            pa = r.get("portfolio_after") or {}
            halted_now = bool(pa.get("halted"))
            if halted_now and not prev_halted:
                halts.append({"date": r.get("date"),
                              "level": _f((pa.get("cumulative_return"))) })
            prev_halted = halted_now

        trades.sort(key=lambda t: -(t["value"] or 0.0))
        out[key] = {
            "top_trades_by_value": trades[:3],
            "stop_loss_triggers": stop_loss,
            "drawdown_halt_triggers": halts,
            "api_json_errors": errors,
        }
    return out


# ==========================================================================
# Data integrity (calendar month)
# ==========================================================================

def _data_integrity(model_keys, win_start, win_end, settings):
    """Per-model failure rates, missing-tick count, and the known incidents."""
    import re
    from collections import Counter

    def _inwin(d):
        return bool(d) and win_start <= d <= win_end

    month_tag = win_start[:7]
    per_model_fail = {}
    per_model_records = {}
    tick_hashes_by_date = defaultdict(set)
    gemini_modes = Counter()
    for key in model_keys:
        fp = TRADES_DIR / f"{key}_{month_tag}.jsonl"
        n = succ = fail = 0
        if fp.exists():
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not _inwin(r.get("date", "")):
                        continue
                    n += 1
                    if r.get("api_success"):
                        succ += 1
                    else:
                        fail += 1
                        if key == "gemini":
                            err = (r.get("api_error") or "")[:40]
                            gemini_modes[err] += 1
                    h = r.get("data_inputs_hash")
                    if h:
                        tick_hashes_by_date[r.get("date")].add(h)
        per_model_records[key] = n
        per_model_fail[key] = {
            "records": n, "api_success": succ, "api_failures": fail,
            "failure_rate": _f(fail / n) if n else None,
        }

    # missing ticks: the modal ticks/day is the healthy cadence; deficits are
    # missing decision periods (uniform across models => systemic outage).
    per_date_ticks = {d: len(h) for d, h in tick_hashes_by_date.items()}
    if per_date_ticks:
        from statistics import mode
        try:
            modal = mode(list(per_date_ticks.values()))
        except Exception:
            modal = max(per_date_ticks.values())
    else:
        modal = 0
    missing_by_date = {d: modal - c for d, c in per_date_ticks.items() if c < modal}
    missing_tick_count = sum(missing_by_date.values())

    incidents = [
        {
            "id": "may26_silent_outage",
            "date": "2026-05-26",
            "type": "missing_ticks",
            "scope": "all_models (systemic)",
            "detail": (f"Silent scheduler outage: {modal - per_date_ticks.get('2026-05-26', modal)} "
                       "decision periods missing on 2026-05-26 (10 ticks logged vs the "
                       f"{modal}-tick healthy cadence), uniform across all six models. Gap spans "
                       "the ~15:0x-16:0x UTC (~11:00-12:00 ET) window."),
        },
        {
            "id": "may27_gemini_json_failure",
            "date": "2026-05-27",
            "type": "response_parse_failure",
            "scope": "gemini",
            "detail": ("Two Gemini ticks on 2026-05-27 (17:04 and 18:35 UTC) failed with "
                       "'Model response was not valid JSON even after repair: Unterminated "
                       "string' — counted in Gemini's api_failures; no decision recorded."),
        },
        {
            "id": "may27_cancelled_run_noise",
            "date": "2026-05-27",
            "type": "operational_noise",
            "scope": "pipeline",
            "detail": ("Cancelled-run noise on 2026-05-27 did NOT corrupt the committed logs: "
                       "no duplicate (date,timestamp) records were found in any model's May "
                       "JSONL. Related Gemini '499 operation was cancelled' API errors "
                       "(9 in May) are folded into Gemini's failure-mode breakdown."),
        },
        {
            "id": "gemini_high_failure_rate",
            "date": "2026-05 (month)",
            "type": "elevated_api_failure_rate",
            "scope": "gemini",
            "detail": ("Gemini May api-failure rate is elevated (open-bell congestion). "
                       "Failure-mode mix this month: " +
                       ", ".join(f"{v}x {k}" for k, v in gemini_modes.most_common()) +
                       ". Missingness is MAR|tick-position (monotonic tick-position gradient, "
                       "market-state independent r=-0.12); reduces power for every Gemini "
                       "estimate and is handled by tick-position conditioning per "
                       "docs/Gemini-selection-bias-characterization-and-per-RQ-handling.md."),
        },
        {
            "id": "deepseek_midmonth_repoint",
            "date": "2026-05-21",
            "type": "model_snapshot_change",
            "scope": "deepseek",
            "detail": ("DeepSeek floating alias repointed mid-month: model_id_returned changed "
                       "deepseek-v4-flash -> deepseek-v4-pro on 2026-05-21 (deliberate: "
                       "v4-flash is sub-frontier; v4-pro restores cohort parity). May DeepSeek "
                       "data spans two snapshots; recorded in report_meta.pinned_snapshots."),
        },
    ]

    return {
        "incidents": incidents,
        "per_model_failure_rate": per_model_fail,
        "missing_tick_count": missing_tick_count,
        "missing_ticks_by_date": missing_by_date,
        "healthy_ticks_per_day_modal": modal,
        "notes": ("Failure rate = api_failures / records over the calendar month. A failed "
                  "tick still writes a record (no decision). Missing ticks are decision "
                  "periods with zero records for any model (systemic), distinct from per-model "
                  "api failures. The 2026-04-09-22 Anthropic state-file commingling defect is "
                  "pre-May and excluded by the >=2026-04-23 pilot-window rule; it does not "
                  "affect May data."),
    }


# ==========================================================================
# Pinned model snapshots (read from May model_id_returned + settings)
# ==========================================================================

def _pinned_snapshots(model_keys, win_start, win_end, settings):
    month_tag = win_start[:7]

    def _inwin(d):
        return bool(d) and win_start <= d <= win_end

    out = {}
    for key in model_keys:
        fp = TRADES_DIR / f"{key}_{month_tag}.jsonl"
        seen = OrderedDict()  # snapshot id -> [first_date, last_date, count]
        if fp.exists():
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not (_inwin(r.get("date", "")) and r.get("api_success")):
                        continue
                    mid = r.get("model_id_returned")
                    d = r.get("date")
                    if mid not in seen:
                        seen[mid] = [d, d, 0]
                    seen[mid][1] = d
                    seen[mid][2] += 1
        cfg = settings["models"].get(key, {})
        snaps = [{"snapshot_id": mid, "first_date": v[0], "last_date": v[1], "n_success": v[2]}
                 for mid, v in seen.items()]
        out[key] = {
            "display_name": cfg.get("display_name", key),
            "provider": cfg.get("provider"),
            "configured_model": cfg.get("model"),
            "cohort": cfg.get("cohort", "core"),
            "may_snapshots": snaps,
            "snapshot_stable": len(snaps) <= 1,
        }
    return out


# ==========================================================================
# Native integrity / provenance emission (Phase-A ledger + computed gates).
# This encodes the framework ratified at the May schema-close
# (scripts/backfill_may_data_layer.py) as a BUILD: persistent facts come from
# phase_a_integrity_ledger.json; per-month facts (completeness gate, clean-window
# returns, the passing cohort) are COMPUTED here, never hand-walked.
# ==========================================================================

def _decision_completeness(layer):
    """api_success / records per model, from the integrity block (Gemini ~0.57).
    Identical definition to scripts/backfill_may_data_layer._decision_completeness."""
    fr = layer["methodology_data_integrity_rq"]["data_integrity"]["per_model_failure_rate"]
    out = {}
    for k, v in fr.items():
        rec = v.get("records") or 0
        out[k] = round(v["api_success"] / rec, 4) if rec else None
    return out


def _evaluate_gates(model_keys, decision_completeness, clean_results, ledger):
    """Apply the three-gate inclusion framework per model and derive the full
    data-confidence taxonomy.

    Carried gate failures (uncorrupted_book, model_identity_stable) come from the
    ledger's persistent_model_status; the completeness gate is COMPUTED from this
    month's decision logs (it varies month to month — e.g. Gemini fails it in May
    with an otherwise-clean book). The clean-window source/value/trust come from
    the committed de-fab overhang correction (clean_results)."""
    comp_min = ledger["inclusion_gates"]["completeness_min"]
    pms = ledger.get("persistent_model_status", {})
    book_fail = {m for m, s in pms.items() if s.get("failed_gate") == "uncorrupted_book"}
    identity_fail = {m for m, s in pms.items() if s.get("failed_gate") == "model_identity_stable"}

    out = {}
    for mk in model_keys:
        comp = decision_completeness.get(mk)
        gate_completeness = comp is not None and comp >= comp_min
        gate_book = mk not in book_fail
        gate_identity = mk not in identity_fail
        model_spliced = mk in identity_fail
        all_pass = gate_completeness and gate_book and gate_identity

        # ---- clean-window figure from the committed de-fab overhang correction ----
        cr = clean_results.get(mk, {})
        trust = cr.get("trust")
        delta_pp = cr.get("delta_pp")
        defab_ret = cr.get("defab_clean_return")
        if trust == "not_reliably_estimable" or defab_ret is None:
            clean_source = "not_estimable"
            clean_value = None
            clean_confidence = "not_reliably_estimable"
        else:
            # source label: de-fab CONFIRMS raw within rounding (|delta|<0.05pp) -> "raw";
            # otherwise a real bounded correction was applied -> "defab". The emitted
            # VALUE is always the de-fab figure (round 4); the label conveys trust basis.
            clean_source = "raw" if abs(delta_pp) < 0.05 else "defab"
            clean_value = round(defab_ret, 4)
            clean_confidence = "estimable"

        # ---- taxonomy (gate priority: book > identity > completeness) ----
        if not gate_book:
            data_confidence = "non_estimable_corrupt_book"
            profile_status = "non_estimable"
        elif not gate_identity:
            data_confidence = "exploratory_model_splice"
            profile_status = "provisional_model_splice"
        elif not gate_completeness:
            data_confidence = "exploratory_completeness"
            profile_status = "performance_only"
        else:
            data_confidence = "estimable"
            profile_status = "estimable_primary"

        # ---- risk_basis: gates certify the POINT return, not the daily-risk path.
        # A de-fab-corrected series is basis-sensitive for Sharpe/vol/DD even when
        # the point return is estimable (gate_scope_refinement). ----
        if clean_source == "not_estimable":
            risk_basis = "non_estimable"
        elif model_spliced:
            risk_basis = "spliced"
        elif clean_source == "defab":
            risk_basis = "raw_basis_sensitive"
        else:  # clean_source == "raw"
            risk_basis = "raw"

        out[mk] = {
            "gate_completeness": gate_completeness,
            "gate_uncorrupted_book": gate_book,
            "gate_model_identity_stable": gate_identity,
            "all_pass": all_pass,
            "model_spliced": model_spliced,
            "clean_source": clean_source,
            "clean_value": clean_value,
            "clean_confidence": clean_confidence,
            "data_confidence": data_confidence,
            "profile_status": profile_status,
            "rq_trust_flag": data_confidence,  # same four-tier taxonomy
            "risk_basis": risk_basis,
        }
    return out


def _rq1_reason(gate):
    """The single-word RQ1 exclusion reason from a model's gate result."""
    if not gate["gate_uncorrupted_book"]:
        return "corrupt_book"
    if not gate["gate_model_identity_stable"]:
        return "model_splice"
    if not gate["gate_completeness"]:
        return "completeness"
    return None


def _apply_integrity(layer, ledger, gates, model_keys, win_start, win_end, month,
                     spy_clean_value, spy_descriptive_sharpe):
    """Emit the integrity / provenance layer onto the freshly-built non-integrity
    layer. Computes nothing about returns/behavior (those are the base build);
    only ADDS the integrity framework — gate taxonomy, clean-window figures, the
    persistent caveats from the ledger, the RQ supersession, and provenance.

    Mirrors the field set ratified in scripts/backfill_may_data_layer.apply_backfill,
    but every per-model value is COMPUTED (gates/clean-window) or read from the
    ledger (persistent facts), not hand-coded."""
    rm = layer["report_meta"]
    rf = ledger["risk_free_rate"]

    # ---- report_meta ----
    rm["schema_version"] = "1.0"
    rm["risk_free_rate"] = {"value": rf["value"], "as_of": rf["as_of"]}
    # rf as-of correction (inception-pinned), preserving the rest of the note text.
    if isinstance(rm.get("risk_free_rate_note"), str) and "as of 2026-06-01" in rm["risk_free_rate_note"]:
        rm["risk_free_rate_note"] = rm["risk_free_rate_note"].replace(
            "as of 2026-06-01", f"as of {rf['as_of']} (inception-pinned)")
    mn = rm.get("methodology_notes", {})
    if isinstance(mn.get("risk_free_rate"), str) and "source date 2026-06-01" in mn["risk_free_rate"]:
        mn["risk_free_rate"] = mn["risk_free_rate"].replace(
            "source date 2026-06-01", f"as-of inception {rf['as_of']}")

    # DeepSeek snapshot first-date correction: only when the model identity
    # spliced WITHIN this month (multi-leg). Correct each leg's first_date to the
    # canonical transition boundary from the ledger (config-repoint date), which
    # the month-local log scan cannot see across the month boundary.
    splice = ledger["integrity_events"]["deepseek_identity_splice"]
    ds_snap = rm.get("pinned_snapshots", {}).get("deepseek", {})
    legs = ds_snap.get("may_snapshots") or ds_snap.get("snapshots") or []
    if len(legs) > 1:
        boundary = {f"deepseek-{t['to']}": t["date"] for t in splice["transitions"]}
        for leg in legs:
            if leg.get("snapshot_id") in boundary:
                leg["first_date"] = boundary[leg["snapshot_id"]]

    rm["spy_benchmark"]["descriptive_sharpe"] = spy_descriptive_sharpe

    # ---- leaderboard: rename + clean/confidence + SPY row ----
    spy_cum_inception = rm["spy_benchmark"].get("cumulative_return_since_inception")
    spy_month = rm["spy_benchmark"].get("monthly_return")
    for row in layer["leaderboard"]:
        if "cumulative_return" in row and "cumulative_return_inception" not in row:
            row["cumulative_return_inception"] = row.pop("cumulative_return")
        mk = row["model"]
        g = gates.get(mk)
        if g:
            row["cumulative_return_clean"] = {
                "value": g["clean_value"], "source": g["clean_source"],
                "confidence": g["clean_confidence"], "model_spliced": g["model_spliced"],
            }
            row["data_confidence"] = g["data_confidence"]
    layer["leaderboard"].append({
        "model": "spy_benchmark", "rank": None,
        "monthly_return": spy_month,
        "cumulative_return_inception": spy_cum_inception,
        "cumulative_return_clean": {"value": spy_clean_value, "source": "benchmark",
                                    "confidence": "benchmark", "model_spliced": False},
        "spy_relative_alpha": 0.0,
        "data_confidence": "benchmark",
    })

    # ---- performance: risk_basis + SPY row ----
    for mk in model_keys:
        p = layer["performance"].get(mk)
        if p is not None and gates.get(mk):
            p["risk_basis"] = gates[mk]["risk_basis"]
    layer["performance"]["spy_benchmark"] = {
        "monthly_return": spy_month, "max_drawdown": None, "volatility": None,
        "sharpe": spy_descriptive_sharpe, "trade_count": None, "win_rate": None,
        "turnover": None, "avg_hold_days": None, "risk_basis": "benchmark",
    }

    # ---- charts: shakedown-fabrication flag (re-anchored Phase-A invariant) ----
    # True iff the displayed equity curve spans the launch shakedown window (the
    # 4/9-4/22 fabricated segment). Computed from the actual curve span. Phase-A
    # equity curves re-anchor at clean_window_start, so a correctly built curve
    # never reaches back to/before shake_end and this is ALWAYS False. It is
    # retained as a standing tripwire: a True value means the curve reverted to
    # carrying the excluded window, which _self_validate treats as a halting
    # regression (the layer is not emitted).
    shake_end = ledger["shakedown_window"]["end"]
    curve_dates = sorted({pt["date"] for s in layer["charts"]["equity_curve"]["series"].values()
                          for pt in s})
    layer["charts"]["equity_curve_carries_shakedown_fabrication"] = bool(
        curve_dates and curve_dates[0] <= shake_end <= curve_dates[-1])

    # ---- cross_model_behavioral: restructure rq1 + episode register ----
    cmb = layer["cross_model_behavioral"]
    if "rq1_herding_point_estimate" in cmb and "rq1" not in cmb:
        numeric = cmb.pop("rq1_herding_point_estimate")
        numeric["reasons"] = {mk: _rq1_reason(gates[mk]) for mk in model_keys
                              if gates.get(mk) and _rq1_reason(gates[mk])}
        cmb["rq1"] = {
            "primary": {"status": "not_estimable", "reason": "single_pair_cohort",
                        "deferred_to": "phase_b"},
            "exploratory_pairwise": numeric,
        }
    cmb.setdefault("cross_model_episode_register", [])

    # ---- profiles: status, decision_completeness, suppress non-estimable ----
    dc = _decision_completeness(layer)
    for prof in layer["profiles"]:
        mk = prof["model"]
        g = gates.get(mk, {})
        prof["status"] = g.get("profile_status")
        prof["decision_completeness"] = dc.get(mk)
        if g.get("profile_status") == "non_estimable":
            em = prof.get("evidence_metrics", {})
            for f in ("rq2_disposition_difference", "rq3_confidence_outcome_corr"):
                if em.get(f) is not None:
                    em[f] = None
            # corrupt-book deployment/cash characterizations are uncertifiable -> null
            for f in ("style_tag", "risk_posture_tag", "strengths", "weaknesses"):
                prof[f] = None

    # ---- methodology_data_integrity_rq ----
    mdr = layer["methodology_data_integrity_rq"]
    lw = ledger["integrity_events"]["launch_window_corruption"]
    clean_window_figures_source = {mk: gates[mk]["clean_source"] for mk in model_keys if gates.get(mk)}
    mdr["known_caveats"] = {
        "state_integrity": {
            "bugs": lw["bugs"],
            "per_model_fabrication": lw["per_model_fabrication"],
            "audit": lw["audit"],
            "persistence": lw["persistence"],
            "clean_window_figures_source": clean_window_figures_source,
        },
        "model_identity": {
            "deepseek_model_splice": {
                "alias": splice["alias"],
                "transitions": splice["transitions"],
                "off_spec_window": splice["off_spec_window"],
                "detection": splice["detection"],
                "performance_status": splice["performance_status"],
                "decision_based_status": splice["decision_based_status"],
                "first_success_note": splice["first_success_note"],
            },
        },
    }
    # inclusion_gates: definitions from the ledger; passing cohort COMPUTED this
    # month. The cohort lives under the STABLE key `passing` (fixed schema: the
    # quarterly aggregator and every downstream consumer read one key, never a
    # month-built one). Month identity stays in report_meta.period.
    mdr["inclusion_gates"] = {
        "completeness_min": ledger["inclusion_gates"]["completeness_min"],
        "uncorrupted_book": True,
        "model_identity_stable": True,
        "passing": [mk for mk in model_keys if gates.get(mk, {}).get("all_pass")],
    }
    mdr["gate_scope_refinement"] = ledger["inclusion_gates"]["gate_scope_refinement"]

    # RQ point-estimate reclassification: RQ1 not estimable as primary (single-pair
    # cohort); RQ2/RQ3 six-model pools SUPERSEDED (they pool non-estimable + exploratory
    # models with the estimable cohort) -> per-model GPT/Grok reported primary.
    pe = mdr["rq_update"]["point_estimates"]
    if "RQ1" in pe:
        pe["RQ1"]["status"] = "not_estimable"
    rq_estimable = [mk for mk in model_keys
                    if gates.get(mk, {}).get("rq_trust_flag") == "estimable"]
    # exploratory / non-estimable enumeration for the supersession prose, in
    # model_keys order, with each model's exclusion reason derived from its gate.
    nonest = [mk for mk in model_keys
              if gates.get(mk, {}).get("profile_status") == "non_estimable"]
    _EXPL_REASON = {"performance_only": "completeness", "provisional_model_splice": "model-splice"}
    explor = [(mk, _EXPL_REASON[gates[mk]["profile_status"]]) for mk in model_keys
              if gates.get(mk, {}).get("profile_status") in _EXPL_REASON]
    _DISPLAY = {"gpt": "GPT", "grok": "Grok", "gemini": "Gemini",
                "deepseek": "DeepSeek", "claude": "Claude Sonnet", "claude_opus": "Claude Opus"}
    _COUNT = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
    est_and = " and ".join(_DISPLAY.get(m, m) for m in rq_estimable)        # "GPT and Grok"
    est_slash = "/".join(_DISPLAY.get(m, m) for m in rq_estimable)          # "GPT/Grok"
    est_refs = " and ".join(f"per_model.{m}" for m in rq_estimable)         # "per_model.gpt and per_model.grok"
    nonest_str = ", ".join(nonest)                                          # "claude, claude_opus"
    explor_str = "; ".join(f"{mk}: {why}" for mk, why in explor)            # "gemini: completeness; deepseek: model-splice"
    POOL_META = {
        "RQ2": {"metric": "disposition_difference", "pool_field": "pooled_disposition_difference",
                "n_field": "n_sale_records", "pool_phrase": "disposition difference",
                "primary_phrase": "disposition differences"},
        "RQ3": {"metric": "confidence_outcome_corr", "pool_field": "pooled_confidence_outcome_corr",
                "n_field": "n_closed_trades", "pool_phrase": "confidence-outcome correlation",
                "primary_phrase": "confidence-outcome correlations"},
    }
    for rq, meta in POOL_META.items():
        node = pe.get(rq)
        if node is None:
            continue
        node["status"] = "superseded_six_model_pool"
        node["reported"] = False
        node["reported_primary"] = {
            "basis": "per_model",
            "cohort": rq_estimable,
            "status": "estimable",
            "metric": meta["metric"],
            "value_ref": ", ".join(f"per_model.{mk}.{meta['metric']}" for mk in rq_estimable),
            "note": (
                f"Reported primary for {rq} is the per-model {est_and} {meta['primary_phrase']} -- "
                f"the {_COUNT.get(len(rq_estimable), len(rq_estimable))} models that clear the "
                "three-gate inclusion framework (uncorrupted book, stable model identity, "
                "completeness >= 0.80) and are each individually estimable. Authoritative values "
                f"live in {est_refs} and are unchanged."
            ),
        }
        node["supersession_note"] = (
            f"The six-model pooled {meta['pool_phrase']} ({meta['pool_field']}, "
            f"{meta['n_field']}={node.get(meta['n_field'])}) pools non-estimable models "
            f"({nonest_str}: corrupt book) and exploratory models ({explor_str}) together with "
            f"the estimable {est_slash} cohort, which contradicts the inclusion framework. It is "
            f"superseded by the per-model {est_slash} reported primary and is retained for "
            "provenance only -- not reported."
        )
        for mk, pm in node.get("per_model", {}).items():
            if gates.get(mk):
                pm["trust_flag"] = gates[mk]["rq_trust_flag"]

    # ---- provenance / source_commit (the one inherently two-step field) ----
    rm["source_commit"] = None  # reconcile after commit (see note)
    rm["provenance"] = {
        "generated_at_commit": _source_commit(),  # HEAD at build (build-time)
        "integrity_refs": {
            "ledger": "scripts/phase_a_integrity_ledger.json",
            "defab_overhang": "scripts/defab_clean_window.py",
            "schema": "docs/REPORT_SCHEMA.md",
        },
        "note": (
            "generated_at_commit is HEAD at build (build-time; on its own it predates this "
            "file and reproduces nothing). source_commit is null at emit: it must point at the "
            "commit that reproduces this report, which the builder cannot know before "
            "committing. MANDATORY RELEASE GATE -- a published monthly layer must NOT carry a "
            "null source_commit: after the data-layer commits, run "
            f"`git log -1 --format=%H -- reports/monthly/{month}/data_layer.json` and write that "
            "hash into report_meta.source_commit before the report is published. The integrity "
            "layer is emitted NATIVELY in this single build from integrity_refs (the Phase-A "
            "ledger + the committed de-fab overhang correction), superseding May's three-commit "
            "hand chain (generator -> integrity back-fill -> pool relabel)."
        ),
    }
    return layer


# ==========================================================================
# Self-validation: the builder errors out rather than emit a bad layer.
# ==========================================================================

def _schema_validate(layer):
    """Structural validation against docs/REPORT_SCHEMA.md: every required field
    present with the right type. Returns a list of issue strings (empty == pass)."""
    issues = []

    def req(cond, msg):
        if not cond:
            issues.append(msg)

    req(set(layer) >= {"report_meta", "leaderboard", "performance", "charts",
                       "cross_model_behavioral", "profiles", "methodology_data_integrity_rq"},
        "top-level keys missing")
    rm = layer.get("report_meta", {})
    req(rm.get("schema_version") == "1.0", "report_meta.schema_version != '1.0'")
    req(isinstance(rm.get("risk_free_rate"), dict) and "value" in rm["risk_free_rate"]
        and "as_of" in rm["risk_free_rate"], "report_meta.risk_free_rate not {value, as_of}")
    req(isinstance(rm.get("spy_benchmark"), dict)
        and rm["spy_benchmark"].get("descriptive_sharpe") is not None,
        "report_meta.spy_benchmark.descriptive_sharpe missing")
    prov = rm.get("provenance", {})
    req(isinstance(prov, dict) and "generated_at_commit" in prov, "report_meta.provenance.generated_at_commit missing")
    req("source_commit" in rm, "report_meta.source_commit missing (populate-or-null)")

    lb = {r.get("model"): r for r in layer.get("leaderboard", [])}
    req("spy_benchmark" in lb, "leaderboard missing spy_benchmark row")
    for mk, r in lb.items():
        req("cumulative_return" not in r, f"leaderboard[{mk}] still has old cumulative_return")
        req("cumulative_return_inception" in r, f"leaderboard[{mk}].cumulative_return_inception missing")
        cc = r.get("cumulative_return_clean")
        req(isinstance(cc, dict) and {"value", "source", "confidence", "model_spliced"} <= set(cc),
            f"leaderboard[{mk}].cumulative_return_clean malformed")
        req("data_confidence" in r, f"leaderboard[{mk}].data_confidence missing")

    perf = layer.get("performance", {})
    req("spy_benchmark" in perf, "performance missing spy_benchmark row")
    for mk, p in perf.items():
        if p is None:
            continue
        req("risk_basis" in p, f"performance[{mk}].risk_basis missing")

    req("equity_curve_carries_shakedown_fabrication" in layer.get("charts", {}),
        "charts.equity_curve_carries_shakedown_fabrication missing")
    cmb = layer.get("cross_model_behavioral", {})
    req("rq1_herding_point_estimate" not in cmb, "cross_model_behavioral.rq1 not restructured")
    req(cmb.get("rq1", {}).get("primary", {}).get("status") == "not_estimable",
        "cross_model_behavioral.rq1.primary.status missing")
    req("observed_action_concordance" in cmb.get("rq1", {}).get("exploratory_pairwise", {}),
        "cross_model_behavioral.rq1.exploratory_pairwise.observed_action_concordance missing")
    req("cross_model_episode_register" in cmb, "cross_model_episode_register missing")

    for prof in layer.get("profiles", []):
        mk = prof.get("model")
        for f in ("status", "decision_completeness", "evidence_metrics", "notable_events",
                  "style_tag", "risk_posture_tag", "strengths", "weaknesses"):
            req(f in prof, f"profiles[{mk}].{f} missing (populate-or-null)")
        req("data_caveat" not in prof, f"profiles[{mk}].data_caveat not dropped")

    mdr = layer.get("methodology_data_integrity_rq", {})
    for f in ("data_integrity", "rq_update", "known_caveats", "inclusion_gates", "gate_scope_refinement"):
        req(f in mdr, f"methodology_data_integrity_rq.{f} missing")
    ig = mdr.get("inclusion_gates", {})
    req(ig.get("completeness_min") is not None and isinstance(ig.get("passing"), list),
        "inclusion_gates malformed (completeness_min / passing)")
    pe = mdr.get("rq_update", {}).get("point_estimates", {})
    req(pe.get("RQ1", {}).get("status") == "not_estimable", "RQ1.status != not_estimable")
    for rq in ("RQ2", "RQ3"):
        n = pe.get(rq, {})
        req(n.get("status") == "superseded_six_model_pool", f"{rq}.status not superseded")
        req(n.get("reported") is False, f"{rq}.reported != false")
        req(n.get("reported_primary", {}).get("status") == "estimable", f"{rq}.reported_primary missing")
        req(not str(n.get("status", "")).startswith("estimable"),
            f"{rq} pool flagged estimable while pooling non-estimable models")
    return issues


def _rq_consistency_check(layer):
    """The read-only RQ-consistency cross-check, applied to any month's layer:
    the OBSERVATION-WEIGHTED off-diagonal action_concordance of the correlation
    matrix must equal the relocated RQ1 exploratory_pairwise concordance scalar.

    This is the identical reconciliation embedded in
    scripts/backfill_may_data_layer.verify (the definition-of-done). It is run
    UNCHANGED there against the reproduce-May candidate (Part 4); here it is
    applied generally so every monthly build is held to the same invariant.
    Returns (reconciles: bool, weighted, scalar)."""
    cm = layer["charts"]["correlation_matrix"]
    ac, ns, mods = cm["action_concordance"], cm["n_shared_ticks"], cm["models"]
    num = den = 0.0
    for a in mods:
        for b in mods:
            if a >= b:
                continue
            v, w = ac[a][b], ns[a][b]
            if v is not None and w:
                num += v * w
                den += w
    weighted = num / den if den else None
    scalar = layer["cross_model_behavioral"]["rq1"]["exploratory_pairwise"]["observed_action_concordance"]
    reconciles = weighted is not None and scalar is not None and abs(weighted - scalar) < 1e-9
    return reconciles, weighted, scalar


def _self_validate(layer):
    """Schema validation + the read-only RQ-consistency cross-check + SPY
    single-source invariant + the re-anchored equity-curve invariant. Returns
    (ok, report_lines). The builder errors out on any failure rather than emit a
    bad layer.

    NOTE: the full scripts/backfill_may_data_layer.verify carries May-specific
    literal asserts (SPY clean 0.0637, descriptive Sharpe 6.11) and post-authoring
    interpretive-field asserts — it is the May definition-of-done and is run
    UNCHANGED by the reproduce-May regression test, not here. This general
    per-build validator reuses verify's RQ-consistency reconciliation algorithm
    verbatim (_rq_consistency_check) so the same invariant holds every month."""
    schema_issues = _schema_validate(layer)

    reconciles, weighted, scalar = _rq_consistency_check(layer)
    rq_issue = [] if reconciles else [
        f"RQ-consistency FAIL: weighted off-diag mean {weighted} != scalar {scalar}"]

    # SPY descriptive-Sharpe single-source: the performance row and report_meta
    # must agree (general invariant, no month-specific literal).
    spy_perf = (layer["performance"].get("spy_benchmark") or {}).get("sharpe")
    spy_meta = (layer["report_meta"].get("spy_benchmark") or {}).get("descriptive_sharpe")
    spy_issue = [] if spy_perf == spy_meta else [
        f"SPY single-source FAIL: performance {spy_perf} != report_meta {spy_meta}"]

    # Re-anchored Phase-A invariant: the displayed equity curve must NOT carry the
    # launch/shakedown window. equity_curve_carries_shakedown_fabrication is
    # computed from the curve span; for a correctly re-anchored curve it is always
    # False. A True value is a regression (the curve reverted to spanning the
    # excluded 4/9-4/22 window) -> halt, do not emit.
    carries = (layer.get("charts") or {}).get("equity_curve_carries_shakedown_fabrication")
    anchor_issue = [] if carries is False else [
        f"equity-curve re-anchor INVARIANT FAIL: equity_curve_carries_shakedown_fabrication "
        f"is {carries!r} (must be False for a re-anchored Phase-A curve; the displayed curve "
        f"has reverted to carrying the excluded launch/shakedown window)"]

    lines = [
        f"  schema validation (REPORT_SCHEMA)                : {'PASS' if not schema_issues else 'FAIL'}",
        f"  RQ-consistency cross-check (matrix wmean==scalar): {'PASS' if reconciles else 'FAIL'}"
        + (f"  (w={weighted:.10f} s={scalar:.10f})" if weighted is not None and scalar is not None else ""),
        f"  SPY descriptive-Sharpe single-source             : {'PASS' if not spy_issue else 'FAIL'}",
        f"  equity-curve re-anchor invariant (no shakedown)  : {'PASS' if not anchor_issue else 'FAIL'}",
    ]
    all_issues = schema_issues + rq_issue + spy_issue + anchor_issue
    if all_issues:
        lines.append("  ISSUES:")
        lines += [f"    - {i}" for i in all_issues]
    return (len(all_issues) == 0), lines


# ==========================================================================
# Driver
# ==========================================================================

def build(month: str) -> dict:
    settings = load_settings()
    model_keys = get_model_keys(settings)
    risk_free_rate = settings.get("risk_free_rate")  # annualized; None => Sharpe stays null

    # Phase-A integrity ledger (carried facts). Loaded once at the top and reused
    # for both the equity-curve clean-window re-anchor and the native integrity
    # layer below. The clean-window anchor is read from the ledger, never hardcoded.
    ledger = _load_ledger()
    clean_window_start = ledger["clean_window_start"]        # 2026-04-23 (display start)
    clean_window_base = ledger["shakedown_window"]["end"]    # 2026-04-22 (index-base close)

    win_start, _y = f"{month}-01", month
    # last calendar day
    import calendar
    yy, mm = (int(x) for x in month.split("-"))
    win_end = f"{month}-{calendar.monthrange(yy, mm)[1]:02d}"

    logger.info("Loading full decision history for %d models...", len(model_keys))
    # MONTH-END FREEZE: a monthly report is a deterministic function of the data
    # available through its own month-end. Cap the full decision history at
    # win_end so the inception-anchored series, the RQ2/RQ3 replay, and the
    # accumulating RQ inputs are frozen at the report month and never drift as
    # later-month logs accrue. (Pre-window history is retained for cost-basis
    # replay; only post-month records are dropped.) Calendar-month windows are
    # unaffected — they already restrict to [win_start, win_end].
    full_records = {k: [r for r in load_decision_records(k) if r.get("date", "") <= win_end]
                    for k in model_keys}
    may_records = {k: [r for r in full_records[k] if win_start <= r.get("date", "") <= win_end]
                   for k in model_keys}

    # ---- regime map for the month (cache-only; no network refetch) ----
    try:
        # End at the SPY cache max so covers_end passes -> no network fetch;
        # label_for_dates forward-fills any later month-dates to the last regime.
        regime_df = classify_regimes(start="2025-12-01", end=win_end, use_cache=True)
        may_dates = sorted({r.get("date") for recs in may_records.values() for r in recs if r.get("date")})
        regime_map = label_for_dates(may_dates, regime_df=regime_df)
        regime_summary = summarize_regimes(regime_df)
    except Exception:
        logger.exception("Regime classification unavailable; proceeding unstratified")
        regime_map, regime_summary = {}, {"total_days": 0, "counts": {}}

    # ====================================================================
    # Inception-anchored series + leaderboard + performance
    # ====================================================================
    spy_series = _spy_eod_series(cap=win_end)
    spy_dates = [d for d, _ in spy_series]
    spy_vals = {d: v for d, v in spy_series}
    spy_inception = spy_vals.get(INCEPTION_DATE) or (spy_series[0][1] if spy_series else None)
    spy_last = spy_series[-1][1] if spy_series else None
    spy_cumulative = _f(spy_last / spy_inception - 1.0) if (spy_inception and spy_last) else None
    spy_may = [(d, v) for d, v in spy_series if win_start <= d <= win_end]
    spy_month_return = _f(spy_may[-1][1] / spy_may[0][1] - 1.0) if len(spy_may) >= 2 else None

    perf = {}
    equity_series = {}
    underwater_series = {}
    leaderboard_rows = []
    for key in model_keys:
        series = _eod_series(key, cap=win_end)
        if not series:
            perf[key] = None
            continue
        dates = [d for d, _ in series]
        vals = [v for _, v in series]
        cumulative = _f(vals[-1] / INCEPTION_CAPITAL - 1.0)
        may = [(d, v) for d, v in series if win_start <= d <= win_end]
        may_vals = [v for _, v in may]
        monthly_return = _f(may_vals[-1] / may_vals[0] - 1.0) if len(may_vals) >= 2 else None
        may_daily = list(np.diff(may_vals) / np.array(may_vals[:-1])) if len(may_vals) >= 2 else []
        mdd_may = _f(_max_drawdown(may_vals))

        # Displayed equity curve: re-anchored to the Phase-A clean window (excludes
        # the 4/9-4/22 launch/shakedown window; indexed off the 4/22 base close).
        # cumulative_return above stays inception-anchored and is unaffected.
        equity_series[key], _ = _reanchor_equity_series(series, clean_window_base, clean_window_start)
        uw = _underwater(vals)
        underwater_series[key] = [{"date": d, "drawdown": uw[i]} for i, d in enumerate(dates)]

        perf[key] = {
            "monthly_return": monthly_return,
            "max_drawdown": mdd_may,
            "volatility": _ann_vol(may_daily),
            "sharpe": _descriptive_sharpe(may_daily, risk_free_rate),  # rf pinned in report_meta
            "trade_count": sum(len(_executed_trades(r)) for r in may_records[key]),
            "win_rate": _f(float(np.mean([1.0 if x > 0 else 0.0 for x in may_daily]))) if may_daily else None,
            "turnover": None,   # filled from behavioral evidence below
            "avg_hold_days": None,  # filled from behavioral evidence below
        }
        leaderboard_rows.append({
            "model": key,
            "monthly_return": monthly_return,
            "cumulative_return": cumulative,
            "spy_relative_alpha": _f(cumulative - spy_cumulative) if (cumulative is not None and spy_cumulative is not None) else None,
        })

    # SPY equity curve (benchmark line), re-anchored to the clean-window base on
    # the same convention as the model series: $100k-normalized benchmark indexed
    # off the 4/22 close, displayed from clean_window_start. SPY's month-end index
    # == (1 + SPY clean-window return) * 100, matching its cumulative_return_clean.
    spy_base_val = spy_vals.get(clean_window_base)
    if spy_base_val is None:
        _sp = [spy_vals[d] for d in spy_dates if d < clean_window_start]
        spy_base_val = _sp[-1] if _sp else None
    equity_series["spy_benchmark"] = [
        {"date": d,
         "portfolio_value": _f(spy_vals[d] / spy_base_val * INCEPTION_CAPITAL),
         "indexed_return": _f(spy_vals[d] / spy_base_val * 100.0)}
        for d in spy_dates if d >= clean_window_start
    ] if spy_base_val else []

    # rank by cumulative_return desc (canonical build_leaderboard basis)
    leaderboard_rows.sort(key=lambda r: -(r["cumulative_return"] if r["cumulative_return"] is not None else -1e9))
    for i, r in enumerate(leaderboard_rows, 1):
        r["rank"] = i
    leaderboard = [{"model": r["model"], "rank": r["rank"], "monthly_return": r["monthly_return"],
                    "cumulative_return": r["cumulative_return"], "spy_relative_alpha": r["spy_relative_alpha"]}
                   for r in leaderboard_rows]

    # ====================================================================
    # Behavioral evidence (fills perf turnover / hold, feeds profiles)
    # ====================================================================
    evidence = _behavioral_evidence(full_records, model_keys, win_start, win_end)
    for key in model_keys:
        if perf.get(key) is not None:
            perf[key]["turnover"] = evidence[key]["mean_daily_turnover"]
            perf[key]["avg_hold_days"] = evidence[key]["avg_hold_days"]

    # ====================================================================
    # RQ point estimates (RQ1/2/3 month) + accumulating inputs (RQ4/5/6)
    # ====================================================================
    logger.info("RQ1 (calendar-month, permutation null + bootstrap CI)...")
    rq1 = compute_rq1(may_records, regime_map, model_keys,
                      n_permutations=500, n_resamples=DEFAULT_N_RESAMPLES)
    logger.info("RQ2/RQ3 (full replay, month-attributed)...")
    rq2 = _rq2_month(full_records, model_keys, win_start, win_end, DEFAULT_N_RESAMPLES)
    rq3 = _rq3_month(full_records, model_keys, win_start, win_end, DEFAULT_N_RESAMPLES)
    logger.info("RQ4 (accumulating factor regression)...")
    rq4 = compute_rq4({}, model_keys)
    # Diagnose RQ4 blocking cause (factor-publication lag vs aligned-day count).
    from src.analytics.research_metrics import _model_daily_returns, load_ff_factors
    _ff = load_ff_factors() or {}
    _ff_last = max(_ff.keys()) if _ff else None
    _ret_dates = set()
    for _k in model_keys:
        _ret_dates |= set(_model_daily_returns(_k).keys())
    rq4_aligned_days = len([dt for dt in _ret_dates if dt in _ff])
    rq4_factor_last_date = _ff_last
    logger.info("RQ5 (accumulating Phase-A pilot; interim bootstrap B=2000)...")
    rq5 = compute_rq5(full_records, {}, model_keys, n_resamples=2000)
    logger.info("RQ6 (accumulating determinism probe)...")
    rq6 = compute_rq6(model_keys)

    # ====================================================================
    # Charts: correlation matrix (separate field)
    # ====================================================================
    corr_matrix = _correlation_matrix(may_records, model_keys)

    # ====================================================================
    # cross_model_behavioral
    # ====================================================================
    cross_model_behavioral = {
        "rq1_herding_point_estimate": {
            "observed_action_concordance": _f(rq1.get("observed_action_concordance")),
            "null_action_concordance_mean": _f(rq1.get("null_action_concordance_mean")),
            "concordance_excess_over_chance": _f(rq1.get("concordance_excess_over_chance")),
            "permutation_p_value": _f(rq1.get("permutation_p_value")),
            "observed_weight_correlation": _f(rq1.get("observed_weight_correlation")),
            "ci_action_concordance": {
                "low": _f((rq1.get("ci_action_concordance") or {}).get("low")),
                "high": _f((rq1.get("ci_action_concordance") or {}).get("high")),
            },
            "n_pairwise_observations": rq1.get("n_pairwise_observations"),
            "n_ticks": rq1.get("n_ticks"),
            "canonical_definition_ref": "research_metrics.compute_rq1 / v1.json RQ1",
            "window": "calendar_month",
            "pilot_exploratory": True,
        },
        "per_model_trade_activity": {
            key: {
                "trade_count": evidence[key]["trade_count"],
                "buy_count": evidence[key]["buy_count"],
                "sell_count": evidence[key]["sell_count"],
                "trades_per_active_day": evidence[key]["trades_per_active_day"],
                "active_trading_days": evidence[key]["active_trading_days"],
            } for key in model_keys
        },
        "per_model_reversal_churn_rate": {
            key: {
                "reversal_flip_rate": evidence[key]["reversal_flip_rate"],
                "n_flips": evidence[key]["n_flips"],
                "n_reversal_pairs": evidence[key]["n_reversal_pairs"],
            } for key in model_keys
        },
        "definition_refs": {
            "reversal_flip_rate": ("in-window executed trades reversing the prior executed side "
                                   "on the same ticker / in-window trades with a known prior side "
                                   "(scripts/behavioral_summary.flip_rate; full-history prior side)."),
            "trade_activity": "executed BUY/SELL only (HOLDs excluded), calendar month.",
        },
        "window": "calendar_month",
        "pilot_exploratory": True,
    }

    # ====================================================================
    # profiles[] — evidence_metrics ONLY; interpretive fields null
    # ====================================================================
    notable = _notable_events(full_records, model_keys, win_start, win_end)
    profiles = []
    for key in model_keys:
        cfg = settings["models"].get(key, {})
        e = evidence[key]
        profiles.append({
            "model": key,
            "display_name": cfg.get("display_name", key),
            "cohort": cfg.get("cohort", "core"),
            "evidence_metrics": {
                "monthly_return": (perf[key] or {}).get("monthly_return"),
                "cumulative_return": next((r["cumulative_return"] for r in leaderboard if r["model"] == key), None),
                "trade_count": e["trade_count"],
                "trades_per_active_day": e["trades_per_active_day"],
                "reversal_flip_rate": e["reversal_flip_rate"],
                "turnover_mean_daily": e["mean_daily_turnover"],
                "concentration_mean_daily_hhi": e["mean_daily_hhi_concentration"],
                "avg_position_size_mean_daily": e["mean_daily_avg_position_size"],
                "cash_pct_mean_daily": e["mean_daily_cash_pct"],
                "avg_hold_days": e["avg_hold_days"],
                "median_hold_days": e["median_hold_days"],
                "same_day_exits": e["same_day_exits"],
                "n_closed_trades": e["n_closed_trades"],
                "win_rate_day_level": (perf[key] or {}).get("win_rate"),
                "rq3_confidence_outcome_corr": rq3["per_model"].get(key, {}).get("confidence_outcome_corr"),
                "rq2_disposition_difference": rq2["per_model"].get(key, {}).get("disposition_difference"),
            },
            # Objective event extraction (no notability judgment / narrative) — fixed
            # field, every model every month. Empty arrays where none occurred.
            "notable_events": notable[key],
            # Interpretive fields — authored in the Reports chat, written back later.
            "style_tag": None,
            "risk_posture_tag": None,
            "strengths": None,
            "weaknesses": None,
        })

    # ====================================================================
    # methodology_data_integrity_rq
    # ====================================================================
    data_integrity = _data_integrity(model_keys, win_start, win_end, settings)
    rq_update = {
        "point_estimates": {
            "RQ1": {
                "title": "Decision convergence under identical information sets",
                "value_observed_action_concordance": _f(rq1.get("observed_action_concordance")),
                "null_mean": _f(rq1.get("null_action_concordance_mean")),
                "excess_over_chance": _f(rq1.get("concordance_excess_over_chance")),
                "permutation_p_value": _f(rq1.get("permutation_p_value")),
                "ci_90": {"low": _f((rq1.get("ci_action_concordance") or {}).get("low")),
                          "high": _f((rq1.get("ci_action_concordance") or {}).get("high"))},
                "by_regime": rq1.get("by_regime", {}),
                "canonical_definition_ref": "compute_rq1 / v1.json RQ1.metric_definition",
                "window": "calendar_month", "pilot_tag": "Phase A pilot / exploratory",
            },
            "RQ2": {
                "title": "Disposition effect in sequential trading",
                "pooled_disposition_difference": rq2["pooled"]["disposition_difference"],
                "pooled_PGR": rq2["pooled"]["PGR"], "pooled_PLR": rq2["pooled"]["PLR"],
                "pooled_ci_90": rq2["pooled"]["ci_difference"], "pooled_p_value": rq2["pooled"]["p_value"],
                "n_sale_records": rq2["pooled"]["n_sale_records"],
                "per_model": rq2["per_model"],
                "canonical_definition_ref": "compute_rq2 helpers (_replay_avg_cost/_pgr_plr) / v1.json RQ2",
                "windowing_note": ("full-history avg-cost replay; sale records attributed by "
                                   "sale-date in the calendar month (behavioral_summary discipline)."),
                "window": "calendar_month", "pilot_tag": "Phase A pilot / exploratory",
            },
            "RQ3": {
                "title": "Confidence calibration on closed trades",
                "pooled_confidence_outcome_corr": rq3["pooled"]["confidence_outcome_corr"],
                "pooled_ece": rq3["pooled"]["expected_calibration_error"],
                "pooled_brier": rq3["pooled"]["brier_score"],
                "pooled_hit_rate": rq3["pooled"]["overall_hit_rate"],
                "pooled_ci_90": rq3["pooled"]["corr_ci"], "pooled_p_value": rq3["pooled"]["p_value"],
                "n_closed_trades": rq3["pooled"]["n_closed_trades"],
                "calibration_curve": rq3["pooled"]["calibration_curve"],
                "per_model": rq3["per_model"],
                "min_closed_trades_for_resolution": MIN_CLOSED_TRADES_RQ3,
                "canonical_definition_ref": "compute_rq3 helpers (_closed_trades/_calibration_stats) / v1.json RQ3",
                "windowing_note": ("full-history replay for correct entry confidence/cost basis; "
                                   "trades attributed by full-exit date in the calendar month."),
                "window": "calendar_month", "pilot_tag": "Phase A pilot / exploratory",
            },
        },
        "accumulating_inputs": {
            "RQ4": {
                "title": "Systematic style-factor tilts (FF5 + Momentum)",
                "status": rq4.get("status"),
                "factor_model": rq4.get("factor_model"),
                "per_model": rq4.get("per_model", {}),
                "tag": "RAW ACCUMULATING INPUT — not a monthly point estimate; pooled at quarter.",
                "window": "full Phase-A daily excess returns to date (factor regression; needs >=60 aligned days to resolve)",
                "n_aligned_factor_days": rq4_aligned_days,
                "factor_data_last_date": rq4_factor_last_date,
                "blocking_reason": (
                    f"Open: the Fama-French daily factor cache ends {rq4_factor_last_date} (Ken "
                    f"French Data Library publishes with a multi-week lag), while the experiment "
                    f"runs from {INCEPTION_DATE} onward — {rq4_aligned_days} aligned factor days. "
                    "RQ4 will populate once FF factors for the experiment window publish and "
                    ">=60 aligned days accrue. Independent of any windowing choice."),
                "canonical_definition_ref": "compute_rq4 / v1.json RQ4",
            },
            "RQ5": {
                "title": "Path-dependent risk behavior under drawdown",
                "status": rq5.get("status"),
                "pilot_window_start": rq5.get("pilot_window_start"),
                "drawdown_threshold": rq5.get("drawdown_threshold"),
                "total_drawdown_days_across_models": rq5.get("total_drawdown_days_across_models"),
                "behavioral_metrics": rq5.get("behavioral_metrics"),
                "per_model": rq5.get("per_model", {}),
                "headline_test": rq5.get("headline_test", {}),
                "tag": "RAW ACCUMULATING INPUT — not a monthly point estimate; pooled at quarter.",
                "window": f"canonical Phase-A pilot window (>= {RQ5_PHASE_A_PILOT_START}), self-filtered",
                "bootstrap_note": "interim accumulating estimate at B=2000; quarterly pooling uses locked B=10000.",
                "canonical_definition_ref": "compute_rq5 / v1.json RQ5",
            },
            "RQ6": {
                "title": "Operational reproducibility of deployed agents",
                "status": rq6.get("status"),
                "estimand": rq6.get("estimand"),
                "overall_mean_divergence": rq6.get("overall_mean_divergence"),
                "per_model": rq6.get("per_model", {}),
                "tag": "RAW ACCUMULATING INPUT — no frozen-context probe data yet (data/determinism/ empty).",
                "window": "all determinism-probe data (manual off-peak frozen-context probe)",
                "canonical_definition_ref": "compute_rq6 / v1.json RQ6",
            },
        },
        "fdr_note": ("Benjamini-Hochberg FDR (q=0.10, family RQ1-RQ5) is a live-phase confirmatory "
                     "construct applied to one headline p-value per RQ; it is NOT computed for the "
                     "pilot monthly. Monthly figures are Phase A pilot/exploratory only."),
    }

    # ====================================================================
    # report_meta
    # ====================================================================
    report_meta = {
        "period": month,
        "period_label": datetime.strptime(win_start, "%Y-%m-%d").strftime("%B %Y"),
        "regime": "v1",
        "phase": settings.get("phase", "Phase A - Paper Trading"),
        "pilot_exploratory": True,
        "data_window": {
            "calendar_month": {"start": win_start, "end": win_end,
                               "first_trading_date": sorted({r.get("date") for recs in may_records.values() for r in recs if r.get("date")})[0] if any(may_records.values()) else None,
                               "last_trading_date": sorted({r.get("date") for recs in may_records.values() for r in recs if r.get("date")})[-1] if any(may_records.values()) else None,
                               "trading_days": len({r.get("date") for recs in may_records.values() for r in recs if r.get("date")})},
            "inception_anchor": {"inception_date": INCEPTION_DATE, "inception_capital_usd": INCEPTION_CAPITAL,
                                 "note": ("cumulative_return + equity-curve series only; pre-month pilot data "
                                          "(2026-04-09..2026-04-30) enters ONLY these inception-anchored series, "
                                          "never the monthly behavioral/RQ window.")},
        },
        "prompt_version": "v1",
        "prompt_version_note": ("May decision records are entirely prompt_version=v1 (verified). "
                                "settings.json now reads prompt_version=v2 (flipped 2026-05-31, "
                                "commit deploys v2 from June); the May report pins the v1 regime the "
                                "data was generated under, NOT the current settings value."),
        "pinned_snapshots": _pinned_snapshots(model_keys, win_start, win_end, settings),
        "risk_free_rate": _f(risk_free_rate),
        "risk_free_rate_note": ("Pinned in settings.json = 0.0368 (3-month T-bill, annualized, as of "
                                "2026-06-01). performance[].sharpe is now populated as the descriptive "
                                "annualized monthly Sharpe (mean daily excess return / daily std * "
                                "sqrt(252) over May EOD returns), explicitly NOT the Deflated Sharpe "
                                "Ratio of v1.json sharpe_reporting."),
        "spy_benchmark": {
            "ticker": settings.get("benchmark_ticker", "SPY"),
            "inception_anchor_date": INCEPTION_DATE,
            "inception_value": _f(spy_inception),
            "month_end_value": _f(spy_last),
            "cumulative_return_since_inception": spy_cumulative,
            "monthly_return": spy_month_return,
            "note": "SPY EOD from perf-log benchmark_value; anchored to the 2026-04-09 model inception EOD.",
        },
        "regime_summary": regime_summary,
        "source_commit": _source_commit(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": "scripts/build_monthly_data_layer.py",
        "bootstrap_config": {"block_length": DEFAULT_BLOCK_LENGTH, "rq1_rq2_rq3_n_resamples": DEFAULT_N_RESAMPLES,
                             "rq1_n_permutations": 500, "rq5_n_resamples_interim": 2000},
        "methodology_notes": {
            "leaderboard_rank_basis": "cumulative_return descending (canonical build_leaderboard basis).",
            "cumulative_return": "last EOD value / $100,000 inception capital - 1 (== stored cumulative_return field).",
            "monthly_return": "last May EOD value / first May EOD value - 1 (calendar-month window).",
            "spy_relative_alpha": "model cumulative_return - SPY cumulative_return, both anchored to 2026-04-09 EOD.",
            "max_drawdown": "performance[].max_drawdown is May-confined (running peak within May); the underwater chart uses inception-anchored running peak.",
            "volatility": "annualized std of May daily EOD returns (ddof=1) * sqrt(252).",
            "turnover": "performance[].turnover and profiles[].turnover_mean_daily are mean daily executed-notional/EOD-value over May (RQ5 _daily_portfolio_features definition).",
            "win_rate": "day-level: fraction of May trading days with positive EOD return (canonical compute_metrics definition).",
            "risk_free_rate": "0.0368 annualized (3-month T-bill), source date 2026-06-01; pinned in settings.json.risk_free_rate.",
            "sharpe": "descriptive annualized monthly Sharpe = mean(daily EOD return - rf/252) / std(daily return, ddof=1) * sqrt(252) over the May calendar month; NOT the Deflated Sharpe.",
            "concentration": "evidence_metrics.concentration_mean_daily_hhi is the portfolio-concentration (Herfindahl) measure; mean daily HHI of position weights over May. No separate concentration_hhi field was added.",
            "notable_events": "profiles[].notable_events is objective event extraction (top-3 trades by abs USD value, stop-loss force-sells, drawdown-halt triggers, API/JSON error events) over the calendar month; empty arrays where none.",
            "halt_gate": "all six compute_rqX verified against v1.json; PASS. Cosmetic only: RQ5 metric label cash_pct (code) == cash (v1.json), same quantity.",
        },
    }

    layer = {
        "report_meta": report_meta,
        "leaderboard": leaderboard,
        "performance": perf,
        "charts": {
            "equity_curve": {
                "anchor": {
                    "clean_window_start": clean_window_start,
                    "clean_window_base_date": clean_window_base,
                    "index_base": 100.0,
                    "excluded_window": {
                        "start": ledger["shakedown_window"]["start"],
                        "end": ledger["shakedown_window"]["end"],
                    },
                    "note": (
                        "Phase-A reporting convention: the displayed equity curve anchors at "
                        "clean_window_start (read from phase_a_integrity_ledger.json) and indexes "
                        "off the clean-window base close (clean_window_base_date == the last "
                        "shakedown day). The launch/shakedown window is excluded from the "
                        "displayed curve as non-estimable; the inception-anchored cumulative "
                        "return is unchanged and lives in leaderboard.cumulative_return_inception."
                    ),
                },
                "series": equity_series,
                "caption": EQUITY_CURVE_CAPTION,
            },
            "underwater": {
                "anchor": {"inception_date": INCEPTION_DATE, "definition": "drawdown vs inception-anchored running peak (<=0)"},
                "series": underwater_series,
            },
            "correlation_matrix": corr_matrix,
        },
        "cross_model_behavioral": cross_model_behavioral,
        "profiles": profiles,
        "methodology_data_integrity_rq": {
            "data_integrity": data_integrity,
            "rq_update": rq_update,
        },
    }

    # ====================================================================
    # Native integrity / provenance layer (Phase-A ledger + computed gates)
    # ====================================================================
    from scripts.defab_clean_window import compute_clean_window
    # `ledger` was loaded once at the top of build() (reused here).

    # De-fab clean-window overhang correction (committed scripts/defab_clean_window
    # logic, reused). Window: shakedown-end base -> report month-end, over the
    # inception..report-month decision logs. For May this reduces to the committed
    # default window (base 2026-04-22 -> 2026-05-29) and reproduces it exactly.
    inception_ym = INCEPTION_DATE[:7]
    months_in_window = []
    y, m = (int(x) for x in inception_ym.split("-"))
    ey, em = (int(x) for x in month.split("-"))
    while (y, m) <= (ey, em):
        months_in_window.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    logger.info("De-fab clean-window overhang correction (%s)...", "/".join(months_in_window))
    clean_results = compute_clean_window(
        end_date=win_end, base_date=ledger["shakedown_window"]["end"],
        clean_start=ledger["clean_window_start"], months=months_in_window)

    # SPY clean-window return + descriptive (May-window) Sharpe — both derived,
    # single-sourced into the leaderboard/performance SPY rows.
    base_date = ledger["shakedown_window"]["end"]
    spy_clean_value = None
    if spy_vals.get(base_date) and spy_dates:
        spy_clean_value = round(spy_vals[spy_dates[-1]] / spy_vals[base_date] - 1.0, 4)
    spy_month_daily = list(np.diff([v for _, v in spy_may]) / np.array([v for _, v in spy_may][:-1])) \
        if len(spy_may) >= 2 else []
    _sds = _descriptive_sharpe(spy_month_daily, risk_free_rate)
    spy_descriptive_sharpe = round(_sds, 2) if _sds is not None else None

    decision_completeness = _decision_completeness(layer)
    gates = _evaluate_gates(model_keys, decision_completeness, clean_results, ledger)
    _apply_integrity(layer, ledger, gates, model_keys, win_start, win_end, month,
                     spy_clean_value, spy_descriptive_sharpe)
    return layer


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Build the canonical monthly data layer (read-only).")
    parser.add_argument("--month", required=True, help="Target calendar month, YYYY-MM (e.g. 2026-05)")
    parser.add_argument("--output", default=None, help="Output path (default: reports/monthly/<month>/data_layer.json)")
    args = parser.parse_args()

    layer = build(args.month)

    # ---- self-validation: never emit a bad layer silently (Part 3) ----
    ok, report = _self_validate(layer)
    print("\nSELF-VALIDATION")
    print("-" * 70)
    for line in report:
        print(line)
    print(f"\nOVERALL: {'PASS' if ok else 'FAIL'}")
    if not ok:
        logger.error("Self-validation FAILED; data layer NOT written.")
        return 1

    out_path = args.output or str(REPORTS_DIR / "monthly" / args.month / "data_layer.json")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(layer, f, indent=2, default=str)
    logger.info("Data layer written: %s", out_path)
    print(out_path)
    # MANDATORY RELEASE GATE: source_commit is null until reconciled post-commit.
    print(
        "\nRELEASE GATE — report_meta.source_commit is null and MUST be reconciled "
        "before this layer is published:\n"
        f"  1) commit {os.path.relpath(out_path, str(PROJECT_ROOT))}\n"
        f"  2) HASH=$(git log -1 --format=%H -- {os.path.relpath(out_path, str(PROJECT_ROOT))})\n"
        "  3) write HASH into report_meta.source_commit, then commit the reconcile.\n"
        "A published monthly layer with a null source_commit is a release-gate failure."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
