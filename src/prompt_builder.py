"""Universal prompt builder.

Loads the active prompt template + injects the per-run user context (universe,
market data, current portfolio state, intraday session context). Output is:
  - system_prompt: identical across every model
  - user_prompt: per-run text context (universe + data + portfolio)
  - prompt_version: the template version tag
  - images: optional list of PNG bytes (one composite universe chart) for
    vision-capable adapters; text-only adapters ignore the list

Why an intraday context block?
  Without it, the model sees only "DATE: 2026-04-09" and treats every 15-min
  call as if it were the only call of the day. It would happily blow its
  entire 30-trade budget on the first run. The intraday block tells the model
  the current ET clock, how many trades/runs it's already used, and how much
  trading session remains — so it paces itself.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .charts import build_universe_overview_png
from .config_loader import PROMPTS_DIR, load_settings, load_universe
from .data.sentiment import sentiment_label

logger = logging.getLogger("llmlab.prompt")

EASTERN = ZoneInfo("America/New_York")
NYSE_OPEN = dtime(9, 30)
NYSE_CLOSE = dtime(16, 0)


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


def _format_news_block(
    news: dict[str, Any] | None,
    sentiment: dict[str, float] | None,
    universe_symbols: list[str],
) -> str:
    """Per-stock sentiment + top headlines, ready to drop into the user prompt.

    Layout: one stanza per ticker that has at least one headline. Sentiment
    appears as both the raw -1..+1 score and a categorical label so models
    that aren't great at calibrating numerical signals still get the gist.
    """
    if not news:
        return "CURRENT EVENTS & NEWS:\n  (no news data available — pipeline running on technicals only)"

    sentiment = sentiment or {}
    lines = ["CURRENT EVENTS & NEWS (per-stock sentiment + top headlines):"]
    rendered_any = False
    for sym in universe_symbols:
        items = news.get(sym) or []
        if not items:
            continue
        rendered_any = True
        score = float(sentiment.get(sym, 0.0))
        label = sentiment_label(score)
        lines.append(f"  {sym}  [sentiment: {score:+.2f} ({label})]")
        for item in items:
            ts = (item.get("datetime") or "")[:16].replace("T", " ")
            src = item.get("source", "?")
            title = item.get("title", "")
            lines.append(f"    - [{ts}] {src}: {title}")
    if not rendered_any:
        lines.append("  (no per-stock headlines for any universe symbol this run)")
    return "\n".join(lines)


def _format_macro_block(news: dict[str, Any] | None, sentiment: dict[str, float] | None) -> str:
    """Top market-wide headlines that aren't stock-specific."""
    if not news:
        return ""
    macro = news.get("macro") or []
    if not macro:
        return "MARKET-WIDE CONTEXT:\n  (no macro headlines available this run)"
    sentiment = sentiment or {}
    macro_score = float(sentiment.get("macro", 0.0))
    label = sentiment_label(macro_score)
    lines = [f"MARKET-WIDE CONTEXT  [aggregate sentiment: {macro_score:+.2f} ({label})]:"]
    for item in macro:
        ts = (item.get("datetime") or "")[:16].replace("T", " ")
        src = item.get("source", "?")
        title = item.get("title", "")
        lines.append(f"  - [{ts}] {src}: {title}")
    return "\n".join(lines)


def _format_intraday_context_block(
    run_timestamp: datetime,
    trades_executed_today: int,
    runs_today: int,
    max_trades_per_day: int,
    is_eod: bool = False,
) -> str:
    """Tell the model where it is in the trading session.

    The whole point of this block is to make the model pace its 30-trade
    budget across the ~26 intraday calls of the day instead of dumping it
    all on the first call.
    """
    et = run_timestamp.astimezone(EASTERN) if run_timestamp.tzinfo else run_timestamp.replace(tzinfo=EASTERN)
    et_str = et.strftime("%H:%M ET")
    close_str = "16:00 ET"

    # Estimate runs remaining: number of 15-min slots between now and 16:00 ET
    if is_eod:
        runs_remaining = 0
        session_label = "END-OF-DAY WRAP-UP RUN"
    else:
        now_t = et.time()
        if now_t >= NYSE_CLOSE:
            runs_remaining = 0
        elif now_t < NYSE_OPEN:
            # 6.5 hours of session = 26 fifteen-min slots
            runs_remaining = 26
        else:
            minutes_left = (NYSE_CLOSE.hour * 60 + NYSE_CLOSE.minute) - (now_t.hour * 60 + now_t.minute)
            runs_remaining = max(0, minutes_left // 15)
        session_label = "INTRADAY RUN"

    trades_remaining = max(0, max_trades_per_day - trades_executed_today)

    lines = [
        f"INTRADAY SESSION CONTEXT ({session_label}):",
        f"  Current time:        {et_str}",
        f"  Market close:        {close_str}",
        f"  Run number today:    {runs_today + 1}",
        f"  Approx runs left:    {runs_remaining}",
        f"  Trades used today:   {trades_executed_today} / {max_trades_per_day}",
        f"  Trades remaining:    {trades_remaining}",
        "",
        "  IMPORTANT: This is one of many ~15-minute intraday calls today.",
        "  Pace your 30-trade daily budget across the remaining runs — do",
        "  not exhaust it on a single call. HOLD is a valid action when",
        "  there is no clear edge. New information will arrive each tick.",
    ]
    if is_eod:
        lines.append("  This is the END-OF-DAY pass — final positioning for the close.")
    return "\n".join(lines)


def build_prompts(
    portfolio_state: dict[str, Any],
    market_data: dict[str, pd.DataFrame],
    run_date: datetime,
    trades_executed_today: int = 0,
    runs_today: int = 0,
    is_eod: bool = False,
    include_chart_image: bool = True,
    news_data: dict[str, Any] | None = None,
    sentiment_data: dict[str, float] | None = None,
) -> tuple[str, str, str, list[bytes]]:
    """Build (system_prompt, user_prompt, prompt_version, images).

    The system prompt is the raw template — identical across all models and runs.
    The user prompt is the per-run text context (universe, market data, portfolio,
    news headlines + sentiment).
    The images list contains a single composite candlestick PNG of the universe
    when `include_chart_image` is True (vision-capable adapters use it).
    """
    settings = load_settings()
    universe = load_universe()
    version = settings["prompt_version"]
    system_prompt = load_prompt_template(version)
    max_trades = int(settings["portfolio_rules"]["max_trades_per_day"])
    universe_syms = [t["symbol"] for t in universe["tickers"]]

    parts = [
        f"DATE: {run_date.strftime('%Y-%m-%d')}",
        f"EXECUTION MODE: {settings['mode'].upper()}",
        f"PHASE: {settings['phase']}",
        "",
        _format_intraday_context_block(
            run_timestamp=run_date,
            trades_executed_today=trades_executed_today,
            runs_today=runs_today,
            max_trades_per_day=max_trades,
            is_eod=is_eod,
        ),
        "",
        _format_universe_block(universe),
        "",
        _format_market_data_block(market_data),
        "",
        _format_portfolio_block(portfolio_state),
        "",
        _format_news_block(news_data, sentiment_data, universe_syms),
        "",
        _format_macro_block(news_data, sentiment_data),
        "",
        (
            "Consider the news context alongside technical data. Headlines may signal "
            "fundamental changes that technicals have not yet fully priced in. "
            "A composite candlestick chart of the universe is attached above for "
            "vision-capable models. Each panel shows the last 30 trading bars with "
            "a 20-period SMA overlay."
        ),
        "",
        "Output your decisions now as a single JSON object conforming to the schema in the system prompt. Every decision must include a one-sentence `summary` field.",
    ]
    user_prompt = "\n".join(parts)

    images: list[bytes] = []
    if include_chart_image:
        try:
            png = build_universe_overview_png(
                market_data,
                title="UNIVERSE OVERVIEW",
                subtitle=run_date.strftime("%Y-%m-%d %H:%M ET"),
            )
            images.append(png)
        except Exception as e:
            logger.exception("Failed to build universe overview chart: %s", e)
            # Non-fatal — adapters will just not see an image this run

    return system_prompt, user_prompt, version, images


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
            "first_date": str(df.index[0].date()) if hasattr(df.index[0], "date") else str(df.index[0])[:10],
            "last_date": str(df.index[-1].date()) if hasattr(df.index[-1], "date") else str(df.index[-1])[:10],
        }
    serialized = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
