"""Post-hoc check of one session's intraday cycle count against the expectation
registered BEFORE the session (ledger session_timing_calendar_close_and_et_chain,
ratified by the hub 2026-10-24).

A cycle is one 30-minute intraday tick: a :00/:30 slot whose run STARTS before
the day's NYSE close. For every book with a decision log that day it checks:

  * the set of slots that ran equals the expected set exactly (missing and
    extra slots are named, not just counted);
  * no slot at or after the day's close (the 2026-11-27 failure mode of the
    pre-2026-10-24 code: trading after the bell on stale prices);
  * one tick per slot per book (two would be a per-boundary guard breach).

Expected slots come from two independent sources that must agree: the NYSE
calendar (src.data.market_data.session_bounds, the pipeline's own gate) and,
for the registered dates, the count recorded in the ledger before the fact.
If they disagree the check fails -- that is a calendar or registration
problem, and it must not be resolved by trusting whichever number is handy.

Source of a tick: the decision log, data/trades/{book}_{YYYY-MM}.jsonl. The slot
is read from fetch_started_at (the tick's single market-data fetch, UTC), NOT
from `timestamp`, which is written after the model call and drifts by each
model's latency. Records without fetch_started_at are not pipeline ticks (the
2026-10-23 v4 boundary force-cover wrote two such gemini records, 19:11Z and
19:36Z); they are listed, never counted and never silently dropped.

Run:  python scripts/verify_session_cycles.py 2026-11-02
      python scripts/verify_session_cycles.py 2026-11-27
Exit 0 = PASS, 1 = FAIL, 2 = no decision logs for that date.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.market_data import session_bounds  # noqa: E402

ET = ZoneInfo("America/New_York")
TRADES_DIR = ROOT / "data" / "trades"

# Registered 2026-10-24, before any of these sessions; ratified by the hub the
# same day. Mirror of the ledger's expected_cycle_counts -- a change here must
# be a change there first.
REGISTERED_CYCLES = {
    "2026-11-02": 13,   # first EST session the lab has run; validation month day 1
    "2026-11-27": 7,    # 13:00 ET early close: 09:30 .. 12:30, EOD 14:05 ET
    "2026-12-24": 7,    # 13:00 ET early close
}

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("verify_session_cycles")


def expected_slots(day: date) -> tuple[list[str], time] | None:
    """(["09:30", ...], close) for the session on `day`, or None if no session."""
    bounds = session_bounds(datetime.combine(day, time(12, 0), tzinfo=ET))
    if bounds is None:
        return None
    open_t, close_t = bounds
    slots, t = [], datetime.combine(day, open_t)
    while t.time() < close_t:
        slots.append(t.strftime("%H:%M"))
        t += timedelta(minutes=30)
    return slots, close_t


def slot_of(fetch_started_at: str) -> str:
    """ET :00/:30 slot of a UTC fetch timestamp."""
    t = datetime.fromisoformat(fetch_started_at).astimezone(ET)
    return f"{t.hour:02d}:{0 if t.minute < 30 else 30:02d}"


def read_ticks(day: date, trades_dir: Path) -> tuple[dict, dict]:
    """({book: {slot: [fetch_started_at, ...]}}, {book: [non-tick timestamps]})."""
    ticks: dict = defaultdict(lambda: defaultdict(list))
    non_ticks: dict = defaultdict(list)
    day_str = day.isoformat()
    for path in sorted(trades_dir.glob(f"*_{day.strftime('%Y-%m')}.jsonl")):
        book = path.name[: -len(f"_{day.strftime('%Y-%m')}.jsonl")]
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"{path.name}:{lineno} is not valid JSON: {exc}") from exc
                if rec.get("date") != day_str:
                    continue
                fetched = rec.get("fetch_started_at")
                if fetched:
                    ticks[book][slot_of(fetched)].append(fetched)
                else:
                    non_ticks[book].append(rec.get("timestamp", "?"))
    return ticks, non_ticks


def verify(day: date, trades_dir: Path = TRADES_DIR) -> int:
    exp = expected_slots(day)
    if exp is None:
        log.error("%s has no NYSE session; nothing to verify", day)
        return 1
    slots, close_t = exp
    ok = True
    registered = REGISTERED_CYCLES.get(day.isoformat())
    log.info("%s: calendar close %s ET, %d expected cycles (%s .. %s)%s", day,
             close_t.strftime("%H:%M"), len(slots), slots[0], slots[-1],
             f"; registered before the fact: {registered}" if registered is not None else
             "; no registered expectation for this date (calendar only)")
    if registered is not None and registered != len(slots):
        log.error("FAIL: calendar gives %d cycles but %d was registered -- resolve the "
                  "disagreement, do not pick one", len(slots), registered)
        ok = False

    ticks, non_ticks = read_ticks(day, trades_dir)
    if not ticks:
        log.error("no decision-log ticks dated %s under %s", day, trades_dir)
        return 2
    want = set(slots)
    for book in sorted(ticks):
        got = ticks[book]
        missing = sorted(want - set(got))
        extra = sorted(set(got) - want)
        after_close = sorted(s for s in got if s >= close_t.strftime("%H:%M"))
        doubled = {s: v for s, v in sorted(got.items()) if len(v) > 1}
        book_ok = not (missing or extra or after_close or doubled)
        ok &= book_ok
        log.info("%-12s %s  %d/%d cycles%s%s%s%s", book, "PASS" if book_ok else "FAIL",
                 len(got), len(slots),
                 f"  missing={missing}" if missing else "",
                 f"  extra={extra}" if extra else "",
                 f"  AT/AFTER CLOSE={after_close}" if after_close else "",
                 f"  DOUBLED={doubled}" if doubled else "")
    for book, stamps in sorted(non_ticks.items()):
        log.info("%-12s %d non-tick record(s), not counted (no fetch_started_at): %s",
                 book, len(stamps), stamps)
    log.info("OVERALL: %s", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("date", type=date.fromisoformat, help="session date, YYYY-MM-DD")
    parser.add_argument("--trades-dir", type=Path, default=TRADES_DIR)
    args = parser.parse_args()
    return verify(args.date, args.trades_dir)


if __name__ == "__main__":
    sys.exit(main())
