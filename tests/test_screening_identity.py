"""Screening-leg identity (hub H-OPS-12; lifecycle_and_identity_ruling_2026-11-22
section 2.3): the model the screening response names and its fingerprint reach
the decision row beside the decision leg's. No provider is contacted.
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from src.adapters.base import BaseAdapter
from src.logging import decision_log


class _Fake(BaseAdapter):
    provider_name = "fake"

    def _call_api(self, system_prompt, user_prompt, images=None):
        return "SCREEN", "served-model", {"system_fingerprint": "fp_1", "finish_reason": "stop",
                                          "output_tokens": 7}


def test_generate_raw_returns_the_model_named():
    raw, _lat, md = _Fake("requested-model").generate_raw("S", "U")
    assert raw == "SCREEN" and md["model_returned"] == "served-model"
    assert md["system_fingerprint"] == "fp_1"


def test_the_row_carries_both_legs(tmp_path, monkeypatch):
    monkeypatch.setattr(decision_log, "TRADES_DIR", tmp_path)
    _raw, _lat, sm = _Fake("requested-model").generate_raw("S", "U")
    dr = SimpleNamespace(model_id_configured="m", model_id_returned="m", provider="p", success=True,
                         error=None, latency_seconds=1.0, raw_response="{}", overall_reasoning="",
                         decisions=[], metadata={"system_fingerprint": "fp_d", "finish_reason": "stop"})
    decision_log.log_decision_run(
        model_key="cb_grok", run_date=datetime(2026, 12, 1, 9, 35), decision_result=dr,
        accepted_decisions=[], violations=[], execution_results=[], portfolio_snapshot_after={},
        prompt_version="v4", data_inputs_hash="h", execution_mode="paper",
        inception_date="2026-12-01", screening_response="SCREEN", screening_metadata=sm)
    row = json.loads((tmp_path / "cb_grok_2026-12.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["screening_model_returned"] == "served-model"
    assert row["screening_system_fingerprint"] == "fp_1" and row["screening_finish_reason"] == "stop"
    assert row["model_id_returned"] == "m" and row["api_system_fingerprint"] == "fp_d"
