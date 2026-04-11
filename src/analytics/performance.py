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


def compute_metrics(model_key: str) -> dict[str, Any]:
    df = load_performance_history(model_key)
    if df.empty or len(df) < 2:
        last_api_success = True
        if not df.empty and "api_success" in df.columns:
            v = df["api_success"].iloc[-1]
            if v is not None and not pd.isna(v):
                last_api_success = bool(v)
        return {
            "model_key": model_key,
            "days": len(df),
            "cumulative_return": 0.0,
            "daily_pnl_pct": None,
            "win_rate": None,
            "sharpe_30d": None,
            "sharpe_90d": None,
            "max_drawdown": 0.0,
            "alpha_vs_spy": None,
            "streak_count": 0,
            "streak_type": None,
            "current_value": float(df["total_value"].iloc[-1]) if not df.empty else 0.0,
            "current_cash_pct": float(df["cash_pct"].iloc[-1]) if not df.empty else 1.0,
            "num_positions": int(df["num_positions"].iloc[-1]) if not df.empty else 0,
            "halted": bool(df["halted"].iloc[-1]) if not df.empty else False,
            "last_api_success": last_api_success,
        }

    values = df["total_value"].astype(float).values
    daily_returns = np.diff(values) / values[:-1]
    cumulative_return = values[-1] / values[0] - 1.0

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

    # Alpha vs benchmark
    alpha = None
    if "benchmark_value" in df.columns and df["benchmark_value"].notna().sum() >= 2:
        bench = df["benchmark_value"].dropna().astype(float).values
        if len(bench) >= 2 and bench[0] > 0:
            bench_return = bench[-1] / bench[0] - 1.0
            alpha = cumulative_return - bench_return

    last_api_success = True
    if "api_success" in df.columns:
        v = df["api_success"].iloc[-1]
        if v is not None and not pd.isna(v):
            last_api_success = bool(v)

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
        "last_api_success": last_api_success,
    }


def compute_spy_benchmark_metrics(starting_capital: float = 100_000.0) -> dict[str, Any] | None:
    """Synthesize a SPY buy-and-hold portfolio from the benchmark_value series.

    Reads any model's perf log to get the SPY closing prices logged at each
    EOD pass (they all share the same series since the pipeline fetches one
    benchmark per tick). Computes the same metrics as `compute_metrics`,
    treating SPY as a single-position portfolio bought at inception with
    `starting_capital` and held without trading.

    Returns None if no perf log has a usable benchmark_value series yet.
    The output shape matches `compute_metrics()` so the leaderboard table
    can render it as a regular row alongside the model rows.
    """
    if not PERFORMANCE_DIR.exists():
        return None
    # Pick the perf log with the LONGEST benchmark_value series. All models
    # share the same SPY series since the pipeline fetches one price per
    # tick, but the per-model logs differ in length (newer models have
    # fewer rows). Picking the longest gives us the full SPY history.
    df = pd.DataFrame()
    best_len = 0
    for fp in PERFORMANCE_DIR.glob("*.jsonl"):
        candidate = load_performance_history(fp.stem)
        if candidate.empty or "benchmark_value" not in candidate.columns:
            continue
        n_bench = int(candidate["benchmark_value"].notna().sum())
        if n_bench >= 2 and n_bench > best_len:
            df = candidate
            best_len = n_bench
    if df.empty or "benchmark_value" not in df.columns:
        return None

    series = df[["date", "benchmark_value"]].dropna()
    if len(series) < 2:
        return None

    bench = series["benchmark_value"].astype(float).values
    base_price = bench[0]
    if base_price <= 0:
        return None
    shares = starting_capital / base_price
    values = bench * shares  # synthetic equity curve

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


def compute_api_cost_summary(model_key: str) -> dict[str, Any]:
    """Sum token usage and USD cost across every decision-log entry for a model.

    Walks /data/trades/{model_key}_YYYY-MM.jsonl files (using a regex match
    rather than a glob so prefix-collision keys like "claude" / "claude_opus"
    don't cross-pollute). Returns:
        {
          "calls": int,
          "input_tokens": int,
          "output_tokens": int,
          "total_tokens": int,
          "cost_usd": float,           # 0.0 if no rates known
          "cost_known": bool,          # True if every call had a cost rate
        }
    """
    pattern = re.compile(rf"^{re.escape(model_key)}_\d{{4}}-\d{{2}}\.jsonl$")
    files = sorted(
        fp for fp in TRADES_DIR.iterdir()
        if fp.is_file() and pattern.match(fp.name)
    ) if TRADES_DIR.exists() else []

    calls = 0
    in_tok = 0
    out_tok = 0
    cost = 0.0
    unknown_cost_calls = 0
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
                c = rec.get("cost_usd")
                if c is None:
                    unknown_cost_calls += 1
                else:
                    cost += float(c)
    return {
        "calls": calls,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok,
        "cost_usd": cost,
        "cost_known": unknown_cost_calls == 0 and calls > 0,
        "unknown_cost_calls": unknown_cost_calls,
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

    from .cost_rates import compute_call_cost_usd

    calls = 0
    in_tok = 0
    out_tok = 0
    cost = 0.0
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
                rec_in = int(rec.get("input_tokens") or 0)
                rec_out = int(rec.get("output_tokens") or 0)
                in_tok += rec_in
                out_tok += rec_out
                c = rec.get("cost_usd")
                if c is None and (rec_in > 0 or rec_out > 0):
                    # Backfill: the record was written before the cost-rates
                    # prefix-fallback fix landed, but we have token counts.
                    # Recompute from the rate table now.
                    c = compute_call_cost_usd(
                        rec.get("model_id_returned") or rec.get("model_id_configured", ""),
                        rec_in, rec_out,
                    )
                if c is not None:
                    cost += float(c)
                # Count BUY/SELL executions for cost-per-trade
                for ex in rec.get("executions") or []:
                    if ex.get("executed") and ex.get("side") in ("BUY", "SELL"):
                        trades_executed += 1
    return {
        "calls": calls,
        "trades_executed": trades_executed,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok,
        "cost_usd": cost,
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


def build_leaderboard(model_keys: list[str]) -> list[dict[str, Any]]:
    """Sort by cumulative return descending.

    Tiebreakers (in order): halted/failed runs sink to the bottom, then daily
    cumulative return desc, then alphabetical for stability. This prevents a
    failed model from anchoring rank #1 on tied 0% days.
    """
    rows = [compute_metrics(k) for k in model_keys]
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
