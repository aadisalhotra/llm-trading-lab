"""The decision deadline D and the finish-reason classes, refreshed arms.

tick_deadline_and_calibration_rulings_2026-11-21.md section 1.5 (D from the
pipeline's start; a call not returned at D is cancelled, streaming or not; a
parse retry starts only before D; TICK_DEADLINE its own class);
send_omit_measure_and_finish_reason_ruling_2026-11-21.md section 3 and
calibration_contingencies_ruling_2026-11-21.md section 1 (only a normal
completion is parsed; budget stop = failed decision, decision_truncated;
REFUSAL; ABNORMAL_FINISH for everything else, unknown values included; all
classified before the parser, never retried). H-OPS-00: refreshed arms only.
No provider is contacted.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import anthropic
import openai
import pytest
import requests
from google import genai

from src.adapters import base, get_adapter
from src.adapters.base import BaseAdapter
from src.config_loader import tick_deadline_seconds
from src.logging.call_inputs import response_flags

ROOT = Path(__file__).resolve().parents[1]
DECISION = json.dumps({"decisions": [], "overall_reasoning": "ok"})


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    monkeypatch.setattr(base, "_RETRY_DELAY_SECONDS", 0.05)


class _Fake(BaseAdapter):
    """A refreshed arm whose calls are scripted."""
    provider_name = "fake"

    def __init__(self, script, refreshed=True):
        super().__init__("fake-model")
        self.script, self._refreshed, self.calls = list(script), refreshed, 0

    @property
    def refreshed(self):
        return self._refreshed

    def _call_api(self, system_prompt, user_prompt, images=None):
        self.calls += 1
        delay, text = self.script.pop(0)
        time.sleep(delay)
        return text, "fake-model", {"finish_reason": "stop"}


def _arm(script, seconds_to_d, refreshed=True):
    a = _Fake(script, refreshed)
    a.deadline_at = time.monotonic() + seconds_to_d
    return a


# ------------------------------------------------------------------- deadline
def test_a_call_returning_before_d_is_untouched():
    r = _arm([(0.05, DECISION)], 2.0).generate_decision("S", "U")
    assert r.success and r.metadata["attempt"] == 1


def test_a_call_not_returned_at_d_is_cancelled_as_tick_deadline():
    a = _arm([(3.0, DECISION)], 0.4)
    t0 = time.monotonic()
    r = a.generate_decision("S", "U")
    assert time.monotonic() - t0 < 1.5                       # cut at D, not at the call's end
    assert r.success is False and r.error_class == "TICK_DEADLINE"
    assert r.metadata["deadline_cut"] == "in_call" and r.raw_response == ""
    assert a.calls == 1


def test_past_d_no_call_is_made():
    a = _arm([(0.0, DECISION)], -1.0)
    r = a.generate_decision("S", "U")
    assert r.error_class == "TICK_DEADLINE" and r.metadata["deadline_cut"] == "before_call"
    assert a.calls == 0


def test_the_parse_retry_is_not_started_once_d_is_too_close(monkeypatch):
    monkeypatch.setattr(base, "_RETRY_DELAY_SECONDS", 5)
    a = _arm([(0.0, "not json"), (0.0, DECISION)], 1.0)
    r = a.generate_decision("S", "U")
    assert r.error_class == "PARSE_FAILURE" and r.metadata["parse_retry"] == "not_started_deadline"
    assert a.calls == 1


def test_a_parse_retry_started_before_d_is_still_cut_at_d():
    a = _arm([(0.0, "not json"), (3.0, DECISION)], 0.6)
    r = a.generate_decision("S", "U")
    assert r.error_class == "TICK_DEADLINE" and r.metadata["parse_retry"] == "cut_at_deadline"
    assert r.metadata["first_parse_error"] and a.calls == 2


def test_a_parse_retry_with_time_left_recovers():
    a = _arm([(0.0, "not json"), (0.0, DECISION)], 3.0)
    r = a.generate_decision("S", "U")
    assert r.success and r.metadata["attempt"] == 2


def test_no_deadline_off_the_refreshed_arms():
    a = _arm([(0.6, DECISION)], 0.1, refreshed=False)
    assert a.generate_decision("S", "U").success           # snapshots unchanged (H-OPS-00)


def test_the_screening_call_is_cut_at_d_too():
    a = _arm([(3.0, "x")], 0.3)
    with pytest.raises(base.TickDeadlineExceeded):
        a.generate_raw("S", "U")


def test_the_transport_timeout_is_cut_to_the_time_left():
    a = _arm([], 10.0)
    assert 9.0 < a.transport_timeout(300) <= 10.0
    a.deadline_at = None
    assert a.transport_timeout(300) == 300


# -------------------------------------------------------- finish classes, Claude
def _claude_stream(monkeypatch, stop, events_until_closed=False):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    usage = SimpleNamespace(input_tokens=10, output_tokens=20,
                            output_tokens_details={"thinking_tokens": 5})

    class _S:
        closed = False

        def __init__(self, model):
            self.model = model

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            self.closed = True

        def __iter__(self):
            yield SimpleNamespace(type="message_start", message=SimpleNamespace(
                model=self.model, usage=SimpleNamespace(input_tokens=10)))
            if events_until_closed:
                while not self.closed:
                    time.sleep(0.02)
                    yield SimpleNamespace(type="content_block_delta")
            yield SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason=stop),
                                  usage=usage)
            yield SimpleNamespace(type="message_stop")

        def get_final_message(self):
            return SimpleNamespace(model=self.model, stop_reason=stop, stop_sequence=None,
                                   stop_details=None, usage=SimpleNamespace(
                                       input_tokens=10, output_tokens=20,
                                       cache_read_input_tokens=0, cache_creation_input_tokens=0,
                                       service_tier="standard"),
                                   content=[SimpleNamespace(type="text", text=DECISION)])
    calls = []
    client = SimpleNamespace(messages=SimpleNamespace(
        stream=lambda **kw: calls.append(kw) or _S(kw["model"])))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **k: client)
    return calls


@pytest.mark.parametrize("stop,cls", [("end_turn", None), ("max_tokens", "DECISION_TRUNCATED"),
                                      ("refusal", "REFUSAL"), ("pause_turn", "ABNORMAL_FINISH"),
                                      ("stop_sequence", "ABNORMAL_FINISH"),
                                      ("something_new", "ABNORMAL_FINISH")])
def test_claude_finish_classes(monkeypatch, stop, cls):
    calls = _claude_stream(monkeypatch, stop)
    r = get_adapter("anthropic", "claude-sonnet-5-5").generate_decision("S", "U")
    assert r.error_class == cls and len(calls) == 1
    assert r.metadata["finish_reason"] == stop               # raw reason always recorded
    if cls:
        assert r.success is False and r.raw_response == "" and r.decisions == []
    if cls == "DECISION_TRUNCATED":
        assert response_flags(r)["decision_truncated"] is True


def test_claude_stream_cancelled_at_d_is_tick_deadline(monkeypatch):
    _claude_stream(monkeypatch, "end_turn", events_until_closed=True)
    a = get_adapter("anthropic", "claude-opus-5-5")
    a.deadline_at = time.monotonic() + 0.4
    t0 = time.monotonic()
    r = a.generate_decision("S", "U")
    assert time.monotonic() - t0 < 1.5
    assert r.error_class == "TICK_DEADLINE" and r.raw_response == ""


# ------------------------------------------------- finish classes, Astra / Grok
def _openai(monkeypatch, finish, refusal=None):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    resp = lambda kw: SimpleNamespace(  # noqa: E731
        model=kw["model"], system_fingerprint=None, service_tier=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content=DECISION, refusal=refusal),
                                 finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2,
                              completion_tokens_details=SimpleNamespace(reasoning_tokens=1),
                              prompt_tokens_details=SimpleNamespace(cached_tokens=0)))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: resp(kw))))
    monkeypatch.setattr(openai, "OpenAI", lambda **k: client)


@pytest.mark.parametrize("finish,refusal,cls", [
    ("stop", None, None), ("length", None, "DECISION_TRUNCATED"),
    ("content_filter", None, "REFUSAL"), ("stop", "I can't", "REFUSAL"),
    ("tool_calls", None, "ABNORMAL_FINISH"), (None, None, "ABNORMAL_FINISH")])
def test_astra_finish_classes(monkeypatch, finish, refusal, cls):
    _openai(monkeypatch, finish, refusal)
    r = get_adapter("openai", "gpt-6-astra").generate_decision("S", "U")
    assert r.error_class == cls
    if cls:
        assert r.raw_response == "" and r.metadata["finish_class"] in ("truncated", "refusal", "abnormal")


def test_snapshot_length_stop_is_unchanged(monkeypatch):
    _openai(monkeypatch, "length")
    r = get_adapter("openai", "gpt-5.4-2026-03-05").generate_decision("S", "U")
    assert r.success and "finish_class" not in r.metadata       # parsed as before


@pytest.mark.parametrize("finish,cls", [("stop", None), ("length", "DECISION_TRUNCATED"),
                                        ("content_filter", "REFUSAL"), ("weird", "ABNORMAL_FINISH")])
def test_grok_finish_classes(monkeypatch, finish, cls):
    monkeypatch.setenv("XAI_API_KEY", "k")
    data = {"model": "grok-4.7", "choices": [{"message": {"content": DECISION},
                                               "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                      "completion_tokens_details": {"reasoning_tokens": 1}}}
    monkeypatch.setattr(requests, "post", lambda *a, **k: SimpleNamespace(
        ok=True, status_code=200, json=lambda: data, text=""))
    r = get_adapter("xai", "grok-4.7").generate_decision("S", "U")
    assert r.error_class == cls


# ---------------------------------------------------------- finish classes, Argon
@pytest.mark.parametrize("finish,block,cls", [
    ("STOP", None, None), ("MAX_TOKENS", None, "DECISION_TRUNCATED"),
    ("SAFETY", None, "REFUSAL"), ("PROHIBITED_CONTENT", None, "REFUSAL"),
    ("BLOCKLIST", None, "REFUSAL"), ("SPII", None, "REFUSAL"), ("RECITATION", None, "REFUSAL"),
    ("STOP", "SAFETY", "REFUSAL"), ("OTHER", None, "ABNORMAL_FINISH"),
    ("IMAGE_SAFETY", None, "ABNORMAL_FINISH"), ("FOO", None, "ABNORMAL_FINISH")])
def test_argon_finish_classes(monkeypatch, finish, block, cls):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    usage = SimpleNamespace(prompt_token_count=1, candidates_token_count=1, thoughts_token_count=1,
                            cached_content_token_count=None)
    resp = SimpleNamespace(text=DECISION, model_version="gemini-4-argon", usage_metadata=usage,
                           candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish),
                                                       finish_message=None)],
                           prompt_feedback=SimpleNamespace(block_reason=SimpleNamespace(name=block))
                           if block else None)
    monkeypatch.setattr(genai, "Client", lambda **k: SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kw: resp)))
    r = get_adapter("google", "gemini-4-argon").generate_decision("S", "U")
    assert r.error_class == cls


# ------------------------------------------------------------- config and wiring
def test_d_is_one_configured_value():
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    assert tick_deadline_seconds(settings) == 780
    assert tick_deadline_seconds({}) is None
    with pytest.raises(ValueError):
        tick_deadline_seconds({"tick_deadline": {"seconds": 775}})


def test_d_runs_from_the_pipeline_start_and_reaches_every_adapter():
    src = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    run = src[src.index("def run_pipeline("):]
    start = run.index("pipeline_start = time.monotonic()")
    assert start < run.index("tick_deadline_at = (pipeline_start + tick_deadline_s)")
    assert "tick_deadline_at=tick_deadline_at," in run
    one = src[src.index("def run_one_model("):src.index("def run_pipeline(")]
    assert one.index("adapter = get_adapter(") < one.index("adapter.deadline_at = tick_deadline_at")


def test_the_row_carries_the_new_fields():
    src = (ROOT / "src" / "logging" / "decision_log.py").read_text(encoding="utf-8")
    for f in ('"finish_class"', '"tick_deadline_seconds"', '"deadline_cut"', '"parse_retry"'):
        assert f in src
