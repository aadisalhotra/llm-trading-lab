"""scripts/prompt_cutover_delta.py: config-change delta against the frozen prompt."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import prompt_cutover_delta as delta  # noqa: E402
import src.config_loader as cl  # noqa: E402
import src.prompt_builder as pb  # noqa: E402

CURRENT = json.loads((cl.CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))
UNIVERSE = cl.CONFIG_DIR / "universe.json"


def _candidate(tmp_path, **edits):
    s = json.loads(json.dumps(CURRENT))
    for path, value in edits.items():
        node = s
        *parents, leaf = path.split("__")
        for p in parents:
            node = node[p]
        node[leaf] = value
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(s), encoding="utf-8")
    return p


def test_mode_flip_is_prompt_neutral_and_controls_pass(tmp_path):
    code, report = delta.run(_candidate(tmp_path, mode="broker_paper"), UNIVERSE)
    assert code == 0 and report["verdict"] == "match"
    assert report["controls"] == {"positive_current_config_matches": True,
                                  "negative_max_positions_detected": True}
    assert "mode" not in report["settings_keys_read_by_render"]


def test_prompt_change_inside_a_config_change_is_named(tmp_path):
    code, report = delta.run(_candidate(tmp_path, portfolio_rules__max_trades_per_day=40), UNIVERSE)
    assert code == 1 and report["differing"] == ["decision_user"]
    d = report["first_difference"]["decision_user"]
    assert "50" in d["expected"] and "40" in d["actual"]


def test_override_restores_the_real_loaders(tmp_path):
    before = (pb.load_settings, pb.load_universe, cl.load_settings, cl.load_universe)
    delta.run(_candidate(tmp_path, mode="live"), UNIVERSE)
    assert (pb.load_settings, pb.load_universe, cl.load_settings, cl.load_universe) == before


def test_keys_read_by_the_render_are_the_registered_few(tmp_path):
    _code, report = delta.run(_candidate(tmp_path), UNIVERSE)
    assert set(report["settings_keys_read_by_render"]) == {
        "prompt_version", "portfolio_rules", "portfolio_rules.max_positions",
        "portfolio_rules.max_trades_per_day", "settlement",
        "settlement.enforce_settled_funds", "settlement.activation_date"}
