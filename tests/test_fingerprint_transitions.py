"""Fingerprint transitions per arm (hub H-OPS-19; fingerprint_rotation_ruling_2026-11-22.md)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.analytics import fingerprint_transitions as ft

ROOT = Path(__file__).resolve().parents[1]


def _row(ts, fp, mid="deepseek-v4-pro", cycle=None, sfp=None):
    return {"timestamp": ts, "date": ts[:10], "cycle_id": cycle or ts, "model_id_configured": mid,
            "api_system_fingerprint": fp, "screening_system_fingerprint": sfp}


@pytest.fixture
def trades(tmp_path, monkeypatch):
    monkeypatch.setattr(ft, "TRADES_DIR", tmp_path)

    def write(key, month, rows):
        with open(tmp_path / f"{key}_{month}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    return write


def test_classifications_are_the_rulings():
    assert ft.CLASSIFICATION == {"grok-4.20-0309-reasoning": "rotating", "deepseek-v4-pro": "stable"}
    assert ft.classification("grok-4.7") == "unclassified"


def test_a_row_without_a_fingerprint_is_not_a_transition():
    rows = [_row("2026-11-02T14:00", "a"), _row("2026-11-02T15:00", None), _row("2026-11-03T14:00", "a")]
    assert ft.transitions("deepseek", rows) == []
    rows.append(_row("2026-11-04T14:00", "b"))
    t = ft.transitions("deepseek", rows)
    assert len(t) == 1 and (t[0]["from"], t[0]["to"], t[0]["last_row"]) == ("a", "b", "2026-11-03T14:00")


def test_identifiers_and_legs_are_kept_apart():
    rows = [_row("2026-11-02T14:00", "a", mid="m1", sfp="s1"),
            _row("2026-11-02T15:00", "z", mid="m2", sfp="s1"),        # another model: no transition
            _row("2026-11-03T14:00", "a", mid="m1", sfp="s2")]        # screening leg only
    t = ft.transitions("x", rows)
    assert [(x["model_id"], x["leg"], x["from"], x["to"]) for x in t] == [("m1", "screening", "s1", "s2")]


def test_a_change_across_the_month_boundary_counts_in_the_later_month(trades):
    trades("deepseek", "2026-10", [_row("2026-10-30T19:00", "a")])
    trades("deepseek", "2026-11", [_row("2026-11-02T14:00", "b"), _row("2026-11-03T14:00", "b")])
    s = ft.month_summary(["deepseek", "gpt"], "2026-11-01", "2026-11-30")
    (g,) = s["per_arm"]["deepseek"]
    assert (g["transitions"], g["distinct_values"], g["sessions"], g["classification"]) == (1, 1, 2, "stable")
    assert "gpt" not in s["per_arm"]                                   # no fingerprint, omitted
    assert ft.month_summary(["deepseek"], "2026-10-01", "2026-10-31")["per_arm"]["deepseek"][0]["transitions"] == 0


def test_record_backfills_silently_warns_on_a_stable_change_this_cycle_only(trades, tmp_path):
    log = tmp_path / "integrity" / "t.jsonl"
    alerts = []
    trades("deepseek", "2026-11", [_row("2026-11-13T19:00", "a"), _row("2026-11-16T14:00", "b")])
    trades("grok", "2026-11", [_row("2026-11-09T20:00", "p", mid="grok-4.20-0309-reasoning"),
                               _row("2026-11-10T14:00", "q", mid="grok-4.20-0309-reasoning", cycle="C1")])
    new = ft.record_transitions("C9", ["deepseek", "grok"], date(2026, 11, 22),
                                alert=lambda *a, **k: alerts.append((a, k)), log_path=log)
    assert len(new) == 2 and alerts == []                              # history: recorded, no WARN
    assert ft.record_transitions("C9", ["deepseek", "grok"], date(2026, 11, 22),
                                 alert=lambda *a, **k: alerts.append(1), log_path=log) == []
    trades("deepseek", "2026-11", [_row("2026-11-13T19:00", "a"), _row("2026-11-16T14:00", "b"),
                                   _row("2026-11-23T14:00", "c", cycle="C10")])
    trades("grok", "2026-11", [_row("2026-11-10T14:00", "q", mid="grok-4.20-0309-reasoning"),
                               _row("2026-11-23T14:00", "r", mid="grok-4.20-0309-reasoning", cycle="C10")])
    new = ft.record_transitions("C10", ["deepseek", "grok"], date(2026, 11, 23),
                                alert=lambda *a, **k: alerts.append((a, k)), log_path=log)
    assert {(t["model_key"], t["to"]) for t in new} == {("deepseek", "c"), ("grok", "r")}
    assert len(alerts) == 1                                            # stable only, not rotating
    (args, kw) = alerts[0]
    assert args[0] == "WARN" and "Candidate identity event" in args[1]
    assert kw["kind"] == "fingerprint_transition_stable"
    lines = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 4 and all(l["recorded_in_cycle"] in ("C9", "C10") for l in lines)


def test_the_record_matches_the_ledgered_history():
    """Sep-Nov on the committed rows: grok-4.20 11 transitions, deepseek-v4-pro 1."""
    if not (ft.TRADES_DIR / "grok_2026-09.jsonl").exists():
        pytest.skip("trade history not in this checkout")
    for mk, n in (("grok", 11), ("deepseek", 1)):
        rows = [r for r in ft.load_rows(mk, ("2026-09", "2026-10", "2026-11"))
                if (r.get("timestamp") or "") <= "2026-11-20T23:59"]
        assert sum(1 for t in ft.transitions(mk, rows) if t["leg"] == "decision") == n


def test_wiring():
    pipe = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")
    assert "record_fingerprint_transitions(executor.cycle_id" in pipe
    build = (ROOT / "scripts" / "build_monthly_data_layer.py").read_text(encoding="utf-8")
    i = build.index('if month_tag >= "2026-11":')
    assert 'out["fingerprint_transitions"] = month_summary(' in build[i:i + 200]
