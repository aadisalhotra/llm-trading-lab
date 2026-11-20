"""Induced demonstrations (iii), (iv), (viii) and (x) for (V) and EOD admission.

Governing text: docs/registrations/tick_admission_guards_ruling.md rev. 5 §3
condition 4, and the hub's dispatch of 2026-11-17, Part 4 I. None runs inside
a production validation tick or touches a validation book or the validation
account.

Two venues:
  --venue fake   an in-process fake venue served through the REAL BrokerClient
                 code (submit, await_terminal, place_and_confirm, cancel); the
                 rehearsal.
  --venue test   the separate TEST paper account (ALPACA_TEST_API_KEY_ID /
                 ALPACA_TEST_API_SECRET_KEY). What counts.

Safety, in this order, before anything can place an order:
  1. The validation account number is read with the validation keys.
  2. The process's broker_paper credentials are replaced IN MEMORY with the
     test keys, so every BrokerClient built in this process, by any code path,
     is the test account. Keys are never printed, logged or written.
  3. The client's account number is read back and must differ from the
     validation account's (the gate). Otherwise exit 1, nothing placed.
  4. The test account must start flat with no open orders.
Every order carries a demo book's segment (DEMOA / DEMOB / DEMOFLAT). All
state (decision logs, verdict rows, the boundary ledger, book saves, alerts)
lives in a temp workspace; nothing under data/ is written and no email is
sent. Each scenario ends flat: its positions are sold under DEMOFLAT.

Usage: python scripts/demos/admission_demos.py --venue fake|test
                                               [--demos iii,iv,viii,x] [--out PATH]
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from src.config_loader import load_env, load_settings  # noqa: E402
from src.execution import broker as broker_mod  # noqa: E402
from src.execution import reconciliation as recon_mod  # noqa: E402
from src.execution.broker import BrokerAPIError, BrokerClient  # noqa: E402
from src.execution.executor import Executor  # noqa: E402
from src.logging import boundary_ledger, decision_log  # noqa: E402
from src.logging import venue_check as vc  # noqa: E402
from src.portfolio.portfolio import Portfolio  # noqa: E402

logger = logging.getLogger("demos")
ET = ZoneInfo("America/New_York")
BOOKS = ("DEMOA", "DEMOB")
FLAT_BOOK = "DEMOFLAT"
BOOK_CASH = 100.0
NOTIONAL = 12.0                  # per demo order, above the $1 venue minimum
PAPER = "https://paper-api.alpaca.markets"


# --------------------------------------------------------------------------- venues
class FakeVenue(BrokerClient):
    """A venue in memory behind the real BrokerClient. Market orders fill at
    once at `price` while open; after the close they are accepted and never
    fill (case B) or refused (case A), per `after_close`."""

    def __init__(self, price: float, *, cash: float = 10500.0):
        self.mode, self.base_url = "broker_paper", "fake://venue"
        self.fill_deadline_seconds, self.poll_interval_seconds = 3, 0.01
        self.price, self.cash, self.market_open, self.after_close = price, cash, True, "accept"
        self.orders: dict[str, dict] = {}
        self.positions: dict[str, float] = {}
        self.account_number = "FAKE0000"

    def _request(self, method, path, *, body=None, params=None, timeout=20):
        now = datetime.now(timezone.utc).isoformat()
        if method == "POST" and path == "/v2/orders":
            if not self.market_open and self.after_close == "reject":
                raise BrokerAPIError("market is closed", status=403, code=40310000)
            oid = uuid.uuid4().hex
            qty = float(body["qty"])
            o = {"id": oid, "client_order_id": body["client_order_id"], "symbol": body["symbol"],
                 "side": body["side"], "qty": body["qty"], "submitted_at": now,
                 "status": "accepted", "filled_qty": "0", "filled_avg_price": None}
            if self.market_open:
                sign = 1 if body["side"] == "buy" else -1
                self.positions[body["symbol"]] = self.positions.get(body["symbol"], 0.0) + sign * qty
                self.cash -= sign * round(qty * self.price, 2)
                o.update(status="filled", filled_qty=body["qty"], filled_avg_price=str(self.price),
                         filled_at=now)
            self.orders[oid] = o
            return dict(o)
        if method == "GET" and path.startswith("/v2/orders:by_client_order_id"):
            return dict(next(o for o in self.orders.values()
                             if o["client_order_id"] == params["client_order_id"]))
        if method == "GET" and path.startswith("/v2/orders/"):
            return dict(self.orders[path.rsplit("/", 1)[1]])
        if method == "DELETE" and path.startswith("/v2/orders/"):
            o = self.orders[path.rsplit("/", 1)[1]]
            if o["status"] not in ("filled", "canceled"):
                o["status"] = "canceled"
            return {}
        if method == "GET" and path == "/v2/orders":
            st = (params or {}).get("status", "open")
            rows = [dict(o) for o in self.orders.values()]
            return [o for o in rows if st == "all" or o["status"] in ("new", "accepted")]
        if method == "GET" and path == "/v2/account":
            return {"cash": str(round(self.cash, 2)), "account_number": self.account_number}
        if method == "GET" and path == "/v2/positions":
            return [{"symbol": s, "qty": str(q), "current_price": str(self.price),
                     "market_value": str(q * self.price)}
                    for s, q in self.positions.items() if abs(q) > 1e-9]
        if method == "GET" and path == "/v2/clock":
            return {"is_open": self.market_open, "timestamp": now, "next_open": None}
        raise AssertionError(f"fake venue: unhandled {method} {path}")


def gate_test_account() -> tuple[BrokerClient, str]:
    """Steps 1-4 of the safety order. Returns the gated client and the test
    account's last four characters."""
    load_env()
    val = (os.getenv("ALPACA_PAPER_KEY") or os.environ["ALPACA_API_KEY"],
           os.getenv("ALPACA_PAPER_SECRET") or os.environ["ALPACA_SECRET_KEY"])
    tk, ts = os.environ["ALPACA_TEST_API_KEY_ID"], os.environ["ALPACA_TEST_API_SECRET_KEY"]
    if tk == val[0]:
        sys.exit("GATE: the test key IS the validation key. Nothing placed.")
    val_acct = requests.get(PAPER + "/v2/account", timeout=30, headers={
        "APCA-API-KEY-ID": val[0], "APCA-API-SECRET-KEY": val[1]}).json()["account_number"]
    # Step 2: every broker_paper client in this process is the test account.
    for k in ("ALPACA_PAPER_KEY", "ALPACA_API_KEY"):
        os.environ[k] = tk
    for k in ("ALPACA_PAPER_SECRET", "ALPACA_SECRET_KEY"):
        os.environ[k] = ts
    client = BrokerClient("broker_paper")
    acct = client.get_account()
    if acct["account_number"] == val_acct:
        sys.exit("GATE FAILED: the client resolves to the validation account. Nothing placed.")
    cfg = client._request("GET", "/v2/account/configurations")
    if str(cfg.get("max_margin_multiplier")) != "1" or cfg.get("no_shorting") is not True:
        sys.exit(f"GATE: test account not configured like validation: {cfg}")
    return client, acct["account_number"][-4:]


def assert_flat(client, allow_held: set[str] = frozenset()) -> None:
    """No open orders, and no position except the declared `allow_held`
    symbols (the after-close test's position), which each World carries as an
    expected-side term so reconciliation accounts for it."""
    pos = [p for p in client.get_positions() if abs(float(p["qty"])) > 1e-9
           and broker_mod.from_venue_symbol(p["symbol"]) not in allow_held]
    opens = client._request("GET", "/v2/orders", params={"status": "open", "limit": 500})
    if pos or opens:
        sys.exit(f"Test account not flat: positions {pos}, open orders {len(opens)}. Nothing placed.")


# --------------------------------------------------------------------------- workspace
class World:
    """One scenario's committed record (temp dirs), the books, and the venue."""

    def __init__(self, client, symbol: str, price_fn, root: Path):
        self.client, self.symbol, self.price_fn = client, symbol, price_fn
        self.root = root
        self.trades = root / "trades"
        self.vdir = root / "venue_check"
        self.repairs = root / "repairs.jsonl"
        self.ledger = root / "handled_boundaries.json"
        for d in (self.trades, self.vdir):
            d.mkdir(parents=True, exist_ok=True)
        self.alerts: list[tuple] = []
        self.books = {b: Portfolio(model_key=b, cash=BOOK_CASH, inception_value=BOOK_CASH,
                                   inception_date="2026-11-19") for b in BOOKS}
        self.committed = copy.deepcopy(self.books)        # the "saved and pushed" books
        acct_cash = float(client.get_account()["cash"])
        self.unallocated = acct_cash - BOOK_CASH * len(BOOKS)
        # Positions present before the scenario (only declared ones pass
        # assert_flat): an expected-side term, as probe positions are.
        self.baseline_positions = {broker_mod.from_venue_symbol(p["symbol"]): float(p["qty"])
                                   for p in client.get_positions() if abs(float(p["qty"])) > 1e-9}
        settings = load_settings()
        settings["mode"] = "broker_paper"
        self.settings = settings

    # A tick: an Executor bound to this venue, with a fresh cycle id.
    def executor(self, cycle_id: str) -> Executor:
        ex = Executor(cycle_id, settings=self.settings, venue_books=set(BOOKS) | {FLAT_BOOK})
        ex.broker = self.client
        return ex

    def buy(self, ex: Executor, book: str) -> dict:
        price = self.price_fn()
        shares = round(NOTIONAL / price, 4)
        pl = ex._place(book, self.symbol, shares, "buy", price)
        if pl.moved:
            self.books[book].buy(self.symbol, pl.filled_qty, pl.fill_price, venue_confirmed=True)
        return {"order_id": pl.order_id, "filled_qty": pl.filled_qty, "moved": pl.moved,
                "status": pl.order.status if pl.order else None, "error": pl.error}

    def commit_row(self, book: str, cycle_id: str, executions: list[dict]) -> None:
        """The committed decision row (model-call form, minimal)."""
        d = cycle_id[:4] + "-" + cycle_id[4:6] + "-" + cycle_id[6:8]
        row = {"cycle_id": cycle_id, "model_key": book, "date": d,
               "timestamp": datetime.now(timezone.utc).isoformat(), "api_success": True,
               "execution_mode": "broker_paper", "provider": "demo",
               "executions": executions}
        with open(self.trades / f"{book}_{cycle_id[:4]}-{cycle_id[4:6]}.jsonl", "a",
                  encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def save(self, book: str) -> None:
        self.committed[book] = copy.deepcopy(self.books[book])

    def quantity_test(self, broker, books, cycle_id):
        return recon_mod.reconcile([self.committed[b] for b in books], broker, cycle_id,
                                   cash_tolerance_usd=1.0, unallocated_cash_usd=self.unallocated,
                                   position_tolerance_usd=1.0,
                                   probe_positions=self.baseline_positions)

    def v(self, scope="intraday") -> dict[str, dict]:
        rows = vc.run_check(self.client, list(BOOKS), datetime.now(timezone.utc), scope=scope,
                            trades_dir=self.trades, repairs_path=self.repairs,
                            verdict_dir=self.vdir, quantity_test=self.quantity_test)
        vc.record_rows(rows, directory=self.vdir)
        return {r["book"]: r for r in rows}

    def flatten(self) -> None:
        """Sell every position this scenario created, under DEMOFLAT."""
        ex = self.executor(cyc())
        for p in self.client.get_positions():
            q = float(p["qty"]) - self.baseline_positions.get(
                broker_mod.from_venue_symbol(p["symbol"]), 0.0)
            if q > 1e-9:
                ex._place(FLAT_BOOK, broker_mod.from_venue_symbol(p["symbol"]), q, "sell",
                          self.price_fn())


def cyc() -> str:
    time.sleep(1.1)                          # cycle ids are second-resolution
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def check(log: list, name: str, cond: bool, detail: Any = None) -> bool:
    log.append({"check": name, "pass": bool(cond), "detail": detail})
    logger.info("%s %s %s", "PASS" if cond else "FAIL", name, "" if detail is None else detail)
    return bool(cond)


def summary(v: dict) -> dict:
    return {b: (r["verdict"], r["would_withhold_class"], [u["cycle_id"] for u in r["unaccounted"]],
                r.get("books_to_repair")) for b, r in v.items()}


# --------------------------------------------------------------------------- scenarios
def demo_iii(w: World) -> list:
    """(iii) A run stopped after its first fill, then a same-slot re-run and the
    next slot's run: in both the id test names the filled book, the quantity
    test fails, and every venue book is withheld; no halt flag is set."""
    log: list = []
    c1 = cyc()
    ex = w.executor(c1)
    for b in BOOKS:                                    # tick 1, committed normally
        r = w.buy(ex, b)
        w.commit_row(b, c1, [r])
        w.save(b)
    check(log, "baseline (V) proceeds", all(r["verdict"] == "proceed" for r in w.v().values()),
          summary(w.v()))
    c2 = cyc()
    killed = w.buy(w.executor(c2), "DEMOA")           # first fill, then the runner dies
    check(log, "the killed run's first order filled", killed["moved"], killed)
    for label in ("same-slot re-run", "next slot's run"):
        v = w.v()
        check(log, f"{label}: id test names DEMOA's lost cycle",
              [u["cycle_id"] for u in v["DEMOA"]["unaccounted"]] == [c2], summary(v))
        check(log, f"{label}: quantity test fails", v["DEMOA"]["quantity_test_clean"] is False)
        check(log, f"{label}: every venue book would be withheld (BOOKS_DIVERGED)",
              all(r["would_withhold_class"] == vc.TICK_WITHHELD_BOOKS_DIVERGED for r in v.values()))
        check(log, f"{label}: books_to_repair names DEMOA",
              v["DEMOB"].get("books_to_repair") == ["DEMOA"])
    check(log, "no halt flag set", not any(p.halted for p in w.committed.values()))
    return log


def demo_iv(w: World) -> list:
    """(iv) A completed run whose push fails: the next run withholds every venue
    book, the id test naming each book that traded."""
    log: list = []
    c1 = cyc()
    ex = w.executor(c1)
    for b in BOOKS:
        r = w.buy(ex, b)
        w.commit_row(b, c1, [r])
        w.save(b)
    c2 = cyc()
    ex = w.executor(c2)
    for b in BOOKS:                                    # the run completes in the runner...
        w.buy(ex, b)
    # ...and its push fails five times: nothing of c2 reaches the committed record.
    v = w.v()
    check(log, "id test names both books that traded",
          all([u["cycle_id"] for u in v[b]["unaccounted"]] == [c2] for b in BOOKS), summary(v))
    check(log, "every venue book would be withheld (BOOKS_DIVERGED)",
          all(r["would_withhold_class"] == vc.TICK_WITHHELD_BOOKS_DIVERGED for r in v.values()))
    check(log, "books_to_repair names each book", v["DEMOA"].get("books_to_repair") == list(BOOKS))
    return log


def demo_viii(w: World) -> list:
    """(viii) A book raises after its orders inside a run that commits.
    Before the save: the same run's post-cycle reconciliation halts every venue
    book, and the next tick's quantity test withholds them all. After the save:
    reconciliation is clean and the next tick's id test withholds that book."""
    log: list = []
    c1 = cyc()
    ex = w.executor(c1)
    for b in BOOKS:
        r = w.buy(ex, b)
        w.commit_row(b, c1, [r])
        w.save(b)
    # --- branch 1: raised BEFORE the save
    c2 = cyc()
    ex = w.executor(c2)
    w.buy(ex, "DEMOA")                                 # A's order fills, then A raises
    w.books["DEMOA"] = copy.deepcopy(w.committed["DEMOA"])   # nothing saved for A
    rb = w.buy(ex, "DEMOB")                            # B finishes and commits
    w.commit_row("DEMOB", c2, [rb])
    w.save("DEMOB")
    halted: list = []
    saved: list = []
    orig_save = recon_mod.save_portfolio
    recon_mod.save_portfolio = lambda p: saved.append(p.model_key)
    try:
        books = [copy.deepcopy(w.committed[b]) for b in BOOKS]   # reloaded from disk
        res = w.quantity_test(w.client, list(BOOKS), c2)
        recon_mod.handle_reconciliation_result(res, books,
                                               send_alert_fn=lambda *a, **k: w.alerts.append(a))
        halted = [p.model_key for p in books if p.halted]
    finally:
        recon_mod.save_portfolio = orig_save
    check(log, "before-save: post-cycle reconciliation diverges", not res.clean,
          [e.to_dict() for e in res.events])
    check(log, "before-save: the handler halts EVERY venue book", sorted(halted) == list(BOOKS),
          halted)
    v = w.v()
    check(log, "before-save: next tick's quantity test would withhold every book",
          all(r["would_withhold_class"] == vc.TICK_WITHHELD_BOOKS_DIVERGED for r in v.values()),
          summary(v))
    # Flatten and reset for branch 2 (the halted flags were the handler's,
    # on reloaded copies; branch 2 starts from a fresh, consistent record).
    w.flatten()
    w2 = World(w.client, w.symbol, w.price_fn, w.root / "branch2")
    c3 = cyc()
    ex = w2.executor(c3)
    for b in BOOKS:
        r = w2.buy(ex, b)
        w2.commit_row(b, c3, [r])
        w2.save(b)
    # --- branch 2: raised AFTER the save
    c4 = cyc()
    ex = w2.executor(c4)
    w2.buy(ex, "DEMOA")
    w2.save("DEMOA")                                   # saved, then A raises: no row
    rb = w2.buy(ex, "DEMOB")
    w2.commit_row("DEMOB", c4, [rb])
    w2.save("DEMOB")
    res = w2.quantity_test(w2.client, list(BOOKS), c4)
    check(log, "after-save: post-cycle reconciliation is clean", res.clean,
          [e.to_dict() for e in res.events])
    v = w2.v()
    check(log, "after-save: id test withholds DEMOA alone",
          v["DEMOA"]["would_withhold_class"] == vc.BOOK_WITHHELD_UNACCOUNTED_ORDERS
          and [u["cycle_id"] for u in v["DEMOA"]["unaccounted"]] == [c4], summary(v))
    check(log, "after-save: DEMOB proceeds", v["DEMOB"]["verdict"] == "proceed")
    w2.flatten()
    return log


def demo_x(w: World, after_close: bool) -> list:
    """(x) End of day, three cases. Needs a position held into the close; on
    the test account it runs after the close (the venue's real behaviour), on
    the fake venue the close is simulated."""
    import src.pipeline as pl
    log: list = []
    c1 = cyc()
    ex = w.executor(c1)
    r = w.buy(ex, "DEMOA")
    w.commit_row("DEMOA", c1, [r])
    w.save("DEMOA")
    w.commit_row("DEMOB", c1, [])
    if isinstance(w.client, FakeVenue):
        w.client.market_open = False
    else:
        # The real venue: hold the position through the close, then run the
        # EOD cases against the closed market (polls the venue clock, <= 40 min).
        deadline = time.monotonic() + 40 * 60
        while w.client._request("GET", "/v2/clock").get("is_open"):
            if time.monotonic() > deadline:
                raise RuntimeError("market still open after 40 min; (x) needs the close")
            time.sleep(30)
    # The book's cost is set above the close so the stop triggers (the venue
    # does not see costs; reconciliation compares shares).
    for p in (w.books["DEMOA"], w.committed["DEMOA"]):
        p.holdings[w.symbol].avg_cost = w.price_fn() * 1.5
    patches = {"send_alert": lambda *a, **k: w.alerts.append(a[:2]),
               "effective_mode": lambda k, s: "broker_paper"}
    saved = {k: getattr(pl, k) for k in patches}
    orig_td = decision_log.TRADES_DIR
    for k, f in patches.items():
        setattr(pl, k, f)
    decision_log.TRADES_DIR = w.trades
    try:
        prices = {w.symbol: w.price_fn()}
        # ---- case 1: the EOD stop submits, its admin row is committed
        e1 = cyc()
        ex = w.executor(e1)
        book = w.books["DEMOA"]
        pre = {t: (h.shares, h.avg_cost) for t, h in book.holdings.items()}
        forced, dust = pl._position_stop_sweep("DEMOA", book, prices, ex, "2026-11-19", logger)
        row = pl._write_eod_admin_row("DEMOA", book, forced, pre, prices, ex, w.settings,
                                      datetime.now(ET), book.snapshot(prices), dust, logger)
        check(log, "case 1: the EOD stop sweep submitted an order", bool(forced),
              [(f.order_id, f.executed, f.constraint) for f in forced])
        check(log, "case 1: the administrative row was written with the EOD cycle",
              row is not None and row["cycle_id"] == e1,
              None if row is None else {"constraint": row["executions"][0]["constraint"],
                                        "stop_unprotected": row["executions"][0]["stop_unprotected"],
                                        "venue_clock": row.get("venue_clock")})
        w.save("DEMOA")
        v = w.v(scope="EOD")
        check(log, "case 1: the next morning's first tick admits DEMOA",
              v["DEMOA"]["verdict"] == "proceed", summary(v))
        # ---- case 2: an EOD pass submits and does not commit
        if isinstance(w.client, FakeVenue):
            w.client.market_open = True
            cr = cyc()                                 # reopen a position (fake only),
            rr = w.buy(w.executor(cr), "DEMOA")        # committed like any tick
            w.commit_row("DEMOA", cr, [rr])
            w.save("DEMOA")
            w.client.market_open = False
        e2 = cyc()
        ex = w.executor(e2)
        book = copy.deepcopy(w.committed["DEMOA"])
        if w.symbol in book.holdings:
            book.holdings[w.symbol].avg_cost = w.price_fn() * 1.5
            forced2, _ = pl._position_stop_sweep("DEMOA", book, prices, ex, "2026-11-19", logger)
        else:
            # On the test account the case-1 stop may have filled after hours
            # (case D); then place the uncommitted EOD order directly.
            pl2 = ex._place("DEMOA", w.symbol, round(NOTIONAL / w.price_fn(), 4), "sell",
                            w.price_fn())
            forced2 = [pl2]
        # ...its row and book are lost (no commit). A late EOD pass and the
        # next morning's tick:
        for label, scope in (("late/backup EOD pass", "EOD"), ("next morning's tick", "intraday")):
            v = w.v(scope=scope)
            check(log, f"case 2: {label} would withhold DEMOA (id test names the EOD cycle)",
                  v["DEMOA"]["verdict"] == "would_withhold"
                  and e2 in [u["cycle_id"] for u in v["DEMOA"]["unaccounted"]], summary(v))
        # ---- case 3: a duplicate EOD pass after a committed one
        boundary_ledger.mark_boundary_handled("2026-11-19", "EOD", path=w.ledger)
        orig_ll = pl.load_ledger
        pl.load_ledger = lambda *a, **k: boundary_ledger.load_ledger(w.ledger)
        try:
            before = len(w.client._request("GET", "/v2/orders",
                                           params={"status": "all", "limit": 500}))
            suspended, readable = pl._eod_admission(w.settings, datetime(2026, 11, 19, 16, 30,
                                                                        tzinfo=ET), False, logger)
            after = len(w.client._request("GET", "/v2/orders",
                                          params={"status": "all", "limit": 500}))
        finally:
            pl.load_ledger = orig_ll
        check(log, "case 3: a duplicate EOD pass suspends the stop sweep",
              suspended == "eod_boundary_already_handled" and readable, suspended)
        check(log, "case 3: it submits nothing", after == before, {"orders_before": before,
                                                                   "orders_after": after})
    finally:
        for k, f in saved.items():
            setattr(pl, k, f)
        decision_log.TRADES_DIR = orig_td
        if isinstance(w.client, FakeVenue):
            w.client.market_open = True
    return log


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", choices=("fake", "test"), required=True)
    ap.add_argument("--demos", default="iii,iv,viii,x")
    ap.add_argument("--symbol", default="F")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--allow-held", default="",
                    help="comma-separated symbols already held (the after-close test's)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.venue == "fake":
        # The Executor builds its own BrokerClient before ours replaces it.
        # Inert credentials that authenticate nowhere, so no real account is
        # reachable from the rehearsal even by mistake.
        for k in ("ALPACA_PAPER_KEY", "ALPACA_PAPER_SECRET"):
            os.environ[k] = "fake-rehearsal-no-account"
        for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
            os.environ.pop(k, None)
        client, acct = FakeVenue(11.25), "FAKE"
        price_fn = lambda: client.price
    else:
        client, acct = gate_test_account()
        assert_flat(client, {s for s in a.allow_held.split(",") if s})

        def price_fn():
            # The market-data host times out intermittently from this machine
            # (2026-11-19 09:37 and 09:38 ET: two (viii) runs lost to it while
            # paper-api answered). Retry; raise if every attempt fails.
            for attempt in range(5):
                try:
                    q = requests.get(
                        f"https://data.alpaca.markets/v2/stocks/{a.symbol}/trades/latest",
                        headers=client.headers, timeout=20).json()
                    return float(q["trade"]["p"])
                except requests.RequestException:
                    if attempt == 4:
                        raise
                    logger.warning("quote fetch failed (attempt %d); retrying", attempt + 1)
                    time.sleep(3 * (attempt + 1))
    results: dict[str, Any] = {"venue": a.venue, "account": f"...{acct}",
                               "started_utc": datetime.now(timezone.utc).isoformat(),
                               "symbol": a.symbol, "demos": {}}
    for name in a.demos.split(","):
        with tempfile.TemporaryDirectory(prefix=f"demo_{name}_") as tmp:
            w = World(client, a.symbol, price_fn, Path(tmp))
            fn = {"iii": demo_iii, "iv": demo_iv, "viii": demo_viii}.get(name)
            try:
                log = fn(w) if fn else demo_x(w, a.venue == "test")
            except Exception as e:  # noqa: BLE001 — recorded as a failed demonstration
                logger.exception("demo %s raised", name)
                log = [{"check": "ran", "pass": False, "detail": f"{type(e).__name__}: {e}"}]
            finally:
                try:
                    w.flatten()
                except Exception as e:  # noqa: BLE001
                    logger.error("flatten after %s failed: %s", name, e)
            results["demos"][name] = {"checks": log, "alerts": [list(map(str, x)) for x in w.alerts],
                                      "passed": all(c["pass"] for c in log)}
    results["finished_utc"] = datetime.now(timezone.utc).isoformat()
    results["all_passed"] = all(d["passed"] for d in results["demos"].values())
    out = a.out or (ROOT / "docs" / "ops" / "demos_2026-11-19" / f"{a.venue}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v["passed"] for k, v in results["demos"].items()}),
          "ALL PASSED" if results["all_passed"] else "FAILURES", "->", out)
    return 0 if results["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
