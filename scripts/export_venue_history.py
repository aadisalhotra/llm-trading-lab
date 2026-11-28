"""Export the venue paper account's full history, read-only, before any account change.

Hub 2026-11-21 (cohort refresh, venue clearing): "BEFORE any reset/account
change export full order/fill/account-activity history and commit it." The
handover of 2026-12-05/06 moves the venue path to a new paper account; this is
the record of the old one, committed before the switch (Batch 4 item (e)).

What it writes, under --out (default data/venue_history/<label>/):
  orders.jsonl          every order, any status, nested legs flattened, one per
                        line, sorted by (submitted_at, id); keyed by order id
  activities.jsonl      every account activity (fills, fees, dividends, cash
                        journals, ...), sorted by (date/transaction_time, id)
  positions.json        the open positions at export time
  account.json          the account object, identifiers removed
  configurations.json   the account configuration (margin, shorting, ...)
  portfolio_history.json  daily equity history (period=all, timeframe=1D)
  MANIFEST.json         counts, sha256 of each file, the cross-checks below

Read-only: GET requests only, on the endpoint the committed mode binds
(BrokerClient refuses anything else). Never places, cancels or changes.

The repo is public. The account number and account id are dropped from every
file (they are never written), and before anything is written every output is
checked in process for the account number and for each credential value in
the environment; a hit stops the export with nothing written. Nothing secret
is printed.

Cross-checks recorded in the manifest (they do not stop the export; a
mismatch is a finding to report):
  * every FILL/PARTIAL_FILL activity names an order that is in orders.jsonl;
  * every order with filled_qty > 0 has at least one fill activity;
  * per order, the summed fill-activity qty equals the order's filled_qty.

Usage:
  python scripts/export_venue_history.py --label 2026-12-04_close
  python scripts/export_venue_history.py --label dryrun --venue test --out <dir>
    (--venue test reads the separate TEST paper account, ALPACA_TEST_*.)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config_loader import force_utf8_console, load_env, load_settings  # noqa: E402

logger = logging.getLogger("llmlab.export_venue_history")

ORDER_PAGE = 500
ACTIVITY_PAGE = 100
MAX_PAGES = 2000
# Fields that identify the account. Dropped wherever they appear.
IDENTIFYING_KEYS = frozenset({"account_number", "account_id", "id_account"})
FILL_TYPES = frozenset({"FILL", "PARTIAL_FILL"})


class ExportError(RuntimeError):
    """The venue could not be read completely; nothing is written."""


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def flatten_orders(orders: Iterable[dict]) -> list[dict]:
    """Orders with their nested legs (bracket/OCO children) as rows of their own."""
    out: list[dict] = []
    for o in orders:
        legs = o.get("legs") or []
        out.append({k: v for k, v in o.items() if k != "legs"} | {"leg_ids": [g["id"] for g in legs]})
        for g in legs:
            out.extend(flatten_orders([g | {"parent_order_id": o["id"]}]))
    return out


def fetch_orders(request, since: datetime) -> list[dict]:
    """Every order submitted since `since`, any status.

    Paginates ascending on submitted_at. The cursor steps back one second from
    the last order seen and orders are deduplicated by id, so two orders that
    share a timestamp across a page boundary are both kept (a strict `after`
    cursor would drop the second). A full page that adds nothing new cannot be
    paginated and stops the export.
    """
    seen: dict[str, dict] = {}
    cursor = since
    for _ in range(MAX_PAGES):
        page = request("GET", "/v2/orders", params={
            "status": "all", "after": cursor.isoformat(), "limit": ORDER_PAGE,
            "direction": "asc", "nested": "true"})
        if not isinstance(page, list):
            raise ExportError(f"order listing returned {type(page).__name__}")
        new = [o for o in page if o["id"] not in seen]
        for o in page:
            seen[o["id"]] = o
        if len(page) < ORDER_PAGE:
            return list(seen.values())
        if not new:
            raise ExportError("a full order page added no new order; cannot paginate")
        last = max((_ts(o.get("submitted_at")) for o in page if _ts(o.get("submitted_at"))), default=None)
        if last is None:
            raise ExportError("order page without submitted_at; cannot paginate")
        cursor = last - timedelta(seconds=1)
    raise ExportError(f"more than {MAX_PAGES} order pages")


def fetch_activities(request) -> list[dict]:
    """Every account activity, ascending, paginated by the last activity id."""
    seen: dict[str, dict] = {}
    token: str | None = None
    for _ in range(MAX_PAGES):
        params: dict[str, Any] = {"direction": "asc", "page_size": ACTIVITY_PAGE}
        if token:
            params["page_token"] = token
        page = request("GET", "/v2/account/activities", params=params)
        if not isinstance(page, list):
            raise ExportError(f"activity listing returned {type(page).__name__}")
        for a in page:
            seen[a["id"]] = a
        if len(page) < ACTIVITY_PAGE:
            return list(seen.values())
        if page[-1]["id"] == token:
            raise ExportError("activity pagination did not advance")
        token = page[-1]["id"]
    raise ExportError(f"more than {MAX_PAGES} activity pages")


REDACTED = "[account]"


def scrub(obj: Any, redact: tuple[str, ...] = ()) -> Any:
    """Drop identifying keys at any depth, and replace each `redact` value
    inside strings (the venue writes the account number into the free-text
    description of FEE activities)."""
    if isinstance(obj, dict):
        return {k: scrub(v, redact) for k, v in obj.items() if k not in IDENTIFYING_KEYS}
    if isinstance(obj, list):
        return [scrub(v, redact) for v in obj]
    if isinstance(obj, str):
        for value in redact:
            if value:
                obj = obj.replace(value, REDACTED)
    return obj


def cross_check(orders: list[dict], activities: list[dict]) -> dict[str, Any]:
    by_id = {o["id"]: o for o in orders}
    fills = [a for a in activities if a.get("activity_type") == "FILL"
             or a.get("type") in FILL_TYPES]
    filled_by_order: dict[str, Decimal] = {}
    orphan_fills = []
    for a in fills:
        oid = a.get("order_id")
        if oid not in by_id:
            orphan_fills.append(a.get("id"))
            continue
        filled_by_order[oid] = filled_by_order.get(oid, Decimal(0)) + Decimal(str(a.get("qty") or 0))
    unfilled_orders, qty_mismatch = [], []
    for o in orders:
        fq = Decimal(str(o.get("filled_qty") or 0))
        if fq > 0 and o["id"] not in filled_by_order:
            unfilled_orders.append(o["id"])
        elif fq > 0 and abs(filled_by_order[o["id"]] - fq) > Decimal("1e-9"):
            qty_mismatch.append({"order_id": o["id"], "order_filled_qty": str(fq),
                                 "fill_activity_qty": str(filled_by_order[o["id"]])})
    return {"fill_activities": len(fills),
            "fills_naming_an_unknown_order": orphan_fills,
            "filled_orders_without_a_fill_activity": unfilled_orders,
            "fill_qty_mismatches": qty_mismatch,
            "consistent": not (orphan_fills or unfilled_orders or qty_mismatch)}


def _jsonl(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode("utf-8")


def _json(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")


def render(raw: dict[str, Any], *, label: str, mode: str, venue: str, captured_at: str) -> dict[str, bytes]:
    """The export's files as bytes, identifiers removed. Pure; testable."""
    ids = tuple(v for v in (str(raw["account"].get("account_number") or ""),
                            str(raw["account"].get("id") or "")) if len(v) >= 4)
    redactions = {"n": 0}

    def clean(obj: Any) -> Any:
        before = json.dumps(obj, sort_keys=True)
        out = scrub(obj, ids)
        redactions["n"] += sum(before.count(v) for v in ids) - sum(
            json.dumps(out, sort_keys=True).count(v) for v in ids)
        return out
    orders = sorted(flatten_orders(raw["orders"]),
                    key=lambda o: (o.get("submitted_at") or "", o["id"]))
    acts = sorted(raw["activities"],
                  key=lambda a: (a.get("transaction_time") or a.get("date") or "", a["id"]))
    files = {
        "orders.jsonl": _jsonl(clean(orders)),
        "activities.jsonl": _jsonl(clean(acts)),
        "positions.json": _json(clean(raw["positions"])),
        "account.json": _json(clean({k: v for k, v in raw["account"].items() if k != "id"})),
        "configurations.json": _json(clean(raw["configurations"])),
        "portfolio_history.json": _json(clean(raw["portfolio_history"])),
    }
    manifest = {
        "label": label, "captured_at": captured_at, "mode": mode, "venue": venue,
        "read_only": True,
        "counts": {"orders": len(orders), "orders_top_level": len(raw["orders"]),
                   "activities": len(acts), "positions": len(raw["positions"])},
        "activity_types": dict(sorted(_count(a.get("activity_type") for a in acts).items())),
        "order_statuses": dict(sorted(_count(o.get("status") for o in orders).items())),
        "first_order_submitted_at": orders[0].get("submitted_at") if orders else None,
        "last_order_submitted_at": orders[-1].get("submitted_at") if orders else None,
        "cross_checks": cross_check(orders, acts),
        "identifiers_removed": sorted(IDENTIFYING_KEYS | {"account.id"}),
        "identifier_occurrences_redacted_in_text": redactions["n"],
        "sha256": {name: hashlib.sha256(b).hexdigest() for name, b in sorted(files.items())},
        "tool": "scripts/export_venue_history.py",
    }
    files["MANIFEST.json"] = _json(manifest)
    return files


def _count(values: Iterable[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return out


def assert_no_secret(files: dict[str, bytes], secrets: Iterable[str]) -> None:
    """Refuse to write if any secret value appears in any file. Prints nothing
    about the value: only the file name."""
    for value in secrets:
        if not value or len(value) < 4:
            continue
        needle = value.encode("utf-8")
        for name, data in files.items():
            if needle in data:
                raise ExportError(f"{name} contains an identifier or credential value; nothing written")


def _test_client():
    from src.execution.broker import BrokerClient
    tk, ts = os.environ["ALPACA_TEST_API_KEY_ID"], os.environ["ALPACA_TEST_API_SECRET_KEY"]
    saved = {k: os.environ.get(k) for k in ("ALPACA_PAPER_KEY", "ALPACA_PAPER_SECRET",
                                            "ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
    try:
        os.environ["ALPACA_PAPER_KEY"], os.environ["ALPACA_PAPER_SECRET"] = tk, ts
        return BrokerClient("broker_paper")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    force_utf8_console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--label", required=True, help="folder name, e.g. 2026-12-04_close")
    ap.add_argument("--out", help="output folder (default data/venue_history/<label>)")
    ap.add_argument("--venue", choices=("validation", "test"), default="validation")
    ap.add_argument("--since", default="2026-08-01T00:00:00+00:00",
                    help="earliest order submission to list (before the account's first order)")
    args = ap.parse_args(argv)

    load_env()
    settings = load_settings()
    mode = settings["mode"]
    if mode != "broker_paper":
        logger.error("mode is %r; this export reads the broker_paper account only", mode)
        return 2
    from src.execution.broker import BrokerClient
    client = _test_client() if args.venue == "test" else BrokerClient("broker_paper")
    out = Path(args.out) if args.out else ROOT / "data" / "venue_history" / args.label
    if out.exists() and any(out.iterdir()):
        logger.error("%s already holds files; an export is never overwritten", out)
        return 2

    def get(method: str, path: str, params: dict | None = None):
        assert method == "GET", "read-only export"
        return client._request(method, path, params=params, timeout=60)

    try:
        account = get("GET", "/v2/account")
        raw = {
            "account": account,
            "configurations": get("GET", "/v2/account/configurations"),
            "positions": get("GET", "/v2/positions"),
            "orders": fetch_orders(get, datetime.fromisoformat(args.since)),
            "activities": fetch_activities(get),
            "portfolio_history": get("GET", "/v2/account/portfolio/history",
                                     params={"period": "all", "timeframe": "1D"}),
        }
        files = render(raw, label=args.label, mode=mode, venue=args.venue,
                       captured_at=datetime.now(timezone.utc).isoformat())
        secrets = [str(account.get("account_number") or ""), str(account.get("id") or "")]
        secrets += [v for k, v in os.environ.items()
                    if k.startswith(("ALPACA_", "APCA_")) and v and "://" not in v]
        assert_no_secret(files, secrets)
    except Exception as e:  # noqa: BLE001 - reported; nothing written
        logger.error("export failed, nothing written: %s: %s", type(e).__name__, e)
        return 2

    out.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out / name).write_bytes(data)
    m = json.loads(files["MANIFEST.json"])
    logger.info("exported to %s: %d orders, %d activities, %d positions; cross-checks consistent=%s",
                out, m["counts"]["orders"], m["counts"]["activities"], m["counts"]["positions"],
                m["cross_checks"]["consistent"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
