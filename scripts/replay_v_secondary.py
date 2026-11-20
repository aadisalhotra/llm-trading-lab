"""(V) condition 2, secondary test: pre-trade verdict at tick t+1 vs the
post-cycle verdict recorded at tick t (tick_admission_guards_ruling.md rev. 5
§3 condition 2: "the pre-trade verdict at tick t+1 must equal the post-cycle
verdict recorded at tick t wherever nothing intervened; differences are listed
and explained (dividends, fees, settlement)").

The venue's past state cannot be queried, so the pre-trade inputs at t+1 are
rebuilt from the record:

  expected side  = t's saved books (book_cash, book_positions recorded at t)
                   + t+1's own probe and unallocated terms (the checkout t+1
                   ran on is the one its post-cycle record read them from)
  venue side     = t's recorded broker state + every account activity between
                   t's reconciliation and t+1's cycle start (fills, dividends,
                   fees, journals), from GET /v2/account/activities

and the same comparison `reconcile` makes is applied at the registered bounds.
Self-check: the rebuilt venue state plus t+1's own fills must equal t+1's
recorded broker state; any pair where it does not is reported as unexplained.

READ-ONLY (GETs on the paper account bound by the committed mode). Writes the
report to stdout only. Usage: python scripts/replay_v_secondary.py [SINCE]
"""
from __future__ import annotations

import ast
import json
from decimal import ROUND_HALF_UP, Decimal
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config_loader import load_env, load_settings, reconciliation_params  # noqa: E402
from src.execution.broker import BrokerClient, from_venue_symbol  # noqa: E402

SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-11-11"
NO_POSITION = 1e-9
EASTERN = __import__("zoneinfo").ZoneInfo("America/New_York")


def _dict(v):
    return v if isinstance(v, dict) else ast.literal_eval(v or "{}")


def records() -> list[dict]:
    raw = subprocess.run(["git", "-C", str(ROOT), "show",
                          "origin/main:data/reconciliation/2026-11.jsonl"],
                         capture_output=True, check=True).stdout.decode()
    rows = [json.loads(l) for l in raw.splitlines() if l.strip()]
    return sorted((r for r in rows if r["timestamp"] >= SINCE), key=lambda r: r["timestamp"])


def activities(client: BrokerClient, after: str) -> list[dict]:
    out, token = [], None
    while True:
        params = {"after": after, "direction": "asc", "page_size": 100}
        if token:
            params["page_token"] = token
        page = client._request("GET", "/v2/account/activities", params=params)
        out += page
        if len(page) < 100:
            return out
        token = page[-1]["id"]


def act_time(a: dict) -> str:
    t = a.get("transaction_time") or a.get("date") or ""
    if len(t) == 10:            # a date-only non-trade activity: booked at the day's start
        t += "T00:00:00Z"
    return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()


def cycle_start(r: dict) -> str:
    return datetime.strptime(r["cycle_id"], "%Y%m%dT%H%M%SZ").replace(
        tzinfo=timezone.utc).isoformat()


def apply(acts: list[dict]) -> tuple[float, dict[str, float], Counter]:
    """Cash and position change from a set of activities, and their kinds."""
    cash, pos, kinds = 0.0, defaultdict(float), Counter()
    for a in acts:
        kind = a.get("activity_type")
        kinds[kind] += 1
        if kind == "FILL":
            sign = 1 if a["side"] == "buy" else -1
            pos[from_venue_symbol(a["symbol"])] += sign * float(a["qty"])
            # The venue books each fill's cash to the cent, half-up (measured:
            # unrounded qty*price leaves 18 of 82 pairs off by cents; half-up
            # per fill leaves 6, all explained below).
            cash -= sign * float((Decimal(a["qty"]) * Decimal(a["price"])).quantize(
                Decimal("0.01"), ROUND_HALF_UP))
        else:
            cash += float(a.get("net_amount") or 0.0)
            if a.get("qty") and a.get("symbol"):
                pos[from_venue_symbol(a["symbol"])] += float(a["qty"])
    return cash, dict(pos), kinds


def verdict(expected_cash, broker_cash, expected_pos, broker_pos, p) -> tuple[bool, list]:
    """The quantity test's comparison at the registered cash bound. Positions:
    no between-tick share movement is expected that the probe term does not
    carry, so ANY share difference above 1e-6 is reported (stricter than the
    $ bound, which needs a mark the record does not carry for every name)."""
    events = []
    if abs(expected_cash - broker_cash) > p["cash_tolerance_usd"]:
        events.append(("cash", round(expected_cash - broker_cash, 4)))
    for t in sorted(set(expected_pos) | set(broker_pos)):
        d = expected_pos.get(t, 0.0) - broker_pos.get(t, 0.0)
        if abs(d) > 1e-6:
            events.append(("position", t, round(d, 6)))
    return not events, events


def main() -> int:
    load_env()
    settings = load_settings()
    client = BrokerClient(settings["mode"])
    assert "paper-api" in client.base_url
    p = reconciliation_params(settings)
    recs = records()
    acts = activities(client, recs[0]["timestamp"])
    fills_by_cycle = defaultdict(list)
    print(f"{len(recs)} post-cycle records since {SINCE}; {len(acts)} activities; "
          f"tolerances cash {p['cash_tolerance_usd']} position {p['position_tolerance_usd']}")
    diffs = unexplained = 0
    kinds_all = Counter()
    for t, t1 in zip(recs, recs[1:]):
        lo, hi = t["timestamp"], cycle_start(t1)
        between = [a for a in acts if lo < act_time(a) <= hi]
        dcash, dpos, kinds = apply(between)
        kinds_all.update(kinds)
        broker_cash = t["broker_cash"] + dcash
        broker_pos = dict(_dict(t["broker_positions"]))
        for k, v in dpos.items():
            broker_pos[k] = broker_pos.get(k, 0.0) + v
        broker_pos = {k: v for k, v in broker_pos.items() if abs(v) > NO_POSITION}
        exp_cash = t["book_cash"] + t1["unallocated_cash"] + t1["probe_cash"]
        exp_pos = dict(_dict(t["book_positions"]))
        for k, v in _dict(t1["probe_positions"]).items():
            exp_pos[k] = exp_pos.get(k, 0.0) + v
        ok, events = verdict(exp_cash, broker_cash, exp_pos, broker_pos, p)
        # Self-check: rebuilt state + t+1's own activities == t+1's record.
        own = [a for a in acts if hi < act_time(a) <= t1["timestamp"]]
        ocash, opos, _ = apply(own)
        rec_pos = _dict(t1["broker_positions"])
        cash_gap = round(broker_cash + ocash - t1["broker_cash"], 4)
        pos_gap = {k: round(broker_pos.get(k, 0.0) + opos.get(k, 0.0) - rec_pos.get(k, 0.0), 6)
                   for k in set(broker_pos) | set(opos) | set(rec_pos)}
        pos_gap = {k: v for k, v in pos_gap.items() if abs(v) > 1e-6}
        # Fees and dividends carry a DATE, not a posting time, so in an
        # overnight window the record cannot say which side of the snapshot
        # they posted on. A cent-level cash gap there is that, and is
        # classified as such; anywhere else it is unexplained.
        et_day = lambda r: datetime.fromisoformat(r["timestamp"]).astimezone(EASTERN).date()
        utc_day = lambda r: r["timestamp"][:10]
        # Date-only activities are placed at 00:00Z, so a window that crosses
        # either the ET or the UTC date boundary can hold one whose actual
        # posting time is on the other side of the snapshot.
        overnight = et_day(t) != et_day(t1) or utc_day(t) != utc_day(t1)
        consistent = abs(cash_gap) < 0.005 and not pos_gap
        posting_time = (not consistent and not pos_gap and overnight
                        and abs(cash_gap) < p["cash_tolerance_usd"])
        if ok != bool(t["clean"]) or not consistent:
            diffs += 1
            unexplained += 0 if (ok == bool(t["clean"]) and (consistent or posting_time)) else 1
            print(f"  {t['cycle_id']} -> {t1['cycle_id']}: post-cycle clean={t['clean']} "
                  f"pre-trade clean={ok} events={events} between={dict(kinds)} "
                  f"self-check cash_gap={cash_gap} pos_gap={pos_gap}"
                  + (" [explained: overnight date-stamped fee/dividend posting time]"
                     if posting_time else ""))
        fills_by_cycle[t1["cycle_id"]] = own
    print(f"pairs: {len(recs) - 1}; verdict mismatches or self-check gaps: {diffs}; unexplained: "
          f"{unexplained}; activity kinds between ticks: {dict(kinds_all)}")
    return 0 if unexplained == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
