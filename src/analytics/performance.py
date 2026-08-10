"""Performance metrics: returns, Sharpe, drawdown, alpha, leaderboard.

Reads from /data/performance/{model}.jsonl which is appended once per daily run.
All metrics are deterministic + cheap to recompute, so we just regen on every run.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config_loader import PERFORMANCE_DIR, TRADES_DIR, load_settings


def load_performance_history(model_key: str) -> pd.DataFrame:
    path = PERFORMANCE_DIR / f"{model_key}.jsonl"
    if not path.exists():
        return pd.DataFrame()
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


# ===========================================================================
# CANONICAL DISPLAY BASIS
#
# Every display metric (leaderboard, performance table, SPY row, expansion
# cohort) is computed from ONE consistent basis so models never get silently
# mixed across windows:
#   * one EOD row per trading date (dedupe — the launch/intraday-refactor
#     window double-wrote some dates; mirrors research_metrics._model_daily_returns)
#   * sliced to >= the configured inception (drops the pre-inception 2026-04-08
#     shakedown rows five models carry) — from the COMPUTATION only; the raw
#     perf logs on disk are never modified
#   * cumulative return anchored to the initial deployed capital from config
#     (the clean $100k), identically in every consumer
#   * alpha benchmarked against the single canonical SPY series shared by all
#     models, not each model's own benchmark_value column / own anchor
# ===========================================================================

_UNSET = object()  # sentinel so compute_metrics can tell "compute SPY" from "SPY is None"


def _starting_capital(settings: dict[str, Any] | None = None) -> float:
    """Initial deployed capital per model from config — the clean anchor for
    cumulative return. Mode-aware (paper vs live)."""
    settings = settings or load_settings()
    caps = settings.get("starting_capital", {}) or {}
    return float(caps.get(settings.get("mode", "paper"), 100_000.0))


def _experiment_inception(settings: dict[str, Any] | None = None) -> str | None:
    """Canonical experiment start date (YYYY-MM-DD) from config, or None."""
    settings = settings or load_settings()
    return settings.get("experiment_start_date")


def canonical_perf_frame(
    model_key: str, settings: dict[str, Any] | None = None
) -> pd.DataFrame:
    """A model's perf history collapsed to one EOD row per trading date and
    sliced to >= the canonical inception.

    The single basis every display metric is computed from. The dedupe mirrors
    ``research_metrics._model_daily_returns`` (groupby date, keep the last EOD
    snapshot); the inception slice drops pre-inception rows (e.g. the 2026-04-08
    launch-day shakedown rows) from the COMPUTATION only — the raw logs are
    never touched.
    """
    df = load_performance_history(model_key)
    if df.empty:
        return df
    df = df.copy()
    df["date_str"] = df["date"].dt.strftime("%Y-%m-%d")
    df = df.groupby("date_str", sort=True).last().reset_index()
    inception = _experiment_inception(settings)
    if inception:
        df = df[df["date_str"] >= inception].reset_index(drop=True)
    return df


def _spy_inception_anchor() -> tuple[str, float] | None:
    """(date, benchmark_value) inception anchor pinned in the Phase-A integrity
    ledger, or None if absent/malformed.

    The 2026-04-09 perf rows carry three conflicting SPY captures (676.01 seed,
    680.40 first real tick, 679.91 later tick), so the anchor is a pinned fact —
    scripts/phase_a_integrity_ledger.json -> spy_benchmark_anchor — not something
    derived from row ordering. See that entry for the full provenance.
    """
    ledger_path = PERFORMANCE_DIR.parent.parent / "scripts" / "phase_a_integrity_ledger.json"
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            anchor = (json.load(f) or {}).get("spy_benchmark_anchor") or {}
        date = anchor.get("date")
        value = anchor.get("benchmark_value")
        if date and value is not None:
            return str(date), float(value)
    except (OSError, ValueError):
        pass
    return None


def _require_unique_spy_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Reader invariant: the canonical SPY series must have exactly one row per
    date. Halts (raises) rather than silently returning a duplicated series that
    would make bench[0]/bench[-1] anchor non-deterministic again."""
    if not df["date_str"].is_unique:
        dupes = sorted(df["date_str"][df["date_str"].duplicated()].tolist())
        raise ValueError(f"canonical SPY series has duplicate dates after dedup: {dupes}")
    return df


def canonical_spy_series(settings: dict[str, Any] | None = None) -> pd.DataFrame | None:
    """One SPY EOD price series for the whole experiment — DETERMINISTIC.

    Every model logs the same SPY price per tick, so we union all models'
    ``benchmark_value`` observations. The launch window double-wrote some dates
    and carries pre-inception 2026-04-08 shakedown rows, so we:

      * drop pre-inception rows (date < configured inception),
      * dedupe to exactly one row per date deterministically — models iterated in
        sorted order, stable-sorted by date, first capture per date wins. This
        replaces ``groupby(date).last()`` over an unstably-ordered concat, which
        flipped the inception anchor (676.01 / 679.91) between runs,
      * pin the inception anchor to the ledger's ``spy_benchmark_anchor`` (the
        2026-04-09 first-real-tick SPY, 680.40), so bench[0] is a fixed fact
        rather than an emergent property of row order.

    Returns DataFrame[date_str, benchmark_value] sorted by date, or None. The raw
    perf logs on disk are never modified — this is a read-time derivation.
    """
    if not PERFORMANCE_DIR.exists():
        return None
    frames = []
    for fp in sorted(PERFORMANCE_DIR.glob("*.jsonl")):  # sorted -> deterministic concat order
        df = load_performance_history(fp.stem)
        if df.empty or "benchmark_value" not in df.columns:
            continue
        sub = df[["date", "benchmark_value"]].dropna()
        if not sub.empty:
            frames.append(sub)
    if not frames:
        return None
    allrows = pd.concat(frames, ignore_index=True)
    allrows["date_str"] = allrows["date"].dt.strftime("%Y-%m-%d")
    inception = _experiment_inception(settings)
    if inception:
        allrows = allrows[allrows["date_str"] >= inception]
    # Deterministic one-row-per-date: stable-sort by date (preserves per-model
    # append order within a date), then keep the first capture per date.
    allrows = allrows.sort_values("date", kind="stable")
    deduped = allrows.drop_duplicates(subset="date_str", keep="first").reset_index(drop=True)
    # Pin the inception anchor from the ledger (overrides the conflicting 04-09 rows).
    anchor = _spy_inception_anchor()
    if anchor is not None:
        adate, avalue = anchor
        deduped.loc[deduped["date_str"] == adate, "benchmark_value"] = avalue
    if len(deduped) < 1:
        return None
    _require_unique_spy_dates(deduped)  # reader invariant — halt on any residual dup
    return deduped[["date_str", "benchmark_value"]]


def canonical_spy_return(settings: dict[str, Any] | None = None) -> float | None:
    """SPY buy-and-hold return over the canonical inception-anchored window —
    the single SPY return every model's alpha is benchmarked against."""
    spy = canonical_spy_series(settings)
    if spy is None or len(spy) < 2:
        return None
    vals = spy["benchmark_value"].astype(float).values
    if vals[0] <= 0:
        return None
    return float(vals[-1] / vals[0] - 1.0)


def compute_metrics(
    model_key: str,
    settings: dict[str, Any] | None = None,
    spy_return: Any = _UNSET,
) -> dict[str, Any]:
    settings = settings or load_settings()
    starting_capital = _starting_capital(settings)
    if spy_return is _UNSET:
        spy_return = canonical_spy_return(settings)

    # Canonical basis: one EOD row per date, sliced to >= inception.
    df = canonical_perf_frame(model_key, settings)

    def _last_api_success(frame: pd.DataFrame) -> bool:
        if not frame.empty and "api_success" in frame.columns:
            v = frame["api_success"].iloc[-1]
            if v is not None and not pd.isna(v):
                return bool(v)
        return True

    if df.empty or len(df) < 2:
        cur = float(df["total_value"].iloc[-1]) if not df.empty else 0.0
        cum = (cur / starting_capital - 1.0) if (not df.empty and starting_capital > 0) else 0.0
        return {
            "model_key": model_key,
            "days": int(len(df)),
            "cumulative_return": cum,
            "daily_pnl_pct": None,
            "win_rate": None,
            "sharpe_30d": None,
            "sharpe_90d": None,
            "max_drawdown": 0.0,
            "alpha_vs_spy": None,
            "streak_count": 0,
            "streak_type": None,
            "current_value": cur,
            "current_cash_pct": float(df["cash_pct"].iloc[-1]) if not df.empty else 1.0,
            "num_positions": int(df["num_positions"].iloc[-1]) if not df.empty else 0,
            "halted": bool(df["halted"].iloc[-1]) if not df.empty else False,
            "last_api_success": _last_api_success(df),
        }

    values = df["total_value"].astype(float).values
    daily_returns = np.diff(values) / values[:-1]
    # Cumulative return anchored to the initial deployed capital from config
    # (the clean $100k) — identical anchor in the leaderboard and the
    # performance table so the two can never disagree.
    cumulative_return = (values[-1] / starting_capital - 1.0) if starting_capital > 0 else (values[-1] / values[0] - 1.0)

    # Today's % change vs the prior EOD row
    daily_pnl_pct = float(daily_returns[-1]) if len(daily_returns) else None

    # Day-level win rate: fraction of days where the EOD value went up vs the
    # prior day. Trade-level win rate would need realized P&L per closed
    # position, which we don't track yet — day-level is the meaningful proxy
    # given the data we already have.
    win_rate = float((daily_returns > 0).sum() / len(daily_returns)) if len(daily_returns) else None

    # Drawdown
    running_max = np.maximum.accumulate(values)
    drawdowns = (values - running_max) / running_max
    max_drawdown = float(drawdowns.min()) if len(drawdowns) else 0.0

    sharpe_30 = _sharpe(daily_returns[-30:]) if len(daily_returns) >= 5 else None
    sharpe_90 = _sharpe(daily_returns[-90:]) if len(daily_returns) >= 5 else None

    # Alpha vs the single canonical SPY series shared by ALL models — measured
    # on the same inception-anchored window, so alpha == cumulative_return −
    # SPY_return reconciles for every model (Sonnet included).
    alpha = (cumulative_return - spy_return) if spy_return is not None else None

    # Win/loss streak: count consecutive days of same sign from most recent
    streak_count = 0
    streak_type = None  # "W" or "L"
    if len(daily_returns) > 0:
        for ret in reversed(daily_returns):
            if ret > 0:
                direction = "W"
            elif ret < 0:
                direction = "L"
            else:
                break  # flat day breaks the streak
            if streak_type is None:
                streak_type = direction
            if direction == streak_type:
                streak_count += 1
            else:
                break

    return {
        "model_key": model_key,
        "days": int(len(df)),
        "cumulative_return": float(cumulative_return),
        "daily_pnl_pct": daily_pnl_pct,
        "win_rate": win_rate,
        "sharpe_30d": sharpe_30,
        "sharpe_90d": sharpe_90,
        "max_drawdown": max_drawdown,
        "alpha_vs_spy": alpha,
        "streak_count": streak_count,
        "streak_type": streak_type,
        "current_value": float(values[-1]),
        "current_cash_pct": float(df["cash_pct"].iloc[-1]),
        "num_positions": int(df["num_positions"].iloc[-1]),
        "halted": bool(df["halted"].iloc[-1]),
        "last_api_success": _last_api_success(df),
    }


def compute_spy_benchmark_metrics(
    starting_capital: float = 100_000.0,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Synthesize a SPY buy-and-hold portfolio from the canonical SPY series.

    Uses the single canonical SPY series (``canonical_spy_series`` — deduped,
    inception-sliced) that every model's alpha is benchmarked against, treating
    SPY as a single-position portfolio bought at inception with
    `starting_capital` and held without trading. Because it shares that series,
    this row's cumulative return is exactly the figure each model's alpha
    reconciles to (alpha == model_cum − this_cum).

    Returns None if no perf log has a usable benchmark_value series yet.
    The output shape matches `compute_metrics()` so the leaderboard table
    can render it as a regular row alongside the model rows.
    """
    # The ONE canonical SPY series (deduped, inception-sliced) — the same
    # series every model's alpha is benchmarked against, so this row's
    # cumulative return is exactly the figure each model's alpha reconciles to.
    series = canonical_spy_series(settings)
    if series is None or len(series) < 2:
        return None

    bench = series["benchmark_value"].astype(float).values
    base_price = bench[0]
    if base_price <= 0:
        return None
    shares = starting_capital / base_price
    values = bench * shares  # synthetic equity curve, anchored to deployed capital

    daily_returns = np.diff(values) / values[:-1]
    cumulative_return = float(values[-1] / values[0] - 1.0)
    daily_pnl_pct = float(daily_returns[-1]) if len(daily_returns) else None
    win_rate = float((daily_returns > 0).sum() / len(daily_returns)) if len(daily_returns) else None

    running_max = np.maximum.accumulate(values)
    drawdowns = (values - running_max) / running_max
    max_drawdown = float(drawdowns.min()) if len(drawdowns) else 0.0

    sharpe_30 = _sharpe(daily_returns[-30:]) if len(daily_returns) >= 5 else None
    sharpe_90 = _sharpe(daily_returns[-90:]) if len(daily_returns) >= 5 else None

    return {
        "model_key": "spy_benchmark",
        "days": int(len(values)),
        "cumulative_return": cumulative_return,
        "daily_pnl_pct": daily_pnl_pct,
        "win_rate": win_rate,
        "sharpe_30d": sharpe_30,
        "sharpe_90d": sharpe_90,
        "max_drawdown": max_drawdown,
        "alpha_vs_spy": 0.0,            # benchmark vs itself
        "streak_count": 0,
        "streak_type": None,
        "current_value": float(values[-1]),
        "current_cash_pct": 0.0,
        "num_positions": 1,
        "halted": False,
        "last_api_success": True,
    }


def reprice_record_usd(rec: dict[str, Any]) -> dict[str, Any]:
    """Reprice one decision-log record at the rates in force on its date.

    Read-time repricing (2026-08-05 rider 1). The stored ``cost_usd`` /
    ``screening_cost_usd`` fields are NOT read for the total: they were
    computed at call time against whatever table was live then, and four of
    the six rates in the pre-2026-08 table were never a published list price
    (see CORRECTIONS.md). The JSONL history stays immutable — we recompute
    from the stored token counts and the record's own date, so a call is
    always priced at the rate in force when it ran.

    Screening calls store output tokens but not input tokens for records
    written before 2026-08-05. Those are back-solved from the logged screening
    cost against the frozen legacy table (exact to ±0.2 tokens); records from
    2026-08-05 onward carry ``screening_input_tokens`` and are priced directly.

    Returns:
        {
          "decision_usd": float | None,   # None = no rate for that model/date
          "screening_usd": float | None,  # None = no screening call, or unpriceable
          "screening_backsolved": bool,   # True if input tokens were inverted
        }
    """
    from .cost_rates import backsolve_screening_input_tokens, compute_call_cost_usd

    model_id = rec.get("model_id_returned") or rec.get("model_id_configured") or ""
    on_date = rec.get("date")

    dec_in = int(rec.get("input_tokens") or 0)
    dec_out = int(rec.get("output_tokens") or 0)
    decision_usd = (
        compute_call_cost_usd(model_id, dec_in, dec_out, on_date=on_date)
        if (dec_in > 0 or dec_out > 0)
        else None
    )

    screening_usd = None
    backsolved = False
    scr_out = rec.get("screening_tokens")
    if scr_out is not None:
        scr_in = rec.get("screening_input_tokens")
        if scr_in is None:
            scr_in = backsolve_screening_input_tokens(
                model_id, rec.get("screening_cost_usd"), scr_out
            )
            backsolved = scr_in is not None
        if scr_in is not None:
            screening_usd = compute_call_cost_usd(
                model_id, int(scr_in), int(scr_out), on_date=on_date
            )

    return {
        "decision_usd": decision_usd,
        "screening_usd": screening_usd,
        "screening_backsolved": backsolved,
    }


def compute_api_cost_summary(model_key: str) -> dict[str, Any]:
    """Sum token usage and repriced USD cost across a model's decision log.

    Walks /data/trades/{model_key}_YYYY-MM.jsonl files (using a regex match
    rather than a glob so prefix-collision keys like "claude" / "claude_opus"
    don't cross-pollute).

    Cost is repriced at read time from stored tokens + each record's date (see
    reprice_record_usd) and INCLUDES the screening call, which the pre-rider-1
    version omitted entirely. ``input_tokens`` / ``output_tokens`` remain
    decision-call counts, unchanged, so token-based consumers are unaffected;
    the screening leg is exposed separately. Returns:
        {
          "calls": int,
          "input_tokens": int,
          "output_tokens": int,
          "total_tokens": int,
          "cost_usd": float,               # decision + screening, repriced
          "decision_cost_usd": float,
          "screening_cost_usd": float,
          "cost_known": bool,              # True if every call priced
          "unknown_cost_calls": int,       # no tokens logged, or no rate for model/date
          "screening_backsolved_calls": int,
        }

    ``unknown_cost_calls`` currently counts only the 37 April 8-9 shakedown
    records written before token logging landed — they carry no token counts
    at all, so they were unpriceable under the old logged-cost path too and
    the count is unchanged by the switch to repricing.
    """
    pattern = re.compile(rf"^{re.escape(model_key)}_\d{{4}}-\d{{2}}\.jsonl$")
    files = sorted(
        fp for fp in TRADES_DIR.iterdir()
        if fp.is_file() and pattern.match(fp.name)
    ) if TRADES_DIR.exists() else []

    calls = 0
    in_tok = 0
    out_tok = 0
    decision_cost = 0.0
    screening_cost = 0.0
    unknown_cost_calls = 0
    backsolved_calls = 0
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not rec.get("api_success"):
                    continue
                calls += 1
                in_tok += int(rec.get("input_tokens") or 0)
                out_tok += int(rec.get("output_tokens") or 0)
                priced = reprice_record_usd(rec)
                if priced["decision_usd"] is None:
                    unknown_cost_calls += 1
                else:
                    decision_cost += priced["decision_usd"]
                if priced["screening_usd"] is not None:
                    screening_cost += priced["screening_usd"]
                if priced["screening_backsolved"]:
                    backsolved_calls += 1
    return {
        "calls": calls,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok,
        "cost_usd": decision_cost + screening_cost,
        "decision_cost_usd": decision_cost,
        "screening_cost_usd": screening_cost,
        "cost_known": unknown_cost_calls == 0 and calls > 0,
        "unknown_cost_calls": unknown_cost_calls,
        "screening_backsolved_calls": backsolved_calls,
    }


def _parse_log_timestamp(ts: str) -> datetime | None:
    """Parse the various ISO formats the decision log uses into a tz-aware UTC datetime."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_api_cost_summary_window(
    model_key: str,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """Time-windowed version of compute_api_cost_summary.

    Walks the same monthly trade-log files but only sums records whose
    timestamp falls within [since, until). Both bounds are optional —
    omit either to leave it open. All comparisons are in UTC.

    Cheap defensive guard: only opens monthly files whose YYYY-MM tag
    overlaps the requested window, so this stays O(window-months) rather
    than O(all-history) for short windows like "today" or "this week".

    Cost is repriced at read time from stored tokens + record date and
    includes the screening call (2026-08-05 rider 1) — see
    reprice_record_usd. The stored cost_usd fields are not summed.
    """
    pattern = re.compile(rf"^{re.escape(model_key)}_(\d{{4}})-(\d{{2}})\.jsonl$")
    files: list[Path] = []
    if TRADES_DIR.exists():
        for fp in TRADES_DIR.iterdir():
            if not fp.is_file():
                continue
            m = pattern.match(fp.name)
            if not m:
                continue
            year, month = int(m.group(1)), int(m.group(2))
            month_start = datetime(year, month, 1, tzinfo=timezone.utc)
            month_end = (month_start + timedelta(days=32)).replace(day=1)
            # Skip if the file's month is entirely outside the window
            if since and month_end <= since:
                continue
            if until and month_start >= until:
                continue
            files.append(fp)
    files.sort()

    calls = 0
    in_tok = 0
    out_tok = 0
    decision_cost = 0.0
    screening_cost = 0.0
    unknown_cost_calls = 0
    backsolved_calls = 0
    trades_executed = 0
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not rec.get("api_success"):
                    continue
                ts = _parse_log_timestamp(rec.get("timestamp", ""))
                if ts is None:
                    continue
                if since and ts < since:
                    continue
                if until and ts >= until:
                    continue
                calls += 1
                in_tok += int(rec.get("input_tokens") or 0)
                out_tok += int(rec.get("output_tokens") or 0)
                # Every record is repriced from its own tokens and date. The
                # old path summed the logged cost and only recomputed when it
                # was null, which propagated the pre-2026-08 rate errors into
                # every window this function feeds.
                priced = reprice_record_usd(rec)
                if priced["decision_usd"] is None:
                    unknown_cost_calls += 1
                else:
                    decision_cost += priced["decision_usd"]
                if priced["screening_usd"] is not None:
                    screening_cost += priced["screening_usd"]
                if priced["screening_backsolved"]:
                    backsolved_calls += 1
                # Count executed position-moving orders for cost-per-trade.
                # All four directional sides count: a SHORT or COVER costs the
                # same to decide as a BUY or SELL, so a BUY/SELL-only
                # denominator overstates cost-per-trade once shorting is live
                # (74 such orders in July 2026 alone).
                for ex in rec.get("executions") or []:
                    if ex.get("executed") and ex.get("side") in ("BUY", "SELL", "SHORT", "COVER"):
                        trades_executed += 1
    cost = decision_cost + screening_cost
    return {
        "calls": calls,
        "trades_executed": trades_executed,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok,
        "cost_usd": cost,
        "decision_cost_usd": decision_cost,
        "screening_cost_usd": screening_cost,
        "unknown_cost_calls": unknown_cost_calls,
        "screening_backsolved_calls": backsolved_calls,
        "cost_per_trade_usd": (cost / trades_executed) if trades_executed > 0 else None,
    }


def compute_budget_status(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Per-provider month-to-date spend vs the configured monthly cap.

    Returns:
        {
            "providers": {
                "anthropic": {
                    "spend_usd": 0.42, "cap_usd": 30.0, "pct_of_cap": 0.014,
                    "status": "ok" | "warn" | "critical",
                    "models": ["claude", "claude_opus"],
                },
                ...
            },
            "any_warn": bool,
            "any_critical": bool,
        }

    "Status" reflects only the cap thresholds — it has nothing to do with
    whether the provider is *succeeding*, just whether the bill is high.
    """
    if settings is None:
        settings = load_settings()
    budget_cfg = settings.get("budget", {}) or {}
    caps = budget_cfg.get("monthly_cap_usd", {}) or {}
    warn_pct = float(budget_cfg.get("warn_threshold_pct", 0.80))
    crit_pct = float(budget_cfg.get("critical_threshold_pct", 1.00))

    # Group model_keys by provider so we can attribute spend correctly when
    # two models share an API (Sonnet + Opus both bill against anthropic).
    by_provider: dict[str, list[str]] = {}
    for key, cfg in (settings.get("models") or {}).items():
        provider = cfg.get("provider", "unknown")
        by_provider.setdefault(provider, []).append(key)

    now = datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

    providers: dict[str, dict[str, Any]] = {}
    any_warn = False
    any_critical = False
    for provider, model_keys in by_provider.items():
        spend = 0.0
        for key in model_keys:
            window = compute_api_cost_summary_window(key, since=month_start)
            spend += float(window["cost_usd"])
        cap = float(caps.get(provider, 0.0))
        pct = (spend / cap) if cap > 0 else 0.0
        if cap <= 0:
            status = "ok"
        elif pct >= crit_pct:
            status = "critical"
            any_critical = True
        elif pct >= warn_pct:
            status = "warn"
            any_warn = True
        else:
            status = "ok"
        providers[provider] = {
            "spend_usd": round(spend, 4),
            "cap_usd": cap,
            "pct_of_cap": round(pct, 4),
            "status": status,
            "models": sorted(model_keys),
        }
    return {
        "providers": providers,
        "any_warn": any_warn,
        "any_critical": any_critical,
        "month_start": month_start.isoformat(),
        "warn_threshold_pct": warn_pct,
        "critical_threshold_pct": crit_pct,
    }


def _sharpe(returns: np.ndarray, periods_per_year: int = 252) -> float | None:
    if len(returns) < 2:
        return None
    std = returns.std(ddof=1)
    if std == 0 or math.isnan(std):
        return None
    mean = returns.mean()
    return float(mean / std * math.sqrt(periods_per_year))


def build_leaderboard(
    model_keys: list[str], settings: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Sort by cumulative return descending.

    Tiebreakers (in order): halted/failed runs sink to the bottom, then daily
    cumulative return desc, then alphabetical for stability. This prevents a
    failed model from anchoring rank #1 on tied 0% days.

    Every row is computed on the canonical basis against the SAME canonical SPY
    return (computed once here and threaded through), so alpha reconciles and no
    model is measured on a different window than the others.
    """
    settings = settings or load_settings()
    spy_return = canonical_spy_return(settings)
    rows = [compute_metrics(k, settings=settings, spy_return=spy_return) for k in model_keys]
    rows.sort(key=lambda r: (
        bool(r.get("halted", False)),
        not bool(r.get("last_api_success", True)),
        r["cumulative_return"] is None,
        -(r["cumulative_return"] or 0),
        r["model_key"],
    ))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows
