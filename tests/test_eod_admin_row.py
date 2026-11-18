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


def _executor(final_status="filled", venue=True, broken=False):
    class B:
        def get_order_by_client_id(self, oid):
            if broken:
                raise ConnectionError("venue down")
            return _Order(final_status)
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
    assert e["venue_constraint"] == "UNFILLED_AT_DEADLINE" and sent == []


def test_rejected_at_submission_is_not_executable(tmp_trades):
    """§4 case A: the venue refused it; no broker order came back."""
    tmp, sent = tmp_trades
    row = _write([_result(False, None)], _executor(broken=True), tmp)
    (e,) = row["executions"]
    assert e["constraint"] == EOD_STOP_NOT_EXECUTABLE and e["stop_unprotected"] is False
    assert "eod_open_orders" not in row and sent == []


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
    assert em.classify_execution(e) == f"ok:constraint_{EOD_STOP_NOT_EXECUTABLE}"
    scored = em.score_cycles([row], {CYC: True})
    assert scored["cycles"] == 1 and scored["successes"] == 1
    assert scored["failure_cycles_by_reason"] == {}
    assert scored["constraint_events"] == {EOD_STOP_NOT_EXECUTABLE: 1}
    r = _result(False, None, constraint=EOD_STOP_NOT_EXECUTABLE, unprotected=False)
    r.broker_order = None
    assert not requires_skip_alarm(r)
