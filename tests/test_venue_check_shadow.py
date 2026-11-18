"""(V), the pre-trade venue check, in shadow (tick_admission_guards_ruling.md
rev. 5 section 2; section 3 condition 3).

Pins: the id test accounts a cycle only by THIS book's log or a ledgered
repair; the window opens at the book's last committed cycle and never advances
past an unaccounted order; the scope rule; venue unreachable; one verdict per
venue book with the count examined; the rows never land in the withhold-event
file the slot watcher reads; and the check never raises into the pipeline.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import cycle_watch
from src.logging import venue_check as vc

NOW = datetime(2026, 11, 19, 15, 0, 5, tzinfo=timezone.utc)


def _order(book, cycle, seq=0, status="filled", symbol="KO"):
    return {"id": f"{book}{cycle}{seq}", "client_order_id": f"{book}-{cycle}-{seq:02d}-a0",
            "symbol": symbol, "side": "buy", "status": status, "filled_qty": "1",
            "submitted_at": cycle[:4] + "-" + cycle[4:6] + "-" + cycle[6:8] + "T"
                            + cycle[9:11] + ":" + cycle[11:13] + ":" + cycle[13:15] + "Z"}


class _Broker:
    def __init__(self, orders, opens=(), down=False):
        self.orders, self.opens, self.down = list(orders), list(opens), down
        self.calls = []

    def _request(self, method, path, params=None, body=None):
        self.calls.append(params)
        if self.down:
            raise ConnectionError("venue down")
        if (params or {}).get("status") == "open":
            return self.opens
        return self.orders


def _logs(tmp_path, rows_by_book, month="2026-11"):
    d = tmp_path / "trades"
    d.mkdir(exist_ok=True)
    for book, cycles in rows_by_book.items():
        with open(d / f"{book}_{month}.jsonl", "w", encoding="utf-8") as f:
            for c in cycles:
                f.write(json.dumps({"cycle_id": c, "model_key": book}) + "\n")
    return d


def _run(tmp_path, broker, books, *, q_clean=True, repairs=None, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(vc, "_quantity_test", lambda *a: SimpleNamespace(
            clean=q_clean, events=[] if q_clean else [SimpleNamespace(
                to_dict=lambda: {"kind": "position", "ticker": "KO"})]))
    rp = tmp_path / "repairs.jsonl"
    if repairs:
        rp.write_text("".join(json.dumps(r) + "\n" for r in repairs), encoding="utf-8")
    return vc.run_check(broker, books, NOW, scope=vc.SCOPE_INTRADAY,
                        trades_dir=tmp_path / "trades", repairs_path=rp,
                        verdict_dir=tmp_path / "vc")


C1, C2, C3 = "20261119T143005Z", "20261119T150005Z", "20261119T143010Z"


def test_cycle_of():
    assert vc.cycle_of("claude_opus-20261119T143005Z-03-a1") == ("claude_opus", C1)
    assert vc.cycle_of("FORCED_PROBE_BUY-20261118T153801Z-00-a0")[0] == "FORCED_PROBE_BUY"
    assert vc.cycle_of("PAPER_BUY_KO_20261119") is None
    assert vc.cycle_of("gpt-notacycle-00-a0") is None


def test_clean_tick_proceeds_with_one_verdict_per_book(tmp_path, monkeypatch):
    _logs(tmp_path, {"gpt": [C1], "grok": [C1]})
    rows = _run(tmp_path, _Broker([_order("gpt", C1), _order("grok", C1)]),
                ["gpt", "grok"], monkeypatch=monkeypatch)
    assert [r["book"] for r in rows] == ["gpt", "grok"]
    assert all(r["verdict"] == vc.PROCEED and r["orders_examined"] == 1 for r in rows)
    assert all(r["shadow"] and not r["enforced"] for r in rows)


def test_another_books_row_does_not_account_for_the_order(tmp_path, monkeypatch):
    """The fourth route: the run committed (gpt has C2), grok's row was lost."""
    _logs(tmp_path, {"gpt": [C1, C2], "grok": [C1]})
    rows = _run(tmp_path, _Broker([_order("gpt", C2), _order("grok", C2)]),
                ["gpt", "grok"], monkeypatch=monkeypatch)
    by = {r["book"]: r for r in rows}
    assert by["gpt"]["verdict"] == vc.PROCEED
    assert by["grok"]["would_withhold_class"] == vc.BOOK_WITHHELD_UNACCOUNTED_ORDERS
    assert [u["cycle_id"] for u in by["grok"]["unaccounted"]] == [C2]


def test_quantity_failure_would_withhold_every_venue_book(tmp_path, monkeypatch):
    _logs(tmp_path, {"gpt": [C1], "grok": [C1]})
    rows = _run(tmp_path, _Broker([_order("grok", C2)]), ["gpt", "grok"],
                q_clean=False, monkeypatch=monkeypatch)
    assert {r["would_withhold_class"] for r in rows} == {vc.TICK_WITHHELD_BOOKS_DIVERGED}
    assert all(r["books_to_repair"] == ["grok"] for r in rows)   # the id names the book


def test_unreachable_venue_would_withhold_every_venue_book(tmp_path, monkeypatch):
    _logs(tmp_path, {"gpt": [C1]})
    rows = _run(tmp_path, _Broker([], down=True), ["gpt"], monkeypatch=monkeypatch)
    (r,) = rows
    assert r["would_withhold_class"] == vc.TICK_WITHHELD_VENUE_UNREACHABLE
    assert r["venue_reachable"] is False and "venue down" in r["venue_error"]


def test_only_a_ledgered_repair_accounts(tmp_path, monkeypatch):
    _logs(tmp_path, {"grok": [C1]})
    broker = _Broker([_order("grok", C2)])
    unledgered = {"book": "grok", "cycle_id": C2, "order_ids": []}
    rows = _run(tmp_path, broker, ["grok"], repairs=[unledgered], monkeypatch=monkeypatch)
    assert rows[0]["verdict"] == vc.WOULD_WITHHOLD
    ledgered = dict(unledgered, ledger_entry="repair_grok_x")
    rows = _run(tmp_path, broker, ["grok"], repairs=[ledgered], monkeypatch=monkeypatch)
    assert rows[0]["verdict"] == vc.PROCEED


def test_window_never_advances_past_an_unaccounted_order(tmp_path, monkeypatch):
    """Shadow does not withhold, so the book trades on and its log moves past
    the lost cycle. The carried finding keeps the window open at it."""
    _logs(tmp_path, {"grok": [C1]})
    broker = _Broker([_order("grok", C3), _order("grok", C2)])
    rows = _run(tmp_path, broker, ["grok"], monkeypatch=monkeypatch)
    assert rows[0]["unaccounted"][0]["cycle_id"] in (C2, C3)
    vc.record_rows(rows, directory=tmp_path / "vc")
    # Next tick: the log now holds a later cycle, so the last committed cycle
    # alone would open the window after the lost order.
    later = "20261119T153005Z"
    _logs(tmp_path, {"grok": [C1, C2, later]})
    rows = _run(tmp_path, broker, ["grok"], monkeypatch=monkeypatch)
    assert rows[0]["window_start_cycle"] == C3
    assert [u["cycle_id"] for u in rows[0]["unaccounted"]] == [C3]


def test_an_unreachable_row_does_not_erase_carried_findings(tmp_path, monkeypatch):
    d = tmp_path / "vc"
    d.mkdir()
    (d / "2026-11.jsonl").write_text(
        json.dumps({"book": "grok", "unaccounted": [{"cycle_id": C3}], "venue_reachable": True})
        + "\n" + json.dumps({"book": "grok", "unaccounted": [], "venue_reachable": False}) + "\n",
        encoding="utf-8")
    assert vc.prior_unaccounted("grok", ["2026-10", "2026-11"], d) == {C3}


def test_month_rollover_finds_the_prior_months_cycles(tmp_path, monkeypatch):
    """Demonstration (vii): first tick of December, last cycle in November."""
    dec = datetime(2026, 12, 1, 14, 35, tzinfo=timezone.utc)
    nov_last = "20261130T205005Z"
    _logs(tmp_path, {"gpt": [nov_last]}, month="2026-11")
    monkeypatch.setattr(vc, "_quantity_test", lambda *a: SimpleNamespace(clean=True, events=[]))
    rows = vc.run_check(_Broker([_order("gpt", nov_last)]), ["gpt"], dec,
                        scope=vc.SCOPE_INTRADAY, trades_dir=tmp_path / "trades",
                        repairs_path=tmp_path / "none.jsonl", verdict_dir=tmp_path / "vc")
    assert rows[0]["verdict"] == vc.PROCEED and rows[0]["window_start_cycle"] == nov_last


def test_probe_orders_are_not_any_books(tmp_path, monkeypatch):
    _logs(tmp_path, {"gpt": [C1]})
    rows = _run(tmp_path, _Broker([_order("FORCED_PROBE_BUY", C2), _order("gpt", C1)]),
                ["gpt"], monkeypatch=monkeypatch)
    assert rows[0]["verdict"] == vc.PROCEED and rows[0]["orders_examined"] == 1


def test_open_orders_are_counted_and_named(tmp_path, monkeypatch):
    _logs(tmp_path, {"gpt": [C1]})
    rows = _run(tmp_path, _Broker([], opens=[_order("gpt", C1, status="accepted")]),
                ["gpt"], monkeypatch=monkeypatch)
    assert rows[0]["open_orders_count"] == 1 and rows[0]["verdict"] == vc.PROCEED
    assert rows[0]["open_order_ids"] == [f"gpt-{C1}-00-a0"]


def test_rows_go_to_their_own_file_never_the_withhold_event_file(tmp_path):
    rows = [{"timestamp": NOW.isoformat(), "book": "gpt", "verdict": vc.WOULD_WITHHOLD,
             "would_withhold_class": vc.BOOK_WITHHELD_UNACCOUNTED_ORDERS}]
    vc.record_rows(rows, directory=tmp_path / "venue_check")
    assert (tmp_path / "venue_check" / "2026-11.jsonl").exists()
    assert vc.venue_check_path(NOW).parent.name == "venue_check"
    # And even if one were read as a withhold line, it carries no TICK_WITHHELD event.
    line = json.dumps(rows[0])
    slot = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
    assert cycle_watch.withhold_in_slot([line], slot) is None


def test_slot_watcher_skips_eod_scope_withholds():
    slot = datetime(2026, 11, 19, 20, 30, tzinfo=timezone.utc)
    row = {"event": "TICK_WITHHELD_GUARD_UNREADABLE", "scope": "EOD",
           "timestamp": "2026-11-19T20:45:00+00:00"}
    assert cycle_watch.withhold_in_slot([json.dumps(row)], slot) is None
    row["scope"] = "intraday"
    assert cycle_watch.withhold_in_slot([json.dumps(row)], slot) is not None


def test_shadow_entry_point_never_raises_and_records_check_errors(tmp_path, monkeypatch):
    recorded, alerts = [], []
    monkeypatch.setattr(vc, "record_rows", lambda rows, **k: recorded.extend(rows) or True)
    monkeypatch.setattr(vc, "run_check", lambda *a, **k: 1 / 0)
    import src.alerts as alerts_mod
    monkeypatch.setattr(alerts_mod, "send_alert", lambda *a, **k: alerts.append(a[1]))
    rows = vc.run_shadow_check({"mode": "broker_paper"}, ["gpt", "grok"], NOW,
                               scope=vc.SCOPE_INTRADAY, broker=object())
    assert [r["book"] for r in rows] == ["gpt", "grok"]
    assert all("ZeroDivisionError" in r["check_error"] for r in rows) and recorded == rows
    assert alerts == ["(V) shadow check did not run"]


def test_would_withhold_pages_critical_but_changes_nothing(tmp_path, monkeypatch):
    sent = []
    import src.alerts as alerts_mod
    monkeypatch.setattr(alerts_mod, "send_alert", lambda sev, title, *a, **k: sent.append((sev, title)))
    monkeypatch.setattr(vc, "record_rows", lambda rows, **k: True)
    monkeypatch.setattr(vc, "run_check", lambda *a, **k: [
        {"book": "grok", "verdict": vc.WOULD_WITHHOLD,
         "would_withhold_class": vc.BOOK_WITHHELD_UNACCOUNTED_ORDERS,
         "unaccounted": [{"client_order_id": "grok-x-00-a0"}], "open_orders_count": 0,
         "orders_examined": 1}])
    vc.run_shadow_check({"mode": "broker_paper"}, ["grok"], NOW, scope="intraday", broker=object())
    assert sent == [("CRITICAL", "(V) shadow would withhold: BOOK_WITHHELD_UNACCOUNTED_ORDERS")]


def test_pipeline_runs_v_before_the_fetch_start_stamp():
    """Admission point: ahead of the fetch-start stamp (and so of every
    model's stop sweep, which runs inside the model loop after the fetch)."""
    src = (Path(__file__).resolve().parent.parent / "src" / "pipeline.py").read_text(encoding="utf-8")
    body = src[src.index("def run_pipeline("):]
    assert body.index("run_shadow_check(") < body.index("fetch_started_at = datetime.now")
    assert body.index("EOD admission (tick_admission_guards_ruling.md") < body.index(
        "fetch_started_at = datetime.now")
