"""Append-only structured logs for decisions and daily snapshots.

Three log streams:
- /data/trades/{model}_{YYYY-MM}.jsonl              — every decision + execution result
- /data/performance/{model}.jsonl                   — EOD portfolio snapshots, one per trading day
- /data/intraday/{model}_{YYYY-MM-DD}.jsonl         — intraday valuation snapshots within a day
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config_loader import TRADES_DIR, PERFORMANCE_DIR, INTRADAY_DIR


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

    md = decision_result.metadata or {}
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
        # Token usage + USD cost — drives the cost-performance comparison
        # in the daily report's expansion cohort section. None for failed
        # calls or providers we don't have a rate table for.
        "input_tokens": md.get("input_tokens"),
        "output_tokens": md.get("output_tokens"),
        "cost_usd": md.get("cost_usd"),
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
    """Append EOD portfolio value to /data/performance/{model}.jsonl.

    Called once per trading day from the EOD pipeline pass — never from
    intraday runs (which would create duplicate same-date rows and break
    every analytics calc that assumes one row per day).
    """
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


def log_intraday_snapshot(
    model_key: str,
    run_timestamp: datetime,
    snapshot: dict[str, Any],
    benchmark_value: float | None,
    trades_executed_this_run: int,
    trades_executed_today: int,
    runs_today: int,
    api_success: bool = True,
) -> None:
    """Append a per-run intraday valuation row to /data/intraday/{model}_{date}.jsonl.

    One file per (model, trading day). Each row is one 15-minute pipeline tick:
    timestamp, mark-to-market value, benchmark price, and the running trade
    counter so the dashboard can chart intraday equity vs SPY without having
    to rejoin against the trade log.
    """
    INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
    date_str = run_timestamp.strftime("%Y-%m-%d")
    path = INTRADAY_DIR / f"{model_key}_{date_str}.jsonl"
    record = {
        "timestamp": run_timestamp.isoformat(),
        "date": date_str,
        "model_key": model_key,
        "total_value": snapshot["total_value"],
        "cash": snapshot["cash"],
        "cash_pct": snapshot["cash_pct"],
        "num_positions": len(snapshot["holdings"]),
        "cumulative_return": snapshot["cumulative_return"],
        "halted": snapshot["halted"],
        "benchmark_value": benchmark_value,
        "trades_executed_this_run": trades_executed_this_run,
        "trades_executed_today": trades_executed_today,
        "runs_today": runs_today,
        "api_success": api_success,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
