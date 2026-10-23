"""v3 -> v4 boundary force-cover — one-shot administrative closure of open shorts.

v4 removes the COVER verb from the action enum, so a short still open when the
prompt flips cannot be closed by its holder. The registered procedure (ledger
`regime_boundaries.phase_b_long_only.open_shorts_at_boundary`, prereg T2.3
censoring principle) is to cover every open short UNDER v3, while COVER still
exists, BEFORE `prompt_version` flips to v4.

This script is that one step and nothing else. It reuses the production path
end to end so the result is indistinguishable in shape from a stop-driven
cover:

  * `Executor.force_liquidate(..., reason="BOUNDARY_ADMIN")` — closes a short
    with COVER (via `Holding.is_short`), never SELL, and stamps
    `order_id = "FORCED_BOUNDARY_ADMIN"`. The `FORCED_` prefix is what
    `research_metrics._is_administrative` censors, so the closure never enters
    RQ2 realization, turnover or reversal. The reason code is deliberately
    distinct from RISK_STOP / POSITION_STOP so the short stop-loss statistics
    stay clean.
  * `save_portfolio` — the same state-file write a normal cycle performs.
  * `log_decision_run` / `log_intraday_snapshot` — the same trade-log and
    intraday-log records, so the fills sit in data/trades/ in the standard
    execution shape.

Deliberately NOT done: `record_intraday_run`. This is not a model run and must
not consume a runs_today slot or count against max_trades_per_day.

KNOWN SHAPE COST (flagged for the ledger entry): `log_decision_run` has no
admin-record variant, so the trade-log record carries `provider:
"administrative"`, `api_success: true`, zero tokens, no decisions. Per-book
completeness is api_success/records, so this adds one record to Gemini's
denominator and numerator. Readers that need a pure model-call population must
exclude `provider == "administrative"`.

Safety refusals (each exits non-zero before anything is written):
  * the book would reach a venue (mode is not paper for that book) — a broker
    cover is a real order and needs its own decision;
  * `prompt_version` is already v4 — COVER no longer exists in the prompt;
  * live prices requested while the market is closed — a stale-close fill is
    not a fill;
  * origin/main has commits not in HEAD — the cron commits data/state every
    ~30 minutes, and a cover applied to stale state conflicts on push.
  * any targeted position is not an open short.

Dry-run by default; pass --execute to write.

    python -m scripts.boundary_force_cover --book gemini            # plan only
    python -m scripts.boundary_force_cover --book gemini --execute
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src import config_loader
from src.adapters.base import DecisionResult
from src.config_loader import effective_mode, load_settings
from src.execution.executor import Executor
from src.logging import decision_log
from src.logging.decision_log import log_decision_run, log_intraday_snapshot
from src.portfolio import portfolio as portfolio_mod

logger = logging.getLogger("boundary_force_cover")

BOUNDARY_REASON = "BOUNDARY_ADMIN"
EASTERN = ZoneInfo("America/New_York")


class ForceCoverRefused(RuntimeError):
    """A safety precondition failed; nothing was written."""


def redirect_data_root(root: Path) -> None:
    """Point every directory this script writes at `root` (simulator copy).

    Those constants are imported by name into their modules, so patching
    config_loader alone would not redirect them.
    """
    portfolio_mod.STATE_DIR = root / "state"
    decision_log.TRADES_DIR = root / "trades"
    decision_log.INTRADAY_DIR = root / "intraday"


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise ForceCoverRefused(
            f"`git {' '.join(args)}` failed (rc={r.returncode}): {r.stderr.strip()[:300]} "
            "— cannot verify the book is current; retry, do not bypass.")
    return r.stdout.strip()


def assert_synced_with_origin() -> str:
    """Refuse unless every origin/main commit is already in HEAD (fresh fetch).

    HEAD may be AHEAD of origin (local commits are fine); what matters is that
    no cron tick is unmerged, because a cover applied to stale state conflicts
    on push. Returns HEAD.
    """
    _git("fetch", "origin", "main", "--quiet")
    head, origin = _git("rev-parse", "HEAD"), _git("rev-parse", "origin/main")
    behind = subprocess.run(["git", "merge-base", "--is-ancestor", origin, head]).returncode
    if behind != 0:
        raise ForceCoverRefused(
            f"origin/main {origin[:9]} is not in HEAD {head[:9]}: merge origin/main "
            "first, then run immediately — the cron rewrites data/state every ~30 minutes.")
    return head


def live_prices(tickers: list[str], benchmark: str) -> dict[str, float]:
    from src.data import fetch_intraday_data, is_market_open_now
    if not is_market_open_now():
        raise ForceCoverRefused(
            "market is closed: a live-price cover would fill at a stale close. "
            "Run pre-open only with --prices-json supplied from a verified quote.")
    data = fetch_intraday_data(symbols=sorted(set(tickers) | {benchmark}), interval="30m")
    out: dict[str, float] = {}
    for sym, df in data.items():
        if df is not None and not df.empty:
            out[sym] = float(df["Close"].iloc[-1])
    return out


def run(book: str, tickers: list[str] | None, prices: dict[str, float] | None,
        execute: bool, when: datetime, head: str | None) -> dict[str, Any]:
    settings = load_settings()
    if str(settings.get("prompt_version", "")).startswith("v4"):
        raise ForceCoverRefused(
            "prompt_version is already v4: COVER is gone from the prompt; the "
            "cover must run under v3, before the flip.")

    executor = Executor()
    mode = effective_mode(book, settings)
    if executor.on_venue(book) or mode != "paper":
        raise ForceCoverRefused(
            f"{book} runs on a venue path (effective mode {mode!r}): a cover is a "
            "real broker order, not a simulator book change. Needs its own decision.")

    portfolio = portfolio_mod.load_portfolio(book)
    shorts = {t: h for t, h in portfolio.holdings.items() if h.is_short}
    targets = sorted(shorts) if tickers is None else sorted(tickers)
    bad = [t for t in targets if t not in shorts]
    if bad:
        raise ForceCoverRefused(f"not open shorts in {book}: {bad}")
    if not targets:
        logger.info("[%s] no open shorts — nothing to do", book)
        return {"book": book, "status": "NOTHING_TO_DO", "fills": []}

    if prices is None:
        prices = live_prices(list(portfolio.holdings), settings["benchmark_ticker"])
    missing = [t for t in list(portfolio.holdings) + [settings["benchmark_ticker"]]
               if t not in prices]
    if missing:
        raise ForceCoverRefused(f"no price for {missing}: refusing to mark or fill on avg_cost")

    session_date = when.astimezone(EASTERN).strftime("%Y-%m-%d")
    portfolio.reset_intraday_if_new_session(session_date)
    pre = portfolio.snapshot(prices)
    pre_holdings = {t: {"shares": h.shares, "avg_cost": h.avg_cost}
                    for t, h in portfolio.holdings.items()}
    pre_cash = portfolio.cash

    plan = [{"ticker": t, "shares": abs(shorts[t].shares), "price": prices[t],
             "avg_cost": shorts[t].avg_cost} for t in targets]
    logger.info("[%s] plan (%s): %s", book, "EXECUTE" if execute else "DRY-RUN", plan)
    if not execute:
        return {"book": book, "status": "DRY_RUN", "plan": plan}

    results = executor.force_liquidate(portfolio, targets, prices, BOUNDARY_REASON,
                                       run_date=session_date)
    failed = [r for r in results if not (r.executed and r.side == "COVER")
              or r.order_id != f"FORCED_{BOUNDARY_REASON}"]
    if failed or len(results) != len(targets):
        # Nothing has been saved: the on-disk book is untouched.
        raise ForceCoverRefused(
            "cover did not complete cleanly, book NOT saved: "
            + json.dumps([{"t": r.ticker, "side": r.side, "ok": r.executed,
                           "err": r.error, "oid": r.order_id} for r in results]))

    portfolio_mod.save_portfolio(portfolio)
    post = portfolio.snapshot(prices)

    # Standard trade-log record, no model call behind it (see module docstring).
    admin = DecisionResult(
        provider="administrative", model_id_configured="n/a", model_id_returned="n/a",
        decisions=[], overall_reasoning=(
            f"Boundary administration ({BOUNDARY_REASON}): v3->v4 force-cover of "
            f"open shorts {targets}. Not a model decision; censored from behavioral "
            "realization metrics."),
        raw_response="", latency_seconds=0.0, success=True)
    log_decision_run(
        model_key=book, run_date=when, decision_result=admin,
        accepted_decisions=[], violations=[], execution_results=results,
        portfolio_snapshot_after=post, prompt_version=settings["prompt_version"],
        data_inputs_hash="", execution_mode=mode,
        inception_date=portfolio.inception_date, cycle_id=executor.cycle_id)
    log_intraday_snapshot(
        model_key=book, run_timestamp=when, snapshot=post,
        benchmark_value=prices.get(settings["benchmark_ticker"]),
        trades_executed_this_run=len(results),
        trades_executed_today=portfolio.intraday.trades_executed_today,
        runs_today=portfolio.intraday.runs_today)

    capture = {
        "book": book, "reason": BOUNDARY_REASON, "run_at_utc": when.astimezone(timezone.utc).isoformat(),
        "session_date": session_date, "head": head, "mode": mode,
        "prompt_version": settings["prompt_version"], "cycle_id": executor.cycle_id,
        "pre": {"cash": pre_cash, "holdings": pre_holdings,
                "total_value": pre["total_value"], "num_shorts": pre.get("num_shorts"),
                "short_exposure_pct": pre.get("short_exposure_pct")},
        "post": {"cash": portfolio.cash,
                 "holdings": {t: {"shares": h.shares, "avg_cost": h.avg_cost}
                              for t, h in portfolio.holdings.items()},
                 "total_value": post["total_value"], "num_shorts": post.get("num_shorts"),
                 "short_exposure_pct": post.get("short_exposure_pct")},
        "fills": [{"ticker": r.ticker, "side": r.side, "shares": r.shares,
                   "fill_price": r.fill_price, "notional": r.notional,
                   "order_id": r.order_id, "executed": r.executed} for r in results],
        # From the pre-cover plan: the Holding objects are mutated by the cover.
        "realized_pnl": [{"ticker": p["ticker"],
                          "pnl": (p["avg_cost"] - p["price"]) * p["shares"]} for p in plan],
        "status": "EXECUTED",
    }
    out_dir = portfolio_mod.STATE_DIR.parent / "boundary"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"force_cover_{session_date}_{book}.json"
    path.write_text(json.dumps(capture, indent=2), encoding="utf-8")
    logger.info("[%s] covered %s; capture written to %s", book, targets, path)
    return capture


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--book", required=True)
    ap.add_argument("--tickers", help="comma list; default = every open short in the book")
    ap.add_argument("--prices-json", help="JSON {ticker: price} for every holding + benchmark "
                                          "(skips the live fetch and the market-open check)")
    ap.add_argument("--data-root", help="redirect all writes to this directory (simulator copy)")
    ap.add_argument("--execute", action="store_true", help="write; default is a dry-run")
    ap.add_argument("--skip-sync-check", action="store_true",
                    help="skip the HEAD==origin/main check (simulator tests only)")
    args = ap.parse_args(argv)

    config_loader.load_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.data_root:
            redirect_data_root(Path(args.data_root))
        head = None if args.skip_sync_check else assert_synced_with_origin()
        prices = json.loads(Path(args.prices_json).read_text()) if args.prices_json else None
        tickers = args.tickers.split(",") if args.tickers else None
        result = run(args.book, tickers, prices, args.execute,
                     datetime.now(timezone.utc), head)
    except ForceCoverRefused as e:
        logger.error("REFUSED: %s", e)
        return 2
    sys.stdout.write(json.dumps(result, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
