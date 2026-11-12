"""Price validity at ingestion (docs/registrations/price_validity_and_source_ruling.md).

On 2026-11-11 the vendor served the day's daily bar with Close = NaN. A NaN
price passed every `price <= 0` check, reached the prompt as "$nan", froze
sizing for every book that held the name, disabled the position stops and the
drawdown halt (NaN compares False), and became seven false threshold alerts.

The ruling (§1a, §1b) puts the guard at ingestion, before the prompt:

  * A ticker without a finite price this cycle is NOT tradable. Its frame is
    replaced by an empty one, so every prompt block renders it through the
    existing "NO DATA" / "N/A" path -- the frozen v4 rendering for a missing
    frame, byte-identical for any cycle whose prices are all finite.
  * Every rejected price is a classified MISSING_PRICE event, per ticker per
    cycle, recorded as a data-availability descriptor. Never a silent skip.

A price is valid when it is a real number (any numbers.Real, not just float, so
an int or numpy scalar cannot cause a false rejection), finite, and positive.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from ..config_loader import DATA_DIR
# The predicates live in a standard-library-only module so the alerting path
# can use them without importing this package (pandas); re-exported here.
from ..price_checks import is_finite_number, is_valid_price  # noqa: F401

logger = logging.getLogger("llmlab.data.price_validity")

EVENT_MISSING_PRICE = "MISSING_PRICE"
REASON_NO_DATA = "no_data"          # empty frame, or the symbol was not returned
REASON_NON_FINITE = "non_finite"    # the last bar's Close is NaN / inf / <= 0

# Valuation marks for a HELD ticker that has no valid price this cycle. The book
# still needs a finite total value so its other orders size normally
# (containment); which mark stood in is part of the classified event.
MARK_LAST_VALID_BAR = "last_valid_bar"   # an earlier bar of the same frame
MARK_AVG_COST = "avg_cost"               # no valid bar at all in the frame

DESCRIPTOR_DIR = DATA_DIR / "data_availability"


@dataclass
class PriceIngestion:
    """One cycle's market data after the validity guard."""
    prices: dict[str, float]                       # valid prices only
    market_data: dict[str, pd.DataFrame]           # unpriced tickers -> empty frame
    missing: dict[str, str] = field(default_factory=dict)          # ticker -> reason
    last_valid_bar: dict[str, float] = field(default_factory=dict)  # unpriced ticker -> earlier valid close


def ingest_market_data(market_data: dict[str, pd.DataFrame]) -> PriceIngestion:
    """Apply the validity guard to a {symbol: frame} fetch.

    The price of a ticker is its LAST bar's Close. If that is invalid the ticker
    is unpriced for the cycle -- an earlier bar is never promoted to "the
    price" (it is kept only as a classified valuation mark for a held name).
    Interior bars with an invalid Close are dropped so no NaN reaches the
    prompt's percent-change columns; a frame with nothing to drop is passed
    through as the same object.
    """
    prices: dict[str, float] = {}
    clean: dict[str, pd.DataFrame] = {}
    missing: dict[str, str] = {}
    last_valid: dict[str, float] = {}
    for sym, df in market_data.items():
        if df is None or df.empty or "Close" not in df.columns:
            missing[sym] = REASON_NO_DATA
            clean[sym] = pd.DataFrame()
            continue
        closes = df["Close"]
        valid = closes.map(is_valid_price).astype(bool)
        if not bool(valid.iloc[-1]):
            missing[sym] = REASON_NON_FINITE
            clean[sym] = pd.DataFrame()
            if bool(valid.any()):
                last_valid[sym] = float(closes[valid].iloc[-1])
            continue
        prices[sym] = float(closes.iloc[-1])
        clean[sym] = df if bool(valid.all()) else df[valid]
    if missing:
        logger.warning("%s: %d ticker(s) without a valid price this cycle: %s",
                       EVENT_MISSING_PRICE, len(missing),
                       ", ".join(f"{t} ({r})" for t, r in sorted(missing.items())))
    return PriceIngestion(prices=prices, market_data=clean, missing=missing,
                          last_valid_bar=last_valid)


def holding_marks(holdings: dict[str, Any], ingestion: PriceIngestion) -> dict[str, tuple[float, str]]:
    """{ticker: (mark, mark_source)} for each held ticker with no valid price."""
    out: dict[str, tuple[float, str]] = {}
    for t, h in holdings.items():
        if t in ingestion.prices:
            continue
        if t in ingestion.last_valid_bar:
            out[t] = (ingestion.last_valid_bar[t], MARK_LAST_VALID_BAR)
        else:
            out[t] = (float(h.avg_cost), MARK_AVG_COST)
    return out


def missing_price_events(ingestion: PriceIngestion,
                         held_by: dict[str, list[str]],
                         marks_by_book: dict[str, dict[str, tuple[float, str]]]) -> list[dict[str, Any]]:
    """One MISSING_PRICE event per rejected ticker, with who held it and how it was marked."""
    events = []
    for t, reason in sorted(ingestion.missing.items()):
        holders = sorted(held_by.get(t, []))
        ev: dict[str, Any] = {"event": EVENT_MISSING_PRICE, "ticker": t, "reason": reason,
                              "held_by": holders}
        if holders:
            ev["valuation_mark"] = {b: {"mark": marks_by_book[b][t][0], "source": marks_by_book[b][t][1]}
                                    for b in holders if t in marks_by_book.get(b, {})}
        events.append(ev)
    return events


def record_data_availability(*, cycle_id: str, mode: str, run_date: datetime,
                             n_expected: int, events: list[dict[str, Any]]) -> None:
    """Append this cycle's data-availability descriptor (one line, every cycle).

    Written every cycle, including clean ones, so the count has a denominator.
    Best-effort: a write failure is logged, never raised into the cycle.
    """
    try:
        DESCRIPTOR_DIR.mkdir(parents=True, exist_ok=True)
        path = DESCRIPTOR_DIR / f"{run_date.strftime('%Y-%m')}.jsonl"
        row = {"cycle_id": cycle_id, "timestamp": run_date.isoformat(), "mode": mode,
               "n_expected": n_expected, "n_missing": len(events), "events": events}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:  # noqa: BLE001 — instrument must not break a cycle
        logger.exception("Data-availability descriptor write failed: %s", e)
