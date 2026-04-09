"""Intraday + EOD pipeline orchestrator.

The pipeline runs in one of two modes, controlled by CLI flags:

  --intraday    (default): one 15-minute pipeline tick during market hours.
                Fetches intraday bars, runs each model, executes any trades
                (subject to the persistent daily trade cap), logs an
                intraday valuation snapshot, and rebuilds dashboard.json.
                Skips silently if NYSE is not currently open (unless --force).

  --end-of-day: post-close wrap-up. Does everything an intraday tick does
                AND writes the EOD row to /data/performance/{model}.jsonl,
                generates the daily research report, and sends the daily
                summary alert. Should fire once per trading day after 16:00 ET.

  --force       bypass the market-open-now check (for manual smoke tests).

The persistent intraday trade cap lives in
`Portfolio.intraday.trades_executed_today` and resets automatically when a
new ET trading day begins. Models therefore see the *whole-day* 30-trade
budget across all 26 ticks of the day, not a fresh 30 every 15 minutes.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

# All run dates are anchored to US/Eastern — the trading-session calendar.
EASTERN = ZoneInfo("America/New_York")

from .adapters import get_adapter
from .alerts import send_alert, send_daily_summary
from .analytics import build_leaderboard
from .config_loader import (
    configure_logging,
    ensure_dirs,
    load_env,
    load_settings,
    universe_symbols,
)
from .dashboard import build_dashboard_payload
from .data import (
    fetch_index_data,
    fetch_intraday_data,
    fetch_universe_data,
    is_market_open_now,
    is_market_open_today,
)
from .logging import log_decision_run, log_daily_snapshot, log_intraday_snapshot
from .reports import generate_daily_report
from .model_versions import detect_monthly_transition, record_observation
from .portfolio import (
    check_portfolio_stop,
    check_position_stops,
    load_portfolio,
    save_portfolio,
    validate_decisions,
)
from .execution import Executor
from .prompt_builder import build_prompts, hash_inputs


def _prices_from_data(data: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for ticker, df in data.items():
        if df is not None and not df.empty:
            out[ticker] = float(df["Close"].iloc[-1])
    return out


def run_one_model(
    model_key: str,
    cfg: dict[str, Any],
    market_data: dict,
    prices: dict[str, float],
    executor: Executor,
    run_date: datetime,
    settings: dict[str, Any],
    logger: logging.Logger,
    is_eod: bool,
) -> dict[str, Any]:
    logger.info("=== Running model: %s (%s/%s) ===", model_key, cfg["provider"], cfg["model"])

    portfolio = load_portfolio(model_key)

    # Reset the intraday counters at the start of a new ET trading day
    session_date_str = run_date.strftime("%Y-%m-%d")
    if portfolio.reset_intraday_if_new_session(session_date_str):
        logger.info("[%s] New session detected — intraday counters reset", model_key)

    if portfolio.halted:
        logger.warning("[%s] Portfolio HALTED — skipping", model_key)
        return {"model_key": model_key, "status": "HALTED"}

    # Per-position stop-loss sweep BEFORE the model trades
    triggered = check_position_stops(portfolio, prices)
    forced_results = []
    if triggered:
        send_alert("WARN", f"Position stop on {model_key}",
                   f"Force-selling: {triggered}", {"model": model_key})
        forced_results = executor.force_liquidate(portfolio, triggered, prices, "POSITION_STOP")

    # Build prompts + call model. Pass intraday context so the model paces
    # its 30-trade budget across the remaining session ticks.
    snapshot_before = portfolio.snapshot(prices)
    system_prompt, user_prompt, prompt_version, images = build_prompts(
        snapshot_before,
        market_data,
        run_date,
        trades_executed_today=portfolio.intraday.trades_executed_today,
        runs_today=portfolio.intraday.runs_today,
        is_eod=is_eod,
        include_chart_image=True,
    )
    data_hash = hash_inputs(market_data)

    adapter = get_adapter(cfg["provider"], cfg["model"])
    # Only send images to vision-capable models. Text-only adapters can
    # accept the kwarg but it's wasteful to base64-encode for nothing.
    images_for_call = images if (cfg.get("vision_capable", False) and adapter.supports_vision) else None
    decision_result = adapter.generate_decision(system_prompt, user_prompt, images=images_for_call)

    # Record version observation regardless of success
    record_observation(model_key, decision_result.model_id_returned, run_date)
    transition = detect_monthly_transition(model_key, run_date)
    if transition:
        send_alert("INFO", f"Model transition: {model_key}",
                   f"{transition['old_version']} -> {transition['new_version']}", transition)

    if not decision_result.success:
        logger.error("[%s] Adapter failure: %s", model_key, decision_result.error)
        send_alert("WARN", f"Model {model_key} failed", decision_result.error or "unknown",
                   {"model": model_key})
        # Still log the failure + intraday snapshot
        log_decision_run(
            model_key=model_key, run_date=run_date,
            decision_result=decision_result,
            accepted_decisions=[], violations=[], execution_results=forced_results,
            portfolio_snapshot_after=portfolio.snapshot(prices),
            prompt_version=prompt_version, data_inputs_hash=data_hash,
            execution_mode=settings["mode"], inception_date=portfolio.inception_date,
        )
        # Still bump the intraday counter (this run consumed a slot)
        portfolio.record_intraday_run(run_date.isoformat(), trades_executed=0)
        save_portfolio(portfolio)
        snap_after = portfolio.snapshot(prices)
        log_intraday_snapshot(
            model_key=model_key,
            run_timestamp=run_date,
            snapshot=snap_after,
            benchmark_value=prices.get(settings["benchmark_ticker"]),
            trades_executed_this_run=0,
            trades_executed_today=portfolio.intraday.trades_executed_today,
            runs_today=portfolio.intraday.runs_today,
            api_success=False,
        )
        if is_eod:
            log_daily_snapshot(model_key, run_date, snap_after,
                               prices.get(settings["benchmark_ticker"]),
                               api_success=False)
        return {"model_key": model_key, "status": "API_FAIL", "error": decision_result.error}

    # Validate against risk rules — feeding in the persistent trade counter
    accepted, violations = validate_decisions(
        decision_result.decisions,
        portfolio,
        prices,
        trades_already_executed_today=portfolio.intraday.trades_executed_today,
    )

    # Execute
    exec_results = executor.execute_decisions(portfolio, accepted, prices)
    all_exec = forced_results + exec_results

    # Portfolio stop check AFTER execution
    if check_portfolio_stop(portfolio, prices):
        send_alert("CRITICAL", f"Portfolio stop hit: {model_key}",
                   "Liquidating to cash and halting model", {"model": model_key})
        portfolio.liquidate_all(prices)
        portfolio.halted = True

    # Update intraday counters with this run's trade count
    n_executed = sum(1 for e in all_exec if e.executed and e.side in ("BUY", "SELL"))
    portfolio.record_intraday_run(run_date.isoformat(), trades_executed=n_executed)
    save_portfolio(portfolio)
    snapshot_after = portfolio.snapshot(prices)

    log_decision_run(
        model_key=model_key, run_date=run_date,
        decision_result=decision_result,
        accepted_decisions=accepted, violations=violations,
        execution_results=all_exec,
        portfolio_snapshot_after=snapshot_after,
        prompt_version=prompt_version, data_inputs_hash=data_hash,
        execution_mode=settings["mode"], inception_date=portfolio.inception_date,
    )

    benchmark_val = prices.get(settings["benchmark_ticker"])
    log_intraday_snapshot(
        model_key=model_key,
        run_timestamp=run_date,
        snapshot=snapshot_after,
        benchmark_value=benchmark_val,
        trades_executed_this_run=n_executed,
        trades_executed_today=portfolio.intraday.trades_executed_today,
        runs_today=portfolio.intraday.runs_today,
        api_success=True,
    )

    if is_eod:
        # Single per-day row in the EOD performance log — drives the daily
        # analytics, leaderboard, and historical equity curves.
        log_daily_snapshot(model_key, run_date, snapshot_after, benchmark_val, api_success=True)

    logger.info("[%s] done — %d trades this run, %d/%d trades today, %d runs today, value=$%.2f",
                model_key, n_executed,
                portfolio.intraday.trades_executed_today,
                int(settings["portfolio_rules"]["max_trades_per_day"]),
                portfolio.intraday.runs_today,
                snapshot_after["total_value"])

    return {
        "model_key": model_key,
        "status": "OK",
        "trades_executed": n_executed,
        "trades_today": portfolio.intraday.trades_executed_today,
        "runs_today": portfolio.intraday.runs_today,
        "violations": len(violations),
        "total_value": snapshot_after["total_value"],
        "cumulative_return": snapshot_after["cumulative_return"],
    }


def run_pipeline(mode: str = "intraday", force: bool = False) -> int:
    """Run one pipeline tick.

    `mode` is one of:
      "intraday"   — normal in-session tick (default)
      "end-of-day" — post-close wrap-up that also writes the EOD perf row,
                     generates the daily report, and sends the summary alert
    """
    load_env()
    ensure_dirs()
    logger = configure_logging()
    settings = load_settings()
    run_date = datetime.now(EASTERN)
    is_eod = (mode == "end-of-day")

    logger.info("==== Pipeline start: %s | mode=%s | session=%s | exec_mode=%s | phase=%s ====",
                run_date.strftime("%Y-%m-%d %H:%M ET"),
                mode,
                run_date.strftime("%Y-%m-%d"),
                settings["mode"],
                settings["phase"])

    # Skip silently outside market hours unless forced or this is the EOD pass.
    # is_market_open_today() catches NYSE holidays via pandas-market-calendars;
    # is_market_open_now() additionally enforces the 9:30-16:00 ET window.
    if is_eod:
        if not is_market_open_today(run_date) and not force:
            logger.info("Not a trading day — skipping EOD pass")
            return 0
    else:
        if not is_market_open_now(run_date) and not force:
            logger.info("Market closed (or outside 09:30-16:00 ET) — skipping intraday tick")
            return 0

    # Pull market data — intraday 15m bars by default, daily fallback at EOD
    # so the closing prices match what the daily analytics expect.
    symbols = universe_symbols() + [settings["benchmark_ticker"]]
    if is_eod:
        logger.info("Fetching daily close data for %d symbols", len(symbols))
        market_data = fetch_universe_data(symbols=symbols)
    else:
        logger.info("Fetching intraday 15m bars for %d symbols", len(symbols))
        market_data = fetch_intraday_data(symbols=symbols, interval="15m")
    prices = _prices_from_data(market_data)
    if not prices:
        logger.error("No prices fetched — aborting tick")
        send_alert("CRITICAL", "Market data failure", "No prices for any symbol")
        return 1

    executor = Executor()

    # Run each enabled model
    per_model_results: list[dict[str, Any]] = []
    for model_key, cfg in settings["models"].items():
        if not cfg.get("enabled", True):
            logger.info("[%s] disabled — skipping", model_key)
            continue
        try:
            r = run_one_model(model_key, cfg, market_data, prices, executor, run_date, settings, logger, is_eod=is_eod)
        except Exception as e:
            logger.exception("[%s] unhandled error: %s", model_key, e)
            send_alert("CRITICAL", f"Unhandled error in {model_key}", str(e), {"model": model_key})
            r = {"model_key": model_key, "status": "ERROR", "error": str(e)}
        per_model_results.append(r)

    # Build dashboard payload (intraday + EOD both refresh this — the dashboard
    # is always live)
    logger.info("Building dashboard payload")
    try:
        build_dashboard_payload(prices=prices)
    except Exception as e:
        logger.exception("Dashboard build failed: %s", e)
        send_alert("WARN", "Dashboard build failed", str(e))

    # EOD-only: report + summary alert
    if is_eod:
        # Index series for the daily prose — only fetched at EOD to keep
        # intraday ticks cheap
        logger.info("Fetching index data (S&P / Nasdaq / Dow) for daily report")
        index_data = fetch_index_data(lookback_days=5)

        logger.info("Generating daily report")
        try:
            report_path = generate_daily_report(
                run_date=run_date,
                market_data=market_data,
                index_data=index_data,
                settings=settings,
            )
            logger.info("Daily report at %s", report_path)
        except Exception as e:
            logger.exception("Daily report generation failed: %s", e)
            send_alert("WARN", "Daily report generation failed", str(e))

        leaderboard = build_leaderboard(list(settings["models"].keys()))
        summary = {
            "date": run_date.strftime("%Y-%m-%d"),
            "mode": settings["mode"],
            "models": per_model_results,
            "leaderboard": leaderboard,
        }
        send_daily_summary(summary)

    logger.info("==== Pipeline tick complete ====")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous LLM Trading Lab — intraday pipeline")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--intraday", action="store_true", help="Run a normal intraday tick (default)")
    group.add_argument("--end-of-day", action="store_true", help="Run the post-close EOD wrap-up")
    parser.add_argument("--force", action="store_true", help="Bypass the market-open check")
    args = parser.parse_args()

    if args.end_of_day:
        mode = "end-of-day"
    else:
        mode = "intraday"
    return run_pipeline(mode=mode, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
