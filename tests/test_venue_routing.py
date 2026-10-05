"""Per-book venue routing: non-cohort books run on the simulator in broker modes.

Hub ruling 2026-09-11 (restated 2026-10-05): the live cohort is five books and
DeepSeek is exploratory-only and paper-only. In a broker mode that means
DeepSeek must (a) never submit a venue order, (b) stay on its paper inception
epoch, (c) be logged as a paper-mode record, and (d) be excluded from both the
cross-book barrier's expected set (else every cycle stalls to the registration
timeout waiting for a book that never registers) and the level-2 reconciliation
sum (else its simulated cash is compared against venue cash).

One concept carries all four: config_loader.effective_mode. No network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import config_loader as cl
from src.execution import executor as ex_mod
from src.execution.executor import Executor
from src.portfolio import portfolio as pf_mod
from src.portfolio.portfolio import Holding, InceptionEpochError, Portfolio

from test_broker_execution import FakeBroker

COHORT = ["claude", "claude_opus", "gpt", "gemini", "grok"]
SIX = COHORT + ["deepseek"]


def cfg(mode, cohort=COHORT):
    return {
        "mode": mode,
        "models": {k: {"enabled": True} for k in SIX},
        "starting_capital": {"paper": 100000, "broker_paper": 2000, "live": 2000},
        "capital_structure": {"live_cohort_book_count": len(cohort) if cohort else 5,
                              "live_cohort_keys": cohort, "reserve_master_usd": 500},
    }


# ----------------------------------------------------------- effective_mode
@pytest.mark.parametrize("mode", ["broker_paper", "live"])
def test_cohort_books_keep_the_venue_mode_and_deepseek_runs_paper(mode):
    s = cfg(mode)
    assert all(cl.effective_mode(k, s) == mode for k in COHORT)
    assert cl.effective_mode("deepseek", s) == "paper"


def test_paper_mode_is_the_identity_for_every_book():
    s = cfg("paper")
    assert all(cl.effective_mode(k, s) == "paper" for k in SIX)


def test_unnamed_cohort_in_a_venue_mode_fails_loud_not_open():
    """A venue mode with no named cohort must never fall back to routing all
    six books to the venue."""
    s = cfg("broker_paper", cohort=None)
    with pytest.raises(cl.PendingCohortError):
        cl.effective_mode("grok", s)


def test_venue_book_keys_drops_deepseek_and_preserves_order():
    assert cl.venue_book_keys(SIX, cfg("live")) == COHORT
    assert cl.venue_book_keys(["deepseek", "grok"], cfg("broker_paper")) == ["grok"]
    assert cl.venue_book_keys(SIX, cfg("paper")) == []


# ------------------------------------------------------------- executor
def venue_executor(broker):
    e = Executor.__new__(Executor)
    e.settings = {"mode": "broker_paper", "settlement": {"enforce_settled_funds": False}}
    e.mode = "broker_paper"
    e.broker = broker
    e.cycle_id = "20261102T1430Z"
    e._order_seq = {}
    e.venue_books = frozenset(COHORT)
    return e


def book(key, cash=10_000.0, holdings=None):
    return Portfolio(model_key=key, cash=cash, holdings=holdings or {},
                     inception_value=10_000.0, inception_date="2026-04-09")


DECISION = {"action": "BUY", "ticker": "AAPL", "target_weight": 0.05, "confidence": 7,
            "reasoning": "t"}


def test_deepseek_buy_never_touches_the_venue_but_a_cohort_buy_does():
    fb = FakeBroker()
    e = venue_executor(fb)
    ds = book("deepseek")
    r = e._do_buy(ds, "AAPL", 5.0, 100.0, DECISION)
    assert r.executed and fb.submissions == [], "DeepSeek must run on the simulator"
    assert ds.holdings["AAPL"].shares == 5.0 and ds.cash == 9_500.0
    gk = book("grok")
    e._do_buy(gk, "AAPL", 5.0, 100.0, DECISION)
    assert [s["client_order_id"].split("-")[0:1] for s in fb.submissions] and len(fb.submissions) == 1


def test_deepseek_short_cover_and_force_liquidate_stay_off_the_venue():
    fb = FakeBroker()
    e = venue_executor(fb)
    ds = book("deepseek", holdings={"AAPL": Holding("AAPL", 5.0, 100.0),
                                    "MSFT": Holding("MSFT", -3.0, 200.0)})
    e.force_liquidate(ds, ["AAPL", "MSFT"], {"AAPL": 101.0, "MSFT": 199.0}, "POSITION_STOP")
    assert fb.submissions == []
    assert ds.holdings == {} or all(abs(h.shares) < 0.01 for h in ds.holdings.values())
    e._do_short(ds, "NVDA", 2.0, 100.0, DECISION)
    assert fb.submissions == []


def test_on_venue_is_false_when_no_broker_even_for_a_cohort_book():
    e = venue_executor(None)
    assert e.on_venue("grok") is False and e.broker_enabled is False


def test_real_init_builds_the_venue_set_from_the_cohort(monkeypatch):
    class Stub:
        def __init__(self, *a, **k):
            self.base_url = "https://paper-api.alpaca.markets"
            self.fill_deadline_seconds = 300

    monkeypatch.setattr(ex_mod, "load_settings", lambda: cfg("broker_paper"))
    monkeypatch.setattr(ex_mod, "BrokerClient", Stub)
    e = Executor()
    assert e.venue_books == frozenset(COHORT)
    assert e.on_venue("grok") and not e.on_venue("deepseek") and e.broker_enabled


# -------------------------------------------------- inception epoch per book
def test_deepseek_keeps_its_paper_epoch_in_a_broker_run(tmp_path, monkeypatch):
    monkeypatch.setattr(pf_mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(pf_mod, "load_settings", lambda: cfg("broker_paper"))
    monkeypatch.setattr(cl, "load_settings", lambda: cfg("broker_paper"))
    ds = pf_mod.load_portfolio("deepseek")          # fresh: incepts as a paper book
    assert ds.inception_epoch == "paper" and ds.cash == 100000.0
    gk = pf_mod.load_portfolio("grok")              # cohort book: broker epoch, $2,000
    assert gk.inception_epoch == "broker_paper" and gk.cash == 2000.0
    assert pf_mod.load_portfolio("deepseek").inception_epoch == "paper"   # reloads clean


def test_a_cohort_book_with_a_paper_state_file_is_still_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(pf_mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(pf_mod, "load_settings", lambda: cfg("paper"))
    monkeypatch.setattr(cl, "load_settings", lambda: cfg("paper"))
    pf_mod.load_portfolio("grok")                    # paper-era state on disk
    monkeypatch.setattr(pf_mod, "load_settings", lambda: cfg("broker_paper"))
    with pytest.raises(InceptionEpochError):
        pf_mod.load_portfolio("grok")
