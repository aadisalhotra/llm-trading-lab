"""Scheduled barrier probe: one genuine cross-book contention per trading day.

What it tests. The P1 cross-book submission barrier (src/execution/barrier.py)
holds every other intent on a contended ticker until the winner's order is
terminal. This probe creates that contention against the real venue, during
RTH, with the production executor and barrier code (no fakes). Two synthetic
administrative books act in one cycle on one symbol: FORCED_PROBE_SELL
intends a SELL, FORCED_PROBE_BUY intends a BUY. Each run records:

  * engaged         -- whether the barrier identified the ticker as contended
  * ordering        -- the winner it chose, against the one the registered
                       rule predicts (spike rulings rev.4 §1: sell-side
                       first, then t mod n over the sorted registered books),
                       derived here independently of barrier.py
  * winner_terminal -- the terminal state the winner's order reached, from
                       the venue's own order record
  * release         -- whether the held intent released on the winner's
                       terminal state (not on the timeout), and whether its
                       venue `submitted_at` is at or after the winner's
                       venue `filled_at`

It records; it does not conclude. A verdict label is computed mechanically
from those fields and the session reads the record.

Why it is standalone. It gates the cutover, so it cannot require it. It builds
its own broker_paper Executor (via the constructor's `settings`/`venue_books`
seams) whatever the committed `mode` is, like the 2026-10-05 probe. That probe
built its Executor with `__new__` and died on `venue_books`, an attribute the
constructor gained after the probe was written. The seam is the fix.

Shared paper account. It trades in the account the validation month uses, so:

  * Every order id starts `FORCED_PROBE_`. That is the `FORCED_` stem
    research_metrics._is_administrative censors, the same handling as
    FORCED_BOUNDARY_ADMIN.
  * Its books exist only in memory. It writes nothing to data/ except its own
    record file, data/barrier_probe/YYYY-MM.jsonl.
  * SYMBOL is F (Ford), which is not in the trading universe, so no production
    book can contend with it or hold it.
  * The run ends flat in F. The few cents of cash the round trip leaves are
    recorded as `residual_cash_usd`. Level-2 reconciliation adds them to the
    unallocated term (src/execution/administrative.py), so probe orders never
    enter the cash comparison.
  * If it cannot get back to flat, the position it holds is recorded as
    `position_delta`, and it raises CRITICAL at once. Level 2 reconciles the
    position against that record rather than halting (attributable, not
    exempt). The next scheduled run closes it first and records the negative
    delta. A position in F the records do not account for is unexplained: the
    probe refuses to trade on top of it and alerts, and level 2 halts.
  * It refuses any endpoint but Alpaca paper.
  * It only cancels or flattens its OWN orders and its own F position.
    `DELETE /v2/orders` (cancel everything) is never called. The old probe's
    cleanup called it, and after cutover that would cancel the books' orders.

Non-interference with production cycles. A real run starts only when all of
these hold, and skips otherwise. A skip is recorded, so "skipped" and "never
fired" stay distinguishable:

  * the market is open, with at least 30 minutes to the close;
  * the ET minute within the half-hour slot is between WINDOW_START_MIN and
    WINDOW_END_MIN. Production cycles fire on :00/:30, and their first orders
    come several minutes later, after the LLM calls;
  * no intraday `run` job is queued or in progress (GitHub Actions API).
    This check fails closed: if it cannot be verified, the run skips;
  * no open order and no position in SYMBOL exists before it starts.

No order is submitted after SUBMIT_CUTOFF_MIN in the slot. Cleanup always runs.

Usage:
  python scripts/barrier_probe.py                 # scheduled path (RTH, guarded)
  python scripts/barrier_probe.py --dry-run --out <dir>
      Bypasses the market/window/production guards and writes to <dir>, never
      to data/. With the market shut nothing fills: this exercises the abort
      path and the scoped cleanup, not the barrier.

Exit status: 0 for CLEARS and for a recorded skip; 1 for anything else
(FAILED, INCONCLUSIVE, error, unclean cleanup, unmet precondition), so the
workflow run goes red. The 2026-10-05 run exited 0 on an INCONCLUSIVE that
measured nothing; that is the rule-9 failure this exit code exists to prevent.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_loader import load_env, load_settings  # noqa: E402
from src.execution.administrative import (  # noqa: E402
    PROBE_BOOK_PREFIX,
    PROBE_DIR,
    PROBE_MODE,
    administrative_positions,
)
from src.execution.barrier import CycleBarrier, rotation_tick  # noqa: E402
from src.execution.broker import BrokerClient, build_order_id  # noqa: E402
from src.execution.executor import Executor  # noqa: E402
from src.portfolio.portfolio import Holding, Portfolio  # noqa: E402

logger = logging.getLogger("llmlab.barrier_probe")

ET = ZoneInfo("America/New_York")
# Broker-paper only, never the live endpoint (hub ruling 2026-11-02 ②): from
# 2026-12-01 live is real capital and a probe has no business in it.
MODE = PROBE_MODE
PAPER_ENDPOINT = "https://paper-api.alpaca.markets"

SYMBOL = "F"                 # liquid, fractionable, NOT in config/universe.json
SEED_SHARES = 10.0           # seeds the sell-side book (~$100 notional)
BUY_BOOK_CASH = 1000.0
BUY_TARGET_WEIGHT = 0.10     # buy-side intent of ~$100

SELL_BOOK = PROBE_BOOK_PREFIX + "SELL"
BUY_BOOK = PROBE_BOOK_PREFIX + "BUY"
SEED_BOOK = PROBE_BOOK_PREFIX + "SEED"
CLEANUP_BOOK = PROBE_BOOK_PREFIX + "CLEANUP"

# Window inside each 30-minute slot, in ET minutes past :00/:30.
WINDOW_START_MIN = 6
WINDOW_END_MIN = 18
SUBMIT_CUTOFF_MIN = 25
MIN_MINUTES_TO_CLOSE = 30

INTRADAY_WORKFLOW = "intraday.yml"
PRODUCTION_JOB = "run"
BUSY_JOB_STATES = {"queued", "in_progress", "waiting", "pending", "requested"}

EXIT_OK, EXIT_FAIL = 0, 1


class ProbeAbort(RuntimeError):
    """The probe cannot proceed. Cleanup still runs."""


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested in tests/test_barrier_probe.py)
# ---------------------------------------------------------------------------
def parse_ts(s: str | None) -> datetime | None:
    """Parse a venue timestamp. Alpaca returns nanosecond fractions, which
    datetime.fromisoformat (3.11) rejects, so the fraction is cut to 6 digits."""
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    m = re.match(r"^(.*?\.\d{1,6})\d*([+-]\d\d:\d\d)?$", s)
    if m:
        s = m.group(1) + (m.group(2) or "")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def minute_in_slot(now_et: datetime) -> int:
    return now_et.minute % 30


def window_check(now: datetime, next_close: datetime) -> tuple[bool, str]:
    """Whether a real run may START now. Times must be tz-aware."""
    now_et = now.astimezone(ET)
    m = minute_in_slot(now_et)
    if not (WINDOW_START_MIN <= m <= WINDOW_END_MIN):
        return False, (f"{now_et:%H:%M} ET is minute {m} of its slot; the probe "
                       f"starts only in minutes {WINDOW_START_MIN}-{WINDOW_END_MIN}")
    to_close = (next_close - now).total_seconds() / 60
    if to_close < MIN_MINUTES_TO_CLOSE:
        return False, f"{to_close:.0f} min to the close; need {MIN_MINUTES_TO_CLOSE}"
    return True, f"{now_et:%H:%M} ET, minute {m} of slot, {to_close:.0f} min to close"


def submission_allowed(now: datetime) -> bool:
    """No probe order may be submitted after the cutoff in the slot the run
    started in. Seed and contention both check this; cleanup does not."""
    return minute_in_slot(now.astimezone(ET)) < SUBMIT_CUTOFF_MIN


# The probe's own contention: one sell-side and one buy-side intent on SYMBOL,
# both books registered with the barrier.
PROBE_INTENTS: tuple[tuple[str, str], ...] = ((SELL_BOOK, "SELL"), (BUY_BOOK, "BUY"))
PROBE_REGISTERED: tuple[str, ...] = (SELL_BOOK, BUY_BOOK)
_BUY_SIDE = frozenset({"BUY", "COVER"})


def expected_winner(intents: Iterable[tuple[str, str]],
                    registered_books: Iterable[str],
                    t: int) -> tuple[str, str] | None:
    """The winner the registered rule predicts for one contended ticker.

    docs/registrations/spike_rulings_parity_wash_barrier.md §1 (rev. 4):
    a ticker is contended only when a buy-side intent (BUY, COVER) and a
    sell-side intent (SELL, SHORT) meet on it; among its intents sell-side
    ranks first, and within a side the book ranks by the rotation of ALL
    registered books, sorted, offset by t mod n (n = number of registered
    books). Returns None when the rule says the ticker is uncontended.

    Written from the registration, not imported from src/execution/barrier.py,
    so that the check can disagree with the code it is checking. (The previous
    version returned the SELL book unconditionally -- the probe's sells-first
    assumption -- and so could never test the rotation; CLAUDE.md rule 9.)
    """
    intents = [(str(b), str(a).upper()) for b, a in intents]
    if not any(a in _BUY_SIDE for _, a in intents) or all(a in _BUY_SIDE for _, a in intents):
        return None
    base = sorted(registered_books)
    if not base:
        return None
    offset = t % len(base)
    rotation = base[offset:] + base[:offset]
    unregistered = sorted({b for b, _ in intents} - set(rotation))
    if unregistered:
        raise ValueError(f"intents from unregistered books: {unregistered}")
    return min(intents, key=lambda ba: (1 if ba[1] in _BUY_SIDE else 0, rotation.index(ba[0])))


def production_busy(list_runs: Callable[[str], list[dict]],
                    list_jobs: Callable[[dict], list[dict]]) -> tuple[bool, str]:
    """True if any intraday run has its `run` job queued or in progress.

    The intraday workflow's `chain` job sleeps between dispatches, so a run
    is `in_progress` for most of the session. Only the `run` job trades,
    so the run's status is not the signal; the `run` job's is. A queued run is
    busy by definition (its `run` job is about to start)."""
    for status in ("queued", "in_progress"):
        for run in list_runs(status):
            if status == "queued":
                return True, f"intraday run {run.get('id')} is queued"
            for job in list_jobs(run):
                if job.get("name") == PRODUCTION_JOB and job.get("status") in BUSY_JOB_STATES:
                    return True, (f"intraday run {run.get('id')} job "
                                  f"'{PRODUCTION_JOB}' is {job.get('status')}")
    return False, "no intraday run job queued or in progress"


def run_orders(orders: list[dict], cycle_id: str) -> list[dict]:
    """This run's probe orders: the FORCED_PROBE_ prefix AND this cycle id."""
    return [o for o in orders
            if str(o.get("client_order_id", "")).startswith(PROBE_BOOK_PREFIX)
            and f"-{cycle_id}-" in str(o.get("client_order_id", ""))]


def fill_cash_and_position(orders: list[dict]) -> tuple[float, float]:
    """(net cash, net shares) from venue fills. Buys cost cash, sells return it."""
    cash = shares = 0.0
    for o in orders:
        q = float(o.get("filled_qty") or 0.0)
        p = float(o.get("filled_avg_price") or 0.0)
        if q <= 0:
            continue
        if o.get("side") == "buy":
            cash -= q * p
            shares += q
        elif o.get("side") == "sell":
            cash += q * p
            shares -= q
    return round(cash, 6), round(shares, 9)


def evaluate(plan: dict, results: dict[str, dict], release_log: list[dict], t: int,
             intents: Iterable[tuple[str, str]] = PROBE_INTENTS,
             registered_books: Iterable[str] = PROBE_REGISTERED) -> dict:
    """Score the contention from the barrier plan, the venue order records
    and the barrier's own release log lines. Mechanical; no judgement.
    `t` is the rotation tick the cycle's CycleBarrier was built with."""
    out: dict[str, Any] = {}
    entry = plan.get(SYMBOL)
    out["engaged"] = entry is not None
    out["rotation_tick"] = t
    exp = expected_winner(intents, registered_books, t)
    out["expected_winner"] = None if exp is None else {"book": exp[0], "action": exp[1]}
    if entry is None:
        out["verdict"] = ("FAILED -- the barrier did not identify the ticker as "
                          "contended; both intents submitted unmediated")
        return out
    out["winner"] = entry["winner"]
    out["held"] = entry["held"]
    out["ordering_as_registered"] = (out["expected_winner"] is not None
                                     and entry["winner"] == out["expected_winner"])

    w = results.get(entry["winner"]["book"], {})
    wo = w.get("broker_order") or {}
    out["winner_terminal_state"] = wo.get("status") or None
    out["winner_filled_at"] = wo.get("filled_at") or None
    out["winner_submitted_at"] = wo.get("submitted_at") or None

    held_books = [h["book"] for h in entry["held"]]
    released_on_terminal = {r["book"] for r in release_log if r["how"] == "terminal"}
    released_on_timeout = {r["book"] for r in release_log if r["how"] == "timeout"}
    held_out = []
    for hb in held_books:
        r = results.get(hb, {})
        ho = r.get("broker_order") or {}
        sub = parse_ts(ho.get("submitted_at"))
        term = parse_ts(wo.get("filled_at"))
        held_out.append({
            "book": hb,
            "released_on": ("terminal" if hb in released_on_terminal else
                            "timeout" if hb in released_on_timeout else "not_released"),
            "constraint": r.get("constraint") or "",
            "error": r.get("error") or "",
            "submitted_at": ho.get("submitted_at") or None,
            "terminal_state": ho.get("status") or None,
            "submitted_after_winner_terminal": (None if sub is None or term is None
                                                else sub >= term),
        })
    out["held_results"] = held_out

    if not out["ordering_as_registered"]:
        out["verdict"] = (f"FAILED -- winner {entry['winner']} is not the registered "
                          f"rule's {out['expected_winner']}")
    elif any(h["constraint"] in ("WASH_REJECT_POST_BARRIER", "WASH_TRADE_BLOCK") for h in held_out):
        out["verdict"] = "FAILED -- a held intent was rejected as a wash trade after release"
    elif any(h["released_on"] == "timeout" for h in held_out):
        out["verdict"] = ("FAILED -- a held intent released on the timeout, not on "
                          "the winner's terminal state")
    elif any(h["submitted_after_winner_terminal"] is False for h in held_out):
        out["verdict"] = "FAILED -- a held intent reached the venue before the winner was terminal"
    elif out["winner_terminal_state"] != "filled":
        out["verdict"] = (f"INCONCLUSIVE -- the winner reached {out['winner_terminal_state']!r}, "
                          f"not 'filled': {w.get('error') or w.get('thread_error') or ''}")
    elif any(h["released_on"] != "terminal" or h["submitted_after_winner_terminal"] is not True
             for h in held_out):
        out["verdict"] = f"INCONCLUSIVE -- a held intent has no venue submission to score: {held_out}"
    else:
        out["verdict"] = ("CLEARS -- barrier engaged, rule-2 winner filled, every held "
                          "intent released on that terminal state and reached the venue "
                          "after the winner's filled_at (venue timestamps)")
    return out


def assert_paper_only(client: Any) -> None:
    """Refuse anything but the Alpaca paper endpoint. A real check, not an
    `assert`, which `python -O` strips."""
    mode = getattr(client, "mode", None)
    url = str(getattr(client, "base_url", "")).rstrip("/")
    if mode != PROBE_MODE or url != PAPER_ENDPOINT:
        raise SystemExit(f"barrier probe refuses mode={mode!r} endpoint={url!r}: "
                         f"it runs against {PAPER_ENDPOINT} only, never live")


def position_explained(broker_qty: float, attributed_qty: float) -> bool:
    """Whether the venue's position in SYMBOL is exactly what the probe's
    committed records account for."""
    return abs(broker_qty - attributed_qty) < 1e-6


def raise_critical(title: str, body: str, context: dict) -> bool:
    """CRITICAL alert for a probe integrity failure (a stuck or unexplained
    position, or an unverified cleanup). Returns whether the dispatch was
    attempted without raising."""
    try:
        from src.alerts.alerter import send_alert
        send_alert("CRITICAL", title, body, context, kind="barrier_probe",
                   dedup_key=f"barrier_probe:{context.get('cycle_id')}:{title}")
        return True
    except Exception:                       # noqa: BLE001 -- logged, never masks the run
        logger.exception("CRITICAL alert dispatch failed: %s", title)
        return False


def completed_today(out_dir: Path, et_date: str) -> bool:
    path = out_dir / f"{et_date[:7]}.jsonl"
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if (r.get("phase") == "FINISHED" and r.get("outcome") == "COMPLETED"
                    and r.get("et_date") == et_date):
                return True
    return False


# ---------------------------------------------------------------------------
# Side effects
# ---------------------------------------------------------------------------
class _ReleaseCapture(logging.Handler):
    """Collects the barrier's own release log lines with their times."""
    _TERMINAL = re.compile(r"releasing (\S+) (\S+) (\S+) -- contender reached terminal")
    _TIMEOUT = re.compile(r"releasing (\S+) (\S+) (\S+) after .* without the contending")

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        for how, rx in (("terminal", self._TERMINAL), ("timeout", self._TIMEOUT)):
            m = rx.search(msg)
            if m:
                self.lines.append({"book": m.group(1), "action": m.group(2),
                                   "ticker": m.group(3), "how": how,
                                   "at": datetime.fromtimestamp(record.created, timezone.utc).isoformat()})


def append_record(out_dir: Path, record: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{record['et_date'][:7]}.jsonl"
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    return path


def _repo_slug() -> str | None:
    slug = os.getenv("GITHUB_REPOSITORY")
    if slug:
        return slug
    try:
        url = subprocess.run(["git", "-C", str(PROJECT_ROOT), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", url)
    return m.group(1) if m else None


def check_production_idle() -> tuple[bool | None, str]:
    """(idle, detail). None means it could not be verified, and the caller skips."""
    slug = _repo_slug()
    if not slug:
        return None, "cannot resolve the GitHub repository"
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    base = f"https://api.github.com/repos/{slug}/actions"

    def _get(url: str, params: dict | None = None) -> dict:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    try:
        busy, detail = production_busy(
            lambda status: _get(f"{base}/workflows/{INTRADAY_WORKFLOW}/runs",
                                {"status": status, "per_page": 20}).get("workflow_runs", []),
            lambda run: _get(run["jobs_url"]).get("jobs", []))
    except (requests.RequestException, KeyError, ValueError) as e:
        return None, f"GitHub Actions API check failed: {type(e).__name__}: {e}"
    return (not busy), detail


def _list_orders(client: BrokerClient, after: datetime) -> list[dict]:
    return client._request("GET", "/v2/orders", params={
        "status": "all", "after": after.isoformat(), "limit": 500, "direction": "asc"})


def _symbol_state(client: BrokerClient) -> tuple[list[dict], float]:
    opens = [o for o in client._request("GET", "/v2/orders", params={"status": "open", "limit": 500})
             if o.get("symbol") == SYMBOL]
    qty = sum(float(p.get("qty") or 0) for p in client.get_positions() if p.get("symbol") == SYMBOL)
    return opens, qty


def _account_view(client: BrokerClient) -> dict:
    """Configuration and cash only; no identifiers (the repo is public)."""
    a = client.get_account()
    c = client._request("GET", "/v2/account/configurations")
    return {
        "cash": float(a.get("cash") or 0.0),
        "multiplier": a.get("multiplier"),
        "shorting_enabled": a.get("shorting_enabled"),
        "options_trading_level": a.get("options_trading_level"),
        "options_approved_level": a.get("options_approved_level"),
        "no_shorting": c.get("no_shorting"),
        "max_margin_multiplier": c.get("max_margin_multiplier"),
        "max_options_trading_level": c.get("max_options_trading_level"),
        "fractional_trading": c.get("fractional_trading"),
    }


def _order_view(o: dict) -> dict:
    keep = ("client_order_id", "side", "qty", "status", "filled_qty", "filled_avg_price",
            "submitted_at", "filled_at", "canceled_at", "failed_at")
    return {k: o.get(k) for k in keep}


def run_contention(executor: Executor, seed_qty: float, price: float,
                   cycle_dt: datetime) -> tuple[dict, dict, list[dict], int]:
    """One contended cycle through the production executor and barrier.
    Returns (plan, results, release log, rotation tick t)."""
    exec_cfg = executor.settings.get("execution") or {}
    t = rotation_tick(cycle_dt)
    barrier = CycleBarrier(
        expected_books=set(PROBE_REGISTERED), t=t,
        registration_timeout=float(exec_cfg.get("barrier_registration_timeout_seconds", 300.0)),
        release_timeout=float(exec_cfg.get("barrier_release_timeout_seconds", 120.0)))
    today = cycle_dt.astimezone(ET).strftime("%Y-%m-%d")
    books = {
        SELL_BOOK: (Portfolio(model_key=SELL_BOOK, cash=0.0,
                              holdings={SYMBOL: Holding(ticker=SYMBOL, shares=seed_qty, avg_cost=price)},
                              inception_value=seed_qty * price, inception_date=today),
                    {"action": "SELL", "ticker": SYMBOL, "target_weight": 0.0, "confidence": 5,
                     "reasoning": "barrier probe: administrative sell-side intent"}),
        BUY_BOOK: (Portfolio(model_key=BUY_BOOK, cash=BUY_BOOK_CASH, holdings={},
                             inception_value=BUY_BOOK_CASH, inception_date=today),
                   {"action": "BUY", "ticker": SYMBOL, "target_weight": BUY_TARGET_WEIGHT,
                    "confidence": 5, "reasoning": "barrier probe: administrative buy-side intent"}),
    }
    capture = _ReleaseCapture()
    blog = logging.getLogger("llmlab.execution.barrier")
    blog.addHandler(capture)
    prev_level = blog.level
    blog.setLevel(logging.INFO)
    results: dict[str, dict] = {}
    lock = threading.Lock()

    def _book(name: str, pf: Portfolio, decision: dict) -> None:
        t0 = time.monotonic()
        try:
            r = executor.execute_decisions(pf, [decision], {SYMBOL: price}, barrier=barrier)[0]
            out = {"executed": r.executed, "side": r.side, "shares": r.shares,
                   "fill_price": r.fill_price, "order_id": r.order_id,
                   "constraint": r.constraint, "error": r.error,
                   "broker_order": r.broker_order}
        except BaseException as e:            # noqa: BLE001 -- recorded, never swallowed
            out = {"thread_error": f"{type(e).__name__}: {e}",
                   "traceback": traceback.format_exc()}
        out["elapsed_sec"] = round(time.monotonic() - t0, 3)
        with lock:
            results[name] = out

    threads = [threading.Thread(target=_book, args=(n, pf, d), name=f"probe-{n}")
               for n, (pf, d) in books.items()]
    try:
        for th in threads:
            th.start()
        limit = (float(exec_cfg.get("barrier_registration_timeout_seconds", 300.0))
                 + float(exec_cfg.get("barrier_release_timeout_seconds", 120.0))
                 + 2 * executor.broker.fill_deadline_seconds + 60)
        for th in threads:
            th.join(timeout=limit)
            if th.is_alive():
                results.setdefault(th.name.removeprefix("probe-"),
                                   {"thread_error": f"did not return within {limit:.0f}s"})
    finally:
        blog.removeHandler(capture)
        blog.setLevel(prev_level)
    for r in results.values():
        bo = r.get("broker_order")
        if bo:
            bo.pop("broker_order_id", None)    # not needed; keep the record minimal
    return barrier.plan(), results, capture.lines, t


def cleanup(client: BrokerClient, cycle_id: str, started: datetime,
            market_open: bool) -> dict:
    """Cancel this run's open orders and flatten this run's net SYMBOL position.
    Never touches an order or position the probe did not create. Never raises."""
    out: dict[str, Any] = {"cancelled": [], "flatten": None, "errors": []}
    try:
        for o in run_orders(client._request("GET", "/v2/orders",
                                             params={"status": "open", "limit": 500}), cycle_id):
            client.cancel_order(o["id"])
            out["cancelled"].append(o.get("client_order_id"))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and run_orders(
                client._request("GET", "/v2/orders", params={"status": "open", "limit": 500}), cycle_id):
            time.sleep(2)
    except Exception as e:                      # noqa: BLE001
        out["errors"].append(f"cancel: {type(e).__name__}: {e}")
    try:
        _, net_shares = fill_cash_and_position(run_orders(_list_orders(client, started), cycle_id))
        if net_shares > 1e-9:
            if not market_open:
                out["flatten"] = f"PENDING: {net_shares:g} {SYMBOL} held, market closed"
            else:
                order, hit = client.place_and_confirm(
                    ticker=SYMBOL, shares=net_shares, side="sell",
                    client_order_id=build_order_id(CLEANUP_BOOK, cycle_id, 0))
                out["flatten"] = {"requested": net_shares, "status": order.status,
                                  "filled_qty": order.filled_qty, "deadline_hit": hit}
        elif net_shares < -1e-9:
            out["errors"].append(f"net {net_shares:g} {SYMBOL}: the probe never sells short")
    except Exception as e:                      # noqa: BLE001
        out["errors"].append(f"flatten: {type(e).__name__}: {e}")
    try:
        opens, qty = _symbol_state(client)
        out["open_orders_in_symbol"] = len(opens)
        out["position_in_symbol"] = qty
        out["clean"] = (not opens and abs(qty) < 1e-9 and not out["errors"])
    except Exception as e:                      # noqa: BLE001
        out["errors"].append(f"verify: {type(e).__name__}: {e}")
        out["clean"] = False
    return out


def settle_record(client: Any, record: dict, cycle_id: str, started: datetime,
                  attributed: float, recovery: bool, code: int) -> int:
    """Measure what the run left in the account, record it, and alert.

    Records `residual_cash_usd` (cash) and `position_delta` (shares) for
    level 2. A position the probe still holds afterwards is recorded, so level
    2 reconciles it instead of halting, and raises CRITICAL at once. A position
    it cannot measure or explain is recorded as None (unaccounted, so level 2
    halts) and also raises CRITICAL. Returns the exit code.
    """
    try:
        mine = run_orders(_list_orders(client, started), cycle_id)
        record["orders"] = [_order_view(o) for o in mine]
        fill_cash, net = fill_cash_and_position(mine)
        others = [o for o in _list_orders(client, started)
                  if not str(o.get("client_order_id", "")).startswith(PROBE_BOOK_PREFIX)
                  and float(o.get("filled_qty") or 0) > 0]
        record["account_after"] = _account_view(client)
        _, qty_after = _symbol_state(client)
        cash_delta = round(record["account_after"]["cash"] - record["account_before"]["cash"], 6)
        quiet = not others
        record.update(fill_cash_usd=fill_cash, account_cash_delta_usd=cash_delta,
                      account_quiet=quiet, position_after=qty_after)
        # With no other fills in the window the account's own cash delta is
        # exact (it includes any fee). Otherwise only the fills are
        # attributable to the probe.
        record["residual_cash_usd"] = cash_delta if quiet else fill_cash
        record["residual_basis"] = "account_cash_delta" if quiet else "probe_fills"
        # Position: this run's own fills, cross-checked against the venue.
        # F is outside the universe, so nothing else moves it.
        if position_explained(qty_after, attributed + net):
            record["position_delta"] = {SYMBOL: net} if abs(net) > 1e-9 else {}
        else:
            record["position_delta"] = None
            record["position_error"] = (f"venue {SYMBOL} {qty_after:g} != recorded "
                                        f"{attributed:g} + this run's fills {net:g}")
    except Exception as e:                      # noqa: BLE001
        record["residual_cash_usd"] = None
        record["residual_basis"] = f"UNMEASURED: {type(e).__name__}: {e}"
        record["position_delta"] = None
        record["position_error"] = record["residual_basis"]
        code = EXIT_FAIL

    delta = record.get("position_delta")
    unclean = (not recovery) and not (record.get("cleanup") or {}).get("clean")
    if delta is None:
        held_msg = (f"The probe could not account for its {SYMBOL} position "
                    f"({record.get('position_error')}). It is unaccounted, so level-2 "
                    "reconciliation will halt every book on it.")
        stuck = True
    else:
        held = attributed + sum(delta.values())
        stuck = abs(held) > 1e-9
        held_msg = (f"The probe holds {held:g} {SYMBOL} after its run. It is recorded, so "
                    "level 2 reconciles it without halting; the next scheduled run "
                    "closes it first.")
    if stuck or unclean:
        code = EXIT_FAIL
        record["critical_alert"] = raise_critical(
            f"Barrier probe: {SYMBOL} not flat in the paper account",
            f"{held_msg} Cleanup: {record.get('cleanup')}",
            {"cycle_id": cycle_id, "position_delta": delta, "attributed_before": attributed})
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="bypass market/window/production guards; requires --out")
    ap.add_argument("--out", type=Path, default=None,
                    help="record directory (default data/barrier_probe; required with --dry-run)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.dry_run and args.out is None:
        ap.error("--dry-run must write to --out, never to data/barrier_probe")
    out_dir = args.out or PROBE_DIR

    started = datetime.now(timezone.utc)
    cycle_id = started.strftime("%Y%m%dT%H%M%SZ")
    et_date = started.astimezone(ET).strftime("%Y-%m-%d")
    record: dict[str, Any] = {
        "probe": "barrier", "cycle_id": cycle_id, "et_date": et_date, "mode": MODE,
        "dry_run": args.dry_run, "symbol": SYMBOL, "started_utc": started.isoformat(),
        "github_run_id": os.getenv("GITHUB_RUN_ID"), "residual_cash_usd": 0.0,
    }

    def finish(outcome: str, code: int) -> int:
        record.update(phase="FINISHED", outcome=outcome,
                      finished_utc=datetime.now(timezone.utc).isoformat())
        path = append_record(out_dir, record)
        logger.info("RECORD %s -> %s", outcome, path)
        logger.info("VERDICT: %s", (record.get("evaluation") or {}).get("verdict", outcome))
        return code

    # A recorded stuck position overrides the once-a-day rule: the next
    # attempt must close it, not wait for tomorrow.
    if (not args.dry_run and completed_today(out_dir, et_date)
            and not administrative_positions(MODE, out_dir)):
        logger.info("A COMPLETED probe run is already recorded for %s; nothing to do.", et_date)
        return EXIT_OK

    load_env()
    settings = copy.deepcopy(load_settings())
    settings["mode"] = MODE
    executor = Executor(cycle_id=cycle_id, settings=settings, venue_books={SELL_BOOK, BUY_BOOK})
    client = executor.broker
    assert_paper_only(client)

    clock = client._request("GET", "/v2/clock")
    market_open = bool(clock.get("is_open"))
    record["clock"] = clock
    if not args.dry_run:
        if not market_open:
            record["skip_reason"] = "market closed"
            return finish("SKIPPED", EXIT_OK)
        ok, why = window_check(started, parse_ts(clock["next_close"]))
        record["window"] = why
        if not ok:
            record["skip_reason"] = why
            return finish("SKIPPED", EXIT_OK)
        idle, why = check_production_idle()
        record["production_check"] = why
        if idle is not True:
            record["skip_reason"] = ("production not verified idle: " + why) if idle is None \
                else ("production cycle active: " + why)
            return finish("SKIPPED", EXIT_OK)

    # The venue's position in SYMBOL must be exactly what the committed probe
    # records account for: zero normally, or a recorded stuck position.
    attributed = administrative_positions(MODE, out_dir).get(SYMBOL, 0.0)
    opens, qty = _symbol_state(client)
    record["attributed_position_before"] = attributed
    if opens or not position_explained(qty, attributed):
        record["precondition"] = {"open_orders_in_symbol": [_order_view(o) for o in opens],
                                  "position_in_symbol": qty, "attributed": attributed}
        record["error"] = (f"{SYMBOL} state is not what the probe records account for "
                           f"(venue {qty:g}, recorded {attributed:g}, {len(opens)} open "
                           "orders); refusing to trade on top of it")
        record["critical_alert"] = raise_critical(
            f"Barrier probe: unexplained {SYMBOL} state in the paper account",
            record["error"] + ". Level-2 reconciliation will halt every book on an "
            "unexplained position. Identify it before the next cycle.",
            {"cycle_id": cycle_id, **record["precondition"]})
        return finish("PRECONDITION_FAILED", EXIT_FAIL)

    record["account_before"] = _account_view(client)
    record["phase"] = "STARTED"
    append_record(out_dir, record)
    code = EXIT_FAIL
    outcome = "ERROR"
    recovery = attributed > 1e-9
    try:
        if not args.dry_run and not submission_allowed(datetime.now(timezone.utc)):
            raise ProbeAbort("submission cutoff reached before the first order")
        if recovery:
            # A stuck position from an earlier run: close it, record the
            # negative delta, and leave the contention for the next attempt.
            if not market_open:
                raise ProbeAbort(f"recorded stuck position {attributed:g} {SYMBOL}; market closed")
            order, hit = client.place_and_confirm(
                ticker=SYMBOL, shares=attributed, side="sell",
                client_order_id=build_order_id(CLEANUP_BOOK, cycle_id, 0))
            record["recovery"] = {"requested": attributed, "status": order.status,
                                  "filled_qty": order.filled_qty, "deadline_hit": hit}
            outcome = ("RECOVERED" if order.filled_qty >= attributed - 1e-9
                       else "RECOVERY_INCOMPLETE")
            code = EXIT_OK if outcome == "RECOVERED" else EXIT_FAIL
        else:
            seed, _ = client.place_and_confirm(
                ticker=SYMBOL, shares=SEED_SHARES, side="buy",
                client_order_id=build_order_id(SEED_BOOK, cycle_id, 0),
                deadline_seconds=None if market_open else 10)
            record["seed"] = {"status": seed.status, "filled_qty": seed.filled_qty,
                              "filled_avg_price": seed.filled_avg_price,
                              "filled_at": seed.filled_at}
            if seed.filled_qty < SEED_SHARES or seed.filled_avg_price <= 0:
                raise ProbeAbort(f"seed did not fill completely ({seed.status}, "
                                 f"{seed.filled_qty:g}/{SEED_SHARES:g}); no contention possible")
            if not args.dry_run and not submission_allowed(datetime.now(timezone.utc)):
                raise ProbeAbort("submission cutoff reached before the contended cycle")
            plan, results, releases, t = run_contention(
                executor, seed.filled_qty, seed.filled_avg_price, datetime.now(timezone.utc))
            record.update(plan=plan, results=results, release_log=releases, rotation_tick=t)
            record["evaluation"] = evaluate(plan, results, releases, t)
            outcome = "COMPLETED"
            code = EXIT_OK if record["evaluation"]["verdict"].startswith("CLEARS") else EXIT_FAIL
    except ProbeAbort as e:
        record["error"] = str(e)
        outcome = "ABORTED"
    except Exception as e:                      # noqa: BLE001
        record["error"] = f"{type(e).__name__}: {e}"
        record["traceback"] = traceback.format_exc()
    finally:
        if not recovery:
            record["cleanup"] = cleanup(client, cycle_id, started, market_open)
        code = settle_record(client, record, cycle_id, started, attributed, recovery, code)
    return finish(outcome, code)

if __name__ == "__main__":
    raise SystemExit(main())
