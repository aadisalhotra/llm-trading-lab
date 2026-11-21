"""Call-input retention and response flags (logging class).

Governing texts:
  * docs/registrations/rq6_harness_and_call_input_retention_ruling.md §2
    (hub ruling 2026-11-19; storage design by Research): every model call's
    prompt is retained from the output-budget boundary, in one file per cycle,
    data/prompts/{YYYY-MM}/{cycle_id}.jsonl, one line per call; the decision
    row carries the hashes; image BYTES are kept only on two tick slots per
    session (§2.4).
  * docs/registrations/output_budget_equalization_ruling.md rev. 2 §3:
    decision_truncated, json_repaired, and a WARN on a successful call with
    either flag.

Logging class: nothing here alters a model input or an order. The retention
file is written from the very objects handed to the adapter, so it cannot
differ from what was sent, and a failure to write it never stops the tick: it
is caught, logged, and the row carries input_retention_failed: true.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..config_loader import DATA_DIR

logger = logging.getLogger("llmlab.call_inputs")

PROMPTS_DIR = DATA_DIR / "prompts"

# §2.4: sessions are numbered from the output-budget boundary, the first being
# n = 0; the session's scheduled intraday ticks are numbered 1..13 from the
# 09:30 ET slot.
RETENTION_BOUNDARY_SESSION = "2026-11-23"
TICKS_PER_SESSION = 13
FIRST_SLOT_MINUTES = 9 * 60 + 30

# Calibration window (hub 2026-11-21, item (a); Research's agreement on rq6
# section 2.4 is the landing condition). The refreshed cohort's calibration
# set is drawn from the snapshots' retained inputs of 2026-11-23..25 and must
# cover all thirteen tick positions with the exact images sent. Inside this
# window every scheduled tick keeps its image bytes; outside it the section
# 2.4 two-slot rotation applies unchanged. The window closes by itself at the
# close of Wednesday 2026-11-25: no revert needs to land.
ET = ZoneInfo("America/New_York")
FULL_IMAGE_RETENTION_START = datetime(2026, 11, 23, 0, 0, tzinfo=ET)
FULL_IMAGE_RETENTION_END = datetime(2026, 11, 25, 16, 0, tzinfo=ET)

# output_budget_equalization_ruling.md §3: a budget stop, by provider.
BUDGET_STOP_REASONS = frozenset({"max_tokens", "length", "MAX_TOKENS"})

# Model threads write the same per-cycle file concurrently.
_FILE_LOCK = threading.Lock()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------- response flags
def response_flags(decision_result: Any) -> dict[str, bool]:
    """decision_truncated / json_repaired for one decision call (§3)."""
    md = getattr(decision_result, "metadata", None) or {}
    return {
        "decision_truncated": str(md.get("finish_reason")) in BUDGET_STOP_REASONS,
        "json_repaired": bool(md.get("json_repaired", False)),
    }


def alert_if_flagged(model_key: str, cycle_id: str, decision_result: Any,
                     send_alert: Any) -> bool:
    """The §3 WARN: a SUCCESSFUL decision call with either flag. Returns
    whether it alerted. Never raises."""
    try:
        flags = response_flags(decision_result)
        if not (getattr(decision_result, "success", False)
                and (flags["decision_truncated"] or flags["json_repaired"])):
            return False
        md = getattr(decision_result, "metadata", None) or {}
        send_alert(
            "WARN", f"Response flagged: {model_key}",
            "A successful decision call ended on its output budget or was parsed "
            "only after repair_json changed its text "
            "(output_budget_equalization_ruling.md section 3).",
            {"model": model_key, "cycle_id": cycle_id, **flags,
             "finish_reason": md.get("finish_reason"),
             "output_tokens": md.get("output_tokens")},
            kind="response_flagged", dedup_key=f"response_flagged:{model_key}:{cycle_id}")
        return True
    except Exception:  # noqa: BLE001 — an alert must never cost the tick
        logger.exception("[%s] response-flag alert failed", model_key)
        return False


# --------------------------------------------------------------- image rotation
def session_index(session_date: str | date, boundary: str = RETENTION_BOUNDARY_SESSION) -> int | None:
    """n for `session_date`: how many NYSE sessions after the boundary session
    it is (the boundary session is 0). None before the boundary."""
    d = session_date if isinstance(session_date, date) else date.fromisoformat(str(session_date)[:10])
    b = date.fromisoformat(boundary)
    if d < b:
        return None
    import pandas_market_calendars as mcal
    sched = mcal.get_calendar("NYSE").schedule(start_date=b.isoformat(), end_date=d.isoformat())
    sessions = [s.date() for s in sched.index]
    if d not in sessions:
        return None
    return sessions.index(d)


def retained_slots(n: int) -> tuple[int, int]:
    """§2.4: the two tick slots whose image bytes are kept on session n."""
    return (1 + (n % TICKS_PER_SESSION), 1 + ((n + 6) % TICKS_PER_SESSION))


def tick_slot(run_date: datetime) -> int | None:
    """1..13 for a scheduled intraday tick: the 30-minute slot its start time
    floors to (the boundary ledger's key), counted from 09:30 ET."""
    minutes = run_date.hour * 60 + (0 if run_date.minute < 30 else 30)
    k = (minutes - FIRST_SLOT_MINUTES) // 30 + 1
    return k if 1 <= k <= TICKS_PER_SESSION else None


def in_full_retention_window(run_date: datetime) -> bool:
    """True from the 2026-11-23 session through the close of 2026-11-25 (ET).
    A naive run_date is read as ET, as the pipeline's run date is."""
    t = run_date if run_date.tzinfo else run_date.replace(tzinfo=ET)
    return FULL_IMAGE_RETENTION_START <= t < FULL_IMAGE_RETENTION_END


# Why a tick kept its image bytes (per-row field image_bytes_reason, hub
# H-OPS-00 Batch 1). The registered section 2.4 rotation is named first: a
# rotation slot inside the window would have kept its bytes anyway, so
# "calibration_window" marks exactly the bytes the window adds.
REASON_RQ6_ROTATION = "rq6_rotation"
REASON_CALIBRATION_WINDOW = "calibration_window"


def image_bytes_reason(run_date: datetime) -> str | None:
    """rq6_rotation, calibration_window, or None when this tick keeps no
    image bytes."""
    n = session_index(run_date.date())
    slot = tick_slot(run_date)
    if n is None or slot is None:
        return None
    if slot in retained_slots(n):
        return REASON_RQ6_ROTATION
    if in_full_retention_window(run_date):
        return REASON_CALIBRATION_WINDOW
    return None


def keeps_image_bytes(run_date: datetime) -> bool:
    """True when this tick keeps its image bytes: every scheduled tick inside
    the calibration window, otherwise only today's two retained slots."""
    return image_bytes_reason(run_date) is not None


# --------------------------------------------------------------- retention
@dataclass
class CallInput:
    """One model call exactly as handed to the adapter."""
    stage: str                       # "screen" or "trade"
    system: str
    user: str
    images: list[bytes] = field(default_factory=list)


def _line(cycle_id: str, model_key: str, prompt_version: str, c: CallInput) -> dict[str, Any]:
    return {
        "cycle_id": cycle_id,
        "model_key": model_key,
        "stage": c.stage,
        "prompt_version": prompt_version,
        "system": c.system,
        "user": c.user,
        "system_sha256": sha256_text(c.system),
        "user_sha256": sha256_text(c.user),
        "images": [{"sha256": sha256_bytes(b), "bytes": len(b)} for b in c.images],
    }


def retain_call_inputs(cycle_id: str, model_key: str, run_date: datetime,
                       prompt_version: str, calls: list[CallInput],
                       prompts_dir: Path | None = None) -> dict[str, Any]:
    """Write one line per call to the cycle's file, keep image bytes on a
    retained slot, and return the fields the decision row carries.

    Never raises. On any failure the row fields still carry every hash that
    could be computed, and input_retention_failed is true.
    """
    root = prompts_dir or PROMPTS_DIR
    fields: dict[str, Any] = {
        "trade_system_sha256": None, "trade_user_sha256": None,
        "screen_system_sha256": None, "screen_user_sha256": None,
        "image_count": 0, "images": [], "image_bytes_retained": False,
        "image_bytes_reason": None, "input_retention_failed": False,
    }
    try:
        lines = [_line(cycle_id, model_key, prompt_version, c) for c in calls]
        for ln in lines:
            fields[f"{ln['stage']}_system_sha256"] = ln["system_sha256"]
            fields[f"{ln['stage']}_user_sha256"] = ln["user_sha256"]
            if ln["stage"] == "trade":
                fields["image_count"] = len(ln["images"])
                fields["images"] = ln["images"]
    except Exception:  # noqa: BLE001 — logging must never cost the tick
        logger.exception("[%s] call-input hashing failed", model_key)
        fields["input_retention_failed"] = True
        return fields
    try:
        month = f"{cycle_id[:4]}-{cycle_id[4:6]}" if len(cycle_id) >= 6 else run_date.strftime("%Y-%m")
        d = root / month
        d.mkdir(parents=True, exist_ok=True)
        payload = "".join(json.dumps(ln, ensure_ascii=False) + "\n" for ln in lines)
        with _FILE_LOCK:
            with open(d / f"{cycle_id}.jsonl", "a", encoding="utf-8", newline="\n") as f:
                f.write(payload)
        trade = next((c for c in calls if c.stage == "trade"), None)
        reason = image_bytes_reason(run_date)
        if trade and trade.images and reason is not None:
            img_dir = d / "images"
            img_dir.mkdir(parents=True, exist_ok=True)
            for b in trade.images:
                p = img_dir / f"{sha256_bytes(b)}.png"
                if not p.exists():
                    p.write_bytes(b)
            fields["image_bytes_retained"] = True
            fields["image_bytes_reason"] = reason
    except Exception:  # noqa: BLE001 — logging must never cost the tick
        logger.exception("[%s] call-input retention write failed for cycle %s",
                         model_key, cycle_id)
        fields["input_retention_failed"] = True
    return fields
