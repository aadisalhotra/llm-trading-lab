"""Regression tests for the 2026-08-14 alert-recipient plaintext scrub.

Two real addresses sat in config/settings.json from May 2026 until this scrub,
in a public repo, and a second copy of them was being appended to a COMMITTED
log on every alert send. Both surfaces are covered here, because the settings
fix alone would have been cosmetic while data/alerts/email_log.jsonl kept
publishing the same pair on every line.

The tests are deliberately about the committed artifacts, not just the code:
a future edit that reintroduces a recipient list into settings.json, or a
writer that goes back to logging addresses, fails here rather than at the next
audit. Run with: python -m pytest tests/test_alert_recipients_scrub.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.alerts.email_alerts import get_recipients, recipients_fingerprint

# Any address that is not an obvious documentation placeholder.
ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PLACEHOLDER_DOMAINS = ("example.com", "example.org", "example.net")


def _real_addresses(text: str) -> list[str]:
    return [a for a in ADDRESS.findall(text)
            if not a.lower().endswith(PLACEHOLDER_DOMAINS)]


# ---------------------------------------------------------------- committed config

def test_settings_json_carries_no_recipient_list():
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    assert "alert_recipients" not in settings, (
        "alert_recipients is back in settings.json — recipients belong in the "
        "ALERT_RECIPIENTS secret, not in a public repo"
    )


def test_settings_json_contains_no_email_address_anywhere():
    raw = (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
    assert not _real_addresses(raw), f"email address(es) in settings.json: {_real_addresses(raw)}"


def test_settings_json_points_at_the_env_var():
    """The pointer is what keeps the next reader from re-adding the list."""
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    assert "ALERT_RECIPIENTS" in settings.get("alert_recipients_source", "")


# ---------------------------------------------------------------- committed log

def test_committed_email_log_carries_no_addresses():
    log = ROOT / "data" / "alerts" / "email_log.jsonl"
    if not log.exists():
        return
    raw = log.read_text(encoding="utf-8")
    assert not _real_addresses(raw), (
        f"email address(es) in the committed email log: {set(_real_addresses(raw))}"
    )


def test_committed_email_log_rows_kept_their_audit_fields():
    """Scrubbing must not have cost the log its audit value."""
    log = ROOT / "data" / "alerts" / "email_log.jsonl"
    if not log.exists():
        return
    rows = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows, "email log is empty — the scrub must not have truncated it"
    for r in rows:
        assert "recipients" not in r
        assert isinstance(r.get("recipient_count"), int)
        assert re.fullmatch(r"[0-9a-f]{12}", r.get("recipients_fingerprint", ""))
        # The fields the log exists for.
        assert r.get("timestamp") and r.get("status") and r.get("subject")


# ---------------------------------------------------------------- the reader

def test_recipients_come_from_the_env_var(monkeypatch):
    monkeypatch.setenv("ALERT_RECIPIENTS", "a@example.com, b@example.com")
    assert get_recipients() == ["a@example.com", "b@example.com"]


def test_unset_env_var_means_no_recipients(monkeypatch):
    monkeypatch.delenv("ALERT_RECIPIENTS", raising=False)
    assert get_recipients() == []


def test_settings_are_never_a_fallback(monkeypatch):
    """The whole point of the scrub. A settings dict carrying a recipient list
    must not resurrect it — that is how a secret-backed list quietly stops
    being secret-backed."""
    monkeypatch.delenv("ALERT_RECIPIENTS", raising=False)
    assert get_recipients({"alert_recipients": ["leaked@example.com"]}) == []


def test_blanks_and_duplicates_are_dropped(monkeypatch):
    monkeypatch.setenv("ALERT_RECIPIENTS", " a@example.com , ,a@example.com, b@example.com ,")
    assert get_recipients() == ["a@example.com", "b@example.com"]


# ---------------------------------------------------------------- fingerprint

def test_fingerprint_is_stable_and_case_insensitive():
    assert recipients_fingerprint(["A@Example.com"]) == recipients_fingerprint(["a@example.com"])
    assert len(recipients_fingerprint(["a@example.com"])) == 12


def test_fingerprint_changes_when_the_set_changes():
    one = recipients_fingerprint(["a@example.com"])
    two = recipients_fingerprint(["a@example.com", "b@example.com"])
    swapped = recipients_fingerprint(["b@example.com", "a@example.com"])
    assert one != two
    assert two != swapped, "order is configuration — a reorder must be visible"


def test_fingerprint_contains_no_address():
    fp = recipients_fingerprint(["someone@example.com"])
    assert "someone" not in fp and "@" not in fp
