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
from .adapters.base import IdentityMismatch, identity_failure_result
from .analytics.fingerprint_transitions import record_transitions as record_fingerprint_transitions
from .alerts import scan_macro_events, send_alert, send_daily_summary, send_heartbeat
from .alerts import preflight
from .alerts.preflight import assert_configured as alert_preflight
from .alerts.account_state import alert_if_account_state
from .analytics import build_leaderboard, compute_budget_status
from .analytics.intended_vs_expressed import write_session_descriptor
from .config_loader import (
    configure_logging,
    effective_mode,
    ensure_dirs,
    load_env,
    load_settings,
    load_universe,
    active_model_keys,
    handover_rules_apply,
    public_model_keys,
    tick_deadline_seconds,
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
from .data.prompt_frame import PromptFrame, build_prompt_frame
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
from .logging.tick_admission import (
    GUARD_BOUNDARY_LEDGER,
    GUARD_SPACING,
    SCOPE_EOD,
    SCOPE_INTRADAY,
    TICK_WITHHELD_GUARD_UNREADABLE,
    build_withhold_event,
    record_withhold,
)
from .logging.venue_check import run_shadow_check
from .logging.call_inputs import CallInput, alert_if_flagged, retain_call_inputs
from .logging.decision_log import log_administrative_row, serialize_execution
from .record_class import ADMIN_CLASS_EOD_POSITION_STOP, EOD_STOP_NOT_EXECUTABLE

# The EOD boundary key (tick_admission_guards_ruling.md rev. 5 section 2): one
# EOD per session, in the same ledger as the intraday :00/:30 slots.
EOD_SLOT = "EOD"
# settings["mode"] values that reach a venue (src/execution/broker.BROKER_MODES).
_VENUE_MODES = ("broker_paper", "live")
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
    # Active books only: load_portfolio incepts a missing book, and a book
    # before its first session (models.<key>.active_from) must not exist yet.
    # Peers are the book's own cohort: from 2026-12-01 the five refreshed arms
    # count among themselves and the six running books among themselves, so
    # the logged agreement count keeps the population it had (no cross-cohort
    # pairs; cohort_refresh_ruling.md rev. 2, hub H-OPS-07).
    models = settings.get("models") or {}
    refreshed = (models.get(model_key) or {}).get("cohort") == "refreshed"
    peers = [k for k in active_model_keys(settings)
             if ((models.get(k) or {}).get("cohort") == "refreshed") is refreshed]
    for key in peers:
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


def _confirm_terminal(executor: Executor, model_key: str,
                      results: list[Any]) -> dict[str, dict[str, Any]]:
    """Re-read every venue order the EOD sweep placed (read-only GETs). Returns
    client_order_id -> {status, terminal, broker_order or error}. An order
    still open is §4 case C: it would fill at the open against a book that has
    not moved, so "no order outlives the run that placed it" is checked here,
    before the commit. An order that cannot be re-read counts as open."""
    out: dict[str, dict[str, Any]] = {}
    if not executor.on_venue(model_key) or executor.broker is None:
        return out
    for r in results:
        oid = str(getattr(r, "order_id", "") or "")
        if not oid or oid.startswith("FORCED_"):
            continue
        try:
            o = executor.broker.get_order_by_client_id(oid)
            out[oid] = {"status": o.status, "terminal": bool(o.is_terminal),
                        "broker_order": o.to_dict()}
        except Exception as e:  # noqa: BLE001 — unconfirmable counts as open
            prior = getattr(r, "broker_order", None) or {}
            if not prior:
                # Never reached the venue (rejected at submission): nothing to
                # confirm, and nothing can be open.
                continue
            out[oid] = {"status": prior.get("status"), "terminal": False,
                        "error": f"{type(e).__name__}: {e}"}
    return out


def _stop_evaluation(pre_sweep: dict[str, tuple[float, float]], prices: dict[str, float],
                     results: list[Any]) -> list[dict[str, Any]]:
    """Per triggered holding: shares, average cost, the closing mark, the loss."""
    rows = []
    for t in sorted({getattr(r, "ticker", "") for r in results}):
        if t not in pre_sweep:
            continue
        shares, avg = pre_sweep[t]
        mark = prices.get(t)
        loss_pct = loss_usd = None
        if mark and avg:
            # Loss against entry, positive = losing: a long below cost, a short above.
            loss_pct = (mark / avg - 1.0) if shares < 0 else (1.0 - mark / avg)
            loss_usd = abs(shares) * avg * loss_pct
        rows.append({"ticker": t, "shares": shares, "avg_cost": avg, "closing_mark": mark,
                     "loss_pct": loss_pct, "loss_usd": loss_usd})
    return rows


def _write_eod_admin_row(model_key: str, portfolio: Any, results: list[Any],
                         pre_sweep: dict[str, tuple[float, float]], prices: dict[str, float],
                         executor: Executor, settings: dict[str, Any], run_date: datetime,
                         snapshot_after: dict[str, Any], stop_dust_exempt: list[dict[str, Any]],
                         logger: logging.Logger) -> dict[str, Any] | None:
    """Write the EOD_POSITION_STOP administrative row (ruling §1, §4).

    Record class only: the orders already happened; this reads them back and
    writes the row. Never raises -- a failure is CRITICAL (any venue fill is
    then an order without a record) but must not stop the book being saved.
    """
    try:
        confirmed = _confirm_terminal(executor, model_key, results)
        # The venue's own evidence that the market was closed (r4_notes rev. 2
        # §4.3): its clock, read once (GET only). None if unreadable.
        clock = None
        if executor.on_venue(model_key) and executor.broker is not None:
            try:
                c = executor.broker._request("GET", "/v2/clock")
                clock = {"is_open": c.get("is_open"), "timestamp": c.get("timestamp"),
                         "next_open": c.get("next_open")}
            except Exception as ce:  # noqa: BLE001 — absence of evidence, recorded
                clock = {"error": f"{type(ce).__name__}: {ce}"}
        executions, open_orders, not_executable, unconfirmed = [], [], [], []
        for r in results:
            e = serialize_execution(r)
            c = confirmed.get(str(e.get("order_id") or ""))
            if c and c.get("broker_order"):
                e["broker_order"] = c["broker_order"]
            if c and not c["terminal"]:
                # §4 case C: open past the run. Recorded on the row and paged;
                # it stays stop_unprotected (the position is open and the
                # order can fill at the open).
                e["eod_open_order"] = True
                open_orders.append({"client_order_id": e.get("order_id"),
                                    "status": c.get("status"), "error": c.get("error")})
            elif not e.get("executed") and executor.on_venue(model_key):
                closed = ((clock or {}).get("is_open") is False
                          or "market is closed" in str(e.get("error") or "").lower()
                          or "market closed" in str(e.get("error") or "").lower())
                if closed:
                    # §4 cases A/B: the venue rejected it, or accepted it for
                    # the next open and our deadline cancelled it, with the
                    # venue's evidence that the market was closed. A detection
                    # by design: stop_unprotected false, a WARN, never STOP
                    # UNPROTECTED (r4_notes rev. 2 §4.4). The executor's own
                    # stamp is overridden on the RECORD only, after the order
                    # resolved; nothing that places, cancels or halts reads it.
                    e["venue_constraint"] = e.get("constraint") or ""
                    e["constraint"] = EOD_STOP_NOT_EXECUTABLE
                    e["stop_unprotected"] = False
                    not_executable.append(e.get("ticker"))
                else:
                    # No market-closed evidence: the tag is not valid, so the
                    # executor's stamp stands and the event is paged.
                    unconfirmed.append(e.get("ticker"))
            executions.append(e)
        extra: dict[str, Any] = {"venue_clock": clock}
        if open_orders:
            extra["eod_open_orders"] = open_orders
        row = log_administrative_row(
            model_key, run_date, admin_class=ADMIN_CLASS_EOD_POSITION_STOP,
            cycle_id=executor.cycle_id, executions=executions,
            portfolio_snapshot_after=snapshot_after,
            prompt_version=settings.get("prompt_version", ""),
            execution_mode=effective_mode(model_key, settings),
            inception_date=settings["experiment_start_date"],
            stop_evaluation=_stop_evaluation(pre_sweep, prices, results),
            stop_dust_exempt=stop_dust_exempt, extra=extra)
        logger.info("[%s] EOD administrative row: %d forced result(s), %d not executable, "
                    "%d open", model_key, len(executions),
                    sum(1 for e in executions if e.get("constraint") == EOD_STOP_NOT_EXECUTABLE),
                    len(open_orders))
        if not_executable:
            send_alert("WARN", f"EOD stop not executable (market closed): {model_key}",
                       f"The end-of-day stop sweep triggered on {', '.join(not_executable)}; "
                       f"the closed market did not execute it (refused, or cancelled at our "
                       f"fill deadline). By design: the position is evaluated again at the "
                       f"next session's first admitted tick. Not STOP UNPROTECTED.",
                       {"model": model_key, "cycle_id": executor.cycle_id},
                       kind="eod_stop_not_executable",
                       dedup_key=f"eod_stop_not_executable:{model_key}:{executor.cycle_id}")
        if unconfirmed:
            send_alert("CRITICAL", f"EOD stop not executed, market state unconfirmed: {model_key}",
                       f"The end-of-day stop on {', '.join(unconfirmed)} did not execute and "
                       f"the venue gave no evidence the market was closed. Recorded as "
                       f"stop_unprotected.", {"model": model_key, "cycle_id": executor.cycle_id},
                       kind="stop_unprotected",
                       dedup_key=f"eod_stop_unconfirmed:{model_key}:{executor.cycle_id}")
        if open_orders:
            send_alert("CRITICAL", f"EOD stop order left open at the venue: {model_key}",
                       f"The end-of-day stop sweep placed order(s) that are not terminal "
                       f"after the run: {open_orders}. An open order fills at the next open "
                       f"against a book that has not moved. Recorded on the administrative row.",
                       {"model": model_key, "cycle_id": executor.cycle_id},
                       kind="eod_open_order",
                       dedup_key=f"eod_open_order:{model_key}:{executor.cycle_id}")
        return row
    except Exception as e:  # noqa: BLE001
        logger.exception("[%s] EOD administrative row NOT written: %s", model_key, e)
        send_alert("CRITICAL", f"EOD administrative row not written: {model_key}",
                   f"The end-of-day stop sweep produced forced results but their row could "
                   f"not be written ({e}). Any venue fill is now an order without a record.",
                   {"model": model_key}, kind="eod_admin_row_error",
                   dedup_key=f"eod_admin_row_error:{model_key}:{run_date.strftime('%Y-%m-%d')}")
        return None


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
    eod_stops_suspended: str | None = None,
    prompt_frame: PromptFrame | None = None,
    tick_deadline_at: float | None = None,
    tick_deadline_s: float | None = None,
) -> dict[str, Any]:
    logger.info("=== Running model: %s (%s/%s) ===", model_key, cfg["provider"], cfg["model"])
    # The prompt builders see the prompt frame (daily history + today's partial
    # bar, src/data/prompt_frame.py); everything that prices, sizes, validates
    # or executes keeps `market_data` / `prices` from the guard. No prompt
    # frame (an EOD pass, which prompts nothing) means the guard's frame.
    prompt_data = prompt_frame.frames if prompt_frame is not None else market_data
    frame_fields = prompt_frame.row_fields() if prompt_frame is not None else {}

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
    # EOD admission (rev. 5 section 2): a withheld EOD pass, or a duplicate of
    # an EOD pass already handled this session, suspends the stop sweep only.
    # `eod_stops_suspended` names why; the snapshot below still runs.
    sweep_suspended = is_eod and bool(eod_stops_suspended)
    # The EOD administrative row records each triggered holding as it stood
    # before the sweep moved it (eod_stop_record_and_evaluation_ruling.md §1).
    pre_sweep = ({t: (h.shares, h.avg_cost) for t, h in portfolio.holdings.items()}
                 if is_eod else {})
    if sweep_suspended:
        logger.info("[%s] EOD stop sweep suspended (%s): no stop evaluated, no order",
                    model_key, eod_stops_suspended)
    forced_results, stop_dust_exempt = (
        ([], []) if (valuation_aborted or sweep_suspended) else _position_stop_sweep(
            model_key, portfolio, prices, executor, session_date_str, logger))

    snapshot_before = portfolio.snapshot(prices)
    adapter = get_adapter(cfg["provider"], cfg["model"])
    # The decision deadline D (time.monotonic() instant). The adapter applies
    # it, with the finish-reason rule, to the refreshed arms from their first
    # session and to the snapshots and DeepSeek from the handover date
    # (settings handover.date; hub H-OPS-14).
    adapter.deadline_at = tick_deadline_at
    adapter.handover_rules = handover_rules_apply(settings, run_date)
    # data_inputs_hash: the sha256 of the frame the models see (daily history
    # ruling 3); the guard's intraday frame is hashed as execution_frame_hash.
    data_hash = (prompt_frame.data_inputs_hash if prompt_frame is not None
                 else hash_inputs(market_data))
    from .config_loader import universe_symbols as _universe_symbols

    # EOD pass: no MODEL trading — only snapshot, log, and report. The EOD cron
    # fires after market close to capture closing prices and generate the
    # daily report. It places no model trades, but it CAN submit per-position
    # stop orders: the stop sweep above runs before this branch (unless EOD
    # admission suspended it), and in broker modes a triggered stop goes to the
    # venue. Any forced result -- executed, rejected or left unexecuted --
    # gets one ADMINISTRATIVE row in this book's decision log, written before
    # the book is saved so both land in the same commit, after every order is
    # confirmed terminal (eod_stop_record_and_evaluation_ruling.md §1, §4).
    if is_eod:
        snapshot_after = snapshot_before
        if forced_results:
            _write_eod_admin_row(model_key, portfolio, forced_results, pre_sweep, prices,
                                 executor, settings, run_date, snapshot_after,
                                 stop_dust_exempt, logger)
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
        prompt_data, snapshot_before, run_date,
        news_data=news_data, sentiment_data=sentiment_data,
        ticker_order=ticker_order,
    )
    screening_raw = ""
    shortlisted: list[str] = []
    screening_metadata: dict[str, Any] = {}
    _scr_latency: float | None = None
    screen_identity: IdentityMismatch | None = None
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
        if isinstance(e, IdentityMismatch):
            # xai_probe_followup_ruling section 2: the screening response named
            # another model, so this tick's decision for the arm has failed.
            # No decision call is made; the row records the screening leg.
            screen_identity = e
            screening_metadata = {**e.metadata}
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
        prompt_data,
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
    if screen_identity is not None:
        decision_result = identity_failure_result(adapter, screen_identity, "screening")
    else:
        decision_result = adapter.generate_decision(system_prompt, user_prompt, images=images_for_call)
    md = decision_result.metadata or {}
    if md.get("identity_leg"):                       # set only on an IDENTITY_MISMATCH
        send_alert("WARN", f"Candidate identity event: {model_key}",
                   "A response named a model that is not on this arm's accepted list. The "
                   "decision failed and was not executed (IDENTITY_MISMATCH, "
                   "xai_probe_followup_ruling_2026-11-22.md section 2). Hub: rule on the name.",
                   {"model": model_key, "configured": cfg["model"],
                    "named": str(md.get("model_named")), "leg": md.get("identity_leg"),
                    "cycle_id": executor.cycle_id},
                   kind="identity_mismatch",
                   dedup_key=f"identity_mismatch:{model_key}:{session_date_str}")
    if getattr(adapter, "tick_rules", False) and tick_deadline_at is not None:
        decision_result.metadata = {**(decision_result.metadata or {}),
                                    "tick_deadline_seconds": tick_deadline_s}

    # Call-input retention and response flags (logging class; 2026-11-21).
    # Written from the very objects just handed to the adapter, so the file
    # cannot differ from what was sent. Neither step can raise into the tick:
    # a failed write is recorded on the row as input_retention_failed.
    call_inputs = retain_call_inputs(
        executor.cycle_id, model_key, run_date, prompt_version,
        [CallInput("screen", screening_sys, screening_user),
         CallInput("trade", system_prompt, user_prompt, list(images_for_call or []))])
    alert_if_flagged(model_key, executor.cycle_id, decision_result, send_alert)

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
        # ACCOUNT_STATE alarm (rossi_review_rulings.md section 2.4 rule 4). Called
        # unconditionally; it decides from the failure class itself and only
        # alerts. Nothing here branches on the class.
        alert_if_account_state(model_key, decision_result, executor.cycle_id)
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
            call_inputs=call_inputs,
            **frame_fields,
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
        call_inputs=call_inputs,
        **frame_fields,
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
    enabled = active_model_keys(settings, run_date)
    if enabled and all(eod_row_exists(mk, day) for mk in enabled):
        return f"every enabled book already has its {day} row"
    return None


def _withhold_tick(settings: dict[str, Any], run_date: datetime, *, guard: str,
                   files: list[str], reason: str, detail: str = "",
                   boundary: tuple[str, str] | None = None) -> None:
    """Gate A withhold: write the TICK_WITHHELD_GUARD_UNREADABLE event and ping
    the watchdog /fail. Called BEFORE the data fetch and the model loop, so the
    caller's `return 0` exits with no model prompted and no order possible —
    the pre-prompt stop sweep included (ruling section 2: the admission point is
    the book's first possible order, and that sweep runs inside run_one_model).

    Exit 0 is deliberate: the workflow commits data/ only after a successful
    pipeline step, and the event must reach origin to be durable. The CRITICAL
    and the /fail ping are the page. Never raises.
    """
    try:
        books = active_model_keys(settings, run_date)
        row = build_withhold_event(
            TICK_WITHHELD_GUARD_UNREADABLE, guard=guard,
            now_utc=run_date.astimezone(timezone.utc), books=books, files=files,
            reason=reason, detail=detail, boundary=boundary)
        record_withhold(row)
    except Exception as e:  # noqa: BLE001 — the tick is withheld regardless
        logging.getLogger("llmlab").error(
            "Withhold event could not be built or written (tick still withheld): %s", e)
    send_heartbeat(success=False, settings=settings)


def _eod_admission(settings: dict[str, Any], run_date: datetime, force_trade: bool,
                   logger: logging.Logger) -> tuple[str | None, bool]:
    """EOD admission (tick_admission_guards_ruling.md rev. 5 section 2).

    Returns (why the EOD stop sweep is suspended or None, whether the boundary
    ledger was readable). See the call site in run_pipeline for the rules.
    """
    eod_stops_suspended: str | None = None
    eod_ledger_readable = True
    e_day = run_date.strftime("%Y-%m-%d")
    _eledger, _eledger_err = load_ledger()
    if _eledger_err is not None:
        eod_ledger_readable = False
        eod_stops_suspended = "guard_unreadable:boundary_ledger"
        logger.error("Idempotency ledger unreadable (%s) — WITHHOLDING the EOD stop "
                     "sweep for %s (snapshot and report still run)", _eledger_err, e_day)
        send_alert("CRITICAL", "Duplicate guard unreadable at EOD: data/state/handled_boundaries.json",
                   f"data/state/handled_boundaries.json could not be read ({_eledger_err}). "
                   f"The {e_day} end-of-day stop sweep is WITHHELD: no stop was evaluated "
                   f"and no order was submitted. The closing snapshot and report ran.",
                   {"file": "data/state/handled_boundaries.json",
                    "event": TICK_WITHHELD_GUARD_UNREADABLE, "scope": SCOPE_EOD},
                   kind="idempotency_ledger", dedup_key=f"idempotency_ledger_unreadable_eod:{e_day}")
        try:
            record_withhold(build_withhold_event(
                TICK_WITHHELD_GUARD_UNREADABLE, guard=GUARD_BOUNDARY_LEDGER,
                now_utc=run_date.astimezone(timezone.utc),
                books=active_model_keys(settings, run_date),
                files=["data/state/handled_boundaries.json"], reason="ledger_unreadable",
                detail=str(_eledger_err), boundary=(e_day, EOD_SLOT), scope=SCOPE_EOD))
        except Exception as e:  # noqa: BLE001 — the sweep is suspended regardless
            logger.error("EOD withhold event could not be written: %s", e)
    elif is_boundary_handled(_eledger, e_day, EOD_SLOT):
        # A duplicate EOD pass after a committed one submits nothing
        # (demonstration (x), case 3). The rest of the pass is housekeeping
        # with its own date de-duplication.
        eod_stops_suspended = "eod_boundary_already_handled"
        logger.info("EOD boundary %s already handled — stop sweep suspended "
                    "(duplicate EOD pass submits nothing)", e_day)
    if not force_trade and eod_stops_suspended is None:
        e_verdict = check_spacing(settings, run_date.astimezone(timezone.utc))
        if e_verdict.withhold:
            eod_stops_suspended = f"guard_unreadable:tick_spacing:{e_verdict.reason}"
            logger.error("Spacing guard cannot answer at EOD (%s) — WITHHOLDING the EOD "
                         "stop sweep", e_verdict.reason)
            send_alert("CRITICAL", "Duplicate guard unreadable at EOD: decision logs",
                       f"The spacing guard could not read the decision logs (verdict "
                       f"{e_verdict.reason}; files {list(e_verdict.unreadable)}). The {e_day} "
                       f"end-of-day stop sweep is WITHHELD: no stop was evaluated and no "
                       f"order was submitted. The closing snapshot and report ran.",
                       {"reason": e_verdict.reason, "scope": SCOPE_EOD},
                       kind="guard_unreadable", dedup_key=f"guard_unreadable_eod:{e_day}")
            try:
                record_withhold(build_withhold_event(
                    TICK_WITHHELD_GUARD_UNREADABLE, guard=GUARD_SPACING,
                    now_utc=run_date.astimezone(timezone.utc),
                    books=active_model_keys(settings, run_date),
                    files=list(e_verdict.unreadable), reason=e_verdict.reason,
                    boundary=(e_day, EOD_SLOT), scope=SCOPE_EOD))
            except Exception as e:  # noqa: BLE001
                logger.error("EOD withhold event could not be written: %s", e)
    return eod_stops_suspended, eod_ledger_readable


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
    # The decision deadline D runs from here, the pipeline's own start after
    # the runner's setup (calibration_contingencies_ruling section 3 item 1).
    tick_deadline_s = None if is_eod else tick_deadline_seconds(settings)
    tick_deadline_at = (pipeline_start + tick_deadline_s) if tick_deadline_s else None
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
    # EOD is not guarded HERE but in the EOD admission block below. It places
    # no MODEL trades, but it CAN submit per-position stop orders: run_one_model
    # runs the stop sweep (_position_stop_sweep -> executor.force_liquidate ->
    # the venue in broker modes) BEFORE its is_eod branch. EOD admission (rev. 5
    # section 2): this ledger under the EOD key (day, "EOD"), the spacing
    # guard's fail-closed blocking conditions, and (V) in shadow all run before
    # that sweep; a withhold, or an EOD key already handled this session,
    # suspends the stop sweep only. _eod_skip_reason above still skips a whole
    # pass whose day's rows already exist.
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
            # FAIL CLOSED (Gate A, tick_admission_guards_ruling.md section 3).
            # A guard that trades when it cannot guard is not a guard. The old
            # fail-open rationale was to avoid a SILENT halt; this withhold is
            # not silent — CRITICAL email (deduped once/day), an external-
            # watchdog /fail ping on every withheld tick, and a tagged
            # TICK_WITHHELD_GUARD_UNREADABLE event. Present-and-unreadable only:
            # an ABSENT ledger loads as {} with no error and proceeds.
            logger.error("Idempotency ledger unreadable (%s) — WITHHOLDING tick "
                         "%s %s (no models prompted, no orders)", _ledger_err, b_day, b_slot)
            send_alert("CRITICAL", "Duplicate guard unreadable: data/state/handled_boundaries.json",
                       f"data/state/handled_boundaries.json could not be read "
                       f"({_ledger_err}). Tick {b_day} {b_slot} is WITHHELD: no model was "
                       f"prompted and no order was submitted. Every tick withholds until "
                       f"the file is repaired.",
                       {"file": "data/state/handled_boundaries.json",
                        "event": TICK_WITHHELD_GUARD_UNREADABLE},
                       kind="idempotency_ledger", dedup_key="idempotency_ledger_unreadable")
            _withhold_tick(settings, run_date, guard=GUARD_BOUNDARY_LEDGER,
                           files=["data/state/handled_boundaries.json"],
                           reason="ledger_unreadable", detail=str(_ledger_err),
                           boundary=(b_day, b_slot))
            return 0
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
    # FAILS CLOSED (Gate A), on the registered blocking conditions only: any
    # enabled book's present log raises on read, or logs are present and none
    # yields a usable final row. One torn row while other books answer is still
    # an answer (the run proceeds, and the torn file is still paged). An absent
    # log — the first tick of a month — is normal and proceeds.
    if not is_eod and not force_trade:
        verdict = check_spacing(settings, run_date.astimezone(timezone.utc))
        if verdict.unreadable:
            # One CRITICAL per file per day (the ledger's cadence); every tick
            # still logs at ERROR.
            consequence = (
                "This tick is WITHHELD: no model was prompted and no order was submitted."
                if verdict.withhold else
                "The other books' logs still answer, so this tick proceeds; this "
                "file gives no spacing protection until it is repaired.")
            for bad in verdict.unreadable:
                send_alert("CRITICAL", f"Duplicate guard unreadable: {bad}",
                           f"The 20-minute spacing guard could not use {bad} "
                           f"(verdict {verdict.reason}). {consequence}",
                           {"file": bad, "reason": verdict.reason,
                            "withhold": verdict.withhold},
                           kind="guard_unreadable",
                           dedup_key=f"guard_unreadable:{bad}:{run_date.strftime('%Y-%m-%d')}")
        if verdict.withhold:
            logger.error("Spacing guard cannot answer (%s) — WITHHOLDING the tick "
                         "(no models prompted, no orders)", verdict.reason)
            if not verdict.unreadable:
                # Every present log is empty: nothing to name per file, but the
                # withhold must still page.
                send_alert("CRITICAL", "Duplicate guard unreadable: decision logs",
                           f"No present decision log yields a final row (verdict "
                           f"{verdict.reason}). This tick is WITHHELD: no model was "
                           f"prompted and no order was submitted.",
                           {"reason": verdict.reason, "withhold": True},
                           kind="guard_unreadable",
                           dedup_key=f"guard_unreadable:no_rows:{run_date.strftime('%Y-%m-%d')}")
            _withhold_tick(settings, run_date, guard=GUARD_SPACING,
                           files=list(verdict.unreadable), reason=verdict.reason,
                           boundary=boundary_parts(run_date))
            return 0
        if verdict.skip:
            logger.info(
                "Spacing guard: last cohort decision %s was %.1f min ago "
                "(minimum %d) — skipping tick (no models prompted, no trades)",
                verdict.last_decision.isoformat(), verdict.gap_minutes,
                verdict.min_minutes,
            )
            record_skip(verdict, run_date.astimezone(timezone.utc))
            return 0

    # --- EOD admission (tick_admission_guards_ruling.md rev. 5 section 2) ----
    # The EOD pass can submit orders (the stop sweep in run_one_model), so the
    # admission point exists here too. Both guards run before that sweep under
    # an EOD boundary key, one EOD per session. At EOD a withhold suspends the
    # stop sweep ONLY: the closing snapshot, the report and the digest submit
    # nothing and still run. An EOD withhold is recorded under the same class
    # with scope EOD; it is not a G-COMPLETE miss and it counts toward
    # unprotected time.
    #
    # The spacing guard contributes its fail-closed blocking conditions only.
    # Its 20-minute skip rule answers "did the cohort just decide?", which the
    # EOD pass never does; one EOD per session is the EOD boundary key's job.
    eod_stops_suspended: str | None = None
    eod_ledger_readable = True
    if is_eod:
        eod_stops_suspended, eod_ledger_readable = _eod_admission(
            settings, run_date, force_trade, logger)

    # --- (V) pre-trade venue check, SHADOW (rev. 5 section 2; condition 3) --
    # Broker modes only, ahead of the stop sweep and of the fetch-start stamp,
    # so the registered latency stages are unaffected. Logging-only: it records
    # one verdict per venue book and pages, and changes nothing about this tick.
    # It builds its own read-only venue client; the Executor and the order
    # path are untouched.
    if settings.get("mode") in _VENUE_MODES:
        try:
            _vbooks = venue_book_keys(active_model_keys(settings, run_date), settings)
            run_shadow_check(
                settings, _vbooks, datetime.now(timezone.utc),
                scope=SCOPE_EOD if is_eod else SCOPE_INTRADAY,
                boundary=((run_date.strftime("%Y-%m-%d"), EOD_SLOT) if is_eod
                          else boundary_parts(run_date)))
        except Exception as e:  # noqa: BLE001 — the shadow must never cost a tick
            logger.exception("(V) shadow check could not start: %s", e)

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

    # The prompt frame (daily_history_restoration, hub GO 2026-11-21): real
    # daily history for the text block and the chart, built AFTER the guard
    # from its accepted frames and prices, which it never modifies. Only the
    # two prompt builders read it. Any failure falls the whole tick back to the
    # intraday frame, flagged on every row, with one WARN. Never raises. An EOD
    # pass prompts nothing and builds none.
    prompt_frame = (None if is_eod else
                    build_prompt_frame(market_data, prices, run_date, send_alert=send_alert))

    # EOD valuation guard. A missing or non-finite close for any held name or
    # the benchmark aborts the valuation outputs (performance and intraday rows,
    # dashboard, daily report, leaderboard, digest and its threshold sweep) with
    # one CRITICAL. Housekeeping still runs, and the run exits 0 so it commits.
    eod_bad_closes: list[str] = []
    if is_eod:
        eod_bad_closes = _eod_unvaluable_closes(
            prices, settings,
            active_model_keys(settings, run_date))
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
    # Active books (config_loader.active_model_keys): enabled, and on or after
    # the book's first session. The refreshed arms join on their active_from.
    _active = set(active_model_keys(settings, run_date))
    enabled_models = [
        (mk, cfg) for mk, cfg in settings["models"].items() if mk in _active
    ]
    for mk, cfg in settings["models"].items():
        if mk not in _active:
            logger.info("[%s] not active on %s — skipping", mk, run_date.strftime("%Y-%m-%d"))

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
                eod_stops_suspended=eod_stops_suspended,
                prompt_frame=prompt_frame,
                tick_deadline_at=tick_deadline_at,
                tick_deadline_s=tick_deadline_s,
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

    # Fingerprint transitions, every arm (fingerprint_rotation_ruling 2026-11-22;
    # hub H-OPS-19). Derived from the rows just written; a stable field's
    # transition in this cycle WARNs as a candidate identity event.
    try:
        record_fingerprint_transitions(executor.cycle_id, [mk for mk, _ in enabled_models],
                                       run_date, alert=send_alert)
    except Exception as e:
        logger.exception("Fingerprint transition record failed: %s", e)
        send_alert("WARN", "Fingerprint transition record failed", str(e),
                   {"cycle_id": executor.cycle_id}, kind="fingerprint_transition_error",
                   dedup_key=f"fingerprint_transition_error:{run_date.strftime('%Y-%m-%d')}")

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
    # KNOWN LIMITATION — marks at COMPLETION (tick_admission_guards_ruling.md
    # sections 1-3). In Phase B the venue is durable state outside the commit,
    # so the hazard is any venue order the committed record does not account
    # for. Four routes reach it: a kill mid-loop; a raise after the first order;
    # a completed run whose push fails five times; one book raising inside a run
    # that then commits. In the first three the NEXT slot also trades on stale
    # books, which no slot-keyed marker can see. Per-model marking written here
    # does not fix this: it lives in the same working tree and is lost in the
    # same cases (ruling section 2, P2).
    #
    # What has landed against it: the workflow fail-stop (.github/workflows/
    # intraday.yml job `fail-stop`, scripts/fail_stop.py) disables the intraday
    # workflow when a failed or cancelled broker-mode run submitted any order,
    # or when that cannot be determined. It keys on run failure, so it does not
    # see the fourth route (the run succeeds); there, post-cycle reconciliation
    # covers only the pre-save half, by halting every venue book. The post-save
    # half is visible only to the pre-trade venue check (V), which runs in
    # SHADOW (src/logging/venue_check.py: verdicts recorded, nothing withheld).
    # Gate B is met by (V) enforcing; on the fail-stop alone, go-live also
    # requires the nightly offline order-id set difference (ruling section 3).
    if not is_eod:
        mark_boundary_handled(*boundary_parts(run_date))
    elif eod_ledger_readable:
        # One EOD per session: a later EOD pass (backup cron, late firing)
        # finds this key and suspends its stop sweep. Not marked over an
        # unreadable ledger: mark_boundary_handled self-heals a corrupt file by
        # overwriting it, which would silently re-arm the intraday guard that
        # Gate A keeps withholding until a person repairs the file.
        mark_boundary_handled(run_date.strftime("%Y-%m-%d"), EOD_SLOT)

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

        # The day's leaderboard snapshot is public: public books only (H-OPS-07).
        leaderboard = build_leaderboard(public_model_keys(settings, run_date))
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
