"""Replay demo for daily_history_restoration (hub GO 2026-11-21).

One past tick rendered from both frames, side by side: the intraday frame the
tick actually prompted from (today's 30-minute bars only) and the daily prompt
frame (30 completed sessions + today's partial bar) that src/data/prompt_frame.py
builds from the same guard-accepted frame.

The tick's intraday frame is reconstructed from 1-minute bars cut at the tick's
recorded fetch_started_at and grouped into 30-minute bars from 09:30 ET, so
the in-progress bar is partial exactly as at the fetch. The reconstruction is
checked against the tick's own record: every held name's current_price in
portfolio_after is the price the tick used. Daily bars come from the same
batched fetch the pipeline uses, into a temporary cache (never data/).

Renders with the PRODUCTION prompt_builder and chart code, unchanged:
  * the decision prompt's MARKET DATA block over the model's recorded shortlist
    (YOUR_LAST_ACTION column omitted: it depends on the book, not the frame);
  * the screening block over the universe;
  * the universe chart from both frames, and a side-by-side composite.

Usage:
  python scripts/demos/daily_history_replay.py --cycle 20261120T160248Z --model claude \
      --out docs/ops/daily_history_2026-11-21
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from src import prompt_builder  # noqa: E402
from src.charts import build_universe_overview_png  # noqa: E402
from src.charts import chart_images  # noqa: E402
from src.config_loader import DATA_DIR, load_settings, load_universe, universe_symbols  # noqa: E402
from src.data import prompt_frame as pf  # noqa: E402
from src.data.price_validity import ingest_market_data  # noqa: E402

ET = ZoneInfo("America/New_York")
log = logging.getLogger("daily_history_replay")


def tick_record(cycle: str, model: str) -> dict:
    path = DATA_DIR / "trades" / f"{model}_{cycle[:4]}-{cycle[4:6]}.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("cycle_id") == cycle:
            return r
    raise SystemExit(f"cycle {cycle} not in {path}")


def reconstruct_intraday(symbols: list[str], fetched_at: datetime) -> dict[str, pd.DataFrame]:
    """The tick's 30-minute frame from 1-minute bars before the fetch minute."""
    import yfinance as yf
    day = fetched_at.astimezone(ET).date()
    raw = yf.download(symbols, start=day.isoformat(),
                      end=(pd.Timestamp(day) + pd.Timedelta(days=1)).date().isoformat(),
                      interval="1m", group_by="ticker", auto_adjust=False,
                      progress=False, threads=True, prepost=False)
    cut = pd.Timestamp(fetched_at).tz_convert(ET).floor("min")
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            m = raw[sym].dropna(how="all")
        except KeyError:
            out[sym] = pd.DataFrame()
            continue
        m = m.tz_convert(ET) if m.index.tz is not None else m.tz_localize("UTC").tz_convert(ET)
        m = m[m.index < cut]
        if m.empty:
            out[sym] = pd.DataFrame()
            continue
        g = m.resample("30min")
        bars = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(),
                             "Low": g["Low"].min(), "Close": g["Close"].last(),
                             "Adj Close": g["Adj Close"].last(),
                             "Volume": g["Volume"].sum()}).dropna(subset=["Close"])
        out[sym] = bars
    return out


def _png_side_by_side(left: bytes, right: bytes) -> bytes:
    from PIL import Image
    a, b = Image.open(io.BytesIO(left)), Image.open(io.BytesIO(right))
    canvas = Image.new("RGB", (a.width + b.width + 16, max(a.height, b.height)), "white")
    canvas.paste(a, (0, 0))
    canvas.paste(b, (a.width + 16, 0))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def _sma_drawn(df: pd.DataFrame) -> bool:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    try:
        chart_images._draw_single_panel(ax, "T", df)
        return any(matplotlib.colors.same_color(ln.get_color(), chart_images.SMA_COLOR)
                   for ln in ax.get_lines())
    finally:
        plt.close(fig)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Render one past tick from both frames.")
    ap.add_argument("--cycle", required=True)
    ap.add_argument("--model", default="claude")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    row = tick_record(a.cycle, a.model)
    fetched_at = datetime.fromisoformat(row["fetch_started_at"])
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    run_date = fetched_at.astimezone(ET)
    symbols = universe_symbols() + [load_settings()["benchmark_ticker"]]

    intraday = reconstruct_intraday(symbols, fetched_at)
    ing = ingest_market_data(intraday)

    # The reconstruction against the tick's own record.
    held = {h["ticker"]: float(h["current_price"])
            for h in (row.get("portfolio_after") or {}).get("holdings", [])}
    price_check = {t: {"recorded": p, "reconstructed": ing.prices.get(t),
                       "abs_diff": (None if ing.prices.get(t) is None
                                    else round(abs(ing.prices[t] - p), 6))}
                   for t, p in sorted(held.items())}
    legacy_hash = prompt_builder.hash_inputs(ing.market_data)

    with tempfile.TemporaryDirectory() as tmp:
        alerts: list = []
        frame = pf.build_prompt_frame(ing.market_data, ing.prices, run_date,
                                      send_alert=lambda *x, **k: alerts.append(x[:2]),
                                      cache_dir=Path(tmp))
    if frame.prompt_frame != pf.FRAME_DAILY:
        raise SystemExit(f"daily frame not built: {frame.fallback_reason}")

    shortlist = list(row.get("screening_shortlist") or [])
    scoped = {k: ing.market_data[k] for k in ing.market_data if k in set(shortlist)}
    scoped_daily = {k: frame.frames[k] for k in frame.frames if k in set(shortlist)}
    order = [{"symbol": t["symbol"], "sector": t.get("sector", "")}
             for t in load_universe()["tickers"]]

    blocks = {
        "market_intraday": prompt_builder._format_market_data_block(scoped),
        "market_daily": prompt_builder._format_market_data_block(scoped_daily),
        "screening_intraday": prompt_builder._format_screening_data_block(ing.market_data, {}, {}, order),
        "screening_daily": prompt_builder._format_screening_data_block(frame.frames, {}, {}, order),
    }
    for name, text in blocks.items():
        (out / f"{name}.txt").write_text(text + "\n", encoding="utf-8", newline="\n")

    sub = run_date.strftime("%Y-%m-%d %H:%M ET")
    png_old = build_universe_overview_png(scoped, title="UNIVERSE OVERVIEW", subtitle=sub)
    png_new = build_universe_overview_png(scoped_daily, title="UNIVERSE OVERVIEW", subtitle=sub)
    (out / "chart_intraday.png").write_bytes(png_old)
    (out / "chart_daily.png").write_bytes(png_new)
    (out / "chart_side_by_side.png").write_bytes(_png_side_by_side(png_old, png_new))

    def rows(block: str) -> dict[str, list[str]]:
        return {ln.split()[0]: ln.split()[1:] for ln in block.splitlines()[2:] if ln.strip()}
    old_rows, new_rows = rows(blocks["market_intraday"]), rows(blocks["market_daily"])
    side = ["# Daily history replay: tick " + a.cycle,
            "",
            f"Model {a.model}, fetch {row['fetch_started_at']} ({sub}), shortlist of "
            f"{len(shortlist)}. Left: the frame the tick prompted from. Right: the daily "
            "prompt frame. Columns CLOSE 1D% 5D% 30D% 30D_HI 30D_LO AVG_VOL.",
            "",
            "| Ticker | Intraday frame (as sent) | Daily frame |",
            "|---|---|---|"]
    for t in old_rows:
        side.append(f"| {t} | {' '.join(old_rows[t])} | {' '.join(new_rows.get(t, []))} |")
    side += ["", "![chart, intraday frame left, daily frame right](chart_side_by_side.png)", ""]
    (out / "side_by_side.md").write_text("\n".join(side), encoding="utf-8", newline="\n")

    sample = next(iter(scoped_daily))
    record = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cycle_id": a.cycle, "model": a.model, "fetch_started_at": row["fetch_started_at"],
        "tick_et": sub, "shortlist": shortlist,
        "logged_data_inputs_hash_legacy": row.get("data_inputs_hash"),
        "reconstructed_hash_inputs_legacy": legacy_hash,
        "legacy_hash_matches": legacy_hash == row.get("data_inputs_hash"),
        "held_price_check": price_check,
        "held_prices_matching_exactly": sum(1 for v in price_check.values() if v["abs_diff"] == 0),
        "held_prices_total": len(price_check),
        "max_abs_price_diff": max((v["abs_diff"] for v in price_check.values()
                                   if v["abs_diff"] is not None), default=None),
        "max_rel_price_diff": max((round(v["abs_diff"] / v["recorded"], 6)
                                   for v in price_check.values()
                                   if v["abs_diff"] is not None), default=None),
        "prompt_frame": frame.prompt_frame, "fallback_alerts": len(alerts),
        "data_inputs_hash_daily": frame.data_inputs_hash,
        "execution_frame_hash": frame.execution_frame_hash,
        "bars_per_priced_ticker": {"intraday": len(ing.market_data[sample]),
                                   "daily": len(frame.frames[sample])},
        "daily_sessions": [str(frame.frames[sample].index[0].date()),
                           str(frame.frames[sample].index[-2].date())],
        "sma_drawn": {"intraday": _sma_drawn(ing.market_data[sample]),
                      "daily": _sma_drawn(frame.frames[sample])},
        "unpriced_tickers": sorted(ing.missing),
        "files": sorted(p.name for p in out.iterdir() if p.name != "replay.json"),
        "notes": [
            "The intraday frame is reconstructed from 1-minute bars before the fetch "
            "minute; Yahoo's live in-progress bar at fetch time can differ from the "
            "minute bars served later, so held_price_check states how close it is.",
            "Daily bars were fetched on the demo date for sessions before the tick's "
            "session; vendor revisions since then, if any, are not visible here.",
            "Rendered with the production prompt_builder and chart code, unchanged.",
        ],
    }
    (out / "replay.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8",
                                     newline="\n")
    print(json.dumps({k: record[k] for k in ("cycle_id", "tick_et", "legacy_hash_matches",
                                             "held_prices_matching_exactly", "held_prices_total",
                                             "max_abs_price_diff", "bars_per_priced_ticker",
                                             "daily_sessions", "sma_drawn")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
