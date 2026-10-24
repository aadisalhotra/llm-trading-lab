"""scripts/verify_session_cycles.py -- the post-hoc cycle-count check for
2026-11-02 / 2026-11-27 / 2026-12-24 (hub ratification 2026-10-24).

Validated against a known-positive control: a synthetic 2026-11-27 log that
contains the exact failure the pre-2026-10-24 code would have produced (a
tick after the 13:00 bell), plus a doubled slot and a missing slot. The check
must FAIL on it and PASS on the clean version.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import verify_session_cycles as vsc  # noqa: E402

NOV27 = date(2026, 11, 27)
# 2026-11-27 is EST: ET = UTC-5. Slots 09:30..12:30 ET = 14:30..17:30 UTC.
CLEAN_SLOTS_UTC = [datetime(2026, 11, 27, 14, 30, tzinfo=timezone.utc) + timedelta(minutes=30 * i)
                   for i in range(7)]


def _write_log(tmp_path, book, fetches, extra_records=()):
    path = tmp_path / f"{book}_2026-11.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for t in fetches:
            stamp = t + timedelta(minutes=2, seconds=30)
            f.write(json.dumps({"date": "2026-11-27", "model_key": book,
                                "fetch_started_at": t.isoformat(),
                                "timestamp": stamp.replace(tzinfo=None).isoformat()}) + "\n")
        for rec in extra_records:
            f.write(json.dumps(rec) + "\n")
        # a record from another day in the same month file is ignored
        f.write(json.dumps({"date": "2026-11-26", "fetch_started_at": CLEAN_SLOTS_UTC[0].isoformat()}) + "\n")


def test_expected_slots_registered_dates():
    for d, n in vsc.REGISTERED_CYCLES.items():
        slots, _close = vsc.expected_slots(date.fromisoformat(d))
        assert len(slots) == n, d
    slots, close = vsc.expected_slots(NOV27)
    assert slots == ["09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30"]
    assert close.strftime("%H:%M") == "13:00"
    assert vsc.expected_slots(date(2026, 11, 26)) is None      # Thanksgiving


def test_slot_reads_fetch_time_in_et_across_dst():
    assert vsc.slot_of("2026-10-23T13:32:25+00:00") == "09:30"  # EDT
    assert vsc.slot_of("2026-11-02T14:32:25+00:00") == "09:30"  # EST
    assert vsc.slot_of("2026-11-02T20:59:59+00:00") == "15:30"


def test_clean_early_close_passes(tmp_path):
    for book in ("gpt", "grok"):
        _write_log(tmp_path, book, CLEAN_SLOTS_UTC,
                   extra_records=[{"date": "2026-11-27", "timestamp": "2026-11-27T19:00:00"}])
    assert vsc.verify(NOV27, tmp_path) == 0


def test_known_positive_control_fails(tmp_path):
    _write_log(tmp_path, "gpt", CLEAN_SLOTS_UTC)                              # clean book
    after_bell = datetime(2026, 11, 27, 18, 2, tzinfo=timezone.utc)           # 13:02 ET
    _write_log(tmp_path, "grok", CLEAN_SLOTS_UTC + [after_bell])
    assert vsc.verify(NOV27, tmp_path) == 1

    doubled = CLEAN_SLOTS_UTC + [CLEAN_SLOTS_UTC[3] + timedelta(minutes=10)]
    _write_log(tmp_path, "grok", doubled)
    assert vsc.verify(NOV27, tmp_path) == 1

    _write_log(tmp_path, "grok", CLEAN_SLOTS_UTC[:-1])                        # 12:30 missing
    assert vsc.verify(NOV27, tmp_path) == 1


def test_no_logs_is_distinct_exit(tmp_path):
    assert vsc.verify(NOV27, tmp_path) == 2


def test_committed_full_session_passes():
    """2026-10-23 (EDT, full session) as committed: 13/13 for every book."""
    assert vsc.verify(date(2026, 10, 23)) == 0
