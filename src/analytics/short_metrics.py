"""Short-side analytics — utilization, short P&L / hit-rate, gross concentration,
and direction-segmented trade outcomes.

These read the same decision-log JSONL the rest of the analytics stack reads.
They are additive and self-contained: the existing RQ2/RQ3/RQ5 functions in
`research_metrics` filter executions to BUY/SELL and so transparently ignore
shorts, keeping the long-only historical analysis byte-stable. This module
covers the v3 short side, which is tagged exploratory in Phase A (v3-only,
expected low-n).

Sign convention (the spine of all of this): a short SUCCEEDS when the price
falls. A short is opened with a SHORT (proceeds credited) and closed with a
COVER (cost paid), so realized P&L over the life of a short = Σ short proceeds −
Σ cover cost, which is positive exactly when the cover prices came in below the
short entries. `profitable` is therefore already direction-correct with no
special casing — it just reads the sign of realized P&L.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

SHARES_EPSILON = 1e-6


# --------------------------------------------------------------------------- #
# execution filters
# --------------------------------------------------------------------------- #
def _short_executions(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """SHORT/COVER executions that actually filled, in record order."""
    return [
        ex for ex in (rec.get("executions") or [])
        if ex.get("executed") and ex.get("side") in ("SHORT", "COVER")
    ]


def _long_executions(rec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        ex for ex in (rec.get("executions") or [])
        if ex.get("executed") and ex.get("side") in ("BUY", "SELL")
    ]


# --------------------------------------------------------------------------- #
# closed shorts: realized P&L + hit-rate + entry confidence
# --------------------------------------------------------------------------- #
def closed_shorts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replay SHORT/COVER fills into fully-closed short positions.

    A short closes only when covers bring the open short magnitude below
    SHARES_EPSILON (a partial cover never closes it — mirrors the long-side
    rule). Realized P&L over the life = Σ short proceeds − Σ cover cost. Entry
    confidence is share-weighted across the SHORT legs of that life.
    """
    open_shares: dict[str, float] = defaultdict(float)   # magnitude currently short
    proceeds: dict[str, float] = defaultdict(float)      # Σ qty*price over SHORT legs
    cover_cost: dict[str, float] = defaultdict(float)    # Σ qty*price over COVER legs
    conf_w: dict[str, float] = defaultdict(float)        # Σ conf*qty over SHORT legs
    shorted_qty: dict[str, float] = defaultdict(float)   # Σ qty over SHORT legs
    entry_date: dict[str, str] = {}
    closed: list[dict[str, Any]] = []

    for rec in records:
        for ex in _short_executions(rec):
            t = ex["ticker"]
            price = float(ex.get("fill_price") or 0.0)
            qty = float(ex.get("shares") or 0.0)
            if ex["side"] == "SHORT":
                if open_shares[t] < SHARES_EPSILON:
                    entry_date[t] = rec.get("date", "")
                open_shares[t] += qty
                proceeds[t] += qty * price
                shorted_qty[t] += qty
                conf = (ex.get("decision") or {}).get("confidence")
                if conf is not None:
                    conf_w[t] += float(conf) * qty
            else:  # COVER
                cover_cost[t] += qty * price
                open_shares[t] -= qty
                if open_shares[t] < SHARES_EPSILON:
                    realized = proceeds[t] - cover_cost[t]
                    ec = (conf_w[t] / shorted_qty[t]) if shorted_qty[t] > 0 else float("nan")
                    closed.append({
                        "ticker": t,
                        "direction": "short",
                        "entry_date": entry_date.get(t, rec.get("date", "")),
                        "exit_date": rec.get("date", ""),
                        "realized_pnl": realized,
                        "profitable": 1 if realized > 0 else 0,
                        "entry_confidence": int(round(ec)) if math.isfinite(ec) else None,
                        "entry_confidence_raw": float(ec) if math.isfinite(ec) else None,
                    })
                    open_shares[t] = 0.0
                    proceeds[t] = 0.0
                    cover_cost[t] = 0.0
                    conf_w[t] = 0.0
                    shorted_qty[t] = 0.0
                    entry_date.pop(t, None)
    return closed


def short_pnl_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Short P&L, hit-rate, and counts over closed short positions."""
    closed = closed_shorts(records)
    n = len(closed)
    if n == 0:
        return {"n_closed_shorts": 0, "total_realized_pnl": 0.0,
                "hit_rate": None, "avg_pnl": None, "wins": 0, "losses": 0}
    wins = sum(c["profitable"] for c in closed)
    total = sum(c["realized_pnl"] for c in closed)
    return {
        "n_closed_shorts": n,
        "total_realized_pnl": round(total, 2),
        "hit_rate": round(wins / n, 4),
        "avg_pnl": round(total / n, 2),
        "wins": wins,
        "losses": n - wins,
    }


# --------------------------------------------------------------------------- #
# short utilization (descriptive reportable, from go-live)
# --------------------------------------------------------------------------- #
def short_utilization(records: list[dict[str, Any]], short_cap_pct: float = 0.20) -> dict[str, Any]:
    """Share of decisions that are short + short-exposure usage vs the cap.

    - decision_share: fraction of executed trades that are SHORT or COVER.
    - short_opens / covers: raw counts.
    - avg/max short_exposure_pct: from each run's portfolio_after snapshot
      (`short_utilization` / `short_exposure_pct`), i.e. |short value| / equity.
    - avg/max_utilization_vs_cap: that exposure expressed as a fraction of the
      configured gross-short cap (1.0 == sitting exactly at the cap).
    """
    n_short_exec = 0
    n_long_exec = 0
    n_short_opens = 0
    n_covers = 0
    exposures: list[float] = []

    for rec in records:
        for ex in _short_executions(rec):
            n_short_exec += 1
            if ex["side"] == "SHORT":
                n_short_opens += 1
            else:
                n_covers += 1
        n_long_exec += len(_long_executions(rec))
        pa = rec.get("portfolio_after") or {}
        se = pa.get("short_exposure_pct", pa.get("short_utilization"))
        if se is not None:
            exposures.append(float(se))

    total_exec = n_short_exec + n_long_exec
    avg_exp = (sum(exposures) / len(exposures)) if exposures else 0.0
    max_exp = max(exposures) if exposures else 0.0
    cap = short_cap_pct if short_cap_pct > 0 else 0.20
    return {
        "decision_share": round(n_short_exec / total_exec, 4) if total_exec else 0.0,
        "short_opens": n_short_opens,
        "covers": n_covers,
        "n_short_executions": n_short_exec,
        "avg_short_exposure_pct": round(avg_exp, 4),
        "max_short_exposure_pct": round(max_exp, 4),
        "avg_utilization_vs_cap": round(avg_exp / cap, 4),
        "max_utilization_vs_cap": round(max_exp / cap, 4),
        "short_cap_pct": cap,
    }


# --------------------------------------------------------------------------- #
# RQ5: gross (absolute) concentration so shorts count
# --------------------------------------------------------------------------- #
def gross_hhi(values: list[float]) -> float:
    """HHI on GROSS (absolute) weights normalized to sum to 1. 0 if empty.

    Unlike the long-only `_hhi_normalized`, this takes |value| of every leg, so
    a short position contributes to concentration exactly as a long of the same
    size does. For a long-only book it is identical to the long-only HHI.
    """
    mags = [abs(float(v)) for v in values if v and float(v) != 0.0]
    tot = sum(mags)
    if tot <= 0:
        return 0.0
    return float(sum((m / tot) ** 2 for m in mags))


def gross_hhi_from_snapshot(snapshot: dict[str, Any]) -> float:
    """Gross HHI from a portfolio snapshot's holdings (by absolute market value)."""
    mvs = [h.get("market_value", 0.0) for h in (snapshot.get("holdings") or [])]
    return gross_hhi(mvs)


# --------------------------------------------------------------------------- #
# RQ2 / RQ3: direction-segmented closed-trade outcomes
# --------------------------------------------------------------------------- #
def _closed_longs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Long closed trades with realized P&L + share-weighted entry confidence.

    Self-contained mirror of the long-only close-matching so this module does
    not reach into research_metrics internals. Closes a long when sells take
    residual shares below SHARES_EPSILON.
    """
    shares: dict[str, float] = defaultdict(float)
    cost_basis: dict[str, float] = defaultdict(float)
    proceeds: dict[str, float] = defaultdict(float)
    conf_w: dict[str, float] = defaultdict(float)
    bought_qty: dict[str, float] = defaultdict(float)
    entry_date: dict[str, str] = {}
    closed: list[dict[str, Any]] = []

    for rec in records:
        for ex in _long_executions(rec):
            t = ex["ticker"]
            price = float(ex.get("fill_price") or 0.0)
            qty = float(ex.get("shares") or 0.0)
            if ex["side"] == "BUY":
                if shares[t] < SHARES_EPSILON:
                    entry_date[t] = rec.get("date", "")
                shares[t] += qty
                cost_basis[t] += qty * price
                bought_qty[t] += qty
                conf = (ex.get("decision") or {}).get("confidence")
                if conf is not None:
                    conf_w[t] += float(conf) * qty
            else:  # SELL
                proceeds[t] += qty * price
                shares[t] -= qty
                if shares[t] < SHARES_EPSILON:
                    realized = proceeds[t] - cost_basis[t]
                    ec = (conf_w[t] / bought_qty[t]) if bought_qty[t] > 0 else float("nan")
                    closed.append({
                        "ticker": t,
                        "direction": "long",
                        "entry_date": entry_date.get(t, rec.get("date", "")),
                        "exit_date": rec.get("date", ""),
                        "realized_pnl": realized,
                        "profitable": 1 if realized > 0 else 0,
                        "entry_confidence": int(round(ec)) if math.isfinite(ec) else None,
                        "entry_confidence_raw": float(ec) if math.isfinite(ec) else None,
                    })
                    shares[t] = 0.0
                    cost_basis[t] = 0.0
                    proceeds[t] = 0.0
                    conf_w[t] = 0.0
                    bought_qty[t] = 0.0
                    entry_date.pop(t, None)
    return closed


def closed_trades_by_direction(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Closed trades split into {'long': [...], 'short': [...]}.

    Both carry sign-corrected `profitable` and share-weighted `entry_confidence`,
    so RQ2 (disposition) and RQ3 (calibration) can be computed per segment. Feed
    each list to the same downstream stats you'd use on the pooled long-only set.
    """
    return {"long": _closed_longs(records), "short": closed_shorts(records)}


def hit_rate_by_direction(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact RQ2/RQ3 segmentation: n, hit-rate, and confidence/outcome
    correlation per direction. Short side is exploratory (low-n) in Phase A."""
    seg = closed_trades_by_direction(records)
    out: dict[str, Any] = {}
    for direction, trades in seg.items():
        n = len(trades)
        if n == 0:
            out[direction] = {"n": 0, "hit_rate": None, "confidence_outcome_corr": None}
            continue
        wins = sum(t["profitable"] for t in trades)
        usable = [t for t in trades if t.get("entry_confidence") is not None]
        corr = _point_biserial(
            [t["entry_confidence"] for t in usable],
            [t["profitable"] for t in usable],
        ) if len(usable) >= 5 else None
        out[direction] = {
            "n": n,
            "hit_rate": round(wins / n, 4),
            "avg_realized_pnl": round(sum(t["realized_pnl"] for t in trades) / n, 2),
            "confidence_outcome_corr": corr,
            "exploratory": direction == "short",  # v3-only, low-n in Phase A
        }
    return out


def _point_biserial(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return round(cov / (sx * sy), 4)
