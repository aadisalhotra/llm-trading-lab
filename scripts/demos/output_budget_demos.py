"""Induced demonstrations for the output-budget equalization, off the
validation record (output_budget_equalization_ruling.md §2 condition 5; hub
F3, 2026-11-19).

  (a) For each of the three raised adapters (Anthropic, OpenAI, xAI), a
      response LONGER than 4096 output tokens returns with a natural finish
      and parses without repair.
  (c) Each provider accepts a non-streaming request at 16384. The (a) calls
      are themselves non-streaming requests at 16384; Claude Opus, which
      shares the Anthropic adapter under a different model id, gets one short
      call of its own, because Research flagged the Anthropic client as
      unverified.

Every call goes through the production adapter class (`get_adapter(...)
._call_api`) with the production model id from config/settings.json, so the
request is the live wire form apart from the prompt. Nothing is written to
data/: the only output is the JSON record named by --out. (b), the small-budget
truncation flags and alert, needs the section 3 logging commit and is not
run here.

Usage: python scripts/demos/output_budget_demos.py --out docs/ops/output_budget_2026-11-20/demos.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.adapters import get_adapter  # noqa: E402
from src.adapters.base import repair_json  # noqa: E402
from src.config_loader import load_env, load_settings  # noqa: E402

logger = logging.getLogger("demos.output_budget")

N_ITEMS = 90  # ~65-75 output tokens per item -> ~6,000-7,000 tokens, past 4096
NATURAL = {"end_turn", "stop"}  # Anthropic / OpenAI-compatible natural finishes

SYSTEM = ("You are a test fixture generator. Respond with one JSON object and "
          "nothing else: no prose, no code fences.")
LONG_USER = (
    f'Return a JSON object {{"items": [...], "done": true}} where "items" has '
    f"exactly {N_ITEMS} elements, numbered 1 to {N_ITEMS}. Each element is "
    '{"id": <int>, "ticker": <an invented four-letter code>, "rationale": '
    '<one sentence of 30 to 35 words about a fictional company>, "checks": '
    "[<three short distinct phrases>]}. Write every element in full; do not "
    'abbreviate, summarize or stop early. The final key must be "done": true. '
    "Output json only."
)
SHORT_USER = 'Return the json object {"ok": true} and nothing else.'

LONG_ARMS = ("claude", "gpt", "grok")      # one per raised adapter
SHORT_ARMS = ("claude_opus",)              # (c) for the second Anthropic model id


def run_call(model_key: str, cfg: dict[str, Any], user: str) -> dict[str, Any]:
    adapter = get_adapter(cfg["provider"], cfg["model"])
    row: dict[str, Any] = {"model_key": model_key, "provider": cfg["provider"],
                           "model_id": cfg["model"], "started_utc":
                           datetime.now(timezone.utc).isoformat()}
    t0 = time.perf_counter()
    try:
        text, returned_id, meta = adapter._call_api(SYSTEM, user, None)
    except Exception as e:  # noqa: BLE001 — a refusal is the (c) answer
        row.update(accepted=False, error=f"{type(e).__name__}: {e}"[:2000],
                   seconds=round(time.perf_counter() - t0, 1))
        return row
    row.update(accepted=True, seconds=round(time.perf_counter() - t0, 1),
               model_id_returned=returned_id,
               output_tokens=meta.get("output_tokens"),
               reasoning_tokens=meta.get("thoughts_tokens"),
               input_tokens=meta.get("input_tokens"),
               finish_reason=meta.get("finish_reason"),
               cost_usd=meta.get("cost_usd"),
               response_chars=len(text))
    # "Parses without repair": the raw text is valid JSON as returned, and the
    # production repair pass would not change it.
    try:
        parsed = json.loads(text)
        row["parses_as_returned"] = True
    except json.JSONDecodeError as e:
        parsed = None
        row["parses_as_returned"] = False
        row["parse_error"] = str(e)
    row["repair_json_changes_text"] = repair_json(text) != text
    if isinstance(parsed, dict) and "items" in parsed:
        row["n_items"] = len(parsed["items"])
        row["done"] = parsed.get("done")
    row["raw_text"] = text
    return row


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Output-budget demonstrations (a) and (c).")
    ap.add_argument("--out", required=True, help="where to write the JSON record")
    ap.add_argument("--arms", help="comma-separated subset of the arms to run "
                                   "(a rerun of one arm into its own --out file)")
    a = ap.parse_args()
    load_env()
    models = load_settings()["models"]
    only = set(a.arms.split(",")) if a.arms else None
    rows = []
    for key in LONG_ARMS:
        if only and key not in only:
            continue
        logger.info("(a) long call: %s", key)
        rows.append(dict(run_call(key, models[key], LONG_USER), demo="a+c"))
    for key in SHORT_ARMS:
        if only and key not in only:
            continue
        logger.info("(c) short call: %s", key)
        rows.append(dict(run_call(key, models[key], SHORT_USER), demo="c"))

    def a_pass(r):
        return (r["accepted"] and (r.get("output_tokens") or 0) > 4096
                and r.get("finish_reason") in NATURAL and r.get("parses_as_returned")
                and not r.get("repair_json_changes_text") and r.get("n_items") == N_ITEMS
                and r.get("done") is True)
    verdict = {
        "a": {r["model_key"]: a_pass(r) for r in rows if r["demo"] == "a+c"},
        "c": {r["model_key"]: r["accepted"] for r in rows},
    }
    out = {"run_utc": datetime.now(timezone.utc).isoformat(),
           "ruling": "docs/registrations/output_budget_equalization_ruling.md rev. 2, "
                     "sha256 05d53670b86597e88a1327739206b56e36e08218c19550e45ef056d00e489c40",
           "budget_under_test": 16384, "n_items_requested": N_ITEMS,
           "pass_rule_a": "accepted, output_tokens > 4096, natural finish (end_turn/stop), "
                          "json.loads succeeds on the raw text, repair_json leaves it "
                          "unchanged, all items present and done=true",
           "verdict": verdict, "calls": rows}
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    for r in rows:
        logger.info("%s accepted=%s out_tokens=%s finish=%s parses=%s repaired=%s items=%s %ss",
                    r["model_key"], r["accepted"], r.get("output_tokens"),
                    r.get("finish_reason"), r.get("parses_as_returned"),
                    r.get("repair_json_changes_text"), r.get("n_items"), r.get("seconds"))
    logger.info("verdict %s", verdict)
    ok = all(verdict["a"].values()) and all(verdict["c"].values())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
