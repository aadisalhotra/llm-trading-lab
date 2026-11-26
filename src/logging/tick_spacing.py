"""Spacing guard keyed on scheduled-slot identity (slot_identity_guard_ruling_2026-11-21).

SLOT IDENTITY (hub decision 2026-11-21 14:31 ET; Research's ruling of the same
day, which this module implements):

  * A run's slot is the 30-minute slot in which the run STARTS (ruling 1.1),
    never the slot that triggered it.
  * A slot is taken once any model-call record exists for it, for any arm
    (ruling 1.2). A withheld-tick record does not take it.
  * A run decides only if it starts within the first W minutes of its slot,
    W = 30 - tick_spacing.min_minutes = 10 (ruling 2). A later start decides
    nothing; the slot is recorded once as missed, with its cause
    (data/state/missed_slots.jsonl). Consecutive ticks therefore start at
    least min_minutes apart, start to start, by construction.
  * No test against the previous run's end: that test is what withheld
    on-schedule ticks behind a long run (ruling 2, last paragraph). The
    elapsed-time skip described below is REPLACED by the two slot tests; the
    elapsed time is still recorded on the verdict.
  * Every decision row carries its slot (slot_fields(), ruling 3.1), and a
    run that starts more than 8.5 minutes into its slot is flagged on its rows
    (late_start, hub H-OPS-05).
  * The EOD pass keeps its own key and is outside this rule (ruling 3.3).

The original elapsed-time design follows, kept for the record; its Gate A
(fail-closed) part is unchanged.

Minimum-spacing guard — no two decision ticks closer than N minutes.

Complements the per-boundary idempotency ledger; it does not replace it. The two
catch different duplicates:

  * `boundary_ledger` keys on (ET day, :00/:30 slot). It stops a *same-boundary*
    duplicate — two triggers for the same tick.
  * This guard keys on elapsed time since the cohort last decided. It stops a
    *near-in-time* duplicate that lands in a DIFFERENT slot, which the boundary
    key cannot see. A tick at 10:59 and another at 11:01 are two minutes apart
    and two distinct boundaries; only spacing catches that.

The gap became reachable when the intraday checkout moved to the branch tip
(2026-08-18). A run queued behind another tick used to trade a stale book and
lose its commit to a rebase conflict, so its duplicate never landed. Now it
lands correctly — and can land minutes after the tick it queued behind.

WHAT THIS MUST NOT DO: suppress a backup run that is filling a genuine gap.
On 2026-08-17 the 14:34Z tick was lost and the next landed at 15:35Z, a
62-minute cohort-wide hole. A backup cron firing into that hole is doing its
job. Only closeness to a tick that ACTUALLY COMPLETED is grounds to skip, which
is why the reference time is read from the committed decision logs rather than
from the schedule: the logs record what happened, the schedule records what was
supposed to.

Cohort-wide and deterministic: one verdict for the whole run, derived from the
most recent decision-log row across all enabled models. Per-model spacing would
let the cohort drift apart tick by tick, which is an effective-parity problem,
not just an untidy one.

FAILS CLOSED (Gate A, tick_admission_guards_ruling.md section 3, registered
blocking conditions). The verdict WITHHOLDS the tick when:

  * any enabled book's PRESENT log raises on read — an OSError, or a final row
    that is not valid UTF-8 (UnicodeDecodeError used to escape this module and
    crash the run); or
  * at least one log is present and NO present log yields a usable final row
    (a JSON object with a parseable timestamp).

One torn final row with other books readable remains an answer, as before:
the readable books still say when the cohort last decided. An ABSENT log is
normal — the first tick of a month — and never withholds (demonstration (v)).
A withhold is not a silent halt: the pipeline pages CRITICAL and writes a
TICK_WITHHELD_GUARD_UNREADABLE event (src/logging/tick_admission.py).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

from ..config_loader import STATE_DIR, TRADES_DIR
from ..record_class import is_administrative_row

logger = logging.getLogger("llmlab.tick_spacing")

SKIP_LOG_FILENAME = "spacing_guard_skips.jsonl"
MISSED_LOG_FILENAME = "missed_slots.jsonl"
DEFAULT_MIN_MINUTES = 20
KEEP_SKIP_ROWS = 200
KEEP_MISSED_ROWS = 5000

EASTERN = ZoneInfo("America/New_York")
SLOT_MINUTES = 30                 # the registered cadence
FIRST_SLOT_MINUTES = 9 * 60 + 30  # 09:30 ET is slot 1
TICKS_PER_SESSION = 13
# Hub H-OPS-05: a run whose pipeline starts more than 8.5 minutes into its slot
# is flagged on its rows. It still decides if it is inside the start window.
LATE_START_FLAG_SECONDS = 510

# Tail read size. Decision-log rows carry full model reasoning and run 2-5 MB
# per model-month, so the last row is read by seeking from the end rather than
# parsing the file. 64 KB clears the largest row observed by a wide margin and
# the loop grows it if a row is somehow bigger.
_TAIL_BLOCK = 65536


class SpacingVerdict(NamedTuple):
    """Outcome of the spacing check.

    The pipeline acts on two fields. `withhold` (Gate A) means the guard could
    not answer and the tick must not run; it is checked first. `skip` means the
    guard answered "too soon". They are never both True. The rest exist so the
    event records why, which is what makes the guard's firing rate auditable.
    """
    skip: bool
    reason: str
    last_decision: datetime | None
    gap_minutes: float | None
    min_minutes: int
    # Files the guard could not use: a read that raised, or a final row that
    # is not usable. Each is named in a CRITICAL. A torn row alone does not
    # withhold while another book answers; see `withhold`.
    unreadable: tuple[str, ...] = ()
    # Gate A: True when a registered blocking condition holds. Defaulted so
    # every existing construction keeps working.
    withhold: bool = False
    # Slot identity (ruling sections 1-2). slot_id is "YYYY-MM-DDTHH:MM" (ET,
    # the slot's start), None off the 13-slot grid. `missed` is True when the
    # run started after the start window: the slot is recorded once as missed.
    slot_id: str | None = None
    start_offset_s: float | None = None
    window_minutes: int | None = None
    missed: bool = False


def _settings_block(settings: dict[str, Any] | None) -> dict[str, Any]:
    return ((settings or {}).get("tick_spacing") or {})


def min_spacing_minutes(settings: dict[str, Any] | None = None) -> int:
    """Configured minimum spacing, defaulting when absent or malformed."""
    raw = _settings_block(settings).get("min_minutes", DEFAULT_MIN_MINUTES)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.error("tick_spacing.min_minutes is not an integer (%r) — using %d",
                     raw, DEFAULT_MIN_MINUTES)
        return DEFAULT_MIN_MINUTES
    return value if value > 0 else 0


def start_window_minutes(settings: dict[str, Any] | None = None) -> int:
    """W = cadence - registered spacing minimum (ruling 2): 30 - 20 = 10."""
    return max(0, SLOT_MINUTES - min_spacing_minutes(settings))


def slot_identity(when: datetime) -> tuple[str | None, int | None, float | None]:
    """(slot_id, slot index 1..13, seconds since the slot's start) for a start
    time. A naive `when` is read as UTC (the decision logs' convention); the
    pipeline's run_date is ET-aware. Off the 13-slot grid: (None, None, None).
    The id is the boundary ledger's (day, "HH:MM") key joined with "T"."""
    t = (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(EASTERN)
    minutes = t.hour * 60 + t.minute
    k = (minutes - FIRST_SLOT_MINUTES) // SLOT_MINUTES + 1
    if not 1 <= k <= TICKS_PER_SESSION:
        return None, None, None
    start_min = FIRST_SLOT_MINUTES + (k - 1) * SLOT_MINUTES
    start = t.replace(hour=start_min // 60, minute=start_min % 60, second=0, microsecond=0)
    return (f"{t.strftime('%Y-%m-%d')}T{start.strftime('%H:%M')}", k,
            round((t - start).total_seconds(), 3))


def slot_fields(run_date: datetime) -> dict[str, Any]:
    """The per-row slot fields (ruling 3.1; hub H-OPS-05 late-start flag).
    `run_date` is the pipeline's start time, the instant the slot is read from."""
    sid, k, off = slot_identity(run_date)
    return {"slot_id": sid, "slot_index": k, "slot_start_offset_s": off,
            "late_start": (off > LATE_START_FLAG_SECONDS) if off is not None else None}


def _row_slot(row: dict[str, Any] | None) -> str | None:
    """The slot a model-call row decided for: its slot_id, or, on rows written
    before the slot fields existed, the slot of its fetch start (the run's
    start to within seconds), else of its timestamp."""
    if not isinstance(row, dict):
        return None
    sid = row.get("slot_id")
    if isinstance(sid, str) and sid:
        return sid
    for key in ("fetch_started_at", "timestamp"):
        stamp = _parse_utc(row.get(key)) if row.get(key) else None
        if stamp is not None:
            return slot_identity(stamp)[0]
    return None


def is_enabled(settings: dict[str, Any] | None = None) -> bool:
    """Guard on/off. Present so a Research objection can be honoured by flipping
    a flag rather than reverting the commit that added the guard."""
    return bool(_settings_block(settings).get("enabled", True))


def _last_line(path: Path) -> str | None:
    """Last non-empty line of a file, read by seeking from the end.

    Returns None for a missing or empty file. OS errors propagate, and so does
    UnicodeDecodeError for a final row that is not valid UTF-8; the caller
    treats both as "raised on read" and withholds (Gate A).
    """
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size == 0:
            return None
        block = _TAIL_BLOCK
        while True:
            start = max(0, size - block)
            f.seek(start)
            chunk = f.read(size - start)
            # Drop the trailing newline(s) FIRST. Testing the raw chunk for a
            # newline would accept the file's own final newline as proof the
            # line is complete, and return a truncated tail for any row longer
            # than one block — and decision rows carry full model reasoning.
            stripped = chunk.rstrip(b"\r\n")
            if not stripped:
                if start == 0:
                    return None
                block *= 2
                continue
            cut = stripped.rfind(b"\n")
            if cut != -1:
                # Slicing after a newline keeps the decode UTF-8 aligned even
                # when the block boundary split a multi-byte character.
                return stripped[cut + 1:].decode("utf-8")
            if start == 0:
                return stripped.decode("utf-8")
            block *= 2


def _parse_utc(raw: str) -> datetime | None:
    """Parse a decision-log timestamp into an aware UTC datetime.

    Decision logs write naive UTC (`datetime.utcnow().isoformat()`), so a naive
    value is stamped UTC rather than local — reading it as local time would
    shift the gap by the machine's offset and silently break the guard.
    """
    if not isinstance(raw, str):
        # A missing or non-string timestamp is "no usable row", never a crash
        # (an int here used to raise AttributeError out of the guard).
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class _LogScan(NamedTuple):
    """What reading the current month's logs found. Internal to the guard."""
    latest: datetime | None
    present: int                 # enabled books whose log path exists
    usable: int                  # present logs that yielded a usable final row
    raised: tuple[str, ...]      # present logs whose read raised (OSError / bad UTF-8)
    torn: tuple[str, ...]        # present logs whose final row is not usable
    slots: tuple[str, ...] = ()  # slot of each book's last model-call row


def _scan_logs(
    settings: dict[str, Any] | None,
    now_utc: datetime,
    trades_dir: Path | None = None,
) -> _LogScan:
    """Read every enabled book's current-month log once. Never raises.

    Every present file is attempted even after one fails, so the CRITICAL can
    name each file that needs repair rather than only the first.
    """
    directory = trades_dir or TRADES_DIR
    month = now_utc.strftime("%Y-%m")
    models = (settings or {}).get("models") or {}
    latest: datetime | None = None
    present = usable = 0
    raised: list[str] = []
    torn: list[str] = []
    slots: list[str] = []
    for model_key, cfg in models.items():
        if not (cfg or {}).get("enabled"):
            continue
        path = directory / f"{model_key}_{month}.jsonl"
        if not path.exists():
            # Absent is normal: no tick has run for this book this month.
            continue
        present += 1
        try:
            line = _last_line(path)
        except (OSError, UnicodeDecodeError) as e:
            logger.error("Decision log %s raised on read (%s: %s)",
                         path.name, type(e).__name__, e)
            raised.append(str(path))
            continue
        if not line:
            # Present but empty: it yields no row. Nothing to name as damaged,
            # but it is not an answer either.
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            row = None
        stamp = _parse_utc(row.get("timestamp")) if isinstance(row, dict) else None
        if stamp is None:
            logger.error("Unusable final row in %s (not a JSON object with a "
                         "parseable timestamp)", path.name)
            torn.append(str(path))
            continue
        usable += 1
        if is_administrative_row(row):
            # The final row answers the read (Gate A's blocking conditions are
            # about readability, and it is readable), but it records no model
            # call, so it is not "last decided" (eod_stop ruling §1). Rare --
            # an EOD stop row, a boundary force-cover -- so a full read is fine.
            stamp, row = _last_model_call_row(path)
            if stamp is None:
                continue
        sid = _row_slot(row)
        if sid:
            slots.append(sid)
        if latest is None or stamp > latest:
            latest = stamp
    return _LogScan(latest, present, usable, tuple(raised), tuple(torn), tuple(slots))


def _last_model_call_stamp(path: Path) -> datetime | None:
    """Timestamp of the last parseable model-call row in `path`, or None."""
    return _last_model_call_row(path)[0]


def _last_model_call_row(path: Path) -> tuple[datetime | None, dict[str, Any] | None]:
    """(timestamp, row) of the last parseable model-call row in `path`."""
    best: datetime | None = None
    best_row: dict[str, Any] | None = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and not is_administrative_row(row):
                stamp = _parse_utc(row.get("timestamp"))
                if stamp is not None:
                    best, best_row = stamp, row
    return best, best_row


def last_cohort_decision_utc(
    settings: dict[str, Any] | None,
    now_utc: datetime,
    trades_dir: Path | None = None,
    unreadable: list[str] | None = None,
) -> datetime | None:
    """Most recent usable decision-log timestamp across all enabled models, or None.

    `unreadable`, when given, collects every present log that raised on read or
    whose final row is not usable. It does not change the return value. Never
    raises (Gate A: the verdict, not an exception, carries a read failure).

    Only the current month's files are consulted. A month whose file is absent
    means no tick has run this month, and the previous month's last tick is at
    minimum a full day old — far outside any spacing window — so falling back
    to it could not change a verdict.

    The MAXIMUM across models is deliberate. The six run within seconds of each
    other, so max and min rarely differ; where they do, max is the conservative
    choice, because over-detecting recency costs a skipped tick while
    under-detecting costs a double-trade.
    """
    scan = _scan_logs(settings, now_utc, trades_dir)
    if unreadable is not None:
        unreadable.extend(scan.raised + scan.torn)
    return scan.latest


def check_spacing(
    settings: dict[str, Any] | None,
    now_utc: datetime,
    trades_dir: Path | None = None,
) -> SpacingVerdict:
    """Decide whether this tick may run (its slot is open and it started inside
    the start window), must not decide (slot taken, or started too late: a
    missed slot), or must be withheld because the guard cannot answer (Gate A).

    `now_utc` is the run's start. Gate A's blocking conditions are unchanged."""
    minimum = min_spacing_minutes(settings)
    if not is_enabled(settings):
        return SpacingVerdict(False, "disabled", None, None, minimum)
    if minimum <= 0:
        return SpacingVerdict(False, "disabled", None, None, minimum)
    scan = _scan_logs(settings, now_utc, trades_dir)
    named = scan.raised + scan.torn

    # Registered blocking condition 1: any enabled book's present log raised.
    if scan.raised:
        logger.error("Spacing guard could not read %d decision log(s) — "
                     "WITHHOLDING the tick", len(scan.raised))
        return SpacingVerdict(False, "read_error", None, None, minimum, named, True)
    # Registered blocking condition 2: logs are present and none of them answers.
    if scan.present and not scan.usable:
        logger.error("No present decision log yields a usable final row "
                     "(%d present) — WITHHOLDING the tick", scan.present)
        return SpacingVerdict(False, "no_usable_row", None, None, minimum, named, True)
    last = scan.latest
    gap = (now_utc - last).total_seconds() / 60.0 if last is not None else None
    window = start_window_minutes(settings)
    sid, _k, off = slot_identity(now_utc)
    slot = dict(slot_id=sid, start_offset_s=off, window_minutes=window)
    if sid is None:
        # Off the 13-slot grid (a run outside 09:30-16:00 ET reaching here).
        # There is no slot to key on; the market-hours gate decides.
        return SpacingVerdict(False, "off_grid", last, gap, minimum, named, **slot)
    # Ruling 1.2: any arm's model-call record for this slot takes it.
    if sid in scan.slots:
        return SpacingVerdict(True, "slot_taken", last, gap, minimum, named, **slot)
    # Ruling 2: decide only inside the first W minutes of the slot.
    if off >= window * 60:
        return SpacingVerdict(True, "late_start", last, gap, minimum, named,
                              missed=True, **slot)
    reason = "no_prior_tick" if last is None else "slot_open"
    return SpacingVerdict(False, reason, last, gap, minimum, named, **slot)


def record_missed_slot(
    verdict: SpacingVerdict,
    now_utc: datetime,
    path: Path | None = None,
) -> bool:
    """Record a missed slot ONCE (ruling 2): one row per slot_id, with its
    cause. A second late run in the same slot writes nothing. Best-effort, as
    record_skip. Returns True when a row was written."""
    target = path or (STATE_DIR / MISSED_LOG_FILENAME)
    row = {
        "event": "slot_missed",
        "slot_id": verdict.slot_id,
        "cause": "late_start",
        "start_utc": now_utc.isoformat(),
        "start_offset_s": verdict.start_offset_s,
        "window_minutes": verdict.window_minutes,
        "last_decision": verdict.last_decision.isoformat() if verdict.last_decision else None,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        existing: list[str] = []
        if target.is_file():
            with open(target, "r", encoding="utf-8") as f:
                existing = [ln for ln in f.read().splitlines() if ln.strip()]
        for ln in existing:
            try:
                if json.loads(ln).get("slot_id") == verdict.slot_id:
                    return False
            except (json.JSONDecodeError, AttributeError):
                continue
        existing.append(json.dumps(row, sort_keys=True))
        with open(target, "w", encoding="utf-8") as f:
            f.write("\n".join(existing[-KEEP_MISSED_ROWS:]) + "\n")
        return True
    except Exception as e:  # noqa: BLE001 — never crash a tick that decides nothing
        logger.error("Failed to record slot_missed (non-fatal): %s", e)
        return False


def record_skip(
    verdict: SpacingVerdict,
    now_utc: datetime,
    path: Path | None = None,
) -> bool:
    """Append a `spacing_guard_skip` event and prune the file.

    Best-effort: a failed write must not crash a tick that is already skipping.
    Returns True when the row was written.
    """
    target = path or (STATE_DIR / SKIP_LOG_FILENAME)
    row = {
        "event": "spacing_guard_skip",
        "timestamp": now_utc.isoformat(),
        "last_decision": verdict.last_decision.isoformat() if verdict.last_decision else None,
        "gap_minutes": round(verdict.gap_minutes, 3) if verdict.gap_minutes is not None else None,
        "min_minutes": verdict.min_minutes,
        "reason": verdict.reason,
        "slot_id": verdict.slot_id,
        "start_offset_s": verdict.start_offset_s,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        existing: list[str] = []
        if target.is_file():
            with open(target, "r", encoding="utf-8") as f:
                existing = [ln for ln in f.read().splitlines() if ln.strip()]
        existing.append(json.dumps(row, sort_keys=True))
        with open(target, "w", encoding="utf-8") as f:
            f.write("\n".join(existing[-KEEP_SKIP_ROWS:]) + "\n")
        return True
    except Exception as e:  # noqa: BLE001 — never crash a skipping tick
        logger.error("Failed to record spacing_guard_skip (non-fatal): %s", e)
        return False
