"""The venue-path handover script (Batch 4 item (e)): plan, proofs, rollback, refusals."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import handover_transition as ht  # noqa: E402
from src import config_loader as cl  # noqa: E402

SNAPS = ["claude", "claude_opus", "gpt", "gemini", "grok"]
ARMS = list(cl.REFRESHED_BOOK_KEYS)
ENDPOINT = "2026-12-04"
FIRST = "2026-12-07"
SAT = datetime(2026, 12, 5, 15, 0, tzinfo=timezone.utc)          # Sat 10:00 ET
SETTINGS_BYTES = (ROOT / "config" / "settings.json").read_bytes()
SETTINGS = json.loads(SETTINGS_BYTES)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def world(tmp_path):
    """A git repo holding config/, data/state and data/performance as at the
    12-04 EOD commit: five venue books, five shakedown books, DeepSeek."""
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "config" / "settings.json").write_bytes(SETTINGS_BYTES)
    state, perf = root / "data" / "state", root / "data" / "performance"
    state.mkdir(parents=True)
    perf.mkdir(parents=True)
    for b in SNAPS + ARMS + ["deepseek"]:
        epoch = "broker_paper" if b in SNAPS else "paper"
        cash = {"deepseek": 100_000.0}.get(b, 1900.0 + len(b))
        (state / f"{b}.json").write_text(json.dumps({
            "model_key": b, "cash": cash, "halted": False,
            "inception_value": 100_000.0 if b == "deepseek" else 2000.0,
            "inception_date": "2026-12-01" if b in ARMS else "2026-11-06",
            "last_updated": "", "holdings": {}, "intraday": {}, "unsettled_cash": [],
            "inception_epoch": epoch}, indent=2), encoding="utf-8")
        (perf / f"{b}.jsonl").write_text(
            json.dumps({"date": "2026-12-03", "total_value": 1.0}) + "\n"
            + json.dumps({"date": ENDPOINT, "total_value": 1999.5, "cash": 10.0}) + "\n"
            + json.dumps({"date": ENDPOINT, "total_value": 2001.25, "cash": 11.0}) + "\n",
            encoding="utf-8")
    vh = root / "data" / "venue_history" / f"{ENDPOINT}_close"
    vh.mkdir(parents=True)
    (vh / "MANIFEST.json").write_text("{}", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "12-04 EOD")
    return root


def good_venue(settings_after):
    return {"positions": 0, "open_orders": 0, "cash": 99_999.99, "five_fresh_books_cash": 10_000.0,
            "registered_unallocated": 89_999.99, "implied_unallocated": 89_999.99,
            "cash_agrees": True, "flat": True}


def snapshot_bytes(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".git" not in p.parts}


# ----------------------------------------------------------------- the plan

def test_the_plan_is_31_steps_in_the_registered_order():
    steps = ht.plan(SETTINGS, ENDPOINT)
    assert [s.kind for s in steps] == (["series_close"] * 10 + ["archive"] * 10
                                       + ["cohort_switch"] + ["inception"] * 10)
    assert [s.book for s in steps[:10]] == SNAPS + ARMS
    assert "deepseek" not in {s.book for s in steps}


# ------------------------------------------------------- the settings edit

def test_the_cohort_switch_is_a_surgical_edit_with_its_proofs():
    out = ht.switch_cohort_text(SETTINGS_BYTES, SNAPS, ARMS)
    after = json.loads(out)
    assert after["capital_structure"]["live_cohort_keys"] == ARMS
    old = b'"live_cohort_keys": ' + json.dumps(SNAPS).encode()
    new = b'"live_cohort_keys": ' + json.dumps(ARMS).encode()
    assert out == SETTINGS_BYTES.replace(old, new)
    assert out.count(b"\r\n") == SETTINGS_BYTES.count(b"\r\n")


def test_the_switch_refuses_when_the_array_is_not_the_snapshots():
    with pytest.raises(ht.HandoverRefused, match="exactly once"):
        ht.switch_cohort_text(SETTINGS_BYTES, ARMS, SNAPS)


# ------------------------------------------------------------- execution

def test_execute_makes_every_change_and_records_it(world):
    rec = ht.run(world, world, SETTINGS, ENDPOINT, FIRST, venue=good_venue, strict=True,
                 seed_missing=False, now=SAT)
    assert all(s["done"] for s in rec["steps"]) and len(rec["steps"]) == 31
    head = _git(world, "rev-parse", "HEAD")
    closes = {s["book"]: s["result"] for s in rec["steps"] if s["kind"] == "series_close"}
    assert all(c["terminal_equity"] == 2001.25 for c in closes.values()), "last EOD row of the day"
    for s in rec["steps"]:
        if s["kind"] == "archive":
            arch = world / s["result"]["archived_to"]
            assert s["result"]["pinned_to_commit"] == head
            assert not os.stat(arch).st_mode & stat.S_IWRITE, "archive is read-only"
            assert arch.read_bytes() == subprocess.run(
                ["git", "show", f"HEAD:data/state/{s['book']}.json"], cwd=world,
                capture_output=True, check=True).stdout
    settings_after = json.loads((world / "config" / "settings.json").read_bytes())
    assert settings_after["capital_structure"]["live_cohort_keys"] == ARMS
    for b in SNAPS + ARMS:
        st = json.loads((world / "data" / "state" / f"{b}.json").read_text())
        assert st["cash"] == 2000.0 and st["inception_date"] == FIRST and st["holdings"] == {}
        assert st["inception_epoch"] == ("broker_paper" if b in ARMS else "paper")
    # DeepSeek is untouched.
    assert json.loads((world / "data" / "state" / "deepseek.json").read_text())["cash"] == 100_000.0
    assert rec["verify"]["venue_books_are_the_refreshed_arms"]
    assert rec["verify"]["venue_books_cash"] == 10_000.0
    assert (world / "data" / "state_archive" / f"handover_{ENDPOINT}.json").exists()
    assert "config/settings.json" in rec["paths_to_commit"]


def test_a_failure_part_way_restores_every_byte(world, monkeypatch):
    before = snapshot_bytes(world)
    calls = {"n": 0}
    real = cl.book_inception_capital

    def fail_on_the_eighth(*a, **k):
        calls["n"] += 1
        if calls["n"] == 8:
            raise RuntimeError("simulated failure at inception 8")
        return real(*a, **k)
    monkeypatch.setattr(cl, "book_inception_capital", fail_on_the_eighth)
    with pytest.raises(RuntimeError, match="inception 8"):
        ht.run(world, world, SETTINGS, ENDPOINT, FIRST, venue=good_venue, strict=True,
               seed_missing=False, now=SAT)
    assert snapshot_bytes(world) == before
    assert not (world / "data" / "state_archive").exists()


# --------------------------------------------------------------- refusals

def _refused(world, match, **kw):
    before = snapshot_bytes(world)
    args = dict(venue=good_venue, strict=True, seed_missing=False, now=SAT)
    args.update(kw)
    with pytest.raises(ht.HandoverRefused, match=match):
        ht.run(world, world, SETTINGS, ENDPOINT, FIRST, **args)
    assert snapshot_bytes(world) == before


def test_refuses_in_market_hours(world):
    _refused(world, "market is open", now=datetime(2026, 12, 4, 15, 0, tzinfo=timezone.utc))


def test_refuses_before_the_endpoint_closes(world):
    _refused(world, "has not closed", now=datetime(2026, 12, 4, 20, 30, tzinfo=timezone.utc))


def test_refuses_a_venue_that_is_not_flat(world):
    _refused(world, "0 open orders|positions", venue=lambda s: {**good_venue(s), "positions": 2, "flat": False})


def test_refuses_when_the_cash_baseline_disagrees(world):
    _refused(world, "implied unallocated", venue=lambda s: {**good_venue(s), "cash_agrees": False})


def test_refuses_when_the_venue_cannot_be_read(world):
    def down(s):
        raise ConnectionError("timed out")
    _refused(world, "could not be read", venue=down)


def test_refuses_without_the_committed_export(world):
    _git(world, "rm", "-q", f"data/venue_history/{ENDPOINT}_close/MANIFEST.json")
    _git(world, "commit", "-qm", "drop export")
    _refused(world, "venue-history export")


def test_refuses_uncommitted_state(world):
    p = world / "data" / "state" / "gpt.json"
    p.write_text(p.read_text().replace("1903.0", "1904.0"))
    _refused(world, "uncommitted changes")


def test_refuses_a_missing_eod_row(world):
    p = world / "data" / "performance" / "gpt.jsonl"
    p.write_text(json.dumps({"date": "2026-12-03", "total_value": 1.0}) + "\n")
    _git(world, "commit", "-qam", "no eod")
    _refused(world, "gpt has no EOD row")


def test_refuses_a_second_run(world):
    ht.run(world, world, SETTINGS, ENDPOINT, FIRST, venue=good_venue, strict=True,
           seed_missing=False, now=SAT)
    settings_after = json.loads((world / "config" / "settings.json").read_bytes())
    with pytest.raises(ht.HandoverRefused, match="already switched|already exists"):
        ht.run(world, world, settings_after, ENDPOINT, FIRST, venue=good_venue, strict=True,
               seed_missing=False, now=SAT)


def test_a_dry_run_seeds_missing_shakedown_books_and_says_so(world):
    for b in ARMS:
        (world / "data" / "state" / f"{b}.json").unlink()
    rec = ht.run(world, world, SETTINGS, ENDPOINT, FIRST, venue=None, strict=False,
                 seed_missing=True, now=datetime(2026, 11, 28, 15, tzinfo=timezone.utc))
    assert rec["seeded_for_dry_run"] == ARMS and rec["mode"] == "dry_run"
    assert any("has not closed" in n for n in rec["notes"])
    assert all(s["done"] for s in rec["steps"])


def test_the_script_never_writes_to_the_venue():
    src = (ROOT / "scripts" / "handover_transition.py").read_text(encoding="utf-8")
    calls = [line for line in src.splitlines() if "_request(" in line]
    assert calls and all('"GET"' in line for line in calls)
