"""Hub ruling 2026-11-06, answers 1 to 3 on the sizing-floor push.

1. A confirmed fill above reference can take book cash below zero. That record
   is truthful, so it is a classified FILL_OVERRUN and a WARN when slippage
   accounts for it. Anything slippage does not account for stays CRITICAL.
2. Settled-funds tags carry settled, unsettled and cap amounts, so a cap from
   plain cash reads differently from a cap from settlement.
3. Nothing between the model's response and the executor drops a decision
   without a named reason.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_executor_sizing_floor import (  # noqa: E402
    ACTIVE,
    PATHS,
    FillingBroker,
    _book,
    _buy,
    _executor,
    _run,
)

from src.adapters.base import BaseAdapter  # noqa: E402
from src.alerts import events  # noqa: E402
from src.execution.executor import (  # noqa: E402
    CONSTRAINT_BELOW_VENUE_MINIMUM,
    CONSTRAINT_UNSETTLED_FUNDS,
    CONSTRAINT_UNSETTLED_FUNDS_CAPPED,
    EVENT_FILL_OVERRUN,
    Executor,
    alarm_fill_overruns,
)
from src.execution.reconciliation import reconcile  # noqa: E402
from src.portfolio.portfolio import Holding, load_portfolio, save_portfolio  # noqa: E402
from src.portfolio.risk import _validate_long_only, _validate_with_shorting  # noqa: E402
from src import prompt_builder  # noqa: E402
from src.portfolio import risk  # noqa: E402

PRICES = {"XOM": 150.0, "PG": 100.0}


def _nearly_full_book(cash=9.60):
    return _book(cash=cash, holdings={"PG": Holding("PG", 19.904, 100.0)})


def _alerts(result, key="grok"):
    sent = []
    alarm_fill_overruns(key, [result], lambda *a, **k: sent.append((a, k)), cycle_id="c1")
    return sent


# --------------------------------------------------------------------------- #
# 1. FILL_OVERRUN
# --------------------------------------------------------------------------- #
def test_an_explained_overrun_is_a_warn_with_the_full_event():
    book = _nearly_full_book()
    r = _run("broker", _buy("XOM", 0.10), PRICES, book, slip=0.001)
    ev = r.fill_overrun
    assert ev["event"] == EVENT_FILL_OVERRUN
    assert ev["order_id"] == r.order_id
    assert ev["reference_price"] == 150.0
    assert ev["fill_price"] == pytest.approx(150.15)
    assert ev["amount_usd"] == pytest.approx(-book.cash)
    assert ev["explained"] is True

    sent = _alerts(r)
    assert len(sent) == 1
    (severity, title, *_), kwargs = sent[0]
    assert severity == "WARN" and EVENT_FILL_OVERRUN in title
    assert kwargs["kind"] == "fill_overrun"


def test_a_fill_at_or_below_reference_has_no_overrun():
    book = _nearly_full_book()
    r = _run("broker", _buy("XOM", 0.10), PRICES, book, slip=-0.001)
    assert r.fill_overrun is None and book.cash >= 0
    assert _alerts(r) == []


def test_an_overrun_slippage_does_not_explain_stays_critical():
    """The venue filled more than was asked: slippage cannot account for that."""

    class OverfillingBroker(FillingBroker):
        def await_terminal(self, order, deadline_seconds=None):
            filled, hit = super().await_terminal(order, deadline_seconds)
            filled.filled_qty = order.requested_qty * 3
            return filled, hit

    ex = _executor("broker", PRICES)
    ex.broker = OverfillingBroker(PRICES, 0.001)
    book = _nearly_full_book()
    r = ex.execute_decisions(book, [_buy("XOM", 0.10)], PRICES, run_date=ACTIVE)[0]
    assert r.fill_overrun["explained"] is False
    assert _alerts(r)[0][0][0] == "CRITICAL"
    assert book.fill_overrun_usd == 0.0                   # nothing excuses the balance


def test_explained_across_a_batch_where_the_first_fill_uses_the_headroom():
    """Two capped buys share $300. The first ends positive and its slippage is
    what pushes the second under, so the explanation needs the batch's sum."""
    prices = {"AAA": 250.0, "BBB": 250.0, "PG": 100.0}
    book = _book(cash=300.0, holdings={"PG": Holding("PG", 17.0, 100.0)})
    ex = _executor("broker", prices, slip=0.001)
    a, b = ex.execute_decisions(book, [_buy("AAA", 0.10), _buy("BBB", 0.10)], prices,
                                run_date=ACTIVE)
    assert a.fill_overrun is None
    assert b.fill_overrun["explained"] is True
    assert book.cash < 0


def test_the_explanation_survives_a_save_and_clears_once_cash_recovers(monkeypatch, tmp_path):
    book = _nearly_full_book()
    book.inception_epoch = "paper"
    _run("broker", _buy("XOM", 0.10), PRICES, book, slip=0.001)
    carried = book.fill_overrun_usd
    assert carried > 0
    save_portfolio(book)
    assert load_portfolio("grok").fill_overrun_usd == pytest.approx(carried)

    book.cash = 5.0
    save_portfolio(book)
    assert load_portfolio("grok").fill_overrun_usd == 0.0


@pytest.mark.parametrize("cash,carried,severity", [
    (-0.03, 0.04, "WARN"),          # slippage accounts for it, truncation slack and all
    (-0.04, 0.04, "WARN"),          # to the cent
    (-5.00, 0.04, "CRITICAL"),      # most of it unexplained
    (-0.03, 0.00, "CRITICAL"),      # nothing recorded at all
])
def test_the_state_audit_separates_explained_from_unexplained(monkeypatch, cash, carried, severity):
    book = _nearly_full_book(cash=cash)
    book.fill_overrun_usd = carried
    monkeypatch.setattr("src.portfolio.load_portfolio", lambda key: book)
    monkeypatch.setattr(events, "_enabled_model_keys", lambda s: ["grok"])
    monkeypatch.setattr(events, "_display_name", lambda s, k: k)
    specs = events.detect_state_anomalies({"portfolio_rules": {}}, "2026-11-09")
    cash_specs = [s for s in specs if s["dedup_key"] == "state_cash:grok"]
    assert len(cash_specs) == 1 and cash_specs[0]["severity"] == severity


# --- the cycle after an overrun ---------------------------------------------- #
def _overrun_book():
    book = _nearly_full_book()
    _run("broker", _buy("XOM", 0.10), PRICES, book, slip=0.001)
    assert book.cash < 0
    return book


def test_next_cycle_prompt_renders_the_negative_balance():
    """settled_cash() clamps at zero, by design, so the deployable line reads
    $0.00 and the negative balance shows on Total cash. The block's format is
    frozen by the v4 rendering amendment and is not changed here."""
    book = _overrun_book()
    block = prompt_builder._format_portfolio_block(book.snapshot(PRICES), 50, settlement_split=True)
    assert "Settled cash (deployable): $0.00 (0.0%)" in block
    assert f"Total cash:     ${book.cash:,.2f}" in block
    assert "$-0.01" in block


@pytest.mark.parametrize("path", PATHS)
def test_next_cycle_sends_no_buy_and_tags_it(path):
    book = _overrun_book()
    r = _run(path, _buy("MSFT", 0.10), {**PRICES, "MSFT": 100.0}, book)
    assert r.executed is False
    assert r.constraint == CONSTRAINT_BELOW_VENUE_MINIMUM
    assert r.decision_id


def test_next_cycle_reconciliation_stays_clean():
    book = _overrun_book()

    class Venue:
        def get_account(self):
            return {"cash": str(round(book.cash, 2))}

        def get_positions(self):
            return [{"symbol": t, "qty": str(h.shares)} for t, h in book.holdings.items()]

    result = reconcile([book], Venue(), "c2", cash_tolerance_usd=1.0, unallocated_cash_usd=0.0,
                       position_tolerance_usd=1.0)
    assert result.clean, result.events


# --------------------------------------------------------------------------- #
# 2. What the settled-funds tags carry
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", PATHS)
def test_a_settlement_cap_carries_its_balances(path):
    prices = {"MSFT": 500.0}
    book = _book()
    book.settlement.record_sale(1950.0, ACTIVE)
    r = _run(path, _buy("MSFT", 0.20), prices, book)
    assert r.constraint == CONSTRAINT_UNSETTLED_FUNDS_CAPPED
    assert r.constraint_detail["unsettled_usd"] == pytest.approx(1950.0)
    assert r.constraint_detail["settled_usd"] == pytest.approx(50.0)
    assert r.constraint_detail["cap_usd"] == pytest.approx(50.0)


@pytest.mark.parametrize("path", PATHS)
def test_a_plain_cash_cap_reads_as_one(path):
    book = _nearly_full_book()
    r = _run(path, _buy("XOM", 0.10), PRICES, book)
    assert r.constraint == CONSTRAINT_UNSETTLED_FUNDS_CAPPED
    assert r.constraint_detail["unsettled_usd"] == 0.0
    assert r.constraint_detail["cap_usd"] == pytest.approx(9.60)


@pytest.mark.parametrize("path", PATHS)
def test_the_blocked_tag_carries_them_too(path):
    book = _book()
    book.settlement.record_sale(2000.0, ACTIVE)
    r = _run(path, _buy("MSFT", 0.20), {"MSFT": 500.0}, book)
    assert r.constraint == CONSTRAINT_UNSETTLED_FUNDS
    assert r.constraint_detail == {"settled_usd": 0.0, "unsettled_usd": 2000.0, "cap_usd": 0.0}


@pytest.mark.parametrize("path", PATHS)
def test_an_uncapped_order_carries_no_detail(path):
    r = _run(path, _buy("XOM", 0.05), PRICES, _book())
    assert r.constraint == "" and r.constraint_detail is None


# --------------------------------------------------------------------------- #
# _rejected is gone, and nothing can take its place
# --------------------------------------------------------------------------- #
def test_rejected_and_the_venue_branches_behind_it_are_gone():
    src = (ROOT / "src/execution/executor.py").read_text(encoding="utf-8")
    assert "_rejected" not in src
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Executor")
    for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name.startswith("_do_")):
        assert "_place" not in ast.unparse(fn), fn.name


@pytest.mark.parametrize("call", ["_do_buy", "_do_sell", "_do_short"])
def test_a_venue_book_cannot_reach_a_simulated_fill(call):
    ex = _executor("broker", PRICES)
    book = _book(holdings={"XOM": Holding("XOM", 1.0, 150.0)})
    with pytest.raises(RuntimeError, match="simulated fill"):
        getattr(ex, call)(book, "XOM", 1.0, 150.0, _buy("XOM", 0.1))


def test_a_simulated_fill_on_a_venue_book_cannot_slip_through_execute_one():
    ex = _executor("broker", PRICES)
    with pytest.raises(RuntimeError, match="simulated fill"):
        ex._execute_one(_book(), _buy("XOM", 0.05), PRICES, ACTIVE)


# --------------------------------------------------------------------------- #
# 3. Every dropped decision has a named reason
# --------------------------------------------------------------------------- #
UNIVERSE = ["AAPL", "MSFT", "NVDA"]
DECISIONS = [
    {"action": "BUY", "ticker": "AAPL", "target_weight": 0.1},
    {"action": "BUY", "ticker": "ZZZZ", "target_weight": 0.1},       # off universe
    {"action": "BUY", "ticker": "MSFT", "target_weight": 0.9},       # clamped, not dropped
    {"action": "SELL", "ticker": "NVDA", "target_weight": 0.0},      # not held
    {"action": "COVER", "ticker": "AAPL", "target_weight": 0.0},     # shorting off / not short
    {"action": "SHORT", "ticker": "AAPL", "target_weight": 0.1},     # shorting off / held long
    {"action": "HOLD", "ticker": "AAPL", "target_weight": 0.1},
    {"action": "WHAT", "ticker": "AAPL", "target_weight": 0.1},      # unknown
]
NON_DROPPING = {"MAX_POSITION_WEIGHT", "EXPOSURE_CAP_RESIZED"}


def _settings(max_trades=50):
    return {"portfolio_rules": {
        "max_positions": 2, "max_position_pct": 0.2, "max_trades_per_day": max_trades,
        "max_long_exposure_pct": 0.25, "max_gross_short_pct": 0.2, "max_gross_exposure_pct": 0.3,
    }}


@pytest.mark.parametrize("validator", [_validate_long_only, _validate_with_shorting])
@pytest.mark.parametrize("max_trades", [50, 1])
@pytest.mark.parametrize("cash", [2000.0, 0.0])
def test_every_decision_is_accepted_or_dropped_with_a_named_reason(
        monkeypatch, validator, max_trades, cash):
    monkeypatch.setattr(risk, "universe_symbols", lambda: UNIVERSE)
    book = _book(cash=cash)
    decisions = [dict(d) for d in DECISIONS]
    accepted, violations = validator(decisions, book, {"AAPL": 100.0, "MSFT": 100.0, "NVDA": 100.0},
                                     0, _settings(max_trades))
    kept = {id(d) for d in accepted}
    dropped = {v.decision_index for v in violations if v.rule not in NON_DROPPING}
    for i, d in enumerate(decisions):
        assert (id(d) in kept) != (i in dropped), (i, d, [v.rule for v in violations])
    assert all(v.rule and v.detail for v in violations)


def test_a_halted_book_names_its_reason(monkeypatch):
    monkeypatch.setattr(risk, "load_settings", lambda: {"portfolio_rules": _settings()["portfolio_rules"]})
    book = _book()
    book.halted = True
    accepted, violations = risk.validate_decisions([dict(DECISIONS[0])], book, {"AAPL": 100.0})
    assert accepted == [] and [v.rule for v in violations] == ["PORTFOLIO_HALTED"]


def test_the_parser_names_what_it_cannot_read():
    raw = """{"overall_reasoning": "x", "decisions": [
        "not an object",
        {"action": "PANIC", "ticker": "AAPL"},
        {"action": "BUY", "ticker": "  "},
        {"action": "BUY", "ticker": "AAPL", "target_weight": 0.1}
    ]}"""
    parsed = BaseAdapter._parse_response(raw)
    assert [d["ticker"] for d in parsed["decisions"]] == ["AAPL"]
    assert [(d["index"], d["rule"]) for d in parsed["parse_drops"]] == [
        (0, "MALFORMED_DECISION"), (1, "UNKNOWN_ACTION"), (2, "MISSING_TICKER")]


def test_a_clean_response_has_no_drops():
    parsed = BaseAdapter._parse_response(
        '{"overall_reasoning": "x", "decisions": [{"action": "HOLD", "ticker": "AAPL"}]}')
    assert parsed["parse_drops"] == []


# --------------------------------------------------------------------------- #
# Names that only fail at runtime
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("module", [
    "src.pipeline", "src.execution.executor", "src.analytics.intended_vs_expressed",
    "src.analytics.execution_metrics", "src.portfolio.portfolio", "src.adapters.base",
])
def test_every_global_a_module_reads_exists(module):
    """A misspelled call inside a guarded block raises nothing until it runs, and
    then the guard swallows it. That is how the descriptor's first call shipped
    calling a function pipeline.py never had: every test passed, and a dry cycle
    was what found it. This reads the bytecode, so it needs no cycle."""
    import builtins
    import dis
    import importlib
    import types

    mod = importlib.import_module(module)
    known = set(vars(mod)) | set(vars(builtins))

    def walk(code):
        for ins in dis.get_instructions(code):
            if ins.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
                yield ins.argval
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                yield from walk(const)

    source = Path(mod.__file__).read_text(encoding="utf-8")
    missing = set(walk(compile(source, mod.__file__, "exec"))) - known
    # Names bound inside a function by a local import resolve as locals there;
    # __annotations__-style module dunders are set by the import system.
    assert not {n for n in missing if not n.startswith("__")}, missing
