"""Session timing: the day's real close (NYSE calendar) and the chain's ET slot rule.

Two hardcoded values stood in for things that vary (fixed 2026-10-24):
  * the close was a 16:00 constant -- wrong on early closes (13:00 ET on
    2026-11-27 and 2026-12-24), where ~6 cycles would have run on stale prices;
  * the chain decided "hour >= 20 UTC means closed" -- true only in EDT. In EST
    (from 2026-11-02, the validation month's first day) it dispatched EOD after
    the 14:30 ET slot, so 15:00 depended on a delayed backup cron and 15:30
    never ran.
The rule now: dispatch the next 30-minute slot only if it STARTS before the
day's close; then EOD at close + 65 min.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from scripts import chain_schedule as cs
from src.data.market_data import is_market_open_now, session_bounds

ET = ZoneInfo("America/New_York")


def et(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET)


# ---------------------------------------------------------------- the calendar

@pytest.mark.parametrize("when, bounds", [
    ("2026-10-23 12:00:00", (time(9, 30), time(16, 0))),   # full session, EDT
    ("2026-11-02 12:00:00", (time(9, 30), time(16, 0))),   # full session, EST
    ("2026-11-27 12:00:00", (time(9, 30), time(13, 0))),   # day after Thanksgiving
    ("2026-12-24 12:00:00", (time(9, 30), time(13, 0))),   # Christmas Eve
    ("2026-11-26 12:00:00", None),                         # Thanksgiving
    ("2026-10-24 12:00:00", None),                         # Saturday
])
def test_session_bounds_come_from_the_calendar(when, bounds):
    pytest.importorskip("pandas_market_calendars")
    assert session_bounds(et(when)) == bounds


@pytest.mark.parametrize("when, is_open", [
    ("2026-10-23 15:59:59", True),
    ("2026-10-23 16:00:00", False),
    ("2026-11-27 12:59:59", True),
    ("2026-11-27 13:00:00", False),   # was True under the 16:00 constant
    ("2026-11-27 15:30:00", False),   # was True: a stale-price cycle
    ("2026-11-26 11:00:00", False),
])
def test_market_open_gate_honours_early_close(when, is_open):
    pytest.importorskip("pandas_market_calendars")
    assert is_market_open_now(et(when)) is is_open


# ---------------------------------------------------------------- the chain

def decide(when_et: str, close: str = "16:00"):
    return cs.decide(et(when_et).astimezone(timezone.utc), cs.parse_close(close))


def test_edt_full_day_last_slot_is_1530_then_eod_at_1705():
    assert decide("2026-10-23 15:05:20") == ("intraday", 25 * 60 - 20 + 30)   # -> 15:30:30
    action, wait = decide("2026-10-23 15:35:16")                             # after the 15:30 slot
    assert action == "eod"
    assert wait == int((et("2026-10-23 17:05:00") - et("2026-10-23 15:35:16")).total_seconds())


def test_est_full_day_keeps_the_1500_and_1530_slots():
    # 2026-11-02 is EST. Under the old UTC rule, the 14:30 run (finishing ~19:35Z)
    # chained to EOD; now it chains to 15:00, and 15:00 chains to 15:30.
    assert decide("2026-11-02 14:35:10")[0] == "intraday"
    assert decide("2026-11-02 15:05:10")[0] == "intraday"
    action, wait = decide("2026-11-02 15:35:10")
    assert action == "eod"
    # close + 65 min = 17:05 EST = 22:05 UTC
    assert (et("2026-11-02 15:35:10").astimezone(timezone.utc).timestamp() + wait
            == datetime(2026, 11, 2, 22, 5, tzinfo=timezone.utc).timestamp())


def test_early_close_last_slot_is_1230_then_eod_at_1405():
    assert decide("2026-11-27 12:05:00", "13:00")[0] == "intraday"   # -> 12:30
    action, wait = decide("2026-11-27 12:35:00", "13:00")
    assert action == "eod"
    assert wait == 90 * 60   # 12:35 -> 14:05


def test_missing_or_malformed_close_fails_open_to_a_full_session():
    for bad in ("", "garbage", None, "25"):
        assert cs.parse_close(bad) == time(16, 0)


def test_weekend_stops():
    assert decide("2026-10-24 10:00:00") == ("stop", 0)


def test_a_slot_under_60s_away_is_skipped_as_before():
    # Old bash: WAIT < 60 -> WAIT += 1800. A run finishing at 15:29:40 must not
    # re-dispatch the 15:30 slot it is about to collide with.
    action, wait = decide("2026-10-23 15:29:40")
    assert action == "eod"   # next slot would be 16:00, which is the close


def test_still_open_after_the_sleep():
    utc = lambda s: et(s).astimezone(timezone.utc)
    assert cs.still_open(utc("2026-11-02 15:30:30"), time(16, 0)) is True
    assert cs.still_open(utc("2026-11-27 13:00:30"), time(13, 0)) is False
    assert cs.still_open(utc("2026-11-27 12:30:30"), time(13, 0)) is True


def test_cli_contract():
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cs.main(["--close-et", "13:00", "--now", "2026-11-27T17:35:00+00:00"])  # 12:35 EST
    assert buf.getvalue().split() == ["eod", str(90 * 60)]


# ---------------------------------------------------------------- whole sessions

def _calendar_slot_count(day: str) -> int:
    """Slots that START before the day's close: 09:30, 10:00, ... < close."""
    o, c = session_bounds(et(f"{day} 12:00:00"))
    n, minutes = 0, o.hour * 60 + o.minute
    while minutes < c.hour * 60 + c.minute:
        n, minutes = n + 1, minutes + 30
    return n


def _simulate_chain(day: str, close: str, run_minutes: int = 5) -> list[str]:
    """Walk the chain from the 09:30 run to its EOD dispatch; return the slots it ran."""
    slots, t = ["09:30"], et(f"{day} 09:30:30")
    while True:
        done = t.astimezone(timezone.utc) + __import__("datetime").timedelta(minutes=run_minutes)
        action, wait = cs.decide(done, cs.parse_close(close))
        if action != "intraday":
            return slots
        t = done + __import__("datetime").timedelta(seconds=wait)
        assert cs.still_open(t, cs.parse_close(close))
        slots.append(t.astimezone(ET).strftime("%H:%M"))


@pytest.mark.parametrize("day, expected", [
    ("2026-10-23", 13),   # EDT full session
    ("2026-11-02", 13),   # EST full session: the validation month's first day
    ("2026-11-27", 7),    # 13:00 early close, inside the validation month
    ("2026-12-24", 7),    # 13:00 early close, live period
])
def test_chain_dispatches_exactly_the_calendar_slots(day, expected):
    pytest.importorskip("pandas_market_calendars")
    close = session_bounds(et(f"{day} 12:00:00"))[1].strftime("%H:%M")
    assert _calendar_slot_count(day) == expected
    ran = _simulate_chain(day, close)
    assert len(ran) == expected, ran
    assert ran[-1] == {13: "15:30", 7: "12:30"}[expected]
