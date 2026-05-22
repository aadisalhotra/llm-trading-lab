"""Model version tracking + drift detector.

Logs every observed model id (returned by the API on each call) and, on every
run, records a transition row if the observed version changed from the
previous observation — catching mid-month provider-side alias repoints, not
just first-of-month upgrades.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from .config_loader import MODEL_VERSIONS_DIR

logger = logging.getLogger("llmlab.versions")


def _log_path(model_key: str):
    return MODEL_VERSIONS_DIR / f"{model_key}.jsonl"


def record_observation(model_key: str, observed_version: str, run_date: datetime,
                       api_success: bool = True) -> None:
    """Append every observed version (success OR failure). One row per run.

    `api_success` is recorded so detect_version_transition can compare only
    successful observations. Failed calls are still logged here for
    audit/history, but they record a *synthetic* observed_version — BaseAdapter
    sets model_id_returned to the configured id on failure — so comparing them
    would fire a spurious transition on every failure→recovery cycle (e.g. real
    model_version → configured alias → real model_version). Defaults to True so
    older callers and pre-existing rows are treated as successful.
    """
    MODEL_VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "date": run_date.strftime("%Y-%m-%d"),
        "model_key": model_key,
        "observed_version": observed_version,
        "api_success": bool(api_success),
        "timestamp": datetime.utcnow().isoformat(),
    }
    with open(_log_path(model_key), "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def detect_version_transition(model_key: str, run_date: datetime) -> dict[str, Any] | None:
    """Compare the latest observation against the previous one for this model.

    Fires on ANY change in the observed model id — mid-month or month-boundary
    alike — so a provider repointing a floating alias (as DeepSeek did on
    2026-04-24) is caught on the tick it happens, not just on the first trading
    day of the month. Must be called AFTER `record_observation` has appended
    the current run's observation.

    Only api_success=true observations are compared. Failed calls are still
    recorded (for audit) but log a synthetic observed_version (the configured
    id, per BaseAdapter), so a failure→recovery cycle must not register as a
    transition. Returns a transition record (and appends it to the per-model
    log) when the two most recent successful observations differ, else None.
    TRANSITION event rows are skipped when reading so they never count as
    observations.
    """
    path = _log_path(model_key)
    if not path.exists():
        return None
    observations: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Skip transition event rows (and any legacy/malformed rows missing
            # observed_version) so only real observations are compared.
            if row.get("type") == "TRANSITION" or "observed_version" not in row:
                continue
            # Compare only SUCCESSFUL observations. Failed calls log the
            # configured id as a synthetic observation; comparing them would
            # fire a spurious transition on every failure→recovery cycle.
            # Rows predating the api_success field default to True.
            if not row.get("api_success", True):
                continue
            observations.append(row)
    if len(observations) < 2:
        return None

    previous = observations[-2]
    current = observations[-1]
    if previous["observed_version"] == current["observed_version"]:
        return None

    transition = {
        "date": run_date.strftime("%Y-%m-%d"),
        "model_key": model_key,
        "old_version": previous["observed_version"],
        "new_version": current["observed_version"],
        "previous_observation_date": previous["date"],
    }
    logger.warning("MODEL TRANSITION %s: %s → %s", model_key,
                   transition["old_version"], transition["new_version"])
    # Append to the per-model log as a typed event too (same row format).
    transition_record = {**transition, "type": "TRANSITION"}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(transition_record) + "\n")
    return transition
