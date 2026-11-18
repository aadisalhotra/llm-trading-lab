"""Tick-admission withhold events — the durable record of a tick that did not run.

Registered by tick_admission_guards_ruling.md section 4. An admission control
decides whether a tick occurs; when it says *withhold*, no model is prompted
and no order is possible, so the tick is a missing observation for G-COMPLETE,
never a G-EXEC event (section 2, property 3). This module writes that event.

Only one class exists in code so far, because only Gate A has landed:

    TICK_WITHHELD_GUARD_UNREADABLE   a duplicate guard's file is present and
                                     unreadable; all books; one event,
                                     correlated across the cohort by construction.

The other four section 4 classes (TICK_WITHHELD_VENUE_UNREACHABLE,
BOOK_WITHHELD_UNACCOUNTED_ORDERS, TICK_WITHHELD_BOOKS_DIVERGED,
DECISION_RECORD_LOST) belong to the pre-trade venue check (V) and the offline
replay. They are deliberately NOT defined here: a class exists in code when the
code that emits it lands, and the closed set is checked by
`REGISTERED_WITHHOLD_CLASSES`.

Durability. Events go to data/admission/{YYYY-MM}.jsonl, which the workflow's
"Commit + push updated data" step commits (it adds all of data/). The pipeline
therefore exits 0 on a withhold: a non-zero exit would skip that step and the
event would never reach origin. The page is the CRITICAL plus the watchdog
/fail ping, not the run colour.

Append-only and never pruned: these rows are scored, not housekeeping.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config_loader import DATA_DIR

logger = logging.getLogger("llmlab.tick_admission")

ADMISSION_DIRNAME = "admission"

TICK_WITHHELD_GUARD_UNREADABLE = "TICK_WITHHELD_GUARD_UNREADABLE"

# The closed set of withhold classes that exist in code. Extends only by
# registration (ruling section 4) and only when the emitting code lands.
REGISTERED_WITHHOLD_CLASSES = frozenset({TICK_WITHHELD_GUARD_UNREADABLE})

# Which guard withheld. Recorded so a person repairing the tick knows which
# file to open, and so the two guards' firing rates stay separable.
GUARD_BOUNDARY_LEDGER = "boundary_ledger"
GUARD_SPACING = "tick_spacing"
_GUARDS = frozenset({GUARD_BOUNDARY_LEDGER, GUARD_SPACING})


def admission_log_path(now_utc: datetime, directory: Path | None = None) -> Path:
    """Monthly event file, keyed on the UTC month of the withheld tick."""
    base = directory or (DATA_DIR / ADMISSION_DIRNAME)
    return base / f"{now_utc.astimezone(timezone.utc).strftime('%Y-%m')}.jsonl"


def build_withhold_event(
    event_class: str,
    *,
    guard: str,
    now_utc: datetime,
    books: Iterable[str],
    files: Iterable[str],
    reason: str,
    detail: str = "",
    boundary: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Compose one withhold row. Raises ValueError on an unregistered class or
    guard — an untagged withhold is the no-third-state failure in a new place."""
    if event_class not in REGISTERED_WITHHOLD_CLASSES:
        raise ValueError(f"unregistered withhold class {event_class!r}")
    if guard not in _GUARDS:
        raise ValueError(f"unknown admission guard {guard!r}")
    row: dict[str, Any] = {
        "event": event_class,
        "timestamp": now_utc.astimezone(timezone.utc).isoformat(),
        "guard": guard,
        "reason": reason,
        "books": sorted(set(books)),
        "files": list(files),
        "models_prompted": 0,
        "orders_submitted": 0,
    }
    if boundary is not None:
        row["boundary_day"], row["boundary_slot"] = boundary
    if detail:
        row["detail"] = detail
    return row


def record_withhold(row: dict[str, Any], *, path: Path | None = None) -> bool:
    """Append a withhold row. Returns True when it was written.

    A failed write does NOT turn the withhold into a proceed — the tick is
    already withheld and stays so. It is logged at ERROR; the CRITICAL the
    caller sends still carries the same facts.
    """
    stamp = datetime.fromisoformat(row["timestamp"])
    target = path or admission_log_path(stamp)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        return True
    except Exception as e:  # noqa: BLE001 — the withhold stands either way
        logger.error("Failed to record %s (the tick is still withheld): %s",
                     row.get("event"), e)
        return False
