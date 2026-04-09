"""Performance metrics: returns, Sharpe, drawdown, alpha, leaderboard.

Reads from /data/performance/{model}.jsonl which is appended once per daily run.
All metrics are deterministic + cheap to recompute, so we just regen on every run.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config_loader import PERFORMANCE_DIR, TRADES_DIR


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
            "sharpe_30d": None,
            "sharpe_90d": None,
            "max_drawdown": 0.0,
            "alpha_vs_spy": None,
            "current_value": float(df["total_value"].iloc[-1]) if not df.empty else 0.0,
            "current_cash_pct": float(df["cash_pct"].iloc[-1]) if not df.empty else 1.0,
            "num_positions": int(df["num_positions"].iloc[-1]) if not df.empty else 0,
            "halted": bool(df["halted"].iloc[-1]) if not df.empty else False,
            "last_api_success": last_api_success,
        }

    values = df["total_value"].astype(float).values
    daily_returns = np.diff(values) / values[:-1]
    cumulative_return = values[-1] / values[0] - 1.0

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

    return {
        "model_key": model_key,
        "days": int(len(df)),
        "cumulative_return": float(cumulative_return),
        "sharpe_30d": sharpe_30,
        "sharpe_90d": sharpe_90,
        "max_drawdown": max_drawdown,
        "alpha_vs_spy": alpha,
        "current_value": float(values[-1]),
        "current_cash_pct": float(df["cash_pct"].iloc[-1]),
        "num_positions": int(df["num_positions"].iloc[-1]),
        "halted": bool(df["halted"].iloc[-1]),
        "last_api_success": last_api_success,
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
