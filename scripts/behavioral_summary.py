"""Behavioral summary across all models for any date range — the prompt-version
comparison instrument.

Originally built as a one-off May read of the v1 prompt; generalized to take an
arbitrary window so the same code measures any month. The intended use is
v1-vs-v2: run it on the v1 window and the v2 window and let it print a side-by-side
delta scorecard, so we can see whether a prompt change actually moved behavior
(less overtrading, less churn, longer holds, better-calibrated confidence).

Everything is READ-ONLY. It pulls from the production decision + performance logs
through the same loaders the research-metrics module uses (`load_decision_records`,
`_executed_trades`, `_closed_trades`), so the numbers here are consistent with the
paper-track definitions. It writes nothing unless you pass --output.

Metrics, per model, over the window:
  * Trades/day        — executed BUY/SELL (HOLDs excluded) / distinct active days
  * Confidence dist.  — count of executed BUY/SELL trades at each 1-10 value + mean
  * Cash %            — avg/min/max EOD cash from the performance log
  * Holding period    — calendar days entry->exit for trades CLOSED in the window
                        (full history is replayed so cost basis + entry are correct)
  * Position flip rate— share of in-window trades whose side reverses the prior
                        executed trade on that same ticker
  * Win rate x conf.  — closed-trade win rate bucketed by entry confidence, plus
                        the confidence<->outcome correlation (calibration signal)

Closed-trade metrics scope to trades whose EXIT falls in the window, but replay the
model's full history first so a position opened before the window still gets the
right entry date, cost basis, and entry confidence.

Run:
    python -m scripts.may_behavioral_summary --month 2026-05
    python -m scripts.may_behavioral_summary --month 2026-06 --vs 2026-05
    python -m scripts.may_behavioral_summary --month 2026-06 --vs 2026-05 \\
        --label "v2 (June)" --vs-label "v1 (May)" --output reports/v1_vs_v2.md
    python -m scripts.may_behavioral_summary --start 2026-05-01 --end 2026-05-15
    python -m scripts.may_behavioral_summary --month 2026-06 --models claude,gpt
"""
from __future__ import annotations

import argparse
import calendar
import json
import logging
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import NamedTuple

import numpy as np

# Allow running as a script from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analytics.research_metrics import (  # noqa: E402
    load_decision_records,
    _executed_trades,
    _closed_trades,
    get_model_keys,
)
from src.config_loader import (  # noqa: E402
    PERFORMANCE_DIR,
    configure_logging,
    force_utf8_console,
    load_settings,
)

logger = logging.getLogger("llmlab.behavioral_summary")


# ==========================================================================
# Window resolution
# ==========================================================================

class Window(NamedTuple):
    start: str   # inclusive, YYYY-MM-DD
    end: str     # inclusive, YYYY-MM-DD
    label: str


def _month_range(month_str: str) -> tuple[str, str]:
    """'YYYY-MM' -> (first-day, last-day) as YYYY-MM-DD."""
    try:
        y, m = (int(x) for x in month_str.split("-"))
        last = calendar.monthrange(y, m)[1]
    except (ValueError, IndexError) as e:
        raise SystemExit(f"Bad --month '{month_str}', expected YYYY-MM: {e}")
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}"


def make_window(month: str | None, start: str | None, end: str | None,
                label: str | None) -> Window | None:
    """Build a Window from --month OR --start/--end. None if no args given."""
    if month:
        s, e = _month_range(month)
        return Window(s, e, label or month)
    if start or end:
        if not (start and end):
            raise SystemExit("Provide BOTH --start and --end (or use --month).")
        if start > end:
            raise SystemExit(f"--start ({start}) is after --end ({end}).")
        return Window(start, end, label or f"{start} to {end}")
    return None


def in_window(date_str: str, w: Window) -> bool:
    return bool(date_str) and w.start <= date_str <= w.end


# ==========================================================================
# Per-metric helpers
# ==========================================================================

def cash_stats(model_key: str, w: Window):
    """(avg, min, max, n) EOD cash % from the performance log, window rows only."""
    path = PERFORMANCE_DIR / f"{model_key}.jsonl"
    vals: list[float] = []
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if in_window(r.get("date", ""), w) and r.get("api_success", True):
                    cp = r.get("cash_pct")
                    if cp is not None:
                        vals.append(float(cp) * 100.0)
    if not vals:
        return None, None, None, 0
    return statistics.mean(vals), min(vals), max(vals), len(vals)


def flip_rate(records: list[dict], w: Window):
    """In-window direction-reversal rate.

    Replay the full executed BUY/SELL history in chronological order, tracking the
    last side traded per ticker. A 'flip' = an in-window trade whose side differs
    from the previous executed trade on that ticker. Denominator = in-window trades
    that had a known prior trade on that ticker (any date).
    """
    last_side: dict[str, str] = {}
    pairs = 0
    flips = 0
    for rec in records:  # load_decision_records returns chronological order
        d = rec.get("date", "")
        for ex in _executed_trades(rec):
            t = ex["ticker"]
            side = ex["side"]
            if t in last_side and in_window(d, w):
                pairs += 1
                if side != last_side[t]:
                    flips += 1
            last_side[t] = side
    return (flips / pairs if pairs else None), flips, pairs


def conf_distribution(window_records: list[dict]) -> Counter:
    """Counter of decision.confidence over executed BUY/SELL trades in the window."""
    c: Counter = Counter()
    for rec in window_records:
        for ex in _executed_trades(rec):
            conf = (ex.get("decision") or {}).get("confidence")
            if conf is not None:
                c[int(conf)] += 1
    return c


def win_rate(trades: list[dict]):
    """(win rate in [0,1], n) over closed trades."""
    if not trades:
        return None, 0
    return statistics.mean(t["profitable"] for t in trades), len(trades)


def conf_outcome_corr(trades: list[dict]):
    """Point-biserial correlation between entry confidence and profitable outcome."""
    usable = [t for t in trades if t.get("entry_confidence") is not None]
    if len(usable) < 3:
        return None
    c = np.array([t["entry_confidence"] for t in usable], float)
    o = np.array([t["profitable"] for t in usable], float)
    if c.std() == 0 or o.std() == 0:
        return None
    return float(np.corrcoef(c, o)[0, 1])


# ==========================================================================
# Window computation
# ==========================================================================

class WindowResult(NamedTuple):
    window: Window
    rows: dict          # model_key -> metrics dict
    pooled_closed: list  # all closed-in-window trades across models


def compute_window(model_keys: list[str], w: Window) -> WindowResult:
    rows: dict[str, dict] = {}
    pooled_closed: list[dict] = []

    for k in model_keys:
        recs = load_decision_records(k)                       # full history
        window_recs = [r for r in recs if in_window(r.get("date", ""), w)]
        active_days = len({r["date"] for r in window_recs if r.get("date")})

        n_trades = sum(len(_executed_trades(r)) for r in window_recs)
        tpd = (n_trades / active_days) if active_days else None

        cdist = conf_distribution(window_recs)
        conf_total = sum(cdist.values())
        conf_mean = (sum(v * n for v, n in cdist.items()) / conf_total) if conf_total else None

        cavg, cmin, cmax, n_cash = cash_stats(k, w)

        closed = _closed_trades(recs)
        win_closed = [t for t in closed if in_window(t.get("exit_date", ""), w)]
        pooled_closed.extend(win_closed)

        holds: list[int] = []
        for t in win_closed:
            try:
                ed = datetime.strptime(t["entry_date"], "%Y-%m-%d")
                xd = datetime.strptime(t["exit_date"], "%Y-%m-%d")
                holds.append((xd - ed).days)
            except (ValueError, KeyError):
                pass
        hold_mean = statistics.mean(holds) if holds else None
        hold_med = statistics.median(holds) if holds else None
        same_day = sum(1 for h in holds if h == 0)

        fr, n_flips, n_pairs = flip_rate(recs, w)

        conf_closed = [t for t in win_closed if t.get("entry_confidence") is not None]
        low = win_rate([t for t in conf_closed if t["entry_confidence"] <= 6])
        mid = win_rate([t for t in conf_closed if t["entry_confidence"] == 7])
        high = win_rate([t for t in conf_closed if t["entry_confidence"] >= 8])

        rows[k] = dict(
            active_days=active_days, n_trades=n_trades, tpd=tpd,
            cdist=cdist, conf_total=conf_total, conf_mean=conf_mean,
            cavg=cavg, cmin=cmin, cmax=cmax, n_cash=n_cash,
            n_closed=len(win_closed), hold_mean=hold_mean, hold_med=hold_med, same_day=same_day,
            fr=fr, n_flips=n_flips, n_pairs=n_pairs,
            low=low, mid=mid, high=high,
            overall=win_rate(conf_closed), corr=conf_outcome_corr(conf_closed),
        )

    return WindowResult(w, rows, pooled_closed)


# ==========================================================================
# Rendering
# ==========================================================================

def _num(x, nd=1, suf=""):
    return f"{x:.{nd}f}{suf}" if x is not None else "—"


def _wr_cell(t):
    rate, n = t
    return f"{rate * 100:.0f}% ({n})" if n else "—"


def _display(settings: dict, k: str) -> str:
    return settings.get("models", {}).get(k, {}).get("display_name", k)


def render_window(res: WindowResult, settings: dict) -> list[str]:
    w = res.window
    out: list[str] = []
    out.append(f"## Behavioral summary — {w.label}  ({w.start} → {w.end})\n")

    # Table 1 — activity & portfolio
    out.append("### Table 1 — Activity & portfolio\n")
    out.append("| Model | Trades/day | Avg cash % | Min cash % | Max cash % | "
               "Avg hold (cal. days) | Median hold | Same-day exits | Flip rate |")
    out.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for k in res.rows:
        r = res.rows[k]
        fr = f"{r['fr'] * 100:.0f}%" if r["fr"] is not None else "—"
        out.append(
            f"| {_display(settings, k)} | {_num(r['tpd'])} | {_num(r['cavg'])} | "
            f"{_num(r['cmin'])} | {_num(r['cmax'])} | {_num(r['hold_mean'])} | "
            f"{_num(r['hold_med'])} | {r['same_day']}/{r['n_closed']} | {fr} |"
        )
    out.append("")

    # Table 2 — confidence distribution
    out.append("### Table 2 — Confidence-score distribution (executed BUY/SELL trades)\n")
    out.append("| Model | " + " | ".join(str(i) for i in range(1, 11)) + " | n | mean |")
    out.append("|---|" + "--:|" * 10 + "--:|--:|")
    for k in res.rows:
        r = res.rows[k]
        cells = " | ".join(str(r["cdist"].get(i, 0)) for i in range(1, 11))
        out.append(f"| {_display(settings, k)} | {cells} | {r['conf_total']} | {_num(r['conf_mean'], 2)} |")
    out.append("")

    # Table 3 — win rate by confidence bucket
    out.append("### Table 3 — Closed-trade win rate by entry-confidence bucket\n")
    out.append("| Model | Low ≤6 | Mid =7 | High ≥8 | Overall | Closed | Conf↔win corr |")
    out.append("|---|--:|--:|--:|--:|--:|--:|")
    for k in res.rows:
        r = res.rows[k]
        out.append(
            f"| {_display(settings, k)} | {_wr_cell(r['low'])} | {_wr_cell(r['mid'])} | "
            f"{_wr_cell(r['high'])} | {_wr_cell(r['overall'])} | {r['n_closed']} | {_num(r['corr'], 2)} |"
        )
    # pooled
    conf_pooled = [t for t in res.pooled_closed if t.get("entry_confidence") is not None]
    plow = win_rate([t for t in conf_pooled if t["entry_confidence"] <= 6])
    pmid = win_rate([t for t in conf_pooled if t["entry_confidence"] == 7])
    phigh = win_rate([t for t in conf_pooled if t["entry_confidence"] >= 8])
    out.append(
        f"| **POOLED** | {_wr_cell(plow)} | {_wr_cell(pmid)} | {_wr_cell(phigh)} | "
        f"{_wr_cell(win_rate(conf_pooled))} | {len(res.pooled_closed)} | "
        f"{_num(conf_outcome_corr(res.pooled_closed), 2)} |"
    )
    allconf = [t["entry_confidence"] for t in conf_pooled]
    if allconf:
        out.append(f"\n_Closed-trade entry-confidence observed range: {min(allconf)}–{max(allconf)}._")
    out.append("")
    return out


# Scalar accessors for the comparison scorecard (name -> value in display units)
def _scalar(row: dict, name: str):
    if name == "tpd":
        return row["tpd"]
    if name == "flip":
        return row["fr"] * 100 if row["fr"] is not None else None
    if name == "hold":
        return row["hold_mean"]
    if name == "conf":
        return row["conf_mean"]
    if name == "corr":
        return row["corr"]
    if name == "wr":
        rate, n = row["overall"]
        return rate * 100 if n else None
    return None


def _delta_cell(a, b, nd=1, suf=""):
    if a is None or b is None:
        return "—"
    d = a - b
    arrow = "▲" if d > 0 else ("▼" if d < 0 else "·")
    return f"{d:+.{nd}f}{suf} {arrow}"


def render_comparison(primary: WindowResult, baseline: WindowResult, settings: dict) -> list[str]:
    """Δ scorecard = primary − baseline (e.g. v2 − v1), per model + average."""
    out: list[str] = []
    out.append(f"## Δ Scorecard — {primary.window.label} − {baseline.window.label}\n")
    out.append("_Each cell is the change from baseline to primary. v2 design targets: "
               "trades/day ↓, flip rate ↓, avg hold ↑, conf↔win corr ↑ (toward calibration), "
               "win rate ↑. Mean confidence shown without a target (watch for wider scale use)._\n")
    metrics = [
        ("Δ Trades/day", "tpd", 1, ""),
        ("Δ Flip rate", "flip", 0, "%"),
        ("Δ Avg hold (d)", "hold", 1, ""),
        ("Δ Mean conf.", "conf", 2, ""),
        ("Δ Conf↔win", "corr", 2, ""),
        ("Δ Win rate", "wr", 0, "%"),
    ]
    out.append("| Model | " + " | ".join(m[0] for m in metrics) + " |")
    out.append("|---|" + "--:|" * len(metrics))
    # per-model rows + collect for the average row
    sums: dict[str, list[float]] = {m[1]: [] for m in metrics}
    common = [k for k in primary.rows if k in baseline.rows]
    for k in common:
        cells = []
        for _, name, nd, suf in metrics:
            a = _scalar(primary.rows[k], name)
            b = _scalar(baseline.rows[k], name)
            cells.append(_delta_cell(a, b, nd, suf))
            if a is not None and b is not None:
                sums[name].append(a - b)
        out.append(f"| {_display(settings, k)} | " + " | ".join(cells) + " |")
    # average-of-model-deltas row
    avg_cells = []
    for _, name, nd, suf in metrics:
        vals = sums[name]
        if vals:
            d = statistics.mean(vals)
            arrow = "▲" if d > 0 else ("▼" if d < 0 else "·")
            avg_cells.append(f"{d:+.{nd}f}{suf} {arrow}")
        else:
            avg_cells.append("—")
    out.append(f"| **AVG of model Δ** | " + " | ".join(avg_cells) + " |")
    out.append("")
    return out


def definitions_footer() -> list[str]:
    return [
        "---",
        "_Definitions: **Trades** = executed BUY/SELL only (HOLDs excluded). "
        "**Trades/day** divides by distinct active days (days the model logged a run), "
        "so it is comparable across months of different length. **Cash %** is from EOD "
        "performance snapshots. **Holding period** counts calendar days for trades whose "
        "exit falls in the window (full history replayed for correct entry/cost basis; "
        "open positions excluded). **Flip rate** = in-window trades reversing the prior "
        "side on the same ticker ÷ in-window trades with a known prior side. **Win rate** "
        "= realized P&L > 0 over a closed position's full life. **Conf↔win corr** = "
        "point-biserial correlation of entry confidence with profitable outcome (the "
        "calibration signal). Confidence buckets are fixed (≤6 / =7 / ≥8) so they stay "
        "comparable across windows._",
    ]


# ==========================================================================
# Driver
# ==========================================================================

def main() -> int:
    configure_logging()
    force_utf8_console()  # ensure ≤ ↔ ▲ render when stdout is piped/redirected

    parser = argparse.ArgumentParser(
        description="Per-model behavioral summary for a date range; "
                    "optional v1-vs-v2 delta scorecard.")
    # Primary window
    parser.add_argument("--month", help="Primary window as YYYY-MM (whole calendar month)")
    parser.add_argument("--start", help="Primary window start YYYY-MM-DD (use with --end)")
    parser.add_argument("--end", help="Primary window end YYYY-MM-DD (inclusive)")
    parser.add_argument("--label", help="Human label for the primary window (e.g. 'v2 (June)')")
    # Comparison / baseline window
    parser.add_argument("--vs", help="Baseline window as YYYY-MM to compare against")
    parser.add_argument("--vs-start", help="Baseline window start YYYY-MM-DD")
    parser.add_argument("--vs-end", help="Baseline window end YYYY-MM-DD")
    parser.add_argument("--vs-label", help="Human label for the baseline window (e.g. 'v1 (May)')")
    # Misc
    parser.add_argument("--models", help="Comma-separated model keys (default: all enabled)")
    parser.add_argument("--output", help="Also write the full markdown report here")
    args = parser.parse_args()

    settings = load_settings()
    all_keys = get_model_keys(settings)
    if args.models:
        want = [m.strip() for m in args.models.split(",") if m.strip()]
        unknown = [m for m in want if m not in all_keys]
        if unknown:
            logger.warning("Ignoring unknown model keys: %s", ", ".join(unknown))
        model_keys = [m for m in want if m in all_keys]
        if not model_keys:
            raise SystemExit(f"No valid models in --models. Enabled: {', '.join(all_keys)}")
    else:
        model_keys = all_keys

    # Resolve windows. Primary defaults to the current calendar month if unspecified.
    primary_w = make_window(args.month, args.start, args.end, args.label)
    if primary_w is None:
        cur = datetime.now(timezone.utc).strftime("%Y-%m")
        primary_w = make_window(cur, None, None, None)
        logger.info("No window given; defaulting to current month %s", cur)
    baseline_w = make_window(args.vs, args.vs_start, args.vs_end, args.vs_label)

    primary = compute_window(model_keys, primary_w)
    if all(r["n_trades"] == 0 and r["active_days"] == 0 for r in primary.rows.values()):
        logger.warning("No decision records found in primary window %s → %s.",
                       primary_w.start, primary_w.end)

    report: list[str] = [
        f"# LLM Trading Lab — Behavioral Summary",
        f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
        f"{len(model_keys)} models · read-only_\n",
    ]
    if baseline_w is not None:
        baseline = compute_window(model_keys, baseline_w)
        report += render_window(baseline, settings)      # v1 first
        report += render_window(primary, settings)       # v2 next
        report += render_comparison(primary, baseline, settings)  # Δ scorecard
    else:
        report += render_window(primary, settings)
    report += definitions_footer()

    text = "\n".join(report)
    print(text)

    if args.output:
        out_path = args.output
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        logger.info("Wrote report to %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
