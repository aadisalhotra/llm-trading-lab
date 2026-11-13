"""Intraday + EOD pipeline orchestrator.

The pipeline runs in one of two modes, controlled by CLI flags:

  --intraday    (default): one 30-minute pipeline tick during market hours.
                Fetches intraday bars, runs each model, executes any trades
                (subject to the persistent daily trade cap), logs an
                intraday valuation snapshot, and rebuilds dashboard.json.
                Skips silently if NYSE is not currently open (unless --force).

  --end-of-day: post-close wrap-up. Does everything an intraday tick does
                AND writes the EOD row to /data/performance/{model}.jsonl,
                generates the daily research report, and sends the daily
                summary alert. Should fire once per trading day after 16:00 ET.

  --force       run the pipeline even if invoked off-schedule. Does NOT
                bypass the market-hours check — if NYSE is closed (weekend,
                holiday, after-hours), the tick still skips. Use this to
                force a manual run during market hours when the cron didn't
                fire.

  --force-trade implies --force AND bypasses the market-hours check. Use
                this only for testing trade execution outside market hours;
                it will execute live decisions on stale prices.

The persistent intraday trade cap lives in
`Portfolio.intraday.trades_executed_today` and resets automatically when a
new ET trading day begins. Models therefore see the *whole-day* 50-trade
budget across all 13 ticks of the day, not a fresh 30 every 30 minutes.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

# All run dates are anchored to US/Eastern — the trading-session calendar.
EASTERN = ZoneInfo("America/New_York")

from .adapters import get_adapter
from .alerts import scan_macro_events, send_alert, send_daily_summary, send_heartbeat
from .alerts import preflight
from .alerts.preflight import assert_configured as alert_preflight
from .analytics import build_leaderboard, compute_budget_status
from .analytics.intended_vs_expressed import write_session_descriptor
from .config_loader import (
    configure_logging,
    effective_mode,
    ensure_dirs,
    load_env,
    load_settings,
    load_universe,
    universe_symbols,
    venue_book_keys,
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
from .data.market_data import session_bounds
from .data.sentiment import compute_sentiment_dict
from .logging import (
    log_decision_run,
    log_daily_snapshot,
    eod_row_exists,
    log_intraday_snapshot,
    read_recent_decisions,
    read_last_action_per_ticker,
    detect_memory_hit,
    boundary_parts,
    check_spacing,
    record_skip,
    load_ledger,
    is_boundary_handled,
    mark_boundary_handled,
)
from .reports import generate_daily_report
from .model_versions import detect_version_transition, record_observation
from .data.price_validity import (
    PriceIngestion,
    holding_marks,
    ingest_market_data,
    is_valid_price,
    missing_price_events,
    record_data_availability,
)
from .portfolio import (
    RiskViolation,
    check_position_stops,
    load_portfolio,
    save_portfolio,
    validate_decisions,
)
from .portfolio.risk import (
    STOP_BREACH,
    STOP_UNEVALUABLE,
    evaluate_portfolio_stop,
    position_stops_unevaluable,
    stop_dust_holdings,
)
from .execution import CycleBarrier, Executor
from .execution.executor import alarm_fill_overruns, alarm_unclassified_skips
from .execution.barrier import rotation_tick
from .execution.reconciliation import run_post_cycle_reconciliation
from .prompt_builder import (
    build_prompts,
    build_screening_prompt,
    hash_inputs,
    parse_screening_response,
    uses_per_ticker_last_action,
)


def _prices_from_data(data: dict) -> dict[str, float]:
    """Valid last-bar prices only (see src/data/price_validity.py)."""
    return ingest_market_data(data).prices


def _eod_unvaluable_closes(prices: dict[str, float], settings: dict[str, Any],
                           model_keys: list[str]) -> list[str]:
    """Closes the EOD valuation needs that are missing or non-finite.

    The valuation set is every ticker any enabled book holds (long or short) plus
    the benchmark. On 2026-11-11 the vendor served the day's daily bar with
    Close = NaN and the EOD run wrote NaN rows, a NaN report and seven false
    threshold alerts (reverted in c67c15418/ed7d825ce).
    """
    needed = {settings["benchmark_ticker"]}
    for mk in model_keys:
        needed.update(load_portfolio(mk).holdings)
    return sorted(t for t in needed if not is_valid_price(prices.get(t)))


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


def _position_stop_sweep(
    model_key: str, portfolio: Any, prices: dict[str, float], executor: Executor,
    session_date_str: str, logger: logging.Logger,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """The per-position stop sweep, run before the model trades.

    Returns (forced exit results, §⑧ dust records). Dust -- notional below
    BEHAVIOURAL_NOTIONAL_FLOOR at the stop-evaluation mark -- is not evaluated:
    no forced order, no alert, and so never a STOP UNPROTECTED. Each dust
    holding gets one log line here and one entry in the cycle's decision-log
    `stop_dust_exempt`.
    """
    stop_dust_exempt = stop_dust_holdings(portfolio, prices)
    for d in stop_dust_exempt:
        logger.info("[%s] Position stop: %s is dust (%g sh x %.4f = $%.4f, below the $1 "
                    "behavioural floor) -- not evaluated (ghost-position ruling §⑧)",
                    model_key, d["ticker"], d["shares"], d["mark"], d["notional"])

    triggered = check_position_stops(portfolio, prices)
    unevaluable = position_stops_unevaluable(portfolio, prices)
    if unevaluable:
        # Can't evaluate is a classified state, never a quiet pass. Once per
        # book per day: vendor gaps of a name or two per fetch are routine.
        send_alert("WARN", f"Position stop unevaluable: {model_key}",
                   "No valid price this cycle for held name(s); their per-position "
                   "stops were not tested.",
                   {"model": model_key, "tickers": ", ".join(unevaluable)},
                   kind="stop_unevaluable",
                   dedup_key=f"stop_unevaluable:{model_key}:{session_date_str}")
    forced_results: list[Any] = []
    if triggered:
        send_alert("WARN", f"Position stop on {model_key}",
                   f"Force-selling: {triggered}", {"model": model_key},
                   kind="position_stop", dedup_key=f"position_stop:{model_key}")
        forced_results = executor.force_liquidate(portfolio, triggered, prices, "POSITION_STOP",
                                                  run_date=session_date_str)
    return forced_results, stop_dust_exempt


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
    barrier: CycleBarrier | None = None,
    fetch_started_at: datetime | None = None,
    eod_valuation_ok: bool = True,
    ingestion: PriceIngestion | None = None,
    marks_by_book: dict[str, dict[str, tuple[float, str]]] | None = None,
) -> dict[str, Any]:
    logger.info("=== Running model: %s (%s/%s) ===", model_key, cfg["provider"], cfg["model"])

    portfolio = load_portfolio(model_key)

    # Held names without a valid price this cycle are valued on a classified
    # fallback mark (src/data/price_validity.py) so the book's total value stays
    # finite and its other orders size normally. Each is a MISSING_PRICE event;
    # the pipeline records the marks in the data-availability descriptor.
    if ingestion is not None:
        portfolio.fallback_marks = holding_marks(portfolio.holdings, ingestion)
        if marks_by_book is not None:
            marks_by_book[model_key] = dict(portfolio.fallback_marks)

    # Reset the intraday counters at the start of a new ET trading day
    session_date_str = run_date.strftime("%Y-%m-%d")
    if portfolio.reset_intraday_if_new_session(session_date_str):
        logger.info("[%s] New session detected — intraday counters reset", model_key)

    if portfolio.halted:
        logger.warning("[%s] Portfolio HALTED — skipping", model_key)
        return {"model_key": model_key, "status": "HALTED"}

    # Per-position stop-loss sweep BEFORE the model trades. Skipped on an EOD
    # pass whose valuation aborted: its one CRITICAL already names every
    # missing close, and nothing is sized or valued.
    valuation_aborted = is_eod and not eod_valuation_ok
    forced_results, stop_dust_exempt = ([], []) if valuation_aborted else _position_stop_sweep(
        model_key, portfolio, prices, executor, session_date_str, logger)

    snapshot_before = portfolio.snapshot(prices)
    adapter = get_adapter(cfg["provider"], cfg["model"])
    data_hash = hash_inputs(market_data)
    from .config_loader import universe_symbols as _universe_symbols

    # EOD pass: no trading — only snapshot, log, and report. The EOD cron
    # fires after market close to capture closing prices and generate the
    # daily report. It must NOT execute any trades.
    if is_eod:
        snapshot_after = snapshot_before
        portfolio.record_intraday_run(run_date.isoformat(), trades_executed=0)
        save_portfolio(portfolio)
        if not eod_valuation_ok:
            # Housekeeping only: no intraday or performance row from a valuation
            # built on a missing or non-finite close. run_pipeline has alerted.
            return {"model_key": model_key, "status": "EOD_VALUATION_ABORTED",
                    "trades_executed": 0,
                    "trades_today": portfolio.intraday.trades_executed_today,
                    "runs_today": portfolio.intraday.runs_today}
        log_intraday_snapshot(
            model_key=model_key, run_timestamp=run_date,
            snapshot=snapshot_after,
            benchmark_value=prices.get(settings["benchmark_ticker"]),
            trades_executed_this_run=0,
            trades_executed_today=portfolio.intraday.trades_executed_today,
            runs_today=portfolio.intraday.runs_today,
            api_success=True,
        )
        log_daily_snapshot(model_key, run_date, snapshot_after,
                           prices.get(settings["benchmark_ticker"]),
                           api_success=True)
        return {
            "model_key": model_key,
            "status": "OK",
            "trades_executed": 0,
            "trades_today": portfolio.intraday.trades_executed_today,
            "runs_today": portfolio.intraday.runs_today,
            "violations": 0,
            "total_value": snapshot_after["total_value"],
            "cumulative_return": snapshot_after["cumulative_return"],
        }

    # ---- Step 1: Screening call (lightweight — all 79 assets) ----
    screening_sys, screening_user = build_screening_prompt(
        market_data, snapshot_before, run_date,
        news_data=news_data, sentiment_data=sentiment_data,
        ticker_order=ticker_order,
    )
    screening_raw = ""
    shortlisted: list[str] = []
    screening_metadata: dict[str, Any] = {}
    _scr_latency: float | None = None
    try:
        screening_raw, _scr_latency, screening_metadata = adapter.generate_raw(
            screening_sys, screening_user,
        )
        held_tickers = [h["ticker"] for h in snapshot_before.get("holdings", [])]
        shortlisted = parse_screening_response(
            screening_raw, held_tickers, _universe_symbols(),
        )
        logger.info("[%s] Screening shortlist (%d): %s",
                    model_key, len(shortlisted), ", ".join(shortlisted))
    except Exception as e:
        logger.warning("[%s] Screening failed: %s — falling back to full universe", model_key, e)
        shortlisted = _universe_symbols()[:20]

    # ---- Rolling memory: last 10 executed BUY/SELL for THIS model only ----
    # Each model only ever sees its own history (read from its own JSONL file),
    # so there is no cross-model contamination and no shared state with the
    # other parallel model threads.
    try:
        recent_decisions = read_recent_decisions(model_key, limit=10, now=run_date,
                                                 since=portfolio.inception_date)
        logger.info("[%s] memory: loaded %d recent decisions",
                    model_key, len(recent_decisions))
    except Exception as e:
        logger.warning("[%s] memory: recent-decisions read failed: %s — continuing without memory", model_key, e)
        recent_decisions = []

    # ---- v2 anti-reversal: most-recent executed BUY/SELL per shortlisted ticker ----
    # Additive to the last-10 rolling memory above. The last-10 is a global
    # recent-activity narrative; this is a per-ticker lookup so the model can see
    # its own prior action on EACH ticker it is considering — including names it
    # sold to zero, which drop out of both the holdings table and the last-10
    # window. Gated to the v2 prompt: under v1 we skip the walk, so live behavior
    # and cost are unchanged until v2 ships.
    last_actions: dict[str, Any] = {}
    if uses_per_ticker_last_action(settings.get("prompt_version", "")):
        try:
            last_actions = read_last_action_per_ticker(model_key, shortlisted, now=run_date,
                                                       since=portfolio.inception_date)
            logger.info("[%s] memory: per-ticker last-action resolved for %d/%d shortlisted",
                        model_key, len(last_actions), len(shortlisted))
        except Exception as e:
            logger.warning("[%s] memory: per-ticker last-action read failed: %s — continuing without it",
                           model_key, e)
            last_actions = {}

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
        recent_decisions=recent_decisions,
        last_actions=last_actions,
    )

    # Only send images to vision-capable models. Text-only adapters can
    # accept the kwarg but it's wasteful to base64-encode for nothing.
    images_for_call = images if (cfg.get("vision_capable", False) and adapter.supports_vision) else None
    decision_result = adapter.generate_decision(system_prompt, user_prompt, images=images_for_call)

    # Surface retry activity in the pipeline log so we can see at a glance
    # whether a model recovered on a second attempt vs. succeeded on first.
    # The attempt field is set by BaseAdapter for every provider.
    _attempt = int((decision_result.metadata or {}).get("attempt") or 1)
    if _attempt > 1:
        status_word = "recovered via retry" if decision_result.success else "failed after retry"
        logger.warning("[%s] %s on attempt %d", model_key, status_word, _attempt)

    # Memory-hit detection: does the model's reasoning text reference a
    # prior decision? Best-effort — failures are logged but never fatal.
    try:
        memory_hit = detect_memory_hit(decision_result)
        if memory_hit:
            logger.info("[%s] memory: reasoning references prior decision (hit)", model_key)
    except Exception as e:
        logger.warning("[%s] memory: hit-detection failed: %s", model_key, e)
        memory_hit = False

    # Record the observed version on every run, success or failure (failures
    # log the configured id as a synthetic observation). The api_success flag
    # lets detect_version_transition compare only successful observations.
    record_observation(model_key, decision_result.model_id_returned, run_date,
                       api_success=decision_result.success)
    transition = detect_version_transition(model_key, run_date)
    if transition:
        send_alert("INFO", f"Model transition: {model_key}",
                   f"{transition['old_version']} -> {transition['new_version']}", transition,
                   kind="model_transition")

    if not decision_result.success:
        logger.error("[%s] Adapter failure: %s", model_key, decision_result.error)
        send_alert("WARN", f"Model {model_key} failed", decision_result.error or "unknown",
                   {"model": model_key},
                   kind="api_failure", dedup_key=f"api_failure:{model_key}")
        # Still log the failure + intraday snapshot
        log_decision_run(
            model_key=model_key, run_date=run_date,
            decision_result=decision_result,
            accepted_decisions=[], violations=[], execution_results=forced_results,
            portfolio_snapshot_after=portfolio.snapshot(prices),
            prompt_version=prompt_version, data_inputs_hash=data_hash,
            execution_mode=effective_mode(model_key, settings),
            inception_date=settings["experiment_start_date"],
            news_headlines_hash=news_hash,
            news_sentiment=sentiment_data or {},
            agreement_counts=_compute_agreement_counts(model_key, forced_results, settings),
            screening_response=screening_raw,
            screening_shortlist=shortlisted,
            screening_metadata=screening_metadata,
            memory_hit=memory_hit,
            cycle_id=executor.cycle_id,
            fetch_started_at=fetch_started_at.isoformat() if fetch_started_at else "",
            screening_latency_seconds=_scr_latency,
            stop_dust_exempt=stop_dust_exempt,
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
    # Decisions the adapter's parser could not read never reach the validator.
    # Their named reasons join its violations, so the record holds both.
    violations = [RiskViolation(d["index"], d["rule"], d["detail"])
                  for d in (decision_result.metadata or {}).get("parse_drops", [])] + violations

    # Execute. The run date drives T+1 settlement under the cash branch's
    # settled-funds constraint; pass the pipeline's own date rather than letting
    # the executor read a wall clock, so a replay settles deterministically.
    exec_results = executor.execute_decisions(portfolio, accepted, prices,
                                              run_date=session_date_str,
                                              barrier=barrier)
    all_exec = forced_results + exec_results

    # Portfolio stop check AFTER execution
    halt_verdict = evaluate_portfolio_stop(portfolio, prices)
    if halt_verdict == STOP_UNEVALUABLE:
        send_alert("CRITICAL", f"Drawdown halt unevaluable: {model_key}",
                   "A held name has no valid price this cycle and the book is not "
                   "provably clear of the drawdown stop with it valued at zero (or "
                   "it is an unpriced short). The halt was neither triggered nor "
                   "passed.",
                   {"model": model_key,
                    "unpriced": ", ".join(position_stops_unevaluable(portfolio, prices))},
                   kind="drawdown_unevaluable",
                   dedup_key=f"drawdown_unevaluable:{model_key}:{session_date_str}")
    if halt_verdict == STOP_BREACH:
        send_alert("CRITICAL", f"Portfolio stop hit: {model_key}",
                   "Liquidating to cash and halting model", {"model": model_key},
                   kind="portfolio_halt", dedup_key=f"portfolio_halt:{model_key}")
        # Routed through the executor so the halt reaches the venue. Calling
        # Portfolio.liquidate_all directly flattened the book without placing
        # a single order, which in a broker mode leaves every position open
        # behind a book showing all cash.
        halt_results = executor.liquidate_all(portfolio, prices,
                                              run_date=session_date_str)
        all_exec = all_exec + halt_results
        portfolio.halted = True

    # Any stop — position-level or portfolio-level — that did not close at the
    # venue leaves live exposure with its safety disabled. This is the loudest
    # condition the system can report, and it must never be inferred from a
    # quiet log line.
    unprotected = [e for e in all_exec if getattr(e, "stop_unprotected", False)]
    if unprotected:
        send_alert(
            "CRITICAL", f"STOP UNPROTECTED: {model_key}",
            "A risk stop did not close at the venue. The position is still "
            "open and the book correctly does not show it as closed.",
            {"model": model_key,
             "tickers": ", ".join(sorted({e.ticker for e in unprotected})),
             "detail": "; ".join(f"{e.ticker}: {e.error or e.constraint}"
                                 for e in unprotected)[:500]},
            kind="stop_unprotected", dedup_key=f"stop_unprotected:{model_key}")

    # A skip with no registered class is an execution failure and an alarm, so
    # the next silent suppression fails loudly on first firing (ruling 5.3).
    alarm_unclassified_skips(model_key, all_exec, send_alert, cycle_id=executor.cycle_id)
    alarm_fill_overruns(model_key, all_exec, send_alert, cycle_id=executor.cycle_id)

    # Update intraday counters with this run's trade count. Shorts and covers
    # are trades too and count toward the daily cap.
    n_executed = sum(1 for e in all_exec if e.executed and e.side in ("BUY", "SELL", "SHORT", "COVER"))
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
        execution_mode=effective_mode(model_key, settings),
            inception_date=settings["experiment_start_date"],
        news_headlines_hash=news_hash,
        news_sentiment=sentiment_data or {},
        agreement_counts=_compute_agreement_counts(model_key, all_exec, settings),
        screening_response=screening_raw,
        screening_shortlist=shortlisted,
        screening_metadata=screening_metadata,
        memory_hit=memory_hit,
        cycle_id=executor.cycle_id,
        fetch_started_at=fetch_started_at.isoformat() if fetch_started_at else "",
        screening_latency_seconds=_scr_latency,
        stop_dust_exempt=stop_dust_exempt,
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


def _eod_skip_reason(settings: dict[str, Any], run_date: datetime) -> str | None:
    """Why this EOD pass must do nothing, or None if it should run.

    The EOD backup is bare cron, and GitHub delays it by hours: on 2026-11-03 it
    fired at 22:16Z, 00:54Z and 01:52Z. Each late run used to reload every book,
    rewrite all six state files, re-run the position-stop sweep and rebuild the
    report. log_daily_snapshot's own duplicate check only stops the row; by then
    the book is already loaded, and a load after the epoch archive CREATES a
    fresh book, early, on a run that decides nothing.

    So both checks sit ahead of the first load.

      * The day's rows all exist: the work is done. The rows land in the same
        commit as the report and the digest marker, and that commit runs only
        after the pipeline exits 0, so their presence in this checkout means the
        whole primary pass finished.
      * The session has not closed: a cron delayed past midnight ET reaches
        here carrying the NEXT session's date, before that session has opened.
        It would book a row of stale closes for a day that has not traded.
    """
    bounds = session_bounds(run_date)
    if bounds is not None and run_date.timetz().replace(tzinfo=None) < bounds[1]:
        return f"session has not closed ({bounds[1]} ET)"
    day = run_date.strftime("%Y-%m-%d")
    enabled = [mk for mk, cfg in settings["models"].items() if cfg.get("enabled", True)]
    if enabled and all(eod_row_exists(mk, day) for mk in enabled):
        return f"every enabled book already has its {day} row"
    return None


def run_pipeline(mode: str = "intraday", force: bool = False,
                 force_trade: bool = False) -> int:
    """Run one pipeline tick.

    `mode` is one of:
      "intraday"   — normal in-session tick (default)
      "end-of-day" — post-close wrap-up that also writes the EOD perf row,
                     generates the daily report, and sends the summary alert

    `force` allows the pipeline to run when invoked off-schedule, but does
    NOT bypass the market-hours check — trades only happen when the market
    is genuinely open. `force_trade` additionally bypasses market hours
    (use only for testing).
    """
    load_env()
    ensure_dirs()
    logger = configure_logging()
    settings = load_settings()
    run_date = datetime.now(EASTERN)
    is_eod = (mode == "end-of-day")

    # force_trade implies force
    if force_trade:
        force = True

    pipeline_start = time.monotonic()
    logger.info("==== Pipeline start: %s | mode=%s | session=%s | exec_mode=%s | phase=%s | force=%s | force_trade=%s ====",
                run_date.strftime("%Y-%m-%d %H:%M ET"),
                mode,
                run_date.strftime("%Y-%m-%d"),
                settings["mode"],
                settings["phase"],
                force, force_trade)

    # Alerting preflight. NON-FATAL by design: a missing recipient list must
    # never stop a trading tick — the experiment is the point and email is
    # instrumentation. The loud, run-reddening signal lives in the workflow's
    # own `alerting-preflight` job, which nothing needs, so it cannot break the
    # run -> chain self-chain. This CRITICAL line is the second line of defence
    # and the one that lands in the committed pipeline log.
    alert_preflight(channels=(preflight.DAILY,), settings=settings, strict=False)

    # Market-hours guard. Only --force-trade bypasses this — bare --force
    # used to bypass it but that let weekend/holiday "force" runs execute
    # trades on stale prices.
    # is_market_open_today() catches NYSE holidays via pandas-market-calendars;
    # is_market_open_now() additionally enforces the day's own session window
    # from the NYSE calendar (09:30-16:00 ET, or the early close, e.g. 13:00).
    if is_eod:
        if not is_market_open_today(run_date) and not force_trade:
            logger.info("Not a trading day — skipping EOD pass (use --force-trade to override)")
            return 0
        if not force_trade:
            eod_skip = _eod_skip_reason(settings, run_date)
            if eod_skip:
                logger.info("Skipping EOD pass: %s (no book loaded)", eod_skip)
                return 0
    else:
        if not is_market_open_now(run_date) and not force_trade:
            logger.info("Market closed (or outside today's NYSE session) — skipping intraday tick (use --force-trade to override)")
            return 0

    # --- Per-boundary idempotency guard (intraday only) ----------------------
    # Exactly-once per 30-min decision tick. Stops a duplicate intraday run — a
    # Fix A dispatch retry, a manual force-run, a backup-cron double-fire, or
    # multi-trigger redundancy — from re-prompting the models and double-trading
    # the same boundary. Checked BEFORE the data fetch and the model loop, so a
    # duplicate exits having done nothing.
    #
    # EOD is not guarded HERE: it places no model trades (run_one_model's is_eod
    # early-return is snapshot/report only), and its per-day idempotency is
    # _eod_skip_reason above, which runs ahead of the first book load, plus its
    # deliberate two-trigger redundancy (chain handoff + 22:00 UTC cron).
    #
    # RACE-FREEDOM DEPENDS ON THE intraday-pipeline CONCURRENCY GROUP in
    # .github/workflows/intraday.yml (cancel-in-progress: false): it serializes
    # runs so each one checks out the prior run's committed ledger before it
    # starts. DO NOT remove that group without the atomic claim-commit hardening
    # (see src/logging/boundary_ledger.py + docs/MONITORING.md).
    if not is_eod:
        b_day, b_slot = boundary_parts(run_date)
        _ledger, _ledger_err = load_ledger()
        if _ledger_err is not None:
            # FAIL OPEN: a silently-halted pipeline is the exact failure class
            # this workstream exists to kill, so a corrupt ledger must never
            # block trading. But make it LOUD on real channels, not just a log
            # line — CRITICAL email (deduped once/day) plus an external-watchdog
            # /fail ping for an SMS/push.
            logger.error("Idempotency ledger unreadable (%s) — proceeding WITHOUT "
                         "duplicate protection for %s %s", _ledger_err, b_day, b_slot)
            send_alert("CRITICAL", "Idempotency ledger unreadable",
                       f"data/state/handled_boundaries.json could not be read "
                       f"({_ledger_err}). Proceeding WITHOUT duplicate protection for "
                       f"boundary {b_day} {b_slot}; a duplicate or concurrent run could "
                       f"double-trade until this is fixed.",
                       kind="idempotency_ledger", dedup_key="idempotency_ledger_unreadable")
            send_heartbeat(success=False, settings=settings)
        elif is_boundary_handled(_ledger, b_day, b_slot):
            logger.info("Boundary %s %s already handled — skipping duplicate tick "
                        "(no models prompted, no trades)", b_day, b_slot)
            return 0

    # --- Minimum-spacing guard (intraday only) -------------------------------
    # The boundary guard above keys on the :00/:30 slot, so it cannot see a
    # duplicate that lands in a DIFFERENT slot minutes later — 10:59 and 11:01
    # are two boundaries and two minutes apart. This one keys on elapsed time
    # since the cohort actually last decided.
    #
    # Reachable since the intraday checkout moved to the branch tip: a run
    # queued behind another tick used to trade a stale book and lose its commit
    # to a rebase conflict, so its duplicate never landed. It lands correctly
    # now, and can land minutes after the tick it queued behind.
    #
    # A backup run filling a GENUINE gap must still run — that is the whole
    # point of the backup cron, and 2026-08-17's 62-minute cohort-wide hole is
    # the case it exists for. The reference is therefore the last COMPLETED
    # decision in the committed logs, not the schedule.
    #
    # Fails open, like the ledger above: check_spacing() swallows read errors
    # and returns skip=False, so an unreadable log trades rather than halting.
    if not is_eod and not force_trade:
        verdict = check_spacing(settings, run_date.astimezone(timezone.utc))
        if verdict.skip:
            logger.info(
                "Spacing guard: last cohort decision %s was %.1f min ago "
                "(minimum %d) — skipping tick (no models prompted, no trades)",
                verdict.last_decision.isoformat(), verdict.gap_minutes,
                verdict.min_minutes,
            )
            record_skip(verdict, run_date.astimezone(timezone.utc))
            return 0

    # Pull market data — intraday 30m bars by default, daily fallback at EOD
    # so the closing prices match what the daily analytics expect.
    symbols = universe_symbols() + [settings["benchmark_ticker"]]
    # Latency instrumentation (Hub registration, 2026-09-17): stamped
    # immediately before the shared fetch call, so it is the earliest point
    # in the tick from which fetch->decision is reconstructable. One fetch
    # serves every model this tick, so this single timestamp is shared by
    # all of them in the decision log.
    fetch_started_at = datetime.now(timezone.utc)
    if is_eod:
        logger.info("Fetching daily close data for %d symbols", len(symbols))
        market_data = fetch_universe_data(symbols=symbols)
    else:
        logger.info("Fetching intraday 30m bars for %d symbols", len(symbols))
        market_data = fetch_intraday_data(symbols=symbols, interval="30m")
    # Price-validity guard at ingestion, before anything reads a price or the
    # prompt sees a frame: a ticker without a valid last-bar price is not
    # tradable this cycle and renders as NO DATA (price_validity_and_source_ruling.md).
    ingestion = ingest_market_data(market_data)
    market_data = ingestion.market_data
    prices = ingestion.prices
    marks_by_book: dict[str, dict[str, tuple[float, str]]] = {}
    # An EOD pass with no valid price at all is the 2026-11-11 shape: it goes to
    # the EOD valuation guard below (one CRITICAL, housekeeping, exit 0) rather
    # than aborting before the housekeeping runs.
    if not prices and not is_eod:
        logger.error("No prices fetched — aborting tick")
        send_alert("CRITICAL", "Market data failure", "No prices for any symbol",
                   kind="market_data_failure", dedup_key="market_data_failure")
        # Trip the external liveness watchdog immediately on a known-fatal
        # intraday abort, rather than waiting out its grace window. EOD runs
        # outside the watchdog's market-hours scope, so it does not ping.
        if not is_eod:
            send_heartbeat(success=False, settings=settings)
        return 1

    # EOD valuation guard. A missing or non-finite close for any held name or
    # the benchmark aborts the valuation outputs (performance and intraday rows,
    # dashboard, daily report, leaderboard, digest and its threshold sweep) with
    # one CRITICAL. Housekeeping still runs, and the run exits 0 so it commits.
    eod_bad_closes: list[str] = []
    if is_eod:
        eod_bad_closes = _eod_unvaluable_closes(
            prices, settings,
            [mk for mk, c in settings["models"].items() if c.get("enabled", True)])
        if eod_bad_closes:
            day = run_date.strftime("%Y-%m-%d")
            logger.critical("EOD valuation aborted for %s: non-finite closes for %d names: %s",
                            day, len(eod_bad_closes), ", ".join(eod_bad_closes))
            send_alert("CRITICAL",
                       f"End-of-day valuation aborted: non-finite closes for {len(eod_bad_closes)} names",
                       "The end-of-day run wrote no performance, intraday, leaderboard or "
                       "daily-report rows and ran no threshold alerts. Housekeeping ran. "
                       "The day's row is backfilled once real closes exist.",
                       {"date": day, "names": ", ".join(eod_bad_closes)[:500]},
                       kind="eod_valuation_aborted", dedup_key=f"eod_valuation_aborted:{day}")

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

    # Trigger #11 — market-wide event scan. Runs every tick off the freshly
    # fetched macro feed (plus the on-disk caches) so a war, an emergency rate
    # decision, a crash/circuit-breaker, etc. fires an immediate CRITICAL
    # alert as it surfaces — not deferred to the EOD sweep. De-duplicated once
    # per event category per ET day, so it can't re-fire across the day's ticks.
    try:
        scan_macro_events(news_data=news_data, settings=settings)
    except Exception as e:
        logger.exception("Macro market-event scan failed: %s — pipeline continues", e)

    executor = Executor()

    # Randomize universe ticker order ONCE per pipeline run — all models see
    # the same shuffled order for fairness, but the order differs across runs
    # so no stock is consistently buried at the bottom of the list.
    import random as _random
    universe = load_universe()
    ticker_order = list(universe["tickers"])
    _random.shuffle(ticker_order)

    # Run each enabled model in parallel. Every model reads/writes its own
    # portfolio state file and its own log files, so the only shared mutation
    # is the alerts.jsonl append path — Python's file append + the stdlib
    # logging module are already thread-safe, so no extra lock is needed.
    # The dashboard build below runs only after all threads have joined.
    enabled_models = [
        (mk, cfg) for mk, cfg in settings["models"].items()
        if cfg.get("enabled", True)
    ]
    for mk, cfg in settings["models"].items():
        if not cfg.get("enabled", True):
            logger.info("[%s] disabled — skipping", mk)

    # P1 cross-book submission barrier (docs/prereg/tier2_novel_sections.md,
    # "Cross-book submission barrier"). One instance per cycle, shared by
    # reference across every model's thread below. Only meaningful in broker
    # modes — the simulator has no venue to collide on, and Executor skips
    # barrier registration entirely when `barrier` is None, so paper-mode
    # cycles are unaffected (no extra thread-coordination overhead, no
    # behavior change).
    barrier: CycleBarrier | None = None
    if executor.broker_enabled:
        exec_cfg = executor.settings.get("execution") or {}
        barrier = CycleBarrier(
            # Venue books only: a simulator book (DeepSeek) never registers,
            # so waiting for it would stall every cycle to the timeout.
            expected_books=set(venue_book_keys([mk for mk, _ in enabled_models],
                                               executor.settings)),
            t=rotation_tick(run_date),
            registration_timeout=float(exec_cfg.get("barrier_registration_timeout_seconds", 300.0)),
            release_timeout=float(exec_cfg.get("barrier_release_timeout_seconds", 120.0)),
        )

    def _run_model_threadsafe(model_key: str, cfg: dict) -> dict:
        t0 = time.monotonic()
        try:
            r = run_one_model(
                model_key, cfg, market_data, prices, executor, run_date, settings, logger,
                is_eod=is_eod,
                news_data=news_data,
                sentiment_data=sentiment_data,
                news_hash=news_hash,
                ticker_order=ticker_order,
                barrier=barrier,
                fetch_started_at=fetch_started_at,
                eod_valuation_ok=not eod_bad_closes,
                ingestion=ingestion,
                marks_by_book=marks_by_book,
            )
        except Exception as e:
            logger.exception("[%s] unhandled error: %s", model_key, e)
            send_alert("CRITICAL", f"Unhandled error in {model_key}", str(e), {"model": model_key},
                       kind="pipeline_error", dedup_key=f"pipeline_error:{model_key}")
            r = {"model_key": model_key, "status": "ERROR", "error": str(e)}
        r["_duration_sec"] = round(time.monotonic() - t0, 2)
        return r

    per_model_results: list[dict[str, Any]] = []
    models_start = time.monotonic()
    logger.info("Launching %d models in parallel (ThreadPoolExecutor)", len(enabled_models))
    with ThreadPoolExecutor(max_workers=max(1, len(enabled_models)), thread_name_prefix="model") as pool:
        futures = {
            pool.submit(_run_model_threadsafe, mk, cfg): mk
            for mk, cfg in enabled_models
        }
        for fut in as_completed(futures):
            mk = futures[fut]
            try:
                r = fut.result()
            except Exception as e:
                logger.exception("[%s] future raised unexpectedly: %s", mk, e)
                r = {"model_key": mk, "status": "ERROR", "error": str(e)}
            logger.info("[%s] finished in %.2fs (status=%s)",
                        mk, r.get("_duration_sec", -1), r.get("status", "?"))
            per_model_results.append(r)
    models_elapsed = time.monotonic() - models_start
    logger.info("All %d models completed in %.2fs (parallel wall-clock)",
                len(enabled_models), models_elapsed)

    # MISSING_PRICE events, one per rejected ticker, as this cycle's
    # data-availability descriptor (written every cycle, clean ones included).
    held_by: dict[str, list[str]] = {}
    for mk, _ in enabled_models:
        try:
            for t in load_portfolio(mk).holdings:
                held_by.setdefault(t, []).append(mk)
        except Exception as e:  # noqa: BLE001 — descriptor only
            logger.warning("[%s] holdings unreadable for the descriptor: %s", mk, e)
    record_data_availability(
        cycle_id=executor.cycle_id, mode=mode, run_date=run_date,
        n_expected=len(symbols),
        events=missing_price_events(ingestion, held_by, marks_by_book))

    # Level-2 (broker-authoritative) reconciliation -- G-BOOK. Broker modes
    # only; every book's state is durably saved by this point (each thread
    # above called save_portfolio before returning). Runs after every book
    # so it sees the whole cycle's fills, not a partial set.
    if executor.broker_enabled:
        try:
            run_post_cycle_reconciliation(
                executor.broker, executor.cycle_id,
                venue_book_keys([mk for mk, _ in enabled_models], executor.settings))
        except Exception as e:
            logger.exception("Reconciliation failed to run: %s", e)
            send_alert("CRITICAL", "G-BOOK reconciliation could not run",
                      str(e), {"cycle_id": executor.cycle_id},
                      kind="gbook_reconciliation_error",
                      dedup_key=f"gbook_reconciliation_error:{executor.cycle_id}")

    # The standing intended-vs-expressed descriptor (ruling 5.4). Recomputed
    # from the decision logs every cycle, so it is current whenever the run ends.
    try:
        write_session_descriptor(run_date.strftime("%Y-%m-%d"),
                                 [mk for mk, _ in enabled_models])
    except Exception as e:
        logger.exception("Intended-vs-expressed descriptor failed: %s", e)
        send_alert("WARN", "Intended-vs-expressed descriptor failed", str(e),
                   {"cycle_id": executor.cycle_id}, kind="descriptor_failure",
                   dedup_key=f"descriptor_failure:{executor.cycle_id}")

    # Mark this intraday boundary handled now that every model's decisions are
    # made. The workflow commits this in the SAME commit as the decision logs +
    # state files, so the marker exists iff the decisions were durably committed.
    # (EOD is not guarded — see the guard block above.)
    #
    # KNOWN LIMITATION (v1 — accepted for Phase A paper, NOT for Phase B):
    # marks at COMPLETION. A hard kill mid-loop (runner eviction / OOM) after
    # some models have filled but before the commit leaves the boundary UNMARKED,
    # so a retry re-prompts and RE-TRADES the models that already filled. That is
    # a real-money double-trade risk in Phase B and is tracked as "per-model
    # idempotency" on the pre-Phase-B list — DO NOT go live with real money
    # without it. The fix is per-model marking (record each model right after it
    # trades, so a retry skips the ones already done).
    if not is_eod:
        mark_boundary_handled(*boundary_parts(run_date))

    # Rendered-prompt hash check (hub ruling 2026-10-30; infrastructure class,
    # no change slot, no version boundary). Runs only now, after every model
    # has decided and the boundary is marked, so it cannot touch decision
    # generation. Bounded in time and never raises; the import sits inside
    # the guard too, so even a broken module cannot fail the tick. Its record
    # lands in data/prompt_integrity/ and is committed with this cycle.
    if not is_eod:
        try:
            from .logging.prompt_integrity import run_prompt_integrity_check_bounded
            run_prompt_integrity_check_bounded(run_date)
        except Exception as e:
            logger.exception("Prompt hash check skipped: %s", e)

    # Build dashboard payload (intraday + EOD both refresh this — the dashboard
    # is always live)
    if not eod_bad_closes:
        logger.info("Building dashboard payload")
        try:
            build_dashboard_payload(prices=prices, run_date=run_date)
        except Exception as e:
            logger.exception("Dashboard build failed: %s", e)
            send_alert("WARN", "Dashboard build failed", str(e),
                       kind="dashboard_failure", dedup_key="dashboard_failure")

    # EOD-only: report + summary alert (skipped when the valuation aborted)
    if is_eod and not eod_bad_closes:
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
            send_alert("WARN", "Daily report generation failed", str(e),
                       kind="report_failure", dedup_key="report_failure")

        leaderboard = build_leaderboard(list(settings["models"].keys()))
        summary = {
            "date": run_date.strftime("%Y-%m-%d"),
            "mode": settings["mode"],
            "models": per_model_results,
            "leaderboard": leaderboard,
        }
        send_daily_summary(summary)

    if is_eod:
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
                               {"budget": budget},
                               kind="budget", dedup_key="budget:critical")
                else:
                    logger.warning("BUDGET WARN — %s", detail)
                    send_alert("WARN", "Monthly API budget approaching cap",
                               f"One or more providers ≥80% of monthly cap: {detail}",
                               {"budget": budget},
                               kind="budget", dedup_key="budget:warn")
            else:
                logger.info("Budget check: all providers below %.0f%% of monthly cap",
                            budget["warn_threshold_pct"] * 100)
        except Exception as e:
            logger.exception("Budget check failed: %s", e)

    total_elapsed = time.monotonic() - pipeline_start
    logger.info("==== Pipeline tick complete in %.2fs (%.2fs in parallel model phase) ====",
                total_elapsed, models_elapsed)

    # Liveness heartbeat — the "a decision tick actually landed" signal for the
    # external dead-man's-switch. Fires ONLY here: we are past the market-hours
    # guard (so the market was open) and every model has run to completion. EOD
    # runs after the close, outside the watchdog's expected window, so it is
    # intentionally excluded. Best-effort — never raises, never blocks the tick.
    if not is_eod:
        send_heartbeat(success=True, settings=settings)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous LLM Trading Lab — intraday pipeline")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--intraday", action="store_true", help="Run a normal intraday tick (default)")
    group.add_argument("--end-of-day", action="store_true", help="Run the post-close EOD wrap-up")
    parser.add_argument("--force", action="store_true",
                        help="Run off-schedule (still respects market hours)")
    parser.add_argument("--force-trade", action="store_true",
                        help="Bypass market-hours check too — trades on stale prices, testing only")
    args = parser.parse_args()

    if args.end_of_day:
        mode = "end-of-day"
    else:
        mode = "intraday"
    return run_pipeline(mode=mode, force=args.force, force_trade=args.force_trade)


if __name__ == "__main__":
    sys.exit(main())
