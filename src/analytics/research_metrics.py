"""Research-question metrics for the LLM Trading Lab paper track.

Computes the six pre-registered RQ metrics from the production decision and
performance logs. Every estimate is reported with a moving-block bootstrap
confidence interval (where a CI is meaningful) and is stratified by market
regime via ``regime_classifier``. The headline frequentist test for each RQ
feeds a Benjamini-Hochberg FDR family at q = 0.10.

The exact definitions, hypotheses, predictions, and decision rules are locked
in ``docs/PRE_REGISTRATION.md`` (and mirrored in ``data/pre_registration/
v1.json``). This module is the executable counterpart — if a definition here
ever diverges from the pre-registration, the pre-registration wins and this
code is the bug.

RQ1  Decision convergence under identical information sets (PRIMARY)
RQ2  Disposition effect in sequential trading
RQ3  Confidence calibration on closed trades
RQ4  Systematic style-factor tilts (Fama-French 5 + momentum)
RQ5  Behavioral response to portfolio drawdowns
RQ6  Non-determinism at temperature = 0 (METHODOLOGICAL, manual probe)

Data source of truth:
  * /data/trades/{model}_{YYYY-MM}.jsonl  — per-tick decisions + executions
  * /data/performance/{model}.jsonl        — one EOD portfolio snapshot per day
  * /data/determinism/*.jsonl              — temperature=0 rerun probe output (RQ6)
"""
from __future__ import annotations

import json
import logging
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from ..config_loader import (
    DATA_DIR,
    PERFORMANCE_DIR,
    TRADES_DIR,
    load_settings,
)
from .performance import load_performance_history
from .regime_classifier import (
    ALL_REGIMES,
    INSUFFICIENT,
    classify_regimes,
    label_for_dates,
)
from .statistical_corrections import (
    DEFAULT_BLOCK_LENGTH,
    DEFAULT_FDR_Q,
    DEFAULT_N_RESAMPLES,
    bca_bootstrap_ci,
    benjamini_hochberg,
    norm_cdf,
)

logger = logging.getLogger("llmlab.research")

DETERMINISM_DIR = DATA_DIR / "determinism"
RESEARCH_DIR = DATA_DIR / "research"

# Pre-registered minimum closed-trade count before RQ3 calibration is reported
MIN_CLOSED_TRADES_RQ3 = 20
# Pre-registered model-drawdown trigger for RQ5 (own equity, distinct from the
# SPY drawdown *regime*): 10% below the trailing 60-day EOD peak.
RQ5_DRAWDOWN_THRESHOLD = -0.10
RQ5_PEAK_WINDOW = 60
# Full-exit epsilon — a position is "closed" only when residual shares fall
# below this (mirrors Portfolio.GHOST_SHARES_EPSILON).
SHARES_EPSILON = 0.01

_ACTION_CODE = {"BUY": 0, "SELL": 1, "HOLD": 2}


# ==========================================================================
# Shared log loading
# ==========================================================================

def get_model_keys(settings: dict[str, Any] | None = None) -> list[str]:
    settings = settings or load_settings()
    return [k for k, cfg in settings["models"].items() if cfg.get("enabled", True)]


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_decision_records(model_key: str) -> list[dict[str, Any]]:
    """All decision-log records for a model, chronological, with parsed _ts."""
    pattern = re.compile(rf"^{re.escape(model_key)}_(\d{{4}})-(\d{{2}})\.jsonl$")
    out: list[dict[str, Any]] = []
    if not TRADES_DIR.exists():
        return out
    for fp in sorted(TRADES_DIR.iterdir()):
        if not fp.is_file() or not pattern.match(fp.name):
            continue
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rec["_ts"] = _parse_ts(rec.get("timestamp", ""))
                out.append(rec)
    out.sort(key=lambda r: (r.get("date", ""), r.get("timestamp", "")))
    return out


def _executed_trades(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """BUY/SELL executions that actually filled, in record order."""
    return [
        ex for ex in (rec.get("executions") or [])
        if ex.get("executed") and ex.get("side") in ("BUY", "SELL")
    ]


def _collect_dates(all_records: dict[str, list[dict[str, Any]]]) -> list[str]:
    dates: set[str] = set()
    for recs in all_records.values():
        for r in recs:
            d = r.get("date")
            if d:
                dates.add(d)
    return sorted(dates)


def _build_regime_map(dates: list[str], with_regime: bool) -> tuple[dict[str, str], dict[str, Any]]:
    """Return ({date: regime}, regime_summary). One SPY fetch for the run."""
    if not with_regime or not dates:
        return ({d: INSUFFICIENT for d in dates}, {"total_days": 0, "counts": {}})
    # pad start back so the trailing windows are populated on the first decision day
    start_pad = (np.datetime64(min(dates)) - np.timedelta64(120, "D")).astype(str)
    try:
        regime_df = classify_regimes(start=str(start_pad), end=max(dates))
        rmap = label_for_dates(dates, regime_df=regime_df)
        from .regime_classifier import summarize_regimes
        return rmap, summarize_regimes(regime_df)
    except Exception:
        logger.exception("Regime classification failed; proceeding unstratified")
        return ({d: INSUFFICIENT for d in dates}, {"total_days": 0, "counts": {}})


def _bootstrap_index_ci(
    n_events: int,
    stat_fn: Callable[[np.ndarray], float],
    block_length: int = DEFAULT_BLOCK_LENGTH,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    alpha: float = 0.10,
    seed: int = 7,
) -> dict[str, Any]:
    """Block-bootstrap a statistic that is a function of *event indices*.

    Lets us bootstrap pooled-proportion statistics (PGR-PLR, calibration) by
    resampling the time-ordered events and recomputing on each resample.
    """
    if n_events < 2:
        return {"point": None, "ci_low": None, "ci_high": None, "p_value": None,
                "method": "insufficient", "n": n_events}
    idx_space = np.arange(n_events, dtype=float)

    def _wrapped(sample: np.ndarray) -> float:
        return stat_fn(sample.astype(int))

    res = bca_bootstrap_ci(idx_space, _wrapped, alpha=alpha,
                           block_length=block_length, n_resamples=n_resamples, seed=seed)
    # Two-sided bootstrap p-value for H0: stat == 0, from the resample sign mix
    reps = []
    rng = np.random.default_rng(seed + 1)
    from .statistical_corrections import _moving_block_indices
    for _ in range(min(n_resamples, 4000)):
        s = _moving_block_indices(n_events, block_length, rng)
        try:
            reps.append(stat_fn(s.astype(int)))
        except Exception:
            continue
    reps = np.asarray([r for r in reps if r is not None and math.isfinite(r)])
    p_value = None
    if len(reps) > 10:
        frac_le0 = float(np.mean(reps <= 0.0))
        p_value = 2.0 * min(frac_le0, 1.0 - frac_le0)
        p_value = min(1.0, max(0.0, p_value))
    return {"point": res.point_estimate, "ci_low": res.ci_low, "ci_high": res.ci_high,
            "p_value": p_value, "method": res.method, "n": n_events}


# ==========================================================================
# RQ1 — Decision convergence under identical information sets (PRIMARY)
# ==========================================================================

def compute_rq1(
    all_records: dict[str, list[dict[str, Any]]],
    regime_map: dict[str, str],
    model_keys: list[str],
    n_permutations: int = 500,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    min_shared: int = 3,
    seed: int = 11,
) -> dict[str, Any]:
    """Do models converge on identical decisions given identical inputs?

    Join key: ``data_inputs_hash`` — verified identical across all models on a
    given tick, so records sharing a hash provably saw the same information.
    For each model we use ``raw_decisions`` (its own pre-risk-filter output)
    to measure *model* cognition, not the executor's downstream filtering.

    Two convergence signals per model pair, per tick, over the tickers both
    models ruled on:
      * action concordance — 3-way BUY/SELL/HOLD exact-match rate (binarized)
      * weight correlation — Pearson r of target_weight vectors (continuous)

    The shuffled-permutation null independently permutes each model's action
    vector within the tick (preserving each model's BUY/SELL/HOLD base rates,
    destroying cross-model alignment), giving the chance level of agreement.
    """
    # Group decision vectors by data_inputs_hash
    # tick -> {model_key: {"actions": {ticker: code}, "weights": {ticker: w}, "date": d}}
    ticks: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for key in model_keys:
        for rec in all_records.get(key, []):
            if not rec.get("api_success"):
                continue
            h = rec.get("data_inputs_hash")
            raw = rec.get("raw_decisions") or []
            if not h or not raw:
                continue
            actions: dict[str, int] = {}
            weights: dict[str, float] = {}
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
                ticks[h][key] = {"actions": actions, "weights": weights,
                                 "date": rec.get("date", "")}

    # Per (pair, tick) observations
    obs_conc: list[float] = []          # observed action concordance
    obs_wcorr: list[float] = []         # observed weight correlation
    obs_regime: list[str] = []
    obs_order: list[str] = []           # tick hash, for block ordering
    # Pre-extract per-tick aligned arrays for the permutation null
    tick_payloads: list[dict[str, Any]] = []

    for h, per_model in ticks.items():
        present = [k for k in model_keys if k in per_model]
        if len(present) < 2:
            continue
        date = per_model[present[0]]["date"]
        regime = regime_map.get(date, INSUFFICIENT)
        # observed pairwise
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                a = per_model[present[i]]["actions"]
                b = per_model[present[j]]["actions"]
                wa = per_model[present[i]]["weights"]
                wb = per_model[present[j]]["weights"]
                shared = [t for t in a if t in b]
                if len(shared) < min_shared:
                    continue
                conc = float(np.mean([1.0 if a[t] == b[t] else 0.0 for t in shared]))
                obs_conc.append(conc)
                obs_regime.append(regime)
                obs_order.append(h)
                # weight correlation
                xa = np.array([wa[t] for t in shared], dtype=float)
                xb = np.array([wb[t] for t in shared], dtype=float)
                if xa.std() > 0 and xb.std() > 0 and len(shared) >= 3:
                    obs_wcorr.append(float(np.corrcoef(xa, xb)[0, 1]))
        # payload for permutation: each model's action array on its tickers
        tick_payloads.append({
            "regime": regime,
            "models": {k: np.array(list(per_model[k]["actions"].values())) for k in present},
            "tickers": {k: list(per_model[k]["actions"].keys()) for k in present},
        })

    n_obs = len(obs_conc)
    if n_obs == 0:
        return {"status": "Open", "n_ticks": len(ticks), "n_pairwise_observations": 0,
                "note": "No ticks with >=2 models sharing >=3 decided tickers yet."}

    observed_conc = float(np.mean(obs_conc))
    observed_wcorr = float(np.mean(obs_wcorr)) if obs_wcorr else None

    # Permutation null: shuffle each model's action vector within each tick
    rng = np.random.default_rng(seed)
    null_means = np.empty(n_permutations, dtype=float)
    for p in range(n_permutations):
        vals: list[float] = []
        for payload in tick_payloads:
            shuffled: dict[str, dict[str, int]] = {}
            for k, arr in payload["models"].items():
                perm = arr.copy()
                rng.shuffle(perm)
                shuffled[k] = dict(zip(payload["tickers"][k], perm))
            present = list(shuffled.keys())
            for i in range(len(present)):
                for j in range(i + 1, len(present)):
                    a = shuffled[present[i]]
                    b = shuffled[present[j]]
                    shared = [t for t in a if t in b]
                    if len(shared) < min_shared:
                        continue
                    vals.append(float(np.mean([1.0 if a[t] == b[t] else 0.0 for t in shared])))
        null_means[p] = float(np.mean(vals)) if vals else np.nan
    null_means = null_means[np.isfinite(null_means)]
    null_mean = float(np.mean(null_means)) if len(null_means) else None
    # one-sided p: chance of null concordance >= observed
    perm_p = None
    if len(null_means):
        perm_p = float((1 + np.sum(null_means >= observed_conc)) / (len(null_means) + 1))

    # Block-bootstrap CI on observed concordance (order by tick for blocks)
    ci = bca_bootstrap_ci(np.array(obs_conc), np.mean, alpha=0.10,
                          n_resamples=n_resamples, seed=seed)

    # Per-regime breakdown
    by_regime: dict[str, Any] = {}
    conc_arr = np.array(obs_conc)
    reg_arr = np.array(obs_regime)
    for regime in ALL_REGIMES:
        mask = reg_arr == regime
        if mask.sum() == 0:
            continue
        sub = conc_arr[mask]
        rci = bca_bootstrap_ci(sub, np.mean, alpha=0.10,
                               n_resamples=min(n_resamples, 4000), seed=seed)
        by_regime[regime] = {
            "n_observations": int(mask.sum()),
            "observed_concordance": float(sub.mean()),
            "ci_low": rci.ci_low, "ci_high": rci.ci_high,
        }

    excess = observed_conc - null_mean if null_mean is not None else None
    return {
        "status": "Testing",
        "n_ticks": len(tick_payloads),
        "n_pairwise_observations": n_obs,
        "observed_action_concordance": observed_conc,
        "null_action_concordance_mean": null_mean,
        "concordance_excess_over_chance": excess,
        "permutation_p_value": perm_p,
        "n_permutations": int(len(null_means)),
        "observed_weight_correlation": observed_wcorr,
        "ci_action_concordance": {"low": ci.ci_low, "high": ci.ci_high, "method": ci.method},
        "by_regime": by_regime,
        "headline_p_value": perm_p,
        "interpretation": (
            "Action concordance above the shuffled-null mean indicates models "
            "converge on the same buy/sell/hold calls more than chance under "
            "identical inputs."
        ),
    }


# ==========================================================================
# RQ2 — Disposition effect (Odean 1998 PGR / PLR)
# ==========================================================================

def _replay_avg_cost(records: list[dict[str, Any]]):
    """Yield (record, sells_with_realized_flag) replaying avg-cost per ticker.

    For each record containing >=1 executed SELL we yield the realized
    gain/loss classification per sell plus the paper gain/loss counts taken
    from that record's portfolio_after snapshot (positions not sold).
    """
    shares: dict[str, float] = defaultdict(float)
    avg_cost: dict[str, float] = {}
    for rec in records:
        sells_info = []
        sold_tickers: set[str] = set()
        for ex in _executed_trades(rec):
            t = ex["ticker"]
            price = float(ex.get("fill_price") or 0.0)
            qty = float(ex.get("shares") or 0.0)
            if ex["side"] == "BUY":
                new_sh = shares[t] + qty
                if new_sh > 0:
                    prev_cost = avg_cost.get(t, price)
                    avg_cost[t] = (prev_cost * shares[t] + price * qty) / new_sh
                shares[t] = new_sh
            else:  # SELL
                ac = avg_cost.get(t)
                if ac is not None and price > 0:
                    is_gain = price > ac
                    is_loss = price < ac
                    sells_info.append((t, is_gain, is_loss))
                    sold_tickers.add(t)
                shares[t] = max(0.0, shares[t] - qty)
                if shares[t] < SHARES_EPSILON:
                    shares[t] = 0.0
        if sells_info:
            # Paper gains/losses from positions still held (not sold this rec)
            pg = pl = 0
            for h in (rec.get("portfolio_after") or {}).get("holdings", []):
                if h.get("ticker") in sold_tickers:
                    continue
                upl = h.get("unrealized_pl_pct")
                if upl is None:
                    continue
                if upl > 0:
                    pg += 1
                elif upl < 0:
                    pl += 1
            rg = sum(1 for _, g, _ in sells_info if g)
            rl = sum(1 for _, _, ll in sells_info if ll)
            yield {"date": rec.get("date", ""), "rg": rg, "rl": rl, "pg": pg, "pl": pl}


def _pgr_plr(events: list[dict[str, int]], idx: Iterable[int] | None = None):
    if idx is None:
        sel = events
    else:
        sel = [events[i] for i in idx]
    RG = sum(e["rg"] for e in sel)
    RL = sum(e["rl"] for e in sel)
    PG = sum(e["pg"] for e in sel)
    PL = sum(e["pl"] for e in sel)
    pgr = RG / (RG + PG) if (RG + PG) > 0 else None
    plr = RL / (RL + PL) if (RL + PL) > 0 else None
    return pgr, plr, RG, RL, PG, PL


def compute_rq2(
    all_records: dict[str, list[dict[str, Any]]],
    regime_map: dict[str, str],
    model_keys: list[str],
    n_resamples: int = DEFAULT_N_RESAMPLES,
) -> dict[str, Any]:
    """Disposition effect: are gains realized at a higher rate than losses?

    PGR = realized gains / (realized gains + paper gains)
    PLR = realized losses / (realized losses + paper losses)
    Disposition difference = PGR - PLR  (Odean's measure; > 0 => disposition)
    Disposition ratio      = PGR / PLR
    Counted at the sale-record level; realized sign from replayed avg cost,
    paper sign from the post-record portfolio_after snapshot.
    """
    per_model: dict[str, Any] = {}
    pooled_events: list[dict[str, int]] = []
    for key in model_keys:
        events = list(_replay_avg_cost(all_records.get(key, [])))
        for e in events:
            e["regime"] = regime_map.get(e["date"], INSUFFICIENT)
        pooled_events.extend(events)
        pgr, plr, RG, RL, PG, PL = _pgr_plr(events)
        entry: dict[str, Any] = {
            "n_sale_records": len(events),
            "realized_gains": RG, "realized_losses": RL,
            "paper_gains": PG, "paper_losses": PL,
            "PGR": pgr, "PLR": plr,
            "disposition_difference": (pgr - plr) if (pgr is not None and plr is not None) else None,
            "disposition_ratio": (pgr / plr) if (pgr is not None and plr is not None and plr > 0) else None,
        }
        if len(events) >= 5 and entry["disposition_difference"] is not None:
            boot = _bootstrap_index_ci(
                len(events),
                lambda idx: (lambda r: (r[0] - r[1]) if (r[0] is not None and r[1] is not None) else 0.0)(_pgr_plr(events, idx)[:2]),
                n_resamples=n_resamples,
            )
            entry["ci_difference"] = {"low": boot["ci_low"], "high": boot["ci_high"]}
            entry["p_value"] = boot["p_value"]
        else:
            entry["ci_difference"] = None
            entry["p_value"] = None
        # regime breakdown
        by_regime: dict[str, Any] = {}
        for regime in ALL_REGIMES:
            rev = [e for e in events if e["regime"] == regime]
            if len(rev) < 5:
                continue
            rp, rl_, *_ = _pgr_plr(rev)
            if rp is not None and rl_ is not None:
                by_regime[regime] = {"n": len(rev), "PGR": rp, "PLR": rl_,
                                     "disposition_difference": rp - rl_}
        entry["by_regime"] = by_regime
        per_model[key] = entry

    # Pooled across models (the RQ2 headline test)
    pgr, plr, RG, RL, PG, PL = _pgr_plr(pooled_events)
    pooled = {
        "n_sale_records": len(pooled_events),
        "PGR": pgr, "PLR": plr,
        "disposition_difference": (pgr - plr) if (pgr is not None and plr is not None) else None,
        "disposition_ratio": (pgr / plr) if (pgr is not None and plr is not None and plr > 0) else None,
    }
    headline_p = None
    if len(pooled_events) >= 5 and pooled["disposition_difference"] is not None:
        boot = _bootstrap_index_ci(
            len(pooled_events),
            lambda idx: (lambda r: (r[0] - r[1]) if (r[0] is not None and r[1] is not None) else 0.0)(_pgr_plr(pooled_events, idx)[:2]),
            n_resamples=n_resamples,
        )
        pooled["ci_difference"] = {"low": boot["ci_low"], "high": boot["ci_high"]}
        headline_p = boot["p_value"]
    pooled["p_value"] = headline_p

    return {
        "status": "Testing" if len(pooled_events) >= 5 else "Open",
        "pooled": pooled,
        "per_model": per_model,
        "headline_p_value": headline_p,
        "interpretation": "Positive PGR-PLR means a model sells winners faster than losers.",
    }


# ==========================================================================
# RQ3 — Confidence calibration on closed (full-exit) trades
# ==========================================================================

def _closed_trades(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replay to extract fully-closed positions with entry confidence + outcome.

    A position counts as closed ONLY when a SELL takes residual shares below
    SHARES_EPSILON. Partial trims never close a position (pre-registered).
    Entry confidence is share-weighted across the buys composing the position.
    Outcome = realized P&L > 0 over the full life of the position.
    """
    shares: dict[str, float] = defaultdict(float)
    cost_basis: dict[str, float] = defaultdict(float)        # Σ shares*price (buys)
    proceeds: dict[str, float] = defaultdict(float)          # Σ shares*price (sells)
    entry_date: dict[str, str] = {}
    closed: list[dict[str, Any]] = []

    for rec in records:
        for ex in _executed_trades(rec):
            t = ex["ticker"]
            price = float(ex.get("fill_price") or 0.0)
            qty = float(ex.get("shares") or 0.0)
            if ex["side"] == "BUY":
                if shares[t] < SHARES_EPSILON:
                    entry_date[t] = rec.get("date", "")
                shares[t] += qty
                cost_basis[t] += qty * price
            else:  # SELL
                proceeds[t] += qty * price
                shares[t] -= qty
                if shares[t] < SHARES_EPSILON:
                    # full exit -> close the position. Realized P&L over the
                    # whole life = total sell proceeds - total buy cost basis.
                    realized = proceeds[t] - cost_basis[t]
                    closed.append({
                        "ticker": t,
                        "entry_date": entry_date.get(t, rec.get("date", "")),
                        "exit_date": rec.get("date", ""),
                        "realized_pnl": realized,
                        "profitable": 1 if realized > 0 else 0,
                    })
                    shares[t] = 0.0
                    cost_basis[t] = 0.0
                    proceeds[t] = 0.0
                    entry_date.pop(t, None)
    # Entry confidence is share-weighted across each position's buys; computed
    # in a focused second pass so re-entries after a full exit stay independent.
    return _attach_entry_confidence(records, closed)


def _attach_entry_confidence(records: list[dict[str, Any]],
                             closed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Second pass: compute share-weighted entry confidence per closed trade.

    Matches closed trades in order of exit; tracks bought shares + conf*shares
    per ticker, resetting on each full exit so re-entries are independent.
    """
    shares: dict[str, float] = defaultdict(float)
    bought: dict[str, float] = defaultdict(float)        # Σ qty over buys in this life
    conf_w: dict[str, float] = defaultdict(float)        # Σ conf*qty over buys
    queue: dict[str, list[float]] = defaultdict(list)    # entry confidences per ticker, FIFO of lives
    for rec in records:
        for ex in _executed_trades(rec):
            t = ex["ticker"]
            qty = float(ex.get("shares") or 0.0)
            if ex["side"] == "BUY":
                shares[t] += qty
                bought[t] += qty
                conf = (ex.get("decision") or {}).get("confidence")
                if conf is not None:
                    conf_w[t] += float(conf) * qty
            else:
                shares[t] -= qty
                if shares[t] < SHARES_EPSILON:
                    ec = (conf_w[t] / bought[t]) if bought[t] > 0 else None
                    queue[t].append(ec if ec is not None else float("nan"))
                    shares[t] = 0.0
                    bought[t] = 0.0
                    conf_w[t] = 0.0
    # assign back, FIFO per ticker
    cursor: dict[str, int] = defaultdict(int)
    for tr in closed:
        t = tr["ticker"]
        ecs = queue.get(t, [])
        i = cursor[t]
        ec = ecs[i] if i < len(ecs) else float("nan")
        cursor[t] += 1
        if ec is not None and math.isfinite(ec):
            tr["entry_confidence"] = int(round(ec))
            tr["entry_confidence_raw"] = float(ec)
        else:
            tr["entry_confidence"] = None
        tr.pop("_cost_basis", None)
        tr.pop("_conf_weight", None)
    return closed


def _calibration_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Calibration curve, Brier score, and ECE from closed trades."""
    usable = [t for t in trades if t.get("entry_confidence") is not None]
    n = len(usable)
    if n == 0:
        return {"n": 0}
    buckets = []
    ece = 0.0
    brier = 0.0
    for t in usable:
        p_pred = t["entry_confidence"] / 10.0
        brier += (p_pred - t["profitable"]) ** 2
    brier /= n
    for k in range(1, 11):
        grp = [t for t in usable if t["entry_confidence"] == k]
        if grp:
            acc = float(np.mean([t["profitable"] for t in grp]))
            buckets.append({"confidence": k, "p_profitable": round(acc, 4),
                            "predicted": k / 10.0, "count": len(grp)})
            ece += (len(grp) / n) * abs(acc - k / 10.0)
        else:
            buckets.append({"confidence": k, "p_profitable": None,
                            "predicted": k / 10.0, "count": 0})
    # point-biserial correlation between confidence and profitable outcome
    confs = np.array([t["entry_confidence"] for t in usable], dtype=float)
    outs = np.array([t["profitable"] for t in usable], dtype=float)
    corr = None
    if confs.std() > 0 and outs.std() > 0:
        corr = float(np.corrcoef(confs, outs)[0, 1])
    return {"n": n, "buckets": buckets, "brier_score": round(brier, 4),
            "expected_calibration_error": round(ece, 4),
            "confidence_outcome_corr": corr,
            "overall_hit_rate": round(float(outs.mean()), 4)}


def compute_rq3(
    all_records: dict[str, list[dict[str, Any]]],
    regime_map: dict[str, str],
    model_keys: list[str],
    n_resamples: int = DEFAULT_N_RESAMPLES,
) -> dict[str, Any]:
    """Are self-reported confidence scores calibrated to closed-trade outcomes?"""
    per_model: dict[str, Any] = {}
    for key in model_keys:
        closed = _closed_trades(all_records.get(key, []))
        for tr in closed:
            tr["regime"] = regime_map.get(tr["exit_date"], INSUFFICIENT)
        stats = _calibration_stats(closed)
        n = stats.get("n", 0)
        entry: dict[str, Any] = {
            "n_closed_trades": n,
            "min_required": MIN_CLOSED_TRADES_RQ3,
            "sufficient": n >= MIN_CLOSED_TRADES_RQ3,
        }
        if n > 0:
            entry.update({
                "brier_score": stats["brier_score"],
                "expected_calibration_error": stats["expected_calibration_error"],
                "confidence_outcome_corr": stats["confidence_outcome_corr"],
                "overall_hit_rate": stats["overall_hit_rate"],
                "calibration_curve": stats["buckets"],
            })
            # CI + p on the confidence/outcome correlation (bootstrap over trades)
            usable = [t for t in closed if t.get("entry_confidence") is not None]
            if len(usable) >= 5:
                confs = np.array([t["entry_confidence"] for t in usable], dtype=float)
                outs = np.array([t["profitable"] for t in usable], dtype=float)

                def _corr(idx: np.ndarray) -> float:
                    c = confs[idx]; o = outs[idx]
                    if c.std() == 0 or o.std() == 0:
                        return 0.0
                    return float(np.corrcoef(c, o)[0, 1])

                boot = _bootstrap_index_ci(len(usable), _corr, n_resamples=n_resamples)
                entry["corr_ci"] = {"low": boot["ci_low"], "high": boot["ci_high"]}
                entry["p_value"] = boot["p_value"]
            # regime breakdown
            by_regime: dict[str, Any] = {}
            for regime in ALL_REGIMES:
                grp = [t for t in closed if t["regime"] == regime]
                if len(grp) >= 5:
                    rs = _calibration_stats(grp)
                    by_regime[regime] = {"n": rs["n"], "ece": rs.get("expected_calibration_error"),
                                         "hit_rate": rs.get("overall_hit_rate")}
            entry["by_regime"] = by_regime
        per_model[key] = entry

    # Headline: pooled confidence/outcome correlation across all models
    all_closed: list[dict[str, Any]] = []
    for key in model_keys:
        cl = _closed_trades(all_records.get(key, []))
        all_closed.extend(cl)
    pooled_stats = _calibration_stats(all_closed)
    headline_p = None
    pooled_corr = pooled_stats.get("confidence_outcome_corr")
    usable = [t for t in all_closed if t.get("entry_confidence") is not None]
    if len(usable) >= 5:
        confs = np.array([t["entry_confidence"] for t in usable], dtype=float)
        outs = np.array([t["profitable"] for t in usable], dtype=float)

        def _corr(idx: np.ndarray) -> float:
            c = confs[idx]; o = outs[idx]
            if c.std() == 0 or o.std() == 0:
                return 0.0
            return float(np.corrcoef(c, o)[0, 1])

        boot = _bootstrap_index_ci(len(usable), _corr, n_resamples=n_resamples)
        headline_p = boot["p_value"]

    return {
        "status": "Testing" if pooled_stats.get("n", 0) >= MIN_CLOSED_TRADES_RQ3 else "Open",
        "pooled": {
            "n_closed_trades": pooled_stats.get("n", 0),
            "confidence_outcome_corr": pooled_corr,
            "brier_score": pooled_stats.get("brier_score"),
            "expected_calibration_error": pooled_stats.get("expected_calibration_error"),
        },
        "per_model": per_model,
        "headline_p_value": headline_p,
        "interpretation": "Positive confidence/outcome correlation and low ECE indicate calibrated confidence.",
    }


# ==========================================================================
# RQ4 — Style-factor tilts (Fama-French 5 + momentum)
# ==========================================================================

FACTORS_DIR = DATA_DIR / "factors"
_FF5_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
_MOM_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip"


def _download_ff_csv(url: str) -> list[str] | None:
    """Download a Ken French daily-factor zip and return its CSV lines."""
    import io
    import urllib.request
    import zipfile
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            blob = resp.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            name = zf.namelist()[0]
            return zf.read(name).decode("latin-1").splitlines()
    except Exception:
        logger.exception("Fama-French factor download failed: %s", url)
        return None


def _parse_ff_daily(lines: list[str], col_names: list[str]) -> dict[str, dict[str, float]]:
    """Parse daily YYYYMMDD rows from a Ken French CSV into {date: {factor: val}}.

    Values are in percent in the source files; converted to decimals here.
    Stops at the first non-daily row (annual section / blank line).
    """
    out: dict[str, dict[str, float]] = {}
    row_re = re.compile(r"^\s*(\d{8})\s*,(.*)$")
    for line in lines:
        m = row_re.match(line)
        if not m:
            continue
        ymd = m.group(1)
        parts = [p.strip() for p in m.group(2).split(",")]
        try:
            vals = [float(p) / 100.0 for p in parts[:len(col_names)]]
        except ValueError:
            continue
        if len(vals) < len(col_names):
            continue
        date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        out[date] = dict(zip(col_names, vals))
    return out


def load_ff_factors(use_cache: bool = True) -> dict[str, dict[str, float]] | None:
    """Return {date: {Mkt-RF, SMB, HML, RMW, CMA, RF, MOM}} or None if unavailable."""
    FACTORS_DIR.mkdir(parents=True, exist_ok=True)
    cache = FACTORS_DIR / "ff_factors_daily.json"
    if use_cache and cache.exists():
        try:
            with open(cache, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data:
                return data
        except Exception:
            logger.exception("Failed reading FF factor cache; refetching")

    ff5_lines = _download_ff_csv(_FF5_URL)
    mom_lines = _download_ff_csv(_MOM_URL)
    if not ff5_lines:
        return None
    ff5 = _parse_ff_daily(ff5_lines, ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"])
    mom = _parse_ff_daily(mom_lines, ["MOM"]) if mom_lines else {}
    merged: dict[str, dict[str, float]] = {}
    for date, row in ff5.items():
        r = dict(row)
        if date in mom:
            r["MOM"] = mom[date]["MOM"]
        merged[date] = r
    if merged:
        try:
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(merged, f)
        except Exception:
            logger.exception("Failed writing FF factor cache")
    return merged or None


def _ols(y: np.ndarray, X: np.ndarray) -> dict[str, Any]:
    """OLS with intercept already in X. Returns betas, std errs, t-stats, R²."""
    n, k = X.shape
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = max(1, n - k)
    sigma2 = float(resid @ resid) / dof
    try:
        xtx_inv = np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        xtx_inv = np.linalg.pinv(X.T @ X)
    se = np.sqrt(np.maximum(0.0, np.diag(sigma2 * xtx_inv)))
    tstat = np.divide(beta, se, out=np.zeros_like(beta), where=se > 0)
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    # normal-approx two-sided p-values (small-sample: conservative to treat as t,
    # but with no scipy we use the normal approx and flag it)
    pvals = [2.0 * (1.0 - norm_cdf(abs(t))) for t in tstat]
    return {"beta": beta, "se": se, "tstat": tstat, "pval": pvals, "r2": r2, "n": n, "dof": dof}


def _model_daily_returns(model_key: str) -> dict[str, float]:
    """EOD daily returns per date (deduped to one EOD row per date)."""
    df = load_performance_history(model_key)
    if df.empty or len(df) < 2:
        return {}
    df = df.copy()
    df["date_str"] = df["date"].dt.strftime("%Y-%m-%d")
    eod = df.groupby("date_str", sort=True).last().reset_index()
    if len(eod) < 2:
        return {}
    vals = eod["total_value"].astype(float).values
    rets = np.diff(vals) / vals[:-1]
    dates = eod["date_str"].iloc[1:].tolist()
    return dict(zip(dates, rets.tolist()))


def compute_rq4(
    regime_map: dict[str, str],
    model_keys: list[str],
) -> dict[str, Any]:
    """Regress each model's daily excess returns on FF5 + momentum factors."""
    factors = load_ff_factors()
    if not factors:
        return {"status": "Open", "note": "Fama-French factor data unavailable "
                "(offline or source unreachable). Regression deferred.",
                "per_model": {}}

    factor_cols = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "MOM"]
    per_model: dict[str, Any] = {}
    for key in model_keys:
        rets = _model_daily_returns(key)
        rows_y: list[float] = []
        rows_x: list[list[float]] = []
        used_dates: list[str] = []
        for date, r in sorted(rets.items()):
            f = factors.get(date)
            if not f or any(c not in f for c in factor_cols) or "RF" not in f:
                continue
            rows_y.append(r - f["RF"])               # excess return
            rows_x.append([1.0] + [f[c] for c in factor_cols])
            used_dates.append(date)
        n = len(rows_y)
        if n < 10:
            per_model[key] = {"n_days": n, "status": "insufficient",
                              "note": "Need >=10 aligned factor days."}
            continue
        y = np.array(rows_y)
        X = np.array(rows_x)
        fit = _ols(y, X)
        names = ["alpha"] + factor_cols
        per_model[key] = {
            "n_days": n,
            "alpha_daily": float(fit["beta"][0]),
            "alpha_annualized": float(fit["beta"][0] * 252),
            "alpha_tstat": float(fit["tstat"][0]),
            "alpha_pvalue": float(fit["pval"][0]),
            "betas": {names[i]: float(fit["beta"][i]) for i in range(1, len(names))},
            "tstats": {names[i]: float(fit["tstat"][i]) for i in range(1, len(names))},
            "pvalues": {names[i]: float(fit["pval"][i]) for i in range(1, len(names))},
            "r_squared": fit["r2"],
            "pvalue_method": "normal-approx (no scipy); treat as approximate at small n",
        }

    # Headline: is any model's alpha distinguishable from zero? Use the smallest
    # alpha p-value across models as the family entry (exploratory at this stage).
    alpha_ps = [m["alpha_pvalue"] for m in per_model.values() if "alpha_pvalue" in m]
    headline_p = min(alpha_ps) if alpha_ps else None
    return {
        "status": "Testing" if alpha_ps else "Open",
        "factor_model": "Fama-French 5 (Mkt-RF, SMB, HML, RMW, CMA) + Momentum",
        "per_model": per_model,
        "headline_p_value": headline_p,
        "power_note": ("Live phase covers a single regime; factor loadings are "
                       "pilot-grade until cross-regime data exists. See "
                       "docs/BACKTEST_HARNESS_SCOPE.md."),
        "interpretation": "Significant betas reveal persistent size/value/quality/momentum tilts.",
    }


# ==========================================================================
# RQ5 — Behavioral response to portfolio drawdowns
# ==========================================================================

def _daily_portfolio_features(records: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Per trading date: HHI, cash%, num positions, avg weight, daily turnover.

    Concentration/sizing come from the LAST record's portfolio_after that day;
    turnover sums executed BUY/SELL notional that day over EOD total value.
    """
    by_date: dict[str, dict[str, Any]] = {}
    for rec in records:
        d = rec.get("date")
        if not d:
            continue
        slot = by_date.setdefault(d, {"turnover_notional": 0.0, "last_pa": None, "last_ts": ""})
        for ex in _executed_trades(rec):
            slot["turnover_notional"] += abs(float(ex.get("notional") or 0.0))
        ts = rec.get("timestamp", "")
        if rec.get("portfolio_after") and ts >= slot["last_ts"]:
            slot["last_pa"] = rec["portfolio_after"]
            slot["last_ts"] = ts

    out: dict[str, dict[str, float]] = {}
    for d, slot in by_date.items():
        pa = slot["last_pa"]
        if not pa:
            continue
        holdings = pa.get("holdings", [])
        weights = [float(h.get("weight") or 0.0) for h in holdings]
        total_value = float(pa.get("total_value") or 0.0)
        hhi = float(sum(w * w for w in weights)) if weights else 0.0
        avg_w = float(np.mean(weights)) if weights else 0.0
        turnover = (slot["turnover_notional"] / total_value) if total_value > 0 else 0.0
        out[d] = {
            "total_value": total_value,
            "hhi": hhi,
            "cash_pct": float(pa.get("cash_pct") or 0.0),
            "num_positions": float(len(holdings)),
            "avg_position_size": avg_w,
            "turnover": turnover,
        }
    return out


def _drawdown_flags(dates: list[str], values: list[float]) -> list[bool]:
    """In-drawdown if EOD value <= (1 + threshold) * trailing 60-day peak."""
    flags = []
    for i in range(len(values)):
        lo = max(0, i - RQ5_PEAK_WINDOW + 1)
        peak = max(values[lo:i + 1]) if values[lo:i + 1] else values[i]
        dd = (values[i] / peak - 1.0) if peak > 0 else 0.0
        flags.append(dd <= RQ5_DRAWDOWN_THRESHOLD)
    return flags


def compute_rq5(
    all_records: dict[str, list[dict[str, Any]]],
    regime_map: dict[str, str],
    model_keys: list[str],
    n_resamples: int = DEFAULT_N_RESAMPLES,
) -> dict[str, Any]:
    """How does behavior change when a model is >=10% off its own 60-day peak?"""
    metrics = ["hhi", "turnover", "avg_position_size", "cash_pct", "num_positions"]
    per_model: dict[str, Any] = {}
    total_dd_days = 0
    for key in model_keys:
        feats = _daily_portfolio_features(all_records.get(key, []))
        dates = sorted(feats.keys())
        if len(dates) < 5:
            per_model[key] = {"n_days": len(dates), "status": "insufficient"}
            continue
        values = [feats[d]["total_value"] for d in dates]
        dd = _drawdown_flags(dates, values)
        n_dd = int(sum(dd))
        total_dd_days += n_dd
        entry: dict[str, Any] = {"n_days": len(dates), "n_drawdown_days": n_dd}
        if n_dd == 0 or n_dd == len(dates):
            entry["status"] = "no_drawdown_contrast"
            entry["note"] = "No 10% portfolio drawdown observed yet (or always in one)."
            per_model[key] = entry
            continue
        entry["status"] = "Testing"
        comp: dict[str, Any] = {}
        for m in metrics:
            in_vals = np.array([feats[d][m] for d, f in zip(dates, dd) if f])
            out_vals = np.array([feats[d][m] for d, f in zip(dates, dd) if not f])
            delta = float(in_vals.mean() - out_vals.mean())
            # bootstrap the difference of means across the day index
            allv = np.array([feats[d][m] for d in dates])
            ddmask = np.array(dd)

            def _diff(idx: np.ndarray, _v=allv, _m=ddmask) -> float:
                sv = _v[idx]; sm = _m[idx]
                if sm.sum() == 0 or (~sm).sum() == 0:
                    return 0.0
                return float(sv[sm].mean() - sv[~sm].mean())

            boot = _bootstrap_index_ci(len(dates), _diff, n_resamples=min(n_resamples, 5000))
            comp[m] = {
                "in_drawdown_mean": float(in_vals.mean()),
                "normal_mean": float(out_vals.mean()),
                "delta": delta,
                "ci_low": boot["ci_low"], "ci_high": boot["ci_high"],
                "p_value": boot["p_value"],
            }
        entry["metrics"] = comp
        per_model[key] = entry

    return {
        "status": "Testing" if total_dd_days > 0 else "Open",
        "drawdown_threshold": RQ5_DRAWDOWN_THRESHOLD,
        "peak_window_days": RQ5_PEAK_WINDOW,
        "total_drawdown_days_across_models": total_dd_days,
        "per_model": per_model,
        "interpretation": ("Compares concentration (HHI), turnover, position size, "
                           "and cash between drawdown and normal days."),
    }


# ==========================================================================
# RQ6 — Non-determinism at temperature = 0 (manual probe analyzer)
# ==========================================================================

def _load_determinism_records() -> list[dict[str, Any]]:
    if not DETERMINISM_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for fp in sorted(DETERMINISM_DIR.glob("*.jsonl")):
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def compute_rq6(model_keys: list[str]) -> dict[str, Any]:
    """Quantify decision non-determinism across K temperature=0 reruns.

    Reads probe output from /data/determinism/*.jsonl. Each line is one rerun:
      {model_key, tick_id, run_index, temperature, decisions:[{ticker,action,
       target_weight,confidence}], api_success}

    Per (model, tick, ticker) we measure whether all K runs agree on the
    action; the decision-flip rate is the fraction that do NOT. Also reports
    mean normalized action entropy and target-weight dispersion.
    """
    records = _load_determinism_records()
    if not records:
        return {"status": "Open",
                "note": ("No determinism-probe data yet. RQ6 is manual-trigger: "
                         "run scripts/determinism_probe.py to generate K reruns "
                         "at temperature=0 over a 5% subsample."),
                "per_model": {}}

    # group: (model, tick) -> list of run decision dicts {ticker: (action, weight)}
    groups: dict[tuple[str, str], list[dict[str, tuple[str, float]]]] = defaultdict(list)
    temps: set[float] = set()
    for r in records:
        if not r.get("api_success", True):
            continue
        mk = r.get("model_key")
        tick = str(r.get("tick_id") or r.get("data_inputs_hash") or "")
        if not mk or not tick:
            continue
        if r.get("temperature") is not None:
            temps.add(float(r["temperature"]))
        dec = {}
        for d in r.get("decisions", []):
            t = str(d.get("ticker", "")).upper().strip()
            a = str(d.get("action", "")).upper()
            if t and a:
                try:
                    w = float(d.get("target_weight", 0.0))
                except (TypeError, ValueError):
                    w = 0.0
                dec[t] = (a, w)
        groups[(mk, tick)].append(dec)

    per_model: dict[str, Any] = {}
    for key in model_keys:
        flips = 0
        total = 0
        entropies: list[float] = []
        weight_stds: list[float] = []
        k_runs: list[int] = []
        for (mk, tick), runs in groups.items():
            if mk != key or len(runs) < 2:
                continue
            k_runs.append(len(runs))
            tickers = set().union(*[set(r.keys()) for r in runs])
            for t in tickers:
                actions = [r[t][0] for r in runs if t in r]
                if len(actions) < 2:
                    continue
                total += 1
                counts = defaultdict(int)
                for a in actions:
                    counts[a] += 1
                if len(counts) > 1:
                    flips += 1
                # normalized entropy
                n = len(actions)
                ent = -sum((c / n) * math.log(c / n) for c in counts.values())
                max_ent = math.log(min(3, n)) if n > 1 else 1.0
                entropies.append(ent / max_ent if max_ent > 0 else 0.0)
                weights = [r[t][1] for r in runs if t in r]
                if len(weights) >= 2:
                    weight_stds.append(float(np.std(weights)))
        if total == 0:
            per_model[key] = {"n_decisions": 0, "status": "no_data"}
            continue
        per_model[key] = {
            "n_tick_ticker_decisions": total,
            "mean_runs_per_tick": round(float(np.mean(k_runs)), 2) if k_runs else None,
            "decision_flip_rate": round(flips / total, 4),
            "mean_normalized_entropy": round(float(np.mean(entropies)), 4) if entropies else 0.0,
            "mean_target_weight_std": round(float(np.mean(weight_stds)), 5) if weight_stds else 0.0,
        }

    overall_flip = None
    flips_all = [m["decision_flip_rate"] for m in per_model.values() if "decision_flip_rate" in m]
    if flips_all:
        overall_flip = round(float(np.mean(flips_all)), 4)
    return {
        "status": "Testing",
        "temperatures_observed": sorted(temps),
        "overall_decision_flip_rate": overall_flip,
        "per_model": per_model,
        "interpretation": ("A non-zero flip rate at temperature=0 means identical "
                           "inputs yield different decisions — irreducible API "
                           "non-determinism that bounds RQ1's measurable convergence."),
    }


# ==========================================================================
# Driver
# ==========================================================================

def compute_all_research_metrics(
    settings: dict[str, Any] | None = None,
    with_regime: bool = True,
    n_permutations: int = 500,
    n_resamples: int = DEFAULT_N_RESAMPLES,
) -> dict[str, Any]:
    """Compute all six RQ metrics + the BH-FDR family. One regime fetch."""
    settings = settings or load_settings()
    model_keys = get_model_keys(settings)
    all_records = {k: load_decision_records(k) for k in model_keys}
    dates = _collect_dates(all_records)
    regime_map, regime_summary = _build_regime_map(dates, with_regime)

    rq1 = compute_rq1(all_records, regime_map, model_keys,
                      n_permutations=n_permutations, n_resamples=n_resamples)
    rq2 = compute_rq2(all_records, regime_map, model_keys, n_resamples=n_resamples)
    rq3 = compute_rq3(all_records, regime_map, model_keys, n_resamples=n_resamples)
    rq4 = compute_rq4(regime_map, model_keys)
    rq5 = compute_rq5(all_records, regime_map, model_keys, n_resamples=n_resamples)
    rq6 = compute_rq6(model_keys)

    # Benjamini-Hochberg across the pre-registered primary tests (RQ6 is
    # descriptive, not a frequentist test, so it is excluded from the family).
    family = [
        ("RQ1_convergence", rq1.get("headline_p_value")),
        ("RQ2_disposition", rq2.get("headline_p_value")),
        ("RQ3_calibration", rq3.get("headline_p_value")),
        ("RQ4_alpha", rq4.get("headline_p_value")),
        ("RQ5_drawdown_response", rq5.get("headline_p_value")),
    ]
    tested = [(lab, p) for lab, p in family if p is not None]
    fdr_block: dict[str, Any] = {"q": DEFAULT_FDR_Q, "note": "Primary tests only; "
                                 "per-model sub-tests are exploratory."}
    if tested:
        fdr = benjamini_hochberg([p for _, p in tested], q=DEFAULT_FDR_Q,
                                 labels=[lab for lab, _ in tested])
        fdr_block.update({
            "tests": [
                {"label": lab, "p_value": fdr.p_values[i],
                 "p_adjusted": round(fdr.p_adjusted[i], 5), "reject_null": fdr.reject[i]}
                for i, lab in enumerate(fdr.labels)
            ],
            "n_significant": fdr.n_significant,
            "critical_p": fdr.critical_p,
            "significant": fdr.significant_labels(),
        })
    else:
        fdr_block["tests"] = []
        fdr_block["note"] += " No tests have enough data yet."

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_phase": settings.get("phase", "—"),
        "mode": settings.get("mode", "—"),
        "data_window": {
            "first_date": dates[0] if dates else None,
            "last_date": dates[-1] if dates else None,
            "n_trading_dates": len(dates),
            "models": model_keys,
        },
        "regime_summary": regime_summary,
        "config": {
            "block_length": DEFAULT_BLOCK_LENGTH,
            "n_resamples": n_resamples,
            "n_permutations": n_permutations,
            "fdr_q": DEFAULT_FDR_Q,
            "min_closed_trades_rq3": MIN_CLOSED_TRADES_RQ3,
            "rq5_drawdown_threshold": RQ5_DRAWDOWN_THRESHOLD,
        },
        "RQ1": rq1, "RQ2": rq2, "RQ3": rq3, "RQ4": rq4, "RQ5": rq5, "RQ6": rq6,
        "fdr_correction": fdr_block,
    }


def main() -> int:
    import argparse
    from ..config_loader import configure_logging

    configure_logging()
    parser = argparse.ArgumentParser(description="Compute LLM Trading Lab research metrics")
    parser.add_argument("--output", default=None, help="Write JSON here (default: data/research/metrics_<date>.json)")
    parser.add_argument("--no-regime", action="store_true", help="Skip SPY regime stratification")
    parser.add_argument("--permutations", type=int, default=500)
    parser.add_argument("--resamples", type=int, default=DEFAULT_N_RESAMPLES)
    parser.add_argument("--quiet", action="store_true", help="Don't print the summary")
    args = parser.parse_args()

    result = compute_all_research_metrics(
        with_regime=not args.no_regime,
        n_permutations=args.permutations,
        n_resamples=args.resamples,
    )

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.output) if args.output else (
        RESEARCH_DIR / f"metrics_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)

    if not args.quiet:
        dw = result["data_window"]
        print(f"\nLLM Trading Lab -- Research Metrics")
        print(f"Window: {dw['first_date']} -> {dw['last_date']} ({dw['n_trading_dates']} days, "
              f"{len(dw['models'])} models)")
        rq1 = result["RQ1"]
        print(f"\nRQ1 convergence: concordance={rq1.get('observed_action_concordance')} "
              f"vs null={rq1.get('null_action_concordance_mean')} "
              f"(p={rq1.get('permutation_p_value')})")
        rq2 = result["RQ2"]["pooled"]
        print(f"RQ2 disposition: PGR={rq2.get('PGR')} PLR={rq2.get('PLR')} "
              f"diff={rq2.get('disposition_difference')}")
        rq3 = result["RQ3"]["pooled"]
        print(f"RQ3 calibration: n_closed={rq3.get('n_closed_trades')} "
              f"corr={rq3.get('confidence_outcome_corr')} ECE={rq3.get('expected_calibration_error')}")
        print(f"RQ4 status: {result['RQ4']['status']}")
        print(f"RQ5 drawdown days: {result['RQ5'].get('total_drawdown_days_across_models')}")
        print(f"RQ6 status: {result['RQ6']['status']}")
        fdr = result["fdr_correction"]
        print(f"\nBH-FDR (q={fdr['q']}): significant={fdr.get('significant', [])}")
        print(f"\nWritten: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
