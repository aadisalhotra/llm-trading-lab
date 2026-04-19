"""Generate favicon.ico + apple-touch-icon.png from the LLM Trading Lab mark.

Renders the 52x48 SVG candlestick mark to raster bitmaps using Pillow. The 16x16
favicon slot uses a simplified layout (candlestick bodies + two signal dots, no
wicks) because thin elements alias poorly at that size.

Run:
    python scripts/generate_favicons.py
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw

log = logging.getLogger("favicons")
logging.basicConfig(level=logging.INFO, format="%(message)s")

# Dashboard dark surface. Favicons bake this in so the mark reads on any tab.
BG_DARK = (10, 14, 23, 255)  # #0a0e17

RED = (239, 68, 68)
GREEN = (34, 197, 94)
BLUE = (74, 122, 237)

VIEW_W, VIEW_H = 52, 48

# (x, y, w, h, rgb, alpha, rx) — lifted directly from the SVG source.
RECTS = [
    (5, 6, 1.2, 34, RED, 0.35, 0.6),
    (1.5, 12, 8, 14, RED, 0.85, 1.5),
    (16, 4, 1.2, 32, GREEN, 0.40, 0.6),
    (12.5, 8, 8, 16, GREEN, 0.90, 1.5),
    (27, 10, 1.2, 28, RED, 0.35, 0.6),
    (23.5, 16, 8, 10, RED, 0.80, 1.5),
    (38, 2, 1.2, 36, GREEN, 0.45, 0.6),
    (34.5, 4, 8, 22, GREEN, 1.0, 1.5),
    (0, 43, 44, 0.8, BLUE, 0.15, 0.4),
]
CIRCLES = [
    (48, 12, 1.4, BLUE, 0.6),
    (48, 23, 1.4, BLUE, 0.4),
    (48, 34, 1.4, BLUE, 0.2),
]
LINES = [
    (48, 13.4, 48, 21.6, BLUE, 0.25, 0.5),
    (48, 24.4, 48, 32.6, BLUE, 0.15, 0.5),
]


def _rgba(color: tuple[int, int, int], alpha: float) -> tuple[int, int, int, int]:
    return color + (max(0, min(255, int(round(alpha * 255)))),)


def render_full(size: int, pad_frac: float = 0.06) -> Image.Image:
    """Render the full mark scaled into a square `size`x`size` image on dark bg."""
    base = Image.new("RGBA", (size, size), BG_DARK)
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")

    # Scale to fit the SVG width, honor aspect, center vertically.
    scale = (size * (1 - 2 * pad_frac)) / VIEW_W
    offset_x = size * pad_frac
    offset_y = (size - VIEW_H * scale) / 2

    def to_canvas(x: float, y: float) -> tuple[float, float]:
        return offset_x + x * scale, offset_y + y * scale

    for x, y, w, h, color, alpha, rx in RECTS:
        x0, y0 = to_canvas(x, y)
        x1, y1 = to_canvas(x + w, y + h)
        fill = _rgba(color, alpha)
        r_px = max(0, int(round(rx * scale)))
        box = (x0, y0, x1, y1)
        width_px = x1 - x0
        height_px = y1 - y0
        if r_px >= 1 and width_px >= 2 * r_px and height_px >= 2 * r_px:
            draw.rounded_rectangle(box, radius=r_px, fill=fill)
        else:
            draw.rectangle(box, fill=fill)

    for x1, y1, x2, y2, color, alpha, lw in LINES:
        sx1, sy1 = to_canvas(x1, y1)
        sx2, sy2 = to_canvas(x2, y2)
        draw.line(
            (sx1, sy1, sx2, sy2),
            fill=_rgba(color, alpha),
            width=max(1, int(round(lw * scale))),
        )

    for cx, cy, r, color, alpha in CIRCLES:
        x0, y0 = to_canvas(cx - r, cy - r)
        x1, y1 = to_canvas(cx + r, cy + r)
        draw.ellipse((x0, y0, x1, y1), fill=_rgba(color, alpha))

    return Image.alpha_composite(base, overlay)


def render_simple_16() -> Image.Image:
    """Simplified 16x16: four candlestick bodies (no wicks) + two signal dots."""
    size = 16
    base = Image.new("RGBA", (size, size), BG_DARK)
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")

    # Bodies: 2px wide, spaced 3px apart. Heights echo the full mark's rhythm.
    bodies = [
        (1, 5, 2, 9, _rgba(RED, 0.90)),     # down
        (4, 3, 5, 10, _rgba(GREEN, 0.95)),  # up, taller
        (7, 6, 8, 10, _rgba(RED, 0.85)),    # down, short
        (10, 2, 11, 12, _rgba(GREEN, 1.0)), # up, tallest
    ]
    for x0, y0, x1, y1, color in bodies:
        draw.rectangle((x0, y0, x1, y1), fill=color)

    # Two blue signal dots on the right, fading downward.
    dots = [
        (13, 5, 14, 6, _rgba(BLUE, 0.70)),
        (13, 9, 14, 10, _rgba(BLUE, 0.40)),
    ]
    for x0, y0, x1, y1, color in dots:
        draw.rectangle((x0, y0, x1, y1), fill=color)

    return Image.alpha_composite(base, overlay)


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "dashboard"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Apple touch icon — flattened PNG, no alpha (iOS masks it into a rounded square).
    apple = render_full(180, pad_frac=0.08).convert("RGB")
    apple_path = out_dir / "apple-touch-icon.png"
    apple.save(apple_path, "PNG", optimize=True)
    log.info("wrote %s", apple_path)

    # Favicon ICO — 16x16 (simplified) + 32x32 (full). Pillow uses native image
    # sizes when append_images entries match entries in `sizes`.
    fav32 = render_full(32, pad_frac=0.05)
    fav16 = render_simple_16()
    ico_path = out_dir / "favicon.ico"
    fav32.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (32, 32)],
        append_images=[fav16],
    )
    log.info("wrote %s", ico_path)


if __name__ == "__main__":
    main()
