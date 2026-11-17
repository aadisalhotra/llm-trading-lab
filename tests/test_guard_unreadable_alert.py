"""Duplicate-guard-unreadable CRITICAL (hub 2026-11-16): logging only.

Both guards keep their behaviour: they proceed when they cannot read. The only
addition is the CRITICAL. Gate A (tick_admission_guards_ruling.md section 3)
is the later change that makes them block.

Proof that behaviour is unchanged: the pre-change tick_spacing.py, loaded from
the commit this branch is based on, and the new one return the same decision
fields on every scenario, including the unreadable ones.
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
NOW = datetime(2026, 11, 17, 15, 10, tzinfo=timezone.utc)
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


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_decision_fields_identical_to_the_base_commit(tmp_path, name):
    _write(tmp_path, SCENARIOS[name])
    o = OLD.check_spacing(SETTINGS, NOW, tmp_path)
    n = new.check_spacing(SETTINGS, NOW, tmp_path)
    assert tuple(n)[:5] == tuple(o), (name, n, o)


def test_torn_rows_are_named_and_still_proceed(tmp_path):
    _write(tmp_path, SCENARIOS["one_torn_others_readable"])
    v = new.check_spacing(SETTINGS, NOW, tmp_path)
    assert v.skip is True                       # gpt/grok still answer: too soon
    assert [Path(p).name for p in v.unreadable] == ["claude_2026-11.jsonl"]


def test_all_torn_names_every_file_and_proceeds(tmp_path):
    _write(tmp_path, SCENARIOS["all_torn"])
    v = new.check_spacing(SETTINGS, NOW, tmp_path)
    assert (v.skip, v.reason) == (False, "no_prior_tick")
    assert sorted(Path(p).name for p in v.unreadable) == [
        "claude_2026-11.jsonl", "gpt_2026-11.jsonl", "grok_2026-11.jsonl"]


def test_absent_is_not_unreadable(tmp_path):
    _write(tmp_path, SCENARIOS["absent_first_tick_of_month"])
    v = new.check_spacing(SETTINGS, NOW, tmp_path)
    assert (v.skip, v.reason, v.unreadable) == (False, "no_prior_tick", ())


def test_os_error_names_the_file_and_proceeds(tmp_path, monkeypatch):
    _write(tmp_path, SCENARIOS["all_readable_spaced"])
    real_open = open

    def guarded(path, *a, **kw):
        if str(path).endswith("gpt_2026-11.jsonl"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", guarded)
    v = new.check_spacing(SETTINGS, NOW, tmp_path)
    assert (v.skip, v.reason) == (False, "read_error")
    assert any(p.endswith("gpt_2026-11.jsonl") for p in v.unreadable)


def test_existing_verdict_constructions_still_work():
    v = new.SpacingVerdict(False, "disabled", None, None, 20)
    assert v.unreadable == ()


# ------------------------------------------------------ pipeline wiring
PIPE = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")


def test_ledger_alert_uses_the_duplicate_guard_wording_and_still_proceeds():
    block = PIPE[PIPE.index("_ledger, _ledger_err = load_ledger()"):PIPE.index("elif is_boundary_handled(")]
    assert '"Duplicate guard unreadable: data/state/handled_boundaries.json"' in block
    assert "return" not in block      # the unreadable-ledger branch never exits the run


def test_spacing_alert_fires_per_unreadable_file_and_skip_logic_is_unchanged():
    block = PIPE[PIPE.index("verdict = check_spacing("):PIPE.index("record_skip(verdict")]
    assert 'f"Duplicate guard unreadable: {bad}"' in block
    assert "for bad in verdict.unreadable:" in block
    alert = block[block.index("if verdict.unreadable:"):block.index("if verdict.skip:")]
    assert "return" not in alert and "skip" not in alert.replace("`skip`", "")


def test_known_defect_for_gate_a_corrupt_bytes_raise_instead_of_failing_open(tmp_path):
    """FOUND 2026-11-17, NOT FIXED HERE (behaviour must not change in this
    package). The docstring says the guard FAILS OPEN, but a log whose final
    row is not valid UTF-8 raises UnicodeDecodeError (a ValueError), which
    check_spacing does not catch: the run crashes rather than proceeding or
    withholding. Identical before and after this package. Gate A owns the fix."""
    _write(tmp_path, {"claude": b'{"timestamp": "\xff\xfe"}\n', "gpt": _row("2026-11-17T14:34:00"),
                      "grok": _row("2026-11-17T14:33:00")})
    for mod in (OLD, new):
        with pytest.raises(UnicodeDecodeError):
            mod.check_spacing(SETTINGS, NOW, tmp_path)
