"""Market data via yfinance.

Pulls daily OHLCV for the universe + benchmark with a configurable lookback,
plus intraday 15-minute bars for the live trading session.
Handles holidays + early closes by checking if today appears in the index.
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dtime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from ..config_loader import load_settings, universe_symbols

logger = logging.getLogger("llmlab.data")

EASTERN = ZoneInfo("America/New_York")
NYSE_OPEN = dtime(9, 30)
NYSE_CLOSE = dtime(16, 0)


def fetch_universe_data(
    symbols: Iterable[str] | None = None,
    lookback_days: int | None = None,
) -> dict[str, pd.DataFrame]:
    """Fetch daily OHLCV per symbol.

    Returns {symbol: DataFrame} where the DataFrame has at least Open/High/Low/Close/Volume
    indexed by date. Empty DataFrame if a symbol failed.
    """
    settings = load_settings()
    if symbols is None:
        symbols = universe_symbols()
    if lookback_days is None:
        lookback_days = int(settings["data"]["lookback_days"])

    end = datetime.utcnow()
    # pad lookback to compensate for weekends/holidays
    start = end - timedelta(days=lookback_days * 2 + 5)

    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = yf.download(
                sym,
                start=start.strftime("%Y-%m-%d"),
                end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                progress=False,
                auto_adjust=False,
            )
            if df is None or df.empty:
                logger.warning("No data returned for %s", sym)
                out[sym] = pd.DataFrame()
                continue
            # Flatten potential MultiIndex columns from yfinance
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            # Trim to last `lookback_days` rows
            df = df.tail(lookback_days)
            out[sym] = df
        except Exception as e:
            logger.exception("Failed to fetch %s: %s", sym, e)
            out[sym] = pd.DataFrame()
    return out


INDEX_SYMBOLS = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq Composite",
    "^DJI":  "Dow Jones Industrial",
}


def fetch_index_data(lookback_days: int = 5) -> dict[str, pd.DataFrame]:
    """Fetch the three headline U.S. index series for the daily report."""
    return fetch_universe_data(symbols=list(INDEX_SYMBOLS.keys()), lookback_days=lookback_days)


def get_latest_price(symbol: str, data: dict[str, pd.DataFrame] | None = None) -> float | None:
    """Latest close for a symbol. If `data` is given, reuses cached frames."""
    if data and symbol in data and not data[symbol].empty:
        return float(data[symbol]["Close"].iloc[-1])
    try:
        df = yf.download(symbol, period="5d", progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df is None or df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception as e:
        logger.exception("Failed to fetch latest price for %s: %s", symbol, e)
        return None


def is_market_open_today(reference: datetime | None = None) -> bool:
    """True if today is a US trading day (NYSE)."""
    try:
        import pandas_market_calendars as mcal
    except ImportError:
        # Fallback: weekday check only (treats holidays as open — acceptable for build phase)
        today = (reference or datetime.utcnow()).date()
        return today.weekday() < 5

    today = (reference or datetime.utcnow()).date()
    nyse = mcal.get_calendar("NYSE")
    sched = nyse.schedule(start_date=today, end_date=today)
    return not sched.empty


def is_market_open_now(reference: datetime | None = None) -> bool:
    """True if NYSE is open RIGHT NOW (intraday-aware).

    Combines a holiday-aware day check with an ET time-of-day window of
    9:30–16:00. Used by the intraday cron to skip silently outside hours
    even when the cron itself is firing every 15 minutes UTC.
    """
    now = reference or datetime.now(EASTERN)
    if now.tzinfo is None:
        now = now.replace(tzinfo=EASTERN)
    else:
        now = now.astimezone(EASTERN)

    if not is_market_open_today(now):
        return False

    t = now.timetz().replace(tzinfo=None)
    return NYSE_OPEN <= t <= NYSE_CLOSE


def fetch_intraday_data(
    symbols: Iterable[str] | None = None,
    interval: str = "15m",
) -> dict[str, pd.DataFrame]:
    """Fetch intraday OHLCV bars for the universe.

    Returns the same {symbol: DataFrame} shape as `fetch_universe_data` so
    downstream consumers (prompt builder, executor, dashboard) don't need
    to branch on intraday vs daily — they always read the latest Close
    from the last row.

    Default interval is 15m to match the cron cadence. yfinance allows
    1m/2m/5m/15m/30m/60m/1h on a 7-day window for intraday data.
    """
    if symbols is None:
        symbols = universe_symbols()

    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = yf.download(
                sym,
                period="1d",
                interval=interval,
                progress=False,
                auto_adjust=False,
                prepost=False,
            )
            if df is None or df.empty:
                logger.warning("No intraday data returned for %s", sym)
                out[sym] = pd.DataFrame()
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            out[sym] = df
        except Exception as e:
            logger.exception("Failed to fetch intraday %s: %s", sym, e)
            out[sym] = pd.DataFrame()
    return out
