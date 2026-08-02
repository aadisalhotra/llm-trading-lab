"""Populate short_activity.notable_short_fills in a committed monthly layer.

Hub ruling (2026-08-02, PI-relayed): the July report's named-fills disclosure
carries "Gemini's four largest short-side COVER fills by dollar value,
source-derived from the fill records." This script materializes them into the
layer FROM THE FILL RECORDS (the decision-log `executions` entries:
fill_price / notional / order_id / timestamps verbatim), never from report
prose or memo approximations, and cross-checks the extraction against the
layer's own already-certified short_activity aggregates before writing. Any
mismatch HALTS without writing.

Provenance note: a prior version of this file (untracked, created 2026-08-02
13:52 local — the parallel-session window) carried a DeepSeek designation
attributed to a "Hub rider (2026-08-02)". The hub disavowed that attribution
and ruled the staged rider discarded. Standing convention from 2026-08-02:
only PI-relayed instructions are hub instructions.

Same maintenance pattern as the hub-approved profile text: a registered
surgical merge into the committed layer, followed by the source_commit
re-reconcile ritual at commit time (a published layer whose source_commit
doesn't match its content is a release-gate failure).

Designations are per-month and explicit — nothing is inferred. A month with
no designation is not touched by this script.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("llmlab.reports.notable_short_fills")

# month -> designation. Values name WHICH fills are notable (hub-designated
# for the month's report narrative); every number in the emitted block comes
# from the decision-log fill records, not from this table. `top_n_by_notional`
# makes the selection mechanical: the N largest executed fills of `side` for
# `model` in `month`, by absolute dollar value.
DESIGNATIONS: dict[str, dict[str, Any]] = {
    "2026-07": {
        "model": "gemini",
        "side": "COVER",
        "top_n_by_notional": 4,
        "basis": (
            "Gemini's four largest short-side COVER fills by dollar value "
            "(hub ruling 2026-08-02, PI-relayed) — selection is mechanical: "
            "top 4 of the month's executed COVER fills by notional; every "
            "figure source-derived from the decision-log fill records."
        ),
    },
}


def extract_fills(month: str, model: str, side: str) -> list[dict[str, Any]]:
    """Executed fills of `side` for `model` in `month`, verbatim from the
    decision-log execution records."""
    log_path = ROOT / "data" / "trades" / f"{model}_{month}.jsonl"
    fills: list[dict[str, Any]] = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            for ex in rec.get("executions") or []:
                if ex.get("side") != side or not ex.get("executed"):
                    continue
                decision = ex.get("decision") or {}
                fills.append({
                    "model": model,
                    "date": rec.get("date"),
                    "timestamp": ex.get("timestamp"),
                    "ticker": ex.get("ticker"),
                    "side": side,
                    "shares": ex.get("shares"),
                    "fill_price": ex.get("fill_price"),
                    "notional": ex.get("notional"),
                    "order_id": ex.get("order_id"),
                    # target_weight 0.0 == full cover (closes the short);
                    # nonzero == partial cover. Straight off the logged decision.
                    "target_weight_after": decision.get("target_weight"),
                    "summary": decision.get("summary"),
                })
    fills.sort(key=lambda x: x["timestamp"] or "")
    return fills


def main() -> int:
    month = sys.argv[1] if len(sys.argv) > 1 else "2026-07"
    desig = DESIGNATIONS.get(month)
    if not desig:
        logger.error("No notable-short-fills designation registered for %s — nothing to do.", month)
        return 1

    layer_path = ROOT / "reports" / "monthly" / month / "data_layer.json"
    with open(layer_path, encoding="utf-8") as f:
        layer = json.load(f)
    sa = (layer.get("cross_model_behavioral") or {}).get("short_activity")
    if sa is None:
        logger.error("Layer has no short_activity block (pre-July shape?) — aborting.")
        return 1

    all_fills = extract_fills(month, desig["model"], desig["side"])
    n = desig["top_n_by_notional"]
    ranked = sorted(all_fills, key=lambda f: abs(f["notional"] or 0), reverse=True)
    selected = ranked[:n]
    # Presentation order = the selection order (largest first); the emitted
    # `selection` field states the criterion so the order is self-describing.
    fills = selected

    # ---- source-check against the layer's certified aggregates (HALT on drift)
    pm = (sa.get("per_model") or {}).get(desig["model"]) or {}
    checks = [
        # extraction basis must reproduce the certified aggregate exactly —
        # if the full cover count drifts, the top-N cut is meaningless
        ("extracted fill count == per_model covers", len(all_fills) == pm.get("covers")),
        ("designation N <= extracted fills", n <= len(all_fills)),
        # the cut must be strict: a tie across the boundary would make
        # "the four largest" ambiguous — halt for a hub call rather than pick
        ("top-N boundary unambiguous (no tie at the cut)",
         len(ranked) <= n or abs(ranked[n - 1]["notional"]) > abs(ranked[n]["notional"])),
        ("every selected fill has fill_price", all(f["fill_price"] for f in fills)),
        ("every selected fill has notional", all(f["notional"] for f in fills)),
        ("every selected fill has order_id", all(f["order_id"] for f in fills)),
        ("selected dates within calendar month", all((f["date"] or "").startswith(month) for f in fills)),
    ]
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        logger.info("check %-45s %s", name, "PASS" if ok else "FAIL")
    if failed:
        logger.error("Source-check FAILED (%s) — layer NOT written.", "; ".join(failed))
        return 1

    sa["notable_short_fills"] = {
        "designation": desig["basis"],
        "selection": (f"top {n} of {len(all_fills)} executed {desig['side']} fills "
                      "by |notional|, listed largest first"),
        "source": ("decision-log execution records (data/trades/"
                   f"{desig['model']}_{month}.jsonl) — fill_price/notional/order_id "
                   "verbatim; summary is the model's own logged decision summary"),
        "fills": fills,
    }

    with open(layer_path, "w", encoding="utf-8") as f:
        json.dump(layer, f, indent=2, default=str)
    logger.info("Wrote %d notable short fills into %s", len(fills), layer_path)
    print(f"{layer_path}")
    print("RE-RECONCILE REQUIRED at commit time: commit this edit, re-hash, "
          "write report_meta.source_commit, commit the reconcile (release gate).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
