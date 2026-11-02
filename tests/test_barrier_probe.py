"""Barrier probe: guards, scoring, scoping, and its reconciliation carve-out.

No network. The probe's venue behaviour is proven by its scheduled runs;
these tests pin the properties that keep those runs honest and harmless:

  * the window and production-idle guards decide correctly, and the
    production check keys on the `run` JOB (the chain job keeps the intraday
    run `in_progress` all session, so the run status is not the signal);
  * scoring is mechanical and returns CLEARS only when the barrier engaged,
    rule 2's winner filled, and the held intent released on that terminal
    state and reached the venue after it;
  * cleanup scoping selects only this run's FORCED_PROBE_ orders;
  * level-2 reconciliation adds the committed residual to the unallocated
    term, so probe orders never enter the cash comparison, and never hides a
    position;
  * the Executor's injection seam replaces the `__new__` bypass that killed
    the 2026-10-05 run.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import barrier_probe as bp  # noqa: E402
from src.analytics.research_metrics import _is_administrative  # noqa: E402
from src.execution import administrative as adm  # noqa: E402
from src.execution import reconciliation as rec  # noqa: E402
from src.execution.executor import Executor  # noqa: E402
from src.portfolio.portfolio import Holding, Portfolio  # noqa: E402

ET = bp.ET


def et(h, m):
    return datetime(2026, 11, 3, h, m, 0, tzinfo=ET)


CLOSE = datetime(2026, 11, 3, 16, 0, tzinfo=ET)


# ------------------------------------------------------------------ timestamps
def test_parse_ts_handles_alpaca_nanoseconds_and_z():
    a = bp.parse_ts("2026-11-02T17:53:14.518021455-05:00")
    assert a == datetime(2026, 11, 2, 22, 53, 14, 518021, tzinfo=timezone.utc)
    assert bp.parse_ts("2026-11-03T15:38:01.1Z").tzinfo is not None
    assert bp.parse_ts("") is None


# ---------------------------------------------------------------------- window
@pytest.mark.parametrize("h,m,ok", [
    (10, 38, True), (10, 36, True), (10, 48, True), (12, 8, True),
    (10, 35, False), (10, 49, False), (10, 0, False), (10, 30, False), (10, 2, False),
    (15, 38, False),   # 22 min to the close
])
def test_window_check(h, m, ok):
    assert bp.window_check(et(h, m), CLOSE)[0] is ok


def test_scheduled_crons_land_inside_the_window_in_both_dst_regimes():
    # Read from the workflow itself, so the schedule and this check cannot
    # drift apart. Each cron (UTC) must open the window under EST and EDT alike.
    import re
    wf = (Path(__file__).resolve().parent.parent / ".github" / "workflows"
          / "barrier_probe.yml").read_text(encoding="utf-8")
    crons = [(int(h), int(m)) for m, h in re.findall(r'cron:\s*"(\d+) (\d+) \* \* 1-5"', wf)]
    assert len(crons) == 2
    for utc_h, utc_m in crons:
        for month in (1, 7):   # EST, EDT
            now = datetime(2026, month, 6, utc_h, utc_m, tzinfo=timezone.utc)
            close = now.astimezone(ET).replace(hour=16, minute=0)
            assert bp.window_check(now, close)[0], (utc_h, utc_m, month)


def test_submission_cutoff():
    assert bp.submission_allowed(et(10, 54))
    assert not bp.submission_allowed(et(10, 55))
    assert not bp.submission_allowed(et(10, 25))


# ------------------------------------------------------------ production guard
def _runs(by_status):
    return lambda status: by_status.get(status, [])


def test_chain_job_in_progress_is_not_busy():
    runs = {"in_progress": [{"id": 1}]}
    jobs = lambda run: [{"name": "run", "status": "completed"},
                        {"name": "chain", "status": "in_progress"}]
    busy, _ = bp.production_busy(_runs(runs), jobs)
    assert busy is False


def test_run_job_in_progress_is_busy():
    runs = {"in_progress": [{"id": 1}]}
    jobs = lambda run: [{"name": "run", "status": "in_progress"}]
    assert bp.production_busy(_runs(runs), jobs)[0] is True


def test_queued_intraday_run_is_busy():
    assert bp.production_busy(_runs({"queued": [{"id": 9}]}), lambda r: [])[0] is True


def test_production_check_fails_closed(monkeypatch):
    def boom(*a, **k):
        raise bp.requests.ConnectionError("down")
    monkeypatch.setattr(bp, "_repo_slug", lambda: "o/r")
    monkeypatch.setattr(bp.requests, "get", boom)
    idle, why = bp.check_production_idle()
    assert idle is None and "failed" in why


# --------------------------------------------------------------------- scoping
def test_run_orders_selects_only_this_runs_probe_orders():
    cyc = "20261103T153801Z"
    orders = [
        {"client_order_id": f"FORCED_PROBE_SEED-{cyc}-00-a0"},
        {"client_order_id": f"FORCED_PROBE_BUY-{cyc}-00-a0"},
        {"client_order_id": "FORCED_PROBE_SEED-20261102T153801Z-00-a0"},   # other run
        {"client_order_id": f"claude-{cyc}-00-a0"},                         # a book
        {"client_order_id": "FORCED_BOUNDARY_ADMIN"},
    ]
    got = [o["client_order_id"] for o in bp.run_orders(orders, cyc)]
    assert got == [f"FORCED_PROBE_SEED-{cyc}-00-a0", f"FORCED_PROBE_BUY-{cyc}-00-a0"]


def test_fill_cash_and_position_round_trip():
    orders = [
        {"side": "buy", "filled_qty": "10", "filled_avg_price": "11.00"},   # seed
        {"side": "sell", "filled_qty": "10", "filled_avg_price": "10.99"},  # SELL book
        {"side": "buy", "filled_qty": "9.0991", "filled_avg_price": "11.00"},  # BUY book
        {"side": "sell", "filled_qty": "9.0991", "filled_avg_price": "10.99"},  # cleanup
        {"side": "buy", "filled_qty": "0", "filled_avg_price": None},      # cancelled
    ]
    cash, shares = bp.fill_cash_and_position(orders)
    assert shares == 0
    assert cash == pytest.approx(-0.01 * 10 - 0.01 * 9.0991)


def test_probe_order_ids_are_administrative_and_censored():
    from src.execution.broker import build_order_id, book_from_order_id
    for book in (bp.SEED_BOOK, bp.SELL_BOOK, bp.BUY_BOOK, bp.CLEANUP_BOOK):
        oid = build_order_id(book, "20261103T153801Z", 0)
        assert book_from_order_id(oid) == book
        assert _is_administrative({"order_id": oid})
        assert adm.is_administrative_order_id(oid)


def test_probe_symbol_is_outside_the_trading_universe():
    universe = json.loads((Path(__file__).resolve().parent.parent / "config" / "universe.json")
                          .read_text(encoding="utf-8"))
    assert f'"{bp.SYMBOL}"' not in json.dumps(universe)


# --------------------------------------------------------------------- scoring
def _plan(winner_book=None, released=True):
    wb = winner_book or bp.SELL_BOOK
    wa = "SELL" if wb == bp.SELL_BOOK else "BUY"
    hb = bp.BUY_BOOK if wb == bp.SELL_BOOK else bp.SELL_BOOK
    ha = "BUY" if wa == "SELL" else "SELL"
    return {bp.SYMBOL: {"winner": {"book": wb, "action": wa},
                        "held": [{"book": hb, "action": ha, "released": released}]}}


def _results(win_status="filled", win_filled="2026-11-03T15:38:05.100Z",
             held_sub="2026-11-03T15:38:07.300Z", held_constraint=""):
    return {
        bp.SELL_BOOK: {"constraint": "", "broker_order": {
            "status": win_status, "filled_at": win_filled if win_status == "filled" else "",
            "submitted_at": "2026-11-03T15:38:04.900Z"}},
        bp.BUY_BOOK: {"constraint": held_constraint, "broker_order": {
            "status": "filled", "submitted_at": held_sub, "filled_at": held_sub}},
    }


TERMINAL_RELEASE = [{"book": bp.BUY_BOOK, "action": "BUY", "ticker": "F", "how": "terminal"}]


def test_clears_on_the_registered_path():
    ev = bp.evaluate(_plan(), _results(), TERMINAL_RELEASE)
    assert ev["verdict"].startswith("CLEARS")
    assert ev["engaged"] and ev["ordering_as_registered"]
    assert ev["winner_terminal_state"] == "filled"
    assert ev["held_results"][0]["submitted_after_winner_terminal"] is True


def test_not_engaged_fails():
    assert bp.evaluate({}, _results(), [])["verdict"].startswith("FAILED")


def test_wrong_winner_fails():
    ev = bp.evaluate(_plan(winner_book=bp.BUY_BOOK), _results(), TERMINAL_RELEASE)
    assert ev["verdict"].startswith("FAILED") and not ev["ordering_as_registered"]


def test_held_submitted_before_winner_terminal_fails():
    ev = bp.evaluate(_plan(), _results(held_sub="2026-11-03T15:38:05.000Z"), TERMINAL_RELEASE)
    assert ev["verdict"].startswith("FAILED")


def test_timeout_release_fails():
    rel = [dict(TERMINAL_RELEASE[0], how="timeout")]
    assert bp.evaluate(_plan(), _results(), rel)["verdict"].startswith("FAILED")


def test_post_barrier_wash_rejection_fails():
    ev = bp.evaluate(_plan(), _results(held_constraint="WASH_REJECT_POST_BARRIER"),
                     TERMINAL_RELEASE)
    assert ev["verdict"].startswith("FAILED")


def test_winner_not_filled_is_inconclusive():
    ev = bp.evaluate(_plan(), _results(win_status="rejected"), TERMINAL_RELEASE)
    assert ev["verdict"].startswith("INCONCLUSIVE")


def test_completed_today_only_counts_completed_finished_records(tmp_path):
    p = tmp_path / "2026-11.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {"phase": "STARTED", "et_date": "2026-11-03"},
        {"phase": "FINISHED", "outcome": "SKIPPED", "et_date": "2026-11-03"},
    ]) + "\n", encoding="utf-8")
    assert not bp.completed_today(tmp_path, "2026-11-03")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"phase": "FINISHED", "outcome": "COMPLETED", "et_date": "2026-11-03"}) + "\n")
    assert bp.completed_today(tmp_path, "2026-11-03")
    assert not bp.completed_today(tmp_path, "2026-11-04")


# ------------------------------------------------- reconciliation carve-out
def _write(tmp_path, records):
    d = tmp_path / "barrier_probe"
    d.mkdir()
    (d / "2026-11.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records),
                                     encoding="utf-8")
    return d


def test_administrative_cash_sums_finished_records_for_the_mode(tmp_path):
    d = _write(tmp_path, [
        {"phase": "STARTED", "mode": "broker_paper", "residual_cash_usd": -9.0},
        {"phase": "FINISHED", "mode": "broker_paper", "residual_cash_usd": -0.19},
        {"phase": "FINISHED", "mode": "broker_paper", "residual_cash_usd": 0.0},
        {"phase": "FINISHED", "mode": "broker_paper", "residual_cash_usd": None},
        {"phase": "FINISHED", "mode": "broker_paper", "residual_cash_usd": 0.03},
        {"phase": "FINISHED", "mode": "live", "residual_cash_usd": 5.0},
    ])
    assert adm.administrative_cash_usd("broker_paper", d) == pytest.approx(-0.16)
    assert adm.administrative_cash_usd("live", d) == 5.0
    assert adm.administrative_cash_usd("broker_paper", tmp_path / "absent") == 0.0


def test_malformed_probe_record_raises_rather_than_guessing(tmp_path):
    d = tmp_path / "barrier_probe"
    d.mkdir()
    (d / "2026-11.jsonl").write_text('{"phase": "FINISHED"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        adm.administrative_cash_usd("broker_paper", d)


class _Broker:
    def __init__(self, cash, positions=()):
        self.cash, self.positions = cash, list(positions)

    def get_account(self):
        return {"cash": str(self.cash)}

    def get_positions(self):
        return [{"symbol": s, "qty": str(q)} for s, q in self.positions]


def _book(cash):
    return Portfolio(model_key="claude", cash=cash, holdings={})


def test_residual_is_carved_out_of_the_cash_comparison():
    # 25 probe runs at -$0.05 each = -$1.25 of residual: past the $1.00 tolerance.
    broker = _Broker(2000.0 + 89999.99 - 1.25)
    without = rec.reconcile([_book(2000.0)], broker, "c1", cash_tolerance_usd=1.0,
                            unallocated_cash_usd=89999.99)
    assert not without.clean           # what level 2 would report with no carve-out
    with_admin = rec.reconcile([_book(2000.0)], broker, "c1", cash_tolerance_usd=1.0,
                               unallocated_cash_usd=89999.99, administrative_cash_usd=-1.25)
    assert with_admin.clean and with_admin.to_dict()["administrative_cash"] == -1.25


def test_residual_never_excuses_a_stranded_probe_position():
    broker = _Broker(2000.0 + 89999.99, positions=[("F", 10.0)])
    r = rec.reconcile([_book(2000.0)], broker, "c1", cash_tolerance_usd=1.0,
                      unallocated_cash_usd=89999.99, administrative_cash_usd=0.0)
    assert [e.ticker for e in r.events] == ["F"]


def test_post_cycle_hook_reads_the_committed_residual(tmp_path, monkeypatch):
    from src import config_loader
    import src.portfolio.portfolio as pf
    import src.alerts.alerter as alerter
    d = _write(tmp_path, [{"phase": "FINISHED", "mode": "broker_paper", "residual_cash_usd": -1.5}])
    monkeypatch.setattr(adm, "PROBE_DIR", d)
    monkeypatch.setattr(rec, "RECONCILIATION_DIR", tmp_path / "rec")
    monkeypatch.setattr(rec, "save_portfolio", lambda p: None)
    monkeypatch.setattr(alerter, "send_alert", lambda *a, **k: None)
    monkeypatch.setattr(pf, "load_portfolio", lambda k: _book(2000.0))
    monkeypatch.setattr(config_loader, "load_settings", lambda: {
        "mode": "broker_paper", "reconciliation": {
            "cash_tolerance_usd": 1.0, "unallocated_cash_usd": {"broker_paper": 89999.99}}})
    r = rec.run_post_cycle_reconciliation(_Broker(2000.0 + 89999.99 - 1.5), "c9", ["claude"])
    assert r.clean and r.administrative_cash == -1.5


# --------------------------------------------------------------- executor seam
def test_executor_seam_builds_the_broker_path_without_new(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_KEY", "k")
    monkeypatch.setenv("ALPACA_PAPER_SECRET", "s")
    settings = {"mode": "broker_paper", "execution": {"fill_deadline_seconds": 300}}
    e = Executor(cycle_id="20261103T153801Z", settings=settings,
                 venue_books={bp.SELL_BOOK, bp.BUY_BOOK})
    assert e.on_venue(bp.SELL_BOOK) and e.on_venue(bp.BUY_BOOK)
    assert not e.on_venue("claude")
    assert "paper-api" in e.broker.base_url


def test_executor_default_construction_unchanged():
    e = Executor()
    assert e.mode == e.settings["mode"]
    if e.mode == "paper":
        assert e.broker is None and e.venue_books == frozenset()
