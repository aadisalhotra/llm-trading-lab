"""Daily research report generator.

After all models trade and logs are written, this builds a polished one-page
markdown briefing at /reports/daily/YYYY-MM-DD.md. Every report follows the
exact same template — same section order, same headings, same tables — so
Day 1 and Day 180 are visually identical with different data.

Read order (everything is log-driven, single source of truth):
  - /data/performance/{model}.jsonl   → daily P&L, cumulative return, history
  - /data/trades/{model}_{YYYY-MM}.jsonl → today's decisions + reasoning
  - /data/leaderboard/{date}.json     → previous-day ranks for arrow tracking
  - /data/state/{model}.json          → halted flag
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..analytics import (
    build_leaderboard,
    compute_api_cost_summary,
    compute_metrics,
    load_performance_history,
)
from ..config_loader import (
    INTRADAY_DIR,
    LEADERBOARD_DIR,
    PERFORMANCE_DIR,
    REPORTS_DIR,
    TRADES_DIR,
    load_settings,
    load_universe,
)
from ..portfolio import load_portfolio

logger = logging.getLogger("llmlab.reports")

DAILY_REPORTS_DIR = REPORTS_DIR / "daily"
LATENCY_WARN_THRESHOLD = 60.0  # seconds — flag in health section if exceeded
ERROR_MSG_TRUNCATE = 180        # max chars of error text rendered in the report


# ===========================================================================
# TABLE FORMATTER — produces source-aligned markdown tables with consistent
# column widths so the raw .md is readable AND GitHub renders cleanly.
# ===========================================================================

def _format_table(headers: list[str], rows: list[list[str]], aligns: list[str]) -> str:
    """Render a markdown table with padded cells.

    aligns: list of "L" | "R" | "C" matching column count.
    """
    cols = len(headers)
    if any(len(r) != cols for r in rows):
        raise ValueError("Row width mismatch")

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if len(cell) > widths[i]:
                widths[i] = len(cell)

    def fmt_cell(cell: str, w: int, align: str) -> str:
        if align == "R":
            return cell.rjust(w)
        if align == "C":
            return cell.center(w)
        return cell.ljust(w)

    def fmt_row(cells: list[str]) -> str:
        return "| " + " | ".join(fmt_cell(c, widths[i], aligns[i]) for i, c in enumerate(cells)) + " |"

    sep_parts = []
    for w, a in zip(widths, aligns):
        inner = w + 2  # "| cell |" has 1 space pad each side
        if a == "R":
            sep_parts.append("-" * (inner - 1) + ":")
        elif a == "C":
            sep_parts.append(":" + "-" * (inner - 2) + ":")
        else:
            sep_parts.append(":" + "-" * (inner - 1))
    sep_line = "|" + "|".join(sep_parts) + "|"

    lines = [fmt_row(headers), sep_line]
    for row in rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)


# ===========================================================================
# FORMATTERS — consistent number/percent/money formatting throughout report
# ===========================================================================

def _money(x: float | None) -> str:
    if x is None:
        return "—"
    return f"${x:,.2f}"


def _pct(x: float | None, sign: bool = True) -> str:
    if x is None:
        return "—"
    s = f"{x*100:+.2f}%" if sign else f"{x*100:.2f}%"
    return s


def _num(x: float | None, digits: int = 2) -> str:
    if x is None:
        return "—"
    return f"{x:,.{digits}f}"


def _signed_money(x: float | None) -> str:
    if x is None:
        return "—"
    sign = "+" if x >= 0 else "-"
    return f"{sign}${abs(x):,.2f}"


def _short_err(msg: str) -> str:
    """Compact a verbose API error to a single readable line for the report.

    Strips JSON wrapping, request IDs, and trims to ERROR_MSG_TRUNCATE chars.
    The full error is still preserved in the trade log — this is display only.
    """
    if not msg:
        return "unknown error"
    s = " ".join(msg.split())  # collapse whitespace
    # Try to pull a 'message' field from JSON-ish payloads
    import re
    m = re.search(r"'message':\s*'([^']+)'", s) or re.search(r'"message":\s*"([^"]+)"', s)
    if m:
        s = m.group(1)
    if len(s) > ERROR_MSG_TRUNCATE:
        s = s[:ERROR_MSG_TRUNCATE - 1].rstrip() + "…"
    return s


# ===========================================================================
# DATA LOADERS — read everything from disk
# ===========================================================================

def _read_today_trade_record(model_key: str, run_date: datetime) -> dict[str, Any] | None:
    """Pull the most recent decision log entry for this model on run_date."""
    month_str = run_date.strftime("%Y-%m")
    path = TRADES_DIR / f"{model_key}_{month_str}.jsonl"
    if not path.exists():
        return None
    target_date = run_date.strftime("%Y-%m-%d")
    last_match = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("date") == target_date:
                last_match = rec
    return last_match


def _read_today_trade_records(model_key: str, run_date: datetime) -> list[dict[str, Any]]:
    """ALL decision-log entries for this model on run_date.

    Intraday-aware version of `_read_today_trade_record` — the daily report
    needs every tick to count total trades and aggregate reasoning, not just
    the last one. Sorted by timestamp ascending.
    """
    month_str = run_date.strftime("%Y-%m")
    path = TRADES_DIR / f"{model_key}_{month_str}.jsonl"
    if not path.exists():
        return []
    target_date = run_date.strftime("%Y-%m-%d")
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
            if rec.get("date") == target_date:
                out.append(rec)
    out.sort(key=lambda r: r.get("timestamp", ""))
    return out


def _read_intraday_curve(model_key: str, run_date: datetime) -> list[dict[str, Any]]:
    """Read /data/intraday/{model}_{date}.jsonl for today's tick-by-tick valuations."""
    date_str = run_date.strftime("%Y-%m-%d")
    path = INTRADAY_DIR / f"{model_key}_{date_str}.jsonl"
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _daily_pnl(model_key: str) -> tuple[float | None, float | None]:
    """Returns (daily_pnl_dollars, daily_pnl_pct) using the last two perf rows."""
    df = load_performance_history(model_key)
    if df.empty:
        return None, None
    if len(df) == 1:
        return 0.0, 0.0
    today_val = float(df["total_value"].iloc[-1])
    prev_val = float(df["total_value"].iloc[-2])
    delta = today_val - prev_val
    pct = (delta / prev_val) if prev_val else 0.0
    return delta, pct


def _previous_leaderboard(run_date: datetime) -> dict[str, int] | None:
    """Find the most recent leaderboard snapshot strictly before run_date.

    Returns {model_key: rank} or None if no prior snapshot exists.
    """
    if not LEADERBOARD_DIR.exists():
        return None
    today_str = run_date.strftime("%Y-%m-%d")
    candidates = []
    for fp in LEADERBOARD_DIR.glob("*.json"):
        stem = fp.stem
        if stem < today_str:
            candidates.append((stem, fp))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    _, latest = candidates[0]
    try:
        with open(latest, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {row["model_key"]: row["rank"] for row in data}
    except Exception as e:
        logger.warning("Failed to read previous leaderboard %s: %s", latest, e)
        return None


# ===========================================================================
# MARKET SUMMARY — analytical prose synthesized from data
# ===========================================================================

def _index_table(index_data: dict[str, pd.DataFrame]) -> str:
    from ..data import INDEX_SYMBOLS
    headers = ["Index", "Close", "Change", "%"]
    rows = []
    for sym, label in INDEX_SYMBOLS.items():
        df = index_data.get(sym)
        if df is None or df.empty:
            rows.append([label, "—", "—", "—"])
            continue
        close = float(df["Close"].iloc[-1])
        prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else close
        change = close - prev
        pct = (change / prev) if prev else 0.0
        rows.append([
            label,
            _num(close),
            f"{'+' if change >= 0 else '-'}{abs(change):,.2f}",
            _pct(pct),
        ])
    return _format_table(headers, rows, aligns=["L", "R", "R", "R"])


def _generate_market_prose(
    index_data: dict[str, pd.DataFrame],
    market_data: dict[str, pd.DataFrame],
) -> str:
    """Synthesize 3-5 sentences of analytical prose about the trading day.

    Pulls only from price data — no fabricated news. Reads like a desk briefing.
    """
    universe = load_universe()
    sector_map = {t["symbol"]: t["sector"] for t in universe["tickers"]}

    # Index moves
    def _pct_change(df: pd.DataFrame) -> float | None:
        if df is None or df.empty or len(df) < 2:
            return None
        return float(df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1)

    spx = _pct_change(index_data.get("^GSPC", pd.DataFrame()))
    ndx = _pct_change(index_data.get("^IXIC", pd.DataFrame()))
    dji = _pct_change(index_data.get("^DJI", pd.DataFrame()))

    # Universe-level stats
    moves: list[tuple[str, float, str]] = []
    for sym, df in market_data.items():
        if sym not in sector_map:
            continue
        if df is None or df.empty or len(df) < 2:
            continue
        ch = float(df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1)
        moves.append((sym, ch, sector_map[sym]))

    # Sentence 1 — overall tone
    if spx is None:
        s1 = "U.S. equity index data was unavailable for today's session."
    else:
        if spx > 0.005:
            tone = "closed firmly higher"
        elif spx > 0:
            tone = "ground out modest gains"
        elif spx > -0.005:
            tone = "drifted lower"
        else:
            tone = "sold off"
        s1 = f"U.S. equities {tone} on the session, with the S&P 500 finishing {_pct(spx)}."

    # Sentence 2 — index breakdown
    if ndx is not None and dji is not None and spx is not None:
        leaders = []
        if ndx > spx + 0.001:
            leaders.append(f"the Nasdaq Composite outperformed at {_pct(ndx)}")
        elif ndx < spx - 0.001:
            leaders.append(f"the Nasdaq Composite lagged at {_pct(ndx)}")
        else:
            leaders.append(f"the Nasdaq Composite moved in line at {_pct(ndx)}")

        if dji > spx + 0.001:
            leaders.append(f"the Dow added {_pct(dji)}")
        elif dji < spx - 0.001:
            leaders.append(f"the Dow trailed at {_pct(dji)}")
        else:
            leaders.append(f"the Dow tracked the broader market at {_pct(dji)}")
        s2 = " ".join([leaders[0].capitalize() + " while " + leaders[1] + "."])
    else:
        s2 = ""

    # Sentence 3 — sector lead/lag
    s3 = ""
    if moves:
        sector_buckets: dict[str, list[float]] = {}
        for sym, ch, sec in moves:
            sector_buckets.setdefault(sec, []).append(ch)
        sector_avgs = {s: sum(v) / len(v) for s, v in sector_buckets.items() if v}
        if sector_avgs:
            best_sec = max(sector_avgs.items(), key=lambda x: x[1])
            worst_sec = min(sector_avgs.items(), key=lambda x: x[1])
            if best_sec[0] != worst_sec[0]:
                s3 = (
                    f"Sector dispersion within the universe favored {best_sec[0]} ({_pct(best_sec[1])}), "
                    f"while {worst_sec[0]} lagged at {_pct(worst_sec[1])}."
                )

    # Sentence 4 — top mover / bottom mover
    s4 = ""
    if moves:
        moves_sorted = sorted(moves, key=lambda x: x[1], reverse=True)
        top = moves_sorted[0]
        bot = moves_sorted[-1]
        if top[0] != bot[0]:
            s4 = (
                f"{top[0]} led individual names at {_pct(top[1])}, "
                f"with {bot[0]} the worst performer at {_pct(bot[1])}."
            )

    # Sentence 5 — breadth
    s5 = ""
    if moves:
        positives = sum(1 for _, ch, _ in moves if ch > 0)
        total = len(moves)
        if total:
            s5 = f"Breadth was {'positive' if positives > total/2 else 'negative' if positives < total/2 else 'flat'} with {positives} of {total} universe names higher on the day."

    sentences = [s for s in (s1, s2, s3, s4, s5) if s]
    return " ".join(sentences)


# ===========================================================================
# PERFORMANCE TABLE — sorted by today's daily P&L
# ===========================================================================

def _build_performance_table(
    model_keys: list[str],
    run_date: datetime,
    settings: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Returns (markdown_table, rows_data_for_reuse)."""
    rows_data: list[dict[str, Any]] = []
    for key in model_keys:
        df = load_performance_history(key)
        if df.empty:
            continue
        today_val = float(df["total_value"].iloc[-1])
        cash_pct = float(df["cash_pct"].iloc[-1])
        cum_ret = float(df["cumulative_return"].iloc[-1])
        daily_pnl, daily_pct = _daily_pnl(key)

        # Total trades today across ALL intraday ticks (not just the last one)
        records = _read_today_trade_records(key, run_date)
        n_trades = 0
        for rec in records:
            for e in rec.get("executions", []):
                if e.get("executed") and e.get("side") in ("BUY", "SELL"):
                    n_trades += 1
        # Keep `record` pointing at the most recent tick for api_success display
        record = records[-1] if records else None

        # Alpha vs SPY for the run
        alpha = None
        if "benchmark_value" in df.columns and df["benchmark_value"].notna().sum() >= 2:
            bench = df["benchmark_value"].dropna().astype(float).values
            if len(bench) >= 2 and bench[0] > 0:
                alpha = cum_ret - (bench[-1] / bench[0] - 1.0)

        api_success = bool(record.get("api_success", True)) if record else False
        rows_data.append({
            "model_key": key,
            "value": today_val,
            "daily_pnl": daily_pnl,
            "daily_pct": daily_pct,
            "cum_return": cum_ret,
            "alpha": alpha,
            "trades": n_trades,
            "cash_pct": cash_pct,
            "api_success": api_success,
        })

    # Sort: successful runs above failed runs, then by daily_pct desc, then by
    # trade activity (more trades wins ties), then alphabetically for stability.
    # This prevents a failed model from anchoring rank #1 on tied 0% days.
    rows_data.sort(key=lambda r: (
        not r["api_success"],
        r["daily_pct"] is None,
        -(r["daily_pct"] or 0),
        -r["trades"],
        r["model_key"],
    ))

    headers = ["#", "Model", "Cohort", "Value", "Daily P&L", "Daily %", "Cum. Return", "Alpha vs SPY", "Trades", "Cash %"]
    models_cfg = settings.get("models", {})
    rows: list[list[str]] = []
    for i, r in enumerate(rows_data, 1):
        cfg = models_cfg.get(r["model_key"], {})
        name = cfg.get("display_name", r["model_key"].upper())
        cohort_label = "EXP" if cfg.get("cohort") == "expansion" else "core"
        if i == 1:
            name = f"**{name}**"
        rows.append([
            str(i),
            name,
            cohort_label,
            _money(r["value"]),
            _signed_money(r["daily_pnl"]),
            _pct(r["daily_pct"]),
            _pct(r["cum_return"]),
            _pct(r["alpha"]) if r["alpha"] is not None else "—",
            str(r["trades"]),
            _pct(r["cash_pct"], sign=False),
        ])

    table = _format_table(headers, rows, aligns=["R", "L", "C", "R", "R", "R", "R", "R", "R", "R"])
    return table, rows_data


# ===========================================================================
# MODEL-BY-MODEL BREAKDOWN — 3-5 sentence prose per model
# ===========================================================================

def _model_breakdown(model_key: str, run_date: datetime) -> str:
    record = _read_today_trade_record(model_key, run_date)
    portfolio = load_portfolio(model_key)
    snapshot = portfolio.snapshot({})  # weights based on last avg_cost; only used for structure summary

    if record is None:
        return f"No decision log entry recorded for {model_key.upper()} today. Likely the model was disabled, the daily run skipped this slot, or the file has not yet been written."

    if not record.get("api_success", True):
        err = _short_err(record.get("api_error", "unknown error"))
        return f"API call failed: _{err}_. No trades executed. Portfolio unchanged from prior session."

    executed = [e for e in record.get("executions", [])
                if e.get("executed") and e.get("side") in ("BUY", "SELL")]
    overall = record.get("overall_reasoning", "").strip()
    portfolio_after = record.get("portfolio_after", {})
    holdings = portfolio_after.get("holdings", []) or snapshot["holdings"]
    cash_pct = portfolio_after.get("cash_pct", snapshot["cash_pct"])
    n_pos = len(holdings)

    # Sentence 1 — what it did
    if not executed:
        s1 = "Held all positions today; no trades executed."
    else:
        buys = [e for e in executed if e["side"] == "BUY"]
        sells = [e for e in executed if e["side"] == "SELL"]
        parts = []
        if buys:
            top = max(buys, key=lambda e: e.get("notional", 0))
            shares_str = f"{top['shares']:.0f}" if top["shares"] >= 1 else f"{top['shares']:.4f}"
            parts.append(f"bought {shares_str} {top['ticker']} at {_money(top['fill_price'])}")
            if len(buys) > 1:
                parts.append(f"plus {len(buys)-1} other buy{'s' if len(buys)>2 else ''}")
        if sells:
            top = max(sells, key=lambda e: e.get("notional", 0))
            shares_str = f"{top['shares']:.0f}" if top["shares"] >= 1 else f"{top['shares']:.4f}"
            verb = "trimmed" if any(h["ticker"] == top["ticker"] for h in holdings) else "exited"
            parts.append(f"{verb} {top['ticker']} ({shares_str} sh @ {_money(top['fill_price'])})")
            if len(sells) > 1:
                parts.append(f"and {len(sells)-1} other sell{'s' if len(sells)>2 else ''}")
        s1 = "Today: " + ", ".join(parts) + "."

    # Sentence 2 — reasoning anchor (highest confidence executed trade)
    s2 = ""
    if executed:
        anchor = max(executed,
                     key=lambda e: (e.get("decision") or {}).get("confidence", 0))
        reason = ((anchor.get("decision") or {}).get("reasoning") or "").strip()
        conf = (anchor.get("decision") or {}).get("confidence")
        if reason:
            reason_short = reason if len(reason) <= 220 else reason[:217] + "…"
            conf_str = f" (conviction {conf}/10)" if conf else ""
            s2 = f'Highest-conviction rationale{conf_str}: "{reason_short}"'
    elif overall:
        overall_short = overall if len(overall) <= 220 else overall[:217] + "…"
        s2 = f'Stated view: "{overall_short}"'

    # Sentence 3 — current positioning
    cash_pct_val = cash_pct if isinstance(cash_pct, (int, float)) else 0
    if n_pos == 0:
        s3 = f"Portfolio is now 100% cash."
    else:
        # Top concentration
        top_h = max(holdings, key=lambda h: h.get("weight", 0))
        sectors = {h.get("ticker"): None for h in holdings}
        s3 = (
            f"Now holds {n_pos} position{'s' if n_pos != 1 else ''} "
            f"with {cash_pct_val*100:.1f}% cash; largest weight is "
            f"{top_h.get('ticker','?')} at {top_h.get('weight',0)*100:.1f}%."
        )

    # Sentence 4 — notable behavior flag
    s4 = ""
    if cash_pct_val > 0.50:
        s4 = "Notable: heavy defensive cash positioning."
    elif n_pos == 1:
        s4 = "Notable: single-name concentration."
    elif holdings:
        top_h = max(holdings, key=lambda h: h.get("weight", 0))
        if top_h.get("weight", 0) >= 0.18:
            s4 = f"Notable: at the position-cap on {top_h['ticker']}."

    # Sentence 5 — violations or risk
    s5 = ""
    violations = record.get("violations", [])
    if violations:
        rules = sorted({v["rule"] for v in violations})
        s5 = f"Risk filter rejected {len(violations)} decision(s): {', '.join(rules)}."

    sentences = [s for s in (s1, s2, s3, s4, s5) if s]
    return " ".join(sentences)


# ===========================================================================
# INTRADAY SESSION SUMMARY — aggregates all 15-min ticks of the day
# ===========================================================================

def _build_intraday_session_table(
    model_keys: list[str],
    run_date: datetime,
) -> str:
    """One row per model showing the day's intraday activity profile.

    Pulls from both the intraday valuation log (high/low/range) and the
    decision log (trades, runs, first/last tick) so every column comes
    straight from the source-of-truth files.
    """
    headers = ["Model", "Cohort", "Ticks", "Trades", "First Tick", "Last Tick",
               "Intraday High", "Intraday Low", "Range %"]
    rows: list[list[str]] = []
    any_data = False
    settings = load_settings()
    models_cfg = settings.get("models", {})

    for key in model_keys:
        ticks = _read_intraday_curve(key, run_date)
        records = _read_today_trade_records(key, run_date)
        n_runs = len(ticks) or len(records)
        if n_runs == 0:
            continue
        any_data = True

        # Trade total across all ticks of the day
        n_trades = 0
        for rec in records:
            for e in rec.get("executions", []):
                if e.get("executed") and e.get("side") in ("BUY", "SELL"):
                    n_trades += 1

        # Intraday high/low/range from the valuation snapshots
        if ticks:
            values = [float(t.get("total_value", 0)) for t in ticks if t.get("total_value")]
            hi = max(values) if values else None
            lo = min(values) if values else None
            range_pct = ((hi - lo) / lo) if (hi is not None and lo and lo > 0) else None
            first_ts = ticks[0].get("timestamp", "")
            last_ts = ticks[-1].get("timestamp", "")
        else:
            hi = lo = range_pct = None
            first_ts = records[0].get("timestamp", "") if records else ""
            last_ts = records[-1].get("timestamp", "") if records else ""

        # Compact HH:MM ET-ish display — strip down ISO timestamps
        def _short_ts(ts: str) -> str:
            if not ts:
                return "—"
            # ISO format like 2026-04-09T14:23:11-04:00
            if "T" in ts:
                return ts.split("T", 1)[1][:5]
            return ts[-8:-3] if len(ts) >= 8 else ts

        cfg = models_cfg.get(key, {})
        label = cfg.get("display_name", key.upper())
        cohort_label = "EXP" if cfg.get("cohort") == "expansion" else "core"
        rows.append([
            label,
            cohort_label,
            str(n_runs),
            str(n_trades),
            _short_ts(first_ts),
            _short_ts(last_ts),
            _money(hi) if hi is not None else "—",
            _money(lo) if lo is not None else "—",
            _pct(range_pct, sign=False) if range_pct is not None else "—",
        ])

    if not any_data:
        return "_No intraday tick data recorded for this session._"

    return _format_table(headers, rows, aligns=["L", "C", "R", "R", "R", "R", "R", "R", "R"])


# ===========================================================================
# EXPANSION COHORT — Sonnet vs Opus cost-performance comparison
# ===========================================================================

def _build_expansion_cohort_section(
    settings: dict[str, Any],
    run_date: datetime,
) -> str:
    """Compare Claude Sonnet (core) vs Claude Opus (expansion) head-to-head.

    The expansion cohort exists to answer one specific research question:
    is the 5x more expensive Opus actually delivering 5x more trading edge,
    or is the cheaper Sonnet competitive on a cost-adjusted basis?

    This section pulls performance metrics + cumulative API cost for both
    models and renders a side-by-side table plus a prose key finding.
    Returns "" if no expansion cohort entries are configured.
    """
    models_cfg = settings.get("models", {})
    expansion_keys = [k for k, cfg in models_cfg.items() if cfg.get("cohort") == "expansion"]
    if not expansion_keys:
        return ""

    # Sonnet anchor: the canonical "claude" key in the core cohort.
    sonnet_key = "claude" if models_cfg.get("claude", {}).get("cohort") == "core" else None
    if not sonnet_key:
        return _expansion_cohort_solo(expansion_keys, models_cfg)

    sonnet_cfg = models_cfg[sonnet_key]
    sections: list[str] = []
    for opus_key in expansion_keys:
        opus_cfg = models_cfg[opus_key]
        sonnet_metrics = compute_metrics(sonnet_key)
        opus_metrics = compute_metrics(opus_key)
        sonnet_cost = compute_api_cost_summary(sonnet_key)
        opus_cost = compute_api_cost_summary(opus_key)

        sonnet_label = sonnet_cfg.get("display_name", sonnet_key.upper())
        opus_label = opus_cfg.get("display_name", opus_key.upper())

        headers = ["Metric", sonnet_label, opus_label, "Δ (Opus − Sonnet)"]

        def _delta_pct(opus, sonnet):
            if opus is None or sonnet is None:
                return "—"
            d = opus - sonnet
            return f"{d*100:+.2f}pp"

        def _delta_num(opus, sonnet, digits=2):
            if opus is None or sonnet is None:
                return "—"
            d = opus - sonnet
            return f"{d:+.{digits}f}"

        def _delta_money(opus, sonnet):
            if opus is None or sonnet is None:
                return "—"
            d = opus - sonnet
            sign = "+" if d >= 0 else "-"
            return f"{sign}${abs(d):,.4f}"

        rows = [
            ["Cumulative return",
             _pct(sonnet_metrics.get("cumulative_return")),
             _pct(opus_metrics.get("cumulative_return")),
             _delta_pct(opus_metrics.get("cumulative_return"), sonnet_metrics.get("cumulative_return"))],
            ["Sharpe (30d)",
             _num(sonnet_metrics.get("sharpe_30d")) if sonnet_metrics.get("sharpe_30d") is not None else "—",
             _num(opus_metrics.get("sharpe_30d")) if opus_metrics.get("sharpe_30d") is not None else "—",
             _delta_num(opus_metrics.get("sharpe_30d"), sonnet_metrics.get("sharpe_30d"))],
            ["Max drawdown",
             _pct(sonnet_metrics.get("max_drawdown")) if sonnet_metrics.get("max_drawdown") is not None else "—",
             _pct(opus_metrics.get("max_drawdown")) if opus_metrics.get("max_drawdown") is not None else "—",
             _delta_pct(opus_metrics.get("max_drawdown"), sonnet_metrics.get("max_drawdown"))],
            ["Alpha vs SPY",
             _pct(sonnet_metrics.get("alpha_vs_spy")) if sonnet_metrics.get("alpha_vs_spy") is not None else "—",
             _pct(opus_metrics.get("alpha_vs_spy")) if opus_metrics.get("alpha_vs_spy") is not None else "—",
             _delta_pct(opus_metrics.get("alpha_vs_spy"), sonnet_metrics.get("alpha_vs_spy"))],
            ["Current value",
             _money(sonnet_metrics.get("current_value")),
             _money(opus_metrics.get("current_value")),
             _delta_money(opus_metrics.get("current_value"), sonnet_metrics.get("current_value"))],
            ["—", "—", "—", "—"],
            ["API calls",
             str(sonnet_cost["calls"]),
             str(opus_cost["calls"]),
             f"{opus_cost['calls'] - sonnet_cost['calls']:+d}"],
            ["Total input tokens",
             f"{sonnet_cost['input_tokens']:,}",
             f"{opus_cost['input_tokens']:,}",
             f"{opus_cost['input_tokens'] - sonnet_cost['input_tokens']:+,}"],
            ["Total output tokens",
             f"{sonnet_cost['output_tokens']:,}",
             f"{opus_cost['output_tokens']:,}",
             f"{opus_cost['output_tokens'] - sonnet_cost['output_tokens']:+,}"],
            ["Total API cost",
             f"${sonnet_cost['cost_usd']:,.4f}",
             f"${opus_cost['cost_usd']:,.4f}",
             _delta_money(opus_cost["cost_usd"], sonnet_cost["cost_usd"])],
        ]

        # Cost per dollar of P&L — the actual cost-performance metric.
        sonnet_ret = sonnet_metrics.get("cumulative_return") or 0.0
        opus_ret = opus_metrics.get("cumulative_return") or 0.0
        sonnet_cur = float(sonnet_metrics.get("current_value", 0) or 0)
        opus_cur = float(opus_metrics.get("current_value", 0) or 0)
        sonnet_pnl = (sonnet_ret * sonnet_cur / (1 + sonnet_ret)) if sonnet_ret else 0.0
        opus_pnl = (opus_ret * opus_cur / (1 + opus_ret)) if opus_ret else 0.0

        rows.append([
            "Cost / $ of P&L",
            f"${(sonnet_cost['cost_usd'] / sonnet_pnl):,.4f}" if sonnet_pnl > 0 else "—",
            f"${(opus_cost['cost_usd'] / opus_pnl):,.4f}" if opus_pnl > 0 else "—",
            "lower = better",
        ])

        table = _format_table(headers, rows, aligns=["L", "R", "R", "R"])

        finding = _expansion_cohort_finding(
            sonnet_label=sonnet_label,
            opus_label=opus_label,
            sonnet_metrics=sonnet_metrics,
            opus_metrics=opus_metrics,
            sonnet_cost=sonnet_cost,
            opus_cost=opus_cost,
        )

        sections.append(
            f"### {sonnet_label}  vs  {opus_label}\n\n"
            f"{finding}\n\n"
            f"{table}"
        )

    return "\n\n".join(sections)


def _expansion_cohort_solo(
    expansion_keys: list[str],
    models_cfg: dict[str, Any],
) -> str:
    """Fallback when there's no Sonnet anchor — just list expansion entries."""
    lines = ["_No Sonnet anchor in core cohort; expansion entries listed without head-to-head comparison._\n"]
    for key in expansion_keys:
        m = compute_metrics(key)
        c = compute_api_cost_summary(key)
        label = models_cfg[key].get("display_name", key.upper())
        lines.append(
            f"- **{label}**: cum. return {_pct(m.get('cumulative_return'))}, "
            f"{c['calls']} calls, ${c['cost_usd']:,.4f} spent."
        )
    return "\n".join(lines)


def _expansion_cohort_finding(
    sonnet_label: str,
    opus_label: str,
    sonnet_metrics: dict[str, Any],
    opus_metrics: dict[str, Any],
    sonnet_cost: dict[str, Any],
    opus_cost: dict[str, Any],
) -> str:
    """Synthesize the prose 'key finding' for the cost-performance comparison."""
    sonnet_ret = sonnet_metrics.get("cumulative_return")
    opus_ret = opus_metrics.get("cumulative_return")

    if sonnet_ret is None or opus_ret is None:
        return f"_Insufficient performance data to compare {sonnet_label} and {opus_label} on a cost-adjusted basis._"

    if sonnet_cost["calls"] == 0 or opus_cost["calls"] == 0:
        return (
            f"_Cost data not yet available for both models — comparison will activate "
            f"once {sonnet_label} and {opus_label} have each completed at least one tick._"
        )

    cost_ratio = opus_cost["cost_usd"] / sonnet_cost["cost_usd"] if sonnet_cost["cost_usd"] > 0 else None
    return_delta_pp = (opus_ret - sonnet_ret) * 100
    cost_ratio_str = f"{cost_ratio:.1f}x" if cost_ratio else "—"

    if abs(return_delta_pp) < 0.05:
        return (
            f"**Key finding:** {opus_label} and {sonnet_label} are running essentially flat "
            f"on cumulative return ({_pct(opus_ret)} vs {_pct(sonnet_ret)}, Δ {return_delta_pp:+.2f}pp), "
            f"but {opus_label} has cost {cost_ratio_str} as much in API spend "
            f"(${opus_cost['cost_usd']:,.4f} vs ${sonnet_cost['cost_usd']:,.4f}). "
            f"At current performance the expansion cohort is **not earning its premium** — "
            f"the cheaper Sonnet variant is delivering equivalent edge for a fraction of the spend."
        )
    elif return_delta_pp > 0:
        return (
            f"**Key finding:** {opus_label} is outperforming {sonnet_label} by "
            f"{return_delta_pp:+.2f}pp on cumulative return ({_pct(opus_ret)} vs {_pct(sonnet_ret)}). "
            f"It's also costing {cost_ratio_str} as much in API spend "
            f"(${opus_cost['cost_usd']:,.4f} vs ${sonnet_cost['cost_usd']:,.4f}). "
            f"Whether the premium is justified depends on the magnitude of the lead vs the "
            f"cost gap — track this trend over the coming weeks before drawing conclusions."
        )
    else:
        return (
            f"**Key finding:** {sonnet_label} is currently **beating** {opus_label} by "
            f"{abs(return_delta_pp):.2f}pp on cumulative return "
            f"({_pct(sonnet_ret)} vs {_pct(opus_ret)}) while costing {cost_ratio_str} less in API spend "
            f"(${sonnet_cost['cost_usd']:,.4f} vs ${opus_cost['cost_usd']:,.4f}). "
            f"If this holds, the cheaper model is dominating the expensive one on both axes — "
            f"a meaningful signal that raw model capability isn't the bottleneck on this task."
        )


# ===========================================================================
# LEADERBOARD with rank arrows
# ===========================================================================

def _build_leaderboard_table(
    model_keys: list[str],
    run_date: datetime,
) -> str:
    leaderboard = build_leaderboard(model_keys)
    prev_ranks = _previous_leaderboard(run_date) or {}
    settings = load_settings()
    models_cfg = settings.get("models", {})

    headers = ["#", "Δ", "Model", "Cohort", "Cum. Return", "Sharpe (30d)", "Max DD", "Days"]
    rows: list[list[str]] = []
    for row in leaderboard:
        key = row["model_key"]
        cur_rank = row["rank"]
        prev = prev_ranks.get(key)
        if prev is None:
            arrow = "–"
        elif prev > cur_rank:
            arrow = f"↑{prev - cur_rank}"
        elif prev < cur_rank:
            arrow = f"↓{cur_rank - prev}"
        else:
            arrow = "–"

        cfg = models_cfg.get(key, {})
        name = cfg.get("display_name", key.upper())
        cohort_label = "EXP" if cfg.get("cohort") == "expansion" else "core"
        if cur_rank == 1:
            name = f"**{name}**"

        rows.append([
            str(cur_rank),
            arrow,
            name,
            cohort_label,
            _pct(row.get("cumulative_return")),
            _num(row["sharpe_30d"]) if row.get("sharpe_30d") is not None else "—",
            _pct(row["max_drawdown"]) if row.get("max_drawdown") is not None else "—",
            str(row.get("days", 0)),
        ])
    return _format_table(headers, rows, aligns=["R", "C", "L", "C", "R", "R", "R", "R"])


# ===========================================================================
# RISK & SYSTEM HEALTH
# ===========================================================================

def _build_health_section(model_keys: list[str], run_date: datetime) -> str:
    notes: list[str] = []

    for key in model_keys:
        portfolio = load_portfolio(key)
        record = _read_today_trade_record(key, run_date)

        if portfolio.halted:
            notes.append(f"- **{key.upper()}**: portfolio HALTED (hard stop-loss triggered).")

        if record is None:
            continue

        if not record.get("api_success", True):
            notes.append(f"- **{key.upper()}**: API failure — _{_short_err(record.get('api_error', 'unknown'))}_.")

        latency = record.get("api_latency_seconds")
        if isinstance(latency, (int, float)) and latency > LATENCY_WARN_THRESHOLD:
            notes.append(f"- **{key.upper()}**: high API latency ({latency:.1f}s).")

        # Forced liquidations show up as executions with order_id starting FORCED_
        forced = [
            e for e in record.get("executions", [])
            if str(e.get("order_id", "")).startswith("FORCED_")
        ]
        if forced:
            tickers = ", ".join(e["ticker"] for e in forced)
            notes.append(f"- **{key.upper()}**: position stop-loss triggered on {tickers}.")

        violations = record.get("violations", [])
        if violations:
            critical = [v for v in violations if v["rule"] in ("PORTFOLIO_HALTED", "DAILY_TRADE_CAP")]
            if critical:
                rules = ", ".join(sorted({v["rule"] for v in critical}))
                notes.append(f"- **{key.upper()}**: risk-control violation — {rules}.")

    if not notes:
        return "No risk events. All systems nominal."
    return "\n".join(notes)


# ===========================================================================
# HEADER + EXPERIMENT DAY
# ===========================================================================

def _header(run_date: datetime, settings: dict[str, Any], index_data: dict[str, pd.DataFrame]) -> str:
    inception_str = settings["experiment_start_date"]
    end_str = settings["experiment_end_date"]
    try:
        inception = datetime.strptime(inception_str, "%Y-%m-%d")
        end = datetime.strptime(end_str, "%Y-%m-%d")
        day_num = max(1, (run_date.date() - inception.date()).days + 1)
        total_days = (end.date() - inception.date()).days + 1
        day_str = f"Day {day_num} / {total_days}"
    except ValueError:
        day_str = "Day —"

    phase = settings["phase"]
    mode = settings["mode"].upper()

    # Market status: if we have a today-dated index print, treat session as closed
    target = run_date.strftime("%Y-%m-%d")
    closed = False
    spx_df = index_data.get("^GSPC")
    if spx_df is not None and not spx_df.empty:
        last_idx_date = pd.to_datetime(spx_df.index[-1]).strftime("%Y-%m-%d")
        if last_idx_date == target:
            closed = True
    market_status = "Closed" if closed else "Mid-session"

    return (
        f"# Daily Report — {target}\n\n"
        f"**{day_str}**  ·  **Phase:** {phase}  ·  **Mode:** {mode}  ·  **Market:** {market_status}"
    )


# ===========================================================================
# MAIN ENTRY POINT
# ===========================================================================

def generate_daily_report(
    run_date: datetime,
    market_data: dict[str, pd.DataFrame],
    index_data: dict[str, pd.DataFrame],
    settings: dict[str, Any] | None = None,
) -> Path:
    """Build the daily report and write it to /reports/daily/YYYY-MM-DD.md.

    Returns the path to the written file.
    """
    if settings is None:
        settings = load_settings()

    DAILY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target_path = DAILY_REPORTS_DIR / f"{run_date.strftime('%Y-%m-%d')}.md"

    model_keys = [k for k, cfg in settings["models"].items() if cfg.get("enabled", True)]

    # ---- Build sections ----
    header = _header(run_date, settings, index_data)
    market_prose = _generate_market_prose(index_data, market_data)
    index_tbl = _index_table(index_data)
    perf_tbl, perf_rows = _build_performance_table(model_keys, run_date, settings)

    breakdown_blocks: list[str] = []
    for key in model_keys:
        cfg = settings["models"].get(key, {})
        label = cfg.get("display_name", key.upper())
        cohort_tag = " _(expansion)_" if cfg.get("cohort") == "expansion" else ""
        breakdown_blocks.append(f"### {label}{cohort_tag}\n\n{_model_breakdown(key, run_date)}")
    breakdown_section = "\n\n".join(breakdown_blocks)

    intraday_session_tbl = _build_intraday_session_table(model_keys, run_date)
    expansion_cohort_section = _build_expansion_cohort_section(settings, run_date)
    leaderboard_tbl = _build_leaderboard_table(model_keys, run_date)
    health_section = _build_health_section(model_keys, run_date)

    generated_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    prompt_version = settings.get("prompt_version", "v1")

    # Machine-readable footer for the analytics aggregator
    metadata = {
        "date": run_date.strftime("%Y-%m-%d"),
        "phase": settings["phase"],
        "mode": settings["mode"],
        "prompt_version": prompt_version,
        "models": [
            {
                "key": r["model_key"],
                "value": r["value"],
                "daily_pnl": r["daily_pnl"],
                "daily_pct": r["daily_pct"],
                "cum_return": r["cum_return"],
                "alpha": r["alpha"],
                "trades": r["trades"],
                "cash_pct": r["cash_pct"],
            }
            for r in perf_rows
        ],
        "leader": perf_rows[0]["model_key"] if perf_rows else None,
    }
    metadata_block = "<!-- METADATA\n" + json.dumps(metadata, indent=2, default=str) + "\n-->"

    # ---- Assemble report ----
    report = "\n".join([
        header,
        "",
        "---",
        "",
        "## Market Summary",
        "",
        market_prose if market_prose else "_Market data unavailable for this session._",
        "",
        index_tbl,
        "",
        "---",
        "",
        f"## Model Performance — {run_date.strftime('%Y-%m-%d')}",
        "",
        perf_tbl,
        "",
        "---",
        "",
        "## Model-by-Model Breakdown",
        "",
        breakdown_section,
        "",
        "---",
        "",
        "## Intraday Session Profile",
        "",
        intraday_session_tbl,
        "",
        "---",
        "",
        "## Expansion Cohort — Cost-Performance",
        "",
        expansion_cohort_section if expansion_cohort_section else "_No expansion cohort entries configured._",
        "",
        "---",
        "",
        "## Leaderboard — Cumulative",
        "",
        leaderboard_tbl,
        "",
        "---",
        "",
        "## Risk & System Health",
        "",
        health_section,
        "",
        "---",
        "",
        f"*Generated {generated_str}  ·  LLM Trading Lab  ·  Prompt {prompt_version}*",
        "",
        metadata_block,
        "",
    ])

    with open(target_path, "w", encoding="utf-8") as f:
        f.write(report)

    logger.info("Daily report written: %s", target_path)
    return target_path
