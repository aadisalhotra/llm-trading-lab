"""Persistent state for alert de-duplication and rate-capping.

Lives at /data/alerts/state.json and is committed with the rest of data/ so
it survives the ephemeral GitHub Actions runners. Two scopes:

  - `milestones_fired`: cross-day ledger of which +N% milestones each model
    has ever crossed. This is what makes milestone alerts fire "once per
    threshold per model, ever" rather than every day the model is above the
    line.

  - `reported_once`: cross-day ledger of conditions that are emailed once
    and then folded into the digest while they persist, as
    {condition key: signature}. A new signature (the condition changed) is a
    new event and emails again. Used for the explained negative-cash state
    (a FILL_OVERRUN the system already accounts for), which otherwise
    re-emailed every evening until the book's cash recovered.

  - `daily`: a single-day bucket (keyed by ET trading date) holding the
    de-dup keys already fired today, the count of event emails sent today
    (against the 10/day cap), the overflow queue (alerts past the cap, to be
    bundled into the digest), and whether the digest itself has gone out.
    Reset automatically when the ET date rolls over.

All reads/writes are best-effort: a corrupt or missing file yields a fresh
default rather than an exception, because losing alert-dedup state is never
worth crashing a trading tick over.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..config_loader import ALERTS_DIR

logger = logging.getLogger("llmlab.alerts.state")

STATE_FILE = ALERTS_DIR / "state.json"


def _default_state() -> dict[str, Any]:
    return {
        "milestones_fired": {},   # model_key -> [thresholds]
        "reported_once": {},      # condition key -> signature already emailed
        "daily": _default_daily(""),
    }


def _default_daily(date_str: str) -> dict[str, Any]:
    return {
        "date": date_str,
        "sent_count": 0,        # event emails actually sent today (toward the cap)
        "dedup_keys": [],       # keys already fired today
        "overflow": [],         # events past the cap — bundled into the digest
        "continuing": [],       # already-reported conditions, folded into the digest
        "digest_sent": False,   # digest dedupe (EOD can fire multiple times/day)
        "fired_log": [],        # compact record of every event handled today
    }


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return _default_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Alert state unreadable (%s) — starting fresh", e)
        return _default_state()
    # Defensive shape repair so older/partial files don't KeyError downstream.
    if not isinstance(data, dict):
        return _default_state()
    data.setdefault("milestones_fired", {})
    if not isinstance(data.get("reported_once"), dict):
        data["reported_once"] = {}
    daily = data.get("daily")
    if not isinstance(daily, dict):
        data["daily"] = _default_daily("")
    else:
        for k, v in _default_daily(daily.get("date", "")).items():
            daily.setdefault(k, v)
    return data


def save_state(state: dict[str, Any]) -> None:
    try:
        ALERTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
        tmp.replace(STATE_FILE)
    except Exception:
        logger.exception("Failed to persist alert state")


def ensure_daily(state: dict[str, Any], date_str: str) -> dict[str, Any]:
    """Return today's daily bucket, resetting it if the ET date has rolled.

    `milestones_fired` and `reported_once` are intentionally preserved across
    the reset — they are the cross-day ledgers. Everything else is per-day and
    starts clean.
    """
    daily = state.get("daily") or {}
    if daily.get("date") != date_str:
        daily = _default_daily(date_str)
        state["daily"] = daily
    return daily
