"""Intended-vs-expressed deployment: a standing per-book, per-session descriptor.

Registered by docs/registrations/executor_sizing_floor_ruling.md section 5.4, as
the generic detector for silent suppression of any kind. It is what caught the
one-share floor, when Gate 4 and BELOW_VENUE_MINIMUM both read clean, and it
costs one subtraction: what the decisions asked for, minus what reached the book.

    intended_usd   sum of each decision's `intended_notional`, before any cap
    expressed_usd  sum of `notional` on the orders that executed
    gap_usd        intended_usd - expressed_usd

Both sides are repeat-counting by construction: a decision re-issued in five
cycles intends five times. That is what makes the gap a rate of suppression and
not a statement about any one position, so it is never read as a dollar shortfall
in the book.

Forced orders (stops, boundary administration) are left out of both sides. They
were not decisions, and an unintended fill must not hide an intended miss.

Decisions logged before the field existed have no `intended_notional`. Those
fall back to target_weight x NAV, which is exact for a first purchase and an upper
bound on an add, and the row says so in `intended_source`. In practice only
2026-11-06, the session that needed this, is affected.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from ..config_loader import DATA_DIR, TRADES_DIR

logger = logging.getLogger("llmlab.analytics.intended_vs_expressed")

DESCRIPTOR_DIR = DATA_DIR / "execution_descriptors"
TRADE_SIDES = frozenset({"BUY", "SELL", "SHORT", "COVER"})


def _intended(e: dict[str, Any], nav: float) -> tuple[float, bool]:
    """(dollars intended, whether the value is the logged field)."""
    logged = e.get("intended_notional")
    if logged is not None:
        return float(logged), True
    d = e.get("decision") or {}
    if str(d.get("action", "")).upper() == "HOLD":
        return 0.0, False
    return float(d.get("target_weight") or 0.0) * nav, False


def session_row(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """One book's descriptor for one session, from its decision records."""
    intended = expressed = 0.0
    orders_intended = orders_expressed = 0
    any_derived = False
    skips: Counter[str] = Counter()
    nav = 0.0
    n = 0
    for rec in records:
        n += 1
        nav = float((rec.get("portfolio_after") or {}).get("total_value") or nav)
        for e in rec.get("executions") or []:
            if str(e.get("order_id") or "").startswith("FORCED_"):
                continue
            usd, logged = _intended(e, nav)
            if e.get("side") == "HOLD":
                # An at-target decision intends no order; a HOLD carries none.
                continue
            any_derived = any_derived or not logged
            intended += usd
            if usd > 0:
                orders_intended += 1
            if e.get("executed") and e.get("side") in TRADE_SIDES:
                expressed += float(e.get("notional") or 0.0)
                orders_expressed += 1
            if e.get("side") == "SKIP":
                skips[e.get("constraint") or "UNTAGGED"] += 1
    gap = intended - expressed
    return {
        "cycles": n,
        "nav_close": nav,
        "orders_intended": orders_intended,
        "orders_expressed": orders_expressed,
        "intended_usd": round(intended, 2),
        "expressed_usd": round(expressed, 2),
        "gap_usd": round(gap, 2),
        "expressed_pct_of_intended": round(expressed / intended, 4) if intended else None,
        "skips_by_constraint": dict(sorted(skips.items())),
        "intended_source": "target_weight_x_nav" if any_derived else "logged",
    }


def _book_records(book: str, date: str, trades_dir: Path) -> list[dict[str, Any]]:
    path = trades_dir / f"{book}_{date[:7]}.jsonl"
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                # A torn line must not silently shrink either side of the gap.
                raise ValueError(f"{path.name}:{n} is not valid JSON: {exc}") from exc
            if rec.get("date") == date:
                out.append(rec)
    return out


def write_session_descriptor(date: str, books: list[str],
                             trades_dir: Path | None = None,
                             out_dir: Path | None = None) -> Path:
    """Upsert `date` in the month's descriptor file. Rewrites only that date, so
    a later cycle of the session replaces the earlier row and no other session
    moves."""
    trades_dir = trades_dir or TRADES_DIR
    out_dir = out_dir or DESCRIPTOR_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"intended_vs_expressed_{date[:7]}.json"
    month: dict[str, Any] = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            month = json.load(f)
    month[date] = {b: session_row(_book_records(b, date, trades_dir)) for b in sorted(books)}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dict(sorted(month.items())), f, indent=2, sort_keys=True)
        f.write("\n")
    return path
