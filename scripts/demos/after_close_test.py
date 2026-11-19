"""The after-close test: what the venue and our 300 s fill deadline do with a
fractional market SELL submitted after the close.

Governing text: eod_stop_record_and_evaluation_ruling.md §4 and §6 items 1-2,
tick_admission_guards_ruling.md rev. 5 §6 item 8, and the hub's amendment to
Part 4 I (adopted from Research, 2026-11-18): through `_place`, not a raw API
call; a fractional SELL of a held position; one SELL in each window, about
21:05Z, 22:00Z and 01:00Z; record the submit time, the venue's response, what
the deadline did, and each order's final state on Friday morning.

TEST paper account only, behind the same gate as the demonstrations
(scripts/demos/admission_demos.gate_test_account). Book segment DEMOAC.

Phases (each appends to docs/ops/after_close_test_2026-11-19/results.jsonl):
  buy             during Thursday's session: a small fractional position
  sell --window W after the close: one fractional SELL through Executor._place
                  (W = 1, 2 or 3), resolved by the real await_terminal
  final           Friday morning: every DEMOAC order's final state at the venue
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "demos"))

from admission_demos import gate_test_account  # noqa: E402
from src.config_loader import load_settings  # noqa: E402
from src.execution.executor import Executor  # noqa: E402

BOOK = "DEMOAC"
OUT = ROOT / "docs" / "ops" / "after_close_test_2026-11-19" / "results.jsonl"
BUY_NOTIONAL = 9.0
SELL_FRACTION = 0.25      # of the position held at the first SELL; each SELL ~$2


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record(row: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps(row, indent=2, sort_keys=True))


def latest_price(client, symbol: str) -> float:
    import requests
    q = requests.get(f"https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest",
                     headers=client.headers, timeout=20).json()
    return float(q["trade"]["p"])


SUBMITTED: list[dict] = []


def capture_submissions(client) -> None:
    """Record the venue's immediate response to each submission (this
    script's wrapper only; production code is untouched). A refusal raises
    inside submit and is recorded by _place as the placement error."""
    real = client.submit_market_order

    def wrapped(**kw):
        o = real(**kw)
        SUBMITTED.append({"at_utc": now(), "status": o.status, "submitted_at": o.submitted_at,
                          "client_order_id": o.client_order_id})
        return o
    client.submit_market_order = wrapped


def executor(client) -> Executor:
    settings = load_settings()
    settings["mode"] = "broker_paper"
    ex = Executor(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                  settings=settings, venue_books={BOOK})
    ex.broker = client
    return ex


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=("buy", "sell", "final"))
    ap.add_argument("--window", type=int, choices=(1, 2, 3))
    ap.add_argument("--symbol", default="BAC")
    a = ap.parse_args()
    client, acct = gate_test_account()
    capture_submissions(client)
    clock = client._request("GET", "/v2/clock")
    base = {"phase": a.phase, "account": f"...{acct}", "symbol": a.symbol, "utc": now(),
            "venue_clock": {k: clock.get(k) for k in ("is_open", "timestamp", "next_open",
                                                      "next_close")}}
    if a.phase == "buy":
        if not clock.get("is_open"):
            sys.exit("buy runs during the session; the market is closed")
        price = latest_price(client, a.symbol)
        ex = executor(client)
        pl = ex._place(BOOK, a.symbol, round(BUY_NOTIONAL / price, 4), "buy", price)
        record(dict(base, order_id=pl.order_id, moved=pl.moved, filled_qty=pl.filled_qty,
                    fill_price=pl.fill_price, status=pl.order.status if pl.order else None,
                    error=pl.error, constraint=pl.constraint))
        return 0 if pl.moved else 1
    if a.phase == "sell":
        if clock.get("is_open"):
            sys.exit("sell runs after the close; the market is open")
        held = sum(float(p["qty"]) for p in client.get_positions()
                   if p["symbol"] == a.symbol)
        prior = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines()
                 if l.strip()] if OUT.exists() else []
        bought = next((r["filled_qty"] for r in prior if r["phase"] == "buy" and r["moved"]), held)
        qty = round(bought * SELL_FRACTION, 4)
        price = latest_price(client, a.symbol)
        ex = executor(client)
        t0 = time.monotonic()
        submitted_utc = now()
        pl = ex._place(BOOK, a.symbol, qty, "sell", price)
        o = pl.order
        record(dict(base, window=a.window, held_before=held, requested_qty=qty,
                    reference_price=price, submitted_utc=submitted_utc,
                    seconds_in_place=round(time.monotonic() - t0, 1),
                    order_id=pl.order_id, moved=pl.moved, filled_qty=pl.filled_qty,
                    constraint=pl.constraint, error=pl.error,
                    venue_status_at_return=o.status if o else None,
                    venue_response_at_submit=SUBMITTED[-1] if SUBMITTED else None,
                    deadline_hit=pl.constraint == "UNFILLED_AT_DEADLINE",
                    broker_order=o.to_dict() if o else None))
        return 0
    # final: every DEMOAC order's state at the venue now
    orders = client._request("GET", "/v2/orders", params={
        "status": "all", "limit": 500, "after": "2026-11-19T00:00:00Z", "direction": "asc"})
    mine = [o for o in orders if str(o.get("client_order_id", "")).startswith(BOOK + "-")]
    record(dict(base, orders=[{k: o.get(k) for k in (
        "client_order_id", "side", "qty", "status", "submitted_at", "filled_at",
        "filled_qty", "filled_avg_price", "canceled_at", "expired_at", "failed_at",
        "time_in_force", "updated_at")} for o in mine],
        positions=[{k: p.get(k) for k in ("symbol", "qty")} for p in client.get_positions()]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
