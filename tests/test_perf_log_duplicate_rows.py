"""Tripwire: the performance logs' duplicate-date rows are a FROZEN, registered set.

The launch window (2026-04-08 .. 2026-04-13) wrote more than one row for some
dates before the writer's duplicate-date guard landed. The rows are never
deleted; the integrity ledger registers them
(operational_events.2026-10[perf_log_duplicate_rows_frozen]) along with the one
rule every reader resolves them by (keep the last row per date). This test fails
if a new duplicate date appears, a frozen row is edited, or the code's rule and
the ledger's rule drift apart.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

from src.analytics import performance
from src.analytics.performance import EOD_DEDUP_RULE, dedupe_eod, load_performance_history

ROOT = Path(__file__).resolve().parents[1]


def _registration() -> dict:
    ledger = json.loads((ROOT / "scripts" / "phase_a_integrity_ledger.json").read_text(encoding="utf-8"))
    hits = [e for e in ledger["operational_events"]["2026-10"] if e["id"] == "perf_log_duplicate_rows_frozen"]
    assert len(hits) == 1
    return hits[0]


def _observed_duplicates() -> dict:
    out = {}
    for fp in sorted(performance.PERFORMANCE_DIR.glob("*.jsonl")):
        lines = fp.read_text(encoding="utf-8").splitlines()
        rows = [(i + 1, json.loads(l)) for i, l in enumerate(lines) if l.strip()]
        counts = collections.Counter(r["date"] for _, r in rows)
        dup = {d for d, n in counts.items() if n > 1}
        if dup:
            out[fp.stem] = {d: [(i, r["total_value"]) for i, r in rows if r["date"] == d]
                            for d in sorted(dup)}
    return out


def test_duplicate_set_is_exactly_the_frozen_set():
    frozen = _registration()["frozen_set"]
    expected = {b: {d: [(r["line"], r["total_value"]) for r in rows] for d, rows in dates.items()}
                for b, dates in frozen.items()}
    assert _observed_duplicates() == expected, (
        "data/performance duplicate-date rows differ from the ledger's frozen set: "
        "a new duplicate date was written, or a frozen row was edited")


def test_eod_of_record_is_the_last_row_and_dedupe_selects_it():
    reg = _registration()
    assert reg["resolution_rule"] == EOD_DEDUP_RULE
    for book, dates in reg["frozen_set"].items():
        eod = dedupe_eod(load_performance_history(book))
        by_date = {d.strftime("%Y-%m-%d"): v for d, v in zip(eod["date"], eod["total_value"])}
        for d, rows in dates.items():
            records = [r for r in rows if r["eod_of_record"]]
            assert len(records) == 1 and records[0] is rows[-1], (book, d)
            assert by_date[d] == records[0]["total_value"], (book, d)
