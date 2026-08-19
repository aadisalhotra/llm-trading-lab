"""Regression tests for the minimum-spacing guard.

The guard closes a duplicate-tick gap the per-boundary ledger cannot see: two
runs minutes apart that fall in DIFFERENT :00/:30 slots. It became reachable on
2026-08-18, when the intraday checkout moved to the branch tip — a run queued
behind another tick used to trade a stale book and lose its commit to a rebase
conflict, so its duplicate never landed.

The load-bearing test here is `test_backup_run_filling_a_genuine_gap_is_not_suppressed`.
A guard that suppresses the backup cron is worse than no guard: it converts a
recoverable lost tick into a permanent hole. 2026-08-17's 62-minute cohort-wide
gap (14:33 -> 15:35) is the case the backup exists for, and it is pinned here as
a literal.

Run with: python -m pytest tests/test_tick_spacing.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.logging.tick_spacing import (
    DEFAULT_MIN_MINUTES,
    KEEP_SKIP_ROWS,
    _last_line,
    check_spacing,
    is_enabled,
    last_cohort_decision_utc,
    min_spacing_minutes,
    record_skip,
)

NOW = datetime(2026, 8, 17, 15, 5, 0, tzinfo=timezone.utc)
COHORT = ["claude", "claude_opus", "deepseek", "gemini", "gpt", "grok"]


def _settings(enabled=True, minutes=20, models=None, extra_disabled=None):
    m = {k: {"enabled": True} for k in (models or COHORT)}
    for k in (extra_disabled or []):
        m[k] = {"enabled": False}
    return {"tick_spacing": {"enabled": enabled, "min_minutes": minutes}, "models": m}


def _write_log(directory: Path, model: str, when: datetime, month: str = "2026-08",
               rows_before: int = 2) -> Path:
    """Write a decision log whose LAST row carries `when`.

    Earlier rows are older, so a reader that grabs the first row instead of the
    last fails these tests rather than passing them by luck.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{model}_{month}.jsonl"
    lines = []
    for i in range(rows_before, 0, -1):
        older = when - timedelta(hours=i)
        lines.append(json.dumps({"model_key": model,
                                 "timestamp": older.replace(tzinfo=None).isoformat()}))
    lines.append(json.dumps({"model_key": model,
                             "timestamp": when.replace(tzinfo=None).isoformat()}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------- the case that must not break

def test_backup_run_filling_a_genuine_gap_is_not_suppressed(tmp_path):
    """2026-08-17: the 14:33 tick was lost, the next landed 15:35 — 62 minutes.

    A backup cron firing into that hole is doing its job. Suppressing it turns a
    recoverable lost tick into a permanent one.
    """
    last = datetime(2026, 8, 17, 14, 33, 41, tzinfo=timezone.utc)
    now = datetime(2026, 8, 17, 15, 35, 51, tzinfo=timezone.utc)
    for m in COHORT:
        _write_log(tmp_path, m, last)
    v = check_spacing(_settings(), now, tmp_path)
    assert v.skip is False
    assert v.reason == "spaced"
    assert v.gap_minutes == pytest.approx(62.17, abs=0.05)


def test_eod_and_force_trade_bypass_the_guard_at_the_call_site():
    """EOD executes no trades and must never be spacing-skipped; --force-trade
    is the deliberate operator override. Both are call-site conditions, pinned
    here because neither is expressible in `check_spacing` itself."""
    src = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")
    assert re.search(r"if not is_eod and not force_trade:\s*\n\s*verdict = check_spacing\(", src)


# ------------------------------------------------------------------ core behaviour

def test_duplicate_minutes_later_in_a_different_slot_is_skipped(tmp_path):
    """10:59 and 11:01 are two boundaries — the ledger permits both."""
    last = datetime(2026, 8, 17, 14, 59, 0, tzinfo=timezone.utc)
    now = datetime(2026, 8, 17, 15, 1, 0, tzinfo=timezone.utc)
    for m in COHORT:
        _write_log(tmp_path, m, last)
    v = check_spacing(_settings(), now, tmp_path)
    assert v.skip is True
    assert v.reason == "too_soon"
    assert v.gap_minutes == pytest.approx(2.0)


def test_no_prior_tick_never_skips(tmp_path):
    v = check_spacing(_settings(), NOW, tmp_path)
    assert v.skip is False
    assert v.reason == "no_prior_tick"
    assert v.last_decision is None


@pytest.mark.parametrize("gap_min,expected_skip", [
    (19.9, True),    # inside the window
    (20.0, False),   # exactly at the minimum is spaced enough
    (20.1, False),
])
def test_threshold_is_inclusive_at_the_minimum(tmp_path, gap_min, expected_skip):
    last = NOW - timedelta(minutes=gap_min)
    for m in COHORT:
        _write_log(tmp_path, m, last)
    assert check_spacing(_settings(), NOW, tmp_path).skip is expected_skip


def test_cohort_wide_uses_the_most_recent_model(tmp_path):
    """One model decided 2 minutes ago, five decided an hour ago.

    Max, not min: over-detecting recency costs a skipped tick, under-detecting
    costs a double-trade.
    """
    for m in COHORT[:-1]:
        _write_log(tmp_path, m, NOW - timedelta(minutes=60))
    _write_log(tmp_path, COHORT[-1], NOW - timedelta(minutes=2))
    v = check_spacing(_settings(), NOW, tmp_path)
    assert v.skip is True
    assert v.gap_minutes == pytest.approx(2.0)


def test_disabled_models_are_not_part_of_the_cohort(tmp_path):
    _write_log(tmp_path, "retired_model", NOW - timedelta(minutes=1))
    for m in COHORT:
        _write_log(tmp_path, m, NOW - timedelta(minutes=60))
    settings = _settings(extra_disabled=["retired_model"])
    assert check_spacing(settings, NOW, tmp_path).skip is False


def test_last_row_wins_not_the_first(tmp_path):
    """The reader must tail the file; decision logs append."""
    for m in COHORT:
        _write_log(tmp_path, m, NOW - timedelta(minutes=2), rows_before=5)
    assert check_spacing(_settings(), NOW, tmp_path).skip is True


# ------------------------------------------------------------------ fail-open paths

def test_torn_final_row_does_not_halt_and_other_models_still_count(tmp_path):
    for m in COHORT[:-1]:
        _write_log(tmp_path, m, NOW - timedelta(minutes=2))
    bad = tmp_path / f"{COHORT[-1]}_2026-08.jsonl"
    bad.write_text('{"model_key": "grok", "timesta\n', encoding="utf-8")
    v = check_spacing(_settings(), NOW, tmp_path)
    assert v.skip is True          # the five readable models still answer
    assert v.gap_minutes == pytest.approx(2.0)


def test_all_logs_unparseable_proceeds_rather_than_halting(tmp_path):
    for m in COHORT:
        (tmp_path / f"{m}_2026-08.jsonl").write_text("not json at all\n", encoding="utf-8")
    v = check_spacing(_settings(), NOW, tmp_path)
    assert v.skip is False
    assert v.reason == "no_prior_tick"


def test_future_timestamp_proceeds(tmp_path):
    """Clock skew or a hand-edited log must not wedge trading shut."""
    for m in COHORT:
        _write_log(tmp_path, m, NOW + timedelta(minutes=30))
    v = check_spacing(_settings(), NOW, tmp_path)
    assert v.skip is False
    assert v.reason == "future_timestamp"


def test_empty_file_is_not_a_prior_tick(tmp_path):
    for m in COHORT:
        (tmp_path / f"{m}_2026-08.jsonl").write_text("", encoding="utf-8")
    assert check_spacing(_settings(), NOW, tmp_path).reason == "no_prior_tick"


# --------------------------------------------------------------- timestamp handling

def test_naive_timestamps_are_read_as_utc_not_local(tmp_path):
    """Decision logs write naive UTC. Reading them as local time would shift the
    gap by the machine's offset — on a US-Eastern runner, by four hours."""
    last = NOW - timedelta(minutes=2)
    for m in COHORT:
        _write_log(tmp_path, m, last)
    v = check_spacing(_settings(), NOW, tmp_path)
    assert v.last_decision == last
    assert v.gap_minutes == pytest.approx(2.0)


def test_offset_aware_timestamps_are_normalised(tmp_path):
    """A future writer switching to aware stamps must not break the guard."""
    last = NOW - timedelta(minutes=2)
    for m in COHORT:
        path = tmp_path / f"{m}_2026-08.jsonl"
        path.write_text(json.dumps({"timestamp": last.isoformat()}) + "\n", encoding="utf-8")
    assert check_spacing(_settings(), NOW, tmp_path).gap_minutes == pytest.approx(2.0)


def test_only_the_current_month_is_consulted(tmp_path):
    """A prior-month file cannot be within any spacing window."""
    _write_log(tmp_path, "claude", NOW - timedelta(minutes=1), month="2026-07")
    assert check_spacing(_settings(), NOW, tmp_path).reason == "no_prior_tick"


# ------------------------------------------------------------------- configuration

def test_disabled_flag_reverts_to_pre_guard_behaviour(tmp_path):
    for m in COHORT:
        _write_log(tmp_path, m, NOW - timedelta(minutes=1))
    v = check_spacing(_settings(enabled=False), NOW, tmp_path)
    assert v.skip is False
    assert v.reason == "disabled"


def test_zero_minutes_disables_the_guard(tmp_path):
    for m in COHORT:
        _write_log(tmp_path, m, NOW - timedelta(minutes=1))
    assert check_spacing(_settings(minutes=0), NOW, tmp_path).skip is False


def test_malformed_minimum_falls_back_to_the_default():
    assert min_spacing_minutes({"tick_spacing": {"min_minutes": "twenty"}}) == DEFAULT_MIN_MINUTES
    assert min_spacing_minutes({}) == DEFAULT_MIN_MINUTES
    assert min_spacing_minutes(None) == DEFAULT_MIN_MINUTES


def test_guard_is_on_by_default_when_unconfigured():
    assert is_enabled({}) is True
    assert is_enabled(None) is True


def test_shipped_settings_configure_the_guard_at_twenty_minutes():
    """The ratified value, pinned against a silent edit."""
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    assert settings["tick_spacing"]["enabled"] is True
    assert settings["tick_spacing"]["min_minutes"] == 20


# ------------------------------------------------------------------- the skip event

def test_record_skip_writes_a_spacing_guard_skip_event(tmp_path):
    last = NOW - timedelta(minutes=3)
    for m in COHORT:
        _write_log(tmp_path, m, last)
    v = check_spacing(_settings(), NOW, tmp_path)
    target = tmp_path / "spacing_guard_skips.jsonl"
    assert record_skip(v, NOW, target) is True
    row = json.loads(target.read_text(encoding="utf-8").strip())
    assert row["event"] == "spacing_guard_skip"
    assert row["reason"] == "too_soon"
    assert row["min_minutes"] == 20
    assert row["gap_minutes"] == pytest.approx(3.0)
    assert row["timestamp"] == NOW.isoformat()
    assert row["last_decision"] == last.isoformat()


def test_skip_log_is_pruned(tmp_path):
    target = tmp_path / "spacing_guard_skips.jsonl"
    target.write_text("\n".join(f'{{"n": {i}}}' for i in range(KEEP_SKIP_ROWS + 50)) + "\n",
                      encoding="utf-8")
    for m in COHORT:
        _write_log(tmp_path, m, NOW - timedelta(minutes=1))
    record_skip(check_spacing(_settings(), NOW, tmp_path), NOW, target)
    rows = [ln for ln in target.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(rows) == KEEP_SKIP_ROWS
    assert json.loads(rows[-1])["event"] == "spacing_guard_skip"


def test_record_skip_never_raises_on_a_bad_path(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    v = check_spacing(_settings(), NOW, tmp_path)
    assert record_skip(v, NOW, blocker / "nested" / "skips.jsonl") is False


# ------------------------------------------------------------------ the tail reader

def test_tail_reader_handles_a_row_larger_than_one_block(tmp_path):
    """Decision rows carry full model reasoning; one must never be truncated."""
    path = tmp_path / "big.jsonl"
    huge = {"timestamp": "2026-08-17T15:00:00", "reasoning": "x" * 200_000}
    path.write_text(json.dumps({"first": True}) + "\n" + json.dumps(huge) + "\n",
                    encoding="utf-8")
    assert json.loads(_last_line(path))["reasoning"] == "x" * 200_000


def test_tail_reader_handles_a_single_line_without_trailing_newline(tmp_path):
    path = tmp_path / "one.jsonl"
    path.write_text('{"timestamp": "2026-08-17T15:00:00"}', encoding="utf-8")
    assert json.loads(_last_line(path))["timestamp"] == "2026-08-17T15:00:00"


def test_missing_file_yields_no_decision(tmp_path):
    assert last_cohort_decision_utc(_settings(), NOW, tmp_path) is None
