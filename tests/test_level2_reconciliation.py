"""Level-2 (broker-authoritative) reconciliation and the Gate 4 metric.

Level 2 compares the AGGREGATE pipeline ledger against the venue's own account
state each cycle. These tests pin the properties that make it a measurement
rather than a formality:

  * the cash comparison includes the unallocated baseline (live: the $500
    master reserve), so a correct ledger does not raise G-BOOK on cycle one;
  * short positions sign-match the venue's negative qty, and signed shares net
    across books;
  * HALT-never-repair: a divergence halts every enabled book and never adjusts
    a book's cash or holdings toward the broker;
  * Gate 4 scores a MISSING reconciliation record as a failed cycle, and a
    deliberately induced submission failure is classified as a failure (the
    registered October obligation tests the gate itself, not just the venue).

No network: a fake broker returns scripted account state.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config_loader
from src.analytics import execution_metrics as em
from src.execution import reconciliation as rec
from src.portfolio.portfolio import Holding, Portfolio

TOL = 1.00


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    """No test may touch real state, alert logs or the reconciliation dir --
    including when the implementation under test is broken. (A mutation run
    of this file once wrote data/state/*.json and the alert logs, because a
    divergence path reached the real save_portfolio / send_alert.)"""
    monkeypatch.setattr(rec, "save_portfolio", lambda p: None)
    monkeypatch.setattr(rec, "RECONCILIATION_DIR", tmp_path / "reconciliation")
    import src.alerts.alerter as alerter
    monkeypatch.setattr(alerter, "send_alert", lambda *a, **k: None)


class FakeBroker:
    def __init__(self, cash, positions=()):
        self._cash = cash
        self._positions = list(positions)

    def get_account(self):
        return {"cash": str(self._cash)}

    def get_positions(self):
        return [{"symbol": s, "qty": str(q)} for s, q in self._positions]


def book(key, cash, **holdings):
    return Portfolio(model_key=key, cash=cash, holdings={
        t: Holding(ticker=t, shares=q, avg_cost=100.0) for t, q in holdings.items()})


def run(portfolios, broker, unallocated=500.0, tol=TOL):
    return rec.reconcile(portfolios, broker, "cyc1", cash_tolerance_usd=tol,
                         unallocated_cash_usd=unallocated)


# ------------------------------------------------------------------ reconcile
def test_clean_when_ledger_plus_unallocated_matches_broker():
    ps = [book("a", 1000.0, AAPL=2.0), book("b", 1500.0)]
    r = run(ps, FakeBroker(3000.0, [("AAPL", 2.0)]))
    assert r.clean and r.ledger_cash_total == 3000.0 and r.unallocated_cash == 500.0


def test_omitting_the_unallocated_baseline_would_diverge():
    """Documents why the baseline exists: books alone != venue cash."""
    r = run([book("a", 1000.0)], FakeBroker(1500.0), unallocated=0.0)
    assert [e.kind for e in r.events] == ["cash"]
    assert r.events[0].delta == pytest.approx(-500.0)


def test_cash_within_tolerance_is_clean_beyond_is_event():
    ps = [book("a", 1000.0)]
    assert run(ps, FakeBroker(1500.99)).clean
    r = run(ps, FakeBroker(1502.50))
    assert len(r.events) == 1 and r.events[0].kind == "cash"
    assert r.events[0].tolerance == TOL


def test_position_the_venue_holds_that_no_book_claims():
    r = run([book("a", 1000.0)], FakeBroker(1500.0, [("TSLA", 3.0)]))
    assert [(e.kind, e.ticker, e.delta) for e in r.events] == [("position", "TSLA", -3.0)]


def test_position_a_book_claims_that_the_venue_lacks():
    r = run([book("a", 1000.0, MSFT=4.0)], FakeBroker(1500.0))
    assert [(e.kind, e.ticker, e.delta) for e in r.events] == [("position", "MSFT", 4.0)]


def test_short_signs_match_and_signed_shares_net_across_books():
    ps = [book("a", 1000.0, NVDA=5.0), book("b", 1000.0, NVDA=-3.0),
          book("c", 1000.0, AMD=-2.0)]
    r = run(ps, FakeBroker(3500.0, [("NVDA", 2.0), ("AMD", -2.0)]))
    assert r.clean
    # a short the venue shows as long is a divergence, not a sign convention
    r2 = run([book("c", 1000.0, AMD=-2.0)], FakeBroker(1500.0, [("AMD", 2.0)]))
    assert r2.events[0].delta == pytest.approx(-4.0)


def test_dust_below_ghost_epsilon_is_not_a_position():
    ps = [book("a", 1000.0, AAPL=0.004), book("b", 1000.0, AAPL=0.004)]
    assert run(ps, FakeBroker(2500.0)).clean   # 0.008 < GHOST_SHARES_EPSILON


def test_share_class_ticker_maps_from_venue_spelling():
    r = run([book("a", 1000.0, **{"BRK-B": 1.0})], FakeBroker(1500.0, [("BRK.B", 1.0)]))
    assert r.clean


# --------------------------------------------------------- HALT, never repair
def test_divergence_halts_every_book_and_never_repairs(monkeypatch):
    saved = []
    monkeypatch.setattr(rec, "save_portfolio", lambda p: saved.append(p.model_key))
    ps = [book("a", 1000.0, AAPL=2.0), book("b", 1500.0)]
    r = run(ps, FakeBroker(9999.0, [("AAPL", 2.0)]))
    alerts = []
    rec.handle_reconciliation_result(r, ps, lambda *a, **k: alerts.append((a, k)))
    assert all(p.halted for p in ps) and sorted(saved) == ["a", "b"]
    # nothing was adjusted toward the broker
    assert ps[0].cash == 1000.0 and ps[1].cash == 1500.0
    assert ps[0].holdings["AAPL"].shares == 2.0
    assert len(alerts) == 1 and alerts[0][0][0] == "CRITICAL"
    assert alerts[0][1]["kind"] == "gbook_divergence"


def test_clean_result_halts_nothing_and_alerts_nothing(monkeypatch):
    monkeypatch.setattr(rec, "save_portfolio", lambda p: pytest.fail("saved on clean"))
    ps = [book("a", 1000.0)]
    alerts = []
    rec.handle_reconciliation_result(run(ps, FakeBroker(1500.0)), ps,
                                     lambda *a, **k: alerts.append(a))
    assert not ps[0].halted and alerts == []


def test_record_is_append_only_one_line_per_cycle(tmp_path):
    r = run([book("a", 1000.0)], FakeBroker(1500.0))
    p = rec.record_reconciliation(r, tmp_path)
    rec.record_reconciliation(r, tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["clean"] is True


def test_post_cycle_hook_reloads_books_and_uses_config_baseline(tmp_path, monkeypatch):
    import src.portfolio.portfolio as pf
    monkeypatch.setattr(pf, "load_portfolio", lambda k: book(k, 1000.0))
    monkeypatch.setattr(rec, "RECONCILIATION_DIR", tmp_path)
    monkeypatch.setattr(config_loader, "load_settings", lambda: {
        "mode": "live", "capital_structure": {"reserve_master_usd": 500},
        "reconciliation": {"cash_tolerance_usd": 1.0}})
    r = rec.run_post_cycle_reconciliation(FakeBroker(2500.0), "cyc9", ["a", "b"])
    assert r.clean and r.unallocated_cash == 500.0
    assert (tmp_path / f"{r.timestamp[:7]}.jsonl").exists()


# ------------------------------------------------------------ config baseline
def test_broker_paper_baseline_unset_raises_rather_than_guessing():
    s = {"mode": "broker_paper", "reconciliation": {
        "cash_tolerance_usd": 1.0, "unallocated_cash_usd": {"broker_paper": None}}}
    with pytest.raises(config_loader.PendingReconciliationBaselineError):
        config_loader.reconciliation_params(s)
    s["reconciliation"]["unallocated_cash_usd"]["broker_paper"] = 90000
    assert config_loader.reconciliation_params(s)["unallocated_cash_usd"] == 90000.0


def test_live_baseline_is_derived_from_the_reserve_not_configurable():
    s = {"mode": "live", "capital_structure": {"reserve_master_usd": 500},
         "reconciliation": {"cash_tolerance_usd": 1.0,
                            "unallocated_cash_usd": {"live": 12345}}}
    assert config_loader.reconciliation_params(s)["unallocated_cash_usd"] == 500.0


def test_shipped_settings_resolve_for_live():
    p = config_loader.reconciliation_params(config_loader.load_settings(), "live")
    assert p == {"cash_tolerance_usd": 1.0, "unallocated_cash_usd": 500.0}


# -------------------------------------------------------------- Gate 4 metric
def fill(status="filled", **kw):
    return {"side": "BUY", "order_id": "LLM-x", "constraint": "", "error": "",
            "broker_order": {"status": status}, **kw}


@pytest.mark.parametrize("execution,expected", [
    (fill("filled"), "ok:terminal_filled"),
    (fill("canceled", constraint="UNFILLED_AT_DEADLINE"), "ok:terminal_canceled"),
    (fill("rejected"), "ok:terminal_rejected_unclassified"),
    (fill("accepted"), "fail:unresolved_status"),
    (fill("pending_cancel"), "fail:unresolved_status"),
    ({"side": "SKIP", "order_id": "EXEC_CONSTRAINT_UNSETTLED_FUNDS",
      "constraint": "UNSETTLED_FUNDS"}, "ok:constraint_UNSETTLED_FUNDS"),
    ({"side": "SKIP", "order_id": "", "constraint": "BELOW_VENUE_MINIMUM"},
     "ok:constraint_BELOW_VENUE_MINIMUM"),
    ({"side": "SKIP", "order_id": "LLM-y", "constraint": "WASH_REJECT_POST_BARRIER"},
     "ok:constraint_WASH_REJECT_POST_BARRIER"),
    ({"side": "SKIP", "order_id": "LLM-y", "constraint": "WASH_TRADE_BLOCK"},
     "fail:wash_trade_unmediated"),
    # the induced-submission-failure case: id minted, no venue order, no reason
    ({"side": "SKIP", "order_id": "LLM-z", "constraint": "", "error": "HTTP 503",
      "broker_order": None}, "fail:submission_failed"),
    ({"side": "BUY", "order_id": "LLM-s", "stop_unprotected": True,
      "broker_order": None}, "fail:stop_unprotected"),
    ({"side": "HOLD", "order_id": ""}, "no_order"),
    ({"side": "SKIP", "order_id": "", "constraint": "", "error": "sizing"}, "no_order"),
])
def test_classify_execution(execution, expected):
    assert em.classify_execution(execution) == expected


def cycle(cid, executions, mode="broker_paper", api_success=True):
    return {"cycle_id": cid, "execution_mode": mode, "api_success": api_success,
            "executions": executions}


def test_cycle_scoring_success_missing_diverged_and_induced_failure():
    recon = {"c1": True, "c2": False, "c4": True}        # c3 has NO record
    recs = [
        cycle("c1", [fill()]),                                        # success
        cycle("c2", [fill()]),                                        # diverged
        cycle("c3", [fill()]),                                        # recon missing
        cycle("c4", [{"side": "SKIP", "order_id": "LLM-z", "constraint": "",
                      "error": "HTTP 503", "broker_order": None}]),   # induced failure
    ]
    s = em.score_cycles(recs, recon)
    assert (s["cycles"], s["successes"]) == (4, 1)
    assert s["execution_success_rate"] == 0.25 and s["passes_gate_4"] is False
    assert s["failure_cycles_by_reason"] == {
        "reconciliation_diverged": 1, "reconciliation_missing": 1, "submission_failed": 1}


def test_paper_mode_ignored_and_failed_model_call_excluded_not_failed():
    recs = [cycle("c1", [fill()], mode="paper"),
            cycle("c2", [], api_success=False),
            cycle("c3", [])]                       # no orders, clean recon -> success
    s = em.score_cycles(recs, {"c2": True, "c3": True})
    assert (s["cycles"], s["successes"], s["excluded_no_decision"]) == (1, 1, 1)


def test_no_broker_cycles_is_not_a_pass():
    s = em.score_cycles([cycle("c1", [fill()], mode="paper")], {})
    assert s["execution_success_rate"] is None and s["passes_gate_4"] is None


def test_threshold_boundary_is_inclusive():
    recs = [cycle(f"c{i}", []) for i in range(5)]
    ok = {f"c{i}": (i != 0) for i in range(5)}      # 4 of 5 clean = 0.80
    s = em.score_cycles(recs, ok)
    assert s["execution_success_rate"] == 0.8 and s["passes_gate_4"] is True


def test_duplicate_recon_records_for_a_cycle_are_clean_only_if_all_clean(tmp_path):
    (tmp_path / "2026-11.jsonl").write_text(
        json.dumps({"cycle_id": "c1", "clean": True}) + "\n" +
        json.dumps({"cycle_id": "c1", "clean": False}) + "\n" +
        json.dumps({"cycle_id": "c2", "clean": True}) + "\n", encoding="utf-8")
    assert em.load_reconciliation(tmp_path) == {"c1": False, "c2": True}


def test_compute_execution_metrics_end_to_end(tmp_path):
    trades, recon_dir = tmp_path / "trades", tmp_path / "recon"
    trades.mkdir(); recon_dir.mkdir()
    (trades / "gpt_2026-11.jsonl").write_text(
        json.dumps(cycle("c1", [fill()])) + "\n", encoding="utf-8")
    (recon_dir / "2026-11.jsonl").write_text(
        json.dumps({"cycle_id": "c1", "clean": True}) + "\n", encoding="utf-8")
    out = em.compute_execution_metrics("2026-11", ["gpt", "grok"], trades, recon_dir)
    assert out["gpt"]["execution_success_rate"] == 1.0 and out["gpt"]["passes_gate_4"] is True
    assert out["grok"]["passes_gate_4"] is None      # no file -> no evidence, not a pass
