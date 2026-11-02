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

Positions are ATTRIBUTABLE, not exempt (hub ruling 2026-11-02 ②). Probe
activity is excluded from research estimands and never from integrity checks.
A probe position is real: it consumes buying power and carries market risk. So
level 2 still compares it, but against the probe's own recorded state, the same
shape as unallocated cash:

  * a position the probe's committed records account for (the sum of each
    FINISHED record's `position_delta`) is expected, joins the ledger side, and
    reconciles without halting;
  * a position the probe does not account for is unexplained and halts every
    book, exactly as any other divergence.

The probe also raises CRITICAL on its own stuck position the moment it sees
one, so it gets fixed in minutes rather than found by a halt. Its next
scheduled run closes the attributed position first and records the negative
delta.

Broker-paper only. Live probe records cannot exist (the probe refuses any
other endpoint), and neither function below ever counts one for `live`.
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


PROBE_MODE = "broker_paper"


def _finished(mode: str, probe_dir: Path | None) -> list[dict]:
    if mode != PROBE_MODE:
        return []
    return [r for r in probe_records(probe_dir)
            if r.get("phase") == "FINISHED" and r.get("mode") == PROBE_MODE]


def administrative_positions(mode: str, probe_dir: Path | None = None) -> dict[str, float]:
    """Net shares, by ticker, the committed probe runs account for as open.

    The sum of `position_delta` over FINISHED records. A run whose delta is
    unmeasured (None) contributes nothing: an unmeasured position is not an
    accounted one, so if it exists, level 2 reports it as unexplained.
    """
    totals: dict[str, float] = {}
    for r in _finished(mode, probe_dir):
        for ticker, q in (r.get("position_delta") or {}).items():
            totals[ticker] = totals.get(ticker, 0.0) + float(q)
    return {t: round(q, 9) for t, q in totals.items() if abs(q) > 1e-9}


def administrative_cash_usd(mode: str, probe_dir: Path | None = None) -> float:
    """Net cash the committed probe runs have left in the `mode` account.

    The sum of `residual_cash_usd` over FINISHED records for that mode. A run
    that died before FINISHED contributes nothing. If it traded, the cash it
    moved is unattributed, and level 2 is right to report it.
    """
    total = 0.0
    for r in _finished(mode, probe_dir):
        v = r.get("residual_cash_usd")
        if v is not None:
            total += float(v)
    return round(total, 6)
