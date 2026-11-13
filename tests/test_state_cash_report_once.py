"""Hub Thursday close 2026-11-12, item 3b: an explained negative-cash state
anomaly emails once, then folds into the digest while it persists unchanged.
GPT's -$0.0105 (a FILL_OVERRUN already alerted at the fill) emailed at 11-12
02:04Z, 02:41Z and 22:07Z.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_alerts import SETTINGS, iso  # noqa: E402,F401  (iso is a fixture)
from test_executor_sizing_floor import _book  # noqa: E402

from src.alerts import alert_state, digest, events  # noqa: E402


# --------------------------------------------------------------------------- #
# 3b. Explained negative cash: report once, then the digest
# --------------------------------------------------------------------------- #
def _cash_specs(monkeypatch, cash, carried):
    book = _book(cash=cash)
    book.fill_overrun_usd = carried
    monkeypatch.setattr("src.portfolio.load_portfolio", lambda key: book)
    monkeypatch.setattr(events, "_enabled_model_keys", lambda s: ["gpt"])
    monkeypatch.setattr(events, "_display_name", lambda s, k: "GPT-5.4")
    specs = events.detect_state_anomalies({"portfolio_rules": {}}, "2026-11-12")
    return [s for s in specs if s["dedup_key"] == "state_cash:gpt"]


def _dispatch(spec):
    return events.dispatch_event(spec["kind"], spec["severity"], spec["title"], spec["body"],
                                 spec.get("context"), dedup_key=spec["dedup_key"],
                                 report_once=spec.get("report_once"), settings=SETTINGS)


def _on_day(monkeypatch, day):
    monkeypatch.setattr(events, "session_date", lambda: day)


def test_an_explained_balance_emails_once_then_folds_into_the_digest(iso, monkeypatch):
    (spec,) = _cash_specs(monkeypatch, -0.0105, 0.0123)
    assert spec["severity"] == "WARN" and spec["report_once"][0] == "state_cash:gpt"

    _on_day(monkeypatch, "2026-11-12")
    assert _dispatch(spec) == "sent"
    _on_day(monkeypatch, "2026-11-13")
    assert _dispatch(spec) == "continuing"
    _on_day(monkeypatch, "2026-11-16")
    assert _dispatch(spec) == "continuing"
    assert len(iso) == 1                                    # one email in three evenings

    daily = alert_state.load_state()["daily"]
    assert daily["date"] == "2026-11-16" and daily["sent_count"] == 0
    assert [e["title"] for e in daily["continuing"]] == [spec["title"]]
    assert daily["overflow"] == []
    html = digest._continuing_section(daily["continuing"])
    assert "Continuing conditions" in html and spec["title"] in html


def test_the_same_evening_rerun_is_still_a_plain_dedup(iso, monkeypatch):
    (spec,) = _cash_specs(monkeypatch, -0.0105, 0.0123)
    _on_day(monkeypatch, "2026-11-12")
    assert _dispatch(spec) == "sent"
    assert _dispatch(spec) == "deduped"
    assert alert_state.load_state()["daily"]["continuing"] == []


def test_a_changed_balance_is_a_new_event_and_emails(iso, monkeypatch):
    (first,) = _cash_specs(monkeypatch, -0.0105, 0.0123)
    _on_day(monkeypatch, "2026-11-12")
    assert _dispatch(first) == "sent"
    (second,) = _cash_specs(monkeypatch, -0.0440, 0.0500)
    _on_day(monkeypatch, "2026-11-13")
    assert _dispatch(second) == "sent"
    assert len(iso) == 2


def test_an_unexplained_balance_is_critical_and_emails_every_evening(iso, monkeypatch):
    (spec,) = _cash_specs(monkeypatch, -5.00, 0.04)
    assert spec["severity"] == "CRITICAL" and "report_once" not in spec
    for day in ("2026-11-12", "2026-11-13", "2026-11-16"):
        _on_day(monkeypatch, day)
        assert _dispatch(spec) == "sent"
    assert len(iso) == 3


def test_a_failed_send_does_not_mark_it_reported(iso, monkeypatch):
    (spec,) = _cash_specs(monkeypatch, -0.0105, 0.0123)
    monkeypatch.setattr(events, "send_email", lambda *a, **k: False)
    _on_day(monkeypatch, "2026-11-12")
    assert _dispatch(spec) == "send_failed"
    assert alert_state.load_state()["reported_once"] == {}
    monkeypatch.setattr(events, "send_email", lambda *a, **k: True)
    _on_day(monkeypatch, "2026-11-13")
    assert _dispatch(spec) == "sent"


def test_the_eod_sweep_passes_report_once_through(iso, monkeypatch):
    (spec,) = _cash_specs(monkeypatch, -0.0105, 0.0123)
    seen = []
    monkeypatch.setattr(events, "dispatch_event", lambda *a, **k: seen.append(k) or "sent")
    for name in ("detect_macro_market_events", "detect_missed_runs",
                 "detect_completeness_degradation", "detect_oversized_trades",
                 "detect_negative_crossings", "detect_new_ath", "detect_milestones",
                 "detect_news_impact"):
        monkeypatch.setattr(events, name, lambda *a, **k: [])
    monkeypatch.setattr(events, "detect_state_anomalies", lambda *a, **k: [spec])
    events.run_eod_alert_sweep({"date": "2026-11-12"}, settings=SETTINGS)
    assert seen and seen[0]["report_once"] == spec["report_once"]


def test_a_state_file_without_the_ledger_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(alert_state, "STATE_FILE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text('{"milestones_fired": {}, "daily": {"date": "x"}}',
                                         encoding="utf-8")
    st = alert_state.load_state()
    assert st["reported_once"] == {} and st["daily"]["continuing"] == []
