"""The throwaway fail-stop live probe (hub dispatch 2026-11-17, Part 4 H).

Text checks (the CI lock has no YAML parser): the probe acts only on the dummy
target, never on intraday.yml; GITHUB_TOKEN is its only secret; it carries the
intraday fail-stop's own always() condition and disable call; and it never
cancels the target's queued runs (that is what Q3 observes)."""
from __future__ import annotations

import re
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PROBE = (WF / "failstop_probe.yml").read_text(encoding="utf-8")
TARGET = (WF / "failstop_target.yml").read_text(encoding="utf-8")


def _body(text):
    """The workflow without its comment lines."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def test_never_acts_on_intraday():
    assert "intraday.yml" not in _body(PROBE) and "intraday.yml" not in _body(TARGET)
    assert set(re.findall(r"workflows/([\w.-]+\.yml)", _body(PROBE))) == {"failstop_target.yml"}


def test_github_token_is_the_only_secret():
    assert set(re.findall(r"secrets\.(\w+)", PROBE)) == {"GITHUB_TOKEN"}
    assert "secrets." not in TARGET and "permissions: {}" in TARGET


def test_minimal_permissions():
    assert "permissions:\n  actions: write\n  contents: read\n" in PROBE


def test_carries_the_intraday_fail_stop_condition_and_disable_call():
    intraday = (WF / "intraday.yml").read_text(encoding="utf-8")
    cond = "always() && (needs.run.result == 'failure' || needs.run.result == 'cancelled'"
    assert cond in intraday and cond in PROBE
    assert "-X PUT" in PROBE and "/disable" in PROBE


def test_does_not_cancel_the_targets_queued_runs():
    assert "/cancel" not in _body(PROBE)


def test_target_queues_like_intraday():
    assert "cancel-in-progress: false" in TARGET and "workflow_dispatch" in TARGET
    assert "schedule" not in TARGET
