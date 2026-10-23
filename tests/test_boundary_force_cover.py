"""Simulator test for scripts/boundary_force_cover.py (v3->v4 boundary cover).

Runs the real Executor / Portfolio / decision-log code against a temp data root
seeded with a Gemini-shaped book holding two shorts. Nothing touches data/.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from scripts import boundary_force_cover as bfc
from src.analytics.research_metrics import _is_administrative
from src.config_loader import load_settings
from src.logging import decision_log
from src.portfolio import portfolio as portfolio_mod

WHEN = datetime(2026, 10, 19, 13, 31, tzinfo=timezone.utc)  # 09:31 ET Monday
PRICES = {"PEP": 131.0, "NFLX": 72.0, "EOG": 150.0, "SPY": 600.0}


def _state(**over):
    d = {
        "model_key": "gemini", "cash": 40000.0, "halted": False,
        "inception_value": 100000.0, "inception_date": "2026-04-09",
        "last_updated": "2026-10-16 19:34:18",
        "holdings": {
            "EOG": {"shares": 59.1098, "avg_cost": 145.93},
            "PEP": {"shares": -99.9598, "avg_cost": 130.86},
            "NFLX": {"shares": -94.0503, "avg_cost": 70.75},
        },
        "intraday": {"session_date": "2026-10-16", "trades_executed_today": 7,
                     "runs_today": 12, "first_run_at": "", "last_run_at": ""},
        "unsettled_cash": [], "inception_epoch": "paper",
    }
    d.update(over)
    return d


@pytest.fixture
def sim(tmp_path, monkeypatch):
    """Redirect writes to a temp root (monkeypatch restores the real dirs)."""
    for mod, name in [(portfolio_mod, "STATE_DIR"), (decision_log, "TRADES_DIR"),
                      (decision_log, "INTRADAY_DIR")]:
        monkeypatch.setattr(mod, name, getattr(mod, name))  # register restore
    bfc.redirect_data_root(tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "gemini.json").write_text(json.dumps(_state()))
    # The real settings flip to v4 at the landing; this test is about the v3 cover.
    s = dict(load_settings(), prompt_version="v3", mode="paper")
    monkeypatch.setattr(bfc, "load_settings", lambda: s)
    return tmp_path


def _run(execute=True, tickers=None, prices=PRICES):
    return bfc.run("gemini", tickers, dict(prices), execute, WHEN, "deadbeef")


def test_dry_run_writes_nothing(sim):
    before = (sim / "state" / "gemini.json").read_text()
    out = _run(execute=False)
    assert out["status"] == "DRY_RUN" and [p["ticker"] for p in out["plan"]] == ["NFLX", "PEP"]
    assert (sim / "state" / "gemini.json").read_text() == before
    assert not (sim / "trades").exists()


def test_both_shorts_cover_with_cover_not_sell(sim):
    out = _run()
    assert [(f["ticker"], f["side"]) for f in out["fills"]] == [("NFLX", "COVER"), ("PEP", "COVER")]
    assert all(f["side"] != "SELL" for f in out["fills"])
    assert {f["order_id"] for f in out["fills"]} == {"FORCED_BOUNDARY_ADMIN"}
    assert out["fills"][1]["shares"] == pytest.approx(99.9598)
    assert out["fills"][0]["fill_price"] == 72.0


def test_book_persists_like_a_normal_cycle(sim):
    _run()
    saved = json.loads((sim / "state" / "gemini.json").read_text())
    assert set(saved["holdings"]) == {"EOG"}            # shorts swept off the book
    cost = 99.9598 * 131.0 + 94.0503 * 72.0
    assert saved["cash"] == pytest.approx(40000.0 - cost)
    # Same schema a normal save writes, and not a model run:
    assert set(saved) == set(_state())
    # New session day (10-16 -> 10-19) resets counters exactly as a normal cycle
    # does; the cover itself consumes no run slot and no trade-cap slot.
    assert saved["intraday"]["session_date"] == "2026-10-19"
    assert saved["intraday"]["runs_today"] == 0
    assert saved["intraday"]["trades_executed_today"] == 0
    assert saved["inception_epoch"] == "paper"
    p = portfolio_mod.load_portfolio("gemini")           # round-trips through the real loader
    assert not any(h.is_short for h in p.holdings.values())


def test_fills_logged_in_standard_shape_and_censored(sim):
    _run()
    rec = json.loads((sim / "trades" / "gemini_2026-10.jsonl").read_text().splitlines()[-1])
    assert rec["provider"] == "administrative" and rec["raw_decisions"] == []
    assert rec["prompt_version"] == "v3" and rec["execution_mode"] == "paper"
    ex = rec["executions"]
    assert [(e["ticker"], e["side"], e["direction"], e["executed"]) for e in ex] == [
        ("NFLX", "COVER", "short", True), ("PEP", "COVER", "short", True)]
    assert all(e["order_id"] == "FORCED_BOUNDARY_ADMIN" for e in ex)
    assert all(_is_administrative(e) for e in ex)         # censored from behavioral metrics
    assert (sim / "intraday" / "gemini_2026-10-19.jsonl").exists()


def test_capture_records_pre_post_and_pnl(sim):
    out = _run()
    cap = json.loads((sim / "boundary" / "force_cover_2026-10-19_gemini.json").read_text())
    assert cap == json.loads(json.dumps(out))
    assert cap["pre"]["num_shorts"] == 2 and cap["post"]["num_shorts"] == 0
    pnl = {r["ticker"]: r["pnl"] for r in cap["realized_pnl"]}
    assert pnl["PEP"] == pytest.approx((130.86 - 131.0) * 99.9598)
    assert pnl["NFLX"] == pytest.approx((70.75 - 72.0) * 94.0503)


def test_second_cover_same_session_never_overwrites_the_first(sim):
    """2026-10-23: the cycle after the first sweep reopened shorts; the second
    run must add a record beside the first, not replace it."""
    _run()
    first = (sim / "boundary" / "force_cover_2026-10-19_gemini.json").read_bytes()
    state = json.loads((sim / "state" / "gemini.json").read_text())
    state["holdings"]["PEP"] = {"shares": -10.0, "avg_cost": 131.5}
    (sim / "state" / "gemini.json").write_text(json.dumps(state))
    out = _run()
    assert out["status"] == "EXECUTED"
    assert (sim / "boundary" / "force_cover_2026-10-19_gemini.json").read_bytes() == first
    second = json.loads((sim / "boundary" / "force_cover_2026-10-19_gemini_2.json").read_text())
    assert [f["ticker"] for f in second["fills"]] == ["PEP"]


def test_write_capture_suffixes_past_every_existing_record(tmp_path):
    for name in ("r.json", "r_2.json"):
        (tmp_path / name).write_text("keep")
    assert bfc.write_capture(tmp_path, "r", {"x": 1}).name == "r_3.json"
    assert (tmp_path / "r.json").read_text() == "keep"
    assert (tmp_path / "r_2.json").read_text() == "keep"


def test_idempotent_second_run_is_a_noop(sim):
    _run()
    assert _run()["status"] == "NOTHING_TO_DO"


def test_refuses_a_long_ticker_and_writes_nothing(sim):
    before = (sim / "state" / "gemini.json").read_text()
    with pytest.raises(bfc.ForceCoverRefused, match="not open shorts"):
        _run(tickers=["EOG"])
    assert (sim / "state" / "gemini.json").read_text() == before


def test_refuses_without_a_price(sim):
    with pytest.raises(bfc.ForceCoverRefused, match="no price"):
        _run(prices={"PEP": 131.0, "EOG": 150.0, "SPY": 600.0})


def test_refuses_once_prompt_is_v4(sim, monkeypatch):
    s = dict(bfc.load_settings(), prompt_version="v4")
    monkeypatch.setattr(bfc, "load_settings", lambda: s)
    with pytest.raises(bfc.ForceCoverRefused, match="already v4"):
        _run()


def test_refuses_a_venue_book(sim, monkeypatch):
    class _Venue:
        cycle_id = "x"
        def on_venue(self, book): return True
    monkeypatch.setattr(bfc, "Executor", _Venue)
    with pytest.raises(bfc.ForceCoverRefused, match="venue"):
        _run()


def test_failed_cover_leaves_the_book_untouched(sim, monkeypatch):
    from src.execution.executor import Executor
    real = Executor.force_liquidate
    def bad(self, portfolio, tickers, prices, reason="RISK_STOP", run_date=None):
        res = real(self, portfolio, tickers, prices, reason, run_date)
        res[0].executed = False
        return res
    monkeypatch.setattr(Executor, "force_liquidate", bad)
    before = (sim / "state" / "gemini.json").read_text()
    with pytest.raises(bfc.ForceCoverRefused, match="NOT saved"):
        _run()
    assert (sim / "state" / "gemini.json").read_text() == before
    assert not (sim / "trades").exists()
