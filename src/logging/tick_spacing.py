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

FAILS OPEN. An unreadable or unparseable log proceeds to trade and logs at
ERROR — a silently halted pipeline is the worse failure, and the boundary
ledger still holds the primary duplicate protection underneath.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

from ..config_loader import STATE_DIR, TRADES_DIR

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

    `skip` is the only field the pipeline acts on; the rest exist so the skip
    event records why, which is what makes the guard's firing rate auditable.
    """
    skip: bool
    reason: str
    last_decision: datetime | None
    gap_minutes: float | None
    min_minutes: int


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

    Returns None for a missing or empty file. Never raises for content reasons;
    OS errors propagate to the caller, which fails open.
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
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def last_cohort_decision_utc(
    settings: dict[str, Any] | None,
    now_utc: datetime,
    trades_dir: Path | None = None,
) -> datetime | None:
    """Most recent decision-log timestamp across all enabled models, or None.

    Only the current month's files are consulted. A month whose file is absent
    means no tick has run this month, and the previous month's last tick is at
    minimum a full day old — far outside any spacing window — so falling back
    to it could not change a verdict.

    The MAXIMUM across models is deliberate. The six run within seconds of each
    other, so max and min rarely differ; where they do, max is the conservative
    choice, because over-detecting recency costs a skipped tick while
    under-detecting costs a double-trade.
    """
    directory = trades_dir or TRADES_DIR
    month = now_utc.strftime("%Y-%m")
    models = (settings or {}).get("models") or {}
    latest: datetime | None = None
    for model_key, cfg in models.items():
        if not (cfg or {}).get("enabled"):
            continue
        path = directory / f"{model_key}_{month}.jsonl"
        if not path.is_file():
            continue
        line = _last_line(path)
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            # A torn final line is not a reason to halt trading; the other five
            # models still answer the question.
            logger.error("Unparseable final row in %s — ignoring for spacing", path.name)
            continue
        stamp = _parse_utc(row.get("timestamp"))
        if stamp is None:
            continue
        if latest is None or stamp > latest:
            latest = stamp
    return latest


def check_spacing(
    settings: dict[str, Any] | None,
    now_utc: datetime,
    trades_dir: Path | None = None,
) -> SpacingVerdict:
    """Decide whether this tick is too close to the cohort's last decision."""
    minimum = min_spacing_minutes(settings)
    if not is_enabled(settings):
        return SpacingVerdict(False, "disabled", None, None, minimum)
    if minimum <= 0:
        return SpacingVerdict(False, "disabled", None, None, minimum)
    try:
        last = last_cohort_decision_utc(settings, now_utc, trades_dir)
    except OSError as e:
        logger.error("Spacing guard could not read the decision logs (%s) — "
                     "proceeding WITHOUT spacing protection", e)
        return SpacingVerdict(False, "read_error", None, None, minimum)
    if last is None:
        return SpacingVerdict(False, "no_prior_tick", None, None, minimum)

    gap = (now_utc - last).total_seconds() / 60.0
    if gap < 0:
        # A future-dated last tick means clock skew or a hand-edited log. Trading
        # is not the thing to halt over it; say so and proceed.
        logger.error("Last decision (%s) is ahead of now (%s) — ignoring for spacing",
                     last.isoformat(), now_utc.isoformat())
        return SpacingVerdict(False, "future_timestamp", last, gap, minimum)
    if gap < minimum:
        return SpacingVerdict(True, "too_soon", last, gap, minimum)
    return SpacingVerdict(False, "spaced", last, gap, minimum)


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
