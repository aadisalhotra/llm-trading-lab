"""Per-boundary idempotency ledger — exactly-once per intraday tick.

A "decision boundary" is one 30-minute intraday tick, keyed by ET trading day +
the :00/:30 slot it falls in. The pipeline records each boundary here once its
decisions are made, and checks the ledger BEFORE prompting any model, so a
duplicate run for the same boundary cleanly no-ops instead of re-prompting the
models and double-trading.

The EOD wrap-up is deliberately NOT guarded: it executes no trades (run_one_model
has an is_eod early-return — snapshot/report only), so it carries no double-trade
risk, and it keeps its own per-day idempotency (log_daily_snapshot date-dedup,
once-per-day digest) plus its intentional two-trigger redundancy (chain post-close
handoff + the 21:00 UTC cron). Guarding it here would needlessly neuter that
redundancy.

What this protects against: a Fix A dispatch retry, a manual force-run, a
backup-cron double-fire, and multi-trigger redundancy (Path 1/2) — any of which
can otherwise land two runs on the same intraday boundary and trade twice.

================================ TRIPWIRE ====================================
RACE-FREEDOM DEPENDS ON THE intraday-pipeline CONCURRENCY GROUP.

This is a read-check-then-write guard. It is race-free ONLY because the
`concurrency: group: intraday-pipeline, cancel-in-progress: false` block in
.github/workflows/intraday.yml serializes runs — so each run checks out the
PRIOR run's committed ledger before it starts, and two runs for the same
boundary never execute at the same time.

DO NOT remove or weaken that concurrency group (in particular during the future
Path 2 stateless-scheduler rework) without first adding the atomic claim-commit
hardening: commit+push a claim BEFORE the model loop and abort if the push is
rejected non-fast-forward. Without serialization, two concurrent runs could both
pass the check before either writes — and both would trade. See docs/MONITORING.md.
==============================================================================

The ledger lives at data/state/handled_boundaries.json and is committed by the
workflow in the SAME commit as the decision logs and per-model state files, so a
boundary's marker exists if-and-only-if its decisions were durably committed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from ..config_loader import STATE_DIR

logger = logging.getLogger("llmlab.boundary_ledger")

LEDGER_FILENAME = "handled_boundaries.json"
DEFAULT_KEEP_DAYS = 5


def _ledger_path(path: Path | None = None) -> Path:
    return path or (STATE_DIR / LEDGER_FILENAME)


def boundary_parts(run_date: datetime) -> tuple[str, str]:
    """(day, slot) identity for one intraday tick. day = YYYY-MM-DD (ET); slot = "HH:MM".

    `run_date` is the ET-localized run timestamp (datetime.now(EASTERN)). The
    slot floors the start time to the 30-minute boundary, so every trigger for
    the same tick (chain dispatch, backup cron, manual force, repository_dispatch)
    maps to the same key even with a minute or two of start-time jitter.

    EOD is intentionally not keyed here — see the module docstring.
    """
    day = run_date.strftime("%Y-%m-%d")
    minute = 0 if run_date.minute < 30 else 30
    return day, f"{run_date.hour:02d}:{minute:02d}"


def load_ledger(path: Path | None = None) -> tuple[dict[str, list[str]], Exception | None]:
    """Load the ledger. Returns (ledger, read_error).

    Distinguishes "absent/empty" (normal — first tick of the day, no file yet)
    from "present-but-unreadable" (corrupt JSON / OS error), because only the
    latter warrants an alert:
        absent file        -> ({}, None)        normal; proceed silently
        present + parses   -> (ledger, None)
        present + corrupt  -> ({}, exception)   caller FAILS OPEN and alerts loudly

    Never raises — a corrupt ledger must never block a trading tick.
    """
    p = _ledger_path(path)
    if not p.exists():
        return {}, None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"ledger root is {type(data).__name__}, expected an object")
        return data, None
    except Exception as e:  # noqa: BLE001 — a corrupt ledger is reported, never fatal
        return {}, e


def is_boundary_handled(ledger: dict[str, list[str]], day: str, slot: str) -> bool:
    """True if (day, slot) is already recorded as handled in the given ledger dict."""
    return slot in (ledger.get(day) or [])


def mark_boundary_handled(
    day: str,
    slot: str,
    *,
    keep_days: int = DEFAULT_KEEP_DAYS,
    path: Path | None = None,
) -> bool:
    """Record (day, slot) as handled and prune to the most recent `keep_days`.

    Best-effort: never raises. A failed write means a future duplicate run could
    re-trade this boundary (same risk class as the partial-completion gap), so it
    is logged at ERROR — but it must not crash a tick whose decisions are already
    made. Returns True on a successful write.

    On a corrupt existing ledger this overwrites it with a fresh ledger
    containing at least this entry, self-healing the corruption. Past-day entries
    lost in that overwrite are harmless: their boundaries are in the past and
    cannot re-run.
    """
    try:
        ledger, _err = load_ledger(path)  # corrupt -> {}, and we overwrite (self-heal)
        slots = set(ledger.get(day) or [])
        slots.add(slot)
        ledger[day] = sorted(slots)
        # Keep only the most recent `keep_days` so the committed file stays tiny.
        if len(ledger) > keep_days:
            for old_day in sorted(ledger)[:-keep_days]:
                del ledger[old_day]
        p = _ledger_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2, sort_keys=True)
        return True
    except Exception as e:  # noqa: BLE001 — a failed mark must never crash the tick
        logger.error(
            "Failed to mark boundary %s %s handled (non-fatal; a duplicate run "
            "could re-trade this boundary): %s", day, slot, e,
        )
        return False
