"""Regression tests for the alerting-configuration preflight.

Guards the defect class that produced the 2026-08-14 gap: wired code, absent
secret, silent skip. The recipient list moved to a repository secret, the code
shipped, the secret did not exist, and `send_email()`'s never-crash contract
turned that into a green run with alerting switched off.

The tests pin the three judgement calls in `preflight`, because each one is a
place where a plausible "simplification" would reintroduce the hole:

  * no credentials  -> NOT a problem (a dev checkout is not misconfigured)
  * credentials + empty recipients -> IS a problem (the actual defect)
  * channels are declared by the caller, never sniffed (the intraday workflow
    passes only ALERT_RECIPIENTS, the competitor workflow only
    COMPETITOR_ALERT_TO; sniffing cannot tell "unused here" from "missing")

Run with: python -m pytest tests/test_alerting_preflight.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.alerts import preflight
from src.alerts.preflight import AlertingConfigError, COMPETITOR, DAILY


@pytest.fixture
def wired(monkeypatch):
    """Gmail credentials present — the 'somebody intended mail to work' state."""
    monkeypatch.setenv("GMAIL_ADDRESS", "bot@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")


# ---------------------------------------------------------------- not wired

def test_no_credentials_is_not_a_problem(monkeypatch):
    """A developer checkout with no email configured must not fail."""
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.delenv("ALERT_RECIPIENTS", raising=False)
    monkeypatch.delenv("COMPETITOR_ALERT_TO", raising=False)
    assert preflight.check() == []
    assert preflight.transport_is_wired() is False


def test_half_configured_transport_is_not_wired(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "bot@example.com")
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    assert preflight.transport_is_wired() is False
    assert preflight.check() == []


# ---------------------------------------------------------------- the defect

def test_wired_transport_with_no_daily_recipients_is_a_problem(wired, monkeypatch):
    """This is the exact 2026-08-14 state: code deployed, secret absent."""
    monkeypatch.delenv("ALERT_RECIPIENTS", raising=False)
    problems = preflight.check((DAILY,))
    assert len(problems) == 1
    assert problems[0]["env_var"] == "ALERT_RECIPIENTS"
    assert "repository secret" in problems[0]["reason"]


def test_wired_transport_with_no_competitor_recipients_is_a_problem(wired, monkeypatch):
    monkeypatch.delenv("COMPETITOR_ALERT_TO", raising=False)
    problems = preflight.check((COMPETITOR,))
    assert len(problems) == 1
    assert problems[0]["env_var"] == "COMPETITOR_ALERT_TO"


def test_both_channels_reported_independently(wired, monkeypatch):
    monkeypatch.delenv("ALERT_RECIPIENTS", raising=False)
    monkeypatch.setenv("COMPETITOR_ALERT_TO", "hub@example.com")
    problems = preflight.check()
    assert [p["env_var"] for p in problems] == ["ALERT_RECIPIENTS"]


def test_configured_channels_pass(wired, monkeypatch):
    monkeypatch.setenv("ALERT_RECIPIENTS", "a@example.com,b@example.com")
    monkeypatch.setenv("COMPETITOR_ALERT_TO", "hub@example.com")
    assert preflight.check() == []


def test_whitespace_only_secret_counts_as_empty(wired, monkeypatch):
    """A secret created with an accidental blank value must not read as set."""
    monkeypatch.setenv("ALERT_RECIPIENTS", "   ,  , ")
    problems = preflight.check((DAILY,))
    assert len(problems) == 1


# ---------------------------------------------------------------- switch-off

def test_alerts_disabled_in_settings_is_not_a_problem(wired, monkeypatch):
    """A deliberate switch-off is not a misconfiguration."""
    monkeypatch.delenv("ALERT_RECIPIENTS", raising=False)
    assert preflight.check((DAILY,), {"alerts": {"enabled": False}}) == []


def test_alerts_enabled_default_still_checks(wired, monkeypatch):
    monkeypatch.delenv("ALERT_RECIPIENTS", raising=False)
    assert len(preflight.check((DAILY,), {"alerts": {}})) == 1


# ---------------------------------------------------------------- strictness

def test_strict_raises_and_nonstrict_does_not(wired, monkeypatch):
    """The trading pipeline must never be stopped by this; the monitor must."""
    monkeypatch.delenv("ALERT_RECIPIENTS", raising=False)
    assert len(preflight.assert_configured((DAILY,), strict=False)) == 1
    with pytest.raises(AlertingConfigError):
        preflight.assert_configured((DAILY,), strict=True)


def test_unknown_channel_is_rejected():
    with pytest.raises(ValueError):
        preflight.check(("not_a_channel",))


# ---------------------------------------------------------------- the CLI

def _run_cli(env, channel="all"):
    """Run the CLI with the four alerting vars pinned, whatever the machine has.

    The vars are set EMPTY rather than deleted. The CLI calls `load_env()`, and
    python-dotenv's default is `override=False` — it fills only keys absent from
    the environment. Deleting them would let a developer's real `.env` supply
    them and make the test machine-dependent; an empty string is 'present', so
    dotenv leaves it alone.

    It also happens to be the exact production condition being tested: a
    workflow that references an undeclared secret gets an empty string, not an
    unset variable.
    """
    import os
    e = dict(os.environ)
    e.update({"GMAIL_ADDRESS": "", "GMAIL_APP_PASSWORD": "",
              "ALERT_RECIPIENTS": "", "COMPETITOR_ALERT_TO": ""})
    e.update(env)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_alerting_config.py"), "--channel", channel],
        capture_output=True, text=True, env=e, cwd=str(ROOT))


def test_cli_exits_zero_when_unwired():
    r = _run_cli({})
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_exits_zero_when_configured():
    r = _run_cli({"GMAIL_ADDRESS": "b@example.com", "GMAIL_APP_PASSWORD": "x",
                  "ALERT_RECIPIENTS": "a@example.com", "COMPETITOR_ALERT_TO": "h@example.com"})
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_exits_nonzero_on_the_defect():
    """The non-zero exit is the entire mechanism — it is the one failure signal
    in this class that does not itself depend on email working."""
    r = _run_cli({"GMAIL_ADDRESS": "b@example.com", "GMAIL_APP_PASSWORD": "x"}, channel="daily")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "ALERTING MISCONFIGURED" in r.stdout
    assert "ALERT_RECIPIENTS" in r.stdout
