"""Pins the day-one cash defect flag (executor sizing-floor ruling, section 4.4)
on every surface that renders book cash, and the disclosure artifacts it points
to. Surface list: docs/registrations/executor_sizing_floor_ruling.md 4.4 and
ledger operational_events['2026-11'].executor_floor_defect_2026_11_06.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from src.reports import cash_defect_flag as cdf

ROOT = Path(__file__).resolve().parent.parent
COHORT = ("claude", "claude_opus", "gemini", "gpt", "grok")
FLAG_TEXT = "figures dominated by orders suppressed by an executor defect; see ledger"


# ---------------------------------------------------------------- the flag itself

def test_flag_text_and_scope_are_pinned():
    assert cdf.FLAG_TEXT == FLAG_TEXT
    assert cdf.FLAGGED_DATES == frozenset({"2026-11-06"})
    assert cdf.FLAGGED_BOOKS == frozenset(COHORT)
    for book in COHORT:
        f = cdf.cash_flag(book, "2026-11-06")
        assert f["text"] == FLAG_TEXT and f["code"] == "EXECUTOR_DEFECT_2026_11_06"
        assert cdf.cash_flag(book, "2026-11-05") is None
        assert cdf.cash_flag(book, "2026-11-09") is None
    # DeepSeek hit the floor zero times that day: flagging it would be false.
    assert cdf.cash_flag("deepseek", "2026-11-06") is None
    assert cdf.cash_flag("claude", None) is None


def test_window_flags_cover_aggregates_that_include_the_day():
    keys = list(COHORT) + ["deepseek"]
    assert set(cdf.window_flags(keys, "2026-11-01", "2026-11-30")) == set(COHORT)
    assert cdf.window_flags(keys, "2026-04-23", "2026-12-31")  # RQ5-style accumulating window
    assert cdf.window_flags(keys, "2026-10-01", "2026-10-31") == {}
    assert cdf.window_flags(keys, "2026-11-09", "2026-11-30") == {}


def test_footnote_names_the_ledger_entry():
    note = cdf.footnote()
    assert FLAG_TEXT in note and "executor_floor_defect_2026_11_06" in note
    assert note.startswith(cdf.MARKER)


def test_ledger_ref_resolves_to_a_real_entry():
    ledger = json.loads((ROOT / "scripts/phase_a_integrity_ledger.json").read_text(encoding="utf-8"))
    ids = [e["id"] for e in ledger["operational_events"]["2026-11"]]
    assert "executor_floor_defect_2026_11_06" in ids
    assert "operational_events['2026-11'].executor_floor_defect_2026_11_06" in cdf.LEDGER_REF


# ---------------------------------------------------------------- daily report

class _Snap:
    def __init__(self, cash_pct):
        self._c = cash_pct

    def snapshot(self, _prices):
        return {"holdings": [], "cash_pct": self._c}


def _records(cash_pct):
    return [{
        "api_success": True,
        "overall_reasoning": "x",
        "executions": [{"executed": True, "side": "BUY", "ticker": "KO", "shares": 1.5,
                        "fill_price": 94.0, "notional": 141.0,
                        "decision": {"confidence": 6, "reasoning": "r"}}],
        "portfolio_after": {"cash_pct": cash_pct,
                            "holdings": [{"ticker": "KO", "weight": 0.07}]},
    }]


@pytest.mark.parametrize("key,date,flagged", [
    ("claude_opus", "2026-11-06", True),
    ("claude_opus", "2026-11-09", False),
    ("deepseek", "2026-11-06", False),
])
def test_daily_breakdown_flags_day_one_cash(monkeypatch, key, date, flagged):
    from src.reports import daily_report as dr
    monkeypatch.setattr(dr, "_read_today_trade_records", lambda k, d: _records(0.807))
    monkeypatch.setattr(dr, "load_portfolio", lambda k: _Snap(0.807))
    text = dr._model_breakdown(key, datetime.strptime(date, "%Y-%m-%d"))
    assert ("Cash level is defect-flagged: " + FLAG_TEXT in text) is flagged
    # A flagged cash level is never read as behavior.
    assert ("heavy defensive cash positioning" in text) is (not flagged)


def test_daily_performance_table_marks_cash_and_adds_footnote(monkeypatch):
    from src.reports import daily_report as dr
    monkeypatch.setattr(dr, "canonical_spy_return", lambda s: 0.0)
    monkeypatch.setattr(dr, "compute_metrics", lambda k, settings, spy_return: {
        "days": 1, "current_value": 2000.0, "daily_pnl_pct": None, "cumulative_return": 0.0,
        "alpha_vs_spy": None, "current_cash_pct": 0.8074})
    monkeypatch.setattr(dr, "_daily_pnl", lambda k: (0.0, None))
    monkeypatch.setattr(dr, "_read_today_trade_records", lambda k, d: [])
    settings = {"models": {k: {"display_name": k} for k in ("claude_opus", "deepseek")}}
    table, rows = dr._build_performance_table(["claude_opus", "deepseek"],
                                              datetime(2026, 11, 6), settings)
    opus = next(l for l in table.splitlines() if "claude_opus" in l and l.startswith("|"))
    ds = next(l for l in table.splitlines() if "deepseek" in l and l.startswith("|"))
    assert "80.74% ‡" in opus and "‡" not in ds
    assert cdf.footnote() in table
    table2, _ = dr._build_performance_table(["claude_opus"], datetime(2026, 11, 9), settings)
    assert "‡" not in table2 and FLAG_TEXT not in table2


def test_committed_day_one_report_carries_the_flag():
    text = (ROOT / "reports/daily/2026-11-06.md").read_text(encoding="utf-8")
    perf = text.split("## Model Performance", 1)[1].split("**API Cost Today:**", 1)[0]
    table = [l for l in perf.splitlines() if re.search(r"^\| \d+ \|", l)]
    assert len(table) == 6
    flagged = [l for l in table if l.rstrip().endswith("‡ |")]
    assert len(flagged) == 5
    assert not any("DeepSeek" in l for l in flagged)
    assert cdf.footnote({"2026-11-06"}) in text
    assert text.count("Cash level is defect-flagged: " + FLAG_TEXT) == 5
    assert "heavy defensive cash positioning" not in text
    meta = json.loads(re.search(r"<!-- METADATA\n(.*)\n-->", text.replace("\r\n", "\n"), re.S).group(1))
    codes = {m["key"]: m.get("cash_flag") for m in meta["models"]}
    assert {k for k, v in codes.items() if v == "EXECUTOR_DEFECT_2026_11_06"} == set(COHORT)
    assert codes["deepseek"] is None


# ---------------------------------------------------------------- dashboard

def test_dashboard_annotation_dates_each_surface_by_its_own_source():
    from src.dashboard.build_data import _state_session_date, annotate_cash_flags
    # 22:07Z Friday is 17:07 ET Friday; 14:35Z Monday is Monday.
    assert _state_session_date("2026-11-06 22:07:30") == "2026-11-06"
    assert _state_session_date("2026-11-09 14:35:00") == "2026-11-09"
    assert _state_session_date("") is None
    lb = [{"model_key": "claude_opus"}, {"model_key": "deepseek"}]
    pf = [{"model_key": "claude_opus"}, {"model_key": "deepseek"}]
    # Monday morning: the leaderboard's cash is still Friday's EOD, the live book is not.
    annotate_cash_flags(lb, pf, {"claude_opus": "2026-11-06", "deepseek": "2026-11-06"},
                        {"claude_opus": "2026-11-09", "deepseek": "2026-11-09"})
    assert lb[0]["cash_defect_flag"]["text"] == FLAG_TEXT
    assert "cash_defect_flag" not in lb[1]
    assert "cash_defect_flag" not in pf[0] and "cash_defect_flag" not in pf[1]


def test_dashboard_frontend_renders_the_flag_at_every_cash_site():
    js = (ROOT / "dashboard/js/app.js").read_text(encoding="utf-8")
    assert "function cashFlagMark(obj)" in js and "cash_defect_flag" in js
    assert "fmtPct(row.current_cash_pct, false) + cashFlagMark(row)" in js
    assert "${fmtPct(p.cash_pct, false)})${cashFlagMark(p)}" in js
    assert "fmtPct(rowA.current_cash_pct, false) + cashFlagMark(rowA)" in js
    assert "fmtPct(rowB.current_cash_pct, false) + cashFlagMark(rowB)" in js
    # No cash rendering left unflagged.
    for m in re.finditer(r"fmtPct\((\w+)\.(current_cash_pct|cash_pct), false\)", js):
        tail = js[m.end():m.end() + 40]
        assert "cashFlagMark" in tail, js[m.start():m.end() + 40]


# ---------------------------------------------------------------- alerts

def test_macro_alert_total_cash_inherits_the_flag(monkeypatch):
    from src.alerts import events
    monkeypatch.setattr(events, "_enabled_model_keys", lambda s: ["claude_opus", "deepseek"])
    monkeypatch.setattr(events, "_read_dashboard_portfolios", lambda: [
        {"model_key": "claude_opus", "cash": 1600.0, "total_value": 2000.0, "holdings": [],
         "cash_defect_flag": cdf.cash_flag("claude_opus", "2026-11-06")},
        {"model_key": "deepseek", "cash": 50.0, "total_value": 120000.0, "holdings": []},
    ])
    pos = events._models_positioning({})
    assert pos["cash_defect_flag"]["text"] == FLAG_TEXT
    monkeypatch.setattr(events, "_read_dashboard_portfolios", lambda: [
        {"model_key": "deepseek", "cash": 50.0, "total_value": 120000.0, "holdings": []}])
    assert events._models_positioning({})["cash_defect_flag"] is None


# ---------------------------------------------------------------- monthly builder

def _layer():
    return {"profiles": [{"model": k, "evidence_metrics": {"cash_pct_mean_daily": 0.5}}
                         for k in COHORT + ("deepseek",)],
            "methodology_data_integrity_rq": {"rq_update": {"accumulating_inputs": {"RQ5": {}}}}}


def test_monthly_builder_flags_november_and_leaves_earlier_months_untouched():
    from scripts.build_monthly_data_layer import _apply_cash_defect_flags
    keys = list(COHORT) + ["deepseek"]
    oct_layer = _layer()
    before = json.dumps(oct_layer, sort_keys=True)
    _apply_cash_defect_flags(oct_layer, keys, "2026-10-01", "2026-10-31", "2026-04-23")
    assert json.dumps(oct_layer, sort_keys=True) == before  # byte-unchanged: reproduce-May class

    nov = _layer()
    _apply_cash_defect_flags(nov, keys, "2026-11-01", "2026-11-30", "2026-04-23")
    prof = {p["model"]: p["evidence_metrics"] for p in nov["profiles"]}
    for k in COHORT:
        assert prof[k]["cash_pct_mean_daily_defect_flag"]["text"] == FLAG_TEXT
    assert "cash_pct_mean_daily_defect_flag" not in prof["deepseek"]
    rq5 = nov["methodology_data_integrity_rq"]["rq_update"]["accumulating_inputs"]["RQ5"]
    assert set(rq5["cash_pct_defect_flag"]) == set(COHORT)
    assert nov["cash_defect_flags"]["books"] == sorted(COHORT)

    # December: the month mean no longer includes 11-06, the accumulating RQ5 still does.
    dec = _layer()
    _apply_cash_defect_flags(dec, keys, "2026-12-01", "2026-12-31", "2026-04-23")
    assert all("cash_pct_mean_daily_defect_flag" not in p["evidence_metrics"] for p in dec["profiles"])
    assert set(dec["methodology_data_integrity_rq"]["rq_update"]["accumulating_inputs"]["RQ5"]
               ["cash_pct_defect_flag"]) == set(COHORT)


# ---------------------------------------------------------------- behavioral summary

def test_behavioral_summary_flags_cash_stats_over_a_flagged_window():
    from scripts.behavioral_summary import Window, WindowResult, render_window
    row = dict(tpd=1.0, cavg=50.0, cmin=40.0, cmax=80.0, hold_mean=None, hold_med=None,
               same_day=0, n_closed=0, fr=None, cdist={}, conf_total=0, conf_mean=None,
               low=(None, 0), mid=(None, 0), high=(None, 0), overall=(None, 0), corr=None)
    res = WindowResult(Window("2026-11-01", "2026-11-30", "Nov"),
                       {"claude_opus": dict(row), "deepseek": dict(row)}, [])
    out = "\n".join(render_window(res, {"models": {}}))
    opus = next(l for l in out.splitlines() if l.startswith("| claude_opus"))
    ds = next(l for l in out.splitlines() if l.startswith("| deepseek"))
    assert opus.count("‡") == 3 and "‡" not in ds
    assert FLAG_TEXT in out
    oct_res = WindowResult(Window("2026-10-01", "2026-10-31", "Oct"), {"claude_opus": dict(row)}, [])
    assert "‡" not in "\n".join(render_window(oct_res, {"models": {}}))


# ---------------------------------------------------------------- disclosure artifacts

def test_annotation_file_and_g_exec_recompute_agree_with_the_ledger():
    rows = [json.loads(l) for l in
            (ROOT / "data/annotations/executor_defect_2026-11-06.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 140
    assert all(r["annotation"] == "EXECUTOR_DEFECT_SUPPRESSED" for r in rows)
    per_book = Counter(r["book"] for r in rows)
    assert per_book == {"claude": 40, "claude_opus": 51, "gemini": 28, "gpt": 14, "grok": 7}
    cf = [r for r in rows if "counterfactual" in r]
    assert len(cf) == 12 and all(r["cash_below_one_share"] for r in cf)
    assert all(r["key_type"] == "composite" and r["decision_id"] is None for r in rows)
    assert len({r["record_key"] for r in rows}) == 140

    g = json.loads((ROOT / "data/annotations/g_exec_recompute_2026-11-06.json").read_text(encoding="utf-8"))
    assert g["failures_total"] == 140
    for book, n in per_book.items():
        b = g["books"][book]
        assert b["original"]["execution_success_rate"] == 1.0
        assert b["orders"]["suppressed_untagged_skips"] == n
        assert b["session_classification"] == "decision-estimable (execution-impaired)"

    ledger = json.loads((ROOT / "scripts/phase_a_integrity_ledger.json").read_text(encoding="utf-8"))
    entry = next(e for e in ledger["operational_events"]["2026-11"]
                 if e["id"] == "executor_floor_defect_2026_11_06")
    table = entry["intended_vs_expressed"]["per_book"]
    for book, n in per_book.items():
        assert table[book]["orders_suppressed"] == n
        assert entry["g_exec"]["per_book"][book]["recomputed"] == pytest.approx(
            g["books"][book]["recomputed"]["execution_success_rate"])


def test_annotation_artifacts_rebuild_byte_identically_from_the_logs():
    from scripts.executor_defect_disclosure import main
    assert main(["--check"]) == 0
