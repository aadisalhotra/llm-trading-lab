"""NYSE session calendar with no heavy imports, for the CI gate (hub H-OPS-41).

Why this module exists (2026-11-27). The chain's gate job
(.github/workflows/intraday.yml `gate`, scripts/check_trading_day.py) installs
only pandas and pandas-market-calendars. check_trading_day.py read the
calendar through src.data.market_data, which imports yfinance at module top,
and importing anything under src.data runs src/data/__init__.py, which imports
market_data too. The import therefore failed on every run and the gate failed
OPEN: "trading=true" on Thanksgiving 2026-11-26, and a 16:00 close on the
2026-11-27 early close. The chain kept dispatching ticks and slot watchers, and
the watcher paged CRITICAL "produced no cycle" for every slot of the holiday
(13) and for 13:00-15:30 after the early close (6). Nothing traded: the
pipeline's own market-open check (src.data.market_data, full dependencies)
skipped every one of those ticks.

This module needs the standard library plus pandas_market_calendars, imported
lazily. It lives outside src.data so that importing it never runs
src/data/__init__.py. Same source as the pipeline: the pandas_market_calendars
NYSE schedule, so holidays and early closes are the exchange's own.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
FULL_OPEN = time(9, 30)
FULL_CLOSE = time(16, 0)


def et_date(reference: datetime | date | None = None) -> date:
    """The America/New_York date of `reference` (a naive datetime is read as
    ET, as the pipeline's run date is); a date is taken as given."""
    if isinstance(reference, date) and not isinstance(reference, datetime):
        return reference
    now = reference or datetime.now(EASTERN)
    now = now.replace(tzinfo=EASTERN) if now.tzinfo is None else now.astimezone(EASTERN)
    return now.date()


def session(reference: datetime | date | None = None) -> tuple[time, time] | None:
    """(open, close) as ET wall-clock times for the NYSE session on the ET date
    of `reference`, or None when that date has no session. Raises ImportError
    if pandas_market_calendars is missing: callers choose their own fallback
    (the gate fails open), so a missing calendar is never mistaken for a
    holiday."""
    import pandas_market_calendars as mcal

    day = et_date(reference)
    sched = mcal.get_calendar("NYSE").schedule(start_date=day, end_date=day)
    if sched.empty:
        return None
    row = sched.iloc[0]
    return (row["market_open"].tz_convert(EASTERN).time(),
            row["market_close"].tz_convert(EASTERN).time())


def is_session_day(reference: datetime | date | None = None) -> bool:
    return session(reference) is not None
