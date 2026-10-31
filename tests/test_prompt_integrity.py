"""Rendered-prompt hash check (src/logging/prompt_integrity.py).

Two jobs. First, the cross-platform proof: the reference was rendered on the
build machine from a worktree of the frozen commit, and
test_current_tree_renders_the_frozen_reference re-renders it wherever the suite
runs -- in CI that is ubuntu-latest / CPython 3.11 on the production lock, the
environment the cycles run in. Second, the non-blocking contract: every failure
path returns a status and raises nothing, and the pipeline calls the check only
after decisions are made.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import src.prompt_builder as pb  # noqa: E402
from src.logging import prompt_golden_fixture as fixture  # noqa: E402
from src.logging import prompt_integrity as pi  # noqa: E402

FROZEN_COMMIT = "00557e557dd3cf9bbd911ac6b362ff8370d1e6fb"
ARMED_RUN = datetime(2026, 11, 2, 10, 0, tzinfo=fixture.EASTERN)


@pytest.fixture
def reference():
    return json.loads(pi.REFERENCE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Log to tmp and capture alerts instead of sending them."""
    alerts = []
    monkeypatch.setattr(pi, "LOG_DIR", tmp_path / "prompt_integrity")
    monkeypatch.setattr(pi, "send_alert", lambda *a, **k: alerts.append((a, k)))
    return tmp_path / "prompt_integrity", alerts


# ---- the reference -----------------------------------------------------------

def test_current_tree_renders_the_frozen_reference(reference):
    record = pi.evaluate(reference)
    assert record["status"] == "match", record
    assert record["combined_sha256"] == reference["combined_sha256"]


def test_reference_is_pinned_to_the_frozen_commit_and_this_fixture(reference):
    assert reference["frozen_commit"] == FROZEN_COMMIT
    assert reference["fixture_sha256_lf"] == pi.sha256_lf(pi.FIXTURE_PATH.read_bytes())
    assert set(reference["component_sha256"]) == set(fixture.COMPONENTS)
    for k in fixture.COMPONENTS:
        assert pi.text_sha256(reference["rendered_text"][k]) == reference["component_sha256"][k]
    assert pi.combined_sha256(reference["component_sha256"]) == reference["combined_sha256"]


def test_reference_render_is_the_amended_v4_form(reference):
    user = reference["rendered_text"]["decision_user"]
    assert "  Settled cash (deployable): $1,100.00 (55.0%)\n  Total cash:     $1,500.00\n" in user
    assert "EXECUTION MODE" not in user  # v4 component 3
    assert reference["rendered_text"]["decision_system"].startswith(pb.load_prompt_template("v4"))


# ---- what it detects -----------------------------------------------------------

def test_config_change_is_detected(reference, monkeypatch):
    real = pb.load_settings()
    changed = {**real, "portfolio_rules": {**real["portfolio_rules"], "max_positions": 49}}
    monkeypatch.setattr(pb, "load_settings", lambda: changed)
    record = pi.evaluate(reference)
    assert record["status"] == "mismatch"
    assert record["mismatched_components"] == ["decision_user"]
    diff = record["first_difference"]["decision_user"]
    assert diff["expected"].endswith("/ 50 max") and diff["actual"].endswith("/ 49 max")


def test_universe_change_is_detected(reference, monkeypatch):
    real = pb.load_universe()
    changed = {**real, "tickers": real["tickers"][:-1]}
    monkeypatch.setattr(pb, "load_universe", lambda: changed)
    import src.config_loader as cl
    monkeypatch.setattr(cl, "load_universe", lambda: changed)
    record = pi.evaluate(reference)
    assert record["status"] == "mismatch"
    assert set(record["mismatched_components"]) == {"decision_user", "screening_user"}


def test_template_change_is_detected(reference, monkeypatch):
    real = pb.load_prompt_template
    monkeypatch.setattr(pb, "load_prompt_template", lambda v: real(v) + " ")
    record = pi.evaluate(reference)
    assert record["status"] == "mismatch"
    assert record["mismatched_components"] == ["decision_system"]


# ---- every failure path is a status, never an exception -----------------------

def test_mismatch_logs_and_alerts_critical(sandbox, monkeypatch):
    log_dir, alerts = sandbox
    real = fixture.render_golden
    monkeypatch.setattr(fixture, "render_golden",
                        lambda: {**real(), "screening_system": "drifted"})
    record = pi.run_prompt_integrity_check(ARMED_RUN)
    assert record["status"] == "mismatch"
    logged = [json.loads(l) for l in (log_dir / "2026-11.jsonl").read_text().splitlines()]
    assert logged[-1]["status"] == "mismatch" and logged[-1]["boundary"] == ARMED_RUN.isoformat()
    (severity, title, _body, _ctx), kw = alerts[-1]
    assert severity == "CRITICAL" and kw["kind"] == "prompt_integrity"


def test_match_logs_and_does_not_alert(sandbox):
    log_dir, alerts = sandbox
    assert pi.run_prompt_integrity_check(ARMED_RUN)["status"] == "match"
    assert (log_dir / "2026-11.jsonl").exists() and alerts == []


def test_render_error_is_reported_not_raised(sandbox, monkeypatch):
    _log, alerts = sandbox
    def boom():
        raise RuntimeError("render failed")
    monkeypatch.setattr(fixture, "render_golden", boom)
    record = pi.run_prompt_integrity_check(ARMED_RUN)
    assert record["status"] == "render_error" and "render failed" in record["error"]
    assert alerts[-1][0][0] == "WARN"


def test_missing_reference_is_reported_not_raised(sandbox, monkeypatch, tmp_path):
    monkeypatch.setattr(pi, "REFERENCE_PATH", tmp_path / "absent.json")
    assert pi.run_prompt_integrity_check(ARMED_RUN)["status"] == "reference_unreadable"


def test_fixture_edit_reports_fixture_changed_not_drift(sandbox, monkeypatch, tmp_path):
    edited = tmp_path / "fixture.py"
    edited.write_bytes(pi.FIXTURE_PATH.read_bytes() + b"\n# edited\n")
    monkeypatch.setattr(pi, "FIXTURE_PATH", edited)
    assert pi.run_prompt_integrity_check(ARMED_RUN)["status"] == "fixture_changed"


def test_alert_and_log_failures_never_raise(monkeypatch, tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    monkeypatch.setattr(pi, "LOG_DIR", blocker / "sub")  # mkdir fails
    def alert_boom(*a, **k):
        raise OSError("smtp down")
    monkeypatch.setattr(pi, "send_alert", alert_boom)
    monkeypatch.setattr(fixture, "render_golden", lambda: {k: "x" for k in fixture.COMPONENTS})
    assert pi.run_prompt_integrity_check(ARMED_RUN)["status"] == "mismatch"


def test_unexpected_failure_returns_none(monkeypatch):
    def boom(*a, **k):
        raise MemoryError("anything at all")
    monkeypatch.setattr(pi, "evaluate", boom)
    assert pi.run_prompt_integrity_check(ARMED_RUN) is None


def test_not_armed_before_2026_10_31(sandbox):
    log_dir, alerts = sandbox
    assert pi.run_prompt_integrity_check(datetime(2026, 10, 30, 15, 30, tzinfo=fixture.EASTERN)) is None
    assert not log_dir.exists() and alerts == []


def test_bounded_entry_returns_within_its_limit(sandbox, monkeypatch):
    _log, alerts = sandbox
    # Patch the thread's target, not the render: the daemon thread outlives this
    # test's monkeypatch, and must have nothing left to write when it wakes.
    monkeypatch.setattr(pi, "run_prompt_integrity_check", lambda run_date: time.sleep(5))
    t0 = time.monotonic()
    pi.run_prompt_integrity_check_bounded(ARMED_RUN, time_limit=0.3)
    assert time.monotonic() - t0 < 2.0
    assert alerts[-1][1]["dedup_key"] == "prompt_integrity:timeout"


# ---- placement in the pipeline ---------------------------------------------------

def test_pipeline_runs_the_check_only_after_decisions_and_inside_a_guard():
    src = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")
    call = src.index("run_prompt_integrity_check_bounded(run_date)")
    assert src.index("as_completed(futures)") < call
    assert src.index("mark_boundary_handled(*boundary_parts(run_date))") < call
    guard = src.rindex("try:", 0, call)
    imp = src.index("from .logging.prompt_integrity import", guard)
    assert guard < imp < call < src.index("except Exception", call)
    assert "prompt_integrity" not in src[:src.index("def run_pipeline")]  # no top-level import
