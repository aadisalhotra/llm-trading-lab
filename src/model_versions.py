"""Model version tracking + monthly upgrade checker.

Logs every observed model id (returned by the API on each call) and, on the
first trading day of each month, records a transition row if the version
changed from the prior month.
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


def record_observation(model_key: str, observed_version: str, run_date: datetime) -> None:
    """Append every observed version. Cheap, idempotent-ish (one row per run)."""
    MODEL_VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "date": run_date.strftime("%Y-%m-%d"),
        "model_key": model_key,
        "observed_version": observed_version,
        "timestamp": datetime.utcnow().isoformat(),
    }
    with open(_log_path(model_key), "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def detect_monthly_transition(model_key: str, run_date: datetime) -> dict[str, Any] | None:
    """If today is the first observation of a new month AND the version changed
    from the last observation of the prior month, return a transition record.
    Returns None otherwise.
    """
    path = _log_path(model_key)
    if not path.exists():
        return None
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if len(rows) < 2:
        return None

    today_str = run_date.strftime("%Y-%m")
    today_rows = [r for r in rows if r["date"].startswith(today_str)]
    prior_rows = [r for r in rows if not r["date"].startswith(today_str)]
    if not today_rows or not prior_rows:
        return None

    # Only fire on the FIRST observation of this month
    if len(today_rows) > 1:
        return None

    last_prior = prior_rows[-1]
    today_first = today_rows[0]
    if last_prior["observed_version"] == today_first["observed_version"]:
        return None

    transition = {
        "date": run_date.strftime("%Y-%m-%d"),
        "model_key": model_key,
        "old_version": last_prior["observed_version"],
        "new_version": today_first["observed_version"],
        "previous_observation_date": last_prior["date"],
    }
    logger.warning("MODEL TRANSITION %s: %s → %s", model_key,
                   transition["old_version"], transition["new_version"])
    # Append to the per-model log as a typed event too
    transition_record = {**transition, "type": "TRANSITION"}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(transition_record) + "\n")
    return transition
