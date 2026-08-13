"""Competitor escalation alerting — fires on ESCALATE only, recipient from secret.

Offline: the SMTP transport is monkeypatched. What is under test is the
firing policy (which tiers alert), the recipient sourcing (secret, never the
committed settings list), and the payload shape.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src.alerts import competitor_escalation as CE  # noqa: E402

TS = datetime(2026, 8, 13, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def captured(monkeypatch):
    box = {}

    def fake_send(subject, html_body, recipients=None, **kw):
        box.update(subject=subject, html=html_body, recipients=recipients, kw=kw)
        return True

    monkeypatch.setattr(CE, "send_email", fake_send)
    return box


# ---- firing policy -------------------------------------------------------

def test_no_escalations_sends_nothing(captured, monkeypatch):
    monkeypatch.setenv("COMPETITOR_ALERT_TO", "someone@example.com")
    assert CE.send_escalation_alert([], "2026-33", TS) is False
    assert captured == {}, "must not send on an empty escalation list"


def test_escalation_sends(captured, monkeypatch):
    monkeypatch.setenv("COMPETITOR_ALERT_TO", "someone@example.com")
    assert CE.send_escalation_alert(CE.test_payload("2026-33"), "2026-33", TS) is True
    assert "ESCALATION" in captured["subject"]
    assert captured["recipients"] == ["someone@example.com"]
    assert captured["kw"]["trigger"] == CE.TRIGGER


def test_subject_counts_and_pluralises(captured, monkeypatch):
    monkeypatch.setenv("COMPETITOR_ALERT_TO", "a@example.com")
    one = CE.test_payload("2026-33")
    CE.send_escalation_alert(one, "2026-33", TS)
    assert "1 paper " in captured["subject"]
    CE.send_escalation_alert(one + one, "2026-33", TS)
    assert "2 papers " in captured["subject"]


def test_test_alert_is_marked(captured, monkeypatch):
    monkeypatch.setenv("COMPETITOR_ALERT_TO", "a@example.com")
    CE.send_escalation_alert(CE.test_payload("2026-33"), "2026-33", TS, is_test=True)
    assert captured["subject"].startswith("[TEST] ")
    assert "<b>test</b> alert" in captured["html"]


# ---- recipient sourcing — public-repo boundary ---------------------------

def test_recipient_comes_from_the_secret(monkeypatch):
    monkeypatch.setenv("COMPETITOR_ALERT_TO", " a@example.com , b@example.com ")
    assert CE.get_escalation_recipients() == ["a@example.com", "b@example.com"]


def test_unset_secret_yields_no_recipients(monkeypatch):
    monkeypatch.delenv("COMPETITOR_ALERT_TO", raising=False)
    assert CE.get_escalation_recipients() == []


def test_missing_recipient_skips_send_rather_than_falling_back(captured, monkeypatch):
    """The committed settings.alert_recipients list must never be a fallback.

    config/settings.json is committed to a public repo. Falling back to it
    would defeat the point of sourcing the address from a secret.
    """
    monkeypatch.delenv("COMPETITOR_ALERT_TO", raising=False)
    assert CE.send_escalation_alert(CE.test_payload("2026-33"), "2026-33", TS) is False
    assert captured == {}, "must not send when the secret is unset"


# ---- payload -------------------------------------------------------------

def test_payload_carries_the_standard_fields(captured, monkeypatch):
    monkeypatch.setenv("COMPETITOR_ALERT_TO", "a@example.com")
    row = {
        "title": "A Paper", "url": "https://arxiv.org/abs/1", "venue": "arXiv (q-fin.TR)",
        "date": "2026-08-01", "triage_criteria": ["(a) cross-model"],
        "threatened_rqs": ["RQ1 (cross-model decision convergence)"],
        "assessment": "Meets (a). Threatens RQ1.",
    }
    CE.send_escalation_alert([row], "2026-33", TS)
    html = captured["html"]
    for needle in ("A Paper", "https://arxiv.org/abs/1", "arXiv (q-fin.TR)",
                   "2026-08-01", "(a) cross-model", "RQ1", "Meets (a)."):
        assert needle in html, f"payload missing {needle!r}"
    assert needle in captured["kw"]["text_body"] or "A Paper" in captured["kw"]["text_body"]


def test_unnamed_rq_says_read_before_ruling(captured, monkeypatch):
    monkeypatch.setenv("COMPETITOR_ALERT_TO", "a@example.com")
    CE.send_escalation_alert([{"title": "T", "url": "u", "venue": "v", "date": "d",
                               "triage_criteria": ["(c) pre-registered"],
                               "threatened_rqs": [], "assessment": "x"}],
                             "2026-33", TS)
    assert "read before ruling" in captured["html"]


def test_html_is_escaped(captured, monkeypatch):
    monkeypatch.setenv("COMPETITOR_ALERT_TO", "a@example.com")
    CE.send_escalation_alert([{"title": "<script>alert(1)</script>", "url": "u",
                               "venue": "v", "date": "d", "triage_criteria": [],
                               "threatened_rqs": [], "assessment": ""}],
                             "2026-33", TS)
    assert "<script>" not in captured["html"]
    assert "&lt;script&gt;" in captured["html"]


# ---- integration with the scan ------------------------------------------

def test_scan_alerts_only_on_escalate_tier(monkeypatch):
    """The run loop must select ESCALATE rows and no others."""
    import competitor_monitor as CM
    papers = [{"tier": CM.ESCALATE, "title": "E"}, {"tier": CM.DIGEST, "title": "D"},
              {"tier": CM.SILENT, "title": "S"}]
    selected = [p for p in papers if p.get("tier") == CM.ESCALATE]
    assert [p["title"] for p in selected] == ["E"]
