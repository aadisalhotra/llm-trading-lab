"""The calibration tool's rules (scripts/calibration/calibrate_refreshed_arms.py).

No provider is contacted: the rules are pure functions, and the set loader
reads synthetic prompt files on tmp_path.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "calib", ROOT / "scripts" / "calibration" / "calibrate_refreshed_arms.py")
cal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cal)


# ---------------------------------------------------------------- t quantile
@pytest.mark.parametrize("df,expected", [(29, 1.699127), (30, 1.697261), (38, 1.685954),
                                         (10, 1.812461), (120, 1.657651)])
def test_t_quantile_matches_tables(df, expected):
    assert cal.t_ppf(0.95, df) == pytest.approx(expected, abs=2e-5)


# ---------------------------------------------------------- allowance, ladder
@pytest.mark.parametrize("R,al", [(0, 4096), (1500, 4096), (2048, 4096), (2049, 8192),
                                  (6000, 12288), (8192, 16384)])
def test_allowance_rounds_up_with_the_floor(R, al):
    assert cal.allowance(R) == al


def test_ladder_rungs_for_argon():
    spec = cal.ARMS["cb_argon"]                              # L 32,768, H 16,384
    assert cal.ladder(spec, 8192) == {"rung": 1, "R": 8192, "allowance": 16384, "cap": 32768,
                                      "L": 32768, "H": 16384}
    r2 = cal.ladder(spec, 10000)                             # 2R -> 20,480 > H; H >= 1.4R = 14,000
    assert r2["rung"] == 2 and r2["cap"] == 32768 and r2["allowance"] == 16384
    assert cal.ladder(spec, 12000)["rung"] == 3              # 1.4R = 16,800 > H
    assert cal.ladder(spec, None)["rung"] == 3               # R unknown


def test_ladder_for_claude_and_grok():
    assert cal.ladder(cal.ARMS["cb_opus"], 20000)["cap"] == 16384 + 40960
    assert cal.ladder(cal.ARMS["cb_grok"], 50000)["rung"] is None   # reasoning outside the cap


# ------------------------------------------------------------------- timeout
def test_timeout_rule():
    assert cal.derived_timeout([100.0], False, 780) == 300           # floor
    assert cal.derived_timeout([250.0], False, 780) == 360           # 350 -> 360
    assert cal.derived_timeout([600.0], False, 780) == 780           # capped at D
    assert cal.derived_timeout([100.0], True, 780) == 780            # a call reached the bound
    assert cal.call_bound(780) == 900 and cal.call_bound(800) == 920


# ---------------------------------------------------------------- send/omit
def test_send_omit_equivalent_and_not():
    same = [(1000 + i, 1000 + (i % 7)) for i in range(35)]
    assert cal.send_omit_test(same)["shown"] is True
    off = [(1500 + i, 1000) for i in range(35)]
    assert cal.send_omit_test(off)["shown"] is False
    few = cal.send_omit_test(same[:20])
    assert few["shown"] is False and "fewer than 30" in few["reason"]


# -------------------------------------------------------- the set and subset
def _write_tick(root: Path, cycle: str, models: list[str], image: bytes | None = None):
    month = root / f"{cycle[:4]}-{cycle[4:6]}"
    (month / "images").mkdir(parents=True, exist_ok=True)
    lines = []
    for mk in models:
        imgs = []
        if image is not None:
            sha = hashlib.sha256(image).hexdigest()
            (month / "images" / f"{sha}.png").write_bytes(image)
            imgs = [{"sha256": sha, "bytes": len(image)}]
        for stage in ("screen", "trade"):
            lines.append({"cycle_id": cycle, "model_key": mk, "stage": stage, "prompt_version": "v4",
                          "system": f"{stage}-sys", "user": f"{stage}-{mk}",
                          "images": imgs if stage == "trade" else []})
    (month / f"{cycle}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")


def test_set_and_subset(tmp_path):
    all5 = ["claude", "claude_opus", "gemini", "gpt", "grok", "deepseek"]
    _write_tick(tmp_path, "20261123T143512Z", all5, b"img")         # 09:35 ET, slot 1, k=0
    _write_tick(tmp_path, "20261123T150512Z", all5)                 # slot 2, k=1
    _write_tick(tmp_path, "20261123T153512Z", ["claude", "gpt", "grok"])   # slot 3, k=2: gemini absent
    _write_tick(tmp_path, "20261120T153512Z", all5)                 # outside the sessions
    inputs = cal.load_common_set(tmp_path, ["2026-11-23"])
    assert len(inputs) == 5 + 5 + 3                                 # DeepSeek is not in the set
    assert {x["position"] for x in inputs} == {1, 2, 3}
    assert cal.load_images(inputs[0]) == [b"img"]
    sub = cal.send_subset(inputs)
    assert [(x["tick_index"], x["model_key"]) for x in sub] == [
        (0, "claude"), (1, "claude_opus"), (2, "gpt")]              # k=2 -> gemini absent -> next


def test_a_corrupt_image_is_refused(tmp_path):
    _write_tick(tmp_path, "20261123T143512Z", ["claude"], b"img")
    x = cal.load_common_set(tmp_path, ["2026-11-23"])[0]
    p = next((tmp_path / "2026-11" / "images").iterdir())
    p.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        cal.load_images(x)


# --------------------------------------------------------------- the summary
def _rec(arm, iid, leg, setting, cls, pos=1, reasoning=100, total=300, lat=50.0, model=None):
    return {"arm": arm, "input_id": iid, "leg": leg, "setting": setting, "cls": cls,
            "position": pos, "reasoning_tokens": reasoning, "total_output": total,
            "latency_s": lat, "model_named": model or cal.ARMS[arm]["model"],
            "error_class": None if cls == "valid" else "X", "finish_reason": "stop"}


def test_summary_rules():
    arm = "cb_astra"
    recs = [_rec(arm, f"i{i}", "trade", "omit", "valid", pos=1 + i % 13, reasoning=100 + i,
                 lat=40.0 + i) for i in range(100)]
    recs += [_rec(arm, "i0", "trade", "send", "valid", reasoning=110, total=310)]
    recs += [_rec(arm, "x1", "trade", "omit", "invalid_finish"),
             _rec(arm, "x2", "screen", "omit", "provider_failure")]
    s = cal.summarize_arm(arm, recs, 780)
    assert s["valid_trading_calls"] == 100 and s["coverage_met"] is True
    assert s["R"] == 199 and s["ladder"]["rung"] == 1 and s["ladder"]["allowance"] == 4096
    assert s["timeout_s"] == max(300, math.ceil(139 * 1.4 / 30) * 30) == 300   # the floor
    assert s["counts_by_class"]["invalid_finish"] == 1 and len(s["invalid_finishes"]) == 1
    assert s["send_omit"]["n_pairs"] == 1 and s["send_omit"]["shown"] is False
    budget = recs + [_rec(arm, "b1", "trade", "omit", "budget_stop")]
    assert cal.summarize_arm(arm, budget, 780)["ladder"]["rung"] == 3    # R unknown


def test_a_mismatched_model_name_is_not_valid():
    arm = "cb_grok"
    s = cal.summarize_arm(arm, [_rec(arm, "i0", "trade", "omit", "valid", model="grok-4.6")], 780)
    assert s["valid_trading_calls"] == 0 and s["model_name_mismatches"] == 1


def test_every_arm_states_its_fields_before_its_runs():
    assert set(cal.ARMS) == {"cb_sonnet", "cb_opus", "cb_astra", "cb_grok", "cb_argon"}
    for spec in cal.ARMS.values():
        assert spec["fields"] and "total" in spec["fields"] or "as cb_sonnet" in spec["fields"]
