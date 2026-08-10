"""_previous_leaderboard() must select on a complete cohort close, not a date.

A leaderboard snapshot is rewritten on every intraday tick, so a session that
halts mid-day leaves a file named for that date holding the PREVIOUS close's
values. Selecting the newest prior-dated file therefore selects a non-close.

The real instance (2026-08-06, ledger cycle_gap_2026_08_06) was benign: the
file held the 08-05 ranks and 08-05 was the true prior close, so the 08-07
arrows were right by content. The case these tests pin is the one that is not
benign — a halt later in a session, after some models have written updated rows
and others have not, leaving a genuinely mixed snapshot.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.reports import daily_report as DR


COHORT = ["claude", "gpt", "gemini", "grok", "deepseek", "claude_opus"]


def _write_leaderboard(dirpath, date, ranks):
    rows = [{"model_key": k, "rank": r, "cumulative_return": 0.1 * r} for k, r in ranks.items()]
    (dirpath / f"{date}.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


def _write_perf(dirpath, model, dates):
    lines = [json.dumps({"date": d, "model_key": model, "total_value": 100000.0}) for d in dates]
    (dirpath / f"{model}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    lb = tmp_path / "leaderboard"
    perf = tmp_path / "performance"
    lb.mkdir()
    perf.mkdir()
    monkeypatch.setattr(DR, "LEADERBOARD_DIR", lb)
    monkeypatch.setattr(DR, "PERFORMANCE_DIR", perf)
    return lb, perf


RANKS_MON = {"claude": 1, "gpt": 2, "gemini": 3, "grok": 4, "deepseek": 5, "claude_opus": 6}
RANKS_TUE = {"claude": 6, "gpt": 5, "gemini": 4, "grok": 3, "deepseek": 2, "claude_opus": 1}


def test_skips_a_date_with_no_cohort_eod_and_walks_back(dirs):
    """The 2026-08-06 shape: a leaderboard file exists for the halted day, but
    no model wrote an EOD row, so it must be skipped."""
    lb, perf = dirs
    _write_leaderboard(lb, "2026-08-05", RANKS_MON)
    _write_leaderboard(lb, "2026-08-06", RANKS_MON)     # frozen mid-session copy
    for m in COHORT:
        _write_perf(perf, m, ["2026-08-05"])            # 08-06 absent for all
    got = DR._previous_leaderboard(datetime(2026, 8, 7), COHORT)
    assert got == RANKS_MON
    # And it is the 08-05 file that supplied it — 08-06 was skipped, not read.
    assert "2026-08-06" not in DR._complete_eod_dates(COHORT)


def test_mixed_state_snapshot_is_rejected(dirs):
    """The dangerous case. A halt after SOME models wrote their EOD leaves a
    snapshot that is part today, part yesterday. The old predicate propagated
    it as 'yesterday'; the completeness gate must reject it."""
    lb, perf = dirs
    _write_leaderboard(lb, "2026-08-05", RANKS_MON)
    _write_leaderboard(lb, "2026-08-06", RANKS_TUE)     # mixed / partially updated
    for m in COHORT[:3]:
        _write_perf(perf, m, ["2026-08-05", "2026-08-06"])   # three models closed
    for m in COHORT[3:]:
        _write_perf(perf, m, ["2026-08-05"])                 # three did not
    got = DR._previous_leaderboard(datetime(2026, 8, 7), COHORT)
    assert got == RANKS_MON, "a partially-closed day must not become the baseline"
    assert got != RANKS_TUE


def test_complete_date_is_used(dirs):
    lb, perf = dirs
    _write_leaderboard(lb, "2026-08-05", RANKS_MON)
    _write_leaderboard(lb, "2026-08-06", RANKS_TUE)
    for m in COHORT:
        _write_perf(perf, m, ["2026-08-05", "2026-08-06"])   # full cohort closed
    assert DR._previous_leaderboard(datetime(2026, 8, 7), COHORT) == RANKS_TUE


def test_returns_none_when_no_complete_prior_close_exists(dirs):
    lb, perf = dirs
    _write_leaderboard(lb, "2026-08-06", RANKS_MON)
    for m in COHORT:
        _write_perf(perf, m, [])
    assert DR._previous_leaderboard(datetime(2026, 8, 7), COHORT) is None


def test_walks_past_a_corrupt_snapshot(dirs):
    """A corrupt file must not cost arrow tracking when an older good one exists."""
    lb, perf = dirs
    _write_leaderboard(lb, "2026-08-05", RANKS_MON)
    (lb / "2026-08-06.json").write_text("{ this is not json", encoding="utf-8")
    for m in COHORT:
        _write_perf(perf, m, ["2026-08-05", "2026-08-06"])
    assert DR._previous_leaderboard(datetime(2026, 8, 7), COHORT) == RANKS_MON


def test_does_not_depend_on_the_staleness_annotation(dirs):
    """The 08-06 disclosure marker is a one-off annotation, not a mechanism.
    A halted day with NO marker must be rejected just the same."""
    lb, perf = dirs
    _write_leaderboard(lb, "2026-08-05", RANKS_MON)
    rows = [{"model_key": k, "rank": r} for k, r in RANKS_TUE.items()]   # no _staleness key
    (lb / "2026-08-06.json").write_text(json.dumps(rows), encoding="utf-8")
    for m in COHORT:
        _write_perf(perf, m, ["2026-08-05"])
    assert DR._previous_leaderboard(datetime(2026, 8, 7), COHORT) == RANKS_MON


def test_strictly_before_run_date_is_preserved(dirs):
    """Same-day and future snapshots are still excluded."""
    lb, perf = dirs
    _write_leaderboard(lb, "2026-08-05", RANKS_MON)
    _write_leaderboard(lb, "2026-08-07", RANKS_TUE)
    for m in COHORT:
        _write_perf(perf, m, ["2026-08-05", "2026-08-07"])
    assert DR._previous_leaderboard(datetime(2026, 8, 7), COHORT) == RANKS_MON


def test_live_repo_08_07_baseline_is_unchanged_by_the_fix():
    """Regression against the real committed data: the published 2026-08-07
    report drew its arrows from the 08-06 file, whose ranks are the 08-05
    ranks. The fix skips 08-06 and lands on 08-05 — same answer, now by rule."""
    ranks_0805 = {r["model_key"]: r["rank"]
                  for r in json.loads((DR.LEADERBOARD_DIR / "2026-08-05.json")
                                      .read_text(encoding="utf-8"))}
    ranks_0806 = {r["model_key"]: r["rank"]
                  for r in json.loads((DR.LEADERBOARD_DIR / "2026-08-06.json")
                                      .read_text(encoding="utf-8"))}
    assert ranks_0806 == ranks_0805, "the 08-06 file holds the 08-05 close"
    assert DR._previous_leaderboard(datetime(2026, 8, 7), COHORT) == ranks_0805
    assert "2026-08-06" not in DR._complete_eod_dates(COHORT)
