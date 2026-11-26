"""Slot-identity guard (slot_identity_guard_ruling_2026-11-21) and the 8.5-minute
late-start flag (hub H-OPS-05).

Section 4 of the ruling lists seven cases to add to the replay; each is a test
below, named after its case. The runs are simulated against the guard: a run
that is admitted writes one row per arm carrying its slot, exactly as
log_decision_run does, and the next run checks the logs that result.

Run with: python -m pytest tests/test_slot_identity_guard.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.logging.tick_spacing import (  # noqa: E402
    LATE_START_FLAG_SECONDS,
    check_spacing,
    record_missed_slot,
    slot_fields,
    slot_identity,
    start_window_minutes,
)

ET = ZoneInfo("America/New_York")
COHORT = ["claude", "claude_opus", "deepseek", "gemini", "gpt", "grok"]
DAY = (2026, 11, 30)      # a Monday, EST (UTC-5)


def et(h, m, s=0, day=DAY):
    return datetime(*day, h, m, s, tzinfo=ET)


def _settings(models=None):
    return {"tick_spacing": {"enabled": True, "min_minutes": 20},
            "models": {k: {"enabled": True} for k in (models or COHORT)}}


class Sim:
    """The committed decision logs, and the outcome of each run against them."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path
        self.decided: list[str] = []       # slot ids decided, in order
        self.missed: list[str] = []

    def write(self, start: datetime, end: datetime, arms=COHORT):
        for m in arms:
            path = self.dir / f"{m}_{start.strftime('%Y-%m')}.jsonl"
            row = {"model_key": m, "timestamp": end.astimezone(timezone.utc).replace(tzinfo=None).isoformat(),
                   "fetch_started_at": start.astimezone(timezone.utc).isoformat(), **slot_fields(start)}
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")

    def run(self, start: datetime, duration_min: float = 3.0, arms=COHORT):
        """One pipeline run starting at `start`. Returns the verdict."""
        v = check_spacing(_settings(), start.astimezone(timezone.utc), self.dir)
        assert not v.withhold
        if v.missed:
            if record_missed_slot(v, start.astimezone(timezone.utc), self.dir / "missed_slots.jsonl"):
                self.missed.append(v.slot_id)
        elif not v.skip:
            self.write(start, start + timedelta(minutes=duration_min), arms)
            self.decided.append(v.slot_id)
        return v


# ----------------------------------------------------------- the slot itself

def test_window_is_cadence_minus_spacing_minimum():
    assert start_window_minutes(_settings()) == 10


def test_a_run_s_slot_is_the_slot_it_starts_in():
    """Ruling 1.1: a run triggered for 10:00 that starts at 10:31 is a 10:30 run."""
    assert slot_identity(et(10, 31))[:2] == ("2026-11-30T10:30", 3)
    assert slot_identity(et(9, 30)) == ("2026-11-30T09:30", 1, 0.0)
    assert slot_identity(et(15, 59, 59))[:2] == ("2026-11-30T15:30", 13)
    assert slot_identity(et(9, 29, 59)) == (None, None, None)
    assert slot_identity(et(16, 0)) == (None, None, None)


def test_naive_times_are_utc_like_the_decision_logs():
    assert slot_identity(datetime(2026, 11, 30, 15, 2))[0] == "2026-11-30T10:00"


def test_rows_carry_their_slot_and_the_late_start_flag():
    on_time = slot_fields(et(10, 2, 30))
    assert on_time == {"slot_id": "2026-11-30T10:00", "slot_index": 2,
                       "slot_start_offset_s": 150.0, "late_start": False}
    assert slot_fields(et(10, 8, 30))["late_start"] is False          # exactly 8.5 min
    assert slot_fields(et(10, 8, 31))["late_start"] is True
    assert LATE_START_FLAG_SECONDS == 510
    assert slot_fields(et(16, 5)) == {"slot_id": None, "slot_index": None,
                                      "slot_start_offset_s": None, "late_start": None}


def test_a_flagged_run_still_decides_inside_the_window(tmp_path):
    s = Sim(tmp_path)
    v = s.run(et(10, 9))
    assert (v.skip, v.reason) == (False, "no_prior_tick") and s.decided == ["2026-11-30T10:00"]
    row = json.loads((tmp_path / "gpt_2026-11.jsonl").read_text().splitlines()[-1])
    assert row["late_start"] is True and row["slot_id"] == "2026-11-30T10:00"


# ------------------------------------------------------ ruling section 4 cases

def test_case_1_long_run_followed_by_an_on_schedule_run(tmp_path):
    s = Sim(tmp_path)
    s.run(et(10, 2), duration_min=27)           # ends 10:29
    v = s.run(et(10, 32))                       # on schedule, 3 minutes after it ended
    assert (v.skip, v.reason) == (False, "slot_open")
    assert s.decided == ["2026-11-30T10:00", "2026-11-30T10:30"]


def test_case_2_backup_queued_behind_a_long_run_in_the_same_slot(tmp_path):
    s = Sim(tmp_path)
    s.run(et(10, 2), duration_min=6)
    v = s.run(et(10, 8))                        # backup for 10:00, inside the window
    assert (v.skip, v.missed, v.reason) == (True, False, "slot_taken")
    v = s.run(et(10, 25))                       # a later one, outside the window, slot taken
    assert (v.skip, v.reason) == (True, "slot_taken")
    assert s.decided == ["2026-11-30T10:00"] and s.missed == []


def test_case_3_runs_starting_9_and_11_minutes_into_a_slot(tmp_path):
    s = Sim(tmp_path)
    assert s.run(et(10, 9)).skip is False
    v = s.run(et(10, 41))
    assert (v.skip, v.missed, v.reason) == (True, True, "late_start")
    assert s.decided == ["2026-11-30T10:00"] and s.missed == ["2026-11-30T10:30"]


def test_case_4_primary_more_than_10_minutes_late_then_next_slot_on_time(tmp_path):
    s = Sim(tmp_path)
    s.run(et(9, 32))
    v = s.run(et(10, 15))                       # the 10:00 primary, 15 minutes late
    assert v.missed is True
    v = s.run(et(10, 31))                       # 10:30 on time: not withheld
    assert (v.skip, v.reason) == (False, "slot_open")
    assert s.decided == ["2026-11-30T09:30", "2026-11-30T10:30"]
    assert s.missed == ["2026-11-30T10:00"]


def test_case_5_run_stops_after_some_arms_decided_then_backup_in_window(tmp_path):
    """Ruling 1.2: any arm's model-call record takes the slot."""
    s = Sim(tmp_path)
    s.run(et(11, 2), arms=["claude", "gpt"])    # stopped after two arms
    v = s.run(et(11, 7))
    assert (v.skip, v.reason) == (True, "slot_taken")
    assert s.decided == ["2026-11-30T11:00"]


def test_case_6_first_last_and_early_close_ticks(tmp_path):
    s = Sim(tmp_path)
    assert s.run(et(9, 31)).reason == "no_prior_tick"
    assert s.run(et(15, 32)).reason == "slot_open"
    assert s.decided == ["2026-11-30T09:30", "2026-11-30T15:30"]
    # Early close (Fri 2026-11-27, 13:00): the last tick is the 12:30 slot.
    e = Sim(tmp_path / "early")
    (tmp_path / "early").mkdir()
    assert e.run(et(12, 31, day=(2026, 11, 27))).skip is False
    assert e.decided == ["2026-11-27T12:30"]


def test_case_7_run_at_window_edge_taking_full_duration_then_next_two_slots(tmp_path):
    """Ruling 2.3: the deadline bounds a run under 29 minutes; a run starting
    at the edge of its window ends by 39 minutes in, and the next run, queued
    behind it, starts inside its own window. Delay does not accumulate."""
    s = Sim(tmp_path)
    s.run(et(10, 9, 59), duration_min=28.9)     # edge of window, full bounded duration
    v1 = s.run(et(10, 38, 53))                  # queued behind it: 8:53 into 10:30
    v2 = s.run(et(11, 2))
    assert (v1.skip, v2.skip) == (False, False)
    assert s.decided == ["2026-11-30T10:00", "2026-11-30T10:30", "2026-11-30T11:00"]


# ----------------------------------------------------------- the missed record

def test_a_missed_slot_is_recorded_once_with_its_cause(tmp_path):
    s = Sim(tmp_path)
    s.run(et(10, 2))
    s.run(et(10, 44))
    s.run(et(10, 52))                           # a second late run in the same slot
    rows = [json.loads(x) for x in (tmp_path / "missed_slots.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["slot_id"] == "2026-11-30T10:30" and rows[0]["cause"] == "late_start"
    assert rows[0]["start_offset_s"] == 840.0 and rows[0]["window_minutes"] == 10


def test_consecutive_decisions_start_at_least_20_minutes_apart(tmp_path):
    """Ruling 2.1, swept: every pair of admitted starts one minute apart."""
    s = Sim(tmp_path)
    starts = []
    t = et(9, 30)
    while t < et(16, 0):
        if s.run(t, duration_min=0.5).skip is False:
            starts.append(t)
        t += timedelta(minutes=1)
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(starts, starts[1:])]
    assert len(starts) == 13 and min(gaps) >= 20


def test_the_pipeline_records_a_missed_slot_and_skips_a_taken_one():
    src = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")
    assert "if verdict.skip and verdict.missed:" in src
    assert "record_missed_slot(verdict, run_date.astimezone(timezone.utc))" in src


def test_decision_rows_carry_the_slot_fields():
    src = (ROOT / "src" / "logging" / "decision_log.py").read_text(encoding="utf-8")
    assert "**slot_fields(run_date)," in src
