"""The nightly instrument's pure parts (the venue read is not exercised here)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import order_id_set_difference as osd  # noqa: E402


def test_months_cover_the_window():
    assert osd.months_since(datetime(2026, 11, 6, tzinfo=timezone.utc),
                            datetime(2027, 1, 2, tzinfo=timezone.utc)) == ["2026-11", "2026-12", "2027-01"]


def test_after_close_is_et_session_based():
    assert osd.after_close("2026-11-19T21:00:01Z")          # 16:00:01 ET
    assert not osd.after_close("2026-11-19T20:59:00Z")      # 15:59 ET
    assert osd.after_close("2026-11-19T14:00:00Z")          # 09:00 ET, before the open


def test_the_lost_probe_record_is_named_by_its_ledger_entry():
    import json
    ledger = json.loads((ROOT / "scripts" / "phase_a_integrity_ledger.json").read_text(encoding="utf-8"))
    ids = {e.get("id") for e in ledger["operational_events"]["2026-11"]}
    for cycle, ref in osd.LEDGERED_LOST_PROBE_RECORDS.items():
        assert ref.split(" ")[0] in ids, (cycle, ref)
