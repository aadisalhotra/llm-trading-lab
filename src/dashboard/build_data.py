"""Generates the consolidated JSON payload that the GitHub Pages dashboard reads.

Output: /data/dashboard.json — single file the frontend fetches on load.
The frontend is purely static; everything dynamic flows through this JSON.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from ..analytics import (
    build_leaderboard,
    compute_api_cost_summary,
    compute_api_cost_summary_window,
    compute_budget_status,
    compute_spy_benchmark_metrics,
    load_performance_history,
)
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


def _build_cost_tracker(model_keys: list[str], starting_capital: float) -> list[dict[str, Any]]:
    """Per-model API cost rollups + ROI calc for the dashboard cost panel.

    Returns one row per model with:
        - model_key
        - cost_today_usd, cost_week_usd, cost_month_usd, cost_total_usd
        - cost_per_trade_usd  (None if no trades)
        - net_pnl_usd         (gross P&L $ minus total API cost)
        - is_profitable       (True if cumulative return $ > total API cost)

    "Today" / "this week" / "this month" / "total" are all UTC-anchored to
    keep the math consistent with the trade log timestamps.
    """
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    # Trailing 7-day rolling window for "this week" — calendar weeks would
    # reset at unintuitive times (Sunday/Monday) and the user-facing label
    # is "this week's cost" which trailing-7-day satisfies just fine.
    week_start = today_start - timedelta(days=7)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

    out: list[dict[str, Any]] = []
    for key in model_keys:
        today = compute_api_cost_summary_window(key, since=today_start)
        week = compute_api_cost_summary_window(key, since=week_start)
        month = compute_api_cost_summary_window(key, since=month_start)
        total = compute_api_cost_summary_window(key, since=None)   # full history

        # Gross P&L $ = current value - inception value, approximated from
        # the perf log. For models with only one perf row (just initialized
        # this session), fall back to current_value vs the configured
        # starting_capital so the day-1 number isn't None.
        df = load_performance_history(key)
        gross_pnl_usd: float | None = None
        if not df.empty:
            try:
                current_val = float(df["total_value"].iloc[-1])
                inception = float(df["total_value"].iloc[0])
                gross_pnl_usd = current_val - inception
                if abs(gross_pnl_usd) < 0.001 and len(df) == 1:
                    gross_pnl_usd = current_val - starting_capital
            except (KeyError, IndexError, ValueError):
                pass

        net_pnl_usd = (gross_pnl_usd - total["cost_usd"]) if gross_pnl_usd is not None else None

        cost_per_trade = None
        if total["trades_executed"] > 0:
            cost_per_trade = round(float(total["cost_usd"]) / total["trades_executed"], 4)

        out.append({
            "model_key": key,
            "cost_today_usd": round(float(today["cost_usd"]), 4),
            "cost_week_usd": round(float(week["cost_usd"]), 4),
            "cost_month_usd": round(float(month["cost_usd"]), 4),
            "cost_total_usd": round(float(total["cost_usd"]), 4),
            "cost_per_trade_usd": cost_per_trade,
            "trades_executed_total": int(total["trades_executed"]),
            "trades_executed_month": int(month["trades_executed"]),
            "trades_executed_today": int(today["trades_executed"]),
            "gross_pnl_usd": round(float(gross_pnl_usd), 2) if gross_pnl_usd is not None else None,
            "net_pnl_usd": round(float(net_pnl_usd), 2) if net_pnl_usd is not None else None,
            "is_profitable": net_pnl_usd is not None and net_pnl_usd > 0,
        })
    return out


def _consensus_picks(
    portfolios: list[dict[str, Any]],
    model_keys: list[str],
    all_trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Stocks held by 3+ models, sorted by holder count then avg weight."""
    ticker_holders: dict[str, list[dict[str, Any]]] = {}
    for p in portfolios:
        for h in p.get("holdings", []):
            ticker_holders.setdefault(h["ticker"], []).append({
                "model_key": p["model_key"],
                "weight": h.get("weight", 0),
                "unrealized_pl_pct": h.get("unrealized_pl_pct", 0),
            })

    total_models = len(model_keys)
    picks: list[dict[str, Any]] = []
    for ticker, holders in ticker_holders.items():
        if len(holders) < 3:
            continue
        avg_weight = sum(h["weight"] for h in holders) / len(holders)
        avg_pl = sum(h["unrealized_pl_pct"] for h in holders) / len(holders)
        # Average confidence from most recent buys of this ticker across models
        confs = [
            t["confidence"] for t in all_trades
            if t["ticker"] == ticker and t["side"] == "BUY" and t.get("confidence")
        ]
        avg_conf = round(sum(confs) / len(confs), 1) if confs else None
        picks.append({
            "ticker": ticker,
            "model_count": len(holders),
            "total_models": total_models,
            "models": [h["model_key"] for h in holders],
            "avg_weight": round(avg_weight, 4),
            "avg_confidence": avg_conf,
            "avg_pl_pct": round(avg_pl, 4),
        })
    picks.sort(key=lambda x: (-x["model_count"], -x["avg_weight"]))
    return picks


def _compute_trade_analytics(
    model_keys: list[str],
    portfolios: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Walk all trade history and compute per-trade returns, agreement stats,
    and confidence calibration data.

    Returns (trade_list, agreement_returns, confidence_calibration).

    agreement_returns = {
        high_avg: float, high_count: int,   (4+ models agree)
        low_avg: float, low_count: int,     (1-2 models)
    }

    confidence_calibration = {
        model_key: {
            buckets: [{confidence, avg_return, count}, ...],
            calibration_score: float (-1 to +1),
            total_trades: int,
        }
    }
    """
    # Current holdings per model for pricing open positions
    current_holdings: dict[str, dict[str, dict[str, float]]] = {}
    for p in portfolios:
        ch: dict[str, dict[str, float]] = {}
        for h in p.get("holdings", []):
            ch[h["ticker"]] = {
                "current_price": h.get("current_price", 0),
                "unrealized_pl_pct": h.get("unrealized_pl_pct", 0),
            }
        current_holdings[p["model_key"]] = ch

    # Walk all trade records chronologically across all models
    all_records: list[tuple[str, str, dict[str, Any]]] = []
    if TRADES_DIR.exists():
        for key in model_keys:
            pattern = re.compile(rf"^{re.escape(key)}_\d{{4}}-\d{{2}}\.jsonl$")
            files = sorted(
                fp for fp in TRADES_DIR.iterdir()
                if fp.is_file() and pattern.match(fp.name)
            )
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
                        all_records.append((rec.get("timestamp", ""), key, rec))

    all_records.sort(key=lambda x: x[0])

    # Running holdings per model: {model_key: set(tickers)}
    running: dict[str, set[str]] = {k: set() for k in model_keys}
    # Open BUY positions for matching: {(model_key, ticker): [buy_info]}
    open_buys: dict[tuple[str, str], list[dict[str, Any]]] = {}
    # Completed trades with returns
    trades: list[dict[str, Any]] = []

    for ts, model_key, rec in all_records:
        for ex in rec.get("executions", []):
            if not ex.get("executed") or ex.get("side") in ("HOLD", "SKIP"):
                continue
            ticker = ex["ticker"]
            side = ex["side"]
            decision = ex.get("decision") or {}
            confidence = decision.get("confidence")
            fill_price = ex.get("fill_price", 0)

            if side == "BUY":
                # Agreement: how many models (including buyer) hold this ticker
                agreement = sum(1 for k in model_keys if k != model_key and ticker in running[k]) + 1
                kt = (model_key, ticker)
                open_buys.setdefault(kt, []).append({
                    "fill_price": fill_price,
                    "confidence": confidence,
                    "agreement": agreement,
                })
                running[model_key].add(ticker)
            elif side == "SELL":
                kt = (model_key, ticker)
                if kt in open_buys and open_buys[kt]:
                    buy = open_buys[kt].pop(0)
                    if not open_buys[kt]:
                        del open_buys[kt]
                    ret = (fill_price / buy["fill_price"] - 1) if buy["fill_price"] > 0 else 0
                    trades.append({
                        "model_key": model_key,
                        "ticker": ticker,
                        "confidence": buy["confidence"],
                        "return_pct": ret,
                        "agreement": buy["agreement"],
                        "is_closed": True,
                    })
                running[model_key].discard(ticker)

    # Still-open positions: compute return from current prices
    for (model_key, ticker), buys in open_buys.items():
        ch = current_holdings.get(model_key, {}).get(ticker)
        for buy in buys:
            if ch and buy["fill_price"] > 0:
                ret = ch["current_price"] / buy["fill_price"] - 1
            else:
                ret = 0
            trades.append({
                "model_key": model_key,
                "ticker": ticker,
                "confidence": buy["confidence"],
                "return_pct": ret,
                "agreement": buy["agreement"],
                "is_closed": False,
            })

    # --- Agreement returns ---
    high = [t for t in trades if t["agreement"] >= 4]
    low = [t for t in trades if t["agreement"] <= 2]
    agreement_returns = {
        "high_avg": round(sum(t["return_pct"] for t in high) / len(high), 4) if high else None,
        "high_count": len(high),
        "low_avg": round(sum(t["return_pct"] for t in low) / len(low), 4) if low else None,
        "low_count": len(low),
    }

    # --- Confidence calibration per model ---
    from collections import defaultdict
    model_buckets: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for t in trades:
        if t["confidence"] is not None:
            model_buckets[t["model_key"]][t["confidence"]].append(t["return_pct"])

    calibration: dict[str, Any] = {}
    for key in model_keys:
        buckets_raw = model_buckets.get(key, {})
        total = sum(len(v) for v in buckets_raw.values())
        buckets = []
        for conf in range(1, 11):
            returns = buckets_raw.get(conf, [])
            if returns:
                buckets.append({
                    "confidence": conf,
                    "avg_return": round(sum(returns) / len(returns), 4),
                    "count": len(returns),
                })
            else:
                buckets.append({"confidence": conf, "avg_return": None, "count": 0})

        # Pearson correlation between confidence and return
        cal_score = None
        if total >= 5:
            pairs = [(t["confidence"], t["return_pct"]) for t in trades
                     if t["model_key"] == key and t["confidence"] is not None]
            if len(pairs) >= 5:
                xs = [p[0] for p in pairs]
                ys = [p[1] for p in pairs]
                mx = sum(xs) / len(xs)
                my = sum(ys) / len(ys)
                cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                sx = sum((x - mx) ** 2 for x in xs) ** 0.5
                sy = sum((y - my) ** 2 for y in ys) ** 0.5
                if sx > 0 and sy > 0:
                    cal_score = round(cov / (sx * sy), 3)

        calibration[key] = {
            "buckets": buckets,
            "calibration_score": cal_score,
            "total_trades": total,
            "min_trades": 20,
        }

    return trades, agreement_returns, calibration


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

    # Append the SPY buy-and-hold benchmark as a non-competing row at the
    # very bottom of the leaderboard. The "benchmark" cohort tag tells the
    # frontend to render it with neutral gray styling and pin it below
    # all model rows regardless of sort.
    starting_capital = float(settings.get("starting_capital", {}).get(
        settings.get("mode", "paper"), 100_000.0
    ))
    spy_metrics = compute_spy_benchmark_metrics(starting_capital=starting_capital)
    if spy_metrics is not None:
        spy_metrics["rank"] = len(leaderboard) + 1   # nominal — frontend pins to bottom anyway
        spy_metrics["cohort"] = "benchmark"
        spy_metrics["display_name"] = "SPY (Benchmark)"
        spy_metrics["recent_summaries"] = []
        leaderboard.append(spy_metrics)

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
    # Synthetic SPY buy-and-hold equity curve for the leaderboard sparkline.
    # Built from the benchmark_value field of whichever model has the LONGEST
    # benchmark series (model perf logs differ in length — Sonnet was added
    # mid-experiment so its log only has 1 row, but the older models have
    # the full history). All benchmark prices are identical since the
    # pipeline fetches one SPY price per tick, so we just need the longest.
    spy_curve: list[dict[str, Any]] = []
    spy_start_capital = float(settings.get("starting_capital", {}).get(
        settings.get("mode", "paper"), 100_000.0
    ))
    longest_bench: list[dict[str, Any]] = []
    for key in model_keys:
        candidate = equity_curves.get(key) or []
        bench_points = [p for p in candidate if p.get("benchmark") not in (None, 0)]
        if len(bench_points) > len(longest_bench):
            longest_bench = bench_points
    if longest_bench:
        base = longest_bench[0]["benchmark"]
        if base and base > 0:
            shares = spy_start_capital / base
            spy_curve = [
                {"date": p["date"], "value": p["benchmark"] * shares, "benchmark": p["benchmark"]}
                for p in longest_bench
            ]
    if spy_curve:
        equity_curves["spy_benchmark"] = spy_curve

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

    cost_tracker = _build_cost_tracker(model_keys, starting_capital)
    try:
        budget_status = compute_budget_status(settings)
    except Exception:
        budget_status = {"providers": {}, "any_warn": False, "any_critical": False}

    # Consensus picks + trade analytics (agreement returns, confidence calibration)
    recent_all = _recent_trades(model_keys, limit=50)
    consensus_picks = _consensus_picks(portfolios, model_keys, recent_all)
    try:
        _, agreement_returns, confidence_calibration = _compute_trade_analytics(
            model_keys, portfolios,
        )
    except Exception:
        agreement_returns = {"high_avg": None, "high_count": 0, "low_avg": None, "low_count": 0}
        confidence_calibration = {}

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
        "recent_trades": recent_all,
        "equity_curves": equity_curves,
        "intraday_curves": intraday_curves,
        "intraday_session_date": session_date,
        "universe": universe,
        "models": settings["models"],
        "benchmark_ticker": settings["benchmark_ticker"],
        "prompt_version": settings["prompt_version"],
        "cost_tracker": cost_tracker,
        "budget_status": budget_status,
        "consensus_picks": consensus_picks,
        "agreement_returns": agreement_returns,
        "confidence_calibration": confidence_calibration,
        "universe_coverage": {
            "total_tracked": len(universe.get("tickers", [])),
            "actively_held": len({
                h["ticker"]
                for p in portfolios
                for h in p.get("holdings", [])
            }),
        },
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
