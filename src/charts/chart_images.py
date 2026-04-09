"""Server-side chart image generation for multimodal LLM prompts.

We can't use TradingView for image export — their public API doesn't expose
one. Instead we render charts with matplotlib + mplfinance directly from the
yfinance daily frames the pipeline already has, then base64-encode and send
them to vision-capable models alongside the numerical context block.

Single-call composite design: ONE PNG per pipeline tick containing all 21
universe tickers as small candlestick panels with a 20-day SMA overlay.
This keeps the per-call image cost at exactly one image instead of 21 — a
deliberate trade-off to keep the multimodal token bill bounded while still
giving the model enough chart-pattern context to reason about.

Per-holding deeper-dive charts are deferred to a phase-2 follow-up.
"""
from __future__ import annotations

import base64
import io
import logging
import math
from typing import Any

import matplotlib

# Use the non-interactive Agg backend so this works in headless CI
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

logger = logging.getLogger("llmlab.charts")

# Visual config — kept consistent with the dashboard's Bloomberg palette so
# the model sees the same color semantics across the prompt and the live UI.
BG = "#05070b"
GRID = "#1a2235"
TEXT = "#c8d4e6"
TEXT_DIM = "#5f6b80"
GREEN = "#00d488"
RED = "#ff3355"
SMA_COLOR = "#2b8aff"


def _draw_single_panel(ax, ticker: str, df: pd.DataFrame, sma_window: int = 20) -> None:
    """Render one ticker as a compact candlestick panel with a 20-SMA line.

    We hand-draw the candles instead of using mplfinance directly because
    mplfinance creates one Figure per call which is awkward when we want
    21 sub-axes inside a single composite Figure. The drawing logic is
    short and matches what mplfinance would output for `type=candle`.
    """
    if df is None or df.empty:
        ax.text(0.5, 0.5, f"{ticker}\nNO DATA",
                ha="center", va="center",
                color=TEXT_DIM, fontsize=8,
                transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color(GRID)
        ax.set_facecolor(BG)
        return

    # Trim to last ~30 bars so each panel stays readable at small size
    df = df.tail(30).copy()
    df = df.reset_index(drop=True)

    opens = df["Open"].astype(float).values
    highs = df["High"].astype(float).values
    lows = df["Low"].astype(float).values
    closes = df["Close"].astype(float).values

    # Wicks
    for i in range(len(df)):
        color = GREEN if closes[i] >= opens[i] else RED
        ax.vlines(i, lows[i], highs[i], color=color, linewidth=0.6)
    # Bodies
    for i in range(len(df)):
        color = GREEN if closes[i] >= opens[i] else RED
        bottom = min(opens[i], closes[i])
        height = abs(closes[i] - opens[i])
        if height < 1e-9:
            # Doji — draw a thin horizontal line so it's still visible
            ax.hlines(opens[i], i - 0.35, i + 0.35, color=color, linewidth=0.8)
        else:
            ax.add_patch(plt.Rectangle(
                (i - 0.35, bottom),
                0.7,
                height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.4,
            ))

    # SMA overlay
    if len(closes) >= sma_window:
        sma = pd.Series(closes).rolling(window=sma_window, min_periods=1).mean()
        ax.plot(range(len(sma)), sma.values, color=SMA_COLOR, linewidth=0.9, alpha=0.85)

    # Period return label (last vs first close)
    if len(closes) >= 2 and closes[0] > 0:
        ret = closes[-1] / closes[0] - 1
        ret_color = GREEN if ret >= 0 else RED
        ret_str = f"{'+' if ret >= 0 else ''}{ret*100:.1f}%"
    else:
        ret_color = TEXT_DIM
        ret_str = "—"

    ax.set_title(f"{ticker}  {ret_str}",
                 color=ret_color, fontsize=8.5, fontweight="bold",
                 pad=2, loc="left")

    # Cosmetic
    ax.set_facecolor(BG)
    ax.set_xticks([])
    ax.tick_params(axis="y", colors=TEXT_DIM, labelsize=6, length=2, pad=1)
    for spine in ax.spines.values():
        spine.set_color(GRID)
        spine.set_linewidth(0.5)
    ax.grid(True, color=GRID, linewidth=0.3, alpha=0.5)
    ax.margins(x=0.02)


def build_universe_overview_png(
    market_data: dict[str, pd.DataFrame],
    title: str = "UNIVERSE OVERVIEW",
    subtitle: str | None = None,
    cols: int = 5,
) -> bytes:
    """Render all universe tickers as a single composite candlestick PNG.

    Returns raw PNG bytes (caller can base64-encode for the multimodal
    payload, or write to disk for debugging).
    """
    tickers = list(market_data.keys())
    n = len(tickers)
    if n == 0:
        # Defensive: still produce a "no data" image so adapters don't crash
        fig = plt.figure(figsize=(8, 2), facecolor=BG)
        ax = fig.add_subplot(111)
        ax.text(0.5, 0.5, "NO MARKET DATA",
                ha="center", va="center", color=TEXT_DIM, fontsize=14,
                transform=ax.transAxes)
        ax.set_facecolor(BG)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color(GRID)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=BG, dpi=110, bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()

    rows = math.ceil(n / cols)

    # Sized for ~1100px wide at 110 dpi — safely under all model image limits
    fig_w = 10.0
    fig_h = max(2.4, rows * 1.7 + 0.8)
    fig = plt.figure(figsize=(fig_w, fig_h), facecolor=BG)
    fig.patch.set_facecolor(BG)

    # Title bar
    title_text = title
    if subtitle:
        title_text = f"{title}   |   {subtitle}"
    fig.suptitle(title_text,
                 color=TEXT, fontsize=11, fontweight="bold",
                 x=0.012, y=0.985, ha="left")

    # Build axes grid
    for idx, ticker in enumerate(tickers):
        ax = fig.add_subplot(rows, cols, idx + 1)
        _draw_single_panel(ax, ticker, market_data.get(ticker))

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.subplots_adjust(hspace=0.55, wspace=0.30)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def encode_png_b64(png_bytes: bytes) -> str:
    """Base64-encode raw PNG bytes for inclusion in multimodal API payloads."""
    return base64.b64encode(png_bytes).decode("ascii")
