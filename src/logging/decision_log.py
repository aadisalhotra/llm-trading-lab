"""Append-only structured logs for decisions and daily snapshots.

Two log streams:
- /data/trades/{model}_{YYYY-MM}.jsonl  — every decision + execution result, one per line
- /data/performance/{model}.jsonl       — daily portfolio snapshots, one per day per model
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config_loader import TRADES_DIR, PERFORMANCE_DIR


def _month_tag(d: datetime, inception_date: str) -> str:
    """Convert a date to an experiment month tag like M1, M2, ..."""
    if not inception_date:
        return ""
    try:
        start = datetime.strptime(inception_date, "%Y-%m-%d")
    except ValueError:
        return ""
    months = (d.year - start.year) * 12 + (d.month - start.month) + 1
    return f"M{max(1, months)}"


def log_decision_run(
    model_key: str,
    run_date: datetime,
    decision_result: Any,
    accepted_decisions: list[dict[str, Any]],
    violations: list[Any],
    execution_results: list[Any],
    portfolio_snapshot_after: dict[str, Any],
    prompt_version: str,
    data_inputs_hash: str,
    execution_mode: str,
    inception_date: str,
) -> None:
    """Append a single line to /data/trades/{model}_{YYYY-MM}.jsonl with everything that happened."""
    TRADES_DIR.mkdir(parents=True, exist_ok=True)
    month_str = run_date.strftime("%Y-%m")
    path = TRADES_DIR / f"{model_key}_{month_str}.jsonl"

    record = {
        "date": run_date.strftime("%Y-%m-%d"),
        "timestamp": datetime.utcnow().isoformat(),
        "model_key": model_key,
        "model_id_configured": decision_result.model_id_configured,
        "model_id_returned": decision_result.model_id_returned,
        "provider": decision_result.provider,
        "execution_mode": execution_mode,
        "month_tag": _month_tag(run_date, inception_date),
        "prompt_version": prompt_version,
        "data_inputs_hash": data_inputs_hash,
        "api_success": decision_result.success,
        "api_error": decision_result.error,
        "api_latency_seconds": decision_result.latency_seconds,
        "overall_reasoning": decision_result.overall_reasoning,
        "raw_decisions": decision_result.decisions,
        "accepted_decisions": accepted_decisions,
        "violations": [
            {"index": v.decision_index, "rule": v.rule, "detail": v.detail}
            for v in violations
        ],
        "executions": [
            {
                "ticker": e.ticker,
                "side": e.side,
                "executed": e.executed,
                "shares": e.shares,
                "fill_price": e.fill_price,
                "notional": e.notional,
                "order_id": e.order_id,
                "error": e.error,
                "timestamp": e.timestamp,
                "decision": e.decision,
            }
            for e in execution_results
        ],
        "portfolio_after": portfolio_snapshot_after,
    }

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def log_daily_snapshot(
    model_key: str,
    run_date: datetime,
    snapshot: dict[str, Any],
    benchmark_value: float | None,
    api_success: bool = True,
) -> None:
    """Append daily portfolio value to /data/performance/{model}.jsonl."""
    PERFORMANCE_DIR.mkdir(parents=True, exist_ok=True)
    path = PERFORMANCE_DIR / f"{model_key}.jsonl"
    record = {
        "date": run_date.strftime("%Y-%m-%d"),
        "model_key": model_key,
        "total_value": snapshot["total_value"],
        "cash": snapshot["cash"],
        "cash_pct": snapshot["cash_pct"],
        "num_positions": len(snapshot["holdings"]),
        "cumulative_return": snapshot["cumulative_return"],
        "halted": snapshot["halted"],
        "benchmark_value": benchmark_value,
        "api_success": api_success,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
