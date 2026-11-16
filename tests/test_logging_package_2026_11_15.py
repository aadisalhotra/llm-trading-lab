"""The 2026-11-15 logging package: what each new decision-record field records.

  * api_error_class   TIMEOUT / API_ERROR / PARSE_FAILURE, null on success
  * model_version_raw Gemini's raw response.model_version, null when absent
  * num_sources_used, citations   xAI live-search provenance, null when absent
  * barrier_tag / barrier_detail  BARRIER_DELAYED on orders the P1 barrier held
  * the retro-annotation of the four registered xAI 120s timeouts

Request bodies are unchanged; tests/test_adapter_request_invariance.py proves
that byte for byte. No network: every transport here is a fake.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import socket

import httpx
import pytest
import requests

try:  # the CI lock's anthropic/openai transport (see test_adapter_request_invariance)
    import httpx2
except ImportError:  # pragma: no cover - depends on the installed SDKs
    httpx2 = None

HTTPX_FAMILY = [m for m in (httpx, httpx2) if m is not None]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(self, *a, **kw):
        raise OSError("test_logging_package_2026_11_15: outbound connect refused")
    monkeypatch.setattr(socket.socket, "connect", refuse)

from src.adapters import base as base_mod
from src.adapters.base import (
    ERROR_CLASS_API_ERROR,
    ERROR_CLASS_PARSE_FAILURE,
    ERROR_CLASS_TIMEOUT,
    BaseAdapter,
    DecisionResult,
    is_client_timeout,
)
from src.adapters.gemini_adapter import GeminiAdapter
from src.adapters.xai_adapter import XAIAdapter
from src.adapters.deepseek_adapter import DeepSeekAdapter
from src.execution.barrier import CycleBarrier, Intent
from src.execution.executor import (
    BARRIER_DELAYED_TAG,
    CONSTRAINT_WASH_REJECT_POST_BARRIER,
    REGISTERED_SKIP_CLASSES,
)
from src.logging import decision_log

ROOT = Path(__file__).resolve().parents[1]
GOOD = '{"decisions": [], "overall_reasoning": "ok"}'


# --------------------------------------------------------------------------- #
# api_error_class
# --------------------------------------------------------------------------- #
class _Scripted(BaseAdapter):
    provider_name = "scripted"

    def __init__(self, behaviour):
        super().__init__("m")
        self._behaviour = behaviour

    def _call_api(self, system_prompt, user_prompt, images=None):
        return self._behaviour()


def _raise(exc):
    def f():
        raise exc
    return f


@pytest.mark.parametrize("exc", [
    requests.exceptions.ReadTimeout("HTTPSConnectionPool(host='api.x.ai', port=443): "
                                    "Read timed out. (read timeout=300)"),
    requests.exceptions.ConnectTimeout("connect timed out"),
    httpx.ReadTimeout("timed out"),
    TimeoutError("socket timed out"),
], ids=["requests-read", "requests-connect", "httpx-read", "builtin"])
def test_client_timeouts_classify_as_timeout(exc):
    assert is_client_timeout(exc)
    r = _Scripted(_raise(exc)).generate_decision("s", "u")
    assert (r.success, r.error_class) == (False, ERROR_CLASS_TIMEOUT)


def test_sdk_timeout_errors_classify_as_timeout():
    import anthropic
    import openai
    req = httpx.Request("POST", "https://example.invalid")
    for exc in (anthropic.APITimeoutError(request=req), openai.APITimeoutError(request=req)):
        assert is_client_timeout(exc), type(exc)


@pytest.mark.parametrize("lib", HTTPX_FAMILY, ids=lambda m: m.__name__)
def test_every_installed_httpx_family_timeout_is_a_timeout(lib):
    assert is_client_timeout(lib.ReadTimeout("t"))
    assert is_client_timeout(lib.ConnectTimeout("t"))


def test_a_timeout_wrapped_by_another_exception_is_still_a_timeout():
    try:
        try:
            raise httpx.ReadTimeout("inner")
        except httpx.ReadTimeout as inner:
            raise RuntimeError("SDK wrapper") from inner
    except RuntimeError as outer:
        assert is_client_timeout(outer)


@pytest.mark.parametrize("exc", [
    RuntimeError("xAI API 504: gateway timeout page"),   # provider-side deadline
    RuntimeError("DeepSeek API 400: bad request"),
    KeyError("choices"),
    ConnectionError("reset by peer"),
])
def test_other_call_failures_classify_as_api_error(exc):
    assert not is_client_timeout(exc)
    r = _Scripted(_raise(exc)).generate_decision("s", "u")
    assert r.error_class == ERROR_CLASS_API_ERROR


def test_unparseable_after_retry_classifies_as_parse_failure(monkeypatch):
    monkeypatch.setattr(base_mod, "_RETRY_DELAY_SECONDS", 0)
    r = _Scripted(lambda: ("not json", "m", {})).generate_decision("s", "u")
    assert (r.success, r.error_class) == (False, ERROR_CLASS_PARSE_FAILURE)


def test_success_has_no_error_class():
    r = _Scripted(lambda: (GOOD, "m", {})).generate_decision("s", "u")
    assert (r.success, r.error_class) == (True, None)


def _requests_raising(monkeypatch, exc):
    def send(self, prepared, **kw):
        raise exc
    monkeypatch.setattr(requests.Session, "send", send)


@pytest.mark.parametrize("cls,key", [(XAIAdapter, "XAI_API_KEY"),
                                     (DeepSeekAdapter, "DEEPSEEK_API_KEY")])
def test_raw_rest_adapters_report_a_real_read_timeout_as_timeout(monkeypatch, cls, key):
    """End to end through the adapter: the exact shape of the 2026-11-05 Grok
    failure (requests ReadTimeout at the 300s ceiling)."""
    monkeypatch.setenv(key, "k")
    _requests_raising(monkeypatch, requests.exceptions.ReadTimeout(
        "HTTPSConnectionPool(host='x', port=443): Read timed out. (read timeout=300)"))
    r = cls("m").generate_decision("s", "u")
    assert r.error_class == ERROR_CLASS_TIMEOUT
    assert "read timeout=300" in r.error


def test_raw_rest_http_error_is_api_error(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "k")

    def send(self, prepared, **kw):
        resp = requests.Response()
        resp.status_code = 504
        resp._content = b"gateway timeout"
        resp.request = prepared
        return resp
    monkeypatch.setattr(requests.Session, "send", send)
    r = XAIAdapter("m").generate_decision("s", "u")
    assert r.error_class == ERROR_CLASS_API_ERROR


def test_gemini_sdk_timeout_is_timeout(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    monkeypatch.setattr(time, "sleep", lambda s: None)  # SDK retry backoff, if any

    for lib in HTTPX_FAMILY:
        def send(self, request, *a, _lib=lib, **kw):
            raise _lib.ReadTimeout("timed out", request=request)
        monkeypatch.setattr(lib.Client, "send", send)
    r = GeminiAdapter("gemini-3.1-pro-preview").generate_decision("s", "u")
    assert r.error_class == ERROR_CLASS_TIMEOUT, r.error


# --------------------------------------------------------------------------- #
# Gemini model_version_raw
# --------------------------------------------------------------------------- #
def _gemini_with(monkeypatch, body):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")

    for lib in HTTPX_FAMILY:
        def send(self, request, *a, _lib=lib, **kw):
            return _lib.Response(200, json=body, request=request)
        monkeypatch.setattr(lib.Client, "send", send)
    return GeminiAdapter("gemini-3.1-pro-preview").generate_decision("s", "u")


def _gemini_body(**extra):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": GOOD}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}, **extra}


def test_gemini_model_version_raw_is_the_raw_field(monkeypatch):
    r = _gemini_with(monkeypatch, _gemini_body(modelVersion="gemini-3.1-pro-preview-0219"))
    assert r.metadata["model_version_raw"] == "gemini-3.1-pro-preview-0219"
    assert r.model_id_returned == "gemini-3.1-pro-preview-0219"   # unchanged behaviour


def test_gemini_echoed_alias_is_recorded_as_echoed(monkeypatch):
    r = _gemini_with(monkeypatch, _gemini_body(modelVersion="gemini-3.1-pro-preview"))
    assert r.metadata["model_version_raw"] == "gemini-3.1-pro-preview"


def test_gemini_absent_model_version_is_null_not_the_configured_id(monkeypatch):
    """The case the fallback hid: model_id_returned still falls back to the
    configured id (unchanged), but model_version_raw says it was absent."""
    r = _gemini_with(monkeypatch, _gemini_body())
    assert r.metadata["model_version_raw"] is None
    assert r.model_id_returned == "gemini-3.1-pro-preview"


# --------------------------------------------------------------------------- #
# xAI num_sources_used / citations
# --------------------------------------------------------------------------- #
def _xai_with(monkeypatch, body):
    monkeypatch.setenv("XAI_API_KEY", "k")

    def send(self, prepared, **kw):
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json.dumps(body).encode()
        resp.request = prepared
        return resp
    monkeypatch.setattr(requests.Session, "send", send)
    return XAIAdapter("grok-4.20-0309-reasoning").generate_decision("s", "u")


def _xai_body(usage_extra=None, **extra):
    return {"model": "grok-4.20-0309-reasoning",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": GOOD}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, **(usage_extra or {})},
            **extra}


def test_xai_zero_sources_is_zero_not_null(monkeypatch):
    r = _xai_with(monkeypatch, _xai_body({"num_sources_used": 0}))
    assert r.metadata["num_sources_used"] == 0
    assert r.metadata["citations"] is None


def test_xai_sources_and_citations_are_recorded_verbatim(monkeypatch):
    cites = ["https://x.com/i/status/1", "https://example.com/a"]
    r = _xai_with(monkeypatch, _xai_body({"num_sources_used": 2}, citations=cites))
    assert (r.metadata["num_sources_used"], r.metadata["citations"]) == (2, cites)


def test_xai_absent_fields_are_null(monkeypatch):
    r = _xai_with(monkeypatch, _xai_body())
    assert r.metadata["num_sources_used"] is None
    assert r.metadata["citations"] is None


# --------------------------------------------------------------------------- #
# The decision record carries the fields
# --------------------------------------------------------------------------- #
def _write(tmp_path, monkeypatch, result, execution_results=()):
    monkeypatch.setattr(decision_log, "TRADES_DIR", tmp_path)
    decision_log.log_decision_run(
        model_key="grok", run_date=datetime(2026, 11, 16, 14, 35), decision_result=result,
        accepted_decisions=[], violations=[], execution_results=list(execution_results),
        portfolio_snapshot_after={}, prompt_version="v4", data_inputs_hash="h",
        execution_mode="broker_paper", inception_date="2026-11-06")
    (line,) = (tmp_path / "grok_2026-11.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads(line)


def _result(**kw):
    base = dict(provider="xai", model_id_configured="m", model_id_returned="m",
                decisions=[], overall_reasoning="", raw_response="",
                latency_seconds=0.0, success=True)
    base.update(kw)
    return DecisionResult(**base)


def test_record_carries_error_class_and_provider_fields(tmp_path, monkeypatch):
    rec = _write(tmp_path, monkeypatch, _result(
        success=False, error="Read timed out. (read timeout=300)",
        error_class=ERROR_CLASS_TIMEOUT,
        metadata={"num_sources_used": 0, "citations": None, "model_version_raw": None}))
    assert rec["api_error_class"] == "TIMEOUT"
    assert rec["num_sources_used"] == 0
    assert rec["citations"] is None
    assert rec["model_version_raw"] is None


def test_new_keys_are_always_present(tmp_path, monkeypatch):
    rec = _write(tmp_path, monkeypatch, _result())
    for k in ("api_error_class", "model_version_raw", "num_sources_used", "citations"):
        assert k in rec and rec[k] is None


def test_execution_rows_carry_the_barrier_tag(tmp_path, monkeypatch):
    from src.execution.executor import ExecutionResult
    held = ExecutionResult(decision={}, executed=True, side="BUY", ticker="PFE",
                           shares=1, fill_price=27.0, notional=27.0)
    held.barrier_tag = BARRIER_DELAYED_TAG
    held.barrier_detail = {"winner_book": "beta", "winner_action": "SELL",
                           "released_by": "CONTENDER_TERMINAL", "wait_seconds": 0.2}
    free = ExecutionResult(decision={}, executed=True, side="BUY", ticker="AAA",
                           shares=1, fill_price=10.0, notional=10.0)
    rec = _write(tmp_path, monkeypatch, _result(), [held, free])
    a, b = rec["executions"]
    assert (a["barrier_tag"], a["barrier_detail"]["winner_book"]) == ("BARRIER_DELAYED", "beta")
    assert (b["barrier_tag"], b["barrier_detail"]) == ("", None)
    assert a["constraint"] == "" and b["constraint"] == ""


# --------------------------------------------------------------------------- #
# BARRIER_DELAYED
# --------------------------------------------------------------------------- #
def test_tag_is_not_a_constraint_or_skip_class():
    assert BARRIER_DELAYED_TAG not in REGISTERED_SKIP_CLASSES


def test_wait_to_submit_returns_none_for_an_unheld_order():
    b = CycleBarrier(expected_books={"alpha"}, t=0, registration_timeout=1, release_timeout=1)
    b.register("alpha", [Intent("alpha", "PFE", "BUY")])
    assert b.wait_to_submit("alpha", "PFE", "BUY") is None


def _contended(release_timeout):
    b = CycleBarrier(expected_books={"alpha", "beta"}, t=0,
                     registration_timeout=2, release_timeout=release_timeout)
    threads = [threading.Thread(target=b.register, args=("alpha", [Intent("alpha", "PFE", "BUY")])),
               threading.Thread(target=b.register, args=("beta", [Intent("beta", "PFE", "SELL")]))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return b


def test_held_order_detail_when_the_contender_terminates():
    b = _contended(release_timeout=5)
    threading.Timer(0.1, b.notify_terminal, args=("beta", "PFE", "SELL")).start()
    d = b.wait_to_submit("alpha", "PFE", "BUY")
    assert d["winner_book"] == "beta" and d["winner_action"] == "SELL"
    assert d["released_by"] == "CONTENDER_TERMINAL"
    assert 0.0 < d["wait_seconds"] < 5


def test_held_order_detail_when_the_release_times_out():
    b = _contended(release_timeout=0.1)
    d = b.wait_to_submit("alpha", "PFE", "BUY")
    assert d["released_by"] == "RELEASE_TIMEOUT"
    assert d["wait_seconds"] >= 0.09


def test_executor_tags_the_held_order_and_only_the_held_order():
    from tests.test_p1_concurrent_execution import ScriptedBroker, _book, _executor
    from src.portfolio.portfolio import Holding

    broker = ScriptedBroker()
    ex = _executor(broker)
    barrier = CycleBarrier(expected_books={"alpha", "beta"}, t=0,
                           registration_timeout=5.0, release_timeout=5.0)
    alpha, beta = _book("alpha"), _book("beta")
    beta.holdings["PFE"] = Holding(ticker="PFE", shares=10.0, avg_cost=25.0)
    out = {}
    ta = threading.Thread(target=lambda: out.__setitem__("alpha", ex.execute_decisions(
        alpha, [{"action": "BUY", "ticker": "PFE", "target_weight": 0.1, "confidence": 8},
                {"action": "BUY", "ticker": "AAA", "target_weight": 0.1, "confidence": 8}],
        {"PFE": 27.0, "AAA": 10.0}, barrier=barrier)))
    tb = threading.Thread(target=lambda: out.__setitem__("beta", ex.execute_decisions(
        beta, [{"action": "SELL", "ticker": "PFE", "target_weight": 0.0, "confidence": 8}],
        {"PFE": 27.0}, barrier=barrier)))
    ta.start(); tb.start()
    broker.resolve_by("AAA", "buy", filled_qty=100.0, price=10.0)
    broker.resolve_by("PFE", "sell", filled_qty=10.0, price=27.0)
    broker.resolve_by("PFE", "buy", filled_qty=37.0, price=27.0)
    ta.join(5); tb.join(5)

    pfe, aaa = out["alpha"]
    assert (pfe.ticker, pfe.barrier_tag) == ("PFE", "BARRIER_DELAYED")
    assert pfe.barrier_detail["winner_book"] == "beta"
    assert pfe.barrier_detail["released_by"] == "CONTENDER_TERMINAL"
    assert pfe.constraint == ""                       # a hold is not a constraint
    assert (aaa.ticker, aaa.barrier_tag, aaa.barrier_detail) == ("AAA", "", None)
    (sell,) = out["beta"]
    assert (sell.barrier_tag, sell.barrier_detail) == ("", None)   # the winner waited for nobody


def test_a_held_order_rejected_after_release_keeps_both_labels():
    from tests.test_p1_concurrent_execution import (
        ScriptedBroker, _book, _executor, _wash_trade_error)
    from src.portfolio.portfolio import Holding

    broker = ScriptedBroker()
    ex = _executor(broker)
    broker.reject_on_submit("PFE", "buy", _wash_trade_error())
    barrier = CycleBarrier(expected_books={"alpha", "beta"}, t=0,
                           registration_timeout=5.0, release_timeout=5.0)
    alpha, beta = _book("alpha"), _book("beta")
    beta.holdings["PFE"] = Holding(ticker="PFE", shares=10.0, avg_cost=25.0)
    out = {}
    ta = threading.Thread(target=lambda: out.__setitem__("alpha", ex.execute_decisions(
        alpha, [{"action": "BUY", "ticker": "PFE", "target_weight": 0.1, "confidence": 8}],
        {"PFE": 27.0}, barrier=barrier)))
    tb = threading.Thread(target=lambda: out.__setitem__("beta", ex.execute_decisions(
        beta, [{"action": "SELL", "ticker": "PFE", "target_weight": 0.0, "confidence": 8}],
        {"PFE": 27.0}, barrier=barrier)))
    ta.start(); tb.start()
    broker.resolve_by("PFE", "sell", filled_qty=10.0, price=27.0)
    ta.join(5); tb.join(5)
    (r,) = out["alpha"]
    assert r.constraint == CONSTRAINT_WASH_REJECT_POST_BARRIER
    assert r.barrier_tag == "BARRIER_DELAYED"


# --------------------------------------------------------------------------- #
# Retro-annotation of the four registered xAI client timeouts
# --------------------------------------------------------------------------- #
ANNOTATIONS = ROOT / "data" / "annotations" / "xai_client_timeouts_retro_2026-11-15.jsonl"
REGISTERED_DATES = ["2026-04-24", "2026-08-20", "2026-08-25", "2026-09-17"]


def _annotations():
    return [json.loads(l) for l in ANNOTATIONS.read_text(encoding="utf-8").splitlines() if l]


def test_exactly_the_four_registered_events_are_annotated():
    rows = _annotations()
    assert [r["date"] for r in rows] == REGISTERED_DATES
    assert {r["api_error_class"] for r in rows} == {"TIMEOUT"}
    assert {r["model_key"] for r in rows} == {"grok"}


def test_each_annotation_binds_to_the_unchanged_source_line():
    """The annotation names a committed trade-log line by file, line number and
    sha256. The line must still exist byte-for-byte, carry the 120s timeout,
    and lack api_error_class (the annotation supplies it; the log is not
    rewritten)."""
    for a in _annotations():
        lines = (ROOT / a["source_file"]).read_bytes().splitlines()
        raw = lines[a["source_line"] - 1].rstrip(b"\r")
        assert hashlib.sha256(raw).hexdigest() == a["source_line_sha256"], a["date"]
        rec = json.loads(raw)
        assert rec["date"] == a["date"] and rec["timestamp"] == a["timestamp"]
        assert rec["api_error"] == a["api_error_verbatim"]
        assert "read timeout=120" in rec["api_error"]
        assert "api_error_class" not in rec
        assert a["completeness_denominator_change"] == 0
