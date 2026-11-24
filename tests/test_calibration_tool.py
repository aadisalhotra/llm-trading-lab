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
    assert cal.call_bound(840) == 960                                 # D = 840 (H-OPS-14)
    assert cal.configured_d(840) == 840.0
    with pytest.raises(SystemExit):
        cal.configured_d(None)


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


def test_call_cost_prefers_the_adapter_then_list_rates():
    assert cal.call_cost("grok-4.7", {"cost_usd": 0.0123}, 500, "2026-11-23") == {
        "cost_usd": 0.0123, "cost_source": "adapter"}
    # gemini-4-argon at $2/$10 through 2026-11-30, $4/$20 from 2026-12-01
    md = {"input_tokens": 10_000}
    assert cal.call_cost("gemini-4-argon", md, 2_000, "2026-11-23") == {
        "cost_usd": 0.04, "cost_source": "list_rate"}
    assert cal.call_cost("gemini-4-argon", md, 2_000, "2026-12-01")["cost_usd"] == 0.08
    assert cal.call_cost("gpt-6-astra", {}, None, "2026-11-23") == {
        "cost_usd": None, "cost_source": None}


def test_cost_totals_count_every_record_written():
    recs = [{"cost_usd": 0.5}, {"cost_usd": 0.25}, {"cost_usd": None}]
    t = cal.cost_totals(recs)
    assert t["cost_usd"] == 0.75 and t["records_priced"] == 2 and t["records_unpriced"] == 1


def test_a_fast_connection_failure_is_a_provider_failure_not_a_bound(monkeypatch):
    """urllib3's NewConnectionError subclasses ConnectTimeoutError, so an
    unreachable host reads as a client timeout at once: re-run it, never
    count it as reaching the bound (smoke test 2026-11-23, api.x.ai)."""
    import src.adapters as adapters

    class ConnectTimeoutError(Exception):
        pass

    class FakeAdapter:
        def _bounded_call(self, system, user, images):
            raise ConnectTimeoutError("Failed to establish a new connection: [WinError 10051] "
                                      "key sk-THISISNOTAREALKEY123 unreachable network")

    monkeypatch.setattr(adapters, "get_adapter", lambda provider, model: FakeAdapter())
    monkeypatch.setenv("XAI_API_KEY", "sk-THISISNOTAREALKEY123")
    x = {"input_id": "c:claude", "tick_index": 0, "position": 1,
         "trade": {"system": "s", "user": "u", "images": []}, "images_dir": "."}
    rec = cal.run_call("cb_grok", "trade", "omit", x, 960.0)
    assert rec["error_class"] == "TIMEOUT" and rec["cls"] == "provider_failure"
    assert "THISISNOTAREALKEY" not in rec["error"]
    assert ("c:claude", "trade", "omit") not in cal.done_keys_from([rec])


def _inp(k, mk="claude", screen=True):
    return {"input_id": f"c{k}:{mk}", "tick_index": k, "model_key": mk,
            "screen": {"system": "s"} if screen else None}


def test_pairs_run_back_to_back_send_first_when_k_is_even():
    inputs = [_inp(0), _inp(0, "gpt"), _inp(1), _inp(1, "gpt")]
    subset = [inputs[0], inputs[3]]
    order = [(x["input_id"], leg, s) for leg, s, x in cal.job_order(inputs, subset, set())]
    assert order == [("c0:claude", "screen", "omit"), ("c0:claude", "trade", "send"),
                     ("c0:claude", "trade", "omit"),
                     ("c0:gpt", "screen", "omit"), ("c0:gpt", "trade", "omit"),
                     ("c1:claude", "screen", "omit"), ("c1:claude", "trade", "omit"),
                     ("c1:gpt", "screen", "omit"), ("c1:gpt", "trade", "omit"),
                     ("c1:gpt", "trade", "send")]


def test_a_pair_half_run_earlier_is_completed_not_rerun():
    inputs = [_inp(0)]
    done = {("c0:claude", "screen", "omit"), ("c0:claude", "trade", "omit")}
    assert [(leg, s) for leg, s, _ in cal.job_order(inputs, inputs, done)] == [("trade", "send")]


def test_pair_intervals():
    recs = [{"input_id": "c2:gpt", "tick_index": 2, "leg": "trade", "setting": "send",
             "started_utc": "2026-11-24T01:00:00+00:00"},
            {"input_id": "c2:gpt", "tick_index": 2, "leg": "trade", "setting": "omit",
             "started_utc": "2026-11-24T01:00:30+00:00"},
            {"input_id": "c3:gpt", "tick_index": 3, "leg": "trade", "setting": "omit",
             "started_utc": "2026-11-24T01:01:00+00:00"}]
    assert cal.pair_intervals(recs) == [{"input_id": "c2:gpt", "k": 2, "first": "send",
                                         "interval_s": 30.0}]


def test_the_tool_commit_is_read_from_git():
    tc = cal.tool_commit()
    assert tc["commit"] and len(tc["commit"]) == 40 and tc["run_path_modified"] in (True, False)


def test_every_arm_states_its_fields_before_its_runs():
    assert set(cal.ARMS) == {"cb_sonnet", "cb_opus", "cb_astra", "cb_grok", "cb_argon"}
    for spec in cal.ARMS.values():
        assert spec["fields"] and "total" in spec["fields"] or "as cb_sonnet" in spec["fields"]
