"""Universal prompt builder.

Loads the active prompt template + injects the per-run user context (universe,
market data, current portfolio state). Output is two strings: a system prompt
that's identical across every model, and a user prompt that's identical across
every model EXCEPT for the per-portfolio holdings/cash section.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from .config_loader import PROMPTS_DIR, load_settings, load_universe


def load_prompt_template(version: str) -> str:
    path = PROMPTS_DIR / f"{version}.txt"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _format_universe_block(universe: dict[str, Any]) -> str:
    rows = []
    for t in universe["tickers"]:
        rows.append(f"  {t['symbol']:<6}  {t['sector']:<24}  {t['name']}")
    return "UNIVERSE (these are the only tickers you may trade):\n" + "\n".join(rows)


def _format_market_data_block(market_data: dict[str, pd.DataFrame]) -> str:
    """Compact, neutral OHLCV summary per ticker.

    For each ticker we render: last close, 1d %, 5d %, 30d %, 30d high/low,
    avg volume. Neutral framing — no labels like "strong" or "weak".
    """
    lines = ["MARKET DATA (last close + recent context):"]
    header = f"  {'TICKER':<6} {'CLOSE':>10} {'1D%':>7} {'5D%':>7} {'30D%':>8} {'30D_HI':>10} {'30D_LO':>10} {'AVG_VOL':>14}"
    lines.append(header)
    for ticker, df in market_data.items():
        if df is None or df.empty:
            lines.append(f"  {ticker:<6} {'NO DATA':>10}")
            continue
        close = float(df["Close"].iloc[-1])
        prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else close
        d5 = float(df["Close"].iloc[-6]) if len(df) >= 6 else close
        d30 = float(df["Close"].iloc[0])
        hi30 = float(df["High"].max())
        lo30 = float(df["Low"].min())
        vol = float(df["Volume"].mean())
        pct_1d = (close / prev - 1) * 100 if prev else 0
        pct_5d = (close / d5 - 1) * 100 if d5 else 0
        pct_30d = (close / d30 - 1) * 100 if d30 else 0
        lines.append(
            f"  {ticker:<6} {close:>10.2f} {pct_1d:>+7.2f} {pct_5d:>+7.2f} {pct_30d:>+8.2f} {hi30:>10.2f} {lo30:>10.2f} {vol:>14,.0f}"
        )
    return "\n".join(lines)


def _format_portfolio_block(portfolio_state: dict[str, Any]) -> str:
    lines = ["YOUR CURRENT PORTFOLIO STATE:"]
    lines.append(f"  Total value:   ${portfolio_state['total_value']:,.2f}")
    lines.append(f"  Cash:          ${portfolio_state['cash']:,.2f} ({portfolio_state['cash_pct']*100:.1f}%)")
    lines.append(f"  Open positions: {len(portfolio_state['holdings'])} / 10 max")
    if portfolio_state["holdings"]:
        lines.append("")
        lines.append("  HOLDINGS:")
        lines.append(f"    {'TICKER':<6} {'SHARES':>10} {'AVG_COST':>10} {'CUR_PRICE':>10} {'WEIGHT':>9} {'UNREAL_PL%':>12}")
        for h in portfolio_state["holdings"]:
            lines.append(
                f"    {h['ticker']:<6} {h['shares']:>10.4f} {h['avg_cost']:>10.2f} "
                f"{h['current_price']:>10.2f} {h['weight']*100:>8.2f}% {h['unrealized_pl_pct']*100:>+11.2f}%"
            )
    else:
        lines.append("  (no open positions — 100% cash)")
    return "\n".join(lines)


def build_prompts(
    portfolio_state: dict[str, Any],
    market_data: dict[str, pd.DataFrame],
    run_date: datetime,
) -> tuple[str, str, str]:
    """Build (system_prompt, user_prompt, prompt_version).

    The system prompt is the raw template — identical across all models and runs.
    The user prompt is the per-run context (universe + data + portfolio state).
    """
    settings = load_settings()
    universe = load_universe()
    version = settings["prompt_version"]
    system_prompt = load_prompt_template(version)

    parts = [
        f"DATE: {run_date.strftime('%Y-%m-%d')}",
        f"EXECUTION MODE: {settings['mode'].upper()}",
        f"PHASE: {settings['phase']}",
        "",
        _format_universe_block(universe),
        "",
        _format_market_data_block(market_data),
        "",
        _format_portfolio_block(portfolio_state),
        "",
        "Output your decisions now as a single JSON object conforming to the schema in the system prompt.",
    ]
    user_prompt = "\n".join(parts)

    return system_prompt, user_prompt, version


def hash_inputs(market_data: dict[str, pd.DataFrame]) -> str:
    """Stable hash of the data inputs for reproducibility logging."""
    import hashlib

    payload = {}
    for ticker, df in market_data.items():
        if df is None or df.empty:
            payload[ticker] = None
            continue
        payload[ticker] = {
            "last_close": float(df["Close"].iloc[-1]),
            "rows": int(len(df)),
            "first_date": str(df.index[0].date()),
            "last_date": str(df.index[-1].date()),
        }
    serialized = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
