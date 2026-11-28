"""(V) — the pre-trade venue check, in SHADOW (logging only).

Governing text: docs/registrations/tick_admission_guards_ruling.md rev. 5, §2
(P1, the primary and secondary tests, venue unreachable, the withhold scope,
the open-order check) and §3 condition 3 (shadow before enforce). Admission
class: it runs before a book's first possible order in the tick — ahead of the
pre-prompt stop sweep and of the fetch-start stamp — and its only outputs are
*proceed* and *would withhold*. In shadow it enforces nothing: every book
proceeds exactly as it would without this module, and the verdicts are only
recorded. The flip to enforcing is a configuration change recorded in the
ledger, made on the hub's call once §3 condition 3's criteria hold.

The tests, per venue book:

  Primary — by order id. A venue order whose client_order_id carries this
  book's segment is accounted for when its cycle_id appears in THIS book's
  committed decision log (administrative rows count) or in a ledgered repair
  record for this book (data/admission/repairs.jsonl). Another book's row for
  the same cycle does not account for it. The query window opens at the book's
  last committed cycle and never advances past an unaccounted order: every
  unaccounted order this check has recorded stays in the window until a repair
  accounts for it, because the window start is the earlier of the last
  committed cycle and the oldest still-unaccounted cycle in the prior verdicts.

  Secondary — by quantity. `reconcile` from src/execution/reconciliation.py,
  called unchanged at the registered post-cycle bounds with the same probe
  terms. Never recorded as a G-BOOK event and never handled here: no halt flag.

  Venue unreachable — if any venue query fails, P1 cannot be evaluated and
  every venue book would be withheld (TICK_WITHHELD_VENUE_UNREACHABLE).

  Scope — the quantity test failing would withhold every venue book
  (TICK_WITHHELD_BOOKS_DIVERGED, naming any book the id test also failed);
  only the id test failing would withhold that book alone
  (BOOK_WITHHELD_UNACCOUNTED_ORDERS). Never a halt flag.

  Open-order check (rev. 5) — the venue's open-order count for the account is
  recorded at every check; nonzero is a CRITICAL. No withhold, in shadow or
  otherwise, until the hub rules on the shadow results.

Output: ONE verdict row per venue book per check, each carrying the count of
venue orders it examined (§3 condition 3(a): a shadow that did not run also
reports zero withholds, so completeness is checked by counting rows). Rows go
to data/admission/venue_check/{YYYY-MM}.jsonl — deliberately NOT the withhold
event file data/admission/{YYYY-MM}.jsonl, which the slot watcher reads as
"this slot was withheld". A shadow verdict is not a withhold.

Never raises into the pipeline: `run_shadow_check` catches everything, and a
failure of the check itself is recorded as a verdict row with
`check_error` set.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from ..config_loader import DATA_DIR, TRADES_DIR

logger = logging.getLogger("llmlab.venue_check")

VENUE_CHECK_DIRNAME = "venue_check"
REPAIRS_FILENAME = "repairs.jsonl"

TICK_WITHHELD_VENUE_UNREACHABLE = "TICK_WITHHELD_VENUE_UNREACHABLE"
BOOK_WITHHELD_UNACCOUNTED_ORDERS = "BOOK_WITHHELD_UNACCOUNTED_ORDERS"
TICK_WITHHELD_BOOKS_DIVERGED = "TICK_WITHHELD_BOOKS_DIVERGED"

PROCEED = "proceed"
WOULD_WITHHOLD = "would_withhold"

SCOPE_INTRADAY = "intraday"
SCOPE_EOD = "EOD"

# When a book has no committed cycle at all (a new book, or both months' logs
# absent) the window has no anchor. A week covers any gap the lab has had.
NO_ANCHOR_LOOKBACK = timedelta(days=7)
# Alpaca's `after` filters on submission time; a cycle's first order is
# submitted after its cycle_id stamp, so a minute of slack only widens.
WINDOW_SLACK = timedelta(minutes=1)
PAGE_LIMIT = 500

CYCLE_FORMAT = "%Y%m%dT%H%M%SZ"
EASTERN = ZoneInfo("America/New_York")


def admission_dir() -> Path:
    return DATA_DIR / "admission"


def venue_check_path(now_utc: datetime, directory: Path | None = None) -> Path:
    base = directory or (admission_dir() / VENUE_CHECK_DIRNAME)
    return base / f"{now_utc.astimezone(timezone.utc).strftime('%Y-%m')}.jsonl"


def cycle_of(client_order_id: str) -> tuple[str, str] | None:
    """(book, cycle_id) from `{book}-{cycle_id}-{seq:02d}-a{attempt}`, or None
    for an id not in that format."""
    parts = str(client_order_id or "").split("-")
    if len(parts) != 4 or not parts[3].startswith("a"):
        return None
    try:
        datetime.strptime(parts[1], CYCLE_FORMAT)
    except ValueError:
        return None
    return parts[0], parts[1]


def cycle_time(cycle_id: str) -> datetime:
    return datetime.strptime(cycle_id, CYCLE_FORMAT).replace(tzinfo=timezone.utc)


def _months(now_utc: datetime) -> list[str]:
    """This month and the previous one: the window may cross a month boundary
    (demonstration (vii))."""
    first = now_utc.astimezone(timezone.utc).replace(day=1)
    prev = (first - timedelta(days=1)).strftime("%Y-%m")
    return [prev, first.strftime("%Y-%m")]


@dataclass
class BookLog:
    cycles: set[str] = field(default_factory=set)
    last_cycle: str | None = None
    unparseable_lines: int = 0


def read_book_log(book: str, months: Iterable[str], trades_dir: Path | None = None) -> BookLog:
    """Every cycle_id in this book's committed decision log for `months`, any
    row kind (model-call or administrative). A torn line is counted, not fatal:
    the guards own torn-line policy; here it only means fewer accounted ids,
    which errs toward a would-withhold, never toward a silent proceed."""
    out = BookLog()
    base = trades_dir or TRADES_DIR
    for m in months:
        p = base / f"{book}_{m}.jsonl"
        if not p.exists():
            continue
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    c = json.loads(line).get("cycle_id")
                except (json.JSONDecodeError, AttributeError):
                    out.unparseable_lines += 1
                    continue
                if c:
                    out.cycles.add(str(c))
    out.last_cycle = max(out.cycles) if out.cycles else None
    return out


def read_repairs(book: str, path: Path | None = None) -> set[str]:
    """cycle_ids a ledgered repair record accounts for, for this book. A repair
    row: {"book", "cycle_id", "order_ids", "ledger_entry", "repaired_utc"}; one
    without `ledger_entry` is not ledgered and accounts for nothing
    (r4_notes_gate_a_and_repair.md §2 item 3)."""
    p = path or (admission_dir() / REPAIRS_FILENAME)
    out: set[str] = set()
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("repairs.jsonl: unparseable line skipped")
            continue
        if r.get("book") == book and r.get("cycle_id") and r.get("ledger_entry"):
            out.add(str(r["cycle_id"]))
    return out


def prior_unaccounted(book: str, months: Iterable[str], directory: Path | None = None) -> set[str]:
    """Cycle ids the most recent prior verdict for this book recorded as
    unaccounted. Carrying them forward is what stops the window advancing past
    an unaccounted order once the book's log moves on."""
    latest: dict[str, Any] | None = None
    for m in months:
        p = (directory or (admission_dir() / VENUE_CHECK_DIRNAME)) / f"{m}.jsonl"
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            # A row that could not see the venue carries no findings; it must
            # not erase the ones before it.
            if (r.get("book") == book and not r.get("check_error")
                    and r.get("venue_reachable") is not False):
                latest = r
    if not latest:
        return set()
    return {str(u["cycle_id"]) for u in latest.get("unaccounted") or [] if u.get("cycle_id")}


def fetch_orders_since(broker: Any, since: datetime) -> list[dict[str, Any]]:
    """Every venue order submitted after `since`, all statuses, paginated."""
    orders: dict[str, dict[str, Any]] = {}
    cursor = since
    while True:
        page = broker._request("GET", "/v2/orders", params={
            "status": "all", "after": cursor.isoformat(), "limit": PAGE_LIMIT,
            "direction": "asc", "nested": "false"})
        for o in page:
            orders[o["id"]] = o
        if len(page) < PAGE_LIMIT:
            return list(orders.values())
        cursor = datetime.fromisoformat(str(page[-1]["submitted_at"]).replace("Z", "+00:00"))


@dataclass
class BookVerdict:
    book: str
    window_start_cycle: str | None
    last_committed_cycle: str | None
    orders_examined: int
    unaccounted: list[dict[str, Any]]
    log_unparseable_lines: int = 0

    @property
    def id_ok(self) -> bool:
        return not self.unaccounted


def primary_test(book: str, orders: list[dict[str, Any]], log: BookLog,
                 repaired: set[str], carried: set[str], now_utc: datetime) -> BookVerdict:
    """The id test for one book over orders already fetched."""
    accounted = log.cycles | repaired
    still_open = sorted(c for c in carried if c not in accounted)
    anchors = [c for c in [log.last_cycle, *still_open] if c]
    start = min(anchors) if anchors else None
    since = cycle_time(start) if start else now_utc - NO_ANCHOR_LOOKBACK
    mine, unaccounted = [], []
    for o in orders:
        bc = cycle_of(o.get("client_order_id", ""))
        if bc is None or bc[0] != book:
            continue
        if cycle_time(bc[1]) < since:
            continue
        mine.append(o)
        if bc[1] not in accounted:
            unaccounted.append({
                "client_order_id": o.get("client_order_id"), "cycle_id": bc[1],
                "symbol": o.get("symbol"), "side": o.get("side"), "status": o.get("status"),
                "filled_qty": o.get("filled_qty"), "submitted_at": o.get("submitted_at")})
    return BookVerdict(book=book, window_start_cycle=start, last_committed_cycle=log.last_cycle,
                       orders_examined=len(mine), unaccounted=unaccounted,
                       log_unparseable_lines=log.unparseable_lines)


def decide(verdicts: list[BookVerdict], quantity_clean: bool | None,
           reachable: bool) -> dict[str, tuple[str, str | None]]:
    """book -> (verdict, would-withhold class). The §2 scope rule."""
    if not reachable:
        return {v.book: (WOULD_WITHHOLD, TICK_WITHHELD_VENUE_UNREACHABLE) for v in verdicts}
    if quantity_clean is False:
        return {v.book: (WOULD_WITHHOLD, TICK_WITHHELD_BOOKS_DIVERGED) for v in verdicts}
    return {v.book: ((PROCEED, None) if v.id_ok else
                     (WOULD_WITHHOLD, BOOK_WITHHELD_UNACCOUNTED_ORDERS)) for v in verdicts}


def _quantity_test(broker: Any, books: list[str], cycle_id: str,
                   load_portfolios: Callable[[list[str]], list[Any]] | None) -> Any:
    """`reconcile`, called unchanged, exactly as the post-cycle hook calls it
    (same params, same probe terms) — but never recorded and never handled."""
    from ..config_loader import load_settings, reconciliation_params
    from ..execution.administrative import administrative_cash_usd, administrative_positions
    from ..execution.reconciliation import reconcile
    settings = load_settings()
    mode = settings.get("mode", "paper")
    if load_portfolios is None:
        from ..portfolio.portfolio import load_portfolio
        portfolios = [load_portfolio(k) for k in books]
    else:
        portfolios = load_portfolios(books)
    from ..execution.account_pin import since as _account_since
    start = _account_since(settings, mode)
    return reconcile(portfolios, broker, cycle_id, **reconciliation_params(settings),
                     probe_cash_usd=administrative_cash_usd(mode, since=start),
                     probe_positions=administrative_positions(mode, since=start))


def run_check(broker: Any, books: list[str], now_utc: datetime, *, scope: str,
              boundary: tuple[str, str] | None = None,
              trades_dir: Path | None = None, repairs_path: Path | None = None,
              verdict_dir: Path | None = None,
              load_portfolios: Callable[[list[str]], list[Any]] | None = None,
              quantity_test: Callable[[Any, list[str], str], Any] | None = None,
              ) -> list[dict[str, Any]]:
    """Evaluate (V) for every venue book and return one row per book. Pure of
    side effects apart from venue GETs; the caller records and alerts.

    `quantity_test(broker, books, cycle_id)` replaces the production quantity
    test for a caller that is not the validation account (the demonstrations
    on the separate test account, whose unallocated cash and probe terms are
    not the committed ones). Production passes nothing."""
    now_utc = now_utc.astimezone(timezone.utc)
    cycle_id = "pretrade-" + now_utc.strftime(CYCLE_FORMAT)
    months = _months(now_utc)
    logs = {b: read_book_log(b, months, trades_dir) for b in books}
    repaired = {b: read_repairs(b, repairs_path) for b in books}
    carried = {b: prior_unaccounted(b, months, verdict_dir) for b in books}

    # The window for the one account-wide query is the earliest book window.
    starts = []
    for b in books:
        anchors = [c for c in [logs[b].last_cycle, *(carried[b] - logs[b].cycles - repaired[b])] if c]
        starts.append(cycle_time(min(anchors)) if anchors else now_utc - NO_ANCHOR_LOOKBACK)
    since = (min(starts) if starts else now_utc) - WINDOW_SLACK

    reachable, error = True, ""
    orders: list[dict[str, Any]] = []
    open_count: int | None = None
    open_ids: list[str] = []
    quantity: Any = None
    try:
        orders = fetch_orders_since(broker, since)
        opens = broker._request("GET", "/v2/orders", params={"status": "open", "limit": PAGE_LIMIT})
        open_count = len(opens)
        open_ids = [str(o.get("client_order_id")) for o in opens][:50]
        quantity = (quantity_test(broker, books, cycle_id) if quantity_test is not None
                    else _quantity_test(broker, books, cycle_id, load_portfolios))
    except Exception as e:  # noqa: BLE001 — unreachable is a verdict, not a crash
        reachable, error = False, f"{type(e).__name__}: {e}"

    verdicts = [primary_test(b, orders, logs[b], repaired[b], carried[b], now_utc) for b in books]
    q_clean = None if quantity is None else bool(quantity.clean)
    decisions = decide(verdicts, q_clean, reachable)
    id_failed = sorted(v.book for v in verdicts if not v.id_ok)
    q_events = [] if quantity is None else [e.to_dict() for e in quantity.events]

    rows = []
    for v in verdicts:
        verdict, cls = decisions[v.book]
        row: dict[str, Any] = {
            "check": "V", "shadow": True, "enforced": False, "scope": scope,
            "timestamp": now_utc.isoformat(), "book": v.book,
            "verdict": verdict, "would_withhold_class": cls,
            "orders_examined": v.orders_examined,
            "window_start_cycle": v.window_start_cycle,
            "last_committed_cycle": v.last_committed_cycle,
            "unaccounted": v.unaccounted,
            "id_test_ok": v.id_ok if reachable else None,
            "quantity_test_clean": q_clean,
            "quantity_events": q_events,
            "venue_reachable": reachable,
            "open_orders_count": open_count,
        }
        if boundary is not None:
            row["boundary_day"], row["boundary_slot"] = boundary
        if cls == TICK_WITHHELD_BOOKS_DIVERGED:
            row["books_to_repair"] = id_failed
        if open_count:
            row["open_order_ids"] = open_ids
        if error:
            row["venue_error"] = error
        if v.log_unparseable_lines:
            row["log_unparseable_lines"] = v.log_unparseable_lines
        rows.append(row)
    return rows


def record_rows(rows: list[dict[str, Any]], *, directory: Path | None = None) -> bool:
    if not rows:
        return True
    target = venue_check_path(datetime.fromisoformat(rows[0]["timestamp"]), directory)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        return True
    except Exception as e:  # noqa: BLE001 — a lost shadow row is logged, never fatal
        logger.error("(V) shadow: verdict rows not written: %s", e)
        return False


def _alert(rows: list[dict[str, Any]], scope: str, day: str) -> None:
    """Pages for the shadow results. A would-withhold is CRITICAL in shadow too:
    an unaccounted order is a lost decision record whether or not (V) enforces,
    and an unexplained shadow withhold is what blocks the flip. The open-order
    check is CRITICAL by registration."""
    from ..alerts import send_alert
    would = [r for r in rows if r["verdict"] == WOULD_WITHHOLD]
    if would:
        cls = sorted({r["would_withhold_class"] for r in would})
        books = sorted(r["book"] for r in would)
        unacc = [u["client_order_id"] for r in would for u in r.get("unaccounted") or []]
        send_alert("CRITICAL", f"(V) shadow would withhold: {', '.join(cls)}",
                   f"The pre-trade venue check (shadow, {scope}) would withhold "
                   f"{', '.join(books)}. Nothing was withheld: (V) is logging-only. "
                   f"Unaccounted order ids: {unacc[:20] or 'none'}. "
                   f"Venue error: {would[0].get('venue_error') or 'none'}.",
                   {"scope": scope, "classes": cls, "books": books},
                   kind="venue_check_shadow",
                   dedup_key=f"venue_check_shadow:{scope}:{'/'.join(cls)}:{day}")
    opens = next((r["open_orders_count"] for r in rows if r.get("open_orders_count")), 0)
    if opens:
        send_alert("CRITICAL", f"Open orders at the venue before the tick: {opens}",
                   f"The pre-trade venue check ({scope}) found {opens} open order(s) on the "
                   "account. Every order the pipeline places is resolved to a terminal "
                   "state before its run ends, so an open order is unaccounted state: a "
                   "cancel that failed, or an after-close order left queued. Ids: "
                   f"{rows[0].get('open_order_ids')}. Not withheld (open-order check is "
                   "logging-only until the hub rules).",
                   {"scope": scope, "open_orders": opens},
                   kind="venue_open_orders", dedup_key=f"venue_open_orders:{scope}:{day}")


def run_shadow_check(settings: dict[str, Any], books: list[str], now_utc: datetime, *,
                     scope: str, boundary: tuple[str, str] | None = None,
                     broker: Any = None) -> list[dict[str, Any]]:
    """Pipeline entry point. Builds its own read-only BrokerClient (never the
    Executor's: the order path is not touched), runs the check, records one row
    per venue book, pages, and returns the rows. Never raises; enforces nothing."""
    now_utc = now_utc.astimezone(timezone.utc)
    try:
        if not books:
            return []
        if broker is None:
            from ..execution.broker import BrokerClient
            broker = BrokerClient(settings["mode"])
        rows = run_check(broker, books, now_utc, scope=scope, boundary=boundary)
    except Exception as e:  # noqa: BLE001 — the shadow must never cost a tick
        logger.exception("(V) shadow check failed to run: %s", e)
        rows = [{"check": "V", "shadow": True, "enforced": False, "scope": scope,
                 "timestamp": now_utc.isoformat(), "book": b, "verdict": None,
                 "would_withhold_class": None, "orders_examined": None,
                 "check_error": f"{type(e).__name__}: {e}"} for b in books]
    record_rows(rows)
    for r in rows:
        logger.info("(V) shadow [%s] %s: %s%s (%s orders examined)", scope, r["book"],
                    r.get("verdict"), f" {r['would_withhold_class']}" if r.get(
                        "would_withhold_class") else "", r.get("orders_examined"))
    try:
        if not any(r.get("check_error") for r in rows):
            _alert(rows, scope, now_utc.astimezone(EASTERN).strftime("%Y-%m-%d"))
        else:
            from ..alerts import send_alert
            send_alert("WARN", "(V) shadow check did not run",
                       f"{rows[0]['check_error']}. Recorded as check_error rows; no tick "
                       "was affected (shadow).", {"scope": scope},
                       kind="venue_check_shadow",
                       dedup_key=f"venue_check_error:{scope}:{now_utc.strftime('%Y-%m-%d')}")
    except Exception as e:  # noqa: BLE001
        logger.exception("(V) shadow alert failed: %s", e)
    return rows
