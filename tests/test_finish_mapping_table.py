"""The committed finish-reason mapping table matches the adapters (hub H-OPS-15)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("fmt", ROOT / "scripts" / "finish_mapping_table.py")
fmt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fmt)


def test_committed_table_matches_the_code():
    committed = (ROOT / "docs" / "ops" / "finish_reason_mapping.md").read_text(encoding="utf-8")
    assert committed.replace("\r\n", "\n") == fmt.render()


def test_refusal_values_are_listed_by_name_only():
    from src.adapters import gemini_adapter as ga
    assert all(name.isupper() and "*" not in name for name in ga.REFUSAL_FINISH_REASONS)
    assert ga.finish_class("IMAGE_SAFETY_V2", False) == "abnormal"     # no pattern matching
