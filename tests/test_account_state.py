"""ACCOUNT_STATE failure class (rossi_review_rulings.md section 2.4, rev. 2).

  * the classifier keys on the error TEXT, per provider, from a closed set;
  * the verbatim stored forms classify, and the near misses do not (xAI's 429
    at capacity, Google's 429 quota-exceeded, other providers);
  * the class changes attribution only: the adapter's return is the same shape
    for ACCOUNT_STATE as for API_ERROR;
  * any occurrence raises an immediate CRITICAL naming the book and provider;
  * nothing in control flow reads the class (grep-proved below);
  * the retro annotation holds exactly the 20 Aug-Sep records, verbatim.

No network: every transport is a fake.
"""
from __future__ import annotations

import hashlib
import json
import re
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from src.adapters import base as base_mod
from src.adapters.base import (
    ACCOUNT_STATE_PATTERNS,
    ERROR_CLASS_ACCOUNT_STATE,
    ERROR_CLASS_API_ERROR,
    BaseAdapter,
    is_account_state,
)
from src.adapters.deepseek_adapter import DeepSeekAdapter
from src.adapters.xai_adapter import XAIAdapter
from src.alerts.account_state import alert_if_account_state

ROOT = Path(__file__).resolve().parents[1]
ANNOTATION = ROOT / "data" / "annotations" / "account_state_retro_2026-11-17.jsonl"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(self, *a, **kw):
        raise OSError("test_account_state: outbound connect refused")
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _stored(path: str, line: int) -> str:
    """The verbatim api_error of one committed decision record."""
    raw = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{path}"],
                         capture_output=True, check=True).stdout
    return json.loads(raw.split(b"\n")[line - 1])["api_error"]


DEEPSEEK_AUG = ("data/trades/deepseek_2026-08.jsonl", 244)
DEEPSEEK_NOV = ("data/trades/deepseek_2026-11.jsonl", 20)   # "(request_id: ...)" form
XAI_403 = ("data/trades/grok_2026-08.jsonl", 237)
GOOGLE_429_PREPAY = ("data/trades/gemini_2026-08.jsonl", 66)
XAI_429_CAPACITY = ("data/trades/grok_2026-09.jsonl", 264)
GOOGLE_429_QUOTA = ("data/trades/gemini_2026-04.jsonl", 77)
ANTHROPIC_CREDIT = ("data/trades/claude_opus_2026-04.jsonl", 1)


# ------------------------------------------------------------------ the set
def test_closed_set_is_exactly_the_three_registered_forms():
    assert set(ACCOUNT_STATE_PATTERNS) == {"deepseek", "xai", "google"}
    assert all(len(v) == 1 for v in ACCOUNT_STATE_PATTERNS.values())


@pytest.mark.parametrize("provider,src", [
    ("deepseek", DEEPSEEK_AUG),
    ("deepseek", DEEPSEEK_NOV),
    ("xai", XAI_403),
    ("google", GOOGLE_429_PREPAY),
])
def test_verbatim_stored_forms_classify(provider, src):
    assert is_account_state(provider, _stored(*src))


@pytest.mark.parametrize("provider,src,why", [
    ("xai", XAI_429_CAPACITY, "429 at capacity is provider availability"),
    ("google", GOOGLE_429_QUOTA, "quota-exceeded is not a registered form"),
    ("anthropic", ANTHROPIC_CREDIT, "Anthropic has no registered form"),
])
def test_near_misses_do_not_classify(provider, src, why):
    assert not is_account_state(provider, _stored(*src)), why


def test_a_form_only_matches_its_own_provider():
    deepseek = _stored(*DEEPSEEK_AUG)
    assert not is_account_state("xai", deepseek)
    assert not is_account_state("google", _stored(*XAI_403))
    assert not is_account_state("", deepseek)


def test_status_code_alone_never_classifies():
    assert not is_account_state("deepseek", 'DeepSeek API 402: {"error":{"message":"Payment Required"}}')
    assert not is_account_state("xai", 'xAI API 403: {"code":"forbidden","error":"region"}')
    assert not is_account_state("google", "429 RESOURCE_EXHAUSTED. rate limit for requests per minute")
    assert not is_account_state("deepseek", None)
    assert not is_account_state("deepseek", "")


def test_word_boundary_on_the_deepseek_message():
    assert not is_account_state("deepseek", 'DeepSeek API 402: {"error":{"message":"Insufficient Balances"}}')


# ------------------------------------------------------ through the adapter
def _http(monkeypatch, status: int, body: str):
    def send(self, prepared, **kw):
        resp = requests.Response()
        resp.status_code = status
        resp._content = body.encode("utf-8")
        resp.request = prepared
        return resp
    monkeypatch.setattr(requests.Session, "send", send)


def test_xai_403_end_to_end_is_account_state(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "k")
    stored = _stored(*XAI_403)
    _http(monkeypatch, 403, stored.split(": ", 1)[1])
    r = XAIAdapter("m").generate_decision("s", "u")
    assert (r.success, r.error_class) == (False, ERROR_CLASS_ACCOUNT_STATE)


def test_xai_429_capacity_end_to_end_stays_api_error(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "k")
    _http(monkeypatch, 429, _stored(*XAI_429_CAPACITY).split(": ", 1)[1])
    r = XAIAdapter("m").generate_decision("s", "u")
    assert r.error_class == ERROR_CLASS_API_ERROR


@pytest.mark.parametrize("src", [DEEPSEEK_AUG, DEEPSEEK_NOV])
def test_deepseek_402_end_to_end_is_account_state(monkeypatch, src):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    _http(monkeypatch, 402, _stored(*src).split(": ", 1)[1])
    r = DeepSeekAdapter("m").generate_decision("s", "u")
    assert r.error_class == ERROR_CLASS_ACCOUNT_STATE


class _Google(BaseAdapter):
    provider_name = "google"

    def __init__(self, exc):
        super().__init__("m")
        self._exc = exc

    def _call_api(self, system_prompt, user_prompt, images=None):
        raise self._exc


def test_google_prepay_429_is_account_state_and_quota_429_is_not():
    assert _Google(RuntimeError(_stored(*GOOGLE_429_PREPAY))).generate_decision("s", "u").error_class \
        == ERROR_CLASS_ACCOUNT_STATE
    assert _Google(RuntimeError(_stored(*GOOGLE_429_QUOTA))).generate_decision("s", "u").error_class \
        == ERROR_CLASS_API_ERROR


def test_class_changes_attribution_never_the_result_shape():
    a = _Google(RuntimeError(_stored(*GOOGLE_429_PREPAY))).generate_decision("s", "u")
    b = _Google(RuntimeError("500 INTERNAL")).generate_decision("s", "u")
    for r in (a, b):
        assert (r.success, r.decisions, r.metadata) == (False, [], {"attempt": 1})
    assert {a.error_class, b.error_class} == {ERROR_CLASS_ACCOUNT_STATE, ERROR_CLASS_API_ERROR}


# ------------------------------------------------------------------ alarm
class _Sent(list):
    def __call__(self, *a, **kw):
        self.append((a, kw))


def _result(cls, provider="deepseek", error="DeepSeek API 402: ..."):
    return SimpleNamespace(error_class=cls, provider=provider, error=error, success=False)


def test_account_state_raises_a_critical_naming_book_and_provider():
    sent = _Sent()
    assert alert_if_account_state("deepseek", _result(ERROR_CLASS_ACCOUNT_STATE), "20261103T173900Z", sent)
    (args, kw), = sent
    assert args[0] == "CRITICAL"
    assert "deepseek" in args[1] and "DeepSeek" in args[1]
    assert kw["kind"] == "account_state"
    assert kw["dedup_key"] == "account_state:deepseek:20261103T173900Z"


@pytest.mark.parametrize("provider,name", [("xai", "xAI"), ("google", "Google (Gemini)")])
def test_alert_names_each_provider(provider, name):
    sent = _Sent()
    alert_if_account_state("grok", _result(ERROR_CLASS_ACCOUNT_STATE, provider), "c", sent)
    assert name in sent[0][0][1]


@pytest.mark.parametrize("cls", [ERROR_CLASS_API_ERROR, "TIMEOUT", "PARSE_FAILURE", None])
def test_no_alert_for_other_classes(cls):
    sent = _Sent()
    assert not alert_if_account_state("grok", _result(cls), "c", sent)
    assert sent == []


def test_the_alarm_never_raises_into_the_tick():
    def boom(*a, **kw):
        raise RuntimeError("smtp down")
    assert alert_if_account_state("gpt", _result(ERROR_CLASS_ACCOUNT_STATE), "c", boom) is False


def test_pipeline_calls_the_alarm_unconditionally_in_the_failed_call_branch():
    src = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")
    branch = src[src.index("    if not decision_result.success:"):]
    branch = branch[:branch.index("log_decision_run(")]
    assert "alert_if_account_state(model_key, decision_result, executor.cycle_id)" in branch
    # no `if ... error_class` anywhere in the branch: the call is unconditional
    assert "error_class" not in branch


# ----------------------------------------------- nothing in control flow reads it
def test_grep_proof_only_three_sites_touch_the_class():
    """Every line under src/ naming error_class or api_error_class. Expected:
    base.py sets it, decision_log.py records it, alerts/account_state.py alerts
    on it. Any other reader is a control-flow read and fails this test."""
    hits = subprocess.run(
        ["git", "-C", str(ROOT), "grep", "--untracked", "-n", "-E", r"error_class", "--", "src/"],
        capture_output=True, text=True).stdout.splitlines()
    files = {h.split(":", 1)[0] for h in hits}
    assert files == {"src/adapters/base.py", "src/logging/decision_log.py",
                     "src/alerts/account_state.py"}, sorted(files)
    record = [h for h in hits if h.startswith("src/logging/decision_log.py")]
    assert all("getattr(decision_result, \"error_class\", None)" in h or h.split(":", 2)[2].lstrip().startswith("#")
               for h in record), record


# --------------------------------------------------------- retro annotation
def test_retro_annotation_is_every_closed_set_match_from_april_verbatim():
    """Complete, not scoped to one window (hub 2026-11-17): every decision
    record from 2026-04 onward whose stored api_error matches the closed set."""
    rows = [json.loads(l) for l in ANNOTATION.read_text(encoding="utf-8").splitlines() if l.strip()]
    from collections import Counter
    assert Counter((r["date"][:7], r["model_key"]) for r in rows) == {
        ("2026-08", "grok"): 13, ("2026-08", "deepseek"): 6, ("2026-08", "gemini"): 1,
        ("2026-11", "deepseek"): 19}
    for r in rows:
        assert r["api_error_class"] == ERROR_CLASS_ACCOUNT_STATE
        assert r["classification_basis"] == "classified retroactively from stored error text"
        assert r["completeness_denominator_change"] == 0
        raw = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{r['source_file']}"],
                             capture_output=True, check=True).stdout.split(b"\n")[r["source_line"] - 1]
        assert hashlib.sha256(raw).hexdigest() == r["source_line_sha256"]
        assert json.loads(raw)["api_error"] == r["api_error_verbatim"]
        assert is_account_state(r["provider"], r["api_error_verbatim"])
        # the 2026-11-03 wording change is noted on exactly the rows that carry it
        assert ("wording_note" in r) == ("(request_id:" in r["api_error_verbatim"])


def test_annotation_is_complete_against_a_fresh_scan():
    """No closed-set match from April onward is missing from the annotation."""
    import glob
    annotated = {(r["source_file"], r["source_line"]) for r in
                 (json.loads(l) for l in ANNOTATION.read_text(encoding="utf-8").splitlines() if l.strip())}
    found = set()
    for path in sorted(glob.glob(str(ROOT / "data" / "trades" / "*_2026-*.jsonl"))):
        rel = "data/trades/" + Path(path).name
        if rel[len("data/trades/"):].split("_")[-1][:7] < "2026-04":
            continue
        raw = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{rel}"],
                             capture_output=True, check=True).stdout.split(b"\n")
        for i, line in enumerate(raw, 1):
            if not line.strip():
                continue
            rec = json.loads(line)
            if not rec.get("api_success") and is_account_state(rec.get("provider", ""), rec.get("api_error")):
                found.add((rel, i))
    assert found == annotated
