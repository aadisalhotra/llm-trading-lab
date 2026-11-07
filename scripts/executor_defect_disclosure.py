"""Disclosure artifacts for the 2026-11-06 executor one-share floor.

Governing text: docs/registrations/executor_sizing_floor_ruling.md (sha256
34eb21268de60a6d...), sections 3 and 4. The fix landed as 98f36b8b9..bbee5da1f.
This script changes nothing on the order path and rewrites no decision log. It
reads the logs and writes two disclosure files:

  data/annotations/executor_defect_2026-11-06.jsonl
      One EXECUTOR_DEFECT_SUPPRESSED record per BUY the floor deleted on
      2026-11-06: exactly 140. The 12 whose available cash was below one share's
      price also carry the ruling's counterfactual annotation, as disclosure only.

  data/annotations/g_exec_recompute_2026-11-06.json
      Friday's Gate 4 per book: the reading as recorded (1.0 on every book) beside
      the recompute under the no-third-state clause, and the session classification.

It also prints (never writes) the paper-era exposure table that the ledger entry
records, so every number in that entry can be reproduced from the logs.

How the sizing state is rebuilt (2026-11-06, broker path, `_size_for_broker` at
executor.py:449 before the fix). Day one had no SELL, SHORT or COVER, so the
reconstruction is exact rather than approximate:
  * cash and settled cash at cycle start are the previous record's portfolio_after
    (2000.00 for the first cycle, the registered inception);
  * the reference price of a held name is this cycle's portfolio_after
    current_price; of a skipped name, the SKIP record's fill_price, which the old
    code set to the reference price;
  * NAV at sizing is cash plus held shares at those prices;
  * `reserved` is the running sum of earlier sized BUYs, qty x reference price,
    exactly as the P1 batch reserved it.
Self-check: every filled BUY's recomputed quantity must equal the venue's
requested_qty, and every skip must satisfy (capped delta) < price. Any mismatch
aborts the run; nothing is written on a failed check.

Keys: no 2026-11-06 record carries a decision id (the stamp landed in
159e907e4, after the session). Each record is therefore keyed by the composite
book|cycle_id|execution_index|ticker, and says so in `key_type`. A record that did
carry a decision id would use it, with key_type "decision_id".

Run:
    python scripts/executor_defect_disclosure.py            # write both files
    python scripts/executor_defect_disclosure.py --check    # verify committed files
    python scripts/executor_defect_disclosure.py --exposure # print paper-era table
Exit 0 = written / identical; 1 = a check failed.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.analytics.execution_metrics import (  # noqa: E402
    EXECUTION_SUCCESS_THRESHOLD, classify_execution, load_reconciliation, score_cycles,
)
from src.config_loader import DATA_DIR, RECONCILIATION_DIR, TRADES_DIR  # noqa: E402

logger = logging.getLogger("llmlab.scripts.executor_defect_disclosure")

DATE = "2026-11-06"
COHORT = ("claude", "claude_opus", "gemini", "gpt", "grok")
INCEPTION_CASH = 2000.0
FLOOR_LABEL = "Insufficient cash for meaningful position"
ANNOTATION = "EXECUTOR_DEFECT_SUPPRESSED"
RULING = "docs/registrations/executor_sizing_floor_ruling.md"
RULING_SHA256 = "34eb21268de60a6d7eba2f7eda142bf9120e226c024db886b7f24f2f6da5024c"
FLOOR_SITE = "src/execution/executor.py:449 (_size_for_broker) at 98f36b8b9"
COUNTERFACTUAL = ("Disclosure only, not a reclassification: a correct executor would "
                  "have produced a cash-capped order and a tagged constraint event.")
LEDGER_REF = ("scripts/phase_a_integrity_ledger.json "
              "operational_events['2026-11'].executor_floor_defect_2026_11_06")

OUT_DIR = DATA_DIR / "annotations"
ANNOTATION_PATH = OUT_DIR / "executor_defect_2026-11-06.jsonl"
GEXEC_PATH = OUT_DIR / "g_exec_recompute_2026-11-06.json"

# The reading as recorded before the fix: Gate 4 as scored by
# src/analytics/execution_metrics.py at 98f36b8b9, where an untagged skip with no
# order id fell through to no_order. Reproduced 2026-11-07 by running that
# scorer on these same logs: 13 of 13 cycles successful on every book.
RECORDED_READING = {"cycles": 13, "successes": 13, "execution_success_rate": 1.0,
                    "scorer": "src/analytics/execution_metrics.py at 98f36b8b9"}


def _trunc4(x: float) -> float:
    return int(x * 10000) / 10000


def _day_records(book: str) -> list[dict[str, Any]]:
    path = TRADES_DIR / f"{book}_{DATE[:7]}.jsonl"
    out = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{n} is not valid JSON: {exc}") from exc
            if rec.get("date") == DATE:
                out.append(rec)
    return out


def _cycle_ts(cycle_id: str) -> str:
    """20261106T143249Z -> 2026-11-06T14:32:49Z (the cycle's UTC start)."""
    d, t = cycle_id.rstrip("Z").split("T")
    return f"{d[:4]}-{d[4:6]}-{d[6:]}T{t[:2]}:{t[2:4]}:{t[4:6]}Z"


def reconstruct() -> list[dict[str, Any]]:
    """The 140 suppressed BUYs, with the sizing state each one met. Raises on any
    self-check failure."""
    out: list[dict[str, Any]] = []
    for book in COHORT:
        cash = settled = INCEPTION_CASH
        held: dict[str, float] = {}
        for rec in _day_records(book):
            if rec.get("execution_mode") != "broker_paper":
                raise ValueError(f"{book} {rec.get('cycle_id')}: not a broker_paper record")
            pa = rec["portfolio_after"]
            ref = {h["ticker"]: float(h["current_price"]) for h in pa["holdings"]}
            for e in rec["executions"]:
                if e["side"] == "SKIP":
                    ref.setdefault(e["ticker"], float(e["fill_price"]))
            missing = sorted(t for t in held if t not in ref)
            if missing:
                raise ValueError(f"{book} {rec['cycle_id']}: no reference price for {missing}")
            nav = cash + sum(s * ref[t] for t, s in held.items())
            reserved = 0.0
            for idx, e in enumerate(rec["executions"]):
                d = e["decision"]
                if e["side"] in ("SELL", "SHORT", "COVER") or d["action"] not in ("BUY", "HOLD"):
                    raise ValueError(f"{book} {rec['cycle_id']}: day one had no "
                                     f"{e['side']}/{d['action']}; reconstruction is not exact")
                if d["action"] != "BUY":
                    continue
                t = d["ticker"]
                px = ref[t]
                intended = nav * float(d["target_weight"]) - held.get(t, 0.0) * px
                if intended <= 0:
                    if e["side"] != "HOLD":
                        raise ValueError(f"{book} {t}: at target but logged {e['side']}")
                    continue
                spendable = settled - reserved
                available = cash - reserved
                capped = min(intended, max(spendable, 0.0))
                if e["side"] == "BUY":
                    qty = _trunc4(capped / px)
                    if abs(qty - float(e["broker_order"]["requested_qty"])) > 1e-9:
                        raise ValueError(f"{book} {t}: recomputed qty {qty} != venue "
                                         f"{e['broker_order']['requested_qty']}")
                    reserved += qty * px
                    continue
                if e["side"] != "SKIP" or e.get("error") != FLOOR_LABEL:
                    raise ValueError(f"{book} {t}: unexpected {e['side']} {e.get('error')!r}")
                if not capped < px:
                    raise ValueError(f"{book} {t}: logged as floored but {capped} >= {px}")
                decision_id = e.get("decision_id") or d.get("decision_id")
                key = f"{book}|{rec['cycle_id']}|{idx}|{t}"
                row = {
                    "annotation": ANNOTATION,
                    "date": DATE,
                    "book": book,
                    "cycle_id": rec["cycle_id"],
                    "cycle_timestamp_utc": _cycle_ts(rec["cycle_id"]),
                    "execution_timestamp": e["timestamp"],
                    "ticker": t,
                    "key_type": "decision_id" if decision_id else "composite",
                    "record_key": decision_id or key,
                    "decision_id": decision_id,
                    "target_weight": float(d["target_weight"]),
                    "intended_notional_usd": round(intended, 6),
                    "price": px,
                    "cash_usd": round(available, 6),
                    "settled_cash_usd": round(spendable, 6),
                    "book_cash_at_cycle_start_usd": round(cash, 6),
                    "book_settled_cash_at_cycle_start_usd": round(settled, 6),
                    "reserved_before_usd": round(reserved, 6),
                    "nav_at_sizing_usd": round(nav, 6),
                    "expressible_notional_usd": round(capped, 6),
                    "cash_below_one_share": available < px,
                    "logged_error": FLOOR_LABEL,
                    "site": FLOOR_SITE,
                    "gate4": "execution_failure (untagged skip; ruling section 3)",
                    "ruling": RULING,
                    "ledger": LEDGER_REF,
                }
                if available < px:
                    row["counterfactual"] = COUNTERFACTUAL
                out.append(row)
            cash, settled = float(pa["cash"]), float(pa["settled_cash"])
            held = {h["ticker"]: float(h["shares"]) for h in pa["holdings"]}
    return out


def gexec(rows: list[dict[str, Any]]) -> dict[str, Any]:
    recon = load_reconciliation(RECONCILIATION_DIR)
    per_book_rows = Counter(r["book"] for r in rows)
    books: dict[str, Any] = {}
    for book in COHORT:
        recs = _day_records(book)
        scored = score_cycles(recs, recon)
        outcomes = Counter(classify_execution(e) for r in recs for e in r["executions"])
        failures = outcomes.get("fail:unclassified_skip", 0)
        if failures != per_book_rows[book]:
            raise ValueError(f"{book}: scorer sees {failures} untagged skips, "
                             f"annotation has {per_book_rows[book]}")
        rate = scored["execution_success_rate"]
        books[book] = {
            "original": dict(RECORDED_READING),
            "recomputed": {
                "cycles": scored["cycles"],
                "successes": scored["successes"],
                "execution_success_rate": round(rate, 6),
                "failure_cycles_by_reason": scored["failure_cycles_by_reason"],
                "scorer": "src/analytics/execution_metrics.py at bbee5da1f",
            },
            "orders": {
                "suppressed_untagged_skips": failures,
                "filled": outcomes.get("ok:terminal_filled", 0),
            },
            "session_classification": ("passes" if rate >= EXECUTION_SUCCESS_THRESHOLD
                                       else "decision-estimable (execution-impaired)"),
        }
    return {
        "date": DATE,
        "gate": "Gate 4 (G-EXEC), docs/prereg/tier2_novel_sections.md",
        "threshold": EXECUTION_SUCCESS_THRESHOLD,
        "unit": ("cycle: a cycle succeeds only if every intended order reached a terminal, "
                 "broker-reconciled state and the level-2 reconciliation was clean"),
        "basis": ("Ruling section 3: all 140 suppressed BUYs are untagged skips, and an "
                  "untagged skip is by definition an execution failure. The 12 cash-below-"
                  "one-share cases are failures as recorded; their counterfactual is in the "
                  "annotation file as disclosure, not as a reclassification."),
        "correlation_disclosure": ("One defect event, correlated across five books by "
                                   "construction: the same executor code path deleted the "
                                   "orders on every book. These are not five independent "
                                   "execution failures."),
        "failures_total": sum(per_book_rows.values()),
        "books": books,
        "annotation_file": str(ANNOTATION_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "ruling": {"path": RULING, "sha256": RULING_SHA256},
        "ledger": LEDGER_REF,
    }


def render(rows: list[dict[str, Any]], g: dict[str, Any]) -> tuple[bytes, bytes]:
    jl = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows)
    gj = json.dumps(g, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return jl.encode("utf-8"), gj.encode("utf-8")


def exposure() -> dict[str, Any]:
    """Paper-era floor hits (records before 2026-11-06), replayed sequentially as
    the simulator path (`_execute_one`, executor.py:882) sized them. NAV at sizing is
    the cycle's portfolio_after total_value (the simulator's fills move it by
    slippage only); the split is insensitive to that at the 0.1%-of-NAV level
    except for at most two sub-share top-ups."""
    files: dict[str, list[str]] = defaultdict(list)
    for p in sorted(glob.glob(str(TRADES_DIR / "*.jsonl"))):
        files[Path(p).name.rsplit("_", 1)[0]].append(p)
    hits = []
    for book, paths in files.items():
        prev = None
        for p in paths:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    r = json.loads(line)
                    pa = r.get("portfolio_after") or {}
                    if prev is None:
                        cash = settled = float(pa.get("inception_value") or 100000.0)
                        shares: dict[str, float] = {}
                    else:
                        cash = float(prev.get("cash", 0.0))
                        sc = prev.get("settled_cash")
                        settled = float(sc) if sc is not None else cash
                        shares = {h["ticker"]: h["shares"] for h in prev.get("holdings", [])}
                    nav = float(pa.get("total_value") or 0.0)
                    enforce = r["date"] >= "2026-10-23"  # settled-funds activation
                    for e in r.get("executions") or []:
                        t, side = e.get("ticker"), e.get("side")
                        n = float(e.get("notional") or 0.0)
                        if e.get("error") == FLOOR_LABEL and r["date"] < DATE:
                            px = float(e["fill_price"])
                            tw = float(e["decision"]["target_weight"])
                            delta = nav * tw - shares.get(t, 0.0) * px
                            deploy = min(settled, cash) if enforce else cash
                            hits.append({"book": book, "date": r["date"], "price": px,
                                         "target": nav * tw, "delta": delta, "deploy": deploy,
                                         "capped": max(min(delta, deploy), 0.0)})
                        if not e.get("executed"):
                            continue
                        if side in ("BUY", "COVER"):
                            cash -= n
                            settled -= n
                            shares[t] = shares.get(t, 0.0) + abs(float(e["shares"]))
                        elif side in ("SELL", "SHORT"):
                            cash += n
                            if not enforce:
                                settled += n
                            shares[t] = shares.get(t, 0.0) - abs(float(e["shares"]))
                    prev = pa
    table: dict[str, Any] = {}
    for book in sorted({h["book"] for h in hits}):
        xs = [h for h in hits if h["book"] == book]
        below = [h for h in xs if h["target"] < h["price"]]
        rest = [h for h in xs if h["target"] >= h["price"]]
        sent = [h["capped"] for h in xs if h["capped"] >= 1.0]
        table[book] = {
            "hits": len(xs),
            "target_below_one_share": len(below),
            "cash_limited": len(rest),
            "cash_limited_decomposition": {
                "full_delta_cash_below_price": sum(1 for h in rest if h["delta"] >= h["price"]),
                "subshare_topup_cash_below_price": sum(
                    1 for h in rest if h["delta"] < h["price"] and h["deploy"] < h["price"]),
                "subshare_topup_cash_at_or_above_price": sum(
                    1 for h in rest if h["delta"] < h["price"] and h["deploy"] >= h["price"]),
            },
            "first_date": min(h["date"] for h in xs),
            "last_date": max(h["date"] for h in xs),
            "hits_a_1usd_floor_would_have_sent": len(sent),
            "suppressed_notional_usd": round(sum(sent), 2),
            "largest_single_usd": round(max(sent), 2) if sent else 0.0,
            "hits_under_1usd_deployable": len(xs) - len(sent),
        }
    return table


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="verify the committed files")
    ap.add_argument("--exposure", action="store_true", help="print the paper-era table")
    args = ap.parse_args(argv)

    if args.exposure:
        t = exposure()
        print(json.dumps(t, indent=2, sort_keys=True))
        tot = {k: sum(v[k] for v in t.values()) for k in
               ("hits", "target_below_one_share", "cash_limited",
                "hits_a_1usd_floor_would_have_sent", "hits_under_1usd_deployable")}
        tot["suppressed_notional_usd"] = round(sum(v["suppressed_notional_usd"] for v in t.values()), 2)
        print(json.dumps({"total": tot}, sort_keys=True))
        return 0

    rows = reconstruct()
    if len(rows) != 140:
        logger.error("expected 140 suppressed BUYs, reconstructed %d", len(rows))
        return 1
    jl, gj = render(rows, gexec(rows))
    if args.check:
        ok = True
        for path, want in ((ANNOTATION_PATH, jl), (GEXEC_PATH, gj)):
            have = path.read_bytes().replace(b"\r\n", b"\n") if path.exists() else None
            if have != want:
                logger.error("%s does not match a fresh build from the logs", path)
                ok = False
            else:
                logger.info("%s matches", path.name)
        return 0 if ok else 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ANNOTATION_PATH.write_bytes(jl)
    GEXEC_PATH.write_bytes(gj)
    c = Counter(r["book"] for r in rows)
    logger.info("wrote %d records %s; counterfactual on %d", len(rows), dict(c),
                sum(1 for r in rows if "counterfactual" in r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
