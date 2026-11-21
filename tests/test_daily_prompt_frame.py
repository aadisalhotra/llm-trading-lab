"""Daily history restoration (hub GO 2026-11-21): the prompt frame.

What must hold (hub), each tested here:
  * src/prompt_builder.py, src/charts/chart_images.py and prompts/ unchanged,
    and the frozen golden hash unchanged;
  * the guard's frames, the prices and the executor's inputs byte-identical
    whether the prompt frame is daily, a fallback, or absent;
  * the frame arithmetic: CLOSE = latest intraday price, 1D/5D/30D% vs the
    daily closes 1/5/30 sessions back; 30D_HI/30D_LO/AVG_VOL over the 31 bars
    (ruling 1); the screening block reads the same frame (ruling 2);
  * the chart's 20-period SMA draws;
  * data_inputs_hash = sha256 of the prompt frame, execution_frame_hash of the
    intraday frame (ruling 3);
  * the cache: one fetch per session, a later miss fetches again, only a valid
    fetch is written (ruling 4);
  * any failure -> the whole tick falls back, flagged, one WARN (ruling 5).

No network and nothing under data/: the fetch is a stub and the cache is tmp_path.
"""
from __future__ import annotations

import hashlib
import json
import pickle
import re
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import src.pipeline as pipeline  # noqa: E402
from src import prompt_builder  # noqa: E402
from src.adapters.base import DecisionResult  # noqa: E402
from src.charts import chart_images  # noqa: E402
from src.data import prompt_frame as pf  # noqa: E402
from src.data.price_validity import ingest_market_data  # noqa: E402
from src.data.prompt_frame import fetch_daily_bars as REAL_FETCH_DAILY_BARS  # noqa: E402
from src.logging import prompt_integrity  # noqa: E402
from src.portfolio.portfolio import IntradayState, Portfolio  # noqa: E402

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
SESSION = date(2026, 11, 23)
RUN = datetime(2026, 11, 23, 11, 5, tzinfo=ET)
SESSIONS = pf.expected_sessions(SESSION)

# Real universe names, so the shortlist scoping in build_prompts keeps them.
PRICED = {"AAPL": (180.0, 0.5), "MSFT": (400.0, -1.25), "SPY": (600.0, 0.25)}
UNPRICED = "KO"                        # the guard leaves it empty -> NO DATA
TODAY_CLOSE = {"AAPL": 197.25, "MSFT": 361.5, "SPY": 608.75}


# ----------------------------------------------------------------- fixtures
def _daily(base: float, step: float, sessions: list[str]) -> pd.DataFrame:
    """Completed daily bars; every price a multiple of 0.25 (exact in binary)."""
    close = base + step * np.arange(len(sessions), dtype=float)
    return pd.DataFrame({"Open": close - 0.25, "High": close + 0.75, "Low": close - 0.75,
                         "Close": close, "Volume": 1000.0 * (1 + np.arange(len(sessions)))},
                        index=pd.DatetimeIndex([pd.Timestamp(s) for s in sessions], name="Date"))


def _intraday(sym: str) -> pd.DataFrame:
    """Four 30-minute bars, 09:30..11:00 ET, tz-aware like yfinance's."""
    last = TODAY_CLOSE[sym]
    closes = [last - 1.0, last + 0.5, last - 0.25, last]
    idx = pd.date_range("2026-11-23 09:30", periods=4, freq="30min", tz=ET)
    return pd.DataFrame({"Open": [c - 0.25 for c in closes], "High": [c + 0.5 for c in closes],
                         "Low": [c - 0.5 for c in closes], "Close": closes,
                         "Adj Close": closes, "Volume": [500, 400, 300, 200]}, index=idx)


def _fetch_frame() -> dict[str, pd.DataFrame]:
    return {**{s: _intraday(s) for s in PRICED}, UNPRICED: pd.DataFrame()}


def _ingested():
    return ingest_market_data(_fetch_frame())


EXTRA_HISTORY = 10
LONGER = pf.expected_sessions(SESSION, n=30 + EXTRA_HISTORY)


def _expected_daily(sym: str) -> pd.DataFrame:
    """The 30 completed sessions the frame must keep from the stub's 40."""
    return _daily(*PRICED[sym], LONGER).tail(30)


def _stub_fetch():
    """A fetch returning more history than needed (the frame keeps 30)."""
    calls: list[list[str]] = []
    longer = LONGER

    def fetch(symbols, session_date):
        calls.append(list(symbols))
        return {s: (_daily(*PRICED[s], longer) if s in PRICED else _daily(50.0, 0.25, longer))
                for s in symbols}
    return fetch, calls


def _build(tmp_path, fetch=None, alerts=None):
    ing = _ingested()
    stub, calls = _stub_fetch()
    frame = pf.build_prompt_frame(
        ing.market_data, ing.prices, RUN,
        send_alert=(lambda *a, **k: alerts.append((a, k))) if alerts is not None else None,
        cache_dir=tmp_path, fetch=fetch or stub)
    return ing, frame, calls


# ------------------------------------------------- frozen files, golden hash
FROZEN_LF_SHA256 = {          # origin/main 1abb146d5, the commit this change branches from
    "src/prompt_builder.py": "41a01b5337756d0d47fb05048fbd00ab8a6fc3d5e90e3476631050a486576f0a",
    "src/charts/chart_images.py": "b000473cb1c25aea51bb98ba6f8b4326c60a0f64dc0d05c4c72ab794fbfc04bb",
    "prompts/CHANGELOG.md": "667ecd551e1b98eb0a2225242b62cd07b14330e8df1e4852f0637c2c99cc5e6d",
    "prompts/v1.txt": "6a91ed87c95505ac022820ad21427603e6ba53b90a702b5a4346328409abc2b1",
    "prompts/v2.txt": "963e726207feec3d07934b4df80b28a4388c8b86145226d031a03e5887534f11",
    "prompts/v3.txt": "81597da24da36db098c8a36b5ad744605f3226ea7c90c267db2e724a873c6626",
    "prompts/v4.txt": "fbba04e1a558bab43cb35a52a22f5935dc16e5ddbf9e514889fdcc56f331e703",
}


@pytest.mark.parametrize("rel", sorted(FROZEN_LF_SHA256))
def test_prompt_builder_chart_and_templates_unchanged(rel):
    b = (ROOT / rel).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(b).hexdigest() == FROZEN_LF_SHA256[rel]


def test_prompts_dir_has_no_new_file():
    assert sorted(p.name for p in (ROOT / "prompts").iterdir() if p.is_file()) == \
        sorted(Path(r).name for r in FROZEN_LF_SHA256 if r.startswith("prompts/"))


def test_golden_hash_unchanged():
    rec = prompt_integrity.evaluate()
    assert rec["status"] == "match"
    assert rec["combined_sha256"] == \
        "b415afcb588792873743df073fac242f642a60f19508d4a414add356c87e38b7"


# ------------------------------------------------------------ the arithmetic
def _row(block: str, ticker: str) -> list[str]:
    if "MARKET DATA (last close" in block:          # a whole user prompt: its data block only
        block = block[block.index("MARKET DATA (last close"):].split("\n\n")[0]
    (line,) = [ln for ln in block.splitlines() if ln.split()[:1] == [ticker]]
    return line.split()


@pytest.mark.parametrize("sym", sorted(PRICED))
def test_text_block_arithmetic(tmp_path, sym):
    ing, frame, _ = _build(tmp_path)
    assert frame.prompt_frame == pf.FRAME_DAILY
    block = prompt_builder._format_market_data_block(frame.frames)
    t, close, d1, d5, d30, hi, lo, vol = _row(block, sym)
    p = ing.prices[sym]
    daily = _expected_daily(sym)
    assert [str(i.date()) for i in daily.index] == SESSIONS
    c = daily["Close"].tolist()
    assert len(frame.frames[sym]) == 31
    assert float(close) == pytest.approx(p) and p == TODAY_CLOSE[sym]   # latest intraday price
    assert d1 == f"{(p / c[-1] - 1) * 100:+.2f}"      # 1 session back
    assert d5 == f"{(p / c[-5] - 1) * 100:+.2f}"      # 5 sessions back
    assert d30 == f"{(p / c[0] - 1) * 100:+.2f}"      # 30 sessions back
    today = _intraday(sym)
    # Ruling 1: 30D_HI / 30D_LO / AVG_VOL over the 31 bars, today's partial included.
    assert float(hi) == pytest.approx(max(daily["High"].max(), today["High"].max(), p))
    assert float(lo) == pytest.approx(min(daily["Low"].min(), today["Low"].min(), p))
    vols = daily["Volume"].tolist() + [float(today["Volume"].sum())]
    assert vol == f"{sum(vols) / 31:,.0f}"


def test_unpriced_ticker_still_renders_no_data(tmp_path):
    _, frame, _ = _build(tmp_path)
    assert frame.frames[UNPRICED].empty
    assert _row(prompt_builder._format_market_data_block(frame.frames), UNPRICED)[1:] == ["NO", "DATA"]


def test_today_bar_is_built_from_the_guard_frame(tmp_path):
    ing, frame, _ = _build(tmp_path)
    today = frame.frames["AAPL"].iloc[-1]
    src = ing.market_data["AAPL"]
    assert frame.frames["AAPL"].index[-1] == pd.Timestamp(SESSION)
    assert today["Open"] == src["Open"].iloc[0]
    assert today["Close"] == ing.prices["AAPL"]
    assert today["Volume"] == float(src["Volume"].sum())
    assert [str(i.date()) for i in frame.frames["AAPL"].index[:-1]] == SESSIONS
    assert list(frame.frames["AAPL"].columns) == list(pf.FIELDS)


def test_screening_block_reads_the_daily_frame(tmp_path):
    """Ruling 2: 1D% vs the previous session's close; VOL_VS_AVG is today's
    volume so far over the 31-bar mean (reads low early in the session)."""
    ing, frame, _ = _build(tmp_path)
    order = [{"symbol": s, "sector": "X"} for s in PRICED]
    block = prompt_builder._format_screening_data_block(frame.frames, {}, {}, order)
    p = ing.prices["AAPL"]
    c = _expected_daily("AAPL")["Close"].tolist()
    row = _row(block, "AAPL")
    assert row[3] == f"{(p / c[-1] - 1) * 100:+.2f}"
    vols = frame.frames["AAPL"]["Volume"]
    assert row[4] == f"{vols.iloc[-1] / vols.mean():.1f}x"


# ------------------------------------------------------------------ the chart
def _sma_drawn(df: pd.DataFrame) -> bool:
    fig, ax = plt.subplots()
    try:
        chart_images._draw_single_panel(ax, "T", df)
        return any(matplotlib.colors.same_color(ln.get_color(), chart_images.SMA_COLOR)
                   for ln in ax.get_lines())
    finally:
        plt.close(fig)


def test_chart_sma_draws_on_the_daily_frame_and_not_on_the_intraday_one(tmp_path):
    ing, frame, _ = _build(tmp_path)
    assert _sma_drawn(frame.frames["AAPL"])
    assert not _sma_drawn(ing.market_data["AAPL"])          # the old frame: 4 bars, no SMA


# --------------------------------------------------------------------- hashes
def test_hashes(tmp_path):
    ing, frame, _ = _build(tmp_path)
    assert frame.data_inputs_hash == pf.frame_sha256(frame.frames)
    assert frame.execution_frame_hash == pf.frame_sha256(ing.market_data)
    assert frame.data_inputs_hash != frame.execution_frame_hash
    assert len(frame.data_inputs_hash) == 64
    changed = {k: v.copy() for k, v in frame.frames.items()}
    changed["AAPL"].iloc[3, 3] += 0.25
    assert pf.frame_sha256(changed) != frame.data_inputs_hash
    reordered = dict(reversed(list(frame.frames.items())))
    assert pf.frame_sha256(reordered) != frame.data_inputs_hash  # order is what the prompt shows


# ---------------------------------------------- guard, prices, executor inputs
def _frame_bytes(df: pd.DataFrame) -> tuple:
    """Every byte of a frame's content: index (dtype, tz, int64 nanoseconds),
    column names, dtypes and the value buffer."""
    if df.empty:
        return ("empty", list(df.columns))
    return (str(df.index.dtype), df.index.asi8.tobytes(), list(map(str, df.columns)),
            [str(t) for t in df.dtypes], df.to_numpy(dtype=float).tobytes())


def _snapshot(ing):
    return pickle.dumps(([(s, _frame_bytes(df)) for s, df in ing.market_data.items()],
                         sorted(ing.prices.items()), sorted(ing.missing.items())))


@pytest.mark.parametrize("fails", [False, True], ids=["daily", "fallback"])
def test_guard_frames_and_prices_byte_identical(tmp_path, fails):
    ing = _ingested()
    before, ids = _snapshot(ing), {s: id(df) for s, df in ing.market_data.items()}
    stub, _ = _stub_fetch()

    def broken(*a):
        raise pf.DailyFrameError("down")
    frame = pf.build_prompt_frame(ing.market_data, ing.prices, RUN, cache_dir=tmp_path,
                                  fetch=broken if fails else stub)
    assert _snapshot(ing) == before
    assert {s: id(df) for s, df in ing.market_data.items()} == ids
    assert all(frame.frames[s] is not ing.market_data[s] or fails for s in PRICED)


def test_executor_inputs_identical_and_prompts_use_the_daily_frame(tmp_path, monkeypatch):
    """The real run_one_model, I/O stubbed: the validator's and the executor's
    inputs are byte-identical with no prompt frame (before this change) and
    with the daily one; only what the adapter is sent differs."""
    ing = _ingested()
    frame = _build(tmp_path)[1]
    settings = pipeline.load_settings()
    sp = monkeypatch.setattr
    seen: dict[str, list] = {"validate": [], "execute": [], "rows": [], "calls": []}
    real_validate = pipeline.validate_decisions

    def validate(decisions, portfolio, prices, **k):
        seen["validate"].append(pickle.dumps((decisions, sorted(prices.items()), k)))
        return real_validate(decisions, portfolio, prices, **k)

    def execute(portfolio, accepted, prices, **k):
        seen["execute"].append(pickle.dumps((accepted, sorted(prices.items()),
                                             {x: y for x, y in k.items() if x != "barrier"})))
        return []

    decision = DecisionResult(provider="fake", model_id_configured="m", model_id_returned="m",
                              decisions=[{"action": "BUY", "ticker": "AAPL", "target_weight": 0.05,
                                          "confidence": 0.6, "summary": "s", "reasoning": "r"}],
                              overall_reasoning="", raw_response="{}", latency_seconds=0.1,
                              success=True, metadata={"finish_reason": "end_turn"})
    adapter = SimpleNamespace(supports_vision=True,
                              generate_raw=lambda s, u: ("{}", 0.1, {}),
                              generate_decision=lambda s, u, images=None: decision)
    sp(pipeline, "load_portfolio", lambda k: Portfolio(
        model_key=k, cash=2000.0, inception_value=2000.0, inception_date="2026-11-06",
        intraday=IntradayState(session_date="2026-11-23", runs_today=2)))
    sp(pipeline, "save_portfolio", lambda p: None)
    sp(pipeline, "get_adapter", lambda *a: adapter)
    sp(pipeline, "parse_screening_response", lambda *a: ["AAPL", "MSFT"])
    sp(pipeline, "read_recent_decisions", lambda *a, **k: [])
    sp(pipeline, "read_last_action_per_ticker", lambda *a, **k: {})
    sp(pipeline, "retain_call_inputs", lambda *a: seen["calls"].append(a[4]) or {})
    sp(pipeline, "alert_if_flagged", lambda *a: False)
    sp(pipeline, "detect_memory_hit", lambda *a: False)
    sp(pipeline, "record_observation", lambda *a, **k: None)
    sp(pipeline, "detect_version_transition", lambda *a: None)
    sp(pipeline, "validate_decisions", validate)
    sp(pipeline, "_compute_agreement_counts", lambda *a: {})
    sp(pipeline, "log_decision_run", lambda **k: seen["rows"].append(k))
    sp(pipeline, "log_intraday_snapshot", lambda **k: None)
    sp(pipeline, "send_alert", lambda *a, **k: None)
    executor = SimpleNamespace(cycle_id="cyc", execute_decisions=execute)
    cfg = {"provider": "fake", "model": "m", "vision_capable": True}
    log = pipeline.logging.getLogger("t")
    for prompt_frame in (None, frame):
        pipeline.run_one_model("claude", cfg, ing.market_data, ing.prices, executor, RUN,
                               settings, log, is_eod=False, ingestion=ing,
                               prompt_frame=prompt_frame)
    assert len(seen["execute"]) == 2 and seen["execute"][0] == seen["execute"][1]
    assert len(seen["validate"]) == 2 and seen["validate"][0] == seen["validate"][1]
    old_calls, new_calls = seen["calls"]
    old_trade = next(c for c in old_calls if c.stage == "trade")
    new_trade = next(c for c in new_calls if c.stage == "trade")
    assert old_trade.system == new_trade.system                      # template untouched
    assert old_trade.user != new_trade.user                          # the data block is daily
    assert _row(new_trade.user, "AAPL")[3] == _row(
        prompt_builder._format_market_data_block(frame.frames), "AAPL")[3]
    assert len(new_trade.images) == 1 and len(old_trade.images) == 1
    old_row, new_row = seen["rows"]
    assert "prompt_frame" not in old_row
    assert new_row["prompt_frame"] == "daily" and new_row["prompt_frame_fallback_reason"] is None
    assert new_row["data_inputs_hash"] == frame.data_inputs_hash
    assert new_row["execution_frame_hash"] == pf.frame_sha256(ing.market_data)


def test_pipeline_builds_the_frame_after_the_guard_and_passes_it_only_to_the_models():
    src = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")
    body = src[src.index("def run_pipeline("):]
    guard = body.index("ingestion = ingest_market_data(market_data)")
    build = body.index("build_prompt_frame(market_data, prices, run_date, send_alert=send_alert)")
    assert guard < body.index("prices = ingestion.prices") < build
    assert len(re.findall(r"\bprompt_frame\b", body)) == 3   # built once, passed once
    assert body.count("build_prompt_frame(") == 1 and body.count("prompt_frame=prompt_frame,") == 1
    one = src[src.index("def run_one_model("):src.index("def run_pipeline(")]
    assert one.count("prompt_data") == 3            # defined, screening, decision prompts
    assert "build_screening_prompt(\n        prompt_data," in one.replace("\r\n", "\n")
    assert "build_prompts(\n        snapshot_before,\n        prompt_data," in one.replace("\r\n", "\n")
    assert "execute_decisions(portfolio, accepted, prices," in one


# ----------------------------------------------------------------- the cache
def test_one_fetch_per_session_then_hits(tmp_path):
    ing = _ingested()
    stub, calls = _stub_fetch()
    first = pf.build_prompt_frame(ing.market_data, ing.prices, RUN, cache_dir=tmp_path, fetch=stub)
    later = pf.build_prompt_frame(ing.market_data, ing.prices,
                                  RUN.replace(hour=14, minute=35), cache_dir=tmp_path, fetch=stub)
    assert (first.cache, later.cache) == (pf.CACHE_FETCHED, pf.CACHE_HIT)
    assert len(calls) == 1
    assert first.data_inputs_hash == later.data_inputs_hash        # a hit == a fetch, bit for bit
    doc = json.loads((tmp_path / "2026-11-23.json").read_text(encoding="utf-8"))
    assert doc["session_date"] == "2026-11-23" and doc["sessions"] == SESSIONS
    assert sorted(doc["bars"]) == sorted([*PRICED, UNPRICED])
    assert all(len(doc["bars"][s]["Close"]) == 30 for s in doc["bars"])


@pytest.mark.parametrize("damage", ["corrupt", "other_day", "short"])
def test_a_bad_cache_is_a_miss_and_fetches_again(tmp_path, damage):
    ing = _ingested()
    stub, calls = _stub_fetch()
    pf.build_prompt_frame(ing.market_data, ing.prices, RUN, cache_dir=tmp_path, fetch=stub)
    p = tmp_path / "2026-11-23.json"
    if damage == "corrupt":
        p.write_text("{not json", encoding="utf-8")
    else:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if damage == "other_day":
            doc["session_date"] = "2026-11-20"
        else:
            doc["bars"]["AAPL"] = {k: v[:-1] for k, v in doc["bars"]["AAPL"].items()}
        p.write_text(json.dumps(doc), encoding="utf-8")
    again = pf.build_prompt_frame(ing.market_data, ing.prices, RUN, cache_dir=tmp_path, fetch=stub)
    assert again.prompt_frame == pf.FRAME_DAILY and again.cache == pf.CACHE_FETCHED
    assert len(calls) == 2
    json.loads(p.read_text(encoding="utf-8"))                       # rewritten valid


def test_an_invalid_fetch_writes_no_cache_and_the_next_tick_fetches_again(tmp_path):
    ing = _ingested()
    good, calls = _stub_fetch()
    n = {"i": 0}

    def flaky(symbols, d):
        n["i"] += 1
        bars = good(symbols, d)
        if n["i"] == 1:
            bars["MSFT"].iloc[-1, bars["MSFT"].columns.get_loc("Close")] = float("nan")
        return bars
    alerts: list = []
    first = pf.build_prompt_frame(ing.market_data, ing.prices, RUN, send_alert=lambda *a, **k:
                                  alerts.append(a), cache_dir=tmp_path, fetch=flaky)
    assert first.prompt_frame == pf.FRAME_FALLBACK and not (tmp_path / "2026-11-23.json").exists()
    second = pf.build_prompt_frame(ing.market_data, ing.prices, RUN.replace(hour=11, minute=35),
                                   cache_dir=tmp_path, fetch=flaky)
    assert second.prompt_frame == pf.FRAME_DAILY and second.cache == pf.CACHE_FETCHED
    assert n["i"] == 2 and len(alerts) == 1


# ------------------------------------------------------------- the fallback
def _set(sym: str, col: str, value) -> callable:
    def mutate(b):
        b[sym] = b[sym].copy()
        b[sym].loc[:, col] = value(b[sym]) if callable(value) else value
    return mutate


def _bars_with(mutate):
    stub, _ = _stub_fetch()

    def fetch(symbols, d):
        bars = stub(symbols, d)
        mutate(bars)
        return bars
    return fetch


FAILURES = {
    "fetch_raises": lambda *a: (_ for _ in ()).throw(RuntimeError("yfinance down")),
    "symbol_missing": _bars_with(lambda b: b.pop("MSFT")),
    "nan_close": _bars_with(_set("AAPL", "Close", float("nan"))),
    "zero_price": _bars_with(_set("SPY", "Low", 0.0)),
    "high_below_close": _bars_with(_set("SPY", "High", lambda f: f["Close"] - 1)),
    "negative_volume": _bars_with(_set("AAPL", "Volume", -1.0)),
    "session_gap": _bars_with(lambda b: b.__setitem__("AAPL", b["AAPL"].drop(b["AAPL"].index[-3]))),
    "stale_history": _bars_with(lambda b: b.__setitem__("AAPL", b["AAPL"].iloc[:-1])),
}


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_any_failure_falls_back_the_whole_tick_with_one_warn(tmp_path, case):
    ing = _ingested()
    alerts: list = []
    frame = pf.build_prompt_frame(ing.market_data, ing.prices, RUN,
                                  send_alert=lambda *a, **k: alerts.append((a, k)),
                                  cache_dir=tmp_path, fetch=FAILURES[case])
    assert frame.prompt_frame == pf.FRAME_FALLBACK and frame.fallback_reason
    assert frame.frames is ing.market_data                           # today's behaviour, exactly
    assert frame.data_inputs_hash == frame.execution_frame_hash == pf.frame_sha256(ing.market_data)
    assert frame.row_fields() == {"prompt_frame": "intraday_fallback",
                                  "prompt_frame_fallback_reason": frame.fallback_reason,
                                  "execution_frame_hash": frame.execution_frame_hash}
    assert len(alerts) == 1
    (sev, title, *_), kw = alerts[0]
    assert sev == "WARN" and kw["kind"] == "prompt_frame_fallback"
    assert not (tmp_path / "2026-11-23.json").exists()


def test_fallback_never_raises_even_if_the_alert_does(tmp_path):
    ing = _ingested()

    def boom(*a, **k):
        raise RuntimeError("smtp down")
    frame = pf.build_prompt_frame(ing.market_data, ing.prices, RUN, send_alert=boom,
                                  cache_dir=tmp_path, fetch=FAILURES["fetch_raises"])
    assert frame.prompt_frame == pf.FRAME_FALLBACK


def test_suite_default_fetch_is_blocked_so_no_test_touches_the_network(tmp_path):
    ing = _ingested()
    frame = pf.build_prompt_frame(ing.market_data, ing.prices, RUN, cache_dir=tmp_path)
    assert frame.prompt_frame == pf.FRAME_FALLBACK and "never fetches" in frame.fallback_reason


# ------------------------------------------------------ the fetch's own parse
def test_fetch_daily_bars_drops_todays_partial_row(monkeypatch):
    import yfinance as yf
    days = pf.expected_sessions(SESSION, n=40) + ["2026-11-23"]
    cols = pd.MultiIndex.from_product([["AAPL", "SPY"], ["Open", "High", "Low", "Close",
                                                         "Adj Close", "Volume"]])
    raw = pd.DataFrame(1.0, index=pd.DatetimeIndex([pd.Timestamp(d) for d in days]), columns=cols)
    seen = {}

    def download(symbols, **k):
        seen.update(k, symbols=symbols)
        return raw
    monkeypatch.setattr(yf, "download", download)
    out = REAL_FETCH_DAILY_BARS(["AAPL", "SPY"], SESSION)
    assert seen["interval"] == "1d" and seen["auto_adjust"] is False and seen["group_by"] == "ticker"
    assert seen["symbols"] == ["AAPL", "SPY"]                        # one batched call
    assert out["AAPL"].index[-1] == pd.Timestamp("2026-11-20")       # today's row dropped
    assert list(out["AAPL"].columns) == list(pf.FIELDS)


def test_expected_sessions_skip_holidays():
    s = pf.expected_sessions(date(2026, 11, 30))
    assert "2026-11-26" not in s and s[-1] == "2026-11-27" and len(s) == 30
