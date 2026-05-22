"""Per-ticker last-action memory + the v2 anti-reversal prompt gate.

This is the regression guard for a feature that fails *silently*: if the lookup
or the gate breaks, the model simply stops seeing its own prior action on each
ticker and the v2 anti-reversal rule no-ops with no error. The test is therefore
part of the feature, not optional coverage. It locks:

  * full-exit case — a ticker sold to zero still surfaces its SELL
  * 60-day lookback cap — trades older than the window are dropped
  * newest-wins — the most recent executed action per ticker is returned
  * executed-only — a non-executed (risk-rejected) decision never counts
  * v1/v2 gate — the inline column + instruction are absent under v1, present under v2
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import pytest

import src.logging.memory as memmod
import src.prompt_builder as pb
from src.logging.memory import read_last_action_per_ticker
from src.prompt_builder import build_prompts, uses_per_ticker_last_action

MODEL = "testmodel"
NOW = datetime(2026, 5, 22, 10, 30)  # cutoff at 60d = 2026-03-23


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _rec(date, ts, side, ticker, shares, price, conf, executed=True):
    return {
        "date": date, "timestamp": ts, "model_key": MODEL,
        "executions": [{
            "executed": executed, "side": side, "ticker": ticker,
            "shares": shares, "fill_price": price, "timestamp": ts,
            "decision": {"confidence": conf},
        }],
    }


@pytest.fixture
def trades_dir(tmp_path, monkeypatch):
    """Point the memory module's TRADES_DIR at a temp fixture dir."""
    d = tmp_path / "trades"
    d.mkdir()
    monkeypatch.setattr(memmod, "TRADES_DIR", d)
    return d


def _write_log(trades_dir, month, records):
    (trades_dir / f"{MODEL}_{month}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# read_last_action_per_ticker
# ---------------------------------------------------------------------------

def test_full_exit_ticker_surfaces_its_sell(trades_dir):
    # TSLA bought then SOLD TO ZERO. It is no longer a holding, so the holdings
    # table and a busy last-N window both hide it — the lookup must not.
    _write_log(trades_dir, "2026-05", [
        _rec("2026-05-10", "2026-05-10T15:00:00", "BUY", "TSLA", 4, 240.0, 6),
        _rec("2026-05-20", "2026-05-20T13:30:00", "SELL", "TSLA", 4, 255.0, 8),
    ])
    la = read_last_action_per_ticker(MODEL, ["TSLA"], now=NOW)
    assert la["TSLA"]["action"] == "SELL"
    assert la["TSLA"]["date"] == "2026-05-20"
    assert la["TSLA"]["confidence"] == 8


def test_lookback_cap_drops_old_trades(trades_dir):
    # 2026-03-01 is ~82 days before NOW — outside the default 60-day window.
    _write_log(trades_dir, "2026-03", [
        _rec("2026-03-01", "2026-03-01T14:00:00", "BUY", "NVDA", 5, 850.0, 7),
    ])
    assert "NVDA" not in read_last_action_per_ticker(MODEL, ["NVDA"], now=NOW)
    # ...but a wider lookback finds the same trade.
    la = read_last_action_per_ticker(MODEL, ["NVDA"], now=NOW, max_lookback_days=120)
    assert la["NVDA"]["action"] == "BUY"


def test_newest_action_wins_per_ticker(trades_dir):
    # Three actions on AAPL across days + months -> the most recent one wins.
    _write_log(trades_dir, "2026-04", [
        _rec("2026-04-28", "2026-04-28T10:00:00", "BUY", "AAPL", 10, 170.0, 6),
    ])
    _write_log(trades_dir, "2026-05", [
        _rec("2026-05-12", "2026-05-12T11:00:00", "SELL", "AAPL", 5, 175.0, 7),
        _rec("2026-05-19", "2026-05-19T14:00:00", "BUY", "AAPL", 3, 180.0, 9),
    ])
    la = read_last_action_per_ticker(MODEL, ["AAPL"], now=NOW)
    assert la["AAPL"]["action"] == "BUY"
    assert la["AAPL"]["date"] == "2026-05-19"
    assert la["AAPL"]["confidence"] == 9


def test_only_executed_trades_count(trades_dir):
    # A risk-rejected (non-executed) decision must not register as a prior action.
    _write_log(trades_dir, "2026-05", [
        _rec("2026-05-19", "2026-05-19T14:00:00", "BUY", "AAPL", 3, 180.0, 9, executed=False),
    ])
    assert "AAPL" not in read_last_action_per_ticker(MODEL, ["AAPL"], now=NOW)


def test_never_traded_and_empty_inputs(trades_dir):
    _write_log(trades_dir, "2026-05", [
        _rec("2026-05-19", "2026-05-19T14:00:00", "BUY", "AAPL", 3, 180.0, 9),
    ])
    assert "MSFT" not in read_last_action_per_ticker(MODEL, ["MSFT"], now=NOW)
    assert read_last_action_per_ticker(MODEL, [], now=NOW) == {}


# ---------------------------------------------------------------------------
# v1/v2 gate — the rendering half of the feature
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("version,expected", [
    ("v1", False), ("v2", True), ("v2.0", True), ("v2.1", True),
    ("v3", True), ("", False), ("garbage", False),
])
def test_gate_predicate(version, expected):
    assert uses_per_ticker_last_action(version) is expected


def _mkdf(base):
    close = np.linspace(base * 0.9, base, 30)
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": np.full(30, 1_000_000.0)},
        index=pd.date_range("2026-04-01", periods=30, freq="D"),
    )


def _build_user_prompt(monkeypatch, version, last_actions):
    """Build a real user prompt; stub only the template loader + settings."""
    monkeypatch.setattr(pb, "load_prompt_template", lambda v: "STUB SYSTEM PROMPT")
    monkeypatch.setattr(pb, "load_settings", lambda: {
        "prompt_version": version, "mode": "paper", "phase": "Phase A",
        "portfolio_rules": {"max_trades_per_day": 50, "max_positions": 50},
    })
    tickers = ["AAPL", "TSLA", "MSFT"]
    market_data = {t: _mkdf(b) for t, b in zip(tickers, [180, 250, 420])}
    portfolio_state = {
        "total_value": 100000.0, "cash": 5000.0, "cash_pct": 0.05,
        "holdings": [{"ticker": "AAPL", "shares": 10, "avg_cost": 178.0,
                      "current_price": 180.0, "weight": 0.018, "unrealized_pl_pct": 0.011}],
    }
    _sys, user, _ver, _imgs = build_prompts(
        portfolio_state, market_data, NOW,
        shortlisted_symbols=tickers, recent_decisions=[], last_actions=last_actions,
        include_chart_image=False,
    )
    return user


# TSLA sold to zero (not in holdings); AAPL/MSFT have no last_action entry.
_LAST_ACTIONS = {
    "TSLA": {"action": "SELL", "timestamp": "2026-05-20T13:30:00", "date": "2026-05-20",
             "shares": 4.0, "price": 255.0, "confidence": 8},
}


def test_v2_renders_column_and_instruction(monkeypatch):
    user = _build_user_prompt(monkeypatch, "v2", _LAST_ACTIONS)
    assert "YOUR_LAST_ACTION" in user
    assert "SELL 2026-05-20 13:30" in user   # full-exit ticker visible inline (incl. time)
    assert "(no prior trade)" in user         # AAPL/MSFT have no recorded action
    assert "ANTI-REVERSAL RULE" in user


def test_v1_omits_column_and_instruction(monkeypatch):
    user = _build_user_prompt(monkeypatch, "v1", _LAST_ACTIONS)
    assert "YOUR_LAST_ACTION" not in user
    assert "ANTI-REVERSAL RULE" not in user
    assert "SELL 2026-05-20" not in user      # last-action data must not leak into v1
