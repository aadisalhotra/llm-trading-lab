"""Validation-clock segment completeness — regression tests.

The monthly builder computes decision completeness over the CALENDAR month. A
validation clock (Gemini Phase B) starts at a timestamp mid-way through its
first day, so the segment denominator is neither the calendar month nor a whole
number of days. `_phase_b_clock` resolves the window and `_data_integrity`
emits `phase_b_segment_completeness` alongside — never in place of — the
calendar figure.

Two boundary cases are pinned here because both are real and both silently
corrupt the segment verdict if mishandled:

  * the clock-start date carries cycles BEFORE the clock start (2026-08-03 has
    13 logged cycles; only 12 are in-segment), and
  * a day the chain cut short contributes only the cycles it ran (2026-08-06
    ran 5 of 13 cohort-wide, ledger event cycle_gap_2026_08_06) — the
    denominator is the logged count, never an assumed ticks-per-day.

Numeric assertions deliberately avoid live-data-dependent totals: August is an
open month whose record count grows daily. The invariants asserted hold for any
snapshot of the data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_monthly_data_layer as B  # noqa: E402

LEDGER = json.loads((ROOT / "scripts" / "phase_a_integrity_ledger.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- clock resolution

def test_clock_resolves_for_a_month_inside_the_window():
    pb = B._phase_b_clock(LEDGER, "2026-08-01", "2026-08-31")
    assert pb is not None, "August must resolve the Phase B clock"
    assert pb["model"] == "gemini"
    assert pb["clock_start"] == "2026-08-03T14:06:25.440972Z"
    # The naive form is what decision-log timestamps are compared against.
    assert pb["clock_start_naive"] == "2026-08-03T14:06:25.440972"
    assert pb["clock_start_date"] == "2026-08-03"
    assert pb["clock_end"] == "2026-10-31"


def test_clock_is_none_for_every_month_before_the_start():
    for start, end in (("2026-05-01", "2026-05-31"),
                       ("2026-06-01", "2026-06-30"),
                       ("2026-07-01", "2026-07-31")):
        assert B._phase_b_clock(LEDGER, start, end) is None, f"{start} must not resolve a clock"


def test_clock_is_none_after_clock_end():
    assert B._phase_b_clock(LEDGER, "2026-11-01", "2026-11-30") is None


def test_clock_is_found_in_a_later_month_than_it_was_declared():
    """The clock is registered under operational_events['2026-08'] but governs
    September and October — the scan must not be limited to the build month."""
    pb = B._phase_b_clock(LEDGER, "2026-09-01", "2026-09-30")
    assert pb is not None and pb["declared_in"] == "2026-08"
    assert pb["seg_start"] == "2026-09-01", "a whole month inside the window starts at the month"


# ---------------------------------------------------------------- emission

def _integrity(month_start, month_end):
    settings = B.load_settings()
    model_keys = ["claude", "claude_opus", "deepseek", "gemini", "gpt", "grok"]
    return B._data_integrity(model_keys, month_start, month_end, settings, LEDGER)


def test_no_segment_block_for_a_pre_clock_month():
    """This is what keeps reproduce-May exact and every published month stable."""
    out = _integrity("2026-07-01", "2026-07-31")
    assert "phase_b_segment_completeness" not in out


def test_segment_block_emitted_for_august():
    out = _integrity("2026-08-01", "2026-08-31")
    assert "phase_b_segment_completeness" in out
    blk = out["phase_b_segment_completeness"]["gemini_sdk_migration_2026_08"]
    assert blk["model"] == "gemini"
    assert blk["clock_start"] == "2026-08-03T14:06:25.440972Z"
    assert blk["ledger_ref"].endswith("gemini_sdk_migration_2026_08.phase_b_clock_spec")


def test_calendar_figure_is_not_replaced():
    """The segment is ADDITIONAL. per_model_failure_rate must remain the
    calendar-month figure for the scoped model."""
    out = _integrity("2026-08-01", "2026-08-31")
    blk = out["phase_b_segment_completeness"]["gemini_sdk_migration_2026_08"]
    cal = out["per_model_failure_rate"]["gemini"]
    assert blk["calendar_month_comparison"]["records"] == cal["records"]
    assert blk["calendar_month_comparison"]["api_success"] == cal["api_success"]
    # The segment is strictly smaller — a pre-clock cycle exists on 2026-08-03.
    assert blk["records"] < cal["records"]


def test_segment_and_pre_clock_partition_the_calendar_month():
    """Every scoped record is either in-segment, pre-clock, or timestamp-less.
    Nothing may be dropped or double-counted."""
    out = _integrity("2026-08-01", "2026-08-31")
    blk = out["phase_b_segment_completeness"]["gemini_sdk_migration_2026_08"]
    cal = out["per_model_failure_rate"]["gemini"]
    total = blk["records"] + blk["excluded_pre_clock"]["records"] + blk["records_without_timestamp"]
    assert total == cal["records"]
    assert blk["api_success"] + blk["excluded_pre_clock"]["api_success"] == cal["api_success"]


def test_boundary_case_clock_start_day_excludes_the_pre_clock_cycle():
    """2026-08-03 logged 13 cycles; cycle 1 (13:41:57Z) precedes the clock start
    and is a failure, so the in-segment day is 12 records / 10 successes."""
    out = _integrity("2026-08-01", "2026-08-31")
    blk = out["phase_b_segment_completeness"]["gemini_sdk_migration_2026_08"]
    day = blk["daily"]["2026-08-03"]
    assert day["records"] == 12, "the pre-clock cycle must not be in the denominator"
    assert blk["excluded_pre_clock"]["records"] == 1
    assert blk["excluded_pre_clock"]["api_success"] == 0


def test_boundary_case_chain_halt_day_uses_the_logged_denominator():
    """2026-08-06 ran 5 of 13 cycles cohort-wide (ledger cycle_gap_2026_08_06).
    The denominator is 5 — an assumed 13 would read 5/13 = 0.385 and drag the
    segment below the 0.80 gate on an infrastructure outage."""
    out = _integrity("2026-08-01", "2026-08-31")
    blk = out["phase_b_segment_completeness"]["gemini_sdk_migration_2026_08"]
    day = blk["daily"]["2026-08-06"]
    assert day["records"] == 5
    assert day["api_success"] == 5
    assert day["completeness"] == 1.0
    # And the incident is pre-documented in the same layer.
    assert any(ev.get("id") == "cycle_gap_2026_08_06" for ev in out["incidents"])


def test_completeness_arithmetic_and_max_tokens_share():
    out = _integrity("2026-08-01", "2026-08-31")
    blk = out["phase_b_segment_completeness"]["gemini_sdk_migration_2026_08"]
    assert blk["completeness"] == pytest.approx(
        round(blk["api_success"] / blk["records"], 4), abs=1e-9)
    mt = blk["max_tokens"]
    assert mt["count"] == blk["finish_reason_profile"].get("MAX_TOKENS", 0)
    assert mt["share_of_cycles"] == pytest.approx(
        round(mt["count"] / blk["records"], 6), abs=1e-9)
    # Daily records must sum to the segment denominator.
    assert sum(d["records"] for d in blk["daily"].values()) == blk["records"]
    assert sum(d["api_success"] for d in blk["daily"].values()) == blk["api_success"]


def test_gates_carried_verbatim_from_the_ledger():
    out = _integrity("2026-08-01", "2026-08-31")
    blk = out["phase_b_segment_completeness"]["gemini_sdk_migration_2026_08"]
    spec = [e for e in LEDGER["operational_events"]["2026-08"]
            if e["id"] == "gemini_sdk_migration_2026_08"][0]["phase_b_clock_spec"]
    assert blk["gates_per_segment"] == spec["gates_per_segment"]
    assert blk["no_averaging"] == spec["no_averaging"]
