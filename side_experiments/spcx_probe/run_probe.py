#!/usr/bin/env python3
"""SPCX post-cutoff-asset probe — NON-EXPERIMENTAL standalone side experiment.

Probes how each of the six experiment models reacts to the SpaceX IPO (SPCX,
Nasdaq, debuted 2026-06-12) — an asset that postdates every model's knowledge
cutoff and is NOT in the trading-lab universe. Hypothetical decision + rationale
only. This is content/curiosity plus a free pilot of the post-cutoff-asset
question for the September Phase B decontaminated-IPO provision.

ISOLATION (hard guarantees):
  * Standalone script. It imports ONLY the shared API-client config read-only:
    `get_adapter` (per-model adapters, keys, retry logic) and `load_settings`/
    `load_env` (model snapshots + .env loading). It does NOT import, read, or
    write anything under data/state/, decision logs, perf logs, or any pipeline
    module that touches portfolio state.
  * The v2 trading persona is read directly from prompts/v2.txt (a static
    template file) — not via the prompt_builder pipeline — so nothing that
    reads/writes state is on the import path.
  * Every file this script writes lands in this directory
    (side_experiments/spcx_probe/). Nothing under data/ or reports/.

This is a probe of cognition, not a trade. Nothing here affects any model's real
portfolio, decision log, or RQ dataset.

Run:
    python side_experiments/spcx_probe/run_probe.py                # all 6 models x3, temp=0
    python side_experiments/spcx_probe/run_probe.py --dry-run      # build prompt, no API calls, no file write
    python side_experiments/spcx_probe/run_probe.py --runs 3 --models claude,grok
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --- standalone bootstrap: make the shared src/ importable read-only -------------
THIS_DIR = Path(__file__).resolve().parent              # .../side_experiments/spcx_probe
PROJECT_ROOT = THIS_DIR.parent.parent                   # repo root
sys.path.insert(0, str(PROJECT_ROOT))

# The ONLY shared imports — all read-only API-client config. No state modules.
from src.adapters import get_adapter            # noqa: E402  per-model adapters + retry logic
from src.config_loader import load_env, load_settings  # noqa: E402  model snapshots + .env

logger = logging.getLogger("spcx_probe")

# --- constants -------------------------------------------------------------------
OUTPUT_DIR = THIS_DIR                                    # all output stays here
RESPONSES_PATH = OUTPUT_DIR / "responses.jsonl"
PROMPT_DUMP_PATH = OUTPUT_DIR / "prompt_used.txt"
V2_PROMPT_PATH = PROJECT_ROOT / "prompts" / "v2.txt"

PROBE_TEMPERATURE = 0.0
RUNS_PER_MODEL = 3
SEED_CAPITAL = 100_000.0
SPCX_TICKER = "SPCX"
RUN_DATE = "2026-06-12"

# Per-provider API key env var names (for a pre-flight "which keys are missing" warning).
_PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

# Public IPO facts as of the run date — IDENTICAL for all six models, so reactions
# are not confounded by different information. Only public facts; the live price is
# appended at runtime (or marked unavailable — never invented).
SPCX_FACTS = """SpaceX (ticker SPCX) — IPO facts, public as of 2026-06-12:
  - Debuted today, 2026-06-12, on the Nasdaq.
  - IPO priced at $135.00 per share; 555.6M shares offered; ~$75B raised.
  - Valuation ~$1.77 trillion at the pricing — the 7th most valuable US company,
    ranking above Tesla (TSLA).
  - Indicated to open around $175 (~30% first-trade "pop" over the IPO price).
  - Business segments:
      * Launch / Space (rockets, launch services)
      * Starlink / Connectivity (satellite broadband)
      * AI — including stated plans for orbital AI data centers and a constellation
        of 100,000+ satellites.
  - Company states it has been cash-flow positive since ~2015.
  - Last reported quarter: net income of -$4.28B (a quarterly net loss).
  - BlackRock reportedly placed an anchor order of at least $5B.
  - Retail demand reportedly exceeded $100B in indicated orders."""

# Probe addendum appended to the v2 system prompt. Keeps the trading persona intact
# (role, objective, confidence anchors, output discipline) while (a) framing the whole
# exercise as hypothetical, (b) stating SPCX is outside the model's real universe and
# this never affects its real portfolio, and (c) pinning the JSON output to the exact
# field names the pipeline parser keys off — plus one extra free-text `ipo_view` field.
PROBE_ADDENDUM = """\
====================  HYPOTHETICAL POST-CUTOFF-ASSET PROBE  ====================

IMPORTANT — READ FIRST. This is a HYPOTHETICAL thought-experiment, not a real
decision period in your experiment. SPCX (SpaceX) is NOT in your actual trading
universe and is not a stock you normally manage. Nothing you decide here is
executed, logged to your record, or applied to your real portfolio. You are being
asked, as a one-off probe, how you WOULD react to this newly public company under
the explicit hypothesis that it were tradable from an all-cash $100,000 book.

Reason exactly as your normal trading persona would: same objective (long-run
risk-adjusted return), same operating constraints (long-only, max 20% in a single
name, etc.), and the same confidence-scoring discipline defined above.

# Output format — JSON schema (authoritative field names)

Return ONLY a single JSON object — no prose before or after, no markdown code
fences. Use exactly these field names.

{
  "ipo_view": "<brief free-text view on the SPCX IPO itself: the ~$1.77T valuation, the bull case, and the key risks as you see them>",
  "overall_reasoning": "<your overall read on whether/how to act on SPCX this period>",
  "cash_rationale": "<why your resulting cash level is a deliberate choice; null if not applicable>",
  "decisions": [
    {
      "action": "BUY" | "SELL" | "HOLD",
      "ticker": "SPCX",
      "target_weight": <float 0.0-0.20: SPCX's desired share of portfolio value after the trade; this is the trade "size">,
      "confidence": <integer 1-10, per the confidence anchors above>,
      "summary": "<one sentence, plain English>",
      "reasoning": "<the deeper rationale: signals, drivers, sizing logic>",
      "confidence_justification": "<maps the score to its anchor band; if confidence >= 8, names the specific driver>",
      "why_now": "<why acting on the IPO now is preferable to waiting>"
    }
  ],
  "no_trade_reason": "<if you choose not to trade SPCX, state why here; otherwise null>"
}

Notes:
- `decisions` must be a list. If you decide to act, include one entry for SPCX. If
  you decline to trade it, return an empty `decisions` list and set `no_trade_reason`.
- A HOLD/abstain is a perfectly valid answer for a novel post-IPO name.
- `ipo_view` is required regardless of your decision.
- Remember: this is hypothetical and SPCX is not in your real universe."""


def _build_portfolio_block() -> str:
    """Render the standard $100k all-cash book in the experiment's portfolio style.

    Mirrors src.prompt_builder._format_portfolio_block exactly (replicated inline so
    this probe imports no pipeline/state modules). Identical for all six models.
    """
    lines = [
        "YOUR CURRENT PORTFOLIO STATE:",
        f"  Total value:   ${SEED_CAPITAL:,.2f}",
        f"  Cash:          ${SEED_CAPITAL:,.2f} (100.0%)",
        "  Open positions: 0 / 50 max",
        "  (no open positions — 100% cash)",
    ]
    return "\n".join(lines)


def fetch_spcx_price() -> tuple[float | None, str]:
    """Fetch the latest/close SPCX price via yfinance. Never invents a number.

    Returns (price_or_None, source_note). On any failure returns
    (None, "<reason>") and the prompt states "first-day price unavailable".
    """
    try:
        import yfinance as yf
    except Exception as e:  # yfinance not installed
        return None, f"yfinance import failed: {e}"
    try:
        ticker = yf.Ticker(SPCX_TICKER)
        hist = ticker.history(period="5d")
        if hist is not None and not hist.empty:
            last_close = float(hist["Close"].iloc[-1])
            last_day = str(hist.index[-1].date())
            if last_close > 0:
                return last_close, f"yfinance close {last_day}"
        # Fallback to fast_info last price (intraday) if history is empty
        fast = getattr(ticker, "fast_info", None)
        if fast is not None:
            last = getattr(fast, "last_price", None)
            if last:
                return float(last), "yfinance fast_info.last_price"
        return None, "yfinance returned no data for SPCX"
    except Exception as e:
        return None, f"yfinance error: {e}"


def build_prompts() -> tuple[str, str, float | None, str]:
    """Build (system_prompt, user_prompt, spcx_price, price_source).

    System prompt = the verbatim v2 trading persona + the hypothetical probe
    addendum. User prompt = v2 structure: market context (SPCX facts + live price)
    -> portfolio ($100k cash) -> decision request. Identical across all six models.
    """
    with open(V2_PROMPT_PATH, "r", encoding="utf-8") as f:
        v2_persona = f.read().strip()
    system_prompt = f"{v2_persona}\n\n{PROBE_ADDENDUM}"

    price, source = fetch_spcx_price()
    if price is not None:
        price_line = f"  - Current/last traded price (fetched {source}): ${price:,.2f}"
    else:
        price_line = f"  - Current/last traded price: first-day price unavailable ({source})."

    user_prompt = "\n".join([
        f"DATE: {RUN_DATE}",
        "EXECUTION MODE: HYPOTHETICAL PROBE (not a real decision period)",
        "",
        "=== HYPOTHETICAL POST-CUTOFF-ASSET PROBE ===",
        "The following describes a company that went public TODAY and is NOT part of",
        "your normal trading universe. Treat this as a one-off hypothetical: how would",
        "you react if SPCX were tradable from your current book? Nothing here is real or",
        "executed.",
        "",
        "MARKET CONTEXT:",
        SPCX_FACTS,
        price_line,
        "",
        _build_portfolio_block(),
        "",
        "TASK:",
        "  (a) Give your hypothetical decision as the JSON `decisions` list (action,",
        "      ticker SPCX, size as target_weight, confidence, rationale) under the",
        "      explicit hypothesis that SPCX is tradable.",
        "  (b) Give a brief free-text `ipo_view` on the IPO — the valuation and the risks.",
        "  (c) Remember this is HYPOTHETICAL and SPCX is NOT in your actual universe; it",
        "      does not touch your real portfolio.",
        "",
        "Output your response now as a single JSON object conforming to the schema in",
        "the system prompt.",
    ])
    return system_prompt, user_prompt, price, source


def _loads_lenient(raw: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction from a raw model response (fences/preamble tolerant).

    Mirrors the slicing in BaseAdapter._parse_response so we can pull the extra
    `ipo_view` field that the pipeline parser doesn't surface. Returns None on failure.
    """
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    else:
        first, last = text.find("{"), text.rfind("}")
        if first != -1 and last > first:
            text = text[first:last + 1]
    try:
        return json.loads(text)
    except Exception:
        return None


def _spcx_decision(decisions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the SPCX decision from the parsed list (or the first decision if any)."""
    if not decisions:
        return None
    for d in decisions:
        if str(d.get("ticker", "")).upper() == SPCX_TICKER:
            return d
    return decisions[0]


def run_probe(
    runs: int = RUNS_PER_MODEL,
    model_keys: list[str] | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Execute the probe: each selected model `runs` times at temperature=0.

    Returns the list of per-call records (also written to responses.jsonl unless
    dry_run). Never raises on a model failure — failed calls are recorded.
    """
    load_env()
    settings = load_settings()
    all_enabled = [k for k, c in settings["models"].items() if c.get("enabled", True)]
    selected = model_keys or all_enabled
    selected = [k for k in selected if k in settings["models"]]
    if not selected:
        logger.error("No valid models selected (known: %s)", list(settings["models"]))
        return []

    system_prompt, user_prompt, price, price_source = build_prompts()
    prompt_hash = hashlib.sha256(
        (system_prompt + "\n\n" + user_prompt).encode("utf-8")
    ).hexdigest()
    logger.info("Prompt built — hash=%s | SPCX price: %s (%s)",
                prompt_hash[:12],
                f"${price:,.2f}" if price is not None else "UNAVAILABLE",
                price_source)

    # Dump the exact prompt used for auditability (stays in this dir).
    with open(PROMPT_DUMP_PATH, "w", encoding="utf-8") as f:
        f.write(f"# SPCX PROBE PROMPT — hash {prompt_hash}\n")
        f.write(f"# SPCX price: {price if price is not None else 'unavailable'} ({price_source})\n")
        f.write("\n===== SYSTEM PROMPT =====\n\n")
        f.write(system_prompt)
        f.write("\n\n===== USER PROMPT =====\n\n")
        f.write(user_prompt)
        f.write("\n")

    # Pre-flight: warn about any missing provider keys (calls will still be attempted
    # and recorded as failures, but this surfaces the cause up front).
    for key in selected:
        provider = settings["models"][key]["provider"]
        env_name = _PROVIDER_KEY_ENV.get(provider)
        if env_name and not os.getenv(env_name):
            logger.warning("Model '%s' (%s): %s is not set — its calls will fail.",
                           key, provider, env_name)

    probe_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{prompt_hash[:8]}"

    if dry_run:
        logger.info("DRY RUN — would issue %d models x %d runs = %d calls at temp=%s. "
                    "No API calls, no responses.jsonl written.",
                    len(selected), runs, len(selected) * runs, PROBE_TEMPERATURE)
        return []

    records: list[dict[str, Any]] = []
    for key in selected:
        cfg = settings["models"][key]
        provider = cfg["provider"]
        snapshot = cfg["model"]
        display = cfg.get("display_name", key)
        adapter = get_adapter(provider, snapshot, temperature=PROBE_TEMPERATURE)
        logger.info("=== %s (%s / %s) — %d runs ===", key, provider, snapshot, runs)

        for run_index in range(runs):
            result = adapter.generate_decision(system_prompt, user_prompt, images=None)
            raw = result.raw_response or ""
            parsed = _loads_lenient(raw) or {}
            ipo_view = str(parsed.get("ipo_view", "") or "").strip()
            decision = _spcx_decision(result.decisions)
            meta = result.metadata or {}

            record = {
                "probe_id": probe_id,
                "model_key": key,
                "provider": provider,
                "snapshot": snapshot,
                "display_name": display,
                "model_id_returned": result.model_id_returned,
                "run_index": run_index,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "temperature": PROBE_TEMPERATURE,
                "prompt_hash": prompt_hash,
                "prompt_version": "v2-spcx-probe",
                "spcx_price": price,
                "spcx_price_source": price_source,
                "api_success": bool(result.success),
                "error": result.error,
                "latency_seconds": round(result.latency_seconds, 3),
                "attempt": meta.get("attempt"),
                "tokens": {
                    "input": meta.get("input_tokens"),
                    "output": meta.get("output_tokens"),
                },
                "cost_usd": meta.get("cost_usd"),
                # The hypothetical decision (SPCX) + supporting free-text.
                "decision": decision,
                "decisions_all": result.decisions,
                "overall_reasoning": result.overall_reasoning,
                "ipo_view": ipo_view,
                "no_trade_reason": result.no_trade_reason,
                # Full raw for audit.
                "raw_response": raw,
            }
            records.append(record)

            if result.success:
                act = decision.get("action") if decision else "NO-TRADE"
                conf = decision.get("confidence") if decision else "-"
                tw = decision.get("target_weight") if decision else 0.0
                logger.info("  run %d/%d: %s SPCX  size=%.0f%%  conf=%s  cost=%s",
                            run_index + 1, runs, act,
                            (tw or 0.0) * 100, conf,
                            f"${meta.get('cost_usd'):.4f}" if meta.get("cost_usd") is not None else "n/a")
            else:
                logger.warning("  run %d/%d: FAILED — %s",
                               run_index + 1, runs, result.error)

    # Write all records to responses.jsonl (in this directory only).
    with open(RESPONSES_PATH, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logger.info("Wrote %d records to %s", len(records), RESPONSES_PATH)
    return records


def _consistency_label(actions: list[str], weights: list[float], confs: list[Any]) -> str:
    """Summarize 3-run agreement: identical / same-action / split, with the spread."""
    if not actions:
        return "no successful runs"
    # Build a comparable signature per run: (action, size bucket, confidence).
    sigs = {
        (a, round((w or 0.0), 2), c)
        for a, w, c in zip(actions, weights, confs)
    }
    distinct_actions = sorted(set(actions))
    if len(sigs) == 1:
        return f"identical ({len(actions)}/{len(actions)})"
    if len(distinct_actions) == 1:
        return f"same action ({distinct_actions[0]}), size/conf vary"
    return "split: " + "/".join(distinct_actions)


def print_summary(records: list[dict[str, Any]]) -> None:
    """Print the per-model summary table: action(s), size, confidence, 3-run consistency."""
    if not records:
        logger.info("No records to summarize.")
        return

    # Preserve model order as first seen.
    order: list[str] = []
    by_model: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        k = r["model_key"]
        if k not in by_model:
            by_model[k] = []
            order.append(k)
        by_model[k].append(r)

    rows: list[tuple[str, ...]] = []
    total_cost = 0.0
    for key in order:
        recs = by_model[key]
        display = recs[0].get("display_name", key)
        ok = [r for r in recs if r["api_success"]]
        actions, weights, confs = [], [], []
        for r in ok:
            d = r.get("decision")
            if d:
                actions.append(str(d.get("action", "?")))
                weights.append(float(d.get("target_weight") or 0.0))
                confs.append(d.get("confidence"))
            else:
                actions.append("NO-TRADE")
                weights.append(0.0)
                confs.append("-")
        for r in recs:
            if r.get("cost_usd"):
                total_cost += r["cost_usd"]

        n_ok = len(ok)
        action_str = "/".join(actions) if actions else "—"
        if weights:
            avg_w = sum(weights) / len(weights)
            size_str = f"{avg_w * 100:.0f}%"
        else:
            size_str = "—"
        conf_str = "/".join(str(c) for c in confs) if confs else "—"
        consistency = _consistency_label(actions, weights, confs)
        if n_ok < len(recs):
            consistency += f" [{len(recs) - n_ok} failed]"
        rows.append((display, action_str, size_str, conf_str, consistency))

    # Render fixed-width.
    headers = ("MODEL", "ACTION (per run)", "AVG SIZE", "CONF (per run)", "3-RUN CONSISTENCY")
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(row: tuple[str, ...]) -> str:
        return "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))

    line = "-" * (sum(widths) + 2 * (len(widths) - 1))
    print()
    print("=" * len(line))
    print("SPCX POST-CUTOFF PROBE — SUMMARY  (NON-EXPERIMENTAL; hypothetical only)")
    print("=" * len(line))
    print(fmt(headers))
    print(line)
    for row in rows:
        print(fmt(row))
    print(line)
    n_calls = len(records)
    n_fail = sum(1 for r in records if not r["api_success"])
    print(f"{n_calls} calls ({n_fail} failed) · temp={PROBE_TEMPERATURE} · "
          f"total cost ${total_cost:.4f} · output: {RESPONSES_PATH.name}")
    print()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(
        description="SPCX post-cutoff-asset probe (NON-EXPERIMENTAL side experiment)"
    )
    parser.add_argument("--runs", type=int, default=RUNS_PER_MODEL,
                        help=f"Runs per model (default {RUNS_PER_MODEL})")
    parser.add_argument("--models", default=None,
                        help="Comma-separated model keys (default: all enabled)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build the prompt and report the plan; no API calls, no file write")
    args = parser.parse_args()

    model_keys = [m.strip() for m in args.models.split(",")] if args.models else None
    records = run_probe(runs=args.runs, model_keys=model_keys, dry_run=args.dry_run)
    if records:
        print_summary(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
