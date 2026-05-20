"""RQ6 determinism probe — manual-trigger only.

Re-runs each model K times at temperature = 0 on an *identical* fixed input
and logs every run, so `research_metrics.compute_rq6` can measure how often
the same prompt yields a different decision. This is the empirical ceiling on
RQ1: two models cannot agree more reliably than one model agrees with itself.

Per the pre-registration (docs/PRE_REGISTRATION.md, RQ6):
  * K = 10 reruns per model (override with --k)
  * a seeded random ~5% subsample of the universe (override with --sample-fraction)
  * temperature = 0 (greedy decoding requested via the adapter)

It reuses the production prompt builder and adapters unchanged — only the
sampling temperature differs — so the cognition under test is identical to
live. Screening is skipped on purpose: the trading prompt is built once per
model from a fixed shortlist (the sampled tickers + that model's current
holdings) and held byte-identical across the K reruns, isolating decode-time
non-determinism from screening-time non-determinism.

Output: one JSONL file per invocation in data/determinism/, one line per
rerun, in the schema compute_rq6 expects:
    {probe_id, timestamp, model_key, tick_id, run_index, temperature,
     decisions:[{ticker, action, target_weight, confidence}], api_success, error}

Run:
    python -m scripts.determinism_probe                  # all models, K=10, 5%
    python -m scripts.determinism_probe --dry-run        # build prompts, no API calls
    python -m scripts.determinism_probe --k 5 --models claude,gpt
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Allow running as a script from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.adapters import get_adapter  # noqa: E402
from src.config_loader import (  # noqa: E402
    DATA_DIR,
    configure_logging,
    load_env,
    load_settings,
    universe_symbols,
)
from src.data import fetch_news, fetch_universe_data  # noqa: E402
from src.data.sentiment import compute_sentiment_dict  # noqa: E402
from src.portfolio import load_portfolio  # noqa: E402
from src.prompt_builder import build_prompts, hash_inputs  # noqa: E402

logger = logging.getLogger("llmlab.determinism_probe")
EASTERN = ZoneInfo("America/New_York")
DETERMINISM_DIR = DATA_DIR / "determinism"
PROBE_TEMPERATURE = 0.0


def _prices_from_data(data: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for ticker, df in data.items():
        if df is not None and not df.empty:
            out[ticker] = float(df["Close"].iloc[-1])
    return out


def _sample_universe(fraction: float, seed: int) -> list[str]:
    """Seeded random subsample of the universe (the pre-registered ~5%)."""
    syms = universe_symbols()
    n = max(1, math.ceil(fraction * len(syms)))
    rng = random.Random(seed)
    return sorted(rng.sample(syms, min(n, len(syms))))


def _decisions_for_log(decision_result) -> list[dict]:
    """Compact decision view for the rerun record."""
    out = []
    for d in decision_result.decisions:
        out.append({
            "ticker": d.get("ticker"),
            "action": d.get("action"),
            "target_weight": d.get("target_weight"),
            "confidence": d.get("confidence"),
        })
    return out


def run_probe(
    k: int = 10,
    sample_fraction: float = 0.05,
    seed: int = 42,
    model_keys: list[str] | None = None,
    include_news: bool = False,
    dry_run: bool = False,
    output_dir: str | None = None,
) -> str | None:
    load_env()
    settings = load_settings()
    all_models = [m for m, cfg in settings["models"].items() if cfg.get("enabled", True)]
    model_keys = model_keys or all_models
    model_keys = [m for m in model_keys if m in settings["models"]]
    if not model_keys:
        logger.error("No valid models selected")
        return None

    run_date = datetime.now(EASTERN)
    sampled = _sample_universe(sample_fraction, seed)
    logger.info("Seeded %.0f%% universe subsample (seed=%d): %s",
                sample_fraction * 100, seed, ", ".join(sampled))

    # Fetch only the symbols we actually need: the sampled tickers + every
    # selected model's current holdings + the benchmark. Much cheaper than
    # the full 79-name pull and sufficient because we feed a fixed shortlist.
    needed: set[str] = set(sampled)
    needed.add(settings.get("benchmark_ticker", "SPY"))
    holdings_by_model: dict[str, list[str]] = {}
    for key in model_keys:
        try:
            held = list(load_portfolio(key).holdings.keys())
        except Exception:
            held = []
        holdings_by_model[key] = held
        needed.update(held)

    logger.info("Fetching market data for %d symbols", len(needed))
    market_data = fetch_universe_data(symbols=sorted(needed))
    prices = _prices_from_data(market_data)
    tick_id = hash_inputs(market_data)

    news_data = None
    sentiment_data = None
    if include_news:
        try:
            news_data = fetch_news(sorted(needed))
            sentiment_data = compute_sentiment_dict(news_data)
            logger.info("News + sentiment loaded for prompt context")
        except Exception as e:
            logger.warning("News fetch failed (%s) — continuing without it", e)

    probe_id = f"{run_date.strftime('%Y%m%dT%H%M%S')}_{tick_id[:8]}"
    records: list[dict] = []

    for key in model_keys:
        cfg = settings["models"][key]
        portfolio = load_portfolio(key)
        snapshot_before = portfolio.snapshot(prices)
        # Fixed shortlist held identical across all K reruns
        shortlist = sorted(set(sampled) | set(holdings_by_model.get(key, [])))
        system_prompt, user_prompt, prompt_version, _images = build_prompts(
            snapshot_before, market_data, run_date,
            trades_executed_today=0, runs_today=0, is_eod=False,
            include_chart_image=False,
            news_data=news_data, sentiment_data=sentiment_data,
            shortlisted_symbols=shortlist, recent_decisions=[],
        )
        logger.info("[%s] prompt built (%d chars), shortlist=%d, tick=%s",
                    key, len(user_prompt), len(shortlist), tick_id[:8])

        if dry_run:
            logger.info("[%s] DRY RUN — would issue %d reruns at temperature=%s",
                        key, k, PROBE_TEMPERATURE)
            continue

        adapter = get_adapter(cfg["provider"], cfg["model"], temperature=PROBE_TEMPERATURE)
        for run_index in range(k):
            try:
                result = adapter.generate_decision(system_prompt, user_prompt, images=None)
                rec = {
                    "probe_id": probe_id,
                    "timestamp": datetime.utcnow().isoformat(),
                    "model_key": key,
                    "model_id": cfg["model"],
                    "tick_id": tick_id,
                    "prompt_version": prompt_version,
                    "run_index": run_index,
                    "temperature": PROBE_TEMPERATURE,
                    "decisions": _decisions_for_log(result),
                    "api_success": bool(result.success),
                    "error": result.error,
                }
            except Exception as e:
                logger.exception("[%s] rerun %d crashed", key, run_index)
                rec = {
                    "probe_id": probe_id, "timestamp": datetime.utcnow().isoformat(),
                    "model_key": key, "tick_id": tick_id, "run_index": run_index,
                    "temperature": PROBE_TEMPERATURE, "decisions": [],
                    "api_success": False, "error": str(e),
                }
            records.append(rec)
            logger.info("[%s] rerun %d/%d: %d decisions (success=%s)",
                        key, run_index + 1, k, len(rec["decisions"]), rec["api_success"])

    if dry_run:
        logger.info("Dry run complete — prompts built for %d models, no API calls, "
                    "no file written.", len(model_keys))
        return None

    out_dir = output_dir or str(DETERMINISM_DIR)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"probe_{probe_id}.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    logger.info("Wrote %d rerun records to %s", len(records), out_path)
    return out_path


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="RQ6 temperature=0 determinism probe (manual)")
    parser.add_argument("--k", type=int, default=10, help="Reruns per model (default 10)")
    parser.add_argument("--sample-fraction", type=float, default=0.05,
                        help="Universe subsample fraction (default 0.05)")
    parser.add_argument("--seed", type=int, default=42, help="Subsample seed for reproducibility")
    parser.add_argument("--models", default=None, help="Comma-separated model keys (default: all enabled)")
    parser.add_argument("--include-news", action="store_true", help="Add news + sentiment to the prompt")
    parser.add_argument("--dry-run", action="store_true", help="Build prompts but make no API calls")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    model_keys = [m.strip() for m in args.models.split(",")] if args.models else None
    path = run_probe(
        k=args.k, sample_fraction=args.sample_fraction, seed=args.seed,
        model_keys=model_keys, include_news=args.include_news,
        dry_run=args.dry_run, output_dir=args.output_dir,
    )
    if path:
        # Summarize via the analyzer so a run immediately shows the flip rate.
        try:
            from src.analytics.research_metrics import compute_rq6, get_model_keys
            summary = compute_rq6(get_model_keys())
            logger.info("RQ6 now: overall flip rate=%s", summary.get("overall_decision_flip_rate"))
        except Exception:
            logger.exception("RQ6 summary failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
