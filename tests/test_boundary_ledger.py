"""Unit tests for the per-boundary idempotency guard. No network.

These encode the dry-run the design specifies: a boundary handled once is not
re-handled (duplicate run no-ops), a fresh boundary proceeds, and a corrupt
ledger surfaces its read error, on which the pipeline WITHHOLDS the tick (Gate A,
tick_admission_guards_ruling.md section 3). The guard is
intraday-only; EOD is not keyed here yet (EOD places no model trades but can
submit per-position stop orders; EOD admission coverage is pending, Thursday).
Run with: python -m pytest tests/test_boundary_ledger.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.logging.boundary_ledger import (
    boundary_parts,
    load_ledger,
    is_boundary_handled,
    mark_boundary_handled,
)


# ----- boundary identity -----

def test_boundary_parts_floors_to_30min():
    assert boundary_parts(datetime(2026, 5, 26, 15, 1, 12)) == ("2026-05-26", "15:00")
    assert boundary_parts(datetime(2026, 5, 26, 15, 29, 59)) == ("2026-05-26", "15:00")
    assert boundary_parts(datetime(2026, 5, 26, 15, 30, 0)) == ("2026-05-26", "15:30")
    assert boundary_parts(datetime(2026, 5, 26, 15, 45, 0)) == ("2026-05-26", "15:30")
    assert boundary_parts(datetime(2026, 5, 26, 9, 0, 5)) == ("2026-05-26", "09:00")


def test_jittered_starts_same_slot():
    """Two triggers a couple minutes apart for the same tick map to one key."""
    a = boundary_parts(datetime(2026, 5, 26, 15, 0, 31))
    b = boundary_parts(datetime(2026, 5, 26, 15, 6, 10))
    assert a == b == ("2026-05-26", "15:00")


# ----- the dry-run behaviours -----

def test_fresh_boundary_proceeds(tmp_path):
    led = tmp_path / "ledger.json"
    ledger, err = load_ledger(led)
    assert err is None and ledger == {}          # absent file is normal, not an error
    assert not is_boundary_handled(ledger, "2026-05-26", "15:00")  # -> run proceeds


def test_handled_boundary_no_ops(tmp_path):
    """Run a boundary once; a second run for the same boundary must see it handled."""
    led = tmp_path / "ledger.json"
    assert mark_boundary_handled("2026-05-26", "15:00", path=led) is True
    ledger, err = load_ledger(led)
    assert err is None
    assert is_boundary_handled(ledger, "2026-05-26", "15:00")      # -> duplicate no-ops
    assert not is_boundary_handled(ledger, "2026-05-26", "15:30")  # a new boundary still proceeds


def test_corrupt_ledger_returns_its_error(tmp_path):
    """A present-but-unreadable ledger surfaces an error (never raises). The
    caller WITHHOLDS the tick on it (Gate A) — see test_gate_a_admission.py."""
    led = tmp_path / "ledger.json"
    led.write_text("{not valid json", encoding="utf-8")
    ledger, err = load_ledger(led)
    assert err is not None                         # caller withholds and pages
    assert ledger == {}


def test_non_utf8_ledger_is_corrupt_not_a_crash(tmp_path):
    led = tmp_path / "ledger.json"
    led.write_bytes(b'{"2026-11-17": ["09:30\xff"]}')
    _, err = load_ledger(led)
    assert isinstance(err, UnicodeDecodeError)


@pytest.mark.parametrize("body", ['{"2026-11-17": "09:30"}', '{"2026-11-17": [930]}',
                                  '{"2026-11-17": null}'])
def test_wrong_shape_day_is_corrupt(tmp_path, body):
    """A day mapped to a string would answer `slot in "09:30"` by substring."""
    led = tmp_path / "ledger.json"
    led.write_text(body, encoding="utf-8")
    _, err = load_ledger(led)
    assert err is not None


def test_absent_ledger_is_normal(tmp_path):
    assert load_ledger(tmp_path / "missing.json") == ({}, None)


def test_non_dict_ledger_is_corrupt(tmp_path):
    led = tmp_path / "ledger.json"
    led.write_text("[1, 2, 3]", encoding="utf-8")  # valid JSON, wrong shape
    _, err = load_ledger(led)
    assert err is not None


# ----- housekeeping -----

def test_mark_self_heals_corruption(tmp_path):
    led = tmp_path / "ledger.json"
    led.write_text("garbage", encoding="utf-8")
    assert mark_boundary_handled("2026-05-26", "15:00", path=led) is True
    ledger, err = load_ledger(led)
    assert err is None                             # overwritten with a valid ledger
    assert is_boundary_handled(ledger, "2026-05-26", "15:00")


def test_prune_keeps_recent_days(tmp_path):
    led = tmp_path / "ledger.json"
    for day in range(1, 11):                       # 2026-05-01 .. 2026-05-10
        mark_boundary_handled(f"2026-05-{day:02d}", "10:00", keep_days=5, path=led)
    ledger, _ = load_ledger(led)
    assert len(ledger) == 5
    assert "2026-05-10" in ledger and "2026-05-06" in ledger
    assert "2026-05-05" not in ledger              # oldest pruned


def test_multiple_slots_accumulate(tmp_path):
    led = tmp_path / "ledger.json"
    for slot in ("09:30", "10:00", "10:30"):
        mark_boundary_handled("2026-05-26", slot, path=led)
    ledger, _ = load_ledger(led)
    assert sorted(ledger["2026-05-26"]) == ["09:30", "10:00", "10:30"]
