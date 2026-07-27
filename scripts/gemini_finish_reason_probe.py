"""Gemini finish_reason diagnostic probe — Operations item 4a (2026-07-27).

Standalone, read-only diagnostic for the Gemini completeness failure
(May 0.57 / June 0.79 / July 0.51). Replays a REAL production decision
prompt (assembled through the same code path as the live pipeline:
portfolio snapshot -> screening call -> memory reads -> build_prompts,
including chart images) against the Gemini API and records what the
pipeline's logging currently throws away: candidates[0].finish_reason,
the full raw response text, safety ratings, and the served model_version.

Three arms:
  A  production replica  — gemini-3.1-pro-preview, JSON mime mode, NO
     response_schema, max_output_tokens=4096 (exactly gemini_adapter.py).
     Runs until >= --failure-target parse failures or --max-calls calls.
  B  response_schema     — identical, plus a typed response_schema for the
     v3 decision JSON. Fixed --n-fixed calls.
  C  non-preview model   — identical to A but on the closest non-preview
     Gemini model that supports generateContent (discovered via
     list_models). Fixed --n-fixed calls. Skipped (and recorded as a
     finding) if no non-preview gemini-3* model exists.

Never mutates lab state: no portfolio save, no decision/intraday logging.
(fetch_news MAY refresh the tracked news cache exactly as a live tick
would; restore data/news_cache/ from git afterwards if cleanliness of the
working tree matters.)

Results: side_experiments/gemini_finish_reason_probe/
  context.json   — the exact prompts + assembly metadata (one per run)
  results.jsonl  — one record per API call, full raw text included
  summary.json   — per-arm aggregates: finish_reason x parse-outcome
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.adapters import get_adapter                      # noqa: E402
from src.adapters.base import BaseAdapter                 # noqa: E402
from src.analytics.cost_rates import compute_call_cost_usd  # noqa: E402
from src.config_loader import load_env, load_settings, universe_symbols  # noqa: E402
from src.data.market_data import fetch_universe_data      # noqa: E402
from src.data.news import fetch_news                      # noqa: E402
from src.data.sentiment import compute_sentiment_dict     # noqa: E402
from src.logging.memory import (                          # noqa: E402
    read_last_action_per_ticker,
    read_recent_decisions,
)
from src.pipeline import _prices_from_data                # noqa: E402
from src.portfolio.portfolio import load_portfolio        # noqa: E402
from src.prompt_builder import (                          # noqa: E402
    build_prompts,
    build_screening_prompt,
    parse_screening_response,
    uses_per_ticker_last_action,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("llmlab.probe.gemini_finish_reason")

OUT_DIR = ROOT / "side_experiments" / "gemini_finish_reason_probe"
MODEL_KEY = "gemini"
PRODUCTION_MODEL = "gemini-3.1-pro-preview"
PRODUCTION_GENCFG: dict[str, Any] = {
    # Mirrors src/adapters/gemini_adapter.py exactly (no temperature in
    # production; the provider default is used).
    "response_mime_type": "application/json",
    "max_output_tokens": 4096,
}

# Typed schema for the v3 decision JSON (arm B). Field set mirrors
# BaseAdapter._parse_response expectations; permissive on optionals so the
# schema constrains SHAPE, not content.
DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD", "SHORT", "COVER"]},
                    "ticker": {"type": "string"},
                    "target_weight": {"type": "number"},
                    "confidence": {"type": "integer"},
                    "summary": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "confidence_justification": {"type": "string"},
                    "why_now": {"type": "string"},
                    "reversal_justification": {"type": "string", "nullable": True},
                },
                "required": ["action", "ticker", "target_weight", "confidence"],
            },
        },
        "overall_reasoning": {"type": "string"},
        "cash_rationale": {"type": "string", "nullable": True},
        "position_reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "thesis_status": {"type": "string"},
                    "implication": {"type": "string"},
                },
            },
        },
        "no_trade_reason": {"type": "string", "nullable": True},
    },
    "required": ["decisions", "overall_reasoning"],
}

# Parse-error signature buckets — the same taxonomy used in the July log
# analysis so probe results are directly comparable to production history.
_SIGNATURES = [
    ("unterminated_string", "Unterminated string"),
    ("expecting_delimiter", "Expecting ',' delimiter"),
    ("expecting_value", "Expecting value"),
    ("extra_data", "Extra data"),
    ("expecting_property_name", "Expecting property name"),
    ("missing_decisions", "missing required 'decisions'"),
    ("empty_response", "Empty response"),
]


def classify_parse_error(err: str) -> str:
    for bucket, needle in _SIGNATURES:
        if needle in err:
            return bucket
    return "other_parse"


def build_context(reuse: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assemble one real decision prompt exactly as run_one_model does,
    minus every state mutation. Returns prompts + images + metadata.

    `reuse`: a previously saved context.json payload. When given, the
    screening call is skipped (its saved shortlist is used for the image/
    prompt assembly) and the saved system/user prompts REPLACE the freshly
    built ones — so a later arm replays the exact prompt text an earlier
    arm classified, with only the chart image regenerated from the same
    (post-close, unchanged) daily data."""
    settings = load_settings()
    run_date = datetime.now(ZoneInfo("America/New_York"))

    logger.info("Fetching market data for the universe ...")
    market_data = fetch_universe_data()
    prices = _prices_from_data(market_data)

    news_data = None
    sentiment_data = None
    try:
        if settings.get("news", {}).get("enabled", False):
            news_data = fetch_news(symbols=universe_symbols(), settings=settings)
            if news_data:
                sentiment_data = compute_sentiment_dict(news_data)
    except Exception as e:  # news is contextual, not structural — degrade like the pipeline would
        logger.warning("News fetch failed (%s) — continuing without news context", e)

    portfolio = load_portfolio(MODEL_KEY)
    snapshot = portfolio.snapshot(prices)
    adapter = get_adapter("google", PRODUCTION_MODEL)

    # Step 1 screening — one real call through the production adapter, so the
    # shortlist is genuinely model-chosen like every live tick.
    screening_note = "ok"
    if reuse is not None:
        screening_note = "reused prior run's shortlist (no fresh screening call)"
        shortlisted = list(reuse.get("shortlist") or universe_symbols()[:20])
    else:
        try:
            scr_sys, scr_user = build_screening_prompt(
                market_data, snapshot, run_date,
                news_data=news_data, sentiment_data=sentiment_data,
            )
            scr_raw, _lat, _meta = adapter.generate_raw(scr_sys, scr_user)
            held = [h["ticker"] for h in snapshot.get("holdings", [])]
            shortlisted = parse_screening_response(scr_raw, held, universe_symbols())
        except Exception as e:
            screening_note = f"failed ({e}); fell back to universe[:20] like the pipeline"
            logger.warning("Screening failed: %s — falling back to universe[:20]", e)
            shortlisted = universe_symbols()[:20]

    recent_decisions: list[dict[str, Any]] = []
    try:
        recent_decisions = read_recent_decisions(MODEL_KEY, limit=10, now=run_date)
    except Exception as e:
        logger.warning("recent-decisions read failed: %s", e)

    last_actions: dict[str, Any] = {}
    if uses_per_ticker_last_action(settings.get("prompt_version", "")):
        try:
            last_actions = read_last_action_per_ticker(MODEL_KEY, shortlisted, now=run_date)
        except Exception as e:
            logger.warning("per-ticker last-action read failed: %s", e)

    system_prompt, user_prompt, prompt_version, images = build_prompts(
        snapshot,
        market_data,
        run_date,
        trades_executed_today=portfolio.intraday.trades_executed_today,
        runs_today=portfolio.intraday.runs_today,
        is_eod=False,
        include_chart_image=True,
        news_data=news_data,
        sentiment_data=sentiment_data,
        shortlisted_symbols=shortlisted,
        recent_decisions=recent_decisions,
        last_actions=last_actions,
    )

    if reuse is not None:
        # Replay the classified prompts verbatim; only the chart image was
        # regenerated (from the same closed daily data).
        system_prompt = reuse["system_prompt"]
        user_prompt = reuse["user_prompt"]
        logger.info("Reusing saved system/user prompts from prior run verbatim")

    ctx = {
        "built_at": run_date.isoformat(),
        "model": PRODUCTION_MODEL,
        "prompt_version": prompt_version,
        "shortlist": shortlisted,
        "screening": screening_note,
        "news_present": news_data is not None,
        "n_images": len(images or []),
        "image_bytes": [len(b) for b in (images or [])],
        "system_prompt_chars": len(system_prompt),
        "user_prompt_chars": len(user_prompt),
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "context.json", "w", encoding="utf-8") as f:
        json.dump(ctx, f, indent=2, ensure_ascii=False)
    logger.info(
        "Context built: prompt_version=%s shortlist=%d images=%d sys=%d chars user=%d chars",
        prompt_version, len(shortlisted), ctx["n_images"],
        ctx["system_prompt_chars"], ctx["user_prompt_chars"],
    )
    return {**ctx, "images": images or []}


def call_once(
    arm: str,
    idx: int,
    model_name: str,
    ctx: dict[str, Any],
    schema: dict[str, Any] | None,
    max_output_tokens: int | None = None,
) -> dict[str, Any]:
    """One API call, production-shaped, with full forensic capture."""
    import google.generativeai as genai

    gencfg = dict(PRODUCTION_GENCFG)
    if schema is not None:
        gencfg["response_schema"] = schema
    if max_output_tokens is not None:
        # Arm D — the raised-cap validation: everything identical to arm A
        # except the generation budget.
        gencfg["max_output_tokens"] = max_output_tokens
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=ctx["system_prompt"],
        generation_config=gencfg,
    )
    # Mirror gemini_adapter.py content assembly: image parts first, then text.
    if ctx["images"]:
        parts: list[Any] = [{"mime_type": "image/png", "data": b} for b in ctx["images"]]
        parts.append(ctx["user_prompt"])
    else:
        parts = ctx["user_prompt"]

    rec: dict[str, Any] = {
        "arm": arm, "i": idx, "model": model_name,
        "ts": datetime.now(ZoneInfo("UTC")).isoformat(),
        "schema": schema is not None,
        "max_output_tokens": gencfg["max_output_tokens"],
    }
    start = time.perf_counter()
    try:
        response = model.generate_content(parts, request_options={"timeout": 180})
    except Exception as e:
        rec.update({"api_error": f"{type(e).__name__}: {e}", "latency_s": round(time.perf_counter() - start, 1)})
        return rec
    rec["latency_s"] = round(time.perf_counter() - start, 1)

    # --- forensic capture (everything production discards) ---
    cands = list(getattr(response, "candidates", None) or [])
    rec["n_candidates"] = len(cands)
    if cands:
        fr = getattr(cands[0], "finish_reason", None)
        rec["finish_reason"] = getattr(fr, "name", str(fr))
        ratings = getattr(cands[0], "safety_ratings", None) or []
        flagged = [
            f"{getattr(r.category, 'name', r.category)}={getattr(r.probability, 'name', r.probability)}"
            for r in ratings
            if getattr(getattr(r, "probability", None), "name", "") not in ("", "NEGLIGIBLE")
        ]
        if flagged:
            rec["safety_flags"] = flagged
    pf = getattr(response, "prompt_feedback", None)
    block = getattr(pf, "block_reason", None) if pf else None
    if block:
        rec["block_reason"] = getattr(block, "name", str(block))
    rec["model_version"] = getattr(response, "model_version", None)

    usage = getattr(response, "usage_metadata", None)
    in_tok = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
    out_tok = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
    rec["input_tokens"], rec["output_tokens"] = in_tok, out_tok
    # Reasoning models bill hidden thought tokens against max_output_tokens;
    # capturing them is the whole point of the MAX_TOKENS hypothesis test.
    if usage:
        rec["thoughts_tokens"] = int(getattr(usage, "thoughts_token_count", 0) or 0)
        rec["total_tokens"] = int(getattr(usage, "total_token_count", 0) or 0)
    rec["cost_usd"] = compute_call_cost_usd(PRODUCTION_MODEL, in_tok, out_tok)

    try:
        text = response.text or ""
    except Exception as e:
        # .text raises when the response has no usable part (pure soft-stop).
        # Production's getattr would propagate this as an API error; record
        # it distinctly here because it IS the interesting case.
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


def pick_non_preview_model() -> str | None:
    """Closest non-preview gemini-3* generateContent model, or None."""
    import google.generativeai as genai

    candidates: list[str] = []
    for m in genai.list_models():
        name = m.name.removeprefix("models/")
        if "generateContent" not in (m.supported_generation_methods or []):
            continue
        if not name.startswith("gemini-3"):
            continue
        if re.search(r"preview|exp", name):
            continue
        candidates.append(name)
    if not candidates:
        return None
    # Prefer pro-tier, then the highest version string, then shortest name
    # (base alias over dated variants).
    pro = [c for c in candidates if "pro" in c] or candidates
    pro.sort(key=lambda n: (n.split("-")[1], -len(n)), reverse=True)
    return pro[0]


def run_arm(
    arm: str,
    ctx: dict[str, Any],
    results_path: Path,
    max_calls: int,
    failure_target: int,
    sleep_s: float,
    budget_usd: float,
    spent: list[float],
    max_output_tokens: int | None = None,
) -> list[dict[str, Any]]:
    schema = DECISION_SCHEMA if arm == "B" else None
    model_name = PRODUCTION_MODEL
    if arm == "C":
        found = pick_non_preview_model()
        if not found:
            logger.warning("Arm C: NO non-preview gemini-3* model exists — recording and skipping")
            rec = {"arm": "C", "finding": "no non-preview gemini-3* generateContent model available"}
            with open(results_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return [rec]
        model_name = found
        logger.info("Arm C model: %s", model_name)

    out: list[dict[str, Any]] = []
    failures = 0
    for i in range(1, max_calls + 1):
        if spent[0] >= budget_usd:
            logger.warning("Budget guard: $%.2f spent >= $%.2f cap — stopping arm %s", spent[0], budget_usd, arm)
            break
        rec = call_once(arm, i, model_name, ctx, schema,
                        max_output_tokens=max_output_tokens if arm == "D" else None)
        out.append(rec)
        spent[0] += float(rec.get("cost_usd") or 0.0)
        if rec.get("parse_ok") is False:
            failures += 1
        with open(results_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info(
            "[%s %d/%d] finish=%s parse_ok=%s sig=%s out_tok=%s think_tok=%s chars=%s lat=%ss ($%.2f cum)",
            arm, i, max_calls, rec.get("finish_reason"), rec.get("parse_ok"),
            rec.get("signature", "-"), rec.get("output_tokens"), rec.get("thoughts_tokens"),
            rec.get("raw_chars"), rec.get("latency_s"), spent[0],
        )
        if arm == "A" and failures >= failure_target:
            logger.info("Arm A: reached failure target (%d) after %d calls", failures, i)
            break
        time.sleep(sleep_s)
    return out


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for arm in ("A", "B", "C", "D"):
        rows = [r for r in records if r.get("arm") == arm and "i" in r]
        if not rows:
            continue
        fails = [r for r in rows if r.get("parse_ok") is False]
        fr_fail: dict[str, int] = {}
        fr_ok: dict[str, int] = {}
        sig: dict[str, int] = {}
        for r in rows:
            key = r.get("finish_reason", "API_ERROR" if r.get("api_error") else "?")
            (fr_fail if r.get("parse_ok") is False else fr_ok)[key] = \
                (fr_fail if r.get("parse_ok") is False else fr_ok).get(key, 0) + 1
        for r in fails:
            s = r.get("signature", "?")
            sig[s] = sig.get(s, 0) + 1
        out_ok = [r["output_tokens"] for r in rows if r.get("parse_ok") and r.get("output_tokens")]
        out_fail = [r["output_tokens"] for r in fails if r.get("output_tokens")]
        summary[arm] = {
            "model": rows[0].get("model"),
            "calls": len(rows),
            "api_errors": sum(1 for r in rows if r.get("api_error")),
            "parse_failures": len(fails),
            "failure_rate": round(len(fails) / len(rows), 3) if rows else None,
            "finish_reason_on_failure": fr_fail,
            "finish_reason_on_success": fr_ok,
            "failure_signatures": sig,
            "mean_output_tokens_success": round(sum(out_ok) / len(out_ok)) if out_ok else None,
            "mean_output_tokens_failure": round(sum(out_fail) / len(out_fail)) if out_fail else None,
            "total_cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 2),
            "model_versions_seen": sorted({r.get("model_version") for r in rows if r.get("model_version")}),
        }
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", choices=["A", "B", "C", "D", "all"], default="all")
    ap.add_argument("--max-calls", type=int, default=60, help="Arm A call ceiling")
    ap.add_argument("--failure-target", type=int, default=22, help="Arm A stops at this many parse failures")
    ap.add_argument("--n-fixed", type=int, default=15, help="Calls for arms B, C and D")
    ap.add_argument("--sleep", type=float, default=3.0)
    ap.add_argument("--budget-usd", type=float, default=8.0, help="Hard probe spend ceiling (est., list rates)")
    ap.add_argument("--max-output-tokens", type=int, default=16384,
                    help="Arm D generation budget (arm A's production value is 4096)")
    ap.add_argument("--reuse-context", action="store_true",
                    help="Replay the saved context.json prompts verbatim instead of rebuilding "
                         "(no fresh screening call; chart image regenerated from the same data)")
    ap.add_argument("--smoke", action="store_true", help="Build context + exactly one arm-A call, then exit")
    args = ap.parse_args()

    load_env()
    import google.generativeai as genai
    import os
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        logger.error("GOOGLE_API_KEY not set (checked .env) — cannot run")
        return 1
    genai.configure(api_key=key)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results_path = OUT_DIR / "results.jsonl"

    reuse = None
    if args.reuse_context:
        ctx_path = OUT_DIR / "context.json"
        if ctx_path.exists():
            with open(ctx_path, encoding="utf-8") as f:
                reuse = json.load(f)
        else:
            logger.warning("--reuse-context: no saved context.json — building fresh")
    ctx = build_context(reuse=reuse)

    spent = [0.0]
    records: list[dict[str, Any]] = []
    if args.smoke:
        rec = call_once("A", 0, PRODUCTION_MODEL, ctx, None)
        with open(results_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        pretty = {k: v for k, v in rec.items() if k != "raw_text"}
        logger.info("SMOKE result: %s", json.dumps(pretty, ensure_ascii=False))
        return 0

    arms = ["A", "B", "C"] if args.arm == "all" else [args.arm]
    for arm in arms:
        n = args.max_calls if arm == "A" else args.n_fixed
        records += run_arm(arm, ctx, results_path, n, args.failure_target,
                           args.sleep, args.budget_usd, spent,
                           max_output_tokens=args.max_output_tokens)

    # Summarize EVERYTHING in results.jsonl (including earlier partial runs)
    all_records: list[dict[str, Any]] = []
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            try:
                all_records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    summary = summarize(all_records)
    with open(OUT_DIR / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("SUMMARY:\n%s", json.dumps(summary, indent=2, ensure_ascii=False))
    logger.info("Total probe spend (est. list rates): $%.2f", spent[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
