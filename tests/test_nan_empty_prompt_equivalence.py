"""A NaN-priced ticker and an empty-frame ticker render byte-identical prompts.

Price-validity ruling (docs/registrations/price_validity_and_source_ruling.md
§1a): a ticker without a finite price this cycle is replaced by an empty frame
at ingestion, so it renders through the existing NO DATA / N/A path. The
2026-11-11 vendor fault (Close = NaN) and an ordinary vendor gap (no frame)
must therefore reach the model as the same bytes.

Each case drives the production path end to end: ingest_market_data ->
holding_marks -> Portfolio.snapshot -> build_prompts / build_screening_prompt,
on the golden fixture's inputs (src/logging/prompt_golden_fixture.py), and
compares all four model-facing texts plus the chart images.

Where the two can legitimately differ: a HELD name whose NaN frame still has
earlier valid bars is valued at the last valid bar, while an empty frame
leaves only avg_cost (price_validity.holding_marks). That is the ruling's
classified-mark design, not a rendering difference, and the tests pin it both
ways: with the marks made equal the prompts are byte-identical, so the mark is
the only channel; with them unequal they differ, so the comparison can fail.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from src.data import price_validity as pv
from src.logging import prompt_golden_fixture as fx
from src.portfolio.portfolio import Holding, Portfolio

NAN = float("nan")


def _book() -> Portfolio:
    """The fixture's book as a real Portfolio: AAPL and XOM held, JPM not."""
    p = Portfolio(model_key="book", cash=1500.0, inception_value=2000.0,
                  inception_date="2026-11-02")
    p.holdings = {"AAPL": Holding(ticker="AAPL", shares=1.5, avg_cost=190.0),
                  "XOM": Holding(ticker="XOM", shares=2.25, avg_cost=115.0)}
    return p


def _render(frames: dict[str, pd.DataFrame], *, chart: bool = True) -> dict[str, object]:
    """Ingest, value and render one cycle exactly as the pipeline orders it."""
    from src.prompt_builder import build_prompts, build_screening_prompt
    from src.config_loader import load_universe

    ing = pv.ingest_market_data(frames)
    book = _book()
    book.fallback_marks = pv.holding_marks(book.holdings, ing)
    state = book.snapshot(ing.prices)
    d_sys, d_user, _v, images = build_prompts(
        state, ing.market_data, fx.RUN_DATE,
        trades_executed_today=fx.TRADES_EXECUTED_TODAY, runs_today=fx.RUNS_TODAY,
        is_eod=False, include_chart_image=chart,
        news_data=fx.NEWS_DATA, sentiment_data=fx.SENTIMENT_DATA,
        shortlisted_symbols=fx.TICKERS, recent_decisions=fx.RECENT_DECISIONS,
        last_actions=fx.LAST_ACTIONS)
    s_sys, s_user = build_screening_prompt(
        ing.market_data, state, fx.RUN_DATE, news_data=fx.NEWS_DATA,
        sentiment_data=fx.SENTIMENT_DATA, ticker_order=list(load_universe()["tickers"]))
    return {"decision_system": d_sys, "decision_user": d_user,
            "screening_system": s_sys, "screening_user": s_user,
            "images": list(images), "missing": dict(ing.missing)}


def _with(ticker: str, frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    frames = fx.market_data()
    frames[ticker] = frame
    return frames


def _nan_last(ticker: str) -> pd.DataFrame:
    """The 2026-11-11 shape: a full frame whose last Close is NaN."""
    df = fx.market_data()[ticker].copy()
    df.iloc[-1, df.columns.get_loc("Close")] = NAN
    return df


def _all_nan(ticker: str) -> pd.DataFrame:
    df = fx.market_data()[ticker].copy()
    df["Close"] = NAN
    return df


def _assert_identical(a: dict[str, object], b: dict[str, object]) -> None:
    for k in fx.COMPONENTS:
        assert a[k].encode("utf-8") == b[k].encode("utf-8"), f"{k} differs"
    assert len(a["images"]) == len(b["images"])
    for x, y in zip(a["images"], b["images"]):
        assert bytes(x) == bytes(y), "chart image differs"


# --------------------------------------------------------------------------
# Byte-identical: the ruling's claim
# --------------------------------------------------------------------------

def test_unheld_nan_ticker_and_empty_frame_render_identical_prompts():
    nan = _render(_with("JPM", _nan_last("JPM")))
    empty = _render(_with("JPM", pd.DataFrame()))
    assert nan["missing"] == {"JPM": pv.REASON_NON_FINITE}
    assert empty["missing"] == {"JPM": pv.REASON_NO_DATA}
    _assert_identical(nan, empty)
    assert not re.search(r"\bnan\b", nan["decision_user"], re.IGNORECASE)
    # Both fetchers (fetch_universe_data, fetch_intraday_data) write an empty
    # frame for a symbol with no data, so "empty frame" is the production shape
    # of a vendor gap; a key missing from the fetch dict does not occur.


@pytest.mark.parametrize("ticker", ["AAPL", "XOM"])
def test_held_ticker_with_no_valid_bar_renders_identical_to_an_empty_frame(ticker):
    # No valid bar anywhere: both paths value the holding at avg_cost.
    nan = _render(_with(ticker, _all_nan(ticker)))
    empty = _render(_with(ticker, pd.DataFrame()))
    _assert_identical(nan, empty)


@pytest.mark.parametrize("ticker", ["AAPL", "XOM"])
def test_held_ticker_renders_identical_once_the_fallback_marks_agree(ticker):
    # NaN last close with an earlier valid bar equal to avg_cost: the
    # last-valid-bar mark and the avg_cost mark are the same number, so any
    # remaining difference would be a rendering difference. There is none.
    avg = {"AAPL": 190.0, "XOM": 115.0}[ticker]
    df = _nan_last(ticker)
    df.iloc[-2, df.columns.get_loc("Close")] = avg
    nan = _render(_with(ticker, df))
    empty = _render(_with(ticker, pd.DataFrame()))
    _assert_identical(nan, empty)


# --------------------------------------------------------------------------
# The comparison can fail (known-positive controls)
# --------------------------------------------------------------------------

def test_held_ticker_prompts_differ_when_the_fallback_marks_differ():
    # Classified-mark design, recorded: an earlier valid bar (XOM 117.0)
    # values the holding differently from avg_cost (115.0).
    nan = _render(_with("XOM", _nan_last("XOM")), chart=False)
    empty = _render(_with("XOM", pd.DataFrame()), chart=False)
    assert nan["decision_user"] != empty["decision_user"]
    assert nan["decision_system"] == empty["decision_system"]


def test_without_the_guard_a_nan_frame_does_not_render_like_an_empty_one():
    # Bypass ingestion for the NaN frame: the prompt must then differ from the
    # empty-frame render, or the identity tests above prove nothing.
    from src.prompt_builder import _format_market_data_block
    raw = _with("JPM", _nan_last("JPM"))
    guarded = pv.ingest_market_data(_with("JPM", pd.DataFrame())).market_data
    assert _format_market_data_block(raw) != _format_market_data_block(guarded)


def test_the_golden_inputs_are_unchanged_by_these_tests():
    frames = fx.market_data()
    assert all(np.isfinite(frames[t]["Close"]).all() for t in fx.TICKERS)
