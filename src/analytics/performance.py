"""Performance metrics: returns, Sharpe, drawdown, alpha, leaderboard.

Reads from /data/performance/{model}.jsonl which is appended once per daily run.
All metrics are deterministic + cheap to recompute, so we just regen on every run.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config_loader import PERFORMANCE_DIR


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
        return {
            "model_key": model_key,
            "days": len(df),
            "cumulative_return": 0.0,
            "sharpe_30d": None,
            "sharpe_90d": None,
            "max_drawdown": 0.0,
            "alpha_vs_spy": None,
            "current_value": float(df["total_value"].iloc[-1]) if not df.empty else 0.0,
            "halted": bool(df["halted"].iloc[-1]) if not df.empty else False,
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
    """Sort by cumulative return descending."""
    rows = [compute_metrics(k) for k in model_keys]
    rows.sort(key=lambda r: (r["cumulative_return"] is None, -(r["cumulative_return"] or 0)))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows
