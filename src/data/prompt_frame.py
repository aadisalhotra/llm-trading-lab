"""The prompt frame: real daily history for the text block and the chart.

Hub GO 2026-11-21 (daily_history_restoration). Since cd53b8d11 (2026-04-09)
an intraday tick handed the prompt builder the session's 30-minute bars only
(fetch_intraday_data, period="1d"), so the block's 1D/5D/30D columns, its
30D_HI/30D_LO/AVG_VOL and the chart were computed over today's bars, and the
chart's 20-period SMA never drew. This module builds, for every intraday tick,
a separate frame that only the two prompt builders see:

    30 completed NYSE daily bars (the sessions before today)
  + 1 partial bar for today, from the intraday frame the guard accepted:
        Open  = today's first bar's Open
        High  = max(today's bar Highs, CLOSE)
        Low   = min(today's bar Lows, CLOSE)
        Close = the tick's price for the ticker (the guard's last valid close,
                the very number the executor prices with)
        Volume = sum of today's bar volumes

With src/prompt_builder.py unchanged (frozen; the golden hash proves it), the
31-bar frame gives:
    CLOSE   = latest intraday price
    1D%     = vs the close 1 session back      (iloc[-2])
    5D%     = vs the close 5 sessions back     (iloc[-6])
    30D%    = vs the close 30 sessions back    (iloc[0])
    30D_HI / 30D_LO / AVG_VOL over the 31 bars, today's partial bar included
        (ruling 1: accepted and documented; AVG_VOL reads slightly low because
        today's volume is partial)
    The screening block reads the same frame (ruling 2): its 1D% is vs the
        previous session's close, and VOL_VS_AVG = today's volume so far over
        the 31-bar average, so it reads low early in the session.
    The chart's tail(30) is 29 completed sessions plus today: the 20-period
        SMA draws.

Nothing here touches the execution path. The guard (ingest_market_data) has
already run; its frames and prices are read, never modified, and the executor
keeps receiving them. data_inputs_hash becomes the sha256 of THIS frame
(ruling 3); execution_frame_hash is the sha256 of the guard's intraday frame.

Daily bars come from one batched fetch per session, cached and committed at
data/market/daily/{YYYY-MM-DD}.json (ruling 4). A cache is written only after
it passes the validity check, so a failed or invalid fetch leaves no cache and
the next tick fetches again. Any failure anywhere -- fetch, cache, validity,
assembly -- makes the WHOLE tick fall back to the intraday frame: the prompt
is what it was before this change, rows carry prompt_frame
"intraday_fallback", and one WARN is sent (ruling 5). Never raises.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from ..config_loader import DATA_DIR

logger = logging.getLogger("llmlab.prompt_frame")

DAILY_CACHE_DIR = DATA_DIR / "market" / "daily"
COMPLETED_SESSIONS = 30
FIELDS = ("Open", "High", "Low", "Close", "Volume")

FRAME_DAILY = "daily"
FRAME_FALLBACK = "intraday_fallback"

CACHE_HIT = "hit"
CACHE_FETCHED = "fetched"

SOURCE = "yfinance download, interval=1d, auto_adjust=False, batched"


class DailyFrameError(Exception):
    """The daily frame cannot be used this tick; the tick falls back."""


@dataclass
class PromptFrame:
    """What the prompt builders see this tick, and how it was made."""
    frames: dict[str, pd.DataFrame]
    prompt_frame: str                      # FRAME_DAILY or FRAME_FALLBACK
    data_inputs_hash: str                  # sha256 of `frames`
    execution_frame_hash: str              # sha256 of the guard's intraday frames
    fallback_reason: str | None = None
    cache: str | None = None               # CACHE_HIT / CACHE_FETCHED on success

    def row_fields(self) -> dict[str, Any]:
        """The decision-row fields."""
        return {"prompt_frame": self.prompt_frame,
                "prompt_frame_fallback_reason": self.fallback_reason,
                "execution_frame_hash": self.execution_frame_hash}


# ------------------------------------------------------------------- hashing
def frame_sha256(frames: dict[str, pd.DataFrame]) -> str:
    """sha256 of a {symbol: frame} dict exactly as a consumer iterates it:
    symbol order, index labels, column names and every value. An empty frame
    hashes as null. Floats serialize by repr, so equal frames hash equal and
    any changed value changes the hash."""
    payload: list[Any] = []
    for sym, df in frames.items():
        if df is None or df.empty:
            payload.append([str(sym), None])
            continue
        payload.append([str(sym),
                        [str(i) for i in df.index],
                        [str(c) for c in df.columns],
                        [[_num(v) for v in row] for row in df.itertuples(index=False, name=None)]])
    text = json.dumps(payload, separators=(",", ":"), allow_nan=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _num(v: Any) -> Any:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f


# ------------------------------------------------------------------ sessions
def expected_sessions(session_date: date, n: int = COMPLETED_SESSIONS) -> list[str]:
    """The n NYSE sessions strictly before `session_date`, oldest first."""
    import pandas_market_calendars as mcal
    sched = mcal.get_calendar("NYSE").schedule(
        start_date=(session_date - timedelta(days=3 * n + 15)).isoformat(),
        end_date=(session_date - timedelta(days=1)).isoformat())
    days = [ts.date().isoformat() for ts in sched.index]
    if len(days) < n:
        raise DailyFrameError(f"NYSE calendar gave {len(days)} sessions before "
                              f"{session_date}, need {n}")
    return days[-n:]


# --------------------------------------------------------------------- fetch
def fetch_daily_bars(symbols: list[str], session_date: date) -> dict[str, pd.DataFrame]:
    """One batched yfinance download of daily OHLCV, completed sessions only
    (any row dated `session_date` or later -- today's partial daily bar -- is
    dropped). Returns {symbol: frame}; a symbol yfinance returned nothing for
    is an empty frame, which the validity check rejects."""
    import yfinance as yf
    raw = yf.download(list(symbols), period="3mo", interval="1d", group_by="ticker",
                      auto_adjust=False, progress=False, threads=True)
    if raw is None or raw.empty:
        raise DailyFrameError("batched daily download returned nothing")
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            out[sym] = pd.DataFrame()
            continue
        df = df.dropna(how="all")
        idx = pd.DatetimeIndex(df.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        df = df.set_axis(idx.normalize(), axis=0)
        df = df[df.index < pd.Timestamp(session_date)]
        out[sym] = df[[c for c in FIELDS if c in df.columns]].astype(float)
    return out


# ------------------------------------------------------------------ validity
def validate_daily_bars(bars: dict[str, pd.DataFrame], symbols: Iterable[str],
                        sessions: list[str]) -> list[str]:
    """Problems with the daily bars; empty means valid. Every requested symbol
    must have exactly the expected sessions, finite positive O/H/L/C with
    High >= max(Open, Close) and Low <= min(Open, Close), and a finite
    non-negative Volume."""
    problems: list[str] = []
    for sym in symbols:
        df = bars.get(sym)
        if df is None or df.empty:
            problems.append(f"{sym}: no daily bars")
            continue
        missing_cols = [c for c in FIELDS if c not in df.columns]
        if missing_cols:
            problems.append(f"{sym}: missing {','.join(missing_cols)}")
            continue
        got = [pd.Timestamp(i).date().isoformat() for i in df.index]
        if got != sessions:
            problems.append(f"{sym}: sessions {got[:1]}..{got[-1:]} ({len(got)}) "
                            f"!= expected {sessions[0]}..{sessions[-1]} ({len(sessions)})")
            continue
        for ts, o, h, lo, c, v in df[list(FIELDS)].itertuples(name=None):
            vals = (o, h, lo, c)
            if not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in vals):
                problems.append(f"{sym} {pd.Timestamp(ts).date()}: non-finite or non-positive price")
                break
            if h < max(o, c) or lo > min(o, c) or h < lo:
                problems.append(f"{sym} {pd.Timestamp(ts).date()}: inconsistent OHLC")
                break
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v >= 0):
                problems.append(f"{sym} {pd.Timestamp(ts).date()}: bad volume")
                break
    return problems


# --------------------------------------------------------------------- cache
def cache_path(session_date: date, cache_dir: Path | None = None) -> Path:
    return (cache_dir or DAILY_CACHE_DIR) / f"{session_date.isoformat()}.json"


def write_cache(path: Path, session_date: date, sessions: list[str],
                bars: dict[str, pd.DataFrame], symbols: list[str]) -> None:
    """One symbol per line, so a day's file diffs readably. Atomic replace."""
    head = {"session_date": session_date.isoformat(),
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": SOURCE, "sessions": sessions}
    lines = ["{", *(f"  {json.dumps(k)}: {json.dumps(v)}," for k, v in head.items()),
             '  "bars": {']
    body = []
    for sym in symbols:
        cols = {c: [float(x) for x in bars[sym][c].tolist()] for c in FIELDS}
        body.append(f"    {json.dumps(sym)}: {json.dumps(cols, separators=(',', ':'))}")
    lines.append(",\n".join(body))
    lines += ["  }", "}", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def read_cache(path: Path, session_date: date) -> tuple[list[str], dict[str, pd.DataFrame]]:
    """The cached sessions and bars. Raises DailyFrameError on anything off."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise DailyFrameError(f"cache unreadable: {e}") from e
    if doc.get("session_date") != session_date.isoformat():
        raise DailyFrameError(f"cache is for {doc.get('session_date')}, not {session_date}")
    sessions = list(doc.get("sessions") or [])
    idx = pd.DatetimeIndex([pd.Timestamp(s) for s in sessions], name="Date")
    bars: dict[str, pd.DataFrame] = {}
    try:
        for sym, cols in (doc.get("bars") or {}).items():
            bars[sym] = pd.DataFrame({c: [float(x) for x in cols[c]] for c in FIELDS}, index=idx)
    except (KeyError, TypeError, ValueError) as e:
        raise DailyFrameError(f"cache malformed: {e}") from e
    return sessions, bars


def daily_bars_for_session(symbols: list[str], session_date: date,
                           cache_dir: Path | None = None,
                           fetch: Callable[[list[str], date], dict[str, pd.DataFrame]] | None = None,
                           ) -> tuple[dict[str, pd.DataFrame], str]:
    """The session's validated daily bars and whether they came from the cache.

    A cache that is missing, unreadable, for another day or invalid for this
    tick's symbols is a miss: fetch, validate, and write it only if valid.
    Raises DailyFrameError when no valid bars can be had this tick.
    """
    sessions = expected_sessions(session_date)
    path = cache_path(session_date, cache_dir)
    if path.exists():
        try:
            c_sessions, c_bars = read_cache(path, session_date)
            if c_sessions == sessions and not validate_daily_bars(c_bars, symbols, sessions):
                return {s: c_bars[s] for s in symbols}, CACHE_HIT
            logger.warning("Daily cache %s does not cover this tick; fetching again", path.name)
        except DailyFrameError as e:
            logger.warning("Daily cache %s rejected (%s); fetching again", path.name, e)
    bars = (fetch or fetch_daily_bars)(symbols, session_date)
    bars = {s: (bars.get(s, pd.DataFrame()).tail(COMPLETED_SESSIONS)
                if bars.get(s) is not None else pd.DataFrame()) for s in symbols}
    problems = validate_daily_bars(bars, symbols, sessions)
    if problems:
        raise DailyFrameError(f"{len(problems)} daily-bar problem(s): " + "; ".join(problems[:5]))
    write_cache(path, session_date, sessions, bars, symbols)
    # Return what a later tick will read back, so a hit and a fetch give the
    # same frame bit for bit.
    _, bars = read_cache(path, session_date)
    return {s: bars[s] for s in symbols}, CACHE_FETCHED


# ------------------------------------------------------------------ assembly
def today_bar(intraday: pd.DataFrame, price: float, session_date: date) -> pd.DataFrame:
    """Today's partial daily bar from the guard-accepted intraday frame."""
    opens = pd.to_numeric(intraday["Open"], errors="coerce").dropna()
    highs = pd.to_numeric(intraday["High"], errors="coerce").dropna()
    lows = pd.to_numeric(intraday["Low"], errors="coerce").dropna()
    vols = pd.to_numeric(intraday["Volume"], errors="coerce").dropna()
    if opens.empty or highs.empty or lows.empty:
        raise DailyFrameError("intraday frame has no valid Open/High/Low")
    o = float(opens.iloc[0])
    row = {"Open": o,
           "High": max(float(highs.max()), price),
           "Low": min(float(lows.min()), price),
           "Close": float(price),
           "Volume": float(vols.sum())}
    if not all(math.isfinite(v) for v in row.values()) or o <= 0:
        raise DailyFrameError("today's partial bar is not finite")
    return pd.DataFrame([row], index=pd.DatetimeIndex([pd.Timestamp(session_date)], name="Date"))


def assemble(intraday_frames: dict[str, pd.DataFrame], prices: dict[str, float],
             bars: dict[str, pd.DataFrame], session_date: date) -> dict[str, pd.DataFrame]:
    """The prompt frames, in the intraday dict's order. A ticker the guard left
    unpriced keeps its empty frame (it renders NO DATA, as before)."""
    out: dict[str, pd.DataFrame] = {}
    for sym, df in intraday_frames.items():
        if sym not in prices or df is None or df.empty:
            out[sym] = pd.DataFrame()
            continue
        daily = bars[sym][list(FIELDS)]
        out[sym] = pd.concat([daily, today_bar(df, prices[sym], session_date)])
    return out


def build_prompt_frame(intraday_frames: dict[str, pd.DataFrame], prices: dict[str, float],
                       run_date: datetime, send_alert: Callable[..., Any] | None = None,
                       cache_dir: Path | None = None,
                       fetch: Callable[[list[str], date], dict[str, pd.DataFrame]] | None = None,
                       ) -> PromptFrame:
    """This tick's prompt frame. Never raises: on any failure the whole tick
    falls back to the intraday frame, flagged, with one WARN."""
    execution_hash = frame_sha256(intraday_frames)
    session_date = run_date.date()
    try:
        symbols = list(intraday_frames.keys())
        bars, cache = daily_bars_for_session(symbols, session_date, cache_dir,
                                             fetch or fetch_daily_bars)
        frames = assemble(intraday_frames, prices, bars, session_date)
        pf = PromptFrame(frames, FRAME_DAILY, frame_sha256(frames), execution_hash, None, cache)
        logger.info("Prompt frame: daily (%s cache, %d sessions + today, %d priced tickers)",
                    cache, COMPLETED_SESSIONS, sum(1 for f in frames.values() if not f.empty))
        return pf
    except Exception as e:  # noqa: BLE001 -- the prompt frame must never cost a tick
        reason = f"{type(e).__name__}: {e}"[:500]
        logger.warning("Prompt frame falls back to intraday: %s", reason)
        if send_alert is not None:
            try:
                send_alert("WARN", "Prompt frame fell back to intraday bars",
                           "This tick's prompts were built from today's intraday bars, as "
                           "before daily_history_restoration, because the daily frame "
                           "could not be built. Execution is unaffected.",
                           {"session": session_date.isoformat(),
                            "tick": run_date.isoformat(timespec="minutes"),
                            "reason": reason},
                           kind="prompt_frame_fallback",
                           dedup_key=f"prompt_frame_fallback:{run_date.isoformat(timespec='minutes')}")
            except Exception:  # noqa: BLE001
                logger.exception("prompt-frame fallback alert failed")
        return PromptFrame(intraday_frames, FRAME_FALLBACK, execution_hash, execution_hash,
                           reason, None)
