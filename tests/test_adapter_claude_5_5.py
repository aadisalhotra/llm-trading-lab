"""Claude 5.5 adapter path for the refreshed confirmatory arms.

cohort_refresh_ruling.md rev. 2: section 5 (adaptive thinking at the provider
default effort, sent explicitly: high on claude-sonnet-5-5, medium on
claude-opus-5-5; the calibration cap at the provider maximum), section 6
(no server-side fallback ever requested; a refusal is its own class and is
never retried). Hub 2026-11-21: no temperature is sent to a refreshed arm.
The Phase A snapshots (claude-sonnet-4-6, claude-opus-4-6) keep their request
unchanged; tests/test_adapter_request_invariance.py proves their bytes.

No provider is contacted: anthropic.Anthropic is replaced by a recorder.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import anthropic
import pytest

from src.adapters import anthropic_adapter as aa
from src.adapters import get_adapter
from src.adapters.base import ERROR_CLASS_REFUSAL
from src.analytics.cost_rates import compute_call_cost_usd

DECISION = json.dumps({"decisions": [{"ticker": "AAPL", "action": "HOLD", "target_weight": 0.0,
                                      "confidence": 0.5, "summary": "s", "reasoning": "r"}],
                       "overall_reasoning": "ok"})


def _message(model, stop="end_turn", thinking=1234, text=DECISION, details=None):
    usage = SimpleNamespace(input_tokens=1000, output_tokens=(thinking or 0) + 200,
                            output_tokens_details=(SimpleNamespace(thinking_tokens=thinking)
                                                   if thinking is not None else None),
                            cache_read_input_tokens=0, cache_creation_input_tokens=0,
                            service_tier="standard")
    content = [SimpleNamespace(type="thinking", thinking=""),
               SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(model=model, stop_reason=stop, stop_sequence=None, stop_details=details,
                           usage=usage, content=content)


def _events(msg, stop_event=True):
    det = msg.usage.output_tokens_details
    yield SimpleNamespace(type="message_start", message=SimpleNamespace(
        model=msg.model, usage=SimpleNamespace(input_tokens=msg.usage.input_tokens, output_tokens=1)))
    yield SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason=msg.stop_reason),
                          usage=SimpleNamespace(input_tokens=msg.usage.input_tokens,
                                                output_tokens=msg.usage.output_tokens,
                                                output_tokens_details=(
                                                    {"thinking_tokens": det.thinking_tokens}
                                                    if det else None)))
    if stop_event:
        yield SimpleNamespace(type="message_stop")


class _Recorder:
    def __init__(self, reply, events=_events):
        self.calls: list[tuple[str, dict]] = []
        self.enter_delay = 0.0
        rec = self

        class _Stream:
            def __init__(self, kw):
                self.kw = kw

            def __enter__(self):
                time.sleep(rec.enter_delay)     # waiting for the response headers
                return self

            def __exit__(self, *a):
                return False

            closed = False

            def close(self):
                self.closed = True

            def __iter__(self):
                self.msg = reply(self.kw)
                yield from events(self.msg, stream=self) if events is not _events else events(self.msg)

            def get_final_message(self):
                msg = getattr(self, "msg", None) or reply(self.kw)
                usage = SimpleNamespace(**{k: v for k, v in vars(msg.usage).items()
                                           if k != "output_tokens_details"})
                return SimpleNamespace(**{**vars(msg), "usage": usage})

        class _Messages:
            def create(self, **kw):
                rec.calls.append(("create", kw))
                return reply(kw)

            def stream(self, **kw):
                rec.calls.append(("stream", kw))
                return _Stream(kw)

        self.messages = _Messages()


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def install(reply=lambda kw: _message(kw["model"]), events=_events):
        rec = _Recorder(reply, events)
        monkeypatch.setattr(anthropic, "Anthropic", lambda **k: rec)
        return rec
    return install


def test_the_levels_are_the_ruled_defaults():
    assert aa.REFRESHED_ARMS == {"claude-sonnet-5-5": {"effort": "high"},
                                 "claude-opus-5-5": {"effort": "medium"}}
    assert aa.REFRESHED_MAX_TOKENS == 128_000


@pytest.mark.parametrize("model,effort", [("claude-sonnet-5-5", "high"),
                                          ("claude-opus-5-5", "medium")])
def test_refreshed_arm_request(fake, model, effort):
    rec = fake()
    r = get_adapter("anthropic", model).generate_decision("SYS", "USER", images=[b"\x89PNG-a"])
    assert r.success and r.model_id_returned == model
    ((kind, kw),) = rec.calls
    assert kind == "stream"
    assert kw["model"] == model and kw["max_tokens"] == 128_000 and kw["system"] == "SYS"
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": effort}
    assert set(kw) == {"model", "max_tokens", "system", "messages", "thinking", "output_config",
                       "timeout"}          # timeout is client-side transport, not sent to the model
    content = kw["messages"][0]["content"]
    assert [c["type"] for c in content] == ["image", "text"] and content[-1]["text"] == "USER"
    # Nothing else that could change the model or the sampling is sent.
    for absent in ("temperature", "top_p", "top_k", "fallbacks", "betas", "speed"):
        assert absent not in kw
    md = r.metadata
    assert md["thoughts_tokens"] == 1234 and md["reasoning_effort_sent"] == effort
    assert md["max_tokens_sent"] == 128_000 and md["finish_reason"] == "end_turn"


@pytest.mark.parametrize("model", ["claude-sonnet-4-6", "claude-opus-4-6"])
def test_snapshot_request_is_unchanged(fake, model):
    rec = fake(lambda kw: _message(kw["model"], thinking=None))
    r = get_adapter("anthropic", model).generate_decision("SYS", "USER")
    assert r.success
    ((kind, kw),) = rec.calls
    assert kind == "create"
    assert set(kw) == {"model", "max_tokens", "system", "messages"} and kw["max_tokens"] == 16384
    assert "reasoning_effort_sent" not in r.metadata


def test_temperature_is_never_sent_to_a_refreshed_arm(fake):
    rec = fake()
    get_adapter("anthropic", "claude-opus-5-5", temperature=0.0).generate_decision("S", "U")
    assert "temperature" not in rec.calls[0][1]
    rec = fake(lambda kw: _message(kw["model"], thinking=None))
    get_adapter("anthropic", "claude-opus-4-6", temperature=0.0).generate_decision("S", "U")
    assert rec.calls[0][1]["temperature"] == 0.0           # the RQ6 probe's snapshot path


def test_a_refusal_is_its_own_class_and_is_not_retried(fake):
    details = SimpleNamespace(category="cyber", explanation="declined")
    rec = fake(lambda kw: _message(kw["model"], stop="refusal", text="", details=details))
    r = get_adapter("anthropic", "claude-sonnet-5-5").generate_decision("S", "U")
    assert r.success is False and r.error_class == ERROR_CLASS_REFUSAL
    assert "category=cyber" in r.error and r.metadata["attempt"] == 1
    assert len(rec.calls) == 1                              # no retry, no other model


def test_screening_call_raises_on_a_refusal(fake):
    fake(lambda kw: _message(kw["model"], stop="refusal", text=""))
    with pytest.raises(aa.ModelRefusal):
        get_adapter("anthropic", "claude-opus-5-5").generate_raw("S", "U")


def test_budget_stop_is_reported_not_hidden(fake, monkeypatch):
    monkeypatch.setattr("src.adapters.base._RETRY_DELAY_SECONDS", 0)
    fake(lambda kw: _message(kw["model"], stop="max_tokens", text=DECISION[:40]))
    r = get_adapter("anthropic", "claude-sonnet-5-5").generate_decision("S", "U")
    assert r.metadata["finish_reason"] == "max_tokens"


def test_thinking_tokens_read_from_either_shape():
    assert aa.thinking_tokens({"thinking_tokens": 54}) == 54          # extra field (dict)
    assert aa.thinking_tokens(SimpleNamespace(thinking_tokens=7)) == 7
    assert aa.thinking_tokens(None) is None


@pytest.mark.parametrize("model,cost", [("claude-sonnet-5-5", 12.0), ("claude-opus-5-5", 24.0)])
def test_rates(model, cost):
    assert compute_call_cost_usd(model, 1_000_000, 1_000_000) == pytest.approx(cost)


# ----------------------- reasoning_allowance_limit_ruling.md section 3 conditions
# Item 4: the call timeout is total wall-clock time, enforced by the adapter.
def test_wall_clock_deadline_is_total_not_per_chunk(fake, monkeypatch):
    import time

    from src.adapters import base

    def trickle(msg, stream):
        # A stream that keeps delivering chunks (so no per-read timeout ever
        # trips) until the adapter closes it.
        yield SimpleNamespace(type="message_start", message=SimpleNamespace(
            model=msg.model, usage=SimpleNamespace(input_tokens=1, output_tokens=1)))
        while not stream.closed:
            time.sleep(0.02)
            yield SimpleNamespace(type="content_block_delta")
    monkeypatch.setattr(base, "API_CALL_TIMEOUT_SECONDS", 0.3)
    rec = fake(events=trickle)
    t0 = time.monotonic()
    r = get_adapter("anthropic", "claude-opus-5-5").generate_decision("S", "U")
    assert time.monotonic() - t0 < 3
    assert r.success is False and r.error_class == "TIMEOUT" and "0s total deadline" in r.error
    assert r.raw_response == "" and r.metadata["attempt"] == 1 and len(rec.calls) == 1


# Item 5a: a stream that ends without message_stop is its own failed class.
def test_a_stream_without_message_stop_is_never_parsed(fake):
    rec = fake(events=lambda msg, stream=None: _events(msg, stop_event=False))
    r = get_adapter("anthropic", "claude-sonnet-5-5").generate_decision("S", "U")
    assert r.success is False and r.error_class == "STREAM_INCOMPLETE"
    assert r.raw_response == "" and r.decisions == []          # not parsed, not repaired
    assert r.metadata["attempt"] == 1 and len(rec.calls) == 1   # not retried


# Item 5b: stop reason, usage (thinking from message_delta) and model id are
# read from the stream on every call, not from the accumulated message.
def test_finish_usage_and_model_come_from_the_stream(fake, monkeypatch):
    monkeypatch.setattr("src.adapters.base._RETRY_DELAY_SECONDS", 0)

    def disagreeing_events(msg, stream=None):
        yield SimpleNamespace(type="message_start", message=SimpleNamespace(
            model="claude-opus-5-5", usage=SimpleNamespace(input_tokens=4321, output_tokens=1)))
        yield SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="max_tokens"),
                              usage=SimpleNamespace(input_tokens=4321, output_tokens=999,
                                                    output_tokens_details={"thinking_tokens": 777}))
        yield SimpleNamespace(type="message_stop")
    # The accumulated message says something else on every count.
    fake(reply=lambda kw: _message("some-other-model", stop="end_turn", thinking=1),
         events=disagreeing_events)
    r = get_adapter("anthropic", "claude-opus-5-5").generate_decision("S", "U")
    md = r.metadata
    assert r.model_id_returned == "claude-opus-5-5"
    assert md["finish_reason"] == "max_tokens" and md["thoughts_tokens"] == 777
    assert (md["input_tokens"], md["output_tokens"]) == (4321, 999)
    assert md["transport"] == "stream"


def test_refreshed_arms_stream_whatever_the_cap(fake):
    # Hub 2026-11-21: calibration and production use the same shape.
    for model in aa.REFRESHED_ARMS:
        rec = fake()
        get_adapter("anthropic", model).generate_decision("S", "U")
        assert [k for k, _ in rec.calls] == ["stream"]


# Item 1 / the snapshots: request bytes unchanged. The byte-level proof is
# tests/test_adapter_request_invariance.py (claude-sonnet-4-6 through the real
# SDK and a recording transport); this pins that it still covers Anthropic.
def test_snapshot_bytes_are_pinned_by_the_invariance_test():
    from pathlib import Path
    src = (Path(__file__).parent / "test_adapter_request_invariance.py").read_text(encoding="utf-8")
    assert '"anthropic": "claude-sonnet-4-6"' in src


def test_the_deadline_counts_the_wait_for_the_first_byte(fake, monkeypatch):
    """The clock starts before the request is sent: time spent waiting for the
    response headers is taken out of what the body may use."""
    from src.adapters import base

    def endless(msg, stream):
        yield SimpleNamespace(type="message_start", message=SimpleNamespace(
            model=msg.model, usage=SimpleNamespace(input_tokens=1, output_tokens=1)))
        while not stream.closed:
            time.sleep(0.02)
            yield SimpleNamespace(type="content_block_delta")
    monkeypatch.setattr(base, "API_CALL_TIMEOUT_SECONDS", 0.6)
    rec = fake(events=endless)
    rec.enter_delay = 0.4
    t0 = time.monotonic()
    r = get_adapter("anthropic", "claude-sonnet-5-5").generate_decision("S", "U")
    elapsed = time.monotonic() - t0
    assert r.error_class == "TIMEOUT"
    assert elapsed < 0.6 + 0.35, elapsed            # total, not headers + a fresh 0.6 s
    assert rec.calls[0][1]["timeout"] == 0.6
