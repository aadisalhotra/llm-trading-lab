"""Logging package 2026-11-21: call-input retention and response flags.

rq6_harness_and_call_input_retention_ruling.md §2 and
output_budget_equalization_ruling.md rev. 2 §3. Everything here runs on
tmp_path; nothing touches data/, a provider or the venue.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.adapters.base import BaseAdapter
from src.logging import call_inputs as ci
from src.logging import decision_log

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
PNG_A = b"\x89PNG\r\n\x1a\nimage-a"
PNG_B = b"\x89PNG\r\n\x1a\nimage-b-longer"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _calls():
    return [ci.CallInput("screen", "screen system —", "screen user"),
            ci.CallInput("trade", "trade system", "trade user {\"json\": 1}", [PNG_A, PNG_B])]


# ------------------------------------------------------------------ the file
def test_one_line_per_call_with_texts_exactly_as_passed(tmp_path):
    run = datetime(2026, 11, 23, 10, 5, tzinfo=ET)            # slot 2: no image bytes
    f = ci.retain_call_inputs("20261123T150512Z", "claude", run, "v4", _calls(), tmp_path)
    lines = [json.loads(x) for x in
             (tmp_path / "2026-11" / "20261123T150512Z.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [ln["stage"] for ln in lines] == ["screen", "trade"]
    for ln, c in zip(lines, _calls()):
        assert (ln["system"], ln["user"]) == (c.system, c.user)
        assert ln["system_sha256"] == _sha(c.system.encode("utf-8"))
        assert ln["user_sha256"] == _sha(c.user.encode("utf-8"))
        assert ln["cycle_id"] == "20261123T150512Z" and ln["model_key"] == "claude"
        assert ln["prompt_version"] == "v4"
    assert lines[1]["images"] == [{"sha256": _sha(PNG_A), "bytes": len(PNG_A)},
                                  {"sha256": _sha(PNG_B), "bytes": len(PNG_B)}]
    # the row fields
    assert f["trade_user_sha256"] == lines[1]["user_sha256"]
    assert f["screen_system_sha256"] == lines[0]["system_sha256"]
    assert f["image_count"] == 2 and f["images"] == lines[1]["images"]
    assert f["input_retention_failed"] is False and f["image_bytes_retained"] is False
    assert not (tmp_path / "2026-11" / "images").exists()


def test_concurrent_model_threads_append_whole_lines(tmp_path):
    run = datetime(2026, 11, 23, 10, 5, tzinfo=ET)
    ts = [threading.Thread(target=ci.retain_call_inputs,
                           args=("C1", f"m{i}", run, "v4", _calls(), tmp_path)) for i in range(12)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    rows = (tmp_path / run.strftime("%Y-%m") / "C1.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 24 and all(json.loads(r) for r in rows)


def test_a_write_failure_never_raises_and_marks_the_row(tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    f = ci.retain_call_inputs("20261123T150512Z", "gpt", datetime(2026, 11, 23, 9, 35, tzinfo=ET),
                              "v4", _calls(), blocker)
    assert f["input_retention_failed"] is True
    assert f["trade_user_sha256"] == _sha(b'trade user {"json": 1}')   # the hashes survive


# ---------------------------------------------------------- image rotation §2.4
@pytest.mark.parametrize("day,n", [("2026-11-23", 0), ("2026-11-24", 1), ("2026-11-25", 2),
                                   ("2026-11-27", 3),    # Thanksgiving 11-26 is not a session
                                   ("2026-11-30", 4), ("2026-11-20", None), ("2026-11-26", None)])
def test_session_index_counts_nyse_sessions_from_the_boundary(day, n):
    assert ci.session_index(day) == n


def test_retained_slots_formula():
    assert ci.retained_slots(0) == (1, 7)
    assert ci.retained_slots(7) == (8, 1)
    assert ci.retained_slots(12) == (13, 6)
    assert ci.retained_slots(13) == (1, 7)
    every = {s for n in range(13) for s in ci.retained_slots(n)}
    assert every == set(range(1, 14))         # every tick position is covered


@pytest.mark.parametrize("hh,mm,slot", [(9, 35, 1), (10, 5, 2), (12, 35, 7), (15, 35, 13),
                                        (9, 5, None), (16, 0, None)])
def test_tick_slot(hh, mm, slot):
    assert ci.tick_slot(datetime(2026, 11, 23, hh, mm, tzinfo=ET)) == slot


def test_image_bytes_kept_only_on_a_retained_slot(tmp_path):
    kept = ci.retain_call_inputs("20261123T143512Z", "grok",
                                 datetime(2026, 11, 23, 9, 35, tzinfo=ET), "v4", _calls(), tmp_path)
    assert kept["image_bytes_retained"] is True
    img = tmp_path / "2026-11" / "images"
    assert sorted(p.name for p in img.iterdir()) == sorted(f"{_sha(b)}.png" for b in (PNG_A, PNG_B))
    assert (img / f"{_sha(PNG_A)}.png").read_bytes() == PNG_A
    # session 0 keeps slots 1 and 7; slot 3 keeps nothing
    other = tmp_path / "other"
    f = ci.retain_call_inputs("20261123T153512Z", "grok",
                              datetime(2026, 11, 23, 10, 35, tzinfo=ET), "v4", _calls(), other)
    assert f["image_bytes_retained"] is False and not (other / "2026-11" / "images").exists()


# ------------------------------------------------------- flags and the WARN §3
def _result(success=True, finish="end_turn", repaired=False):
    return SimpleNamespace(success=success, metadata={"finish_reason": finish,
                                                      "json_repaired": repaired,
                                                      "output_tokens": 4096})


@pytest.mark.parametrize("finish,truncated", [("max_tokens", True), ("length", True),
                                              ("MAX_TOKENS", True), ("end_turn", False),
                                              ("stop", False), ("STOP", False), (None, False)])
def test_decision_truncated(finish, truncated):
    assert ci.response_flags(_result(finish=finish))["decision_truncated"] is truncated


@pytest.mark.parametrize("success,finish,repaired,alerts", [
    (True, "max_tokens", False, True), (True, "end_turn", True, True),
    (True, "end_turn", False, False), (False, "max_tokens", True, False)])
def test_warn_only_on_a_successful_flagged_call(success, finish, repaired, alerts):
    sent = []
    fired = ci.alert_if_flagged("claude", "C1", _result(success, finish, repaired),
                                lambda *a, **k: sent.append((a, k)))
    assert fired is alerts and len(sent) == int(alerts)
    if alerts:
        assert sent[0][0][0] == "WARN" and sent[0][1]["kind"] == "response_flagged"


def test_alert_failure_never_raises():
    def boom(*a, **k):
        raise RuntimeError("smtp down")
    assert ci.alert_if_flagged("gpt", "C1", _result(finish="length"), boom) is False


def test_parser_reports_json_repaired():
    cut = '{"decisions": [{"ticker": "AAPL", "action": "BUY", "target_weight": 0.1, "confidence": 0.6}'
    clean = cut + "]}"
    assert BaseAdapter._parse_response(cut)["json_repaired"] is True
    assert BaseAdapter._parse_response(clean)["json_repaired"] is False


# ------------------------------------------------------------- the decision row
def _dr(raw, success=True):
    return SimpleNamespace(model_id_configured="m", model_id_returned="m", provider="p",
                           success=success, error=None, latency_seconds=1.0,
                           metadata={"finish_reason": "max_tokens", "json_repaired": True},
                           raw_response=raw, overall_reasoning="", decisions=[])


def _log(tmp_path, monkeypatch, dr, **kw):
    monkeypatch.setattr(decision_log, "TRADES_DIR", tmp_path)
    decision_log.log_decision_run(
        model_key="claude", run_date=datetime(2026, 11, 23, 9, 35), decision_result=dr,
        accepted_decisions=[], violations=[], execution_results=[], portfolio_snapshot_after={},
        prompt_version="v4", data_inputs_hash="h", execution_mode="broker_paper",
        inception_date="2026-04-08", screening_response="S" * 5000, **kw)
    return json.loads((tmp_path / "claude_2026-11.jsonl").read_text(encoding="utf-8").splitlines()[-1])


def test_row_carries_full_raw_text_flags_and_hashes(tmp_path, monkeypatch):
    raw = "x" * 50_000                                          # past the old 20,000 cap
    fields = ci.retain_call_inputs("C1", "claude", datetime(2026, 11, 23, 10, 5, tzinfo=ET), "v4",
                                   _calls(), tmp_path / "prompts")
    row = _log(tmp_path, monkeypatch, _dr(raw), call_inputs=fields)
    assert row["raw_response"] == raw
    assert row["raw_response_on_failure"] is None               # unchanged for its readers
    assert row["screening_raw_response"] == "S" * 5000 and len(row["screening_response"]) == 2000
    assert (row["decision_truncated"], row["json_repaired"]) == (True, True)
    for k in ("trade_system_sha256", "trade_user_sha256", "screen_system_sha256",
              "screen_user_sha256", "image_count", "images", "input_retention_failed"):
        assert row[k] == fields[k]


def test_failed_call_keeps_its_full_raw_text_too(tmp_path, monkeypatch):
    raw = "y" * 30_000
    row = _log(tmp_path, monkeypatch, _dr(raw, success=False), call_inputs={})
    assert row["raw_response"] == raw and len(row["raw_response_on_failure"]) == 20_000


def test_row_without_retention_says_so(tmp_path, monkeypatch):
    row = _log(tmp_path, monkeypatch, _dr("{}"))
    assert row["input_retention_failed"] is True and row["trade_user_sha256"] is None


# ------------------------------------------------------------- pipeline wiring
def test_pipeline_retains_the_objects_it_hands_the_adapter():
    src = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")
    body = src[src.index("def run_one_model("):]
    call = body.index("decision_result = adapter.generate_decision(system_prompt, user_prompt, "
                      "images=images_for_call)")
    keep = body.index("call_inputs = retain_call_inputs(")
    assert call < keep < body.index("alert_if_flagged(model_key, executor.cycle_id, decision_result")
    block = body[keep:body.index("alert_if_flagged(", keep)]
    assert 'CallInput("screen", screening_sys, screening_user)' in block
    assert 'CallInput("trade", system_prompt, user_prompt, list(images_for_call or []))' in block
    assert body.count("call_inputs=call_inputs,") == 2          # both log_decision_run calls
