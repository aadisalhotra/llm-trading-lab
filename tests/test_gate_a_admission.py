"""Gate A demonstrations (i), (ii) and (v) — tick_admission_guards_ruling.md §3.

These drive the REAL run_pipeline(mode="intraday") through the REAL guard code
(load_ledger, check_spacing, the withhold record) against files in tmp_path.
Nothing here reads or writes data/: condition 4 keeps induced demonstrations
off the validation record, and every guard path is redirected below.

What "no model prompted" is proven by: the data fetch is the first thing after
the guards, and the model loop — including the pre-prompt stop sweep inside
run_one_model, the book's first possible order — comes after the fetch. The
fetch, run_one_model, get_adapter and the Executor are all replaced by
recorders; a withheld tick must touch none of them. A tick that PROCEEDS
reaches the fetch, which raises `Reached` so the test stops there without
any further I/O.

    (i)   ledger present and unreadable   -> no model prompted, CRITICAL, tagged event
    (ii)  decision log unreadable         -> same
    (v)   month rollover, guards: the first tick of a month, when this month's
          decision logs do not exist yet  -> absent is normal and must PROCEED
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import src.pipeline as pipeline
from src.logging import boundary_ledger, tick_admission, tick_spacing
from src.logging.tick_admission import TICK_WITHHELD_GUARD_UNREADABLE

ET = ZoneInfo("America/New_York")
BOOKS = ("claude", "claude_opus", "gpt", "gemini", "grok", "deepseek")


class Reached(Exception):
    """The tick passed both guards and reached the data fetch."""


def _row(ts: str, book: str) -> str:
    return json.dumps({"timestamp": ts, "model_key": book, "cycle_id": "c"}) + "\n"


@pytest.fixture
def tick(monkeypatch, tmp_path):
    """Run one intraday tick at `when` against the given sandbox files."""
    state = tmp_path / "state"
    trades = tmp_path / "trades"
    data = tmp_path / "data"
    for d in (state, trades, data):
        d.mkdir()

    def run(when: datetime, *, ledger: bytes | None = None,
            logs: dict[str, bytes] | None = None):
        if ledger is not None:
            (state / "handled_boundaries.json").write_bytes(ledger)
        for name, content in (logs or {}).items():
            (trades / name).write_bytes(content)

        calls = {k: [] for k in ("alerts", "heartbeat", "fetch", "model", "adapter",
                                 "executor", "mark")}

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return when if tz is None else when.astimezone(tz)

        settings = {
            "mode": "broker_paper", "phase": "test", "benchmark_ticker": "SPY",
            "models": {b: {"provider": "fake", "model": f"fake-{b}", "enabled": True}
                       for b in BOOKS},
            "tick_spacing": {"enabled": True, "min_minutes": 20},
        }
        sp = monkeypatch.setattr
        # Sandbox every path the guards and the withhold record touch.
        sp(boundary_ledger, "STATE_DIR", state)
        sp(tick_spacing, "TRADES_DIR", trades)
        sp(tick_spacing, "STATE_DIR", state)
        sp(tick_admission, "DATA_DIR", data)
        # Everything before the guards: inert.
        sp(pipeline, "datetime", Frozen)
        sp(pipeline, "load_env", lambda: None)
        sp(pipeline, "ensure_dirs", lambda: None)
        sp(pipeline, "configure_logging", lambda: pipeline.logging.getLogger("t"))
        sp(pipeline, "load_settings", lambda: settings)
        sp(pipeline, "alert_preflight", lambda **k: None)
        sp(pipeline, "is_market_open_now", lambda *_: True)
        # Everything after the guards: recorders. A withheld tick touches none.
        def fetch(**k):
            calls["fetch"].append(1)
            raise Reached()
        sp(pipeline, "fetch_intraday_data", fetch)
        sp(pipeline, "fetch_universe_data", fetch)
        sp(pipeline, "universe_symbols", lambda: ["KO"])
        sp(pipeline, "run_one_model", lambda *a, **k: calls["model"].append(a))
        sp(pipeline, "get_adapter", lambda *a: calls["adapter"].append(a))
        sp(pipeline, "Executor", lambda *a, **k: calls["executor"].append(1))
        sp(pipeline, "mark_boundary_handled", lambda *a, **k: calls["mark"].append(a))
        sp(pipeline, "send_heartbeat", lambda **k: calls["heartbeat"].append(k.get("success")))
        sp(pipeline, "send_alert", lambda sev, title, *a, **k: calls["alerts"].append((sev, title)))
        try:
            rc = pipeline.run_pipeline(mode="intraday")
        except Reached:
            rc = "PROCEEDED"
        events = []
        for f in sorted(data.glob("admission/*.jsonl")):
            events += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x]
        return rc, calls, events
    return run


NOV17 = datetime(2026, 11, 17, 11, 5, tzinfo=ET)          # a session tick
GOOD_LOGS = {f"{b}_2026-11.jsonl": _row("2026-11-17T15:34:00", b).encode() for b in BOOKS}


def _assert_withheld(rc, calls, events, guard, reason):
    assert rc == 0                                       # exits 0: the event commits
    assert calls["fetch"] == [] and calls["model"] == []  # no data, no model loop
    assert calls["adapter"] == [] and calls["executor"] == []  # no prompt, no order path
    assert calls["mark"] == []                           # boundary NOT marked handled
    assert any(sev == "CRITICAL" and "Duplicate guard unreadable" in t for sev, t in calls["alerts"])
    assert calls["heartbeat"] == [False]                 # watchdog /fail ping
    assert len(events) == 1
    ev = events[0]
    assert ev["event"] == TICK_WITHHELD_GUARD_UNREADABLE
    assert (ev["guard"], ev["reason"]) == (guard, reason)
    assert ev["books"] == sorted(BOOKS)                  # all books, correlated by construction
    assert (ev["models_prompted"], ev["orders_submitted"]) == (0, 0)
    return ev


# ---------------------------------------------------------------- (i) ledger

@pytest.mark.parametrize("label,ledger", [
    ("corrupt JSON", b"{not json"),
    ("not valid UTF-8", b'{"2026-11-17": ["09:30\xff"]}'),
    ("root is not an object", b"[1, 2]"),
    ("day is not a list of slots", b'{"2026-11-17": "11:00"}'),
])
def test_demo_i_ledger_present_and_unreadable_withholds(tick, label, ledger):
    rc, calls, events = tick(NOV17, ledger=ledger, logs=GOOD_LOGS)
    ev = _assert_withheld(rc, calls, events, "boundary_ledger", "ledger_unreadable")
    assert ev["files"] == ["data/state/handled_boundaries.json"]
    assert (ev["boundary_day"], ev["boundary_slot"]) == ("2026-11-17", "11:00")


def test_demo_i_control_readable_ledger_proceeds(tick):
    rc, calls, events = tick(NOV17, ledger=b'{"2026-11-17": ["10:30"]}', logs=GOOD_LOGS)
    assert rc == "PROCEEDED" and events == []


# ---------------------------------------------------------- (ii) decision log

def test_demo_ii_non_utf8_final_row_withholds_cleanly(tick):
    """The 2026-11-17 crash: UnicodeDecodeError used to escape the guard."""
    logs = dict(GOOD_LOGS)
    logs["grok_2026-11.jsonl"] = b'{"timestamp": "2026-11-17T15:34:00", "x": "\xff\xfe"}\n'
    rc, calls, events = tick(NOV17, logs=logs)
    ev = _assert_withheld(rc, calls, events, "tick_spacing", "read_error")
    assert [Path(f).name for f in ev["files"]] == ["grok_2026-11.jsonl"]


def test_demo_ii_os_error_on_read_withholds(tick, monkeypatch):
    real_open = open

    def guarded(path, *a, **kw):
        if str(path).endswith("gpt_2026-11.jsonl"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", guarded)
    rc, calls, events = tick(NOV17, logs=GOOD_LOGS)
    ev = _assert_withheld(rc, calls, events, "tick_spacing", "read_error")
    assert [Path(f).name for f in ev["files"]] == ["gpt_2026-11.jsonl"]


def test_demo_ii_no_present_log_yields_a_row_withholds(tick):
    logs = {f"{b}_2026-11.jsonl": b'{"timestamp": "2026-11-17T15:3' for b in BOOKS}
    rc, calls, events = tick(NOV17, logs=logs)
    ev = _assert_withheld(rc, calls, events, "tick_spacing", "no_usable_row")
    assert len(ev["files"]) == len(BOOKS)


def test_demo_ii_control_one_torn_row_with_others_readable_proceeds(tick):
    """Registered: one torn final row with other books readable remains an answer."""
    logs = dict(GOOD_LOGS)
    logs["grok_2026-11.jsonl"] = b'{"timestamp": "2026-11-17T15:3'
    rc, calls, events = tick(NOV17, logs=logs)
    assert rc == "PROCEEDED" and events == []
    # ...and the torn file is still paged.
    assert any("grok_2026-11.jsonl" in t for _, t in calls["alerts"])


# ------------------------------------------------------- (v) month rollover

DEC1 = datetime(2026, 12, 1, 9, 35, tzinfo=ET)                # first tick of December
NOV_LAST = {f"{b}_2026-11.jsonl": _row("2026-11-30T20:34:00", b).encode() for b in BOOKS}


def test_demo_v_first_tick_of_month_proceeds(tick):
    """No *_2026-12.jsonl exists yet. Absent is normal; Gate A must not block."""
    rc, calls, events = tick(DEC1, ledger=b'{"2026-11-30": ["15:00", "15:30"]}', logs=NOV_LAST)
    assert rc == "PROCEEDED"
    assert events == [] and calls["alerts"] == [] and calls["heartbeat"] == []


def test_demo_v_absent_ledger_also_proceeds(tick):
    rc, calls, events = tick(DEC1, logs=NOV_LAST)
    assert rc == "PROCEEDED" and events == []


def test_demo_v_prior_month_damage_does_not_block_the_new_month(tick):
    """Only the current month's logs are consulted, so a damaged November file
    cannot withhold December's first tick (and could not change its verdict)."""
    logs = dict(NOV_LAST)
    logs["gpt_2026-11.jsonl"] = b"\xff\xfe torn"
    rc, calls, events = tick(DEC1, logs=logs)
    assert rc == "PROCEEDED" and events == []


# ------------------------------------------------- record-class discipline

def test_withhold_class_set_is_closed():
    with pytest.raises(ValueError):
        tick_admission.build_withhold_event(
            "TICK_WITHHELD_SOMETHING_NEW", guard="tick_spacing",
            now_utc=NOV17, books=[], files=[], reason="x")


def test_failed_event_write_still_withholds(tick, monkeypatch):
    monkeypatch.setattr(tick_admission, "record_withhold", lambda *a, **k: False)
    monkeypatch.setattr(pipeline, "record_withhold", lambda *a, **k: False)
    rc, calls, events = tick(NOV17, ledger=b"{bad")
    assert rc == 0 and calls["fetch"] == [] and calls["heartbeat"] == [False]
