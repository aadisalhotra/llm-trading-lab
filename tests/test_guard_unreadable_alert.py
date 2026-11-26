"""Duplicate-guard-unreadable CRITICAL (hub 2026-11-16), now under Gate A.

The CRITICAL landed logging-only on 2026-11-17. Gate A
(tick_admission_guards_ruling.md section 3) then made both guards WITHHOLD on
the registered blocking conditions. This file pins both facts against the
pre-CRITICAL guard (BASE): every scenario Gate A does not touch returns the
same decision fields as BASE; the ones it does touch are named and asserted.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.logging import tick_spacing as new

ROOT = Path(__file__).resolve().parents[1]
BASE = "71d025fb4"     # this package's base: the guard code before the change
# 10:05 ET: inside the slot-identity start window (2026-11-26). At 15:10Z, the
# value before, every scenario would be a late start: 10:10 is the window's end.
NOW = datetime(2026, 11, 17, 15, 5, tzinfo=timezone.utc)
SETTINGS = {"tick_spacing": {"min_minutes": 20},
            "models": {b: {"enabled": True} for b in ("claude", "gpt", "grok")}}


def _old_module():
    src = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE}:src/logging/tick_spacing.py"],
                         capture_output=True, text=True, check=True, encoding="utf-8").stdout
    mod = types.ModuleType("src.logging._tick_spacing_base")
    mod.__package__ = "src.logging"
    exec(compile(src, "tick_spacing@base", "exec"), mod.__dict__)
    return mod


OLD = _old_module()


def _row(ts: str) -> str:
    return '{"timestamp": "%s", "model_key": "x"}\n' % ts


def _write(d: Path, files: dict[str, bytes | str | None]):
    d.mkdir(parents=True, exist_ok=True)
    for book, content in files.items():
        if content is None:
            continue
        p = d / f"{book}_2026-11.jsonl"
        p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


SCENARIOS = {
    "all_readable_spaced": {"claude": _row("2026-11-17T14:33:00"), "gpt": _row("2026-11-17T14:34:10"),
                            "grok": _row("2026-11-17T14:33:40")},
    "all_readable_too_soon": {"claude": _row("2026-11-17T15:02:00"), "gpt": _row("2026-11-17T15:03:00"),
                              "grok": _row("2026-11-17T15:02:30")},
    "one_torn_others_readable": {"claude": _row("2026-11-17T14:33:00") + '{"timestamp": "2026-11-17T15:0',
                                 "gpt": _row("2026-11-17T15:03:00"), "grok": _row("2026-11-17T15:02:30")},
    "all_torn": {"claude": '{"timest', "gpt": '{"ti', "grok": "nope"},
    "absent_first_tick_of_month": {"claude": None, "gpt": None, "grok": None},
    "one_absent": {"claude": None, "gpt": _row("2026-11-17T14:34:00"), "grok": _row("2026-11-17T14:33:00")},
}


# Scenarios where a registered Gate A blocking condition holds. Every other
# scenario must be decided exactly as before.
GATE_A_CHANGED = {"all_torn"}

# Slot identity (slot_identity_guard_ruling_2026-11-21) replaced the elapsed-time
# skip with two slot tests. On these scenarios it decides as BASE did (same skip,
# same withhold), under the new names: an open slot was "spaced", a slot this
# run's cohort already decided was "too_soon".
SLOT_REASON = {"spaced": "slot_open", "too_soon": "slot_taken", "no_prior_tick": "no_prior_tick"}


@pytest.mark.parametrize("name", sorted(set(SCENARIOS) - GATE_A_CHANGED))
def test_decision_fields_identical_to_the_base_commit(tmp_path, name):
    _write(tmp_path, SCENARIOS[name])
    o = OLD.check_spacing(SETTINGS, NOW, tmp_path)
    n = new.check_spacing(SETTINGS, NOW, tmp_path)
    assert (n.skip, SLOT_REASON.get(o.reason), n.last_decision, n.gap_minutes, n.min_minutes) ==         (o.skip, n.reason, o.last_decision, o.gap_minutes, o.min_minutes), (name, n, o)
    assert not n.withhold and not n.missed


def test_torn_rows_are_named_and_still_proceed(tmp_path):
    _write(tmp_path, SCENARIOS["one_torn_others_readable"])
    v = new.check_spacing(SETTINGS, NOW, tmp_path)
    assert v.skip is True                       # gpt/grok still answer: too soon
    assert [Path(p).name for p in v.unreadable] == ["claude_2026-11.jsonl"]


def test_all_torn_names_every_file_and_withholds(tmp_path):
    _write(tmp_path, SCENARIOS["all_torn"])
    o = OLD.check_spacing(SETTINGS, NOW, tmp_path)
    assert (o.skip, o.reason) == (False, "no_prior_tick")      # BASE: fail open
    v = new.check_spacing(SETTINGS, NOW, tmp_path)
    assert (v.withhold, v.skip, v.reason) == (True, False, "no_usable_row")
    assert sorted(Path(p).name for p in v.unreadable) == [
        "claude_2026-11.jsonl", "gpt_2026-11.jsonl", "grok_2026-11.jsonl"]


def test_absent_is_not_unreadable(tmp_path):
    _write(tmp_path, SCENARIOS["absent_first_tick_of_month"])
    v = new.check_spacing(SETTINGS, NOW, tmp_path)
    assert (v.skip, v.reason, v.unreadable) == (False, "no_prior_tick", ())


def test_os_error_names_the_file_and_withholds(tmp_path, monkeypatch):
    _write(tmp_path, SCENARIOS["all_readable_spaced"])
    real_open = open

    def guarded(path, *a, **kw):
        if str(path).endswith("gpt_2026-11.jsonl"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", guarded)
    v = new.check_spacing(SETTINGS, NOW, tmp_path)
    assert (v.withhold, v.skip, v.reason) == (True, False, "read_error")
    assert [Path(p).name for p in v.unreadable] == ["gpt_2026-11.jsonl"]


def test_existing_verdict_constructions_still_work():
    v = new.SpacingVerdict(False, "disabled", None, None, 20)
    assert v.unreadable == ()


# ------------------------------------------------------ pipeline wiring
PIPE = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")


def test_ledger_alert_uses_the_duplicate_guard_wording_and_withholds():
    # Anchored inside run_pipeline: the EOD admission function (2026-11-19)
    # holds its own ledger check and its own "elif is_boundary_handled(".
    body = PIPE[PIPE.index("def run_pipeline("):]
    start = body.index("_ledger, _ledger_err = load_ledger()")
    block = body[start:body.index("elif is_boundary_handled(", start)]
    assert '"Duplicate guard unreadable: data/state/handled_boundaries.json"' in block
    assert "_withhold_tick(" in block and block.rstrip().endswith("return 0")


def test_spacing_alert_fires_per_unreadable_file_and_withhold_precedes_skip():
    body = PIPE[PIPE.index("def run_pipeline("):]
    start = body.index("    verdict = check_spacing(")       # not EOD's e_verdict
    block = body[start:body.index("record_skip(verdict", start)]
    assert 'f"Duplicate guard unreadable: {bad}"' in block
    assert "for bad in verdict.unreadable:" in block
    alert = block[block.index("if verdict.unreadable:"):block.index("if verdict.withhold:")]
    assert "return" not in alert                 # the per-file alert never exits by itself
    withhold = block[block.index("if verdict.withhold:"):block.index("if verdict.skip:")]
    assert "_withhold_tick(" in withhold and "return 0" in withhold


def test_gate_a_fixes_the_non_utf8_crash_by_withholding(tmp_path):
    """FOUND 2026-11-17, FIXED BY GATE A. A log whose final row is not valid
    UTF-8 raised UnicodeDecodeError out of check_spacing and crashed the run.
    Under Gate A that is "a present log raises on read": the verdict withholds
    cleanly, names the file, and nothing escapes."""
    _write(tmp_path, {"claude": b'{"timestamp": "\xff\xfe"}\n', "gpt": _row("2026-11-17T14:34:00"),
                      "grok": _row("2026-11-17T14:33:00")})
    with pytest.raises(UnicodeDecodeError):
        OLD.check_spacing(SETTINGS, NOW, tmp_path)               # BASE: crash
    v = new.check_spacing(SETTINGS, NOW, tmp_path)
    assert (v.withhold, v.skip, v.reason) == (True, False, "read_error")
    assert [Path(p).name for p in v.unreadable] == ["claude_2026-11.jsonl"]
