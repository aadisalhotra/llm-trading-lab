"""Legacy-SDK control arm for the 2026-08 Gemini SDK-migration probe.

Same prompts (the saved arm-A context replayed verbatim), same cap (16384),
but through the LEGACY google-generativeai client — i.e. arm D re-run today.
Purpose: if the google-genai migration probe shows a parse-failure rate above
the 2026-07-27 arm D baseline, this control separates "the new SDK changed
something" from "the unpinned gemini-3.1-pro-preview alias serves different
behavior today than it did on 2026-07-27". Identical failure rates across
both of today's arms = provider-side drift, migration exonerated.

Writes into side_experiments/gemini_genai_migration_probe/ (results_legacy_
control.jsonl) — deliberately NOT into the legacy probe's directory, whose
results.jsonl/summary.json are committed evidence for the 7/27 verdict and
must not be contaminated by later runs.
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
from src.analytics.cost_rates import compute_call_cost_usd  # noqa: E402
from src.config_loader import load_env                    # noqa: E402

from gemini_finish_reason_probe import (                  # noqa: E402
    OUT_DIR as LEGACY_OUT_DIR,
    PRODUCTION_MODEL,
    build_context,
    classify_parse_error,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("llmlab.probe.gemini_legacy_control")

OUT_DIR = ROOT / "side_experiments" / "gemini_genai_migration_probe"
ARM = "D-control"  # arm D's config, re-run at the migration boundary


def call_once(idx: int, ctx: dict[str, Any]) -> dict[str, Any]:
    import google.generativeai as genai

    model = genai.GenerativeModel(
        model_name=PRODUCTION_MODEL,
        system_instruction=ctx["system_prompt"],
        generation_config={
            "response_mime_type": "application/json",
            "max_output_tokens": 16384,
        },
    )
    if ctx["images"]:
        parts: list[Any] = [{"mime_type": "image/png", "data": b} for b in ctx["images"]]
        parts.append(ctx["user_prompt"])
    else:
        parts = ctx["user_prompt"]

    rec: dict[str, Any] = {
        "arm": ARM, "i": idx, "model": PRODUCTION_MODEL,
        "sdk": "google-generativeai",
        "ts": datetime.now(ZoneInfo("UTC")).isoformat(),
        "max_output_tokens": 16384,
    }
    start = time.perf_counter()
    try:
        response = model.generate_content(parts, request_options={"timeout": 180})
    except Exception as e:
        rec.update({"api_error": f"{type(e).__name__}: {e}",
                    "latency_s": round(time.perf_counter() - start, 1)})
        return rec
    rec["latency_s"] = round(time.perf_counter() - start, 1)

    cands = list(getattr(response, "candidates", None) or [])
    if cands:
        fr = getattr(cands[0], "finish_reason", None)
        rec["finish_reason"] = getattr(fr, "name", str(fr))
    rec["model_id_returned"] = getattr(response, "model_version", None) or PRODUCTION_MODEL

    usage = getattr(response, "usage_metadata", None)
    in_tok = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
    out_tok = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
    rec["input_tokens"], rec["output_tokens"] = in_tok, out_tok
    rec["thoughts_tokens"] = int(getattr(usage, "thoughts_token_count", 0) or 0) if usage else 0
    rec["cost_usd"] = compute_call_cost_usd(PRODUCTION_MODEL, in_tok, out_tok)

    try:
        text = response.text or ""
    except Exception as e:
        rec.update({"text_access_error": f"{type(e).__name__}: {e}", "raw_text": "",
                    "parse_ok": False, "parse_error": "Empty response from model",
                    "signature": "empty_response"})
        return rec

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--sleep", type=float, default=3.0)
    ap.add_argument("--budget-usd", type=float, default=5.0)
    args = ap.parse_args()

    load_env()
    import os
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        logger.error("GOOGLE_API_KEY not set (checked .env) — cannot run")
        return 1
    import google.generativeai as genai
    genai.configure(api_key=key)

    ctx_path = LEGACY_OUT_DIR / "context.json"
    if not ctx_path.exists():
        logger.error("No saved context at %s — control must replay the classified "
                     "prompts. Aborting.", ctx_path)
        return 1
    with open(ctx_path, encoding="utf-8") as f:
        saved = json.load(f)
    ctx = build_context(reuse=saved)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results_path = OUT_DIR / "results_legacy_control.jsonl"

    spent = 0.0
    fails = 0
    for i in range(1, args.n + 1):
        if spent >= args.budget_usd:
            logger.warning("Budget guard: $%.2f >= $%.2f cap — stopping", spent, args.budget_usd)
            break
        rec = call_once(i, ctx)
        spent += float(rec.get("cost_usd") or 0.0)
        if rec.get("parse_ok") is False:
            fails += 1
        with open(results_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info(
            "[%s %d/%d] finish=%s parse_ok=%s sig=%s out_tok=%s think_tok=%s lat=%ss ($%.2f cum)",
            ARM, i, args.n, rec.get("finish_reason"), rec.get("parse_ok"),
            rec.get("signature", "-"), rec.get("output_tokens"),
            rec.get("thoughts_tokens"), rec.get("latency_s"), spent,
        )
        if i < args.n:
            time.sleep(args.sleep)
    logger.info("Control done: %d/%d parse failures, $%.2f spent", fails, args.n, spent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
