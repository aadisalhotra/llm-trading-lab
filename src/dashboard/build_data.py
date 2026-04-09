"""Generates the consolidated JSON payload that the GitHub Pages dashboard reads.

Output: /data/dashboard.json — single file the frontend fetches on load.
The frontend is purely static; everything dynamic flows through this JSON.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

from ..analytics import build_leaderboard, load_performance_history
from ..config_loader import (
    DATA_DIR,
    INTRADAY_DIR,
    LEADERBOARD_DIR,
    TRADES_DIR,
    load_settings,
    load_universe,
)
from ..portfolio import load_portfolio

EASTERN = ZoneInfo("America/New_York")


def _recent_trades(model_keys: list[str], limit: int = 50) -> list[dict[str, Any]]:
    """Pull the most recent N trade events across all models, newest first."""
    all_records: list[dict[str, Any]] = []
    for key in model_keys:
        # Use a regex instead of glob so model keys that are prefixes of other
        # keys (e.g. "claude" vs "claude_opus") don't accidentally cross-match.
        # The expected filename shape is exactly {key}_YYYY-MM.jsonl.
        pattern = re.compile(rf"^{re.escape(key)}_\d{{4}}-\d{{2}}\.jsonl$")
        files = sorted(
            (fp for fp in TRADES_DIR.iterdir() if fp.is_file() and pattern.match(fp.name)),
            key=lambda fp: fp.name,
        )
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
                        decision = ex.get("decision", {}) or {}
                        all_records.append({
                            "timestamp": ex.get("timestamp", rec["timestamp"]),
                            "date": rec["date"],
                            "model_key": rec["model_key"],
                            "side": ex["side"],
                            "ticker": ex["ticker"],
                            "shares": ex["shares"],
                            "fill_price": ex["fill_price"],
                            "notional": ex["notional"],
                            "confidence": decision.get("confidence"),
                            "summary": decision.get("summary", ""),
                            "reasoning": decision.get("reasoning", ""),
                        })
    all_records.sort(key=lambda r: r["timestamp"], reverse=True)
    return all_records[:limit]


def _recent_summaries_per_model(
    model_keys: list[str],
    n: int = 3,
) -> dict[str, list[dict[str, Any]]]:
    """Last N trade summaries for each model — drives the leaderboard hover tooltip.

    Returns {model_key: [{timestamp, side, ticker, confidence, summary}, ...]}
    sorted newest-first per model. Only counts BUY/SELL executions (no HOLDs).
    """
    out: dict[str, list[dict[str, Any]]] = {}
    if not TRADES_DIR.exists():
        return {k: [] for k in model_keys}
    for key in model_keys:
        pattern = re.compile(rf"^{re.escape(key)}_\d{{4}}-\d{{2}}\.jsonl$")
        files = sorted(
            (fp for fp in TRADES_DIR.iterdir() if fp.is_file() and pattern.match(fp.name)),
            key=lambda fp: fp.name,
        )
        records: list[dict[str, Any]] = []
        for fp in files[-2:]:
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for ex in rec.get("executions", []):
                        if not ex.get("executed") or ex.get("side") in ("HOLD", "SKIP"):
                            continue
                        decision = ex.get("decision", {}) or {}
                        summary = decision.get("summary", "") or (decision.get("reasoning", "") or "")[:160]
                        records.append({
                            "timestamp": ex.get("timestamp", rec.get("timestamp", "")),
                            "side": ex.get("side", ""),
                            "ticker": ex.get("ticker", ""),
                            "confidence": decision.get("confidence"),
                            "summary": summary,
                        })
        records.sort(key=lambda r: r["timestamp"], reverse=True)
        out[key] = records[:n]
    return out


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


def _intraday_curve(model_key: str, session_date: str) -> list[dict[str, Any]]:
    """Read /data/intraday/{model}_{session_date}.jsonl for the live tick chart.

    Returns one entry per pipeline tick: timestamp + value + benchmark price.
    Frontend rebases this to 0 at the first point of the day for the TODAY
    chart view. Empty list if no intraday file exists yet (pre-market or
    first run of the session).
    """
    path = INTRADAY_DIR / f"{model_key}_{session_date}.jsonl"
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append({
                "timestamp": rec.get("timestamp"),
                "value": float(rec.get("total_value", 0.0)),
                "benchmark": float(rec["benchmark_value"]) if rec.get("benchmark_value") else None,
                "trades_today": int(rec.get("trades_executed_today", 0)),
                "runs_today": int(rec.get("runs_today", 0)),
            })
    return out


def build_dashboard_payload(prices: dict[str, float] | None = None) -> dict[str, Any]:
    settings = load_settings()
    universe = load_universe()
    model_keys = list(settings["models"].keys())

    leaderboard = build_leaderboard(model_keys)
    # Recent trade summaries per model — used by the leaderboard hover tooltip
    summaries_by_key = _recent_summaries_per_model(model_keys, n=3)
    # Annotate each leaderboard row with cohort, display_name, and recent
    # summaries so the frontend can render badges + tooltips without
    # re-reading the config or re-walking the trade log.
    for row in leaderboard:
        cfg = settings["models"].get(row["model_key"], {})
        row["cohort"] = cfg.get("cohort", "core")
        row["display_name"] = cfg.get("display_name", row["model_key"].upper())
        row["recent_summaries"] = summaries_by_key.get(row["model_key"], [])

    portfolios: list[dict[str, Any]] = []
    for key in model_keys:
        p = load_portfolio(key)
        snap = p.snapshot(prices or {})
        cfg = settings["models"][key]
        snap["provider"] = cfg["provider"]
        snap["model_id"] = cfg["model"]
        snap["enabled"] = cfg["enabled"]
        snap["cohort"] = cfg.get("cohort", "core")
        snap["display_name"] = cfg.get("display_name", key.upper())
        portfolios.append(snap)

    equity_curves = {key: _equity_curve(key) for key in model_keys}

    # Intraday curves are keyed by the *current ET trading day* so the
    # frontend's TODAY view always shows live ticks for today, never a
    # stale day's intraday file.
    session_date = datetime.now(EASTERN).strftime("%Y-%m-%d")
    intraday_curves = {key: _intraday_curve(key, session_date) for key in model_keys}

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
        "intraday_curves": intraday_curves,
        "intraday_session_date": session_date,
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
