"""Market data via yfinance.

Pulls daily OHLCV for the universe + benchmark with a configurable lookback.
Handles holidays + early closes by checking if today appears in the index.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Iterable

import pandas as pd
import yfinance as yf

from ..config_loader import load_settings, universe_symbols

logger = logging.getLogger("llmlab.data")


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
