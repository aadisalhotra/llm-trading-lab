"""Chain scheduling decision for intraday.yml's self-chain job — in ET, from the day's close.

After each intraday run the chain job asks this script what to do next:

    python scripts/chain_schedule.py --close-et 16:00
    -> prints "intraday <wait_s>" | "eod <wait_s>" | "stop 0"

and, after sleeping, re-checks that the slot it slept to still starts before
the close:

    python scripts/chain_schedule.py --close-et 16:00 --still-open
    -> prints "true" | "false"

Why this exists (2026-10-24). The chain decided in UTC: "hour >= 20 means the
market has closed". That is only true in EDT. In EST (from 2026-11-02, the
validation month's first day) 20:00 UTC is 15:00 ET, so the chain dispatched
EOD after the 14:30 ET slot, leaving 15:00 to a delayed backup cron and never
running 15:30. And the close itself was a 16:00 constant, wrong on early-close
days (13:00 ET on 2026-11-27). Both are a hardcoded value standing in for
something that varies.

The rule, in slots: the chain dispatches the next 30-minute slot only if that
slot STARTS before the day's close. After the last such slot it dispatches the
EOD run at close + 65 minutes (17:05 ET on a full day, the same 21:05 UTC as
before in EDT; 14:05 ET on an early close).

Stdlib only (zoneinfo), like the rest of the chain job: no pip install runs
there. The close comes from the gate job (scripts/check_trading_day.py
--close), which has the NYSE calendar; an empty or malformed value falls back
to 16:00, a full session. The pipeline's own is_market_open_now() remains the
authority on whether a dispatched tick actually trades.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
FULL_SESSION_CLOSE = time(16, 0)
EOD_DELAY = timedelta(minutes=65)   # close + 65 min: 17:05 ET on a full day
SLOT = timedelta(minutes=30)
DISPATCH_BUFFER = timedelta(seconds=30)  # land after the wall-clock minute rolls


def parse_close(value: str | None) -> time:
    """'HH:MM' -> time; anything empty or malformed -> 16:00 (fail-open, full session)."""
    try:
        hh, mm = (value or "").strip().split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return FULL_SESSION_CLOSE


def next_slot(now_et: datetime) -> datetime:
    """The next :00 / :30 boundary strictly after now, as the old bash computed it:
    a slot less than 60 s away is skipped (it is the one just run)."""
    base = now_et.replace(minute=(now_et.minute // 30) * 30, second=0, microsecond=0)
    slot = base + SLOT
    if slot + DISPATCH_BUFFER - now_et < timedelta(seconds=60):
        slot += SLOT
    return slot


def decide(now_utc: datetime, close: time) -> tuple[str, int]:
    """('intraday' | 'eod' | 'stop', seconds to wait before dispatching)."""
    now_et = now_utc.astimezone(ET)
    if now_et.weekday() >= 5:
        return "stop", 0
    close_dt = now_et.replace(hour=close.hour, minute=close.minute, second=0, microsecond=0)
    slot = next_slot(now_et)
    if slot < close_dt:
        return "intraday", int((slot + DISPATCH_BUFFER - now_et).total_seconds())
    wait = int((close_dt + EOD_DELAY - now_et).total_seconds())
    return "eod", max(wait, 30)


def still_open(now_utc: datetime, close: time) -> bool:
    """After the sleep: does the slot we are about to dispatch start before the close?"""
    now_et = now_utc.astimezone(ET)
    slot_start = now_et - DISPATCH_BUFFER
    return slot_start.time() < close and now_et.weekday() < 5


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--close-et", default="", help="the day's NYSE close, ET 'HH:MM' (default 16:00)")
    ap.add_argument("--still-open", action="store_true", help="post-sleep re-check")
    ap.add_argument("--now", help="ISO timestamp override (tests only)")
    args = ap.parse_args(argv)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    close = parse_close(args.close_et)
    if args.still_open:
        print("true" if still_open(now, close) else "false")
    else:
        action, wait = decide(now, close)
        print(f"{action} {wait}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
