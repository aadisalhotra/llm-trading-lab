"""Minimum-spacing guard — no two decision ticks closer than N minutes.

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

from ..config_loader import STATE_DIR, TRADES_DIR, book_active
from ..record_class import is_administrative_row

logger = logging.getLogger("llmlab.tick_spacing")

SKIP_LOG_FILENAME = "spacing_guard_skips.jsonl"
DEFAULT_MIN_MINUTES = 20
KEEP_SKIP_ROWS = 200

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
    for model_key, cfg in models.items():
        if not (cfg or {}).get("enabled"):
            continue
        if not book_active(model_key, settings, now_utc):
            # Before its first session (models.<key>.active_from).
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
            stamp = _last_model_call_stamp(path)
            if stamp is None:
                continue
        if latest is None or stamp > latest:
            latest = stamp
    return _LogScan(latest, present, usable, tuple(raised), tuple(torn))


def _last_model_call_stamp(path: Path) -> datetime | None:
    """Timestamp of the last parseable model-call row in `path`, or None."""
    best: datetime | None = None
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
                    best = stamp
    return best


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
    """Decide whether this tick may run, is too close to the last decision, or
    must be withheld because the guard cannot answer (Gate A)."""
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
    if scan.latest is None:
        # Nothing present at all: the first tick of a month. Normal; proceed.
        return SpacingVerdict(False, "no_prior_tick", None, None, minimum, named)

    last = scan.latest
    gap = (now_utc - last).total_seconds() / 60.0
    if gap < 0:
        # A future-dated last tick means clock skew or a hand-edited log. The
        # guard did read its files, so this is an answer, not an unreadable
        # guard: say so and proceed.
        logger.error("Last decision (%s) is ahead of now (%s) — ignoring for spacing",
                     last.isoformat(), now_utc.isoformat())
        return SpacingVerdict(False, "future_timestamp", last, gap, minimum, named)
    if gap < minimum:
        return SpacingVerdict(True, "too_soon", last, gap, minimum, named)
    return SpacingVerdict(False, "spaced", last, gap, minimum, named)


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
