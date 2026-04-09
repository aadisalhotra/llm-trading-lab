"""Generates the consolidated JSON payload that the GitHub Pages dashboard reads.

Output: /data/dashboard.json — single file the frontend fetches on load.
The frontend is purely static; everything dynamic flows through this JSON.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..analytics import build_leaderboard, load_performance_history
from ..config_loader import (
    DATA_DIR,
    LEADERBOARD_DIR,
    TRADES_DIR,
    load_settings,
    load_universe,
)
from ..portfolio import load_portfolio


def _recent_trades(model_keys: list[str], limit: int = 50) -> list[dict[str, Any]]:
    """Pull the most recent N trade events across all models, newest first."""
    all_records: list[dict[str, Any]] = []
    for key in model_keys:
        files = sorted(TRADES_DIR.glob(f"{key}_*.jsonl"))
        for fp in files[-3:]:  # last 3 months only — cheap
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    for ex in rec.get("executions", []):
                        if not ex.get("executed") or ex.get("side") in ("HOLD", "SKIP"):
                            continue
                        all_records.append({
                            "timestamp": ex.get("timestamp", rec["timestamp"]),
                            "date": rec["date"],
                            "model_key": rec["model_key"],
                            "side": ex["side"],
                            "ticker": ex["ticker"],
                            "shares": ex["shares"],
                            "fill_price": ex["fill_price"],
                            "notional": ex["notional"],
                            "confidence": ex.get("decision", {}).get("confidence"),
                            "reasoning": ex.get("decision", {}).get("reasoning", ""),
                        })
    all_records.sort(key=lambda r: r["timestamp"], reverse=True)
    return all_records[:limit]


def _equity_curve(model_key: str) -> list[dict[str, Any]]:
    df = load_performance_history(model_key)
    if df.empty:
        return []
    return [
        {
            "date": row["date"].strftime("%Y-%m-%d"),
            "value": float(row["total_value"]),
            "benchmark": float(row["benchmark_value"]) if row.get("benchmark_value") else None,
        }
        for _, row in df.iterrows()
    ]


def build_dashboard_payload(prices: dict[str, float] | None = None) -> dict[str, Any]:
    settings = load_settings()
    universe = load_universe()
    model_keys = list(settings["models"].keys())

    leaderboard = build_leaderboard(model_keys)

    portfolios: list[dict[str, Any]] = []
    for key in model_keys:
        p = load_portfolio(key)
        snap = p.snapshot(prices or {})
        cfg = settings["models"][key]
        snap["provider"] = cfg["provider"]
        snap["model_id"] = cfg["model"]
        snap["enabled"] = cfg["enabled"]
        portfolios.append(snap)

    equity_curves = {key: _equity_curve(key) for key in model_keys}

    today = datetime.utcnow()
    inception_str = settings["experiment_start_date"]
    try:
        inception = datetime.strptime(inception_str, "%Y-%m-%d")
        end = datetime.strptime(settings["experiment_end_date"], "%Y-%m-%d")
        day_num = max(1, (today - inception).days + 1)
        total_days = (end - inception).days + 1
    except ValueError:
        day_num = 0
        total_days = 0

    payload = {
        "generated_at": today.isoformat(),
        "phase": settings["phase"],
        "mode": settings["mode"],
        "experiment_day": day_num,
        "experiment_total_days": total_days,
        "experiment_start": inception_str,
        "experiment_end": settings["experiment_end_date"],
        "leaderboard": leaderboard,
        "portfolios": portfolios,
        "recent_trades": _recent_trades(model_keys, limit=50),
        "equity_curves": equity_curves,
        "universe": universe,
        "models": settings["models"],
        "benchmark_ticker": settings["benchmark_ticker"],
        "prompt_version": settings["prompt_version"],
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "dashboard.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    # Also drop a leaderboard-only snapshot for archival
    LEADERBOARD_DIR.mkdir(parents=True, exist_ok=True)
    snap_path = LEADERBOARD_DIR / f"{today.strftime('%Y-%m-%d')}.json"
    with open(snap_path, "w", encoding="utf-8") as f:
        json.dump(leaderboard, f, indent=2, default=str)

    return payload
