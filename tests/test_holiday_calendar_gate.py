"""Chain gate and slot watch on the NYSE calendar: holidays and early closes (hub H-OPS-41).

2026-11-26 (Thanksgiving): the gate job failed open because check_trading_day.py
imported src.data.market_data (yfinance) in a job that installs only pandas and
pandas-market-calendars. The chain dispatched every slot and the watcher paged
CRITICAL for 13 slots; on the 2026-11-27 early close it paged for 13:00-15:30.
Nothing traded. These tests pin the four dates the hub named.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_slot_cycle as watch  # noqa: E402
import chain_schedule  # noqa: E402
from src import nyse_calendar  # noqa: E402

# The gate job's environment: pandas and pandas_market_calendars only. Every
# other third-party module (yfinance, dotenv, requests, ...) is refused, and
# so is src.data, whose __init__ imports yfinance.
GATE_RUNNER = r'''
import builtins, runpy, sys
real = builtins.__import__
OK = {"pandas", "pandas_market_calendars", "numpy", "pytz", "dateutil", "exchange_calendars",
      "tzdata", "six", "korean_lunar_calendar", "pyluach", "toolz", "src", "scripts"}
def imp(name, globals=None, locals=None, fromlist=(), level=0):
    top = name.split(".")[0]
    if level == 0 and (name.startswith("src.data") or (top not in OK and top not in sys.stdlib_module_names)):
        raise ImportError(f"refused in the gate environment: {name}")
    return real(name, globals, locals, fromlist, level)
builtins.__import__ = imp
sys.argv = ["scripts/check_trading_day.py"] + sys.argv[1:]
runpy.run_path("scripts/check_trading_day.py", run_name="__main__")
'''

DATES = {  # ET date: (trading, close)
    "2026-11-26": ("false", "16:00"),   # Thanksgiving: no session (close unused)
    "2026-11-27": ("true", "13:00"),    # early close
    "2026-12-24": ("true", "13:00"),    # early close
    "2026-12-25": ("false", "16:00"),   # Christmas
    "2026-11-30": ("true", "16:00"),    # an ordinary full session
}


def _gate(*args: str) -> str:
    p = subprocess.run([sys.executable, "-c", GATE_RUNNER, *args], cwd=ROOT,
                       capture_output=True, text=True, timeout=120)
    return p.stdout.strip().splitlines()[-1]


@pytest.mark.parametrize("day", sorted(DATES))
def test_the_gate_answers_from_the_calendar_in_its_own_environment(day):
    trading, close = DATES[day]
    assert _gate("--date", day) == trading
    assert _gate("--close", "--date", day) == close


def test_the_gate_still_fails_open_without_the_calendar(tmp_path):
    runner = GATE_RUNNER.replace('"pandas_market_calendars", ', "")
    p = subprocess.run([sys.executable, "-c", runner, "--date", "2026-11-26"], cwd=ROOT,
                       capture_output=True, text=True, timeout=120)
    assert p.stdout.strip().splitlines()[-1] == "true"
    assert "failing OPEN" in p.stderr


def test_the_light_calendar_agrees_with_the_pipeline_s_calendar():
    """One source: every day from 2026-11-01 to 2027-12-31."""
    from src.data.market_data import session_bounds
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    d = date(2026, 11, 1)
    while d <= date(2027, 12, 31):
        noon = datetime(d.year, d.month, d.day, 12, tzinfo=et)
        assert nyse_calendar.session(noon) == session_bounds(noon), d
        d += timedelta(days=1)


# ------------------------------------------------------------ the slot watch

def _slot(day: str, hhmm: str) -> datetime:
    from zoneinfo import ZoneInfo
    t = datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("America/New_York"))
    return t.astimezone(timezone.utc)


@pytest.mark.parametrize("day,close", [("2026-11-27", "13:00"), ("2026-12-24", "13:00")])
def test_after_an_early_close_no_slot_is_watched(day, close):
    assert watch.outside_session(_slot(day, "12:30"), close) is None
    for hhmm in ("13:00", "13:30", "14:00", "14:30", "15:00", "15:30"):
        assert watch.outside_session(_slot(day, hhmm), close)


@pytest.mark.parametrize("day", ["2026-11-26", "2026-12-25"])
def test_on_a_holiday_no_slot_is_watched(day):
    for hhmm in ("09:30", "10:00", "15:30"):
        assert watch.outside_session(_slot(day, hhmm), "closed")


def test_without_a_close_the_watch_behaves_as_before():
    assert watch.outside_session(_slot("2026-11-27", "15:30"), "") is None


def test_a_skipped_slot_sends_nothing(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no email may be sent for a slot outside the session")
    monkeypatch.setattr(watch, "email_sender", boom)
    monkeypatch.setattr(watch, "watch", boom)
    slot = _slot("2026-11-27", "13:00").strftime("%Y-%m-%dT%H:%M:00Z")
    assert watch.main(["--slot", slot, "--close-et", "13:00"]) == 0
    assert watch.main(["--slot", _slot("2026-11-26", "10:00").strftime("%Y-%m-%dT%H:%M:00Z"),
                       "--close-et", "closed"]) == 0


def test_a_malformed_close_is_refused():
    assert watch.main(["--slot", "2026-11-27T18:00:00Z", "--close-et", "1pm"]) == 2


# -------------------------------------------------------------- the chain

@pytest.mark.parametrize("day", ["2026-11-27", "2026-12-24"])
def test_the_chain_stops_at_the_early_close_and_dispatches_eod_at_1405(day):
    close = chain_schedule.parse_close("13:00")
    at_1235 = _slot(day, "12:35")
    # After the sleep, a 13:00 slot would not be dispatched; the 12:30 one was.
    assert chain_schedule.still_open(_slot(day, "13:00") + chain_schedule.DISPATCH_BUFFER, close) is False
    assert chain_schedule.still_open(_slot(day, "12:30") + chain_schedule.DISPATCH_BUFFER, close) is True
    action, wait = chain_schedule.decide(at_1235, close)
    assert action == "eod"
    assert (at_1235 + timedelta(seconds=wait)).astimezone(nyse_calendar.EASTERN).strftime("%H:%M") == "14:05"


def test_the_chain_passes_the_close_to_the_watch():
    wf = (ROOT / ".github" / "workflows" / "intraday.yml").read_text(encoding="utf-8")
    assert '\\"close_et\\":\\"${CLOSE_ET}\\"' in wf
    sw = (ROOT / ".github" / "workflows" / "slot_watch.yml").read_text(encoding="utf-8")
    assert 'CLOSE_ET: ${{ github.event.inputs.close_et }}' in sw
    assert '--close-et "$CLOSE_ET"' in sw


def test_the_gate_imports_nothing_from_src_data():
    src = (ROOT / "scripts" / "check_trading_day.py").read_text(encoding="utf-8")
    assert "src.data" not in src.split('"""', 2)[2]
