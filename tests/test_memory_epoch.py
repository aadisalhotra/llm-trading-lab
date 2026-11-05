"""Rolling memory stops at the book's own epoch.

A re-incepted book holds nothing from the closed paper book (the 2026-08-29
ruling: nothing carries), so it must not be told it made that book's trades.
"""
import json
from datetime import datetime

import pytest

from src.logging import memory


def rec(date, ticker, side="BUY"):
    return {"date": date, "timestamp": f"{date}T10:00:00-05:00",
            "executions": [{"executed": True, "side": side, "ticker": ticker, "shares": 3.0,
                            "fill_price": 100.0, "timestamp": f"{date}T10:00:00-05:00",
                            "decision": {"confidence": 7, "summary": "Because."}}]}


@pytest.fixture
def trades(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "TRADES_DIR", tmp_path)
    (tmp_path / "claude_2026-11.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in [
            rec("2026-11-04", "LLY"), rec("2026-11-05", "NVDA", "SELL"),
            rec("2026-11-06", "AAPL"), rec("2026-11-09", "MSFT")]),
        encoding="utf-8")
    (tmp_path / "claude_2026-10.jsonl").write_text(json.dumps(rec("2026-10-30", "XOM")) + "\n",
                                                   encoding="utf-8")


NOW = datetime(2026, 11, 9, 11, 0)


def test_recent_decisions_stop_at_the_epoch(trades):
    out = memory.read_recent_decisions("claude", now=NOW, since="2026-11-06")
    assert [r["ticker"] for r in out] == ["MSFT", "AAPL"]


def test_first_cycle_of_an_epoch_has_no_memory(trades):
    assert memory.read_recent_decisions("claude", now=NOW, since="2026-11-10") == []


def test_last_action_stops_at_the_epoch(trades):
    out = memory.read_last_action_per_ticker(
        "claude", ["LLY", "NVDA", "AAPL", "MSFT", "XOM"], now=NOW, since="2026-11-06")
    assert set(out) == {"AAPL", "MSFT"}


def test_a_book_on_its_first_epoch_sees_all_of_it(trades):
    # DeepSeek's case: inception 2026-04-09, so the filter is inert.
    out = memory.read_recent_decisions("claude", now=NOW, since="2026-04-09")
    assert [r["ticker"] for r in out] == ["MSFT", "AAPL", "NVDA", "LLY", "XOM"]
    assert out == memory.read_recent_decisions("claude", now=NOW)
