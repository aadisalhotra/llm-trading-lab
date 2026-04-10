"""Monthly research report generator.

A rolled-up companion to the daily report, written once per calendar month
with month-to-date numbers (or month-final numbers when called from the
last EOD pass of the month). Six substantive sections — every section is
derived from real log data, no scaffolding or placeholders:

  1. Header + month at a glance
  2. Performance ranking (cumulative + monthly return per model)
  3. Trade activity (counts, BUY/SELL split, top traded tickers)
  4. Risk events (halts, position stops, cap violations)
  5. Sonnet vs Opus + cohort comparison
  6. Cost Analysis  ← featured section: spend, $/trade, ROI, callouts

Output: /reports/monthly/YYYY-MM.md
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ..analytics import (
    build_leaderboard,
    compute_api_cost_summary_window,
    compute_metrics,
    compute_spy_benchmark_metrics,
    load_performance_history,
)
from ..config_loader import (
    MONTHLY_REPORTS_DIR,
    TRADES_DIR,
    load_settings,
)
from ..portfolio import load_portfolio

logger = logging.getLogger("llmlab.reports.monthly")


# Re-use the dashboard's trade analytics engine for monthly consensus +
# calibration sections. It walks the same JSONL files but computes agreement
# and confidence correlation per trade.
def _lazy_trade_analytics(model_keys, settings):
    """Import and run the dashboard trade analytics engine."""
    from ..dashboard.build_data import _compute_trade_analytics
    # Build portfolio snapshots for current pricing
    portfolios = []
    for key in model_keys:
        try:
            p = load_portfolio(key)
            snap = p.snapshot({})  # no live prices in report context — zeros are fine
            snap["model_key"] = key
            portfolios.append(snap)
        except Exception:
            continue
    return _compute_trade_analytics(model_keys, portfolios)


# ---- formatters ----------------------------------------------------------

def _money(x: float | None) -> str:
    if x is None:
        return "—"
    return f"${x:,.2f}"


def _money4(x: float | None) -> str:
    if x is None:
        return "—"
    return f"${x:,.4f}"


def _pct(x: float | None, sign: bool = True) -> str:
    if x is None:
        return "—"
    s = f"{x*100:+.2f}%" if sign else f"{x*100:.2f}%"
    return s


def _signed_money(x: float | None) -> str:
    if x is None:
        return "—"
    sign = "+" if x >= 0 else "-"
    return f"{sign}${abs(x):,.2f}"


def _format_table(headers: list[str], rows: list[list[str]], aligns: list[str]) -> str:
    cols = len(headers)
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
        inner = w + 2
        if a == "R":
            sep_parts.append("-" * (inner - 1) + ":")
        elif a == "C":
            sep_parts.append(":" + "-" * (inner - 2) + ":")
        else:
            sep_parts.append(":" + "-" * (inner - 1))
    sep_line = "|" + "|".join(sep_parts) + "|"

    return "\n".join([fmt_row(headers), sep_line] + [fmt_row(r) for r in rows])


# ---- data helpers --------------------------------------------------------

def _month_bounds(month: datetime) -> tuple[datetime, datetime]:
    """Return (start, end) UTC datetimes for the calendar month containing `month`."""
    start = datetime(month.year, month.month, 1, tzinfo=timezone.utc)
    if month.month == 12:
        end = datetime(month.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(month.year, month.month + 1, 1, tzinfo=timezone.utc)
    return start, end


def _month_perf_slice(model_key: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Slice the EOD perf log to the rows whose date falls in [start, end)."""
    df = load_performance_history(model_key)
    if df.empty:
        return df
    # `date` column is already pd.Timestamp from load_performance_history
    mask = (df["date"] >= pd.Timestamp(start.date())) & (df["date"] < pd.Timestamp(end.date()))
    return df[mask].reset_index(drop=True)


def _walk_trades_for_month(
    model_key: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Yield every executed BUY/SELL execution for a model within [start, end).

    Walks the {model_key}_YYYY-MM.jsonl files. Returns a flat list of execution
    dicts, each with the parent record's timestamp attached.
    """
    pattern = re.compile(rf"^{re.escape(model_key)}_(\d{{4}})-(\d{{2}})\.jsonl$")
    out: list[dict[str, Any]] = []
    if not TRADES_DIR.exists():
        return out
    for fp in sorted(TRADES_DIR.iterdir()):
        if not fp.is_file():
            continue
        m = pattern.match(fp.name)
        if not m:
            continue
        year, month = int(m.group(1)), int(m.group(2))
        month_start = datetime(year, month, 1, tzinfo=timezone.utc)
        month_end = (month_start + timedelta(days=32)).replace(day=1)
        if month_end <= start or month_start >= end:
            continue
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_str = rec.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if not (start <= ts < end):
                    continue
                for ex in rec.get("executions") or []:
                    if not ex.get("executed") or ex.get("side") not in ("BUY", "SELL"):
                        continue
                    out.append({**ex, "_ts": ts, "_record_ts": ts_str})
    return out


# ---- Section 1: Header + at a glance -------------------------------------

def _section_header(month: datetime, settings: dict[str, Any]) -> str:
    month_label = month.strftime("%B %Y")
    phase = settings.get("phase", "—")
    mode = settings.get("mode", "—").upper()
    return (
        f"# Monthly Report — {month_label}\n\n"
        f"**Phase:** {phase}  ·  **Mode:** {mode}  ·  "
        f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )


def _section_at_a_glance(
    model_keys: list[str],
    month: datetime,
    settings: dict[str, Any],
) -> str:
    start, end = _month_bounds(month)
    total_trades = 0
    total_cost = 0.0
    days_traded: set[str] = set()
    for key in model_keys:
        executions = _walk_trades_for_month(key, start, end)
        total_trades += len(executions)
        for ex in executions:
            days_traded.add(ex["_ts"].strftime("%Y-%m-%d"))
        cost = compute_api_cost_summary_window(key, since=start, until=end)
        total_cost += cost["cost_usd"]

    lines = [
        "## 1. Month at a Glance",
        "",
        f"- **Trading days with activity:** {len(days_traded)}",
        f"- **Total executed trades across all models:** {total_trades:,}",
        f"- **Total API spend across all models:** ${total_cost:,.4f}",
        f"- **Active models:** {len([k for k in model_keys if settings['models'].get(k, {}).get('enabled', True)])}",
    ]
    return "\n".join(lines)


# ---- Section 2: Performance ranking --------------------------------------

def _section_performance(
    model_keys: list[str],
    month: datetime,
    settings: dict[str, Any],
) -> str:
    start, end = _month_bounds(month)
    rows_data: list[dict[str, Any]] = []
    for key in model_keys:
        cfg = settings["models"].get(key, {})
        df = _month_perf_slice(key, start, end)
        if df.empty:
            continue
        values = df["total_value"].astype(float).values
        if len(values) < 1:
            continue
        month_start_val = float(values[0])
        month_end_val = float(values[-1])
        month_return = (month_end_val / month_start_val - 1.0) if month_start_val > 0 else None
        # cumulative return = full-history return from compute_metrics
        full_metrics = compute_metrics(key)
        cum_return = full_metrics.get("cumulative_return")
        rows_data.append({
            "key": key,
            "label": cfg.get("display_name", key.upper()),
            "cohort": cfg.get("cohort", "core"),
            "month_start_val": month_start_val,
            "month_end_val": month_end_val,
            "month_return": month_return,
            "cum_return": cum_return,
            "days": int(len(df)),
        })

    rows_data.sort(key=lambda r: -(r["month_return"] or 0))
    headers = ["Rank", "Model", "Cohort", "Month Start", "Month End", "Month Return", "Cum. Return", "Days"]
    rows: list[list[str]] = []
    for i, r in enumerate(rows_data, 1):
        cohort_label = "EXP" if r["cohort"] == "expansion" else "core"
        name = f"**{r['label']}**" if i == 1 else r["label"]
        rows.append([
            str(i),
            name,
            cohort_label,
            _money(r["month_start_val"]),
            _money(r["month_end_val"]),
            _pct(r["month_return"]),
            _pct(r["cum_return"]),
            str(r["days"]),
        ])

    if not rows:
        return "## 2. Performance Ranking\n\n_No EOD performance data recorded this month._"

    table = _format_table(headers, rows, aligns=["R", "L", "C", "R", "R", "R", "R", "R"])
    return "## 2. Performance Ranking\n\n" + table


# ---- Section 3: Trade activity -------------------------------------------

def _section_trade_activity(
    model_keys: list[str],
    month: datetime,
    settings: dict[str, Any],
) -> str:
    start, end = _month_bounds(month)
    headers = ["Model", "Total Trades", "BUY", "SELL", "Top Traded Tickers"]
    rows: list[list[str]] = []
    for key in model_keys:
        cfg = settings["models"].get(key, {})
        executions = _walk_trades_for_month(key, start, end)
        if not executions:
            rows.append([cfg.get("display_name", key.upper()), "0", "0", "0", "—"])
            continue
        n_buys = sum(1 for e in executions if e.get("side") == "BUY")
        n_sells = sum(1 for e in executions if e.get("side") == "SELL")
        ticker_counts: dict[str, int] = {}
        for e in executions:
            t = e.get("ticker", "")
            if t:
                ticker_counts[t] = ticker_counts.get(t, 0) + 1
        top_tickers = sorted(ticker_counts.items(), key=lambda x: -x[1])[:3]
        top_str = ", ".join(f"{t}({n})" for t, n in top_tickers)
        rows.append([
            cfg.get("display_name", key.upper()),
            str(len(executions)),
            str(n_buys),
            str(n_sells),
            top_str or "—",
        ])
    table = _format_table(headers, rows, aligns=["L", "R", "R", "R", "L"])
    return "## 3. Trade Activity\n\n" + table


# ---- Section 4: Risk events ----------------------------------------------

def _section_risk_events(
    model_keys: list[str],
    month: datetime,
    settings: dict[str, Any],
) -> str:
    start, end = _month_bounds(month)
    notes: list[str] = []
    for key in model_keys:
        cfg = settings["models"].get(key, {})
        label = cfg.get("display_name", key.upper())
        executions = _walk_trades_for_month(key, start, end)
        forced = [
            e for e in executions
            if str(e.get("order_id", "")).startswith("FORCED_")
        ]
        if forced:
            tickers = sorted({e["ticker"] for e in forced})
            notes.append(f"- **{label}**: position stop-loss force-sell on {', '.join(tickers)}.")

        # Risk-rule violations from the parent record (we re-walk for these
        # since they're at the record level, not the execution level)
        pattern = re.compile(rf"^{re.escape(key)}_(\d{{4}})-(\d{{2}})\.jsonl$")
        if not TRADES_DIR.exists():
            continue
        all_violations: list[str] = []
        for fp in sorted(TRADES_DIR.iterdir()):
            if not fp.is_file():
                continue
            m = pattern.match(fp.name)
            if not m:
                continue
            year, mo = int(m.group(1)), int(m.group(2))
            if not (year == start.year and mo == start.month):
                continue
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for v in rec.get("violations") or []:
                        rule = v.get("rule", "?")
                        if rule in ("DAILY_TRADE_CAP", "PORTFOLIO_HALTED", "MAX_POSITIONS"):
                            all_violations.append(rule)
        if all_violations:
            counts: dict[str, int] = {}
            for r in all_violations:
                counts[r] = counts.get(r, 0) + 1
            summary = ", ".join(f"{r}×{n}" for r, n in counts.items())
            notes.append(f"- **{label}**: risk filter rejections — {summary}.")

        # Halted models
        portfolio = load_portfolio(key)
        if portfolio.halted:
            notes.append(f"- **{label}**: portfolio HALTED (hard stop-loss triggered).")

    if not notes:
        return "## 4. Risk Events\n\nNo risk events this month. All systems nominal."
    return "## 4. Risk Events\n\n" + "\n".join(notes)


# ---- Section 5: Cohort comparison ----------------------------------------

def _section_cohort_comparison(
    settings: dict[str, Any],
    month: datetime,
) -> str:
    """Per-cohort summary table — core models vs expansion + benchmark."""
    start, end = _month_bounds(month)
    rows_data: list[dict[str, Any]] = []
    for key, cfg in settings["models"].items():
        df = _month_perf_slice(key, start, end)
        if df.empty:
            continue
        values = df["total_value"].astype(float).values
        month_return = (values[-1] / values[0] - 1.0) if len(values) > 0 and values[0] > 0 else None
        cost = compute_api_cost_summary_window(key, since=start, until=end)
        rows_data.append({
            "key": key,
            "label": cfg.get("display_name", key.upper()),
            "cohort": cfg.get("cohort", "core"),
            "month_return": month_return,
            "month_cost": cost["cost_usd"],
        })

    spy = compute_spy_benchmark_metrics()
    spy_month_return = None
    if spy:
        # Approximate the SPY month return — slice the SPY synthetic curve to
        # the month window. compute_spy_benchmark_metrics already gives us the
        # full-history value but we want the month return specifically.
        # Re-derive from any model's perf log:
        for key in settings["models"]:
            df = _month_perf_slice(key, start, end)
            if df.empty or "benchmark_value" not in df.columns:
                continue
            bench = df["benchmark_value"].dropna().astype(float).values
            if len(bench) >= 2 and bench[0] > 0:
                spy_month_return = float(bench[-1] / bench[0] - 1.0)
                break

    headers = ["Cohort", "Model", "Month Return", "Month API Cost", "Net Month P&L (approx)"]
    rows: list[list[str]] = []
    starting_capital = float(settings.get("starting_capital", {}).get(
        settings.get("mode", "paper"), 100_000.0
    ))
    for r in sorted(rows_data, key=lambda x: (x["cohort"], -(x["month_return"] or 0))):
        cohort_label = "EXP" if r["cohort"] == "expansion" else "core"
        # Net month $ P&L = month_return × starting_capital − month_cost
        # (rough approximation; accurate enough for a monthly callout)
        gross_pnl = (r["month_return"] or 0) * starting_capital
        net_pnl = gross_pnl - r["month_cost"]
        rows.append([
            cohort_label,
            r["label"],
            _pct(r["month_return"]),
            _money4(r["month_cost"]),
            _signed_money(net_pnl),
        ])
    if spy_month_return is not None:
        rows.append([
            "BENCH",
            "_SPY (Benchmark)_",
            _pct(spy_month_return),
            "$0.0000",
            _signed_money(spy_month_return * starting_capital),
        ])

    table = _format_table(headers, rows, aligns=["C", "L", "R", "R", "R"])
    return "## 5. Cohort Comparison\n\n" + table


# ---- Section 6: Cost Analysis (FEATURED) ---------------------------------

def _section_cost_analysis(
    model_keys: list[str],
    month: datetime,
    settings: dict[str, Any],
) -> str:
    """The featured section — comprehensive monthly cost + ROI breakdown.

    Per-model: total spend, $/trade, cumulative spend since experiment start,
    ROI = (gross P&L − API cost) / API cost. Then highlight callouts for
    most/least efficient models, plus a Sonnet-vs-Opus cost-efficiency
    featured comparison.
    """
    start, end = _month_bounds(month)
    starting_capital = float(settings.get("starting_capital", {}).get(
        settings.get("mode", "paper"), 100_000.0
    ))

    rows_data: list[dict[str, Any]] = []
    for key in model_keys:
        cfg = settings["models"].get(key, {})
        month_cost = compute_api_cost_summary_window(key, since=start, until=end)
        total_cost = compute_api_cost_summary_window(key, since=None)
        df_month = _month_perf_slice(key, start, end)
        if df_month.empty:
            month_return_dollars: float | None = None
        else:
            values = df_month["total_value"].astype(float).values
            if len(values) >= 1 and values[0] > 0:
                month_return_dollars = float(values[-1] - values[0])
            else:
                month_return_dollars = None
        # Full-history gross P&L
        full_df = load_performance_history(key)
        gross_pnl_dollars: float | None = None
        if not full_df.empty:
            try:
                first_v = float(full_df["total_value"].iloc[0])
                last_v = float(full_df["total_value"].iloc[-1])
                gross_pnl_dollars = last_v - first_v
            except (KeyError, IndexError, ValueError):
                pass
        # ROI = (gross PnL − API cost) / API cost. None if cost is zero.
        roi = None
        if total_cost["cost_usd"] > 0 and gross_pnl_dollars is not None:
            roi = (gross_pnl_dollars - total_cost["cost_usd"]) / total_cost["cost_usd"]

        rows_data.append({
            "key": key,
            "label": cfg.get("display_name", key.upper()),
            "cohort": cfg.get("cohort", "core"),
            "provider": cfg.get("provider", "?"),
            "month_cost": month_cost["cost_usd"],
            "month_trades": int(month_cost["trades_executed"]),
            "month_return_dollars": month_return_dollars,
            "total_cost": total_cost["cost_usd"],
            "total_trades": int(total_cost["trades_executed"]),
            "cost_per_trade": (
                total_cost["cost_usd"] / total_cost["trades_executed"]
                if total_cost["trades_executed"] > 0 else None
            ),
            "gross_pnl": gross_pnl_dollars,
            "roi": roi,
        })

    # ----- main cost table -----
    headers = [
        "Model", "Cohort", "Month Spend", "Trades This Month", "$/Trade (Total)",
        "Total Spend (Cum.)", "Gross P&L (Cum.)", "ROI",
    ]
    table_rows: list[list[str]] = []
    for r in rows_data:
        table_rows.append([
            r["label"],
            "EXP" if r["cohort"] == "expansion" else "core",
            _money4(r["month_cost"]),
            str(r["month_trades"]),
            _money4(r["cost_per_trade"]) if r["cost_per_trade"] is not None else "—",
            _money4(r["total_cost"]),
            _signed_money(r["gross_pnl"]),
            f"{r['roi']*100:+.1f}%" if r["roi"] is not None else "—",
        ])
    cost_table = _format_table(
        headers, table_rows,
        aligns=["L", "C", "R", "R", "R", "R", "R", "R"],
    )

    # ----- callouts -----
    parts: list[str] = ["## 6. Cost Analysis", "", cost_table, ""]

    eligible = [r for r in rows_data if r["roi"] is not None]
    if eligible:
        best = max(eligible, key=lambda r: r["roi"])
        worst = min(eligible, key=lambda r: r["roi"])
        parts.append("### Cost-Efficiency Callouts")
        parts.append("")
        parts.append(
            f"- **Most cost-efficient:** {best['label']} — "
            f"ROI **{best['roi']*100:+.1f}%** "
            f"(${best['gross_pnl']:,.2f} gross P&L on ${best['total_cost']:,.4f} spend)."
        )
        parts.append(
            f"- **Least cost-efficient:** {worst['label']} — "
            f"ROI **{worst['roi']*100:+.1f}%** "
            f"(${worst['gross_pnl']:,.2f} gross P&L on ${worst['total_cost']:,.4f} spend)."
        )
        parts.append("")

    # ----- Sonnet vs Opus featured callout -----
    sonnet = next((r for r in rows_data if r["key"] == "claude"), None)
    opus = next((r for r in rows_data if r["key"] == "claude_opus"), None)
    if sonnet and opus:
        parts.append("### Featured: Claude Sonnet 4.6 vs Claude Opus 4.6 Cost-Efficiency")
        parts.append("")
        cost_ratio = (
            opus["total_cost"] / sonnet["total_cost"]
            if sonnet["total_cost"] > 0 else None
        )
        roi_delta = None
        if sonnet["roi"] is not None and opus["roi"] is not None:
            roi_delta = opus["roi"] - sonnet["roi"]
        if cost_ratio is None:
            verdict = (
                f"Sonnet has spent ${sonnet['total_cost']:.4f} this experiment, "
                f"Opus has spent ${opus['total_cost']:.4f}. Cost ratio not yet computable."
            )
        else:
            ratio_str = f"{cost_ratio:.1f}x"
            if roi_delta is None:
                verdict = (
                    f"Opus is currently {ratio_str} more expensive than Sonnet "
                    f"(${opus['total_cost']:.4f} vs ${sonnet['total_cost']:.4f}). "
                    f"ROI comparison pending more days of data."
                )
            elif roi_delta > 0:
                verdict = (
                    f"Opus is {ratio_str} more expensive than Sonnet but its ROI is "
                    f"**{roi_delta*100:+.1f}pp higher** "
                    f"(Opus {opus['roi']*100:+.1f}% vs Sonnet {sonnet['roi']*100:+.1f}%). "
                    f"The premium is currently being earned."
                )
            elif roi_delta < 0:
                verdict = (
                    f"Opus is {ratio_str} more expensive than Sonnet AND its ROI is "
                    f"**{abs(roi_delta)*100:.1f}pp lower** "
                    f"(Opus {opus['roi']*100:+.1f}% vs Sonnet {sonnet['roi']*100:+.1f}%). "
                    f"At current performance, Opus is **not earning its premium** — "
                    f"Sonnet is dominating on a cost-adjusted basis."
                )
            else:
                verdict = (
                    f"Opus is {ratio_str} more expensive than Sonnet and their ROIs "
                    f"are within rounding error. Sonnet is winning by default — "
                    f"same edge for less spend."
                )
        parts.append(verdict)

    return "\n".join(parts)


# ---- Section 7: Consensus Analysis ---------------------------------------

def _section_consensus_analysis(
    model_keys: list[str],
    settings: dict[str, Any],
) -> str:
    """Consensus Analysis — does model agreement predict returns?"""
    try:
        trades, agreement_returns, _ = _lazy_trade_analytics(model_keys, settings)
    except Exception as e:
        return f"## 7. Consensus Analysis\n\n_Could not compute: {e}_"

    parts = ["## 7. Consensus Analysis", ""]
    parts.append("_Does model agreement predict returns? Trades where 4+ models hold the "
                 "same stock vs trades where only 1-2 models hold it._")
    parts.append("")

    high = agreement_returns
    if high["high_count"] > 0 or high["low_count"] > 0:
        headers = ["Agreement Level", "Avg Return", "Trade Count"]
        rows = [
            ["High (4+ models)", _pct(high["high_avg"]) if high["high_avg"] is not None else "—", str(high["high_count"])],
            ["Low (1–2 models)", _pct(high["low_avg"]) if high["low_avg"] is not None else "—", str(high["low_count"])],
        ]
        parts.append(_format_table(headers, rows, aligns=["L", "R", "R"]))
        parts.append("")

        if high["high_avg"] is not None and high["low_avg"] is not None:
            delta = high["high_avg"] - high["low_avg"]
            if delta > 0.005:
                parts.append(f"**Finding:** High-agreement trades outperform by "
                           f"**{_pct(delta)}** — consensus is a positive signal.")
            elif delta < -0.005:
                parts.append(f"**Finding:** Low-agreement trades outperform by "
                           f"**{_pct(abs(delta))}** — contrarian picks are winning.")
            else:
                parts.append("**Finding:** No meaningful difference between high and low "
                           "agreement trades yet. More data needed.")
    else:
        parts.append("_Insufficient trade data to compute agreement statistics._")

    # Current consensus positions
    current_holdings: dict[str, list[str]] = {}
    for key in model_keys:
        try:
            p = load_portfolio(key)
            for ticker in p.holdings:
                current_holdings.setdefault(ticker, []).append(key)
        except Exception:
            continue

    consensus = {t: models for t, models in current_holdings.items() if len(models) >= 3}
    if consensus:
        parts.append("")
        parts.append("### Current Consensus Positions (3+ models)")
        parts.append("")
        sorted_consensus = sorted(consensus.items(), key=lambda x: -len(x[1]))
        for ticker, models in sorted_consensus:
            model_names = ", ".join(
                settings["models"].get(k, {}).get("display_name", k.upper())
                for k in models
            )
            parts.append(f"- **{ticker}** — held by {len(models)}/{len(model_keys)} models ({model_names})")

    return "\n".join(parts)


# ---- Section 8: Confidence Calibration -----------------------------------

def _section_confidence_calibration(
    model_keys: list[str],
    settings: dict[str, Any],
) -> str:
    """Confidence Calibration — do the models know what they know?"""
    try:
        _, _, calibration = _lazy_trade_analytics(model_keys, settings)
    except Exception as e:
        return f"## 8. Confidence Calibration\n\n_Could not compute: {e}_"

    parts = ["## 8. Confidence Calibration", ""]
    parts.append("_Correlation between a model's self-reported confidence (1–10) and "
                 "actual trade return. +1.0 = perfectly calibrated, 0.0 = random noise, "
                 "negative = overconfident on bad trades._")
    parts.append("")

    headers = ["Model", "Calibration Score", "Trades", "Assessment"]
    rows: list[list[str]] = []
    for key in model_keys:
        cfg = settings["models"].get(key, {})
        label = cfg.get("display_name", key.upper())
        cal = calibration.get(key, {})
        total = cal.get("total_trades", 0)
        score = cal.get("calibration_score")
        min_trades = cal.get("min_trades", 20)

        if total < min_trades:
            rows.append([label, "—", f"{total}/{min_trades}", "Insufficient data"])
        elif score is not None:
            if score > 0.2:
                assessment = "Well calibrated"
            elif score > 0.05:
                assessment = "Weakly calibrated"
            elif score > -0.05:
                assessment = "Random noise"
            elif score > -0.2:
                assessment = "Weakly miscalibrated"
            else:
                assessment = "Miscalibrated (overconfident on bad trades)"
            rows.append([label, f"{score:+.3f}", str(total), assessment])
        else:
            rows.append([label, "—", str(total), "Cannot compute"])

    parts.append(_format_table(headers, rows, aligns=["L", "R", "R", "L"]))

    # Per-model bucket breakdown for models with enough data
    for key in model_keys:
        cfg = settings["models"].get(key, {})
        label = cfg.get("display_name", key.upper())
        cal = calibration.get(key, {})
        total = cal.get("total_trades", 0)
        min_trades = cal.get("min_trades", 20)
        if total < min_trades:
            continue
        buckets = cal.get("buckets", [])
        active = [b for b in buckets if b["count"] > 0]
        if not active:
            continue
        parts.append("")
        parts.append(f"### {label} — Return by Confidence Level")
        parts.append("")
        bheaders = ["Confidence", "Avg Return", "Trades"]
        brows = []
        for b in active:
            brows.append([
                str(b["confidence"]),
                _pct(b["avg_return"]) if b["avg_return"] is not None else "—",
                str(b["count"]),
            ])
        parts.append(_format_table(bheaders, brows, aligns=["C", "R", "R"]))

    return "\n".join(parts)


# ---- Section 9: Screening Analysis ---------------------------------------

def _section_screening_analysis(
    model_keys: list[str],
    month: datetime,
    settings: dict[str, Any],
) -> str:
    """Screening Analysis — what did each model choose to focus on?

    Walks the trade logs for screening_shortlist data logged per decision run.
    Reports: most shortlisted stocks, most ignored stocks, and whether
    filtered-out stocks had gains the model missed.
    """
    start, end = _month_bounds(month)
    parts = ["## 9. Screening Analysis", ""]
    parts.append("_Which stocks did each model consistently shortlist? Which were ignored? "
                 "Did models miss gains by filtering stocks out?_")
    parts.append("")

    # Walk trade logs and collect screening shortlists
    model_shortlists: dict[str, dict[str, int]] = {}  # model -> {ticker: count}
    model_runs: dict[str, int] = {}  # model -> total runs with screening data
    all_tickers: set[str] = set()

    for key in model_keys:
        pattern = re.compile(rf"^{re.escape(key)}_(\d{{4}})-(\d{{2}})\.jsonl$")
        shortlist_counts: dict[str, int] = {}
        runs = 0
        if not TRADES_DIR.exists():
            continue
        for fp in sorted(TRADES_DIR.iterdir()):
            if not fp.is_file():
                continue
            m = pattern.match(fp.name)
            if not m:
                continue
            year, mo = int(m.group(1)), int(m.group(2))
            if not (year == start.year and mo == start.month):
                continue
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    shortlist = rec.get("screening_shortlist")
                    if not shortlist:
                        continue
                    runs += 1
                    for sym in shortlist:
                        shortlist_counts[sym] = shortlist_counts.get(sym, 0) + 1
                        all_tickers.add(sym)

        model_shortlists[key] = shortlist_counts
        model_runs[key] = runs

    if not any(model_runs.values()):
        parts.append("_No screening data available this month (screening was not active or "
                     "no runs were recorded with shortlist data)._")
        return "\n".join(parts)

    # Per-model: top 5 most shortlisted and top 5 most ignored
    for key in model_keys:
        cfg = settings["models"].get(key, {})
        label = cfg.get("display_name", key.upper())
        counts = model_shortlists.get(key, {})
        runs = model_runs.get(key, 0)
        if runs == 0:
            continue

        sorted_picks = sorted(counts.items(), key=lambda x: -x[1])
        top5 = sorted_picks[:5]
        # Stocks in the universe that were NEVER shortlisted
        never = [t for t in all_tickers if t not in counts]

        parts.append(f"### {label} ({runs} screening runs)")
        parts.append("")
        if top5:
            parts.append("**Most shortlisted:** " +
                         ", ".join(f"{t} ({n}/{runs})" for t, n in top5))
        if never:
            parts.append(f"**Never shortlisted:** {', '.join(sorted(never)[:10])}"
                         + (f" (+{len(never)-10} more)" if len(never) > 10 else ""))
        parts.append("")

    return "\n".join(parts)


# ---- Main entry ----------------------------------------------------------

def generate_monthly_report(
    month: datetime | None = None,
    settings: dict[str, Any] | None = None,
) -> Path:
    """Build and write the monthly report. `month` is any datetime in the
    target calendar month — defaults to the current UTC month.

    Returns the path to the written report.
    """
    if settings is None:
        settings = load_settings()
    if month is None:
        month = datetime.now(timezone.utc)

    MONTHLY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target_path = MONTHLY_REPORTS_DIR / f"{month.strftime('%Y-%m')}.md"

    model_keys = [k for k, cfg in settings["models"].items() if cfg.get("enabled", True)]

    sections = [
        _section_header(month, settings),
        "",
        "---",
        "",
        _section_at_a_glance(model_keys, month, settings),
        "",
        "---",
        "",
        _section_performance(model_keys, month, settings),
        "",
        "---",
        "",
        _section_trade_activity(model_keys, month, settings),
        "",
        "---",
        "",
        _section_risk_events(model_keys, month, settings),
        "",
        "---",
        "",
        _section_cohort_comparison(settings, month),
        "",
        "---",
        "",
        _section_cost_analysis(model_keys, month, settings),
        "",
        "---",
        "",
        _section_consensus_analysis(model_keys, settings),
        "",
        "---",
        "",
        _section_confidence_calibration(model_keys, settings),
        "",
        "---",
        "",
        _section_screening_analysis(model_keys, month, settings),
        "",
        "---",
        "",
        f"*LLM Trading Lab — Monthly Report  ·  Phase: {settings.get('phase', '—')}*",
        "",
    ]
    report = "\n".join(sections)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info("Monthly report written: %s", target_path)
    return target_path
