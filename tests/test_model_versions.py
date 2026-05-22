"""Tests for the model-version drift detector (src/model_versions.py).

Focus: detect_version_transition must compare only SUCCESSFUL observations.
A failed API call between two successful observations of the same id must NOT
fire a transition (failed calls log a synthetic configured id); a genuine id
change between two successful calls MUST fire.

Run with: python -m pytest tests/test_model_versions.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import src.model_versions as mv


def _transition_rows(path: Path) -> int:
    """Count TRANSITION event rows written to a per-model log."""
    n = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and json.loads(line).get("type") == "TRANSITION":
                n += 1
    return n


def test_failed_call_between_successes_does_not_fire(monkeypatch, tmp_path):
    # Redirect the per-model log dir to an isolated temp dir.
    monkeypatch.setattr(mv, "MODEL_VERSIONS_DIR", tmp_path)
    d = datetime(2026, 5, 22)
    key = "gemini"

    # success(idA)
    mv.record_observation(key, "gemini-3.1-pro-002", d, api_success=True)
    assert mv.detect_version_transition(key, d) is None        # only one observation

    # failed call: BaseAdapter records the configured id as a synthetic
    # observation (differs from idA), flagged api_success=False
    mv.record_observation(key, "gemini-3.1-pro-preview", d, api_success=False)
    assert mv.detect_version_transition(key, d) is None        # failure excluded from comparison

    # success(idA) again — recovery to the same real id
    mv.record_observation(key, "gemini-3.1-pro-002", d, api_success=True)
    assert mv.detect_version_transition(key, d) is None        # idA == idA, nothing fires

    # The failed call must not have produced a transition row.
    assert _transition_rows(mv._log_path(key)) == 0


def test_genuine_id_change_between_successes_fires(monkeypatch, tmp_path):
    monkeypatch.setattr(mv, "MODEL_VERSIONS_DIR", tmp_path)
    d = datetime(2026, 5, 22)
    key = "deepseek"

    mv.record_observation(key, "deepseek-v4-flash", d, api_success=True)
    assert mv.detect_version_transition(key, d) is None        # only one observation

    mv.record_observation(key, "deepseek-v4-pro", d, api_success=True)
    t = mv.detect_version_transition(key, d)
    assert t is not None
    assert t["old_version"] == "deepseek-v4-flash"
    assert t["new_version"] == "deepseek-v4-pro"

    # Exactly one transition row was written.
    assert _transition_rows(mv._log_path(key)) == 1
