"""Missed-cycle detection: did a 30-minute intraday slot produce a cycle?

STDLIB ONLY. The real-time watcher (scripts/check_slot_cycle.py, run by
.github/workflows/slot_watch.yml) installs nothing, the same as the other
alerting entry points (tests/test_stdlib_only_entry_points.py). Nothing here may
import pandas or anything under src.data.

Why (2026-11-16). The 15:30 ET slot produced no cycle on 2026-11-11 and again on
2026-11-16, and the 14:00 and 10:00 slots were missed on 2026-11-03 and
2026-11-05. Nothing alerted on any of them. The chain's dispatch check only
confirms that a run was CREATED. On 11-16 the run was created on time, then
GitHub held it in the concurrency queue with no jobs for 30 minutes, past the
close, and it finished green. "A run exists" is not "a cycle happened".

What counts as a cycle (same definition as scripts/verify_session_cycles.py).
A slot produced a cycle when at least one book wrote a decision record whose
`fetch_started_at` (the tick's single market-data fetch, UTC) falls inside the
slot: [slot start, slot start + 30 min). `timestamp` is not used, because it is
written after the model call and drifts by each model's latency. Records without
`fetch_started_at` are not pipeline ticks; they are skipped here and never
counted.

A record carries a failed model call too (api_success false), so a slot whose
every call failed still counts as a cycle. That is completeness, which the
existing per-model completeness alert covers. This module answers the earlier
question: did the cycle run at all.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
SLOT = timedelta(minutes=30)
SESSION_OPEN = time(9, 30)

# Event kinds written to data/cycle_completeness/{YYYY-MM}.jsonl.
MISSED_CYCLE = "MISSED_CYCLE"      # no book wrote a record for the slot
PARTIAL_CYCLE = "PARTIAL_CYCLE"    # some books did, some did not
# A slot with no record that an admission guard WITHHELD is not a generic miss.
# It is recorded once, under the admission record's own class (e.g.
# TICK_WITHHELD_GUARD_UNREADABLE), never as MISSED_CYCLE
# (hub ruling 2026-11-17, single classification of a withheld tick). Only the
# cohort-wide TICK_WITHHELD_* classes qualify: a per-book withhold leaves the
# other books' records, so its slot is never a MISSED_CYCLE.
WITHHELD_PREFIX = "TICK_WITHHELD_"


def admission_log_name(slot_utc: datetime) -> str:
    """The admission file a slot's withhold lands in. src/logging/tick_admission
    keys it on the UTC month of the withheld tick; a session never crosses a
    UTC month boundary (the latest close is 21:00 UTC)."""
    return f"{slot_utc.astimezone(timezone.utc).strftime('%Y-%m')}.jsonl"


def read_admission_log(admission_dir: Path, slot_utc: datetime) -> list[str]:
    """Raw lines of the slot's admission file. A missing file reads as no lines."""
    p = admission_dir / admission_log_name(slot_utc)
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def withhold_in_slot(admission_lines: Iterable[str], slot_utc: datetime) -> dict | None:
    """The first TICK_WITHHELD_* admission record whose timestamp (the withheld
    run's start) falls inside the slot, or None. A malformed line is skipped,
    never fatal, as in books_in_slot."""
    lo, hi = slot_utc, slot_utc + SLOT
    for i, line in enumerate(admission_lines):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("cycle_watch: admission line %d is not JSON; skipped", i + 1)
            continue
        if not isinstance(rec, dict) or not str(rec.get("event", "")).startswith(WITHHELD_PREFIX):
            continue
        try:
            t = parse_utc(str(rec.get("timestamp", "")))
        except ValueError:
            logger.warning("cycle_watch: admission line %d has an unparseable timestamp", i + 1)
            continue
        if lo <= t < hi:
            return rec
    return None


def parse_utc(value: str) -> datetime:
    """ISO timestamp -> aware UTC datetime. A naive value is read as UTC."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def slot_start_containing(moment_utc: datetime) -> datetime:
    """The :00 / :30 boundary at or before `moment_utc`, in UTC.

    ET and UTC differ by whole hours, so a half-hour floor in UTC is the same
    instant as a half-hour floor in ET."""
    m = moment_utc.astimezone(timezone.utc)
    return m.replace(minute=(m.minute // 30) * 30, second=0, microsecond=0)


def slot_label(slot_utc: datetime) -> str:
    """'HH:MM' in ET — the name the hub uses for a slot."""
    return slot_utc.astimezone(ET).strftime("%H:%M")


def expected_slots(session_date: date, close_et: time) -> list[datetime]:
    """Every slot that STARTS before the close, from the 09:30 open, as UTC
    datetimes. The same rule as scripts/chain_schedule.py: a slot runs only if
    it starts before the day's close (13 on a full day, 7 on a 13:00 close)."""
    start = datetime.combine(session_date, SESSION_OPEN, tzinfo=ET)
    close = datetime.combine(session_date, close_et, tzinfo=ET)
    slots: list[datetime] = []
    s = start
    while s < close:
        slots.append(s.astimezone(timezone.utc))
        s += SLOT
    return slots


def books_in_slot(lines_by_book: Mapping[str, Iterable[str]], slot_utc: datetime) -> dict[str, int]:
    """Per book, how many decision records fall inside the slot.

    `lines_by_book` maps a book key to the raw JSONL lines of its month log. A
    malformed line is logged and skipped, never fatal: one corrupt line must
    not turn into a false "no cycle"."""
    lo, hi = slot_utc, slot_utc + SLOT
    counts: dict[str, int] = {}
    for book, lines in lines_by_book.items():
        n = 0
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("cycle_watch: %s line %d is not JSON; skipped", book, i + 1)
                continue
            fetched = rec.get("fetch_started_at")
            if not fetched:
                continue
            try:
                t = parse_utc(str(fetched))
            except ValueError:
                logger.warning("cycle_watch: %s line %d has unparseable fetch_started_at %r",
                               book, i + 1, fetched)
                continue
            if lo <= t < hi:
                n += 1
        counts[book] = n
    return counts


def month_log_name(book: str, slot_utc: datetime) -> str:
    """The decision log a slot's records land in. The pipeline names the month
    by the ET run date, which never crosses a month boundary inside a session."""
    return f"{book}_{slot_utc.astimezone(ET).strftime('%Y-%m')}.jsonl"


def read_month_logs(trades_dir: Path, books: Iterable[str], slot_utc: datetime) -> dict[str, list[str]]:
    """Read each book's month log from disk. A missing file reads as no lines."""
    out: dict[str, list[str]] = {}
    for book in books:
        p = trades_dir / month_log_name(book, slot_utc)
        out[book] = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    return out


def classify_slot(counts: Mapping[str, int]) -> str | None:
    """MISSED_CYCLE, PARTIAL_CYCLE, or None when every book has a record."""
    have = [b for b, n in counts.items() if n > 0]
    if not have:
        return MISSED_CYCLE
    if len(have) < len(counts):
        return PARTIAL_CYCLE
    return None


def day_events(trades_dir: Path, books: list[str], session_date: date, close_et: time,
               detected_by: str, detected_at: datetime | None = None,
               admission_dir: Path | None = None) -> list[dict]:
    """Completeness events for every expected slot of the day that is missed,
    withheld or partial. Empty list = every slot ran for every book.

    With `admission_dir`, a slot with no record that an admission guard withheld
    is classified under the withhold's class instead of MISSED_CYCLE, and the
    event carries the admission record's facts."""
    detected_at = detected_at or datetime.now(timezone.utc)
    events: list[dict] = []
    for slot in expected_slots(session_date, close_et):
        counts = books_in_slot(read_month_logs(trades_dir, books, slot), slot)
        kind = classify_slot(counts)
        if kind is None:
            continue
        cause = None
        if kind == MISSED_CYCLE and admission_dir is not None:
            cause = withhold_in_slot(read_admission_log(admission_dir, slot), slot)
            if cause is not None:
                kind = str(cause["event"])
        event = {
            "kind": kind,
            "date": session_date.isoformat(),
            "slot_et": slot_label(slot),
            "slot_utc": slot.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "books_expected": sorted(counts),
            "books_with_record": sorted(b for b, n in counts.items() if n > 0),
            "detected_by": detected_by,
            "detected_at": detected_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if cause is not None:
            event["admission_record"] = {k: cause.get(k) for k in
                                         ("timestamp", "guard", "reason", "files")}
        events.append(event)
    return events


def record_events(events: list[dict], out_dir: Path) -> int:
    """Append events to {out_dir}/{YYYY-MM}.jsonl, idempotently on (kind, slot_utc).

    An EOD rerun (a backup cron, a manual dispatch) must not write the same miss
    twice. Returns how many new rows were written."""
    written = 0
    by_month: dict[str, list[dict]] = {}
    for ev in events:
        by_month.setdefault(ev["date"][:7], []).append(ev)
    for month, evs in by_month.items():
        path = out_dir / f"{month}.jsonl"
        seen: set[tuple[str, str]] = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("cycle_watch: unreadable line in %s; kept, not deduplicated against", path)
                    continue
                seen.add((r.get("kind", ""), r.get("slot_utc", "")))
        new = [e for e in evs if (e["kind"], e["slot_utc"]) not in seen]
        if not new:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            for e in new:
                fh.write(json.dumps(e, sort_keys=True) + "\n")
        written += len(new)
    return written
