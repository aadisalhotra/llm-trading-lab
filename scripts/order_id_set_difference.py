"""Offline order-id set difference — the nightly instrument.

tick_admission_guards_ruling.md rev. 5 §3 condition 2 (the offline replay of
(V)'s primary test) and §3 "Gate B": if (V) is not enforcing, this runs after
every session's close and is the only instrument that sees the fourth route's
post-save half, as a DECISION_RECORD_LOST finding the next morning.

Every venue order since the 2026-11-06 cutover is classified by its
client_order_id segment:

  * a BOOK order is accounted when its cycle_id appears in THAT book's
    committed decision log on origin/main (administrative rows included), or in
    a ledgered repair (data/admission/repairs.jsonl with a ledger_entry);
  * a PROBE order (FORCED_PROBE_*) is accounted when its cycle has a committed
    record in data/barrier_probe/ — the 2026-11-18 10:38 run (cycle
    20261118T153801Z) is the case: probe orders with no committed record;
  * anything else is an unknown segment and is listed.

Expected: zero unaccounted book orders, zero unrecorded probe cycles beyond
those already ledgered, zero unknown segments, zero book orders at/after the
close.

READ-ONLY: GET /v2/orders on the paper endpoint (bound by the committed mode).
Writes nothing; the report goes to stdout. Exit 0 when clean, 1 when anything
is unaccounted, 2 when the venue cannot be read.

Usage: python scripts/order_id_set_difference.py [REPO_ROOT]
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config_loader import load_env, load_settings  # noqa: E402
from src.execution.administrative import PROBE_BOOK_PREFIX  # noqa: E402
from src.execution.broker import BrokerClient  # noqa: E402

SINCE = datetime(2026, 11, 6, 14, 30, tzinfo=timezone.utc)
EASTERN = ZoneInfo("America/New_York")
# Probe cycles whose missing record is already ledgered. Listed, not failed.
LEDGERED_LOST_PROBE_RECORDS = {
    "20261118T153801Z": "barrier_probe_record_lost_2026_11_18 (run 44337543855)",
}


def git_show(path: str) -> str | None:
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"origin/main:{path}"],
                       capture_output=True)
    return r.stdout.decode("utf-8") if r.returncode == 0 else None


def months_since(start: datetime, end: datetime) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def committed_cycles(book: str, months: list[str]) -> set[str]:
    cycles: set[str] = set()
    for m in months:
        raw = git_show(f"data/trades/{book}_{m}.jsonl")
        for line in (raw or "").splitlines():
            if line.strip():
                c = json.loads(line).get("cycle_id")
                if c:
                    cycles.add(c)
    raw = git_show("data/admission/repairs.jsonl")
    for line in (raw or "").splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("book") == book and r.get("ledger_entry") and r.get("cycle_id"):
                cycles.add(r["cycle_id"])
    return cycles


def probe_record_cycles(months: list[str]) -> set[str]:
    cycles: set[str] = set()
    for m in months:
        for line in (git_show(f"data/barrier_probe/{m}.jsonl") or "").splitlines():
            if line.strip():
                cycles.add(json.loads(line).get("cycle_id"))
    return cycles


def fetch_orders(client: BrokerClient) -> list[dict]:
    orders: dict[str, dict] = {}
    cursor = SINCE
    while True:
        page = client._request("GET", "/v2/orders", params={
            "status": "all", "after": cursor.isoformat(), "limit": 500,
            "direction": "asc", "nested": "false"})
        for o in page:
            orders[o["id"]] = o
        if len(page) < 500:
            return list(orders.values())
        cursor = datetime.fromisoformat(page[-1]["submitted_at"].replace("Z", "+00:00"))


def after_close(submitted_at: str) -> bool:
    t = datetime.fromisoformat(submitted_at.replace("Z", "+00:00")).astimezone(EASTERN)
    return (t.hour, t.minute) >= (16, 0) or (t.hour, t.minute) < (9, 30)


def main() -> int:
    load_env()
    settings = load_settings()
    if settings["mode"] != "broker_paper":
        print(f"mode is {settings['mode']!r}; this instrument reads the paper account only")
        return 2
    client = BrokerClient("broker_paper")
    if "paper-api" not in client.base_url:
        return 2
    try:
        orders = fetch_orders(client)
    except Exception as e:  # noqa: BLE001 — reported, exit 2
        print(f"VENUE UNREADABLE: {type(e).__name__}: {e}")
        return 2

    months = months_since(SINCE, datetime.now(timezone.utc))
    books = list(settings["models"])
    cycles = {b: committed_cycles(b, months) for b in books}
    probe_records = probe_record_cycles(months)
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "origin/main"],
                          capture_output=True, text=True).stdout.strip()

    book_orders, probe, unknown, unaccounted = [], [], [], []
    for o in orders:
        coid = o.get("client_order_id") or ""
        parts = coid.split("-")
        seg, cyc = (parts[0], parts[1]) if len(parts) == 4 else ("", "")
        if seg.startswith(PROBE_BOOK_PREFIX):
            probe.append((cyc, o))
        elif seg in cycles:
            book_orders.append(o)
            if cyc not in cycles[seg]:
                unaccounted.append(o)
        else:
            unknown.append(o)

    probe_cycles = {c for c, _ in probe}
    unrecorded = sorted(probe_cycles - probe_records)
    unrecorded_new = [c for c in unrecorded if c not in LEDGERED_LOST_PROBE_RECORDS]
    post_close = [o for o in book_orders if after_close(o["submitted_at"])]
    book_cycles = {tuple(o["client_order_id"].split("-")[:2]) for o in book_orders}
    last = max((o["submitted_at"] for o in orders), default=None)

    print(f"origin/main = {head}; venue orders since {SINCE.isoformat()}: {len(orders)} "
          f"(last submitted {last})")
    print(f"book orders: {len(book_orders)} across {len(book_cycles)} book-cycles; by book "
          f"{dict(Counter(o['client_order_id'].split('-')[0] for o in book_orders))}")
    print(f"book orders by status: {dict(Counter(o['status'] for o in book_orders))}")
    print(f"book orders at/after the close (ET): {len(post_close)}")
    print(f"UNACCOUNTED book orders: {len(unaccounted)}")
    for o in unaccounted:
        print("  ", o["client_order_id"], o["symbol"], o["side"], o["status"], o["submitted_at"])
    print(f"probe orders: {len(probe)} across {len(probe_cycles)} cycles; cycles with no "
          f"committed probe record: {len(unrecorded)}")
    for c in unrecorded:
        ids = [o["client_order_id"] for cc, o in probe if cc == c]
        print("  ", c, LEDGERED_LOST_PROBE_RECORDS.get(c, "NOT LEDGERED"), ids)
    print(f"unknown segment: {len(unknown)}")
    for o in unknown:
        print("  ", o.get("client_order_id"), o["symbol"], o["side"], o["status"], o["submitted_at"])
    return 1 if (unaccounted or unrecorded_new or unknown or post_close) else 0


if __name__ == "__main__":
    raise SystemExit(main())
