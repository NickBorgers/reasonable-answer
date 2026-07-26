#!/usr/bin/env python3
"""Regenerate the placeholder home-screen icons.

Run by hand, never by the app:

    uv run python scripts/make-icons.py

The icons it writes are *placeholders*. To use your own artwork you do not need this
script at all — drop your PNGs into `src/reasonable_answer/web/static/icons/` with the
same filenames and pixel sizes and restart the server. See the README in that directory.

Why generate them in Python instead of committing something drawn elsewhere: it keeps the
mark reproducible and reviewable as source, and it costs no dependency. PNG is an easy
format to write — a signature, three chunks, and zlib — and the shapes are drawn from
signed distance fields, which gives real anti-aliasing at one sample per pixel instead of
the 16 a supersampled rasteriser would need. A 512x512 icon is a quarter of a million
pixels; in pure Python that difference is the difference between a second and a minute.

Output is deterministic: no timestamp chunk and a fixed compression level, so re-running
this on an unchanged mark produces a byte-identical file and no diff churn.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ICONS = Path(__file__).resolve().parent.parent / "src" / "reasonable_answer" / "web" / "static" / "icons"

#: `--accent` and `--bg` from the light palette in `web/render.py`. The icon is the one
#: surface that cannot follow `prefers-color-scheme`, so it commits to the light pairing.
ACCENT = (0x2F, 0x5D, 0x50)
INK = (0xFB, 0xFA, 0xF8)


# ------------------------------------------------------------------ distance fields
#
# Each returns the signed distance from a point to a shape, in pixels: negative inside,
# positive outside, and — because these are true distances rather than a mere inside test
# — `0.5 - d` clamped to 0..1 is the pixel's coverage. That is the anti-aliasing.


def _rounded_box(px: float, py: float, cx: float, cy: float, half: float, radius: float) -> float:
    qx = abs(px - cx) - half + radius
    qy = abs(py - cy) - half + radius
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return outside + min(max(qx, qy), 0.0) - radius


def _capsule(
    px: float, py: float, ax: float, ay: float, bx: float, by: float, radius: float
) -> float:
    """Distance to a line segment, minus the stroke radius — a stroke with round caps."""
    pax, pay = px - ax, py - ay
    bax, bay = bx - ax, by - ay
    denom = bax * bax + bay * bay
    h = 0.0 if denom == 0 else max(0.0, min(1.0, (pax * bax + pay * bay) / denom))
    return math.hypot(pax - bax * h, pay - bay * h) - radius


# ------------------------------------------------------------------------ the mark
#
# A check: two round-capped strokes, given in fractions of the glyph's own box so it can
# be placed and scaled independently of the plate behind it.

_CHECK = ((0.13, 0.52), (0.40, 0.79), (0.88, 0.20))
_CHECK_STROKE = 0.155


def _check_distance(px: float, py: float, x0: float, y0: float, size: float) -> float:
    """Union of the two strokes: the *minimum* of the two distances, not the maximum of
    two coverages — taking it before the coverage is what keeps the elbow seamless."""
    (ax, ay), (bx, by), (cx, cy) = _CHECK
    radius = _CHECK_STROKE * size / 2
    short = _capsule(px, py, x0 + ax * size, y0 + ay * size, x0 + bx * size, y0 + by * size, radius)
    long = _capsule(px, py, x0 + bx * size, y0 + by * size, x0 + cx * size, y0 + cy * size, radius)
    return min(short, long)


def _coverage(distance: float) -> float:
    return max(0.0, min(1.0, 0.5 - distance))


def render(size: int, *, corner: float, glyph: float, opaque: bool) -> bytearray:
    """RGBA pixels for one icon.

    `corner` is the plate's corner radius as a fraction of the size — 0 for a square
    plate. `glyph` is the check's width as a fraction of the size. `opaque` fills the
    whole canvas with the plate colour regardless of the corner radius, which is what
    iOS and Android maskable icons need: both apply their own mask, and a transparent
    corner under it shows through as a notch.
    """
    radius = corner * size
    glyph_size = glyph * size
    origin = (size - glyph_size) / 2
    rows = bytearray()
    for y in range(size):
        rows.append(0)  # PNG filter type 0 (None) for this scanline
        py = y + 0.5
        for x in range(size):
            px = x + 0.5
            plate = 1.0 if opaque else _coverage(_rounded_box(px, py, size / 2, size / 2, size / 2, radius))
            mark = _coverage(_check_distance(px, py, origin, origin, glyph_size))
            # The glyph is composited over the plate, so where it is opaque the pixel is
            # ink even if the plate under it is partly transparent at the corner.
            alpha = max(plate, mark)
            if alpha <= 0.0:
                rows += b"\x00\x00\x00\x00"
                continue
            rows += bytes(
                round(a * mark + b * (1 - mark)) for a, b in zip(INK, ACCENT, strict=True)
            ) + bytes((round(alpha * 255),))
    return rows


# --------------------------------------------------------------------------- PNG


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def write_png(path: Path, size: int, rows: bytes) -> None:
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit, colour type 6 = RGBA
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + _chunk(b"IEND", b"")
    )


# The four files and why each is shaped the way it is. The names are the contract with
# `web/assets.py` and `static/manifest.webmanifest`; changing one means changing all three.
TARGETS = (
    # name, size, corner radius, glyph width, opaque
    # Browser and Android "any" icons: a rounded plate on transparency, drawn as it ships.
    ("icon-192.png", 192, 0.22, 0.52, False),
    ("icon-512.png", 512, 0.22, 0.52, False),
    # Android maskable: full-bleed and opaque, because the launcher crops to its own shape.
    # The glyph stays inside the safe circle (80% of the width), so a circular crop keeps it.
    ("maskable-512.png", 512, 0.0, 0.40, True),
    # iOS: opaque with square corners — iOS applies its own squircle, and rounding it here
    # would show the mask's corners cutting into an already-rounded plate.
    ("apple-touch-icon.png", 180, 0.0, 0.52, True),
)


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    for name, size, corner, glyph, opaque in TARGETS:
        write_png(ICONS / name, size, render(size, corner=corner, glyph=glyph, opaque=opaque))
        print(f"wrote {ICONS / name} ({size}x{size})")


if __name__ == "__main__":
    main()
