"""Publish pause: the deploy stops, the build does not, the monitor knows.

Registered by the Research ruling of 2026-11-04 (dashboard's next refresh held
across the cutover) and the hub's accepted design. The workflow assertions read
the real intraday.yml, because a pause that is only true of a settings key would
be exactly the "instrument reports fine" failure of CLAUDE.md rule 9.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts import check_dashboard_staleness as monitor

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "intraday.yml"
PAUSE_IF = "if: steps.publish.outputs.paused != 'true'"


def _pages_job() -> str:
    text = WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")
    return text[text.index("\n  pages:\n"):text.index("\n  notify-failure:\n")]


def _step(job: str, name: str) -> str:
    m = re.search(rf"      - name: {re.escape(name)}\n(.*?)(?=\n      - |\Z)", job, re.S)
    assert m, f"step {name!r} missing from the pages job"
    return m.group(1)


@pytest.mark.parametrize("name", ["Setup Pages", "Upload dashboard artifact", "Deploy"])
def test_every_deploy_step_is_gated(name):
    assert PAUSE_IF in _step(_pages_job(), name)


def test_retry_cannot_deploy_through_the_pause():
    # A skipped attempt has outcome 'skipped', so the retry's own condition stays
    # false. If that condition ever widened, the pause would leak here.
    assert "if: steps.deployment.outcome == 'failure'" in _step(_pages_job(), "Deploy (retry)")


def test_the_pause_is_read_before_anything_deploys():
    job = _pages_job()
    assert job.index("name: Read publish pause") < job.index("name: Setup Pages")
    assert "['publish']['dashboard_paused']" in job


def test_the_build_is_not_gated():
    # The build must continue: the detectors read data/dashboard.json. Only the
    # three deploy steps carry the condition, and none of them is in `run`.
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("steps.publish.outputs.paused") == 3
    assert "steps.publish" not in text[:text.index("\n  pages:")]


def _monday_14et():
    return datetime(2026, 11, 9, 14, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)


def test_paused_outage_shape_is_quiet_and_unpaused_alerts():
    now = _monday_14et()
    live, local = now - timedelta(hours=8), now - timedelta(minutes=20)
    assert monitor.decide(live, now, expected_generated_at=local, paused=False)["alert"] is True
    quiet = monitor.decide(live, now, expected_generated_at=local, paused=True)
    assert quiet["alert"] is False
    assert "publish pause" in quiet["reason"]


def test_flag_reads_true_only_on_literal_true(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"publish": {"dashboard_paused": True}}), encoding="utf-8")
    assert monitor.read_publish_pause(p) is True
    p.write_text(json.dumps({"publish": {"dashboard_paused": False}}), encoding="utf-8")
    assert monitor.read_publish_pause(p) is False


@pytest.mark.parametrize("content", ["{not json", "{}", '{"publish": {}}'])
def test_unreadable_flag_leaves_the_monitor_armed(tmp_path, content):
    p = tmp_path / "settings.json"
    p.write_text(content, encoding="utf-8")
    assert monitor.read_publish_pause(p) is False


def test_missing_settings_leaves_the_monitor_armed(tmp_path):
    assert monitor.read_publish_pause(tmp_path / "absent.json") is False


def test_committed_flag_is_on():
    assert monitor.read_publish_pause() is True
