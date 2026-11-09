"""The 0.01-share deletion, its reconciliation twin, and the macro alert record.

A book used to delete any holding under 0.01 share while the broker kept the
shares, and reconciliation used the same 0.01 as its tolerance, so it could not
see the result. On LLY that is $13.93, 0.7% of a $2,000 book. These tests pin the
replacement: "no position" means venue precision, and divergence is measured in
dollars per name.

No network: a fake broker returns scripted account state.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.alerts import events
from src.execution import reconciliation as rec
from src.execution.broker import QTY_DECIMALS
from src.portfolio.audit import book_fill_differences
from src.portfolio.portfolio import Holding, Portfolio
from src.portfolio.risk import validate_decisions
from src.prompt_builder import _format_portfolio_block

LLY = 1393.44


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    monkeypatch.setattr(rec, "save_portfolio", lambda p: None)
    monkeypatch.setattr(rec, "RECONCILIATION_DIR", tmp_path / "reconciliation")
    monkeypatch.setattr(rec, "TRADES_DIR", tmp_path / "trades")
    import src.alerts.alerter as alerter
    monkeypatch.setattr(alerter, "send_alert", lambda *a, **k: None)


class FakeBroker:
    def __init__(self, cash, positions=()):
        self._cash, self._positions = cash, list(positions)

    def get_account(self):
        return {"cash": str(self._cash)}

    def get_positions(self):
        return [{"symbol": s, "qty": str(q), "current_price": str(p)}
                for s, q, p in self._positions]


def book(key="a", cash=1000.0, **holdings):
    return Portfolio(model_key=key, cash=cash, holdings={
        t: Holding(ticker=t, shares=q, avg_cost=100.0) for t, q in holdings.items()})


def run(portfolios, broker):
    return rec.reconcile(portfolios, broker, "cyc1", cash_tolerance_usd=1.00,
                         unallocated_cash_usd=0.0, position_tolerance_usd=1.00)


# ------------------------------------------------------------------- booking
def test_no_position_is_derived_from_venue_precision():
    assert Portfolio.no_position_shares() == 10.0 ** -QTY_DECIMALS


def test_sub_cent_share_buy_survives_a_sell_of_a_different_name():
    """The corruption path: every SELL sweeps the whole book."""
    p = book(cash=1000.0, KO=2.0)
    p.buy("LLY", 0.0069, LLY)
    p.sell("KO", 1.0, 95.0)
    assert p.sweep_ghost_positions() == []
    assert p.holdings["LLY"].shares == pytest.approx(0.0069)


def test_a_full_exit_short_one_tick_keeps_the_remainder_as_booked():
    p = book(NVDA=1.6081)
    p.sell("NVDA", 1.608, 173.84)
    assert p.holdings["NVDA"].shares == pytest.approx(0.0001)
    assert p.sweep_ghost_positions() == []
    assert "NVDA" in p.holdings


def test_a_full_exit_still_closes_the_position():
    p = book(KO=1.1)
    p.sell("KO", 1.1, 95.0)
    assert "KO" not in p.holdings


def test_float_residue_at_venue_precision_is_removed():
    p = book(KO=1.0 + 5e-10)
    p.sell("KO", 1.0, 95.0)
    assert "KO" not in p.holdings


def test_oversell_allowance_cannot_open_a_short():
    """A SELL up to 1e-6 over the holding leaves a negative remainder."""
    p = book(KO=1.0)
    p.sell("KO", 1.0 + 5e-7, 95.0)
    assert "KO" not in p.holdings


def test_small_cover_remainder_is_kept_and_a_full_cover_closes():
    p = book(cash=5000.0, AMD=-0.5)
    p.cover("AMD", 0.4999, 480.0)
    assert p.holdings["AMD"].shares == pytest.approx(-0.0001)
    p.cover("AMD", 0.0001, 480.0)
    assert "AMD" not in p.holdings


def test_sweep_leaves_live_shorts_alone():
    p = book(AMD=-0.0001)
    assert p.sweep_ghost_positions() == []


# -------------------------------------------- dust in prompts and in the rules
def test_dust_holding_renders_in_prompt_state():
    p = book(NVDA=0.0001, KO=2.0)
    state = p.snapshot({"NVDA": 173.84, "KO": 95.0})
    block = _format_portfolio_block(state, 50)
    assert "NVDA" in block and "0.0001" in block
    assert "Open positions: 2 / 50 max" in block


def test_dust_holding_passes_the_validator():
    p = book(cash=1900.0, NVDA=0.0001)
    prices = {"NVDA": 173.84, "KO": 95.0}
    decisions = [{"action": "BUY", "ticker": "KO", "target_weight": 0.05, "confidence": 6},
                 {"action": "HOLD", "ticker": "NVDA", "target_weight": 0.0, "confidence": 5}]
    accepted, violations = validate_decisions(decisions, p, prices, shorting_enabled=True)
    assert violations == []
    assert {d["ticker"] for d in accepted} == {"KO", "NVDA"}


def test_dust_holding_counts_as_one_position_against_the_cap(monkeypatch):
    """Documents the limit: a dust holding is a position. It only matters at the
    cap, which is 50 against the handful of names these books hold."""
    from src.portfolio import risk
    real = risk.load_settings()
    rules = {**real["portfolio_rules"], "max_positions": 1}
    monkeypatch.setattr(risk, "load_settings", lambda: {**real, "portfolio_rules": rules})
    p = book(cash=1900.0, NVDA=0.0001)
    prices = {"NVDA": 173.84, "KO": 95.0}
    decisions = [{"action": "BUY", "ticker": "KO", "target_weight": 0.05, "confidence": 6}]
    _, violations = validate_decisions(decisions, p, prices, shorting_enabled=True)
    assert [v.rule for v in violations] == ["MAX_POSITIONS"]


# ------------------------------------------------------------- reconciliation
def test_one_dollar_divergence_on_lly_is_caught():
    shares = 0.5
    diff = 1.0 / LLY
    r = run([book(LLY=shares)], FakeBroker(1000.0, [("LLY", shares + diff, LLY)]))
    assert [(e.kind, e.ticker) for e in r.events] == [("position", "LLY")]
    assert r.events[0].delta_usd == pytest.approx(1.0)


def test_divergence_under_a_dollar_on_lly_is_clean():
    r = run([book(LLY=0.5)], FakeBroker(1000.0, [("LLY", 0.5 + 0.99 / LLY, LLY)]))
    assert r.clean


def test_same_share_difference_is_judged_by_its_dollars():
    """0.005 share: $6.97 of LLY is an event, $0.15 of PFE is not."""
    lly = run([book(LLY=1.0)], FakeBroker(1000.0, [("LLY", 1.005, LLY)]))
    pfe = run([book(PFE=1.0)], FakeBroker(1000.0, [("PFE", 1.005, 29.20)]))
    assert not lly.clean and pfe.clean


def test_orphan_of_a_dollar_or_more_raises_gbook_and_halts():
    ps = [book()]
    r = run(ps, FakeBroker(1000.0, [("TSLA", 1.0 / 400.0, 400.0)]))
    assert [(e.kind, e.ticker) for e in r.events] == [("orphan", "TSLA")]
    assert r.orphan_positions["TSLA"]["usd"] == pytest.approx(1.0)
    alerts = []
    rec.handle_reconciliation_result(r, ps, lambda *a, **k: alerts.append((a, k)))
    assert alerts[0][0][0] == "CRITICAL" and ps[0].halted


def test_orphan_under_a_dollar_warns_and_does_not_halt():
    ps = [book()]
    r = run(ps, FakeBroker(1000.0, [("NVDA", 0.0001, 173.84)]))
    assert r.clean and r.orphan_positions["NVDA"]["usd"] == pytest.approx(0.017384)
    alerts = []
    rec.handle_reconciliation_result(r, ps, lambda *a, **k: alerts.append((a, k)))
    assert [a[0][0] for a in alerts] == ["WARN"]
    assert not ps[0].halted


def test_orphan_line_is_empty_when_every_position_is_owned():
    r = run([book(KO=1.0)], FakeBroker(1000.0, [("KO", 1.0, 95.0)]))
    assert r.clean and r.orphan_positions == {}
    assert r.to_dict()["orphan_positions"] == {}


def test_re_attributed_dust_reconciles_with_no_orphan():
    r = run([book(NVDA=0.0001)], FakeBroker(1000.0, [("NVDA", 0.0001, 173.84)]))
    assert r.clean and r.orphan_positions == {}


def test_unpriced_orphan_is_an_event():
    class NoPrice(FakeBroker):
        def get_positions(self):
            return [{"symbol": "XYZ", "qty": "0.5"}]
    r = run([book()], NoPrice(1000.0))
    assert r.events[0].kind == "orphan" and r.events[0].delta_usd is None


# ------------------------------------------------- level-1 per-book aggregate
def _trades(tmp_path, book_key, rows):
    d = tmp_path / "trades"
    d.mkdir(exist_ok=True)
    with open(d / f"{book_key}_2026-11.jsonl", "w", encoding="utf-8") as f:
        for date, side, ticker, shares, price in rows:
            f.write(json.dumps({"date": date, "executions": [
                {"executed": True, "side": side, "ticker": ticker,
                 "shares": shares, "fill_price": price}]}) + "\n")
    return d


def test_book_fill_differences_ignore_float_noise():
    fills = [("KO", 0.1, 95.0), ("KO", 0.2, 95.0)]
    assert book_fill_differences({"KO": 0.3}, fills) == {}


def test_aggregate_finds_a_deletion_and_prices_it(tmp_path):
    trades = _trades(tmp_path, "grok", [
        ("2026-11-06", "BUY", "NVDA", 1.6081, 187.93),
        ("2026-11-09", "SELL", "NVDA", 1.608, 173.84)])
    p = Portfolio(model_key="grok", cash=100.0, inception_date="2026-11-06")
    out = rec.book_fill_aggregates([p], {}, trades, tmp_path / "none.json")
    assert out["grok"] == pytest.approx(0.0001 * 173.84)


def test_aggregate_crosses_two_dollars_for_a_large_deletion(tmp_path):
    trades = _trades(tmp_path, "gemini", [("2026-11-09", "BUY", "LLY", 0.0069, LLY)])
    p = Portfolio(model_key="gemini", cash=100.0, inception_date="2026-11-06")
    out = rec.book_fill_aggregates([p], {"LLY": LLY}, trades, tmp_path / "none.json")
    assert out["gemini"] == pytest.approx(0.0069 * LLY)
    assert out["gemini"] >= 2.00


def test_baseline_starts_a_book_with_unreconcilable_history(tmp_path):
    trades = _trades(tmp_path, "deepseek", [
        ("2026-04-22", "BUY", "UNH", 39.0, 300.0),
        ("2026-11-09", "BUY", "KO", 5.0, 95.0),
        ("2026-11-10", "BUY", "KO", 1.0, 95.0)])
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"deepseek": {"as_of_date": "2026-11-09",
                                             "positions": {"KO": 5.0}}}))
    p = Portfolio(model_key="deepseek", cash=100.0, inception_date="2026-04-09",
                  holdings={"KO": Holding(ticker="KO", shares=6.0, avg_cost=95.0)})
    assert rec.book_fill_aggregates([p], {}, trades, base)["deepseek"] == 0.0


def test_a_torn_trade_line_is_an_error_not_a_smaller_history(tmp_path):
    d = tmp_path / "trades"
    d.mkdir()
    (d / "grok_2026-11.jsonl").write_text('{"date": "2026-11-09"\n')
    p = Portfolio(model_key="grok", cash=100.0, inception_date="2026-11-06")
    with pytest.raises(ValueError):
        rec.book_fill_aggregates([p], {}, d, tmp_path / "none.json")


def test_aggregate_failure_alerts_and_keeps_the_record(monkeypatch):
    r = run([book()], FakeBroker(1000.0))
    monkeypatch.setattr(rec, "book_fill_aggregates",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("torn")))
    alerts = []
    settings = {"mode": "broker_paper", "models": {},
                "reconciliation": {"book_aggregate_bound_pct": 0.001},
                "starting_capital": {"broker_paper": 2000.0}}
    rec._check_book_fill_aggregates(r, settings, FakeBroker(1000.0),
                                    lambda *a, **k: alerts.append(a))
    assert alerts[0][0] == "CRITICAL" and r.book_fill_aggregate_usd is None


def test_registered_bound_is_two_dollars():
    from src.config_loader import book_aggregate_bound_usd
    settings = {"mode": "broker_paper",
                "reconciliation": {"book_aggregate_bound_pct": 0.001},
                "starting_capital": {"broker_paper": 2000.0}}
    assert book_aggregate_bound_usd(settings) == pytest.approx(2.00)


# --------------------------------------------------------- macro alert record
def test_macro_trigger_is_kept_in_the_compact_record(monkeypatch):
    headline = {"title": "Trump to meet Iran war mediator Qatar, no imminent deal seen to end the war",
                "source": "Reuters", "datetime": "2026-11-09T11:46:13+00:00", "summary": ""}
    monkeypatch.setattr(events, "_read_macro_top_cache", lambda: [])
    monkeypatch.setattr(events, "_read_news_cache", lambda: {})
    monkeypatch.setattr(events, "_models_positioning", lambda s: {
        "n_models": 6, "total_cash_pct": 0.1, "most_held": []})
    specs = events.detect_macro_market_events({"alerts": {}}, {"macro": [headline]})
    assert len(specs) == 1
    trigger = specs[0]["context"]["trigger"]
    assert trigger["headline"] == headline["title"]
    assert trigger["source"] == "Reuters"
    assert trigger["matched_term"].lower() == "war"
    compact = events._compact({"kind": "macro_event", "severity": "CRITICAL", "title": "t",
                               "timestamp": "x", "context": specs[0]["context"]})
    assert compact["trigger"] == trigger


def test_compact_record_of_other_events_is_unchanged():
    assert set(events._compact({"kind": "k", "severity": "WARN", "title": "t", "timestamp": "x",
                                "context": {"models": ["a"]}})) == {
        "kind", "severity", "title", "timestamp"}
