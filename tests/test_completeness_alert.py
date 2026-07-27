"""Proof tests for the daily decision-completeness alert (Operations ⑥,
2026-07-27). Fully offline — SMTP stubbed, decision logs simulated in tmp.

The hole being closed: on 2026-07-14 Gemini failed 11 of 13 cycles while
DAILY_SUMMARY reported status OK / 0 violations. The simulated-bad-day test
below reproduces exactly that day shape and proves an email fires.
Run with: python -m pytest tests/test_completeness_alert.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.alerts import alert_state, events

DATE = "2026-07-14"

SETTINGS = {
    "mode": "paper",
    "alert_recipients": ["a@example.com"],
    "alerts": {
        "enabled": True,
        "max_event_alerts_per_day": 10,
        "daily_completeness_min": 0.6,
    },
    "models": {
        "gemini": {"display_name": "Gemini 3.1 Pro", "enabled": True},
        "gpt": {"display_name": "GPT-5.4", "enabled": True},
    },
}


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate alert state + email transport (mirrors tests/test_alerts.py)."""
    monkeypatch.setattr(alert_state, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(events, "TRADES_DIR", tmp_path / "trades")
    (tmp_path / "trades").mkdir()

    sent: list[dict] = []

    def fake_send(subject, html, recipients=None, **kwargs):
        sent.append({"subject": subject, "html": html, "kwargs": kwargs})
        return True

    monkeypatch.setattr(events, "send_email", fake_send)
    return sent


def _write_day(model_key: str, n_ok: int, n_fail: int,
               error: str = ("Model response was not valid JSON even after repair: "
                             "Unterminated string starting at: line 3 column 21 (char 691)")) -> None:
    """Append a simulated day of decision-log rows for `model_key`."""
    path = events.TRADES_DIR / f"{model_key}_{DATE[:7]}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        for _ in range(n_ok):
            f.write(json.dumps({"date": DATE, "api_success": True, "api_error": None}) + "\n")
        for _ in range(n_fail):
            f.write(json.dumps({"date": DATE, "api_success": False, "api_error": error}) + "\n")


def _dispatch_all(specs: list[dict]) -> list[str]:
    return [
        events.dispatch_event(
            kind=s["kind"], severity=s["severity"], title=s["title"], body=s["body"],
            context=s.get("context"), dedup_key=s.get("dedup_key"), settings=SETTINGS,
        )
        for s in specs
    ]


def test_simulated_bad_day_fires_email(iso):
    """The July-14 shape: 11 of 13 Gemini cycles failed → CRITICAL email sent."""
    _write_day("gemini", n_ok=2, n_fail=11)
    _write_day("gpt", n_ok=13, n_fail=0)

    specs = events.detect_completeness_degradation(SETTINGS, DATE)
    assert len(specs) == 1, "only the degraded model should fire"
    spec = specs[0]
    assert spec["context"]["model"] == "gemini"
    # 2/13 = 0.154 < floor/2 (0.30) → a collapse, not a wobble.
    assert spec["severity"] == "CRITICAL"
    assert "11/13 cycles failed" in spec["title"]
    assert "Unterminated string" in spec["context"]["numbers"]["Dominant failure"]

    # Prove it actually FIRES through the real dispatch path (email sent).
    assert _dispatch_all(specs) == ["sent"]
    assert len(iso) == 1
    assert "completeness" in iso[0]["subject"].lower()


def test_warn_tier_between_half_floor_and_floor(iso):
    """7 of 13 ok (0.54): below the 0.6 floor but above floor/2 → WARN."""
    _write_day("gemini", n_ok=7, n_fail=6)
    specs = events.detect_completeness_degradation(SETTINGS, DATE)
    assert len(specs) == 1
    assert specs[0]["severity"] == "WARN"
    assert _dispatch_all(specs) == ["sent"]
    assert len(iso) == 1


def test_clean_day_and_at_floor_do_not_fire(iso):
    """A clean day and a day exactly AT the floor both stay silent."""
    _write_day("gemini", n_ok=13, n_fail=0)
    _write_day("gpt", n_ok=6, n_fail=4)  # 0.6 == floor → not below → silent
    assert events.detect_completeness_degradation(SETTINGS, DATE) == []
    assert iso == []


def test_partial_day_guard(iso):
    """Fewer than 5 logged cycles: never judged, even at 100% failure."""
    _write_day("gemini", n_ok=0, n_fail=4)
    assert events.detect_completeness_degradation(SETTINGS, DATE) == []
    assert iso == []


def test_fires_once_per_model_day(iso):
    """Second sweep the same day dedups — one email, not thirteen."""
    _write_day("gemini", n_ok=2, n_fail=11)
    specs1 = events.detect_completeness_degradation(SETTINGS, DATE)
    specs2 = events.detect_completeness_degradation(SETTINGS, DATE)
    assert _dispatch_all(specs1) == ["sent"]
    assert _dispatch_all(specs2) == ["deduped"]
    assert len(iso) == 1
