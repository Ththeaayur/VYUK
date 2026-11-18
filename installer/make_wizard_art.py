"""Deterministic generator for the Inno Setup wizard art — Wave 10
(v5.65) Leaf 1, PJ-61.

Produces, beside this script unless --out-dir is given:

* wizard_image.bmp — 164x314 (Inno WizardImageFile, the tall left
  panel of the welcome/finished pages)
* wizard_small.bmp — 55x58 (Inno WizardSmallImageFile, the small
  header image on the inner pages)

Art direction: abstract, premium, Aura-adjacent — a deep blue/teal
glow field with a soft, faceless orb-like bloom. No faces, no text,
no logos (nothing here reproduces a mark VYUK does not own).

Implementation is stdlib only (argparse, math, os, struct): the
generator must run on a bare Windows build Python with no Pillow
installed. Output is byte-for-byte deterministic: the pixel field
is pure math over fixed constants (no time, no randomness, no
hostname), and the BMP writer emits a fixed 54-byte header with no
timestamp field anywhere in the format. Re-running the generator
over the shipped BMPs must produce identical bytes — the test
suite checks exactly that.

BMP format written: 24-bit, uncompressed (BI_RGB), bottom-up rows,
each row padded to a 4-byte boundary.
"""

from __future__ import annotations

import argparse
import math
import os
import struct

WIZARD_IMAGE_SIZE = (164, 314)
WIZARD_SMALL_SIZE = (55, 58)

# Aura-adjacent palette (fixed constants — the art's only inputs).
_TOP = (9, 20, 48)        # deep navy, top edge
_BOTTOM = (5, 38, 52)      # deep teal, bottom edge
_GLOW_BLUE = (64, 128, 255)
_GLOW_TEAL = (45, 216, 196)
_CORE = (190, 235, 255)


def _clamp(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _glow(x: float, y: float, cx: float, cy: float,
          radius: float) -> float:
    """Soft radial falloff in [0, 1]: 1 at the centre, ~0 at radius."""
    dx = (x - cx) / radius
    dy = (y - cy) / radius
    dist_sq = dx * dx + dy * dy
    if dist_sq >= 1.0:
        return 0.0
    return (1.0 - dist_sq) ** 2


def render_aura_pixels(width: int, height: int) -> list[list[tuple[int, int, int]]]:
    """Top-down rows of (r, g, b) pixels for an abstract glow field.

    Composition, all fixed: a vertical navy-to-teal gradient; a
    large soft blue bloom upper-left-of-centre; a teal bloom
    lower-right; a small bright core where they overlap (the
    orb-like bloom, deliberately faceless and mark-free); and a
    faint diagonal sheen so the field does not read as flat.
    """
    rows: list[list[tuple[int, int, int]]] = []
    for y in range(height):
        v = y / max(1, height - 1)
        row: list[tuple[int, int, int]] = []
        for x in range(width):
            u = x / max(1, width - 1)
            r = _TOP[0] + (_BOTTOM[0] - _TOP[0]) * v
            g = _TOP[1] + (_BOTTOM[1] - _TOP[1]) * v
            b = _TOP[2] + (_BOTTOM[2] - _TOP[2]) * v
            blue = _glow(u, v, 0.38, 0.30, 0.62)
            teal = _glow(u, v, 0.68, 0.72, 0.55)
            core = _glow(u, v, 0.52, 0.50, 0.20)
            r += _GLOW_BLUE[0] * blue * 0.55
            g += _GLOW_BLUE[1] * blue * 0.55
            b += _GLOW_BLUE[2] * blue * 0.55
            r += _GLOW_TEAL[0] * teal * 0.50
            g += _GLOW_TEAL[1] * teal * 0.50
            b += _GLOW_TEAL[2] * teal * 0.50
            r += _CORE[0] * core * 0.55
            g += _CORE[1] * core * 0.55
            b += _CORE[2] * core * 0.55
            # Faint diagonal sheen, ±6 levels, fixed phase.
            sheen = 6.0 * math.sin((u + v) * math.pi * 2.0)
            row.append((_clamp(r + sheen), _clamp(g + sheen),
                        _clamp(b + sheen)))
        rows.append(row)
    return rows


def bmp_bytes(width: int, height: int,
              rows: list[list[tuple[int, int, int]]]) -> bytes:
    """Encode top-down (r, g, b) rows as a 24-bit BMP byte string."""
    row_bytes = width * 3
    padding = (4 - (row_bytes % 4)) % 4
    stride = row_bytes + padding
    pixel_data = bytearray()
    for row in reversed(rows):  # BMP stores bottom-up.
        for (r, g, b) in row:
            pixel_data.extend((b, g, r))
        pixel_data.extend(b"\x00" * padding)
    header_size = 54
    file_size = header_size + len(pixel_data)
    header = struct.pack(
        "<2sIHHI", b"BM", file_size, 0, 0, header_size)
    dib = struct.pack(
        "<IiiHHIIiiII", 40, width, height, 1, 24, 0,
        len(pixel_data), 2835, 2835, 0, 0)
    return header + dib + bytes(pixel_data)


def write_bmp(path: str, width: int, height: int) -> str:
    """Render and write one BMP; returns the path written."""
    data = bmp_bytes(width, height, render_aura_pixels(width, height))
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def expected_bmp_size(width: int, height: int) -> int:
    """Exact byte size of the BMP this generator writes."""
    stride = (width * 3 + 3) // 4 * 4
    return 54 + stride * height


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the Inno wizard art BMPs.")
    parser.add_argument(
        "--out-dir", default=None,
        help="directory to write into (default: this script's dir)")
    args = parser.parse_args(argv)
    out_dir = args.out_dir or os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out_dir, exist_ok=True)
    for filename, (width, height) in (
            ("wizard_image.bmp", WIZARD_IMAGE_SIZE),
            ("wizard_small.bmp", WIZARD_SMALL_SIZE)):
        path = os.path.join(out_dir, filename)
        write_bmp(path, width, height)
        print(f"wrote {path} ({width}x{height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
