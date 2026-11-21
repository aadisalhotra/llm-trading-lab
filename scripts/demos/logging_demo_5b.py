"""Demonstration 5(b) of output_budget_equalization_ruling.md rev. 2 §2:
a test call with a deliberately small budget ends on it, sets both §3 flags,
and raises the WARN. Off the validation record (hub S1, 2026-11-21).

One real call to Claude Sonnet through the PRODUCTION AnthropicAdapter and its
generate_decision (the production retry and parse path). The small budget is
imposed in this process only, by wrapping the Anthropic SDK's Messages.create
to override max_tokens; the adapter's own constant (16384) is not edited. The
row is written by the production log_decision_run into a temporary directory,
and the WARN goes through the production alert_if_flagged into a capture list,
not to email. Nothing is written under data/.

Usage: python scripts/demos/logging_demo_5b.py --out docs/ops/logging_2026-11-21/demo_5b.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402

from src.adapters import get_adapter  # noqa: E402
from src.config_loader import load_env, load_settings  # noqa: E402
from src.logging import decision_log  # noqa: E402
from src.logging.call_inputs import CallInput, alert_if_flagged, retain_call_inputs  # noqa: E402

SMALL_BUDGET = 300
SYSTEM = ('You are a portfolio manager. Respond with one JSON object: {"decisions": '
          '[{"ticker", "action", "target_weight", "confidence", "reasoning"}...], '
          '"overall_reasoning": "..."}. No prose, no code fences.')
USER = ("Give a decision for each of these 25 tickers, in this order, each with a "
        "reasoning of 40 words: AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO JPM V MA UNH "
        "LLY XOM JNJ PG HD COST ABBV MRK PEP KO WMT BAC CVX. Output json only.")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Demonstration 5(b): small budget -> flags + WARN.")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    load_env()
    cfg = load_settings()["models"]["claude"]

    real_create = anthropic.resources.messages.Messages.create
    seen: dict = {}

    def small_budget_create(self, *args, **kw):
        seen["production_max_tokens"] = kw.get("max_tokens")
        kw["max_tokens"] = SMALL_BUDGET
        return real_create(self, *args, **kw)
    anthropic.resources.messages.Messages.create = small_budget_create
    try:
        adapter = get_adapter(cfg["provider"], cfg["model"])
        result = adapter.generate_decision(SYSTEM, USER, images=None)
    finally:
        anthropic.resources.messages.Messages.create = real_create

    alerts: list = []
    cycle = "DEMO5B-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fired = alert_if_flagged("claude", cycle, result, lambda *x, **k: alerts.append(
        {"severity": x[0], "title": x[1], "context": x[3] if len(x) > 3 else None,
         "kind": k.get("kind")}))
    with tempfile.TemporaryDirectory() as tmp:
        fields = retain_call_inputs(cycle, "claude", datetime.now(timezone.utc), "demo",
                                    [CallInput("trade", SYSTEM, USER)], Path(tmp) / "prompts")
        decision_log.TRADES_DIR = Path(tmp)
        decision_log.log_decision_run(
            model_key="claude", run_date=datetime.now(timezone.utc), decision_result=result,
            accepted_decisions=[], violations=[], execution_results=[],
            portfolio_snapshot_after={}, prompt_version="demo", data_inputs_hash="demo",
            execution_mode="demo", inception_date="2026-04-08", call_inputs=fields)
        row = json.loads(next(Path(tmp).glob("claude_*.jsonl")).read_text(
            encoding="utf-8").splitlines()[-1])
    md = result.metadata or {}
    out = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": cfg["model"], "small_budget": SMALL_BUDGET,
        "production_max_tokens_seen_by_the_wrapper": seen.get("production_max_tokens"),
        "api_success": result.success, "finish_reason": md.get("finish_reason"),
        "output_tokens": md.get("output_tokens"), "attempt": md.get("attempt"),
        "n_decisions_parsed": len(result.decisions),
        "row": {k: row.get(k) for k in ("api_success", "api_finish_reason", "decision_truncated",
                                        "json_repaired", "input_retention_failed")},
        "raw_response_chars_on_row": len(row.get("raw_response") or ""),
        "raw_response_tail": (result.raw_response or "")[-160:],
        "warn_fired": fired, "alerts": alerts,
        "pass": bool(result.success and row.get("decision_truncated") and row.get("json_repaired")
                     and fired and alerts and alerts[0]["severity"] == "WARN"),
    }
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: out[k] for k in ("api_success", "finish_reason", "output_tokens",
                                          "n_decisions_parsed", "row", "warn_fired", "pass",
                                          "production_max_tokens_seen_by_the_wrapper")}, indent=1))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
