"""Gate 4 -- execution success rate, per model-month (live-phase segments).

Registered predicate: docs/prereg/tier2_novel_sections.md, "Gate 4 -- Execution
integrity (live phase)". A decision cycle is execution-SUCCESSFUL iff

  (i)   every intended order was submitted to the venue, and
  (ii)  every intended order resolved to a terminal, broker-reconciled state --
        filled, partially filled, rejected by a legitimate market constraint
        (halt, liquidity, buying-power), or rejected under
        WASH_REJECT_POST_BARRIER, and
  (iii) the post-cycle level-2 reconciliation ran and was CLEAN.

A cycle FAILS when orders cannot be submitted (venue/API/auth failure), order
status is unresolved, or reconciliation diverges. Threshold: >= 0.80 per
model-segment. Inapplicable to paper-mode segments (simulated fills; trivially
1.0), so only broker-mode records are scored.

Design rules this module holds to:

  * A MISSING reconciliation record fails the cycle. Absence of evidence that
    the book agrees with the venue is not evidence that it does; scoring it as
    success would report a condition this gate does not measure.
  * Classification reads the execution record's own fields (`broker_order`,
    `constraint`, `order_id`, `error`, `stop_unprotected`); it never infers a
    venue outcome from `executed`, because a constraint-blocked order is
    execution-SUCCESSFUL and executed=false.
  * Cycles whose model call produced no decision (`api_success` false) are
    EXCLUDED, not failed: no order was intended, and Gate 1 already owns that
    failure. They are counted so the exclusion is visible.
  * Pre-submission suppression has no third state (ruling 2026-11-06,
    docs/registrations/executor_sizing_floor_ruling.md section 3): a skip is a
    registered, tagged constraint event or an execution failure. An untagged
    skip is the second, whatever its `error` says.
  * An un-mediated WASH_TRADE_BLOCK is a FAILURE. The 2026-09-15 landing note
    files only the POST-barrier case as a success class; the un-mediated
    collision stays unfiled until its class is ruled.
  * Reconciliation divergence is venue-wide (one aggregate record per cycle),
    so it fails every book's cycle for that cycle_id -- consistent with the
    registered correlation disclosure (a broker outage is one event, not six).
"""
from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from ..config_loader import RECONCILIATION_DIR, TRADES_DIR
from ..execution.broker import TERMINAL_STATES
from ..execution.executor import CONSTRAINT_WASH_TRADE_BLOCK, REGISTERED_SKIP_CLASSES
from ..record_class import (ADMIN_CLASS_EOD_POSITION_STOP, EOD_STOP_NOT_EXECUTABLE,
                            RECORD_CLASS_ADMINISTRATIVE)

logger = logging.getLogger("llmlab.analytics.execution_metrics")

# Registered threshold (Gate 4), uniform with the decision-completeness gate.
EXECUTION_SUCCESS_THRESHOLD = 0.80

BROKER_MODES = frozenset({"broker_paper", "live"})

# Constraint classes that are execution-SUCCESSFUL when no broker order exists:
# the linkage worked and the venue/our rules declined. This is the executor's
# closed set, so the alarm and this gate cannot accept different things.
_LEGIT_CONSTRAINTS = REGISTERED_SKIP_CLASSES

# Outcome labels. Success labels start "ok:", failures "fail:".
NO_ORDER = "no_order"


def classify_execution(e: dict[str, Any]) -> str:
    """Classify one execution record: `no_order`, `ok:<why>` or `fail:<why>`."""
    if e.get("stop_unprotected"):
        # A risk stop that never reached the venue: the position is still open.
        return "fail:stop_unprotected"

    bo = e.get("broker_order")
    if bo:
        status = str(bo.get("status") or "").lower()
        if status in TERMINAL_STATES:
            if status == "rejected" and not e.get("constraint"):
                # Terminal, therefore resolved -- but the reason is unclassified.
                return "ok:terminal_rejected_unclassified"
            return f"ok:terminal_{status}"
        return "fail:unresolved_status"

    constraint = e.get("constraint") or ""
    if constraint == EOD_STOP_NOT_EXECUTABLE:
        # Valid only on an EOD administrative row with the venue's market-
        # closed evidence (r4_notes rev. 2 §4.3), which classify_admin_execution
        # checks. Reached here, the tag stands on a row that cannot carry it,
        # so it explains nothing: unclassified, a failure.
        return "fail:unclassified_skip"
    if constraint in _LEGIT_CONSTRAINTS:
        return f"ok:constraint_{constraint}"
    if constraint == CONSTRAINT_WASH_TRADE_BLOCK:
        return "fail:wash_trade_unmediated"

    order_id = str(e.get("order_id") or "")
    synthetic = order_id.startswith("EXEC_CONSTRAINT_")
    if e.get("side") == "HOLD":
        return NO_ORDER
    if e.get("side") == "SKIP" and (not order_id or synthetic):
        # Nothing was sent and no registered class says why.
        return "fail:unclassified_skip"
    if not order_id or synthetic:
        return NO_ORDER
    # An order id was minted, no venue order came back, no constraint explains
    # it: the submission itself failed.
    return "fail:submission_failed"


def market_closed_evidence(row: dict[str, Any], e: dict[str, Any]) -> bool:
    """The venue's own evidence that the market was closed (r4_notes rev. 2
    §4.3): its clock as read by the EOD run, or its rejection reason."""
    if (row.get("venue_clock") or {}).get("is_open") is False:
        return True
    err = str(e.get("error") or "").lower()
    return "market is closed" in err or "market closed" in err


def classify_admin_execution(e: dict[str, Any], row: dict[str, Any]) -> str:
    """One execution on an ADMINISTRATIVE row (r4_notes rev. 2 §4).

    A terminal state comes before the tag: an order not terminal, or one the
    EOD run could not confirm, fails as unresolved_status whatever it carries
    (case C). The tag is honoured only on an EOD_POSITION_STOP row with the
    venue's market-closed evidence. Case A (refused) scores as a market
    rejection; case B (accepted for the open, cancelled at our deadline) as a
    resolved terminal state; case D (filled) as an ordinary fill.
    """
    bo = e.get("broker_order") or {}
    status = str(bo.get("status") or "").lower()
    if e.get("eod_open_order") or (bo and status not in TERMINAL_STATES):
        return "fail:unresolved_status"
    if e.get("constraint") == EOD_STOP_NOT_EXECUTABLE:
        if (row.get("admin_class") != ADMIN_CLASS_EOD_POSITION_STOP
                or not market_closed_evidence(row, e)):
            return "fail:unclassified_skip"
        if bo and status != "rejected":
            return f"ok:terminal_{status}"                       # case B
        return f"ok:constraint_{EOD_STOP_NOT_EXECUTABLE}"        # case A
    return classify_execution(e)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                # A torn line must not silently shrink the denominator.
                logger.error("%s line %d is not valid JSON: %s", path, n, exc)
                raise
    return rows


def load_reconciliation(recon_dir: Path | None = None) -> dict[str, bool]:
    """cycle_id -> clean?, across every month file. A cycle with more than one
    record (a rerun) is clean only if ALL of them are."""
    recon_dir = recon_dir or RECONCILIATION_DIR
    clean: dict[str, bool] = {}
    if not recon_dir.exists():
        return clean
    for path in sorted(recon_dir.glob("*.jsonl")):
        for rec in _read_jsonl(path):
            cid = rec.get("cycle_id")
            if not cid:
                continue
            clean[cid] = clean.get(cid, True) and bool(rec.get("clean"))
    return clean


def score_cycles(records: Iterable[dict[str, Any]],
                 recon_clean: dict[str, bool]) -> dict[str, Any]:
    """Score one model's decision records (already restricted to one month).

    Administrative rows (r4_notes_gate_a_and_repair.md rev. 2 §4.1): an EOD
    pass whose stop sweep intended an order is one execution cycle of the book.
    It is admitted by record_class AHEAD of the api_success test (its
    api_success is null by construction) and also counted separately in
    `administrative_cycles`, by outcome, so the rate reads with and without
    them (`execution_success_rate` / `rate_excluding_administrative`).
    """
    cycles = successes = excluded = 0
    admin_cycles = admin_successes = 0
    admin_outcomes: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    constraints: Counter[str] = Counter()
    unclassified_rejections = 0
    for rec in records:
        if rec.get("execution_mode") not in BROKER_MODES:
            continue
        admin = rec.get("record_class") == RECORD_CLASS_ADMINISTRATIVE
        if not admin and not rec.get("api_success", True):
            excluded += 1
            continue
        cycles += 1
        cycle_fail: list[str] = []
        for e in rec.get("executions") or []:
            outcome = classify_admin_execution(e, rec) if admin else classify_execution(e)
            if outcome == NO_ORDER:
                continue
            if outcome.startswith("fail:"):
                cycle_fail.append(outcome[5:])
            elif e.get("constraint"):
                constraints[e["constraint"]] += 1
            if outcome == "ok:terminal_rejected_unclassified":
                unclassified_rejections += 1
        cid = rec.get("cycle_id") or ""
        if not cid or cid not in recon_clean:
            cycle_fail.append("reconciliation_missing")
        elif not recon_clean[cid]:
            cycle_fail.append("reconciliation_diverged")
        if cycle_fail:
            for reason in set(cycle_fail):
                failures[reason] += 1
        else:
            successes += 1
        if admin:
            admin_cycles += 1
            admin_successes += 0 if cycle_fail else 1
            admin_outcomes["success" if not cycle_fail else
                           "fail:" + ",".join(sorted(set(cycle_fail)))] += 1
    rate = (successes / cycles) if cycles else None
    decision_cycles = cycles - admin_cycles
    return {
        "cycles": cycles,
        "successes": successes,
        "execution_success_rate": rate,
        "rate_excluding_administrative": ((successes - admin_successes) / decision_cycles
                                          if decision_cycles else None),
        "administrative_cycles": {"cycles": admin_cycles, "successes": admin_successes,
                                  "by_outcome": dict(sorted(admin_outcomes.items()))},
        "passes_gate_4": (rate >= EXECUTION_SUCCESS_THRESHOLD) if rate is not None else None,
        "threshold": EXECUTION_SUCCESS_THRESHOLD,
        "failure_cycles_by_reason": dict(sorted(failures.items())),
        "constraint_events": dict(sorted(constraints.items())),
        "unclassified_terminal_rejections": unclassified_rejections,
        "excluded_no_decision": excluded,
    }


def compute_execution_metrics(month: str, models: list[str],
                              trades_dir: Path | None = None,
                              recon_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Per-model Gate 4 metrics for `month` (YYYY-MM).

    `rate is None` (no broker-mode cycles) is reported as such and is NOT a
    pass -- `passes_gate_4` is None, so a segment with no live-path evidence
    cannot read as having cleared the gate.
    """
    trades_dir = trades_dir or TRADES_DIR
    recon_clean = load_reconciliation(recon_dir)
    out: dict[str, dict[str, Any]] = {}
    for model in models:
        path = trades_dir / f"{model}_{month}.jsonl"
        records = _read_jsonl(path) if path.exists() else []
        out[model] = score_cycles(records, recon_clean)
        out[model]["month"] = month
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        sys.stderr.write("usage: python -m src.analytics.execution_metrics YYYY-MM model [model ...]\n")
        return 2
    result = compute_execution_metrics(argv[0], argv[1:])
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
