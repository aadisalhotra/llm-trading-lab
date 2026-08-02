"""Gemini SDK-migration probe — pre-cutover validation (Operations 2026-08-02).

Arm-A replica for the google-generativeai -> google-genai migration: replays
the SAME production decision prompts the 2026-07-27 finish_reason probe
classified (side_experiments/gemini_finish_reason_probe/context.json, the
prompts arm D validated at 16384) through the MIGRATED production adapter —
src/adapters/gemini_adapter.py's real _call_api, so the request config, the
response accessors, and the (4)b forensics capture are all exercised
end-to-end on the new SDK. Nothing is mocked and nothing is bypassed.

Pass criteria (Research integration-path ruling):
  * finish_reason=STOP on every non-error call (zero MAX_TOKENS)
  * valid JSON decisions (BaseAdapter._parse_response succeeds)
  * visible output-token profile comparable to the post-fix baseline
    (arm D mean 660 on these same prompts; in-situ 7/28-7/30 39/39 STOP)
  * model_version echo present (identity telemetry intact)
  * thoughts_tokens now populated (google-genai surfaces the field the
    legacy SDK reported as 0/absent) — captured, reported, expected nonzero

Never mutates lab state: no portfolio save, no decision/intraday logging.
(The context rebuild MAY refresh the tracked news cache exactly as a live
tick would; restore data/news_cache/ from git afterwards if cleanliness of
the working tree matters.) The chart image is regenerated from current
closed daily data — prompt TEXT is verbatim; the image pixels necessarily
differ from July's, exactly as arm D's own regeneration did.

Results: side_experiments/gemini_genai_migration_probe/
  results.jsonl  — one record per call, full raw text included
  summary.json   — aggregate + side-by-side vs the arm D baseline
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src.adapters.base import BaseAdapter                 # noqa: E402
from src.adapters.gemini_adapter import GeminiAdapter     # noqa: E402
from src.config_loader import load_env                    # noqa: E402

# Reuse the original probe's context assembly and failure taxonomy so the
# records are directly comparable — same prompts, same signature buckets.
from gemini_finish_reason_probe import (                  # noqa: E402
    OUT_DIR as LEGACY_OUT_DIR,
    PRODUCTION_MODEL,
    build_context,
    classify_parse_error,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("llmlab.probe.gemini_genai_migration")

OUT_DIR = ROOT / "side_experiments" / "gemini_genai_migration_probe"
ARM = "E-genai"  # follows the legacy probe's arm lettering (A..D are taken)


def call_once(idx: int, adapter: GeminiAdapter, ctx: dict[str, Any]) -> dict[str, Any]:
    """One call through the migrated production adapter, forensics recorded."""
    rec: dict[str, Any] = {
        "arm": ARM, "i": idx, "model": PRODUCTION_MODEL,
        "sdk": "google-genai",
        "ts": datetime.now(ZoneInfo("UTC")).isoformat(),
        "max_output_tokens": 16384,
    }
    start = time.perf_counter()
    try:
        text, returned_id, meta = adapter._call_api(
            ctx["system_prompt"], ctx["user_prompt"], ctx["images"] or None,
        )
    except Exception as e:
        rec.update({"api_error": f"{type(e).__name__}: {e}",
                    "latency_s": round(time.perf_counter() - start, 1)})
        return rec
    rec["latency_s"] = round(time.perf_counter() - start, 1)

    # Everything below came through the adapter's own metadata dict — this IS
    # the (4)b telemetry-survival check, live.
    rec["finish_reason"] = meta.get("finish_reason")
    rec["input_tokens"] = meta.get("input_tokens")
    rec["output_tokens"] = meta.get("output_tokens")
    rec["thoughts_tokens"] = meta.get("thoughts_tokens", 0)
    rec["cost_usd"] = meta.get("cost_usd")
    # Verbatim echo. Google echoes the configured alias unchanged (both the
    # legacy probe and settings note this), so alias==returned_id is the
    # continuity-positive case; the ambiguous case (field absent -> adapter
    # falls back to the alias) is settled separately by
    # check_model_version_field().
    rec["model_id_returned"] = returned_id
    rec["raw_text"] = text
    rec["raw_chars"] = len(text)

    try:
        parsed = BaseAdapter._parse_response(text)
        rec["parse_ok"] = True
        rec["n_decisions"] = len(parsed.get("decisions", []))
    except ValueError as e:
        rec["parse_ok"] = False
        rec["parse_error"] = str(e)
        rec["signature"] = classify_parse_error(str(e))
    return rec


def check_model_version_field() -> dict[str, Any]:
    """One raw google-genai call to settle whether the response carries
    modelVersion at all under the new SDK (the adapter's alias fallback makes
    'field absent' and 'alias echoed' indistinguishable downstream). The
    identity-stability gate reads model_id_returned, so this must be a real
    echo, not our fallback."""
    import os

    from google import genai

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    resp = client.models.generate_content(model=PRODUCTION_MODEL, contents="ping")
    return {
        "model_version_field_present": resp.model_version is not None,
        "model_version_verbatim": resp.model_version,
    }


def load_arm_d_baseline() -> dict[str, Any] | None:
    """Arm D aggregates from the legacy probe's summary.json, if present."""
    path = LEGACY_OUT_DIR / "summary.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return (json.load(f) or {}).get("D")


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    calls = [r for r in rows if "i" in r]
    fails = [r for r in calls if r.get("parse_ok") is False]
    fr: dict[str, int] = {}
    for r in calls:
        key = r.get("finish_reason") or ("API_ERROR" if r.get("api_error") else "?")
        fr[key] = fr.get(key, 0) + 1
    out_ok = [r["output_tokens"] for r in calls if r.get("parse_ok") and r.get("output_tokens")]
    thoughts = [r["thoughts_tokens"] for r in calls if r.get("thoughts_tokens")]
    return {
        "arm": ARM,
        "sdk": "google-genai",
        "model": PRODUCTION_MODEL,
        "max_output_tokens": 16384,
        "calls": len(calls),
        "api_errors": sum(1 for r in calls if r.get("api_error")),
        "parse_failures": len(fails),
        "failure_rate": round(len(fails) / len(calls), 3) if calls else None,
        "finish_reason_distribution": fr,
        "failure_signatures": {
            s: sum(1 for r in fails if r.get("signature") == s)
            for s in {r.get("signature") for r in fails if r.get("signature")}
        },
        "mean_output_tokens_success": round(sum(out_ok) / len(out_ok)) if out_ok else None,
        "mean_thoughts_tokens": round(sum(thoughts) / len(thoughts)) if thoughts else None,
        "thoughts_tokens_populated": bool(thoughts),
        "model_ids_returned": sorted({r.get("model_id_returned") for r in calls if r.get("model_id_returned")}),
        "total_cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in calls), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=15, help="calls (mirrors arm D's 15)")
    ap.add_argument("--sleep", type=float, default=3.0)
    ap.add_argument("--budget-usd", type=float, default=8.0, help="hard spend ceiling (est., list rates)")
    ap.add_argument("--smoke", action="store_true", help="one call, then exit")
    args = ap.parse_args()

    load_env()
    import os
    if not os.getenv("GOOGLE_API_KEY"):
        logger.error("GOOGLE_API_KEY not set (checked .env) — cannot run")
        return 1

    ctx_path = LEGACY_OUT_DIR / "context.json"
    if not ctx_path.exists():
        logger.error("No saved context at %s — the migration probe must replay the "
                     "classified prompts, not fresh ones. Aborting.", ctx_path)
        return 1
    with open(ctx_path, encoding="utf-8") as f:
        saved = json.load(f)
    ctx = build_context(reuse=saved)
    logger.info("Replaying verbatim prompts: sys=%d chars user=%d chars images=%d",
                len(ctx["system_prompt"]), len(ctx["user_prompt"]), len(ctx["images"]))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results_path = OUT_DIR / "results.jsonl"

    adapter = GeminiAdapter(PRODUCTION_MODEL)  # production temperature=None path
    n = 1 if args.smoke else args.n
    spent = 0.0
    rows: list[dict[str, Any]] = []
    for i in range(1, n + 1):
        if spent >= args.budget_usd:
            logger.warning("Budget guard: $%.2f >= $%.2f cap — stopping", spent, args.budget_usd)
            break
        rec = call_once(i, adapter, ctx)
        rows.append(rec)
        spent += float(rec.get("cost_usd") or 0.0)
        with open(results_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info(
            "[%s %d/%d] finish=%s parse_ok=%s out_tok=%s think_tok=%s ver=%s lat=%ss ($%.2f cum)",
            ARM, i, n, rec.get("finish_reason"), rec.get("parse_ok"),
            rec.get("output_tokens"), rec.get("thoughts_tokens"),
            rec.get("model_version"), rec.get("latency_s"), spent,
        )
        if i < n:
            time.sleep(args.sleep)

    logger.info("Checking modelVersion field presence on the raw response ...")
    try:
        mv_check = check_model_version_field()
    except Exception as e:
        mv_check = {"error": f"{type(e).__name__}: {e}"}
    logger.info("modelVersion check: %s", mv_check)

    summary = {
        "generated": datetime.now(ZoneInfo("UTC")).isoformat(),
        "model_version_field_check": mv_check,
        "this_run": summarize(rows),
        "baseline_arm_d_legacy_sdk": load_arm_d_baseline(),
        "baseline_in_situ": {
            "window": "2026-07-28..2026-07-30 (legacy SDK, cap 16384, live)",
            "finish_reason": {"STOP": 39, "MAX_TOKENS": 0},
            "source": "scripts/phase_a_integrity_ledger.json -> gemini_budget_equalization.in_situ_verification",
        },
    }
    with open(OUT_DIR / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("SUMMARY:\n%s", json.dumps(summary["this_run"], indent=2, ensure_ascii=False))
    logger.info("Total probe spend (est. list rates): $%.2f", spent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
