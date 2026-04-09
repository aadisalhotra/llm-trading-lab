"""Daily pipeline orchestrator.

One run = one trading day:
  1. Load config + check market open
  2. Pull market data for universe + benchmark
  3. For each model:
       a. Load portfolio state
       b. Apply per-position stop-loss force-sells
       c. Build per-model prompt with current state
       d. Call adapter -> structured decisions
       e. Validate against risk rules
       f. Execute (paper or live)
       g. Check portfolio-level stop -> halt if needed
       h. Save state
       i. Log decisions + snapshot
       j. Record model version observation + check monthly transition
  4. Build dashboard JSON
  5. Send daily summary alert

Run as: python -m src.pipeline
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from typing import Any

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
from .data import fetch_index_data, fetch_universe_data, get_latest_price, is_market_open_today
from .logging import log_decision_run, log_daily_snapshot
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
) -> dict[str, Any]:
    logger.info("=== Running model: %s (%s/%s) ===", model_key, cfg["provider"], cfg["model"])

    portfolio = load_portfolio(model_key)
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

    # Build prompts + call model
    snapshot_before = portfolio.snapshot(prices)
    system_prompt, user_prompt, prompt_version = build_prompts(snapshot_before, market_data, run_date)
    data_hash = hash_inputs(market_data)

    adapter = get_adapter(cfg["provider"], cfg["model"])
    decision_result = adapter.generate_decision(system_prompt, user_prompt)

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
        # Still log the failure + snapshot
        log_decision_run(
            model_key=model_key, run_date=run_date,
            decision_result=decision_result,
            accepted_decisions=[], violations=[], execution_results=forced_results,
            portfolio_snapshot_after=portfolio.snapshot(prices),
            prompt_version=prompt_version, data_inputs_hash=data_hash,
            execution_mode=settings["mode"], inception_date=portfolio.inception_date,
        )
        save_portfolio(portfolio)
        log_daily_snapshot(model_key, run_date, portfolio.snapshot(prices), prices.get(settings["benchmark_ticker"]))
        return {"model_key": model_key, "status": "API_FAIL", "error": decision_result.error}

    # Validate against risk rules
    accepted, violations = validate_decisions(decision_result.decisions, portfolio, prices)

    # Execute
    exec_results = executor.execute_decisions(portfolio, accepted, prices)
    all_exec = forced_results + exec_results

    # Portfolio stop check AFTER execution
    if check_portfolio_stop(portfolio, prices):
        send_alert("CRITICAL", f"Portfolio stop hit: {model_key}",
                   "Liquidating to cash and halting model", {"model": model_key})
        portfolio.liquidate_all(prices)
        portfolio.halted = True

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
    log_daily_snapshot(model_key, run_date, snapshot_after,
                       prices.get(settings["benchmark_ticker"]))

    n_executed = sum(1 for e in all_exec if e.executed and e.side in ("BUY", "SELL"))
    logger.info("[%s] done — %d trades executed, %d violations, value=$%.2f",
                model_key, n_executed, len(violations), snapshot_after["total_value"])

    return {
        "model_key": model_key,
        "status": "OK",
        "trades_executed": n_executed,
        "violations": len(violations),
        "total_value": snapshot_after["total_value"],
        "cumulative_return": snapshot_after["cumulative_return"],
    }


def run_pipeline(force: bool = False) -> int:
    load_env()
    ensure_dirs()
    logger = configure_logging()
    settings = load_settings()
    run_date = datetime.utcnow()

    logger.info("==== Daily pipeline start: %s | mode=%s | phase=%s ====",
                run_date.strftime("%Y-%m-%d"), settings["mode"], settings["phase"])

    if not is_market_open_today(run_date) and not force:
        logger.info("Market closed today — exiting (use --force to override)")
        return 0

    # Pull market data
    symbols = universe_symbols() + [settings["benchmark_ticker"]]
    logger.info("Fetching market data for %d symbols", len(symbols))
    market_data = fetch_universe_data(symbols=symbols)
    prices = _prices_from_data(market_data)
    if not prices:
        logger.error("No prices fetched — aborting")
        send_alert("CRITICAL", "Market data failure", "No prices for any symbol")
        return 1

    # Pull index data for the daily report
    logger.info("Fetching index data (S&P / Nasdaq / Dow)")
    index_data = fetch_index_data(lookback_days=5)

    executor = Executor()

    # Run each enabled model
    per_model_results: list[dict[str, Any]] = []
    for model_key, cfg in settings["models"].items():
        if not cfg.get("enabled", True):
            logger.info("[%s] disabled — skipping", model_key)
            continue
        try:
            r = run_one_model(model_key, cfg, market_data, prices, executor, run_date, settings, logger)
        except Exception as e:
            logger.exception("[%s] unhandled error: %s", model_key, e)
            send_alert("CRITICAL", f"Unhandled error in {model_key}", str(e), {"model": model_key})
            r = {"model_key": model_key, "status": "ERROR", "error": str(e)}
        per_model_results.append(r)

    # Build dashboard payload
    logger.info("Building dashboard payload")
    try:
        build_dashboard_payload(prices=prices)
    except Exception as e:
        logger.exception("Dashboard build failed: %s", e)
        send_alert("WARN", "Dashboard build failed", str(e))

    # Generate daily research report
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

    # Daily summary
    leaderboard = build_leaderboard(list(settings["models"].keys()))
    summary = {
        "date": run_date.strftime("%Y-%m-%d"),
        "mode": settings["mode"],
        "models": per_model_results,
        "leaderboard": leaderboard,
    }
    send_daily_summary(summary)

    logger.info("==== Daily pipeline complete ====")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous LLM Trading Lab — daily pipeline")
    parser.add_argument("--force", action="store_true", help="Run even if market is closed")
    args = parser.parse_args()
    return run_pipeline(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
