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


class _Recorder:
    def __init__(self, reply):
        self.calls: list[tuple[str, dict]] = []
        rec = self

        class _Stream:
            def __init__(self, kw):
                self.kw = kw

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __iter__(self):
                # As the API streams it: the final usage, with the thinking
                # count, arrives on message_delta; the SDK's accumulated
                # message drops output_tokens_details.
                self.msg = reply(self.kw)
                yield SimpleNamespace(type="message_start")
                det = self.msg.usage.output_tokens_details
                usage = SimpleNamespace(**{**vars(self.msg.usage), "output_tokens_details":
                                           ({"thinking_tokens": det.thinking_tokens} if det else None)})
                yield SimpleNamespace(type="message_delta", usage=usage)
                yield SimpleNamespace(type="message_stop")

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

    def install(reply=lambda kw: _message(kw["model"])):
        rec = _Recorder(reply)
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
    assert set(kw) == {"model", "max_tokens", "system", "messages", "thinking", "output_config"}
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
