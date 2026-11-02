"""Administrative venue orders: tags, and the residual cash they leave behind.

The barrier probe (scripts/barrier_probe.py) trades in the same Alpaca paper
account the validation month's books trade in. Its orders are administrative
and censored, under the same rule as FORCED_BOUNDARY_ADMIN: every one carries
a client_order_id beginning ``FORCED_``, which is the predicate
`analytics.research_metrics._is_administrative` censors. They never enter a
book, a decision log, or any estimand: the probe's books are in-memory
synthetics and the probe writes only to PROBE_DIR.

A probe run ends flat in its symbol, but not flat in cash. Buying and selling
back the same shares a few seconds apart leaves the spread, so the account's
cash moves by a few cents per run. No book owns that cash. By the registered
definition it is unallocated cash ("the cash the venue account holds that no
book owns", config/settings.json -> reconciliation._note), so level-2
reconciliation adds it to the unallocated term as a separate, attributable
component. It is not tolerance. Without it, enough runs would add up past the
$1.00 cash tolerance and raise a G-BOOK event, halting every book over orders
no book placed.

Positions are deliberately NOT carved out. A probe run that cannot get back to
flat leaves a venue position that no book claims. That is real account state,
and level 2 should see it and halt; it is not to be excused from a log line.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config_loader import DATA_DIR

logger = logging.getLogger("llmlab.execution.administrative")

# Every administrative order's client_order_id starts with this. The
# `FORCED_` stem is what research_metrics._is_administrative censors.
ADMIN_ORDER_PREFIX = "FORCED_"
PROBE_BOOK_PREFIX = "FORCED_PROBE_"

# Committed probe records, one JSONL file per month (same tagging convention
# as data/trades and data/reconciliation).
PROBE_DIR = DATA_DIR / "barrier_probe"


def is_administrative_order_id(client_order_id: str) -> bool:
    return str(client_order_id or "").startswith(ADMIN_ORDER_PREFIX)


def probe_records(probe_dir: Path | None = None) -> list[dict]:
    """Every record in every month file, in file order. A malformed line
    raises: the residual must not be computed from a log it cannot read."""
    probe_dir = probe_dir or PROBE_DIR
    out: list[dict] = []
    if not probe_dir.exists():
        return out
    for path in sorted(probe_dir.glob("*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise ValueError(f"{path.name}:{n} is not valid JSON: {e}") from e
    return out


def administrative_cash_usd(mode: str, probe_dir: Path | None = None) -> float:
    """Net cash the committed probe runs have left in the `mode` account.

    The sum of `residual_cash_usd` over FINISHED records for that mode. A run
    that died before FINISHED contributes nothing. If it traded, the cash it
    moved is unattributed, and level 2 is right to report it.
    """
    total = 0.0
    for r in probe_records(probe_dir):
        if r.get("phase") != "FINISHED" or r.get("mode") != mode:
            continue
        v = r.get("residual_cash_usd")
        if v is not None:
            total += float(v)
    return round(total, 6)
