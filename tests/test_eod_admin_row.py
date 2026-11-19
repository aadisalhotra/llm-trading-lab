"""The EOD administrative row (eod_stop_record_and_evaluation_ruling.md §1, §4).

An EOD pass whose stop sweep produces any forced result -- executed, rejected,
or left unexecuted -- writes one ADMINISTRATIVE row to the book's decision
log, with every model-call field null, after confirming every order terminal.
EOD_STOP_NOT_EXECUTABLE (cases A/B) never raises stop_unprotected; an order
still open (case C) is a CRITICAL recorded on the row.
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import src.pipeline as pipeline
from src.execution.executor import ExecutionResult
from src.logging import decision_log
from src.record_class import EOD_STOP_NOT_EXECUTABLE, is_administrative_row

ET = ZoneInfo("America/New_York")
RUN = datetime(2026, 11, 19, 16, 5, tzinfo=ET)
CYC = "20261119T210512Z"
SETTINGS = {"prompt_version": "v4", "experiment_start_date": "2026-04-09",
            "mode": "broker_paper", "models": {"gpt": {"enabled": True}}}


def _result(executed, status=None, constraint="", unprotected=None, order_id=f"gpt-{CYC}-00-a0"):
    d = {"action": "SELL", "ticker": "KO", "target_weight": 0.0, "confidence": 10,
         "reasoning": "Forced liquidation: POSITION_STOP"}
    bo = {"client_order_id": order_id, "status": status} if status else None
    r = ExecutionResult(decision=d, executed=executed, side="SELL" if executed else "SKIP",
                        ticker="KO", shares=2.0 if executed else 0,
                        fill_price=80.0 if executed else 0,
                        notional=160.0 if executed else 0, order_id=order_id,
                        constraint=constraint, broker_order=bo,
                        stop_unprotected=(not executed) if unprotected is None else unprotected)
    r.decision_id = f"gpt-{CYC}-stop-KO"
    return r


class _Order:
    def __init__(self, status):
        self.status = status

    @property
    def is_terminal(self):
        return self.status in ("filled", "canceled", "rejected", "expired")

    def to_dict(self):
        return {"status": self.status, "client_order_id": f"gpt-{CYC}-00-a0"}


def _executor(final_status="filled", venue=True, broken=False, clock_open=False):
    """`broken` breaks the order re-read; `clock_open` is what the venue clock
    says (None = the clock read fails)."""
    class B:
        def get_order_by_client_id(self, oid):
            if broken:
                raise ConnectionError("venue down")
            return _Order(final_status)

        def _request(self, method, path, params=None, body=None):
            assert (method, path) == ("GET", "/v2/clock")
            if clock_open is None:
                raise ConnectionError("clock down")
            return {"is_open": clock_open, "timestamp": "2026-11-19T16:05:12-05:00",
                    "next_open": "2026-11-20T09:30:00-05:00"}
    return SimpleNamespace(cycle_id=CYC, broker=B() if venue else None,
                           on_venue=lambda k: venue)


@pytest.fixture
def tmp_trades(tmp_path, monkeypatch):
    monkeypatch.setattr(decision_log, "TRADES_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "effective_mode", lambda k, s: "broker_paper")
    sent = []
    monkeypatch.setattr(pipeline, "send_alert", lambda sev, title, *a, **k: sent.append((sev, title)))
    return tmp_path, sent


def _write(results, executor, tmp):
    row = pipeline._write_eod_admin_row(
        "gpt", None, results, {"KO": (2.0, 100.0)}, {"KO": 80.0}, executor, SETTINGS, RUN,
        {"total_value": 1990.0, "holdings": []}, [], pipeline.logging.getLogger("t"))
    lines = (tmp / "gpt_2026-11.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0]) == row
    return row


def test_executed_stop_row_shape(tmp_trades):
    tmp, sent = tmp_trades
    row = _write([_result(True, "filled")], _executor("filled"), tmp)
    assert is_administrative_row(row) and row["record_class"] == "ADMINISTRATIVE"
    assert row["admin_class"] == "EOD_POSITION_STOP" and row["provider"] == "administrative"
    assert row["model_id_configured"] == row["model_id_returned"] == "n/a"
    assert row["cycle_id"] == CYC and row["fetch_started_at"] == ""
    for f in ("api_success", "api_attempt", "api_latency_seconds", "input_tokens",
              "output_tokens", "cost_usd"):
        assert row[f] is None, f
    assert row["raw_decisions"] == [] == row["accepted_decisions"] == row["violations"]
    (e,) = row["executions"]
    assert e["executed"] and e["decision_id"] == f"gpt-{CYC}-stop-KO"
    assert e["constraint"] == "" and e["stop_unprotected"] is False
    (ev,) = row["stop_evaluation"]
    assert ev["closing_mark"] == 80.0 and ev["avg_cost"] == 100.0
    assert ev["loss_pct"] == pytest.approx(0.20) and ev["loss_usd"] == pytest.approx(40.0)
    assert "No model was called" in row["overall_reasoning"] and sent == []


def test_cancelled_at_deadline_is_not_executable_not_unprotected(tmp_trades):
    """§4 case B: accepted for the next open, cancelled by our 300 s deadline."""
    tmp, sent = tmp_trades
    row = _write([_result(False, "canceled", constraint="UNFILLED_AT_DEADLINE")],
                 _executor("canceled"), tmp)
    (e,) = row["executions"]
    assert e["constraint"] == EOD_STOP_NOT_EXECUTABLE and e["stop_unprotected"] is False
    assert e["venue_constraint"] == "UNFILLED_AT_DEADLINE"
    assert row["venue_clock"]["is_open"] is False
    assert sent == [("WARN", "EOD stop not executable (market closed): gpt")]


def test_rejected_at_submission_is_not_executable(tmp_trades):
    """§4 case A: the venue refused it; no broker order came back."""
    tmp, sent = tmp_trades
    row = _write([_result(False, None)], _executor(broken=True), tmp)
    (e,) = row["executions"]
    assert e["constraint"] == EOD_STOP_NOT_EXECUTABLE and e["stop_unprotected"] is False
    assert "eod_open_orders" not in row
    assert sent == [("WARN", "EOD stop not executable (market closed): gpt")]


def test_order_open_past_the_run_is_critical_on_the_row(tmp_trades):
    """§4 case C must not occur; if it does, it is recorded and paged."""
    tmp, sent = tmp_trades
    row = _write([_result(False, "accepted")], _executor("accepted"), tmp)
    (e,) = row["executions"]
    assert e["eod_open_order"] is True and e["stop_unprotected"] is True
    assert row["eod_open_orders"][0]["status"] == "accepted"
    assert sent == [("CRITICAL", "EOD stop order left open at the venue: gpt")]


def test_unconfirmable_venue_order_counts_as_open(tmp_trades):
    tmp, sent = tmp_trades
    row = _write([_result(False, "accepted")], _executor(broken=True), tmp)
    assert row["eod_open_orders"][0]["error"].startswith("ConnectionError")
    assert sent and sent[0][0] == "CRITICAL"


def test_paper_book_records_the_book_entry(tmp_trades):
    tmp, sent = tmp_trades
    row = _write([_result(True, None, order_id="FORCED_POSITION_STOP")],
                 _executor(venue=False), tmp)
    (e,) = row["executions"]
    assert e["executed"] and e["order_id"] == "FORCED_POSITION_STOP" and sent == []
    assert row["venue_clock"] is None                    # paper: no venue, no clock


def test_writer_failure_is_critical_and_never_raises(tmp_trades, monkeypatch):
    tmp, sent = tmp_trades
    monkeypatch.setattr(pipeline, "log_administrative_row", lambda *a, **k: 1 / 0)
    assert pipeline._write_eod_admin_row(
        "gpt", None, [_result(True, "filled")], {}, {}, _executor(), SETTINGS, RUN, {}, [],
        pipeline.logging.getLogger("t")) is None
    assert sent == [("CRITICAL", "EOD administrative row not written: gpt")]


def test_row_is_written_before_the_book_is_saved():
    """Same run, same commit as the saved book: the row precedes save_portfolio
    in the EOD branch."""
    from pathlib import Path
    src = (Path(pipeline.__file__)).read_text(encoding="utf-8")
    branch = src[src.index("    if is_eod:\n        snapshot_after = snapshot_before"):]
    assert branch.index("_write_eod_admin_row(") < branch.index("save_portfolio(portfolio)")


def test_case_a_rejection_scores_as_a_legitimate_rejection_from_the_one_set(tmp_trades):
    """Hub ruling 2026-11-18: a venue refusal of an after-close stop is a
    legitimate market rejection, never fail:submission_failed, and Gate 4 and
    the skip alarm read it from the one shared set."""
    from src.analytics import execution_metrics as em
    from src.execution.executor import REGISTERED_SKIP_CLASSES, requires_skip_alarm
    tmp, _ = tmp_trades
    row = _write([_result(False, None)], _executor(broken=True), tmp)
    assert EOD_STOP_NOT_EXECUTABLE in REGISTERED_SKIP_CLASSES
    assert em._LEGIT_CONSTRAINTS is REGISTERED_SKIP_CLASSES
    (e,) = row["executions"]
    assert em.classify_admin_execution(e, row) == f"ok:constraint_{EOD_STOP_NOT_EXECUTABLE}"
    scored = em.score_cycles([row], {CYC: True})
    assert scored["cycles"] == 1 and scored["successes"] == 1
    assert scored["failure_cycles_by_reason"] == {}
    assert scored["constraint_events"] == {EOD_STOP_NOT_EXECUTABLE: 1}
    r = _result(False, None, constraint=EOD_STOP_NOT_EXECUTABLE, unprotected=False)
    r.broker_order = None
    assert not requires_skip_alarm(r)


# ------------------------------------- r4_notes rev. 2 section 4 (Gate 4)
def _admin(results, ex, tmp):
    return _write(results, ex, tmp)


def test_no_market_closed_evidence_means_no_tag_and_a_critical(tmp_trades):
    """§4.3: the tag needs the venue's evidence. Clock unreadable and no
    rejection reason: the executor's stamp stands, and it is paged."""
    tmp, sent = tmp_trades
    row = _write([_result(False, "canceled", constraint="UNFILLED_AT_DEADLINE")],
                 _executor("canceled", clock_open=None), tmp)
    (e,) = row["executions"]
    assert e["constraint"] == "UNFILLED_AT_DEADLINE" and e["stop_unprotected"] is True
    assert sent == [("CRITICAL", "EOD stop not executed, market state unconfirmed: gpt")]


def test_rejection_reason_alone_is_evidence(tmp_trades):
    tmp, sent = tmp_trades
    r = _result(False, None)
    r.error = "Alpaca 403: market is closed"
    row = _write([r], _executor(broken=True, clock_open=None), tmp)
    assert row["executions"][0]["constraint"] == EOD_STOP_NOT_EXECUTABLE


def test_score_by_case(tmp_trades):
    """§4.5: A success (market rejection), B success (resolved), C failure
    (unresolved_status, whatever the tag), D an ordinary fill."""
    from src.analytics import execution_metrics as em
    tmp, _ = tmp_trades
    a = _write([_result(False, None)], _executor(broken=True), tmp)
    (tmp / "gpt_2026-11.jsonl").unlink()
    b = _write([_result(False, "canceled", constraint="UNFILLED_AT_DEADLINE")],
               _executor("canceled"), tmp)
    (tmp / "gpt_2026-11.jsonl").unlink()
    c = _write([_result(False, "accepted")], _executor("accepted"), tmp)
    (tmp / "gpt_2026-11.jsonl").unlink()
    d = _write([_result(True, "filled")], _executor("filled"), tmp)
    out = {k: em.classify_admin_execution(r["executions"][0], r)
           for k, r in zip("ABCD", (a, b, c, d))}
    assert out == {"A": f"ok:constraint_{EOD_STOP_NOT_EXECUTABLE}", "B": "ok:terminal_canceled",
                   "C": "fail:unresolved_status", "D": "ok:terminal_filled"}


def test_terminal_state_comes_before_the_tag():
    from src.analytics import execution_metrics as em
    row = {"record_class": "ADMINISTRATIVE", "admin_class": "EOD_POSITION_STOP",
           "venue_clock": {"is_open": False}}
    e = {"constraint": EOD_STOP_NOT_EXECUTABLE, "stop_unprotected": False,
         "broker_order": {"status": "accepted"}}
    assert em.classify_admin_execution(e, row) == "fail:unresolved_status"


def test_tag_without_evidence_or_off_an_eod_row_fails():
    from src.analytics import execution_metrics as em
    e = {"constraint": EOD_STOP_NOT_EXECUTABLE, "side": "SKIP", "order_id": "x"}
    no_evidence = {"record_class": "ADMINISTRATIVE", "admin_class": "EOD_POSITION_STOP",
                   "venue_clock": {"is_open": True}}
    other_admin = {"record_class": "ADMINISTRATIVE", "admin_class": "BOUNDARY_ADMIN",
                   "venue_clock": {"is_open": False}}
    assert em.classify_admin_execution(e, no_evidence) == "fail:unclassified_skip"
    assert em.classify_admin_execution(e, other_admin) == "fail:unclassified_skip"
    assert em.classify_execution(e) == "fail:unclassified_skip"     # an intraday row


def test_admin_rows_admitted_by_record_class_and_counted_separately(tmp_trades):
    from src.analytics import execution_metrics as em
    tmp, _ = tmp_trades
    a = _write([_result(False, None)], _executor(broken=True), tmp)
    model = {"execution_mode": "broker_paper", "api_success": True, "cycle_id": "m1",
             "executions": [{"side": "HOLD"}]}
    c = dict(a, cycle_id="c1", executions=[dict(a["executions"][0], eod_open_order=True)])
    out = em.score_cycles([model, a, c], {"m1": True, CYC: True, "c1": True})
    assert out["cycles"] == 3 and out["successes"] == 2
    assert out["rate_excluding_administrative"] == 1.0
    assert out["administrative_cycles"] == {
        "cycles": 2, "successes": 1,
        "by_outcome": {"fail:unresolved_status": 1, "success": 1}}
    assert out["excluded_no_decision"] == 0              # null api_success is not "no decision"


def test_eod_reconciliation_must_be_clean(tmp_trades):
    from src.analytics import execution_metrics as em
    tmp, _ = tmp_trades
    d = _write([_result(True, "filled")], _executor("filled"), tmp)
    out = em.score_cycles([d], {CYC: False})
    assert out["administrative_cycles"]["by_outcome"] == {"fail:reconciliation_diverged": 1}
