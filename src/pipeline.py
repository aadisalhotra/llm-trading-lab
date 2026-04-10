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
from .analytics import build_leaderboard, compute_budget_status
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
    fetch_news,
    fetch_universe_data,
    hash_news_payload,
    is_market_open_now,
    is_market_open_today,
)
from .data.sentiment import compute_sentiment_dict
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
from .prompt_builder import (
    build_prompts,
    build_screening_prompt,
    hash_inputs,
    parse_screening_response,
)


def _prices_from_data(data: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for ticker, df in data.items():
        if df is not None and not df.empty:
            out[ticker] = float(df["Close"].iloc[-1])
    return out


def _compute_agreement_counts(
    model_key: str,
    execution_results: list[Any],
    settings: dict[str, Any],
) -> dict[str, int]:
    """For each executed ticker, count how many models (including this one) hold it.

    Reads the current state files for all other models. This is cheap — just
    reading JSON state files already on disk — and the result is logged per
    execution for long-term consensus analysis.
    """
    # Collect holdings across all models
    from .portfolio import load_portfolio as _lp
    all_holdings: dict[str, set[str]] = {}  # ticker -> set of model_keys holding it
    for key in settings["models"]:
        try:
            p = _lp(key)
            for ticker in p.holdings:
                all_holdings.setdefault(ticker, set()).add(key)
        except Exception:
            continue

    # For BUY executions, the buying model might not hold it yet (pre-execution),
    # so ensure the buying model is counted
    counts: dict[str, int] = {}
    for e in execution_results:
        if not e.executed or e.side in ("HOLD", "SKIP"):
            continue
        holders = all_holdings.get(e.ticker, set())
        if e.side == "BUY":
            holders = holders | {model_key}
        counts[e.ticker] = len(holders)
    return counts


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
    news_data: dict[str, Any] | None = None,
    sentiment_data: dict[str, float] | None = None,
    news_hash: str = "",
    ticker_order: list[dict[str, Any]] | None = None,
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

    snapshot_before = portfolio.snapshot(prices)
    adapter = get_adapter(cfg["provider"], cfg["model"])
    data_hash = hash_inputs(market_data)
    from .config_loader import universe_symbols as _universe_symbols

    # ---- Step 1: Screening call (lightweight — all 75 stocks) ----
    screening_sys, screening_user = build_screening_prompt(
        market_data, snapshot_before, run_date,
        news_data=news_data, sentiment_data=sentiment_data,
        ticker_order=ticker_order,
    )
    screening_raw = ""
    shortlisted: list[str] = []
    screening_metadata: dict[str, Any] = {}
    try:
        screening_result = adapter.generate_decision(screening_sys, screening_user)
        screening_raw = screening_result.raw_response
        screening_metadata = screening_result.metadata or {}
        held_tickers = [h["ticker"] for h in snapshot_before.get("holdings", [])]
        shortlisted = parse_screening_response(
            screening_raw, held_tickers, _universe_symbols(),
        )
        logger.info("[%s] Screening shortlist (%d): %s",
                    model_key, len(shortlisted), ", ".join(shortlisted))
    except Exception as e:
        logger.warning("[%s] Screening failed: %s — falling back to full universe", model_key, e)
        shortlisted = _universe_symbols()[:20]

    # ---- Step 2: Trading decision call (full data, shortlisted stocks) ----
    system_prompt, user_prompt, prompt_version, images = build_prompts(
        snapshot_before,
        market_data,
        run_date,
        trades_executed_today=portfolio.intraday.trades_executed_today,
        runs_today=portfolio.intraday.runs_today,
        is_eod=is_eod,
        include_chart_image=True,
        news_data=news_data,
        sentiment_data=sentiment_data,
        shortlisted_symbols=shortlisted,
    )

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
            news_headlines_hash=news_hash,
            news_sentiment=sentiment_data or {},
            agreement_counts=_compute_agreement_counts(model_key, forced_results, settings),
            screening_response=screening_raw,
            screening_shortlist=shortlisted,
            screening_metadata=screening_metadata,
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
        news_headlines_hash=news_hash,
        news_sentiment=sentiment_data or {},
        agreement_counts=_compute_agreement_counts(model_key, all_exec, settings),
        screening_response=screening_raw,
        screening_shortlist=shortlisted,
        screening_metadata=screening_metadata,
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

    # Fetch news intelligence ONCE per pipeline tick — not per model — so all
    # 6 models see the same headline set on the same tick. The news module
    # caches to disk with a TTL so the actual provider call only fires when
    # the cache is stale (default: every 60 minutes).
    news_data: dict[str, Any] = {}
    sentiment_data: dict[str, float] = {}
    news_hash = ""
    try:
        news_data = fetch_news(symbols=universe_symbols(), settings=settings)
        if news_data:
            sentiment_data = compute_sentiment_dict(news_data)
            news_hash = hash_news_payload(news_data)
            n_stocks_with_news = sum(1 for k, v in news_data.items() if k != "macro" and v)
            logger.info("News context: %d stocks with headlines, %d macro headlines, hash=%s",
                        n_stocks_with_news, len(news_data.get("macro", [])), news_hash)
        else:
            logger.info("News context: empty (no provider key, cache miss, or all providers failed) — pipeline runs without headlines")
    except Exception as e:
        logger.exception("News fetch failed unexpectedly: %s — pipeline continues without headlines", e)

    executor = Executor()

    # Randomize universe ticker order ONCE per pipeline run — all models see
    # the same shuffled order for fairness, but the order differs across runs
    # so no stock is consistently buried at the bottom of the list.
    import random as _random
    universe = load_universe()
    ticker_order = list(universe["tickers"])
    _random.shuffle(ticker_order)

    # Run each enabled model
    per_model_results: list[dict[str, Any]] = []
    for model_key, cfg in settings["models"].items():
        if not cfg.get("enabled", True):
            logger.info("[%s] disabled — skipping", model_key)
            continue
        try:
            r = run_one_model(
                model_key, cfg, market_data, prices, executor, run_date, settings, logger,
                is_eod=is_eod,
                news_data=news_data,
                sentiment_data=sentiment_data,
                news_hash=news_hash,
                ticker_order=ticker_order,
            )
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

        # Budget check — month-to-date spend per provider against the
        # configured monthly cap. Emits WARN at warn_threshold_pct (default
        # 80%) and CRITICAL at critical_threshold_pct (default 100%). Never
        # halts trading — this is observability, not enforcement.
        try:
            budget = compute_budget_status(settings)
            if budget["any_critical"] or budget["any_warn"]:
                lines = []
                for provider, info in budget["providers"].items():
                    if info["status"] == "ok":
                        continue
                    lines.append(
                        f"{provider}: ${info['spend_usd']:.2f} / ${info['cap_usd']:.2f} "
                        f"({info['pct_of_cap']*100:.0f}% of cap, {info['status'].upper()})"
                    )
                detail = "; ".join(lines)
                if budget["any_critical"]:
                    logger.error("BUDGET CRITICAL — %s", detail)
                    send_alert("CRITICAL", "Monthly API budget exceeded",
                               f"One or more providers over their monthly cap (trading continues): {detail}",
                               {"budget": budget})
                else:
                    logger.warning("BUDGET WARN — %s", detail)
                    send_alert("WARN", "Monthly API budget approaching cap",
                               f"One or more providers ≥80% of monthly cap: {detail}",
                               {"budget": budget})
            else:
                logger.info("Budget check: all providers below %.0f%% of monthly cap",
                            budget["warn_threshold_pct"] * 100)
        except Exception as e:
            logger.exception("Budget check failed: %s", e)

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
