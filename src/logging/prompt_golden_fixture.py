"""Golden fixture for the rendered-prompt hash check.

Renders every model-facing prompt the pipeline sends -- the decision call's
system and user prompts and the screening call's system and user prompts --
against a FIXED synthetic input: portfolio state, market data, news, recent
decisions, last actions and run time are all constants below. The live
pipeline's prompt carries live prices and holdings, so its hash changes every
cycle and detects nothing; this one changes only when the rendering itself
changes.

What still flows in live, on purpose: config/settings.json and
config/universe.json (read by build_prompts / build_screening_prompt through
load_settings / load_universe), the prompts/ template, src/prompt_builder.py
and whatever it imports, and the installed dependencies. A change to any of
them that alters what a model would be sent changes a hash here. Hashing
prompts/v4.txt and prompt_builder.py alone would miss a config or dependency
change.

Determinism across platforms and versions: every price is a multiple of 0.25
and every volume an integer, so sums, means, highs and lows are exact in
binary floating point regardless of summation order, and the remaining
arithmetic is plain IEEE division and multiplication, correctly rounded
everywhere. The chart image is not rendered (PNG bytes depend on the
font stack and plotting library build, not on the prompt).

Kept separate from prompt_integrity.py so this file's hash identifies the
fixture alone: the reference records it, and a fixture edit without a rebuilt
reference reports `fixture_changed`, not prompt drift. Imports are absolute
and lazy so scripts/build_prompt_golden_reference.py can load this file
against a checkout of the frozen commit, where this module does not exist.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

# A full-session intraday slot on the validation month's first day. Must stay
# on or after settlement.activation_date, or build_prompts refuses v4.
RUN_DATE = datetime(2026, 11, 2, 10, 0, tzinfo=EASTERN)
TRADES_EXECUTED_TODAY = 3
RUNS_TODAY = 1

TICKERS = ["AAPL", "JPM", "XOM"]

# (base close, daily step) per ticker; 30 bars, all multiples of 0.25.
_SERIES = {"AAPL": (180.00, 0.50), "JPM": (240.00, -0.25), "XOM": (110.00, 0.25)}
_VOLUMES = {"AAPL": 50_000_000, "JPM": 9_000_000, "XOM": 15_000_000}

PORTFOLIO_STATE = {
    "total_value": 2000.0,
    "cash": 1500.0,
    "cash_pct": 0.75,
    "settled_cash": 1100.0,
    "unsettled_cash": 400.0,
    "settled_cash_pct": 0.55,
    "holdings": [
        {"ticker": "AAPL", "shares": 1.5, "avg_cost": 190.0, "current_price": 194.5,
         "weight": 0.145875, "unrealized_pl_pct": 0.0236842105263158},
        {"ticker": "XOM", "shares": 2.25, "avg_cost": 115.0, "current_price": 117.25,
         "weight": 0.131906, "unrealized_pl_pct": 0.0195652173913043},
    ],
}

NEWS_DATA = {
    "AAPL": [
        {"datetime": "2026-11-02T08:15:00", "source": "Fixture Wire",
         "title": "Synthetic headline one for the golden prompt fixture"},
        {"datetime": "2026-11-01T17:40:00", "source": "Fixture Wire",
         "title": "Synthetic headline two for the golden prompt fixture"},
    ],
    "JPM": [
        {"datetime": "2026-11-02T07:05:00", "source": "Fixture Daily",
         "title": "Synthetic headline three for the golden prompt fixture"},
    ],
    "macro": [
        {"datetime": "2026-11-02T06:30:00", "source": "Fixture Macro",
         "title": "Synthetic market-wide headline for the golden prompt fixture"},
    ],
}
SENTIMENT_DATA = {"AAPL": 0.25, "JPM": -0.5, "macro": 0.125}

RECENT_DECISIONS = [
    {"timestamp": "2026-10-30T14:30:12", "ticker": "AAPL", "action": "BUY",
     "shares": 1.5, "confidence": 0.7, "summary": "Synthetic fixture decision summary."},
    {"timestamp": "2026-10-30T15:00:08", "ticker": "XOM", "action": "BUY",
     "shares": 2.25, "confidence": 0.6, "summary": "Second synthetic fixture decision summary."},
]
LAST_ACTIONS = {
    "AAPL": {"action": "BUY", "timestamp": "2026-10-30T14:30:12"},
    "XOM": {"action": "BUY", "timestamp": "2026-10-30T15:00:08"},
}

COMPONENTS = ("decision_system", "decision_user", "screening_system", "screening_user")


def market_data() -> dict:
    import numpy as np
    import pandas as pd

    idx = pd.date_range("2026-09-18", periods=30, freq="B")
    out = {}
    for t in TICKERS:
        base, step = _SERIES[t]
        close = base + step * np.arange(30, dtype=float)
        out[t] = pd.DataFrame(
            {"Open": close - 0.25, "High": close + 0.75, "Low": close - 0.75,
             "Close": close, "Volume": np.full(30, float(_VOLUMES[t]))},
            index=idx)
    return out


def render_golden() -> dict[str, str]:
    """The four model-facing texts for the fixed input, keyed by COMPONENTS."""
    from src.config_loader import load_universe
    from src.prompt_builder import build_prompts, build_screening_prompt

    data = market_data()
    decision_system, decision_user, _version, _images = build_prompts(
        PORTFOLIO_STATE, data, RUN_DATE,
        trades_executed_today=TRADES_EXECUTED_TODAY, runs_today=RUNS_TODAY,
        is_eod=False, include_chart_image=False,
        news_data=NEWS_DATA, sentiment_data=SENTIMENT_DATA,
        shortlisted_symbols=TICKERS, recent_decisions=RECENT_DECISIONS,
        last_actions=LAST_ACTIONS)
    # Universe file order, unshuffled: the live run shuffles per cycle, the
    # fixture must not. A universe change still changes this text.
    screening_system, screening_user = build_screening_prompt(
        data, PORTFOLIO_STATE, RUN_DATE, news_data=NEWS_DATA,
        sentiment_data=SENTIMENT_DATA, ticker_order=list(load_universe()["tickers"]))
    return {"decision_system": decision_system, "decision_user": decision_user,
            "screening_system": screening_system, "screening_user": screening_user}
