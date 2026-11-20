"""Reader audit for administrative decision-log rows (Thursday 2026-11-19, item D).

Governing text: eod_stop_record_and_evaluation_ruling.md §1 and §3. Every
reader of data/trades/*.jsonl must exclude administrative rows from model-call
populations, include them in execution replays (and in the memory feed, §1),
and never crash on their null model-call fields. Each reader is driven here
with an EOD_POSITION_STOP fixture row (nulls) and a BOUNDARY_ADMIN-shaped row
(the 2026-10-23 form: api_success true, latency 0.0, provider marker only).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src import cycle_watch  # noqa: E402
from src.analytics import execution_metrics, intended_vs_expressed, performance  # noqa: E402
from src.analytics import research_metrics  # noqa: E402
from src.alerts import events  # noqa: E402
from src.logging import memory, tick_spacing  # noqa: E402
from src.record_class import (  # noqa: E402
    is_administrative_row, is_model_call_row, last_model_call_row)
from src.reports import daily_report  # noqa: E402

DAY = "2026-11-19"
EOD_CYCLE = "20261119T210512Z"


def _exec(side="SELL", executed=True, shares=2.0, price=95.0, cycle=EOD_CYCLE,
          ticker="KO", order_id=None):
    return {"decision": {"action": side, "ticker": ticker, "target_weight": 0.0,
                         "confidence": 10, "reasoning": "Forced liquidation: POSITION_STOP"},
            "executed": executed, "side": side if executed else "SKIP", "ticker": ticker,
            "shares": shares if executed else 0, "fill_price": price if executed else 0,
            "notional": shares * price if executed else 0,
            "order_id": order_id or f"gpt-{cycle}-00-a0", "error": "", "constraint": "",
            "stop_unprotected": False, "decision_id": f"gpt-{cycle}-stop-{ticker}",
            "broker_order": {"status": "filled" if executed else "canceled",
                             "client_order_id": f"gpt-{cycle}-00-a0"}}


def admin_row(**over):
    r = {"record_class": "ADMINISTRATIVE", "admin_class": "EOD_POSITION_STOP",
         "provider": "administrative", "model_id_configured": "n/a",
         "model_id_returned": "n/a", "cycle_id": EOD_CYCLE,
         "timestamp": "2026-11-19T21:05:40+00:00", "date": DAY, "model_key": "gpt",
         "execution_mode": "broker_paper", "prompt_version": "v4", "month_tag": "2026-11",
         "fetch_started_at": "", "api_success": None, "api_attempt": None,
         "api_latency_seconds": None, "input_tokens": None, "output_tokens": None,
         "thinking_tokens": None, "cost_usd": None, "raw_decisions": [],
         "accepted_decisions": [], "violations": [], "executions": [_exec()],
         "portfolio_after": {"total_value": 1990.0, "cash": 1990.0, "holdings": []},
         "overall_reasoning": "Administrative row (EOD_POSITION_STOP): no model was called."}
    r.update(over)
    return r


def boundary_admin_row():
    """The 2026-10-23 form: provider marker only, a call that never happened."""
    return {"provider": "administrative", "model_id_configured": "n/a",
            "model_id_returned": "n/a", "cycle_id": "20261119T191100Z",
            "timestamp": "2026-11-19T19:11:00+00:00", "date": DAY, "model_key": "gpt",
            "execution_mode": "broker_paper", "fetch_started_at": "", "api_success": True,
            "api_attempt": 1, "api_latency_seconds": 0.0, "raw_decisions": [],
            "accepted_decisions": [], "violations": [], "executions": [],
            "portfolio_after": {"total_value": 2000.0, "holdings": []}}


def model_row(hh, mm, *, ok=True, cycle=None, executions=None, total=2000.0, holdings=None):
    ts = f"{DAY}T{hh:02d}:{mm:02d}:30+00:00"
    cyc = cycle or f"20261119T{hh:02d}{mm:02d}05Z"
    return {"cycle_id": cyc, "timestamp": ts, "date": DAY, "model_key": "gpt",
            "provider": "openai", "model_id_configured": "gpt-5.4",
            "model_id_returned": "gpt-5.4-2026-03-05", "execution_mode": "broker_paper",
            "fetch_started_at": f"{DAY}T{hh:02d}:{mm:02d}:05+00:00",
            "api_success": ok, "api_attempt": 1, "api_latency_seconds": 12.0,
            "input_tokens": 1000, "output_tokens": 100, "data_inputs_hash": f"h{hh}{mm}",
            "raw_decisions": [{"action": "BUY", "ticker": "KO", "target_weight": 0.1}] if ok else [],
            "accepted_decisions": [], "violations": [],
            "executions": executions or [], "overall_reasoning": "model text",
            "portfolio_after": {"total_value": total,
                                "holdings": holdings if holdings is not None else
                                [{"ticker": "KO", "shares": 2.0, "market_value": 190.0,
                                  "current_price": 95.0}]}}


def _write(dir_: Path, book: str, rows, month="2026-11") -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / f"{book}_{month}.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


# ---------------------------------------------------------------- predicate
def test_discriminator_covers_both_eras():
    assert is_administrative_row(admin_row()) and is_administrative_row(boundary_admin_row())
    assert not is_administrative_row(model_row(15, 0)) and not is_administrative_row(None)
    assert is_model_call_row(model_row(15, 0))
    rows = [model_row(15, 0), model_row(15, 30), admin_row()]
    assert last_model_call_row(rows)["timestamp"].startswith(f"{DAY}T15:30")


def test_the_two_committed_boundary_admin_rows_are_recognised():
    rows = [json.loads(l) for l in
            (ROOT / "data" / "trades" / "gemini_2026-10.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    admin = [r for r in rows if is_administrative_row(r)]
    assert len(admin) == 2 and all(r["api_success"] is True for r in admin)
    assert {r["timestamp"][11:16] for r in admin} == {"19:11", "19:36"}


# ---------------------------------------------------- model-call populations
def test_slot_watcher_never_counts_an_admin_row_even_with_a_fetch_stamp():
    slot = datetime(2026, 11, 19, 21, 0, tzinfo=timezone.utc)
    stamped = admin_row(fetch_started_at="2026-11-19T21:05:05+00:00")
    lines = {"gpt": [json.dumps(stamped)]}
    assert cycle_watch.books_in_slot(lines, slot).get("gpt", 0) == 0


def test_session_verifier_lists_admin_rows_as_non_ticks(tmp_path):
    import verify_session_cycles as vsc
    _write(tmp_path, "gpt", [model_row(15, 0), admin_row(fetch_started_at="2026-11-19T21:05:05Z")])
    ticks, non = vsc.read_ticks(datetime(2026, 11, 19).date(), tmp_path)
    assert sum(len(v) for v in ticks["gpt"].values()) == 1 and len(non["gpt"]) == 1


def test_completeness_alert_ignores_admin_rows(monkeypatch):
    rows = [model_row(14, 30 * (i % 2)) for i in range(5)] + [admin_row(), boundary_admin_row()]
    monkeypatch.setattr(events, "_read_today_trade_records", lambda k, d: rows)
    monkeypatch.setattr(events, "_enabled_model_keys", lambda s: ["gpt"])
    out = events.detect_completeness_degradation({"alerts": {"daily_completeness_min": 0.9}}, DAY)
    assert out == []        # 5/5 model calls succeeded; the admin row is no failure


def test_cost_excludes_admin_rows(tmp_path, monkeypatch):
    _write(tmp_path, "gpt", [model_row(15, 0), admin_row(), boundary_admin_row()])
    monkeypatch.setattr(performance, "TRADES_DIR", tmp_path)
    s = performance.compute_api_cost_summary("gpt")
    assert s["calls"] == 1 and s.get("unknown_cost_calls", 0) == 0


def test_spacing_last_decided_skips_a_trailing_admin_row(tmp_path):
    _write(tmp_path, "gpt", [model_row(15, 0), model_row(15, 30), admin_row()])
    scan = tick_spacing._scan_logs({"models": {"gpt": {"enabled": True}}},
                                   datetime(2026, 11, 19, 21, 10, tzinfo=timezone.utc), tmp_path)
    assert scan.usable == 1 and scan.torn == () and scan.raised == ()
    assert scan.latest == datetime(2026, 11, 19, 15, 30, 30, tzinfo=timezone.utc)


def test_spacing_book_with_only_admin_rows_contributes_no_decision(tmp_path):
    _write(tmp_path, "gpt", [boundary_admin_row()])
    scan = tick_spacing._scan_logs({"models": {"gpt": {"enabled": True}}},
                                   datetime(2026, 11, 19, 21, 10, tzinfo=timezone.utc), tmp_path)
    assert scan.usable == 1 and scan.latest is None


def test_daily_report_health_uses_the_last_model_call_row(tmp_path, monkeypatch):
    _write(tmp_path, "gpt", [model_row(15, 30), admin_row()])
    monkeypatch.setattr(daily_report, "TRADES_DIR", tmp_path)
    rec = daily_report._read_today_trade_record("gpt", datetime(2026, 11, 19))
    assert rec["api_success"] is True and rec["timestamp"].startswith(f"{DAY}T15:30")


def test_rq1_never_takes_an_admin_row():
    rows = {"gpt": [boundary_admin_row(), admin_row()], "grok": []}
    out = research_metrics.compute_rq1(rows, {}, ["gpt", "grok"], n_permutations=1)
    assert isinstance(out, dict)       # ran; admin rows contributed no decision set


def test_rq5_admin_row_moves_the_book_but_is_no_observation():
    held = [{"ticker": "KO", "shares": 2.0, "market_value": 190.0, "current_price": 95.0}]
    rows = [model_row(15, 0, holdings=held), model_row(15, 30, holdings=held),
            admin_row(portfolio_after={"total_value": 1990.0, "holdings": []})]
    obs = research_metrics._rq5_trade_panel(rows, DAY)
    assert len(obs) == 1 and obs[0]["tick_pos"] == 2


# -------------------------------------------- execution replays include them
def test_gexec_scores_the_admin_rows_forced_execution():
    out = execution_metrics.score_cycles([admin_row()], {EOD_CYCLE: True})
    assert out["cycles"] == 1 and out["successes"] == 1


def test_intended_vs_expressed_takes_nav_but_no_cycle_from_admin_rows():
    row = intended_vs_expressed.session_row([model_row(15, 0), admin_row()])
    assert row["cycles"] == 1 and row["nav_close"] == 1990.0
    assert row["expressed_usd"] == 0.0 and row["gap_usd"] == 0.0   # forced, not intent


def test_defab_replay_keeps_the_admin_rows_fills(tmp_path, monkeypatch):
    import defab_clean_window as defab
    _write(tmp_path / "data" / "trades", "gpt", [model_row(15, 0), admin_row()])
    monkeypatch.setattr(defab, "ROOT", str(tmp_path))
    runs = defab.load_runs("gpt", ["2026-11"])
    assert len(runs) == 2 and is_administrative_row(runs[-1])


def test_book_conservation_counts_the_admin_rows_fills(tmp_path):
    from src.execution import reconciliation
    _write(tmp_path, "gpt", [admin_row()])
    fills = reconciliation._book_fills("gpt", DAY, False, tmp_path)
    assert fills == [("KO", -2.0, 95.0)]


def test_memory_feed_shows_an_eod_stop_like_an_intraday_stop(tmp_path, monkeypatch):
    """§1: an EOD stop appears in the model's recent decisions exactly as an
    intraday stop does, and the null model-call fields crash nothing."""
    _write(tmp_path, "gpt", [model_row(15, 0), admin_row()])
    monkeypatch.setattr(memory, "TRADES_DIR", tmp_path)
    recent = memory.read_recent_decisions("gpt", limit=5,
                                          now=datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc))
    assert any(d.get("ticker") == "KO" and str(d.get("action", d.get("side", ""))).upper() == "SELL"
               for d in recent)
