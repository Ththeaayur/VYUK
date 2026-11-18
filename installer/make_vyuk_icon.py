"""Deterministic generator for the VYUK S1 mark icon — Phase G
(Command Deck), decision D7.

History (D7, complete): the S1 mark replaced the pixels of the
former ``gui/assets/nova_icon.png``, which was renamed to
``gui/assets/vyuk_icon.png`` with all references updated. This
script performs the drawing; the shipped files are
``gui/assets/vyuk_icon.png`` (master) and ``gui/assets/vyuk.ico``.

The S1 mark (guide §6 S1): one master vector — a diamond frame
around a core with a single ring — legible at 16 px. Original
geometry drawn here (no film, trademark, or third-party artwork):
a cyan diamond outline, one thin cyan ring, a dark core disc with
a bright centre glow, on transparency.

Outputs (beside the tree paths below, unless overridden):
* the 512x512 master PNG (S1 mark, RGBA)
* the multi-size Windows .ico (16/20/24/32/40/48/64/128/256),
  generated from the master

Determinism: fixed constants only (no time, randomness, hostname,
or Pillow version-dependent dithering — supersampled drawing then
a single LANCZOS downscale). Re-running over the shipped files
must produce identical bytes for the PNG; the .ico is
byte-stable for a fixed Pillow version and is verified by size
table + 16 px legibility, not by hash.

The generator runs at asset-build time (this lab) with Pillow;
the shipped .ico/.png are checked in, so a bare Windows build box
never needs to regenerate them. Nothing here runs at app runtime.
"""

from __future__ import annotations

import argparse
import math
import os

from PIL import Image, ImageDraw

# S1 geometry, in 512-space (drawn at 2x, then downscaled).
SIZE = 512
SS = 2  # supersample factor
CYAN = (53, 214, 255, 255)       # Command Deck accent_1
CYAN_DIM = (53, 214, 255, 180)   # ring, slightly quieter than frame
CORE_DARK = (4, 20, 32, 255)     # core disc
CORE_MID = (10, 60, 80, 255)     # core inner glow step
CORE_GLOW = (190, 235, 255, 255)  # bright centre
CORE_WHITE = (255, 255, 255, 255)

DIAMOND_HALF = 235   # vertex distance from centre (512-space)
DIAMOND_WIDTH = 15   # frame stroke (512-space)
RING_RADIUS = 150    # single ring radius (512-space)
RING_WIDTH = 5
CORE_RADIUS = 115    # core disc radius (512-space)
GLOW_RADIUS = 46     # bright centre radius (512-space)

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _diamond_points(cx: float, cy: float, half: float) -> list:
    return [(cx, cy - half), (cx + half, cy),
            (cx, cy + half), (cx - half, cy)]


def draw_mark(size: int = SIZE) -> Image.Image:
    """The S1 mark at ``size`` x ``size`` (RGBA, transparent)."""
    w = size * SS
    cx = cy = w / 2.0
    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Core disc (dark) + inner glow steps + bright centre.
    draw.ellipse([cx - CORE_RADIUS * SS, cy - CORE_RADIUS * SS,
                  cx + CORE_RADIUS * SS, cy + CORE_RADIUS * SS],
                 fill=CORE_DARK)
    mid_r = CORE_RADIUS * SS * 0.62
    draw.ellipse([cx - mid_r, cy - mid_r, cx + mid_r, cy + mid_r],
                 fill=CORE_MID)
    glow_r = GLOW_RADIUS * SS
    steps = 12
    for i in range(steps, 0, -1):
        r = glow_r * i / steps
        t = 1.0 - (i / steps)
        col = tuple(
            round(CORE_GLOW[c] + (CORE_WHITE[c] - CORE_GLOW[c]) * t)
            for c in range(3)) + (255,)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    # Single ring around the core.
    rr = RING_RADIUS * SS
    draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                 outline=CYAN_DIM, width=RING_WIDTH * SS)
    # Diamond frame, drawn last so it sits above the ring ends.
    pts = _diamond_points(cx, cy, DIAMOND_HALF * SS)
    draw.line(pts + [pts[0]], fill=CYAN, width=DIAMOND_WIDTH * SS,
              joint="curve")
    if size != w:
        img = img.resize((size, size), Image.LANCZOS)
    return img


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    tree = os.path.dirname(here)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--png", default=os.path.join(
        tree, "gui", "assets", "vyuk_icon.png"),
        help="master PNG path (Step 2 name; the rename is done)")
    ap.add_argument("--ico", default=os.path.join(
        tree, "gui", "assets", "vyuk.ico"),
        help="multi-size .ico path")
    args = ap.parse_args()
    master = draw_mark(SIZE)
    master.save(args.png)
    print(f"wrote {args.png} ({master.size[0]}x{master.size[1]} RGBA)")
    master.save(args.ico, sizes=[(s, s) for s in ICO_SIZES])
    print(f"wrote {args.ico} sizes={list(ICO_SIZES)}")


if __name__ == "__main__":
    main()
