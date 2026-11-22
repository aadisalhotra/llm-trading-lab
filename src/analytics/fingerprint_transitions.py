"""Fingerprint transitions, per arm (hub H-OPS-19; fingerprint_rotation_ruling_2026-11-22.md).

A transition is a change in an identifier's ``system_fingerprint`` between two
consecutive rows that carry one, on the same leg, in timestamp order. A row
without a fingerprint is not a transition (ruling section 3.1). Identifiers are
kept apart: rows for a different configured model never form a transition.

Every transition is recorded in ``data/integrity/fingerprint_transitions.jsonl``
after each tick, for every arm (refreshed arms included, for the Go/No-Go
classification), and counted in the monthly data-integrity section. On a field
classified **stable**, a transition first seen in the current cycle is a
candidate identity event: the hub gets a same-day WARN. On a **rotating** or
not-yet-classified field it is logged only.

Everything here is derived from the decision rows, so the log can be rebuilt
from them at any time and the monthly count never depends on the log.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
TRADES_DIR = ROOT / "data" / "trades"
LOG_PATH = ROOT / "data" / "integrity" / "fingerprint_transitions.jsonl"

# The ruling's present classifications (section 4), by configured identifier.
# The refreshed arms are classified at the Go/No-Go from their December rows;
# until a hub ruling adds them here they read "unclassified" and are logged only.
CLASSIFICATION: dict[str, str] = {
    "grok-4.20-0309-reasoning": "rotating",
    "deepseek-v4-pro": "stable",
}
RULING = "fingerprint_rotation_ruling_2026-11-22.md"

# The row field that carries each leg's fingerprint.
LEGS: dict[str, str] = {
    "decision": "api_system_fingerprint",
    "screening": "screening_system_fingerprint",
}


def classification(model_id: str | None) -> str:
    return CLASSIFICATION.get(model_id or "", "unclassified")


def _prev_month(tag: str) -> str:
    y, m = int(tag[:4]), int(tag[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def load_rows(model_key: str, month_tags: Iterable[str]) -> list[dict[str, Any]]:
    """The arm's rows for the given months, in timestamp order. A malformed
    line is logged and skipped, never silently dropped."""
    rows: list[dict[str, Any]] = []
    for tag in month_tags:
        p = TRADES_DIR / f"{model_key}_{tag}.jsonl"
        if not p.exists():
            continue
        with open(p, "r", encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("fingerprint transitions: %s line %d is not JSON", p.name, n)
    rows.sort(key=lambda r: r.get("timestamp") or "")
    return rows


def transitions(model_key: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every transition in timestamp-ordered rows, per (configured model, leg)."""
    last: dict[tuple[str, str], dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for r in rows:
        mid = r.get("model_id_configured")
        for leg, field in LEGS.items():
            fp = r.get(field)
            if not fp:
                continue                          # not a transition (ruling 3.1)
            prev = last.get((mid, leg))
            if prev is not None and prev[field] != fp:
                out.append({
                    "model_key": model_key, "model_id": mid, "leg": leg,
                    "classification": classification(mid),
                    "from": prev[field], "to": fp,
                    "last_row": prev.get("timestamp"), "last_cycle": prev.get("cycle_id"),
                    "first_row": r.get("timestamp"), "first_cycle": r.get("cycle_id"),
                })
            last[(mid, leg)] = r
    return out


def month_summary(model_keys: Iterable[str], win_start: str, win_end: str) -> dict[str, Any]:
    """The monthly data-integrity count: per arm, identifier and leg, the
    transitions whose first row falls in [win_start, win_end]. The previous
    month's rows seed the comparison, so a change across the month boundary is
    counted in the month it appears."""
    tag = win_start[:7]
    per_arm: dict[str, Any] = {}
    for mk in model_keys:
        rows = load_rows(mk, (_prev_month(tag), tag))
        trans = [t for t in transitions(mk, rows)
                 if win_start <= (t["first_row"] or "")[:10] <= win_end]
        groups: dict[str, Any] = {}
        for r in rows:
            d = (r.get("timestamp") or "")[:10]
            if not (win_start <= d <= win_end):
                continue
            for leg, field in LEGS.items():
                if r.get(field):
                    g = groups.setdefault(f"{r.get('model_id_configured')}|{leg}", {
                        "model_id": r.get("model_id_configured"), "leg": leg,
                        "classification": classification(r.get("model_id_configured")),
                        "rows_with_fingerprint": 0, "values": set(), "sessions": set()})
                    g["rows_with_fingerprint"] += 1
                    g["values"].add(r[field])
                    g["sessions"].add(d)
        if not groups:
            continue
        per_arm[mk] = []
        for key in sorted(groups):
            g = groups[key]
            mine = [t for t in trans if f"{t['model_id']}|{t['leg']}" == key]
            per_arm[mk].append({
                "model_id": g["model_id"], "leg": g["leg"],
                "classification": g["classification"],
                "rows_with_fingerprint": g["rows_with_fingerprint"],
                "sessions": len(g["sessions"]),
                "distinct_values": len(g["values"]),
                "transitions": len(mine),
                "list": [{k: t[k] for k in ("from", "to", "last_row", "first_row")} for t in mine],
            })
    return {
        "per_arm": per_arm,
        "rule": (f"{RULING} section 3: a transition is a change between consecutive rows "
                 "carrying a fingerprint, per identifier and leg; a row without one is not a "
                 "transition. On a stable field a transition is a candidate identity event; on "
                 "a rotating field it is counted here and is not by itself an event. Arms that "
                 "return no fingerprint are omitted."),
    }


def record_transitions(cycle_id: str, model_keys: Iterable[str], today: date,
                       alert: Callable[..., Any] | None = None,
                       log_path: Path = LOG_PATH) -> list[dict[str, Any]]:
    """Append every transition not yet in the log (this month and last, so the
    first run backfills them) and WARN on a stable field's transition first
    seen in this cycle. Returns the newly recorded transitions."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    have: set[tuple] = set()
    if log_path.exists():
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    t = json.loads(line)
                    have.add((t["model_key"], t["model_id"], t["leg"], t["first_row"]))
    tag = today.strftime("%Y-%m")
    new: list[dict[str, Any]] = []
    for mk in model_keys:
        for t in transitions(mk, load_rows(mk, (_prev_month(tag), tag))):
            k = (t["model_key"], t["model_id"], t["leg"], t["first_row"])
            if k in have:
                continue
            t["recorded_in_cycle"] = cycle_id
            new.append(t)
            have.add(k)
    if new:
        with open(log_path, "a", encoding="utf-8", newline="\n") as f:
            for t in new:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
    for t in new:
        logger.info("[%s] fingerprint transition (%s, %s leg, %s): %s -> %s",
                    t["model_key"], t["model_id"], t["leg"], t["classification"], t["from"], t["to"])
        if alert is not None and t["classification"] == "stable" and t["first_cycle"] == cycle_id:
            alert("WARN", f"Candidate identity event: {t['model_key']} fingerprint changed",
                  f"{t['model_id']}'s system_fingerprint is classified stable, so a transition "
                  f"is a candidate identity event ({RULING} section 3.2): ledger entry and a "
                  "profile reading against the control arm. A second one inside 15 sessions "
                  "reclassifies the field as rotating (section 3.4).",
                  {"model": t["model_key"], "model_id": t["model_id"], "leg": t["leg"],
                   "from": t["from"], "to": t["to"], "first_row": t["first_row"],
                   "cycle_id": cycle_id},
                  kind="fingerprint_transition_stable",
                  dedup_key=f"fingerprint_transition:{t['model_key']}:{t['leg']}:{t['first_row']}")
    return new
